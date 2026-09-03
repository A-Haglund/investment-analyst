#!/usr/bin/env python3
"""corporate_actions.py / nordic_shares.py - the price-adjustment truth.

v2.6 architecture review's highest-ranked defect: this codebase asserted
BOTH that Nasdaq Nordic's daily price history is back-adjusted for splits
AND that it is unadjusted, in different docstrings, with the language model
reading whichever one it hit as an operating instruction. The resolution
(see corporate_actions.py's module docstring, "THE ANSWER" section): the
series IS back-adjusted for splits, measured against four confirmed, dated
Stockholm splits by price_check() below. Every contradicting docstring and
printed line was corrected to agree.

Covers, all offline (nordic_shares.search/price_history are monkeypatched at
the module-attribute level on the real, already-imported sibling module - no
socket is ever opened by this suite):

  - price_check()'s three outcomes on synthetic bar series with a known
    relationship to a synthetic split date: a clean halving (UNADJUSTED), no
    discontinuity at all (ALREADY SPLIT-ADJUSTED), and a move that matches
    neither pattern (INCONCLUSIVE) - each must carry a plain `instruction`,
    and the ambiguous case must never carry anything an unattended caller
    could read as a factor to apply.
  - split_adjustment_factor() end to end (every network-touching dependency
    monkeypatched) refuses to fold an UNCONFIRMED share-count signature into
    `factor` when no exchange notice confirms it - `factor` stays None and
    `reliable` stays False, exactly the mechanism the module docstring's
    "THE DESIGN CHOICE THAT MATTERS" section describes.
  - _to_int()'s v3.0.0 delegation to scripts/numparse.py via
    _bootstrap.soft_load: it must actually call into a numparse module when
    one is present, and degrade to its own original stripping logic when
    _bootstrap is absent, numparse is absent, or numparse raises - the
    "fall back rather than crash" convention this repo uses everywhere else
    a sibling module might be mid-edit. A second class exercises the
    stripping logic itself (Swedish space/nbsp thousands, English comma
    thousands, period-grouped thousands, a typographic minus) against
    whichever path is actually active on this checkout, so it keeps passing
    once numparse.py lands.
"""
import unittest
from unittest import mock

import helpers

helpers.bootstrap_path()

ca = helpers.load("corporate_actions")


def _bars(rows):
    """[(date, close), ...] -> the bar-dict shape nordic_shares.price_history
    returns. open/high/low/volume are never read by price_check."""
    return [{"date": d, "open": c, "high": c, "low": c, "close": c, "volume": 1000}
            for d, c in rows]


# ---------------------------------------------------------------------------
# price_check() - the three outcomes
# ---------------------------------------------------------------------------

class PriceCheckOffline(unittest.TestCase):
    """price_check() compares the close immediately before a confirmed
    split's effective date with the close immediately after. It never
    decides whether a split happened (the exchange notice does that) - it
    only diagnoses which convention the PRICE SERIES in hand follows."""

    EFFECTIVE = "2024-06-15"

    def _patched(self, bars):
        hit = [{"orderbookId": 99999, "symbol": "TEST"}]
        return (mock.patch.object(ca.nordic_shares, "search", return_value=hit),
                mock.patch.object(ca.nordic_shares, "price_history",
                                  return_value=bars))

    def test_unadjusted_series_shows_the_split(self):
        """A clean 2:1 halving right at the effective date is exactly what an
        UNADJUSTED series does - the close divides by the split factor."""
        bars = _bars([("2024-06-13", 100.0), ("2024-06-14", 100.0),
                      ("2024-06-17", 50.0), ("2024-06-18", 50.0)])
        p_search, p_hist = self._patched(bars)
        with p_search, p_hist:
            result = ca.price_check("Test Co", self.EFFECTIVE, 2.0)
        self.assertEqual(result["status"], "SERIES IS UNADJUSTED")
        self.assertIn("NOT comparable", result["detail"])
        self.assertIn("instruction", result)
        self.assertIn("APPLY", result["instruction"])

    def test_already_adjusted_series_shows_no_discontinuity(self):
        """No jump at all across the effective date - Nasdaq has already
        back-adjusted the series. This is the documented NORMAL case for
        this endpoint (four confirmed splits measured, all four this way)."""
        bars = _bars([("2024-06-13", 100.0), ("2024-06-14", 100.0),
                      ("2024-06-17", 99.0), ("2024-06-18", 101.0)])
        p_search, p_hist = self._patched(bars)
        with p_search, p_hist:
            result = ca.price_check("Test Co", self.EFFECTIVE, 2.0)
        self.assertEqual(result["status"], "SERIES IS ALREADY SPLIT-ADJUSTED")
        self.assertIn("back-adjusted", result["detail"])
        self.assertIn("Do NOT apply", result["instruction"])

    def test_ambiguous_move_is_neither_verdict(self):
        """A move matching neither a clean halving nor a clean series -
        ordinary volatility, a trading gap, or a second event on the same
        date. Must not be silently read as either verdict, and must never
        carry anything an unattended caller could apply as a factor."""
        bars = _bars([("2024-06-13", 100.0), ("2024-06-14", 100.0),
                      ("2024-06-17", 75.0), ("2024-06-18", 75.0)])
        p_search, p_hist = self._patched(bars)
        with p_search, p_hist:
            result = ca.price_check("Test Co", self.EFFECTIVE, 2.0)
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertNotIn("factor", result)
        self.assertIn("Do NOT apply a factor", result["instruction"])
        self.assertIn("ambiguous", result["instruction"])

    def test_data_not_available_without_nordic_shares(self):
        real = ca.nordic_shares
        ca.nordic_shares = None
        try:
            result = ca.price_check("Test Co", self.EFFECTIVE, 2.0)
        finally:
            ca.nordic_shares = real
        self.assertEqual(result["status"], "DATA NOT AVAILABLE")

    def test_data_not_available_without_effective_date(self):
        result = ca.price_check("Test Co", None, 2.0)
        self.assertEqual(result["status"], "DATA NOT AVAILABLE")


# ---------------------------------------------------------------------------
# split_adjustment_factor() - refuses a factor on ambiguous evidence
# ---------------------------------------------------------------------------

class SplitAdjustmentFactorRefusesOnAmbiguity(unittest.TestCase):
    """Only a CONFIRMED split (Nasdaq exchange notice) may ever contribute to
    `factor` (module docstring, "THE DESIGN CHOICE THAT MATTERS"). A
    near-integer jump in the share-count log with NO confirming notice is a
    signature, not a confirmation, and must be reported as a warning while
    `factor` stays None. Every network-touching dependency of
    split_adjustment_factor is monkeypatched so this drives the real
    end-to-end pipeline, offline."""

    # A clean, reliably-timed 2:1 jump in the disclosure log with nothing
    # else in the picture - the exact shape share_count_split_check() (run
    # for real inside split_adjustment_factor) is built to flag.
    EVENTS = [
        {"date": "2023-01-10T08:00", "title": "shares outstanding",
         "total_shares": 100000000},
        {"date": "2023-06-01T08:00", "title": "shares outstanding",
         "total_shares": 200000000},
    ]

    def test_unconfirmed_signature_yields_no_factor(self):
        with mock.patch.object(ca, "find_split_notices", return_value=[]), \
             mock.patch.object(ca, "share_history",
                               return_value=(self.EVENTS, 0, False)), \
             mock.patch.object(ca, "collect_nasdaq", return_value=[]), \
             mock.patch.object(ca, "collect_mfn", return_value=([], None)), \
             mock.patch.object(ca, "find_split_announcements", return_value=[]):
            result = ca.split_adjustment_factor(
                "Test Co", "2023-01-01", "2023-12-31")

        self.assertIsNone(result["factor"])
        self.assertFalse(result["reliable"])
        self.assertEqual(len(result["confirmed_splits"]), 0)
        self.assertEqual(len(result["unconfirmed_signatures"]), 1)
        self.assertEqual(result["unconfirmed_signatures"][0]["looks_like"],
                         "2:1 split")
        self.assertTrue(any("NOT included in `factor`" in w
                            for w in result["warnings"]),
                        result["warnings"])
        self.assertTrue(any("factor is None" in w for w in result["warnings"]),
                        result["warnings"])

    def test_confirmed_split_alone_does_yield_a_factor(self):
        """Sanity check on the other side of the same mechanism: a CONFIRMED
        notice with no competing unconfirmed signature or other action DOES
        produce a usable factor. Without this, the ambiguity test above could
        pass merely because the pipeline never returns a factor at all."""
        notice = {"messageUrl": "https://example.invalid/notice/1",
                  "published": "2023-06-01T08:00"}
        parsed = {"effective_date": "2023-06-01",
                 "events": [{"kind": "SPLIT", "terms": "2:1", "factor": 2.0}]}
        with mock.patch.object(ca, "find_split_notices", return_value=[notice]), \
             mock.patch.object(ca, "cns_body", return_value="<html></html>"), \
             mock.patch.object(ca, "parse_split_notice", return_value=parsed), \
             mock.patch.object(ca, "share_history",
                               return_value=(self.EVENTS, 0, False)), \
             mock.patch.object(ca, "collect_nasdaq", return_value=[]), \
             mock.patch.object(ca, "collect_mfn", return_value=([], None)), \
             mock.patch.object(ca, "find_split_announcements", return_value=[]):
            result = ca.split_adjustment_factor(
                "Test Co", "2023-01-01", "2023-12-31")

        self.assertEqual(result["factor"], 2.0)
        self.assertEqual(len(result["confirmed_splits"]), 1)
        # The share-count jump is now the SAME event seen from the other
        # side (within 10 days of the confirmed notice date), not a second,
        # unconfirmed one.
        self.assertEqual(len(result["unconfirmed_signatures"]), 0)
        self.assertTrue(result["reliable"])


# ---------------------------------------------------------------------------
# _to_int() - numparse delegation and fallback
# ---------------------------------------------------------------------------

class ToIntDelegatesToNumparse(unittest.TestCase):
    """_to_int is no longer a fourth private copy of the space/nbsp/comma/
    period stripping every sibling script used to do its own way - it
    delegates to scripts/numparse.py (v3.0.0) via _bootstrap.soft_load, and
    falls back to its own original logic whenever that path is unavailable
    or disagrees. ca._bootstrap is monkeypatched directly (mirroring how
    test_multi_share_class.py patches cr.nordic) so this is exact regardless
    of whether the real _bootstrap.py/numparse.py exist yet on this
    checkout."""

    def setUp(self):
        self._real_bootstrap = ca._bootstrap

    def tearDown(self):
        ca._bootstrap = self._real_bootstrap

    def test_delegates_to_numparse_to_int_when_available(self):
        calls = []

        class FakeNumparse(object):
            @staticmethod
            def to_int(raw):
                calls.append(raw)
                return 195833018

        class FakeBootstrap(object):
            @staticmethod
            def soft_load(name):
                self.assertEqual(name, "numparse")
                return FakeNumparse()

        ca._bootstrap = FakeBootstrap()
        self.assertEqual(ca._to_int("195 833 018"), 195833018)
        self.assertEqual(calls, ["195 833 018"])

    def test_falls_back_to_to_number_when_to_int_absent(self):
        class FakeNumparse(object):
            @staticmethod
            def to_number(raw):
                return 195833018.0

        class FakeBootstrap(object):
            @staticmethod
            def soft_load(name):
                return FakeNumparse()

        ca._bootstrap = FakeBootstrap()
        self.assertEqual(ca._to_int("195,833,018"), 195833018)

    def test_falls_back_to_local_logic_when_numparse_absent(self):
        class FakeBootstrap(object):
            @staticmethod
            def soft_load(name):
                return None

        ca._bootstrap = FakeBootstrap()
        self.assertEqual(ca._to_int("195 833 018"), 195833018)

    def test_falls_back_to_local_logic_when_numparse_raises(self):
        class FakeNumparse(object):
            @staticmethod
            def to_int(raw):
                raise ValueError("boom")

        class FakeBootstrap(object):
            @staticmethod
            def soft_load(name):
                return FakeNumparse()

        ca._bootstrap = FakeBootstrap()
        self.assertEqual(ca._to_int("195 833 018"), 195833018)

    def test_falls_back_to_local_logic_when_bootstrap_module_absent(self):
        """The state of every checkout before scripts/_bootstrap.py exists,
        and the degradation path if a parallel edit ever leaves it broken:
        the top-of-file `try: import _bootstrap / except Exception:
        _bootstrap = None` guard, exercised directly."""
        ca._bootstrap = None
        self.assertEqual(ca._to_int("195 833 018"), 195833018)


class ToIntParsesEverySeparatorConvention(unittest.TestCase):
    """Exercises whichever path is actually active (numparse delegation once
    it exists, or the local fallback today) against every thousands
    convention this parser has to survive. Share counts are always whole
    numbers with grouping only, never a decimal point, so a period is a
    thousands separator here exactly like a comma or a space."""

    def test_swedish_space_thousands(self):
        self.assertEqual(ca._to_int("195 833 018"), 195833018)

    def test_swedish_nbsp_thousands(self):
        self.assertEqual(ca._to_int("195 833 018"), 195833018)

    def test_english_comma_thousands(self):
        self.assertEqual(ca._to_int("195,833,018"), 195833018)

    def test_period_grouped_thousands(self):
        self.assertEqual(ca._to_int("195.833.018"), 195833018)

    def test_typographic_minus_sign_does_not_crash(self):
        """U+2212 MINUS SIGN, as opposed to ASCII hyphen-minus '-'. Share
        counts are never negative in practice, but a parser that raises
        instead of returning None on an unexpected character is the wrong
        failure mode everywhere in this toolkit (see mfn_news.py's own
        sign-handling bugs in test_parser_regressions.py) - this only
        guards against a crash, it does not mandate a specific reading."""
        result = ca._to_int("−123")
        self.assertIn(result, (None, -123))

    def test_none_on_garbage(self):
        self.assertIsNone(ca._to_int("not a number"))


if __name__ == "__main__":
    unittest.main()
