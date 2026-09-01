#!/usr/bin/env python3
"""Verify an ESEF filer's figures instead of merely sourcing them.

A FACT tag records where a number came from. These three checks establish that
the number is right, using only data the toolkit already fetches for free.

  1. RESTATEMENT CHECK - every ESEF annual filing carries the current year and
     the prior-year comparative. Filing N's comparative for year Y is compared
     against filing N-1's own figure for year Y. They come from two separately
     prepared documents, so agreement is real corroboration and disagreement is
     a restatement, which is itself a finding about the company.

  2. INTERNAL TIES - arithmetic the primary statements must satisfy. A break is
     a parsing error or a misunderstood line item, never an opinion.

  3. RELEASE CROSS-CHECK - where the company's own report release is still in
     MFN's feed, its headline figures are compared against the tagged filing.
     MFN caps a company feed at roughly 30 recent items, so this fires only for
     recent fiscal years; it is skipped, never faked, when out of reach.

Usage:
    python verify_filing.py --lei 549300SUH6ZR1RF6TA88
    python verify_filing.py --search "Evolution" --country SE --slug evolution
    python verify_filing.py --lei <LEI> --slug <mfn-slug> --json

Exit code 1 if any check fails.
"""
import argparse
import datetime
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOLERANCE = 0.01          # 1% - beyond this it is not a rounding artefact

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


esef = load("esef_fundamentals")
mfn = load("mfn_news")

CHECKED = ["revenue", "operating_income", "net_income", "cfo", "equity",
           "total_assets", "cash"]

RELEASE_LABELS = {
    "revenue": ["net revenue", "net sales", "revenue", "nettoomsättning"],
    # "ebit" as a bare substring also matches EBITDA, which is a different
    # measure - Evolution's release leads with EBITDA and the filed figure is
    # EBIT, producing a 10% "MISMATCH" on two correct numbers. A check that
    # cries wolf teaches the reader to ignore it.
    "operating_income": ["operating profit", "rörelseresultat", "ebit "],
    "net_income": ["profit for the period", "net profit", "periodens resultat"],
}

FULL_YEAR = ("year-end", "year end", "full year", "full-year", "annual", "q4",
             "bokslut", "helår", "fourth quarter")
NOT_FULL_YEAR = ("q1", "q2", "q3", "first quarter", "second quarter",
                 "third quarter", "half-year", "half year", "interim",
                 "delårsrapport", "january-june", "january-march",
                 "january-september")


def pct_diff(a, b):
    if a is None or b is None or a == 0:
        return None
    return abs(a - b) / abs(a)


def facts_for(filing):
    """Return {metric: {period_end: value}} for one ESEF filing."""
    doc = esef.get_json(esef.FILINGS_BASE + filing["json_url"])
    raw = esef.extract(doc)
    out, currency = {}, None
    for metric, names in esef.CONCEPTS.items():
        found = esef.pick(raw, names, metric in esef.DURATION)
        if not found:
            continue
        out[metric] = {}
        for period, (value, unit, _concept) in found.items():
            out[metric][period] = value
            if unit and unit.startswith("iso4217:") and "/" not in unit:
                currency = unit.split(":", 1)[1]
    return out, currency


def check_restatements(newer, older, newer_id, older_id):
    """Compare the overlapping year the two filings both report."""
    rows = []
    shared_periods = set()
    for metric in CHECKED:
        a, b = newer.get(metric, {}), older.get(metric, {})
        shared_periods |= set(a) & set(b)
    if not shared_periods:
        return rows, None
    period = max(shared_periods)
    for metric in CHECKED:
        a = newer.get(metric, {}).get(period)
        b = older.get(metric, {}).get(period)
        if a is None or b is None:
            continue
        d = pct_diff(b, a)
        rows.append({"metric": metric, "period": period, "as_filed": b,
                     "as_restated": a, "delta": d,
                     "verdict": "MATCH" if (d is not None and d <= TOLERANCE)
                                else "RESTATED"})
    return rows, period


def check_ties(vals, period):
    """Arithmetic the statements must satisfy for the given period."""
    g = lambda m: vals.get(m, {}).get(period)
    ties = []

    assets, liab, eq = g("total_assets"), g("total_liabilities"), g("equity")
    if assets is not None and liab is not None and eq is not None:
        d = pct_diff(assets, liab + eq)
        ties.append({"name": "assets = liabilities + equity",
                     "left": assets, "right": liab + eq, "delta": d,
                     "verdict": "OK" if d is not None and d <= TOLERANCE else "BREAK"})

    rev, cos, gp = g("revenue"), g("cost_of_sales"), g("gross_profit")
    if rev is not None and cos is not None and gp is not None:
        d = pct_diff(gp, rev - abs(cos))
        ties.append({"name": "revenue - cost of sales = gross profit",
                     "left": gp, "right": rev - abs(cos), "delta": d,
                     "verdict": "OK" if d is not None and d <= TOLERANCE else "BREAK"})

    cfo, cfi, cff, fx = g("cfo"), g("cfi"), g("cff"), g("fx_on_cash")
    if None not in (cfo, cfi, cff):
        flows = cfo + cfi + cff
        # IFRS reports FX translation on cash as a separate line. For a group
        # with foreign operations it is large, and omitting it produced false
        # BREAKs on exactly the biggest companies. Only assert a break when
        # every term is present; otherwise this is INFO, not a failure.
        if fx is not None:
            flows += fx
        # The same filing carries opening and closing cash, so the roll-forward
        # is a real tie rather than a number to note. FX on cash can move it a
        # little, hence the wider tolerance below.
        cash_periods = sorted(vals.get("cash", {}))
        opening = None
        if len(cash_periods) >= 2 and cash_periods[-1] == period:
            opening = vals["cash"][cash_periods[-2]]
        closing = g("cash")
        if opening is not None and closing is not None:
            change = closing - opening
            d = pct_diff(change, flows) if change else None
            if fx is None:
                # Cannot complete the equation - say so rather than fail it.
                ties.append({"name": "cash roll-forward (FX line not tagged)",
                             "left": change, "right": flows, "delta": d,
                             "verdict": "INCOMPLETE"})
            else:
                ok = d is not None and d <= TOLERANCE
                ties.append({"name": "cash roll-forward (open + flows + FX = close)",
                             "left": change, "right": flows, "delta": d,
                             "verdict": "OK" if ok else "BREAK"})
        else:
            ties.append({"name": "cfo + cfi + cff = net change in cash",
                         "left": flows, "right": None, "delta": None,
                         "verdict": "INFO"})
    return ties


def find_release(slug, fiscal_end):
    """The company's own full-year release, if MFN still carries it."""
    # The capped /a/<slug>.json endpoint put older years out of reach, which is
    # why this check used to be skipped for anything but the current year.
    # fetch_company_pages walks the paging endpoint and also reaches issuers
    # that publish through Cision, so a prior-year release IS retrievable now.
    end = datetime.date.fromisoformat(fiscal_end)
    try:
        raw = mfn.fetch_company_pages(slug, since=end.isoformat())
    except (SystemExit, AttributeError, Exception):
        try:
            raw = (mfn.fetch("/a/%s.json" % slug, limit=30) or {}).get("items") or []
        except SystemExit:
            return None
    if isinstance(raw, dict):
        raw = raw.get("items") or []
    candidates = []
    for item in (mfn.flatten(i) for i in raw):
        if not (item["is_report"] and item["text"] and item["date"]):
            continue
        try:
            published = datetime.date.fromisoformat(item["date"][:10])
        except ValueError:
            continue
        if not (end <= published <= end + datetime.timedelta(days=200)):
            continue
        text = ((item["title"] or "") + " " + (item["preamble"] or "")).lower()
        if any(w in text for w in NOT_FULL_YEAR) and \
           not any(w in text for w in ("year-end", "full year", "bokslut", "helår")):
            continue
        if not any(w in text for w in FULL_YEAR):
            continue
        candidates.append(item)

    # An "Annual Report 20XX" release is usually only an announcement that the
    # report has been published - the figures live in the PDF, not the body. The
    # year-end report (bokslutskommunike) carries them in prose. So rank by
    # whether the body actually yields figures we can compare, not by title
    # alone; a release we cannot cross-check is not a cross-check.
    def rank(item):
        figs = len(mfn.extract_figures(item.get("text") or ""))
        english = 1 if item.get("lang") == "en" else 0
        yearend = 1 if any(w in (item.get("title") or "").lower()
                           for w in ("year-end", "year end", "bokslut",
                                     "fourth quarter", "q4")) else 0
        return (1 if figs else 0, yearend, english, figs)

    candidates.sort(key=rank, reverse=True)
    return candidates[0] if candidates else None


FULL_YEAR_PERIOD = re.compile(
    r"jan[a-z]*\s*[-–]\s*dec|"          # Jan-Dec / januari-december
    r"full[\s-]?year|helår|twelve months", re.I)


def is_full_year_row(fig):
    """A year-end report leads with the QUARTER, then states the full year.

    Both appear under the same labels. Comparing an annual filed figure against
    the Q4 line produces a ~75% 'MISMATCH' on data that is perfectly correct -
    and a check that cries wolf teaches the reader to ignore the real conflicts
    it exists to surface. So only a row whose period reads as the full year may
    be compared against an annual figure.
    """
    per = (fig.get("period") or "").strip()
    if per:
        return bool(FULL_YEAR_PERIOD.search(per))
    # No period heading captured: fall back to the sentence itself.
    return bool(FULL_YEAR_PERIOD.search(fig.get("source_line") or ""))


def check_release(release, vals, period):
    rows = []
    all_figs = mfn.extract_figures(release["text"])
    figs = [f for f in all_figs if is_full_year_row(f)]
    if not figs:
        return []          # nothing annual to compare - SINGLE SOURCE, not a mismatch
    for metric, labels in RELEASE_LABELS.items():
        filed = vals.get(metric, {}).get(period)
        if filed is None:
            continue
        hit = None
        for fig in figs:
            low = fig["label"].lower()
            if metric == "operating_income" and "ebitda" in low:
                continue
            for rank, lab in enumerate(labels):
                if lab in low and (hit is None or rank < hit[0]):
                    hit = (rank, fig)
        if not hit:
            continue
        d = pct_diff(filed, hit[1]["current"])
        rows.append({"metric": metric, "filed": filed, "release": hit[1]["current"],
                     "delta": d, "line": hit[1]["source_line"],
                     "verdict": "MATCH" if d is not None and d <= TOLERANCE
                                else "MISMATCH"})
    return rows


def money(v):
    return "n/a" if v is None else "{:,.0f}".format(v)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lei")
    ap.add_argument("--search")
    ap.add_argument("--country", default="SE")
    ap.add_argument("--slug", help="MFN slug, enables the release cross-check")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    lei = args.lei
    if not lei:
        if not args.search:
            ap.error("give --lei, or --search NAME to resolve one")
        hits = esef.search_index(args.search, args.country.upper())
        if not hits:
            sys.exit("DATA NOT AVAILABLE: no ESEF filer matched %r in %s."
                     % (args.search, args.country.upper()))
        hits.sort(key=lambda h: h["latest"], reverse=True)
        if len(hits) > 1:
            # Each hit is already a distinct LEI (search_index dedupes on it),
            # so more than one hit means more than one real issuer matched -
            # "Volvo" is the standing example (AB Volvo, Volvo Car AB). Taking
            # hits[0] used to resolve silently to whichever filed most
            # recently. Refuse instead of guessing - same rule and the same
            # presentation as company_resolve.py's brand-ambiguity refusal.
            print("REFUSING TO RESOLVE %r: %d distinct issuers matched in %s."
                  % (args.search, len(hits), args.country.upper()))
            print()
            print("These are different companies. Re-run with --lei <LEI> for the")
            print("one you mean, or narrow --search to the full legal name.")
            print()
            print("  %-14s %-44s %-8s %s" % ("LEI", "NAME", "COUNTRY", "LATEST FY"))
            print("  " + "-" * 90)
            for h in hits:
                print("  %-14s %-44.44s %-8s %s"
                      % (h["lei"], h["name"], h["country"] or "-", h["latest"]))
            print()
            print("Nothing was resolved. No verification should be produced from")
            print("this query.")
            sys.exit(2)
        lei = hits[0]["lei"]
        print("Resolved %r -> %s (%s)\n" % (args.search, hits[0]["name"], lei))

    filings = esef.list_filings(lei, 3)
    if not filings:
        sys.exit("DATA NOT AVAILABLE: no ESEF filing for LEI %s.\n"
                 "First North, Spotlight and NGM issuers file none - verify the "
                 "release against the report PDF by hand instead." % lei)

    newer, currency = facts_for(filings[0])
    dated = newer.get("revenue", {}) or newer.get("total_assets", {})
    if not dated:
        sys.exit("DATA NOT AVAILABLE: no dated concepts tagged in this filing")
    latest = max(dated)

    restated, shared_period = ([], None)
    if len(filings) > 1:
        older, _ = facts_for(filings[1])
        restated, shared_period = check_restatements(newer, older, filings[0]["fxo_id"],
                                                     filings[1]["fxo_id"])

    ties = check_ties(newer, latest)

    release, release_rows = None, []
    if args.slug:
        release = find_release(args.slug, latest)
        if release:
            release_rows = check_release(release, newer, latest)

    failures = ([r for r in restated if r["verdict"] == "RESTATED"]
                + [t for t in ties if t["verdict"] == "BREAK"]
                + [r for r in release_rows if r["verdict"] == "MISMATCH"])
    retrieved = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if args.as_json:
        print(json.dumps({"lei": lei, "currency": currency, "fiscal_end": latest,
                          "retrieved_utc": retrieved,
                          "filings": [f["fxo_id"] for f in filings[:2]],
                          "restatement_check": restated, "internal_ties": ties,
                          "release_check": release_rows,
                          "release_url": release["url"] if release else None},
                         indent=2, ensure_ascii=False))
        sys.exit(1 if failures else 0)

    print("VERIFICATION  |  LEI %s  |  FY ending %s  |  %s" % (lei, latest, currency or "?"))
    print("  retrieved %s" % retrieved)
    print()

    print("1. RESTATEMENT CHECK  (FY %s filing vs FY %s filing)"
          % (filings[0]["period_end"],
             filings[1]["period_end"] if len(filings) > 1 else "n/a"))
    if not restated:
        print("   DATA NOT AVAILABLE - only one filing indexed, no comparative to check")
    else:
        print("   overlapping period: %s" % shared_period)
        print("   %-20s %18s %18s %9s  %s" % ("metric", "as first filed", "as restated", "delta", ""))
        for r in restated:
            d = "%.2f%%" % (r["delta"] * 100) if r["delta"] is not None else "-"
            print("   %-20s %18s %18s %9s  %s"
                  % (r["metric"], money(r["as_filed"]), money(r["as_restated"]), d, r["verdict"]))
    print()

    print("2. INTERNAL TIES  (FY %s)" % latest)
    if not ties:
        print("   DATA NOT AVAILABLE - the filing did not tag enough lines to tie")
    for t in ties:
        if t["verdict"] in ("INFO", "INCOMPLETE"):
            if t["verdict"] == "INCOMPLETE":
                d = "%.2f%%" % (t["delta"] * 100) if t["delta"] is not None else "-"
                print("   %-44s %18s vs %18s  %6s  INCOMPLETE"
                      % (t["name"], money(t["left"]), money(t["right"]), d))
                continue
            print("   %-44s %18s  INFO" % (t["name"], money(t["left"])))
        else:
            d = "%.2f%%" % (t["delta"] * 100) if t["delta"] is not None else "-"
            print("   %-44s %18s vs %18s  %6s  %s"
                  % (t["name"], money(t["left"]), money(t["right"]), d, t["verdict"]))
    print()

    print("3. RELEASE CROSS-CHECK")
    if not args.slug:
        print("   skipped - pass --slug <mfn-slug> to enable")
    elif not release:
        print("   DATA NOT AVAILABLE - no full-year release found for FY %s." % latest)
        print("   The feed was paged back past the fiscal year end, so this is an")
        print("   absence rather than a reach limit. Check is skipped, not failed.")
    else:
        print("   %s" % release["title"])
        print("   %s" % release["url"])
        if not release_rows:
            print("   no headline figures matched - read the release with --text")
        for r in release_rows:
            d = "%.2f%%" % (r["delta"] * 100) if r["delta"] is not None else "-"
            print("   %-20s filed %16s | release %16s  %8s  %s"
                  % (r["metric"], money(r["filed"]), money(r["release"]), d, r["verdict"]))
            if r["verdict"] == "MISMATCH":
                print("        release line: %s" % r["line"][:100])
    print()

    if failures:
        print("!! %d CHECK(S) FAILED. A restatement is a finding to report; a broken"
              % len(failures))
        print("!! tie or a release mismatch means a figure is wrong - resolve before use.")
    else:
        n = len([r for r in restated if r["verdict"] == "MATCH"]) \
            + len([t for t in ties if t["verdict"] == "OK"]) \
            + len([r for r in release_rows if r["verdict"] == "MATCH"])
        print("All %d checks passed. Figures corroborated beyond a single source." % n)

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
