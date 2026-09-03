#!/usr/bin/env python3
"""research_delta.py - the pure diff() engine and its rendering, offline.

Covers:
  - No material change at all: identical records must not read as a call
    change, and the rendered headline must say so plainly.
  - A price-only change: everything else stays "unchanged", not "unavailable".
  - Verdict upgrade AND downgrade on decision_record.VERDICTS' real (and
    counter-intuitive: index 0 is most bullish) ordering.
  - Conviction transitions in both directions on decision_record.CONVICTIONS'
    real (weakest-to-strongest) ordering.
  - A margin move is reported in PERCENTAGE POINTS, never percent-of-percent.
  - A field missing from one side is "unavailable", never a fabricated 0.
  - A currency change on price marks the whole valuation group "not_comparable"
    and names the reason, rather than printing a wrong percentage.
  - A basis change on fundamentals (adjusted vs IFRS) is likewise refused.
  - An assumption that vanishes between two records is flagged, not silently
    dropped.
  - A reason code appearing and a different one resolving, in the same diff.
  - Both orderings of a two-way comparison (the property --between OLD NEW /
    --between NEW OLD relies on): swapping old and new flips the verdict/
    conviction direction and the sign of a percent change.
  - The no-baseline path: research_delta must report "no baseline yet"
    rather than crash when thesis_ledger has no ledger, or no decisions
    store, for a company.
  - No rendered line in render_human() exceeds 88 columns, across several
    shapes of delta (no change, a "worst case" full change, and a
    not-comparable case).
"""
import copy
import json
import os
import shutil
import tempfile
import unittest

import helpers

helpers.bootstrap_path()

dr = helpers.load("decision_record")
rd = helpers.load("research_delta")


def _base():
    """decision_record's own Sandvik fixture, validated so expected_return /
    margin_of_safety are populated the way a real caller would see them, and
    deep-copied so mutating the result in one test cannot leak into another."""
    rec, _warns = dr.validate(copy.deepcopy(dr._sandvik_fixture()))
    return rec


class TestNoChange(unittest.TestCase):
    def test_identical_records_are_not_material(self):
        d = rd.diff(_base(), _base())
        self.assertFalse(d["material"])
        self.assertEqual(d["decision"]["verdict"]["direction"], "unchanged")
        self.assertEqual(d["decision"]["conviction"]["direction"], "unchanged")

    def test_headline_states_no_change_plainly(self):
        d = rd.diff(_base(), _base())
        block = rd.render_human(d)
        self.assertIn("NO CHANGE TO THE CALL", block)

    def test_identical_records_carry_no_fabricated_fundamentals(self):
        d = rd.diff(_base(), _base())
        self.assertEqual(d["fundamentals"]["status"], "unavailable")


class TestPriceOnlyChange(unittest.TestCase):
    def test_price_moves_alone_leaves_the_call_unchanged(self):
        old, new = _base(), _base()
        new["price"] = dict(new["price"])
        new["price"]["value"] = 400.0
        d = rd.diff(old, new)
        self.assertEqual(d["valuation"]["price"]["status"], "ok")
        self.assertAlmostEqual(d["valuation"]["price"]["change"], (400.0 - 356.0) / 356.0, places=6)
        self.assertEqual(d["decision"]["verdict"]["direction"], "unchanged")
        self.assertEqual(d["decision"]["conviction"]["direction"], "unchanged")
        self.assertFalse(d["material"])


class TestVerdictDirection(unittest.TestCase):
    """decision_record.VERDICTS runs STRONG BUY -> STRONG SELL, so an
    upgrade is a FALL in index - the opposite of conviction's ladder."""

    def test_upgrade_buy_to_strong_buy(self):
        old, new = _base(), _base()
        new["verdict"] = "STRONG BUY"
        d = rd.diff(old, new)
        self.assertEqual(d["decision"]["verdict"]["direction"], "upgrade")
        self.assertTrue(d["material"])

    def test_downgrade_buy_to_sell(self):
        old, new = _base(), _base()
        new["verdict"] = "SELL"
        d = rd.diff(old, new)
        self.assertEqual(d["decision"]["verdict"]["direction"], "downgrade")
        self.assertTrue(d["material"])

    def test_function_matches_decision_record_ordering_across_the_ladder(self):
        for i in range(len(dr.VERDICTS) - 1):
            more_bullish, less_bullish = dr.VERDICTS[i], dr.VERDICTS[i + 1]
            self.assertEqual(rd.verdict_direction(less_bullish, more_bullish), "upgrade")
            self.assertEqual(rd.verdict_direction(more_bullish, less_bullish), "downgrade")

    def test_headline_names_the_transition(self):
        old, new = _base(), _base()
        new["verdict"] = "STRONG BUY"
        block = rd.render_human(rd.diff(old, new))
        self.assertIn("THE CALL MOVED", block)
        self.assertIn("BUY -> STRONG BUY", block)


class TestConvictionDirection(unittest.TestCase):
    """decision_record.CONVICTIONS is weakest to strongest: an upgrade is a
    RISE in index."""

    def test_upgrade_medium_to_high(self):
        old, new = _base(), _base()
        new["conviction"] = "HIGH"
        d = rd.diff(old, new)
        self.assertEqual(d["decision"]["conviction"]["direction"], "upgrade")

    def test_downgrade_medium_to_low(self):
        old, new = _base(), _base()
        new["conviction"] = "LOW"
        d = rd.diff(old, new)
        self.assertEqual(d["decision"]["conviction"]["direction"], "downgrade")

    def test_function_matches_decision_record_ordering_across_the_ladder(self):
        for i in range(len(dr.CONVICTIONS) - 1):
            weaker, stronger = dr.CONVICTIONS[i], dr.CONVICTIONS[i + 1]
            self.assertEqual(rd.conviction_direction(weaker, stronger), "upgrade")
            self.assertEqual(rd.conviction_direction(stronger, weaker), "downgrade")


class TestMarginIsPercentagePointsNotPercent(unittest.TestCase):
    def test_18_0_to_18_7_is_plus_0_7_pp(self):
        old, new = _base(), _base()
        old["fundamentals"] = {"ebit_margin": 0.180, "currency": "SEK", "basis": "IFRS"}
        new["fundamentals"] = {"ebit_margin": 0.187, "currency": "SEK", "basis": "IFRS"}
        d = rd.diff(old, new)
        field = d["fundamentals"]["ebit_margin"]
        self.assertEqual(field["status"], "ok")
        self.assertEqual(field["unit"], "pp")
        self.assertAlmostEqual(field["change"], 0.7, places=6)
        # The wrong, percent-of-percent answer would be roughly +3.9%.
        self.assertNotAlmostEqual(field["change"], 3.888888, places=1)

    def test_rendered_margin_carries_the_pp_suffix(self):
        old, new = _base(), _base()
        old["fundamentals"] = {"ebit_margin": 0.180, "currency": "SEK"}
        new["fundamentals"] = {"ebit_margin": 0.187, "currency": "SEK"}
        block = rd.render_human(rd.diff(old, new))
        self.assertIn("+0.7pp", block)


class TestMissingFieldNeverFabricated(unittest.TestCase):
    def test_dropped_expected_return_is_unavailable_not_zero(self):
        old, new = _base(), _base()
        new.pop("fair_value", None)
        new.pop("scenario_weights", None)
        new["expected_return"] = None
        new["margin_of_safety"] = None
        d = rd.diff(old, new)
        field = d["valuation"]["expected_return"]
        self.assertEqual(field["status"], "unavailable")
        self.assertIsNone(field.get("change"))

    def test_fundamentals_absent_entirely_is_unavailable(self):
        d = rd.diff(_base(), _base())
        self.assertEqual(d["fundamentals"]["status"], "unavailable")
        self.assertNotEqual(d["fundamentals"]["status"], "ok")

    def test_rendered_missing_fundamentals_says_not_available_not_zero(self):
        """A field that is MISSING must never render as a change of zero.

        The assertion is scoped to the FACTS section on purpose. Two
        identical decisions legitimately print "+0%" under VALUATION -
        the price really did not move - and asserting on the whole block
        confused "unchanged", which is a fact, with "unavailable", which
        is the absence of one. Those are exactly the two things this test
        exists to keep apart."""
        block = rd.render_human(rd.diff(_base(), _base()))
        self.assertIn("DATA NOT AVAILABLE", block)
        facts = [chunk for chunk in block.split("\n\n")
                 if chunk.lstrip().startswith("FACTS")]
        self.assertEqual(len(facts), 1, block)
        self.assertNotIn("+0%", facts[0])
        self.assertNotIn("+0.0pp", facts[0])


class TestBasisChangeRefusesComparison(unittest.TestCase):
    def test_price_currency_change_marks_valuation_not_comparable(self):
        old, new = _base(), _base()
        new["price"] = dict(new["price"])
        new["price"]["currency"] = "EUR"
        d = rd.diff(old, new)
        self.assertEqual(d["valuation"]["status"], "not_comparable")
        self.assertTrue(any("currency" in r for r in d["valuation"]["reasons"]))

    def test_not_comparable_names_the_reason_in_the_render(self):
        old, new = _base(), _base()
        new["price"] = dict(new["price"])
        new["price"]["currency"] = "EUR"
        block = rd.render_human(rd.diff(old, new))
        self.assertIn("NOT COMPARABLE", block)
        self.assertIn("currency", block)

    def test_fundamentals_reporting_basis_change_is_not_comparable(self):
        old, new = _base(), _base()
        old["fundamentals"] = {"ebit_margin": 0.18, "basis": "adjusted", "currency": "SEK"}
        new["fundamentals"] = {"ebit_margin": 0.19, "basis": "IFRS", "currency": "SEK"}
        d = rd.diff(old, new)
        self.assertEqual(d["fundamentals"]["status"], "not_comparable")

    def test_fundamentals_currency_change_is_not_comparable(self):
        old, new = _base(), _base()
        old["fundamentals"] = {"revenue": 100.0, "currency": "SEK"}
        new["fundamentals"] = {"revenue": 12.0, "currency": "EUR"}
        d = rd.diff(old, new)
        self.assertEqual(d["fundamentals"]["status"], "not_comparable")

    def test_identity_mismatch_refuses_the_whole_diff(self):
        old, new = _base(), _base()
        new["identity"] = dict(new["identity"])
        new["identity"]["lei"] = "549300ZZZZZZZZZZZZZZ99"
        new["identity"]["isin"] = "SE0000000099"
        with self.assertRaises(rd.DeltaError):
            rd.diff(old, new)


class TestAssumptionDisappearing(unittest.TestCase):
    def test_removed_assumption_is_listed(self):
        old, new = _base(), _base()
        new["assumptions"] = []
        d = rd.diff(old, new)
        self.assertIn("ebit_margin_through_cycle", d["thesis"]["assumptions"]["removed"])

    def test_removed_assumption_is_flagged_in_the_render(self):
        old, new = _base(), _base()
        new["assumptions"] = []
        block = rd.render_human(rd.diff(old, new))
        self.assertIn("WARNING", block)
        self.assertIn("vanished", block)


class TestReasonCodesAppearAndResolve(unittest.TestCase):
    def test_one_appears_and_one_resolves_in_the_same_diff(self):
        old, new = _base(), _base()
        new["reason_codes"] = [{"code": "VENUE_MICROCAP", "severity": "WARN", "detail": "x"}]
        d = rd.diff(old, new)
        appeared = [c["code"] for c in d["thesis"]["reason_codes"]["appeared"]]
        self.assertIn("VENUE_MICROCAP", appeared)
        # resolved carries {code, severity} entries too - symmetric with
        # appeared, because "a BLOCK cleared" and "a WARN cleared" are not
        # the same news.
        resolved = [c["code"] for c in d["thesis"]["reason_codes"]["resolved"]]
        self.assertIn("GATE_TTM_INCOMPLETE", resolved)

    def test_a_new_warn_code_counts_as_a_concern(self):
        old, new = _base(), _base()
        new["reason_codes"] = list(old["reason_codes"]) + [
            {"code": "VENUE_MICROCAP", "severity": "WARN", "detail": "x"}]
        d = rd.diff(old, new)
        self.assertEqual(d["thesis"]["concerns"], 1)

    def test_unchanged_rests_on_counts_as_confirmations(self):
        d = rd.diff(_base(), _base())
        self.assertEqual(d["thesis"]["confirmations"], 3)  # the fixture's 3 rests_on entries


class TestSupersededIsSurfaced(unittest.TestCase):
    """thesis_ledger.supersede_decision() sets superseded_by/superseded_at
    explicitly; storing a newer decision does NOT set them on the older one.
    diff() must not silently diff against a withdrawn/superseded record - it
    has to say so."""

    def test_withdrawn_old_decision_is_flagged(self):
        old, new = _base(), _base()
        old["superseded_by"] = "WITHDRAWN"
        old["superseded_at"] = "2026-09-01T00:00:00Z"
        d = rd.diff(old, new)
        self.assertTrue(any(n["which"] == "old" and n["withdrawn"] for n in d["superseded"]))

    def test_superseded_by_another_decision_is_flagged_with_its_id(self):
        old, new = _base(), _base()
        old["superseded_by"] = "LEI-213800Y2XLTQMHLB5J34:2026-09-02T00:00:00Z"
        d = rd.diff(old, new)
        note = [n for n in d["superseded"] if n["which"] == "old"][0]
        self.assertFalse(note["withdrawn"])
        self.assertEqual(note["by"], "LEI-213800Y2XLTQMHLB5J34:2026-09-02T00:00:00Z")

    def test_a_standing_pair_carries_no_superseded_notes(self):
        d = rd.diff(_base(), _base())
        self.assertEqual(d["superseded"], [])

    def test_withdrawn_warning_reaches_the_render(self):
        old, new = _base(), _base()
        old["superseded_by"] = "WITHDRAWN"
        block = rd.render_human(rd.diff(old, new))
        self.assertIn("WARNING", block)
        self.assertIn("WITHDRAWN", block)


class TestThesisRefRealShape(unittest.TestCase):
    """thesis_ledger.decision_thesis_ref()'s actual shape: {thesis_id,
    status, status_since, as_of, last_evaluated, action, active_theses,
    captured_at}, status being one of thesis_ledger.STATUS_ORDER."""

    def test_confirmed_to_warning_is_deteriorating(self):
        old, new = _base(), _base()
        old["thesis_ref"] = {"thesis_id": "T1", "status": "CONFIRMED"}
        new["thesis_ref"] = {"thesis_id": "T1", "status": "WARNING"}
        d = rd.diff(old, new)
        self.assertEqual(d["thesis"]["thesis_ref"]["direction"], "deteriorating")

    def test_warning_to_confirmed_is_improving(self):
        old, new = _base(), _base()
        old["thesis_ref"] = {"thesis_id": "T1", "status": "WARNING"}
        new["thesis_ref"] = {"thesis_id": "T1", "status": "CONFIRMED"}
        d = rd.diff(old, new)
        self.assertEqual(d["thesis"]["thesis_ref"]["direction"], "improving")

    def test_no_thesis_linked_on_either_side_is_unavailable(self):
        d = rd.diff(_base(), _base())
        self.assertEqual(d["thesis"]["thesis_ref"]["status"], "unavailable")

    def test_thesis_linked_on_only_one_side_is_unavailable_not_unchanged(self):
        old, new = _base(), _base()
        new["thesis_ref"] = {"thesis_id": "T1", "status": "STABLE"}
        d = rd.diff(old, new)
        self.assertEqual(d["thesis"]["thesis_ref"]["status"], "unavailable")


class TestValidationWarningsDiff(unittest.TestCase):
    def test_appeared_and_resolved(self):
        old, new = _base(), _base()
        old["validation_warnings"] = ["rests_on is empty"]
        new["validation_warnings"] = ["assumption 'x' carries no rationale"]
        d = rd.diff(old, new)
        self.assertIn("assumption 'x' carries no rationale", d["validation_warnings"]["appeared"])
        self.assertIn("rests_on is empty", d["validation_warnings"]["resolved"])

    def test_absent_on_both_sides_is_unavailable(self):
        d = rd.diff(_base(), _base())
        self.assertEqual(d["validation_warnings"]["status"], "unavailable")


class TestBetweenBothOrderings(unittest.TestCase):
    """--between OLD_ID NEW_ID resolves to diff(old_rec, new_rec) in exactly
    the order given; swapping the two ids must swap the direction of every
    directional field. This exercises that property on diff() directly."""

    def test_swapping_flips_verdict_and_conviction_direction(self):
        a, b = _base(), _base()
        b["verdict"] = "STRONG BUY"
        b["conviction"] = "HIGH"
        forward = rd.diff(a, b)
        backward = rd.diff(b, a)
        self.assertEqual(forward["decision"]["verdict"]["direction"], "upgrade")
        self.assertEqual(backward["decision"]["verdict"]["direction"], "downgrade")
        self.assertEqual(forward["decision"]["conviction"]["direction"], "upgrade")
        self.assertEqual(backward["decision"]["conviction"]["direction"], "downgrade")

    def test_swapping_flips_the_sign_of_a_percent_change(self):
        a, b = _base(), _base()
        b["price"] = dict(b["price"])
        b["price"]["value"] = 400.0
        forward = rd.diff(a, b)
        backward = rd.diff(b, a)
        self.assertGreater(forward["valuation"]["price"]["change"], 0)
        self.assertLess(backward["valuation"]["price"]["change"], 0)


class TestIsolatedLedgerStore(unittest.TestCase):
    """The ledger-backed shell (_open_ledger / _cmd_between /
    _cmd_new_vs_stored) exercised against thesis_ledger.py's REAL decisions
    store (add_decision / list_decisions / latest_decision / get_decision),
    landed by a parallel agent. THESIS_LEDGER_HOME points at a scratch
    directory for the lifetime of each test - never the real
    ~/.investment-analyst - and resolve_identity() is stubbed to a fixed
    identity so no network call is needed; every other function used here
    (ledger_key, new_ledger, save_ledger, add_decision, list_decisions,
    latest_decision, get_decision) is the real thing."""

    IDENTITY = {"lei": "213800Y2XLTQMHLB5J34", "isin": "SE0000667891",
                "company_name": "Sandvik AB", "legal_name": "Sandvik AB",
                "ticker": "SAND.ST", "country": "SE",
                "reporting_currency": "SEK"}

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="research_delta_test_")
        self._old_home = os.environ.get("THESIS_LEDGER_HOME")
        os.environ["THESIS_LEDGER_HOME"] = self._tmpdir
        self._old_resolve = rd.thesis_ledger.resolve_identity
        rd.thesis_ledger.resolve_identity = lambda query, **kw: dict(self.IDENTITY)

    def tearDown(self):
        rd.thesis_ledger.resolve_identity = self._old_resolve
        if self._old_home is None:
            os.environ.pop("THESIS_LEDGER_HOME", None)
        else:
            os.environ["THESIS_LEDGER_HOME"] = self._old_home
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _new_ledger(self):
        tl = rd.thesis_ledger
        key = tl.ledger_key(self.IDENTITY)
        led = tl.new_ledger(key, dict(self.IDENTITY), "Sandvik AB")
        tl.save_ledger(led)
        return led, key

    class _Args(object):
        company = "Sandvik AB"
        new = "-"
        against = None
        between = None
        country = None
        offline = True

    def test_no_ledger_at_all_is_no_baseline(self):
        with self.assertRaises(rd.NoBaselineError):
            rd._open_ledger("Sandvik AB", offline=True)

    def test_ledger_with_zero_decisions_is_no_baseline(self):
        self._new_ledger()
        led, _key, _ident = rd._open_ledger("Sandvik AB", offline=True)
        self.assertIsNone(rd.thesis_ledger.latest_decision(led))
        with self.assertRaises(rd.NoBaselineError):
            rd._cmd_new_vs_stored(self._Args())

    def test_between_both_orderings_against_the_real_store(self):
        tl = rd.thesis_ledger
        led, _key = self._new_ledger()
        rec1 = copy.deepcopy(dr._sandvik_fixture())
        d1 = tl.add_decision(led, rec1)
        rec2 = copy.deepcopy(dr._sandvik_fixture())
        rec2["verdict"] = "STRONG BUY"
        rec2["conviction"] = "HIGH"
        d2 = tl.add_decision(led, rec2)
        tl.save_ledger(led)

        class _Forward(self._Args):
            between = (d1["decision_id"], d2["decision_id"])

        class _Backward(self._Args):
            between = (d2["decision_id"], d1["decision_id"])

        forward = rd._cmd_between(_Forward())
        backward = rd._cmd_between(_Backward())
        self.assertEqual(forward["old_decision_id"], d1["decision_id"])
        self.assertEqual(forward["new_decision_id"], d2["decision_id"])
        self.assertEqual(forward["decision"]["verdict"]["direction"], "upgrade")
        self.assertEqual(backward["old_decision_id"], d2["decision_id"])
        self.assertEqual(backward["new_decision_id"], d1["decision_id"])
        self.assertEqual(backward["decision"]["verdict"]["direction"], "downgrade")
        # Both decisions were filed the same day in the fixture (as_of is
        # never mutated here), so no report can fall strictly between them.
        if forward["interval"]["status"] == "ok":
            self.assertEqual(forward["interval"]["reports_between"], 0)

    def test_latest_decision_is_the_default_baseline(self):
        tl = rd.thesis_ledger
        led, _key = self._new_ledger()
        tl.add_decision(led, copy.deepcopy(dr._sandvik_fixture()))
        rec2 = copy.deepcopy(dr._sandvik_fixture())
        rec2["verdict"] = "SELL"
        d2 = tl.add_decision(led, rec2)
        tl.save_ledger(led)

        new_rec = copy.deepcopy(dr._sandvik_fixture())
        new_rec["verdict"] = "STRONG SELL"
        new_path = os.path.join(self._tmpdir, "new_decision.json")
        with open(new_path, "w", encoding="utf-8") as fh:
            json.dump(new_rec, fh)

        class _Args(self._Args):
            new = new_path

        delta = rd._cmd_new_vs_stored(_Args())
        self.assertEqual(delta["old_decision_id"], d2["decision_id"])
        self.assertEqual(delta["decision"]["verdict"]["direction"], "downgrade")

    def test_superseded_decision_is_surfaced_when_diffed_via_against(self):
        tl = rd.thesis_ledger
        led, _key = self._new_ledger()
        d1 = tl.add_decision(led, copy.deepcopy(dr._sandvik_fixture()))
        rec2 = copy.deepcopy(dr._sandvik_fixture())
        rec2["verdict"] = "STRONG BUY"
        d2 = tl.add_decision(led, rec2)
        self.assertTrue(tl.supersede_decision(led, d1["decision_id"], by_id=d2["decision_id"]))
        tl.save_ledger(led)

        new_rec = copy.deepcopy(dr._sandvik_fixture())
        new_path = os.path.join(self._tmpdir, "new_decision.json")
        with open(new_path, "w", encoding="utf-8") as fh:
            json.dump(new_rec, fh)

        class _Args(self._Args):
            against = d1["decision_id"]
            new = new_path

        delta = rd._cmd_new_vs_stored(_Args())
        self.assertTrue(any(n["which"] == "old" and n["by"] == d2["decision_id"]
                            for n in delta["superseded"]))


class TestNoBaselineYet(unittest.TestCase):
    """The expected state on a first run must read as "no baseline yet", not
    as a crash or a generic error."""

    def test_open_ledger_raises_no_baseline_when_thesis_ledger_missing(self):
        saved = rd.thesis_ledger
        rd.thesis_ledger = None
        try:
            with self.assertRaises(rd.NoBaselineError):
                rd._open_ledger("Sandvik AB", offline=True)
        finally:
            rd.thesis_ledger = saved

    def test_decisions_api_degrades_to_all_none_when_thesis_ledger_missing(self):
        saved = rd.thesis_ledger
        rd.thesis_ledger = None
        try:
            self.assertEqual(rd._decisions_api(), (None, None, None))
        finally:
            rd.thesis_ledger = saved

    def test_new_vs_stored_reports_no_baseline_without_a_decisions_store(self):
        # thesis_ledger.py as of this writing (and possibly still, depending
        # on how far the parallel decisions-store work has landed) does not
        # expose list_decisions/latest_decision/get_decision. Either way,
        # this must degrade to NoBaselineError, never a crash - simulate a
        # ledger that opened fine but has no decisions store, without
        # touching the filesystem or the network.
        class _Args(object):
            company = "Sandvik AB"
            new = "-"
            against = None
            between = None
            country = None
            offline = True

        saved_open = rd._open_ledger
        rd._open_ledger = lambda *a, **kw: ({"ledger_key": "TEST"}, "TEST", {})
        try:
            with self.assertRaises(rd.NoBaselineError):
                rd._cmd_new_vs_stored(_Args())
        finally:
            rd._open_ledger = saved_open

    def test_between_reports_no_baseline_when_a_decision_id_is_missing(self):
        class _Args(object):
            company = "Sandvik AB"
            new = "-"
            against = None
            between = ("D1", "D2")
            country = None
            offline = True

        saved_open = rd._open_ledger
        rd._open_ledger = lambda *a, **kw: ({"ledger_key": "TEST"}, "TEST", {})
        saved_api = rd._decisions_api
        rd._decisions_api = lambda: (lambda led: [], None, lambda led, did: None)
        try:
            with self.assertRaises(rd.NoBaselineError):
                rd._cmd_between(_Args())
        finally:
            rd._open_ledger = saved_open
            rd._decisions_api = saved_api


class TestRenderWidth(unittest.TestCase):
    def _assert_all_lines_fit(self, delta):
        block = rd.render_human(delta)
        for line in block.splitlines():
            self.assertLessEqual(len(line), 88, msg=repr(line))

    def test_no_change(self):
        self._assert_all_lines_fit(rd.diff(_base(), _base()))

    def test_not_comparable_case(self):
        old, new = _base(), _base()
        new["price"] = dict(new["price"])
        new["price"]["currency"] = "EUR"
        self._assert_all_lines_fit(rd.diff(old, new))

    def test_worst_case_everything_changes_at_once(self):
        old, new = _base(), _base()
        new["verdict"] = "STRONG SELL"
        new["conviction"] = "VERY LOW"
        new["price"] = dict(new["price"])
        new["price"]["value"] = 999.99
        new["reason_codes"] = [
            {"code": "VENUE_MICROCAP", "severity": "WARN", "detail": "x"},
            {"code": "CONFLICT_UNRESOLVED", "severity": "BLOCK", "detail": "y"},
        ]
        new["assumptions"] = []
        new["fundamentals"] = {"revenue": 100.0, "ebit": 10.0, "ebit_margin": 0.10,
                               "net_debt": 50.0, "currency": "SEK"}
        self._assert_all_lines_fit(rd.diff(old, new))


if __name__ == "__main__":
    unittest.main()
