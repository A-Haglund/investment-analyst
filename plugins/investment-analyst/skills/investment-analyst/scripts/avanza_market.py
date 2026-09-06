#!/usr/bin/env python3
"""Avanza market-guide endpoints: short selling, key ratios, owner counts.

TIER 4 CONSTRAINT (from references/source-registry.md):

    Tier 4 may never be the sole source of a material financial figure.
    It can supply a lead, a cross-check, or a pointer.

Every function in this module returns data explicitly labelled as tier 4,
unsuitable for direct citation in a valuation, and marked for use only as
a cross-check. Avanza is a broker redistributing licensed market data, not
the issuer or a regulator. The three endpoints below are keyless and carry
no analyst forecasts or price targets - they are computed ratios, a ratio
history, and an owner-count series whose underlying population Avanza does
not state. Nothing here names a registrar, an exchange or a regulator as the
origin of a figure, because no such attribution has been verified against
the endpoint; naming one would let a tier-4 number inherit a tier-1
reputation. Each function's docstring states its limits.

Usage:
    python avanza_market.py --selftest
    python avanza_market.py "Pricer" --json        (live calls: short selling)
    python avanza_market.py --help

Free, no API key. Python 3 stdlib only.
"""
import argparse
import datetime
import json
import os
import re
import sys
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

NA = "DATA NOT AVAILABLE"

AVANZA_SEARCH = "https://www.avanza.se/_api/search/filtered-search"
AVANZA_SHORT_SELLING = "https://www.avanza.se/_api/market-guide/short-selling/%s"
AVANZA_ANALYSIS = "https://www.avanza.se/_api/market-guide/stock/%s/analysis"
AVANZA_OWNERS = "https://www.avanza.se/_api/market-guide/number-of-owners/%s"

UA = "Mozilla/5.0 (compatible; investment-analyst-skill/1.0)"

CACHE_DIR = os.path.join(tempfile.gettempdir(), "investment-analyst-cache",
                         "avanza_market")
TTL_SEARCH = 21600           # 6h - a name-to-orderBookId mapping is stable
TTL_SHORT_SELLING = 86400    # 24h - intraday data changes daily
TTL_OWNERS = 604800          # 7 days - ownership updates weekly
TTL_RATIOS = 2592000         # 30 days - ratio history updates slowly

# Value sanity bounds. Timestamps were validated hard and values not at all,
# which is backwards: a wrong date is visible in the output, a wrong magnitude
# is not. `ratio` is documented by its own scale (0.0079 -> 0.79%), so anything
# above 1.0 is not a fraction, and multiplying it by 100 would print a
# four-figure short interest as though it had been measured.
MAX_SHORT_RATIO = 1.0
# An owner count is a headcount of accounts. Ten million is far above any
# Nordic issuer's plausible retail base and still well below where a corrupt
# field (an epoch, an amount in ore) would land.
MAX_OWNERS = 10000000


def _cache_path(key):
    import hashlib
    return os.path.join(CACHE_DIR, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")


def _worth_caching(value, useful):
    """True when `value` is worth remembering for the rest of the TTL.

    None is never cached - a failed source must be retried, not remembered.
    Neither is an empty or contentless payload: `{}` from the analysis
    endpoint and `{"hits": []}` from search are both non-None, so a plain
    `is not None` test wrote them to disk and every later call read back the
    same refusal for the whole TTL - 30 days for ratios, 6 hours for search -
    out of one bad response. `useful` lets a caller say what "contentless"
    means for its own endpoint.
    """
    if value is None or not value:
        return False
    if useful is not None and not useful(value):
        return False
    return True


def _cached(key, ttl, produce, useful=None):
    """Run produce() unless a fresh cached value exists.

    Returns `(value, fetched_epoch)`. The fetch time is returned rather than
    left to the caller because the caller cannot tell a cache hit from a live
    call: stamping `retrieved` with today's date at return time claimed
    same-day retrieval of a payload that could be 30 days old under
    TTL_RATIOS. `fetched_epoch` is when the data actually left Avanza.

    Useless responses are not cached - see _worth_caching. On failure the
    value is None and the epoch is the moment of the failed attempt.
    """
    path = _cache_path(key)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            blob = json.load(fh)
        if time.time() - blob["t"] < ttl:
            return blob["v"], blob["t"]
    except (OSError, ValueError, KeyError):
        pass
    value = produce()
    fetched = time.time()
    if _worth_caching(value, useful):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"t": fetched, "v": value}, fh)
        except OSError:
            pass
    return value, fetched


def _has_hits(payload):
    """Search-payload usefulness: a hit list with something in it."""
    return isinstance(payload, dict) and bool(payload.get("hits"))


def _has_key(field):
    """Build a usefulness test for a payload keyed on one non-empty field."""
    def check(payload):
        return isinstance(payload, dict) and bool(payload.get(field))
    return check


def _utc_instant(epoch):
    """Render an epoch as a UTC ISO-8601 instant, e.g. 2026-09-06T07:41:12Z.

    An instant, not a date: the series dates below are Stockholm calendar
    dates, and a bare machine-local `retrieved` date sitting next to them
    cannot be compared with anything. The Z is explicit so nobody has to guess.
    """
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _http_json(url, payload=None, timeout=40):
    """GET or POST JSON, returning parsed result or None on any failure."""
    headers = {
        "User-Agent": UA,
        "Accept": "application/json",
    }
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            blob = r.read()
        return json.loads(blob.decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            OSError, ValueError):
        return None


# Stockholm is UTC+1 in winter and UTC+2 in summer, and Avanza stamps these
# series at local midnight. A flat two-hour shift lands inside the Stockholm
# calendar day under both offsets (22:00Z -> 00:00, 23:00Z -> 01:00) and never
# crosses into the following one, so no zoneinfo lookup and no local clock is
# involved. See _epoch_ms_to_iso.
_STOCKHOLM_SHIFT = datetime.timedelta(hours=2)


def _epoch_ms_to_iso(epoch_ms):
    """Convert epoch milliseconds to the Stockholm calendar date, or None.

    Never datetime.date.fromtimestamp(), which resolves against the machine's
    local zone and yields a series whose dates depend on which box fetched it -
    a disagreement that is silent, because both answers look right.

    Reading the stamp as UTC removes the machine dependence but is
    systematically one day early, and that is not an acceptable trade for a
    series whose whole job is to line up against a dated FI or filing figure.
    Measured on the live endpoints: every stamp lands at 22:00:00Z (summer) or
    23:00:00Z (winter), and both encode Stockholm midnight, so the day the
    issuer and the register would both call it is the *following* UTC day.
    Adding the fixed offset above is exactly as reproducible as reading UTC -
    it depends on no local clock and no tz database - and it is also correct,
    so there is no trade-off to make here.

    A plausibility window is applied, and it is not decoration. The realistic
    corruption here is a SECONDS value reaching a millisecond parameter:
    1696543200 read as milliseconds is 1970-01-20, which converts cleanly and
    is returned as a confident, wrong date at the front of a price or
    short-interest series. Anything outside 1990..now+2y is treated as
    unparseable rather than rendered. Every caller counts what it drops and
    returns the tally beside the series, so a gap is visible as a gap.
    """
    if epoch_ms is None:
        return None
    try:
        moment = datetime.datetime.fromtimestamp(
            epoch_ms / 1000.0, datetime.timezone.utc) + _STOCKHOLM_SHIFT
    except (ValueError, OSError, OverflowError, TypeError):
        return None
    year = moment.year
    if year < 1990 or year > datetime.datetime.now(datetime.timezone.utc).year + 2:
        return None
    return moment.date().isoformat()


def _number(value):
    """Return `value` as a float if it really is a number, else None.

    bool is excluded on purpose: True is an int in Python and would score as
    1.0. This exists because the endpoints are untyped JSON - a string in a
    `ratio` field reached round() and raised TypeError, and a string in
    `numberOfOwners` reached int() and raised ValueError, both escaping as a
    traceback instead of the repo's DATA NOT AVAILABLE contract.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


# --------------------------------------------------------------------------
# Name resolution
# --------------------------------------------------------------------------

# Suffixes Avanza appends for share classes, and legal forms its index does not
# carry. Both are stripped before matching so "Atlas Copco" reaches "Atlas
# Copco B", while the score still prefers the closer name. These mirror
# ir_discovery.score_candidate(); they are restated rather than imported
# because this module deliberately imports no siblings.
CLASS_SUFFIX = re.compile(r"\s+(?:[A-D]|Pref(?:\s*[A-D])?|SDB|BTA|TR)$", re.I)
LEGAL_SUFFIX = re.compile(
    r"\b(?:ab|abp|a/s|asa|oyj|plc|nv|se|group|publ)\b\.?", re.I)

# The markets this plugin covers. Avanza's search is global and returns US
# lines for Nordic queries - "Pricer" comes back with T. Rowe Price Group and
# Mattel alongside Pricer B - so flagCode has to do some work rather than be
# read and discarded. It breaks a tie in favour of the local listing, and it is
# part of the issuer identity below, so a same-named issuer in another market
# is a genuinely distinct issuer and still refuses. The endpoint's searchFilter
# is only documented for `types`, so the market is handled in the ranking
# rather than guessed at in the request.
COVERED_MARKETS = ("SE", "NO", "DK", "FI", "IS", "DE", "FR")

# Confidence floor, the same value ir_discovery uses. Below it the closest hit
# is not a match at all, and taking it would be a guess.
MIN_SCORE = 25.0


def _normalise_name(name):
    s = (name or "").lower()
    s = LEGAL_SUFFIX.sub(" ", s)
    s = re.sub(r"[^a-z0-9à-ÿ ]+", " ", s)
    return " ".join(s.split())


def _parse_hit(hit):
    """Split an Avanza search hit into name, ticker, id and market."""
    title = hit.get("title") or ""
    # "Atlas Copco B (ATCO B)" -> name "Atlas Copco B", ticker "ATCO B"
    m = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", title)
    name, ticker = (m.group(1), m.group(2)) if m else (title, "")
    return {
        "orderbook_id": str(hit.get("orderBookId")),
        "name": name,
        "ticker": ticker,
        "country": hit.get("flagCode"),
        "title": title or name,
    }


def _issuer_key(cand):
    """Identity of the *issuer* behind one listed line.

    Share class stripped, legal form stripped, market kept. This is what makes
    "Atlas Copco A" and "Atlas Copco B" one company rather than two - a false
    refusal on two classes of one issuer is a real defect - while keeping a
    same-named foreign listing separate, which is a real ambiguity.
    """
    return (_normalise_name(CLASS_SUFFIX.sub("", cand["name"])), cand["country"])


def _score_candidate(query, cand):
    """Rank Avanza hits against the query.

    The same shape as ir_discovery.score_candidate(): an exact match on the
    class-stripped name outranks everything, then a prefix match, then a
    token-overlap fraction. A covered market and a trailing B break the
    remaining ties, because B is the class an index quotes for a Nordic
    dual-class name.
    """
    q = _normalise_name(query)
    base = _normalise_name(CLASS_SUFFIX.sub("", cand["name"]))
    full = _normalise_name(cand["name"])
    if q and (q == base or q == full):
        s = 100.0
    elif base and q and (base.startswith(q) or q.startswith(base)):
        s = 70.0 - abs(len(base) - len(q))
    else:
        qt, bt = set(q.split()), set(base.split())
        s = 40.0 * len(qt & bt) / max(1, len(qt | bt))
    if cand.get("country") in COVERED_MARKETS:
        s += 3
    if re.search(r"\bB$", cand["name"] or ""):
        s += 1
    return s


def resolve_candidate(name):
    """Resolve a company name to one Avanza listing.

    Ranks the hits and picks, rather than refusing whenever more than one row
    comes back. The previous version deduplicated on `(flagCode, orderBookId)`,
    which collapses nothing at all - orderBookId is already unique per hit - so
    every multi-hit search refused: "Pricer" refused against Pricer B plus two
    unrelated US lines, and "Atlas Copco" refused against its own A and B
    classes plus Epiroc. Both are wrong, and the second is exactly the false
    refusal on two share classes of one issuer that the repo treats as a
    defect. The module's own usage example did not work.

    Refusal is kept for the case it exists for: two genuinely distinct issuers,
    both plausible matches. "Volvo" still refuses against AB Volvo and Volvo
    Car AB.

    Returns a dict: orderbook_id, name, ticker, country, title, score, plus the
    tier-4 markers. Raises SystemExit("DATA NOT AVAILABLE: ...") on no hits, an
    unusable payload, a best match below the confidence floor, or two distinct
    issuers in contention.
    """
    if not name:
        raise SystemExit("DATA NOT AVAILABLE: empty company name")

    def produce():
        return _http_json(AVANZA_SEARCH,
                          {"query": name, "searchFilter": {"types": ["STOCK"]}})

    data, fetched = _cached("search:" + name.lower(), TTL_SEARCH, produce,
                            useful=_has_hits)
    if data is None:
        raise SystemExit("DATA NOT AVAILABLE: Avanza search failed")
    if not isinstance(data, dict):
        raise SystemExit("DATA NOT AVAILABLE: unexpected Avanza search payload "
                         "shape (%s)" % type(data).__name__)

    hits = data.get("hits") or []
    if not isinstance(hits, list) or not hits:
        raise SystemExit("DATA NOT AVAILABLE: no match for %r" % name)

    candidates = [_parse_hit(h) for h in hits
                  if isinstance(h, dict) and h.get("orderBookId")]
    if not candidates:
        raise SystemExit("DATA NOT AVAILABLE: no valid hits for %r" % name)

    for cand in candidates:
        cand["score"] = _score_candidate(name, cand)
    candidates.sort(key=lambda c: -c["score"])
    best = candidates[0]

    if best["score"] < MIN_SCORE:
        raise SystemExit(
            "DATA NOT AVAILABLE: closest Avanza match for %r is %r, too weak "
            "to trust. Give the listed name." % (name, best["title"]))

    # Only hits that clear the floor can be in contention. A US line sharing no
    # name tokens with a Nordic query scores 0 and is not evidence of
    # ambiguity - it is evidence that the search is global.
    contenders = [c for c in candidates if c["score"] >= MIN_SCORE]
    issuers = {}
    for cand in contenders:
        issuers.setdefault(_issuer_key(cand), []).append(cand)

    if len(issuers) > 1:
        names = ", ".join(sorted(group[0]["name"] or group[0]["ticker"]
                                 for group in issuers.values()))
        raise SystemExit(
            "DATA NOT AVAILABLE: %d distinct issuers match %r (%s). "
            "Specify the full legal name to disambiguate."
            % (len(issuers), name, names))

    best["source"] = "Avanza search (broker redistribution, not primary source)"
    best["source_tier"] = 4
    best["usage"] = "identifier resolution only - never a cited figure"
    best["retrieved"] = _utc_instant(fetched)
    return best


def resolve_orderbook_id(name):
    """Resolve a company name to Avanza's orderBookId (a string).

    Thin wrapper over resolve_candidate(). The return value is Avanza's own
    internal identifier for a listing, not a datum - there is nothing in it to
    cite - so it carries no tier marker of its own; resolve_candidate() returns
    the same match with the tier-4 markers attached.
    """
    return resolve_candidate(name)["orderbook_id"]


def short_selling(orderbook_id):
    """Avanza short-selling history for one orderBookId.

    What this is, stated carefully because the careless version is dangerous:
    a ratio series published by Avanza. Avanza does not say where it derives
    it, and this module must not say either. It plausibly traces back to
    Finansinspektionen's Blankningsregistret, but "plausibly" is not
    provenance, and writing FI's name on a tier-4 number would let it inherit
    FI's tier-1 authority in a reader's head - the exact laundering the tier
    system exists to prevent.

    FI's register remains the authority for Swedish short interest, via
    short_se.py. Use this series to ask a cheap question ("has it moved at
    all since the last review?") and let a fired answer escalate to short_se
    for the figure that is actually cited.

    Returns a dict with keys:
        data: list of {date, ratio_pct} dicts, newest first
        dropped: per-reason count of entries that did not survive parsing
        source: string explaining tier and usage
        source_tier: 4
        usage: string explaining cross-check-only constraint
        retrieved: UTC instant the payload actually left Avanza

    Raises SystemExit("DATA NOT AVAILABLE: ...") on network failure or
    missing data.
    """
    if not orderbook_id:
        raise SystemExit("DATA NOT AVAILABLE: empty orderBook ID")

    def produce():
        url = AVANZA_SHORT_SELLING % urllib.parse.quote(str(orderbook_id))
        return _http_json(url)

    data, fetched = _cached("short:" + str(orderbook_id), TTL_SHORT_SELLING,
                            produce, useful=_has_key("shortSellingHistory"))
    if data is None:
        raise SystemExit("DATA NOT AVAILABLE: Avanza short-selling endpoint failed")
    if not isinstance(data, dict):
        raise SystemExit("DATA NOT AVAILABLE: unexpected short-selling payload "
                         "shape (%s)" % type(data).__name__)

    history = data.get("shortSellingHistory") or []
    if not isinstance(history, list) or not history:
        raise SystemExit("DATA NOT AVAILABLE: no short-selling history available")

    # Convert epoch-millisecond stamps to Stockholm dates, then sort newest
    # first. This series arrives OLDEST first - measured on Pricer B, 913
    # points, index 0 rendering 2023-10-06 and the last one 2026-09-05. An
    # earlier comment here asserted the opposite and did not sort, so `data[0]`
    # was the oldest observation while the docstring promised the newest, and a
    # caller asking "has short interest moved since the last review?" read a
    # three-year-old ratio as today's. The sort is the guarantee; the arrival
    # order is only an observation, and the sort is idempotent under either.
    dropped = {"malformed_entry": 0, "missing_field": 0,
               "unparseable_timestamp": 0, "non_numeric_ratio": 0,
               "implausible_ratio": 0}
    out = []
    for entry in history:
        if not isinstance(entry, dict):
            dropped["malformed_entry"] += 1
            continue
        ts = entry.get("timestamp")
        raw_ratio = entry.get("ratio")
        if ts is None or raw_ratio is None:
            dropped["missing_field"] += 1
            continue
        date_str = _epoch_ms_to_iso(ts)
        if date_str is None:
            dropped["unparseable_timestamp"] += 1
            continue
        ratio = _number(raw_ratio)
        if ratio is None:
            dropped["non_numeric_ratio"] += 1
            continue
        if ratio < 0 or ratio > MAX_SHORT_RATIO:
            dropped["implausible_ratio"] += 1
            continue
        out.append({
            "date": date_str,
            "ratio_pct": round(ratio * 100, 4),  # ratio as 0.0079 -> 0.79%
        })

    out.sort(key=lambda r: r["date"], reverse=True)

    if not out:
        raise SystemExit("DATA NOT AVAILABLE: no usable entries in short-selling "
                         "history (%d dropped: %s)"
                         % (sum(dropped.values()), _dropped_text(dropped)))

    return {
        "data": out,
        "dropped": dropped,
        "source": "Avanza market-guide (broker redistribution, not primary source)",
        # Addressed per orderBookId like the owners series, but this one is
        # genuinely per ISSUER: measured, both Handelsbanken classes report the
        # identical 0.0375, because short positions are disclosed against the
        # issuer. Recorded so nobody has to re-derive which series is which.
        "instrument": {"orderbook_id": str(orderbook_id),
                       "scope": "issuer (both share classes report alike)"},
        "source_tier": 4,
        "usage": "cross-check only - never a primary source",
        "retrieved": _utc_instant(fetched),
    }


def key_ratios(orderbook_id):
    """Avanza key ratios and financial series for one orderBookId.

    This endpoint provides Avanza's own computed ratios and financial history
    based on reported figures. It does NOT provide analyst estimates or
    price targets (verified by inspection).

    The raw payload contains several series families:
        stockKeyRatiosByYear / ByQuarter / ByQuarterTTM / ByQuarterQuarter
        companyKeyRatiosByYear / ByQuarter / ByQuarterTTM / ByQuarterQuarter
        companyFinancialsByYear / ByQuarter / ByQuarterTTM / ByQuarterQuarter
        dividendsByYear
        keyRatiosByYear / ByQuarter / ByQuarterTTM / ByQuarterQuarter

    Each family is a DICT of metric name -> list of entries, e.g.
    companyFinancialsByYear = {"sales": [{...}, {...}], "ebit": [...]}. It is
    not a bare list; an earlier version called dict() straight over the family
    and crashed on every real response.

    Series entries look like:
        {date: "2024-10-24", reportType: "Q3", financialYear: 2024, value: 17.05}

    Note: 'date' field is ABSENT on older entries - those rows are passed
    through as they are, because they are the earliest history.

    The keyRatios* families are DIFFERENT: they are {metric: {latest, average}}
    summary structures, not series. Do not conflate them with the By*/ByYear
    time series.

    Returns a dict with keys:
        data: the /analysis payload exactly as Avanza returned it
        source: string explaining tier and usage
        source_tier: 4
        usage: string explaining cross-check-only constraint
        retrieved: UTC instant the payload actually left Avanza

    Raises SystemExit("DATA NOT AVAILABLE: ...") on network failure.
    """
    if not orderbook_id:
        raise SystemExit("DATA NOT AVAILABLE: empty orderBook ID")

    def produce():
        url = AVANZA_ANALYSIS % urllib.parse.quote(str(orderbook_id))
        return _http_json(url)

    data, fetched = _cached("analysis:" + str(orderbook_id), TTL_RATIOS, produce)
    if data is None:
        raise SystemExit("DATA NOT AVAILABLE: Avanza analysis endpoint failed")
    if not isinstance(data, dict):
        raise SystemExit("DATA NOT AVAILABLE: unexpected analysis payload "
                         "shape (%s)" % type(data).__name__)
    if not data:
        raise SystemExit("DATA NOT AVAILABLE: empty analysis payload")

    # The payload is passed through untouched, deliberately. This function used
    # to deep-copy every family looking for `timestamp` fields to render as
    # ISO. Measured against the live response: there are ZERO `timestamp`
    # fields at any depth - entries carry `date`, already an ISO string, plus
    # reportType, financialYear and value. The walk rebuilt roughly 850 dicts,
    # changed none of them, and let the docstring promise a normalisation that
    # never happened. Copying data and calling the copy normalised is worse
    # than not copying it, because the claim outlives the code. Should Avanza
    # ever add an epoch field here, _epoch_ms_to_iso is the function to reach
    # for, and that change belongs behind a measurement, not an assumption.

    return {
        "data": data,
        "source": "Avanza market-guide (broker redistribution, not primary source)",
        "source_tier": 4,
        "usage": "cross-check only - never a primary source",
        "note": "This endpoint does NOT provide analyst estimates or price targets",
        "retrieved": _utc_instant(fetched),
    }


def number_of_owners(orderbook_id):
    """Avanza owner-count history for one orderBookId.

    An owner-count series published by Avanza. Whose count it is, is NOT
    stated by the endpoint and must not be asserted here. The cadence argues
    against any share register: measured on the live series, 564 of 571
    inter-point gaps are exactly seven days across eleven years, which is a
    scheduled internal count and not the irregular rhythm of registrar
    snapshots. It could as easily be Avanza's own customer base. Naming a
    registrar nobody has verified would dress a tier-4 figure as a registry
    fact, so the emitted `source` names no origin at all.

    PER SHARE CLASS, NOT PER ISSUER - and that distinction is measured, not
    assumed. Handelsbanken's two classes carry 136720 owners on the A line
    (orderBookId 5264) and 35432 on the B line (5265): a factor of four for
    what a caller might reasonably have read as one company's figure. Short
    interest behaves the opposite way - both Handelsbanken classes report the
    identical 0.0375, because short positions are disclosed per issuer - so a
    resolver that collapses share classes to one issuer is right for that
    series and wrong for this one.

    The collapse is therefore not undone (a false refusal between two classes
    of one issuer is a defect in this codebase), but every return names the
    instrument it actually read. A caller comparing owner counts across
    holdings MUST check `instrument`: comparing an A-line count with a B-line
    count is comparing two different populations.

    Treat it as a direction, not a level - a rising or falling trend in
    ownership breadth. For an ownership figure that enters an analysis,
    ownership_se.py reads FI's Fondinnehav and the annual report's ownership
    table, and those remain the authority.

    Returns a dict with keys:
        data: list of {date, owners} dicts, newest first
        history_summary: Avanza's own one-year and year-to-date change fields,
            passed through when present - the direct answer to the "has it
            moved?" question this series is kept for, previously discarded
        dropped: per-reason count of entries that did not survive parsing
        source: string explaining tier and usage
        source_tier: 4
        usage: string explaining cross-check-only constraint
        retrieved: UTC instant the payload actually left Avanza

    Raises SystemExit("DATA NOT AVAILABLE: ...") on network failure or
    missing data.
    """
    if not orderbook_id:
        raise SystemExit("DATA NOT AVAILABLE: empty orderBook ID")

    def produce():
        url = AVANZA_OWNERS % urllib.parse.quote(str(orderbook_id))
        return _http_json(url)

    data, fetched = _cached("owners:" + str(orderbook_id), TTL_OWNERS, produce,
                            useful=_has_key("ownersPoints"))
    if data is None:
        raise SystemExit("DATA NOT AVAILABLE: Avanza number-of-owners endpoint failed")
    if not isinstance(data, dict):
        raise SystemExit("DATA NOT AVAILABLE: unexpected number-of-owners payload "
                         "shape (%s)" % type(data).__name__)

    points = data.get("ownersPoints") or []
    if not isinstance(points, list) or not points:
        raise SystemExit("DATA NOT AVAILABLE: no owner-count history available")

    dropped = {"malformed_entry": 0, "missing_field": 0,
               "unparseable_timestamp": 0, "non_numeric_count": 0,
               "implausible_count": 0}
    out = []
    for entry in points:
        if not isinstance(entry, dict):
            dropped["malformed_entry"] += 1
            continue
        ts = entry.get("timestamp")
        raw_count = entry.get("numberOfOwners")
        if ts is None or raw_count is None:
            dropped["missing_field"] += 1
            continue
        date_str = _epoch_ms_to_iso(ts)
        if date_str is None:
            dropped["unparseable_timestamp"] += 1
            continue
        count = _number(raw_count)
        if count is None:
            dropped["non_numeric_count"] += 1
            continue
        # A headcount is a whole number and a bounded one. A fractional or
        # out-of-range value is corruption, not a very small owner base.
        if count < 0 or count > MAX_OWNERS or count != int(count):
            dropped["implausible_count"] += 1
            continue
        out.append({
            "date": date_str,
            "owners": int(count),
        })

    # Newest first, matching short_selling and the docstring. This series
    # arrives NEWEST first - measured on the live endpoint, ownersPoints[0]
    # renders 2026-08-31 and the last point 2015-09-16 - which is the opposite
    # of short_selling's arrival order. The output was right either way because
    # sorting is idempotent, but the comment that used to sit here asserted
    # oldest-first as though it had been measured. It had not been.
    out.sort(key=lambda r: r["date"], reverse=True)

    if not out:
        raise SystemExit("DATA NOT AVAILABLE: no usable entries in owner-count "
                         "history (%d dropped: %s)"
                         % (sum(dropped.values()), _dropped_text(dropped)))

    result = {
        "data": out,
        "dropped": dropped,
        # Which line this is. The endpoint is addressed per orderBookId, and
        # for this series the two classes of one issuer are different numbers
        # (see the docstring's measured example), so the identity travels with
        # the data rather than being left to whatever the caller thinks it
        # asked for.
        "instrument": {"orderbook_id": str(orderbook_id),
                       "scope": "share class, not issuer"},
        "source": "Avanza market-guide (broker redistribution, not primary source)",
        "source_tier": 4,
        "usage": "cross-check only - never a primary source",
        "retrieved": _utc_instant(fetched),
    }
    summary = data.get("historySummary")
    if isinstance(summary, dict) and summary:
        result["history_summary"] = summary
    return result


def _dropped_text(dropped):
    """Render a drop tally, or 'none' - never an empty string in a message."""
    parts = ["%s=%d" % (k, v) for k, v in sorted(dropped.items()) if v]
    return ", ".join(parts) if parts else "none"


def _count_entries(data):
    """Describe the size of a returned payload for the text output.

    The old text output printed `entries: 1` for the whole analysis payload,
    because it counted a dict as one thing. Seventeen families of series are
    not one entry, and a reader of that line would conclude the endpoint had
    returned almost nothing.
    """
    if isinstance(data, list):
        return "entries: %d" % len(data)
    if isinstance(data, dict):
        families = metrics = entries = 0
        for family in data.values():
            families += 1
            if isinstance(family, dict):
                for series in family.values():
                    metrics += 1
                    if isinstance(series, list):
                        entries += len(series)
            elif isinstance(family, list):
                entries += len(family)
        return "families: %d, metrics: %d, entries: %d" % (families, metrics, entries)
    return "entries: n/a"


def _guarded(label, fn, *args):
    """Call one endpoint, turning every failure into the refusal contract.

    SystemExit is the repo's hard-failure signal and passes through as its
    message. Anything else is a bug, or a payload shape nobody predicted, and
    it must still leave the process saying DATA NOT AVAILABLE rather than
    printing a traceback the caller cannot parse. Catching only SystemExit
    meant a string `ratio`, a string `numberOfOwners` and a top-level JSON
    array each produced a raw TypeError, ValueError and AttributeError.
    """
    try:
        return fn(*args)
    except SystemExit as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - deliberate contract boundary
        return {"error": "%s: %s failed unexpectedly (%s: %s)"
                         % (NA, label, type(exc).__name__, exc)}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else "Avanza market endpoints",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("company", nargs="?", help="company name to query")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="output as JSON")
    ap.add_argument("--selftest", action="store_true",
                    help="run offline self-tests")
    args = ap.parse_args()

    if args.selftest:
        return 0 if _selftest() else 1

    if not args.company:
        ap.error("give a company name, or --selftest")

    match = _guarded("name resolution", resolve_candidate, args.company)
    if "error" in match:
        if args.as_json:
            print(json.dumps({"error": match["error"], "data": None}, indent=2,
                             ensure_ascii=False))
        else:
            print("%s: %s" % (args.company, match["error"]))
        return 1

    orderbook_id = match["orderbook_id"]

    results = {
        "short_selling": _guarded("short_selling", short_selling, orderbook_id),
        "key_ratios": _guarded("key_ratios", key_ratios, orderbook_id),
        "number_of_owners": _guarded("number_of_owners", number_of_owners,
                                     orderbook_id),
    }

    output = {
        "company": args.company,
        "resolved": {k: match.get(k) for k in
                     ("title", "name", "ticker", "country", "orderbook_id",
                      "score", "source", "source_tier", "usage", "retrieved")},
        "orderbook_id": orderbook_id,
        "results": results,
    }

    if args.as_json:
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print("Company: %s -> %s [%s] (orderbook ID %s)"
              % (args.company, match.get("title"), match.get("country"),
                 orderbook_id))
        print()
        for endpoint, result in results.items():
            print("  %s:" % endpoint)
            if "error" in result:
                print("    ERROR: %s" % result["error"])
                continue
            print("    source_tier: %s" % result.get("source_tier"))
            print("    retrieved: %s" % result.get("retrieved"))
            if "data" in result:
                print("    %s" % _count_entries(result["data"]))
            dropped = result.get("dropped")
            if dropped:
                print("    dropped: %d (%s)"
                      % (sum(dropped.values()), _dropped_text(dropped)))
            if result.get("history_summary"):
                print("    history_summary: %s"
                      % json.dumps(result["history_summary"], ensure_ascii=False))
        print()

    return 0


def _selftest():
    """Offline tests: date anchoring, drop accounting, caching, resolution.

    Fully offline - no network call. `_http_json` and `CACHE_DIR` are replaced
    and restored in a finally block, so a failure cannot leave the module
    pointing at a scratch directory. Returns False rather than raising on a
    failed assertion, so the CLI exits 1 with a readable line instead of a
    traceback, and the caller's `else 1` branch is reachable.
    """
    ok = 0
    tmp = tempfile.mkdtemp(prefix="avanza_market_selftest_")
    global _http_json, CACHE_DIR
    real_http, real_cache = _http_json, CACHE_DIR
    CACHE_DIR = tmp
    try:
        # -- epoch-ms to the Stockholm calendar date -------------------------
        # 1696543200000 is 2023-10-05T22:00:00Z, which is Stockholm midnight on
        # the 6th - the day FI and the issuer would both call it. Reading it as
        # UTC gave the 5th, one day early on every point of every series.
        assert _epoch_ms_to_iso(1696543200000) == "2023-10-06", "summer offset (22:00Z)"
        ok += 1
        # 2024-01-04T23:00:00Z is Stockholm midnight on the 5th, in winter.
        assert _epoch_ms_to_iso(1704409200000) == "2024-01-05", "winter offset (23:00Z)"
        ok += 1
        # The shift must not overshoot. A stamp that is not at local midnight
        # keeps its own date: 1729696800000 is 2024-10-23T15:20:00Z, which is
        # mid-afternoon in Stockholm on the same day.
        assert _epoch_ms_to_iso(1729696800000) == "2024-10-23", "midday stamp keeps its date"
        ok += 1
        assert _epoch_ms_to_iso(None) is None, "None input"
        ok += 1
        # Outside the plausibility window -> None, not a rendered 1970 date.
        # Epoch 0 and negatives are corrupt input for a market-data series, and
        # the third case is the one that actually happens: a SECONDS value
        # handed to a millisecond parameter, which converts cleanly to
        # 1970-01-20 and would otherwise head the series as a confident wrong
        # date.
        assert _epoch_ms_to_iso(0) is None, "epoch 0 is corrupt, not 1970-01-01"
        ok += 1
        assert _epoch_ms_to_iso(-1000) is None, "negative epoch (invalid)"
        ok += 1
        assert _epoch_ms_to_iso(1696543200) is None, "seconds mistaken for milliseconds"
        ok += 1
        assert _epoch_ms_to_iso("2023-10-05") is None, "a string is not an epoch"
        ok += 1

        # -- numeric guard ----------------------------------------------------
        assert _number("0.0079") is None, "a string is not a number"
        ok += 1
        assert _number(True) is None, "bool is not a measurement"
        ok += 1
        assert _number(3) == 3.0, "int accepted"
        ok += 1

        # -- caching: nothing useless is remembered ---------------------------
        empty_calls = []

        def produce_empty():
            empty_calls.append(1)
            return {}

        _cached("selftest-empty", 600, produce_empty)
        _cached("selftest-empty", 600, produce_empty)
        assert len(empty_calls) == 2, "an empty payload must not stick in the cache"
        ok += 1

        nohit_calls = []

        def produce_nohits():
            nohit_calls.append(1)
            return {"hits": []}

        _cached("selftest-nohits", 600, produce_nohits, useful=_has_hits)
        _cached("selftest-nohits", 600, produce_nohits, useful=_has_hits)
        assert len(nohit_calls) == 2, "a hitless search must not stick in the cache"
        ok += 1

        good_calls = []

        def produce_good():
            good_calls.append(1)
            return {"hits": [{"orderBookId": "1"}]}

        _, first_t = _cached("selftest-good", 600, produce_good, useful=_has_hits)
        value, second_t = _cached("selftest-good", 600, produce_good, useful=_has_hits)
        assert len(good_calls) == 1, "a useful payload is cached"
        ok += 1
        # The real fetch time survives the cache hit. Recomputing it at return
        # time let a 30-day-old payload claim same-day retrieval.
        assert second_t == first_t, (first_t, second_t)
        ok += 1
        assert value["hits"], value
        ok += 1

        # -- the three endpoints, called against a fake transport -------------
        #
        # An earlier version of this selftest asserted against inspect.getsource()
        # - it grepped its own text for '"source_tier": 4' and passed. That test
        #   cannot fail for any reason a caller would care about: it stays green
        #   if the function returns None, raises, or drops the marker at runtime
        #   while leaving the literal in a comment. These call the functions.
        payloads = {
            "short-selling": {"shortSellingHistory": [
                {"timestamp": 1696543200000, "ratio": 0.0079},
                {"timestamp": 1696629600000, "ratio": 0.0081},
                # Three rows that must be dropped AND counted, never silently
                # vanished: a corrupt stamp, a string where a number belongs
                # (which used to raise TypeError out of round()), and a value
                # already expressed as a percentage, where *100 would print
                # 7900% as a measured short interest.
                {"timestamp": 1696543200, "ratio": 0.0080},
                {"timestamp": 1696716000000, "ratio": "0.0082"},
                {"timestamp": 1696802400000, "ratio": 79.0}]},
            "number-of-owners": {
                "ownersPoints": [
                    {"timestamp": 1788127200000, "numberOfOwners": 5543},
                    {"timestamp": 1787522400000, "numberOfOwners": "5600"}],
                "historySummary": {"oneYearChangePercent": -0.1027,
                                   "oneYearChange": -635}},
            # One entry deliberately has no `date` - the live endpoint omits it
            # on older rows, and dropping or crashing on those silently loses
            # the earliest history.
            "analysis": {"companyFinancialsByYear": {"sales": [
                {"reportType": "FULL_YEAR", "financialYear": 2016, "value": 757600000.0},
                {"date": "2019-02-14", "reportType": "FULL_YEAR",
                 "financialYear": 2018, "value": 1194500000.0}]}},
        }

        def fake_http(url, payload=None, timeout=40):
            for needle, body in payloads.items():
                if needle in url:
                    return body
            return None

        _http_json = fake_http

        ss = short_selling("5395")
        assert ss["source_tier"] == 4, ss
        ok += 1
        assert "cross-check" in ss["usage"].lower(), ss["usage"]
        ok += 1
        # Newest first, as the docstring promises. The fixture is given in the
        # order the live endpoint returns it - OLDEST first - so this fails if
        # the sort is ever dropped again.
        assert [r["date"] for r in ss["data"]] == ["2023-10-07", "2023-10-06"], ss["data"]
        ok += 1
        assert isinstance(ss["data"][0]["ratio_pct"], float), ss["data"][0]
        ok += 1
        assert ss["dropped"]["unparseable_timestamp"] == 1, ss["dropped"]
        ok += 1
        assert ss["dropped"]["non_numeric_ratio"] == 1, ss["dropped"]
        ok += 1
        assert ss["dropped"]["implausible_ratio"] == 1, ss["dropped"]
        ok += 1
        # retrieved is a UTC instant, not a machine-local date.
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
                        ss["retrieved"]), ss["retrieved"]
        ok += 1

        no = number_of_owners("5395")
        assert no["source_tier"] == 4, no
        ok += 1
        assert no["data"][0]["owners"] == 5543, no["data"][0]
        ok += 1
        assert no["dropped"]["non_numeric_count"] == 1, no["dropped"]
        ok += 1
        # No registrar, no register, no Euroclear in the emitted attribution:
        # the origin of this count has never been verified, and the weekly
        # cadence argues against the registrar reading in particular.
        lowered = (no["source"] + no["usage"]).lower()
        assert "registrar" not in lowered and "register" not in lowered \
            and "euroclear" not in lowered, no["source"]
        ok += 1
        assert no["history_summary"]["oneYearChange"] == -635, no["history_summary"]
        ok += 1

        kr = key_ratios("5395")
        assert kr["source_tier"] == 4, kr
        ok += 1
        # The payload is handed back exactly as received - identity, not a copy
        # that claims to have been normalised.
        assert kr["data"] is payloads["analysis"], "analysis payload must pass through"
        ok += 1
        sales = kr["data"]["companyFinancialsByYear"]["sales"]
        assert len(sales) == 2, sales
        ok += 1
        # The dateless row must survive rather than be dropped or crash.
        assert any(r.get("date") is None for r in sales), sales
        ok += 1

        # -- a payload shape nobody predicted must refuse, not traceback ------
        _http_json = lambda url, payload=None, timeout=40: ["unexpected"]  # noqa: E731
        for fn in (short_selling, key_ratios, number_of_owners):
            try:
                fn("selftest-array-%s" % fn.__name__)
                raise AssertionError("%s must refuse a top-level JSON array" % fn.__name__)
            except SystemExit as exc:
                assert NA in str(exc), str(exc)
            ok += 1

        # -- resolution: refuse real ambiguity, resolve share classes ---------
        def search_stub(hits):
            def stub(url, payload=None, timeout=40):
                return {"totalNumberOfHits": len(hits), "hits": hits}
            return stub

        # Two distinct issuers must refuse, not take the first hit.
        _http_json = search_stub([
            {"type": "STOCK", "title": "AB Volvo (VOLV B)",
             "orderBookId": "1", "flagCode": "SE"},
            {"type": "STOCK", "title": "Volvo Car AB (VOLCAR B)",
             "orderBookId": "2", "flagCode": "SE"}])
        try:
            resolve_orderbook_id("Volvo")
            raise AssertionError("resolve_orderbook_id must refuse an ambiguous name")
        except SystemExit as exc:
            assert "Volvo Car" in str(exc), str(exc)
        ok += 1

        # Two share classes of ONE issuer are one company. A refusal here is a
        # real defect, and it is what the old (flagCode, orderBookId) dedupe
        # produced for every multi-class Nordic name.
        _http_json = search_stub([
            {"type": "STOCK", "title": "Atlas Copco B (ATCO B)",
             "orderBookId": "5235", "flagCode": "SE"},
            {"type": "STOCK", "title": "Atlas Copco A (ATCO A)",
             "orderBookId": "5234", "flagCode": "SE"},
            {"type": "STOCK", "title": "Epiroc A (EPI A)",
             "orderBookId": "861430", "flagCode": "SE"},
            {"type": "STOCK", "title": "Epiroc B (EPI B)",
             "orderBookId": "861431", "flagCode": "SE"}])
        assert resolve_orderbook_id("Atlas Copco") == "5235", "share classes must resolve"
        ok += 1

        # A global search returning unrelated foreign lines is not ambiguity.
        _http_json = search_stub([
            {"type": "STOCK", "title": "Pricer B (PRIC B)",
             "orderBookId": "5395", "flagCode": "SE"},
            {"type": "STOCK", "title": "T. Rowe Price Group (TROW)",
             "orderBookId": "4390", "flagCode": "US"},
            {"type": "STOCK", "title": "Mattel (MAT)",
             "orderBookId": "303716", "flagCode": "US"}])
        assert resolve_orderbook_id("Pricer") == "5395", "unrelated hits must not refuse"
        ok += 1

        # The same name listed in two markets IS two issuers. A different query
        # string from the case above, because the search cache is keyed on it.
        _http_json = search_stub([
            {"type": "STOCK", "title": "Meridian B (MER B)",
             "orderBookId": "111", "flagCode": "SE"},
            {"type": "STOCK", "title": "Meridian Inc (MRDN)",
             "orderBookId": "222", "flagCode": "US"}])
        try:
            resolve_orderbook_id("Meridian")
            raise AssertionError("the same name in two markets must refuse")
        except SystemExit as exc:
            assert "distinct issuers" in str(exc), str(exc)
        ok += 1

        # Nothing close enough must refuse rather than take the least bad hit.
        _http_json = search_stub([
            {"type": "STOCK", "title": "Mattel (MAT)",
             "orderBookId": "303716", "flagCode": "US"}])
        try:
            resolve_orderbook_id("Sandvik")
            raise AssertionError("a weak best match must refuse")
        except SystemExit as exc:
            assert "too weak" in str(exc), str(exc)
        ok += 1
    except AssertionError as exc:
        print("avanza_market selftest FAILED after %d assertions: %s" % (ok, exc))
        return False
    finally:
        _http_json, CACHE_DIR = real_http, real_cache
        shutil.rmtree(tmp, ignore_errors=True)

    print("avanza_market selftest: %d assertions passed" % ok)
    return True


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - the CLI must never print a traceback
        print("%s: avanza_market failed (%s: %s)" % (NA, type(exc).__name__, exc))
        sys.exit(1)
