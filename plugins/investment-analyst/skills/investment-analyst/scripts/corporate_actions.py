#!/usr/bin/env python3
"""Corporate-action awareness for Swedish and Nordic issuers.

WHY THIS EXISTS

Every other script in this toolkit reads a number and hands it over. Corporate
actions are what silently make those numbers wrong:

  * A 10:1 split multiplies the share count by ten overnight. Any per-share
    figure carried across that date - EPS, dividend per share, book value per
    share - is out by an order of magnitude, and nothing in the data says so.
  * A rights issue (foretradesemission) or a directed issue (riktad emission)
    raises the share count without raising last year's profit. A P/E built on
    the old count looks cheap for entirely mechanical reasons.
  * A buyback plus cancellation cuts the count. Compare a pre-cancellation EPS
    with a post-cancellation price and the multiple is nonsense.
  * A spin-off or a large disposal removes earnings that the trailing figures
    still contain.

None of this is visible in a price feed or a share-count snapshot. It is
visible in the disclosure record, and in Sweden that record is unusually good:
Nasdaq's CNS feed carries a legally mandated category, "Total number of voting
rights and capital", in which every issuer must publish its new share count in
the month a change is registered. That category is the authoritative dilution
log for the Stockholm market and it is free and keyless.

WHAT THIS SCRIPT DOES

  default   classify recent announcements into corporate-action types
  --shares  the share-count disclosure record, with the change at each step
  --splits  split and reverse-split events, from the exchange's own notices,
            cross-checked against the share count and the price series

SOURCES, and what each is actually good for (all verified 2026-08-31)

  1. Nasdaq CNS  api.news.eu.nasdaq.com  - the backbone. Categorised, complete
     back to the 2000s, covers every Nasdaq Nordic issuer including the large
     caps MFN misses. Exchange notices (globalGroup=exchangeNotice) carry the
     split notices with the exact ratio and ISIN change.
  2. MFN.se via mfn_news.py - the cleanest CLASSIFICATION available free. Its
     `sub:ca:*` tag tree is applied by a human newsroom. But a company feed is
     capped at ~30 recent items, so MFN gives recency and labels, not history.
  3. Cision via cision_news.py (--cision) - Sandvik, Atlas Copco, Hexagon and
     AB Volvo publish there. It has NO regulatory or corporate-action tag, so
     anything from it is headline-keyword guesswork. Off by default.
  4. Nasdaq reference data via nordic_shares.py - current share count per
     class, and the daily price series used for the split cross-check.

THREE THINGS THIS CANNOT DO - read these before trusting a clean result

  * A "no corporate actions found" result is NOT proof there were none. It
    means nothing matched in the window and sources queried. Say so.
  * Split detection from a PRICE DISCONTINUITY DOES NOT WORK against Nasdaq
    Nordic's chart endpoint. Measured on four confirmed Stockholm splits -
    Mycronic 2:1 (Jun 2025), Investor A/B 4:1 (May 2021), Bambuser 1:30 reverse
    (Dec 2025) and Nobia 1:10 reverse (May 2026) - the series is retroactively
    BACK-ADJUSTED and every one of them left NO jump whatsoever. Splits here
    therefore come from the exchange notice, which is authoritative; the price
    check is run only to tell you WHICH convention the series you are holding
    follows. See --splits output.
  * Unlisted share classes never appear in any of this. NIBE and Fenix Outdoor
    have unlisted A shares. The share-count DISCLOSURE covers them; the Nasdaq
    reference count does not. Where the two disagree, the disclosure wins.

Usage:
    python corporate_actions.py "Sandvik"
    python corporate_actions.py "Evolution" --shares
    python corporate_actions.py "Mycronic" --splits
    python corporate_actions.py "KebNi" --limit 40
    python corporate_actions.py "Atlas Copco" --json
    python corporate_actions.py "Sandvik" --cision      # add untagged Cision PR
    python corporate_actions.py --resolve "volvo"       # exact CNS company name

Python 3 stdlib only. Free, keyless, no scraping beyond published JSON/RSS
endpoints the exchanges serve to their own web front ends.
"""
import argparse
import datetime
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Sibling scripts are importable helpers. Import defensively: a parallel agent
# may be mid-edit on one of them, and a broken sibling must degrade this script
# to fewer sources rather than kill it.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import mfn_news
except Exception:                                        # pragma: no cover
    mfn_news = None
try:
    import cision_news
except Exception:                                        # pragma: no cover
    cision_news = None
try:
    import nordic_shares
except Exception:                                        # pragma: no cover
    nordic_shares = None
try:
    import finfact
except Exception:                                        # pragma: no cover
    finfact = None

CNS = "https://api.news.eu.nasdaq.com/news/query.action"

# The default urllib User-Agent is blocklisted by Nasdaq's edge; the request
# does not error, it hangs. Any browser-shaped UA works.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Refuse to buffer an unbounded response. Nothing legitimate here is this big.
MAX_BYTES = 12 * 1024 * 1024


# ---------------------------------------------------------------------------
# Nasdaq CNS transport
# ---------------------------------------------------------------------------

def _http(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(MAX_BYTES + 1)


def cns_query(**params):
    """One page of the Nasdaq CNS feed.

    The endpoint answers JSONP only - `type=json` still wraps the payload in
    the callback name - so the wrapper is stripped by hand. `limit` and `start`
    must be numeric strings; the documented empty values return HTTP 400.
    """
    p = {"type": "json", "callback": "cb", "countResults": "true",
         "displayLanguage": "en", "language": "en", "timeZone": "CET",
         "dateMask": "yyyy-MM-dd HH:mm", "dir": "DESC",
         "limit": "50", "start": "0"}
    p.update({k: v for k, v in params.items() if v not in (None, "")})
    url = CNS + "?" + urllib.parse.urlencode(p)
    try:
        raw = _http(url).decode("utf-8", "replace")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            OSError) as e:
        raise SystemExit("DATA NOT AVAILABLE: Nasdaq CNS unreachable (%s)" % e)
    if len(raw) > MAX_BYTES:
        raise SystemExit("DATA NOT AVAILABLE: Nasdaq CNS response oversized; refusing.")
    start, end = raw.find("("), raw.rfind(")")
    if start < 0 or end <= start:
        raise SystemExit("DATA NOT AVAILABLE: Nasdaq CNS returned no JSONP payload.")
    try:
        data = json.loads(raw[start + 1:end])
    except json.JSONDecodeError:
        raise SystemExit("DATA NOT AVAILABLE: Nasdaq CNS payload was not JSON.")
    items = ((data.get("results") or {}).get("item")) or []
    if isinstance(items, dict):          # single-item responses are not a list
        items = [items]
    return items, data.get("count")


def cns_pages(pages=1, per_page=200, **params):
    out = []
    for n in range(pages):
        items, _ = cns_query(limit=str(per_page), start=str(n * per_page), **params)
        if not items:
            break
        out.extend(items)
        if len(items) < per_page:
            break
    return out


_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"(?is)<(script|style)\b.*?</\1>")


def cns_body(message_url):
    """Plain text of a CNS release or exchange notice.

    The view page is HTML, so tags are replaced with newlines rather than
    stripped: in the exchange notices the label and its value sit in separate
    cells, and collapsing them onto one line destroys the "Terms: Split: 10:1"
    structure the split parser depends on.
    """
    try:
        raw = _http(message_url, timeout=45).decode("utf-8", "replace")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return ""
    raw = _SCRIPT.sub(" ", raw)
    text = html.unescape(_TAG.sub("\n", raw))
    return "\n".join(l.strip() for l in text.splitlines() if l.strip())


# ---------------------------------------------------------------------------
# Company name resolution
# ---------------------------------------------------------------------------
#
# CNS `company=` is an EXACT match on the registered name as Nasdaq spells it:
# "Sandvik AB" works, "Sandvik" and "Sandvik AB (publ)" both return zero rows
# and look identical to "this company has never disclosed anything". Since no
# company-list endpoint is exposed, resolution goes through the free-text
# index and collects the distinct company names it returns.

_LEGAL_SUFFIX = re.compile(
    r"(?i)\s*\b(ab|abp|a/s|asa|hf\.?|oyj|oy|plc|publ|se|ltd|inc|corp|nv|"
    r"holding|group|\(publ\))\b\.?", re.UNICODE)


def _norm(name):
    # Parentheses are stripped BEFORE the legal-suffix regex runs, not after.
    # That regex's "publ" alternative matches the bare word inside "(publ)"
    # and consumes it, but leaves the parentheses themselves behind as orphan
    # punctuation ("scandinavian enviro systems ( )"), which then never equals
    # the plain query "scandinavian enviro systems". Fixed 2026-08-31: this
    # silently broke collect_mfn's exact-match slug selection for every issuer
    # whose MFN-recorded name carries "(publ)" (the common case) - ties then
    # went to whichever OTHER unrelated hit had the shortest slug (e.g. a
    # research publisher like "redeye"), attributing that unrelated company's
    # press releases - including its own rights-issue and buyback headlines -
    # to the company actually being queried.
    n = (name or "").lower().replace(",", " ").replace("(", " ").replace(")", " ")
    n = _LEGAL_SUFFIX.sub(" ", n)
    return re.sub(r"\s+", " ", n).strip()


def resolve_company(name, probe=120):
    """Distinct CNS company names matching `name`, best first."""
    items, _ = cns_query(limit=str(probe), freeText=name)
    needle = _norm(name)
    counts, first_seen = {}, {}
    for it in items:
        comp = it.get("company")
        if not comp:
            continue
        n = _norm(comp)
        if needle and (needle in n or n in needle):
            counts[comp] = counts.get(comp, 0) + 1
            first_seen.setdefault(comp, it.get("market"))
    hits = [{"company": c, "announcements_in_probe": counts[c],
             "market": first_seen.get(c)} for c in counts]
    # Prefer an exact normalised match, then whichever name the free-text index
    # returned most often - that is the issuer actually publishing under it.
    hits.sort(key=lambda h: (_norm(h["company"]) != needle,
                             -h["announcements_in_probe"]))
    if hits:
        return hits

    # The free-text index ranks by relevance across ALL issuers, so a common
    # word drowns its own issuer: "Investor" returns a hundred releases from
    # other companies before one from Investor AB. Fall back to probing the
    # exact-match `company=` parameter with the usual Nordic legal suffixes.
    for suffix in ("", " AB", " AB (publ)", ", AB", " Oyj", " Oy", " A/S",
                   " ASA", " hf.", " plc", " Group AB", " Holding AB"):
        candidate = name.strip() + suffix
        items, count = cns_query(limit="1", company=candidate)
        if items:
            return [{"company": items[0].get("company") or candidate,
                     "announcements_in_probe": count or len(items),
                     "market": items[0].get("market")}]
    return []


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
#
# Two independent signals are combined:
#
#   1. MFN's `sub:ca:*` tags, where available. This is a human-curated tag tree
#      and it is the cleanest free classification of Nordic corporate actions.
#      Verified over a 6,000-release sample of the MFN global feed, the FULL
#      taxonomy in use is:
#          sub:ca                     parent, on every corporate action
#          sub:ca:shares              anything changing the share register
#          sub:ca:shares:issuance     new shares issued
#          sub:ca:shares:repurchase   buybacks
#          sub:ca:ma                  mergers, acquisitions, public offers
#          sub:ca:prospectus          prospectus / information document
#          sub:ca:exdate              ex-dividend date
#          sub:ca:aoa                 articles of association changes
#          sub:ca:other               everything else tagged as an action
#          sub:ca:staff               observed, but a mis-tag in every case seen
#      There is NO split tag and NO dividend-decision tag. Splits and dividends
#      must come from elsewhere - which is why the Nasdaq feed is primary here.
#
#   2. Nasdaq's cnsCategory plus headline keywords in English and Swedish.
#      The category alone is too coarse ("Inside information" holds both a
#      directed issue and a profit warning), and the headline alone is too
#      noisy, so category narrows and headline decides.

# Nasdaq cnsCategory values that are corporate actions by definition.
CA_CATEGORIES = {
    "total number of voting rights and capital": "SHARE_COUNT_DISCLOSURE",
    "changes in company's own shares": "BUYBACK",
    "corporate action": "CORPORATE_ACTION_OTHER",
    "changes in the rights attached to the classes of shares or securities":
        "SHARE_CLASS_CHANGE",
    "tender offer": "TENDER_OFFER",
    "prospectus/announcement of prospectus": "PROSPECTUS",
}

# Categories worth scanning with the keyword rules even though most of their
# contents are not corporate actions.
SCAN_CATEGORIES = {
    "inside information", "decisions of general meeting",
    "decisions of extraordinary general meeting", "notice to general meeting",
    "notice to convene extr.general meeting", "company announcement",
    "other information disclosed according to the rules of the exchange",
    "major shareholder announcements", "investor news",
}

# Ordered: the first rule that fires wins. Specific before generic - a
# "reverse split" headline also contains "split", and a cancellation headline
# very often also contains "repurchased shares".
RULES = [
    ("REVERSE_SPLIT", 0, r"reverse\s+split|omv[aä]nd\s+split|"
                         r"sammanl[aä]ggning\s+av\s+aktier"),
    ("SPLIT", 0, r"\b(share|stock)\s+split\b|\baktiesplit\b|\bsplit\s+of\s+"
                 r"(the\s+)?shares?\b|uppdelning\s+av\s+aktier|"
                 r"\bsplit\s+and\s+change\s+of\s+isin\b|f[oö]rdelning\s+av\s+aktier"),
    # A Swedish spin-off is legally a dividend of shares in the subsidiary
    # ("Lex Asea"), so its headlines say "dividend" and "listing of" rather
    # than "spin-off" - Sandvik's separation of Alleima is the canonical case.
    # This rule must therefore sit ahead of both DIVIDEND and PROSPECTUS.
    ("SPINOFF", -1, r"spin[-\s]?off|demerger|utdelning\s+av\s+aktierna?\s+i|"
                    r"distribution\s+of\s+(the\s+)?shares\s+in|lex\s+asea|"
                    r"\bavknoppning\b|\bs[aä]rnotering\b|"
                    r"separate\s+listing\s+of|separat\s+notering\s+av|"
                    r"(prospectus|record\s+date).{0,40}\blisting\s+of\b|"
                    r"distribution\s+and\s+listing\s+of|"
                    r"split\s+the\s+group|dela\s+upp\s+koncernen|"
                    r"internal\s+separation\s+of|intern\s+separation\s+av"),
    ("CANCELLATION", +1, r"cancellation\s+of\s+.{0,30}shares?|cancels?\s+"
                         r"(repurchased|treasury|own)?\s*shares?|indragning\s+av\s+aktier|"
                         r"reduction\s+of\s+(the\s+)?share\s+capital|"
                         r"minskning\s+av\s+aktiekapitalet|makulering\s+av\s+aktier"),
    ("REDEMPTION", +1, r"redemption\s+(procedure|programme|program|of\s+shares)|"
                       r"(share|mandatory|automatic)\s+redemption|"
                       r"inl[oö]senprogram|inl[oö]sen\s+av\s+aktier|"
                       r"inl[oö]senf[oö]rfarande|automatisk\s+inl[oö]sen"),
    ("RIGHTS_ISSUE", -1, r"rights\s+issue|f[oö]retr[aä]desemission|"
                         r"with\s+preferential\s+rights?|med\s+f[oö]retr[aä]desr[aä]tt|"
                         r"subscription\s+rights?|teckningsr[aä]tt|"
                         r"paid\s+subscription\s+shares?|betald\s+tecknad\s+aktie|\bBTA\b"),
    ("DIRECTED_ISSUE", -1, r"directed\s+(new\s+)?(share\s+)?issue|riktad\s+"
                           r"(ny)?emission|riktad\s+nyemission|private\s+placement|"
                           r"directed\s+issue\s+of\s+shares|"
                           r"issue\s+directed\s+to"),
    ("SET_OFF_OR_INKIND_ISSUE", -1, r"set[-\s]?off\s+issue|kvittningsemission|"
                                    r"apportemission|non[-\s]cash\s+issue|"
                                    r"issue\s+in\s+kind"),
    ("WARRANT_OR_INCENTIVE_ISSUE", -1,
     r"warrants?\b|teckningsoption|incentive\s+program|incitamentsprogram|"
     r"exercise\s+of\s+(warrants|options|subscription)|utnyttjande\s+av\s+"
     r"teckningsoption|option\s+programme|share\s+saving"),
    ("CONVERTIBLE_CONVERSION", -1, r"conversion\s+of\s+convertibles?|"
                                   r"konvertering\s+av\s+konvertibler|"
                                   r"convertible\s+(bond|loan)s?\s+converted"),
    ("SHARE_CLASS_CONVERSION", 0, r"conversion\s+of\s+shares|omvandling\s+av\s+"
                                  r"aktier|conversion\s+of\s+(class\s+)?a\s+"
                                  r"(shares?\s+)?into|omst[aä]mpling"),
    ("NEW_SHARE_CLASS", -1, r"new\s+(class|series)\s+of\s+shares|nytt\s+aktieslag|"
                            r"issue\s+of\s+class\s+[a-d]\s+shares|"
                            r"changes\s+in\s+the\s+rights\s+attached"),
    ("BUYBACK", +1, r"buy[-\s]?backs?|repurchases?|acquisitions?\s+of\s+own\s+"
                    r"shares|[aå]terk[oö]p|transactions\s+in\s+own\s+shares|"
                    r"own\s+shares\s+(purchase|transaction)|share\s+buyback"),
    ("SHARE_COUNT_DISCLOSURE", 0,
     r"(change|changes)\s+in\s+(the\s+)?number\s+of\s+shares(\s+and\s+votes)?|"
     r"total\s+number\s+of\s+(shares|voting\s+rights)|new\s+number\s+of\s+"
     r"(shares|votes)|antal(et)?\s+aktier\s+och\s+r[oö]ster|"
     r"number\s+of\s+shares\s+and\s+votes"),
    ("TENDER_OFFER", 0, r"tender\s+offer|public\s+(cash\s+)?offer|"
                        r"uppk[oö]pserbjudande|offer\s+to\s+the\s+shareholders|"
                        r"recommended\s+(cash\s+)?offer|erbjudande\s+till\s+"
                        r"aktie[aä]garna|\bmerger\b|\bfusion\b|budpliktsbud"),
    # Someone buying the ISSUER's shares is an ownership change, not the issuer
    # acquiring a business. Without this rule "Salenia becomes the largest
    # shareholder in KebNi through acquisition of shares" reads as KebNi making
    # an acquisition - the opposite of what happened, and it changes no share
    # count at all.
    ("MAJOR_SHAREHOLDER_CHANGE", 0,
     r"largest\s+shareholder|major\s+shareholding|shareholder\s+announcement|"
     r"\bflaggning|flaggningsmeddelande|threshold\s+(exceeded|passed)|"
     r"st[oö]rste\s+[aä]gare|st[oö]rsta\s+aktie[aä]gare|"
     r"passes?\s+the\s+.{0,20}threshold"),
    ("ACQUISITION", 0, r"acquisitions?\s+of\s+(?!own\s+shares)|acquires?\b|"
                       r"to\s+acquire\b|f[oö]rv[aä]rvar|f[oö]rv[aä]rv\s+av|"
                       r"completes\s+the\s+acquisition|signs?\s+agreement\s+to\s+acquire"),
    ("DISPOSAL", 0, r"divest(s|ment|ing)?|disposal\s+of|avyttr(ar|ing|at)|"
                    r"f[oö]rs[aä]ljning\s+av\s+(dotter|verksamhet|aktier)|"
                    r"sells\s+(its|the)\b|s[aä]ljer\s+"),
    ("DIVIDEND", 0, r"\bdividend\b|\butdelning\b|ex[-\s]dividend|"
                    r"avst[aä]mningsdag|record\s+date\s+for\s+(the\s+)?dividend|"
                    r"extra\s+utdelning"),
    ("SHARE_ISSUE_OTHER", -1, r"new\s+share\s+issue|nyemission|issue\s+of\s+"
                              r"(new\s+)?shares|emission\s+av\s+aktier|"
                              r"capital\s+raise|kapitalanskaffning"),
    ("PROSPECTUS", 0, r"prospectus|prospekt|information\s+document|"
                      r"informationsdokument|offering\s+circular"),
]
COMPILED = [(t, d, re.compile(p, re.I | re.U)) for t, d, p in RULES]

# How each type moves the share count. Printed so the reader never has to
# remember which way an action cuts.
DILUTION_NOTE = {
    -1: "DILUTIVE - share count rises, per-share history breaks",
    +1: "ACCRETIVE - share count falls, per-share history breaks",
    0: "",
}

# Types where the per-share series before and after the event are simply not
# comparable without a manual restatement.
BREAKS_PER_SHARE = {"SPLIT", "REVERSE_SPLIT", "RIGHTS_ISSUE", "DIRECTED_ISSUE",
                    "SET_OFF_OR_INKIND_ISSUE", "CANCELLATION", "REDEMPTION",
                    "SPINOFF", "SHARE_ISSUE_OTHER", "CONVERTIBLE_CONVERSION",
                    "WARRANT_OR_INCENTIVE_ISSUE", "NEW_SHARE_CLASS"}

MFN_TAG_TYPE = [
    ("sub:ca:shares:repurchase", "BUYBACK"),
    ("sub:ca:shares:issuance", "SHARE_ISSUE_OTHER"),
    ("sub:ca:ma", "TENDER_OFFER"),
    ("sub:ca:exdate", "DIVIDEND"),
    ("sub:ca:prospectus", "PROSPECTUS"),
    ("sub:ca:aoa", "ARTICLES_OF_ASSOCIATION"),
    ("sub:ca:shares", "SHARE_REGISTER_CHANGE"),
    ("sub:ca:other", "CORPORATE_ACTION_OTHER"),
    ("sub:ca", "CORPORATE_ACTION_OTHER"),
]


def classify(headline, category=None, tags=None):
    """Return (type, dilution_sign, basis) or (None, 0, None) if not an action.

    `basis` names what decided it, so a reader can see whether the label came
    from a curated tag, a regulated category, or a keyword guess.
    """
    text = headline or ""
    cat = (category or "").strip().lower()

    # 1. Headline keywords first, even when a tag exists: the tag tree has no
    #    split, cancellation or rights-issue node, so the headline carries
    #    strictly more information where it fires.
    for typ, sign, rx in COMPILED:
        if rx.search(text):
            basis = "headline keyword"
            if cat in CA_CATEGORIES:
                basis = "headline keyword + Nasdaq category"
            return typ, sign, basis

    # 2. MFN's curated tag.
    for tag, typ in (MFN_TAG_TYPE if tags else []):
        if tag in tags:
            return typ, (-1 if typ == "SHARE_ISSUE_OTHER" else
                         +1 if typ == "BUYBACK" else 0), "MFN tag %s" % tag

    # 3. The Nasdaq category on its own.
    if cat in CA_CATEGORIES:
        typ = CA_CATEGORIES[cat]
        return typ, (+1 if typ == "BUYBACK" else 0), "Nasdaq category"

    return None, 0, None


# ---------------------------------------------------------------------------
# Number extraction from share-count disclosures
# ---------------------------------------------------------------------------
#
# Nordic releases group thousands with a space, a non-breaking space, a comma
# or a full stop depending on language and house style. Only groups of exactly
# three digits are treated as grouped, which keeps "1,5 miljoner" (a Swedish
# decimal) from being read as 15.

# Wrapped in a non-capturing group because this fragment is spliced INTO
# larger patterns: a bare top-level alternation would cut those in half and
# leave group(1) unmatched.
_NUM = r"(?:\d{1,3}(?:[\s\u00a0\u202f\u2009.,]\d{3})+|\d{6,})"
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Cue-anchored patterns, not "a big number in a sentence about shares". The
# loose version reads "a total of 338,000 shares ... have been cancelled" as
# Evolution's TOTAL count - off by a factor of 500 and entirely plausible on
# the page. The anchor has to be the phrase that actually means "new total".
TOTAL_PATTERNS = [
    # "there are" is not always followed directly by the number - AB Volvo's
    # monthly template reads "there are a total of 2,128,420,220 shares", and
    # without the optional "a total of" this left EVERY Volvo share-count
    # disclosure unparsed (found 2026-08-31 auditing --shares against Volvo,
    # which has filed this release monthly since at least 2011).
    re.compile(r"(?:amounts?\s+to|uppg[a\u00e5]r\s+till|now\s+amounts\s+to|"
               r"is\s+now|there\s+are(?:\s+a\s+total\s+of)?|new\s+total\s+of)\s*("
               + _NUM + r")", re.I),
    re.compile(r"(" + _NUM + r")\s+(?:shares?\s+and\s+votes?|"
               r"aktier\s+och\s+r[o\u00f6]ster)", re.I),
    re.compile(r"total\s+number\s+of\s+(?:shares?|votes?)[^.]{0,80}?"
               r"(?:is|was|to|:)\s*(" + _NUM + r")", re.I),
    # A split release states the jump directly: "increases from A shares to B".
    re.compile(r"(?:increase[sd]?|decrease[sd]?|chang(?:es|ed))\s+from\s+"
               + _NUM + r"\s+(?:shares?|aktier)\s+to\s+(" + _NUM + r")", re.I),
]
DELTA_PATTERNS = [
    re.compile(r"(?:a\s+total\s+of|cancellation\s+of|cancelled|indragning\s+av|"
               r"issued\s+a\s+total\s+of|has\s+issued|increased\s+by|"
               r"[o\u00f6]kat\s+med|minskat\s+med|decreased\s+by|reduced\s+by|"
               r"subscribed\s+for|tecknat)\s*(" + _NUM + r")", re.I),
    re.compile(r"(" + _NUM + r")\s+(?:new\s+shares?|nya\s+aktier|"
               r"shares?\s+(?:have\s+been\s+|were\s+|has\s+been\s+)?"
               r"(?:cancelled|repurchased|issued|subscribed))", re.I),
]
# "SEK 55,000,000" in a directed-issue release is money, not shares.
MONEY_BEFORE = re.compile(r"(?i)(SEK|EUR|NOK|DKK|USD|MSEK|MEUR|kr|kronor)\s*$")
MONEY_AFTER = re.compile(r"(?i)^\s*(SEK|EUR|NOK|DKK|USD|kronor|kr\b)")


def _to_int(raw):
    cleaned = raw
    for ch in (" ", "\u00a0", "\u202f", "\u2009", ",", "."):
        cleaned = cleaned.replace(ch, "")
    try:
        return int(cleaned)
    except ValueError:
        return None


def _reflow(text):
    """Undo the hard line wrapping in older CNS releases.

    Releases before roughly 2021 arrive wrapped at ~70 characters mid-sentence,
    which splits "the number of shares and votes ... amounts to 181,284,725"
    across three lines and defeats any sentence-level match. A blank line, or a
    line already ending in sentence punctuation, stays a break; the rest are
    joined back up.
    """
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            out.append("")
        elif out and out[-1] and not re.search(r"[.!?:]$", out[-1]):
            out[-1] += " " + line
        else:
            out.append(line)
    return " ".join(out)


def _scan(patterns, sent, exclude=()):
    for rx in patterns:
        for m in rx.finditer(sent):
            span = m.span(1)
            if MONEY_BEFORE.search(sent[max(0, span[0] - 10):span[0]]):
                continue
            if MONEY_AFTER.match(sent[span[1]:span[1] + 8]):
                continue
            val = _to_int(m.group(1))
            if val is None or val < 1000 or val in exclude:
                continue
            return val, sent[:300]
    return None, None


# A dual-class issuer discloses PER CLASS: "there are 3,357,576,384 series A
# shares and 1,560,876,032 series B shares". Reading only the first number
# understates Atlas Copco by 1.56 billion shares - a 32% error in market cap
# with a correct-looking citation attached. Enumerated classes must be summed.
CLASS_SHARES = re.compile(
    r"(" + _NUM + r")\s+(?:series|class)\s+([A-Za-z])\b\s*(?:shares?)?|"
    r"(" + _NUM + r")\s+aktier\s+av\s+serie\s+([A-Za-z])\b", re.I)

# A sentence describing shares MOVING from one class to another is not a
# breakdown of the total register - it is the same shares counted once as the
# class they left and once as the class they entered. AB Volvo's monthly
# "New number of votes" release reads (verbatim, 2026-03-31): "...due to the
# CONVERSION of a total of 745,007 Series A shares to a total of 745,007
# Series B shares." Before this fix _class_sum matched that sentence FIRST -
# ahead of the real total two sentences later ("there are a total of
# 2,033,452,084 registered shares... 441,543,462 Series A... 1,591,908,622
# Series B") - and returned 1,490,014 as Volvo's total share count: three
# orders of magnitude wrong, with a plausible by_class breakdown attached and
# no error surfaced anywhere. Found auditing --shares 2026-08-31.
# "\bconver" (not "\bconvert") is deliberate: "conversion"/"conversions" share
# only the first six letters with "convert" ("convers-" vs "convert-"), so
# anchoring on the full word "convert" missed every sentence that actually
# says "conversion" - which is the word Volvo's own release uses - and let
# the bug above straight through on first patch. Verified against the live
# release text before trusting this.
_CONVERSION_SENTENCE = re.compile(r"(?i)\bconver|omvandl")


def _class_sum(sent):
    """Total across enumerated share classes, or None if fewer than two."""
    if _CONVERSION_SENTENCE.search(sent):
        return None, None
    per_class = {}
    for m in CLASS_SHARES.finditer(sent):
        num = m.group(1) or m.group(3)
        cls = (m.group(2) or m.group(4) or "").upper()
        val = _to_int(num)
        if val is None or val < 1000 or not cls:
            continue
        # First mention of a class wins: a later restatement in the same
        # sentence ("of which half is the result of the split") is not a
        # separate holding.
        per_class.setdefault(cls, val)
    if len(per_class) < 2:
        return None, None
    return sum(per_class.values()), per_class


def parse_share_counts(text):
    """Best-effort {total, delta, total_sentence, delta_sentence} from a body.

    The sentence each number came from is always returned. A silently wrong
    share count is the most damaging error this toolkit can make, so the raw
    evidence travels with the figure and the caller is expected to read it.
    """
    out = {"total": None, "total_sentence": None,
           "delta": None, "delta_sentence": None, "by_class": None}
    if not text:
        return out
    for s in SENT_SPLIT.split(_reflow(text)):
        s = s.strip()
        if not s or len(s) > 800:
            continue
        if out["total"] is None:
            summed, per_class = _class_sum(s)
            if summed is not None:
                out["total"], out["total_sentence"] = summed, s[:300]
                out["by_class"] = per_class
        if out["total"] is None:
            val, src = _scan(TOTAL_PATTERNS, s)
            if val is not None:
                out["total"], out["total_sentence"] = val, src
        if out["delta"] is None:
            val, src = _scan(DELTA_PATTERNS, s,
                             (out["total"],) if out["total"] else ())
            if val is not None:
                out["delta"], out["delta_sentence"] = val, src
        if out["total"] is not None and out["delta"] is not None:
            break
    return out


# ---------------------------------------------------------------------------
# Split notices
# ---------------------------------------------------------------------------

SPLIT_HEADLINE = re.compile(
    r"(reverse\s+)?split\s+and\s+change\s+of\s+isin|"
    r"change\s+of\s+isin.*\bsplit\b", re.I)
SPLIT_TERMS = re.compile(
    r"(?i)\b(reverse\s+split|split)\s*:\s*(\d+)\s*:\s*(\d+)")
ISIN_CUR = re.compile(r"(?i)current\s+ISIN\s*:?\s*\n?\s*([A-Z]{2}[A-Z0-9]{9}\d)")
ISIN_NEW = re.compile(r"(?i)new\s+ISIN\s+code\s*:?\s*\n?\s*([A-Z]{2}[A-Z0-9]{9}\d)")
FIRST_DAY = re.compile(
    r"(?i)first\s+day\s+of\s+trading\s+with\s+new\s+ISIN\s+code\s*:?\s*\n?\s*"
    r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})")
SHORT_NAME = re.compile(r"(?i)short\s+name\s*:?\s*\n?\s*([A-Z0-9][A-Z0-9 .\-]{0,14})")

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}


def _parse_notice_date(raw):
    m = re.match(r"([A-Za-z]{3})[a-z]*\s+(\d{1,2}),\s+(\d{4})", raw or "")
    if not m:
        return None
    mon = _MONTHS.get(m.group(1).lower())
    if not mon:
        return None
    return "%04d-%02d-%02d" % (int(m.group(3)), mon, int(m.group(2)))


def parse_split_notice(text):
    """Ratio, ISINs and effective date out of a Nasdaq split exchange notice.

    A dual-class issuer gets one block per class in a single notice, so every
    ratio found is returned rather than only the first.
    """
    terms = []
    for m in SPLIT_TERMS.finditer(text):
        kind = "REVERSE_SPLIT" if "reverse" in m.group(1).lower() else "SPLIT"
        a, b = int(m.group(2)), int(m.group(3))
        # Nasdaq writes a 10:1 split (ten new for one old) and a 1:30 reverse
        # (one new for thirty old). factor = new shares per old share.
        factor = (a / b) if b else None
        terms.append({"kind": kind, "terms": "%d:%d" % (a, b), "factor": factor})
    eff = FIRST_DAY.search(text)
    return {
        "events": terms,
        "short_names": sorted(set(x.strip() for x in SHORT_NAME.findall(text))),
        "isin_current": sorted(set(ISIN_CUR.findall(text))),
        "isin_new": sorted(set(ISIN_NEW.findall(text))),
        "effective_date": _parse_notice_date(eff.group(1)) if eff else None,
    }


def find_split_notices(company_name, probe=200):
    """Exchange notices announcing a split for this issuer.

    The exchange notice is the AUTHORITATIVE split record for Nasdaq Stockholm:
    it is published by Issuer Surveillance, carries the exact ratio, and states
    the ISIN change and the first day of trading on the new ISIN. On an
    exchange notice the `company` field is "NASDAQ OMX Nordic", not the issuer,
    so matching has to be on the headline.

    Three probes rather than one. The free-text index is relevance-ranked
    across all issuers, and for a company with a long notice history the single
    split notice falls outside the first 200 rows: searching "Investor AB"
    alone misses Investor's 4:1 in May 2021 entirely, while "Investor AB split"
    returns it at rank five. Adding the keyword to the query is what makes the
    search reliable for large caps.
    """
    needle = _norm(company_name)
    tokens = [t for t in needle.split() if len(t) > 2] or [needle]
    found = {}
    for query in (company_name, company_name + " split",
                  company_name + " reverse split"):
        items, _ = cns_query(limit=str(probe), globalGroup="exchangeNotice",
                             freeText=query)
        for it in items:
            head = it.get("headline") or ""
            if not SPLIT_HEADLINE.search(head):
                continue
            hnorm = _norm(head)
            if not all(t in hnorm for t in tokens):
                continue
            found[it.get("disclosureId")] = it
    return list(found.values())


def find_split_announcements(company, probe=200):
    """The issuer's OWN split releases, as a secondary confirmation.

    The exchange notice gives the mechanics; the company release gives the
    resolution behind them ("the AGM resolved... one existing share split into
    two"). Where the exchange notice search comes back empty, this is the next
    best evidence - and it is what catches a split too old for the notice
    archive.
    """
    out = {}
    for query in ("split", "sammanlaggning"):
        items, _ = cns_query(limit=str(probe), company=company, freeText=query)
        for it in items:
            typ, _, _ = classify(it.get("headline"), it.get("cnsCategory"))
            if typ in ("SPLIT", "REVERSE_SPLIT"):
                out[it.get("disclosureId")] = {
                    "date": (it.get("published") or "")[:16],
                    "title": (it.get("headline") or "").strip(),
                    "type": typ, "category": it.get("cnsCategory"),
                    "url": it.get("messageUrl")}
    return sorted(out.values(), key=lambda r: r["date"], reverse=True)


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------
#
# spec: actions carry provenance as a FinancialFact, publication_date set from
# the announcement itself - that date is what lets a later caller ask "was
# this known as of as-of-date X" (finfact.is_available_as_of). The fact is
# attached as an extra "fact" key alongside the existing row fields; nothing
# already read from a row changes shape or goes away.

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _action_fact(row, source_id):
    """A FinancialFact for one classified corporate-action row, or None.

    value is None - a classified action is an event, not a measured quantity -
    so this exists purely to carry source, origin group and publication_date
    on something that otherwise has no formal provenance at all.
    """
    if finfact is None or not row.get("type"):
        return None
    date = (row.get("date") or "")[:10]
    if not _ISO_DATE.match(date):
        return None
    try:
        f = finfact.FinancialFact(
            metric="corporate_action:%s" % row["type"], value=None, unit="event",
            source=source_id, period_end=date, publication_date=date,
            source_detail=row.get("title"),
            verification=finfact.Verification.SINGLE_SOURCE)
        return f.to_dict()
    except Exception:
        return None


def collect_nasdaq(company, pages=2, per_page=200):
    rows = []
    for it in cns_pages(pages=pages, per_page=per_page, company=company):
        cat = it.get("cnsCategory") or ""
        typ, sign, basis = classify(it.get("headline"), cat)
        row = {
            "date": (it.get("published") or it.get("releaseTime") or "")[:16],
            "company": it.get("company"),
            "title": (it.get("headline") or "").strip(),
            "type": typ, "dilution": sign, "basis": basis,
            "category": cat, "market": it.get("market"),
            "url": it.get("messageUrl"),
            "source": "Nasdaq CNS",
            "attachments": [a.get("attachmentUrl")
                            for a in (it.get("attachment") or [])],
        }
        row["fact"] = _action_fact(row, "nasdaq_cns")
        rows.append(row)
    return rows


def collect_mfn(slug_or_name, limit=60):
    """MFN releases with the curated sub:ca:* tags attached."""
    if mfn_news is None:
        return [], "mfn_news.py not importable"
    slug = slug_or_name
    try:
        hits = mfn_news.search(slug_or_name, limit=8)
    except SystemExit as e:
        return [], str(e)
    if hits:
        needle = _norm(slug_or_name)
        best = sorted(hits, key=lambda h: (_norm(h["name"] or "") != needle,
                                           len(h["slug"])))
        slug = best[0]["slug"]
    try:
        data = mfn_news.fetch("/a/%s.json" % urllib.parse.quote(slug), limit=limit)
    except SystemExit as e:
        return [], str(e)
    rows = []
    for raw in data.get("items") or []:
        f = mfn_news.flatten(raw)
        tags = f.get("tags") or []
        typ, sign, basis = classify(f.get("title"), None, tags)
        row = {
            "date": (f.get("date") or "")[:16].replace("T", " "),
            "company": f.get("company"), "title": (f.get("title") or "").strip(),
            "type": typ, "dilution": sign, "basis": basis,
            "category": ", ".join(t for t in tags if t.startswith("sub:ca")) or None,
            "market": None, "url": f.get("url"), "source": "MFN (%s)" % slug,
            "mfn_tags": [t for t in tags if t.startswith("sub:ca")],
            "attachments": [a.get("url") for a in (f.get("attachments") or [])],
        }
        row["fact"] = _action_fact(row, "mfn")
        rows.append(row)
    return rows, None


def collect_cision(name, pages=2):
    """Cision, for the large caps that never reach MFN.

    No regulatory or corporate-action tag exists here, so every label produced
    from this source is a keyword guess over a feed that also carries product
    marketing. Opt-in only, and marked as unverified in the output.
    """
    if cision_news is None:
        return [], "cision_news.py not importable"
    try:
        hits = cision_news.resolve(name)
    except SystemExit as e:
        return [], str(e)
    if not hits:
        return [], "no Cision newsroom matched %r" % name
    slug = hits[0]["slug"]
    try:
        items = cision_news.releases(slug, pages=pages)
    except SystemExit as e:
        return [], str(e)
    rows = []
    for i in items:
        typ, sign, basis = classify(i.get("title"))
        # Cision's own date is RFC-822 ("Tue, 07 Jul 2026..."), not ISO, so
        # _action_fact's ISO guard will simply decline to attach a fact here
        # rather than mis-parse it - no publication_date is safer than a wrong
        # one for a source that is already unverified by design.
        row = {
            "date": (i.get("date") or "")[:25], "company": hits[0]["name"],
            "title": (i.get("title") or "").strip(),
            "type": typ, "dilution": sign,
            "basis": (basis + " (UNTAGGED SOURCE)") if basis else None,
            "category": None, "market": None, "url": i.get("url"),
            "source": "Cision (%s)" % slug, "attachments": [],
        }
        row["fact"] = _action_fact(row, "cision")
        rows.append(row)
    return rows, None


def _sort_key(row):
    # Cision dates are RFC-822; Nasdaq and MFN are ISO. Sorting the raw string
    # would interleave them wrongly, so normalise to an ISO prefix first.
    d = row.get("date") or ""
    if re.match(r"^\d{4}-\d{2}-\d{2}", d):
        return d
    m = re.search(r"(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})", d)
    if m:
        mon = _MONTHS.get(m.group(2).lower())
        if mon:
            return "%s-%02d-%02d" % (m.group(3), mon, int(m.group(1)))
    return ""


# An issuer running a buyback programme files a report EVERY WEEK. Evolution
# alone produced 12 of the 14 most recent corporate actions that way, in two
# languages, which buries the one thing that actually mattered - a mandatory
# cash offer. These are collapsed to a single summary line by default.
ROUTINE_BUYBACK = re.compile(
    r"(?i)acquisitions?\s+of\s+own\s+(ordinary\s+)?shares|"
    r"[aå]terk[oö]p\s+av\s+(egna\s+)?aktier|"
    r"(repurchase|buyback|buy-back|share\s+buybacks?)\b.{0,60}"
    r"\b(week|during|period|\d{1,2}\s*[-–]\s*\d{1,2})|"
    r"transactions\s+in\s+own\s+shares|"
    r"(repurchase|buyback)\s+of\s+(own\s+)?shares\s+in\b|"
    r"did\s+not\s+acquire\s+its\s+own\s+shares")


def is_routine(row):
    return (row.get("type") == "BUYBACK"
            and bool(ROUTINE_BUYBACK.search(row.get("title") or "")))


def dedupe(rows):
    """Collapse the same event arriving twice.

    Two distinct duplications happen here. A release can reach both Nasdaq CNS
    and MFN verbatim, which a title key catches. More often the SAME event
    arrives in English on CNS and in Swedish on MFN, where no title key can
    match - so a second pass drops the MFN copy when CNS already carries an
    action of the same type on the same day. Nasdaq is preferred as the
    canonical copy because it is the venue of record and carries the category.
    """
    seen, out = set(), []
    for r in rows:
        key = (_sort_key(r)[:10],
               re.sub(r"[^a-z0-9]", "", (r.get("title") or "").lower())[:60])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)

    nasdaq_day_type = {(_sort_key(r)[:10], r.get("type")) for r in out
                       if (r.get("source") or "").startswith("Nasdaq") and r.get("type")}
    kept, collapsed = [], 0
    for r in out:
        if ((r.get("source") or "").startswith("Nasdaq")
                or not r.get("type")
                or (_sort_key(r)[:10], r["type"]) not in nasdaq_day_type):
            kept.append(r)
        else:
            collapsed += 1
    return kept, collapsed


# ---------------------------------------------------------------------------
# --shares : the dilution record
# ---------------------------------------------------------------------------

SHARE_COUNT_CATEGORY = "Total number of voting rights and capital"

# Ceiling on how many 200-row pages of the mandated category share_history()
# will fetch. An issuer that files monthly - AB Volvo does - produces roughly
# 12 disclosures a year, so 20 pages (4,000 rows) is generous headroom over a
# multi-decade history while still bounding the request count for a runaway
# feed.
SHARE_HISTORY_MAX_PAGES = 20


def _cns_paged_full(max_pages, per_page=200, **params):
    """Page a CNS query until it is exhausted or max_pages is hit.

    Returns (items, hit_ceiling). hit_ceiling is True only when every page up
    to max_pages came back full - i.e. there is very likely more history
    beyond what was fetched, and that must be said rather than silently
    presenting a truncated history as complete.
    """
    out = []
    for n in range(max_pages):
        items, _ = cns_query(limit=str(per_page), start=str(n * per_page), **params)
        if not items:
            return out, False
        out.extend(items)
        if len(items) < per_page:
            return out, False
    return out, True


def share_history(company, fetch_bodies=40):
    """Every share-count disclosure this issuer has made, oldest first.

    Two categories carry them. The mandated one is "Total number of voting
    rights and capital", but issuers whose count changes through a buyback
    programme sometimes file the same disclosure under "Changes in company's
    own shares" - Sinch does exactly that. Querying only the mandated category
    therefore misses real dilution events, so both are swept and the headline
    decides.

    The mandated category used to be fetched as a single 200-row page, which
    silently lost the oldest history for a monthly filer (AB Volvo). It is
    now paged up to SHARE_HISTORY_MAX_PAGES; `paging_ceiling_hit` in the
    return value says whether that ceiling was actually reached, so a
    truncated history is never presented as a complete one.
    """
    rows = {}
    items, paging_ceiling_hit = _cns_paged_full(
        SHARE_HISTORY_MAX_PAGES, 200, company=company,
        cnsCategory=SHARE_COUNT_CATEGORY)
    for it in items:
        rows[it.get("disclosureId")] = it
    for it in cns_pages(pages=2, per_page=200, company=company):
        typ, _, _ = classify(it.get("headline"), it.get("cnsCategory"))
        if typ == "SHARE_COUNT_DISCLOSURE":
            rows[it.get("disclosureId")] = it

    events = []
    for it in rows.values():
        events.append({
            "date": (it.get("published") or "")[:16],
            "title": (it.get("headline") or "").strip(),
            "category": it.get("cnsCategory"),
            "url": it.get("messageUrl"),
            "total_shares": None, "reported_change": None,
            "total_sentence": None, "change_sentence": None, "by_class": None,
        })
    events.sort(key=lambda e: e["date"])

    # Bodies cost one request each. Fetch the most recent ones - that is where
    # a current share count actually matters - and say so when older ones are
    # left unparsed rather than pretending the record is complete.
    to_fetch = events[-fetch_bodies:] if fetch_bodies else []
    for e in to_fetch:
        parsed = parse_share_counts(cns_body(e["url"]))
        e["total_shares"] = parsed["total"]
        e["total_sentence"] = parsed["total_sentence"]
        e["by_class"] = parsed.get("by_class")
        e["reported_change"] = parsed["delta"]
        e["change_sentence"] = parsed["delta_sentence"]

    prev = None
    for e in events:
        e["change_vs_previous"] = None
        e["pct_change"] = None
        if e["total_shares"] and prev:
            e["change_vs_previous"] = e["total_shares"] - prev
            e["pct_change"] = 100.0 * (e["total_shares"] - prev) / prev
        if e["total_shares"]:
            prev = e["total_shares"]

    # spec: share counts carry provenance as a FinancialFact, publication_date
    # set from the disclosure itself. This is what makes point-in-time
    # reasoning possible later - "what was the share count AS OF date X" is
    # only answerable if every count on record knows when it became public.
    if finfact is not None:
        for e in events:
            e["fact"] = None
            if e["total_shares"] is None or not _ISO_DATE.match(e["date"][:10]):
                continue
            try:
                f = finfact.FinancialFact(
                    metric="shares_outstanding", value=e["total_shares"],
                    unit="shares", source="nasdaq_cns", period_end=e["date"][:10],
                    publication_date=e["date"][:10], source_detail=e["title"],
                    freshness_key="shares_outstanding",
                    verification=finfact.Verification.SINGLE_SOURCE)
                e["fact"] = f.to_dict()
            except Exception:
                e["fact"] = None
    return events, len(events) - len(to_fetch), paging_ceiling_hit


# ---------------------------------------------------------------------------
# --splits
# ---------------------------------------------------------------------------

def price_check(company, effective_date, expected_factor):
    """Does the unadjusted price series show the split, or was it back-adjusted?

    THIS IS A DIAGNOSTIC ON THE PRICE SERIES, NOT EVIDENCE ABOUT THE SPLIT.
    Measured against four real Stockholm splits, Nasdaq Nordic's chart endpoint
    back-adjusts, so the honest answer is almost always "no discontinuity" and
    that says nothing about whether the split happened. What it does tell you
    is whether the series in your hands already has the split baked in - which
    is exactly what you need to know before you divide anything by it.
    """
    if nordic_shares is None or not effective_date:
        return {"status": "DATA NOT AVAILABLE",
                "detail": "nordic_shares.py not importable, or no effective date"}
    try:
        hits = nordic_shares.search(company)
    except SystemExit as e:
        return {"status": "DATA NOT AVAILABLE", "detail": str(e)}
    if not hits:
        return {"status": "DATA NOT AVAILABLE",
                "detail": "no Nasdaq listing matched %r" % company}
    eff = datetime.date.fromisoformat(effective_date)
    lo = (eff - datetime.timedelta(days=20)).isoformat()
    hi = (eff + datetime.timedelta(days=20)).isoformat()
    try:
        bars = nordic_shares.price_history(hits[0]["orderbookId"], lo, hi)
    except SystemExit as e:
        return {"status": "DATA NOT AVAILABLE", "detail": str(e)}
    before = [b for b in bars if b["date"] < effective_date]
    after = [b for b in bars if b["date"] >= effective_date]
    if not before or not after:
        return {"status": "DATA NOT AVAILABLE",
                "detail": "no bars on both sides of %s" % effective_date}
    last_before, first_after = before[-1]["close"], after[0]["close"]
    if not last_before:
        return {"status": "DATA NOT AVAILABLE", "detail": "zero close before event"}
    observed = first_after / last_before
    # An unadjusted series divides the price by the factor at the split.
    expected_unadj = (1.0 / expected_factor) if expected_factor else None
    result = {"symbol": hits[0]["symbol"], "last_close_before": last_before,
              "first_close_after": first_after, "observed_ratio": observed,
              "expected_if_unadjusted": expected_unadj}
    if expected_unadj and abs(observed / expected_unadj - 1) < 0.15:
        result["status"] = "SERIES IS UNADJUSTED"
        result["detail"] = ("the close falls by roughly the split factor across "
                            "the effective date - prices before this date are "
                            "NOT comparable with prices after it")
    elif abs(observed - 1) < 0.15:
        result["status"] = "SERIES IS ALREADY SPLIT-ADJUSTED"
        result["detail"] = ("no discontinuity at a confirmed split - Nasdaq has "
                            "back-adjusted the history. Do NOT apply the factor "
                            "again. This is the normal case for this endpoint.")
    else:
        result["status"] = "INCONCLUSIVE"
        result["detail"] = ("the move across the date matches neither an "
                            "unadjusted split nor a clean series - ordinary "
                            "volatility, a gap in trading, or a second event")
    return result


def share_count_split_check(events, max_gap_days=400):
    """A split leaves a near-integer jump in the share-count record.

    This cross-check works where the price check does not, because the
    disclosure record is never retroactively rewritten.

    The gap guard is not cosmetic. Mycronic's record jumps straight from a 2009
    disclosure of 65,277,693 to a 2025 disclosure of 195,833,018 - exactly
    3.000x - because the issuer filed nothing in between. Without the guard
    that reads as a clean 3:1 split; it is sixteen years of ordinary issuance
    followed by a real 2:1. Any gap this long is reported as UNSAFE rather than
    as a signature.
    """
    flags = []
    prev = prev_date = None
    for e in events:
        n = e.get("total_shares")
        if n and prev:
            ratio = n / prev
            gap = None
            try:
                gap = (datetime.date.fromisoformat(e["date"][:10])
                       - datetime.date.fromisoformat(prev_date[:10])).days
            except ValueError:
                pass
            for cand in (2, 3, 4, 5, 10, 20, 50, 100):
                hit = None
                if abs(ratio - cand) / cand < 0.02:
                    hit = "%d:1 split" % cand
                elif abs(ratio - 1.0 / cand) * cand < 0.02:
                    hit = "1:%d reverse split" % cand
                if not hit:
                    continue
                flags.append({
                    "date": e["date"], "ratio": ratio, "looks_like": hit,
                    "from": prev, "to": n, "from_date": prev_date,
                    "gap_days": gap,
                    "reliable": gap is not None and gap <= max_gap_days,
                })
        if n:
            prev, prev_date = n, e["date"]
    return flags


# Types that legitimately explain a change in the REGISTERED share count (the
# figure in "total number of shares and votes"). A plain buyback does NOT
# belong here - shares sitting in treasury are still registered, so an
# ordinary "acquisitions of own shares" filing moves nothing in that count.
# Only an actual cancellation, redemption, split or issuance does.
SHARE_COUNT_MOVING_TYPES = {
    "SPLIT", "REVERSE_SPLIT", "RIGHTS_ISSUE", "DIRECTED_ISSUE",
    "SET_OFF_OR_INKIND_ISSUE", "WARRANT_OR_INCENTIVE_ISSUE",
    "CONVERTIBLE_CONVERSION", "SHARE_ISSUE_OTHER", "CANCELLATION",
    "REDEMPTION", "NEW_SHARE_CLASS",
}


def unexplained_share_count_moves(events, classified_rows, window_days=15):
    """Disclosure-log changes with no classified action nearby to explain them.

    The share-count log is never retroactively rewritten, but classify() only
    ever sees a HEADLINE, and several real issuers never publish one worded
    like a cancellation at all. Evolution AB is the concrete case (found
    2026-08-31 auditing --shares): its registered share count fell by
    4,565,503 (2024-05-31), 7,371,042 (2025-05-30) and 5,235,549 (2026-04-30)
    - real cancellations of repurchased shares, confirmed by the disclosure
    log itself - with no Nasdaq CNS or MFN headline in a 1,200-announcement
    sweep using the words "cancellation", "indragning" or "makulering"
    anywhere near those dates. A caller that trusts only classified TYPES
    would never learn the share count moved at all; this reads the numbers
    instead of the words.
    """
    explained_dates = []
    for r in classified_rows:
        if r.get("type") not in SHARE_COUNT_MOVING_TYPES:
            continue
        d = _sort_key(r)[:10]
        if _ISO_DATE.match(d or ""):
            explained_dates.append(datetime.date.fromisoformat(d))

    out = []
    for e in events:
        chg = e.get("change_vs_previous")
        if not chg:
            continue
        try:
            d = datetime.date.fromisoformat(e["date"][:10])
        except ValueError:
            continue
        if any(abs((d - cd).days) <= window_days for cd in explained_dates):
            continue
        out.append({
            "date": e["date"][:10], "change": chg, "to": e["total_shares"],
            "from": e["total_shares"] - chg,
            "likely": ("cancellation of repurchased shares, or a redemption"
                      if chg < 0 else
                      "an issuance the keyword rules in classify() did not "
                      "recognise"),
        })
    return out


# ---------------------------------------------------------------------------
# Historical adjustment factor between two dates (spec 8)
#
# WHY THIS EXISTS: Nasdaq Nordic's price history is UNADJUSTED for splits and
# dividends (nordic_shares.price_history's own docstring). A raw price ratio,
# or an EPS series, spanning a split is wrong by the split factor and nothing
# in either data feed says so. A caller building a historical multiple range
# needs one question answered before it divides anything: between two dates,
# what happened to the share count, and is there a clean multiplicative
# factor that can be applied, or not.
#
# THE DESIGN CHOICE THAT MATTERS: only a CONFIRMED split (Nasdaq exchange
# notice, ratio and effective date straight from Issuer Surveillance) ever
# contributes to the returned factor. A near-integer jump in the share-count
# log with no confirming notice is reported as a warning, never folded into
# the number - and a rights issue, directed issue, buyback+cancellation,
# redemption or spin-off in the window is reported as a break with NO factor
# at all, because none of those has a clean price relationship: a rights
# issue's effect on the pre-issue price depends on the subscription price
# relative to market price (TERP), not on a fixed ratio. Guessing one would be
# exactly the "silent adjustment factor" the spec warns against.

def corporate_actions_between(company, date_from, date_to, pages=3):
    """Every classified action in [date_from, date_to], oldest first.

    Sweeps Nasdaq CNS and MFN (the same two sources the default view uses),
    dedupes the same way, and keeps only rows that both classified as an
    action and fall inside the window. This is NOT a substitute for a deeper
    --pages sweep on an issuer with a very long history - it inherits the same
    "this IS the history depth" limit collect_nasdaq documents.
    """
    rows = collect_nasdaq(company, pages=pages)
    mfn_rows, _ = collect_mfn(company)
    rows += mfn_rows
    rows, _ = dedupe(rows)
    out = [r for r in rows if r.get("type")
           and date_from <= _sort_key(r)[:10] <= date_to]
    return sorted(out, key=_sort_key)


def split_adjustment_factor(company, date_from, date_to, pages=3):
    """Cumulative split/reverse-split factor between two dates, or none.

    Returns a dict:
      factor                  product of CONFIRMED split factors in the
                               window, or None if none is confirmed
      confirmed_splits        [{date, terms, factor, source_url}], each from
                               a Nasdaq exchange notice
      unconfirmed_signatures  near-integer jumps in the share-count log inside
                               the window with NO matching exchange notice -
                               excluded from `factor` on purpose
      other_actions_in_window every OTHER action in the window that also
                               breaks per-share comparability (rights issue,
                               directed issue, buyback+cancellation, spin-off,
                               ...) - these have no clean multiplicative
                               factor and are listed, not folded in
      reliable                False if anything here should stop a caller
                               from applying `factor` unattended
      warnings                plain-English cautions, always read these
    """
    warnings = []
    notices = find_split_notices(company)
    confirmed = []
    for it in notices:
        parsed = parse_split_notice(cns_body(it.get("messageUrl") or ""))
        eff = parsed.get("effective_date")
        if not eff or not (date_from <= eff <= date_to):
            continue
        for ev in parsed.get("events") or []:
            if ev.get("factor"):
                confirmed.append({"date": eff, "terms": ev["terms"],
                                  "kind": ev["kind"], "factor": ev["factor"],
                                  "source_url": it.get("messageUrl")})
                break   # one factor per notice; a dual-class notice repeats it

    factor = None
    if confirmed:
        factor = 1.0
        for c in confirmed:
            factor *= c["factor"]

    events, _, _ = share_history(company, fetch_bodies=60)
    flags = [f for f in share_count_split_check(events)
             if date_from <= f["date"][:10] <= date_to]
    confirmed_dates = {c["date"] for c in confirmed}
    unconfirmed = []
    for f in flags:
        # A flag within a few days of a confirmed notice is the SAME event
        # seen from the share-count side, not a second one.
        if any(abs((datetime.date.fromisoformat(f["date"][:10])
                    - datetime.date.fromisoformat(cd)).days) <= 10
               for cd in confirmed_dates):
            continue
        unconfirmed.append(f)
        warnings.append(
            "share count jumps %s -> %s on %s (x%.3f, looks like a %s) with NO "
            "confirming exchange notice in this window - NOT included in "
            "`factor`. Applying it anyway risks a wrong adjustment, which is "
            "worse than none." % (fmt_int(f["from"]), fmt_int(f["to"]),
                                  f["date"][:10], f["ratio"], f["looks_like"]))
        if not f["reliable"]:
            warnings.append(
                "  ...and that jump spans %s days between disclosures - too "
                "long a gap to trust as one signature even before the notice "
                "is missing (see share_count_split_check)." % f["gap_days"])

    # classify() sweep unrestricted by the window, so an action that explains
    # a share-count move can still be matched even when it lands just outside
    # date_from/date_to (a headline dated the day before the disclosure, say).
    all_rows = collect_nasdaq(company, pages=pages)
    mfn_rows, _ = collect_mfn(company)
    all_rows += mfn_rows
    all_rows, _ = dedupe(all_rows)

    other = [r for r in all_rows
             if r.get("type") in BREAKS_PER_SHARE
             and r["type"] not in ("SPLIT", "REVERSE_SPLIT")
             and date_from <= _sort_key(r)[:10] <= date_to]

    # A real share-count change with NO classified headline anywhere near it -
    # Evolution's cancellations of repurchased shares are the confirmed live
    # case (see unexplained_share_count_moves). Without this, --factor would
    # silently agree with classify()'s blind spot and report a clean window
    # when the share count in fact moved.
    for u in unexplained_share_count_moves(events, all_rows):
        if not (date_from <= u["date"] <= date_to):
            continue
        other.append({"date": u["date"], "type": "UNEXPLAINED_SHARE_COUNT_CHANGE",
                      "title": "share count %s -> %s (%+d), likely %s"
                               % (fmt_int(u["from"]), fmt_int(u["to"]),
                                  u["change"], u["likely"])})
        warnings.append(
            "share count changed by %+d on %s with NO classified announcement "
            "(CNS or MFN) anywhere near it - likely %s. --shares shows the raw "
            "log; nothing here should be folded into `factor`."
            % (u["change"], u["date"], u["likely"]))

    # classify() assigns ONE type per headline, first-rule-wins (RULES is
    # ordered, most-specific first). A headline naming two actions at once -
    # Bambuser's actual 2025 release reads "...proposes a fully secured rights
    # issue of shares... and a reverse share split" - gets tagged SPLIT only,
    # so the confirmed split factor above is correct for the RATIO but says
    # nothing about the capital raise bundled into the same resolution. Rather
    # than silently miss that second action, the issuer's own split release is
    # re-scanned for an issuance keyword and surfaced as a warning if found.
    for a in find_split_announcements(company):
        if not (date_from <= a["date"][:10] <= date_to):
            continue
        for typ, _sign, rx in COMPILED:
            if (typ in ("RIGHTS_ISSUE", "DIRECTED_ISSUE",
                       "SET_OFF_OR_INKIND_ISSUE") and rx.search(a["title"])):
                warnings.append(
                    "the split announcement on %s also reads as a %s ('%s') - "
                    "this looks like a combined capital raise and split. The "
                    "confirmed factor above covers the SPLIT RATIO ONLY; it "
                    "does NOT account for the additional dilution from the "
                    "raise. Read the release before adjusting anything with "
                    "this factor." % (a["date"][:10], typ, a["title"][:80]))
                break

    if not confirmed and not unconfirmed and not other:
        # Distinct from the branch below: THERE the sources found suspicious
        # evidence they could not confirm, and factor stays None on purpose.
        # HERE nothing at all was found, so 1.0 is not a guess - it is "no
        # adjustment because no evidence of anything to adjust for" - but the
        # warning is unconditional because absence of a match is never proof
        # of absence of the underlying event.
        factor = 1.0
        warnings.append(
            "no split, and no other share-count-breaking action, was found in "
            "the sources queried for this window. That is NOT proof none "
            "occurred - only that none matched. factor=1.0 reflects the "
            "absence of evidence, not evidence of absence.")
    elif not confirmed and unconfirmed:
        warnings.append(
            "no CONFIRMED split in this window, so factor is None rather than "
            "a guess. Run --splits for the full cross-check on this issuer.")
    if other:
        warnings.append(
            "%d other action(s) in this window change the share count with NO "
            "clean multiplicative price factor (a rights issue's effect "
            "depends on the subscription price vs market price, not a fixed "
            "ratio) - restate per-share figures from the share count at each "
            "date via --shares, do not try to fold these into `factor`."
            % len(other))

    return {
        "company": company, "date_from": date_from, "date_to": date_to,
        "factor": factor,
        "confirmed_splits": confirmed,
        "unconfirmed_signatures": unconfirmed,
        "other_actions_in_window": other,
        "reliable": bool(confirmed or (not unconfirmed and not other)),
        "warnings": warnings,
    }


def print_factor(result):
    print("%s  -  share-count-breaking actions and split factor" % result["company"])
    print("  window: %s to %s" % (result["date_from"], result["date_to"]))
    print()
    if result["confirmed_splits"]:
        print("  CONFIRMED SPLITS (Nasdaq exchange notice, factor = new/old shares):")
        for c in result["confirmed_splits"]:
            print("    %s  %-14s terms %-8s  factor x%.4f"
                  % (c["date"], c["kind"], c["terms"], c["factor"]))
            print("      %s" % c["source_url"])
    else:
        print("  No confirmed split (exchange notice) in this window.")
    print()
    if result["factor"] is not None:
        print("  CUMULATIVE CONFIRMED SPLIT FACTOR: x%.6f" % result["factor"])
        print("  Multiply a PRE-window unadjusted price or per-share figure by")
        print("  this to express it in POST-window share terms. This factor")
        print("  covers confirmed splits ONLY - read the warnings below before")
        print("  assuming nothing else changed the share count in this window.")
    else:
        print("  NO FACTOR RETURNED. Applying an adjustment here would be a")
        print("  guess, which the spec is explicit is worse than none.")
    if result["unconfirmed_signatures"]:
        print()
        print("  UNCONFIRMED near-integer jumps in the share-count log (NOT in factor):")
        for f in result["unconfirmed_signatures"]:
            print("    %s  %s -> %s  x%.3f  looks like a %s%s"
                  % (f["date"][:10], fmt_int(f["from"]), fmt_int(f["to"]),
                     f["ratio"], f["looks_like"],
                     "" if f["reliable"] else "  [gap %s days - weak signature]"
                     % f["gap_days"]))
    if result["other_actions_in_window"]:
        print()
        print("  OTHER SHARE-COUNT-BREAKING ACTIONS IN THIS WINDOW (no clean factor):")
        for r in result["other_actions_in_window"]:
            print("    %s  %-20s %s" % (r["date"][:10], r["type"], r["title"][:60]))
    print()
    for w in result["warnings"]:
        print("  !! %s" % w)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def fmt_int(n):
    return "{:,}".format(n) if isinstance(n, int) else "n/a"


def print_actions(company, rows, shown_sources, notes, limit):
    print("%s  -  corporate actions  -  retrieved %s"
          % (company, datetime.datetime.now(datetime.timezone.utc)
             .strftime("%Y-%m-%d %H:%M UTC")))
    print("Sources queried: %s" % ", ".join(shown_sources))
    print()
    if not rows:
        print("DATA NOT AVAILABLE: no announcement in the window classified as a")
        print("corporate action. That is NOT proof there were none - it means")
        print("nothing matched in the sources above. Widen with --limit, add")
        print("--cision for a Cision-distributed large cap, or check the issuer's")
        print("own 'Total number of voting rights and capital' disclosures with")
        print("--shares, which is the legally mandated record.")
        for n in notes:
            print("  note: %s" % n)
        return
    print("  %-16s %-26s %-4s %s" % ("DATE", "TYPE", "DIL", "HEADLINE"))
    print("  " + "-" * 108)
    for r in rows[:limit]:
        sign = {-1: "  -", 1: "  +", 0: "   "}[r.get("dilution") or 0]
        print("  %-16s %-26s %-4s %s"
              % (r["date"][:16], r["type"] or "?", sign, (r["title"] or "")[:60]))
        print("  %-16s %-26s %-4s %s" % ("", "", "", r.get("url") or ""))
        detail = []
        if r.get("basis"):
            detail.append("via %s" % r["basis"])
        if r.get("category"):
            detail.append(r["category"][:52])
        detail.append(r.get("source") or "")
        print("  %-16s %-26s %-4s %s" % ("", "", "", " | ".join(d for d in detail if d)))
        print()
    print("  " + "-" * 108)
    print("  DIL column:  -  dilutive, share count rises")
    print("               +  accretive, share count falls")
    breaks = sorted({r["type"] for r in rows[:limit]
                     if r["type"] in BREAKS_PER_SHARE})
    if breaks:
        print()
        print("  PER-SHARE HISTORY IS BROKEN by: %s" % ", ".join(breaks))
        print("  Any EPS, DPS or book-value-per-share series crossing those dates")
        print("  must be restated before it is compared. Run --shares for the")
        print("  share counts that bracket each event.")
    for n in notes:
        print()
        print("  note: %s" % n)


def print_shares(company, events, unparsed, current, unexplained=None,
                 paging_ceiling_hit=False):
    print("%s  -  share-count disclosure record  -  Nasdaq CNS" % company)
    print()
    if paging_ceiling_hit:
        print("!! History paging hit its ceiling (%d pages) - there is very likely"
              % SHARE_HISTORY_MAX_PAGES)
        print("!! MORE history than is shown below. This record is NOT")
        print("!! presented as complete; treat it as a lower bound.")
        print()
    if not events:
        print("DATA NOT AVAILABLE: this issuer has filed no 'Total number of")
        print("voting rights and capital' disclosure that Nasdaq CNS indexes")
        print("under this exact company name.")
        print()
        print("Two innocent explanations before concluding anything: the share")
        print("count genuinely has not changed (the disclosure is only required")
        print("in a month when it does), or the CNS company name differs from")
        print("what was searched - run --resolve to check the spelling.")
        return
    print("  Every Swedish issuer must publish its new total share and vote count")
    print("  in the month a change is registered. This is that record - the")
    print("  authoritative dilution log, oldest first.")
    print()
    print("  %-12s %18s %16s %9s  %s"
          % ("DATE", "TOTAL SHARES", "CHANGE", "PCT", "DISCLOSURE"))
    print("  " + "-" * 112)
    for e in events:
        chg = ("{:+,}".format(e["change_vs_previous"])
               if e["change_vs_previous"] else "-")
        pct = ("%+.2f%%" % e["pct_change"]) if e["pct_change"] is not None else "-"
        print("  %-12s %18s %16s %9s  %s"
              % (e["date"][:10], fmt_int(e["total_shares"]), chg, pct,
                 (e["title"] or "")[:44]))
    print("  " + "-" * 112)
    print()
    print("  Evidence for each parsed figure (a share count read wrong is worse")
    print("  than no share count, so the source sentence travels with it):")
    for e in events:
        if e.get("total_sentence"):
            print("    %s  %s" % (e["date"][:10], e["total_sentence"][:150]))
            if e.get("by_class"):
                print("             summed across classes: %s"
                      % ", ".join("%s %s" % (k, fmt_int(v))
                                  for k, v in sorted(e["by_class"].items())))
        elif e["date"] and e["total_shares"] is None:
            print("    %s  NOT PARSED - read %s" % (e["date"][:10], e["url"]))
    if unparsed > 0:
        print()
        print("  %d older disclosure(s) were listed but their bodies were not"
              % unparsed)
        print("  fetched (one HTTP request each). Raise --bodies to include them.")

    flags = share_count_split_check(events)
    if flags:
        print()
        print("  SPLIT SIGNATURE IN THE SHARE COUNT:")
        for f in flags:
            print("    %s  %s -> %s  (x%.3f)  looks like a %s%s"
                  % (f["date"][:10], fmt_int(f["from"]), fmt_int(f["to"]),
                     f["ratio"], f["looks_like"],
                     "" if f["reliable"]
                     else "   [UNSAFE: %s days since the previous disclosure]"
                          % f["gap_days"]))
        print("    Confirm with --splits, which reads the exchange's own notice.")

    if unexplained:
        print()
        print("  CHANGES WITH NO CLASSIFIED ANNOUNCEMENT NEAR THEM:")
        print("  The number below is real - it is this issuer's own disclosure -")
        print("  but no Nasdaq CNS or MFN headline classify() recognised falls")
        print("  near this date. Do not conclude nothing happened; read the AGM")
        print("  minutes or the issuer's own release list directly.")
        for u in unexplained:
            print("    %s  %s -> %s  (%+d)  likely: %s"
                  % (u["date"], fmt_int(u["from"]), fmt_int(u["to"]),
                     u["change"], u["likely"]))

    print()
    if current and current.get("total_shares"):
        latest = next((e["total_shares"] for e in reversed(events)
                       if e["total_shares"]), None)
        print("  Nasdaq reference data today: %s shares across %d listed class(es)."
              % (fmt_int(int(current["total_shares"])), current["classes"]))
        if latest:
            diff = int(current["total_shares"]) - latest
            if abs(diff) <= max(2, latest * 0.0001):
                print("  Matches the latest disclosure. Both agree.")
            else:
                print("  !! DISAGREES with the latest disclosure by %s shares."
                      % fmt_int(diff))
                print("  The disclosure is the legal record and includes UNLISTED")
                print("  classes; the reference data covers listed lines only.")
                print("  Where they differ, prefer the disclosure.")
    else:
        print("  Current reference count unavailable - could not cross-check.")


def print_splits(company, notices, announcements, price_checks, count_flags):
    print("%s  -  split and reverse-split detection" % company)
    print()
    if notices:
        print("  CONFIRMED, from Nasdaq exchange notices (Issuer Surveillance):")
        print()
        for n in notices:
            # A dual-class issuer gets one identical Terms block per class in
            # the same notice; print the distinct terms once.
            seen_terms = []
            for ev in n["parsed"]["events"] or [{"kind": "SPLIT?", "terms": "?",
                                                 "factor": None}]:
                key = (ev["kind"], ev["terms"])
                if key in seen_terms:
                    continue
                seen_terms.append(key)
                print("    %s  %-14s terms %-8s  effective %s"
                      % (n["published"][:10], ev["kind"], ev["terms"],
                         n["parsed"]["effective_date"] or "DATA NOT AVAILABLE"))
            p = n["parsed"]
            if p["short_names"]:
                print("      classes: %s" % ", ".join(p["short_names"]))
            if p["isin_current"] or p["isin_new"]:
                print("      ISIN %s -> %s"
                      % (", ".join(p["isin_current"]) or "?",
                         ", ".join(p["isin_new"]) or "?"))
            print("      %s" % n["messageUrl"])
            print()
    else:
        print("  No split exchange notice found for this issuer in the indexed")
        print("  window. That is a weak negative, not a clean bill of health:")
        print("  the free-text index is relevance-ranked, and a split registered")
        print("  before the notice archive began will not appear.")
        print()

    if announcements:
        print("  The issuer's own split releases (secondary, but they carry the")
        print("  resolution and the record date):")
        for a in announcements:
            print("    %s  %-14s %s" % (a["date"][:10], a["type"], a["title"][:58]))
            print("      %s" % a["url"])
        print()

    if count_flags:
        print("  CROSS-CHECK 1 - share-count record (never back-adjusted, so")
        print("  this is the cross-check that can actually falsify):")
        for f in count_flags:
            print("    %s -> %s  %s -> %s  x%.3f  consistent with a %s"
                  % (f["from_date"][:10], f["date"][:10], fmt_int(f["from"]),
                     fmt_int(f["to"]), f["ratio"], f["looks_like"]))
            if not f["reliable"]:
                print("      UNSAFE: %s days separate these two disclosures. A "
                      "ratio measured" % f["gap_days"])
                print("      across a gap that long is ordinary issuance, not a "
                      "split signature.")
        print()
    else:
        print("  CROSS-CHECK 1 - share-count record: no near-integer jump found.")
        print("  A split still shows up here only if the issuer filed a share-")
        print("  count disclosure on both sides of it; many small caps do not.")
        print()

    print("  CROSS-CHECK 2 - unadjusted price series:")
    if not price_checks:
        print("    Not run (no confirmed effective date to test).")
    for pc in price_checks:
        print("    %s  %s" % (pc.get("effective_date", "?"), pc["status"]))
        if "observed_ratio" in pc:
            print("      %s: %.4f on %s -> %.4f, ratio %.3f (a %s split would "
                  "give %.3f in an unadjusted series)"
                  % (pc.get("symbol"), pc["last_close_before"],
                     pc.get("effective_date", "?"), pc["first_close_after"],
                     pc["observed_ratio"], pc.get("terms", "?"),
                     pc["expected_if_unadjusted"] or float("nan")))
        print("      %s" % pc.get("detail", ""))
    print()
    print("  HOW MUCH TO TRUST THIS")
    print("  The exchange notice is authoritative - ratio, ISINs and effective")
    print("  date come straight from Nasdaq Issuer Surveillance.")
    print("  The PRICE cross-check is not a detector. Nasdaq Nordic's chart")
    print("  endpoint BACK-ADJUSTS for splits: verified on Mycronic 2:1 (Jun")
    print("  2025), Investor A/B 4:1 (May 2021), Bambuser 1:30 reverse (Dec")
    print("  2025) and Nobia 1:10 reverse (May 2026) - all four confirmed by")
    print("  exchange notice, all four left NO discontinuity. Treat a clean")
    print("  series as information about the series, never as evidence that")
    print("  no split occurred.")


# ---------------------------------------------------------------------------

def current_reference_count(name):
    if nordic_shares is None:
        return None
    try:
        hits = nordic_shares.search(name)
        if not hits:
            return None
        needle = _norm(name)
        chosen = [h for h in hits if needle in _norm(h["name"] or "")] or hits
        root = nordic_shares.root_symbol(chosen[0]["symbol"])
        classes = [h for h in chosen
                   if nordic_shares.root_symbol(h["symbol"]) == root]
        total = 0.0
        for c in classes:
            s = nordic_shares.summary(c["orderbookId"])
            total += s.get("shares") or 0
        return {"total_shares": total, "classes": len(classes)}
    except SystemExit:
        return None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("company", nargs="?", help='issuer name, e.g. "Sandvik"')
    ap.add_argument("--resolve", metavar="NAME",
                    help="show the exact Nasdaq CNS company names matching NAME")
    ap.add_argument("--company-exact", metavar="NAME",
                    help="skip resolution and use this exact CNS company name")
    ap.add_argument("--shares", action="store_true",
                    help="the share-count disclosure record (the dilution log)")
    ap.add_argument("--splits", action="store_true",
                    help="split / reverse-split events with cross-checks")
    ap.add_argument("--factor", nargs=2, metavar=("FROM", "TO"),
                    help="cumulative CONFIRMED split factor between two "
                         "YYYY-MM-DD dates, plus every other share-count-"
                         "breaking action in the window (rights issue, "
                         "directed issue, buyback+cancellation, spin-off, "
                         "...), each flagged separately - for adjusting a "
                         "historical price or per-share figure without "
                         "guessing at what a rights issue or buyback did to it")
    ap.add_argument("--cision", action="store_true",
                    help="also sweep Cision (untagged; Sandvik, Atlas Copco, "
                         "Hexagon, AB Volvo publish there)")
    ap.add_argument("--all", action="store_true", dest="show_all",
                    help="include announcements that classify as no action")
    ap.add_argument("--routine", action="store_true",
                    help="keep the weekly buyback progress reports, which are "
                         "hidden by default because they bury everything else")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--pages", type=int, default=3,
                    help="Nasdaq CNS pages of 200 to sweep. This IS the history "
                         "depth: Atlas Copco has 689 announcements, so the "
                         "default 600 stops in 2008 and a longer-listed issuer "
                         "needs more")
    ap.add_argument("--bodies", type=int, default=40,
                    help="--shares: how many disclosure bodies to fetch and parse")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    if args.resolve:
        hits = resolve_company(args.resolve)
        if not hits:
            print("DATA NOT AVAILABLE: no Nasdaq CNS company name matched %r."
                  % args.resolve)
            print("CNS matches the registered name exactly - try the full legal")
            print("name, or a distinctive fragment of it.")
            return
        print("Nasdaq CNS company names matching %r:" % args.resolve)
        for h in hits:
            print("  %-42s %-24s (%d hits in probe)"
                  % (h["company"], h["market"] or "-", h["announcements_in_probe"]))
        return

    if not args.company:
        ap.error('give a company name, or --resolve NAME')

    notes = []
    if args.company_exact:
        company = args.company_exact
    else:
        hits = resolve_company(args.company)
        if not hits:
            print("DATA NOT AVAILABLE: Nasdaq CNS has no company matching %r."
                  % args.company)
            print("Run --resolve to search the name index, or pass the exact")
            print("registered name with --company-exact.")
            return
        company = hits[0]["company"]
        if len(hits) > 1:
            notes.append("%d CNS names matched; using %r. Others: %s"
                         % (len(hits), company,
                            ", ".join(h["company"] for h in hits[1:4])))

    # ---- --factor ---------------------------------------------------------
    if args.factor:
        date_from, date_to = args.factor
        for d in (date_from, date_to):
            try:
                datetime.date.fromisoformat(d)
            except ValueError:
                ap.error("--factor dates must be YYYY-MM-DD, got %r" % d)
        result = split_adjustment_factor(company, date_from, date_to,
                                         pages=args.pages)
        if args.as_json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return
        print_factor(result)
        return

    # ---- --splits -------------------------------------------------------
    if args.splits:
        raw = find_split_notices(company)
        notices = []
        for it in raw:
            parsed = parse_split_notice(cns_body(it.get("messageUrl") or ""))
            notices.append({"published": it.get("published") or "",
                            "headline": it.get("headline"),
                            "messageUrl": it.get("messageUrl"),
                            "parsed": parsed})
        notices.sort(key=lambda n: n["published"], reverse=True)

        announcements = find_split_announcements(company)
        events, _, _ = share_history(company, fetch_bodies=min(args.bodies, 25))
        count_flags = share_count_split_check(events)

        checks = []
        for n in notices:
            p = n["parsed"]
            for ev in p["events"] or []:
                pc = price_check(args.company, p["effective_date"], ev["factor"])
                pc["effective_date"] = p["effective_date"]
                pc["terms"] = ev["terms"]
                checks.append(pc)
                break        # one check per notice is enough; classes share it

        if args.as_json:
            print(json.dumps({"company": company, "source": "Nasdaq CNS exchange notices",
                              "retrieved_utc": datetime.datetime.now(
                                  datetime.timezone.utc).isoformat(),
                              "split_notices": notices,
                              "issuer_split_releases": announcements,
                              "share_count_signatures": count_flags,
                              "price_series_checks": checks,
                              "warning": "Nasdaq Nordic price history is "
                                         "back-adjusted for splits; absence of a "
                                         "discontinuity is not evidence of no split."},
                             indent=2, ensure_ascii=False))
            return
        print_splits(company, notices, announcements, checks, count_flags)
        return

    # ---- --shares -------------------------------------------------------
    if args.shares:
        events, unparsed, paging_ceiling_hit = share_history(company, fetch_bodies=args.bodies)
        current = current_reference_count(args.company)
        # A second sweep, classified by headline, to catch a share-count move
        # with NO announcement worded like one - see
        # unexplained_share_count_moves for the confirmed Evolution case.
        classified_rows = collect_nasdaq(company, pages=args.pages)
        mfn_rows, _ = collect_mfn(args.company)
        classified_rows += mfn_rows
        classified_rows, _ = dedupe(classified_rows)
        unexplained = unexplained_share_count_moves(events, classified_rows)
        if args.as_json:
            print(json.dumps({"company": company,
                              "source": "Nasdaq CNS - Total number of voting "
                                        "rights and capital",
                              "retrieved_utc": datetime.datetime.now(
                                  datetime.timezone.utc).isoformat(),
                              "disclosures": events,
                              "unparsed_older": unparsed,
                              "history_paging_ceiling_hit": paging_ceiling_hit,
                              "current_reference_count": current,
                              "split_signatures": share_count_split_check(events),
                              "unexplained_changes": unexplained},
                             indent=2, ensure_ascii=False))
            return
        print_shares(company, events, unparsed, current, unexplained,
                    paging_ceiling_hit=paging_ceiling_hit)
        return

    # ---- default: recent classified corporate actions --------------------
    sources = ["Nasdaq CNS (company=%r)" % company]
    rows = collect_nasdaq(company, pages=args.pages)

    mfn_rows, mfn_err = collect_mfn(args.company)
    if mfn_err:
        notes.append("MFN not used: %s" % mfn_err)
    else:
        sources.append("MFN.se sub:ca tags")
        rows += mfn_rows

    if args.cision:
        cis_rows, cis_err = collect_cision(args.company)
        if cis_err:
            notes.append("Cision not used: %s" % cis_err)
        else:
            sources.append("Cision (UNTAGGED - labels are keyword guesses)")
            rows += cis_rows

    rows, collapsed = dedupe(rows)
    rows.sort(key=_sort_key, reverse=True)
    if collapsed:
        notes.append("%d release(s) collapsed as a same-day, same-type copy of a "
                     "Nasdaq item (usually the Swedish original of an English "
                     "release)." % collapsed)
    if not args.show_all:
        rows = [r for r in rows if r["type"]]

    for r in rows:
        r["routine"] = is_routine(r)
    routine = [r for r in rows if r["routine"]]
    if not args.routine:
        rows = [r for r in rows if not r["routine"]]
        if routine:
            notes.append(
                "%d routine buyback progress report(s) hidden, %s to %s. They are "
                "weekly filings under an existing mandate, not new decisions - "
                "show them with --routine, or use --shares for the share count "
                "they actually moved."
                % (len(routine), _sort_key(routine[-1])[:10],
                   _sort_key(routine[0])[:10]))

    if args.as_json:
        print(json.dumps({"company": company, "query": args.company,
                          "sources": sources,
                          "retrieved_utc": datetime.datetime.now(
                              datetime.timezone.utc).isoformat(),
                          "count": len(rows[:args.limit]),
                          "notes": notes,
                          "actions": rows[:args.limit]},
                         indent=2, ensure_ascii=False))
        return
    print_actions(company, rows, sources, notes, args.limit)


if __name__ == "__main__":
    main()
