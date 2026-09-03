#!/usr/bin/env python3
"""market_universe.py - the shared Swedish-market universe/liquidity layer.

EXTRACTED from screen_digest.py in v3.0.0. Before this module existed,
screen_digest.py owned this entire pipeline stage (Nasdaq snapshot + ESMA
FIRDS -> combined universe -> LEI grouping -> per-class price history ->
tradeable-primary selection -> SEK-equivalent liquidity floor -> corporate-
action cross-check) and screen_value.py (the on-demand deep-value screen)
reused it by aliasing 18 names out of screen_digest's own module globals -
`fetch_nasdaq_snapshot`, `combine_universe`, `check_corporate_actions`,
`Budget` and so on - with a full second reimplementation of the same logic
sitting in an `else:` branch for whenever screen_digest.py itself was not
importable. That meant the universe/liquidity logic that BOTH scripts'
correctness depends on existed in two places that could silently drift
apart, and the dependency itself was invisible to a plain grep (nothing in
screen_value.py said `screen_digest.combine_universe` - it said
`combine_universe`, a bare name bound once at import time).

This module is that shared layer, pulled out on its own so there is exactly
ONE implementation. screen_digest.py and screen_value.py both import THIS
module now:
  - screen_value.py (the live, user-facing `/screen` market-discovery path)
    aliases the same 18 names from here that it used to alias from
    screen_digest, and no longer carries a full fallback reimplementation of
    its own - see that module's own docstring for what happens on the rare
    path where this module itself fails to import.
  - screen_digest.py (the unattended daily digest, RETIRED as a CLI in
    v3.0.0 - see its own module docstring for what superseded it and why)
    now imports this module too, instead of defining the logic itself. Its
    own remaining code - regulatory-news checking, short-interest signals,
    worst-decile selection, the digest's specific report shape - still
    lives there, since none of that is shared with screen_value.py.

NOT moved here (deliberately, and this is a common source of confusion -
see screen_value.py's own docstring for the full reasoning):
  - fetch_return_for_instrument / fetch_returns_parallel: screen_digest's
    OWN 130-day-lookback history fetch. screen_value.py fetches its own
    multi-year history instead (fetch_history_parallel /
    _fetch_bars_for_instrument, in screen_value.py) and reuses only
    compute_returns() on those bars - not the fetch function itself.
  - fetch_other_venue_turnover: the optional XNGM/NSME turnover integration
    point. screen_value.py's run() never asked for it (it calls
    combine_universe() without an `other_venue_turnover` argument at all),
    so it stays a screen_digest-only concern.
  - check_regulatory_news, short-interest signals (short_se), worst-decile
    selection: all digest-specific report shape, not reused by
    screen_value.py's own value-filter pipeline.

Every threshold, default, and bug-history comment below is carried over
UNCHANGED from screen_digest.py - this is a relocation, not a rewrite. Where
a comment below says "this script" or references screen_digest's own pipeline
stage numbering, that is the ORIGINAL comment, preserved because it documents
real defect history (see the B1/B2/M2/M4/M7 markers, which correspond 1:1 to
screen_digest.py's own docstring and screen_digest's test suite,
tests/test_screen_digest.py, which still exercises this logic - indirectly,
through screen_digest's re-exported names - and must keep passing unchanged).

Python 3 stdlib only. Free, keyless, everywhere.
"""
import datetime
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Sibling scripts are standalone CLI tools imported directly, following the
# convention already established across this toolkit. A parallel agent may
# be mid-edit on a sibling, so every import is defensive: a broken or
# missing sibling degrades the axis it feeds, never crashes this module.
# Calls into any of them must ALSO be guarded with
# `except (Exception, SystemExit)` - these scripts raise SystemExit as their
# error convention, and SystemExit does not inherit from Exception.
try:
    import nordic_shares
except Exception:                                        # pragma: no cover
    nordic_shares = None
try:
    import venues_se
except Exception:                                        # pragma: no cover
    venues_se = None
try:
    import corporate_actions
except Exception:                                        # pragma: no cover
    corporate_actions = None
try:
    import mfn_news
except Exception:                                        # pragma: no cover
    mfn_news = None


DEFAULT_LIQUIDITY_FLOOR_SEK = 2_000_000.0
WINDOW_DAYS = {"1w": 7, "1m": 30, "3m": 90}

NASDAQ_MICS = ("XSTO", "SSME")        # covered by nordic_shares (price/turnover)
OTHER_MICS = ("XSAT", "XNGM", "NSME")  # identity-only, via venues_se/FIRDS
ALL_MICS = NASDAQ_MICS + OTHER_MICS

REGULATED_MICS = {"XSTO", "XNGM"}     # ESEF applies; SSME/XSAT/NSME are MTFs
VENUE_LABEL = {
    "XSTO": "Nasdaq Stockholm (main market)",
    "SSME": "Nasdaq First North Growth Market Sweden",
    "XSAT": "Spotlight Stock Market",
    "XNGM": "NGM Equity",
    "NSME": "Nordic SME",
}


# ---------------------------------------------------------------------------
# time budget
# ---------------------------------------------------------------------------

class Budget(object):
    """A wall-clock ceiling for the whole run.

    Nothing here can forcibly interrupt a blocking network call already in
    flight - each sibling's own per-call socket timeout (45-90s, see their
    own `urlopen(..., timeout=...)`) is what bounds that. What this DOES
    guarantee is that no NEW work is submitted once the budget is spent, and
    - via `remaining()` - that COLLECTING already-submitted work is itself
    bounded, so the run's tail latency is bounded by one round of in-flight
    requests, never open-ended.
    """

    def __init__(self, seconds):
        self.seconds = seconds
        self.start = time.monotonic()
        self.deadline = self.start + seconds if seconds and seconds > 0 else None

    def exceeded(self):
        return self.deadline is not None and time.monotonic() >= self.deadline

    def elapsed(self):
        return time.monotonic() - self.start

    def remaining(self):
        """Seconds left before the deadline, or None if this budget has no
        ceiling at all (matches as_completed's own `timeout=None` = wait
        forever convention)."""
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())


# ---------------------------------------------------------------------------
# a light, shared throttle against api.nasdaq.com
# ---------------------------------------------------------------------------

class RateLimiter(object):
    """A minimum-interval throttle shared across worker threads.

    api.nasdaq.com publishes no documented numeric rate limit, but this
    toolkit's own data-sources.md commits every script to respecting
    published rate limits and never hammering a host - ten concurrent
    workers with zero pacing between requests is the opposite of that. This
    is deliberately not a full token bucket: a job that runs once a day
    against a handful of hundred names needs one lock-guarded "not before"
    timestamp, not a scheduler.
    """

    def __init__(self, min_interval=0.15):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._next_ok = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            delay = self._next_ok - now
            if delay > 0:
                time.sleep(delay)
                now = time.monotonic()
            self._next_ok = now + self.min_interval


NASDAQ_THROTTLE = RateLimiter()


# ---------------------------------------------------------------------------
# number parsing - reuse mfn_news.to_number, never a second parser
# ---------------------------------------------------------------------------

def _num(raw):
    """Parse a raw screener string ("30,054,559", "151,286", "+0.85%") via
    mfn_news.to_number - the toolkit's ONE Swedish/English number parser.
    Only the '+' and '%' decoration specific to this endpoint is stripped
    first; the actual digit-grouping/decimal logic is never reimplemented.
    """
    if raw is None or mfn_news is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.startswith("+"):
        s = s[1:]
    if s.endswith("%"):
        s = s[:-1]
    return mfn_news.to_number(s)


# ---------------------------------------------------------------------------
# stage 1: universe
# ---------------------------------------------------------------------------

def fetch_nasdaq_snapshot(market="STO"):
    """One pass over `/screener/shares` per segment, giving BOTH the
    universe-shaped rows (price, sector, currency, ISIN) AND the
    turnover/volume/percentageChange fields nordic_shares.universe() itself
    discards - fetching those separately used to cost twice the requests
    (4 segments x 2 calls) and join a price snapshot to a turnover snapshot
    taken seconds apart. Returns (rows, liquidity_by_obid, error_or_None).
    """
    if nordic_shares is None:
        return None, None, "nordic_shares.py not importable"
    rows, liq = [], {}
    try:
        for category, segment in nordic_shares.SEGMENTS:
            params = {"category": category, "market": market, "tableonly": "false"}
            if segment:
                params["segment"] = segment
            data = nordic_shares.api("/screener/shares", **params)
            for r in data["instrumentListing"]["rows"]:
                obid = r.get("orderbookId")
                if not obid:
                    continue
                rows.append({"orderbookId": obid, "symbol": r.get("symbol"),
                            "name": r.get("fullName"), "isin": r.get("isin"),
                            "currency": r.get("currency"),
                            "segment": segment or "FIRST_NORTH",
                            "sector": r.get("sector"),
                            "last": _num(r.get("lastSalePrice"))})
                liq[obid] = {"turnover": _num(r.get("turnover")),
                            "volume": _num(r.get("volume")),
                            "percent_change_1d": _num(r.get("percentageChange"))}
    except (Exception, SystemExit) as exc:
        return (rows or None), (liq or None), str(exc)
    return rows, liq, None


def fetch_firds(mics):
    """venues_se.firds_instruments() per MIC - identity for the whole market.
    Returns (results_by_mic, failed_by_mic); a MIC that fails is reported,
    not silently dropped from the universe count."""
    results, failed = {}, {}
    if venues_se is None:
        return results, {m: "venues_se.py not importable" for m in mics}
    for mic in mics:
        try:
            r = venues_se.firds_instruments(mic)
        except (Exception, SystemExit) as exc:
            failed[mic] = str(exc)
            continue
        if r is None:
            failed[mic] = "ESMA FIRDS unreachable"
            continue
        results[mic] = r
    return results, failed


def _mic_for_nasdaq_segment(segment):
    return "SSME" if segment == "FIRST_NORTH" else "XSTO"


def _blank_row(isin, name, mic):
    return {"isin": isin, "lei": None, "name": name, "mic": mic, "currency": None,
            "price": None, "turnover": None, "volume": None, "percent_change_1d": None,
            "sector": None, "orderbookId": None, "has_price_source": False,
            "also_on": []}


def combine_universe(nasdaq_rows, nasdaq_liquidity, firds_by_mic, mics,
                     other_venue_turnover=None):
    """One row per ISIN, identity (LEI/name/mic) from FIRDS where available,
    price/turnover from Nasdaq where available. Never invents either.

    An ISIN dual-listed across two MICs (Paradox Interactive: XSTO and
    SSME) used to be silently overwritten by whichever MIC's FIRDS batch
    happened to be processed last in dict order - which could clobber a
    regulated-market (ESEF-covered) row with an MTF one, understating the
    row's own data confidence. On a collision the REGULATED venue (XSTO,
    XNGM) is always kept as the row of record; the other MIC is recorded in
    `also_on`, never silently dropped.
    """
    combined = {}
    for mic, result in (firds_by_mic or {}).items():
        if mic not in mics:
            continue
        for inst in result.get("instruments", []):
            isin = inst["isin"]
            row = _blank_row(isin, inst["name"], mic)
            row["lei"] = inst.get("lei") or None
            existing = combined.get(isin)
            if existing is None:
                combined[isin] = row
                continue
            regulated_new = mic in REGULATED_MICS
            regulated_old = existing["mic"] in REGULATED_MICS
            if regulated_new and not regulated_old:
                row["also_on"] = existing.get("also_on", []) + [existing["mic"]]
                combined[isin] = row
            else:
                existing.setdefault("also_on", []).append(mic)

    for row in nasdaq_rows or []:
        mic = _mic_for_nasdaq_segment(row.get("segment"))
        if mic not in mics:
            continue
        isin = row.get("isin")
        if not isin:
            continue
        c = combined.get(isin) or _blank_row(isin, row.get("name"), mic)
        c["name"] = c["name"] or row.get("name")
        c["currency"] = row.get("currency") or c["currency"]
        c["price"] = row.get("last")
        c["sector"] = row.get("sector") or c["sector"]
        c["orderbookId"] = row.get("orderbookId")
        liq = (nasdaq_liquidity or {}).get(row.get("orderbookId"))
        if liq:
            c["turnover"] = liq.get("turnover")
            c["volume"] = liq.get("volume")
            c["percent_change_1d"] = liq.get("percent_change_1d")
            c["has_price_source"] = True
        combined[isin] = c

    for isin, info in (other_venue_turnover or {}).items():
        c = combined.get(isin)
        if c is None or c.get("has_price_source"):
            continue
        if info.get("turnover") is not None or info.get("last_price") is not None:
            c["price"] = info.get("last_price") if info.get("last_price") is not None else c["price"]
            c["currency"] = info.get("currency") or c["currency"]
            c["other_venue_turnover"] = info.get("turnover")

    return list(combined.values())


def group_by_issuer(combined_rows):
    """Collapse share classes by LEI (spec: 997 ISIN lines are 925 issuers,
    Investor A/B is one candidate). Falls back to the bare ISIN when FIRDS
    carried no LEI for a row, matching short_se.group_by_company's and
    venues_se.group_by_issuer's own convention.

    `primary` here is only PROVISIONAL - the first instrument with an
    orderbook id, or the first row if none has one. The tradeable line is
    NOT chosen by turnover at this stage: the Nasdaq screener's intraday
    `turnover` field is blank for the entire market before the 09:00 open,
    so picking "the most liquid class" from it here picked the most liquid
    class of a partial morning. select_primary_instrument() re-picks the
    real primary once every class's own price history (and therefore its
    LAST-SESSION turnover) has been fetched - see the pipeline stage order
    in screen_digest.py's own module docstring.
    """
    groups = {}
    for r in combined_rows:
        key = r.get("lei") or ("isin:" + r["isin"])
        groups.setdefault(key, []).append(r)

    issuers = []
    for key, rows in groups.items():
        primary = next((r for r in rows if r.get("orderbookId")), rows[0])
        name = next((r["name"] for r in rows if r.get("name")), None) or primary.get("name")
        issuers.append({"key": key, "lei": primary.get("lei"), "name": name,
                        "isins": [r["isin"] for r in rows], "instruments": rows,
                        "primary": primary})
    return issuers


# ---------------------------------------------------------------------------
# stage 2: returns and turnover
# ---------------------------------------------------------------------------

def percentile_rank(closes, value):
    """Where `value` sits in `closes`, as a 0-100 percentile.

    Strictly LESS THAN, not less-than-or-equal: counting the value against
    itself meant the single lowest close in a monotonically falling series
    always scored 100/N (e.g. 1.1 for a 90-bar window) and could never
    reach 0, even though it IS the minimum of the range.
    """
    if not closes:
        return None
    return 100.0 * sum(1 for c in closes if c < value) / len(closes)


def compute_returns(bars):
    """1w/1m/3m returns plus the percentile of the last close within the
    bars actually fetched (NOT a multi-year range - the caller decides how
    much history to fetch; this function only ever summarises whatever bars
    it is handed). Also carries the LAST COMPLETED SESSION's own close and
    volume through (`last_volume`) - this is what the liquidity floor is
    computed from (see B1 in screen_digest.py's own module docstring): the
    Nasdaq screener's intraday `turnover` field is blank before the market
    opens, but a daily bar's own volume is a fact about a session that has
    already closed. Returns None if there is nothing usable."""
    usable = [b for b in (bars or []) if b.get("close") is not None]
    if not usable:
        return None
    usable.sort(key=lambda b: b["date"])
    closes = [b["close"] for b in usable]
    last_bar = usable[-1]
    last_close, last_date = last_bar["close"], last_bar["date"]

    out = {"as_of": last_date, "last_close": last_close,
          "last_volume": last_bar.get("volume"), "windows": {},
          "percentile_in_fetched_range": percentile_rank(closes, last_close),
          "bars_fetched": len(usable)}
    last_d = datetime.date.fromisoformat(last_date)
    for label, days in WINDOW_DAYS.items():
        target = (last_d - datetime.timedelta(days=days)).isoformat()
        ref = None
        for b in usable:
            if b["date"] <= target:
                ref = b
        if ref is None:
            out["windows"][label] = {"pct": None, "from_date": None,
                                     "note": "insufficient history for this window"}
            continue
        pct = (last_close / ref["close"] - 1.0) * 100.0 if ref["close"] else None
        out["windows"][label] = {"pct": pct, "from_date": ref["date"],
                                 "from_close": ref["close"]}
    return out


def _instrument_turnover_sek(row, ret):
    """LAST-COMPLETED-SESSION turnover (close x volume from the daily bar
    `ret` carries), FX-converted to SEK via nordic_shares._fx_convert_to_sek
    (dated Riksbank/ECB rates - never a guessed one). Returns
    (turnover_sek_or_None, error_or_None). NOT the Nasdaq screener's
    intraday `turnover` field - see B1 in screen_digest.py's own module
    docstring."""
    if not ret or ret.get("status") != "checked":
        return None, None
    close, volume = ret.get("last_close"), ret.get("last_volume")
    if close is None or volume is None:
        return None, "last bar missing close or volume"
    raw = close * volume
    ccy = (row.get("currency") or "SEK").upper()
    if ccy == "SEK":
        return raw, None
    if nordic_shares is None:
        return None, "nordic_shares.py not importable - cannot FX-convert %s" % ccy
    try:
        converted = nordic_shares._fx_convert_to_sek({ccy: raw})
    except (Exception, SystemExit) as exc:
        return None, str(exc)
    if not converted:
        return None, "no dated FX rate for %s" % ccy
    return converted["total_sek"], None


def select_primary_instrument(issuer, returns_by_obid):
    """The most-liquid class becomes the tradeable 'primary' line, using
    each class's own LAST-SESSION turnover (see _instrument_turnover_sek) -
    never the intraday screener snapshot, which is blank for the whole
    market before the open and used to hand this decision to whichever
    class happened to have a nonzero morning print. Attaches
    `primary_returns` (that class's own compute_returns() result) to the
    issuer alongside the (possibly re-picked) `primary` row.
    """
    best_row, best_ret, best_sek = None, None, None
    for row in issuer["instruments"]:
        obid = row.get("orderbookId")
        ret = returns_by_obid.get(obid) if obid else None
        sek, _err = _instrument_turnover_sek(row, ret) if ret else (None, None)
        if sek is not None and (best_sek is None or sek > best_sek):
            best_row, best_ret, best_sek = row, ret, sek
    if best_row is None:
        best_row = issuer["primary"]
        obid = best_row.get("orderbookId")
        best_ret = (returns_by_obid.get(obid) if obid else None) or {
            "status": "not checked",
            "reason": "no orderbook id - no free price-history source exists "
                      "in this toolkit for this venue"}
    issuer["primary"] = best_row
    issuer["primary_returns"] = best_ret
    issuer["primary_turnover_sek"] = best_sek
    return best_row, best_ret, best_sek


def compute_issuer_turnover(issuer):
    """Attach turnover_status ("ok" | "unresolved" | "no_source"),
    turnover_sek and turnover_error to an issuer, from its primary
    instrument's last completed session - called once per issuer, after
    select_primary_instrument. apply_liquidity_floor is a pure decision
    function over exactly these three fields, kept separate so it stays
    trivially unit-testable.

    An issuer with no Nasdaq orderbook id at all (XSAT always; XNGM/NSME
    unless the OPTIONAL INTEGRATION POINT is live - see screen_digest.py's
    own module docstring) falls back to whatever combine_universe attached
    as `other_venue_turnover` - venues_se.ngm_turnover()'s own figure for
    XNGM/NSME - before being written off as source-less entirely.
    """
    has_orderbook = any(r.get("orderbookId") for r in issuer["instruments"])
    if not has_orderbook:
        other = issuer["primary"].get("other_venue_turnover")
        if other is None:
            issuer["turnover_status"] = "no_source"
            issuer["turnover_sek"] = None
            issuer["turnover_error"] = None
            return
        ccy = (issuer["primary"].get("currency") or "SEK").upper()
        if ccy == "SEK":
            issuer["turnover_status"] = "ok"
            issuer["turnover_sek"] = other
            issuer["turnover_error"] = None
            return
        if nordic_shares is None:
            issuer["turnover_status"] = "unresolved"
            issuer["turnover_sek"] = None
            issuer["turnover_error"] = "nordic_shares.py not importable - cannot FX-convert %s" % ccy
            return
        try:
            converted = nordic_shares._fx_convert_to_sek({ccy: other})
        except (Exception, SystemExit) as exc:
            issuer["turnover_status"] = "unresolved"
            issuer["turnover_sek"] = None
            issuer["turnover_error"] = str(exc)
            return
        if not converted:
            issuer["turnover_status"] = "unresolved"
            issuer["turnover_sek"] = None
            issuer["turnover_error"] = "no dated FX rate for %s" % ccy
            return
        issuer["turnover_status"] = "ok"
        issuer["turnover_sek"] = converted["total_sek"]
        issuer["turnover_error"] = None
        return

    ret = issuer.get("primary_returns") or {}
    sek, err = _instrument_turnover_sek(issuer["primary"], ret)
    if sek is None:
        issuer["turnover_status"] = "unresolved"
        issuer["turnover_sek"] = None
        issuer["turnover_error"] = err or ret.get("reason") or "price history unavailable"
    else:
        issuer["turnover_status"] = "ok"
        issuer["turnover_sek"] = sek
        issuer["turnover_error"] = None


# ---------------------------------------------------------------------------
# stage 3: liquidity floor
# ---------------------------------------------------------------------------

def apply_liquidity_floor(issuers, floor, include_illiquid):
    """Cuts on the LAST-SESSION, SEK-equivalent turnover computed by
    compute_issuer_turnover - never the intraday screener snapshot (see B1:
    that field is blank for the entire market before the open and used to
    cut the whole universe as illiquid on a pre-market run).

    Expects each issuer to already carry turnover_status/turnover_sek/
    turnover_error (compute_issuer_turnover). Three cut reasons, each
    counted UNCONDITIONALLY (once per issuer that meets it, whether or not
    --include-illiquid then keeps it in `survivors` too - a cut issuer kept
    by that flag must still count once against the reason it was cut for,
    not zero times and not twice):
      no_price_source  - no venue in this toolkit has a free feed at all.
      did_not_trade    - a feed exists, the last session is known, and its
                         volume was zero. Distinct from no_price_source:
                         Nokia/Modelon/Qlucore-style names that simply had
                         a quiet day used to be told this toolkit has no
                         feed for their venue at all, which is false.
      below_floor      - turnover is known and positive, but under the floor.
    An issuer whose turnover could not be determined at all (price history
    not yet checked, or no dated FX rate for its currency) is NEITHER cut
    NOR confirmed liquid - there is no evidence either way, so it passes
    through uncut and is reported separately by the caller, never silently
    treated as having cleared the floor.
    """
    survivors = []
    cuts = {"no_price_source": 0, "below_floor": 0, "did_not_trade": 0}
    for iss in issuers:
        status = iss.get("turnover_status")
        turnover = iss.get("turnover_sek")

        if status == "no_source":
            iss["liquidity_status"] = "not checked - no free turnover source for this venue"
            cuts["no_price_source"] += 1
            if include_illiquid:
                survivors.append(iss)
            continue

        if status != "ok":
            iss["liquidity_status"] = "not checked - %s" % (
                iss.get("turnover_error") or "price history unavailable")
            survivors.append(iss)
            continue

        if turnover is None or turnover <= 0:
            iss["liquidity_status"] = "did not trade in the last completed session"
            cuts["did_not_trade"] += 1
            if include_illiquid:
                survivors.append(iss)
            continue

        if turnover < floor:
            iss["liquidity_status"] = ("below floor (%s < %s SEK-equiv)"
                                       % ("{:,.0f}".format(turnover), "{:,.0f}".format(floor)))
            cuts["below_floor"] += 1
            if include_illiquid:
                survivors.append(iss)
            continue

        iss["liquidity_status"] = "checked"
        survivors.append(iss)
    return survivors, cuts


# ---------------------------------------------------------------------------
# name cleanup shared by both deep-check sources
# ---------------------------------------------------------------------------

_CLASS_SUFFIX_RE = re.compile(r"[,]?\s*(ser\.?|serie|class)\s*[A-Z]\d?\s*$", re.I)


def _strip_class_suffix(name):
    """Strip an exchange's class-suffixed form ("Atlas Copco AB ser. A",
    "Volvo, AB ser. B") down to the bare company name before resolving it
    against Nasdaq CNS or MFN. 236 of 754 XSTO+SSME lines carry a '.' in
    this form and MFN returns HTTP 500 for them outright - and even where a
    source degrades gracefully instead of erroring, a class-suffixed name is
    simply less likely to resolve to the right (or any) company."""
    return _CLASS_SUFFIX_RE.sub("", name or "").strip()


# ---------------------------------------------------------------------------
# corporate actions - the cross-check hook shared by both screens
# ---------------------------------------------------------------------------

_DIV_AMOUNT_RE = re.compile(
    r"(?:SEK|kr|kronor)\s*([\d]+[.,]\d+|\d+)(?:\s*(?:per\s+share|per\s+aktie))?|"
    r"([\d]+[.,]\d+|\d+)\s*(?:SEK|kr|kronor)\s*per\s+(?:share|aktie)", re.I)


def _extract_dividend_per_share(title):
    """Best-effort per-share dividend amount out of a headline, e.g.
    "SEK 5.20 per share" or "utdelning om 2,50 kr per aktie". None if no
    such figure is present - this is a headline scrape, not a parsed
    disclosure, and is only ever used to STATE a yield alongside a
    dividend-routed technical move, never to compute anything load-bearing.
    """
    if not title or mfn_news is None:
        return None
    m = _DIV_AMOUNT_RE.search(title)
    if not m:
        return None
    raw = m.group(1) or m.group(2)
    return mfn_news.to_number(raw.replace(",", ".")) if raw else None


def check_corporate_actions(name, date_from, last_close_date, date_to, price=None):
    """Was there a split/rights issue/spin-off/dividend/other per-share-
    affecting action inside [date_from, date_to]?

    CORRECTED IN v3.0.0 (see corporate_actions.py's own "THE ANSWER" section
    and its price_check() diagnostic for the measured evidence): nordic_
    shares' daily price series IS back-adjusted for splits - four dated,
    confirmed splits, in both directions, showed zero price discontinuity
    at the effective date. A confirmed SPLIT/REVERSE_SPLIT is therefore no
    longer, by itself, a reason the observed return is a technical
    artefact - the series already reflects it cleanly. It stays inside
    this check's per-share-affecting set anyway, alongside RIGHTS_ISSUE/
    DIRECTED_ISSUE/SPIN_OFF/DIVIDEND, none of which are confirmed adjusted
    (dividends explicitly: "not verified either way, and treated as
    unadjusted" - see nordic_shares.py) - a hit on any of THOSE still means
    the return may be a technical artefact rather than a real fall. This
    function does not itself tell a confirmed-harmless split apart from a
    genuinely distorting action of a different kind within one merged
    `has_breaking_action` flag; a caller that needs that distinction reads
    each event's own `type` in `events`. NEVER multiply a price or a price
    ratio by split_adjustment_factor()'s `factor` here or downstream of
    this check - that field is scoped to per-share FUNDAMENTALS (EPS,
    dividend, book value) only; only its `confirmed_splits` dates are used
    below, never its numeric factor.

    The window is split in two against `last_close_date` (the same date the
    candidate's return was measured to - it may fall on the same day as
    `date_to`, or earlier), for the same reason a regulatory-news check
    splits its own window: an ex-dividend date or a split EFFECTIVE this
    morning, before the open, is not an explanation of a fall measured to
    yesterday's close - the fall predates the action. Only actions on or
    before `last_close_date` can explain the fall (`has_breaking_action`,
    `events`); anything strictly after it is new and not yet priced in, and
    is reported separately under `since_last_close` so it is never silently
    read as having caused a fall it postdates. One fetch across the WHOLE
    [date_from, date_to] range is partitioned locally into the two windows
    rather than fetching twice.

    Refuses (returns `not checked`, naming the candidates seen) rather than
    silently take the top-ranked Nasdaq CNS company when MORE THAN ONE
    distinct company matches `name` and the top match is not an EXACT one -
    corporate_actions.resolve_company's own free-text ranking has, on live
    data, put an unrelated company ahead of the one actually being asked
    about; taking hits[0] unconditionally attached that company's actions
    (or lack of them) to the wrong candidate.

    Dividends are included on purpose (BREAKS_PER_SHARE itself does not
    carry DIVIDEND - nordic_shares' price series is not confirmed adjusted
    for them and is treated as unadjusted, and Swedish AGM season clusters
    ex-dates tightly enough that an April 1m decile is otherwise dominated
    by ordinary ex-dividend drops wearing a crash costume); where a
    per-share amount can be scraped from the headline and `price` is
    known, the implied yield is stated alongside it.

    Splits are checked TWICE on purpose: once via corporate_actions_between
    (announcement date, catches everything else BREAKS_PER_SHARE covers),
    and once via corporate_actions.split_adjustment_factor, which parses the
    exchange notice's own EFFECTIVE date - a split announced before the
    window but effective inside it is invisible to an announcement-date-only
    check and split_adjustment_factor is the tool built to answer that. Only
    the DATES in its `confirmed_splits` are used (to add an event this
    function would otherwise miss); its numeric `factor` is never read here
    - see the note above on why that field must never touch a price.
    """
    if corporate_actions is None:
        return {"status": "not checked", "reason": "corporate_actions.py not importable",
                "since_last_close": {"status": "not checked",
                                     "reason": "corporate_actions.py not importable"}}
    try:
        hits = corporate_actions.resolve_company(name)
    except (Exception, SystemExit) as exc:
        reason = "CNS name resolution failed: %s" % exc
        return {"status": "not checked", "reason": reason,
                "since_last_close": {"status": "not checked", "reason": reason}}
    if not hits:
        return {"status": "checked", "has_breaking_action": False, "events": [],
                "note": "no Nasdaq CNS company matched %r; treated as no action "
                        "found, which is not proof there was none" % name,
                "since_last_close": {"status": "checked", "count": 0, "events": [],
                                     "window": [last_close_date, date_to]}}

    distinct = sorted(set(h["company"] for h in hits))
    try:
        top_is_exact = (corporate_actions._norm(hits[0]["company"])
                        == corporate_actions._norm(name))
    except (Exception, SystemExit):                 # pragma: no cover - defensive
        top_is_exact = True
    if len(distinct) > 1 and not top_is_exact:
        reason = ("ambiguous Nasdaq CNS match for %r - candidates seen: %s"
                  % (name, "; ".join(distinct[:6])))
        return {"status": "not checked", "reason": reason,
                "since_last_close": {"status": "not checked", "reason": reason}}
    company = hits[0]["company"]

    breaks_price = corporate_actions.BREAKS_PER_SHARE | {"DIVIDEND"}
    try:
        rows = corporate_actions.corporate_actions_between(company, date_from, date_to, pages=2)
    except (Exception, SystemExit) as exc:
        reason = str(exc)
        return {"status": "not checked", "reason": reason,
                "since_last_close": {"status": "not checked", "reason": reason}}
    breaking = [r for r in rows if r.get("type") in breaks_price]

    try:
        factor_info = corporate_actions.split_adjustment_factor(company, date_from, date_to)
    except (Exception, SystemExit) as exc:
        factor_info = {"confirmed_splits": [], "warnings": [str(exc)]}
    already = {(r.get("date") or "")[:10] for r in breaking
              if r.get("type") in ("SPLIT", "REVERSE_SPLIT")}
    for c in factor_info.get("confirmed_splits") or []:
        if c.get("date") in already:
            continue
        breaking.append({"date": c.get("date"), "type": c.get("kind"),
                         "title": "%s %s (effective date)" % (c.get("kind"), c.get("terms"))})

    events = []
    for r in breaking:
        ev = {"date": r.get("date"), "type": r.get("type"), "title": r.get("title")}
        if r.get("type") == "DIVIDEND":
            amt = _extract_dividend_per_share(r.get("title"))
            if amt is not None:
                ev["dividend_per_share"] = amt
                if price:
                    ev["dividend_yield_pct"] = 100.0 * amt / price
        events.append(ev)

    explains = [ev for ev in events if (ev.get("date") or "") <= last_close_date]
    since_close = [ev for ev in events if (ev.get("date") or "") > last_close_date]

    return {"status": "checked", "has_breaking_action": bool(explains),
            "cns_company": company, "events": explains,
            "since_last_close": {"status": "checked", "count": len(since_close),
                                 "events": since_close, "window": [last_close_date, date_to]}}


# ---------------------------------------------------------------------------
# data confidence
# ---------------------------------------------------------------------------

def data_confidence(mic):
    regulated = mic in REGULATED_MICS
    return {"mic": mic, "regulated_market": regulated, "esef_applies": regulated,
            "label": ("ESEF-covered regulated market" if regulated else
                      "MTF - no ESEF; any fundamental context here is parsed "
                      "prose, not machine-verified XBRL")}


# ---------------------------------------------------------------------------
# market cap - summed across share classes, never blended price × total count
# ---------------------------------------------------------------------------

def attach_market_cap(issuers, budget=None):
    """Compute SEK market cap summed ACROSS listed share classes.

    For each issuer dict, calculates market cap as the SUM over share classes
    of (that class's share count × THAT CLASS'S OWN LAST PRICE). Never
    multiplies one blended price by a total share count — Swedish A and B
    shares trade at different prices, and doing this wrong previously
    understated Volvo by SEK 154bn.

    Gets per-class share count from nordic_shares.summary(orderbook_id)["shares"]
    with one call per listed class. Uses the class's own last price already
    carried on the issuer's instrument rows. FX-converts to SEK with the same
    nordic_shares._fx_convert_to_sek idiom used elsewhere in this file.

    A class with NO share count is tracked separately - not treated as zero.
    If some classes contributed a count and others did not, the result is marked
    PARTIAL and the basis names which classes are missing.

    Sets on each issuer:
    - market_cap_sek (float or None)
    - market_cap_status ("checked" | "partial" | "not_checked")
    - market_cap_basis (human-readable string naming derivation and missing classes)
    - market_cap_error (reason string when computation failed, or None)

    Never raises on a single instrument's failure — records the error on that
    issuer and continues. Respects budget if given; stops submitting new work
    once deadline is exceeded.
    """
    if nordic_shares is None:
        for iss in issuers:
            iss["market_cap_sek"] = None
            iss["market_cap_status"] = "not_checked"
            iss["market_cap_basis"] = "nordic_shares.py not importable"
            iss["market_cap_error"] = "nordic_shares.py not importable"
        return

    for iss in issuers:
        if budget and budget.exceeded():
            iss["market_cap_sek"] = None
            iss["market_cap_status"] = "not_checked"
            iss["market_cap_basis"] = "time budget exceeded"
            iss["market_cap_error"] = "time budget exceeded"
            continue

        total_sek = 0.0
        have_shares = []
        missing_symbols = []
        errors = []

        for row in iss.get("instruments", []):
            obid = row.get("orderbookId")
            if not obid:
                missing_symbols.append(row.get("symbol") or "?")
                continue

            # Fetch per-class share count
            try:
                summary = nordic_shares.summary(obid)
            except (Exception, SystemExit) as exc:
                errors.append((row.get("symbol") or "?", str(exc)))
                continue

            shares = summary.get("shares")
            symbol = row.get("symbol") or "?"

            if shares is None:
                missing_symbols.append(symbol)
                continue

            price = row.get("price")
            if price is None:
                errors.append((symbol, "no price on this instrument"))
                continue

            # Compute raw market cap for this class: shares × price
            raw = shares * price

            # FX-convert to SEK
            ccy = (row.get("currency") or "SEK").upper()
            if ccy == "SEK":
                converted = raw
            else:
                try:
                    fx_result = nordic_shares._fx_convert_to_sek({ccy: raw})
                except (Exception, SystemExit) as exc:
                    errors.append((symbol, "FX conversion failed: %s" % exc))
                    continue
                if not fx_result:
                    errors.append((symbol, "no dated FX rate for %s" % ccy))
                    continue
                converted = fx_result.get("total_sek")
                if converted is None:
                    errors.append((symbol, "FX conversion returned no total_sek"))
                    continue

            total_sek += converted
            have_shares.append(symbol)

        # Determine status and basis
        n_classes = len(iss.get("instruments", []))

        if not have_shares and not missing_symbols:
            # No instruments at all, or all failed
            iss["market_cap_sek"] = None
            iss["market_cap_status"] = "not_checked"
            basis = "no instruments with orderbook ids"
            if errors:
                basis += "; errors: " + "; ".join("%s (%s)" % e for e in errors)
            iss["market_cap_basis"] = basis
            iss["market_cap_error"] = basis if errors else None
        elif have_shares and not missing_symbols and not errors:
            # All classes contributed
            iss["market_cap_sek"] = total_sek
            iss["market_cap_status"] = "checked"
            iss["market_cap_basis"] = (
                "sum across %d LISTED class(es): %s. Listed classes only - an "
                "unlisted class would not appear here, so this is a floor "
                "rather than a confirmed total market cap."
                % (len(have_shares), ", ".join(have_shares)))
            iss["market_cap_error"] = None
        else:
            # Some contributed, some didn't — mark as PARTIAL
            iss["market_cap_sek"] = total_sek if have_shares else None
            iss["market_cap_status"] = "partial"
            basis_parts = []
            if have_shares:
                basis_parts.append("sum across %d of %d listed class(es): %s"
                                   % (len(have_shares), n_classes,
                                      ", ".join(have_shares)))
            if missing_symbols:
                basis_parts.append("missing share count: %s" % ", ".join(missing_symbols))
            if errors:
                basis_parts.append("errors: " + "; ".join("%s (%s)" % e for e in errors))
            iss["market_cap_basis"] = ("FLOOR (incomplete data); "
                                       + "; ".join(basis_parts))
            iss["market_cap_error"] = None if have_shares else "no shares contributed"


def apply_size_band(issuers, cap_floor=None, cap_ceiling=None):
    """Apply minimum and maximum market-cap thresholds.

    Cuts on market_cap_sek (computed by attach_market_cap and carried on each
    issuer). Mirror apply_liquidity_floor's signature and return shape exactly.

    Returns (survivors, cuts) where survivors is the filtered list and cuts
    is a dict of integer counts:
    - above_ceiling: market_cap_sek > cap_ceiling
    - below_floor: market_cap_sek < cap_floor
    - no_market_cap: status is not "checked" (per CLAUDE.md: "A check that
      could not run is `not checked`, counted separately, never folded into
      a clean result")

    No-market-cap issuers pass through to the caller marked with size_status,
    so the caller can report them as unscreened rather than cut.

    cap_floor=None means no floor; cap_ceiling=None means no ceiling; both
    None means the whole function is a no-op returning every issuer with empty
    cuts. Existing callers must be unaffected.
    """
    if cap_floor is None and cap_ceiling is None:
        # No-op: return everything with empty cuts
        for iss in issuers:
            iss["size_status"] = "not checked"
        return issuers, {"above_ceiling": 0, "below_floor": 0, "no_market_cap": 0}

    survivors = []
    cuts = {"above_ceiling": 0, "below_floor": 0, "no_market_cap": 0}

    for iss in issuers:
        cap = iss.get("market_cap_sek")
        status = iss.get("market_cap_status")

        # A PARTIAL cap is a FLOOR: some classes were counted, others could
        # not be, so the true cap can only be HIGHER than what we hold. That
        # makes the two tests asymmetric, and the asymmetry is load-bearing:
        #   - above_ceiling stays SOUND on a floor. If the partial sum already
        #     exceeds the ceiling, the true cap does too, so the cut is safe
        #     and refusing to make it would pass a company we KNOW is too big
        #     through as "unscreened".
        #   - below_floor is NOT sound on a floor. A partial sum under the
        #     floor says nothing about the true cap, so it is never cut there.
        if status == "partial" and cap is not None:
            if cap_ceiling is not None and cap > cap_ceiling:
                iss["size_status"] = (
                    "above ceiling on a PARTIAL cap, which is a floor "
                    "(%s > %s SEK; true cap can only be higher)"
                    % ("{:,.0f}".format(cap), "{:,.0f}".format(cap_ceiling)))
                cuts["above_ceiling"] += 1
                continue
            iss["size_status"] = "not checked - partial cap is a floor, %s" % (
                iss.get("market_cap_basis") or "market cap unavailable")
            cuts["no_market_cap"] += 1
            survivors.append(iss)
            continue

        # No market cap computed at all
        if status != "checked":
            iss["size_status"] = "not checked - %s" % (
                iss.get("market_cap_basis") or "market cap unavailable")
            cuts["no_market_cap"] += 1
            survivors.append(iss)
            continue

        # Check ceiling
        if cap_ceiling is not None and cap is not None and cap > cap_ceiling:
            iss["size_status"] = ("above ceiling (%s > %s SEK)"
                                  % ("{:,.0f}".format(cap),
                                     "{:,.0f}".format(cap_ceiling)))
            cuts["above_ceiling"] += 1
            continue

        # Check floor
        if cap_floor is not None and cap is not None and cap < cap_floor:
            iss["size_status"] = ("below floor (%s < %s SEK)"
                                  % ("{:,.0f}".format(cap),
                                     "{:,.0f}".format(cap_floor)))
            cuts["below_floor"] += 1
            continue

        # Passed all checks
        iss["size_status"] = "checked"
        survivors.append(iss)

    return survivors, cuts


# ---------------------------------------------------------------------------
# selftest: attach_market_cap and apply_size_band, pure in-memory fixtures
# ---------------------------------------------------------------------------

def _selftest():
    """Pure in-memory tests for attach_market_cap and apply_size_band.

    No network endpoints touched; all data is synthetic. Run with:
        python3 market_universe.py --selftest
    """
    failures = []

    # Test attach_market_cap: two classes, both with shares and prices
    iss1 = {
        "name": "Test AB",
        "instruments": [
            {"symbol": "TEST_A", "orderbookId": "OB-A", "currency": "SEK",
             "price": 100.0},
            {"symbol": "TEST_B", "orderbookId": "OB-B", "currency": "SEK",
             "price": 50.0},
        ]
    }

    # Mock nordic_shares.summary to return fixed share counts
    real_summary = nordic_shares.summary if nordic_shares else None
    if nordic_shares:
        def mock_summary(obid):
            if obid == "OB-A":
                return {"orderbookId": obid, "isin": "SE-A", "shares": 1_000_000.0,
                        "market_cap": None, "segment": "XSTO", "icb": None, "note": ""}
            elif obid == "OB-B":
                return {"orderbookId": obid, "isin": "SE-B", "shares": 2_000_000.0,
                        "market_cap": None, "segment": "XSTO", "icb": None, "note": ""}
            return {"orderbookId": obid, "shares": None}

        nordic_shares.summary = mock_summary

        try:
            attach_market_cap([iss1])

            # Expected: (1_000_000 * 100) + (2_000_000 * 50) = 100M + 100M = 200M SEK
            if iss1.get("market_cap_sek") != 200_000_000.0:
                failures.append("attach_market_cap sum: expected 200M, got %s"
                               % iss1.get("market_cap_sek"))
            if iss1.get("market_cap_status") != "checked":
                failures.append("attach_market_cap status: expected 'checked', got %r"
                               % iss1.get("market_cap_status"))
            if "TEST_A" not in iss1.get("market_cap_basis", "") or \
               "TEST_B" not in iss1.get("market_cap_basis", ""):
                failures.append("attach_market_cap basis missing class symbols: %r"
                               % iss1.get("market_cap_basis"))
        finally:
            if real_summary:
                nordic_shares.summary = real_summary

    # Test apply_size_band: two issuers, one above floor, one below
    iss_above = {"name": "Above AB", "market_cap_sek": 50_000_000.0,
                 "market_cap_status": "checked"}
    iss_below = {"name": "Below AB", "market_cap_sek": 1_000.0,
                 "market_cap_status": "checked"}
    iss_unresolved = {"name": "Unresolved AB", "market_cap_sek": None,
                      "market_cap_status": "partial",
                      "market_cap_basis": "missing data"}

    survivors, cuts = apply_size_band([iss_above, iss_below, iss_unresolved],
                                      cap_floor=5_000_000.0, cap_ceiling=100_000_000.0)
    if len(survivors) != 2:
        failures.append("apply_size_band survivors: expected 2, got %d" % len(survivors))
    if cuts.get("below_floor") != 1:
        failures.append("apply_size_band below_floor: expected 1, got %d"
                       % cuts.get("below_floor"))
    if cuts.get("no_market_cap") != 1:
        failures.append("apply_size_band no_market_cap: expected 1, got %d"
                       % cuts.get("no_market_cap"))
    if cuts.get("above_ceiling") != 0:
        failures.append("apply_size_band above_ceiling: expected 0, got %d"
                       % cuts.get("above_ceiling"))
    if iss_unresolved.get("size_status") != "not checked - missing data":
        failures.append("apply_size_band unresolved size_status: expected "
                       "'not checked - missing data', got %r"
                       % iss_unresolved.get("size_status"))

    # Test apply_size_band: no-op when both cap_floor and cap_ceiling are None
    iss_any = {"name": "Any AB", "market_cap_sek": 1.0,
               "market_cap_status": "checked"}
    survivors, cuts = apply_size_band([iss_any])
    if len(survivors) != 1:
        failures.append("apply_size_band no-op: expected 1 survivor, got %d"
                       % len(survivors))
    if cuts != {"above_ceiling": 0, "below_floor": 0, "no_market_cap": 0}:
        failures.append("apply_size_band no-op cuts: expected all zeros, got %s" % cuts)

    if failures:
        print("SELFTEST FAILURES:")
        for f in failures:
            print("  " + f)
        return False
    print("attach_market_cap and apply_size_band: OK")
    return True


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        sys.exit(0 if _selftest() else 1)
    print("Use --selftest to run the pure in-memory selftest")
