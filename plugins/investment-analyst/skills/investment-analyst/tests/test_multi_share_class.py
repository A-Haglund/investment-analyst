#!/usr/bin/env python3
"""Group 3 (spec §37) - multi-share-class aggregation.

Swedish large caps routinely carry two (or more) listed share classes, and
the most common silent error in this domain is taking the count from one
class only. company_resolve.share_classes() is the code that must sum every
listed class without double-counting a class that is cross-listed under
several order books (Nordea-style: one ISIN, three venues).

The offline tests below drive that aggregation logic directly, with
nordic_shares.py's network calls replaced by canned data, so they are exact
and fast. The network tests reproduce the real numbers named in the task
against Nasdaq Nordic's live reference data (Volvo, Atlas Copco, Investor,
NIBE) and are opt-in - see helpers.network.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load, network

cr = load("company_resolve")
nordic_shares = load("nordic_shares")


def make_candidate(lines):
    cand = cr.Candidate()
    cand.lines = list(lines)
    cand.isins = {l["isin"] for l in lines}
    return cand


class ShareClassAggregationOffline(unittest.TestCase):
    """cr.nordic is the module-level `nordic_shares` import inside
    company_resolve.py; monkeypatching its `summary` (and, where relevant,
    `search`) keeps this deterministic and offline while exercising the real
    aggregation code in share_classes()."""

    def setUp(self):
        self._real_summary = cr.nordic.summary
        self._real_search = cr.nordic.search

    def tearDown(self):
        cr.nordic.summary = self._real_summary
        cr.nordic.search = self._real_search

    def test_sums_every_listed_class(self):
        lines = [
            {"orderbookId": 1, "symbol": "TEST A", "isin": "SE0000000001",
             "currency": "SEK", "name": "Test Group A", "group": "Nasdaq Stockholm"},
            {"orderbookId": 2, "symbol": "TEST B", "isin": "SE0000000002",
             "currency": "SEK", "name": "Test Group B", "group": "Nasdaq Stockholm"},
        ]
        cand = make_candidate(lines)
        shares_by_ob = {1: 600000000, 2: 1400000000}
        cr.nordic.summary = lambda ob: {"orderbookId": ob, "isin": None,
                                        "shares": shares_by_ob[ob],
                                        "market_cap": None, "segment": "LARGE_CAP",
                                        "note": ""}
        cr.nordic.search = lambda isin: []

        classes = cr.share_classes(cand)
        self.assertEqual(len(classes), 2)
        total = sum(c["shares"] for c in classes)
        self.assertEqual(total, 2000000000)

    def test_cross_listed_order_books_are_not_double_counted(self):
        """Nordea-style: the same ISIN quoted under two order books (e.g. a
        Stockholm line and a cross-listed Helsinki line) is ONE share class,
        not two. share_classes() must count it once."""
        lines = [
            {"orderbookId": 10, "symbol": "NDA SE", "isin": "SE0000000099",
             "currency": "SEK", "name": "Nordea SE", "group": "Nasdaq Stockholm"},
            {"orderbookId": 11, "symbol": "NDA FI", "isin": "SE0000000099",
             "currency": "EUR", "name": "Nordea FI", "group": "Nasdaq Helsinki"},
        ]
        cand = make_candidate(lines)
        cr.nordic.summary = lambda ob: {"orderbookId": ob, "isin": "SE0000000099",
                                        "shares": 3300000000, "market_cap": None,
                                        "segment": "LARGE_CAP", "note": ""}
        cr.nordic.search = lambda isin: []

        classes = cr.share_classes(cand)
        self.assertEqual(len(classes), 1,
                          "one ISIN across two order books is one share class")
        self.assertEqual(classes[0]["shares"], 3300000000)
        self.assertEqual(classes[0]["cross_listed_as"], ["NDA FI"])

    def test_single_listed_class_is_the_unlisted_a_class_trigger(self):
        """NIBE's unlisted A class is invisible to Nasdaq Nordic reference
        data; the only observable signal is that exactly one listed class
        comes back. assemble()'s warning fires on len(classes) == 1 - this
        test drives that same condition through share_classes()."""
        lines = [
            {"orderbookId": 5, "symbol": "NIBE B", "isin": "SE0000000123",
             "currency": "SEK", "name": "NIBE Industrier B", "group": "Nasdaq Stockholm"},
        ]
        cand = make_candidate(lines)
        cr.nordic.summary = lambda ob: {"orderbookId": ob, "isin": "SE0000000123",
                                        "shares": 1000000000, "market_cap": None,
                                        "segment": "LARGE_CAP", "note": ""}
        cr.nordic.search = lambda isin: []

        classes = cr.share_classes(cand)
        self.assertEqual(len(classes), 1)
        # This is precisely the condition assemble() tests before emitting:
        # "Only one listed class found. An UNLISTED class would be invisible
        # here (NIBE, Fenix Outdoor)."
        self.assertTrue(len(classes) == 1)


@network
class MultiShareClassLiveTotals(unittest.TestCase):
    """Live Nasdaq Nordic reference data. Root symbols (VOLV / ATCO / INVE)
    are used instead of company names so the search cannot be confused with
    an unrelated issuer (e.g. "Volvo" also matching Volvo Car / VOLCAR)."""

    EXPECTED_TOTALS = {
        "VOLV": 2033451933,    # AB Volvo, A + B
        "ATCO": 4918452416,    # Atlas Copco, A + B
        "INVE": 3068700120,    # Investor, A + B
    }

    def test_totals_sum_all_listed_classes(self):
        for root, expected in self.EXPECTED_TOTALS.items():
            with self.subTest(root=root):
                hits = nordic_shares.search(root)
                classes = [h for h in hits
                          if nordic_shares.root_symbol(h["symbol"]) == root
                          and h.get("isin")]
                self.assertTrue(classes, "no listed classes found for %r" % root)
                by_isin = {}
                for c in classes:
                    by_isin.setdefault(c["isin"], c)
                total = sum(nordic_shares.summary(c["orderbookId"])["shares"] or 0
                           for c in by_isin.values())
                # nordic_shares.py's own docstring: "exact to within 151
                # shares on 2.03bn" - a loose tolerance absorbs normal
                # buyback/issuance drift without masking a real regression.
                self.assertAlmostEqual(
                    total, expected, delta=max(1000, expected * 0.002),
                    msg="%s: got %r, expected close to %r" % (root, total, expected))

    def test_nibe_shows_exactly_one_listed_class(self):
        hits = nordic_shares.search("NIBE")
        self.assertTrue(hits, "NIBE not found on Nasdaq Nordic")
        root = nordic_shares.root_symbol(hits[0]["symbol"])
        classes = [h for h in hits if nordic_shares.root_symbol(h["symbol"]) == root]
        self.assertEqual(
            len(classes), 1,
            "NIBE is expected to show exactly one LISTED class (its A share "
            "is unlisted); if this now shows two, NIBE may have listed the A "
            "share and the uncertainty flag should be revisited, not just "
            "this test.")


if __name__ == "__main__":
    unittest.main()
