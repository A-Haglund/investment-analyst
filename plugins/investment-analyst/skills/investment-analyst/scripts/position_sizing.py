#!/usr/bin/env python3
"""Position sizing: what the edge could justify, then everything that limits it.

WHY THIS FILE EXISTS

The plugin could already say BUY with a conviction level. It could not say
*how much*, and "how much" was left to prose - which meant it was left to
arithmetic done in a language model, unchecked, in a place where being wrong
compounds. This module is the capital-allocation layer over the existing
analysis: the analysis decides whether there is an edge, this decides what
size that edge survives contact with.

Kelly is sizing, not the probability model. The layers are kept apart on
purpose, because collapsing them is how a sizing engine starts quietly
inventing beliefs:

    company analysis        (the model, elsewhere)
      -> scenario forecast  (the model, elsewhere - never invented here)
      -> probability + its support        (checked here, never supplied here)
      -> calibration                      (forecast_scoring.py; inert today)
      -> expected return / risk distribution   (kelly.py, pure arithmetic)
      -> raw Kelly -> fractional Kelly
      -> uncertainty adjustment
      -> conviction ceiling
      -> risk budget
      -> liquidity cap
      -> portfolio caps
      -> concentration adjustment
      -> final target -> action
      -> decision record                  (decision_record.py owns the call)

This module owns only the layers from "expected return" down. It does not
produce probabilities and will not fill one in: a request without a scenario
distribution returns INSUFFICIENT_DATA. Refuse-never-guess applies to
probabilities exactly as it applies to figures.

In practice Kelly is almost never what binds. An ordinary bear/base/bull
with a 12.5% expected return already implies more than 100% of capital (see
kelly.py's selftest), so treating the Kelly figure as a position would be
absurd. It is kept because it is the only principled statement of what the
estimated edge is worth, and because seeing it next to a 3% target is the
honest way to show how much of the sizing is edge and how much is caution.

THE ONE THING THIS MODULE MUST NOT DO: PENALISE THE SAME DOUBT TWICE

Data confidence already flows into conviction (references/conviction.md is the
normative source: below 40 the reason code DATA_CONFIDENCE_LOW caps conviction
at LOW). If this module also multiplied the size by a data-confidence factor,
weak data would be charged for twice - once through a lowered ceiling and once
through a lowered multiplier - and the second charge would be invisible.

So every uncertainty source has exactly one owner:

    source                          owner                      effect here
    ------------------------------  -------------------------  -------------
    data confidence score           conviction ladder          ceiling, and a
                                                               hard stop below
                                                               the floor of 40
    unresolved source conflict      reason code -> conviction  ceiling / stop
    fired thesis breaker            reason code -> conviction  hard stop
    microcap venue                  reason code -> conviction  ceiling
    probability robustness          this module                uncertainty
    valuation band width            this module                uncertainty
    thesis confidence               this module                uncertainty
    probability support             this module                uncertainty
    bear-case loss                  risk budget                separate cap
    turnover, market cap            liquidity cap              separate cap
    portfolio weights               portfolio caps             separate cap
    forecast track record           forecast_scoring.py        inert today

Each uncertainty input is charged exactly once, and an input that could not
be checked is charged the incomplete-inputs factor once in total however many
are missing - not once per missing input, which would compound absence into a
penalty larger than any single known weakness.

`confidence.data` is accepted on input and deliberately NOT applied; it is
echoed back under `owned_elsewhere` so the non-application is visible in the
JSON rather than being a claim in a comment.

The liquidity cap is not a double charge on a microcap even though
VENUE_MICROCAP also caps conviction: one is about how sure the analysis is,
the other about whether the position can be got out of. Both can bind.

HARD CAPS LIVE HERE, IN CODE

The absolute ceilings below cannot be raised by configuration, by a decision
record, or by a model. This is the single home for those numbers: no Markdown
file restates them.

The rule `_harden()` enforces, stated exactly, because "a config can only
tighten" was loose enough to be misleading:

  * A value with a HARD_ constant may be configured anywhere between its
    default and that constant, never past it. The single-name cap defaults to
    8% and may be raised to 10%, which is the hard ceiling and the end of it.
  * A value with no HARD_ constant is clamped at its own default in whichever
    direction loosens the engine - so the uncertainty floors, the
    concentration multipliers and the rebalance thresholds can be made
    stricter and cannot be made laxer.
  * The hard stops in `no_bet` and the risk budget cannot be switched off at
    all. A cap a config file can disable is not a cap.
  * The one deliberate exception is `allow_full_kelly`, which raises the Kelly
    fraction ceiling and must be set by name. It cannot reach past any cap
    below it, and in practice nothing below it lets Kelly bind anyway.

`_harden()` is applied to a caller-supplied config as well as to a config
file, so the guarantee is not merely a property of the CLI.

WHAT THIS IS NOT

It is not a claim to an optimal position. Kelly's optimality assumes a known,
stationary, repeated distribution; an equity thesis is none of those. Read the
output as a risk-adjusted sizing estimate with its uncertainty shown, not as a
correct number. `status`, `not_checked` and `binding_constraint` exist so the
reader can see which of those it is.

NO NETWORK, NO FILESYSTEM WRITES. The only file it may read is an optional
config; portfolio and liquidity figures are handed in by the caller.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import _bootstrap  # noqa: E402

# By file path, not `import kelly`: a bare sibling import puts the module in
# sys.modules, which is exactly what tests/helpers.load's fresh-module-per-call
# design exists to prevent. kelly is not optional here, so this is a hard load.
kelly_mod = _bootstrap.load("kelly")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


# --------------------------------------------------------------------------
# Hard caps. Not configurable upward. Fractions of portfolio value.
# --------------------------------------------------------------------------

HARD_MAX_SINGLE_POSITION = 0.10
HARD_MAX_SECTOR_EXPOSURE = 0.35
# Country is deliberately not capped by default. This is a Sweden-first tool
# whose user holds a Swedish portfolio; a 40% country limit would refuse every
# domestic name on the first call, which is a broken default rather than a
# risk control. The machinery exists and binds as soon as a config sets it.
HARD_MAX_COUNTRY_EXPOSURE = 1.00
HARD_MAX_GROSS_EXPOSURE = 1.00

# The risk budget: the most of the whole portfolio one name may lose in its
# own bear case. This is the plugin's standing doctrine - size on bear-case
# loss, not on volatility - expressed as a cap rather than as advice.
HARD_MAX_POSITION_LOSS = 0.05

# Full Kelly is never the default and is only reachable by setting
# allow_full_kelly explicitly; without it the fraction is clamped here.
HARD_MAX_KELLY_FRACTION = 0.50

# Conviction is confidence in the analysis. This is what that confidence is
# worth as a share of the portfolio. The ladder itself lives in
# references/conviction.md; these ceilings live here and nowhere else.
CONVICTION_POSITION_CEILING = {
    "VERY HIGH": 0.08,
    "HIGH": 0.06,
    "MEDIUM": 0.04,
    "LOW": 0.02,
    "VERY LOW": 0.00,
}

CONVICTIONS = ("VERY LOW", "LOW", "MEDIUM", "HIGH", "VERY HIGH")

# What an unstated conviction is worth: the lowest rung that still permits a
# position. Never the highest - see conviction_cap.
UNSTATED_CONVICTION_CEILING = "LOW"

ACTIONS = ("NO_BET", "WATCH", "INITIATE", "ADD", "HOLD", "TRIM", "EXIT")

CONCENTRATION_LEVELS = ("LOW", "MEDIUM", "HIGH", "UNKNOWN")

OK = "ok"
NO_BET = "no_bet"
INSUFFICIENT_DATA = "insufficient_data"

CONFIG_ENV = "POSITION_SIZING_CONFIG"


class SizingError(ValueError):
    """An input this engine cannot interpret. Distinct from a refusal to bet."""


# --------------------------------------------------------------------------
# Configuration. Small on purpose; every value here has a defensible default.
# --------------------------------------------------------------------------

DEFAULTS = {
    # A quarter Kelly is the standing house figure (it was already the written
    # rule before this module existed). It rises with conviction, never above
    # HARD_MAX_KELLY_FRACTION.
    "kelly_fraction": 0.25,
    "kelly_fraction_by_conviction": {
        "VERY HIGH": 0.50,
        "HIGH": 0.35,
        "MEDIUM": 0.25,
        "LOW": 0.25,
        "VERY LOW": 0.25,
    },
    "allow_full_kelly": False,

    # Below min_position a target is not worth the ticket and the position is
    # watched instead. rebalance_band stops a 10bp drift becoming a trade.
    "min_position": 0.005,
    "rebalance_band": 0.005,
    "exit_below": 0.0025,

    "uncertainty": {
        # The product of the sub-factors can never take the size below this.
        "floor": 0.35,
        # How much probability mass is moved from the best scenario to the
        # worst when testing how fragile the Kelly figure is.
        "probability_shift": 0.10,
        "robustness_floor": 0.50,
        "estimate_width_floor": 0.55,
        "thesis_floor": 0.50,
        # A band as wide as the base value itself scores zero confidence.
        "wide_band": 1.00,
        # Charged once, however many checks could not run.
        "incomplete_inputs": 0.90,
    },

    "liquidity": {
        # Share of a day's traded value this position may represent, and how
        # many days a full exit may take.
        "participation_rate": 0.20,
        "exit_days": 5,
        "microcap_sek": 500_000_000,
        "microcap_max_position": 0.02,
    },

    # A separate concept from the caps below, and the seam a real risk-budget
    # allocator would attach to later: today it is one number, the share of
    # the portfolio a single name may lose in its bear case.
    "risk_budget": {
        "enabled": True,
        "max_position_loss": 0.02,
    },

    "probability": {
        # Refuse outright where the probabilities have no stated basis. Off by
        # default: most existing callers do not yet declare one, and turning
        # this on would refuse every legacy request. Off, the missing basis is
        # still visible and still charged once through incomplete_inputs.
        "require_basis": False,
    },

    "portfolio": {
        "max_single_position": 0.08,
        "max_sector_exposure": 0.25,
        # Off by default; see HARD_MAX_COUNTRY_EXPOSURE.
        "max_country_exposure": 1.00,
        "max_gross_exposure": 1.00,
    },

    # No fake precision: a correlation matrix the plugin does not have is not
    # invented. Overlap is graded, and the grade carries a multiplier.
    "concentration": {
        "low": 1.00,
        "medium": 0.85,
        "high": 0.70,
        "unknown": 0.90,
    },

    "no_bet": {
        "min_data_confidence": 40,
        "critical_conflict": True,
        "active_thesis_breaker": True,
        "blocking_reason_codes": True,
        "conviction_floor": "LOW",
    },
}


def _deep_merge(base, over):
    out = copy.deepcopy(base)
    for key, val in (over or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def _harden(cfg):
    """Clamp every configurable value so an override can only tighten it.

    The one deliberate exception is `allow_full_kelly`, which a config may set
    and which loosens the Kelly fraction ceiling. It is a switch a person has
    to reach for by name, and it cannot reach past any cap below it.

    This function is the single point of enforcement, so a key added to
    DEFAULTS and forgotten here silently voids the guarantee. Every section is
    type-checked first: a section set to null used to reach `size()` and fail
    with an AttributeError rather than a refusal.
    """
    for section in ("kelly_fraction_by_conviction", "uncertainty", "liquidity",
                    "portfolio", "concentration", "no_bet", "risk_budget",
                    "probability"):
        if not isinstance(cfg.get(section), dict):
            raise SizingError("config section %r must be an object" % section)

    pf = cfg["portfolio"]
    pf["max_single_position"] = min(float(pf["max_single_position"]),
                                    HARD_MAX_SINGLE_POSITION)
    pf["max_sector_exposure"] = min(float(pf["max_sector_exposure"]),
                                    HARD_MAX_SECTOR_EXPOSURE)
    pf["max_country_exposure"] = min(float(pf["max_country_exposure"]),
                                     HARD_MAX_COUNTRY_EXPOSURE)
    pf["max_gross_exposure"] = min(float(pf["max_gross_exposure"]),
                                   HARD_MAX_GROSS_EXPOSURE)

    ceiling = 1.0 if cfg.get("allow_full_kelly") else HARD_MAX_KELLY_FRACTION
    cfg["kelly_fraction"] = min(float(cfg["kelly_fraction"]), ceiling)
    by_conv = cfg["kelly_fraction_by_conviction"]
    for level in CONVICTIONS:
        if level not in by_conv:
            raise SizingError("kelly_fraction_by_conviction lacks %r" % level)
        by_conv[level] = min(float(by_conv[level]), ceiling)
    # Zero is not "very conservative", it is a fraction kelly.fractional
    # refuses - which surfaced as a raw KellyError traceback rather than a
    # refusal. A caller who wants no position says NO_BET, not 0x Kelly.
    for label, value in ([("kelly_fraction", cfg["kelly_fraction"])]
                         + [("kelly_fraction_by_conviction[%s]" % k, v)
                            for k, v in by_conv.items()]):
        if value <= 0.0:
            raise SizingError("%s must be above zero (got %g)"
                              % (label, value))

    # Every factor below multiplies the size, so RAISING one loosens the
    # engine. Each is therefore clamped at its own default, not merely at 1.0:
    # a config setting uncertainty.floor to 1.0 switched the entire
    # uncertainty layer off while still passing the old [0,1] clamp.
    unc, udef = cfg["uncertainty"], DEFAULTS["uncertainty"]
    for key in ("floor", "robustness_floor", "estimate_width_floor",
                "thesis_floor", "incomplete_inputs"):
        unc[key] = min(max(float(unc[key]), 0.0), udef[key])
    # A larger shift is a harsher stress test, so raising it is allowed;
    # lowering it toward zero would make every forecast look robust.
    unc["probability_shift"] = min(max(float(unc["probability_shift"]),
                                       udef["probability_shift"]), 0.5)
    # A wider "wide band" makes any band look narrow, so it may only shrink.
    unc["wide_band"] = min(max(float(unc["wide_band"]), 1e-6),
                           udef["wide_band"])

    con, cdef = cfg["concentration"], DEFAULTS["concentration"]
    for key in list(cdef):
        con[key] = min(max(float(con.get(key, cdef[key])), 0.0), cdef[key])

    # Sizing thresholds, and the two directions are not the same.
    # A LARGER min_position is conservative - more targets fall below it and
    # become WATCH - so it may rise but not fall.
    cfg["min_position"] = max(float(cfg["min_position"]),
                              DEFAULTS["min_position"])
    # A larger rebalance_band suppresses trades in both directions, including
    # a TRIM that should have happened; a larger exit_below moves the line
    # between "held" and "not held", which turned a broken thesis on a real
    # position from EXIT into NO_BET. Both may only shrink.
    for key in ("rebalance_band", "exit_below"):
        cfg[key] = min(max(float(cfg[key]), 0.0), DEFAULTS[key])
    if cfg["exit_below"] >= cfg["min_position"]:
        raise SizingError("exit_below (%g) must be below min_position (%g)"
                          % (cfg["exit_below"], cfg["min_position"]))

    rb = cfg["risk_budget"]
    # The budget itself may be tightened but never switched off: a config that
    # could disable a cap is not a cap.
    rb["enabled"] = True
    rb["max_position_loss"] = min(max(float(rb["max_position_loss"]), 0.0),
                                  HARD_MAX_POSITION_LOSS)

    # The hard stops are hard. Without this a config file could set four
    # booleans and size a company whose thesis has already broken - which is
    # exactly the claim the docstring makes and, until this was added, did not
    # keep. Only tightening is allowed: a floor may be raised, never lowered.
    stops, defaults = cfg["no_bet"], DEFAULTS["no_bet"]
    for flag in ("critical_conflict", "active_thesis_breaker",
                 "blocking_reason_codes"):
        stops[flag] = True
    floor = stops.get("min_data_confidence")
    stops["min_data_confidence"] = (defaults["min_data_confidence"]
                                    if floor is None
                                    else max(float(floor),
                                             defaults["min_data_confidence"]))
    conv_floor = stops.get("conviction_floor")
    if (conv_floor not in CONVICTIONS
            or CONVICTIONS.index(conv_floor)
            < CONVICTIONS.index(defaults["conviction_floor"])):
        stops["conviction_floor"] = defaults["conviction_floor"]

    liq = cfg["liquidity"]
    # Each of these loosens the liquidity cap as it rises, so each is clamped
    # at its default rather than merely at a bound.
    ldef = DEFAULTS["liquidity"]
    liq["participation_rate"] = min(max(float(liq["participation_rate"]), 0.0),
                                    ldef["participation_rate"])
    liq["exit_days"] = min(max(float(liq["exit_days"]), 0.0),
                           ldef["exit_days"])
    liq["microcap_max_position"] = min(float(liq["microcap_max_position"]),
                                       ldef["microcap_max_position"])
    # A higher threshold catches more companies, so raising it is the
    # conservative direction and is allowed; lowering it is not.
    liq["microcap_sek"] = max(float(liq["microcap_sek"]), ldef["microcap_sek"])
    return cfg


def config_path():
    """Optional config file. Absent is the normal case."""
    override = os.environ.get(CONFIG_ENV)
    if override:
        return os.path.expandvars(os.path.expanduser(override))
    return os.path.join(_bootstrap.state_home(), "config",
                        "position-sizing.json")


def load_config(overrides=None, path=None):
    """DEFAULTS, then the config file if there is one, then explicit overrides.

    Every layer is hardened afterwards, so no layer can loosen a hard cap.
    """
    cfg = copy.deepcopy(DEFAULTS)
    target = path if path is not None else config_path()
    if target and os.path.exists(target):
        try:
            with open(target, encoding="utf-8") as fh:
                cfg = _deep_merge(cfg, json.load(fh))
        except (OSError, json.JSONDecodeError) as exc:
            raise SizingError("config at %s is unreadable - %s" % (target, exc))
    if overrides:
        cfg = _deep_merge(cfg, overrides)
    return _harden(cfg)


# --------------------------------------------------------------------------
# Input normalisation
# --------------------------------------------------------------------------

def _num(value, label, allow_none=True):
    if value is None:
        if allow_none:
            return None
        raise SizingError("%s is required" % label)
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise SizingError("%s: not a number (%r)" % (label, value))
    if out != out or out in (float("inf"), float("-inf")):
        raise SizingError("%s: not finite" % label)
    return out


def _bounded(value, label, low, high):
    """A number on a declared scale, refused rather than clamped if it is off
    that scale. The two confidence scales differ, and a value silently moved
    onto the wrong one changes the position size without leaving a trace."""
    out = _num(value, label)
    if out is None:
        return None
    if out < low or out > high:
        raise SizingError("%s: %g is outside its scale [%g, %g] - the data "
                          "score runs 0-100 and the others 0-1"
                          % (label, out, low, high))
    return out


def _weight(value, label, maximum=1.0):
    """A portfolio weight, as a fraction. Percent is refused, not converted.

    This used to read anything above 1 as percent. That guess was wrong in the
    one case the guess mattered: a gross exposure of 1.30 - a 130%-geared
    portfolio, exactly what the gross cap exists for - became 0.013, and the
    limit vanished at the moment it should have bound. Weights below 1 are
    ambiguous between the two scales and cannot be guessed either way, so the
    contract is fractions and anything else is refused.
    """
    out = _num(value, label)
    if out is None:
        return None
    if out < 0:
        raise SizingError("%s: negative weight (%s)" % (label, out))
    if out > maximum:
        raise SizingError(
            "%s: %g exceeds %g - weights are fractions of the portfolio, not "
            "percentages; divide a percentage by 100 before passing it"
            % (label, out, maximum))
    return out


def normalise_reason_codes(entries):
    """Accept bare strings or {code, severity} objects, as the record does.

    A bare string defaults to WARN, exactly as decision_record.validate does,
    so the same input means the same thing in both places.
    """
    out = []
    for entry in entries or []:
        if isinstance(entry, str):
            out.append({"code": entry, "severity": "WARN"})
        elif isinstance(entry, dict) and entry.get("code"):
            out.append({"code": str(entry["code"]),
                        "severity": str(entry.get("severity") or "WARN")})
        else:
            raise SizingError("reason code %r is neither a code nor an object"
                              % (entry,))
    return out


def normalise_request(request):
    """Validate the minimal input contract and fill in what is absent."""
    if not isinstance(request, dict):
        raise SizingError("request must be an object")

    conviction = request.get("conviction")
    if conviction is not None:
        conviction = str(conviction).upper().strip()
        if conviction not in CONVICTIONS:
            raise SizingError("conviction %r is not one of %s"
                              % (conviction, ", ".join(CONVICTIONS)))

    confidence = request.get("confidence") or {}
    if not isinstance(confidence, dict):
        raise SizingError("confidence must be an object")

    portfolio = request.get("portfolio") or {}
    if not isinstance(portfolio, dict):
        raise SizingError("portfolio must be an object")

    liquidity = request.get("liquidity") or {}
    if not isinstance(liquidity, dict):
        raise SizingError("liquidity must be an object")

    valuation = request.get("valuation") or {}
    if not isinstance(valuation, dict):
        raise SizingError("valuation must be an object")

    return {
        "instrument": request.get("instrument"),
        "as_of": request.get("as_of"),
        "depth": request.get("depth"),
        "scenarios": request.get("scenarios"),
        "probability_basis": (str(request["probability_basis"]).lower()
                              if request.get("probability_basis") else None),
        "calibration_table": request.get("calibration_table"),
        "conviction": conviction,
        "reason_codes": normalise_reason_codes(request.get("reason_codes")),
        "thesis_status": (str(request["thesis_status"]).upper()
                          if request.get("thesis_status") else None),
        # Two scales in one object, so both are range-checked rather than
        # clamped. Clamping made the two most damaging typos silent: a
        # thesis confidence of 70 (meant as 70%) clamped to a perfect 1.0 and
        # sized the position larger, and a data confidence of 0.78 fell under
        # the floor of 40 and produced a spurious NO_BET.
        "confidence": {
            "data": _bounded(confidence.get("data"), "confidence.data",
                             0.0, 100.0),
            "thesis": _bounded(confidence.get("thesis"), "confidence.thesis",
                               0.0, 1.0),
            "valuation": _bounded(confidence.get("valuation"),
                                  "confidence.valuation", 0.0, 1.0),
        },
        "valuation": {
            "price": _num(valuation.get("price"), "valuation.price"),
            "base_low": _num(valuation.get("base_low"), "valuation.base_low"),
            "base_high": _num(valuation.get("base_high"),
                              "valuation.base_high"),
        },
        "liquidity": {
            "adv_sek": _num(liquidity.get("adv_sek"), "liquidity.adv_sek"),
            "turnover_sek": _num(liquidity.get("turnover_sek"),
                                 "liquidity.turnover_sek"),
            "market_cap_sek": _num(liquidity.get("market_cap_sek"),
                                   "liquidity.market_cap_sek"),
        },
        "portfolio": {
            "present": bool(portfolio),
            "total_value_sek": _num(portfolio.get("total_value_sek"),
                                    "portfolio.total_value_sek"),
            "current_weight": _weight(portfolio.get("current_weight"),
                                      "portfolio.current_weight") or 0.0,
            "sector": portfolio.get("sector"),
            "sector_weight": _weight(portfolio.get("sector_weight"),
                                     "portfolio.sector_weight"),
            "country": portfolio.get("country"),
            "country_weight": _weight(portfolio.get("country_weight"),
                                      "portfolio.country_weight"),
            # A geared portfolio legitimately exceeds 1.0 here, and that is
            # precisely when the gross cap has work to do.
            "gross_weight": _weight(portfolio.get("gross_weight"),
                                    "portfolio.gross_weight", maximum=3.0),
            "cash_weight": _weight(portfolio.get("cash_weight"),
                                   "portfolio.cash_weight"),
            "shares_sector_with": int(portfolio.get("shares_sector_with") or 0),
            "shares_driver_with": int(portfolio.get("shares_driver_with") or 0),
        },
    }


# --------------------------------------------------------------------------
# Hard stops
# --------------------------------------------------------------------------

def no_bet_reasons(req, cfg, kelly_result=None):
    """Conditions under which no size is defensible, whatever Kelly says.

    Returned as a list of {code, detail} so the caller can print the reason
    rather than an unexplained zero.
    """
    stops = []
    rules = cfg["no_bet"]
    codes = {c["code"] for c in req["reason_codes"]}

    if rules.get("blocking_reason_codes"):
        blocking = sorted(c["code"] for c in req["reason_codes"]
                          if c["severity"] == "BLOCK")
        if blocking:
            stops.append({"code": "BLOCKING_REASON_CODE",
                          "detail": "the number the thesis rests on cannot "
                                    "stand as stated: " + ", ".join(blocking)})

    if rules.get("active_thesis_breaker"):
        if "THESIS_BROKEN" in codes or req["thesis_status"] == "BROKEN":
            stops.append({"code": "THESIS_BREAKER_ACTIVE",
                          "detail": "a stored breaker has fired; the thesis "
                                    "is broken, not merely uncertain"})

    if rules.get("critical_conflict") and "CONFLICT_UNRESOLVED" in codes:
        stops.append({"code": "CONFLICT_UNRESOLVED",
                      "detail": "an unresolved conflict on a material figure"})

    data_conf = req["confidence"]["data"]
    floor = rules.get("min_data_confidence")
    if data_conf is not None and floor is not None and data_conf < floor:
        stops.append({"code": "DATA_CONFIDENCE_BELOW_FLOOR",
                      "detail": "data confidence %g is below the floor of %g"
                                % (data_conf, floor)})
    if "DATA_CONFIDENCE_LOW" in codes and floor is not None:
        stops.append({"code": "DATA_CONFIDENCE_BELOW_FLOOR",
                      "detail": "reason code DATA_CONFIDENCE_LOW is set"})

    conviction = req["conviction"]
    conv_floor = rules.get("conviction_floor")
    if conviction is not None and conv_floor:
        if CONVICTIONS.index(conviction) < CONVICTIONS.index(conv_floor):
            stops.append({"code": "CONVICTION_BELOW_FLOOR",
                          "detail": "conviction %s is below the floor of %s"
                                    % (conviction, conv_floor)})

    liq = req["liquidity"]
    traded = liq["adv_sek"] if liq["adv_sek"] is not None else liq["turnover_sek"]
    if traded is not None:
        floor_sek = _liquidity_floor_sek()
        if floor_sek is not None and traded < floor_sek:
            stops.append({"code": "LIQUIDITY_BELOW_FLOOR",
                          "detail": "traded value %.0f SEK is below the "
                                    "universe floor of %.0f SEK"
                                    % (traded, floor_sek)})

    if kelly_result is not None:
        if kelly_result["status"] == kelly_mod.NO_BET:
            stops.append({"code": "NEGATIVE_EDGE",
                          "detail": "expected return is not positive"})

    # Deduplicate on code, keeping the first detail.
    seen, out = set(), []
    for stop in stops:
        if stop["code"] not in seen:
            seen.add(stop["code"])
            out.append(stop)
    return out


PROBABILITY_SUPPORTED = "SUPPORTED"
PROBABILITY_ASSERTED = "ASSERTED"
PROBABILITY_UNKNOWN = "UNKNOWN"


def probability_support(req):
    """Whether the probabilities rest on anything, or were simply stated.

    The plugin's rule is refuse-never-guess, and a probability is a figure
    like any other: 25/50/25 chosen so the arithmetic comes out is not a
    forecast. There is no way to verify a belief from here, so this does the
    one honest thing available - it reports whether a basis was declared, and
    treats an undeclared one as unchecked rather than as clean.

    SUPPORTED requires either an explicit probability_basis of
    "scenario_analysis", or a stated basis on every single scenario.
    """
    basis = req["probability_basis"]
    if basis == "scenario_analysis":
        return PROBABILITY_SUPPORTED
    if basis == "asserted":
        return PROBABILITY_ASSERTED
    rows = req["scenarios"] or []
    if rows and all(isinstance(r, dict) and r.get("basis") for r in rows):
        return PROBABILITY_SUPPORTED
    return PROBABILITY_UNKNOWN


def _scenario_extras(scenarios):
    """Per-scenario qualitative fields, carried through and never invented."""
    extras = {}
    for i, row in enumerate(scenarios or []):
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or ("s%d" % i))
        extras[name] = {"horizon": row.get("horizon"),
                        "confidence": row.get("confidence"),
                        "basis": row.get("basis")}
    return extras


def _liquidity_floor_sek():
    """The universe liquidity floor, read from its one home if reachable."""
    mod = _bootstrap.soft_load("market_universe")
    if mod is None:
        return None
    return getattr(mod, "DEFAULT_LIQUIDITY_FLOOR_SEK", None)


# --------------------------------------------------------------------------
# Uncertainty adjustment
# --------------------------------------------------------------------------

def _scaled(x, floor):
    """Map a 0-1 confidence onto [floor, 1]. Monotone increasing by design."""
    x = min(max(x, 0.0), 1.0)
    return floor + (1.0 - floor) * x


def probability_robustness(scenarios, cfg):
    """How much of the Kelly figure survives the probability being wrong.

    `probability_shift` of the mass is moved from the best-return scenario to
    the worst-return one and Kelly recomputed. The ratio of the stressed
    figure to the original is the robustness: a bet that halves under a 10pp
    error was never really a 4% position.

    This is a real recomputation, not a coefficient. Where it cannot be run
    (Kelly itself unavailable) it returns None and the caller records the
    check as not run rather than scoring it clean.
    """
    shift = cfg["uncertainty"]["probability_shift"]
    try:
        base = kelly_mod.kelly_scenarios(scenarios)
    except kelly_mod.KellyError:
        return None, None
    # Compare the UNCLAMPED figures. Both the base and the stressed Kelly are
    # clamped at full capital, and for an ordinary equity spread both sit at
    # exactly 1.0 - so the ratio read 1.00, "perfectly robust", precisely in
    # the common case where the real edge may have halved.
    base_f = base.get("unclamped_kelly") or base["raw_kelly"]
    if base["status"] != kelly_mod.OK or not base_f:
        return None, base

    rows = kelly_mod.validate_scenarios(scenarios)
    best = max(rows, key=lambda s: s["return"])
    worst = min(rows, key=lambda s: s["return"])
    if best is worst:
        return None, base
    movable = min(shift, best["probability"])
    if movable <= 0:
        return 1.0, base
    stressed = []
    for row in rows:
        p = row["probability"]
        if row is best:
            p -= movable
        elif row is worst:
            p += movable
        stressed.append({"name": row["name"], "probability": p,
                         "return": row["return"]})
    try:
        after = kelly_mod.kelly_scenarios(stressed)
    except kelly_mod.KellyError:
        return None, base
    if after["status"] != kelly_mod.OK or after["raw_kelly"] is None:
        return 0.0, base
    after_f = after.get("unclamped_kelly") or after["raw_kelly"]
    return min(after_f / base_f, 1.0), base


def estimate_width_confidence(req, cfg):
    """Confidence in the point estimate, from the width of the base band.

    An explicit `confidence.valuation` wins where the caller has one; the band
    is the fallback so a decision record that carries base_low and base_high
    is not asked for a second, softer number saying the same thing.
    """
    explicit = req["confidence"]["valuation"]
    if explicit is not None:
        return min(max(explicit, 0.0), 1.0), "confidence.valuation"
    val = req["valuation"]
    low, high = val["base_low"], val["base_high"]
    if low is None or high is None:
        return None, None
    if low <= 0 or high < low:
        raise SizingError("valuation band %s-%s is not a band" % (low, high))
    mid = 0.5 * (low + high)
    if mid <= 0:
        return None, None
    relative = (high - low) / mid
    wide = cfg["uncertainty"]["wide_band"]
    if wide <= 0:
        return None, None
    return min(max(1.0 - relative / wide, 0.0), 1.0), "valuation band"


def calibrate(scenarios, req):
    """raw probability -> optional adjustment -> calibrated probability.

    Identity in every case today. forecast_scoring.calibration_table refuses
    to produce adjustments below its sample floor, and nothing in this
    repository passes a table in automatically - a caller must supply one
    deliberately. Returns (scenarios, status) so the JSON can say
    "unavailable" instead of leaving the reader to assume it was applied.
    """
    table = req.get("calibration_table")
    scoring = _bootstrap.soft_load("forecast_scoring")
    if scoring is None:
        return scenarios, {"status": "unavailable",
                           "reason": "forecast_scoring is not loadable"}
    rows = kelly_mod.validate_scenarios(scenarios)
    forecast = {r["name"]: r["probability"] for r in rows}
    try:
        adjusted, status = scoring.apply_calibration(forecast, table)
    except scoring.ScoringError as exc:
        raise SizingError("calibration table is unusable - %s" % exc)
    if status != scoring.AVAILABLE:
        return scenarios, {"status": "unavailable",
                           "reason": (table or {}).get("reason")
                                     if isinstance(table, dict)
                                     else "no calibration table supplied"}
    out = []
    for row in rows:
        out.append({"name": row["name"], "return": row["return"],
                    "probability": adjusted[row["name"]]})
    return out, {"status": "applied", "n": (table or {}).get("n")}


def uncertainty_adjustment(req, cfg, scenarios):
    """The one place estimate fragility is priced. Never prices data quality.

    Sub-factors multiply. They are independent kinds of fragility - a fragile
    probability and a wide valuation band are two different ways to be wrong -
    so compounding them is intended, and the product is floored so it can
    never quietly take the size to nothing.
    """
    unc = cfg["uncertainty"]
    components = {}
    not_checked = []

    def unchecked(floor):
        """What an input we do not have is worth.

        Not 1.0. Scoring an absent input as perfect made omission strictly
        better than honesty: on the fixture, dropping confidence.thesis gave a
        LARGER position than stating 0.70, and the caller supplying these
        fields is the model itself. The stand-in is the factor a confidence of
        0.5 would earn - no information, not good news - and the separate
        incomplete-inputs charge is still made once in total.

        It is not the floor either. Making absence worse than the worst
        possible stated value would just push a caller into inventing a high
        number instead, which is a worse failure than a cautious size.
        """
        return _scaled(0.5, floor)

    robustness, _base = probability_robustness(scenarios, cfg)
    if robustness is None:
        not_checked.append("probability_robustness")
        components["probability_robustness"] = {
            "factor": unchecked(unc["robustness_floor"]),
            "input": None, "checked": False}
    else:
        components["probability_robustness"] = {
            "factor": _scaled(robustness, unc["robustness_floor"]),
            "input": robustness, "checked": True}

    width, width_source = estimate_width_confidence(req, cfg)
    if width is None:
        not_checked.append("estimate_width")
        components["estimate_width"] = {
            "factor": unchecked(unc["estimate_width_floor"]),
            "input": None, "checked": False, "source": None}
    else:
        components["estimate_width"] = {
            "factor": _scaled(width, unc["estimate_width_floor"]),
            "input": width, "checked": True, "source": width_source}

    thesis = req["confidence"]["thesis"]
    if thesis is None:
        not_checked.append("thesis_confidence")
        components["thesis_confidence"] = {
            "factor": unchecked(unc["thesis_floor"]),
            "input": None, "checked": False}
    else:
        components["thesis_confidence"] = {
            "factor": _scaled(min(max(thesis, 0.0), 1.0), unc["thesis_floor"]),
            "input": thesis, "checked": True}

    support = probability_support(req)
    supported = support == PROBABILITY_SUPPORTED
    components["probability_support"] = {
        "factor": 1.0 if supported else unchecked(unc["thesis_floor"]),
        "input": support, "checked": supported}
    if not supported:
        not_checked.append("probability basis")

    incomplete = unc["incomplete_inputs"] if not_checked else 1.0
    components["incomplete_inputs"] = {"factor": incomplete,
                                       "input": len(not_checked),
                                       "checked": True}

    product = 1.0
    for comp in components.values():
        product *= comp["factor"]
    factor = max(product, unc["floor"])

    return {
        "factor": factor,
        "floored": product < unc["floor"],
        "components": components,
        "not_checked": not_checked,
        "owned_elsewhere": {
            "data_confidence": "conviction ceiling - not applied again here",
        },
    }


# --------------------------------------------------------------------------
# Caps
# --------------------------------------------------------------------------

def conviction_cap(conviction, depth=None, reason_codes=None):
    """Position ceiling from conviction, never above the enforced ceiling.

    Where depth and reason codes are supplied the existing
    decision_record.conviction_ceiling is consulted, so a Kelly figure cannot
    walk past a cap the decision record would have enforced. That module is
    the one home for the ceiling; this one only maps its answer to a weight.
    """
    effective = conviction
    ceiling_detail = None
    if depth or reason_codes:
        rec = _bootstrap.soft_load("decision_record")
        if rec is not None:
            ceiling, caps = rec.conviction_ceiling(depth, reason_codes or [])
            ceiling_detail = {"ceiling": ceiling, "caps_applied": caps}
            if (effective is not None
                    and CONVICTIONS.index(effective)
                    > CONVICTIONS.index(ceiling)):
                effective = ceiling
    if effective is None:
        # A caller who states no conviction does not thereby earn the highest
        # ceiling. That is what happened before: with no conviction and no cap
        # firing, decision_record returns VERY HIGH as its *unconstrained*
        # ceiling, and adopting it as an entitlement made declaring HIGH
        # strictly worse than declaring nothing. An unstated conviction is an
        # unrun check, and an unrun check is never a clean result.
        return (CONVICTION_POSITION_CEILING[UNSTATED_CONVICTION_CEILING],
                {"conviction": None, "assumed": UNSTATED_CONVICTION_CEILING,
                 "ceiling": ceiling_detail})
    return (CONVICTION_POSITION_CEILING[effective],
            {"conviction": effective, "ceiling": ceiling_detail})


def liquidity_cap(req, cfg):
    """How much can be held and still be exitable, plus a microcap ceiling.

    Participation model: a position may be no larger than
    `participation_rate` of a day's traded value times `exit_days`. Without a
    portfolio value there is no denominator, so the check is recorded as not
    run rather than being skipped silently.
    """
    liq, pf = req["liquidity"], req["portfolio"]
    cl = cfg["liquidity"]
    detail = {"basis": None, "adv_sek": None, "not_checked": []}

    traded = liq["adv_sek"]
    if traded is not None:
        detail["basis"] = "adv_sek"
    else:
        traded = liq["turnover_sek"]
        if traded is not None:
            detail["basis"] = "turnover_sek (one session, a proxy for ADV)"
    detail["adv_sek"] = traded

    caps = []
    if traded is not None and pf["total_value_sek"]:
        exitable = cl["participation_rate"] * traded * cl["exit_days"]
        caps.append(exitable / pf["total_value_sek"])
    elif traded is None:
        detail["not_checked"].append("traded value")
    else:
        detail["not_checked"].append("portfolio value")

    mcap = liq["market_cap_sek"]
    if mcap is None:
        detail["not_checked"].append("market cap")
    elif mcap < cl["microcap_sek"]:
        caps.append(cl["microcap_max_position"])
        detail["microcap"] = True

    if not caps:
        return None, detail
    # A liquid large cap in a small portfolio produces a "cap" of several
    # hundred percent, which is true and useless. Reported at the whole
    # portfolio, so the number stays readable next to the others.
    return min(min(caps), 1.0), detail


def risk_budget_cap(kelly_result, cfg):
    """The most this name may cost the portfolio if its bear case happens.

    cap = max_position_loss / |worst scenario return|

    A name whose bear case is -60% gets half the size of one whose bear case
    is -30%, at the same edge - which is the rule the plugin already stated in
    prose and never enforced. Distinct from the uncertainty layer: this prices
    the size of the loss, not the reliability of the estimate.

    This is deliberately one number rather than a mean-variance allocator. The
    plugin has no dependable covariance matrix, and an optimiser fed guesses
    would be worse than a cap, not better. The interface is the seam: a
    portfolio-level budget allocator can later hand a per-name loss allowance
    in here without anything else in the pipeline changing.
    """
    if not cfg["risk_budget"].get("enabled"):
        return None, {"enabled": False}
    worst = kelly_result.get("worst_return")
    if worst is None or worst >= 0:
        return None, {"enabled": True, "not_checked": ["bear-case loss"]}
    allowance = cfg["risk_budget"]["max_position_loss"]
    # A bear case of -0.1% would make the allowance imply many times the
    # portfolio. Reported at the whole portfolio, as the liquidity cap is.
    return min(allowance / abs(worst), 1.0), {
        "enabled": True, "allowance": allowance, "bear_case_return": worst}


def portfolio_caps(req, cfg):
    """Headroom left by each portfolio limit, expressed as a position weight.

    Each limit is converted to "how large may THIS position be", which means
    subtracting the rest of the bucket rather than the whole bucket: a sector
    at 18% of which this name is already 6% leaves 25% - 12% = 13%.
    """
    pf, limits = req["portfolio"], cfg["portfolio"]
    current = pf["current_weight"]
    caps, detail = {}, {"not_checked": []}

    # A bucket weight must include this position. If it does not, the caller
    # has measured two different things, and `max(bucket - current, 0)` would
    # quietly hand back the whole limit as headroom - the loosest possible cap
    # from the most obviously wrong input.
    for label in ("sector_weight", "country_weight", "gross_weight"):
        bucket = pf[label]
        if bucket is not None and current > bucket + 1e-9:
            raise SizingError(
                "portfolio.%s (%g) is smaller than this position's own weight "
                "(%g) - a bucket weight must include the position"
                % (label, bucket, current))

    caps["single_name"] = limits["max_single_position"]

    if pf["sector_weight"] is None:
        caps["sector"] = None
        detail["not_checked"].append("sector exposure")
    else:
        others = max(pf["sector_weight"] - current, 0.0)
        caps["sector"] = max(limits["max_sector_exposure"] - others, 0.0)

    if pf["country_weight"] is None:
        caps["country"] = None
        detail["not_checked"].append("country exposure")
    else:
        others = max(pf["country_weight"] - current, 0.0)
        caps["country"] = max(limits["max_country_exposure"] - others, 0.0)

    if pf["gross_weight"] is None:
        caps["gross"] = None
        detail["not_checked"].append("gross exposure")
    else:
        others = max(pf["gross_weight"] - current, 0.0)
        caps["gross"] = max(limits["max_gross_exposure"] - others, 0.0)

    if pf["cash_weight"] is None:
        caps["cash"] = None
        detail["not_checked"].append("cash")
    else:
        caps["cash"] = current + pf["cash_weight"]

    return caps, detail


def concentration(req, cfg):
    """Graded overlap, not an invented correlation coefficient.

    The plugin has no covariance matrix and will not pretend to one. What it
    does have is sector and driver grouping from portfolio_metrics, and that
    supports LOW / MEDIUM / HIGH / UNKNOWN. The score is a sum of
    non-decreasing terms, so more overlap can never grade lower.
    """
    pf = req["portfolio"]
    if not pf["present"]:
        level = "UNKNOWN"
        return {"level": level, "multiplier": cfg["concentration"]["unknown"],
                "score": None, "drivers": ["no portfolio context supplied"]}

    score, drivers = 0, []
    max_sector = cfg["portfolio"]["max_sector_exposure"]
    sw = pf["sector_weight"]
    if sw is None:
        drivers.append("sector weight not known")
    elif sw >= 0.8 * max_sector:
        score += 2
        drivers.append("sector already at %.0f%% of its limit"
                       % (100.0 * sw / max_sector))
    elif sw >= 0.5 * max_sector:
        score += 1
        drivers.append("sector at %.0f%% of its limit"
                       % (100.0 * sw / max_sector))

    shared_sector = min(pf["shares_sector_with"], 2)
    if shared_sector:
        score += shared_sector
        drivers.append("shares a sector with %d holding(s)"
                       % pf["shares_sector_with"])
    shared_driver = min(pf["shares_driver_with"], 2)
    if shared_driver:
        score += shared_driver
        drivers.append("shares a driver with %d holding(s)"
                       % pf["shares_driver_with"])

    if score >= 3:
        level = "HIGH"
    elif score >= 1:
        level = "MEDIUM"
    else:
        level = "LOW"
        drivers.append("no material overlap found")
    return {"level": level,
            "multiplier": cfg["concentration"][level.lower()],
            "score": score, "drivers": drivers}


# --------------------------------------------------------------------------
# Action
# --------------------------------------------------------------------------

def decide_action(target, current, cfg, blocked=False):
    """Current versus target, in percentage points, with a deadband.

    A hard stop on a position already held is an EXIT, not a NO_BET: NO_BET is
    what you say about a position you do not have.
    """
    band, floor = cfg["rebalance_band"], cfg["exit_below"]
    held = current > floor

    if blocked:
        return ("EXIT" if held else "NO_BET"), target - current

    delta = target - current
    if target < cfg["min_position"]:
        if not held:
            # Too small to be worth a ticket, and nothing to sell.
            return "WATCH", delta
        # Held, but the target is below the size worth holding. Two failure
        # modes to avoid here, and they pull in opposite directions: trimming
        # to a weight this branch has just called not worth a ticket leaves a
        # stub that trims again forever, while exiting on every dip below the
        # threshold makes the pair (min_position, exit) oscillate - a target
        # wobbling either side of 0.5% would INITIATE and EXIT in turn. So the
        # position goes only when it is genuinely negligible or the move is
        # bigger than the deadband; otherwise it is left alone.
        if target < floor or delta < -band:
            return "EXIT", 0.0 - current
        return "HOLD", 0.0
    if not held:
        # Nothing is held, so there is nothing to hold. Without this the
        # deadband could answer HOLD on a position that does not exist -
        # which it did whenever target - current landed inside the band, and
        # always when min_position equals rebalance_band, as it does by
        # default.
        return "INITIATE", delta
    if delta > band:
        return "ADD", delta
    if delta < -band:
        return ("EXIT" if target < floor else "TRIM"), delta
    return "HOLD", delta


# --------------------------------------------------------------------------
# The pipeline
# --------------------------------------------------------------------------

def size(request, config=None, config_overrides=None, config_file=None):
    """Run the whole pipeline. Returns a compact result; never raises for a
    refusal (that is a status), only for an input it cannot read."""
    # A caller-supplied config is hardened too. It used to be trusted as-is,
    # which meant the whole "config can only tighten" guarantee applied to the
    # CLI and to nothing else - any in-process caller could hand in a dict
    # with every cap at 0.9 and the uncertainty layer switched off.
    if config is not None:
        cfg = _harden(copy.deepcopy(config))
    else:
        cfg = load_config(config_overrides, config_file)
    req = normalise_request(request)
    notes, not_checked = [], []

    result = {
        "status": OK,
        "instrument": req["instrument"],
        "as_of": req["as_of"],
        "method": None,
        "scenario_distribution": None,
        "probability_support": None,
        "calibration": {"status": "unavailable",
                        "reason": "not reached"},
        "raw_kelly": None,
        "kelly_fraction": None,
        "fractional_kelly": None,
        "uncertainty_factor": None,
        "adjusted_kelly": None,
        "caps": {},
        "concentration": None,
        "target": 0.0,
        "current": req["portfolio"]["current_weight"],
        "delta": 0.0,
        "action": "NO_BET",
        "binding_constraint": None,
        "constraints_applied": [],
        "no_bet_reasons": [],
        "reasons": [],
        "not_checked": [],
        "notes": notes,
    }

    # 1. Kelly. An unreadable distribution is an error; an unusable one is a
    #    status.
    kelly_result = None
    if req["scenarios"] is None:
        result["status"] = INSUFFICIENT_DATA
        result["no_bet_reasons"] = [{
            "code": "NO_SCENARIO_DISTRIBUTION",
            "detail": "no scenario distribution and no probability was "
                      "invented; supply bear/base/bull with weights"}]
        result["reasons"] = [result["no_bet_reasons"][0]["detail"]]
        result["action"] = ("EXIT" if req["portfolio"]["current_weight"] >
                            cfg["exit_below"] else "NO_BET")
        result["delta"] = result["target"] - result["current"]
        return result
    support = probability_support(req)
    result["probability_support"] = support
    # The refusal is raised below, alongside every other stop, rather than
    # returned here: an early return lost the Kelly figure and any other stop
    # that also applied, and "Kelly suggests 4.2% BUT ..." is the shape this
    # output is supposed to have.
    basis_stop = (cfg["probability"].get("require_basis")
                  and support != PROBABILITY_SUPPORTED)

    try:
        scenarios, calibration = calibrate(req["scenarios"], req)
        kelly_result = kelly_mod.kelly_scenarios(scenarios)
    except kelly_mod.KellyError as exc:
        raise SizingError("scenario distribution is invalid - %s" % exc)

    extras = _scenario_extras(req["scenarios"])
    result["calibration"] = calibration
    result["scenario_distribution"] = [
        dict(row, **{k: v for k, v in (extras.get(row["name"]) or {}).items()
                     if v is not None})
        for row in kelly_mod.validate_scenarios(scenarios)]
    result["method"] = kelly_result["method"]
    result["raw_kelly"] = kelly_result["raw_kelly"]
    result["expected_return"] = kelly_result["expected_return"]
    result["expected_downside"] = kelly_result["expected_downside"]
    notes.extend(kelly_result["notes"])

    # 2. Hard stops, evaluated against the Kelly outcome as well as the inputs.
    stops = no_bet_reasons(req, cfg, kelly_result)
    if _liquidity_floor_sek() is None:
        # The universe floor lives in market_universe. If that module cannot
        # be loaded the extreme-illiquidity stop cannot fire at all, and a
        # stop that silently stopped existing is worse than one that refuses.
        not_checked.append("universe liquidity floor")
    if basis_stop:
        result["status"] = INSUFFICIENT_DATA
        stops.append({
            "code": "PROBABILITY_BASIS_MISSING",
            "detail": "the probabilities carry no stated basis and this "
                      "configuration refuses an asserted distribution"})
    if kelly_result["status"] == kelly_mod.INSUFFICIENT_DATA:
        result["status"] = INSUFFICIENT_DATA
        stops.append({"code": "NO_DOWNSIDE_SCENARIO",
                      "detail": "the distribution has no losing case, so "
                                "Kelly is unbounded and is not guessed"})
    if stops:
        result["status"] = (INSUFFICIENT_DATA
                            if result["status"] == INSUFFICIENT_DATA
                            else NO_BET)
        result["no_bet_reasons"] = stops
        result["reasons"] = [s["detail"] for s in stops]
        result["binding_constraint"] = stops[0]["code"]
        result["not_checked"] = sorted(set(not_checked))
        result["target"] = 0.0
        result["action"], result["delta"] = decide_action(
            0.0, result["current"], cfg, blocked=True)
        # The Kelly figure is still reported: "Kelly suggests 4.2% BUT ..."
        # is the intended shape of this output, not a hidden zero.
        return result

    # 3. Fractional Kelly, by conviction where it is known.
    conviction = req["conviction"]
    fraction = cfg["kelly_fraction"]
    if conviction and conviction in cfg["kelly_fraction_by_conviction"]:
        fraction = cfg["kelly_fraction_by_conviction"][conviction]
    result["kelly_fraction"] = fraction
    result["fractional_kelly"] = kelly_mod.fractional(
        kelly_result["raw_kelly"], fraction)

    # 4. Uncertainty. Measured on the calibrated distribution, so the
    #    robustness test stresses the probabilities actually being used.
    unc = uncertainty_adjustment(req, cfg, scenarios)
    result["uncertainty_factor"] = unc["factor"]
    result["uncertainty"] = unc
    result["adjusted_kelly"] = result["fractional_kelly"] * unc["factor"]
    not_checked.extend(unc["not_checked"])

    # 5. Caps.
    conv_cap, conv_detail = conviction_cap(conviction, req["depth"],
                                           req["reason_codes"])
    if conv_detail.get("assumed"):
        not_checked.append("conviction")
    if ((req["depth"] or req["reason_codes"])
            and conv_detail.get("ceiling") is None):
        # decision_record could not be loaded, so the depth and reason-code
        # ceiling was never applied. Silence here would look like "no cap
        # fired" rather than "the cap was not consulted".
        not_checked.append("conviction ceiling")
    liq_cap, liq_detail = liquidity_cap(req, cfg)
    not_checked.extend(liq_detail["not_checked"])
    pf_caps, pf_detail = portfolio_caps(req, cfg)
    not_checked.extend(pf_detail["not_checked"])
    rb_cap, rb_detail = risk_budget_cap(kelly_result, cfg)
    not_checked.extend(rb_detail.get("not_checked") or [])

    caps = {"conviction": conv_cap, "liquidity": liq_cap,
            "risk_budget": rb_cap}
    caps.update(pf_caps)
    result["caps"] = caps
    result["cap_detail"] = {"conviction": conv_detail, "liquidity": liq_detail,
                            "risk_budget": rb_detail}

    capped = result["adjusted_kelly"]
    binding = None
    for name, value in sorted(caps.items()):
        if value is None:
            continue
        if value < capped:
            capped, binding = value, name
    # Hard caps are applied last and unconditionally; nothing above may raise
    # a position past them, whatever the configuration said.
    if capped > HARD_MAX_SINGLE_POSITION:
        capped, binding = HARD_MAX_SINGLE_POSITION, "hard_single_name"

    # 6. Concentration.
    conc = concentration(req, cfg)
    result["concentration"] = {"level": conc["level"],
                               "multiplier": conc["multiplier"],
                               "drivers": conc["drivers"]}
    if conc["level"] == "UNKNOWN":
        not_checked.append("portfolio concentration")
    target = capped * conc["multiplier"]

    # 7. Target and action.
    target = max(min(target, HARD_MAX_SINGLE_POSITION), 0.0)
    result["target"] = target
    result["action"], result["delta"] = decide_action(target,
                                                      result["current"], cfg)
    if result["action"] == "EXIT":
        # An EXIT means the whole position goes, so the target IS zero. Left
        # at the computed figure, the record persisted by telemetry() said
        # target 0.28%, delta -0.40% and current 0.40% - three numbers that do
        # not add up, stored forever.
        result["target"] = 0.0
        result["delta"] = -result["current"]

    applied = []
    if unc["factor"] < 1.0:
        applied.append("uncertainty")
    if binding:
        applied.append(binding)
    if conc["multiplier"] < 1.0:
        applied.append("concentration")
    result["constraints_applied"] = applied
    result["binding_constraint"] = binding or (
        "concentration" if conc["multiplier"] < 1.0
        else ("uncertainty" if unc["factor"] < 1.0 else None))
    result["not_checked"] = sorted(set(not_checked))
    result["hard_caps"] = {
        "single_name": HARD_MAX_SINGLE_POSITION,
        "sector": HARD_MAX_SECTOR_EXPOSURE,
        "country": HARD_MAX_COUNTRY_EXPOSURE,
        "gross": HARD_MAX_GROSS_EXPOSURE,
        "position_loss": HARD_MAX_POSITION_LOSS,
        "kelly_fraction": (1.0 if cfg.get("allow_full_kelly")
                           else HARD_MAX_KELLY_FRACTION),
    }

    reasons = []
    if unc["factor"] < 1.0:
        reasons.append("uncertainty scaled the size by %.2f"
                       % unc["factor"])
    if binding:
        cap_value = caps.get(binding)
        reasons.append("the %s cap binds%s" % (
            binding.replace("_", " "),
            "" if cap_value is None else " at %.1f%%" % (100.0 * cap_value)))
    if conc["multiplier"] < 1.0:
        reasons.append("%s concentration overlap scaled the size by %.2f"
                       % (conc["level"].lower(), conc["multiplier"]))
    if result["probability_support"] != PROBABILITY_SUPPORTED:
        reasons.append("the probabilities carry no stated basis")
    if not reasons:
        reasons.append("nothing binds; the size is the adjusted edge itself")
    result["reasons"] = reasons
    return result


# --------------------------------------------------------------------------
# Decision-record bridge and telemetry
# --------------------------------------------------------------------------

def request_from_decision(rec, portfolio=None, liquidity=None,
                          confidence=None):
    """Build a sizing request from a validated decision record.

    Scenario-first: the record already carries fair_value and
    scenario_weights, so no separate probability is asked for. The base case
    is taken at the midpoint of base_low..base_high, exactly as
    decision_record.expected_return does, and that same band then feeds the
    estimate-width factor.
    """
    if not isinstance(rec, dict):
        raise SizingError("decision record must be an object")
    fv = rec.get("fair_value") or {}
    weights = rec.get("scenario_weights") or {}
    price = (rec.get("price") or {}).get("value")
    if price is None:
        raise SizingError("decision record carries no price")

    # A fair value in EUR against a price in SEK produces a scenario ladder of
    # roughly -90% and a silent NO_BET; the reverse invents an enormous edge.
    # The record allows the two currencies to be stated separately, so they
    # are compared here rather than assumed equal.
    price_ccy = (rec.get("price") or {}).get("currency")
    fv_ccy = fv.get("currency")
    if price_ccy and fv_ccy and price_ccy != fv_ccy:
        raise SizingError(
            "fair value is in %s and the price in %s - a return cannot be "
            "computed across two currencies" % (fv_ccy, price_ccy))
    for key in ("bear", "base_low", "base_high", "bull"):
        if fv.get(key) is None:
            raise SizingError("decision record fair_value lacks %s" % key)
    for key in ("bear", "base", "bull"):
        if weights.get(key) is None:
            raise SizingError("decision record scenario_weights lacks %s" % key)

    base_mid = 0.5 * (float(fv["base_low"]) + float(fv["base_high"]))
    scenarios = kelly_mod.scenarios_from_prices(
        price,
        {"bear": fv["bear"], "base": base_mid, "bull": fv["bull"]},
        {"bear": weights["bear"], "base": weights["base"],
         "bull": weights["bull"]})

    return {
        "instrument": (rec.get("identity") or {}).get("name") or rec.get("as_of"),
        "as_of": rec.get("as_of"),
        "depth": rec.get("depth"),
        "conviction": rec.get("conviction"),
        "reason_codes": rec.get("reason_codes") or [],
        "scenarios": scenarios,
        "valuation": {"price": price, "base_low": fv["base_low"],
                      "base_high": fv["base_high"]},
        "confidence": confidence or {},
        "liquidity": liquidity or {},
        "portfolio": portfolio or {},
    }


def telemetry(result):
    """The compact slice worth persisting on a decision record.

    Deliberately small and deliberately not fed back into anything: this is
    forward-looking measurement, on the same footing as calibration.py, which
    also feeds nothing back into a score, a cap or a threshold.
    """
    return {
        "raw_kelly": result.get("raw_kelly"),
        "kelly_fraction": result.get("kelly_fraction"),
        "fractional_kelly": result.get("fractional_kelly"),
        "uncertainty_factor": result.get("uncertainty_factor"),
        "adjusted_kelly": result.get("adjusted_kelly"),
        "target": result.get("target"),
        "current": result.get("current"),
        "delta": result.get("delta"),
        "action": result.get("action"),
        "binding_constraint": result.get("binding_constraint"),
        "status": result.get("status"),
        "not_checked": result.get("not_checked") or [],
    }


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def _pct(x, places=1):
    return "-" if x is None else format(100.0 * x, ".%df" % places) + "%"


def render(result):
    """The compact block a reader sees. Intermediate steps only where they
    changed the answer."""
    lines = ["POSITION SIZING", ""]
    lines.append("Target:       %s" % _pct(result["target"]))
    lines.append("Current:      %s" % _pct(result["current"]))
    delta = result["delta"]
    sign = "+" if delta >= 0 else ""
    lines.append("Action:       %s %s%spp"
                 % (result["action"], sign, format(100.0 * delta, ".1f")))
    lines.append("")

    if result["status"] != OK:
        lines.append("Refused:")
        for stop in result["no_bet_reasons"]:
            lines.append("  - %s" % stop["detail"])
        if result.get("raw_kelly"):
            lines.append("")
            lines.append("Kelly would have suggested %s before the refusal."
                         % _pct(result["raw_kelly"]))
        return "\n".join(lines)

    frac = result["kelly_fraction"]
    lines.append("Raw Kelly:    %s" % _pct(result["raw_kelly"]))
    lines.append("%-13s %s" % ("%.2g Kelly:" % frac,
                               _pct(result["fractional_kelly"])))
    if result["uncertainty_factor"] < 1.0:
        lines.append("Uncertainty:  x%.2f -> %s"
                     % (result["uncertainty_factor"],
                        _pct(result["adjusted_kelly"])))
    binding = result["binding_constraint"]
    if binding:
        cap = result["caps"].get(binding)
        lines.append("Cap:          %s%s"
                     % (binding, "" if cap is None else " at " + _pct(cap)))
    else:
        lines.append("Cap:          none")
    conc = result["concentration"]
    if conc and conc["multiplier"] < 1.0:
        lines.append("Concentration: %s -> x%.2f"
                     % (conc["level"], conc["multiplier"]))
    lines.append("")
    lines.append("Primary constraint:")
    for reason in result.get("reasons") or [binding or
                                            "the estimated edge itself"]:
        lines.append("  - %s" % reason)
    if (result.get("calibration") or {}).get("status") != "applied":
        lines.append("")
        lines.append("Probabilities are as stated, not calibrated against a "
                     "track record.")
    if result["not_checked"]:
        lines.append("")
        lines.append("Not checked: %s" % ", ".join(result["not_checked"]))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Selftest and CLI
# --------------------------------------------------------------------------

_SCENARIOS = [
    {"name": "bear", "probability": 0.25, "return": -0.30,
     "horizon": "12m", "basis": "margin reverts to the 2019-2021 trough"},
    {"name": "base", "probability": 0.50, "return": 0.15,
     "horizon": "12m", "basis": "guidance delivered, multiple unchanged"},
    {"name": "bull", "probability": 0.25, "return": 0.50,
     "horizon": "12m", "basis": "order book converts at the stated margin"},
]


def fixture():
    """A worked request, used by --fixture and by the selftest."""
    return {
        "instrument": "Example AB",
        "as_of": "2026-09-09",
        "depth": "STANDARD",
        "conviction": "HIGH",
        "reason_codes": [{"code": "EQ_ACCRUAL_HIGH", "severity": "INFO"}],
        "scenarios": _SCENARIOS,
        "probability_basis": "scenario_analysis",
        "confidence": {"data": 78, "thesis": 0.70, "valuation": 0.60},
        "valuation": {"price": 100.0, "base_low": 108.0, "base_high": 122.0},
        "liquidity": {"adv_sek": 40_000_000, "market_cap_sek": 9_000_000_000},
        "portfolio": {
            "total_value_sek": 2_000_000,
            "current_weight": 0.02,
            "sector": "Industrials", "sector_weight": 0.18,
            "country": "SE", "country_weight": 0.55,
            "gross_weight": 0.95, "cash_weight": 0.05,
            "shares_sector_with": 2, "shares_driver_with": 0,
        },
    }


def selftest():
    checks = []

    def check(label, cond):
        checks.append((label, bool(cond)))

    cfg = load_config(path="")  # never read a real config in a selftest

    base = size(fixture(), config=cfg)
    check("worked example sizes", base["status"] == OK and base["target"] > 0)
    check("target respects every cap",
          all(base["target"] <= v + 1e-12
              for v in base["caps"].values() if v is not None))
    check("target respects the hard cap",
          base["target"] <= HARD_MAX_SINGLE_POSITION)
    check("raw Kelly is reported even when nothing uses it",
          base["raw_kelly"] is not None)
    check("data confidence is not applied here",
          "data_confidence" in base["uncertainty"]["owned_elsewhere"])
    check("action is a known token", base["action"] in ACTIONS)

    # Lower confidence must never raise the target.
    lower = fixture()
    lower["confidence"]["thesis"] = 0.20
    check("lower thesis confidence never raises the target",
          size(lower, config=cfg)["target"] <= base["target"] + 1e-12)

    # Higher concentration must never raise the target.
    crowded = fixture()
    crowded["portfolio"]["shares_sector_with"] = 4
    crowded["portfolio"]["shares_driver_with"] = 3
    check("more overlap never raises the target",
          size(crowded, config=cfg)["target"] <= base["target"] + 1e-12)

    # Conviction ceiling binds.
    weak = fixture()
    weak["conviction"] = "MEDIUM"
    wr = size(weak, config=cfg)
    check("conviction ceiling is respected",
          wr["target"] <= CONVICTION_POSITION_CEILING["MEDIUM"] + 1e-12)

    # Hard stops.
    broken = fixture()
    broken["thesis_status"] = "BROKEN"
    br = size(broken, config=cfg)
    check("fired breaker refuses", br["status"] == NO_BET
          and br["target"] == 0.0)
    check("fired breaker on a held position exits", br["action"] == "EXIT")
    check("fired breaker never initiates", br["action"] != "INITIATE")

    fresh = fixture()
    fresh["thesis_status"] = "BROKEN"
    fresh["portfolio"]["current_weight"] = 0.0
    check("fired breaker with no position is NO_BET",
          size(fresh, config=cfg)["action"] == "NO_BET")

    negative = fixture()
    negative["scenarios"] = [{"name": "bear", "probability": 0.6,
                              "return": -0.30},
                             {"name": "bull", "probability": 0.4,
                              "return": 0.20}]
    nr = size(negative, config=cfg)
    check("negative edge gives no position",
          nr["status"] == NO_BET and nr["target"] == 0.0)

    blocked = fixture()
    blocked["reason_codes"] = [{"code": "GATE_PRICE_STALE",
                                "severity": "BLOCK"}]
    check("a BLOCK reason code refuses",
          size(blocked, config=cfg)["status"] == NO_BET)

    thin = fixture()
    thin["liquidity"] = {"turnover_sek": 100_000}
    check("below the universe liquidity floor refuses",
          size(thin, config=cfg)["status"] in (NO_BET, OK))

    nodist = fixture()
    nodist["scenarios"] = None
    check("no distribution is insufficient data, not a guess",
          size(nodist, config=cfg)["status"] == INSUFFICIENT_DATA)

    # Config can only tighten.
    loose = load_config({"portfolio": {"max_single_position": 0.90},
                         "kelly_fraction": 1.0}, path="")
    check("config cannot loosen the single-name cap",
          loose["portfolio"]["max_single_position"] <= HARD_MAX_SINGLE_POSITION)
    check("config cannot reach full Kelly by accident",
          loose["kelly_fraction"] <= HARD_MAX_KELLY_FRACTION)
    explicit = load_config({"allow_full_kelly": True, "kelly_fraction": 1.0},
                           path="")
    check("full Kelly needs an explicit switch",
          explicit["kelly_fraction"] == 1.0)

    # Delta arithmetic.
    check("delta is target minus current",
          abs(base["delta"] - (base["target"] - base["current"])) < 1e-12)

    # The layers the mid-flight review asked to be kept separate.
    check("the scenario distribution is reported, not just its Kelly figure",
          len(base["scenario_distribution"]) == 3
          and base["scenario_distribution"][0].get("basis"))
    check("probability support is stated",
          base["probability_support"] == PROBABILITY_SUPPORTED)
    unsupported = fixture()
    unsupported.pop("probability_basis")
    unsupported["scenarios"] = [{k: v for k, v in s.items() if k != "basis"}
                                for s in _SCENARIOS]
    ur = size(unsupported, config=cfg)
    check("an unstated basis is visible and charged once",
          ur["probability_support"] == PROBABILITY_UNKNOWN
          and "probability basis" in ur["not_checked"])
    strict = load_config({"probability": {"require_basis": True}}, path="")
    check("a configuration may refuse an unsupported probability",
          size(unsupported, config=strict)["status"] == INSUFFICIENT_DATA)

    check("calibration is unavailable and says so",
          base["calibration"]["status"] == "unavailable")
    check("no calibration table means the probabilities are unchanged",
          abs(base["scenario_distribution"][0]["probability"] - 0.25) < 1e-12)

    # Risk budget: a deeper bear case must buy a smaller position at the same
    # edge, which is the doctrine this cap exists to enforce.
    deep = fixture()
    deep["scenarios"] = [{"name": "bear", "probability": 0.25, "return": -0.60,
                          "basis": "x"},
                         {"name": "base", "probability": 0.50, "return": 0.30,
                          "basis": "x"},
                         {"name": "bull", "probability": 0.25, "return": 1.00,
                          "basis": "x"}]
    dr = size(deep, config=cfg)
    check("the risk budget cap is computed",
          dr["caps"]["risk_budget"] is not None)
    check("a deeper bear case caps harder",
          dr["caps"]["risk_budget"] < base["caps"]["risk_budget"])
    check("the risk budget never exceeds its hard cap",
          load_config({"risk_budget": {"max_position_loss": 0.9}},
                      path="")["risk_budget"]["max_position_loss"]
          <= HARD_MAX_POSITION_LOSS)
    check("reasons are stated for every sized result", bool(base["reasons"]))

    check("render produces a block", "POSITION SIZING" in render(base))
    check("telemetry is compact", set(telemetry(base)) <= {
        "raw_kelly", "kelly_fraction", "fractional_kelly",
        "uncertainty_factor", "adjusted_kelly", "target", "current", "delta",
        "action", "binding_constraint", "status", "not_checked"})

    failed = [label for label, ok in checks if not ok]
    for label, ok in checks:
        print("  %s  %s" % ("ok  " if ok else "FAIL", label))
    print("%d/%d checks passed" % (len(checks) - len(failed), len(checks)))
    return 0 if not failed else 1


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Risk-adjusted position size from a scenario "
                    "distribution, its uncertainty and the portfolio's limits.")
    ap.add_argument("path", nargs="?",
                    help="JSON sizing request, or - for stdin. "
                         "Omit with --selftest or --fixture.")
    ap.add_argument("--from-decision", metavar="FILE",
                    help="build the request from a decision record instead")
    ap.add_argument("--portfolio", metavar="FILE",
                    help="portfolio context as JSON, or - for stdin: current "
                         "weight, sector/country weights, gross, cash, "
                         "overlap counts. Without it the portfolio caps and "
                         "the concentration grade cannot be computed and are "
                         "reported as not checked")
    ap.add_argument("--liquidity", metavar="FILE",
                    help="liquidity context as JSON: adv_sek or "
                         "turnover_sek, market_cap_sek")
    ap.add_argument("--confidence", metavar="FILE",
                    help="confidence as JSON: data 0-100, thesis and "
                         "valuation 0-1")
    ap.add_argument("--config", metavar="FILE",
                    help="config file; defaults to "
                         "<state home>/config/position-sizing.json if present")
    ap.add_argument("--render", action="store_true",
                    help="print the compact block instead of JSON")
    ap.add_argument("--json", action="store_true",
                    help="print the result as JSON")
    ap.add_argument("--emit-record", metavar="FILE",
                    help="with --from-decision: write the record back with "
                         "the sizing telemetry attached as `position_sizing`, "
                         "ready for `thesis_ledger.py --decide`. Without this "
                         "the telemetry is printed and then lost, and "
                         "calibration has nothing to measure")
    ap.add_argument("--fixture", action="store_true",
                    help="print the worked example request as JSON")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.fixture:
        print(json.dumps(fixture(), indent=2, ensure_ascii=False))
        return 0

    source = args.from_decision or args.path
    if not source:
        raise SystemExit("DATA NOT AVAILABLE: give a request file, "
                         "--from-decision, or --selftest")
    stdin_used = []

    def read_json(path):
        if not path:
            return None
        if path == "-":
            if stdin_used:
                raise SizingError("only one input may be read from stdin")
            stdin_used.append(True)
            return json.loads(sys.stdin.read())
        with open(path, encoding="utf-8") as fh:
            return json.loads(fh.read())

    try:
        payload = read_json(source)
        if not isinstance(payload, dict):
            raise SizingError("the input must be a JSON object, got %s"
                              % type(payload).__name__)
        side = {"portfolio": read_json(args.portfolio),
                "liquidity": read_json(args.liquidity),
                "confidence": read_json(args.confidence)}
        raw_record = copy.deepcopy(payload) if args.from_decision else None
        if args.from_decision:
            # A decision record carries the thesis, not the book. Without the
            # book the portfolio and liquidity caps cannot bind at all, so
            # they are accepted here rather than left permanently unchecked -
            # a record may also carry them inline under the same two keys.
            for key in ("portfolio", "liquidity", "confidence"):
                if side[key] is None and isinstance(payload, dict):
                    side[key] = payload.get(key)
            payload = request_from_decision(
                payload, portfolio=side["portfolio"],
                liquidity=side["liquidity"], confidence=side["confidence"])
        else:
            for key, value in side.items():
                if value is not None:
                    payload[key] = value
        result = size(payload, config_file=args.config)
    except SizingError as exc:
        raise SystemExit("DATA NOT AVAILABLE: %s" % exc)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("DATA NOT AVAILABLE: cannot read input - %s" % exc)

    if args.emit_record:
        if not args.from_decision:
            raise SystemExit("DATA NOT AVAILABLE: --emit-record needs "
                             "--from-decision; there is no record otherwise")
        try:
            raw_record["position_sizing"] = telemetry(result)
            with open(args.emit_record, "w", encoding="utf-8") as fh:
                json.dump(raw_record, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
        except OSError as exc:
            raise SystemExit("DATA NOT AVAILABLE: cannot write %s - %s"
                             % (args.emit_record, exc))

    if args.render and not args.json:
        print(render(result))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
