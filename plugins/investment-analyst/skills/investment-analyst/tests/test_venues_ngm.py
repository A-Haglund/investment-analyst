#!/usr/bin/env python3
"""NGM post-trade turnover (ngm_turnover / ngm_trading_days) in venues_se.py.

venues_se.py used to imply, by never mentioning one, that no free price or
turnover source exists for NGM Equity (XNGM) or Nordic SME (NSME). It does:
mdapi.ngm.se/delayed/post-trade is a sibling of the delayed/pre-trade
bid/ask snapshot ngm_symbols() already reads, and carries every executed
trade with a "Venue of execution" field that cleanly separates XNGM and NSME
from NMTF (NGM's ETP and warrant segment - securities, not the listed equity
universe this script covers).

Everything here is OFFLINE. The real feed is a live, dated, 2.7 MB CSV with a
72-hour retention window, so nothing about it can be baked into a fixture
that stays true tomorrow - instead this file feeds a small CSV shaped
exactly like the real header (helpers.py's `network` marker, and the
run_tests.py --network live check, cover the real endpoint). Every fetch
that would touch the network is monkeypatched.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

venues = load("venues_se")


# Column names match the real feed exactly (verified live 2026-09-01); extra
# columns the parser never reads are included, named plausibly, to prove the
# parser keys off column NAME (csv.DictReader) rather than position.
HEADER = ("Trading date and Time,Instrument identification code type,"
         "Instrument identification code,Price,Venue of execution,"
         "Price notation,Price currency,Trading capacity,Publication venue,"
         "Quantity,Complex trade component id")

FIXTURE_ROWS = [
    # Two XNGM trades, same ISIN: turnover must sum, and last_price must be
    # the LATER trade (12.500) even though it is not the larger of the two
    # prices seen (13.000, at the earlier timestamp) - last by time, not by
    # size and not by first-seen.
    "2026-08-31T09:35:48.370Z,ISIN,SE0007897384,13.000,XNGM,PERC,SEK,,,10000,",
    "2026-08-31T14:02:11.000Z,ISIN,SE0007897384,12.500,XNGM,PERC,SEK,,,5000,",
    # One NSME trade, plain SEK.
    "2026-08-31T10:15:00.000Z,ISIN,SE0013382546,50.000,NSME,PERC,SEK,,,2000,",
    # One NSME trade in DKK - currency must be read per row, never assumed
    # SEK (the exact bug this file had earlier for Spotlight market cap).
    "2026-08-31T11:00:00.000Z,ISIN,DK0060000000,105.750,NSME,PERC,DKK,,,300,",
    # NMTF: NGM's ETP/warrant segment, must be excluded entirely.
    "2026-08-31T09:10:00.000Z,ISIN,NL0000000001,5.000,NMTF,PERC,EUR,,,1000,",
    # Malformed: no ISIN. Must be skipped and counted, not crash the parse.
    "2026-08-31T09:20:00.000Z,ISIN,,7.500,XNGM,PERC,SEK,,,100,",
    # Malformed: unparseable price (mfn_news.to_number returns None on junk).
    "2026-08-31T09:25:00.000Z,ISIN,SE0099999999,not-a-number,XNGM,PERC,SEK,,,100,",
]

FIXTURE_CSV = (HEADER + "\n" + "\n".join(FIXTURE_ROWS) + "\n").encode("utf-8")


class ParsePostTrade(unittest.TestCase):
    """venues._parse_ngm_posttrade() is a pure function over CSV bytes - no
    monkeypatching needed to exercise the parsing logic itself."""

    def setUp(self):
        self.rows, self.malformed = venues._parse_ngm_posttrade(FIXTURE_CSV)

    def test_turnover_summed_correctly_per_isin(self):
        entry = self.rows["SE0007897384"]
        expected = 13.000 * 10000 + 12.500 * 5000
        self.assertAlmostEqual(entry["turnover"], expected)
        self.assertEqual(entry["trades"], 2)

    def test_segments_split_xngm_and_nsme_both_present(self):
        self.assertEqual(self.rows["SE0007897384"]["mic"], "XNGM")
        self.assertEqual(self.rows["SE0013382546"]["mic"], "NSME")
        self.assertEqual(self.rows["DK0060000000"]["mic"], "NSME")

    def test_nmtf_is_excluded(self):
        self.assertNotIn("NL0000000001", self.rows)
        for entry in self.rows.values():
            self.assertNotEqual(entry["mic"], "NMTF")

    def test_non_sek_row_keeps_its_own_currency(self):
        entry = self.rows["DK0060000000"]
        self.assertEqual(entry["currency"], "DKK")
        # And a same-file SEK row must not have been coerced to match it, or
        # vice versa - each ISIN keeps the currency its own rows reported.
        self.assertEqual(self.rows["SE0013382546"]["currency"], "SEK")

    def test_last_price_is_latest_by_timestamp_not_the_largest(self):
        entry = self.rows["SE0007897384"]
        # The later trade (14:02) priced lower (12.500) than the earlier one
        # (13.000 at 09:35). last_price must be 12.500, proving it is picked
        # by timestamp, not by max().
        self.assertEqual(entry["last_price"], 12.500)
        self.assertEqual(entry["last_trade_utc"], "2026-08-31T14:02:11.000Z")

    def test_malformed_rows_skipped_and_counted_not_crashing(self):
        # Two malformed rows in the fixture: empty ISIN, and an unparseable
        # price. Both must be counted, and neither may appear as an entry.
        self.assertEqual(self.malformed, 2)
        self.assertNotIn("", self.rows)
        self.assertNotIn("SE0099999999", self.rows)

    def test_only_real_isins_survive(self):
        self.assertEqual(set(self.rows),
                         {"SE0007897384", "SE0013382546", "DK0060000000"})


class ToNumberGuardsSiblingCall(unittest.TestCase):
    """venues._to_number() wraps mfn_news.to_number() and must never let a
    sibling failure - Exception OR SystemExit, which does not inherit from
    Exception - escape and crash the CSV parse."""

    def setUp(self):
        self._real_mfn_news = venues.mfn_news

    def tearDown(self):
        venues.mfn_news = self._real_mfn_news

    def test_to_number_survives_sibling_raising_systemexit(self):
        class _Fake(object):
            @staticmethod
            def to_number(raw):
                raise SystemExit("simulated sibling failure")
        venues.mfn_news = _Fake
        self.assertIsNone(venues._to_number("123"))

    def test_to_number_survives_sibling_raising_exception(self):
        class _Fake(object):
            @staticmethod
            def to_number(raw):
                raise RuntimeError("simulated sibling failure")
        venues.mfn_news = _Fake
        self.assertIsNone(venues._to_number("123"))

    def test_to_number_returns_none_when_sibling_missing(self):
        venues.mfn_news = None
        self.assertIsNone(venues._to_number("123"))

    def test_parse_still_completes_when_sibling_is_broken(self):
        """The end-to-end consequence: if mfn_news itself is broken, the
        whole post-trade file must degrade to zero real rows (everything
        malformed), never crash."""
        class _Fake(object):
            @staticmethod
            def to_number(raw):
                raise RuntimeError("simulated sibling failure")
        venues.mfn_news = _Fake
        rows, malformed = venues._parse_ngm_posttrade(FIXTURE_CSV)
        self.assertEqual(rows, {})
        self.assertGreater(malformed, 0)


class CompletedTradingDayResolution(unittest.TestCase):
    """date=None must never silently return a partial (still-accumulating)
    day. _completed_trading_day() is the pure decision function; ngm_turnover
    wires it up with monkeypatched fetches below."""

    def setUp(self):
        self._real_today = venues._utc_today
        venues._utc_today = lambda: "2026-09-01"

    def tearDown(self):
        venues._utc_today = self._real_today

    def test_todays_entry_is_skipped_in_favour_of_the_prior_day(self):
        dates = ["2026-09-01", "2026-08-31", "2026-08-30"]
        self.assertEqual(venues._completed_trading_day(dates), "2026-08-31")

    def test_no_prior_day_available_returns_none(self):
        self.assertIsNone(venues._completed_trading_day(["2026-09-01"]))

    def test_feed_queried_before_todays_file_exists_uses_newest_directly(self):
        # Newest entry is not today (e.g. queried just after UTC midnight
        # before NGM has posted anything yet) - already final, use directly.
        dates = ["2026-08-31", "2026-08-30"]
        self.assertEqual(venues._completed_trading_day(dates), "2026-08-31")

    def test_empty_list_returns_none(self):
        self.assertIsNone(venues._completed_trading_day([]))


class NgmTurnoverDateHandling(unittest.TestCase):
    """ngm_turnover()'s date=None vs explicit-date behaviour, with every
    network call monkeypatched away."""

    def setUp(self):
        self._real_today = venues._utc_today
        self._real_days = venues.ngm_trading_days
        self._real_csv = venues._ngm_posttrade_csv
        venues._utc_today = lambda: "2026-09-01"

    def tearDown(self):
        venues._utc_today = self._real_today
        venues.ngm_trading_days = self._real_days
        venues._ngm_posttrade_csv = self._real_csv

    def test_date_none_resolves_to_previous_completed_day_not_partial(self):
        venues.ngm_trading_days = lambda: ["2026-09-01", "2026-08-31"]
        seen = {}
        def fake_csv(date):
            seen["date"] = date
            return FIXTURE_CSV
        venues._ngm_posttrade_csv = fake_csv

        result = venues.ngm_turnover(None)
        self.assertIsNotNone(result)
        self.assertEqual(seen["date"], "2026-08-31")
        self.assertEqual(result["_meta"]["date"], "2026-08-31")
        self.assertFalse(result["_meta"]["partial"])

    def test_date_none_with_only_todays_file_available_returns_none(self):
        venues.ngm_trading_days = lambda: ["2026-09-01"]
        venues._ngm_posttrade_csv = lambda date: FIXTURE_CSV  # must not even be called
        self.assertIsNone(venues.ngm_turnover(None))

    def test_explicit_todays_date_is_flagged_partial(self):
        venues._ngm_posttrade_csv = lambda date: FIXTURE_CSV
        result = venues.ngm_turnover("2026-09-01")
        self.assertIsNotNone(result)
        self.assertTrue(result["_meta"]["partial"])

    def test_explicit_completed_date_is_not_flagged_partial(self):
        venues._ngm_posttrade_csv = lambda date: FIXTURE_CSV
        result = venues.ngm_turnover("2026-08-31")
        self.assertIsNotNone(result)
        self.assertFalse(result["_meta"]["partial"])

    def test_meta_reports_segments_and_malformed_count(self):
        venues._ngm_posttrade_csv = lambda date: FIXTURE_CSV
        result = venues.ngm_turnover("2026-08-31")
        meta = result["_meta"]
        self.assertEqual(set(meta["segments_included"]), {"XNGM", "NSME"})
        self.assertEqual(meta["segments_excluded"], ["NMTF"])
        self.assertEqual(meta["malformed_rows_skipped"], 2)

    def test_meta_key_is_not_mistaken_for_an_isin(self):
        """"_meta" must never collide with a real ISIN (2 letters + 10
        alphanumerics) - guard the reserved-key contract itself."""
        self.assertFalse(venues.re.match(r"^[A-Z]{2}[A-Z0-9]{10}$", "_meta"))


class FetchFailureDegrades(unittest.TestCase):
    """A dead NGM endpoint must degrade ngm_turnover() to None, never raise -
    same contract as every other fetch-backed function in this file."""

    def setUp(self):
        self._real_days = venues.ngm_trading_days
        self._real_csv = venues._ngm_posttrade_csv
        self._real_index_fetch = venues.fetch_json

    def tearDown(self):
        venues.ngm_trading_days = self._real_days
        venues._ngm_posttrade_csv = self._real_csv
        venues.fetch_json = self._real_index_fetch

    def test_dead_index_degrades_to_none(self):
        venues.ngm_trading_days = lambda: None
        self.assertIsNone(venues.ngm_turnover(None))

    def test_dead_csv_fetch_degrades_to_none(self):
        venues.ngm_trading_days = lambda: ["2026-08-31", "2026-08-30"]
        venues._ngm_posttrade_csv = lambda date: None
        self.assertIsNone(venues.ngm_turnover(None))

    def test_dead_csv_fetch_degrades_to_none_for_an_explicit_date_too(self):
        venues._ngm_posttrade_csv = lambda date: None
        self.assertIsNone(venues.ngm_turnover("2026-08-31"))

    def test_oversized_response_is_refused_not_parsed_truncated(self):
        """_ngm_posttrade_csv itself must refuse (return None) rather than
        hand back a truncated read when the cap is exceeded - exercised via
        the real function with urlopen monkeypatched, since this is the one
        path that talks about a size cap at all."""
        real_urlopen = venues.urllib.request.urlopen

        class _HugeResp(object):
            def __enter__(self_):
                return self_
            def __exit__(self_, *a):
                return False
            def read(self_, n=None):
                # Pretend the server sent more than the cap allows.
                return b"x" * (n if n else 1)
        venues.urllib.request.urlopen = lambda *a, **k: _HugeResp()
        # Point at a cache key that cannot already be on disk from an
        # earlier run of this file.
        try:
            result = venues._ngm_posttrade_csv("1999-01-01")
        finally:
            venues.urllib.request.urlopen = real_urlopen
        self.assertIsNone(result)


class PrintNgmTurnoverCliDegrades(unittest.TestCase):
    """--ngm-turnover's print path must turn a None result into a clear
    SystemExit ("DATA NOT AVAILABLE: ..."), never a raw exception or a
    silent empty success."""

    def setUp(self):
        self._real_turnover = venues.ngm_turnover

    def tearDown(self):
        venues.ngm_turnover = self._real_turnover

    def test_none_result_raises_systemexit_with_clear_message(self):
        venues.ngm_turnover = lambda date: None

        class _Args(object):
            json = False
        with self.assertRaises(SystemExit) as ctx:
            venues.print_ngm_turnover(None, _Args())
        self.assertIn("DATA NOT AVAILABLE", str(ctx.exception))

    def test_successful_result_prints_without_raising(self):
        import io
        import contextlib

        def fake_turnover(date):
            out = {k: dict(v) for k, v in
                   venues._parse_ngm_posttrade(FIXTURE_CSV)[0].items()}
            out["_meta"] = {"date": "2026-08-31", "partial": False,
                            "malformed_rows_skipped": 2,
                            "segments_included": ["XNGM", "NSME"],
                            "segments_excluded": ["NMTF"]}
            return out
        venues.ngm_turnover = fake_turnover

        class _Args(object):
            json = False
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            venues.print_ngm_turnover(None, _Args())
        printed = buf.getvalue()
        self.assertIn("SE0007897384", printed)
        self.assertIn("DKK", printed)
        self.assertNotIn("NL0000000001", printed)


if __name__ == "__main__":
    unittest.main()
