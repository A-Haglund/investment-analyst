#!/usr/bin/env python3
"""Pin down WHICH company before any analysis starts.

Every other script in this toolkit takes an identifier - an LEI, an orderbook
id, an MFN slug, an ISIN - and returns figures. None of them can tell you that
you handed it the wrong company. That failure is silent, and in Swedish equities
it is easy to trigger, because brand names are shared across separately listed
issuers:

    "Volvo"    -> AB Volvo (VOLV A / VOLV B, orgnr 556012-5790) is the truck
                  and construction-equipment group.
                  Volvo Car AB (VOLCAR B, orgnr 556810-8988) is a different
                  listed company with a different owner, different accounts and
                  a different share count.
    "Atlas"    -> Atlas Copco, two listed classes that must both be counted.
    "EVO"      -> Evolution AB: quoted in SEK, reports in EUR.

So this script REFUSES rather than guesses. Where identity is ambiguous it
prints every candidate with its distinguishing identifiers and exits non-zero.
A caller that gets exit 0 has a canonical object it can safely feed downstream.

WHAT EACH FIELD IS SOURCED FROM, and how far it can be trusted:

  ticker / isin / share_classes / currency / market_segment
      Nasdaq Nordic reference data (nordic_shares.py). Authoritative for the
      Nordic listing itself. UNLISTED share classes are invisible here - see
      the warning nordic_shares.py prints.
  organisation_number / lei / mfn_slug
      MFN's issuer record. `local_refs` carries "SE:556012-5790", which is the
      organisationsnummer. This is the best free orgnr<->ISIN<->LEI<->ticker
      mapping available without a paid reference-data feed.
  legal_name
      EU VIES (the tax authority's own register) for Swedish issuers, GLEIF
      otherwise. NOT the trading name - "Aktiebolaget Volvo", not "Volvo".
  exchange (MIC)
      MFN ticker prefix, cross-checked against ESMA FIRDS. FIRDS lists every
      venue that reports the ISIN (80+ MICs for a large cap, mostly systematic
      internalisers), so the primary listing is taken from MFN and FIRDS is
      used only to confirm it.
  fiscal_year_end
      DETECTED, NEVER ASSUMED. Taken from the ESEF filing's period_end. H&M ends
      30 November, Sectra 30 April, Addtech and Lagercrantz 31 March. For
      issuers with no ESEF filing (First North, Spotlight, NGM) it is parsed
      from the year-end report's own period range and flagged as derived.
  reporting_currency
      The currency the accounts are PREPARED in, read from the ESEF filing's
      units. This is not the quote currency: Evolution is quoted in SEK and
      reports in EUR, so a SEK market cap over an EUR profit is a 10x error.

Usage:
    python company_resolve.py "Volvo"            # refuses - two issuers
    python company_resolve.py "AB Volvo"
    python company_resolve.py "Atlas Copco"
    python company_resolve.py EVO --json
    python company_resolve.py --isin SE0012673267
    python company_resolve.py --lei 549300SUH6ZR1RF6TA88
    python company_resolve.py --orgnr 556012-5790
    python company_resolve.py "Kebni" --country SE --json

Importable, for a sibling script that would otherwise shell out to the above
and parse stdout - see the "--- importable API ---" section near the bottom
of this file for the full contract:

    from company_resolve import resolve, resolve_lei, Ambiguous, NotFound
    rec = resolve("AB Volvo")            # dict, or raises Ambiguous/NotFound
    lei = resolve_lei("Volvo Car")       # str or None, or raises

Exit codes:
    0  one issuer resolved
    2  ambiguous - candidates printed, nothing resolved
    3  no listed Nordic issuer matched

Free, keyless. Sources: Nasdaq Nordic, MFN, filings.xbrl.org, GLEIF, EU VIES,
ESMA FIRDS. Expensive lookups are cached under the system temp directory.
"""
import argparse
import calendar
import collections
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
NA = "DATA NOT AVAILABLE"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


nordic = load("nordic_shares")
esef = load("esef_fundamentals")
mfn = load("mfn_news")
cision = load("cision_news")

# The Nasdaq endpoint blocklists the default urllib UA and hangs rather than
# erroring; nordic_shares.py documents this. Reuse a browser-shaped UA here too.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

GLEIF_RECORD = "https://api.gleif.org/api/v1/lei-records/"
VIES = "https://ec.europa.eu/taxation_customs/vies/rest-api/ms/%s/vat/%s"
FIRDS = "https://registers.esma.europa.eu/solr/esma_registers_firds/select"
FILINGS_BASE = "https://filings.xbrl.org"

# Venues where a Nordic issuer is actually LISTED. FIRDS reports every MIC that
# has ever quoted the ISIN - Cboe, Turquoise, dozens of systematic
# internalisers - so a MIC outside this set is a trading venue, not the listing.
NORDIC_MICS = {
    "XSTO": "Nasdaq Stockholm",
    "FNSE": "Nasdaq First North Growth Market Sweden",
    "XSAT": "Spotlight Stock Market",
    "NSME": "NGM Nordic SME",
    "XNGM": "Nordic Growth Market",
    "XHEL": "Nasdaq Helsinki",
    "FNFI": "Nasdaq First North Growth Market Finland",
    "XCSE": "Nasdaq Copenhagen",
    "FNDK": "Nasdaq First North Growth Market Denmark",
    "XICE": "Nasdaq Iceland",
    "FNIS": "Nasdaq First North Growth Market Iceland",
    "XOSL": "Oslo Bors",
    "XOAS": "Euronext Expand Oslo",
    "MERK": "Euronext Growth Oslo",
}

# Nasdaq's own group label, used only when MFN carries no Nordic ticker.
GROUP_MIC = {"Shares Main Market": "XSTO", "Shares First North": "FNSE"}

# FIRDS reports First North lines under Nasdaq's per-country SEGMENT MICs, not
# under the operating MIC that MFN and Nasdaq publish. Verified 2026-08-31:
# Gapwaves (FNSE per MFN) comes back as SSME/DNSE/MNSE, Neovici as SSME/DNSE/
# MNSE with no FNSE at all. Treating that as a contradiction would fire a false
# warning on every First North issuer.
FIRST_NORTH_SEGMENT_MICS = {"SSME", "DNSE", "MNSE", "HNSE", "INSE", "FNSE",
                            "FNDK", "FNFI", "FNIS"}

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
LEI_RE = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")
ORGNR_RE = re.compile(r"^(\d{6})-?(\d{4})$")

# Legal-form tokens. Stripped only when building the "core" name used for the
# brand-ambiguity test, never when comparing a query the user typed in full.
LEGAL_TOKENS = {"ab", "publ", "aktiebolaget", "asa", "as", "a", "s", "oyj",
                "abp", "oy", "plc", "hf", "ehf", "nv", "sa", "se", "ag", "spa",
                "corp", "inc", "ltd", "limited", "holding", "holdings"}
CLASS_TOKENS = {"a", "b", "c", "d", "sdb", "sdr", "pref", "ser"}

MONTHS = {
    "jan": 1, "januari": 1, "january": 1, "feb": 2, "februari": 2, "february": 2,
    "mar": 3, "mars": 3, "march": 3, "apr": 4, "april": 4,
    "maj": 5, "may": 5, "jun": 6, "juni": 6, "june": 6,
    "jul": 7, "juli": 7, "july": 7, "aug": 8, "augusti": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "okt": 10, "oct": 10, "oktober": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
MONTH_RE = re.compile(
    r"(?:(\d{1,2})\s+)?\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) +
    r")\b\.?(?:\s+(\d{1,2}))?", re.I)

WARNINGS = []


def warn(msg):
    if msg not in WARNINGS:
        WARNINGS.append(msg)


# --- caching --------------------------------------------------------------
#
# Reference data changes on the timescale of a corporate action, not a trading
# day, so an aggressive cache costs nothing and turns a nine-request resolution
# into one local read. A PRICE IS NOT REFERENCE DATA (A4): market_cap is
# derived from a live quote and must not sit behind the same 6-hour window as
# an ISIN or a share count. It gets its own short-TTL cache key - see
# nordic_summary_reference() / nordic_market_cap() below, both used from
# share_classes().

TTL_REFERENCE = 21600   # 6h - identifiers, share counts, segments, filings lists
TTL_QUOTE = 900         # 15m - anything derived from a live price

CACHE_DIR = os.path.join(tempfile.gettempdir(), "investment-analyst-cache",
                         "company_resolve")


def cache_path(key):
    return os.path.join(CACHE_DIR, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")


def cached(key, ttl, produce):
    """Run produce() unless a fresh cached value exists. None is not cached -
    a failed source must be retried, not remembered as an absence."""
    path = cache_path(key)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            blob = json.load(fh)
        if time.time() - blob["t"] < ttl:
            return blob["v"]
    except (OSError, ValueError, KeyError):
        pass
    value = produce()
    if value is not None:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"t": time.time(), "v": value}, fh)
        except OSError:
            pass
    return value


def http(url, timeout=45, limit=None):
    """Raw GET. Returns None on any failure - identity resolution must degrade
    field by field, never abort because one register was down."""
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(limit) if limit else r.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None


def http_json(url, ttl=86400, timeout=45):
    def produce():
        raw = http(url, timeout=timeout)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None
    return cached("json:" + url, ttl, produce)


# --- name normalisation ---------------------------------------------------

def norm(text):
    """Comparison form. '&' becomes 'and' so that the query "H&M" and MFN's
    issuer name "H&M" and Nasdaq's "Hennes & Mauritz" all tokenise sanely."""
    s = (text or "").lower().replace("&", " and ")
    s = re.sub(r"[^0-9a-zåäöüøæ]+", " ", s)
    return " ".join(s.split())


def core_name(text):
    """Name with the legal form and the share-class suffix removed.

    'Volvo Car B' and 'Volvo Car AB (publ)' both reduce to 'volvo car'. This is
    the form the brand-ambiguity test runs on: it is what a user means when they
    type a company name, and it is precisely where 'Volvo' collides.
    """
    words = norm(text).split()
    while words and words[-1] in CLASS_TOKENS and len(words) > 1:
        words.pop()
    while words and words[-1] in LEGAL_TOKENS and len(words) > 1:
        words.pop()
    while words and words[0] in ("aktiebolaget", "ab") and len(words) > 1:
        words.pop(0)
    return " ".join(words)


STOPWORDS = {"and", "the", "of", "och", "i", "group", "ab", "publ", "holding",
             "holdings", "bank", "nordic", "sweden", "norden", "international"}


def names_compatible(a_cores, b_cores):
    """Do two spellings plausibly name the same issuer?

    Used to police the symbol-root merge. Sharing a generic word is not enough -
    "Nordic X" and "Nordic Y" are different companies - so the common tokens
    have to be distinctive ones.
    """
    for x in a_cores:
        for y in b_cores:
            if not x or not y:
                continue
            if x == y or is_word_prefix(x, y) or is_word_prefix(y, x):
                return True
            shared = (set(x.split()) & set(y.split())) - STOPWORDS
            if any(len(t) > 2 for t in shared):
                return True
    return False


def is_word_prefix(query, name):
    """True when `name` starts with `query` at a word boundary.

    Substring matching is wrong here: 'investor' is a substring of 'investors
    house' but not a prefix of it word-wise, and treating it as one would make
    every Investor AB lookup ambiguous for no reason.
    """
    q, n = query.split(), name.split()
    return len(q) <= len(n) and n[:len(q)] == q


# --- candidate assembly ---------------------------------------------------

class Candidate(object):
    def __init__(self):
        self.isins, self.leis, self.orgnrs = set(), set(), set()
        self.mfn = None
        self.lines = []           # Nasdaq listed share lines
        self.names = set()        # every observed spelling, normalised
        self.cores = set()        # brand form of each
        self.symbols = set()

    def add_name(self, text):
        if text:
            self.names.add(norm(text))
            self.cores.add(core_name(text))

    def display(self):
        if self.mfn and self.mfn.get("name"):
            return self.mfn["name"]
        if self.lines:
            return re.sub(r"\s+[A-Z]$", "", self.lines[0].get("name") or "")
        return sorted(self.names)[0] if self.names else "?"

    def keys(self):
        return (set("I" + i for i in self.isins) | set("L" + l for l in self.leis)
                | set("O" + o for o in self.orgnrs))


def symbol_root(symbol):
    return nordic.root_symbol(symbol or "")


def mfn_entities(query):
    """Issuer records harvested from MFN's news search.

    MFN exposes no entity endpoint - `isin=` is silently ignored, and
    /a/<slug>.json is empty for issuers that distribute through Cision - so the
    only way in is to search the newswire and read the `author`/`subjects`
    objects it attaches to every item. Those objects are the payload: name,
    slug, ISINs, LEIs, tickers with MIC prefix, and local_refs, which for a
    Swedish issuer is "SE:<organisationsnummer>".
    """
    out = {}
    for term in query_variants(query):
        def produce(term=term):
            try:
                data = mfn.fetch("/all/s.json", query=term, limit=50)
            except SystemExit:
                return None
            return data
        # MFN 500s on some inputs (a bare "VOLV B", "H&M Hennes"); treat as empty.
        data = cached("mfnent:" + term, 21600, produce)
        for item in (data or {}).get("items") or []:
            for ent in [item.get("author")] + (item.get("subjects") or []):
                if ent and ent.get("slug"):
                    out.setdefault(ent["slug"], ent)
    return list(out.values())


def query_variants(query):
    """Alternative spellings to put past the two search engines.

    MFN tokenises '&' away, so a search for "H&M" returns unrelated noise while
    "handm" - which is also MFN's own slug - returns the issuer. Nasdaq answers
    HTTP 400 for very short strings and empty for "H&M", so both get tried.
    """
    seen, out = set(), []
    for cand in (query,
                 query.replace("&", " and "),
                 re.sub(r"\s*&\s*", "and", query),
                 re.sub(r"[^0-9A-Za-z ]+", " ", query),
                 # Neither engine indexes the legal form, so "AB Volvo" finds
                 # nothing on Nasdaq while "Volvo" finds all three share lines.
                 # Widening the SEARCH is safe; the disambiguation below still
                 # compares against the full string the user typed.
                 core_name(query)):
        cand = " ".join(cand.split())
        if len(cand) >= 3 and cand.lower() not in seen:
            seen.add(cand.lower())
            out.append(cand)
    return out


def nasdaq_lines(query):
    """All matching share lines, deduplicated by ORDERBOOK ID rather than ISIN.

    Nordea trades as NDA SE, NDA FI and NDA DK against a single ISIN: three
    order books, one share class. Deduplicating by ISIN here would silently drop
    two of the three and leave whichever came back first as "the" ticker, which
    for a Finnish bank is the Copenhagen line quoted in DKK. They are collapsed
    later, deliberately, in share_classes().
    """
    rows, seen = [], set()
    for term in query_variants(query):
        def produce(term=term):
            try:
                return nordic.search(term)
            except SystemExit:
                return None
        for r in cached("nasdaq:" + term, 21600, produce) or []:
            key = r.get("orderbookId") or (r.get("symbol"), r.get("isin"))
            if key not in seen:
                seen.add(key)
                rows.append(r)
    return rows


def build_candidates(entities, lines):
    """Fold MFN issuer records and Nasdaq share lines into one issuer per
    company. ISIN is the join key; symbol root is the fallback for Nasdaq lines
    MFN has never seen (Norwegian First North names, mostly)."""
    cands = []

    def find(keys, roots, cores):
        for c in cands:
            if keys & c.keys():
                return c
            # A symbol root is unique only WITHIN a market. Oslo's SAGA is Saga
            # Pure and Stockholm's SAGA A/B/D are AB Sagax; merging on the root
            # alone fused them into one issuer carrying Sagax's ISIN under Saga
            # Pure's name. So the names have to agree as well.
            if (roots and roots & set(symbol_root(s) for s in c.symbols)
                    and names_compatible(cores, c.cores)):
                return c
        return None

    for ent in entities:
        c = Candidate()
        c.mfn = ent
        c.isins.update(ent.get("isins") or [])
        c.leis.update(ent.get("leis") or [])
        for ref in ent.get("local_refs") or []:
            c.orgnrs.add(ref.split(":", 1)[-1])
        for tick in ent.get("tickers") or []:
            mic, _, sym = tick.partition(":")
            if mic in NORDIC_MICS:
                c.symbols.add(sym)
        c.add_name(ent.get("name"))
        existing = find(c.keys(), set(symbol_root(s) for s in c.symbols), c.cores)
        if existing:
            existing.isins |= c.isins
            existing.leis |= c.leis
            existing.orgnrs |= c.orgnrs
            existing.symbols |= c.symbols
            existing.names |= c.names
            existing.cores |= c.cores
        else:
            cands.append(c)

    for row in lines:
        isin = row.get("isin")
        root = symbol_root(row.get("symbol"))
        core = {core_name(re.sub(r"\s+[A-Z]{1,3}$", "", row.get("name") or ""))}
        c = find(set(["I" + isin]) if isin else set(), {root} if root else set(), core)
        if c is None:
            c = Candidate()
            cands.append(c)
        if isin:
            c.isins.add(isin)
        if row.get("symbol"):
            c.symbols.add(row["symbol"])
        c.add_name(re.sub(r"\s+[A-Z]{1,3}$", "", row.get("name") or ""))
        if all(row.get("orderbookId") != x.get("orderbookId") for x in c.lines):
            c.lines.append(row)
    return cands


# --- scoring and the refusal rule ----------------------------------------

def classify_query(raw):
    q = raw.strip()
    up = q.upper().replace(" ", "")
    if ISIN_RE.match(up):
        return "isin", up
    if LEI_RE.match(up) and len(up) == 20:
        return "lei", up
    m = ORGNR_RE.match(q.replace(" ", ""))
    if m:
        return "orgnr", "%s-%s" % (m.group(1), m.group(2))
    return "name", q


def name_variants(cand):
    """Every spelling worth comparing an exact query against, including the
    synthesised legal forms a user is likely to type ("AB Volvo")."""
    out = set()
    for n in cand.names | cand.cores:
        if not n:
            continue
        out.add(n)
        out.add(n + " ab")
        out.add("ab " + n)
        out.add(n + " ab publ")
        out.add("aktiebolaget " + n)
    return out


def resolve_candidates(cands, kind, needle):
    """Return (winner, reason, confidence, contenders).

    winner is None when the query cannot be pinned to one issuer. The order of
    the tests is the whole point:

      1. A real identifier is unambiguous by construction.
      2. An exchange ticker is unambiguous within a market.
      3. THE BRAND GUARD. If the typed name is the leading words of two or more
         different listed issuers, it is a brand, not an identity - refuse.
         This is what stops "Volvo" resolving to AB Volvo while Volvo Car AB
         sits right beside it.
      4. Only then does an exact full-name match win, which is how "AB Volvo"
         and "Volvo Car AB" still resolve cleanly.
    """
    if kind == "isin":
        hits = [c for c in cands if needle in c.isins]
        return one(hits, "ISIN %s" % needle, 1.0)
    if kind == "lei":
        hits = [c for c in cands if needle in c.leis]
        return one(hits, "LEI %s" % needle, 1.0)
    if kind == "orgnr":
        hits = [c for c in cands if needle in c.orgnrs]
        return one(hits, "organisationsnummer %s" % needle, 1.0)

    q = norm(needle)
    if not q:
        return None, "empty query", 0.0, []

    # A user types "SBB" or "ATCO", not "SBB B". The class letter is part of the
    # order-book symbol, not of the ticker as anyone says it, so the symbol root
    # counts as a ticker match - it is still unique within a market.
    want = needle.strip().upper()
    ticker = [c for c in cands
              if want in set(s.upper() for s in c.symbols)
              or want in set(symbol_root(s).upper() for s in c.symbols)]
    if len(ticker) == 1:
        return ticker[0], "exchange ticker %s" % want, 0.97, ticker
    if len(ticker) > 1:
        # A ticker is unique per market, not across the Nordics: SAGA is AB
        # Sagax in Stockholm and Saga Pure in Oslo. Falling through to the name
        # tests here would pick whichever spelling happened to look closer.
        return None, "ticker %s is used by %d Nordic issuers" % (want, len(ticker)), \
            0.0, ticker

    # Only candidates that share vocabulary with the query are contenders; the
    # searches return plenty of unrelated issuers that merely mentioned the name
    # in a press release.
    qt = set(q.split())
    related = [c for c in cands
               if any(qt <= set(n.split()) or is_word_prefix(q, n)
                      for n in cand_all_names(c) + sorted(name_variants(c)))]
    if not related:
        return None, "no name match", 0.0, []

    prefix = [c for c in related if any(is_word_prefix(q, n) for n in c.cores if n)]
    if len(prefix) > 1:
        return None, "brand shared by %d listed issuers" % len(prefix), 0.0, prefix

    exact = [c for c in related if q in name_variants(c)]
    if len(exact) == 1:
        return exact[0], "exact company name", 0.93, related
    if len(exact) > 1:
        return None, "%d issuers share this exact name" % len(exact), 0.0, exact

    if len(prefix) == 1:
        return prefix[0], "unique name prefix", 0.85, related

    if len(related) == 1:
        warn("Matched on name tokens only, not an exact name or ticker. "
             "Confirm the issuer is the one you meant.")
        return related[0], "unique partial name match", 0.60, related
    return None, "%d issuers match the name loosely" % len(related), 0.0, related


def cand_all_names(c):
    return [n for n in (c.names | c.cores) if n]


def one(hits, reason, conf):
    if len(hits) == 1:
        return hits[0], reason, conf, hits
    if not hits:
        return None, "no issuer carries " + reason, 0.0, []
    return None, "%d issuers carry %s" % (len(hits), reason), 0.0, hits


# --- enrichment -----------------------------------------------------------

def gleif(lei):
    data = http_json(GLEIF_RECORD + lei)
    if not data or "data" not in data:
        return {}
    ent = data["data"]["attributes"]["entity"]
    return {"legal_name": (ent.get("legalName") or {}).get("name"),
            "country": (ent.get("legalAddress") or {}).get("country"),
            "registered_as": ent.get("registeredAs"),
            "status": ent.get("status")}


def gleif_by_orgnr(orgnr):
    """GLEIF indexes the national company number as `registeredAs`, hyphen and
    all - "5560125790" finds nothing, "556012-5790" finds Aktiebolaget Volvo.
    Neither Nasdaq nor MFN can be searched by orgnr, so this is the only way in
    from a company-register number."""
    url = ("https://api.gleif.org/api/v1/lei-records?"
           + urllib.parse.urlencode({"filter[entity.registeredAs]": orgnr,
                                     "page[size]": "5"}))
    data = http_json(url)
    for rec in (data or {}).get("data") or []:
        return (rec["attributes"]["entity"].get("legalName") or {}).get("name")
    return None


def vies_legal_name(orgnr, country="SE"):
    """The tax authority's own register. For a Swedish company the VAT number is
    the organisationsnummer without the hyphen plus '01', so this doubles as a
    check that the orgnr we carry is real and still active."""
    digits = re.sub(r"\D", "", orgnr or "")
    if country != "SE" or len(digits) != 10:
        return None
    raw = cached("vies:" + digits, 604800,
                 lambda: (http(VIES % ("SE", digits + "01"), timeout=30) or b"").decode(
                     "utf-8", "replace") or None)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not data.get("isValid"):
        warn("EU VIES reports VAT number SE%s01 as not valid - the "
             "organisationsnummer may be stale." % digits)
        return None
    name = (data.get("name") or "").strip()
    return name or None


def firds(isin):
    """ESMA's instrument reference file: issuer LEI, CFI, notional currency and
    every venue MIC for one ISIN. Authoritative, and independent of both the
    exchange and the newswire, which makes it the cross-check."""
    url = FIRDS + "?" + urllib.parse.urlencode({
        "q": "isin:" + isin, "wt": "json", "rows": "300",
        "fl": "isin,lei,mic,gnr_full_name,gnr_cfi_code,gnr_notional_curr_code,"
              "mrkt_trdng_start_date"})
    data = http_json(url, timeout=60)
    docs = ((data or {}).get("response") or {}).get("docs") or []
    if not docs:
        return {}
    venue_mics = set(NORDIC_MICS) | FIRST_NORTH_SEGMENT_MICS
    mics = sorted({d.get("mic") for d in docs if d.get("mic")})
    listing = [m for m in mics if m in venue_mics]
    starts = [d.get("mrkt_trdng_start_date") for d in docs
              if d.get("mic") in venue_mics and d.get("mrkt_trdng_start_date")]
    return {"lei": next((d.get("lei") for d in docs if d.get("lei")), None),
            "full_name": next((d.get("gnr_full_name") for d in docs
                               if d.get("gnr_full_name")), None),
            "cfi": next((d.get("gnr_cfi_code") for d in docs if d.get("gnr_cfi_code")), None),
            "currency": next((d.get("gnr_notional_curr_code") for d in docs
                              if d.get("gnr_notional_curr_code")), None),
            "listing_mics": listing, "venue_count": len(mics),
            "first_trading_date": min(starts)[:10] if starts else None}


def esef_filings(lei):
    def produce():
        try:
            return esef.list_filings(lei, limit=8)
        except SystemExit:
            return None
    return cached("esef:" + lei, 604800, produce) or []


def reporting_currency(json_url):
    """Read the presentation currency out of the filing's units.

    Only the head of the document is fetched. An xBRL-JSON report declares its
    monetary units inline as "iso4217:EUR" on every fact, so the first megabyte
    already contains hundreds of them and settles the question - without pulling
    a 30 MB document to answer a three-letter question.
    """
    def produce():
        raw = http(FILINGS_BASE + json_url, timeout=90, limit=1500000)
        if not raw:
            return None
        counts = collections.Counter(
            m.decode("ascii") for m in re.findall(rb"iso4217:([A-Z]{3})", raw))
        if not counts:
            return None
        return counts.most_common(3)
    top = cached("cur:" + json_url, 2592000, produce)
    if not top:
        return None, None
    ranked = [(c, n) for c, n in top]
    if len(ranked) > 1 and ranked[1][1] > ranked[0][1] * 0.25:
        warn("The ESEF filing tags material amounts in %s as well as %s; the "
             "presentation currency shown is the dominant one only."
             % (ranked[1][0], ranked[0][0]))
    return ranked[0][0], ranked


def _filename_period_end(json_url):
    """The reporting period end embedded in the filing's own FILENAME, as a
    cross-check against the filings-index metadata attribute.

    filings.xbrl.org names each document <issuer>-<period_end>[-n]-<lang>.json
    and that date is normally identical to the index's own `period_end`
    attribute - but not always. Empirically (Clas Ohlson, LEI
    549300MH8OETHBBKJU80, checked 2026-08-31): the index carries period_end
    2024-05-31 for the latest SE filing, while the filename says
    "Clas-2024-04-30-en.json" and every one of that SAME filing's own
    duration and instant facts (revenue, total_assets, equity, cash) says
    2024-04-30. The index attribute was simply wrong by a month, silently -
    exactly the kind of error that turns into a TTM three months out with a
    citation that still reads "ESEF filing period_end". No extra network
    call: json_url is already present on every filing this file fetches.
    """
    name = (json_url or "").rsplit("/", 1)[-1]
    m = re.search(r"(\d{4}-\d{2}-\d{2})", name)
    return m.group(1) if m else None


def fiscal_year_end_from_esef(filings):
    if not filings:
        return None, None
    ends = [f["period_end"] for f in filings if f.get("period_end")]
    if not ends:
        return None, None
    latest = max(ends)

    # Cross-check the winning filing's own filename - see
    # _filename_period_end(). Only the latest filing is checked: it is the
    # only one whose date is actually returned/used downstream.
    latest_filing = next((f for f in filings if f.get("period_end") == latest), None)
    fname_end = _filename_period_end((latest_filing or {}).get("json_url"))
    if fname_end and fname_end != latest:
        warn("ESEF filings-index period_end (%s) disagrees with the date in "
             "the filing's own filename (%s) for the latest filing; the "
             "filename is used as the more reliable of the two free signals."
             % (latest, fname_end))
        latest = fname_end

    monthdays = {e[5:] for e in ends}
    if len(monthdays) > 1:
        warn("ESEF period ends disagree across filings (%s); the fiscal year "
             "may have been changed. Latest filing used."
             % ", ".join(sorted(monthdays)))
    return latest[5:], latest


def month_ranges(text):
    """Every 'month ... - ... month' span in a block of report prose, as
    (end_month, end_day_or_None). Nordic issuers write the covered period in the
    release itself: '12 MONTHS (1 April 2025 - 31 March 2026)', 'Oct-Dec 2025',
    'delarsrapport 1 april - 30 september 2025'."""
    hits = []
    for m in MONTH_RE.finditer(text):
        day = m.group(1) or m.group(3)
        hits.append((m.start(), m.end(), MONTHS[m.group(2).lower()],
                     int(day) if day else None))
    out = []
    for i in range(len(hits) - 1):
        gap = text[hits[i][1]:hits[i + 1][0]]
        if len(gap) < 30 and re.search(r"[-‐-―]|\bto\b|\btill\b", gap):
            out.append((hits[i + 1][2], hits[i + 1][3]))
    return out


def _year_end_answer(hits):
    """Reduce one fiscal year's (month, day) hits to a single 'MM-DD'.

    Within a SINGLE year-end release the twelve-month span and the fourth
    quarter both close on the fiscal year end, so that month recurs and wins
    on count - this is a within-year tie-break, not a cross-year vote.
    """
    by_month = collections.Counter()
    for (month, day), n in hits.items():
        by_month[month] += n
    month = by_month.most_common(1)[0][0]
    last = calendar.monthrange(2025, month)[1]
    # A day number picked up next to the month is only trusted when it could
    # actually be a year end. Report prose is full of other dates - a signing
    # date, an AGM date - and "20 December" must not become the fiscal year end.
    days = [d for (m, d), _ in hits.items() if m == month and d and 26 <= d <= last]
    return "%02d-%02d" % (month, max(days) if days else last)


def fiscal_year_end_from_reports(slug):
    """Fallback for First North / Spotlight / NGM issuers, which file no ESEF.

    Only year-end releases are read - a Q2 report would say 'January-June' and
    hand back a June year end.

    Evidence is grouped by the CALENDAR YEAR each release was published, and
    only the most recently published year's own group decides the answer.
    Pooling every year-end release ever seen into one count (the previous
    behaviour here) is a mode across the union of periods - exactly the A1
    defect: a company that changed its fiscal year, or one where an older
    year simply has more parseable releases than the latest, would get
    smoothed into whichever month happened to recur most often across its
    whole history, not its CURRENT fiscal year end. Mirrors
    fiscal_year_end_from_esef(): latest wins, and a disagreement between
    years is surfaced as a warning rather than voted away.
    """
    if not slug:
        return None
    def produce():
        try:
            return mfn.fetch("/a/%s.json" % slug, limit=40)
        except SystemExit:
            return None
    data = cached("mfnfeed:" + slug, 21600, produce)

    by_year = {}
    for item in (data or {}).get("items") or []:
        tags = (item.get("properties") or {}).get("tags") or []
        if not ({"sub:report:interim:q4", "sub:report:year-end"} & set(tags)):
            continue
        content = item.get("content") or {}
        published = (content.get("publish_date") or "")[:4]
        if not published.isdigit():
            continue
        blob = " ".join([content.get("title") or "", content.get("preamble") or "",
                         re.sub(r"<[^>]+>", " ", content.get("html") or "")[:4000]])
        ranges = month_ranges(blob)
        if not ranges:
            continue
        year_hits = by_year.setdefault(int(published), collections.Counter())
        for month, day in ranges:
            year_hits[(month, day)] += 1

    if not by_year:
        return None

    years = sorted(by_year)
    latest = years[-1]
    answer = _year_end_answer(by_year[latest])

    older = {_year_end_answer(by_year[y]) for y in years[:-1]}
    disagreement = older - {answer}
    if disagreement:
        warn("Year-end report text implies fiscal year end %s in an earlier "
             "year but %s in the latest (%d) release; the fiscal year may "
             "have been changed. Latest release used."
             % (", ".join(sorted(disagreement)), answer, latest))
    return answer


CORPORATE_SKIP = ("mfn.se", "cision.com", "cision.se", "cisionwire", "linkedin.com",
                  "twitter.com", "facebook.com", "youtube.com", "instagram.com",
                  "x.com", "google", "gstatic", "nasdaq.com", "w3.org",
                  "schema.org", "spotify.com", "soundcloud.com", "euroclear.com",
                  "finansinspektionen.se", "outlook.com", "pinterest.com",
                  "apple.com", "adobe.com", "cookie", "onetrust", "vimeo.com",
                  "wikipedia.org", "bit.ly", "computershare.com")


def domains(html):
    found = collections.Counter()
    for host in re.findall(r"https?://([A-Za-z0-9.\-]+)", html or ""):
        host = host.lower()
        host = host[4:] if host.startswith("www.") else host
        if any(s in host for s in CORPORATE_SKIP) or host.count(".") > 2:
            continue
        found[host] += 1
    return found


def ir_url(slug, cision_slug, names):
    """Best-effort corporate/IR site.

    No free register publishes an issuer website, so it is read off the
    issuer's own releases: MFN carries the body HTML, and for the large caps
    that distribute through Cision the newsroom page links back to the company.
    This is a homepage, not necessarily the IR landing page - hence the note.
    """
    tokens = set()
    for n in names:
        tokens.update(t for t in n.split() if len(t) > 3)

    def rank(counter):
        best = None
        for host, n in counter.most_common(12):
            stem = host.split(".")[0].replace("-", "")
            bonus = 5 if any(t.replace(" ", "") in stem or stem in t for t in tokens) else 0
            score = n + bonus
            if best is None or score > best[0]:
                best = (score, host)
        return best

    if slug:
        def produce():
            try:
                return mfn.fetch("/a/%s.json" % slug, limit=8)
            except SystemExit:
                return None
        data = cached("mfnfeed8:" + slug, 86400, produce)
        counter = collections.Counter()
        for item in (data or {}).get("items") or []:
            counter += domains((item.get("content") or {}).get("html") or "")
        best = rank(counter)
        if best and best[0] >= 4:
            return "https://" + best[1]

    if cision_slug:
        page = cached("cisionpage:" + cision_slug, 604800,
                      lambda: (http("https://news.cision.com/se/" + cision_slug)
                               or b"").decode("utf-8", "replace") or None)
        best = rank(domains(page or ""))
        if best and best[0] >= 4:
            return "https://" + best[1]
    return None


def cision_slug_for(names):
    """Cision's resolver matches any newsroom, including subsidiaries and
    unrelated agencies ("Volvo Trucks", "Vattenfall & Volvo"), so only an exact
    or near-exact name match is accepted."""
    for name in names:
        def produce(name=name):
            try:
                return cision.resolve(name)
            except SystemExit:
                return None
        rows = cached("cision:" + name, 604800, produce) or []
        target = core_name(name)
        for row in rows:
            if core_name(row.get("name")) == target:
                return row["slug"]
    return None


# --- assembly -------------------------------------------------------------

def nordic_summary_reference(obid):
    """The part of nordic.summary() that IS reference data: share count,
    ISIN, segment, ICB code, exchange note. Cached at TTL_REFERENCE.

    market_cap is deliberately excluded here - see nordic_market_cap() and
    the A4 note above the cache section. Both read the same underlying
    endpoint, so a cold call to either may hit the network once; that is the
    price of not letting a quote squat on the reference-data cache.
    """
    def produce():
        try:
            s = nordic.summary(obid)
        except SystemExit:
            return None
        if s is None:
            return None
        return {k: v for k, v in s.items() if k != "market_cap"}
    return cached("nsumref:" + str(obid), TTL_REFERENCE, produce) or {}


def nordic_market_cap(obid):
    """market_cap alone, price-derived, cached at TTL_QUOTE (15 minutes) -
    NOT the 6-hour reference-data TTL. See the A4 note above."""
    def produce():
        try:
            s = nordic.summary(obid)
        except SystemExit:
            return None
        return (s or {}).get("market_cap")
    return cached("nsumcap:" + str(obid), TTL_QUOTE, produce)


def share_classes(cand):
    """Every listed class of the issuer, with its registered share count.

    Any ISIN MFN knows about that Nasdaq's name search did not return is looked
    up by ISIN, so a class missing from the text search still gets counted.
    """
    known = {l.get("isin") for l in cand.lines}
    for isin in sorted(cand.isins - known):
        def produce(isin=isin):
            try:
                return nordic.search(isin)
            except SystemExit:
                return None
        for row in cached("nasdaq:" + isin, 21600, produce) or []:
            if row.get("isin") == isin and row["isin"] not in known:
                known.add(isin)
                cand.lines.append(row)

    # One ISIN is one share class however many order books quote it. Summing
    # per order book would treble Nordea's share count.
    by_isin = collections.OrderedDict()
    for line in sorted(cand.lines, key=lambda r: r.get("symbol") or ""):
        by_isin.setdefault(line.get("isin"), []).append(line)

    out = []
    for isin, lines in by_isin.items():
        home = home_listing(isin, lines)
        obid = home.get("orderbookId")
        # A4: reference fields (long TTL) and market_cap (price-derived, short
        # TTL) are fetched and cached separately - see nordic_summary_reference
        # / nordic_market_cap above.
        summary = nordic_summary_reference(obid)
        market_cap = nordic_market_cap(obid)
        others = [l["symbol"] for l in lines if l is not home]
        if others:
            warn("%s is cross-listed as %s. Those are the same share class, "
                 "not extra shares; the count below is not multiplied."
                 % (home.get("symbol"), ", ".join(others)))
        out.append({"symbol": home.get("symbol"), "isin": isin,
                    "shares": summary.get("shares"), "orderbook_id": obid,
                    "market_cap": market_cap,
                    "segment": summary.get("segment"),
                    "currency": home.get("currency"),
                    "cross_listed_as": others,
                    "exchange_note": summary.get("note") or ""})
    return out


# Which venue is the issuer's own. A cross-listed line carries the market in the
# symbol ("NDA FI") and quotes in that market's currency.
HOME_CURRENCY = {"SE": "SEK", "FI": "EUR", "DK": "DKK", "NO": "NOK", "IS": "ISK"}


def home_listing(isin, lines):
    """The order book on the issuer's home market, which is the one whose
    price and currency belong with its accounts."""
    country = (isin or "")[:2]
    for line in lines:
        if (line.get("symbol") or "").upper().endswith(" " + country):
            return line
    want = HOME_CURRENCY.get(country)
    for line in lines:
        if want and line.get("currency") == want:
            return line
    return lines[0]


def primary_class(classes):
    """The class an analyst means by 'the ticker': the B share where the issuer
    has one, since that is the liquid line in a Swedish dual-class structure."""
    for c in classes:
        if (c["symbol"] or "").endswith(" B"):
            return c
    return classes[0] if classes else None


def assemble(cand, reason, confidence):
    ent = cand.mfn or {}
    classes = share_classes(cand)
    primary = primary_class(classes)

    lei = None
    isin = primary["isin"] if primary else (sorted(cand.isins)[0] if cand.isins else None)
    firds_info = firds(isin) if isin else {}
    for source in (firds_info.get("lei"), *sorted(cand.leis)):
        if source:
            lei = source
            break
    if firds_info.get("lei") and cand.leis and firds_info["lei"] not in cand.leis:
        warn("ESMA FIRDS names LEI %s as the issuer of %s, but MFN carries %s. "
             "The issuing entity may differ from the reporting entity."
             % (firds_info["lei"], isin, ", ".join(sorted(cand.leis))))

    gl = gleif(lei) if lei else {}
    orgnr = (sorted(cand.orgnrs)[0] if cand.orgnrs else None) or gl.get("registered_as")
    country = gl.get("country") or (isin[:2] if isin else None)
    # VIES is the tax authority's live register and beats GLEIF's self-reported
    # name; GLEIF is the fallback for non-Swedish issuers and for a company
    # whose VAT registration cannot be read.
    legal = vies_legal_name(orgnr, country or "SE")
    legal_source = "EU VIES" if legal else None
    if not legal:
        legal = gl.get("legal_name")
        legal_source = "GLEIF" if legal else None

    mic = None
    for tick in ent.get("tickers") or []:
        head = tick.partition(":")[0]
        if head in NORDIC_MICS:
            mic = head
            break
    if not mic:
        listing = firds_info.get("listing_mics") or []
        mic = listing[0] if len(listing) == 1 else None
    if not mic and cand.lines:
        mic = GROUP_MIC.get(cand.lines[0].get("group"))
    reported = set(firds_info.get("listing_mics") or [])
    consistent = mic in reported or (mic in FIRST_NORTH_SEGMENT_MICS
                                     and reported & FIRST_NORTH_SEGMENT_MICS)
    if mic and reported and not consistent:
        warn("MIC %s is not among the listing venues ESMA FIRDS reports for %s "
             "(%s). One of the two is stale - check for a venue change."
             % (mic, isin, ", ".join(sorted(reported))))

    filings = esef_filings(lei) if lei else []
    fye, fye_full = fiscal_year_end_from_esef(filings)
    fye_source = "ESEF filing period_end (%s)" % fye_full if fye else None
    if not fye:
        fye = fiscal_year_end_from_reports(ent.get("slug"))
        if fye:
            fye_source = "parsed from the issuer's year-end report period range"
            warn("No ESEF filing for this issuer, so the fiscal year end was "
                 "READ OUT OF THE REPORT TEXT. Confirm it against the report "
                 "itself before using it to align periods.")
        else:
            warn("Fiscal year end could not be established from any free source. "
                 "Do NOT assume 31 December.")

    rep_cur, cur_mix = (None, None)
    if filings:
        rep_cur, cur_mix = reporting_currency(filings[0]["json_url"])
    quote_cur = primary["currency"] if primary else None
    if rep_cur and quote_cur and rep_cur != quote_cur:
        warn("Quoted in %s, reports in %s. Market cap and accounting figures are "
             "in DIFFERENT currencies - convert before forming any multiple."
             % (quote_cur, rep_cur))
    if not rep_cur:
        warn("Reporting currency unknown (no ESEF filing reachable). It is NOT "
             "safe to assume it equals the quote currency %s." % (quote_cur or "?"))

    names = [n for n in [ent.get("name"), legal, cand.display()] if n]
    cslug = cision_slug_for(names)
    website = ir_url(ent.get("slug"), cslug, [norm(n) for n in names])

    shares = [c["shares"] for c in classes]
    total = sum(s for s in shares if s) if any(shares) else None
    if total is not None and any(s is None for s in shares):
        warn("At least one listed class has no share count; total_listed_shares "
             "is a PARTIAL sum.")
    if len(classes) == 1:
        warn("Only one listed class found. An UNLISTED class would be invisible "
             "here (NIBE, Fenix Outdoor). Confirm against the issuer's latest "
             "total-number-of-shares-and-votes disclosure.")

    return {
        "company_name": cand.display(),
        "legal_name": legal or NA,
        "ticker": (primary or {}).get("symbol") or NA,
        "isin": isin or NA,
        "lei": lei or NA,
        "organisation_number": orgnr or NA,
        "exchange": mic or NA,
        "exchange_name": NORDIC_MICS.get(mic, NA),
        "market_segment": (primary or {}).get("segment")
                          or (cand.lines[0].get("group") if cand.lines else None) or NA,
        "currency": quote_cur or NA,
        "reporting_currency": rep_cur or NA,
        "fiscal_year_end": fye or NA,
        "fiscal_year_end_source": fye_source or NA,
        "share_classes": classes,
        "total_listed_shares": total,
        "ir_url": website or NA,
        "mfn_slug": ent.get("slug") or NA,
        "cision_slug": cslug or NA,
        "country": country or NA,
        "cfi": firds_info.get("cfi") or NA,
        "first_trading_date": firds_info.get("first_trading_date") or NA,
        "confidence": round(confidence, 2),
        "confidence_basis": reason,
        "identifier_sources": {
            "listing": "Nasdaq Nordic reference data",
            "orgnr_lei_slug": "MFN issuer record",
            "legal_name": legal_source or NA,
            "isin_mic_cfi": "ESMA FIRDS",
            "fiscal_year_end": fye_source or NA,
        },
        "warnings": WARNINGS,
    }


# --- importable API ---------------------------------------------------
#
# Everything above this line is what does the work; everything here is how a
# SIBLING SCRIPT gets at it without shelling out to `python company_resolve.py
# NAME --json` and parsing stdout. This is the fix for the root cause the
# audit flagged in short_se.py, insider_se.py, nordic_shares.py and
# peers_se.py: each of them re-implements its own name matching (bare
# substring or prefix match) because there was no importable entry point to
# this file's correct one. peers_se.py's Volvo/Volvo Car mixup - AB Volvo's
# market cap divided by Volvo Car's earnings, printed as P/E 44.5x against a
# true 14.7x, with a real LEI cited - is exactly the failure mode this API
# closes off: `resolve_lei("Volvo")` raises Ambiguous instead of returning a
# plausible-looking wrong answer.
#
#   from company_resolve import resolve, resolve_lei, Ambiguous, NotFound
#
#   try:
#       rec = resolve("Volvo Car")
#   except Ambiguous as e:
#       ...  # e.reason, e.candidates (same shape as --json's "candidates")
#   except NotFound:
#       ...  # nothing listed on a Nordic market matches at all
#
#   lei = resolve_lei("AB Volvo")   # -> a single LEI, or raises
#
# resolve() runs the IDENTICAL engine main() does - same brand guard, same
# refusal behaviour, same fields - so a caller gets the CLI's guarantees
# without a subprocess.


class ResolutionError(Exception):
    """Base class for a query resolve() could not turn into one issuer."""


class NotFound(ResolutionError):
    """No listed Nordic issuer matched the query at all (CLI exit code 3).

    Checked Nasdaq Nordic's instrument search and MFN's issuer index; the
    company may be unlisted, listed outside the Nordics, or spelled
    differently.
    """
    def __init__(self, query):
        self.query = query
        super().__init__("no listed Nordic issuer matched %r" % query)


class Ambiguous(ResolutionError):
    """The query names more than one listed issuer (CLI exit code 2).

    This is the BRAND GUARD firing - "Volvo" refuses because AB Volvo and
    Volvo Car AB are both listed issuers with that prefix; a caller must
    supply the full legal name, the ticker, or the ISIN instead.

    .reason      human-readable explanation (why the query was refused).
    .candidates  list of dicts, one per contender, same shape as --json's
                 "candidates": company_name, tickers, isins,
                 organisation_numbers, leis, mfn_slug.
    """
    def __init__(self, query, reason, candidates):
        self.query = query
        self.reason = reason
        self.candidates = candidates
        super().__init__("%r is ambiguous: %s" % (query, reason))


def _candidate_summary(c):
    """Plain-data summary of one Candidate - the shape --json emits under
    "candidates" for an ambiguous or unresolved query, and the shape
    Ambiguous.candidates carries. Candidate itself is an internal type."""
    return {"company_name": c.display(),
            "tickers": sorted(c.symbols),
            "isins": sorted(c.isins),
            "organisation_numbers": sorted(c.orgnrs),
            "leis": sorted(c.leis),
            "mfn_slug": (c.mfn or {}).get("slug")}


def _resolve_core(raw, forced_kind=None, country=None):
    """The resolution engine shared by the CLI and the importable API.

    `raw` is a company name, ticker, ISIN, LEI or organisationsnummer exactly
    as a user would type it. `forced_kind` overrides classify_query()'s guess
    (the CLI's --isin/--lei/--orgnr flags do this). `country` is an optional
    ISO-2 filter, e.g. "SE".

    Returns (record, None) when exactly one issuer resolves, or
    (None, {"reason": str, "candidates": [Candidate, ...] or None})
    otherwise - `candidates` is None when nothing matched at all and a
    non-empty list of Candidate objects when the query is ambiguous.
    """
    kind, needle = classify_query(raw)
    if forced_kind:
        kind = forced_kind

    # An identifier is searchable text in both engines: Nasdaq's search accepts
    # an ISIN directly, and MFN surfaces the issuer whose release mentions it.
    entities = mfn_entities(needle)
    lines = nasdaq_lines(needle)

    if kind in ("lei", "orgnr") and not any(
            needle in ((e.get("leis") or []) + [r.split(":")[-1]
                                                for r in e.get("local_refs") or []])
            for e in entities):
        # Neither engine indexes LEIs or orgnrs. Go to GLEIF for the legal name
        # and search again on that.
        seed = gleif(needle).get("legal_name") if kind == "lei" else gleif_by_orgnr(needle)
        if seed:
            entities += mfn_entities(seed)
            lines += nasdaq_lines(seed)

    cands = build_candidates(entities, lines)

    if country:
        want = country.upper()
        kept = [c for c in cands
                if any(i.startswith(want) for i in c.isins)
                or any((e or "").startswith(want)
                       for e in (c.mfn or {}).get("local_refs") or [])
                or any((l.get("isin") or "").startswith(want) for l in c.lines)]
        if kept:
            cands = kept

    # Only issuers with an actual Nordic listing are resolvable identities; MFN
    # also indexes banks, research houses and regulators that publish releases.
    listed = [c for c in cands if c.lines or c.symbols]
    if listed:
        cands = listed

    winner, reason, confidence, contenders = resolve_candidates(cands, kind, needle)
    if winner is None:
        return None, {"reason": reason, "candidates": contenders or None}

    record = assemble(winner, reason, confidence)
    return record, None


def _run_resolution(raw, forced_kind=None, country=None, use_cache=True):
    """Run _resolve_core() with per-call isolation, so an in-process caller
    that resolves several companies in one run never leaks state between
    them:

      * WARNINGS is swapped for a fresh list before the call and restored
        after - assemble() keeps its own reference to the fresh list, so the
        returned record's "warnings" are exactly this call's, never a mix
        with whatever a previous resolve() in the same process produced.
      * use_cache=False disables the on-disk cache for this call only,
        without disturbing any sibling call in the same process (the CLI's
        --no-cache flips this globally for the life of one run, which is
        fine because a CLI invocation only ever resolves one query).
    """
    global cached, WARNINGS
    prev_cached, prev_warnings = cached, WARNINGS
    WARNINGS = []
    if not use_cache:
        cached = lambda key, ttl, produce: produce()  # noqa: E731
    try:
        return _resolve_core(raw, forced_kind, country)
    finally:
        cached = prev_cached
        WARNINGS = prev_warnings


def resolve(query=None, *, isin=None, lei=None, orgnr=None, country=None,
           use_cache=True):
    """Resolve one company to its canonical Nordic identity.

    This is the SAME engine `python company_resolve.py NAME --json` runs -
    importable so a sibling script never has to shell out and parse stdout.
    Give exactly one of `query` (a name or exchange ticker), `isin`, `lei` or
    `orgnr`; `country` optionally restricts the match to one ISO-2 market
    (e.g. "SE").

    On success returns the same dict --json prints for a resolved query:
    company_name, legal_name, ticker, isin, lei, organisation_number,
    exchange, exchange_name, market_segment, currency, reporting_currency,
    fiscal_year_end (+ fiscal_year_end_source), share_classes,
    total_listed_shares, ir_url, mfn_slug, cision_slug, country, cfi,
    first_trading_date, confidence, confidence_basis, identifier_sources,
    warnings. See assemble() for exactly how each is built, and the module
    docstring at the top of this file for what each field is sourced from and
    how far it can be trusted.

    Raises Ambiguous when the query names a BRAND shared by more than one
    listed issuer - "Volvo" (AB Volvo vs Volvo Car AB), "Atlas" (Atlas Copco's
    two share CLASSES are one issuer and resolve fine; a brand shared across
    separate issuers does not). This is the exact case that made peers_se.py
    divide AB Volvo's market cap by Volvo Car's earnings. Raises NotFound
    when nothing listed on a Nordic market matches at all. This function
    never guesses: there is no confidence level low enough to make it return
    a wrong answer instead of raising.

    use_cache=False bypasses the on-disk cache for this call only.
    """
    raw = isin or lei or orgnr or query
    if not raw:
        raise ValueError("give query, isin, lei or orgnr")
    forced_kind = "isin" if isin else "lei" if lei else "orgnr" if orgnr else None
    record, ambiguity = _run_resolution(raw, forced_kind, country, use_cache)
    if record is None:
        candidates = [_candidate_summary(c) for c in (ambiguity["candidates"] or [])]
        if not candidates:
            raise NotFound(raw)
        raise Ambiguous(raw, ambiguity["reason"], candidates)
    return record


def try_resolve(query=None, *, isin=None, lei=None, orgnr=None, country=None,
                use_cache=True):
    """resolve(), but returns (record, None) or (None, error) instead of
    raising - for a caller that would rather branch than catch.

    `error` is None on success, otherwise an unraised NotFound or Ambiguous
    instance (inspect `error.candidates` on an Ambiguous).
    """
    try:
        return resolve(query, isin=isin, lei=lei, orgnr=orgnr, country=country,
                       use_cache=use_cache), None
    except ResolutionError as e:
        return None, e


def resolve_lei(name, country=None, use_cache=True):
    """Answer exactly one question: the ESEF-filer LEI for `name`, or tell
    the caller it is ambiguous.

    Returns the LEI string, or None if the issuer resolves cleanly but no
    LEI could be found anywhere (MFN / ESMA FIRDS / GLEIF) - some small First
    North names have none. Raises Ambiguous or NotFound exactly as resolve()
    does; it never returns a plausible-but-wrong LEI.

    This is the direct fix for the failure the audit traced through
    peers_se.py: a bare prefix match let "Volvo" resolve to Volvo Car AB's
    LEI while AB Volvo's market cap was used for the numerator, producing a
    P/E of 44.5x against a true 14.7x with a real, citable LEI attached.
    Calling resolve_lei("Volvo") here raises Ambiguous instead.
    """
    rec = resolve(name, country=country, use_cache=use_cache)
    lei = rec.get("lei")
    return lei if lei and lei != NA else None


# --- output ---------------------------------------------------------------

def print_candidates(cands, reason, query):
    print("REFUSING TO RESOLVE %r: %s." % (query, reason))
    print()
    print("These are different listed companies. Re-run with the one you mean —")
    print("its full legal name, its ticker, or its ISIN.")
    print()
    print("  %-28s %-12s %-14s %-15s %s"
          % ("COMPANY", "TICKER", "ISIN", "ORGNR", "LEI"))
    print("  " + "-" * 100)
    for c in sorted(cands, key=lambda x: x.display()):
        syms = sorted(c.symbols) or [l.get("symbol") for l in c.lines]
        isins = sorted(c.isins) or [l.get("isin") for l in c.lines]
        print("  %-28s %-12s %-14s %-15s %s"
              % (c.display()[:28], (syms[0] if syms else "-")[:12],
                 (isins[0] if isins else "-") or "-",
                 (sorted(c.orgnrs)[0] if c.orgnrs else "-"),
                 (sorted(c.leis)[0] if c.leis else "-")))
        for extra_sym, extra_isin in zip(syms[1:], (isins[1:] + [None] * len(syms))):
            print("  %-28s %-12s %-14s" % ("", extra_sym or "", extra_isin or ""))
    print()
    print("  Nothing was resolved. No downstream figure should be produced from")
    print("  this query.")


def print_record(rec):
    print("%s — resolved identity" % rec["company_name"])
    print()
    rows = [
        ("legal name", rec["legal_name"]),
        ("ticker", rec["ticker"]),
        ("ISIN", rec["isin"]),
        ("LEI", rec["lei"]),
        ("organisationsnummer", rec["organisation_number"]),
        ("exchange (MIC)", "%s  %s" % (rec["exchange"], rec["exchange_name"])
         if rec["exchange"] != NA else NA),
        ("market segment", rec["market_segment"]),
        ("quote currency", rec["currency"]),
        ("reporting currency", rec["reporting_currency"]),
        ("fiscal year end", rec["fiscal_year_end"]),
        ("  from", rec["fiscal_year_end_source"]),
        ("CFI", rec["cfi"]),
        ("first trading date", rec["first_trading_date"]),
        ("IR / corporate site", rec["ir_url"]),
        ("MFN slug", rec["mfn_slug"]),
        ("Cision slug", rec["cision_slug"]),
        ("confidence", "%.2f  (%s)" % (rec["confidence"], rec["confidence_basis"])),
    ]
    for label, value in rows:
        print("  %-22s %s" % (label, value))

    print()
    print("  %-12s %-16s %18s %20s %s"
          % ("CLASS", "ISIN", "SHARES", "MARKET CAP", "SEGMENT"))
    print("  " + "-" * 92)
    for c in rec["share_classes"]:
        print("  %-12s %-16s %18s %20s %s"
              % (c["symbol"] or "-", c["isin"] or "-",
                 "{:,.0f}".format(c["shares"]) if c["shares"] else "n/a",
                 "{:,.0f}".format(c["market_cap"]) if c["market_cap"] else "n/a",
                 c["segment"] or "-"))
        if c.get("cross_listed_as"):
            print("  %-12s also quoted as %s (same shares)"
                  % ("", ", ".join(c["cross_listed_as"])))
    print("  " + "-" * 92)
    if rec["total_listed_shares"]:
        print("  %-12s %-16s %18s" % ("TOTAL", "",
                                      "{:,.0f}".format(rec["total_listed_shares"])))
    else:
        print("  %-12s %-16s %18s" % ("TOTAL", "", NA))

    notes = {c["exchange_note"] for c in rec["share_classes"] if c.get("exchange_note")}
    if notes:
        print()
        for n in notes:
            print("  !! Exchange note: %s" % n)

    if rec["warnings"]:
        print()
        for w in rec["warnings"]:
            print("  !! %s" % w)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?", help="company name, ticker, ISIN, LEI or orgnr")
    ap.add_argument("--isin", help="resolve by ISIN, e.g. SE0012673267")
    ap.add_argument("--lei", help="resolve by 20-character LEI")
    ap.add_argument("--orgnr", help="resolve by Swedish organisationsnummer")
    ap.add_argument("--country", help="restrict to one ISO country, e.g. SE")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--no-cache", action="store_true",
                    help="ignore the on-disk cache for this run")
    args = ap.parse_args()

    raw = args.isin or args.lei or args.orgnr or args.query
    if not raw:
        ap.error("give a company name, ticker, ISIN, LEI or organisationsnummer")

    forced_kind = ("isin" if args.isin else "lei" if args.lei
                  else "orgnr" if args.orgnr else None)
    record, ambiguity = _run_resolution(raw, forced_kind, args.country,
                                        use_cache=not args.no_cache)

    if record is None:
        reason, contenders = ambiguity["reason"], ambiguity["candidates"]
        if not contenders:
            print("%s: no listed Nordic issuer matched %r." % (NA, raw))
            print()
            print("Checked Nasdaq Nordic's instrument search and MFN's issuer")
            print("index. The company may be unlisted, listed outside the Nordics,")
            print("or spelled differently. Nothing was resolved.")
            return 3
        if args.as_json:
            print(json.dumps({"query": raw, "resolved": False, "reason": reason,
                              "candidates": [_candidate_summary(c) for c in contenders]},
                             indent=2, ensure_ascii=False))
        else:
            print_candidates(contenders, reason, raw)
        return 2

    if args.as_json:
        print(json.dumps(dict(query=raw, resolved=True, **record),
                         indent=2, ensure_ascii=False))
    else:
        print_record(record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
