#!/usr/bin/env python3
"""screen_digest.py - the daily fell-and-might-be-cheap screen, offline.

Every network-touching sibling call is monkeypatched at the module-attribute
level (mock.patch.object), the same convention test_portfolio_review.py uses
for its own layer-2 fetchers - no subprocess is spawned and no socket is ever
opened by this suite.

Covers, per the review this script was fixed against:
  - B1: the liquidity floor is computed from the LAST COMPLETED SESSION's
    turnover (close x volume from price history, FX-converted to SEK), never
    the Nasdaq screener's intraday `turnover` field - which is blank for the
    whole market before the open - and the pre-open "refuse to publish"
    guard fires when most of the screener carries no turnover at all.
  - B2: corporate-action and regulatory-news identity resolution refuses an
    ambiguous or unverified match rather than silently taking a bare
    search's top hit.
  - B3: a class-suffixed name ("... AB ser. A") is stripped before
    resolving, and a regulatory-news check that could not be completed is
    routed to its own NOT CLASSIFIED bucket, never folded into "fell on
    flows" by elimination.
  - M2: the time budget bounds COLLECTION of parallel work, not just
    submission, and a worker's SystemExit degrades one result rather than
    killing the run.
  - M3: short_se's group_by_company/belongs/trend calls are guarded; a
    shape-drift error degrades to "not checked", never raises out of run().
  - M4: the liquidity floor is FX-converted, and "no free feed for this
    venue" is told apart from "fed, but didn't trade".
  - M5: the worst-decile selection only ever selects names that FELL
    (pct < 0), a too-small or tie-blown-open pool reports cutoff as None
    and is capped/flagged rather than presented as a real decile.
  - M7: a dual-listed ISIN keeps its regulated-venue row on a collision.
  - LEI grouping collapsing two share classes (Investor A/B) into one
    candidate, with the more liquid class (by LAST-SESSION turnover) chosen
    as the tradeable line.
  - Swedish and English number formats surviving the parse.
"""
import json
import datetime
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

sd = load("screen_digest")


# ---------------------------------------------------------------------------
# small, direct unit tests - no patching, pure functions
# ---------------------------------------------------------------------------

class LeiGroupingCollapsesShareClasses(unittest.TestCase):
    def test_two_isins_same_lei_become_one_issuer(self):
        rows = [
            {"isin": "SE0000000101", "lei": "LEI-INV", "name": "Investor A",
             "mic": "XSTO", "currency": "SEK", "price": 150.0,
             "turnover": 8_000_000.0, "volume": 40000, "percent_change_1d": -0.5,
             "sector": "Financials", "orderbookId": "OB-A", "has_price_source": True},
            {"isin": "SE0000000102", "lei": "LEI-INV", "name": "Investor B",
             "mic": "XSTO", "currency": "SEK", "price": 152.0,
             "turnover": 60_000_000.0, "volume": 300000, "percent_change_1d": -0.6,
             "sector": "Financials", "orderbookId": "OB-B", "has_price_source": True},
            {"isin": "SE0000000900", "lei": "LEI-OTHER", "name": "Other AB",
             "mic": "XSTO", "currency": "SEK", "price": 30.0,
             "turnover": 1_000_000.0, "volume": 10000, "percent_change_1d": 0.1,
             "sector": "Industrials", "orderbookId": "OB-C", "has_price_source": True},
        ]
        issuers = sd.group_by_issuer(rows)
        self.assertEqual(len(issuers), 2, "997 ISIN lines is not 997 issuers")
        investor = next(i for i in issuers if i["lei"] == "LEI-INV")
        self.assertEqual(sorted(investor["isins"]),
                         ["SE0000000101", "SE0000000102"])

    def test_provisional_primary_is_just_the_first_priced_instrument(self):
        """group_by_issuer no longer picks the primary by (intraday)
        turnover - see B1. The real pick happens later, in
        select_primary_instrument, once every class's own LAST-SESSION
        turnover is known."""
        rows = [
            {"isin": "SE0000000101", "lei": "LEI-INV", "name": "Investor A",
             "mic": "XSTO", "currency": "SEK", "price": 150.0, "turnover": None,
             "volume": None, "percent_change_1d": None, "sector": "Financials",
             "orderbookId": "OB-A", "has_price_source": False},
            {"isin": "SE0000000102", "lei": "LEI-INV", "name": "Investor B",
             "mic": "XSTO", "currency": "SEK", "price": 152.0, "turnover": None,
             "volume": None, "percent_change_1d": None, "sector": "Financials",
             "orderbookId": "OB-B", "has_price_source": False},
        ]
        issuers = sd.group_by_issuer(rows)
        self.assertEqual(issuers[0]["primary"]["isin"], "SE0000000101",
                         "with no turnover data at all yet, the first instrument "
                         "with an orderbook id is the provisional primary")

    def test_no_lei_falls_back_to_the_bare_isin_as_the_grouping_key(self):
        rows = [{"isin": "SE0000000999", "lei": None, "name": "No LEI AB",
                "mic": "XSAT", "currency": "SEK", "price": None, "turnover": None,
                "volume": None, "percent_change_1d": None, "sector": None,
                "orderbookId": None, "has_price_source": False}]
        issuers = sd.group_by_issuer(rows)
        self.assertEqual(len(issuers), 1)
        self.assertEqual(issuers[0]["key"], "isin:SE0000000999")


class SelectPrimaryInstrumentUsesLastSessionTurnover(unittest.TestCase):
    """B1: the tradeable line is chosen from the LAST COMPLETED SESSION's
    turnover (close x volume out of price history), never the Nasdaq
    screener's intraday snapshot - which is blank for the whole market
    before the 09:00 open and used to hand this decision to whichever class
    happened to have a nonzero morning print."""

    def test_higher_last_session_turnover_wins_even_with_no_intraday_data(self):
        issuer = {
            "instruments": [
                {"isin": "SE-A", "orderbookId": "OB-A", "currency": "SEK"},
                {"isin": "SE-B", "orderbookId": "OB-B", "currency": "SEK"},
            ],
            "primary": {"isin": "SE-A", "orderbookId": "OB-A", "currency": "SEK"},
        }
        returns_by_obid = {
            "OB-A": {"status": "checked", "last_close": 100.0, "last_volume": 10.0},   # 1,000
            "OB-B": {"status": "checked", "last_close": 100.0, "last_volume": 900.0},  # 90,000
        }
        row, ret, sek = sd.select_primary_instrument(issuer, returns_by_obid)
        self.assertEqual(row["isin"], "SE-B")
        self.assertEqual(sek, 90_000.0)
        self.assertEqual(issuer["primary"]["isin"], "SE-B")

    def test_falls_back_to_the_provisional_primary_when_nothing_priced(self):
        issuer = {
            "instruments": [{"isin": "SE-A", "orderbookId": "OB-A", "currency": "SEK"}],
            "primary": {"isin": "SE-A", "orderbookId": "OB-A", "currency": "SEK"},
        }
        row, ret, sek = sd.select_primary_instrument(issuer, {})
        self.assertEqual(row["isin"], "SE-A")
        self.assertEqual(ret["status"], "not checked")
        self.assertIsNone(sek)


class InstrumentTurnoverFxConversion(unittest.TestCase):
    """M4: a non-SEK line's turnover must be FX-converted before comparison -
    Verisure-style (EUR turnover compared unconverted against a SEK floor)
    used to clear the floor by 11% while its true SEK turnover was 12x it."""

    def test_sek_line_needs_no_conversion(self):
        ret = {"status": "checked", "last_close": 100.0, "last_volume": 1000.0}
        sek, err = sd._instrument_turnover_sek({"currency": "SEK"}, ret)
        self.assertEqual(sek, 100_000.0)
        self.assertIsNone(err)

    def test_non_sek_line_is_converted_via_nordic_shares_dated_rate(self):
        ret = {"status": "checked", "last_close": 100.0, "last_volume": 1000.0}  # 100,000 EUR

        class FakeNordicShares(object):
            @staticmethod
            def _fx_convert_to_sek(cap_by_ccy):
                return {"lines": [], "total_sek": cap_by_ccy["EUR"] * 11.0}

        real = sd.nordic_shares
        sd.nordic_shares = FakeNordicShares
        try:
            sek, err = sd._instrument_turnover_sek({"currency": "EUR"}, ret)
        finally:
            sd.nordic_shares = real
        self.assertEqual(sek, 1_100_000.0)
        self.assertIsNone(err)

    def test_no_dated_fx_rate_is_reported_not_guessed(self):
        ret = {"status": "checked", "last_close": 100.0, "last_volume": 1000.0}

        class FakeNordicShares(object):
            @staticmethod
            def _fx_convert_to_sek(cap_by_ccy):
                return None     # no dated rate available

        real = sd.nordic_shares
        sd.nordic_shares = FakeNordicShares
        try:
            sek, err = sd._instrument_turnover_sek({"currency": "EUR"}, ret)
        finally:
            sd.nordic_shares = real
        self.assertIsNone(sek)
        self.assertIn("no dated FX rate", err)

    def test_unchecked_returns_have_no_turnover_and_no_error(self):
        sek, err = sd._instrument_turnover_sek({"currency": "SEK"},
                                               {"status": "not checked"})
        self.assertIsNone(sek)
        self.assertIsNone(err)


class OptionalNgmTurnoverIntegration(unittest.TestCase):
    """OPTIONAL INTEGRATION POINT: a sibling agent's venues_se.ngm_turnover()
    has landed - feature-detected via hasattr, called with NO date argument
    (letting it resolve its own last completed trading day), and its "_meta"
    key must never be treated as an ISIN. Once present, an XNGM/NSME issuer
    (no Nasdaq orderbook id at all) must stop being cut as source-less."""

    def test_fetch_strips_the_reserved_meta_key(self):
        class FakeVenuesSE(object):
            @staticmethod
            def ngm_turnover():
                return {"SE0000000001": {"turnover": 5_000_000.0, "last_price": 42.0,
                                         "currency": "SEK"},
                        "_meta": {"date": "2026-08-28", "partial": False}}

        real = sd.venues_se
        sd.venues_se = FakeVenuesSE
        try:
            data, err = sd.fetch_other_venue_turnover(["XNGM"])
        finally:
            sd.venues_se = real
        self.assertIsNone(err)
        self.assertNotIn("_meta", data)
        self.assertEqual(data["SE0000000001"]["turnover"], 5_000_000.0)

    def test_absent_function_is_a_silent_no_op(self):
        class FakeVenuesSENoNgm(object):
            pass

        real = sd.venues_se
        sd.venues_se = FakeVenuesSENoNgm
        try:
            data, err = sd.fetch_other_venue_turnover(["XNGM"])
        finally:
            sd.venues_se = real
        self.assertEqual(data, {})
        self.assertIsNone(err)

    def test_no_xngm_or_nsme_requested_skips_the_call_entirely(self):
        class FailIfCalled(object):
            @staticmethod
            def ngm_turnover():
                raise AssertionError("must not be called when XNGM/NSME are not requested")

        real = sd.venues_se
        sd.venues_se = FailIfCalled
        try:
            data, err = sd.fetch_other_venue_turnover(["XSTO", "SSME"])
        finally:
            sd.venues_se = real
        self.assertEqual(data, {})

    def test_compute_issuer_turnover_uses_the_ngm_figure_for_a_no_orderbook_issuer(self):
        """An XNGM issuer with no Nasdaq orderbook id at all must stop being
        cut as source-less once other_venue_turnover is populated."""
        issuer = {"instruments": [{"orderbookId": None, "currency": "SEK",
                                  "other_venue_turnover": 5_000_000.0}],
                 "primary": {"orderbookId": None, "currency": "SEK",
                            "other_venue_turnover": 5_000_000.0},
                 "primary_returns": {"status": "not checked"}}
        sd.compute_issuer_turnover(issuer)
        self.assertEqual(issuer["turnover_status"], "ok")
        self.assertEqual(issuer["turnover_sek"], 5_000_000.0)

    def test_compute_issuer_turnover_still_reports_no_source_when_ngm_absent(self):
        issuer = {"instruments": [{"orderbookId": None, "currency": "SEK",
                                  "other_venue_turnover": None}],
                 "primary": {"orderbookId": None, "currency": "SEK",
                            "other_venue_turnover": None},
                 "primary_returns": {"status": "not checked"}}
        sd.compute_issuer_turnover(issuer)
        self.assertEqual(issuer["turnover_status"], "no_source")


class LiquidityFloorCutsAndReports(unittest.TestCase):
    """B1/M4: apply_liquidity_floor is now a pure decision function over
    turnover_status/turnover_sek/turnover_error (computed upstream by
    compute_issuer_turnover from the LAST-SESSION bar) - never the intraday
    screener snapshot."""

    def test_ok_below_floor_no_source_and_did_not_trade_are_all_counted_separately(self):
        issuers = [
            {"turnover_status": "ok", "turnover_sek": 10_000_000.0},   # survives
            {"turnover_status": "ok", "turnover_sek": 500.0},          # below floor
            {"turnover_status": "no_source", "turnover_sek": None},    # no feed at all
            {"turnover_status": "ok", "turnover_sek": 0.0},            # fed, didn't trade
        ]
        survivors, cuts = sd.apply_liquidity_floor(issuers, 1_000_000.0,
                                                    include_illiquid=False)
        self.assertEqual(len(survivors), 1)
        self.assertEqual(cuts, {"no_price_source": 1, "below_floor": 1, "did_not_trade": 1})

    def test_did_not_trade_is_distinct_from_no_turnover_source(self):
        """A NASDAQ-covered name that simply had a quiet session (Nokia,
        Modelon, Qlucore-style) must never be told this toolkit has no feed
        for its venue at all - that used to be true only of XSAT/XNGM/NSME
        names with no feed whatsoever."""
        issuers = [{"turnover_status": "ok", "turnover_sek": 0.0}]
        survivors, cuts = sd.apply_liquidity_floor(issuers, 1_000_000.0,
                                                    include_illiquid=False)
        self.assertEqual(cuts["did_not_trade"], 1)
        self.assertEqual(cuts["no_price_source"], 0)
        self.assertIn("did not trade", issuers[0]["liquidity_status"])

    def test_include_illiquid_disables_the_cut_but_keeps_the_label(self):
        issuers = [{"turnover_status": "ok", "turnover_sek": 500.0}]
        survivors, cuts = sd.apply_liquidity_floor(issuers, 1_000_000.0,
                                                    include_illiquid=True)
        self.assertEqual(len(survivors), 1)
        self.assertEqual(survivors[0]["liquidity_status"][:11], "below floor")
        self.assertEqual(cuts["below_floor"], 1)

    def test_cut_counts_are_unconditional_regardless_of_include_illiquid(self):
        """With --include-illiquid, a cut-but-kept issuer must still count
        once against its cut reason - not zero times, and not twice against
        both its cut reason AND the survivors total in a way that stops the
        arithmetic reconciling."""
        issuers = [{"turnover_status": "ok", "turnover_sek": 500.0},
                  {"turnover_status": "no_source", "turnover_sek": None}]
        _, cuts_off = sd.apply_liquidity_floor(issuers, 1_000_000.0, include_illiquid=False)
        _, cuts_on = sd.apply_liquidity_floor(issuers, 1_000_000.0, include_illiquid=True)
        self.assertEqual(cuts_off, cuts_on)

    def test_unresolved_turnover_survives_uncut_pending_evidence(self):
        """No evidence either way (price history not checked, or no dated
        FX rate) must never be silently treated as clearing the floor OR
        cutting the issuer - it passes through, uncut, and is reported
        separately (see run()'s "returns: not checked" cut-stage counter)."""
        issuers = [{"turnover_status": "unresolved", "turnover_sek": None,
                   "turnover_error": "no dated FX rate for EUR"}]
        survivors, cuts = sd.apply_liquidity_floor(issuers, 1_000_000.0,
                                                    include_illiquid=False)
        self.assertEqual(len(survivors), 1)
        self.assertEqual(cuts, {"no_price_source": 0, "below_floor": 0, "did_not_trade": 0})
        self.assertIn("no dated FX rate", survivors[0]["liquidity_status"])


class PreMarketRunIsSupported(unittest.TestCase):
    """B1, after the fix: a blank intraday turnover is NORMAL before the 09:00
    open and must not stop the run.

    The liquidity floor reads the last completed daily bar, so a pre-open run
    has everything it needs - and yesterday's bar is unambiguously final,
    where an evening run has to judge whether today's session has settled.
    An earlier guard refused to publish whenever the screener field was blank;
    it outlived the dependency it protected and blocked the better schedule."""

    def test_run_proceeds_when_the_screener_is_blank_before_the_open(self):
        liq = {"OB-%d" % i: {"turnover": None, "volume": None, "percent_change_1d": None}
              for i in range(80)}
        liq.update({"OB-%d" % i: {"turnover": 1000.0, "volume": 10.0, "percent_change_1d": 0.1}
                   for i in range(80, 100)})   # 80 of 100 blank
        patchers = [
            mock.patch.object(sd, "fetch_nasdaq_snapshot", lambda market="STO": ([], liq, None)),
            mock.patch.object(sd, "fetch_firds", lambda mics: ({}, {})),
            mock.patch.object(sd, "today", lambda: datetime.date(2026, 8, 31)),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        args = mock.Mock(window="1m", venue="xsto,ssme", limit=20,
                         liquidity_floor=1_000_000.0, include_illiquid=False, budget=30.0)
        result = sd.run(args)   # must NOT raise: the floor does not read this field
        self.assertNotIn("REFUSING TO PUBLISH", json.dumps(result, default=str))
        self.assertIn("universe", result)

    def test_run_proceeds_when_the_screener_is_mostly_populated(self):
        liq = {"OB-%d" % i: {"turnover": 1000.0, "volume": 10.0, "percent_change_1d": 0.1}
              for i in range(100)}
        patchers = [
            mock.patch.object(sd, "fetch_nasdaq_snapshot", lambda market="STO": ([], liq, None)),
            mock.patch.object(sd, "fetch_firds", lambda mics: ({}, {})),
            mock.patch.object(sd, "today", lambda: datetime.date(2026, 8, 31)),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        args = mock.Mock(window="1m", venue="xsto,ssme", limit=20,
                         liquidity_floor=1_000_000.0, include_illiquid=False, budget=30.0)
        result = sd.run(args)          # must not raise
        self.assertEqual(result["universe"]["issuers"], 0)


class NumberParsingReusesTheOneParser(unittest.TestCase):
    """screen_digest.py must never write a second number parser - every raw
    string from the Nasdaq screener goes through mfn_news.to_number."""

    def test_english_comma_thousands_turnover(self):
        self.assertEqual(sd._num("30,054,559"), 30054559.0)

    def test_english_comma_thousands_volume(self):
        self.assertEqual(sd._num("151,286"), 151286.0)

    def test_signed_percentage_change(self):
        self.assertEqual(sd._num("+0.85%"), 0.85)

    def test_negative_percentage_change(self):
        self.assertEqual(sd._num("-1.20%"), -1.20)

    def test_nordic_decimal_comma_percentage(self):
        self.assertEqual(sd._num("-1,20%"), -1.20)

    def test_missing_value_does_not_raise(self):
        self.assertIsNone(sd._num(None))
        self.assertIsNone(sd._num(""))


class ComputeReturnsCarriesTheLastBarThrough(unittest.TestCase):
    def test_windows_and_percentile_and_last_volume(self):
        bars = [{"date": "2026-06-01", "close": 100.0, "volume": 5.0},
               {"date": "2026-07-31", "close": 90.0, "volume": 7.0},
               {"date": "2026-08-24", "close": 85.0, "volume": 8.0},
               {"date": "2026-08-31", "close": 80.0, "volume": 9.0}]
        ret = sd.compute_returns(bars)
        self.assertEqual(ret["as_of"], "2026-08-31")
        self.assertEqual(ret["last_close"], 80.0)
        self.assertEqual(ret["last_volume"], 9.0,
                         "the last bar's volume must be carried through - this is what "
                         "the liquidity floor is computed from (see B1)")
        self.assertAlmostEqual(ret["windows"]["1w"]["pct"], (80.0 / 85.0 - 1) * 100.0)
        self.assertAlmostEqual(ret["windows"]["1m"]["pct"], (80.0 / 90.0 - 1) * 100.0)

    def test_no_usable_bars_returns_none(self):
        self.assertIsNone(sd.compute_returns([]))
        self.assertIsNone(sd.compute_returns([{"date": "2026-08-31", "close": None}]))

    def test_insufficient_history_for_a_window_is_reported_not_guessed(self):
        bars = [{"date": "2026-08-30", "close": 10.0, "volume": 1.0},
               {"date": "2026-08-31", "close": 11.0, "volume": 1.0}]
        ret = sd.compute_returns(bars)
        self.assertIsNone(ret["windows"]["3m"]["pct"])
        self.assertEqual(ret["windows"]["3m"]["note"], "insufficient history for this window")


class PercentileRankIsStrictlyLessThan(unittest.TestCase):
    def test_minimum_of_a_falling_series_scores_zero(self):
        """The value being ranked must not count against itself - a
        monotonically falling series' minimum used to score 100/N (never 0)
        because <= counted the value's own bar."""
        closes = [100.0, 90.0, 80.0, 70.0, 60.0]
        self.assertEqual(sd.percentile_rank(closes, min(closes)), 0.0)

    def test_maximum_scores_close_to_100(self):
        closes = [100.0, 90.0, 80.0, 70.0, 60.0]
        self.assertEqual(sd.percentile_rank(closes, max(closes)), 80.0)

    def test_empty_closes_returns_none(self):
        self.assertIsNone(sd.percentile_rank([], 50.0))


class ParseVenuesTests(unittest.TestCase):
    def test_no_venue_means_all_five(self):
        self.assertEqual(sd._parse_venues(None), list(sd.ALL_MICS))
        self.assertEqual(sd._parse_venues(""), list(sd.ALL_MICS))

    def test_explicit_list_is_parsed_uppercased_in_order(self):
        self.assertEqual(sd._parse_venues("xsto,ssme"), ["XSTO", "SSME"])

    def test_unknown_venue_raises_value_error(self):
        with self.assertRaises(ValueError):
            sd._parse_venues("xosl")


class CombineUniverseKeepsTheRegulatedVenueOnCollision(unittest.TestCase):
    """M7: a dual-listed ISIN (Paradox Interactive: XSTO and SSME both) used
    to be silently clobbered by whichever MIC's FIRDS batch was processed
    last in dict order - which could downgrade an ESEF-covered regulated-
    market row to an MTF one. The regulated venue must always win, and the
    other MIC must be recorded in `also_on`, in EITHER encounter order."""

    def test_mtf_first_then_regulated_keeps_regulated(self):
        firds = {"SSME": {"instruments": [{"isin": "SE0008294953",
                                          "name": "Paradox Interactive", "lei": "LEI-PDX"}]},
                "XSTO": {"instruments": [{"isin": "SE0008294953",
                                         "name": "Paradox Interactive", "lei": "LEI-PDX"}]}}
        combined = sd.combine_universe(None, None, firds, ["SSME", "XSTO"])
        row = next(r for r in combined if r["isin"] == "SE0008294953")
        self.assertEqual(row["mic"], "XSTO")
        self.assertIn("SSME", row["also_on"])

    def test_regulated_first_then_mtf_still_keeps_regulated(self):
        firds = {"XSTO": {"instruments": [{"isin": "SE0016609846",
                                          "name": "Flat Capital", "lei": "LEI-FLAT"}]},
                "SSME": {"instruments": [{"isin": "SE0016609846",
                                         "name": "Flat Capital", "lei": "LEI-FLAT"}]}}
        combined = sd.combine_universe(None, None, firds, ["XSTO", "SSME"])
        row = next(r for r in combined if r["isin"] == "SE0016609846")
        self.assertEqual(row["mic"], "XSTO")
        self.assertIn("SSME", row["also_on"])

    def test_no_collision_is_unaffected(self):
        firds = {"XSTO": {"instruments": [{"isin": "SE0000000001",
                                          "name": "Solo AB", "lei": "LEI-SOLO"}]}}
        combined = sd.combine_universe(None, None, firds, ["XSTO"])
        row = combined[0]
        self.assertEqual(row["mic"], "XSTO")
        self.assertEqual(row["also_on"], [])


class EndpointFailureDegradesToUnchecked(unittest.TestCase):
    def test_no_orderbook_id_degrades_the_return_axis(self):
        result = sd.fetch_return_for_instrument({"orderbookId": None},
                                                datetime.date(2026, 8, 31))
        self.assertEqual(result["status"], "not checked")

    def test_price_history_raising_systemexit_degrades_not_raises(self):
        class FakeNordicShares(object):
            @staticmethod
            def price_history(*_a, **_k):
                raise SystemExit("DATA NOT AVAILABLE: simulated outage")
        real = sd.nordic_shares
        sd.nordic_shares = FakeNordicShares
        try:
            result = sd.fetch_return_for_instrument(
                {"orderbookId": "OB-1"}, datetime.date(2026, 8, 31))
        finally:
            sd.nordic_shares = real
        self.assertEqual(result["status"], "not checked")
        self.assertIn("simulated outage", result["reason"])

    def test_short_interest_source_unreachable_degrades_not_raises(self):
        result = sd.short_signal(None, {"lei": "LEI-X", "isins": []},
                                 datetime.date(2026, 8, 31))
        self.assertEqual(result["status"], "not checked")

    def test_short_signal_survives_a_shape_drift_error_in_belongs(self):
        """M3: short_se.belongs()/trend() used to be called from a plain
        loop in run() with no guard at all - a bare KeyError from a shape
        drift (company["names"] missing) killed the whole run."""
        class BrokenShortSE(object):
            @staticmethod
            def belongs(row, company):
                return company["names"][0] == row["issuer"]     # KeyError

        real = sd.short_se
        sd.short_se = BrokenShortSE
        try:
            short_data = {"companies_by_lei": {"LEI-X": {"lei": "LEI-X", "agg": []}},
                         "companies_by_isin": {}, "rows": [{"issuer": "x"}]}
            result = sd.short_signal(short_data, {"lei": "LEI-X", "isins": []},
                                     datetime.date(2026, 8, 31))
        finally:
            sd.short_se = real
        self.assertEqual(result["status"], "not checked")

    def test_load_short_data_survives_group_by_company_raising(self):
        """M3: group_by_company() used to sit OUTSIDE the try block guarding
        aggregated()/merged_rows() - a shape-drift error there killed the
        whole run instead of degrading this one axis."""
        class BrokenShortSE(object):
            @staticmethod
            def aggregated():
                return {}

            @staticmethod
            def merged_rows():
                return []

            @staticmethod
            def group_by_company(agg, rows):
                raise KeyError("names")

        real = sd.short_se
        sd.short_se = BrokenShortSE
        try:
            data, err = sd.load_short_data()
        finally:
            sd.short_se = real
        self.assertIsNone(data)
        self.assertIsNotNone(err)

    def test_corporate_action_check_degrades_when_sibling_missing(self):
        real = sd.corporate_actions
        sd.corporate_actions = None
        try:
            result = sd.check_corporate_actions("Any AB", "2026-08-01", "2026-08-25",
                                                "2026-08-31")
        finally:
            sd.corporate_actions = real
        self.assertEqual(result["status"], "not checked")
        self.assertEqual(result["since_last_close"]["status"], "not checked",
                         "a failed check must report 'not checked' for BOTH windows, "
                         "never silently imply 'no news'")

    def test_regulatory_news_check_degrades_when_sibling_missing(self):
        real = sd.mfn_news
        sd.mfn_news = None
        try:
            result = sd.check_regulatory_news("Any AB", "2026-08-01", "2026-08-25",
                                              "2026-08-31")
        finally:
            sd.mfn_news = real
        self.assertEqual(result["status"], "not checked")
        self.assertEqual(result["since_last_close"]["status"], "not checked",
                         "a failed check must report 'not checked' for BOTH windows, "
                         "never silently imply 'no news'")

    def test_regulatory_news_check_degrades_when_venues_se_missing(self):
        real = sd.venues_se
        sd.venues_se = None
        try:
            result = sd.check_regulatory_news("Any AB", "2026-08-01", "2026-08-25",
                                              "2026-08-31")
        finally:
            sd.venues_se = real
        self.assertEqual(result["status"], "not checked")
        self.assertEqual(result["since_last_close"]["status"], "not checked",
                         "a failed check must report 'not checked' for BOTH windows, "
                         "never silently imply 'no news'")


class TimeBudgetBoundsCollectionNotJustSubmission(unittest.TestCase):
    """M2: fetch_returns_parallel's budget check at submission time never
    bounded anything by itself - a stdlib ThreadPoolExecutor's queue is
    unbounded, so every already-submitted worker used to run to completion
    regardless of the budget. `as_completed(..., timeout=...)` is what
    actually bounds COLLECTION."""

    def test_a_tight_budget_leaves_slow_workers_uncollected(self):
        class SlowNordicShares(object):
            @staticmethod
            def price_history(obid, from_date, to_date):
                time.sleep(0.5)
                return [{"date": "2026-08-31", "close": 100.0, "volume": 10.0}]

        real = sd.nordic_shares
        sd.nordic_shares = SlowNordicShares
        try:
            budget = sd.Budget(0.05)
            rows = [{"orderbookId": "OB-%d" % i} for i in range(6)]
            t0 = time.monotonic()
            out = sd.fetch_returns_parallel(rows, datetime.date(2026, 8, 31),
                                            budget, max_workers=6)
            elapsed = time.monotonic() - t0
        finally:
            sd.nordic_shares = real
        self.assertLess(elapsed, 2.0,
                        "collection must be bounded by the budget (took %.2fs)" % elapsed)
        self.assertLess(len(out), len(rows),
                        "a budget this tight must leave some instruments uncollected")

    def test_ample_budget_collects_everything(self):
        class FastNordicShares(object):
            @staticmethod
            def price_history(obid, from_date, to_date):
                return [{"date": "2026-08-31", "close": 100.0, "volume": 10.0}]

        real = sd.nordic_shares
        sd.nordic_shares = FastNordicShares
        try:
            budget = sd.Budget(30.0)
            rows = [{"orderbookId": "OB-%d" % i} for i in range(6)]
            out = sd.fetch_returns_parallel(rows, datetime.date(2026, 8, 31),
                                            budget, max_workers=6)
        finally:
            sd.nordic_shares = real
        self.assertEqual(len(out), 6)
        self.assertFalse(budget.exceeded())

    def test_worker_raising_systemexit_degrades_one_result_not_the_pool(self):
        class ExplodingNordicShares(object):
            @staticmethod
            def price_history(*_a, **_k):
                raise SystemExit("DATA NOT AVAILABLE: simulated outage")

        real = sd.nordic_shares
        sd.nordic_shares = ExplodingNordicShares
        try:
            out = sd.fetch_returns_parallel([{"orderbookId": "OB-BOOM"}],
                                            datetime.date(2026, 8, 31), sd.Budget(30.0))
        finally:
            sd.nordic_shares = real
        self.assertEqual(out["OB-BOOM"]["status"], "not checked")


class DecileSelection(unittest.TestCase):
    def test_small_pool_is_degenerate_keeps_everyone_and_cutoff_is_none(self):
        survivors = [{"key": "a", "returns": {"status": "checked",
                                              "windows": {"1m": {"pct": -5.0}}}},
                    {"key": "b", "returns": {"status": "checked",
                                             "windows": {"1m": {"pct": -20.0}}}}]
        candidates, cutoff, degenerate = sd.select_worst_decile(survivors, "1m")
        self.assertTrue(degenerate)
        self.assertEqual(len(candidates), 2)
        self.assertIsNone(cutoff, "a degenerate small pool must never invent a cutoff "
                                 "from the best return in the pool")

    def test_positive_returns_are_never_selected_as_a_fall(self):
        """M5: with no sign filter, a strong month's least-positive names
        used to be filed as having 'fallen on flows'."""
        survivors = [{"key": str(i), "returns": {"status": "checked",
                                                 "windows": {"1m": {"pct": float(i) + 1}}}}
                    for i in range(20)]
        candidates, cutoff, degenerate = sd.select_worst_decile(survivors, "1m")
        self.assertEqual(candidates, [])
        self.assertIsNone(cutoff)

    def test_large_pool_with_no_ties_uses_an_exact_real_decile(self):
        survivors = [{"key": str(i), "returns": {"status": "checked",
                                                 "windows": {"1m": {"pct": -(i + 1.1)}}}}
                    for i in range(100)]
        candidates, cutoff, degenerate = sd.select_worst_decile(survivors, "1m")
        self.assertFalse(degenerate)
        self.assertEqual(len(candidates), 10)
        self.assertTrue(all(c["returns"]["windows"]["1m"]["pct"] <= cutoff
                            for c in candidates))

    def test_ties_at_the_cutoff_blow_the_decile_open_and_are_capped(self):
        """50 names tied at the worst return, among 100 total - the naive
        `pct <= cutoff` selection used to return all 50 as a "real" decile
        of 100, `degenerate=False`."""
        tied = [{"key": "tied-%d" % i, "returns": {"status": "checked",
                                                   "windows": {"1m": {"pct": -100.0}}}}
               for i in range(50)]
        tied += [{"key": "spread-%d" % i, "returns": {"status": "checked",
                                                      "windows": {"1m": {"pct": -(i + 1.0)}}}}
                for i in range(50)]
        candidates, cutoff, degenerate = sd.select_worst_decile(tied, "1m")
        self.assertTrue(degenerate)
        self.assertLessEqual(len(candidates), 10)

    def test_unchecked_returns_are_excluded_from_the_decile(self):
        survivors = [{"key": "a", "returns": {"status": "not checked", "reason": "x"}},
                    {"key": "b", "returns": {"status": "checked",
                                             "windows": {"1m": {"pct": -30.0}}}}]
        candidates, cutoff, degenerate = sd.select_worst_decile(survivors, "1m")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["key"], "b")


class ClassSuffixStripping(unittest.TestCase):
    """B3: 236 of 754 XSTO+SSME lines carry an exchange class suffix and MFN
    returns HTTP 500 for the suffixed form outright."""

    def test_strips_series_suffix(self):
        self.assertEqual(sd._strip_class_suffix("Atlas Copco AB ser. A"), "Atlas Copco AB")
        self.assertEqual(sd._strip_class_suffix("ASSA ABLOY AB ser. B"), "ASSA ABLOY AB")
        self.assertEqual(sd._strip_class_suffix("Investor AB ser. A"), "Investor AB")

    def test_strips_suffix_after_a_comma(self):
        self.assertEqual(sd._strip_class_suffix("Volvo, AB ser. B"), "Volvo, AB")

    def test_leaves_an_unsuffixed_name_alone(self):
        self.assertEqual(sd._strip_class_suffix("Evolution AB"), "Evolution AB")

    def test_none_is_handled(self):
        self.assertEqual(sd._strip_class_suffix(None), "")


class CorporateActionAmbiguityRefusal(unittest.TestCase):
    """B2: corporate_actions.resolve_company's free-text ranking has, on
    live data, put an unrelated company (Alvotech ahead of AstraZeneca PLC)
    ahead of the one actually being asked about - taking hits[0]
    unconditionally attached the wrong company's actions to the candidate."""

    def test_ambiguous_top_hit_refuses_rather_than_guesses(self):
        class FakeCA(object):
            BREAKS_PER_SHARE = {"SPLIT"}

            @staticmethod
            def _norm(s):
                return (s or "").strip().lower()

            @staticmethod
            def resolve_company(name):
                return [{"company": "Alvotech", "announcements_in_probe": 9},
                        {"company": "AstraZeneca PLC", "announcements_in_probe": 1}]

        real = sd.corporate_actions
        sd.corporate_actions = FakeCA
        try:
            result = sd.check_corporate_actions("AstraZeneca PLC", "2026-08-01", "2026-08-31",
                                                "2026-08-31")
        finally:
            sd.corporate_actions = real
        self.assertEqual(result["status"], "not checked")
        self.assertIn("Alvotech", result["reason"])

    def test_single_hit_proceeds_even_if_not_a_normalised_exact_match(self):
        class FakeCA(object):
            BREAKS_PER_SHARE = {"SPLIT"}

            @staticmethod
            def _norm(s):
                return (s or "").strip().lower()

            @staticmethod
            def resolve_company(name):
                return [{"company": "Evolution AB (publ)", "announcements_in_probe": 3}]

            @staticmethod
            def corporate_actions_between(company, date_from, date_to, pages=2):
                return []

            @staticmethod
            def split_adjustment_factor(company, date_from, date_to, pages=3):
                return {"confirmed_splits": [], "other_actions_in_window": []}

        real = sd.corporate_actions
        sd.corporate_actions = FakeCA
        try:
            result = sd.check_corporate_actions("Evolution", "2026-08-01", "2026-08-31", "2026-08-31")
        finally:
            sd.corporate_actions = real
        self.assertEqual(result["status"], "checked")

    def test_exact_top_match_among_several_still_proceeds(self):
        class FakeCA(object):
            BREAKS_PER_SHARE = {"SPLIT"}

            @staticmethod
            def _norm(s):
                return (s or "").strip().lower()

            @staticmethod
            def resolve_company(name):
                return [{"company": "Sandvik AB", "announcements_in_probe": 50},
                        {"company": "Sandvik Materials Technology", "announcements_in_probe": 1}]

            @staticmethod
            def corporate_actions_between(company, date_from, date_to, pages=2):
                return []

            @staticmethod
            def split_adjustment_factor(company, date_from, date_to, pages=3):
                return {"confirmed_splits": [], "other_actions_in_window": []}

        real = sd.corporate_actions
        sd.corporate_actions = FakeCA
        try:
            result = sd.check_corporate_actions("Sandvik AB", "2026-08-01", "2026-08-31", "2026-08-31")
        finally:
            sd.corporate_actions = real
        self.assertEqual(result["status"], "checked")


class SplitEffectiveDateFix(unittest.TestCase):
    """M6: a split announced before the window but EFFECTIVE inside it used
    to be invisible to corporate_actions_between's announcement-date-only
    filter."""

    def test_split_confirmed_by_effective_date_is_flagged_even_if_announced_earlier(self):
        class FakeCA(object):
            BREAKS_PER_SHARE = {"SPLIT"}

            @staticmethod
            def _norm(s):
                return (s or "").strip().lower()

            @staticmethod
            def resolve_company(name):
                return [{"company": "Effective Split AB", "announcements_in_probe": 1}]

            @staticmethod
            def corporate_actions_between(company, date_from, date_to, pages=2):
                return []          # announced OUTSIDE this window - nothing here

            @staticmethod
            def split_adjustment_factor(company, date_from, date_to, pages=3):
                return {"confirmed_splits": [{"date": "2026-08-15", "kind": "SPLIT",
                                             "terms": "10:1", "factor": 10.0}],
                       "other_actions_in_window": []}

        real = sd.corporate_actions
        sd.corporate_actions = FakeCA
        try:
            result = sd.check_corporate_actions("Effective Split AB",
                                                 "2026-08-01", "2026-08-31", "2026-08-31")
        finally:
            sd.corporate_actions = real
        self.assertTrue(result["has_breaking_action"])
        self.assertEqual(len(result["events"]), 1)

    def test_dividend_is_routed_to_technical_with_yield_stated(self):
        """M6: BREAKS_PER_SHARE excludes DIVIDEND on purpose (it is a
        classification set, not a price-comparability one) - this script
        must add DIVIDEND back for its OWN purpose, since nordic_shares'
        price series is unadjusted for ex-dividend drops too."""
        class FakeCA(object):
            BREAKS_PER_SHARE = {"SPLIT"}

            @staticmethod
            def _norm(s):
                return (s or "").strip().lower()

            @staticmethod
            def resolve_company(name):
                return [{"company": "Payer AB", "announcements_in_probe": 1}]

            @staticmethod
            def corporate_actions_between(company, date_from, date_to, pages=2):
                return [{"date": "2026-08-10", "type": "DIVIDEND",
                        "title": "Payer AB: dividend of SEK 5.00 per share"}]

            @staticmethod
            def split_adjustment_factor(company, date_from, date_to, pages=3):
                return {"confirmed_splits": [], "other_actions_in_window": []}

        real = sd.corporate_actions
        sd.corporate_actions = FakeCA
        try:
            result = sd.check_corporate_actions("Payer AB", "2026-08-01", "2026-08-31",
                                                "2026-08-31", price=100.0)
        finally:
            sd.corporate_actions = real
        self.assertTrue(result["has_breaking_action"])
        ev = result["events"][0]
        self.assertEqual(ev["type"], "DIVIDEND")
        self.assertAlmostEqual(ev["dividend_yield_pct"], 5.0)


class RegulatoryNewsIdentityVerification(unittest.TestCase):
    """B2: mfn_identity accepts only an entity whose OWN ISIN/LEI matches -
    never a bare search's top hit ("AstraZeneca PLC" -> Alvotech first on
    live data)."""

    def test_verified_identity_is_used_for_the_fetch(self):
        class FakeVenuesSE(object):
            @staticmethod
            def mfn_identity(name, isin=None, lei=None, limit=30):
                self_isin_matches = isin == "SE0000000101"
                if self_isin_matches:
                    return {"slug": "right-company", "isins": [isin], "leis": []}
                return None

        class FakeMFN(object):
            @staticmethod
            def fetch_company_pages(slug, pages=2):
                return [{"content": {"publish_date": "2026-08-20T08:00:00",
                                    "title": "Profit warning"},
                        "properties": {"tags": [":regulatory"], "lang": "en"},
                        "author": {"name": "Right Company", "slug": slug},
                        "url": "https://mfn.se/a/x"}]

            @staticmethod
            def flatten(item):
                content = item.get("content") or {}
                props = item.get("properties") or {}
                tags = props.get("tags") or []
                return {"date": (content.get("publish_date") or "")[:19],
                       "title": content.get("title"), "tags": tags,
                       "regulatory": ":regulatory" in tags}

        real_mfn, real_venues = sd.mfn_news, sd.venues_se
        sd.mfn_news, sd.venues_se = FakeMFN, FakeVenuesSE
        try:
            result = sd.check_regulatory_news("Right Company", "2026-08-01", "2026-08-31",
                                              "2026-08-31", isin="SE0000000101", lei=None)
        finally:
            sd.mfn_news, sd.venues_se = real_mfn, real_venues
        self.assertEqual(result["status"], "checked")
        self.assertTrue(result["has_release"])

    def test_no_verified_identity_is_checked_false_not_unchecked(self):
        class FakeVenuesSE(object):
            @staticmethod
            def mfn_identity(name, isin=None, lei=None, limit=30):
                return None    # no MFN entity's own ISIN/LEI matched

        real_venues = sd.venues_se
        sd.venues_se = FakeVenuesSE
        try:
            result = sd.check_regulatory_news("Wrongly Searched AB",
                                              "2026-08-01", "2026-08-31", "2026-08-31",
                                              isin="SE0000000999", lei=None)
        finally:
            sd.venues_se = real_venues
        self.assertEqual(result["status"], "checked")
        self.assertFalse(result["has_release"])
        self.assertIn("no MFN entity", result["note"])


class RegulatoryNewsSplitAgainstLastClose(unittest.TestCase):
    """THE FIX: this screen runs pre-open, and Swedish issuers publish in a
    heavy wave 06:30-08:30 - before the return being classified was even
    measured (it is measured to the LAST COMPLETED close, i.e. yesterday).
    A release published this morning must never be read as explaining a
    fall that predates it - that is causally backwards. The window is
    split against `last_close_date` (the same date the candidate's own
    return is measured to): [date_from, last_close_date] can explain the
    fall; (last_close_date, date_to] is new, unpriced information, reported
    separately under `since_last_close`, never folded into `has_release`.
    """

    LAST_CLOSE = "2026-08-28"          # the fall was measured to this close
    TODAY = "2026-08-29"               # the script runs the morning after

    @staticmethod
    def _fakes(items):
        class FakeVenuesSE(object):
            @staticmethod
            def mfn_identity(name, isin=None, lei=None, limit=30):
                return {"slug": "target-ab", "isins": [], "leis": []}

        class FakeMFN(object):
            calls = {"identity": 0, "fetch": 0}

            @staticmethod
            def fetch_company_pages(slug, pages=2):
                FakeMFN.calls["fetch"] += 1
                return items

            @staticmethod
            def flatten(item):
                content = item.get("content") or {}
                props = item.get("properties") or {}
                tags = props.get("tags") or []
                return {"date": (content.get("publish_date") or "")[:19],
                       "title": content.get("title"), "tags": tags,
                       "regulatory": ":regulatory" in tags}

        return FakeMFN, FakeVenuesSE

    @staticmethod
    def _mfn_item(publish_date, title, regulatory=True):
        return {"content": {"publish_date": publish_date, "title": title},
                "properties": {"tags": [":regulatory"] if regulatory else [], "lang": "en"},
                "author": {"name": "Target AB", "slug": "target-ab"},
                "url": "https://mfn.se/a/x"}

    def _check(self, items):
        FakeMFN, FakeVenuesSE = self._fakes(items)
        real_mfn, real_venues = sd.mfn_news, sd.venues_se
        sd.mfn_news, sd.venues_se = FakeMFN, FakeVenuesSE
        try:
            result = sd.check_regulatory_news("Target AB", "2026-08-01", self.LAST_CLOSE,
                                              self.TODAY)
        finally:
            sd.mfn_news, sd.venues_se = real_mfn, real_venues
        return result, FakeMFN

    def test_release_published_after_last_close_does_not_explain_the_fall(self):
        """This is the defect itself: a 07:15 release the morning AFTER the
        last completed close must NOT put the candidate in
        fell_on_information - the fall predates the release, so the release
        cannot be its cause."""
        result, _ = self._check([self._mfn_item("2026-08-29T07:15:00", "Profit warning")])
        self.assertFalse(result["has_release"],
                         "a release strictly after last_close_date must never count "
                         "as explaining a fall already measured to that close")
        self.assertEqual(result["items"], [])

    def test_release_after_last_close_is_marked_since_last_close(self):
        result, _ = self._check([self._mfn_item("2026-08-29T07:15:00", "Profit warning")])
        since = result["since_last_close"]
        self.assertEqual(since["status"], "checked")
        self.assertEqual(since["count"], 1)
        self.assertEqual(since["items"][0]["title"], "Profit warning")
        self.assertEqual(since["items"][0]["date"], "2026-08-29T07:15:00")

    def test_release_on_or_before_last_close_still_classifies_as_before(self):
        """Regression guard: this fix must not change anything about a
        release that already fell inside the explains-the-fall window."""
        result, _ = self._check([self._mfn_item("2026-08-20T08:00:00", "Profit warning")])
        self.assertTrue(result["has_release"])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["date"], "2026-08-20")
        self.assertEqual(result["since_last_close"]["count"], 0)

        # A release ON the last close date itself is still "on or before".
        same_day = self._check([self._mfn_item("2026-08-28T16:00:00", "Report")])[0]
        self.assertTrue(same_day["has_release"])
        self.assertEqual(same_day["since_last_close"]["count"], 0)

    def test_candidate_with_both_an_explaining_and_a_fresh_release(self):
        """An older release that explains the fall AND a fresh one this
        morning must both be recognised: has_release True (from the old
        one) AND since_last_close flagging the new one."""
        result, _ = self._check([
            self._mfn_item("2026-08-20T08:00:00", "Profit warning"),
            self._mfn_item("2026-08-29T07:15:00", "Trading update"),
        ])
        self.assertTrue(result["has_release"])
        self.assertEqual([i["title"] for i in result["items"]], ["Profit warning"])
        self.assertEqual(result["since_last_close"]["count"], 1)
        self.assertEqual(result["since_last_close"]["items"][0]["title"], "Trading update")

    def test_untagged_release_after_last_close_does_not_count_as_regulatory(self):
        result, _ = self._check([self._mfn_item("2026-08-29T07:15:00", "Marketing puff",
                                                 regulatory=False)])
        self.assertEqual(result["since_last_close"]["count"], 0)

    def test_identity_resolved_once_and_only_one_fetch_is_made(self):
        """Both windows must come out of ONE identity resolution and ONE
        fetch - never a second round-trip to serve since_last_close."""
        calls = {"identity": 0}

        class CountingVenuesSE(object):
            @staticmethod
            def mfn_identity(name, isin=None, lei=None, limit=30):
                calls["identity"] += 1
                return {"slug": "target-ab", "isins": [], "leis": []}

        FakeMFN, _ = self._fakes([
            self._mfn_item("2026-08-20T08:00:00", "Profit warning"),
            self._mfn_item("2026-08-29T07:15:00", "Trading update"),
        ])
        real_mfn, real_venues = sd.mfn_news, sd.venues_se
        sd.mfn_news, sd.venues_se = FakeMFN, CountingVenuesSE
        try:
            sd.check_regulatory_news("Target AB", "2026-08-01", self.LAST_CLOSE, self.TODAY)
        finally:
            sd.mfn_news, sd.venues_se = real_mfn, real_venues
        self.assertEqual(calls["identity"], 1, "identity must be resolved exactly once")
        self.assertEqual(FakeMFN.calls["fetch"], 1, "only one fetch must be made per candidate")

    def test_failed_fetch_reports_not_checked_for_both_windows_never_no_news(self):
        class FakeVenuesSE(object):
            @staticmethod
            def mfn_identity(name, isin=None, lei=None, limit=30):
                return {"slug": "target-ab", "isins": [], "leis": []}

        class FailingMFN(object):
            @staticmethod
            def fetch_company_pages(slug, pages=2):
                raise RuntimeError("simulated MFN outage")

        real_mfn, real_venues = sd.mfn_news, sd.venues_se
        sd.mfn_news, sd.venues_se = FailingMFN, FakeVenuesSE
        try:
            result = sd.check_regulatory_news("Target AB", "2026-08-01", self.LAST_CLOSE,
                                              self.TODAY)
        finally:
            sd.mfn_news, sd.venues_se = real_mfn, real_venues
        self.assertEqual(result["status"], "not checked")
        self.assertEqual(result["since_last_close"]["status"], "not checked",
                         "a failed check must report 'not checked' for BOTH windows - "
                         "never present as if 'no news' had been confirmed")
        self.assertNotIn("has_release", result,
                         "an incomplete check must never assert a release verdict "
                         "either way")


class CorporateActionSplitAgainstLastClose(unittest.TestCase):
    """Same reasoning as RegulatoryNewsSplitAgainstLastClose applied to
    corporate actions: an ex-dividend date or a split EFFECTIVE this
    morning is likewise not an explanation of a fall measured to
    yesterday's close, and is likewise urgent, unpriced information."""

    LAST_CLOSE = "2026-08-28"
    TODAY = "2026-08-29"

    class FakeCA(object):
        BREAKS_PER_SHARE = {"SPLIT"}
        ROWS = []

        @staticmethod
        def _norm(s):
            return (s or "").strip().lower()

        @staticmethod
        def resolve_company(name):
            return [{"company": name, "announcements_in_probe": 1}]

        @staticmethod
        def corporate_actions_between(company, date_from, date_to, pages=2):
            return list(CorporateActionSplitAgainstLastClose.FakeCA.ROWS)

        @staticmethod
        def split_adjustment_factor(company, date_from, date_to, pages=3):
            return {"confirmed_splits": [], "other_actions_in_window": []}

    def _check(self, rows):
        self.FakeCA.ROWS = rows
        real = sd.corporate_actions
        sd.corporate_actions = self.FakeCA
        try:
            return sd.check_corporate_actions("Target AB", "2026-08-01", self.LAST_CLOSE,
                                              self.TODAY)
        finally:
            sd.corporate_actions = real

    def test_action_effective_after_last_close_does_not_explain_the_fall(self):
        result = self._check([{"date": "2026-08-29", "type": "SPLIT", "title": "10:1 split"}])
        self.assertFalse(result["has_breaking_action"],
                         "an action effective AFTER the last close must not be read as "
                         "explaining a fall already measured to that close")
        self.assertEqual(result["events"], [])

    def test_action_after_last_close_is_marked_since_last_close(self):
        result = self._check([{"date": "2026-08-29", "type": "SPLIT", "title": "10:1 split"}])
        since = result["since_last_close"]
        self.assertEqual(since["status"], "checked")
        self.assertEqual(since["count"], 1)
        self.assertEqual(since["events"][0]["type"], "SPLIT")

    def test_action_on_or_before_last_close_still_classifies_as_before(self):
        result = self._check([{"date": "2026-08-15", "type": "SPLIT", "title": "10:1 split"}])
        self.assertTrue(result["has_breaking_action"])
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["since_last_close"]["count"], 0)


# ---------------------------------------------------------------------------
# full pipeline, end to end, every fetcher monkeypatched
# ---------------------------------------------------------------------------

def _firds_row(isin, name, lei, mic="XSTO"):
    return {"isin": isin, "mic": mic, "name": name, "firds_short_name": name[:12],
           "cfi": "ESVUFR", "lei": lei, "trading_since": "2000-01-01"}


class FullPipelineRoutesCandidatesCorrectly(unittest.TestCase):
    """One run() covering LEI collapse, the (post-history) liquidity floor,
    an endpoint failure, a technical (split) move, both regulatory-news
    buckets, AND the new NOT CLASSIFIED bucket - so the routing decisions
    are exercised together, the way a real run would actually produce them.
    """

    ISINS = {
        "inv_a": "SE0000000101", "inv_b": "SE0000000102",
        "split": "SE0000000201", "newsy": "SE0000000301",
        "quiet": "SE0000000401", "faily": "SE0000000501",
        "illiquid": "SE0000000601", "unresolvable": "SE0000000701",
    }

    def setUp(self):
        firds = {"XSTO": {"instruments": [
            _firds_row(self.ISINS["inv_a"], "Investor A", "LEI-INV"),
            _firds_row(self.ISINS["inv_b"], "Investor B", "LEI-INV"),
            _firds_row(self.ISINS["split"], "Splitty AB", "LEI-SPLIT"),
            _firds_row(self.ISINS["newsy"], "Newsy AB", "LEI-NEWS"),
            _firds_row(self.ISINS["quiet"], "Quiet AB", "LEI-QUIET"),
            _firds_row(self.ISINS["faily"], "Faily AB", "LEI-FAIL"),
            _firds_row(self.ISINS["illiquid"], "Illiquid AB", "LEI-ILLIQ"),
            _firds_row(self.ISINS["unresolvable"], "Unresolvable AB", "LEI-UNRES"),
        ], "structured_excluded": 0}, "SSME": {"instruments": [], "structured_excluded": 0}}

        obid_by_isin = {
            self.ISINS["inv_a"]: "OB-INV-A", self.ISINS["inv_b"]: "OB-INV-B",
            self.ISINS["split"]: "OB-SPLIT", self.ISINS["newsy"]: "OB-NEWSY",
            self.ISINS["quiet"]: "OB-QUIET", self.ISINS["faily"]: "OB-FAILY",
            self.ISINS["illiquid"]: "OB-ILLIQ", self.ISINS["unresolvable"]: "OB-UNRES",
        }
        nordic_rows = [
            {"orderbookId": obid, "symbol": name.upper()[:8], "name": name, "isin": isin,
            "currency": "SEK", "segment": "LARGE_CAP", "sector": "Test", "last": price}
            for isin, obid, name, price in [
                (self.ISINS["inv_a"], "OB-INV-A", "Investor A", 150.0),
                (self.ISINS["inv_b"], "OB-INV-B", "Investor B", 152.0),
                (self.ISINS["split"], "OB-SPLIT", "Splitty AB", 10.0),
                (self.ISINS["newsy"], "OB-NEWSY", "Newsy AB", 40.0),
                (self.ISINS["quiet"], "OB-QUIET", "Quiet AB", 60.0),
                (self.ISINS["faily"], "OB-FAILY", "Faily AB", 20.0),
                (self.ISINS["illiquid"], "OB-ILLIQ", "Illiquid AB", 5.0),
                (self.ISINS["unresolvable"], "OB-UNRES", "Unresolvable AB", 30.0),
            ]
        ]

        # LAST-SESSION close/volume per obid - this is what apply_liquidity_
        # floor now cuts on, NOT an intraday screener field. Illiquid AB's
        # last-session turnover (5.0 * 20 = 100) is genuinely tiny.
        returns_by_obid = {
            "OB-INV-A": {"status": "checked", "as_of": "2026-08-28",
                        "last_close": 150.0, "last_volume": 40000.0,
                        "windows": {"1w": {"pct": -1.0}, "1m": {"pct": -2.0},
                                   "3m": {"pct": -3.0}},
                        "percentile_in_fetched_range": 40.0, "bars_fetched": 90},
            "OB-INV-B": {"status": "checked", "as_of": "2026-08-28",
                        "last_close": 152.0, "last_volume": 300000.0,
                        "windows": {"1w": {"pct": -1.0}, "1m": {"pct": -2.0},
                                   "3m": {"pct": -3.0}},
                        "percentile_in_fetched_range": 40.0, "bars_fetched": 90},
            "OB-SPLIT": {"status": "checked", "as_of": "2026-08-28",
                        "last_close": 10.0, "last_volume": 200000.0,
                        "windows": {"1w": {"pct": -40.0}, "1m": {"pct": -45.0},
                                   "3m": {"pct": -45.0}},
                        "percentile_in_fetched_range": 5.0, "bars_fetched": 90},
            "OB-NEWSY": {"status": "checked", "as_of": "2026-08-28",
                        "last_close": 40.0, "last_volume": 50000.0,
                        "windows": {"1w": {"pct": -15.0}, "1m": {"pct": -30.0},
                                   "3m": {"pct": -32.0}},
                        "percentile_in_fetched_range": 8.0, "bars_fetched": 90},
            "OB-QUIET": {"status": "checked", "as_of": "2026-08-28",
                        "last_close": 60.0, "last_volume": 50000.0,
                        "windows": {"1w": {"pct": -10.0}, "1m": {"pct": -25.0},
                                   "3m": {"pct": -27.0}},
                        "percentile_in_fetched_range": 9.0, "bars_fetched": 90},
            "OB-UNRES": {"status": "checked", "as_of": "2026-08-28",
                        "last_close": 30.0, "last_volume": 50000.0,
                        "windows": {"1w": {"pct": -12.0}, "1m": {"pct": -22.0},
                                   "3m": {"pct": -24.0}},
                        "percentile_in_fetched_range": 12.0, "bars_fetched": 90},
            "OB-ILLIQ": {"status": "checked", "as_of": "2026-08-28",
                        "last_close": 5.0, "last_volume": 20.0,
                        "windows": {"1w": {"pct": -50.0}, "1m": {"pct": -50.0},
                                   "3m": {"pct": -50.0}},
                        "percentile_in_fetched_range": 1.0, "bars_fetched": 90},
            # OB-FAILY deliberately absent - price_history failed for it.
        }

        def fake_snapshot(market="STO"):
            return list(nordic_rows), {}, None

        def fake_firds(mics):
            return {m: firds[m] for m in mics if m in firds}, {}

        def fake_fetch_returns_parallel(instruments, as_of_date, budget, max_workers=10):
            out = {}
            for row in instruments:
                obid = row.get("orderbookId")
                out[obid] = returns_by_obid.get(
                    obid, {"status": "not checked", "reason": "no fixture"})
            return out

        def fake_check_corporate_action(name, date_from, last_close_date, date_to, price=None):
            if name == "Splitty AB":
                return {"status": "checked", "has_breaking_action": True,
                        "events": [{"date": "2026-08-15", "type": "SPLIT",
                                   "title": "Splitty AB: 10:1 split"}],
                        "since_last_close": {"status": "checked", "count": 0, "events": [],
                                             "window": [last_close_date, date_to]}}
            return {"status": "checked", "has_breaking_action": False, "events": [],
                    "since_last_close": {"status": "checked", "count": 0, "events": [],
                                        "window": [last_close_date, date_to]}}

        def fake_check_regulatory_news(name, date_from, last_close_date, date_to,
                                       isin=None, lei=None):
            if name == "Newsy AB":
                # Deliberately carries BOTH: an older release that explains
                # the fall (well before last_close_date) AND a fresh one
                # published the morning after last close, not yet priced -
                # exercises the "candidate with both" case end to end.
                return {"status": "checked", "has_release": True,
                        "window": [date_from, last_close_date],
                        "items": [{"date": "2026-08-20", "title": "Profit warning"}],
                        "since_last_close": {
                            "status": "checked", "count": 1,
                            "window": [last_close_date, date_to],
                            "items": [{"date": "2026-08-30T07:15:00",
                                      "title": "Trading update"}]}}
            if name == "Unresolvable AB":
                reason = "MFN identity resolution failed: simulated outage"
                return {"status": "not checked", "reason": reason,
                        "since_last_close": {"status": "not checked", "reason": reason}}
            return {"status": "checked", "has_release": False,
                    "window": [date_from, last_close_date], "items": [],
                    "since_last_close": {"status": "checked", "count": 0, "items": [],
                                        "window": [last_close_date, date_to]}}

        def fake_load_short_data():
            company = {"lei": "LEI-NEWS", "display": "Newsy AB", "names": ["newsy ab"],
                      "isins": [self.ISINS["newsy"]],
                      "agg": [{"issuer": "Newsy AB", "lei": "LEI-NEWS", "pct": 6.5,
                              "pct_shown": "6,50", "date": "2026-08-25"}],
                      "row_count": 0}
            return {"companies_by_lei": {"LEI-NEWS": company}, "companies_by_isin": {},
                    "rows": []}, None

        self.patchers = [
            mock.patch.object(sd, "fetch_nasdaq_snapshot", fake_snapshot),
            mock.patch.object(sd, "fetch_firds", fake_firds),
            mock.patch.object(sd, "fetch_returns_parallel", fake_fetch_returns_parallel),
            mock.patch.object(sd, "check_corporate_actions", fake_check_corporate_action),
            mock.patch.object(sd, "check_regulatory_news", fake_check_regulatory_news),
            mock.patch.object(sd, "load_short_data", fake_load_short_data),
            mock.patch.object(sd, "today", lambda: datetime.date(2026, 8, 31)),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

        args = mock.Mock()
        args.window = "1m"
        args.venue = "xsto,ssme"
        args.limit = 20
        args.liquidity_floor = 1_000_000.0
        args.include_illiquid = False
        args.budget = 60.0
        self.result = sd.run(args)

    def _names_in(self, bucket):
        return {c["name"] for c in self.result[bucket]}

    def test_lei_grouping_collapses_investor_a_and_b_into_one_issuer(self):
        self.assertEqual(self.result["universe"]["issuers"], 7,
                         "Investor A+B must collapse to one issuer: 8 ISIN "
                         "lines, 7 issuers")

    def test_illiquid_name_is_cut_and_the_count_is_reported(self):
        below_floor = next(c for c in self.result["cuts"]
                           if c["stage"] == "liquidity_floor: below floor")
        self.assertEqual(below_floor["count"], 1)
        all_names = (self._names_in("technical_moves")
                    | self._names_in("fell_on_information")
                    | self._names_in("fell_on_flows")
                    | self._names_in("not_classified"))
        self.assertNotIn("Illiquid AB", all_names)

    def test_split_inside_window_routes_to_technical_not_candidates(self):
        self.assertIn("Splitty AB", self._names_in("technical_moves"))
        self.assertNotIn("Splitty AB", self._names_in("fell_on_information"))
        self.assertNotIn("Splitty AB", self._names_in("fell_on_flows"))

    def test_regulatory_release_is_bucketed_apart_from_no_release(self):
        self.assertIn("Newsy AB", self._names_in("fell_on_information"))
        self.assertIn("Quiet AB", self._names_in("fell_on_flows"))
        self.assertNotIn("Newsy AB", self._names_in("fell_on_flows"))
        self.assertNotIn("Quiet AB", self._names_in("fell_on_information"))

    def test_unresolvable_regulatory_check_is_not_classified_never_fell_on_flows(self):
        """B3: a name whose regulatory-news check could not be COMPLETED
        must never be filed under "fell on flows" by elimination - that
        header claims "no regulatory release found", which is not what
        happened."""
        self.assertIn("Unresolvable AB", self._names_in("not_classified"))
        self.assertNotIn("Unresolvable AB", self._names_in("fell_on_flows"))
        self.assertNotIn("Unresolvable AB", self._names_in("fell_on_information"))
        not_classified_total = next(c for c in self.result["cuts"]
                                    if c["stage"] == "regulatory news: not classified")
        self.assertEqual(not_classified_total["count"], 1)

    def test_price_history_failure_degrades_to_unchecked_and_is_counted(self):
        unchecked = next(c for c in self.result["cuts"]
                         if c["stage"] == "returns: not checked")
        self.assertGreaterEqual(unchecked["count"], 1)
        all_names = (self._names_in("technical_moves")
                    | self._names_in("fell_on_information")
                    | self._names_in("fell_on_flows")
                    | self._names_in("not_classified"))
        self.assertNotIn("Faily AB", all_names,
                         "a name whose return could not be checked must never "
                         "appear as a candidate")

    def test_per_axis_as_of_dates_are_independent(self):
        newsy = next(c for c in self.result["fell_on_information"]
                    if c["name"] == "Newsy AB")
        price_as_of = newsy["returns"]["as_of"]
        short_as_of = newsy["short_interest"]["as_of"]
        window = newsy["regulatory_news"]["window"]
        self.assertEqual(price_as_of, "2026-08-28")
        self.assertEqual(short_as_of, "2026-08-25")
        self.assertNotEqual(price_as_of, short_as_of,
                            "price and short-interest as-of dates must not be "
                            "collapsed into one headline date")
        # The "explains the fall" window is bounded by the candidate's OWN
        # last completed close (its returns "as_of", 2026-08-28) - NOT by
        # today (result["window_to"], 2026-08-31). Collapsing it to
        # window_to is exactly the defect this fix corrects: it would let a
        # release published this morning (after the last close, before
        # today) count as having explained a fall already measured to
        # yesterday's close, which is causally impossible.
        self.assertEqual(window, [self.result["window_from"], price_as_of])
        self.assertNotEqual(window[1], self.result["window_to"],
                            "the explains-the-fall window must never be collapsed "
                            "onto today's date")

    def test_fresh_news_since_last_close_is_marked_without_moving_the_bucket(self):
        """The central fix: Newsy AB carries an older release (2026-08-20,
        well before its 2026-08-28 last close) that explains the fall, AND a
        fresh one (2026-08-30, after that close) that does not. The
        candidate must stay in fell_on_information (unchanged bucket -
        regression guard) while ALSO being flagged, in the summary count,
        the per-candidate text marker, and the JSON payload, as carrying
        news since the last close."""
        self.assertIn("Newsy AB", self._names_in("fell_on_information"))
        self.assertIn("Newsy AB", self.result["since_last_close_news_names"])
        self.assertGreaterEqual(self.result["since_last_close_news_total"], 1)

        newsy = next(c for c in self.result["fell_on_information"]
                    if c["name"] == "Newsy AB")
        since = newsy["regulatory_news"]["since_last_close"]
        self.assertEqual(since["status"], "checked")
        self.assertEqual(since["count"], 1)
        self.assertEqual(since["items"][0]["title"], "Trading update")

        text = sd._line_for(newsy, self.result["window"])
        self.assertIn("NEWS SINCE LAST CLOSE", text)
        self.assertIn("Trading update", text)

        safe = sd._json_safe(self.result)
        newsy_json = next(c for c in safe["fell_on_information"] if c["name"] == "Newsy AB")
        self.assertEqual(newsy_json["regulatory_news"]["since_last_close"]["count"], 1)

    def test_summary_line_names_candidates_with_fresh_news(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            sd.print_text(self.result)
        out = buf.getvalue()
        self.assertIn("Newsy AB", out)
        self.assertRegex(out, r"\d+ candidate\(s\) have regulatory news published SINCE "
                              r"their last completed close")

    def test_data_confidence_flags_regulated_market(self):
        newsy = next(c for c in self.result["fell_on_information"]
                    if c["name"] == "Newsy AB")
        self.assertTrue(newsy["data_confidence"]["regulated_market"])
        self.assertTrue(newsy["data_confidence"]["esef_applies"])

    def test_run_is_not_marked_partial_when_the_budget_is_ample(self):
        self.assertFalse(self.result["partial"])

    def test_arithmetic_reconciles_without_include_illiquid(self):
        u = self.result["universe"]["issuers"]
        liq = {c["stage"]: c["count"] for c in self.result["cuts"]
              if c["stage"].startswith("liquidity_floor")}
        survivors = next(c["count"] for c in self.result["cuts"] if c["stage"] == "survivors")
        self.assertEqual(u, sum(liq.values()) + survivors)


class FullPipelineReconcilesWithIncludeIlliquid(unittest.TestCase):
    """M4: --include-illiquid must not stop the arithmetic reconciling -
    with it on, EVERY issuer is a survivor (nothing is actually removed),
    while the liquidity cut-reason counts still say how many WOULD have
    been cut."""

    def setUp(self):
        firds = {"XSTO": {"instruments": [
            _firds_row("SE0000000801", "Liquid AB", "LEI-LIQ"),
            _firds_row("SE0000000802", "Illiquid AB", "LEI-ILLIQ2"),
        ]}, "SSME": {"instruments": [], "structured_excluded": 0}}
        nordic_rows = [
            {"orderbookId": "OB-LIQ", "symbol": "LIQUID", "name": "Liquid AB",
            "isin": "SE0000000801", "currency": "SEK", "segment": "LARGE_CAP",
            "sector": "Test", "last": 100.0},
            {"orderbookId": "OB-ILLIQ2", "symbol": "ILLIQ", "name": "Illiquid AB",
            "isin": "SE0000000802", "currency": "SEK", "segment": "LARGE_CAP",
            "sector": "Test", "last": 5.0},
        ]
        returns_by_obid = {
            "OB-LIQ": {"status": "checked", "as_of": "2026-08-28",
                      "last_close": 100.0, "last_volume": 100000.0,
                      "windows": {"1w": {"pct": -1.0}, "1m": {"pct": -2.0},
                                 "3m": {"pct": -3.0}},
                      "percentile_in_fetched_range": 40.0, "bars_fetched": 90},
            "OB-ILLIQ2": {"status": "checked", "as_of": "2026-08-28",
                         "last_close": 5.0, "last_volume": 5.0,
                         "windows": {"1w": {"pct": -1.0}, "1m": {"pct": -2.0},
                                    "3m": {"pct": -3.0}},
                         "percentile_in_fetched_range": 5.0, "bars_fetched": 90},
        }

        def fake_snapshot(market="STO"):
            return list(nordic_rows), {}, None

        def fake_firds(mics):
            return {m: firds[m] for m in mics if m in firds}, {}

        def fake_fetch_returns_parallel(instruments, as_of_date, budget, max_workers=10):
            return {r["orderbookId"]: returns_by_obid.get(r["orderbookId"]) for r in instruments}

        def fake_check_corporate_action(name, date_from, last_close_date, date_to, price=None):
            return {"status": "checked", "has_breaking_action": False, "events": [],
                    "since_last_close": {"status": "checked", "count": 0, "events": [],
                                        "window": [last_close_date, date_to]}}

        def fake_check_regulatory_news(name, date_from, last_close_date, date_to,
                                       isin=None, lei=None):
            return {"status": "checked", "has_release": False,
                    "window": [date_from, last_close_date], "items": [],
                    "since_last_close": {"status": "checked", "count": 0, "items": [],
                                        "window": [last_close_date, date_to]}}

        def fake_load_short_data():
            return {"companies_by_lei": {}, "companies_by_isin": {}, "rows": []}, None

        self.patchers = [
            mock.patch.object(sd, "fetch_nasdaq_snapshot", fake_snapshot),
            mock.patch.object(sd, "fetch_firds", fake_firds),
            mock.patch.object(sd, "fetch_returns_parallel", fake_fetch_returns_parallel),
            mock.patch.object(sd, "check_corporate_actions", fake_check_corporate_action),
            mock.patch.object(sd, "check_regulatory_news", fake_check_regulatory_news),
            mock.patch.object(sd, "load_short_data", fake_load_short_data),
            mock.patch.object(sd, "today", lambda: datetime.date(2026, 8, 31)),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def _run(self, include_illiquid):
        args = mock.Mock()
        args.window, args.venue, args.limit = "1m", "xsto,ssme", 20
        args.liquidity_floor, args.budget = 1_000_000.0, 60.0
        args.include_illiquid = include_illiquid
        return sd.run(args)

    def test_reconciles_without_include_illiquid(self):
        result = self._run(False)
        u = result["universe"]["issuers"]
        liq = sum(c["count"] for c in result["cuts"] if c["stage"].startswith("liquidity_floor"))
        survivors = next(c["count"] for c in result["cuts"] if c["stage"] == "survivors")
        self.assertEqual(u, 2)
        self.assertEqual(u, liq + survivors)
        self.assertEqual(survivors, 1)

    def test_reconciles_with_include_illiquid(self):
        result = self._run(True)
        u = result["universe"]["issuers"]
        survivors = next(c["count"] for c in result["cuts"] if c["stage"] == "survivors")
        below_floor = next(c["count"] for c in result["cuts"]
                           if c["stage"] == "liquidity_floor: below floor")
        # With --include-illiquid nothing is actually removed - every issuer
        # is a survivor - but the cut-reason count is UNCHANGED from the
        # non-illiquid run (unconditional counting, see M4).
        self.assertEqual(survivors, u)
        self.assertEqual(below_floor, 1)


if __name__ == "__main__":
    unittest.main()
