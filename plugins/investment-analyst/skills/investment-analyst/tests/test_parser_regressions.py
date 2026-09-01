#!/usr/bin/env python3
"""Group 6 - parser regressions: the bugs already found, in mfn_news.py.

Four distinct, previously-shipped errors, each with a comment in mfn_news.py
explaining exactly how it happened:

  1. English thousands separator misread as a Nordic decimal comma (or vice
     versa) - a 1000x error that looks entirely plausible on the page.
  2. The minus sign on a Nordic-formatted negative ("- 11 471") swallowed by
     a greedy label match, turning a cash outflow into an inflow.
  3. A quarter and a half-year sharing an identical line label ("Net sales")
     inside the same release, with no period attached to tell them apart.

All pure regex/string logic - offline, fast, deterministic.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

mfn = load("mfn_news")


class ToNumberSeparatorHandling(unittest.TestCase):
    """THOUSANDS_COMMA in mfn_news.py distinguishes 'comma followed by groups
    of exactly three digits' (English thousands) from 'comma followed by one
    or two digits' (Nordic decimal). Getting this backwards is the named
    1000x bug."""

    def test_english_thousands_separator(self):
        self.assertEqual(mfn.to_number("24,297"), 24297.0)

    def test_nordic_negative_decimal_comma(self):
        self.assertEqual(mfn.to_number("-0,05"), -0.05)

    def test_nordic_positive_decimal_comma(self):
        self.assertEqual(mfn.to_number("3,5"), 3.5)

    def test_multiple_thousands_groups(self):
        self.assertEqual(mfn.to_number("1,234,567"), 1234567.0)

    def test_negative_thousands_separator(self):
        self.assertEqual(mfn.to_number("-24,297"), -24297.0)

    def test_space_and_nbsp_thousands_are_stripped(self):
        self.assertEqual(mfn.to_number("24 297"), 24297.0)
        self.assertEqual(mfn.to_number("24 297"), 24297.0)

    def test_none_and_unparseable_input(self):
        self.assertIsNone(mfn.to_number(None))
        self.assertIsNone(mfn.to_number("n/a"))
        self.assertIsNone(mfn.to_number(""))


class ExtractFiguresSignPreservation(unittest.TestCase):
    """The named bug: 'Cash flow from operations, - 11 471 (12 345)' - Nordic
    releases put a space after the minus sign, and a non-greedy label match
    that does not allow for it swallows the sign, turning a cash outflow into
    an inflow with a correct-looking citation."""

    def test_space_separated_minus_sign_is_kept(self):
        text = "Cash flow from operations, - 11 471 (12 345)"
        figs = mfn.extract_figures(text)
        self.assertEqual(len(figs), 1)
        self.assertEqual(figs[0]["current"], -11471.0)
        self.assertGreater(figs[0]["previous"], 0)

    def test_ordinary_negative_without_space_is_also_kept(self):
        text = "Adjusted EBIT margin, -3.2% (1.1%)"
        figs = mfn.extract_figures(text)
        self.assertEqual(len(figs), 1)
        self.assertEqual(figs[0]["current"], -3.2)

    def test_bracket_holding_a_margin_is_not_read_as_the_comparative(self):
        """'Adjusted net profit, -3 798 (-13%)' - the parenthesis holds a
        margin, not last year's figure. Treating it as the comparative would
        invert the apparent trend."""
        text = "Adjusted net profit, -3 798 (-13%)"
        figs = mfn.extract_figures(text)
        self.assertEqual(len(figs), 1)
        fig = figs[0]
        self.assertEqual(fig["current"], -3798.0)
        self.assertIsNone(fig["previous"])
        self.assertEqual(fig["pct_in_brackets"], -13.0)


class ExtractFiguresPrefixScaleToken(unittest.TestCase):
    """FIGURE_RE used to support a scale token (MSEK/KSEK/...) only as a
    SUFFIX after the number, plus a bare currency prefix. The large-cap
    Swedish style puts the scale BEFORE the number instead - 'MSEK 104 435
    (112 047)' - which used to parse as the unscaled, unlabelled 104435 and
    then produce a spurious MISMATCH against the (correctly scaled) ESEF
    figure in verify_filing.py."""

    def test_prefix_scale_token_is_recognised_and_applied(self):
        text = "Net sales MSEK 104 435 (112 047)"
        figs = mfn.extract_figures(text)
        self.assertEqual(len(figs), 1)
        fig = figs[0]
        self.assertEqual(fig["unit"], "SEK")
        self.assertEqual(fig["current"], 104435e6)
        self.assertEqual(fig["previous"], 112047e6)

    def test_suffix_scale_token_still_wins_over_a_bare_prefix_currency(self):
        """'SEK 24,297 thousand (23,000 thousand)' must still resolve to SEK,
        not fall through to '?' now that a prefix scale group also exists."""
        text = "Revenue SEK 24,297 thousand (23,000 thousand)"
        figs = mfn.extract_figures(text)
        self.assertEqual(len(figs), 1)
        fig = figs[0]
        self.assertEqual(fig["unit"], "SEK")
        self.assertEqual(fig["current"], 24297e3)


class ExtractFiguresPeriodDisambiguation(unittest.TestCase):
    """KebNi Q2 2026: 'Net sales 28,838' (the quarter) and 'Net sales 41,881'
    (year-to-date) appear under an IDENTICAL label in the same release.
    Without capturing the heading each bullet sits under, picking the wrong
    one is a 45% revenue error with a correct-looking citation."""

    def test_quarter_and_half_year_figures_get_distinct_periods(self):
        text = (
            "Financial development Apr-Jun 2026 (KSEK)\n"
            "- Net sales 28,838 (25 000)\n"
            "\n"
            "Financial development Jan-Jun 2026 (KSEK)\n"
            "- Net sales 41,881 (35 000)\n"
        )
        figs = mfn.extract_figures(text)
        sales = [f for f in figs if f["label"].lower() == "net sales"]
        self.assertEqual(len(sales), 2, "expected exactly one 'Net sales' "
                         "figure per heading, got %r" % (figs,))

        periods = [f["period"] for f in sales]
        self.assertTrue(all(periods), "both figures must carry a period, "
                        "not fall back to None")
        self.assertEqual(len(set(periods)), 2,
                         "the quarter and the half-year must not collapse "
                         "onto the same period label")
        self.assertNotEqual(sales[0]["current"], sales[1]["current"])

    def test_period_context_survives_a_blank_line_gap(self):
        """reflow() drops blank lines rather than treating them as a period
        reset, so a heading's scope must extend across the blank line to the
        bullet beneath it."""
        text = (
            "Q2 2026\n"
            "\n"
            "- Net sales 100 (90)\n"
        )
        figs = mfn.extract_figures(text)
        self.assertEqual(len(figs), 1)
        self.assertIsNotNone(figs[0]["period"])


if __name__ == "__main__":
    unittest.main()
