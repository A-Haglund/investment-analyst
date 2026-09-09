#!/usr/bin/env python3
"""Kelly is a closed-form optimiser; this file is what makes it trustworthy.

WHY THIS FILE EXISTS

kelly.py exists precisely because a language model asked to "size a quarter
Kelly position" produces a number that looks plausible and cannot be checked.
The whole point is defeated if the bisection root-finder itself is wrong in a
way nothing catches - a silently mislocated root is a large, confident, wrong
number handed downstream to position_sizing.py. So this file does not just
exercise the public functions; for every "interior" result (not clamped, not
a boundary case) it independently verifies the calculus the module claims to
be doing: g'(f*) is (numerically) zero at the reported f*, and g(f*) is
actually larger than g at nearby f - not merely that bisection terminated.

It also guards three narrower defect classes that are easy to reintroduce in
a refactor:

  * None vs 0.0 for raw_kelly. A NO_BET means "the edge is not positive, size
    zero" - a real, usable number. INSUFFICIENT_DATA means "this distribution
    has no downside scenario, so f* is unbounded and nothing is guessed" - the
    absence of a number. Collapsing that distinction (returning None for a
    no-bet, or 0.0 for insufficient data) would make a caller silently treat
    "we refuse to guess" as "bet nothing", which is a different claim.
  * PROB_TOLERANCE drifting from decision_record.TOLERANCE_PP. The module's
    own docstring says these must stay equal so a scenario set that its own
    decision record accepted is not rejected here on a rounding difference;
    nothing in kelly.py enforces that at runtime, so a test has to.
  * kelly_binary and kelly_scenarios silently disagreeing on the same bet.
    The binary closed form exists only as a documented shortcut over the
    general bisection; if the two ever diverge, the shortcut has become a
    second, uncoordinated model of the same quantity.

Everything below is offline, synthetic, pure arithmetic - no network, no
filesystem.
"""
import math
import unittest

import helpers

helpers.bootstrap_path()

kelly = helpers.load("kelly")
decision_record = helpers.load("decision_record")


# ----------------------------------------------------------------------------
# Shared fixtures. Reused across the "several distributions" checks so the
# optimiser-correctness tests and the maths tests are not each inventing
# their own scenario sets.
# ----------------------------------------------------------------------------

# A thin edge: bear case is large enough that the unconstrained root sits
# inside (0, KELLY_HARD_MAX), unlike the "ordinary equity spread" in
# kelly.py's own selftest() which clamps.
THIN = [{"name": "bear", "probability": 0.35, "return": -0.50},
        {"name": "base", "probability": 0.45, "return": 0.15},
        {"name": "bull", "probability": 0.20, "return": 0.60}]

# (p, b) pairs whose closed-form Kelly f = p - q/b is always < 1 (q = 1-p is
# never negative, so f can never exceed p <= 1), so every one of these is
# guaranteed interior - useful for exercising several distributions without
# hand-computing a bisection root for each.
PB_PAIRS = [(0.6, 2.0), (0.55, 1.2), (0.7, 1.0), (0.52, 3.0)]


def binary_as_scenarios(p, b):
    return [{"name": "win", "probability": p, "return": b},
            {"name": "loss", "probability": 1.0 - p, "return": -1.0}]


class PositiveZeroAndNegativeEdge(unittest.TestCase):
    """The three-way split every caller depends on: bet, no-bet, refuse."""

    def test_positive_edge_thin_distribution_gives_interior_fraction(self):
        r = kelly.kelly_scenarios(THIN)
        self.assertEqual(r["status"], kelly.OK)
        self.assertFalse(r["clamped"])
        self.assertGreater(r["raw_kelly"], 0.0)
        self.assertLess(r["raw_kelly"], 1.0)

    def test_zero_expected_edge_is_no_bet_with_zero_not_none(self):
        flat = [{"name": "up", "probability": 0.5, "return": 0.20},
                {"name": "down", "probability": 0.5, "return": -0.20}]
        r = kelly.kelly_scenarios(flat)
        self.assertEqual(r["status"], kelly.NO_BET)
        self.assertEqual(r["raw_kelly"], 0.0)
        self.assertIsNotNone(r["raw_kelly"])

    def test_negative_expected_edge_is_no_bet_with_zero_not_none(self):
        bad = [{"name": "up", "probability": 0.3, "return": 0.20},
               {"name": "down", "probability": 0.7, "return": -0.20}]
        r = kelly.kelly_scenarios(bad)
        self.assertEqual(r["status"], kelly.NO_BET)
        self.assertEqual(r["raw_kelly"], 0.0)
        self.assertIsNotNone(r["raw_kelly"])


class InvalidDistributionsRefuse(unittest.TestCase):
    """Every malformed input raises KellyError rather than producing a
    number that merely looks wrong."""

    def test_probability_below_zero_refuses(self):
        """Isolates the `p < 0.0` half of validate_scenarios' range check:
        every other row is in range and the set sums to 1.0, so deleting
        just that half leaves nothing else to raise on this input."""
        rows = [{"name": "a", "probability": -0.1, "return": 0.05},
                {"name": "b", "probability": 0.6, "return": 0.1},
                {"name": "c", "probability": 0.5, "return": -0.05}]
        with self.assertRaisesRegex(
                kelly.KellyError,
                r"scenario a: probability -0\.1 outside \[0, 1\]"):
            kelly.kelly_scenarios(rows)

    def test_probability_above_one_refuses(self):
        """Isolates the `p > 1.0` half. The out-of-range row is first and
        sums with its negative partner to 1.0, so the message is pinned to
        scenario a / 1.4 specifically - if the `p > 1.0` half were deleted,
        row a would pass silently and only row b's `p < 0.0` check would
        fire, raising a different message that this regex would reject."""
        rows = [{"name": "a", "probability": 1.4, "return": 0.1},
                {"name": "b", "probability": -0.4, "return": -0.1}]
        with self.assertRaisesRegex(
                kelly.KellyError,
                r"scenario a: probability 1\.4 outside \[0, 1\]"):
            kelly.kelly_scenarios(rows)

    def test_probabilities_not_summing_to_one_refuses(self):
        rows = [{"probability": 0.5, "return": 0.1},
                {"probability": 0.2, "return": -0.1}]
        with self.assertRaises(kelly.KellyError):
            kelly.kelly_scenarios(rows)

    def test_single_scenario_refuses(self):
        rows = [{"probability": 1.0, "return": 0.1}]
        with self.assertRaises(kelly.KellyError):
            kelly.kelly_scenarios(rows)

    def test_non_numeric_probability_refuses(self):
        rows = [{"probability": "abc", "return": 0.1},
                {"probability": 0.5, "return": -0.1}]
        with self.assertRaises(kelly.KellyError):
            kelly.kelly_scenarios(rows)

    def test_non_numeric_return_refuses(self):
        rows = [{"probability": 0.5, "return": "xyz"},
                {"probability": 0.5, "return": -0.1}]
        with self.assertRaises(kelly.KellyError):
            kelly.kelly_scenarios(rows)

    def test_duplicate_scenario_name_refuses(self):
        """Two scenarios both called 'bear' would silently collapse into one
        downstream (calibration, scoring, the reported distribution key on
        the survivor's probability), so validate_scenarios must refuse
        rather than let the second one win."""
        rows = [{"name": "bear", "probability": 0.5, "return": -0.2},
                {"name": "bear", "probability": 0.5, "return": 0.3}]
        with self.assertRaises(kelly.KellyError):
            kelly.kelly_scenarios(rows)
        with self.assertRaises(kelly.KellyError):
            kelly.validate_scenarios(rows)

    def test_nan_probability_refuses(self):
        rows = [{"probability": float("nan"), "return": 0.1},
                {"probability": 0.5, "return": -0.1}]
        with self.assertRaises(kelly.KellyError):
            kelly.kelly_scenarios(rows)

    def test_inf_return_refuses(self):
        rows = [{"probability": 0.5, "return": float("inf")},
                {"probability": 0.5, "return": -0.5}]
        with self.assertRaises(kelly.KellyError):
            kelly.kelly_scenarios(rows)

    def test_return_worse_than_total_loss_refuses(self):
        rows = [{"probability": 0.5, "return": 0.5},
                {"probability": 0.5, "return": -1.4}]
        with self.assertRaises(kelly.KellyError):
            kelly.kelly_scenarios(rows)


class MissingDownsideIsInsufficientData(unittest.TestCase):
    """A distribution with no losing scenario has an unbounded Kelly root -
    the module must refuse to guess one, not return a number."""

    def test_no_negative_return_scenario_is_insufficient_data_not_a_number(self):
        up_only = [{"name": "a", "probability": 0.5, "return": 0.10},
                   {"name": "b", "probability": 0.5, "return": 0.30}]
        r = kelly.kelly_scenarios(up_only)
        self.assertEqual(r["status"], kelly.INSUFFICIENT_DATA)
        self.assertIsNone(r["raw_kelly"])
        # expected_log_growth is defined only relative to an f, so with no
        # raw_kelly it must also come back None, not silently evaluated at
        # some default f.
        self.assertIsNone(r["expected_log_growth"])


class ClampingAtHardMax(unittest.TestCase):
    """An extreme enough payoff pushes the unconstrained root past full
    capital; the module must clamp and say so, not report leverage."""

    def test_extreme_payoff_clamps_at_hard_max_and_reports_it(self):
        extreme = [{"name": "bear", "probability": 0.10, "return": -0.20},
                   {"name": "bull", "probability": 0.90, "return": 10.0}]
        r = kelly.kelly_scenarios(extreme)
        self.assertEqual(r["status"], kelly.OK)
        self.assertTrue(r["clamped"])
        self.assertEqual(r["raw_kelly"], kelly.KELLY_HARD_MAX)


class LiveProbabilityFloorExcludesDeadScenarios(unittest.TestCase):
    """A scenario at or below LIVE_PROBABILITY_FLOOR must not be able to
    truncate the domain the bisection searches. A dead -100% row used to cap
    the answer at 1.0 without setting the clamp flag, because its
    1 + f*r term still went non-positive even though it carries no
    probability weight. The fix (_live()) must give the identical answer -
    clamped flag included - as the same distribution with that row dropped."""

    def test_zero_probability_total_loss_scenario_does_not_truncate_the_domain(self):
        with_dead = [{"name": "dead", "probability": 0.0, "return": -1.0},
                     {"name": "base", "probability": 0.3, "return": -0.2},
                     {"name": "bull", "probability": 0.7, "return": 0.3}]
        without_dead = [{"name": "base", "probability": 0.3, "return": -0.2},
                        {"name": "bull", "probability": 0.7, "return": 0.3}]
        r_with = kelly.kelly_scenarios(with_dead)
        r_without = kelly.kelly_scenarios(without_dead)
        self.assertEqual(r_with["status"], kelly.OK)
        self.assertEqual(r_with["clamped"], True)
        self.assertAlmostEqual(r_with["raw_kelly"], 1.0, places=9)
        self.assertEqual(r_with["clamped"], r_without["clamped"])
        self.assertAlmostEqual(r_with["raw_kelly"], r_without["raw_kelly"],
                               places=9)


class BinaryCertaintyAgreesWithScenarioForm(unittest.TestCase):
    """A bet that cannot lose (p=1) has no Kelly fraction - the closed form
    would happily divide out to 1.0, but kelly_binary must refuse to guess
    exactly as kelly_scenarios does on the equivalent distribution, and the
    two must not disagree with each other."""

    def test_certain_win_is_insufficient_data_not_a_number(self):
        r = kelly.kelly_binary(1.0, 2.0)
        self.assertEqual(r["status"], kelly.INSUFFICIENT_DATA)
        self.assertIsNone(r["raw_kelly"])

    def test_agrees_with_the_equivalent_scenario_distribution(self):
        bin_r = kelly.kelly_binary(1.0, 2.0)
        scen_r = kelly.kelly_scenarios(
            [{"name": "win", "probability": 1.0, "return": 2.0},
             {"name": "loss", "probability": 0.0, "return": -1.0}])
        self.assertEqual(bin_r["status"], scen_r["status"])
        self.assertEqual(bin_r["raw_kelly"], scen_r["raw_kelly"])


class UnclampedKellyIsReportedSeparately(unittest.TestCase):
    """unclamped_kelly is the true root of g', never itself clamped. It must
    equal raw_kelly whenever no clamp fired, and it must EXCEED raw_kelly
    whenever one did - anything comparing two Kelly figures as a ratio (e.g.
    probability_robustness in position_sizing.py) needs the unclamped pair,
    since two clamped figures both sitting at 1.0 would divide to 'perfectly
    robust' while the real edge may have halved."""

    def test_unclamped_equals_raw_when_not_clamped(self):
        r = kelly.kelly_scenarios(THIN)
        self.assertFalse(r["clamped"])
        self.assertIn("unclamped_kelly", r)
        self.assertEqual(r["unclamped_kelly"], r["raw_kelly"])

    def test_unclamped_exceeds_raw_when_clamped(self):
        extreme = [{"name": "bear", "probability": 0.10, "return": -0.20},
                   {"name": "bull", "probability": 0.90, "return": 10.0}]
        r = kelly.kelly_scenarios(extreme)
        self.assertTrue(r["clamped"])
        self.assertEqual(r["raw_kelly"], kelly.KELLY_HARD_MAX)
        self.assertGreater(r["unclamped_kelly"], r["raw_kelly"])


class TinyNonzeroProbabilityDoesNotBreakBracketing(unittest.TestCase):
    """A tiny-but-nonzero probability on the only negative scenario must
    still bracket the bisection root, not raise 'bisection failed to
    bracket' - the domain boundary 1/(-r_min) is unaffected by how small a
    live probability is, only by whether it is above LIVE_PROBABILITY_FLOOR."""

    def test_tiny_probability_on_the_only_negative_scenario_still_resolves(self):
        tiny = [{"name": "bear", "probability": 1e-7, "return": -0.99},
                {"name": "bull", "probability": 1.0 - 1e-7, "return": 0.10}]
        r = kelly.kelly_scenarios(tiny)
        self.assertEqual(r["status"], kelly.OK)
        self.assertIsNotNone(r["raw_kelly"])


class ProbabilityToleranceBoundary(unittest.TestCase):
    """PROB_TOLERANCE is a hard edge: just inside it, the module absorbs a
    rounding difference; just outside, it refuses rather than guess which
    scenario's probability was wrong."""

    def test_sum_inside_tolerance_is_accepted_and_renormalised(self):
        diff = kelly.PROB_TOLERANCE * 0.5
        rows = [{"probability": 0.5 + diff / 2, "return": 0.1},
                {"probability": 0.5 + diff / 2, "return": -0.5}]
        out = kelly.validate_scenarios(rows)
        total = sum(row["probability"] for row in out)
        self.assertAlmostEqual(total, 1.0, places=9)

    def test_sum_just_outside_tolerance_refuses(self):
        diff = kelly.PROB_TOLERANCE * 2.0
        rows = [{"probability": 0.5 + diff / 2, "return": 0.1},
                {"probability": 0.5 + diff / 2, "return": -0.5}]
        with self.assertRaises(kelly.KellyError):
            kelly.validate_scenarios(rows)


class OptimiserCorrectness(unittest.TestCase):
    """The part that matters: verify the calculus, not just that bisection
    terminated and returned something in range."""

    def _interior_cases(self):
        """(label, validated_scenarios, f_star) for every case guaranteed
        not to be clamped or a boundary result."""
        cases = []
        r = kelly.kelly_scenarios(THIN)
        self.assertEqual(r["status"], kelly.OK)
        self.assertFalse(r["clamped"])
        cases.append(("thin", kelly.validate_scenarios(THIN), r["raw_kelly"]))
        for p, b in PB_PAIRS:
            scen = binary_as_scenarios(p, b)
            r = kelly.kelly_scenarios(scen)
            self.assertEqual(r["status"], kelly.OK)
            self.assertFalse(r["clamped"])
            cases.append(("p=%s,b=%s" % (p, b),
                         kelly.validate_scenarios(scen), r["raw_kelly"]))
        return cases

    def test_g_prime_is_approximately_zero_at_the_interior_root(self):
        for label, scen, f_star in self._interior_cases():
            with self.subTest(label=label):
                self.assertLess(abs(kelly._g_prime(scen, f_star)), 1e-9)

    def test_f_star_is_a_maximum_of_expected_log_growth(self):
        for label, scen, f_star in self._interior_cases():
            with self.subTest(label=label):
                g_star = kelly.expected_log_growth(scen, f_star)
                # Deltas scaled to f_star so they can never push f_star+delta
                # to or below zero, nor anywhere near the domain boundary
                # where 1 + f*r_min <= 0 and expected_log_growth turns None.
                for frac in (0.01, 0.05, 0.10):
                    for sign in (1.0, -1.0):
                        delta = sign * frac * f_star
                        g_off = kelly.expected_log_growth(scen, f_star + delta)
                        self.assertIsNotNone(g_off)
                        self.assertGreater(g_star, g_off)

    def test_binary_and_scenario_form_agree_on_the_equivalent_bet(self):
        for p, b in PB_PAIRS:
            with self.subTest(p=p, b=b):
                bin_r = kelly.kelly_binary(p, b)
                gen_r = kelly.kelly_scenarios(binary_as_scenarios(p, b))
                self.assertLess(
                    abs(bin_r["raw_kelly"] - gen_r["raw_kelly"]), 1e-9)

    def test_closed_form_matches_for_the_same_pairs(self):
        for p, b in PB_PAIRS:
            with self.subTest(p=p, b=b):
                q = 1.0 - p
                expected = (p * b - q) / b
                bin_r = kelly.kelly_binary(p, b)
                self.assertAlmostEqual(bin_r["raw_kelly"], expected, places=9)


class FractionalScaling(unittest.TestCase):
    """fractional() is deliberately trivial arithmetic; the tests exist so
    a future "improvement" (e.g. clamping inside it) is caught."""

    def test_quarter_kelly_scales_exactly(self):
        self.assertAlmostEqual(kelly.fractional(0.4, 0.25), 0.1, places=12)

    def test_half_kelly_scales_exactly(self):
        self.assertAlmostEqual(kelly.fractional(0.4, 0.5), 0.2, places=12)

    def test_full_kelly_one_point_zero_is_accepted_and_returns_raw_unchanged(self):
        # The module allows the caller to ask for full Kelly; the policy
        # that forbids acting on it lives in position_sizing.py, not here.
        self.assertEqual(kelly.fractional(0.4, 1.0), 0.4)

    def test_zero_fraction_refuses(self):
        with self.assertRaises(kelly.KellyError):
            kelly.fractional(0.4, 0.0)

    def test_negative_fraction_refuses(self):
        with self.assertRaises(kelly.KellyError):
            kelly.fractional(0.4, -0.1)

    def test_fraction_above_one_refuses(self):
        with self.assertRaises(kelly.KellyError):
            kelly.fractional(0.4, 1.5)

    def test_none_raw_kelly_passes_through_as_none(self):
        self.assertIsNone(kelly.fractional(None, 0.5))


class DownsideDispersionExpectedReturn(unittest.TestCase):
    """expected_downside is the mean of the LOSING scenarios only - sizing
    on the bear case, not on the whole distribution's mean - so the test
    computes that weighted mean by hand rather than trusting the module's
    own arithmetic to check itself."""

    def test_expected_downside_is_the_probability_weighted_mean_of_losses_only(self):
        scen = [{"name": "a", "probability": 0.2, "return": -0.3},
                {"name": "b", "probability": 0.3, "return": -0.1},
                {"name": "c", "probability": 0.5, "return": 0.4}]
        d = kelly.downside(scen)
        loss_p = 0.2 + 0.3
        by_hand = (0.2 * -0.3 + 0.3 * -0.1) / loss_p
        self.assertAlmostEqual(d["expected_downside"], by_hand, places=12)
        # Not the mean of the whole distribution - that would include the
        # winning scenario and give a different (less negative) number.
        whole_mean = kelly.expected_return(scen)
        self.assertNotAlmostEqual(d["expected_downside"], whole_mean, places=6)

    def test_loss_probability_and_worst_return(self):
        scen = [{"name": "a", "probability": 0.2, "return": -0.3},
                {"name": "b", "probability": 0.3, "return": -0.1},
                {"name": "c", "probability": 0.5, "return": 0.4}]
        d = kelly.downside(scen)
        self.assertAlmostEqual(d["loss_probability"], 0.5, places=12)
        self.assertAlmostEqual(d["worst_return"], -0.3, places=12)
        self.assertTrue(d["has_downside"])

    def test_downside_with_no_losses_reports_none_and_false(self):
        scen = [{"name": "a", "probability": 0.5, "return": 0.1},
                {"name": "b", "probability": 0.5, "return": 0.2}]
        d = kelly.downside(scen)
        self.assertIsNone(d["expected_downside"])
        self.assertEqual(d["loss_probability"], 0.0)
        self.assertFalse(d["has_downside"])

    def test_coefficient_of_variation_is_none_when_mean_is_non_positive(self):
        zero_mean = [{"name": "up", "probability": 0.5, "return": 0.20},
                     {"name": "down", "probability": 0.5, "return": -0.20}]
        self.assertIsNone(kelly.dispersion(zero_mean)["coefficient_of_variation"])

        negative_mean = [{"name": "up", "probability": 0.3, "return": 0.20},
                         {"name": "down", "probability": 0.7, "return": -0.20}]
        d = kelly.dispersion(negative_mean)
        self.assertLess(d["mean"], 0.0)
        self.assertIsNone(d["coefficient_of_variation"])

    def test_coefficient_of_variation_is_computed_for_a_positive_mean(self):
        scen = [{"name": "up", "probability": 0.6, "return": 0.30},
                {"name": "down", "probability": 0.4, "return": -0.10}]
        d = kelly.dispersion(scen)
        self.assertGreater(d["mean"], 0.0)
        self.assertIsNotNone(d["coefficient_of_variation"])
        self.assertAlmostEqual(
            d["coefficient_of_variation"], d["sd"] / d["mean"], places=12)


class ScenariosFromPrices(unittest.TestCase):
    """The one function that turns a decision record's fair-value ladder
    into a return distribution - errors here are unit errors, not maths."""

    def test_converts_price_targets_to_returns_correctly(self):
        price = 100.0
        values = {"bear": 80.0, "base": 110.0, "bull": 150.0}
        weights = {"bear": 0.3, "base": 0.5, "bull": 0.2}
        out = kelly.scenarios_from_prices(price, values, weights)
        by_name = {row["name"]: row for row in out}
        self.assertAlmostEqual(by_name["bear"]["return"], -0.20, places=12)
        self.assertAlmostEqual(by_name["base"]["return"], 0.10, places=12)
        self.assertAlmostEqual(by_name["bull"]["return"], 0.50, places=12)
        self.assertAlmostEqual(by_name["bear"]["probability"], 0.3, places=12)
        self.assertAlmostEqual(by_name["base"]["probability"], 0.5, places=12)
        self.assertAlmostEqual(by_name["bull"]["probability"], 0.2, places=12)

    def test_refuses_a_weight_with_no_matching_value(self):
        price = 100.0
        values = {"base": 110.0}
        weights = {"base": 0.5, "bull": 0.5}
        with self.assertRaises(kelly.KellyError):
            kelly.scenarios_from_prices(price, values, weights)

    def test_refuses_a_non_positive_price(self):
        values = {"base": 110.0}
        weights = {"base": 1.0}
        with self.assertRaises(kelly.KellyError):
            kelly.scenarios_from_prices(0.0, values, weights)
        with self.assertRaises(kelly.KellyError):
            kelly.scenarios_from_prices(-5.0, values, weights)


class ProbToleranceMatchesDecisionRecord(unittest.TestCase):
    """kelly.py's own docstring says PROB_TOLERANCE is kept equal to
    decision_record.TOLERANCE_PP deliberately, and puts the enforcement of
    that promise here rather than importing decision_record from kelly.py
    itself (which would drag the record module into pure arithmetic). If
    this test ever fails, one of the two constants moved without the other."""

    def test_prob_tolerance_equals_decision_record_weight_tolerance(self):
        """The tolerance on a set of scenario weights summing to one has one
        home: decision_record.WEIGHT_TOLERANCE. Tied to TOLERANCE_PP instead -
        which is the tolerance on expected return in percentage points, a
        different quantity - `--from-decision` refused records that
        decision_record.validate() had just accepted."""
        decision_record = helpers.load("decision_record")
        self.assertEqual(kelly.PROB_TOLERANCE,
                         decision_record.WEIGHT_TOLERANCE)

    def test_a_record_decision_record_accepts_is_never_refused_here(self):
        """The drift guard above, stated as the behaviour it protects."""
        decision_record = helpers.load("decision_record")
        edge = 1.0 + decision_record.WEIGHT_TOLERANCE * 0.9
        scen = [{"name": "bear", "probability": 0.25 * edge, "return": -0.30},
                {"name": "base", "probability": 0.50 * edge, "return": 0.15},
                {"name": "bull", "probability": 0.25 * edge, "return": 0.50}]
        self.assertEqual(kelly.kelly_scenarios(scen)["status"], kelly.OK)


class SelftestPasses(unittest.TestCase):
    def test_selftest_returns_zero(self):
        self.assertEqual(kelly.selftest(), 0)


if __name__ == "__main__":
    unittest.main()
