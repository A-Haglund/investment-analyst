#!/usr/bin/env python3
"""Earnings quality: does reported profit convert to actual cash. (spec §10/§11)

Reported profit and cash generation diverge for mundane reasons (revenue
recognised ahead of collection on a genuine growth ramp) and for serious ones
(channel-stuffing, aggressive capitalisation, working-capital games). The
divergence itself is measurable from free tagged data; the *reason* for it is
not, so this script computes the divergence and states what would resolve it
rather than guessing at intent.

Every number this script prints is a finfact.FinancialFact: sourced, dated
where the source allows it, and confidence-scored. A derived ratio's
confidence is the MINIMUM of its inputs' confidence, never their average and
never asserted independently - see combine_conf() below.

RATIOS COMPUTED (spec §10), each over 1/3/5 years where the data allows:

    CFO / Net Income                    cash conversion of the bottom line
    FCF / Net Income                    cash conversion after reinvestment
    FCF / EBIT                          cash conversion of operating profit
    CFO / EBIT                          cash conversion before capex
    Working capital / Revenue           capital tied up in running the business
    Receivables growth vs revenue growth
    Inventory growth vs revenue growth
    Capex / Revenue
    Accrual ratio  (NI - CFO) / average total assets

METHODOLOGY NOTE on multi-year windows: a flow-over-flow ratio (CFO/NI,
FCF/NI, FCF/EBIT, CFO/EBIT, capex/revenue) is aggregated as SUM(numerator over
the window) / SUM(denominator over the window), not the average of the yearly
ratios. This is the "cumulative accrual gap" method fundamentals.md prescribes
("sum net income and sum CFO over five years") and it is far less distorted by
a single near-zero-denominator year than averaging yearly ratios would be. A
ratio that mixes a stock (working capital, total assets) with a flow (revenue,
NI-CFO) is instead computed per year and then AVERAGED across the window,
because summing a balance-sheet figure across years is not meaningful.

THRESHOLDS - each one is lifted from, or reasoned by direct analogy with,
../references/red-flags-and-smallcap.md (Part 1 §2, "Cash and earnings
quality" - the file this plugin already uses for the same judgement elsewhere,
so this script does not invent a second, conflicting set):

  * FCF/NI < 60% averaged over THREE years -> weak conversion. Directly flag 5
    of the reference file. "Three years, not one: a single year below 60% is
    usually working capital absorbed by growth and is not a finding."
  * Negative FCF in 3 of the last 5 years -> persistently negative FCF.
    Directly flag 6 of the reference file.
  * Receivables growth exceeding revenue growth by >10 percentage points in
    TWO CONSECUTIVE years -> flag. Directly flag 7 of the reference file
    ("a single quarter's gap... is routinely produced by one large invoice").
    SUPPLEMENTARY, not in the reference file: a >40 percentage-point gap in a
    SINGLE year also fires on its own. A gap of that size (NVDA's receivables
    grew 96% against a much smaller revenue increase in FY2024) is far outside
    the invoice-timing noise the "two consecutive years" rule exists to
    filter out, and the reference file itself uses exactly this two-tier
    pattern elsewhere (flag 2: "more than 5%... more than 15% is material at
    any size").
  * Inventory growth exceeding revenue growth by >10 percentage points for two
    consecutive years, where revenue growth is also decelerating or negative
    -> flag. Reference flag 8 combines a DIO test with a demand-deceleration
    test; DIO needs cost_of_sales, which is "frequently untagged in Swedish
    ESEF filings" (sweden.md, and confirmed below). This script substitutes a
    receivables-style growth-rate comparison, computable from inventory and
    revenue alone, and keeps the reference's combined "AND decelerating"
    condition and its 10pp threshold by direct analogy. Same >40pp single-year
    supplementary trigger as above, for the same reason.
  * Unusually large working-capital movement: working-capital/revenue swings
    by more than 5 percentage points in one year. NOT in the reference file -
    this script's own heuristic, stated as such. Five points is roughly the
    scale of a full quarter's revenue shifting between balance-sheet and
    income-statement recognition for a typical industrial working-capital
    ratio (10-20% of revenue); it is offered as a screen for "look at this",
    not as a calibrated red line the way the 60% and 10pp figures are.

WORDING (fixed, matches the task spec, deliberately NOT the "RED FLAG" wording
of red-flags-and-smallcap.md so the two screens are not mistaken for one
another): `QUALITY WARNING — REQUIRES INVESTIGATION`. Never fraud, never
manipulation, never misconduct - an observation, its source, what it could
mean, and what would resolve it. Exactly data-quality.md's discipline, applied
to one specific question.

INPUTS AND THEIR LIMITS:
  esef_fundamentals.py   IFRS annual figures for Nordic/French issuers. ESEF
                         Phase 1 tags PRIMARY STATEMENTS ONLY: cost of sales,
                         SBC and some working-capital lines are frequently
                         absent. No filing-level publication date is carried
                         through by that script, so ESEF-sourced facts cannot
                         claim VERIFIED and carry no publication_date - this
                         is a real, stated limitation of the free source, not
                         a bug here.
  company_resolve.py     Identity, reporting currency and fiscal year end for
                         Nordic issuers (LEI, used to pull ESEF filings).
  finfact.py             FinancialFact / Verification / State / confidence.

Python 3 stdlib only. Free and keyless.

Coverage: European (Nordic/French ESEF) issuers only. A US ticker or CIK is
DATA NOT AVAILABLE here - see module note below.

Usage:
    python earnings_quality.py "Sandvik"
    python earnings_quality.py "Evolution AB" --years 5
    python earnings_quality.py "Addtech" --json
"""
import argparse
import datetime
import importlib.util
import json
import os
import statistics
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
NA = "DATA NOT AVAILABLE"


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


finfact = load("finfact")
esef = load("esef_fundamentals")
company_resolve = load("company_resolve")

FinancialFact = finfact.FinancialFact
Verification = finfact.Verification

NEEDED = ("revenue", "net_income", "operating_income", "cfo", "capex",
          "total_assets", "inventory", "receivables", "payables")

WINDOWS = (1, 3, 5)


# --------------------------------------------------------------------------
# Confidence combination (spec: "Derived ratios carry the confidence of their
# weakest input"). Never averaged, never taken from the stronger side.
# --------------------------------------------------------------------------

def combine_conf(fact, *inputs):
    fact.confidence = min([fact.confidence] + [i.confidence for i in inputs])
    return fact


def weakest_verification(*inputs):
    order = [Verification.CONFLICT, Verification.UNVERIFIED, Verification.INCOMPLETE,
             Verification.SINGLE_SOURCE, Verification.STALE, Verification.CROSS_CHECKED,
             Verification.VERIFIED]
    return min(inputs, key=lambda v: order.index(v)) if inputs else Verification.SINGLE_SOURCE


# --------------------------------------------------------------------------
# Fetch: ESEF (Nordic/French) into a common
# {metric: {period_end_iso: FinancialFact}} shape.
# --------------------------------------------------------------------------

def resolve_nordic(query, country=None):
    """Mirror company_resolve.py's main(), returning the record rather than
    printing it. Refuses (returns None) exactly where company_resolve.py
    would refuse - an ambiguous brand name is not silently guessed here
    either."""
    kind, needle = company_resolve.classify_query(query)
    entities = company_resolve.mfn_entities(needle)
    lines = company_resolve.nasdaq_lines(needle)
    if kind in ("lei", "orgnr") and not any(
            needle in ((e.get("leis") or []) + [r.split(":")[-1]
                                                for r in e.get("local_refs") or []])
            for e in entities):
        seed = (company_resolve.gleif(needle).get("legal_name") if kind == "lei"
                else company_resolve.gleif_by_orgnr(needle))
        if seed:
            entities += company_resolve.mfn_entities(seed)
            lines += company_resolve.nasdaq_lines(seed)

    cands = company_resolve.build_candidates(entities, lines)
    if country:
        want = country.upper()
        kept = [c for c in cands
                if any(i.startswith(want) for i in c.isins)
                or any((e or "").startswith(want)
                       for e in (c.mfn or {}).get("local_refs") or [])
                or any((l.get("isin") or "").startswith(want) for l in c.lines)]
        if kept:
            cands = kept
    listed = [c for c in cands if c.lines or c.symbols]
    if listed:
        cands = listed

    winner, reason, confidence, contenders = company_resolve.resolve_candidates(cands, kind, needle)
    if winner is None:
        return None, reason, contenders
    record = company_resolve.assemble(winner, reason, confidence)
    return record, reason, contenders


def fetch_esef(lei, years):
    """Merge annual concept values across enough ESEF filings to cover the
    requested history. Returns ({metric: {period: {val,unit,concept,filing}}},
    filings_used, error_or_None)."""
    filings = esef.list_filings(lei, limit=max(years, 3))
    if not filings:
        return {}, [], ("no ESEF filings indexed for LEI %s (Germany and "
                        "Ireland are never covered; a First North/Spotlight "
                        "issuer files no ESEF at all - see europe.md/"
                        "sweden.md, this is a fact about the venue, not the "
                        "company)" % lei)
    merged = {}
    for f in filings:
        try:
            doc = esef.get_json(esef.FILINGS_BASE + f["json_url"])
        except SystemExit as e:
            continue
        facts = esef.extract(doc)
        for metric, names in esef.CONCEPTS.items():
            if metric not in NEEDED:
                continue
            found = esef.pick(facts, names, metric in esef.DURATION)
            for period, (value, unit, concept) in found.items():
                merged.setdefault(metric, {}).setdefault(
                    period, {"val": value, "unit": unit, "concept": concept,
                             "filing": f["fxo_id"]})
    return merged, filings, None


def to_facts_esef(merged, currency):
    out = {}
    for metric, periods in merged.items():
        out[metric] = {}
        for period_end, info in periods.items():
            out[metric][period_end] = FinancialFact(
                metric=metric, value=info["val"], source="esef",
                period_end=period_end, unit="currency", currency=currency,
                source_detail="ESEF concept %s, filing %s" % (info["concept"], info["filing"]),
                verification=Verification.SINGLE_SOURCE,
                freshness_key="annual_financials")
    return out


# --------------------------------------------------------------------------
# Derivation helpers. Every one returns FinancialFact objects, never a bare
# float, and every one enforces "weakest input wins" via combine_conf().
# --------------------------------------------------------------------------

def per_year_ratio(name, num, den, unit="ratio"):
    out = {}
    for period in sorted(set(num) & set(den)):
        n, d = num[period], den[period]
        if not d.value:
            continue
        fact = FinancialFact(metric=name, value=n.value / d.value, source=n.source,
                             period_end=period, unit=unit,
                             source_detail="%s / %s" % (n.metric, d.metric),
                             verification=weakest_verification(n.verification, d.verification),
                             freshness_key="annual_financials")
        out[period] = combine_conf(fact, n, d)
    return out


def per_year_diff(name, a, b, sign=-1):
    """FCF = CFO - capex. capex is definitionally a cash outflow; the ESEF
    extension tags and the US GAAP Payments* tag do not share one sign
    convention, so the magnitude is what is subtracted."""
    out = {}
    for period in sorted(set(a) & set(b)):
        fa, fb = a[period], b[period]
        value = fa.value + sign * abs(fb.value)
        fact = FinancialFact(metric=name, value=value, source=fa.source,
                             period_end=period, unit="currency", currency=fa.currency,
                             source_detail="%s %s |%s|" % (fa.metric, "-" if sign < 0 else "+", fb.metric),
                             verification=weakest_verification(fa.verification, fb.verification),
                             freshness_key="annual_financials")
        out[period] = combine_conf(fact, fa, fb)
    return out


def per_year_wc(receivables, inventory, payables):
    """Operating working capital = receivables + inventory - payables.
    Excludes cash and interest-bearing debt deliberately: this is the capital
    tied up in the operating cycle, not the whole balance sheet."""
    out = {}
    for period in sorted(set(receivables) & set(inventory) & set(payables)):
        r, i, p = receivables[period], inventory[period], payables[period]
        value = r.value + i.value - p.value
        fact = FinancialFact(metric="operating_working_capital", value=value, source=r.source,
                             period_end=period, unit="currency", currency=r.currency,
                             source_detail="receivables + inventory - payables",
                             verification=weakest_verification(r.verification, i.verification, p.verification),
                             freshness_key="annual_financials")
        out[period] = combine_conf(fact, r, i, p)
    return out


def per_year_accrual(ni, cfo, ta):
    """(NI - CFO) / average total assets, average of the period-end and the
    prior period-end - the standard cash-flow-statement accrual ratio."""
    periods = sorted(ta)
    out = {}
    for i in range(1, len(periods)):
        p, p_prev = periods[i], periods[i - 1]
        if p not in ni or p not in cfo:
            continue
        avg_ta = (ta[p].value + ta[p_prev].value) / 2.0
        if not avg_ta:
            continue
        value = (ni[p].value - cfo[p].value) / avg_ta
        fact = FinancialFact(metric="accrual_ratio", value=value, source=ni[p].source,
                             period_end=p, unit="ratio",
                             source_detail="(NI - CFO) / avg(total_assets %s, %s)" % (p_prev, p),
                             verification=weakest_verification(ni[p].verification, cfo[p].verification,
                                                               ta[p].verification, ta[p_prev].verification),
                             freshness_key="annual_financials")
        out[p] = combine_conf(fact, ni[p], cfo[p], ta[p], ta[p_prev])
    return out


def window_sum_ratio(name, num, den, window):
    """SUM(num)/SUM(den) over the last `window` years. None if the window
    cannot be filled - never estimated or padded."""
    periods = sorted(set(num) & set(den))
    if len(periods) < window:
        return None
    periods = periods[-window:]
    num_sum = sum(num[p].value for p in periods)
    den_sum = sum(den[p].value for p in periods)
    if not den_sum:
        return None
    fact = FinancialFact(metric=name, value=num_sum / den_sum, source=num[periods[-1]].source,
                         period_end=periods[-1], unit="ratio",
                         source_detail="sum(%s)/sum(%s), %s..%s (%d yrs)"
                                       % (num[periods[-1]].metric, den[periods[-1]].metric,
                                          periods[0], periods[-1], window),
                         verification=weakest_verification(*(num[p].verification for p in periods),
                                                            *(den[p].verification for p in periods)),
                         freshness_key="annual_financials")
    return combine_conf(fact, *(num[p] for p in periods), *(den[p] for p in periods))


def window_avg_ratio(ratio_facts, window, name=None):
    """Mean of the per-year ratio over the last `window` years - used only
    where the ratio mixes a stock and a flow and summing would be meaningless
    (working capital/revenue, accrual ratio)."""
    periods = sorted(ratio_facts)
    if len(periods) < window:
        return None
    periods = periods[-window:]
    vals = [ratio_facts[p].value for p in periods]
    value = sum(vals) / len(vals)
    base = ratio_facts[periods[-1]]
    fact = FinancialFact(metric=name or (base.metric + "_avg"), value=value, source=base.source,
                         period_end=periods[-1], unit="ratio",
                         source_detail="mean of per-year ratio, %s..%s (%d yrs)"
                                       % (periods[0], periods[-1], window),
                         verification=weakest_verification(*(ratio_facts[p].verification for p in periods)),
                         freshness_key="annual_financials")
    return combine_conf(fact, *(ratio_facts[p] for p in periods))


def cagr_fact(facts, window, metric_name):
    """CAGR over `window` years (window=1 is plain YoY growth). None if the
    window years of history are not both present."""
    periods = sorted(facts)
    if len(periods) < window + 1:
        return None
    end, start = periods[-1], periods[-1 - window]
    v0, v1 = facts[start].value, facts[end].value
    if not v0 or v0 <= 0:
        return None
    value = (v1 / v0) ** (1.0 / window) - 1
    fact = FinancialFact(metric=metric_name, value=value, source=facts[end].source,
                         period_end=end, unit="percent",
                         source_detail="CAGR %d yr: %s (%.4g) -> %s (%.4g)" % (window, start, v0, end, v1),
                         verification=weakest_verification(facts[start].verification, facts[end].verification),
                         freshness_key="annual_financials")
    return combine_conf(fact, facts[start], facts[end])


def yoy_series(facts):
    """{period_end: (growth_rate, prev_period, prev_value, value)} for every
    consecutive pair available - the raw material for the two-consecutive-
    year flag tests."""
    periods = sorted(facts)
    out = {}
    for a, b in zip(periods, periods[1:]):
        va, vb = facts[a].value, facts[b].value
        if va:
            out[b] = (vb / va - 1, a, va, vb)
    return out


# --------------------------------------------------------------------------
# Flags. Fixed wording per the task spec - never fraud/manipulation language.
# --------------------------------------------------------------------------

QUALITY_WARNING = "QUALITY WARNING — REQUIRES INVESTIGATION"


def flag_block(observation, source, could_mean, would_resolve, status="UNRESOLVED"):
    return {"observation": observation, "source": source, "could_mean": could_mean,
            "would_resolve": would_resolve, "status": status}


def check_conversion(fcf_ni_windows, accrual_windows, cfo_ebit_windows, currency_label, ni_facts=None):
    w3 = fcf_ni_windows.get(3)
    w1 = fcf_ni_windows.get(1)
    w5 = fcf_ni_windows.get(5)
    reference = w3 or w5
    if reference is None:
        return None, "insufficient history for the 3-year FCF/NI window (need 3 consecutive years of CFO, capex and net income)"
    window_n = 3 if w3 is not None else 5
    if ni_facts:
        ni_periods = sorted(ni_facts)[-window_n:]
        if len(ni_periods) == window_n:
            ni_sum = sum(ni_facts[p].value for p in ni_periods)
            if ni_sum <= 0:
                return None, (
                    "the %d-year window ending %s summed to a net LOSS (%.0f%s), so FCF/NI "
                    "is not a meaningful ratio here - a non-positive denominator makes the "
                    "quotient uninterpretable as a cash-conversion rate. See the persistently"
                    "-negative-FCF check instead." % (window_n, reference.period_end,
                                                      ni_sum / 1e6, currency_label or ""))
    if reference.value >= 0.60:
        note = None
        if w1 is not None and w1.value < 0.60:
            note = ("FY%s alone shows weak conversion (FCF/NI %.0f%%), but the "
                    "%d-year figure does not (%.0f%%) - one weak year in a ramp "
                    "is not yet a pattern." % (w1.period_end, w1.value * 100,
                                               3 if fcf_ni_windows.get(3) else 5, reference.value * 100))
        return None, note
    acc = accrual_windows.get(3) or accrual_windows.get(5)
    cash_ebit = cfo_ebit_windows.get(3) or cfo_ebit_windows.get(5)
    extra = []
    if acc is not None:
        extra.append("the accrual ratio over the same window is %.1f%% of average total assets"
                     % (acc.value * 100))
    if cash_ebit is not None:
        extra.append("CFO/EBIT over the same window is %.0f%%" % (cash_ebit.value * 100))
    contra = ""
    if w1 is not None and w1.value >= 0.60:
        contra = (" FY%s alone looks fine (FCF/NI %.0f%%); the multi-year figure is what is "
                 "weak, and a single good year does not resolve a multi-year pattern." %
                 (w1.period_end, w1.value * 100))
    flag = flag_block(
        observation=("FCF/net income averaged %.0f%% over the last %d years (period ending %s), "
                    "below the 60%% conversion threshold this plugin uses elsewhere "
                    "(red-flags-and-smallcap.md flag 5).%s%s" %
                    (reference.value * 100, 3 if fcf_ni_windows.get(3) else 5,
                     reference.period_end, (" " + "; ".join(extra) + ".") if extra else "", contra)),
        source="CFO and capex from %s, %s" % (reference.source, reference.source_detail),
        could_mean=("revenue recognised ahead of collection; a genuine working-capital-heavy "
                   "growth ramp; aggressive capitalisation of costs that keeps net income up "
                   "while cash lags; or a one-off cash item (an insurance settlement, a "
                   "litigation payment) distorting one year of the window"),
        would_resolve=("the cash-flow statement note reconciling net income to operating cash "
                      "flow, and naming which working-capital or non-cash line absorbs the gap "
                      "(fundamentals.md's cumulative-accrual-gap procedure)"))
    return flag, None


def check_negative_fcf(fcf_facts):
    periods = sorted(fcf_facts)[-5:]
    if len(periods) < 3:
        return None, "fewer than 3 years of FCF available"
    negatives = [p for p in periods if fcf_facts[p].value < 0]
    if len(negatives) < 3:
        return None, None
    partial = " (only %d years of history were available, not the full 5)" % len(periods) if len(periods) < 5 else ""
    flag = flag_block(
        observation=("FCF was negative in %d of the last %d reported years%s: %s."
                    % (len(negatives), len(periods), partial,
                       ", ".join("%s (%.0f%s)" % (p, fcf_facts[p].value / 1e6, fcf_facts[p].currency or "")
                                for p in negatives))),
        source="CFO and capex, %s" % fcf_facts[periods[-1]].source_detail.split(",")[0],
        could_mean=("a capacity build or expansion programme still ramping toward its designed "
                   "return; structurally negative unit economics; or working capital consumed "
                   "by growth that has not yet turned into cash"),
        would_resolve=("a management-stated completion year and target return for any capex "
                      "programme (red-flags-and-smallcap.md flag 6's qualifier); absent that "
                      "disclosure, persistent negative FCF is read as capital consumption rather "
                      "than investment"))
    return flag, None


def check_receivables(revenue, receivables):
    gaps = yoy_series(receivables)
    rev_gaps = yoy_series(revenue)
    common = sorted(set(gaps) & set(rev_gaps))
    if len(common) < 1:
        return None, "insufficient overlapping history for receivables vs revenue growth"
    diffs = [(p, gaps[p][0] - rev_gaps[p][0], gaps[p][0], rev_gaps[p][0]) for p in common]
    two_consec = any(diffs[i][1] > 0.10 and diffs[i + 1][1] > 0.10 for i in range(len(diffs) - 1))
    single_extreme = [d for d in diffs if d[1] > 0.40]
    if not two_consec and not single_extreme:
        return None, None
    worst = max(diffs, key=lambda d: d[1])
    trigger = ("a single-year gap of %.0f percentage points (receivables grew %.0f%%, revenue "
              "grew %.0f%%, FY%s) - large enough to fire on its own even though it is one year, "
              "well beyond the two-consecutive-year noise the 10pp rule screens for"
              % (worst[1] * 100, worst[2] * 100, worst[3] * 100, worst[0])) if single_extreme else \
        ("two consecutive years with receivables growth exceeding revenue growth by more than "
         "10 percentage points")
    observation = "Receivables outgrew revenue materially: %s." % trigger
    if worst[0] != common[-1]:
        latest = diffs[-1]
        observation += (" That was FY%s, not the most recent year: the latest reading (FY%s) shows "
                        "a gap of %+.0fpp (receivables %+.0f%% vs revenue %+.0f%%), which %s "
                        "confirm the pattern has continued - a single unusual year is not yet a trend."
                        % (worst[0], latest[0], latest[1] * 100, latest[2] * 100, latest[3] * 100,
                           "does" if latest[1] > 0.10 else "does not"))
    flag = flag_block(
        observation=observation,
        source="receivables and revenue, %s" % revenue[common[-1]].source_detail.split(",")[0],
        could_mean=("looser credit terms extended to hold volume; a customer mix shift toward "
                   "slower-paying accounts; one large invoice outstanding at period end; or "
                   "revenue recognised ahead of cash collection"),
        would_resolve=("the receivables ageing note in the annual report and, where available, "
                      "management's characterisation of the change on the earnings call"))
    return flag, None


def check_inventory(revenue, inventory):
    inv_gaps = yoy_series(inventory)
    rev_gaps = yoy_series(revenue)
    common = sorted(set(inv_gaps) & set(rev_gaps))
    if not common:
        return None, "insufficient overlapping history for inventory vs revenue growth"
    diffs = [(p, inv_gaps[p][0] - rev_gaps[p][0], inv_gaps[p][0], rev_gaps[p][0]) for p in common]
    decel = lambda i: (diffs[i][3] < 0) or (i > 0 and diffs[i][3] < diffs[i - 1][3])
    two_consec = any(diffs[i][1] > 0.10 and diffs[i + 1][1] > 0.10 and decel(i + 1)
                     for i in range(len(diffs) - 1))
    single_extreme = [d for i, d in enumerate(diffs) if d[1] > 0.40 and decel(i)]
    if not two_consec and not single_extreme:
        return None, None
    worst = max(diffs, key=lambda d: d[1])
    observation = ("Inventory grew %.0f%% against revenue growth of %.0f%% in FY%s, and revenue "
                  "growth is decelerating over the same window - the combination this plugin's "
                  "flag 8 (by analogy; that flag uses DIO, here substituted with a growth-rate "
                  "comparison because cost of sales is frequently untagged) treats as the one "
                  "that precedes write-downs."
                  % (worst[2] * 100, worst[3] * 100, worst[0]))
    if worst[0] != common[-1]:
        latest = diffs[-1]
        observation += (" That was FY%s, not the most recent year: the latest reading (FY%s) shows "
                        "inventory growth of %+.0f%% against revenue growth of %+.0f%%, which %s "
                        "confirm the pattern has continued."
                        % (worst[0], latest[0], latest[2] * 100, latest[3] * 100,
                           "does" if (latest[1] > 0.10 and decel(len(diffs) - 1)) else "does not"))
    flag = flag_block(
        observation=observation,
        source="inventory and revenue, %s" % revenue[common[-1]].source_detail.split(",")[0],
        could_mean=("inventory built for demand that has not arrived; a deliberate stock build "
                   "ahead of a supply disruption or price increase; a product transition leaving "
                   "old stock unsold; or a genuine but temporary order-timing effect"),
        would_resolve=("the inventory note (ageing / obsolescence provision) in the annual "
                      "report, and management's commentary on channel inventory and sell-through"))
    return flag, None


def check_wc_swing(wc_to_rev):
    periods = sorted(wc_to_rev)
    if len(periods) < 2:
        return None, "fewer than 2 years of working-capital/revenue history"
    a, b = periods[-2], periods[-1]
    swing = wc_to_rev[b].value - wc_to_rev[a].value
    if abs(swing) <= 0.05:
        return None, None
    flag = flag_block(
        observation=("Operating working capital / revenue moved %.1f percentage points in one "
                    "year (%s: %.1f%% -> %s: %.1f%%). This threshold is this script's own "
                    "heuristic (see module docstring), not a figure carried from "
                    "red-flags-and-smallcap.md." %
                    (swing * 100, a, wc_to_rev[a].value * 100, b, wc_to_rev[b].value * 100)),
        source="receivables + inventory - payables, %s" % wc_to_rev[b].source_detail,
        could_mean=("a real change in payment terms with customers or suppliers; a large order "
                   "landing either side of the period end; a genuine step-change in the business "
                   "model; or the working-capital effects of an acquisition completing mid-year"),
        would_resolve=("the working-capital movement line in the cash-flow statement note, and "
                      "the notes on trade receivables/payables terms"))
    return flag, None


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def analyse(raw_facts, currency, label, source_name):
    """raw_facts: {metric: {period: FinancialFact}}. Returns the full result
    dict used by both the text and JSON renderers."""
    missing = [m for m in NEEDED if m not in raw_facts or not raw_facts[m]]
    revenue = raw_facts.get("revenue", {})
    ni = raw_facts.get("net_income", {})
    ebit = raw_facts.get("operating_income", {})
    cfo = raw_facts.get("cfo", {})
    capex = raw_facts.get("capex", {})
    ta = raw_facts.get("total_assets", {})
    inv = raw_facts.get("inventory", {})
    rec = raw_facts.get("receivables", {})
    pay = raw_facts.get("payables", {})

    fcf = per_year_diff("fcf", cfo, capex) if cfo and capex else {}
    wc = per_year_wc(rec, inv, pay) if (rec and inv and pay) else {}
    wc_to_rev = per_year_ratio("wc_to_revenue", wc, revenue) if (wc and revenue) else {}
    accrual = per_year_accrual(ni, cfo, ta) if (ni and cfo and ta) else {}

    windows = {}
    for key, num, den, kind in (
        ("cfo_ni", cfo, ni, "sum"),
        ("fcf_ni", fcf, ni, "sum"),
        ("fcf_ebit", fcf, ebit, "sum"),
        ("cfo_ebit", cfo, ebit, "sum"),
        ("capex_rev", capex, revenue, "sum_abs"),
    ):
        windows[key] = {}
        for w in WINDOWS:
            if kind == "sum_abs":
                num_abs = {p: FinancialFact(metric=num[p].metric, value=abs(num[p].value),
                                            source=num[p].source, period_end=p,
                                            source_detail=num[p].source_detail,
                                            verification=num[p].verification,
                                            freshness_key="annual_financials")
                          for p in num} if num else {}
                windows[key][w] = window_sum_ratio(key, num_abs, den, w) if num_abs and den else None
            else:
                windows[key][w] = window_sum_ratio(key, num, den, w) if num and den else None

    windows["wc_rev"] = {w: window_avg_ratio(wc_to_rev, w, name="wc_rev") for w in WINDOWS} if wc_to_rev else {w: None for w in WINDOWS}
    windows["accrual"] = {w: window_avg_ratio(accrual, w, name="accrual") for w in WINDOWS} if accrual else {w: None for w in WINDOWS}

    # Coverage: the actual overlapping years behind each ratio, so a DATA NOT
    # AVAILABLE window can be explained ("only 4 years overlap, need 5") rather
    # than left as a bare gap.
    coverage = {
        "cfo_ni": sorted(set(cfo) & set(ni)),
        "fcf_ni": sorted(set(fcf) & set(ni)),
        "fcf_ebit": sorted(set(fcf) & set(ebit)),
        "cfo_ebit": sorted(set(cfo) & set(ebit)),
        "capex_rev": sorted(set(capex) & set(revenue)),
        "wc_rev": sorted(wc_to_rev),
        "accrual": sorted(accrual),
    }
    growth_coverage = {
        "receivables": sorted(rec), "inventory": sorted(inv), "revenue": sorted(revenue),
    }

    growth = {"receivables": {}, "inventory": {}, "revenue_for_rec": {}, "revenue_for_inv": {}}
    for w in WINDOWS:
        growth["receivables"][w] = cagr_fact(rec, w, "receivables_cagr_%dy" % w) if rec else None
        growth["inventory"][w] = cagr_fact(inv, w, "inventory_cagr_%dy" % w) if inv else None
        growth["revenue_for_rec"][w] = cagr_fact(revenue, w, "revenue_cagr_%dy" % w) if revenue else None
        growth["revenue_for_inv"][w] = growth["revenue_for_rec"][w]

    flags = []
    f, note = check_conversion(windows["fcf_ni"], windows["accrual"], windows["cfo_ebit"], currency, ni)
    flags.append(("earnings not converting to cash", f, note))
    f, note = check_negative_fcf(fcf) if fcf else (None, "FCF not computable (missing CFO or capex)")
    flags.append(("persistently negative FCF", f, note))
    f, note = check_receivables(revenue, rec) if (revenue and rec) else (None, "receivables or revenue not available")
    flags.append(("receivables growing faster than revenue", f, note))
    f, note = check_inventory(revenue, inv) if (revenue and inv) else (None, "inventory or revenue not available")
    flags.append(("inventory accumulation into decelerating revenue", f, note))
    f, note = check_wc_swing(wc_to_rev) if wc_to_rev else (None, "working capital not computable (missing receivables, inventory or payables)")
    flags.append(("unusually large working-capital movement", f, note))

    all_facts = []
    for d in (revenue, ni, ebit, cfo, capex, ta, inv, rec, pay, fcf, wc, wc_to_rev, accrual):
        all_facts.extend(d.values())
    for wd in windows.values():
        all_facts.extend(v for v in wd.values() if v is not None)
    for gd in growth.values():
        all_facts.extend(v for v in gd.values() if v is not None)

    return {
        "label": label, "source": source_name, "currency": currency,
        "missing_base_concepts": missing,
        "periods_available": {m: sorted(raw_facts[m]) for m in raw_facts},
        "windows": windows, "growth": growth, "flags": flags,
        "coverage": coverage, "growth_coverage": growth_coverage,
        "n_facts": len(all_facts),
        "confidence": finfact.confidence_score(all_facts, required_metrics=NEEDED),
        "raw": {"revenue": revenue, "net_income": ni, "operating_income": ebit,
               "cfo": cfo, "capex": capex, "fcf": fcf, "total_assets": ta,
               "inventory": inv, "receivables": rec, "payables": pay,
               "operating_working_capital": wc},
    }


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def fmt_ratio(fact):
    if fact is None:
        return "n/a"
    if fact.unit == "percent":
        return "%+.1f%%" % (fact.value * 100)
    return "%.0f%%" % (fact.value * 100)


RATIO_ROWS = (("cfo_ni", "CFO / Net Income"), ("fcf_ni", "FCF / Net Income"),
             ("fcf_ebit", "FCF / EBIT"), ("cfo_ebit", "CFO / EBIT (cash conversion)"),
             ("wc_rev", "Working capital / Revenue"), ("capex_rev", "Capex / Revenue"),
             ("accrual", "Accrual ratio"))
GROWTH_ROWS = (("receivables", "Receivables growth (CAGR)"),
              ("revenue_for_rec", "  vs revenue growth (CAGR)"),
              ("inventory", "Inventory growth (CAGR)"),
              ("revenue_for_inv", "  vs revenue growth (CAGR)"))


def print_report(result):
    print("%s — earnings quality  |  source: %s  |  currency %s"
          % (result["label"], result["source"], result["currency"] or "unknown"))
    print()
    if result["missing_base_concepts"]:
        print("%s (base concepts not tagged in these filings): %s"
              % (NA, ", ".join(result["missing_base_concepts"])))
        print("These ratios cannot be computed at all; see the per-ratio table below.")
        print()

    w = 10
    header = "ratio".ljust(38) + "".join(("%dy" % y).rjust(w) for y in WINDOWS)
    print(header)
    print("-" * len(header))
    for key, lbl in RATIO_ROWS:
        row = result["windows"].get(key, {})
        line = lbl.ljust(38)
        for y in WINDOWS:
            line += fmt_ratio(row.get(y)).rjust(w)
        print(line)
    for key, lbl in GROWTH_ROWS:
        row = result["growth"].get(key, {})
        line = lbl.ljust(38)
        for y in WINDOWS:
            line += fmt_ratio(row.get(y)).rjust(w)
        print(line)

    gaps = []
    for key, lbl in RATIO_ROWS:
        have = result["coverage"].get(key, [])
        for y in WINDOWS:
            if result["windows"].get(key, {}).get(y) is None:
                gaps.append("%s %dy: %s overlapping years available (%s), need %d"
                            % (lbl.strip(), y, len(have), ", ".join(have) or "none", y))
    for key, lbl in (("receivables", "Receivables growth"), ("inventory", "Inventory growth")):
        have = result["growth_coverage"].get(key, [])
        rev_have = result["growth_coverage"].get("revenue", [])
        for y in WINDOWS:
            if result["growth"].get(key, {}).get(y) is None:
                gaps.append("%s %dy CAGR: %d years of %s, %d years of revenue, need %d+1 overlapping"
                            % (lbl, y, len(have), key, len(rev_have), y))
    if gaps:
        print()
        print("%s — window gaps (n/a cells above):" % NA)
        for g in gaps:
            print("  - %s" % g)

    print()
    print("FLAGS")
    for name, f, note in result["flags"]:
        if f:
            print()
            print("  %s" % QUALITY_WARNING)
            print("    Flag              %s" % name)
            print("    Observation       %s" % f["observation"])
            print("    Source            %s" % f["source"])
            print("    Could mean        %s" % f["could_mean"])
            print("    Would resolve it  %s" % f["would_resolve"])
            print("    Status            %s" % f["status"])
        elif note:
            print("  CLEAR (with note)   %s" % name)
            print("      %s" % note)
        else:
            print("  CLEAR               %s" % name)

    conf, detail = result["confidence"]
    print()
    print("DATA CONFIDENCE  %d/100  (facts=%d, tier1_share=%.0f%%, verified=%d, "
          "conflicts=%d, undated=%d, stale=%d, missing_required=%s)"
          % (conf, result["n_facts"], detail["tier1_share"] * 100, detail["verified_metrics"],
             detail["conflicts"], detail["undated"], detail["stale"], detail["missing_required"]))
    if result["source"] == "esef":
        print()
        print("Note: ESEF filings carry no reliable publication date through this")
        print("toolkit's fetch path, so every fact above is SINGLE SOURCE with no")
        print("independent corroboration and no publication_date. This is a real")
        print("limit of the free ESEF route, not a defect in this company's record.")


def to_json(result):
    def fact_or_none(f):
        return f.to_dict() if f is not None else None

    out = {"label": result["label"], "source": result["source"], "currency": result["currency"],
          "missing_base_concepts": result["missing_base_concepts"],
          "periods_available": result["periods_available"],
          "windows": {k: {y: fact_or_none(v) for y, v in d.items()} for k, d in result["windows"].items()},
          "growth": {k: {y: fact_or_none(v) for y, v in d.items()} for k, d in result["growth"].items()},
          "coverage": result["coverage"], "growth_coverage": result["growth_coverage"],
          "flags": [{"name": n, "warning": f, "note": note} for n, f, note in result["flags"]],
          "data_confidence": {"score": result["confidence"][0], "detail": result["confidence"][1]}}
    return out


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", help="company name (Nordic/French ESEF issuer)")
    ap.add_argument("--years", type=int, default=5,
                    help="years of annual history to analyse; also caps the largest reporting window")
    ap.add_argument("--country", help="restrict Nordic name resolution to one ISO country, e.g. SE")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()
    if args.years < 1:
        ap.error("--years must be at least 1")

    record, contenders = None, []
    try:
        record, reason, contenders = resolve_nordic(args.query, args.country)
    except (Exception, SystemExit):
        record = None

    if record is not None:
        lei = record.get("lei")
        if not lei or lei == NA:
            print("%s: Nordic identity resolved for %r but no LEI was found, so no "
                  "ESEF filings can be pulled." % (NA, args.query))
            return 1
        merged, filings, err = fetch_esef(lei, args.years)
        if err:
            print("%s: %s" % (NA, err))
            return 1
        currency = record.get("reporting_currency") or None
        if currency == NA:
            currency = None
        facts = to_facts_esef(merged, currency)
        result = analyse(facts, currency, record["company_name"], "esef")
    elif contenders:
        print("REFUSING TO RESOLVE %r: %s." % (args.query, contenders and "ambiguous name"))
        for c in contenders:
            print("  %s" % c.display())
        print()
        print("Re-run with the ticker, ISIN or full legal name.")
        return 2
    else:
        print("DATA NOT AVAILABLE: %r did not resolve to a Nordic/French ESEF "
              "issuer. US issuers are out of scope; this toolkit covers "
              "European venues only." % args.query)
        return 1

    if args.as_json:
        print(json.dumps(to_json(result), indent=2, ensure_ascii=False, default=str))
    else:
        print_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
