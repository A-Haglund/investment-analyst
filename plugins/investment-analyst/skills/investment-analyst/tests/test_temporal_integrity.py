#!/usr/bin/env python3
"""Group 1 (spec §35) - temporal integrity. The highest priority group.

finfact.py is the trust boundary: every number that reaches a valuation
carries a source, a period and a retrieval date, and is-available-as-of() is
the hard gate that keeps tomorrow's filing out of yesterday's analysis. All
offline, all fast - this must run every time, with no network.
"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

finfact = load("finfact")


class ProvenanceIsMandatory(unittest.TestCase):
    """A bare float never enters a valuation: source, period and metric are
    all required at construction time, not asserted later by convention."""

    def test_missing_period_end_raises(self):
        with self.assertRaises(ValueError):
            finfact.FinancialFact("revenue", 100, "esef", None)

    def test_unknown_source_raises(self):
        with self.assertRaises(ValueError):
            finfact.FinancialFact("revenue", 100, "a_source_nobody_registered",
                                  "2024-12-31")

    def test_missing_metric_name_raises(self):
        with self.assertRaises(ValueError):
            finfact.FinancialFact("", 100, "esef", "2024-12-31")


class PublicationDateGate(unittest.TestCase):
    """The named case: a fact published 2024-02-20 must be rejected when the
    analysis date (as_of) is 2024-01-15 - the fact did not yet exist."""

    def test_fact_published_after_as_of_is_rejected(self):
        fact = finfact.FinancialFact(
            "revenue", 100, "esef", "2023-12-31",
            publication_date="2024-02-20", freshness_key="annual_financials")
        available, state = fact.is_available_as_of("2024-01-15")
        self.assertFalse(available)
        # "not yet published" is a distinct, milder condition than "cannot be
        # placed in time at all" - it must not be confused with the undated case.
        self.assertEqual(state, finfact.State.OK)

    def test_same_fact_is_available_once_as_of_reaches_publication(self):
        fact = finfact.FinancialFact(
            "revenue", 100, "esef", "2023-12-31",
            publication_date="2024-02-20", freshness_key="annual_financials")
        self.assertTrue(fact.is_available_as_of("2024-02-20")[0])
        self.assertTrue(fact.is_available_as_of("2024-06-01")[0])
        self.assertFalse(fact.is_available_as_of("2024-02-19")[0])

    def test_undated_fact_can_never_be_asserted_historically_available(self):
        """A fact with no publication date can never be claimed to have been
        historically available - absence of a date is not evidence of an
        early date, however far in the future as_of is set."""
        undated = finfact.FinancialFact("revenue", 100, "esef", "2023-12-31")
        available, state = undated.is_available_as_of("2099-01-01")
        self.assertFalse(available)
        self.assertEqual(state, finfact.State.POINT_IN_TIME_UNVERIFIED)


class FilterAsOfRespectsMode(unittest.TestCase):
    """filter_as_of() is where the gate actually gets applied to a batch of
    facts, and Mode.HINDSIGHT must allow exactly what Mode.HISTORICAL refuses,
    while Mode.CURRENT never filters at all."""

    def setUp(self):
        self.future_published = finfact.FinancialFact(
            "revenue", 100, "esef", "2023-12-31",
            publication_date="2024-02-20", freshness_key="annual_financials")
        self.undated = finfact.FinancialFact("revenue", 100, "esef", "2023-12-31")

    def test_historical_mode_rejects_not_yet_published_fact(self):
        kept, rejected = finfact.filter_as_of(
            [self.future_published], "2024-01-15", finfact.Mode.HISTORICAL)
        self.assertEqual(kept, [])
        self.assertEqual(len(rejected), 1)
        fact, state, reason = rejected[0]
        self.assertIs(fact, self.future_published)
        self.assertIn("2024-02-20", reason)
        self.assertIn("2024-01-15", reason)

    def test_historical_mode_rejects_undated_fact_with_distinct_reason(self):
        kept, rejected = finfact.filter_as_of(
            [self.undated], "2024-01-15", finfact.Mode.HISTORICAL)
        self.assertEqual(kept, [])
        self.assertEqual(rejected[0][1], finfact.State.POINT_IN_TIME_UNVERIFIED)
        self.assertIn("no publication date", rejected[0][2])

    def test_hindsight_allows_what_historical_refuses(self):
        kept_hist, rejected_hist = finfact.filter_as_of(
            [self.future_published], "2024-01-15", finfact.Mode.HISTORICAL)
        kept_hind, rejected_hind = finfact.filter_as_of(
            [self.future_published], "2024-01-15", finfact.Mode.HINDSIGHT)
        self.assertEqual(kept_hist, [])
        self.assertEqual(len(rejected_hist), 1)
        self.assertEqual(kept_hind, [self.future_published])
        self.assertEqual(rejected_hind, [])

    def test_current_mode_never_filters_regardless_of_as_of(self):
        kept, rejected = finfact.filter_as_of(
            [self.future_published, self.undated], "1999-01-01", finfact.Mode.CURRENT)
        self.assertEqual(kept, [self.future_published, self.undated])
        self.assertEqual(rejected, [])


class CorroborationCountsOriginsNotSources(unittest.TestCase):
    """corroborate() is the guard against the most flattering mistake this
    system could make: a company's IR page, its MFN release and its Cision
    release are one disclosure carried by three channels, and two sources
    sharing an origin_group must NOT count as independent verification."""

    def test_two_sources_sharing_origin_group_do_not_verify_each_other(self):
        # esef and annual_report both map to origin_group "issuer_filing".
        esef_fact = finfact.FinancialFact(
            "revenue", 100, "esef", "2024-12-31", publication_date="2025-02-20")
        annual_report_fact = finfact.FinancialFact(
            "revenue", 100, "annual_report", "2024-12-31",
            publication_date="2025-03-10")
        self.assertEqual(esef_fact.origin_group, annual_report_fact.origin_group)

        verification, detail = finfact.corroborate([esef_fact, annual_report_fact])
        self.assertEqual(detail["independent_origins"], 1)
        self.assertEqual(verification, finfact.Verification.CROSS_CHECKED)
        self.assertNotEqual(verification, finfact.Verification.VERIFIED)

    def test_two_sources_with_distinct_origin_groups_do_verify(self):
        esef_fact = finfact.FinancialFact(
            "revenue", 100, "esef", "2024-12-31", publication_date="2025-02-20")
        mfn_fact = finfact.FinancialFact(
            "revenue", 100, "mfn", "2024-12-31", publication_date="2025-02-20")
        self.assertNotEqual(esef_fact.origin_group, mfn_fact.origin_group)

        verification, detail = finfact.corroborate([esef_fact, mfn_fact])
        self.assertEqual(detail["independent_origins"], 2)
        self.assertEqual(verification, finfact.Verification.VERIFIED)

    def test_three_channels_of_one_issuer_disclosure_still_count_as_one_origin(self):
        """company_ir, mfn and cision are exactly the three-channels-one-fact
        case called out in finfact.py's own docstring: all three map to
        origin_group "issuer_disclosure"."""
        ir = finfact.FinancialFact("revenue", 100, "company_ir", "2024-12-31",
                                   publication_date="2025-02-20")
        mfn_fact = finfact.FinancialFact("revenue", 100, "mfn", "2024-12-31",
                                         publication_date="2025-02-20")
        cision_fact = finfact.FinancialFact("revenue", 100, "cision", "2024-12-31",
                                            publication_date="2025-02-20")
        verification, detail = finfact.corroborate([ir, mfn_fact, cision_fact])
        self.assertEqual(detail["independent_origins"], 1)
        self.assertNotEqual(verification, finfact.Verification.VERIFIED)

    def test_disagreement_beyond_tolerance_is_conflict_even_across_origins(self):
        esef_fact = finfact.FinancialFact(
            "revenue", 100, "esef", "2024-12-31", publication_date="2025-02-20")
        mfn_fact = finfact.FinancialFact(
            "revenue", 130, "mfn", "2024-12-31", publication_date="2025-02-20")
        verification, _ = finfact.corroborate([esef_fact, mfn_fact])
        self.assertEqual(verification, finfact.Verification.CONFLICT)

    def test_single_source_is_neither_verified_nor_cross_checked(self):
        esef_fact = finfact.FinancialFact(
            "revenue", 100, "esef", "2024-12-31", publication_date="2025-02-20")
        verification, _ = finfact.corroborate([esef_fact])
        self.assertEqual(verification, finfact.Verification.SINGLE_SOURCE)


class ZeroValueMustNotCorroborate(unittest.TestCase):
    """corroborate()'s spread denominator is max(abs(v) for v in values), so
    a zero-valued fact never silently divides the spread down to nothing.

    Old behaviour: the denominator was abs(min(values)). When one of the two
    values IS the minimum and is exactly 0.0, that denominator is 0, and
    (with the `if denom else 0.0` guard already in place) spread comes back
    0.0 regardless of how large the other value actually is - a fact of 0.0
    and a fact of 5,000,000 from two independent origins would then read as
    perfect agreement and verify each other, which is the exact opposite of
    what happened: one origin reported no evidence of the number at all.
    """

    def test_zero_versus_a_large_value_is_conflict_not_verified(self):
        zero_fact = finfact.FinancialFact(
            "revenue", 0.0, "esef", "2024-12-31", publication_date="2025-02-20")
        big_fact = finfact.FinancialFact(
            "revenue", 5000000.0, "mfn", "2024-12-31", publication_date="2025-02-20")
        verification, detail = finfact.corroborate([zero_fact, big_fact])
        self.assertNotEqual(verification, finfact.Verification.VERIFIED,
                            "a 0.0 fact must never corroborate a wildly "
                            "different independent value:\n%r" % (detail,))
        self.assertEqual(verification, finfact.Verification.CONFLICT)
        self.assertGreater(detail["spread"], 0.5)

    def test_two_independent_facts_that_genuinely_agree_at_zero_still_verify(self):
        """Control: this is not "corroborate() must reject zero" - two
        independent origins that both genuinely report 0.0 (e.g. a
        discontinued-operations line with nothing to report) must still
        agree with each other."""
        zero_a = finfact.FinancialFact(
            "revenue", 0.0, "esef", "2024-12-31", publication_date="2025-02-20")
        zero_b = finfact.FinancialFact(
            "revenue", 0.0, "mfn", "2024-12-31", publication_date="2025-02-20")
        verification, detail = finfact.corroborate([zero_a, zero_b])
        self.assertEqual(verification, finfact.Verification.VERIFIED)
        self.assertEqual(detail["spread"], 0.0)

    def test_an_ordinary_agreeing_nonzero_pair_is_unaffected(self):
        """Control: the denominator fix must not change the ordinary case -
        two independent, non-zero, closely-agreeing values still verify."""
        a = finfact.FinancialFact(
            "revenue", 100.0, "esef", "2024-12-31", publication_date="2025-02-20")
        b = finfact.FinancialFact(
            "revenue", 100.5, "mfn", "2024-12-31", publication_date="2025-02-20")
        verification, detail = finfact.corroborate([a, b])
        self.assertEqual(verification, finfact.Verification.VERIFIED)
        self.assertLess(detail["spread"], 0.01)


class ModeGuarantee(unittest.TestCase):
    """Spec §34: HISTORICAL, CURRENT and HINDSIGHT are never mixed."""

    def test_the_three_modes_exist_and_are_distinct(self):
        self.assertEqual(len({finfact.Mode.CURRENT, finfact.Mode.HISTORICAL,
                              finfact.Mode.HINDSIGHT}), 3)


if __name__ == "__main__":
    unittest.main()
