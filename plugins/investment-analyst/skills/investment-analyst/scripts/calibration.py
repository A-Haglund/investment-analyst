#!/usr/bin/env python3
"""calibration.py - forward-only track record measurement for stored decisions.

WHY THIS EXISTS, AND WHY IT IS NARROWER THAN IT SOUNDS

The v2.6 architecture review concluded that historical BACKTESTING is not
scientifically defensible in this system, and references/data-quality.md says
so in the project's own voice: "Say so rather than implying a backtesting
capability that does not exist." Three structural facts make that true and
none of them go away here:

  * esef_fundamentals.py overwrites as-filed figures with the latest
    restatement, so the as-originally-reported number a past decision would
    need to be graded against does not exist.
  * Every universe build (nordic_shares.universe, the screens) queries the
    LIVE listing. A company that delisted, merged or went bankrupt between
    then and now is invisible to any "what would the screen have said"
    reconstruction. Survivorship bias is structural, not a data gap that a
    better query fixes.
  * There is no consensus history and no historical share register, so an
    "as investors knew it then" reconstruction of anything except price and
    the decision's OWN stored numbers is not possible.

THIS MODULE DOES NOT ATTEMPT ANY OF THAT. It does the one thing that needs
none of it: forward-only track record measurement, starting from the day
decision_record.py started giving decisions a durable, validated shape. A
decision stored today has a dated verdict, a validated price at that date, and
a real future close. That triangle is measurable without touching a single
one of the three problems above. Nothing here reconstructs, replays or
re-scores a past universe.

TWO RULES THAT ARE THE ACTUAL POINT OF THIS FILE

  1. A bucket below --min-n prints the literal string "INSUFFICIENT SAMPLE",
     never a number, in JSON as well as text. A hit rate on four decisions is
     noise wearing the costume of evidence, and refusing to overstate
     evidence is this codebase's whole discipline (see decision_record.py's
     own refusal-over-guessing posture). DEFAULT_MIN_N = 20: the 95%
     confidence half-width on a simple hit-rate proportion at n=20 is still
     roughly +-22 percentage points in the worst case (p=0.5) - wide enough
     that almost any observed rate is statistically indistinguishable from a
     coin flip, which is exactly the point at which printing a headline
     number invites false confidence rather than informing anyone. 20 is not
     a magic constant - it is configurable with --min-n for precisely that
     reason: the "right" minimum is a judgement call, not an identity, and
     this file refuses to pretend otherwise.
  2. NOTHING FEEDS BACK AUTOMATICALLY. This module is read-only with respect
     to every other number in the system: it never adjusts a score, a
     conviction, a cap or a screen threshold. It attaches outcomes to
     decisions and reports on them - full stop. Auto-tuning conviction from a
     handful of realized outcomes is how a system talks itself into
     confidence it has not earned; the fix for that temptation is not to
     build the tuning loop "carefully", it is to never build it. If a human
     reads this report and decides to change how conviction is assigned,
     that is a human decision made in some OTHER file - this one only
     describes what happened.

THE `outcome` OBJECT decision_record.py LEAVES AS None

A decision now carries, per measured horizon:

    decision["outcome"] = {
        "3m":  <horizon_outcome> | None,
        "6m":  <horizon_outcome> | None,
        "12m": <horizon_outcome> | None,
        "expectations": <expectation_calibration> | None,
    }

A decision measured at 3, 6 and 12 months answers three different questions
(did the market notice fast, or did it take a full year), so this module
supports three NAMED horizons rather than one implicit one, and a given
decision can carry all three, some, or none (none elapsed yet). A horizon key
is present in `outcome` ONLY once that horizon has actually elapsed since
`decision["as_of"]` - a partially-elapsed return is not an outcome, it is a
snapshot, and this module refuses to attach one (see compute_horizon_outcome:
status "PENDING").

Note this `horizon` is NOT the same concept as horizon.py's holding horizon
(the next dated event that resolves a thesis, e.g. an AGM date). horizon.py
answers "until when should this be held"; this module answers "how did the
call read N months after it was made". The name collision is real but the
concepts do not overlap.

Each `<horizon_outcome>` is:

    {
        "horizon": "3m",
        "target_date": "2027-02-28",       # as_of + the horizon, calendar months
        "matured": True,
        "price_then": {"value": 356.0, "currency": "SEK", "as_of": "..."},
        "price_later": {"value": 401.2, "currency": "SEK", "as_of": "2027-03-02"},
        "elapsed_days": 182,
        "realized_return": 0.1270,          # price_later / price_then - 1
        "corporate_actions": {
            "checked": True,
            "found": [{"date": ..., "type": ..., "title": ...}, ...],
            "comparability": "CLEAN" | "SPLIT_ADJUSTED_SERIES"
                            | "CAUTION_OTHER_ACTION" | "UNKNOWN",
            "note": "...",
        },
        "scenario": {
            "available": True,
            "landing": "BELOW_BEAR" | "BEAR_TO_BASE" | "WITHIN_BASE"
                     | "BASE_TO_BULL" | "ABOVE_BULL" | None,
            "detail": "...",
        },
        "superseded": {
            "is_superseded": False, "excluded_from_scoring": False,
            "superseded_by": None, "superseded_at": None,
        },
        "attached_at": "2027-03-02T09:14:00Z",
    }

REALIZED_RETURN IS A PRICE RETURN, NOT A TOTAL RETURN. nordic_shares.price_history
carries no dividend data, and this system verifies none, so a dividend paid
inside the measurement window is simply absent from `realized_return`. Say so
rather than quietly overstating precision the data does not have - a decision
held through an ex-dividend date will read as having underperformed its true
economic return by roughly the yield.

THE SPLIT-ADJUSTMENT QUESTION - RESOLVED, NOT ASSUMED

corporate_actions.py's module docstring ("THE ANSWER" section) settles this:
nordic_shares.price_history() is a BACK-ADJUSTED series, measured against four
confirmed, dated Stockholm splits in both directions (Mycronic 2:1, Investor
A/B 4:1, Bambuser 1:30 reverse, Nobia 1:10 reverse - zero discontinuity in any
of the four). Consequently a realized_return computed directly from that
series across a pure split/reverse-split is ALREADY correct and must never be
adjusted a second time. What genuinely breaks per-share comparability is
everything else in corporate_actions.BREAKS_PER_SHARE minus the two split
types - a rights issue, a directed issue, a buyback-and-cancellation, a
spin-off - because those change the economics behind the price, not just the
share count the price is quoted per. compute_horizon_outcome() therefore
never touches the price for a split; classify_corporate_action_window() below
exists only to tell the reader whether a NON-split action fell in the window,
in which case the realized return is flagged CAUTION_OTHER_ACTION rather than
silently trusted. This mirrors corporate_actions.py's own
split_adjustment_factor() warning, not a decision made independently here.

THE SUPERSEDED-DECISION RULE - set by the store, not invented here

thesis_ledger.py's decisions store makes `superseded_by` mean "explicitly
withdrawn or corrected via supersede_decision()", never "merely an older
decision that a newer one came after". Consequences, deliberately:

  * A company may hold several NON-superseded decisions over time (a March
    call and a September call on the same issuer). Each is its own valid
    dated observation and is scored independently - that is the statistically
    correct treatment, not a duplicate to be merged or the older one to be
    discarded.
  * A decision that WAS explicitly superseded (superseded_by is a decision_id,
    or the literal string "WITHDRAWN" when nothing replaced it) is EXCLUDED
    from hit-rate and expected-vs-realized scoring, full stop - it was
    retired rather than allowed to run its course, regardless of whether its
    own horizon had already elapsed by the time it was withdrawn. Its raw
    price outcome is still attached and visible on the record; only the
    aggregate scoring buckets skip it.
  * Excluding it is the right call, but it is also a selection effect (a
    system could in principle retire its worst-looking calls before they
    mature and call that a clean track record), so every report prints how
    many decisions were excluded this way per producer
    ("n_excluded_superseded") rather than dropping them silently.

PRODUCER GROUPING

/analyze and /screen build decisions through different selection processes (a
chosen name vs a systematic sweep), so pooling their hit rates would silently
average two different experiments. Every report groups by `producer`
(decision_record.PRODUCERS) and never pools across it.

THESIS-STATUS CUT

Each stored decision carries `thesis_ref` (the worst-standing active thesis's
status AT DECISION TIME - thesis_ledger.decision_thesis_ref, one of
thesis_ledger.STATUS_ORDER, or absent when no thesis was active) and
`validation_warnings`. Whether calls made against a WARNING or BROKEN thesis
did worse than calls made against a healthy one is a genuinely useful cut, so
the report includes it - subject to the same --min-n refusal as everything
else, since this axis will be the thinnest-sampled one for a long time.

WHAT THIS IS NOT

  - Not a backtester. See above.
  - Not a feedback loop. See above.
  - Not a consensus-comparison tool: no consensus history exists in this
    system, by design (see data-quality.md). Expectation calibration compares
    the DECISION'S OWN recorded implied_expectations against what the issuer
    subsequently reported - it needs no consensus data, which is exactly why
    it survives the constraint that blocks everything else.

Python 3 stdlib only. Windows/cp1252-safe.
"""
import argparse
import calendar
import datetime
import importlib.util
import json
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _soft_load(name):
    """Degrade to None with a clear message instead of crashing when a
    sibling is absent, mid-edit, or broken - e.g. thesis_ledger's decisions
    API on an older checkout. Prefers _bootstrap.soft_load (the v3.0.0
    canonical version of this idiom, which the module's own docstring notes
    corrects a real bug in some of the pre-existing per-script copies: it
    catches SystemExit explicitly, since SystemExit does not inherit from
    Exception and every sibling here raises it for "DATA NOT AVAILABLE").
    Falls back to an identical local implementation if _bootstrap.py itself
    is not present, so this file never depends on load order between
    concurrently-added modules."""
    try:
        return _load("_bootstrap").soft_load(name)
    except (Exception, SystemExit):
        pass
    try:
        return _load(name)
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        print("(calibration: %s not available - %s)" % (name, exc), file=sys.stderr)
        return None


# decision_record.py is the schema this whole file reads against - already
# built, pure, no network - so it is loaded hard, the same way valuation_gate
# hard-loads finfact as its trust boundary.
decision_record = _load("decision_record")

# Everything below is genuinely optional at import time:
#   thesis_ledger  - owns the decisions store (list_decisions/latest_decision/
#                    get_decision/add_decision/supersede_decision). Soft-loaded
#                    anyway: an older checkout or a sibling mid-edit must
#                    degrade to "nothing to calibrate" rather than crash.
#   nordic_shares  - only needed by --attach's live price lookup.
#   corporate_actions - only needed by --attach's corporate-action check.
# --report is pure aggregation over whatever is already stored and never
# needs nordic_shares or corporate_actions at all.


# ---------------------------------------------------------------------------
# Horizon convention
# ---------------------------------------------------------------------------

HORIZON_MONTHS = {"3m": 3, "6m": 6, "12m": 12}
ALL_HORIZONS = ("3m", "6m", "12m")

# How many days after the target date a trading bar may still count as "the"
# observation for that horizon (weekends, holidays, an illiquid name). Past
# this, treat the horizon as matured-but-unobservable rather than reach for a
# bar so far off the date that it answers a different question.
PRICE_MATCH_TOLERANCE_DAYS = 10

DEFAULT_MIN_N = 20

# 1 percentage point: an "in line" band for an implied long-run growth or
# margin figure compared with a single subsequently reported figure. This is
# a judgement call, not an identity - it is wide enough not to flag every
# forecast as "wrong" on ordinary noise, narrow enough not to wave through a
# figure that actually missed by several points.
EXPECTATION_TOLERANCE = 0.01

BUY_SIDE_VERDICTS = ("STRONG BUY", "BUY")
SELL_SIDE_VERDICTS = ("SELL", "STRONG SELL", "TRIM")
# HOLD has no directional call and is deliberately excluded from hit-rate:
# there is no "realized return" a HOLD is trying to predict.

# Mirrors thesis_ledger.STATUS_ORDER (worst first), plus a sentinel for a
# decision with no thesis_ref at all (decision_thesis_ref() returns None when
# no thesis was active). Kept local so the thesis-status cut stays pure and
# testable without requiring thesis_ledger to import successfully.
THESIS_STATUSES = ("BROKEN", "WARNING", "UNKNOWN", "STABLE", "IMPROVING",
                    "CONFIRMED", "NO_THESIS")


def thesis_status_of(decision):
    ref = decision.get("thesis_ref")
    if not ref:
        return "NO_THESIS"
    return ref.get("status") or "NO_THESIS"

# Mirrors corporate_actions.BREAKS_PER_SHARE. Kept as a local literal so the
# PURE classification function below never requires that sibling to import
# successfully; the CLI prefers the live module's set when it is available
# (see cmd_attach).
_BREAKS_PER_SHARE_FALLBACK = frozenset({
    "SPLIT", "REVERSE_SPLIT", "RIGHTS_ISSUE", "DIRECTED_ISSUE",
    "SET_OFF_OR_INKIND_ISSUE", "CANCELLATION", "REDEMPTION", "SPINOFF",
    "SHARE_ISSUE_OTHER", "CONVERTIBLE_CONVERSION", "WARRANT_OR_INCENTIVE_ISSUE",
    "NEW_SHARE_CLASS"})
_SPLIT_TYPES = frozenset({"SPLIT", "REVERSE_SPLIT"})


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_date(raw):
    """YYYY-MM-DD (or a longer ISO timestamp, first 10 chars) -> date, or None."""
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def add_months(d, months):
    """Calendar-month arithmetic, day clamped to the target month's length.

    31 Jan + 1 month -> 28/29 Feb, never 3 Mar. Stdlib only (no dateutil).
    """
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def horizon_target_date(as_of_date, horizon_key):
    return add_months(as_of_date, HORIZON_MONTHS[horizon_key])


def has_matured(as_of_date, horizon_key, today):
    return today >= horizon_target_date(as_of_date, horizon_key)


# ---------------------------------------------------------------------------
# Outcome attachment - PURE. bars/actions are handed in already fetched so
# this stays testable with synthetic data and never touches the network
# itself.
# ---------------------------------------------------------------------------

def compute_horizon_outcome(decision, horizon_key, bars, today,
                             tolerance_days=PRICE_MATCH_TOLERANCE_DAYS):
    """(status, outcome_or_None). status is one of:

      PENDING       - the horizon has not elapsed since as_of. Never attach.
      BAD_DECISION  - decision.as_of or decision.price.value is missing/bad.
      NO_PRICE_DATA - horizon elapsed but no bar landed within tolerance of
                      the target date - a data gap, not an outcome.
      OK            - outcome computed; see module docstring for the shape.

    bars: [{"date": "YYYY-MM-DD", "close": float, ...}, ...], any order, may
    contain close=None (skipped). Corporate-action, scenario-landing and
    supersession sub-objects are NOT filled in here - see
    classify_corporate_action_window / scenario_landing / classify_supersession,
    which the CLI composes in afterward once it has that context.
    """
    as_of_date = _parse_date(decision.get("as_of"))
    price = decision.get("price") or {}
    p0 = price.get("value")
    if as_of_date is None or p0 is None:
        return "BAD_DECISION", None
    try:
        p0 = float(p0)
    except (TypeError, ValueError):
        return "BAD_DECISION", None
    if p0 <= 0:
        return "BAD_DECISION", None

    target = horizon_target_date(as_of_date, horizon_key)
    if today < target:
        return "PENDING", None

    usable = sorted(
        (b for b in (bars or []) if b.get("close") is not None and b.get("date")),
        key=lambda b: b["date"])
    window_end = (target + datetime.timedelta(days=tolerance_days)).isoformat()
    later = next((b for b in usable if target.isoformat() <= b["date"] <= window_end), None)
    if later is None:
        return "NO_PRICE_DATA", None

    p1 = later["close"]
    realized = p1 / p0 - 1.0
    later_date = _parse_date(later["date"])
    outcome = {
        "horizon": horizon_key,
        "target_date": target.isoformat(),
        "matured": True,
        "price_then": {"value": p0, "currency": price.get("currency"),
                       "as_of": price.get("as_of")},
        "price_later": {"value": float(p1), "currency": price.get("currency"),
                        "as_of": later["date"]},
        "elapsed_days": (later_date - as_of_date).days if later_date else None,
        "realized_return": realized,
    }
    return "OK", outcome


def classify_corporate_action_window(actions, breaks_per_share=None):
    """Did any share-count-breaking action fall in the measurement window,
    and does that leave realized_return trustworthy as computed?

    actions: [{"date": ..., "type": ..., "title": ...}, ...] already
    restricted to the window by the caller (e.g.
    corporate_actions.corporate_actions_between).
    """
    breaks = breaks_per_share if breaks_per_share else _BREAKS_PER_SHARE_FALLBACK
    relevant = [a for a in (actions or []) if a.get("type") in breaks]
    if not relevant:
        return {"checked": True, "found": [], "comparability": "CLEAN",
                "note": "no share-count-breaking action found in this window - "
                        "not proof none occurred, only that none matched"}

    found = [{"date": a.get("date"), "type": a.get("type"), "title": a.get("title")}
             for a in relevant]
    non_split = [a for a in relevant if a.get("type") not in _SPLIT_TYPES]
    if not non_split:
        return {"checked": True, "found": found, "comparability": "SPLIT_ADJUSTED_SERIES",
                "note": "a split fell in this window; nordic_shares.price_history is "
                        "measured back-adjusted for splits (corporate_actions.py), so "
                        "realized_return is valid as computed - do not re-adjust it"}

    types = ", ".join(sorted({a["type"] for a in non_split}))
    return {"checked": True, "found": found, "comparability": "CAUTION_OTHER_ACTION",
            "note": "%d action(s) in this window (%s) change the share count with "
                    "no clean price-series adjustment; realized_return may not "
                    "represent a clean shareholder return across it"
                    % (len(non_split), types)}


def scenario_landing(fair_value, price_currency, realized_price):
    """Where the realized price sits inside the bear/base/bull range stated
    at decision time - the most informative comparison available, because it
    grades the scenarios, not just the verdict's direction."""
    if not fair_value:
        return {"available": False, "landing": None,
                "detail": "no fair_value stored on this decision (a shallow "
                          "depth runs no scenarios)"}
    fv_ccy = fair_value.get("currency") or price_currency
    if price_currency and fv_ccy and fv_ccy != price_currency:
        return {"available": False, "landing": None,
                "detail": "fair_value is in %s but price is in %s; no FX "
                          "conversion is attempted" % (fv_ccy, price_currency)}
    try:
        bear = float(fair_value["bear"])
        lo = float(fair_value["base_low"])
        hi = float(fair_value["base_high"])
        bull = float(fair_value["bull"])
    except (KeyError, TypeError, ValueError):
        return {"available": False, "landing": None,
                "detail": "fair_value is incomplete"}
    if not (bear <= lo <= hi <= bull):
        return {"available": False, "landing": None,
                "detail": "fair_value range is not monotonic "
                          "(bear<=base_low<=base_high<=bull)"}

    p = float(realized_price)
    if p < bear:
        landing = "BELOW_BEAR"
    elif p < lo:
        landing = "BEAR_TO_BASE"
    elif p <= hi:
        landing = "WITHIN_BASE"
    elif p <= bull:
        landing = "BASE_TO_BULL"
    else:
        landing = "ABOVE_BULL"
    return {"available": True, "landing": landing, "detail": ""}


def classify_supersession(decision):
    """See the module docstring's SUPERSEDED-DECISION RULE.

    thesis_ledger.supersede_decision() is the ONLY thing that sets
    `superseded_by` - it means "explicitly withdrawn or corrected", never
    "merely older than a later decision". So the rule is unconditional: any
    decision carrying a `superseded_by` (a decision_id, or the literal string
    "WITHDRAWN") is excluded from hit-rate and expected-vs-realized scoring,
    regardless of whether its own horizon had already elapsed by the time it
    was withdrawn - it was retired rather than allowed to run its course.
    """
    sid = decision.get("superseded_by")
    if not sid:
        return {"is_superseded": False, "excluded_from_scoring": False,
                "superseded_by": None, "superseded_at": None, "note": ""}
    return {"is_superseded": True, "excluded_from_scoring": True,
            "superseded_by": sid, "superseded_at": decision.get("superseded_at"),
            "note": "this call was explicitly withdrawn or corrected "
                    "(superseded_by=%s); excluded from hit-rate and "
                    "expected-vs-realized scoring - a retired call was not "
                    "allowed to run its course" % sid}


def hit_direction(verdict):
    if verdict in BUY_SIDE_VERDICTS:
        return "UP"
    if verdict in SELL_SIDE_VERDICTS:
        return "DOWN"
    return None


def is_hit(verdict, realized_return):
    """True/False, or None when there is no clean call to grade (HOLD, or a
    dead-flat realized_return of exactly 0, which is not a signed outcome)."""
    direction = hit_direction(verdict)
    if direction is None or realized_return is None or realized_return == 0:
        return None
    return realized_return > 0 if direction == "UP" else realized_return < 0


def compare_expectation(implied_value, actual_value, tolerance=EXPECTATION_TOLERANCE):
    """PURE comparison of one implied_expectations entry's value against what
    was subsequently reported for the same metric. Neither value is fetched
    here - see _lookup_actual_metric for that (network, CLI-only)."""
    if implied_value is None or actual_value is None:
        return None
    delta = float(actual_value) - float(implied_value)
    if abs(delta) <= tolerance:
        verdict = "IN_LINE"
    elif delta > 0:
        verdict = "MARKET_UNDERESTIMATED"
    else:
        verdict = "MARKET_OVERESTIMATED"
    return {"implied_value": float(implied_value), "actual_value": float(actual_value),
            "delta": delta, "verdict": verdict}


# ---------------------------------------------------------------------------
# Aggregation - PURE. Operates only on decisions that already carry whatever
# outcome/expectations data --attach put there.
# ---------------------------------------------------------------------------

def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def stat_expected_vs_realized(rows, min_n):
    """rows: [(expected_return, realized_return), ...]."""
    n = len(rows)
    if n < min_n:
        return {"n": n, "insufficient": True, "mean_expected": None, "mean_realized": None}
    return {"n": n, "insufficient": False,
            "mean_expected": _mean([r[0] for r in rows]),
            "mean_realized": _mean([r[1] for r in rows])}


def stat_hit_rate(hits, min_n):
    """hits: [bool, ...] (already filtered to decisions with a clean call)."""
    n = len(hits)
    if n < min_n:
        return {"n": n, "insufficient": True, "hit_count": None, "rate": None}
    hc = sum(1 for h in hits if h)
    return {"n": n, "insufficient": False, "hit_count": hc, "rate": hc / n}


def stat_scenario_landing(landings, min_n):
    n = len(landings)
    if n < min_n:
        return {"n": n, "insufficient": True, "counts": None}
    counts = {}
    for landing in landings:
        counts[landing] = counts.get(landing, 0) + 1
    return {"n": n, "insufficient": False, "counts": counts}


def stat_expectation_calibration(entries, min_n):
    """entries: comparison dicts from compare_expectation (verdict, delta)."""
    n = len(entries)
    if n < min_n:
        return {"n": n, "insufficient": True, "counts": None, "mean_delta": None}
    counts = {}
    for e in entries:
        counts[e["verdict"]] = counts.get(e["verdict"], 0) + 1
    return {"n": n, "insufficient": False, "counts": counts,
            "mean_delta": _mean([e["delta"] for e in entries])}


def eligible_rows(decisions, horizon_key):
    """(decision, horizon_outcome) pairs that matured for this horizon and
    are not excluded by the superseded-decision rule (classify_supersession)."""
    out = []
    for d in decisions:
        outcome = (d.get("outcome") or {}).get(horizon_key)
        if not outcome or not outcome.get("matured"):
            continue
        sup = outcome.get("superseded") or {}
        if sup.get("excluded_from_scoring"):
            continue
        out.append((d, outcome))
    return out


def build_calibration_report(decisions, horizon_key, min_n=DEFAULT_MIN_N):
    """The whole descriptive report for one horizon, grouped by producer and
    never pooled across it. PURE - decisions must already carry `outcome`."""
    by_producer = {}
    for d in decisions:
        by_producer.setdefault(d.get("producer") or "UNKNOWN", []).append(d)

    producers_out = {}
    for producer, prod_decisions in by_producer.items():
        rows = eligible_rows(prod_decisions, horizon_key)
        n_excluded = sum(
            1 for d in prod_decisions
            if ((d.get("outcome") or {}).get(horizon_key) or {})
               .get("superseded", {}).get("excluded_from_scoring"))

        def er_rows(filtered):
            return [(d.get("expected_return"), o["realized_return"])
                    for d, o in filtered if d.get("expected_return") is not None]

        by_conviction = {c: stat_expected_vs_realized(
            er_rows([(d, o) for d, o in rows if d.get("conviction") == c]), min_n)
            for c in decision_record.CONVICTIONS}
        by_verdict = {v: stat_expected_vs_realized(
            er_rows([(d, o) for d, o in rows if d.get("verdict") == v]), min_n)
            for v in decision_record.VERDICTS}
        by_thesis_status = {s: stat_expected_vs_realized(
            er_rows([(d, o) for d, o in rows if thesis_status_of(d) == s]), min_n)
            for s in THESIS_STATUSES}

        hits = [is_hit(d.get("verdict"), o.get("realized_return")) for d, o in rows]
        hits = [h for h in hits if h is not None]
        hit_rate = stat_hit_rate(hits, min_n)

        landings = [o["scenario"]["landing"] for d, o in rows
                    if (o.get("scenario") or {}).get("available")]
        scenario_stats = stat_scenario_landing(landings, min_n)

        expect_entries = []
        for d in prod_decisions:
            exp = (d.get("outcome") or {}).get("expectations") or {}
            for e in exp.get("entries") or []:
                if e.get("verdict"):
                    expect_entries.append(e)
        expectation_stats = stat_expectation_calibration(expect_entries, min_n)

        producers_out[producer] = {
            "n_decisions_total": len(prod_decisions),
            "n_scored": len(rows),
            "n_excluded_superseded": n_excluded,
            "by_conviction": by_conviction,
            "by_verdict": by_verdict,
            "by_thesis_status": by_thesis_status,
            "hit_rate": hit_rate,
            "scenario_landing": scenario_stats,
            "expectation_calibration": expectation_stats,
        }

    return {"horizon": horizon_key, "min_n": min_n, "producers": producers_out}


CAVEATS = (
    "Forward-only track record, not a backtest: this answers 'how did stored "
    "calls read later', never 'what would this system have called in the "
    "past' - see this module's docstring and references/data-quality.md.",
    "Decisions are not a random sample of the market: they are the names "
    "someone chose to ask about, so a good hit rate here says nothing about "
    "skill against an unscreened universe.",
    "Grouped by producer and never pooled: /analyze (a chosen name) and "
    "/screen (a systematic sweep) have different selection processes.",
    "A decision explicitly superseded or withdrawn is excluded from hit-rate "
    "and expected-vs-realized scoring regardless of when that happened - it "
    "was retired rather than allowed to run its course (see "
    "'n_excluded_superseded'); a company may still hold several "
    "NON-superseded decisions over time and each is scored as its own dated "
    "call, never merged or discarded for being older.",
    "realized_return is a PRICE return, not a total return: dividends inside "
    "the measurement window are not included and are not verified here.",
    "Descriptive only: nothing in this report adjusts a score, a conviction "
    "cap or a screen threshold.",
)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _fmt_pct(x, places=1):
    return "%+.*f%%" % (places, 100.0 * x) if x is not None else "n/a"


def _fmt_er(stat):
    if stat["insufficient"]:
        return "n=%-3d INSUFFICIENT SAMPLE" % stat["n"]
    return "n=%-3d exp %s  real %s" % (stat["n"], _fmt_pct(stat["mean_expected"]),
                                        _fmt_pct(stat["mean_realized"]))


def _fmt_hit(stat):
    if stat["insufficient"]:
        return "n=%-3d INSUFFICIENT SAMPLE" % stat["n"]
    return "n=%-3d %d/%d hit (%s)" % (stat["n"], stat["hit_count"], stat["n"],
                                       _fmt_pct(stat["rate"], 0))


def _fmt_counts(stat):
    if stat["insufficient"]:
        return "n=%-3d INSUFFICIENT SAMPLE" % stat["n"]
    parts = ", ".join("%s=%d" % (k, v) for k, v in sorted(stat["counts"].items()))
    return "n=%-3d %s" % (stat["n"], parts or "(none)")


def render_text(report, out=None):
    p = print if out is None else (lambda s="": print(s, file=out))
    p("CALIBRATION -- %s horizon (min n=%d)" % (report["horizon"], report["min_n"]))
    for producer in decision_record.PRODUCERS:
        prod = report["producers"].get(producer)
        if not prod:
            continue
        p()
        p("%s -- %d decision(s), %d scored, %d excluded (withdrawn/superseded)"
          % (producer.upper(), prod["n_decisions_total"], prod["n_scored"],
             prod["n_excluded_superseded"]))
        p("  expected vs realized, by conviction:")
        for c in decision_record.CONVICTIONS:
            p("    %-10s %s" % (c, _fmt_er(prod["by_conviction"][c])))
        p("  expected vs realized, by verdict:")
        for v in decision_record.VERDICTS:
            p("    %-12s %s" % (v, _fmt_er(prod["by_verdict"][v])))
        p("  expected vs realized, by thesis status at decision time:")
        for s in THESIS_STATUSES:
            p("    %-10s %s" % (s, _fmt_er(prod["by_thesis_status"][s])))
        p("  hit rate (BUY-side hit = return>0, SELL-side hit = return<0):")
        p("    %s" % _fmt_hit(prod["hit_rate"]))
        p("  realized price vs the bear/base/bull range stated at the time:")
        _print_wrapped(p, "    ", _fmt_counts(prod["scenario_landing"]))
        p("  expectation calibration (implied vs subsequently reported):")
        _print_wrapped(p, "    ", _fmt_counts(prod["expectation_calibration"]))
    p()
    for c in CAVEATS:
        for line in _wrap("- " + c, 86):
            p("  " + line)


def _print_wrapped(p, indent, text, width=88):
    """Wraps arbitrarily-long counts (many buckets, wide numbers) so the
    88-column limit holds regardless of how many landing/verdict categories a
    bucket happens to carry - a fixed format string cannot guarantee that on
    its own once counts run into 3+ digits across 5 categories."""
    for line in _wrap(text, width - len(indent)):
        p(indent + line)


def _wrap(text, width):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if len(cand) > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


def _collect_decisions(tl, company, producer, since):
    keys = []
    if company:
        try:
            identity = tl.resolve_identity(company)
            keys = [tl.ledger_key(identity)]
        except Exception as e:                          # noqa: BLE001
            print("could not resolve %r: %s" % (company, e), file=sys.stderr)
            return []
    else:
        idx = tl.read_index()
        keys = list((idx or {}).get("companies", {}).keys())

    out = []
    for key in keys:
        led = tl.read_ledger(key)
        if not led:
            continue
        try:
            decs = tl.list_decisions(led)
        except Exception as e:                           # noqa: BLE001
            print("(calibration: list_decisions failed for %s - %s)" % (key, e),
                  file=sys.stderr)
            continue
        for d in decs or []:
            if producer and d.get("producer") != producer:
                continue
            if since and (d.get("as_of") or "") < since:
                continue
            out.append(d)
    return out


def _fetch_bars(nordic, decision, as_of_date, target_date):
    identity = decision.get("identity") or {}
    needle = identity.get("ticker") or identity.get("name")
    if not needle:
        return None
    try:
        hits = nordic.search(needle)
        if not hits:
            return None
        root = nordic.root_symbol(hits[0]["symbol"])
        classes = [h for h in hits if nordic.root_symbol(h["symbol"]) == root]
        b_class = [c for c in classes if (c.get("symbol") or "").endswith(" B")]
        primary = b_class[0] if b_class else classes[0]
        to_date = target_date + datetime.timedelta(days=PRICE_MATCH_TOLERANCE_DAYS + 5)
        bars = nordic.price_history(primary["orderbookId"],
                                     as_of_date.isoformat(), to_date.isoformat())
    except (Exception, SystemExit) as e:                 # noqa: BLE001
        print("(calibration: price lookup failed for %r - %s)" % (needle, e),
              file=sys.stderr)
        return None
    return bars or None


def _attach_one_horizon(decision, horizon_key, today, nordic, corp):
    as_of_date = _parse_date(decision.get("as_of"))
    price = decision.get("price") or {}
    if as_of_date is None or price.get("value") is None:
        return "BAD_DECISION", None
    target = horizon_target_date(as_of_date, horizon_key)
    if today < target:
        return "PENDING", None
    if nordic is None:
        return "NO_PRICE_DATA", None
    bars = _fetch_bars(nordic, decision, as_of_date, target)
    if bars is None:
        return "NO_PRICE_DATA", None

    status, outcome = compute_horizon_outcome(decision, horizon_key, bars, today)
    if status != "OK":
        return status, None

    if corp is not None:
        try:
            name = (decision.get("identity") or {}).get("name") or \
                (decision.get("identity") or {}).get("ticker")
            actions = corp.corporate_actions_between(
                name, as_of_date.isoformat(), outcome["price_later"]["as_of"])
            breaks = getattr(corp, "BREAKS_PER_SHARE", None)
            outcome["corporate_actions"] = classify_corporate_action_window(actions, breaks)
        except (Exception, SystemExit) as e:              # noqa: BLE001
            outcome["corporate_actions"] = {
                "checked": False, "found": [], "comparability": "UNKNOWN",
                "note": "corporate action check failed: %s" % e}
    else:
        outcome["corporate_actions"] = {
            "checked": False, "found": [], "comparability": "UNKNOWN",
            "note": "corporate_actions.py was not available"}

    fv = decision.get("fair_value")
    outcome["scenario"] = scenario_landing(fv, price.get("currency"),
                                            outcome["price_later"]["value"])

    outcome["superseded"] = classify_supersession(decision)
    outcome["attached_at"] = _now_iso()
    return "OK", outcome


def _lookup_actual_metric(tl, lei, mid, since_date):
    try:
        bundle = tl.fetch_esef(lei)
        if not bundle:
            return None, None, "no ESEF filings found for this issuer"
        raw, basis = tl.raw_facts(bundle, bundle.get("currency"))
        series, note = tl.metric_series(mid, raw, basis, bundle.get("currency"))
        if not series:
            return None, None, note or "metric not computable from filed data"
        candidates = sorted(p for p in series if not since_date or p > since_date)
        if not candidates:
            return None, None, "no filing period ends after the decision date yet"
        period = candidates[0]
        return series[period].value, period, None
    except (Exception, SystemExit) as e:                  # noqa: BLE001
        return None, None, "fundamentals lookup failed: %s" % e


def _attach_expectations(decision, tl):
    entries = decision.get("implied_expectations")
    if not entries:
        return None
    if isinstance(entries, dict):
        entries = [entries]
    identity = decision.get("identity") or {}
    lei = identity.get("lei")
    out_entries = []
    for entry in entries:
        metric_text = entry.get("metric")
        mid = None
        try:
            mid = tl.resolve_metric(metric_text) if metric_text else None
        except Exception:                                 # noqa: BLE001
            mid = None
        if not mid:
            out_entries.append({"metric": metric_text, "implied_value": entry.get("value"),
                                "actual_value": None,
                                "note": "metric not recognised by thesis_ledger's vocabulary"})
            continue
        if not lei:
            out_entries.append({"metric": mid, "implied_value": entry.get("value"),
                                "actual_value": None,
                                "note": "no LEI on this decision's identity"})
            continue
        actual_value, period_end, note = _lookup_actual_metric(
            tl, lei, mid, decision.get("as_of"))
        if actual_value is None:
            out_entries.append({"metric": mid, "implied_value": entry.get("value"),
                                "actual_value": None, "note": note or "not yet reported"})
            continue
        cmp_ = compare_expectation(entry.get("value"), actual_value)
        if cmp_ is None:
            continue
        cmp_["metric"] = mid
        cmp_["actual_period_end"] = period_end
        out_entries.append(cmp_)
    return {"checked_at": _now_iso(), "entries": out_entries}


def cmd_attach(args, tl):
    nordic = _soft_load("nordic_shares")
    corp = _soft_load("corporate_actions")
    horizons = [args.horizon] if args.horizon else list(ALL_HORIZONS)
    today = datetime.date.today()

    if args.company:
        try:
            identity = tl.resolve_identity(args.company)
            keys = [tl.ledger_key(identity)]
        except Exception as e:                            # noqa: BLE001
            print("could not resolve %r: %s" % (args.company, e))
            return 1
    else:
        idx = tl.read_index()
        keys = list((idx or {}).get("companies", {}).keys())

    n_attached = n_pending = n_skipped = 0
    for key in keys:
        led = tl.read_ledger(key)
        if not led:
            continue
        try:
            decisions = tl.list_decisions(led)
        except Exception:                                 # noqa: BLE001
            continue
        changed = False
        for dec in decisions or []:
            outcome = dec.get("outcome")
            outcome = dict(outcome) if isinstance(outcome, dict) else {}
            for h in horizons:
                if outcome.get(h) is not None:
                    continue
                status, result = _attach_one_horizon(dec, h, today, nordic, corp)
                if status == "OK":
                    outcome[h] = result
                    changed = True
                    n_attached += 1
                elif status == "PENDING":
                    n_pending += 1
                else:
                    n_skipped += 1
            if dec.get("implied_expectations") and not outcome.get("expectations"):
                exp = _attach_expectations(dec, tl)
                if exp:
                    outcome["expectations"] = exp
                    changed = True
            dec["outcome"] = outcome
        if changed:
            tl.save_ledger(led)
    print("attach: %d horizon-outcome(s) attached, %d still pending, "
          "%d skipped (no price data)" % (n_attached, n_pending, n_skipped))
    return 0


def cmd_report(args, tl):
    decisions = _collect_decisions(tl, args.company, args.producer, args.since)
    if not decisions:
        print("no decisions stored yet.")
        return 0

    horizons = [args.horizon] if args.horizon else list(ALL_HORIZONS)
    reports = {h: build_calibration_report(decisions, h, args.min_n) for h in horizons}
    any_scored = any(p["n_scored"] > 0
                      for r in reports.values() for p in r["producers"].values())
    if not any_scored:
        dated = [d.get("as_of") for d in decisions if d.get("as_of")]
        earliest = min(dated) if dated else "unknown"
        print("no matured decisions yet; %d stored, earliest %s" % (len(decisions), earliest))
        print("run --attach once a decision's horizon (3m/6m/12m) has elapsed.")
        return 0

    if args.json:
        print(json.dumps({"reports": reports, "caveats": list(CAVEATS)},
                         indent=2, ensure_ascii=False))
    else:
        for h in horizons:
            render_text(reports[h])
            print()
    return 0


def selftest():
    import contextlib
    import io

    fails = []

    def check(label, cond):
        if not cond:
            fails.append(label)

    # add_months: day clamping across a leap boundary.
    check("31 Jan + 1 month should clamp to the shortest February",
          add_months(datetime.date(2027, 1, 31), 1) in
          (datetime.date(2027, 2, 28), datetime.date(2027, 2, 29)))
    check("31 May + 3 months should land on 31 Aug",
          add_months(datetime.date(2026, 5, 31), 3) == datetime.date(2026, 8, 31))

    # compute_horizon_outcome: pending vs matured vs realized arithmetic.
    dec = {"as_of": "2026-01-01", "price": {"value": 100.0, "currency": "SEK", "as_of": "2026-01-01"}}
    status, out = compute_horizon_outcome(dec, "3m", [], datetime.date(2026, 2, 1))
    check("a horizon that has not elapsed must be PENDING, no outcome",
          status == "PENDING" and out is None)

    bars = [{"date": "2026-04-01", "close": 110.0}, {"date": "2026-04-03", "close": 115.0}]
    status, out = compute_horizon_outcome(dec, "3m", bars, datetime.date(2026, 4, 5))
    check("matured horizon with a bar in tolerance should be OK", status == "OK")
    check("realized_return should be exact arithmetic (110/100-1=0.10)",
          out is not None and abs(out["realized_return"] - 0.10) < 1e-9)

    status, out = compute_horizon_outcome(dec, "3m", [], datetime.date(2026, 4, 5))
    check("matured horizon with no price bar must refuse, not return 0.0",
          status == "NO_PRICE_DATA" and out is None)

    # Minimum-sample refusal: must refuse (INSUFFICIENT SAMPLE), never 0.0.
    few = stat_hit_rate([True, True, False], min_n=20)
    check("a bucket below min_n must be marked insufficient", few["insufficient"] is True)
    check("an insufficient bucket must carry no numeric rate", few["rate"] is None)
    enough = stat_hit_rate([True] * 15 + [False] * 5, min_n=20)
    check("a bucket at/above min_n must report a real rate",
          enough["insufficient"] is False and abs(enough["rate"] - 0.75) < 1e-9)

    # Hit-rate direction: BUY hits on a rise, SELL hits on a fall.
    check("BUY with a positive return is a hit", is_hit("BUY", 0.05) is True)
    check("BUY with a negative return is a miss", is_hit("BUY", -0.05) is False)
    check("SELL with a negative return is a hit", is_hit("SELL", -0.05) is True)
    check("SELL with a positive return is a miss", is_hit("SELL", 0.05) is False)
    check("HOLD has no clean call to grade", is_hit("HOLD", 0.05) is None)
    check("a flat 0.0 return is inconclusive, not a miss", is_hit("BUY", 0.0) is None)

    # Scenario landing.
    fv = {"bear": 100, "base_low": 150, "base_high": 180, "bull": 220, "currency": "SEK"}
    check("below bear lands BELOW_BEAR",
          scenario_landing(fv, "SEK", 90)["landing"] == "BELOW_BEAR")
    check("inside the base range lands WITHIN_BASE",
          scenario_landing(fv, "SEK", 165)["landing"] == "WITHIN_BASE")
    check("above bull lands ABOVE_BULL",
          scenario_landing(fv, "SEK", 300)["landing"] == "ABOVE_BULL")
    check("a currency mismatch refuses rather than silently compares",
          scenario_landing({"bear": 1, "base_low": 2, "base_high": 3, "bull": 4,
                            "currency": "EUR"}, "SEK", 2)["available"] is False)

    # Corporate action classification.
    clean = classify_corporate_action_window([])
    check("no actions in window is CLEAN", clean["comparability"] == "CLEAN")
    split_only = classify_corporate_action_window(
        [{"date": "2026-06-01", "type": "SPLIT", "title": "2:1 split"}])
    check("a pure split is SPLIT_ADJUSTED_SERIES, not a caution",
          split_only["comparability"] == "SPLIT_ADJUSTED_SERIES")
    rights = classify_corporate_action_window(
        [{"date": "2026-06-01", "type": "RIGHTS_ISSUE", "title": "rights issue"}])
    check("a rights issue must be flagged as a caution",
          rights["comparability"] == "CAUTION_OTHER_ACTION")

    # Supersession rule: unconditional exclusion once explicitly superseded,
    # regardless of timing relative to the horizon - set by thesis_ledger's
    # supersede_decision(), never inferred from dates here.
    replaced = classify_supersession({"superseded_by": "LEI-X:2026-05-01T00:00:00Z"})
    check("a decision superseded by a named replacement is excluded",
          replaced["is_superseded"] is True and replaced["excluded_from_scoring"] is True)
    withdrawn = classify_supersession({"superseded_by": "WITHDRAWN",
                                        "superseded_at": "2026-09-01T00:00:00Z"})
    check("a decision withdrawn with no replacement is excluded the same way",
          withdrawn["is_superseded"] is True and withdrawn["excluded_from_scoring"] is True)
    none_sup = classify_supersession({"superseded_by": None})
    check("a decision never superseded is never excluded",
          none_sup["is_superseded"] is False and none_sup["excluded_from_scoring"] is False)

    # A company may hold several non-superseded decisions over time; each is
    # its own dated observation and neither is dropped for being "older".
    older = {"producer": "analyze", "conviction": "MEDIUM", "verdict": "BUY",
            "expected_return": 0.05, "as_of": "2026-01-01", "superseded_by": None,
            "outcome": {"3m": {"matured": True, "realized_return": 0.04,
                               "superseded": {"excluded_from_scoring": False},
                               "scenario": {"available": False}}}}
    newer = {"producer": "analyze", "conviction": "MEDIUM", "verdict": "BUY",
            "expected_return": 0.05, "as_of": "2026-06-01", "superseded_by": None,
            "outcome": {"3m": {"matured": True, "realized_return": 0.09,
                               "superseded": {"excluded_from_scoring": False},
                               "scenario": {"available": False}}}}
    rep_both = build_calibration_report([older, newer], "3m", min_n=1)
    check("two non-superseded decisions on the same producer/conviction "
          "should both be scored, not merged or deduplicated",
          rep_both["producers"]["analyze"]["by_conviction"]["MEDIUM"]["n"] == 2)

    # Expectation calibration: present and absent.
    cmp_ = compare_expectation(0.04, 0.062)
    check("expectation calibration should read MARKET_UNDERESTIMATED when "
          "actual growth beat what was priced in",
          cmp_["verdict"] == "MARKET_UNDERESTIMATED")
    cmp_over = compare_expectation(0.08, 0.02)
    check("expectation calibration should read MARKET_OVERESTIMATED when "
          "actual growth fell well short",
          cmp_over["verdict"] == "MARKET_OVERESTIMATED")
    check("absent implied/actual values must not fabricate a comparison",
          compare_expectation(None, 0.05) is None)
    in_line = compare_expectation(0.04, 0.045)
    check("a small delta inside tolerance should read IN_LINE",
          in_line["verdict"] == "IN_LINE")

    # producer pooling: analyze and screen decisions must not merge.
    d_analyze = {"producer": "analyze", "conviction": "MEDIUM", "verdict": "BUY",
                "expected_return": 0.10, "as_of": "2026-01-01",
                "outcome": {"3m": {"matured": True, "realized_return": 0.12,
                                   "superseded": {"excluded_from_scoring": False},
                                   "scenario": {"available": False}}}}
    d_screen = {"producer": "screen", "conviction": "MEDIUM", "verdict": "BUY",
               "expected_return": 0.20, "as_of": "2026-01-01",
               "outcome": {"3m": {"matured": True, "realized_return": -0.05,
                                  "superseded": {"excluded_from_scoring": False},
                                  "scenario": {"available": False}}}}
    report = build_calibration_report([d_analyze, d_screen], "3m", min_n=1)
    check("analyze and screen must appear as separate producer buckets",
          set(report["producers"].keys()) == {"analyze", "screen"})
    check("the analyze bucket must not see the screen decision's return",
          abs(report["producers"]["analyze"]["by_conviction"]["MEDIUM"]["mean_realized"]
              - 0.12) < 1e-9)
    check("the screen bucket must not see the analyze decision's return",
          abs(report["producers"]["screen"]["by_conviction"]["MEDIUM"]["mean_realized"]
              - (-0.05)) < 1e-9)

    # Thesis-status cut: calls made against a WARNING thesis vs no thesis.
    check("thesis_status_of reads the worst-standing active thesis's status",
          thesis_status_of({"thesis_ref": {"status": "WARNING"}}) == "WARNING")
    check("a decision with no thesis_ref falls into the NO_THESIS bucket",
          thesis_status_of({}) == "NO_THESIS")
    d_warning = dict(d_analyze, thesis_ref={"status": "WARNING"})
    rep_thesis = build_calibration_report([d_warning, d_screen], "3m", min_n=1)
    check("a WARNING-thesis decision must land in its own thesis-status bucket",
          rep_thesis["producers"]["analyze"]["by_thesis_status"]["WARNING"]["n"] == 1)
    check("a decision with no thesis carries into NO_THESIS, not WARNING",
          rep_thesis["producers"]["screen"]["by_thesis_status"]["NO_THESIS"]["n"] == 1)

    # Rendered text must fit the house 88-column limit.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        render_text(build_calibration_report([d_warning, d_screen], "3m", min_n=1))
    check("no rendered line may exceed 88 columns",
          all(len(l) <= 88 for l in buf.getvalue().splitlines()))

    if fails:
        print("SELFTEST FAILED (%d)" % len(fails))
        for f in fails:
            print("  - %s" % f)
        return 1
    print("calibration selftest: OK")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("company", nargs="?",
                    help="restrict to one company (default: every stored decision)")
    ap.add_argument("--attach", action="store_true",
                    help="attach outcomes to decisions whose horizon has elapsed")
    ap.add_argument("--report", action="store_true",
                    help="print the calibration report")
    ap.add_argument("--horizon", choices=ALL_HORIZONS,
                    help="restrict to one named horizon (default: all three)")
    ap.add_argument("--min-n", type=int, default=DEFAULT_MIN_N,
                    help="minimum bucket size to report a number "
                         "(default: %d; see module docstring)" % DEFAULT_MIN_N)
    ap.add_argument("--producer", choices=decision_record.PRODUCERS,
                    help="restrict the report to one producer")
    ap.add_argument("--since", metavar="YYYY-MM-DD",
                    help="restrict to decisions as_of this date or later")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if not args.attach and not args.report:
        ap.error("give --attach or --report (or --selftest)")

    tl = _soft_load("thesis_ledger")
    if tl is None or not hasattr(tl, "list_decisions"):
        print("DATA NOT AVAILABLE: the decisions store (thesis_ledger's "
              "list_decisions/latest_decision/get_decision) is not available "
              "yet. There is nothing to calibrate until decisions are being "
              "recorded and stored - see decision_record.py.")
        return 1

    if args.attach:
        return cmd_attach(args, tl)
    return cmd_report(args, tl)


if __name__ == "__main__":
    sys.exit(main())
