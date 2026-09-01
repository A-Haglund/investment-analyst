#!/usr/bin/env python3
"""Swedish institutional ownership, from Finansinspektionen's fund holdings register.

Every Swedish UCITS fund files its complete line-item holdings with FI each
quarter. Reverse-indexed by ISIN, that answers a question no free source
otherwise answers: which domestic institutions own this company, how much, and
how large a bet it is inside each of their funds.

This is not the full shareholder register - it covers Swedish-domiciled funds
only, so foreign institutions, the AP funds' direct holdings, and private
owners are absent. It is a floor on institutional ownership, not the whole of
it. Say so when reporting.

Two readings matter more than the raw list:

  * Concentration in the holder base. A name held by forty funds at token
    weights is differently owned from one held by six at 5% of NAV each.
  * Conviction. A fund with 8% of its NAV in one company has taken a real
    position; the analyst behind it has done work worth respecting.

Usage:
    python ownership_se.py --isin SE0012673267
    python ownership_se.py --name "Evolution"
    python ownership_se.py --isin SE0000163628 --quarter 2025Q4
    python ownership_se.py --name "Addtech" --json
    python ownership_se.py --quarters            # list what FI has published

Source: https://www.fi.se/sv/vara-register/fondinnehav-per-kvartal/
"""
import argparse
import datetime
import html
import json
import os
import re
import statistics
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

LIST_URL = "https://www.fi.se/sv/vara-register/fondinnehav-per-kvartal/"
DOWNLOAD = "https://www.fi.se/FondInnehavLista/download"
UA = "Mozilla/5.0 (compatible; investment-analyst-skill/1.0)"
CACHE = os.path.join(tempfile.gettempdir(), "fi-fondinnehav-cache")
LIST_TTL = 24 * 3600
MAX_MEMBER_BYTES = 64 * 1024 * 1024     # decompression-bomb ceiling per file

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def local(tag):
    return tag.split("}")[-1]


def http_get(url, timeout=180):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        raise SystemExit("DATA NOT AVAILABLE: could not reach FI (%s)" % e)


def available_quarters():
    """Scrape the published quarters. Filenames carry a timestamp, so they
    cannot be constructed - the list page is the only index."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, "quarters.html")
    if not (os.path.exists(path) and time.time() - os.path.getmtime(path) < LIST_TTL):
        with open(path, "wb") as f:
            f.write(http_get(LIST_URL, timeout=60))
    page = open(path, encoding="utf-8", errors="replace").read()
    out = {}
    for m in re.finditer(r'filnamn=(Fondinnehav_(\d{4}Q[1-4])[^"&]*\.zip)', page):
        out[m.group(2)] = html.unescape(m.group(1))
    return out


def fetch_quarter(quarter, filename):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, "%s.zip" % quarter)
    if os.path.exists(path) and os.path.getsize(path) > 100000:
        return path
    url = DOWNLOAD + "?" + urllib.parse.urlencode({"filnamn": filename})
    data = http_get(url)
    if not data.startswith(b"PK"):
        raise SystemExit("DATA NOT AVAILABLE: FI did not return a zip for %s." % quarter)
    with open(path, "wb") as f:
        f.write(data)
    return path


_SUBMITTED = re.compile(r"_(\d{4}-\d\d-\d\d \d\d\.\d\d)(?:\.xml)?$")


def _latest_submissions(z):
    """One XML member per fund: the most recently submitted one.

    FI files corrections INTO the same quarterly archive instead of replacing
    the original, so Fondinnehav_2025Q1.zip carries AP7 Aktiefond under both
    'Sjunde AP-fonden_91117_2025-04-09 10.37/' and
    'Sjunde AP-fonden_91117_2026-06-10 14.18/'. Reading both double-counts
    every holding of Sweden's largest equity fund.
    """
    best = {}
    for info in z.infolist():
        if not info.filename.lower().endswith(".xml"):
            continue
        parts = info.filename.replace("\\", "/").split("/")
        leaf = parts[-1][:-4]
        folder = parts[-2] if len(parts) > 1 else ""
        stamps = [m.group(1) for m in (_SUBMITTED.search(leaf),
                                       _SUBMITTED.search(folder)) if m]
        submitted = max(stamps) if stamps else ""
        key = (_SUBMITTED.sub("", folder), _SUBMITTED.sub("", leaf))
        prev = best.get(key)
        if prev is None or submitted >= prev[0]:
            best[key] = (submitted, info)
    return [info for _s, info in best.values()]


def build_index(zip_path):
    """{isin: [holding, ...]} across every fund in the quarter."""
    index, quarter_end = {}, None
    with zipfile.ZipFile(zip_path) as z:
        for info in _latest_submissions(z):
            if info.file_size > MAX_MEMBER_BYTES:
                continue
            raw = z.read(info)
            # The archive is untrusted input. stdlib ElementTree does not resolve
            # external entities, and defusedxml is unavailable here, so refuse
            # any document declaring a DTD - a fund report never carries one.
            if b"<!DOCTYPE" in raw[:2048] or b"<!ENTITY" in raw[:2048]:
                continue
            try:
                root = ET.fromstring(raw.decode("utf-8-sig", errors="replace"))
            except ET.ParseError:
                continue

            manager = ""
            for child in root:
                name = local(child.tag)
                if name == "Rapportinformation":
                    for x in child:
                        if local(x.tag) == "Kvartalsslut":
                            quarter_end = quarter_end or (x.text or "").strip()
                elif name == "Bolagsinformation":
                    for x in child:
                        if local(x.tag) == "Fondbolag_namn":
                            manager = (x.text or "").strip()

            for fund in root:
                if local(fund.tag) != "Fondinformation":
                    continue
                fund_name, nav = "", None
                instruments = None
                for x in fund:
                    tag = local(x.tag)
                    if tag == "Fond_namn":
                        fund_name = (x.text or "").strip()
                    elif tag == "Fondförmögenhet":
                        try:
                            nav = float((x.text or "0").replace(",", "."))
                        except ValueError:
                            nav = None
                    elif tag == "FinansiellaInstrument":
                        instruments = x
                if instruments is None:
                    continue

                for inst in instruments:
                    row = {local(c.tag): (c.text or "").strip() for c in inst}
                    isin = row.get("ISIN-kod_instrument", "").strip().upper()
                    if not isin:
                        continue

                    def num(key):
                        try:
                            return float(row.get(key, "").replace(",", "."))
                        except (ValueError, AttributeError):
                            return None

                    # Marknadsvärde_instrument is in the FUND's reporting
                    # currency - SEK for a Swedish UCITS - while Valuta is the
                    # currency the instrument is QUOTED in. Verified against a
                    # line in FI's own file: 1490 Allianz at EUR 359.30 with an
                    # FX rate of 10.9391 is filed as 5,856,323.76, which is SEK.
                    # Printing "156,086,586,237 USD" for Apple, as a naive read
                    # of Valuta gives, overstates the holding tenfold.
                    index.setdefault(isin, []).append({
                        "manager": manager,
                        "fund": fund_name,
                        "fund_nav": nav,
                        "instrument": row.get("Instrumentnamn", ""),
                        "shares": num("Antal"),
                        "value": num("Marknadsvärde_instrument"),
                        "value_currency": "SEK",
                        "pct_of_fund": num("Andel_av_fondförmögenhet_instrument"),
                        "price": num("Kurs_som_använts_vid_värdering_av_instrumentet"),
                        "fx_to_sek": num("Valutakurs_instrument"),
                        "currency": row.get("Valuta", ""),
                    })
    return index, quarter_end


def load_index(quarter=None):
    quarters = available_quarters()
    if not quarters:
        raise SystemExit("DATA NOT AVAILABLE: could not read FI's quarter list.")
    chosen = quarter or max(quarters)
    if chosen not in quarters:
        raise SystemExit("DATA NOT AVAILABLE: FI has no %s. Published: %s"
                         % (chosen, ", ".join(sorted(quarters))))

    # v2: the cached shape gained price / fx_to_sek / value_currency, so an
    # index written by an earlier run would silently lack them.
    cached = os.path.join(CACHE, "%s-index-v3.json" % chosen)
    if os.path.exists(cached):
        with open(cached, encoding="utf-8") as f:
            blob = json.load(f)
        return blob["index"], blob["quarter_end"], chosen

    index, quarter_end = build_index(fetch_quarter(chosen, quarters[chosen]))
    with open(cached, "w", encoding="utf-8") as f:
        json.dump({"index": index, "quarter_end": quarter_end}, f)
    return index, quarter_end, chosen


# ------------------------------------------------------------------- analysis

def quarter_back(chosen, n):
    """The quarter n publications before `chosen`, or None.

    Stepping through FI's own published list rather than doing calendar
    arithmetic: the list has gaps (nothing before 2018Q4) and a missed
    publication would otherwise silently shift the comparison by a quarter.
    """
    qs = sorted(available_quarters())
    if chosen not in qs:
        return None
    i = qs.index(chosen) - n
    return qs[i] if i >= 0 else None


def holdings_in(isin, quarter):
    """One company's fund holdings in one quarter, or ([], None) if absent."""
    index, quarter_end, _ = load_index(quarter)
    return index.get(isin, []), quarter_end


def fund_key(h):
    """A fund is identified by manager + fund name.

    Fund names are not unique across managers ("Sverigefond" is everywhere),
    and FI's export carries no stable fund id in this file, so the pair is the
    best key available. A fund that is renamed between quarters therefore looks
    like one exit and one addition - flagged in the output, not silently
    smoothed over.
    """
    return "%s | %s" % (h.get("manager") or "?", h.get("fund") or "?")


def concentration(holdings):
    """How much of the disclosed fund ownership sits in the largest holders.

    Forty funds holding a token weight each is index and allocation flow.
    Five funds holding 80% of the disclosed total is a real owner base whose
    behaviour moves the share, and whose exit is a risk worth naming.
    """
    vals = sorted((h["value"] or 0.0) for h in holdings)[::-1]
    total = sum(vals)
    if total <= 0:
        return None
    out = {"total_value": total}
    for n in (1, 3, 5, 10):
        out["top%d_pct" % n] = 100.0 * sum(vals[:n]) / total
    # Herfindahl on the disclosed base only: 10000 = a single fund owns it all.
    out["hhi"] = sum((100.0 * v / total) ** 2 for v in vals)
    return out


def position_stats(holdings):
    """Counts, totals, and the average vs median that says how skewed it is."""
    shares = [h["shares"] or 0.0 for h in holdings]
    values = [h["value"] or 0.0 for h in holdings]
    navs = [h["pct_of_fund"] for h in holdings if h["pct_of_fund"] is not None]
    # Quote currency of the instrument, NOT the currency of the value column.
    currencies = sorted({(h.get("currency") or "").strip()
                         for h in holdings if h.get("currency")})
    return {
        "fund_count": len(holdings),
        "manager_count": len({(h.get("manager") or "?") for h in holdings}),
        "total_shares": sum(shares),
        "total_value": sum(values),
        "value_currency": "SEK",
        "quote_currencies": currencies,
        "mean_value": statistics.mean(values) if values else None,
        "median_value": statistics.median(values) if values else None,
        "mean_shares": statistics.mean(shares) if shares else None,
        "median_shares": statistics.median(shares) if shares else None,
        "largest_value": max(values) if values else None,
        "smallest_value": min(values) if values else None,
        "mean_pct_of_nav": statistics.mean(navs) if navs else None,
        "median_pct_of_nav": statistics.median(navs) if navs else None,
        "conviction_funds": len([h for h in holdings
                                 if (h["pct_of_fund"] or 0) >= 4.0]),
    }


def compare_quarters(now, then, q_now, q_then):
    """Funds added, funds exited, and the net share change between quarters.

    Share count is the comparable quantity, not market value: value moves with
    the share price, so a fund that sold a third of its position during a
    rising quarter can still show a higher value. Both are reported, but the
    added/exited/net conclusion is drawn from the share count.
    """
    if then is None:
        return {"quarter": q_then, "available": False,
                "reason": "FI has published no earlier quarter to compare with"}
    a = {fund_key(h): h for h in now}
    b = {fund_key(h): h for h in then}
    added = [a[k] for k in a if k not in b]
    exited = [b[k] for k in b if k not in a]
    kept = [k for k in a if k in b]
    changes = []
    for k in kept:
        d = (a[k]["shares"] or 0.0) - (b[k]["shares"] or 0.0)
        if d:
            changes.append({"fund": a[k]["fund"], "manager": a[k]["manager"],
                            "then_shares": b[k]["shares"] or 0.0,
                            "now_shares": a[k]["shares"] or 0.0,
                            "change_shares": d})

    # A fund appearing in BOTH the added and exited lists under the same fund
    # name but a different manager has not turned over: the management company
    # was renamed or the fund was transferred. Storebrand Fonder AB became
    # Storebrand Asset Management AS between 2025Q4 and 2026Q1, and left
    # uncorrected that single rebrand fabricated five exits and five additions
    # in Sandvik alone. Reclassify them as continuity and keep their real
    # share change.
    add_names, exit_names = {}, {}
    for h in added:
        add_names.setdefault(h["fund"], []).append(h)
    for h in exited:
        exit_names.setdefault(h["fund"], []).append(h)
    reclassified = []
    for name in sorted(set(add_names) & set(exit_names)):
        # Only safe when the name is unambiguous on both sides.
        if len(add_names[name]) != 1 or len(exit_names[name]) != 1:
            continue
        x, y = add_names[name][0], exit_names[name][0]
        d = (x["shares"] or 0.0) - (y["shares"] or 0.0)
        reclassified.append({"fund": name, "from_manager": y["manager"],
                             "to_manager": x["manager"],
                             "then_shares": y["shares"] or 0.0,
                             "now_shares": x["shares"] or 0.0,
                             "change_shares": d})
        changes.append({"fund": name, "manager": x["manager"],
                        "then_shares": y["shares"] or 0.0,
                        "now_shares": x["shares"] or 0.0,
                        "change_shares": d})
    moved = {r["fund"] for r in reclassified}
    added = [h for h in added if h["fund"] not in moved]
    exited = [h for h in exited if h["fund"] not in moved]
    changes.sort(key=lambda c: -abs(c["change_shares"]))
    sh_now = sum(h["shares"] or 0.0 for h in now)
    sh_then = sum(h["shares"] or 0.0 for h in then)
    val_now = sum(h["value"] or 0.0 for h in now)
    val_then = sum(h["value"] or 0.0 for h in then)
    return {
        "quarter": q_then, "available": True,
        "fund_count_then": len(then), "fund_count_now": len(now),
        "fund_count_change": len(now) - len(then),
        "total_shares_then": sh_then, "total_shares_now": sh_now,
        "net_share_change": sh_now - sh_then,
        "net_share_change_pct": (100.0 * (sh_now - sh_then) / sh_then
                                 if sh_then else None),
        "total_value_then": val_then, "total_value_now": val_now,
        "added": sorted([{"fund": h["fund"], "manager": h["manager"],
                          "shares": h["shares"] or 0.0, "value": h["value"] or 0.0}
                         for h in added], key=lambda x: -x["shares"]),
        "exited": sorted([{"fund": h["fund"], "manager": h["manager"],
                           "shares": h["shares"] or 0.0, "value": h["value"] or 0.0}
                          for h in exited], key=lambda x: -x["shares"]),
        "changes": changes,
        "manager_renames": reclassified,
        "direction": ("ACCUMULATING" if sh_now > sh_then * 1.01 else
                      "DISTRIBUTING" if sh_now < sh_then * 0.99 else "FLAT"),
    }


def fmt(x, dp=0):
    return "DATA NOT AVAILABLE" if x is None else ("{:,.%df}" % dp).format(x)


_SHARE_CLASS_RE = re.compile(r"\s+(?:ser\.?\s*)?[A-D]$", re.I)


def issuer_stem(instrument_name):
    """Strip a trailing share-class letter ('A', 'B', 'ser. C', ...) so that
    two listed classes of ONE issuer (Atlas Copco A / Atlas Copco B) collapse
    to the same stem, while two different issuers keep their own names."""
    return _SHARE_CLASS_RE.sub("", instrument_name or "").strip()


def resolve_name(index, name):
    """Resolve a --name substring query against the index.

    FI's holdings archive files a company's bonds under the same issuer name
    as its equity: Antal (shares) is empty and Nominellt_belopp is populated
    on every bond row, while every equity row carries a share count.
    Requiring at least one row with a non-empty share count keeps bonds out
    of the match entirely - a name match on debt alone is not an equity hit.

    A single issuer routinely lists more than one share class (Atlas Copco
    A/B, Investor A/B, ...). Collapsing the surviving equity ISINs by issuer
    stem (the instrument name with a trailing class letter stripped) means
    two classes of ONE issuer resolve rather than refuse. Two DIFFERENT
    issuers whose names merely share a fragment still refuse, and the
    refusal names every issuer it saw - the same distinction
    company_resolve.py and peers_se.py draw elsewhere in this toolkit.

    Returns a dict, one of:
      {"status": "resolved", "isin", "holdings", "label",
       "other_classes": [{"isin", "instrument", "value"}, ...]}
      {"status": "ambiguous",
       "candidates": [{"isin", "instrument", "value", "fund_count"}, ...]}
      {"status": "absent"}
    """
    needle = name.lower()
    equity = {}
    for isin, h in index.items():
        if not any(needle in (x["instrument"] or "").lower() for x in h):
            continue
        if not any(x.get("shares") is not None for x in h):
            continue  # every row is a bond (Antal empty) - not an equity hit
        equity[isin] = h

    if not equity:
        return {"status": "absent"}

    def instrument_of(h):
        for x in h:
            if x["instrument"]:
                return x["instrument"]
        return ""

    def value_of(h):
        return sum(x["value"] or 0 for x in h)

    groups = {}
    for isin, h in equity.items():
        groups.setdefault(issuer_stem(instrument_of(h)), []).append((isin, h))

    if len(groups) > 1:
        candidates = [{"isin": isin, "instrument": instrument_of(h),
                       "value": value_of(h), "fund_count": len(h)}
                      for isin, h in equity.items()]
        candidates.sort(key=lambda c: -c["value"])
        return {"status": "ambiguous", "candidates": candidates}

    [(_stem, items)] = groups.items()
    items.sort(key=lambda t: -value_of(t[1]))
    isin, holdings = items[0]
    other_classes = [{"isin": i, "instrument": instrument_of(h), "value": value_of(h)}
                     for i, h in items[1:]]
    return {"status": "resolved", "isin": isin, "holdings": holdings,
           "label": "%s (%s)" % (instrument_of(holdings), isin),
           "other_classes": other_classes}


def data_age_days(quarter_end):
    """Days between a quarter end (YYYY-MM-DD) and today, or None if unknown."""
    if not quarter_end:
        return None
    try:
        return (datetime.date.today() - datetime.date.fromisoformat(quarter_end)).days
    except ValueError:
        return None


# --------------------------------------------------------------------- output

def print_stats(st, conc):
    quoted = "/".join(st["quote_currencies"]) or "?"
    print("  Funds holding          %d   (across %d fund managers)"
          % (st["fund_count"], st["manager_count"]))
    print("  Shares held, disclosed %s" % fmt(st["total_shares"]))
    print("  Market value           %s SEK" % fmt(st["total_value"]))
    if quoted not in ("SEK", "?"):
        print("    Values are SEK, the funds' reporting currency; the share itself")
        print("    is quoted in %s. FI's Valuta column is the QUOTE currency, not"
              % quoted)
        print("    the currency of the value - do not relabel these figures.")
    print()
    print("  Position size          %-22s %s" % ("SHARES", "VALUE (SEK)"))
    print("    average              %-22s %s"
          % (fmt(st["mean_shares"]), fmt(st["mean_value"])))
    print("    median               %-22s %s"
          % (fmt(st["median_shares"]), fmt(st["median_value"])))
    print("    largest              %-22s %s" % ("", fmt(st["largest_value"])))
    if (st["mean_value"] or 0) > 0 and st["median_value"] is not None:
        ratio = st["mean_value"] / st["median_value"] if st["median_value"] else None
        if ratio and ratio > 2:
            print("    Mean is %.1fx the median: the disclosed base is dominated by a"
                  % ratio)
            print("    few large funds, and the typical holder is far smaller than the")
            print("    average suggests. Read the median, not the average.")
    print("    Weight in the holding funds: mean %s%%, median %s%% of NAV"
          % (fmt(st["mean_pct_of_nav"], 2), fmt(st["median_pct_of_nav"], 2)))
    print()
    if conc:
        print("  Concentration of the disclosed total (by market value)")
        print("    largest fund   %5.1f%%" % conc["top1_pct"])
        print("    top 3          %5.1f%%" % conc["top3_pct"])
        print("    top 5          %5.1f%%" % conc["top5_pct"])
        print("    top 10         %5.1f%%" % conc["top10_pct"])
        print("    HHI            %5.0f   (10000 = one fund owns the whole"
              " disclosed base)" % conc["hhi"])
        if conc["top5_pct"] >= 70:
            print("    Concentrated: five funds carry most of the disclosed holding, so")
            print("    domestic fund flow in this name is a handful of decisions.")
        elif conc["top5_pct"] <= 40:
            print("    Diffuse: no small group of funds dominates, which usually means")
            print("    index and allocation flow rather than active positions.")
    else:
        print("  Concentration: DATA NOT AVAILABLE — no market values reported.")


def print_trend(cmp_, label):
    print("  %s — vs %s" % (label, cmp_["quarter"] or "n/a"))
    if not cmp_["available"]:
        print("    DATA NOT AVAILABLE: %s" % cmp_["reason"])
        return
    print("    Funds holding    %d  ->  %d   (%+d)"
          % (cmp_["fund_count_then"], cmp_["fund_count_now"],
             cmp_["fund_count_change"]))
    print("    Shares held      %s  ->  %s"
          % (fmt(cmp_["total_shares_then"]), fmt(cmp_["total_shares_now"])))
    pctv = cmp_["net_share_change_pct"]
    print("    Net share change %s   (%s)   %s"
          % (fmt(cmp_["net_share_change"]),
             "%+.1f%%" % pctv if pctv is not None else "n/a",
             cmp_["direction"]))
    if cmp_["added"]:
        print("    Funds added (%d), largest first:" % len(cmp_["added"]))
        for x in cmp_["added"][:6]:
            print("      +%-14s %-34.34s %s"
                  % (fmt(x["shares"]), x["fund"], x["manager"][:26]))
        if len(cmp_["added"]) > 6:
            print("      ... %d more (--json)" % (len(cmp_["added"]) - 6))
    if cmp_["exited"]:
        print("    Funds exited (%d), largest first:" % len(cmp_["exited"]))
        for x in cmp_["exited"][:6]:
            print("      -%-14s %-34.34s %s"
                  % (fmt(x["shares"]), x["fund"], x["manager"][:26]))
        if len(cmp_["exited"]) > 6:
            print("      ... %d more (--json)" % (len(cmp_["exited"]) - 6))
    if cmp_["changes"]:
        print("    Largest changes among funds present in both quarters:")
        for c in cmp_["changes"][:6]:
            print("      %+14s %-34.34s %s"
                  % (fmt(c["change_shares"]), c["fund"], c["manager"][:26]))
    if cmp_["manager_renames"]:
        print("    %d fund(s) reclassified as continuity, not turnover — same fund"
              % len(cmp_["manager_renames"]))
        print("    name under a renamed or transferred management company:")
        seen = set()
        for r in cmp_["manager_renames"][:4]:
            pair = (r["from_manager"], r["to_manager"])
            if pair in seen:
                continue
            seen.add(pair)
            print("      %s  ->  %s" % (r["from_manager"], r["to_manager"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--isin", help="the company's ISIN, e.g. SE0012673267")
    ap.add_argument("--name", help="instrument name substring, if the ISIN is unknown")
    ap.add_argument("--quarter", help="e.g. 2025Q4 (default: latest published)")
    ap.add_argument("--quarters", action="store_true", help="list published quarters")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--no-trend", action="store_true", dest="no_trend",
                    help="skip the quarter-over-quarter comparison (avoids "
                         "downloading two earlier quarters)")
    args = ap.parse_args()

    if args.quarters:
        qs = available_quarters()
        print("FI has published fund holdings for %d quarters:" % len(qs))
        print("  " + ", ".join(sorted(qs)))
        return

    if not (args.isin or args.name):
        ap.error("give --isin or --name, or use --quarters")

    index, quarter_end, quarter = load_index(args.quarter)

    other_classes = []
    if args.isin:
        isin = args.isin.strip().upper()
        holdings = index.get(isin, [])
        label = isin
    else:
        result = resolve_name(index, args.name)
        if result["status"] == "ambiguous":
            print("Several issuers match %r — pick an ISIN:" % args.name)
            for c in result["candidates"][:12]:
                print("  %-16s %-40.40s %d funds"
                      % (c["isin"], c["instrument"], c["fund_count"]))
            return
        if result["status"] == "absent":
            # No ISIN to key a cross-quarter comparison on, so the trend cannot
            # be computed even though the name is known.
            holdings, label, isin = [], args.name, None
        else:
            isin, holdings, label = result["isin"], result["holdings"], result["label"]
            other_classes = result["other_classes"]

    if not holdings:
        print("DATA NOT AVAILABLE: no Swedish fund reported holding %s in %s."
              % (label, quarter))
        print()
        print("That is a finding, not a blank: no Swedish UCITS fund held the name")
        print("at the quarter end. Foreign institutions and direct holdings are")
        print("outside this register, so it is not evidence of no institutional")
        print("ownership at all — only of no domestic fund ownership.")
        # Absence this quarter and absence always are different findings.
        if not args.no_trend and args.isin:
            prev_q = quarter_back(quarter, 1)
            if prev_q:
                prev, _ = holdings_in(isin, prev_q)
                if prev:
                    print()
                    print("It WAS held in %s, by %d fund(s) holding %s shares. Every"
                          % (prev_q, len(prev),
                             "{:,.0f}".format(sum(h["shares"] or 0 for h in prev))))
                    print("Swedish fund exited the name in one quarter — that is a")
                    print("finding worth chasing, not a data gap.")
        return

    holdings.sort(key=lambda h: -(h["value"] or 0))
    total_value = sum(h["value"] or 0 for h in holdings)
    total_shares = sum(h["shares"] or 0 for h in holdings)

    stats = position_stats(holdings)
    conc = concentration(holdings)

    # Quarter-over-quarter. One quarter back is the flow; four quarters back is
    # the trend, and the two often disagree - a name being sold this quarter
    # after a year of accumulation is not the same story as steady distribution.
    trends = {}
    if not args.no_trend:
        for n, key in ((1, "vs_1q"), (4, "vs_4q")):
            q_prev = quarter_back(quarter, n)
            prev = holdings_in(isin, q_prev)[0] if q_prev else None
            trends[key] = compare_quarters(holdings, prev, quarter, q_prev)

    if args.as_json:
        print(json.dumps({"query": label, "isin": isin, "quarter": quarter,
                          "quarter_end": quarter_end,
                          "source": "Finansinspektionen fondinnehav",
                          "basis": "Swedish UCITS funds only — a FLOOR on "
                                   "institutional ownership, never the total",
                          "retrieved_utc": datetime.datetime.now(
                              datetime.timezone.utc).isoformat(),
                          "fund_count": len(holdings),
                          "total_shares": total_shares, "total_value": total_value,
                          "stats": stats, "concentration": conc,
                          "trend": trends, "other_share_classes": other_classes,
                          "holdings": holdings}, indent=2, ensure_ascii=False))
        return

    print("Swedish fund ownership — %s" % label)
    print("Source: Finansinspektionen fondinnehav, %s (quarter end %s)"
          % (quarter, quarter_end or "n/a"))
    if other_classes:
        print("Also listed (not included above — use --isin to pick one): %s"
              % ", ".join("%s (%s)" % (o["instrument"], o["isin"])
                          for o in other_classes[:6]))
    print()
    print_stats(stats, conc)
    print()

    print("  %-34.34s %-30.30s %14s %8s" % ("FUND", "MANAGER", "VALUE", "% NAV"))
    print("  " + "-" * 90)
    for h in holdings[:args.limit]:
        print("  %-34.34s %-30.30s %14s %7s%%"
              % (h["fund"], h["manager"],
                 "{:,.0f}".format(h["value"] or 0),
                 ("%.2f" % h["pct_of_fund"]) if h["pct_of_fund"] is not None else "-"))
    if len(holdings) > args.limit:
        print("  ... %d more (use --limit or --json)" % (len(holdings) - args.limit))

    # Conviction is the position's share of the FUND's NAV, not the position's
    # size in SEK - sort by pct_of_fund, not by the value ordering `holdings`
    # already carries from the table above.
    conviction = sorted([h for h in holdings if (h["pct_of_fund"] or 0) >= 4.0],
                        key=lambda h: -(h["pct_of_fund"] or 0))
    print()
    if conviction:
        print("  High-conviction positions (>= 4%% of the fund's NAV): %d"
              % len(conviction))
        for h in conviction[:6]:
            print("    %.2f%%  %-38.38s  %s"
                  % (h["pct_of_fund"], h["fund"], h["manager"]))
        print("  A manager with this much of one fund in a single name has done real")
        print("  work on it. Their view is worth understanding before dismissing it.")
    else:
        print("  No fund holds this at 4% or more of NAV — ownership is broad and")
        print("  shallow, which usually means index and allocation flows rather than")
        print("  active conviction.")

    print()
    if trends:
        print("Ownership trend — disclosed Swedish fund holdings across quarters")
        print_trend(trends["vs_1q"], "One quarter back")
        print()
        print_trend(trends["vs_4q"], "Four quarters back")
        print()
        print("  Funds are matched on manager + fund name, the only stable key in")
        print("  FI's file. A renamed or merged fund therefore shows as one exit")
        print("  plus one addition rather than as continuity — check the names")
        print("  before reading a large added/exited count as real turnover.")
        print("  Conclusions are drawn from SHARE counts, not market value: value")
        print("  moves with the price, so a fund that cut its position in a rising")
        print("  quarter can still report a higher value.")
    elif args.no_trend:
        print("Ownership trend: skipped (--no-trend).")

    print()
    print("Swedish UCITS funds only. Foreign institutions, AP-fund direct holdings")
    print("and private owners are outside this register — treat the figure as a")
    print("floor on institutional ownership, not the whole of it.")
    print("Reported quarterly at quarter end, published with a lag.")
    age = data_age_days(quarter_end)
    if age is not None:
        print("Quarter end %s was %d days ago — this is a stock, not a flow you"
              % (quarter_end, age))
        print("can trade against.")
    else:
        print("Quarter end date DATA NOT AVAILABLE — treat this as a stock, not a")
        print("flow you can trade against.")


if __name__ == "__main__":
    main()
