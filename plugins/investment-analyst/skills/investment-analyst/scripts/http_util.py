#!/usr/bin/env python3
"""One HTTP fetcher - stdlib only (urllib), no third-party dependency.

Roughly fifteen scripts in this folder hand-roll their own
`urllib.request.Request(url, headers={"User-Agent": UA, ...})`, each with its
own copy of one of two User-Agent strings. Of all of them, only
macro_se.py:120-151 retries on 429/502/503/504 with backoff; every other
fetcher raises (or returns None) on the first transient failure, even though
the free, keyless, rate-limited sources this toolkit lives on (Riksbanken,
SCB, MFN, FI, Nasdaq Nordic, ESMA FIRDS...) are exactly the kind of source
that returns a 429 under ordinary use and would succeed on the very next try.
get() below generalises macro_se.py's retry loop so every caller gets it, not
just the one script that happened to need it first.

CACHE KEY COLLISIONS. macro_se.py's own _cache_path() docstring names the
risk directly: "truncating the key alone is not safe - two SCB POST bodies
can share their first 150 characters and differ only in the SNI code near
the end", so it hashes the full key. mfn_news.py:103, peers_se.py:535 and
venues_se.py:232 each define their own _cache_path() that truncates a
sanitised key to 150/120/120 characters WITHOUT a hash - exactly the
collision macro_se.py's own docstring warns about, just not yet triggered in
those three scripts by an unlucky pair of URLs. cache_path() below always
appends a hash of the full key, generalising macro_se.py's fix rather than
its two siblings' un-fixed originals.

Deliberately NOT provided: any identifying contact header (a "From:" address,
an app-with-an-email-in-it User-Agent). That is a hard project constraint -
this toolkit fetches free, keyless, anonymous public data, and does not ask
any source to be able to reach a person over it.
"""
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

# The plain, compatibility-only UA used by most fetchers in this toolkit
# (macro_se.py, mfn_news.py, insider_se.py, quote.py, esef_fundamentals.py,
# cision_news.py, guidance_track.py, ownership_se.py, short_se.py,
# valuation_gate.py all use this exact string - kept verbatim, since changing
# it changes how every one of those sources sees this toolkit).
UA = "Mozilla/5.0 (compatible; investment-analyst-skill/1.0)"

# A browser-shaped UA for the handful of hosts that reject or silently hang
# on the plain UA above - Nasdaq Nordic is the documented offender
# (venues_se.py, company_resolve.py, corporate_actions.py, nordic_shares.py,
# ir_discovery.py all use this exact string for that reason).
UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

DEFAULT_TIMEOUT = 30
DEFAULT_MAX_ATTEMPTS = 4
RETRY_STATUSES = (429, 502, 503, 504)

CACHE_ROOT = os.path.join(tempfile.gettempdir(), "investment-analyst-cache")


class FetchError(Exception):
    """A GET/POST failed after retries. Wraps the last urllib exception seen
    so a caller can still inspect it, without every caller needing to know
    which of urllib.error.HTTPError/URLError/OSError/TimeoutError to catch."""

    def __init__(self, message, last_exc=None):
        super(FetchError, self).__init__(message)
        self.last_exc = last_exc


def _retry_after_seconds(http_error):
    """Parse a Retry-After header. Only the integer-seconds form is honoured
    - the HTTP-date form is rare enough on the free/keyless sources this
    toolkit touches that guessing its format wrong (and waiting the wrong
    amount of time, or raising) is a worse outcome than falling back to
    plain exponential backoff, which is what returning None here does."""
    headers = getattr(http_error, "headers", None)
    val = headers.get("Retry-After") if headers else None
    if not val:
        return None
    try:
        return max(0, int(str(val).strip()))
    except ValueError:
        return None


def get(url, timeout=DEFAULT_TIMEOUT, headers=None, data=None, ua=UA,
        max_attempts=DEFAULT_MAX_ATTEMPTS, backoff=1.0,
        retry_statuses=RETRY_STATUSES):
    """GET (POST, when `data` is given) with retry and backoff on 429/5xx,
    honouring a Retry-After header when the response carries one.

    Returns raw response bytes. Raises FetchError on final failure - this
    function does not swallow a final failure into None or a stale value;
    callers that want a "return None on failure" or "serve a stale cache
    entry on failure" contract build it on top of this, the way
    cached_get() below does, matching how the sibling fetchers in this
    toolkit already choose their own degrade behaviour independently.
    """
    hdrs = {"User-Agent": ua}
    hdrs.update(headers or {})
    delay = backoff
    last = None
    for attempt in range(max_attempts):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in retry_statuses and attempt < max_attempts - 1:
                wait = _retry_after_seconds(e)
                if wait is None:
                    wait = delay
                    delay *= 2
                time.sleep(wait)
                continue
            raise FetchError("HTTP %s for %s" % (e.code, url), last_exc=e)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            if attempt < max_attempts - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise FetchError("%s unreachable (%s)" % (url, e), last_exc=e)
    raise FetchError("%s unreachable (%s)" % (url, last), last_exc=last)  # pragma: no cover


def get_json(url, timeout=DEFAULT_TIMEOUT, headers=None, data=None, ua=UA, **kwargs):
    """get(), decoded as JSON. Tries utf-8, then the two encodings this
    toolkit has actually met on a Nordic source that did not declare
    utf-8 (macro_se.py's own fetch_json() does the same fallback)."""
    raw = get(url, timeout=timeout, headers=headers, data=data, ua=ua, **kwargs)
    for enc in ("utf-8", "cp1252", "iso-8859-1"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, ValueError):
            continue
    raise FetchError("unparseable JSON response from %s" % url)


def cache_path(cache_dir, key):
    """A readable prefix plus a full-key SHA-256 hash.

    Truncating the sanitised key alone (mfn_news.py, peers_se.py, venues_se.py
    all do this today) is not safe: two keys that share a long common prefix
    and differ only near the end - two URLs differing only in a trailing
    query parameter, two cache keys built from a long POST body that only
    diverges near the tail - collide and silently serve one request's answer
    for another's. Hashing the full key, the way macro_se.py's own
    _cache_path() already does, is the fix; this generalises it.
    """
    os.makedirs(cache_dir, exist_ok=True)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:80]
    return os.path.join(cache_dir, "%s-%s" % (safe, digest))


def cached_get(url, cache_dir=CACHE_ROOT, ttl=3600, cache_key=None,
               timeout=DEFAULT_TIMEOUT, headers=None, data=None, ua=UA, **kwargs):
    """get() with an on-disk cache, hashed key (see cache_path()).

    A stale cache entry beats no answer: if the live fetch ultimately fails
    (FetchError) and something is already on disk, that stale copy is
    returned rather than propagating the error - matching macro_se.fetch()'s
    own "a stale value beats hanging on retries" behaviour. A caller that
    needs to know whether what it got was fresh should stat the file's mtime
    itself; the observation date belongs with the caller's own data model,
    not with this cache.
    """
    key = cache_key if cache_key is not None else (
        url + "|" + (data.decode("utf-8", "replace") if data else ""))
    path = cache_path(cache_dir, key)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        with open(path, "rb") as fh:
            return fh.read()
    try:
        raw = get(url, timeout=timeout, headers=headers, data=data, ua=ua, **kwargs)
    except FetchError:
        if os.path.exists(path):
            with open(path, "rb") as fh:
                return fh.read()
        raise
    with open(path, "wb") as fh:
        fh.write(raw)
    return raw


def cached_get_json(url, cache_dir=CACHE_ROOT, ttl=3600, **kwargs):
    raw = cached_get(url, cache_dir=cache_dir, ttl=ttl, **kwargs)
    for enc in ("utf-8", "cp1252", "iso-8859-1"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, ValueError):
            continue
    raise FetchError("unparseable JSON response from %s" % url)


# --------------------------------------------------------------------------
# Selftest - offline only. Nothing here makes a real network call; a live
# fetch is exercised by whichever caller opts into network tests (see
# tests/helpers.py's `network` skip-gate), not by this module's own
# --selftest.
# --------------------------------------------------------------------------

def _selftest():
    ok = 0

    assert UA.startswith("Mozilla/5.0 (compatible;")
    assert "Chrome" in UA_BROWSER
    ok += 1

    class _FakeHeaders(object):
        def __init__(self, value):
            self._value = value

        def get(self, name, default=None):
            return self._value if name == "Retry-After" else default

    class _FakeHTTPError(object):
        def __init__(self, value):
            self.headers = _FakeHeaders(value)

    assert _retry_after_seconds(_FakeHTTPError("5")) == 5
    assert _retry_after_seconds(_FakeHTTPError(None)) is None
    assert _retry_after_seconds(_FakeHTTPError("Wed, 21 Oct 2026 07:28:00 GMT")) is None
    ok += 1

    # Two keys sharing a long common prefix, differing only near the end,
    # must not collide - the exact hazard the truncate-only cache_path()
    # implementations elsewhere in this toolkit are exposed to.
    import shutil
    tmp = tempfile.mkdtemp(prefix="http_util_selftest_")
    try:
        prefix = "https://api.example.se/v1/data?very=long&query=string&that=repeats&a=" * 3
        p1 = cache_path(tmp, prefix + "key-AAAA")
        p2 = cache_path(tmp, prefix + "key-BBBB")
        assert p1 != p2, (p1, p2)
        ok += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("http_util selftest: %d assertions ok" % ok)
    return 0


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return _selftest()
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
