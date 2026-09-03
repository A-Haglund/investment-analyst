#!/usr/bin/env python3
"""Tests for A9's v3.0.0 portfolio/watchlist work:

  1. portfolio_store.py defect 1 - _merge_duplicate_holdings must compute the
     exact quantity-weighted average cost when every contributing row has a
     known cost in the SAME currency, and fall back to None only for a
     genuine unknown (a missing cost, or a currency mismatch).
  2. portfolio_metrics.py defect 2 - fetch_price() no longer calls the
     nonexistent q.from_nasdaq() (see FetchPriceHasNoDeadNasdaqCall below);
     the happy path itself (Yahoo-only) is already covered by
     test_portfolio_metrics.py and is not re-tested here.
  3. quote.py defect 3 - the two-source cross-check against Nasdaq Nordic
     (nordic_shares.py): agreement, material disagreement, currency
     mismatch, and an unreachable/non-covering second source, all via
     injected fakes.
  4. portfolio_store.py defect 4 - --add must merge with a prior holding's
     analyst-entered fields (note/fair-value/bear), never erase them.
  5. watchlist_store.py (new) - round trip, identity resolution (ambiguous
     refusal, NotFound kept unresolved), idempotent duplicate add, removal.
  6. portfolio_metrics.portfolio_context() (new) - already-held weight and
     sector/driver overlap, a not-yet-held candidate using its own supplied
     sector, and the "no portfolio on file" degrade path.

All offline. company_resolve.py and nordic_shares.py are swapped for small
fakes duck-typing exactly what the code under test calls on them - never a
live network request. Every store test isolates PORTFOLIO_STORE_HOME /
WATCHLIST_STORE_HOME to a throwaway temp directory, so nothing here ever
touches a real ~/.investment-analyst.
"""
import argparse
import contextlib
import io
import os
import sys
import tempfile
import textwrap
import unittest

# Defensive: makes `import helpers` resolve even when this file is run
# directly (`python test_watchlist.py`) rather than via run_tests.py/
# unittest discover, both of which already put this directory on sys.path
# themselves - see helpers.py's own module docstring.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import helpers
helpers.bootstrap_path()

pf = helpers.load("portfolio_store")
pm = helpers.load("portfolio_metrics")
q = helpers.load("quote")
ws = helpers.load("watchlist_store")


# --------------------------------------------------------------------------
# Isolation helpers - one per store, since each reads a different env var.
# Mirrors test_portfolio_store.py's own isolated_store() exactly.
# --------------------------------------------------------------------------

@contextlib.contextmanager
def isolated_portfolio_store():
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


@contextlib.contextmanager
def isolated_watchlist_store():
    with tempfile.TemporaryDirectory() as tmp:
        old = os.environ.get("WATCHLIST_STORE_HOME")
        os.environ["WATCHLIST_STORE_HOME"] = tmp
        try:
            yield tmp
        finally:
            if old is None:
                os.environ.pop("WATCHLIST_STORE_HOME", None)
            else:
                os.environ["WATCHLIST_STORE_HOME"] = old


def _pf_fake_cr():
    """portfolio_store.py's own selftest fake (module-level _FakeCR):
    volvo -> ambiguous, "totally unknown security xyz" -> NotFound,
    sandvik/evo/investor -> resolve; literal "<name> B" NotFound so the
    class-suffix retry is what actually resolves "Sandvik B" etc."""
    return pf._FakeCR()


def _ws_fake_cr():
    """watchlist_store.py's own selftest fake: same shape, its own table
    (volvo -> ambiguous, beijer ref / investor -> resolve)."""
    return ws._FakeCR()


# ==========================================================================
# 0. Defect 2 - the dead q.from_nasdaq() call is gone
# ==========================================================================

class FetchPriceHasNoDeadNasdaqCall(unittest.TestCase):
    """portfolio_metrics.fetch_price() used to also call
    q.from_nasdaq(symbol.replace("-", ".")) as a "US-symbol fallback" - but
    quote.py has never defined from_nasdaq since the Nasdaq US-listings
    lookup was removed from it, so every such call raised AttributeError,
    silently swallowed by an `except (Exception, SystemExit)` around it. The
    branch was permanently dead code. Two things prove it is actually gone,
    not just unreachable in today's inputs:
    """

    def test_quote_module_has_no_from_nasdaq_at_all(self):
        self.assertFalse(hasattr(q, "from_nasdaq"))

    def test_fetch_price_does_not_reference_from_nasdaq_in_source(self):
        import inspect
        src = inspect.getsource(pm.fetch_price)
        # Forbid the CALL, not the word. fetch_price's docstring documents
        # why the dead q.from_nasdaq() branch was removed, and that
        # explanation is worth keeping - a future reader who greps for the
        # name should find the reason it is gone, not silence. So this
        # checks the parsed code instead of the text.
        import ast as _ast
        tree = _ast.parse(textwrap.dedent(src))
        calls = [n for n in _ast.walk(tree)
                 if isinstance(n, _ast.Attribute) and n.attr == "from_nasdaq"]
        self.assertEqual(calls, [], "fetch_price still references from_nasdaq")

    def test_fetch_price_still_returns_yahoo_price_via_from_yahoo_alone(self):
        real_quote_mod = pm.quote_mod
        fake_yahoo = {"price": 123.45, "currency": "SEK",
                     "source": "Yahoo Finance (unofficial endpoint)",
                     "as_of_utc": "2026-09-01T10:00:00+00:00"}

        class _FakeQuoteModule(object):
            def from_yahoo(self, symbol):
                return fake_yahoo

        pm.quote_mod = lambda: _FakeQuoteModule()
        try:
            info = pm.fetch_price("SAND-B.ST")
        finally:
            pm.quote_mod = real_quote_mod
        self.assertEqual(info["price"], 123.45)
        self.assertEqual(info["source_key"], "yahoo")


# ==========================================================================
# 1. Defect 1 - weighted-average cost on a duplicate-row merge
# ==========================================================================

class DuplicateRowMergeWeightedCost(unittest.TestCase):
    """_merge_duplicate_holdings (portfolio_store.py:~740) used to null
    cost_per_share/cost_currency on EVERY merge, unconditionally - even when
    every contributing row carried a known cost in the same currency, in
    which case the combined cost is exact arithmetic
    ((q1*c1+q2*c2)/(q1+q2)), not a guess, and nulling it destroyed
    recoverable information."""

    def _row(self, qty, cps, ccy):
        return {"lei": "L1", "isin": "I1", "name": "Investor AB",
               "symbol": "INVE B", "quantity": qty, "cost_per_share": cps,
               "cost_currency": ccy, "acquired": None, "note": "",
               "fair_value_low": None, "fair_value_high": None,
               "bear_value": None, "resolved": True}

    def test_both_known_same_currency_gives_exact_weighted_average(self):
        merged = pf._merge_duplicate_holdings(
            [self._row(100, 50.0, "SEK"), self._row(200, 60.0, "SEK")])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["quantity"], 300)
        self.assertAlmostEqual(merged[0]["cost_per_share"],
                               (100 * 50.0 + 200 * 60.0) / 300, places=9)
        self.assertEqual(merged[0]["cost_currency"], "SEK")

    def test_three_way_merge_all_known_same_currency(self):
        merged = pf._merge_duplicate_holdings([
            self._row(100, 50.0, "SEK"), self._row(100, 60.0, "SEK"),
            self._row(100, 70.0, "SEK")])
        self.assertEqual(merged[0]["quantity"], 300)
        self.assertAlmostEqual(merged[0]["cost_per_share"], 60.0, places=9)
        self.assertEqual(merged[0]["cost_currency"], "SEK")

    def test_one_row_with_unknown_cost_gives_none(self):
        merged = pf._merge_duplicate_holdings(
            [self._row(100, 50.0, "SEK"), self._row(200, None, None)])
        self.assertEqual(merged[0]["quantity"], 300)
        self.assertIsNone(merged[0]["cost_per_share"])
        self.assertIsNone(merged[0]["cost_currency"])

    def test_currency_mismatch_gives_none_never_a_mixed_average(self):
        merged = pf._merge_duplicate_holdings(
            [self._row(100, 50.0, "SEK"), self._row(200, 60.0, "EUR")])
        self.assertEqual(merged[0]["quantity"], 300)
        self.assertIsNone(merged[0]["cost_per_share"])
        self.assertIsNone(merged[0]["cost_currency"])

    def test_solo_unmerged_holding_cost_is_left_exactly_as_given(self):
        merged = pf._merge_duplicate_holdings([self._row(100, 312.4, "SEK")])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["cost_per_share"], 312.4)
        self.assertEqual(merged[0]["cost_currency"], "SEK")


# ==========================================================================
# 2. --add must not erase analyst-entered fields on re-add (defect 4)
# ==========================================================================

class AddMustNotEraseAnalystEnteredFields(unittest.TestCase):
    """_cmd_add used to drop the old holding by name and append a brand-new
    row built from the fresh --add arguments, which have no way to carry a
    prior note/fair-value/bear-case forward - every one of those fields
    silently reverted to null on every re-add. --paste's own
    _merge_paste_with_prior already prevents exactly this; --add must route
    through the same function."""

    def test_second_add_preserves_note_fair_value_and_bear(self):
        fake = _pf_fake_cr()
        old_cr = pf._company_resolve
        pf._company_resolve = lambda: fake
        try:
            with isolated_portfolio_store():
                args1 = argparse.Namespace(
                    add="Sandvik B", qty=420.0, price=312.40, acquired=None,
                    note="", fair_value=None, bear=None, force=False,
                    name="addtest")
                with contextlib.redirect_stdout(io.StringIO()):
                    code = pf._cmd_add(args1)
                self.assertEqual(code, 0)

                doc = pf.load("addtest")
                self.assertEqual(len(doc["holdings"]), 1)
                h = doc["holdings"][0]
                h["note"] = "thesis: cyclical trough, hold for margin recovery"
                h["fair_value_low"] = 300.0
                h["fair_value_high"] = 400.0
                h["bear_value"] = 220.0
                pf.save(doc, "addtest")

                # Re-add with a new quantity/price; no note/fair-value/bear
                # supplied this time - exactly the common "just update the
                # position" case.
                args2 = argparse.Namespace(
                    add="Sandvik B", qty=500.0, price=330.0, acquired=None,
                    note="", fair_value=None, bear=None, force=False,
                    name="addtest")
                with contextlib.redirect_stdout(io.StringIO()):
                    code2 = pf._cmd_add(args2)
                self.assertEqual(code2, 0)

                back = pf.load("addtest")
                self.assertEqual(len(back["holdings"]), 1)
                h2 = back["holdings"][0]
                self.assertEqual(h2["quantity"], 500.0)
                self.assertEqual(h2["cost_per_share"], 330.0)
                self.assertEqual(h2["note"],
                                 "thesis: cyclical trough, hold for margin recovery")
                self.assertEqual(h2["fair_value_low"], 300.0)
                self.assertEqual(h2["fair_value_high"], 400.0)
                self.assertEqual(h2["bear_value"], 220.0)
        finally:
            pf._company_resolve = old_cr

    def test_add_with_new_fair_value_overrides_the_old_one(self):
        """A fresh --add DOES carry a replacement when the caller actually
        supplies one - merge means "survives if not resupplied", not "never
        changes"."""
        fake = _pf_fake_cr()
        old_cr = pf._company_resolve
        pf._company_resolve = lambda: fake
        try:
            with isolated_portfolio_store():
                args1 = argparse.Namespace(
                    add="Evo", qty=10.0, price=None, acquired=None, note="",
                    fair_value="1000-1200", bear=None, force=False, name="ov")
                with contextlib.redirect_stdout(io.StringIO()):
                    pf._cmd_add(args1)
                args2 = argparse.Namespace(
                    add="Evo", qty=15.0, price=None, acquired=None, note="",
                    fair_value="1100-1300", bear=None, force=False, name="ov")
                with contextlib.redirect_stdout(io.StringIO()):
                    pf._cmd_add(args2)
                h = pf.load("ov")["holdings"][0]
                self.assertEqual(h["fair_value_low"], 1100.0)
                self.assertEqual(h["fair_value_high"], 1300.0)
        finally:
            pf._company_resolve = old_cr


# ==========================================================================
# 3. quote.py's two-source cross-check (defect 3)
# ==========================================================================

class _FakeNordic(object):
    """Duck-types exactly what quote.from_nordic() calls on nordic_shares.py
    (.search, .quote, .root_symbol) - never the real module, never the
    network."""
    def __init__(self, rows, quotes):
        self._rows = rows
        self._quotes = quotes

    def search(self, text):
        return self._rows

    def quote(self, orderbook_id):
        return self._quotes[orderbook_id]

    def root_symbol(self, symbol):
        parts = (symbol or "").rsplit(" ", 1)
        return parts[0] if len(parts) == 2 and len(parts[1]) <= 2 else symbol


class _BrokenNordic(object):
    """Every call fails exactly how nordic_shares.py's own api() fails on an
    unreachable endpoint - a SystemExit, not a plain Exception."""
    def search(self, text):
        raise SystemExit("DATA NOT AVAILABLE: Nasdaq Nordic unreachable (simulated)")


class QuoteCrossCheckTests(unittest.TestCase):
    def setUp(self):
        self._real_nordic = q._NORDIC_MODULE

    def tearDown(self):
        q._NORDIC_MODULE = self._real_nordic

    def test_ticker_mapping_yahoo_to_nasdaq_nordic(self):
        self.assertEqual(q._nordic_symbol_from_yahoo("VOLV-B.ST"), "VOLV B")
        self.assertEqual(q._nordic_symbol_from_yahoo("EVO.ST"), "EVO")
        self.assertEqual(q._nordic_symbol_from_yahoo("NOVO-B.CO"), "NOVO B")
        self.assertIsNone(q._nordic_symbol_from_yahoo("AAPL"))
        # Oslo Bors is Euronext, not Nasdaq - outside nordic_shares.py's
        # coverage even though it is a "Nordic" market in the loose sense.
        self.assertIsNone(q._nordic_symbol_from_yahoo("EQNR.OL"))

    def test_agreement_within_tolerance_is_cross_checked(self):
        q._NORDIC_MODULE = _FakeNordic(
            rows=[{"orderbookId": 1, "symbol": "VOLV B",
                  "isin": "SE0000115446", "currency": "SEK"}],
            quotes={1: {"last": 275.10, "currency": "SEK", "as_of": "2026-09-02"}})
        yahoo = {"price": 275.50, "currency": "SEK",
                "source": "Yahoo Finance (unofficial endpoint)"}
        check = q.cross_check(yahoo, "VOLV-B.ST")
        self.assertEqual(check["status"], "CROSS-CHECKED", check)

    def test_material_disagreement_is_a_conflict_not_silently_resolved(self):
        q._NORDIC_MODULE = _FakeNordic(
            rows=[{"orderbookId": 1, "symbol": "VOLV B",
                  "isin": "SE0000115446", "currency": "SEK"}],
            quotes={1: {"last": 275.10, "currency": "SEK", "as_of": "2026-09-02"}})
        yahoo_bad = {"price": 400.0, "currency": "SEK",
                    "source": "Yahoo Finance (unofficial endpoint)"}
        check = q.cross_check(yahoo_bad, "VOLV-B.ST")
        self.assertEqual(check["status"], "CONFLICT", check)

    def test_currency_mismatch_is_a_conflict_never_averaged_away(self):
        q._NORDIC_MODULE = _FakeNordic(
            rows=[{"orderbookId": 1, "symbol": "VOLV B",
                  "isin": "SE0000115446", "currency": "EUR"}],
            quotes={1: {"last": 275.10, "currency": "EUR", "as_of": "2026-09-02"}})
        yahoo = {"price": 275.50, "currency": "SEK",
                "source": "Yahoo Finance (unofficial endpoint)"}
        check = q.cross_check(yahoo, "VOLV-B.ST")
        self.assertEqual(check["status"], "CONFLICT", check)
        self.assertIn("currency", check["reason"])

    def test_unreachable_second_source_degrades_to_not_checked(self):
        q._NORDIC_MODULE = _BrokenNordic()
        yahoo = {"price": 275.50, "currency": "SEK",
                "source": "Yahoo Finance (unofficial endpoint)"}
        check = q.cross_check(yahoo, "VOLV-B.ST")
        # Never folded into CROSS-CHECKED (nothing was compared) and never
        # reported as CONFLICT (nothing disagreed - the check didn't run).
        self.assertEqual(check["status"], "not checked", check)

    def test_non_nordic_ticker_is_not_checked_not_silently_skipped(self):
        yahoo = {"price": 190.0, "currency": "USD",
                "source": "Yahoo Finance (unofficial endpoint)"}
        check = q.cross_check(yahoo, "AAPL")
        self.assertEqual(check["status"], "not checked", check)

    def test_no_matching_class_on_the_venue_is_not_checked(self):
        # Nasdaq Nordic search returns something, but not the exact class
        # requested and no class-less root match either.
        q._NORDIC_MODULE = _FakeNordic(
            rows=[{"orderbookId": 1, "symbol": "VOLV A",
                  "isin": "SE0000115420", "currency": "SEK"}],
            quotes={1: {"last": 275.10, "currency": "SEK", "as_of": "2026-09-02"}})
        yahoo = {"price": 275.50, "currency": "SEK",
                "source": "Yahoo Finance (unofficial endpoint)"}
        check = q.cross_check(yahoo, "VOLV-B.ST")
        self.assertEqual(check["status"], "not checked", check)


# ==========================================================================
# 4. watchlist_store.py (new)
# ==========================================================================

class WatchlistRoundTrip(unittest.TestCase):
    def test_save_load_round_trip(self):
        with isolated_watchlist_store():
            doc = ws._new_doc("rt")
            doc["entries"] = [{"lei": "L1", "isin": "I1", "name": "Beijer Ref AB",
                              "symbol": "BEIJ B", "date_added": "2026-09-01",
                              "why": "waiting for margin recovery",
                              "target_price": 210.0, "target_currency": "SEK",
                              "last_checked": None}]
            ws.save(doc, "rt")
            back = ws.load("rt")
            self.assertEqual(back["entries"][0]["name"], "Beijer Ref AB")
            self.assertEqual(back["schema_version"], ws.SCHEMA_VERSION)

    def test_load_of_a_name_never_saved_returns_empty_dict(self):
        with isolated_watchlist_store():
            self.assertEqual(ws.load("never-saved"), {})

    def test_save_refuses_a_nameless_entry(self):
        with isolated_watchlist_store():
            doc = ws._new_doc("bad")
            doc["entries"] = [{"lei": None, "isin": None, "name": "", "symbol": None}]
            with self.assertRaises(ValueError):
                ws.save(doc, "bad")

    def test_load_defaults_new_fields_on_a_pre_existing_document(self):
        with isolated_watchlist_store():
            doc = {"entries": [{"lei": None, "isin": None, "name": "Old Entry",
                               "symbol": None, "date_added": "2020-01-01"}]}
            ws.save(doc, "old")
            path = ws._path("old")
            import json as _json
            with open(path, "r", encoding="utf-8") as fh:
                raw = _json.load(fh)
            for e in raw["entries"]:
                for k in ("target_price", "target_currency", "last_checked", "why"):
                    e.pop(k, None)
            with open(path, "w", encoding="utf-8") as fh:
                _json.dump(raw, fh)
            back = ws.load("old")
            e = back["entries"][0]
            self.assertIn("target_price", e)
            self.assertIsNone(e["target_price"])
            self.assertIsNone(e["last_checked"])
            self.assertEqual(e["why"], "")


class WatchlistIdentityResolution(unittest.TestCase):
    def test_ambiguous_name_is_refused_with_every_candidate_named(self):
        fake = _ws_fake_cr()
        old_cr = ws._company_resolve
        ws._company_resolve = lambda: fake
        try:
            entry, refusal = ws.resolve_entry("Volvo")
            self.assertIsNone(entry)
            self.assertIsNotNone(refusal)
            joined = " ".join(ws._candidate_line(c) for c in refusal["candidates"])
            self.assertIn("AB Volvo", joined)
            self.assertIn("Volvo Car AB", joined)
        finally:
            ws._company_resolve = old_cr

    def test_notfound_is_kept_unresolved_not_refused(self):
        fake = _ws_fake_cr()
        old_cr = ws._company_resolve
        ws._company_resolve = lambda: fake
        try:
            entry, refusal = ws.resolve_entry("Totally Unknown Security XYZ")
            self.assertIsNone(refusal)
            self.assertFalse(entry["resolved"])
            self.assertIsNone(entry["lei"])
            self.assertEqual(entry["name"], "Totally Unknown Security XYZ")
        finally:
            ws._company_resolve = old_cr

    def test_class_suffix_retry_resolves_investor_b(self):
        fake = _ws_fake_cr()
        old_cr = ws._company_resolve
        ws._company_resolve = lambda: fake
        try:
            entry, refusal = ws.resolve_entry("Investor B")
            self.assertIsNone(refusal)
            self.assertEqual(entry["name"], "Investor AB")
            self.assertEqual(entry["symbol"], "INVE B")
            self.assertTrue(entry["resolved"])
        finally:
            ws._company_resolve = old_cr


class WatchlistAddRemoveTouch(unittest.TestCase):
    def test_duplicate_add_is_idempotent_and_merges_fields(self):
        fake = _ws_fake_cr()
        old_cr = ws._company_resolve
        ws._company_resolve = lambda: fake
        try:
            with isolated_watchlist_store():
                args1 = argparse.Namespace(add="Beijer Ref", why="first look",
                                          target=None, name="dupe")
                with contextlib.redirect_stdout(io.StringIO()):
                    ws._cmd_add(args1)
                # Second add supplies a target but no why - the why must
                # survive from the first add, never silently dropped.
                args2 = argparse.Namespace(add="Beijer Ref", why="",
                                          target="210,5", name="dupe")
                with contextlib.redirect_stdout(io.StringIO()):
                    ws._cmd_add(args2)

                doc = ws.load("dupe")
                self.assertEqual(len(doc["entries"]), 1)
                e = doc["entries"][0]
                self.assertEqual(e["why"], "first look")
                self.assertEqual(e["target_price"], 210.5)
                self.assertEqual(e["target_currency"], "SEK")
        finally:
            ws._company_resolve = old_cr

    def test_remove_by_resolved_name(self):
        fake = _ws_fake_cr()
        old_cr = ws._company_resolve
        ws._company_resolve = lambda: fake
        try:
            with isolated_watchlist_store():
                args = argparse.Namespace(add="Beijer Ref", why="", target=None,
                                          name="rm")
                with contextlib.redirect_stdout(io.StringIO()):
                    ws._cmd_add(args)
                rm = argparse.Namespace(remove="Beijer Ref AB", name="rm")
                with contextlib.redirect_stdout(io.StringIO()):
                    code = ws._cmd_remove(rm)
                self.assertEqual(code, 0)
                self.assertEqual(ws.load("rm")["entries"], [])
        finally:
            ws._company_resolve = old_cr

    def test_remove_of_a_name_not_on_the_list_fails_cleanly(self):
        fake = _ws_fake_cr()
        old_cr = ws._company_resolve
        ws._company_resolve = lambda: fake
        try:
            with isolated_watchlist_store():
                args = argparse.Namespace(add="Beijer Ref", why="", target=None,
                                          name="rm2")
                with contextlib.redirect_stdout(io.StringIO()):
                    ws._cmd_add(args)
                rm = argparse.Namespace(remove="Nope AB", name="rm2")
                with contextlib.redirect_stdout(io.StringIO()):
                    code = ws._cmd_remove(rm)
                self.assertEqual(code, 1)
                self.assertEqual(len(ws.load("rm2")["entries"]), 1)
        finally:
            ws._company_resolve = old_cr

    def test_touch_stamps_last_checked(self):
        fake = _ws_fake_cr()
        old_cr = ws._company_resolve
        ws._company_resolve = lambda: fake
        try:
            with isolated_watchlist_store():
                args = argparse.Namespace(add="Beijer Ref", why="", target=None,
                                          name="touch")
                with contextlib.redirect_stdout(io.StringIO()):
                    ws._cmd_add(args)
                self.assertIsNone(ws.load("touch")["entries"][0]["last_checked"])
                touch_args = argparse.Namespace(touch="Beijer Ref AB", name="touch")
                with contextlib.redirect_stdout(io.StringIO()):
                    code = ws._cmd_touch(touch_args)
                self.assertEqual(code, 0)
                self.assertIsNotNone(ws.load("touch")["entries"][0]["last_checked"])
        finally:
            ws._company_resolve = old_cr


# ==========================================================================
# 5. portfolio_metrics.portfolio_context() (new)
# ==========================================================================

class PortfolioContextTests(unittest.TestCase):
    def _patch_fetchers(self, prices, sectors):
        old = (pm.fetch_price, pm.fetch_sector, pm.fetch_fx)
        pm.fetch_price = lambda symbol: prices.get(symbol)
        pm.fetch_sector = lambda name, symbol: sectors.get(name, (None, None))
        pm.fetch_fx = lambda currencies: {}
        return old

    def _unpatch_fetchers(self, old):
        pm.fetch_price, pm.fetch_sector, pm.fetch_fx = old

    def test_no_portfolio_on_file_degrades_cleanly_never_raises(self):
        with isolated_portfolio_store():
            result = pm.portfolio_context({"name": "Ghost AB"},
                                          portfolio_name="none-such")
            self.assertEqual(result["status"], "NO_PORTFOLIO_ON_FILE")

    def test_empty_holdings_list_also_reports_no_portfolio_on_file(self):
        with isolated_portfolio_store():
            pf.save(pf._new_doc("empty"), "empty")
            result = pm.portfolio_context({"name": "Ghost AB"},
                                          portfolio_name="empty")
            self.assertEqual(result["status"], "NO_PORTFOLIO_ON_FILE")

    def test_already_held_reports_weight_and_shared_sector(self):
        with isolated_portfolio_store():
            portfolio = {
                "name": "ctx", "account_type": "ISK", "currency": "SEK",
                "cash": {"amount": 0.0, "currency": "SEK"},
                "holdings": [
                    {"lei": "LEI-A", "isin": "ISIN-A", "name": "Alpha AB",
                     "symbol": "ALPHA.ST", "quantity": 10, "cost_per_share": None,
                     "cost_currency": None, "acquired": None, "note": ""},
                    {"lei": "LEI-B", "isin": "ISIN-B", "name": "Beta AB",
                     "symbol": "BETA.ST", "quantity": 10, "cost_per_share": None,
                     "cost_currency": None, "acquired": None, "note": ""},
                ],
            }
            pf.save(portfolio, "ctx")

            prices = {
                "ALPHA.ST": {"price": 100.0, "currency": "SEK",
                            "source_key": "yahoo", "source_label": "test",
                            "as_of": "2026-09-01"},
                "BETA.ST": {"price": 100.0, "currency": "SEK",
                           "source_key": "yahoo", "source_label": "test",
                           "as_of": "2026-09-01"},
            }
            sectors = {"Alpha AB": ("Industrials", "test"),
                      "Beta AB": ("Industrials", "test")}
            old = self._patch_fetchers(prices, sectors)
            try:
                result = pm.portfolio_context({"lei": "LEI-A", "name": "Alpha AB"},
                                              portfolio_name="ctx")
            finally:
                self._unpatch_fetchers(old)

            self.assertEqual(result["status"], "OK")
            self.assertTrue(result["already_held"])
            self.assertAlmostEqual(result["current_weight_pct"], 50.0, places=6)
            self.assertEqual(result["sector"], "Industrials")
            self.assertEqual(len(result["shares_sector_with"]), 1)
            self.assertEqual(result["shares_sector_with"][0]["name"], "Beta AB")
            self.assertEqual(result["shares_driver_with"], [])

    def test_not_held_uses_caller_supplied_sector_for_overlap(self):
        with isolated_portfolio_store():
            portfolio = {
                "name": "ctx2", "account_type": "ISK", "currency": "SEK",
                "cash": {"amount": 0.0, "currency": "SEK"},
                "holdings": [
                    {"lei": "LEI-A", "isin": "ISIN-A", "name": "Alpha AB",
                     "symbol": "ALPHA.ST", "quantity": 10, "cost_per_share": None,
                     "cost_currency": None, "acquired": None, "note": ""},
                ],
            }
            pf.save(portfolio, "ctx2")

            prices = {"ALPHA.ST": {"price": 100.0, "currency": "SEK",
                                   "source_key": "yahoo", "source_label": "test",
                                   "as_of": "2026-09-01"}}
            sectors = {"Alpha AB": ("Industrials", "test")}
            old = self._patch_fetchers(prices, sectors)
            try:
                result = pm.portfolio_context(
                    {"name": "Gamma AB"}, candidate_sector="Industrials",
                    portfolio_name="ctx2")
            finally:
                self._unpatch_fetchers(old)

            self.assertEqual(result["status"], "OK")
            self.assertFalse(result["already_held"])
            self.assertIsNone(result["held_as"])
            self.assertIsNone(result["current_weight_pct"])
            self.assertEqual(result["sector"], "Industrials")
            self.assertEqual(len(result["shares_sector_with"]), 1)
            self.assertEqual(result["shares_sector_with"][0]["name"], "Alpha AB")

    def test_not_held_and_no_sector_supplied_reports_no_overlap(self):
        with isolated_portfolio_store():
            portfolio = {
                "name": "ctx3", "account_type": "ISK", "currency": "SEK",
                "cash": {"amount": 0.0, "currency": "SEK"},
                "holdings": [
                    {"lei": "LEI-A", "isin": "ISIN-A", "name": "Alpha AB",
                     "symbol": "ALPHA.ST", "quantity": 10, "cost_per_share": None,
                     "cost_currency": None, "acquired": None, "note": ""},
                ],
            }
            pf.save(portfolio, "ctx3")
            prices = {"ALPHA.ST": {"price": 100.0, "currency": "SEK",
                                   "source_key": "yahoo", "source_label": "test",
                                   "as_of": "2026-09-01"}}
            sectors = {"Alpha AB": ("Industrials", "test")}
            old = self._patch_fetchers(prices, sectors)
            try:
                result = pm.portfolio_context({"name": "Gamma AB"},
                                              portfolio_name="ctx3")
            finally:
                self._unpatch_fetchers(old)
            self.assertEqual(result["status"], "OK")
            self.assertFalse(result["already_held"])
            self.assertIsNone(result["sector"])
            self.assertEqual(result["shares_sector_with"], [])


if __name__ == "__main__":
    unittest.main()
