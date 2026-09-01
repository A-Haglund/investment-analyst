#!/usr/bin/env python3
"""Identity ambiguity must be refused, not guessed.

"Volvo" matches both AB Volvo and Volvo Car AB - two entirely different
listed issuers. Attributing a lookup to the wrong one is silent and looks
identical to a correct answer, so every resolver in this toolkit that can
hit this collision must refuse rather than pick whichever candidate
happened to sort first, and the refusal must NAME the candidates it saw -
a refusal that does not tell the user what to disambiguate is not much
better than a guess.

This covers all five places the collision can occur:
  * company_resolve.resolve_candidates - the brand guard itself, previously
    untested directly.
  * valuation_gate.py's Nasdaq-search fallback (_gather_nordic).
  * share_semantics.py's Nasdaq-search fallback (resolve_identity /
    build_reconciliation).
  * guidance_track.py's MFN/Cision resolver (resolve_company).
  * peers_se.py's issuer-table resolver (resolve_target).

All offline: every search/fetch call is constructed or monkeypatched, so no
network call is made anywhere in this file.
"""
import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

company_resolve = load("company_resolve")
valuation_gate = load("valuation_gate")
share_semantics = load("share_semantics")
guidance_track = load("guidance_track")
peers_se = load("peers_se")


class CompanyResolveBrandGuard(unittest.TestCase):
    """resolve_candidates() is the shared engine every other resolver in
    this file ultimately depends on (directly, or via a simpler fallback of
    its own). Constructing the two Volvo-shaped candidates directly - no
    monkeypatching needed, this function is pure over its `cands` list."""

    def _make(self, display_name, symbols):
        cand = company_resolve.Candidate()
        cand.add_name(display_name)
        cand.symbols = set(symbols)
        return cand

    def test_volvo_refuses_between_ab_volvo_and_volvo_car(self):
        ab_volvo = self._make("AB Volvo", ["VOLV B"])
        volvo_car = self._make("Volvo Car AB", ["VOLCAR B"])
        winner, reason, confidence, contenders = company_resolve.resolve_candidates(
            [ab_volvo, volvo_car], None, "Volvo")
        self.assertIsNone(winner, "an ambiguous brand must not resolve to "
                          "either issuer")
        self.assertEqual(confidence, 0.0)
        self.assertIn("brand", reason.lower())
        self.assertEqual(len(contenders), 2)
        names = sorted(c.display() for c in contenders)
        self.assertEqual(names, ["ab volvo", "volvo car ab"],
                         "the refusal must expose the candidates it saw, not "
                         "just a bare count")

    def test_an_unambiguous_exact_name_still_resolves(self):
        """Control: the guard must not turn every query into a refusal."""
        ab_volvo = self._make("AB Volvo", ["VOLV B"])
        volvo_car = self._make("Volvo Car AB", ["VOLCAR B"])
        winner, reason, _confidence, _contenders = company_resolve.resolve_candidates(
            [ab_volvo, volvo_car], None, "AB Volvo")
        self.assertIs(winner, ab_volvo, reason)


class ValuationGateRefusesAmbiguousIdentity(unittest.TestCase):
    """_gather_nordic()'s fallback groups Nasdaq search hits by root symbol
    and refuses when more than one distinct root survives - the same
    collision, reached through valuation_gate.py's own code path."""

    def setUp(self):
        self._real_run_company_resolve = valuation_gate._run_company_resolve
        self._real_quote = valuation_gate.quote
        self._real_corporate_actions = valuation_gate.corporate_actions
        self._real_search = valuation_gate.nordic_shares.search
        valuation_gate._run_company_resolve = lambda name, timeout=100: None
        valuation_gate.quote = None
        valuation_gate.corporate_actions = None

    def tearDown(self):
        valuation_gate._run_company_resolve = self._real_run_company_resolve
        valuation_gate.quote = self._real_quote
        valuation_gate.corporate_actions = self._real_corporate_actions
        valuation_gate.nordic_shares.search = self._real_search

    def test_volvo_is_refused_not_guessed(self):
        valuation_gate.nordic_shares.search = lambda text: [
            {"name": "AB Volvo A", "symbol": "VOLV A", "currency": "SEK",
             "orderbookId": 1, "isin": "SE1"},
            {"name": "AB Volvo B", "symbol": "VOLV B", "currency": "SEK",
             "orderbookId": 2, "isin": "SE2"},
            {"name": "Volvo Car AB A", "symbol": "VOLCAR A", "currency": "SEK",
             "orderbookId": 3, "isin": "SE3"},
            {"name": "Volvo Car AB B", "symbol": "VOLCAR B", "currency": "SEK",
             "orderbookId": 4, "isin": "SE4"},
        ]
        bundle, notes = valuation_gate._gather_nordic("Volvo")
        self.assertIsNone(bundle, "an ambiguous identity must not produce a "
                          "usable bundle at all")
        joined = "\n".join(notes)
        self.assertIn("COMPANY_IDENTITY_AMBIGUOUS", joined)
        self.assertIn("AB Volvo", joined)
        self.assertIn("Volvo Car AB", joined)


class ShareSemanticsRefusesAmbiguousIdentity(unittest.TestCase):
    """share_semantics.py's own Nasdaq-search fallback (company_resolve
    disabled, so this exercises its independent root-symbol grouping)."""

    def setUp(self):
        self._real_company_resolve = share_semantics.company_resolve
        self._real_search = share_semantics.nordic_shares.search
        share_semantics.company_resolve = None

    def tearDown(self):
        share_semantics.company_resolve = self._real_company_resolve
        share_semantics.nordic_shares.search = self._real_search

    def test_build_reconciliation_refuses_and_names_both_issuers(self):
        share_semantics.nordic_shares.search = lambda q: [
            {"name": "AB Volvo A", "symbol": "VOLV A", "currency": "SEK",
             "orderbookId": 1, "isin": "SE1"},
            {"name": "AB Volvo B", "symbol": "VOLV B", "currency": "SEK",
             "orderbookId": 2, "isin": "SE2"},
            {"name": "Volvo Car AB A", "symbol": "VOLCAR A", "currency": "SEK",
             "orderbookId": 3, "isin": "SE3"},
            {"name": "Volvo Car AB B", "symbol": "VOLCAR B", "currency": "SEK",
             "orderbookId": 4, "isin": "SE4"},
        ]
        result = share_semantics.build_reconciliation("Volvo")
        self.assertFalse(result["resolved"])
        self.assertEqual(result["state"],
                         share_semantics.State.COMPANY_IDENTITY_AMBIGUOUS.value)
        self.assertEqual(len(result["candidates"]), 2)
        joined = " ".join(result["candidates"])
        self.assertIn("AB Volvo", joined)
        self.assertIn("Volvo Car AB", joined)


class GuidanceTrackRefusesAmbiguousIdentity(unittest.TestCase):
    """resolve_company() refuses when a query matches more than one distinct
    MFN slug that each carry a real release archive."""

    def setUp(self):
        self._real_search = guidance_track.MFN.search
        self._real_fetch = guidance_track.MFN.fetch
        self._real_flatten = guidance_track.MFN.flatten

    def tearDown(self):
        guidance_track.MFN.search = self._real_search
        guidance_track.MFN.fetch = self._real_fetch
        guidance_track.MFN.flatten = self._real_flatten

    def test_volvo_is_refused_and_both_issuers_are_named(self):
        guidance_track.MFN.search = lambda name: [
            {"name": "AB Volvo", "slug": "ab-volvo"},
            {"name": "Volvo Car AB", "slug": "volvo-car-ab"},
        ]

        def fake_fetch(path, **kw):
            if path == "/all/a.json":
                return {"items": [{"slug": kw.get("author"), "title": "x"}]}
            return {"items": []}

        guidance_track.MFN.fetch = fake_fetch
        guidance_track.MFN.flatten = lambda i: i

        venue, slug, label, note = guidance_track.resolve_company("Volvo")
        self.assertIsNone(venue)
        self.assertIsNone(slug)
        self.assertIsNone(label)
        self.assertIn("COMPANY_IDENTITY_AMBIGUOUS", note)
        self.assertIn("AB Volvo", note)
        self.assertIn("Volvo Car AB", note)


class PeersSeRefusesAmbiguousIdentity(unittest.TestCase):
    """resolve_target() is a pure function over an issuer table - no
    monkeypatching needed. Two distinct issuers that tie in the same
    matching tier (both are a substring/prefix match on "volvo", neither is
    an exact name match) must refuse and print both."""

    def test_two_tied_issuers_refuse_and_print_both_names(self):
        issuers = {
            "k1": {"display": "Volvo Cars International", "root": "VOLVCAR",
                  "segment": "Large Cap", "sector": "Consumer"},
            "k2": {"display": "Volvo Trucks Corporation", "root": "VOLVTRK",
                  "segment": "Large Cap", "sector": "Industrials"},
        }
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = peers_se.resolve_target("volvo", issuers)
        self.assertIs(result, peers_se.AMBIGUOUS_TARGET)
        printed = buf.getvalue()
        self.assertIn("COMPANY_IDENTITY_AMBIGUOUS", printed)
        self.assertIn("Volvo Cars International", printed)
        self.assertIn("Volvo Trucks Corporation", printed)

    def test_an_unambiguous_query_still_resolves(self):
        """Control: a query that ties in only one issuer's tier must still
        resolve normally, not refuse."""
        issuers = {
            "k1": {"display": "AB Volvo", "root": "VOLV",
                  "segment": "Large Cap", "sector": "Industrials"},
        }
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = peers_se.resolve_target("volvo", issuers)
        self.assertEqual(result, "k1")


if __name__ == "__main__":
    unittest.main()
