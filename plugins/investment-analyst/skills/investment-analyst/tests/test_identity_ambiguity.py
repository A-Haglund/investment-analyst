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
portfolio_review = load("portfolio_review")


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


class PortfolioReviewRefusesAmbiguousIdentity(unittest.TestCase):
    """fetch_news_since() refuses when a holding's issuer name is ambiguous,
    naming both candidates so the user can disambiguate. It correctly delegates
    to issuer_feed.resolve() and returns (None, note) on refusal - the contract
    that portfolio_review.fetch_news_since() must uphold.
    """

    def setUp(self):
        self._real_issuer_feed = portfolio_review.issuer_feed

    def tearDown(self):
        portfolio_review.issuer_feed = self._real_issuer_feed

    def test_two_distinct_issuers_refuse_and_name_both_candidates(self):
        """Two distinct issuers matching one query must refuse and name both.
        This is the Volvo case: "Volvo" could be either AB Volvo or Volvo Car AB.
        """
        call_count = [0]

        def mock_resolve(self, name, search_mfn=None, search_cision=None, limit=12):
            call_count[0] += 1
            if name == "Volvo":
                # Simulate issuer_feed.resolve() returning a refusal with both candidates
                return (None, None, None,
                        "COMPANY_IDENTITY_AMBIGUOUS: 2 distinct MFN issuers match "
                        "'Volvo' (ab-volvo (AB Volvo), volvo-car-ab (Volvo Car AB)). "
                        "Attributing a newsroom to the wrong issuer is silent and looks "
                        "identical to a correct answer - re-run with the exact legal name.")
            return None, None, None, None

        portfolio_review.issuer_feed = type('MockIssuerfeed', (object,),
                                             {'resolve': mock_resolve})()
        data, error = portfolio_review.fetch_news_since({"name": "Volvo"})
        self.assertIsNone(data)
        self.assertIsNotNone(error)
        self.assertIn("COMPANY_IDENTITY_AMBIGUOUS", error)
        self.assertIn("AB Volvo", error)
        self.assertIn("Volvo Car AB", error)
        self.assertEqual(call_count[0], 1)

    def test_correct_issuer_not_top_hit_regression_case(self):
        """Regression test: MFN's /all/s.json is relevance-ranked by factors
        other than exact issuer name, so hits[0] regularly points to a broker,
        regulator or research publisher that mentioned the company, not the
        company's own newsroom. When searching for "Axfood", hits might be:
        [Nordnet, Avanza, Axfood, Stockpicker]. The correct slug "axfood" is
        present at position 2, not at position 0. fetch_news_since() must use
        issuer_feed.resolve() which correctly picks Axfood (position 2), not
        blindly taking hits[0] which would be Nordnet. This test would have
        caught the original bug where fetch_news_since() did `hits[0]["slug"]`.
        """
        call_count = [0]

        def mock_resolve(self, name, search_mfn=None, search_cision=None, limit=12):
            call_count[0] += 1
            if name == "Axfood":
                # Simulate issuer_feed.resolve() correctly returning the axfood
                # slug even though hits[0] would have been nordnet
                return "MFN", "axfood", "Axfood", None
            return None, None, None, None

        # Mock mfn_news to avoid network calls
        self._real_mfn_news = portfolio_review.mfn_news
        def mock_fetch_company_pages(slug, pages=1):
            if slug == "axfood":
                return [{"slug": "axfood", "title": "Axfood news"}]
            return []

        def mock_flatten(item):
            return item

        portfolio_review.issuer_feed = type('MockIssuerfeed', (object,),
                                             {'resolve': mock_resolve})()
        if portfolio_review.mfn_news:
            portfolio_review.mfn_news.fetch_company_pages = mock_fetch_company_pages
            portfolio_review.mfn_news.flatten = mock_flatten

        data, error = portfolio_review.fetch_news_since({"name": "Axfood"})
        self.assertIsNone(error, "Should resolve cleanly to axfood, not to nordnet")
        self.assertIsNotNone(data)
        self.assertEqual(data["slug"], "axfood")
        self.assertEqual(call_count[0], 1)

        portfolio_review.mfn_news = self._real_mfn_news

    def test_prefilled_feed_slug_skips_resolution(self):
        """A holding that already carries feed_slug must NOT trigger any
        resolution at all. issuer_feed.resolve() must never be called.
        """
        call_count = [0]

        def mock_resolve(self, name, search_mfn=None, search_cision=None, limit=12):
            call_count[0] += 1
            # Should never reach this
            return None, None, None, "Should not be called"

        # Mock mfn_news to return data when slug is provided
        self._real_mfn_news = portfolio_review.mfn_news
        def mock_fetch_company_pages(slug, pages=1):
            if slug == "prefilled-slug":
                return [{"slug": "prefilled-slug", "title": "News"}]
            return []

        def mock_flatten(item):
            return item

        portfolio_review.issuer_feed = type('MockIssuerfeed', (object,),
                                             {'resolve': mock_resolve})()
        if portfolio_review.mfn_news:
            portfolio_review.mfn_news.fetch_company_pages = mock_fetch_company_pages
            portfolio_review.mfn_news.flatten = mock_flatten

        # Holding with feed_slug already set
        data, error = portfolio_review.fetch_news_since({
            "name": "Axfood",
            "feed_slug": "prefilled-slug"
        })
        # Should succeed with the prefilled slug
        self.assertIsNone(error)
        self.assertIsNotNone(data)
        self.assertEqual(data["slug"], "prefilled-slug")
        # Verify resolve was never called
        self.assertEqual(call_count[0], 0)

        portfolio_review.mfn_news = self._real_mfn_news

    def test_unambiguous_single_match_resolves_cleanly(self):
        """Control: an unambiguous single match must resolve cleanly without
        triggering a refusal. The resolver must not be over-eager in refusing.
        """
        call_count = [0]

        def mock_resolve(self, name, search_mfn=None, search_cision=None, limit=12):
            call_count[0] += 1
            if name == "AB Volvo":
                # Unambiguous match - should return cleanly
                return "MFN", "ab-volvo", "AB Volvo", None
            return None, None, None, None

        # Mock mfn_news
        self._real_mfn_news = portfolio_review.mfn_news
        def mock_fetch_company_pages(slug, pages=1):
            if slug == "ab-volvo":
                return [{"slug": "ab-volvo", "title": "AB Volvo news"}]
            return []

        def mock_flatten(item):
            return item

        portfolio_review.issuer_feed = type('MockIssuerfeed', (object,),
                                             {'resolve': mock_resolve})()
        if portfolio_review.mfn_news:
            portfolio_review.mfn_news.fetch_company_pages = mock_fetch_company_pages
            portfolio_review.mfn_news.flatten = mock_flatten

        data, error = portfolio_review.fetch_news_since({"name": "AB Volvo"})
        self.assertIsNone(error, "An unambiguous match should not produce a refusal")
        self.assertIsNotNone(data)
        self.assertEqual(data["slug"], "ab-volvo")
        self.assertEqual(call_count[0], 1)

        portfolio_review.mfn_news = self._real_mfn_news


if __name__ == "__main__":
    unittest.main()
