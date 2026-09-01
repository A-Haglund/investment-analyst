#!/usr/bin/env python3
"""Partial share-class sums must be flagged, never presented as a confident
total.

Both valuation_gate.py and share_semantics.py sum a company's listed share
classes (VOLV A + VOLV B, etc.). When Nasdaq's per-class summary() lookup
fails for ONE class - a bad orderbookId, a transient error - the old
behaviour in both files was to silently sum only the classes that DID come
back, while continuing to describe the total as if every class had been
counted ("2 listed class(es) summed" when only 1 actually contributed a
number). That understates the true total with no visible sign of it: for a
Volvo-shaped case, a failed A-line fetch would have reported roughly 1.59bn
shares as the "all-class total" instead of the true ~2.03bn.

test_multi_share_class.py's existing offline aggregation tests always supply
a share count for every class, which is exactly why this bug was missed
there - it only shows up when one class's count is None/0/missing.

All offline: nordic_shares.py and company_resolve.py are monkeypatched (or,
for valuation_gate, forced onto its raw-Nasdaq-search fallback path) so no
network call is made anywhere in this file.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

share_semantics = load("share_semantics")
valuation_gate = load("valuation_gate")


class ShareSemanticsPartialSum(unittest.TestCase):
    """listed_shares_fact() is a pure function - no monkeypatching needed."""

    def test_a_missing_class_count_downgrades_to_incomplete(self):
        classes = [{"symbol": "TEST A", "shares": 600000000},
                  {"symbol": "TEST B", "shares": None}]
        fact, n_classes, missing = share_semantics.listed_shares_fact(classes)
        self.assertIsNotNone(fact)
        self.assertEqual(n_classes, 2)
        self.assertEqual(missing, ["TEST B"])
        self.assertEqual(fact.verification, share_semantics.Verification.INCOMPLETE,
                         "a partial sum must not be reported as SINGLE_SOURCE "
                         "or any other confident verification level")
        self.assertEqual(fact.value, 600000000,
                         "the total must be the sum of the classes that "
                         "actually had a count, not silently zero-filled")
        self.assertIn("1 of 2", fact.source_detail)
        self.assertIn("TEST B", fact.source_detail,
                      "source_detail must name which class is missing, not "
                      "just say how many classes were summed")
        self.assertNotIn("2 listed class(es): TEST A, TEST B", fact.source_detail,
                         "must not claim every class was summed when one "
                         "contributed no count")

    def test_every_class_present_is_reported_as_a_complete_sum(self):
        """Control: the ordinary case (every class has a count) must still
        report SINGLE_SOURCE, not INCOMPLETE."""
        classes = [{"symbol": "TEST A", "shares": 600000000},
                  {"symbol": "TEST B", "shares": 1400000000}]
        fact, n_classes, missing = share_semantics.listed_shares_fact(classes)
        self.assertEqual(missing, [])
        self.assertEqual(fact.verification, share_semantics.Verification.SINGLE_SOURCE)
        self.assertEqual(fact.value, 2000000000)


class ValuationGatePartialSum(unittest.TestCase):
    """_gather_nordic()'s fallback path (used when company_resolve.py does
    not cleanly resolve the name) sums nordic_shares.summary() across every
    listed class of the issuer. Forcing company_resolve to fail (returning
    None) exercises this fallback directly and offline."""

    def setUp(self):
        self._real_run_company_resolve = valuation_gate._run_company_resolve
        self._real_quote = valuation_gate.quote
        self._real_corporate_actions = valuation_gate.corporate_actions
        self._real_search = valuation_gate.nordic_shares.search
        self._real_summary = valuation_gate.nordic_shares.summary
        # No identity resolver, no price lookup, no corporate-actions lookup -
        # this test is only about the share-count aggregation, and none of
        # those three make network calls once disabled this way.
        valuation_gate._run_company_resolve = lambda name, timeout=100: None
        valuation_gate.quote = None
        valuation_gate.corporate_actions = None

    def tearDown(self):
        valuation_gate._run_company_resolve = self._real_run_company_resolve
        valuation_gate.quote = self._real_quote
        valuation_gate.corporate_actions = self._real_corporate_actions
        valuation_gate.nordic_shares.search = self._real_search
        valuation_gate.nordic_shares.summary = self._real_summary

    def test_a_failed_class_lookup_yields_incomplete_not_a_confident_total(self):
        valuation_gate.nordic_shares.search = lambda text: [
            {"name": "Test Group A", "symbol": "TEST A", "currency": "SEK",
             "orderbookId": 1},
            {"name": "Test Group A", "symbol": "TEST B", "currency": "SEK",
             "orderbookId": 2},
        ]
        valuation_gate.nordic_shares.summary = (
            lambda ob: {"shares": 600000000} if ob == 1 else {"shares": None})

        bundle, notes = valuation_gate._gather_nordic("Test Group A")
        shares_fact = bundle["shares_fact"]
        self.assertTrue(
            shares_fact is None
            or shares_fact.verification == valuation_gate.Verification.INCOMPLETE,
            "a partial class sum must be absent or explicitly INCOMPLETE, "
            "never a plain SINGLE_SOURCE total")
        self.assertIsNotNone(shares_fact)
        self.assertEqual(shares_fact.value, 600000000)
        self.assertIn("1 of 2", shares_fact.source_detail)
        self.assertIn("missing", shares_fact.source_detail.lower())
        self.assertNotEqual(
            shares_fact.source_detail,
            "Nasdaq Nordic reference data, 2 listed class(es) summed",
            "must not use the unqualified 'N listed class(es) summed' "
            "wording that claims every class was counted")
        self.assertTrue(
            any("PARTIAL" in n for n in notes),
            "a partial sum must be called out in the CLI notes too, not "
            "just buried in source_detail: %r" % (notes,))

    def test_every_class_present_is_a_complete_sum(self):
        """Control: no missing class, no INCOMPLETE flag."""
        valuation_gate.nordic_shares.search = lambda text: [
            {"name": "Test Group A", "symbol": "TEST A", "currency": "SEK",
             "orderbookId": 1},
            {"name": "Test Group A", "symbol": "TEST B", "currency": "SEK",
             "orderbookId": 2},
        ]
        valuation_gate.nordic_shares.summary = (
            lambda ob: {"shares": 600000000} if ob == 1 else {"shares": 1400000000})

        bundle, _notes = valuation_gate._gather_nordic("Test Group A")
        shares_fact = bundle["shares_fact"]
        self.assertIsNotNone(shares_fact)
        self.assertEqual(shares_fact.value, 2000000000)
        self.assertNotEqual(shares_fact.verification, valuation_gate.Verification.INCOMPLETE)


if __name__ == "__main__":
    unittest.main()
