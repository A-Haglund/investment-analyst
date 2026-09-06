#!/usr/bin/env python3
"""Regulatory disclosures and press releases for Norwegian issuers, from Oslo Børs NewsWeb.

Oslo Børs NewsWeb is the Norwegian OAM (officially appointed mechanism) and the
authoritative source for Norwegian listed companies' MAR-regulated disclosures and press
releases; all messages are public, keyless and free.

The regulatory signal lives in `category[].category_en`, NOT in `oamMandatory`: that field
measured 0 on all 601 messages of a 36-day window, including every half-year report and
every inside-information release, so it is surfaced and never relied on. flatten() carries
the full measurement and the mapping rules that follow from it.

Usage:
    python oslo_bors.py --from 2026-09-04 --to 2026-09-05
    python oslo_bors.py --from 2026-09-01 --to 2026-09-05 --json
    python oslo_bors.py --from 2026-09-04 --to 2026-09-05 --issuer "Telenor ASA"
    python oslo_bors.py --selftest

Free NewsWeb API, no API key. The /list endpoint requires an explicit date range;
queries without a date window return only 2 messages and silently omit almost everything.

The overflow flag is always surfaced. Every function that returns messages returns it
alongside them, and the CLI reports it on every path including --json - a truncated
window is never presented as complete, and an empty truncated window is reported as
"could not check", not as "nothing was published".
"""
import argparse
import datetime
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api3.oslo.oslobors.no/v1/newsreader"
UA = "Mozilla/5.0 (compatible; investment-analyst-skill/1.0)"

# Three TTLs, because three different things are being cached.
#   RECENT  - a window that can still gain messages (it ends today or later).
#   CLOSED  - a window that ended before today: no message can be added to it
#             any more, so re-asking every 15 minutes is a request the source
#             did not need to serve. mfn_news.fetch_company_pages splits its
#             TTL the same way, on offset == 0 rather than on a date.
#   MESSAGE - one message, immutable once published.
CACHE_TTL_RECENT = 15 * 60           # 15 minutes
CACHE_TTL_CLOSED = 30 * 24 * 3600    # 30 days (the window can no longer change)
CACHE_TTL_MESSAGE = 365 * 24 * 3600  # 365 days (immutable once published)

CACHE = os.path.join(tempfile.gettempdir(), "oslo-bors-cache")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def fetch(endpoint, **params):
    """GET JSON from the API. Raises SystemExit on network/HTTP/parse error."""
    url = BASE + endpoint
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        raise SystemExit("DATA NOT AVAILABLE: Oslo Børs returned HTTP %s for %s" % (e.code, url))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SystemExit("DATA NOT AVAILABLE: Oslo Børs unreachable (%s)" % e)
    # A 200 carrying an HTML error page, a WAF interstitial or a truncated body
    # is a failure, not data. json.JSONDecodeError subclasses ValueError.
    # mfn_news.fetch_json_cached separates the parse from the transport for the
    # same reason; here the failure is hard rather than soft because callers of
    # fetch() have no fallback source to fall through to.
    try:
        return json.loads(body)
    except ValueError as e:
        raise SystemExit("DATA NOT AVAILABLE: Oslo Børs returned a non-JSON body for %s (%s)"
                         % (url, e))


def _cache_path(key):
    """Hash a cache key into a filesystem-safe name."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)[:150]
    return os.path.join(CACHE, safe)


def fetch_json_cached(cache_key, ttl, fetcher_func, cache_if=None):
    """GET JSON with on-disk cache. Returns parsed JSON, or raises SystemExit on error.

    `fetcher_func` is a callable that takes no arguments and returns parsed JSON.
    If the cache is stale or missing, fetcher_func is called to refresh it.

    A failure is never cached: the file is written only after fetcher_func has
    returned, so a fetcher that raises leaves nothing behind and the next run
    retries. Same rule as thesis_ledger.cached() and peers_se.cached() - a
    source that was down must be retried, not remembered as an answer.

    `cache_if`, when given, is consulted on the freshly fetched value and must
    return True for it to be written. That keeps a well-formed *negative*
    answer ("there is no such message") out of a 365-day cache entry, which is
    a different thing from a failure but equally wrong to freeze for a year.
    """
    path = _cache_path(cache_key)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        try:
            with open(path, "rb") as fh:
                return json.loads(fh.read())
        except (OSError, ValueError):
            pass  # corrupt cache entry; refetch

    data = fetcher_func()

    if cache_if is None or cache_if(data):
        try:
            os.makedirs(CACHE, exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        except OSError:
            pass  # cache is an optimisation only
    return data


def _iso_date(value, label):
    """Normalise one date argument to YYYY-MM-DD, or raise SystemExit.

    `datetime.datetime` is a SUBCLASS of `datetime.date`, so an
    `isinstance(x, datetime.date)` guard alone lets a datetime through and
    `.isoformat()` then yields "2026-09-05T14:30:00". That goes on the wire as
    fromDate=<timestamp> and mints a fresh cache key on every single call, so
    the cache never hits. Test for datetime FIRST and drop the time part.

    Validation lives here rather than only in main() because these functions
    are importable: an inverted range, "2026-13-45" or None reaching the wire
    from a caller that never went through argparse is the same defect with no
    error message attached.
    """
    if isinstance(value, datetime.datetime):
        value = value.date()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if not isinstance(value, str) or not value.strip():
        raise SystemExit("DATA NOT AVAILABLE: %s must be a YYYY-MM-DD string or a date "
                         "object (got %r)" % (label, value))
    try:
        parsed = datetime.datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit("DATA NOT AVAILABLE: %s must be YYYY-MM-DD (got %r)" % (label, value))
    # Round-tripping through date() also normalises "2026-9-5" to "2026-09-05",
    # so two spellings of one day share a cache entry instead of two.
    return parsed.isoformat()


def _date_window(from_date, to_date):
    """Validate and normalise a (from, to) pair. Raises SystemExit if unusable."""
    start = _iso_date(from_date, "from_date")
    end = _iso_date(to_date, "to_date")
    if start > end:
        # The API answers an inverted range with an empty window, which reads
        # as "nothing was published" - a wrong answer to a wrong question.
        raise SystemExit("DATA NOT AVAILABLE: date range is inverted (%s to %s)" % (start, end))
    return start, end


def _list_ttl(to_date):
    """15 minutes while the window can still change, 30 days once it cannot.

    "Closed" is judged against today's UTC date, deliberately not the local
    one: Oslo is UTC+1/+2, so the UTC date is never ahead of the Oslo date and
    a window can therefore only be declared closed too late, never too early.
    """
    today_utc = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    return CACHE_TTL_CLOSED if to_date < today_utc else CACHE_TTL_RECENT


def releases_between(from_date, to_date):
    """Fetch regulatory disclosures published between two dates (inclusive).

    Returns (messages, overflow_bool). The overflow flag indicates whether the
    API truncated the result window - a truncated window is never presented as
    complete, so callers can detect incompleteness and act on it.

    `from_date` and `to_date` are YYYY-MM-DD strings, or date/datetime objects.

    An empty list with overflow False is a real answer, not a failure: NewsWeb
    publishes nothing on a Sunday. Anything that prevents the window from being
    established - transport failure, HTTP error, non-JSON body, or a 200 whose
    envelope is not the shape this module knows - raises SystemExit instead, so
    "could not check" can never be read as "nothing was published".
    """
    from_date, to_date = _date_window(from_date, to_date)

    # Cache key includes the normalised date range. TTL depends on whether the
    # window can still gain messages - see _list_ttl().
    cache_key = "list-%s-%s" % (from_date, to_date)

    def fetcher():
        data = fetch("/list", fromDate=from_date, toDate=to_date)
        # Validate the envelope rather than the old `(data or {}).get("data") or {}`:
        # that idiom turns an unrecognised 200 into an empty window, i.e. answers
        # "no disclosures" to a question that was never answered - and then caches
        # that for the TTL. Absence and unavailability must not share a value.
        envelope = data.get("data") if isinstance(data, dict) else None
        if not isinstance(envelope, dict) or not isinstance(envelope.get("messages"), list):
            raise SystemExit("DATA NOT AVAILABLE: Oslo Børs returned an unrecognised /list "
                             "envelope for %s to %s" % (from_date, to_date))
        return {"messages": envelope["messages"], "overflow": bool(envelope.get("overflow"))}

    window = fetch_json_cached(cache_key, ttl=_list_ttl(to_date), fetcher_func=fetcher)
    # Re-check after the cache: an entry written by an older version of this
    # module, or a half-written file, must not silently become an empty window.
    if not isinstance(window, dict) or not isinstance(window.get("messages"), list):
        raise SystemExit("DATA NOT AVAILABLE: cached /list window for %s to %s is unusable"
                         % (from_date, to_date))
    return window["messages"], bool(window.get("overflow"))


def message(message_id):
    """Fetch one message's full content including body and attachments.

    Returns the message dict, or None when NewsWeb answers, well-formedly, that
    there is no such message. Every other outcome - unreachable, HTTP 404/500,
    non-JSON body, unrecognised envelope - raises SystemExit: a message that
    could not be fetched is not a message that does not exist, and collapsing
    both into None hands the caller a silent absence.

    Individual messages are immutable once published, so a successfully fetched
    one is cached for a year. A failure is not cached at all, and the negative
    answer is not cached either (`cache_if`): a 365-day "does not exist" is an
    assertion this module has no business making from one round trip.
    """
    if message_id in (None, ""):
        raise SystemExit("DATA NOT AVAILABLE: message() needs a message id")
    cache_key = "message-%s" % message_id

    def fetcher():
        data = fetch("/message", messageId=message_id)
        envelope = data.get("data") if isinstance(data, dict) else None
        if not isinstance(envelope, dict) or "message" not in envelope:
            raise SystemExit("DATA NOT AVAILABLE: Oslo Børs returned an unrecognised /message "
                             "envelope for message %s" % message_id)
        msg = envelope["message"]
        if msg is None:
            return {"found": False, "message": None}
        if not isinstance(msg, dict):
            raise SystemExit("DATA NOT AVAILABLE: Oslo Børs returned a malformed message body "
                             "for message %s" % message_id)
        return {"found": True, "message": msg}

    payload = fetch_json_cached(cache_key, ttl=CACHE_TTL_MESSAGE, fetcher_func=fetcher,
                                cache_if=lambda p: bool(p.get("found")))
    if not isinstance(payload, dict):
        raise SystemExit("DATA NOT AVAILABLE: cached message %s is unusable" % message_id)
    return payload.get("message")


def releases_for_issuer(sign_or_name, from_date, to_date):
    """Fetch messages for one issuer, filtered locally.

    Filter by issuerSign (exact, case-insensitive) or issuerName (exact).
    Server-side filtering is not available - the API's ?issuer= parameter
    returns empty results even for valid issuer ids.

    Returns (messages, overflow_bool), the same pair as releases_between().
    The overflow flag belongs to the WHOLE window, not to the filtered subset,
    and it matters more here, not less: if the API truncated the window, the
    issuer's message may be exactly what was dropped, so an empty filtered list
    from a truncated window establishes nothing.
    """
    all_messages, overflow = releases_between(from_date, to_date)

    # Filter locally on issuerSign (case-insensitive) or issuerName (exact).
    needle_sign = (sign_or_name or "").upper()
    needle_name = sign_or_name or ""

    filtered = []
    for msg in all_messages:
        issuer_sign = (msg.get("issuerSign") or "").upper()
        issuer_name = msg.get("issuerName") or ""
        if issuer_sign == needle_sign or issuer_name == needle_name:
            filtered.append(msg)
    return filtered, overflow


# --- Category and title vocabulary ----------------------------------------
#
# Every string below was harvested from a live 601-message window
# (2026-08-01 to 2026-08-31), not from documentation. Counts in that window
# are given where they explain a choice.

# The only two categories that name a periodic report. There is no
# "QUARTERLY REPORT" and no "INTERIM REPORT" in this vocabulary - both were
# invented by an earlier version of this file. Oslo Børs files Q1 and Q3 under
# HALF YEAR FINANCIAL REPORT (167 in the window) and a substantial minority
# under the catch-all below (122).
REPORT_CATEGORIES = {
    "ANNUAL FINANCIAL REPORT",
    "HALF YEAR FINANCIAL REPORT",
}

# The Transparency Directive catch-all. Unlike every other category it names no
# content type at all, so it carries everything from bond tap issues and AGM
# summonses to genuine quarterly reports ("Kvartalsrapport for andre kvartal
# 2026", "Q2 26 Interim report", "Interim Financial Report for Q2/H1 ..."). It
# is the ONE category where the title has to be read, because the category
# itself is silent by construction.
CATCH_ALL_CATEGORY = ("ADDITIONAL REGULATED INFORMATION REQUIRED TO BE DISCLOSED "
                      "UNDER THE LAWS OF A MEMBER STATE")

# Issuer material that is explicitly not regulated disclosure.
NON_REGULATORY_CATEGORIES = {
    "NON-REGULATORY PRESS RELEASES",
}

# ...and material that is not issuer disclosure at all because somebody else
# published it. Matched as a PREFIX, not as a fixed list: the observed members
# are "ANNOUNCEMENT FROM OSLO BØRS", "ANNOUNCEMENT FROM OTHER PARTICIPANTS" and
# "ANNOUNCEMENT FROM THE FSA" (Finanstilsynet), and the phrase itself names a
# non-issuer originator, so a fourth one appearing tomorrow is excluded for the
# same reason without this file needing an edit. A fixed list is what let the
# FSA announcements through as regulatory=True.
THIRD_PARTY_CATEGORY_PREFIX = "ANNOUNCEMENT FROM"

# Title words that mean the message is ABOUT a report rather than being one.
# Checked before the positive patterns, because "Invitation to Q2 2026
# presentation and Q&A session" matches several of them.
NOT_A_REPORT_TITLE = (
    "invitation", "invites", "invitasjon", "innkalling", "summons",
    "presentation", "presentasjon", "webcast", "conference call", "q&a",
    "notice of", "financial calendar", "finansiell kalender", "kalender",
    "agenda", "minutes from", "protokoll",
)

# Positive title evidence for a periodic report, Norwegian and English.
# Deliberately conservative: a missed report degrades to a plain regulated
# disclosure, whereas a false one drives a "new report since last review"
# alert at layer 2, so the asymmetry is priced in favour of missing.
REPORT_TITLE_RE = re.compile(
    r"kvartalsrapport|del[åa]rsrapport|halv[åa]rsrapport|[åa]rsrapport|"
    r"bokslutskommunik|resultatrapport|"
    r"(?:interim|quarterly|half[\s-]?year|annual|year[\s-]?end)\s+"
    r"(?:financial\s+)?(?:report|statements?|results)|"
    r"trading update|"
    # Both quarter notations are in live use: "Q2 26" and "2Q26".
    r"\b(?:q[1-4]|[1-4]q\d{0,4}|first|second|third|fourth|1st|2nd|3rd|4th)\b"
    r"[^,;]{0,40}?\b(?:report|statements?|results|rapport)\b",
    re.I)


def _title_says_report(title):
    """Does the title itself claim to be a periodic report?

    Only consulted for CATCH_ALL_CATEGORY - see the constant. Applying it to
    every category would let a NON-REGULATORY press release announcing that a
    report has been published count as the report.
    """
    text = (title or "").lower()
    if not text:
        return False
    if any(word in text for word in NOT_A_REPORT_TITLE):
        return False
    return bool(REPORT_TITLE_RE.search(text))


def _published_iso(raw):
    """Normalise publishedTime to YYYY-MM-DDTHH:MM:SS, mfn_news.flatten()'s shape.

    Two shapes exist. /list and /message both return the full
    "2026-08-31T20:30:00.000Z" today; a minute-precision "2026-09-05T07:00" has
    also been seen and is what the API documents. The old test for the short
    form - `not raw.endswith(":00")` - was a suffix test standing in for a
    length test, and it is false exactly on the on-the-hour stamps: 07:00 and
    08:00, the two busiest Oslo publication slots, therefore stayed 16
    characters while 07:05 became 19. Test the length.

    The trailing Z means UTC. It is truncated, not converted: there is no zone
    database guaranteed present on every platform this runs on (zoneinfo needs
    tzdata on Windows), and inventing a fixed +1/+2 offset would be a guess
    about DST. So this field's wall clock is UTC where mfn_news's is local -
    timestamps order correctly within a feed, and to the day, but not to the
    minute across the two feeds.
    """
    raw = (raw or "").strip()
    if not raw or "T" not in raw:
        return raw[:19]
    if len(raw) == 16:  # "YYYY-MM-DDTHH:MM" - minute precision, pad the seconds
        return raw + ":00"
    return raw[:19]


def flatten(msg):
    """Normalise a message dict to mfn_news.flatten()'s shape.

    KEY PARITY WITH mfn_news.flatten(). Every key that function emits is
    emitted here, so an item from either feed can go through the same caller
    (verify_filing.py hard-subscripts item["text"] and item["preamble"];
    ttm_engine.py and corporate_actions.py read item["lang"] and item["tags"]).
    Four of them are structurally empty on this feed, and that is a property of
    NewsWeb, not an omission:

      preamble    - NewsWeb has no preamble field. Always "".
      lang        - NewsWeb exposes no language field. Always None. Guessing it
                    from the title would be a guess; a Norwegian title is not a
                    language tag.
      via_cision  - always False. NewsWeb is a primary OAM, not a Cision mirror.
      text        - the message BODY, which /list does not return. It is "" for
                    an item that came from releases_between()/releases_for_issuer()
                    and populated for one that came from message(), because
                    /message returns `body`. So flatten(message(mid))["text"] is
                    the full release; flattening a list item and expecting text
                    is asking /list for a field it never sends.

    Oslo-native aliases are emitted alongside the mfn names, not instead of
    them: `issuer_sign` next to `slug` (both the issuer ticker), `categories`
    next to `tags` (both the category_en strings). Renaming them was what made
    the previous output unconsumable.

    REGULATORY/REPORT MAPPING (documented here because it is a judgement, and
    because the obvious mapping is wrong).

    `oamMandatory` looks like the MAR flag and is NOT usable as one. Measured
    2026-09-05 over a 36-day window: 601 messages, `oamMandatory == 0` on every
    single one, including 167 half-year reports and 23 INSIDE INFORMATION
    releases. Reading `regulatory` off that field yields False for the entire
    feed - a flag that never fires, which is worse than no flag because it
    reads as a checked answer. The signal lives in `category[].category_en`.

    So `regulatory` is derived by exclusion, not inclusion. NewsWeb is the
    Norwegian OAM: what it carries is regulated disclosure unless the category
    says otherwise. Two kinds of category say otherwise - the issuer's own
    explicitly non-regulatory press releases, and anything announced by someone
    who is not the issuer (the exchange, other market participants, the FSA),
    which is not issuer disclosure at all. The second kind is matched on the
    "ANNOUNCEMENT FROM" prefix rather than enumerated; the enumeration is what
    let "ANNOUNCEMENT FROM THE FSA" through as regulated. Excluding a
    known-small deny-list is the safer direction here: a new regulated category
    appearing in the vocabulary is then treated as regulated, whereas an
    allow-list would silently drop it.

    `is_report` is the category when the category says so, and the title when
    the category cannot. Two categories name a periodic report; a third, the
    Transparency-Directive catch-all, names nothing at all and carries real
    quarterly reports mixed in with bond tap issues, so for that one category
    the title is read as well. See REPORT_CATEGORIES / CATCH_ALL_CATEGORY.
    Categories are matched whole, never on a bare "report" substring: a future
    "REPORT FROM THE NOMINATION COMMITTEE" would make the substring wrong, and
    a false report flag drives the layer-2 "new report since last review" alert.
    """
    msg = msg or {}
    categories = msg.get("category") or []
    category_names = sorted({c.get("category_en") or "" for c in categories if c.get("category_en")})
    upper = {c.strip().upper() for c in category_names if c.strip()}

    title = msg.get("title")
    is_report = bool(upper & REPORT_CATEGORIES) or (
        CATCH_ALL_CATEGORY in upper and _title_says_report(title))

    excluded = {c for c in upper
                if c in NON_REGULATORY_CATEGORIES or c.startswith(THIRD_PARTY_CATEGORY_PREFIX)}
    # An uncategorised message cannot be asserted to be regulated disclosure.
    regulatory = bool(upper) and not excluded

    # Kept and surfaced, never relied on: if Oslo Børs starts populating it,
    # a caller can see that without this mapping having to change first.
    oam_mandatory = int(msg.get("oamMandatory") or 0)

    message_id = msg.get("messageId") or msg.get("id") or ""
    url = "https://newsweb.oslobors.no/message/%s" % message_id if message_id else ""

    return {
        "date": _published_iso(msg.get("publishedTime")),
        "company": msg.get("issuerName"),
        # mfn's `slug` and Oslo's issuerSign are both "the issuer key in this
        # feed's own namespace"; they are NOT interchangeable across feeds.
        "slug": msg.get("issuerSign"),
        "issuer_sign": msg.get("issuerSign"),
        "title": title,
        "preamble": "",
        "text": msg.get("body") or "",
        "lang": None,
        "tags": list(category_names),
        "categories": list(category_names),
        "regulatory": regulatory,
        # Surfaced but never the basis for `regulatory` above - measured 0 on
        # every message in a 601-message window, so a caller must not read it
        # as MAR status. Kept so a future change in Oslo Børs's behaviour is
        # visible without this module having to change first.
        "oam_mandatory": bool(oam_mandatory),
        "is_report": is_report,
        "via_cision": False,
        "url": url,
        "message_id": message_id,
        # /list sends only the COUNT of attachments, never the attachments. An
        # empty list below therefore means "not fetched" on a list item and
        # "none" only when num_attachments is 0; fetch the message to resolve it.
        "num_attachments": int(msg.get("numbAttachments") or 0),
        "attachments": [
            # Measured live: /message returns {"id", "name"} per attachment -
            # no URL and no MIME type. The previous mapping read attachmentURL /
            # attachmentName / attachmentMimeType, keys this payload does not
            # have, so every real attachment flattened to three Nones. Those
            # keys are still honoured in case the API grows them, but nothing
            # is fabricated: there is no documented public URL for an
            # attachment id, so `url` stays None and the file is reached
            # through the message page in `url` above.
            {"title": a.get("name") or a.get("attachmentName"),
             "url": a.get("attachmentURL"),
             "type": a.get("attachmentMimeType"),
             "attachment_id": a.get("id")}
            for a in (msg.get("attachments") or [])
        ],
    }


# --- Selftest --------------------------------------------------------------

# mfn_news.flatten()'s key set, which this module's flatten() must cover. The
# selftest reads it from mfn_news.py when that file is reachable and reports
# NOT CHECKED when it is not, rather than passing on a hardcoded copy that
# could drift away from the real thing without anyone noticing.
def _mfn_flatten_keys():
    """Return mfn_news.flatten()'s output keys, or None if unavailable.

    Import by file path, never `import mfn_news`: scripts/ is not a package and
    is appended to sys.path, so a bare import is not guaranteed to resolve. No
    network happens at import time in that module.
    """
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mfn_news.py")
    if not os.path.exists(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location("_mfn_news_for_selftest", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sample = mod.flatten({"content": {"publish_date": "2026-09-05T07:00:00",
                                          "title": "t", "preamble": "p", "text": "b",
                                          "attachments": []},
                              "properties": {"tags": [], "lang": "en"},
                              "author": {"name": "n", "slug": "s"},
                              "url": "https://mfn.se/a/s/x"})
        return set(sample)
    except (Exception, SystemExit):
        # SystemExit does not inherit from Exception; a loader that hard-exits
        # must degrade to "not checked" here, not kill the selftest.
        return None


def _selftest():
    """Offline tests. No network call, and no write to the shared cache."""
    ok = 0
    not_checked = 0

    # The whole selftest runs against a THROWAWAY cache directory. It patches
    # fetch() with fabricated messages, and fetch_json_cached() - which is not
    # patched - would otherwise write those fabrications into the shared
    # production cache under real query keys, so a real query for the same
    # dates would be served invented Telenor disclosures for the next 15
    # minutes. Redirect CACHE, restore it, and delete the directory.
    original_cache = CACHE
    original_fetch = globals()["fetch"]
    # Snapshot the shared cache so test 11 can prove THIS run added nothing to
    # it. A bare "no list-* files exist" check would fail on entries a real
    # query left behind, or on the ones the pre-fix selftest wrote.
    try:
        cache_before = set(os.listdir(original_cache))
    except OSError:
        cache_before = set()
    temp_cache = tempfile.mkdtemp(prefix="oslo-bors-selftest-")
    globals()["CACHE"] = temp_cache
    try:
        # Test 1: Overflow flag is surfaced from API response.
        # Fake response with overflow=True; caller must see it, not have it swallowed.
        fake_data_with_overflow = {
            "data": {
                "messages": [
                    {"messageId": "1", "title": "Release 1", "publishedTime": "2026-09-05T10:00",
                     "issuerSign": "TEL", "issuerName": "Telenor ASA", "oamMandatory": 1,
                     "category": [{"category_en": "INSIDE INFORMATION"}]}
                ],
                "overflow": True
            }
        }

        def fake_fetch_overflow(endpoint, **params):
            if endpoint == "/list":
                return fake_data_with_overflow
            return original_fetch(endpoint, **params)

        globals()["fetch"] = fake_fetch_overflow
        try:
            msgs, overflow = releases_between("2026-09-05", "2026-09-05")
            assert overflow is True, "Test 1: overflow flag not surfaced"
            assert len(msgs) == 1, "Test 1: wrong message count"
            print("PASS: Test 1 - overflow flag surfaced correctly")
            ok += 1
        finally:
            globals()["fetch"] = original_fetch

        # Test 2: Issuer filtering works on both issuerSign and issuerName, and
        # carries the window's overflow flag out with the filtered subset.
        # Use different dates to avoid cache collisions with test 1.
        fake_data_multiple = {
            "data": {
                "messages": [
                    {"messageId": "1", "title": "TEL Release", "publishedTime": "2026-09-04T10:00",
                     "issuerSign": "TEL", "issuerName": "Telenor ASA", "oamMandatory": 1,
                     "category": [{"category_en": "INSIDE INFORMATION"}]},
                    {"messageId": "2", "title": "NAS Release", "publishedTime": "2026-09-04T11:00",
                     "issuerSign": "NAS", "issuerName": "Nasjonalbanken", "oamMandatory": 0,
                     "category": []},
                ],
                "overflow": True
            }
        }

        def fake_fetch_multiple(endpoint, **params):
            if endpoint == "/list":
                return fake_data_multiple
            return original_fetch(endpoint, **params)

        globals()["fetch"] = fake_fetch_multiple
        try:
            # Filter by issuerSign (case-insensitive).
            filtered, overflow = releases_for_issuer("tel", "2026-09-04", "2026-09-04")
            assert len(filtered) == 1, "Test 2a: issuerSign filter failed"
            assert filtered[0]["issuerSign"] == "TEL", "Test 2a: wrong issuer"
            print("PASS: Test 2a - issuerSign filtering (case-insensitive)")
            ok += 1

            # Filter by issuerName (exact).
            filtered, overflow = releases_for_issuer("Nasjonalbanken", "2026-09-04", "2026-09-04")
            assert len(filtered) == 1, "Test 2b: issuerName filter failed"
            assert filtered[0]["issuerSign"] == "NAS", "Test 2b: wrong issuer"
            print("PASS: Test 2b - issuerName filtering (exact)")
            ok += 1

            # The truncation flag must survive the local filter: an issuer with
            # no hit in a truncated window has NOT been shown to be silent.
            filtered, overflow = releases_for_issuer("EQNR", "2026-09-04", "2026-09-04")
            assert filtered == [], "Test 2c: unexpected match"
            assert overflow is True, "Test 2c: overflow lost in releases_for_issuer()"
            print("PASS: Test 2c - overflow survives issuer filtering")
            ok += 1
        finally:
            globals()["fetch"] = original_fetch

        # Test 3: flatten()'s regulatory/report mapping.
        #
        # These fixtures use the REAL category vocabulary, harvested from a live
        # 601-message window on 2026-09-05. An earlier version of this selftest
        # used invented categories ("RNS", "News") and drove `regulatory` off
        # oamMandatory - a field that measured 0 on all 601 messages, so the flag
        # it was asserting could never have fired in production.
        def _msg(mid, category, oam=0, title="t"):
            return {"messageId": mid, "title": title, "publishedTime": "2026-09-05T14:30",
                    "issuerSign": "TEL", "issuerName": "Telenor ASA",
                    "oamMandatory": oam, "category": [{"category_en": category}]}

        flattened = flatten(_msg("100", "INSIDE INFORMATION"))
        assert flattened["regulatory"] is True, "INSIDE INFORMATION is regulated disclosure"
        assert flattened["is_report"] is False, "INSIDE INFORMATION is not a report"
        assert flattened["url"] == "https://newsweb.oslobors.no/message/100", flattened["url"]
        print("PASS: Test 3a - regulated category")
        ok += 1

        flattened = flatten(_msg("101", "HALF YEAR FINANCIAL REPORT"))
        assert flattened["is_report"] is True, "HALF YEAR FINANCIAL REPORT is a report"
        assert flattened["regulatory"] is True, "a periodic report is regulated disclosure"
        print("PASS: Test 3b - report category")
        ok += 1

        # The deny-list: NewsWeb carries non-regulated material too, and it must
        # not be dressed as regulated just because it arrived on the OAM.
        flattened = flatten(_msg("102", "NON-REGULATORY PRESS RELEASES"))
        assert flattened["regulatory"] is False, "explicitly non-regulatory category"
        assert flattened["is_report"] is False, flattened
        print("PASS: Test 3c - non-regulatory category excluded")
        ok += 1

        # oamMandatory must NOT be able to force the flag on its own.
        flattened = flatten(_msg("103", "NON-REGULATORY PRESS RELEASES", oam=1))
        assert flattened["regulatory"] is False, "oamMandatory must not override the category"
        assert flattened["oam_mandatory"] is True, "the raw field is still surfaced"
        print("PASS: Test 3d - oamMandatory does not override the category")
        ok += 1

        # An uncategorised message cannot be asserted to be regulated.
        flattened = flatten({"messageId": "104", "title": "t",
                             "publishedTime": "2026-09-05T14:30", "category": []})
        assert flattened["regulatory"] is False, "no category means no assertion"
        print("PASS: Test 3e - uncategorised message is not asserted regulated")
        ok += 1

        # The non-ASCII deny-list entry. "Ø" is U+00D8 and the category string
        # in the source file must match the API's byte-for-byte after .upper().
        flattened = flatten(_msg("105", "ANNOUNCEMENT FROM OSLO BØRS"))
        assert flattened["regulatory"] is False, "exchange announcement is not issuer disclosure"
        print("PASS: Test 3f - ANNOUNCEMENT FROM OSLO BØRS excluded (non-ASCII)")
        ok += 1

        # Found live as regulatory=True before the prefix rule: a Finanstilsynet
        # announcement is about an issuer, not by one.
        flattened = flatten(_msg("106", "ANNOUNCEMENT FROM THE FSA"))
        assert flattened["regulatory"] is False, "FSA announcement is not issuer disclosure"
        flattened = flatten(_msg("107", "ANNOUNCEMENT FROM OTHER PARTICIPANTS"))
        assert flattened["regulatory"] is False, "third-party announcement is not issuer disclosure"
        print("PASS: Test 3g - every ANNOUNCEMENT FROM ... category excluded")
        ok += 1

        # U+2019 RIGHT SINGLE QUOTATION MARK, not an ASCII apostrophe. 47 of
        # these in the sample window; a mis-typed apostrophe here would not
        # break anything today, but would the moment the category is matched
        # against instead of merely passed through.
        managers = "MANAGERS’ TRANSACTION"
        flattened = flatten(_msg("108", managers))
        assert flattened["regulatory"] is True, "a PDMR notification is regulated disclosure"
        assert flattened["is_report"] is False, "a PDMR notification is not a report"
        assert flattened["tags"] == [managers], flattened["tags"]
        print("PASS: Test 3h - MANAGERS’ TRANSACTION (U+2019) is regulated, not a report")
        ok += 1

        # Test 4: the catch-all category, where the title has to be read.
        # Real titles from the sample window.
        for title in ("Kvartalsrapport for andre kvartal 2026 med styrets fullstendighetserklæring",
                      "Delårsrapport 1. kvartal 2026",
                      "Q1 2026 trading update",
                      "Q2 26 Interim report",
                      "Panther Bidco AS - Interim report for Q2 2026",
                      "NAVIOS SOUTH AMERICAN LOGISTICS INC. SECOND QUARTER 2026 FINANCIAL RESULTS"):
            flattened = flatten(_msg("200", CATCH_ALL_CATEGORY, title=title))
            assert flattened["is_report"] is True, "missed a real quarterly report: %s" % title
            assert flattened["regulatory"] is True, title
        print("PASS: Test 4a - quarterly reports under the catch-all category are found")
        ok += 1

        # ...and the near misses in the same category that must NOT be reports.
        for title in ("Invitation to Q2 2026 presentation and Q&A session",
                      "FRO - Q2 2026 Presentation",
                      "Ventura Offshore Holding Ltd.: Presentation of Second Quarter 2026 results",
                      "Financial calendar",
                      "Innkalling til ekstraordinær generalforsamling: 14. September 2026",
                      "Tap issue of senior unsecured bonds in NOK",
                      "Key information relating to the dividend to be paid for the second quarter, 2026"):
            flattened = flatten(_msg("201", CATCH_ALL_CATEGORY, title=title))
            assert flattened["is_report"] is False, "false report flag on: %s" % title
        print("PASS: Test 4b - invitations, presentations and bond notices are not reports")
        ok += 1

        # The title signal is scoped to the catch-all: elsewhere the category is
        # authoritative, so a press release ABOUT a report is not the report.
        flattened = flatten(_msg("202", "NON-REGULATORY PRESS RELEASES",
                                 title="Q2-2026 Interim Financial Statements"))
        assert flattened["is_report"] is False, "title signal must not leak past the catch-all"
        print("PASS: Test 4c - title signal is scoped to the catch-all category")
        ok += 1

        # Test 5: publishedTime normalisation. On-the-hour stamps are the trap:
        # 07:00 and 08:00 are the busiest Oslo slots and endswith(":00") is true
        # for both, so the old suffix test left exactly those 16 characters long.
        assert flatten(_msg("300", "INSIDE INFORMATION"))["date"] == "2026-09-05T14:30:00"
        on_the_hour = {"messageId": "301", "publishedTime": "2026-09-05T07:00", "category": []}
        assert flatten(on_the_hour)["date"] == "2026-09-05T07:00:00", flatten(on_the_hour)["date"]
        full_utc = {"messageId": "302", "publishedTime": "2026-08-31T20:30:00.000Z", "category": []}
        assert flatten(full_utc)["date"] == "2026-08-31T20:30:00", flatten(full_utc)["date"]
        missing = {"messageId": "303", "category": []}
        assert flatten(missing)["date"] == "", flatten(missing)["date"]
        print("PASS: Test 5 - publishedTime padded by length, not by suffix")
        ok += 1

        # Test 6: key parity with mfn_news.flatten(), the whole point of this
        # module's output shape.
        mfn_keys = _mfn_flatten_keys()
        if mfn_keys is None:
            print("NOT CHECKED: Test 6 - mfn_news.py not importable, key parity unverified")
            not_checked += 1
        else:
            produced = set(flatten(_msg("400", "INSIDE INFORMATION")))
            missing_keys = mfn_keys - produced
            assert not missing_keys, "flatten() is missing mfn keys: %s" % sorted(missing_keys)
            print("PASS: Test 6 - every mfn_news.flatten() key is present")
            ok += 1

        # verify_filing.py subscripts these directly; a KeyError there is a
        # crash on the first Oslo item, not a degradation.
        item = flatten(_msg("401", "HALF YEAR FINANCIAL REPORT"))
        assert item["text"] == "", "a /list item carries no body"
        assert item["preamble"] == "", "NewsWeb has no preamble"
        assert item["lang"] is None and item["via_cision"] is False
        with_body = flatten({"messageId": "402", "title": "t", "category": [],
                             "body": "Full release text.", "publishedTime": "2026-09-05T07:00",
                             "attachments": [{"id": 1, "name": "report.pdf"}]})
        assert with_body["text"] == "Full release text.", "message() body must land in text"
        assert with_body["attachments"][0]["title"] == "report.pdf", with_body["attachments"]
        print("PASS: Test 6b - text/preamble/lang present; /message body maps to text")
        ok += 1

        # Test 7: date normalisation and validation, in the library rather than
        # only in main(). A datetime is a date subclass and used to reach the
        # wire as "2026-09-05T14:30:00" with a fresh cache key every call.
        assert _iso_date(datetime.datetime(2026, 9, 5, 14, 30), "x") == "2026-09-05"
        assert _iso_date(datetime.date(2026, 9, 5), "x") == "2026-09-05"
        assert _iso_date("2026-9-5", "x") == "2026-09-05"
        for bad in (None, "", "2026-13-45", "yesterday", 20260905):
            try:
                _iso_date(bad, "x")
            except SystemExit:
                pass
            else:
                raise AssertionError("Test 7: %r was accepted" % (bad,))
        try:
            _date_window("2026-09-05", "2026-09-01")
        except SystemExit:
            pass
        else:
            raise AssertionError("Test 7: inverted range was accepted")
        print("PASS: Test 7 - dates normalised and validated before any request")
        ok += 1

        # Test 8: a malformed 200 is unavailability, not absence.
        for broken in ({}, {"data": None}, {"data": {}}, {"data": {"messages": None}}, []):
            def fake_fetch_broken(endpoint, _payload=broken, **params):
                return _payload

            globals()["fetch"] = fake_fetch_broken
            try:
                releases_between("2026-09-02", "2026-09-02")
            except SystemExit:
                pass
            else:
                raise AssertionError("Test 8: malformed envelope %r read as empty" % (broken,))
            finally:
                globals()["fetch"] = original_fetch
        print("PASS: Test 8a - a malformed /list body raises instead of reporting zero news")
        ok += 1

        # ...and the inverse: a real empty window (a Sunday) is a real answer.
        def fake_fetch_empty(endpoint, **params):
            return {"data": {"messages": [], "overflow": False}}

        globals()["fetch"] = fake_fetch_empty
        try:
            msgs, overflow = releases_between("2026-09-06", "2026-09-06")
            assert msgs == [] and overflow is False, "Test 8b: empty window mishandled"
            print("PASS: Test 8b - a legitimately empty window is an answer, not a failure")
            ok += 1
        finally:
            globals()["fetch"] = original_fetch

        # Test 9: failures and negative answers are not cached.
        calls = {"n": 0}

        def fake_fetch_failing(endpoint, **params):
            calls["n"] += 1
            raise SystemExit("DATA NOT AVAILABLE: simulated outage")

        globals()["fetch"] = fake_fetch_failing
        try:
            for _ in range(2):
                try:
                    message("999001")
                except SystemExit:
                    pass
            assert calls["n"] == 2, "Test 9a: a failure was cached and not retried"
            print("PASS: Test 9a - a failed message fetch is retried, never cached")
            ok += 1
        finally:
            globals()["fetch"] = original_fetch

        calls["n"] = 0

        def fake_fetch_absent(endpoint, **params):
            calls["n"] += 1
            return {"data": {"message": None}}

        globals()["fetch"] = fake_fetch_absent
        try:
            assert message("999002") is None, "Test 9b: absence must read as None"
            assert message("999002") is None
            assert calls["n"] == 2, "Test 9b: a negative answer was frozen for a year"
            print("PASS: Test 9b - 'no such message' is not cached for 365 days")
            ok += 1
        finally:
            globals()["fetch"] = original_fetch

        def fake_fetch_no_envelope(endpoint, **params):
            return {"data": {}}

        globals()["fetch"] = fake_fetch_no_envelope
        try:
            try:
                message("999003")
            except SystemExit:
                print("PASS: Test 9c - a missing /message envelope raises instead of caching {}")
                ok += 1
            else:
                raise AssertionError("Test 9c: unrecognised envelope accepted")
        finally:
            globals()["fetch"] = original_fetch

        # Test 10: TTL splits on whether the window can still change.
        today_utc = datetime.datetime.now(datetime.timezone.utc).date()
        assert _list_ttl((today_utc - datetime.timedelta(days=1)).isoformat()) == CACHE_TTL_CLOSED
        assert _list_ttl(today_utc.isoformat()) == CACHE_TTL_RECENT
        assert _list_ttl((today_utc + datetime.timedelta(days=1)).isoformat()) == CACHE_TTL_RECENT
        print("PASS: Test 10 - closed windows get the long TTL, open windows the short one")
        ok += 1

        # Test 11: the selftest's own isolation. Nothing may have been written
        # outside the throwaway directory.
        assert _cache_path("x").startswith(temp_cache), "selftest is writing outside its temp cache"
        try:
            cache_after = set(os.listdir(original_cache))
        except OSError:
            cache_after = set()
        assert cache_after == cache_before, \
            "selftest wrote fabricated entries into the shared cache: %s" % sorted(
                cache_after - cache_before)
        assert os.listdir(temp_cache), "selftest cached nothing at all - isolation not exercised"
        print("PASS: Test 11 - the shared production cache was not touched")
        ok += 1
    finally:
        globals()["fetch"] = original_fetch
        globals()["CACHE"] = original_cache
        shutil.rmtree(temp_cache, ignore_errors=True)

    print("\noffline selftests: %d assertion groups ok, %d not checked "
          "(no network calls made, shared cache untouched)" % (ok, not_checked))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="from_date", metavar="YYYY-MM-DD",
                    help="start date (inclusive)")
    ap.add_argument("--to", dest="to_date", metavar="YYYY-MM-DD",
                    help="end date (inclusive)")
    ap.add_argument("--issuer", metavar="SIGN_OR_NAME",
                    help="filter by issuerSign (case-insensitive) or issuerName (exact)")
    ap.add_argument("--limit", type=int, default=50,
                    help="limit output to N messages")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="output JSON")
    ap.add_argument("--selftest", action="store_true",
                    help="run offline tests (no network calls)")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    if not args.from_date or not args.to_date:
        ap.error("--from and --to are required (YYYY-MM-DD format)")

    # The library validates too (it is importable), but a CLI typo deserves the
    # argparse usage message and exit 2 rather than a DATA NOT AVAILABLE line,
    # which means something else in this codebase.
    try:
        from_date, to_date = _date_window(args.from_date, args.to_date)
    except SystemExit as e:
        ap.error(str(e).replace("DATA NOT AVAILABLE: ", ""))

    if args.issuer:
        messages, overflow = releases_for_issuer(args.issuer, from_date, to_date)
    else:
        messages, overflow = releases_between(from_date, to_date)

    # Surfaced on every path, --json included, and before anything else so it
    # is attached to the output even when stdout is piped away.
    if overflow:
        print("WARNING: Result window was truncated by the API (overflow=true) - "
              "this listing is incomplete", file=sys.stderr)

    items = [flatten(m) for m in messages]
    shown = items[:args.limit]

    # An empty result means "nothing was published" ONLY if the window was
    # complete. If the API truncated it, the issuer's message may be precisely
    # what was dropped, so absence has not been established and the exit code
    # must say so.
    absence_established = bool(shown) or not overflow

    if args.as_json:
        print(json.dumps({"count": len(shown),
                          "matched": len(items),
                          "from_date": from_date,
                          "to_date": to_date,
                          "issuer_filter": args.issuer,
                          "overflow": overflow,
                          "limit_truncated": len(items) > len(shown),
                          "complete": absence_established and not overflow,
                          "messages": shown},
                         indent=2, ensure_ascii=False))
        return 0 if absence_established else 1

    if not shown:
        if not absence_established:
            print("DATA NOT AVAILABLE: the window %s to %s was truncated by the API, so "
                  "'no messages' cannot be established" % (from_date, to_date))
            return 1
        # A quiet window is a fact about the market, not a failure of the tool.
        print("Oslo Børs NewsWeb  |  %s to %s  |  no messages published%s"
              % (from_date, to_date, (" by %s" % args.issuer) if args.issuer else ""))
        return 0

    print("Oslo Børs NewsWeb  |  %s to %s  |  %d messages%s" %
          (from_date, to_date, len(shown),
           "  (window truncated by the API - incomplete)" if overflow else ""))
    print()
    for i in shown:
        marks = []
        if i["regulatory"]:
            marks.append("REGULATORY")
        if i["is_report"]:
            marks.append("REPORT")
        flag = ("  [" + "/".join(marks) + "]") if marks else ""
        print("%s  %s%s" % (i["date"], i["title"], flag))
        print("  %s" % i["url"])
        if i["issuer_sign"]:
            print("  Issuer: %s (%s)" % (i["company"], i["issuer_sign"]))
        for a in i["attachments"]:
            if a.get("url"):
                print("  Attachment: %s  ->  %s" % (a.get("title") or "file", a["url"]))
            elif a.get("title"):
                # No public URL is exposed for an attachment id - the file is on
                # the message page above. Saying so beats printing nothing.
                print("  Attachment: %s  (on the message page)" % a["title"])
        if i["num_attachments"] and not i["attachments"]:
            print("  Attachments: %d (not listed by /list - fetch the message)"
                  % i["num_attachments"])
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
