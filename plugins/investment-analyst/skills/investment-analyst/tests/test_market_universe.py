#!/usr/bin/env python3
"""market_universe.py - the shared Swedish-market universe/liquidity layer,
offline.

EXTRACTED from screen_digest.py in v3.0.0 (see that module's own docstring,
and market_universe.py's own, for the full history). screen_digest.py and
screen_value.py both depend on this module now; test_screen_digest.py
already exercises every one of these functions exhaustively (LEI grouping,
the liquidity floor's three cut reasons, corporate-action ambiguity
refusal, the split-effective-date fix, the dual-listed-venue collision
rule, the worst-decile pool degeneracy rules, and so on) via screen_digest's
own re-exported names - screen_digest.group_by_issuer IS
market_universe.group_by_issuer, the same function object, so that suite
already covers its behaviour in depth.

This suite does NOT re-run that coverage. It tests the EXTRACTION BOUNDARY
instead:
  - that market_universe exposes every name screen_value.py's own import
    block aliases (an explicit list, so a future rename here breaks a test
    here rather than silently breaking screen_value.py at import time);
  - that the venue/MIC tables carried over are byte-for-byte what
    screen_digest.py's own module docstring and cut-reason logic have
    always used (a constant typo'd during the move would not show up in
    any behavioural test, since the wrong constant would just be
    self-consistently wrong everywhere it's read);
  - one direct, synthetic-input check per moved function confirming the
    symbol market_universe.py exports really is the working implementation
    (not confirmed by test_screen_digest.py's own tests, which exercise
    these through screen_digest's aliases - identical function objects, but
    a regression here would be a regression there too, and this suite is
    the one that should catch it first, since this is the module that
    actually defines the code);
  - the two documented edge cases explicitly called out in the brief this
    module was extracted under: LEI-less grouping falls back to the bare
    ISIN, and a class-suffixed name is stripped before resolution.

Every test here is offline - no live network endpoint is touched. Nothing
here needs @helpers.network.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers

helpers.bootstrap_path()
mu = helpers.load("market_universe")


# ---------------------------------------------------------------------------
# the extraction boundary itself: every name screen_value.py's own alias
# block (and screen_digest.py's own alias/wrapper block) expects to find
# ---------------------------------------------------------------------------

class ExtractionExposesEveryNameBothCallersNeed(unittest.TestCase):
    """screen_value.py's own import block (and screen_digest.py's, after its
    v3.0.0 rewire) aliases these names directly out of this module's
    globals. Asserted as an explicit list, not a loop over "whatever
    dir(mu) happens to contain" - a future rename must fail HERE, not as a
    silent AttributeError the first time a user runs `/screen`."""

    NAMES_SCREEN_VALUE_ALIASES = [
        "fetch_nasdaq_snapshot", "fetch_firds", "combine_universe",
        "group_by_issuer", "compute_returns", "compute_issuer_turnover",
        "select_primary_instrument", "apply_liquidity_floor",
        "check_corporate_actions", "data_confidence", "Budget",
        "NASDAQ_THROTTLE", "REGULATED_MICS", "ALL_MICS", "NASDAQ_MICS",
        "VENUE_LABEL", "DEFAULT_LIQUIDITY_FLOOR_SEK", "_strip_class_suffix",
    ]

    # Not aliased by screen_value.py (it never asked for XNGM/NSME turnover
    # or the 130-day return fetch - see market_universe.py's own module
    # docstring for why), but still needed for screen_digest.py's own
    # wrapper functions (_instrument_turnover_sek, check_corporate_actions)
    # and for compute_returns/select_primary_instrument's own internals.
    ADDITIONAL_NAMES_SCREEN_DIGEST_WRAPPERS_NEED = [
        "_instrument_turnover_sek", "_num", "percentile_rank", "WINDOW_DAYS",
        "OTHER_MICS", "RateLimiter",
    ]

    def test_every_screen_value_alias_target_exists(self):
        missing = [n for n in self.NAMES_SCREEN_VALUE_ALIASES if not hasattr(mu, n)]
        self.assertEqual(missing, [], "market_universe.py is missing names "
                                      "screen_value.py's import block aliases: %r" % missing)

    def test_every_screen_value_alias_target_is_not_none(self):
        for n in self.NAMES_SCREEN_VALUE_ALIASES:
            self.assertIsNotNone(getattr(mu, n), "%s must not be None" % n)

    def test_every_screen_digest_wrapper_dependency_exists(self):
        missing = [n for n in self.ADDITIONAL_NAMES_SCREEN_DIGEST_WRAPPERS_NEED
                  if not hasattr(mu, n)]
        self.assertEqual(missing, [], "market_universe.py is missing names "
                                      "screen_digest.py's own wrappers depend on: %r" % missing)

    def test_the_functions_in_the_list_are_callable(self):
        callables = [n for n in self.NAMES_SCREEN_VALUE_ALIASES
                    if n not in ("REGULATED_MICS", "ALL_MICS", "NASDAQ_MICS",
                                "VENUE_LABEL", "DEFAULT_LIQUIDITY_FLOOR_SEK",
                                "NASDAQ_THROTTLE")]
        for n in callables:
            self.assertTrue(callable(getattr(mu, n)), "%s must be callable" % n)


# ---------------------------------------------------------------------------
# venue/MIC tables: byte-for-byte what they always were
# ---------------------------------------------------------------------------

class VenueTablesAreUnchangedByTheMove(unittest.TestCase):
    def test_nasdaq_mics_are_the_two_nordic_shares_covers(self):
        self.assertEqual(mu.NASDAQ_MICS, ("XSTO", "SSME"))

    def test_other_mics_are_the_three_identity_only_venues(self):
        self.assertEqual(mu.OTHER_MICS, ("XSAT", "XNGM", "NSME"))

    def test_all_mics_is_nasdaq_plus_other_in_order(self):
        self.assertEqual(mu.ALL_MICS, ("XSTO", "SSME", "XSAT", "XNGM", "NSME"))

    def test_regulated_mics_are_xsto_and_xngm_only(self):
        self.assertEqual(mu.REGULATED_MICS, {"XSTO", "XNGM"})

    def test_venue_label_covers_all_five_mics(self):
        self.assertEqual(set(mu.VENUE_LABEL), set(mu.ALL_MICS))
        self.assertEqual(mu.VENUE_LABEL["XSTO"], "Nasdaq Stockholm (main market)")
        self.assertEqual(mu.VENUE_LABEL["SSME"],
                         "Nasdaq First North Growth Market Sweden")

    def test_default_liquidity_floor_is_two_million_sek(self):
        self.assertEqual(mu.DEFAULT_LIQUIDITY_FLOOR_SEK, 2_000_000.0)

    def test_window_days_covers_1w_1m_3m(self):
        self.assertEqual(mu.WINDOW_DAYS, {"1w": 7, "1m": 30, "3m": 90})

    def test_nasdaq_throttle_is_a_ratelimiter_instance(self):
        self.assertIsInstance(mu.NASDAQ_THROTTLE, mu.RateLimiter)


# ---------------------------------------------------------------------------
# group_by_issuer: LEI grouping, with the documented bare-ISIN fallback
# ---------------------------------------------------------------------------

class GroupByIssuerFallsBackToBareIsinWithNoLei(unittest.TestCase):
    def test_two_lei_matched_rows_collapse_to_one_issuer(self):
        rows = [
            {"isin": "SE0000000101", "lei": "LEI-INV", "name": "Investor A",
             "orderbookId": "OB-A"},
            {"isin": "SE0000000102", "lei": "LEI-INV", "name": "Investor B",
             "orderbookId": "OB-B"},
        ]
        issuers = mu.group_by_issuer(rows)
        self.assertEqual(len(issuers), 1)
        self.assertEqual(sorted(issuers[0]["isins"]),
                         ["SE0000000101", "SE0000000102"])
        self.assertEqual(issuers[0]["key"], "LEI-INV")

    def test_no_lei_falls_back_to_isin_prefixed_key(self):
        rows = [{"isin": "SE0000000999", "lei": None, "name": "No LEI AB",
                "orderbookId": None}]
        issuers = mu.group_by_issuer(rows)
        self.assertEqual(len(issuers), 1)
        self.assertEqual(issuers[0]["key"], "isin:SE0000000999")

    def test_two_rows_with_no_lei_and_different_isins_stay_separate(self):
        """A missing LEI must never accidentally merge two DIFFERENT
        issuers under one grouping key - the bare-ISIN fallback key is
        per-ISIN, not a shared "no LEI" bucket."""
        rows = [{"isin": "SE0000000001", "lei": None, "name": "A AB",
                "orderbookId": None},
               {"isin": "SE0000000002", "lei": None, "name": "B AB",
                "orderbookId": None}]
        issuers = mu.group_by_issuer(rows)
        self.assertEqual(len(issuers), 2)


# ---------------------------------------------------------------------------
# combine_universe: the dual-listed-venue collision rule (M7)
# ---------------------------------------------------------------------------

class CombineUniverseKeepsTheRegulatedVenueOnCollision(unittest.TestCase):
    def test_regulated_venue_wins_regardless_of_encounter_order(self):
        firds_mtf_first = {
            "SSME": {"instruments": [{"isin": "SE0008294953", "name": "Dual AB",
                                     "lei": "LEI-DUAL"}]},
            "XSTO": {"instruments": [{"isin": "SE0008294953", "name": "Dual AB",
                                     "lei": "LEI-DUAL"}]},
        }
        combined = mu.combine_universe(None, None, firds_mtf_first, ["SSME", "XSTO"])
        row = next(r for r in combined if r["isin"] == "SE0008294953")
        self.assertEqual(row["mic"], "XSTO")
        self.assertIn("SSME", row["also_on"])


# ---------------------------------------------------------------------------
# compute_returns: window arithmetic and the last-session carry-through the
# liquidity floor is computed from
# ---------------------------------------------------------------------------

class ComputeReturnsWindowArithmetic(unittest.TestCase):
    def test_windows_and_last_bar_carry_through(self):
        bars = [{"date": "2026-06-01", "close": 100.0, "volume": 5.0},
               {"date": "2026-07-31", "close": 90.0, "volume": 7.0},
               {"date": "2026-08-31", "close": 80.0, "volume": 9.0}]
        ret = mu.compute_returns(bars)
        self.assertEqual(ret["as_of"], "2026-08-31")
        self.assertEqual(ret["last_close"], 80.0)
        self.assertEqual(ret["last_volume"], 9.0)
        self.assertAlmostEqual(ret["windows"]["1m"]["pct"], (80.0 / 90.0 - 1) * 100.0)

    def test_no_usable_bars_returns_none(self):
        self.assertIsNone(mu.compute_returns([]))


# ---------------------------------------------------------------------------
# select_primary_instrument: last-session turnover, not the intraday
# screener snapshot (B1)
# ---------------------------------------------------------------------------

class SelectPrimaryInstrumentUsesLastSessionTurnover(unittest.TestCase):
    def test_higher_last_session_turnover_wins(self):
        issuer = {
            "instruments": [
                {"isin": "SE-A", "orderbookId": "OB-A", "currency": "SEK"},
                {"isin": "SE-B", "orderbookId": "OB-B", "currency": "SEK"},
            ],
            "primary": {"isin": "SE-A", "orderbookId": "OB-A", "currency": "SEK"},
        }
        returns_by_obid = {
            "OB-A": {"status": "checked", "last_close": 100.0, "last_volume": 10.0},
            "OB-B": {"status": "checked", "last_close": 100.0, "last_volume": 900.0},
        }
        row, _ret, sek = mu.select_primary_instrument(issuer, returns_by_obid)
        self.assertEqual(row["isin"], "SE-B")
        self.assertEqual(sek, 90_000.0)


# ---------------------------------------------------------------------------
# apply_liquidity_floor: the three cut reasons, and an unresolved turnover
# passing through uncut
# ---------------------------------------------------------------------------

class ApplyLiquidityFloorCutReasons(unittest.TestCase):
    def test_ok_below_floor_no_source_and_did_not_trade_all_counted_separately(self):
        issuers = [
            {"turnover_status": "ok", "turnover_sek": 10_000_000.0},
            {"turnover_status": "ok", "turnover_sek": 500.0},
            {"turnover_status": "no_source", "turnover_sek": None},
            {"turnover_status": "ok", "turnover_sek": 0.0},
        ]
        survivors, cuts = mu.apply_liquidity_floor(issuers, 1_000_000.0,
                                                    include_illiquid=False)
        self.assertEqual(len(survivors), 1)
        self.assertEqual(cuts, {"no_price_source": 1, "below_floor": 1, "did_not_trade": 1})

    def test_unresolved_turnover_survives_uncut(self):
        issuers = [{"turnover_status": "unresolved", "turnover_sek": None,
                   "turnover_error": "no dated FX rate for EUR"}]
        survivors, cuts = mu.apply_liquidity_floor(issuers, 1_000_000.0,
                                                    include_illiquid=False)
        self.assertEqual(len(survivors), 1)
        self.assertEqual(cuts, {"no_price_source": 0, "below_floor": 0, "did_not_trade": 0})


# ---------------------------------------------------------------------------
# _strip_class_suffix: share-class suffix handling
# ---------------------------------------------------------------------------

class StripClassSuffixHandling(unittest.TestCase):
    def test_strips_series_suffix(self):
        self.assertEqual(mu._strip_class_suffix("Atlas Copco AB ser. A"), "Atlas Copco AB")
        self.assertEqual(mu._strip_class_suffix("Investor AB ser. A"), "Investor AB")

    def test_strips_suffix_after_a_comma(self):
        self.assertEqual(mu._strip_class_suffix("Volvo, AB ser. B"), "Volvo, AB")

    def test_leaves_an_unsuffixed_name_alone(self):
        self.assertEqual(mu._strip_class_suffix("Evolution AB"), "Evolution AB")

    def test_none_is_handled(self):
        self.assertEqual(mu._strip_class_suffix(None), "")


# ---------------------------------------------------------------------------
# check_corporate_actions: reachable directly, degrades cleanly, and the
# v3.0.0 correction (splits no longer imply a technical artefact by
# themselves) is documented rather than silently reverted
# ---------------------------------------------------------------------------

class CheckCorporateActionsDirectOnMarketUniverse(unittest.TestCase):
    def test_degrades_when_corporate_actions_sibling_missing(self):
        real = mu.corporate_actions
        mu.corporate_actions = None
        try:
            result = mu.check_corporate_actions("Any AB", "2026-08-01", "2026-08-25",
                                                "2026-08-31")
        finally:
            mu.corporate_actions = real
        self.assertEqual(result["status"], "not checked")
        self.assertEqual(result["since_last_close"]["status"], "not checked")

    def test_a_split_in_window_is_still_flagged_as_a_breaking_action(self):
        """The v3.0.0 correction (nordic_shares' price series is back-
        adjusted for splits, see this function's own docstring) changed
        the WORDING of what a hit here means, not the classification
        itself - a confirmed split still routes to has_breaking_action,
        alongside rights issues/spin-offs/dividends, none of which are
        confirmed adjusted. This guards against that classification being
        silently dropped in a future edit."""
        class FakeCA(object):
            BREAKS_PER_SHARE = {"SPLIT", "RIGHTS_ISSUE"}

            @staticmethod
            def _norm(s):
                return (s or "").strip().lower()

            @staticmethod
            def resolve_company(name):
                return [{"company": name, "announcements_in_probe": 1}]

            @staticmethod
            def corporate_actions_between(company, date_from, date_to, pages=2):
                return [{"date": "2026-08-15", "type": "SPLIT", "title": "10:1 split"}]

            @staticmethod
            def split_adjustment_factor(company, date_from, date_to, pages=3):
                return {"confirmed_splits": [], "other_actions_in_window": []}

        real = mu.corporate_actions
        mu.corporate_actions = FakeCA
        try:
            result = mu.check_corporate_actions("Splitty AB", "2026-08-01", "2026-08-31",
                                                "2026-08-31")
        finally:
            mu.corporate_actions = real
        self.assertTrue(result["has_breaking_action"])

    def test_never_reads_the_numeric_split_factor(self):
        """Point 2 of the v3.0.0 correction: split_adjustment_factor()'s
        `factor` is scoped to per-share fundamentals only and must never
        touch a price or a price ratio. This function must only ever read
        `confirmed_splits` dates from that call - never the sibling
        `factor` key some other fixture might carry."""
        class FakeCAWithSuspiciousFactor(object):
            BREAKS_PER_SHARE = {"SPLIT"}

            @staticmethod
            def _norm(s):
                return (s or "").strip().lower()

            @staticmethod
            def resolve_company(name):
                return [{"company": name, "announcements_in_probe": 1}]

            @staticmethod
            def corporate_actions_between(company, date_from, date_to, pages=2):
                return []

            @staticmethod
            def split_adjustment_factor(company, date_from, date_to, pages=3):
                # A `factor` far from 1.0 must have zero effect on anything
                # this function returns - it is never read.
                return {"confirmed_splits": [{"date": "2026-08-15", "kind": "SPLIT",
                                             "terms": "10:1", "factor": 10.0}],
                       "factor": 10.0, "other_actions_in_window": []}

        real = mu.corporate_actions
        mu.corporate_actions = FakeCAWithSuspiciousFactor
        try:
            result = mu.check_corporate_actions("Splitty AB", "2026-08-01", "2026-08-31",
                                                "2026-08-31", price=100.0)
        finally:
            mu.corporate_actions = real
        # The only price-shaped field this function ever emits is a
        # dividend yield, and only for DIVIDEND events - a SPLIT event must
        # carry no such field at all, confirming the numeric factor above
        # was never multiplied into anything.
        for ev in result["events"]:
            self.assertNotIn("dividend_yield_pct", ev)


# ---------------------------------------------------------------------------
# data_confidence
# ---------------------------------------------------------------------------

class DataConfidence(unittest.TestCase):
    def test_regulated_venue_is_flagged_esef_covered(self):
        conf = mu.data_confidence("XSTO")
        self.assertTrue(conf["regulated_market"])
        self.assertTrue(conf["esef_applies"])

    def test_mtf_venue_is_flagged_not_esef(self):
        conf = mu.data_confidence("SSME")
        self.assertFalse(conf["regulated_market"])
        self.assertFalse(conf["esef_applies"])


# ---------------------------------------------------------------------------
# Budget: the wall-clock ceiling both screen_digest.py and screen_value.py
# rely on for bounded parallel fetches
# ---------------------------------------------------------------------------

class BudgetWallClockCeiling(unittest.TestCase):
    def test_zero_or_negative_seconds_means_no_ceiling(self):
        b = mu.Budget(0)
        self.assertFalse(b.exceeded())
        self.assertIsNone(b.remaining())

    def test_a_spent_budget_reports_exceeded(self):
        b = mu.Budget(0.01)
        import time
        time.sleep(0.05)
        self.assertTrue(b.exceeded())
        self.assertEqual(b.remaining(), 0.0)


# ---------------------------------------------------------------------------
# attach_market_cap: Volvo defect (two classes at different prices),
# partial results, and FX conversion
# ---------------------------------------------------------------------------

class AttachMarketCapTwoClassesDifferentPrices(unittest.TestCase):
    """The Volvo defect: two share classes at DIFFERENT prices must give
    (sharesA × priceA) + (sharesB × priceB), never a blended price.

    If this test fails, the sum is wrong and we are incorrectly treating
    the two classes' market caps as if they could be computed from a single
    average price times total shares."""

    def test_two_classes_at_different_prices_sum_correctly(self):
        """Class A: 1M shares × 100 SEK = 100M SEK
           Class B: 2M shares × 50 SEK = 100M SEK
           Correct total: 200M SEK
           Wrong (blended): (3M shares × average price) gives wrong answer."""
        mu_test = helpers.load("market_universe")

        issuer = {
            "name": "Volvo AB",
            "instruments": [
                {"symbol": "VOLV_A", "orderbookId": "OB-A", "currency": "SEK",
                 "price": 100.0},
                {"symbol": "VOLV_B", "orderbookId": "OB-B", "currency": "SEK",
                 "price": 50.0},
            ]
        }

        def mock_summary(obid):
            if obid == "OB-A":
                return {"shares": 1_000_000.0}
            elif obid == "OB-B":
                return {"shares": 2_000_000.0}
            return {"shares": None}

        real_summary = mu_test.nordic_shares.summary if mu_test.nordic_shares else None
        try:
            if mu_test.nordic_shares:
                mu_test.nordic_shares.summary = mock_summary
                mu_test.attach_market_cap([issuer])
                # 1M * 100 + 2M * 50 = 100M + 100M = 200M
                self.assertEqual(issuer["market_cap_sek"], 200_000_000.0)
                self.assertEqual(issuer["market_cap_status"], "checked")
        finally:
            if real_summary and mu_test.nordic_shares:
                mu_test.nordic_shares.summary = real_summary


class AttachMarketCapMissingShareCount(unittest.TestCase):
    def test_one_class_missing_shares_becomes_partial(self):
        """One class has a share count, the other returns shares: None.
        Result is marked PARTIAL, not treated as zero."""
        mu_test = helpers.load("market_universe")

        issuer = {
            "name": "Mixed AB",
            "instruments": [
                {"symbol": "MIX_A", "orderbookId": "OB-A", "currency": "SEK",
                 "price": 100.0},
                {"symbol": "MIX_B", "orderbookId": "OB-B", "currency": "SEK",
                 "price": 100.0},
            ]
        }

        def mock_summary(obid):
            if obid == "OB-A":
                return {"shares": 1_000_000.0}
            elif obid == "OB-B":
                return {"shares": None}
            return {"shares": None}

        real_summary = mu_test.nordic_shares.summary if mu_test.nordic_shares else None
        try:
            if mu_test.nordic_shares:
                mu_test.nordic_shares.summary = mock_summary
                mu_test.attach_market_cap([issuer])
                self.assertEqual(issuer["market_cap_status"], "partial")
                self.assertIn("MIX_B", issuer["market_cap_basis"])
                self.assertIn("missing share count", issuer["market_cap_basis"])
                # Partial still has the sum from MIX_A
                self.assertEqual(issuer["market_cap_sek"], 100_000_000.0)
        finally:
            if real_summary and mu_test.nordic_shares:
                mu_test.nordic_shares.summary = real_summary


class AttachMarketCapNoInstrumentsAtAll(unittest.TestCase):
    def test_empty_instruments_list_becomes_not_checked(self):
        """Issuer with no instruments at all (empty list).
        Status is NOT_CHECKED, market_cap_sek is None."""
        mu_test = helpers.load("market_universe")

        issuer = {
            "name": "NoInstruments AB",
            "instruments": []
        }

        mu_test.attach_market_cap([issuer])
        self.assertEqual(issuer["market_cap_status"], "not_checked")
        self.assertIsNone(issuer["market_cap_sek"])
        self.assertIn("no instruments", issuer["market_cap_basis"])


class AttachMarketCapSummaryRaisesDoesNotPropagate(unittest.TestCase):
    def test_summary_exception_does_not_propagate(self):
        """When nordic_shares.summary() raises an exception, the issuer
        records the error and the loop continues to the next issuer."""
        mu_test = helpers.load("market_universe")

        issuer_a = {
            "name": "Good AB",
            "instruments": [
                {"symbol": "GOOD", "orderbookId": "OB-GOOD", "currency": "SEK",
                 "price": 100.0},
            ]
        }
        issuer_b = {
            "name": "Bad AB",
            "instruments": [
                {"symbol": "BAD", "orderbookId": "OB-BAD", "currency": "SEK",
                 "price": 100.0},
            ]
        }

        def mock_summary(obid):
            if obid == "OB-GOOD":
                return {"shares": 1_000_000.0}
            elif obid == "OB-BAD":
                raise ValueError("API error for OB-BAD")
            return {"shares": None}

        real_summary = mu_test.nordic_shares.summary if mu_test.nordic_shares else None
        try:
            if mu_test.nordic_shares:
                mu_test.nordic_shares.summary = mock_summary
                # Both issuers together - should not raise
                mu_test.attach_market_cap([issuer_a, issuer_b])

                # Good one should succeed
                self.assertEqual(issuer_a["market_cap_status"], "checked")
                self.assertEqual(issuer_a["market_cap_sek"], 100_000_000.0)

                # Bad one should record the error, not raise
                self.assertEqual(issuer_b["market_cap_status"], "not_checked")
                self.assertIn("API error", issuer_b["market_cap_basis"])
        finally:
            if real_summary and mu_test.nordic_shares:
                mu_test.nordic_shares.summary = real_summary


class AttachMarketCapNonSEKCurrency(unittest.TestCase):
    def test_non_sek_currency_goes_through_fx_path(self):
        """A class priced in EUR should be FX-converted to SEK using
        nordic_shares._fx_convert_to_sek."""
        mu_test = helpers.load("market_universe")

        issuer = {
            "name": "Euro AB",
            "instruments": [
                {"symbol": "EUR_SHARE", "orderbookId": "OB-EUR", "currency": "EUR",
                 "price": 100.0},
            ]
        }

        def mock_summary(obid):
            return {"shares": 1_000_000.0}

        def mock_fx_convert(raw_dict):
            # Simulate EUR to SEK at 11.5x
            if "EUR" in raw_dict:
                return {"total_sek": raw_dict["EUR"] * 11.5}
            return {}

        real_summary = mu_test.nordic_shares.summary if mu_test.nordic_shares else None
        real_fx = mu_test.nordic_shares._fx_convert_to_sek if mu_test.nordic_shares else None
        try:
            if mu_test.nordic_shares:
                mu_test.nordic_shares.summary = mock_summary
                mu_test.nordic_shares._fx_convert_to_sek = mock_fx_convert
                mu_test.attach_market_cap([issuer])

                self.assertEqual(issuer["market_cap_status"], "checked")
                # 1M shares × 100 EUR = 100M EUR = 1150M SEK at 11.5x
                self.assertEqual(issuer["market_cap_sek"], 1_150_000_000.0)
        finally:
            if real_summary and mu_test.nordic_shares:
                mu_test.nordic_shares.summary = real_summary
            if real_fx and mu_test.nordic_shares:
                mu_test.nordic_shares._fx_convert_to_sek = real_fx


# ---------------------------------------------------------------------------
# apply_size_band: Filtering by market cap with asymmetric PARTIAL handling
# ---------------------------------------------------------------------------

class ApplySizeBandBothBoundsNone(unittest.TestCase):
    def test_both_bounds_none_is_complete_noop(self):
        """When both cap_floor and cap_ceiling are None, every issuer
        survives, all cut counts are 0, and this is a complete no-op."""
        mu_test = helpers.load("market_universe")

        issuers = [
            {"name": "Tiny", "market_cap_sek": 1.0, "market_cap_status": "checked"},
            {"name": "Huge", "market_cap_sek": 1_000_000_000_000.0,
             "market_cap_status": "checked"},
            {"name": "None", "market_cap_sek": None, "market_cap_status": "not_checked"},
        ]

        survivors, cuts = mu_test.apply_size_band(issuers, cap_floor=None,
                                                   cap_ceiling=None)

        self.assertEqual(len(survivors), 3)
        self.assertEqual(cuts["above_ceiling"], 0)
        self.assertEqual(cuts["below_floor"], 0)
        self.assertEqual(cuts["no_market_cap"], 0)


class ApplySizeBandAboveCeiling(unittest.TestCase):
    def test_cap_above_ceiling_is_cut(self):
        """An issuer with market_cap_sek > cap_ceiling is cut and counted
        in above_ceiling."""
        mu_test = helpers.load("market_universe")

        issuers = [
            {"name": "TooLarge", "market_cap_sek": 50_000_000_000.0,
             "market_cap_status": "checked"},
        ]

        survivors, cuts = mu_test.apply_size_band(issuers, cap_floor=None,
                                                   cap_ceiling=10_000_000_000.0)

        self.assertEqual(len(survivors), 0)
        self.assertEqual(cuts["above_ceiling"], 1)
        self.assertEqual(issuers[0]["size_status"], "above ceiling (50,000,000,000 > 10,000,000,000 SEK)")


class ApplySizeBandBelowFloor(unittest.TestCase):
    def test_cap_below_floor_is_cut(self):
        """An issuer with market_cap_sek < cap_floor is cut and counted
        in below_floor."""
        mu_test = helpers.load("market_universe")

        issuers = [
            {"name": "TooSmall", "market_cap_sek": 100_000.0,
             "market_cap_status": "checked"},
        ]

        survivors, cuts = mu_test.apply_size_band(issuers, cap_floor=1_000_000.0,
                                                   cap_ceiling=None)

        self.assertEqual(len(survivors), 0)
        self.assertEqual(cuts["below_floor"], 1)
        self.assertEqual(issuers[0]["size_status"], "below floor (100,000 < 1,000,000 SEK)")


class ApplySizeBandNotCheckedNotCut(unittest.TestCase):
    def test_status_not_checked_survives_with_no_market_cap_count(self):
        """An issuer with market_cap_status == 'not_checked' is NOT cut,
        survives, and is counted in no_market_cap. It carries a size_status
        explaining why it was not screened."""
        mu_test = helpers.load("market_universe")

        issuers = [
            {"name": "Unresolved", "market_cap_sek": None,
             "market_cap_status": "not_checked",
             "market_cap_basis": "nordic_shares not importable"},
        ]

        survivors, cuts = mu_test.apply_size_band(
            issuers, cap_floor=1_000_000.0, cap_ceiling=100_000_000_000.0)

        self.assertEqual(len(survivors), 1)
        self.assertEqual(survivors[0]["name"], "Unresolved")
        self.assertEqual(cuts["no_market_cap"], 1)
        self.assertEqual(cuts["above_ceiling"], 0)
        self.assertEqual(cuts["below_floor"], 0)
        self.assertIn("not checked", issuers[0]["size_status"])


class ApplySizeBandPartialAboveCeiling(unittest.TestCase):
    """A PARTIAL cap is a FLOOR - some classes were counted, others could
    not be, so the true cap can only be HIGHER.

    This makes the two tests asymmetric, and the asymmetry is load-bearing:
    A partial cap ABOVE the ceiling IS cut (counted in above_ceiling), because
    a floor above the ceiling proves the true cap is above it too."""

    def test_partial_cap_above_ceiling_is_cut(self):
        """PARTIAL cap of 50M, ceiling of 40M: we know the true cap is at least
        50M, which is above 40M, so cut it. Refusing to cut would pass a company
        we KNOW is too big through as 'unscreened'."""
        mu_test = helpers.load("market_universe")

        issuers = [
            {"name": "PartialBig", "market_cap_sek": 50_000_000.0,
             "market_cap_status": "partial",
             "market_cap_basis": "sum across 1 of 2 listed classes; missing share count: CLASS_B"},
        ]

        survivors, cuts = mu_test.apply_size_band(
            issuers, cap_floor=None, cap_ceiling=40_000_000.0)

        self.assertEqual(len(survivors), 0)
        self.assertEqual(cuts["above_ceiling"], 1)
        self.assertIn("above ceiling on a PARTIAL cap", issuers[0]["size_status"])


class ApplySizeBandPartialBelowFloor(unittest.TestCase):
    """A PARTIAL cap is a FLOOR - some classes were counted, others could
    not be, so the true cap can only be HIGHER.

    A partial cap BELOW the floor is NOT cut - it goes to no_market_cap and
    survives, because a floor below the threshold proves nothing about the
    true value. The true cap might be above the floor."""

    def test_partial_cap_below_floor_is_not_cut(self):
        """PARTIAL cap of 5M, floor of 10M: we know the true cap is at least
        5M, which is below 10M, but we DON'T know the true value. It might be
        above the floor. This issuer survives, counted in no_market_cap."""
        mu_test = helpers.load("market_universe")

        issuers = [
            {"name": "PartialSmall", "market_cap_sek": 5_000_000.0,
             "market_cap_status": "partial",
             "market_cap_basis": "sum across 1 of 2 listed classes; missing share count: CLASS_B"},
        ]

        survivors, cuts = mu_test.apply_size_band(
            issuers, cap_floor=10_000_000.0, cap_ceiling=None)

        self.assertEqual(len(survivors), 1)
        self.assertEqual(survivors[0]["name"], "PartialSmall")
        self.assertEqual(cuts["below_floor"], 0)
        self.assertEqual(cuts["no_market_cap"], 1)
        self.assertIn("not checked - partial cap", issuers[0]["size_status"])


if __name__ == "__main__":
    unittest.main()
