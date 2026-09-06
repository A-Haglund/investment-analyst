#!/usr/bin/env python3
"""nordic_registers.py - the Norwegian and Finnish company registers, offline.

Every test here calls the REAL code. That is the point of the file.

The module it covers shipped with a selftest that made a live network call
under a comment claiming it skipped one, and asserted nothing about what came
back. Underneath it, the name matcher had never worked in either country: it
ranked RAW register payloads on keys that only exist after normalisation, so
every comparison ran against the empty string and an exact legal name could
not resolve. A suite that mocks the resolver would have reported all of that
as green.

So no mock stands between a test and the code under test. Register payloads
are injected as data through the `get` transport seam that fetch_norway() and
fetch_finland() already provide - one call below the socket and above
everything else - so URL construction, envelope parsing, window accounting,
normalisation and the refusal ladder are all really executed. Every fixture
is trimmed from a payload observed live on data.brreg.no or
avoindata.prh.fi.

TheseTestsExerciseTheRealModule enforces the property with sys.settrace, so
the file cannot quietly rot back into a mock suite.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

nr = load("nordic_registers")


# --------------------------------------------------------------------------
# Fixtures, trimmed from live payloads
# --------------------------------------------------------------------------

def no_entity(navn, orgnr, **over):
    """A Brreg name-search hit. All four status flags present, as live."""
    entity = {
        "organisasjonsnummer": orgnr,
        "navn": navn,
        "organisasjonsform": {"kode": "ASA",
                              "beskrivelse": "Allmennaksjeselskap"},
        "naeringskode1": {"kode": "06.100",
                          "beskrivelse": "Utvinning av råolje"},
        "konkurs": False,
        "underAvvikling": False,
        "underTvangsavviklingEllerTvangsopplosning": False,
        "slettedato": None,
        "registreringsdatoEnhetsregisteret": "1995-03-12",
    }
    entity.update(over)
    return entity


def fi_entity(name, business_id, **over):
    """A PRH company record, with the live shapes intact: businessId is a
    dict, names carry a type code, mainBusinessLine keys on `type`."""
    entity = {
        "businessId": {"value": business_id, "registrationDate": "1978-03-15",
                       "source": "3"},
        "names": [{"name": name, "type": "1", "registrationDate": "1997-09-01",
                   "version": 1, "source": "1"}],
        "companyForms": [{"type": "17", "registrationDate": "1997-09-01",
                          "descriptions": [
                              {"languageCode": "1",
                               "description": "Julkinen osakeyhtiö"},
                              {"languageCode": "3",
                               "description": "Public limited company"}]}],
        "mainBusinessLine": {"type": "86950", "typeCodeSet": "TOIMI4",
                             "descriptions": [
                                 {"languageCode": "3",
                                  "description": "Physiotherapy activities"},
                                 {"languageCode": "1",
                                  "description": "Fysioterapia"}]},
        "companySituations": [],
        "status": "2",
        "tradeRegisterStatus": "1",
        "registrationDate": "1978-03-15",
        "endDate": None,
    }
    entity.update(over)
    return entity


# data.brreg.no, ?navn=Telenor&size=10: 95 entities matched. Two of these
# collapse to "telenor" once the legal form is stripped, and two distinct
# companies are literally registered as TELENOR PAKISTAN.
TELENOR = [
    no_entity("TELENOR ASA", "982463718", hjemmeside="www.telenor.com",
              antallAnsatte=15000,
              naeringskode1={"kode": "61.100",
                             "beskrivelse": "Kabelbasert, satellittbasert og "
                                            "trådløs telekommunikasjon"}),
    no_entity("TELENOR A/S", "814742342", naeringskode1=None,
              organisasjonsform={"kode": "UTLA",
                                 "beskrivelse": "Utenlandsk enhet"}),
    no_entity("TELENOR PENSJONSKASSE", "947316281",
              organisasjonsform={"kode": "PK",
                                 "beskrivelse": "Pensjonskasse"},
              naeringskode1={"kode": "65.300",
                             "beskrivelse": "Pensjonskasser"}),
    no_entity("TELENOR PAKISTAN", "993373206"),
    no_entity("TELENOR PAKISTAN", "993373257"),
]

# avoindata.prh.fi, businessId 0194099-3: 34 name records, every one of them
# carrying an endDate, and most of them auxiliary trade names (type 3).
FYSIOS_NAMES = [
    {"name": "NeuroFysio Nokia", "type": "3",
     "registrationDate": "2025-03-31", "endDate": "2026-04-30"},
    {"name": "Fysios Ylä-Malmi", "type": "3",
     "registrationDate": "2018-12-13", "endDate": "2026-04-30"},
    {"name": "Fysios Oy", "type": "1",
     "registrationDate": "2017-12-31", "endDate": "2024-07-16"},
    {"name": "Fysios Mehiläinen Oy", "type": "1",
     "registrationDate": "2024-07-16", "endDate": "2026-04-30"},
]


# --------------------------------------------------------------------------
# Transport seam
# --------------------------------------------------------------------------

def brreg_search(entities, total):
    return {"_embedded": {"enheter": list(entities)},
            "page": {"size": len(entities), "totalElements": total}}


def transport(routes, log=None):
    """A (url, timeout) -> (status, payload) transport backed by a route map.

    A route value that is an exception is raised, which is how an outage is
    injected without touching a socket.
    """
    def get(url, timeout=None):
        if log is not None:
            log.append(url)
        for fragment, reply in routes.items():
            if fragment in url:
                if isinstance(reply, BaseException):
                    raise reply
                return reply
        return 404, None
    return get


def no_lookup(query, entities=TELENOR, total=95, **kwargs):
    return nr.lookup(query, "NO",
                     get=transport({"enheter?": (200,
                                                 brreg_search(entities, total))}),
                     **kwargs)


def fi_lookup(query, entities, total, **kwargs):
    return nr.lookup(query, "FI",
                     get=transport({"companies?": (200, {
                         "companies": list(entities),
                         "totalResults": total})}),
                     **kwargs)


# --------------------------------------------------------------------------


class NameMatchingActuallyWorks(unittest.TestCase):
    """The regression the rewrite exists for.

    The matcher used to read `registration_number` and `legal_name` off RAW
    register payloads, where neither key exists - Brreg says navn and
    organisasjonsnummer, PRH says names and businessId. Every comparison ran
    against the empty string, so an exact legal name could not resolve in
    either country, and the refusal that came out named nothing at all:
    "10 distinct companies (? (?), ? (?), ...)".
    """

    def test_an_exact_norwegian_legal_name_resolves(self):
        record, note = no_lookup("TELENOR ASA")
        self.assertIsNone(note)
        self.assertEqual(record["registration_number"], "982463718")
        self.assertEqual(record["legal_name"], "TELENOR ASA")

    def test_an_exact_finnish_legal_name_resolves(self):
        record, note = fi_lookup("Nokia Oyj",
                                 [fi_entity("Nokia Oyj", "0112038-9")], 1)
        self.assertIsNone(note)
        self.assertEqual(record["registration_number"], "0112038-9")

    def test_case_and_diacritics_do_not_block_a_match(self):
        record, note = no_lookup("telenor asa")
        self.assertIsNone(note)
        self.assertEqual(record["registration_number"], "982463718")
        # NFKD splits a letter from its combining accent, so a caller who
        # types the plain vowel still reaches the register's spelling.
        aardal = [no_entity("ÅRDAL ENERGI AS", "912908688")]
        record, note = no_lookup("Ardal Energi AS", entities=aardal, total=1)
        self.assertIsNone(note, note)
        self.assertEqual(record["registration_number"], "912908688")

    def test_a_refusal_names_its_candidates(self):
        """A refusal whose candidate list reads "? (?)" cannot be acted on."""
        record, note = no_lookup("Telenor")
        self.assertIsNone(record)
        self.assertTrue(note.startswith("COMPANY_IDENTITY_AMBIGUOUS"), note)
        for expected in ("TELENOR ASA", "982463718",
                         "TELENOR A/S", "814742342"):
            self.assertIn(expected, note)
        self.assertNotIn("? (?)", note)

    def test_identical_registered_names_refuse_rather_than_take_the_first(self):
        record, note = no_lookup("Telenor Pakistan")
        self.assertIsNone(record)
        self.assertIn("993373206", note)
        self.assertIn("993373257", note)

    def test_a_word_boundary_prefix_is_not_an_identity(self):
        """A pension fund is not the telco, even alone in the window."""
        record, note = no_lookup("Telenor", entities=[TELENOR[2]], total=1)
        self.assertIsNone(record)
        self.assertTrue(note.startswith("COMPANY_IDENTITY_AMBIGUOUS"), note)

    def test_the_registration_number_resolves_outright(self):
        record, note = nr.lookup(
            "982 463 718", "NO",
            get=transport({"/enheter/982463718": (200, TELENOR[0])}))
        self.assertIsNone(note)
        self.assertEqual(record["legal_name"], "TELENOR ASA")

    def test_the_finnish_business_id_keeps_its_hyphen(self):
        """PRH answers businessId=01120389 with zero results; the hyphen is
        part of the key, and stripping it broke every direct FI lookup."""
        self.assertEqual(nr.fi_business_id("01120389"), "0112038-9")
        seen = []
        nr.fetch_finland("0112038-9", get=transport(
            {"companies?": (200, {"companies": [], "totalResults": 0})},
            log=seen))
        self.assertIn("businessId=0112038-9", seen[0])


class NorwegianStatusIsDerivedNotRead(unittest.TestCase):
    """Brreg has no `status` field.

    The old mapping ladder read one, never matched, and stamped
    status="UNKNOWN" on every Norwegian record ever produced - including
    records whose own bankrupt field said True, two fields of one record
    contradicting each other in the same breath.
    """

    def test_brreg_really_has_no_status_field(self):
        self.assertNotIn("status", TELENOR[0],
                         "fixture drifted: the derivation exists because the "
                         "live payload has no status field")

    def test_all_flags_false_is_active(self):
        record = nr.normalise_norway(TELENOR[0])
        self.assertEqual(record["status"], "ACTIVE")
        self.assertIs(record["bankrupt"], False)

    def test_bankruptcy_is_not_reported_as_unknown(self):
        record = nr.normalise_norway(no_entity("1VASK AS", "915330193",
                                               konkurs=True))
        self.assertEqual(record["status"], "BANKRUPTCY")
        self.assertIs(record["bankrupt"], True)

    def test_the_other_two_flags_map_too(self):
        liq = nr.normalise_norway(no_entity("&MORE AS", "999888777",
                                            underAvvikling=True))
        self.assertEqual(liq["status"], "LIQUIDATION")
        forced = nr.normalise_norway(no_entity(
            "TVANG AS", "999888555",
            underTvangsavviklingEllerTvangsopplosning=True))
        self.assertEqual(forced["status"], "COMPULSORY_LIQUIDATION")
        gone = nr.normalise_norway(no_entity("SLETTET AS", "999888666",
                                             slettedato="2020-01-01"))
        self.assertEqual(gone["status"], "DEREGISTERED")

    def test_status_and_bankrupt_never_contradict_each_other(self):
        for over in ({"konkurs": True}, {"underAvvikling": True},
                     {"slettedato": "2020-01-01"}, {}):
            record = nr.normalise_norway(no_entity("X AS", "111111111", **over))
            if record["bankrupt"]:
                self.assertEqual(record["status"], "BANKRUPTCY", over)
            self.assertNotEqual(record["status"], "UNKNOWN", over)


class FinnishStatusIsNotARegisterCode(unittest.TestCase):
    """PRH's `status` is an undocumented number - "2" for every company
    sampled live, active and long-ceased alike. The old code compared it
    against the string "ACTIVE", never matched, and then published the raw
    code in the field callers read as the normalised status."""

    def test_the_raw_code_never_reaches_the_status_field(self):
        record = nr.normalise_finland(fi_entity("Nokia Oyj", "0112038-9"))
        self.assertEqual(record["status"], "ACTIVE")
        self.assertNotIn(record["status"], ("2", "1", "4"))
        # Kept, but fenced off where it cannot be mistaken for the answer.
        self.assertEqual(record["register_codes"]["status"], "2")
        self.assertEqual(record["register_codes"]["tradeRegisterStatus"], "1")

    def test_an_end_date_is_a_ceased_company(self):
        record = nr.normalise_finland(fi_entity(
            "Fysios Oy", "0194099-3", names=FYSIOS_NAMES,
            endDate="2026-04-30", tradeRegisterStatus="4"))
        self.assertEqual(record["status"], "CEASED")
        self.assertIn("2026-04-30", record["status_basis"])

    def test_a_konk_situation_is_a_bankruptcy(self):
        record = nr.normalise_finland(fi_entity(
            "Konkurssi Oy", "0211270-0",
            companySituations=[{"type": "KONK",
                                "registrationDate": "2020-09-18"}]))
        self.assertEqual(record["status"], "BANKRUPTCY")
        self.assertIs(record["bankrupt"], True)

    def test_an_unreadable_situation_type_is_not_read_as_fine(self):
        record = nr.normalise_finland(fi_entity(
            "Odd Oy", "0000001-9", companySituations=[{"type": "ZZZZ"}]))
        self.assertIsNone(record["status"])
        self.assertIn("ZZZZ", record["status_basis"])


class AFieldNobodyReadIsNotAFinding(unittest.TestCase):
    """`bankrupt` defaulted to False in both countries where nothing had been
    consulted - an assertion of "not bankrupt" about a field nobody read."""

    def test_norwegian_bankrupt_is_none_when_the_flag_is_absent(self):
        record = nr.normalise_norway({"organisasjonsnummer": "123456789",
                                      "navn": "Test AS"})
        self.assertIsNone(record["bankrupt"])
        self.assertIsNone(record["status"])

    def test_finnish_bankrupt_is_none_when_situations_are_absent(self):
        record = nr.normalise_finland(
            {"businessId": {"value": "0000002-7"},
             "names": [{"name": "Bare Oy", "type": "1"}]})
        self.assertIsNone(record["bankrupt"])
        self.assertIsNone(record["status"])

    def test_a_partial_flag_set_does_not_claim_active(self):
        """Half the ladder ran. ACTIVE would claim the other half came back
        clean."""
        record = nr.normalise_norway({"organisasjonsnummer": "123456789",
                                      "navn": "Half AS", "konkurs": False})
        self.assertIsNone(record["status"])
        self.assertIs(record["bankrupt"], False)


class RegisterFieldsBecomeUsableValues(unittest.TestCase):
    def test_norwegian_industry_code_is_a_string_not_a_dict(self):
        record = nr.normalise_norway(TELENOR[0])
        self.assertEqual(record["industry_code"], "61.100")
        self.assertTrue(record["industry_description"].startswith("Kabelbasert"))

    def test_finnish_industry_comes_off_the_type_key(self):
        """mainBusinessLine has no code/line keys; both were always None."""
        record = nr.normalise_finland(fi_entity("Fysios Oy", "0194099-3"))
        self.assertEqual(record["industry_code"], "86950")
        self.assertEqual(record["industry_description"],
                         "Physiotherapy activities")

    def test_finnish_registration_number_is_a_string_not_a_dict(self):
        record = nr.normalise_finland(fi_entity("Nokia Oyj", "0112038-9"))
        self.assertIsInstance(record["registration_number"], str)
        self.assertEqual(record["registration_number"], "0112038-9")

    def test_the_scheme_less_homepage_is_repaired(self):
        record = nr.normalise_norway(TELENOR[0])
        self.assertEqual(record["homepage"], "https://www.telenor.com")

    def test_source_url_identifies_the_company_not_the_register(self):
        """A generic search landing page is documentation, not provenance."""
        no_record = nr.normalise_norway(TELENOR[0])
        self.assertIn("982463718", no_record["source_url"])
        fi_record = nr.normalise_finland(fi_entity("Nokia Oyj", "0112038-9"))
        self.assertIn("0112038-9", fi_record["source_url"])


class TheFinnishNameTypeDecidesTheLegalName(unittest.TestCase):
    """PRH types every name: 1 company name, 2 parallel name, 3 auxiliary
    trade name. Picking by list position among endDate-null records ignored
    all of that, and its `names_list[0]` fallback fires on real data -
    businessId 0194099-3 has 34 names and every one has an endDate."""

    def test_an_auxiliary_trade_name_is_never_the_legal_name(self):
        self.assertEqual(nr.pick_fi_name(FYSIOS_NAMES),
                         "Fysios Mehiläinen Oy")

    def test_the_most_recent_company_name_wins_when_all_have_ended(self):
        names = [n for n in FYSIOS_NAMES if n["type"] == "1"]
        self.assertEqual(nr.pick_fi_name(names), "Fysios Mehiläinen Oy")

    def test_a_current_name_outranks_every_ended_one(self):
        names = [{"name": "Old Oy", "type": "1", "endDate": "2020-01-01"},
                 {"name": "Current Oy", "type": "1"},
                 {"name": "Shop Sign", "type": "3"}]
        self.assertEqual(nr.pick_fi_name(names), "Current Oy")

    def test_no_company_name_at_all_returns_none(self):
        self.assertIsNone(nr.pick_fi_name([{"name": "Shop Sign", "type": "3"}]))
        self.assertIsNone(nr.pick_fi_name([]))

    def test_a_current_parallel_name_can_still_match_a_query(self):
        record, note = fi_lookup(
            "Nokia Corporation",
            [fi_entity("Nokia Oyj", "0112038-9",
                       names=[{"name": "Nokia Oyj", "type": "1"},
                              {"name": "Nokia Corporation", "type": "2"}])], 1)
        self.assertIsNone(note, note)
        self.assertEqual(record["legal_name"], "Nokia Oyj")


class AnOutageIsNotAnAbsence(unittest.TestCase):
    """Both fetchers ended `... or []`, so they could never return None, the
    SystemExit guards below them were unreachable, and a 404, a timeout, a DNS
    failure and a genuine zero-hit search all produced the same (None, None).

    SystemExit does not inherit from Exception, which is why these tests
    name it explicitly.
    """

    def _expect_outage(self, reply):
        with self.assertRaises(SystemExit) as caught:
            nr.lookup("TELENOR ASA", "NO",
                      get=transport({"enheter?": reply}))
        self.assertIn("DATA NOT AVAILABLE", str(caught.exception))

    def test_a_server_error_is_an_outage(self):
        self._expect_outage((503, None))

    def test_a_timeout_is_an_outage(self):
        self._expect_outage(nr.RegisterOutage("TimeoutError: timed out"))

    def test_a_dns_failure_is_an_outage(self):
        self._expect_outage(
            nr.RegisterOutage("URLError: [Errno -2] Name or service not known"))

    def test_a_two_hundred_that_is_not_json_is_an_outage(self):
        """A success code over an error page is an outage in disguise.

        Driven through the real http_get_json() with urlopen replaced by a
        canned response - the body is the injected data, the parsing and the
        classification are the module's own.
        """
        import urllib.request

        class _Resp(object):
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            @staticmethod
            def getcode():
                return 200

            @staticmethod
            def read():
                return b"<html><body>503 Service Unavailable</body></html>"

        real = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: _Resp()
        try:
            with self.assertRaises(nr.RegisterOutage):
                nr.http_get_json("https://data.brreg.no/enhetsregisteret/"
                                 "api/enheter?navn=x&size=1")
        finally:
            urllib.request.urlopen = real

    def test_a_404_on_a_number_lookup_is_an_answer(self):
        record, note = nr.lookup(
            "999999999", "NO",
            get=transport({"/enheter/999999999": (404, None)}))
        self.assertIsNone(record)
        self.assertIsNone(note, "a register that answered established an "
                                "absence; that is not a refusal")

    def test_a_clean_zero_hit_search_is_an_answer(self):
        record, note = no_lookup("Nothing Here AS", entities=[], total=0)
        self.assertIsNone(record)
        self.assertIsNone(note)

    def test_an_outage_and_an_absence_are_distinguishable(self):
        """The whole point: these two must not produce the same value."""
        absence = no_lookup("Nothing Here AS", entities=[], total=0)
        with self.assertRaises(SystemExit):
            nr.lookup("Nothing Here AS", "NO",
                      get=transport({"enheter?": (503, None)}))
        self.assertEqual(absence, (None, None))


class ATruncatedWindowDidNotProveUniqueness(unittest.TestCase):
    """`page.totalElements` (179 for "Equinor") and PRH's `totalResults` were
    discarded, so a window holding part of the result set could turn a
    refusal into a confident answer."""

    def test_the_window_is_recorded_on_the_record(self):
        record, _note = no_lookup("TELENOR ASA")
        self.assertEqual(record["search_window"],
                         {"returned": 5, "total": 95, "truncated": True})

    def test_a_suffix_stripped_match_refuses_on_a_truncated_window(self):
        record, note = no_lookup("Telenor", entities=[TELENOR[0]], total=95)
        self.assertIsNone(record)
        self.assertTrue(note.startswith("REGISTER_WINDOW_TRUNCATED"), note)
        self.assertIn("95", note)

    def test_the_same_match_resolves_when_the_window_is_complete(self):
        record, note = no_lookup("Telenor", entities=[TELENOR[0]], total=1)
        self.assertIsNone(note, note)
        self.assertEqual(record["registration_number"], "982463718")

    def test_the_exact_legal_name_resolves_even_when_truncated(self):
        """Rung 1 does not claim to have seen every competitor, so truncation
        does not veto it - otherwise the refusal's own advice, "re-run with
        the exact registered legal name", would have no valid input."""
        record, note = no_lookup("TELENOR ASA", entities=[TELENOR[0]], total=95)
        self.assertIsNone(note, note)
        self.assertEqual(record["registration_number"], "982463718")

    def test_an_absence_inside_a_truncated_window_is_not_an_absence(self):
        record, note = no_lookup("Nothing Here AS", entities=[], total=95)
        self.assertIsNone(record)
        self.assertTrue(note.startswith("REGISTER_WINDOW_TRUNCATED"), note)

    def test_unrelated_hits_report_both_facts(self):
        """What came back, and that not all of it did.

        Brreg's name search is relevance-ranked full text: live, a nonsense
        query reports 438934 "matches" and returns twenty unrelated units. It
        cannot establish that a company does not exist, and the refusal has to
        say both things rather than pick one.
        """
        record, note = no_lookup("Zzzqq Notacompany AS")
        self.assertIsNone(record)
        self.assertTrue(note.startswith("COMPANY_IDENTITY_UNMATCHED"), note)
        self.assertIn("TELENOR ASA", note, "the refusal must name what it saw")
        self.assertIn("not an established absence", note)

    def test_a_complete_window_reports_a_plain_unmatched(self):
        record, note = no_lookup("Zzzqq Notacompany AS", total=len(TELENOR))
        self.assertIsNone(record)
        self.assertTrue(note.startswith("COMPANY_IDENTITY_UNMATCHED"), note)
        self.assertNotIn("not an established absence", note)

    def test_finland_reads_total_results(self):
        """PRH ignores `limit` - 3 and 100 both returned the same 63 of 991 -
        so the window size cannot be inferred from what was asked for."""
        record, note = fi_lookup("Nokia",
                                 [fi_entity("Nokia Oyj", "0112038-9")], 991)
        self.assertIsNone(record)
        self.assertTrue(note.startswith("REGISTER_WINDOW_TRUNCATED"), note)

    def test_an_unreported_total_is_unknown_not_complete(self):
        window = nr.Window([1, 2, 3], total=None)
        self.assertIsNone(window.truncated)


class TheCacheKeyCarriesTheWindowSize(unittest.TestCase):
    """The old keys were "no_name:<query>" and "fi_name:<query>", so a
    limit=3 lookup poisoned a limit=50 search of the same name for 24 hours -
    and the second caller then computed its truncation flag off three rows."""

    def test_the_requested_size_reaches_the_url(self):
        seen = []
        get = transport({"enheter?": (200, brreg_search([], 0))}, log=seen)
        nr.fetch_norway("Telenor", limit=3, get=get)
        nr.fetch_norway("Telenor", limit=50, get=get)
        self.assertIn("size=3", seen[0])
        self.assertIn("size=50", seen[1])

    def test_two_window_sizes_do_not_share_a_cache_entry(self):
        seen = []
        get = transport({"enheter?": (200, brreg_search([], 0))}, log=seen)
        nr.fetch_norway("Telenor", limit=3, get=get)
        nr.fetch_norway("Telenor", limit=50, get=get)
        self.assertNotEqual(nr.cache_path(seen[0]), nr.cache_path(seen[1]))

    def test_the_full_key_is_hashed_never_truncated(self):
        """Two URLs differing only near the end must not collide."""
        base = "https://data.brreg.no/enhetsregisteret/api/enheter?navn=" \
               + ("a" * 400)
        self.assertNotEqual(nr.cache_path(base + "&size=3"),
                            nr.cache_path(base + "&size=50"))


class LookupAlwaysReturnsTheSameShape(unittest.TestCase):
    """It used to return three: a bare dict, (None, note), and (None, None)."""

    def test_every_outcome_is_a_record_note_pair(self):
        cases = [("TELENOR ASA", TELENOR, 95),
                 ("Telenor", TELENOR, 95),
                 ("Nothing Here AS", [], 0),
                 ("Nothing Here AS", [], 95),
                 (None, TELENOR, 95),
                 ("", TELENOR, 95),
                 ("   ", TELENOR, 95)]
        for query, entities, total in cases:
            result = no_lookup(query, entities=entities, total=total)
            self.assertIsInstance(result, tuple, query)
            self.assertEqual(len(result), 2, query)
            record, note = result
            self.assertTrue(record is None or note is None,
                            "%r returned both halves" % (query,))
            if record is not None:
                self.assertIsInstance(record, dict, query)

    def test_a_none_query_refuses_instead_of_raising(self):
        record, note = no_lookup(None)
        self.assertIsNone(record)
        self.assertTrue(note.startswith("COMPANY_IDENTITY_UNMATCHED"), note)

    def test_an_unsupported_country_refuses_before_any_fetch(self):
        seen = []
        get = transport({"enheter?": (200, brreg_search(TELENOR, 95))}, log=seen)
        for bad in ("DK", "", None, "xx"):
            with self.assertRaises(SystemExit):
                nr.lookup("Anything", bad, get=get)
        self.assertEqual(seen, [], "no register may be queried for a country "
                                   "this module does not cover")

    def test_denmark_stays_out_of_scope(self):
        with self.assertRaises(SystemExit) as caught:
            nr.lookup("Novo Nordisk", "DK")
        self.assertIn("DATA NOT AVAILABLE", str(caught.exception))


class NormalisationRules(unittest.TestCase):
    def test_nordic_legal_forms_are_stripped(self):
        self.assertEqual(nr.normalise("TELENOR ASA"), "telenor")
        self.assertEqual(nr.normalise("Telenor A/S"), "telenor")
        self.assertEqual(nr.normalise("Nokia Oyj"), "nokia")
        self.assertEqual(nr.normalise("Fysios Mehiläinen Oy"),
                         "fysios mehilainen")
        self.assertEqual(
            nr.normalise("Scandinavian Enviro Systems AB (publ)"),
            "scandinavian enviro systems")

    def test_holding_and_group_are_not_stripped(self):
        """In a company register the holding company and its subsidiary are
        two entities with two organisation numbers. Collapsing them would
        merge the identities this module exists to keep apart."""
        self.assertEqual(nr.normalise("Aker Holding AS"), "aker holding")
        self.assertEqual(nr.normalise("Kongsberg Gruppen ASA"),
                         "kongsberg gruppen")

    def test_a_form_inside_a_word_is_left_alone(self):
        """"as", "oy" and "sa" are short enough to appear inside ordinary
        words; only a whole token is a legal form."""
        self.assertEqual(nr.normalise("TELENOR PENSJONSKASSE"),
                         "telenor pensjonskasse")
        self.assertEqual(nr.normalise("Basalt AS"), "basalt")
        self.assertEqual(nr.normalise("Asplan Viak AS"), "asplan viak")
        self.assertEqual(nr.normalise("Oyster Oy"), "oyster")

    def test_empty_input_is_not_a_match(self):
        self.assertEqual(nr.normalise(""), "")
        self.assertEqual(nr.normalise(None), "")
        self.assertEqual(nr.rank("", [{"name_forms": ["Anything"]}]), ([], []))


class MalformedPayloadsDoNotCrash(unittest.TestCase):
    def test_entities_missing_every_optional_field_survive(self):
        for bad in ({}, {"navn": None}, {"organisasjonsnummer": None},
                    {"navn": "X", "organisasjonsform": None,
                     "naeringskode1": None}):
            record = nr.normalise_norway(bad)
            if record is not None:
                self.assertIn("registration_number", record)

    def test_finnish_records_missing_every_optional_field_survive(self):
        for bad in ({"businessId": "0112038-9"}, {"names": []},
                    {"businessId": {}, "names": None,
                     "mainBusinessLine": None, "companyForms": None}):
            record = nr.normalise_finland(bad)
            self.assertIn("registration_number", record)

    def test_a_brreg_envelope_without_a_page_block_is_unknown_not_complete(self):
        window = nr.fetch_norway(
            "Telenor",
            get=transport({"enheter?": (200, {"_embedded":
                                              {"enheter": TELENOR}})}))
        self.assertIsNone(window.total)
        self.assertIsNone(window.truncated)

    def test_a_prh_reply_without_a_companies_key_is_an_outage(self):
        """Not an empty register: the reply was not the reply we asked for."""
        with self.assertRaises(nr.RegisterOutage):
            nr.fetch_finland("Nokia",
                             get=transport({"companies?": (200, {"error": 1})}))


class TheseTestsExerciseTheRealModule(unittest.TestCase):
    """Guards the property the docstring claims, so it cannot rot.

    The module's own previous selftest asserted nothing about the one lookup
    it performed, and that lookup went over the network. A suite that replaced
    lookup() with a fake would be green against a module whose matcher never
    matched anything - which is exactly the state this rewrite found it in.
    """

    def test_the_whole_stack_is_actually_executed(self):
        seen = set()
        target = os.path.abspath(nr.__file__)

        def trace(frame, event, _arg):
            if event == "call" and os.path.abspath(
                    frame.f_code.co_filename) == target:
                seen.add(frame.f_code.co_name)
            return None

        old = sys.gettrace()
        sys.settrace(trace)
        try:
            no_lookup("TELENOR ASA")
            no_lookup("Telenor")
            fi_lookup("Nokia Oyj", [fi_entity("Nokia Oyj", "0112038-9")], 1)
        finally:
            sys.settrace(old)

        for fn in ("lookup", "search", "choose", "rank", "normalise", "fold",
                   "fetch_norway", "fetch_finland", "normalise_norway",
                   "normalise_finland", "pick_fi_name", "_no_status",
                   "_fi_status", "ambiguous_note"):
            self.assertIn(fn, seen,
                          "%s() was never called - the tests are mocking away "
                          "the code under test" % fn)

    def test_no_socket_is_opened(self):
        """Every fixture arrives through the `get` seam; nothing dials out."""
        import socket
        real_connect = socket.socket.connect
        real_create = socket.create_connection
        calls = []

        def blocked(*args, **kwargs):
            calls.append(args)
            raise AssertionError("these tests must not open a socket")

        socket.socket.connect = blocked
        socket.create_connection = blocked
        try:
            no_lookup("TELENOR ASA")
            no_lookup("Telenor")
            fi_lookup("Nokia Oyj", [fi_entity("Nokia Oyj", "0112038-9")], 1)
            nr.normalise_finland(fi_entity("Fysios Oy", "0194099-3",
                                           names=FYSIOS_NAMES))
        finally:
            socket.socket.connect = real_connect
            socket.create_connection = real_create
        self.assertEqual(calls, [])

    def test_the_modules_own_selftest_is_offline_too(self):
        """It used to make a live call under a comment saying it did not."""
        import io
        import contextlib
        import socket
        real_connect = socket.socket.connect
        real_create = socket.create_connection

        def blocked(*args, **kwargs):
            raise AssertionError("--selftest must not open a socket")

        socket.socket.connect = blocked
        socket.create_connection = blocked
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                self.assertEqual(nr._selftest(), 0)
        finally:
            socket.socket.connect = real_connect
            socket.create_connection = real_create
        self.assertIn("assertion groups ok", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
