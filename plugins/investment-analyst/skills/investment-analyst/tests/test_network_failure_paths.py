#!/usr/bin/env python3
"""Network-failure paths must degrade, not crash.

Two distinct bugs:

  1. mfn_news.fetch() must turn a URLError or a timeout into SystemExit
     ("DATA NOT AVAILABLE: ..."), never let the bare urllib exception
     propagate. Three other scripts in this toolkit (verify_filing.py,
     ttm_engine.py, guidance_track.py) catch only SystemExit around their
     mfn_news calls - a bare URLError/TimeoutError escaping fetch() used to
     crash all three instead of producing the intended graceful skip.

  2. quote.from_nasdaq() must return None on a timeout rather than let the
     exception propagate, so an already-successfully-fetched Yahoo quote is
     not thrown away just because the (optional, US-only) Nasdaq
     cross-check timed out.

Every urlopen/fetch call is monkeypatched to fail deterministically, so this
never touches the network - including on a real outage, which would
otherwise make this file indistinguishable from a genuine regression.
"""
import os
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

mfn_news = load("mfn_news")
quote = load("quote")


class MfnFetchDegradesOnNetworkFailure(unittest.TestCase):
    def setUp(self):
        self._real_urlopen = mfn_news.urllib.request.urlopen

    def tearDown(self):
        mfn_news.urllib.request.urlopen = self._real_urlopen

    def test_urlerror_raises_systemexit_not_urlerror(self):
        def raise_urlerror(*_a, **_k):
            raise urllib.error.URLError("simulated network outage")
        mfn_news.urllib.request.urlopen = raise_urlerror
        with self.assertRaises(SystemExit) as ctx:
            mfn_news.fetch("/all/a.json")
        self.assertIn("DATA NOT AVAILABLE", str(ctx.exception))

    def test_timeout_raises_systemexit_not_timeouterror(self):
        def raise_timeout(*_a, **_k):
            raise TimeoutError("simulated timeout")
        mfn_news.urllib.request.urlopen = raise_timeout
        with self.assertRaises(SystemExit) as ctx:
            mfn_news.fetch("/all/a.json")
        self.assertIn("DATA NOT AVAILABLE", str(ctx.exception))

    def test_a_successful_fetch_still_returns_the_parsed_json(self):
        """Control: the failure handling must not have broken the happy
        path."""
        import io as _io
        class _Resp(object):
            def __enter__(self_):
                return self_
            def __exit__(self_, *a):
                return False
            def read(self_):
                return b'{"ok": true}'
        mfn_news.urllib.request.urlopen = lambda *a, **k: _Resp()
        self.assertEqual(mfn_news.fetch("/all/a.json"), {"ok": True})


class QuoteFromNasdaqDegradesOnTimeout(unittest.TestCase):
    def setUp(self):
        self._real_fetch = quote.fetch
        self._real_from_yahoo = quote.from_yahoo

    def tearDown(self):
        quote.fetch = self._real_fetch
        quote.from_yahoo = self._real_from_yahoo

    def test_from_nasdaq_returns_none_on_timeout(self):
        def raise_timeout(*_a, **_k):
            raise TimeoutError("simulated timeout")
        quote.fetch = raise_timeout
        result = quote.from_nasdaq("NVDA")
        self.assertIsNone(result, "from_nasdaq must return None on a "
                          "timeout, not propagate the exception")

    def test_report_keeps_an_already_fetched_yahoo_quote_when_nasdaq_times_out(self):
        """The named consequence: a US ticker's report() calls both
        from_yahoo and from_nasdaq. If from_nasdaq's timeout were to
        propagate instead of returning None, report() would crash and the
        Yahoo quote it already had in hand would never reach the caller."""
        yahoo_quote = {
            "source": "Yahoo Finance (unofficial endpoint)", "symbol": "NVDA",
            "exchange": "NASDAQ", "currency": "USD", "price": 123.45,
            "previous_close": 120.0, "fifty_two_week_high": 150.0,
            "fifty_two_week_low": 100.0,
            "as_of_utc": "2026-08-31T12:00:00+00:00",
            "timezone": "America/New_York",
        }
        quote.from_yahoo = lambda symbol: dict(yahoo_quote, symbol=symbol)

        def raise_timeout(*_a, **_k):
            raise TimeoutError("simulated timeout")
        quote.fetch = raise_timeout

        ok = quote.report("NVDA", as_json=False)
        self.assertTrue(ok, "report() must not crash or report failure when "
                        "only the optional Nasdaq cross-check times out")


if __name__ == "__main__":
    unittest.main()
