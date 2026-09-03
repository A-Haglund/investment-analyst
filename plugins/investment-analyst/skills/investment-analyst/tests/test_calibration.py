#!/usr/bin/env python3
"""calibration.py - forward-only track record measurement, offline.

Covers, per the build spec:
  - realized-return arithmetic (compute_horizon_outcome, exact numbers)
  - a horizon that has not elapsed producing NO outcome (PENDING, never a
    partial/estimated return)
  - a matured horizon with no price bar refusing (NO_PRICE_DATA), never
    silently returning 0.0
  - the minimum-sample refusal: a bucket below --min-n must print/carry
    "INSUFFICIENT SAMPLE" and no numeric value at all - never 0.0, never a
    number computed from too few observations
  - hit-rate direction for BUY-side (hit = return > 0) and SELL-side
    (hit = return < 0), and HOLD being excluded entirely
  - realized price landing inside/below/above the stated bear/base/bull range
  - expectation calibration when implied_expectations is present (market
    underestimated / overestimated / in line) and its clean absence
  - the superseded-decision rule: EXCLUDED unconditionally once
    superseded_by is set (a decision_id or "WITHDRAWN"), regardless of
    whether that happened before or after the horizon matured; a company
    holding several non-superseded decisions has each scored independently
  - analyze and screen decisions never pooled into the same bucket
  - no rendered text line exceeding 88 columns
  - one isolated-store integration test against a real (throwaway)
    THESIS_LEDGER_HOME, proving the store-facing glue (_collect_decisions)
    actually reads what add_decision()/save_ledger() wrote - never against
    the real ~/.investment-analyst.

All pure-function tests build decisions and price bars by hand; none of them
touch the network or the real store.
"""
import contextlib
import datetime
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers

helpers.bootstrap_path()
cal = helpers.load("calibration")


# ---------------------------------------------------------------------------
# compute_horizon_outcome: realized-return arithmetic and the two refusals
# ---------------------------------------------------------------------------

class RealizedReturnArithmetic(unittest.TestCase):
    def _decision(self, as_of="2026-01-01", price=100.0, currency="SEK"):
        return {"as_of": as_of,
                "price": {"value": price, "currency": currency, "as_of": as_of}}

    def test_exact_arithmetic_on_a_matured_horizon(self):
        """3 months after 2026-01-01 is 2026-04-01; a bar landing exactly on
        that date with close=125.0 should give realized_return = +0.25."""
        dec = self._decision(price=100.0)
        bars = [{"date": "2026-04-01", "close": 125.0}]
        status, out = cal.compute_horizon_outcome(dec, "3m", bars, datetime.date(2026, 4, 2))
        self.assertEqual(status, "OK")
        self.assertAlmostEqual(out["realized_return"], 0.25, places=9)
        self.assertEqual(out["price_then"]["value"], 100.0)
        self.assertEqual(out["price_later"]["value"], 125.0)
        self.assertTrue(out["matured"])

    def test_negative_return_arithmetic(self):
        dec = self._decision(price=200.0)
        bars = [{"date": "2026-04-01", "close": 150.0}]
        status, out = cal.compute_horizon_outcome(dec, "3m", bars, datetime.date(2026, 4, 3))
        self.assertEqual(status, "OK")
        self.assertAlmostEqual(out["realized_return"], -0.25, places=9)

    def test_elapsed_days_measured_from_the_actual_bar_not_the_target(self):
        dec = self._decision(as_of="2026-01-01", price=100.0)
        bars = [{"date": "2026-04-04", "close": 110.0}]  # 3 days past the 3m target
        status, out = cal.compute_horizon_outcome(dec, "3m", bars, datetime.date(2026, 4, 5))
        self.assertEqual(status, "OK")
        expected_days = (datetime.date(2026, 4, 4) - datetime.date(2026, 1, 1)).days
        self.assertEqual(out["elapsed_days"], expected_days)

    def test_bars_out_of_order_and_with_none_closes_are_handled(self):
        dec = self._decision(price=50.0)
        bars = [{"date": "2026-04-05", "close": None},
                {"date": "2026-04-02", "close": 55.0},
                {"date": "2026-04-01", "close": None}]
        status, out = cal.compute_horizon_outcome(dec, "3m", bars, datetime.date(2026, 4, 6))
        self.assertEqual(status, "OK")
        # The earliest USABLE bar at/after the target (2026-04-01) is 04-02.
        self.assertEqual(out["price_later"]["as_of"], "2026-04-02")
        self.assertAlmostEqual(out["realized_return"], 0.10, places=9)


class HorizonNotElapsed(unittest.TestCase):
    def test_pending_horizon_produces_no_outcome_at_all(self):
        dec = {"as_of": "2026-01-01", "price": {"value": 100.0, "currency": "SEK",
                                                  "as_of": "2026-01-01"}}
        status, out = cal.compute_horizon_outcome(dec, "3m", [], datetime.date(2026, 3, 1))
        self.assertEqual(status, "PENDING")
        self.assertIsNone(out, "a partially-elapsed horizon must never produce "
                              "a partial/estimated outcome")

    def test_the_day_before_the_target_date_is_still_pending(self):
        dec = {"as_of": "2026-01-01", "price": {"value": 100.0, "currency": "SEK",
                                                  "as_of": "2026-01-01"}}
        target = cal.horizon_target_date(datetime.date(2026, 1, 1), "3m")
        status, out = cal.compute_horizon_outcome(
            dec, "3m", [{"date": target.isoformat(), "close": 999}],
            target - datetime.timedelta(days=1))
        self.assertEqual(status, "PENDING")
        self.assertIsNone(out)

    def test_exactly_on_the_target_date_has_matured(self):
        dec = {"as_of": "2026-01-01", "price": {"value": 100.0, "currency": "SEK",
                                                  "as_of": "2026-01-01"}}
        target = cal.horizon_target_date(datetime.date(2026, 1, 1), "3m")
        status, out = cal.compute_horizon_outcome(
            dec, "3m", [{"date": target.isoformat(), "close": 110.0}], target)
        self.assertEqual(status, "OK")


class NoPriceDataRefusal(unittest.TestCase):
    def test_matured_with_no_bars_refuses_rather_than_returning_zero(self):
        dec = {"as_of": "2026-01-01", "price": {"value": 100.0, "currency": "SEK",
                                                  "as_of": "2026-01-01"}}
        status, out = cal.compute_horizon_outcome(dec, "3m", [], datetime.date(2026, 6, 1))
        self.assertEqual(status, "NO_PRICE_DATA")
        self.assertIsNone(out)

    def test_matured_with_bars_too_far_from_the_target_still_refuses(self):
        dec = {"as_of": "2026-01-01", "price": {"value": 100.0, "currency": "SEK",
                                                  "as_of": "2026-01-01"}}
        # Target is 2026-04-01; a bar 30 days later is outside the tolerance.
        bars = [{"date": "2026-05-01", "close": 130.0}]
        status, out = cal.compute_horizon_outcome(dec, "3m", bars, datetime.date(2026, 5, 2))
        self.assertEqual(status, "NO_PRICE_DATA")
        self.assertIsNone(out)

    def test_a_bad_decision_with_no_price_refuses_distinctly(self):
        status, out = cal.compute_horizon_outcome(
            {"as_of": "2026-01-01", "price": {}}, "3m", [], datetime.date(2026, 6, 1))
        self.assertEqual(status, "BAD_DECISION")
        self.assertIsNone(out)


class AddMonthsCalendarArithmetic(unittest.TestCase):
    def test_ordinary_three_month_add(self):
        self.assertEqual(cal.add_months(datetime.date(2026, 1, 1), 3),
                         datetime.date(2026, 4, 1))

    def test_day_clamped_across_a_short_february(self):
        # 2027 is not a leap year: 31 Jan + 1 month must clamp to 28 Feb, not
        # roll over into March.
        self.assertEqual(cal.add_months(datetime.date(2027, 1, 31), 1),
                         datetime.date(2027, 2, 28))

    def test_day_clamped_across_a_leap_february(self):
        self.assertEqual(cal.add_months(datetime.date(2028, 1, 31), 1),
                         datetime.date(2028, 2, 29))

    def test_year_rollover(self):
        self.assertEqual(cal.add_months(datetime.date(2026, 11, 30), 3),
                         datetime.date(2027, 2, 28))


# ---------------------------------------------------------------------------
# Minimum-sample refusal
# ---------------------------------------------------------------------------

class MinimumSampleRefusal(unittest.TestCase):
    def test_hit_rate_below_min_n_is_insufficient_not_zero(self):
        stat = cal.stat_hit_rate([True, True, False], min_n=20)
        self.assertTrue(stat["insufficient"])
        self.assertIsNone(stat["rate"], "an insufficient bucket must carry no "
                                        "numeric rate at all, not 0.0")
        self.assertIsNone(stat["hit_count"])

    def test_hit_rate_at_min_n_reports_a_real_number(self):
        stat = cal.stat_hit_rate([True] * 15 + [False] * 5, min_n=20)
        self.assertFalse(stat["insufficient"])
        self.assertEqual(stat["n"], 20)
        self.assertAlmostEqual(stat["rate"], 0.75, places=9)

    def test_expected_vs_realized_below_min_n_is_insufficient(self):
        stat = cal.stat_expected_vs_realized([(0.1, 0.05), (0.2, -0.1)], min_n=20)
        self.assertTrue(stat["insufficient"])
        self.assertIsNone(stat["mean_expected"])
        self.assertIsNone(stat["mean_realized"])

    def test_scenario_landing_below_min_n_is_insufficient(self):
        stat = cal.stat_scenario_landing(["WITHIN_BASE", "ABOVE_BULL"], min_n=5)
        self.assertTrue(stat["insufficient"])
        self.assertIsNone(stat["counts"])

    def test_expectation_calibration_below_min_n_is_insufficient(self):
        entries = [{"verdict": "IN_LINE", "delta": 0.001}]
        stat = cal.stat_expectation_calibration(entries, min_n=5)
        self.assertTrue(stat["insufficient"])
        self.assertIsNone(stat["counts"])
        self.assertIsNone(stat["mean_delta"])

    def test_default_min_n_is_the_documented_twenty(self):
        self.assertEqual(cal.DEFAULT_MIN_N, 20)

    def test_a_bucket_at_zero_observations_is_also_insufficient_not_zero(self):
        """n=0 must read INSUFFICIENT SAMPLE, never a fabricated 0% rate."""
        stat = cal.stat_hit_rate([], min_n=20)
        self.assertTrue(stat["insufficient"])
        self.assertIsNone(stat["rate"])


# ---------------------------------------------------------------------------
# Hit-rate direction
# ---------------------------------------------------------------------------

class HitRateDirection(unittest.TestCase):
    def test_buy_hits_on_a_rise(self):
        self.assertIs(cal.is_hit("BUY", 0.10), True)
        self.assertIs(cal.is_hit("STRONG BUY", 0.001), True)

    def test_buy_misses_on_a_fall(self):
        self.assertIs(cal.is_hit("BUY", -0.10), False)

    def test_sell_hits_on_a_fall(self):
        self.assertIs(cal.is_hit("SELL", -0.10), True)
        self.assertIs(cal.is_hit("STRONG SELL", -0.001), True)
        self.assertIs(cal.is_hit("TRIM", -0.05), True)

    def test_sell_misses_on_a_rise(self):
        self.assertIs(cal.is_hit("SELL", 0.10), False)

    def test_hold_has_no_clean_call_and_is_excluded(self):
        self.assertIsNone(cal.is_hit("HOLD", 0.10))
        self.assertIsNone(cal.is_hit("HOLD", -0.10))

    def test_a_dead_flat_return_is_inconclusive_not_a_miss(self):
        self.assertIsNone(cal.is_hit("BUY", 0.0))
        self.assertIsNone(cal.is_hit("SELL", 0.0))

    def test_hit_rate_aggregation_excludes_hold_and_flat(self):
        rows = [("BUY", 0.05), ("SELL", 0.05), ("HOLD", 0.10), ("BUY", 0.0)]
        hits = [h for h in (cal.is_hit(v, r) for v, r in rows) if h is not None]
        self.assertEqual(hits, [True, False])


# ---------------------------------------------------------------------------
# Scenario landing
# ---------------------------------------------------------------------------

class ScenarioLanding(unittest.TestCase):
    FV = {"bear": 100.0, "base_low": 150.0, "base_high": 180.0, "bull": 220.0,
          "currency": "SEK"}

    def test_below_bear(self):
        self.assertEqual(cal.scenario_landing(self.FV, "SEK", 80)["landing"], "BELOW_BEAR")

    def test_between_bear_and_base_low(self):
        self.assertEqual(cal.scenario_landing(self.FV, "SEK", 120)["landing"], "BEAR_TO_BASE")

    def test_within_the_base_range(self):
        self.assertEqual(cal.scenario_landing(self.FV, "SEK", 165)["landing"], "WITHIN_BASE")

    def test_between_base_high_and_bull(self):
        self.assertEqual(cal.scenario_landing(self.FV, "SEK", 200)["landing"], "BASE_TO_BULL")

    def test_above_bull(self):
        self.assertEqual(cal.scenario_landing(self.FV, "SEK", 300)["landing"], "ABOVE_BULL")

    def test_exactly_on_a_boundary_is_inclusive_to_the_narrower_side(self):
        self.assertEqual(cal.scenario_landing(self.FV, "SEK", 150)["landing"], "WITHIN_BASE")
        self.assertEqual(cal.scenario_landing(self.FV, "SEK", 180)["landing"], "WITHIN_BASE")

    def test_no_fair_value_is_unavailable_not_a_guess(self):
        result = cal.scenario_landing(None, "SEK", 150)
        self.assertFalse(result["available"])
        self.assertIsNone(result["landing"])

    def test_currency_mismatch_refuses_rather_than_silently_comparing(self):
        fv = dict(self.FV, currency="EUR")
        result = cal.scenario_landing(fv, "SEK", 150)
        self.assertFalse(result["available"])
        self.assertIsNone(result["landing"])

    def test_non_monotonic_range_refuses(self):
        fv = {"bear": 200, "base_low": 100, "base_high": 180, "bull": 220, "currency": "SEK"}
        result = cal.scenario_landing(fv, "SEK", 150)
        self.assertFalse(result["available"])


# ---------------------------------------------------------------------------
# Expectation calibration
# ---------------------------------------------------------------------------

class ExpectationCalibration(unittest.TestCase):
    def test_market_underestimated_when_actual_beats_implied(self):
        cmp_ = cal.compare_expectation(0.04, 0.062)
        self.assertEqual(cmp_["verdict"], "MARKET_UNDERESTIMATED")
        self.assertAlmostEqual(cmp_["delta"], 0.022, places=9)

    def test_market_overestimated_when_actual_falls_short(self):
        cmp_ = cal.compare_expectation(0.08, 0.02)
        self.assertEqual(cmp_["verdict"], "MARKET_OVERESTIMATED")

    def test_in_line_within_tolerance(self):
        cmp_ = cal.compare_expectation(0.04, 0.045)
        self.assertEqual(cmp_["verdict"], "IN_LINE")

    def test_absent_implied_value_produces_no_fabricated_comparison(self):
        self.assertIsNone(cal.compare_expectation(None, 0.05))

    def test_absent_actual_value_produces_no_fabricated_comparison(self):
        self.assertIsNone(cal.compare_expectation(0.05, None))

    def test_a_decision_with_no_implied_expectations_reports_a_clean_absence(self):
        """build_calibration_report must not error or fabricate anything when
        no decision in the set carries implied_expectations."""
        dec = _matured_decision("analyze", "MEDIUM", "BUY", 0.10, 0.10)
        report = cal.build_calibration_report([dec], "3m", min_n=1)
        stat = report["producers"]["analyze"]["expectation_calibration"]
        self.assertTrue(stat["insufficient"])
        self.assertIsNone(stat["counts"])

    def test_expectation_entries_stored_on_the_outcome_are_aggregated(self):
        dec = _matured_decision("analyze", "MEDIUM", "BUY", 0.10, 0.10)
        dec["outcome"]["expectations"] = {
            "entries": [{"metric": "revenue_growth", "implied_value": 0.04,
                        "actual_value": 0.062, "delta": 0.022,
                        "verdict": "MARKET_UNDERESTIMATED"}]}
        report = cal.build_calibration_report([dec], "3m", min_n=1)
        stat = report["producers"]["analyze"]["expectation_calibration"]
        self.assertFalse(stat["insufficient"])
        self.assertEqual(stat["counts"], {"MARKET_UNDERESTIMATED": 1})


# ---------------------------------------------------------------------------
# Corporate-action classification
# ---------------------------------------------------------------------------

class CorporateActionClassification(unittest.TestCase):
    def test_no_actions_is_clean(self):
        result = cal.classify_corporate_action_window([])
        self.assertEqual(result["comparability"], "CLEAN")

    def test_a_pure_split_is_split_adjusted_series_not_a_caution(self):
        actions = [{"date": "2026-06-01", "type": "SPLIT", "title": "2:1 split"}]
        result = cal.classify_corporate_action_window(actions)
        self.assertEqual(result["comparability"], "SPLIT_ADJUSTED_SERIES")

    def test_a_reverse_split_is_also_split_adjusted_series(self):
        actions = [{"date": "2026-06-01", "type": "REVERSE_SPLIT", "title": "1:10"}]
        result = cal.classify_corporate_action_window(actions)
        self.assertEqual(result["comparability"], "SPLIT_ADJUSTED_SERIES")

    def test_a_rights_issue_is_flagged_as_a_caution(self):
        actions = [{"date": "2026-06-01", "type": "RIGHTS_ISSUE", "title": "rights"}]
        result = cal.classify_corporate_action_window(actions)
        self.assertEqual(result["comparability"], "CAUTION_OTHER_ACTION")

    def test_a_split_plus_a_rights_issue_is_still_flagged_as_a_caution(self):
        """One non-split action is enough to break the clean read, even
        alongside a split that is itself fine."""
        actions = [{"date": "2026-06-01", "type": "SPLIT", "title": "2:1"},
                  {"date": "2026-06-05", "type": "RIGHTS_ISSUE", "title": "rights"}]
        result = cal.classify_corporate_action_window(actions)
        self.assertEqual(result["comparability"], "CAUTION_OTHER_ACTION")

    def test_an_irrelevant_action_type_is_ignored(self):
        actions = [{"date": "2026-06-01", "type": "DIVIDEND", "title": "ex-div"}]
        result = cal.classify_corporate_action_window(actions)
        self.assertEqual(result["comparability"], "CLEAN")


# ---------------------------------------------------------------------------
# The superseded-decision rule
# ---------------------------------------------------------------------------

class SupersededDecisionRule(unittest.TestCase):
    def test_never_superseded_is_never_excluded(self):
        result = cal.classify_supersession({"superseded_by": None})
        self.assertFalse(result["is_superseded"])
        self.assertFalse(result["excluded_from_scoring"])

    def test_superseded_by_a_named_replacement_is_excluded(self):
        result = cal.classify_supersession({"superseded_by": "LEI-X:2026-05-01T00:00:00Z"})
        self.assertTrue(result["is_superseded"])
        self.assertTrue(result["excluded_from_scoring"])

    def test_withdrawn_with_no_replacement_is_excluded_the_same_way(self):
        """superseded_by='WITHDRAWN' (no replacement named) must be treated
        identically to a named replacement - both mean 'retired', so the
        exclusion is unconditional."""
        result = cal.classify_supersession({"superseded_by": "WITHDRAWN",
                                            "superseded_at": "2026-09-01T00:00:00Z"})
        self.assertTrue(result["is_superseded"])
        self.assertTrue(result["excluded_from_scoring"])

    def test_exclusion_does_not_depend_on_when_it_happened(self):
        """The rule is unconditional: superseded_by alone triggers exclusion,
        with no reference to the horizon's target date at all - a call
        withdrawn the day after it was made and one withdrawn a year later
        are excluded identically."""
        early = cal.classify_supersession({"superseded_by": "D2",
                                            "superseded_at": "2026-01-02T00:00:00Z"})
        late = cal.classify_supersession({"superseded_by": "D2",
                                          "superseded_at": "2028-01-02T00:00:00Z"})
        self.assertEqual(early["excluded_from_scoring"], True)
        self.assertEqual(late["excluded_from_scoring"], True)

    def test_a_superseded_decision_is_dropped_from_the_aggregate_report(self):
        good = _matured_decision("analyze", "MEDIUM", "BUY", 0.10, 0.10)
        withdrawn = _matured_decision("analyze", "MEDIUM", "BUY", 0.10, -0.50)
        withdrawn["outcome"]["3m"]["superseded"] = {"excluded_from_scoring": True}
        report = cal.build_calibration_report([good, withdrawn], "3m", min_n=1)
        prod = report["producers"]["analyze"]
        self.assertEqual(prod["n_scored"], 1)
        self.assertEqual(prod["n_excluded_superseded"], 1)
        self.assertAlmostEqual(
            prod["by_conviction"]["MEDIUM"]["mean_realized"], 0.10, places=9,
            msg="the withdrawn call's -50% return must not leak into the "
               "aggregate mean")

    def test_several_non_superseded_decisions_are_each_scored_independently(self):
        """A March call and a September call on the same producer/conviction
        are two observations, never merged or discarded for being older."""
        march = _matured_decision("analyze", "MEDIUM", "BUY", 0.05, 0.04)
        march["as_of"] = "2026-03-01"
        september = _matured_decision("analyze", "MEDIUM", "BUY", 0.05, 0.09)
        september["as_of"] = "2026-09-01"
        report = cal.build_calibration_report([march, september], "3m", min_n=1)
        stat = report["producers"]["analyze"]["by_conviction"]["MEDIUM"]
        self.assertEqual(stat["n"], 2)
        self.assertAlmostEqual(stat["mean_realized"], 0.065, places=9)


# ---------------------------------------------------------------------------
# Producer pooling
# ---------------------------------------------------------------------------

def _matured_decision(producer, conviction, verdict, expected_return, realized_return,
                      as_of="2026-01-01"):
    return {
        "producer": producer, "conviction": conviction, "verdict": verdict,
        "expected_return": expected_return, "as_of": as_of,
        "outcome": {
            "3m": {
                "matured": True, "realized_return": realized_return,
                "superseded": {"excluded_from_scoring": False},
                "scenario": {"available": False},
            }
        },
    }


class ProducersNeverPooled(unittest.TestCase):
    def test_analyze_and_screen_form_separate_buckets(self):
        d_analyze = _matured_decision("analyze", "MEDIUM", "BUY", 0.10, 0.12)
        d_screen = _matured_decision("screen", "MEDIUM", "BUY", 0.20, -0.05)
        report = cal.build_calibration_report([d_analyze, d_screen], "3m", min_n=1)
        self.assertEqual(set(report["producers"].keys()), {"analyze", "screen"})

    def test_neither_bucket_sees_the_others_numbers(self):
        d_analyze = _matured_decision("analyze", "MEDIUM", "BUY", 0.10, 0.12)
        d_screen = _matured_decision("screen", "MEDIUM", "BUY", 0.20, -0.05)
        report = cal.build_calibration_report([d_analyze, d_screen], "3m", min_n=1)
        self.assertAlmostEqual(
            report["producers"]["analyze"]["by_conviction"]["MEDIUM"]["mean_realized"],
            0.12, places=9)
        self.assertAlmostEqual(
            report["producers"]["screen"]["by_conviction"]["MEDIUM"]["mean_realized"],
            -0.05, places=9)
        self.assertEqual(
            report["producers"]["analyze"]["by_conviction"]["MEDIUM"]["n"], 1)
        self.assertEqual(
            report["producers"]["screen"]["by_conviction"]["MEDIUM"]["n"], 1)

    def test_a_pooled_across_producer_figure_is_never_computed(self):
        """There is no top-level 'all producers' bucket anywhere in the
        report shape - only per-producer entries."""
        d_analyze = _matured_decision("analyze", "MEDIUM", "BUY", 0.10, 0.12)
        d_screen = _matured_decision("screen", "MEDIUM", "BUY", 0.20, -0.05)
        report = cal.build_calibration_report([d_analyze, d_screen], "3m", min_n=1)
        self.assertNotIn("all", report["producers"])
        self.assertNotIn("ALL", report["producers"])
        self.assertNotIn("pooled", report)


# ---------------------------------------------------------------------------
# Thesis-status cut (a bonus, coordinated-in axis)
# ---------------------------------------------------------------------------

class ThesisStatusCut(unittest.TestCase):
    def test_status_read_from_thesis_ref(self):
        self.assertEqual(cal.thesis_status_of({"thesis_ref": {"status": "WARNING"}}),
                         "WARNING")

    def test_no_thesis_ref_falls_into_no_thesis_bucket(self):
        self.assertEqual(cal.thesis_status_of({}), "NO_THESIS")

    def test_thesis_status_grouping_in_the_report(self):
        warned = _matured_decision("analyze", "MEDIUM", "BUY", 0.10, -0.10)
        warned["thesis_ref"] = {"status": "WARNING"}
        healthy = _matured_decision("analyze", "MEDIUM", "BUY", 0.10, 0.10)
        report = cal.build_calibration_report([warned, healthy], "3m", min_n=1)
        by_status = report["producers"]["analyze"]["by_thesis_status"]
        self.assertEqual(by_status["WARNING"]["n"], 1)
        self.assertEqual(by_status["NO_THESIS"]["n"], 1)


# ---------------------------------------------------------------------------
# Rendered text must fit the house 88-column limit
# ---------------------------------------------------------------------------

class RenderedTextColumnLimit(unittest.TestCase):
    def _render(self, report):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cal.render_text(report)
        return buf.getvalue().splitlines()

    def test_a_small_report_fits(self):
        d_analyze = _matured_decision("analyze", "MEDIUM", "BUY", 0.10, 0.12)
        report = cal.build_calibration_report([d_analyze], "3m", min_n=1)
        lines = self._render(report)
        self.assertTrue(lines)
        for line in lines:
            self.assertLessEqual(len(line), 88, msg=repr(line))

    def test_a_wide_report_with_every_scenario_bucket_populated_still_fits(self):
        """The historically riskiest case: every landing/verdict category
        populated with multi-digit counts, across two producers at once."""
        decisions = []
        landings = ("BELOW_BEAR", "BEAR_TO_BASE", "WITHIN_BASE", "BASE_TO_BULL",
                   "ABOVE_BULL")
        for producer in ("analyze", "screen"):
            for i in range(30):
                d = _matured_decision(producer, "HIGH", "BUY", 0.1, 0.1 + i * 0.001)
                d["outcome"]["3m"]["scenario"] = {
                    "available": True, "landing": landings[i % len(landings)]}
                decisions.append(d)
        report = cal.build_calibration_report(decisions, "3m", min_n=1)
        lines = self._render(report)
        for line in lines:
            self.assertLessEqual(len(line), 88, msg=repr(line))

    def test_caveats_are_always_printed(self):
        d_analyze = _matured_decision("analyze", "MEDIUM", "BUY", 0.10, 0.12)
        report = cal.build_calibration_report([d_analyze], "3m", min_n=1)
        text = "\n".join(self._render(report))
        self.assertIn("Forward-only track record", text)
        self.assertIn("PRICE return", text)


# ---------------------------------------------------------------------------
# Isolated-store integration test: real thesis_ledger, throwaway
# THESIS_LEDGER_HOME. Skips cleanly if the decisions store is not yet
# present on this checkout, rather than failing a test that depends on
# another agent's concurrent work landing first.
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def isolated_ledger_home():
    """Points thesis_ledger.ledger_home() at a throwaway directory for the
    life of the with-block. No test may touch the real ~/.investment-analyst
    - mirrors tests/test_portfolio_store.py's isolated_store()."""
    with tempfile.TemporaryDirectory() as tmp:
        old = os.environ.get("THESIS_LEDGER_HOME")
        os.environ["THESIS_LEDGER_HOME"] = tmp
        try:
            yield tmp
        finally:
            if old is None:
                os.environ.pop("THESIS_LEDGER_HOME", None)
            else:
                os.environ["THESIS_LEDGER_HOME"] = old


class IsolatedStoreIntegration(unittest.TestCase):
    """Exercises _collect_decisions against a genuinely stored decision,
    written through thesis_ledger's own add_decision()/save_ledger() - never
    through calibration.py reimplementing the store's read logic."""

    def setUp(self):
        self.tl = helpers.try_load("thesis_ledger")
        if self.tl is None or not hasattr(self.tl, "add_decision"):
            self.skipTest("thesis_ledger's decisions store is not available "
                         "on this checkout yet")

    def _minimal_decision(self):
        return {
            "as_of": "2026-01-01", "depth": "QUICK", "producer": "analyze",
            "identity": {"name": "Calibration Test AB", "ticker": "CALTEST.ST"},
            "verdict": "BUY", "conviction": "MEDIUM",
            "price": {"value": 100.0, "currency": "SEK",
                     "as_of": "2026-01-01 09:00 UTC", "source": "test"},
            "reason_codes": [],
        }

    def test_a_stored_decision_is_readable_through_collect_decisions(self):
        with isolated_ledger_home():
            tl = helpers.load("thesis_ledger")
            identity = {"company_name": "Calibration Test AB", "ticker": "CALTEST.ST",
                       "lei": "5493000CALTEST0000AA"}
            key = tl.ledger_key(identity)
            led = tl.new_ledger(key, identity, "Calibration Test AB")
            tl.add_decision(led, self._minimal_decision())
            tl.save_ledger(led)

            decisions = cal._collect_decisions(tl, None, None, None)
            self.assertEqual(len(decisions), 1)
            self.assertEqual(decisions[0]["producer"], "analyze")
            self.assertEqual(decisions[0]["identity"]["ticker"], "CALTEST.ST")
            self.assertIsNone(decisions[0]["outcome"])

    def test_an_attached_outcome_persists_and_is_scored_on_reread(self):
        with isolated_ledger_home():
            tl = helpers.load("thesis_ledger")
            identity = {"company_name": "Calibration Test AB", "ticker": "CALTEST.ST",
                       "lei": "5493000CALTEST0000BB"}
            key = tl.ledger_key(identity)
            led = tl.new_ledger(key, identity, "Calibration Test AB")
            stored = tl.add_decision(led, self._minimal_decision())
            tl.save_ledger(led)

            # Attach an outcome directly (bypassing the network price lookup
            # cmd_attach would normally do) the same way cmd_attach does:
            # mutate the live dict in place, then save_ledger().
            led2 = tl.read_ledger(key)
            dec = tl.get_decision(led2, stored["decision_id"])
            self.assertIsNotNone(dec)
            status, outcome = cal.compute_horizon_outcome(
                dec, "3m", [{"date": "2026-04-01", "close": 112.0}],
                datetime.date(2026, 4, 2))
            self.assertEqual(status, "OK")
            outcome["corporate_actions"] = {"checked": False, "found": [],
                                            "comparability": "UNKNOWN", "note": ""}
            outcome["scenario"] = {"available": False, "landing": None, "detail": ""}
            outcome["superseded"] = cal.classify_supersession(dec)
            dec["outcome"] = {"3m": outcome}
            tl.save_ledger(led2)

            # Re-read from disk (a fresh module load and a fresh ledger read)
            # to prove this actually persisted, not just mutated in memory.
            tl_fresh = helpers.load("thesis_ledger")
            decisions = cal._collect_decisions(tl_fresh, None, None, None)
            self.assertEqual(len(decisions), 1)
            report = cal.build_calibration_report(decisions, "3m", min_n=1)
            self.assertEqual(report["producers"]["analyze"]["n_scored"], 1)


if __name__ == "__main__":
    unittest.main()
