#!/usr/bin/env python3
"""issuer_feed.py - the shared issuer-to-newsroom resolver, offline.

Every test here calls the REAL ladder. That is the point of the file.

An earlier attempt at covering this module patched `issuer_feed.resolve` with
a fake that reimplemented the matching rules, then asserted the fake returned
what the fake had been told to return. Traced with sys.settrace, those tests
entered issuer_feed only to import it: zero calls to normalise, fold, rank,
choose or resolve. They passed, and they would have passed with the module
deleted or with choose() replaced by `return hits[0], None` - which is the
exact defect the module exists to prevent.

So: no mock stands between a test and the code under test. Search results are
injected as data through resolve()'s own `search_mfn` / `search_cision`
parameters - the seam the module provides for exactly this - and every
fixture below is shaped like a real /all/s.json result set.

test_ladder_is_actually_executed() enforces the property, so this cannot
quietly rot back into a mock suite.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

issuer_feed = load("issuer_feed")


def hit(slug, name, aliases=None):
    """One /all/s.json result, trimmed to the fields the resolver reads."""
    return {"slug": slug, "name": name, "aliases": aliases or []}


def resolve(name, mfn=(), cision=()):
    """resolve() against injected result sets. No network, no mocks."""
    return issuer_feed.resolve(name,
                               search_mfn=lambda q: list(mfn),
                               search_cision=lambda q: list(cision))


# Observed on mfn.se: the query's own issuer is present but not first, because
# the feed is relevance-ranked on something other than exact issuer name.
AXFOOD_SEARCH = [
    hit("nordnet", "Nordnet", ["nordnet"]),
    hit("avanza-bank-holding", "Avanza Bank Holding AB"),
    hit("axfood", "Axfood", ["axfood"]),
    hit("stockpicker", "Stockpicker"),
]
PRICER_SEARCH = [
    hit("fi-se", "Finansinspektionen", ["fi-se"]),
    hit("q-linea", "Q-linea"),
    hit("carnegie", "DNB Carnegie Access", ["carnegie", "carnegie-commissioned-research"]),
    hit("pricer", "Pricer", ["pricer"]),
]
VOLVO_SEARCH = [hit("volvo", "AB Volvo"), hit("volvo-car", "Volvo Car AB")]


class TopHitIsNotAnIdentity(unittest.TestCase):
    """The regression the module was written for."""

    def test_correct_issuer_wins_from_third_place(self):
        venue, slug, label, note = resolve("Axfood", mfn=AXFOOD_SEARCH)
        self.assertEqual(slug, "axfood",
                         "must not take the top-ranked hit (nordnet)")
        self.assertEqual(venue, "MFN")
        self.assertEqual(label, "Axfood")
        self.assertIsNone(note)

    def test_a_regulator_ranked_first_is_not_the_issuer(self):
        _v, slug, _l, note = resolve("Pricer", mfn=PRICER_SEARCH)
        self.assertEqual(slug, "pricer")
        self.assertIsNone(note)

    def test_no_match_refuses_even_when_the_set_holds_one_hit(self):
        """One unrelated result is an absence of alternatives, not evidence.

        This rung used to return the sole hit, which attributed
        Finansinspektionen's regulatory releases to a listed company with
        checked=True - a confident wrong answer, not a missed check.
        """
        _v, slug, _l, note = resolve(
            "Arla Plast", mfn=[hit("fi-se", "Finansinspektionen")])
        self.assertIsNone(slug)
        self.assertTrue(note.startswith("COMPANY_IDENTITY_UNMATCHED"), note)
        self.assertIn("fi-se", note, "the refusal must name what it saw")


class LooseContainmentIsNotAMatch(unittest.TestCase):
    """Substring tests in either direction returned the wrong issuer."""

    def test_a_fund_manager_is_not_the_bank(self):
        _v, slug, _l, _n = resolve(
            "Handelsbanken",
            mfn=[hit("handelsbanken-fonder", "Handelsbanken Fonder AB")])
        self.assertIsNone(slug, "a word-boundary prefix is not an identity")

    def test_a_token_of_the_query_is_not_a_match(self):
        _v, slug, _l, _n = resolve("Bio", mfn=[hit("bio-stock", "BioStock")])
        self.assertIsNone(slug)

    def test_the_bank_resolves_when_it_is_actually_present(self):
        _v, slug, _l, note = resolve(
            "Handelsbanken",
            mfn=[hit("handelsbanken-fonder", "Handelsbanken Fonder AB"),
                 hit("handelsbanken", "Handelsbanken")])
        self.assertEqual(slug, "handelsbanken")
        self.assertIsNone(note)


class TheQueryMustDiscriminate(unittest.TestCase):
    """A brand two issuers share is not an unambiguous match."""

    def test_bare_brand_refuses_and_names_both(self):
        venue, slug, label, note = resolve("Volvo", mfn=VOLVO_SEARCH)
        self.assertEqual((venue, slug, label), (None, None, None))
        self.assertTrue(note.startswith("COMPANY_IDENTITY_AMBIGUOUS"), note)
        self.assertIn("volvo-car", note)
        self.assertIn("AB Volvo", note)

    def test_the_refusals_own_advice_is_followable(self):
        """"Re-run with the exact legal name" must have a valid input.

        Both legal names normalise into each other's shadow once the legal
        suffix is stripped ("AB Volvo" -> "volvo"), so an earlier ladder
        refused both and left the advice impossible to act on.
        """
        for query, want in (("AB Volvo", "volvo"), ("Volvo Car AB", "volvo-car")):
            _v, slug, _l, note = resolve(query, mfn=VOLVO_SEARCH)
            self.assertIsNone(note, "%s should resolve: %s" % (query, note))
            self.assertEqual(slug, want, query)

    def test_two_exact_matches_refuse(self):
        _v, slug, _l, note = resolve(
            "Acme", mfn=[hit("acme-a", "Acme"), hit("acme-b", "Acme")])
        self.assertIsNone(slug)
        self.assertTrue(note.startswith("COMPANY_IDENTITY_AMBIGUOUS"), note)


class NormalisationRules(unittest.TestCase):
    def test_legal_suffixes_are_stripped(self):
        self.assertEqual(issuer_feed.normalise("Kambi Group plc"), "kambi")
        self.assertEqual(
            issuer_feed.normalise("Scandinavian Enviro Systems AB (publ)"),
            "scandinavian enviro systems")

    def test_diacritics_fold(self):
        """A name typed without the umlaut must match the register's spelling."""
        self.assertEqual(issuer_feed.normalise("Orrön Energy AB"), "orron energy")
        _v, slug, _l, note = resolve(
            "Orron Energy", mfn=[hit("orron-energy", "Orrön Energy AB")])
        self.assertEqual(slug, "orron-energy")
        self.assertIsNone(note)

    def test_empty_and_none_are_not_matches(self):
        for query in ("", "   ", None):
            _v, slug, _l, _n = resolve(query, mfn=[hit("only", "Only One")])
            self.assertIsNone(slug, "query %r must not resolve" % (query,))


class VenueFallThrough(unittest.TestCase):
    def test_empty_mfn_falls_through_to_cision(self):
        venue, slug, _l, note = resolve(
            "Elsewhere", mfn=[], cision=[hit("els", "Elsewhere")])
        self.assertEqual((venue, slug), ("Cision", "els"))
        self.assertIsNone(note)

    def test_mfn_noise_falls_through_to_cision(self):
        """MFN answers with brokers for real names; Cision carries the rest.

        Refusing at MFN would strand every Cision-distributed issuer.
        """
        venue, slug, _l, _n = resolve(
            "Elsewhere", mfn=[hit("nordnet", "Nordnet"), hit("redeye", "Redeye")],
            cision=[hit("els", "Elsewhere")])
        self.assertEqual((venue, slug), ("Cision", "els"))

    def test_ambiguity_does_not_fall_through(self):
        """A query that cannot tell two issuers apart is not fixed by Cision."""
        _v, slug, _l, note = resolve(
            "Volvo", mfn=VOLVO_SEARCH, cision=[hit("volvo-cision", "Volvo")])
        self.assertIsNone(slug)
        self.assertTrue(note.startswith("COMPANY_IDENTITY_AMBIGUOUS"), note)


class AnOutageIsNotAFinding(unittest.TestCase):
    """SystemExit does not inherit from Exception; siblings raise it for
    "DATA NOT AVAILABLE". A failed search must not read as an absent issuer."""

    @staticmethod
    def _boom(_q):
        raise SystemExit("DATA NOT AVAILABLE: simulated outage")

    def test_one_venue_down_the_other_answers(self):
        _v, slug, _l, note = issuer_feed.resolve(
            "Elsewhere", search_mfn=self._boom,
            search_cision=lambda q: [hit("els", "Elsewhere")])
        self.assertEqual(slug, "els")
        self.assertIsNone(note)

    def test_both_down_is_reported_as_an_outage_not_an_absence(self):
        _v, slug, _l, note = issuer_feed.resolve(
            "Elsewhere", search_mfn=self._boom, search_cision=self._boom)
        self.assertIsNone(slug)
        self.assertTrue(note.startswith("FEED_LOOKUP_FAILED"), note)
        self.assertIn("simulated outage", note)

    def test_a_clean_nothing_found_is_not_an_outage(self):
        _v, slug, _l, note = resolve("Nowhere", mfn=[], cision=[])
        self.assertIsNone(slug)
        self.assertIsNone(note, "no candidates at all is a clean absence")


class MalformedHitsDoNotCrash(unittest.TestCase):
    def test_hits_with_missing_fields_are_survivable(self):
        for bad in ([hit(None, "No slug")],
                    [{"slug": "s"}],
                    [{"slug": "s", "name": None, "aliases": None}]):
            _v, slug, _l, _n = resolve("Anything", mfn=bad)
            self.assertIsNone(slug)

    def test_duplicate_slugs_are_deduped(self):
        deduped = issuer_feed.dedupe_by_slug(
            [hit("a", "First"), hit("a", "Second"), hit("b", "Other")])
        self.assertEqual([h["slug"] for h in deduped], ["a", "b"])


class TheseTestsExerciseTheRealModule(unittest.TestCase):
    """Guards the property the docstring claims, so it cannot rot.

    Without this, a future refactor that reintroduces a mock reimplementing
    the ladder would leave the suite green and the resolver uncovered - which
    is precisely how the defect this module fixes survived its first suite.
    """

    def test_ladder_is_actually_executed(self):
        seen = set()
        target = os.path.abspath(issuer_feed.__file__)

        def trace(frame, event, _arg):
            if event == "call" and os.path.abspath(
                    frame.f_code.co_filename) == target:
                seen.add(frame.f_code.co_name)
            return None

        old = sys.gettrace()
        sys.settrace(trace)
        try:
            resolve("Axfood", mfn=AXFOOD_SEARCH)
            resolve("Volvo", mfn=VOLVO_SEARCH)
        finally:
            sys.settrace(old)

        for fn in ("resolve", "choose", "rank", "normalise", "fold"):
            self.assertIn(fn, seen,
                          "%s() was never called - the tests are mocking away "
                          "the code under test" % fn)


if __name__ == "__main__":
    unittest.main()
