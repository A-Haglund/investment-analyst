#!/usr/bin/env python3
"""horizon.py - the holding horizon: next dated corporate event, or none.

Covers:
  - parse_date and select_upcoming as pure functions (malformed dates
    dropped, past events dropped, same-day counts as upcoming, sort order)
  - resolve_horizon's happy path via a dependency-injected fake ir_discovery
    (no real network call anywhere in this file)
  - the three degrade paths, each of which must produce DATA NOT AVAILABLE
    with a reason rather than a guessed duration: no matching company, a
    matched company with only past events, and the lookup itself raising -
    including SystemExit, which sibling scripts raise and which does NOT
    inherit from Exception (the guard in avanza_events() must catch both)
  - avanza_lookup's four distinct "no match" causes (search raised, no hit,
    low-confidence best hit, details fetch failed) must not collapse into
    one indistinguishable "not listed" reason - the true cause travels via
    ir_discovery's note()/_notes, and horizon.py must drain and surface it
  - an ambiguous brand match ("Volvo" -> Volvo B, over Volvo A and Volvo
    Car B) is disclosed, not silently resolved: the runners-up ride along
    on the result and format_text names them, not just a bare count
  - format_text renders both the happy path and the DATA NOT AVAILABLE path
    without raising, never invents a duration in either, and the happy path
    carries the tier-4 single-source provenance inline on the date itself
  - _selftest() raises on failure instead of relying on a bare `assert`
    (stripped under `python -O`), and main() turns --selftest's outcome
    into a real process exit code instead of ignoring it

All offline: horizon.py never imports ir_discovery.py or touches the network
unless resolve_horizon()/avanza_events() are called with `lookup=None` (the
production default), which every test here avoids by injecting a fake lookup
object instead. A separate @network class at the bottom exercises the real
Avanza route against live company names, gated the same way as the rest of
this suite.
"""
import datetime
import io
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load, network

horizon = load("horizon")

TODAY = datetime.date(2026, 9, 1)

RAW_EVENTS = [
    {"date": "2026-07-17", "type": "INTERIM_REPORT", "isConfirmed": True},   # past
    {"date": "2026-10-23", "type": "INTERIM_REPORT", "isConfirmed": True},
    {"date": "2026-09-22", "type": "EXTRAORDINARY_GENERAL_MEETING",
     "isConfirmed": True},
    {"date": "garbage", "type": "ANNUAL_REPORT", "isConfirmed": True},       # malformed
    {"date": "2027-02-04", "type": "ANNUAL_REPORT", "isConfirmed": False},
]


class FakeIR(object):
    """Stands in for ir_discovery.py's public surface that horizon.py uses.

    `note`, if given, is appended to `_notes` (an instance-level list, mirroring
    ir_discovery's module-level one) the moment avanza_lookup runs - this is how
    the real module records *why* it returned no match, and avanza_events() must
    drain exactly that to surface the true reason instead of a canned guess.

    `scores`, if given, maps a candidate's `name` to its score, so a fake ranked
    list can carry a winner and runners-up with distinct scores (a plain `score`
    would make every candidate tie).
    """

    def __init__(self, best=None, ranked=None, score=100.0, raises=None,
                note=None, scores=None):
        self._best = best
        self._ranked = ranked or []
        self._score = score
        self._raises = raises
        self._note = note
        self._scores = scores or {}
        self._notes = []

    def avanza_lookup(self, query, delay):
        if self._raises:
            raise self._raises
        if self._note:
            self._notes.append(self._note)
        return self._best, self._ranked

    def score_candidate(self, query, cand):
        return self._scores.get(cand.get("name"), self._score)


# --------------------------------------------------------------------------
# Pure functions
# --------------------------------------------------------------------------

class ParseDate(unittest.TestCase):
    def test_plain_iso_date(self):
        self.assertEqual(horizon.parse_date("2026-10-23"),
                         datetime.date(2026, 10, 23))

    def test_iso_datetime_is_truncated_to_the_date(self):
        self.assertEqual(horizon.parse_date("2026-10-23T00:00:00"),
                         datetime.date(2026, 10, 23))

    def test_none_and_garbage_both_return_none(self):
        self.assertIsNone(horizon.parse_date(None))
        self.assertIsNone(horizon.parse_date("not-a-date"))
        self.assertIsNone(horizon.parse_date(""))


class SelectUpcoming(unittest.TestCase):
    def test_past_events_dropped_malformed_dropped_sorted_ascending(self):
        up = horizon.select_upcoming(RAW_EVENTS, TODAY)
        self.assertEqual([e["date"] for e in up],
                         ["2026-09-22", "2026-10-23", "2027-02-04"])

    def test_nearest_event_keeps_its_type_and_confirmed_flag(self):
        up = horizon.select_upcoming(RAW_EVENTS, TODAY)
        self.assertEqual(up[0]["type"], "EXTRAORDINARY_GENERAL_MEETING")
        self.assertTrue(up[0]["confirmed"])
        self.assertFalse(up[-1]["confirmed"])

    def test_event_dated_exactly_today_counts_as_upcoming(self):
        up = horizon.select_upcoming(
            [{"date": TODAY.isoformat(), "type": "INTERIM_REPORT",
              "isConfirmed": True}], TODAY)
        self.assertEqual(len(up), 1)

    def test_empty_events_returns_empty(self):
        self.assertEqual(horizon.select_upcoming([], TODAY), [])
        self.assertEqual(horizon.select_upcoming(None, TODAY), [])


# --------------------------------------------------------------------------
# resolve_horizon - happy path
# --------------------------------------------------------------------------

class ResolveHorizonHappyPath(unittest.TestCase):
    def test_next_event_is_the_nearest_future_one(self):
        fake = FakeIR(best={"name": "Volvo", "ticker": "VOLV B",
                            "events": RAW_EVENTS})
        res = horizon.resolve_horizon("Volvo", as_of=TODAY, lookup=fake)
        self.assertTrue(res["available"])
        self.assertEqual(res["next_event"]["date"], "2026-09-22")
        self.assertEqual(res["matched_name"], "Volvo (VOLV B)")
        self.assertEqual(res["source"], horizon.SOURCE_NOTE)
        self.assertIsNone(res["reason"])

    def test_upcoming_list_is_capped_and_includes_the_next_event_first(self):
        fake = FakeIR(best={"name": "Volvo", "ticker": "VOLV B",
                            "events": RAW_EVENTS})
        res = horizon.resolve_horizon("Volvo", as_of=TODAY, lookup=fake)
        self.assertEqual(res["upcoming"][0], res["next_event"])
        self.assertLessEqual(len(res["upcoming"]), horizon.KEEP_UPCOMING)

    def test_matched_name_without_a_ticker_falls_back_to_the_bare_name(self):
        fake = FakeIR(best={"name": "SomeCo", "ticker": "", "events": RAW_EVENTS})
        res = horizon.resolve_horizon("SomeCo", as_of=TODAY, lookup=fake)
        self.assertEqual(res["matched_name"], "SomeCo")


# --------------------------------------------------------------------------
# resolve_horizon - degrade paths (DATA NOT AVAILABLE, never a guess)
# --------------------------------------------------------------------------

class ResolveHorizonDegradesHonestly(unittest.TestCase):
    def test_no_matching_company(self):
        fake = FakeIR(best=None, note=("Avanza search returned no listed equity "
                                       "for 'Not A Real Company'"))
        res = horizon.resolve_horizon("Not A Real Company", as_of=TODAY, lookup=fake)
        self.assertFalse(res["available"])
        self.assertIsNone(res["next_event"])
        self.assertIn("no listed equity", res["reason"])

    def test_matched_company_with_only_past_events(self):
        fake = FakeIR(best={"name": "Stale Co", "ticker": "STALE",
                            "events": [{"date": "2020-01-01",
                                       "type": "ANNUAL_REPORT",
                                       "isConfirmed": True}]})
        res = horizon.resolve_horizon("Stale Co", as_of=TODAY, lookup=fake)
        self.assertFalse(res["available"])
        self.assertIn("no future-dated event", res["reason"])

    def test_matched_company_with_no_events_at_all(self):
        fake = FakeIR(best={"name": "No Calendar Co", "ticker": "NOCAL", "events": []})
        res = horizon.resolve_horizon("No Calendar Co", as_of=TODAY, lookup=fake)
        self.assertFalse(res["available"])
        self.assertIn("no future-dated event", res["reason"])

    def test_lookup_raising_systemexit_degrades_instead_of_propagating(self):
        # Sibling scripts raise SystemExit for a network failure, and
        # SystemExit does NOT inherit from Exception - the guard in
        # avanza_events() must catch both, or this test fails with an
        # uncaught SystemExit instead of a clean DATA NOT AVAILABLE result.
        fake = FakeIR(raises=SystemExit("DATA NOT AVAILABLE: Avanza unreachable"))
        res = horizon.resolve_horizon("Anything", as_of=TODAY, lookup=fake)
        self.assertFalse(res["available"])
        self.assertIn("Avanza lookup failed", res["reason"])

    def test_lookup_raising_a_plain_exception_also_degrades(self):
        fake = FakeIR(raises=ValueError("boom"))
        res = horizon.resolve_horizon("Anything", as_of=TODAY, lookup=fake)
        self.assertFalse(res["available"])
        self.assertIn("Avanza lookup failed", res["reason"])

    def test_result_shape_is_stable_even_on_failure(self):
        """A caller (portfolio_review.py-style consumer) should be able to
        read every key, with the right value, regardless of which branch
        ran - not just find the key present, which a hardcoded key list
        checked against nothing but its own keys could satisfy by accident
        even after a real field went missing or silently changed shape."""
        fake = FakeIR(best=None, note="Avanza search returned no listed equity for 'X'")
        res = horizon.resolve_horizon("X", as_of=TODAY, lookup=fake)
        self.assertEqual(res["company_query"], "X")
        self.assertIsNone(res["matched_name"])
        self.assertIsNone(res["match_score"])
        self.assertEqual(res["contenders"], [])
        self.assertEqual(res["as_of"], TODAY.isoformat())
        self.assertFalse(res["available"])
        self.assertIsInstance(res["reason"], str)
        self.assertIn("no listed equity", res["reason"])
        self.assertIsNone(res["next_event"])
        self.assertEqual(res["upcoming"], [])
        self.assertEqual(res["source"], horizon.SOURCE_NOTE)
        self.assertEqual(res["tier_note"], horizon.TIER_NOTE)


class ResolveHorizonDistinguishesTheRealFailureReason(unittest.TestCase):
    """M8: ir_discovery's avanza_lookup returns (None, ...) for four distinct
    reasons - the search itself raised, no hit at all, the best hit scored
    too low to trust, and the per-instrument details fetch failed - and
    records which one actually happened via its module-level note()/_notes.
    Before this fix, avanza_events() never read _notes, so all four read as
    the same canned "not listed with enough confidence to trust" string -
    an Avanza network outage was reported as "this company is not listed",
    a false statement in the one field whose entire job is stating why.

    Confirmed these fail against the pre-fix code: with the old
    avanza_events() body, `res["reason"]` for every case below was the
    literal string "Avanza has no listed company matching 'Nope' with
    enough confidence to trust", regardless of which note() call preceded
    it. assertNotEqual and the specific substring checks below all failed
    on that code; resolve_horizon() also did not carry a `reason` computed
    from anything but the injected `company` name, so the two FakeIR
    instances below (different notes, same company) produced byte-identical
    reason strings."""

    def test_no_hit_and_details_fetch_failure_read_differently(self):
        no_hit = FakeIR(best=None,
                       note="Avanza search returned no listed equity for 'Nope'")
        network_fail = FakeIR(best=None, note="Avanza details failed - HTTP 503")
        res_no_hit = horizon.resolve_horizon("Nope", as_of=TODAY, lookup=no_hit)
        res_network = horizon.resolve_horizon("Nope", as_of=TODAY, lookup=network_fail)
        self.assertNotEqual(res_no_hit["reason"], res_network["reason"])
        self.assertIn("no listed equity", res_no_hit["reason"])
        self.assertIn("details failed", res_network["reason"])

    def test_search_itself_failing_is_surfaced_verbatim(self):
        fake = FakeIR(best=None, note="Avanza search failed - HTTP 500: url")
        res = horizon.resolve_horizon("Nope", as_of=TODAY, lookup=fake)
        self.assertIn("Avanza search failed", res["reason"])

    def test_low_confidence_best_hit_is_surfaced_verbatim(self):
        fake = FakeIR(best=None, note=("Avanza's closest match 'Volvo Cars "
                                      "Something' scored too low to trust"))
        res = horizon.resolve_horizon("Volvo Cars Something", as_of=TODAY, lookup=fake)
        self.assertIn("scored too low to trust", res["reason"])

    def test_with_no_note_at_all_a_generic_fallback_is_used_not_a_false_claim(self):
        """Defensive branch: a lookup object with no _notes to drain at all
        (not even an empty one recorded) must not fabricate the old "not
        listed with enough confidence" claim - it degrades to a generic,
        honestly-hedged message instead."""
        fake = FakeIR(best=None)
        res = horizon.resolve_horizon("Nope", as_of=TODAY, lookup=fake)
        self.assertIn("no match", res["reason"])


# --------------------------------------------------------------------------
# format_text
# --------------------------------------------------------------------------

class FormatText(unittest.TestCase):
    def test_happy_path_shows_the_date_type_confirmed_flag_and_tier_provenance(self):
        """Replaces a test that only asserted the ABSENCE of duration words
        format_text has no code path capable of emitting - it could not
        fail no matter what format_text printed. This asserts what a
        reader-facing render must actively contain, including the tier-4
        single-source provenance line that is the entire mitigation for
        M10 (source-registry.md's rule that a tier-4 figure's status must
        travel with it). Confirmed this fails against the pre-fix
        format_text: the old function never referenced `result["tier_note"]`
        at all (the key did not exist on the result dict either), so
        `self.assertIn(horizon.TIER_NOTE, text)` raised first on the
        missing attribute/constant and, once TIER_NOTE is stubbed in
        without wiring it into format_text, on a plain substring miss."""
        fake = FakeIR(best={"name": "Volvo", "ticker": "VOLV B", "events": RAW_EVENTS})
        res = horizon.resolve_horizon("Volvo", as_of=TODAY, lookup=fake)
        text = horizon.format_text(res)
        self.assertIn("2026-09-22", text)
        self.assertIn("extraordinary general meeting", text)
        self.assertIn("confirmed", text)
        self.assertIn(horizon.TIER_NOTE, text)

    def test_data_not_available_path_names_the_reason_and_never_guesses(self):
        fake = FakeIR(best=None)
        res = horizon.resolve_horizon("Nope", as_of=TODAY, lookup=fake)
        text = horizon.format_text(res)
        self.assertIn(horizon.NA, text)
        self.assertIn("What would settle the case", text)
        for guess_word in ("6-12 months", "6 to 12 months"):
            self.assertNotIn(guess_word, text)


class ResolveHorizonDisclosesAmbiguousMatches(unittest.TestCase):
    """M9: live, "Volvo" resolves to Volvo B (score 104) over Volvo A (103)
    and Volvo Car B (70) - the right issuer wins, but nothing told the
    reader Volvo Car was even in the running, so a reader cannot tell
    whether "Volvo" meant AB Volvo or Volvo Car. This is the same standard
    test_identity_ambiguity.py sets for the outright-refusal resolvers: a
    refusal - or here, a match that could plausibly be wrong - must expose
    the candidates it saw, not a bare count (or nothing at all).

    Confirmed these fail against the pre-fix code: avanza_events() returned
    a 4-tuple with no contenders at all, so `res["contenders"]` raised
    KeyError immediately, and format_text() had no "also matched" branch or
    match-score header for `assertIn` to find."""

    def _volvo_like(self):
        winner = {"name": "Volvo B", "ticker": "VOLV B", "events": RAW_EVENTS}
        runner_up = {"name": "Volvo A", "ticker": "VOLV A", "events": []}
        other_issuer = {"name": "Volvo Car B", "ticker": "VOLCAR B", "events": []}
        scores = {"Volvo B": 104.0, "Volvo A": 103.0, "Volvo Car B": 70.0}
        return FakeIR(best=winner, ranked=[winner, runner_up, other_issuer],
                     scores=scores)

    def test_contenders_are_carried_on_the_result_winner_excluded(self):
        fake = self._volvo_like()
        res = horizon.resolve_horizon("Volvo", as_of=TODAY, lookup=fake)
        self.assertEqual(res["matched_name"], "Volvo B (VOLV B)")
        names = [c["name"] for c in res["contenders"]]
        self.assertEqual(names, ["Volvo A", "Volvo Car B"])
        self.assertEqual(res["contenders"][0]["score"], 103.0)

    def test_format_text_names_the_contenders_not_just_a_count(self):
        fake = self._volvo_like()
        res = horizon.resolve_horizon("Volvo", as_of=TODAY, lookup=fake)
        text = horizon.format_text(res)
        self.assertIn("Volvo A", text)
        self.assertIn("Volvo Car B", text)
        self.assertIn("match score", text)
        self.assertIn("re-run", text)

    def test_an_unambiguous_match_prints_no_contenders_section(self):
        """Control: a clean single-hit match must not be cluttered with a
        contenders section that has nothing to disclose."""
        fake = FakeIR(best={"name": "Evolution", "ticker": "EVO", "events": RAW_EVENTS})
        res = horizon.resolve_horizon("Evolution", as_of=TODAY, lookup=fake)
        self.assertEqual(res["contenders"], [])
        text = horizon.format_text(res)
        self.assertNotIn("also matched", text)


# --------------------------------------------------------------------------
# The lazy-import path (production default, still offline: the fake module is
# installed via horizon._import_ir_discovery, not a real network call)
# --------------------------------------------------------------------------

class LazyImportPath(unittest.TestCase):
    def setUp(self):
        self._orig = horizon._import_ir_discovery

    def tearDown(self):
        horizon._import_ir_discovery = self._orig

    def test_default_lookup_none_uses_the_lazily_imported_module(self):
        fake_module = FakeIR(best={"name": "Volvo", "ticker": "VOLV B",
                                   "events": RAW_EVENTS})
        horizon._import_ir_discovery = lambda: fake_module
        res = horizon.resolve_horizon("Volvo", as_of=TODAY)
        self.assertTrue(res["available"])
        self.assertEqual(res["next_event"]["date"], "2026-09-22")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

class SelftestFlag(unittest.TestCase):
    def test_selftest_runs_clean(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ok = horizon._selftest()
        self.assertTrue(ok)
        self.assertIn("assertions passed", buf.getvalue())

    def test_main_selftest_flag_exits_zero_instead_of_ignoring_the_outcome(self):
        """main() now turns _selftest()'s True/raise outcome into a real
        process exit code (matching screen_digest.py's --selftest), instead
        of calling it and returning regardless of what happened. Confirmed
        this fails against the pre-fix main(), which called _selftest() and
        `return`ed unconditionally - assertRaises(SystemExit) found nothing
        raised."""
        buf = io.StringIO()
        old_argv = sys.argv
        sys.argv = ["horizon.py", "--selftest"]
        try:
            with redirect_stdout(buf):
                with self.assertRaises(SystemExit) as ctx:
                    horizon.main()
        finally:
            sys.argv = old_argv
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("assertions passed", buf.getvalue())


# --------------------------------------------------------------------------
# Live network checks - gated, opt-in (see helpers.network)
# --------------------------------------------------------------------------

@network
class LiveAvanzaChecks(unittest.TestCase):
    """Verified live 2026-09-01: Volvo B and KebNi (First North) both return
    a structured, dated, confirmed calendar. Re-running this later will see
    different dates as the calendar rolls forward - the assertions only check
    shape and that the next event is genuinely in the future, not a fixed
    date."""

    def test_large_cap_has_a_future_dated_event(self):
        res = horizon.resolve_horizon("Volvo")
        self.assertTrue(res["available"], res.get("reason"))
        self.assertGreaterEqual(res["next_event"]["date"], res["as_of"])

    def test_first_north_micro_cap_has_a_future_dated_event(self):
        res = horizon.resolve_horizon("KebNi")
        self.assertTrue(res["available"], res.get("reason"))
        self.assertGreaterEqual(res["next_event"]["date"], res["as_of"])

    def test_unmatchable_company_degrades_without_raising(self):
        res = horizon.resolve_horizon("Zzzznonexistentcompanyxyz123abc")
        self.assertFalse(res["available"])
        self.assertTrue(res["reason"])


if __name__ == "__main__":
    unittest.main()
