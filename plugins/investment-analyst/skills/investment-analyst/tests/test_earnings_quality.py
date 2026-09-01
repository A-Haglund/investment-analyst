#!/usr/bin/env python3
"""earnings_quality.py - no cash-conversion flag on a loss-making window.

check_conversion() computes FCF/NI (free cash flow over net income) averaged
over a multi-year window and flags anything below 60% as weak conversion.
When the window's summed net income is negative, that ratio is not a
conversion rate at all - dividing a (possibly also negative, possibly
positive) FCF figure by a negative denominator produces a number that reads
exactly like a real shortfall but means nothing. Old behaviour: the "FCF/NI
below 60%" flag fired anyway, with wording that reads as a genuine
cash-conversion problem for what is simply a loss-making window - see
check_negative_fcf() for the check this situation should route to instead.

All offline: FinancialFact objects are constructed directly with synthetic
values, no network call anywhere in this file.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

earnings_quality = load("earnings_quality")
finfact = load("finfact")


def mk(metric, value, period_end):
    return finfact.FinancialFact(
        metric, value, "esef", period_end, publication_date=period_end,
        freshness_key="annual_financials", currency="SEK",
        source_detail="test filing")


class NoConversionFlagOnALossMakingWindow(unittest.TestCase):
    def test_negative_summed_net_income_suppresses_the_flag(self):
        # FCF/NI ratio itself is a weak 30% - the shape that would normally
        # flag - but the three years behind it sum to a net LOSS.
        w3 = mk("fcf_ni", 0.30, "2025-12-31")
        ni_facts = {
            "2023-12-31": mk("net_income", -50e6, "2023-12-31"),
            "2024-12-31": mk("net_income", -20e6, "2024-12-31"),
            "2025-12-31": mk("net_income", -10e6, "2025-12-31"),
        }
        flag, reason = earnings_quality.check_conversion(
            {3: w3}, {}, {}, "SEK", ni_facts=ni_facts)
        self.assertIsNone(flag, "a loss-making window must not raise the "
                          "FCF/NI conversion flag")
        self.assertIsNotNone(reason)
        self.assertIn("LOSS", reason.upper())
        self.assertNotIn("weak conversion", reason.lower(),
                         "the given reason must not itself read as a "
                         "conversion-shortfall finding for what is simply a "
                         "loss-making window")

    def test_positive_net_income_with_weak_conversion_still_flags(self):
        """Control: the fix must not blanket-suppress the flag - a
        genuinely profitable window with weak cash conversion must still be
        raised."""
        w3 = mk("fcf_ni", 0.30, "2025-12-31")
        ni_facts = {
            "2023-12-31": mk("net_income", 50e6, "2023-12-31"),
            "2024-12-31": mk("net_income", 20e6, "2024-12-31"),
            "2025-12-31": mk("net_income", 10e6, "2025-12-31"),
        }
        flag, reason = earnings_quality.check_conversion(
            {3: w3}, {}, {}, "SEK", ni_facts=ni_facts)
        self.assertIsNotNone(flag, "a genuinely weak conversion in a "
                             "profitable window must still be flagged")
        self.assertIsNone(reason)
        self.assertEqual(flag["status"], "UNRESOLVED")

    def test_a_healthy_conversion_ratio_never_flags_regardless_of_income_sign(self):
        """Control: reference.value >= 0.60 must short-circuit before the
        loss check even runs - this must keep working."""
        w3 = mk("fcf_ni", 0.75, "2025-12-31")
        ni_facts = {
            "2023-12-31": mk("net_income", 50e6, "2023-12-31"),
            "2024-12-31": mk("net_income", 20e6, "2024-12-31"),
            "2025-12-31": mk("net_income", 10e6, "2025-12-31"),
        }
        flag, _reason = earnings_quality.check_conversion(
            {3: w3}, {}, {}, "SEK", ni_facts=ni_facts)
        self.assertIsNone(flag)


if __name__ == "__main__":
    unittest.main()
