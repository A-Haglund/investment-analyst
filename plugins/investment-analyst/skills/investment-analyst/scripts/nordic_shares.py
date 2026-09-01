#!/usr/bin/env python3
"""Shares outstanding and market cap from Nasdaq Nordic's own reference data.

Getting the share count wrong corrupts market cap and every multiple derived
from it, and it is the most common silent error in Swedish analysis because
most large caps carry two listed classes. Taking the count from a quote site,
or from one class only, understates market cap badly.

This reads the exchange's own reference data - the same figure Nasdaq uses to
publish market cap - for every listed class, and sums them.

Verified 2026-08-31 against issuers' statutory disclosures: Evolution
199,226,613 exact; Atlas Copco, Investor and Volvo class sums exact to within
151 shares on 2.03bn.

De-duplication: ten ISINs on the Nordic venues are cross-listed (e.g. Nordea
on Stockholm, Copenhagen and Helsinki -- one class, three order books, same
ISIN). The symbol root alone cannot tell a cross-listing from a genuine
second share class, so every class list is de-duplicated by ISIN before
anything is summed: same ISIN on several venues counts once; different ISINs
under the same symbol root (VOLV A vs VOLV B) still sum as before. Market
caps are never added across currencies -- a mixed-currency result is reported
per currency, with an optional Riksbank-FX-converted total shown only when
every rate used is dated and disclosed.

TWO LIMITS YOU MUST RESPECT:

  * UNLISTED CLASSES ARE INVISIBLE. NIBE and Fenix Outdoor have unlisted A
    shares that never appear here - NIBE's true count is roughly 12% above what
    this returns. The script flags any issuer where a single listed class looks
    like it may have an unlisted sibling, but the flag cannot be exhaustive.
    Confirm against the issuer's latest "Total number of voting rights and
    capital" disclosure before relying on the figure.
  * These are REGISTERED shares including treasury. Subtract treasury before
    computing market cap or EV (see references/valuation.md), and never use
    this count for per-share earnings.

Usage:
    python nordic_shares.py "Evolution"
    python nordic_shares.py "Volvo"            # sums VOLV A + VOLV B
    python nordic_shares.py "KebNi"            # First North works too
    python nordic_shares.py "Atlas Copco" --json
    python nordic_shares.py --universe STO     # the whole Stockholm list

Free, no API key. Source: https://www.nasdaq.com/european-market-activity
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.nasdaq.com/api/nordic"

# The default Python-urllib User-Agent is blocklisted and the request hangs
# until timeout rather than erroring. Any browser-shaped UA works.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

SEGMENTS = [("MAIN_MARKET", "LARGE_CAP"), ("MAIN_MARKET", "MID_CAP"),
            ("MAIN_MARKET", "SMALL_CAP"), ("FIRST_NORTH", None)]

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def api(path, **params):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        raise SystemExit("DATA NOT AVAILABLE: Nasdaq Nordic unreachable (%s)" % e)
    status = payload.get("status") or {}
    if status.get("rCode") != 200:
        raise SystemExit("DATA NOT AVAILABLE: %s"
                         % (status.get("bCodeMessage") or "Nasdaq returned an error"))
    return payload["data"]


def num(raw):
    if raw is None:
        return None
    cleaned = str(raw).replace(",", "").replace("SEK", "").replace("%", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def root_symbol(symbol):
    """VOLV A and VOLV B belong to the same issuer. No issuer id is exposed, so
    the symbol root is the only available grouping key.

    WARNING: this also groups the SAME class listed on several venues --
    "NDA DK" / "NDA FI" / "NDA SE" all root to "NDA", but that is one Nordea
    share cross-listed three times, not three classes. root_symbol() cannot
    tell the two cases apart from the symbol text alone; callers that sum
    over a root_symbol group MUST de-duplicate by ISIN first (see
    dedupe_by_isin below) or they will triple-count cross-listed issuers."""
    parts = (symbol or "").rsplit(" ", 1)
    if len(parts) == 2 and len(parts[1]) <= 2:
        return parts[0]
    return symbol


# ISIN country-code prefix -> the currency that venue's home listing normally
# trades in. Used only as a tie-breaker for which cross-listed order book to
# treat as the reporting line; every other venue is kept and disclosed, never
# silently dropped.
_HOME_CCY = {"SE": "SEK", "FI": "EUR", "DK": "DKK", "NO": "NOK", "IS": "ISK"}


def dedupe_by_isin(details):
    """Collapse cross-listed venues of the SAME ISIN to one counted line.

    Nasdaq's own /search endpoint returns every venue a share class trades on
    as a separate row with its own orderbookId, but the same shares
    outstanding and (converted through whatever FX applied that day) roughly
    the same market cap, just quoted in that venue's currency. Summing those
    rows -- which is what grouping by root_symbol() alone does -- triples a
    three-venue cross-listing like Nordea's.

    The ISIN is the reliable signal: two GENUINE share classes have two
    different ISINs (VOLV A vs VOLV B) and are kept as separate lines to be
    summed; the SAME ISIN appearing more than once is one instrument quoted
    several places, and only one of those lines is kept for counting.

    Returns (deduped, cross_listed):
      deduped      -- one dict per unique ISIN (or per orderbookId if an ISIN
                       is missing), safe to sum shares/market cap over.
      cross_listed -- [{"isin", "chosen", "venues": [symbol, ...]}] for every
                       ISIN that had more than one venue, so callers can
                       disclose the collapse rather than hide it.
    """
    groups, order = {}, []
    for d in details:
        key = d.get("isin") or ("orderbook:" + str(d.get("orderbookId")))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(d)

    deduped, cross_listed = [], []
    for key in order:
        group = groups[key]
        if len(group) == 1:
            deduped.append(group[0])
            continue
        home_ccy = _HOME_CCY.get((group[0].get("isin") or "")[:2])
        primary = next((g for g in group if g.get("currency") == home_ccy), None)
        if primary is None:
            primary = group[0]
        others = [g for g in group if g is not primary]
        rep = dict(primary)
        rep["cross_listed_venues"] = [
            {"symbol": o.get("symbol"), "currency": o.get("currency"),
             "market_cap": o.get("market_cap"), "orderbookId": o.get("orderbookId")}
            for o in others]
        deduped.append(rep)
        cross_listed.append({"isin": group[0].get("isin"),
                             "chosen": primary.get("symbol"),
                             "venues": [g.get("symbol") for g in group]})
    return deduped, cross_listed


def search(text):
    """Listed equity lines only.

    The response is a list of {group, instruments}. A name search also returns
    warrants and leverage certificates - "Evolution" pulls in BEAR EVOLUTION X10
    - so filter on assetClass rather than trusting the group label.
    """
    rows = []
    for group in api("/search", searchText=text) or []:
        label = group.get("group") or ""
        for r in group.get("instruments") or []:
            if (r.get("assetClass") or "").upper() != "SHARES":
                continue
            rows.append({"orderbookId": r.get("orderbookId"), "symbol": r.get("symbol"),
                         "name": r.get("fullName"), "isin": r.get("isin"),
                         "currency": r.get("currency"), "group": label})
    return rows


def summary(orderbook_id):
    s = api("/instruments/%s/summary" % orderbook_id, assetClass="SHARES")["summaryData"]
    get = lambda k: (s.get(k) or {}).get("value")
    return {"orderbookId": orderbook_id, "isin": get("isin"),
            "shares": num(get("shares")), "market_cap": num(get("marketCap")),
            "segment": get("insSegment"), "icb": get("icbCode"),
            "note": (get("note") or "").strip()}


def quote(orderbook_id):
    """Last traded price with the exchange's own timestamp.

    This read `qdHeader["lastPrice"]` and `["timeAsOf"]`, neither of which the
    endpoint publishes, so it silently returned `last=None` and the literal
    string "Trading" as the as-of - a missing price and a meaningless
    timestamp, with no error to reveal either. The figures live one level down
    in `primaryData`.
    """
    h = api("/instruments/%s/info" % orderbook_id, assetClass="SHARES")["qdHeader"]
    p = h.get("primaryData") or {}
    raw = (p.get("lastSalePrice") or "").replace("$", "").replace(",", "").strip()
    return {"last": num(raw) if raw else num(h.get("lastPrice")),
            "currency": h.get("currency"),
            "as_of": p.get("lastTradeTimestamp") or h.get("marketStatus"),
            "net_change": p.get("netChange"),
            "percent_change": p.get("percentageChange"),
            "market_status": h.get("marketStatus")}


def price_history(orderbook_id, from_date, to_date):
    """Daily OHLCV from the venue of record.

    Without both dates the endpoint returns intraday minute bars instead;
    `timeframe` and `period` are ignored. Prices are UNADJUSTED for splits and
    dividends - fine for reconstructing a multiple range when paired with the
    share count of the time, wrong for total return.
    """
    data = api("/instruments/%s/chart" % orderbook_id, assetClass="SHARES",
               lang="en", fromDate=from_date, toDate=to_date)
    bars = []
    for b in data.get("CP") or []:
        z = b.get("z") or {}
        bars.append({"date": z.get("dateTime"), "open": num(z.get("open")),
                     "high": num(z.get("high")), "low": num(z.get("low")),
                     "close": num(z.get("close")), "volume": num(z.get("volume"))})
    return [b for b in bars if b["close"] is not None]


def universe(market="STO"):
    """Every listed share line on a market.

    Keeps the screener's own `currency` field. It used to be dropped, which
    forced callers to infer currency from the venue -- wrong for a foreign
    issuer trading in its own currency on a Nordic exchange (Verisure trades
    in EUR on Stockholm, not SEK; that guess was an 11x market-cap error).
    """
    seen = {}
    for category, segment in SEGMENTS:
        params = {"category": category, "market": market, "tableonly": "false"}
        if segment:
            params["segment"] = segment
        rows = api("/screener/shares", **params)["instrumentListing"]["rows"]
        for r in rows:
            seen[r["orderbookId"]] = {
                "orderbookId": r["orderbookId"], "symbol": r.get("symbol"),
                "name": r.get("fullName"), "isin": r.get("isin"),
                "currency": r.get("currency"),
                "segment": segment or "FIRST_NORTH", "sector": r.get("sector"),
                "last": num(r.get("lastSalePrice"))}
    return list(seen.values())


def _fx_convert_to_sek(cap_by_ccy):
    """Best-effort SEK conversion of a per-currency market-cap breakdown,
    using macro_se.py's dated Riksbank/ECB rates -- never a guessed rate.

    Opt-in and defensive: this is a convenience on top of the mandatory
    per-currency report, not a replacement for it. If macro_se.py cannot be
    imported, or a rate for one of the currencies present cannot be fetched,
    this returns None and the caller falls back to the per-currency figures
    with no combined total at all -- silently substituting a stale or
    fabricated rate would recreate exactly the defect being fixed here.
    """
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import macro_se
    except ImportError:
        return None
    try:
        fx = macro_se.sek_fx(extra_fx=True)
    except Exception:
        return None

    lines, total = [], 0.0
    for ccy, amount in sorted(cap_by_ccy.items()):
        if ccy == "SEK":
            lines.append({"currency": ccy, "amount": amount, "rate": 1.0,
                          "obs_date": "-", "source": "SEK is already SEK",
                          "converted_sek": amount})
            total += amount
            continue
        info = fx.get(ccy)
        if not info or info.get("sek_per_unit") is None:
            return None
        converted = amount * info["sek_per_unit"]
        lines.append({"currency": ccy, "amount": amount,
                      "rate": info["sek_per_unit"], "obs_date": info["obs_date"],
                      "source": info["source"], "converted_sek": converted})
        total += converted
    return {"lines": lines, "total_sek": total}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("company", nargs="?", help="company or ticker, e.g. Evolution")
    ap.add_argument("--universe", metavar="MARKET",
                    help="list every listed share line on a market, e.g. STO")
    ap.add_argument("--history", type=int, metavar="YEARS",
                    help="daily closes over N years, for historical multiple ranges")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    if args.universe:
        rows = universe(args.universe.upper())
        if args.as_json:
            print(json.dumps({"market": args.universe.upper(), "count": len(rows),
                              "rows": rows}, indent=2, ensure_ascii=False))
            return
        by_segment = {}
        for r in rows:
            by_segment.setdefault(r["segment"], []).append(r)
        print("Nasdaq %s — %d listed share lines" % (args.universe.upper(), len(rows)))
        for seg in ("LARGE_CAP", "MID_CAP", "SMALL_CAP", "FIRST_NORTH"):
            if seg in by_segment:
                print("  %-14s %4d" % (seg, len(by_segment[seg])))
        return

    if not args.company:
        ap.error("give a company name, or --universe STO")

    hits = search(args.company)
    if not hits:
        print("DATA NOT AVAILABLE: Nasdaq Nordic has no listed share matching %r."
              % args.company)
        print("The company may be listed elsewhere, unlisted, or spelled differently.")
        return

    # Keep every listed class of the best-matching issuer.
    needle = args.company.lower()
    exact = [h for h in hits if needle in (h["name"] or "").lower()
             or needle in (h["symbol"] or "").lower()]
    chosen = exact or hits
    root = root_symbol(chosen[0]["symbol"])
    classes = [h for h in chosen if root_symbol(h["symbol"]) == root]

    details = []
    for c in classes:
        d = summary(c["orderbookId"])
        d.update({"symbol": c["symbol"], "name": c["name"], "group": c["group"],
                 "currency": c.get("currency")})
        try:
            d.update(quote(c["orderbookId"]))
        except SystemExit:
            d.setdefault("last", None)
            d.setdefault("currency", c.get("currency"))
        details.append(d)

    if args.history:
        import datetime
        to_date = datetime.date.today()
        from_date = to_date - datetime.timedelta(days=365 * args.history + 5)
        primary = max(classes, key=lambda c: len(c["symbol"] or ""))
        # The most liquid class carries the meaningful price series; for a
        # dual-class issuer that is normally the B share.
        b_class = [c for c in classes if (c["symbol"] or "").endswith(" B")]
        primary = b_class[0] if b_class else classes[0]
        bars = price_history(primary["orderbookId"],
                             from_date.isoformat(), to_date.isoformat())
        if not bars:
            print("DATA NOT AVAILABLE: no price history for %s." % primary["symbol"])
            return
        closes = [b["close"] for b in bars]
        if args.as_json:
            print(json.dumps({"symbol": primary["symbol"], "bars": len(bars),
                              "from": bars[0]["date"], "to": bars[-1]["date"],
                              "history": bars}, indent=2))
            return
        lo, hi, last = min(closes), max(closes), closes[-1]
        srt = sorted(closes)
        pctile = 100.0 * sum(1 for c in closes if c <= last) / len(closes)
        print("%s — %d daily bars, %s to %s"
              % (primary["symbol"], len(bars), bars[0]["date"], bars[-1]["date"]))
        print()
        print("  last close      %10.2f" % last)
        print("  range           %10.2f  to %10.2f" % (lo, hi))
        print("  median          %10.2f" % srt[len(srt) // 2])
        print("  current sits at %9.0f%% of the %d-year distribution"
              % (pctile, args.history))
        print()
        print("  Unadjusted for splits and dividends. Pair each close with the")
        print("  share count of the time before turning this into a multiple range.")
        return

    details.sort(key=lambda d: d["symbol"] or "")

    # De-duplicate cross-listed venues of the SAME ISIN before summing anything.
    # Genuinely different ISINs under one symbol root (VOLV A / VOLV B) still
    # sum as before; the same ISIN quoted on several venues (NDA DK/FI/SE) now
    # counts once instead of tripling the share count and blending currencies.
    counted, cross_listed = dedupe_by_isin(details)
    counted.sort(key=lambda d: d["symbol"] or "")

    total_shares = sum(d["shares"] or 0 for d in counted)
    currencies = sorted({d.get("currency") for d in counted if d.get("currency")})
    mixed_currency = len(currencies) > 1
    cap_by_ccy = {}
    for d in counted:
        ccy = d.get("currency") or "UNKNOWN"
        cap_by_ccy[ccy] = cap_by_ccy.get(ccy, 0) + (d.get("market_cap") or 0)
    total_cap = None if mixed_currency else sum(cap_by_ccy.values())

    fx_conversion = _fx_convert_to_sek(cap_by_ccy) if mixed_currency else None

    if args.as_json:
        print(json.dumps({"query": args.company, "symbol_root": root,
                          "source": "Nasdaq Nordic reference data",
                          "total_shares": total_shares,
                          "total_market_cap": total_cap,
                          "currencies_mixed": mixed_currency,
                          "market_cap_by_currency": cap_by_ccy,
                          "fx_conversion_to_sek": fx_conversion,
                          "cross_listed": cross_listed,
                          "classes": details,
                          "counted_classes": counted}, indent=2, ensure_ascii=False))
        return

    print("%s — Nasdaq Nordic reference data" % (details[0]["name"] or args.company))
    print()
    chosen_symbol = {cl["isin"]: cl["chosen"] for cl in cross_listed}
    print("  %-12s %-16s %18s %6s %20s %s"
          % ("CLASS", "ISIN", "SHARES", "CCY", "MARKET CAP", "SEGMENT"))
    print("  " + "-" * 100)
    for d in details:
        dup = d.get("isin") in chosen_symbol and d["symbol"] != chosen_symbol[d["isin"]]
        line = ("  %-12s %-16s %18s %6s %20s %s"
                % (d["symbol"], d["isin"] or "-",
                   "{:,.0f}".format(d["shares"]) if d["shares"] else "n/a",
                   d.get("currency") or "-",
                   "{:,.0f}".format(d["market_cap"]) if d["market_cap"] else "n/a",
                   d["segment"] or "-"))
        if dup:
            line += "   [cross-listing of %s, same ISIN — not counted separately]" \
                    % chosen_symbol[d["isin"]]
        print(line)
    print("  " + "-" * 100)
    print("  %-12s %-16s %18s" % ("TOTAL SHARES", "", "{:,.0f}".format(total_shares)))
    if not mixed_currency:
        print("  %-12s %-16s %18s %6s %20s"
              % ("TOTAL CAP", "", "", currencies[0] if currencies else "-",
                 "{:,.0f}".format(total_cap)))
    else:
        print()
        print("  Market cap NOT combined into one figure — the counted lines are in")
        print("  different currencies. Reporting per currency instead of summing them:")
        for ccy in sorted(cap_by_ccy):
            print("    %-6s %20s" % (ccy, "{:,.0f}".format(cap_by_ccy[ccy])))
        if fx_conversion:
            print()
            print("  Converted to SEK using dated Riksbank/ECB rates (shown, not hidden):")
            for row in fx_conversion["lines"]:
                print("    %-6s %20s  x %10s (obs %s, %s) = %20s SEK"
                      % (row["currency"], "{:,.0f}".format(row["amount"]),
                         "%.4f" % row["rate"], row["obs_date"], row["source"],
                         "{:,.0f}".format(row["converted_sek"])))
            print("    %-6s %20s %35s %20s SEK"
                  % ("TOTAL", "", "", "{:,.0f}".format(fx_conversion["total_sek"])))
        else:
            print()
            print("  FX conversion unavailable (macro_se.py not importable, or a rate")
            print("  could not be fetched) — the per-currency figures above are final.")

    if cross_listed:
        print()
        for cl in cross_listed:
            print("  !! %s is cross-listed on %d Nordic venues (%s) under ISIN %s."
                  % (details[0]["name"] or args.company, len(cl["venues"]),
                     ", ".join(v for v in cl["venues"] if v), cl["isin"]))
            print("     Counted once via %s — the other venues are the SAME shares"
                  % cl["chosen"])
            print("     quoted in a different currency, not additional shares.")

    notes = [d["note"] for d in details if d.get("note")]
    if notes:
        print()
        for n in set(notes):
            print("  !! Exchange note: %s" % n)

    print()
    if len(counted) == 1:
        print("  Only one listed class found. Verify against the issuer's latest")
        print("  \"Total number of voting rights and capital\" disclosure — an")
        print("  UNLISTED class would be invisible here. NIBE and Fenix Outdoor are")
        print("  known cases where that understates the count materially.")
    else:
        print("  %d listed classes summed (after ISIN de-duplication). Unlisted"
              % len(counted))
        print("  classes, if any, are still invisible — confirm against the")
        print("  issuer's voting-rights disclosure.")
    print("  Registered shares INCLUDING treasury. Subtract treasury before market")
    print("  cap or EV (valuation.md); never use this count for EPS.")


if __name__ == "__main__":
    main()
