#!/usr/bin/env python3
"""Regulatory filings and press releases for Nordic issuers, from MFN.se.

MFN is the distribution channel Swedish listed companies use for MAR-regulated
disclosures. Releases tagged ":regulatory" are the primary-source equivalent of
an 8-K; interim and annual reports arrive here with the PDF attached, which is
the document to read rather than any secondary summary.

Usage:
    python mfn_news.py evolution
    python mfn_news.py volvo --reports          # interim/annual reports only
    python mfn_news.py investor-ab --regulatory --limit 15
    python mfn_news.py sandvik --since 2024-01-01   # page back past the default window
    python mfn_news.py atlas-copco --pages 10       # ~300 releases, oldest first at page 10
    python mfn_news.py --search "atlas copco"   # find the slug
    python mfn_news.py evolution --json

Free JSON Feed, no API key. Always resolve the slug with --search first.

ENDPOINT, corrected 2026-08-31: the per-company feed at /a/<slug>.json caps at
~30 items and silently ignores its own offset parameter - it cannot page past
that cap. This module instead pages the SITE-WIDE feed,
/all/a.json?author=<slug>&limit=30&offset=N, which pages an issuer's full
history correctly (verified back to 2015 for Evolution) and, unlike the
capped endpoint, is what actually carries several Swedish large caps (below).
The capped /a/<slug>.json is kept only as a fallback for a slug the paging
endpoint returns nothing for.

COVERAGE, corrected 2026-08-31: the earlier claim that MFN "does not carry"
Sandvik, Atlas Copco, Hexagon or AB Volvo was wrong - it was an artefact of the
capped, non-paging endpoint, not a real gap. All four ARE on MFN: they
distribute through Cision, and MFN mirrors Cision's feed for them at
https://mfn.se/cis/a/<slug>/... (native MFN issuers stay at
https://mfn.se/a/<slug>/...; the slug string itself is identical on both
paths, e.g. "sandvik", so --search resolves it either way). Verified
empirically across all four: Cision-mirrored items carry the SAME MFN tag
vocabulary as native releases - ":regulatory", "sub:report",
"sub:report:interim", "sub:report:annual", etc. - so --regulatory, --reports
and the REGULATORY/REPORT markers below all work unchanged on them, with no
special-casing needed. Items sourced via the Cision mirror are additionally
marked "CISION" in the output so provenance stays visible.

For First North / Spotlight / NGM issuers there is no ESEF filing at all, so the
report release here IS the primary source. Use --figures and --text.
"""
import argparse
import datetime
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://mfn.se"
UA = "Mozilla/5.0 (compatible; investment-analyst-skill/1.0)"

REPORT_TAGS = ("sub:report", "sub:report:interim", "sub:report:annual",
               "sub:report:year-end", "sub:report:quarterly")

# --- Paging the site-wide feed ---------------------------------------------
#
# /a/<slug>.json caps at ~30 items and ignores offset (verified 2026-08-31).
# /all/a.json?author=<slug> is the same underlying feed but pages correctly,
# and it is also the endpoint that carries Cision-mirrored large caps
# (Sandvik, Atlas Copco, Hexagon, AB Volvo) - see the module docstring.
PAGE_SIZE = 30
MAX_PAGE_REQUESTS = 20          # politeness ceiling: at most this many HTTP
                                # requests per run, regardless of --pages/--since
DEFAULT_AUTO_PAGES = 6          # pages fetched when neither --pages nor --since
                                # is given - 180 raw items, enough headroom for
                                # --reports/--regulatory to still fill --limit

CACHE = os.path.join(tempfile.gettempdir(), "mfn-news-cache")
CACHE_TTL_RECENT = 15 * 60          # the newest page changes through the day
CACHE_TTL_HISTORY = 30 * 24 * 3600  # a page of history, once published, is immutable

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def fetch(path, **params):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit("DATA NOT AVAILABLE: MFN returned HTTP %s for %s" % (e.code, url))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SystemExit("DATA NOT AVAILABLE: MFN unreachable (%s)" % e)


def _cache_path(key):
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)[:150]
    return os.path.join(CACHE, safe)


def fetch_json_cached(url, cache_key, ttl):
    """GET JSON with an on-disk cache. Returns parsed JSON, or None on failure.

    Callers degrade gracefully on None rather than crash - a dead page (or a
    slug the paging endpoint has nothing for) should fall through to the
    fallback path, not abort the whole run.
    """
    path = _cache_path(cache_key)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        try:
            with open(path, "rb") as fh:
                return json.loads(fh.read())
        except (OSError, ValueError):
            pass  # corrupt cache entry; refetch

    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None

    try:
        os.makedirs(CACHE, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(body)
    except OSError:
        pass  # cache is an optimisation only
    return data


def fetch_company_pages(slug, pages=None, since=None, auto_pages=DEFAULT_AUTO_PAGES):
    """Page /all/a.json?author=<slug> - the endpoint that actually pages.

    Without --pages/--since, fetches `auto_pages` pages (enough over-fetch for
    client-side filters to still fill --limit). --pages fetches exactly that
    many pages (up to MAX_PAGE_REQUESTS). --since keeps paging back until a
    page's oldest item predates it, or MAX_PAGE_REQUESTS is hit - whichever
    first. offset=0 is cached briefly (it changes through the day); older
    pages are cached for a month since a published page of history does not
    change once written.
    """
    if pages is not None:
        request_cap = max(1, min(pages, MAX_PAGE_REQUESTS))
    else:
        request_cap = MAX_PAGE_REQUESTS if since else min(auto_pages, MAX_PAGE_REQUESTS)

    items, offset, made = [], 0, 0
    while made < request_cap:
        ttl = CACHE_TTL_RECENT if offset == 0 else CACHE_TTL_HISTORY
        url = BASE + "/all/a.json?" + urllib.parse.urlencode(
            {"author": slug, "limit": PAGE_SIZE, "offset": offset})
        data = fetch_json_cached(url, cache_key="a-%s-%06d" % (slug, offset), ttl=ttl)
        made += 1
        page_items = (data or {}).get("items") or []
        if not page_items:
            break
        items.extend(page_items)
        oldest = (page_items[-1].get("content") or {}).get("publish_date", "")[:10]
        offset += PAGE_SIZE
        if since and oldest and oldest < since:
            break
    return items


def flatten(item):
    content = item.get("content") or {}
    props = item.get("properties") or {}
    author = item.get("author") or {}
    tags = props.get("tags") or []
    url = item.get("url") or ""
    return {
        "date": (content.get("publish_date") or "")[:19],
        "company": author.get("name"),
        "slug": author.get("slug"),
        "title": content.get("title"),
        "preamble": (content.get("preamble") or "").strip(),
        # The full body is the primary source for issuers with no ESEF filing.
        "text": content.get("text") or "",
        "lang": props.get("lang"),
        "tags": tags,
        "regulatory": ":regulatory" in tags,
        "is_report": any(t.startswith("sub:report") for t in tags),
        "url": url,
        # /cis/a/<slug>/... is MFN's mirror of a Cision-distributed release
        # (e.g. Sandvik, Atlas Copco, Hexagon, AB Volvo). Verified empirically
        # that these carry the identical tag vocabulary to native /a/<slug>/...
        # releases, so every tag-based filter below applies to them unchanged;
        # this flag is provenance only, not a filtering distinction.
        "via_cision": "/cis/a/" in url,
        "attachments": [
            {"title": a.get("title"), "url": a.get("url"), "type": a.get("content_type")}
            for a in (content.get("attachments") or [])
        ],
    }


# --- Figure extraction from report press releases -------------------------
#
# First North, Spotlight and NGM issuers are exempt from ESEF, so no tagged
# XBRL exists for them. The MAR-regulated report release is the primary source,
# and its body normally carries the headline figures with prior-year
# comparatives. Formats vary a lot between issuers, so this extractor is
# BEST EFFORT and always prints the raw line it parsed, making a misparse
# visible rather than silent.

UNIT_RE = re.compile(r"\(\s*([KMT]SEK|MEUR|KEUR|MNOK|MDKK)\s*\)", re.I)

# A heading such as "Financial development Apr-Jun 2026 (KSEK)" scopes the
# bullets beneath it. Without capturing it, a report's quarter and year-to-date
# figures are emitted under IDENTICAL labels - KebNi Q2 2026 shows both
# "Net sales 28,838" and "Net sales 41,881" - and picking the wrong one is a
# 45% revenue error with a correct-looking citation.
PERIOD_RE = re.compile(
    # A section heading scopes the bullets beneath it. Without it a report's
    # quarter and full-year figures appear under IDENTICAL labels, and picking
    # the wrong one is a 4x revenue error with a correct-looking citation.
    '\\b((?:first|second|third|fourth|1st|2nd|3rd|4th|första|andra|tredje|fjärde)\\s+(?:quarter|kvartalet|kvartal)(?:\\s+(?:of|av))?\\s*\\d{4}|(?:jan|feb|mar|apr|maj|may|jun|jul|aug|sep|okt|oct|nov|dec)[a-z]*\\s*[-\\u2013]\\s*(?:jan|feb|mar|apr|maj|may|jun|jul|aug|sep|okt|oct|nov|dec)[a-z]*\\s*\\d{4}|(?:full[\\s-]?year|hel\\u00e5r)\\s*\\d{4}|(?:Q[1-4]|kvartal(?:et)?\\s*[1-4])\\s*\\d{0,4})',
    re.I)

BULLET = re.compile(r"^[\s]*[\u2022\u00b7*\u2013\u2014-]\s+")
UNIT_WORD = (r"thousands?|millions?|billions?|tkr|mkr|mdkr|"
             r"[KMT]SEK|MEUR|KEUR|MNOK|MDKK|SEK|EUR|NOK|DKK|USD|%")
# Scale-only tokens (no bare currency code), for use as a PREFIX before the
# number. "MSEK 104 435 (112 047)" - the large-cap Swedish style - puts the
# scale before the figure, not after it as a suffix. Bare currency codes
# (SEK/EUR/NOK/DKK/USD) are deliberately excluded here: CURRENCY_PREFIX /
# CUR_CAPTURE already own that job, and letting this group also match them
# would let it steal "SEK" away from curprefix, breaking the fallback that
# infers currency from curprefix when the unit is a bare scale word. Mirrors
# ttm_engine.py's own FIGURE_RE, which has the equivalent "pre" group.
SCALE_PREFIX_WORD = (r"thousands?|millions?|billions?|tkr|mkr|mdkr|"
                     r"[KMT]SEK|MEUR|KEUR|MNOK|MDKK")
CURRENCY_PREFIX = r"(?:SEK|EUR|NOK|DKK|USD|kr)\s*"
CUR_CAPTURE = r"(?P<curprefix>SEK|EUR|NOK|DKK|USD)?\s*"
# "- 11 471": Nordic releases put a space after the minus sign. Without allowing
# it, the non-greedy label swallows the sign and a cash outflow reads as an
# inflow - a silent error of exactly the kind this toolkit exists to prevent.
# Numbers arrive in four conventions and a single figure can combine two:
#   28 838   Nordic space thousands
#   24,297   English comma thousands
#   -0,05    Nordic decimal comma
#   2,063.1  English comma thousands AND a decimal point
# The old pattern allowed only one separator group, so it could not span
# the last form at all - Evolution's full-year revenue line simply never
# matched, and the release cross-check silently had nothing to compare.
NUMBER = '-?[\\s\\u00a0]?\\d{1,3}(?:[\\s\\u00a0\\u2009,]\\d{3})*(?:[.,]\\d+)?'

# label ... [prefix-scale] number [unit] ( [prefix-scale] comparative [unit] )
FIGURE_RE = re.compile(
    r"(?P<label>[A-Za-z\u00c5\u00c4\u00d6\u00e5\u00e4\u00f6\u00dc\u00fc\u00d8\u00f8\u00c6\u00e6][^()\n]{2,70}?)"
    r"[,:]?\s+(?P<preunit>" + SCALE_PREFIX_WORD + r")?\s*" + CUR_CAPTURE +
    r"(?P<cur>" + NUMBER + r")"
    r"\s*(?P<curunit>" + UNIT_WORD + r")?"
    r"[^()\d]{0,12}?\(\s*(?P<preunit2>" + SCALE_PREFIX_WORD + r")?\s*(?:" + CURRENCY_PREFIX + r")?"
    r"(?P<prev>" + NUMBER + r")"
    r"\s*(?P<prevunit>" + UNIT_WORD + r")?\s*\)",
    re.UNICODE | re.IGNORECASE)

SCALE = {"THOUSAND": 1e3, "THOUSANDS": 1e3, "TKR": 1e3, "KSEK": 1e3, "TSEK": 1e3,
         "KEUR": 1e3, "MILLION": 1e6, "MILLIONS": 1e6, "MKR": 1e6, "MSEK": 1e6,
         "MEUR": 1e6, "MNOK": 1e6, "MDKK": 1e6, "BILLION": 1e9, "BILLIONS": 1e9,
         "MDKR": 1e9}

# A comma followed by groups of exactly three digits is an English thousands
# separator (SEK 24,297 thousand). A comma followed by one or two digits is a
# Nordic decimal comma (-0,05). Getting this backwards turns 24,297 into 24.297
# - a 1000x error that looks entirely plausible on the page.
THOUSANDS_COMMA = re.compile(r"^-?\d{1,3}(?:,\d{3})+$")


# Typeset financial documents use U+2212 MINUS SIGN or an en-dash where a
# keyboard writes a hyphen. Left unnormalised, "− 13 612" parsed as
# +13,612 - a cash OUTFLOW emitted as an inflow, with a correct-looking
# source line beside it.
MINUS_CHARS = "−–‐‑‒˗﹣－"


def normalise_minus(text):
    if not text:
        return text
    for ch in MINUS_CHARS:
        text = text.replace(ch, "-")
    return text


def to_number(raw):
    """Parse a figure written in any of the conventions Nordic issuers use.

    Four live formats, and they appear in the same report:

        28 838      Nordic, space thousands
        24,297      English, comma thousands
        -0,05       Nordic, comma decimal
        2,063.1     English, comma thousands AND period decimal

    The last one used to return None, so Evolution's full-year revenue line
    - "Net revenues increased 14.7% to EUR 2,063.1 million" - was silently
    dropped and the release cross-check found nothing to compare. A dropped
    figure is quieter than a wrong one but it hides just as much.
    """
    if raw is None:
        return None
    cleaned = normalise_minus(raw)
    for space in (" ", "\u00a0", "\u2009", "\u202f"):
        cleaned = cleaned.replace(space, "")
    cleaned = cleaned.strip()
    if not cleaned:
        return None

    has_comma = "," in cleaned
    has_dot = "." in cleaned

    if has_comma and has_dot:
        # Whichever separator comes last is the decimal point; the other groups
        # thousands. "2,063.1" is English, "2.063,1" is continental.
        if cleaned.rfind(".") > cleaned.rfind(","):
            cleaned = cleaned.replace(",", "")
        else:
            cleaned = cleaned.replace(".", "").replace(",", ".")
    elif has_comma:
        # A comma followed by groups of exactly three digits is a thousands
        # separator; one followed by one or two digits is a decimal comma.
        # Reading 24,297 as 24.297 is a 1000x error that looks entirely
        # plausible on the page.
        if THOUSANDS_COMMA.match(cleaned):
            cleaned = cleaned.replace(",", "")
        else:
            cleaned = cleaned.replace(",", ".")

    try:
        return float(cleaned)
    except ValueError:
        return None


def reflow(text):
    """Join wrapped continuation lines back onto their bullet.

    Several issuers wrap a bullet mid-sentence, which splits the figure from its
    comparative across two lines and defeats any line-by-line match.
    """
    blocks = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            blocks.append("")
            continue
        if BULLET.match(line) or not blocks or not blocks[-1]:
            blocks.append(line.strip())
        else:
            blocks[-1] += " " + line.strip()
    return [b for b in blocks if b]


def extract_figures(text):
    """Return [{label, current, previous, unit, source_line}] - best effort."""
    if not text:
        return []
    text = normalise_minus(text)
    out, unit_context, period_context = [], None, None
    for block in reflow(text):
        head = UNIT_RE.search(block)
        period = PERIOD_RE.search(block)

        # "January-December 2024 (2023)" matches FIGURE_RE perfectly - label,
        # number, bracket, number - so a full-year section heading was being
        # consumed as a data row worth 2024, and every row beneath it kept the
        # previous quarter's period. A block whose only numbers are plausible
        # YEARS is a heading, never a figure.
        looks_like_heading = False
        if period and len(block) < 120:
            nums = [to_number(m.group("cur")) for m in FIGURE_RE.finditer(block)]
            nums += [to_number(m.group("prev")) for m in FIGURE_RE.finditer(block)]
            real = [n for n in nums if n is not None]
            if real and all(1990 <= n <= 2100 and float(n).is_integer() for n in real):
                looks_like_heading = True

        if looks_like_heading or not FIGURE_RE.search(block):
            if head:
                unit_context = head.group(1).upper().replace(" ", "")
            if period and len(block) < 120:
                # Only a short line is a heading; a long sentence merely
                # mentions a period.
                period_context = re.sub(r"\s+", " ", period.group(1)).strip()
            if head or period:
                continue
        for m in FIGURE_RE.finditer(block):
            label = re.sub(r"^[\s\u2022\u00b7*\u2013\u2014-]+", "", m.group("label"))
            label = re.sub(r"\s+", " ", label).strip(" ,:-.")
            if len(label) < 3:
                continue
            cur = to_number(m.group("cur"))
            prev = to_number(m.group("prev"))
            if cur is None:
                continue

            curprefix = (m.group("curprefix") or "").upper()
            # A scale token can appear before the number ("MSEK 104 435") as
            # well as after it ("104 435 MSEK") - fall back to the prefix
            # form only when no suffix was captured, so a line carrying both
            # still prefers the suffix as it always did.
            curunit = (m.group("curunit") or m.group("preunit") or "").upper()
            prevunit = (m.group("prevunit") or m.group("preunit2") or "").upper()

            # "Adjusted net profit, -3 798 (-13%)" - the bracket holds a margin,
            # not last year's figure. Treating it as a comparative would invert
            # the apparent trend.
            pct = None
            if prevunit == "%" and curunit != "%":
                pct, prev = prev, None
                # The bracket was a margin, so its "%" is not this figure's unit.
                # Leaving it set labelled KebNi's -3,798 KSEK adjusted profit as
                # a percentage.
                prevunit = ""

            unit = curunit or prevunit or unit_context or ""
            per_share = bool(re.search(r"per share|per aktie|/share|/aktie",
                                       label, re.I))

            # Scale to absolute units where the release states the scale, so a
            # KSEK line and an MSEK line become comparable. Per-share figures
            # never carry the heading's scale.
            factor = SCALE.get(unit)
            if factor and not per_share:
                cur *= factor
                if prev is not None:
                    prev *= factor
                # After scaling, report the base currency - a value already
                # multiplied to absolute terms must not still say "THOUSAND".
                if "SEK" in unit or unit in ("TKR", "MKR", "MDKR"):
                    unit = "SEK"
                elif "EUR" in unit:
                    unit = "EUR"
                elif "NOK" in unit:
                    unit = "NOK"
                elif "DKK" in unit:
                    unit = "DKK"
                else:
                    unit = curprefix or "?"

            if per_share:
                unit = "per share"

            out.append({"label": label, "current": cur, "previous": prev,
                        "pct_in_brackets": pct, "unit": unit,
                        "period": period_context,
                        "source_line": block[:150]})
    return out


def download_attachments(item, directory):
    """Save a release's PDFs locally so the full statements can be read."""
    saved = []
    os.makedirs(directory, exist_ok=True)
    for a in item["attachments"]:
        url = a.get("url")
        if not url or not url.lower().endswith(".pdf"):
            continue
        name = os.path.basename(urllib.parse.urlparse(url).path) or "report.pdf"
        path = os.path.join(directory, name)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
                f.write(r.read())
            saved.append(path)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            saved.append("FAILED %s (%s)" % (url, e))
    return saved


def search(term, limit=12):
    data = fetch("/all/s.json", query=term, limit=limit)
    seen, out = set(), []
    for it in data.get("items") or []:
        a = it.get("author") or {}
        key = a.get("slug")
        if key and key not in seen:
            seen.add(key)
            out.append({"slug": key, "name": a.get("name"),
                        "aliases": a.get("slugs") or []})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug", nargs="?", help="MFN company slug, e.g. evolution")
    ap.add_argument("--search", help="find the slug for a company name")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--regulatory", action="store_true", help="MAR-regulated releases only")
    ap.add_argument("--reports", action="store_true", help="interim/annual reports only")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--text", action="store_true",
                    help="print the full release body (the primary source for "
                         "First North / Spotlight / NGM issuers)")
    ap.add_argument("--figures", action="store_true",
                    help="best-effort extraction of headline figures, each shown "
                         "with the raw line it came from")
    ap.add_argument("--lang", choices=("en", "sv"),
                    help="keep only releases in this language (issuers publish both)")
    ap.add_argument("--pdf", metavar="DIR",
                    help="download attached PDFs into DIR for full-statement reading")
    ap.add_argument("--pages", type=int, metavar="N",
                    help="fetch N pages of %d releases each (default %d), to reach "
                         "further back than the default window; capped at %d pages"
                         % (PAGE_SIZE, DEFAULT_AUTO_PAGES, MAX_PAGE_REQUESTS))
    ap.add_argument("--since", metavar="YYYY-MM-DD",
                    help="keep paging back until a release older than this date is "
                         "reached (capped at %d pages)" % MAX_PAGE_REQUESTS)
    args = ap.parse_args()

    since_date = None
    if args.since:
        try:
            since_date = datetime.datetime.strptime(args.since, "%Y-%m-%d").date().isoformat()
        except ValueError:
            ap.error("--since must be YYYY-MM-DD")

    if args.search:
        hits = search(args.search)
        if not hits:
            print("DATA NOT AVAILABLE: no MFN issuer matched %r." % args.search)
            return
        print("MFN issuers matching %r:" % args.search)
        for h in hits:
            extra = ("  aliases: " + ", ".join(h["aliases"])) if len(h["aliases"]) > 1 else ""
            print("  %-28s %s%s" % (h["slug"], h["name"], extra))
        return

    if not args.slug:
        ap.error("give a slug, or use --search NAME to find one")

    # Page the site-wide feed - it pages correctly and carries Cision-mirrored
    # large caps that the capped /a/<slug>.json cannot reach at all.
    raw = fetch_company_pages(args.slug, pages=args.pages, since=since_date)
    used_fallback = False
    if not raw:
        # Fallback: the legacy, native, ~30-item-capped feed, for a slug the
        # paging endpoint itself returns nothing for.
        legacy_url = (BASE + "/a/%s.json?" % urllib.parse.quote(args.slug)
                      + urllib.parse.urlencode({"limit": max(args.limit * 4, 40)}))
        legacy = fetch_json_cached(legacy_url, cache_key="native-%s" % args.slug,
                                   ttl=CACHE_TTL_RECENT)
        raw = (legacy or {}).get("items") or []
        used_fallback = True

    items = [flatten(i) for i in raw]
    if since_date:
        items = [i for i in items if i["date"][:10] >= since_date]

    if not items:
        print("DATA NOT AVAILABLE: no releases found under slug %r." % args.slug)
        print()
        print("This checks both MFN's native feed and its Cision mirror (used by")
        print("large caps such as Sandvik, Atlas Copco, Hexagon and AB Volvo that")
        print("distribute through Cision, not MFN directly) - so an empty result here")
        print("means the slug is wrong or the issuer truly has no releases, not that")
        print("MFN lacks coverage for a whole class of company.")
        print("Run --search to confirm the slug before concluding it is wrong.")
        return

    if args.reports:
        items = [i for i in items if i["is_report"]]
    elif args.regulatory:
        items = [i for i in items if i["regulatory"]]
    if args.lang:
        items = [i for i in items if i["lang"] == args.lang]
    items = items[:args.limit]

    if args.figures or args.as_json:
        for i in items:
            i["figures"] = extract_figures(i.get("text"))

    if args.as_json:
        print(json.dumps({"slug": args.slug, "count": len(items),
                          "source": "MFN.se (native /a/<slug>.json fallback)"
                                    if used_fallback else
                                    "MFN.se (paged /all/a.json?author=<slug>)",
                          "retrieved_utc": datetime.datetime.now(
                              datetime.timezone.utc).isoformat(),
                          "items": items}, indent=2, ensure_ascii=False))
        return

    if not items:
        print("DATA NOT AVAILABLE: no releases matched the filter for %r." % args.slug)
        return

    print("%s  |  MFN.se  |  %d releases  |  retrieved %s"
          % (items[0]["company"] or args.slug, len(items),
             datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")))
    print()
    for i in items:
        marks = []
        if i["regulatory"]:
            marks.append("REGULATORY")
        if i["is_report"]:
            marks.append("REPORT")
        if i["via_cision"]:
            marks.append("CISION")
        flag = ("  [" + "/".join(marks) + "]") if marks else ""
        print("%s  %s%s" % (i["date"][:16], i["title"], flag))
        print("    %s" % i["url"])
        for a in i["attachments"]:
            print("    PDF: %s  ->  %s" % (a["title"] or "attachment", a["url"]))
        if args.pdf:
            for path in download_attachments(i, args.pdf):
                print("    SAVED: %s" % path)
        if args.figures:
            figs = i.get("figures") or []
            if figs:
                print("    --- figures extracted from the release body "
                      "(BEST EFFORT - verify against the source line) ---")
                for f in figs:
                    prev = ("%s" % f["previous"]) if f["previous"] is not None else "-"
                    extra = ("  margin %s%%" % f["pct_in_brackets"]) if f.get("pct_in_brackets") is not None else ""
                    period = (" [%s]" % f["period"]) if f.get("period") else ""
                    print("      %-30.30s%-16.16s %14s  prior %12s  %-9s%s"
                          % (f["label"], period, f["current"], prev,
                             f["unit"], extra))
                    print("        source: %s" % f["source_line"][:110])
            else:
                print("    No figures matched the extractor - read --text instead.")
        if args.text and i.get("text"):
            print("    --- release body ---")
            for line in i["text"].splitlines():
                print("    %s" % line)
        print()

    print("Read the attached PDF for reported figures; treat the release body as")
    print("management framing, not as verified fact.")


if __name__ == "__main__":
    main()
