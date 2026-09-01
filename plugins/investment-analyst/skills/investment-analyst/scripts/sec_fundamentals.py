#!/usr/bin/env python3
"""Pull traceable fundamentals for a US filer from SEC EDGAR XBRL company facts.

Every returned number carries the filing it came from (tag + form + accession),
so the analyst can mark it FACT rather than guessing.

Usage:
    python sec_fundamentals.py NVDA
    python sec_fundamentals.py NVDA --years 6
    python sec_fundamentals.py NVDA --json
    python sec_fundamentals.py NVDA --quarters
    python sec_fundamentals.py 0001045810            # CIK also accepted

Requires env SEC_USER_AGENT, e.g. "Firstname Lastname you@example.com".
SEC rejects requests without a descriptive User-Agent (fair-access policy).
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

UA = os.environ.get("SEC_USER_AGENT", "").strip()
BASE = "https://data.sec.gov"
TICKERS = "https://www.sec.gov/files/company_tickers.json"

# Ordered fallbacks: filers tag the same economic concept differently.
CONCEPTS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax",
                "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet",
                # ifrs-full names used by 20-F filers (e.g. TSMC) whose GAAP-style
                # concept names above never appear for them.
                "RevenueFromContractsWithCustomers", "Revenue"],
    "cogs": ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold",
             "CostOfSales"],
    "gross_profit": ["GrossProfit"],
    "rnd": ["ResearchAndDevelopmentExpense"],
    "sga": ["SellingGeneralAndAdministrativeExpense", "GeneralAndAdministrativeExpense"],
    "operating_income": ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"],
    "pretax_income": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "ProfitLossBeforeTax"],
    "tax": ["IncomeTaxExpenseBenefit", "IncomeTaxExpenseContinuingOperations"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "eps_basic": ["EarningsPerShareBasic"],
    "shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding",
                       "WeightedAverageNumberOfSharesOutstandingDiluted"],
    "shares_basic": ["WeightedAverageNumberOfSharesOutstandingBasic",
                     "WeightedAverageNumberOfSharesOutstanding"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
            "CashFlowsFromUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets"],
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
    "dividends": ["PaymentsOfDividendsCommonStock", "PaymentsOfOrdinaryDividends",
                  "PaymentsOfDividends"],
    "acquisitions": ["PaymentsToAcquireBusinessesNetOfCashAcquired"],
    "depreciation_amort": ["DepreciationDepletionAndAmortization",
                           "DepreciationAmortizationAndAccretionNet", "Depreciation"],
    # InterestIncomeExpenseNet deliberately excluded: it is a NET element whose
    # sign convention is the opposite, which would silently invert coverage.
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating",
                         "InterestExpenseDebt", "InterestAndDebtExpense"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "short_term_investments": ["ShortTermInvestments", "MarketableSecuritiesCurrent",
                               "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
                               "AvailableForSaleSecuritiesDebtMaturitiesWithinOneYearFairValue",
                               "OtherShortTermInvestments"],
    "total_assets": ["Assets"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "total_liabilities": ["Liabilities"],
    "lt_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "st_debt": ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "inventory": ["InventoryNet"],
    "receivables": ["AccountsReceivableNetCurrent"],
    "payables": ["AccountsPayableCurrent"],
    "goodwill": ["Goodwill"],
    "intangibles": ["IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet"],
}

# Duration ("flow") concepts vs. instant ("stock") balance-sheet concepts.
FLOW = {
    "revenue", "cogs", "gross_profit", "rnd", "sga", "operating_income", "pretax_income",
    "tax", "net_income", "eps_diluted", "eps_basic", "shares_diluted", "shares_basic",
    "cfo", "capex", "sbc", "buybacks", "dividends", "acquisitions", "depreciation_amort",
    "interest_expense",
}

# Metrics denominated in currency (used to detect the reporting currency).
MONEY = {"revenue","cogs","gross_profit","rnd","sga","operating_income","pretax_income",
         "tax","net_income","cfo","capex","sbc","buybacks","dividends","acquisitions",
         "depreciation_amort","interest_expense","cash","short_term_investments",
         "total_assets","current_assets","current_liabilities","total_liabilities",
         "lt_debt","st_debt","equity","inventory","receivables","payables","goodwill",
         "intangibles"}

ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A")
QUARTER_FORMS = ("10-Q", "10-Q/A", "10-K", "10-K/A", "6-K")


def _date(s):
    return datetime.date.fromisoformat(s)


def get(url, tries=4):
    if not UA:
        sys.exit('ERROR: set SEC_USER_AGENT first, e.g.\n'
                 '  export SEC_USER_AGENT="Adam Haglund adam.haglund@ajprodukter.se"')
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
                if enc == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                elif enc == "deflate":
                    import zlib
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 503) and attempt < tries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < tries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            sys.exit("DATA NOT AVAILABLE: could not reach SEC EDGAR (%s)" % e)
    raise RuntimeError("unreachable")


def resolve(symbol):
    symbol = symbol.strip()
    if symbol.isdigit():
        return symbol.zfill(10), None
    data = get(TICKERS)
    want = symbol.upper()
    for row in data.values():
        if row["ticker"].upper() == want:
            return str(row["cik_str"]).zfill(10), row["title"]
    sys.exit("DATA NOT AVAILABLE: ticker %r is not in SEC company_tickers.json.\n"
             "Non-US issuers (e.g. Nasdaq Stockholm) do not file with the SEC. "
             "See references/sweden.md for the Swedish source chain." % symbol)


def pick_unit(units, prefer=None):
    """Pick which unit's rows to use out of an XBRL fact node's units dict.

    `prefer`, when given and present in `units`, wins outright - this is how
    callers pin a tag to the filer's actual reporting currency (see
    detect_currency()) instead of grabbing whatever happens to be first.
    Without it, fall back to the old USD-first preference order, which is
    correct for the non-currency unit types (a per-share tag only ever has
    "USD/shares", a share-count tag only "shares") and is preserved here
    unchanged for backward compatibility with callers that inspect units
    directly (e.g. ttm_engine.py calls pick_unit(node["units"]) with no
    preference).
    """
    if prefer and prefer in units:
        return prefer, units[prefer]
    for u in ("USD", "USD/shares", "shares", "pure"):
        if u in units:
            return u, units[u]
    key = next(iter(units))
    return key, units[key]


def detect_currency(facts):
    """Determine the filer's actual primary reporting currency.

    pick_unit() used to grab "USD" unconditionally whenever it was present.
    That is wrong for a foreign private issuer: a 20-F reports primarily in
    the issuer's home currency (TWD, EUR, ...) and commonly *also* tags a USD
    convenience translation - usually covering only the most recent fiscal
    year (Form 20-F Item 8.A(2) only requires that much), sometimes a couple
    of years. Blindly preferring "USD" therefore serves that thin convenience
    slice as if it were the whole history, and silently mixes units across a
    series when the primary-currency rows for other periods get merged in
    from a different tag.

    The fix: tally annual-form (10-K/20-F/40-F family) datapoints per
    currency unit across a handful of core income-statement/balance-sheet
    concepts, and return whichever currency has the most datapoints. The true
    reporting currency has a full multi-year history under these concepts; a
    convenience translation, filed for one or two years, cannot outnumber it.
    A domestic US filer only ever tags "USD", so this reduces to "USD" for
    every 10-K filer - verified below against AAPL/NVDA.
    """
    CORE = ("revenue", "net_income", "operating_income", "total_assets", "gross_profit")
    tally = {}
    for metric in CORE:
        for tag in CONCEPTS.get(metric, ()):
            for ns in ("us-gaap", "ifrs-full"):
                node = facts.get("facts", {}).get(ns, {}).get(tag)
                if not node:
                    continue
                for unit, rows in node["units"].items():
                    # Only consider genuine ISO-4217-style currency codes -
                    # skip per-share/pure/count units, which never compete
                    # with a currency for the same tag.
                    if not (len(unit) == 3 and unit.isalpha() and unit.isupper()):
                        continue
                    n = sum(1 for r in rows if r.get("form") in ANNUAL_FORMS)
                    if n:
                        tally[unit] = tally.get(unit, 0) + n
    if not tally:
        return "USD"
    return max(tally, key=tally.get)


def series(facts, tags, annual=True, currency=None):
    """Return {period_end: fact}, merged across tags in priority order.

    Filers switch tags mid-history (NVIDIA moved off
    RevenueFromContractWithCustomerExcludingAssessedTax after FY2022), so taking
    only the first tag that has *any* data silently blanks recent years. Instead
    walk the fallback list in order and let each tag fill the periods its
    predecessors left empty.

    `currency`, when given, pins every tag to that one unit (falling back to
    pick_unit()'s default order only when this specific node has no data
    under `currency` at all - see main()'s retry). This is what stops a
    metric's series from mixing units: FY2023 and FY2024 must come from the
    same currency column of the same tag, or they are not comparable and one
    of them does not belong in this series.
    """
    out = {}
    for tag in tags:
        for ns in ("us-gaap", "ifrs-full", "dei"):
            node = facts.get("facts", {}).get(ns, {}).get(tag)
            if not node:
                continue
            if currency:
                if currency not in node["units"]:
                    # This tag never reported in the filer's primary
                    # currency (e.g. it only ever carries a USD convenience
                    # translation for one year) - skip it here rather than
                    # pull in a different, incompatible unit. main() retries
                    # without a currency pin if a whole metric ends up empty.
                    continue
                unit, rows = currency, node["units"][currency]
            else:
                unit, rows = pick_unit(node["units"])
            claimed = {}
            for r in rows:
                form = r.get("form")
                if annual:
                    if form not in ANNUAL_FORMS:
                        continue
                    if "start" in r and not (330 <= (_date(r["end"]) - _date(r["start"])).days <= 400):
                        continue
                else:
                    if form not in QUARTER_FORMS:
                        continue
                    if "start" in r and not (80 <= (_date(r["end"]) - _date(r["start"])).days <= 100):
                        continue
                key = r["end"]
                prev = claimed.get(key)
                # Keep the most recently filed version so restatements win.
                if prev is None or r.get("filed", "") >= prev.get("filed", ""):
                    claimed[key] = {"val": r["val"], "form": form, "end": r["end"],
                                    "accn": r.get("accn"), "fy": r.get("fy"),
                                    "fp": r.get("fp"), "filed": r.get("filed"),
                                    "tag": tag, "unit": unit}
            # Earlier tags in the fallback list win; later ones only fill gaps.
            for key, fact in claimed.items():
                out.setdefault(key, fact)
    return out


def fmt(metric, value):
    """Format a cell. Everything except EPS is scaled to millions so a small
    absolute value cannot be misread as a millions figure (0.1M printed as
    '100,000.00' beside '1,010M' is how that goes wrong)."""
    if "eps" in metric:
        return "%.2f" % value
    scaled = value / 1e6
    return "{:,.1f}M".format(scaled) if abs(scaled) < 10 else "{:,.0f}M".format(scaled)


def split_warning(result, periods):
    """Detect a stock split straddling the displayed columns.

    companyfacts only carries split-adjusted comparatives for periods that a
    later filing actually restated. Older columns therefore stay in pre-split
    basis, and EPS or per-share growth computed across the boundary is wrong by
    the split ratio.
    """
    shares = result.get("shares_diluted", {})
    eps = result.get("eps_diluted", {})
    for a, b in zip(periods, periods[1:]):
        sa, sb = shares.get(a), shares.get(b)
        ea, eb = eps.get(a), eps.get(b)
        if not (sa and sb and ea and eb):
            continue
        if not sa["val"] or not eb["val"]:
            continue
        share_jump = sb["val"] / sa["val"]
        eps_drop = ea["val"] / eb["val"] if eb["val"] else 0
        if share_jump > 1.5 and eps_drop > 1.5:
            return (a, b, share_jump)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol", help="ticker or 10-digit CIK")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--quarters", action="store_true", help="quarterly instead of annual")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()
    if args.years < 1:
        ap.error("--years must be at least 1")

    cik, name = resolve(args.symbol)
    facts = get("%s/api/xbrl/companyfacts/CIK%s.json" % (BASE, cik))
    name = facts.get("entityName", name)
    annual = not args.quarters
    span = args.years if annual else args.years * 4

    # Determine the filer's actual primary reporting currency up front, so
    # every money metric's series is pinned to the SAME currency across ALL
    # of its fallback tags and periods. Never let a metric mix units (e.g.
    # TWD for FY2023, USD for FY2024 because a convenience translation only
    # covers the latest year) - that silently corrupts any growth rate.
    reporting_currency = detect_currency(facts)

    result, tags_used, currency_fallback = {}, {}, set()
    for metric, tags in CONCEPTS.items():
        prefer = reporting_currency if metric in MONEY else None
        if not annual and metric not in FLOW:
            found = (series(facts, tags, annual=False, currency=prefer)
                      or series(facts, tags, annual=True, currency=prefer))
        else:
            found = series(facts, tags, annual=annual, currency=prefer)
        if not found and prefer:
            # Nothing at all for this metric in the primary currency (rare -
            # e.g. a line item only ever appears in a convenience-translation
            # filing). Retry without pinning currency rather than showing
            # nothing, but flag it: this one series may not be comparable to
            # the rest and should not be trusted for a growth rate.
            found = series(facts, tags, annual=annual, currency=None)
            if found:
                currency_fallback.add(metric)
        if found:
            keys = sorted(found)[-span:]
            result[metric] = {k: found[k] for k in keys}
            tags_used[metric] = found[keys[-1]]["tag"]

    periods = sorted({p for m in result.values() for p in m})[-span:]
    retrieved = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())

    # Currency must be visible: a 20-F filer reports in EUR or SEK, and an
    # unlabelled EUR EPS against a USD quote misstates P/E by the FX rate.
    # Recomputed from the actual per-fact units (not just `reporting_currency`)
    # so the warning reflects what was really served, including any
    # currency_fallback exceptions above.
    money_units = {v["unit"] for m, s in result.items() for v in s.values()
                   if m in MONEY and v.get("unit")}
    currency = "/".join(sorted(money_units)) or "unknown"

    if args.as_json:
        print(json.dumps({"cik": cik, "entity": name, "retrieved_utc": retrieved,
                          "basis": "annual" if annual else "quarterly",
                          "periods": periods,
                          "reporting_currency": reporting_currency,
                          "currency_fallback_metrics": sorted(currency_fallback),
                          "tags_used": tags_used,
                          "data": result}, indent=2))
        return

    print("%s  |  CIK %s  |  source: SEC EDGAR XBRL companyfacts" % (name, cik))
    print("basis: %s   currency: %s   retrieved: %s"
          % ("annual" if annual else "quarterly", currency, retrieved))
    if money_units and money_units != {"USD"}:
        print()
        print("!! REPORTING CURRENCY IS NOT USD (%s). Do not compare these figures"
              % currency)
        print("!! against a USD share price without converting. For IFRS filers,")
        print("!! esef_fundamentals.py gives better coverage than this script.")
        print("!! Detected as the filer's primary currency (most annual-filing")
        print("!! datapoints across revenue/net income/assets); any USD")
        print("!! convenience-translation figures were excluded so that no")
        print("!! metric's series mixes units across periods.")
    if currency_fallback:
        print()
        print("!! NO DATA IN THE PRIMARY CURRENCY (%s) for: %s"
              % (reporting_currency, ", ".join(sorted(currency_fallback))))
        print("!! These rows fall back to whatever unit was available and may")
        print("!! not be comparable across periods or to other metrics above.")
    print()
    w = 16
    print("metric".ljust(24) + "".join(p.rjust(w) for p in periods))
    print("-" * (24 + w * len(periods)))
    for metric in CONCEPTS:
        if metric not in result:
            continue
        line = metric.ljust(24)
        for p in periods:
            fact = result[metric].get(p)
            line += (fmt(metric, fact["val"]) if fact else "n/a").rjust(w)
        print(line)

    split = split_warning(result, periods)
    if split:
        a, b, ratio = split
        print()
        print("!! STOCK SPLIT between %s and %s (~%.0f:1). Columns before %s are in"
              % (a, b, ratio, b))
        print("!! PRE-SPLIT basis - EPS and share counts are not comparable across")
        print("!! that boundary. Do not compute per-share growth through it.")

    multi = {m: sorted({f["tag"] for f in s.values()})
             for m, s in result.items() if len({f["tag"] for f in s.values()}) > 1}
    if multi:
        print()
        print("Rows served by more than one XBRL tag (concepts may not be identical):")
        for m, tags in multi.items():
            print("  %-22s %s" % (m, " -> ".join(tags)))

    if not annual:
        print()
        print("NOTE on --quarters: 10-Q cash-flow figures are year-to-date, so cfo,")
        print("capex, buybacks and D&A appear only in Q1 columns. Q4 is never filed")
        print("as a discrete quarter - derive it as FY minus the first nine months.")

    print()
    print("Provenance of latest column (%s):" % (periods[-1] if periods else "n/a"))
    for metric in CONCEPTS:
        if metric in result and periods and periods[-1] in result[metric]:
            f = result[metric][periods[-1]]
            print("  %-22s %-60s %-6s %s" % (metric, f["tag"], f["form"], f["accn"]))

    print()
    print("Filings index: https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
          "&CIK=%s&type=10-&dateb=&owner=include&count=40" % cik)


if __name__ == "__main__":
    main()
