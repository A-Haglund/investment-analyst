#!/usr/bin/env python3
"""position_sizing telemetry on a decision record - collection, not learning.

WHY THIS FILE EXISTS

position_sizing.py already computes the Kelly-derived sizing call (raw Kelly,
the fractional cut, the uncertainty haircut, target vs current vs delta, the
action, the binding constraint) and its telemetry() function trims that down
to the compact slice worth persisting. Until now nothing carried that slice
onto the one durable, validated object this system keeps - decision_record.py
- so calibration.py had nothing to group a realized outcome by later.

THE FACT THAT DECIDES THE SHAPE OF THIS CHANGE

decision_record.validate() builds its normalised record with `out = dict(rec)`
- a shallow copy of whatever the caller handed in - and returns that, not a
freshly constructed dict naming only the fields it knows about. An unknown
top-level key such as `position_sizing` therefore SURVIVES validate() on its
own, with zero code change, unless something explicitly strips or rejects it.
That is what makes `position_sizing` additive rather than a schema change:
decision_record.py only needs to VALIDATE the field when it is present, never
to plumb it through.

WHAT THIS FILE PINS DOWN

  * a record with no position_sizing validates exactly as it did before this
    field existed - no new key on the output, no new warning
  * a record that carries a consistent position_sizing round-trips through
    validate() unchanged
  * a delta that disagrees with target - current is refused (the same
    free-arithmetic-only posture already applied to expected_return and
    margin_of_safety), and so is an action outside the seven tokens
    position_sizing.telemetry() actually emits
  * the field survives thesis_ledger.add_decision()'s validate-then-store path
    and a save/read round trip to disk
  * calibration.py groups realized outcomes by action and by binding_constraint,
    and counts decisions that carry no such telemetry SEPARATELY rather than
    folding them into a clean bucket - the same "a check that could not run is
    not checked" rule this codebase applies everywhere else

WHAT THIS FILE DOES NOT TEST, ON PURPOSE

Nothing here exercises position_sizing.py or kelly.py - the task that added
this coverage was explicit that neither file is touched, and this file only
needs position_sizing.telemetry()'s OUTPUT SHAPE (a dict with target, current,
delta, action, binding_constraint among other keys), which decision_record.py
and calibration.py both treat as an opaque dict. Nor does anything here test
that a score, cap or threshold changes as a result of stored telemetry -
calibration.py's own docstring commits to never building that feedback loop,
and this file has nothing to assert because there is nothing to find.

OFFLINE, AND ISOLATED FROM THE REAL STORE

The persistence test points THESIS_LEDGER_HOME at a throwaway temp directory,
the same override test_decisions_store.py uses, so nothing here can read or
write the user's real ~/.investment-analyst.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers

helpers.bootstrap_path()
dr = helpers.load("decision_record")
cal = helpers.load("calibration")


def fixture():
    """SKILL.md section 9's Sandvik example, fresh each time - the module's
    own canonical selftest fixture, used here as the baseline a record
    without position_sizing must still match exactly."""
    return dr._sandvik_fixture()


class ValidateWithoutPositionSizing(unittest.TestCase):
    """The field is OPTIONAL: a record that never mentions it must validate
    exactly as it did before this field existed."""

    def test_no_new_key_and_no_new_warning(self):
        rec, warns = dr.validate(fixture())
        self.assertNotIn("position_sizing", rec)
        self.assertEqual(warns, [])

    def test_matches_the_modules_own_fixture_round_trip(self):
        """A record with no position_sizing, validated twice (once via the
        baseline path with no code exercised here, once through this test's
        own call) must produce the identical normalised record."""
        baseline, _ = dr.validate(fixture())
        again, _ = dr.validate(fixture())
        self.assertEqual(baseline, again)
        self.assertNotIn("position_sizing", baseline)


class ValidateWithPositionSizing(unittest.TestCase):
    """Present and internally consistent: round-trips unchanged."""

    def test_round_trips_unchanged(self):
        rec = fixture()
        rec["position_sizing"] = {
            "action": "ADD", "target": 0.06, "current": 0.04, "delta": 0.02,
            "binding_constraint": "concentration_cap",
        }
        out, warns = dr.validate(rec)
        self.assertEqual(out["position_sizing"],
                         {"action": "ADD", "target": 0.06, "current": 0.04,
                          "delta": 0.02, "binding_constraint": "concentration_cap"})
        self.assertEqual(warns, [])

    def test_a_bare_action_with_no_numbers_is_accepted(self):
        """target/current/delta are each individually optional - only their
        MUTUAL consistency is checked, and only when all three are present."""
        rec = fixture()
        rec["position_sizing"] = {"action": "NO_BET"}
        out, _ = dr.validate(rec)
        self.assertEqual(out["position_sizing"]["action"], "NO_BET")

    def test_every_one_of_the_seven_action_tokens_is_accepted(self):
        for action in dr.POSITION_SIZING_ACTIONS:
            rec = fixture()
            rec["position_sizing"] = {"action": action}
            out, _ = dr.validate(rec)
            self.assertEqual(out["position_sizing"]["action"], action)


class InconsistentPositionSizingIsRefused(unittest.TestCase):
    """decision_record.py never recomputes the Kelly arithmetic - it only
    checks the one identity that is free: delta == target - current."""

    def test_a_disagreeing_delta_is_refused(self):
        rec = fixture()
        rec["position_sizing"] = {
            "action": "ADD", "target": 0.06, "current": 0.04, "delta": 0.5}
        with self.assertRaises(dr.DecisionError):
            dr.validate(rec)

    def test_a_delta_within_tolerance_of_rounding_is_accepted(self):
        """The same TOLERANCE_PP the module already applies to expected_return
        - display rounding must not trip the refusal."""
        rec = fixture()
        rec["position_sizing"] = {
            "action": "ADD", "target": 0.06, "current": 0.04, "delta": 0.0201}
        out, _ = dr.validate(rec)
        self.assertAlmostEqual(out["position_sizing"]["delta"], 0.0201)

    def test_an_unknown_action_token_is_refused(self):
        rec = fixture()
        rec["position_sizing"] = {"action": "BUY_MORE"}
        with self.assertRaises(dr.DecisionError):
            dr.validate(rec)

    def test_a_missing_action_is_refused(self):
        rec = fixture()
        rec["position_sizing"] = {"target": 0.06, "current": 0.04, "delta": 0.02}
        with self.assertRaises(dr.DecisionError):
            dr.validate(rec)

    def test_a_non_object_position_sizing_is_refused(self):
        rec = fixture()
        rec["position_sizing"] = "ADD"
        with self.assertRaises(dr.DecisionError):
            dr.validate(rec)

    def test_a_non_numeric_target_is_refused(self):
        rec = fixture()
        rec["position_sizing"] = {"action": "ADD", "target": "a lot"}
        with self.assertRaises(dr.DecisionError):
            dr.validate(rec)


class PersistsThroughTheStore(unittest.TestCase):
    """add_decision() validates then appends; the field must come back off
    disk exactly as it was validated, the same guarantee every other field on
    the record already has."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_home = os.environ.get("THESIS_LEDGER_HOME")
        os.environ["THESIS_LEDGER_HOME"] = self._tmp.name
        self.tl = helpers.load("thesis_ledger")
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old_home is None:
            os.environ.pop("THESIS_LEDGER_HOME", None)
        else:
            os.environ["THESIS_LEDGER_HOME"] = self._old_home
        self._tmp.cleanup()

    def _new_ledger(self):
        identity = {
            "lei": "213800Y2XLTQMHLB5J34", "isin": "SE0000667891",
            "company_name": "Sandvik AB", "legal_name": "Sandvik AB",
            "ticker": "SAND.ST", "country": "SE",
            "organisation_number": "556000-3468", "reporting_currency": "SEK",
        }
        return self.tl.new_ledger("LEI-213800Y2XLTQMHLB5J34", identity, "Sandvik")

    def test_survives_add_decision_and_a_save_read_round_trip(self):
        led = self._new_ledger()
        rec = self.tl.decision_record._sandvik_fixture()
        rec["position_sizing"] = {
            "action": "TRIM", "target": 0.02, "current": 0.05, "delta": -0.03,
            "binding_constraint": "uncertainty_haircut",
        }
        stored = self.tl.add_decision(led, rec)
        self.assertEqual(stored["position_sizing"]["action"], "TRIM")

        self.tl.save_ledger(led)
        back = self.tl.read_ledger("LEI-213800Y2XLTQMHLB5J34")
        reloaded = self.tl.latest_decision(back)
        self.assertEqual(reloaded["position_sizing"],
                         {"action": "TRIM", "target": 0.02, "current": 0.05,
                          "delta": -0.03, "binding_constraint": "uncertainty_haircut"})

    def test_a_decision_with_no_position_sizing_still_stores_cleanly(self):
        led = self._new_ledger()
        stored = self.tl.add_decision(led, self.tl.decision_record._sandvik_fixture())
        self.assertNotIn("position_sizing", stored)

    def test_an_inconsistent_delta_is_refused_before_anything_is_written(self):
        led = self._new_ledger()
        rec = self.tl.decision_record._sandvik_fixture()
        rec["position_sizing"] = {
            "action": "ADD", "target": 0.06, "current": 0.04, "delta": 9.0}
        with self.assertRaises(self.tl.DecisionError):
            self.tl.add_decision(led, rec)
        self.assertEqual(led.get("decisions"), [])


class CalibrationGroupsBySizingTelemetry(unittest.TestCase):
    """calibration.py's report: distribution of realized outcomes by action
    and by binding_constraint, plus a separate not-checked count for
    decisions carrying no telemetry at all. Purely descriptive - these tests
    assert grouping and counting, never a feedback path."""

    def _matured(self, **extra):
        base = {"producer": "analyze", "conviction": "MEDIUM", "verdict": "BUY",
                "expected_return": 0.10, "as_of": "2026-01-01",
                "outcome": {"3m": {"matured": True, "realized_return": 0.15,
                                   "superseded": {"excluded_from_scoring": False},
                                   "scenario": {"available": False}}}}
        base.update(extra)
        return base

    def test_a_decision_with_telemetry_is_counted_and_bucketed(self):
        d = self._matured(position_sizing={
            "action": "INITIATE", "binding_constraint": "confidence_floor",
            "target": 0.05, "current": 0.0, "delta": 0.05})
        report = cal.build_calibration_report([d], "3m", min_n=1)
        st = report["producers"]["analyze"]["sizing_telemetry"]
        self.assertEqual(st["n_with_telemetry"], 1)
        self.assertEqual(st["n_without_telemetry"], 0)
        self.assertEqual(st["by_action"]["INITIATE"]["n"], 1)
        self.assertAlmostEqual(st["by_action"]["INITIATE"]["mean_realized"], 0.15)
        self.assertEqual(st["by_binding_constraint"]["confidence_floor"]["n"], 1)

    def test_a_decision_without_telemetry_is_not_checked_not_folded_in(self):
        d = self._matured()
        report = cal.build_calibration_report([d], "3m", min_n=1)
        st = report["producers"]["analyze"]["sizing_telemetry"]
        self.assertEqual(st["n_with_telemetry"], 0)
        self.assertEqual(st["n_without_telemetry"], 1)
        # every action bucket stays empty - the untelemetered decision must
        # not land in any of them by default.
        for action in dr.POSITION_SIZING_ACTIONS:
            self.assertEqual(st["by_action"][action]["n"], 0)
        self.assertEqual(st["by_binding_constraint"], {})

    def test_mixed_decisions_split_cleanly_between_the_two_counts(self):
        with_telemetry = self._matured(position_sizing={
            "action": "ADD", "binding_constraint": "concentration_cap",
            "target": 0.06, "current": 0.04, "delta": 0.02})
        without_telemetry = self._matured(as_of="2026-02-01")
        report = cal.build_calibration_report(
            [with_telemetry, without_telemetry], "3m", min_n=1)
        st = report["producers"]["analyze"]["sizing_telemetry"]
        self.assertEqual(st["n_with_telemetry"], 1)
        self.assertEqual(st["n_without_telemetry"], 1)
        self.assertEqual(st["by_action"]["ADD"]["n"], 1)


if __name__ == "__main__":
    unittest.main()
