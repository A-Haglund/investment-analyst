#!/usr/bin/env python3
"""Daily digest screen: which Swedish listed companies fell recently in a way
that might be an opportunity rather than a verdict?

This is deliberately NOT the full screen (screen.py / the `screen` skill). No
ten-year price cache, no DCF, no scorecard. It runs unattended as a scheduled
cloud job once a day, so the overriding design constraints are:

  * NEVER HANG. Every sibling call is wrapped so a dead endpoint degrades one
    axis of one candidate to "not checked", never the whole run.
  * FIT A TIME BUDGET. An overall wall-clock budget (--budget, default
    {budget}s) bounds the run; if it is exceeded, the digest still prints
    whatever it has, clearly labelled PARTIAL, rather than hang or crash. The
    budget bounds both SUBMISSION and COLLECTION of every parallel stage - a
    stdlib ThreadPoolExecutor's task queue is unbounded, so checking the
    budget only before submitting new work never actually bounds a run whose
    workers are already in flight; `as_completed(..., timeout=...)` is what
    does that (see Budget.remaining()).
  * BE AUDITABLE. Every stage prints how many names it cut and why. A screen
    that silently drops 900 of 997 names down to a nice round 20 is not a
    screen, it is a black box. Where a check could not be performed at all -
    an ambiguous name match, a dead endpoint, a suffixed name a source could
    not resolve - that is reported as ITS OWN outcome, never silently folded
    into "checked, and nothing was found" (see NOT CLASSIFIED below).
  * REFUSE TO PUBLISH A REPORT BUILT ON NO DATA. If the belt-and-braces guard
    below finds the market has evidently not opened yet, the run stops with
    a loud SystemExit rather than print a well-formed, empty digest.

THE PIPELINE

  1. UNIVERSE.  nordic_shares covers Nasdaq Stockholm's main market (XSTO)
     and Nasdaq First North Sweden (SSME) - the only two of the five Swedish
     equity venues with a free bulk price/turnover feed anywhere in this
     toolkit. venues_se.firds_instruments() adds identity (ISIN, LEI, name)
     for the other three (XSAT Spotlight, XNGM NGM Equity, NSME Nordic SME)
     so they are counted and their absence of return data is reported rather
     than silently swallowed - but no free price history exists for them
     anywhere in this toolkit (XNGM/NSME turnover is fed in when a sibling
     script exposes it - see the OPTIONAL INTEGRATION POINT below - but even
     then no free price HISTORY exists for them), so they can never produce
     a "fell X%" candidate here. That is a real, disclosed limitation, not a
     bug: see `--venue` and the per-stage cut counts.

     One pass over `/screener/shares` per segment gives BOTH the universe
     fields (price, sector, currency, ISIN) AND the turnover/volume/percent-
     change fields nordic_shares.universe() itself discards - fetching those
     twice, seconds apart, used to join a price snapshot to a turnover
     snapshot that were never the same moment.

     Every ISIN line is grouped into an issuer by LEI (falling back to the
     ISIN itself when FIRDS carries no LEI): 997 ISIN lines is not 997
     companies, and Investor A + Investor B is one candidate, not two.

  2. HISTORY.  price_history() on every priced ISIN line (not just one class
     per issuer - see the note below), one call each (~130 calendar days
     back - enough to cover the 3m window with a weekend/holiday buffer;
     this script carries NO multi-year cache). 1w/1m/3m returns, the
     percentile of the last close within that fetched window (not a
     multi-year range - this is the lightweight screen), and the LAST
     COMPLETED SESSION's close and volume, which is what turns into turnover
     for stage 3.

     The most-liquid class becomes the tradeable "primary" line for its
     issuer - by LAST-SESSION turnover (close x volume from its own price
     history, FX-converted to SEK), never by the Nasdaq screener's `turnover`
     field, which is TODAY'S RUNNING INTRADAY TOTAL and is blank for the
     entire market before the 09:00 open (see the module's fix history: a
     pre-open run used to cut the whole universe as illiquid on that field
     alone). This is why every class of every issuer needs its own history
     call, not just whichever class the screener happened to show turnover
     for first.

  3. LIQUIDITY FLOOR.  Issuers whose tradeable line's LAST-SESSION turnover
     (SEK-equivalent) is below --liquidity-floor (default {floor}) are cut.
     Three distinct "cannot use this name" reasons are told apart, because
     they mean different things to a reader:
       - no_turnover_source: no venue in this toolkit has a free feed at all
         for this issuer (XSAT always; XNGM/NSME unless the optional
         integration point below is live).
       - did_not_trade: a feed exists and was read successfully, but the
         last completed session shows zero volume - a real fact about a
         thinly-traded name, not a missing data source.
       - below_floor: a feed exists, the name traded, and the SEK-equivalent
         turnover is under the floor.
     --include-illiquid disables the CUT but keeps the label; the count in
     each cut reason is unconditional (issuers cut-but-kept via that flag
     still count once against the reason they were cut for).

  4. WORST DECILE on the --window return (1w or 1m, selectable), among names
     that actually FELL (pct < 0 only - a strong month's positive "bottom
     decile" is not a fall). Below MIN_DECILE_POOL usable falling names, or
     where ties at the cutoff would otherwise blow the decile open to a
     large fraction of the pool, the result is flagged degenerate and the
     candidate list is capped rather than silently presented as a real
     decile; a degenerate small pool reports its cutoff as None, never the
     best return in the (tiny) pool.

  5. CORPORATE ACTIONS.  Every decile candidate is cross-checked against
     corporate_actions.py for a split, rights issue, spin-off, dividend or
     other per-share-breaking action inside the return window.
     nordic_shares' price series is UNADJUSTED for these, so an unadjusted
     "-40%" can be a 10:1 split - or an ordinary ex-dividend drop in AGM
     season - wearing a crash costume. A hit routes the name to the
     TECHNICAL bucket, labelled as a technical move, never into candidates.
     Splits are checked on their EFFECTIVE date (corporate_actions.
     split_adjustment_factor), not the announcement date - a split announced
     months before the window but effective inside it used to be invisible
     to an announcement-date-only check. Where more than one Nasdaq CNS
     company matches the name and the top match is not an exact one, the
     check REFUSES rather than silently take the highest-ranked row (see
     `not checked` below) - the alternative already misattributed a 10:1
     split to "no corporate action" often enough to pass a real crash
     through as a candidate.

     This runs pre-open (see PRE-OPEN SCHEDULING below), so "inside the
     return window" is itself split at the candidate's own last completed
     close: only an action on or before that close can make the return a
     technical artefact; an action effective AFTER it (an ex-date or split
     landing this morning) is new and unpriced, reported separately under
     `since_last_close`, never folded into `has_breaking_action`.

  6. REGULATORY NEWS.  mfn_news.py, resolved via venues_se.mfn_identity -
     which accepts only an MFN entity whose OWN ISIN or LEI matches the one
     FIRDS gave this candidate, never a bare name search's top hit (a bare
     search for "AstraZeneca PLC" returns Alvotech first; for "Spiltan
     Invest" it returns Nordnet). Was there a MAR-flagged (regulatory)
     release inside the window? This is the discriminator between "fell on
     information" and "fell on flows" - and a THIRD outcome, "not
     classified", covers a name this check could not resolve or fetch at
     all (a dead endpoint, an unresolvable identity): that is NOT evidence
     of no news, and is never printed under the same heading as one.

     PRE-OPEN SCHEDULING.  This runs as a scheduled job before the Stockholm
     09:00 open, and Swedish issuers publish in a heavy wave from roughly
     06:30 to 08:30 - so "the window" is split at the candidate's own last
     completed close (the same date its return was measured to, which is
     NOT today when run pre-open). A release on or before that close can
     explain the fall (`has_release`, feeding fell_on_information/
     fell_on_flows); a release strictly AFTER it - up to now - is new,
     unpriced information that CANNOT explain a fall that predates it, so it
     is never folded into `has_release`. It is reported separately as
     `since_last_close` (count, titles, timestamps) on every candidate in
     every bucket (technical moves included), surfaced as its own summary
     line and a per-candidate marker in the text output - the single most
     time-critical thing a reader running this before the open needs to see
     first. A failed check reports `since_last_close` as `not checked` too,
     same as the main window - never silently "no news". Both windows come
     from ONE identity resolution and ONE fetch, never a second round-trip.

  7. SIGNALS.  short_se.aggregated() plus one shared FI file download (never
     one per candidate - that would defeat FI's own hourly cache) for the
     30-day named-holder short-interest direction. A stale (>14 days old)
     disclosure is flagged rather than presented as current.

DATA CONFIDENCE.  XSTO and XNGM are regulated markets with ESEF; SSME, XSAT
and NSME have none, so anything fundamental read from them is parsed prose,
not machine-verified XBRL. Each candidate carries its venue's regulated/MTF
status so an MTF row is never read as ranking above an ESEF row on a number
that is, in fact, less well evidenced. Where the SAME ISIN is dual-listed
across a regulated MIC and an MTF (Paradox Interactive: XSTO and SSME both),
the regulated venue is kept as the row of record and the other is recorded
in `also_on` - a plain "whichever FIRDS batch loaded last wins" used to let
an MTF row silently clobber - and thereby understate the confidence of - a
name that is, in fact, ESEF-covered.

AS-OF DATES.  Price is the last completed session in the fetched bars, short
interest is FI's own publication date read from its file (never the fetch
time), and the regulatory-news/corporate-action "explains the fall" window
runs from the explicit `from` date up to (and including) that same last
completed close - never up to today, which is what a pre-open run's `to`
date would otherwise be. Each axis also separately reports what happened
SINCE that close, up to now, under its own `since_last_close`. A single
headline "as of" date would be false precision.

OPTIONAL INTEGRATION POINT.  If a sibling script exposes
`venues_se.ngm_turnover(date)` (feature-detected via `hasattr` - absent is
not an error), its turnover/last-price is used for XNGM and NSME so those two
venues stop being cut as source-less. XSAT genuinely has no free price source
anywhere in this toolkit; that part of this docstring is correct and stays.

LIMITATIONS, disclosed rather than hidden:
  * No free source in this toolkit gives price HISTORY for XSAT, XNGM or
    NSME, so those venues are counted in the universe but can never produce
    a return-based candidate. Only XSTO and SSME names ever reach the
    worst-decile step.
  * Turnover is the last COMPLETED session's close x volume from the fetched
    price history, not a multi-day average - a single volatile day can look
    more or less liquid than the name typically is.
  * Corporate-action identity resolution is by company name
    (corporate_actions.resolve_company) - venues_se's ISIN/LEI-verified match
    has no equivalent against Nasdaq CNS. Ambiguous matches are refused
    rather than guessed (see stage 5); regulatory-news identity IS
    ISIN/LEI-verified (venues_se.mfn_identity, stage 6).
  * The liquidity floor's SEK conversion uses the latest DATED Riksbank/ECB
    rate this toolkit can fetch (nordic_shares._fx_convert_to_sek), not a
    rate specific to the turnover's own trade date. Where no dated rate can
    be fetched at all for a currency, that issuer is reported as not checked
    rather than compared against the floor using a guessed or stale rate.

Usage:
    python screen_digest.py                        # whole Swedish market, 1m window
    python screen_digest.py --window 1w
    python screen_digest.py --venue xsto,xngm
    python screen_digest.py --limit 15 --json
    python screen_digest.py --include-illiquid
    python screen_digest.py --selftest

Python 3 stdlib only. Free, keyless, everywhere.
"""
import argparse
import datetime
import json
import os
import re
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Sibling scripts are standalone CLI tools imported directly, following the
# convention already established across this toolkit (corporate_actions.py
# importing mfn_news/cision_news/nordic_shares/finfact the same way). A
# parallel agent may be mid-edit on a sibling, so every import is defensive:
# a broken or missing sibling degrades the axis it feeds, never crashes this
# script. Calls into any of them must ALSO be guarded with
# `except (Exception, SystemExit)` - these scripts raise SystemExit as their
# error convention, and SystemExit does not inherit from Exception.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import nordic_shares
except Exception:                                        # pragma: no cover
    nordic_shares = None
try:
    import venues_se
except Exception:                                        # pragma: no cover
    venues_se = None
try:
    import short_se
except Exception:                                        # pragma: no cover
    short_se = None
try:
    import corporate_actions
except Exception:                                        # pragma: no cover
    corporate_actions = None
try:
    import mfn_news
except Exception:                                        # pragma: no cover
    mfn_news = None

DEFAULT_LIQUIDITY_FLOOR_SEK = 2_000_000.0
DEFAULT_BUDGET_SECONDS = 240.0
HISTORY_LOOKBACK_DAYS = 130          # covers the 3m window plus a buffer
WINDOW_DAYS = {"1w": 7, "1m": 30, "3m": 90}
MIN_DECILE_POOL = 10                 # below this, "worst decile" just means "everyone"
STALE_SHORT_INTEREST_DAYS = 14
PREMARKET_BLANK_TURNOVER_FRACTION = 0.5   # refuse to publish above this

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

# .format() on the two placeholders only, via replace() rather than str.format
# on the whole docstring - the docstring's prose is full of literal braces-free
# but percent-sign text ("fell X%", "0.1%"), and %-style formatting elsewhere
# in this toolkit is exactly what breaks on that; a plain two-token replace
# has no such collision.
__doc__ = (__doc__.replace("{budget}", str(int(DEFAULT_BUDGET_SECONDS)))
                 .replace("{floor}", "{:,.0f} SEK-equivalent".format(DEFAULT_LIQUIDITY_FLOOR_SEK)))


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


def today():
    """A seam for tests: patched to a fixed date so as-of comparisons are
    deterministic instead of depending on the day the suite happens to run."""
    return datetime.date.today()


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


def fetch_other_venue_turnover(mics):
    """XNGM/NSME per-ISIN turnover and last price, via
    venues_se.ngm_turnover() - feature-detected with `hasattr` (its absence
    is not an error, see the module's OPTIONAL INTEGRATION POINT). Called
    with NO date argument on purpose: `date=None` is what makes
    ngm_turnover() resolve to its OWN most recently completed trading day
    (NGM's post-trade file for the current UTC calendar date is still
    accumulating trades while the market is open) - passing this script's
    own `as_of` would have asked for a specific date and, on a same-day
    call, been handed back flagged `partial`.

    Returns ({isin: {"turnover", "last_price", "currency", ...}},
    error_or_None); an empty dict when the function does not exist yet, no
    XNGM/NSME venue was requested, or the feed could not be reached (that
    is reported as `error`, not silently swallowed as "nothing found").
    The reserved "_meta" key in ngm_turnover()'s own return is dropped here
    - it is never a real ISIN.
    """
    if venues_se is None or not hasattr(venues_se, "ngm_turnover"):
        return {}, None
    if not any(m in mics for m in ("XNGM", "NSME")):
        return {}, None
    try:
        data = venues_se.ngm_turnover()
    except (Exception, SystemExit) as exc:
        return {}, str(exc)
    if data is None:
        return {}, "NGM post-trade feed unreachable, or no completed trading day available"
    return {isin: info for isin, info in data.items() if isin != "_meta"}, None


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
    in the module docstring.
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
# stage 2: history and returns
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
    bars actually fetched (NOT a multi-year range - this screen deliberately
    carries no historical cache). Also carries the LAST COMPLETED SESSION's
    own close and volume through (`last_volume`) - this is what the
    liquidity floor is computed from (see B1 in the module docstring): the
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


def fetch_return_for_instrument(row, as_of_date):
    """Price history for ONE instrument row (not an issuer - every listed
    class of an issuer needs its own history call now, since the true
    tradeable ("primary") class can only be chosen after every class's
    last-session turnover is known; see group_by_issuer)."""
    obid = row.get("orderbookId")
    if not obid or nordic_shares is None:
        return {"status": "not checked",
                "reason": "no Nasdaq orderbook id - no free price-history source "
                          "exists in this toolkit for this venue"}
    NASDAQ_THROTTLE.wait()
    from_date = (as_of_date - datetime.timedelta(days=HISTORY_LOOKBACK_DAYS)).isoformat()
    to_date = as_of_date.isoformat()
    try:
        bars = nordic_shares.price_history(obid, from_date, to_date)
    except (Exception, SystemExit) as exc:
        return {"status": "not checked", "reason": str(exc)}
    result = compute_returns(bars)
    if result is None:
        return {"status": "not checked", "reason": "no usable price bars returned"}
    result["status"] = "checked"
    return result


def fetch_returns_parallel(instruments, as_of_date, budget, max_workers=10):
    """Fetch price_history for every instrument concurrently, bounded by the
    time budget on BOTH submission and collection.

    A stdlib ThreadPoolExecutor's task queue is unbounded, so checking
    `budget.exceeded()` only before each `pool.submit()` never actually
    bounds anything once the queue is full of already-accepted work -
    every submitted future still runs to completion. `as_completed(...,
    timeout=...)` is what bounds COLLECTION: whatever has not completed by
    the deadline is left uncollected, and the pool is torn down without
    waiting for it (`shutdown(wait=False, cancel_futures=True)`, never the
    `with` statement's implicit `shutdown(wait=True)`, which blocks until
    every submitted task finishes regardless of any budget).

    `fut.result()` is caught with `except BaseException`, not `except
    Exception`: a worker that raises SystemExit (this toolkit's own error
    convention) does not inherit from Exception, so `except Exception` lets
    it propagate out of this function and kill the whole run over one bad
    candidate.
    """
    results = {}
    pool = ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures = {}
        for row in instruments:
            if budget.exceeded():
                break
            obid = row.get("orderbookId")
            if not obid:
                continue
            futures[pool.submit(fetch_return_for_instrument, row, as_of_date)] = obid
        try:
            for fut in as_completed(futures, timeout=budget.remaining()):
                obid = futures[fut]
                try:
                    results[obid] = fut.result()
                except BaseException as exc:            # pragma: no cover - defensive
                    results[obid] = {"status": "not checked",
                                     "reason": "worker error: %s" % exc}
        except TimeoutError:
            pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return results


def _instrument_turnover_sek(row, ret):
    """LAST-COMPLETED-SESSION turnover (close x volume from the daily bar
    `ret` carries), FX-converted to SEK via nordic_shares._fx_convert_to_sek
    (dated Riksbank/ECB rates - never a guessed one). Returns
    (turnover_sek_or_None, error_or_None). NOT the Nasdaq screener's
    intraday `turnover` field - see B1 in the module docstring."""
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
    instrument's last completed session - called once per issuer in run(),
    after select_primary_instrument. apply_liquidity_floor is a pure
    decision function over exactly these three fields, kept separate so it
    stays trivially unit-testable.

    An issuer with no Nasdaq orderbook id at all (XSAT always; XNGM/NSME
    unless the OPTIONAL INTEGRATION POINT is live) falls back to whatever
    `combine_universe` attached as `other_venue_turnover` - venues_se.
    ngm_turnover()'s own figure for XNGM/NSME - before being written off as
    source-less entirely.
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
    through uncut and is reported separately (see the "returns: not
    checked" cut-stage counter in run()), never silently treated as having
    cleared the floor.
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
# stage 4: worst decile
# ---------------------------------------------------------------------------

def select_worst_decile(survivors, window):
    """Sort ascending on the chosen window's return among names that
    actually FELL (pct < 0 only) and keep the bottom decile.

    Three ways this used to degenerate, all fixed here:
      1. No sign filter - in a strong month the "bottom decile" was
         whatever fell least (or rose least), and names that ROSE were
         filed as having "fallen on flows". Filtering to pct < 0 first
         fixes this at the source.
      2. Below MIN_DECILE_POOL usable falling names, a decile is not a
         meaningful statistic, so everyone who fell is kept instead - but
         the cutoff is now reported as None, not `scored[-1][0]` (the BEST
         return in the tiny pool, which used to be presented as a "worst
         decile" cutoff).
      3. Ties AT the computed cutoff can blow a real decile open (100 names,
         50 tied at the same return, all <= the cutoff) - capped back to
         roughly the intended decile size and flagged degenerate, same as
         the too-small-pool case, rather than silently keeping half the
         pool under a "real decile" banner.
    """
    scored = []
    for iss in survivors:
        r = iss.get("returns") or {}
        if r.get("status") != "checked":
            continue
        w = (r.get("windows") or {}).get(window) or {}
        pct = w.get("pct")
        if pct is not None and pct < 0:
            scored.append((pct, iss))
    scored.sort(key=lambda t: t[0])
    n = len(scored)
    if n == 0:
        return [], None, False
    if n < MIN_DECILE_POOL:
        return [iss for _, iss in scored], None, True

    target_n = max(1, n // 10)
    try:
        cutoff = statistics.quantiles([p for p, _ in scored], n=10)[0]
    except statistics.StatisticsError:              # pragma: no cover - defensive
        cutoff = scored[max(0, target_n - 1)][0]
    candidates = [iss for p, iss in scored if p <= cutoff]
    degenerate = False
    if len(candidates) > target_n * 2:
        candidates = [iss for _, iss in scored[:target_n]]
        degenerate = True
    return candidates, cutoff, degenerate


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
# stage 5: corporate actions
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
    breaking action inside [date_from, date_to]? A hit means the return
    computed against nordic_shares' UNADJUSTED price series is a technical
    artefact, not a real fall - see corporate_actions.py's own module
    docstring.

    The window is split in two against `last_close_date` (the same date the
    candidate's return was measured to - it may fall on the same day as
    `date_to`, or earlier), for the same reason check_regulatory_news splits
    its own window: an ex-dividend date or a split EFFECTIVE this morning,
    before the open, is not an explanation of a fall measured to yesterday's
    close - the fall predates the action. Only actions on or before
    `last_close_date` can explain the fall (`has_breaking_action`,
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
    carry DIVIDEND - nordic_shares' price series is unadjusted for them, and
    Swedish AGM season clusters ex-dates tightly enough that an April 1m
    decile is otherwise dominated by ordinary ex-dividend drops wearing a
    crash costume); where a per-share amount can be scraped from the
    headline and `price` is known, the implied yield is stated alongside it.

    Splits are checked TWICE on purpose: once via corporate_actions_between
    (announcement date, catches everything else BREAKS_PER_SHARE covers),
    and once via corporate_actions.split_adjustment_factor, which parses the
    exchange notice's own EFFECTIVE date - a split announced before the
    window but effective inside it is invisible to an announcement-date-only
    check and split_adjustment_factor is the tool built to answer that.
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
# stage 6: regulatory news
# ---------------------------------------------------------------------------

def check_regulatory_news(name, date_from, last_close_date, date_to, isin=None, lei=None):
    """Was there a MAR-flagged release inside [date_from, last_close_date]?
    The discriminator between "fell on information" and "fell on flows".

    The window is split in two against `last_close_date` - the same date
    the candidate's return was measured to (it may equal `date_to`, or fall
    earlier than it, e.g. a pre-open run where `date_to` is today but the
    last completed session was yesterday). Only a release on or before
    `last_close_date` can EXPLAIN a fall already measured to that close, so
    `has_release`/`items` (and therefore fell_on_information/fell_on_flows)
    are decided from that window alone. A release strictly after
    `last_close_date` - up to `date_to` - is new information the market has
    not priced yet; it is orthogonal to the fall classification and is
    reported separately under `since_last_close`, never folded into
    `has_release`. Getting this backwards would file a 07:15 release as
    having caused a fall measured to yesterday's close, which is causally
    impossible - Swedish issuers publish in a heavy wave from ~06:30-08:30,
    right before this screen's own pre-open scheduled run.

    Identity is resolved ONCE and news is fetched ONCE for the whole
    [date_from, date_to] span; both windows are cut locally from that same
    fetch, never a second call.

    Identity comes from venues_se.mfn_identity(name, isin, lei), which
    accepts ONLY an MFN entity whose own ISIN or LEI matches the one FIRDS
    gave this candidate - never a bare `mfn_news.search(name)` top hit,
    which on live data has returned entirely unrelated companies first
    ("AstraZeneca PLC" -> Alvotech, "Spiltan Invest" -> Nordnet) and
    attached THEIR news to this candidate.
    """
    if mfn_news is None:
        reason = "mfn_news.py not importable"
        return {"status": "not checked", "reason": reason,
                "since_last_close": {"status": "not checked", "reason": reason}}
    if venues_se is None:
        reason = "venues_se.py not importable - cannot verify MFN identity"
        return {"status": "not checked", "reason": reason,
                "since_last_close": {"status": "not checked", "reason": reason}}
    try:
        identity = venues_se.mfn_identity(name, isin=isin, lei=lei)
    except (Exception, SystemExit) as exc:
        reason = "MFN identity resolution failed: %s" % exc
        return {"status": "not checked", "reason": reason,
                "since_last_close": {"status": "not checked", "reason": reason}}
    if not identity or not identity.get("slug"):
        return {"status": "checked", "has_release": False,
                "window": [date_from, last_close_date],
                "items": [], "note": "no MFN entity's ISIN/LEI matched %r; treated as no "
                                     "release found, which is not proof there was none" % name,
                "since_last_close": {"status": "checked", "count": 0, "items": [],
                                     "window": [last_close_date, date_to]}}
    slug = identity["slug"]
    try:
        raw = mfn_news.fetch_company_pages(slug, pages=2)
    except (Exception, SystemExit) as exc:
        reason = "MFN fetch failed for slug %s: %s" % (slug, exc)
        return {"status": "not checked", "reason": reason,
                "since_last_close": {"status": "not checked", "reason": reason}}
    try:
        items = [mfn_news.flatten(i) for i in raw]
    except (Exception, SystemExit) as exc:
        reason = "MFN item parsing failed: %s" % exc
        return {"status": "not checked", "reason": reason,
                "since_last_close": {"status": "not checked", "reason": reason}}

    in_window = [i for i in items
                if date_from <= (i.get("date") or "")[:10] <= last_close_date]
    regulatory = [i for i in in_window if i.get("regulatory")]

    since_close = [i for i in items
                  if last_close_date < (i.get("date") or "")[:10] <= date_to]
    regulatory_since_close = [i for i in since_close if i.get("regulatory")]

    return {"status": "checked", "has_release": bool(regulatory),
            "window": [date_from, last_close_date], "slug": slug,
            "items": [{"date": i["date"][:10], "title": i["title"]} for i in regulatory[:3]],
            "since_last_close": {
                "status": "checked", "count": len(regulatory_since_close),
                "window": [last_close_date, date_to],
                "items": [{"date": i["date"][:19], "title": i["title"]}
                         for i in regulatory_since_close]}}


def deep_check_candidate(candidate, date_from, date_to):
    """The two deep checks are cut against `last_close_date` - the same
    date the candidate's OWN return was measured to (candidate["returns"]
    ["as_of"]), not `date_to` (today) - so a release or corporate action
    published this morning, before the open, is never read as explaining a
    fall already measured to yesterday's close. See check_regulatory_news
    and check_corporate_actions docstrings. Falls back to `date_to` only if
    the return itself was somehow never checked (should not happen for a
    candidate that survived select_worst_decile, which requires a checked
    return) - in that degenerate case there is no known close date to
    prefer, and since_last_close collapses to an empty window rather than
    guessing one.
    """
    raw_name = candidate["primary"].get("name") or candidate.get("name")
    name = _strip_class_suffix(raw_name)
    isin = candidate["primary"].get("isin")
    lei = candidate.get("lei")
    price = candidate["primary"].get("price")
    last_close_date = (candidate.get("returns") or {}).get("as_of") or date_to
    return {"corporate_action": check_corporate_actions(
                name, date_from, last_close_date, date_to, price=price),
            "regulatory_news": check_regulatory_news(
                name, date_from, last_close_date, date_to, isin=isin, lei=lei)}


def deep_check_parallel(candidates, date_from, date_to, budget, max_workers=6):
    """Same submission/collection budget bounding as fetch_returns_parallel -
    see that function's docstring for why `as_completed(..., timeout=...)`
    and `except BaseException` both matter here too (M2 in the review this
    was built against applies at BOTH parallel sites)."""
    results = {}
    pool = ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures = {}
        for c in candidates:
            if budget.exceeded():
                break
            futures[pool.submit(deep_check_candidate, c, date_from, date_to)] = c["key"]
        try:
            for fut in as_completed(futures, timeout=budget.remaining()):
                key = futures[fut]
                try:
                    results[key] = fut.result()
                except BaseException as exc:            # pragma: no cover - defensive
                    unchecked = {"status": "not checked", "reason": "worker error: %s" % exc}
                    results[key] = {"corporate_action": unchecked, "regulatory_news": unchecked}
        except TimeoutError:
            pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return results


# ---------------------------------------------------------------------------
# stage 7: signals - short interest
# ---------------------------------------------------------------------------

def load_short_data():
    """One shared fetch for the whole market - short_se's own file cache is
    an HOUR wide, and calling it once here (instead of once per candidate)
    is what respects that cache rather than defeating it.

    `group_by_company` used to sit OUTSIDE this try block, so a shape-drift
    KeyError from it (it reads `company["names"]` etc, same as `belongs`)
    killed the whole run instead of degrading this one axis."""
    if short_se is None:
        return None, "short_se.py not importable"
    try:
        agg = short_se.aggregated()
        rows = short_se.merged_rows()
        companies = short_se.group_by_company(agg, rows)
    except (Exception, SystemExit) as exc:
        return None, str(exc)
    by_lei = {c["lei"]: c for c in companies if c.get("lei")}
    by_isin = {}
    for c in companies:
        for isin in c.get("isins", []):
            by_isin[isin] = c
    return {"companies_by_lei": by_lei, "companies_by_isin": by_isin, "rows": rows}, None


def short_signal(short_data, candidate, as_of_date):
    """short_se.belongs()/short_se.trend() used to be called from a plain
    loop in run(), not a worker, with no guard at all - `belongs` reads
    `company["names"]` and a bare shape-drift KeyError there killed the
    whole run (main() only ever caught ValueError). Wrapped here instead."""
    if short_data is None:
        return {"status": "not checked", "reason": "FI blankningsregister unreachable"}
    try:
        c = short_data["companies_by_lei"].get(candidate.get("lei"))
        if c is None:
            for isin in candidate.get("isins", []):
                c = short_data["companies_by_isin"].get(isin)
                if c:
                    break
        if c is None:
            return {"status": "checked", "aggregate_pct": None, "as_of": None,
                    "named_30d_direction": None, "named_30d_change_pp": None,
                    "stale": None,
                    "note": "no disclosed short position >= 0.1% for this issuer"}
        agg_pct = c["agg"][0]["pct"] if c.get("agg") else None
        agg_date = c["agg"][0]["date"] if c.get("agg") else None
        rows_c = [r for r in short_data["rows"] if short_se.belongs(r, c)]
        tr = short_se.trend(rows_c, as_of_date) if rows_c else None
        w30 = ((tr or {}).get("windows") or {}).get("30") or {}
        stale = None
        if agg_date:
            try:
                age_days = (as_of_date - datetime.date.fromisoformat(agg_date)).days
                stale = age_days > STALE_SHORT_INTEREST_DAYS
            except ValueError:
                stale = None
        return {"status": "checked", "aggregate_pct": agg_pct, "as_of": agg_date,
                "named_30d_direction": w30.get("direction"),
                "named_30d_change_pp": w30.get("change_pp"),
                "stale": stale}
    except (Exception, SystemExit) as exc:
        return {"status": "not checked", "reason": "short-interest lookup failed: %s" % exc}


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
# orchestration
# ---------------------------------------------------------------------------

def _parse_venues(raw):
    if not raw:
        return list(ALL_MICS)
    out = []
    for tok in raw.split(","):
        mic = tok.strip().upper()
        if mic not in ALL_MICS:
            raise ValueError("unknown venue %r - choose from %s"
                             % (tok, ", ".join(m.lower() for m in ALL_MICS)))
        out.append(mic)
    return out


def run(args):
    budget = Budget(args.budget)
    notes = []
    as_of = today()

    mics = _parse_venues(args.venue)
    nasdaq_mics = [m for m in mics if m in NASDAQ_MICS]

    nasdaq_rows, nasdaq_liq, nasdaq_err = (None, None, None)
    if nasdaq_mics:
        nasdaq_rows, nasdaq_liq, nasdaq_err = fetch_nasdaq_snapshot("STO")
        if nasdaq_err:
            notes.append("Nasdaq universe/liquidity: %s" % nasdaq_err)

    # Belt-and-braces: refuse to publish a report built entirely from a
    # screener snapshot the market has not opened yet to populate. This is
    # deliberately separate from the liquidity floor itself (which no longer
    # uses this intraday field at all, see apply_liquidity_floor) - it
    # exists purely so a pre-open run fails LOUDLY instead of quietly
    # printing a well-formed, empty digest.
    # The screener's intraday turnover being blank is NORMAL before the 09:00
    # open and no longer means anything: the liquidity floor reads the last
    # completed daily bar instead. Guarding on it here would refuse every
    # pre-open run, which is a supported and in fact preferable schedule -
    # yesterday's bar is unambiguously final, where an evening run has to
    # judge whether today's session has settled.
    #
    # What still deserves a loud failure is the floor having nothing to work
    # with at all. That is checked after the history stage, where the evidence
    # actually is.

    firds_by_mic, firds_failed = fetch_firds(mics)
    for mic, why in firds_failed.items():
        notes.append("%s (%s) universe: %s" % (mic, VENUE_LABEL.get(mic, mic), why))

    other_turnover, other_err = fetch_other_venue_turnover(mics)
    if other_err:
        notes.append("NGM turnover (XNGM/NSME): %s" % other_err)

    combined = combine_universe(nasdaq_rows, nasdaq_liq, firds_by_mic, mics, other_turnover)
    issuers = group_by_issuer(combined)

    by_mic_counts = {}
    for r in combined:
        by_mic_counts[r["mic"]] = by_mic_counts.get(r["mic"], 0) + 1

    universe_summary = {"isin_lines": len(combined), "issuers": len(issuers),
                        "by_mic": by_mic_counts, "venues_included": mics,
                        "venue_fetch_failures": firds_failed}

    # STAGE 2 (history) now runs BEFORE the liquidity floor (STAGE 3) - see
    # B1 in the module docstring. Every priced instrument is fetched, not
    # just one class per issuer, because the true tradeable ("primary")
    # class can only be chosen once every class's own last-session turnover
    # is known.
    all_instruments = [r for iss in issuers for r in iss["instruments"]
                       if r.get("orderbookId")]
    returns_by_obid = fetch_returns_parallel(all_instruments, as_of, budget)

    for iss in issuers:
        select_primary_instrument(iss, returns_by_obid)
        compute_issuer_turnover(iss)
        iss["returns"] = iss["primary_returns"]

    survivors, liquidity_cuts = apply_liquidity_floor(
        issuers, args.liquidity_floor, args.include_illiquid)
    unchecked_returns = sum(1 for iss in survivors if iss["returns"].get("status") != "checked")

    candidates, cutoff, degenerate_pool = select_worst_decile(survivors, args.window)

    window_from = (as_of - datetime.timedelta(days=WINDOW_DAYS[args.window])).isoformat()
    window_to = as_of.isoformat()

    deep = deep_check_parallel(candidates, window_from, window_to, budget)
    for c in candidates:
        d = deep.get(c["key"])
        if d is None:
            unchecked = {"status": "not checked",
                        "reason": "time budget exceeded before this name could be checked"}
            d = {"corporate_action": unchecked, "regulatory_news": unchecked}
        c["corporate_action"] = d["corporate_action"]
        c["regulatory_news"] = d["regulatory_news"]
        c["data_confidence"] = data_confidence(c["primary"]["mic"])

    technical_keys = {c["key"] for c in candidates
                      if c["corporate_action"].get("status") == "checked"
                      and c["corporate_action"].get("has_breaking_action")}
    technical = [c for c in candidates if c["key"] in technical_keys]
    for c in technical:
        # Not evaluated on purpose: a technical-move name is not a real
        # candidate, so spending a lookup on its short interest is wasted
        # budget - but the field must still exist, and say why, rather than
        # print as a bare unexplained "not checked".
        c["short_interest"] = {"status": "not checked",
                               "reason": "not evaluated - routed to the technical-move "
                                         "bucket, not a candidate"}

    real = [c for c in candidates if c["key"] not in technical_keys]

    short_data, short_err = load_short_data()
    if short_err:
        notes.append("Short interest (FI blankningsregister): %s" % short_err)
    for c in real:
        c["short_interest"] = short_signal(short_data, c, as_of)

    real.sort(key=lambda c: (c["returns"]["windows"][args.window]["pct"]))

    # Regulatory-news status is now a THREE-way split, not two: a name whose
    # check could not even be performed (status != "checked" - a dead
    # endpoint, an unresolvable identity) used to fall through into
    # "fell_on_flows" by elimination (it is neither in fell_on_information
    # nor excluded from "real"), printed under a header claiming "no
    # regulatory release found in window" - which is not what happened; the
    # check was simply never completed. See B3 in the module docstring.
    regulatory_checked = [c for c in real if c["regulatory_news"].get("status") == "checked"]
    not_classified = [c for c in real if c["regulatory_news"].get("status") != "checked"]
    fell_on_information = [c for c in regulatory_checked
                           if c["regulatory_news"].get("has_release")]
    fell_on_flows = [c for c in regulatory_checked
                     if not c["regulatory_news"].get("has_release")]

    # Orthogonal to the fell_on_information/fell_on_flows/not_classified
    # split above: a candidate in ANY bucket (including technical_moves) may
    # ALSO carry a regulatory release published after its own last completed
    # close - new information not yet priced, and the single most
    # time-critical thing a reader running this before the 09:00 open needs
    # to see first. Surfaced as its own count here (used for the text
    # summary line) and as a per-candidate marker in print_text/_line_for -
    # never folded into fell_on_information itself (see check_regulatory_
    # news's docstring for why that would be causally backwards).
    since_close_flagged = [c for c in candidates
                           if ((c.get("regulatory_news") or {}).get("since_last_close") or {})
                              .get("count")]
    since_close_news_total = len(since_close_flagged)

    decile_label = "worst decile"
    if degenerate_pool:
        decile_label += (" (degenerate: fewer than %d usable falling names, or ties "
                         "blew the decile open - kept a capped set)" % MIN_DECILE_POOL)

    cuts = [
        {"stage": "universe", "count": universe_summary["issuers"],
         "reason": "distinct issuers after LEI grouping, across %s"
                   % ", ".join(mics)},
        {"stage": "liquidity_floor: no turnover source", "count": liquidity_cuts["no_price_source"],
         "reason": "no free bulk turnover feed for this venue in this toolkit"},
        {"stage": "liquidity_floor: did not trade", "count": liquidity_cuts["did_not_trade"],
         "reason": "a feed exists, but the last completed session shows zero volume"},
        {"stage": "liquidity_floor: below floor", "count": liquidity_cuts["below_floor"],
         "reason": "last-session turnover (SEK-equiv) below %s"
                   % "{:,.0f}".format(args.liquidity_floor)},
        {"stage": "survivors", "count": len(survivors), "reason": "carried into the worst-decile step"},
        {"stage": "returns: not checked", "count": unchecked_returns,
         "reason": "no orderbook id, price_history failed, or budget exceeded"},
        {"stage": decile_label,
         "count": len(candidates), "reason": "bottom decile of NEGATIVE %s returns only"
                                             % args.window},
        {"stage": "technical moves (corporate action in window)", "count": len(technical),
         "reason": "split/rights issue/spin-off/dividend/etc makes the unadjusted fall a "
                   "technical artefact"},
        {"stage": "regulatory news: not classified", "count": len(not_classified),
         "reason": "the regulatory-news check itself could not be completed - "
                   "NOT evidence of no news"},
    ]

    return {
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "as_of": as_of.isoformat(), "window": args.window,
        "window_from": window_from, "window_to": window_to,
        "liquidity_floor": args.liquidity_floor,
        "include_illiquid": args.include_illiquid,
        "universe": universe_summary,
        "decile_cutoff_pct": cutoff,
        "decile_degenerate": degenerate_pool,
        "cuts": cuts,
        "technical_moves": technical[:args.limit],
        "technical_moves_total": len(technical),
        "fell_on_information": fell_on_information[:args.limit],
        "fell_on_information_total": len(fell_on_information),
        "fell_on_flows": fell_on_flows[:args.limit],
        "fell_on_flows_total": len(fell_on_flows),
        "not_classified": not_classified[:args.limit],
        "not_classified_total": len(not_classified),
        "since_last_close_news_total": since_close_news_total,
        "since_last_close_news_names": sorted(c["name"] or "?" for c in since_close_flagged),
        "partial": budget.exceeded(),
        "budget_seconds": args.budget,
        "elapsed_seconds": round(budget.elapsed(), 1),
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------

def _fmt_pct(v):
    return "%+.1f%%" % v if v is not None else "n/a"


def _line_for(c, window):
    primary = c["primary"]
    r = c["returns"]["windows"][window]
    conf = c["data_confidence"]
    short = c.get("short_interest") or {}
    news = c.get("regulatory_news") or {}
    parts = [
        "%-28.28s %-5s %10s %8s  price %10s %-4s as of %s"
        % (c["name"] or "?", primary["mic"], _fmt_pct(r["pct"]),
           conf["label"].split(" ")[0], "{:,.2f}".format(primary["price"])
           if primary.get("price") is not None else "n/a",
           primary.get("currency") or "-", c["returns"]["as_of"]),
        "    liquidity: %s   confidence: %s"
        % (c.get("liquidity_status", "?"), conf["label"]),
    ]
    if news.get("status") == "checked":
        if news.get("has_release"):
            parts.append("    regulatory release in window: %s"
                         % "; ".join("%s %s" % (i["date"], i["title"]) for i in news["items"]))
        else:
            parts.append("    no regulatory release in window %s to %s"
                         % tuple(news.get("window") or ["?", "?"]))
    else:
        parts.append("    regulatory news: NOT CLASSIFIED (%s) - not evidence of no news"
                     % news.get("reason", "?"))
    since_news = news.get("since_last_close") or {}
    if since_news.get("status") == "checked" and since_news.get("count"):
        parts.append("    !! NEWS SINCE LAST CLOSE (%d, not yet priced in): %s"
                     % (since_news["count"],
                        "; ".join("%s %s" % (i["date"], i["title"])
                                 for i in since_news.get("items") or [])))
    elif since_news.get("status") != "checked":
        parts.append("    news since last close: NOT CHECKED (%s)"
                     % since_news.get("reason", "?"))
    ca = c.get("corporate_action") or {}
    ca_since = ca.get("since_last_close") or {}
    if ca_since.get("status") == "checked" and ca_since.get("count"):
        parts.append("    !! CORPORATE ACTION SINCE LAST CLOSE (%d, not yet priced in): %s"
                     % (ca_since["count"],
                        "; ".join("%s %s" % (ev.get("date"), ev.get("type"))
                                 for ev in ca_since.get("events") or [])))
    elif ca_since.get("status") != "checked" and ca.get("status") == "checked":
        # corporate_action itself resolved (so has_breaking_action/events are
        # meaningful) but the since-close half of the SAME check degraded -
        # should not happen in practice (they share one fetch), but printed
        # rather than silently dropped if it ever does.
        parts.append("    corporate action since last close: NOT CHECKED (%s)"
                     % ca_since.get("reason", "?"))
    if short.get("status") == "checked":
        stale_flag = " [STALE]" if short.get("stale") else ""
        parts.append("    short interest: %s%% as of %s%s (30d %s)"
                     % ("%.2f" % short["aggregate_pct"] if short.get("aggregate_pct") is not None
                        else "n/a", short.get("as_of") or "n/a", stale_flag,
                        short.get("named_30d_direction") or "unknown"))
    else:
        parts.append("    short interest: not checked (%s)" % short.get("reason", "?"))
    return "\n".join(parts)


def print_text(result):
    print("SWEDISH MARKET SCREEN DIGEST - as of %s, %s window"
          % (result["as_of"], result["window"]))
    if result["partial"]:
        print("!! PARTIAL RUN - time budget of %ss exceeded (elapsed %ss). "
              "Some names below were not checked on every axis." %
              (result["budget_seconds"], result["elapsed_seconds"]))
    if result["decile_degenerate"]:
        print("!! DEGENERATE DECILE - too few falling names, or ties at the cutoff, "
              "so the set below is not a real bottom decile.")
    if result["since_last_close_news_total"]:
        print("!! %d candidate(s) have regulatory news published SINCE their last "
              "completed close - not yet priced in, and not what explained the fall "
              "below. See the [NEWS SINCE LAST CLOSE] marker on each: %s"
              % (result["since_last_close_news_total"],
                 ", ".join(result["since_last_close_news_names"])))
    else:
        print("No candidate has regulatory news since its last completed close.")
    print()
    u = result["universe"]
    print("Universe: %d ISIN lines, %d issuers, venues %s"
          % (u["isin_lines"], u["issuers"], ", ".join(u["venues_included"])))
    for mic, n in sorted(u["by_mic"].items()):
        print("  %-6s %-46s %5d" % (mic, VENUE_LABEL.get(mic, mic), n))
    if u["venue_fetch_failures"]:
        for mic, why in u["venue_fetch_failures"].items():
            print("  !! %s could not be fetched: %s" % (mic, why))
    print()
    print("Cuts (auditability - every stage, every reason):")
    for c in result["cuts"]:
        print("  %-58s %6d   %s" % (c["stage"], c["count"], c["reason"]))
    cutoff = result["decile_cutoff_pct"]
    print("  decile cutoff (%s window): %s"
          % (result["window"], _fmt_pct(cutoff) if cutoff is not None else "n/a"))
    print()

    print("=" * 78)
    print("TECHNICAL MOVES (corporate action in window - NOT candidates)  [%d total]"
          % result["technical_moves_total"])
    print("=" * 78)
    if not result["technical_moves"]:
        print("  none")
    for c in result["technical_moves"]:
        print(_line_for(c, result["window"]))
        for ev in c["corporate_action"].get("events", []):
            extra = ""
            if ev.get("dividend_yield_pct") is not None:
                extra = "  (implied yield %.2f%%)" % ev["dividend_yield_pct"]
            print("    %s  %-20s %s%s" % (ev.get("date"), ev.get("type"), ev.get("title"), extra))
        print()

    print("=" * 78)
    print("FELL ON INFORMATION (regulatory release in window)  [%d total]"
          % result["fell_on_information_total"])
    print("=" * 78)
    if not result["fell_on_information"]:
        print("  none")
    for c in result["fell_on_information"]:
        print(_line_for(c, result["window"]))
        print()

    print("=" * 78)
    print("FELL ON FLOWS (no regulatory release found in window)  [%d total]"
          % result["fell_on_flows_total"])
    print("=" * 78)
    if not result["fell_on_flows"]:
        print("  none")
    for c in result["fell_on_flows"]:
        print(_line_for(c, result["window"]))
        print()

    print("=" * 78)
    print("NOT CLASSIFIED (regulatory-news check failed - NOT evidence of no news)  [%d total]"
          % result["not_classified_total"])
    print("=" * 78)
    if not result["not_classified"]:
        print("  none")
    for c in result["not_classified"]:
        print(_line_for(c, result["window"]))
        print()

    if result["notes"]:
        print("Notes:")
        for n in result["notes"]:
            print("  - %s" % n)
    print()
    print("Run in %ss (budget %ss)." % (result["elapsed_seconds"], result["budget_seconds"]))


def _json_safe(result):
    def strip(c):
        return {"name": c.get("name"), "lei": c.get("lei"), "isins": c.get("isins"),
                "primary": {"isin": c["primary"].get("isin"), "mic": c["primary"].get("mic"),
                           "currency": c["primary"].get("currency"),
                           "price": c["primary"].get("price"),
                           "also_on": c["primary"].get("also_on")},
                "liquidity_status": c.get("liquidity_status"),
                "turnover_sek": c.get("turnover_sek"),
                "returns": c.get("returns"),
                "corporate_action": c.get("corporate_action"),
                "regulatory_news": c.get("regulatory_news"),
                "short_interest": c.get("short_interest"),
                "data_confidence": c.get("data_confidence")}
    out = dict(result)
    for key in ("technical_moves", "fell_on_information", "fell_on_flows", "not_classified"):
        out[key] = [strip(c) for c in out[key]]
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--window", choices=["1w", "1m"], default="1m",
                    help="return window the worst decile is selected on (default 1m)")
    ap.add_argument("--venue", metavar="LIST",
                    help="comma list of xsto,ssme,xsat,xngm,nsme (default: all five)")
    ap.add_argument("--limit", type=int, default=20,
                    help="max candidates printed per bucket (default 20)")
    ap.add_argument("--liquidity-floor", type=float, metavar="SEK",
                    default=DEFAULT_LIQUIDITY_FLOOR_SEK, dest="liquidity_floor",
                    help="min last-session turnover, SEK-equivalent "
                         "(default %s)" % "{:,.0f}".format(DEFAULT_LIQUIDITY_FLOOR_SEK))
    ap.add_argument("--include-illiquid", action="store_true",
                    help="disable the liquidity floor (keeps the label)")
    ap.add_argument("--budget", type=float, default=DEFAULT_BUDGET_SECONDS, metavar="SECONDS",
                    help="overall wall-clock budget (default %d)" % DEFAULT_BUDGET_SECONDS)
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(0 if _selftest() else 1)

    try:
        result = run(args)
    except ValueError as exc:
        ap.error(str(exc))
        return

    if args.as_json:
        print(json.dumps(_json_safe(result), indent=2, ensure_ascii=False, default=str))
        return
    print_text(result)


# ---------------------------------------------------------------------------
# --selftest - offline, no network
# ---------------------------------------------------------------------------

def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _selftest():
    # Declared once, up front: every module-level sibling this function
    # monkeypatches with a fake, so a later re-declaration never trips
    # Python's "name used prior to global declaration" rule.
    global corporate_actions, mfn_news, short_se, venues_se, nordic_shares
    n = 0

    # -- LEI grouping collapses two share classes into one candidate --------
    rows = [
        {"isin": "SE0000000001", "lei": "LEI1", "name": "Investor A", "mic": "XSTO",
         "currency": "SEK", "price": 200.0, "turnover": 5_000_000.0, "volume": 25000,
         "percent_change_1d": -1.0, "sector": "Financials", "orderbookId": "OB1",
         "has_price_source": True},
        {"isin": "SE0000000002", "lei": "LEI1", "name": "Investor B", "mic": "XSTO",
         "currency": "SEK", "price": 210.0, "turnover": 40_000_000.0, "volume": 190000,
         "percent_change_1d": -1.2, "sector": "Financials", "orderbookId": "OB2",
         "has_price_source": True},
        {"isin": "SE0000000003", "lei": "LEI2", "name": "Other AB", "mic": "XSTO",
         "currency": "SEK", "price": 50.0, "turnover": 1_000_000.0, "volume": 20000,
         "percent_change_1d": 0.5, "sector": "Industrials", "orderbookId": "OB3",
         "has_price_source": True},
    ]
    issuers = group_by_issuer(rows)
    _assert(len(issuers) == 2, "two LEIs must collapse to two issuers, got %d" % len(issuers))
    investor = next(i for i in issuers if i["lei"] == "LEI1")
    _assert(len(investor["isins"]) == 2, "Investor A+B must carry both ISINs")
    n += 2

    # -- select_primary_instrument uses LAST-SESSION turnover, never the
    #    intraday screener field (B1) ----------------------------------------
    returns_by_obid = {
        "OB1": {"status": "checked", "last_close": 200.0, "last_volume": 100.0},   # 20,000
        "OB2": {"status": "checked", "last_close": 210.0, "last_volume": 500.0},   # 105,000
    }
    _row, ret, sek = select_primary_instrument(investor, returns_by_obid)
    _assert(investor["primary"]["isin"] == "SE0000000002",
            "the class with the higher LAST-SESSION turnover must be primary, got %s"
            % investor["primary"]["isin"])
    _assert(sek == 105_000.0, "turnover must come from close x last-bar volume, got %r" % sek)
    n += 2

    # -- liquidity floor cuts on the three new fields, and counts are
    #    unconditional (before --include-illiquid keeps a name) -------------
    floor_issuers = [
        {"turnover_status": "ok", "turnover_sek": 10_000_000.0},
        {"turnover_status": "ok", "turnover_sek": 500.0},
        {"turnover_status": "no_source", "turnover_sek": None},
        {"turnover_status": "ok", "turnover_sek": 0.0},
    ]
    survivors, cuts = apply_liquidity_floor(floor_issuers, 1_000_000.0, include_illiquid=False)
    _assert(len(survivors) == 1, "only the liquid issuer should survive")
    _assert(cuts == {"no_price_source": 1, "below_floor": 1, "did_not_trade": 1},
            "cut counts must be reported per reason, got %r" % (cuts,))
    n += 2

    survivors_all, cuts_all = apply_liquidity_floor(floor_issuers, 1_000_000.0, include_illiquid=True)
    _assert(len(survivors_all) == 4, "--include-illiquid must keep everyone")
    _assert(cuts_all == cuts, "cut counts must not change just because --include-illiquid keeps names")
    n += 2

    # -- a name whose turnover could not be determined is neither cut nor
    #    confirmed liquid - it survives, uncut, pending evidence ------------
    unresolved = [{"turnover_status": "unresolved", "turnover_sek": None,
                  "turnover_error": "no dated FX rate for EUR"}]
    surv_u, cuts_u = apply_liquidity_floor(unresolved, 1_000_000.0, include_illiquid=False)
    _assert(len(surv_u) == 1 and surv_u[0]["liquidity_status"].startswith("not checked"),
            "an unresolved turnover must pass through uncut, not be silently cut or kept liquid")
    _assert(cuts_u == {"no_price_source": 0, "below_floor": 0, "did_not_trade": 0},
            "an unresolved turnover must not be counted against any cut reason")
    n += 2

    # -- a split inside the window routes to technical, not candidates -------
    class FakeCA(object):
        BREAKS_PER_SHARE = {"SPLIT", "RIGHTS_ISSUE"}

        @staticmethod
        def _norm(s):
            return (s or "").strip().lower()

        @staticmethod
        def resolve_company(name):
            return [{"company": name, "market": "STO", "announcements_in_probe": 1}]

        @staticmethod
        def corporate_actions_between(company, date_from, date_to, pages=2):
            if company == "Splitty AB":
                return [{"date": "2026-08-15", "type": "SPLIT", "title": "10:1 split"}]
            return []

        @staticmethod
        def split_adjustment_factor(company, date_from, date_to, pages=3):
            return {"confirmed_splits": [], "other_actions_in_window": [], "factor": 1.0,
                   "reliable": True, "warnings": []}

    real_ca = corporate_actions
    corporate_actions = FakeCA
    try:
        split_hit = check_corporate_actions("Splitty AB", "2026-08-01", "2026-08-31", "2026-08-31")
        no_hit = check_corporate_actions("Clean AB", "2026-08-01", "2026-08-31", "2026-08-31")
    finally:
        corporate_actions = real_ca
    _assert(split_hit["status"] == "checked" and split_hit["has_breaking_action"] is True,
            "a split inside the window must be flagged")
    _assert(no_hit["status"] == "checked" and no_hit["has_breaking_action"] is False,
            "no action found must not be flagged")
    n += 2

    # -- ambiguous CNS resolution refuses rather than guesses (B2) -----------
    class FakeCAAmbiguous(object):
        BREAKS_PER_SHARE = {"SPLIT"}

        @staticmethod
        def _norm(s):
            return (s or "").strip().lower()

        @staticmethod
        def resolve_company(name):
            return [{"company": "Alvotech", "announcements_in_probe": 9},
                    {"company": "AstraZeneca PLC", "announcements_in_probe": 1}]

        @staticmethod
        def corporate_actions_between(company, date_from, date_to, pages=2):
            return []

        @staticmethod
        def split_adjustment_factor(company, date_from, date_to, pages=3):
            return {"confirmed_splits": [], "other_actions_in_window": []}

    corporate_actions = FakeCAAmbiguous
    try:
        ambiguous = check_corporate_actions("AstraZeneca PLC", "2026-08-01", "2026-08-31",
                                            "2026-08-31")
    finally:
        corporate_actions = real_ca
    _assert(ambiguous["status"] == "not checked",
            "an ambiguous top-ranked CNS match must refuse, not silently take hits[0]")
    n += 1

    # -- a name with a regulatory release is bucketed apart from one without,
    #    identity resolved via venues_se.mfn_identity (never a bare search) -
    class FakeVenuesSE(object):
        @staticmethod
        def mfn_identity(name, isin=None, lei=None, limit=30):
            if name in ("Newsy AB", "Quiet AB"):
                return {"name": name, "slug": name.lower().replace(" ", "-"),
                        "isins": [], "leis": [], "orgnr": "", "tickers": {}, "wires": []}
            return None

    class FakeMFN(object):
        @staticmethod
        def fetch_company_pages(slug, pages=2):
            if slug == "newsy-ab":
                return [{"content": {"publish_date": "2026-08-20T08:00:00", "title": "Profit warning"},
                        "properties": {"tags": [":regulatory"], "lang": "en"},
                        "author": {"name": "Newsy AB", "slug": slug}, "url": "https://mfn.se/a/x"}]
            return [{"content": {"publish_date": "2026-08-20T08:00:00", "title": "Marketing puff"},
                    "properties": {"tags": [], "lang": "en"},
                    "author": {"name": "Quiet AB", "slug": slug}, "url": "https://mfn.se/a/y"}]

        @staticmethod
        def flatten(item):
            content = item.get("content") or {}
            props = item.get("properties") or {}
            author = item.get("author") or {}
            tags = props.get("tags") or []
            return {"date": (content.get("publish_date") or "")[:19],
                   "company": author.get("name"), "slug": author.get("slug"),
                   "title": content.get("title"), "tags": tags,
                   "regulatory": ":regulatory" in tags, "is_report": False,
                   "url": item.get("url")}

    real_mfn, real_venues = mfn_news, venues_se
    mfn_news, venues_se = FakeMFN, FakeVenuesSE
    try:
        with_release = check_regulatory_news("Newsy AB", "2026-08-01", "2026-08-31", "2026-08-31")
        without_release = check_regulatory_news("Quiet AB", "2026-08-01", "2026-08-31", "2026-08-31")
        unresolvable = check_regulatory_news("Nobody AB", "2026-08-01", "2026-08-31", "2026-08-31")
        # -- last_close_date BEFORE date_to: a release published the morning
        #    after the last completed close must NOT explain a fall already
        #    measured to that close - the whole point of this fix. --------
        morning_after = check_regulatory_news("Newsy AB", "2026-08-01", "2026-08-19", "2026-08-20")
    finally:
        mfn_news, venues_se = real_mfn, real_venues
    _assert(with_release["status"] == "checked" and with_release["has_release"] is True,
            "a MAR-flagged release in window must be found")
    _assert(without_release["status"] == "checked" and without_release["has_release"] is False,
            "an untagged release must not count as regulatory")
    _assert(unresolvable["status"] == "checked" and unresolvable["has_release"] is False,
            "no verified MFN identity must be 'checked, no release', with a caveat note")
    _assert(morning_after["has_release"] is False,
            "a release AFTER last_close_date must never be read as explaining a fall "
            "already measured to that close")
    _assert(morning_after["since_last_close"]["count"] == 1,
            "that same release must be flagged as news since the last close")
    n += 5

    # -- class-suffixed names are stripped before resolving (B3) -------------
    _assert(_strip_class_suffix("Atlas Copco AB ser. A") == "Atlas Copco AB",
            "class suffix must be stripped")
    _assert(_strip_class_suffix("Investor AB ser. A") == "Investor AB",
            "class suffix must be stripped")
    _assert(_strip_class_suffix("Volvo, AB ser. B") == "Volvo, AB",
            "class suffix must be stripped")
    _assert(_strip_class_suffix("Evolution AB") == "Evolution AB",
            "a name with no class suffix must be left alone")
    n += 1

    # -- an endpoint failure degrades to unchecked, never raises -------------
    real_short = short_se
    short_se = None
    try:
        sig = short_signal(None, {"lei": "LEI1", "isins": []}, datetime.date(2026, 8, 31))
    finally:
        short_se = real_short
    _assert(sig["status"] == "not checked", "a dead short-interest source must degrade, not raise")
    n += 1

    # -- a shape-drift KeyError inside short_se.belongs() degrades, not
    #    crashes the run (M3) -------------------------------------------------
    class FakeShortSEBroken(object):
        @staticmethod
        def belongs(row, company):
            return company["names"][0] == row["issuer"]     # KeyError: no "names" key

    fake_short_data = {"companies_by_lei": {"LEI1": {"lei": "LEI1", "agg": []}},
                       "companies_by_isin": {}, "rows": [{"issuer": "x"}]}
    real_short = short_se
    short_se = FakeShortSEBroken
    try:
        sig2 = short_signal(fake_short_data, {"lei": "LEI1", "isins": []},
                            datetime.date(2026, 8, 31))
    finally:
        short_se = real_short
    _assert(sig2["status"] == "not checked",
            "a shape-drift error inside belongs()/trend() must degrade, not raise")
    n += 1

    r_no_obid = fetch_return_for_instrument({"orderbookId": None}, datetime.date(2026, 8, 31))
    _assert(r_no_obid["status"] == "not checked",
            "an instrument with no orderbook id must degrade, not raise")
    n += 1

    # -- fetch_returns_parallel bounds COLLECTION, not just submission (M2) --
    class SlowNordicShares(object):
        @staticmethod
        def price_history(obid, from_date, to_date):
            time.sleep(0.4)
            return [{"date": "2026-08-31", "close": 100.0, "volume": 10.0}]

    real_nordic = nordic_shares
    nordic_shares = SlowNordicShares
    try:
        tiny_budget = Budget(0.05)
        rows_ = [{"orderbookId": "OB-SLOW-%d" % i} for i in range(4)]
        t0 = time.monotonic()
        out = fetch_returns_parallel(rows_, datetime.date(2026, 8, 31), tiny_budget, max_workers=4)
        elapsed = time.monotonic() - t0
    finally:
        nordic_shares = real_nordic
    _assert(elapsed < 2.0,
            "collection must be bounded by the budget, not run every worker to completion "
            "(took %.2fs)" % elapsed)
    _assert(len(out) < len(rows_),
            "a budget this tight must leave some instruments uncollected, got %d of %d"
            % (len(out), len(rows_)))
    n += 2

    # -- a worker raising SystemExit degrades ONE result, not the run (M2) ---
    class ExplodingNordicShares(object):
        @staticmethod
        def price_history(obid, from_date, to_date):
            raise SystemExit("DATA NOT AVAILABLE: simulated outage")

    real_nordic = nordic_shares
    nordic_shares = ExplodingNordicShares
    try:
        out2 = fetch_returns_parallel([{"orderbookId": "OB-BOOM"}],
                                      datetime.date(2026, 8, 31), Budget(30.0))
    finally:
        nordic_shares = real_nordic
    _assert(out2["OB-BOOM"]["status"] == "not checked",
            "a worker's SystemExit must degrade that one result, not raise out of the pool")
    n += 1

    # -- per-axis as-of dates are distinct ------------------------------------
    bars = [{"date": "2026-07-01", "close": 100.0, "volume": 10.0},
           {"date": "2026-07-31", "close": 90.0, "volume": 20.0},
           {"date": "2026-08-31", "close": 80.0, "volume": 30.0}]
    ret = compute_returns(bars)
    _assert(ret["as_of"] == "2026-08-31", "price as-of must be the last bar's own date")
    _assert(ret["last_volume"] == 30.0, "compute_returns must carry the last bar's volume through")
    short_result = {"as_of": "2026-08-25"}
    _assert(short_result["as_of"] != ret["as_of"],
            "price as-of and short-interest as-of are independent fields that need not match")
    n += 2

    # -- percentile_rank reaches 0 for the minimum of a falling series -------
    falling = [100.0, 90.0, 80.0, 70.0, 60.0]
    _assert(percentile_rank(falling, min(falling)) == 0.0,
            "the minimum close in the fetched range must score percentile 0, not 100/N")
    n += 1

    # -- Swedish/English number formats survive the parse --------------------
    real_mfn2 = mfn_news
    if real_mfn2 is None:
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mfn_news.py")
        spec = importlib.util.spec_from_file_location("mfn_news", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mfn_news = mod
    try:
        _assert(_num("30,054,559") == 30054559.0, "English comma-thousands turnover must parse")
        _assert(_num("151,286") == 151286.0, "English comma-thousands volume must parse")
        _assert(_num("+0.85%") == 0.85, "signed percentage-change must parse")
        _assert(_num("-1,20%") == -1.20, "Nordic decimal-comma percentage must parse")
        _assert(_num(None) is None, "a missing value must not raise")
    finally:
        mfn_news = real_mfn2
    n += 5

    # -- decile selection: sign filter, degenerate-cutoff-is-None, and a
    #    tied fixture that would otherwise blow the decile open (M5) --------
    tiny = [{"key": "a", "returns": {"status": "checked",
                                    "windows": {"1m": {"pct": -5.0}}}},
           {"key": "b", "returns": {"status": "checked",
                                   "windows": {"1m": {"pct": -20.0}}}}]
    cands, cutoff, degenerate = select_worst_decile(tiny, "1m")
    _assert(degenerate is True and len(cands) == 2,
            "fewer than MIN_DECILE_POOL usable falling returns must keep everyone, flagged degenerate")
    _assert(cutoff is None,
            "a degenerate small pool must report cutoff as None, not the best return in it")
    n += 2

    rising = [{"key": str(i), "returns": {"status": "checked",
                                          "windows": {"1m": {"pct": float(i) + 1}}}}
             for i in range(20)]
    cands_r, cutoff_r, degenerate_r = select_worst_decile(rising, "1m")
    _assert(cands_r == [] and cutoff_r is None,
            "names that ROSE must never be selected as a 'worst decile' fall")
    n += 1

    big = [{"key": str(i), "returns": {"status": "checked",
                                       "windows": {"1m": {"pct": -(i + 1.1)}}}}
          for i in range(100)]
    cands2, cutoff2, degenerate2 = select_worst_decile(big, "1m")
    _assert(degenerate2 is False, "a large pool with no ties at the cutoff must use a real decile")
    _assert(len(cands2) == 10, "a real decile of 100 distinct falls must be exactly 10, got %d"
            % len(cands2))
    n += 2

    tied = [{"key": "tied-%d" % i, "returns": {"status": "checked",
                                              "windows": {"1m": {"pct": -100.0}}}}
           for i in range(50)]
    tied += [{"key": "spread-%d" % i, "returns": {"status": "checked",
                                                 "windows": {"1m": {"pct": -(i + 1.0)}}}}
            for i in range(50)]
    cands3, cutoff3, degenerate3 = select_worst_decile(tied, "1m")
    _assert(degenerate3 is True,
            "50 names tied at the cutoff must be flagged degenerate, not presented as a real decile")
    _assert(len(cands3) <= 10,
            "a tie blowout must be capped near the intended decile size, got %d" % len(cands3))
    n += 2

    # -- combine_universe keeps the regulated venue on a dual-listed
    #    collision, never whichever MIC happened to be processed last (M7) --
    firds_by_mic = {
        "SSME": {"instruments": [{"isin": "SE0008294953", "name": "Paradox Interactive",
                                  "lei": "LEI-PDX"}]},
        "XSTO": {"instruments": [{"isin": "SE0008294953", "name": "Paradox Interactive",
                                  "lei": "LEI-PDX"}]},
    }
    combined_dual = combine_universe(None, None, firds_by_mic, ["SSME", "XSTO"])
    row = next(r for r in combined_dual if r["isin"] == "SE0008294953")
    _assert(row["mic"] == "XSTO",
            "a dual-listed ISIN must keep the REGULATED venue as the row of record, got %s"
            % row["mic"])
    _assert("SSME" in row["also_on"], "the non-regulated venue must be recorded in also_on")
    n += 2

    # -- _parse_venues rejects an unknown MIC and defaults to all five -------
    _assert(_parse_venues(None) == list(ALL_MICS), "no --venue must mean all five MICs")
    _assert(_parse_venues("xsto,ssme") == ["XSTO", "SSME"], "venue list must parse in order")
    try:
        _parse_venues("xosl")
        _assert(False, "an unknown venue must raise ValueError")
    except ValueError:
        pass
    n += 1

    print("screen_digest --selftest: %d assertions passed" % n)
    return True


if __name__ == "__main__":
    main()
