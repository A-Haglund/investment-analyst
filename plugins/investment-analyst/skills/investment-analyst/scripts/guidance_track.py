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

# Sibling scripts are importable helpers, not subprocesses.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import mfn_news as MFN            # noqa: E402
import cision_news as CIS         # noqa: E402
import esef_fundamentals as ESEF  # noqa: E402

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
# Orchestration
# ---------------------------------------------------------------------------

def _dedupe_by_slug(hits):
    out, seen = [], set()
    for h in hits:
        slug = h.get("slug")
        if slug and slug not in seen:
            seen.add(slug)
            out.append(h)
    return out


def _identity_ambiguous(venue, name, cands):
    listing = ", ".join("%s (%s)" % (c["name"], c["slug"]) for c in cands)
    return ("COMPANY_IDENTITY_AMBIGUOUS: %d distinct %s issuers match %r (%s). "
            "Attributing a guidance record to the wrong one is silent and looks "
            "identical to a correct answer - re-run with the exact legal name."
            % (len(cands), venue, name, listing))


def resolve_company(name):
    """Find the company on MFN first, then Cision. Returns (venue, slug, label, note).

    `note` is None on a clean single match. When the query matches more than
    one distinct issuer (by slug) - "Volvo" resolves to both AB Volvo and
    Volvo Car AB on MFN - this refuses instead of letting whichever hit
    happened to come first win: venue/slug/label come back None and `note`
    names every candidate seen, the same discipline company_resolve.py's
    brand guard applies.
    """
    try:
        hits = MFN.search(name)
    except SystemExit:
        hits = []
    needle = name.lower()

    named = _dedupe_by_slug([h for h in hits if needle in (h["name"] or "").lower()
                             or needle in h["slug"]])
    with_archive = []
    for h in named:
        items, _ = mfn_archive(h["slug"], want=60)
        if items:
            with_archive.append(h)
    if len(with_archive) > 1:
        return None, None, None, _identity_ambiguous("MFN", name, with_archive)
    if with_archive:
        h = with_archive[0]
        return "MFN", h["slug"], h["name"], None

    try:
        chits = CIS.resolve(name)
    except SystemExit:
        chits = []
    cnamed = _dedupe_by_slug([h for h in chits if needle in (h["name"] or "").lower()
                              or needle in h["slug"]])
    if len(cnamed) > 1:
        return None, None, None, _identity_ambiguous("Cision", name, cnamed)
    if cnamed:
        h = cnamed[0]
        return "Cision", h["slug"], h["name"], None

    # Last-resort fallback: neither search matched on name/slug at all, so
    # fall back to whatever the search engine ranked first - but still refuse
    # rather than guess if that raw result set itself spans more than one
    # issuer.
    hits_d, chits_d = _dedupe_by_slug(hits), _dedupe_by_slug(chits)
    if len(hits_d) > 1:
        return None, None, None, _identity_ambiguous("MFN", name, hits_d)
    if hits_d:
        return "MFN", hits_d[0]["slug"], hits_d[0]["name"], None
    if len(chits_d) > 1:
        return None, None, None, _identity_ambiguous("Cision", name, chits_d)
    if chits_d:
        return "Cision", chits_d[0]["slug"], chits_d[0]["name"], None
    return None, None, None, None


def run(name, do_ir=True, esef_country="SE", limit_releases=500):
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
    else:
        items = cision_archive(slug)
        notes.append("Cision issuer: only report-like releases were fetched for "
                     "their body text, and Cision publishes no regulatory tag.")
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
    }
    return result, notes


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("company", help="company name, e.g. \"Sandvik\"")
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
    args = ap.parse_args()

    result, notes = run(args.company, do_ir=not args.no_ir,
                        esef_country=args.country, limit_releases=args.releases)
    if result is None:
        for n in notes:
            print(n)
        raise SystemExit(1)

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
