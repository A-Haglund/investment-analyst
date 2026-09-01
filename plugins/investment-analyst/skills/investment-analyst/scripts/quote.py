#!/usr/bin/env python3
"""Current quote with an explicit as-of timestamp and staleness warning.

The analyst rule is "never present a stale price as current", so this always
prints when the print was struck and how old it is.

Usage:
    python quote.py VOLV-B.ST EVO.ST INVE-B.ST
    python quote.py VOLV-B.ST --json

Swedish tickers use the Nasdaq Stockholm suffix .ST (B-shares use a hyphen:
VOLV-B.ST, INVE-B.ST, ATCO-A.ST). No API key required.

Coverage: any ticker Yahoo Finance's chart endpoint carries a quote for -
in practice European (Nordic/French) venues. US issuers are out of scope for
this toolkit; the Nasdaq (api.nasdaq.com) US-listings cross-check that used
to run alongside Yahoo for bare US tickers has been removed rather than left
as dead code that nothing calls.
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

    if not y:
        print("%s: DATA NOT AVAILABLE - no source returned a quote." % symbol)
        return False

    hours, note = staleness(y.get("as_of_utc"))

    if as_json:
        print(json.dumps({"query": symbol, "yahoo": y,
                          "staleness_hours": hours, "staleness_note": note}, indent=2))
        return True

    print("%s  %s %s" % (y.get("symbol", symbol), y.get("price"), y.get("currency", "")))
    print("  exchange   : %s" % y.get("exchange"))
    print("  as of      : %s  (%s)" % (y.get("as_of_utc"), note))
    pc = y.get("previous_close")
    if pc and y.get("price"):
        chg = y["price"] - pc
        print("  prev close : %.2f   (change %+.2f, %+.2f%%)" % (pc, chg, chg / pc * 100))
    lo, hi = y.get("fifty_two_week_low"), y.get("fifty_two_week_high")
    if lo and hi and y.get("price") and hi > lo:
        pos = (y["price"] - lo) / (hi - lo) * 100
        print("  52w range  : %.2f - %.2f   (at %.0f%% of range)" % (lo, hi, pos))
    elif lo and hi:
        print("  52w range  : %.2f - %.2f" % (lo, hi))
    print("  source     : %s" % y["source"])
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
