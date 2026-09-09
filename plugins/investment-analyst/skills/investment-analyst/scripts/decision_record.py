#!/usr/bin/env python3
"""decision_record.py - the decision as a first-class, validated object.

WHY THIS EXISTS

Until v3.0.0 this system produced an excellent analysis and then forgot it.
SKILL.md section 9 specified a machine-comparable decision record; no script
ever wrote one, so a past verdict was unrecoverable except by re-running the
analysis - which is not guaranteed to reproduce the same call. Everything
longitudinal (research delta, thesis health over time, expectation
calibration, monitoring, track record) was blocked behind that one absence.

THE AUTHORITY IS INVERTED, DELIBERATELY

SKILL.md's checksum design has the verdict block, the signal line and the
decision record carrying the same numbers, with any divergence treated as a
defect. But three prose copies inside one document are cross-checked by
nothing, and persisting a fourth copy would inherit zero enforcement while
adding a new failure mode: a stored record that outlives the prose that
explained it, and lies about it.

So the model emits THIS record first, as JSON, and render_decision_block()
renders the human block back from it. There is then one source, and the two
cannot diverge. That is the breaking change in v3.0.0.

WHAT IS AND IS NOT MECHANICAL HERE

Mechanical: the arithmetic identities, and the conviction ceiling.
  expected_return  = SUM w_i * (FV_i / P - 1)      (base uses its midpoint)
  margin_of_safety = 1 - P / FV                    (to base-low and base-high)
  SUM w_i          = 1
These are identities, not opinions, so validate() RECOMPUTES them and refuses
the record when the model's numbers disagree. This removes the weakest link in
the pipeline - arithmetic performed in prose - without building a valuation
engine.

Not mechanical: the call itself. There is no weighted composite score here and
there must never be one. references/bear-case-and-scoring.md is explicit that
"the recommendation is not a function of the investment score", and a score
that decided the call would trade interpretability for the appearance of
rigour. What code enforces is the CONSTRAINTS on the judgement - hard gates,
conviction caps, thesis breakers - each recorded as a reason code so a past
decision can be audited rather than re-litigated.

Verified against SKILL.md section 9's own worked example (Sandvik, P=356,
base 420-470 @55%, bear 310 @25%, bull 540 @20%): the identities above
reproduce its stated +20.9% expected return and its +15%/+24% margins of
safety to the digit. That example is the canonical selftest fixture below.

PURE MODULE: no network, no filesystem, no imports of sibling scripts.
Persistence lives in thesis_ledger.py, which owns the store, the atomic write
and the identity resolution. This file owns the shape and the arithmetic.
"""
import argparse
import json
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

SCHEMA_VERSION = 1

# --------------------------------------------------------------------------
# Controlled vocabularies. A decision that uses a value outside these is
# refused rather than stored - a free-text verdict cannot be counted later.
# --------------------------------------------------------------------------

VERDICTS = ("STRONG BUY", "BUY", "HOLD", "TRIM", "SELL", "STRONG SELL")

# Ordered weakest to strongest; the index is the ceiling comparison.
CONVICTIONS = ("VERY LOW", "LOW", "MEDIUM", "HIGH", "VERY HIGH")

DEPTHS = ("TLDR", "QUICK", "COMPARE", "STANDARD", "DEEP", "PORTFOLIO", "SCREEN")

PRODUCERS = ("analyze", "quick", "tldr", "compare", "screen", "portfolio")

# position_sizing.py's telemetry() emits exactly these seven action tokens.
# Duplicated here as a closed vocabulary for the same reason VERDICTS and
# CONVICTIONS are: a free-text action cannot be counted later, and this file
# refuses one that is not on the list rather than storing it.
POSITION_SIZING_ACTIONS = ("NO_BET", "WATCH", "INITIATE", "ADD", "HOLD",
                           "TRIM", "EXIT")

# An assumption's epistemic class. Mirrors SKILL.md's evidence tags, minus
# FACT: a fact is not an assumption, and putting one here would be a category
# error the whole system exists to prevent.
BASES = ("ESTIMATE", "ASSUMPTION", "OPINION")

# --------------------------------------------------------------------------
# Reason codes. Every one of these is ALREADY computed somewhere in the
# toolkit; the gap was that the outcome was printed as prose and then lost.
# Nothing new is calculated to justify a decision - the codes are a recording
# of constraints that already ran.
#
# severity: BLOCK  - the number or the call cannot stand as stated
#           WARN   - stands, but the reader must be told
#           INFO   - recorded for later audit, no present effect
# --------------------------------------------------------------------------

REASON_CODES = {
    # valuation_gate.py - the eight checks
    "GATE_PRICE_STALE":            "Price timestamp is older than the freshness limit",
    "GATE_PERIOD_INCOMPATIBLE":    "Price and earnings do not share a compatible period",
    "GATE_PUBLICATION_UNKNOWN":    "Earnings fact carries no known publication date",
    "GATE_SHARE_COUNT_UNCERTAIN":  "Share-count semantic is not certain",
    "GATE_CURRENCY_MISMATCH":      "Price currency and reporting currency differ",
    "GATE_CORPORATE_ACTION":       "A share-count-changing action falls between the terms",
    "GATE_TTM_INCOMPLETE":         "TTM is not four contiguous quarters",
    "GATE_RESTATEMENT_SUPERSEDED": "A superseded restatement was silently in play",

    # A ninth gate outcome, distinct in kind from the eight above: the caller
    # handed the gate a bare float instead of a provenanced fact. That is a
    # PROGRAMMING error, not a finding about the issuer, and it is filed
    # separately so it can never be counted as a data-quality problem with
    # the company. Always BLOCK - a gate that could not see provenance did
    # not check anything.
    "GATE_INPUT_NOT_A_FACT":       "The gate was given a bare value with no provenance",

    # peers_se.py - the parallel warn-mode suppressions
    "PEER_CURRENCY_UNKNOWN":       "Reporting currency unknown; the peer row was suppressed",
    "PEER_FUNDAMENTALS_STALE":     "Latest ESEF annual is past the life of an annual figure",
    "PEER_NET_DEBT_UNTAGGED":      "Cash or borrowings untagged; EV multiples suppressed",
    # Both currencies are KNOWN here and simply differ with no rate to hand -
    # a different state from PEER_CURRENCY_UNKNOWN, which would misreport it.
    "PEER_FX_UNAVAILABLE":         "Quote and reporting currencies differ and no FX rate was available",
    # An absence rather than a suppression: nothing was withheld because
    # nothing was there to withhold.
    "PEER_NO_FUNDAMENTALS":        "No ESEF fundamentals or no market cap for this issuer",

    # thesis_ledger.py - derive_status
    "THESIS_BROKEN":               "A stored thesis breaker has fired",
    "THESIS_WARNING":              "A stored thesis breaker is close to firing",
    "THESIS_UNKNOWN":              "A stored thesis could not be evaluated",
    "THESIS_NONE":                 "No thesis is stored for this issuer",

    # finfact.py / references/data-quality.md
    "DATA_CONFIDENCE_LOW":         "Data confidence is below the usable floor",
    "CONFLICT_UNRESOLVED":         "Two independent sources disagree and it was not resolved",
    "SINGLE_SOURCE_MATERIAL":      "A material figure rests on one source",

    # earnings_quality.py
    "EQ_ACCRUAL_HIGH":             "Accrual ratio outside the acceptable band",
    "EQ_CASH_CONVERSION_LOW":      "Cash conversion materially below reported profit",

    # venues_se.py / references/red-flags-and-smallcap.md
    "VENUE_MTF":                   "Issuer trades on an MTF, not a regulated market",
    "VENUE_MICROCAP":              "Issuer is a microcap on First North, Spotlight or NGM",
    "REPORTING_K3":                "Issuer may report under Swedish GAAP K3, not IFRS",

    # portfolio_store.py / references/portfolio.md
    "PORTFOLIO_CONCENTRATION":     "Adding to this position breaches a concentration limit",
    "PORTFOLIO_OVERLAP":           "Position overlaps an existing holding's driver",

    # depth
    "DEPTH_NO_SCORECARD":          "No scorecard runs at this depth; no Investment Score",
    "DEPTH_NO_SCENARIOS":          "No scenarios run at this depth; no expected return",
}

SEVERITIES = ("BLOCK", "WARN", "INFO")

# --------------------------------------------------------------------------
# Conviction caps. Documented in SKILL.md and references/conviction.md as
# prose the model was asked to self-apply; grep for "conviction" across
# scripts/ returned nothing before v3.0.0. Now they are enforced: conviction
# is capped by the WEAKEST input, never averaged.
# --------------------------------------------------------------------------

CAPS = {
    "CAP_DEPTH_TLDR":        ("TLDR", "MEDIUM"),
    "CAP_DEPTH_QUICK":       ("QUICK", "MEDIUM"),
    "CAP_DEPTH_COMPARE":     ("COMPARE", "MEDIUM"),
    "CAP_VENUE_MICROCAP":    ("MTF microcap", "MEDIUM"),
    "CAP_CONFLICT":          ("unresolved source conflict", "LOW"),
    "CAP_THESIS_BROKEN":     ("thesis breaker fired", "LOW"),
    "CAP_DATA_CONFIDENCE":   ("data confidence below floor", "LOW"),
}

DEPTH_CAPS = {"TLDR": "CAP_DEPTH_TLDR",
              "QUICK": "CAP_DEPTH_QUICK",
              "COMPARE": "CAP_DEPTH_COMPARE"}

# Arithmetic tolerance. The model reports expected return to one decimal
# place, so a recomputation may legitimately differ by rounding. 0.15pp is
# tight enough to catch a real error and loose enough not to fire on display
# rounding of a correctly-computed figure.
TOLERANCE_PP = 0.0015
WEIGHT_TOLERANCE = 0.005


class DecisionError(ValueError):
    """The record is not storable. Raised instead of writing a wrong record."""


# --------------------------------------------------------------------------
# Arithmetic - the identities the validator enforces
# --------------------------------------------------------------------------

def base_midpoint(fair_value):
    """The base case is a RANGE in this system, never a point (SKILL.md: 'no
    point estimate on the fair-value line'). Its midpoint is what the
    probability weighting uses."""
    lo, hi = fair_value.get("base_low"), fair_value.get("base_high")
    if lo is None or hi is None:
        raise DecisionError("fair_value needs base_low and base_high")
    return (float(lo) + float(hi)) / 2.0


def expected_return(price, fair_value, weights):
    """SUM w_i * (FV_i / P - 1), base taken at its midpoint.

    Verified against SKILL.md section 9's Sandvik example: +20.9%.
    """
    p = float(price)
    if p <= 0:
        raise DecisionError("price must be positive to form a return")
    pts = {"bear": fair_value.get("bear"),
           "base": base_midpoint(fair_value),
           "bull": fair_value.get("bull")}
    total, wsum = 0.0, 0.0
    for name, w in weights.items():
        if name not in pts:
            raise DecisionError("unknown scenario in weights: %s" % name)
        fv = pts[name]
        if fv is None:
            raise DecisionError("scenario %s is weighted but has no fair value" % name)
        total += float(w) * (float(fv) / p - 1.0)
        wsum += float(w)
    if abs(wsum - 1.0) > WEIGHT_TOLERANCE:
        raise DecisionError("scenario weights sum to %.4f, not 1.0" % wsum)
    return total


def margin_of_safety(price, fair_value):
    """1 - P/FV, to base-low and base-high.

    Deliberately NOT the same quantity as the verdict block's upside
    (FV/P - 1). SKILL.md section 9 says so explicitly: the two will not match
    to the digit, and that is not a divergence provided each is labelled.
    """
    p = float(price)
    lo, hi = float(fair_value["base_low"]), float(fair_value["base_high"])
    if lo <= 0 or hi <= 0:
        raise DecisionError("fair value must be positive")
    return {"to_base_low": 1.0 - p / lo, "to_base_high": 1.0 - p / hi}


def conviction_ceiling(depth, reason_codes):
    """The weakest input sets the ceiling. Returns (ceiling, caps_applied).

    Never averages. A microcap on QUICK depth with an unresolved conflict is
    capped at LOW by the conflict, not at MEDIUM by the average of three.
    """
    applied = []
    if depth in DEPTH_CAPS:
        applied.append(DEPTH_CAPS[depth])
    codes = {c.get("code") for c in reason_codes or []}
    if "VENUE_MICROCAP" in codes:
        applied.append("CAP_VENUE_MICROCAP")
    if "CONFLICT_UNRESOLVED" in codes:
        applied.append("CAP_CONFLICT")
    if "THESIS_BROKEN" in codes:
        applied.append("CAP_THESIS_BROKEN")
    if "DATA_CONFIDENCE_LOW" in codes:
        applied.append("CAP_DATA_CONFIDENCE")
    if not applied:
        return CONVICTIONS[-1], []
    ceiling = min((CAPS[c][1] for c in applied), key=CONVICTIONS.index)
    return ceiling, [{"cap": c, "reason": CAPS[c][0], "ceiling": CAPS[c][1]}
                     for c in applied]


# --------------------------------------------------------------------------
# implied_expectations - what the MARKET was assuming on the decision date
# --------------------------------------------------------------------------
# This is the field that makes expectation calibration possible without any
# consensus data: record what price implied at the time, and a later run can
# ask whether the company delivered it. Because no consensus source is
# obtainable free and keyless, this is the only forward-expectation series
# this system can honestly keep.
#
# The canonical form is a LIST of entries. It was briefly ambiguous - the
# calibration reader coerces a bare dict to a single entry and then reads
# entry["metric"], so a FLAT mapping like {"revenue_cagr": 0.12} would have
# been accepted and then silently produced zero comparisons, because the
# wrapped dict has no "metric" key. Normalising here closes that: a flat
# mapping is expanded into proper entries instead of being quietly useless.

def normalise_implied_expectations(raw):
    """Return implied_expectations as a list of entries, or [] when absent.

    Accepts three authoring shapes and canonicalises all of them:
      [{"metric": "revenue_cagr", "value": 0.12}, ...]   the canonical list
      {"metric": "revenue_cagr", "value": 0.12}          one entry
      {"revenue_cagr": 0.12, "ebit_margin": 0.22}        a flat mapping

    `metric` is free text; it is resolved against the ledger's metric
    vocabulary at read time rather than constrained here, so a metric this
    system cannot yet check is still recorded rather than refused.
    """
    if not raw:
        return []
    if isinstance(raw, dict):
        if "metric" in raw or "value" in raw:
            raw = [raw]
        else:
            raw = [{"metric": k, "value": v} for k, v in raw.items()]
    if not isinstance(raw, (list, tuple)):
        raise DecisionError(
            "implied_expectations must be a list of entries, one entry, or a "
            "flat {metric: value} mapping - got %s" % type(raw).__name__)
    out = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise DecisionError(
                "each implied_expectations entry must be an object, got %s"
                % type(entry).__name__)
        metric = entry.get("metric")
        if not metric:
            raise DecisionError(
                "an implied_expectations entry needs a metric - an unnamed "
                "expectation cannot be checked against a later filing")
        value = entry.get("value")
        if value is not None:
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise DecisionError(
                    "implied_expectations[%s].value is not a number: %r"
                    % (metric, entry.get("value")))
        out.append({"metric": metric, "value": value,
                    "unit": entry.get("unit"),
                    "period": entry.get("period"),
                    "method": entry.get("method") or "reverse_dcf",
                    "note": entry.get("note") or ""})
    return out


# --------------------------------------------------------------------------
# position_sizing - OPTIONAL, ADDITIVE telemetry so calibration.py can later
# measure sizing decisions forward, on the same forward-only footing as
# everything else calibration.py reads. A record that carries none of this
# validates exactly as it did before this field existed.
#
# THIS DOES NOT RECOMPUTE POSITION SIZING. That arithmetic (raw Kelly, the
# fractional cut, the uncertainty haircut) lives in position_sizing.py and
# stays there - "a value has exactly one home" applies here exactly as it
# does to the conviction ladder, and duplicating the Kelly math in this file
# would create a second, driftable copy of it. The only check performed below
# is the one that is FREE: when target, current and delta are all present,
# they must agree with each other, the same posture already applied a few
# lines up to expected_return and margin_of_safety - a persisted number that
# disagrees with its own inputs is refused rather than stored.
#
# decision_record.py does NOT import position_sizing.py. The dependency runs
# one way: position_sizing.telemetry() produces a dict shaped to satisfy this
# validator, never the reverse.
# --------------------------------------------------------------------------

def normalise_position_sizing(raw):
    """None when absent (the field stays entirely off the record, exactly as
    before this existed). Otherwise a light-touch validated copy, or
    DecisionError."""
    if not isinstance(raw, dict):
        raise DecisionError(
            "position_sizing must be an object, got %s" % type(raw).__name__)

    action = raw.get("action")
    if action not in POSITION_SIZING_ACTIONS:
        raise DecisionError(
            "position_sizing.action %r is not one of %s"
            % (action, ", ".join(POSITION_SIZING_ACTIONS)))

    out = dict(raw)
    for key in ("target", "current", "delta"):
        v = out.get(key)
        if v is None:
            continue
        try:
            out[key] = float(v)
        except (TypeError, ValueError):
            raise DecisionError(
                "position_sizing.%s is not a number: %r" % (key, v))

    target, current, delta = out.get("target"), out.get("current"), out.get("delta")
    if target is not None and current is not None and delta is not None:
        expected_delta = target - current
        if abs(delta - expected_delta) > TOLERANCE_PP:
            raise DecisionError(
                "position_sizing.delta %.4f does not match target - current "
                "= %.4f. The record is refused rather than stored: a "
                "persisted number that disagrees with its own inputs is "
                "worse than none." % (delta, expected_delta))

    out["action"] = action
    return out


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

REQUIRED = ("as_of", "depth", "producer", "identity", "verdict", "conviction",
            "price", "reason_codes")


def validate(rec, strict=True):
    """Recompute every identity and check every vocabulary.

    Returns (normalised_record, warnings). Raises DecisionError on anything
    that would store a wrong or uncountable record.

    strict=False relaxes the scenario requirements only, for the depths that
    genuinely run no scenarios (TLDR, QUICK, COMPARE, SCREEN) - those record
    DEPTH_NO_SCENARIOS instead of inventing an expected return.
    """
    if not isinstance(rec, dict):
        raise DecisionError("decision record must be a JSON object")

    out = dict(rec)
    out["schema_version"] = SCHEMA_VERSION
    warnings = []

    for field in REQUIRED:
        if out.get(field) in (None, "", []):
            if field == "reason_codes":
                out["reason_codes"] = []
                continue
            raise DecisionError("missing required field: %s" % field)

    if out["verdict"] not in VERDICTS:
        raise DecisionError("verdict %r is not one of %s"
                            % (out["verdict"], ", ".join(VERDICTS)))
    if out["conviction"] not in CONVICTIONS:
        raise DecisionError("conviction %r is not one of %s"
                            % (out["conviction"], ", ".join(CONVICTIONS)))
    if out["depth"] not in DEPTHS:
        raise DecisionError("depth %r is not one of %s"
                            % (out["depth"], ", ".join(DEPTHS)))
    if out["producer"] not in PRODUCERS:
        raise DecisionError("producer %r is not one of %s"
                            % (out["producer"], ", ".join(PRODUCERS)))

    ident = out["identity"] or {}
    if not (ident.get("lei") or ident.get("isin")):
        raise DecisionError(
            "identity needs a lei or an isin - a decision keyed on a name "
            "cannot be matched to a later outcome, and 'Volvo' is two companies")

    # --- reason codes -----------------------------------------------------
    norm_codes = []
    for entry in out.get("reason_codes") or []:
        if isinstance(entry, str):
            entry = {"code": entry, "severity": "WARN", "detail": ""}
        code = entry.get("code")
        if code not in REASON_CODES:
            raise DecisionError(
                "unknown reason code %r - add it to REASON_CODES rather than "
                "inventing one at the call site, or it cannot be counted later"
                % code)
        sev = entry.get("severity") or "WARN"
        if sev not in SEVERITIES:
            raise DecisionError("severity %r is not one of %s"
                                % (sev, ", ".join(SEVERITIES)))
        norm_codes.append({"code": code, "severity": sev,
                           "detail": entry.get("detail") or REASON_CODES[code]})
    out["reason_codes"] = norm_codes

    # --- the conviction ceiling is enforced, not suggested ----------------
    ceiling, caps = conviction_ceiling(out["depth"], norm_codes)
    out["caps_applied"] = caps
    if CONVICTIONS.index(out["conviction"]) > CONVICTIONS.index(ceiling):
        raise DecisionError(
            "conviction %s exceeds the ceiling %s set by %s - conviction is "
            "capped by the weakest input, never averaged"
            % (out["conviction"], ceiling,
               ", ".join(c["cap"] for c in caps) or "the applicable caps"))

    # --- price ------------------------------------------------------------
    price = out["price"] or {}
    pv = price.get("value")
    if pv is None or float(pv) <= 0:
        raise DecisionError("price.value must be a positive number")
    if not price.get("currency"):
        raise DecisionError("price.currency is required - an unlabelled "
                            "currency is how a 63x P/E gets printed for a 5.7x company")
    if not price.get("as_of"):
        raise DecisionError("price.as_of is required - never present a stale "
                            "price as current")

    # --- position sizing telemetry (optional, additive) -------------------
    # Only touched when the field is present at all - an absent field must
    # leave the record byte-for-byte as it would have validated before this
    # existed, with no new key and no new warning.
    if "position_sizing" in out and out["position_sizing"] is not None:
        out["position_sizing"] = normalise_position_sizing(out["position_sizing"])

    # --- scenarios and the arithmetic identities --------------------------
    fv = out.get("fair_value")
    weights = out.get("scenario_weights")
    scenario_depth = out["depth"] in ("STANDARD", "DEEP")

    if not fv or not weights:
        if strict and scenario_depth:
            raise DecisionError(
                "fair_value and scenario_weights are required at %s depth"
                % out["depth"])
        if not any(c["code"] == "DEPTH_NO_SCENARIOS" for c in norm_codes):
            out["reason_codes"].append(
                {"code": "DEPTH_NO_SCENARIOS", "severity": "INFO",
                 "detail": REASON_CODES["DEPTH_NO_SCENARIOS"]})
        out["expected_return"] = None
        out["margin_of_safety"] = None
        return out, warnings

    computed_er = expected_return(pv, fv, weights)
    stated_er = out.get("expected_return")
    if stated_er is None:
        out["expected_return"] = computed_er
    elif abs(float(stated_er) - computed_er) > TOLERANCE_PP:
        raise DecisionError(
            "expected_return %.4f does not match SUM w(FV/P-1) = %.4f. "
            "The record is refused rather than stored: a persisted number "
            "that disagrees with its own inputs is worse than none."
            % (float(stated_er), computed_er))
    else:
        out["expected_return"] = computed_er

    computed_mos = margin_of_safety(pv, fv)
    stated_mos = out.get("margin_of_safety")
    if isinstance(stated_mos, dict):
        for key in ("to_base_low", "to_base_high"):
            if stated_mos.get(key) is not None and \
                    abs(float(stated_mos[key]) - computed_mos[key]) > TOLERANCE_PP:
                raise DecisionError(
                    "margin_of_safety.%s %.4f does not match 1 - P/FV = %.4f"
                    % (key, float(stated_mos[key]), computed_mos[key]))
    out["margin_of_safety"] = computed_mos

    # --- assumptions ------------------------------------------------------
    norm_assumptions = []
    for a in out.get("assumptions") or []:
        if not a.get("key"):
            raise DecisionError("every assumption needs a key")
        basis = a.get("basis") or "ASSUMPTION"
        if basis not in BASES:
            raise DecisionError(
                "assumption %r has basis %r; a fact is not an assumption"
                % (a["key"], basis))
        if not a.get("rationale"):
            warnings.append("assumption %r carries no rationale, so it cannot "
                            "be challenged later" % a["key"])
        norm_assumptions.append({"key": a["key"], "value": a.get("value"),
                                 "unit": a.get("unit"), "basis": basis,
                                 "scenario": a.get("scenario"),
                                 "rationale": a.get("rationale") or ""})
    out["assumptions"] = norm_assumptions

    # Canonicalise the market's recorded expectations (see the note above the
    # helper: the flat-mapping shape was silently useless downstream).
    out["implied_expectations"] = normalise_implied_expectations(
        out.get("implied_expectations"))

    # The holding horizon, when the run resolved one. horizon.py computes the
    # next dated event that settles or breaks the thesis - "the next report,
    # an AGM vote, a financing deadline" - and until now nothing stored it,
    # so the date the call was meant to be judged by was lost with the prose.
    # Optional: a run that could not date an event records nothing rather
    # than inventing a horizon.
    hz = out.get("horizon")
    if hz:
        if not isinstance(hz, dict):
            raise DecisionError("horizon must be an object with a date")
        if not hz.get("date"):
            raise DecisionError(
                "horizon needs a date - a horizon without one is the "
                "'6-12 months' guess horizon.py exists to replace")
        out["horizon"] = {"date": hz["date"], "event": hz.get("event"),
                          "source": hz.get("source"),
                          "confirmed": bool(hz.get("confirmed", False))}

    rests = out.get("rests_on") or []
    if scenario_depth and not rests:
        warnings.append("rests_on is empty; SKILL.md section 9 asks for the "
                        "two or three assumptions the call actually depends on")
    if len(rests) > 3:
        warnings.append("rests_on carries %d entries; the contract asks for "
                        "two or three" % len(rests))

    out.setdefault("superseded_by", None)
    out.setdefault("outcome", None)
    return out, warnings


# --------------------------------------------------------------------------
# Rendering - the human block is DERIVED, never authored separately
# --------------------------------------------------------------------------

def _pct(x, places=1):
    return "%+.*f%%" % (places, 100.0 * float(x))


# The rendered block is indented to a 17-column label field plus one space.
# Every chart in this toolkit is capped at 88 columns, so the ladder gets
# what is left after the indent and its own two end labels - never a
# constant. A fixed width=64 overran at 90 columns (91 with a four-digit
# bull), which the module's own selftest would have caught only after the
# fact; deriving it means the cap holds for any price magnitude.
LABEL_WIDTH = 17
MAX_COLS = 88


def scenario_ladder(price, fair_value, width=None):
    """The bear-to-bull ladder from SKILL.md section 9.

    The scale line carries the positions and the legend carries the numbers,
    so an inline label can never push a marker out of true - the rule
    SKILL.md sets for every positional chart in this system.

    `width` defaults to whatever fits inside MAX_COLS once the indent and the
    two end labels are accounted for. Pass it only to override.
    """
    bear, bull = float(fair_value["bear"]), float(fair_value["bull"])
    lo, hi = float(fair_value["base_low"]), float(fair_value["base_high"])
    p = float(price)
    span = bull - bear
    if span <= 0:
        return ""
    lo_label, hi_label = "%.0f" % bear, "%.0f" % bull
    if width is None:
        # indent + label + space + bar + space + label
        width = MAX_COLS - (LABEL_WIDTH + 1) - len(lo_label) - len(hi_label) - 2
    if width < 8:
        return ""

    def pos(v):
        return max(0, min(width - 1, int(round((float(v) - bear) / span * (width - 1)))))

    cells = ["─"] * width
    for i in range(pos(lo), pos(hi) + 1):
        cells[i] = "═"
    cells[pos(lo)] = "├"
    cells[pos(hi)] = "┤"
    cells[pos(p)] = "●"
    return "%s %s %s" % (lo_label, "".join(cells), hi_label)


def render_decision_block(rec):
    """Render the fixed-shape English block from the validated record.

    This is the direction of authority that makes the checksum real: there is
    one source of these numbers, and the prose is generated from it. Paste the
    output verbatim; do not retype it.
    """
    rec, _ = validate(rec, strict=False)
    ident = rec["identity"]
    price = rec["price"]
    lines = []

    # SKILL.md section 9 prints "SAND.ST · Sandvik AB (556000-3468) · ...":
    # the legal name and its org number are ONE element, not two separated
    # by a bullet.
    named = ident.get("name") or ""
    if named and ident.get("org_number"):
        named = "%s (%s)" % (named, ident["org_number"])
    head = " · ".join(x for x in (
        ident.get("ticker"), named or None,
        rec["depth"], rec["as_of"]) if x)
    lines.append("DECISION — %s" % head)
    lines.append("")
    lines.append("%-17s %s — %s CONVICTION"
                 % ("RECOMMENDATION", rec["verdict"], rec["conviction"]))

    rep = price.get("reporting_currency")
    meta = ", ".join(x for x in (price.get("as_of"), price.get("source")) if x)
    tail = "(%s%s)" % (meta, " · reports in %s" % rep if rep else "")
    # A price keeps two decimals whatever its magnitude - section 9 prints
    # "SEK 356.00". _fmt_num drops them above 100, which is right for a
    # fair-value bound and wrong for the traded price.
    lines.append("%-17s %s %.2f   %s" % ("Price", price["currency"],
                                         float(price["value"]), tail))

    fv, w = rec.get("fair_value"), rec.get("scenario_weights")
    if fv and w:
        lines.append("%-17s %s %s-%s base (%d%%) · %s bear (%d%%) · %s bull (%d%%)"
                     % ("Fair value", fv.get("currency") or price["currency"],
                        _fmt_num(fv["base_low"]), _fmt_num(fv["base_high"]),
                        round(100 * w["base"]), _fmt_num(fv["bear"]),
                        round(100 * w["bear"]), _fmt_num(fv["bull"]),
                        round(100 * w["bull"])))
        ladder = scenario_ladder(price["value"], fv)
        if ladder:
            lines.append("")
            lines.append("%-17s %s" % ("", ladder))
            lines.append("")
        lines.append("%-17s %s   (probability-weighted across scenarios)"
                     % ("Expected return", _pct(rec["expected_return"])))
        mos = rec["margin_of_safety"]
        lines.append("%-17s %s to base-low · %s to base-high"
                     % ("Margin of safety", _pct(mos["to_base_low"], 0),
                        _pct(mos["to_base_high"], 0)))

    scores = rec.get("scores") or {}
    inv, dc = scores.get("investment_score"), scores.get("data_confidence")
    inv_s = "%d/100" % inv if inv is not None else "n/a — no scorecard at this depth"
    dc_s = "%d/100" % dc if dc is not None else "n/a"
    lines.append("%-17s %-13s Data Confidence  %s"
                 % ("Investment Score", inv_s, dc_s))

    rests = rec.get("rests_on") or []
    if rests:
        lines.append("")
        for i, r in enumerate(rests, 1):
            text = r.get("text") if isinstance(r, dict) else str(r)
            tag = (r.get("basis") if isinstance(r, dict) else None) or "ASSUMPTION"
            label = "Rests on" if i == 1 else ""
            lines.append("%-17s %d. %-42s %s" % (label, i, text[:42], tag))

    codes = [c for c in rec["reason_codes"] if c["severity"] in ("BLOCK", "WARN")]
    if codes:
        lines.append("")
        for i, c in enumerate(codes):
            lines.append("%-17s %s  %s" % ("Flags" if i == 0 else "",
                                           c["severity"], c["code"]))

    caps = rec.get("caps_applied") or []
    if caps:
        lines.append("%-17s %s" % ("Conviction cap",
                                   " · ".join("%s (%s)" % (c["cap"], c["ceiling"])
                                                   for c in caps)))

    trig = rec.get("triggers")
    if trig:
        lines.append("%-17s see Bevakning in the closing block — %s rows"
                     % ("Triggers", trig if isinstance(trig, int) else len(trig)))

    lines.append("")
    lines.append("*This is analysis, not investment advice.*")
    return "\n".join(lines)


def _fmt_num(v):
    v = float(v)
    return "%.2f" % v if abs(v) < 100 else "%.0f" % v


# --------------------------------------------------------------------------
# CLI - validate or render a record handed in as JSON
# --------------------------------------------------------------------------

def _sandvik_fixture():
    """SKILL.md section 9's own worked example, used as the selftest fixture
    precisely because the document states its expected outputs (+20.9%,
    +15%/+24%) - so the identities are checked against the contract rather
    than against my own arithmetic."""
    return {
        "as_of": "2026-08-31", "depth": "STANDARD", "producer": "analyze",
        "identity": {"name": "Sandvik AB", "ticker": "SAND.ST",
                     "org_number": "556000-3468",
                     "lei": "213800Y2XLTQMHLB5J34", "isin": "SE0000667891"},
        "verdict": "BUY", "conviction": "MEDIUM",
        "price": {"value": 356.00, "currency": "SEK",
                  "as_of": "2026-08-31 07:14 UTC", "source": "Nasdaq",
                  "reporting_currency": "SEK"},
        "fair_value": {"bear": 310, "base_low": 420, "base_high": 470,
                       "bull": 540, "currency": "SEK"},
        "scenario_weights": {"bear": 0.25, "base": 0.55, "bull": 0.20},
        "scores": {"investment_score": 74, "data_confidence": 61},
        "rests_on": [
            {"text": "Mining capex holds through 2027", "basis": "ASSUMPTION"},
            {"text": "EBIT margin >= 15% through the cycle", "basis": "ASSUMPTION"},
            {"text": "Multiple reverts to 10y median, not peak", "basis": "ASSUMPTION"}],
        "assumptions": [
            {"key": "ebit_margin_through_cycle", "value": 0.15, "unit": "ratio",
             "basis": "ASSUMPTION", "scenario": "base",
             "rationale": "Trough margin in 2016 and 2020 both held above 14%"}],
        "reason_codes": [{"code": "GATE_TTM_INCOMPLETE", "severity": "WARN",
                          "detail": "Q2 EBIT not disclosed as a standalone line"}],
        "triggers": 5,
    }


def selftest():
    fails = []

    def check(label, cond):
        if not cond:
            fails.append(label)

    rec, warns = validate(_sandvik_fixture())
    check("Sandvik expected return should be +20.9%%, got %.4f" % rec["expected_return"],
          abs(rec["expected_return"] - 0.2086) < 0.0006)
    check("MoS to base-low should be ~+15%%, got %.4f" % rec["margin_of_safety"]["to_base_low"],
          abs(rec["margin_of_safety"]["to_base_low"] - 0.152381) < 0.0006)
    check("MoS to base-high should be ~+24%%, got %.4f" % rec["margin_of_safety"]["to_base_high"],
          abs(rec["margin_of_safety"]["to_base_high"] - 0.2426) < 0.0006)

    # A stated expected return that disagrees with its own inputs is refused.
    bad = _sandvik_fixture()
    bad["expected_return"] = 0.35
    try:
        validate(bad)
        check("a wrong expected_return must be refused", False)
    except DecisionError:
        pass

    # Weights that do not sum to 1 are refused.
    bad = _sandvik_fixture()
    bad["scenario_weights"] = {"bear": 0.25, "base": 0.55, "bull": 0.50}
    try:
        validate(bad)
        check("weights summing to 1.30 must be refused", False)
    except DecisionError:
        pass

    # Conviction above the ceiling is refused: QUICK caps at MEDIUM.
    bad = _sandvik_fixture()
    bad["depth"] = "QUICK"
    bad["conviction"] = "HIGH"
    try:
        validate(bad, strict=False)
        check("HIGH conviction at QUICK depth must be refused", False)
    except DecisionError:
        pass

    # The weakest input sets the ceiling, never the average.
    ceiling, caps = conviction_ceiling(
        "QUICK", [{"code": "VENUE_MICROCAP"}, {"code": "CONFLICT_UNRESOLVED"}])
    check("three caps should floor at LOW, got %s" % ceiling, ceiling == "LOW")
    check("all three caps should be recorded, got %d" % len(caps), len(caps) == 3)

    # An identity without lei or isin cannot be matched to a later outcome.
    bad = _sandvik_fixture()
    bad["identity"] = {"name": "Volvo"}
    try:
        validate(bad)
        check("a name-only identity must be refused", False)
    except DecisionError:
        pass

    # An invented reason code cannot be counted later, so it is refused.
    bad = _sandvik_fixture()
    bad["reason_codes"] = [{"code": "LOOKS_A_BIT_EXPENSIVE"}]
    try:
        validate(bad)
        check("an unknown reason code must be refused", False)
    except DecisionError:
        pass

    # A shallow depth records why it has no expected return rather than inventing one.
    quick = _sandvik_fixture()
    quick["depth"] = "QUICK"
    quick.pop("fair_value")
    quick.pop("scenario_weights")
    quick["scores"] = {"data_confidence": 55}
    qrec, _ = validate(quick, strict=False)
    check("QUICK must record DEPTH_NO_SCENARIOS",
          any(c["code"] == "DEPTH_NO_SCENARIOS" for c in qrec["reason_codes"]))
    check("QUICK must not carry an expected return",
          qrec["expected_return"] is None)

    # position_sizing is optional and additive: absent, it changes nothing.
    plain_rec, plain_warns = validate(_sandvik_fixture())
    check("a record with no position_sizing must carry no such key",
          "position_sizing" not in plain_rec)
    check("a record with no position_sizing must raise no new warning",
          plain_warns == [])

    with_sizing = _sandvik_fixture()
    with_sizing["position_sizing"] = {
        "action": "ADD", "target": 0.06, "current": 0.04, "delta": 0.02}
    sized_rec, _ = validate(with_sizing)
    check("position_sizing must round-trip through validate() unchanged",
          sized_rec["position_sizing"] ==
          {"action": "ADD", "target": 0.06, "current": 0.04, "delta": 0.02})

    bad_delta = _sandvik_fixture()
    bad_delta["position_sizing"] = {
        "action": "ADD", "target": 0.06, "current": 0.04, "delta": 0.5}
    try:
        validate(bad_delta)
        check("a position_sizing delta that disagrees with target-current "
              "must be refused", False)
    except DecisionError:
        pass

    bad_action = _sandvik_fixture()
    bad_action["position_sizing"] = {"action": "BUY_MORE"}
    try:
        validate(bad_action)
        check("a position_sizing action outside the seven tokens must be "
              "refused", False)
    except DecisionError:
        pass

    block = render_decision_block(_sandvik_fixture())
    check("block should open with DECISION", block.startswith("DECISION — SAND.ST"))
    check("block should carry the recommendation", "BUY — MEDIUM CONVICTION" in block)
    check("block should carry both scores",
          "74/100" in block and "61/100" in block)
    check("no rendered line may exceed 88 columns",
          all(len(l) <= 88 for l in block.splitlines()))

    ladder = scenario_ladder(356.0, _sandvik_fixture()["fair_value"])
    check("ladder should mark the price with a filled circle", "●" in ladder)
    check("ladder should bracket the base range",
          "├" in ladder and "┤" in ladder)

    if fails:
        print("SELFTEST FAILED (%d)" % len(fails))
        for f in fails:
            print("  - %s" % f)
        return 1
    print("decision_record selftest: OK")
    if warns:
        print("(warnings on the fixture: %d)" % len(warns))
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Validate or render a decision record handed in as JSON.")
    ap.add_argument("path", nargs="?",
                    help="JSON file, or - for stdin. Omit with --selftest.")
    ap.add_argument("--render", action="store_true",
                    help="print the fixed-shape decision block")
    ap.add_argument("--json", action="store_true",
                    help="print the validated, normalised record as JSON")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--fixture", action="store_true",
                    help="print SKILL.md section 9's worked example as JSON")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.fixture:
        print(json.dumps(_sandvik_fixture(), indent=2, ensure_ascii=False))
        return 0
    if not args.path:
        ap.error("a JSON path is required (or --selftest / --fixture)")

    if args.path == "-":
        raw = sys.stdin.read()
    else:
        with open(args.path, encoding="utf-8") as fh:
            raw = fh.read()
    try:
        rec, warns = validate(json.loads(raw))
    except DecisionError as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print("REFUSED: not valid JSON - %s" % e, file=sys.stderr)
        return 2

    for w in warns:
        print("warning: %s" % w, file=sys.stderr)
    if args.json and args.render:
        ap.error("--json and --render ask for different outputs; pick one")
    if args.json:
        print(json.dumps(rec, indent=2, ensure_ascii=False))
    else:
        # --render is the default, and naming it explicitly is allowed so a
        # caller can be unambiguous about what it wants back.
        print(render_decision_block(rec))
    return 0


if __name__ == "__main__":
    sys.exit(main())
