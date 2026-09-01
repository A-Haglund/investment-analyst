#!/usr/bin/env python3
"""Trailing twelve months, assembled from interim reports rather than assumed.

THE PROBLEM THIS EXISTS TO FIX. ESEF carries ANNUAL reports only, and the
filings.xbrl.org index lags the filing itself. Verified 2026-08-31:

    Sandvik    latest structured data  FY 2024-12-31   20 months stale
    Evolution  latest structured data  FY 2024-12-31   20 months stale
    H&M        latest structured data  FY 2024-11-30   21 months stale
    Addtech    latest structured data  FY 2024-03-31   29 months stale

The price is live. Every multiple built on those numbers is today's price over
two-year-old earnings. The interim reports exist and are published; nothing in
this toolkit assembled them into a trailing twelve months. This does.

THE ARITHMETIC (spec 6)

    TTM = latest YTD + previous full FY - previous-year YTD over the same months

The awkward cases are the point:

  * NON-CALENDAR FILERS. Addtech and Lagercrantz close 31 March, H&M 30
    November, Sectra 30 April. December is never assumed - the fiscal year end
    is read out of the report's own stated period range, cross-checked against
    the ESEF filing's period_end, and if the two disagree the run refuses.
  * CUMULATIVE vs DISCRETE. Nordic issuers mostly publish year-to-date figures;
    Sandvik publishes discrete quarters only. Both are handled, and where both
    exist the two answers are compared against each other.
  * THE COMPARATIVE IS FREE. An interim report states the prior-year figure for
    the same months in the same sentence, so two of the three terms come out of
    one document. That is why this works at all.
  * A MISSING QUARTER yields TTM_INCOMPLETE naming the uncovered window. It
    never silently closes the gap.
  * A RESTATED PRIOR YEAR: the figure from the most recently published report
    wins, and the superseded figure is printed beside it.
  * A FISCAL-YEAR CHANGE (a stated "full year" that is not twelve months, or a
    year end that moves) refuses rather than guesses.
  * DISCONTINUED OPERATIONS in any contributing report raise a flag, because
    mixing a continuing-operations interim into a total-operations annual is a
    silent basis change.

HOW RELIABLE IS THIS. The annual term can usually be corroborated against ESEF
XBRL. The interim terms cannot: they are parsed out of press-release prose, and
prose changes format without warning. Every figure therefore carries the exact
source line it was read from, so a misparse is visible on the page rather than
buried in a multiple. Treat a text-derived TTM as tier-2 evidence - good enough
to stop you valuing a company on 2024 earnings, not good enough to skip reading
the report before you act.

Usage:
    python ttm_engine.py "Sandvik"
    python ttm_engine.py "Evolution" --metric revenue --explain
    python ttm_engine.py "Addtech" --explain
    python ttm_engine.py "H&M" --json
    python ttm_engine.py "KebNi" --as-of 2026-05-01
    python ttm_engine.py "Sandvik" --cision-slug sandvik --lei 5299008ZUAXN43LVZF54
    python ttm_engine.py --selftest            # period arithmetic + number parsing

WHERE THE REPORTS COME FROM. MFN's per-issuer feed (/a/<slug>.json) returns only
the last thirty releases, which for an active issuer is two months - not far
enough back to reach last year's comparative report. The global feed filtered by
author (/all/a.json?author=<slug>&offset=N) pages through the full history, and
it also carries MFN's mirror of Cision releases under /cis/a/<slug>/. That
mirror is why Sandvik, Atlas Copco and Evolution's Cision-distributed reports
are reachable here even though mfn_news.py's own company feed is empty for them.
Cision is still read directly for the issuers MFN does not mirror, such as H&M,
and there the RSS description is truncated at about 600 characters, so the
release page itself is fetched and cached.

Free and keyless. Sources: MFN.se, Cision, filings.xbrl.org (ESEF). Coverage
is European (Nordic/French ESEF) issuers only.
Python 3 standard library only.
"""
import argparse
import calendar
import datetime
import hashlib
import html
import importlib.util
import subprocess
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


finfact = _load("finfact")
FinancialFact = finfact.FinancialFact
State = finfact.State
Verification = finfact.Verification
Mode = finfact.Mode
corroborate = finfact.corroborate

_mfn = None
_cision = None
_esef = None


def mfn():
    global _mfn
    if _mfn is None:
        _mfn = _load("mfn_news")
    return _mfn


def cision():
    global _cision
    if _cision is None:
        _cision = _load("cision_news")
    return _cision


def esef():
    global _esef
    if _esef is None:
        _esef = _load("esef_fundamentals")
    return _esef


# ==========================================================================
# Cache. Release pages are immutable once published; feeds are not.
# ==========================================================================

CACHE = os.path.join(tempfile.gettempdir(), "investment-analyst-ttm")
NO_CACHE = False


def cached(key, ttl, produce):
    if NO_CACHE:
        return produce()
    try:
        os.makedirs(CACHE, exist_ok=True)
    except OSError:
        return produce()
    path = os.path.join(CACHE, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")
    try:
        if time.time() - os.path.getmtime(path) < ttl:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
    except (OSError, ValueError):
        pass
    value = produce()
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(value, fh)
    except (OSError, TypeError):
        pass
    return value


# ==========================================================================
# Dates. Nothing here may assume a December year end.
# ==========================================================================

DAY = datetime.timedelta(days=1)


def d(value):
    if value is None or isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def month_end(year, month):
    return datetime.date(year, month, calendar.monthrange(year, month)[1])


def shift_years(date, n):
    """Move a date n years, keeping a month end a month end.

    28 February 2026 shifted back a year must land on 28 February 2025, but
    30 November must not become 30 November of a month with 31 days by accident.
    Snapping only when the input is already the last day of its month keeps both
    honest.
    """
    was_month_end = date.day == calendar.monthrange(date.year, date.month)[1]
    year = date.year + n
    last = calendar.monthrange(year, date.month)[1]
    if was_month_end:
        return datetime.date(year, date.month, last)
    return datetime.date(year, date.month, min(date.day, last))


def months_span(start, end):
    """Whole months covered by an inclusive [start, end] period, else None.

    A quarter is 89 to 92 days and a year 365 or 366, so day counts alone cannot
    tell 3 months from 3 months and a week. This measures calendar months and
    returns None when the range does not sit on month boundaries.
    """
    if start is None or end is None:
        return None
    if start.day != 1:
        return None
    if end != month_end(end.year, end.month):
        return None
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


def fy_bounds(date, fye_md):
    """(start, end) of the fiscal year containing `date`, given a MM-DD year end."""
    month, day = int(fye_md[:2]), int(fye_md[3:5])
    end = datetime.date(date.year, month, min(day, calendar.monthrange(date.year, month)[1]))
    if date > end:
        end = datetime.date(date.year + 1, month,
                            min(day, calendar.monthrange(date.year + 1, month)[1]))
    start = shift_years(end, -1) + DAY
    return start, end


def fy_end_for_label(year, fye_md):
    """The fiscal year end of the year an issuer would call `year`.

    Convention across every Nordic issuer checked: the fiscal year is named for
    the calendar year in which it ENDS. H&M's "Bokslutskommunike 2025" closes
    30 November 2025; Addtech writes 2025/2026 for the year ending 31 March 2026.
    """
    month, day = int(fye_md[:2]), int(fye_md[3:5])
    return datetime.date(year, month, min(day, calendar.monthrange(year, month)[1]))


# ==========================================================================
# Number parsing.
#
# Two ways to be wrong by three orders of magnitude, both of which look
# plausible on the page:
#
#   "1,030.8"  English thousands comma with a decimal point.
#   "16,6"     Nordic decimal comma.
#
# And one footnote trap: H&M prints "MSEK 2 983 1" where the trailing 1 is a
# footnote marker for a restated comparative. Read naively that is 29,831 -
# a tenfold error in the prior-year figure that a growth rate would hide.
# Space-separated groups must therefore be exactly three digits or the number
# ends there.
#
# And a third trap, this one about the sign rather than the magnitude:
# typeset financial documents use U+2212 MINUS SIGN or an en/em-dash where a
# keyboard writes a hyphen. mfn_news.py hit this first (see its MINUS_CHARS /
# normalise_minus) - unnormalised, a negative EBITDA parses as positive with
# no warning, and the sign-change sanity check in _finish explicitly declines
# to flag it because a real swing to profit looks identical.
# ==========================================================================

MINUS_CHARS_RE = re.compile("[−–—‐‑‒]")

NUM = r"-?[\s ]?\d[\d   ]*(?:[.,]\d+(?:[.,]\d+)?)?"


def parse_number(raw):
    """Return (value, truncated). `truncated` means trailing junk was dropped."""
    if raw is None:
        return None, False
    s = raw.replace(" ", " ").replace(" ", " ").strip()
    s = MINUS_CHARS_RE.sub("-", s)
    negative = s.startswith("-")
    s = s.lstrip("-").strip()
    m = re.match(r"\d+", s)
    if not m:
        return None, False
    integer = m.group(0)
    pos = m.end()
    truncated = False
    decimal = None

    # Space-grouped thousands, strictly three digits per group.
    while pos < len(s) and s[pos] == " ":
        nxt = re.match(r"\d+", s[pos + 1:])
        if not nxt:
            break
        if len(nxt.group(0)) != 3:
            truncated = True          # footnote marker or an adjacent number
            break
        integer += nxt.group(0)
        pos += 1 + nxt.end()

    rest = s[pos:]
    seps = re.findall(r"[.,](?=\d)", rest)
    if seps:
        groups = re.findall(r"[.,](\d+)", rest)
        if len({c for c in seps}) > 1:
            # Both separators present: the LAST one is the decimal point.
            last = rest.rfind(seps[-1])
            integer += re.sub(r"[.,]", "", rest[:last])
            decimal = re.sub(r"\D", "", rest[last:])
        elif len(seps) > 1:
            integer += "".join(groups)          # repeated separator = thousands
        elif len(groups[0]) == 3 and len(integer) <= 3:
            integer += groups[0]                # "37,799" - thousands
        else:
            decimal = groups[0]                 # "16,6" / "3.35" - decimal

    try:
        value = float(integer + ("." + decimal if decimal else ""))
    except ValueError:
        return None, truncated
    return (-value if negative else value), truncated


# ==========================================================================
# Scale and currency tokens.
# ==========================================================================

CUR_SCALE = {
    "MSEK": (1e6, "SEK"), "MKR": (1e6, "SEK"), "MDKR": (1e9, "SEK"),
    "KSEK": (1e3, "SEK"), "TSEK": (1e3, "SEK"), "TKR": (1e3, "SEK"),
    "MEUR": (1e6, "EUR"), "KEUR": (1e3, "EUR"), "TEUR": (1e3, "EUR"),
    "MNOK": (1e6, "NOK"), "TNOK": (1e3, "NOK"),
    "MDKK": (1e6, "DKK"), "TDKK": (1e3, "DKK"),
    "MUSD": (1e6, "USD"),
    "SEK": (1.0, "SEK"), "EUR": (1.0, "EUR"), "NOK": (1.0, "NOK"),
    "DKK": (1.0, "DKK"), "USD": (1.0, "USD"), "KR": (1.0, "SEK"),
}
WORD_SCALE = {
    "MILLION": 1e6, "MILLIONS": 1e6, "MILJONER": 1e6, "MN": 1e6,
    "BILLION": 1e9, "BILLIONS": 1e9, "MILJARD": 1e9, "MILJARDER": 1e9, "MDR": 1e9,
    "THOUSAND": 1e3, "THOUSANDS": 1e3, "TUSEN": 1e3,
    # H&M's English releases write "SEK 49,607 m". Without the bare "m" every
    # H&M figure comes out a million times too small - and because every term
    # of the TTM is wrong by the same factor, no ratio check catches it.
    "M": 1e6, "BN": 1e9, "MD": 1e9,
}
PERCENT = {"%", "PERCENT", "PROCENT"}

_CUR_TOK = "|".join(sorted(CUR_SCALE, key=len, reverse=True))
_WORD_TOK = "|".join(sorted(list(WORD_SCALE) + ["PERCENT", "PROCENT"],
                            key=len, reverse=True))
SCALE_TOK = r"(?:\b(?:%s)\b|%%)" % ("|".join([_CUR_TOK, _WORD_TOK]),)

# "(KSEK)" or "(MSEK)" in a heading scopes every bullet beneath it.
HEAD_UNIT_RE = re.compile(r"\(\s*(%s)\s*\)" % _CUR_TOK, re.I)

FIGURE_RE = re.compile(
    r"(?P<label>[^()\n]{2,110}?)"
    r"[\s,:]*"
    r"(?P<pre>" + SCALE_TOK + r")?\s*"
    r"(?P<cur>" + NUM + r")"
    r"\s*(?P<post>" + SCALE_TOK + r")?"
    r"[^()\d]{0,22}?"
    r"\(\s*(?P<pre2>" + SCALE_TOK + r")?\s*"
    r"(?P<prev>" + NUM + r")"
    r"\s*(?P<post2>" + SCALE_TOK + r")?\s*\)",
    re.IGNORECASE | re.UNICODE)


def token_scale(token):
    """(factor, currency, is_percent) for a matched scale token."""
    if not token:
        return None, None, False
    t = token.strip().upper()
    if t in PERCENT:
        return None, None, True
    if t in CUR_SCALE:
        factor, cur = CUR_SCALE[t]
        return factor, cur, False
    if t in WORD_SCALE:
        return WORD_SCALE[t], None, False
    return None, None, False


# ==========================================================================
# Period grammar.
#
# Everything downstream depends on knowing exactly which months a number
# covers. KebNi's Q2 release prints "Net sales 28 838" and "Net sales 41 881"
# under different headings; picking the wrong one is a 45% error with a
# perfectly correct-looking citation.
# ==========================================================================

MONTHS = {}
for _i, _names in enumerate([
        ("january", "januari", "jan"), ("february", "februari", "feb"),
        ("march", "mars", "mar"), ("april", "apr"),
        ("may", "maj"), ("june", "juni", "jun"),
        ("july", "juli", "jul"), ("august", "augusti", "aug"),
        ("september", "sep", "sept"), ("october", "oktober", "okt", "oct"),
        ("november", "nov"), ("december", "dec")], start=1):
    for _n in _names:
        MONTHS[_n] = _i

_MONTH_TOK = "|".join(sorted(MONTHS, key=len, reverse=True))
DASH = r"[-‐-―−]|\bto\b|\btill\b|\bthrough\b"

# "1 april 2025 - 31 mars 2026", "1 December 2025 - 31 May 2026"
RANGE_FULL_RE = re.compile(
    r"(\d{1,2})\s*(%s)[a-z]*\.?\s*(\d{4})?\s*(?:%s)\s*(\d{1,2})\s*(%s)[a-z]*\.?\s*(\d{4})"
    % (_MONTH_TOK, DASH, _MONTH_TOK), re.I)
# "Oct-Dec 2025", "January-June 2026"
RANGE_MONTHS_RE = re.compile(
    r"\b(%s)[a-z]*\.?\s*(?:%s)\s*(%s)[a-z]*\.?\s*(\d{4})\b" % (_MONTH_TOK, DASH, _MONTH_TOK),
    re.I)
ORDINAL = {"first": 1, "second": 2, "third": 3, "fourth": 4,
           "forsta": 1, "andra": 2, "tredje": 3, "fjarde": 4,
           "första": 1, "fjärde": 4}
QUARTER_WORD_RE = re.compile(
    r"\b(first|second|third|fourth|första|forsta|andra|tredje|fjärde|fjarde)\s+"
    r"(?:quarter|kvartalet|kvartal)\b(?:\s*(?:of|av)?\s*(\d{4}))?", re.I)
QUARTER_Q_RE = re.compile(r"\bQ\s?([1-4])\s*[/\-]?\s*(\d{4})?\b", re.I)
CUMULATIVE_WORDS = [
    (12, r"\bhelår|\bhelar\b|\bfull[- ]year\b|räkenskapsåret|\bbokslutskommunik|"
         r"\byear[- ]end report\b|\btwelve months\b|\btolv månader\b"),
    (9, r"\bniomånaders|\bnine[- ]month|\b9M\b|\bnio månader\b"),
    (6, r"\bsexmånaders|\bsix[- ]month|\bhalvårs|\bfirst half\b|\bförsta halvåret|\b1H\b"),
    (3, r"\btremånaders|\bthree[- ]month\b"),
]
R12_RE = re.compile(
    r"senaste tolvmånaders|senaste tolv månader|rullande (?:12|tolv)|"
    r"rolling (?:12|twelve)|(?:last|latest) twelve[- ]month|"
    r"(?:last|latest) 12[- ]month|\bLTM\b|\bR12\b|trailing twelve", re.I)
YEAR_RE = re.compile(r"\b(20\d{2})\s*/\s*(20\d{2})\b|\b(20\d{2})\b")


def _period_spans(text):
    """(start, end) of every period expression in `text`, earliest first."""
    spans = []
    for rx in (RANGE_FULL_RE, RANGE_MONTHS_RE, QUARTER_WORD_RE, QUARTER_Q_RE):
        for m in rx.finditer(text):
            spans.append((m.start(), m.end()))
    for _months, pattern in CUMULATIVE_WORDS:
        for m in re.finditer(pattern, text, re.I):
            spans.append((m.start(), m.end()))
    return sorted(set(spans))


def _is_heading_span(block, span):
    """A period expression only scopes figures when it reads as a heading.

    Two shapes qualify: it opens the block, or a scale tag follows it -
    "Financial development Jan-Sep 2025 (KSEK)". Prose that merely mentions a
    period ("despite that the Q4 results were below expectations") must not
    re-date the numbers around it, which is why a bare mid-sentence match is
    ignored.
    """
    start, end = span
    if start <= 2:
        return True
    return bool(HEAD_UNIT_RE.search(block[end:end + 15]))


def split_sections(block):
    """Split a block wherever a second heading appears inside it.

    KebNi's Q3 release runs "Financial development Jul-Sep 2025 (KSEK) ...
    Financial development Jan-Sep 2025 (KSEK) ..." with no blank line between,
    so both the quarter and the nine-month column arrive as one block and the
    nine-month figures get dated as the quarter. That is a 3.4x error on EBITDA
    with a citation that points at the right report.
    """
    cuts = [start for span in _period_spans(block)
            for start in ([span[0]] if _is_heading_span(block, span) and span[0] > 2
                          else [])]
    if not cuts:
        return [block]
    out, prev = [], 0
    for cut in cuts:
        if cut - prev < 20:
            continue
        out.append(block[prev:cut])
        prev = cut
    out.append(block[prev:])
    return [b for b in out if b.strip()]


def leading_period(block, fye_md, default_year):
    """The heading period this block is written under, or None."""
    for span in _period_spans(block[:160]):
        if _is_heading_span(block, span):
            return parse_period(block[span[0]:span[0] + 110], fye_md, default_year)
    return None


class Period(object):
    __slots__ = ("start", "end", "months", "kind", "label")

    def __init__(self, start, end, kind, label):
        self.start = start
        self.end = end
        self.months = months_span(start, end)
        self.kind = kind
        self.label = label

    def key(self):
        return (self.start.isoformat(), self.end.isoformat())

    def __repr__(self):
        return "%s..%s(%sm)" % (self.start, self.end, self.months)


def _year_from(text, fallback):
    m = YEAR_RE.search(text)
    if not m:
        return fallback
    if m.group(2):
        return int(m.group(2))
    return int(m.group(1) or m.group(3))


# Addtech and Lagercrantz write a split fiscal year as "2025/26", and the
# module's own convention (see fy_end_for_label) is that a fiscal year is
# named for the calendar year it ENDS in - "2025/26" ends in 2026. QUARTER_Q_RE
# captures a bare "(\d{4})?" right after the quarter number, so on "Q1 2025/26"
# it grabs "2025" - the year the fiscal year STARTS in - and every downstream
# window lands twelve months early with no error, because a wrong year still
# produces a well-formed Period. This is authoritative over whatever a quarter
# or cumulative-words regex captured on its own.
SPLIT_YEAR_RE = re.compile(r"\b(20\d{2})\s*/\s*((?:20)?\d{2})\b")


def _label_year(text, fallback):
    m = SPLIT_YEAR_RE.search(text)
    if m:
        b = m.group(2)
        return int(b) if len(b) == 4 else int(m.group(1)[:2] + b)
    return _year_from(text, fallback)


def parse_period(text, fye_md, default_year, source="heading"):
    """Read a period out of a heading or a report title. None when unreadable.

    Precedence is explicit-dates first, because a stated range cannot be
    misconstrued the way "Q2 2026" can when the fiscal year does not start in
    January.
    """
    if not text:
        return None
    t = re.sub(r"\s+", " ", text)

    m = RANGE_FULL_RE.search(t)
    if m:
        d1, mo1, y1, d2, mo2, y2 = m.groups()
        mo1, mo2 = MONTHS[mo1.lower()], MONTHS[mo2.lower()]
        y2 = int(y2)
        y1 = int(y1) if y1 else (y2 if mo1 <= mo2 else y2 - 1)
        try:
            start = datetime.date(y1, mo1, int(d1))
            end = datetime.date(y2, mo2, int(d2))
        except ValueError:
            return None
        if end <= start:
            return None
        return Period(start, end, "stated_range", m.group(0))

    m = RANGE_MONTHS_RE.search(t)
    if m:
        mo1, mo2, year = MONTHS[m.group(1).lower()], MONTHS[m.group(2).lower()], int(m.group(3))
        y1 = year if mo1 <= mo2 else year - 1
        return Period(datetime.date(y1, mo1, 1), month_end(year, mo2),
                      "stated_range", m.group(0))

    if fye_md is None:
        return None

    m = QUARTER_WORD_RE.search(t)
    q = None
    if m:
        q = ORDINAL[m.group(1).lower()]
        year = int(m.group(2)) if m.group(2) else _label_year(t, default_year)
    else:
        m = QUARTER_Q_RE.search(t)
        if m:
            q = int(m.group(1))
            year = int(m.group(2)) if m.group(2) else _label_year(t, default_year)
    if q is not None and SPLIT_YEAR_RE.search(t):
        # A split-year token beats whatever the quarter regex itself captured
        # - "Q1 2025/26" has QUARTER_Q_RE grab "2025", the fiscal year's START
        # year, not the year it is named for.
        year = _label_year(t, default_year)
    if q is not None:
        fy_end = fy_end_for_label(year, fye_md)
        fy_start = shift_years(fy_end, -1) + DAY
        # Quarters are counted from the FISCAL year start, not from January.
        # Addtech's second quarter is July to September; reading it as April to
        # June puts the whole TTM window three months out.
        start = _add_months(fy_start, 3 * (q - 1))
        end = fy_end if q == 4 else _add_months(fy_start, 3 * q) - DAY
        return Period(start, end, "quarter", m.group(0))

    for months, pattern in CUMULATIVE_WORDS:
        if re.search(pattern, t, re.I):
            year = _label_year(t, default_year)
            fy_end = fy_end_for_label(year, fye_md)
            fy_start = shift_years(fy_end, -1) + DAY
            end = fy_end if months == 12 else _add_months(fy_start, months) - DAY
            return Period(fy_start, end, "cumulative", re.search(pattern, t, re.I).group(0))
    return None


def _add_months(date, n):
    month = date.month - 1 + n
    year = date.year + month // 12
    month = month % 12 + 1
    return datetime.date(year, month, min(date.day, calendar.monthrange(year, month)[1]))


# ==========================================================================
# Metric classification.
#
# Two rules do most of the work. First, "adjusted" is never folded into the
# reported figure - Sandvik publishes "Adjusted profit for the period" and
# "Profit for the period" three lines apart and they differ by 10%. Second,
# a bracket holding a percentage is a margin, not last year's number.
# ==========================================================================

METRIC_RULES = [
    ("eps", r"earnings per share|resultat per aktie|vinst per aktie|"
            r"per share|per aktie|per stamaktie",
     r"dividend|utdelning|kassaflöde|cash flow|antal aktier|number of shares|"
     r"equity per|eget kapital per"),
    ("ebitda", r"\bEBITDA\b", None),
    ("ebita", r"\bEBITA\b", None),
    ("gross_profit", r"gross profit|bruttoresultat|bruttovinst", None),
    # cfo is tested BEFORE operating_income on purpose. H&M's English H1 2025
    # release reads "Cash flow from operating profit amounted to SEK 12,729 m",
    # which the operating-profit pattern takes for an operating result 79% too
    # high. A cash-flow sentence is a cash-flow sentence whatever it calls the
    # thing the cash came from.
    ("cfo",
     r"cash flow from (?:the )?operat|kassaflöde(?:t)? från den löpande|"
     r"operating cash flow",
     r"\bfree\b|\bfritt\b|per share|per aktie"),
    ("operating_income",
     r"\bEBIT\b|operating profit|operating income|rörelseresultat|rorelseresultat",
     r"margin|marginal|cash flow|kassaflöde"),
    ("net_income",
     r"profit for the period|profit/loss for the period|net profit|net income|"
     r"resultat(?:et)? efter skatt|periodens resultat|årets resultat|nettoresultat|"
     r"profit after tax",
     r"before tax|före skatt|per share|per aktie"),
    ("revenue",
     r"net revenue|net sales|nettoomsättning|nettoomsattning|\brevenues?\b|"
     r"\bturnover\b|\bomsättning",
     r"total operating|other operating|övriga rörelse|order|orderingång|"
     r"growth|tillväxt|organic|organisk|fixed exchange|constant currency|"
     r"local currenc|lokala valutor|årsomsättning|per share|per aktie|"
     r"margin|marginal|share of|andel"),
]
ADJUSTED_RE = re.compile(
    r"adjusted|justerad|justerat|exklusive|excluding|underlying|jämförelsestörande",
    re.I)
DISCONTINUED_RE = re.compile(
    r"discontinued operation|avvecklad(?:e)? verksamhet|avyttrad verksamhet|"
    r"held for sale|innehav för försäljning|assets held for", re.I)

PER_SHARE_RE = re.compile(r"per share|per aktie|/share|/aktie", re.I)
# Only a per-share phrase IMMEDIATELY after the bracket belongs to this figure.
# "SEK 631 million (562) and earnings per share ... SEK 2.30 (2.00)" holds two
# figures; letting the second one's words reach back over the first turns a
# 631-million profit into an earnings-per-share number.
TAIL_PER_SHARE_RE = re.compile(r"^[\s,.]{0,3}(?:per share|per aktie|/share|/aktie)", re.I)
# A margin sits right before its number. Checking only the tail of the prefix
# keeps "increased by 11 percent and amounted to SEK 1,026 million" alive while
# dropping "corresponds to a record-high margin of 16.0 (15.0) percent", which
# is otherwise indistinguishable from an EBITA figure.
MARGIN_TAIL_RE = re.compile(r"margin|marginal|andel av|share of", re.I)


SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÅÄÖ])")


def _classify_window(text):
    """The prose that actually describes this number: its own sentence.

    H&M writes "In local currencies, net sales increased by 2 percent in the
    2025 financial year. Converted into SEK, the H&M group's net sales amounted
    to SEK 228,285 m (234,478)." in one paragraph. Reading the whole paragraph,
    the "local currencies" exclusion - which exists to reject constant-currency
    growth rates - silently threw away the full-year revenue, and with it the
    prior-FY term of every H&M TTM.
    """
    window = text[-220:]
    parts = SENTENCE_END_RE.split(window)
    return parts[-1] if parts else window


def classify(prefix, tail):
    """Metric name for a figure, from the prose that precedes it. None to drop."""
    if MARGIN_TAIL_RE.search(prefix[-30:]):
        return None
    per_share_tail = bool(TAIL_PER_SHARE_RE.match(tail))
    for metric, positive, negative in METRIC_RULES:
        if metric == "eps":
            hit = per_share_tail or bool(re.search(positive, prefix, re.I))
        else:
            hit = bool(re.search(positive, prefix, re.I))
        if not hit:
            continue
        if negative and re.search(negative, prefix, re.I):
            continue
        if ADJUSTED_RE.search(prefix):
            return "adjusted_" + metric
        return metric
    return None


# ==========================================================================
# Observations - the shared ledger both text and XBRL feed into.
# ==========================================================================

class Obs(object):
    __slots__ = ("metric", "start", "end", "value", "currency", "source",
                 "report_title", "report_url", "published", "source_line",
                 "comparative", "synthetic", "note", "precision")

    def __init__(self, metric, start, end, value, currency, source,
                 report_title=None, report_url=None, published=None,
                 source_line=None, comparative=False, synthetic=False, note=None,
                 precision=0):
        self.metric = metric
        self.start = start
        self.end = end
        self.value = value
        self.currency = currency
        self.source = source
        self.report_title = report_title
        self.report_url = report_url
        # Publication dates arrive as dates from the text path and as strings
        # from ESEF. One representation, or every comparison is a bug.
        self.published = (published.isoformat() if isinstance(published, datetime.date)
                          else (str(published)[:10] if published else None))
        self.source_line = source_line
        self.comparative = comparative
        self.synthetic = synthetic
        self.note = note
        # Significant digits in the figure as printed. A CEO letter saying
        # "EBITDA for the full year amounted to 10,3 MSEK" and the statement
        # bullet saying "10 275" are the same fact at two precisions, and the
        # rounded one must not be the version that reaches a valuation.
        self.precision = precision

    @property
    def months(self):
        return months_span(self.start, self.end)

    def key(self):
        return (self.metric, self.start.isoformat(), self.end.isoformat())

    def describe(self):
        tag = " [prior-year comparative]" if self.comparative else ""
        tag += " [synthesised]" if self.synthetic else ""
        return "%s (%s, %s)%s" % (self.report_title or "?", self.published or "?",
                                  self.source, tag)

    def to_dict(self):
        return {"metric": self.metric, "period_start": self.start.isoformat(),
                "period_end": self.end.isoformat(), "months": self.months,
                "value": self.value, "currency": self.currency,
                "source": self.source, "report": self.report_title,
                "url": self.report_url, "published": self.published,
                "source_line": self.source_line, "comparative": self.comparative,
                "synthetic": self.synthetic, "note": self.note}


# ==========================================================================
# Text extraction.
# ==========================================================================

def blocks_of(text, wrapped):
    """Split a release body into blocks a heading cannot leak across.

    MFN carries hard-wrapped plain text, so a bullet's comparative can sit on
    the next line and the lines must be rejoined - that is what mfn_news.reflow
    does. A Cision release page is one complete paragraph per line, and running
    the same reflow over it welds the period heading onto the first figure
    beneath it, which silently dates every number in the release wrongly. So the
    two are treated differently, and the Cision path only rejoins a line that is
    obviously a broken sentence.
    """
    if wrapped:
        return mfn().reflow(text)
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if out and re.match(r"[a-zåäöéü,;]", line) and not re.search(r"[.!?:)]$", out[-1]):
            out[-1] += " " + line
        else:
            out.append(line)
    return out


def extract_observations(text, title, published, fye_md, source, url,
                         default_currency=None, wrapped=None):
    """Every figure in a release body, with the period it belongs to.

    Returns (observations, warnings). Each money figure yields two observations:
    the stated period and, from the bracketed comparative, the same period one
    fiscal year earlier.
    """
    out, warnings = [], []
    if not text:
        return out, warnings
    # Normalise typographic minus signs to an ASCII hyphen before anything else
    # touches this text. Left alone, a Unicode minus inside a bracket also
    # breaks FIGURE_RE's match on the comparative (NUM is ASCII-hyphen-only),
    # silently dropping the figure - normalising here fixes both failures at
    # once, upstream of every regex that runs over the block.
    text = MINUS_CHARS_RE.sub("-", text)

    if wrapped is None:
        wrapped = (source == "mfn")
    default_year = published.year if published else datetime.date.today().year
    title_period = parse_period(title, fye_md, default_year)
    if title_period and fye_md:
        # A bare "the full year" in a January release means the year that just
        # ended, not the one that just started. Anchoring the default on the
        # report's own fiscal year rather than on its publication year is what
        # makes that come out right.
        default_year = fy_bounds(title_period.end, fye_md)[1].year
    current = title_period
    horizon = (published + datetime.timedelta(days=10)) if published else None
    head_scale, head_cur = None, None

    raw_blocks = []
    for b in blocks_of(text, wrapped):
        raw_blocks.extend(split_sections(b))

    def usable(period):
        """No report covers a period that had not finished when it was published."""
        if period is None or period.months is None:
            return False
        return horizon is None or period.end <= horizon

    for block in raw_blocks:
        lead = leading_period(block, fye_md, default_year)
        if usable(lead):
            current = lead
        matches = list(FIGURE_RE.finditer(block))
        unit_head = HEAD_UNIT_RE.search(block)
        if unit_head:
            head_scale, head_cur = CUR_SCALE[unit_head.group(1).upper()]

        emitted = 0
        cursor = 0
        for m in matches:
            prefix = _classify_window(block[cursor:m.start("cur")])
            cursor = m.end()
            tail = block[m.end():m.end() + 24]

            _sf, _sc, cur_pct = token_scale(m.group("post"))
            _pf, _pc, prev_pct = token_scale(m.group("post2"))
            metric = classify(prefix, tail)
            if metric is None:
                continue
            per_share = ("eps" in metric or PER_SHARE_RE.search(prefix)
                         or TAIL_PER_SHARE_RE.match(tail))
            if cur_pct:
                continue                     # a percentage is not the metric

            value, truncated = parse_number(m.group("cur"))
            digits = len(re.sub(r"\D", "", m.group("cur") or ""))
            prev, prev_trunc = parse_number(m.group("prev"))
            if value is None:
                continue
            if prev_pct:
                prev = None                  # the bracket held a margin

            pre_f, pre_c, _ = token_scale(m.group("pre"))
            post_f, post_c, _ = token_scale(m.group("post"))
            currency = pre_c or post_c or head_cur or default_currency
            if per_share:
                factor = 1.0
            else:
                factor = (pre_f or 1.0) * (post_f or 1.0)
                if m.group("pre") is None and m.group("post") is None and head_scale:
                    factor = head_scale
            if not per_share and currency is None:
                # No currency token on the line, none in the heading: the scale
                # is unknown, and a number whose scale is unknown is not a
                # figure. Dropping it is the only safe reading.
                continue
            value *= factor
            if prev is not None:
                pre2_f, pre2_c, _ = token_scale(m.group("pre2"))
                post2_f, post2_c, _ = token_scale(m.group("post2"))
                prev *= 1.0 if per_share else ((pre2_f or post2_f) and
                                               (pre2_f or 1.0) * (post2_f or 1.0) or factor)
                currency = currency or pre2_c or post2_c

            # Carry the words AROUND the matched number, not the first 200
            # characters of the block. A citation that stops before the figure
            # it is citing cannot be checked, which defeats the point.
            line = re.sub(r"\s+", " ",
                          block[max(0, m.start("cur") - 110):m.end() + 40]).strip()
            if truncated or prev_trunc:
                warnings.append("number with non-standard digit grouping (footnote "
                                "marker?) in %s: %s" % (title, line[:120]))

            # A disclosed rolling-twelve-month figure is a DIRECT TTM, not a
            # period figure - Addtech states R12 earnings per share outright.
            if R12_RE.search(prefix):
                if current is None:
                    continue
                end = current.end
                start = shift_years(end, -1) + DAY
                out.append(Obs(metric, start, end, value, currency, source,
                               title, url, published, line, note="disclosed R12",
                               precision=digits))
                continue

            # A sentence can carry its own period, and it beats the block
            # heading. KebNi's Q4 release opens with "EBITDA for the full year
            # amounted to 10,3 MSEK" above any heading; read under the release
            # title that is a fourth-quarter figure, and the four-quarter sum
            # then disagrees with the bridge by 246%.
            scope = current
            own = parse_period(prefix, fye_md, default_year, source="sentence")
            if usable(own):
                scope = own

            if scope is None:
                warnings.append("figure with no readable period in %s: %s"
                                % (title, line[:120]))
                continue

            out.append(Obs(metric, scope.start, scope.end, value, currency,
                           source, title, url, published, line, precision=digits))
            emitted += 1
            if prev is not None:
                out.append(Obs(metric, shift_years(scope.start, -1),
                               shift_years(scope.end, -1), prev, currency, source,
                               title, url, published, line, comparative=True,
                               precision=len(re.sub(r"\D", "", m.group("prev") or ""))))

        # A short block that yielded no figure is a heading. Evolution's
        # "January-December 2025 (2024)" looks exactly like a figure to any
        # bracket-matching regex - year, bracket, year - so a block can only be
        # ruled a heading AFTER its figures have failed to classify. Before this
        # check the whole full-year column landed under the Q4 heading above it.
        if emitted == 0 and len(block) < 160:
            found = parse_period(block, fye_md, default_year)
            if found:
                current = found
    return out, warnings


# ==========================================================================
# Report harvesting.
# ==========================================================================

REPORT_TITLE_RE = re.compile(
    r"interim report|delarsrapport|delårsrapport|year[- ]end report|"
    r"bokslutskommunik|quarterly report|kvartalsrapport|half[- ]year|halvårsrapport|"
    r"six[- ]month|three[- ]month|nine[- ]month|twelve[- ]month|full[- ]year report|"
    r"sexmånaders|tremånaders|niomånaders|\bQ[1-4]\b|"
    r"(?:first|second|third|fourth) quarter|(?:första|andra|tredje|fjärde) kvartal",
    re.I)
INVITE_RE = re.compile(r"inbjudan|invitation|presentation av|webcast|teleconference|"
                       r"kallelse|notice of", re.I)


def is_report_title(title):
    return bool(title and REPORT_TITLE_RE.search(title) and not INVITE_RE.search(title))


def harvest_mfn(slug, pages=6):
    """Report releases from MFN, paged.

    /a/<slug>.json caps at the last 30 items, which for an active issuer is two
    months. /all/a.json?author=<slug> pages properly, which is the only way to
    reach last year's comparative report.
    """
    items = []
    for page in range(pages):
        def produce(page=page):
            try:
                return mfn().fetch("/all/a.json", limit=50, offset=page * 50, author=slug)
            except SystemExit:
                return {}
        data = cached("ttm:mfnpage:%s:%d" % (slug, page), 6 * 3600, produce)
        rows = (data or {}).get("items") or []
        if not rows:
            break
        items += [mfn().flatten(r) for r in rows]

    reports, seen = [], set()
    for it in items:
        if not (it.get("is_report") or is_report_title(it.get("title"))):
            continue
        if INVITE_RE.search(it.get("title") or ""):
            continue
        published = (it.get("date") or "")[:10]
        key = (published, it.get("lang") == "en", it.get("url"))
        if key in seen:
            continue                  # the identical item, seen twice across paged fetches
        seen.add(key)
        reports.append({"title": it["title"], "url": it["url"], "published": published,
                        "lang": it.get("lang"), "text": it.get("text") or "",
                        "source": "mfn"})
    # An issuer publishes the same report in Swedish and English on the same
    # day - that pair is deliberately collapsed to one (English preferred).
    # But grouping by date ALONE could not tell that pair apart from two
    # genuinely different reports that happen to land on the same day, and
    # collapsed those too, silently dropping one. Only fold a date's items
    # together when they are exactly that pair - one Swedish, one English;
    # anything else on the same date (two same-language reports, or three or
    # more items) is kept as separate reports rather than guessed away.
    by_date = {}
    for r in reports:
        by_date.setdefault(r["published"], []).append(r)
    best = []
    for rows in by_date.values():
        langs = {r["lang"] for r in rows}
        if len(rows) == 2 and len(langs) == 2:
            best.append(next((r for r in rows if r["lang"] == "en"), rows[0]))
        else:
            best.extend(rows)
    return sorted(best, key=lambda r: r["published"], reverse=True)


def cision_text(url):
    """Plain text of a Cision release page.

    The RSS description is truncated at roughly 600 characters, which for
    Sandvik cuts the bullet list in half. The release page carries the whole
    thing, and once published it never changes - so it is cached hard.
    """
    def produce():
        try:
            raw = cision().http_get(url, timeout=45)
        except SystemExit:
            return ""
        page = raw.decode("utf-8", errors="replace")
        page = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", page)
        page = re.sub(r"(?i)<br\s*/?>", "\n", page)
        page = re.sub(r"(?i)</(p|div|li|tr|h\d|td)>", "\n", page)
        page = re.sub(r"<[^>]+>", " ", page)
        page = html.unescape(page)
        lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in page.splitlines()]
        text = "\n".join(ln for ln in lines if ln)
        marker = text.find("Report this content")
        return text[marker + len("Report this content"):] if marker >= 0 else text
    return cached("ttm:cisionpage:" + url, 30 * 86400, produce)


MONTH_ABBR = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
              "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
              "maj": 5, "okt": 10}


def rfc822_date(text):
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})\w*\s+(\d{4})", text or "")
    if not m:
        return None
    month = MONTH_ABBR.get(m.group(2).lower())
    if not month:
        return None
    try:
        return datetime.date(int(m.group(3)), month, int(m.group(1)))
    except ValueError:
        return None


def harvest_cision(slug, pages=4, max_bodies=10):
    """Report releases from Cision, English newsroom preferred."""
    def collect(english):
        def produce():
            try:
                return cision().releases(slug, pages, english)
            except SystemExit:
                return []
        return cached("ttm:cision:%s:%d:%d" % (slug, pages, int(english)), 6 * 3600, produce)

    chosen, lang = [], None
    for english in (True, False):
        rows = collect(english)
        reports = [r for r in rows if is_report_title(r.get("title"))]
        if reports:
            chosen, lang = reports, ("en" if english else "sv")
            break
    out = []
    for r in chosen[:max_bodies]:
        published = rfc822_date(r.get("date"))
        out.append({"title": r["title"], "url": r["url"],
                    "published": published.isoformat() if published else "",
                    "lang": lang, "text": cision_text(r["url"]), "source": "cision"})
    return sorted([r for r in out if r["published"]],
                  key=lambda r: r["published"], reverse=True)


# ==========================================================================
# ESEF: the annual term, and the fiscal-year-end cross-check.
# ==========================================================================

ESEF_METRIC = {"revenue": "revenue", "operating_income": "operating_income",
               "net_income": "net_income", "eps": "eps_basic",
               "gross_profit": "gross_profit", "cfo": "cfo"}


def esef_observations(lei, filings=3):
    """Annual IFRS figures as observations, plus the filing period ends."""
    mod = esef()

    def produce():
        rows = mod.list_filings(lei, filings)
        docs = []
        for f in rows:
            try:
                docs.append((f, mod.get_json(mod.FILINGS_BASE + f["json_url"])))
            except SystemExit:
                continue
        packed = []
        for f, doc in docs:
            facts = mod.extract(doc)
            found = {}
            for metric, esef_name in ESEF_METRIC.items():
                for concept in mod.CONCEPTS.get(esef_name, []):
                    rows2 = facts.get(concept)
                    if not rows2:
                        continue
                    for start, end, value, unit in rows2:
                        if start is None:
                            continue
                        try:
                            days = (datetime.date.fromisoformat(end)
                                    - datetime.date.fromisoformat(start)).days
                        except ValueError:
                            continue
                        if not (330 <= days <= 400):
                            continue
                        found.setdefault((metric, start, end),
                                         (value, unit, concept))
                    if found:
                        break
            packed.append({"filing": f, "facts": [
                {"metric": k[0], "start": k[1], "end": k[2], "value": v[0],
                 "unit": v[1], "concept": v[2]} for k, v in found.items()]})
        return packed

    packed = cached("ttm:esef:%s:%d" % (lei, filings), 7 * 86400, produce)
    out, ends = [], []
    for entry in packed:
        f = entry["filing"]
        ends.append(f["period_end"])
        for row in entry["facts"]:
            unit = row.get("unit") or ""
            currency = unit.split(":", 1)[1] if unit.startswith("iso4217:") and "/" not in unit else None
            # xBRL states an inclusive start with an end that may be exclusive;
            # esef_fundamentals.normalise_end already handled that on the end.
            start = d(row["start"])
            end = d(esef().normalise_end(row["end"]))
            # `published` must be when the filing became knowable, not the
            # fiscal period it describes - an FY2024 figure stamped 2024-12-31
            # would clear an as-of gate months before the annual report
            # existed (see valuation_gate.py / thesis_ledger.py, which use
            # exactly this indexed_date as their publication-upper-bound).
            # indexed_date is itself a conservative upper bound (the index's
            # harvest date, not the issuer's own publication date), which is
            # the safe direction to be wrong in. When it is missing there is
            # no safe date to fall back to - period_end would reintroduce the
            # same look-ahead this fix exists to remove - so `published`
            # stays None and the as-of gate treats it as unknown.
            published = f.get("indexed_date")
            note = (None if published else
                    "publication date unknown: the ESEF filing index has no "
                    "indexed_date for this filing, so it cannot be dated and "
                    "is excluded from any as-of gate")
            out.append(Obs(row["metric"], start, end, row["value"], currency, "esef",
                           "ESEF filing %s" % f.get("fxo_id"),
                           esef().FILINGS_BASE + f["json_url"], published,
                           "xbrl concept %s" % row["concept"], note=note))
    return out, sorted(set(ends), reverse=True)


# ==========================================================================
# Identity. Cheap, explicit, and it prints what it chose.
# ==========================================================================

def _norm(text):
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _score(query, name):
    q, n = _norm(query), _norm(name)
    if not q or not n:
        return 0
    if q == n:
        return 100
    if n.startswith(q):
        return 80 - min(len(n) - len(q), 30)
    if q in n:
        return 50 - min(len(n) - len(q), 30)
    return 0


# Below this, all that is left is the weak end of a bare substring hit (as
# low as 20 - "q in n" with nothing else in common) - not enough to commit an
# entire TTM run to one issuer's identity. company_resolve.py's whole reason
# to exist is refusing exactly this kind of guess; locate() had no floor at
# all before this, and no check that the top score was not shared by two
# different issuers either.
MIN_LOCATE_SCORE = 40


def _pick(ranked, notes, field_label, query):
    """The winning hit from a ranked (score, hit) list, or None.

    Refuses - rather than guessing - below MIN_LOCATE_SCORE, and refuses when
    the top score is tied between hits that are not the same issuer (printed
    into `notes` the way company_resolve.py prints its tied candidates,
    rather than silently picking whichever happened to sort first).
    """
    if not ranked or ranked[0][0] < MIN_LOCATE_SCORE:
        return None
    top = ranked[0][0]
    tied = [h for score, h in ranked if score == top]
    if len(tied) > 1 and len({_norm(h["name"]) for h in tied}) > 1:
        notes.append(
            "REFUSING %s match for %r: tied at score %d between different "
            "issuers - %s. Pass the slug/LEI explicitly instead of guessing."
            % (field_label, query, top, "; ".join(sorted(h["name"] for h in tied))))
        return None
    return tied[0]


def locate(name, country="SE", mfn_slug=None, cision_slug=None, lei=None, use_esef=True):
    """Resolve a name to the feeds that carry its reports. Never guesses silently."""
    found = {"query": name, "mfn_slug": mfn_slug, "cision_slug": cision_slug,
             "lei": lei, "notes": []}

    if not mfn_slug:
        try:
            hits = cached("ttm:mfnsearch:" + name, 7 * 86400,
                          lambda: mfn().search(name, 12))
        except SystemExit:
            hits = []
        ranked = sorted(((_score(name, h["name"]), h) for h in hits),
                        key=lambda x: -x[0])
        hit = _pick(ranked, found["notes"], "MFN", name)
        if hit:
            found["mfn_slug"] = hit["slug"]
            found["mfn_name"] = hit["name"]

    if not cision_slug:
        try:
            hits = cached("ttm:cisionsearch:" + name, 7 * 86400,
                          lambda: cision().resolve(name))
        except SystemExit:
            hits = []
        ranked = sorted(((_score(name, h["name"]), h) for h in hits), key=lambda x: -x[0])
        hit = _pick(ranked, found["notes"], "Cision", name)
        if hit:
            found["cision_slug"] = hit["slug"]
            found["cision_name"] = hit["name"]

    if not lei and use_esef and country:
        try:
            hits = cached("ttm:esefsearch:%s:%s" % (name, country), 7 * 86400,
                          lambda: esef().search_index(name, country.upper()))
        except SystemExit:
            hits = []
        ranked = sorted(((_score(name, h["name"]), h) for h in hits), key=lambda x: -x[0])
        hit = _pick(ranked, found["notes"], "ESEF", name)
        if hit:
            found["lei"] = hit["lei"]
            found["esef_name"] = hit["name"]
            found["esef_latest_fy"] = hit["latest"]
    return found


# ==========================================================================
# Fiscal year end: DETECTED, never assumed.
# ==========================================================================

def detect_fye(reports, esef_ends):
    """(fye_md, source, warnings). The report's own stated range beats everything.

    A stated "Helar (1 april 2025 - 31 mars 2026)" is the issuer telling you the
    year end in its own words. ESEF's period_end is the cross-check. If the two
    disagree the fiscal year has moved and nothing downstream is safe.
    """
    warnings = []
    stated = {}
    for r in reports:
        for text in (r["title"], r["text"][:4000]):
            for m in RANGE_FULL_RE.finditer(re.sub(r"\s+", " ", text or "")):
                d1, mo1, y1, d2, mo2, y2 = m.groups()
                mo1i, mo2i = MONTHS[mo1.lower()], MONTHS[mo2.lower()]
                y2 = int(y2)
                y1i = int(y1) if y1 else (y2 if mo1i <= mo2i else y2 - 1)
                try:
                    start = datetime.date(y1i, mo1i, int(d1))
                    end = datetime.date(y2, mo2i, int(d2))
                except ValueError:
                    continue
                if 350 <= (end - start).days + 1 <= 380:
                    stated["%02d-%02d" % (end.month, end.day)] = \
                        stated.get("%02d-%02d" % (end.month, end.day), 0) + 1

    if not stated:
        for r in reports:
            for text in (r["title"], r["text"][:4000]):
                for m in RANGE_MONTHS_RE.finditer(re.sub(r"\s+", " ", text or "")):
                    mo1 = MONTHS[m.group(1).lower()]
                    mo2 = MONTHS[m.group(2).lower()]
                    year = int(m.group(3))
                    y1 = year if mo1 <= mo2 else year - 1
                    start = datetime.date(y1, mo1, 1)
                    end = month_end(year, mo2)
                    if months_span(start, end) == 12:
                        key = "%02d-%02d" % (end.month, end.day)
                        stated[key] = stated.get(key, 0) + 1

    from_esef = None
    if esef_ends:
        mds = {e[5:] for e in esef_ends}
        from_esef = esef_ends[0][5:]
        if len(mds) > 1:
            warnings.append(
                "ESEF period ends disagree across filings (%s). The fiscal year may "
                "have been changed; TTM across the boundary is not defensible."
                % ", ".join(sorted(mds)))

    from_text = max(stated, key=stated.get) if stated else None

    if from_text and from_esef and from_text != from_esef:
        warnings.append(
            "REFUSING: the reports state a fiscal year ending %s but the ESEF "
            "filings end %s. Either the year end moved or the wrong issuer was "
            "resolved. Nothing is computed on a guess." % (from_text, from_esef))
        return None, "conflict", warnings
    if from_text:
        return from_text, "stated period range in the issuer's own report", warnings
    if from_esef:
        return from_esef, "ESEF filing period_end (%s)" % esef_ends[0], warnings

    warnings.append("Fiscal year end could not be established from any free source. "
                    "It is NOT safe to assume 31 December.")
    return None, None, warnings


# ==========================================================================
# The ledger and the TTM assembly.
# ==========================================================================

def build_ledger(observations):
    """{(metric, start, end): [obs newest-published first]} with restatements kept."""
    ledger = {}
    for o in observations:
        if o.months is None:
            continue
        ledger.setdefault(o.key(), []).append(o)
    for rows in ledger.values():
        # reverse=True sorts every component descending, so the "wins on a
        # tie" side of each component must be the LARGER value:
        #   published:  later date string sorts higher  -> newest wins
        #   precision:  more significant digits          -> the exact figure
        #               (not the rounded one) wins
        #   esef flag:  1 for esef, 0 otherwise           -> the tagged filing
        #               beats prose on an equal date and precision. Getting
        #               this flag backwards (0 for esef) let any text figure
        #               with one extra decimal digit outrank the filing, and
        #               ESEF facts are always recorded at precision=0, so it
        #               would lose on every real tie.
        rows.sort(key=lambda o: (o.published or "", o.precision,
                                 1 if o.source == "esef" else 0),
                  reverse=True)
    return ledger


def pick_obs(ledger, metric, start, end, as_of=None):
    """The current figure for a period, plus any superseded value it replaced.

    `as_of` is optional so every existing call site keeps its old behaviour
    (the newest restatement, full stop) when it is omitted. Passed, it picks
    the newest restatement that was actually known by that date - without it,
    a restatement filed after `as_of` could be handed back on a run that is
    supposed to be blind to anything published after that date (see
    cover_window(), which has the same gap).
    """
    rows = ledger.get((metric, start.isoformat(), end.isoformat()))
    if not rows:
        return None, None
    if as_of is None:
        best = rows[0]
    else:
        best = next((o for o in rows if (o.published or "") <= as_of), None)
        if best is None:
            return None, None
    superseded = None
    for other in rows:
        if other is best:
            continue
        if best.value and abs(other.value - best.value) / abs(best.value) > 0.005:
            superseded = other
            break
    return best, superseded


def synthesise_discretes(ledger, fye_md):
    """Q_n = YTD_n - YTD_(n-1). A legitimate derivation, flagged as one.

    Sandvik publishes discrete quarters and no year-to-date; Evolution the
    reverse. Filling in the other form from what an issuer does publish is what
    lets the two assembly methods check each other.
    """
    made = []
    by_metric = {}
    for (metric, start, end), rows in ledger.items():
        by_metric.setdefault(metric, []).append((d(start), d(end), rows[0]))
    for metric, rows in by_metric.items():
        cumulative = {}
        for start, end, obs in rows:
            fy_start, _fy_end = fy_bounds(end, fye_md)
            if start == fy_start:
                cumulative.setdefault((fy_start, obs.months), (end, obs))
        for (fy_start, months), (end, obs) in cumulative.items():
            if months is None or months <= 3:
                continue
            prior = cumulative.get((fy_start, months - 3))
            if not prior:
                continue
            p_end, p_obs = prior
            if obs.currency and p_obs.currency and obs.currency != p_obs.currency:
                continue
            made.append(Obs(metric, p_end + DAY, end, obs.value - p_obs.value,
                            obs.currency or p_obs.currency, obs.source,
                            "%s minus %s" % (obs.report_title, p_obs.report_title),
                            obs.report_url, obs.published,
                            "derived: YTD %dm less YTD %dm" % (months, months - 3),
                            synthetic=True))
    return made


def cover_window(ledger, metric, start, end, as_of=None, allow_synthetic=True):
    """Tile [start, end] exactly with non-overlapping reported periods.

    Returns (pieces, gap). A gap is reported as the uncovered window rather than
    quietly ignored - that is the difference between TTM_INCOMPLETE and a wrong
    number.

    `as_of` gates every candidate piece to what was actually known by that
    date. Without it (the default, so every existing call keeps its old
    behaviour), this always took rows[0] - the newest restatement on record,
    regardless of when it was published - which is exactly how an
    unfiltered observation source could leak a later filing into a TTM run
    for a past as-of date.
    """
    pieces = []
    cursor = end
    guard = 0
    while cursor >= start and guard < 12:
        guard += 1
        candidates = []
        for (m, s, e), rows in ledger.items():
            if m != metric or d(e) != cursor:
                continue
            if as_of is None:
                obs = rows[0]
            else:
                obs = next((o for o in rows if (o.published or "") <= as_of), None)
                if obs is None:
                    continue
            if obs.synthetic and not allow_synthetic:
                continue
            if d(s) < start:
                continue
            candidates.append(obs)
        if not candidates:
            return pieces, (start, cursor)
        # Prefer a reported figure over a synthesised one, then the shortest
        # piece, so a stray twelve-month row cannot swallow the whole window.
        candidates.sort(key=lambda o: (o.synthetic, -(o.start.toordinal())))
        best = candidates[0]
        pieces.append(best)
        cursor = best.start - DAY
        if best.start == start:
            return pieces, None
    return pieces, (start, cursor)


def agrees(metric, a, b):
    """Do two independently assembled TTMs agree?

    A per-share figure is published to two decimals, so four quarters carry up
    to two ore of accumulated rounding. Calling that a conflict would flag every
    small-cap earnings-per-share TTM ever assembled.
    """
    if a is None or b is None:
        return False, None
    if a == 0:
        return b == 0, None
    delta = abs(a - b) / abs(a)
    if "eps" in metric and abs(a - b) <= 0.03:
        return True, delta
    return delta <= 0.01, delta


def rank_verification(v):
    order = {Verification.CONFLICT: 0, Verification.UNVERIFIED: 1,
             Verification.INCOMPLETE: 2, Verification.SINGLE_SOURCE: 3,
             Verification.CROSS_CHECKED: 4, Verification.VERIFIED: 5}
    return order.get(v, 3)


SOURCE_FOR = {"mfn": "mfn", "cision": "cision", "esef": "esef"}
TIER_OF = {"esef": 1, "mfn": 2, "cision": 2}


def assemble(ledger, metric, fye_md, as_of, warnings, anchor_end=None):
    """Build one TTM figure. Returns a result dict, always with a state."""
    result = {"metric": metric, "state": State.TTM_INCOMPLETE.value, "value": None,
              "currency": None, "method": None, "completeness": None,
              "period_start": None, "period_end": None, "reports": [],
              "components": [], "reason": None, "flags": [], "cross_check": None}

    rows = [(d(s), d(e), r[0]) for (m, s, e), r in ledger.items() if m == metric]
    if not rows:
        result["reason"] = "no %s figure was found in any harvested report" % metric
        return result
    rows = [r for r in rows if r[2].published and r[2].published[:10] <= as_of]
    if not rows:
        result["reason"] = ("every %s figure found was published after the as-of "
                            "date %s" % (metric, as_of))
        return result

    latest_end = max(e for _s, e, _o in rows)
    # The window is anchored on the issuer's most recent reported period, not on
    # this metric's most recent one. Otherwise a metric that only the annual
    # report discloses quietly reports a two-year-old fiscal year as "the TTM".
    if anchor_end and latest_end < anchor_end:
        newest = max((o for _s, e, o in rows if e == latest_end),
                     key=lambda o: o.published or "")
        stale_days = (d(as_of) - latest_end).days
        result["reason"] = (
            "TTM_INCOMPLETE: the latest %s figure on record is the period ending "
            "%s, %d days (%.0f months) before the issuer's latest reported period "
            "end %s. No interim report discloses it, so there is no honest TTM - "
            "use the annual figure below and state its age."
            % (metric, latest_end, stale_days, stale_days / 30.44, anchor_end))
        result["state"] = State.DATA_STALE.value
        result["fallback"] = dict(newest.to_dict(), role="stale annual", sign="+")
        result["fallback"]["age_days"] = stale_days
        return result

    ttm_end = latest_end
    ttm_start = shift_years(ttm_end, -1) + DAY
    result["period_start"] = ttm_start.isoformat()
    result["period_end"] = ttm_end.isoformat()

    bridge = _bridge(ledger, metric, fye_md, ttm_end, as_of)

    # ---- DIRECT: the issuer stated a rolling twelve months itself, or the
    # latest period IS a full fiscal year.
    direct, _sup = pick_obs(ledger, metric, ttm_start, ttm_end, as_of)
    if direct is not None and direct.published and direct.published[:10] <= as_of:
        if direct.note == "disclosed R12":
            method, note = "DIRECT", "the issuer disclosed this rolling twelve-month figure"
        elif not direct.synthetic:
            method, note = "DIRECT", "the latest reported period is itself a full fiscal year"
        else:
            method, note = None, None
        if method:
            result.update(state=State.OK.value, value=direct.value,
                          currency=direct.currency, method=method,
                          completeness="COMPLETE",
                          components=[dict(direct.to_dict(), role="TTM", sign="+")],
                          reports=[direct.describe()], reason=note)
            # A figure the issuer states outright is the best evidence there is,
            # and rebuilding it from the period figures is the best check there
            # is. Addtech states R12 earnings per share of 8.25; the bridge
            # returns 2.30 + 7.95 - 2.00 = 8.25. That agreement is worth having.
            if bridge and direct.value:
                ok, delta = agrees(metric, direct.value, bridge["value"])
                result["cross_check"] = {
                    "method": "cumulative bridge (YTD + prior FY - prior-year YTD)",
                    "value": bridge["value"],
                    "relative_difference": round(delta or 0, 6),
                    "agrees": ok}
                if not ok:
                    result["state"] = State.DATA_CONFLICT.value
                    result["flags"].append(
                        "the issuer's own rolling-twelve-month figure and the "
                        "bridge rebuilt from its period figures differ by %.2f%%"
                        % ((delta or 0) * 100))
            _finish(result, ledger, metric, fye_md, ttm_start, ttm_end, warnings, as_of)
            return result

    # ---- Method B: tile the window with reported (or synthesised) periods.
    pieces, gap = cover_window(ledger, metric, ttm_start, ttm_end, as_of)
    tiled = None
    if gap is None and pieces:
        currencies = {p.currency for p in pieces if p.currency}
        if len(currencies) <= 1:
            tiled = {"value": sum(p.value for p in pieces), "pieces": pieces,
                     "currency": (list(currencies) or [None])[0],
                     "synthetic": any(p.synthetic for p in pieces)}

    if bridge:
        result.update(state=State.OK.value, value=bridge["value"],
                      currency=bridge["currency"], method="DERIVED",
                      completeness="COMPLETE",
                      reason="TTM = latest YTD + prior full FY - prior-year YTD",
                      components=[dict(c["obs"].to_dict(), role=c["role"], sign=c["sign"])
                                  for c in bridge["terms"]],
                      reports=_unique([c["obs"].describe() for c in bridge["terms"]]))
        if bridge.get("superseded"):
            result["flags"].append(bridge["superseded"])
        if tiled and bridge["value"]:
            ok, delta = agrees(metric, bridge["value"], tiled["value"])
            result["cross_check"] = {
                "method": "sum of %d reported periods%s" % (
                    len(tiled["pieces"]), " (partly synthesised)" if tiled["synthetic"] else ""),
                "value": tiled["value"], "relative_difference": round(delta or 0, 6),
                "agrees": ok}
            if not ok:
                result["state"] = State.DATA_CONFLICT.value
                result["flags"].append(
                    "the cumulative bridge and the sum of reported periods disagree "
                    "by %.2f%% - one of the two readings is wrong" % ((delta or 0) * 100))
        _finish(result, ledger, metric, fye_md, ttm_start, ttm_end, warnings, as_of)
        return result

    if tiled:
        result.update(state=State.OK.value, value=tiled["value"],
                      currency=tiled["currency"], method="DERIVED",
                      completeness="PARTIAL" if tiled["synthetic"] else "COMPLETE",
                      reason="TTM = sum of %d consecutive reported periods"
                             % len(tiled["pieces"]),
                      components=[dict(p.to_dict(), role="period", sign="+")
                                  for p in sorted(tiled["pieces"], key=lambda o: o.start)],
                      reports=_unique([p.describe() for p in tiled["pieces"]]))
        if tiled["synthetic"]:
            result["flags"].append(
                "at least one quarter was not reported on its own and was derived "
                "as the difference between two year-to-date figures")
        _finish(result, ledger, metric, fye_md, ttm_start, ttm_end, warnings, as_of)
        return result

    if gap:
        result["reason"] = (
            "TTM_INCOMPLETE: no %s figure covers %s to %s. The twelve months "
            "ending %s cannot be assembled without inventing that period."
            % (metric, gap[0], gap[1], ttm_end))
    else:
        result["reason"] = ("TTM_INCOMPLETE: neither the year-to-date bridge nor a "
                            "run of consecutive periods could be completed for %s"
                            % metric)
    return result


def _bridge(ledger, metric, fye_md, ttm_end, as_of):
    """latest YTD + prior FY - prior-year YTD, or None when a term is missing."""
    fy_start, fy_end = fy_bounds(ttm_end, fye_md)
    if ttm_end == fy_end:
        return None                       # that is the annual figure, not a bridge
    # Each pick_obs call is gated on as_of itself now (falling back to an
    # older restatement rather than only ever considering the newest one),
    # so the checks below are a second, redundant confirmation - cheap
    # insurance against this function's own as_of ever going stale relative
    # to what pick_obs used.
    ytd, ytd_sup = pick_obs(ledger, metric, fy_start, ttm_end, as_of)
    if ytd is None or ytd.synthetic or ytd.months is None:
        return None
    if ytd.published and ytd.published[:10] > as_of:
        return None

    prior_fy_end = fy_start - DAY
    prior_fy_start = shift_years(prior_fy_end, -1) + DAY
    prior_fy, prior_fy_sup = pick_obs(ledger, metric, prior_fy_start, prior_fy_end, as_of)
    if prior_fy is None or prior_fy.synthetic:
        return None
    if prior_fy.months != 12:
        return None
    if prior_fy.published and prior_fy.published[:10] > as_of:
        return None

    p_start = shift_years(fy_start, -1)
    p_end = shift_years(ttm_end, -1)
    prior_ytd, _ = pick_obs(ledger, metric, p_start, p_end, as_of)
    if prior_ytd is None or prior_ytd.months != ytd.months:
        return None
    if prior_ytd.published and prior_ytd.published[:10] > as_of:
        return None

    currencies = {o.currency for o in (ytd, prior_fy, prior_ytd) if o.currency}
    if len(currencies) > 1:
        return None

    superseded = None
    if prior_fy_sup:
        superseded = ("prior full year %s restated to %s (was %s in %s) - the "
                      "restated figure is used" % (
                          prior_fy_end, _num(prior_fy.value), _num(prior_fy_sup.value),
                          prior_fy_sup.report_title))
    elif ytd_sup:
        superseded = ("year-to-date %s restated to %s (was %s in %s)" % (
            ttm_end, _num(ytd.value), _num(ytd_sup.value), ytd_sup.report_title))

    return {"value": ytd.value + prior_fy.value - prior_ytd.value,
            "currency": (list(currencies) or [None])[0],
            "superseded": superseded,
            "terms": [{"obs": ytd, "role": "latest YTD", "sign": "+"},
                      {"obs": prior_fy, "role": "prior full FY", "sign": "+"},
                      {"obs": prior_ytd, "role": "prior-year YTD", "sign": "-"}]}


def _finish(result, ledger, metric, fye_md, ttm_start, ttm_end, warnings, as_of=None):
    """Corroboration, plausibility and the discontinued-operations flag."""
    # Corroborate each contributing period across independent origins.
    worst = Verification.VERIFIED
    for comp in result["components"]:
        rows = ledger.get((metric, comp["period_start"], comp["period_end"])) or []
        facts = []
        for o in rows:
            if o.synthetic or o.value is None:
                continue
            try:
                facts.append(FinancialFact(
                    metric, o.value, SOURCE_FOR.get(o.source, "mfn"), o.end,
                    currency=o.currency, period_start=o.start,
                    publication_date=o.published or None,
                    freshness_key="interim_financials"))
            except ValueError:
                continue
        verification, detail = corroborate(facts) if facts else (
            Verification.SINGLE_SOURCE, {})
        comp["verification"] = verification.value
        comp["independent_origins"] = detail.get("independent_origins")
        if rank_verification(verification) < rank_verification(worst):
            worst = verification
    result["verification"] = worst.value

    # Plausibility. A scale misparse is the failure mode with the highest cost
    # and the lowest visibility, so compare against the prior full year.
    fy_start, _fy_end = fy_bounds(ttm_end, fye_md)
    prior_fy_end = fy_start - DAY
    prior_fy, _ = pick_obs(ledger, metric, shift_years(prior_fy_end, -1) + DAY,
                           prior_fy_end, as_of)
    if prior_fy and prior_fy.value and result["value"] is not None:
        ratio = result["value"] / prior_fy.value if prior_fy.value else None
        result["vs_prior_fy"] = {"prior_fy_end": prior_fy_end.isoformat(),
                                 "prior_fy_value": prior_fy.value,
                                 "ratio": round(ratio, 4) if ratio else None}
        # A scale misparse is always a factor of a thousand or a million, so
        # only those bands are called one. A sign flip is a different animal: a
        # company really can go from profit to loss, and calling that a parse
        # error would cry wolf on every loss-making small cap. And a base near
        # zero - KebNi's prior-year net result was -237 KSEK on 125 MSEK of
        # revenue - makes any ratio meaningless, so no ratio is claimed.
        base_revenue, _ = pick_obs(ledger, "revenue",
                                   shift_years(prior_fy_end, -1) + DAY, prior_fy_end, as_of)
        tiny_base = bool("eps" not in metric and base_revenue and base_revenue.value
                         and abs(prior_fy.value) < 0.02 * abs(base_revenue.value))
        scale_like = ratio is not None and (300 <= abs(ratio) <= 3000
                                            or 3e5 <= abs(ratio) <= 3e6)
        if scale_like:
            result["flags"].append(
                "TTM is %.0fx the prior full year in absolute terms - close to a "
                "round factor of a thousand, which is what a scale misparse looks "
                "like. Check the source lines before using this." % abs(ratio))
            result["completeness"] = "PARTIAL"
        elif tiny_base:
            result["flags"].append(
                "the prior full year for this metric (%s) is near zero against "
                "revenue, so no ratio sanity check was possible."
                % _num(prior_fy.value))
        elif ratio is not None and ratio < 0:
            result["flags"].append(
                "sign change against the prior full year (%s -> %s). Verify it "
                "is a real swing and not a period or sign misread."
                % (_num(prior_fy.value), _num(result["value"])))
        elif ratio is not None and not (0.25 <= ratio <= 4.0):
            result["flags"].append(
                "TTM is %.2fx the prior full year - a large move. Read the "
                "source lines before relying on it." % ratio)

    for comp in result["components"]:
        if comp.get("source_line") and DISCONTINUED_RE.search(comp["source_line"]):
            result["flags"].append(
                "a contributing figure's own line mentions discontinued operations; "
                "the continuing- and total-operations bases may be mixed")
            break
    for w in warnings:
        if w not in result["flags"]:
            result["flags"].append(w)


def _unique(seq):
    out = []
    for item in seq:
        if item not in out:
            out.append(item)
    return out


def _num(value):
    if value is None:
        return "n/a"
    if abs(value) < 1000:
        return "%.2f" % value
    return "{:,.0f}".format(value)


# ==========================================================================
# Output
# ==========================================================================

METRIC_ORDER = ["revenue", "gross_profit", "ebitda", "ebita", "operating_income",
                "net_income", "eps", "cfo", "adjusted_ebitda", "adjusted_ebita",
                "adjusted_operating_income", "adjusted_net_income", "adjusted_eps"]


def build_fact(result, fye_md):
    """The FinancialFact this whole module exists to be able to hand over."""
    if result["value"] is None:
        return None
    sources = {c["source"] for c in result["components"]}
    weakest = max(sources, key=lambda s: TIER_OF.get(s, 3))
    published = max((c["published"] or "" for c in result["components"]), default="")
    verification = Verification(result.get("verification", "SINGLE SOURCE"))
    if result["completeness"] == "PARTIAL":
        verification = Verification.INCOMPLETE
    if result["state"] == State.DATA_CONFLICT.value:
        verification = Verification.CONFLICT

    fact = FinancialFact(
        metric=result["metric"] + "_ttm",
        value=result["value"],
        source=SOURCE_FOR.get(weakest, "mfn"),
        period_end=result["period_end"],
        period_start=result["period_start"],
        currency=result["currency"],
        unit="per share" if "eps" in result["metric"] else "currency",
        publication_date=published[:10] or None,
        source_detail="; ".join(result["reports"]),
        verification=verification,
        freshness_key="interim_financials",
        note="method %s, completeness %s; %s" % (result["method"],
                                                 result["completeness"],
                                                 result["reason"]))
    # Spec 6: reduce confidence when the figure is derived rather than reported.
    # A DIRECT annual number and a three-term bridge off press-release prose are
    # not the same evidence, and the confidence must say so.
    penalty = 0.0
    if result["method"] == "DERIVED":
        penalty += 0.05
    if len(result["components"]) >= 4:
        penalty += 0.03
    if result["completeness"] == "PARTIAL":
        penalty += 0.10
    if any(c["source"] in ("mfn", "cision") for c in result["components"]):
        penalty += 0.05          # parsed from prose, not from a tagged filing
    if result.get("cross_check", {}) and result["cross_check"].get("agrees"):
        penalty -= 0.05
    fact.confidence = max(0.0, min(1.0, fact.confidence - penalty))
    fact.note += " | derivation penalty %.2f applied to confidence" % penalty
    return fact


def print_result(result, fact, explain, fye_md):
    metric = result["metric"]
    if result["value"] is None:
        print("  %-22s %s" % (metric, result["state"]))
        print("      %s" % result["reason"])
        fb = result.get("fallback")
        if fb:
            v = ("%.2f" % fb["value"]) if "eps" in metric else "{:,.0f}".format(fb["value"])
            print("      fallback (NOT a TTM): %s %s for %s..%s, %d days old, from %s"
                  % (v, fb["currency"] or "?", fb["period_start"], fb["period_end"],
                     fb["age_days"], fb["report"]))
        return
    unit = result["currency"] or "?"
    value = ("%.2f" % result["value"]) if "eps" in metric else "{:,.0f}".format(result["value"])
    print("  %-22s %18s %-4s  %s..%s  %s/%s  conf %.2f  %s"
          % (metric, value, unit, result["period_start"], result["period_end"],
             result["method"], result["completeness"],
             fact.confidence if fact else 0.0, result.get("verification", "")))
    if result["state"] != State.OK.value:
        print("      STATE: %s" % result["state"])
    if explain:
        print("      %s" % result["reason"])
        for comp in result["components"]:
            v = ("%.2f" % comp["value"]) if "eps" in metric else "{:,.0f}".format(comp["value"])
            print("        %s %18s %-4s  %s..%s  %2sm  %-16s %s"
                  % (comp["sign"], v, comp["currency"] or "?", comp["period_start"],
                     comp["period_end"], comp["months"] or "?", comp["role"],
                     comp["verification"]))
            print("            from: %s" % (comp["report"] or "?"))
            print("            line: %s" % (comp["source_line"] or "")[:150])
        print("        = %18s %-4s  %s..%s"
              % (value, unit, result["period_start"], result["period_end"]))
        if result.get("cross_check"):
            cc = result["cross_check"]
            print("        cross-check (%s): %s  %s (%.3f%%)"
                  % (cc["method"], _num(cc["value"]),
                     "agrees" if cc["agrees"] else "DISAGREES",
                     cc["relative_difference"] * 100))
        if result.get("vs_prior_fy"):
            p = result["vs_prior_fy"]
            print("        prior FY %s: %s   TTM/FY = %sx"
                  % (p["prior_fy_end"], _num(p["prior_fy_value"]), p["ratio"]))
    for flag in result["flags"]:
        print("      ! %s" % flag)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("company", nargs="?", help="company name")
    ap.add_argument("--metric", help="one metric, e.g. revenue, net_income, eps")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--as-of", dest="as_of", help="ignore anything published after "
                                                  "this date (YYYY-MM-DD)")
    ap.add_argument("--explain", action="store_true", help="show the period arithmetic")
    ap.add_argument("--country", default="SE", help="ISO-2 country for the ESEF lookup")
    ap.add_argument("--mfn-slug"), ap.add_argument("--cision-slug"), ap.add_argument("--lei")
    ap.add_argument("--reports", type=int, default=10,
                    help="report release bodies to read (default 10)")
    ap.add_argument("--no-esef", action="store_true",
                    help="skip the ESEF annual cross-check (faster, weaker)")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.company:
        ap.error("give a company name, or --selftest")

    global NO_CACHE
    NO_CACHE = args.no_cache
    as_of = (args.as_of or datetime.date.today().isoformat())[:10]
    warnings = []
    observations = []
    reports = []
    esef_ends = []
    label = args.company

    who = locate(args.company, args.country, args.mfn_slug, args.cision_slug,
                args.lei, use_esef=not args.no_esef)
    warnings += who.get("notes") or []
    if who.get("mfn_slug"):
        reports += harvest_mfn(who["mfn_slug"])
    if not reports and who.get("cision_slug"):
        reports += harvest_cision(who["cision_slug"], max_bodies=args.reports)
    if not reports:
        print("DATA NOT AVAILABLE: no interim or year-end report releases were "
              "found for %r." % args.company)
        print("  MFN slug tried:    %s" % (who.get("mfn_slug") or "none resolved"))
        print("  Cision slug tried: %s" % (who.get("cision_slug") or "none resolved"))
        print("Pass --mfn-slug / --cision-slug explicitly if the name did not "
              "resolve. Without interim reports there is no TTM, only the "
              "annual figure - and its age is the whole problem.")
        return 1
    reports = [r for r in reports if (r["published"] or "9999") <= as_of][:args.reports]
    label = who.get("mfn_name") or who.get("cision_name") or who.get("esef_name") or label

    if who.get("lei"):
        esef_obs, esef_ends = esef_observations(who["lei"])
        observations += esef_obs

    fye_md, fye_source, fye_warn = detect_fye(reports, esef_ends)
    warnings += fye_warn

    # company_resolve answers the same question from wider evidence: it
    # parses the year-end report's own period range for issuers with no
    # ESEF filing at all. Two modules disagreeing about a fiscal year is
    # worse than either answer, so it is asked before refusing - but only
    # then, because it is slow. It is called out of process because it
    # exposes its result through its CLI, not through a stable function.
    if fye_md is None:
        try:
            proc = subprocess.run(
                [sys.executable, os.path.join(HERE, "company_resolve.py"),
                 args.company, "--json"],
                capture_output=True, text=True, timeout=300)
            candidate = None
            if proc.stdout.strip():
                record = json.loads(proc.stdout)
                candidate = record.get("fiscal_year_end")
            if candidate and re.match(r"^\d{2}-\d{2}$", str(candidate)):
                fye_md = candidate
                fye_source = "company_resolve: %s" % (
                    record.get("fiscal_year_end_source") or "issuer report")
                warnings.append(
                    "fiscal year end came from company_resolve, not from the "
                    "reports read here. Confirm it against the report itself.")
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            warnings.append("company_resolve fallback unavailable (%s)" % exc)

    if fye_md is None:
        print("REFUSING: %s" % (fye_warn[-1] if fye_warn else "fiscal year end unknown"))
        print("Every term of a TTM is defined relative to the fiscal year. "
              "Guessing 31 December is how a March-year-end issuer's TTM ends "
              "up three months out with a citation that looks correct.")
        return 2

    for r in reports:
        obs, warn = extract_observations(r["text"], r["title"], d(r["published"]),
                                         fye_md, r["source"], r["url"])
        observations += obs
        warnings += warn
        if DISCONTINUED_RE.search(r["text"] or ""):
            warnings.append("%s discusses discontinued operations; check that "
                            "every term of the TTM is on the same basis"
                            % r["title"][:70])

    if fye_md is None:
        print("REFUSING: fiscal year end unknown; nothing computed.")
        return 2

    ledger = build_ledger(observations)
    for extra in synthesise_discretes(ledger, fye_md):
        ledger.setdefault(extra.key(), []).append(extra)

    metrics = [args.metric] if args.metric else [
        m for m in METRIC_ORDER if any(k[0] == m for k in ledger)]
    if not metrics:
        print("DATA NOT AVAILABLE: no recognised metric was extracted from the "
              "%d reports read." % len(reports))
        return 1

    text_ends = [d(k[2]) for k, rows in ledger.items()
                 if rows[0].source in ("mfn", "cision")
                 and (rows[0].published or "") <= as_of]
    anchor_end = max(text_ends) if text_ends else None

    results, facts = [], {}
    for metric in metrics:
        res = assemble(ledger, metric, fye_md, as_of, warnings, anchor_end)
        fact = build_fact(res, fye_md)
        results.append(res)
        facts[metric] = fact

    if args.as_json:
        print(json.dumps({
            "company": label, "query": args.company, "as_of": as_of,
            "fiscal_year_end": fye_md, "fiscal_year_end_source": fye_source,
            "retrieved_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "reports_read": [{"title": r["title"], "published": r["published"],
                              "source": r["source"], "url": r["url"]} for r in reports],
            "esef_period_ends": esef_ends,
            "results": results,
            "facts": {m: (f.to_dict() if f else None) for m, f in facts.items()},
        }, indent=2, ensure_ascii=False, default=str))
        return 0

    print("%s  |  TTM engine  |  as of %s" % (label, as_of))
    print("fiscal year end: %s   (%s)" % (fye_md or "UNKNOWN", fye_source or "-"))
    if esef_ends:
        latest = d(esef_ends[0])
        stale = (d(as_of) - latest).days
        print("latest ESEF annual data: %s  -  %d days (%.0f months) stale. "
              "That staleness is why this module exists."
              % (esef_ends[0], stale, stale / 30.44))
    print("reports read: %d" % len(reports))
    for r in reports[:6]:
        print("   %s  %-62.62s  %s" % (r["published"], r["title"], r["source"]))
    if len(reports) > 6:
        print("   ... and %d older" % (len(reports) - 6))
    print()
    print("  %-22s %18s %-4s  %-23s %s" % ("metric", "TTM", "cur", "period", "method"))
    print("  " + "-" * 104)
    for res in results:
        print_result(res, facts.get(res["metric"]), args.explain, fye_md)
    print()
    print("Interim figures here are parsed out of press-release prose, not out of a")
    print("tagged filing. Each one prints the line it came from - read the line. If")
    print("a period or a scale looks wrong, it is wrong, and the annual figure with")
    print("its age stated is the honest fallback.")
    return 0


# ==========================================================================
# Selftest: the arithmetic, with no network.
# ==========================================================================

def selftest():
    ok = 0

    # -- numbers
    cases = [("1,030.8", 1030.8), ("37,799", 37799.0), ("28 838", 28838.0),
             ("16,6", 16.6), ("3.35", 3.35), ("-0,05", -0.05),
             ("- 11 471", -11471.0), ("2 066,5", 2066.5), ("104 435", 104435.0),
             ("1.030,8", 1030.8)]
    for raw, want in cases:
        got, _ = parse_number(raw)
        assert got == want, (raw, got, want)
        ok += 1
    # the footnote trap
    got, truncated = parse_number("2 983 1")
    assert got == 2983.0 and truncated, (got, truncated)
    ok += 1

    # typographic minus signs (U+2212, en-dash) must flip the sign, not be
    # silently read as an ASCII hyphen would fail to
    got, _ = parse_number("−139")
    assert got == -139.0, got
    got, _ = parse_number("– 11 471")
    assert got == -11471.0, got
    ok += 2
    # ... and the same character inside a release block, end to end - the
    # exact figure the bug report was verified against
    kebni_minus = ("Financial development Apr-Jun 2026 (KSEK)\n\n"
                   "* Net sales, 28 838 (33 677), -14% year-on-year\n"
                   "* EBITDA, −139 (4 882), 0% (14%)\n")
    obs, warn = extract_observations(kebni_minus, "Kebni Q2 report 2026",
                                     datetime.date(2026, 8, 14), "12-31", "mfn", "u")
    got = {(o.metric, o.start.isoformat(), o.end.isoformat()): o.value for o in obs}
    assert got[("ebitda", "2026-04-01", "2026-06-30")] == -139000.0, (got, warn)
    ok += 1

    # -- fiscal years that do not end in December
    assert fy_bounds(datetime.date(2026, 6, 30), "03-31") == \
        (datetime.date(2026, 4, 1), datetime.date(2027, 3, 31))
    assert fy_bounds(datetime.date(2026, 3, 31), "03-31") == \
        (datetime.date(2025, 4, 1), datetime.date(2026, 3, 31))
    assert fy_bounds(datetime.date(2026, 5, 31), "11-30") == \
        (datetime.date(2025, 12, 1), datetime.date(2026, 11, 30))
    assert fy_bounds(datetime.date(2026, 6, 30), "12-31") == \
        (datetime.date(2026, 1, 1), datetime.date(2026, 12, 31))
    ok += 3

    # -- period grammar on the real strings these issuers publish
    p = parse_period("Helar (1 april 2025 - 31 mars 2026)", "03-31", 2026)
    assert (p.start, p.end, p.months) == (datetime.date(2025, 4, 1),
                                          datetime.date(2026, 3, 31), 12), p
    p = parse_period("Första halvåret (1 december 2025 — 31 maj 2026)", "11-30", 2026)
    assert (p.start, p.end, p.months) == (datetime.date(2025, 12, 1),
                                          datetime.date(2026, 5, 31), 6), p
    p = parse_period("Financial development Oct-Dec 2025 (KSEK)", "12-31", 2026)
    assert (p.start, p.end, p.months) == (datetime.date(2025, 10, 1),
                                          datetime.date(2025, 12, 31), 3), p
    p = parse_period("January-June 2026 (1H 2025)", "12-31", 2026)
    assert (p.start, p.end, p.months) == (datetime.date(2026, 1, 1),
                                          datetime.date(2026, 6, 30), 6), p
    p = parse_period("Interim report second quarter 2026", "12-31", 2026)
    assert (p.start, p.end) == (datetime.date(2026, 4, 1), datetime.date(2026, 6, 30)), p
    p = parse_period("Interim report fourth quarter 2025", "12-31", 2026)
    assert (p.start, p.end) == (datetime.date(2025, 10, 1), datetime.date(2025, 12, 31)), p
    # a non-calendar filer's Q2 must not be April-June
    p = parse_period("Second quarter 2026", "03-31", 2026)
    assert (p.start, p.end) == (datetime.date(2025, 7, 1), datetime.date(2025, 9, 30)), p
    ok += 7

    # split-year fiscal labels ("2025/26") resolve to the year they END in,
    # not the year the quarter/cumulative-words regex captured on its own
    p = parse_period("Interim report Q1 2025/26", "03-31", 2025)
    assert (p.start, p.end) == (datetime.date(2025, 4, 1), datetime.date(2025, 6, 30)), p
    p = parse_period("Bokslutskommunike 2025/26 helar", "03-31", 2026)
    assert (p.start, p.end) == (datetime.date(2025, 4, 1), datetime.date(2026, 3, 31)), p
    ok += 2

    # -- classification keeps adjusted separate and drops order intake
    assert classify("Profit for the period SEK ", "") == "net_income"
    assert classify("Adjusted profit for the period SEK ", "") == "adjusted_net_income"
    assert classify("Order intake SEK ", "") is None
    assert classify("Revenues SEK ", "") == "revenue"
    assert classify("Revenue growth, at fixed exchange rates, increased by ", "") is None
    assert classify("and total operating revenues decreased 4.3% to ", "") is None
    assert classify("Rörelseresultatet före avskrivningar (EBITA) uppgick till ", "") == "ebita"
    assert classify(", motsvarande SEK ", " per aktie.") == "eps"
    assert classify("The Board of Directors proposes a dividend of SEK ", " per share (5.75)") is None
    ok += 9

    # -- extraction end to end, on the exact prose these issuers publish
    kebni = ("Financial development Apr-Jun 2026 (KSEK)\n\n"
             "* Net sales, 28 838 (33 677), -14% year-on-year\n"
             "* EBITDA, -139 (4 882), 0% (14%)\n\n"
             "Financial development Jan-June 2026 (KSEK)\n\n"
             "* Net sales, 41 881 (68 638), -39% year-on-year\n")
    obs, _ = extract_observations(kebni, "Kebni Q2 report 2026",
                                  datetime.date(2026, 8, 14), "12-31", "mfn", "u")
    got = {(o.metric, o.start.isoformat(), o.end.isoformat()): o.value for o in obs}
    assert got[("revenue", "2026-04-01", "2026-06-30")] == 28838000.0, got
    assert got[("revenue", "2026-01-01", "2026-06-30")] == 41881000.0, got
    assert got[("revenue", "2025-01-01", "2025-06-30")] == 68638000.0, got
    ok += 3

    hm = ("Andra kvartalet (1 mars 2026 — 31 maj 2026)\n"
          "Nettoomsättningen uppgick till MSEK 54 828 (56 714) i andra kvartalet.\n"
          "Första halvåret (1 december 2025 — 31 maj 2026)\n"
          "H&M-gruppens nettoomsättning uppgick till MSEK 104 435 (112 047).\n")
    obs, _ = extract_observations(hm, "Sexmånadersrapport 2026",
                                  datetime.date(2026, 6, 25), "11-30", "cision", "u")
    got = {(o.metric, o.start.isoformat(), o.end.isoformat()): o.value for o in obs}
    assert got[("revenue", "2026-03-01", "2026-05-31")] == 54828e6, got
    assert got[("revenue", "2025-12-01", "2026-05-31")] == 104435e6, got
    assert got[("revenue", "2024-12-01", "2025-05-31")] == 112047e6, got
    ok += 3

    evo = ("January-June 2026 (1H 2025)\n\n"
           "* Net revenues declined 1.4% to EUR 1,030.8 million (1,045.2)\n")
    obs, _ = extract_observations(evo, "Interim report January-June 2026",
                                  datetime.date(2026, 7, 17), "12-31", "mfn", "u")
    got = {(o.metric, o.start.isoformat(), o.end.isoformat()): (o.value, o.currency)
           for o in obs}
    assert got[("revenue", "2026-01-01", "2026-06-30")] == (1030.8e6, "EUR"), got
    ok += 1

    # -- the bridge itself, on Addtech's published figures
    fye = "03-31"
    ledger = build_ledger([
        Obs("revenue", datetime.date(2026, 4, 1), datetime.date(2026, 6, 30),
            6172e6, "SEK", "cision", "Q1 2026/27", "u", "2026-07-14", "line"),
        Obs("revenue", datetime.date(2025, 4, 1), datetime.date(2025, 6, 30),
            5839e6, "SEK", "cision", "Q1 2026/27", "u", "2026-07-14", "line", True),
        Obs("revenue", datetime.date(2025, 4, 1), datetime.date(2026, 3, 31),
            22703e6, "SEK", "cision", "Bokslutskommunike", "u", "2026-05-20", "line"),
    ])
    res = assemble(ledger, "revenue", fye, "2026-08-31", [])
    assert res["state"] == State.OK.value, res
    assert abs(res["value"] - 23036e6) < 1, res["value"]
    assert res["period_start"] == "2025-07-01" and res["period_end"] == "2026-06-30", res
    assert res["method"] == "DERIVED" and res["completeness"] == "COMPLETE"
    ok += 4

    # -- a missing quarter must refuse, not interpolate
    ledger2 = build_ledger([
        Obs("revenue", datetime.date(2026, 4, 1), datetime.date(2026, 6, 30),
            100.0, "SEK", "cision", "Q2", "u", "2026-07-14", "line"),
        Obs("revenue", datetime.date(2026, 1, 1), datetime.date(2026, 3, 31),
            90.0, "SEK", "cision", "Q1", "u", "2026-04-14", "line"),
    ])
    res2 = assemble(ledger2, "revenue", "12-31", "2026-08-31", [])
    assert res2["state"] == State.TTM_INCOMPLETE.value, res2
    assert "2025-07-01" in res2["reason"], res2["reason"]
    ok += 2

    # -- a fiscal year change is refused
    fye_md, source, warn = detect_fye(
        [{"title": "Bokslutskommunike 1 april 2025 - 31 mars 2026", "text": ""}],
        ["2025-12-31", "2024-12-31"])
    assert fye_md is None and source == "conflict", (fye_md, source)
    ok += 1

    # -- a restated prior year prefers the newer figure and says so
    ledger3 = build_ledger([
        Obs("revenue", datetime.date(2026, 1, 1), datetime.date(2026, 6, 30),
            110.0, "EUR", "mfn", "H1 2026", "u", "2026-07-17", "line"),
        Obs("revenue", datetime.date(2025, 1, 1), datetime.date(2025, 6, 30),
            100.0, "EUR", "mfn", "H1 2026", "u", "2026-07-17", "line", True),
        Obs("revenue", datetime.date(2025, 1, 1), datetime.date(2025, 12, 31),
            205.0, "EUR", "mfn", "Year-end 2025 restated", "u", "2026-02-05", "line"),
        Obs("revenue", datetime.date(2025, 1, 1), datetime.date(2025, 12, 31),
            200.0, "EUR", "esef", "ESEF 2025", "u", "2025-03-01", "line"),
    ])
    res3 = assemble(ledger3, "revenue", "12-31", "2026-08-31", [])
    assert abs(res3["value"] - 215.0) < 1e-9, res3["value"]
    assert any("restated" in f for f in res3["flags"]), res3["flags"]
    ok += 2

    # -- a stated full year that is not twelve months never sets the year end
    fye_md, _s, _w = detect_fye(
        [{"title": "Rapport 1 januari 2025 - 30 juni 2025", "text": ""}], [])
    assert fye_md is None, fye_md
    ok += 1

    # -- a First North issuer with no ESEF: the year end comes out of the text
    fye_md, source, _w = detect_fye(
        [{"title": "Kebni Q4 report 2025",
          "text": "Financial development Jan-Dec 2025 (KSEK)\n* Net sales, 124 632"}], [])
    assert fye_md == "12-31", fye_md
    assert "own report" in source, source
    ok += 2

    # -- two sections welded into one block must still be dated apart
    merged = ("Financial development Jul-Sep 2025 (KSEK) Net sales, 30 422 (28 295) "
              "EBITDA, 3 479 (-1 154) Financial development Jan-Sep 2025 (KSEK) "
              "Net sales, 99 061 (90 176) EBITDA, 11 781 (5 109)")
    obs, _ = extract_observations(merged, "Kebni Q3 report 2025",
                                  datetime.date(2025, 10, 23), "12-31", "mfn", "u")
    got = {(o.metric, o.start.isoformat(), o.end.isoformat()): o.value for o in obs}
    assert got[("ebitda", "2025-07-01", "2025-09-30")] == 3479000.0, got
    assert got[("ebitda", "2025-01-01", "2025-09-30")] == 11781000.0, got
    ok += 2

    # -- a sentence carrying its own period overrides the release title
    ceo = ("EBITDA for the full year amounted to 10,3 MSEK (8%), in line with 2024, "
           "despite that the Q4 results were below our expectations.")
    obs, _ = extract_observations(ceo, "Kebni Q4 report 2025",
                                  datetime.date(2026, 2, 12), "12-31", "mfn", "u")
    got = {(o.metric, o.start.isoformat(), o.end.isoformat()): o.value for o in obs}
    assert got.get(("ebitda", "2025-01-01", "2025-12-31")) == 10300000.0, got
    ok += 1

    # -- the precisely stated figure beats the rounded one for the same period
    led = build_ledger([
        Obs("ebitda", datetime.date(2025, 1, 1), datetime.date(2025, 12, 31),
            10300000.0, "SEK", "mfn", "Q4", "u", "2026-02-12", "CEO letter",
            precision=3),
        Obs("ebitda", datetime.date(2025, 1, 1), datetime.date(2025, 12, 31),
            10275000.0, "SEK", "mfn", "Q4", "u", "2026-02-12", "bullet",
            precision=5)])
    best, _sup = pick_obs(led, "ebitda", datetime.date(2025, 1, 1),
                          datetime.date(2025, 12, 31))
    assert best.value == 10275000.0, best.value
    ok += 1

    # -- on an equal date AND equal precision, the tagged ESEF filing beats
    # prose - ESEF facts are recorded at precision=0, so this is the only
    # tie-break left, and it must not go to whichever text figure happens to
    # have one more digit of rounding
    led_tie = build_ledger([
        Obs("revenue", datetime.date(2025, 1, 1), datetime.date(2025, 12, 31),
            205.0, "EUR", "mfn", "Year-end 2025", "u", "2026-02-05", "line",
            precision=0),
        Obs("revenue", datetime.date(2025, 1, 1), datetime.date(2025, 12, 31),
            200.0, "EUR", "esef", "ESEF 2025", "u", "2026-02-05", "line",
            precision=0)])
    best, _sup = pick_obs(led_tie, "revenue", datetime.date(2025, 1, 1),
                          datetime.date(2025, 12, 31))
    assert best.source == "esef" and best.value == 200.0, (best.source, best.value)
    ok += 1

    # -- a metric no interim discloses is stale, never a TTM
    led2 = build_ledger([
        Obs("revenue", datetime.date(2026, 1, 1), datetime.date(2026, 6, 30),
            50.0, "SEK", "mfn", "H1", "u", "2026-07-17", "line"),
        Obs("gross_profit", datetime.date(2024, 1, 1), datetime.date(2024, 12, 31),
            30.0, "SEK", "esef", "ESEF 2024", "u", "2025-03-01", "xbrl")])
    res = assemble(led2, "gross_profit", "12-31", "2026-08-31", [],
                   anchor_end=datetime.date(2026, 6, 30))
    assert res["state"] == State.DATA_STALE.value, res
    assert res["value"] is None and res["fallback"]["value"] == 30.0
    ok += 2

    # -- rounding in per-share figures is not a conflict; 1% in money is
    assert agrees("eps", -0.10, -0.11)[0] is True
    assert agrees("revenue", 100.0, 103.0)[0] is False
    assert agrees("revenue", 100.0, 100.5)[0] is True
    ok += 3

    print("ttm_engine selftest: %d assertions passed" % ok)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
