#!/usr/bin/env python3
"""ttm_engine.py had no test file at all before this one, and it carried
three of the nine bugs an independent review found - all silent, all capable
of producing a wrong number with a correct-looking citation:

  1. A typeset minus sign (U+2212) or an en-dash flips a negative figure to
     positive with no error (parse_number / extract_observations).
  2. A split fiscal year label ("Q1 2025/26") resolved to the wrong calendar
     year - twelve months early - because the quarter regex's own captured
     year (the fiscal year's START year) was used instead of the year the
     split-year token actually names (parse_period).
  3. ESEF observations were dated by the fiscal period they describe
     (period_end) rather than by when the filing became knowable
     (indexed_date), which let a run with --as-of serve figures that had not
     been published yet at that date (esef_observations / cover_window /
     pick_obs).

All offline: no network call is made anywhere in this file.
"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

ttm = load("ttm_engine")
d = datetime.date


class ParseNumberMinusSigns(unittest.TestCase):
    """parse_number() must treat every typographic minus as a real minus.

    Old behaviour: NUM's "-?" only matched an ASCII hyphen, so a Unicode
    minus sign in front of the digits was simply not part of the match - the
    regex started reading at the first digit instead, and the number came
    back positive with no warning at all.
    """

    def test_u2212_minus_sign_is_negative(self):
        value, truncated = ttm.parse_number(u"−139")
        self.assertEqual(value, -139.0)
        self.assertFalse(truncated)

    def test_en_dash_is_also_negative(self):
        value, truncated = ttm.parse_number(u"–139")
        self.assertEqual(value, -139.0)
        self.assertFalse(truncated)

    def test_ordinary_ascii_hyphen_still_works(self):
        """Control: the fix must not have broken the plain case."""
        value, _truncated = ttm.parse_number("-139")
        self.assertEqual(value, -139.0)


class ExtractObservationsSignPreservation(unittest.TestCase):
    """The same bug one level up: a real release block naming a negative
    EBITDA with a U+2212 minus must come back negative, not positive.

    extract_observations() normalises MINUS_CHARS_RE over the whole text
    before FIGURE_RE ever runs - drop that normalisation (or apply it only
    after FIGURE_RE has already matched) and this figure comes back +139.0,
    which is what shipped before the fix.
    """

    def test_negative_ebitda_with_unicode_minus_stays_negative(self):
        text = u"EBITDA, −139 (4 882)"
        title = "Interim report Q1 2026"
        published = d(2026, 4, 20)
        obs, warnings = ttm.extract_observations(
            text, title, published, "12-31", "cision", "http://example.test",
            default_currency="SEK", wrapped=False)
        ebitda = [o for o in obs if o.metric == "ebitda" and not o.comparative]
        self.assertEqual(len(ebitda), 1, "expected exactly one EBITDA figure, "
                         "got %r" % ([o.to_dict() for o in obs],))
        self.assertEqual(ebitda[0].value, -139.0,
                         "a Unicode minus sign must not be silently dropped, "
                         "turning a negative EBITDA positive")
        self.assertEqual(warnings, [])

    def test_the_bracketed_comparative_is_unaffected_and_positive(self):
        text = u"EBITDA, −139 (4 882)"
        obs, _warnings = ttm.extract_observations(
            text, "Interim report Q1 2026", d(2026, 4, 20), "12-31", "cision",
            "http://example.test", default_currency="SEK", wrapped=False)
        comparative = [o for o in obs if o.comparative]
        self.assertEqual(len(comparative), 1)
        self.assertEqual(comparative[0].value, 4882.0)


class ParsePeriodSplitFiscalYear(unittest.TestCase):
    """Addtech/Lagercrantz-shaped split fiscal year labels.

    QUARTER_Q_RE captures the bare 4-digit year right after "Q1", which on
    "Q1 2025/26" is "2025" - the year the fiscal year STARTS in, not the year
    it is named for. Without the SPLIT_YEAR_RE override that follows it in
    parse_period(), that captured "2025" is used directly as the fiscal-year
    label, putting fy_end_for_label(2025, "03-31") = 2025-03-31 and the whole
    quarter twelve months too early (start 2024-04-01 instead of 2025-04-01).
    """

    def test_q1_of_a_split_year_starts_in_the_correct_calendar_year(self):
        period = ttm.parse_period("Interim report Q1 2025/26", "03-31", 2025)
        self.assertIsNotNone(period)
        self.assertEqual(period.start, d(2025, 4, 1),
                         "Q1 of fiscal 2025/26 (year ending 31 March 2026) "
                         "must start 2025-04-01, not 2024-04-01")
        self.assertEqual(period.start.year, 2025)
        self.assertEqual(period.end, d(2025, 6, 30))

    def test_full_year_split_label_resolves_to_the_year_it_ends_in(self):
        """Bokslutskommuniké 2025/26 helår: the module's own naming
        convention is that a split fiscal year is named for the year it
        ENDS in - here 2026, not 2025."""
        period = ttm.parse_period(u"Bokslutskommuniké 2025/26 helår", "03-31", 2025)
        self.assertIsNotNone(period)
        self.assertEqual(period.end, d(2026, 3, 31),
                         "a split-year full-year label must resolve to the "
                         "year the fiscal year ENDS in (2026), not 2025")
        self.assertEqual(period.end.year, 2026)
        self.assertEqual(period.start, d(2025, 4, 1))


class EsefObservationsPublicationDate(unittest.TestCase):
    """esef_observations() must date every fact by when the filing became
    knowable (the index's indexed_date), never by the fiscal period_end -
    the latter would let a run with --as-of see figures published months
    later, since a filing for FY2024 (period_end 2024-12-31) is not actually
    knowable until it is filed the following spring.

    The ESEF fetch itself is monkeypatched (via ttm.esef, the lazy module
    loader) so this needs no network and no on-disk cache (ttm.NO_CACHE).
    """

    def setUp(self):
        self._real_esef = ttm.esef
        self._real_no_cache = ttm.NO_CACHE
        ttm.NO_CACHE = True

    def tearDown(self):
        ttm.esef = self._real_esef
        ttm.NO_CACHE = self._real_no_cache

    class _FakeEsefModule(object):
        FILINGS_BASE = "https://filings.example.test/"
        CONCEPTS = {"revenue": ["Revenue"]}

        def list_filings(self, lei, limit):
            return [
                {"period_end": "2024-12-31", "json_url": "filing1.json",
                 "fxo_id": "FXO1", "indexed_date": "2025-03-15"},
                {"period_end": "2023-12-31", "json_url": "filing0.json",
                 "fxo_id": "FXO0"},   # no indexed_date at all
            ]

        def get_json(self, url):
            return {"__marker__": url}

        def extract(self, doc):
            # Two distinct filings, two distinct (start, end) keys, so both
            # rows are kept independently rather than treated as the same
            # restated period.
            if doc.get("__marker__", "").endswith("filing1.json"):
                return {"Revenue": [("2024-01-01", "2024-12-31", 1000.0,
                                     "iso4217:SEK")]}
            return {"Revenue": [("2023-01-01", "2023-12-31", 900.0,
                                 "iso4217:SEK")]}

        def normalise_end(self, end):
            return end

    def test_published_is_indexed_date_never_period_end(self):
        ttm.esef = lambda: self._FakeEsefModule()
        obs, _ends = ttm.esef_observations("FAKE_LEI_1", filings=3)
        with_date = [o for o in obs if o.published]
        self.assertEqual(len(with_date), 1)
        self.assertEqual(with_date[0].published, "2025-03-15")
        self.assertNotEqual(with_date[0].published, with_date[0].end.isoformat(),
                            "published must never equal the fiscal period_end")
        self.assertEqual(with_date[0].end, d(2024, 12, 31))

    def test_missing_indexed_date_is_none_not_period_end(self):
        """When the index carries no indexed_date, `published` must stay
        None - falling back to period_end would silently reintroduce the
        exact look-ahead this fix removes."""
        ttm.esef = lambda: self._FakeEsefModule()
        obs, _ends = ttm.esef_observations("FAKE_LEI_1", filings=3)
        undated = [o for o in obs if o.published is None]
        self.assertEqual(len(undated), 1)
        self.assertEqual(undated[0].end, d(2023, 12, 31))
        self.assertIsNotNone(undated[0].note)
        self.assertIn("indexed_date", undated[0].note)


class AsOfGatingNeverServesAFutureRestatement(unittest.TestCase):
    """cover_window() and pick_obs(), given an as_of, must never hand back an
    observation whose `published` postdates it - even when that observation
    is the "newest" one on record. Before as_of support existed, both
    functions unconditionally took rows[0] (the newest restatement, full
    stop), which could leak a filing published after the requested as_of
    into a run that is supposed to be blind to it.
    """

    def test_pick_obs_skips_a_restatement_published_after_as_of(self):
        old = ttm.Obs("revenue", d(2025, 10, 1), d(2025, 12, 31), 100.0, "SEK",
                     "mfn", published="2025-11-05", precision=0)
        new = ttm.Obs("revenue", d(2025, 10, 1), d(2025, 12, 31), 999.0, "SEK",
                     "mfn", published="2026-02-01", precision=0)
        ledger = ttm.build_ledger([old, new])
        best, _superseded = ttm.pick_obs(
            ledger, "revenue", d(2025, 10, 1), d(2025, 12, 31), as_of="2025-12-01")
        self.assertIsNotNone(best)
        self.assertEqual(best.value, 100.0)
        self.assertLessEqual(best.published, "2025-12-01")

    def test_pick_obs_without_as_of_still_takes_the_newest(self):
        """Control: omitting as_of must keep every existing call site's old
        behaviour (the newest restatement, full stop)."""
        old = ttm.Obs("revenue", d(2025, 10, 1), d(2025, 12, 31), 100.0, "SEK",
                     "mfn", published="2025-11-05", precision=0)
        new = ttm.Obs("revenue", d(2025, 10, 1), d(2025, 12, 31), 999.0, "SEK",
                     "mfn", published="2026-02-01", precision=0)
        ledger = ttm.build_ledger([old, new])
        best, _superseded = ttm.pick_obs(
            ledger, "revenue", d(2025, 10, 1), d(2025, 12, 31))
        self.assertEqual(best.value, 999.0)

    def test_cover_window_uses_the_as_of_visible_value_not_the_later_one(self):
        old = ttm.Obs("revenue", d(2025, 10, 1), d(2025, 12, 31), 100.0, "SEK",
                     "mfn", published="2025-11-05", precision=0)
        new = ttm.Obs("revenue", d(2025, 10, 1), d(2025, 12, 31), 999.0, "SEK",
                     "mfn", published="2026-02-01", precision=0)
        ledger = ttm.build_ledger([old, new])
        pieces, gap = ttm.cover_window(
            ledger, "revenue", d(2025, 10, 1), d(2025, 12, 31), as_of="2025-12-01")
        self.assertIsNone(gap)
        self.assertEqual(len(pieces), 1)
        self.assertEqual(pieces[0].value, 100.0)

    def test_cover_window_reports_a_gap_when_only_a_late_restatement_exists(self):
        """If the ONLY observation on record for a period was published
        after as_of, that period must come back as a gap, never as a value -
        a gap is the honest TTM_INCOMPLETE signal; silently serving the late
        figure would look like a correct answer."""
        new_only = ttm.Obs("revenue", d(2025, 10, 1), d(2025, 12, 31), 999.0,
                           "SEK", "mfn", published="2026-02-01", precision=0)
        ledger = ttm.build_ledger([new_only])
        pieces, gap = ttm.cover_window(
            ledger, "revenue", d(2025, 10, 1), d(2025, 12, 31), as_of="2025-12-01")
        self.assertEqual(pieces, [])
        self.assertIsNotNone(gap)
        self.assertEqual(gap, (d(2025, 10, 1), d(2025, 12, 31)))


class RestatementTieBreakPrefersEsef(unittest.TestCase):
    """build_ledger()'s sort key ends with `1 if o.source == "esef" else 0`,
    reverse=True - so on an equal published date and equal precision, the
    ESEF-tagged observation must sort first (win the tie), not a prose
    figure from an MFN/Cision release. Getting the flag backwards (0 for
    esef, 1 otherwise) would let any text figure with the exact same
    published date and precision as an ESEF fact outrank the filing on
    every real tie - and ESEF facts are always recorded at precision=0, so
    it would lose on every one.
    """

    def test_esef_outranks_prose_on_an_exact_date_and_precision_tie(self):
        prose = ttm.Obs("revenue", d(2025, 1, 1), d(2025, 3, 31), 100.0, "SEK",
                        "mfn", published="2025-04-20", precision=0)
        esef = ttm.Obs("revenue", d(2025, 1, 1), d(2025, 3, 31), 101.0, "SEK",
                       "esef", published="2025-04-20", precision=0)
        ledger = ttm.build_ledger([prose, esef])
        rows = ledger[("revenue", "2025-01-01", "2025-03-31")]
        self.assertEqual(rows[0].source, "esef",
                         "on an equal published date and precision, the ESEF "
                         "observation must win the tie, not the prose one")
        self.assertEqual(rows[0].value, 101.0)

    def test_a_strictly_newer_publication_still_wins_regardless_of_source(self):
        """Control: the esef-flag tiebreak only applies ON a tie. A genuinely
        later restatement, from either source, must still win outright."""
        older_esef = ttm.Obs("revenue", d(2025, 1, 1), d(2025, 3, 31), 101.0,
                             "SEK", "esef", published="2025-04-20", precision=0)
        newer_prose = ttm.Obs("revenue", d(2025, 1, 1), d(2025, 3, 31), 105.0,
                              "SEK", "mfn", published="2025-05-01", precision=0)
        ledger = ttm.build_ledger([older_esef, newer_prose])
        rows = ledger[("revenue", "2025-01-01", "2025-03-31")]
        self.assertEqual(rows[0].source, "mfn")
        self.assertEqual(rows[0].value, 105.0)


if __name__ == "__main__":
    unittest.main()
