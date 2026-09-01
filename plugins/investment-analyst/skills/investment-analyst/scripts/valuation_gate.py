#!/usr/bin/env python3
"""The hard gate: refuse to print a multiple when price and earnings do not
belong to the same moment.

Verified 2026-08-31: Sandvik's latest structured earnings (ESEF, FY2024-12-31)
are twenty months old while its price is live. Nothing upstream currently
checks that before dividing one by the other. This module is that check.

spec Sec7 requires eight things to be true before P/E, EV/EBIT, EV/EBITDA,
EV/Sales, P/FCF, FCF yield or dividend yield may be shown as a precise number:

    1. price timestamp - present and fresh
    2. financial period  - temporally compatible with the price date
    3. publication date  - present on the financial fact
    4. share count       - semantic type known and certain
    5. currency          - price currency == earnings currency (or converted)
    6. corporate actions - nothing share-count-changing in the gap
    7. TTM completeness  - a "TTM" figure is actually four contiguous quarters
    8. restatement       - the figure has not been silently superseded

On failure this prints the state and the reason, never a number:

    VALUATION INTEGRITY: FAILED
    Reason: TTM EBIT is based on financial data ending 2024-12-31,
            608 days before the price date. Roll forward with the
            interim report before using this multiple.

A PASS may still carry warnings - a check that could not be fully verified is
not the same as a check that passed clean, and both are reported.

REUSABLE API (no network, no argparse - safe to import from anywhere):

    from valuation_gate import gate, gate_detail
    passed, states, report = gate(price_fact, earnings_fact, shares_fact,
                                  as_of="2026-08-31", metric_name="EV/EBIT")

`gate_detail(...)` returns `(passed, states, report, results)` where `results`
is the full per-check list, for callers that want more than the headline.

Every argument is a finfact.FinancialFact (or None, meaning "not supplied" -
itself a fail on any check that needed it). Nothing here accepts a bare float.

CLI (network, live data - this is what the eight checks are tested against):

    python valuation_gate.py "Sandvik"          # FAILS - the motivating case
    python valuation_gate.py "Evolution"        # FAILS - SEK price, EUR books
    python valuation_gate.py "AB Volvo"         # dual share class
    python valuation_gate.py "KebNi"            # First North, no ESEF
    python valuation_gate.py NVDA               # PASSES - current SEC data
    python valuation_gate.py "Sandvik" --json --explain

Nordic identity/currency comes from company_resolve.py; earnings from
esef_fundamentals.py's own extraction helpers (Nordic) or sec_fundamentals.py
(US); shares from Nasdaq Nordic reference data (Nordic) or SEC XBRL diluted
weighted-average (US); corporate actions from corporate_actions.py --shares'
underlying share_history(); price from quote.py's Yahoo source. ttm_engine.py
is imported softly (spec: it is being written in parallel) - its absence
degrades the TTM-completeness check to a warning, never a crash.

Python 3 stdlib only. Free, keyless.
"""
import argparse
import datetime
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import urllib.parse
import urllib.request

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))

# SEC's fair-access policy requires a descriptive User-Agent. sec_fundamentals
# reads this from the environment AT IMPORT TIME, so it must be set before the
# module is loaded below. Same convention sec_fundamentals.py itself suggests.
# SEC fair-access requires a real contact address on every request. Prefer
# setting SEC_USER_AGENT in the environment; this fallback exists so an
# unattended run does not die on a missing variable, but an identity baked
# into a shared repository is a smell - it makes every clone speak as one
# person to a regulator.
os.environ.setdefault("SEC_USER_AGENT", "Adam Haglund haglund.adam@icloud.com")

UA = "Mozilla/5.0 (compatible; investment-analyst-skill/1.0)"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _soft_load(name):
    try:
        return _load(name)
    except Exception as e:  # noqa: BLE001 - a sibling script may be mid-edit or unreachable
        print("(valuation_gate: %s not available - %s)" % (name, e), file=sys.stderr)
        return None


# finfact is the trust boundary this whole file rests on: hard-required.
finfact = _load("finfact")
FinancialFact = finfact.FinancialFact
State = finfact.State
Verification = finfact.Verification
FRESHNESS_DAYS = finfact.FRESHNESS_DAYS

# Data-gathering siblings: only needed by the CLI orchestration below, never
# by gate() itself. Loaded softly so a missing/broken sibling degrades the CLI
# rather than making this module unimportable.
nordic_shares = _soft_load("nordic_shares")
quote = _soft_load("quote")
esef_fundamentals = _soft_load("esef_fundamentals")
sec_fundamentals = _soft_load("sec_fundamentals")
corporate_actions = _soft_load("corporate_actions")

# ttm_engine.py is being written in parallel by another agent (spec instruction:
# do not import it hard). Its absence must not break this file today.
try:
    ttm_engine = _load("ttm_engine")
except Exception:
    ttm_engine = None


# ---------------------------------------------------------------------------
# Thresholds (spec Sec7.2) - each one is a judgement call, so each is justified.
# ---------------------------------------------------------------------------

# A price older than this cannot be called "current" under any circumstance.
# quote.py's own staleness() bands treat >96h (4 days) as STALE-verify-before-
# quoting; that is the same boundary used here, wide enough to cover a Friday
# close read on a Monday through a public holiday, no wider.
PRICE_MAX_AGE_DAYS = 4

# The longest a financial fact's period end may lag the price date before the
# multiple is presumptively stale. Built from the reporting cadence: a fresh
# TTM should turn over every quarter (90 days), and issuers are typically given
# on the order of 45 days to file an interim report after a quarter closes
# (SEC 10-Q: 40-45 days for large filers; Nasdaq Nordic / EU Transparency
# Directive interim windows are comparable). 90 + 45 = 135 days is therefore
# the point by which a FRESHER interim report should already exist and should
# have been rolled forward into the figure being gated. Sandvik's actual gap
# (608 days) blows through this by more than 4x - it is not a borderline case.
MAX_EARNINGS_LAG_DAYS = 135

# Below this fraction of the hard limit, print a heads-up rather than a fail -
# the figure is still usable but a fresher interim may already be sitting
# unfetched.
LAG_WARN_FRACTION = 0.7

# A quarter-to-quarter gap outside this band means a quarter was skipped,
# duplicated, or the two facts do not belong to the same reporting cadence.
TTM_QUARTER_GAP_MIN_DAYS = 60
TTM_QUARTER_GAP_MAX_DAYS = 110

FILINGS_API = "https://filings.xbrl.org/api/filings"
FILINGS_BASE = "https://filings.xbrl.org"


def _as_date(x):
    if x is None:
        return None
    if isinstance(x, datetime.date):
        return x
    return datetime.date.fromisoformat(str(x)[:10])


# ---------------------------------------------------------------------------
# Per-check results. Each check returns one of these; nothing here ever
# returns a bare bool, so the reason a check failed travels with the verdict.
# ---------------------------------------------------------------------------

def _res(status, check, state, detail):
    return {"check": check, "status": status, "state": state.value if state else None,
            "detail": detail}


def _ok(check, detail):
    return _res("PASS", check, None, detail)


def _warn(check, state, detail):
    return _res("WARN", check, state, detail)


def _fail(check, state, detail):
    return _res("FAIL", check, state, detail)


# ---------------------------------------------------------------------------
# Check 1: price timestamp
# ---------------------------------------------------------------------------

def check_price_timestamp(price_fact, as_of):
    if price_fact is None:
        return _fail("price_timestamp", State.DATA_NOT_AVAILABLE,
                     "no price fact was supplied - there is nothing to certify as current.")
    ts = price_fact.publication_date or price_fact.effective_date
    if ts is None:
        return _fail("price_timestamp", State.DATA_STALE,
                     "the price carries no timestamp at all; freshness cannot be certified.")
    age = (as_of - ts).days
    if age < 0:
        return _warn("price_timestamp", None,
                     "the price timestamp (%s) is AFTER the as-of date (%s) - check which "
                     "clock produced this." % (ts, as_of))
    if age > PRICE_MAX_AGE_DAYS:
        return _fail("price_timestamp", State.DATA_STALE,
                     "the price is %d days old (%s), past the %d-day limit for calling a quote "
                     "current." % (age, ts, PRICE_MAX_AGE_DAYS))
    if age > FRESHNESS_DAYS.get("price", 1):
        return _warn("price_timestamp", State.DATA_STALE,
                     "the price is %d day(s) old (%s) - normal across a weekend or holiday "
                     "close, but confirm before treating it as live." % (age, ts))
    return _ok("price_timestamp", "the price is %d day(s) old (%s)." % (age, ts))


# ---------------------------------------------------------------------------
# Check 2: financial period vs price date
# ---------------------------------------------------------------------------

def check_period_lag(earnings_fact, price_fact, context):
    label = context.get("metric_name") or earnings_fact.metric
    period_end, price_date = earnings_fact.period_end, price_fact.period_end
    lag = (price_date - period_end).days
    if lag < 0:
        return _fail("period_lag", State.VALUATION_INTEGRITY_FAILED,
                     "%s's period ends %s, AFTER the price date %s - a mismatched pairing, "
                     "not a valid multiple." % (label, period_end, price_date))
    if lag > MAX_EARNINGS_LAG_DAYS:
        return _fail("period_lag", State.DATA_STALE,
                     "%s is based on financial data ending %s, %d days before the price date. "
                     "Roll forward with the interim report before using this multiple."
                     % (label, period_end, lag))
    if lag > MAX_EARNINGS_LAG_DAYS * LAG_WARN_FRACTION:
        return _warn("period_lag", State.DATA_STALE,
                     "%s ends %s, %d days before the price date - approaching the %d-day "
                     "limit; a fresher interim report may already exist and should be sought."
                     % (label, period_end, lag, MAX_EARNINGS_LAG_DAYS))
    return _ok("period_lag", "%s ends %s, %d days before the price date (limit %d)."
              % (label, period_end, lag, MAX_EARNINGS_LAG_DAYS))


# ---------------------------------------------------------------------------
# Check 3: publication date present (and, free with it, not from the future)
# ---------------------------------------------------------------------------

def check_publication_date(earnings_fact, as_of):
    if earnings_fact.publication_date is None:
        return _fail("publication_date", State.POINT_IN_TIME_UNVERIFIED,
                     "%s carries no publication date - it cannot be shown to have been public "
                     "knowledge at all, let alone as of %s." % (earnings_fact.metric, as_of))
    available, state = earnings_fact.is_available_as_of(as_of)
    if not available:
        return _fail("publication_date", state,
                     "%s was published %s, AFTER the as-of date %s - using it here is "
                     "hindsight, not analysis." % (earnings_fact.metric,
                                                   earnings_fact.publication_date, as_of))
    return _ok("publication_date", "%s published %s, before the as-of date %s."
              % (earnings_fact.metric, earnings_fact.publication_date, as_of))


# ---------------------------------------------------------------------------
# Check 4: share count semantics and certainty
# ---------------------------------------------------------------------------

def check_share_count(shares_fact, context):
    if shares_fact is None:
        return _fail("share_count", State.SHARE_COUNT_UNCERTAIN,
                     "no share count was supplied.")
    semantics = context.get("share_semantics") or shares_fact.note
    per_share = context.get("per_share_metric", True)
    if not semantics:
        return _warn("share_count", State.SHARE_COUNT_UNCERTAIN,
                     "the share count has no declared semantic (basic weighted-average / "
                     "diluted weighted-average / listed-registered) - which one this is "
                     "changes the answer and was not recorded.")
    if per_share and "registered" in semantics:
        return _fail("share_count", State.SHARE_COUNT_UNCERTAIN,
                     "the share count is %s (%s shares, including treasury) - the right basis "
                     "for market cap, the WRONG basis for a per-share earnings multiple. Use "
                     "the diluted weighted-average count implied by the EPS calculation "
                     "instead." % (semantics,
                                   "{:,.0f}".format(shares_fact.value) if shares_fact.value else "?"))
    if shares_fact.verification == Verification.SINGLE_SOURCE:
        return _warn("share_count", None,
                     "the share count (%s) is single-sourced, not cross-verified against a "
                     "second independent origin." % semantics)
    return _ok("share_count", "share count is %s (%s)." % (semantics, shares_fact.verification.value))


# ---------------------------------------------------------------------------
# Check 5: currency of price vs currency of earnings
# ---------------------------------------------------------------------------

def check_currency(price_fact, earnings_fact, context):
    pc, ec = price_fact.currency, earnings_fact.currency
    if not pc or not ec:
        return _fail("currency", State.VALUATION_INTEGRITY_FAILED,
                     "the price currency (%s) or the earnings currency (%s) is unknown - a "
                     "multiple cannot be certified correct when one side of it is unlabelled."
                     % (pc or "unknown", ec or "unknown"))
    if pc == ec:
        return _ok("currency", "price and earnings are both in %s." % pc)
    fx_rate = context.get("fx_rate")
    if fx_rate:
        return _warn("currency", None,
                     "price is %s and earnings are %s; converted at a supplied FX rate of "
                     "%.4f - confirm that rate's own timestamp is current before trusting the "
                     "result." % (pc, ec, fx_rate))
    return _fail("currency", State.VALUATION_INTEGRITY_FAILED,
                "price is quoted in %s but earnings are reported in %s. A multiple mixing "
                "them is wrong by the FX rate - convert one side before forming it."
                % (pc, ec))


# ---------------------------------------------------------------------------
# Check 6: corporate actions between the earnings period end and the price date
# ---------------------------------------------------------------------------

def check_corporate_actions(earnings_fact, price_fact, context):
    events = context.get("corporate_action_facts")
    if events is None:
        return _warn("corporate_actions", None,
                     "no corporate-action disclosure log was checked - the free source this "
                     "toolkit uses (Nasdaq CNS share-count disclosures) only covers Nordic "
                     "issuers, so a share-count-changing event between the earnings period "
                     "and the price date cannot be ruled out here.")
    lo, hi = earnings_fact.period_end, price_fact.period_end
    in_window = [e for e in events if e.period_end and lo < e.period_end <= hi]
    if not in_window:
        return _ok("corporate_actions",
                   "no share-count-changing disclosure found between %s and %s (%d "
                   "disclosure(s) on record checked)." % (lo, hi, len(events)))
    worst = max(in_window, key=lambda e: e.value or 0)
    return _fail("corporate_actions", State.VALUATION_INTEGRITY_FAILED,
                "a share-count disclosure on %s (%s shares) falls between the earnings period "
                "end (%s) and the price date (%s) - naive per-share arithmetic across that "
                "boundary is invalid." % (worst.period_end,
                                          "{:,.0f}".format(worst.value) if worst.value else "?",
                                          lo, hi))


# ---------------------------------------------------------------------------
# Check 7: TTM completeness
# ---------------------------------------------------------------------------

def check_ttm_completeness(earnings_fact, context):
    as_of = context["as_of"]
    is_ttm = bool(context.get("is_ttm"))
    quarters = context.get("ttm_quarters")
    ttm_result = context.get("ttm_engine_result")

    if ttm_result is not None:
        complete = getattr(ttm_result, "complete", None)
        if complete is True:
            return _ok("ttm_completeness", "ttm_engine confirms four contiguous quarters.")
        if complete is False:
            return _fail("ttm_completeness", State.TTM_INCOMPLETE,
                         getattr(ttm_result, "reason", None)
                         or "ttm_engine reports an incomplete trailing-twelve-month window.")

    if not is_ttm:
        return _warn("ttm_completeness", None,
                     "%s is a single-period figure (period end %s), not a claimed rolling "
                     "trailing-twelve-month number. No completeness claim is being made here "
                     "- but this figure must not be silently relabelled TTM downstream."
                     % (earnings_fact.metric, earnings_fact.period_end))

    if not quarters:
        return _fail("ttm_completeness", State.TTM_INCOMPLETE,
                     "%s is asserted as TTM but no constituent quarters were supplied to "
                     "verify it actually covers four contiguous quarters ending near %s. "
                     "Wire in ttm_engine.py or pass ttm_quarters explicitly before trusting "
                     "this as TTM." % (earnings_fact.metric, as_of))

    ends = sorted(q.period_end for q in quarters if q.period_end)
    if len(ends) < 4:
        return _fail("ttm_completeness", State.TTM_INCOMPLETE,
                     "only %d of the 4 quarters a TTM figure needs are present (%s)."
                     % (len(ends), ", ".join(str(e) for e in ends)))
    gaps = [(b - a).days for a, b in zip(ends, ends[1:])]
    bad = [g for g in gaps if not (TTM_QUARTER_GAP_MIN_DAYS <= g <= TTM_QUARTER_GAP_MAX_DAYS)]
    if bad:
        return _fail("ttm_completeness", State.TTM_INCOMPLETE,
                     "the supplied quarters are not evenly spaced (gaps of %s days) - a "
                     "quarter is likely missing or duplicated." % ", ".join(str(g) for g in gaps))
    lag_last = (as_of - ends[-1]).days
    if lag_last > MAX_EARNINGS_LAG_DAYS:
        return _fail("ttm_completeness", State.TTM_INCOMPLETE,
                     "the most recent constituent quarter ends %s, %d days before the as-of "
                     "date - the TTM window itself is stale." % (ends[-1], lag_last))
    return _ok("ttm_completeness", "four contiguous quarters ending %s, %d days before as-of."
              % (ends[-1], lag_last))


# ---------------------------------------------------------------------------
# Check 8: restatement status
# ---------------------------------------------------------------------------

def check_restatement(earnings_fact, context):
    if earnings_fact.verification == Verification.CONFLICT:
        return _fail("restatement", State.DATA_CONFLICT,
                     "sources disagree on this figure beyond tolerance - likely a restatement "
                     "that has not been reconciled to a single value.")
    restated = context.get("restated")
    detail = context.get("restatement_detail")
    if restated is True:
        return _warn("restatement", None,
                     "this figure has been restated since first publication%s; make sure any "
                     "comparison period uses the same restated basis."
                     % (" (%s)" % detail if detail else ""))
    if restated is False:
        return _ok("restatement", "checked against the prior filing's own figure for the same "
                                  "period; no restatement beyond 1%% found%s."
                                  % (" (%s)" % detail if detail else ""))
    return _warn("restatement", None,
                "restatement status was not checked - only one filing was available for this "
                "period, or no comparison was attempted.")


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def _run_checks(price_fact, earnings_fact, shares_fact, context):
    results = []

    results.append(check_price_timestamp(price_fact, context["as_of"]))

    if earnings_fact is None:
        for check in ("period_lag", "publication_date", "ttm_completeness", "restatement"):
            results.append(_fail(check, State.DATA_NOT_AVAILABLE,
                                 "no earnings fact was supplied - nothing to gate."))
    else:
        if price_fact is not None:
            results.append(check_period_lag(earnings_fact, price_fact, context))
        results.append(check_publication_date(earnings_fact, context["as_of"]))
        results.append(check_ttm_completeness(earnings_fact, context))
        results.append(check_restatement(earnings_fact, context))

    results.append(check_share_count(shares_fact, context))

    if price_fact is not None and earnings_fact is not None:
        results.append(check_currency(price_fact, earnings_fact, context))
        results.append(check_corporate_actions(earnings_fact, price_fact, context))

    return results


def _wrap(prefix, text, width=78):
    indent = " " * (len(prefix) + 1)
    return textwrap.fill(text, width=width, initial_indent=prefix + " ",
                         subsequent_indent=indent)


def _format_report(company, metric_name, results):
    fails = [r for r in results if r["status"] == "FAIL"]
    warns = [r for r in results if r["status"] == "WARN"]
    passed = not fails

    lines = ["VALUATION INTEGRITY: %s" % ("PASSED" if passed else "FAILED")]
    if company or metric_name:
        lines.append("Company: %s   Metric: %s" % (company or "?", metric_name or "?"))
    if not passed:
        for i, f in enumerate(fails):
            prefix = "Reason:" if i == 0 else "Also failed [%s]:" % f["check"]
            lines.append(_wrap(prefix, f["detail"]))
    if warns:
        lines.append("")
        lines.append("Warnings (do not block a PASS, but read before trusting the number):")
        for w in warns:
            lines.append(_wrap("  -", "[%s] %s" % (w["check"], w["detail"])))
    elif passed:
        lines.append("No warnings.")
    return passed, "\n".join(lines)


def gate_detail(price_fact, earnings_fact, shares_fact=None, **context):
    """Run every spec-Sec7 check and return (passed, states, report, results).

    `results` is the full per-check record: [{"check", "status", "state",
    "detail"}, ...], status one of PASS/WARN/FAIL. Use this when you want more
    than the headline verdict - `gate()` below is the same call with only the
    first three returned, for callers that just need a go/no-go.
    """
    # A gate that crashes on a malformed argument is not a gate. Validate the
    # arguments before anything dereferences them - the setdefault below already
    # touches earnings_fact.metric, so this has to come first.
    for label, obj in (("price_fact", price_fact),
                       ("earnings_fact", earnings_fact),
                       ("shares_fact", shares_fact)):
        # NOT isinstance. Every script here loads its siblings through
        # importlib.spec_from_file_location, which creates a SEPARATE module
        # object per loader - so the caller's FinancialFact and ours are
        # different classes with the same name and isinstance is always False.
        # What the gate actually requires is provenance, so check for it
        # structurally.
        missing = [a for a in ("metric", "period_end", "publication_date",
                               "source_tier", "currency")
                   if not hasattr(obj, a)]
        if obj is not None and missing:
            bad = _fail("input_types", State.VALUATION_INTEGRITY_FAILED,
                        "%s must carry provenance (a FinancialFact or "
                        "equivalent) or be None. Got %s, missing %s. A bare "
                        "value has no period, source or publication date, so "
                        "no integrity check can be performed on it."
                        % (label, type(obj).__name__, ", ".join(missing)))
            return (False, [State.VALUATION_INTEGRITY_FAILED],
                    "VALUATION INTEGRITY: FAILED" + chr(10) +
                    "Reason: " + bad["detail"],
                    [bad])

    context.setdefault("as_of", datetime.date.today())
    context["as_of"] = _as_date(context["as_of"])
    context.setdefault("metric_name", earnings_fact.metric if earnings_fact else None)
    context.setdefault("per_share_metric", True)

    results = _run_checks(price_fact, earnings_fact, shares_fact, context)
    passed, report = _format_report(context.get("company"), context.get("metric_name"), results)

    states, seen = [], set()
    for r in results:
        if r["status"] in ("FAIL", "WARN") and r["state"]:
            s = State(r["state"])
            if s not in seen:
                seen.add(s)
                states.append(s)
    return passed, states, report, results


def gate(price_fact, earnings_fact, shares_fact=None, **context):
    """Spec Sec7 hard gate. Returns (passed: bool, states: list[State], report: str).

    price_fact, earnings_fact, shares_fact are finfact.FinancialFact or None.
    Recognised **context keys (all optional):
        as_of              date or ISO string, default today
        metric_name         e.g. "EV/EBIT (TTM)", for readable messages
        per_share_metric    bool, default True - is this an EPS-style ratio,
                            where a registered/listed share count is WRONG
        share_semantics      str override for shares_fact.note
        fx_rate              float - if price/earnings currencies differ,
                            supplying this downgrades the currency check from
                            FAIL to WARN (it is on the caller to keep it fresh)
        corporate_action_facts   list[FinancialFact] of share-count disclosures,
                            or None if that source was not checked
        is_ttm                bool - is earnings_fact claimed as a rolling TTM
        ttm_quarters          list[FinancialFact], the 4 constituent quarters
        ttm_engine_result      an object from ttm_engine.py with .complete /
                            .reason, if that module was wired in
        restated / restatement_detail   bool / str, if a restatement check
                            was actually performed by the caller
        company               str, for the report header only
    """
    passed, states, report, _results = gate_detail(price_fact, earnings_fact, shares_fact,
                                                    **context)
    return passed, states, report


# ===========================================================================
# CLI data gathering - everything below calls the sibling scripts against
# live data. gate() above has no dependency on any of this.
# ===========================================================================

def _run_company_resolve(name, timeout=100):
    """Subprocess, not import: company_resolve.py owns argparse and a fair
    amount of network-backed module state of its own, and running it as the
    CLI it is meant to be keeps this file decoupled from that."""
    path = os.path.join(HERE, "company_resolve.py")
    try:
        proc = subprocess.run([sys.executable, path, name, "--json"],
                              capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    out = (proc.stdout or "").strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def _esef_filing_meta(lei):
    """The filing index's own date_added/processed timestamps.

    esef_fundamentals.list_filings() does not surface these (it only needs
    period_end/json_url for its own purpose), so this reads the same public
    filings.xbrl.org endpoint directly for the fields this file needs."""
    params = {"filter[entity.identifier]": lei, "page[size]": "4", "sort": "-period_end"}
    url = FILINGS_API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as r:
        data = json.loads(r.read())
    out = []
    for f in data.get("data") or []:
        a = f.get("attributes") or {}
        out.append({"period_end": a.get("period_end"), "json_url": a.get("json_url"),
                    "fxo_id": a.get("fxo_id"),
                    "date_added": (a.get("date_added") or "")[:10] or None,
                    "processed": (a.get("processed") or "")[:10] or None})
    return out


def _price_fact_from_yahoo(yahoo_symbol, fallback_currency=None):
    if quote is None:
        return None, "quote.py not available"
    try:
        y = quote.from_yahoo(yahoo_symbol)
    except Exception as e:
        return None, "Yahoo lookup raised %s" % e
    if not y or y.get("price") is None or not y.get("as_of_utc"):
        return None, "no live quote returned for %s" % yahoo_symbol
    ts = datetime.datetime.fromisoformat(y["as_of_utc"]).date()
    fact = FinancialFact(metric="price", value=y["price"], source="yahoo",
                         period_end=ts, currency=y.get("currency") or fallback_currency,
                         publication_date=ts, effective_date=ts, freshness_key="price",
                         source_detail="Yahoo Finance chart endpoint (%s)" % yahoo_symbol,
                         note="regularMarketTime %s" % y["as_of_utc"])
    return fact, None


NA = "DATA NOT AVAILABLE"


def _gather_nordic(name):
    notes = []
    identity = _run_company_resolve(name)
    lei = ticker = quote_ccy = rep_ccy = None
    total_shares = None
    share_classes = []

    if identity and identity.get("resolved"):
        lei = identity.get("lei")
        lei = None if lei in (None, NA) else lei
        ticker = identity.get("ticker")
        quote_ccy = identity.get("currency")
        quote_ccy = None if quote_ccy in (None, NA) else quote_ccy
        rep_ccy = identity.get("reporting_currency")
        rep_ccy = None if rep_ccy in (None, NA) else rep_ccy
        total_shares = identity.get("total_listed_shares")
        share_classes = identity.get("share_classes") or []
        notes.append("identity resolved via company_resolve.py (confidence %.2f, %s)."
                     % (identity.get("confidence", 0), identity.get("confidence_basis")))
        for w in identity.get("warnings") or []:
            notes.append("company_resolve.py: %s" % w)
    else:
        reason = (identity or {}).get("reason") if identity else "no response"
        notes.append("company_resolve.py did not cleanly resolve %r (%s); falling back to "
                     "raw Nasdaq Nordic search - currency and fiscal-year metadata will be "
                     "less certain." % (name, reason))
        if nordic_shares is None:
            return None, notes + ["nordic_shares.py not available for the fallback."]
        try:
            hits = nordic_shares.search(name)
        except Exception as e:
            hits = []
            notes.append("nordic_shares.search failed: %s" % e)
        if not hits:
            return None, notes + ["no Nasdaq Nordic listing matched %r." % name]
        needle = name.lower()
        exact = [h for h in hits if needle in (h["name"] or "").lower()]
        pool = exact or hits
        # A bare `pool[0]` here is exactly the bug this fallback exists to
        # avoid one layer up: "Volvo" matches both AB Volvo (root VOLV) and
        # Volvo Car AB (root VOLCAR). Group by root symbol - which collapses
        # A/B share classes of the SAME issuer but not two different issuers -
        # and refuse instead of guessing when more than one issuer survives.
        roots = {}
        for h in pool:
            roots.setdefault(nordic_shares.root_symbol(h["symbol"]), []).append(h)
        if len(roots) > 1:
            names = sorted({"%s (%s)" % (h["name"], h["symbol"]) for h in pool})
            return None, notes + [
                "COMPANY_IDENTITY_AMBIGUOUS: %d distinct issuers on Nasdaq Nordic match "
                "%r (%s). Re-run with the exact legal name, ticker or ISIN so the right "
                "one is used." % (len(roots), name, "; ".join(names))]
        chosen = pool
        ticker = chosen[0]["symbol"]
        quote_ccy = chosen[0]["currency"]
        root = nordic_shares.root_symbol(ticker)
        share_classes = []
        for h in hits:
            if nordic_shares.root_symbol(h["symbol"]) != root:
                continue
            try:
                s = nordic_shares.summary(h["orderbookId"])
            except SystemExit as e:
                # nordic_shares.summary() raises SystemExit on a failed
                # orderbook lookup. Uncaught, that would propagate past
                # gather()'s `except Exception` (SystemExit is not an
                # Exception) and kill the whole CLI over one bad class. Treat
                # it as a missing share count for this one class instead - the
                # partial-sum handling below then reports it honestly rather
                # than silently understating the total.
                notes.append("nordic_shares.summary failed for %s: %s" % (h["symbol"], e))
                s = {}
            except Exception as e:
                notes.append("nordic_shares.summary raised %s for %s: %s"
                             % (type(e).__name__, h["symbol"], e))
                s = {}
            share_classes.append({"symbol": h["symbol"], "shares": s.get("shares")})

    company_label = (identity.get("company_name") if identity and identity.get("resolved")
                    else name)

    # ---- price -------------------------------------------------------
    yahoo_sym = (ticker or "").replace(" ", "-") + ".ST"
    price_fact, err = _price_fact_from_yahoo(yahoo_sym, quote_ccy) if ticker else (None, "no ticker")
    if err:
        notes.append(err)

    # ---- shares --------------------------------------------------------
    # A class whose summary() fetch failed contributes no share count. Summing
    # with `or 0` would silently understate the total while source_detail kept
    # claiming every class was counted (verified: a failed A-line fetch would
    # have AB Volvo report ~1.59bn shares as the all-class total instead of
    # the true ~2.03bn) - so a missing count is tracked, not zeroed.
    have_counts = [c for c in share_classes if c.get("shares")]
    missing_symbols = [c.get("symbol") or "?" for c in share_classes if not c.get("shares")]
    if total_shares is None and share_classes:
        total_shares = sum(c["shares"] for c in have_counts) if have_counts else None
    shares_fact = None
    if total_shares:
        n_classes = len(share_classes)
        n_summed = len(have_counts) if have_counts else n_classes
        partial = bool(missing_symbols) and n_summed < n_classes
        if partial:
            source_detail = ("Nasdaq Nordic reference data, %d of %d listed class(es) "
                             "summed (missing: %s)"
                             % (n_summed, n_classes, ", ".join(missing_symbols)))
        else:
            source_detail = "Nasdaq Nordic reference data, %d listed class(es) summed" % n_classes
        shares_fact = FinancialFact(
            metric="shares_outstanding", value=total_shares, source="nasdaq_reference",
            unit="shares", period_end=datetime.date.today(), publication_date=datetime.date.today(),
            freshness_key="shares_outstanding",
            verification=Verification.INCOMPLETE if partial else Verification.SINGLE_SOURCE,
            source_detail=source_detail,
            note="listed_registered_including_treasury")
        if partial:
            notes.append("share count is a PARTIAL sum: %d of %d listed classes summed; "
                         "no share count for %s. total_shares UNDERSTATES the true "
                         "all-class total - do not use it for market cap until the "
                         "missing class is confirmed by hand."
                         % (n_summed, n_classes, ", ".join(missing_symbols)))
        elif n_classes > 1:
            notes.append("share count sums %d listed classes (%s) - right basis for market "
                         "cap, wrong basis for EPS." % (n_classes,
                         ", ".join(c.get("symbol") or "?" for c in share_classes)))

    # ---- earnings (ESEF annual report) ----------------------------------
    earnings_fact = None
    restated, restatement_detail = None, None
    if lei is None:
        notes.append("no LEI resolved - cannot look up ESEF filings.")
    elif esef_fundamentals is None:
        notes.append("esef_fundamentals.py not available.")
    else:
        try:
            meta = _esef_filing_meta(lei)
        except Exception as e:
            meta = []
            notes.append("ESEF filing index unreachable: %s" % e)
        if not meta:
            notes.append("no ESEF (Inline XBRL) annual report indexed for LEI %s - this "
                         "issuer may file no ESEF at all (e.g. First North / Spotlight) or use "
                         "extension taxonomy tags esef_fundamentals.py does not recognise."
                         % lei)
        else:
            latest = meta[0]
            try:
                doc = esef_fundamentals.get_json(FILINGS_BASE + latest["json_url"])
                facts = esef_fundamentals.extract(doc)
            except Exception as e:
                facts = {}
                notes.append("could not fetch the ESEF filing body: %s" % e)
            oi = esef_fundamentals.pick(facts, esef_fundamentals.CONCEPTS["operating_income"],
                                        True) if facts else {}
            row = oi.get(latest["period_end"])
            if not row:
                notes.append("operating_income is not tagged in the latest ESEF filing (%s)."
                             % latest["fxo_id"])
            else:
                value, unit, concept = row
                currency = rep_ccy
                if not currency and unit and unit.startswith("iso4217:"):
                    currency = unit.split(":", 1)[1]
                pub = latest.get("date_added") or latest.get("processed") or latest["period_end"]
                earnings_fact = FinancialFact(
                    metric="operating_income", value=value, source="esef",
                    period_end=latest["period_end"], currency=currency,
                    publication_date=pub, freshness_key="annual_financials",
                    source_detail="IFRS concept %s, filing %s" % (concept, latest["fxo_id"]),
                    note="Full fiscal-year figure - ESEF indexes annual reports only; this is "
                         "NOT a rolling trailing-twelve-month number.")
                if len(meta) > 1:
                    prev = meta[1]
                    try:
                        pdoc = esef_fundamentals.get_json(FILINGS_BASE + prev["json_url"])
                        pfacts = esef_fundamentals.extract(pdoc)
                        poi = esef_fundamentals.pick(
                            pfacts, esef_fundamentals.CONCEPTS["operating_income"], True)
                        prow = poi.get(prev["period_end"])
                        comp_row = oi.get(prev["period_end"])
                        if prow and comp_row and prow[0] and comp_row[0]:
                            if abs(comp_row[0] - prow[0]) / abs(prow[0]) > 0.01:
                                restated = True
                                restatement_detail = (
                                    "%s originally %.0f in %s, carried as %.0f in %s's "
                                    "comparative column" % (prev["period_end"], prow[0],
                                                            prev["fxo_id"], comp_row[0],
                                                            latest["fxo_id"]))
                            else:
                                restated = False
                                restatement_detail = (
                                    "%s matches within 1%% between %s and its comparative in %s"
                                    % (prev["period_end"], prev["fxo_id"], latest["fxo_id"]))
                    except Exception:
                        pass

    # ---- corporate actions (Nasdaq CNS share-count disclosure log) -----
    ca_facts = None
    if corporate_actions is None:
        notes.append("corporate_actions.py not available.")
    else:
        try:
            hits = corporate_actions.resolve_company(name)
        except Exception as e:
            hits = []
            notes.append("Nasdaq CNS company resolution failed: %s" % e)
        if not hits:
            notes.append("no Nasdaq CNS company matched %r for the corporate-actions check."
                         % name)
        else:
            try:
                events, _unparsed = corporate_actions.share_history(hits[0]["company"],
                                                                    fetch_bodies=6)
                ca_facts = []
                for e in events:
                    if not e.get("date") or e.get("total_shares") is None:
                        continue
                    try:
                        d = datetime.date.fromisoformat(e["date"][:10])
                    except ValueError:
                        continue
                    ca_facts.append(FinancialFact(
                        metric="shares_outstanding", value=e["total_shares"],
                        source="nasdaq_cns", unit="shares", period_end=d, publication_date=d,
                        freshness_key="shares_outstanding", note=e.get("title")))
                notes.append("%d share-count disclosure(s) on record for %s (Nasdaq CNS)."
                             % (len(ca_facts), hits[0]["company"]))
            except Exception as e:
                notes.append("Nasdaq CNS share-history lookup failed: %s" % e)

    context = {
        "company": company_label,
        "metric_name": "EV/EBIT (TTM)",
        "per_share_metric": True,
        "share_semantics": shares_fact.note if shares_fact else None,
        "is_ttm": True,   # the realistic failure mode: upstream calls the latest
                          # available annual figure "TTM" because it is the newest
                          # thing on file, which is only true the day it is filed.
        "corporate_action_facts": ca_facts,
        "restated": restated,
        "restatement_detail": restatement_detail,
    }
    bundle = {"price_fact": price_fact, "earnings_fact": earnings_fact,
             "shares_fact": shares_fact, "context": context}
    return bundle, notes


def _gather_us(name):
    notes = []
    if sec_fundamentals is None:
        return None, ["sec_fundamentals.py not available."]
    try:
        cik, entity_name = sec_fundamentals.resolve(name)
    except SystemExit as e:
        return None, [str(e)]
    try:
        facts = sec_fundamentals.get("%s/api/xbrl/companyfacts/CIK%s.json"
                                    % (sec_fundamentals.BASE, cik))
    except Exception as e:
        return None, ["SEC EDGAR unreachable: %s" % e]

    def latest(tags, annual_fallback=True):
        found = sec_fundamentals.series(facts, tags, annual=False)
        if not found and annual_fallback:
            found = sec_fundamentals.series(facts, tags, annual=True)
        if not found:
            return None
        return found[sorted(found)[-1]]

    oi_row = latest(sec_fundamentals.CONCEPTS["operating_income"])
    sd_row = latest(sec_fundamentals.CONCEPTS["shares_diluted"])

    earnings_fact = None
    if oi_row:
        is_quarterly = str(oi_row["form"]).startswith("10-Q")
        currency = "USD" if oi_row.get("unit") == "USD" else None
        earnings_fact = FinancialFact(
            metric="operating_income", value=oi_row["val"], source="sec_xbrl",
            period_end=oi_row["end"], currency=currency, publication_date=oi_row["filed"],
            freshness_key="interim_financials" if is_quarterly else "annual_financials",
            source_detail="XBRL tag %s, form %s, accession %s"
                          % (oi_row["tag"], oi_row["form"], oi_row["accn"]),
            note=("latest discrete %s quarter - not an assembled TTM (10-Q income-statement "
                 "data has no discrete Q4; deriving one needs the 10-K minus nine months, "
                 "which this path does not attempt)." % oi_row["fp"] if is_quarterly
                 else "latest annual (10-K) figure."))
    else:
        notes.append("no operating_income tag matched in SEC companyfacts.")

    shares_fact = None
    if sd_row:
        shares_fact = FinancialFact(
            metric="shares_outstanding", value=sd_row["val"], source="sec_xbrl",
            unit="shares", period_end=sd_row["end"], publication_date=sd_row["filed"],
            freshness_key="shares_outstanding",
            source_detail="XBRL tag %s, form %s, accession %s"
                          % (sd_row["tag"], sd_row["form"], sd_row["accn"]),
            note="diluted_weighted_average")
    else:
        notes.append("no diluted-shares tag matched in SEC companyfacts.")

    price_fact, err = _price_fact_from_yahoo(name.upper())
    if err:
        notes.append(err)

    notes.append("SEC EDGAR has no free source of share-count-changing disclosures comparable "
                "to Nasdaq CNS, so the corporate-actions check cannot be verified on this path.")

    context = {
        "company": entity_name or name,
        "metric_name": "EV/EBIT (quarterly, not TTM)",
        "per_share_metric": True,
        "share_semantics": shares_fact.note if shares_fact else None,
        "is_ttm": False,
        "corporate_action_facts": None,
        "restated": None,
        "restatement_detail": None,
    }
    bundle = {"price_fact": price_fact, "earnings_fact": earnings_fact,
             "shares_fact": shares_fact, "context": context}
    return bundle, notes


def gather(name):
    """Best-effort live-data assembly for the CLI. Tries the Nordic chain
    first (company_resolve -> ESEF -> Nasdaq CNS), then SEC EDGAR. Returns
    (bundle_or_None, notes, path) where path is 'nordic', 'us', or None."""
    notes = []
    try:
        nordic_bundle, nordic_notes = _gather_nordic(name)
    except Exception as e:
        nordic_bundle, nordic_notes = None, ["Nordic path raised %s" % e]
    notes += nordic_notes
    if nordic_bundle and (nordic_bundle["price_fact"] or nordic_bundle["earnings_fact"]):
        return nordic_bundle, notes, "nordic"

    try:
        us_bundle, us_notes = _gather_us(name)
    except Exception as e:
        us_bundle, us_notes = None, ["US path raised %s" % e]
    notes += us_notes
    if us_bundle and (us_bundle["price_fact"] or us_bundle["earnings_fact"]):
        return us_bundle, notes, "us"

    return None, notes, None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _fact_summary(fact):
    if fact is None:
        return None
    return {"metric": fact.metric, "value": fact.value, "unit": fact.unit,
           "currency": fact.currency, "period_end": fact.period_end.isoformat(),
           "publication_date": fact.publication_date.isoformat() if fact.publication_date else None,
           "source": fact.source, "note": fact.note}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("company", help='company name or US ticker, e.g. "Sandvik", '
                                    '"AB Volvo", "Evolution", "KebNi", NVDA')
    ap.add_argument("--as-of", help="YYYY-MM-DD, default today")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--explain", action="store_true",
                    help="print every check (PASS included), not just failures/warnings")
    args = ap.parse_args()

    as_of = _as_date(args.as_of) if args.as_of else datetime.date.today()

    bundle, notes, path = gather(args.company)

    if bundle is None:
        if args.as_json:
            print(json.dumps({"company": args.company, "passed": False,
                              "report": "VALUATION INTEGRITY: FAILED", "notes": notes},
                             indent=2, ensure_ascii=False))
        else:
            print("VALUATION INTEGRITY: FAILED")
            print(_wrap("Reason:", "could not gather enough data for %r to run any check."
                       % args.company))
            for n in notes:
                print("  - %s" % n)
        sys.exit(1)

    ctx = dict(bundle["context"])
    ctx["as_of"] = as_of
    passed, states, report, results = gate_detail(bundle["price_fact"], bundle["earnings_fact"],
                                                  bundle["shares_fact"], **ctx)

    if args.as_json:
        print(json.dumps({
            "company": args.company, "data_path": path, "as_of": as_of.isoformat(),
            "metric_name": ctx.get("metric_name"), "passed": passed,
            "states": [s.value for s in states], "report": report,
            "checks": results,
            "inputs": {"price": _fact_summary(bundle["price_fact"]),
                      "earnings": _fact_summary(bundle["earnings_fact"]),
                      "shares": _fact_summary(bundle["shares_fact"])},
            "notes": notes,
        }, indent=2, ensure_ascii=False))
        return

    print(report)
    if args.explain:
        print()
        print("All checks (data path: %s):" % path)
        for r in results:
            print("  [%s] %-20s %s" % (r["status"], r["check"], r["detail"]))
    if notes:
        print()
        print("Data-gathering notes:")
        for n in notes:
            print("  - %s" % n)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
