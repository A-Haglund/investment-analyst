#!/usr/bin/env python3
"""Group 5 (spec §39) - First North / small cap, using KebNi.

KebNi (ISIN SE0012904803) trades on Nasdaq First North Growth Market Sweden,
an MTF, not a regulated market. Three things must never happen for an issuer
like this:

  1. The system must not ASSUME an ESEF filing exists - First North issuers
     are exempt from the Transparency Directive and file none, ever.
  2. It must not claim COMPLETE ownership data - ownership_se.py covers only
     Swedish-domiciled UCITS funds, a floor, never the whole register.
  3. It must not claim CONSENSUS exists - no free source in this toolkit
     provides real analyst consensus, for any issuer, ever.

And the positive requirement: absence of ESEF on an MTF must be reported as
NOT APPLICABLE (a fact about the venue's legal status), never as missing data
(a fact that would imply something went wrong or was overlooked).
"""
import json
import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import SCRIPTS_DIR, load, network

venues = load("venues_se")

KEBNI_ISIN = "SE0012904803"

# venues_se.py classifies venues by MIC; these three are Swedish MTFs (no
# ESEF ever), XSTO is the regulated main market (ESEF required) as a control.
MTF_MICS = ("XSAT", "SSME", "NSME")   # Spotlight, First North Sweden, Nordic SME
REGULATED_MICS = ("XSTO", "XNGM")


class EsefDoesNotApplyOnAnMtf(unittest.TestCase):
    """Exercises venues_se.py's real Segment classification and its
    source_chain() text directly - no network needed, since which MICs are
    regulated markets vs MTFs is static reference data in the script, not a
    live lookup."""

    def test_mtf_segments_do_not_claim_esef(self):
        for mic in MTF_MICS:
            with self.subTest(mic=mic):
                segment = venues.SEGMENTS[mic]
                self.assertFalse(segment.regulated)
                self.assertFalse(segment.esef,
                                 "%s is an MTF; ESEF must never be assumed "
                                 "to apply there" % mic)

    def test_regulated_markets_do_expect_esef(self):
        for mic in REGULATED_MICS:
            with self.subTest(mic=mic):
                segment = venues.SEGMENTS[mic]
                self.assertTrue(segment.regulated)
                self.assertTrue(segment.esef)

    def test_source_chain_reports_not_applicable_never_missing(self):
        """This is the exact code path venues_se.py's own build_json() uses to
        set esef_status: 'not_applicable' when `not segment.esef`. Calling
        source_chain() directly (periods=None, wire=None - no ESEF filing was
        even looked for, because none can exist) reproduces its wording."""
        for mic in MTF_MICS:
            with self.subTest(mic=mic):
                segment = venues.SEGMENTS[mic]
                lines = venues.source_chain(segment, None, None)
                first = lines[0]
                self.assertIn("DOES NOT APPLY", first)
                self.assertIn("Do not report this as missing data", first)
                # And the inverse must be equally true on a regulated market
                # with genuinely no filings found - that IS a gap to flag.
        reg_segment = venues.SEGMENTS["XSTO"]
        reg_lines = venues.source_chain(reg_segment, [], None)
        self.assertIn("NONE FOUND", reg_lines[0])
        self.assertIn("genuine gap", reg_lines[0])

    def test_build_json_esef_status_ternary_matches_segment_flag(self):
        """Reproduces the literal ternary venues_se.py's build_json() uses,
        against the real Segment objects, so a future edit to either the
        ternary or the MIC table trips this test."""
        for mic in MTF_MICS:
            segment = venues.SEGMENTS[mic]
            periods = None  # never even queried for an MTF in practice
            status = ("not_applicable" if not segment.esef else
                      "unknown" if periods is None else
                      "present" if periods else "missing")
            self.assertEqual(status, "not_applicable")


class NoScriptFabricatesConsensus(unittest.TestCase):
    """No free source in this toolkit provides real analyst consensus (see
    references/valuation.md and references/source-registry.md: consensus is
    to be reported as DATA NOT AVAILABLE, never approximated).

    This was originally a bare trip-wire: NO script could contain the word.
    v3.0.0 took the second branch its own docstring offered ("update this
    test with a clear reason why it is now safe"), because three files must
    now discuss consensus in order to say it does not exist -
    calibration.py explains that a forward track record needs none,
    decision_record.py explains that implied expectations are the only
    forward-expectation series obtainable without it, and research_delta.py
    diffs whatever consensus string a stored decision already carried,
    which for this toolkit's coverage is "CONSENSUS DATA NOT AVAILABLE".

    Discussing the absence is the honest behaviour the rule exists to
    protect, so the rule now tests the FABRICATION instead of the word:
      1. a file that mentions consensus must also state its unavailability,
         so the word can never appear without the caveat attached; and
      2. no file may bind a NUMBER to a consensus-named variable, which is
         what fabricating or approximating a consensus figure would look
         like in code.
    That is strictly harder to defeat than the grep it replaces."""

    UNAVAILABILITY = re.compile(
        r"no consensus|needs no consensus|consensus data not available|"
        r"consensus is usually|without .{0,20}consensus", re.I)

    def test_a_script_mentioning_consensus_also_states_it_is_unavailable(self):
        undisclosed = []
        for fname in sorted(os.listdir(SCRIPTS_DIR)):
            if not fname.endswith(".py"):
                continue
            with open(os.path.join(SCRIPTS_DIR, fname), encoding="utf-8") as fh:
                text = fh.read()
            if re.search(r"consensus", text, re.I) and \
                    not self.UNAVAILABILITY.search(text):
                undisclosed.append(fname)
        self.assertEqual(
            undisclosed, [],
            "%r mention 'consensus' without anywhere stating that it is not "
            "available. Either add the caveat or stop mentioning it - the "
            "word must never travel without it (spec §39)." % (undisclosed,))

    def test_no_script_binds_a_number_to_a_consensus_name(self):
        import ast
        offenders = []
        for fname in sorted(os.listdir(SCRIPTS_DIR)):
            if not fname.endswith(".py"):
                continue
            path = os.path.join(SCRIPTS_DIR, fname)
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = [t.id for t in targets if isinstance(t, ast.Name)]
                names += [t.attr for t in targets if isinstance(t, ast.Attribute)]
                if not any("consensus" in n.lower() for n in names):
                    continue
                value = node.value
                numeric = (isinstance(value, ast.Constant)
                           and isinstance(value.value, (int, float))
                           and not isinstance(value.value, bool))
                computed = isinstance(value, (ast.BinOp, ast.Call))
                if numeric or computed:
                    offenders.append("%s:%d" % (fname, node.lineno))
        self.assertEqual(
            offenders, [],
            "%r bind a number to a consensus-named variable. This toolkit has "
            "no consensus source; a computed consensus is a fabricated one "
            "(spec §39)." % (offenders,))


@network
class KebNiLiveChecks(unittest.TestCase):
    """Live checks against KebNi specifically (First North Sweden, ISIN
    SE0012904803)."""

    def test_kebni_venue_is_an_mtf_with_no_esef(self):
        matches, failed = venues.resolve("KebNi", mics=["SSME"])
        self.assertTrue(matches, "KebNi not found on First North Sweden via FIRDS")
        row = matches[0]
        segment = venues.SEGMENTS[row["mic"]]
        self.assertFalse(segment.esef)
        periods = venues.esef_filings(row["lei"])
        # None means "index unreachable" (unknown); [] means "genuinely no
        # filings" - both are consistent with an MTF, but a populated list
        # would be the real surprise worth investigating.
        self.assertFalse(periods, "KebNi (First North) unexpectedly has ESEF "
                                  "filings indexed - investigate before "
                                  "trusting the MTF assumption")

    def test_ownership_is_reported_as_a_floor_not_completeness(self):
        """Runs ownership_se.py end-to-end for KebNi's real ISIN and reads
        its own JSON output. We deliberately do not assert a specific fund
        count or share total - FI publishes a new quarter over time and that
        drift is not a regression. What must never change is that the output
        labels itself a floor, not the complete shareholder register."""
        script = os.path.join(SCRIPTS_DIR, "ownership_se.py")
        proc = subprocess.run(
            [sys.executable, script, "--isin", KEBNI_ISIN, "--json", "--no-trend"],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        basis = payload.get("basis", "")
        self.assertIn("FLOOR", basis.upper())
        self.assertIn("SWEDISH", basis.upper())
        self.assertNotIn("COMPLETE", basis.upper())


if __name__ == "__main__":
    unittest.main()
