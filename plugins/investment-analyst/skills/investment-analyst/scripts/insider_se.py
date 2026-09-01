#!/usr/bin/env python3
"""Swedish insider (PDMR) transactions from Finansinspektionen's Insynsregistret.

This is the Swedish counterpart to SEC Form 4: every person discharging
managerial responsibilities at a Nasdaq Stockholm / Spotlight / NGM issuer must
report their trades under MAR Art. 19. Free, public, no API key.

The register is a raw event log, and read raw it misleads. A large minority of
the rows are not decisions at all: RSU allotments, option exercises, the
sell-to-cover that pays the tax on those, internal transfers, pledges, gifts
and rights-issue subscriptions. Counting a sell-to-cover as an insider dumping
stock turns a payroll event into a bear signal. So every row is classified, and
the headline net is computed from DISCRETIONARY open-market trades in the share
itself. The mechanical rows are still reported - separately, where they cannot
contaminate the signal.

Usage:
    python insider_se.py --issuer "Volvo"
    python insider_se.py --issuer "Evolution" --months 12
    python insider_se.py --from 2026-01-01 --to 2026-08-31 --issuer "Investor"
    python insider_se.py --issuer "Atlas Copco" --json
    python insider_se.py --lei 549300HGV012CNC8JD22          # AB Volvo, unambiguous
    python insider_se.py --issuer "Volvo" --isin SE0000115446

A NAME IS NOT AN IDENTITY. "Volvo" matches both AB Volvo and Volvo Car AB -
two different listed companies with different owners and accounts. FI's own
Issuer column also carries non-breaking spaces, trailing "*" marks, legal-form
variants ("AB Volvo" / "Aktiebolaget Volvo") and plain typos ("Evolution AP
(publ)" for "AB"). Every --issuer query is therefore clustered by LEI/ISIN
identity before anything is aggregated: when it resolves to one issuer, the
report reads as before; when it resolves to more than one, this script prints
each issuer separately and refuses to print a combined "Volvo" net - see
cluster_issuers() for the merge rule. Pass --lei or --isin to pin the issuer
directly and skip the ambiguity check altogether.

Source: https://marknadssok.fi.se/publiceringsklient  (Finansinspektionen)
Coverage starts 2016-07-03, when MAR (EU) 596/2014 entered into force.
"""
import argparse
import csv
import datetime
import io
import json
import os
import re
import statistics
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

# company_resolve.py holds the toolkit's canonical identity logic (brand-name
# ambiguity, orgnr/LEI/ISIN cross-checks). It is owned and evolved by another
# script in this toolkit, so it is imported defensively: if it is missing, an
# older revision, or raises on import for any reason, this script still runs
# standalone on the LEI/ISIN clustering implemented below. Never let a broken
# or absent optional import take the whole tool down.
try:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "company_resolve",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "company_resolve.py"))
    company_resolve = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(company_resolve)
except Exception:
    company_resolve = None

BASE = "https://marknadssok.fi.se/publiceringsklient/en-GB/Search/Search"
UA = "Mozilla/5.0 (compatible; investment-analyst-skill/1.0)"

BUY_WORDS = ("acquisition", "subscription", "förvärv", "teckning")
SELL_WORDS = ("disposal", "sale", "avyttring", "försäljning")

# Rolling windows, in days. 30 catches the reaction to the last report, 90 a
# quarter of behaviour, 365 the base rate the other two are read against.
WINDOWS = (30, 90, 365)

# Instrument types that are economically the share itself. A call option bought
# by a CFO is a real view but a different risk, and folding its premium into a
# "net bought SEK x" line makes the two incomparable, so derivatives aggregate
# apart. BTA/BTU are interim shares from an issue - still the equity.
SHARE_LIKE = ("share", "aktie", "depositary receipt", "depåbevis", "bta", "btu",
              "sdb", "interim share")

# FI data carries Swedish characters; a cp1252 console would mangle them.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def to_iso(value):
    """FI prints dd/mm/yyyy. Return YYYY-MM-DD so dates sort chronologically."""
    head = (value or "").strip()[:10]
    if len(head) == 10 and head[2] == "/" and head[5] == "/":
        return "%s-%s-%s" % (head[6:], head[3:5], head[:2])
    return head


def norm(s):
    """FI separates the words in Position with U+00A0, not a space.

    The exported CEO label is literally "Chief<nbsp>Executive<nbsp>Officer",
    which does not contain the substring "chief executive officer", so every
    naive role match silently returns nothing. Collapse all whitespace,
    including the non-breaking kind, before matching anything.
    """
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def iso(d):
    try:
        return datetime.date.fromisoformat(d)
    except (ValueError, TypeError):
        return datetime.date(1900, 1, 1)


def money(x):
    return "{:,.0f}".format(x)


def signed(x):
    return "{:+,.0f}".format(x)


# FI's Volume/Price columns are usually decimal-comma with no grouping
# ("16,6"), but not always: a comma-grouped thousands separator ("1,000")
# also appears, and so does a space-grouped one ("1 234,5"). The old
# `.replace(",", ".")` read "1,000" as 1.0 - a silent thousandfold error -
# and raised on "1 234,5", which was then swallowed into a silent 0.0.
# mfn_news.py's to_number() draws the same distinction for MFN release text;
# this mirrors it for FI's simpler format (no thousands-separator-AND-
# decimal-point case, since FI never mixes the two conventions in one field).
_THOUSANDS_COMMA = re.compile(r"^-?\d{1,3}(?:,\d{3})+$")
_NUMBER_SPACES = (" ", " ", " ", " ")


def parse_fi_number(raw):
    """Parse one FI Volume/Price field. Returns (value, ok).

    ok is False when nothing could be parsed, so the caller can count and
    report the failure rather than silently substituting 0.0.
    """
    if raw is None:
        return 0.0, True
    cleaned = raw.strip()
    if not cleaned:
        return 0.0, True
    for sp in _NUMBER_SPACES:
        cleaned = cleaned.replace(sp, "")
    if "," in cleaned:
        # A comma followed by groups of exactly three digits is a thousands
        # separator; one followed by one or two digits is a decimal comma.
        if _THOUSANDS_COMMA.match(cleaned):
            cleaned = cleaned.replace(",", "")
        else:
            cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned), True
    except ValueError:
        return 0.0, False


# ---------------------------------------------------------------- classifying

def classify(nature, option_linked, instrument_type):
    """Return (category, direction, signal).

    category  - what actually happened, in FI's own vocabulary
    direction - +1 holding up, -1 holding down, 0 neutral
    signal    - DISCRETIONARY (a decision to trade at the market price),
                MECHANICAL (compensation, transfer, admin - no view expressed),
                DERIVATIVE (a real decision, but not in the share itself)

    Only DISCRETIONARY rows feed the headline net. This is the single most
    important judgement in the script: FI's register does not separate a CEO
    spending his own cash from a CEO having shares handed to him, and reading
    them as the same number is how insider data gets misused.
    """
    n = norm(nature).lower()
    opt = norm(option_linked).lower() in ("yes", "ja", "y", "true", "1")
    itype = norm(instrument_type).lower()
    is_share = any(w in itype for w in SHARE_LIKE) or itype == ""

    # Order matters. The specific vocabulary is tested before the generic
    # acquisition/disposal fallback, because "Internal transaction - Disposal"
    # contains "disposal" but is a move between the insider's own accounts.
    if "internal transaction" in n or "intern transaktion" in n:
        d = (1 if any(w in n for w in BUY_WORDS)
             else -1 if any(w in n for w in SELL_WORDS) else 0)
        return "transfer (internal)", d, "MECHANICAL"
    if "pledg" in n or "pant" in n:
        # Collateral. Economic exposure unchanged - though a pledged holding is
        # a margin-call risk worth noticing, it is not a buy or a sell.
        return "pledge / collateral", 0, "MECHANICAL"
    if "loan" in n or "lån" in n:
        return "securities lending", 0, "MECHANICAL"
    if "gift" in n or "gåva" in n or "inherit" in n or "arv" in n or "donation" in n:
        d = -1 if ("given" in n or "granted" in n or "donated" in n) else 1
        return "gift / inheritance", d, "MECHANICAL"
    if "exchange" in n or "byte" in n or "issue of instrument" in n or "emission" in n:
        return "corporate action", 0, "MECHANICAL"
    if "allotment" in n or "tilldelning" in n:
        # RSU / performance-share vesting. Shares arrive; no cash leaves.
        return "compensation award", 1, "MECHANICAL"
    if "exercise" in n or "utnyttjande" in n or "lösen" in n:
        d = -1 if ("decrease" in n or "minsk" in n) else 1
        return "option/warrant exercise", d, "MECHANICAL"
    if "subscription" in n or "teckning" in n:
        # Rights issue or warrant subscription. Cash does leave the insider's
        # pocket, which is mildly bullish, but the price comes from the terms
        # of the issue, not from the insider's view of the market price.
        return "subscription / rights issue", 1, "MECHANICAL"
    if any(w in n for w in SELL_WORDS):
        if opt:
            # Sell-to-cover: the tax withholding on a vesting. Not a view.
            return "option-programme sale (tax/cover)", -1, "MECHANICAL"
        return "open-market sale", -1, ("DISCRETIONARY" if is_share else "DERIVATIVE")
    if any(w in n for w in BUY_WORDS):
        if opt:
            return "option-programme acquisition", 1, "MECHANICAL"
        return "open-market purchase", 1, ("DISCRETIONARY" if is_share else "DERIVATIVE")
    return "other / unclassified", 0, "MECHANICAL"


def role_of(position):
    """Bucket FI's Position text into CEO / CFO / BOARD / OTHER.

    Tested most-specific first: "Deputy CEO/Deputy Managing Director" contains
    "managing director" and would otherwise be counted as the chief executive.
    """
    p = norm(position).lower()
    if not p:
        return "OTHER"
    if "deputy" in p or "vice vd" in p or "vvd" in p or "vice verkställande" in p:
        return "OTHER"
    if ("chief executive" in p or "(ceo)" in p or re.search(r"\bceo\b", p)
            or "managing director" in p or "managing directory" in p
            or re.search(r"\bvd\b", p) or "verkställande direktör" in p):
        return "CEO"
    if ("chief financial" in p or "(cfo)" in p or re.search(r"\bcfo\b", p)
            or "finansdirektör" in p or "ekonomichef" in p):
        return "CFO"
    if "board of directors" in p or "styrelse" in p:
        return "BOARD"
    return "OTHER"


# ------------------------------------------------------------------- identity

def clean_issuer(s):
    """FI's Issuer column, collapsed to a stable display/comparison form.

    Whitespace (including U+00A0) is collapsed by norm(); on top of that FI
    appends a bare "*" to some issuer names as a footnote marker that is not
    part of any company's name ("Evolution AB (publ) *"), so that is stripped
    too. This alone is NOT identity resolution - "AB Volvo" and "Volvo Car AB"
    both survive this unchanged and are still two different companies. See
    cluster_issuers().
    """
    return re.sub(r"\*+\s*$", "", norm(s)).strip()


def cluster_issuers(rows):
    """Group parsed transaction rows into distinct real-world issuers.

    FI's free-text Issuer column is not a safe grouping key by itself: besides
    whitespace and "*" noise (clean_issuer), it carries legal-form variants
    ("AB Volvo" / "Aktiebolaget Volvo" / "AB Volvo (publ)") and outright typos
    ("Evolution AP (publ)" for "AB") - confirmed against a live FI export.

    The LEI-code column looks like a stronger key but is NOT reliable
    row-by-row: a transaction filed by a legal entity closely associated with
    a PDMR carries THAT entity's own LEI, not the issuer's. Verified against a
    real Evolution AB filing: rows for a closely-associated notifier carry LEI
    636700MHDIO8IZME8154, which GLEIF resolves to "Camiga AB", not Evolution.
    Trusting that LEI at face value would manufacture a second issuer out of
    one company's own filing - the exact bug this function exists to prevent,
    just moved one column over. So the "issuer LEI" used for merging is the
    MODE (most common) LEI seen under a given raw issuer string, never any
    single row's LEI - one closely-associated filing cannot outvote the
    issuer's own.

    ISIN is the reliable anchor: it names one specific security, which belongs
    to exactly one issuer. Two raw issuer strings are merged into the same
    cluster only when they share a non-blank ISIN or share the same modal
    LEI - NEVER on name similarity alone, per the audit finding this function
    was written to close. A raw string with no ISIN and no LEI anywhere in its
    rows cannot be corroborated, so it is left as its own singleton cluster
    rather than fuzzy-merged into a guess.
    """
    strings = {}
    for r in rows:
        s = clean_issuer(r.get("issuer"))
        if not s:
            continue
        e = strings.setdefault(s, {"isins": set(), "leis": []})
        if r.get("isin"):
            e["isins"].add(r["isin"])
        if r.get("lei"):
            e["leis"].append(r["lei"])
    modal_lei = {s: Counter(e["leis"]).most_common(1)[0][0]
                 for s, e in strings.items() if e["leis"]}

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

    by_isin, by_lei = {}, {}
    for s, e in strings.items():
        for isin in e["isins"]:
            union(s, by_isin.setdefault(isin, s))
        lei = modal_lei.get(s)
        if lei:
            union(s, by_lei.setdefault(lei, s))

    groups = defaultdict(list)
    for s in strings:
        groups[find(s)].append(s)

    clusters = []
    for members in groups.values():
        member_set = set(members)
        crows = [r for r in rows if clean_issuer(r.get("issuer")) in member_set]
        counts = Counter(clean_issuer(r.get("issuer")) for r in crows)
        isins = set()
        for m in members:
            isins |= strings[m]["isins"]
        leis = sorted({modal_lei[m] for m in members if m in modal_lei})
        clusters.append({
            "display": counts.most_common(1)[0][0] if counts else members[0],
            "names": sorted(member_set),
            "isins": sorted(isins),
            "leis": leis,
            "rows": crows,
        })
    clusters.sort(key=lambda c: -len(c["rows"]))
    return clusters


def cluster_matches(cluster, lei=None, isin=None):
    """Does a cluster satisfy an explicit --lei/--isin pin?

    Checked against the cluster's modal LEI/known ISINs AND against every raw
    row's own fields, so a query for the issuer's own LEI still pulls in rows
    that were filed under a closely-associated entity's LEI instead (the
    Camiga case above) rather than silently dropping them.
    """
    if lei:
        if lei in cluster["leis"] or any(r.get("lei") == lei for r in cluster["rows"]):
            pass
        else:
            return False
    if isin:
        if isin in cluster["isins"] or any(r.get("isin") == isin for r in cluster["rows"]):
            pass
        else:
            return False
    return bool(lei or isin)


def gleif_legal_name(lei):
    """Best-effort legal-name lookup for the ambiguity banner, via
    company_resolve's GLEIF helper if that module is importable. Any failure
    (module absent, function renamed, network down) is swallowed - this is a
    cosmetic enrichment, never a dependency of the identity resolution above."""
    if not company_resolve:
        return None
    try:
        return company_resolve.gleif(lei).get("legal_name")
    except Exception:
        return None


# -------------------------------------------------------------------- fetching

def fetch_csv(date_from, date_to, issuer=""):
    params = {
        "SearchFunctionType": "Insyn",
        "Utgivare": issuer,
        "PersonILedandeStallningNamn": "",
        "Transaktionsdatum.From": date_from,
        "Transaktionsdatum.To": date_to,
        "button": "export",
        "Page": "1",
    }
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()

    # FI serves UTF-16 with a BOM; fall back to UTF-16-LE, then cp1252.
    for enc in ("utf-16", "utf-16-le", "utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            if text.count(";") > 5:
                return text
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise SystemExit("DATA NOT AVAILABLE: could not decode FI response.")


def parse(text):
    """Return (rows, parse_stats). parse_stats counts Volume/Price fields
    that could not be parsed at all - those are reported to the caller, never
    silently folded into a 0.0 that looks like a real zero-volume row."""
    rows = list(csv.DictReader(io.StringIO(text), delimiter=";"))
    out = []
    unparsed_volume = 0
    unparsed_price = 0
    for r in rows:
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items() if k}
        if not r.get("Issuer"):
            continue
        vol, vol_ok = parse_fi_number(r.get("Volume"))
        if not vol_ok:
            unparsed_volume += 1
        price, price_ok = parse_fi_number(r.get("Price"))
        if not price_ok:
            unparsed_price += 1
        nature = (r.get("Nature of transaction") or "").lower()
        side = ("BUY" if any(w in nature for w in BUY_WORDS)
                else "SELL" if any(w in nature for w in SELL_WORDS) else "OTHER")
        # FI misspells the header as "Intrument type"; accept both spellings so
        # a silent fix on their side does not silently break the classifier.
        itype = r.get("Intrument type") or r.get("Instrument type")
        opt = r.get("Linked to share option programme")
        category, direction, signal = classify(r.get("Nature of transaction"), opt, itype)
        out.append({
            "issuer": r.get("Issuer"),
            "pdmr": r.get("Person discharging managerial responsibilities"),
            "position": r.get("Position"),
            "role": role_of(r.get("Position")),
            "closely_associated": r.get("Closely associated"),
            "nature": r.get("Nature of transaction"),
            "side": side,
            "category": category,
            "direction": direction,
            "signal": signal,
            "instrument": r.get("Instrument name"),
            "instrument_type": itype,
            "isin": r.get("ISIN"),
            "lei": r.get("LEI-code"),
            "date": to_iso(r.get("Transaction date")),
            "published": to_iso(r.get("Publication date")),
            "volume": vol,
            "unit": r.get("Unit"),
            "price": price,
            "currency": r.get("Currency"),
            "value": vol * price,
            "option_programme": opt,
            "amendment": r.get("Amendment"),
            "status": r.get("Status"),
        })
    return out, {"unparsed_volume": unparsed_volume, "unparsed_price": unparsed_price}


# -------------------------------------------------------------------- analysis

def main_currency(rows):
    """FI reports each row in its own trading currency, and a dual-listed
    issuer mixes SEK with EUR or DKK. Adding them would invent a number, so
    pick the currency carrying the most traded value and exclude the rest."""
    by = defaultdict(float)
    for r in rows:
        by[(r.get("currency") or "").strip() or "?"] += abs(r["value"])
    if not by:
        return "?", []
    main = max(by.items(), key=lambda kv: kv[1])[0]
    others = sorted(c for c in by if c != main)
    return main, others


def counts_shares(r):
    """Volume is a share count only when Unit says so. A bond row carries a
    nominal amount in the same column; adding that to a share tally is
    nonsense, so those rows contribute value but not shares."""
    return norm(r.get("unit")).lower() in ("quantity", "antal", "")


def in_ccy(r, ccy):
    """Zero-value rows (a gift, a pledge) carry no currency; keep them so the
    row counts stay honest, but they add nothing to the money totals anyway."""
    return (r.get("currency") or "").strip() == ccy or not r["value"]


def aggregate(rows, ccy):
    """buys / sells / net, in both share count and value, for one row set."""
    a = {"buy_value": 0.0, "sell_value": 0.0, "buy_shares": 0.0, "sell_shares": 0.0,
         "buy_rows": 0, "sell_rows": 0, "_who": set()}
    for r in rows:
        if not in_ccy(r, ccy):
            continue
        a["_who"].add(norm(r.get("pdmr")) or "?")
        sh = r["volume"] if counts_shares(r) else 0.0
        if r["direction"] > 0:
            a["buy_value"] += r["value"]
            a["buy_shares"] += sh
            a["buy_rows"] += 1
        elif r["direction"] < 0:
            a["sell_value"] += r["value"]
            a["sell_shares"] += sh
            a["sell_rows"] += 1
    a["net_value"] = a["buy_value"] - a["sell_value"]
    a["net_shares"] = a["buy_shares"] - a["sell_shares"]
    a["insider_count"] = len(a["_who"])
    a["insiders"] = sorted(a.pop("_who"))
    return a


def direction_word(net, gross):
    """A net that rounds to nothing against the period's gross flow is FLAT,
    not a weak signal. Two insiders crossing 100m each is not conviction."""
    if gross <= 0 or abs(net) < 0.02 * gross:
        return "FLAT"
    return "NET BUYING" if net > 0 else "NET SELLING"


def trend_word(cur, prev, prior_covered):
    """Is the net flow accelerating or decelerating against the prior window?"""
    if not prior_covered:
        return "DATA NOT AVAILABLE (prior window outside the query range)"
    if cur == 0 and prev == 0:
        return "no discretionary flow in either window"
    if cur == 0:
        return "STOPPED (" + ("buying" if prev > 0 else "selling") + " in prior window)"
    if prev == 0:
        # Nothing to accelerate from. Saying "accelerating" off a zero base
        # reads as a trend when it is really the first flow in twice the window.
        return "STARTED " + ("buying" if cur > 0 else "selling") + " (prior window empty)"
    if cur > 0 and prev < 0:
        return "TURNED TO BUYING"
    if cur < 0 and prev > 0:
        return "TURNED TO SELLING"
    if abs(cur) > abs(prev) * 1.15:
        return "ACCELERATING " + ("buying" if cur > 0 else "selling")
    if abs(cur) < abs(prev) * 0.85:
        return "DECELERATING " + ("buying" if cur > 0 else "selling"
                                  if cur < 0 else "flow")
    return "STEADY " + ("buying" if cur > 0 else "selling" if cur < 0 else "flat")


def windows(rows, ref, earliest, ccy):
    """Rolling 30/90/365-day nets, each against the window before it.

    The preceding window is what makes the number readable: SEK 40m of net
    buying in the last 30 days means one thing after a quiet quarter and
    another after SEK 300m. A window the query range does not cover is
    reported as DATA NOT AVAILABLE, never as a small number.
    """
    out = {}
    for w in WINDOWS:
        lo = ref - datetime.timedelta(days=w)
        lo2 = ref - datetime.timedelta(days=2 * w)
        cur = [r for r in rows if lo < iso(r["date"]) <= ref]
        prev = [r for r in rows if lo2 < iso(r["date"]) <= lo]
        a = aggregate(cur, ccy)
        b = aggregate(prev, ccy)
        out[w] = {
            "days": w,
            "from": lo.isoformat(), "to": ref.isoformat(),
            "covered": earliest <= lo,
            "prior_covered": earliest <= lo2,
            "net_value": a["net_value"], "net_shares": a["net_shares"],
            "buy_value": a["buy_value"], "sell_value": a["sell_value"],
            "buy_shares": a["buy_shares"], "sell_shares": a["sell_shares"],
            "insider_count": a["insider_count"],
            "direction": direction_word(a["net_value"],
                                        a["buy_value"] + a["sell_value"]),
            "prior_net_value": b["net_value"],
            "trend": trend_word(a["net_value"], b["net_value"], earliest <= lo2),
        }
    return out


def build_analysis(rows, ref, earliest, disp_from):
    """Everything computed once, so --json and the printer cannot disagree."""
    ccy, other_ccy = main_currency(rows)
    disc_all = [r for r in rows if r["signal"] == "DISCRETIONARY"]
    shown = [r for r in rows if r["date"] >= disp_from]
    shown_disc = [r for r in shown if r["signal"] == "DISCRETIONARY"]

    # Categories are computed over the whole analysis window, because the point
    # of the table is the shape of the issuer's reporting, not this quarter's.
    by_cat = {}
    for r in rows:
        # Keyed on category AND signal: the same category splits by instrument.
        # An "open-market purchase" of the share is DISCRETIONARY, the identical
        # wording on a call option is DERIVATIVE, and merging them would hide
        # the derivative flow inside the headline signal line.
        key = "%s [%s]" % (r["category"], r["signal"].lower())
        c = by_cat.setdefault(key, {
            "rows": 0, "net_shares": 0.0, "net_value": 0.0,
            "category": r["category"], "signal": r["signal"], "_who": set()})
        c["rows"] += 1
        c["_who"].add(norm(r.get("pdmr")) or "?")
        if in_ccy(r, ccy):
            # direction 0 categories (pledge, corporate action) have no sign to
            # carry; their value is shown positive as a magnitude.
            sign = r["direction"] or 1
            c["net_value"] += r["value"] * sign
            if counts_shares(r):
                c["net_shares"] += r["volume"] * sign
    for c in by_cat.values():
        c["insider_count"] = len(c.pop("_who"))

    by_role = {}
    for role in ("CEO", "CFO", "BOARD", "OTHER"):
        sub = [r for r in shown_disc if r["role"] == role]
        if sub:
            by_role[role] = aggregate(sub, ccy)

    tickets = [r["value"] for r in shown_disc if r["value"] > 0 and in_ccy(r, ccy)]

    return {
        "currency": ccy,
        "other_currencies_excluded": other_ccy,
        "row_counts": {
            "analysis_window": len(rows),
            "display_range": len(shown),
            "discretionary": len(shown_disc),
            "derivative": len([r for r in shown if r["signal"] == "DERIVATIVE"]),
            "mechanical": len([r for r in shown if r["signal"] == "MECHANICAL"]),
        },
        "discretionary": aggregate(shown_disc, ccy),
        "derivative": aggregate([r for r in shown if r["signal"] == "DERIVATIVE"], ccy),
        "mechanical": aggregate([r for r in shown if r["signal"] == "MECHANICAL"], ccy),
        "all_rows": aggregate(shown, ccy),
        "distinct_insiders_all_rows": len(
            {norm(r.get("pdmr")) or "?" for r in shown}),
        "by_category": by_cat,
        "by_role": by_role,
        "median_ticket_value": statistics.median(tickets) if tickets else None,
        "windows": {str(k): v for k, v in windows(disc_all, ref, iso(earliest), ccy).items()},
        "analysis_window_from": earliest,
        "reference_date": ref.isoformat(),
    }


# --------------------------------------------------------------------- output

def print_analysis(an):
    ccy = an["currency"]
    d, m, dv = an["discretionary"], an["mechanical"], an["derivative"]

    print("SIGNAL — discretionary open-market trades in the share only")
    print("  Rows where an insider chose to buy or sell at the market price.")
    print("    Buys     %16s %-4s %16s shares  (%d rows)"
          % (money(d["buy_value"]), ccy, money(d["buy_shares"]), d["buy_rows"]))
    print("    Sells    %16s %-4s %16s shares  (%d rows)"
          % (money(d["sell_value"]), ccy, money(d["sell_shares"]), d["sell_rows"]))
    print("    NET      %16s %-4s %16s shares"
          % (signed(d["net_value"]), ccy, signed(d["net_shares"])))
    print("    Distinct insiders, discretionary rows: %d   (all rows: %d)"
          % (d["insider_count"], an["distinct_insiders_all_rows"]))
    if an["median_ticket_value"] is not None:
        print("    Median discretionary ticket: %s %s"
              % (money(an["median_ticket_value"]), ccy))
    if an["other_currencies_excluded"]:
        print("    NOTE: rows priced in %s are excluded from the value totals."
              % ", ".join(an["other_currencies_excluded"]))
        print("          FI reports each row in its trading currency and this")
        print("          script will not add currencies at an invented FX rate.")
    print()

    print("NON-SIGNAL flow, deliberately kept out of the net above")
    print("    Compensation / mechanical  %16s %-4s (%d rows, %d insiders)"
          % (signed(m["net_value"]), ccy, m["buy_rows"] + m["sell_rows"],
             m["insider_count"]))
    print("    Derivatives, not the share %16s %-4s (%d rows)"
          % (signed(dv["net_value"]), ccy, dv["buy_rows"] + dv["sell_rows"]))
    print()

    print("Transaction categories over the whole analysis window:")
    print("  %-36s %-13s %5s %16s %16s"
          % ("CATEGORY", "SIGNAL", "ROWS", "NET SHARES", "NET VALUE"))
    print("  " + "-" * 91)
    for c in sorted(an["by_category"].values(), key=lambda v: -abs(v["net_value"])):
        print("  %-36.36s %-13s %5d %16s %16s"
              % (c["category"], c["signal"], c["rows"],
                 signed(c["net_shares"]), signed(c["net_value"])))
    print()

    if an["by_role"]:
        print("Role split of the discretionary flow:")
        print("  %-8s %5s %16s %16s %16s"
              % ("ROLE", "PPL", "BUY VALUE", "SELL VALUE", "NET VALUE"))
        print("  " + "-" * 66)
        for role in ("CEO", "CFO", "BOARD", "OTHER"):
            a = an["by_role"].get(role)
            if not a:
                continue
            print("  %-8s %5d %16s %16s %16s"
                  % (role, a["insider_count"], money(a["buy_value"]),
                     money(a["sell_value"]), signed(a["net_value"])))
        print("  BOARD covers chair, ordinary and employee-representative directors.")
        print("  OTHER covers deputy CEOs and other senior executives; FI's Position")
        print("  field has a fixed vocabulary but no separate slot for, say, a COO,")
        print("  so anything it does not name lands in OTHER.")
    else:
        print("Role split: DATA NOT AVAILABLE — no discretionary rows to split.")
    print()

    print("Rolling windows on discretionary flow, to %s (%s):"
          % (an["reference_date"], ccy))
    print("  %-8s %18s %18s %-12s %s"
          % ("WINDOW", "NET VALUE", "PRIOR WINDOW", "DIRECTION", "TREND"))
    print("  " + "-" * 96)
    for w in WINDOWS:
        x = an["windows"][str(w)]
        if not x["covered"]:
            print("  %-8s %18s  window starts %s, before the data at %s"
                  % ("%dd" % w, "NOT AVAILABLE", x["from"],
                     an["analysis_window_from"]))
            continue
        prior = signed(x["prior_net_value"]) if x["prior_covered"] else "n/a"
        print("  %-8s %18s %18s %-12s %s"
              % ("%dd" % w, signed(x["net_value"]), prior,
                 x["direction"], x["trend"]))
    print("  'Prior window' is the equally long period immediately before, so the")
    print("  30d line answers 'is this speeding up or fading', not just 'how much'.")


def print_ambiguous_banner(clusters, query):
    """The point-2 requirement made visible: identity is explicit, not incidental.

    Refusing outright would throw away real, correct data the user already
    paid a network round-trip for. Silently summing is the Volvo/Volvo Car
    bug this whole file exists to fix. So neither: each distinct issuer is
    named here, up front, and then gets its own complete report below -
    nothing is aggregated across the boundary this banner draws.
    """
    print("AMBIGUOUS ISSUER: %r matches %d distinct companies in FI's own data"
          % (query, len(clusters)))
    print("(separated by LEI/ISIN, not by spelling). Nothing below is summed across")
    print("them - each gets its own complete, separately-labelled report.")
    print()
    for c in clusters:
        legal = gleif_legal_name(c["leis"][0]) if c["leis"] else None
        suffix = "  (%s)" % legal if legal and legal != c["display"] else ""
        print("  %s%s" % (c["display"], suffix))
        alt = [n for n in c["names"] if n != c["display"]]
        if alt:
            print("    also filed under: %s" % ", ".join(alt))
        print("    ISIN: %-16s LEI: %-22s rows: %d"
              % (", ".join(c["isins"]) or "-", ", ".join(c["leis"]) or "-",
                 len(c["rows"])))
    print()
    print("  Pass --lei or --isin to fetch exactly one of these directly next time")
    print("  and skip this check.")
    print()


def report_one(label, rows, args, d_from, d_to, analysis_from, dropped, truncated):
    """Everything from the transaction listing to the epilogue, for rows that
    have already been pinned to ONE real issuer. Used both for the ordinary
    single-issuer run and once per company when --issuer matched more than
    one, so the two code paths cannot drift apart."""
    displayed = [r for r in rows if r["date"] >= d_from]
    analysis = build_analysis(rows, iso(d_to), analysis_from, d_from) if rows else None

    if not displayed:
        print("DATA NOT AVAILABLE: no PDMR transactions for %s between %s and %s."
              % (label, d_from, d_to))
        if rows:
            print("The issuer does report — %d row(s) exist between %s and %s —"
                  % (len(rows), analysis_from, d_from))
            print("so the silence is specific to the window you asked for.")
        return

    print("PDMR transactions  |  %s  |  %s to %s  |  %d rows"
          % (label, d_from, d_to, len(displayed)))
    if dropped:
        print("Excluded %d cancelled or superseded row(s)." % dropped)
    if analysis_from != d_from:
        print("Analysis window widened to %s (%d rows) so the rolling windows have"
              % (analysis_from, len(rows)))
        print("something to compare against; the listing below is still %s onward."
              % d_from)
    if truncated:
        print("!! FI capped this export at 1000 rows - the result is PARTIAL.")
        print("!! Narrow the date range or filter by issuer, then re-run.")
    print()

    net = defaultdict(lambda: {"BUY": 0.0, "SELL": 0.0, "OTHER": 0.0})
    for r in displayed:
        net[r["issuer"]][r["side"]] += r["value"]

    print("%-11s %-22s %-19s %-5s %-30s %11s %9s"
          % ("DATE", "ISSUER", "PDMR", "SIDE", "CATEGORY", "VOLUME", "VALUE"))
    print("-" * 114)
    for r in displayed[:args.limit]:
        print("%-11s %-22.22s %-19.19s %-5s %-30.30s %11s %9s"
              % (r["date"], r["issuer"], r["pdmr"] or "-", r["side"], r["category"],
                 "{:,.0f}".format(r["volume"]),
                 "{:,.0f}".format(r["value"] / 1000) + "k"))
    if len(displayed) > args.limit:
        print("... %d more (use --limit or --json)" % (len(displayed) - args.limit))

    print()
    print("Net by issuer — ALL rows, compensation included (transaction value):")
    for issuer, agg in sorted(net.items(), key=lambda kv: -(kv[1]["BUY"] - kv[1]["SELL"])):
        n = agg["BUY"] - agg["SELL"]
        print("  %-34.34s buy %14s   sell %14s   net %+15s"
              % (issuer, "{:,.0f}".format(agg["BUY"]), "{:,.0f}".format(agg["SELL"]),
                 "{:+,.0f}".format(n)))
    if len(net) > 1:
        print("  These are spelling/legal-form variants of the SAME resolved issuer")
        print("  (shared LEI/ISIN) - not different companies.")
    print()

    print_analysis(analysis)
    return displayed, analysis


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--issuer", default="", help="issuer name, partial match (FI-side filter)")
    ap.add_argument("--lei", default="", help="pin to one issuer by exact LEI-code; "
                    "skips the name-ambiguity check entirely")
    ap.add_argument("--isin", default="", help="pin to one issuer by exact ISIN; "
                    "skips the name-ambiguity check entirely")
    ap.add_argument("--months", type=int, default=6)
    ap.add_argument("--from", dest="date_from", help="YYYY-MM-DD (overrides --months)")
    ap.add_argument("--to", dest="date_to", help="YYYY-MM-DD")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--no-widen", action="store_true", dest="no_widen",
                    help="do not extend the fetch to 730 days for the rolling "
                         "windows; analyse exactly the requested range")
    args = ap.parse_args()
    args.lei = args.lei.strip().upper()
    args.isin = args.isin.strip().upper()
    single_issuer_query = bool(args.issuer or args.lei or args.isin)

    today = datetime.date.today()
    d_to = args.date_to or today.isoformat()
    d_from = args.date_from or (today - datetime.timedelta(days=30 * args.months)).isoformat()

    # The 365-day window and the prior year it is compared against need two
    # years of history, which the default six-month range cannot supply. When
    # an issuer filter is set the export stays small, so ask FI for the two
    # years in the SAME single request and still list only the range requested.
    # Without an issuer filter the whole market would blow through FI's
    # 1000-row export cap, so leave that case alone.
    analysis_from = d_from
    if single_issuer_query and not args.no_widen:
        wide = (iso(d_to) - datetime.timedelta(days=730)).isoformat()
        analysis_from = min(d_from, wide)

    # FI expects dd/mm/yyyy in the query string.
    def sv(d):
        y, m, dd = d.split("-")
        return "%s/%s/%s" % (dd, m, y)

    text = fetch_csv(sv(analysis_from), sv(d_to), args.issuer)
    rows, parse_stats = parse(text)
    truncated = len(rows) >= 1000

    # FI's issuer filter is loose; tighten it client-side.
    if args.issuer:
        needle = args.issuer.lower()
        rows = [r for r in rows if needle in (r["issuer"] or "").lower()]

    # FI publishes Cancelled rows and both halves of an amendment chain. Counting
    # them inflates the aggregate and turns cancelled trades into conviction.
    live = [r for r in rows
            if (r.get("status") or "").strip().lower() in ("current", "aktuell", "")]
    dropped = len(rows) - len(live)
    rows = live
    rows.sort(key=lambda r: r["date"], reverse=True)

    # --------------------------------------------------------- identity step
    #
    # A name is not an identity (see the module docstring). Cluster whatever
    # the query returned by LEI/ISIN, then decide what to do with more than
    # one cluster: --lei/--isin pin a single one directly; a bare --issuer
    # name that resolves to more than one real company gets EVERY one of them
    # reported, separately and labelled, never summed into one "Volvo" net.
    clusters = cluster_issuers(rows) if rows else []

    if args.lei or args.isin:
        matched = [c for c in clusters if cluster_matches(c, args.lei, args.isin)]
        if not matched:
            wanted = args.lei or args.isin
            print("DATA NOT AVAILABLE: no rows matching %s %s"
                  % ("LEI" if args.lei else "ISIN", wanted))
            if clusters:
                print("in the %r result set. Issuers actually present there:" % args.issuer
                      if args.issuer else "in the fetched result set. Issuers present there:")
                for c in clusters:
                    print("  %-30s ISIN %-16s LEI %s"
                          % (c["display"], ", ".join(c["isins"]) or "-",
                             ", ".join(c["leis"]) or "-"))
            return
        rows = sorted((r for c in matched for r in c["rows"]),
                      key=lambda r: r["date"], reverse=True)
        clusters = cluster_issuers(rows)

    ambiguous = bool(args.issuer) and not args.lei and not args.isin and len(clusters) > 1

    if args.as_json:
        if ambiguous:
            matches = []
            for c in clusters:
                displayed = [r for r in c["rows"] if r["date"] >= d_from]
                analysis = (build_analysis(c["rows"], iso(d_to), analysis_from, d_from)
                            if c["rows"] else None)
                matches.append({
                    "issuer": c["display"], "also_filed_as": c["names"],
                    "isins": c["isins"], "leis": c["leis"],
                    "row_count": len(c["rows"]),
                    "analysis": analysis, "transactions": displayed})
            print(json.dumps({"issuer_query": args.issuer, "from": d_from, "to": d_to,
                              "analysis_from": analysis_from,
                              "ambiguous": True,
                              "ambiguity_note": "the query matched %d distinct issuers "
                                                 "(separated by LEI/ISIN); see "
                                                 "issuer_matches, nothing is summed"
                                                 % len(clusters),
                              "export_truncated_at_1000": truncated,
                              "unparsed_volume_rows": parse_stats["unparsed_volume"],
                              "unparsed_price_rows": parse_stats["unparsed_price"],
                              "source": "Finansinspektionen Insynsregistret",
                              "retrieved_utc": datetime.datetime.now(
                                  datetime.timezone.utc).isoformat(),
                              "analysis": None, "transactions": [],
                              "issuer_matches": matches}, indent=2, ensure_ascii=False))
            return
        displayed = [r for r in rows if r["date"] >= d_from]
        analysis = (build_analysis(rows, iso(d_to), analysis_from, d_from)
                    if rows else None)
        print(json.dumps({"issuer_query": args.issuer, "lei_query": args.lei,
                          "isin_query": args.isin, "from": d_from, "to": d_to,
                          "analysis_from": analysis_from,
                          "ambiguous": False,
                          "count": len(displayed),
                          "analysis_row_count": len(rows),
                          "excluded_cancelled_or_revised": dropped,
                          "export_truncated_at_1000": truncated,
                          "unparsed_volume_rows": parse_stats["unparsed_volume"],
                          "unparsed_price_rows": parse_stats["unparsed_price"],
                          "source": "Finansinspektionen Insynsregistret",
                          "basis": "MAR Art. 19 PDMR notifications; only Status="
                                   "Current rows; net computed from discretionary "
                                   "open-market share trades only",
                          "retrieved_utc": datetime.datetime.now(
                              datetime.timezone.utc).isoformat(),
                          "analysis": analysis,
                          "transactions": displayed}, indent=2, ensure_ascii=False))
        return

    print("Source: Finansinspektionen Insynsregistret (marknadssok.fi.se), retrieved %s"
          % datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    if parse_stats["unparsed_volume"] or parse_stats["unparsed_price"]:
        print("!! %d row(s) had a Volume field, and %d row(s) a Price field, that "
              "could not be parsed" % (parse_stats["unparsed_volume"],
                                       parse_stats["unparsed_price"]))
        print("!! at all (unrecognised number format) - those rows count towards "
              "volume/value as 0, which")
        print("!! understates the true total rather than being invented.")
    print()

    if ambiguous:
        if dropped:
            print("Excluded %d cancelled or superseded row(s) across all matched "
                  "issuers." % dropped)
            print()
        print_ambiguous_banner(clusters, args.issuer)
        for i, c in enumerate(clusters):
            if i:
                print()
                print("=" * 114)
                print()
            report_one(c["display"], c["rows"], args, d_from, d_to, analysis_from,
                       dropped=0, truncated=truncated)
    else:
        if single_issuer_query and clusters:
            label = clusters[0]["display"]
        elif single_issuer_query:
            label = args.issuer or args.lei or args.isin
        else:
            label = "ALL ISSUERS"
        report_one(label, rows, args, d_from, d_to, analysis_from, dropped, truncated)

    print()
    print("HOW TO READ THIS")
    print("  Open-market purchases are the only rows that cost the insider money at")
    print("  a price they chose. Allotments, exercises and the sales that fund the")
    print("  tax on them are compensation mechanics and carry almost no signal;")
    print("  FI's register does not separate them, so this script does.")
    print("  Selling is weaker evidence than buying in any case: an insider sells")
    print("  for a house, a divorce or a tax bill, but buys for one reason. A")
    print("  cluster of unrelated insiders buying at once is the thing worth acting")
    print("  on, not any single ticket.")
    print("  Values are as-reported transaction values, not marked to today.")
    print("  A whole-market query (no --issuer/--lei/--isin) still mixes every")
    print("  company into one net line - always scope to an issuer for a real signal.")


if __name__ == "__main__":
    main()
