#!/usr/bin/env python3
"""Releases for Swedish issuers that distribute through Cision rather than MFN.

MFN's company feed is empty for several Swedish large caps - Sandvik, Atlas
Copco, Hexagon and AB Volvo among them - because they publish through Cision.
They still appear in MFN search, which makes the gap easy to miss. This closes
it using the issuer's own distributor.

ONE IMPORTANT DIFFERENCE FROM MFN. Cision exposes no regulatory flag in the
feed, and a Cision newsroom mixes MAR-regulated disclosure with ordinary
marketing PR - /se/volvo carries Volvo Trucks product releases alongside
financial reports. MFN's `:regulatory` tag has no equivalent here, so the
classification below is heuristic. Treat a release as regulated only when its
content says so, or confirm against Finansinspektionen's storage mechanism.

Usage:
    python cision_news.py --search "Sandvik"          # resolve the slug
    python cision_news.py sandvik                      # recent releases
    python cision_news.py sandvik --reports            # likely reports only
    python cision_news.py sandvik --pages 3            # 24 items per page
    python cision_news.py sandvik --reports --pdf ./r  # save attachments
    python cision_news.py atlas-copco --json

Free, no API key. Source: https://news.cision.com
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

RESOLVER = "https://news.cision.com/se/_ta/Newsroom"
FEED = "https://news.cision.com/{lang}{slug}/ListItems"
UA = "Mozilla/5.0 (compatible; investment-analyst-skill/1.0)"

# Heuristics, not tags. Cision gives no regulatory marker.
REPORT_WORDS = ("delårsrapport", "bokslutskommuniké", "årsredovisning",
                "kvartalsrapport", "halvårsrapport", "interim report",
                "year-end report", "annual report", "quarterly report",
                "half-year report", "q1", "q2", "q3", "q4")
REGULATORY_HINTS = ("flaggning", "insynshandel", "kallelse till", "årsstämma",
                    "extra bolagsstämma", "återköp av egna aktier",
                    "vinstvarning", "nyemission", "företrädesemission",
                    "beslutar om", "offentliggör", "majority shareholder",
                    "notice of", "annual general meeting", "share buy-back")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def http_get(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        raise SystemExit("DATA NOT AVAILABLE: Cision unreachable (%s)" % e)


def resolve(name):
    raw = http_get(RESOLVER + "?" + urllib.parse.urlencode({"q": name}))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out = []
    for row in data or []:
        model = row.get("FilterModel") or {}
        slug = model.get("Key")
        if not slug:
            continue
        label = re.sub(r"<[^>]+>", "", model.get("DisplayText") or "").strip()
        out.append({"slug": slug, "name": label or slug})
    return out


def classify(title):
    low = (title or "").lower()
    is_report = any(w in low for w in REPORT_WORDS)
    looks_regulatory = is_report or any(w in low for w in REGULATORY_HINTS)
    return is_report, looks_regulatory


def releases(slug, pages=1, english=False):
    base = FEED.format(lang="" if english else "se/", slug=urllib.parse.quote(slug))
    out = []
    for page in range(1, pages + 1):
        url = base + "?format=rss" + ("&pageIx=%d" % page if page > 1 else "")
        raw = http_get(url)
        if b"<!DOCTYPE" in raw[:2048] or b"<!ENTITY" in raw[:2048]:
            raise SystemExit("DATA NOT AVAILABLE: Cision returned a DTD; refusing to parse.")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            break
        items = list(root.iter("item"))
        if not items:
            break
        for item in items:
            title = item.findtext("title") or ""
            is_report, regulatory = classify(title)
            out.append({"date": item.findtext("pubDate") or "",
                        "title": title.strip(),
                        "url": (item.findtext("link") or "").strip(),
                        "description": re.sub(r"<[^>]+>", "",
                                              item.findtext("description") or "").strip(),
                        "is_report": is_report,
                        "looks_regulatory": regulatory})
    return out


def find_pdfs(release_url):
    """Attachments live at mb.cision.com/Main/<customer>/<release>/<file>.pdf and
    are linked from the release page."""
    try:
        page = http_get(release_url, timeout=45).decode("utf-8", errors="replace")
    except SystemExit:
        return []
    return sorted(set(re.findall(r'https://mb\.cision\.com/Main/\d+/\d+/\d+\.pdf', page)))


def save(url, directory):
    os.makedirs(directory, exist_ok=True)
    name = url.rstrip("/").split("/")[-1]
    path = os.path.join(directory, name)
    try:
        data = http_get(url, timeout=180)
    except SystemExit as e:
        return "FAILED (%s) %s" % (e, url)
    if not data.startswith(b"%PDF"):
        return "SKIPPED (not a PDF) %s" % url
    with open(path, "wb") as f:
        f.write(data)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug", nargs="?", help="Cision newsroom slug, e.g. sandvik")
    ap.add_argument("--search", help="resolve a company name to its slug")
    ap.add_argument("--pages", type=int, default=1, help="24 items per page")
    ap.add_argument("--reports", action="store_true", help="likely financial reports only")
    ap.add_argument("--regulatory", action="store_true",
                    help="likely regulated disclosure only (heuristic)")
    ap.add_argument("--english", action="store_true", help="the English newsroom")
    ap.add_argument("--pdf", metavar="DIR", help="download attached PDFs")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    if args.search:
        hits = resolve(args.search)
        if not hits:
            print("DATA NOT AVAILABLE: no Cision newsroom matched %r." % args.search)
            return
        print("Cision newsrooms matching %r:" % args.search)
        for h in hits[:10]:
            print("  %-28s %s" % (h["slug"], h["name"][:60]))
        print()
        print("Then: python cision_news.py <slug>")
        return

    if not args.slug:
        ap.error("give a slug, or --search NAME to find one")

    items = releases(args.slug, args.pages, args.english)
    if not items:
        print("DATA NOT AVAILABLE: no releases for Cision slug %r." % args.slug)
        print("Run --search to confirm the slug. Companies on MFN are not here —")
        print("use mfn_news.py for those.")
        return

    if args.reports:
        items = [i for i in items if i["is_report"]]
    elif args.regulatory:
        items = [i for i in items if i["looks_regulatory"]]
    items = items[:args.limit]

    if args.as_json:
        print(json.dumps({"slug": args.slug, "count": len(items),
                          "source": "Cision",
                          "retrieved_utc": datetime.datetime.now(
                              datetime.timezone.utc).isoformat(),
                          "items": items}, indent=2, ensure_ascii=False))
        return

    if not items:
        print("DATA NOT AVAILABLE: nothing matched the filter. Cision has no")
        print("regulatory tag, so the filter is keyword-based — try without it.")
        return

    print("%s — Cision newsroom — %d releases — retrieved %s"
          % (args.slug, len(items),
             datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")))
    print()
    for i in items:
        marks = []
        if i["is_report"]:
            marks.append("REPORT")
        elif i["looks_regulatory"]:
            marks.append("LIKELY REGULATORY")
        flag = ("  [" + "/".join(marks) + "]") if marks else ""
        print("%-32s %s%s" % (i["date"][:31], i["title"][:70], flag))
        print("    %s" % i["url"])
        if args.pdf:
            for url in find_pdfs(i["url"]):
                print("    SAVED: %s" % save(url, args.pdf))
        print()

    print("Cision publishes no regulatory flag — the labels above are keyword")
    print("heuristics, not MFN's `:regulatory` tag. A Cision newsroom also mixes")
    print("marketing PR with financial disclosure. Confirm before citing a release")
    print("as regulated information.")


if __name__ == "__main__":
    main()
