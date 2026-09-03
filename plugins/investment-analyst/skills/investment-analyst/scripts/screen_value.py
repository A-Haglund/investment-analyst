#!/usr/bin/env python3
"""screen_value.py - the on-demand "deep value" market screen.

Sibling to screen_digest.py (the unattended daily fell-and-might-be-cheap
screen) but built to the opposite runtime constraints: screen_digest is a
scheduled cloud job with a hard wall-clock budget and a shallow, cheap check
per name (a 130-day price window, no fundamentals). This script runs when a
person asks for it, may take minutes, and goes deeper - a multi-year price
history and an ESEF fundamentals pull, but only for the handful of names
that survive the cheap stages first. Per-name fundamentals cannot be fetched
for 400 issuers; on the 20-80 that make it past drawdown, 12-month return and
liquidity, they are fine. CHEAP AXES FIRST, EXPENSIVE AXES ONLY ON SURVIVORS
is the ordering principle of the whole pipeline below.

THE PIPELINE

  1. UNIVERSE. Shared with screen_digest via market_universe.py:
     fetch_nasdaq_snapshot + fetch_firds + combine_universe + group_by_issuer
     turn ~997 ISIN lines into issuers grouped by LEI (falling back to the
     bare ISIN when FIRDS carries no LEI).

  2. HISTORY. price_history over --history-years (default {history_years}) for
     EVERY priced ISIN line, in parallel, bounded on both submission and
     collection - the same idiom screen_digest.fetch_returns_parallel uses
     (Budget + ThreadPoolExecutor + as_completed(timeout=...) +
     shutdown(wait=False, cancel_futures=True)). This is NOT a literal call to
     fetch_returns_parallel itself: that function hardcodes a 130-day lookback
     (screen_digest.HISTORY_LOOKBACK_DAYS, enough for its own 1w/1m/3m windows)
     with no way to ask for years of bars, and it discards the raw bars it
     fetches - it only ever returns the already-summarised 1w/1m/3m windows.
     A multi-year drawdown-from-high and a 365-day return both need the raw
     bars themselves, so this script fetches them itself
     (fetch_history_parallel / _fetch_bars_for_instrument) and then reuses
     market_universe.compute_returns() on those same bars to get the
     last-completed-session close/volume the liquidity floor needs - see the
     module's REAL SHAPE note in the script's own history below.

     Per issuer, on its tradeable PRIMARY line (select_primary_instrument,
     reused verbatim - the most liquid class by LAST-SESSION turnover, never
     the intraday screener snapshot): screen_metrics.drawdown_from_high(bars)
     and screen_metrics.return_over(bars, 365).

  3. VALUE FILTER (passes_value_filter) - the new entry filter, replacing
     screen_digest's worst-decile-on-1m. Keeps an issuer only if it is down
     at least --drawdown-floor percent from its high AND its 365-day return
     is negative. "Down from the high" alone keeps names that have already
     turned back up; "down over 12m" alone keeps names that fell last month
     and are still expensive. The profile wanted is out of favour AND not
     recovered.

  4. LIQUIDITY FLOOR via market_universe.apply_liquidity_floor, reused
     verbatim - the three distinct cut reasons (no turnover source, did not
     trade, below floor) are preserved.

  5. SIZE BAND (MARKET CAP). Optional - only runs if --cap-floor or --cap-ceiling
     is passed. Cuts on SEK market cap summed across share classes via
     market_universe.attach_market_cap + apply_size_band. Issuers whose share
     count could not be resolved are NOT cut; they pass through marked "not
     checked" and are accounted for separately (they may become candidates,
     technical moves, or not classified later). A --small-cap preset exists
     for micro-cap discovery: sets cap_ceiling=5B, cap_floor=300M,
     gross_floor=25%, op_floor=8% (8% sits just above the 6.10% aggregate
     operating margin of Swedish manufacturers with 20-49 employees per SCB
     FY2024 data - the segment this preset is built to find), and margin_mode
     to "operating". Stage cost is negligible on default (no flags passed -
     stage skips entirely) or expensive on survivors only (cheap axes first).

  6. CORPORATE ACTIONS via market_universe.check_corporate_actions, reused
     verbatim, but over the FULL drawdown window (from the high's own date to
     the last close, not screen_digest's 1-month window). CORRECTED IN
     v3.0.0: nordic_shares' price series is back-adjusted for splits -
     measured, not assumed, from four dated confirmed splits in both
     directions showing zero price discontinuity at the effective date (see
     corporate_actions.py's own "THE ANSWER" section) - so a confirmed split
     alone no longer distorts a 3-year return. A spin-off, a rights issue,
     or years of ordinary dividends (dividends remain unverified either way
     and are treated as unadjusted) can still wear a crash costume over
     that long a window, though. A hit on any of these routes the name to
     the TECHNICAL bucket, never into candidates. A check that could not be
     completed (an ambiguous Nasdaq CNS match, a dead endpoint) is its own
     NOT CLASSIFIED outcome, never folded into "checked, no action found".

  7. MARGINS (expensive - survivors only). Per issuer LEI: fetch_esef_margin_
     inputs pulls the latest ESEF annuals via esef_fundamentals' own pieces
     (list_filings/get_json/extract/pick - there is no single public "give
     me everything for this LEI" function there), builds
     screen_metrics.margins_from_esef, margin_trend and
     passes_margin_floor(--gross-floor default {gross_floor}, --op-floor
     default {op_floor}, --margin-mode for custom rules) against the latest
     fiscal year. MANDATORY LABELLING: every margin printed carries the fiscal
     period it came from and its age in months as of the run date - the latest
     ESEF annuals are routinely well over a year old (FY2024-12-31 was about
     20 months old on 2026-09-02) and a bare percentage with no date on it
     invites reading it as current. An issuer on an MTF (mic not in
     REGULATED_MICS) files no ESEF at all and is reported under NOT CLASSIFIED
     with that reason - never silently dropped, and never counted as a
     margin-floor failure. Likewise an issuer with no LEI, or whose ESEF fetch
     fails outright, is NOT CLASSIFIED, not a margin-floor cut - a check that
     could not be performed is its own outcome. This stage runs ONLY on names
     that survived the value filter, the liquidity floor, the size band
     (if run), and the corporate-action check - fetch_esef_margin_inputs is
     called at most once per such survivor and never for a name cut earlier.

  8. RANK via screen_metrics.rank_candidates (eroding margins demoted, then
     deepest drawdown, then highest operating margin) and cut to --limit.

HARD RULE ON MULTIPLES. This screen never computes or prints a valuation
multiple (P/E, EV/EBIT and the like) at all, on purpose: every one of those
divides a live price by an ESEF figure that is, per the labelling above,
often well over a year old - exactly the mismatch valuation_gate.py exists to
catch and refuse. Rather than reproduce that gate's eight checks here (it
needs FinancialFact objects, live quotes and TTM roll-forwards this script
has no reason to fetch), this script simply never forms the ratio: price and
fiscal period are printed side by side, each labelled with its own date and
age, and the reader draws the multiple only after re-deriving it from the
latest interim report. Any number this script DID present as a multiple
would be labelled "ESTIMATE - screening only, re-derive from the latest
interim report before it reaches a recommendation"; in practice the safer
choice was to print the inputs and never the ratio at all.

AUDITABILITY. Every stage prints how many issuers it cut and why - borrowing
screen_digest's own words, "a screen that silently drops 900 of 997 names
down to a nice round 20 is not a screen, it is a black box." A check that
could not be performed is its own outcome (NOT CLASSIFIED), never folded into
"checked, and found nothing". Every issuer in the universe is accounted for
in exactly one of: survived (a final candidate), cut-with-reason (value
filter, liquidity floor or margin floor), technical (a corporate action
inside the drawdown window), or not classified (a check that could not be
completed at all) - see the --include-illiquid caveat in apply_liquidity_
floor's own docstring for the one flag that can put a name in two of those at
once on purpose.

DEGRADE, NEVER HANG. Every sibling call is guarded exactly as screen_digest
guards its own (`except (Exception, SystemExit)` - these scripts raise
SystemExit as their error convention, which does not inherit from Exception).
A dead endpoint degrades one axis of one candidate, never the whole run. A
missing screen_metrics.py (written by another agent in parallel) degrades
every drawdown/margin computation to "not checked" rather than crashing this
script - see the defensive import below.

Usage:
    python screen_value.py                          # whole market, 3y history
    python screen_value.py --drawdown-floor 40
    python screen_value.py --venue xsto,xngm
    python screen_value.py --limit 10 --json
    python screen_value.py --include-illiquid

Python 3 stdlib only. Free, keyless, everywhere.
"""
import argparse
import datetime
import importlib.util
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


def _load_sibling(name):
    """Import scripts/<name>.py by file path - the scripts folder is not a
    package, and a parallel agent may be mid-edit on a sibling, so every
    caller of this wraps it in try/except (see below), the same convention
    screen_digest.py itself uses for its own sibling imports."""
    path = os.path.join(_HERE, name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# market_universe.py - the shared Swedish-market universe/liquidity layer
# this script reuses most of its plumbing from (EXTRACTED from screen_digest
# in v3.0.0 - see that module's own docstring for the full history: this
# used to be 18 names aliased directly out of screen_digest's globals, with
# a full second reimplementation of each one sitting in the `else:` branch
# below for whenever screen_digest itself was not importable. With the
# logic single-sourced in market_universe.py now, that second copy is gone -
# what remains below is deliberately NOT a re-implementation of the real
# fetch/grouping/liquidity/corporate-action logic, only the same class of
# minimal "not importable" degrade every other genuinely-optional sibling in
# this file already uses (see screen_metrics below). Loaded defensively: a
# broken or missing sibling degrades every axis it feeds to "not checked",
# never crashes this script.
# ---------------------------------------------------------------------------
try:
    market_universe = _load_sibling("market_universe")
except Exception as exc:                                     # pragma: no cover
    market_universe = None
    _MU_IMPORT_ERROR = str(exc)
else:
    _MU_IMPORT_ERROR = None

if market_universe is not None:
    fetch_nasdaq_snapshot = market_universe.fetch_nasdaq_snapshot
    fetch_firds = market_universe.fetch_firds
    combine_universe = market_universe.combine_universe
    group_by_issuer = market_universe.group_by_issuer
    compute_returns = market_universe.compute_returns
    compute_issuer_turnover = market_universe.compute_issuer_turnover
    select_primary_instrument = market_universe.select_primary_instrument
    apply_liquidity_floor = market_universe.apply_liquidity_floor
    attach_market_cap = market_universe.attach_market_cap
    apply_size_band = market_universe.apply_size_band
    check_corporate_actions = market_universe.check_corporate_actions
    data_confidence = market_universe.data_confidence
    Budget = market_universe.Budget
    NASDAQ_THROTTLE = market_universe.NASDAQ_THROTTLE
    REGULATED_MICS = market_universe.REGULATED_MICS
    ALL_MICS = market_universe.ALL_MICS
    NASDAQ_MICS = market_universe.NASDAQ_MICS
    VENUE_LABEL = market_universe.VENUE_LABEL
    DEFAULT_LIQUIDITY_FLOOR_SEK = market_universe.DEFAULT_LIQUIDITY_FLOOR_SEK
    _strip_class_suffix = market_universe._strip_class_suffix
else:                                                         # pragma: no cover
    def fetch_nasdaq_snapshot(market="STO"):
        return None, None, "market_universe.py not importable: %s" % _MU_IMPORT_ERROR

    def fetch_firds(mics):
        return {}, {m: "market_universe.py not importable" for m in mics}

    def combine_universe(*a, **k):
        return []

    def group_by_issuer(rows):
        return []

    def compute_returns(bars):
        return None

    def compute_issuer_turnover(issuer):
        issuer["turnover_status"] = "no_source"
        issuer["turnover_sek"] = None
        issuer["turnover_error"] = "market_universe.py not importable"

    def select_primary_instrument(issuer, returns_by_obid):
        row = issuer["primary"]
        ret = {"status": "not checked", "reason": "market_universe.py not importable"}
        issuer["primary"] = row
        issuer["primary_returns"] = ret
        issuer["primary_turnover_sek"] = None
        return row, ret, None

    def apply_liquidity_floor(issuers, floor, include_illiquid):
        for iss in issuers:
            iss["liquidity_status"] = "not checked - market_universe.py not importable"
        return list(issuers), {"no_price_source": 0, "below_floor": 0, "did_not_trade": 0}

    def attach_market_cap(issuers, budget=None):
        for iss in issuers:
            iss["market_cap_sek"] = None
            iss["market_cap_status"] = "not_checked"
            iss["market_cap_basis"] = "market_universe.py not importable"
            iss["market_cap_error"] = "market_universe.py not importable"

    def apply_size_band(issuers, cap_floor=None, cap_ceiling=None):
        for iss in issuers:
            iss["size_status"] = "not checked - market_universe.py not importable"
        return list(issuers), {"above_ceiling": 0, "below_floor": 0, "no_market_cap": 0}

    def check_corporate_actions(name, date_from, last_close_date, date_to, price=None):
        return {"status": "not checked", "reason": "market_universe.py not importable"}

    def data_confidence(mic):
        return {"mic": mic, "regulated_market": False, "esef_applies": False,
                "label": "unknown - market_universe.py not importable"}

    class _UnavailableBudget(object):
        """Used only when market_universe.py itself could not be imported -
        at that point every fetch stage above is already a no-op stub, so
        there is nothing left to bound. This is NOT a second copy of the
        real Budget's monotonic-clock logic (see market_universe.py for
        that): it exists purely so `Budget(args.budget)` in run() does not
        raise NameError."""
        def __init__(self, seconds):
            self.seconds = seconds

        def exceeded(self):
            return False

        def elapsed(self):
            return 0.0

        def remaining(self):
            return None

    Budget = _UnavailableBudget

    class _NoThrottle(object):
        def wait(self):
            pass

    NASDAQ_THROTTLE = _NoThrottle()
    # Honestly empty rather than a hardcoded second copy of the real venue
    # tables (a divergence risk with no benefit here: every fetch above is
    # already a no-op, so an empty venue list just means an empty universe
    # instead of a universe of venues this run cannot actually see).
    REGULATED_MICS = set()
    ALL_MICS = ()
    NASDAQ_MICS = ()
    VENUE_LABEL = {}
    DEFAULT_LIQUIDITY_FLOOR_SEK = 2_000_000.0

    def _strip_class_suffix(name):
        return (name or "").strip()


# ---------------------------------------------------------------------------
# screen_metrics.py - written in parallel by another agent, may not exist
# yet or may not import cleanly. Every function this script needs from it is
# bound defensively to None so a missing/broken sibling degrades every
# drawdown/margin computation to "not checked", never crashes this script.
# ---------------------------------------------------------------------------
try:
    screen_metrics = _load_sibling("screen_metrics")
except Exception:
    screen_metrics = None

if screen_metrics is not None:
    drawdown_from_high = getattr(screen_metrics, "drawdown_from_high", None)
    return_over = getattr(screen_metrics, "return_over", None)
    margins_from_esef = getattr(screen_metrics, "margins_from_esef", None)
    margin_trend = getattr(screen_metrics, "margin_trend", None)
    passes_margin_floor = getattr(screen_metrics, "passes_margin_floor", None)
    rank_candidates = getattr(screen_metrics, "rank_candidates", None)
else:
    drawdown_from_high = None
    return_over = None
    margins_from_esef = None
    margin_trend = None
    passes_margin_floor = None
    rank_candidates = None


# ---------------------------------------------------------------------------
# esef_fundamentals.py - loaded whole (not individual names rebound), since
# only fetch_esef_margin_inputs below ever touches it.
# ---------------------------------------------------------------------------
try:
    esef_fundamentals = _load_sibling("esef_fundamentals")
except Exception:
    esef_fundamentals = None

try:
    nordic_shares = _load_sibling("nordic_shares")
except Exception:
    nordic_shares = None


DEFAULT_DRAWDOWN_FLOOR = 30.0
DEFAULT_GROSS_FLOOR = 40.0
DEFAULT_OP_FLOOR = 15.0
DEFAULT_HISTORY_YEARS = 3.0
DEFAULT_LIMIT = 20
DEFAULT_BUDGET_SECONDS = 900.0          # on-demand and deep - minutes, not seconds
DEFAULT_ESEF_FILINGS = 3                # each filing carries ~2 years - enough for a trend
MARGIN_METRICS = ("revenue", "cost_of_sales", "gross_profit", "operating_income")

# Small-cap preset: a convenience band for micro-cap discovery. Built to find the
# segment screen_digest's own 2y backtest flagged as systematically mispriced. The
# 8.0% op_floor sits just above SCB's Företagens ekonomi (FY2024) measured aggregate
# operating margin of 6.10% for Swedish manufacturers with 20-49 employees - the
# segment this preset exists to find. Standard 15.0% floor would reject the whole
# segment as under-margined. Empirically: the floor's 2.5x overshoot of the segment
# norm used to suppress dozens of candidate names that later outperformed.
SMALL_CAP_CEILING = 5_000_000_000
SMALL_CAP_FLOOR = 300_000_000
SMALL_CAP_GROSS_FLOOR = 25.0
SMALL_CAP_OP_FLOOR = 8.0
SMALL_CAP_MARGIN_MODE = "operating"

__doc__ = (__doc__.replace("{history_years}", str(int(DEFAULT_HISTORY_YEARS)))
                 .replace("{gross_floor}", "%.0f" % DEFAULT_GROSS_FLOOR)
                 .replace("{op_floor}", "%.0f" % DEFAULT_OP_FLOOR))


def today():
    """A seam for tests: patched to a fixed date so as-of/age comparisons
    are deterministic instead of depending on the day the suite runs."""
    return datetime.date.today()


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


# ---------------------------------------------------------------------------
# stage 2: history (own N-year fetch - see the module docstring for why this
# is not a literal call to screen_digest.fetch_returns_parallel)
# ---------------------------------------------------------------------------

def _fetch_bars_for_instrument(row, from_date, to_date):
    obid = row.get("orderbookId")
    if not obid or nordic_shares is None:
        return {"status": "not checked", "bars": None,
                "reason": "no Nasdaq orderbook id - no free price-history source "
                          "exists in this toolkit for this venue"}
    NASDAQ_THROTTLE.wait()
    try:
        bars = nordic_shares.price_history(obid, from_date, to_date)
    except (Exception, SystemExit) as exc:
        return {"status": "not checked", "bars": None, "reason": str(exc)}
    if not bars:
        return {"status": "not checked", "bars": None,
                "reason": "no usable price bars returned"}
    return {"status": "checked", "bars": bars, "reason": None}


def fetch_history_parallel(instruments, from_date, to_date, budget, max_workers=10):
    """N-year price_history for every priced instrument, in parallel, bounded
    on BOTH submission and collection - the same idiom as screen_digest.
    fetch_returns_parallel (see that function's own docstring for why
    `as_completed(..., timeout=...)` is what actually bounds collection, and
    why worker errors are caught with `except BaseException`, not `except
    Exception` - a worker raising SystemExit must not kill the whole run).
    Returns {orderbookId: {"status", "bars", "reason"}}.
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
            futures[pool.submit(_fetch_bars_for_instrument, row, from_date, to_date)] = obid
        try:
            for fut in as_completed(futures, timeout=budget.remaining()):
                obid = futures[fut]
                try:
                    results[obid] = fut.result()
                except BaseException as exc:          # pragma: no cover - defensive
                    results[obid] = {"status": "not checked", "bars": None,
                                     "reason": "worker error: %s" % exc}
        except TimeoutError:
            pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return results


def _build_returns_by_obid(bars_by_obid):
    """compute_returns()-shaped summaries from the raw bars this script
    fetched itself - select_primary_instrument and compute_issuer_turnover
    (both reused verbatim from market_universe) expect exactly this shape."""
    out = {}
    for obid, info in bars_by_obid.items():
        if info.get("status") != "checked":
            out[obid] = {"status": "not checked", "reason": info.get("reason")}
            continue
        ret = compute_returns(info.get("bars"))
        if ret is None:
            out[obid] = {"status": "not checked", "reason": "no usable price bars returned"}
            continue
        ret["status"] = "checked"
        out[obid] = ret
    return out


# ---------------------------------------------------------------------------
# stage 3: value filter
# ---------------------------------------------------------------------------

def passes_value_filter(drawdown, return_365, drawdown_floor):
    """Keep an issuer only if it is down at least `drawdown_floor` percent
    from its high AND its 365-day return is negative.

    "Down from the high" alone keeps names that have already turned back up;
    "down over 12m" alone keeps names that fell last month and are still
    expensive. The profile wanted is out of favour AND not recovered - see
    the module docstring's stage 3.

    Returns (bool, reason_or_None).
    """
    if not drawdown or drawdown.get("drawdown_pct") is None:
        return False, "no usable drawdown data"
    if return_365 is None:
        return False, "insufficient 12-month price history for the 365-day return"
    dd = drawdown["drawdown_pct"]
    floor = -abs(drawdown_floor)
    if dd > floor:
        return False, ("drawdown %.1f%% is above the %.0f%% floor - not down enough "
                       "from the high" % (dd, floor))
    if return_365 >= 0:
        return False, ("365-day return is %+.1f%% - not negative, so this name has "
                       "already recovered or did not fall over the last 12 months"
                       % return_365)
    return True, None


# ---------------------------------------------------------------------------
# stage 5: corporate actions, over the full drawdown window
# ---------------------------------------------------------------------------

def evaluate_corporate_actions(issuer, drawdown):
    raw_name = issuer["primary"].get("name") or issuer.get("name")
    name = _strip_class_suffix(raw_name)
    date_from = drawdown["high_date"]
    last_close_date = drawdown["last_date"]
    price = issuer["primary"].get("price")
    return check_corporate_actions(name, date_from, last_close_date, last_close_date,
                                   price=price)


# ---------------------------------------------------------------------------
# stage 6: margins - survivors only
# ---------------------------------------------------------------------------

def _month_diff(period_end, as_of):
    """Whole months between a fiscal period end and the run date - the
    MANDATORY LABELLING the module docstring requires next to every margin.
    Verified against the brief's own worked example: FY2024-12-31 as of
    2026-09-02 is 20 whole months old, not 21 (the run date's day-of-month,
    2, has not yet reached the period end's day-of-month, 31)."""
    d = datetime.date.fromisoformat(period_end)
    months = (as_of.year - d.year) * 12 + (as_of.month - d.month)
    if as_of.day < d.day:
        months -= 1
    return max(months, 0)


def fetch_esef_margin_inputs(lei, filings=DEFAULT_ESEF_FILINGS):
    """Thin wrapper over esef_fundamentals' pieces - there is no single
    public "give me everything for this LEI" function there, so this
    assembles list_filings + get_json + extract + pick itself, narrowed to
    just the four inputs screen_metrics.margins_from_esef needs, mirroring
    esef_fundamentals.main()'s own merge convention: newest filing processed
    first, first value seen per period wins (the most recently restated
    figure). Returns (margins_by_period, latest_period_end, error_or_None).
    """
    if esef_fundamentals is None:
        return None, None, "esef_fundamentals.py not importable"
    try:
        filing_list = esef_fundamentals.list_filings(lei, filings)
    except (Exception, SystemExit) as exc:
        return None, None, str(exc)
    if not filing_list:
        return None, None, "no ESEF filings indexed for this LEI"

    merged = {}
    fetch_errors = []
    for f in filing_list:
        try:
            doc = esef_fundamentals.get_json(esef_fundamentals.FILINGS_BASE + f["json_url"])
        except (Exception, SystemExit) as exc:
            fetch_errors.append(str(exc))
            continue
        try:
            facts = esef_fundamentals.extract(doc)
        except (Exception, SystemExit) as exc:       # pragma: no cover - defensive
            fetch_errors.append(str(exc))
            continue
        for metric in MARGIN_METRICS:
            names = esef_fundamentals.CONCEPTS[metric]
            found = esef_fundamentals.pick(facts, names, True)
            for period, (value, _unit, _concept) in found.items():
                merged.setdefault(period, {}).setdefault(metric, value)

    if not merged:
        reason = ("; ".join(fetch_errors) if fetch_errors else
                  "filings found but no revenue/margin concepts matched - the issuer "
                  "likely uses extension taxonomy tags")
        return None, None, reason
    latest_period = max(merged)
    return merged, latest_period, None


def evaluate_margins(issuer, as_of, gross_floor, op_floor, margin_mode="either", filings=DEFAULT_ESEF_FILINGS):
    """Runs ONLY on value-filter + liquidity-floor + corporate-action
    survivors - see the module docstring's cost-control note. Returns a dict
    with `outcome` in {"pass", "cut", "not_classified"}.
    """
    mic = issuer["primary"].get("mic")
    if mic not in REGULATED_MICS:
        return {"outcome": "not_classified",
                "reason": "%s is not a regulated market (ESEF applies only to "
                          "XSTO/XNGM) - this issuer files no ESEF annual report "
                          "at all" % (mic or "unknown venue")}
    lei = issuer.get("lei")
    if not lei:
        return {"outcome": "not_classified",
                "reason": "no LEI resolved for this issuer - cannot look up ESEF filings"}
    if margins_from_esef is None or margin_trend is None or passes_margin_floor is None:
        return {"outcome": "not_classified",
                "reason": "screen_metrics.py not importable - margin calculation unavailable"}

    margins_by_period, latest_period, err = fetch_esef_margin_inputs(lei, filings)
    if err:
        return {"outcome": "not_classified", "reason": "ESEF: %s" % err}

    computed = margins_from_esef(margins_by_period)
    trend = margin_trend(computed)
    latest = computed.get(latest_period) or {}
    ok, reason = passes_margin_floor(latest, gross_floor=gross_floor, op_floor=op_floor, mode=margin_mode)
    age_months = _month_diff(latest_period, as_of)
    info = {"fiscal_period_end": latest_period, "age_months": age_months,
            "gross_margin_pct": latest.get("gross_margin_pct"),
            "operating_margin_pct": latest.get("operating_margin_pct"),
            "margin_trend": trend}
    if not ok:
        return {"outcome": "cut", "reason": "margin_floor: %s" % reason, "margins": info}
    return {"outcome": "pass", "margins": info}


# ---------------------------------------------------------------------------
# per-issuer public summaries (JSON/text friendly - no raw bars/instruments)
# ---------------------------------------------------------------------------

def _public_issuer(iss, extra=None):
    out = {"key": iss.get("key"), "name": iss.get("name"), "lei": iss.get("lei"),
          "isins": iss.get("isins"),
          "mic": iss["primary"].get("mic") if iss.get("primary") else None,
          "venue_label": VENUE_LABEL.get(iss["primary"].get("mic")) if iss.get("primary") else None,
          "currency": iss["primary"].get("currency") if iss.get("primary") else None,
          "price": iss["primary"].get("price") if iss.get("primary") else None,
          "turnover_sek": iss.get("turnover_sek"),
          "liquidity_status": iss.get("liquidity_status"),
          # The size axis has to be visible in the output it filtered on: a
          # screen that cuts an issuer for its market cap and then never prints
          # that market cap is unauditable. market_cap_status carries whether
          # the figure is complete, partial (a floor) or was never computed.
          "market_cap_sek": iss.get("market_cap_sek"),
          "market_cap_status": iss.get("market_cap_status"),
          "market_cap_basis": iss.get("market_cap_basis"),
          "size_status": iss.get("size_status"),
          "drawdown": iss.get("drawdown"),
          "return_365_pct": iss.get("return_365")}
    if extra:
        out.update(extra)
    return out


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def run(args):
    budget = Budget(args.budget)
    notes = []
    if screen_metrics is None:
        notes.append("screen_metrics.py not importable - drawdown, 12-month return "
                     "and margin calculations are degraded to 'not checked' for the "
                     "whole run")
    as_of = today()

    mics = _parse_venues(args.venue)
    nasdaq_mics = [m for m in mics if m in NASDAQ_MICS]

    nasdaq_rows, nasdaq_liq, nasdaq_err = (None, None, None)
    if nasdaq_mics:
        nasdaq_rows, nasdaq_liq, nasdaq_err = fetch_nasdaq_snapshot("STO")
        if nasdaq_err:
            notes.append("Nasdaq universe/liquidity: %s" % nasdaq_err)

    firds_by_mic, firds_failed = fetch_firds(mics)
    for mic, why in firds_failed.items():
        notes.append("%s (%s) universe: %s" % (mic, VENUE_LABEL.get(mic, mic), why))

    combined = combine_universe(nasdaq_rows, nasdaq_liq, firds_by_mic, mics)
    issuers = group_by_issuer(combined)
    total_issuers = len(issuers)

    by_mic_counts = {}
    for r in combined:
        by_mic_counts[r["mic"]] = by_mic_counts.get(r["mic"], 0) + 1
    universe_summary = {"isin_lines": len(combined), "issuers": total_issuers,
                        "by_mic": by_mic_counts, "venues_included": mics,
                        "venue_fetch_failures": firds_failed}

    # --- stage 2: history --------------------------------------------------
    history_days = int(round(args.history_years * 365.25)) + 30
    from_date = (as_of - datetime.timedelta(days=history_days)).isoformat()
    to_date = as_of.isoformat()
    all_instruments = [r for iss in issuers for r in iss["instruments"]
                       if r.get("orderbookId")]
    bars_by_obid = fetch_history_parallel(all_instruments, from_date, to_date, budget)
    returns_by_obid = _build_returns_by_obid(bars_by_obid)

    for iss in issuers:
        select_primary_instrument(iss, returns_by_obid)
        compute_issuer_turnover(iss)
        primary_obid = iss["primary"].get("orderbookId")
        bar_info = bars_by_obid.get(primary_obid) if primary_obid else None
        bars = bar_info.get("bars") if bar_info and bar_info.get("status") == "checked" else None
        iss["bars"] = bars
        iss["drawdown"] = drawdown_from_high(bars) if (drawdown_from_high and bars) else None
        iss["return_365"] = return_over(bars, 365) if (return_over and bars) else None

    # --- stage 3: value filter ----------------------------------------------
    value_survivors = []
    value_cut_no_history = 0
    value_cut_criteria = 0
    for iss in issuers:
        if iss["bars"] is None:
            iss["disposition"] = "cut:no_price_history"
            iss["cut_reason"] = "no usable multi-year price history for this venue"
            value_cut_no_history += 1
            continue
        ok, reason = passes_value_filter(iss["drawdown"], iss["return_365"], args.drawdown_floor)
        if not ok:
            iss["disposition"] = "cut:value_filter"
            iss["cut_reason"] = reason
            value_cut_criteria += 1
            continue
        value_survivors.append(iss)

    # --- stage 4: liquidity floor --------------------------------------------
    survivors, liquidity_cuts = apply_liquidity_floor(
        value_survivors, args.liquidity_floor, args.include_illiquid)
    survivor_keys = {iss["key"] for iss in survivors}
    for iss in value_survivors:
        if iss["key"] not in survivor_keys:
            iss["disposition"] = "cut:liquidity_floor"
            iss["cut_reason"] = iss.get("liquidity_status")

    # --- stage 4.5: size band (market cap) -----------------------------------
    size_cuts = {"above_ceiling": 0, "below_floor": 0, "no_market_cap": 0}
    if args.cap_floor is not None or args.cap_ceiling is not None:
        # Expensive check on survivors only - skip the whole stage if both
        # cap_floor and cap_ceiling are None to preserve default run cost
        if market_universe is not None:
            attach_market_cap(survivors, budget=budget)
            pre_size_band_keys = {iss["key"] for iss in survivors}
            survivors, size_cuts = apply_size_band(
                survivors, cap_floor=args.cap_floor, cap_ceiling=args.cap_ceiling)
            post_size_band_keys = {iss["key"] for iss in survivors}
            # Mark issuers cut by size band (not in post-size-band survivors)
            for iss_key in pre_size_band_keys:
                if iss_key not in post_size_band_keys:
                    # Find the issuer in all_issuers to mark it
                    for iss in issuers:
                        if iss["key"] == iss_key:
                            iss["disposition"] = "cut:size_band"
                            iss["cut_reason"] = iss.get("size_status")
                            break

    # --- stage 5: corporate actions -----------------------------------------
    technical = []
    not_classified = []
    corp_clean = []
    for iss in survivors:
        result = evaluate_corporate_actions(iss, iss["drawdown"])
        iss["corporate_action"] = result
        iss["data_confidence"] = data_confidence(iss["primary"]["mic"])
        if result.get("status") != "checked":
            iss["disposition"] = "not_classified:corporate_action"
            iss["cut_reason"] = "corporate-action check could not be completed: %s" % (
                result.get("reason") or "unknown")
            not_classified.append(iss)
            continue
        if result.get("has_breaking_action"):
            iss["disposition"] = "technical"
            iss["cut_reason"] = "corporate action inside the drawdown window may make " \
                                "the fall a technical artefact rather than a real one"
            technical.append(iss)
            continue
        corp_clean.append(iss)

    # --- stage 6: margins - corp_clean survivors only ------------------------
    candidates = []
    margin_cut_count = 0
    for iss in corp_clean:
        m = evaluate_margins(iss, as_of, args.gross_floor, args.op_floor, args.margin_mode)
        if m["outcome"] == "not_classified":
            iss["disposition"] = "not_classified:margins"
            iss["cut_reason"] = m["reason"]
            not_classified.append(iss)
            continue
        if m["outcome"] == "cut":
            iss["disposition"] = "cut:margin_floor"
            iss["cut_reason"] = m["reason"]
            iss["margins"] = m.get("margins")
            margin_cut_count += 1
            continue
        iss["disposition"] = "survived"
        iss["margins"] = m["margins"]
        iss["drawdown_pct"] = iss["drawdown"]["drawdown_pct"]
        iss["operating_margin_pct"] = m["margins"]["operating_margin_pct"]
        iss["margin_trend"] = m["margins"]["margin_trend"]
        candidates.append(iss)

    # --- stage 7: rank --------------------------------------------------------
    if rank_candidates is not None:
        ranked = rank_candidates(candidates)
    else:
        ranked = candidates
        notes.append("screen_metrics.rank_candidates not available - candidates are "
                     "in survivor order, not ranked")
    final = ranked[:args.limit]

    liquidity_cut_total = sum(liquidity_cuts.values())
    size_cut_total = size_cuts["above_ceiling"] + size_cuts["below_floor"]
    accounted = (value_cut_no_history + value_cut_criteria + liquidity_cut_total
                + size_cut_total + len(technical) + len(not_classified)
                + margin_cut_count + len(candidates))

    cuts = [
        {"stage": "universe", "count": total_issuers,
         "reason": "distinct issuers after LEI grouping, across %s" % ", ".join(mics)},
        {"stage": "value_filter: no price history", "count": value_cut_no_history,
         "reason": "no free multi-year price-history source for this venue's tradeable line"},
        {"stage": "value_filter: does not meet criteria", "count": value_cut_criteria,
         "reason": "not both >= %.0f%% down from the high AND negative over 365 days"
                   % args.drawdown_floor},
        {"stage": "liquidity_floor: no turnover source", "count": liquidity_cuts["no_price_source"],
         "reason": "no free bulk turnover feed for this venue in this toolkit"},
        {"stage": "liquidity_floor: did not trade", "count": liquidity_cuts["did_not_trade"],
         "reason": "a feed exists, but the last completed session shows zero volume"},
        {"stage": "liquidity_floor: below floor", "count": liquidity_cuts["below_floor"],
         "reason": "last-session turnover (SEK-equiv) below %s"
                   % "{:,.0f}".format(args.liquidity_floor)},
    ]

    # Add size band cuts only if the stage ran
    if args.cap_floor is not None or args.cap_ceiling is not None:
        cuts.extend([
            {"stage": "size_band: above ceiling", "count": size_cuts["above_ceiling"],
             "reason": "market cap > %s SEK" % "{:,.0f}".format(args.cap_ceiling)
                       if args.cap_ceiling is not None else "no ceiling set"},
            {"stage": "size_band: below floor", "count": size_cuts["below_floor"],
             "reason": "market cap < %s SEK" % "{:,.0f}".format(args.cap_floor)
                       if args.cap_floor is not None else "no floor set"},
            {"stage": "size_band: no market cap", "count": size_cuts["no_market_cap"],
             "reason": "share count could not be resolved - issuer continued unscreened on size "
                       "(NOT a cut, check whether it becomes a candidate, technical move, or "
                       "not classified)"},
        ])

    cuts.extend([
        {"stage": "technical moves (corporate action in drawdown window)", "count": len(technical),
         "reason": "split/rights issue/spin-off/dividend/etc in window may make the "
                   "drawdown a technical artefact rather than a real fall"},
        {"stage": "not classified", "count": len(not_classified),
         "reason": "a corporate-action or ESEF check could not be completed at all - "
                   "NOT evidence the check passed"},
        {"stage": "margin_floor: below floor", "count": margin_cut_count,
         "reason": "gross or operating margin below the %.0f%%/%.0f%% floor"
                   % (args.gross_floor, args.op_floor)},
        {"stage": "candidates", "count": len(candidates),
         "reason": "survived every stage - the deep-value shortlist"},
    ])

    return {
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "as_of": as_of.isoformat(),
        "drawdown_floor": args.drawdown_floor, "gross_floor": args.gross_floor,
        "op_floor": args.op_floor, "history_years": args.history_years,
        "liquidity_floor": args.liquidity_floor, "include_illiquid": args.include_illiquid,
        "cap_floor": args.cap_floor, "cap_ceiling": args.cap_ceiling,
        "margin_mode": args.margin_mode, "small_cap": args.small_cap,
        "universe": universe_summary,
        "cuts": cuts,
        "universe_accounted_for": accounted,
        "universe_total": total_issuers,
        "accounting_ok": accounted == total_issuers,
        "technical": [_public_issuer(c, {"cut_reason": c.get("cut_reason")})
                     for c in technical[:args.limit]],
        "technical_total": len(technical),
        "not_classified": [_public_issuer(c, {"cut_reason": c.get("cut_reason")})
                          for c in not_classified[:args.limit]],
        "not_classified_total": len(not_classified),
        "candidates": [_public_issuer(c, {"margins": c.get("margins"),
                                          "margin_trend": c.get("margin_trend"),
                                          "corporate_action": c.get("corporate_action"),
                                          "data_confidence": c.get("data_confidence")})
                      for c in final],
        "candidates_total": len(candidates),
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


def _fmt_margin(m):
    if not m:
        return "margins: not classified"
    return ("gross margin %s / operating margin %s (FY%s, %d months old, trend: %s)"
           % (_fmt_pct(m.get("gross_margin_pct")), _fmt_pct(m.get("operating_margin_pct")),
              m.get("fiscal_period_end"), m.get("age_months", -1), m.get("margin_trend")))


def print_text(result):
    print("DEEP VALUE SCREEN - as of %s" % result["as_of"])
    print("Drawdown floor -%.0f%%, gross/operating margin floor %.0f%%/%.0f%%, "
         "%.0f-year history, liquidity floor %s SEK-equiv"
         % (result["drawdown_floor"], result["gross_floor"], result["op_floor"],
            result["history_years"], "{:,.0f}".format(result["liquidity_floor"])))
    if result.get("partial"):
        print("*** PARTIAL RUN - time budget exceeded before every stage finished ***")
    print()
    print("Universe: %d issuers (%d ISIN lines) across %s"
         % (result["universe"]["issuers"], result["universe"]["isin_lines"],
            ", ".join(result["universe"]["venues_included"])))
    print()
    print("Stage-by-stage cuts:")
    for c in result["cuts"]:
        print("  %-55s %5d   %s" % (c["stage"], c["count"], c["reason"]))
    if not result.get("accounting_ok"):
        print("  *** ACCOUNTING MISMATCH: %d of %d issuers unaccounted for ***"
             % (result["universe_total"] - result["universe_accounted_for"],
                result["universe_total"]))
    print()

    print("CANDIDATES (%d total, showing %d):" % (result["candidates_total"], len(result["candidates"])))
    for c in result["candidates"]:
        dd = c.get("drawdown") or {}
        print("  %-40.40s %-6s down %s from high (%s), 365d %s"
             % (c.get("name") or "?", c.get("mic") or "?",
                _fmt_pct(dd.get("drawdown_pct")), dd.get("high_date"),
                _fmt_pct(c.get("return_365_pct"))))
        print("      %s" % _fmt_margin(c.get("margins")))
        mc, mc_status = c.get("market_cap_sek"), c.get("market_cap_status")
        if mc is not None:
            print("      market cap %s SEK (%s)"
                 % ("{:,.0f}".format(mc),
                    "listed classes, a floor" if mc_status == "partial"
                    else "listed classes summed per class"))
        elif mc_status:
            print("      market cap %s" % (c.get("size_status") or mc_status))
        print("      %s" % (c.get("data_confidence") or {}).get("label", ""))
    print()

    if result["technical_total"]:
        print("TECHNICAL MOVES (%d, corporate action inside the drawdown window):"
             % result["technical_total"])
        for c in result["technical"]:
            print("  %-40.40s %s" % (c.get("name") or "?", c.get("cut_reason") or ""))
        print()

    if result["not_classified_total"]:
        print("NOT CLASSIFIED (%d, a check could not be completed):"
             % result["not_classified_total"])
        for c in result["not_classified"]:
            print("  %-40.40s %s" % (c.get("name") or "?", c.get("cut_reason") or ""))
        print()

    if result["notes"]:
        print("Notes:")
        for n in result["notes"]:
            print("  - %s" % n)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drawdown-floor", type=float, default=DEFAULT_DRAWDOWN_FLOOR,
                    dest="drawdown_floor",
                    help="minimum %% down from the multi-year high (default: %(default)s)")
    # default=None is a SENTINEL, not "no default": it is the only reliable way
    # to tell "user passed 40" from "argparse filled in 40", which --small-cap
    # needs in order to yield to an explicit flag. Inspecting sys.argv instead
    # misses --gross-floor=40 and argparse's accepted abbreviations. The real
    # defaults are filled in below, after the preset has had its say.
    ap.add_argument("--gross-floor", type=float, default=None, dest="gross_floor",
                    help="minimum %% gross margin (default: {})".format(DEFAULT_GROSS_FLOOR))
    ap.add_argument("--op-floor", type=float, default=None, dest="op_floor",
                    help="minimum %% operating margin (default: {})".format(DEFAULT_OP_FLOOR))
    ap.add_argument("--history-years", type=float, default=DEFAULT_HISTORY_YEARS,
                    dest="history_years")
    ap.add_argument("--liquidity-floor", type=float, default=DEFAULT_LIQUIDITY_FLOOR_SEK,
                    dest="liquidity_floor")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--budget", type=float, default=DEFAULT_BUDGET_SECONDS)
    ap.add_argument("--venue", default=None, help="comma list, e.g. xsto,xngm")
    ap.add_argument("--include-illiquid", action="store_true", dest="include_illiquid")
    ap.add_argument("--cap-floor", type=float, default=None, dest="cap_floor")
    ap.add_argument("--cap-ceiling", type=float, default=None, dest="cap_ceiling")
    ap.add_argument("--margin-mode", choices=["either", "both", "operating"],
                    default=None, dest="margin_mode",
                    help="which margin floors must clear (default: either)")
    ap.add_argument("--small-cap", action="store_true", dest="small_cap")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    # --small-cap yields to any flag the user set explicitly. Every option the
    # preset touches parses with default=None, so "still None" means "not given"
    # - no sys.argv inspection, and immune to the =value form and abbreviations.
    if args.small_cap:
        if args.cap_ceiling is None:
            args.cap_ceiling = SMALL_CAP_CEILING
        if args.cap_floor is None:
            args.cap_floor = SMALL_CAP_FLOOR
        if args.gross_floor is None:
            args.gross_floor = SMALL_CAP_GROSS_FLOOR
        if args.op_floor is None:
            args.op_floor = SMALL_CAP_OP_FLOOR
        if args.margin_mode is None:
            args.margin_mode = SMALL_CAP_MARGIN_MODE

    # Fill the ordinary defaults for anything neither the user nor the preset set.
    if args.gross_floor is None:
        args.gross_floor = DEFAULT_GROSS_FLOOR
    if args.op_floor is None:
        args.op_floor = DEFAULT_OP_FLOOR
    if args.margin_mode is None:
        args.margin_mode = "either"

    result = run(args)
    if args.as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        print_text(result)


if __name__ == "__main__":
    main()
