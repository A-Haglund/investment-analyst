#!/usr/bin/env python3
"""portfolio_store.py: parse a pasted portfolio, resolve identity through
company_resolve.py (never by substring matching), and store/list the result.

Covers, per the build spec:
  * each broker-paste format in the spec's example block, in one parse
  * Swedish ("312,40") and English ("2,063.1") number forms, reused from
    mfn_news.to_number rather than re-implemented
  * a typographic minus sign (U+2212)
  * a holding with no price, and a holding with both a price and a date
  * a header row and a total row, both skipped silently (not reported)
  * an ambiguous name refused with every candidate named, while the rest of
    the paste still loads
  * save() -> load() round-tripping
  * the CLI refusing to save a partially-parsed/partially-resolved paste
    without --force, and saving the clean rows when --force is given

All offline: company_resolve.py is swapped for a small fake (see _FakeCR)
that duck-types exactly what resolve_rows() calls on it - .resolve(),
.Ambiguous, .NotFound, .NA - the same pattern portfolio_store.py's own
--selftest uses. No network call is made anywhere in this file.
"""
import argparse
import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

pf = load("portfolio_store")


# --------------------------------------------------------------------------
# Shared fake identity engine. Deliberately NOT a subclass or instance of
# company_resolve.py's real Ambiguous/NotFound - resolve_rows() only ever
# reaches these through the `cr` object it was handed (cr.Ambiguous,
# cr.NotFound), never through a name imported at this test file's top level,
# so swapping the whole module-like object in is what actually exercises the
# real code path instead of a parallel one.
# --------------------------------------------------------------------------

class FakeAmbiguous(Exception):
    def __init__(self, reason, candidates):
        self.reason = reason
        self.candidates = candidates
        super().__init__(reason)


class FakeNotFound(Exception):
    pass


class FakeCR(object):
    NA = "DATA NOT AVAILABLE"
    Ambiguous = FakeAmbiguous
    NotFound = FakeNotFound

    def __init__(self, table=None, ambiguous=None, notfound=()):
        # table: {lowercased bare name: record dict}
        # ambiguous: {lowercased query: (reason, candidates)}
        # notfound: set of lowercased queries that must raise NotFound even
        #           though a class-suffix-stripped retry might find them
        self.table = table or {}
        self.ambiguous = ambiguous or {}
        self.notfound_only = set(notfound)

    def resolve(self, name):
        key = name.strip().lower()
        if key in self.ambiguous:
            reason, candidates = self.ambiguous[key]
            raise FakeAmbiguous(reason, candidates)
        if key in self.notfound_only:
            raise FakeNotFound()
        if key in self.table:
            return self.table[key]
        raise FakeNotFound()


VOLVO_CANDIDATES = [
    {"company_name": "AB Volvo", "tickers": ["VOLV A", "VOLV B"],
     "isins": ["SE0000115420", "SE0000115446"], "leis": ["549300HGV012CNC8JD22"]},
    {"company_name": "Volvo Car AB", "tickers": ["VOLCAR B"],
     "isins": ["SE0021628898"], "leis": ["5299000EAMGGBEYP7J33"]},
]


def default_fake_cr():
    return FakeCR(
        table={
            "sandvik": {"company_name": "Sandvik AB", "ticker": "SAND B",
                       "isin": "SE0000667891", "lei": "5493004QAI1UOX9SR347"},
            "evo": {"company_name": "Evolution AB", "ticker": "EVO",
                   "isin": "SE0012673267", "lei": "529900S1E1UYIH25X754"},
            "investor": {"company_name": "Investor AB", "ticker": "INVE B",
                        "isin": "SE0000107419", "lei": "549300R7YNS5CS9ZE178"},
        },
        ambiguous={"volvo": ("brand shared by 2 listed issuers", VOLVO_CANDIDATES)},
        # "sandvik b" / "investor b" are intentionally absent from `table` and
        # not listed here either: resolve() falls through to NotFound for the
        # literal string, and portfolio_store.py's own class-suffix retry is
        # what turns that into a hit on the bare "sandvik" / "investor" key -
        # exactly the real gap this shim bridges (see portfolio_store.py's
        # _resolve_identity docstring).
    )


@contextlib.contextmanager
def isolated_store():
    """Point store_home() at a throwaway directory for the life of the
    with-block, so tests never touch a real ~/.investment-analyst."""
    with tempfile.TemporaryDirectory() as tmp:
        old = os.environ.get("PORTFOLIO_STORE_HOME")
        os.environ["PORTFOLIO_STORE_HOME"] = tmp
        try:
            yield tmp
        finally:
            if old is None:
                os.environ.pop("PORTFOLIO_STORE_HOME", None)
            else:
                os.environ["PORTFOLIO_STORE_HOME"] = old


# --------------------------------------------------------------------------

class PasteFormats(unittest.TestCase):
    """The exact four-line block from the build spec, plus the individual
    number-format and shape requirements it exercises."""

    def test_the_spec_example_block_parses_cleanly(self):
        block = ("Sandvik B      420 st    312,40\n"
                "EVO             85       1 240   2025-03-14\n"
                "Investor B     300\n"
                "Kassa          24 000 kr\n")
        rows, problems = pf.parse_paste(block)
        self.assertEqual(problems, [], problems)

        sec = {r["name"]: r for r in rows if r["kind"] == "security"}
        self.assertEqual(set(sec), {"Sandvik B", "EVO", "Investor B"})

        sandvik = sec["Sandvik B"]
        self.assertEqual(sandvik["quantity"], 420)
        self.assertEqual(sandvik["cost_per_share"], 312.40)
        self.assertIsNone(sandvik["acquired"])

        evo = sec["EVO"]
        self.assertEqual(evo["quantity"], 85)
        self.assertEqual(evo["cost_per_share"], 1240.0)
        self.assertEqual(evo["acquired"], "2025-03-14")

        investor = sec["Investor B"]
        self.assertEqual(investor["quantity"], 300)
        self.assertIsNone(investor["cost_per_share"])

        cash = [r for r in rows if r["kind"] == "cash"]
        self.assertEqual(len(cash), 1)
        self.assertEqual(cash[0]["amount"], 24000.0)
        self.assertEqual(cash[0]["currency"], "SEK")

    def test_row_with_no_price(self):
        rows, problems = pf.parse_paste("Investor B     300\n")
        self.assertEqual(problems, [])
        row = rows[0]
        self.assertEqual(row["quantity"], 300)
        self.assertIsNone(row["cost_per_share"])
        self.assertIsNone(row["cost_currency"])
        self.assertIsNone(row["acquired"])

    def test_row_with_price_and_date(self):
        rows, problems = pf.parse_paste(
            "EVO             85       1 240   2025-03-14\n")
        self.assertEqual(problems, [])
        row = rows[0]
        self.assertEqual(row["quantity"], 85)
        self.assertEqual(row["cost_per_share"], 1240.0)
        self.assertEqual(row["acquired"], "2025-03-14")

    def test_swedish_decimal_comma(self):
        rows, problems = pf.parse_paste("Sandvik B      420 st    312,40\n")
        self.assertEqual(problems, [])
        self.assertEqual(rows[0]["cost_per_share"], 312.40)

    def test_english_thousands_and_decimal(self):
        """"2,063.1" - English thousands comma AND period decimal, reused
        directly from mfn_news.to_number - must not be misread as a Nordic
        decimal comma (which would give 2.0631, a 1000x-ish error)."""
        rows, problems = pf.parse_paste("Evolution      10       2,063.1\n")
        self.assertEqual(problems, [])
        self.assertEqual(rows[0]["cost_per_share"], 2063.1)

    def test_typographic_minus_sign(self):
        """U+2212 MINUS SIGN, as Nordic typeset documents use it, must parse
        the same as an ASCII hyphen - reused from mfn_news.normalise_minus
        via to_number, not re-implemented here."""
        rows, problems = pf.parse_paste("TestCo B       10       −5,50\n")
        self.assertEqual(problems, [])
        self.assertEqual(rows[0]["cost_per_share"], -5.50)

    def test_header_row_is_skipped_silently(self):
        block = ("Namn           Antal     Kurs\n"
                "Sandvik B      420 st    312,40\n")
        rows, problems = pf.parse_paste(block)
        self.assertEqual(problems, [], "a header row must not be reported as a problem")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Sandvik B")

    def test_total_row_is_skipped_silently(self):
        block = ("Sandvik B      420 st    312,40\n"
                "Totalt                   131 208\n")
        rows, problems = pf.parse_paste(block)
        self.assertEqual(problems, [], "a total row must not be reported as a problem")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Sandvik B")

    def test_blank_lines_are_ignored(self):
        rows, problems = pf.parse_paste("\n\nInvestor B     300\n\n")
        self.assertEqual(problems, [])
        self.assertEqual(len(rows), 1)

    def test_genuinely_unparsable_line_is_reported_not_guessed(self):
        """Three bare numbers after the name: position can no longer tell
        quantity from cost per share, so this must be refused, not guessed."""
        rows, problems = pf.parse_paste("EVO 1 2 3 4\n")
        self.assertEqual(rows, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("EVO 1 2 3 4", problems[0]["raw_line"])

    def test_semicolon_delimited_paste(self):
        rows, problems = pf.parse_paste("Sandvik B;420;312,40\n")
        self.assertEqual(problems, [])
        row = rows[0]
        self.assertEqual(row["name"], "Sandvik B")
        self.assertEqual(row["quantity"], 420)
        self.assertEqual(row["cost_per_share"], 312.40)

    def test_tab_delimited_paste(self):
        rows, problems = pf.parse_paste("Investor B\t300\t285,10\n")
        self.assertEqual(problems, [])
        row = rows[0]
        self.assertEqual(row["quantity"], 300)
        self.assertEqual(row["cost_per_share"], 285.10)


class ResolveRowsIdentity(unittest.TestCase):
    """resolve_rows() must go through the identity engine, never a bare
    substring match - and an ambiguous name must not block the rest of the
    portfolio from loading."""

    def test_ambiguous_name_is_refused_and_others_still_load(self):
        fake = default_fake_cr()
        rows = [
            {"kind": "security", "raw_line": "r1", "name": "Investor",
             "quantity": 300, "cost_per_share": None, "cost_currency": None,
             "acquired": None, "note": ""},
            {"kind": "security", "raw_line": "r2", "name": "Volvo",
             "quantity": 10, "cost_per_share": None, "cost_currency": None,
             "acquired": None, "note": ""},
            {"kind": "security", "raw_line": "r3", "name": "EVO",
             "quantity": 85, "cost_per_share": None, "cost_currency": None,
             "acquired": None, "note": ""},
        ]
        old = pf._company_resolve
        pf._company_resolve = lambda: fake
        try:
            holdings, refusals = pf.resolve_rows(rows)
        finally:
            pf._company_resolve = old

        self.assertEqual(len(holdings), 2, holdings)
        names = {h["name"] for h in holdings}
        self.assertEqual(names, {"Investor AB", "Evolution AB"})

        self.assertEqual(len(refusals), 1)
        self.assertEqual(refusals[0]["name"], "Volvo")
        joined = " ".join(str(c) for c in refusals[0]["candidates"])
        self.assertIn("AB Volvo", joined)
        self.assertIn("Volvo Car AB", joined,
                     "a refusal must name every candidate, not just a count")

    def test_class_suffix_retry_bridges_broker_paste_format(self):
        """"Sandvik B" fails to resolve literally (the fake mirrors the real
        engine's actual gap - see default_fake_cr()'s comment) but must
        still resolve via the bare-name retry, landing on the correct B-share
        ticker because company_resolve.py's own primary_class() already
        picks it."""
        fake = default_fake_cr()
        rows = [{"kind": "security", "raw_line": "r1", "name": "Sandvik B",
                "quantity": 420, "cost_per_share": 312.40,
                "cost_currency": "SEK", "acquired": None, "note": ""}]
        old = pf._company_resolve
        pf._company_resolve = lambda: fake
        try:
            holdings, refusals = pf.resolve_rows(rows)
        finally:
            pf._company_resolve = old
        self.assertEqual(refusals, [])
        self.assertEqual(len(holdings), 1)
        h = holdings[0]
        self.assertEqual(h["name"], "Sandvik AB")
        self.assertEqual(h["symbol"], "SAND B")
        self.assertEqual(h["lei"], "5493004QAI1UOX9SR347")
        self.assertTrue(h["resolved"])

    def test_notfound_is_kept_unresolved_not_refused(self):
        """A name matching no listed Nordic issuer at all (a private holding,
        a foreign stock, a typo) is not the same failure as an ambiguous
        brand: it is kept, with identity fields null, flagged unresolved -
        refusing it outright would make it impossible to ever record."""
        fake = FakeCR()  # empty: everything raises NotFound
        rows = [{"kind": "security", "raw_line": "r1",
                "name": "Some Private Holding AB", "quantity": 7,
                "cost_per_share": None, "cost_currency": None,
                "acquired": None, "note": ""}]
        old = pf._company_resolve
        pf._company_resolve = lambda: fake
        try:
            holdings, refusals = pf.resolve_rows(rows)
        finally:
            pf._company_resolve = old
        self.assertEqual(refusals, [])
        self.assertEqual(len(holdings), 1)
        h = holdings[0]
        self.assertEqual(h["name"], "Some Private Holding AB")
        self.assertIsNone(h["lei"])
        self.assertIsNone(h["isin"])
        self.assertFalse(h["resolved"])
        self.assertEqual(h["quantity"], 7)


class StoreRoundTrip(unittest.TestCase):
    """load()/save() against an isolated PORTFOLIO_STORE_HOME."""

    def test_load_of_nonexistent_portfolio_is_empty_dict(self):
        with isolated_store():
            self.assertEqual(pf.load("never-created"), {})

    def test_save_then_load_round_trips(self):
        with isolated_store():
            doc = {"account_type": "ISK", "currency": "SEK",
                  "cash": {"amount": 24000.0, "currency": "SEK"},
                  "holdings": [
                      {"lei": "5493004QAI1UOX9SR347", "isin": "SE0000667891",
                       "name": "Sandvik AB", "symbol": "SAND B",
                       "quantity": 420, "cost_per_share": 312.40,
                       "cost_currency": "SEK", "acquired": "2025-03-14",
                       "note": ""}]}
            pf.save(doc, "roundtrip")
            back = pf.load("roundtrip")
            self.assertEqual(back["name"], "roundtrip")
            self.assertEqual(back["account_type"], "ISK")
            self.assertEqual(back["cash"]["amount"], 24000.0)
            self.assertEqual(back["holdings"][0]["name"], "Sandvik AB")
            self.assertEqual(back["holdings"][0]["quantity"], 420)
            self.assertIn("updated", back)

    def test_a_holding_with_no_cost_basis_is_a_complete_valid_record(self):
        with isolated_store():
            doc = {"holdings": [
                {"lei": None, "isin": None, "name": "Unresolved Co",
                 "symbol": None, "quantity": 50, "cost_per_share": None,
                 "cost_currency": None, "acquired": None, "note": ""}]}
            pf.save(doc, "nocosts")
            back = pf.load("nocosts")
            self.assertEqual(back["holdings"][0]["quantity"], 50)
            self.assertIsNone(back["holdings"][0]["cost_per_share"])

    def test_save_refuses_a_holding_with_no_quantity(self):
        with isolated_store():
            doc = {"holdings": [
                {"lei": None, "isin": None, "name": "Broken", "symbol": None,
                 "quantity": None, "cost_per_share": None,
                 "cost_currency": None, "acquired": None, "note": ""}]}
            with self.assertRaises(ValueError):
                pf.save(doc, "broken")
            # and nothing was written
            self.assertEqual(pf.load("broken"), {})

    def test_two_named_portfolios_do_not_collide(self):
        with isolated_store():
            pf.save({"holdings": [], "cash": {"amount": 1.0, "currency": "SEK"}}, "a")
            pf.save({"holdings": [], "cash": {"amount": 2.0, "currency": "SEK"}}, "b")
            self.assertEqual(pf.load("a")["cash"]["amount"], 1.0)
            self.assertEqual(pf.load("b")["cash"]["amount"], 2.0)


class ForceRequiredForPartialParse(unittest.TestCase):
    """The CLI must never write a partially-parsed/partially-resolved
    portfolio silently - only --force may save the clean subset."""

    def _run_paste(self, text, name, force, fake_cr):
        old_cr, old_stdin = pf._company_resolve, sys.stdin
        pf._company_resolve = lambda: fake_cr
        sys.stdin = io.StringIO(text)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                code = pf._cmd_paste(argparse.Namespace(name=name, force=force))
        finally:
            pf._company_resolve = old_cr
            sys.stdin = old_stdin
        return code, buf.getvalue()

    def test_without_force_nothing_is_saved(self):
        fake = default_fake_cr()
        text = "Investor B     300\nVolvo 10\n"
        with isolated_store():
            code, out = self._run_paste(text, "forcetest", force=False, fake_cr=fake)
            self.assertNotEqual(code, 0)
            self.assertIn("Volvo", out)
            self.assertIn("ambiguous", out.lower())
            self.assertIn("--force", out)
            self.assertEqual(pf.load("forcetest"), {},
                             "nothing must be written when a row failed and "
                             "--force was not given")

    def test_with_force_the_clean_rows_are_saved_and_bad_ones_discarded(self):
        fake = default_fake_cr()
        text = "Investor B     300\nVolvo 10\n"
        with isolated_store():
            code, out = self._run_paste(text, "forcetest", force=True, fake_cr=fake)
            self.assertEqual(code, 0, out)
            doc = pf.load("forcetest")
            self.assertEqual(len(doc["holdings"]), 1)
            self.assertEqual(doc["holdings"][0]["name"], "Investor AB")

    def test_a_clean_paste_needs_no_force(self):
        fake = default_fake_cr()
        text = "Investor B     300\nEVO             85       1 240\n"
        with isolated_store():
            code, out = self._run_paste(text, "clean", force=False, fake_cr=fake)
            self.assertEqual(code, 0, out)
            doc = pf.load("clean")
            self.assertEqual(len(doc["holdings"]), 2)


# --------------------------------------------------------------------------
# Schema-change coverage: fair_value_low/fair_value_high/bear_value, the
# --paste erase-on-repaste blocker (B4), the class-suffix substitution bug
# (B5), duplicate-row merging (M4) and the negative/zero-quantity guard (M5).
# --------------------------------------------------------------------------

INVESTOR_CLASSES = [
    {"symbol": "INVE A", "isin": "SE0000107393"},
    {"symbol": "INVE B", "isin": "SE0000107419"},
]
SEB_CLASSES = [
    {"symbol": "SEB A", "isin": "SE0000148884"},
    {"symbol": "SEB C", "isin": "SE0000148912"},
]


def classy_fake_cr():
    """A fake identity engine whose records carry share_classes - unlike
    default_fake_cr()'s records, which deliberately omit that key to prove
    the old-style caller (no share_classes at all) still falls back to the
    resolved record's own primary ticker/isin without regressing. This one
    is for exercising the actual class-suffix-vs-listed-lines check."""
    return FakeCR(table={
        "investor": {"company_name": "Investor AB", "ticker": "INVE B",
                    "isin": "SE0000107419", "lei": "549300R7YNS5CS9ZE178",
                    "share_classes": INVESTOR_CLASSES},
        "seb": {"company_name": "Skandinaviska Enskilda Banken AB",
               "ticker": "SEB A", "isin": "SE0000148884",
               "lei": "F3JS33DEI6XQ4ZBPTN86", "share_classes": SEB_CLASSES},
    })


def _sec_row(name, quantity, **extra):
    row = {"kind": "security", "raw_line": name, "name": name,
          "quantity": quantity, "cost_per_share": None, "cost_currency": None,
          "acquired": None, "note": ""}
    row.update(extra)
    return row


class ClassSuffixMustNotSubstituteAClass(unittest.TestCase):
    """B5: the class-suffix retry must land on the class the caller
    actually typed, checked against the issuer's OWN listed lines - never
    on whatever primary_class() would otherwise pick."""

    def _resolve(self, rows, fake):
        old = pf._company_resolve
        pf._company_resolve = lambda: fake
        try:
            return pf.resolve_rows(rows)
        finally:
            pf._company_resolve = old

    def test_investor_a_does_not_resolve_to_the_b_share(self):
        holdings, refusals = self._resolve(
            [_sec_row("Investor A", 100)], classy_fake_cr())
        self.assertEqual(refusals, [])
        self.assertEqual(len(holdings), 1)
        h = holdings[0]
        self.assertEqual(h["symbol"], "INVE A")
        self.assertEqual(h["isin"], "SE0000107393")
        self.assertNotEqual(h["symbol"], "INVE B")
        self.assertNotEqual(h["isin"], "SE0000107419")

    def test_a_typed_class_not_listed_is_refused_naming_real_classes(self):
        """SEB lists classes A and C, no B - "SEB B" must be refused, not
        silently priced as SEB C (what primary_class()'s classes[0]
        fallback would otherwise hand back)."""
        holdings, refusals = self._resolve(
            [_sec_row("SEB B", 50)], classy_fake_cr())
        self.assertEqual(holdings, [])
        self.assertEqual(len(refusals), 1)
        reason = refusals[0]["reason"]
        self.assertIn("SEB A", reason)
        self.assertIn("SEB C", reason)

    def test_volvo_b_still_refuses_naming_both_issuers(self):
        """Guard against weakening the untouched half of the shim: the
        retry only ever runs on NotFound, and an Ambiguous outcome from the
        retry is still authoritative and still names every candidate."""
        holdings, refusals = self._resolve(
            [_sec_row("Volvo B", 10)], default_fake_cr())
        self.assertEqual(holdings, [])
        self.assertEqual(len(refusals), 1)
        joined = " ".join(str(c) for c in refusals[0]["candidates"])
        self.assertIn("AB Volvo", joined)
        self.assertIn("Volvo Car AB", joined)


class DuplicateRowsMerge(unittest.TestCase):
    """M4: two rows for one issuer (two accounts, an ISK and a KF line)
    must become one holding with quantity summed, not two positions."""

    def test_duplicate_holdings_merge_with_summed_quantity_no_cost_basis(self):
        holdings = [
            {"lei": "L1", "isin": "I1", "name": "Investor AB", "symbol": "INVE B",
             "quantity": 100, "cost_per_share": 50.0, "cost_currency": "SEK",
             "acquired": None, "note": "", "fair_value_low": None,
             "fair_value_high": None, "bear_value": None, "resolved": True},
            {"lei": "L1", "isin": "I1", "name": "Investor AB", "symbol": "INVE B",
             "quantity": 200, "cost_per_share": 60.0, "cost_currency": "SEK",
             "acquired": None, "note": "", "fair_value_low": None,
             "fair_value_high": None, "bear_value": None, "resolved": True},
        ]
        merged = pf._merge_duplicate_holdings(holdings)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["quantity"], 300)
        self.assertIsNone(merged[0]["cost_per_share"])
        self.assertIsNone(merged[0]["cost_currency"])

    def test_duplicate_paste_rows_merge_end_to_end(self):
        fake = default_fake_cr()
        text = "Investor B     100     50\nInvestor B     200     60\n"
        old_cr, old_stdin = pf._company_resolve, sys.stdin
        pf._company_resolve = lambda: fake
        sys.stdin = io.StringIO(text)
        try:
            with isolated_store():
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    code = pf._cmd_paste(argparse.Namespace(name="dupe", force=False))
                self.assertEqual(code, 0, buf.getvalue())
                doc = pf.load("dupe")
                self.assertEqual(len(doc["holdings"]), 1)
                self.assertEqual(doc["holdings"][0]["quantity"], 300)
                self.assertIsNone(doc["holdings"][0]["cost_per_share"])
        finally:
            pf._company_resolve = old_cr
            sys.stdin = old_stdin


class NegativeOrZeroQuantityRejected(unittest.TestCase):
    """M5: save() must refuse a non-positive quantity - a closed or short
    position must be removed, not stored, and never divided into a
    thousand-percent weight downstream."""

    def _holding(self, quantity):
        return {"lei": None, "isin": None, "name": "Bad Qty", "symbol": None,
                "quantity": quantity, "cost_per_share": None,
                "cost_currency": None, "acquired": None, "note": ""}

    def test_negative_quantity_is_rejected(self):
        with isolated_store():
            with self.assertRaises(ValueError):
                pf.save({"holdings": [self._holding(-5)]}, "negqty")
            self.assertEqual(pf.load("negqty"), {})

    def test_zero_quantity_is_rejected(self):
        with isolated_store():
            with self.assertRaises(ValueError):
                pf.save({"holdings": [self._holding(0)]}, "zeroqty")
            self.assertEqual(pf.load("zeroqty"), {})


class FairValueAndBearFields(unittest.TestCase):
    """The new fair_value_low/fair_value_high/bear_value fields: parsed by
    mfn_news.to_number (never a second parser), validated by save(), and
    defaulted to null by load() on a document that predates them."""

    def test_fair_value_range_parses_swedish_decimal_comma(self):
        lo, hi = pf._parse_range("190,5-215,5")
        self.assertEqual(lo, 190.5)
        self.assertEqual(hi, 215.5)

    def test_add_cli_stores_fair_value_and_bear(self):
        fake = default_fake_cr()
        old_cr = pf._company_resolve
        pf._company_resolve = lambda: fake
        try:
            with isolated_store():
                args = argparse.Namespace(
                    add="EVO", qty=10.0, price=None, acquired=None, note="",
                    fair_value="190,5-215,5", bear="140", force=False,
                    name="fvtest")
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    code = pf._cmd_add(args)
                self.assertEqual(code, 0, buf.getvalue())
                h = pf.load("fvtest")["holdings"][0]
                self.assertEqual(h["fair_value_low"], 190.5)
                self.assertEqual(h["fair_value_high"], 215.5)
                self.assertEqual(h["bear_value"], 140.0)
        finally:
            pf._company_resolve = old_cr

    def test_save_rejects_a_crossed_fair_value_range(self):
        with isolated_store():
            doc = {"holdings": [
                {"lei": None, "isin": None, "name": "Crossed", "symbol": None,
                 "quantity": 10, "cost_per_share": None, "cost_currency": None,
                 "acquired": None, "note": "", "fair_value_low": 200.0,
                 "fair_value_high": 100.0, "bear_value": None}]}
            with self.assertRaises(ValueError):
                pf.save(doc, "crossed")

    def test_load_defaults_new_fields_to_none_on_a_pre_existing_document(self):
        with isolated_store():
            # Simulate a document written before the new fields existed: no
            # fair_value_low/fair_value_high/bear_value key at all.
            doc = {"holdings": [
                {"lei": None, "isin": None, "name": "Old Record", "symbol": None,
                 "quantity": 10, "cost_per_share": None, "cost_currency": None,
                 "acquired": None, "note": ""}]}
            pf.save(doc, "old")
            # save() itself would now stamp the new keys since it shares the
            # same holding dicts - so strip them back out to reproduce a
            # genuinely pre-existing file, written before this schema change.
            path = pf._path("old")
            import json as _json
            with open(path, "r", encoding="utf-8") as fh:
                raw = _json.load(fh)
            for h in raw["holdings"]:
                for k in ("fair_value_low", "fair_value_high", "bear_value"):
                    h.pop(k, None)
            with open(path, "w", encoding="utf-8") as fh:
                _json.dump(raw, fh)
            back = pf.load("old")
            h = back["holdings"][0]
            self.assertIn("fair_value_low", h)
            self.assertIsNone(h["fair_value_low"])
            self.assertIsNone(h["fair_value_high"])
            self.assertIsNone(h["bear_value"])


class PasteMustNotEraseAnalystEnteredFields(unittest.TestCase):
    """B4: re-pasting a broker export updates quantity and cost, but must
    never wipe a note, fair-value range or bear case an analyst already
    recorded - that used to happen silently on every re-paste, which is
    step one of this store's own documented workflow."""

    def test_second_paste_preserves_note_fair_value_and_bear(self):
        fake = default_fake_cr()
        old_cr, old_stdin = pf._company_resolve, sys.stdin
        pf._company_resolve = lambda: fake
        try:
            with isolated_store():
                # First paste creates the holding with no analyst fields.
                sys.stdin = io.StringIO("Investor B     300\n")
                with contextlib.redirect_stdout(io.StringIO()):
                    code = pf._cmd_paste(argparse.Namespace(name="repaste", force=False))
                self.assertEqual(code, 0)

                # An analyst records a note, a fair-value range and a bear
                # case against the stored holding directly.
                doc = pf.load("repaste")
                h = doc["holdings"][0]
                h["note"] = "thesis: compounder, hold through cycle"
                h["fair_value_low"] = 250.0
                h["fair_value_high"] = 300.0
                h["bear_value"] = 150.0
                pf.save(doc, "repaste")

                # Re-pasting the broker export (updated quantity and a new
                # price) must not erase any of that.
                sys.stdin = io.StringIO("Investor B     350     92\n")
                with contextlib.redirect_stdout(io.StringIO()):
                    code = pf._cmd_paste(argparse.Namespace(name="repaste", force=False))
                self.assertEqual(code, 0)

                back = pf.load("repaste")
                self.assertEqual(len(back["holdings"]), 1)
                h2 = back["holdings"][0]
                self.assertEqual(h2["quantity"], 350)
                self.assertEqual(h2["cost_per_share"], 92.0)
                self.assertEqual(h2["note"], "thesis: compounder, hold through cycle")
                self.assertEqual(h2["fair_value_low"], 250.0)
                self.assertEqual(h2["fair_value_high"], 300.0)
                self.assertEqual(h2["bear_value"], 150.0)
        finally:
            pf._company_resolve = old_cr
            sys.stdin = old_stdin


class UpdateExistingHoldingWithoutResupplying(unittest.TestCase):
    """--note-for / --fair-value-for / --bear-for: today --add replaces the
    whole record, so recording so much as a bear value meant retyping
    quantity, price and date too. These update one field in place."""

    def _seed(self, name):
        pf.save({"holdings": [
            {"lei": None, "isin": None, "name": "Sandvik AB", "symbol": "SAND B",
             "quantity": 420, "cost_per_share": 312.40, "cost_currency": "SEK",
             "acquired": "2025-03-14", "note": "", "fair_value_low": None,
             "fair_value_high": None, "bear_value": None}]}, name)

    def test_note_for_updates_without_requantifying(self):
        with isolated_store():
            self._seed("notefor")
            args = argparse.Namespace(
                name="notefor", note_for=["Sandvik AB", "waiting on Q3 margin"],
                fair_value_for=None, bear_for=None)
            with contextlib.redirect_stdout(io.StringIO()):
                code = pf._cmd_update_for(args)
            self.assertEqual(code, 0)
            h = pf.load("notefor")["holdings"][0]
            self.assertEqual(h["note"], "waiting on Q3 margin")
            self.assertEqual(h["quantity"], 420)
            self.assertEqual(h["cost_per_share"], 312.40)

    def test_fair_value_for_updates_without_requantifying(self):
        with isolated_store():
            self._seed("fvfor")
            args = argparse.Namespace(
                name="fvfor", note_for=None,
                fair_value_for=["Sandvik AB", "190,5-215,5"], bear_for=None)
            with contextlib.redirect_stdout(io.StringIO()):
                code = pf._cmd_update_for(args)
            self.assertEqual(code, 0)
            h = pf.load("fvfor")["holdings"][0]
            self.assertEqual(h["fair_value_low"], 190.5)
            self.assertEqual(h["fair_value_high"], 215.5)
            self.assertEqual(h["quantity"], 420)

    def test_bear_for_updates_without_requantifying(self):
        with isolated_store():
            self._seed("bearfor")
            args = argparse.Namespace(
                name="bearfor", note_for=None, fair_value_for=None,
                bear_for=["Sandvik AB", "140"])
            with contextlib.redirect_stdout(io.StringIO()):
                code = pf._cmd_update_for(args)
            self.assertEqual(code, 0)
            h = pf.load("bearfor")["holdings"][0]
            self.assertEqual(h["bear_value"], 140.0)
            self.assertEqual(h["quantity"], 420)


if __name__ == "__main__":
    unittest.main()
