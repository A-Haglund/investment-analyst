#!/usr/bin/env python3
"""Kelly sizing as pure arithmetic - no network, no filesystem, no opinions.

WHY THIS FILE EXISTS

Kelly is the one part of position sizing that has a closed form, and it is
therefore the one part that must never be done by a language model in prose.
A model asked to "compute a quarter-Kelly" will produce a number that looks
right and cannot be checked. This module produces a number that can be
checked, and refuses where the inputs do not support one.

WHAT IT DOES NOT DO

It does not decide a position. f* is the fraction of capital that maximises
the expected log growth rate of a *repeated* bet whose distribution is known
exactly. An equity thesis is neither repeated nor known exactly, so f* is an
upper bound on what the estimated edge could justify, not a recommendation.
position_sizing.py owns everything that turns it into a target.

TWO MODES

  * Binary - a win of b against a total loss:  f* = (p*b - q) / b.
  * Scenario - the general case, and the one the plugin actually has data
    for. Given scenarios (probability, return) the growth rate is

        g(f) = SUM_i p_i * ln(1 + f * r_i)

    which is strictly concave on the domain where every 1 + f*r_i > 0,
    because g''(f) = -SUM_i p_i r_i^2 / (1 + f r_i)^2 < 0. So g'(f) is
    strictly decreasing and the maximum is the unique root of

        g'(f) = SUM_i p_i * r_i / (1 + f * r_i) = 0

    found here by bisection. g'(0) = E[r], which gives the two boundary cases
    for free: E[r] <= 0 means the root is at or below zero (NO_BET), and a
    distribution with no negative return has no root at all - the bet is
    unbounded and Kelly is not guessed (INSUFFICIENT_DATA).

REFUSALS, NOT GUESSES

  * probabilities outside [0, 1], or not summing to 1        -> KellyError
  * fewer than two scenarios                                 -> KellyError
  * a return at or below -100%                               -> KellyError
  * non-finite anything                                      -> KellyError
  * no scenario with a negative return (no downside)         -> INSUFFICIENT_DATA
  * expected return <= 0                                     -> NO_BET

The probability tolerance deliberately equals decision_record.TOLERANCE_PP,
so a scenario set that its own decision record accepted is not rejected here
on a rounding difference. test_kelly.py asserts the two stay equal rather
than importing it, which would drag the record module into pure arithmetic.
"""

from __future__ import annotations

import argparse
import json
import math
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


# Probabilities must sum to 1 within this absolute tolerance. Kept equal to
# decision_record.WEIGHT_TOLERANCE - the constant that governs this same
# quantity, a set of scenario weights summing to one - so that a distribution
# its own decision record accepted is never refused here. It was briefly tied
# to TOLERANCE_PP, which is the tolerance on expected return in percentage
# points and a different thing entirely; the effect was that
# `--from-decision` refused records the repository had just declared valid.
# test_kelly.py enforces the equality rather than importing it, which would
# drag the record module into pure arithmetic.
PROB_TOLERANCE = 0.005

# f* is a fraction of capital. Above 1.0 it is leverage, which this plugin
# never sizes for, so the raw figure is clamped and the clamp is reported.
KELLY_HARD_MAX = 1.0

# Above this the raw figure is almost always an artefact of an over-confident
# probability rather than a real edge. Not an error; flagged for the caller.
IMPLAUSIBLE_KELLY = 0.50

_BISECTION_STEPS = 200
_BISECTION_EPS = 1e-13

OK = "ok"
NO_BET = "no_bet"
INSUFFICIENT_DATA = "insufficient_data"


class KellyError(ValueError):
    """An input that cannot be interpreted as a probability distribution."""


def _finite(x, label):
    try:
        v = float(x)
    except (TypeError, ValueError):
        raise KellyError("%s: not a number (%r)" % (label, x))
    if not math.isfinite(v):
        raise KellyError("%s: not finite (%r)" % (label, x))
    return v


def validate_scenarios(scenarios, tolerance=PROB_TOLERANCE):
    """Normalise a scenario distribution or refuse it.

    Accepts a sequence of mappings with `probability` and `return` (the key
    `expected_return` is accepted as a synonym, since that is what a decision
    record calls it). Returns a new list of plain dicts, never the input.
    """
    if scenarios is None:
        raise KellyError("no scenarios given")
    try:
        rows = list(scenarios)
    except TypeError:
        raise KellyError("scenarios must be a sequence")
    if len(rows) < 2:
        raise KellyError(
            "a distribution needs at least two scenarios, got %d" % len(rows))

    out = []
    total = 0.0
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise KellyError("scenario %d: expected an object, got %s"
                             % (i, type(row).__name__))
        name = str(row.get("name") or ("s%d" % i))
        p = _finite(row.get("probability"), "scenario %s: probability" % name)
        if p < 0.0 or p > 1.0:
            raise KellyError("scenario %s: probability %s outside [0, 1]"
                             % (name, p))
        raw_r = row.get("return", row.get("expected_return"))
        r = _finite(raw_r, "scenario %s: return" % name)
        if r < -1.0:
            raise KellyError("scenario %s: return %s is worse than a total loss"
                             % (name, r))
        if any(row["name"] == name for row in out):
            # Downstream code keys forecasts by name (calibration, scoring,
            # the reported distribution). Two scenarios called "bear" would
            # silently become one, and the survivor's probability would be
            # whichever happened to be written last.
            raise KellyError("scenario name %r appears twice" % name)
        out.append({"name": name, "probability": p, "return": r})
        total += p

    if abs(total - 1.0) > tolerance:
        raise KellyError("probabilities sum to %.6f, not 1 (tolerance %s)"
                         % (total, tolerance))
    # Absorb the rounding difference rather than carrying it into every sum.
    if total > 0 and total != 1.0:
        for row in out:
            row["probability"] = row["probability"] / total
    return out


# A scenario below this probability cannot bound g' against the other terms
# in double precision, so it would break the bracketing rather than inform it.
# It is also not a forecast anybody made on purpose.
LIVE_PROBABILITY_FLOOR = 1e-9


def _live(scenarios):
    """Scenarios that can actually happen.

    A scenario carrying (effectively) zero probability contributes nothing to
    any weighted sum, so letting one define the worst case would size the
    position against an outcome the forecast says cannot occur - and, worse,
    would break the bracketing in kelly_scenarios, since g' is
    probability-weighted and such a scenario cannot bound it.
    """
    return [s for s in scenarios if s["probability"] > LIVE_PROBABILITY_FLOOR]


def expected_return(scenarios):
    """Probability-weighted arithmetic return. Validated input assumed."""
    return sum(s["probability"] * s["return"] for s in scenarios)


def downside(scenarios):
    """The loss side of the distribution, separated from the mean.

    `expected_downside` is the probability-weighted mean of the losing
    scenarios only - the number that should size a position, per the standing
    rule that you size on bear-case loss rather than on volatility.
    """
    live = _live(scenarios)
    losses = [s for s in live if s["return"] < 0]
    loss_p = sum(s["probability"] for s in losses)
    worst = min((s["return"] for s in live), default=None)
    exp_down = (sum(s["probability"] * s["return"] for s in losses) / loss_p
                if loss_p > 0 else None)
    return {
        "worst_return": worst,
        "loss_probability": loss_p,
        "expected_downside": exp_down,
        "has_downside": bool(losses),
    }


def dispersion(scenarios):
    """Spread of the distribution, and its spread relative to its own mean.

    The coefficient of variation is the scale-free form and the one the
    uncertainty layer uses; it is undefined for a non-positive mean, which is
    a NO_BET case anyway, so None there rather than a large fake number.
    """
    mu = expected_return(scenarios)
    var = sum(s["probability"] * (s["return"] - mu) ** 2 for s in scenarios)
    sd = math.sqrt(var) if var > 0 else 0.0
    cv = (sd / mu) if mu > 0 else None
    return {"mean": mu, "sd": sd, "coefficient_of_variation": cv}


def _g_prime(scenarios, f):
    total = 0.0
    for s in scenarios:
        denom = 1.0 + f * s["return"]
        if denom <= 0.0:
            return float("-inf")
        total += s["probability"] * s["return"] / denom
    return total


def expected_log_growth(scenarios, f):
    """g(f) = SUM p_i ln(1 + f r_i); None where the bet can go bankrupt at f."""
    total = 0.0
    for s in scenarios:
        denom = 1.0 + f * s["return"]
        if denom <= 0.0:
            return None
        total += s["probability"] * math.log(denom)
    return total


def _result(status, method, raw, scen, notes, clamped=False, unclamped=None):
    d = downside(scen)
    return {
        "status": status,
        "method": method,
        # `raw_kelly` is the figure a caller may size with: clamped at full
        # capital, because this plugin never sizes leverage. `unclamped_kelly`
        # is the true root of g'. They differ for most equity distributions,
        # and anything comparing two Kelly figures as a RATIO must use the
        # unclamped pair - two clamped figures both sitting at 1.0 divide to
        # "perfectly robust" while the real edge may have halved.
        "raw_kelly": raw,
        "unclamped_kelly": raw if unclamped is None else unclamped,
        "expected_return": expected_return(scen),
        "expected_log_growth": (expected_log_growth(scen, raw)
                                if raw is not None else None),
        "worst_return": d["worst_return"],
        "loss_probability": d["loss_probability"],
        "expected_downside": d["expected_downside"],
        "dispersion": dispersion(scen),
        "clamped": clamped,
        "implausible": bool(raw is not None and raw > IMPLAUSIBLE_KELLY),
        "notes": notes,
    }


def kelly_scenarios(scenarios, tolerance=PROB_TOLERANCE):
    """Generalised Kelly over an arbitrary scenario distribution."""
    scen = validate_scenarios(scenarios, tolerance=tolerance)
    notes = []

    mu = expected_return(scen)
    if mu <= 0.0:
        notes.append("expected return is not positive - no bet at any size")
        return _result(NO_BET, "scenario_log_growth", 0.0, scen, notes)

    # From here the working set is the live one. g' and g are
    # probability-weighted, so a dead scenario cannot inform them - but its
    # 1 + f*r term can still go non-positive and silently truncate the domain,
    # which is how a dead -100% case used to cap the answer at 1.0 without
    # setting the clamp flag.
    live = _live(scen)
    r_min = min(s["return"] for s in live)
    if r_min >= 0.0:
        notes.append("no scenario that can happen loses money - Kelly is "
                     "unbounded here and is not guessed; supply a downside "
                     "scenario with a probability above zero")
        return _result(INSUFFICIENT_DATA, "scenario_log_growth", None, live,
                       notes)

    # 1 + f*r_min > 0  =>  f < 1 / (-r_min). g' -> -inf at that boundary, so
    # the root is always bracketed by [0, hi).
    f_boundary = 1.0 / (-r_min)
    lo, hi = 0.0, f_boundary * (1.0 - 1e-12)
    if _g_prime(live, hi) > 0.0:
        # With every live probability above LIVE_PROBABILITY_FLOOR the r_min
        # term dominates at hi, so this cannot happen. It is checked anyway:
        # a silent wrong answer here would be a large one.
        raise KellyError("bisection failed to bracket the Kelly root")

    for _ in range(_BISECTION_STEPS):
        if hi - lo < _BISECTION_EPS:
            break
        mid = 0.5 * (lo + hi)
        if _g_prime(live, mid) > 0.0:
            lo = mid
        else:
            hi = mid
    f_star = 0.5 * (lo + hi)
    unclamped = f_star

    clamped = False
    if f_star > KELLY_HARD_MAX:
        notes.append("raw Kelly %.4f exceeds full capital - clamped; this "
                     "plugin never sizes leverage" % f_star)
        f_star = KELLY_HARD_MAX
        clamped = True
    if f_star > IMPLAUSIBLE_KELLY:
        notes.append("raw Kelly %.1f%% is implausibly large for a single "
                     "equity - treat the probability estimate, not the "
                     "position, as the thing to revisit" % (f_star * 100))
    return _result(OK, "scenario_log_growth", f_star, live, notes, clamped,
                   unclamped=unclamped)


def kelly_binary(p, b):
    """Classic two-outcome Kelly: win b times the stake, or lose all of it.

    Equivalent to kelly_scenarios with returns of +b and -1; test_kelly.py
    holds the two to agreement so this stays a shortcut, not a second model.
    """
    p = _finite(p, "probability")
    if p < 0.0 or p > 1.0:
        raise KellyError("probability %s outside [0, 1]" % p)
    b = _finite(b, "payoff ratio")
    if b <= 0.0:
        raise KellyError("payoff ratio %s must be positive" % b)
    scen = [{"name": "win", "probability": p, "return": b},
            {"name": "loss", "probability": 1.0 - p, "return": -1.0}]
    if p >= 1.0:
        # A bet that cannot lose has no Kelly fraction, and the closed form
        # would happily return 1.0. The scenario path already refuses this;
        # the two must not disagree.
        return _result(INSUFFICIENT_DATA, "binary", None, scen,
                       ["a certainty is not a bet - no downside, so Kelly is "
                        "unbounded and is not guessed"])
    f = (p * b - (1.0 - p)) / b
    notes = []
    if f <= 0.0:
        notes.append("edge is not positive - no bet at any size")
        return _result(NO_BET, "binary", 0.0, scen, notes)
    clamped = False
    if f > KELLY_HARD_MAX:
        f, clamped = KELLY_HARD_MAX, True
        notes.append("raw Kelly clamped to full capital")
    return _result(OK, "binary", f, scen, notes, clamped)


def kelly(scenarios=None, p=None, b=None, tolerance=PROB_TOLERANCE):
    """Dispatcher: scenario distribution if given, else the binary form."""
    if scenarios is not None:
        return kelly_scenarios(scenarios, tolerance=tolerance)
    if p is None or b is None:
        raise KellyError("give either scenarios, or both p and b")
    return kelly_binary(p, b)


def fractional(raw_kelly, fraction):
    """Scale a raw Kelly figure. Refuses a fraction outside (0, 1].

    Kept trivial and separate so the caller's chosen fraction appears in one
    place in the output rather than being folded into the raw figure.
    """
    if raw_kelly is None:
        return None
    f = _finite(fraction, "kelly fraction")
    if f <= 0.0 or f > 1.0:
        raise KellyError("kelly fraction %s outside (0, 1]" % f)
    return raw_kelly * f


def scenarios_from_prices(price, values, weights):
    """Turn a decision record's fair-value ladder into returns.

    `values` maps a scenario name to a price target in the same currency as
    `price`; `weights` maps the same names to probabilities. Refuses rather
    than pairing up whatever happens to match.
    """
    p = _finite(price, "price")
    if p <= 0:
        raise KellyError("price %s must be positive" % p)
    out = []
    for name, w in (weights or {}).items():
        if name not in (values or {}):
            raise KellyError("scenario %s has a weight but no value" % name)
        v = _finite(values[name], "scenario %s: value" % name)
        out.append({"name": name,
                    "probability": _finite(w, "%s: weight" % name),
                    "return": v / p - 1.0})
    if not out:
        raise KellyError("no scenario weights given")
    return out


def selftest():
    checks = []

    def check(label, cond):
        checks.append((label, bool(cond)))

    # Binary and generalised forms must agree on the same bet.
    bin_r = kelly_binary(0.6, 2.0)
    gen_r = kelly_scenarios([{"name": "win", "probability": 0.6, "return": 2.0},
                             {"name": "loss", "probability": 0.4,
                              "return": -1.0}])
    check("binary closed form == generalised bisection",
          abs(bin_r["raw_kelly"] - gen_r["raw_kelly"]) < 1e-9)
    check("binary 0.6/2.0 == 0.4", abs(bin_r["raw_kelly"] - 0.4) < 1e-9)

    # A three-point equity-shaped distribution. Note the raw figure here is
    # itself clamped at full capital: an ordinary-looking bear/base/bull with
    # a 12.5% mean already implies more than 100% of capital, which is the
    # whole reason nothing downstream uses the raw figure directly.
    scen = [{"name": "bear", "probability": 0.25, "return": -0.30},
            {"name": "base", "probability": 0.50, "return": 0.15},
            {"name": "bull", "probability": 0.25, "return": 0.50}]
    r = kelly_scenarios(scen)
    check("positive edge is ok", r["status"] == OK and r["raw_kelly"] > 0)
    check("an ordinary equity spread already clamps",
          r["clamped"] and r["raw_kelly"] == KELLY_HARD_MAX)

    # A thinner edge, where the interior root is the answer.
    thin = [{"name": "bear", "probability": 0.35, "return": -0.50},
            {"name": "base", "probability": 0.45, "return": 0.15},
            {"name": "bull", "probability": 0.20, "return": 0.60}]
    rt = kelly_scenarios(thin)
    check("interior root is not clamped",
          rt["status"] == OK and 0 < rt["raw_kelly"] < KELLY_HARD_MAX)
    check("g'(f*) is zero",
          abs(_g_prime(validate_scenarios(thin), rt["raw_kelly"])) < 1e-9)
    check("f* maximises g",
          expected_log_growth(validate_scenarios(thin), rt["raw_kelly"]) >
          max(expected_log_growth(validate_scenarios(thin), rt["raw_kelly"] + d)
              for d in (-0.02, 0.02)))

    # Zero and negative edge.
    flat = [{"name": "up", "probability": 0.5, "return": 0.20},
            {"name": "down", "probability": 0.5, "return": -0.20}]
    check("zero edge is no bet", kelly_scenarios(flat)["status"] == NO_BET)
    bad = [{"name": "up", "probability": 0.3, "return": 0.20},
           {"name": "down", "probability": 0.7, "return": -0.20}]
    check("negative edge is no bet", kelly_scenarios(bad)["status"] == NO_BET)
    check("no bet gives zero, not None",
          kelly_scenarios(bad)["raw_kelly"] == 0.0)

    # Missing downside.
    up_only = [{"name": "a", "probability": 0.5, "return": 0.10},
               {"name": "b", "probability": 0.5, "return": 0.30}]
    r2 = kelly_scenarios(up_only)
    check("no downside refuses",
          r2["status"] == INSUFFICIENT_DATA and r2["raw_kelly"] is None)

    # Invalid probabilities.
    for label, rows in (
        ("sum != 1", [{"probability": 0.5, "return": 0.1},
                      {"probability": 0.2, "return": -0.1}]),
        ("p > 1", [{"probability": 1.4, "return": 0.1},
                   {"probability": -0.4, "return": -0.1}]),
        ("one scenario", [{"probability": 1.0, "return": 0.1}]),
        ("worse than total loss", [{"probability": 0.5, "return": 0.5},
                                   {"probability": 0.5, "return": -1.4}]),
    ):
        try:
            kelly_scenarios(rows)
            check("refuses " + label, False)
        except KellyError:
            check("refuses " + label, True)

    # Clamping.
    huge = [{"name": "win", "probability": 0.95, "return": 5.0},
            {"name": "loss", "probability": 0.05, "return": -0.10}]
    rh = kelly_scenarios(huge)
    check("clamped at full capital", rh["raw_kelly"] <= KELLY_HARD_MAX)
    check("clamp is reported", rh["clamped"] or rh["implausible"])

    check("fractional halves", abs(fractional(0.4, 0.5) - 0.2) < 1e-12)
    try:
        fractional(0.4, 1.5)
        check("refuses fraction > 1", False)
    except KellyError:
        check("refuses fraction > 1", True)

    failed = [label for label, ok in checks if not ok]
    for label, ok in checks:
        print("  %s  %s" % ("ok  " if ok else "FAIL", label))
    print("%d/%d checks passed" % (len(checks) - len(failed), len(checks)))
    return 0 if not failed else 1


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Kelly fraction from a scenario distribution or a binary "
                    "bet.")
    ap.add_argument("path", nargs="?",
                    help="JSON file with a scenarios list, or - for stdin")
    ap.add_argument("--p", type=float, help="binary: probability of winning")
    ap.add_argument("--b", type=float, help="binary: net payoff ratio")
    ap.add_argument("--fraction", type=float,
                    help="also report this fraction of the raw figure")
    ap.add_argument("--json", action="store_true",
                    help="print the result as JSON")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    try:
        if args.path:
            if args.path == "-":
                text = sys.stdin.read()
            else:
                with open(args.path, encoding="utf-8") as fh:
                    text = fh.read()
            payload = json.loads(text)
            scen = payload.get("scenarios", payload) if isinstance(
                payload, dict) else payload
            result = kelly_scenarios(scen)
        elif args.p is not None and args.b is not None:
            result = kelly_binary(args.p, args.b)
        else:
            raise SystemExit("DATA NOT AVAILABLE: give a scenarios file, "
                             "or both --p and --b")
        if args.fraction is not None:
            result["fraction"] = args.fraction
            result["fractional_kelly"] = fractional(result["raw_kelly"],
                                                    args.fraction)
    except KellyError as exc:
        raise SystemExit("DATA NOT AVAILABLE: %s" % exc)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("DATA NOT AVAILABLE: cannot read input - %s" % exc)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("status        %s" % result["status"])
        print("method        %s" % result["method"])
        rk = result["raw_kelly"]
        print("raw Kelly     %s" % ("-" if rk is None else format(rk, ".4f")))
        if result.get("fractional_kelly") is not None:
            print("fractional    %.4f (%g Kelly)"
                  % (result["fractional_kelly"], result["fraction"]))
        print("E[return]     %.4f" % result["expected_return"])
        for note in result["notes"]:
            print("  note: %s" % note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
