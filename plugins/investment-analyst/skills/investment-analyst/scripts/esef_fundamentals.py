#!/usr/bin/env python3
"""Fundamentals for European filers from ESEF (Inline XBRL) annual reports.

Since FY2020 every issuer on an EU/EEA regulated market must file its annual
financial report in ESEF - IFRS concepts tagged in Inline XBRL. XBRL
International aggregates those filings at filings.xbrl.org and publishes an
xBRL-JSON rendering of each, which is what this script reads.

This is the European counterpart to sec_fundamentals.py, with two honest
caveats that the SEC data does not have:

  * ESEF Phase 1 mandates tagging of the PRIMARY STATEMENTS only. Note-level
    detail is often absent, so metrics like SBC or capex may not be tagged.
  * Issuers may define their own EXTENSION concepts for lines that do not map
    to IFRS. Those are invisible to a standard-taxonomy lookup.

Gaps are therefore normal. They are reported as DATA NOT AVAILABLE rather than
inferred - read the annual report PDF for anything missing.

Coverage confirmed 2026-08-31: FR, SE, NO, DK, FI, AT, NL, BE, IT, ES, LU.
GERMANY IS NOT COVERED - German issuers file with the Bundesanzeiger, which is
not harvested here. See references/europe.md for the German route.

Usage:
    python esef_fundamentals.py --search "Hermes"          # find the LEI
    python esef_fundamentals.py 969500Y4IJGHJE2MTJ13       # by LEI
    python esef_fundamentals.py --search "Evolution AB" --country SE
    python esef_fundamentals.py <LEI> --filings 4 --json
"""
import argparse
import datetime
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

FILINGS_API = "https://filings.xbrl.org/api/filings"
FILINGS_BASE = "https://filings.xbrl.org"
GLEIF_API = "https://api.gleif.org/api/v1/lei-records"
UA = "Mozilla/5.0 (compatible; investment-analyst-skill/1.0)"

NOT_COVERED = {"DE": "Germany - issuers file with the Bundesanzeiger",
               "IE": "Ireland - not harvested by filings.xbrl.org"}

# IFRS taxonomy concepts, ordered fallbacks. Names are given without the
# ifrs-full: prefix; any namespace ending in the concept name matches, so
# taxonomy-year differences do not break the lookup.
CONCEPTS = {
    "revenue": ["Revenue", "RevenueFromContractsWithCustomers",
                "RevenueFromSaleOfGoods", "RevenueFromRenderingOfServices"],
    "cost_of_sales": ["CostOfSales", "CostOfMerchandiseSold"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["ProfitLossFromOperatingActivities",
                         "OperatingIncomeLoss", "ProfitLossFromOperatingActivitiesContinuingOperations"],
    "pretax_income": ["ProfitLossBeforeTax"],
    "tax": ["IncomeTaxExpenseContinuingOperations", "IncomeTaxExpenseBenefit"],
    "net_income": ["ProfitLoss", "ProfitLossAttributableToOwnersOfParent"],
    "eps_basic": ["BasicEarningsLossPerShare"],
    "eps_diluted": ["DilutedEarningsLossPerShare"],
    "cfo": ["CashFlowsFromUsedInOperatingActivities"],
    "cfi": ["CashFlowsFromUsedInInvestingActivities"],
    "cff": ["CashFlowsFromUsedInFinancingActivities"],
    # IFRS reports FX translation on cash as its own line. Omitting it breaks
    # the cash roll-forward for any group with foreign operations.
    "fx_on_cash": ["EffectOfExchangeRateChangesOnCashAndCashEquivalents",
                   "EffectOfExchangeRateChangesOnCashAndCashEquivalentsIncludingCashInSubsidiariesHeldForSale"],
    "net_change_cash": ["IncreaseDecreaseInCashAndCashEquivalents",
                        "IncreaseDecreaseInCashAndCashEquivalentsBeforeEffectOfExchangeRateChanges"],
    "capex": ["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
              "PurchaseOfPropertyPlantAndEquipmentIntangibleAssetsOtherThanGoodwillInvestmentPropertyAndOtherNoncurrentAssets",
              "PaymentsToAcquirePropertyPlantAndEquipment"],
    "depreciation_amort": ["DepreciationAndAmortisationExpense",
                           "DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss"],
    "sbc": ["ShareBasedPaymentsExpense", "ExpenseFromShareBasedPaymentTransactions"],
    "dividends_paid": ["DividendsPaidClassifiedAsFinancingActivities", "DividendsPaid"],
    "interest_expense": ["InterestExpense", "FinanceCosts"],
    # Instant / balance-sheet concepts
    "cash": ["CashAndCashEquivalents"],
    "total_assets": ["Assets"],
    "current_assets": ["CurrentAssets"],
    "current_liabilities": ["CurrentLiabilities"],
    "total_liabilities": ["Liabilities"],
    "equity": ["Equity", "EquityAttributableToOwnersOfParent"],
    "borrowings": ["Borrowings", "BorrowingsNoncurrent"],
    "borrowings_current": ["CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
                           "ShorttermBorrowings"],
    "lease_liabilities": ["LeaseLiabilities", "LeaseLiabilitiesNoncurrent"],
    "inventory": ["Inventories"],
    "receivables": ["TradeAndOtherCurrentReceivables", "CurrentTradeReceivables"],
    "payables": ["TradeAndOtherCurrentPayables", "CurrentTradePayables"],
    "goodwill": ["Goodwill"],
    "intangibles": ["IntangibleAssetsOtherThanGoodwill"],
}

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def get_json(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit("DATA NOT AVAILABLE: HTTP %s for %s" % (e.code, url))
    except urllib.error.URLError as e:
        raise SystemExit("DATA NOT AVAILABLE: could not reach %s (%s)" % (url, e.reason))
    except (TimeoutError, OSError) as e:
        # A raw socket timeout during r.read() (as opposed to urlopen()
        # itself) is not wrapped in URLError, so it escaped uncaught here.
        # ttm_engine.esef_observations() catches SystemExit only, so this
        # crashed the whole run rather than degrading gracefully.
        raise SystemExit("DATA NOT AVAILABLE: timed out reaching %s (%s)" % (url, e))


def search_index(name, country):
    """Substring-search the ESEF index by issuer name within one country.

    The API only supports exact entity.name matching, so page through the
    country's filings with the entity included and match locally. This is more
    reliable than resolving via GLEIF, because a company can hold several LEIs
    and only one of them is the ESEF filer.
    """
    needle = name.lower()
    found, page = {}, 1
    while page <= 8:
        params = {"filter[country]": country, "include": "entity",
                  "page[size]": "500", "page[number]": str(page),
                  "sort": "-period_end"}
        data = get_json(FILINGS_API + "?" + urllib.parse.urlencode(params))
        rows = data.get("data") or []
        if not rows:
            break
        entities = {e["id"]: e["attributes"].get("name")
                    for e in data.get("included", [])}
        for it in rows:
            rel = ((it.get("relationships") or {}).get("entity") or {}).get("data") or {}
            ent_name = entities.get(rel.get("id"))
            if ent_name and needle in ent_name.lower():
                a = it["attributes"]
                lei = (a.get("fxo_id") or "").split("-")[0]
                prev = found.get(lei)
                if prev is None or a["period_end"] > prev["latest"]:
                    found[lei] = {"name": ent_name, "latest": a["period_end"],
                                  "country": a.get("country")}
        page += 1
    return [dict(lei=k, **v) for k, v in found.items()]


def search_lei(name, country=None):
    params = {"filter[entity.legalName]": name, "page[size]": "10"}
    if country:
        params["filter[entity.legalAddress.country]"] = country
    data = get_json(GLEIF_API + "?" + urllib.parse.urlencode(params))
    out = []
    for rec in data.get("data", []):
        ent = rec["attributes"]["entity"]
        out.append({
            "lei": rec["id"],
            "name": ent["legalName"]["name"],
            "country": (ent.get("legalAddress") or {}).get("country"),
            "status": ent.get("status"),
        })
    return out


def list_filings(lei, limit=5):
    params = {"filter[entity.identifier]": lei,
              "page[size]": str(max(limit, 10)), "sort": "-period_end"}
    data = get_json(FILINGS_API + "?" + urllib.parse.urlencode(params))
    out = []
    for f in data.get("data", []):
        a = f["attributes"]
        if not a.get("json_url"):
            continue
        # date_added is when the index HARVESTED the filing, not when the issuer
        # published it - Sandvik's FY2024 report was added 2025-05-08 but
        # published around February 2025. It is therefore a CONSERVATIVE UPPER
        # BOUND on publication, and that is the safe direction to be wrong in:
        # used as a point-in-time cutoff it excludes a filing you might have
        # had, and can never admit one you could not. Never present it as the
        # publication date itself.
        added = (a.get("date_added") or "")[:10] or None
        out.append({"period_end": a["period_end"], "country": a.get("country"),
                    "json_url": a["json_url"], "fxo_id": a.get("fxo_id"),
                    "errors": a.get("error_count"), "warnings": a.get("warning_count"),
                    "indexed_date": added,
                    "publication_upper_bound": added,
                    "publication_date_exact": None})
    return out[:limit]


def normalise_end(date_str):
    """Convert an end-exclusive period end to the reported balance-sheet date.

    xBRL commonly expresses a year as [2025-01-01, 2026-01-01), and the instant
    at the close of that year as 2026-01-01. Both mean fiscal year end
    2025-12-31. Issuers that already report inclusive dates (2025-12-31) land on
    a day other than the 1st and are left alone.
    """
    try:
        d = datetime.date.fromisoformat(date_str)
    except ValueError:
        return date_str
    if d.day == 1:
        d -= datetime.timedelta(days=1)
    return d.isoformat()


def parse_period(period):
    """xBRL-JSON period: instant 'YYYY-MM-DDT..' or duration 'start/end'."""
    if not period:
        return None, None
    if "/" in period:
        start, end = period.split("/", 1)
        return start[:10], normalise_end(end[:10])
    return None, normalise_end(period[:10])


def extract(doc):
    """Return {concept_localname: [(start, end, value, unit)]} for consolidated facts."""
    out = {}
    for fact in doc.get("facts", {}).values():
        dims = fact.get("dimensions") or {}
        # Extra axes mean a segment/component breakdown, not the group total.
        if any(k not in ("concept", "period", "unit", "entity", "language")
               for k in dims):
            continue
        concept = dims.get("concept", "")
        if ":" not in concept:
            continue
        local = concept.split(":", 1)[1]
        start, end = parse_period(dims.get("period"))
        value = fact.get("value")
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        out.setdefault(local, []).append((start, end, value, dims.get("unit")))
    return out


def pick(facts, names, want_duration):
    """First matching concept wins; return {period_end: (value, unit, concept)}."""
    result = {}
    for name in names:
        rows = facts.get(name)
        if not rows:
            continue
        for start, end, value, unit in rows:
            is_duration = start is not None
            if is_duration != want_duration:
                continue
            if is_duration:
                try:
                    days = (datetime.date.fromisoformat(end)
                            - datetime.date.fromisoformat(start)).days
                except ValueError:
                    continue
                if not (330 <= days <= 400):
                    continue
            result.setdefault(end, (value, unit, name))
    return result


DURATION = {"revenue", "cost_of_sales", "gross_profit", "operating_income",
            "pretax_income", "tax", "net_income", "eps_basic", "eps_diluted",
            "cfo", "cfi", "cff", "fx_on_cash", "net_change_cash", "capex",
            "depreciation_amort", "sbc", "dividends_paid", "interest_expense"}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lei", nargs="?", help="20-character LEI")
    ap.add_argument("--search", help="find the LEI for a company name")
    ap.add_argument("--country", help="ISO-2 country filter for --search")
    ap.add_argument("--filings", type=int, default=3,
                    help="annual reports to merge (each carries 2 years)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    if args.search and args.country:
        hits = search_index(args.search, args.country.upper())
        if hits:
            print("ESEF filers in %s matching %r:" % (args.country.upper(), args.search))
            for h in sorted(hits, key=lambda x: x["latest"], reverse=True):
                print("  %s  %-44.44s latest FY %s"
                      % (h["lei"], h["name"], h["latest"]))
            print()
            print("Then: python esef_fundamentals.py <LEI>")
            return
        print("No ESEF filing indexed in %s for %r. Falling back to GLEIF."
              % (args.country.upper(), args.search))
        print()

    if args.search:
        hits = search_lei(args.search, args.country)
        if not hits:
            print("DATA NOT AVAILABLE: no LEI matched %r." % args.search)
            return
        print("GLEIF matches for %r (LEI registry, not proof of an ESEF filing):"
              % args.search)
        for h in hits:
            note = NOT_COVERED.get(h["country"])
            flag = ("   [NOT in ESEF index: %s]" % note) if note else ""
            print("  %s  %-44.44s %s  %s%s"
                  % (h["lei"], h["name"], h["country"] or "--", h["status"] or "", flag))
        print()
        print("Then: python esef_fundamentals.py <LEI>")
        return

    if not args.lei:
        ap.error("give a LEI, or use --search NAME to find one")

    filings = list_filings(args.lei, args.filings)
    if not filings:
        print("DATA NOT AVAILABLE: no ESEF filings indexed for LEI %s." % args.lei)
        print("Germany and Ireland are not covered - see references/europe.md.")
        return

    merged, provenance = {}, {}
    for f in filings:
        doc = get_json(FILINGS_BASE + f["json_url"])
        facts = extract(doc)
        for metric, names in CONCEPTS.items():
            found = pick(facts, names, metric in DURATION)
            for period, (value, unit, concept) in found.items():
                # Newest filing processed first, so keep the first value seen
                # for a period - that is the most recently restated figure.
                merged.setdefault(metric, {}).setdefault(
                    period, {"val": value, "unit": unit, "concept": concept,
                             "filing": f["fxo_id"]})
        provenance[f["fxo_id"]] = f

    if not merged:
        print("DATA NOT AVAILABLE: filings found but no standard IFRS concepts "
              "matched. The issuer likely uses extension taxonomy tags; read the "
              "annual report directly.")
        return

    periods = sorted({p for m in merged.values() for p in m})[-(args.filings + 1):]
    retrieved = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    # Units look like 'iso4217:EUR', 'xbrli:shares' or the per-share compound
    # 'iso4217:EUR/xbrli:shares'. Only a bare iso4217:XXX names the currency -
    # splitting the compound on ':' yields 'shares', which is not a currency.
    units = {v["unit"] for m in merged.values() for v in m.values() if v.get("unit")}
    currencies = sorted({u.split(":", 1)[1] for u in units
                         if u.startswith("iso4217:") and "/" not in u})
    currency = "/".join(currencies) or "unknown"

    if args.as_json:
        print(json.dumps({"lei": args.lei, "currency": currency,
                          "retrieved_utc": retrieved, "periods": periods,
                          "filings": filings, "data": merged}, indent=2))
        return

    print("LEI %s  |  source: ESEF via filings.xbrl.org  |  currency %s"
          % (args.lei, currency))
    print("filings merged: %s" % ", ".join(f["fxo_id"] for f in filings))
    print("retrieved: %s" % retrieved)
    print()
    w = 16
    print("metric".ljust(24) + "".join(p.rjust(w) for p in periods))
    print("-" * (24 + w * len(periods)))
    for metric in CONCEPTS:
        if metric not in merged:
            continue
        line = metric.ljust(24)
        for p in periods:
            f = merged[metric].get(p)
            if not f:
                line += "n/a".rjust(w)
            elif "eps" in metric:
                line += ("%.2f" % f["val"]).rjust(w)
            elif abs(f["val"]) >= 1e6:
                line += "{:,.0f}M".format(f["val"] / 1e6).rjust(w)
            else:
                line += "{:,.2f}".format(f["val"]).rjust(w)
        print(line)

    missing = [m for m in CONCEPTS if m not in merged]
    if missing:
        print()
        print("DATA NOT AVAILABLE (not tagged in these filings): %s" % ", ".join(missing))
        print("ESEF mandates primary-statement tagging only. Read the annual")
        print("report PDF for these - do not infer them.")

    print()
    print("Provenance of latest column (%s):" % (periods[-1] if periods else "n/a"))
    for metric in CONCEPTS:
        if metric in merged and periods and periods[-1] in merged[metric]:
            f = merged[metric][periods[-1]]
            print("  %-22s %-62s %s" % (metric, f["concept"], f["filing"]))


if __name__ == "__main__":
    main()
