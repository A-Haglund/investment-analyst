#!/usr/bin/env python3
"""thesis_ledger.py - a persistence breaker must not fire across a hole in
the data.

The named bug: three breaching observations at 2025-03-31, 2025-06-30 and
2025-12-31, with 2025-09-30 absent, evaluated against a breaker requiring
three CONSECUTIVE breaching quarters. Old behaviour: evaluate_clause()
counted breaching entries by position in the sorted period list, with no
check that each pair of neighbours was actually adjacent in time - three
breaching quarters with a missing quarter between two of them counted as a
run of 3, firing TRIGGERED (action EXIT_OR_REUNDERWRITE upstream) over a
gap in the record, not a genuine three-quarter deterioration.

The fix (_gap_ok, checked between every consecutive pair while counting the
run) requires the run to actually be date-adjacent, not merely
list-adjacent.

All offline: a minimal duck-typed ctx supplies evaluate_clause()'s facts
directly, so no network call and no live ledger are involved.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

thesis_ledger = load("thesis_ledger")

BREAKER_TEXT = "ebit_margin < 15% for 3 consecutive quarters"


class _FakeCtx(object):
    """Duck-types the one method evaluate_clause() calls on its ctx:
    series(metric) -> (basis, facts, reason, provenance)."""

    def __init__(self, facts, basis="quarterly"):
        self.facts = facts
        self.basis = basis

    def series(self, mid):
        return self.basis, self.facts, None, "reported"


def make_facts(pairs):
    out = {}
    for period_end, value in pairs:
        out[period_end] = thesis_ledger.mk_fact(
            metric="ebit_margin", value=value, source="esef",
            period_end=period_end, publication_date=period_end,
            freshness_key="annual_financials")
    return out


class PersistenceRequiresActualAdjacency(unittest.TestCase):
    def setUp(self):
        self.clause = thesis_ledger.parse_breaker(BREAKER_TEXT).clauses[0]

    def test_three_breaches_with_a_missing_quarter_do_not_trigger(self):
        """2025-09-30 is absent: the run from the latest period (2025-12-31)
        back to the next available one (2025-06-30) spans 184 days, nowhere
        near a genuine quarter-to-quarter gap (75-110 days), so _gap_ok must
        stop the run there. Only 1 consecutive breach should be counted, not
        3 - and the outcome must NOT be TRIGGERED."""
        facts = make_facts([("2025-03-31", 10.0), ("2025-06-30", 10.0),
                            ("2025-12-31", 10.0)])
        out = thesis_ledger.evaluate_clause(self.clause, _FakeCtx(facts))
        self.assertNotEqual(
            out["outcome"], thesis_ledger.TRIGGERED,
            "a breaker must not fire across a hole in the data:\n%s"
            % out.get("reason"))
        self.assertEqual(out["consecutive_breaches"], 1)

    def test_four_genuinely_consecutive_breaching_quarters_do_trigger(self):
        """Control: with no gap, a real 3+-quarter run against a
        persistence-3 breaker must still fire TRIGGERED - otherwise "never
        triggers across a hole" would be indistinguishable from "never
        triggers at all"."""
        facts = make_facts([("2025-03-31", 10.0), ("2025-06-30", 10.0),
                            ("2025-09-30", 10.0), ("2025-12-31", 10.0)])
        out = thesis_ledger.evaluate_clause(self.clause, _FakeCtx(facts))
        self.assertEqual(out["outcome"], thesis_ledger.TRIGGERED, out.get("reason"))
        self.assertGreaterEqual(out["consecutive_breaches"], 3)

    def test_the_evaluate_breaker_wrapper_agrees_on_the_gapped_case(self):
        """The same case one level up, through evaluate_breaker() - the
        function thesis evaluation actually calls - so this is not only true
        of evaluate_clause() in isolation."""
        breaker = thesis_ledger.parse_breaker(BREAKER_TEXT)
        facts = make_facts([("2025-03-31", 10.0), ("2025-06-30", 10.0),
                            ("2025-12-31", 10.0)])
        result = thesis_ledger.evaluate_breaker(breaker, _FakeCtx(facts))
        self.assertNotEqual(result["outcome"], thesis_ledger.TRIGGERED, result)


if __name__ == "__main__":
    unittest.main()
