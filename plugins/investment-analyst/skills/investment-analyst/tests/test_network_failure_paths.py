#!/usr/bin/env python3
"""Network-failure paths must degrade, not crash.

mfn_news.fetch() must turn a URLError or a timeout into SystemExit
("DATA NOT AVAILABLE: ..."), never let the bare urllib exception propagate.
Three other scripts in this toolkit (verify_filing.py, ttm_engine.py,
guidance_track.py) catch only SystemExit around their mfn_news calls - a
bare URLError/TimeoutError escaping fetch() used to crash all three instead
of producing the intended graceful skip.

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


if __name__ == "__main__":
    unittest.main()
