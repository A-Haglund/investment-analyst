#!/usr/bin/env python3
"""v3.0.0's shared core: numparse.py, finmath.py, http_util.py, _bootstrap.py.

Before v3.0.0 this toolkit had four independent number parsers (mfn_news.
to_number, ttm_engine.parse_number, insider_se.parse_fi_number, corporate_
actions._to_int) and two independent CAGR implementations (peers_se.py's
fixed one, earnings_quality.py's unfixed one) that disagreed. numparse.py and
finmath.py are the union of the correct behaviours; this file is the
regression suite for THAT union, so a future edit cannot silently reintroduce
one of the bugs the union was built to retire:

  * the 1000x English-comma-vs-Nordic-decimal-comma misread;
  * the typographic-minus-with-a-trailing-space sign flip;
  * the H&M footnote-marker tenfold error;
  * the CAGR index-exponent bug (Investor's gapped series);
  * the CAGR no-currency-check bug (Betsson's SEK-to-EUR redenomination).

All offline: pure string/date/arithmetic logic, no network.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

numparse = load("numparse")
finmath = load("finmath")
http_util = load("http_util")
_bootstrap = load("_bootstrap")


# ==========================================================================
# numparse.py
# ==========================================================================

class SignHandling(unittest.TestCase):
    """The named bug: a typographic minus with a trailing space ("- 11 471")
    once flipped a cash outflow into an inflow."""

    def test_ascii_minus_with_trailing_space(self):
        got, _ = numparse.parse_number("- 11 471")
        self.assertEqual(got, -11471.0)

    def test_unicode_minus_sign_u2212(self):
        got, _ = numparse.parse_number("−139")
        self.assertEqual(got, -139.0)

    def test_unicode_minus_with_trailing_space(self):
        got, _ = numparse.parse_number("– 11 471")  # en dash
        self.assertEqual(got, -11471.0)

    def test_em_dash_minus(self):
        got, _ = numparse.parse_number("—139")
        self.assertEqual(got, -139.0)

    def test_parenthesised_negative(self):
        got, _ = numparse.parse_number("(1 234)")
        self.assertEqual(got, -1234.0)

    def test_positive_is_unaffected(self):
        got, _ = numparse.parse_number("11 471")
        self.assertEqual(got, 11471.0)


class ThousandsVsDecimalComma(unittest.TestCase):
    """Swedish space-grouped thousands vs English comma-grouped thousands vs
    Nordic decimal comma - reading a comma as a decimal separator when it was
    really a thousands separator is a 1000x error that looks entirely
    plausible on the printed page."""

    def test_swedish_space_grouped_thousands(self):
        got, _ = numparse.parse_number("28 838")
        self.assertEqual(got, 28838.0)

    def test_english_comma_grouped_thousands(self):
        got, _ = numparse.parse_number("24,297")
        self.assertEqual(got, 24297.0)

    def test_nordic_decimal_comma(self):
        got, _ = numparse.parse_number("-0,05")
        self.assertEqual(got, -0.05)
        got, _ = numparse.parse_number("16,6")
        self.assertEqual(got, 16.6)

    def test_english_mixed_thousands_and_decimal(self):
        got, _ = numparse.parse_number("2,063.1")
        self.assertEqual(got, 2063.1)

    def test_continental_mixed_thousands_and_decimal(self):
        got, _ = numparse.parse_number("1.030,8")
        self.assertEqual(got, 1030.8)

    def test_repeated_comma_thousands(self):
        got, _ = numparse.parse_number("1,234,567")
        self.assertEqual(got, 1234567.0)

    def test_to_number_matches_parse_number(self):
        self.assertEqual(numparse.to_number("24,297"), 24297.0)
        self.assertIsNone(numparse.to_number(None))
        self.assertIsNone(numparse.to_number("garbage"))


class FootnoteMarkerTruncation(unittest.TestCase):
    """H&M prints "MSEK 2 983 1" where the trailing 1 is a footnote marker
    for a restated comparative - read naively that is 29,831, a tenfold
    error. A space-separated group must be exactly three digits or the
    number ends there, flagged via `truncated`."""

    def test_footnote_digit_is_dropped_and_flagged(self):
        got, truncated = numparse.parse_number("2 983 1")
        self.assertEqual(got, 2983.0)
        self.assertTrue(truncated)

    def test_well_formed_multi_group_number_is_not_flagged(self):
        got, truncated = numparse.parse_number("5 000 000")
        self.assertEqual(got, 5000000.0)
        self.assertFalse(truncated)


class WhitespaceAndPercent(unittest.TestCase):
    def test_non_breaking_space_thousands(self):
        got, _ = numparse.parse_number("24 297")
        self.assertEqual(got, 24297.0)

    def test_narrow_no_break_space_thousands(self):
        got, _ = numparse.parse_number("24 297")
        self.assertEqual(got, 24297.0)

    def test_trailing_percent_is_stripped(self):
        got, _ = numparse.parse_number("-3.2%")
        self.assertEqual(got, -3.2)


class GenuineAmbiguityRefused(unittest.TestCase):
    """numparse refuses rather than guesses: garbage input returns None, and
    an unrecognised scale/currency suffix is refused rather than silently
    treated as unscaled (1x) - guessing "no scale" on an unfamiliar token can
    be exactly the kind of large, silent, plausible-looking error this
    toolkit exists to prevent."""

    def test_unparseable_input_is_none(self):
        for raw in (None, "", "n/a", "garbage"):
            got, truncated = numparse.parse_number(raw)
            self.assertIsNone(got, raw)

    def test_unrecognised_scale_suffix_refuses(self):
        r = numparse.parse_scaled("123.4 GBX")
        self.assertIsNone(r.value_scaled)
        self.assertIsNotNone(r.reason)
        # the digits themselves were still recovered - only the scale is refused
        self.assertEqual(r.value, 123.4)

    def test_recognised_suffix_is_not_refused(self):
        r = numparse.parse_scaled("123.4 MSEK")
        self.assertIsNotNone(r.value_scaled)
        self.assertIsNone(r.reason)


class ScaleSuffixes(unittest.TestCase):
    """Every scale token this toolkit's four original parsers recognised
    between them, merged into one table."""

    CUR_CASES = {
        "KSEK": (1e3, "SEK"), "TSEK": (1e3, "SEK"), "MSEK": (1e6, "SEK"),
        "MKR": (1e6, "SEK"), "TKR": (1e3, "SEK"), "MDKR": (1e9, "SEK"),
        "KEUR": (1e3, "EUR"), "TEUR": (1e3, "EUR"), "MEUR": (1e6, "EUR"),
        "MNOK": (1e6, "NOK"), "TNOK": (1e3, "NOK"),
        "MDKK": (1e6, "DKK"), "TDKK": (1e3, "DKK"),
        "MUSD": (1e6, "USD"), "KUSD": (1e3, "USD"),
        "SEK": (1.0, "SEK"), "EUR": (1.0, "EUR"), "NOK": (1.0, "NOK"),
        "DKK": (1.0, "DKK"), "USD": (1.0, "USD"), "KR": (1.0, "SEK"),
    }
    WORD_CASES = {
        "THOUSAND": 1e3, "TUSEN": 1e3, "MILLION": 1e6, "MILJONER": 1e6,
        "MN": 1e6, "M": 1e6, "BILLION": 1e9, "MILJARD": 1e9,
        "MILJARDER": 1e9, "MDR": 1e9, "BN": 1e9, "MD": 1e9,
    }

    def test_currency_scale_tokens(self):
        for token, (mult, ccy) in self.CUR_CASES.items():
            with self.subTest(token=token):
                found = numparse.parse_scale_token(token)
                self.assertEqual(found, (mult, ccy))
                # case-insensitive
                self.assertEqual(numparse.parse_scale_token(token.lower()), (mult, ccy))

    def test_word_scale_tokens(self):
        for token, mult in self.WORD_CASES.items():
            with self.subTest(token=token):
                found = numparse.parse_scale_token(token)
                self.assertEqual(found, (mult, None))

    def test_parse_scaled_prefix_and_suffix_forms(self):
        r1 = numparse.parse_scaled("MSEK 123.4")
        r2 = numparse.parse_scaled("123.4 MSEK")
        self.assertEqual(r1.value_scaled, 123400000.0)
        self.assertEqual(r2.value_scaled, 123400000.0)
        self.assertEqual(r1.currency, "SEK")

    def test_bare_number_scale_defaults_to_one(self):
        r = numparse.parse_scaled("42")
        self.assertEqual(r.scale, 1.0)
        self.assertEqual(r.value_scaled, 42.0)
        self.assertIsNone(r.currency)


class FiNumberDelegation(unittest.TestCase):
    """insider_se.py's own historical test cases (test_insider_se.py),
    reproduced against numparse.parse_fi_number() directly - the function
    insider_se.parse_fi_number() now delegates to."""

    def test_blank_and_none_are_zero_ok(self):
        self.assertEqual(numparse.parse_fi_number(None), (0.0, True))
        self.assertEqual(numparse.parse_fi_number(""), (0.0, True))
        self.assertEqual(numparse.parse_fi_number("   "), (0.0, True))

    def test_comma_thousands(self):
        self.assertEqual(numparse.parse_fi_number("1,000"), (1000.0, True))

    def test_space_grouped_with_decimal_comma(self):
        value, ok = numparse.parse_fi_number("1 234,5")
        self.assertTrue(ok)
        self.assertAlmostEqual(value, 1234.5)

    def test_decimal_comma(self):
        value, ok = numparse.parse_fi_number("16,6")
        self.assertTrue(ok)
        self.assertAlmostEqual(value, 16.6)

    def test_unparseable_reports_failure(self):
        self.assertEqual(numparse.parse_fi_number("garbage"), (0.0, False))


class ToIntDelegation(unittest.TestCase):
    """corporate_actions.py's _to_int() contract: a bare integer, grouping
    characters stripped, refusing rather than rounding a genuine fraction."""

    def test_comma_grouped_share_count(self):
        self.assertEqual(numparse.to_int("181,284,725"), 181284725)

    def test_space_grouped_share_count(self):
        self.assertEqual(numparse.to_int("55 000 000"), 55000000)

    def test_none_and_garbage(self):
        self.assertIsNone(numparse.to_int(None))
        self.assertIsNone(numparse.to_int("not a number"))


# ==========================================================================
# finmath.py
# ==========================================================================

class CagrGappedSeries(unittest.TestCase):
    """The Investor case (peers_se.py:1104-1135's own worked example):
    periods 2021, 2022, 2024 - 2023 is missing. A count-based ("2 periods
    back = 2 years") exponent overstates the rate; the true rate uses the
    actual 3 elapsed calendar years between 2021 and 2024."""

    def setUp(self):
        self.true_rate = 0.159
        self.v0 = 100.0
        self.v1 = self.v0 * (1.0 + self.true_rate) ** 3
        self.series = {"2021-12-31": self.v0, "2022-12-31": self.v0 * 1.05,
                       "2024-12-31": self.v1}

    def test_cagr_between_uses_elapsed_calendar_time(self):
        result = finmath.cagr_between(self.v0, self.v1, "2021-12-31", "2024-12-31")
        self.assertIsNotNone(result.value)
        self.assertAlmostEqual(result.value, self.true_rate, delta=1e-3)
        self.assertAlmostEqual(result.years, 3.0, delta=0.02)

    def test_naive_count_based_exponent_would_overstate_it(self):
        # The bug being regression-tested: treating the 2-period gap as if
        # it were exactly 2 elapsed years.
        wrong = (self.v1 / self.v0) ** (1.0 / 2.0) - 1.0
        result = finmath.cagr_between(self.v0, self.v1, "2021-12-31", "2024-12-31")
        self.assertGreater(wrong, result.value + 0.05)

    def test_cagr_series_picks_the_matching_base_and_skips_the_gap(self):
        r = finmath.cagr(self.series)
        self.assertIsNotNone(r.value)
        self.assertAlmostEqual(r.value, self.true_rate, delta=1e-3)
        self.assertEqual(r.start_period, "2021-12-31")
        self.assertEqual(r.end_period, "2024-12-31")


class CagrCurrencyChange(unittest.TestCase):
    """The Betsson case: SEK before a 2021 redenomination, EUR after.
    Compounding across the currency change fabricates a growth rate;
    finmath drops the mismatched period (or refuses outright, for a direct
    two-point call) instead."""

    def setUp(self):
        self.series = {"2020-12-31": 6000.0, "2021-12-31": 620.0,
                       "2022-12-31": 700.0, "2023-12-31": 780.0,
                       "2024-12-31": 870.0}
        self.currencies = {"2020-12-31": "SEK", "2021-12-31": "EUR",
                           "2022-12-31": "EUR", "2023-12-31": "EUR",
                           "2024-12-31": "EUR"}

    def test_cagr_between_refuses_on_currency_mismatch(self):
        r = finmath.cagr_between(6000.0, 870.0, "2020-12-31", "2024-12-31",
                                 currency0="SEK", currency1="EUR")
        self.assertIsNone(r.value)
        self.assertIn("currency", r.reason)

    def test_cagr_series_drops_the_mismatched_period_and_reports_it(self):
        r = finmath.cagr(self.series, currencies=self.currencies)
        self.assertIsNotNone(r.value)
        self.assertGreater(r.value, 0.0)  # a real ~16%/yr grower, not -35%
        self.assertTrue(any(d["period"] == "2020-12-31" for d in r.dropped))
        self.assertEqual(r.start_period, "2021-12-31")

    def test_naive_cross_currency_compound_would_be_negative(self):
        # What the un-fixed bug printed: compounding EUR 870 against SEK
        # 6000 as if they were the same unit.
        naive = (870.0 / 6000.0) ** (1.0 / 4.0) - 1.0
        self.assertLess(naive, 0.0)
        r = finmath.cagr(self.series, currencies=self.currencies)
        self.assertGreater(r.value, naive)


class CagrMinimumSpan(unittest.TestCase):
    def test_below_minimum_elapsed_span_refuses(self):
        r = finmath.cagr_between(100.0, 101.0, "2024-01-01", "2024-06-01")
        self.assertIsNone(r.value)
        self.assertIn("minimum", r.reason)

    def test_at_or_above_minimum_elapsed_span_succeeds(self):
        r = finmath.cagr_between(100.0, 110.0, "2024-01-01", "2024-10-01")
        self.assertIsNotNone(r.value)

    def test_non_positive_values_refuse(self):
        self.assertIsNone(finmath.cagr_between(-10.0, 20.0, "2020-01-01", "2024-01-01").value)
        self.assertIsNone(finmath.cagr_between(10.0, 0.0, "2020-01-01", "2024-01-01").value)

    def test_end_not_after_start_refuses(self):
        r = finmath.cagr_between(100.0, 110.0, "2024-01-01", "2023-01-01")
        self.assertIsNone(r.value)

    def test_empty_series_refuses(self):
        self.assertIsNone(finmath.cagr({}).value)


class MarginYoyMultiple(unittest.TestCase):
    def test_margin(self):
        self.assertEqual(finmath.margin(50.0, 200.0).value, 0.25)
        self.assertEqual(finmath.margin(-10.0, 100.0).value, -0.10)
        self.assertIsNone(finmath.margin(10.0, 0.0).value)
        self.assertIsNone(finmath.margin(None, 100.0).value)

    def test_yoy(self):
        self.assertAlmostEqual(finmath.yoy(110.0, 100.0).value, 0.10)
        self.assertIsNone(finmath.yoy(110.0, -5.0).value)
        self.assertIsNone(finmath.yoy(110.0, 0.0).value)

    def test_multiple_refuses_on_non_positive_denominator(self):
        self.assertIsNone(finmath.multiple(1000.0, -5.0, "SEK", "SEK").value)
        self.assertIsNone(finmath.multiple(1000.0, 0.0, "SEK", "SEK").value)

    def test_multiple_refuses_on_unknown_denominator_currency(self):
        self.assertIsNone(finmath.multiple(1000.0, 100.0, "SEK", None).value)

    def test_multiple_refuses_on_currency_mismatch(self):
        self.assertIsNone(finmath.multiple(1000.0, 100.0, "EUR", "SEK").value)

    def test_multiple_succeeds_when_currencies_match(self):
        self.assertEqual(finmath.multiple(1000.0, 100.0, "SEK", "SEK").value, 10.0)


# ==========================================================================
# http_util.py
# ==========================================================================

class CachePathHashing(unittest.TestCase):
    """mfn_news.py, peers_se.py and venues_se.py each truncate a sanitised
    cache key to ~150/120/120 characters with no hash - macro_se.py's own
    _cache_path() docstring names the exact risk: two keys sharing a long
    common prefix, differing only near the end, collide. http_util.
    cache_path() always appends a hash of the FULL key."""

    def test_shared_prefix_does_not_collide(self):
        prefix = ("https://api.example.se/v1/data?very=long&query=string"
                  "&that=repeats&and=repeats&again=andagain&") * 3
        import tempfile
        tmp = tempfile.mkdtemp(prefix="http_util_test_")
        try:
            p1 = http_util.cache_path(tmp, prefix + "key-AAAA")
            p2 = http_util.cache_path(tmp, prefix + "key-BBBB")
            self.assertNotEqual(p1, p2)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_same_key_is_deterministic(self):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="http_util_test_")
        try:
            p1 = http_util.cache_path(tmp, "https://example.se/a")
            p2 = http_util.cache_path(tmp, "https://example.se/a")
            self.assertEqual(p1, p2)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class RetryAfterParsing(unittest.TestCase):
    """Only the integer-seconds Retry-After form is honoured; the HTTP-date
    form is deliberately not guessed at."""

    class _FakeHeaders(object):
        def __init__(self, value):
            self._value = value

        def get(self, name, default=None):
            return self._value if name == "Retry-After" else default

    class _FakeHTTPError(object):
        def __init__(self, value):
            self.headers = RetryAfterParsing._FakeHeaders(value)

    def test_integer_seconds(self):
        self.assertEqual(http_util._retry_after_seconds(self._FakeHTTPError("5")), 5)

    def test_missing_header(self):
        self.assertIsNone(http_util._retry_after_seconds(self._FakeHTTPError(None)))

    def test_http_date_form_is_not_guessed(self):
        err = self._FakeHTTPError("Wed, 21 Oct 2026 07:28:00 GMT")
        self.assertIsNone(http_util._retry_after_seconds(err))


class UserAgentConstants(unittest.TestCase):
    """These exact strings are what roughly fifteen scripts already send;
    changing them changes how every free source out there sees this
    toolkit, so they are pinned here."""

    def test_plain_ua_unchanged(self):
        self.assertEqual(http_util.UA, "Mozilla/5.0 (compatible; investment-analyst-skill/1.0)")

    def test_browser_ua_unchanged(self):
        self.assertEqual(
            http_util.UA_BROWSER,
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


# ==========================================================================
# _bootstrap.py
# ==========================================================================

class BootstrapLoading(unittest.TestCase):
    def test_load_hard_required_sibling(self):
        mod = _bootstrap.load("numparse")
        self.assertEqual(mod.to_number("28 838"), 28838.0)

    def test_load_missing_sibling_raises(self):
        with self.assertRaises((ImportError, OSError, FileNotFoundError)):
            _bootstrap.load("this_module_does_not_exist_anywhere")

    def test_soft_load_missing_sibling_returns_none(self):
        self.assertIsNone(_bootstrap.soft_load("this_module_does_not_exist_anywhere"))

    def test_soft_load_catches_system_exit(self):
        """The bug this module exists to prevent: valuation_gate.py:100's
        `except Exception` does NOT catch SystemExit, so a sibling script
        that raises SystemExit during import-time setup (every sibling in
        this toolkit's own hard-failure convention) would crash a caller
        using that pattern instead of degrading gracefully. soft_load() must
        catch it.
        """
        import tempfile
        scripts_dir = _bootstrap.SCRIPTS_DIR
        fd, temp_path = tempfile.mkstemp(suffix=".py", dir=scripts_dir)
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write("raise SystemExit('DATA NOT AVAILABLE: simulated failure')\n")
            temp_name = os.path.splitext(os.path.basename(temp_path))[0]
            result = _bootstrap.soft_load(temp_name)
            self.assertIsNone(result)
        finally:
            os.remove(temp_path)

    def test_ensure_path_appends_never_inserts_at_front(self):
        """ensure_path() must APPEND, so a scripts/ filename can never
        shadow a stdlib module of the same name.

        The assertion has to be made on a clean sys.path: nine sibling
        scripts still carry their own `sys.path.insert(0, HERE)` prologue,
        so by the time a full suite run reaches this test some other module
        may already have put SCRIPTS_DIR at the front. That would be a
        finding about THOSE files, not about ensure_path(), so this test
        isolates itself rather than measuring the leftovers."""
        saved = list(sys.path)
        try:
            sys.path[:] = [p for p in sys.path if p != _bootstrap.SCRIPTS_DIR]
            _bootstrap.ensure_path()
            self.assertIn(_bootstrap.SCRIPTS_DIR, sys.path)
            self.assertNotEqual(sys.path[0], _bootstrap.SCRIPTS_DIR)
            self.assertEqual(sys.path[-1], _bootstrap.SCRIPTS_DIR)
            # calling twice must not duplicate the entry
            before = sys.path.count(_bootstrap.SCRIPTS_DIR)
            _bootstrap.ensure_path()
            self.assertEqual(sys.path.count(_bootstrap.SCRIPTS_DIR), before)
        finally:
            sys.path[:] = saved


if __name__ == "__main__":
    unittest.main()
