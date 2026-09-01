#!/usr/bin/env python3
"""Regression tests for four verified defects in ir_discovery.py.

All offline - every network call is monkeypatched out. Each test is written
so it fails against the pre-fix code (documented per-test below).

  1. --no-cache was a no-op: http_get/http_json bound `ttl=HTML_TTL` /
     `ttl=JSON_TTL` as default arguments, evaluated once at def time, so
     rebinding the globals in main() afterwards changed nothing actually
     read by a call.
  2. label_period() took max(years) over "filename + link text", so a
     "(publicerad 2025)" / "published March 2024" clause dating the release
     beat the year that names the reporting period.
  3. same_site() compared only the last two dotted labels, so any two
     distinct domains under a common two-label public suffix (a.co.uk vs
     b.co.uk) counted as the same site.
  4. http_get() read exactly max_bytes and returned whatever came back,
     silently truncating an oversized response instead of refusing it.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

ir_discovery = load("ir_discovery")


# --------------------------------------------------------------------------
# Bug 1 - --no-cache is a no-op
# --------------------------------------------------------------------------

class NoCacheZeroesEffectiveTTL(unittest.TestCase):
    """Assert on the ttl the function actually resolves and uses, not on
    the module global by itself - the old bug had the global correctly at
    zero while the function still used its stale default."""

    def setUp(self):
        self._orig_html_ttl = ir_discovery.HTML_TTL
        self._orig_json_ttl = ir_discovery.JSON_TTL
        self._orig_cached = ir_discovery._cached

    def tearDown(self):
        ir_discovery.HTML_TTL = self._orig_html_ttl
        ir_discovery.JSON_TTL = self._orig_json_ttl
        ir_discovery._cached = self._orig_cached

    def test_http_get_reads_html_ttl_at_call_time(self):
        captured = {}

        def fake_cached(path, ttl):
            captured["ttl"] = ttl
            return b'{"url": "http://example.com/", "ct": "text/html"}\n\n<html></html>'

        ir_discovery._cached = fake_cached
        # Simulate what --no-cache does *after* the module (and therefore
        # http_get's default argument, under the old code) has already loaded.
        ir_discovery.HTML_TTL = 0
        ir_discovery.http_get("http://example.com/")
        self.assertEqual(
            captured.get("ttl"), 0,
            "http_get used a stale ttl instead of reading HTML_TTL at call time")

    def test_http_json_reads_json_ttl_at_call_time(self):
        captured = {}

        def fake_cached(path, ttl):
            captured["ttl"] = ttl
            return b'{"ok": true}'

        ir_discovery._cached = fake_cached
        ir_discovery.JSON_TTL = 0
        ir_discovery.http_json("http://example.com/api")
        self.assertEqual(
            captured.get("ttl"), 0,
            "http_json used a stale ttl instead of reading JSON_TTL at call time")


class NoCacheFlagZeroesAllThreeTTLs(unittest.TestCase):
    """main()'s --no-cache handling must clear ROBOTS_TTL too, not just
    HTML_TTL/JSON_TTL - otherwise a stale robots.txt keeps being honoured
    after the operator asked for a clean run."""

    def setUp(self):
        self._orig_html_ttl = ir_discovery.HTML_TTL
        self._orig_json_ttl = ir_discovery.JSON_TTL
        self._orig_robots_ttl = ir_discovery.ROBOTS_TTL
        self._orig_argv = sys.argv
        self._orig_discover = ir_discovery.discover
        self._orig_report = ir_discovery.report

    def tearDown(self):
        ir_discovery.HTML_TTL = self._orig_html_ttl
        ir_discovery.JSON_TTL = self._orig_json_ttl
        ir_discovery.ROBOTS_TTL = self._orig_robots_ttl
        sys.argv = self._orig_argv
        ir_discovery.discover = self._orig_discover
        ir_discovery.report = self._orig_report

    def test_no_cache_zeroes_robots_ttl_as_well(self):
        ir_discovery.discover = lambda *a, **k: {
            "ir_url": "http://example.com/", "notes": []}
        ir_discovery.report = lambda *a, **k: None
        sys.argv = ["ir_discovery.py", "Sandvik", "--no-cache"]
        ir_discovery.main()
        self.assertEqual(ir_discovery.HTML_TTL, 0)
        self.assertEqual(ir_discovery.JSON_TTL, 0)
        self.assertEqual(ir_discovery.ROBOTS_TTL, 0,
                          "--no-cache left ROBOTS_TTL untouched")


# --------------------------------------------------------------------------
# Bug 2 - publication year mistaken for the reporting period
# --------------------------------------------------------------------------

class LabelPeriodIgnoresPublicationClause(unittest.TestCase):
    def test_swedish_publicerad_clause_does_not_win_the_year(self):
        blob = ("arsredovisning-2024.pdf "
                "Årsredovisning 2024 (publicerad 2025)")
        self.assertEqual(ir_discovery.label_period(blob), "FY 2024")

    def test_english_published_clause_does_not_win_the_year(self):
        blob = ("annual-report-2023.pdf "
                "Annual Report 2023 - published March 2024")
        self.assertEqual(ir_discovery.label_period(blob), "FY 2023")

    def test_plain_filename_without_a_publication_clause_is_unaffected(self):
        # Guard against over-stripping: nothing here should be touched by the
        # "published ..." removal, so the plain year must still come through.
        blob = "annual-report-2025.pdf Annual Report 2025"
        self.assertEqual(ir_discovery.label_period(blob), "FY 2025")


# --------------------------------------------------------------------------
# Bug 3 - same_site treats every two-label public suffix as one site
# --------------------------------------------------------------------------

class SameSitePublicSuffixGuard(unittest.TestCase):
    def test_distinct_co_uk_domains_are_not_the_same_site(self):
        self.assertFalse(ir_discovery.same_site(
            "https://a.co.uk/x", "https://b.co.uk/y"))

    def test_subdomains_of_the_same_issuer_are_the_same_site(self):
        self.assertTrue(ir_discovery.same_site(
            "https://ir.sandvik.com/a", "https://www.sandvik.com/b"))


# --------------------------------------------------------------------------
# Bug 4 - an oversized response is silently truncated
# --------------------------------------------------------------------------

class _FakeResponse(object):
    def __init__(self, body, url="http://example.com/big.pdf"):
        self.body = body
        self.url = url
        self.headers = {"Content-Type": "application/pdf"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=None):
        if n is None:
            return self.body
        return self.body[:n]


class HttpGetRefusesOversizedResponse(unittest.TestCase):
    def setUp(self):
        self._orig_cached = ir_discovery._cached
        self._orig_store = ir_discovery._store
        self._orig_urlopen = ir_discovery.urllib.request.urlopen
        ir_discovery._cached = lambda path, ttl: None
        ir_discovery._store = lambda path, blob: None

    def tearDown(self):
        ir_discovery._cached = self._orig_cached
        ir_discovery._store = self._orig_store
        ir_discovery.urllib.request.urlopen = self._orig_urlopen

    def test_response_one_byte_over_the_cap_raises(self):
        max_bytes = 16
        body = b"x" * (max_bytes + 1)
        ir_discovery.urllib.request.urlopen = \
            lambda *a, **k: _FakeResponse(body)
        with self.assertRaises(ir_discovery.Fetch):
            ir_discovery.http_get("http://example.com/big.pdf",
                                  ttl=0, max_bytes=max_bytes)

    def test_response_exactly_at_the_cap_is_not_rejected(self):
        # Control: the fix must not reject a legitimately-sized response.
        max_bytes = 16
        body = b"x" * max_bytes
        ir_discovery.urllib.request.urlopen = \
            lambda *a, **k: _FakeResponse(body)
        final, got, ct = ir_discovery.http_get(
            "http://example.com/ok.pdf", ttl=0, max_bytes=max_bytes)
        self.assertEqual(got, body)


if __name__ == "__main__":
    unittest.main()
