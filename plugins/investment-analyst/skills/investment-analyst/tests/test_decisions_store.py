#!/usr/bin/env python3
"""thesis_ledger.py's decisions store - the record that used to be forgotten.

WHAT WAS MISSING

SKILL.md section 9 specified a machine-comparable decision record, and no
script ever wrote one. The analysis was excellent and then it was gone: a past
verdict could only be recovered by re-running the analysis, which is not
guaranteed to reproduce the same call. Research delta, thesis health over
time, expectation calibration, monitoring and any track record at all were
blocked behind that one absence. v3.0.0 adds a fourth top-level key,
"decisions", to the same per-issuer ledger file.

WHAT THIS FILE PINS DOWN

  * validate-then-store, on SKILL.md section 9's own worked example
  * a record whose expected return disagrees with its own inputs is REFUSED
    and NOTHING is written - not even a ledger file for a new issuer
  * a record naming a different LEI never lands in this issuer's ledger
  * append-only: a second decision does not touch the first, both stay
    retrievable, and the newer one stands
  * --supersede marks and never deletes, and moves what "standing" means
  * a ledger written BEFORE this change (no "decisions" key at all) still
    loads, and every existing reader still works on it - the reason
    SCHEMA_VERSION stayed at 1 and no migration exists
  * decisions are NOT truncated. status_history is capped at 200 entries with
    a silent `del thesis["status_history"][:-200]`, which discards the oldest
    history without saying so; the whole longitudinal design rests on
    decisions never doing that, so the bound is tested past 200 both in
    memory and through a save/read round trip
  * an ambiguous company name is still refused, and refused BEFORE anything
    is written

OFFLINE, AND ISOLATED FROM THE REAL STORE

Every test runs with THESIS_LEDGER_HOME pointed at a throwaway temp directory
(the same override portfolio_review's tests use), so nothing here can read or
write the user's ~/.investment-analyst. resolve_identity() is replaced with a
fixed fake for the CLI tests, so no register is contacted and the identity
layer is exercised through the real code path (load_or_create -> ledger_key ->
read_ledger) rather than a parallel one.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers

helpers.bootstrap_path()
tl = helpers.load("thesis_ledger")

# thesis_ledger.py loads its own copy of decision_record.py, and helpers.load()
# hands out a FRESH module object per call - so a DecisionError raised inside
# the ledger is not the same class as one from a separately loaded
# decision_record. Everything here goes through tl's copy, which is the one
# the store actually raises.
dr = tl.decision_record
DecisionError = tl.DecisionError

SANDVIK_LEI = "213800Y2XLTQMHLB5J34"
KEY = "LEI-" + SANDVIK_LEI

SANDVIK_IDENTITY = {
    "lei": SANDVIK_LEI,
    "isin": "SE0000667891",
    "company_name": "Sandvik AB",
    "legal_name": "Sandvik AB",
    "ticker": "SAND.ST",
    "country": "SE",
    "organisation_number": "556000-3468",
    "reporting_currency": "SEK",
    "fiscal_year_end": "12-31",
    "identity_source": "test fake (no register was contacted)",
    "identity_confidence": 1.0,
}

VOLVO_CANDIDATES = [
    {"company_name": "AB Volvo", "tickers": ["VOLV A", "VOLV B"],
     "isins": ["SE0000115420"], "leis": ["549300HGV012CNC8JD22"],
     "organisation_numbers": ["556012-5790"]},
    {"company_name": "Volvo Car AB", "tickers": ["VOLCAR B"],
     "isins": ["SE0021628898"], "leis": ["5299000EAMGGBEYP7J33"],
     "organisation_numbers": ["556810-8988"]},
]


def fixture():
    """SKILL.md section 9's Sandvik example, fresh each time."""
    return dr._sandvik_fixture()


def new_sandvik_ledger():
    return tl.new_ledger(KEY, dict(SANDVIK_IDENTITY), "Sandvik")


class StoreCase(unittest.TestCase):
    """Base: a throwaway THESIS_LEDGER_HOME, and resolve_identity() faked.

    setUp/tearDown rather than a decorator so that a test which drives the CLI
    and a test which calls the API directly share exactly the same isolation.
    """

    #: query -> identity. Anything not listed resolves as ambiguous or
    #: unresolvable, so a test can never accidentally reach the network.
    IDENTITIES = {"sandvik": SANDVIK_IDENTITY}
    AMBIGUOUS = {"volvo": ("the brand is shared by 2 listed issuers with "
                           "different accounts", VOLVO_CANDIDATES)}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name
        # Records handed to --decide live in a SEPARATE directory: a stray
        # *.json inside the ledger home would show up in ledger_files() and
        # make "nothing was written" impossible to assert.
        self._rec_tmp = tempfile.TemporaryDirectory()
        self.records = self._rec_tmp.name
        self._old_home = os.environ.get("THESIS_LEDGER_HOME")
        os.environ["THESIS_LEDGER_HOME"] = self.home
        self._real_resolve = tl.resolve_identity
        tl.resolve_identity = self._fake_resolve
        self.addCleanup(self._restore)

    def _restore(self):
        tl.resolve_identity = self._real_resolve
        if self._old_home is None:
            os.environ.pop("THESIS_LEDGER_HOME", None)
        else:
            os.environ["THESIS_LEDGER_HOME"] = self._old_home
        self._tmp.cleanup()
        self._rec_tmp.cleanup()

    def _fake_resolve(self, query, country=None, offline=False, refresh=False):
        q = query.strip().lower()
        if q in self.AMBIGUOUS:
            reason, cands = self.AMBIGUOUS[q]
            raise tl.Ambiguous(reason, cands)
        if q in self.IDENTITIES:
            return dict(self.IDENTITIES[q])
        raise tl.Ambiguous("no issuer could be resolved for %r" % query, [])

    # -- helpers -----------------------------------------------------------

    def ledger_files(self):
        """Every ledger file in the isolated home, index.json excluded."""
        return sorted(f for f in os.listdir(self.home)
                      if f.endswith(".json") and f != "index.json")

    def run_cli(self, argv, stdin=None):
        """Drive main() the way the shell does. Returns (exit_code, out, err)."""
        old_argv, old_stdin = sys.argv, sys.stdin
        sys.argv = ["thesis_ledger.py"] + list(argv)
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = tl.main()
                except SystemExit as exc:          # argparse's ap.error()
                    code = exc.code
        finally:
            sys.argv, sys.stdin = old_argv, old_stdin
        return code, out.getvalue(), err.getvalue()

    def write_record(self, rec, name="decision.json"):
        path = os.path.join(self.records, name)
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        return path


# --------------------------------------------------------------------------

class ValidateThenStore(StoreCase):
    """The happy path, on the one fixture whose numbers SKILL.md states."""

    def test_the_worked_example_stores_and_keeps_its_arithmetic(self):
        led = new_sandvik_ledger()
        stored = tl.add_decision(led, fixture())

        self.assertEqual(stored["ledger_key"], KEY)
        self.assertTrue(stored["decision_id"].startswith(KEY + ":"),
                        stored["decision_id"])
        self.assertEqual(stored["verdict"], "BUY")
        self.assertEqual(stored["conviction"], "MEDIUM")
        # SKILL.md section 9 states +20.9% and +15%/+24% for this example.
        self.assertAlmostEqual(stored["expected_return"], 0.2086, places=3)
        self.assertAlmostEqual(stored["margin_of_safety"]["to_base_low"],
                               0.152381, places=3)
        self.assertAlmostEqual(stored["margin_of_safety"]["to_base_high"],
                               0.2426, places=3)
        self.assertEqual(led["decisions"], [stored])

    def test_a_decision_is_never_born_superseded_or_scored(self):
        """outcome is filled later, when the world has had time to disagree;
        superseded_by is set by --supersede, never at creation."""
        stored = tl.add_decision(new_sandvik_ledger(), fixture())
        self.assertIsNone(stored["superseded_by"])
        self.assertIsNone(stored["superseded_at"])
        self.assertIsNone(stored["outcome"])

    def test_the_caller_cannot_forge_the_stamps(self):
        """created / decision_id / ledger_key are stamped by the store. A
        record that arrives claiming to have been decided last year, under
        another issuer's key, must not keep those claims."""
        rec = fixture()
        rec["created"] = "1999-01-01T00:00:00Z"
        rec["decision_id"] = "LEI-somebodyelse:1999-01-01T00:00:00Z"
        rec["ledger_key"] = "LEI-somebodyelse"
        stored = tl.add_decision(new_sandvik_ledger(), rec)
        self.assertNotEqual(stored["created"], "1999-01-01T00:00:00Z")
        self.assertEqual(stored["ledger_key"], KEY)
        self.assertTrue(stored["decision_id"].startswith(KEY + ":"))

    def test_a_decision_is_storable_with_no_thesis_at_all(self):
        """The two objects are wired together loosely on purpose: a decision
        must not require a thesis to exist."""
        led = new_sandvik_ledger()
        self.assertEqual(led["theses"], [])
        stored = tl.add_decision(led, fixture())
        self.assertNotIn("thesis_ref", stored)

    def test_a_stored_decision_survives_the_round_trip_to_disk(self):
        led = new_sandvik_ledger()
        stored = tl.add_decision(led, fixture())
        tl.save_ledger(led)

        back = tl.read_ledger(KEY)
        self.assertEqual(len(back["decisions"]), 1)
        self.assertEqual(back["decisions"][0]["decision_id"],
                         stored["decision_id"])
        self.assertEqual(tl.latest_decision(back)["verdict"], "BUY")
        # and the block renders from what came off disk, not from memory
        block = dr.render_decision_block(back["decisions"][0])
        self.assertIn("BUY — MEDIUM CONVICTION", block)


class RefusedRecordsAreNotWritten(StoreCase):
    """A persisted number that disagrees with its own inputs is worse than no
    number. The refusal must therefore leave nothing behind at all."""

    def test_a_wrong_expected_return_is_refused(self):
        led = new_sandvik_ledger()
        bad = fixture()
        bad["expected_return"] = 0.35          # true value is +20.9%
        with self.assertRaises(DecisionError):
            tl.add_decision(led, bad)
        self.assertEqual(led.get("decisions"), [])

    def test_nothing_reaches_the_disk_when_a_record_is_refused(self):
        """Through the CLI, for a brand-new issuer: the refusal must not even
        create the ledger file. save_ledger() is reached only after
        add_decision() returns."""
        bad = fixture()
        bad["expected_return"] = 0.35
        path = self.write_record(bad, "bad.json")

        code, out, err = self.run_cli(["Sandvik", "--decide", path])
        self.assertNotEqual(code, 0)
        self.assertIn("REFUSED", err)
        self.assertEqual(self.ledger_files(), [],
                         "a refused decision created a ledger file: %s"
                         % self.ledger_files())

    def test_a_refusal_does_not_disturb_decisions_already_on_file(self):
        led = new_sandvik_ledger()
        good = tl.add_decision(led, fixture())
        tl.save_ledger(led)

        bad = fixture()
        bad["verdict"] = "MILDLY KEEN"
        path = self.write_record(bad, "bad.json")
        code, out, err = self.run_cli(["Sandvik", "--decide", path])
        self.assertNotEqual(code, 0)

        back = tl.read_ledger(KEY)
        self.assertEqual([d["decision_id"] for d in back["decisions"]],
                         [good["decision_id"]])

    def test_a_record_naming_another_issuer_is_refused(self):
        """A decision filed under the wrong issuer is the exact defect the LEI
        keying exists to prevent, and would be undetectable afterwards."""
        led = new_sandvik_ledger()
        wrong = fixture()
        wrong["identity"] = dict(wrong["identity"],
                                 lei="549300HGV012CNC8JD22")   # AB Volvo
        with self.assertRaises(DecisionError) as ctx:
            tl.add_decision(led, wrong)
        self.assertIn("LEI", str(ctx.exception))
        self.assertEqual(led.get("decisions"), [])

    def test_a_partial_identity_is_completed_from_the_ledger(self):
        """The ledger's identity came from resolve_identity(), which refuses an
        ambiguous name rather than guessing, so it is authoritative. A record
        that omits the LEI is completed from it rather than refused."""
        led = new_sandvik_ledger()
        rec = fixture()
        rec["identity"] = {"name": "Sandvik AB"}
        stored = tl.add_decision(led, rec)
        self.assertEqual(stored["identity"]["lei"], SANDVIK_LEI)
        self.assertEqual(stored["identity"]["isin"], "SE0000667891")

    def test_a_json_payload_that_is_not_an_object_is_refused(self):
        led = new_sandvik_ledger()
        for payload in ([fixture()], "BUY", 17, None):
            with self.assertRaises(DecisionError):
                tl.add_decision(led, payload)
        self.assertEqual(led.get("decisions"), [])

    def test_malformed_json_is_refused_by_the_cli_without_writing(self):
        path = os.path.join(self.records, "broken.json")
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json at all")
        code, out, err = self.run_cli(["Sandvik", "--decide", path])
        self.assertNotEqual(code, 0)
        self.assertIn("REFUSED", err)
        self.assertEqual(self.ledger_files(), [])


class AppendOnly(StoreCase):
    """A new decision never mutates or deletes a prior one."""

    def test_two_decisions_are_both_retrievable_and_the_newer_stands(self):
        led = new_sandvik_ledger()
        first = tl.add_decision(led, fixture())
        second_rec = fixture()
        second_rec["verdict"] = "HOLD"
        second = tl.add_decision(led, second_rec)

        self.assertEqual(len(led["decisions"]), 2)
        self.assertNotEqual(first["decision_id"], second["decision_id"])
        # the first is untouched
        self.assertEqual(tl.get_decision(led, first["decision_id"])["verdict"],
                         "BUY")
        self.assertEqual(tl.get_decision(led, second["decision_id"])["verdict"],
                         "HOLD")
        # newest first
        self.assertEqual([d["decision_id"] for d in tl.list_decisions(led)],
                         [second["decision_id"], first["decision_id"]])
        self.assertEqual(tl.latest_decision(led)["decision_id"],
                         second["decision_id"])

    def test_ids_stay_unique_and_ordered_inside_a_single_second(self):
        """created is stamped to the second, so several decisions recorded in
        one second would collide on "<key>:<created>". The suffix must keep
        them distinct AND keep string order chronological."""
        led = new_sandvik_ledger()
        ids = [tl.add_decision(led, fixture())["decision_id"] for _ in range(12)]
        self.assertEqual(len(set(ids)), 12)
        self.assertEqual(sorted(ids), ids,
                         "decision ids must sort in the order they were made")

    def test_limit_returns_the_newest_rows_and_zero_returns_none(self):
        led = new_sandvik_ledger()
        made = [tl.add_decision(led, fixture()) for _ in range(5)]
        newest = [d["decision_id"] for d in reversed(made)]
        self.assertEqual([d["decision_id"] for d in tl.list_decisions(led, limit=2)],
                         newest[:2])
        self.assertEqual(tl.list_decisions(led, limit=0), [])
        self.assertEqual(len(tl.list_decisions(led, limit=None)), 5)

    def test_get_decision_answers_none_rather_than_guessing(self):
        led = new_sandvik_ledger()
        tl.add_decision(led, fixture())
        self.assertIsNone(tl.get_decision(led, "LEI-nope:2026-01-01T00:00:00Z"))
        self.assertIsNone(tl.get_decision(led, ""))
        self.assertIsNone(tl.get_decision(led, None))


class Supersede(StoreCase):
    """Superseding is the only way a decision stops applying, and it never
    removes the record."""

    def setUp(self):
        StoreCase.setUp(self)
        self.led = new_sandvik_ledger()
        self.first = tl.add_decision(self.led, fixture())
        rec = fixture()
        rec["verdict"] = "TRIM"
        self.second = tl.add_decision(self.led, rec)

    def test_supersede_sets_superseded_by_and_keeps_the_record(self):
        self.assertTrue(tl.supersede_decision(
            self.led, self.first["decision_id"],
            by_id=self.second["decision_id"]))
        again = tl.get_decision(self.led, self.first["decision_id"])
        self.assertEqual(again["superseded_by"], self.second["decision_id"])
        self.assertIsNotNone(again["superseded_at"])
        self.assertEqual(len(self.led["decisions"]), 2,
                         "supersede must never delete")
        self.assertEqual(len(tl.list_decisions(self.led)), 2,
                         "a superseded decision is still listed")

    def test_superseding_the_standing_decision_changes_what_stands(self):
        self.assertEqual(tl.latest_decision(self.led)["decision_id"],
                         self.second["decision_id"])
        self.assertTrue(tl.supersede_decision(self.led,
                                              self.second["decision_id"]))
        self.assertEqual(tl.latest_decision(self.led)["decision_id"],
                         self.first["decision_id"])
        # withdrawn with no replacement stays distinguishable from replaced
        self.assertEqual(
            tl.get_decision(self.led, self.second["decision_id"])["superseded_by"],
            tl.WITHDRAWN)

    def test_when_every_decision_is_superseded_nothing_stands(self):
        tl.supersede_decision(self.led, self.first["decision_id"])
        tl.supersede_decision(self.led, self.second["decision_id"])
        self.assertIsNone(tl.latest_decision(self.led))
        self.assertEqual(len(tl.list_decisions(self.led)), 2)

    def test_superseding_twice_does_not_rewrite_the_first_supersession(self):
        tl.supersede_decision(self.led, self.first["decision_id"],
                              by_id=self.second["decision_id"])
        stamp = tl.get_decision(self.led, self.first["decision_id"])["superseded_at"]
        self.assertFalse(tl.supersede_decision(self.led,
                                               self.first["decision_id"]))
        self.assertEqual(
            tl.get_decision(self.led, self.first["decision_id"])["superseded_at"],
            stamp)
        self.assertEqual(
            tl.get_decision(self.led, self.first["decision_id"])["superseded_by"],
            self.second["decision_id"])

    def test_an_unknown_decision_id_returns_false_and_changes_nothing(self):
        self.assertFalse(tl.supersede_decision(self.led, "LEI-nope:2026-01-01"))
        self.assertIsNone(tl.latest_decision(self.led)["superseded_by"])

    def test_a_superseded_by_pointing_at_nothing_is_refused(self):
        """A dangling pointer is worse than no pointer."""
        with self.assertRaises(ValueError):
            tl.supersede_decision(self.led, self.first["decision_id"],
                                  by_id="LEI-nope:2026-01-01T00:00:00Z")
        self.assertIsNone(
            tl.get_decision(self.led, self.first["decision_id"])["superseded_by"])

    def test_a_decision_cannot_supersede_itself(self):
        with self.assertRaises(ValueError):
            tl.supersede_decision(self.led, self.first["decision_id"],
                                  by_id=self.first["decision_id"])


class BackwardCompatibility(StoreCase):
    """A ledger written before v3.0.0 has no "decisions" key at all.

    This is the whole reason SCHEMA_VERSION stayed at 1 and migrate() has
    nothing to do: the key is purely additive and every reader reaches it
    through `.get("decisions") or []`.
    """

    def legacy_ledger(self):
        """Exactly the pre-v3.0.0 top-level shape: no "decisions"."""
        breaker = tl.parse_breaker("ebit_margin < 15% for 2 consecutive years")
        return {
            "schema_version": 1,
            "ledger_key": KEY,
            "identity": dict(SANDVIK_IDENTITY),
            "aliases": ["Sandvik"],
            "created": "2025-01-02T09:00:00Z",
            "last_updated": "2025-01-02T09:00:00Z",
            "last_evaluated": None,
            "theses": [{
                "id": "T1",
                "thesis": "Mining aftermarket revenue keeps group EBIT margin "
                          "at or above 15% through the capex cycle.",
                "created": "2025-01-02T09:00:00Z",
                "created_as_of": "2025-01-02",
                "last_updated": "2025-01-02T09:00:00Z",
                "as_of": "2025-01-02",
                "status": "STABLE",
                "status_since": "2025-01-02T09:00:00Z",
                "status_confirmations": 1,
                "confidence": 0.6,
                "prior_confidence": 0.6,
                "key_metrics": ["ebit_margin"],
                "breakers": [breaker.to_dict()],
                "supporting_evidence": [],
                "contradicting_evidence": [],
                "notes": [],
                "active": True,
                "evaluation_count": 0,
                "status_history": [{"at": "2025-01-02T09:00:00Z",
                                    "as_of": "2025-01-02", "mode": "CURRENT",
                                    "status": "STABLE", "previous_status": None,
                                    "confidence": 0.6,
                                    "reason": "thesis created",
                                    "triggered": []}],
            }],
            "observations": {},
        }

    def write_legacy(self):
        led = self.legacy_ledger()
        tl._write_json(tl.ledger_path(KEY), led)
        return led

    def test_a_legacy_file_still_loads(self):
        self.write_legacy()
        led = tl.read_ledger(KEY)
        self.assertIsNotNone(led)
        self.assertNotIn("decisions", led,
                         "read_ledger must not silently rewrite an old file")
        self.assertEqual(len(led["theses"]), 1)

    def test_every_decisions_reader_copes_with_the_missing_key(self):
        led = self.write_legacy()
        self.assertEqual(tl.list_decisions(led), [])
        self.assertEqual(tl.list_decisions(led, limit=3), [])
        self.assertIsNone(tl.latest_decision(led))
        self.assertIsNone(tl.get_decision(led, "anything"))
        self.assertFalse(tl.supersede_decision(led, "anything"))

    def test_the_existing_thesis_readers_still_work_on_a_legacy_file(self):
        self.write_legacy()
        led = tl.read_ledger(KEY)

        self.assertEqual(tl.find_thesis(led, "T1")["status"], "STABLE")
        entry = tl._index_entry(led)
        self.assertEqual(entry["theses"], 1)
        self.assertEqual(entry["decisions"], 0)
        self.assertIsNone(entry["last_decision"])

        idx = tl.rebuild_index()
        self.assertIn(KEY, idx["companies"])

        code, out, _ = self.run_cli(["Sandvik", "--list", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["theses"][0]["id"], "T1")

        code, out, _ = self.run_cli(["Sandvik", "--list"])
        self.assertEqual(code, 0)
        self.assertIn("T1", out)

        code, out, _ = self.run_cli(["Sandvik", "--history", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["history"][0]["id"], "T1")

        code, out, _ = self.run_cli(["--all"])
        self.assertEqual(code, 0)

    def test_a_first_decision_lands_on_a_legacy_file_without_a_migration(self):
        self.write_legacy()
        led = tl.read_ledger(KEY)
        thesis_before = json.dumps(led["theses"], sort_keys=True)

        stored = tl.add_decision(led, fixture())
        tl.save_ledger(led)

        back = tl.read_ledger(KEY)
        self.assertEqual(back["schema_version"], 1,
                         "adding decisions must not bump the schema version")
        self.assertEqual(len(back["decisions"]), 1)
        self.assertEqual(back["decisions"][0]["decision_id"],
                         stored["decision_id"])
        self.assertEqual(json.dumps(back["theses"], sort_keys=True),
                         thesis_before,
                         "storing a decision must not touch the theses")

    def test_a_new_ledger_carries_an_empty_decisions_list(self):
        self.assertEqual(new_sandvik_ledger()["decisions"], [])


class NotTruncated(StoreCase):
    """status_history is capped at 200 with a silent `del [:-200]`, so a thesis
    that has changed status 200 times loses its oldest history without saying
    so. decisions must never do that: the track record IS the history."""

    def test_decisions_past_the_status_history_bound_are_all_kept(self):
        led = new_sandvik_ledger()
        made = [tl.add_decision(led, fixture())["decision_id"]
                for _ in range(205)]
        self.assertEqual(len(led["decisions"]), 205,
                         "decisions were truncated at %d" % len(led["decisions"]))
        self.assertEqual(len(set(made)), 205)
        # the very first one, the one a bound would eat, is still retrievable
        self.assertIsNotNone(tl.get_decision(led, made[0]))
        self.assertEqual(len(tl.list_decisions(led)), 205)

    def test_the_save_read_round_trip_does_not_drop_any_either(self):
        led = new_sandvik_ledger()
        made = [tl.add_decision(led, fixture())["decision_id"]
                for _ in range(205)]
        tl.save_ledger(led)
        back = tl.read_ledger(KEY)
        self.assertEqual(len(back["decisions"]), 205)
        self.assertEqual([d["decision_id"] for d in back["decisions"]], made,
                         "the on-disk order must stay the order they were made")

    def test_no_spill_file_is_written_while_decisions_are_uncapped(self):
        """decisions_spill_path() records the contract for a future bound -
        overflow is APPENDED there, never dropped. Nothing writes it today,
        and this test says so out loud so a silent cap cannot creep in."""
        led = new_sandvik_ledger()
        for _ in range(205):
            tl.add_decision(led, fixture())
        tl.save_ledger(led)
        self.assertFalse(os.path.exists(tl.decisions_spill_path(KEY)))


class ThesisAndDecisionAreWiredLoosely(StoreCase):
    """A decision records what the thesis said; the thesis is not touched, and
    neither object needs the other to exist."""

    def with_thesis(self, status="WARNING"):
        led = new_sandvik_ledger()
        led["theses"] = [{
            "id": "T1", "thesis": "EBIT margin holds at or above 15%.",
            "status": status, "active": True,
            "status_since": "2026-01-15T08:00:00Z",
            "as_of": "2026-06-30", "last_evaluated": "2026-07-01T06:00:00Z",
            "action": tl.ACTION[status], "key_metrics": ["ebit_margin"],
            "breakers": [], "notes": [],
        }]
        return led

    def test_the_decision_records_the_thesis_id_and_its_derived_status(self):
        led = self.with_thesis("WARNING")
        stored = tl.add_decision(led, fixture())
        ref = stored["thesis_ref"]
        self.assertEqual(ref["thesis_id"], "T1")
        self.assertEqual(ref["status"], "WARNING")
        self.assertEqual(ref["last_evaluated"], "2026-07-01T06:00:00Z")
        self.assertEqual(ref["active_theses"],
                         [{"thesis_id": "T1", "status": "WARNING"}])

    def test_storing_a_decision_does_not_modify_the_thesis(self):
        led = self.with_thesis()
        before = json.dumps(led["theses"], sort_keys=True)
        tl.add_decision(led, fixture())
        self.assertEqual(json.dumps(led["theses"], sort_keys=True), before)

    def test_the_worst_standing_thesis_is_the_one_named(self):
        """A reader needs to see the broken one, not an arbitrary one - and
        every active thesis is still listed."""
        led = self.with_thesis("CONFIRMED")
        led["theses"].append(dict(led["theses"][0], id="T2", status="BROKEN"))
        ref = tl.add_decision(led, fixture())["thesis_ref"]
        self.assertEqual(ref["thesis_id"], "T2")
        self.assertEqual(ref["status"], "BROKEN")
        self.assertEqual(len(ref["active_theses"]), 2)

    def test_a_retired_thesis_is_not_referenced(self):
        led = self.with_thesis()
        led["theses"][0]["active"] = False
        self.assertNotIn("thesis_ref", tl.add_decision(led, fixture()))

    def test_the_thesis_status_is_read_never_recomputed(self):
        """Recomputing would need the network, so a filing server being down
        would mean losing a decision. The stored status is copied as-is, even
        when it is plainly stale."""
        led = self.with_thesis("STABLE")
        led["theses"][0]["last_evaluated"] = "2019-01-01T00:00:00Z"
        ref = tl.add_decision(led, fixture())["thesis_ref"]
        self.assertEqual(ref["status"], "STABLE")
        self.assertEqual(ref["last_evaluated"], "2019-01-01T00:00:00Z")


class CommandLine(StoreCase):
    """--decide / --decisions / --decision-latest / --supersede."""

    def test_decide_stores_and_prints_the_rendered_block(self):
        path = self.write_record(fixture())
        code, out, err = self.run_cli(["Sandvik", "--decide", path])
        self.assertEqual(code, 0, err)
        self.assertIn("DECISION — SAND.ST", out)
        self.assertIn("BUY — MEDIUM CONVICTION", out)
        self.assertIn("+20.9%", out)

        led = tl.read_ledger(KEY)
        self.assertEqual(len(led["decisions"]), 1)

    def test_decide_reads_stdin(self):
        code, out, err = self.run_cli(["Sandvik", "--decide", "-", "--json"],
                                      stdin=json.dumps(fixture()))
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["stored"])
        self.assertEqual(payload["ledger_key"], KEY)
        self.assertTrue(payload["decision_id"].startswith(KEY + ":"))

    def test_decide_creates_the_ledger_for_an_issuer_with_no_thesis(self):
        self.assertEqual(self.ledger_files(), [])
        path = self.write_record(fixture())
        code, out, err = self.run_cli(["Sandvik", "--decide", path])
        self.assertEqual(code, 0, err)
        self.assertIn(KEY + ".json", self.ledger_files())

    def test_decisions_lists_newest_first_with_a_limit(self):
        led = new_sandvik_ledger()
        ids = []
        for verdict in ("BUY", "HOLD", "TRIM"):
            rec = fixture()
            rec["verdict"] = verdict
            ids.append(tl.add_decision(led, rec)["decision_id"])
        tl.save_ledger(led)

        code, out, err = self.run_cli(["Sandvik", "--decisions", "--json"])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual([d["verdict"] for d in payload["decisions"]],
                         ["TRIM", "HOLD", "BUY"])
        self.assertEqual(payload["count"], 3)
        self.assertEqual(payload["standing"], ids[-1])

        code, out, err = self.run_cli(["Sandvik", "--decisions", "--limit", "2",
                                       "--json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(len(json.loads(out)["decisions"]), 2)

        code, out, err = self.run_cli(["Sandvik", "--decisions"])
        self.assertEqual(code, 0, err)
        self.assertIn("TRIM", out)
        self.assertIn(ids[-1], out, "the id must be printed; --supersede needs it")

    def test_decisions_on_an_issuer_with_none_is_not_an_error(self):
        led = new_sandvik_ledger()
        tl.save_ledger(led)
        code, out, err = self.run_cli(["Sandvik", "--decisions"])
        self.assertEqual(code, 0, err)
        self.assertIn("No decisions recorded", out)

    def test_decision_latest_shows_the_standing_call(self):
        led = new_sandvik_ledger()
        tl.add_decision(led, fixture())
        rec = fixture()
        rec["verdict"] = "SELL"
        newer = tl.add_decision(led, rec)
        tl.save_ledger(led)

        code, out, err = self.run_cli(["Sandvik", "--decision-latest", "--json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["decision_id"], newer["decision_id"])

        code, out, err = self.run_cli(["Sandvik", "--decision-latest"])
        self.assertEqual(code, 0, err)
        self.assertIn("SELL", out)

    def test_decision_latest_says_so_when_nothing_stands(self):
        led = new_sandvik_ledger()
        only = tl.add_decision(led, fixture())
        tl.supersede_decision(led, only["decision_id"])
        tl.save_ledger(led)
        code, out, err = self.run_cli(["Sandvik", "--decision-latest"])
        self.assertEqual(code, 3)
        self.assertIn("superseded", out)

    def test_supersede_marks_the_decision_and_persists_it(self):
        led = new_sandvik_ledger()
        first = tl.add_decision(led, fixture())
        second = tl.add_decision(led, fixture())
        tl.save_ledger(led)

        code, out, err = self.run_cli(
            ["Sandvik", "--supersede", first["decision_id"],
             "--superseded-by", second["decision_id"], "--json"])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["superseded_by"], second["decision_id"])
        self.assertEqual(payload["standing"], second["decision_id"])

        back = tl.read_ledger(KEY)
        self.assertEqual(len(back["decisions"]), 2)
        self.assertEqual(
            tl.get_decision(back, first["decision_id"])["superseded_by"],
            second["decision_id"])

    def test_supersede_works_without_a_company_because_the_id_carries_it(self):
        led = new_sandvik_ledger()
        only = tl.add_decision(led, fixture())
        tl.save_ledger(led)
        code, out, err = self.run_cli(["--supersede", only["decision_id"]])
        self.assertEqual(code, 0, err)
        back = tl.read_ledger(KEY)
        self.assertEqual(
            tl.get_decision(back, only["decision_id"])["superseded_by"],
            tl.WITHDRAWN)

    def test_supersede_of_an_unknown_id_is_reported_not_guessed(self):
        led = new_sandvik_ledger()
        tl.add_decision(led, fixture())
        tl.save_ledger(led)
        code, out, err = self.run_cli(
            ["Sandvik", "--supersede", KEY + ":2001-01-01T00:00:00Z"])
        self.assertEqual(code, 3)
        back = tl.read_ledger(KEY)
        self.assertIsNone(back["decisions"][0]["superseded_by"])


class AmbiguousNamesAreStillRefused(StoreCase):
    """resolve_identity() refuses "Volvo" - two listed issuers with different
    accounts - and the decision commands must inherit that refusal, not route
    around it. A decision filed under a merged identity is unrecoverable."""

    def test_decide_on_an_ambiguous_name_refuses_and_writes_nothing(self):
        path = self.write_record(fixture())
        code, out, err = self.run_cli(["Volvo", "--decide", path])
        self.assertEqual(code, 2)
        self.assertIn("REFUSING TO OPEN A LEDGER", out)
        self.assertIn("AB Volvo", out)
        self.assertEqual(self.ledger_files(), [])

    def test_the_json_refusal_names_every_candidate(self):
        path = self.write_record(fixture())
        code, out, err = self.run_cli(["Volvo", "--decide", path, "--json"])
        self.assertEqual(code, 2)
        payload = json.loads(out)
        self.assertFalse(payload["resolved"])
        self.assertEqual(len(payload["candidates"]), 2)
        self.assertEqual(self.ledger_files(), [])

    def test_listing_decisions_for_an_ambiguous_name_also_refuses(self):
        code, out, err = self.run_cli(["Volvo", "--decisions"])
        self.assertEqual(code, 2)
        code, out, err = self.run_cli(["Volvo", "--decision-latest"])
        self.assertEqual(code, 2)

    def test_a_name_that_resolves_to_nothing_is_refused_too(self):
        path = self.write_record(fixture())
        code, out, err = self.run_cli(["Some US Ticker", "--decide", path])
        self.assertEqual(code, 2)
        self.assertEqual(self.ledger_files(), [])

    def test_a_ledger_key_still_cannot_be_a_display_name(self):
        with self.assertRaises(tl.Ambiguous):
            tl.ledger_key({"lei": tl.NA, "isin": tl.NA,
                           "company_name": "Volvo"})


if __name__ == "__main__":
    unittest.main()
