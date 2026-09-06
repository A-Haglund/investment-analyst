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
as dead code that nothing calls (see fetch_price()'s history in
portfolio_metrics.py for the concrete defect that left behind).

TWO-SOURCE CROSS-CHECK. Price is now single-source (Yahoo, an unofficial
endpoint) unless a second, genuinely independent source can be reached, and
every multiple this toolkit computes divides by this figure - a bad price
here is not a cosmetic error, it is a bad valuation everywhere downstream.
For a Nasdaq Nordic ticker (.ST/.HE/.CO/.IC), cross_check() corroborates
Yahoo's price against nordic_shares.py's own Nasdaq Nordic reference data -
a real second source (Nasdaq's own venue feed), not a Yahoo mirror. Three
outcomes, never collapsed into each other:

    CROSS-CHECKED   both sources agree within CROSS_CHECK_TOLERANCE.
    CONFLICT        the sources disagree beyond tolerance, OR report
                    different currencies for the same line (a currency
                    mismatch means the two sources are not even pricing the
                    same instrument, which is worse than a price gap and is
                    never averaged away). Reported, never silently resolved
                    - this module does not guess which source is right.
    not checked     the ticker's suffix is outside Nasdaq Nordic's markets,
                    no matching listing was found, or Nasdaq Nordic could
                    not be reached. This is NOT folded into a clean
                    CROSS-CHECKED result (that would overstate confidence)
                    and NOT reported as a CONFLICT either (nothing was
                    actually compared) - it is its own, honestly-labelled
                    outcome, and every caller can see it happened.
"""
import argparse
import datetime
import importlib.util
import json
import os
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


# Yahoo suffixes by ISIN country prefix. Yahoo addresses a listing, not an
# issuer, so the venue has to be in the symbol: "AXFO" is not a Yahoo ticker
# and returns nothing, while "AXFO.ST" returns the Stockholm listing.
_YAHOO_SUFFIX = {
    "SE": ".ST",   # Nasdaq Stockholm / First North Stockholm
    "NO": ".OL",   # Oslo Børs / Euronext Growth Oslo
    "DK": ".CO",   # Nasdaq Copenhagen
    "FI": ".HE",   # Nasdaq Helsinki
    "IS": ".IC",   # Nasdaq Iceland
    "FR": ".PA",   # Euronext Paris
    "DE": ".DE",   # XETRA
    "NL": ".AS", "BE": ".BR", "PT": ".LS",
}


# The listing currency, where it names exactly one venue. This outranks the
# ISIN because it describes where the share TRADES; an ISIN describes where the
# issuer is REGISTERED, and those differ often enough to matter - Kambi Group
# plc carries a Maltese ISIN (MT0000780107) and trades on Nasdaq Stockholm as
# KAMBI.ST. EUR is deliberately absent: Helsinki, Paris, Amsterdam, Brussels
# and XETRA all quote in it, so it names no venue and must fall through to the
# ISIN.
_YAHOO_SUFFIX_BY_CURRENCY = {
    "SEK": ".ST", "NOK": ".OL", "DKK": ".CO", "ISK": ".IC",
}


def yahoo_symbol(ticker, isin=None, country=None, currency=None):
    """Nasdaq-style ticker -> Yahoo symbol, or None when it cannot be built.

    Two transformations, both load-bearing:

      "SHB A"  -> "SHB-A.ST"     space becomes a hyphen, venue suffix appended
      "AXFO"   -> "AXFO.ST"

    WHY THIS IS SHARED. The mapping existed in exactly one place
    (valuation_gate's Nordic path) and the two portfolio scripts did not know
    about it - they handed company_resolve's raw ticker straight to Yahoo,
    every lookup returned None, and every holding was dropped for having no
    price. A portfolio review then reported zero holdings and a total equal to
    the cash balance: not an error, an answer, and a wrong one.

    THE SUFFIX IS DERIVED, NOT ASSUMED. That single earlier copy appended
    ".ST" unconditionally, which is right for Stockholm and wrong for every
    other venue this toolkit covers - Oslo needs ".OL", Copenhagen ".CO",
    Helsinki ".HE", Paris ".PA".

    WHICH SIGNAL, AND WHY THE ORDER. What is needed is the VENUE, and neither
    input states it, so both are proxies and they disagree:

      `currency`  describes where the share trades. Preferred, but only when
                  it names one venue - EUR names five, so it is not in the map.
      `isin`      describes where the ISSUER IS REGISTERED. A weaker proxy,
                  used only when currency cannot answer. Kambi Group plc is
                  the standing counter-example: ISIN MT0000780107 (Malta),
                  traded as KAMBI.ST in Stockholm. Reading the ISIN as the
                  venue dropped it from a portfolio silently, because a
                  holding with no price is a holding with no weight.
      `country`   an explicit override for a caller that actually knows.

    With none of them usable this REFUSES rather than guessing ".ST". A wrong
    suffix does not raise - it prices a different listing, or nothing at all,
    and both read as an answer.

    The authoritative fix is to record the venue on the holding when it is
    resolved. Until the store carries that, this is a documented proxy chain,
    not a lookup.
    """
    if not ticker:
        return None

    # Already qualified: return it untouched. A caller may hold a Yahoo symbol
    # rather than a Nasdaq ticker ("AXFO.ST", "XYZ.OL"), and appending a second
    # suffix produces "AXFO.ST.ST", which resolves to nothing. Any dot means
    # the venue is already stated - Nordic tickers carry spaces and hyphens,
    # never dots.
    cleaned = ticker.strip()
    if "." in cleaned:
        return cleaned.replace(" ", "-")

    suffix = None
    if country:
        suffix = _YAHOO_SUFFIX.get(country.strip().upper())
    if not suffix and currency:
        suffix = _YAHOO_SUFFIX_BY_CURRENCY.get(currency.strip().upper())
    if not suffix and isin:
        suffix = _YAHOO_SUFFIX.get(isin[:2].upper())
    if not suffix:
        return None
    return ticker.strip().replace(" ", "-") + suffix


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


# --------------------------------------------------------------------------
# Second source: Nasdaq Nordic, via nordic_shares.py (owned by a different
# agent - read, never edited, from here). Lazily loaded and swappable, the
# same idiom portfolio_store.py uses for company_resolve.py: a Nordic-only
# ticker cross-check has no business paying nordic_shares.py's import cost
# (which pulls in urllib work of its own) for a caller pricing a non-Nordic
# name, and tests need to swap in a fake without touching the real module.
# --------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
_NORDIC_MODULE = None


def _nordic_shares():
    global _NORDIC_MODULE
    if _NORDIC_MODULE is None:
        spec = importlib.util.spec_from_file_location(
            "nordic_shares", os.path.join(HERE, "nordic_shares.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _NORDIC_MODULE = mod
    return _NORDIC_MODULE


# Nasdaq Nordic's markets (Stockholm, Helsinki, Copenhagen, Iceland) are what
# nordic_shares.py's /search endpoint actually covers - NOT Oslo (.OL), which
# is Euronext, not Nasdaq, and NOT Paris (.PA) or any other venue Yahoo also
# happens to serve. A suffix outside this set must degrade to "not checked",
# never silently skip the check while implying one ran.
NORDIC_YAHOO_SUFFIXES = (".ST", ".HE", ".CO", ".IC")


def _nordic_symbol_from_yahoo(symbol):
    """"VOLV-B.ST" -> "VOLV B", "EVO.ST" -> "EVO", "NOVO-B.CO" -> "NOVO B".

    Yahoo spells a share class with a hyphen where Nasdaq Nordic's own
    listings (nordic_shares.py's `symbol` field) use a plain space; both put
    the venue in a suffix Nasdaq Nordic's search does not expect at all.
    Returns None for any ticker whose suffix is not one of
    NORDIC_YAHOO_SUFFIXES - the caller must treat that as "cannot check",
    never guess at a mapping outside these markets."""
    upper = (symbol or "").upper()
    for suf in NORDIC_YAHOO_SUFFIXES:
        if upper.endswith(suf):
            base = symbol[: -len(suf)]
            return base.replace("-", " ").strip().upper()
    return None


def from_nordic(symbol):
    """Independent second source for a Nordic ticker, or None.

    None means "this did not run", for any of several reasons that all get
    the same treatment from cross_check() (a "not checked" status, never a
    guess): `symbol`'s suffix is outside Nasdaq Nordic's markets, nothing in
    a search for its root ticker matched the exact class, or the request to
    Nasdaq Nordic itself failed. nordic_shares.py's own api() raises
    SystemExit on an unreachable endpoint - caught here rather than left to
    kill the whole quote lookup, exactly like portfolio_metrics.fetch_sector
    already treats the same module's failures."""
    nordic_symbol = _nordic_symbol_from_yahoo(symbol)
    if nordic_symbol is None:
        return None
    try:
        ns = _nordic_shares()
    except (Exception, SystemExit):
        return None

    query = nordic_symbol.split(" ")[0]
    try:
        rows = ns.search(query) or []
    except (Exception, SystemExit):
        return None

    match = next((r for r in rows
                 if (r.get("symbol") or "").upper() == nordic_symbol), None)
    if match is None:
        # A class-less listing (e.g. "NOKIA", no trailing letter) never
        # equals nordic_symbol outright when nordic_symbol itself carries no
        # class either - root_symbol() is a no-op for those, so this still
        # only matches a genuine same-root line, never a different class.
        match = next((r for r in rows if ns.root_symbol(
            (r.get("symbol") or "").upper()) == nordic_symbol), None)
    if match is None or not match.get("orderbookId"):
        return None

    try:
        q = ns.quote(match["orderbookId"])
    except (Exception, SystemExit):
        return None
    if q.get("last") is None:
        return None

    return {"source": "Nasdaq Nordic (nordic_shares.py)",
            "symbol": match.get("symbol"), "isin": match.get("isin"),
            "price": q.get("last"), "currency": q.get("currency"),
            "as_of": q.get("as_of")}


# A live tick vs. a prior close struck at a different moment can legitimately
# be a percent or so apart with nothing wrong; finfact.py's corroborate()
# uses a 1% tolerance, but that is for two sources reading the SAME filed
# figure (an exact number that cannot legitimately differ at all). A traded
# price is a moving target between two independent feeds' timestamps, so a
# tighter band would manufacture false CONFLICTs on ordinary intraday drift.
# 2% is loose enough to absorb that timing noise and still catch what this
# check exists to catch: a wrong ticker, wrong share class, or wrong currency
# priced as if it were the requested line.
CROSS_CHECK_TOLERANCE = 0.02


def cross_check(yahoo_quote, symbol, tolerance=CROSS_CHECK_TOLERANCE):
    """Corroborate `yahoo_quote` (from_yahoo()'s return) against Nasdaq
    Nordic for `symbol`. Returns {"status": ..., "reason": ..., "nordic":
    ... or absent}. status is one of "CROSS-CHECKED", "CONFLICT" or
    "not checked" - see this module's docstring for what each one means and
    why they are never folded into each other."""
    if not yahoo_quote or yahoo_quote.get("price") is None:
        return {"status": "not checked", "reason": "no Yahoo price to check against"}

    nordic = from_nordic(symbol)
    if nordic is None or nordic.get("price") is None:
        return {"status": "not checked",
                "reason": ("no independent Nasdaq Nordic listing found or "
                          "reachable for %r (non-Nordic ticker, no matching "
                          "class, or Nasdaq Nordic unreachable)" % symbol)}

    y_ccy = (yahoo_quote.get("currency") or "").upper()
    n_ccy = (nordic.get("currency") or "").upper()
    if y_ccy and n_ccy and y_ccy != n_ccy:
        return {"status": "CONFLICT",
                "reason": ("currency mismatch: Yahoo reports %s, Nasdaq "
                          "Nordic reports %s for %s - the two sources are "
                          "not pricing the same instrument, not merely "
                          "disagreeing on its price"
                          % (yahoo_quote.get("currency"), nordic.get("currency"),
                             nordic.get("symbol", symbol))),
                "nordic": nordic}

    y_price, n_price = yahoo_quote["price"], nordic["price"]
    hi, lo = max(y_price, n_price), min(y_price, n_price)
    spread = (hi - lo) / hi if hi else 0.0
    reason = ("Yahoo %.2f vs Nasdaq Nordic %.2f (%s) - %.2f%% apart, "
             "tolerance %.0f%%"
             % (y_price, n_price, nordic.get("symbol", symbol),
                spread * 100, tolerance * 100))
    if spread > tolerance:
        return {"status": "CONFLICT", "reason": reason, "nordic": nordic}
    return {"status": "CROSS-CHECKED", "reason": reason, "nordic": nordic}


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
    check = cross_check(y, symbol)

    if as_json:
        print(json.dumps({"query": symbol, "yahoo": y,
                          "staleness_hours": hours, "staleness_note": note,
                          "cross_check": check}, indent=2))
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
    print("  cross-check: %s - %s" % (check["status"], check["reason"]))
    return True


# --------------------------------------------------------------------------
# Self-test - offline, no network. nordic_shares.py is swapped for a fake
# duck-typing exactly what from_nordic() calls on it (.search, .quote,
# .root_symbol), the same swap-the-whole-module pattern portfolio_store.py
# uses for company_resolve.py.
# --------------------------------------------------------------------------

class _FakeNordic(object):
    def __init__(self, rows, quotes):
        self._rows = rows
        self._quotes = quotes

    def search(self, text):
        return self._rows

    def quote(self, orderbook_id):
        return self._quotes[orderbook_id]

    def root_symbol(self, symbol):
        parts = (symbol or "").rsplit(" ", 1)
        return parts[0] if len(parts) == 2 and len(parts[1]) <= 2 else symbol


class _BrokenNordic(object):
    """Every call raises SystemExit, exactly how nordic_shares.py's own
    api() fails on an unreachable endpoint."""
    def search(self, text):
        raise SystemExit("DATA NOT AVAILABLE: Nasdaq Nordic unreachable (simulated)")


def _selftest():
    global _NORDIC_MODULE
    ok = 0

    # --- Yahoo ticker -> Nasdaq Nordic symbol mapping ----------------------
    assert _nordic_symbol_from_yahoo("VOLV-B.ST") == "VOLV B"
    assert _nordic_symbol_from_yahoo("EVO.ST") == "EVO"
    assert _nordic_symbol_from_yahoo("NOVO-B.CO") == "NOVO B"
    assert _nordic_symbol_from_yahoo("AAPL") is None            # no suffix at all
    assert _nordic_symbol_from_yahoo("EQNR.OL") is None         # Oslo is Euronext, not Nasdaq
    ok += 5

    y = {"price": 275.50, "currency": "SEK",
        "source": "Yahoo Finance (unofficial endpoint)"}
    real_nordic, _NORDIC_MODULE = _NORDIC_MODULE, None
    try:
        # --- agreement within tolerance -> CROSS-CHECKED --------------------
        _NORDIC_MODULE = _FakeNordic(
            rows=[{"orderbookId": 1, "symbol": "VOLV B",
                  "isin": "SE0000115446", "currency": "SEK"}],
            quotes={1: {"last": 275.10, "currency": "SEK", "as_of": "2026-09-02"}})
        check = cross_check(y, "VOLV-B.ST")
        assert check["status"] == "CROSS-CHECKED", check
        ok += 1

        # --- a material disagreement is a CONFLICT, never averaged away -----
        y_bad = dict(y, price=400.0)
        check2 = cross_check(y_bad, "VOLV-B.ST")
        assert check2["status"] == "CONFLICT", check2
        ok += 1

        # --- currency mismatch is a CONFLICT even if the numbers are close --
        _NORDIC_MODULE = _FakeNordic(
            rows=[{"orderbookId": 1, "symbol": "VOLV B",
                  "isin": "SE0000115446", "currency": "EUR"}],
            quotes={1: {"last": 275.10, "currency": "EUR", "as_of": "2026-09-02"}})
        check3 = cross_check(y, "VOLV-B.ST")
        assert check3["status"] == "CONFLICT" and "currency" in check3["reason"], check3
        ok += 1

        # --- Nasdaq Nordic unreachable degrades to "not checked", never
        # folded into a clean result and never reported as a conflict -------
        _NORDIC_MODULE = _BrokenNordic()
        check4 = cross_check(y, "VOLV-B.ST")
        assert check4["status"] == "not checked", check4
        ok += 1
    finally:
        _NORDIC_MODULE = real_nordic

    # --- a non-Nordic ticker is "not checked", not silently skipped --------
    y_us = {"price": 190.0, "currency": "USD",
           "source": "Yahoo Finance (unofficial endpoint)"}
    check5 = cross_check(y_us, "AAPL")
    assert check5["status"] == "not checked", check5
    ok += 1

    print("quote selftest: %d assertions passed" % ok)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbols", nargs="*")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(_selftest())
    if not args.symbols:
        ap.error("give one or more ticker symbols, or --selftest")

    ok = True
    for i, s in enumerate(args.symbols):
        if i and not args.as_json:
            print()
        ok = report(s, args.as_json) and ok
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
