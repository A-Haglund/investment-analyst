#!/usr/bin/env python3
"""ownership_se.py - three verified defects in the Swedish fund-ownership tool.

All offline: the FI archive is either constructed in memory (a real zip built
with zipfile.ZipFile over io.BytesIO, or written to a throwaway temp file for
build_index()) or load_index() is monkeypatched with a canned index - no
network call is made anywhere in this file.

Bug 1 (--name never resolves, and falsely refuses two share classes of one
issuer): FI's holdings file carries a company's bonds under the same issuer
name as its equity (Antal/shares empty, Nominellt_belopp populated instead),
and a single issuer routinely lists more than one share class. The old code
counted every raw ISIN match - bonds and both classes included - and refused
whenever more than one turned up, so `--name "Sandvik"` (1 equity + 2 bonds)
and `--name "Atlas Copco"` (A, B, and a bond) both refused, and every --name
example in the module's own docstring failed. resolve_name() restricts to
equity (a non-empty share count on some row) and groups survivors by issuer
stem, refusing only when more than one distinct issuer survives.

Bug 2 (hardcoded staleness claim): the tail of the report used to assert the
data "is already weeks old" unconditionally. data_age_days() computes the
actual age from the quarter end instead.

Bug 3 (conviction list sorted by the wrong key): the "high-conviction
positions" list showed the largest holders BY VALUE under a heading about NAV
weight. It must be sorted by pct_of_fund.

Every test below is written against the NEW module and fails on the OLD
ownership_se.py - see each class docstring for how.
"""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

m = load("ownership_se")


def mk(instrument, isin, shares, value, fund="Fund1", manager="Mgr1", pct=1.0,
      currency="SEK"):
    return {"manager": manager, "fund": fund, "fund_nav": 1000.0,
            "instrument": instrument, "shares": shares, "value": value,
            "value_currency": "SEK", "pct_of_fund": pct, "price": 1.0,
            "fx_to_sek": 1.0, "currency": currency}


def run_main(argv, index, quarter_end="2026-03-31", quarter="2026Q1"):
    """Run main() end-to-end against a canned index, capturing stdout."""
    buf = io.StringIO()
    with mock.patch.object(m, "load_index",
                           lambda quarter=None: (index, quarter_end, quarter)), \
         mock.patch.object(sys, "argv", ["ownership_se.py"] + argv), \
         contextlib.redirect_stdout(buf):
        m.main()
    return buf.getvalue()


# --------------------------------------------------------------------- Bug 1


class NameResolutionEquityVsBonds(unittest.TestCase):
    """resolve_name() is a NEW function - calling it against the old module
    raises AttributeError, so every test in this class fails outright on the
    old ownership_se.py regardless of its assertions."""

    def test_sandvik_resolves_to_the_equity_not_a_refusal(self):
        """1 equity line + 2 bond lines, all filed under 'Sandvik', is the
        exact shape from the task: SANDVIK, SANDVIK 281125 EUR, and
        Sandvik AB 22/29 3,75% C. The old code counted all 3 as raw ISIN
        matches and always refused. shares=None marks the bond rows the way
        FI's own export does (Antal empty, Nominellt_belopp populated
        instead - not modelled here since resolve_name only looks at shares)."""
        index = {
            "SE_SANDVIK_EQ": [mk("SANDVIK", "SE_SANDVIK_EQ", 1000, 500000)],
            "SE_SANDVIK_B1": [mk("SANDVIK 281125 EUR", "SE_SANDVIK_B1", None, 200000)],
            "SE_SANDVIK_B2": [mk("Sandvik AB 22/29 3,75% C", "SE_SANDVIK_B2",
                                 None, 150000)],
        }
        result = m.resolve_name(index, "Sandvik")
        self.assertEqual(result["status"], "resolved",
                         "an equity line plus two bond lines must resolve, "
                         "not refuse: %r" % result)
        self.assertEqual(result["isin"], "SE_SANDVIK_EQ")
        self.assertEqual(result["other_classes"], [],
                         "no other equity class exists - the bonds must not "
                         "show up as 'other classes' either")

    def test_atlas_copco_resolves_to_widest_held_class_and_names_the_other(self):
        """A and B share classes of ONE issuer, plus a bond filed under the
        same name. Old code: 3 raw matches -> refuse (the exact 'same
        issuer, two classes' case the task calls out as wrongly refused).
        New code: one issuer stem survives after dropping the bond; the more
        widely held class (A, 900k SEK) is reported, B is named as an
        'other class', not hidden and not a refusal."""
        index = {
            "SE_ATCO_A": [mk("Atlas Copco A", "SE_ATCO_A", 500, 900000)],
            "SE_ATCO_B": [mk("Atlas Copco B", "SE_ATCO_B", 800, 300000)],
            "SE_ATCO_BOND": [mk("Atlas Copco 24/30", "SE_ATCO_BOND", None, 50000)],
        }
        result = m.resolve_name(index, "Atlas Copco")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["isin"], "SE_ATCO_A",
                         "the more widely held class (by value) must be the "
                         "one reported")
        other_isins = {o["isin"] for o in result["other_classes"]}
        self.assertEqual(other_isins, {"SE_ATCO_B"},
                         "the other listed class must be named, not silently "
                         "dropped and not refused as ambiguous")

    def test_two_different_issuers_still_refuse_and_both_are_named(self):
        """A genuine ambiguity - AB Volvo and Volvo Car AB are different
        issuers that both match 'Volvo' and both carry equity. This must
        still refuse. Asserted against the NEW refusal vocabulary
        ('issuers', not the old 'instruments') so this test also fails
        against the old module, which never distinguished issuer identity
        from raw ISIN count and phrased the refusal differently."""
        index = {
            "SE_VOLVO_B": [mk("AB Volvo B", "SE_VOLVO_B", 100, 400000)],
            "SE_VOLVO_A": [mk("AB Volvo A", "SE_VOLVO_A", 50, 100000)],
            "SE_VOLVOCAR_B": [mk("Volvo Car AB B", "SE_VOLVOCAR_B", 200, 250000)],
        }
        result = m.resolve_name(index, "Volvo")
        self.assertEqual(result["status"], "ambiguous")
        names = {c["instrument"] for c in result["candidates"]}
        self.assertTrue(any("AB Volvo" in n for n in names))
        self.assertTrue(any("Volvo Car AB" in n for n in names))

    def test_bond_only_match_is_not_an_equity_hit(self):
        """A name that matches ONLY bond rows (no row anywhere has a share
        count) must not resolve at all - the old code had no concept of
        'equity only' and would have happily treated a bond-only match as a
        single resolvable instrument."""
        index = {
            "SE_BOND_ONLY": [mk("Kommuninvest 26/28", "SE_BOND_ONLY", None, 100000)],
        }
        result = m.resolve_name(index, "Kommuninvest")
        self.assertEqual(result["status"], "absent",
                         "a debt-only match must not be treated as a "
                         "resolvable equity instrument")

    def test_main_end_to_end_sandvik_does_not_refuse(self):
        """Integration check: through the real CLI path (main(), argument
        parsing, the whole report), 'Sandvik' must produce a report, not the
        refusal banner. The old code prints 'Several instruments match'
        here; this asserts that string is ABSENT and the equity ISIN shows
        up in the header instead."""
        index = {
            "SE_SANDVIK_EQ": [mk("SANDVIK", "SE_SANDVIK_EQ", 1000, 500000)],
            "SE_SANDVIK_B1": [mk("SANDVIK 281125 EUR", "SE_SANDVIK_B1", None, 200000)],
            "SE_SANDVIK_B2": [mk("Sandvik AB 22/29 3,75% C", "SE_SANDVIK_B2",
                                 None, 150000)],
        }
        out = run_main(["--name", "Sandvik", "--no-trend"], index)
        self.assertNotIn("match", out.lower())
        self.assertIn("SE_SANDVIK_EQ", out)

    def test_main_end_to_end_atlas_copco_names_the_other_class(self):
        index = {
            "SE_ATCO_A": [mk("Atlas Copco A", "SE_ATCO_A", 500, 900000)],
            "SE_ATCO_B": [mk("Atlas Copco B", "SE_ATCO_B", 800, 300000)],
        }
        out = run_main(["--name", "Atlas Copco", "--no-trend"], index)
        self.assertNotIn("match", out.lower(),
                         "two share classes of one issuer must resolve, not "
                         "trigger the old code's 'Several instruments match' "
                         "refusal")
        self.assertIn("SE_ATCO_A", out)
        self.assertIn("Also listed", out,
                      "the other class must be named via the new "
                      "'Also listed' note, not silently swapped in")
        self.assertIn("Atlas Copco B", out,
                      "the other class must be named in the report, not "
                      "just silently swapped in")

    def test_main_end_to_end_two_issuers_refuse_with_new_wording(self):
        index = {
            "SE_VOLVO_B": [mk("AB Volvo B", "SE_VOLVO_B", 100, 400000)],
            "SE_VOLVOCAR_B": [mk("Volvo Car AB B", "SE_VOLVOCAR_B", 200, 250000)],
        }
        out = run_main(["--name", "Volvo", "--no-trend"], index)
        self.assertIn("Several issuers match", out,
                      "the old code's exact wording was 'Several instruments "
                      "match' - this pins the new, issuer-aware wording")
        self.assertIn("AB Volvo", out)
        self.assertIn("Volvo Car AB", out)


# --------------------------------------------------------------------- Bug 2


class StalenessIsComputedNotHardcoded(unittest.TestCase):
    def test_data_age_days_is_a_real_computation(self):
        """data_age_days() is a NEW function - AttributeError on the old
        module. FI's latest published quarter as of the task's writing is
        2026Q1 (quarter end 2026-03-31); as of 2026-09-01 that is 154 days,
        not 'weeks'."""
        import datetime
        age = m.data_age_days("2026-03-31")
        expected = (datetime.date.today() - datetime.date(2026, 3, 31)).days
        self.assertEqual(age, expected)
        self.assertGreater(age, 60, "five months old must not read as weeks")

    def test_data_age_days_none_when_quarter_end_unknown(self):
        self.assertIsNone(m.data_age_days(None))
        self.assertIsNone(m.data_age_days(""))

    def test_main_prints_computed_age_not_hardcoded_weeks_claim(self):
        """The old code unconditionally printed 'is already weeks old'. This
        must be gone, replaced by a computed day count that names the actual
        quarter end."""
        index = {
            "SE_TEST": [mk("Test AB", "SE_TEST", 100, 100000)],
        }
        out = run_main(["--name", "Test AB", "--no-trend"], index,
                       quarter_end="2026-03-31")
        self.assertNotIn("already weeks old", out)
        self.assertIn("days ago", out)
        self.assertIn("2026-03-31", out)


# --------------------------------------------------------------------- Bug 3


class ConvictionListSortedByNavWeight(unittest.TestCase):
    """The old code showed conviction[:6] in the VALUE ordering `holdings`
    already carried (holdings.sort(key=value) runs earlier in main()). Built
    here so the largest-VALUE holder (Fund-Big) has the SMALLEST pct_of_fund
    among the conviction set, and the smallest-value holder (Fund-Small) has
    the largest pct_of_fund - the old code would print Fund-Big first, this
    must print Fund-Small first."""

    def test_conviction_block_is_ordered_by_pct_of_fund_desc(self):
        index = {
            "SE_TEST": [
                mk("Test AB", "SE_TEST", 100, 900000, fund="Fund-Big",
                  manager="M1", pct=4.5),
                mk("Test AB", "SE_TEST", 100, 500000, fund="Fund-Mid",
                  manager="M2", pct=6.0),
                mk("Test AB", "SE_TEST", 100, 100000, fund="Fund-Small",
                  manager="M3", pct=9.0),
            ],
        }
        out = run_main(["--name", "Test AB", "--no-trend"], index)
        block = out.split("High-conviction positions")[1]
        block = block.split("A manager with this much")[0]
        pos_small = block.find("Fund-Small")
        pos_mid = block.find("Fund-Mid")
        pos_big = block.find("Fund-Big")
        self.assertNotEqual(pos_small, -1)
        self.assertNotEqual(pos_mid, -1)
        self.assertNotEqual(pos_big, -1)
        self.assertLess(pos_small, pos_mid,
                        "9.0%% NAV (Fund-Small) must print before 6.0%% "
                        "(Fund-Mid), even though its VALUE is smallest")
        self.assertLess(pos_mid, pos_big,
                        "6.0%% NAV (Fund-Mid) must print before 4.5%% "
                        "(Fund-Big), even though Fund-Big's VALUE is largest")


# ------------------------------------------------------ pinned earlier fix


class LatestSubmissionPinning(unittest.TestCase):
    """Not a new bug - a regression pin for the fix already made to
    _latest_submissions() earlier today (FI files corrections INTO the same
    quarterly archive rather than replacing the original). Built as a real
    zip via zipfile.ZipFile so build_index()'s own file-walking code runs,
    not a hand-rolled substitute for it."""

    FUND_XML = """<?xml version="1.0" encoding="utf-8"?>
<Rapport>
  <Rapportinformation><Kvartalsslut>2025-03-31</Kvartalsslut></Rapportinformation>
  <Bolagsinformation><Fondbolag_namn>Sjunde AP-fonden</Fondbolag_namn></Bolagsinformation>
  <Fondinformation>
    <Fond_namn>AP7 Aktiefond</Fond_namn>
    <Fondförmögenhet>1000000</Fondförmögenhet>
    <FinansiellaInstrument>
      <Instrument>
        <ISIN-kod_instrument>SE0000000042</ISIN-kod_instrument>
        <Instrumentnamn>Test Holding AB</Instrumentnamn>
        <Antal>{shares}</Antal>
        <Marknadsvärde_instrument>{value}</Marknadsvärde_instrument>
        <Andel_av_fondförmögenhet_instrument>5</Andel_av_fondförmögenhet_instrument>
        <Kurs_som_använts_vid_värdering_av_instrumentet>100</Kurs_som_använts_vid_värdering_av_instrumentet>
        <Valutakurs_instrument>1</Valutakurs_instrument>
        <Valuta>SEK</Valuta>
      </Instrument>
    </FinansiellaInstrument>
  </Fondinformation>
</Rapport>
"""

    def test_only_the_later_submission_is_read(self):
        early_xml = self.FUND_XML.format(shares=100, value=10000)
        later_xml = self.FUND_XML.format(shares=999, value=99900)

        path = None
        try:
            fd, path = tempfile.mkstemp(suffix=".zip")
            os.close(fd)
            import zipfile
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr(
                    "Sjunde AP-fonden_91117_2025-04-09 10.37/"
                    "91117_2025-04-09 10.37.xml",
                    early_xml.encode("utf-8"))
                zf.writestr(
                    "Sjunde AP-fonden_91117_2026-06-10 14.18/"
                    "91117_2026-06-10 14.18.xml",
                    later_xml.encode("utf-8"))

            index, quarter_end = m.build_index(path)
            self.assertEqual(quarter_end, "2025-03-31")
            holdings = index.get("SE0000000042", [])
            self.assertEqual(len(holdings), 1,
                             "AP7 Aktiefond must appear ONCE across the two "
                             "submitted folders, not once per submission")
            self.assertEqual(holdings[0]["shares"], 999,
                             "the LATER submission's figures must win")
            self.assertEqual(holdings[0]["value"], 99900)
        finally:
            if path and os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main()
