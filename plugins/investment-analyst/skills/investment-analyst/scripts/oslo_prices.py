#!/usr/bin/env python3
"""Daily bar series for Oslo Børs instruments via Euronext + Yahoo Finance.

The value screen computes returns, drawdown, and turnover-based liquidity
floors from daily bars (date, close, volume). For Nasdaq Nordic markets,
nordic_shares.price_history() supplies these keyed on orderbookId. Oslo Børs
is Euronext, not Nasdaq, and Norwegian issuers lack orderbook IDs - they land
in cut:no_price_history before the screen runs.

This module supplies the missing leg: given an Oslo Børs ISIN, it:
  1. Resolves the ISIN to a ticker via Euronext's live search
  2. Fetches daily bars from Yahoo Finance via the .OL (Norway) suffix
  3. Caches both transformations to minimize round-trips

Usage:
    python oslo_prices.py --isin NO0010096985 --json
    python oslo_prices.py --ticker YAR --json
    python oslo_prices.py --selftest

Wired into the screen through market_universe: an Oslo row carries a
synthetic orderbook id (`OSLO:<isin>`) so the rest of the pipeline is
unchanged, and market_universe.fetch_oslo_bars() is the leg that fills it.
Free, keyless. Sources: Euronext live search, Yahoo Finance unofficial endpoint.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

UA = "Mozilla/5.0 (compatible; investment-analyst-skill/1.0)"

# Cache directory - reference data (ISIN->ticker) uses long TTL,
# daily bars use short TTL so they refresh each day.
CACHE_DIR = os.path.join(tempfile.gettempdir(), "investment-analyst-cache",
                         "oslo_prices")
TTL_TICKER = 604800      # 7 days - ISIN->ticker mappings don't change
TTL_BARS = 3600          # 1 hour - bars update daily, refresh hourly for coverage


def cache_path(key):
    """Cache file path: hash the full key to avoid filesystem issues."""
    return os.path.join(CACHE_DIR, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")


def cached(key, ttl, produce):
    """Run produce() unless a fresh cached value exists.

    WHY: ISIN->ticker mappings are stable and expensive to fetch; bars are
    updated daily and worth caching for an hour to reduce round-trips during
    a single screen run.

    RULE: None is not cached. A failed lookup must be retried on the next call,
    not remembered as an absence. This is the house rule for all resolvers in
    this toolkit - a source outage is not evidence that the data does not exist.
    """
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


def fetch_json(url, timeout=20):
    """Fetch and parse JSON, or return None on any error."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                    "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            json.JSONDecodeError, OSError):
        return None


# Oslo equity venues. DOSL (derivatives) is deliberately absent.
EQUITY_MICS = ("XOSL", "MERK")


def resolve_oslo_ticker(isin, _fetch=None):
    """Resolve an Oslo Børs ISIN to its ticker, or None.

    Queries Euronext's live instrument search for the ISIN, extracts the
    ticker symbol, and keeps only equity venues (XOSL, MERK).

    WHY THE FILTER: an ISIN query returns both equity and derivative rows.
    Measured for Equinor (NO0010096985): six rows, one XOSL equity carrying
    "EQNR" and five DOSL stock options carrying "EQN" and "1EQ". Taking the
    wrong symbol prices an option, not the share, so the filter is
    load-bearing and must run before the symbol is read.

    MERK is included because Euronext Growth Oslo is a separate MIC; filtering
    on XOSL alone would make every growth-market issuer unresolvable.

    Args:
        isin: Oslo Børs ISIN (e.g., "NO0010096985").
        _fetch: Injection point for testing; defaults to fetch_json.

    Returns:
        Ticker string (e.g., "YAR"), or None if the ISIN could not be resolved
        or no XOSL listing was found.
    """
    if _fetch is None:
        _fetch = fetch_json

    def produce():
        url = "https://live.euronext.com/instrumentSearch/searchJSON?q=" + urllib.parse.quote(isin)
        data = _fetch(url)
        if not data:
            return None

        # Measured shape: the endpoint answers with a BARE LIST of rows.
        # The dict form is accepted too - this is an unofficial endpoint and
        # the wrapper has been seen both ways - but a list is what it sends.
        if isinstance(data, dict):
            rows = data.get("instruments") or []
        elif isinstance(data, list):
            rows = data
        else:
            return None
        for row in rows:
            if not isinstance(row, dict) or row.get("mic") not in EQUITY_MICS:
                continue
            label = row.get("label") or ""
            # Label is HTML: "<span class='symbol'>YAR</span>..."
            # Extract the symbol from the span.
            match = re.search(r"<span\s+class=['\"]?symbol['\"]?>([^<]+)</span>", label)
            if match:
                return match.group(1).strip()
        return None

    return cached("oslo:ticker:" + isin.upper(), TTL_TICKER, produce)


def bars(ticker, range_="1y", _fetch=None):
    """Daily OHLCV bars for an Oslo Børs ticker, oldest first, or None.

    Queries Yahoo Finance's unofficial chart endpoint with the .OL (Norway)
    suffix and extracts the daily close and volume. Bars are returned in
    chronological order (oldest first).

    TIMEZONE: the response tells us the offset, so nothing has to be looked
    up. `meta.gmtoffset` carries the exchange's UTC offset in seconds for
    this response (measured: 7200 in summer, i.e. CEST), and adding it before
    taking the date yields the Oslo trading date.

    An earlier version used zoneinfo.ZoneInfo("Europe/Oslo"). That is stdlib,
    but the IANA database behind it is NOT shipped with Windows, so it raised
    ZoneInfoNotFoundError on the machine this toolkit runs on and the module
    would not even import its own selftest. The offset from the payload has
    no such dependency and is authoritative for the data it accompanies.

    Daily bars are stamped at market open - measured 07:00Z in summer, which
    is 09:00 Oslo - so the date is ~7 hours clear of either midnight. A single
    offset applied across a range that spans a DST change therefore still
    yields the correct calendar date for every bar; it would take an 8-hour
    error to move one, and the DST step is one hour.

    CURRENCY: each bar carries `currency` from `meta.currency` when the
    payload states it. Oslo Børs is mostly NOK, but several shipping and
    energy lines quote in USD or EUR, and converting a turnover figure with
    the wrong currency is wrong by a factor. When the payload omits it the
    field is omitted too - the caller refuses rather than assuming NOK.

    A bar with a null close or null volume is KEPT and counted, never silently
    dropped: Yahoo emits nulls for non-trading days inside a range, and a
    caller computing a return needs to know a gap was a gap. `drop_report()`
    tallies them.

    Args:
        ticker: Oslo Børs ticker without suffix (e.g., "YAR").
        range_: Yahoo range parameter (e.g., "1y", "6mo", "1d").
        _fetch: Injection point for testing; defaults to fetch_json.

    Returns:
        List of dicts {date: ISO date string, close: float or None,
        volume: int or None}, oldest first. None if the ticker could not
        be fetched or returned no bars.
    """
    if _fetch is None:
        _fetch = fetch_json

    def produce():
        symbol = ticker.strip().replace(" ", "-").upper() + ".OL"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range={range_}&interval=1d"
        data = _fetch(url)
        if not data:
            return None

        result = (data.get("chart") or {}).get("result")
        if not result or not result[0].get("timestamp"):
            return None

        timestamps = result[0]["timestamp"] or []
        quote = ((result[0].get("indicators") or {}).get("quote") or [{}])[0]
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        if not timestamps or len(closes) != len(timestamps) or len(volumes) != len(timestamps):
            return None

        # The exchange offset comes from the payload itself - see the
        # docstring for why this is not a zoneinfo lookup. Absent or
        # implausible, fall back to UTC and say so on the bar rather than
        # guessing an offset: a wrong offset silently shifts a trading date.
        meta = result[0].get("meta") or {}
        raw_offset = meta.get("gmtoffset")
        offset_known = isinstance(raw_offset, (int, float)) and -50400 <= raw_offset <= 50400
        offset = int(raw_offset) if offset_known else 0

        # The quote currency travels with the bars. Oslo Børs is mostly NOK
        # but not only - several shipping and energy lines quote in USD or
        # EUR - and a turnover figure converted with the wrong currency is
        # wrong by a factor, not a rounding. Absent, the field is simply
        # absent and the caller refuses; it is never defaulted here.
        ccy = meta.get("currency")
        ccy = ccy.upper() if isinstance(ccy, str) and ccy.strip() else None

        bars_out = []
        for ts, close, volume in zip(timestamps, closes, volumes):
            if not isinstance(ts, (int, float)):
                continue
            local = datetime.datetime.fromtimestamp(
                float(ts) + offset, datetime.timezone.utc)
            bar = {"date": local.date().isoformat(),
                   "close": close, "volume": volume}
            if ccy:
                bar["currency"] = ccy
            if not offset_known:
                bar["date_offset_unknown"] = True
            bars_out.append(bar)

        return bars_out if bars_out else None

    return cached("oslo:bars:" + ticker.upper() + ":" + range_, TTL_BARS, produce)


def bars_for_isin(isin, range_="1y", _fetch=None):
    """Resolve ISIN to ticker, fetch bars. Chained convenience function.

    Args:
        isin: Oslo Børs ISIN.
        range_: Yahoo range parameter.
        _fetch: Injection point for testing.

    Returns:
        Bars list, or None if ISIN could not be resolved or ticker has no bars.
    """
    ticker = resolve_oslo_ticker(isin, _fetch=_fetch)
    if ticker is None:
        return None
    return bars(ticker, range_=range_, _fetch=_fetch)


# --- CLI and self-test ---

def report_bars(bars_list):
    """Format a bars list for human-readable output."""
    if not bars_list:
        return "no bars"
    lines = []
    dropped = sum(1 for b in bars_list if b.get("close") is None or b.get("volume") is None)
    if dropped > 0:
        lines.append(f"  ({dropped} bar(s) with null close or volume, kept in output)")
    for b in bars_list[:5]:
        lines.append(f"    {b['date']}: close={b.get('close')}, volume={b.get('volume')}")
    if len(bars_list) > 5:
        lines.append(f"    ... {len(bars_list) - 5} more")
    return "\n".join(lines)


def _selftest():
    """Offline self-test: inject a fake HTTP transport, no network calls."""
    global CACHE_DIR
    ok = 0

    # Temporarily point cache at an isolated directory.
    real_cache = CACHE_DIR
    test_cache = tempfile.mkdtemp(prefix="oslo_prices_test_")
    CACHE_DIR = test_cache

    try:
        # --- XOSL/DOSL filter: options must not win ---
        def fake_euronext_with_options(url):
            # Simulate Euronext returning both equity and option rows.
            # Options for the same ISIN carry different symbols.
            return {
                "instruments": [
                    {"mic": "DOSL", "label": "<span class='symbol'>1YA</span> option"},
                    {"mic": "DOSL", "label": "<span class='symbol'>2YA</span> option"},
                    {"mic": "XOSL", "label": "<span class='symbol'>YAR</span> equity"},
                ]
            }

        ticker = resolve_oslo_ticker("NO0010096985", _fetch=fake_euronext_with_options)
        assert ticker == "YAR", f"XOSL filter failed: got {ticker}, expected YAR"
        ok += 1

        # --- Epoch conversion uses the payload's own UTC offset ---
        def fake_yahoo_with_bars(url):
            # Two bars either side of a DST change, each stamped 09:00 Oslo:
            # 08:00Z on 2026-01-15 (CET, +1) and 07:00Z on 2026-09-06 (CEST,
            # +2). meta carries the summer offset, which is what Yahoo sends
            # for a range spanning the boundary. Both must keep their own
            # calendar date.
            return {
                "chart": {
                    "result": [
                        {
                            "meta": {"gmtoffset": 7200},
                            "timestamp": [1768464000, 1788678000],
                            "indicators": {
                                "quote": [
                                    {
                                        "close": [111.11, 123.45],
                                        "volume": [500000, 1000000]
                                    }
                                ]
                            }
                        }
                    ]
                }
            }

        bars_list = bars("YAR", _fetch=fake_yahoo_with_bars)
        assert bars_list is not None and len(bars_list) == 2, f"bars failed: {bars_list}"
        assert [b["date"] for b in bars_list] == ["2026-01-15", "2026-09-06"],             f"date conversion failed: {[b['date'] for b in bars_list]}"
        assert bars_list[1]["close"] == 123.45, f"close mismatch: {bars_list[1]}"
        assert bars_list[1]["volume"] == 1000000, f"volume mismatch: {bars_list[1]}"
        assert "date_offset_unknown" not in bars_list[0],             f"offset was known, bar must not be flagged: {bars_list[0]}"
        ok += 1

        # --- A missing offset is flagged, never silently guessed ---
        def fake_yahoo_no_offset(url):
            return {"chart": {"result": [{
                "meta": {},
                "timestamp": [1788678000],
                "indicators": {"quote": [{"close": [1.0], "volume": [1]}]}}]}}

        nb = bars("NOOFF", _fetch=fake_yahoo_no_offset)
        assert nb and nb[0].get("date_offset_unknown") is True,             f"a date derived without a known offset must say so: {nb}"
        ok += 1

        # --- Null close bar must be kept and counted ---
        # Own ticker per case: bars() caches per ticker, so a shared one
        # would serve the previous case's response and this fixture would
        # never be called.
        def fake_yahoo_with_null_close(url):
            return {
                "chart": {
                    "result": [
                        {
                            "timestamp": [1725638400, 1725724800],
                            "indicators": {
                                "quote": [
                                    {
                                        "close": [123.45, None],  # second bar has null close
                                        "volume": [1000000, 500000]
                                    }
                                ]
                            }
                        }
                    ]
                }
            }

        bars_list = bars("NULLC", _fetch=fake_yahoo_with_null_close)
        assert bars_list is not None and len(bars_list) == 2, \
            f"null-close handling failed: expected 2 bars, got {len(bars_list) if bars_list else 0}"
        assert bars_list[0]["close"] == 123.45, f"first bar mismatch: {bars_list[0]}"
        assert bars_list[1]["close"] is None, f"second bar null not preserved: {bars_list[1]}"
        ok += 1

        # --- Empty result: no instruments ---
        def fake_euronext_empty(url):
            return {"instruments": []}

        ticker = resolve_oslo_ticker("FAKE", _fetch=fake_euronext_empty)
        assert ticker is None, f"empty result should return None, got {ticker}"
        ok += 1

        # --- Empty bars: no timestamps ---
        def fake_yahoo_empty(url):
            return {"chart": {"result": [{"timestamp": None}]}}

        bars_list = bars("FAKE", _fetch=fake_yahoo_empty)
        assert bars_list is None, f"empty bars should return None, got {bars_list}"
        ok += 1

        # --- Chained function: resolve ISIN -> bars ---
        call_count = {"euronext": 0, "yahoo": 0}

        def fake_euronext_for_chain(url):
            call_count["euronext"] += 1
            return {
                "instruments": [
                    {"mic": "XOSL", "label": "<span class='symbol'>TEST</span>"}
                ]
            }

        def fake_yahoo_for_chain(url):
            call_count["yahoo"] += 1
            return {
                "chart": {
                    "result": [
                        {
                            "timestamp": [1725638400],
                            "indicators": {
                                "quote": [
                                    {"close": [99.99], "volume": [100]}
                                ]
                            }
                        }
                    ]
                }
            }

        def fake_fetch_chain(url):
            if "euronext" in url:
                return fake_euronext_for_chain(url)
            return fake_yahoo_for_chain(url)

        result = bars_for_isin("NO0000000000", _fetch=fake_fetch_chain)
        assert result is not None and len(result) == 1, f"chain failed: {result}"
        assert result[0]["close"] == 99.99, f"chained bars mismatch: {result[0]}"
        ok += 1

        print(f"oslo_prices selftest: {ok} assertion groups passed")
        return 0

    finally:
        # Restore real cache and clean up test directory.
        CACHE_DIR = real_cache
        try:
            import shutil
            shutil.rmtree(test_cache)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--isin", help="Oslo Børs ISIN to resolve and fetch bars for")
    ap.add_argument("--ticker", help="Oslo Børs ticker to fetch bars for")
    ap.add_argument("--range", default="1y", dest="range_",
                    help="Yahoo range parameter (default: 1y)")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="output as JSON")
    ap.add_argument("--selftest", action="store_true",
                    help="run offline self-test (no network, no cache pollution)")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(_selftest())

    if not args.isin and not args.ticker:
        ap.error("provide --isin or --ticker, or --selftest")

    payload = {}

    if args.isin:
        isin = args.isin.strip().upper()
        ticker = resolve_oslo_ticker(isin)
        payload["isin"] = isin
        payload["ticker"] = ticker
        payload["ticker_resolved"] = ticker is not None

        if ticker:
            bars_list = bars(ticker, range_=args.range_)
            payload["bars"] = bars_list
            payload["bars_count"] = len(bars_list) if bars_list else 0

    elif args.ticker:
        ticker = args.ticker.strip().upper()
        bars_list = bars(ticker, range_=args.range_)
        payload["ticker"] = ticker
        payload["bars"] = bars_list
        payload["bars_count"] = len(bars_list) if bars_list else 0

    if args.as_json:
        print(json.dumps(payload, indent=2))
    else:
        for key, value in payload.items():
            if key == "bars":
                print(f"  {key}:")
                print(report_bars(value))
            else:
                print(f"  {key}: {value}")

    # Exit nonzero if a key piece failed (ISIN resolved but no bars, etc.)
    success = bool(payload.get("ticker") if args.isin else payload.get("bars"))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
