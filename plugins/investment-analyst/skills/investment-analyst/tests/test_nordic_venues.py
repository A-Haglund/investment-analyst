#!/usr/bin/env python3
"""Opening the screens to Denmark and Finland.

Every test here is offline: fetch_nasdaq_snapshot is replaced with a fake, so
nothing reaches Nasdaq. What is under test is the mapping and the merge, not
the endpoint.
"""
import unittest

import helpers

helpers.bootstrap_path()
market_universe = helpers.load("market_universe")


class VenueTables(unittest.TestCase):
    def test_growth_markets_are_not_regulated(self):
        # DSME and FSME are First North, i.e. MTFs. ESEF applies to regulated
        # markets only, so calling them regulated would make the screen look
        # for an annual report that is not filed anywhere.
        for mic in ("DSME", "FSME", "SSME"):
            self.assertNotIn(mic, market_universe.REGULATED_MICS, mic)
        for mic in ("XSTO", "XCSE", "XHEL", "XNGM"):
            self.assertIn(mic, market_universe.REGULATED_MICS, mic)

    def test_every_nasdaq_mic_maps_to_a_market_and_a_country(self):
        for mic in market_universe.NASDAQ_MICS:
            self.assertIn(mic, market_universe.NASDAQ_MARKET_FOR_MIC, mic)
            self.assertIn(mic, market_universe.COUNTRY_FOR_MIC, mic)

    def test_every_mic_has_a_venue_label(self):
        for mic in market_universe.ALL_MICS:
            self.assertIn(mic, market_universe.VENUE_LABEL, mic)

    def test_default_is_sweden_not_the_whole_nordics(self):
        # Opening the toolkit to DK/FI must not silently triple what an
        # unqualified run costs and reports.
        self.assertEqual(set(market_universe.DEFAULT_MICS),
                         {"XSTO", "SSME", "XSAT", "XNGM", "NSME"})
        for mic in market_universe.DEFAULT_MICS:
            self.assertIn(mic, market_universe.ALL_MICS, mic)


class SegmentToMic(unittest.TestCase):
    def test_same_segment_name_means_a_different_mic_per_market(self):
        f = market_universe._mic_for_nasdaq_segment
        self.assertEqual(f("FIRST_NORTH", "STO"), "SSME")
        self.assertEqual(f("FIRST_NORTH", "CPH"), "DSME")
        self.assertEqual(f("FIRST_NORTH", "HEL"), "FSME")
        self.assertEqual(f("SMALL_CAP", "STO"), "XSTO")
        self.assertEqual(f("SMALL_CAP", "CPH"), "XCSE")
        self.assertEqual(f("LARGE_CAP", "HEL"), "XHEL")

    def test_market_defaults_to_stockholm(self):
        f = market_universe._mic_for_nasdaq_segment
        self.assertEqual(f("SMALL_CAP"), "XSTO")
        self.assertEqual(f("FIRST_NORTH"), "SSME")


def _fake_snapshot(per_market, failing=()):
    """A fetch_nasdaq_snapshot replacement over a {market: [rows]} dict."""
    def fetch(market="STO"):
        if market in failing:
            return None, None, "HTTP 503"
        rows = []
        liq = {}
        for i, (segment, isin) in enumerate(per_market.get(market, [])):
            obid = "%s-%d" % (market, i)
            rows.append({"orderbookId": obid, "symbol": isin[:4],
                         "name": isin, "isin": isin, "currency": "SEK",
                         "segment": segment, "market": market,
                         "sector": None, "last": 10.0})
            liq[obid] = {"turnover": 1.0, "volume": 1.0, "percent_change_1d": 0.0}
        return rows, liq, None
    return fetch


class Snapshots(unittest.TestCase):
    def setUp(self):
        self.mu = helpers.load("market_universe")

    def test_only_the_markets_the_mics_touch_are_fetched(self):
        asked = []

        def spy(market="STO"):
            asked.append(market)
            return [], {}, None

        self.mu.fetch_nasdaq_snapshot = spy
        self.mu.fetch_nasdaq_snapshots(["XCSE", "DSME", "XHEL"])
        self.assertEqual(asked, ["CPH", "HEL"])

    def test_non_nasdaq_mics_fetch_nothing(self):
        asked = []

        def spy(market="STO"):
            asked.append(market)
            return [], {}, None

        self.mu.fetch_nasdaq_snapshot = spy
        rows, liq, errors = self.mu.fetch_nasdaq_snapshots(["XSAT", "XNGM", "NSME"])
        self.assertEqual(asked, [])
        self.assertEqual((rows, liq, errors), ([], {}, {}))

    def test_markets_are_merged_and_each_row_keeps_its_own_mic(self):
        self.mu.fetch_nasdaq_snapshot = _fake_snapshot({
            "STO": [("SMALL_CAP", "SE0000000001"), ("FIRST_NORTH", "SE0000000002")],
            "CPH": [("SMALL_CAP", "DK0000000001")],
            "HEL": [("FIRST_NORTH", "FI0000000001")],
        })
        mics = ["XSTO", "SSME", "XCSE", "FSME"]
        rows, liq, errors = self.mu.fetch_nasdaq_snapshots(mics)
        self.assertEqual(errors, {})
        self.assertEqual(len(rows), 4)
        self.assertEqual(len(liq), 4)
        combined = self.mu.combine_universe(rows, liq, {}, mics)
        got = {r["isin"]: r["mic"] for r in combined}
        self.assertEqual(got, {"SE0000000001": "XSTO", "SE0000000002": "SSME",
                               "DK0000000001": "XCSE", "FI0000000001": "FSME"})

    def test_a_failing_market_is_reported_not_swallowed(self):
        # A Stockholm-only universe must never be presented as a Nordic one.
        self.mu.fetch_nasdaq_snapshot = _fake_snapshot(
            {"STO": [("SMALL_CAP", "SE0000000001")],
             "CPH": [("SMALL_CAP", "DK0000000001")]},
            failing=("CPH",))
        rows, liq, errors = self.mu.fetch_nasdaq_snapshots(["XSTO", "XCSE"])
        self.assertEqual(list(errors), ["CPH"])
        self.assertIn("503", errors["CPH"])
        self.assertEqual([r["isin"] for r in rows], ["SE0000000001"])


class OsloVenue(unittest.TestCase):
    """Oslo Bors is Euronext, not Nasdaq: no orderbook id, no screener row,
    a separate price path. The synthetic id is what lets the rest of the
    pipeline stay unchanged."""

    def setUp(self):
        self.mu = helpers.load("market_universe")

    def test_oslo_mics_are_in_the_universe_but_not_in_the_nasdaq_set(self):
        for mic in ("XOSL", "MERK"):
            self.assertIn(mic, self.mu.ALL_MICS, mic)
            self.assertIn(mic, self.mu.VENUE_LABEL, mic)
            self.assertEqual(self.mu.COUNTRY_FOR_MIC[mic], "NO")
            self.assertNotIn(mic, self.mu.NASDAQ_MICS, mic)
            self.assertNotIn(mic, self.mu.NASDAQ_MARKET_FOR_MIC, mic)

    def test_only_the_main_market_is_regulated(self):
        # Euronext Growth Oslo is an MTF: no ESEF annual report is filed.
        self.assertIn("XOSL", self.mu.REGULATED_MICS)
        self.assertNotIn("MERK", self.mu.REGULATED_MICS)

    def test_norway_is_not_in_the_default_run(self):
        for mic in ("XOSL", "MERK"):
            self.assertNotIn(mic, self.mu.DEFAULT_MICS, mic)

    def test_synthetic_id_round_trips_and_never_matches_a_real_one(self):
        obid = self.mu.oslo_obid("NO0010096985")
        self.assertEqual(self.mu.isin_from_oslo_obid(obid), "NO0010096985")
        for real in ("12345", "", None, 12345):
            self.assertIsNone(self.mu.isin_from_oslo_obid(real), repr(real))

    def test_combine_universe_gives_oslo_rows_an_id_and_leaves_others_alone(self):
        firds = {"XOSL": {"instruments": [
                    {"isin": "NO0010096985", "name": "EQUINOR", "lei": "L1"}]},
                 "XSTO": {"instruments": [
                    {"isin": "SE0000000001", "name": "AXFOOD", "lei": "L2"}]}}
        combined = self.mu.combine_universe([], {}, firds, ["XOSL", "XSTO"])
        rows = {r["isin"]: r for r in combined}
        self.assertEqual(rows["NO0010096985"]["orderbookId"], "OSLO:NO0010096985")
        # A Swedish FIRDS-only row must NOT be handed a synthetic id: it has
        # no Euronext leg, and pretending otherwise would send it to Oslo.
        self.assertFalse(rows["SE0000000001"].get("orderbookId"))

    def test_bars_leg_reads_currency_and_price_off_the_payload(self):
        class Fake(object):
            @staticmethod
            def bars_for_isin(isin):
                return [{"date": "2026-09-03", "close": 10.0, "volume": 1, "currency": "USD"},
                        {"date": "2026-09-04", "close": 12.5, "volume": 2, "currency": "USD"}]

        row = {"isin": "NO0010096985"}
        out = self.mu.fetch_oslo_bars(row, _module=Fake)
        self.assertEqual(out["status"], "checked")
        # Not assumed NOK: Oslo carries USD and EUR lines too, and a wrong
        # currency is a factor-of-nine error in the turnover floor.
        self.assertEqual(row["currency"], "USD")
        self.assertEqual(row["price"], 12.5)

    def test_a_payload_without_a_currency_leaves_the_row_unset(self):
        class Fake(object):
            @staticmethod
            def bars_for_isin(isin):
                return [{"date": "2026-09-04", "close": 12.5, "volume": 2}]

        row = {"isin": "NO0010096985"}
        self.mu.fetch_oslo_bars(row, _module=Fake)
        self.assertIsNone(row.get("currency"))
        self.assertEqual(row["price"], 12.5)

    def test_the_last_close_wins_even_when_the_final_bar_is_a_gap(self):
        class Fake(object):
            @staticmethod
            def bars_for_isin(isin):
                return [{"date": "2026-09-03", "close": 10.0, "volume": 1},
                        {"date": "2026-09-04", "close": None, "volume": None}]

        row = {"isin": "NO0010096985"}
        self.mu.fetch_oslo_bars(row, _module=Fake)
        self.assertEqual(row["price"], 10.0)

    def test_a_failing_leg_is_not_checked_never_an_empty_series(self):
        class Boom(object):
            @staticmethod
            def bars_for_isin(isin):
                raise SystemExit("DATA NOT AVAILABLE: Euronext unreachable")

        out = self.mu.fetch_oslo_bars({"isin": "NO0010096985"}, _module=Boom)
        self.assertEqual(out["status"], "not checked")
        self.assertIsNone(out["bars"])
        self.assertIn("Euronext", out["reason"])

    def test_a_missing_module_degrades_rather_than_crashing(self):
        out = self.mu.fetch_oslo_bars({"isin": "NO0010096985"}, _module=None)
        # _module=None means "use the real one"; the real one is importable
        # here, so assert the shape rather than the outcome.
        self.assertIn(out["status"], ("checked", "not checked"))

    def test_a_row_without_an_isin_refuses(self):
        out = self.mu.fetch_oslo_bars({}, _module=object())
        self.assertEqual(out["status"], "not checked")
        self.assertIn("ISIN", out["reason"])

    def test_market_cap_does_not_send_a_synthetic_id_to_nasdaq(self):
        asked = []

        class FakeShares(object):
            @staticmethod
            def summary(obid):
                asked.append(obid)
                return {"shares": 1000}

        self.mu.nordic_shares = FakeShares
        iss = {"instruments": [{"orderbookId": "OSLO:NO0010096985",
                                "isin": "NO0010096985", "currency": "NOK",
                                "price": 100.0}]}
        self.mu.attach_market_cap([iss])
        self.assertEqual(asked, [], "a synthetic Oslo id must never reach Nasdaq")
        # And the reason must name the missing source, not an outage.
        self.assertIn("no free share-count source for Oslo",
                      iss.get("market_cap_basis") or "")


class ScreenRoutesOsloRows(unittest.TestCase):
    def test_screen_value_sends_an_oslo_row_to_the_euronext_leg(self):
        sv = helpers.load("screen_value")
        called = {}

        def fake_leg(row, _module=None):
            called["isin"] = row.get("isin")
            return {"status": "checked", "bars": [{"date": "2026-09-04",
                                                   "close": 1.0, "volume": 1}],
                    "reason": None}

        real = sv.market_universe.fetch_oslo_bars
        sv.market_universe.fetch_oslo_bars = fake_leg
        try:
            out = sv._fetch_bars_for_instrument(
                {"orderbookId": "OSLO:NO0010096985", "isin": "NO0010096985"},
                "2023-01-01", "2026-09-04")
        finally:
            sv.market_universe.fetch_oslo_bars = real
        self.assertEqual(called.get("isin"), "NO0010096985")
        self.assertEqual(out["status"], "checked")

    def test_a_nasdaq_row_still_takes_the_nasdaq_leg(self):
        sv = helpers.load("screen_value")
        sv.market_universe.fetch_oslo_bars = lambda *a, **k: self.fail(
            "a Nasdaq orderbook id must not be routed to Oslo")
        sv.nordic_shares = None
        out = sv._fetch_bars_for_instrument({"orderbookId": "12345"},
                                            "2023-01-01", "2026-09-04")
        self.assertEqual(out["status"], "not checked")


class SizePercentile(unittest.TestCase):
    @staticmethod
    def _iss(mic, cap, status="complete"):
        return {"primary": {"mic": mic}, "market_cap_sek": cap,
                "market_cap_status": status}

    def test_percentile_is_within_the_national_market_not_across_it(self):
        # A 1bn SEK company is the smallest of three in Sweden and the
        # largest of three in Denmark. One cross-border ranking would put it
        # in the middle and say nothing true about either market.
        issuers = [self._iss("XSTO", 1e9), self._iss("XSTO", 5e9),
                   self._iss("XSTO", 9e9),
                   self._iss("XCSE", 1e8), self._iss("XCSE", 5e8),
                   self._iss("XCSE", 1e9)]
        market_universe.attach_size_percentile(issuers)
        self.assertEqual(issuers[0]["size_percentile"], 0.0)
        self.assertAlmostEqual(issuers[5]["size_percentile"], 200.0 / 3)
        for iss in issuers:
            self.assertEqual(iss["size_percentile_n"], 3)

    def test_first_north_ranks_with_its_own_national_main_market(self):
        issuers = [self._iss("XCSE", 1e9), self._iss("DSME", 1e8)]
        market_universe.attach_size_percentile(issuers)
        self.assertEqual(issuers[0]["size_percentile_n"], 2)
        self.assertEqual(issuers[1]["size_percentile"], 0.0)

    def test_a_partial_cap_yields_a_floor_percentile(self):
        issuers = [self._iss("XSTO", 1e9), self._iss("XSTO", 5e9, "partial"),
                   self._iss("XSTO", 9e9)]
        market_universe.attach_size_percentile(issuers)
        self.assertTrue(issuers[1]["size_percentile_is_floor"])
        self.assertFalse(issuers[0]["size_percentile_is_floor"])

    def test_no_market_cap_is_not_checked_never_a_zero(self):
        issuers = [self._iss("XSTO", 1e9), self._iss("XSTO", None)]
        market_universe.attach_size_percentile(issuers)
        self.assertIsNone(issuers[1]["size_percentile"])
        self.assertIsNone(issuers[1]["size_percentile_n"])
        self.assertIn("no market cap", issuers[1]["size_percentile_status"])

    def test_an_unmapped_mic_refuses_rather_than_guessing_a_market(self):
        issuers = [self._iss("XPAR", 1e9)]
        market_universe.attach_size_percentile(issuers)
        self.assertIsNone(issuers[0]["size_percentile"])
        self.assertIn("XPAR", issuers[0]["size_percentile_status"])

    def test_the_percentile_never_removes_an_issuer(self):
        issuers = [self._iss("XSTO", 1e9), self._iss("XCSE", None),
                   self._iss("XPAR", 5e9)]
        before = len(issuers)
        market_universe.attach_size_percentile(issuers)
        self.assertEqual(len(issuers), before)


class MfnDanishNames(unittest.TestCase):
    """A/S is the standard Danish and Norwegian company form. MFN's search
    endpoint answers HTTP 500 to any query containing a slash, so before this
    the MFN corporate-action leg was dead for every Danish issuer - and
    reported as an outage rather than as the encoding bug it is."""

    def setUp(self):
        self.mfn = helpers.load("mfn_news")

    def test_the_slash_is_removed_not_replaced_with_a_space(self):
        # "cBrain A S" 500s too - only "cBrain AS" is accepted.
        self.assertEqual(self.mfn._searchable("cBrain A/S"), "cBrain AS")
        self.assertEqual(self.mfn._searchable("Better Collective A/S"),
                         "Better Collective AS")

    def test_names_without_a_slash_pass_through_untouched(self):
        for name in ("Axfood", "Qt Group Oyj", "Handelsbanken A", "Volvo"):
            self.assertEqual(self.mfn._searchable(name), name)

    def test_empty_and_none_are_safe(self):
        self.assertEqual(self.mfn._searchable(None), "")
        self.assertEqual(self.mfn._searchable("  "), "")

    def test_search_sends_the_cleaned_term(self):
        sent = {}

        def fake_fetch(path, **params):
            sent.update(params)
            return {"items": []}

        self.mfn.fetch = fake_fetch
        self.mfn.search("Matas A/S")
        self.assertEqual(sent["query"], "Matas AS")


class ScreenVenueParsing(unittest.TestCase):
    def setUp(self):
        self.sv = helpers.load("screen_value")
        self.sd = helpers.load("screen_digest")

    def test_danish_and_finnish_mics_are_accepted_by_both_screens(self):
        for mod in (self.sv, self.sd):
            self.assertEqual(mod._parse_venues("xcse,dsme,xhel,fsme"),
                             ["XCSE", "DSME", "XHEL", "FSME"])

    def test_no_venue_means_sweden_in_both_screens(self):
        for mod in (self.sv, self.sd):
            self.assertEqual(mod._parse_venues(None), list(mod.DEFAULT_MICS))

    def test_an_unknown_mic_is_refused_and_names_the_alternatives(self):
        for mod in (self.sv, self.sd):
            with self.assertRaises(ValueError) as ctx:
                mod._parse_venues("xnas")
            self.assertIn("xcse", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
