#!/usr/bin/env python3
"""Locate a listed company's own Investor Relations site, and read it.

WHY THIS EXISTS

The rest of this toolkit reaches company reports through MFN and Cision. That
is backwards. MFN and Cision are *distribution channels* - they carry the press
release and an attachment, for the issuers who happen to use them, for as long
as they choose to. The company's own IR site is the *primary source*: it is
where the issuer itself publishes the annual report, the interim report, the
presentation deck, the financial calendar, the stated financial targets, the
dividend policy and the buyback authorisation. Cite the issuer, cross-check the
channel - not the other way round.

WHY IT DOES NOT GUESS URLS

The obvious way to find an IR site is to try /investor-relations/, then
/the-share/, then /for-investors/. A previous session spent three 404s on KebNi
before hitting https://www.kebni.com/for-investors/. Guessing is not just slow,
it is unsafe: a guessed URL that happens to 200 (a marketing page, a redirect
to the root) gets reported as "found" and quietly poisons the citation.

So this script never constructs a URL and calls it discovered. It gets a
POINTER from a data source, follows it, and only reports what actually
answered HTTP 200 - the URL *after* redirects, which is what fixes KebNi in one
request (Avanza still points at the old /about-us-investor/, which redirects to
/for-investors/).

DISCOVERY ROUTES, IN THE ORDER TRIED  (all free, all keyless)

  1. AVANZA.  POST https://www.avanza.se/_api/search/filtered-search
     {"query": "<name>", "searchFilter": {"types": ["STOCK"]}}
     -> orderBookId, then
     GET https://www.avanza.se/_api/market-guide/stock/{id}/details
     -> company.homepage, which is an IR-targeted URL, not a corporate root
     (Sandvik -> https://www.home.sandvik/en/investors/).

     A previous session recorded Avanza's resolver as broken. It is not - the
     *global* search endpoint 404s, but filtered-search answers POST (it
     returns 405 to GET, which is what makes it look dead). That matters:
     sitemap1.xml, the documented workaround, holds 6,294 stale id/slug pairs
     and contains neither KebNi nor any other post-2015 listing, and the
     OMXS30 constituents endpoint covers thirty names. filtered-search found
     all five test companies including KebNi.

     Avanza is a broker redistributing licensed market data. Everything from it
     is a CROSS-CHECK, never a primary citation. The homepage field is used
     only as a pointer to the issuer's own site; the numbers it also returns
     (shares, owners, calendar, CEO/chairman) are printed under an explicit
     cross-check heading and must be re-sourced before use.

  2. MFN RELEASE BODIES.  https://mfn.se/a/{slug}.json - the issuer's own
     boilerplate ("For further information visit www.kebni.com") names the
     domain. Works only for MFN issuers: KebNi 78 mentions, Evolution 116,
     but Sandvik / Volvo / Atlas Copco are Cision issuers and return zero
     items. A genuine fallback, not a substitute.

  3. GLEIF - INVESTIGATED AND REJECTED.  api.gleif.org resolves the legal
     entity fine (legal name, LEI, registered address, jurisdiction) but the
     LEI record schema carries NO website field, and neither does VIES, which
     only validates a VAT number against a name and address. ESMA FIRDS is
     instrument reference data keyed on ISIN - issuer LEI, no domain. None of
     the three can produce a URL. They are useful for confirming *which* legal
     entity you are looking at, and this script prints the LEI when it can,
     but they cannot start the search.

WHAT IT THEN READS OFF THE SITE

A bounded, polite crawl - the IR landing page plus a handful of scored
sub-pages, robots.txt honoured, one request per second per host by default -
classifies links into: the reports archive, the latest annual report PDF, the
latest interim report PDF, the latest investor presentation, the financial
calendar, the share/ownership page, and the financial-targets page. It also
records WHERE it saw financial targets, a dividend policy and a buyback
authorisation, with the sentence it saw, so the claim can be checked.

HONEST LIMITS - READ THESE

  * IR sites are hand-built marketing sites. Volvo Group's reports page carries
    its PDFs in JavaScript data rather than <a href>, so anchor parsing alone
    returns nothing; this script also raw-scans the document for PDF URLs,
    which recovers them but loses the link text, so period labels then come
    from the filename and are weaker.
  * A corporate network can make a company unreachable. Evolution AB resolves
    to 146.112.61.106 on this machine - a Cisco Umbrella block page - and the
    TLS chain is issued by "Cisco Umbrella Root CA", i.e. the gambling domain
    is filtered upstream. That is reported as BLOCKED, distinctly from "down",
    and the unverified pointer is shown but never promoted to a finding.
  * Some servers negotiate only RSA key-exchange ciphers that Python's default
    context refuses. The fetcher retries once with a widened cipher list, still
    verifying certificates. It never disables verification.
  * Anything not found is DATA NOT AVAILABLE. Nothing here is inferred.

Usage:
    python ir_discovery.py "Sandvik"
    python ir_discovery.py "AB Volvo"                  # not Volvo Car
    python ir_discovery.py "KebNi" --reports
    python ir_discovery.py "Atlas Copco" --reports --download .\\reports
    python ir_discovery.py "Sandvik" --json
    python ir_discovery.py --url https://www.kebni.com/for-investors/ --reports

Free, no API key.
"""
import argparse
import html
import json
import os
import re
import socket
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

AVANZA_SEARCH = "https://www.avanza.se/_api/search/filtered-search"
AVANZA_DETAILS = "https://www.avanza.se/_api/market-guide/stock/%s/details"
MFN_FEED = "https://mfn.se/a/%s.json?limit=25"
# Percent-encoded brackets, so this is built by concatenation rather than %-format.
GLEIF_LEI = ("https://api.gleif.org/api/v1/lei-records"
             "?filter%5Bentity.legalName%5D={name}&page%5Bsize%5D=5")

# The default Python-urllib User-Agent is blocklisted by several of these hosts
# (Sandvik and Atlas Copco return 403 for robots.txt with it, which silently
# turns urllib.robotparser into "disallow everything"). Any browser-shaped UA
# works, and this one names the tool so an admin can see who called.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
      "investment-analyst-skill/ir_discovery")

CACHE = os.path.join(tempfile.gettempdir(), "ir-discovery-cache")
HTML_TTL = 6 * 3600
JSON_TTL = 6 * 3600
ROBOTS_TTL = 24 * 3600

# Cisco Umbrella serves its block page from this /24. If a company's IR host
# resolves there, the company is filtered by the local network, not offline.
UMBRELLA_BLOCK_PREFIX = "146.112.61."

NA = "DATA NOT AVAILABLE"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


# --------------------------------------------------------------------------
# HTTP: cached, rate-limited, robots-aware, with one TLS retry
# --------------------------------------------------------------------------

_last_hit = {}          # host -> monotonic time of last request
_robots = {}            # host -> RobotFileParser or None
_notes = []             # human-readable trace of what happened


class Fetch(Exception):
    def __init__(self, why, url):
        super().__init__("%s: %s" % (url, why))
        self.why = why
        self.url = url


def note(msg):
    _notes.append(msg)


def _strict_ctx():
    return ssl.create_default_context()


def _wide_ctx():
    """Same trust store, wider cipher list.

    Some IR hosts (and every TLS-intercepting corporate proxy seen so far)
    negotiate only plain-RSA key exchange, which Python's default context
    dropped. SECLEVEL=1 re-admits those suites. Certificate verification stays
    on - a widened cipher list is a compatibility concession, not a trust one.
    """
    ctx = ssl.create_default_context()
    try:
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    except ssl.SSLError:
        pass
    return ctx


def _cache_path(kind, key):
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)[:110]
    digest = 0
    for ch in key:                      # stable across runs, unlike hash()
        digest = (digest * 131 + ord(ch)) & 0xFFFFFFFF
    return os.path.join(CACHE, "%s-%s-%08x" % (kind, safe, digest))


def _cached(path, ttl):
    if ttl and os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        with open(path, "rb") as f:
            return f.read()
    return None


def _store(path, blob):
    os.makedirs(CACHE, exist_ok=True)
    with open(path, "wb") as f:
        f.write(blob)


def _throttle(host, delay):
    prev = _last_hit.get(host)
    if prev is not None:
        wait = delay - (time.monotonic() - prev)
        if wait > 0:
            time.sleep(wait)
    _last_hit[host] = time.monotonic()


def robots_for(host, scheme, delay):
    """Fetch and parse robots.txt ourselves.

    RobotFileParser.read() uses urllib's default UA and treats a 403 as
    "disallow all", so calling it directly would make this script refuse to
    read Sandvik's and Atlas Copco's perfectly open IR pages. A missing
    robots.txt (404) means no restrictions.
    """
    if host in _robots:
        return _robots[host]
    parser = urllib.robotparser.RobotFileParser()
    url = "%s://%s/robots.txt" % (scheme, host)
    path = _cache_path("robots", url)
    body = _cached(path, ROBOTS_TTL)
    if body is None:
        try:
            _throttle(host, delay)
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20, context=_strict_ctx()) as r:
                body = r.read(1024 * 1024)
            _store(path, body)
        except urllib.error.HTTPError as e:
            body = b"" if e.code in (403, 404, 410) else None
            if body is not None:
                _store(path, body)
        except Exception:
            body = None
    if body is None:
        # Could not read robots.txt at all. Treat as unknown, not as licence:
        # only the IR entry point is fetched in that case (see crawl()).
        _robots[host] = None
        return None
    parser.parse(body.decode("utf-8", "replace").splitlines())
    _robots[host] = parser
    return parser


def allowed(url, delay):
    """True / False / None (robots.txt unreadable)."""
    p = urllib.parse.urlparse(url)
    rp = robots_for(p.netloc, p.scheme or "https", delay)
    if rp is None:
        return None
    try:
        return bool(rp.can_fetch(UA, url))
    except Exception:
        return None


def crawl_delay_for(url, floor):
    p = urllib.parse.urlparse(url)
    rp = _robots.get(p.netloc)
    if rp is None:
        return floor
    try:
        d = rp.crawl_delay(UA) or rp.crawl_delay("*")
    except Exception:
        d = None
    return max(floor, float(d)) if d else floor


def diagnose_host(host):
    """Distinguish 'the company is offline' from 'this network blocks it'."""
    try:
        ip = socket.gethostbyname(host)
    except OSError as e:
        return "DNS lookup failed (%s)" % e
    if ip.startswith(UMBRELLA_BLOCK_PREFIX):
        return ("BLOCKED BY LOCAL NETWORK - %s resolves to %s, a Cisco Umbrella "
                "block page. The IR site is filtered upstream, not down."
                % (host, ip))
    return None


def http_get(url, delay=1.0, ttl=None,
             accept="text/html,application/xhtml+xml,*/*",
             max_bytes=8 * 1024 * 1024):
    """GET following redirects. Returns (final_url, body_bytes, content_type)."""
    ttl = HTML_TTL if ttl is None else ttl
    path = _cache_path("get", url)
    blob = _cached(path, ttl)
    if blob is not None:
        head, _, body = blob.partition(b"\n\n")
        try:
            meta = json.loads(head.decode("utf-8"))
            return meta["url"], body, meta.get("ct", "")
        except (ValueError, KeyError):
            pass                            # corrupt cache entry; refetch

    host = urllib.parse.urlparse(url).netloc
    _throttle(host, delay)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": accept,
        "Accept-Language": "en-GB,en;q=0.9,sv;q=0.8"})
    last = None
    for ctx in (_strict_ctx(), _wide_ctx()):
        try:
            with urllib.request.urlopen(req, timeout=45, context=ctx) as r:
                body = r.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise Fetch("response exceeds %d bytes" % max_bytes, url)
                ct = r.headers.get("Content-Type", "")
                final = r.url
            _store(path, json.dumps({"url": final, "ct": ct}).encode("utf-8")
                   + b"\n\n" + body)
            return final, body, ct
        except urllib.error.HTTPError as e:
            raise Fetch("HTTP %s" % e.code, url)
        except urllib.error.URLError as e:
            last = e
            reason = str(getattr(e, "reason", e))
            if "CERTIFICATE_VERIFY_FAILED" in reason:
                # A verify failure after a successful handshake usually means a
                # middlebox re-signed the connection with a CA we do not trust.
                raise Fetch(diagnose_host(host) or
                            "TLS certificate could not be verified - the "
                            "connection is probably being intercepted by a "
                            "proxy whose root CA is not in this trust store",
                            url)
            continue
        except (TimeoutError, socket.timeout) as e:
            last = e
            continue
    raise Fetch(diagnose_host(host) or ("unreachable (%s)" % last), url)


def http_json(url, payload=None, delay=1.0, ttl=None):
    ttl = JSON_TTL if ttl is None else ttl
    key = url + ("|" + json.dumps(payload, sort_keys=True) if payload else "")
    path = _cache_path("json", key)
    blob = _cached(path, ttl)
    if blob is None:
        host = urllib.parse.urlparse(url).netloc
        _throttle(host, delay)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"User-Agent": UA, "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, headers=headers, data=data)
        try:
            with urllib.request.urlopen(req, timeout=40, context=_strict_ctx()) as r:
                blob = r.read()
        except Exception as e:
            raise Fetch(str(e)[:160], url)
        _store(path, blob)
    try:
        return json.loads(blob.decode("utf-8"))
    except ValueError as e:
        raise Fetch("response was not JSON (%s)" % e, url)


# --------------------------------------------------------------------------
# Name matching
# --------------------------------------------------------------------------

# Suffixes Avanza appends for share classes. Stripped before name matching so
# that "Volvo" matches "Volvo B" but the score still prefers the closer name.
CLASS_SUFFIX = re.compile(r"\s+(?:[A-D]|Pref(?:\s*[A-D])?|SDB|BTA|TR)$", re.I)
LEGAL_SUFFIX = re.compile(
    r"\b(?:ab|abp|a/s|asa|oyj|plc|nv|se|group|publ)\b\.?", re.I)


def normalise(name):
    s = html.unescape(name or "").lower()
    s = LEGAL_SUFFIX.sub(" ", s)
    s = re.sub(r"[^a-z0-9\u00e0-\u00ff ]+", " ", s)
    return " ".join(s.split())


# --------------------------------------------------------------------------
# Route 1: Avanza
# --------------------------------------------------------------------------

def avanza_candidates(query, delay):
    data = http_json(AVANZA_SEARCH,
                     {"query": query, "searchFilter": {"types": ["STOCK"]}},
                     delay=delay)
    out = []
    for hit in data.get("hits") or []:
        title = hit.get("title") or ""
        # "Atlas Copco B (ATCO B)" -> name "Atlas Copco B", ticker "ATCO B"
        m = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", title)
        name, ticker = (m.group(1), m.group(2)) if m else (title, "")
        out.append({"orderbook_id": hit.get("orderBookId"), "name": name,
                    "ticker": ticker, "country": hit.get("flagCode"),
                    "slug": hit.get("urlSlugName")})
    return out


def score_candidate(query, cand):
    """Rank Avanza hits against the query.

    Needed because "Volvo" returns Volvo B, Volvo Car B, Volvo A and
    Ferronordic, and "AB Volvo" must not land on Volvo Car. An exact match on
    the class-stripped name outranks everything; otherwise a prefix match, then
    a token-overlap fraction. Nordic listings break ties, because this toolkit
    is Nordic, and a trailing B breaks the remaining tie because that is the
    class an index quotes for a Swedish dual-class name.
    """
    q = normalise(query)
    base = normalise(CLASS_SUFFIX.sub("", cand["name"]))
    full = normalise(cand["name"])
    if q == base or q == full:
        s = 100.0
    elif base and (base.startswith(q) or q.startswith(base)):
        s = 70.0 - abs(len(base) - len(q))
    else:
        qt, bt = set(q.split()), set(base.split())
        s = 40.0 * len(qt & bt) / max(1, len(qt | bt))
    if cand.get("country") in ("SE", "NO", "DK", "FI", "IS"):
        s += 3
    if re.search(r"\bB$", cand["name"] or ""):
        s += 1
    return s


def avanza_lookup(query, delay):
    # Avanza's index does not carry legal forms, so "AB Volvo" matches nothing
    # at all while "Volvo" matches four instruments. Retry once with the legal
    # form stripped - the ranking below still uses the original query, so
    # "AB Volvo" continues to outrank Volvo Car.
    attempts = [query]
    stripped = normalise(query)
    if stripped and stripped != query.strip().lower():
        attempts.append(stripped)
    cands = []
    for attempt in attempts:
        try:
            cands = avanza_candidates(attempt, delay)
        except Fetch as e:
            note("Avanza search failed - %s" % e)
            return None, []
        if cands:
            if attempt != query:
                note("Avanza had no hit for %r; searched %r instead"
                     % (query, attempt))
            break
    if not cands:
        note("Avanza search returned no listed equity for %r" % query)
        return None, []
    ranked = sorted(cands, key=lambda c: -score_candidate(query, c))
    best = ranked[0]
    if score_candidate(query, best) < 25:
        note("Avanza's closest match %r scored too low to trust" % best["name"])
        return None, ranked
    try:
        det = http_json(AVANZA_DETAILS % best["orderbook_id"], delay=delay)
    except Fetch as e:
        note("Avanza details failed - %s" % e)
        return None, ranked
    company = det.get("company") or {}
    best["homepage"] = company.get("homepage") or None
    best["ceo"] = company.get("ceo")
    best["chairman"] = company.get("chairman")
    best["shares"] = company.get("totalNumberOfShares")
    best["owners"] = (det.get("companyOwners") or {}).get("owners") or []
    best["events"] = (det.get("companyEvents") or {}).get("events") or []
    best["dividends"] = (det.get("dividends") or {}).get("pastEvents") or []
    # Avanza also surfaces a third-party (Quartr) copy of the latest report.
    # Kept only as a last-resort cross-check: it is not the issuer's own file.
    best["thirdparty_report"] = None
    for rep in det.get("companyReports") or []:
        ev = rep.get("earningsCallEvent") or {}
        if ev.get("reportUrl"):
            per = rep.get("reportPeriodAndYear") or {}
            best["thirdparty_report"] = {
                "url": ev["reportUrl"],
                "period": "%s %s" % (per.get("reportPeriod"),
                                     per.get("financialYear")),
            }
            break
    return best, ranked


# --------------------------------------------------------------------------
# Route 2: MFN release bodies
# --------------------------------------------------------------------------

# Distributors, registrars, regulators and social networks all appear in
# release boilerplate. None of them is the issuer's own domain.
NOT_ISSUER = re.compile(
    r"(mfn\.se|cision\.com|globenewswire|prnewswire|businesswire|nasdaq|"
    r"avanza|nordnet|linkedin|twitter|x\.com|facebook|youtube|instagram|"
    r"google|w3\.org|schema\.org|euroclear|computershare|fi\.se|"
    r"bolagsverket|safelinks\.protection|inderes\.com|financialhearings|"
    r"quartr|gettyimages|adobe|apple\.com|spotify|outlook\.com)", re.I)


def mfn_domain(slug, delay):
    """Most-mentioned non-distributor domain across the issuer's own releases."""
    if not slug:
        return None
    try:
        feed = http_json(MFN_FEED % urllib.parse.quote(slug), delay=delay)
    except Fetch as e:
        note("MFN feed for %r failed - %s" % (slug, e))
        return None
    items = feed.get("items") or []
    if not items:
        note("MFN has no releases under slug %r (a Cision issuer, most likely)"
             % slug)
        return None
    counts = {}
    for it in items:
        blob = json.dumps(it.get("content") or {}, ensure_ascii=False)
        for dom in re.findall(
                r"(?:https?://|\bwww\.)([a-z0-9][a-z0-9.-]+\.[a-z]{2,})",
                blob, re.I):
            dom = dom.lower().rstrip(".")
            if NOT_ISSUER.search(dom):
                continue
            counts[dom] = counts.get(dom, 0) + 1
    if not counts:
        return None
    top = max(counts, key=counts.get)
    note("MFN: %r appears %d times across %d releases"
         % (top, counts[top], len(items)))
    return top


def gleif_lei(name, country, delay):
    """Legal-entity confirmation only. GLEIF carries no website field.

    Country is required to be useful: "Sandvik" alone matches a South African
    entity called SANDVIK before it matches Sandvik AB in Sweden, and a
    confidently-wrong LEI is worse than none.
    """
    key = normalise(name)
    if not key or not country:
        return None
    try:
        d = http_json(GLEIF_LEI.format(name=urllib.parse.quote(name)),
                      delay=delay)
    except Fetch:
        return None
    for rec in d.get("data") or []:
        ent = (rec.get("attributes") or {}).get("entity") or {}
        legal = (ent.get("legalName") or {}).get("name") or ""
        if ent.get("jurisdiction") != country:
            continue
        if normalise(legal) == key and (ent.get("status") == "ACTIVE"):
            return {"lei": rec.get("id"), "legal_name": legal,
                    "jurisdiction": ent.get("jurisdiction"),
                    "status": ent.get("status")}
    return None


# --------------------------------------------------------------------------
# HTML parsing
# --------------------------------------------------------------------------

TAG = re.compile(r"<[^>]+>")
SCRIPTY = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.S | re.I)
ANCHOR = re.compile(r"<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
                    re.S | re.I)
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
# Volvo Group ships its report PDFs inside JavaScript data rather than anchors,
# so anchors alone find zero. A raw scan recovers them; the cost is that the
# label then has to come from the filename.
RAW_DOC = re.compile(
    r"[\"'(]((?:https?://|/)[^\s\"'()<>]{4,300}?\.(?:pdf|xlsx|xls))"
    r"(?:[?#][^\s\"'()<>]*)?[\"')]", re.I)

# ESEF/iXBRL filings are only ever offered as .xhtml/.xbrl/.zip and are worth
# capturing because the sibling esef_fundamentals.py consumes exactly those.
# Those extensions are ambiguous elsewhere, so they only count on a path that
# says esef or xbrl.
DOC_EXT = re.compile(r"\.(pdf|xlsx|xls|xhtml|xbrl|zip)(?:[?#]|$)", re.I)
ESEF_PATH = re.compile(r"esef|xbrl|ixbrl", re.I)


def doc_ext(url):
    """File extension if this URL is a document worth recording, else None."""
    m = DOC_EXT.search(url)
    if not m:
        return None
    ext = m.group(1).lower()
    if ext in ("xhtml", "xbrl", "zip") and not ESEF_PATH.search(url):
        return None
    return ext

IR_HREF = re.compile(
    r"/(?:investor|investors|investor-relations|investorrelations|ir|"
    r"for-investors|about-us-investor|investerare|investerarrelationer|"
    r"finansiell-information|shareholder|aktieagare|"
    r"\u00e4garinformation)(?:[/.?#-]|$)", re.I)
IR_TEXT = re.compile(
    r"^\s*(?:investor relations|investors|for investors|investor|"
    r"investerare|investerarrelationer|ir|financial information|"
    r"finansiell information|shareholders?)\s*$", re.I)


def page_text(doc):
    return " ".join(html.unescape(TAG.sub(" ", SCRIPTY.sub(" ", doc))).split())


def page_title(doc):
    m = TITLE.search(doc)
    return " ".join(html.unescape(TAG.sub(" ", m.group(1))).split()) if m else ""


def defrag(url):
    return url.partition("#")[0]


def extract_links(base, doc):
    """[(absolute_url, link_text, from_anchor)] - deduped.

    Fragments are KEPT. A single-page IR site such as KebNi's puts its whole
    navigation on one URL - "The share" is /for-investors/#the-share - and
    dropping the fragment before deduplication collapsed every one of those
    entries into the landing page, losing the labels with them.
    """
    seen, out = set(), []
    for m in ANCHOR.finditer(doc):
        href = html.unescape(m.group(1)).strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        url = urllib.parse.urljoin(base, href)
        text = " ".join(html.unescape(TAG.sub(" ", m.group(2))).split())
        if url in seen or defrag(url) == "":
            continue
        seen.add(url)
        out.append((url, text, True))
    for m in RAW_DOC.finditer(doc):
        url = urllib.parse.urljoin(base, html.unescape(m.group(1)))
        if url in seen:
            continue
        seen.add(url)
        out.append((url, "", False))
    return out


def looks_like_ir(url, title=""):
    return bool(IR_HREF.search(urllib.parse.urlparse(url).path or "")
                or re.search(r"investor|investerare", title or "", re.I))


def verify(url, delay):
    """Follow the pointer. Report only what answered 200, at its final URL."""
    if allowed(url, delay) is False:
        return None, "robots.txt disallows it"
    try:
        final, body, ct = http_get(url, delay=crawl_delay_for(url, delay))
    except Fetch as e:
        return None, e.why
    if "html" not in ct.lower() and not body.lstrip()[:200].lower().startswith(b"<"):
        return None, "did not return HTML (%s)" % (ct or "unknown content type")
    return (final, body), None


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

def rx(*parts):
    return re.compile("|".join(parts), re.I)


# Section pages worth naming, and worth following one level deeper.
# (role, href pattern, link-text pattern, follow?)
SECTIONS = [
    ("reports_archive",
     rx(r"reports?-?(?:and|&|--)?-?present", r"reports-presentations",
        r"/reports?(?:/|$)", r"rapporter", r"finansiella-rapporter",
        r"reports-and-documents", r"financial-reports"),
     rx(r"^\s*(?:financial )?reports?(?: (?:&|and) presentations?)?\s*$",
        r"reports and presentations", r"all financial reports",
        r"rapporter(?: och presentationer)?", r"finansiella rapporter"), True),
    ("annual_reports_page",
     rx(r"annual-?reports?", r"arsredovisning", r"\u00e5rsredovisning",
        r"annual-and-sustainability"),
     rx(r"annual reports?", r"\u00e5rsredovisning",
        r"annual (?:&|and) sustainability"), True),
    ("interim_reports_page",
     rx(r"interim-?reports?", r"quarterly", r"delarsrapport",
        r"del\u00e5rsrapport"),
     rx(r"interim reports?", r"quarterly reports?", r"del\u00e5rsrapport"), True),
    ("financial_calendar",
     rx(r"(?:financial-)?calendar", r"kalend", r"finansiell-kalender",
        r"calendar-and-events"),
     rx(r"^\s*(?:financial )?calendar\s*$", r"calendar and events",
        r"finansiell kalender", r"kalendarium"), True),
    # Buyback and dividend pages are matched before the generic share page,
    # because both usually live *under* it (Atlas Copco's share repurchases
    # sit at /investors/atlas-copco-ab-share/share-repurchases) and the
    # specific page is where the actual mandate and policy are stated.
    ("buyback_page",
     rx(r"share-repurchase", r"repurchase", r"buy-?back", r"aterkop",
        r"återköp", r"treasury-shares"),
     rx(r"share repurchases?", r"buy-?backs?", r"återköp",
        r"repurchase of (?:own )?shares"), True),
    ("dividend_page",
     rx(r"dividend", r"utdelning"),
     rx(r"^\s*dividends?(?: information| policy| history)?\s*$",
        r"utdelning"), True),
    ("share_page",
     rx(r"the-share", r"[/-]shares?(?:-price|-information|-capital)?(?:/|$)",
        r"share-price-monitor", r"aktien", r"[/-]aktie(?:n|-|/|$)",
        r"shareholders?", r"ownership", r"agarstruktur",
        r"\u00e4garstruktur"),
     rx(r"^\s*the .{0,14}share\s*$",
        r"^\s*shares?(?: information| capital)?\s*$",
        r"shareholders?", r"ownership", r"aktien", r"\u00e4garstruktur"), True),
    ("financial_targets",
     rx(r"financial-targets?", r"group-targets?", r"[/-]targets?(?:/|$)",
        r"finansiella-mal", r"finansiella-m\u00e5l", r"[/-]mal(?:/|$)"),
     rx(r"financial targets?", r"group targets?", r"finansiella m\u00e5l",
        r"^\s*targets?\s*$"), True),
    ("governance",
     rx(r"corporate-governance", r"bolagsstyrning"),
     rx(r"corporate governance", r"bolagsstyrning"), True),
    ("agm",
     rx(r"annual-general-meeting", r"[/-]agm(?:/|$)", r"bolagsstamma",
        r"bolagsst\u00e4mma", r"general-meeting"),
     rx(r"annual general meeting", r"bolagsst\u00e4mma", r"general meeting"),
     True),
    ("presentations_page",
     rx(r"presentations?", r"capital-markets-day", r"webcast"),
     rx(r"presentations?", r"capital markets day", r"webcast"), False),
]

# Document classification, applied to filename + link text together. Order
# matters: "annual report presentation" is a presentation, so presentation is
# tested before interim but after annual.
DOC_KINDS = [
    ("presentation",
     rx(r"presentation", r"slides", r"[-_ ]deck[-_ .]",
        r"capital[-_ ]markets[-_ ]day", r"\bcmd\b", r"webcast",
        r"business[-_ ]update", r"roadshow")),
    ("annual_report",
     rx(r"annual[-_ ]?(?:and[-_ ]sustainability[-_ ])?report",
        r"arsredovisning", r"\u00e5rsredovisning", r"annual[-_ ]review",
        r"\bar[-_ ]?20\d\d\b")),
    ("interim_report",
     rx(r"interim[-_ ]report", r"quarterly[-_ ]report", r"delarsrapport",
        r"del\u00e5rsrapport", r"half[-_ ]year", r"nine[-_ ]month",
        r"niom\u00e5nader", r"year[-_ ]end[-_ ]report", r"bokslutskommunik",
        r"\bq[1-4][-_ /]?20\d\d", r"20\d\d[-_ /]?q[1-4]",
        r"(?:first|second|third|fourth)[-_ ]quarter", r"\b\d\dq[1-4]\b",
        r"\bq[1-4][-_ ]report")),
    ("sustainability_report",
     rx(r"sustainability[-_ ]report", r"hallbarhetsredovisning",
        r"h\u00e5llbarhetsredovisning", r"esg[-_ ]report")),
    ("governance_report",
     rx(r"corporate[-_ ]governance[-_ ]report", r"bolagsstyrningsrapport",
        r"remuneration[-_ ]report", r"ersattningsrapport",
        r"ers\u00e4ttningsrapport")),
]

# Topics we want to say WHERE we saw, quoting the sentence as evidence.
TOPICS = {
    "financial_targets": rx(
        r"financial target", r"finansiella m\u00e5l", r"group target",
        r"growth target", r"tillv\u00e4xtm\u00e5l", r"over a business cycle",
        r"\u00f6ver en konjunkturcykel", r"margin target",
        r"marginalm\u00e5l", r"return on capital employed target"),
    "dividend_policy": rx(
        r"dividend policy", r"utdelningspolicy", r"utdelningspolitik",
        r"payout ratio", r"utdelningsandel",
        r"dividend of (?:approximately |about )?\d",
        r"(?:per cent|percent|%) of (?:the )?(?:net |adjusted )?"
        r"(?:profit|earnings|income|result)",
        r"av (?:\u00e5rets|periodens) (?:resultat|vinst)"),
    "buyback_authorisation": rx(
        r"share (?:re)?purchase", r"repurchase of (?:its |the |own )*shares",
        r"buy[- ]?back", r"\u00e5terk\u00f6p av (?:egna )?aktier",
        r"mandate to (?:re)?purchase",
        r"authoris\w+ the board .{0,60}(?:re)?purchase",
        r"bemyndigande.{0,60}\u00e5terk\u00f6p", r"treasury shares",
        r"egna aktier"),
}

PERIOD_RANK = {"FY": 5, "Q4": 4, "9M": 3, "Q3": 3, "H1": 2, "Q2": 2, "Q1": 1}


def label_period(blob):
    """Best-effort period label from filename + link text. '' when unclear."""
    # Separators are noise and they differ per site: "second-quarter-2026",
    # "second quarter 2026" and "2021_q2_" all mean the same thing, so flatten
    # every non-alphanumeric run to a single space before matching.
    b = re.sub(r"[^0-9a-z\u00e0-\u00ff]+", " ", blob.lower())
    # A "published <year>" clause dates the release, not the reporting period;
    # max() over both turns the FY2024 annual report into "FY 2025".
    b = re.sub(r"\b(?:published|publicerad\w*|offentliggjord\w*|"
               r"released?|updated|uppdaterad\w*)\b\D{0,20}(?:19|20)\d\d",
               " ", b)
    years = [int(y) for y in re.findall(r"\b((?:19|20)\d\d)\b", b)]
    if not years:
        # Two-digit forms like "26q2" / "q2 26", only when no 4-digit year.
        m = re.search(r"\b(\d\d)q[1-4]\b|\bq[1-4] (\d\d)\b", b)
        if m:
            years = [2000 + int(m.group(1) or m.group(2))]
    year = years[0] if years else None

    quarter = None
    m = re.search(r"\bq ?([1-4])\b|\bkv ?([1-4])\b|\b\d\dq([1-4])\b", b)
    if m:
        quarter = "Q" + next(g for g in m.groups() if g)
    for word, q in (("first quarter", "Q1"), ("second quarter", "Q2"),
                    ("third quarter", "Q3"), ("fourth quarter", "Q4"),
                    ("f\u00f6rsta kvartalet", "Q1"),
                    ("andra kvartalet", "Q2"),
                    ("tredje kvartalet", "Q3"),
                    ("fj\u00e4rde kvartalet", "Q4")):
        if word in b:
            quarter = q
    if not quarter and re.search(r"half year|halv\u00e5r|six month", b):
        quarter = "H1"
    if not quarter and re.search(r"nine month|niom\u00e5nader", b):
        quarter = "9M"
    if not quarter and re.search(r"year end report|bokslutskommunik", b):
        quarter = "Q4"
    if not quarter and re.search(r"annual report|\u00e5rsredovisning|arsredovisning",
                                 b):
        quarter = "FY"

    if year and quarter:
        return "%s %d" % (quarter, year)
    if year:
        return str(year)
    return quarter or ""


def period_key(label):
    m = re.match(r"(?:(FY|Q[1-4]|H1|9M)\s+)?(\d{4})$", label or "")
    if not m:
        return (0, 0)
    return (int(m.group(2)), PERIOD_RANK.get(m.group(1) or "", 0))


def classify_doc(url, text):
    name = urllib.parse.unquote(
        urllib.parse.urlparse(url).path.rsplit("/", 1)[-1])
    blob = "%s %s" % (name, text)
    kind = "other"
    for k, pat in DOC_KINDS:
        if pat.search(blob):
            kind = k
            break
    return {"kind": kind, "url": url, "title": text or name,
            "filename": name, "period": label_period(blob),
            "format": (name.rsplit(".", 1)[-1].lower() if "." in name else "")}


# --------------------------------------------------------------------------
# The crawl
# --------------------------------------------------------------------------

def same_site(a, b):
    """Compare the last two labels, so www.home.sandvik matches home.sandvik.

    A common two-label public suffix (co.uk, com.au, ...) needs a third label
    kept, or every issuer on that suffix compares equal to every other.
    """
    def key(u):
        h = urllib.parse.urlparse(u).netloc.lower().split(":")[0].split(".")
        n = 3 if len(h) >= 3 and h[-2] in ("co", "com", "org", "net", "ac", "gov") else 2
        return h[-n:]
    return key(a) == key(b)


# What to spend the page budget on first.
ROLE_PRIORITY = {
    "reports_archive": 0, "annual_reports_page": 1, "interim_reports_page": 2,
    "financial_targets": 3, "dividend_page": 4, "buyback_page": 5,
    "share_page": 6, "financial_calendar": 7, "presentations_page": 8,
    "agm": 9, "governance": 10,
}

# Which crawled page is the natural home of which topic, so that evidence
# found there outranks the same words appearing in a navigation menu.
ROLE_TOPIC = {"financial_targets": "financial_targets",
              "dividend_page": "dividend_policy",
              "buyback_page": "buyback_authorisation",
              "agm": "buyback_authorisation"}


def classify_section(url, text, from_anchor):
    """Which SECTION role a link belongs to, or None.

    The href is matched against the LAST path segment before the whole path.
    Without that, /en/investors/reports-presentations/annual-reports/ matches
    the reports_archive pattern on its parent segment and the annual-reports
    page is never recognised - which is exactly how the annual report PDF went
    missing in the first draft.
    """
    path = urllib.parse.urlparse(url).path or "/"
    tail = "/" + (path.rstrip("/").rsplit("/", 1)[-1] or "") + "/"
    for scope in (tail, path):
        for role, hpat, _tpat, follow in SECTIONS:
            if hpat.search(scope):
                return role, follow
    if from_anchor and text:
        for role, _hpat, tpat, follow in SECTIONS:
            if tpat.search(text):
                return role, follow
    return None, False


def crawl(ir_url, ir_body, delay, max_pages):
    """IR landing page plus the highest-value sub-pages. Bounded and polite."""
    pages = [{"url": ir_url, "body": ir_body.decode("utf-8", "replace"),
              "role": "ir_root"}]
    sections = {}
    queue = []
    visited = {ir_url}
    # Everything under the IR landing page's own directory is IR content; a
    # link out to /news-and-media/... that happens to be called "Interim report
    # second quarter" is a press release, not the interim-reports section.
    ir_base = (urllib.parse.urlparse(ir_url).path or "/").rstrip("/")
    ir_base = ir_base.rsplit(".", 1)[0] if ir_base.endswith((".html", ".htm")) \
        else ir_base

    def under_ir(url):
        return (urllib.parse.urlparse(url).path or "/").startswith(ir_base)

    def harvest(page):
        for url, text, from_anchor in extract_links(page["url"], page["body"]):
            target = defrag(url)
            if doc_ext(target) or not same_site(target, ir_url):
                continue
            role, follow = classify_section(url, text, from_anchor)
            if not role:
                continue
            have = sections.get(role)
            # First one wins, unless the incumbent sits outside the IR section
            # and this one does not. The fragment is kept in what we report,
            # because on a one-page IR site it is the only thing that points
            # at the right part of the page.
            if have is None or (under_ir(url) and not under_ir(have["url"])):
                sections[role] = {"url": url, "text": text or role}
            if follow and target not in visited:
                queue.append((role, target, under_ir(target)))

    harvest(pages[0])

    budget = max_pages
    done_roles = set()
    # Two rounds: the IR nav names the sections, and a section page (Sandvik's
    # "Reports & Presentations") names the sub-sections that actually hold the
    # PDFs. Two levels is the whole crawl - more would be a bulk crawl, which
    # is exactly what we promised not to do. Roles are fetched at most once and
    # in value order, so a tight page budget spends itself on the reports and
    # the targets rather than on the governance boilerplate.
    for _round in range(2):
        pending, seen_roles = [], set()
        for role, url, is_ir in sorted(
                queue, key=lambda q: (ROLE_PRIORITY.get(q[0], 99), not q[2])):
            if role in seen_roles or role in done_roles or url in visited:
                continue
            seen_roles.add(role)
            pending.append((role, url))
        queue = []
        if not pending:
            break
        for role, url in pending:
            done_roles.add(role)
            if budget <= 0:
                break
            perm = allowed(url, delay)
            if perm is False:
                note("robots.txt disallows %s - skipped" % url)
                continue
            if perm is None:
                note("robots.txt unreadable for %s - stopped at the entry point"
                     % urllib.parse.urlparse(url).netloc)
                continue
            visited.add(url)
            budget -= 1
            try:
                final, body, ct = http_get(url, delay=crawl_delay_for(url, delay))
            except Fetch as e:
                note("could not read %s (%s)" % (url, e.why))
                continue
            if "html" not in ct.lower():
                continue
            page = {"url": final, "body": body.decode("utf-8", "replace"),
                    "role": role}
            pages.append(page)
            harvest(page)

    return pages, sections


def collect_docs(pages, ir_url):
    docs = {}
    for page in pages:
        for url, text, _ in extract_links(page["url"], page["body"]):
            url = defrag(url)
            if not doc_ext(url):
                continue
            d = classify_doc(url, text)
            d["found_on"] = page["url"]
            # The issuer sometimes hosts its own PDFs on its distributor's CDN
            # (KebNi links storage.mfn.se). Still the issuer's published file -
            # flagged so a citation can say where the bytes came from.
            d["offsite"] = not same_site(url, ir_url)
            prev = docs.get(url)
            if prev is None or (not prev["title"] and d["title"]):
                docs[url] = d
    return list(docs.values())


# Large caps increasingly publish the annual report as a website rather than a
# PDF - Sandvik's 2024 and 2025 reports live on annualreport.sandvik and its
# on-site PDF archive stops at 2023. Reporting "latest annual report: FY2023"
# in that situation would be wrong by two years, so online editions are
# collected separately rather than ignored.
ONLINE_AR = re.compile(r"annual\s*report|annualreport|årsredovisning", re.I)


def collect_online_reports(pages, ir_url):
    out = {}
    for page in pages:
        for url, text, from_anchor in extract_links(page["url"], page["body"]):
            url = defrag(url)
            if doc_ext(url) or not from_anchor:
                continue
            blob = "%s %s" % (url, text)
            if not ONLINE_AR.search(blob):
                continue
            period = label_period(blob)
            # "annualreport.sandvik/en/2025/" carries the year but no separate
            # "annual report" token once separators are flattened, so a bare
            # year is accepted here - the URL match above already established
            # that this is an annual report.
            if re.match(r"^\d{4}$", period or ""):
                period = "FY " + period
            if not re.match(r"FY \d{4}$", period or ""):
                continue
            # Must look like a dedicated publication, not a nav entry back to
            # the archive page we are standing on.
            if not re.search(r"annualreport|annual-report|arsredovisning|"
                             r"årsredovisning|report\.", url, re.I):
                continue
            if url == page["url"]:
                continue
            out.setdefault(period, {"period": period, "url": url,
                                    "title": text, "found_on": page["url"],
                                    "offsite": not same_site(url, ir_url)})
    return sorted(out.values(), key=lambda d: -period_key(d["period"])[0])


TOPIC_URL_HINT = {
    "financial_targets": re.compile(r"target|mal|mål", re.I),
    "dividend_policy": re.compile(r"dividend|utdelning|share|aktie", re.I),
    "buyback_authorisation": re.compile(
        r"repurchas|buy-?back|återköp|share|aktie|"
        r"general-meeting|bolagsst", re.I),
}
# The loudest text on an IR page is its own navigation, and the nav contains
# every topic word we are looking for. Two discriminators separate a real
# statement from a menu: a policy or a target quotes a figure, and a menu is
# full of chrome words. Both are cheap and both are needed - the nav on
# Sandvik's targets page contains digits ("2026 AGM", "rotate-180"), so
# "has a number" alone was not enough.
QUANTIFIED = re.compile(r"\d[\d.,]*\s?(?:%|per cent|percent|x\b)|"
                        r"(?:%|per cent|percent)\s?(?:of|av)\b|"
                        r"\bsek\s?\d|\bmsek\b|\bbn\b|\bmillion\b|\bbillion\b|"
                        r"\d[\d ,.]{2,}\s?(?:shares|aktier)")
CHROME = re.compile(r"summary_span|toggle|skip to|jump to|back to|"
                    r"cookie|menu|search|newsletter|subscribe|"
                    r"share this|follow us|read more about", re.I)


def find_topics(pages):
    out = {}
    for topic, pat in TOPICS.items():
        hint = TOPIC_URL_HINT.get(topic)
        best = None
        for page in pages:
            text = page_text(page["body"])
            title = page_title(page["body"])
            role = page.get("role") or ""
            if ROLE_TOPIC.get(role) == topic:
                place = 3
            elif hint and hint.search(urllib.parse.urlparse(page["url"]).path):
                place = 2
            elif pat.search(title or ""):
                place = 2
            else:
                place = 1
            for m in pat.finditer(text):
                start = max(0, m.start() - 160)
                lead = text[start:m.start()]
                # Drop any menu wreckage sitting in front of the match, so the
                # quoted evidence starts at prose rather than mid-navigation.
                cuts = [c.end() for c in CHROME.finditer(lead)]
                cuts += [i + 2 for i in (lead.rfind("]:"), lead.rfind("> "))
                         if i >= 0]
                if cuts:
                    lead = lead[max(cuts):]
                snippet = (lead + text[m.start():m.end() + 260]).strip()
                low = snippet.lower()
                score = (place,
                         1 if QUANTIFIED.search(low) else 0,
                         -len(CHROME.findall(snippet)))
                if best is None or score > best[0]:
                    best = (score, {"url": page["url"], "page_title": title,
                                    "evidence": snippet[:400]})
        if best:
            out[topic] = best[1]
    return out


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def latest(docs, kind):
    """Newest PDF of a kind. Ties go to the copy the issuer hosts itself."""
    pool = [d for d in docs if d["kind"] == kind and d["format"] == "pdf"]
    if not pool:
        return None
    labelled = [d for d in pool if period_key(d["period"]) != (0, 0)]
    if labelled:
        return max(labelled, key=lambda d: (period_key(d["period"]),
                                            0 if d["offsite"] else 1))
    # Unlabelled: one candidate is a safe guess, a pile of them is not.
    return pool[0] if len(pool) == 1 else None


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def discover(query, delay, max_pages, forced_url=None):
    result = {"query": query, "resolved_via": None, "ir_url": None,
              "ir_url_verified": False, "pointer": None, "pointers_tried": [],
              "cross_check": None, "legal_entity": None, "sections": {},
              "documents": [], "topics": {}, "pages_read": [],
              "notes": _notes}

    pointers = []
    av = ranked = None

    if forced_url:
        pointers.append(("--url (operator supplied)", forced_url))
    else:
        av, ranked = avanza_lookup(query, delay)
        if av:
            result["cross_check"] = {
                "source": "Avanza (broker redistribution - cross-check only, "
                          "never a primary citation)",
                "instrument": "%s (%s)" % (av["name"], av["ticker"]),
                "orderbook_id": av["orderbook_id"],
                "country": av.get("country"),
                "ceo": av.get("ceo"), "chairman": av.get("chairman"),
                "total_shares": av.get("shares"),
                "top_owners": [
                    {"name": o.get("name"),
                     "pct_capital": o.get("percentOfCapital"),
                     "pct_votes": o.get("percentOfVotes")}
                    for o in sorted(av.get("owners") or [],
                                    key=lambda o: -(o.get("percentOfCapital") or 0))
                    [:10]],
                "calendar": sorted(
                    [{"date": e.get("date"), "type": e.get("type"),
                      "confirmed": e.get("isConfirmed")}
                     for e in av.get("events") or []],
                    key=lambda e: e["date"] or ""),
                "dividends": [
                    {"ex_date": d.get("exDate"), "amount": d.get("amount"),
                     "currency": d.get("currencyCode"),
                     "type": d.get("dividendType")}
                    for d in (av.get("dividends") or [])[:6]],
                "third_party_report_copy": av.get("thirdparty_report"),
                "other_name_matches": [
                    "%s (%s) %s" % (c["name"], c["ticker"], c.get("country") or "")
                    for c in (ranked or [])[1:4]],
            }
            if av.get("homepage"):
                pointers.append(("Avanza market-guide company.homepage",
                                 av["homepage"]))
        dom = mfn_domain(slugify(query), delay)
        if dom:
            pointers.append(("MFN release bodies", "https://%s/" % dom))

    result["pointers_tried"] = [{"route": r, "url": u} for r, u in pointers]

    if not pointers:
        note("No route produced a pointer to a company domain. "
             "IR URL: %s" % NA)
        return result

    body = None
    for route, url in pointers:
        result["pointer"] = {"route": route, "url": url}
        got, why = verify(url, delay)
        if not got:
            note("pointer from %s did not verify - %s (%s)" % (route, why, url))
            continue
        final, body = got
        # A pointer may land on the corporate root rather than on IR. Follow
        # exactly one link, chosen from that page's own navigation - never
        # invented.
        doc = body.decode("utf-8", "replace")
        if not looks_like_ir(final, page_title(doc)):
            cand = None
            for u, text, from_anchor in extract_links(final, doc):
                if not same_site(u, final):
                    continue
                if IR_HREF.search(urllib.parse.urlparse(u).path or "") or \
                        (from_anchor and IR_TEXT.match(text or "")):
                    cand = u
                    break
            if cand:
                note("%s is not itself an IR page; followed its own investor "
                     "link to %s" % (final, cand))
                got2, why2 = verify(cand, delay)
                if got2:
                    final, body = got2
                else:
                    note("that investor link did not verify - %s" % why2)
            else:
                note("%s has no investor link in its own navigation" % final)
        result["ir_url"] = final
        result["ir_url_verified"] = True
        result["resolved_via"] = route
        break

    if not result["ir_url"]:
        return result

    pages, sections = crawl(result["ir_url"], body, delay, max_pages)
    result["sections"] = {k: v["url"] for k, v in sections.items()}
    result["documents"] = collect_docs(pages, result["ir_url"])
    result["online_annual_reports"] = collect_online_reports(
        pages, result["ir_url"])
    result["topics"] = find_topics(pages)
    result["pages_read"] = [p["url"] for p in pages]

    if av and av.get("name"):
        result["legal_entity"] = gleif_lei(
            CLASS_SUFFIX.sub("", av["name"]), av.get("country"), delay)
    return result


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def line(label, value, width=30):
    print("  %-*s %s" % (width, label + ":", value if value else NA))


def report(res, show_reports):
    print("=" * 78)
    print("INVESTOR RELATIONS - %s" % res["query"])
    print("=" * 78)

    print("\nPRIMARY SOURCE (the issuer's own site)")
    if res["ir_url"]:
        line("IR site", res["ir_url"] + "   [HTTP 200 verified]")
        line("found via", res["resolved_via"])
        ptr = res.get("pointer") or {}
        if ptr.get("url") and ptr["url"] != res["ir_url"]:
            line("pointer was", ptr["url"] + "   (redirected)")
    else:
        line("IR site", NA)
        for p in res.get("pointers_tried") or []:
            line("unverified pointer",
                 "%s  [via %s - NOT confirmed reachable, do not cite]"
                 % (p["url"], p["route"]))

    sec = res.get("sections") or {}
    docs = res.get("documents") or []

    if res["ir_url"]:
        print("\nKEY PAGES")
        line("financial reports archive",
             sec.get("reports_archive") or sec.get("annual_reports_page")
             or sec.get("interim_reports_page"))
        line("annual reports", sec.get("annual_reports_page"))
        line("interim reports", sec.get("interim_reports_page"))
        line("financial calendar", sec.get("financial_calendar"))
        line("the share / ownership", sec.get("share_page"))
        line("financial targets", sec.get("financial_targets"))
        line("dividend", sec.get("dividend_page"))
        line("share repurchases", sec.get("buyback_page"))
        line("corporate governance", sec.get("governance"))
        line("annual general meeting", sec.get("agm"))

        print("\nLATEST DOCUMENTS (published by the issuer)")
        online = (res.get("online_annual_reports") or [])
        for label, kind in (("annual report", "annual_report"),
                            ("interim report", "interim_report"),
                            ("investor presentation", "presentation")):
            d = latest(docs, kind)
            if d:
                tag = "   [hosted off-site]" if d["offsite"] else ""
                line(label, "%s%s" % (d["period"] or "period unclear", tag))
                print("  %-30s %s" % ("", d["url"]))
            else:
                line(label, NA)
            # An online edition newer than the newest PDF is the real answer.
            if kind == "annual_report" and online:
                newest = online[0]
                if not d or period_key(newest["period"]) > period_key(d["period"]):
                    line("  -> newer online edition",
                         "%s (web publication, not a PDF)" % newest["period"])
                    print("  %-30s %s" % ("", newest["url"]))
            if kind == "annual_report":
                esef = [x for x in docs
                        if x["format"] in ("xhtml", "xbrl", "zip")]
                if esef:
                    newest = max(esef, key=lambda x: period_key(x["period"]))
                    line("  -> ESEF/XBRL filing",
                         "%s  %s" % (newest["period"] or "period unclear",
                                     newest["url"]))
        tp = (res.get("cross_check") or {}).get("third_party_report_copy")
        if tp and not latest(docs, "interim_report"):
            print("  %-30s %s (%s)"
                  % ("cross-check copy only", tp["url"], tp["period"]))

        print("\nWHERE FOUND")
        for label, key in (("financial targets", "financial_targets"),
                           ("dividend policy", "dividend_policy"),
                           ("buyback authorisation", "buyback_authorisation")):
            t = (res.get("topics") or {}).get(key)
            if not t:
                line(label, NA)
                continue
            line(label, t["url"])
            print("  %-30s \"%s\"" % ("", t["evidence"][:220]))

    if show_reports and res["ir_url"]:
        print("\nREPORTS DISCOVERABLE ON THE IR SITE (%d documents)" % len(docs))
        order = {"annual_report": 0, "interim_report": 1, "presentation": 2,
                 "sustainability_report": 3, "governance_report": 4, "other": 5}
        for d in sorted(docs, key=lambda d: (order.get(d["kind"], 9),
                                             -period_key(d["period"])[0],
                                             -period_key(d["period"])[1],
                                             d["url"])):
            print("  %-22s %-10s %s%s"
                  % (d["kind"], d["period"] or "-", d["url"],
                     "   [off-site]" if d["offsite"] else ""))
        if not docs:
            print("  " + NA)

    cc = res.get("cross_check")
    if cc:
        print("\nCROSS-CHECK ONLY - %s" % cc["source"])
        line("instrument", "%s  orderbookId %s"
             % (cc["instrument"], cc["orderbook_id"]))
        line("CEO / chairman", "%s / %s"
             % (cc.get("ceo") or NA, cc.get("chairman") or NA))
        line("total shares",
             "{:,}".format(cc["total_shares"]) if cc.get("total_shares") else NA)
        if cc.get("top_owners"):
            print("  %s" % "largest owners (% capital / % votes):")
            for o in cc["top_owners"][:8]:
                print("    %-44s %6s  %6s"
                      % ((o["name"] or "")[:44], o["pct_capital"], o["pct_votes"]))
        if cc.get("calendar"):
            print("  calendar:")
            for e in cc["calendar"][:8]:
                print("    %s  %-32s %s"
                      % (e["date"], e["type"],
                         "confirmed" if e["confirmed"] else "indicative"))
        if cc.get("other_name_matches"):
            line("other name matches", "; ".join(cc["other_name_matches"]))

    le = res.get("legal_entity")
    if le:
        print("\nLEGAL ENTITY (GLEIF - identity only; GLEIF carries no website)")
        line("legal name", le["legal_name"])
        line("LEI", le["lei"])
        line("jurisdiction / status",
             "%s / %s" % (le.get("jurisdiction"), le.get("status")))

    if res["notes"]:
        print("\nNOTES")
        for n in res["notes"]:
            print("  - %s" % n)
    print()


def download(res, dest, delay):
    pdfs = [d for d in (res.get("documents") or []) if d["format"] == "pdf"]
    if not pdfs:
        print("  %s: no PDFs to download" % NA)
        return
    os.makedirs(dest, exist_ok=True)
    for d in pdfs:
        if allowed(d["url"], delay) is False:
            print("  SKIPPED (robots.txt) %s" % d["url"])
            continue
        stem = re.sub(r"[^A-Za-z0-9._-]", "_",
                      "%s_%s_%s" % (d["kind"], d["period"] or "na",
                                    d["filename"]))[:150]
        if not stem.lower().endswith(".pdf"):
            stem += ".pdf"
        path = os.path.join(dest, stem)
        if os.path.exists(path) and os.path.getsize(path) > 1000:
            print("  have    %s" % path)
            continue
        try:
            _, body, _ = http_get(d["url"], delay=crawl_delay_for(d["url"], delay),
                                  ttl=0, accept="application/pdf,*/*",
                                  max_bytes=200 * 1024 * 1024)
        except Fetch as e:
            print("  FAILED  %s (%s)" % (d["url"], e.why))
            continue
        if not body.startswith(b"%PDF"):
            print("  FAILED  %s (server did not return a PDF)" % d["url"])
            continue
        with open(path, "wb") as f:
            f.write(body)
        print("  saved   %s  (%.1f MB)" % (path, len(body) / 1e6))


def main():
    ap = argparse.ArgumentParser(
        description="Find and read a listed company's own Investor Relations "
                    "site. The IR site is the primary source; MFN and Cision "
                    "are only distribution channels.")
    ap.add_argument("company", nargs="?",
                    help='e.g. "Sandvik", "AB Volvo", "KebNi"')
    ap.add_argument("--url", help="skip discovery and read this IR URL directly")
    ap.add_argument("--reports", action="store_true",
                    help="list every report document found, with period labels")
    ap.add_argument("--download", metavar="DIR",
                    help="save the discovered report PDFs into DIR")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="minimum seconds between requests to one host "
                         "(default 1.0; a larger robots.txt crawl-delay wins)")
    ap.add_argument("--max-pages", type=int, default=12,
                    help="sub-pages to read below the IR landing page "
                         "(default 12)")
    ap.add_argument("--no-cache", action="store_true",
                    help="ignore the cache in %s" % CACHE)
    args = ap.parse_args()

    if not args.company and not args.url:
        ap.error("give a company name, or --url")
    if args.no_cache:
        global HTML_TTL, JSON_TTL, ROBOTS_TTL
        HTML_TTL = JSON_TTL = ROBOTS_TTL = 0

    res = discover(args.company or args.url, args.delay, args.max_pages,
                   forced_url=args.url)

    if args.as_json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        report(res, args.reports)

    if args.download:
        print("DOWNLOADING TO %s" % os.path.abspath(args.download))
        download(res, args.download, args.delay)

    return 0 if res.get("ir_url") else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
