#!/usr/bin/env python3
"""screen_metrics.py - pure functions for screening fundamentals, offline.

Covers:
  - drawdown_from_high: Peak-to-current price decline from unsorted bar series,
    skipping None closes, exact arithmetic on hand-built series.
  - return_over: Simple percent return over an exact date window, with
    date-boundary rules (at-or-before, not silently shorter).
  - margins_from_esef: Gross and operating margins from ESEF statement data,
    deriving gross_profit from revenue - cost_of_sales when absent.
  - margin_trend: Four-state trend categorization with 1.0pp boundary.
  - passes_margin_floor: Dual-threshold pass/fail with auditable reason strings.
  - rank_candidates: Stable sort of candidates by margin risk and drawdown depth.
"""
import helpers
import unittest

helpers.bootstrap_path()


class TestDrawdownFromHigh(unittest.TestCase):
    """drawdown_from_high(bars) - peak-to-current price decline."""

    def test_exact_arithmetic_hand_built_series(self):
        """Hand-built series with known high and drawdown percent."""
        bars = [
            {"date": "2024-01-01", "close": 100.0, "volume": 1000},
            {"date": "2024-01-02", "close": 150.0, "volume": 1100},
            {"date": "2024-01-03", "close": 120.0, "volume": 1200},
        ]
        result = helpers.load("screen_metrics").drawdown_from_high(bars)
        self.assertEqual(result["high"], 150.0)
        self.assertEqual(result["high_date"], "2024-01-02")
        self.assertEqual(result["last"], 120.0)
        self.assertEqual(result["last_date"], "2024-01-03")
        self.assertAlmostEqual(result["drawdown_pct"], -20.0, places=2)
        self.assertEqual(result["bars_used"], 3)
        self.assertEqual(result["span_days"], 2)

    def test_unsorted_input(self):
        """Bars arrive unsorted; function sorts by date."""
        bars = [
            {"date": "2024-01-03", "close": 120.0, "volume": 1200},
            {"date": "2024-01-01", "close": 100.0, "volume": 1000},
            {"date": "2024-01-02", "close": 150.0, "volume": 1100},
        ]
        result = helpers.load("screen_metrics").drawdown_from_high(bars)
        self.assertEqual(result["high"], 150.0)
        self.assertEqual(result["high_date"], "2024-01-02")
        self.assertEqual(result["last"], 120.0)
        self.assertEqual(result["last_date"], "2024-01-03")

    def test_none_closes_skipped(self):
        """Bars with close=None are skipped; others used."""
        bars = [
            {"date": "2024-01-01", "close": 100.0, "volume": 1000},
            {"date": "2024-01-02", "close": None, "volume": 1100},
            {"date": "2024-01-03", "close": 120.0, "volume": 1200},
        ]
        result = helpers.load("screen_metrics").drawdown_from_high(bars)
        self.assertEqual(result["bars_used"], 2)
        self.assertEqual(result["last"], 120.0)

    def test_all_time_high_series_returns_zero(self):
        """Series where last close equals high returns drawdown_pct=0.0."""
        bars = [
            {"date": "2024-01-01", "close": 100.0, "volume": 1000},
            {"date": "2024-01-02", "close": 110.0, "volume": 1100},
            {"date": "2024-01-03", "close": 150.0, "volume": 1200},
        ]
        result = helpers.load("screen_metrics").drawdown_from_high(bars)
        self.assertEqual(result["drawdown_pct"], 0.0)

    def test_fewer_than_two_usable_closes_returns_none(self):
        """Fewer than 2 usable closes (after filtering None) returns None."""
        bars = [{"date": "2024-01-01", "close": 100.0, "volume": 1000}]
        result = helpers.load("screen_metrics").drawdown_from_high(bars)
        self.assertIsNone(result)

    def test_all_none_closes_returns_none(self):
        """All closes are None returns None."""
        bars = [
            {"date": "2024-01-01", "close": None, "volume": 1000},
            {"date": "2024-01-02", "close": None, "volume": 1100},
        ]
        result = helpers.load("screen_metrics").drawdown_from_high(bars)
        self.assertIsNone(result)


class TestReturnOver(unittest.TestCase):
    """return_over(bars, days) - simple percent return over N days."""

    def test_exact_arithmetic(self):
        """Calculate simple percent return: (last - start) / start * 100."""
        bars = [
            {"date": "2024-01-01", "close": 100.0, "volume": 1000},
            {"date": "2024-01-02", "close": 105.0, "volume": 1100},
            {"date": "2024-01-03", "close": 110.0, "volume": 1200},
        ]
        result = helpers.load("screen_metrics").return_over(bars, 2)
        self.assertAlmostEqual(result, 10.0, places=2)

    def test_target_date_landing_exactly_on_bar(self):
        """Target date exactly matches a bar date."""
        bars = [
            {"date": "2024-01-01", "close": 100.0, "volume": 1000},
            {"date": "2024-01-03", "close": 110.0, "volume": 1100},
        ]
        result = helpers.load("screen_metrics").return_over(bars, 2)
        self.assertAlmostEqual(result, 10.0, places=2)

    def test_target_date_between_bars_picks_earlier(self):
        """Target date falls between bars; pick the earlier (at-or-before) one."""
        bars = [
            {"date": "2024-01-01", "close": 100.0, "volume": 1000},
            {"date": "2024-01-02", "close": 105.0, "volume": 1100},
            {"date": "2024-01-04", "close": 110.0, "volume": 1200},
        ]
        # 2 days back from 2024-01-04 is 2024-01-02; should pick it
        result = helpers.load("screen_metrics").return_over(bars, 2)
        self.assertAlmostEqual(result, 4.76190476, places=2)  # (110-105)/105*100

    def test_series_too_short_returns_none(self):
        """No bar at or before target date; return None rather than partial window."""
        bars = [
            {"date": "2024-01-05", "close": 100.0, "volume": 1000},
            {"date": "2024-01-06", "close": 110.0, "volume": 1100},
        ]
        # Target is 2024-01-04; no bar at or before that
        result = helpers.load("screen_metrics").return_over(bars, 2)
        self.assertIsNone(result)


class TestMarginsFromEsef(unittest.TestCase):
    """margins_from_esef(esef_by_year) - gross and operating margins."""

    def test_ab_volvo_fy2024_real_data(self):
        """AB Volvo FY2024: revenue 526.816B, cost_of_sales 382.767B,
        gross_profit 144.049B, operating_income 66.611B.
        Gross margin 27.34%, operating margin 12.64%."""
        esef = {
            "2024-12-31": {
                "revenue": 526_816_000_000.0,
                "cost_of_sales": 382_767_000_000.0,
                "gross_profit": 144_049_000_000.0,
                "operating_income": 66_611_000_000.0,
            }
        }
        result = helpers.load("screen_metrics").margins_from_esef(esef)
        self.assertAlmostEqual(
            result["2024-12-31"]["gross_margin_pct"], 27.34, places=2)
        self.assertAlmostEqual(
            result["2024-12-31"]["operating_margin_pct"], 12.64, places=2)

    def test_derive_gross_profit_from_revenue_minus_cost(self):
        """gross_profit absent; derive from revenue - cost_of_sales."""
        esef = {
            "2024-12-31": {
                "revenue": 1000.0,
                "cost_of_sales": 600.0,
                # gross_profit missing
                "operating_income": 200.0,
            }
        }
        result = helpers.load("screen_metrics").margins_from_esef(esef)
        # Derived gross_profit = 1000 - 600 = 400
        # Gross margin = 400 / 1000 = 40.0%
        self.assertAlmostEqual(
            result["2024-12-31"]["gross_margin_pct"], 40.0, places=2)

    def test_revenue_zero_yields_none_margins(self):
        """Revenue is 0.0; both margins are None (no ZeroDivisionError)."""
        esef = {
            "2024-12-31": {
                "revenue": 0.0,
                "cost_of_sales": 600.0,
                "gross_profit": None,
                "operating_income": 200.0,
            }
        }
        result = helpers.load("screen_metrics").margins_from_esef(esef)
        self.assertIsNone(result["2024-12-31"]["gross_margin_pct"])
        self.assertIsNone(result["2024-12-31"]["operating_margin_pct"])

    def test_revenue_none_yields_none_margins(self):
        """Revenue is None; both margins are None (no exception)."""
        esef = {
            "2024-12-31": {
                "revenue": None,
                "cost_of_sales": 600.0,
                "gross_profit": None,
                "operating_income": 200.0,
            }
        }
        result = helpers.load("screen_metrics").margins_from_esef(esef)
        self.assertIsNone(result["2024-12-31"]["gross_margin_pct"])
        self.assertIsNone(result["2024-12-31"]["operating_margin_pct"])

    def test_year_with_only_revenue_yields_none_margins(self):
        """Only revenue; cost_of_sales, gross_profit, operating_income all absent."""
        esef = {
            "2024-12-31": {
                "revenue": 1000.0,
            }
        }
        result = helpers.load("screen_metrics").margins_from_esef(esef)
        self.assertIsNone(result["2024-12-31"]["gross_margin_pct"])
        self.assertIsNone(result["2024-12-31"]["operating_margin_pct"])


class TestMarginTrend(unittest.TestCase):
    """margin_trend(margins_by_year) - expanding/eroding/stable/insufficient."""

    def test_expanding_more_than_1pp_above_earliest(self):
        """Latest > earliest + 1.0pp is 'expanding'."""
        margins = {
            "2022-12-31": {"operating_margin_pct": 10.0},
            "2024-12-31": {"operating_margin_pct": 11.5},
        }
        result = helpers.load("screen_metrics").margin_trend(margins)
        self.assertEqual(result, "expanding")

    def test_eroding_more_than_1pp_below_earliest(self):
        """Latest < earliest - 1.0pp is 'eroding'."""
        margins = {
            "2022-12-31": {"operating_margin_pct": 15.0},
            "2024-12-31": {"operating_margin_pct": 13.0},
        }
        result = helpers.load("screen_metrics").margin_trend(margins)
        self.assertEqual(result, "eroding")

    def test_stable_within_1pp_boundary(self):
        """Within +/- 1.0pp is 'stable'. Exactly 1.0pp is stable (boundary exclusive)."""
        # Test 0.5pp difference (stable)
        margins = {
            "2022-12-31": {"operating_margin_pct": 10.0},
            "2024-12-31": {"operating_margin_pct": 10.5},
        }
        result = helpers.load("screen_metrics").margin_trend(margins)
        self.assertEqual(result, "stable")

        # Test exactly 1.0pp (stable - boundary exclusive)
        margins = {
            "2022-12-31": {"operating_margin_pct": 10.0},
            "2024-12-31": {"operating_margin_pct": 11.0},
        }
        result = helpers.load("screen_metrics").margin_trend(margins)
        self.assertEqual(result, "stable")

    def test_insufficient_fewer_than_two_years(self):
        """Fewer than 2 years with operating_margin_pct is 'insufficient'."""
        margins = {
            "2024-12-31": {"operating_margin_pct": 15.0},
        }
        result = helpers.load("screen_metrics").margin_trend(margins)
        self.assertEqual(result, "insufficient")

    def test_insufficient_all_none_margins(self):
        """All operating_margin_pct are None is 'insufficient'."""
        margins = {
            "2022-12-31": {"operating_margin_pct": None},
            "2024-12-31": {"operating_margin_pct": None},
        }
        result = helpers.load("screen_metrics").margin_trend(margins)
        self.assertEqual(result, "insufficient")


class TestPassesMarginFloor(unittest.TestCase):
    """passes_margin_floor(latest_margins, gross_floor, op_floor) - dual threshold."""

    def test_passes_on_gross_floor_alone(self):
        """Passes if gross_margin_pct >= gross_floor."""
        latest = {"gross_margin_pct": 45.0, "operating_margin_pct": 10.0}
        passed, reason = helpers.load("screen_metrics").passes_margin_floor(
            latest, gross_floor=40.0, op_floor=15.0)
        self.assertTrue(passed)
        self.assertIn("45.0", reason)
        self.assertIn("40.0", reason)

    def test_passes_on_operating_floor_alone(self):
        """Passes if operating_margin_pct >= op_floor."""
        latest = {"gross_margin_pct": 30.0, "operating_margin_pct": 18.0}
        passed, reason = helpers.load("screen_metrics").passes_margin_floor(
            latest, gross_floor=40.0, op_floor=15.0)
        self.assertTrue(passed)
        self.assertIn("18.0", reason)
        self.assertIn("15.0", reason)

    def test_fails_both_floors(self):
        """Fails if both margins below their floors."""
        latest = {"gross_margin_pct": 30.0, "operating_margin_pct": 10.0}
        passed, reason = helpers.load("screen_metrics").passes_margin_floor(
            latest, gross_floor=40.0, op_floor=15.0)
        self.assertFalse(passed)
        self.assertIn("30.0", reason)
        self.assertIn("40.0", reason)
        self.assertIn("10.0", reason)
        self.assertIn("15.0", reason)

    def test_none_margins_fail(self):
        """None margins can never satisfy their floor."""
        latest = {"gross_margin_pct": None, "operating_margin_pct": None}
        passed, reason = helpers.load("screen_metrics").passes_margin_floor(
            latest, gross_floor=40.0, op_floor=15.0)
        self.assertFalse(passed)

    def test_ab_volvo_fy2024_fails_default_floor(self):
        """AB Volvo 27.34% gross, 12.64% operating fails 40/15 floor."""
        latest = {"gross_margin_pct": 27.34, "operating_margin_pct": 12.64}
        passed, reason = helpers.load("screen_metrics").passes_margin_floor(
            latest, gross_floor=40.0, op_floor=15.0)
        self.assertFalse(passed)
        self.assertIn("27.34", reason)
        self.assertIn("12.64", reason)

    # Tests for mode="either" (the default, must not change)
    def test_either_mode_passes_on_gross_alone_when_op_below_floor(self):
        """mode='either' (default): passes on gross alone (53.1, op 8.5, floors 40/15).
        Reason string is printed in screen output as audit trail for the cut."""
        latest = {"gross_margin_pct": 53.1, "operating_margin_pct": 8.5}
        passed, reason = helpers.load("screen_metrics").passes_margin_floor(
            latest, gross_floor=40.0, op_floor=15.0, mode="either")
        self.assertTrue(passed)
        self.assertEqual(reason, "gross margin 53.10% passes floor 40.00%")

    def test_either_mode_passes_on_operating_alone_when_gross_below_floor(self):
        """mode='either': passes on operating alone when gross is below its floor.
        Reason is printed in screen output as audit trail."""
        latest = {"gross_margin_pct": 35.0, "operating_margin_pct": 18.0}
        passed, reason = helpers.load("screen_metrics").passes_margin_floor(
            latest, gross_floor=40.0, op_floor=15.0, mode="either")
        self.assertTrue(passed)
        self.assertEqual(reason, "operating margin 18.00% passes floor 15.00%")

    def test_either_mode_fails_when_both_below_floor(self):
        """mode='either': fails when both margins are below their floors."""
        latest = {"gross_margin_pct": 30.0, "operating_margin_pct": 8.0}
        passed, reason = helpers.load("screen_metrics").passes_margin_floor(
            latest, gross_floor=40.0, op_floor=15.0, mode="either")
        self.assertFalse(passed)

    def test_either_mode_default_no_mode_arg_produces_same_result(self):
        """Calling with no mode= argument produces identical results to mode='either'.
        This pins the default did not move."""
        latest = {"gross_margin_pct": 53.1, "operating_margin_pct": 8.5}
        passed_default, reason_default = helpers.load(
            "screen_metrics").passes_margin_floor(
            latest, gross_floor=40.0, op_floor=15.0)
        passed_explicit, reason_explicit = helpers.load(
            "screen_metrics").passes_margin_floor(
            latest, gross_floor=40.0, op_floor=15.0, mode="either")
        self.assertEqual(passed_default, passed_explicit)
        self.assertEqual(reason_default, reason_explicit)

    # Tests for mode="both"
    def test_both_mode_passes_only_when_both_clear_floors(self):
        """mode='both': passes only when both margins clear their floors.
        Reason names both."""
        latest = {"gross_margin_pct": 45.0, "operating_margin_pct": 18.0}
        passed, reason = helpers.load("screen_metrics").passes_margin_floor(
            latest, gross_floor=40.0, op_floor=15.0, mode="both")
        self.assertTrue(passed)
        self.assertIn("gross", reason)
        self.assertIn("45.00%", reason)
        self.assertIn("40.00%", reason)
        self.assertIn("operating", reason)
        self.assertIn("18.00%", reason)
        self.assertIn("15.00%", reason)

    def test_both_mode_when_only_operating_fails_reason_does_not_claim_gross_failed(self):
        """mode='both': when ONLY operating margin fails, reason must mention
        operating margin failure but NOT claim the gross margin failed.
        This guards against: the reason claiming a passing margin failed.
        Audit trail: passing gross figure must not appear as a failure."""
        latest = {"gross_margin_pct": 45.0, "operating_margin_pct": 12.0}
        passed, reason = helpers.load("screen_metrics").passes_margin_floor(
            latest, gross_floor=40.0, op_floor=15.0, mode="both")
        self.assertFalse(passed)
        self.assertIn("operating", reason.lower())
        self.assertIn("12.00%", reason)
        # The passing gross figure must not appear in the reason
        # (45.00% is the gross, which passed)
        self.assertNotIn("45.00% <", reason)

    def test_both_mode_none_margin_is_not_available_not_zero_pct(self):
        """mode='both': a None margin is reported as 'not available', NOT as 0.00%.
        Reason must not contain '0.00%'. Audit trail: false claim that an
        untagged margin was zero must not appear in screen output."""
        latest = {"gross_margin_pct": None, "operating_margin_pct": 18.0}
        passed, reason = helpers.load("screen_metrics").passes_margin_floor(
            latest, gross_floor=40.0, op_floor=15.0, mode="both")
        self.assertFalse(passed)
        self.assertIn("not available", reason)
        self.assertNotIn("0.00%", reason)

    # Tests for mode="operating"
    def test_operating_mode_passes_when_operating_passes_floor_and_positive(self):
        """mode='operating': passes when operating >= floor and positive."""
        latest = {"gross_margin_pct": 30.0, "operating_margin_pct": 18.0}
        passed, reason = helpers.load("screen_metrics").passes_margin_floor(
            latest, gross_floor=40.0, op_floor=15.0, mode="operating")
        self.assertTrue(passed)
        self.assertIn("18.00%", reason)
        self.assertIn("15.00%", reason)

    def test_operating_mode_fails_when_negative_even_with_high_gross(self):
        """mode='operating': fails when operating is negative even if gross is high.
        This is the whole point of the mode: it demands actual profitability,
        not merely high margins from cost-cutting without generating profit."""
        latest = {"gross_margin_pct": 51.8, "operating_margin_pct": -14.9}
        passed, reason = helpers.load("screen_metrics").passes_margin_floor(
            latest, gross_floor=40.0, op_floor=15.0, mode="operating")
        self.assertFalse(passed)
        self.assertIn("not positive", reason)

    def test_operating_mode_fails_with_not_available_when_operating_is_none(self):
        """mode='operating': fails with 'not available' when operating is None."""
        latest = {"gross_margin_pct": 51.8, "operating_margin_pct": None}
        passed, reason = helpers.load("screen_metrics").passes_margin_floor(
            latest, gross_floor=40.0, op_floor=15.0, mode="operating")
        self.assertFalse(passed)
        self.assertIn("not available", reason)

    def test_operating_mode_gross_margin_ignored_entirely(self):
        """mode='operating': gross margin is ignored; passes with high operating
        even when gross is far below any floor. This mode isolates profitability
        from margin structure."""
        latest = {"gross_margin_pct": 25.0, "operating_margin_pct": 18.0}
        # gross 25.0 is below floor 40.0, but should be ignored
        passed, reason = helpers.load("screen_metrics").passes_margin_floor(
            latest, gross_floor=40.0, op_floor=15.0, mode="operating")
        self.assertTrue(passed)

    # Tests for validation
    def test_unknown_mode_raises_value_error_with_allowed_modes_in_message(self):
        """An unknown mode raises ValueError, and the message names the allowed modes."""
        latest = {"gross_margin_pct": 45.0, "operating_margin_pct": 18.0}
        with self.assertRaises(ValueError) as cm:
            helpers.load("screen_metrics").passes_margin_floor(
                latest, gross_floor=40.0, op_floor=15.0, mode="invalid")
        error_msg = str(cm.exception)
        self.assertIn("both", error_msg)
        self.assertIn("either", error_msg)
        self.assertIn("operating", error_msg)


class TestRankCandidates(unittest.TestCase):
    """rank_candidates(cands) - stable sort: eroding demoted, then drawdown, then margin."""

    def test_eroding_sorted_below_non_eroding(self):
        """Eroding margin trend sorts below expanding/stable, even if cheaper."""
        cands = [
            {
                "name": "cheap_eroding",
                "drawdown_pct": -50.0,
                "operating_margin_pct": 15.0,
                "margin_trend": "eroding",
            },
            {
                "name": "deeper_stable",
                "drawdown_pct": -40.0,
                "operating_margin_pct": 10.0,
                "margin_trend": "stable",
            },
        ]
        result = helpers.load("screen_metrics").rank_candidates(cands)
        # deeper_stable (non-eroding) should come first
        self.assertEqual(result[0]["name"], "deeper_stable")
        self.assertEqual(result[1]["name"], "cheap_eroding")

    def test_deeper_drawdown_first_among_same_trend(self):
        """Among same margin_trend, more negative drawdown_pct sorts first."""
        cands = [
            {
                "name": "shallow",
                "drawdown_pct": -20.0,
                "operating_margin_pct": 15.0,
                "margin_trend": "stable",
            },
            {
                "name": "deep",
                "drawdown_pct": -50.0,
                "operating_margin_pct": 15.0,
                "margin_trend": "stable",
            },
        ]
        result = helpers.load("screen_metrics").rank_candidates(cands)
        self.assertEqual(result[0]["name"], "deep")
        self.assertEqual(result[1]["name"], "shallow")

    def test_higher_operating_margin_first_among_same_drawdown(self):
        """Among same margin_trend and drawdown, higher operating_margin_pct first."""
        cands = [
            {
                "name": "low_margin",
                "drawdown_pct": -30.0,
                "operating_margin_pct": 10.0,
                "margin_trend": "stable",
            },
            {
                "name": "high_margin",
                "drawdown_pct": -30.0,
                "operating_margin_pct": 20.0,
                "margin_trend": "stable",
            },
        ]
        result = helpers.load("screen_metrics").rank_candidates(cands)
        self.assertEqual(result[0]["name"], "high_margin")
        self.assertEqual(result[1]["name"], "low_margin")

    def test_none_operating_margin_sorts_last(self):
        """None operating_margin_pct sorts after any number."""
        cands = [
            {
                "name": "with_margin",
                "drawdown_pct": -30.0,
                "operating_margin_pct": 10.0,
                "margin_trend": "stable",
            },
            {
                "name": "no_margin",
                "drawdown_pct": -30.0,
                "operating_margin_pct": None,
                "margin_trend": "stable",
            },
        ]
        result = helpers.load("screen_metrics").rank_candidates(cands)
        self.assertEqual(result[0]["name"], "with_margin")
        self.assertEqual(result[1]["name"], "no_margin")

    def test_stable_sort_on_ties(self):
        """Equal candidates keep input order (stable sort)."""
        cands = [
            {
                "name": "first",
                "drawdown_pct": -30.0,
                "operating_margin_pct": 15.0,
                "margin_trend": "stable",
            },
            {
                "name": "second",
                "drawdown_pct": -30.0,
                "operating_margin_pct": 15.0,
                "margin_trend": "stable",
            },
        ]
        result = helpers.load("screen_metrics").rank_candidates(cands)
        self.assertEqual(result[0]["name"], "first")
        self.assertEqual(result[1]["name"], "second")

    def test_input_list_not_mutated(self):
        """Original list is not mutated; return a new list."""
        cands = [
            {
                "name": "b",
                "drawdown_pct": -20.0,
                "operating_margin_pct": 15.0,
                "margin_trend": "stable",
            },
            {
                "name": "a",
                "drawdown_pct": -30.0,
                "operating_margin_pct": 15.0,
                "margin_trend": "stable",
            },
        ]
        original_order = [c["name"] for c in cands]
        result = helpers.load("screen_metrics").rank_candidates(cands)
        # Original list should still be in input order
        self.assertEqual([c["name"] for c in cands], original_order)
        # Result should be sorted
        self.assertEqual(result[0]["name"], "a")
        self.assertEqual(result[1]["name"], "b")


class TestLiveDataRegressions(unittest.TestCase):
    """Two crashes the clean hand-built fixtures above never reach.

    Both are shapes real Nordic data produces and no constructed fixture did:
    a null close on a halted session, and a candidate whose drawdown could not
    be scored. Each used to raise TypeError and take down the whole run rather
    than degrade the one row it belongs to.
    """

    def test_return_over_survives_none_close_at_both_endpoints(self):
        """A null close must be filtered, not fed into the subtraction.

        The last bar here is a halted session (close=None). Sorting the raw
        list made it last_close and raised TypeError; the return must instead
        be measured to the last bar that actually has a close.
        """
        bars = [
            {"date": "2024-01-01", "close": 100.0, "volume": 1000},
            {"date": "2024-06-01", "close": None, "volume": 0},
            {"date": "2025-01-01", "close": 120.0, "volume": 1200},
            {"date": "2025-01-02", "close": None, "volume": 0},
        ]
        result = helpers.load("screen_metrics").return_over(bars, 365)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 20.0, places=2)

    def test_rank_candidates_sorts_unscored_drawdown_last(self):
        """`None < float` is a TypeError; an unscored name ranks last instead."""
        cands = [
            {"drawdown_pct": -40.0, "operating_margin_pct": 20.0,
             "margin_trend": "stable"},
            {"drawdown_pct": None, "operating_margin_pct": 25.0,
             "margin_trend": "stable"},
            {"drawdown_pct": -50.0, "operating_margin_pct": 18.0,
             "margin_trend": "stable"},
        ]
        ranked = helpers.load("screen_metrics").rank_candidates(cands)
        self.assertEqual([c["drawdown_pct"] for c in ranked],
                         [-50.0, -40.0, None])


if __name__ == "__main__":
    unittest.main()
