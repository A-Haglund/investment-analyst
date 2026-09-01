#!/usr/bin/env python3
"""Swedish macro rates and official industry benchmarks for a DCF and a peer set.

Two things kill a Swedish DCF faster than a bad growth assumption: a risk-free
rate someone half-remembered, and a margin assumption with no reference point.
Both have free, official, keyless answers, and this script fetches them.

  * Sveriges Riksbank (SWEA) publishes the whole Swedish government yield curve
    daily. The 10-year is the risk-free rate; there is no reason to guess it.
  * SCB's Foretagens ekonomi (Structural Business Statistics) publishes the
    aggregate income statement of every Swedish enterprise, split by NACE
    section and employee size class. Operating profit over net turnover is then
    an OFFICIAL sector operating margin -- a benchmark that a broker's "peer
    average" of five hand-picked names cannot claim to be.

What this script will not do is blur the line between a measurement and a
judgement. The 10-year yield is a FACT: observed, dated, attributable. The
equity risk premium is an ASSUMPTION: no statistical agency publishes it and
none ever will. The output labels them separately, always, because a DCF that
hides which is which is a DCF that cannot be argued with.

Usage:
    python macro_se.py --dcf-inputs
    python macro_se.py --dcf-inputs --beta 1.15 --erp 5.5
    python macro_se.py --curve
    python macro_se.py --industry "tillverkning"
    python macro_se.py --industry 68.20
    python macro_se.py --industry C --size 250+ --year 2024
    python macro_se.py --cpi
    python macro_se.py --ppi
    python macro_se.py --indicator retail
    python macro_se.py --euro
    python macro_se.py --dcf-inputs --json

Sources, all free and keyless:
    Riksbanken SWEA   https://api.riksbank.se/swea/v1/
    SCB PxWeb API v2  https://api.scb.se/OV0104/v2beta/api/v2   (CC0)
    SNI code search   https://sni2007.scb.se/sok
    ECB Data Portal   https://data-api.ecb.europa.eu/service/data/
    Eurostat          https://ec.europa.eu/eurostat/api/dissemination/
"""
import argparse
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; investment-analyst-skill/1.0)"

SWEA = "https://api.riksbank.se/swea/v1"
SCB = "https://api.scb.se/OV0104/v2beta/api/v2"
SNI_SOK = "https://sni2007.scb.se/sok"
ECB = "https://data-api.ecb.europa.eu/service/data"
EUROSTAT = ("https://ec.europa.eu/eurostat/api/dissemination"
            "/statistics/1.0/data")

CACHE = os.path.join(tempfile.gettempdir(), "macro-se-cache")

# Both sources are metered, but differently, and the difference drives the
# caching strategy.
#
# SCB publishes its limit (30 calls / 10 s in its own /config), so a small
# floor delay is enough to stay inside it.
#
# Riksbanken publishes no number, and measurement shows it is NOT a
# per-second limit: spacing calls 3 s apart fails just as hard as 1.2 s once
# a rolling quota is spent. Backoff cannot beat a depleted quota, so the disk
# cache is load-bearing rather than a nicety -- a warm run makes zero calls,
# and a spent quota degrades to the last good value (or an honest
# DATA NOT AVAILABLE) instead of hanging on retries.
MIN_INTERVAL = {"api.riksbank.se": 1.2, "api.scb.se": 0.4}
_last_call = {}

# Rates are set once a day; SCB's business statistics once a year.
TTL_FAST = 12 * 3600
TTL_SLOW = 7 * 24 * 3600
MAX_ATTEMPTS = 4

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


class Unavailable(Exception):
    """A source did not answer. Never silently substituted with a guess."""


def _throttle(url):
    host = urllib.parse.urlparse(url).netloc
    gap = MIN_INTERVAL.get(host, 0.0)
    if not gap:
        return
    wait = gap - (time.time() - _last_call.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    _last_call[host] = time.time()


def _cache_path(key):
    """Readable prefix plus a full-key hash.

    Truncating the key alone is not safe: two SCB POST bodies can share their
    first 150 characters and differ only in the SNI code near the end, which
    would silently serve one industry's numbers for another.
    """
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:80]
    return os.path.join(CACHE, "%s-%s" % (safe, digest))


def fetch(url, body=None, ttl=TTL_FAST, headers=None, accept="application/json"):
    """GET or POST with a disk cache, throttling and 429 backoff.

    Returns raw bytes. Caching is not politeness alone: SWEA starts returning
    429 after a handful of calls, and a yield curve is eight calls.
    """
    os.makedirs(CACHE, exist_ok=True)
    key = url + "|" + accept + ("|" + body.decode("utf-8", "replace") if body else "")
    path = _cache_path(key)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        with open(path, "rb") as f:
            return f.read()

    hdrs = {"User-Agent": UA, "Accept": accept}
    if body:
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})

    delay = 1.0
    last = None
    for _ in range(MAX_ATTEMPTS):
        _throttle(url)
        try:
            req = urllib.request.Request(url, data=body, headers=hdrs)
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            with open(path, "wb") as f:
                f.write(data)
            return data
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 502, 503, 504):
                time.sleep(delay)
                delay *= 2
                continue
            break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(delay)
            delay *= 2
    # A stale cache entry beats no answer. The observation date travels with
    # every value in the output, so a stale read is visible as an old date
    # rather than passing itself off as current.
    if os.path.exists(path):
        sys.stderr.write("NOTE: stale cache used for %s (%s)\n" % (url, last))
        with open(path, "rb") as f:
            return f.read()
    raise Unavailable("%s (%s)" % (url, last))


def fetch_json(url, body=None, ttl=TTL_FAST):
    raw = fetch(url, body=body, ttl=ttl)
    for enc in ("utf-8", "cp1252", "iso-8859-1"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, ValueError):
            continue
    raise Unavailable("unparseable response from %s" % url)


# ---------------------------------------------------------------------------
# Sveriges Riksbank -- SWEA
# ---------------------------------------------------------------------------

# Verified live 2026-08-31 against /swea/v1/Series (117 series, 'seriesClosed'
# flags the dead ones). These are the ones still publishing. Riksbanken retired
# the STIBOR and STFIX series in 2019-2020, so the short end of the curve comes
# from Treasury bills, not from an interbank fixing.
SE_CURVE = [
    ("SETB1MBENCHC", "1M", 1.0 / 12, "Treasury bill"),
    ("SETB3MBENCH", "3M", 0.25, "Treasury bill"),
    ("SETB6MBENCH", "6M", 0.50, "Treasury bill"),
    ("SEGVB2YC", "2Y", 2.0, "Government bond"),
    ("SEGVB5YC", "5Y", 5.0, "Government bond"),
    ("SEGVB7YC", "7Y", 7.0, "Government bond"),
    ("SEGVB10YC", "10Y", 10.0, "Government bond"),
]
# Swedish covered ("mortgage") bonds: the only daily AAA-ish SEK credit curve
# Riksbanken publishes. Against the govvie of equal tenor it gives an observed
# SEK credit spread.
SE_CREDIT = [("SEMB2YCACOMB", "2Y", "SEGVB2YC"),
             ("SEMB5YCACOMB", "5Y", "SEGVB5YC")]

POLICY_RATE = "SECBREPOEFF"
FX_SERIES = {"EUR": "SEKEURPMI", "USD": "SEKUSDPMI", "NOK": "SEKNOKPMI",
             "DKK": "SEKDKKPMI", "GBP": "SEKGBPPMI", "CHF": "SEKCHFPMI",
             "JPY": "SEKJPYPMI", "CNY": "SEKCNYPMI"}
# Riksbanken also republishes foreign 10y benchmarks -- handy for a sanity
# check on where Sweden sits in the European rate complex.
PEER_10Y = {"DE": "DEGVB10Y", "EU": "EMGVB10Y", "US": "USGVB10Y",
            "GB": "GBGVB10Y", "NO": "NOGVB10Y", "DK": "DKGVB10Y",
            "FI": "FIGVB10Y"}


def swea_latest(series, days=30):
    """Latest observation of a SWEA series as (value, date), or None.

    SWEA has no 'latest' endpoint and no multi-series endpoint, so this asks
    for a trailing window and takes the last point. The window must be wide
    enough to clear Swedish public holidays.
    """
    today = time.strftime("%Y-%m-%d")
    start = time.strftime("%Y-%m-%d",
                          time.localtime(time.time() - days * 86400))
    try:
        obs = fetch_json("%s/Observations/%s/%s/%s" % (SWEA, series, start, today))
    except Unavailable:
        return None
    if not obs:
        return None
    last = obs[-1]
    return (last.get("value"), last.get("date"))


def swea_catalog(live_only=True):
    """The SWEA series catalogue -- what Riksbanken actually publishes today."""
    try:
        rows = fetch_json(SWEA + "/Series", ttl=TTL_SLOW)
    except Unavailable:
        return []
    if live_only:
        rows = [r for r in rows if not r.get("seriesClosed")]
    return rows


def se_curve():
    """The Swedish sovereign curve from the maturities SWEA really publishes."""
    out = []
    for sid, label, years, kind in SE_CURVE:
        got = swea_latest(sid)
        out.append({"series": sid, "tenor": label, "years": years,
                    "instrument": kind,
                    "rate_pct": got[0] if got else None,
                    "obs_date": got[1] if got else None,
                    "status": "OK" if got else "DATA NOT AVAILABLE"})
    return out


def se_credit_spreads():
    """Observed SEK covered-bond spread over the government curve, in bp."""
    out = []
    for mb, tenor, gv in SE_CREDIT:
        a, b = swea_latest(mb), swea_latest(gv)
        if not a or not b or a[0] is None or b[0] is None:
            out.append({"tenor": tenor, "status": "DATA NOT AVAILABLE"})
            continue
        out.append({"tenor": tenor, "mortgage_bond_pct": a[0],
                    "government_pct": b[0],
                    "spread_bp": round((a[0] - b[0]) * 100, 1),
                    "obs_date": a[1], "series": "%s - %s" % (mb, gv),
                    "status": "OK"})
    return out


def peer_10y():
    """Foreign 10y benchmarks, as Riksbanken itself republishes them.

    Opt-in, because each one is a separate call against a quota that a full
    yield curve has already largely spent. Same source and same observation
    convention as the Swedish series, which is the point: comparing SEGVB10YC
    against a yield scraped from somewhere else compares two conventions.
    """
    out = []
    for country, sid in PEER_10Y.items():
        got = swea_latest(sid)
        out.append({"country": country, "series": sid,
                    "rate_pct": got[0] if got else None,
                    "obs_date": got[1] if got else None,
                    "status": "OK" if got else "DATA NOT AVAILABLE"})
    return out


def sek_fx(extra_fx=False):
    """SEK crosses from Riksbanken, falling back to the ECB.

    Riksbanken's quota is tight enough that a full DCF block can spend it
    before reaching FX. The ECB publishes the same pair as a euro reference
    rate with no comparable limit, and the two agreed to the fourth decimal
    when checked (11.0885 on 2026-08-28), so it is a sound fallback -- but the
    output names which one answered, because they are different fixings taken
    by different institutions and only one of them is the Swedish official.
    """
    wanted = FX_SERIES if extra_fx else {"EUR": FX_SERIES["EUR"],
                                         "USD": FX_SERIES["USD"]}
    fx = {}
    for ccy, sid in wanted.items():
        got = swea_latest(sid)
        if got:
            fx[ccy] = {"sek_per_unit": got[0], "obs_date": got[1],
                       "series": sid, "source": "Riksbanken SWEA",
                       "status": "OK"}
        else:
            fx[ccy] = {"sek_per_unit": None, "obs_date": None,
                       "series": sid, "source": "Riksbanken SWEA",
                       "status": "DATA NOT AVAILABLE"}

    missing = [c for c, v in fx.items() if v["sek_per_unit"] is None]
    if not missing:
        return fx

    eur = ecb_series("EXR", "D.SEK.EUR.SP00.A")          # SEK per EUR
    if not eur:
        return fx
    eur_date, eur_rate = eur[-1][0], eur[-1][1]
    if "EUR" in missing:
        fx["EUR"] = {"sek_per_unit": eur_rate, "obs_date": eur_date,
                     "series": "EXR D.SEK.EUR.SP00.A",
                     "source": "ECB reference rate (Riksbanken unavailable)",
                     "status": "OK"}
        missing.remove("EUR")
    for ccy in list(missing):
        if ccy == "EUR":
            continue
        cross = ecb_series("EXR", "D.%s.EUR.SP00.A" % ccy)
        if not cross or not cross[-1][1]:
            continue
        # ECB quotes everything against the euro, so SEK per X is the ratio.
        fx[ccy] = {"sek_per_unit": round(eur_rate / cross[-1][1], 4),
                   "obs_date": min(eur_date, cross[-1][0]),
                   "series": "EXR D.SEK.EUR / D.%s.EUR" % ccy,
                   "source": "ECB reference rates, cross-computed "
                             "(Riksbanken unavailable)",
                   "status": "OK"}
    return fx


def dcf_inputs(erp=None, beta=None, tax_rate=20.6, extra_fx=False,
               credit=False):
    """Assemble the observed side of a Swedish DCF, and flag the rest."""
    rf = swea_latest("SEGVB10YC")
    pol = swea_latest(POLICY_RATE)
    two = swea_latest("SEGVB2YC")

    fx = sek_fx(extra_fx=extra_fx)

    facts = {
        "risk_free_rate_pct": {
            "value": rf[0] if rf else None, "obs_date": rf[1] if rf else None,
            "series": "SEGVB10YC",
            "definition": "Swedish 10-year government benchmark bond yield",
            "source": "Sveriges Riksbank SWEA",
            "status": "OK" if rf else "DATA NOT AVAILABLE"},
        "policy_rate_pct": {
            "value": pol[0] if pol else None,
            "obs_date": pol[1] if pol else None, "series": POLICY_RATE,
            "definition": "Riksbank policy rate (styrrantan), 7-day",
            "source": "Sveriges Riksbank SWEA",
            "status": "OK" if pol else "DATA NOT AVAILABLE"},
        "fx_sek": fx,
        "statutory_corporate_tax_pct": {
            "value": tax_rate, "source": "Swedish corporate income tax rate",
            "note": "Statutory rate. A company's effective rate is its own "
                    "number and belongs in the model, not here."},
    }
    if rf and two and rf[0] is not None and two[0] is not None:
        facts["term_spread_10y_2y_bp"] = {
            "value": round((rf[0] - two[0]) * 100, 1),
            "obs_date": rf[1],
            "note": "Positive means the curve is upward sloping."}

    assumptions = {
        "equity_risk_premium_pct": {
            "value": erp,
            "kind": "ASSUMPTION",
            "source": "NOT OBSERVED. No statistical agency publishes an ERP.",
            "note": "Supply your own with --erp. The Swedish market convention "
                    "anchors on the annual PwC Sweden risk premium study, which "
                    "is a survey of practitioners, not a measurement. Whatever "
                    "number you use is a judgement you must defend."},
        "terminal_growth_pct": {
            "value": None, "kind": "ASSUMPTION",
            "note": "Bound it by long-run nominal GDP. Above the risk-free "
                    "rate it is arithmetically indefensible."},
        "beta": {"value": beta, "kind": "ASSUMPTION",
                 "note": "An estimate from a chosen window and index, not a "
                         "property of the company."},
    }

    cost_of_equity = None
    if rf and rf[0] is not None and erp is not None and beta is not None:
        cost_of_equity = {
            "value_pct": round(rf[0] + beta * erp, 3),
            "kind": "PART FACT, PART ASSUMPTION",
            "formula": "Ke = rf (%.3f, observed %s) + beta (%.2f, assumed) "
                       "* ERP (%.2f, assumed)" % (rf[0], rf[1], beta, erp)}

    return {"as_of": time.strftime("%Y-%m-%d"),
            "facts": facts,
            "credit_spreads": se_credit_spreads() if credit else [],
            "assumptions": assumptions,
            "cost_of_equity": cost_of_equity}


# ---------------------------------------------------------------------------
# SCB -- PxWeb API v2 (CC0, keyless, 30 calls / 10 s)
# ---------------------------------------------------------------------------

# GOTCHA, and it is a silent one: the output format must go in the
# `outputFormat` QUERY PARAM. Putting {"response": {"format": "json-stat2"}}
# in the POST body is accepted and ignored, and you get PX text in
# iso-8859-1 that will not json-parse.
def scb_metadata(table, lang="en"):
    return fetch_json("%s/tables/%s/metadata?lang=%s&outputFormat=json-stat2"
                      % (SCB, table, lang), ttl=TTL_SLOW)


def scb_data(table, selection, lang="en", ttl=TTL_SLOW):
    """POST a selection to SCB and return JSON-stat2.

    Every dimension whose metadata has extension.elimination == False is
    MANDATORY; leave one out and SCB answers
    '400 Missing selection for mandantory variable' (its typo, not ours).
    Use ['*'] for all values of a dimension, ['top(3)'] for the latest 3.
    """
    body = json.dumps({"selection": [
        {"variableCode": k, "valueCodes": v} for k, v in selection]})
    return fetch_json("%s/tables/%s/data?lang=%s&outputFormat=json-stat2"
                      % (SCB, table, lang), body=body.encode("utf-8"), ttl=ttl)


def js_axes(js):
    """Ordered category codes for each dimension of a JSON-stat2 dataset."""
    axes = []
    for dim_id in js["id"]:
        cat = js["dimension"][dim_id]["category"]
        idx = cat["index"]
        if isinstance(idx, dict):
            codes = [None] * len(idx)
            for code, pos in idx.items():
                codes[pos] = code
            axes.append(codes)
        else:                                   # the spec also allows a list
            axes.append(list(idx))
    return axes


def js_get(js, axes, coords):
    """Read one cell by dimension-code coordinates.

    JSON-stat2's `value` is a flat, ROW-MAJOR and SPARSE dict keyed by string
    offsets. Sparse matters: a missing combination is an absent key, not a
    null, so never assume len(value) == prod(size).
    """
    size = js["size"]
    offset = 0
    for i, code in enumerate(coords):
        try:
            pos = axes[i].index(code)
        except ValueError:
            return None
        offset = offset * size[i] + pos
    value = js["value"]
    if isinstance(value, dict):
        return value.get(str(offset))
    return value[offset] if offset < len(value) else None


def js_labels(js, dim_id):
    return js["dimension"][dim_id]["category"].get("label", {})


def js_last_period(js, dim_id="Tid"):
    axes = js_axes(js)
    return axes[js["id"].index(dim_id)][-1]


# --- Foretagens ekonomi: the sector benchmark ------------------------------
# TAB6273 income statement + TAB6306 balance sheet share the same NACE-section
# and size-class dimensions, so one industry lookup drives both.
TAB_INCOME = "TAB6273"
TAB_BALANCE = "TAB6306"
CC_INCOME = "000007E5"          # SEK million
CC_BALANCE = "000007E6"         # SEK million

SIZE_CLASSES = ["0-9", "10-19", "20-49", "50-99", "100-249", "250+"]

# NACE section -> the code Foretagens ekonomi uses, and the SNI division range
# that maps onto it. K (finance), O (public), T (households) and U
# (extraterritorial) are absent from the table by design.
SECTIONS = [
    ("A", "A21_A-01-03", (1, 3), "Agriculture, forestry and fishing"),
    ("B", "A21_B-05-09", (5, 9), "Mining and quarrying"),
    ("C", "A21_C-10-33", (10, 33), "Manufacturing"),
    ("D", "A21_D-35", (35, 35), "Electricity, gas, steam and air conditioning"),
    ("E", "A21_E-36-39", (36, 39), "Water supply, sewerage, waste management"),
    ("F", "A21_F-41-43", (41, 43), "Construction"),
    ("G", "A21_G-45-47", (45, 47), "Wholesale and retail trade"),
    ("H", "A21_H-49-53", (49, 53), "Transportation and storage"),
    ("I", "A21_I-55-56", (55, 56), "Accommodation and food service"),
    ("J", "A21_J-58-63", (58, 63), "Information and communication"),
    ("L", "A21_L-68", (68, 68), "Real estate activities"),
    ("M", "A21_M-69-75", (69, 75), "Professional, scientific and technical"),
    ("N", "A21_N-77-82", (77, 82), "Administrative and support services"),
    ("P", "A21_P-85", (85, 85), "Education"),
    ("Q", "A21_Q-86-88", (86, 88), "Human health and social work"),
    ("R", "A21_R-90-93", (90, 93), "Arts, entertainment and recreation"),
    ("S", "A21_S-94-96", (94, 96), "Other service activities"),
]
TOTAL_SECTION = ("TOTAL", "Total_A-SexklK-O", None,
                 "All NACE excluding K, O, T and U")

# Sections Foretagens ekonomi does not cover. Saying so is more useful than
# quietly mapping a bank onto "other service activities".
UNCOVERED = {
    "K": "Financial and insurance activities (64-66)",
    "O": "Public administration and defence (84)",
    "T": "Activities of households (97-98)",
    "U": "Extraterritorial organisations (99)",
}


def section_for_division(div):
    for letter, code, rng, name in SECTIONS:
        if rng and rng[0] <= div <= rng[1]:
            return (letter, code, name)
    return None


def section_by_letter(letter):
    letter = letter.upper()
    if letter in ("TOTAL", "ALL"):
        return (TOTAL_SECTION[0], TOTAL_SECTION[1], TOTAL_SECTION[3])
    for l, code, rng, name in SECTIONS:
        if l == letter:
            return (l, code, name)
    return None


# --- SNI classification ----------------------------------------------------

SNI_RESULT_RE = re.compile(
    r'<div class="result">\s*<a href="/(\d+)">\s*([\d.]+)\s+(.*?)</a>', re.S)


def sni_search(term):
    """Free-text SNI lookup against SCB's public search service.

    HTML only -- there is no JSON endpoint. The service now serves SNI 2025
    (NACE Rev. 2.1); Foretagens ekonomi is still on NACE Rev. 2. At SECTION
    level the two agree closely enough to benchmark against, which is the only
    level that table publishes anyway.
    """
    url = SNI_SOK + "?" + urllib.parse.urlencode({"q": term})
    try:
        raw = fetch(url, ttl=TTL_SLOW)
    except Unavailable:
        return []
    page = raw.decode("utf-8", "replace")
    hits = []
    for _href, code, label in SNI_RESULT_RE.findall(page):
        label = html.unescape(re.sub(r"<[^>]+>", "", label)).strip()
        hits.append({"sni": code, "label": label})
    return hits


UNCOVERED_RANGES = {"K": (64, 66), "O": (84, 84), "T": (97, 98), "U": (99, 99)}


def classify(query):
    """Map a query to a NACE section, and be honest about how sure we are.

    Confidence rules, in order:
      HIGH   -- the caller gave an explicit SNI code or section letter
      HIGH   -- 80% or more of the top SNI hits sit in one section
      MEDIUM -- a simple majority of the top hits agree
      LOW    -- the hits are scattered across sections, or there are none

    SCB's search is stem-based and noisy: 'fastighet' (real estate) also
    returns furniture manufacturing, because 'fast' matches 'fast monterade'.
    Forcing a match there would produce a confident, wrong benchmark, so a
    scattered result is reported as LOW rather than resolved by guesswork.
    """
    q = (query or "").strip()

    # Explicit section letter, e.g. "C" or "L".
    if re.fullmatch(r"[A-Za-z]", q) or q.upper() in ("TOTAL", "ALL"):
        sec = section_by_letter(q)
        if sec:
            return {"confidence": "HIGH", "section": sec,
                    "basis": "explicit NACE section letter", "hits": []}
        if q.upper() in UNCOVERED:
            return {"confidence": "UNCOVERED", "section": None,
                    "basis": "section %s: %s" % (q.upper(), UNCOVERED[q.upper()]),
                    "hits": []}

    # Explicit SNI / NACE code, e.g. "68.20", "6820", "28".
    m = re.fullmatch(r"(\d{2})[.\s]?(\d{0,3})", q)
    if m:
        div = int(m.group(1))
        sec = section_for_division(div)
        if sec:
            return {"confidence": "HIGH", "section": sec,
                    "basis": "SNI division %02d given explicitly" % div,
                    "hits": []}
        for letter, rng in UNCOVERED_RANGES.items():
            if rng[0] <= div <= rng[1]:
                return {"confidence": "UNCOVERED", "section": None,
                        "basis": "SNI %02d falls in section %s: %s"
                                 % (div, letter, UNCOVERED[letter]), "hits": []}
        return {"confidence": "LOW", "section": None,
                "basis": "SNI division %02d is not in the table" % div,
                "hits": []}

    # Free text -> search, then let the top hits vote on a section.
    hits = sni_search(q)
    if not hits:
        return {"confidence": "LOW", "section": None,
                "basis": "SNI search returned no hits for %r" % q, "hits": []}

    top = hits[:10]
    votes = {}
    for h in top:
        try:
            div = int(h["sni"].split(".")[0])
        except ValueError:
            continue
        sec = section_for_division(div)
        if sec:
            votes.setdefault(sec[0], []).append(h)
            h["section"] = sec[0]

    if not votes:
        return {"confidence": "LOW", "section": None,
                "basis": "SNI hits fall outside the sections the table covers",
                "hits": top}

    ranked = sorted(votes.items(), key=lambda kv: -len(kv[1]))
    best, members = ranked[0]
    share = len(members) / float(len(top))
    sec = section_by_letter(best)
    conf = "HIGH" if share >= 0.8 else ("MEDIUM" if share >= 0.5 else "LOW")
    return {"confidence": conf,
            "section": sec if conf != "LOW" else None,
            "candidate_section": sec,
            "basis": "%d of %d top SNI hits map to section %s (%s)"
                     % (len(members), len(top), best, sec[2] if sec else "?"),
            "hits": top}


# --- The benchmark itself --------------------------------------------------

def industry_benchmark(query, size="250+", year=None):
    """Official Swedish sector margin and capital structure for an industry."""
    cls = classify(query)
    out = {"query": query, "size_class": size,
           "classification": {k: v for k, v in cls.items() if k != "hits"},
           "sni_hits": cls.get("hits", [])[:8]}

    if cls["confidence"] == "UNCOVERED":
        out["status"] = "NOT COVERED BY FORETAGENS EKONOMI"
        return out
    if cls["confidence"] == "LOW" or not cls["section"]:
        out["status"] = "INDUSTRY BENCHMARK - LOW CLASSIFICATION CONFIDENCE"
        return out

    letter, code, name = cls["section"]
    out["section"] = {"nace": letter, "name": name, "scb_code": code}

    try:
        js = scb_data(TAB_INCOME, [
            ("Resultatraknposter", ["fgr400", "fgr0140", "fgr0160", "fgr558"]),
            ("SNI2007", [code]),
            ("Storleksklass", [size]),
            ("ContentsCode", [CC_INCOME]),
            ("Tid", [year] if year else ["top(1)"])])
    except Unavailable as e:
        out["status"] = "DATA NOT AVAILABLE: SCB %s (%s)" % (TAB_INCOME, e)
        return out

    axes = js_axes(js)
    per = year or js_last_period(js)
    out["year"] = per
    out["source_income"] = {"table": TAB_INCOME, "label": js.get("label"),
                            "updated": js.get("updated"), "unit": "SEK million"}

    def inc(item):
        return js_get(js, axes, [item, code, size, CC_INCOME, per])

    turnover, ebit = inc("fgr400"), inc("fgr0140")
    pretax = inc("fgr0160")
    # SCB signs cost lines negative (depreciation comes back as e.g. -104894),
    # so add it back as a magnitude or EBITDA lands below EBIT.
    depr = inc("fgr558")
    depr = abs(depr) if depr is not None else None
    out["income_statement_sekm"] = {
        "net_turnover": turnover, "operating_profit": ebit,
        "depreciation_and_writedowns": depr,
        "profit_after_financial_items": pretax}

    out["operating_margin_pct"] = None
    if turnover:
        if ebit is not None:
            out["operating_margin_pct"] = round(100.0 * ebit / turnover, 2)
        if pretax is not None:
            out["pretax_margin_pct"] = round(100.0 * pretax / turnover, 2)
        if ebit is not None and depr is not None:
            out["ebitda_margin_pct"] = round(100.0 * (ebit + depr) / turnover, 2)

    try:
        bs = scb_data(TAB_BALANCE, [
            ("Balansraknposter", ["fgb1299", "fgb372", "fgb0118", "fgb0121",
                                  "fsum904", "fgb0104"]),
            ("SNI2007", [code]),
            ("Storleksklass", [size]),
            ("ContentsCode", [CC_BALANCE]),
            ("Tid", [per])])
    except Unavailable:
        bs = None

    if bs:
        baxes = js_axes(bs)

        def bal(item):
            return js_get(bs, baxes, [item, code, size, CC_BALANCE, per])

        assets, equity = bal("fgb1299"), bal("fgb372")
        ltd, std = bal("fgb0118"), bal("fgb0121")
        cash, fin = bal("fsum904"), bal("fgb0104")
        out["source_balance"] = {"table": TAB_BALANCE, "label": bs.get("label"),
                                 "updated": bs.get("updated"),
                                 "unit": "SEK million"}
        out["balance_sheet_sekm"] = {
            "total_assets": assets, "total_equity": equity,
            "long_term_liabilities": ltd, "short_term_liabilities": std,
            "cash_and_short_term_investments": cash, "financial_assets": fin}
        if assets:
            if equity is not None:
                out["equity_ratio_pct"] = round(100.0 * equity / assets, 2)
            if fin is not None:
                out["financial_assets_share_of_assets_pct"] = \
                    round(100.0 * fin / assets, 2)
        if None not in (ltd, std, cash):
            out["gross_debt_sekm"] = ltd + std
            out["net_debt_sekm"] = ltd + std - cash
            if ebit is not None and depr is not None and (ebit + depr):
                out["net_debt_to_ebitda_x"] = round(
                    (ltd + std - cash) / float(ebit + depr), 2)

    out["status"] = "OK"
    return out


def industry_table(size="250+", year=None):
    """Every covered section at one size class -- the whole benchmark grid."""
    js = scb_data(TAB_INCOME, [
        ("Resultatraknposter", ["fgr400", "fgr0140"]),
        ("SNI2007", ["*"]),
        ("Storleksklass", [size]),
        ("ContentsCode", [CC_INCOME]),
        ("Tid", [year] if year else ["top(1)"])])
    axes = js_axes(js)
    per = year or js_last_period(js)
    labels = js_labels(js, "SNI2007")
    rows = []
    for code in axes[js["id"].index("SNI2007")]:
        turn = js_get(js, axes, ["fgr400", code, size, CC_INCOME, per])
        ebit = js_get(js, axes, ["fgr0140", code, size, CC_INCOME, per])
        rows.append({"scb_code": code, "label": labels.get(code, code),
                     "net_turnover_sekm": turn, "operating_profit_sekm": ebit,
                     "operating_margin_pct":
                         round(100.0 * ebit / turn, 2)
                         if turn and ebit is not None else None})
    return {"year": per, "size_class": size, "table": TAB_INCOME,
            "updated": js.get("updated"), "rows": rows}


# --- Prices and activity indicators ----------------------------------------

def cpi(periods=6):
    """Swedish CPI: index level and annual rate. SCB TAB6596."""
    js = scb_data("TAB6596", [
        ("ContentsCode", ["00000808", "00000804"]),
        ("Tid", ["top(%d)" % periods])], ttl=TTL_FAST)
    axes = js_axes(js)
    rows = []
    for per in axes[js["id"].index("Tid")]:
        rows.append({"period": per,
                     "index_2020_100": js_get(js, axes, ["00000808", per]),
                     "annual_change_pct": js_get(js, axes, ["00000804", per])})
    rows.reverse()
    return {"table": "TAB6596", "label": js.get("label"),
            "updated": js.get("updated"), "source": "SCB", "rows": rows}


# TAB3184 encodes the market split in ContentsCode, not in a separate
# dimension. Import prices are the input-cost side of a Swedish manufacturer's
# P&L; export prices are its realised pricing abroad. The gap between them is
# the margin squeeze, which is why both belong in a DCF discussion.
PPI_SERIES = [("000000SA", "PPI total"),
              ("000001I3", "HMPI home"),
              ("000001I4", "EXPI export"),
              ("000001I0", "IMPI import"),
              ("000004XU", "ITPI supply")]


def ppi(product="B-E", periods=4):
    """Swedish PPI with the home / export / import split. SCB TAB3184.

    `product` is a SPIN 2015 code; 'B-E' is the total. The table carries 432
    product levels, so a manufacturer can be benchmarked on its own output
    prices rather than on the national aggregate.
    """
    js = scb_data("TAB3184", [
        ("SPIN2015", [product]),
        ("ContentsCode", ["*"]),
        ("Tid", ["top(%d)" % periods])], ttl=TTL_FAST)
    axes = js_axes(js)
    plabel = js_labels(js, "SPIN2015").get(product, product)
    rows = []
    for per in axes[js["id"].index("Tid")]:
        row = {"period": per}
        for code, name in PPI_SERIES:
            row[name] = js_get(js, axes, [product, code, per])
        rows.append(row)
    rows.reverse()
    return {"table": "TAB3184", "label": js.get("label"),
            "updated": js.get("updated"), "source": "SCB",
            "product": product, "product_label": plabel,
            "index_base": "2020=100", "rows": rows}


def ppi_products(term):
    """Find SPIN 2015 product codes so --ppi can target one industry."""
    js = scb_metadata("TAB3184")
    labels = js["dimension"]["SPIN2015"]["category"]["label"]
    t = term.lower()
    return [{"code": c, "label": l} for c, l in labels.items()
            if t in l.lower() or c.lower().startswith(t)]


# Indicators worth having next to a Swedish forecast: the demand signal for a
# retailer, the order book for an industrial, and housing starts for anything
# exposed to construction.
INDICATORS = {
    "retail": {
        "table": "TAB3948", "desc": "Retail sales index, NACE 47",
        "selection": [("SNI2007", ["47"]),
                      ("ContentsCode", ["000006VX", "000006VY", "000006VZ"]),
                      ("Tid", ["top(4)"])],
        "series": {"000006VX": "index SA+WDA, constant prices (2021=100)",
                   "000006VY": "monthly change pct",
                   "000006VZ": "annual change pct, WDA"},
        "key": ("SNI2007", "47")},
    "orders": {
        "table": "TAB1710", "desc": "Industrial new orders, NACE B+C",
        "selection": [("Marknad", ["TOTALA", "HEMMA", "EXPORT"]),
                      ("SNI2007", ["B+C"]),
                      ("ContentsCode", ["NV0501BD", "NV0501BX"]),
                      ("Tid", ["top(3)"])],
        "series": {"NV0501BD": "index constant prices, CA+SA (2021=100)",
                   "NV0501BX": "annual change pct"},
        "markets": ["TOTALA", "HEMMA", "EXPORT"],
        "key": ("SNI2007", "B+C")},
    "housing": {
        "table": "TAB4572", "desc": "Dwellings started, Sweden",
        "selection": [("Region", ["00"]), ("Hustyp", ["*"]),
                      ("ContentsCode", ["BO0101A4", "BO0101A3"]),
                      ("Tid", ["top(4)"])],
        "series": {"BO0101A4": "dwellings started",
                   "BO0101A3": "dwellings completed"},
        "key": ("Region", "00")},
}


def indicator(name):
    """Run one of the named SCB activity indicators and flatten the result."""
    spec = INDICATORS.get(name)
    if not spec:
        raise SystemExit("Unknown indicator %r. Available: %s"
                         % (name, ", ".join(sorted(INDICATORS))))
    js = scb_data(spec["table"], spec["selection"], ttl=TTL_FAST)
    axes, ids = js_axes(js), js["id"]
    periods = axes[ids.index("Tid")]

    # Build a coordinate template from the selection, then vary time and the
    # content / market dimensions. Keeps one flattener for three table shapes.
    fixed = {}
    for dim, values in spec["selection"]:
        if dim not in ("Tid", "ContentsCode") and len(values) == 1 \
                and values[0] != "*":
            fixed[dim] = values[0]

    rows = []
    for per in periods:
        for cc, cname in spec["series"].items():
            varying = [d for d in ids
                       if d not in fixed and d not in ("Tid", "ContentsCode")]
            combos = [[]]
            for d in varying:
                combos = [c + [v] for c in combos for v in axes[ids.index(d)]]
            for combo in combos:
                coords, vi = [], 0
                for d in ids:
                    if d == "Tid":
                        coords.append(per)
                    elif d == "ContentsCode":
                        coords.append(cc)
                    elif d in fixed:
                        coords.append(fixed[d])
                    else:
                        coords.append(combo[vi])
                        vi += 1
                val = js_get(js, axes, coords)
                if val is None:
                    continue
                row = {"period": per, "measure": cname, "value": val}
                for d, c in zip(ids, coords):
                    if d in ("Tid", "ContentsCode"):
                        continue
                    lab = js_labels(js, d).get(c, c)
                    if d not in fixed or len(varying) == 0:
                        row.setdefault("breakdown", []).append(lab)
                rows.append(row)
    rows.sort(key=lambda r: (r["period"], r["measure"]), reverse=True)
    return {"indicator": name, "table": spec["table"], "desc": spec["desc"],
            "label": js.get("label"), "updated": js.get("updated"),
            "source": "SCB", "rows": rows}


# ---------------------------------------------------------------------------
# ECB and Eurostat -- the European context a Swedish company trades against
# ---------------------------------------------------------------------------

# Verified keyless 2026-08-31. csvdata is used in preference to the SDMX-JSON:
# the JSON encodes both the series key and the time axis as positional indices
# into a separate `structure` block, which is a lot of parsing for two numbers.
ECB_YC = [("SR_3M", "3M"), ("SR_1Y", "1Y"), ("SR_2Y", "2Y"),
          ("SR_5Y", "5Y"), ("SR_10Y", "10Y"), ("SR_30Y", "30Y")]


def ecb_series(dataflow, key, n=1):
    """Latest observations of an ECB series as [(period, value), ...]."""
    url = "%s/%s/%s?format=csvdata&lastNObservations=%d" % (ECB, dataflow, key, n)
    # GOTCHA: the ECB honours the Accept header over the `format` query param.
    # Sending Accept: application/json here silently returns SDMX-JSON instead.
    try:
        raw = fetch(url, accept="text/csv")
    except Unavailable:
        return []
    lines = raw.decode("utf-8", "replace").splitlines()
    if len(lines) < 2:
        return []
    head = [h.strip() for h in lines[0].split(",")]
    try:
        ti, vi = head.index("TIME_PERIOD"), head.index("OBS_VALUE")
    except ValueError:
        return []
    out = []
    for line in lines[1:]:
        cells = line.split(",")
        if len(cells) <= max(ti, vi):
            continue
        try:
            out.append((cells[ti], float(cells[vi])))
        except ValueError:
            continue
    return sorted(out)


def euro_block():
    """Euro-area rates and the EUR curve, for a Swedish company that funds or
    sells in euro. Comparing SEGVB10YC with the euro AAA 10y is the cleanest
    read on whether Sweden is paying a country premium."""
    curve = []
    for code, tenor in ECB_YC:
        got = ecb_series("YC", "B.U2.EUR.4F.G_N_A.SV_C_YM." + code)
        curve.append({"tenor": tenor, "key": code,
                      "rate_pct": got[-1][1] if got else None,
                      "obs_date": got[-1][0] if got else None,
                      "status": "OK" if got else "DATA NOT AVAILABLE"})
    policy = {}
    for name, key in (("main_refinancing_rate_pct", "D.U2.EUR.4F.KR.MRR_FR.LEV"),
                      ("deposit_facility_rate_pct", "D.U2.EUR.4F.KR.DFR.LEV")):
        got = ecb_series("FM", key)
        policy[name] = {"value": got[-1][1] if got else None,
                        "obs_date": got[-1][0] if got else None, "key": key,
                        "status": "OK" if got else "DATA NOT AVAILABLE"}
    fx = {}
    for name, key in (("sek_per_eur", "D.SEK.EUR.SP00.A"),
                      ("usd_per_eur", "D.USD.EUR.SP00.A")):
        got = ecb_series("EXR", key)
        fx[name] = {"value": got[-1][1] if got else None,
                    "obs_date": got[-1][0] if got else None, "key": key}
    return {"source": "ECB Data Portal (data-api.ecb.europa.eu), keyless",
            "aaa_government_curve": curve, "policy_rates": policy,
            "reference_fx": fx}


def eurostat(dataset, filters):
    """Eurostat JSON-stat 2.0 fetch. Keyless."""
    url = "%s/%s?%s" % (EUROSTAT, dataset,
                        urllib.parse.urlencode(filters, doseq=True))
    return fetch_json(url)


def es_decode(js):
    """Flatten a Eurostat JSON-stat dataset to [(coord-dict, value), ...].

    `value` is a sparse, row-major flat dict. A `size` entry of 0 means a
    filter matched nothing -- Eurostat still answers 200, so check for it.
    """
    ids, size, dim = js["id"], js["size"], js["dimension"]
    if 0 in size:
        return []
    cats = []
    for d in ids:
        idx = dim[d]["category"]["index"]
        if isinstance(idx, dict):
            arr = [None] * len(idx)
            for code, pos in idx.items():
                arr[pos] = code
            cats.append(arr)
        else:
            cats.append(list(idx))
    stride = [1] * len(size)
    for i in range(len(size) - 2, -1, -1):
        stride[i] = stride[i + 1] * size[i + 1]
    out = []
    for k, v in js.get("value", {}).items():
        n = int(k)
        coord = {ids[i]: cats[i][n // stride[i] % size[i]]
                 for i in range(len(size))}
        out.append((coord, v))
    return out


def hicp(geos=("SE", "EA20")):
    """Harmonised inflation, SE against the euro area.

    NOTE: the obvious dataset prc_hicp_manr is a trap. It still answers 200 but
    was frozen at 2025M12 when Eurostat moved to ECOICOP ver.2. This uses the
    live prc_hicp_minr, where the product dimension is `coicop18` (not
    `coicop`), the total is TOTAL (not CP00), and unit=RCH_A must be given or
    you get five different measures back.
    """
    try:
        js = eurostat("prc_hicp_minr", {
            "format": "JSON", "lang": "EN", "geo": list(geos),
            "coicop18": "TOTAL", "unit": "RCH_A", "lastTimePeriod": 3})
    except Unavailable as e:
        return {"status": "DATA NOT AVAILABLE: Eurostat (%s)" % e}
    rows = [{"geo": c["geo"], "period": c["time"], "annual_rate_pct": v}
            for c, v in es_decode(js)]
    rows.sort(key=lambda r: (r["period"], r["geo"]), reverse=True)
    return {"source": "Eurostat prc_hicp_minr (HICP annual rate), keyless",
            "updated": js.get("updated"), "rows": rows,
            "status": "OK" if rows else "DATA NOT AVAILABLE"}


def eu_gross_operating_rate(nace, year="2023"):
    """EU cross-check on a Swedish sector margin.

    Eurostat's structural business statistics publish GOR_PC, the gross
    operating rate (gross operating surplus over turnover), by NACE for both
    Sweden and the EU27. It is NOT the same measure as SCB's operating margin
    -- gross operating surplus is before depreciation -- so read it as a
    relative check on whether Swedish firms in a sector out-earn their EU
    peers, never as a second opinion on the level.

    GOR_PC stops at 2023 even though the dataset runs to 2024, so the year is
    pinned; lastTimePeriod=1 would silently return nothing.
    """
    try:
        js = eurostat("sbs_ovw_act", {
            "format": "JSON", "lang": "EN", "geo": ["SE", "EU27_2020"],
            "nace_r2": nace, "indic_sbs": "GOR_PC", "time": year})
    except Unavailable as e:
        return {"status": "DATA NOT AVAILABLE: Eurostat (%s)" % e}
    rows = {c["geo"]: v for c, v in es_decode(js)}
    if not rows:
        return {"status": "DATA NOT AVAILABLE: no GOR_PC for NACE %s in %s"
                          % (nace, year)}
    return {"source": "Eurostat sbs_ovw_act, indicator GOR_PC", "nace": nace,
            "year": year, "gross_operating_rate_pct": rows,
            "definition": "gross operating surplus / turnover, before "
                          "depreciation", "status": "OK"}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def fmt(v, spec="%.3f"):
    return "DATA NOT AVAILABLE" if v is None else spec % v


def pct(v, spec="%.3f"):
    """Percent with the unit attached -- but no '%' after a missing value."""
    return "DATA NOT AVAILABLE" if v is None else (spec % v) + " %"


def print_dcf(d):
    print("SWEDISH DCF INPUT BLOCK          as of %s" % d["as_of"])
    print("=" * 78)
    print()
    print("FACTS  -- observed, dated, attributable")
    print("-" * 78)
    f = d["facts"]
    for key, label in (("risk_free_rate_pct", "Risk-free rate (10y govt)"),
                       ("policy_rate_pct", "Policy rate")):
        x = f[key]
        print("  %-28s %12s   obs %-11s %s"
              % (label, pct(x["value"]), x["obs_date"] or "-", x["series"]))
    if "term_spread_10y_2y_bp" in f:
        t = f["term_spread_10y_2y_bp"]
        print("  %-28s %10s bp   obs %s"
              % ("Term spread 10y-2y", fmt(t["value"], "%.1f"), t["obs_date"]))
    print()
    for ccy, x in sorted(f["fx_sek"].items()):
        print("  %-28s %12s   obs %-11s %s"
              % ("SEK per " + ccy, fmt(x["sek_per_unit"], "%.4f"),
                 x["obs_date"] or "-", x["source"]))
    print()
    tax = f["statutory_corporate_tax_pct"]
    print("  %-28s %10s %%" % ("Statutory corporate tax", fmt(tax["value"], "%.1f")))
    for line in _wrap(tax["note"], 68):
        print("      %s" % line)
    print()
    print("  Source: Sveriges Riksbank SWEA. Free, keyless, official.")
    print()

    cs = [c for c in d["credit_spreads"] if c.get("status") == "OK"]
    if d["credit_spreads"] and cs:
        print("OBSERVED SEK CREDIT SPREAD  -- covered bonds over government")
        print("-" * 78)
        for c in cs:
            print("  %-4s  %6.3f %% vs %6.3f %%  =  %+6.1f bp   obs %s"
                  % (c["tenor"], c["mortgage_bond_pct"], c["government_pct"],
                     c["spread_bp"], c["obs_date"]))
        print("  A floor for a corporate cost of debt, not a substitute for it:")
        print("  covered bonds are collateralised and rated far above a typical")
        print("  corporate issuer. Use the company's own coupons where they exist.")
        print()

    print("ASSUMPTIONS  -- not observed. These are yours to defend.")
    print("-" * 78)
    a = d["assumptions"]
    erp = a["equity_risk_premium_pct"]
    print("  Equity risk premium          %s"
          % (("%.2f %%  (supplied via --erp)" % erp["value"])
             if erp["value"] is not None else "NOT SET"))
    print("      %s" % erp["source"])
    for line in _wrap(erp["note"], 68):
        print("      %s" % line)
    b = a["beta"]
    print("  Beta                         %s"
          % (("%.2f  (supplied via --beta)" % b["value"])
             if b["value"] is not None else "NOT SET"))
    print("      %s" % b["note"])
    print("  Terminal growth              NOT SET")
    for line in _wrap(a["terminal_growth_pct"]["note"], 68):
        print("      %s" % line)
    print()

    ke = d.get("cost_of_equity")
    if ke:
        print("COST OF EQUITY  -- %s" % ke["kind"])
        print("-" * 78)
        print("  Ke = %.3f %%" % ke["value_pct"])
        print("  %s" % ke["formula"])
    else:
        print("COST OF EQUITY")
        print("-" * 78)
        print("  Not computed. Pass --beta and --erp to combine the observed")
        print("  risk-free rate with your own assumptions. The script will not")
        print("  invent either one.")
    print()


def _wrap(text, width):
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    if line:
        out.append(line)
    return out


def print_curve(rows, euro=None):
    print("SWEDISH YIELD CURVE   Sveriges Riksbank SWEA")
    print("=" * 78)
    print("  %-6s %-18s %12s   %s" % ("TENOR", "INSTRUMENT", "YIELD", "SERIES"))
    print("  " + "-" * 74)
    for r in rows:
        print("  %-6s %-18s %12s   %s"
              % (r["tenor"], r["instrument"], pct(r["rate_pct"]), r["series"]))
    good = [r for r in rows if r["rate_pct"] is not None]
    if good:
        print()
        print("  Observation date %s. Maturities are the ones SWEA actually"
              % good[-1]["obs_date"])
        print("  publishes: there is no 15y, 20y or 30y Swedish series here, and")
        print("  the STIBOR fixings were retired in 2020, so the short end is")
        print("  Treasury bills. Anything beyond 10y has to be extrapolated, and")
        print("  should be labelled as such in a model.")
    if euro:
        print()
        print("EURO AREA AAA CURVE   ECB Data Portal")
        print("  " + "-" * 74)
        for r in euro["aaa_government_curve"]:
            print("  %-6s %-18s %12s   %s"
                  % (r["tenor"], "AAA govt spot", pct(r["rate_pct"]), r["key"]))
        se10 = next((r["rate_pct"] for r in rows if r["tenor"] == "10Y"), None)
        eu10 = next((r["rate_pct"] for r in euro["aaa_government_curve"]
                     if r["tenor"] == "10Y"), None)
        if se10 is not None and eu10 is not None:
            print()
            print("  Sweden 10y less euro AAA 10y: %+.1f bp." % ((se10 - eu10) * 100))
            print("  Both are risk-free by construction; the gap is currency and")
            print("  country, not credit.")
    print()


def print_industry(b, eu=None):
    print("OFFICIAL SWEDISH INDUSTRY BENCHMARK")
    print("=" * 78)
    print("  Query               %s" % b["query"])
    c = b["classification"]
    print("  Classification      %s  (%s)" % (c["confidence"], c["basis"]))

    if b["status"] != "OK":
        print()
        print("  %s" % b["status"])
        print()
        if b["sni_hits"]:
            print("  SNI search returned hits spread across sections:")
            for h in b["sni_hits"]:
                print("    %-9s %-8s %s"
                      % (h["sni"], h.get("section", "?"), h["label"][:52]))
            print()
        if b["status"].startswith("NOT COVERED"):
            for line in _wrap(
                "Foretagens ekonomi excludes financial and insurance activities "
                "(K), public administration (O), household activities (T) and "
                "extraterritorial bodies (U). For a bank or insurer the income "
                "statement in this table would be meaningless anyway; use "
                "Finansinspektionen's institution statistics instead. There is "
                "no benchmark to give here, and inventing one from a "
                "neighbouring section would be a fabrication.", 74):
                print("  %s" % line)
        elif b["status"].startswith("DATA NOT AVAILABLE"):
            for line in _wrap(
                "The classification succeeded; SCB did not answer. Nothing is "
                "wrong with the industry code - retry, or pass --year to pin a "
                "published year.", 74):
                print("  %s" % line)
        else:
            for line in _wrap(
                "Refusing to pick one. Re-run with an explicit SNI code "
                "(--industry 28.99) or a NACE section letter (--industry C) "
                "once the company's actual activity is established. SCB's "
                "search is stem-based, so a plausible-looking term can match "
                "across unrelated sections. A benchmark attached to the wrong "
                "sector is worse than no benchmark.", 74):
                print("  %s" % line)
        print()
        return

    s = b["section"]
    print("  NACE section        %s -- %s   [SCB code %s]"
          % (s["nace"], s["name"], s["scb_code"]))
    print("  Size class          %s employees" % b["size_class"])
    print("  Financial year      %s" % b["year"])
    print()
    print("  FACT -- aggregate income statement of ALL Swedish enterprises in")
    print("  this section and size class (SEK million)")
    print("  " + "-" * 74)
    i = b["income_statement_sekm"]
    for k, label in (("net_turnover", "Net turnover"),
                     ("operating_profit", "Operating profit"),
                     ("depreciation_and_writedowns", "Depreciation & write-downs"),
                     ("profit_after_financial_items", "Profit after fin. items")):
        v = i.get(k)
        print("    %-30s %16s" % (label, "{:,}".format(v) if v is not None
                                  else "DATA NOT AVAILABLE"))
    print()
    print("    %-30s %15s" % ("Operating margin",
                              fmt(b.get("operating_margin_pct"), "%.2f") + " %"))
    if b.get("ebitda_margin_pct") is not None:
        print("    %-30s %15s" % ("EBITDA margin",
                                  "%.2f %%" % b["ebitda_margin_pct"]))
    if b.get("pretax_margin_pct") is not None:
        print("    %-30s %15s" % ("Pre-tax margin",
                                  "%.2f %%" % b["pretax_margin_pct"]))
    print()

    if b.get("balance_sheet_sekm"):
        print("  FACT -- aggregate balance sheet, same population (SEK million)")
        print("  " + "-" * 74)
        bs = b["balance_sheet_sekm"]
        for k, label in (("total_assets", "Total assets"),
                         ("total_equity", "Total equity"),
                         ("long_term_liabilities", "Long-term liabilities"),
                         ("short_term_liabilities", "Short-term liabilities"),
                         ("cash_and_short_term_investments", "Cash & ST investments")):
            v = bs.get(k)
            print("    %-30s %16s" % (label, "{:,}".format(v) if v is not None
                                      else "DATA NOT AVAILABLE"))
        print()
        if b.get("equity_ratio_pct") is not None:
            print("    %-30s %15s" % ("Equity ratio", "%.2f %%" % b["equity_ratio_pct"]))
        if b.get("net_debt_sekm") is not None:
            print("    %-30s %16s" % ("Net debt", "{:,}".format(b["net_debt_sekm"])))
        if b.get("net_debt_to_ebitda_x") is not None:
            print("    %-30s %15s" % ("Net debt / EBITDA",
                                      "%.2f x" % b["net_debt_to_ebitda_x"]))
        fa = b.get("financial_assets_share_of_assets_pct")
        if fa is not None and fa > 30:
            print()
            print("    CAUTION: financial assets are %.0f %% of total assets here." % fa)
            print("    At 250+ employees the population includes group holding")
            print("    companies, so the balance sheet is inflated by intra-group")
            print("    holdings. The equity ratio is still comparable; asset")
            print("    turnover and ROA are not.")
        print()

    print("  Sources: SCB %s (income) and %s (balance sheet), Foretagens"
          % (b["source_income"]["table"],
             b.get("source_balance", {}).get("table", "-")))
    print("  ekonomi. CC0. Updated %s." % b["source_income"].get("updated", "-"))
    print()

    if eu and eu.get("status") == "OK":
        g = eu["gross_operating_rate_pct"]
        print("  EU CROSS-CHECK -- Eurostat gross operating rate, NACE %s, %s"
              % (eu["nace"], eu["year"]))
        print("  " + "-" * 74)
        for geo in ("SE", "EU27_2020"):
            if geo in g:
                print("    %-30s %15s" % (geo, "%.2f %%" % g[geo]))
        for line in _wrap("Different measure (%s), so read this as SE versus "
                          "EU, never as a second opinion on the SCB margin "
                          "above." % eu["definition"], 70):
            print("    %s" % line)
        print()

    print("  HOW FAR THIS BENCHMARK GOES")
    print("  " + "-" * 74)
    for line in _wrap(
        "This is a national sector aggregate, not a peer group. It is the "
        "turnover-weighted average of every Swedish enterprise in the section, "
        "so it is dominated by the largest firms and says nothing about the "
        "dispersion a listed company sits within. The section is broad: NACE C "
        "covers a sawmill and a medtech manufacturer alike. It is Sweden-only, "
        "so a company earning most of its revenue abroad is being measured "
        "against a domestic denominator. And it is annual and lagged, so it "
        "cannot speak to the current cycle. Use it to ask why a company differs "
        "from its sector, never to conclude that it should not.", 74):
        print("  %s" % line)
    print()


def print_industry_table(t):
    print("SWEDISH SECTOR OPERATING MARGINS   %s, %s employees"
          % (t["year"], t["size_class"]))
    print("SCB %s Foretagens ekonomi, updated %s" % (t["table"], t["updated"]))
    print("=" * 78)
    print("  %-52s %12s %8s" % ("NACE SECTION", "TURNOVER", "MARGIN"))
    print("  " + "-" * 74)
    for r in t["rows"]:
        print("  %-52.52s %12s %7s"
              % (r["label"],
                 "{:,}".format(r["net_turnover_sekm"])
                 if r["net_turnover_sekm"] is not None else "n/a",
                 (("%.1f %%" % r["operating_margin_pct"])
                  if r["operating_margin_pct"] is not None else "n/a")))
    print()
    print("  Turnover in SEK million. Margin = operating profit / net turnover.")
    print("  Sections K (finance), O (public), T and U are not in this table.")
    print()


def print_rows(title, meta, rows, cols):
    print(title)
    print("=" * 78)
    if meta:
        print("  %s" % meta)
    print()
    print("  " + "  ".join("%-*s" % (w, h) for h, w in cols))
    print("  " + "-" * 74)
    for r in rows:
        cells = []
        for h, w in cols:
            v = r.get(h)
            if isinstance(v, bool):
                pass
            elif isinstance(v, (int, float)):
                # Counts (dwellings started) read badly as 6076.00.
                v = ("{:,}".format(int(v)) if float(v).is_integer()
                     and abs(v) >= 1000 else "%.2f" % v)
            elif isinstance(v, list):
                v = " / ".join(str(x) for x in v)
            cells.append("%-*.*s" % (w, w, "n/a" if v is None else str(v)))
        print("  " + "  ".join(cells))
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="Swedish macro rates and official industry benchmarks "
                    "from Riksbanken, SCB, ECB and Eurostat. Free, keyless.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Every number is labelled FACT (observed and dated) or "
               "ASSUMPTION (yours). Nothing here needs an API key.")
    p.add_argument("--dcf-inputs", action="store_true",
                   help="risk-free rate, policy rate and FX, each with its "
                        "observation date")
    p.add_argument("--curve", action="store_true",
                   help="Swedish yield curve from the maturities SWEA publishes")
    p.add_argument("--industry", metavar="SNI|TERM",
                   help="official sector margin and capital structure; accepts "
                        "an SNI code (28.99), a NACE section letter (C), or a "
                        "search term")
    p.add_argument("--all-industries", action="store_true",
                   help="margin for every NACE section at one size class")
    p.add_argument("--size", default="250+", choices=SIZE_CLASSES,
                   help="employee size class for the benchmark (default 250+)")
    p.add_argument("--year", help="financial year, e.g. 2024 (default: latest)")
    p.add_argument("--cpi", action="store_true", help="Swedish CPI, SCB TAB6596")
    p.add_argument("--ppi", action="store_true",
                   help="Swedish PPI with home/export/import split, SCB TAB3184")
    p.add_argument("--product", default="B-E",
                   help="SPIN 2015 product code for --ppi (default B-E, total)")
    p.add_argument("--find-product", metavar="TERM",
                   help="search the 432 SPIN 2015 product codes")
    p.add_argument("--indicator", choices=sorted(INDICATORS),
                   help="activity indicator: " + ", ".join(sorted(INDICATORS)))
    p.add_argument("--hicp", action="store_true",
                   help="Eurostat HICP, Sweden against the euro area")
    p.add_argument("--euro", action="store_true",
                   help="ECB euro-area policy rates, AAA curve and reference FX")
    p.add_argument("--peers", action="store_true",
                   help="foreign 10y benchmarks alongside the Swedish one "
                        "(extra Riksbank calls, so opt-in)")
    p.add_argument("--series", action="store_true",
                   help="list the live Riksbank SWEA series catalogue")
    p.add_argument("--erp", type=float,
                   help="your equity risk premium, in percent (an ASSUMPTION)")
    p.add_argument("--beta", type=float,
                   help="your equity beta (an ASSUMPTION)")
    p.add_argument("--tax", type=float, default=20.6,
                   help="statutory corporate tax rate (default 20.6)")
    p.add_argument("--credit", action="store_true",
                   help="add the observed SEK covered-bond credit spread "
                        "(extra Riksbank calls, so opt-in)")
    p.add_argument("--fx-all", action="store_true",
                   help="all eight SEK crosses instead of just EUR and USD")
    p.add_argument("--no-eu", action="store_true",
                   help="skip the ECB/Eurostat calls")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="machine-readable output")
    args = p.parse_args()

    if not any([args.dcf_inputs, args.curve, args.industry, args.all_industries,
                args.cpi, args.ppi, args.find_product, args.indicator,
                args.hicp, args.euro, args.series, args.peers]):
        p.print_help()
        return

    out = {}

    if args.series:
        rows = swea_catalog()
        out["swea_series"] = rows
        if not args.as_json:
            print("LIVE RIKSBANK SWEA SERIES  (%d)" % len(rows))
            print("=" * 78)
            for r in sorted(rows, key=lambda x: x.get("groupId", 0)):
                print("  %-20s %-40.40s %s -> %s"
                      % (r["seriesId"], r.get("shortDescription", ""),
                         r.get("observationMinDate", ""),
                         r.get("observationMaxDate", "")))
            print()

    if args.dcf_inputs:
        d = dcf_inputs(erp=args.erp, beta=args.beta, tax_rate=args.tax,
                       extra_fx=args.fx_all, credit=args.credit)
        out["dcf_inputs"] = d
        if not args.as_json:
            print_dcf(d)

    if args.curve:
        rows = se_curve()
        euro = None if args.no_eu else euro_block()
        out["se_curve"] = rows
        if euro:
            out["euro"] = euro
        if not args.as_json:
            print_curve(rows, euro)

    if args.peers:
        rows = peer_10y()
        out["peer_10y"] = rows
        if not args.as_json:
            print("10-YEAR GOVERNMENT BENCHMARKS   all via Riksbanken SWEA")
            print("=" * 78)
            se = swea_latest("SEGVB10YC")
            for r in sorted(rows, key=lambda x: (x["rate_pct"] is None,
                                                 x["rate_pct"])):
                delta = ""
                if se and se[0] is not None and r["rate_pct"] is not None:
                    delta = "   SE %+.1f bp" % ((se[0] - r["rate_pct"]) * 100)
                print("  %-4s %12s   obs %-11s %s%s"
                      % (r["country"], pct(r["rate_pct"]),
                         r["obs_date"] or "-", r["series"], delta))
            if se:
                print("  %-4s %12s   obs %-11s %s"
                      % ("SE", pct(se[0]), se[1], "SEGVB10YC"))
            print()
            print("  One source, one convention. The spread to Germany is the")
            print("  cleanest read on Sweden's country and currency premium.")
            print()

    if args.industry:
        b = industry_benchmark(args.industry, size=args.size, year=args.year)
        eu = None
        if b.get("status") == "OK" and not args.no_eu:
            eu = eu_gross_operating_rate(b["section"]["nace"])
            b["eu_cross_check"] = eu
        out["industry"] = b
        if not args.as_json:
            print_industry(b, eu)

    if args.all_industries:
        t = industry_table(size=args.size, year=args.year)
        out["industry_table"] = t
        if not args.as_json:
            print_industry_table(t)

    if args.cpi:
        c = cpi()
        out["cpi"] = c
        if not args.as_json:
            print_rows("SWEDISH CPI   SCB %s" % c["table"],
                       "%s | updated %s" % (c["label"], c["updated"]),
                       c["rows"], [("period", 10), ("index_2020_100", 16),
                                   ("annual_change_pct", 18)])

    if args.find_product:
        hits = ppi_products(args.find_product)
        out["spin_products"] = hits
        if not args.as_json:
            print("SPIN 2015 PRODUCT CODES matching %r  (%d)"
                  % (args.find_product, len(hits)))
            print("=" * 78)
            for h in hits[:60]:
                print("  %-16s %s" % (h["code"], h["label"][:56]))
            print()

    if args.ppi:
        d = ppi(product=args.product)
        out["ppi"] = d
        if not args.as_json:
            print_rows("SWEDISH PPI   SCB %s   %s [%s]"
                       % (d["table"], d["product_label"], d["product"]),
                       "%s | updated %s" % (d["index_base"], d["updated"]),
                       d["rows"],
                       [("period", 9)] + [(n, 12) for _c, n in PPI_SERIES])
            print("  Import prices are the input-cost side of a Swedish")
            print("  manufacturer; export prices are its realised pricing abroad.")
            print("  Import running ahead of export is a margin squeeze in the")
            print("  making, and it shows up here before it shows up in results.")
            print()

    if args.indicator:
        d = indicator(args.indicator)
        out["indicator"] = d
        if not args.as_json:
            print_rows("SWEDISH %s   SCB %s"
                       % (d["desc"].upper(), d["table"]),
                       "%s | updated %s" % (d["label"], d["updated"]),
                       d["rows"][:24],
                       [("period", 9), ("breakdown", 26), ("measure", 24),
                        ("value", 10)])

    if args.hicp:
        h = hicp()
        out["hicp"] = h
        if not args.as_json:
            if h.get("status") == "OK":
                print_rows("HICP ANNUAL RATE   Eurostat prc_hicp_minr",
                           "updated %s" % h.get("updated"), h["rows"],
                           [("period", 10), ("geo", 8), ("annual_rate_pct", 18)])
                print("  Sweden's CPIF, not HICP, is what the Riksbank targets.")
                print("  HICP is here because it is the only measure that is")
                print("  constructed identically across Sweden and the euro area.")
                print()
            else:
                print(h["status"])

    if args.euro and "euro" not in out:
        e = euro_block()
        out["euro"] = e
        if not args.as_json:
            print("EURO AREA   ECB Data Portal, keyless")
            print("=" * 78)
            for name, x in sorted(e["policy_rates"].items()):
                print("  %-32s %12s   obs %s"
                      % (name.replace("_", " "), pct(x["value"], "%.2f"),
                         x["obs_date"] or "-"))
            print()
            print("  AAA government spot curve")
            print("  " + "-" * 74)
            for r in e["aaa_government_curve"]:
                print("    %-6s %12s   obs %s"
                      % (r["tenor"], pct(r["rate_pct"]), r["obs_date"] or "-"))
            print()
            for name, x in sorted(e["reference_fx"].items()):
                print("  %-32s %10s      obs %s"
                      % (name.replace("_", " "), fmt(x["value"], "%.4f"),
                         x["obs_date"] or "-"))
            print()

    if args.as_json:
        print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Unavailable as e:
        raise SystemExit("DATA NOT AVAILABLE: %s" % e)
    except KeyboardInterrupt:
        raise SystemExit(130)
