#!/usr/bin/env python3
"""What management PROMISED, and what the company then DELIVERED.

Management credibility is usually assessed on impression: an analyst remembers
that a CEO "always talks a good game" or "under-promises". Nobody keeps the
record. This script keeps the record.

It assembles three separate things and never lets them blur together:

  1. STANDING FINANCIAL TARGETS (finansiella mal) - the growth / margin /
     return / leverage / dividend targets a Swedish issuer publishes on its IR
     site and repeats in the annual report. Stable, explicit, quotable.
  2. PERIOD GUIDANCE - the forward statements made in interim and year-end
     report releases ("we expect an EBITDA margin of 66-68 percent for 2025").
  3. THE DELIVERED OUTCOME - taken, wherever possible, from the company's own
     later report for that period, so the definition matches the promise, and
     otherwise computed from ESEF/IFRS filings and labelled as a different
     basis.

WHY THE THREE MUST STAY SEPARATE. A target is a claim about the future made by
an interested party. It is never independently verified, and every guidance row
below is labelled SINGLE SOURCE - MANAGEMENT GUIDANCE for that reason. Whether a
target was MET is, by contrast, arithmetic on reported figures - a fact. Whether
management is credible is neither; it is an opinion, and it is labelled as one.

WHY PROSE EXTRACTION IS SHOWN, NOT HIDDEN. Targets live in sentences, not in
tagged data. Any regex over prose will misfire eventually. So every extracted
number is printed next to the raw sentence it came from. A misparse should be
visible on the page, not silently folded into a score.

WHAT THIS DOES NOT DO. It does not read PDFs - the annual report PDF cannot be
parsed with the standard library alone, so it is linked, not mined. It does not
invent a target it could not find: absent targets print DATA NOT AVAILABLE with
a pointer to where a human should look. And it does not net out definition
mismatches: a target of "adjusted EBITA margin, excluding currency" compared
against an IFRS operating margin is reported as NOT COMPARABLE, not as a miss.

MANAGEMENT EXECUTION SCORE (--execution). A 0-10 number built ONLY from the
facts above: the delivery rate on promises that could actually be matched to
an outcome, guidance CUTS (a lowered target is scored separately from a
missed one), whether a cut forms a PATTERN (two in a row, or a cut soon after
a reaffirmation - one miss is a forecasting error, a pattern is not),
whether a standing target's DEFINITION changed while its number did not
(moved goalposts), and whether repeated BEATs cluster suspiciously tightly
(chronic sandbagging - information, not a compliment). A target with no
matched outcome is UNKNOWN, never a silent pass. The score is an opinion
derived from evidence and printed with every component that produced it; the
guidance it scores remains SINGLE SOURCE - MANAGEMENT GUIDANCE throughout.

Sources, all free and keyless:
    mfn_news.py           MFN.se release archive (most Swedish issuers)
    cision_news.py        Cision newsroom (Sandvik, Atlas Copco, Hexagon, Volvo)
    esef_fundamentals.py  ESEF/Inline-XBRL annual figures via filings.xbrl.org
    the issuer's own IR site, fetched as plain HTML

Usage:
    python guidance_track.py "Sandvik"
    python guidance_track.py "Evolution" --history
    python guidance_track.py "Addtech" --targets
    python guidance_track.py "Sandvik" --execution
    python guidance_track.py "Evolution" --json
    python guidance_track.py "Addtech" --no-ir      # skip the IR-site crawl
"""
import argparse
import collections
import datetime
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Sibling scripts are importable helpers, not subprocesses.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import mfn_news as MFN            # noqa: E402
import cision_news as CIS         # noqa: E402
import esef_fundamentals as ESEF  # noqa: E402
import company_resolve as CR      # noqa: E402  -- LEI/ISIN identity, for the store only

UA = "Mozilla/5.0 (compatible; investment-analyst-skill/1.0)"
GUIDANCE_LABEL = "SINGLE SOURCE - MANAGEMENT GUIDANCE"


# ---------------------------------------------------------------------------
# HTTP + HTML, kept deliberately small
# ---------------------------------------------------------------------------

def http_html(url, timeout=25):
    """Fetch a page as text. Returns None on any failure - an IR site that
    refuses a stdlib client is a normal outcome, not an error worth aborting."""
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "en,sv;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "")
            if "html" not in ctype and "text" not in ctype:
                return None
            raw = r.read(3_000_000)
        return raw.decode("utf-8", "replace")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            ValueError, OSError):
        return None


def to_text(markup):
    """Strip tags to readable lines. Block-level closers become newlines so a
    bulleted target list does not collapse into one unsplittable sentence."""
    if not markup:
        return ""
    t = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", markup,
               flags=re.S | re.I)
    t = re.sub(r"</(p|li|h[1-6]|div|tr|td|th|section)>", "\n", t, flags=re.I)
    t = re.sub(r"<br[^>]*>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t\xa0 ]+", " ", t)
    return "\n".join(l.strip() for l in t.splitlines() if l.strip())


BULLET = re.compile(r"^\s*[•·*–—-]\s+")


def reflow(text):
    """Join hard-wrapped continuation lines back onto their sentence.

    MFN and Cision bodies are wrapped near 80 characters. Splitting on the raw
    lines cuts nearly every guidance sentence in half - the metric lands on one
    line and its number on the next - and the extractor then silently finds
    nothing. This is the single most important preprocessing step in the file.
    """
    blocks = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            blocks.append("")
            continue
        stripped = line.strip()
        starts_new = (BULLET.match(line) or not blocks or not blocks[-1]
                      or re.match(r"^[A-Z0-9][^a-z]{0,40}$", stripped)
                      or blocks[-1].endswith((".", "!", "?", ":")))
        if starts_new:
            blocks.append(stripped)
        else:
            blocks[-1] += " " + stripped
    return [b for b in blocks if b]


def sentences(text):
    """Sentence-ish units, after un-wrapping the hard line breaks."""
    for block in reflow(text):
        for s in re.split(r"(?<=[.!?;])\s+", block):
            s = re.sub(r"^[\s•·*–—-]+", "", s).strip()
            if 12 <= len(s) <= 500:
                yield s


# ---------------------------------------------------------------------------
# Metric vocabulary
#
# Order matters. "net debt/EBITDA" must be tested before "EBITDA margin", and
# "profit growth" before the bare word "growth", or the specific metric is
# swallowed by the general one.
# ---------------------------------------------------------------------------

METRIC_PATTERNS = [
    ("net_debt_ebitda",
     r"net\s*(?:financial\s*)?debt\s*(?:/|to\s+)\s*ebitda|financial\s+net\s+debt/ebitda|"
     r"nettoskuld\w*\s*(?:/|i\s+f[oö]rh[aå]llande\s+till)\s*ebitda|leverage\s+ratio"),
    ("ebitda_margin", r"ebitda[\s-]*margin|ebitda[\s-]*marginal|margin[^.]{0,25}ebitda"),
    ("ebita_margin", r"ebita[\s-]*margin|ebita[\s-]*marginal"),
    ("ebit_margin",
     r"ebit[\s-]*margin|operating\s+(?:profit\s+)?margin|r[oö]relsemarginal"),
    ("p_wc", r"p\s*/\s*wc|return\s+on\s+working\s+capital|avkastning\s+p[aå]\s+r[oö]relsekapital"),
    ("roce",
     r"\broce\b|\broic\b|return\s+on\s+capital\s+employed|return\s+on\s+invested\s+capital|"
     r"avkastning\s+p[aå]\s+sysselsatt"),
    ("roe", r"return\s+on\s+equity|avkastning\s+p[aå]\s+eget\s+kapital"),
    ("equity_ratio", r"equity\s+ratio|soliditet"),
    ("payout",
     r"pay[\s-]*out|dividend\s+polic|utdelningspolic|dividend[^.]{0,40}(?:ratio|percent|%)|"
     r"of\s+(?:adjusted\s+)?earnings\s+per\s+share|av\s+[aå]rets\s+(?:vinst|resultat)"),
    ("profit_growth",
     r"profit\s+growth|earnings\s+growth|ebita\s+growth|ebit\s+growth|"
     r"vinsttillv[aä]xt|resultattillv[aä]xt|growth[^.]{0,30}measured\s+as\s+profit"),
    ("organic_growth", r"organic\s+(?:revenue\s+|sales\s+|net\s+sales\s+)?growth|organisk\s+tillv[aä]xt"),
    ("growth", r"\bgrowth\b|tillv[aä]xt|revenue\s+cagr|sales\s+cagr"),
    ("capex", r"\bcapex\b|total\s+investments?|investeringar"),
]

METRIC_LABEL = {
    "net_debt_ebitda": "net debt / EBITDA",
    "ebitda_margin": "EBITDA margin",
    "ebita_margin": "EBITA margin",
    "ebit_margin": "EBIT / operating margin",
    "p_wc": "return on working capital (P/WC)",
    "roce": "ROCE / ROIC",
    "roe": "return on equity",
    "equity_ratio": "equity ratio",
    "payout": "dividend payout",
    "profit_growth": "profit / EBITA growth",
    "organic_growth": "organic growth",
    "growth": "revenue growth",
    "capex": "capex / investments",
}

# Which way is good. Used only to turn a numeric comparison into BEAT vs MISS;
# never to decide whether something is a target.
LOWER_IS_BETTER = {"net_debt_ebitda"}
# A payout target is a policy, not an achievement - exceeding it is not a beat.
NEUTRAL = {"payout", "capex", "equity_ratio"}


def classify_metric(sentence):
    low = sentence.lower()
    for name, pattern in METRIC_PATTERNS:
        if re.search(pattern, low):
            return name
    return None


# ---------------------------------------------------------------------------
# Numeric target parsing
#
# Preference order is deliberate: a RANGE beats a THRESHOLD beats a POINT.
# Sentences routinely mix the delivered figure with the promise -
#   "EBITDA margin for the full year amounts to 70.5 percent, in the upper end
#    of the communicated full year guidance of 68-71 percent"
# - and in those the promise is nearly always the range.
# ---------------------------------------------------------------------------

NUM = r"\d{1,4}(?:[.,]\d{1,2})?"
PCT = r"(?:%|per\s*cent|percent|procent)"

RANGE_RE = re.compile(
    r"(?<![\d.,/])(" + NUM + r")\s*" + PCT + r"?\s*[-–—]\s*(" + NUM + r")\s*" + PCT,
    re.I)
RANGE_WORDS_RE = re.compile(
    r"(?:between|mellan)\s+(" + NUM + r")\s*" + PCT + r"?\s+(?:and|och|to|till)\s+("
    + NUM + r")\s*" + PCT, re.I)
FLOOR_RE = re.compile(
    r"(?:at\s+least|no\s+less\s+than|minimum(?:\s+\w+){0,2}\s+of|minst|[oö]ver|"
    r"exceed(?:ing)?|above|more\s+than|greater\s+than|>=?|≥)"
    r"\s*(" + NUM + r")\s*(" + PCT + r")?", re.I)
CEIL_RE = re.compile(
    r"(?:below|less\s+than|not\s+exceed(?:ing)?|no\s+more\s+than|maximum\s+of|max(?:imalt)?|"
    r"under|h[oö]gst|<=?|≤)\s*(" + NUM + r")\s*(" + PCT + r")?", re.I)
POINT_PCT_RE = re.compile(r"(?<![\d.,/])(" + NUM + r")\s*" + PCT, re.I)
POINT_X_RE = re.compile(
    r"(?:ratio|multiple)[^.\d]{0,30}?(?:of|at|around|about|cirka|omkring)\s*(" + NUM + r")\b", re.I)
MONEY_RE = re.compile(
    r"(?:SEK|EUR|USD|NOK|DKK|MSEK|MEUR)\s*(" + NUM + r")\s*(million|billion|bn|m|mn|miljoner|miljarder)?",
    re.I)


def _f(raw):
    try:
        return float(str(raw).replace(" ", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse_quant(sentence, metric):
    """Extract the promised quantity from a sentence.

    Returns {kind, low, high, unit} or None. `kind` is one of range / floor /
    ceiling / point. Nothing here guesses: if no number attaches to a
    recognisable form, the caller gets None and the row is dropped.
    """
    s = sentence
    money_metric = metric == "capex"

    m = RANGE_RE.search(s) or RANGE_WORDS_RE.search(s)
    if m:
        lo, hi = _f(m.group(1)), _f(m.group(2))
        if lo is not None and hi is not None and lo <= hi:
            return {"kind": "range", "low": lo, "high": hi, "unit": "%"}

    # A threshold only counts when the comparator sits next to the number that
    # belongs to this metric. Requiring a percent sign (or an "x"-style ratio)
    # keeps share counts and phone numbers out.
    m = FLOOR_RE.search(s)
    if m and (m.group(2) or metric == "net_debt_ebitda"):
        v = _f(m.group(1))
        if v is not None:
            return {"kind": "floor", "low": v, "high": None,
                    "unit": "%" if m.group(2) else "x"}
    m = CEIL_RE.search(s)
    if m and (m.group(2) or metric == "net_debt_ebitda"):
        v = _f(m.group(1))
        if v is not None:
            return {"kind": "ceiling", "low": None, "high": v,
                    "unit": "%" if m.group(2) else "x"}

    if metric == "net_debt_ebitda":
        m = POINT_X_RE.search(s)
        if m:
            v = _f(m.group(1))
            if v is not None:
                return {"kind": "point", "low": v, "high": v, "unit": "x"}

    if money_metric:
        m = MONEY_RE.search(s)
        if m:
            v = _f(m.group(1))
            if v is not None:
                return {"kind": "point", "low": v, "high": v,
                        "unit": (m.group(0).split()[0] or "").upper()}

    m = POINT_PCT_RE.search(s)
    if m:
        v = _f(m.group(1))
        if v is not None:
            return {"kind": "point", "low": v, "high": v, "unit": "%"}
    return None


def quant_str(q):
    if not q:
        return "n/a"
    u = q["unit"]
    if q["kind"] == "range":
        return "%g-%g%s" % (q["low"], q["high"], u if u != "%" else "%")
    if q["kind"] == "floor":
        return ">= %g%s" % (q["low"], u)
    if q["kind"] == "ceiling":
        return "<= %g%s" % (q["high"], u)
    return "%g%s" % (q["low"], u)


# ---------------------------------------------------------------------------
# Guidance detection in release prose
# ---------------------------------------------------------------------------

# A guidance sentence must carry a cue that is genuinely forward-looking, or
# that explicitly names a promise. "For the full year 2024 net revenue growth is
# 14.7 percent" is a RESULT, and an earlier draft of this file filed it as
# guidance because it matched "for the full year". Period words now only DATE a
# statement; they never qualify one as a promise.
GUIDANCE_CUE = re.compile(
    r"\bguidance\b|\bguide[sd]?\b|\boutlook\b|\bforecast\b|prognos|utsikter|"
    r"we\s+expect|expects?\s+(?:a|an|the|to|it)|we\s+estimate|"
    r"estimate[sd]?\s+(?:the|a|an|to)|anticipat\w+|f[oö]rv[aä]ntar|bed[oö]mer|"
    r"our\s+target|we\s+target|targets?\s+(?:of|to|is|are)|m[aå]ls[aä]ttning|"
    r"ambition\s+(?:is|to|of)|aims?\s+to|dividend\s+polic|utdelningspolic|"
    r"remains?\b[^.]{0,30}\b(?:guidance|target)|maintain\w*[^.]{0,30}\bguidance", re.I)

TARGET_CUE = re.compile(
    r"\btargets?\b|\bgoals?\b|m[aå]ls[aä]ttning|\bm[aå]l\b|polic(?:y|ies)|\bambition\b|"
    r"shall\s+(?:amount|be)|should\s+be|must\s+amount|ska\s+(?:uppg[aå]|vara)|"
    r"through\s+a\s+business\s+cycle|over\s+a\s+business\s+cycle|through\s+the\s+cycle|"
    r"konjunkturcykel|per\s+(?:annum|year)\s*,?\s*(?:over|through)", re.I)

# The hardest false positive in this whole file. Evolution writes, in every
# report, "Net revenue growth at constant currency is estimated to be 2.4
# percent" - a RESTATEMENT of the quarter just reported, in constant currency.
# It matched "estimated to", was filed as a growth promise, and then scored as a
# MISS against the full year. Four fabricated misses in one company. A statement
# now qualifies only if it names a promise (guidance / outlook / target / policy
# / ambition) or uses a first-person forward verb.
PROMISE_NOUN = re.compile(
    r"\bguidance\b|\bguide[sd]?\b|\boutlook\b|\bforecast\b|prognos|utsikter|"
    r"\btargets?\b|\bm[aå]ls[aä]ttning\b|\bambition\b|\bpolic(?:y|ies)\b|"
    r"\bgoals?\b|\bm[aå]l\b", re.I)
FORWARD_VERB = re.compile(
    r"we\s+expect|expects?\s+(?:a|an|the|to|it)|we\s+anticipate|we\s+estimate|"
    r"we\s+aim|aims?\s+to|we\s+will|will\s+(?:be|amount|reach|remain|continue|deliver)|"
    r"plan\s+to|intend\s+to|f[oö]rv[aä]ntar|bed[oö]mer|ska\s+(?:uppg[aå]|vara)|"
    r"shall\s+(?:amount|be)|should\s+be|must\s+amount", re.I)

# Quantities that are commentary, not commitments: an FX headwind, a one-off, a
# share of revenue. Scoring these as promises is worse than missing them.
NOT_A_PROMISE = re.compile(
    r"percentage\s+points?|\bpp\b|headwind|tailwind|currency\s+effect|fx\s+effect|"
    r"impact\s+of|one-?off|non-?recurring|settlement|fine\b|tax\s+rate|"
    r"than\s+(?:anticipated|expected|guided)|share\s+capital|number\s+of\s+shares", re.I)

WITHDRAWN_CUE = re.compile(
    r"withdraw\w*|suspend\w*|no\s+longer\s+(?:provide|give|issue)|retract\w*|"
    r"drar\s+tillbaka|dras\s+tillbaka|[aå]terkalla\w*", re.I)

# Wording that makes a stated figure incomparable to an IFRS-reported one.
ADJUSTED_CUE = re.compile(
    r"adjusted|underlying|organic|at\s+fixed\s+exchange\s+rates|constant\s+currency|"
    r"excluding\s+currency|justerad|organisk|before\s+items\s+affecting", re.I)

# Sentences that only look back. Kept out of the guidance history so a
# retrospective mention is not filed as a fresh promise... except that the
# quantities are still useful, so these become "restatements" (see collapse()).
RETROSPECTIVE_CUE = re.compile(
    r"\bwas\b|\bwere\b|amounted\s+to|amounts\s+to|came\s+in|delivered|"
    r"we\s+(?:reached|achieved|exceeded|met)|reported\s+(?:a|an)\b", re.I)


def find_period(sentence, default_label):
    """What period does the statement apply to?

    Returns (label, inferred). `inferred` is True when the sentence itself named
    no period and the report's own period was substituted - the reader has to
    know which of the two happened.
    """
    s = sentence
    m = re.search(r"(?:full[\s-]?year|financial\s+year|fiscal\s+year|FY)\s*"
                  r"(\d{4})\s*/\s*(\d{2,4})", s, re.I)
    if m:
        b = m.group(2)
        return "FY%s/%s" % (m.group(1), b if len(b) == 4 else m.group(1)[:2] + b), False
    m = re.search(r"(?:for|during|in)\s+(?:the\s+)?(?:full[\s-]?year|financial\s+year|FY)?\s*"
                  r"\b(20\d{2})\b", s, re.I)
    if m:
        return "FY%s" % m.group(1), False
    m = re.search(r"\b(20\d{2})\s*(?:full[\s-]?year\s*)?guidance\b|"
                  r"\bguidance\s+(?:of|for)[^.]{0,40}?\b(20\d{2})\b", s, re.I)
    if m:
        return "FY%s" % (m.group(1) or m.group(2)), False
    if re.search(r"(?:through|over)\s+(?:a|the)\s+business\s+cycle|through\s+the\s+cycle|"
                 r"konjunkturcykel", s, re.I):
        return "through the cycle", False
    m = re.search(r"(?:stretching\s+to|by|to)\s+(20[23]\d)\b", s)
    if m:
        return "by %s" % m.group(1), False
    m = re.search(r"\b(first|second|third|fourth)\s+quarter\b", s, re.I)
    if m:
        n = {"first": 1, "second": 2, "third": 3, "fourth": 4}[m.group(1).lower()]
        year = re.sub(r"[^0-9]", "", default_label)[:4] or "?"
        return "%s Q%d" % (year, n), False
    if re.search(r"annually|per\s+(?:annum|year)|[aå]rligen", s, re.I):
        return "per year (standing)", False
    return default_label, True


def scan_guidance(text, default_period, meta):
    """Pull forward-looking quantified statements out of one release body."""
    rows = []
    for s in sentences(text):
        withdrawn = bool(WITHDRAWN_CUE.search(s)) and bool(
            re.search(r"guidance|outlook|forecast|prognos|target|m[aå]l", s, re.I))
        if NOT_A_PROMISE.search(s) and not withdrawn:
            continue
        cue = (PROMISE_NOUN.search(s) or FORWARD_VERB.search(s)) and (
            GUIDANCE_CUE.search(s) or TARGET_CUE.search(s))
        if not cue and not withdrawn:
            continue
        metric = classify_metric(s)
        if metric is None and not withdrawn:
            continue
        # Only look for the number FROM the promise cue onwards (with a short
        # lead-in). "brands show growth of about 3 percent ... our ambition is to
        # deliver stronger growth" would otherwise attach the reported 3% to the
        # ambition and invent a target nobody stated.
        window = s[max(0, cue.start() - 60):] if hasattr(cue, "start") else s
        q = parse_quant(window, metric) if metric else None
        if q is None and not withdrawn:
            continue
        period, inferred = find_period(s, default_period)
        # "We adopted a dividend policy at the time of our IPO in 2015 ..." -
        # the year names when the policy was set, not the period it governs. A
        # forward statement never applies to a year already two years closed.
        m_year = re.match(r"FY(20\d{2})", period)
        if m_year and int(m_year.group(1)) < int(meta["date"][:4]) - 1:
            period, inferred = "per year (standing)", True
        rows.append({
            "date": meta["date"][:10],
            "metric": metric or "(unspecified)",
            "quant": q,
            "applies_to": period,
            "period_inferred": inferred,
            "withdrawn": withdrawn,
            "adjusted_basis": bool(ADJUSTED_CUE.search(s)),
            "retrospective": bool(RETROSPECTIVE_CUE.search(s)),
            "title": meta.get("title"),
            "url": meta.get("url"),
            "sentence": re.sub(r"\s+", " ", s),
            "kind": "guidance",
        })
    return rows


def scan_targets(text, url):
    """Pull standing financial targets out of an IR page."""
    rows = []
    for s in sentences(text):
        if not TARGET_CUE.search(s):
            continue
        metric = classify_metric(s)
        if metric is None:
            continue
        q = parse_quant(s, metric)
        if q is None:
            continue
        # Articles of association and share-capital boilerplate use the same
        # "shall amount to" phrasing; they carry no financial metric, so the
        # metric test above already removes them. This is a second guard for
        # option-programme pages, which do mention margins in passing.
        if re.search(r"share\s+capital|warrant|option\s+programme|incitamentsprogram",
                     s, re.I):
            continue
        rows.append({
            "metric": metric,
            "quant": q,
            "horizon": find_period(s, "standing target")[0],
            "adjusted_basis": bool(ADJUSTED_CUE.search(s)),
            "url": url,
            "sentence": re.sub(r"\s+", " ", s),
            "kind": "target",
        })
    return rows


# ---------------------------------------------------------------------------
# Delivered outcomes, taken from the company's own later report
#
# This is the comparison that actually matters: the year-end report states the
# outcome using the SAME definition the guidance used ("adjusted EBITDA margin"
# means the same thing in both). ESEF is the fallback and is flagged as a
# different basis.
# ---------------------------------------------------------------------------

ANNUAL_HEAD = re.compile(
    r"^\s*[•*-]?\s*(?:full[\s-]?year|helr?[aå]r|january\s*[-–]\s*december|"
    r"jan(?:uary)?\s*[-–]\s*dec(?:ember)?|1\s+\w+\s+\d{4}\s*[-–]\s*\d{1,2}\s+\w+\s+\d{4}|"
    r"\d{1,2}\s+\w+\s+\d{4}\s*[-–]\s*\d{1,2}\s+\w+\s+\d{4})", re.I)
QUARTER_HEAD = re.compile(
    r"^\s*[•*-]?\s*(?:first|second|third|fourth|f[oö]rsta|andra|tredje|fj[aä]rde)\s+"
    r"(?:quarter|kvartalet)|^\s*Q[1-4]\b", re.I)

# ORDER IS LOAD-BEARING. Evolution's FY2024 report states two EBITDA margins in
# the same block: 70.5% including other operating revenues and 68.4% excluding
# them. Guidance of "69-71 percent" refers to the second. Reading the first
# turned a MISS into a MET - the exact silent failure this file exists to avoid.
# The adjusted line is therefore tried first, and where both exist the other
# figure is disclosed alongside rather than discarded.
ACTUAL_PATTERNS = [
    ("ebitda_margin",
     r"adjusted\s+ebitda[^\n]{0,160}?margin\s+of\s+(?P<v>" + NUM + r")\s*" + PCT),
    ("ebitda_margin",
     r"ebitda[^\n]{0,160}?margin\s+of\s+(?P<v>" + NUM + r")\s*" + PCT),
    ("ebitda_margin",
     r"ebitda[\s-]*margin[^\n]{0,60}?(?:of|was|amounted\s+to|is)\s+(?P<v>" + NUM + r")\s*" + PCT),
    ("ebita_margin",
     r"ebita\s*margin\s+of\s+(?P<v>" + NUM + r")\s*" + PCT),
    ("ebita_margin",
     r"adjusted\s+ebita\s+margin\s+(?P<v>" + NUM + r")\s*" + PCT),
    ("ebit_margin",
     r"operating\s+margin\s+of\s+(?P<v>" + NUM + r")\s*" + PCT),
    ("ebit_margin",
     r"adjusted\s+ebit\s+margin\s+(?P<v>" + NUM + r")\s*" + PCT),
    ("profit_growth",
     r"\(ebita\)[^\n]{0,90}?increased\s+by\s+(?P<v>" + NUM + r")\s*" + PCT),
    ("profit_growth",
     r"ebita[^\n]{0,90}?(?:increased|grew|rose)\s+by\s+(?P<v>" + NUM + r")\s*" + PCT),
    ("profit_growth_neg",
     r"ebita[^\n]{0,90}?(?:decreased|declined|fell)\s+by\s+(?P<v>" + NUM + r")\s*" + PCT),
    ("growth",
     r"net\s+(?:sales|revenues?)[^\n]{0,90}?(?:increased|grew|rose)\s+(?:by\s+)?(?P<v>"
     + NUM + r")\s*" + PCT),
    ("growth_neg",
     r"net\s+(?:sales|revenues?)[^\n]{0,90}?(?:decreased|declined|fell)\s+(?:by\s+)?(?P<v>"
     + NUM + r")\s*" + PCT),
    ("organic_growth",
     r"(?P<v>" + NUM + r")\s*" + PCT + r"[^\n]{0,30}?(?:was|of\s+which)\s+organic"),
    ("organic_growth",
     r"organic(?:ally)?[^\n]{0,50}?(?:growth\s+)?(?:of|by|was)\s+(?P<v>" + NUM + r")\s*" + PCT),
    ("p_wc",
     r"(?:p\s*/\s*wc|return\s+on\s+working\s+capital)[^\n]{0,60}?(?:amounted\s+to|was|of)\s+"
     r"(?P<v>" + NUM + r")\s*" + PCT),
    ("roe",
     r"return\s+on\s+equity[^\n]{0,50}?(?:amounted\s+to|was|of)\s+(?P<v>" + NUM + r")\s*" + PCT),
    ("roce",
     r"(?:roce|roic|return\s+on\s+capital\s+employed)[^\n]{0,60}?"
     r"(?:amounted\s+to|was|of)\s+(?P<v>" + NUM + r")\s*" + PCT),
    ("equity_ratio",
     r"equity\s+ratio[^\n]{0,50}?(?:amounted\s+to|was|of)\s+(?P<v>" + NUM + r")\s*" + PCT),
]


def annual_block(text):
    """Return the part of a report body that describes the FULL YEAR.

    Report releases lead with the quarter and then repeat for the year. Reading
    the quarter figure as the annual outcome would be a silent, plausible-looking
    error, so the block boundaries are found explicitly and the function returns
    nothing rather than guessing.
    """
    lines = (text or "").splitlines()
    start = None
    # Only the opening summary is scanned. Later prose repeats period names
    # ("in January-December we ...") and would drag the block boundary down into
    # the CEO letter, where figures are commentary rather than the statement.
    for i, line in enumerate(lines[:60]):
        if ANNUAL_HEAD.match(line) and len(line.strip()) < 90:
            start = i
    if start is None:
        return None
    out = []
    for line in lines[start + 1:]:
        if QUARTER_HEAD.match(line) and len(line.strip()) < 90:
            break
        if re.match(r"^\s*(CEO|President|Comments?\s+from|This\s+information|Stockholm,)",
                    line, re.I):
            break
        out.append(line)
    return "\n".join(out) if out else None


def fiscal_label(title, text, pub_date):
    """Best-effort fiscal-period label for a report release.

    Addtech runs April-March and labels its year "1 April 2025 - 31 March 2026";
    Evolution runs the calendar year. The label is taken from the document's own
    words where possible so the two never get conflated.
    """
    hay = (title or "") + "\n" + (text or "")[:1200]
    m = re.search(r"1\s+april\s+(\d{4})\s*[-–]\s*31\s+march\s+(\d{4})", hay, re.I)
    if m:
        return "FY%s/%s" % (m.group(1), m.group(2))
    m = re.search(r"1\s+april\s+(\d{4})\s*[-–]\s*31\s+mars\s+(\d{4})", hay, re.I)
    if m:
        return "FY%s/%s" % (m.group(1), m.group(2))
    m = re.search(r"january\s*[-–]\s*december\s+(\d{4})", hay, re.I)
    if m:
        return "FY%s" % m.group(1)
    m = re.search(r"year[\s-]?end\s+report\s+(\d{4})", hay, re.I)
    if m:
        return "FY%s" % m.group(1)
    m = re.search(r"\b(20\d{2})\b", title or "")
    if m:
        return "FY%s" % m.group(1)
    # A year-end release published in Jan-May almost always reports the previous
    # calendar year.
    try:
        d = datetime.date.fromisoformat(pub_date[:10])
        return "FY%d" % (d.year - 1 if d.month <= 5 else d.year)
    except ValueError:
        return "FY?"


def quarter_block(text):
    """The part of a report body that describes the QUARTER just closed."""
    lines = (text or "").splitlines()
    start = None
    for i, line in enumerate(lines[:40]):
        if QUARTER_HEAD.match(line) and len(line.strip()) < 90:
            start = i
            break
    if start is None:
        return None
    out = []
    for line in lines[start + 1:]:
        if ANNUAL_HEAD.match(line) and len(line.strip()) < 90:
            break
        if re.match(r"^\s*(CEO|President|Comments?\s+from|This\s+information|Stockholm,)",
                    line, re.I):
            break
        out.append(line)
    return "\n".join(out) if out else None


# Most specific first: "Year-end report January-December 2025" must resolve to
# Q4, not to Q1 via a stray "January".
QUARTER_OF_TITLE = [
    (re.compile(r"year[\s-]?end|bokslutskommunik|fourth\s+quarter|\bQ4\b|"
                r"january\s*[-–]\s*december", re.I), 4),
    (re.compile(r"january\s*[-–]\s*september|third\s+quarter|\bQ3\b", re.I), 3),
    (re.compile(r"january\s*[-–]\s*june|second\s+quarter|half[\s-]?year|\bQ2\b", re.I), 2),
    (re.compile(r"january\s*[-–]\s*march|first\s+quarter|\bQ1\b", re.I), 1),
]


def quarter_of(title):
    """Which quarter does this report's quarter section cover?

    Used only to key quarter-scoped guidance to a quarter-scoped outcome. A
    report whose title does not say is left unkeyed rather than assumed.
    """
    for pattern, n in QUARTER_OF_TITLE:
        if pattern.search(title or ""):
            return n
    return None


def _match_actuals(block, meta, basis_note):
    # reflow(), not a naive newline squash: report bullets wrap onto lines that
    # begin with a digit ("  1,365.7 million"), so a rule keyed on lower-case
    # continuations leaves the figure stranded from its label.
    flat = "\n".join(reflow(block))
    out = {}
    for metric, pattern in ACTUAL_PATTERNS:
        neg = metric.endswith("_neg")
        key = metric[:-4] if neg else metric
        if key in out:
            continue
        m = re.search(pattern, flat, re.I)
        if not m:
            continue
        v = _f(m.group("v"))
        if v is None:
            continue
        line = flat[max(0, m.start() - 90):m.end() + 40]
        out[key] = {"value": -v if neg else v,
                    "unit": "%",
                    "basis": basis_note,
                    "source_line": re.sub(r"\s+", " ", line).strip(),
                    "source_url": meta.get("url"),
                    "reported_on": meta["date"][:10]}
    _disclose_alternatives(flat, out)
    return out


ALTERNATIVE_PATTERNS = {
    "ebitda_margin": r"ebitda[^\n]{0,160}?margin\s+of\s+(?P<v>" + NUM + r")\s*" + PCT,
    "ebita_margin": r"ebita\s*margin\s+of\s+(?P<v>" + NUM + r")\s*" + PCT,
    "ebit_margin": r"operating\s+margin\s+of\s+(?P<v>" + NUM + r")\s*" + PCT,
}


def _disclose_alternatives(flat, out):
    """Say so when the same section reports a second figure for the same metric.

    A report that carries both an adjusted and an unadjusted margin leaves the
    reader to decide which one the promise meant. Hiding the second number would
    make an arbitrary choice look like a fact.
    """
    for metric, pattern in ALTERNATIVE_PATTERNS.items():
        if metric not in out:
            continue
        chosen = out[metric]["value"]
        others = sorted({_f(m.group("v")) for m in re.finditer(pattern, flat, re.I)}
                        - {chosen, None})
        if others:
            out[metric]["basis"] += ("; the same section also reports %s%% for this "
                                     "metric - check which one the promise meant"
                                     % ", ".join("%g" % o for o in others))


def _quarter_key(period_label, title):
    """Key a quarter outcome the same way find_period() keys quarter guidance.

    Both sides must build the string identically or the join silently never
    happens, which reads on the page as "no outcome reported" - a lie.
    """
    n = quarter_of(title)
    year = re.sub(r"[^0-9]", "", period_label or "")[:4]
    return "%s Q%d" % (year, n) if (n and len(year) == 4) else None


def scan_actuals(text, meta):
    """Delivered figures for the full year covered by this report."""
    block = annual_block(text)
    if not block:
        return {}
    return _match_actuals(block, meta,
                          "company report, full-year section (definition as stated)")


def scan_quarter_actuals(text, meta):
    """Delivered figures for the quarter covered by this report."""
    block = quarter_block(text)
    if not block:
        return {}
    return _match_actuals(block, meta,
                          "company report, quarter section (definition as stated)")


# ---------------------------------------------------------------------------
# Delivered outcomes from ESEF - IFRS basis, explicitly a different measure
# ---------------------------------------------------------------------------

def esef_actuals(name, country="SE", filings=5):
    """Compute IFRS ratios per fiscal year. Returns (dict, note)."""
    try:
        hits = ESEF.search_index(name, country)
    except SystemExit as e:
        return {}, str(e)
    if not hits:
        return {}, "no ESEF filer indexed in %s matching %r" % (country, name)
    hit = sorted(hits, key=lambda h: h["latest"], reverse=True)[0]
    try:
        flist = ESEF.list_filings(hit["lei"], filings)
    except SystemExit as e:
        return {}, str(e)
    merged = {}
    for f in flist:
        try:
            doc = ESEF.get_json(ESEF.FILINGS_BASE + f["json_url"])
        except SystemExit:
            continue
        facts = ESEF.extract(doc)
        for metric, names in ESEF.CONCEPTS.items():
            for period, (val, _unit, _c) in ESEF.pick(facts, names,
                                                      metric in ESEF.DURATION).items():
                merged.setdefault(metric, {}).setdefault(period, val)

    def get(metric, period):
        return (merged.get(metric) or {}).get(period)

    periods = sorted({p for m in merged.values() for p in m})
    out = {}
    for p in periods:
        year = p[:4]
        key = "FY%s" % year
        row = {}
        rev, prev_rev = get("revenue", p), None
        for q in periods:
            if q < p and q[5:] == p[5:]:
                prev_rev = get("revenue", q)
        if rev and prev_rev:
            row["growth"] = 100.0 * (rev / prev_rev - 1.0)
        op = get("operating_income", p)
        if rev and op is not None:
            row["ebit_margin"] = 100.0 * op / rev
        ni, eq = get("net_income", p), get("equity", p)
        if ni is not None and eq:
            row["roe"] = 100.0 * ni / eq
        div = get("dividends_paid", p)
        if div and ni:
            # Cash DIVIDENDS PAID during a year settle the PRIOR year's declared
            # dividend. Against a payout policy that is off by one year; it is
            # still reported, with the caveat attached, rather than dropped.
            row["payout"] = 100.0 * abs(div) / ni
            row["_payout_caveat"] = True
        debt = sum(x for x in (get("borrowings", p), get("borrowings_current", p),
                               get("lease_liabilities", p)) if x)
        cash = get("cash", p)
        da = get("depreciation_amort", p)
        if debt and cash is not None and op is not None and da:
            ebitda = op + da
            if ebitda:
                row["net_debt_ebitda"] = (debt - cash) / ebitda
        ta = get("total_assets", p)
        cl = get("current_liabilities", p)
        if op is not None and ta and cl:
            row["roce"] = 100.0 * op / (ta - cl)
        if eq and ta:
            row["equity_ratio"] = 100.0 * eq / ta
        payout_caveat = row.pop("_payout_caveat", False)
        for metric, value in row.items():
            basis = "ESEF / IFRS as reported - NOT the company's adjusted measure"
            if metric == "payout" and payout_caveat:
                basis += ("; cash dividends PAID in the year, i.e. the prior "
                          "year's declaration")
            out.setdefault(key, {})[metric] = {
                "value": value,
                "unit": "x" if metric == "net_debt_ebitda" else "%",
                "basis": basis,
                "source_line": "computed from tagged XBRL facts, period ending %s" % p,
                "source_url": "%s/api/filings?filter[entity.identifier]=%s"
                              % (ESEF.FILINGS_BASE, hit["lei"]),
                "reported_on": p}
    return out, "ESEF filer %s (%s), latest tagged FY %s" % (
        hit["name"], hit["lei"], hit["latest"])


# ---------------------------------------------------------------------------
# Release archives
# ---------------------------------------------------------------------------

def mfn_archive(slug, want=500):
    """Deep release archive for an MFN issuer.

    /a/<slug>.json is hard-capped near 30 items and ignores offset, which is far
    too shallow for a guidance history. The undocumented /all/a.json accepts an
    `author` filter and honours large limits, reaching back to 2018 for some
    issuers. Verified 2026-08-31; if it ever stops working the shallow feed is
    used instead and the depth limitation is printed.
    """
    items, note = [], ""
    try:
        data = MFN.fetch("/all/a.json", query="", author=slug, limit=want)
        items = [MFN.flatten(i) for i in (data.get("items") or [])]
    except SystemExit:
        items = []
    if not items:
        try:
            data = MFN.fetch("/a/%s.json" % urllib.parse.quote(slug), limit=40)
            items = [MFN.flatten(i) for i in (data.get("items") or [])]
            note = ("MFN deep archive unavailable; only the ~30 most recent "
                    "releases were read, so older guidance is missing.")
        except SystemExit:
            items = []
    items = [i for i in items if i.get("slug") == slug]
    return items, note


def cision_archive(slug, pages=6, max_bodies=22):
    """Release archive for a Cision issuer.

    The Cision RSS carries only a truncated description, so each interesting
    release page is fetched for its body. That is expensive, so the fetch list
    is restricted to reports and to titles that hint at guidance - which means
    a guidance statement buried in an unrelated release will be missed.
    """
    try:
        rows = CIS.releases(slug, pages=pages, english=True)
    except SystemExit:
        rows = []
    interesting = re.compile(
        r"report|outlook|guidance|target|capital\s+markets\s+day|profit\s+warning|"
        r"trading\s+update|financial\s+goal", re.I)
    picked = [r for r in rows if r["is_report"] or interesting.search(r["title"] or "")]
    out = []
    for r in picked[:max_bodies]:
        body = to_text(http_html(r["url"]))
        # Cision wraps the release in site chrome; drop everything after the
        # MAR boilerplate so nav text is not scanned as management prose.
        body = re.split(r"Tags:\s*$", body, flags=re.M)[0]
        out.append({"date": _cision_date(r["date"]), "title": r["title"],
                    "url": r["url"], "text": body, "lang": "en",
                    "is_report": r["is_report"], "attachments": []})
    return out


def _cision_date(rfc):
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%a, %d %b %Y %H:%M %Z", "%a, %d %b %Y"):
        try:
            return datetime.datetime.strptime(rfc.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    m = re.search(r"(\d{1,2})\s+(\w{3})\s+(20\d{2})", rfc or "")
    if m:
        try:
            return datetime.datetime.strptime(" ".join(m.groups()),
                                              "%d %b %Y").date().isoformat()
        except ValueError:
            pass
    return (rfc or "")[:10]


# ---------------------------------------------------------------------------
# IR-site target discovery
# ---------------------------------------------------------------------------

DISTRIBUTOR_DOMAINS = {
    "mfn.se", "cision.com", "news.cision.com", "mb.cision.com", "inderes.com",
    "events.inderes.com", "twitter.com", "x.com", "linkedin.com", "facebook.com",
    "youtube.com", "instagram.com", "nasdaq.com", "nasdaqomxnordic.com",
    "google.com", "microsoft.com", "outlook.com", "europa.eu", "sec.gov",
    "safelinks.protection.outlook.com", "globenewswire.com", "gov.uk",
    "wikipedia.org", "apple.com", "vimeo.com", "financialhearings.com",
}
BARE_DOMAIN = re.compile(r"\b((?:[a-z0-9][a-z0-9-]{0,30}\.)+[a-z][a-z0-9-]{1,14})\b", re.I)
FILEY = re.compile(r"\.(?:pdf|jpe?g|png|gif|html?|php|aspx|js|css|xml|json|zip|docx?)$", re.I)


def guess_domains(texts, company_name="", limit=4):
    """The issuer's own web domain, taken from its releases rather than assumed.

    Every Swedish release ends with an "About us" paragraph naming the company
    site (www.addtech.com, home.sandvik/investors). Reading it out of the primary
    source beats hardcoding a per-company table that silently rots.

    Raw frequency is not enough on its own: a company that files many flagging
    notices mentions its share registrar (computershare.se) more often than
    itself. So a domain that contains the company's own name outranks a more
    frequent stranger.
    """
    stem = re.sub(r"[^a-z]", "", (company_name or "").lower())
    stem = re.sub(r"(ab|publ|group|holding|aktiebolag)$", "", stem) or stem
    counts = collections.Counter()
    for t in texts:
        for m in BARE_DOMAIN.finditer(t or ""):
            d = m.group(1).lower().strip(".")
            if d in DISTRIBUTOR_DOMAINS or FILEY.search(d):
                continue
            if any(d.endswith("." + b) for b in DISTRIBUTOR_DOMAINS):
                continue
            if d.count(".") > 3 or len(d) < 6:
                continue
            if re.match(r"^\d", d):
                continue
            # Accept a conventional TLD, or a brand TLD such as home.sandvik.
            if not re.search(r"\.(?:se|com|net|org|eu|io|ai|co\.uk|dk|no|fi|de)$", d) \
                    and not re.match(r"^[a-z][a-z0-9-]{2,}\.[a-z]{4,}$", d):
                continue
            counts[d] += 1
    scored = []
    for d, n in counts.items():
        flat = re.sub(r"[^a-z]", "", d)
        bonus = 1000 if (stem and len(stem) >= 4 and stem[:6] in flat) else 0
        scored.append((bonus + n, d))
    return [d for _, d in sorted(scored, reverse=True)[:limit]]


NAV_HINT = re.compile(
    r"financial[\s-]*target|financial[\s-]*goal|finansiella[\s-]*m[aå]l|"
    r"overarching[\s-]*target|[oö]vergripande[\s-]*m[aå]l|targets?[\s-]*and[\s-]*outcome|"
    r"m[aå]l[\s-]*och[\s-]*utfall|investment[\s-]*case|invest[\s-]*in[\s-]|"
    r"as[\s-]*an[\s-]*investment|som[\s-]*investering|financial[\s-]*objective|"
    r"dividend[\s-]*polic|utdelningspolic|this[\s-]*is[\s-]", re.I)
IR_HINT = re.compile(r"/investor|/investerare|investor-relations|/ir/|/about", re.I)
LINK_RE = re.compile(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
SKIP_PAGE = re.compile(r"articles-of-association|bolagsordning|/press-releases|/pressmeddelanden|"
                       r"/calendar|/kalend|image-gallery|/contact", re.I)


def crawl_targets(domain, budget=16):
    """Two-level crawl of an issuer's IR pages, looking for the targets block.

    Deliberately shallow and keyword-steered. Nothing here renders JavaScript,
    so an IR site that ships its targets only as a client-side component will
    return nothing - which is reported as DATA NOT AVAILABLE, not filled in.
    """
    seeds = ["https://%s/en/investors/" % domain,
             "https://%s/investors/" % domain,
             "https://%s/en/investor-relations/" % domain,
             "https://%s/investerare/" % domain,
             "https://%s/" % domain]
    fetched, seen, queue, pages = 0, set(), [], []
    for seed in seeds:
        if fetched >= 4:
            break
        markup = http_html(seed)
        if markup is None:
            continue
        fetched += 1
        seen.add(seed.rstrip("/"))
        pages.append((seed, markup))
        host = urllib.parse.urlparse(seed).netloc
        for href, label in LINK_RE.findall(markup):
            full = urllib.parse.urljoin(seed, href).split("#")[0].split("?")[0].rstrip("/")
            if urllib.parse.urlparse(full).netloc != host or SKIP_PAGE.search(full):
                continue
            anchor = html.unescape(re.sub(r"<[^>]+>", " ", label)).strip()
            score = 2 if (NAV_HINT.search(full) or NAV_HINT.search(anchor)) else (
                1 if IR_HINT.search(full) else 0)
            if score:
                queue.append((-score, full))
    for _score, url in sorted(set(queue)):
        if fetched >= budget:
            break
        if url in seen:
            continue
        seen.add(url)
        markup = http_html(url)
        if markup is None:
            continue
        fetched += 1
        pages.append((url, markup))

    rows, seen_sentence = [], set()
    for url, markup in pages:
        for row in scan_targets(to_text(markup), url):
            key = row["sentence"].lower()
            if key in seen_sentence:
                continue
            seen_sentence.add(key)
            rows.append(row)
    return rows, fetched, sorted(u for u, _ in pages)


# ---------------------------------------------------------------------------
# Collapse repeated statements into a history
# ---------------------------------------------------------------------------

def collapse(rows):
    """Group identical promises so a reiteration is visible as a reiteration.

    Key is (metric, period, the number itself). The earliest date is when the
    promise was first made; later dates are reiterations. A DIFFERENT number for
    the same metric and period is a separate row - which is exactly how a
    quietly revised target becomes visible.
    """
    buckets = collections.OrderedDict()
    for r in sorted(rows, key=lambda x: (x["date"], x["metric"])):
        q = r["quant"]
        key = (r["metric"], r["applies_to"],
               None if q is None else (q["kind"], q["low"], q["high"], q["unit"]),
               r["withdrawn"])
        b = buckets.get(key)
        if b is None:
            b = dict(r)
            b["first_said"] = r["date"]
            b["repeated_on"] = []
            buckets[key] = b
        else:
            b["repeated_on"].append(r["date"])
            # Prefer a forward-looking sentence over a retrospective one as the
            # quotable evidence for the promise.
            if b.get("retrospective") and not r.get("retrospective"):
                b["sentence"], b["url"], b["title"] = r["sentence"], r["url"], r["title"]
                b["retrospective"] = False
    return list(buckets.values())


def detect_changes(history):
    """Same metric, same period, different number, stated on different dates."""
    by_key = collections.defaultdict(list)
    for r in history:
        if r["quant"] is None:
            continue
        by_key[(r["metric"], r["applies_to"])].append(r)
    changes = []
    for (metric, period), rows in by_key.items():
        rows = sorted(rows, key=lambda x: x["first_said"])
        for a, b in zip(rows, rows[1:]):
            qa, qb = a["quant"], b["quant"]
            mid_a = (qa["low"] if qa["low"] is not None else qa["high"])
            mid_b = (qb["low"] if qb["low"] is not None else qb["high"])
            if qa["kind"] == qb["kind"] == "range":
                mid_a = (qa["low"] + qa["high"]) / 2.0
                mid_b = (qb["low"] + qb["high"]) / 2.0
            direction = "LOWERED" if (mid_b is not None and mid_a is not None
                                      and mid_b < mid_a) else (
                "RAISED" if (mid_b is not None and mid_a is not None and mid_b > mid_a)
                else "RESTATED")
            if metric in LOWER_IS_BETTER and direction in ("LOWERED", "RAISED"):
                direction = "LOOSENED" if direction == "RAISED" else "TIGHTENED"
            if direction == "RESTATED" and quant_str(qa) == quant_str(qb):
                continue
            changes.append({"metric": metric, "applies_to": period,
                            "from": quant_str(qa), "to": quant_str(qb),
                            "from_date": a["first_said"], "to_date": b["first_said"],
                            "direction": direction,
                            "from_url": a["url"], "to_url": b["url"]})
    # A period whose guidance was withdrawn is the sharpest kind of change.
    for r in history:
        if r["withdrawn"]:
            changes.append({"metric": r["metric"], "applies_to": r["applies_to"],
                            "from": "(previously guided)", "to": "WITHDRAWN",
                            "from_date": "", "to_date": r["first_said"],
                            "direction": "WITHDRAWN",
                            "from_url": "", "to_url": r["url"]})
    return sorted(changes, key=lambda c: c["to_date"])


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

def judge(quant, metric, actual):
    """FACT: did the reported figure satisfy the stated number?

    Returns (verdict, explanation). No opinion is formed here - only arithmetic.
    """
    if quant is None:
        return "NO NUMBER", "statement carries no quantified target"
    if actual is None:
        return "NO OUTCOME", "no reported figure found for this period"
    v = actual["value"]
    if quant["unit"] != actual.get("unit"):
        return "NOT COMPARABLE", "target in %s, outcome in %s" % (
            quant["unit"], actual.get("unit"))
    lower_better = metric in LOWER_IS_BETTER
    if quant["kind"] == "range":
        if quant["low"] <= v <= quant["high"]:
            return "MET (in range)", "%.4g within %g-%g" % (v, quant["low"], quant["high"])
        if v > quant["high"]:
            return ("MISS (above ceiling)" if lower_better else "BEAT (above range)",
                    "%.4g vs top of range %g" % (v, quant["high"]))
        return ("BEAT (below range)" if lower_better else "MISS (below range)",
                "%.4g vs bottom of range %g" % (v, quant["low"]))
    if quant["kind"] == "floor":
        return (("MET" if v >= quant["low"] else "MISS"),
                "%.4g vs floor %g" % (v, quant["low"]))
    if quant["kind"] == "ceiling":
        return (("MET" if v <= quant["high"] else "MISS"),
                "%.4g vs ceiling %g" % (v, quant["high"]))
    target = quant["low"]
    if metric in NEUTRAL:
        return "AT/AROUND" if abs(v - target) <= max(1.0, 0.1 * abs(target)) else (
            "ABOVE" if v > target else "BELOW"), "%.4g vs stated %g" % (v, target)
    if lower_better:
        return ("MET" if v <= target else "MISS"), "%.4g vs %g" % (v, target)
    return ("MET" if v >= target else "MISS"), "%.4g vs %g" % (v, target)


def pick_actual(metric, period, report_actuals, esef, adjusted_claim):
    """Company's own reported figure first; ESEF second, flagged."""
    got = (report_actuals.get(period) or {}).get(metric)
    if got:
        return got, False
    got = (esef.get(period) or {}).get(metric)
    if got:
        # An "adjusted" / "organic" / "fixed-FX" promise cannot be scored against
        # an IFRS-reported number. Return it as evidence, marked incomparable.
        # A dividend payout policy is ALWAYS incomparable to the ESEF figure: the
        # policy is a share of the year's profit as declared, while the cash-flow
        # statement records what was paid out for the previous year.
        return got, bool(adjusted_claim) or metric == "payout"
    return None, False


def latest_metric_actual(metric, report_actuals, esef, adjusted_claim):
    """The most recent single-year actual for a metric, regardless of period key.

    A "through the cycle" or "per year (standing)" target is never keyed the
    same way as a reported figure (which is always keyed to one fiscal year),
    so pick_actual() never finds it a match - that gap would otherwise make
    Sandvik's textbook cycle targets (7% growth, 20-22% adjusted EBITA margin,
    ~1x net debt/EBITDA) permanently NOT SCORABLE even though the delivered
    figures exist. A multi-year target is not settled by any single year, so
    this is offered as ONE YEAR OF EVIDENCE toward it (see the
    EVIDENCE ONLY verdict below), never as a pass/fail on the cycle itself.
    """
    def newest(d):
        best_year, best = None, None
        for period, bucket in (d or {}).items():
            entry = (bucket or {}).get(metric)
            if not entry:
                continue
            m = re.match(r"^FY(\d{4})", period or "")
            year = m.group(1) if m else "0000"
            if best_year is None or year > best_year:
                best_year, best = year, entry
        return best

    got = newest(report_actuals)
    if got:
        return got, False
    got = newest(esef)
    if got:
        return got, bool(adjusted_claim) or metric == "payout"
    return None, False


# ---------------------------------------------------------------------------
# MANAGEMENT EXECUTION SCORE (spec S12)
#
# Everything above this point answers two separate questions and keeps them
# separate: what was PROMISED (guidance - a claim, never independently
# verified) and what was DELIVERED (an outcome - arithmetic on reported
# figures, a fact). What follows turns the accumulated facts into a single
# number, and does so from evidence only: a pattern of cuts, a moved
# definition, a suspiciously tidy string of beats. The number is a disclosed,
# fixed heuristic over those facts - an OPINION layer, printed with every
# input that fed it so it can be checked, not trusted.
# ---------------------------------------------------------------------------

# Wording that changes what a metric MEANS, not what it is worth. An issuer
# that quietly redefines "organic growth" - adds back acquisitions, drops
# currency, changes the comparability base - has moved the goalposts even if
# the stated number is unchanged. This vocabulary is necessarily partial: it
# catches the qualifiers Swedish/Nordic issuers use most often, not all of them.
DEFINITION_QUALIFIERS = [
    ("constant_currency",
     r"constant\s+currency|fixed\s+exchange\s+rates|excluding\s+currency|at\s+fixed\s+fx"),
    ("organic_only", r"\borganic(?:ally)?\b"),
    ("adjusted", r"\badjusted\b"),
    ("underlying", r"\bunderlying\b"),
    ("excl_ifrs16", r"excl(?:uding)?\.?\s+ifrs\s*16"),
    ("before_items_affecting_comparability",
     r"before\s+items\s+affecting\s+comparability|excluding\s+items\s+affecting\s+comparability"),
    ("incl_acquisitions", r"including\s+acquisitions|inclusive\s+of\s+acquisitions"),
    ("excl_acquisitions", r"excluding\s+acquisitions"),
    ("continuing_operations", r"continuing\s+operations"),
    ("cash_basis", r"cash[\s-]?basis|cash\s+conversion"),
]

# The two horizons a "standing target" (as opposed to one year's guidance) is
# normally expressed as. Definition drift is only meaningful against a target
# an issuer claims is stable over time; a definition legitimately differing
# between FY2023 guidance and FY2025 guidance is not a moved goalpost.
STANDING_PERIODS = {"through the cycle", "per year (standing)"}


def qualifier_fingerprint(sentence):
    """Which definitional qualifiers appear in one sentence, as a frozenset."""
    low = (sentence or "").lower()
    return frozenset(name for name, pattern in DEFINITION_QUALIFIERS
                     if re.search(pattern, low))


def detect_definition_changes(guidance_rows, targets):
    """Same metric, same kind of standing commitment, different wording.

    Compares the qualifier fingerprint of every standing-target statement
    found in release prose, in date order, and - as the most current data
    point - the fingerprint of whatever the IR site states today. A change in
    fingerprint with the underlying number untouched is exactly the case this
    exists to catch: the target reads the same but no longer means the same
    thing. This is heuristic, regex-over-prose extraction, same as the rest of
    this file, and is shown with the two sentences so a misfire is visible.
    """
    points = collections.defaultdict(list)
    for r in guidance_rows:
        if r.get("withdrawn") or r.get("applies_to") not in STANDING_PERIODS:
            continue
        points[r["metric"]].append({
            "date": r["date"], "fingerprint": qualifier_fingerprint(r["sentence"]),
            "sentence": r["sentence"], "url": r["url"]})
    # crawl_targets() reads BOTH the English and the Swedish IR pages; that is
    # one current statement mirrored in two languages, not two observations,
    # and the qualifier vocabulary above is English-only. Comparing the two
    # would manufacture a "redefinition" out of a translation. Only the first
    # target sentence per metric - the earliest page crawled, which is the
    # English seed - is used as "current".
    seen_metric = set()
    for t in (targets or []):
        if t["metric"] in seen_metric:
            continue
        seen_metric.add(t["metric"])
        points[t["metric"]].append({
            "date": "current (IR site)", "fingerprint": qualifier_fingerprint(t["sentence"]),
            "sentence": t["sentence"], "url": t["url"]})

    findings = []
    for metric, pts in points.items():
        pts = sorted(pts, key=lambda p: (p["date"] == "current (IR site)", p["date"]))
        seen = []
        for p in pts:
            if seen and seen[-1]["fingerprint"] == p["fingerprint"]:
                continue
            seen.append(p)
        for a, b in zip(seen, seen[1:]):
            if not a["fingerprint"] and not b["fingerprint"]:
                continue
            findings.append({
                "metric": metric,
                "from_date": a["date"], "to_date": b["date"],
                "from_definition": sorted(a["fingerprint"]) or ["(no qualifier detected)"],
                "to_definition": sorted(b["fingerprint"]) or ["(no qualifier detected)"],
                "from_sentence": a["sentence"], "to_sentence": b["sentence"],
                "from_url": a["url"], "to_url": b["url"],
            })
    return findings


def detect_cut_patterns(history, changes):
    """A pattern, not a single forecasting error (spec S12).

    Two shapes count: the same target cut twice in a row, or a target cut
    within 90 days of being reaffirmed. Both are read off facts already on
    hand - the change list and the repeated_on dates collapse() recorded -
    nothing here is inferred from tone.
    """
    def is_cut(c):
        return c["direction"] in ("LOWERED", "LOOSENED")

    patterns = []
    by_key = collections.defaultdict(list)
    for c in changes:
        if c["direction"] == "WITHDRAWN":
            continue
        by_key[(c["metric"], c["applies_to"])].append(c)
    for (metric, period), lst in by_key.items():
        lst = sorted(lst, key=lambda c: c["to_date"])
        for a, b in zip(lst, lst[1:]):
            if is_cut(a) and is_cut(b):
                patterns.append({
                    "type": "TWO CONSECUTIVE CUTS", "metric": metric, "applies_to": period,
                    "detail": "%s -> %s -> %s" % (a["from"], a["to"], b["to"]),
                    "dates": [a["to_date"], b["to_date"]]})

    by_bucket = {}
    for r in history:
        bkey = (r["metric"], r["applies_to"], quant_str(r["quant"]) if r["quant"] else "withdrawn")
        by_bucket[bkey] = r
    for c in changes:
        if not is_cut(c):
            continue
        bucket = by_bucket.get((c["metric"], c["applies_to"], c["from"]))
        if not bucket or not bucket.get("repeated_on"):
            continue
        try:
            to_d = datetime.date.fromisoformat(c["to_date"])
        except ValueError:
            continue
        for rep in bucket["repeated_on"]:
            try:
                rep_d = datetime.date.fromisoformat(rep)
            except ValueError:
                continue
            if 0 <= (to_d - rep_d).days <= 90:
                patterns.append({
                    "type": "CUT WITHIN 90 DAYS OF REAFFIRMING",
                    "metric": c["metric"], "applies_to": c["applies_to"],
                    "detail": "reaffirmed %s on %s, then cut to %s on %s"
                              % (c["from"], rep, c["to"], c["to_date"]),
                    "dates": [rep, c["to_date"]]})
                break
    return patterns


def _target_reference(quant, lower_better):
    """The numeric bar a BEAT was measured against - mirrors judge()'s logic."""
    kind = quant["kind"]
    if kind == "range":
        return quant["low"] if lower_better else quant["high"]
    if kind == "floor":
        return quant["low"]
    if kind == "ceiling":
        return quant["high"]
    return quant["low"]


def detect_sandbagging(history):
    """FACT: the margin by which every BEAT cleared its stated target.
    OPINION, printed separately: a tight, repeated positive margin on the same
    metric looks like a target management can always clear, which is
    information about the target-setting, not a compliment on execution.
    Fewer than three beats on a metric is not a pattern - it is noise.
    """
    by_metric = collections.defaultdict(list)
    for r in history:
        if not r["verdict"].startswith("BEAT") or r["actual"] is None or r["quant"] is None:
            continue
        lower_better = r["metric"] in LOWER_IS_BETTER
        ref = _target_reference(r["quant"], lower_better)
        if ref is None:
            continue
        v = r["actual"]["value"]
        margin = (v - ref) if not lower_better else (ref - v)
        by_metric[r["metric"]].append({"date": r["first_said"], "applies_to": r["applies_to"],
                                       "margin": margin, "url": r["url"]})
    findings = []
    for metric, margins in by_metric.items():
        if len(margins) < 3:
            continue
        vals = [m["margin"] for m in margins]
        mean = sum(vals) / len(vals)
        if mean <= 0:
            continue
        var = sum((x - mean) ** 2 for x in vals) / len(vals)
        cv = (var ** 0.5) / mean
        if cv < 0.4:
            findings.append({
                "metric": metric, "n": len(vals), "mean_margin": round(mean, 2),
                "coefficient_of_variation": round(cv, 2), "instances": margins,
                "flag": ("CHRONIC SANDBAGGING (pattern, not a compliment): %d consecutive "
                         "beats averaging +%.2g with low variance (cv=%.2f) - the target "
                         "looks set low enough to clear routinely" % (len(vals), mean, cv)),
            })
    return findings


def build_structured_history(history, changes):
    """Spec S12's required shape: date | metric | guidance | revision | actual
    | outcome. Everything under 'guidance' is SINGLE SOURCE - MANAGEMENT
    GUIDANCE; everything under 'actual'/'outcome' is arithmetic on reported
    figures. A promise with no matched outcome is UNKNOWN here, never a
    silent MET.
    """
    revision_by_key = {}
    for c in changes:
        revision_by_key[(c["metric"], c["applies_to"], c["to_date"])] = c["direction"]

    rows = []
    for r in history:
        if r["withdrawn"]:
            guidance, revision = "(withdrawn)", "WITHDRAWN"
        else:
            guidance = quant_str(r["quant"])
            revision = revision_by_key.get((r["metric"], r["applies_to"], r["first_said"]), "-")
        actual = r["actual"]
        if actual is None:
            actual_str, outcome = "UNKNOWN", "UNKNOWN" if not r["withdrawn"] else r["verdict"]
        else:
            actual_str = "%.4g%s" % (actual["value"], actual.get("unit", ""))
            outcome = r["verdict"]
        rows.append({"date": r["first_said"], "metric": r["metric"], "applies_to": r["applies_to"],
                     "guidance": guidance, "revision": revision,
                     "actual": actual_str, "outcome": outcome})
    return rows


def compute_execution_score(history, changes, definition_changes, cut_patterns, sandbag_findings):
    """A 0-10 score, composed entirely of the facts computed above.

    Base 5.0 (neutral: no evidence either way). Every adjustment below is
    printed with the count and the arithmetic that produced it - nothing is
    folded in silently. An unscored target (UNKNOWN) is never counted toward
    the hit rate; it only ever shows up as reduced confidence.
    """
    scored = [r for r in history if r["verdict"].startswith(("MET", "BEAT", "MISS"))]
    met = [r for r in scored if r["verdict"].startswith("MET")]
    beat = [r for r in scored if r["verdict"].startswith("BEAT")]
    miss = [r for r in scored if r["verdict"].startswith("MISS")]
    unknown = [r for r in history if r["actual"] is None and not r["withdrawn"]]
    lowered = [c for c in changes if c["direction"] in ("LOWERED", "LOOSENED")]
    raised = [c for c in changes if c["direction"] in ("RAISED", "TIGHTENED")]
    withdrawn = [c for c in changes if c["direction"] == "WITHDRAWN"]

    # A multi-year (through-the-cycle) target is never a pass/fail on one
    # year's figure, so it is EXCLUDED from the delivery rate above by
    # construction. But one year of evidence is still evidence, so it is
    # tracked here as its own small, separately-weighted signal - never
    # blended into "beats" or "misses" on annual guidance.
    on_track, off_track = 0, 0
    for r in history:
        if r["verdict"] != "EVIDENCE ONLY (multi-year target)" or r["actual"] is None \
                or r["quant"] is None:
            continue
        cyc_verdict, _ = judge(r["quant"], r["metric"], r["actual"])
        if cyc_verdict.startswith(("MET", "BEAT")):
            on_track += 1
        elif cyc_verdict.startswith("MISS"):
            off_track += 1

    facts = {"scored_promises": len(scored), "met": len(met), "beat": len(beat),
             "miss": len(miss), "unknown_or_unscored": len(unknown),
             "guidance_cuts": len(lowered), "guidance_raises": len(raised),
             "cut_patterns": len(cut_patterns), "definition_changes": len(definition_changes),
             "withdrawn_events": len(withdrawn), "sandbagging_flags": len(sandbag_findings),
             "cycle_target_years_on_track": on_track, "cycle_target_years_off_track": off_track}

    n_cycle = on_track + off_track
    if not scored and not n_cycle:
        return {
            "score": None, "band": "NOT SCORABLE", "base": 5.0, "components": [], "facts": facts,
            "note": ("No quantified promise in the read history could be matched to a "
                     "delivered outcome, on an annual or a cycle basis. This is the "
                     "honest-failure path, not a zero: a score requires evidence, and none "
                     "was extractable here. Treat the extraction, not the company, as "
                     "unproven."),
        }

    hit_rate = (len(met) + len(beat)) / len(scored) if scored else None
    confidence_factor = min(1.0, len(scored) / 6.0)   # matches this file's own "under ~6 is thin"
    delta_delivery = round((hit_rate - 0.5) * 6.0 * confidence_factor, 2) if scored else 0.0
    delta_cuts = -round(0.5 * min(len(lowered), 4), 2)
    delta_pattern = -round(1.0 * min(len(cut_patterns), 3), 2)
    delta_withdrawn = -round(0.75 * min(len(withdrawn), 2), 2)
    delta_defchange = -round(1.0 * min(len(definition_changes), 2), 2)
    delta_sandbag = -round(0.75 * min(len(sandbag_findings), 2), 2)
    delta_coverage = -0.5 if 0 < len(scored) < 4 else 0.0
    # Cycle-target tracking is a much smaller signal than annual delivery: one
    # year proves nothing about a multi-year target on its own, so each year
    # of evidence is worth a fraction of a point either way, capped low.
    cycle_rate = (on_track / n_cycle) if n_cycle else None
    delta_cycle = round((cycle_rate - 0.5) * 2.0, 2) if n_cycle else 0.0
    delta_cycle = max(-1.0, min(1.0, delta_cycle))

    components = [
        ("delivery rate on scored ANNUAL promises",
         ("%d/%d hit (%.0f%%), confidence x%.2f for n=%d scored"
          % (len(met) + len(beat), len(scored), 100 * hit_rate, confidence_factor, len(scored)))
         if scored else "no annual (single fiscal-year) promise had a matched outcome",
         delta_delivery),
        ("cycle-target tracking (informational only - 1 year is not a verdict)",
         ("%d/%d reported year(s) within/above the standing range" % (on_track, n_cycle))
         if n_cycle else "no standing/through-the-cycle target had a matched outcome",
         delta_cycle),
        ("guidance cuts (target lowered / ceiling loosened)",
         "%d cut(s)" % len(lowered), delta_cuts),
        ("cut PATTERNS (2 consecutive cuts, or a cut <90d after reaffirming)",
         "%d pattern(s)" % len(cut_patterns), delta_pattern),
        ("guidance withdrawn / suspended", "%d event(s)" % len(withdrawn), delta_withdrawn),
        ("target definition changed (goalposts moved)",
         "%d change(s)" % len(definition_changes), delta_defchange),
        ("chronic sandbagging pattern", "%d metric(s) flagged" % len(sandbag_findings),
         delta_sandbag),
        ("coverage (few scored promises => low confidence)",
         "%d annual scored, %d cycle-years, %d unknown/unscored"
         % (len(scored), n_cycle, len(unknown)), delta_coverage),
    ]

    raw = 5.0 + sum(d for _, _, d in components)
    score = max(0.0, min(10.0, round(raw, 1)))
    if not scored:
        band = "LOW CONFIDENCE (0 annual promises scored; %d cycle-year(s) of evidence only)" % n_cycle
    elif len(scored) < 4:
        band = "LOW CONFIDENCE (%d scored promise(s))" % len(scored)
    elif len(scored) < 6:
        band = "MODERATE CONFIDENCE (%d scored promises)" % len(scored)
    else:
        band = "SUFFICIENT EVIDENCE (%d scored promises)" % len(scored)

    return {
        "score": score, "band": band, "base": 5.0, "components": components, "facts": facts,
        "note": ("OPINION. Every count feeding this number is a fact computed from reported "
                 "figures and the company's own dated statements; the weights that turn those "
                 "facts into one number are a fixed, disclosed heuristic - not a market view, "
                 "and not independent verification of guidance. %s." % GUIDANCE_LABEL),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

BAR = "=" * 100


def print_targets(targets, ir_note, company):
    print(BAR)
    print("STANDING FINANCIAL TARGETS  (finansiella mal)")
    print(BAR)
    if not targets:
        print("DATA NOT AVAILABLE - no standing financial targets could be read.")
        print()
        print(ir_note)
        print()
        print("Where a human should look, in order:")
        print("  1. The issuer's IR site, page usually named 'Financial targets',")
        print("     'Finansiella mal', 'Targets and outcomes' or 'X as an investment'.")
        print("  2. The annual report, section 'Financial targets' / 'Mal och utfall'")
        print("     - normally within the first 20 pages. This script does not parse")
        print("     PDFs (no stdlib PDF text extraction), so it cannot read it for you.")
        print("  3. The most recent Capital Markets Day presentation.")
        print("Do NOT assume the company has no targets. Absence here means the")
        print("extractor found none in machine-readable HTML, nothing more.")
        return
    print("Source: the issuer's own IR pages. %s" % ir_note)
    print("These are management's own statements. %s" % GUIDANCE_LABEL)
    print()
    for t in targets:
        print("  %-26s %-14s  horizon: %s%s"
              % (METRIC_LABEL.get(t["metric"], t["metric"]), quant_str(t["quant"]),
                 t["horizon"], "   [adjusted/organic basis]" if t["adjusted_basis"] else ""))
        print("      raw: \"%s\"" % t["sentence"][:170])
        print("      src: %s" % t["url"])
    print()


def print_history(rows, changes, company):
    print(BAR)
    print("GUIDANCE AND TARGET HISTORY vs DELIVERED OUTCOME")
    print(BAR)
    if not rows:
        print("DATA NOT AVAILABLE - no quantified forward statement was found in any")
        print("release body read. Many issuers put guidance only in the report PDF or")
        print("the earnings call, neither of which is machine-readable here.")
        return
    print("Every row below is %s." % GUIDANCE_LABEL)
    print("The 'outcome' column is a FACT computed from reported figures; the")
    print("verdict is arithmetic, not a view on management.")
    print()
    head = ("%-10s %-24s %-14s %-18s %-14s %-22s" %
            ("date", "metric", "target", "applies to", "actual", "verdict"))
    print(head)
    print("-" * len(head))
    for r in rows:
        actual = r["actual"]
        astr = "n/a"
        if actual:
            astr = "%.4g%s" % (actual["value"], actual.get("unit", ""))
        print("%-10s %-24.24s %-14.14s %-18.18s %-14.14s %-22.22s"
              % (r["first_said"], METRIC_LABEL.get(r["metric"], r["metric"]),
                 quant_str(r["quant"]) if not r["withdrawn"] else "WITHDRAWN",
                 r["applies_to"] + ("*" if r["period_inferred"] else ""),
                 astr, r["verdict"]))
        print("      said : \"%s\"" % r["sentence"][:160])
        print("      src  : %s" % (r["url"] or "n/a"))
        if r["repeated_on"]:
            print("      repeated on: %s" % ", ".join(r["repeated_on"]))
        if actual:
            print("      actual: %s  [%s]" % (r["verdict_why"], actual["basis"]))
            print("      from : %s" % (actual["source_line"] or "")[:150])
            if actual.get("source_url"):
                print("      src  : %s" % actual["source_url"])
        else:
            print("      actual: DATA NOT AVAILABLE for %s - outcome not yet reported, "
                  "or not extractable." % r["applies_to"])
        print()
    print("* period was NOT stated in the sentence; the report's own period was")
    print("  substituted. Treat those rows as weaker evidence.")
    print()
    print(BAR)
    print("CHANGES IN WHAT WAS PROMISED")
    print(BAR)
    if not changes:
        print("No revision detected among the statements read. That is not proof")
        print("nothing changed - only that nothing changed in what was extracted.")
    for c in changes:
        print("  %s  %-24s %-16s  %s -> %s"
              % (c["to_date"], METRIC_LABEL.get(c["metric"], c["metric"]),
                 c["applies_to"], c["from"], c["to"]))
        print("      %s   first stated %s" % (c["direction"], c["from_date"] or "n/a"))
        print("      %s" % (c["to_url"] or ""))
    print()


def print_summary(rows, targets, changes, company, notes):
    print(BAR)
    print("SUMMARY")
    print(BAR)
    scored = [r for r in rows if r["verdict"].startswith(("MET", "BEAT", "MISS"))]
    met = [r for r in scored if r["verdict"].startswith("MET")]
    beat = [r for r in scored if r["verdict"].startswith("BEAT")]
    miss = [r for r in scored if r["verdict"].startswith("MISS")]
    nocmp = [r for r in rows if r["verdict"] == "NOT COMPARABLE"]
    pending = [r for r in rows if r["verdict"] == "NO OUTCOME"]
    withheld = [r for r in rows if r["withdrawn"]]

    print("FACTS")
    print("  standing targets read from the IR site : %d" % len(targets))
    print("  distinct quantified promises found     : %d" % len(rows))
    print("  promises with a matched outcome        : %d" % len(scored))
    print("     met / in range                      : %d" % len(met))
    print("     beaten                              : %d" % len(beat))
    print("     missed                              : %d" % len(miss))
    print("  outcome exists but NOT COMPARABLE      : %d  (definition mismatch)" % len(nocmp))
    print("  no outcome yet or not extractable      : %d" % len(pending))
    print("  guidance withdrawn / suspended         : %d" % len(withheld))
    print("  revisions to a stated number           : %d" % len(changes))
    lowered = [c for c in changes if c["direction"] in ("LOWERED", "LOOSENED")]
    raised = [c for c in changes if c["direction"] in ("RAISED", "TIGHTENED")]
    if changes:
        print("     lowered / loosened                  : %d" % len(lowered))
        print("     raised / tightened                  : %d" % len(raised))
    print()
    print("OPINION - the reading below is a judgement, not a measurement.")
    print("It rests on %d scored promise(s); anything under ~6 is too thin to"
          % len(scored))
    print("support a conclusion about management.")
    if not scored:
        print("  Not enough scored promises to say anything about credibility.")
        print("  Read the rows above as raw evidence instead.")
    else:
        hit = len(met) + len(beat)
        rate = 100.0 * hit / len(scored)
        print("  Delivery rate on quantified promises: %d/%d (%.0f%%)."
              % (hit, len(scored), rate))
        if len(scored) < 6:
            print("  OPINION WITHHELD: too few scored promises to generalise.")
        elif rate >= 85 and len(beat) >= len(miss):
            print("  OPINION: the record is consistent with a management team that")
            print("  sets targets it can hit. Whether that is competence or")
            print("  sandbagging cannot be told apart from the numbers alone -")
            print("  check whether ranges were set wide and hit near the top.")
        elif rate <= 55:
            print("  OPINION: the record shows chronic shortfall against stated")
            print("  numbers. Discount forward statements from this management")
            print("  accordingly, and read the revision list above closely.")
        else:
            print("  OPINION: a mixed record. Neither chronic over-promising nor")
            print("  systematic sandbagging is evident in what was extracted.")
        if lowered:
            print("  Note for the reader: %d stated number(s) were LOWERED after"
                  % len(lowered))
            print("  first being published. A target that moves to meet the outcome")
            print("  is not a target that was met.")
    print()
    print("LIMITS OF THIS OUTPUT")
    for n in notes:
        print("  - %s" % n)
    print("  - Guidance is never independently verified. It is the company's own")
    print("    claim about its own future.")
    print("  - Extraction is regex over prose. Every number above is printed next")
    print("    to its raw sentence precisely so you can catch a misparse.")
    print("  - Targets stated only in the annual report PDF, in a Capital Markets")
    print("    Day deck, or on the earnings call are invisible here.")
    print()


def print_structured_history(rows):
    print(BAR)
    print("STRUCTURED HISTORY  (date | metric | guidance | revision | actual | outcome)")
    print(BAR)
    if not rows:
        print("DATA NOT AVAILABLE - no quantified promise was found to structure.")
        print()
        return
    head = ("%-10s %-24s %-14s %-16s %-12s %-24s"
            % ("date", "metric", "guidance", "revision", "actual", "outcome"))
    print(head)
    print("-" * len(head))
    for r in rows:
        print("%-10s %-24.24s %-14.14s %-16.16s %-12.12s %-24.24s"
              % (r["date"], METRIC_LABEL.get(r["metric"], r["metric"]), r["guidance"],
                 r["revision"], r["actual"], r["outcome"]))
    print()
    print("Every 'guidance' value is %s. 'actual' and 'outcome' are" % GUIDANCE_LABEL)
    print("arithmetic on reported figures - facts, not opinions. UNKNOWN means no")
    print("delivered figure could be matched to this promise; it is never quietly")
    print("counted as a pass. 'revision' shows what this row changed FROM, when it")
    print("is itself a later restatement of an earlier promise for the same metric")
    print("and period; '-' means no revision was detected for it.")
    print()


def print_execution(score, cut_patterns, definition_changes, sandbag, company):
    print(BAR)
    print("MANAGEMENT EXECUTION SCORE")
    print(BAR)
    if score["score"] is None:
        print("MANAGEMENT EXECUTION SCORE: NOT SCORABLE")
        print()
        print(score["note"])
        print()
        print("FACTS behind the non-score:")
        for k, v in score["facts"].items():
            print("  %-24s %s" % (k, v))
        print()
        return

    print("MANAGEMENT EXECUTION SCORE: %.1f / 10   [%s]" % (score["score"], score["band"]))
    print()
    print("Composition (base %.1f, then evidence-based adjustments - additive, nothing hidden):"
          % score["base"])
    for label, detail, delta in score["components"]:
        print("  %+6.2f  %-58s %s" % (delta, label, detail))
    print()
    print(score["note"])
    print()

    if cut_patterns:
        print("CUT PATTERNS DETECTED (a pattern, not a single miss):")
        for p in cut_patterns:
            print("  [%s] %s / %s: %s  (%s)"
                  % (p["type"], METRIC_LABEL.get(p["metric"], p["metric"]), p["applies_to"],
                     p["detail"], ", ".join(p["dates"])))
        print()
    if definition_changes:
        print("TARGET DEFINITION CHANGES (goalposts moved - same metric, different basis):")
        for d in definition_changes:
            print("  %s: [%s] on %s  ->  [%s] on %s"
                  % (METRIC_LABEL.get(d["metric"], d["metric"]),
                     ", ".join(d["from_definition"]), d["from_date"],
                     ", ".join(d["to_definition"]), d["to_date"]))
            print("      before: \"%s\"" % d["from_sentence"][:140])
            print("      after : \"%s\"" % d["to_sentence"][:140])
        print()
    if sandbag:
        print("SANDBAGGING SIGNAL (repeated beats with unusually low variance):")
        for s in sandbag:
            print("  %s: %s" % (METRIC_LABEL.get(s["metric"], s["metric"]), s["flag"]))
        print()

    print("FACTS the score is built from:")
    for k, v in score["facts"].items():
        print("  %-24s %s" % (k, v))
    print()
    print("This score is an OPINION derived from evidence, never from tone. It is")
    print("not a substitute for reading the rows above - it is a compressed pointer")
    print("into them.")
    print()


# ---------------------------------------------------------------------------
# GUIDANCE STORE (v3.0.0)
#
# Everything above this line was already true before this store existed: it
# reads whatever the current run could fetch and forgets it the moment the
# process exits. That is a real defect, verified by a full read of this file
# before writing a line of the store - there was no home directory, no
# schema, nothing. Two consequences follow directly from that gap:
#
#   1. mfn_archive() reaches pre-2024 history only through an UNDOCUMENTED
#      endpoint (/all/a.json?author=<slug>) that this file's own docstring
#      dates "Verified 2026-08-31" - i.e. admits it could stop working with
#      no warning. If it does, the fallback is the documented feed, which is
#      hard-capped near 30 items and ignores offset. A run made on the day
#      the deep endpoint breaks would silently see less history than a run
#      made the day before, and nothing on screen would say so.
#   2. cision_archive() is best-effort by construction - it only fetches
#      bodies for releases that already look report-like or guidance-like,
#      so a guidance sentence buried in an unrelated release is missed by
#      design, not by bug.
#
# WHAT IS STORED, AND WHY THAT LIST AND NO OTHER. The governing rule is
# "persist only what cannot be re-derived". judge()'s verdict (MET / MISS /
# NOT COMPARABLE) is arithmetic on numbers this file can always re-fetch, so
# it is never written to disk - see judge()'s own docstring, which calls this
# "a fact", not a record. The guidance STATEMENT is the opposite: it lives in
# one dated release, reached through the fragile paths above, and once that
# release scrolls out of a shallow archive's ~30-item window the sentence is
# gone for good, not merely stale. So the store keeps the statement, its
# vintage (the release's own publication date, never the fetch date), its
# basis (adjusted/organic vs as-stated), its channel and source URL, the
# verbatim sentence, and whether it was period guidance or a standing
# through-the-cycle target - the exact three-way separation this file's
# module docstring insists on keeping intact. Everything else (actual,
# verdict, verdict_why) is recomputed fresh every time from CURRENT filings,
# by the SAME judge()/pick_actual() pipeline a freshly-scanned row goes
# through - see stored_statement_to_history_row() and run()'s merge step.
#
# APPEND-ONLY, REVISION-LINKED. A prior statement is never edited or dropped;
# a revised number is a NEW row, cross-linked to what it supersedes. This is
# a deliberate departure from thesis_ledger.py, which truncates
# status_history at 200 entries (`del thesis["status_history"][:-200]`) and
# silently replaces a re-observed value in place - both are information
# loss this store refuses to repeat. There is no cap on len(statements)
# anywhere below.
#
# IDENTITY. Keyed on LEI first, ISIN second - the exact discipline
# thesis_ledger.py's ledger_key() applies, for the exact same reason:
# "Volvo" is two listed issuers with different management teams, and a store
# keyed on a display name would silently merge their guidance histories.
# A company with neither a LEI nor an ISIN anywhere in the Nordic registers
# is refused, not stored under its typed name - see resolve_store_identity().
# ---------------------------------------------------------------------------

GUIDANCE_STORE_SCHEMA_VERSION = 1

# Presence-based, exactly like thesis_ledger.ledger_key(): the VALUE of
# identity["lei"]/["isin"] is trusted from the resolver that produced it
# (company_resolve.py or esef_fundamentals' own filing index), not
# re-validated against a checksum here.
_STORE_KEY_RE = re.compile(r"^(LEI|ISIN)-[A-Za-z0-9]+$")


def guidance_store_home():
    """Root directory for the guidance store, overridable the same way
    portfolio_store.py (PORTFOLIO_STORE_HOME) and thesis_ledger.py
    (THESIS_LEDGER_HOME) already are - so the test suite never has to touch
    a real ~/.investment-analyst."""
    override = os.environ.get("GUIDANCE_STORE_HOME")
    if override:
        return os.path.abspath(override)
    return os.path.join(os.path.expanduser("~"), ".investment-analyst", "guidance")


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def guidance_store_key(identity):
    """LEI first, ISIN second. Never the name - see the module note above.

    Returns None (never raises) when neither is available, so callers can
    print their own refusal with the query that produced it; run() and the
    CLI both do this rather than let an exception cross a code path that
    also has to keep working when the store is never touched at all.
    """
    ident = identity or {}
    lei = ident.get("lei")
    if lei and lei != CR.NA:
        return "LEI-" + lei
    isin = ident.get("isin")
    if isin and isin != CR.NA:
        return "ISIN-" + isin
    return None


def resolve_store_identity(label, query, country):
    """LEI/ISIN identity for the guidance store. Two engines, in order:

      1. company_resolve.py's full Nordic identity engine - the same brand
         guard portfolio_store.py and thesis_ledger.py both build on, so
         "Volvo" is refused here exactly as it is everywhere else in this
         toolkit, with every candidate named.
      2. esef_fundamentals.search_index() - the SAME lookup esef_actuals()
         already performs a few lines below in run(), reused rather than
         re-implemented, as a fallback for an issuer company_resolve cannot
         place (a name form it does not recognise) but whose ESEF filings
         are indexed under an unambiguous LEI.

    Returns (identity_dict, note). identity_dict is None when refused; note
    explains why and is always safe to print or fold into `notes`. This
    never raises - a company with no clean identity must not abort a run
    that only wanted to READ guidance, it must simply not be stored.
    """
    name = label or query
    if not name:
        return None, "DATA NOT AVAILABLE: no company name to resolve an identity for."
    try:
        rec = CR.resolve(name, country=country)
        lei = rec.get("lei")
        isin = rec.get("isin")
        if (lei and lei != CR.NA) or (isin and isin != CR.NA):
            return {"lei": lei if lei and lei != CR.NA else None,
                    "isin": isin if isin and isin != CR.NA else None,
                    "company_name": rec.get("company_name") or name,
                    "legal_name": rec.get("legal_name")}, None
    except CR.Ambiguous as exc:
        cands = ", ".join(c.get("company_name", "?") for c in (exc.candidates or []))
        return None, ("GUIDANCE_STORE_IDENTITY_AMBIGUOUS: %d distinct issuer(s) "
                      "match %r (%s). Attributing a guidance record to the wrong "
                      "one is silent and looks identical to a correct answer - "
                      "nothing was stored; re-run with the exact legal name."
                      % (len(exc.candidates or []), name, cands))
    except CR.NotFound:
        pass
    except Exception:                                  # noqa: BLE001
        pass  # a resolver outage degrades to the ESEF fallback, never aborts

    try:
        hits = ESEF.search_index(name, country)
    except SystemExit:
        hits = []
    if len(hits) == 1:
        h = hits[0]
        return {"lei": h["lei"], "isin": None, "company_name": h["name"],
                "legal_name": h["name"]}, None
    if len(hits) > 1:
        return None, ("GUIDANCE_STORE_IDENTITY_AMBIGUOUS: %d ESEF filers in %s "
                      "match %r - nothing was stored." % (len(hits), country, name))
    return None, ("DATA NOT AVAILABLE: no LEI or ISIN could be resolved for %r "
                  "through company_resolve.py or the ESEF filing index. Storing "
                  "guidance under a bare display name is exactly the failure mode "
                  "this store exists to avoid, so nothing was written. Try the "
                  "exact legal name, an ISIN or an LEI." % name)


def _store_path(key):
    if not _STORE_KEY_RE.match(key or ""):
        raise ValueError("invalid guidance store key %r" % key)
    return os.path.join(guidance_store_home(), key + ".json")


def _guidance_index_path():
    return os.path.join(guidance_store_home(), "index.json")


def _write_json_atomic(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)          # atomic: a crash never leaves half a store file


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _new_guidance_doc(key, identity):
    return {"schema_version": GUIDANCE_STORE_SCHEMA_VERSION, "store_key": key,
            "identity": identity or {}, "aliases": [], "created": _now_iso(),
            "last_updated": _now_iso(), "statements": [], "fetch_log": []}


def guidance_store_load(key):
    """The stored document for `key`, or None if nothing has been saved yet.

    None, not {} (unlike portfolio_store.load()) - a caller here always has
    to tell "no store yet" apart from "store exists but is empty", because
    --stored-history and --revisions print a different message for each.
    """
    if not key:
        return None
    doc = _read_json(_store_path(key))
    if doc is None:
        return None
    doc.setdefault("statements", [])
    doc.setdefault("fetch_log", [])
    doc.setdefault("aliases", [])
    return doc


def _guidance_read_index():
    idx = _read_json(_guidance_index_path())
    if not idx or idx.get("schema_version") != GUIDANCE_STORE_SCHEMA_VERSION:
        idx = {"schema_version": GUIDANCE_STORE_SCHEMA_VERSION, "companies": {}}
    return idx


def _guidance_index_update(doc):
    idx = _guidance_read_index()
    ident = doc.get("identity") or {}
    idx["companies"][doc["store_key"]] = {
        "company_name": ident.get("company_name"), "lei": ident.get("lei"),
        "isin": ident.get("isin"), "aliases": doc.get("aliases", []),
        "statements": len(doc.get("statements") or []),
        "last_updated": doc.get("last_updated")}
    _write_json_atomic(_guidance_index_path(), idx)


def guidance_store_save(doc):
    """Write `doc` atomically (temp file + os.replace, matching
    portfolio_store.save() and thesis_ledger._write_json()) and refresh the
    alias index alongside it. Callers should reach the store through
    guidance_store_merge() below, not this directly - this has no
    idempotency or revision logic of its own."""
    doc = dict(doc)
    doc["schema_version"] = GUIDANCE_STORE_SCHEMA_VERSION
    doc["last_updated"] = _now_iso()
    _write_json_atomic(_store_path(doc["store_key"]), doc)
    _guidance_index_update(doc)
    return None


def guidance_store_index_lookup(query):
    """Offline alias lookup, so --stored-history/--revisions never have to
    touch the network for a company already on file - the same shape as
    thesis_ledger.index_lookup()."""
    idx = _guidance_read_index()
    q = (query or "").strip().lower()
    if not q:
        return None
    for key, entry in idx["companies"].items():
        if q == key.lower():
            return key
        for field in ("lei", "isin"):
            if entry.get(field) and q == str(entry[field]).lower():
                return key
        if entry.get("company_name") and q == entry["company_name"].strip().lower():
            return key
        for alias in entry.get("aliases") or []:
            if q == (alias or "").strip().lower():
                return key
    return None


def _statement_id(source_url, metric, applies_to, sentence):
    """Idempotency key for one guidance/target statement.

    (source_url, metric, target period, verbatim sentence) pins a statement
    to the one release or IR page that carried it. Re-running this script
    over an UNCHANGED release reproduces the identical tuple every time -
    same hash, same row, no duplicate; that is the whole idempotency
    guarantee. A REVISED number lives in a different sentence (almost always
    a different release too), so it always earns a new id and is linked as a
    revision by guidance_store_merge() instead of overwriting anything. The
    sentence is folded in, not just source_url+metric+period, because a
    single report occasionally restates both a full-year guide and a
    reiterated cycle target for the same metric in the same document - two
    distinct promises that must not collide into one row.
    """
    h = hashlib.sha1()
    for part in (source_url or "", metric or "", applies_to or "",
                re.sub(r"\s+", " ", (sentence or "").strip().lower())):
        h.update(part.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()[:20]


def _sort_date(stmt):
    return stmt.get("vintage") or stmt.get("first_observed") or ""


def _basis_label(adjusted_basis):
    return ("adjusted/organic - not directly comparable to an IFRS-reported "
            "figure" if adjusted_basis else
            "as stated (comparable to an IFRS/ESEF figure where the metric "
            "permits)")


def guidance_row_to_statement(row, channel, archive_meta):
    """One collapsed guidance-history row (collapse()'s output, post-join
    with `_stmt_id`/`_from_store` attached by run()) -> a store record.

    Drops exactly what judge() can re-derive (actual, verdict, verdict_why -
    none of those three keys are read here even though `row` may carry
    them); keeps exactly what a vanished MFN endpoint could otherwise take
    with it: the promise, verbatim, dated to the release that carried it.
    """
    return {
        "id": _statement_id(row.get("url"), row.get("metric"), row.get("applies_to"),
                            row.get("sentence")),
        "kind": "guidance",
        "standing": row.get("applies_to") in STANDING_PERIODS,
        "metric": row.get("metric"),
        "quant": row.get("quant"),
        "applies_to": row.get("applies_to"),
        "period_inferred": bool(row.get("period_inferred")),
        "withdrawn": bool(row.get("withdrawn")),
        "adjusted_basis": bool(row.get("adjusted_basis")),
        "basis_label": _basis_label(row.get("adjusted_basis")),
        "vintage": row.get("first_said") or row.get("date"),
        "reaffirmed_on": list(row.get("repeated_on") or []),
        "source_url": row.get("url"),
        "channel": channel,
        "title": row.get("title"),
        "sentence": row.get("sentence"),
        "supersedes": None,
        "superseded_by": None,
        "archive_depth": dict(archive_meta or {}),
        "stored_utc": _now_iso(),
    }


def target_row_to_statement(row, archive_meta):
    """One standing-target row from crawl_targets()/scan_targets() -> a
    store record. IR "financial targets" pages are undated live pages, not
    archived releases, so there is no real publication date to record -
    `vintage` is honestly left None rather than guessed, and
    `first_observed` (this run's own date) is kept as a clearly separate
    field so a reader can never mistake one for the other.
    """
    return {
        "id": _statement_id(row.get("url"), row.get("metric"), row.get("horizon"),
                            row.get("sentence")),
        "kind": "target",
        "standing": True,
        "metric": row.get("metric"),
        "quant": row.get("quant"),
        "applies_to": row.get("horizon"),
        "period_inferred": False,
        "withdrawn": False,
        "adjusted_basis": bool(row.get("adjusted_basis")),
        "basis_label": _basis_label(row.get("adjusted_basis")),
        "vintage": None,
        "first_observed": _now_iso()[:10],
        "reaffirmed_on": [],
        "source_url": row.get("url"),
        "channel": "IR",
        "title": None,
        "sentence": row.get("sentence"),
        "supersedes": None,
        "superseded_by": None,
        "archive_depth": {"channel": "IR", "deep": None,
                          "note": "IR pages are live, undated snapshots, not an "
                                  "archive - archive-depth does not apply the way "
                                  "it does to a release feed; only THIS crawl's "
                                  "own findings are represented."},
        "stored_utc": _now_iso(),
    }


def stored_statement_to_history_row(stmt):
    """Stored guidance statement -> the row shape collapse() produces, so it
    can rejoin the SAME pick_actual()/judge() pipeline a freshly-scanned row
    goes through (see run()). Nothing about a stored verdict is trusted or
    even read here - there isn't one on file; only the promise itself is
    replayed against TODAY's actuals.
    """
    vintage = stmt.get("vintage") or stmt.get("first_observed") or ""
    return {
        "date": vintage, "metric": stmt.get("metric"), "quant": stmt.get("quant"),
        "applies_to": stmt.get("applies_to"),
        "period_inferred": bool(stmt.get("period_inferred")),
        "withdrawn": bool(stmt.get("withdrawn")),
        "adjusted_basis": bool(stmt.get("adjusted_basis")),
        "retrospective": False, "title": stmt.get("title"),
        "url": stmt.get("source_url"), "sentence": stmt.get("sentence"),
        "kind": "guidance", "first_said": vintage,
        "repeated_on": list(stmt.get("reaffirmed_on") or []),
        "_stmt_id": stmt.get("id"), "_from_store": True,
    }


def guidance_store_merge(key, identity, alias, new_statements, fetch_meta):
    """Append-only merge of `new_statements` into the store for `key`.

    IDEMPOTENT: a statement whose id already exists on file is skipped -
    this is what makes running the same, unchanged release twice a no-op
    rather than a duplicate row (see _statement_id()'s docstring).

    REVISION-AWARE, NEVER DESTRUCTIVE: a genuinely new statement that shares
    (kind, metric, applies_to) with an already-stored one is linked to the
    most recent prior statement in that slot - `supersedes` on the new row,
    `superseded_by` on the old one. The old row's own content is never
    edited, only that one cross-reference field is set on it; nothing is
    deleted and nothing is capped. This is the deliberate opposite of
    thesis_ledger.py's `del thesis["status_history"][:-200]` truncation and
    its silent in-place value replacement - see the module note above.

    `new_statements` is sorted by vintage before merging so a run that
    itself introduces two links in one chain (rare, but possible when a
    deep archive fetch reaches several years back in a single pass) links
    them in the right order.
    """
    doc = guidance_store_load(key) or _new_guidance_doc(key, identity)
    if identity:
        doc["identity"] = identity   # refresh display fields only; the key itself never changes
    if alias:
        alias = alias.strip()
        if alias and alias not in doc["aliases"]:
            doc["aliases"].append(alias)

    existing_ids = {s["id"] for s in doc["statements"]}
    added, revised = 0, 0
    for stmt in sorted(new_statements, key=lambda s: _sort_date(s) or ""):
        if stmt["id"] in existing_ids:
            continue
        prior = None
        for s in doc["statements"]:
            if s["kind"] != stmt["kind"] or s["metric"] != stmt["metric"] \
                    or s["applies_to"] != stmt["applies_to"]:
                continue
            if prior is None or (_sort_date(s) or "") > (_sort_date(prior) or ""):
                prior = s
        if prior is not None:
            stmt["supersedes"] = prior["id"]
            prior["superseded_by"] = stmt["id"]
            revised += 1
        doc["statements"].append(stmt)
        existing_ids.add(stmt["id"])
        added += 1

    doc["fetch_log"].append(fetch_meta)
    guidance_store_save(doc)
    return doc, added, revised


def _quant_mid(q):
    if not q:
        return None
    if q.get("kind") == "range" and q.get("low") is not None and q.get("high") is not None:
        return (q["low"] + q["high"]) / 2.0
    return q.get("low") if q.get("low") is not None else q.get("high")


def guidance_store_history_rows(doc):
    """Every stored statement, newest vintage first, each annotated (when it
    is itself a revision) with what it revises. A pure read - no verdict is
    computed here; this is a record of what was SAID, not a re-scored
    outcome (run the plain company query, optionally with --use-history, for
    that).
    """
    stmts = list(doc.get("statements") or [])
    by_id = {s["id"]: s for s in stmts}
    rows = []
    for s in sorted(stmts, key=lambda s: _sort_date(s) or "", reverse=True):
        row = dict(s)
        prior = by_id.get(s.get("supersedes"))
        if prior:
            row["revises_quant"] = quant_str(prior.get("quant"))
        rows.append(row)
    return rows


def guidance_store_revision_chain(doc):
    """Just the links: every statement that revises another, oldest first,
    with the direction a plain number comparison cannot convey on its own
    (a margin FLOOR going from 10% to 8% is a cut; a net-debt/EBITDA CEILING
    going from 1.0x to 1.5x is also a cut - LOWER_IS_BETTER decides which
    word applies, same table detect_changes() already uses).
    """
    by_id = {s["id"]: s for s in doc.get("statements") or []}
    out = []
    for s in doc.get("statements") or []:
        prior = by_id.get(s.get("supersedes"))
        if not prior:
            continue
        if s.get("withdrawn"):
            direction = "WITHDRAWN"
        else:
            mid_a, mid_b = _quant_mid(prior.get("quant")), _quant_mid(s.get("quant"))
            lower_better = s.get("metric") in LOWER_IS_BETTER
            if mid_a is None or mid_b is None or mid_a == mid_b:
                direction = "RESTATED"
            elif mid_b < mid_a:
                direction = "TIGHTENED" if lower_better else "LOWERED"
            else:
                direction = "LOOSENED" if lower_better else "RAISED"
        out.append({"metric": s.get("metric"), "applies_to": s.get("applies_to"),
                    "from": quant_str(prior.get("quant")), "to": quant_str(s.get("quant")),
                    "from_vintage": _sort_date(prior), "to_vintage": _sort_date(s),
                    "direction": direction, "from_url": prior.get("source_url"),
                    "to_url": s.get("source_url")})
    return sorted(out, key=lambda c: c["to_vintage"] or "")


def print_guidance_store_history(doc, company):
    print(BAR)
    print("STORED GUIDANCE HISTORY - %s" % (company or doc.get("store_key")))
    print(BAR)
    print("store key: %s   |   %d statement(s) on file   |   last updated %s"
          % (doc["store_key"], len(doc.get("statements") or []), doc.get("last_updated")))
    if any(fl.get("deep_archive") is False for fl in doc.get("fetch_log") or []):
        print()
        print("CAVEAT: at least one fetch behind this history used a SHALLOW "
              "release feed (the ~30 most recent releases, or a best-effort "
              "Cision crawl). Absence of guidance before that fetch's own date "
              "range is NOT evidence none was given - see the fetch log below.")
    print()
    for row in guidance_store_history_rows(doc):
        tag = " [WITHDRAWN]" if row.get("withdrawn") else ""
        rev = "  (revises %s)" % row["revises_quant"] if row.get("revises_quant") else ""
        print("  %-10s  %-24s %-14s applies to %-20s%s%s"
              % (row.get("vintage") or row.get("first_observed") or "?",
                 METRIC_LABEL.get(row["metric"], row["metric"]), quant_str(row["quant"]),
                 row["applies_to"], tag, rev))
        print("      kind : %s%s" % (row["kind"], " (standing)" if row.get("standing") else ""))
        print("      basis: %s" % row["basis_label"])
        print("      src  : [%s] %s" % (row["channel"], row["source_url"]))
        print("      raw  : \"%s\"" % (row.get("sentence") or "")[:170])
        if (row.get("archive_depth") or {}).get("deep") is False:
            print("      NOTE : %s" % row["archive_depth"].get("note"))
        print()
    print("FETCH LOG (%d run(s)):" % len(doc.get("fetch_log") or []))
    for fl in doc.get("fetch_log") or []:
        print("  %s  venue=%-8s deep_archive=%-5s releases=%s  %s"
              % (str(fl.get("run_utc"))[:19], fl.get("venue"), fl.get("deep_archive"),
                 fl.get("releases_scanned"), fl.get("archive_note") or ""))
    print()


def print_guidance_revisions(doc, company):
    print(BAR)
    print("GUIDANCE REVISION CHAIN - %s" % (company or doc.get("store_key")))
    print(BAR)
    chain = guidance_store_revision_chain(doc)
    if not chain:
        print("No revisions on file for this issuer - every stored statement is "
              "either a first statement or a plain reiteration of one already "
              "seen (a reiteration is never filed as a revision).")
        return
    for c in chain:
        print("  %s -> %s   [%s]   %s / %s"
              % (c["from_vintage"], c["to_vintage"], c["direction"],
                 METRIC_LABEL.get(c["metric"], c["metric"]), c["applies_to"]))
        print("      %s  ->  %s" % (c["from"], c["to"]))
        print("      from: %s" % c["from_url"])
        print("      to  : %s" % c["to_url"])
        print()


def _execution_score_with_coverage(execution_score, history, store_key):
    """Non-destructively add a 'coverage_statement' fact disclosing how many
    of the scored promises came from stored history vs. this run's own
    fetch - a standalone function (rather than inline in run()) precisely so
    it can be unit-tested offline, with a synthetic `history`, without
    running the network-dependent pipeline that builds one for real.

    compute_execution_score()'s formula, inputs and return shape are never
    touched - this is called AFTER it, on its already-finished output, and
    only from run() when --use-history was passed. A caller that never
    engages the feature never sees this key at all, which is what keeps the
    score's default output byte-identical to before this store existed.
    """
    scored_all = [r for r in history if r["verdict"].startswith(("MET", "BEAT", "MISS"))]
    scored_from_store = [r for r in scored_all if r.get("_from_store")]
    said_dates = [r["first_said"] for r in history if r.get("first_said")]
    years = 0.0
    if len(said_dates) >= 2:
        try:
            d0 = datetime.date.fromisoformat(min(said_dates)[:10])
            d1 = datetime.date.fromisoformat(max(said_dates)[:10])
            years = (d1 - d0).days / 365.25
        except ValueError:
            years = 0.0
    out = dict(execution_score)
    out["facts"] = dict(out["facts"])
    out["facts"]["coverage_statement"] = (
        "scored on %d statement(s) over %.1f year(s), of which %d from stored "
        "history (store key %s)" % (len(scored_all), years, len(scored_from_store),
                                    store_key or "n/a"))
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def resolve_company(name):
    """Find the company on MFN first, then Cision. Returns (venue, slug, label, note).

    `note` is None on a clean single match. When the query matches more than
    one distinct issuer (by slug) - "Volvo" resolves to both AB Volvo and
    Volvo Car AB on MFN - this refuses instead of letting whichever hit
    happened to come first win: venue/slug/label come back None and `note`
    names every candidate seen, the same discipline company_resolve.py's
    brand guard applies.

    This function has one unique requirement: for MFN candidates, it verifies
    that the newsroom actually has archive history by probing mfn_archive().
    Guidance tracking needs a newsroom with history, not merely a newsroom.
    If an MFN archive probe fails, the function falls through to Cision.

    Deliberately NO local fallback implementation. issuer_feed.py lives in
    this same folder, imports nothing risky at module scope, and is the single
    home for this logic - three divergent copies of it are what made a real
    misattribution bug survive in portfolio_review.py. A second copy kept here
    "in case the first is missing" would reintroduce exactly that, and would
    drift the moment either side is touched. If it cannot be imported, this
    refuses and says so: a resolution that could not run is not a resolution
    that passed.
    """
    try:
        import issuer_feed
    except (Exception, SystemExit):  # SystemExit does not inherit from Exception
        return None, None, None, ("issuer_feed.py not available - a newsroom "
                                  "cannot be resolved without it.")

    # The searchers are passed in explicitly rather than left to issuer_feed's
    # own module handles. Two reasons, and the second is the load-bearing one:
    # this file already imports mfn_news as MFN and cision_news as CIS, so
    # resolving through anything else would mean two handles on the same
    # module; and MFN/CIS are the seam every test in this repo monkeypatches.
    # Resolving through issuer_feed's private handles instead silently ignores
    # those patches and sends unit tests to the live network.
    _search_mfn = lambda q: MFN.search(q)          # noqa: E731
    _search_cision = lambda q: CIS.resolve(q)      # noqa: E731

    venue, slug, label, note = issuer_feed.resolve(
        name, search_mfn=_search_mfn, search_cision=_search_cision)

    if venue == "MFN" and note is None:
        # The one domain-specific rule guidance_track keeps: a newsroom is only
        # useful here if it actually carries history. Guidance tracking reads a
        # company's own past statements, so an empty newsroom is the same as no
        # newsroom - fall through to Cision rather than accept it.
        items, _ = mfn_archive(slug, want=60)
        if items:
            return venue, slug, label, note
        venue, slug, label, note = issuer_feed.resolve(
            name, search_mfn=lambda q: [], search_cision=_search_cision)

    return venue, slug, label, note


def run(name, do_ir=True, esef_country="SE", limit_releases=500,
       use_stored_history=False, save_to_store=False):
    notes = []
    venue, slug, label, resolve_note = resolve_company(name)
    if not slug:
        if resolve_note:
            return None, [resolve_note]
        return None, ["DATA NOT AVAILABLE: %r resolved to no MFN and no Cision "
                      "issuer. Try the exact legal name." % name]

    if venue == "MFN":
        items, note = mfn_archive(slug, want=limit_releases)
        if note:
            notes.append(note)
        # The archive-depth caveat travels with every statement stored from
        # this run (see guidance_row_to_statement()) - deep=False means the
        # ~30-item shallow feed was used, and absence of older guidance in
        # that case is a feed limitation, not evidence none was given.
        archive_meta = {"channel": "MFN", "deep": not bool(note),
                        "note": note or ("MFN /all/a.json deep archive worked for "
                                         "this run; up to %d releases were "
                                         "requested." % limit_releases)}
    else:
        items = cision_archive(slug)
        notes.append("Cision issuer: only report-like releases were fetched for "
                     "their body text, and Cision publishes no regulatory tag.")
        archive_meta = {"channel": "Cision", "deep": False,
                        "note": ("Cision archive is inherently best-effort: only "
                                 "report-like releases have their body fetched, so "
                                 "a guidance statement buried in an unrelated "
                                 "release can be missed. Absence here is a coverage "
                                 "limit, not evidence none was given.")}
    if not items:
        return None, ["DATA NOT AVAILABLE: no releases retrieved for %s (%s/%s)."
                      % (label, venue, slug)]

    english = [i for i in items if i.get("lang") in (None, "en")]
    scanned = english or items
    if not english:
        notes.append("No English releases found; Swedish bodies were scanned, where "
                     "the extractor is weaker.")

    # --- guidance from release prose
    guidance_rows, report_actuals = [], {}
    for it in scanned:
        text = it.get("text") or ""
        if not text:
            continue
        meta = {"date": it["date"], "title": it["title"], "url": it["url"]}
        period = fiscal_label(it["title"], text, it["date"])
        guidance_rows.extend(scan_guidance(text, period, meta))
        if it.get("is_report"):
            # The newest report restates the year; keep the first (newest)
            # statement of each figure, matching esef_fundamentals' rule.
            for key, found in ((period, scan_actuals(text, meta)),
                               (_quarter_key(period, it["title"]),
                                scan_quarter_actuals(text, meta))):
                if not key or not found:
                    continue
                bucket = report_actuals.setdefault(key, {})
                for k, v in found.items():
                    bucket.setdefault(k, v)

    history = collapse(guidance_rows)
    for r in history:
        r["_stmt_id"] = _statement_id(r.get("url"), r.get("metric"), r.get("applies_to"),
                                      r.get("sentence"))
        r["_from_store"] = False

    # --- identity + stored guidance history (guidance store, v3.0.0) -------
    # Resolving identity costs a network round trip, so it only happens when
    # a caller actually asked to read from or write to the store - a plain
    # `guidance_track.py "Sandvik"` run touches none of this and behaves
    # exactly as it did before the store existed.
    store_identity, store_identity_note, store_key = None, None, None
    stored_merged = 0
    if use_stored_history or save_to_store:
        store_identity, store_identity_note = resolve_store_identity(
            label, name, esef_country)
        store_key = guidance_store_key(store_identity) if store_identity else None
        if store_identity_note:
            notes.append(store_identity_note)
    if use_stored_history and store_key:
        stored_doc = guidance_store_load(store_key)
        if stored_doc:
            seen_ids = {r["_stmt_id"] for r in history}
            for s in stored_doc.get("statements") or []:
                if s.get("kind") != "guidance":
                    continue          # standing targets do not feed the join/score below
                row = stored_statement_to_history_row(s)
                if row["_stmt_id"] in seen_ids:
                    continue          # already present from this run's own fetch
                history.append(row)
                seen_ids.add(row["_stmt_id"])
                stored_merged += 1
            if stored_merged:
                notes.append(
                    "Stored guidance history folded into the join/score below: "
                    "%d statement(s) from the guidance store (key %s) that this "
                    "run's own fetch did not (re-)surface. Their verdicts are "
                    "recomputed fresh against CURRENT actuals, never read back "
                    "from the store - see --stored-history for the raw record "
                    "with revision links." % (stored_merged, store_key))

    # --- standing targets from the IR site
    targets, ir_note = [], "IR-site crawl skipped (--no-ir)."
    domains = guess_domains([i.get("text") or "" for i in items[:80]], label or name)
    if do_ir:
        if not domains:
            ir_note = ("DATA NOT AVAILABLE: no company web domain could be read out "
                       "of the release bodies, so no IR page was crawled.")
        for d in domains:
            rows, fetched, urls = crawl_targets(d)
            if rows:
                targets = rows
                ir_note = "Crawled %d page(s) under %s." % (fetched, d)
                break
            ir_note = ("Crawled %s (%d page(s)) and found no machine-readable "
                       "financial-target statement." % (d, fetched))
        if not targets and domains:
            notes.append("The IR site was reachable but yielded no targets. Sites that "
                         "render targets client-side, or block a stdlib HTTP client, "
                         "look identical to a site with no targets - check by hand.")

    # --- outcomes from ESEF, as a second and different basis
    esef, esef_note = {}, ""
    try:
        esef, esef_note = esef_actuals(label or name, esef_country)
    except Exception as exc:                      # never let a data source abort the run
        esef_note = "ESEF lookup failed (%s)" % exc
    if esef_note:
        notes.append("ESEF: %s" % esef_note)
    if esef:
        notes.append("ESEF ratios are IFRS as reported. A target expressed as "
                     "'adjusted', 'organic' or 'at fixed exchange rates' is NOT the "
                     "same measure and is reported as NOT COMPARABLE, never as a miss.")

    # --- join promises to outcomes
    for r in history:
        actual, incomparable = pick_actual(r["metric"], r["applies_to"],
                                           report_actuals, esef, r["adjusted_basis"])
        is_multi_year = (r["applies_to"] in ("through the cycle", "per year (standing)")
                        or r["applies_to"].startswith("by "))
        if actual is None and is_multi_year:
            # pick_actual() keys on the exact period label, and a reported
            # figure is always keyed to one fiscal year - it can never match
            # "through the cycle" literally. Without this fallback a textbook
            # cycle target (Sandvik's 7% growth, 20-22% EBITA margin, ~1x net
            # debt/EBITDA) would be NOT SCORABLE forever, evidence or not.
            actual, incomparable = latest_metric_actual(r["metric"], report_actuals, esef,
                                                        r["adjusted_basis"])
        r["actual"] = actual
        if r["withdrawn"]:
            r["verdict"], r["verdict_why"] = "WITHHELD", "guidance withdrawn or suspended"
        elif is_multi_year:
            # A through-the-cycle target cannot be passed or failed in one year.
            r["verdict"] = "NOT SCORABLE (multi-year)" if actual is None else \
                "EVIDENCE ONLY (multi-year target)"
            if actual is None:
                r["verdict_why"] = ("a through-the-cycle target is not settled by any "
                                    "single year; no reported figure was found to show "
                                    "as one year of evidence")
            else:
                _, cycle_why = judge(r["quant"], r["metric"], actual)
                r["verdict_why"] = ("a through-the-cycle target is not settled by any "
                                    "single year; most recent reported year shown as one "
                                    "year of evidence toward it - %s" % cycle_why)
        elif incomparable:
            r["verdict"] = "NOT COMPARABLE"
            r["verdict_why"] = ("promise is on an adjusted/organic basis; only an IFRS "
                                "reported figure is available")
        else:
            r["verdict"], r["verdict_why"] = judge(r["quant"], r["metric"], actual)

    history.sort(key=lambda r: (r["first_said"], r["metric"]), reverse=True)
    changes = detect_changes(history)

    # --- management execution score: derived entirely from the facts above
    definition_changes = detect_definition_changes(guidance_rows, targets)
    cut_patterns = detect_cut_patterns(history, changes)
    sandbagging = detect_sandbagging(history)
    execution_score = compute_execution_score(history, changes, definition_changes,
                                              cut_patterns, sandbagging)

    # compute_execution_score()'s own formula, inputs and output shape are
    # untouched above - _execution_score_with_coverage() only ADDS a
    # disclosure line, and only when --use-history actually engaged, so a
    # caller who never passes it sees byte-identical scores to before this
    # store existed. "Never silently change what a score means": if the
    # history feature changed this number, that fact is printed, not implied.
    if use_stored_history:
        execution_score = _execution_score_with_coverage(execution_score, history, store_key)

    structured_history = build_structured_history(history, changes)

    result = {
        "company": label, "query": name, "venue": venue, "slug": slug,
        "retrieved_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "releases_scanned": len(scanned),
        "date_range": [scanned[-1]["date"][:10], scanned[0]["date"][:10]] if scanned else [],
        "ir_domains_tried": domains,
        "ir_note": ir_note,
        "standing_targets": targets,
        "guidance_history": history,
        "changes": changes,
        "report_actuals": report_actuals,
        "esef_actuals": esef,
        "structured_history": structured_history,
        "definition_changes": definition_changes,
        "cut_patterns": cut_patterns,
        "sandbagging": sandbagging,
        "execution_score": execution_score,
        "notes": notes,
        "disclaimer": GUIDANCE_LABEL,
        "store_identity": store_identity,
        "store_key": store_key,
        "store_identity_note": store_identity_note,
        "store_archive_meta": archive_meta,
        "store_history_used": use_stored_history,
        "store_history_merged_count": stored_merged,
    }
    return result, notes


def _cli_store_save(result, query):
    """--save/--store: persist what THIS run extracted (never what a prior
    --use-history merge pulled back in - see the `_from_store` guard below,
    which is exactly the flag run() set on merged-in rows)."""
    key = result.get("store_key")
    if not key:
        print(result.get("store_identity_note") or
              ("GUIDANCE NOT STORED: no LEI or ISIN could be resolved for %r - "
               "storing under a display name risks merging two issuers, so "
               "nothing was written." % query))
        return
    archive_meta = result["store_archive_meta"]
    statements = [guidance_row_to_statement(row, result["venue"], archive_meta)
                 for row in result["guidance_history"] if not row.get("_from_store")]
    statements += [target_row_to_statement(row, archive_meta)
                  for row in result["standing_targets"]]
    fetch_meta = {"run_utc": result["retrieved_utc"], "venue": result["venue"],
                 "slug": result["slug"], "releases_scanned": result["releases_scanned"],
                 "date_range": result["date_range"], "deep_archive": archive_meta.get("deep"),
                 "archive_note": archive_meta.get("note"),
                 "ir_domains_tried": result["ir_domains_tried"], "ir_note": result["ir_note"]}
    doc, added, revised = guidance_store_merge(
        key, result.get("store_identity"), result["company"] or query, statements, fetch_meta)
    print(BAR)
    print("GUIDANCE STORE")
    print(BAR)
    print("store key %s: %d new statement(s) saved (%d of them linked as a "
          "revision of a prior statement), %d already on file unchanged."
          % (key, added, revised, len(statements) - added))
    print("%d statement(s) on file in total for this issuer." % len(doc["statements"]))
    print()


def _cli_store_read(args):
    """--stored-history / --revisions: read-only, and offline whenever the
    company is already in the store's alias index - only a company never
    seen before falls through to a live identity resolution (still no
    guidance re-fetch: this never touches MFN/Cision/ESEF)."""
    key = guidance_store_index_lookup(args.company)
    note = None
    if not key:
        identity, note = resolve_store_identity(None, args.company, args.country)
        key = guidance_store_key(identity) if identity else None
    if not key:
        print(note or ("DATA NOT AVAILABLE: no LEI or ISIN could be resolved for "
                       "%r, so no guidance store entry could exist under any "
                       "name for it." % args.company))
        raise SystemExit(1)
    doc = guidance_store_load(key)
    if not doc:
        print("No stored guidance history for %r (identity key %s). Run a plain "
              "query with --save first to start one." % (args.company, key))
        raise SystemExit(1)
    company_label = (doc.get("identity") or {}).get("company_name") or args.company
    if args.as_json:
        payload = (guidance_store_revision_chain(doc) if args.revisions
                  else guidance_store_history_rows(doc))
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return
    if args.revisions:
        print_guidance_revisions(doc, company_label)
    else:
        print_guidance_store_history(doc, company_label)


def _selftest():
    """Offline assertions for the guidance store added in v3.0.0. Extraction
    (scan_guidance/scan_targets/judge/...) has its own, much larger,
    coverage in tests/test_guidance_store.py and the rest of the suite; this
    is deliberately narrow - the store's own persistence contract, run
    through `python guidance_track.py --selftest` with no network and no
    real ~/.investment-analyst touched.
    """
    ok = 0

    # --- idempotency key: stable across calls, sensitive to content --------
    a = _statement_id("https://mfn.se/a/x/1", "ebitda_margin", "FY2025", "We expect 66-68%.")
    b = _statement_id("https://mfn.se/a/x/1", "ebitda_margin", "FY2025", "We expect 66-68%.")
    c = _statement_id("https://mfn.se/a/x/1", "ebitda_margin", "FY2025", "We expect 70-72%.")
    assert a == b and a != c
    ok += 2

    # --- identity: LEI first, ISIN second, name-only refused ---------------
    assert guidance_store_key({"lei": "5493004QAI1UOX9SR347"}) == "LEI-5493004QAI1UOX9SR347"
    assert guidance_store_key({"lei": None, "isin": "SE0000667891"}) == "ISIN-SE0000667891"
    assert guidance_store_key({"company_name": "Some Co"}) is None
    assert guidance_store_key(None) is None
    ok += 4

    with tempfile.TemporaryDirectory() as tmp:
        old = os.environ.get("GUIDANCE_STORE_HOME")
        os.environ["GUIDANCE_STORE_HOME"] = tmp
        try:
            key = "LEI-5493004QAI1UOX9SR347"
            ident = {"lei": "5493004QAI1UOX9SR347", "isin": "SE0000667891",
                     "company_name": "Sandvik AB", "legal_name": "Sandvik Aktiebolag"}
            row = {"date": "2025-07-17", "first_said": "2025-07-17", "metric": "ebitda_margin",
                   "quant": {"kind": "range", "low": 66.0, "high": 68.0, "unit": "%"},
                   "applies_to": "FY2025", "period_inferred": False, "withdrawn": False,
                   "adjusted_basis": True, "title": "Q2 2025 report",
                   "url": "https://mfn.se/a/sandvik/q2-2025", "sentence": "We expect 66-68%.",
                   "repeated_on": []}
            archive_meta = {"channel": "MFN", "deep": True, "note": ""}
            stmt = guidance_row_to_statement(row, "MFN", archive_meta)

            # save() -> load() round trip
            doc, added, revised = guidance_store_merge(
                key, ident, "Sandvik", [stmt], {"run_utc": "2025-07-17T00:00:00Z",
                                                "venue": "MFN", "deep_archive": True})
            assert added == 1 and revised == 0
            back = guidance_store_load(key)
            assert back is not None and len(back["statements"]) == 1
            assert back["statements"][0]["adjusted_basis"] is True
            assert back["statements"][0]["basis_label"].startswith("adjusted")
            ok += 5

            # idempotent: the SAME release merged again adds nothing
            doc2, added2, revised2 = guidance_store_merge(
                key, ident, "Sandvik", [stmt], {"run_utc": "2025-08-01T00:00:00Z",
                                                "venue": "MFN", "deep_archive": True})
            assert added2 == 0 and len(doc2["statements"]) == 1
            ok += 1

            # a revision: same metric/period, different number, appended -
            # the original stays retrievable, both ends of the link are set
            row2 = dict(row, quant={"kind": "range", "low": 63.0, "high": 65.0, "unit": "%"},
                       first_said="2025-10-20", date="2025-10-20",
                       url="https://mfn.se/a/sandvik/q3-2025",
                       sentence="We now expect 63-65%, a cut from our previous guidance.")
            stmt2 = guidance_row_to_statement(row2, "MFN", archive_meta)
            doc3, added3, revised3 = guidance_store_merge(
                key, ident, "Sandvik", [stmt2], {"run_utc": "2025-10-20T00:00:00Z",
                                                 "venue": "MFN", "deep_archive": True})
            assert added3 == 1 and revised3 == 1
            assert len(doc3["statements"]) == 2      # nothing overwritten, nothing dropped
            old_row = [s for s in doc3["statements"] if s["id"] == stmt["id"]][0]
            new_row = [s for s in doc3["statements"] if s["id"] == stmt2["id"]][0]
            assert old_row["superseded_by"] == new_row["id"]
            assert new_row["supersedes"] == old_row["id"]
            chain = guidance_store_revision_chain(doc3)
            assert len(chain) == 1 and chain[0]["direction"] == "LOWERED"
            ok += 5

            # no truncation at any bound - append 250 distinct statements and
            # confirm every one of them (thesis_ledger caps status_history at
            # 200; this store must not repeat that defect)
            many = []
            for i in range(250):
                r = dict(row, applies_to="FY%d" % (1900 + i), first_said="2020-01-01",
                         date="2020-01-01", url="https://mfn.se/a/sandvik/bulk-%d" % i,
                         sentence="bulk guidance statement number %d" % i)
                many.append(guidance_row_to_statement(r, "MFN", archive_meta))
            doc4, added4, _ = guidance_store_merge(
                key, ident, "Sandvik", many, {"run_utc": "2026-01-01T00:00:00Z",
                                              "venue": "MFN", "deep_archive": True})
            assert added4 == 250
            assert len(doc4["statements"]) == 2 + 250
            ok += 2

            # shallow-archive caveat is recorded and surfaced
            shallow_meta = {"channel": "MFN", "deep": False,
                            "note": "MFN deep archive unavailable; only the ~30 "
                                    "most recent releases were read."}
            shallow_row = dict(row, applies_to="FY2030", first_said="2030-01-01",
                              date="2030-01-01", url="https://mfn.se/a/sandvik/shallow",
                              sentence="shallow-fetch guidance statement")
            shallow_stmt = guidance_row_to_statement(shallow_row, "MFN", shallow_meta)
            doc5, _, _ = guidance_store_merge(
                key, ident, "Sandvik", [shallow_stmt],
                {"run_utc": "2030-01-01T00:00:00Z", "venue": "MFN", "deep_archive": False,
                 "archive_note": shallow_meta["note"]})
            found = [s for s in doc5["statements"] if s["id"] == shallow_stmt["id"]][0]
            assert found["archive_depth"]["deep"] is False
            assert any(fl.get("deep_archive") is False for fl in doc5["fetch_log"])
            ok += 2

            # load() of a company never saved returns None, not {} or a crash
            assert guidance_store_load("LEI-00000000000000000000") is None
            ok += 1
        finally:
            if old is None:
                os.environ.pop("GUIDANCE_STORE_HOME", None)
            else:
                os.environ["GUIDANCE_STORE_HOME"] = old

    # --- execution score is untouched when the feature is never engaged ---
    # compute_execution_score() itself takes no store-related argument at
    # all (see the call in run()); the disclosure line is added by run()
    # ONLY when use_stored_history=True, so a caller that never asked for it
    # gets the exact facts dict compute_execution_score() always produced.
    empty = compute_execution_score([], [], [], [], [])
    assert "coverage_statement" not in empty["facts"]
    ok += 1

    print("guidance_track selftest: %d assertions passed" % ok)
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("company", nargs="?", help="company name, e.g. \"Sandvik\"")
    ap.add_argument("--targets", action="store_true",
                    help="only the standing financial targets")
    ap.add_argument("--history", action="store_true",
                    help="only the dated guidance history and outcomes")
    ap.add_argument("--execution", action="store_true",
                    help="only the structured history and the management execution score")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--no-ir", action="store_true",
                    help="skip the IR-site crawl (faster; targets will be missing)")
    ap.add_argument("--country", default="SE",
                    help="ISO-2 country for the ESEF lookup (default SE)")
    ap.add_argument("--releases", type=int, default=500,
                    help="how many MFN releases to pull (default 500)")
    ap.add_argument("--save", "--store", dest="save_history", action="store_true",
                    help="persist this run's extracted guidance/targets to the "
                         "guidance store (~/.investment-analyst/guidance)")
    ap.add_argument("--use-history", action="store_true",
                    help="fold previously stored guidance into this run's join "
                         "and management execution score (disclosed in the "
                         "output as 'coverage_statement'); off by default so a "
                         "plain run's score never silently changes")
    ap.add_argument("--stored-history", action="store_true",
                    help="print the STORED guidance record for this company over "
                         "time, newest first - no live fetch, offline when the "
                         "company is already on file")
    ap.add_argument("--revisions", action="store_true",
                    help="print just the stored revision chain for this company "
                         "- no live fetch")
    ap.add_argument("--selftest", action="store_true",
                    help="offline self-test of the guidance store; touches "
                         "neither the network nor the real store")
    args = ap.parse_args()

    if args.selftest:
        raise SystemExit(_selftest())

    if not args.company:
        ap.error("give a company name, or use --selftest")

    if args.stored_history or args.revisions:
        _cli_store_read(args)
        return

    result, notes = run(args.company, do_ir=not args.no_ir,
                        esef_country=args.country, limit_releases=args.releases,
                        use_stored_history=args.use_history,
                        save_to_store=args.save_history)
    if result is None:
        for n in notes:
            print(n)
        raise SystemExit(1)

    if args.save_history:
        _cli_store_save(result, args.company)

    if args.as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    print(BAR)
    print("GUIDANCE TRACK RECORD - %s" % (result["company"] or args.company))
    print(BAR)
    print("release source : %s (%s), %d release(s) scanned%s"
          % (result["venue"], result["slug"], result["releases_scanned"],
             ", %s to %s" % tuple(result["date_range"]) if result["date_range"] else ""))
    print("retrieved      : %s" % result["retrieved_utc"][:19])
    print()

    any_flag = args.targets or args.history or args.execution
    show_targets = args.targets or not any_flag
    show_history = args.history or not any_flag
    show_execution = args.execution or not any_flag
    show_summary = (not any_flag) or args.history

    if show_targets:
        print_targets(result["standing_targets"], result["ir_note"], result["company"])
    if show_history:
        print_history(result["guidance_history"], result["changes"], result["company"])
    if show_execution:
        print_structured_history(result["structured_history"])
        print_execution(result["execution_score"], result["cut_patterns"],
                        result["definition_changes"], result["sandbagging"], result["company"])
    if show_summary:
        print_summary(result["guidance_history"], result["standing_targets"],
                      result["changes"], result["company"], result["notes"])


if __name__ == "__main__":
    main()
