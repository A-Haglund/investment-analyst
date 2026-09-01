#!/usr/bin/env python3
"""Group 4 (spec §38) - non-calendar fiscal years.

Addtech and Lagercrantz end 31 March; H&M ends 30 November. The default that
must never happen silently is 31 December. company_resolve.py detects the
fiscal year end from the ESEF filing's own period_end (fiscal_year_end_from_esef)
or, for issuers with no ESEF filing, by reading the year-end report's own
period range out of its prose (fiscal_year_end_from_reports / month_ranges).

Both functions are pure enough to drive directly with synthetic input, so the
detection logic itself is tested offline. The network test additionally
confirms the three named real companies against live ESEF data.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load, network

cr = load("company_resolve")
esef = load("esef_fundamentals")


class FiscalYearEndFromEsefOffline(unittest.TestCase):
    def setUp(self):
        cr.WARNINGS[:] = []

    def test_march_year_end_is_detected_not_defaulted_to_december(self):
        """Addtech- and Lagercrantz-shaped input: ESEF period ends on 31 March."""
        filings = [{"period_end": "2026-03-31"}, {"period_end": "2025-03-31"}]
        fye, full = cr.fiscal_year_end_from_esef(filings)
        self.assertEqual(fye, "03-31")
        self.assertEqual(full, "2026-03-31")
        self.assertNotEqual(fye, "12-31")

    def test_november_year_end_is_detected_not_defaulted_to_december(self):
        """H&M-shaped input: ESEF period ends on 30 November."""
        filings = [{"period_end": "2025-11-30"}, {"period_end": "2024-11-30"}]
        fye, full = cr.fiscal_year_end_from_esef(filings)
        self.assertEqual(fye, "11-30")
        self.assertNotEqual(fye, "12-31")

    def test_ordinary_december_year_end_still_works(self):
        """The detector must not be biased against December either - it
        reports whatever the filing says, with no built-in assumption in
        either direction."""
        filings = [{"period_end": "2025-12-31"}]
        fye, _ = cr.fiscal_year_end_from_esef(filings)
        self.assertEqual(fye, "12-31")

    def test_no_filings_yields_unknown_not_a_default(self):
        self.assertEqual(cr.fiscal_year_end_from_esef([]), (None, None))
        self.assertEqual(cr.fiscal_year_end_from_esef(None), (None, None))

    def test_a_changed_fiscal_year_is_flagged_and_the_latest_filing_wins(self):
        filings = [{"period_end": "2026-03-31"}, {"period_end": "2024-12-31"}]
        fye, _ = cr.fiscal_year_end_from_esef(filings)
        self.assertEqual(fye, "03-31")
        self.assertTrue(any("disagree" in w for w in cr.WARNINGS),
                        "disagreeing period ends across filings must raise a "
                        "warning, not be silently averaged or overwritten")


class FiscalYearEndFromReportProseOffline(unittest.TestCase):
    """The First North / Spotlight / NGM fallback: no ESEF exists, so the
    fiscal year end is read out of the year-end report's own stated period,
    e.g. '12 MONTHS (1 April 2025 - 31 March 2026)'."""

    def test_month_ranges_reads_an_april_to_march_span(self):
        text = "12 MONTHS (1 April 2025 - 31 March 2026), unaudited figures."
        ranges = cr.month_ranges(text)
        self.assertIn((3, 31), ranges,
                      "the span must end in March, not fall through to "
                      "December by default")

    def test_month_ranges_reads_a_swedish_delarsrapport_span(self):
        text = "delarsrapport 1 april - 30 september 2025"
        ranges = cr.month_ranges(text)
        self.assertIn((9, 30), ranges)

    def test_month_ranges_ignores_an_unrelated_agm_date(self):
        """A signing date or an AGM date near the covered period must not be
        mistaken for a period boundary - month_ranges() only pairs months
        joined by a dash/'to'/'till' within a short gap."""
        text = "Annual General Meeting will be held on 20 December 2026."
        ranges = cr.month_ranges(text)
        self.assertEqual(ranges, [])


@network
class FiscalYearEndLiveEsef(unittest.TestCase):
    """Live filings.xbrl.org data for the three named companies."""

    CASES = {
        "Addtech": "03-31",
        "Lagercrantz": "03-31",
        "Hennes": "11-30",   # H&M's ESEF legal name is "H & M Hennes & Mauritz"
    }

    def test_non_calendar_fiscal_years_are_detected(self):
        for needle, expected_mmdd in self.CASES.items():
            with self.subTest(company=needle):
                hits = esef.search_index(needle, "SE")
                self.assertTrue(hits, "no ESEF filer matched %r in SE" % needle)
                hits.sort(key=lambda h: h["latest"], reverse=True)
                lei = hits[0]["lei"]
                filings = esef.list_filings(lei, limit=4)
                self.assertTrue(filings, "no ESEF filings indexed for %r" % needle)
                fye, _full = cr.fiscal_year_end_from_esef(filings)
                self.assertEqual(
                    fye, expected_mmdd,
                    "%s resolved fiscal year end %r, expected %r - must not "
                    "default to 12-31" % (needle, fye, expected_mmdd))


if __name__ == "__main__":
    unittest.main()
