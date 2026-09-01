#!/usr/bin/env python3
"""Swedish net short positions from Finansinspektionen's blankningsregister.

Disclosed short interest is the strongest free evidence available for a bear
case: a named professional has committed capital against the company and filed
it with the regulator. Free, no API key, no cookie.

Two universes, and the difference matters analytically:

  * Holder-level files list only positions >= 0.5%, with the holder named.
  * The aggregate file sums everything >= 0.1%, so total short interest is
    typically around twice the sum of the named holders. Embracer at the time
    of writing: 7.98% aggregate against 4.50% from four named holders.

Reporting only the named holders understates the short base by roughly half.
This script prints both, and never lets the named sum stand in for the total.

The level is only half the read. A 6% short base that has halved in two months
is a covering trade; the same 6% built from nothing over the same weeks is a
thesis being expressed. So the script also reconstructs the position history
holder by holder and reports the 30- and 90-day change, who is new, who added,
who cut and who has closed.

Usage:
    python short_se.py "Embracer"
    python short_se.py "Embracer" --history
    python short_se.py --top 20
    python short_se.py "Elekta" --json
    python short_se.py --lei 549300HGV012CNC8JD22      # AB Volvo, unambiguous
    python short_se.py "Volvo" --isin SE0021628898      # Volvo Car AB only

A NAME IS NOT AN IDENTITY. "Volvo" matches both AB Volvo (0.79% aggregate)
and Volvo Car AB (4.59%) - two different listed companies. FI's own data
carries the same ambiguity twice over: "Evolution" matches both Evolution AB
(the gambling company) and the dormant "Evolution Services Sweden AB". FI's
aggregate file gives each issuer an LEI; the holder-level files give an ISIN
but no LEI. Every name query is resolved against both before anything is
printed: when it names one company, the report reads as before; when it
names more than one, this script reports each SEPARATELY and never sums a
"Volvo" total across them - see group_by_company(). Pass --lei or --isin to
pin the issuer directly and skip the check.

Source: https://www.fi.se/sv/vara-register/blankningsregistret/
Cite an issuer as: .../blankningsregistret/emittent?id=<LEI>
"""
import argparse
import datetime
import difflib
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict

# company_resolve.py holds the toolkit's canonical identity logic. It is
# owned and evolved by another script in this toolkit, so it is imported
# defensively: this file must keep working standalone whether that module is
# missing, an older revision, or raises on import for any reason.
try:
    _spec = importlib.util.spec_from_file_location(
        "company_resolve",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "company_resolve.py"))
    company_resolve = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(company_resolve)
except Exception:
    company_resolve = None

BASE = "https://www.fi.se/BlankningsRegister/"
FILES = {
    "current": "GetAktuellFile",                  # holder-level, >= 0.5%
    "history": "GetHistFile",                     # holder-level, back to 2010-05-10
    "aggregated": "GetBlankningsregisterAggregat",  # per-issuer sum, >= 0.1%, with LEI
}
UA = "Mozilla/5.0 (compatible; investment-analyst-skill/1.0)"
CACHE = os.path.join(tempfile.gettempdir(), "fi-blankning-cache")
TTL = 3600      # FI throttles heavy users and may block without notice

NS_T = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
NS_X = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
NS_O = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"

# Windows for the change analysis, in calendar days.
WINDOWS = (30, 90)

# A holder whose position moves by less than this is noise, not a decision:
# FI rounds to two decimals and a position drifts with the share count.
EPS = 0.005

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def fetch(kind):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, kind + ".ods")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < TTL:
        return path
    req = urllib.request.Request(BASE + FILES[kind], headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = r.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        raise SystemExit("DATA NOT AVAILABLE: could not reach FI (%s)" % e)
    if not data.startswith(b"PK"):
        raise SystemExit("DATA NOT AVAILABLE: FI returned something that is not "
                         "an ODS file. The endpoint may have moved.")
    with open(path, "wb") as f:
        f.write(data)
    return path


MAX_CONTENT_BYTES = 256 * 1024 * 1024      # decompression-bomb ceiling


def ods_rows(path):
    """Yield the first sheet's rows as lists of strings.

    ODS is a zip; content.xml inside is UTF-8. Every row ends with a cell
    carrying number-columns-repeated="16379" and sheets end with rows repeated
    thousands of times - expanding those naively exhausts memory, so any repeat
    above 100 is treated as filler.

    Two defensive notes. The archive is untrusted input even though it comes
    from a government host, so the declared uncompressed size is checked before
    reading - a zip bomb is the realistic attack on an ODS download. XXE is
    handled by refusing any document carrying a DOCTYPE: stdlib ElementTree
    does not resolve external entities, and defusedxml is not available here.
    """
    with zipfile.ZipFile(path) as z:
        try:
            info = z.getinfo("content.xml")
        except KeyError:
            raise SystemExit("DATA NOT AVAILABLE: FI's file has no content.xml.")
        if info.file_size > MAX_CONTENT_BYTES:
            raise SystemExit("DATA NOT AVAILABLE: FI's file expands to %d bytes, "
                             "above the %d byte safety ceiling. Not parsed."
                             % (info.file_size, MAX_CONTENT_BYTES))
        raw = z.read("content.xml")

    head = raw[:2048].lstrip()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise SystemExit("DATA NOT AVAILABLE: FI's file declares a DTD, which a "
                         "spreadsheet export never should. Refusing to parse it.")
    root = ET.fromstring(raw)
    for table in root.iter("{%s}table" % NS_T):
        for row in table.findall("{%s}table-row" % NS_T):
            if int(row.get("{%s}number-rows-repeated" % NS_T, "1")) > 100:
                continue
            cells = []
            for cell in row:
                if cell.tag.split("}")[1] not in ("table-cell", "covered-table-cell"):
                    continue
                repeat = int(cell.get("{%s}number-columns-repeated" % NS_T, "1"))
                value = cell.get("{%s}value" % NS_O) or cell.get("{%s}date-value" % NS_O)
                if value is None:
                    p = cell.find("{%s}p" % NS_X)
                    value = "".join(p.itertext()) if p is not None else ""
                cells.extend([value] * (1 if repeat > 100 else repeat))
            while cells and cells[-1] == "":
                cells.pop()
            if cells:
                yield cells


def clean(s):
    # Issuer names carry non-breaking spaces; name matching fails without this.
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def pct(raw):
    """The same column mixes '0.62' and '0,58'. '<0,5' is a sentinel, not a number."""
    s = clean(raw)
    if s.startswith("<"):
        return None, s
    try:
        return float(s.replace(",", ".")), s.replace(",", ".")
    except ValueError:
        return None, s


def day(raw):
    s = clean(raw)
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else s


def positions(kind):
    rows, started = [], False
    for r in ods_rows(fetch(kind)):
        if not started:
            started = bool(r) and clean(r[0]).lower().startswith("innehavare")
            continue
        r = (list(r) + [""] * 6)[:6]
        holder, issuer, isin, p, d, note = [clean(x) for x in r]
        if not holder and not issuer:
            continue
        value, shown = pct(p)
        rows.append({"holder": holder, "issuer": issuer, "isin": isin,
                     "pct": value, "pct_shown": shown, "date": day(d), "note": note})
    return rows


def aggregated():
    rows, started = [], False
    for r in ods_rows(fetch("aggregated")):
        if not started:
            started = bool(r) and clean(r[0]).lower().startswith("namn")
            continue
        r = (list(r) + [""] * 4)[:4]
        issuer, lei, p, d = [clean(x) for x in r]
        if not issuer:
            continue
        value, shown = pct(p)
        rows.append({"issuer": issuer, "lei": lei, "pct": value,
                     "pct_shown": shown, "date": day(d)})
    return rows


# ------------------------------------------------------------------- identity

def clean_issuer(s):
    """clean() plus FI's occasional trailing '*' footnote marker, which is
    not part of any company's name. Comparison/display form only - not, by
    itself, an identity decision. See group_by_company()."""
    return re.sub(r"\*+\s*$", "", clean(s)).strip()


def cluster_by_isin(rows):
    """Union-find raw issuer strings that share a non-blank ISIN.

    Used only as a fallback for rows the aggregate file cannot vouch for (see
    group_by_company) - the holder-level files carry no LEI, so ISIN is the
    only hard identifier available at this layer. Never merges on name
    similarity alone.
    """
    strings = {}
    for r in rows:
        s = clean_issuer(r.get("issuer"))
        if not s:
            continue
        strings.setdefault(s, set()).add(r.get("isin") or "")

    parent = {s: s for s in strings}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    by_isin = {}
    for s, isins in strings.items():
        for isin in isins:
            if isin:
                union(s, by_isin.setdefault(isin, s))

    groups = defaultdict(list)
    for s in strings:
        groups[find(s)].append(s)

    out = []
    for members in groups.values():
        mset = set(members)
        crows = [r for r in rows if clean_issuer(r.get("issuer")) in mset]
        isins = set()
        for m in members:
            isins |= strings[m]
        isins.discard("")
        out.append({"names": sorted(mset), "isins": sorted(isins), "rows": crows})
    return out


def group_by_company(agg_rows, holder_rows):
    """Bundle every matched row - aggregate and holder-level - into one entry
    per real issuer.

    The aggregate file is keyed by LEI, the reliable anchor, but carries no
    ISIN. The holder-level files carry ISIN but no LEI at all. So a
    holder-level row joins a company's bucket when its cleaned issuer name
    exactly matches one of that company's aggregate names, or when it shares
    an ISIN with a row that already matched - which recovers spelling
    variants ("Volvo car AB") the aggregate file itself never mentions,
    without ever merging on name similarity alone.

    A holder-level issuer string matching neither test becomes its own
    bucket with lei=None. FI's aggregate file is a live snapshot: an issuer
    that has closed out, fallen under 0.1%, or been renamed (history holds
    "Evolution Gaming Group AB (publ)", Evolution's own former legal name,
    under a different ISIN with no current aggregate row at all) will not be
    in it. Reporting that as an unlinked bucket is honest; guessing which
    live company it belongs to is exactly the string-similarity merge this
    function exists to refuse.
    """
    buckets, order = {}, []
    for a in agg_rows:
        key = a.get("lei") or ("noname:" + clean_issuer(a["issuer"]))
        if key not in buckets:
            buckets[key] = {"lei": a.get("lei") or None, "agg": [], "names": set(),
                            "isins": set()}
            order.append(key)
        b = buckets[key]
        b["agg"].append(a)
        b["names"].add(clean_issuer(a["issuer"]))

    matched, unmatched = defaultdict(list), []
    for r in holder_rows:
        n = clean_issuer(r.get("issuer"))
        key = next((k for k, b in buckets.items() if n in b["names"]), None)
        if key:
            matched[key].append(r)
            if r.get("isin"):
                buckets[key]["isins"].add(r["isin"])
        else:
            unmatched.append(r)

    still = []
    for r in unmatched:
        isin = r.get("isin")
        key = next((k for k, b in buckets.items() if isin and isin in b["isins"]), None) \
            if isin else None
        if key:
            matched[key].append(r)
            buckets[key]["names"].add(clean_issuer(r.get("issuer")))
        else:
            still.append(r)

    for c in cluster_by_isin(still):
        key = "isin:" + (c["isins"][0] if c["isins"] else c["names"][0])
        buckets[key] = {"lei": None, "agg": [], "names": set(c["names"]),
                        "isins": set(c["isins"])}
        order.append(key)
        matched[key] = c["rows"]

    out = []
    for key in order:
        b = buckets[key]
        rows = matched.get(key, [])
        if not rows and not b["agg"]:
            continue
        display = (b["agg"][0]["issuer"] if b["agg"] else
                   Counter(clean_issuer(r["issuer"]) for r in rows).most_common(1)[0][0])
        out.append({"lei": b["lei"], "display": display, "names": sorted(b["names"]),
                    "isins": sorted(b["isins"]), "agg": b["agg"], "row_count": len(rows)})
    out.sort(key=lambda c: -(c["row_count"] + len(c["agg"])))
    return out


def belongs(row, company):
    """Does this holder-level row (current/history/merged) belong to this
    resolved company? Same two tests as group_by_company, applied to a
    single row rather than a whole file, so current/rows/history can each be
    re-scoped to one company without re-deriving identity three times."""
    return (clean_issuer(row.get("issuer")) in company["names"]
            or (row.get("isin") and row["isin"] in company["isins"]))


def gleif_legal_name(lei):
    """Best-effort legal-name lookup for the ambiguity banner, via
    company_resolve's GLEIF helper if importable. Cosmetic only - any
    failure (module absent, function renamed, network down) is swallowed."""
    if not company_resolve:
        return None
    try:
        return company_resolve.gleif(lei).get("legal_name")
    except Exception:
        return None


def print_ambiguous_banner(companies, query):
    print("AMBIGUOUS ISSUER: %r matches %d distinct companies in FI's own data"
          % (query, len(companies)))
    print("(separated by LEI/ISIN, not by spelling). Nothing below is summed across")
    print("them - each gets its own complete, separately-labelled report.")
    print()
    for c in companies:
        legal = gleif_legal_name(c["lei"]) if c["lei"] else None
        suffix = "  (%s)" % legal if legal and legal != c["display"] else ""
        print("  %s%s" % (c["display"], suffix))
        alt = [n for n in c["names"] if n != c["display"]]
        if alt:
            print("    also filed under: %s" % ", ".join(alt))
        print("    LEI: %-22s  ISIN: %-16s  named-holder rows: %d"
              % (c["lei"] or "-", ", ".join(c["isins"]) or "-", c["row_count"]))
    print()
    print("  Pass --lei or --isin to fetch exactly one of these directly next time")
    print("  and skip this check.")
    print()


# ------------------------------------------------------------------- timeline

def merged_rows(needle=None):
    """Every holder-level row FI has, current and superseded, in one list.

    FI splits the holder-level data across two files and they are DISJOINT:
    GetAktuellFile holds only each holder's live position, GetHistFile only the
    superseded ones. Verified empirically - zero rows appear in both. So the
    history file alone ends days before today (its newest Embracer row was
    2026-08-25 while the live file already had 2026-08-27), and reading only
    it would report a stale position as current. Concatenating is the only way
    to get the full record.
    """
    cur = positions("current")
    for r in cur:
        r["live"] = True          # this row is in FI's authoritative live file
    hist = positions("history")
    for r in hist:
        r["live"] = False
    rows = hist + cur
    if needle:
        rows = [r for r in rows if needle in r["issuer"].lower()]
    # Sort by date so "last row wins" reconstructs state correctly. Rows sharing
    # a date keep file order, with the live file last, which is what we want.
    rows.sort(key=lambda r: (r["date"], r["live"]))
    return rows


def state_at(rows, asof):
    """Each holder's position as at `asof`, with two rules that matter.

    1. '<0,5' is FI's sentinel for "this holder has dropped under the 0.5%
       disclosure threshold". The true value is somewhere in [0, 0.5) and FI
       does not say where, so it is carried as 0.0 rather than guessed at.
       That biases the reconstruction DOWN by up to 0.5pp per lapsed holder -
       stated in the output, not hidden.

    2. A numeric row is NOT carried forward for ever. FI's older entries often
       just stop: Scopia Capital's last Elekta row is 3.53% dated 2015-01-23
       with no closing row and no presence in the live file. Carrying that
       forward put Elekta's reconstructed named base at 17.10% against an
       actual 8.63% - double the truth, from four holders who left a decade
       ago. So a numeric position counts at `asof` only if the record
       continues past it: the holder is in FI's live file, or filed another
       row after `asof`. Anything else is a truncated record, not a position,
       and is reported as STALE rather than counted.

       This makes the reconstruction reliable for recent dates - which is what
       the 30- and 90-day windows need - and deliberately conservative for
       dates deep in the past, where FI's record simply cannot support a
       point-in-time total.
    """
    live_file = {r["holder"] for r in rows if r["live"]}
    last_before, last_ever = {}, {}
    for r in rows:
        last_ever[r["holder"]] = r
        if r["date"] <= asof:
            last_before[r["holder"]] = r

    state, stale = {}, []
    for h, r in last_before.items():
        v = r["pct"] if r["pct"] is not None else 0.0
        if v > 0 and h not in live_file and last_ever[h]["date"] <= asof:
            stale.append({"holder": h, "last_pct": v, "last_date": r["date"]})
            v = 0.0
        state[h] = v
    return state, last_before, stale


def named_total(rows, asof):
    state, latest, stale = state_at(rows, asof)
    live = {h: v for h, v in state.items() if v > 0}
    return sum(live.values()), len(live), state, latest, stale


def classify_change(before, after, seen_before=False):
    """New / re-entered / increased / reduced / closed / unchanged.

    RE-ENTERED is worth splitting out from NEW: a fund crossing 0.5% for the
    first time is a fresh thesis, one that has been in and out of this name
    for years is running a trade. FI's data supports the distinction because
    the history file goes back to 2010.
    """
    if before <= 0 and after > 0:
        return "RE-ENTERED" if seen_before else "NEW"
    if before > 0 and after <= 0:
        return "CLOSED (below 0.5%)"
    if after > before + EPS:
        return "INCREASED"
    if after < before - EPS:
        return "REDUCED"
    if before <= 0 and after <= 0:
        return "absent"
    return "UNCHANGED"


def trend(rows, ref):
    """Named-holder short interest now, 30d ago and 90d ago, plus per-holder moves.

    IMPORTANT: this is the >= 0.5% NAMED base only. FI publishes no time series
    at all for the >= 0.1% aggregate - the aggregate file is a single snapshot,
    and the issuer page carries no history for it either. So the direction here
    is the direction of the disclosed large positions, which is the best free
    proxy available but is NOT the aggregate's own history. Do not present it
    as one.
    """
    today = ref.isoformat()
    now_total, now_n, now_state, now_latest, now_stale = named_total(rows, today)
    out = {"as_of": today, "named_total_pct": now_total, "named_holders": now_n,
           "basis": "named positions >= 0.5% only; FI publishes no history "
                    "for the >= 0.1% aggregate",
           "stale_records_excluded": now_stale,
           "windows": {}}
    for w in WINDOWS:
        past = (ref - datetime.timedelta(days=w)).isoformat()
        then_total, then_n, then_state, _, _ = named_total(rows, past)
        # Anyone with a filing before the window opened has history in the name.
        seen = {r["holder"] for r in rows if r["date"] < past}
        changes = []
        for h in sorted(set(now_state) | set(then_state)):
            b, a = then_state.get(h, 0.0), now_state.get(h, 0.0)
            status = classify_change(b, a, h in seen)
            if status == "absent":
                continue
            changes.append({"holder": h, "then_pct": b, "now_pct": a,
                            "change_pp": a - b, "status": status,
                            "last_report": (now_latest.get(h) or {}).get("date", "")})
        changes.sort(key=lambda c: -abs(c["change_pp"]))
        delta = now_total - then_total
        out["windows"][str(w)] = {
            "days": w, "from": past,
            "covered": bool(rows) and rows[0]["date"] <= past,
            "then_total_pct": then_total, "then_holders": then_n,
            "change_pp": delta,
            "direction": ("RISING" if delta > EPS else
                          "FALLING" if delta < -EPS else "FLAT"),
            "new": [c["holder"] for c in changes if c["status"] == "NEW"],
            "re_entered": [c["holder"] for c in changes
                           if c["status"] == "RE-ENTERED"],
            "increased": [c["holder"] for c in changes if c["status"] == "INCREASED"],
            "reduced": [c["holder"] for c in changes if c["status"] == "REDUCED"],
            "closed": [c["holder"] for c in changes
                       if c["status"].startswith("CLOSED")],
            "changes": changes,
        }
    return out


def ranked(rows, current):
    """The live named holders, largest first, each against their own record.

    Ranking is taken from FI's live file, never from the reconstruction: the
    live file IS the definitive statement of who is disclosed at >= 0.5% right
    now. The merged history is used only to date each holder's peak and their
    first disclosure, which is where the reading is - a holder at half their
    own peak is covering, one at a fresh peak is pressing.
    """
    peak, first_seen = {}, {}
    for r in rows:
        if r["pct"] is not None:
            if r["pct"] > peak.get(r["holder"], (0.0, ""))[0]:
                peak[r["holder"]] = (r["pct"], r["date"])
            first_seen.setdefault(r["holder"], r["date"])
    out = []
    for p in current:
        if p["pct"] is None or p["pct"] <= 0:
            continue
        h = p["holder"]
        pk, pd = peak.get(h, (p["pct"], p["date"]))
        out.append({"rank": 0, "holder": h, "pct": p["pct"],
                    "as_of": p["date"], "isin": p["isin"],
                    "peak_pct": pk, "peak_date": pd,
                    "off_peak_pp": p["pct"] - pk,
                    "first_disclosed": first_seen.get(h, p["date"])})
    out.sort(key=lambda x: -x["pct"])
    for i, x in enumerate(out, 1):
        x["rank"] = i
    return out


# --------------------------------------------------------------------- output

def print_trend(tr, agg_pct, live_named_sum=None):
    print("Named-holder short interest — trend")
    print("  Positions >= 0.5% only. FI publishes NO time series for the >= 0.1%")
    print("  aggregate, so this is the disclosed large-holder base moving, not the")
    print("  total short interest moving. It is a proxy, and a good one, not the")
    print("  same number as the %s%% headline above."
          % ("%.2f" % agg_pct if agg_pct is not None else "aggregate"))
    print()
    print("  %-14s %10s %10s %10s   %s"
          % ("WINDOW", "THEN", "NOW", "CHANGE", "DIRECTION"))
    print("  " + "-" * 60)
    for w in WINDOWS:
        x = tr["windows"][str(w)]
        if not x["covered"]:
            print("  %-14s %10s   register history starts after %s"
                  % ("%dd ago" % w, "N/A", x["from"]))
            continue
        print("  %-14s %9.2f%% %9.2f%% %+9.2fpp   %s"
              % ("%dd (%s)" % (w, x["from"]), x["then_total_pct"],
                 tr["named_total_pct"], x["change_pp"], x["direction"]))
    print("  Holders named today: %d" % tr["named_holders"])
    print()

    for w in WINDOWS:
        x = tr["windows"][str(w)]
        if not x["covered"] or not x["changes"]:
            continue
        print("  Change per named holder over %d days:" % w)
        print("    %-46s %8s %8s %9s  %s"
              % ("HOLDER", "THEN", "NOW", "CHANGE", "STATUS"))
        print("    " + "-" * 88)
        for c in x["changes"]:
            print("    %-46.46s %7.2f%% %7.2f%% %+8.2fpp  %s"
                  % (c["holder"], c["then_pct"], c["now_pct"],
                     c["change_pp"], c["status"]))
        print()
    print("  'CLOSED (below 0.5%)' means the holder stopped being named, not")
    print("  necessarily that the position is gone: anything under 0.5% is")
    print("  undisclosed, so a holder shown at 0.00% may still be short 0.49%.")
    print("  The reconstructed totals are therefore a FLOOR on the named base.")

    stale = tr.get("stale_records_excluded") or []
    if stale:
        print()
        print("  %d holder record(s) excluded as stale — FI's file has a numeric"
              % len(stale))
        print("  position for them, no closing row, and no entry in the live file:")
        for s in sorted(stale, key=lambda s: -s["last_pct"])[:8]:
            print("    %-46.46s last %.2f%% on %s"
                  % (s["holder"], s["last_pct"], s["last_date"]))
        print("  Counting these would have inflated the reconstruction; they are")
        print("  dropped, so the 'THEN' column is if anything understated.")

    # A cheap self-check: the reconstruction at today must reproduce the live
    # file's own sum. If it does not, the reconstruction is wrong and the
    # reader needs to know rather than trust a silent number.
    if live_named_sum is not None:
        if abs(tr["named_total_pct"] - live_named_sum) > 0.02:
            print()
            print("  !! CONSISTENCY WARNING: the reconstruction puts today's named")
            print("  !! base at %.2f%% but FI's live file sums to %.2f%%. Trust the"
                  % (tr["named_total_pct"], live_named_sum))
            print("  !! live file; treat the trend above as indicative only.")


def print_ranked(rk):
    print("Largest short holders, ranked (FI's live >= 0.5%% file, %d holders)"
          % len(rk))
    print("  %-4s %-42s %8s %11s %8s %11s %10s"
          % ("#", "HOLDER", "NOW", "AS OF", "PEAK", "PEAK DATE", "FIRST SEEN"))
    print("  " + "-" * 100)
    for x in rk:
        print("  %-4d %-42.42s %7.2f%% %11s %7.2f%% %11s %10s"
              % (x["rank"], x["holder"], x["pct"], x["as_of"],
                 x["peak_pct"], x["peak_date"], x["first_disclosed"]))
    print("  PEAK is the largest position that holder has ever disclosed in this")
    print("  name, across FI's whole record back to 2010. A holder well below")
    print("  their own peak is covering; one at a fresh peak is pressing.")
    print("  AS OF is that holder's own last filing date, not today: a position")
    print("  dated years ago is still live until they file a change.")


def company_json(agg_c, current_c, rows_c, history_c, today):
    """The existing single-issuer JSON payload shape, computed for rows that
    have already been scoped to one resolved company. Shared by the
    unambiguous top-level payload and each entry of issuer_matches so the
    two paths cannot drift apart."""
    live_named_sum = sum(p["pct"] for p in current_c if p["pct"] is not None)
    tr = trend(rows_c, today) if rows_c else None
    rk = ranked(rows_c, current_c) if rows_c else None
    return {
        "aggregate": agg_c, "named_holders": current_c,
        "aggregate_basis": "all positions >= 0.1%",
        "named_basis": "positions >= 0.5% only",
        "named_sum_pct": live_named_sum,
        "aggregate_pct": (agg_c[0]["pct"] if agg_c else None),
        "undisclosed_gap_pp": ((agg_c[0]["pct"] - live_named_sum)
                                if agg_c and agg_c[0]["pct"] is not None else None),
        "warning": "named_sum_pct is NOT total short interest; aggregate_pct is. "
                   "The gap sits in undisclosed 0.1-0.5% positions.",
        "trend": tr, "ranked_holders": rk, "history": history_c,
    }


def print_company_report(agg_c, current_c, rows_c, history_c, args, today):
    """Everything from the aggregate line to the history table, for rows
    already pinned to ONE real issuer. Used both for the ordinary
    single-issuer run and once per company when a name matched more than
    one, so the two paths cannot drift apart."""
    live_named_sum = sum(p["pct"] for p in current_c if p["pct"] is not None)
    tr = trend(rows_c, today) if rows_c else None
    rk = ranked(rows_c, current_c) if rows_c else None

    for x in agg_c:
        print("%s" % x["issuer"])
        print("  LEI %s" % x["lei"])
        print("  Aggregate short interest  %s%%   as of %s" % (x["pct_shown"], x["date"]))
        print("  Register entry            https://www.fi.se/sv/vara-register/"
              "blankningsregistret/emittent?id=%s" % x["lei"])
    print()

    if current_c:
        current_c = sorted(current_c, key=lambda p: (p["pct"] is None, -(p["pct"] or 0)))
        named = sum(p["pct"] for p in current_c if p["pct"] is not None)
        print("Named holders at or above 0.5%%  (%d)" % len(current_c))
        print("  %-48s %8s  %-11s %s" % ("HOLDER", "PCT", "AS OF", "ISIN"))
        print("  " + "-" * 84)
        for p in current_c:
            print("  %-48.48s %8s  %-11s %s"
                  % (p["holder"], p["pct_shown"], p["date"], p["isin"]))
        print()
        if agg_c and agg_c[0]["pct"]:
            hidden = agg_c[0]["pct"] - named
            print("  Named holders sum to %.2f%% of the %.2f%% aggregate. The remaining"
                  % (named, agg_c[0]["pct"]))
            print("  %.2f%% sits in positions between 0.1%% and 0.5%%, where holders are"
                  % max(hidden, 0))
            print("  not disclosed. Quote the aggregate as total short interest.")
    else:
        print("No individual position currently at or above 0.5%.")
        print("Any aggregate figure above therefore comes entirely from undisclosed")
        print("positions between 0.1% and 0.5%.")
    print()

    if rk:
        print_ranked(rk)
        print()
    if tr:
        print_trend(tr, agg_c[0]["pct"] if agg_c else None, live_named_sum)
    elif args.no_trend:
        print("Trend: skipped (--no-trend).")
    else:
        print("Trend: DATA NOT AVAILABLE — FI's holder-level files hold no rows")
        print("for this issuer, so there is no position history to reconstruct.")

    if args.history:
        print()
        print("History — %d rows, newest first. '<0,5' means the holder dropped"
              % len(history_c))
        print("below the 0.5% disclosure threshold, i.e. the position was cut or closed.")
        print("  %-48s %8s  %s" % ("HOLDER", "PCT", "DATE"))
        print("  " + "-" * 72)
        for p in history_c[:40]:
            print("  %-48.48s %8s  %s" % (p["holder"], p["pct_shown"], p["date"]))
        if len(history_c) > 40:
            print("  ... %d more (use --json)" % (len(history_c) - 40))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("issuer", nargs="?", help="issuer name substring")
    ap.add_argument("--history", action="store_true",
                    help="include closed and past positions")
    ap.add_argument("--top", type=int, metavar="N",
                    help="the N most-shorted Swedish issuers")
    ap.add_argument("--lei", default="", help="pin to one issuer by exact LEI; "
                    "skips the name-ambiguity check entirely")
    ap.add_argument("--isin", default="", help="pin to one issuer by exact ISIN "
                    "of a holder-level position; skips the name-ambiguity check")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--no-trend", action="store_true", dest="no_trend",
                    help="skip the 30/90-day reconstruction (avoids downloading "
                         "FI's full history file)")
    args = ap.parse_args()
    args.lei = args.lei.strip()
    args.isin = args.isin.strip().upper()

    today = datetime.date.today()

    if args.top:
        agg = [x for x in aggregated() if x["pct"] is not None]
        agg.sort(key=lambda x: x["pct"], reverse=True)
        top = agg[:args.top]

        # Attach the 30-day move on the named base. One parse of the history
        # file covers every issuer, so this costs nothing extra per row.
        moves = {}
        if not args.no_trend:
            # Join on the same normalised key group_by_company() uses (see
            # its docstring), not raw .lower() - the aggregate file and the
            # named-holder history file spell an issuer's name slightly
            # differently often enough that a raw-string join silently drops
            # the match and prints "n/a" in the 30D NAMED column.
            by_issuer = {}
            for r in merged_rows():
                by_issuer.setdefault(clean_issuer(r["issuer"]).lower(), []).append(r)
            for x in top:
                rows = by_issuer.get(clean_issuer(x["issuer"]).lower(), [])
                if not rows:
                    continue
                now, _, _, _, _ = named_total(rows, today.isoformat())
                then, _, _, _, _ = named_total(
                    rows, (today - datetime.timedelta(days=30)).isoformat())
                moves[x["issuer"]] = {"named_now_pct": now, "named_30d_ago_pct": then,
                                      "change_pp": now - then}
                x["named_30d_change_pp"] = now - then

        if args.as_json:
            print(json.dumps({"source": "Finansinspektionen blankningsregister",
                              "basis": "aggregate of all positions >= 0.1%",
                              "named_basis_30d_moves": moves,
                              "retrieved_utc": datetime.datetime.now(
                                  datetime.timezone.utc).isoformat(),
                              "issuers": top}, indent=2, ensure_ascii=False))
            return
        print("Most-shorted Swedish issuers — aggregate of all positions >= 0.1%")
        print("Source: Finansinspektionen blankningsregister")
        print()
        print("%-46s %8s  %-11s %s" % ("ISSUER", "SHORT %", "AS OF", "30D NAMED"))
        print("-" * 80)
        for x in top:
            mv = x.get("named_30d_change_pp")
            print("%-46.46s %8.2f  %-11s %s"
                  % (x["issuer"], x["pct"], x["date"],
                     "%+.2fpp" % mv if mv is not None else "n/a"))
        if not args.no_trend:
            print()
            print("SHORT % is the >= 0.1% aggregate. 30D NAMED is the change over 30")
            print("days in the sum of the >= 0.5% named holders - a different, smaller")
            print("base, shown because FI publishes no history for the aggregate.")
        return

    if not (args.issuer or args.lei or args.isin):
        ap.error("give an issuer name, --lei, --isin, or --top N")

    needle = (args.issuer or "").lower()
    current = [p for p in positions("current") if needle in p["issuer"].lower()]
    agg = [x for x in aggregated() if needle in x["issuer"].lower()]
    rows = [] if args.no_trend else merged_rows(needle)
    history = ([p for p in positions("history") if needle in p["issuer"].lower()]
               if args.history else [])
    history.sort(key=lambda p: p["date"], reverse=True)

    if not current and not agg:
        print("DATA NOT AVAILABLE: no issuer matching %r in FI's blankningsregister."
              % (args.issuer or (args.lei or args.isin)))
        print("Only issuers with at least one reported position >= 0.1% appear.")
        print("A company absent from the register has no disclosed short interest,")
        print("which is itself informative — say so rather than leaving it blank.")
        if rows:
            print()
            print("It has been shorted before, though: FI's history holds %d past"
                  % len(rows))
            print("position rows for a matching name, newest %s." % rows[-1]["date"])
        if args.issuer:
            # FI indexes the full registered legal name, so a ticker or a common
            # short form misses: "SBB" finds nothing, "Samhällsbyggnadsbolaget"
            # finds a 15% short base. Offer the near misses rather than let an
            # absent-from-register conclusion rest on a naming mismatch.
            names = [x["issuer"] for x in aggregated()]
            # Cut-off deliberately tight and legal-form words dropped: loose fuzzy
            # matching on Swedish issuer names returns every "... AB" in the
            # file, which reads as a suggestion and is really just noise.
            near = difflib.get_close_matches(args.issuer, names, n=5, cutoff=0.6)
            tokens = [t for t in re.split(r"\W+", args.issuer)
                      if len(t) >= 4 and t.lower() not in
                      ("publ", "group", "holding", "aktiebolag", "aktiebolaget",
                       "company", "corp", "international")]
            for n in names:
                if any(t.lower() in n.lower() for t in tokens) and n not in near:
                    near.append(n)
            if near:
                print()
                print("Did you mean one of these? FI indexes the full registered legal")
                print("name, so a ticker or short form will not match:")
                for n in near[:8]:
                    print("  %s" % n)
        return

    # --------------------------------------------------------- identity step
    #
    # A name is not an identity (see the module docstring). Resolve whatever
    # the substring/pin matched into distinct real companies before printing
    # anything: --lei/--isin pin one directly; a bare name that resolves to
    # more than one gets every one of them reported, separately and
    # labelled, never summed into one "Volvo" total.
    holder_pool = rows if rows else current
    companies = group_by_company(agg, holder_pool)

    if args.lei or args.isin:
        matched = [c for c in companies
                   if (args.lei and c["lei"] == args.lei)
                   or (args.isin and args.isin in c["isins"])]
        if not matched:
            print("DATA NOT AVAILABLE: no company matching %s %s among the %d "
                  "issuer(s) %r matched."
                  % ("LEI" if args.lei else "ISIN", args.lei or args.isin,
                     len(companies), args.issuer or "(any)"))
            for c in companies:
                print("  %-40s LEI %-22s ISIN %s"
                      % (c["display"], c["lei"] or "-", ", ".join(c["isins"]) or "-"))
            return
        companies = matched
        ambiguous = False
    else:
        ambiguous = len(companies) > 1

    if args.as_json:
        if ambiguous:
            matches = []
            for c in companies:
                cur_c = [p for p in current if belongs(p, c)]
                rows_c = [r for r in rows if belongs(r, c)]
                hist_c = [p for p in history if belongs(p, c)]
                entry = company_json(c["agg"], cur_c, rows_c, hist_c, today)
                entry.update({"issuer": c["display"], "also_filed_as": c["names"],
                              "lei": c["lei"], "isins": c["isins"]})
                matches.append(entry)
            print(json.dumps({"query": args.issuer,
                              "source": "Finansinspektionen blankningsregister",
                              "retrieved_utc": datetime.datetime.now(
                                  datetime.timezone.utc).isoformat(),
                              "ambiguous": True,
                              "ambiguity_note": "the query matched %d distinct "
                                                 "issuers (separated by LEI/ISIN); "
                                                 "see issuer_matches, nothing is "
                                                 "summed" % len(companies),
                              "aggregate": [], "named_holders": [], "trend": None,
                              "ranked_holders": None, "history": [],
                              "issuer_matches": matches}, indent=2, ensure_ascii=False))
            return
        c = companies[0]
        cur_c = [p for p in current if belongs(p, c)]
        rows_c = [r for r in rows if belongs(r, c)]
        hist_c = [p for p in history if belongs(p, c)]
        payload = company_json(c["agg"], cur_c, rows_c, hist_c, today)
        payload.update({"query": args.issuer, "resolved_issuer": c["display"],
                        "lei": c["lei"], "isins": c["isins"], "ambiguous": False,
                        "source": "Finansinspektionen blankningsregister",
                        "retrieved_utc": datetime.datetime.now(
                            datetime.timezone.utc).isoformat()})
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if ambiguous:
        print_ambiguous_banner(companies, args.issuer)
        for i, c in enumerate(companies):
            if i:
                print()
                print("=" * 100)
                print()
            print("%s" % c["display"])
            print_company_report(c["agg"], [p for p in current if belongs(p, c)],
                                 [r for r in rows if belongs(r, c)],
                                 [p for p in history if belongs(p, c)], args, today)
    else:
        c = companies[0]
        print_company_report(c["agg"], [p for p in current if belongs(p, c)],
                             [r for r in rows if belongs(r, c)],
                             [p for p in history if belongs(p, c)], args, today)

    print()
    print("Reported T+1 by 15:30 CET. A rising short base into a results date is a")
    print("different signal from a stable one — read the trend, not just the level.")


if __name__ == "__main__":
    main()
