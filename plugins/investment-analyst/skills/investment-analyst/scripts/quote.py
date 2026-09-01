#!/usr/bin/env python3
"""Current quote with an explicit as-of timestamp and staleness warning.

The analyst rule is "never present a stale price as current", so this always
prints when the print was struck and how old it is, and cross-checks two
independent sources for US tickers.

Usage:
    python quote.py NVDA
    python quote.py VOLV-B.ST EVO.ST INVE-B.ST
    python quote.py NVDA --json

Swedish tickers use the Nasdaq Stockholm suffix .ST (B-shares use a hyphen:
VOLV-B.ST, INVE-B.ST, ATCO-A.ST). No API key required for either source.
"""
import argparse
import datetime
import json
import sys
import urllib.error
import urllib.request

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1mo&interval=1d"
NASDAQ = "https://api.nasdaq.com/api/quote/{sym}/info?assetclass=stocks"
UA = "Mozilla/5.0 (compatible; investment-analyst-skill/1.0)"


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def from_yahoo(symbol):
    # Must not raise: Yahoo 404s on a bad ticker and 429s under rate limiting,
    # and those are exactly the moments the Nasdaq fallback has to take over.
    try:
        data = fetch(YAHOO.format(sym=urllib.request.quote(symbol)))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            json.JSONDecodeError):
        return None
    result = (data.get("chart") or {}).get("result")
    if not result:
        return None
    meta = result[0]["meta"]
    ts = meta.get("regularMarketTime")

    # meta.chartPreviousClose is the close *before the requested range*, i.e. a
    # month ago here - not yesterday. Take the prior session off the series.
    closes = [c for c in (((result[0].get("indicators") or {}).get("quote") or [{}])[0]
                          .get("close") or []) if c is not None]
    prev_close = closes[-2] if len(closes) >= 2 else meta.get("previousClose")

    return {
        "source": "Yahoo Finance (unofficial endpoint)",
        "symbol": meta.get("symbol"),
        "exchange": meta.get("fullExchangeName"),
        "currency": meta.get("currency"),
        "price": meta.get("regularMarketPrice"),
        "previous_close": prev_close,
        "fifty_two_week_high": meta.get("fiftyTwoWeekHigh"),
        "fifty_two_week_low": meta.get("fiftyTwoWeekLow"),
        "as_of_utc": datetime.datetime.fromtimestamp(
            ts, datetime.timezone.utc).isoformat() if ts else None,
        "timezone": meta.get("exchangeTimezoneName"),
    }


def from_nasdaq(symbol):
    """US listings only. Returns None for anything Nasdaq does not serve."""
    try:
        data = fetch(NASDAQ.format(sym=urllib.request.quote(symbol)))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            OSError, json.JSONDecodeError):
        return None
    d = data.get("data")
    if not d or not d.get("primaryData"):
        return None
    p = d["primaryData"]
    raw = (p.get("lastSalePrice") or "").replace("$", "").replace(",", "")
    try:
        price = float(raw)
    except ValueError:
        return None
    return {
        "source": "Nasdaq (api.nasdaq.com)",
        "symbol": d.get("symbol"),
        "exchange": d.get("exchange"),
        "currency": "USD",
        "price": price,
        "net_change": p.get("netChange"),
        "percent_change": p.get("percentageChange"),
        "as_of_label": p.get("lastTradeTimestamp"),
        "delta": p.get("deltaIndicator"),
    }


def staleness(as_of_iso):
    if not as_of_iso:
        return None, "as-of timestamp unavailable"
    then = datetime.datetime.fromisoformat(as_of_iso)
    hours = (datetime.datetime.now(datetime.timezone.utc) - then).total_seconds() / 3600
    if hours < 1:
        note = "live / just closed"
    elif hours < 24:
        note = "%.0f h old - normal outside trading hours" % hours
    elif hours < 96:
        note = "%.0f h old - likely a weekend or holiday close" % hours
    else:
        note = "%.0f h old - STALE, verify before quoting" % hours
    return hours, note


def report(symbol, as_json=False):
    y = from_yahoo(symbol)
    # Nasdaq writes share classes with a dot (BRK.B); Yahoo uses a hyphen (BRK-B).
    # The "." test also keeps Nasdaq Stockholm tickers (VOLV-B.ST) out of it.
    n = from_nasdaq(symbol.replace("-", ".")) if "." not in symbol else None

    if not y and not n:
        print("%s: DATA NOT AVAILABLE - no source returned a quote." % symbol)
        return False

    primary = y or n
    hours, note = staleness(primary.get("as_of_utc")) if y else (None, primary.get("as_of_label"))

    if as_json:
        print(json.dumps({"query": symbol, "yahoo": y, "nasdaq": n,
                          "staleness_hours": hours, "staleness_note": note}, indent=2))
        return True

    print("%s  %s %s" % (primary.get("symbol", symbol),
                         primary.get("price"), primary.get("currency", "")))
    print("  exchange   : %s" % primary.get("exchange"))
    print("  as of      : %s  (%s)" % (primary.get("as_of_utc") or primary.get("as_of_label"), note))
    if y:
        pc = y.get("previous_close")
        if pc and primary.get("price"):
            chg = primary["price"] - pc
            print("  prev close : %.2f   (change %+.2f, %+.2f%%)" % (pc, chg, chg / pc * 100))
        lo, hi = y.get("fifty_two_week_low"), y.get("fifty_two_week_high")
        if lo and hi and primary.get("price") and hi > lo:
            pos = (primary["price"] - lo) / (hi - lo) * 100
            print("  52w range  : %.2f - %.2f   (at %.0f%% of range)" % (lo, hi, pos))
        elif lo and hi:
            print("  52w range  : %.2f - %.2f" % (lo, hi))
    print("  source     : %s" % primary["source"])

    if y and n and y.get("price") and n.get("price"):
        diff = abs(y["price"] - n["price"]) / n["price"] * 100
        flag = "OK" if diff < 0.5 else "MISMATCH - investigate"
        print("  cross-check: Nasdaq %s vs Yahoo %s -> %.2f%% [%s]"
              % (n["price"], y["price"], diff, flag))
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    ok = True
    for i, s in enumerate(args.symbols):
        if i and not args.as_json:
            print()
        ok = report(s, args.as_json) and ok
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
