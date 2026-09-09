#!/usr/bin/env python3
"""Proper scoring rules for the plugin's scenario probabilities.

WHY THIS FILE EXISTS

Until now the plugin stated probabilities - bear 25%, base 50%, bull 25% - and
nothing ever came back to ask whether those numbers meant anything. A forecast
that is never scored is not a forecast, it is a decoration. calibration.py
already measures whether the *return* landed where the decision said it would;
this measures whether the *probabilities* were honest, which is a different
question and needs a different instrument.

A proper scoring rule is one whose expected score is optimised by reporting
your true belief. That property is the whole point: it means a forecaster
cannot improve their score by hedging toward 50/50 or by being boldly wrong.
Two are implemented, both standard, both deterministic:

  * Brier score - the multi-category form, SUM_i (p_i - o_i)^2 over the k
    outcomes, where o_i is 1 for the outcome that happened and 0 otherwise.
    Range 0 (perfect) to 2 (confidently wrong). Quadratic, so it punishes
    confident errors hard but finitely.

  * Logarithmic score - -ln(p) of the outcome that happened. Range 0 to
    infinity. It punishes a confident error without limit, which is the right
    shape for an equity thesis but means a probability of exactly zero would
    score as infinite; it is floored, and the flooring is reported rather than
    hidden, because a forecast of 0% for something that then happened is
    exactly the failure worth seeing.

A raw score is close to meaningless on its own, so every report carries two
reference forecasts to be judged against:

  * uniform - 1/k on every scenario, the forecast of someone who knows nothing
  * climatology - the observed base rate across the sample, the forecast of
    someone who knows the history but nothing about this company

A skill score above zero against climatology is the only evidence that the
analysis adds anything. Below zero it does not, and the report says so.

WHAT THIS FILE REFUSES TO DO

It does not adjust anybody's probabilities. `calibration_table()` exists so
that adjustment becomes possible later without a redesign, and it returns
status "unavailable" until there is a real sample - the minimum is
calibration.py's DEFAULT_MIN_N, read from that module rather than restated
here. "Claude was 73% right, so the probability is now 0.73" is precisely the
false precision this plugin exists not to produce, and with fewer than twenty
resolved forecasts it would be noise dressed as a correction.

NO NETWORK, NO FILESYSTEM WRITES, NO MODEL. Pure arithmetic over rows the
caller supplies.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import _bootstrap  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


# A probability of exactly zero on the outcome that happened would make the
# logarithmic score infinite. Floored, and every flooring is counted.
LOG_SCORE_FLOOR = 1e-6

INSUFFICIENT_SAMPLE = "INSUFFICIENT SAMPLE"

# Probabilities must sum to 1 within this. Kept equal to kelly.PROB_TOLERANCE,
# which is in turn kept equal to decision_record.WEIGHT_TOLERANCE - one
# quantity, one tolerance, three modules that must not drift.
# test_forecast_scoring.py enforces the equality rather than importing it.
PROB_TOLERANCE = 0.005

# A calibration multiplier is bounded in both directions. Even a sound sample
# should not be allowed to eliminate a scenario or to multiply one fourfold.
MULTIPLIER_FLOOR = 0.25
MULTIPLIER_CEILING = 4.0

UNAVAILABLE = "unavailable"
AVAILABLE = "available"


class ScoringError(ValueError):
    """A forecast that is not a probability distribution over known labels."""


def min_sample():
    """The sample floor, read from calibration.py so there is one home."""
    cal = _bootstrap.soft_load("calibration")
    if cal is None:
        return None
    return getattr(cal, "DEFAULT_MIN_N", None)


def _check_forecast(forecast, tolerance=PROB_TOLERANCE):
    if not isinstance(forecast, dict) or not forecast:
        raise ScoringError("forecast must be a non-empty mapping of "
                           "label to probability")
    out, total = {}, 0.0
    for label, p in forecast.items():
        try:
            val = float(p)
        except (TypeError, ValueError):
            raise ScoringError("forecast %s: %r is not a number" % (label, p))
        if not math.isfinite(val):
            raise ScoringError("forecast %s: not finite" % label)
        if val < 0.0 or val > 1.0:
            raise ScoringError("forecast %s: probability %s outside [0, 1]"
                               % (label, val))
        out[str(label)] = val
        total += val
    if abs(total - 1.0) > tolerance:
        raise ScoringError("forecast probabilities sum to %.6f, not 1"
                           % total)
    # Only rescale a sum that is meaningfully off. Rescaling a float sum that
    # is one ulp from 1.0 moves a stated 0.4 to 0.39999999999999997, which
    # then falls into the wrong reliability bucket - the diagram would be
    # reporting a number nobody forecast.
    if abs(total - 1.0) > 1e-9:
        out = {k: v / total for k, v in out.items()}
    return out


def brier_score(forecast, realised):
    """Multi-category Brier score. Lower is better; 0 is perfect."""
    fc = _check_forecast(forecast)
    if realised not in fc:
        raise ScoringError("outcome %r is not one of the forecast labels %s"
                           % (realised, ", ".join(sorted(fc))))
    return sum((p - (1.0 if label == realised else 0.0)) ** 2
               for label, p in fc.items())


def log_score(forecast, realised, floor=LOG_SCORE_FLOOR):
    """Negative log of the probability given to what happened.

    Returns (score, floored) so a forecast that gave the outcome no chance at
    all is visible rather than being smoothed into an ordinary bad score.
    """
    fc = _check_forecast(forecast)
    if realised not in fc:
        raise ScoringError("outcome %r is not one of the forecast labels %s"
                           % (realised, ", ".join(sorted(fc))))
    p = fc[realised]
    floored = p < floor
    return -math.log(max(p, floor)), floored


def _labels(rows):
    labels = set()
    for row in rows:
        labels.update(_check_forecast(row["forecast"]))
    return sorted(labels)


def _normalise_rows(rows):
    out = []
    for i, row in enumerate(rows or []):
        if not isinstance(row, dict):
            raise ScoringError("row %d is not an object" % i)
        if "forecast" not in row or "realised" not in row:
            raise ScoringError("row %d needs both forecast and realised" % i)
        fc = _check_forecast(row["forecast"])
        realised = str(row["realised"])
        if realised not in fc:
            raise ScoringError("row %d: outcome %r is not a forecast label"
                               % (i, realised))
        out.append({"forecast": fc, "realised": realised,
                    "decision_id": row.get("decision_id"),
                    "horizon": row.get("horizon")})
    return out


def climatology(rows):
    """The observed base rate per label - the forecast of someone who knows
    the history and nothing else."""
    rows = _normalise_rows(rows)
    if not rows:
        return {}
    labels = _labels(rows)
    counts = {label: 0 for label in labels}
    for row in rows:
        counts[row["realised"]] = counts.get(row["realised"], 0) + 1
    n = float(len(rows))
    return {label: counts.get(label, 0) / n for label in labels}


def score_forecasts(rows, min_n=None):
    """Score a set of resolved forecasts against both reference forecasts.

    Prints nothing and decides nothing. Below the sample floor every mean is
    the literal string INSUFFICIENT SAMPLE, matching calibration.py rather
    than reporting a number that cannot mean anything yet.
    """
    rows = _normalise_rows(rows)
    floor_n = min_sample() if min_n is None else min_n
    n = len(rows)

    if n == 0:
        # Same shape as every other return, so a consumer of the JSON does not
        # have to special-case the empty sample - the one case most likely to
        # reach a caller that never tested for it.
        return {"n": 0, "min_n": floor_n, "labels": [],
                "brier": INSUFFICIENT_SAMPLE,
                "log_score": INSUFFICIENT_SAMPLE,
                "floored_forecasts": 0,
                "reference": {"uniform_brier": INSUFFICIENT_SAMPLE,
                              "climatology_brier": INSUFFICIENT_SAMPLE,
                              "climatology": {}},
                "skill": {"vs_uniform": INSUFFICIENT_SAMPLE,
                          "vs_climatology": INSUFFICIENT_SAMPLE},
                "caveats": []}

    labels = _labels(rows)
    clim = climatology(rows)

    briers, logs, floored = [], [], 0
    u_briers, c_briers = [], []
    for row in rows:
        briers.append(brier_score(row["forecast"], row["realised"]))
        score, was_floored = log_score(row["forecast"], row["realised"])
        logs.append(score)
        floored += 1 if was_floored else 0
        # Uniform over THIS row's own scenario count. A union-wide uniform
        # judged a three-scenario forecast against a four-way baseline
        # whenever some other decision happened to name a fourth scenario,
        # which quietly changed the benchmark for everyone.
        row_uniform = {label: 1.0 / len(row["forecast"])
                       for label in row["forecast"]}
        u_briers.append(brier_score(row_uniform, row["realised"]))
        if clim:
            c_briers.append(brier_score(clim, row["realised"]))

    def mean(values):
        return sum(values) / len(values) if values else None

    # An unreadable floor means the check could not run, which in this
    # codebase is never a clean result. calibration_table already refuses in
    # that case; these two used to sail through and score a single
    # observation.
    enough = floor_n is not None and n >= floor_n
    mean_brier = mean(briers)

    def skill(reference):
        ref = mean(reference)
        if not enough or ref in (None, 0):
            return INSUFFICIENT_SAMPLE if not enough else None
        return 1.0 - (mean_brier / ref)

    return {
        "n": n,
        "min_n": floor_n,
        "labels": labels,
        "brier": mean_brier if enough else INSUFFICIENT_SAMPLE,
        "log_score": mean(logs) if enough else INSUFFICIENT_SAMPLE,
        "floored_forecasts": floored,
        "reference": {
            "uniform_brier": mean(u_briers) if enough else INSUFFICIENT_SAMPLE,
            "climatology_brier": (mean(c_briers) if enough
                                  else INSUFFICIENT_SAMPLE),
            "climatology": clim,
        },
        "skill": {
            "vs_uniform": skill(u_briers),
            "vs_climatology": skill(c_briers),
        },
        "caveats": [
            "A proper scoring rule ranks forecasters; it does not certify "
            "any single forecast.",
            "Skill below zero against climatology means the analysis added "
            "nothing to the base rate over this sample.",
        ],
    }


def reliability(rows, bins=5, min_n=None):
    """Forecast probability against realised frequency, in buckets.

    The diagonal is a calibrated forecaster. A bucket below the sample floor
    reports its count and INSUFFICIENT SAMPLE rather than a frequency, because
    a 'realised frequency' from three observations is a coin toss with a
    decimal point.
    """
    rows = _normalise_rows(rows)
    floor_n = min_sample() if min_n is None else min_n
    if bins < 1:
        raise ScoringError("bins must be at least 1")

    buckets = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        stated, hits = [], 0
        for row in rows:
            for label, p in row["forecast"].items():
                in_bucket = (lo <= p < hi) or (b == bins - 1 and p == 1.0)
                if not in_bucket:
                    continue
                stated.append(p)
                if row["realised"] == label:
                    hits += 1
        count = len(stated)
        enough = floor_n is not None and count >= floor_n
        buckets.append({
            "range": [lo, hi],
            "n": count,
            "mean_stated": (sum(stated) / count) if count else None,
            "realised_frequency": ((hits / count) if enough and count
                                   else INSUFFICIENT_SAMPLE),
        })
    return {"bins": buckets, "min_n": floor_n, "n_forecasts": len(rows)}


def calibration_table(rows, min_n=None):
    """The seam where empirical calibration would attach. Inert today.

    Returns a per-label multiplicative adjustment derived from realised
    frequency over mean stated probability - and returns it ONLY once the
    sample clears the floor. Below that the status is "unavailable" and there
    are no adjustments at all, which is the honest answer and the one the
    engine gets today.

    Nothing in this repository applies the table automatically. A caller must
    pass it in deliberately. That is the point of the design: the architecture
    supports calibration later without a redesign, and refuses to fake it now.
    """
    rows = _normalise_rows(rows)
    floor_n = min_sample() if min_n is None else min_n
    n = len(rows)
    if floor_n is None:
        return {"status": UNAVAILABLE, "n": n, "min_n": None,
                "adjustments": {},
                "reason": "the sample floor could not be read from "
                          "calibration.py; no adjustment is offered"}
    if n < floor_n:
        return {"status": UNAVAILABLE, "n": n, "min_n": floor_n,
                "adjustments": {},
                "reason": "%d resolved forecasts is below the floor of %d; "
                          "an adjustment from this many would be noise"
                          % (n, floor_n)}

    labels = _labels(rows)
    # Both sides are averaged over the SAME denominator - every row. A label a
    # forecast did not name was implicitly given zero, so averaging its stated
    # probability over only the rows that named it, while counting its hits
    # over all of them, made a perfectly calibrated scenario look four times
    # too likely purely because the scenario set varies between decisions.
    stated = {label: 0.0 for label in labels}
    realised = {label: 0 for label in labels}
    for row in rows:
        for label in labels:
            stated[label] += row["forecast"].get(label, 0.0)
        realised[row["realised"]] = realised.get(row["realised"], 0) + 1

    k = len(labels)
    adjustments = {}
    for label in labels:
        mean_stated = stated[label] / float(n)
        freq = realised.get(label, 0) / float(n)
        if mean_stated <= 0:
            continue
        # Laplace-smoothed, and the multiplier is bounded. Without smoothing a
        # scenario that simply has not happened yet gets multiplier 0, and
        # apply_calibration then deletes it outright - at a true 5% rate over
        # the twenty-observation floor, that is the single most likely sample
        # outcome. Deleting the bear case is the worst thing this file could
        # do to a position size.
        smoothed = (realised.get(label, 0) + 1.0) / (n + k)
        adjustments[label] = {
            "mean_stated": mean_stated,
            "realised_frequency": freq,
            "smoothed_frequency": smoothed,
            "multiplier": min(max(smoothed / mean_stated,
                                  MULTIPLIER_FLOOR), MULTIPLIER_CEILING)}
    return {"status": AVAILABLE, "n": n, "min_n": floor_n,
            "adjustments": adjustments,
            "reason": "advisory only; no caller applies this automatically"}


def apply_calibration(forecast, table=None):
    """raw probability -> optional adjustment -> calibrated probability.

    Identity whenever the table is missing or unavailable, which is every case
    today. Returns (probabilities, status) so the caller can report which of
    the two it got instead of having to guess.
    """
    fc = _check_forecast(forecast)
    if not table or table.get("status") != AVAILABLE:
        return fc, UNAVAILABLE
    adjustments = table.get("adjustments") or {}
    adjusted, total = {}, 0.0
    for label, p in fc.items():
        mult = (adjustments.get(label) or {}).get("multiplier", 1.0)
        val = max(p * float(mult), 0.0)
        adjusted[label] = val
        total += val
    if total <= 0:
        return fc, UNAVAILABLE
    return {k: v / total for k, v in adjusted.items()}, AVAILABLE


def rows_from_decisions(decisions, horizon="12m"):
    """Pull scored forecasts out of stored decision records.

    A decision qualifies only when it carries scenario_weights AND an attached
    outcome for the horizon naming the scenario the price actually landed in -
    the field calibration.py already produces. Anything else is skipped and
    counted, never guessed at.
    """
    rows, skipped = [], {"no_weights": 0, "no_outcome": 0, "no_landing": 0}
    for dec in decisions or []:
        weights = dec.get("scenario_weights")
        if not weights:
            skipped["no_weights"] += 1
            continue
        outcome = (dec.get("outcome") or {}).get(horizon)
        if not outcome:
            skipped["no_outcome"] += 1
            continue
        landing = outcome.get("scenario")
        if not landing or landing not in weights:
            skipped["no_landing"] += 1
            continue
        rows.append({"forecast": dict(weights), "realised": str(landing),
                     "decision_id": dec.get("decision_id"),
                     "horizon": horizon})
    return rows, skipped


def selftest():
    checks = []

    def check(label, cond):
        checks.append((label, bool(cond)))

    perfect = {"bear": 0.0, "base": 1.0, "bull": 0.0}
    check("perfect Brier is zero", brier_score(perfect, "base") == 0.0)
    check("confidently wrong Brier is 2",
          abs(brier_score(perfect, "bear") - 2.0) < 1e-12)
    uni = {"bear": 0.25, "base": 0.5, "bull": 0.25}
    check("Brier is between",
          0 < brier_score(uni, "base") < 2)
    check("log score of certainty is zero",
          abs(log_score(perfect, "base")[0]) < 1e-12)
    score, floored = log_score(perfect, "bear")
    check("log score floors a zero probability", floored and score > 10)

    # A proper scoring rule must reward honesty: reporting the true
    # distribution must beat hedging, in expectation over that distribution.
    truth = {"bear": 0.2, "base": 0.5, "bull": 0.3}
    hedge = {"bear": 0.33, "base": 0.34, "bull": 0.33}

    def expected_brier(report):
        return sum(truth[label] * brier_score(report, label) for label in truth)

    check("honest report beats hedging under Brier",
          expected_brier(truth) < expected_brier(hedge))

    rows = [{"forecast": uni, "realised": "base"} for _ in range(15)]
    rows += [{"forecast": uni, "realised": "bear"} for _ in range(10)]
    rep = score_forecasts(rows)
    check("a real sample scores", isinstance(rep["brier"], float))
    check("skill against uniform is reported",
          rep["skill"]["vs_uniform"] is not None)

    small = rows[:3]
    rep_small = score_forecasts(small)
    check("a small sample refuses a number",
          rep_small["brier"] == INSUFFICIENT_SAMPLE)

    table_small = calibration_table(small)
    check("a small sample offers no adjustment",
          table_small["status"] == UNAVAILABLE
          and table_small["adjustments"] == {})
    back, status = apply_calibration(uni, table_small)
    check("no table means identity", status == UNAVAILABLE and back == uni)

    table = calibration_table(rows)
    check("a sufficient sample produces a table",
          table["status"] == AVAILABLE and table["adjustments"])
    calibrated, status2 = apply_calibration(uni, table)
    check("a calibrated forecast still sums to 1",
          abs(sum(calibrated.values()) - 1.0) < 1e-9 and status2 == AVAILABLE)

    rel = reliability(rows)
    check("reliability bins cover the range", len(rel["bins"]) == 5)

    for label, bad in (
        ("sum != 1", {"a": 0.5, "b": 0.2}),
        ("negative", {"a": 1.4, "b": -0.4}),
        ("empty", {}),
    ):
        try:
            brier_score(bad, "a")
            check("refuses " + label, False)
        except ScoringError:
            check("refuses " + label, True)
    try:
        brier_score(uni, "sideways")
        check("refuses an unknown outcome", False)
    except ScoringError:
        check("refuses an unknown outcome", True)

    decs = [{"scenario_weights": {"bear": 0.25, "base": 0.5, "bull": 0.25},
             "outcome": {"12m": {"scenario": "base"}}},
            {"scenario_weights": None, "outcome": {"12m": {"scenario": "bear"}}},
            {"scenario_weights": {"bear": 0.3, "base": 0.4, "bull": 0.3},
             "outcome": {}}]
    pulled, skipped = rows_from_decisions(decs)
    check("pulls only resolved forecasts",
          len(pulled) == 1 and skipped["no_weights"] == 1
          and skipped["no_outcome"] == 1)

    failed = [label for label, ok in checks if not ok]
    for label, ok in checks:
        print("  %s  %s" % ("ok  " if ok else "FAIL", label))
    print("%d/%d checks passed" % (len(checks) - len(failed), len(checks)))
    return 0 if not failed else 1


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Score stored scenario probabilities with proper scoring "
                    "rules. Measures forecasts; adjusts nothing.")
    ap.add_argument("path", nargs="?",
                    help="JSON file with a rows list of {forecast, realised}, "
                         "or - for stdin")
    ap.add_argument("--horizon", default="12m",
                    help="which attached outcome horizon to score")
    ap.add_argument("--reliability", action="store_true",
                    help="also print the reliability bins")
    ap.add_argument("--table", action="store_true",
                    help="print the calibration table, or why there is none")
    ap.add_argument("--min-n", type=int, default=None,
                    help="override the sample floor")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.path:
        raise SystemExit("DATA NOT AVAILABLE: give a rows file or --selftest")

    try:
        if args.path == "-":
            text = sys.stdin.read()
        else:
            with open(args.path, encoding="utf-8") as fh:
                text = fh.read()
        payload = json.loads(text)
        if isinstance(payload, dict) and "decisions" in payload:
            rows, skipped = rows_from_decisions(payload["decisions"],
                                                args.horizon)
        else:
            rows = payload.get("rows", payload) if isinstance(
                payload, dict) else payload
            skipped = {}
        out = {"scores": score_forecasts(rows, min_n=args.min_n),
               "skipped": skipped}
        if args.reliability:
            out["reliability"] = reliability(rows, min_n=args.min_n)
        if args.table:
            out["calibration_table"] = calibration_table(rows,
                                                         min_n=args.min_n)
    except ScoringError as exc:
        raise SystemExit("DATA NOT AVAILABLE: %s" % exc)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("DATA NOT AVAILABLE: cannot read input - %s" % exc)

    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        s = out["scores"]
        print("forecasts scored   %d (floor %s)" % (s["n"], s["min_n"]))
        print("Brier              %s" % s["brier"])
        print("log score          %s" % s["log_score"])
        print("skill vs uniform   %s" % s["skill"].get("vs_uniform"))
        print("skill vs base rate %s" % s["skill"].get("vs_climatology"))
        if s["floored_forecasts"]:
            print("zero-probability outcomes that happened: %d"
                  % s["floored_forecasts"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
