#!/usr/bin/env python3
"""Scoring the scorer: a proper scoring rule that is never checked for the
property that makes it proper is just an arbitrary formula that happens to
look rigorous.

WHY THIS FILE EXISTS

forecast_scoring.py's own docstring makes a specific, checkable claim: Brier
and the logarithmic score are PROPER, meaning a forecaster cannot improve
their expected score by hedging toward 50/50 or by reporting anything other
than what they actually believe. That claim is the entire reason the module
exists instead of some ad-hoc "percent correct" tally, and it is exactly the
kind of claim that is easy to state in a docstring and never actually verify.
An example or two ("perfect forecast scores 0") does not test properness -
properness is a statement about EVERY alternative report a forecaster could
have made instead, so the only honest test sweeps a grid of alternatives and
confirms the truth beats all of them in expectation. That sweep,
ProperScoringRuleIsMinimizedByTruth, is the single most important test in
this file; everything else here is important but secondary to it.

The second thing this module is careful about is refusing to manufacture
precision it does not have. calibration_table() returns an empty adjustments
dict - not a small number, not a rounded-to-zero number, an empty dict -
below calibration.py's DEFAULT_MIN_N, and apply_calibration() is the
identity function whenever no usable table exists. CalibrationTableGuard
below exists to catch the regression where "empty dict" quietly becomes "a
dict of zeros" or some other value a caller could mistake for a real
adjustment, which would reintroduce exactly the false precision the module's
own docstring says it refuses to produce.

Everything below is offline, synthetic, pure arithmetic - no network, no
filesystem, matching the module itself.
"""
import itertools
import math
import unittest

import helpers

helpers.bootstrap_path()

forecast_scoring = helpers.load("forecast_scoring")
calibration = helpers.load("calibration")

ScoringError = forecast_scoring.ScoringError
INSUFFICIENT_SAMPLE = forecast_scoring.INSUFFICIENT_SAMPLE
AVAILABLE = forecast_scoring.AVAILABLE
UNAVAILABLE = forecast_scoring.UNAVAILABLE


# ----------------------------------------------------------------------------
# Shared grid machinery. A probability simplex over `labels`, quantised to
# `step`, used both to sweep "every alternative report" for the properness
# test and to sweep "every forecast/outcome pair" for the Brier bounds test.
# ----------------------------------------------------------------------------

def _simplex_grid(labels, step=0.1):
    """Every report over `labels` with probabilities on a `step` grid that
    sum to exactly 1 (the last label absorbs the remainder)."""
    levels = [round(i * step, 10) for i in range(int(round(1.0 / step)) + 1)]
    *free, last = labels
    grid = []
    for combo in itertools.product(levels, repeat=len(free)):
        remainder = round(1.0 - sum(combo), 10)
        if -1e-9 <= remainder <= 1.0 + 1e-9:
            remainder = min(max(remainder, 0.0), 1.0)
            report = dict(zip(free, combo))
            report[last] = remainder
            if abs(sum(report.values()) - 1.0) < 1e-6:
                grid.append(report)
    return grid


def _single_score(kind, report, label):
    if kind == "brier":
        return forecast_scoring.brier_score(report, label)
    return forecast_scoring.log_score(report, label)[0]


def _expected_score(kind, report, truth):
    return sum(p * _single_score(kind, report, label)
              for label, p in truth.items())


class ProperScoringRuleIsMinimizedByTruth(unittest.TestCase):
    """THE load-bearing test in this file.

    A scoring rule is proper iff, for every true distribution, the expected
    score under that distribution is minimised by reporting the true
    distribution itself - not approximated by it, not tied with some other
    report, minimised, uniquely. This sweeps a full grid of alternative
    reports (step 0.1) for several true distributions and checks exactly
    that, for both Brier and the log score.
    """

    LABELS = ("bear", "base", "bull")

    TRUTHS = [
        {"bear": 0.2, "base": 0.5, "bull": 0.3},
        {"bear": 0.1, "base": 0.1, "bull": 0.8},
        {"bear": 0.4, "base": 0.3, "bull": 0.3},
        {"bear": 0.0, "base": 0.5, "bull": 0.5},
        {"bear": 0.6, "base": 0.2, "bull": 0.2},
    ]

    def _assert_truth_uniquely_minimises(self, kind):
        grid = _simplex_grid(self.LABELS, step=0.1)
        for truth in self.TRUTHS:
            with self.subTest(kind=kind, truth=truth):
                scored = {tuple(sorted(report.items())): _expected_score(
                    kind, report, truth) for report in grid}
                truth_key = tuple(sorted(truth.items()))
                self.assertIn(truth_key, scored,
                              "true distribution must itself be on the grid")
                min_value = min(scored.values())
                minimisers = [key for key, value in scored.items()
                             if value <= min_value + 1e-9]
                self.assertEqual(
                    minimisers, [truth_key],
                    "%s score must be uniquely minimised by the true "
                    "distribution, not tied or beaten by any grid "
                    "alternative" % kind)

    def test_brier_is_minimised_by_the_true_distribution(self):
        self._assert_truth_uniquely_minimises("brier")

    def test_log_score_is_minimised_by_the_true_distribution(self):
        self._assert_truth_uniquely_minimises("log")


class BrierScoreBounds(unittest.TestCase):
    """Brier is bounded in [0, 2] over every forecast/outcome pair, and hits
    both ends exactly at the extremes."""

    def test_brier_is_between_zero_and_two_over_a_swept_grid(self):
        grid = _simplex_grid(("bear", "base", "bull"), step=0.1)
        for report in grid:
            for label in report:
                with self.subTest(report=report, realised=label):
                    score = forecast_scoring.brier_score(report, label)
                    self.assertGreaterEqual(score, 0.0)
                    self.assertLessEqual(score, 2.0)

    def test_correct_point_forecast_scores_exactly_zero(self):
        perfect = {"bear": 0.0, "base": 1.0, "bull": 0.0}
        self.assertEqual(forecast_scoring.brier_score(perfect, "base"), 0.0)

    def test_confidently_wrong_point_forecast_scores_exactly_two(self):
        perfect = {"bear": 0.0, "base": 1.0, "bull": 0.0}
        self.assertEqual(forecast_scoring.brier_score(perfect, "bear"), 2.0)


class LogScoreShape(unittest.TestCase):
    """log score is 0 for a correct point forecast and grows monotonically
    as the probability given to what actually happened falls."""

    def test_correct_point_forecast_scores_exactly_zero(self):
        perfect = {"bear": 0.0, "base": 1.0, "bull": 0.0}
        score, floored = forecast_scoring.log_score(perfect, "base")
        self.assertEqual(score, 0.0)
        self.assertFalse(floored)

    def test_log_score_rises_monotonically_as_realised_probability_falls(self):
        ps = [0.99, 0.9, 0.7, 0.5, 0.3, 0.1, 0.01, 0.001, 0.0001, 0.00001]
        scores = []
        for p in ps:
            forecast = {"base": p, "bear": (1.0 - p) / 2.0,
                       "bull": (1.0 - p) / 2.0}
            score, _ = forecast_scoring.log_score(forecast, "base")
            scores.append(score)
        for earlier, later in zip(scores, scores[1:]):
            self.assertLess(earlier, later)


class OrderInvariance(unittest.TestCase):
    """Both scores are a function of the label->probability mapping, not of
    the order the labels happen to be inserted in the dict."""

    def test_brier_is_order_invariant(self):
        forward = {"bear": 0.2, "base": 0.5, "bull": 0.3}
        backward = {"bull": 0.3, "base": 0.5, "bear": 0.2}
        self.assertAlmostEqual(
            forecast_scoring.brier_score(forward, "base"),
            forecast_scoring.brier_score(backward, "base"), places=12)

    def test_log_score_is_order_invariant(self):
        forward = {"bear": 0.2, "base": 0.5, "bull": 0.3}
        backward = {"bull": 0.3, "base": 0.5, "bear": 0.2}
        score_forward, floored_forward = forecast_scoring.log_score(
            forward, "bull")
        score_backward, floored_backward = forecast_scoring.log_score(
            backward, "bull")
        self.assertAlmostEqual(score_forward, score_backward, places=12)
        self.assertEqual(floored_forward, floored_backward)


class Refusals(unittest.TestCase):
    """Every malformed forecast refuses with ScoringError rather than
    silently producing a number."""

    def _assert_refuses(self, forecast, realised="a"):
        with self.assertRaises(ScoringError):
            forecast_scoring.brier_score(forecast, realised)
        with self.assertRaises(ScoringError):
            forecast_scoring.log_score(forecast, realised)

    def test_sum_far_from_one_refuses(self):
        self._assert_refuses({"a": 0.5, "b": 0.2})

    def test_negative_probability_refuses(self):
        """Isolates the `val < 0.0` half of _check_forecast's range check:
        b and c are in range and the set sums to 1.0, so deleting just that
        half leaves nothing else in this input to raise on."""
        forecast = {"a": -0.1, "b": 0.6, "c": 0.5}
        with self.assertRaisesRegex(
                ScoringError,
                r"forecast a: probability -0\.1 outside \[0, 1\]"):
            forecast_scoring.brier_score(forecast, "a")
        with self.assertRaisesRegex(
                ScoringError,
                r"forecast a: probability -0\.1 outside \[0, 1\]"):
            forecast_scoring.log_score(forecast, "a")

    def test_probability_above_one_refuses(self):
        """Isolates the `val > 1.0` half. The out-of-range row is first and
        sums with its negative partner to 1.0, so the message is pinned to
        label a / 1.5 specifically - if the `val > 1.0` half were deleted,
        label a would pass silently and only label b's `val < 0.0` check
        would fire, raising a different message this regex would reject."""
        forecast = {"a": 1.5, "b": -0.5}
        with self.assertRaisesRegex(
                ScoringError,
                r"forecast a: probability 1\.5 outside \[0, 1\]"):
            forecast_scoring.brier_score(forecast, "a")
        with self.assertRaisesRegex(
                ScoringError,
                r"forecast a: probability 1\.5 outside \[0, 1\]"):
            forecast_scoring.log_score(forecast, "a")

    def test_non_numeric_probability_refuses(self):
        self._assert_refuses({"a": "high", "b": 0.5})

    def test_nan_probability_refuses(self):
        self._assert_refuses({"a": float("nan"), "b": 0.5})

    def test_inf_probability_refuses(self):
        self._assert_refuses({"a": float("inf"), "b": -float("inf")})

    def test_empty_mapping_refuses(self):
        self._assert_refuses({})

    def test_non_mapping_refuses(self):
        self._assert_refuses([0.5, 0.5])
        self._assert_refuses(None)

    def test_realised_label_outside_forecast_refuses(self):
        with self.assertRaises(ScoringError):
            forecast_scoring.brier_score(
                {"bear": 0.5, "bull": 0.5}, "base")
        with self.assertRaises(ScoringError):
            forecast_scoring.log_score(
                {"bear": 0.5, "bull": 0.5}, "base")


class ToleranceIsRenormalised(unittest.TestCase):
    """A probability sum inside the tolerance is accepted and renormalised,
    rather than refused or scored on the raw un-normalised numbers."""

    def test_sum_inside_tolerance_is_renormalised_to_the_same_result(self):
        base = {"bear": 0.2, "base": 0.5, "bull": 0.3}
        # Scale everything by the same factor so the RATIOS are unchanged;
        # the sum drifts to 1.0008, inside _check_forecast's 0.0015
        # tolerance. Renormalising should reproduce the exact same score as
        # the untouched distribution.
        nudged = {k: v * 1.0008 for k, v in base.items()}
        self.assertLess(abs(sum(nudged.values()) - 1.0), 0.0015)
        self.assertGreater(abs(sum(nudged.values()) - 1.0), 0.0)
        for label in base:
            with self.subTest(realised=label):
                self.assertAlmostEqual(
                    forecast_scoring.brier_score(base, label),
                    forecast_scoring.brier_score(nudged, label), places=6)


class LogScoreFlooring(unittest.TestCase):
    """A forecast of exactly zero for what happened is floored rather than
    scored as infinite, and the flooring is reported rather than hidden."""

    def test_zero_probability_on_the_realised_outcome_is_floored(self):
        forecast = {"bear": 0.0, "base": 0.5, "bull": 0.5}
        score, floored = forecast_scoring.log_score(forecast, "bear")
        self.assertTrue(floored)
        self.assertTrue(math.isfinite(score))
        self.assertAlmostEqual(
            score, -math.log(forecast_scoring.LOG_SCORE_FLOOR), places=9)

    def test_ordinary_forecast_is_not_floored(self):
        forecast = {"bear": 0.3, "base": 0.4, "bull": 0.3}
        _, floored = forecast_scoring.log_score(forecast, "bear")
        self.assertFalse(floored)


class ScoreForecastsSampleFloor(unittest.TestCase):
    """Below DEFAULT_MIN_N every mean is the literal INSUFFICIENT_SAMPLE
    string, never a number that looks precise but is not."""

    def _rows(self, n, forecast=None, realised="a"):
        forecast = forecast or {"a": 0.6, "b": 0.4}
        return [{"forecast": dict(forecast), "realised": realised}
                for _ in range(n)]

    def test_n_zero_does_not_divide_by_zero_and_refuses_a_number(self):
        rep = forecast_scoring.score_forecasts([])
        self.assertEqual(rep["n"], 0)
        self.assertEqual(rep["brier"], INSUFFICIENT_SAMPLE)
        self.assertEqual(rep["log_score"], INSUFFICIENT_SAMPLE)
        self.assertEqual(rep["floored_forecasts"], 0)
        self.assertEqual(rep["labels"], [])
        # The empty sample must carry the same keys as every other return.
        # It is the case a consumer is least likely to have tested for, so a
        # missing "reference" here would surface as a KeyError in production
        # and nowhere in development.
        self.assertIn("reference", rep)
        self.assertEqual(rep["reference"]["climatology"], {})
        self.assertEqual(rep["skill"]["vs_uniform"], INSUFFICIENT_SAMPLE)

    def test_just_below_the_floor_refuses_a_number(self):
        min_n = forecast_scoring.min_sample()
        rep = forecast_scoring.score_forecasts(self._rows(min_n - 1))
        self.assertEqual(rep["n"], min_n - 1)
        self.assertEqual(rep["brier"], INSUFFICIENT_SAMPLE)
        self.assertEqual(rep["log_score"], INSUFFICIENT_SAMPLE)
        self.assertEqual(rep["reference"]["uniform_brier"],
                         INSUFFICIENT_SAMPLE)
        self.assertEqual(rep["reference"]["climatology_brier"],
                         INSUFFICIENT_SAMPLE)

    def test_exactly_at_the_floor_produces_real_numbers(self):
        min_n = forecast_scoring.min_sample()
        rep = forecast_scoring.score_forecasts(self._rows(min_n))
        self.assertEqual(rep["n"], min_n)
        self.assertIsInstance(rep["brier"], float)
        self.assertIsInstance(rep["log_score"], float)
        self.assertIsInstance(rep["reference"]["uniform_brier"], float)
        self.assertIsInstance(rep["reference"]["climatology_brier"], float)


class ScoreForecastsClimatologyAndSkill(unittest.TestCase):
    """Climatology is the observed base rate; skill is signed correctly for
    a forecaster who is systematically right versus systematically wrong."""

    def test_climatology_is_the_observed_base_rate_and_sums_to_one(self):
        rows = ([{"forecast": {"bear": 0.25, "base": 0.5, "bull": 0.25},
                  "realised": "base"} for _ in range(15)]
               + [{"forecast": {"bear": 0.25, "base": 0.5, "bull": 0.25},
                  "realised": "bear"} for _ in range(10)])
        clim = forecast_scoring.climatology(rows)
        self.assertAlmostEqual(clim["base"], 15 / 25.0, places=12)
        self.assertAlmostEqual(clim["bear"], 10 / 25.0, places=12)
        self.assertAlmostEqual(clim["bull"], 0.0, places=12)
        self.assertAlmostEqual(sum(clim.values()), 1.0, places=12)

    def test_systematically_right_forecaster_has_positive_skill_vs_uniform(self):
        min_n = forecast_scoring.min_sample()
        good = {"a": 0.8, "b": 0.1, "c": 0.1}
        rows = [{"forecast": good, "realised": "a"} for _ in range(min_n)]
        rep = forecast_scoring.score_forecasts(rows)
        self.assertGreater(rep["skill"]["vs_uniform"], 0.0)

    def test_systematically_wrong_forecaster_has_negative_skill_vs_uniform(self):
        min_n = forecast_scoring.min_sample()
        bad = {"a": 0.1, "b": 0.1, "c": 0.8}
        rows = [{"forecast": bad, "realised": "a"} for _ in range(min_n)]
        rep = forecast_scoring.score_forecasts(rows)
        self.assertLess(rep["skill"]["vs_uniform"], 0.0)


class ScoreForecastsFlooredCount(unittest.TestCase):
    """floored_forecasts counts exactly the rows whose realised outcome was
    given zero probability."""

    def test_floored_forecasts_counts_zero_probability_hits(self):
        min_n = forecast_scoring.min_sample()
        zeroed = {"a": 0.0, "b": 0.5, "c": 0.5}
        ordinary = {"a": 0.4, "b": 0.3, "c": 0.3}
        n_zeroed = 5
        rows = ([{"forecast": zeroed, "realised": "a"}
                 for _ in range(n_zeroed)]
               + [{"forecast": ordinary, "realised": "a"}
                 for _ in range(min_n)])
        rep = forecast_scoring.score_forecasts(rows)
        self.assertEqual(rep["floored_forecasts"], n_zeroed)


class Reliability(unittest.TestCase):
    """Bins tile [0, 1] exactly, a stated probability of 1.0 lands in the
    last bin, an under-floor bucket withholds a frequency, and a
    perfectly-calibrated synthetic forecaster's realised frequency tracks
    its stated probability in the buckets that clear the floor."""

    def test_bins_tile_zero_to_one_with_no_gap_and_no_overlap(self):
        rel = forecast_scoring.reliability([], bins=5)
        bins = rel["bins"]
        self.assertEqual(len(bins), 5)
        self.assertAlmostEqual(bins[0]["range"][0], 0.0, places=12)
        self.assertAlmostEqual(bins[-1]["range"][1], 1.0, places=12)
        for earlier, later in zip(bins, bins[1:]):
            self.assertAlmostEqual(earlier["range"][1], later["range"][0],
                                   places=12)

    def test_probability_of_one_lands_in_the_last_bin(self):
        rows = [{"forecast": {"a": 1.0, "b": 0.0}, "realised": "a"}]
        rel = forecast_scoring.reliability(rows, bins=5, min_n=1)
        last = rel["bins"][-1]
        self.assertEqual(last["n"], 1)
        self.assertAlmostEqual(last["mean_stated"], 1.0, places=12)
        self.assertEqual(last["realised_frequency"], 1.0)
        # And it must not also have leaked into the second-to-last bucket.
        self.assertEqual(rel["bins"][-2]["n"], 0)

    def test_bucket_below_the_floor_reports_insufficient_sample_not_a_frequency(self):
        # Default floor (calibration.DEFAULT_MIN_N) is far above these 3
        # stated probabilities, so the bucket they land in must withhold a
        # realised_frequency while still reporting its count honestly.
        rows = [{"forecast": {"a": 0.05, "b": 0.95}, "realised": "a"}
                for _ in range(3)]
        rel = forecast_scoring.reliability(rows, bins=5)
        first = rel["bins"][0]
        self.assertEqual(first["n"], 3)
        self.assertIsNotNone(first["mean_stated"])
        self.assertEqual(first["realised_frequency"], INSUFFICIENT_SAMPLE)

    def test_perfectly_calibrated_forecaster_matches_stated_and_realised(self):
        # 30 rows stating 0.7/0.3: 21 realise "yes" (0.7 of 30), 9 realise
        # "no" (0.3 of 30). Both the "yes" and "no" stated-probability
        # entries clear the default sample floor on their own (30 each).
        rows = ([{"forecast": {"yes": 0.7, "no": 0.3}, "realised": "yes"}
                 for _ in range(21)]
               + [{"forecast": {"yes": 0.7, "no": 0.3}, "realised": "no"}
                 for _ in range(9)])
        rel = forecast_scoring.reliability(rows, bins=5)
        yes_bucket = rel["bins"][3]   # [0.6, 0.8) holds the 0.7 "yes" stated
        no_bucket = rel["bins"][1]    # [0.2, 0.4) holds the 0.3 "no" stated
        self.assertEqual(yes_bucket["n"], 30)
        self.assertNotEqual(yes_bucket["realised_frequency"],
                            INSUFFICIENT_SAMPLE)
        self.assertAlmostEqual(yes_bucket["mean_stated"],
                               yes_bucket["realised_frequency"], places=9)
        self.assertEqual(no_bucket["n"], 30)
        self.assertNotEqual(no_bucket["realised_frequency"],
                            INSUFFICIENT_SAMPLE)
        self.assertAlmostEqual(no_bucket["mean_stated"],
                               no_bucket["realised_frequency"], places=9)


class CalibrationTableSameDenominatorGuard(unittest.TestCase):
    """Stated and realised must be averaged over the SAME denominator (every
    row), not stated-over-the-rows-that-named-it against realised-over-all.
    The old bug made a perfectly calibrated scenario look four times too
    likely purely because the scenario set varies between decisions: 20
    rows total, 15 of them a 3-way bear/base/bull that never mentions
    'sideways' at all, 5 of them a 4-way set that does, with 'sideways'
    realised exactly once among those 5 - a true rate of 1-in-5 = 0.2,
    matching its stated 0.2 exactly. Under the fix both stated and realised
    are averaged over all 20 rows (0.2 contributed by 5 of 20 = 0.05 mean
    stated; 1 of 20 = 0.05 realised), so the multiplier lands near 1.0.
    Under the old per-row-that-named-it denominator, stated would have been
    averaged over only the 5 (0.2) while realised was still averaged over
    all 20 (0.05), giving a multiplier near 0.25."""

    def test_sideways_multiplier_uses_the_same_denominator_as_every_row(self):
        rows = [{"forecast": {"bear": 0.25, "base": 0.5, "bull": 0.25},
                "realised": "base"} for _ in range(15)]
        five = [{"forecast": {"bear": 0.2, "base": 0.4, "bull": 0.2,
                             "sideways": 0.2}, "realised": "sideways"}]
        five += [{"forecast": {"bear": 0.2, "base": 0.4, "bull": 0.2,
                              "sideways": 0.2}, "realised": "base"}
                for _ in range(4)]
        rows += five
        table = forecast_scoring.calibration_table(rows)
        self.assertEqual(table["status"], AVAILABLE)
        adj = table["adjustments"]["sideways"]
        n, k = 20, 4  # four distinct labels appear across the sample
        self.assertAlmostEqual(adj["mean_stated"], 1.0 / n, places=9)
        self.assertAlmostEqual(adj["realised_frequency"], 1.0 / n, places=9)
        expected_smoothed = (1.0 + 1.0) / (n + k)
        expected_multiplier = expected_smoothed / (1.0 / n)
        self.assertAlmostEqual(adj["multiplier"], expected_multiplier,
                               places=9)
        # The load-bearing claim: near 1.0 (a perfectly-calibrated scenario),
        # not near 0.25 (the old bug's four-fold understatement).
        self.assertGreater(adj["multiplier"], 0.9)
        self.assertLess(adj["multiplier"], 2.0)


class CalibrationMultiplierNeverDeletesAScenario(unittest.TestCase):
    """A multiplier is bounded below by MULTIPLIER_FLOOR, never 0, and
    apply_calibration must never let a scenario that has simply not
    happened yet (in a small sample) collapse to zero probability - that
    would be the worst thing this module could do to a position size,
    silently deleting the bear case."""

    def test_a_never_realised_scenario_keeps_a_strictly_positive_probability(self):
        fc = {"bear": 0.05, "base": 0.7, "bull": 0.25}
        rows = [{"forecast": fc, "realised": "base"} for _ in range(20)]
        table = forecast_scoring.calibration_table(rows)
        self.assertEqual(table["status"], AVAILABLE)
        for label in ("bear", "bull"):
            self.assertGreaterEqual(
                table["adjustments"][label]["multiplier"],
                forecast_scoring.MULTIPLIER_FLOOR - 1e-12)
        adjusted, status = forecast_scoring.apply_calibration(fc, table)
        self.assertEqual(status, AVAILABLE)
        self.assertGreater(adjusted["bear"], 0.0)
        self.assertAlmostEqual(sum(adjusted.values()), 1.0, places=9)


class CalibrationMultiplierBoundedOverASweep(unittest.TestCase):
    """Every multiplier calibration_table ever emits stays within
    [MULTIPLIER_FLOOR, MULTIPLIER_CEILING], over a swept set of samples with
    varying stated probabilities, sample sizes and realised distributions -
    not merely on the one example a hand-picked test happens to try."""

    def test_multiplier_stays_within_floor_and_ceiling_over_a_swept_grid(self):
        min_n = forecast_scoring.min_sample()
        samples = [
            ({"a": 0.05, "b": 0.7, "c": 0.25}, ["b"] * min_n),
            ({"a": 0.4, "b": 0.4, "c": 0.2}, ["a"] * min_n),
            ({"a": 0.9, "b": 0.05, "c": 0.05}, ["c"] * min_n),
            ({"a": 0.5, "b": 0.5}, (["a"] * (min_n - 1)) + ["b"]),
            ({"a": 0.5, "b": 0.5}, (["b"] * (min_n - 1)) + ["a"]),
            ({"a": 0.1, "b": 0.1, "c": 0.8}, ["a", "b", "c"] * 10),
        ]
        n_tables = 0
        for forecast, realised_seq in samples:
            rows = [{"forecast": forecast, "realised": r}
                    for r in realised_seq]
            table = forecast_scoring.calibration_table(rows)
            if table["status"] != AVAILABLE:
                continue
            n_tables += 1
            for label, adj in table["adjustments"].items():
                with self.subTest(forecast=forecast, label=label):
                    self.assertGreaterEqual(
                        adj["multiplier"],
                        forecast_scoring.MULTIPLIER_FLOOR - 1e-9)
                    self.assertLessEqual(
                        adj["multiplier"],
                        forecast_scoring.MULTIPLIER_CEILING + 1e-9)
        self.assertGreater(n_tables, 0)


class SampleFloorFailsClosed(unittest.TestCase):
    """An unreadable sample floor (min_sample() returning None, as it does
    when calibration.py cannot be loaded) must never be treated as 'no
    floor' - score_forecasts and reliability must both still report
    INSUFFICIENT SAMPLE rather than a number computed from as little as one
    observation. Each test uses a fresh module instance (helpers.load's
    per-call guarantee) so the monkeypatch cannot leak into another test."""

    def test_score_forecasts_reports_insufficient_sample_when_floor_unreadable(self):
        fresh = helpers.load("forecast_scoring")
        fresh.min_sample = lambda: None
        rows = [{"forecast": {"a": 0.6, "b": 0.4}, "realised": "a"}]
        rep = fresh.score_forecasts(rows)
        self.assertIsNone(rep["min_n"])
        self.assertEqual(rep["brier"], INSUFFICIENT_SAMPLE)
        self.assertEqual(rep["log_score"], INSUFFICIENT_SAMPLE)
        self.assertEqual(rep["reference"]["uniform_brier"], INSUFFICIENT_SAMPLE)
        self.assertEqual(rep["skill"]["vs_uniform"], INSUFFICIENT_SAMPLE)

    def test_reliability_reports_insufficient_sample_when_floor_unreadable(self):
        fresh = helpers.load("forecast_scoring")
        fresh.min_sample = lambda: None
        rows = [{"forecast": {"a": 0.6, "b": 0.4}, "realised": "a"}]
        rel = fresh.reliability(rows)
        self.assertIsNone(rel["min_n"])
        for bucket in rel["bins"]:
            if bucket["n"] > 0:
                self.assertEqual(bucket["realised_frequency"],
                                 INSUFFICIENT_SAMPLE)


class UniformReferenceIsPerRowNotUnionWide(unittest.TestCase):
    """The uniform reference forecast is 1/k over THIS row's own scenario
    count, not 1/(size of the union of every label seen across the whole
    sample). A union-wide uniform judged a three-scenario forecast against a
    four-way baseline whenever some other decision in the sample happened to
    name a fourth scenario, quietly changing the benchmark for everyone."""

    def test_union_wide_uniform_would_give_a_different_reference_score(self):
        min_n = forecast_scoring.min_sample()
        two = {"a": 0.6, "b": 0.4}
        four = {"a": 0.3, "b": 0.3, "c": 0.2, "d": 0.2}
        rows = ([{"forecast": two, "realised": "a"} for _ in range(min_n)]
               + [{"forecast": four, "realised": "a"} for _ in range(min_n)])
        rep = forecast_scoring.score_forecasts(rows)
        per_row_uniform_brier = rep["reference"]["uniform_brier"]

        union_labels = sorted(set(two) | set(four))
        union_uniform = {label: 1.0 / len(union_labels)
                         for label in union_labels}
        union_wide = sum(forecast_scoring.brier_score(union_uniform,
                                                       row["realised"])
                         for row in rows) / len(rows)

        self.assertNotAlmostEqual(per_row_uniform_brier, union_wide, places=6)
        # And the per-row version is what the module actually reports.
        expected_per_row = sum(
            forecast_scoring.brier_score(
                {label: 1.0 / len(row["forecast"]) for label in row["forecast"]},
                row["realised"])
            for row in rows) / len(rows)
        self.assertAlmostEqual(per_row_uniform_brier, expected_per_row,
                               places=9)


class ProbToleranceMatchesKelly(unittest.TestCase):
    """forecast_scoring.PROB_TOLERANCE, kelly.PROB_TOLERANCE and
    decision_record.WEIGHT_TOLERANCE are one quantity - a scenario weight
    tolerance - kept equal across three modules so a distribution accepted
    in one is never rejected in another on a rounding difference."""

    def test_prob_tolerance_equals_kelly_prob_tolerance(self):
        kelly = helpers.load("kelly")
        self.assertEqual(forecast_scoring.PROB_TOLERANCE, kelly.PROB_TOLERANCE)


class CalibrationTableFalsePrecisionGuard(unittest.TestCase):
    """THE false-precision guard. Below the sample floor there must be no
    adjustment - not a small one, not a zeroed one, an EMPTY dict - and
    nothing in the reason should read like a leaked number a caller could
    mistake for a real multiplier."""

    def test_below_floor_returns_empty_adjustments_and_a_reason_with_counts(self):
        min_n = forecast_scoring.min_sample()
        rows = [{"forecast": {"a": 0.6, "b": 0.4}, "realised": "a"}
                for _ in range(min_n - 1)]
        table = forecast_scoring.calibration_table(rows)
        self.assertEqual(table["status"], UNAVAILABLE)
        self.assertEqual(table["adjustments"], {})
        self.assertIsInstance(table["adjustments"], dict)
        self.assertEqual(len(table["adjustments"]), 0)
        self.assertIn(str(table["n"]), table["reason"])
        self.assertIn(str(table["min_n"]), table["reason"])
        # No stray keys that could carry a number disguised as advice.
        self.assertEqual(set(table.keys()),
                         {"status", "n", "min_n", "adjustments", "reason"})

    def test_at_or_above_floor_returns_an_available_table_with_adjustments(self):
        min_n = forecast_scoring.min_sample()
        rows = [{"forecast": {"a": 0.6, "b": 0.4}, "realised": "a"}
                for _ in range(min_n)]
        table = forecast_scoring.calibration_table(rows)
        self.assertEqual(table["status"], AVAILABLE)
        self.assertTrue(table["adjustments"])

    def test_min_sample_reads_default_min_n_from_calibration_module(self):
        # Constant-drift guard in the spirit of test_instruction_layer.py:
        # the sample floor has exactly one home, calibration.DEFAULT_MIN_N.
        self.assertEqual(forecast_scoring.min_sample(),
                         calibration.DEFAULT_MIN_N)


class ApplyCalibration(unittest.TestCase):
    """Nothing is silently adjusted today: no table, an explicit None, and
    an "unavailable" table must all return the forecast UNCHANGED."""

    def test_no_table_argument_returns_the_forecast_unchanged(self):
        fc = {"a": 0.6, "b": 0.4}
        out, status = forecast_scoring.apply_calibration(fc)
        self.assertEqual(out, fc)
        self.assertEqual(status, UNAVAILABLE)

    def test_explicit_none_table_returns_the_forecast_unchanged(self):
        fc = {"a": 0.6, "b": 0.4}
        out, status = forecast_scoring.apply_calibration(fc, None)
        self.assertEqual(out, fc)
        self.assertEqual(status, UNAVAILABLE)

    def test_unavailable_table_returns_the_forecast_unchanged(self):
        fc = {"a": 0.6, "b": 0.4}
        table = {"status": UNAVAILABLE, "adjustments": {}}
        out, status = forecast_scoring.apply_calibration(fc, table)
        self.assertEqual(out, fc)
        self.assertEqual(status, UNAVAILABLE)

    def test_available_table_still_sums_to_one(self):
        min_n = forecast_scoring.min_sample()
        good = {"a": 0.7, "b": 0.3}
        rows = [{"forecast": good, "realised": "a"} for _ in range(min_n)]
        table = forecast_scoring.calibration_table(rows)
        self.assertEqual(table["status"], AVAILABLE)
        out, status = forecast_scoring.apply_calibration(good, table)
        self.assertEqual(status, AVAILABLE)
        self.assertAlmostEqual(sum(out.values()), 1.0, places=9)

    def test_available_table_multiplies_and_renormalises_to_known_values(self):
        """A hand-built AVAILABLE table must scale each probability by its
        own multiplier and renormalise to the exact expected values, not
        merely leave the output summing to one - a mutation that makes
        apply_calibration ignore the table and return the raw forecast
        unchanged also sums to one on this input and would slip past a
        sum-only assertion."""
        fc = {"a": 0.5, "b": 0.5}
        table = {"status": AVAILABLE,
                "adjustments": {"a": {"multiplier": 2.0},
                                "b": {"multiplier": 1.0}}}
        out, status = forecast_scoring.apply_calibration(fc, table)
        self.assertEqual(status, AVAILABLE)
        self.assertAlmostEqual(out["a"], 2.0 / 3.0, places=9)
        self.assertAlmostEqual(out["b"], 1.0 / 3.0, places=9)

    def test_all_zero_adjustment_falls_back_to_the_raw_forecast(self):
        fc = {"a": 0.5, "b": 0.5}
        table = {"status": AVAILABLE,
                "adjustments": {"a": {"multiplier": 0.0},
                                "b": {"multiplier": 0.0}}}
        out, status = forecast_scoring.apply_calibration(fc, table)
        self.assertEqual(out, fc)
        self.assertEqual(status, UNAVAILABLE)


class RowsFromDecisions(unittest.TestCase):
    """Pulls only decisions with both scenario_weights and an attached
    outcome naming a scenario that is one of the weight labels; everything
    else is skipped and counted, never guessed at."""

    def test_pulls_only_fully_resolved_forecasts_and_counts_the_rest_exactly(self):
        decisions = [
            {"decision_id": "d1",
             "scenario_weights": {"bear": 0.3, "base": 0.4, "bull": 0.3},
             "outcome": {"12m": {"scenario": "base"}}},
            # no scenario_weights at all
            {"decision_id": "d2", "scenario_weights": None,
             "outcome": {"12m": {"scenario": "bear"}}},
            # empty scenario_weights is falsy, same bucket as None
            {"decision_id": "d3", "scenario_weights": {},
             "outcome": {"12m": {"scenario": "bear"}}},
            # scenario_weights present, outcome dict has nothing at this
            # horizon
            {"decision_id": "d4",
             "scenario_weights": {"bear": 0.5, "base": 0.5},
             "outcome": {}},
            # scenario_weights present, no "outcome" key whatsoever
            {"decision_id": "d5",
             "scenario_weights": {"bear": 0.5, "base": 0.5}},
            # landing scenario is not one of the forecast's own labels -
            # must be skipped, never coerced into the row
            {"decision_id": "d6",
             "scenario_weights": {"bear": 0.5, "base": 0.5},
             "outcome": {"12m": {"scenario": "sideways"}}},
            # outcome present at the horizon but an empty dict - names no
            # scenario at all, and an empty dict is itself falsy, so this
            # lands in no_outcome rather than no_landing
            {"decision_id": "d7",
             "scenario_weights": {"bear": 0.5, "base": 0.5},
             "outcome": {"12m": {}}},
        ]
        rows, skipped = forecast_scoring.rows_from_decisions(decisions)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["decision_id"], "d1")
        self.assertEqual(rows[0]["realised"], "base")
        self.assertEqual(rows[0]["forecast"],
                         {"bear": 0.3, "base": 0.4, "bull": 0.3})
        self.assertEqual(rows[0]["horizon"], "12m")
        self.assertEqual(skipped, {"no_weights": 2, "no_outcome": 3,
                                   "no_landing": 1})


class SelftestPasses(unittest.TestCase):
    def test_selftest_returns_zero(self):
        self.assertEqual(forecast_scoring.selftest(), 0)


if __name__ == "__main__":
    unittest.main()
