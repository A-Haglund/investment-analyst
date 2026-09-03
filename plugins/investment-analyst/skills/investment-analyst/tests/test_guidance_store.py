#!/usr/bin/env python3
"""guidance_track.py's persistence layer, added in v3.0.0 (spec: BUILD AGENT
A5 - GUIDANCE MEMORY).

Before this store existed, guidance_track.py rebuilt everything from scratch
on every run: no home directory, no schema, nothing survived the process
exiting. The governing rule for what got added is "persist only what cannot
be re-derived" - judge()'s MET/MISS/NOT-COMPARABLE verdict is arithmetic on
reported figures (this file's own docstring calls it "a fact") and is never
written to disk; the guidance STATEMENT itself is not durably re-derivable,
because mfn_archive() reaches deep history only through an undocumented
endpoint that could stop working at any time, and cision_archive() is
best-effort by construction. So the store keeps statements, dated to the
release that carried them, and always re-derives the verdict fresh.

This file covers, entirely offline with synthetic statements:
  * round-trip save/load
  * idempotency: the same release merged twice produces one row, not two
  * a revision appended and linked both ways, with the original statement
    still retrievable unmodified
  * no truncation at any bound (thesis_ledger.py's status_history caps at
    200 and silently replaces values in place - this store must repeat
    neither defect)
  * the adjusted-vs-IFRS basis surviving the round trip
  * the shallow-archive-fallback caveat being recorded per statement and
    per fetch, and surfaced to a reader
  * identity keyed on LEI/ISIN, never a display name - a name-only issuer is
    refused, not stored under its typed name
  * the management execution score being byte-for-byte unchanged when the
    stored-history feature is never engaged

Isolation: every test that touches disk points GUIDANCE_STORE_HOME at a
throwaway tempfile.TemporaryDirectory(), the same technique
test_portfolio_store.py uses for PORTFOLIO_STORE_HOME - no test in this file
touches a real ~/.investment-analyst. company_resolve.py and
esef_fundamentals.py are monkeypatched at the attribute level (not replaced
wholesale) so identity-resolution tests exercise the REAL exception classes
(gt.CR.Ambiguous, gt.CR.NotFound) guidance_track.py's except clauses catch,
never a parallel fake type.
"""
import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

gt = load("guidance_track")


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

SANDVIK_LEI = "5493004QAI1UOX9SR347"
SANDVIK_ISIN = "SE0000667891"


def _row(**kw):
    """A collapsed guidance-history row, the shape collapse() produces and
    run() attaches _stmt_id/_from_store to - see guidance_row_to_statement()
    and stored_statement_to_history_row()."""
    base = {
        "date": "2025-07-17", "first_said": "2025-07-17", "metric": "ebitda_margin",
        "quant": {"kind": "range", "low": 66.0, "high": 68.0, "unit": "%"},
        "applies_to": "FY2025", "period_inferred": False, "withdrawn": False,
        "adjusted_basis": True, "title": "Q2 2025 report",
        "url": "https://mfn.se/a/sandvik/q2-2025",
        "sentence": "We expect an adjusted EBITDA margin of 66-68 percent for 2025.",
        "repeated_on": [],
    }
    base.update(kw)
    return base


def _archive_meta(deep=True, note=""):
    return {"channel": "MFN", "deep": deep,
           "note": note or ("MFN /all/a.json deep archive worked for this run; "
                            "up to 500 releases were requested.")}


def _identity():
    return {"lei": SANDVIK_LEI, "isin": SANDVIK_ISIN, "company_name": "Sandvik AB",
           "legal_name": "Sandvik Aktiebolag"}


@contextlib.contextmanager
def isolated_store():
    """Point guidance_store_home() at a throwaway directory for the life of
    the with-block, so no test here ever touches a real
    ~/.investment-analyst. Same technique as test_portfolio_store.py's
    isolated_store() for PORTFOLIO_STORE_HOME."""
    with tempfile.TemporaryDirectory() as tmp:
        old = os.environ.get("GUIDANCE_STORE_HOME")
        os.environ["GUIDANCE_STORE_HOME"] = tmp
        try:
            yield tmp
        finally:
            if old is None:
                os.environ.pop("GUIDANCE_STORE_HOME", None)
            else:
                os.environ["GUIDANCE_STORE_HOME"] = old


# --------------------------------------------------------------------------

class StatementIdentityKey(unittest.TestCase):
    """_statement_id(): the idempotency key every merge decision hangs on."""

    def test_identical_inputs_produce_identical_id(self):
        a = gt._statement_id("https://mfn.se/a/x/1", "ebitda_margin", "FY2025",
                             "We expect 66-68 percent.")
        b = gt._statement_id("https://mfn.se/a/x/1", "ebitda_margin", "FY2025",
                             "We expect 66-68 percent.")
        self.assertEqual(a, b)

    def test_whitespace_and_case_in_sentence_do_not_change_the_id(self):
        a = gt._statement_id("https://mfn.se/a/x/1", "ebitda_margin", "FY2025",
                             "We expect 66-68 percent.")
        b = gt._statement_id("https://mfn.se/a/x/1", "ebitda_margin", "FY2025",
                             "  We   EXPECT 66-68 percent.  ")
        self.assertEqual(a, b)

    def test_a_different_sentence_earns_a_different_id(self):
        a = gt._statement_id("https://mfn.se/a/x/1", "ebitda_margin", "FY2025",
                             "We expect 66-68 percent.")
        b = gt._statement_id("https://mfn.se/a/x/1", "ebitda_margin", "FY2025",
                             "We expect 70-72 percent.")
        self.assertNotEqual(a, b)

    def test_a_different_url_earns_a_different_id(self):
        a = gt._statement_id("https://mfn.se/a/x/1", "ebitda_margin", "FY2025", "same text")
        b = gt._statement_id("https://mfn.se/a/x/2", "ebitda_margin", "FY2025", "same text")
        self.assertNotEqual(a, b)


class IdentityKeying(unittest.TestCase):
    """guidance_store_key(): LEI first, ISIN second, never a name."""

    def test_lei_wins_when_both_present(self):
        key = gt.guidance_store_key({"lei": SANDVIK_LEI, "isin": SANDVIK_ISIN})
        self.assertEqual(key, "LEI-" + SANDVIK_LEI)

    def test_isin_used_when_no_lei(self):
        key = gt.guidance_store_key({"lei": None, "isin": SANDVIK_ISIN})
        self.assertEqual(key, "ISIN-" + SANDVIK_ISIN)

    def test_the_na_sentinel_is_treated_as_absent(self):
        key = gt.guidance_store_key({"lei": gt.CR.NA, "isin": SANDVIK_ISIN})
        self.assertEqual(key, "ISIN-" + SANDVIK_ISIN)

    def test_name_only_identity_is_refused_not_keyed(self):
        key = gt.guidance_store_key({"company_name": "Some Company AB",
                                     "lei": None, "isin": None})
        self.assertIsNone(key)

    def test_none_identity_is_refused(self):
        self.assertIsNone(gt.guidance_store_key(None))


class ResolveStoreIdentity(unittest.TestCase):
    """resolve_store_identity(): company_resolve.py first, ESEF's own filing
    index as a fallback, refusal (never a guess) when neither answers.

    Monkeypatches the REAL company_resolve/esef_fundamentals modules
    guidance_track.py imported as CR/ESEF - restored in tearDown - so the
    except clauses in resolve_store_identity() catch the actual exception
    types, not a parallel fake."""

    def setUp(self):
        self._real_cr_resolve = gt.CR.resolve
        self._real_esef_search = gt.ESEF.search_index

    def tearDown(self):
        gt.CR.resolve = self._real_cr_resolve
        gt.ESEF.search_index = self._real_esef_search

    def test_clean_company_resolve_hit_is_used(self):
        gt.CR.resolve = lambda name, country=None, use_cache=True: {
            "lei": SANDVIK_LEI, "isin": SANDVIK_ISIN, "company_name": "Sandvik AB",
            "legal_name": "Sandvik Aktiebolag"}
        ident, note = gt.resolve_store_identity("Sandvik AB", "Sandvik", "SE")
        self.assertIsNone(note)
        self.assertEqual(ident["lei"], SANDVIK_LEI)
        self.assertEqual(gt.guidance_store_key(ident), "LEI-" + SANDVIK_LEI)

    def test_ambiguous_brand_is_refused_with_every_candidate_named(self):
        def fake_resolve(name, country=None, use_cache=True):
            raise gt.CR.Ambiguous(name, "two distinct issuers share this brand",
                                  [{"company_name": "AB Volvo"},
                                   {"company_name": "Volvo Car AB"}])
        gt.CR.resolve = fake_resolve
        ident, note = gt.resolve_store_identity(None, "Volvo", "SE")
        self.assertIsNone(ident)
        self.assertIn("GUIDANCE_STORE_IDENTITY_AMBIGUOUS", note)
        self.assertIn("AB Volvo", note)
        self.assertIn("Volvo Car AB", note)

    def test_not_found_falls_back_to_esef_filing_index(self):
        def fake_resolve(name, country=None, use_cache=True):
            raise gt.CR.NotFound(name)
        gt.CR.resolve = fake_resolve
        gt.ESEF.search_index = lambda name, country: [
            {"lei": SANDVIK_LEI, "name": "Sandvik AB", "latest": "2025-12-31",
             "country": "SE"}]
        ident, note = gt.resolve_store_identity(None, "Sandvik", "SE")
        self.assertIsNone(note)
        self.assertEqual(ident["lei"], SANDVIK_LEI)
        self.assertIsNone(ident["isin"])

    def test_name_only_issuer_is_refused_when_no_engine_answers(self):
        def fake_resolve(name, country=None, use_cache=True):
            raise gt.CR.NotFound(name)
        gt.CR.resolve = fake_resolve
        gt.ESEF.search_index = lambda name, country: []
        ident, note = gt.resolve_store_identity(None, "Totally Unknown Co", "SE")
        self.assertIsNone(ident)
        self.assertIn("DATA NOT AVAILABLE", note)
        self.assertIsNone(gt.guidance_store_key(ident))

    def test_ambiguous_esef_fallback_is_also_refused(self):
        def fake_resolve(name, country=None, use_cache=True):
            raise gt.CR.NotFound(name)
        gt.CR.resolve = fake_resolve
        gt.ESEF.search_index = lambda name, country: [
            {"lei": "A", "name": "X AB", "latest": "2025-01-01", "country": "SE"},
            {"lei": "B", "name": "X Holding AB", "latest": "2025-01-01", "country": "SE"}]
        ident, note = gt.resolve_store_identity(None, "X", "SE")
        self.assertIsNone(ident)
        self.assertIn("GUIDANCE_STORE_IDENTITY_AMBIGUOUS", note)


class StoreRoundTrip(unittest.TestCase):
    """Basic save/load against an isolated GUIDANCE_STORE_HOME."""

    def test_load_of_never_saved_key_is_none(self):
        with isolated_store():
            self.assertIsNone(gt.guidance_store_load("LEI-" + "0" * 20))

    def test_save_then_load_round_trips_the_statement(self):
        with isolated_store():
            stmt = gt.guidance_row_to_statement(_row(), "MFN", _archive_meta())
            doc, added, revised = gt.guidance_store_merge(
                "LEI-" + SANDVIK_LEI, _identity(), "Sandvik",
                [stmt], {"run_utc": "2025-07-17T00:00:00Z", "venue": "MFN",
                        "deep_archive": True})
            self.assertEqual((added, revised), (1, 0))
            back = gt.guidance_store_load("LEI-" + SANDVIK_LEI)
            self.assertIsNotNone(back)
            self.assertEqual(back["schema_version"], gt.GUIDANCE_STORE_SCHEMA_VERSION)
            self.assertEqual(len(back["statements"]), 1)
            got = back["statements"][0]
            self.assertEqual(got["metric"], "ebitda_margin")
            self.assertEqual(got["quant"], {"kind": "range", "low": 66.0,
                                            "high": 68.0, "unit": "%"})
            self.assertEqual(got["applies_to"], "FY2025")
            self.assertEqual(got["source_url"], "https://mfn.se/a/sandvik/q2-2025")
            self.assertEqual(got["channel"], "MFN")
            self.assertIn("Sandvik", back["aliases"])

    def test_two_different_issuers_do_not_collide(self):
        with isolated_store():
            stmt_a = gt.guidance_row_to_statement(_row(), "MFN", _archive_meta())
            stmt_b = gt.guidance_row_to_statement(
                _row(url="https://mfn.se/a/other/q2-2025", metric="growth"),
                "MFN", _archive_meta())
            gt.guidance_store_merge("LEI-" + SANDVIK_LEI, _identity(), "Sandvik",
                                    [stmt_a], {"run_utc": "t", "venue": "MFN"})
            gt.guidance_store_merge("LEI-" + "1" * 20, {"lei": "1" * 20},
                                    "Other Co", [stmt_b], {"run_utc": "t", "venue": "MFN"})
            a = gt.guidance_store_load("LEI-" + SANDVIK_LEI)
            b = gt.guidance_store_load("LEI-" + "1" * 20)
            self.assertEqual(len(a["statements"]), 1)
            self.assertEqual(len(b["statements"]), 1)
            self.assertEqual(a["statements"][0]["metric"], "ebitda_margin")
            self.assertEqual(b["statements"][0]["metric"], "growth")


class Idempotency(unittest.TestCase):
    """Running the same, unchanged release through the store twice must not
    duplicate the row it produced the first time."""

    def test_same_statement_merged_twice_yields_one_row(self):
        with isolated_store():
            key = "LEI-" + SANDVIK_LEI
            stmt = gt.guidance_row_to_statement(_row(), "MFN", _archive_meta())
            gt.guidance_store_merge(key, _identity(), "Sandvik", [stmt],
                                    {"run_utc": "2025-07-17T00:00:00Z", "venue": "MFN"})
            doc2, added2, revised2 = gt.guidance_store_merge(
                key, _identity(), "Sandvik", [stmt],
                {"run_utc": "2025-08-01T00:00:00Z", "venue": "MFN"})
            self.assertEqual((added2, revised2), (0, 0))
            self.assertEqual(len(doc2["statements"]), 1)
            # two fetches were still logged - the fetch log is not deduped,
            # only the statements are
            self.assertEqual(len(doc2["fetch_log"]), 2)

    def test_a_plain_reiteration_from_a_new_release_is_not_a_duplicate_row(self):
        # collapse() itself would have merged a same-quant reiteration within
        # one run; a SEPARATE later run seeing the SAME release again
        # (identical url/metric/period/sentence) must still be a no-op here,
        # never a second row and never a spurious "revision".
        with isolated_store():
            key = "LEI-" + SANDVIK_LEI
            stmt = gt.guidance_row_to_statement(_row(), "MFN", _archive_meta())
            gt.guidance_store_merge(key, _identity(), "Sandvik", [stmt],
                                    {"run_utc": "t1", "venue": "MFN"})
            doc, added, revised = gt.guidance_store_merge(
                key, _identity(), "Sandvik", [dict(stmt)],
                {"run_utc": "t2", "venue": "MFN"})
            self.assertEqual((added, revised), (0, 0))
            self.assertEqual(len(doc["statements"]), 1)
            self.assertIsNone(doc["statements"][0]["supersedes"])


class RevisionLinking(unittest.TestCase):
    """A revised guidance number is APPENDED, never overwritten, and linked
    both ways to what it supersedes."""

    def test_a_cut_is_appended_and_linked_both_directions(self):
        with isolated_store():
            key = "LEI-" + SANDVIK_LEI
            first = gt.guidance_row_to_statement(_row(), "MFN", _archive_meta())
            gt.guidance_store_merge(key, _identity(), "Sandvik", [first],
                                    {"run_utc": "t1", "venue": "MFN"})

            cut_row = _row(quant={"kind": "range", "low": 63.0, "high": 65.0, "unit": "%"},
                          first_said="2025-10-20", date="2025-10-20",
                          url="https://mfn.se/a/sandvik/q3-2025",
                          sentence="We now expect an adjusted EBITDA margin of "
                                   "63-65 percent, down from our previous guidance.")
            second = gt.guidance_row_to_statement(cut_row, "MFN", _archive_meta())
            doc, added, revised = gt.guidance_store_merge(
                key, _identity(), "Sandvik", [second], {"run_utc": "t2", "venue": "MFN"})

            self.assertEqual((added, revised), (1, 1))
            self.assertEqual(len(doc["statements"]), 2)   # nothing overwritten

            by_id = {s["id"]: s for s in doc["statements"]}
            self.assertEqual(by_id[first["id"]]["superseded_by"], second["id"])
            self.assertEqual(by_id[second["id"]]["supersedes"], first["id"])

            # the ORIGINAL statement is still retrievable, completely intact
            original = by_id[first["id"]]
            self.assertEqual(original["quant"], {"kind": "range", "low": 66.0,
                                                 "high": 68.0, "unit": "%"})
            self.assertEqual(original["sentence"], first["sentence"])

            chain = gt.guidance_store_revision_chain(doc)
            self.assertEqual(len(chain), 1)
            self.assertEqual(chain[0]["direction"], "LOWERED")
            self.assertEqual(chain[0]["from"], "66-68%")
            self.assertEqual(chain[0]["to"], "63-65%")

    def test_a_widening_of_a_net_debt_ceiling_is_loosened_not_lowered(self):
        # net_debt_ebitda is in LOWER_IS_BETTER: a ceiling going UP (1.0x ->
        # 1.5x) is a cut in discipline (LOOSENED), the mirror image of a
        # margin floor being cut (LOWERED) - guidance_store_revision_chain()
        # must apply the same LOWER_IS_BETTER table detect_changes() uses.
        with isolated_store():
            key = "LEI-" + SANDVIK_LEI
            base = _row(metric="net_debt_ebitda", applies_to="through the cycle",
                       quant={"kind": "ceiling", "low": None, "high": 1.0, "unit": "x"},
                       sentence="Net debt/EBITDA should not exceed 1.0x through the cycle.")
            first = gt.guidance_row_to_statement(base, "MFN", _archive_meta())
            gt.guidance_store_merge(key, _identity(), "Sandvik", [first],
                                    {"run_utc": "t1", "venue": "MFN"})
            looser = dict(base, quant={"kind": "ceiling", "low": None, "high": 1.5, "unit": "x"},
                         first_said="2026-01-01", date="2026-01-01",
                         url="https://mfn.se/a/sandvik/q4-2025",
                         sentence="Net debt/EBITDA should not exceed 1.5x through the cycle.")
            second = gt.guidance_row_to_statement(looser, "MFN", _archive_meta())
            doc, added, revised = gt.guidance_store_merge(
                key, _identity(), "Sandvik", [second], {"run_utc": "t2", "venue": "MFN"})
            self.assertEqual((added, revised), (1, 1))
            chain = gt.guidance_store_revision_chain(doc)
            self.assertEqual(chain[0]["direction"], "LOOSENED")

    def test_withdrawn_guidance_is_a_revision_link_too(self):
        with isolated_store():
            key = "LEI-" + SANDVIK_LEI
            first = gt.guidance_row_to_statement(_row(), "MFN", _archive_meta())
            gt.guidance_store_merge(key, _identity(), "Sandvik", [first],
                                    {"run_utc": "t1", "venue": "MFN"})
            withdrawn_row = _row(withdrawn=True, quant=None, first_said="2025-11-01",
                                date="2025-11-01", url="https://mfn.se/a/sandvik/profit-warning",
                                sentence="We are withdrawing our full-year guidance.")
            second = gt.guidance_row_to_statement(withdrawn_row, "MFN", _archive_meta())
            doc, added, revised = gt.guidance_store_merge(
                key, _identity(), "Sandvik", [second], {"run_utc": "t2", "venue": "MFN"})
            self.assertEqual((added, revised), (1, 1))
            chain = gt.guidance_store_revision_chain(doc)
            self.assertEqual(chain[0]["direction"], "WITHDRAWN")


class NoTruncationAtAnyBound(unittest.TestCase):
    """thesis_ledger.py truncates status_history at 200 entries
    (`del thesis["status_history"][:-200]`) and silently replaces a
    re-observed value in place. This store must repeat neither defect."""

    def test_more_than_two_hundred_statements_are_all_kept(self):
        with isolated_store():
            key = "LEI-" + SANDVIK_LEI
            statements = []
            for i in range(210):
                row = _row(applies_to="FY%d" % (1800 + i), first_said="2020-01-01",
                          date="2020-01-01", url="https://mfn.se/a/sandvik/bulk-%d" % i,
                          sentence="bulk guidance statement number %d" % i)
                statements.append(gt.guidance_row_to_statement(row, "MFN", _archive_meta()))
            doc, added, _ = gt.guidance_store_merge(
                key, _identity(), "Sandvik", statements, {"run_utc": "t", "venue": "MFN"})
            self.assertEqual(added, 210)
            self.assertEqual(len(doc["statements"]), 210)
            back = gt.guidance_store_load(key)
            self.assertEqual(len(back["statements"]), 210)
            # every one of them, not just the most recent 200, survives a reload
            ids = {s["id"] for s in statements}
            self.assertEqual({s["id"] for s in back["statements"]}, ids)

    def test_revision_does_not_replace_the_old_value_in_place(self):
        with isolated_store():
            key = "LEI-" + SANDVIK_LEI
            first = gt.guidance_row_to_statement(_row(), "MFN", _archive_meta())
            gt.guidance_store_merge(key, _identity(), "Sandvik", [first],
                                    {"run_utc": "t1", "venue": "MFN"})
            for i, (lo, hi) in enumerate([(63, 65), (60, 62), (58, 60)]):
                vintage = "2025-08-%02d" % (i + 1)
                row = _row(quant={"kind": "range", "low": float(lo), "high": float(hi),
                                 "unit": "%"},
                          first_said=vintage, date=vintage,
                          url="https://mfn.se/a/sandvik/rev-%d" % i,
                          sentence="revision number %d to %d-%d percent." % (i, lo, hi))
                stmt = gt.guidance_row_to_statement(row, "MFN", _archive_meta())
                gt.guidance_store_merge(key, _identity(), "Sandvik", [stmt],
                                        {"run_utc": "t%d" % (i + 2), "venue": "MFN"})
            doc = gt.guidance_store_load(key)
            # first + 3 revisions = 4 distinct rows, every quant preserved
            self.assertEqual(len(doc["statements"]), 4)
            lows = sorted(s["quant"]["low"] for s in doc["statements"])
            self.assertEqual(lows, [58.0, 60.0, 63.0, 66.0])


class AdjustedBasisSurvivesRoundTrip(unittest.TestCase):
    """The adjusted/organic-vs-IFRS distinction guidance_track.py already
    computes (ADJUSTED_CUE / adjusted_basis) must not be lost in storage."""

    def test_adjusted_basis_true_round_trips(self):
        with isolated_store():
            key = "LEI-" + SANDVIK_LEI
            row = _row(adjusted_basis=True)
            stmt = gt.guidance_row_to_statement(row, "MFN", _archive_meta())
            gt.guidance_store_merge(key, _identity(), "Sandvik", [stmt],
                                    {"run_utc": "t", "venue": "MFN"})
            back = gt.guidance_store_load(key)
            got = back["statements"][0]
            self.assertTrue(got["adjusted_basis"])
            self.assertIn("adjusted", got["basis_label"])

    def test_adjusted_basis_false_round_trips(self):
        with isolated_store():
            key = "LEI-" + SANDVIK_LEI
            row = _row(adjusted_basis=False,
                      sentence="We expect an EBITDA margin of 66-68 percent for 2025.")
            stmt = gt.guidance_row_to_statement(row, "MFN", _archive_meta())
            gt.guidance_store_merge(key, _identity(), "Sandvik", [stmt],
                                    {"run_utc": "t", "venue": "MFN"})
            back = gt.guidance_store_load(key)
            got = back["statements"][0]
            self.assertFalse(got["adjusted_basis"])
            self.assertTrue(got["basis_label"].startswith("as stated"))

    def test_standing_target_carries_its_own_basis_too(self):
        target_row = {"metric": "ebita_margin",
                     "quant": {"kind": "range", "low": 20.0, "high": 22.0, "unit": "%"},
                     "horizon": "through the cycle", "adjusted_basis": True,
                     "url": "https://sandvik.com/en/investors/financial-targets",
                     "sentence": "Adjusted EBITA margin of 20-22 percent through the cycle."}
        stmt = gt.target_row_to_statement(target_row, {"channel": "IR", "deep": None,
                                                       "note": "n/a"})
        self.assertEqual(stmt["kind"], "target")
        self.assertTrue(stmt["standing"])
        self.assertTrue(stmt["adjusted_basis"])
        self.assertIsNone(stmt["vintage"])          # IR pages are undated - see module note
        self.assertIsNotNone(stmt["first_observed"])


class ArchiveDepthCaveat(unittest.TestCase):
    """The MFN-deep-vs-shallow (and Cision-is-always-best-effort) caveat
    must be recorded per statement AND per fetch, and a reader must be told
    when a history could be incomplete."""

    def test_deep_archive_statement_carries_no_caveat(self):
        stmt = gt.guidance_row_to_statement(_row(), "MFN", _archive_meta(deep=True))
        self.assertTrue(stmt["archive_depth"]["deep"])

    def test_shallow_fallback_is_recorded_on_the_statement(self):
        shallow = {
            "channel": "MFN", "deep": False,
            "note": ("MFN deep archive unavailable; only the ~30 most recent "
                    "releases were read, so older guidance is missing.")}
        stmt = gt.guidance_row_to_statement(_row(), "MFN", shallow)
        self.assertFalse(stmt["archive_depth"]["deep"])
        self.assertIn("older guidance is missing", stmt["archive_depth"]["note"])

    def test_shallow_fetch_is_surfaced_in_the_printed_history(self):
        with isolated_store():
            key = "LEI-" + SANDVIK_LEI
            shallow_meta = {"channel": "MFN", "deep": False,
                           "note": "MFN deep archive unavailable; only the ~30 "
                                   "most recent releases were read."}
            stmt = gt.guidance_row_to_statement(_row(), "MFN", shallow_meta)
            doc, _, _ = gt.guidance_store_merge(
                key, _identity(), "Sandvik", [stmt],
                {"run_utc": "t", "venue": "MFN", "deep_archive": False,
                 "archive_note": shallow_meta["note"]})
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                gt.print_guidance_store_history(doc, "Sandvik AB")
            out = buf.getvalue()
            self.assertIn("CAVEAT", out)
            self.assertIn("SHALLOW", out)

    def test_deep_fetch_prints_no_caveat_banner(self):
        with isolated_store():
            key = "LEI-" + SANDVIK_LEI
            stmt = gt.guidance_row_to_statement(_row(), "MFN", _archive_meta(deep=True))
            doc, _, _ = gt.guidance_store_merge(
                key, _identity(), "Sandvik", [stmt],
                {"run_utc": "t", "venue": "MFN", "deep_archive": True})
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                gt.print_guidance_store_history(doc, "Sandvik AB")
            self.assertNotIn("CAVEAT", buf.getvalue())

    def test_cision_targets_are_never_marked_deep(self):
        stmt = gt.guidance_row_to_statement(
            _row(), "Cision", {"channel": "Cision", "deep": False,
                              "note": "Cision archive is inherently best-effort."})
        self.assertFalse(stmt["archive_depth"]["deep"])
        self.assertEqual(stmt["channel"], "Cision")


class StoredHistoryConversionRoundTrip(unittest.TestCase):
    """guidance_row_to_statement() and stored_statement_to_history_row() are
    inverses of each other on every field the join/score pipeline reads."""

    def test_round_trip_preserves_what_judge_and_pick_actual_need(self):
        row = _row()
        stmt = gt.guidance_row_to_statement(row, "MFN", _archive_meta())
        back = gt.stored_statement_to_history_row(stmt)
        self.assertEqual(back["metric"], row["metric"])
        self.assertEqual(back["quant"], row["quant"])
        self.assertEqual(back["applies_to"], row["applies_to"])
        self.assertEqual(back["withdrawn"], row["withdrawn"])
        self.assertEqual(back["adjusted_basis"], row["adjusted_basis"])
        self.assertEqual(back["url"], row["url"])
        self.assertEqual(back["sentence"], row["sentence"])
        self.assertEqual(back["first_said"], row["first_said"])
        self.assertEqual(back["kind"], "guidance")
        self.assertTrue(back["_from_store"])
        self.assertEqual(back["_stmt_id"], stmt["id"])
        # no verdict/actual is carried - those are re-derived, never stored
        self.assertNotIn("verdict", back)
        self.assertNotIn("actual", back)

    def test_reaffirmed_on_round_trips_as_repeated_on(self):
        row = _row(repeated_on=["2025-08-01", "2025-09-01"])
        stmt = gt.guidance_row_to_statement(row, "MFN", _archive_meta())
        self.assertEqual(stmt["reaffirmed_on"], ["2025-08-01", "2025-09-01"])
        back = gt.stored_statement_to_history_row(stmt)
        self.assertEqual(back["repeated_on"], ["2025-08-01", "2025-09-01"])

    def test_standing_flag_set_for_a_through_the_cycle_guidance_row(self):
        row = _row(applies_to="through the cycle")
        stmt = gt.guidance_row_to_statement(row, "MFN", _archive_meta())
        self.assertTrue(stmt["standing"])

    def test_standing_flag_clear_for_ordinary_annual_guidance(self):
        stmt = gt.guidance_row_to_statement(_row(applies_to="FY2025"), "MFN", _archive_meta())
        self.assertFalse(stmt["standing"])


class IndexLookup(unittest.TestCase):
    """guidance_store_index_lookup(): offline alias resolution, the same
    shape thesis_ledger.index_lookup() provides."""

    def test_lookup_by_lei_isin_name_and_alias(self):
        with isolated_store():
            key = "LEI-" + SANDVIK_LEI
            stmt = gt.guidance_row_to_statement(_row(), "MFN", _archive_meta())
            gt.guidance_store_merge(key, _identity(), "Sandvik", [stmt],
                                    {"run_utc": "t", "venue": "MFN"})
            self.assertEqual(gt.guidance_store_index_lookup(SANDVIK_LEI), key)
            self.assertEqual(gt.guidance_store_index_lookup(SANDVIK_ISIN), key)
            self.assertEqual(gt.guidance_store_index_lookup("Sandvik AB"), key)
            self.assertEqual(gt.guidance_store_index_lookup("Sandvik"), key)
            self.assertEqual(gt.guidance_store_index_lookup("sandvik"), key)  # case-insensitive
            self.assertIsNone(gt.guidance_store_index_lookup("Totally Unrelated Co"))

    def test_lookup_against_an_empty_store_is_none(self):
        with isolated_store():
            self.assertIsNone(gt.guidance_store_index_lookup("Sandvik"))


class ExecutionScoreUnchangedWithoutStoredHistory(unittest.TestCase):
    """compute_execution_score() itself takes no store-related input at all;
    _execution_score_with_coverage() is a separate, additive step run() only
    calls when --use-history was passed. Verify both halves of that claim."""

    def _sample_history(self):
        actual = {"value": 67.0, "unit": "%", "basis": "as stated",
                  "source_line": "x", "source_url": "y", "reported_on": "2026-02-01"}
        rows = []
        for i in range(5):
            rows.append({
                "metric": "ebitda_margin", "applies_to": "FY202%d" % i,
                "quant": {"kind": "range", "low": 66.0, "high": 68.0, "unit": "%"},
                "withdrawn": False, "first_said": "202%d-07-17" % i,
                "actual": actual, "verdict": "MET (in range)", "url": "u", "sentence": "s",
                "repeated_on": [],
            })
        return rows

    def test_compute_execution_score_has_no_coverage_key_by_default(self):
        history = self._sample_history()
        score = gt.compute_execution_score(history, [], [], [], [])
        self.assertNotIn("coverage_statement", score["facts"])

    def test_not_scorable_path_also_has_no_coverage_key(self):
        score = gt.compute_execution_score([], [], [], [], [])
        self.assertIsNone(score["score"])
        self.assertNotIn("coverage_statement", score["facts"])

    def test_coverage_wrapper_is_purely_additive(self):
        history = self._sample_history()
        base = gt.compute_execution_score(history, [], [], [], [])
        augmented = gt._execution_score_with_coverage(base, history, "LEI-" + SANDVIK_LEI)
        # every key/value compute_execution_score() itself produced survives untouched
        for k, v in base.items():
            if k == "facts":
                for fk, fv in v.items():
                    self.assertEqual(augmented["facts"][fk], fv)
            else:
                self.assertEqual(augmented[k], v)
        self.assertIn("coverage_statement", augmented["facts"])
        self.assertIn("stored history", augmented["facts"]["coverage_statement"])
        # the original dict returned by compute_execution_score() is not mutated
        self.assertNotIn("coverage_statement", base["facts"])

    def test_coverage_statement_counts_stored_rows_separately(self):
        history = self._sample_history()
        history[0]["_from_store"] = True
        history[1]["_from_store"] = True
        augmented = gt._execution_score_with_coverage(
            gt.compute_execution_score(history, [], [], [], []), history, "LEI-x")
        self.assertIn("of which 2 from stored history", augmented["facts"]["coverage_statement"])
        self.assertIn("5 statement(s)", augmented["facts"]["coverage_statement"])


if __name__ == "__main__":
    unittest.main()
