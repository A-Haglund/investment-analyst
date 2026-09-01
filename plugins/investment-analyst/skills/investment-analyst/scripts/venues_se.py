#!/usr/bin/env python3
"""Which Swedish venue is this company on, and where do its financials come from?

The rest of this toolkit assumes Nasdaq Stockholm. Roughly 570 Swedish listed
companies are NOT on Nasdaq Stockholm's main market, and for those the whole
source chain changes. Getting the venue wrong produces two specific, expensive
analytical errors:

  1. TREATING A MISSING ESEF FILING AS A MISSING FILING. ESEF (the machine
     readable annual report mandated by the Transparency Directive) applies
     only to issuers on a REGULATED MARKET. Spotlight, Nordic SME and First
     North are MTFs, so their issuers never file ESEF and never will. An
     "annual report not found" from esef_fundamentals.py means nothing for
     them - the financials exist, they are just in a PDF. This script exists
     mainly to stop that mistake.

  2. ASSUMING NGM = MTF. It does not. Nordic Growth Market NGM AB operates
     TWO Swedish equity venues with opposite disclosure regimes:
        NGM Equity  (MIC XNGM) - a REGULATED MARKET. ESEF DOES apply.
        Nordic SME  (MIC NSME) - an MTF. ESEF does not apply.
     Verified 2026-08-31 against filings.xbrl.org: Glycorex Transplantation
     (XNGM) has ESEF for FY2021-FY2024, Obducat (XNGM) the same four years,
     Spiltan Invest (XNGM) FY2024, while Kopparbergs (NSME), Absolicon (XSAT)
     and KebNi (First North) have none at all. So "NGM" alone is never a
     sufficient answer - the segment is what decides.

ONE MORE ASSUMPTION IN HERE WAS WRONG AND HAS BEEN CORRECTED: this file used
to imply, by never mentioning one, that no free price or turnover source
exists for XNGM or NSME. It does. NGM's delayed/post-trade feed - a sibling
of the delayed/pre-trade bid/ask snapshot ngm_symbols() reads, and not the
same endpoint - carries every trade actually executed on every NGM segment,
with 'Venue of execution' splitting XNGM and NSME cleanly from NMTF (NGM's
ETP and warrant segment, not the listed equity universe this script covers).
Verified 2026-09-01: 1,413 XNGM trades and 2,633 NSME trades on 2026-08-31
alone. See ngm_turnover() and --ngm-turnover. Retention is 72 hours, so this
is LAST-SESSION turnover and a last-trade price only - it can never build a
history series, and it does not extend to XSAT or First North (NGM does not
operate those venues).

IDENTITY IS ANCHORED ON ESMA FIRDS, NOT ON A NAME SEARCH. FIRDS is the EU
reference database every venue must report its instruments to, keyed by MIC,
so it is the only free source that authoritatively answers "which venue".
Name searches on news wires are not: querying MFN for "Spiltan Invest" returns
the entity "nordnet" (XSTO:SAVE), and "Bluelake Mineral" returns the research
house "mangold-insight". This script therefore resolves ISIN+LEI from FIRDS
first and only accepts a wire entity whose own ISIN or LEI matches.

Usage:
    python venues_se.py "Kopparbergs"              # which venue, and why it matters
    python venues_se.py --check "Absolicon"        # the ESEF routing question
    python venues_se.py --venue spotlight --list
    python venues_se.py --venue ngm --list         # NGM Equity + Nordic SME
    python venues_se.py --venue first-north --list
    python venues_se.py "KebNi" --json
    python venues_se.py --ngm-turnover              # last completed session
    python venues_se.py --ngm-turnover 2026-08-31   # a specific date, if in the 72h window

Free, no API key, no registration, no login, on every source used. Sources:
    ESMA FIRDS   https://registers.esma.europa.eu/solr/esma_registers_firds/select
    Spotlight    https://spotlightstockmarket.com/Umbraco/api/companyapi/GetCompanies
    NGM pre-trade  https://mdapi.ngm.se/delayed/pre-trade   (free, non-commercial: tickers, bid/ask)
    NGM post-trade https://mdapi.ngm.se/delayed/post-trade  (free, non-commercial: executed trades,
                    MiFIR post-trade transparency - turnover and last price, see ngm_turnover())
    MFN          https://mfn.se/all/s.json
    ESEF index   https://filings.xbrl.org/api/filings

robots.txt checked 2026-08-31: spotlightstockmarket.com allows all with
Crawl-delay: 10 (honoured below); www.ngm.se disallows only /sample-* and
HubSpot preview paths. Nothing here is behind a paywall or a login. NGM's own
wording on the delayed-data feed is "Icke-kommersiell anvandning av data ar
kostnadsfri" - non-commercial use is free, anything else may be chargeable.
Spotlight's *licensed* market data is a paid product (their Market Data Fee
Schedule, distributed via Nasdaq); only the public website feeds used here are
free, and they are delayed and best-effort.
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

# Sibling scripts are importable helpers, not subprocesses - same pattern as
# corporate_actions.py and portfolio_review.py. Import defensively: a broken
# or absent sibling must degrade this script to a clear error, never crash it.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import mfn_news
except Exception:                                        # pragma: no cover
    mfn_news = None


def _to_number(raw):
    """mfn_news.to_number(), guarded. The one number parser in this toolkit -
    never a second parser - but a sibling import can fail or raise, and
    SystemExit does not inherit from Exception, so both are caught here."""
    if mfn_news is None:
        return None
    try:
        return mfn_news.to_number(raw)
    except (Exception, SystemExit):
        return None


# Several of these hosts reject or silently hang on the default Python-urllib
# User-Agent (Nasdaq's Nordic API is the known offender in this toolkit).
# A browser-shaped UA is the cheapest way to stay unblocked everywhere.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

FIRDS = "https://registers.esma.europa.eu/solr/esma_registers_firds/select"
SPOTLIGHT_COMPANIES = ("https://spotlightstockmarket.com"
                       "/Umbraco/api/companyapi/GetCompanies")
SPOTLIGHT_NEWS = ("https://spotlightstockmarket.com"
                  "/Umbraco/api/Newsapi/GetNews?lang=1033")
NGM_MDAPI = "https://mdapi.ngm.se"
MFN_SEARCH = "https://mfn.se/all/s.json"
FILINGS_API = "https://filings.xbrl.org/api/filings"

CACHE = os.path.join(tempfile.gettempdir(), "se-venues-cache")
TTL = 6 * 3600          # venue membership changes on listing days, not hourly
SPOTLIGHT_DELAY = 10    # their robots.txt asks for Crawl-delay: 10

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


# --------------------------------------------------------------------------
# The venue table. `esef` is the whole point of this file: it is a property of
# the venue's legal status (regulated market vs MTF), not of the issuer's size
# or its willingness to disclose.
# --------------------------------------------------------------------------
class Segment(object):
    def __init__(self, mic, label, operator, regulated, notes):
        self.mic = mic
        self.label = label
        self.operator = operator
        self.regulated = regulated       # True = regulated market -> ESEF applies
        self.notes = notes

    @property
    def esef(self):
        return self.regulated


SEGMENTS = {
    "XSAT": Segment(
        "XSAT", "Spotlight Stock Market", "Spotlight Stock Market AB", False,
        "MTF. Sweden's smallest-cap venue; ~135 issuers. Runs on Nasdaq INET, "
        "which is why its licensed market data is sold through Nasdaq."),
    "XNGM": Segment(
        "XNGM", "NGM Equity", "Nordic Growth Market NGM AB", True,
        "REGULATED MARKET, not an MTF. Part of Boerse Stuttgart Group. Small "
        "(~14 equities) but subject to the full Transparency Directive."),
    "NSME": Segment(
        "NSME", "Nordic SME", "Nordic Growth Market NGM AB", False,
        "MTF. NGM's growth segment, formerly Nordic MTF (MIC NMTF, still seen "
        "on older FI filings and in NGM's own trade reports)."),
    "SSME": Segment(
        "SSME", "Nasdaq First North Growth Market Sweden", "Nasdaq Stockholm AB", False,
        "MTF. FIRDS reports it under MIC SSME; MFN and Nasdaq label the same "
        "venue FNSE. Both refer to First North Sweden."),
    "XSTO": Segment(
        "XSTO", "Nasdaq Stockholm (main market)", "Nasdaq Stockholm AB", True,
        "REGULATED MARKET. Already covered by nordic_shares.py and "
        "esef_fundamentals.py - this script is not needed for it."),
}

VENUES = {
    "spotlight":   ["XSAT"],
    "ngm":         ["XNGM", "NSME"],
    "ngm-equity":  ["XNGM"],
    "nordic-sme":  ["NSME"],
    "first-north": ["SSME"],
    "stockholm":   ["XSTO"],
}

# Search order when no --venue is given. Every Swedish equity venue, so that
# "not found" really means not listed in Sweden rather than not looked for.
ALL_MICS = ["XSAT", "XNGM", "NSME", "SSME", "XSTO"]

# CFI codes all start with E for equity. EY is "structured instruments", which
# on XNGM means SEB's certificates - securities, but not listed companies, so
# they are excluded from an issuer universe and counted separately instead.
STRUCTURED_CFI = "EY"

# NGM's post-trade feed reports THREE venues under one MIC-like "Venue of
# execution" field: XNGM, NSME, and NMTF. NMTF is NGM's ETP and warrant
# segment (Nordic MTF's trade-reporting label) - securities, but not the
# listed equity universe this script otherwise covers - so it is excluded
# from ngm_turnover() the same way EY structured products are excluded above.
NGM_EQUITY_VENUES = ("XNGM", "NSME")

# Observed file size is ~2.7 MB; this caps a single read well above that so a
# malformed or unexpectedly large response cannot be read into memory whole.
NGM_POSTTRADE_MAX_BYTES = 16 * 1024 * 1024

MTF_CAUTION = """\
MTF CAUTION - this issuer is NOT on a regulated market
  * No ESEF. There is no machine-readable annual report and there never will
    be one. An empty esef_fundamentals.py result is expected, not a red flag.
  * Lighter disclosure. No Transparency Directive half-year requirement in the
    same form, no mandatory IFRS (K3 is common), and prospectus obligations
    are triggered less often. Accounting policies are frequently not
    comparable year on year, let alone across peers.
  * Thinner liquidity. Spreads of several percent and days with no trade at
    all are normal. A quoted last price may be stale, and market cap computed
    from it can be meaningless. Position sizing, not valuation, is usually the
    binding constraint.
  * Higher governance risk. Certified adviser / mentor supervision replaces
    exchange listing supervision. Related-party transactions, repeated
    discounted rights issues and going-concern qualifications are materially
    more common here than on the main market. Read the auditor's report before
    the income statement."""


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------
_last_spotlight_call = [0.0]


def _cache_path(key):
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)[:120]
    return os.path.join(CACHE, safe)


def fetch(url, cache_key=None, ttl=TTL, timeout=90, binary=False):
    """GET with an on-disk cache. Returns bytes, or None if the fetch failed.

    Callers must handle None rather than crash: a single dead source should
    degrade one section of the report, not the whole run.
    """
    path = _cache_path(cache_key) if cache_key else None
    if path and os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        with open(path, "rb") as fh:
            return fh.read()

    # Spotlight's robots.txt asks for a 10 second crawl delay. Honour it.
    if "spotlightstockmarket.com" in url:
        wait = SPOTLIGHT_DELAY - (time.time() - _last_spotlight_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_spotlight_call[0] = time.time()

    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError,
            TimeoutError, OSError):
        return None

    if path:
        try:
            os.makedirs(CACHE, exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(body)
        except OSError:
            pass                                  # cache is an optimisation only
    return body


def fetch_json(url, cache_key=None, ttl=TTL, timeout=90):
    body = fetch(url, cache_key, ttl, timeout)
    if body is None:
        return None
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return None


def normalise(name):
    """Strip the noise that stops two spellings of one company from matching.

    FIRDS says "Kopparbergs B", Spotlight says "Kopparbergs Bryggeri AB", MFN
    says "Kopparbergs Bryggeri". Share-class suffixes and the corporate form
    carry no identity, so they go.
    """
    text = (name or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"\(publ\)|\bpubl\b", " ", text)
    text = re.sub(r"\bser\.?\b|\bserie\b|\bclass\b", " ", text)
    text = re.sub(r"\b(ab|abp|asa|oyj|plc|inc|holding|holdings|group|"
                  r"aktiebolag|shrs|sh)\b", " ", text)
    # A trailing lone share-class letter ("Kopparbergs B") is not identity.
    text = re.sub(r"[^a-z0-9åäö ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+[abcd]$", "", text)
    return text


# --------------------------------------------------------------------------
# ESMA FIRDS - the authoritative venue membership source
# --------------------------------------------------------------------------
def firds_instruments(mic):
    """Every live equity instrument reported to FIRDS under one MIC.

    latest_received_flag:1 keeps only the current version of each record -
    FIRDS keeps a full amendment history and without this filter a single
    share appears a dozen times. -status:TERM drops delisted instruments.
    """
    query = [
        ("q", "*:*"),
        ("fq", "mic:%s" % mic),
        ("fq", "gnr_cfi_code:E*"),
        ("fq", "-status:TERM"),
        ("fq", "latest_received_flag:1"),
        ("rows", "2000"),
        ("wt", "json"),
        ("sort", "publication_date desc"),
        ("fl", "isin,mic,gnr_full_name,gnr_short_name,gnr_cfi_code,lei,"
               "status,mrkt_trdng_start_date"),
    ]
    url = FIRDS + "?" + urllib.parse.urlencode(query)
    data = fetch_json(url, cache_key="firds-%s" % mic)
    if not data:
        return None

    resp = data.get("response", {})
    if resp.get("numFound", 0) > len(resp.get("docs", [])):
        sys.stderr.write("WARNING: FIRDS returned %d of %d instruments for %s; "
                         "the universe is truncated and a 'not listed' answer "
                         "is not reliable.\n"
                         % (len(resp.get("docs", [])), resp["numFound"], mic))

    seen, out, structured = set(), [], 0
    for doc in resp.get("docs", []):
        cfi = doc.get("gnr_cfi_code") or ""
        if cfi.startswith(STRUCTURED_CFI):
            structured += 1
            continue
        isin = doc.get("isin")
        if not isin or isin in seen:
            continue
        seen.add(isin)
        out.append({
            "isin": isin,
            "mic": doc.get("mic"),
            "name": doc.get("gnr_full_name") or "",
            "firds_short_name": doc.get("gnr_short_name") or "",
            "cfi": cfi,
            "lei": doc.get("lei") or "",
            "trading_since": (doc.get("mrkt_trdng_start_date") or "")[:10],
        })
    out.sort(key=lambda r: r["name"].lower())
    return {"instruments": out, "structured_excluded": structured}


def load_universes(mics):
    """Fetch several MICs, returning {mic: result} and a list of failures."""
    universes, failed = {}, []
    for mic in mics:
        result = firds_instruments(mic)
        if result is None:
            failed.append(mic)
        else:
            universes[mic] = result
    return universes, failed


# --------------------------------------------------------------------------
# Venue-native extras
# --------------------------------------------------------------------------
def spotlight_companies():
    """Spotlight's own company list: name, sector, market cap. No ISIN.

    Spotlight publishes no ISIN or ticker anywhere on its public site - not on
    the company pages, not on the INET List page - so this is a supplement to
    FIRDS, never a replacement. The market cap is Spotlight's own figure.
    """
    data = fetch_json(SPOTLIGHT_COMPANIES, cache_key="spotlight-companies")
    if not data:
        return None
    out = {}
    for row in data.get("results") or []:
        name = row.get("heading") or ""
        if not name:
            continue
        instrument = ""
        match = re.search(r"InstrumentId=([A-Za-z0-9]+)", row.get("url") or "")
        if match:
            instrument = match.group(1)
        # Extract currency and numeric value from mcap field like "192 266 123 DKK"
        mcap_raw = row.get("mcap") or ""
        mcap_currency = None
        mcap_value = None
        if mcap_raw:
            # Extract currency suffix (last token that is 3 letters)
            parts = mcap_raw.strip().split()
            if parts and len(parts[-1]) == 3 and parts[-1].isalpha():
                mcap_currency = parts[-1]
                mcap_raw = " ".join(parts[:-1])
            # Remove decimal separators and whitespace, keep only digits
            mcap_value = re.sub(r"[^0-9]", "", mcap_raw) or None
        out[normalise(name)] = {
            "name": name,
            "sector": row.get("industry") or "",
            "mcap": mcap_value,
            "mcap_currency": mcap_currency,
            "instrument_id": instrument,
            "segment_flag": (row.get("notice") or {}).get("text") or "",
        }
    return out


def ngm_symbols():
    """ISIN -> exchange ticker, from NGM's free delayed pre-trade snapshot.

    This is the only free bulk source of real NGM tickers anywhere: FIRDS
    carries only its own short name ("KOPPARBERG/SH B"), not the symbol you
    would type into a broker ("KOBR B"). The snapshot is ~4 MB and covers all
    68k NGM instruments including ETPs, so it is cached hard.

    NGM state that non-commercial use of this feed is free of charge; any
    other use may be chargeable. Data is delayed 15 minutes and retained 72
    hours, so outside those 72 hours this returns nothing.

    This is bid/ask pre-trade data only - it carries no executed price and no
    turnover. For those, see ngm_turnover(), which reads the sibling
    delayed/post-trade feed instead.
    """
    stamps = fetch_json(NGM_MDAPI + "/delayed/pre-trade",
                        cache_key="ngm-pretrade-index", ttl=1800)
    if not stamps:
        return None
    body = fetch(NGM_MDAPI + "/delayed/pre-trade/" + stamps[0],
                 cache_key="ngm-pretrade-snapshot", ttl=TTL, timeout=180)
    if body is None:
        return None
    out = {}
    reader = csv.DictReader(io.StringIO(body.decode("utf-8", "replace")))
    for row in reader:
        isin = (row.get("ISIN") or "").strip()
        symbol = (row.get("Symbol") or "").strip()
        if isin and symbol:
            out[isin] = symbol
    return out


# --------------------------------------------------------------------------
# NGM post-trade - MiFIR transparency, the actual price/turnover source
# --------------------------------------------------------------------------
def _utc_today():
    return time.strftime("%Y-%m-%d", time.gmtime())


def ngm_trading_days():
    """Dates with an NGM post-trade file available right now, newest first.

    Retention is 72 hours, so this is normally 3-4 dates. The newest entry
    may be today's UTC calendar date, whose file is still growing while the
    market trades - see ngm_turnover()'s date=None handling, which treats
    that entry as not-yet-complete rather than using it silently.
    """
    dates = fetch_json(NGM_MDAPI + "/delayed/post-trade",
                       cache_key="ngm-posttrade-index", ttl=1800)
    if not dates:
        return None
    return list(dates)


def _completed_trading_day(dates):
    """The newest date in `dates` whose file is guaranteed final.

    NGM's post-trade file for the current UTC calendar date accumulates
    trades all session, so it is never picked as "the last session" on its
    own - only the entry before it is. If the newest entry is not today (the
    feed queried outside trading hours, or on a non-trading day) it is
    already final and used directly.
    """
    if not dates:
        return None
    if dates[0] == _utc_today():
        return dates[1] if len(dates) > 1 else None
    return dates[0]


def _ngm_posttrade_csv(date):
    """One day's raw NGM post-trade CSV, capped and cached per date.

    A completed day's file never changes once written, so it gets the same
    long TTL as the pre-trade snapshot; today's (still-growing) file gets a
    short one so a same-day re-run picks up new trades. The read is capped at
    NGM_POSTTRADE_MAX_BYTES rather than an unbounded resp.read(), so an
    oversized or runaway response cannot be pulled into memory whole.
    """
    key = "ngm-posttrade-%s" % date
    path = _cache_path(key)
    ttl = 1800 if date == _utc_today() else TTL * 8
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        with open(path, "rb") as fh:
            return fh.read()

    url = NGM_MDAPI + "/delayed/post-trade/" + date
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = resp.read(NGM_POSTTRADE_MAX_BYTES + 1)
    except (urllib.error.HTTPError, urllib.error.URLError,
            TimeoutError, OSError):
        return None

    if len(body) > NGM_POSTTRADE_MAX_BYTES:
        sys.stderr.write("WARNING: NGM post-trade file for %s exceeded the "
                         "%d MB cap - refusing to parse a possibly-truncated "
                         "read.\n" % (date, NGM_POSTTRADE_MAX_BYTES // (1024 * 1024)))
        return None

    try:
        os.makedirs(CACHE, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(body)
    except OSError:
        pass                                          # cache is an optimisation only
    return body


def _parse_ngm_posttrade(body):
    """Parse one day's NGM post-trade CSV into per-ISIN turnover.

    Returns (rows, malformed_count). `rows` is {isin: {"isin", "mic",
    "currency", "turnover", "last_price", "last_trade_utc", "trades"}}.

    NMTF is dropped here, in the parser, rather than left to the caller to
    remember - it is NGM's ETP and warrant segment, not the listed equity
    universe this script covers (see NGM_EQUITY_VENUES above). Every number
    goes through the toolkit's one number parser, mfn_news.to_number - never
    a second, ad hoc one - and currency is read from "Price currency" on
    every row rather than assumed, since assuming SEK for a DKK/NOK issuer
    was a live bug elsewhere in this file. A row missing a required field, or
    whose ISIN reappears under a different venue/currency than its first
    sighting (which should never legitimately happen), is skipped and
    counted as malformed rather than allowed to crash the whole parse.
    """
    text = body.decode("utf-8", "replace")
    reader = csv.DictReader(io.StringIO(text))
    out = {}
    malformed = 0
    for row in reader:
        try:
            venue = (row.get("Venue of execution") or "").strip()
            if venue not in NGM_EQUITY_VENUES:
                continue                    # NMTF (ETPs/warrants) excluded
            isin = (row.get("Instrument identification code") or "").strip()
            ts = (row.get("Trading date and Time") or "").strip()
            currency = (row.get("Price currency") or "").strip()
            price = _to_number(row.get("Price"))
            qty = _to_number(row.get("Quantity"))
            if not isin or not ts or not currency or price is None or qty is None:
                malformed += 1
                continue
        except Exception:
            malformed += 1
            continue

        entry = out.get(isin)
        if entry is None:
            entry = out[isin] = {"isin": isin, "mic": venue,
                                 "currency": currency, "turnover": 0.0,
                                 "last_price": None, "last_trade_utc": None,
                                 "trades": 0}
        elif entry["mic"] != venue or entry["currency"] != currency:
            # Same ISIN reported under a different venue or currency within
            # one day's file - should not happen; treat as malformed rather
            # than silently mixing two different instruments' figures.
            malformed += 1
            continue

        entry["turnover"] += price * qty
        entry["trades"] += 1
        # ISO-8601 timestamps with a fixed-width fractional second sort
        # lexicographically in trade order, so a plain string comparison
        # finds the latest trade without parsing every timestamp.
        if entry["last_trade_utc"] is None or ts > entry["last_trade_utc"]:
            entry["last_trade_utc"] = ts
            entry["last_price"] = price

    return out, malformed


def ngm_turnover(date=None):
    """Per-ISIN last-session turnover and last-trade price for NGM Equity
    (XNGM) and Nordic SME (NSME), from NGM's free delayed/post-trade MiFIR
    transparency feed - a sibling of ngm_symbols()'s delayed/pre-trade
    snapshot, not the same endpoint, and the only free source in this
    toolkit for real turnover or a real last-trade price on these two
    segments. NGM state that non-commercial use of this feed is free of
    charge; any other use may be chargeable.

    Retention is 72 hours: this supplies LAST-SESSION figures and nothing
    more. It cannot build a history series, and calling it repeatedly for
    the same completed date is wasted network traffic - the result is cached
    per date because a completed day's file never changes.

    date=None resolves to the most recent COMPLETED day (see
    _completed_trading_day): NGM's file for the current UTC calendar date is
    still accumulating trades while the market is open, so it is never used
    as "the last session" unless a caller asks for it by name. Pass that
    date explicitly to see it anyway; the result comes back with
    meta["partial"] = True so nobody mistakes a half day for a full one.

    NMTF (NGM's ETP and warrant segment) is excluded - see NGM_EQUITY_VENUES.

    Returns {isin: {"isin", "mic", "currency", "turnover", "last_price",
    "last_trade_utc", "trades"}, "_meta": {...}}. "_meta" is a reserved key,
    never a valid ISIN (ISINs are 12 characters, 2 letters then 10
    alphanumerics), holding {"date", "partial", "malformed_rows_skipped",
    "segments_included", "segments_excluded"}.

    Returns None - never raises - if the feed cannot be reached, or if
    date=None and no completed day exists in the current 72-hour window.
    """
    if date is None:
        days = ngm_trading_days()
        if not days:
            return None
        date = _completed_trading_day(days)
        if date is None:
            return None
        partial = False
    else:
        partial = (date == _utc_today())

    body = _ngm_posttrade_csv(date)
    if body is None:
        return None

    out, malformed = _parse_ngm_posttrade(body)
    out["_meta"] = {
        "date": date,
        "partial": partial,
        "malformed_rows_skipped": malformed,
        "segments_included": list(NGM_EQUITY_VENUES),
        "segments_excluded": ["NMTF"],
    }
    return out


# --------------------------------------------------------------------------
# MFN - identity enrichment, accepted only on an ISIN/LEI match
# --------------------------------------------------------------------------
# MFN aggregates the Swedish wires rather than competing with them, so the
# "MFN or Cision?" question has an answer that surprises people: use MFN for
# everything, because Cision releases arrive there too. The source code on
# each item says which wire the issuer actually pays.
DISTRIBUTORS = {"cis": "Cision", "mfn": "MFN (own wire)", "beq": "beQuoted",
                "ngn": "NG News"}


def mfn_identity(name, isin=None, lei=None, limit=30):
    """Resolve a company to its MFN entity, or return None.

    The validation matters. A bare name search on MFN is actively misleading:
    "Spiltan Invest" returns the entity nordnet (XSTO:SAVE) because Nordnet
    press releases mention it, and "Bluelake Mineral" returns mangold-insight,
    a research house. Only an entity whose own ISIN or LEI matches the one
    FIRDS gave us is accepted; anything else is reported as not found.
    """
    url = MFN_SEARCH + "?" + urllib.parse.urlencode({"query": name,
                                                     "limit": limit})
    data = fetch_json(url, cache_key="mfn-%s" % normalise(name), ttl=TTL)
    if not data:
        return None

    best, wires = None, set()
    for item in data.get("items") or []:
        entities = [item.get("author")] + (item.get("subjects") or [])
        for entity in entities:
            if not entity:
                continue
            isins = entity.get("isins") or []
            leis = entity.get("leis") or []
            if isin and isin in isins:
                matched = True
            elif lei and lei in leis:
                matched = True
            elif not isin and not lei and normalise(entity.get("name")) == normalise(name):
                matched = True                    # unanchored: exact name only
            else:
                matched = False
            if matched:
                best = entity
                if entity is item.get("author"):
                    wires.add(item.get("source") or "")
    if not best:
        return None

    tickers = {}
    for raw in best.get("tickers") or []:
        if ":" in raw:
            mic, symbol = raw.split(":", 1)
            tickers.setdefault(mic, symbol)
    orgnr = ""
    for ref in best.get("local_refs") or []:
        if ref.startswith("SE:"):
            orgnr = ref[3:]
    return {
        "name": best.get("name") or "",
        "slug": best.get("slug") or "",
        "isins": best.get("isins") or [],
        "leis": best.get("leis") or [],
        "orgnr": orgnr,
        "tickers": tickers,
        "wires": sorted(w for w in wires if w),
    }


def wire_for(row, query=None):
    """MFN identity for a FIRDS row, trying the spellings MFN indexes under.

    MFN's search is literal enough that the FIRDS instrument name often misses:
    "Kopparbergs B" and "Glycorex Transplantation B" both return nothing useful
    because of the share-class suffix, while "Kopparbergs" and "Glycorex" hit
    immediately. Every candidate is still validated against this row's ISIN and
    LEI, so widening the query cannot widen the risk of a wrong match.
    """
    base = re.sub(r"\s+(ser\.?\s*)?[A-D]$", "", row["name"]).strip()
    candidates = []
    for cand in (query, row["name"], base, base.split()[0] if base else ""):
        if cand and cand not in candidates:
            candidates.append(cand)
    for cand in candidates:
        hit = mfn_identity(cand, row["isin"], row["lei"])
        if hit:
            return hit
    return None


# --------------------------------------------------------------------------
# ESEF - the question --check exists to answer
# --------------------------------------------------------------------------
def esef_filings(lei):
    """Annual report periods filed in ESEF for this LEI, from filings.xbrl.org.

    Returns None if the index could not be reached - which must be reported as
    unknown, not as "no ESEF". The difference decides whether the analyst goes
    looking for a machine-readable report or stops.
    """
    if not lei:
        return None
    flt = json.dumps([{"name": "entity.identifier", "op": "eq", "val": lei}])
    url = FILINGS_API + "?" + urllib.parse.urlencode({"filter": flt,
                                                      "page[size]": 50})
    data = fetch_json(url, cache_key="esef-%s" % lei, ttl=TTL)
    if data is None:
        return None
    periods = []
    for row in data.get("data") or []:
        end = (row.get("attributes") or {}).get("period_end")
        if end:
            periods.append(end)
    return sorted(set(periods), reverse=True)


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------
def _contains_word(haystack, needle):
    """Substring match that has to start on a word boundary.

    A plain `in` test is useless on company names: "be" (BE Group) and "berg"
    (Bergman & Beving) are both substrings of "kopparbergs", so searching for
    Kopparbergs returned three unrelated issuers. Anchoring to a word start
    and demanding some length kills those without losing real prefix matches.
    """
    if len(needle) < 4:
        return False
    return re.search(r"(^|\s)" + re.escape(needle), haystack) is not None


def score(query, candidate):
    """Higher is better; 0 means no match at all."""
    q, c = normalise(query), normalise(candidate)
    if not q or not c:
        return 0
    if q == c:
        return 100
    if len(q) >= 3 and c.startswith(q):
        return 80 - min(len(c) - len(q), 30)
    if _contains_word(c, q):
        return 50 - min(len(c) - len(q), 30)
    if _contains_word(q, c):
        return 40
    return 0


def resolve(query, mics=None):
    """Find a company across the Swedish venues. Returns (matches, failed_mics).

    Two passes, because legal names and trading names diverge. The FIRDS pass
    matches the reported instrument name. If that finds nothing, the MFN pass
    turns a colloquial name into an ISIN and looks that ISIN up in FIRDS, so
    the venue answer still comes from FIRDS and never from the wire.
    """
    mics = mics or list(ALL_MICS)
    universes, failed = load_universes(mics)

    matches = []
    for mic, universe in universes.items():
        for row in universe["instruments"]:
            points = max(score(query, row["name"]),
                         score(query, row["firds_short_name"].split("/")[0]))
            if points:
                matches.append((points, row))

    if not matches:
        wire = mfn_identity(query)                # unanchored fallback
        if wire:
            wanted = set(wire["isins"])
            for mic, universe in universes.items():
                for row in universe["instruments"]:
                    if row["isin"] in wanted:
                        matches.append((60, row))

    matches.sort(key=lambda pair: (-pair[0], pair[1]["name"].lower()))
    return [row for _, row in matches], failed


def group_by_issuer(rows):
    """Collapse share classes. LEI identifies the issuer; ISIN identifies a
    class, and an issuer with A and B shares is still one routing decision."""
    groups = {}
    for row in rows:
        key = row["lei"] or row["isin"]
        groups.setdefault(key, []).append(row)
    return groups


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
# Whether FI's short register actually returns anything is not the same
# question as whether it legally covers the venue, and the two answers differ
# by venue. Measured 2026-08-31 against FI's full history file (back to 2010):
# 39 of the 342 First North Sweden issuers appear in it, and ZERO Spotlight,
# NGM Equity or Nordic SME issuers ever have.
SHORT_REGISTER_EMPTY = ("XSAT", "XNGM", "NSME")


def register_notes(mic):
    lines = [
        "FI insider register (marknadssok.fi.se) - COVERS THIS VENUE.",
        "  MAR Art. 19 applies to every trading venue including MTFs. "
        "Verified 2026-08-31 with real hits for Absolicon (Spotlight) and "
        "Kopparbergs (Nordic SME). Use insider_se.py --issuer.",
    ]
    if mic in SHORT_REGISTER_EMPTY:
        lines += [
            "FI short-selling register (fi.se/blankningsregistret) - IN SCOPE "
            "IN LAW, EMPTY IN PRACTICE.",
            "  SSR covers shares admitted on any EU trading venue, but no "
            "Spotlight, NGM Equity or Nordic SME issuer appears in FI's "
            "current file or in the full history back to 2010 (measured "
            "2026-08-31). There is no stock borrow in these names. Absence of "
            "short interest here is NOT a bullish signal - it is an absence "
            "of data, and short_se.py will correctly return nothing.",
        ]
    else:
        lines += [
            "FI short-selling register (fi.se/blankningsregistret) - COVERS "
            "THIS VENUE AND RETURNS REAL DATA.",
            "  39 First North Sweden issuers appear in FI's history file "
            "(measured 2026-08-31), so short_se.py is worth running here. A "
            "nil result still means no DISCLOSED position >= 0.1%, not no "
            "shorting.",
        ]
    return lines


def source_chain(segment, esef_periods, wire):
    """The ordered list of places this issuer's numbers actually come from."""
    lines = []
    if segment.esef:
        if esef_periods is None:
            lines.append("1. ESEF - EXPECTED (regulated market) but the index "
                         "could not be reached. Verify before concluding.")
        elif esef_periods:
            lines.append("1. ESEF - AVAILABLE, %d filing(s), latest FY %s. "
                         "Use esef_fundamentals.py."
                         % (len(esef_periods), esef_periods[0]))
        else:
            lines.append("1. ESEF - EXPECTED on a regulated market but NONE "
                         "FOUND. Treat as a genuine gap and investigate: "
                         "recent listing, exemption, or a late filer.")
    else:
        lines.append("1. ESEF - DOES NOT APPLY. MTF issuer; no XBRL annual "
                     "report exists. Do not report this as missing data.")

    if wire and wire["slug"]:
        wires = ", ".join(DISTRIBUTORS.get(w, w) for w in wire["wires"]) or "unknown"
        lines.append("2. Reports and regulatory news - mfn_news.py --slug %s "
                     "(issuer's own wire: %s; MFN relays all of them, so MFN "
                     "is the single entry point)." % (wire["slug"], wires))
    else:
        lines.append("2. Reports and regulatory news - MFN entity NOT "
                     "CONFIRMED. DATA NOT AVAILABLE. Do not guess a slug; "
                     "fall back to the issuer's own IR page.")

    if wire and "cis" in (wire.get("wires") or []):
        lines.append("3. Cision newsroom also carries this issuer - "
                     "cision_news.py is a valid cross-check.")

    if segment.mic in NGM_EQUITY_VENUES:
        lines.append("%d. Last-session turnover and last-trade price - "
                     "venues_se.py --ngm-turnover (NGM's free delayed/"
                     "post-trade MiFIR transparency feed; last session only, "
                     "72h retention, never a history series)."
                     % (len(lines) + 1))

    tail = ("On an MTF this is the ONLY full-precision source."
            if not segment.regulated else
            "Still the primary record; ESEF is a rendering of it, not a "
            "replacement.")
    lines.append("%d. Financial statements themselves - PDF interim and annual "
                 "reports from the issuer's IR page or the MFN attachment. %s"
                 % (len(lines) + 1, tail))
    return lines


def print_company(query, rows, failed, args, searched=None):
    if not rows:
        print("DATA NOT AVAILABLE: no Swedish listed instrument matches %r."
              % query)
        print()
        print("Checked ESMA FIRDS for MICs: %s."
              % ", ".join("%s (%s)" % (m, SEGMENTS[m].label)
                          for m in (searched or ALL_MICS)))
        if searched and len(searched) < len(ALL_MICS):
            print("Only --venue %s was searched. Drop --venue to search every "
                  "Swedish venue." % args.venue)
        if failed:
            print("WARNING: %s could not be queried, so this is not a clean "
                  "negative." % ", ".join(failed))
        print("The company may be unlisted, delisted, or listed outside "
              "Sweden. This script does not guess.")
        return

    groups = group_by_issuer(rows)
    if len(groups) > 1 and not args.all:
        print("%d issuers match %r. Showing the closest; pass --all for the "
              "rest." % (len(groups), query))
        print()
        first = list(groups)[0]
        groups = {first: groups[first]}

    for key, classes in groups.items():
        primary = classes[0]
        mics_here = sorted({c["mic"] for c in classes})
        segment = SEGMENTS[primary["mic"]]

        print("=" * 74)
        print(primary["name"])
        print("=" * 74)
        print()
        print("VENUE")
        for mic in mics_here:
            seg = SEGMENTS[mic]
            print("  %-6s %-46s %s" % (
                mic, seg.label,
                "REGULATED MARKET" if seg.regulated else "MTF"))
            print("         operator: %s" % seg.operator)
        print("  %s" % segment.notes)
        print()

        print("IDENTIFIERS")
        print("  LEI          %s" % (primary["lei"] or "DATA NOT AVAILABLE"))
        for cls in classes:
            print("  ISIN         %-14s %-34s [%s]"
                  % (cls["isin"], cls["name"], cls["cfi"]))
            print("               FIRDS short name: %s"
                  % (cls["firds_short_name"] or "DATA NOT AVAILABLE"))

        wire = wire_for(primary, query)
        if wire:
            print("  org.nr       %s" % (wire["orgnr"] or "DATA NOT AVAILABLE"))
            if wire["tickers"]:
                for mic, symbol in sorted(wire["tickers"].items()):
                    tag = ""
                    if mic in mics_here or (mic == "FNSE" and "SSME" in mics_here):
                        tag = "  <- home venue"
                    print("  ticker       %-6s %s%s" % (mic, symbol, tag))
            else:
                print("  ticker       DATA NOT AVAILABLE")
            print("  MFN slug     %s" % wire["slug"])
        else:
            print("  org.nr       DATA NOT AVAILABLE (no ISIN/LEI-verified "
                  "MFN entity)")
            print("  ticker       DATA NOT AVAILABLE")

        if args.symbols and any(m in ("XNGM", "NSME") for m in mics_here):
            table = ngm_symbols()
            if table:
                for cls in classes:
                    if cls["isin"] in table:
                        print("  NGM symbol   %s (mdapi delayed snapshot)"
                              % table[cls["isin"]])
            else:
                print("  NGM symbol   DATA NOT AVAILABLE (mdapi unreachable)")

        print()
        periods = esef_filings(primary["lei"])
        print("SOURCE CHAIN")
        for line in source_chain(segment, periods, wire):
            print("  " + line)
        print()

        print("REGULATORY REGISTERS")
        for line in register_notes(primary["mic"]):
            print("  " + line)
        if not segment.regulated:
            print()
            print(MTF_CAUTION)
        print()


def print_check(query, rows, failed, args, searched=None):
    """One question: does ESEF exist, and if not where do the numbers come from?"""
    if not rows:
        print("DATA NOT AVAILABLE: %r is not a listed Swedish instrument in "
              "ESMA FIRDS (%s), so the ESEF question cannot be answered."
              % (query, ", ".join(searched or ALL_MICS)))
        if failed:
            print("WARNING: %s could not be queried." % ", ".join(failed))
        return

    primary = rows[0]
    segment = SEGMENTS[primary["mic"]]
    periods = esef_filings(primary["lei"])

    print("%s  [%s / %s]" % (primary["name"], primary["mic"], segment.label))
    print("ISIN %s   LEI %s" % (primary["isin"],
                                primary["lei"] or "DATA NOT AVAILABLE"))
    print()

    if not segment.esef:
        verdict = "NO - AND THAT IS CORRECT, NOT A GAP"
    elif periods is None:
        verdict = "UNKNOWN - ESEF index unreachable"
    elif periods:
        verdict = "YES - %d filing(s), latest FY %s" % (len(periods), periods[0])
    else:
        verdict = "NO - BUT IT SHOULD EXIST. Investigate."
    print("DOES ESEF EXIST?  %s" % verdict)
    print()

    if segment.esef and periods:
        # An ESEF filing on a regulated market can still be contradicted by
        # reality, so say what was actually observed rather than what is due.
        print("Regulated market, and filings are present. Route to "
              "esef_fundamentals.py first.")
    elif segment.esef:
        print("%s is a REGULATED MARKET, so the Transparency Directive "
              "requires ESEF. Its absence is a real finding - check for a "
              "recent listing or a late filer before assuming the data is "
              "simply elsewhere." % segment.label)
    else:
        print("%s is an MTF. ESEF is not required and none will ever be "
              "filed. Do not record this as missing financials."
              % segment.label)
    print()

    wire = wire_for(primary, query)
    print("WHERE THE FINANCIALS COME FROM")
    for line in source_chain(segment, periods, wire):
        print("  " + line)
    if not segment.regulated:
        print()
        print(MTF_CAUTION)


def print_list(venue, mics, args):
    universes, failed = load_universes(mics)
    if failed:
        print("WARNING: ESMA FIRDS unreachable for %s - that segment is "
              "missing from this list." % ", ".join(failed), file=sys.stderr)
    if not universes:
        raise SystemExit("DATA NOT AVAILABLE: ESMA FIRDS could not be reached.")

    extra_names = spotlight_companies() if "XSAT" in universes else None
    symbols = ngm_symbols() if (args.symbols and
                                any(m in ("XNGM", "NSME") for m in universes)) else None

    total = 0
    for mic in mics:
        if mic not in universes:
            continue
        universe = universes[mic]
        segment = SEGMENTS[mic]
        rows = universe["instruments"]
        total += len(rows)

        print("=" * 100)
        print("%s  [MIC %s]  -  %s" % (
            segment.label, mic,
            "REGULATED MARKET, ESEF APPLIES" if segment.regulated
            else "MTF, NO ESEF"))
        print("%d equity instruments, %d issuers (by LEI)"
              % (len(rows), len(group_by_issuer(rows))))
        if universe["structured_excluded"]:
            print("%d structured products (CFI EY*) excluded - securities, "
                  "not listed companies." % universe["structured_excluded"])
        print("=" * 100)
        spotlight_here = extra_names if mic == "XSAT" else None
        tail = "SECTOR / MCAP (Spotlight)" if spotlight_here else "LEI"
        print("%-14s %-42s %-14s %s" % ("ISIN", "NAME", "TICKER", tail))
        print("-" * 100)
        for row in rows:
            ticker = symbols.get(row["isin"]) if symbols else None
            if not ticker:
                # FIRDS short name is not the exchange ticker. Marked with ~
                # so nobody pastes it into a broker or a data vendor.
                short = row["firds_short_name"]
                ticker = ("~" + short.split("/")[0]) if short else ""

            last = row["lei"] or "n/a"
            if spotlight_here:
                # Sector and market cap are Spotlight's own figures, matched on
                # name because Spotlight publishes no ISIN to join on. An
                # unmatched row is left blank rather than guessed.
                extra = spotlight_here.get(normalise(row["name"]))
                if extra:
                    mcap = extra["mcap"]
                    currency = extra.get("mcap_currency") or "?"
                    last = "%-18s %s" % (
                        extra["sector"][:18],
                        ("%.0f M%s" % (int(mcap) / 1e6, currency)) if mcap else "")
                    if extra["segment_flag"]:
                        last += "  [%s]" % extra["segment_flag"]
                else:
                    last = ""
            print("%-14s %-42s %-14s %s"
                  % (row["isin"], row["name"][:42], ticker[:14], last))
        print()

    if symbols is None and any(m in ("XNGM", "NSME") for m in mics):
        print("Tickers marked ~ are ESMA FIRDS short names, NOT exchange "
              "tickers. Pass --symbols to attach real NGM symbols from "
              "mdapi.ngm.se (free for non-commercial use, ~4 MB, cached).")
    if "XSAT" in universes:
        print("Spotlight publishes no ISIN or ticker anywhere on its public "
              "site, so tickers marked ~ are FIRDS short names. Real Spotlight "
              "tickers are available per company via MFN - run this script "
              "with a company name.")
    if any(not SEGMENTS[m].regulated for m in mics if m in universes):
        print()
        print(MTF_CAUTION)
    print()
    print("%d instruments total. Source: ESMA FIRDS, free and keyless."
          % total)


def build_json(query, rows, args):
    out = []
    for key, classes in group_by_issuer(rows).items():
        primary = classes[0]
        segment = SEGMENTS[primary["mic"]]
        wire = wire_for(primary, query)
        periods = esef_filings(primary["lei"])
        out.append({
            "query": query,
            "name": primary["name"],
            "lei": primary["lei"] or None,
            "venue": {"mic": primary["mic"], "label": segment.label,
                      "operator": segment.operator,
                      "regulated_market": segment.regulated,
                      "esef_applies": segment.esef},
            "instruments": [{"isin": c["isin"], "name": c["name"],
                             "cfi": c["cfi"], "mic": c["mic"],
                             "firds_short_name": c["firds_short_name"]}
                            for c in classes],
            "esef_filings": periods,
            "esef_status": ("not_applicable" if not segment.esef else
                            "unknown" if periods is None else
                            "present" if periods else "missing"),
            "mfn": wire,
            "fi_insider_register": True,
            "fi_short_register": {
                "legally_in_scope": True,
                "observed_positions": primary["mic"] not in SHORT_REGISTER_EMPTY,
                "note": register_notes(primary["mic"])[-1].strip()},
            "source_chain": source_chain(segment, periods, wire),
        })
    return out


def print_ngm_turnover(date, args):
    """--ngm-turnover: last-session (or a named date's) turnover and last
    price for NGM Equity + Nordic SME, from ngm_turnover()."""
    result = ngm_turnover(date)
    if result is None:
        raise SystemExit(
            "DATA NOT AVAILABLE: NGM post-trade feed unreachable, or (if no "
            "date was given) no completed trading day is available in the "
            "current 72-hour retention window.")

    meta = result.pop("_meta")
    if args.json:
        payload = dict(result)
        payload["_meta"] = meta
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print("NGM EQUITY (XNGM) + NORDIC SME (NSME) - LAST-SESSION TURNOVER")
    print("Source: mdapi.ngm.se/delayed/post-trade (MiFIR post-trade "
          "transparency, free for non-commercial use). NOT ngm_symbols()'s "
          "pre-trade bid/ask feed.")
    print("Date: %s%s" % (meta["date"],
                          "  *** TODAY - STILL ACCUMULATING, PARTIAL DAY ***"
                          if meta["partial"] else "  (completed session)"))
    print("Segments included: %s.  Excluded: %s (ETPs/warrants, not listed "
          "equity)." % (", ".join(meta["segments_included"]),
                        ", ".join(meta["segments_excluded"])))
    if meta["malformed_rows_skipped"]:
        print("%d malformed row(s) skipped." % meta["malformed_rows_skipped"])
    print()

    rows = sorted(result.values(), key=lambda r: -r["turnover"])
    if not rows:
        print("No XNGM/NSME trades found for this date.")
        return
    print("%-14s %-4s %16s %14s %5s  %s"
         % ("ISIN", "MIC", "TURNOVER", "LAST PRICE", "CCY", "LAST TRADE (UTC)"))
    print("-" * 84)
    for row in rows:
        print("%-14s %-4s %16.0f %14.3f %5s  %s"
             % (row["isin"], row["mic"], row["turnover"], row["last_price"],
                row["currency"], row["last_trade_utc"]))
    print()
    print("%d ISIN(s) traded, %d trade(s) total."
          % (len(rows), sum(r["trades"] for r in rows)))


def main():
    parser = argparse.ArgumentParser(
        description="Swedish listing venues beyond Nasdaq Stockholm main "
                    "market: Spotlight, NGM Equity, Nordic SME, First North.",
        epilog="Free and keyless throughout. ESEF applies only to regulated "
               "markets - NGM Equity yes, Spotlight / Nordic SME / First "
               "North no.")
    parser.add_argument("company", nargs="?",
                        help="company name to route")
    parser.add_argument("--check", metavar="NAME",
                        help="answer only: does ESEF exist, and if not where "
                             "do the financials come from?")
    parser.add_argument("--venue", choices=sorted(VENUES),
                        help="venue for --list, or to restrict a name lookup")
    parser.add_argument("--list", action="store_true",
                        help="print the issuer universe for --venue")
    parser.add_argument("--symbols", action="store_true",
                        help="attach real NGM tickers from mdapi.ngm.se "
                             "(~4 MB download, cached, non-commercial use)")
    parser.add_argument("--all", action="store_true",
                        help="show every matching issuer, not just the closest")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--news", action="store_true",
                        help="Spotlight's venue-wide news feed (30 most recent "
                             "items; the feed cannot be filtered by company)")
    parser.add_argument("--ngm-turnover", nargs="?", const="", default=None,
                        metavar="DATE", dest="ngm_turnover",
                        help="last-session turnover and last price for NGM "
                             "Equity + Nordic SME (NGM's free delayed/"
                             "post-trade MiFIR transparency feed). Omit DATE "
                             "for the most recent completed day, or give one "
                             "(YYYY-MM-DD) from --ngm-turnover's own output "
                             "if it is still in the 72-hour retention window")
    args = parser.parse_args()

    if args.ngm_turnover is not None:
        print_ngm_turnover(args.ngm_turnover or None, args)
        return

    if args.news:
        data = fetch_json(SPOTLIGHT_NEWS, cache_key="spotlight-news", ttl=600)
        if not data:
            raise SystemExit("DATA NOT AVAILABLE: Spotlight news feed "
                             "unreachable.")
        items = []

        def flatten(node):
            if isinstance(node, list):
                for child in node:
                    flatten(child)
            elif isinstance(node, dict):
                items.append(node)
        flatten(data.get("results") or [])
        print("Spotlight Stock Market - %d most recent items" % len(items))
        print("NOTE: this feed is venue-wide. Spotlight's API ignores "
              "publisher, page and search parameters, so per-company history "
              "is DATA NOT AVAILABLE here - use mfn_news.py for that.")
        print()
        for item in items:
            print("%s  %s" % (item.get("time", "")[:16],
                              (item.get("text") or "")[:100]))
        return

    if args.list:
        if not args.venue:
            raise SystemExit("--list requires --venue "
                             "(spotlight | ngm | first-north | ngm-equity | "
                             "nordic-sme | stockholm)")
        print_list(args.venue, VENUES[args.venue], args)
        return

    query = args.check or args.company
    if not query:
        parser.error("give a company name, or --venue X --list")

    mics = VENUES[args.venue] if args.venue else None
    rows, failed = resolve(query, mics)

    if args.json:
        print(json.dumps(build_json(query, rows, args),
                         ensure_ascii=False, indent=2))
        return

    if args.check:
        print_check(query, rows, failed, args, mics)
    else:
        print_company(query, rows, failed, args, mics)


if __name__ == "__main__":
    main()
