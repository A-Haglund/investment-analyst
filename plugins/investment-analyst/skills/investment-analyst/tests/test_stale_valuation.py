#!/usr/bin/env python3
"""Group 2 (spec §36) - the stale-valuation regression.

The named bug class: a current price divided by financial results that are
roughly 20 months old, presented as if it were a current multiple with no
staleness label attached. valuation_gate.py's own docstring names the exact
motivating case: "Sandvik's latest structured earnings (ESEF, FY2024-12-31)
are twenty months old while its price is live."

valuation_gate.py was being written by a parallel agent when this suite was
first built and did not exist yet; every test below still degrades to a
clearly-explained skip if the file is absent or fails to import, per the
task's own instruction ("import it inside try/except and skip with a clear
message if absent, so the suite runs today"). Once it exists, the real,
documented API (`gate` / `gate_detail`, from its own module docstring) is
exercised directly rather than guessed at.
"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import SCRIPTS_DIR, load, try_load

finfact = load("finfact")
valuation_gate = try_load("valuation_gate")

TODAY = datetime.date(2026, 8, 31)


def stale_case():
    """FY2024 earnings (period end 2024-12-31, published 2025-02-20) priced
    against a 2026-08-31 quote: about 20 months between the earnings figure's
    publication and "today" - the exact shape of the named bug (and of
    valuation_gate.py's own Sandvik example)."""
    earnings = finfact.FinancialFact(
        "net_income", 100.0, "esef", "2024-12-31",
        publication_date="2025-02-20", freshness_key="annual_financials",
        currency="SEK")
    price = finfact.FinancialFact(
        "price", 250.0, "nasdaq_reference", TODAY.isoformat(),
        publication_date=TODAY.isoformat(), freshness_key="price",
        currency="SEK")
    shares = finfact.FinancialFact(
        "shares_outstanding", 1000000.0, "nasdaq_reference", TODAY.isoformat(),
        publication_date=TODAY.isoformat(), note="diluted_weighted_average",
        verification=finfact.Verification.VERIFIED)
    return price, earnings, shares


def fresh_case():
    """A positive control: recent interim earnings against a live price, both
    in the same currency, with a cross-verified share count. This must PASS -
    otherwise "always refuses" would be indistinguishable from "correctly
    refuses the stale case", and the gate would be worthless."""
    earnings = finfact.FinancialFact(
        "net_income", 100.0, "esef", "2026-06-30",
        publication_date="2026-07-20", freshness_key="interim_financials",
        currency="SEK")
    price = finfact.FinancialFact(
        "price", 250.0, "nasdaq_reference", TODAY.isoformat(),
        publication_date=TODAY.isoformat(), freshness_key="price",
        currency="SEK")
    shares = finfact.FinancialFact(
        "shares_outstanding", 1000000.0, "nasdaq_reference", TODAY.isoformat(),
        publication_date=TODAY.isoformat(), note="diluted_weighted_average",
        verification=finfact.Verification.VERIFIED)
    return price, earnings, shares


class StaleCaseIsActuallyStale(unittest.TestCase):
    """Always runs, regardless of valuation_gate.py's existence: proves the
    constructed regression case is genuinely the bug shape described (>20
    months of staleness on the earnings side, a fresh price on the other), so
    that a gate refusing it later is refusing something real."""

    def test_earnings_are_stale_by_finfacts_own_freshness_rule(self):
        price, earnings, _shares = stale_case()
        stale, days, limit = earnings.staleness(TODAY)
        self.assertTrue(stale, "FY2024 earnings published 2025-02-20 must be "
                               "past the %s-day annual_financials limit by "
                               "2026-08-31" % limit)
        months = days / 30.4
        self.assertGreaterEqual(months, 18)
        self.assertLessEqual(months, 24)

    def test_price_side_of_the_same_case_is_fresh(self):
        price, _earnings, _shares = stale_case()
        self.assertFalse(price.staleness(TODAY)[0])

    def test_naive_pe_would_silently_mix_two_periods(self):
        """What the bug looks like without a gate: nothing stops a caller
        from dividing today's price by a >20-month-old earnings figure and
        calling the result a P/E. This documents that finfact.py alone does
        not prevent the division - that is valuation_gate.py's job, tested
        below."""
        price, earnings, _shares = stale_case()
        naive_pe = price.value / earnings.value
        self.assertIsInstance(naive_pe, float)  # nothing raised - no gate yet


@unittest.skipIf(
    valuation_gate is None,
    "valuation_gate.py not found (or failed to import) under %s - it is "
    "being written by a parallel agent. Skipping the refusal tests; "
    "StaleCaseIsActuallyStale above still runs and still needs to pass."
    % SCRIPTS_DIR)
class ValuationGateRefusesTheStaleCase(unittest.TestCase):
    """Drives valuation_gate.py's real, documented API:

        passed, states, report = gate(price_fact, earnings_fact, shares_fact,
                                      as_of=..., metric_name=...)

    straight from its own module docstring."""

    def test_gate_refuses_current_price_over_stale_earnings(self):
        price, earnings, shares = stale_case()
        passed, states, report = valuation_gate.gate(
            price, earnings, shares, as_of=TODAY, metric_name="P/E")
        self.assertFalse(
            passed, "a >20-month-old earnings figure priced against a "
                    "current quote must be refused:\n%s" % report)
        self.assertIn(finfact.State.DATA_STALE, states)
        self.assertIn("VALUATION INTEGRITY: FAILED", report)

    def test_the_failure_is_specifically_the_period_lag_check(self):
        """Not just "some check failed" - the check that exists precisely
        for this bug (period_lag, comparing the earnings period end against
        the price date) must be the one that fires."""
        price, earnings, shares = stale_case()
        passed, _states, _report, results = valuation_gate.gate_detail(
            price, earnings, shares, as_of=TODAY, metric_name="P/E")
        by_check = {r["check"]: r for r in results}
        self.assertIn("period_lag", by_check)
        self.assertEqual(by_check["period_lag"]["status"], "FAIL")
        self.assertIn("days before the price date", by_check["period_lag"]["detail"])

    def test_gate_does_not_always_refuse_a_genuinely_fresh_case(self):
        """Positive control: a gate that fails everything would trivially
        "pass" the test above for the wrong reason. Recent interim earnings
        against a live price, same currency, cross-verified share count must
        come back PASSED."""
        price, earnings, shares = fresh_case()
        passed, states, report = valuation_gate.gate(
            price, earnings, shares, as_of=TODAY, metric_name="P/E")
        self.assertTrue(passed, "a fresh, well-formed case must pass:\n%s" % report)
        self.assertEqual(states, [])


if __name__ == "__main__":
    unittest.main()
