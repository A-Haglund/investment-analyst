#!/usr/bin/env python3
"""Portfolio-level concentration, overlap, exposure, downside and confidence.

Implements `references/portfolio.md`: concentration (Herfindahl / effective
number of positions), hidden overlap across sector and shared driver, currency
exposure, the bear-case value ratio and drawdown, a value-weighted Portfolio
Data Confidence, and two ISK-specific observations (cash drag, foreign
withholding) that this script only FLAGS -- it never computes a tax bill.

Weights come from CURRENT MARKET VALUE, never from cost basis (see
`references/portfolio.md` "Position sizing" and "Concentration risk"). Cost
basis and unrealised P&L do not appear anywhere in this script's output --
that lives in the position-sizing part of the main skill, not here.

DATA SOURCES
  portfolio_store.py  the holdings, quantities, cost data and cash (imported
                      by file path, like every sibling script imports its
                      neighbours -- see `_load()` below). Degrades with a
                      clear message if that module does not exist yet.
  quote.py            current price per symbol (`fetch_price`)
  nordic_shares.py    ICB industry code per issuer, via Nasdaq Nordic's own
                      reference data, for the "hidden overlap" section
  venues_se.py        Spotlight's own sector label, for MTF names that
                      `nordic_shares.py` (Nasdaq-listed only) cannot resolve
  macro_se.py         SEK FX crosses (Riksbanken, falling back to ECB) for any
                      holding that trades in a non-SEK currency
  finfact.py          FinancialFact / confidence_score -- the same provenance
                      and confidence machinery every other script in this
                      skill uses, reused here rather than reinvented

NOTE-FIELD TAGS. The fixed portfolio_store.py contract carries no dedicated
field for a per-holding bear-case value, an analyst's sector override or a
shared-driver label -- `note` is the only freeform field it exposes. This
script reads three optional tags out of that field, none of them required:

    bear=<number>       bear-case value per share, in the holding's quote
                         currency. ASSUMPTION -- an analyst input, not fetched.
    sector=<label>       overrides the auto-detected ICB / Spotlight sector
    driver=<label>       a shared-driver tag ("mining capex", "data centre
                         buildout") for the hidden-overlap grouping. OPINION,
                         per `references/portfolio.md` "Correlation and hidden
                         overlap" -- a mechanism, not a fetched fact.

Example: note="bear=210; driver=mining capex; core position since 2019"

ISK CASH DRAG AND THE SCHABLON RATE. The schablon rate (statslaanteraentan +
1pp, floor 1.25%) and the tax-free allowance both change between years, and
`references/source-registry.md` names hardcoding either "A remembered or
assumed rate" -- never done, never once, anywhere in this codebase. Pass
--schablon-rate / --schablon-allowance to size the drag; without them the
report states the observation qualitatively and marks the rate ASSUMPTION
NOT SUPPLIED with the source to check (Skatteverket / Riksgaelden). Even when
supplied, this prints one illustrative line, not a tax calculation -- no
quarterly kapitalunderlag averaging, no return filing logic.

Usage:
    python portfolio_metrics.py --name default
    python portfolio_metrics.py --json
    python portfolio_metrics.py --schablon-rate 3.62 --schablon-allowance 300000
    python portfolio_metrics.py --selftest

Free, no API key beyond what the sibling scripts already need. Python 3
standard library only.
"""
import argparse
import datetime
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def _load(name):
    path = os.path.join(HERE, name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load %s from %s" % (name, path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


finfact = _load("finfact")
FinancialFact = finfact.FinancialFact
Verification = finfact.Verification
confidence_score = finfact.confidence_score

mfn_news = _load("mfn_news")
to_number = mfn_news.to_number

_cache = {}


def _lazy(name):
    if name not in _cache:
        _cache[name] = _load(name)
    return _cache[name]


def portfolio_store_mod():
    """Raises ImportError with a clear message if the sibling script that
    owns this module has not landed yet -- it is being built in parallel."""
    return _lazy("portfolio_store")


def quote_mod():
    return _lazy("quote")


def nordic_shares_mod():
    return _lazy("nordic_shares")


def venues_se_mod():
    return _lazy("venues_se")


def macro_se_mod():
    return _lazy("macro_se")


# ==========================================================================
# note-field tag parsing (bear=, sector=, driver=)
# ==========================================================================

_TAG_RE = {
    "bear": re.compile(r"(?i)\bbear\s*[:=]\s*"
                       r"([-−]?[0-9][0-9.,    ]*[0-9]|[-−]?[0-9])"),
    "sector": re.compile(r"(?i)\bsector\s*[:=]\s*([^;,\n]+)"),
    "driver": re.compile(r"(?i)\bdriver\s*[:=]\s*([^;,\n]+)"),
}


def parse_tags(note):
    """Pull the optional bear=/sector=/driver= tags out of a holding's note.

    Never raises: a note with none of these tags returns an empty dict, and
    a malformed number after `bear=` is ignored rather than crashing a whole
    portfolio run over one typo.

    The number after `bear=` is routed through `mfn_news.to_number` -- the
    same Swedish/English number parser every other script in this skill
    uses -- rather than a second, bespoke parser. A naive `.replace(",", "")`
    silently turns a Swedish decimal comma into a thousands separator
    (`bear=190,5` -> 1905.0 instead of 190.5), which is exactly backwards for
    a downside figure: it makes the bear value look higher than the price and
    sends the drawdown negative.
    """
    text = note or ""
    out = {}
    m = _TAG_RE["bear"].search(text)
    if m:
        n = to_number(m.group(1))
        if n is not None:
            out["bear"] = n
    m = _TAG_RE["sector"].search(text)
    if m:
        out["sector"] = m.group(1).strip()
    m = _TAG_RE["driver"].search(text)
    if m:
        out["driver"] = m.group(1).strip()
    return out


# ==========================================================================
# Price. One seam (`fetch_price`) so tests can monkeypatch every price fetch
# without touching quote.py itself.
# ==========================================================================

def fetch_price(symbol):
    """Current price for one symbol, or None. Never raises.

    Tries Yahoo first, then Nasdaq's API (US symbols only) -- the same order
    `quote.py`'s own report() uses. Returns a plain dict, not a FinancialFact:
    callers that need provenance build the fact themselves (see
    `price_fact()`), because a bare price is also useful on its own for the
    market-value arithmetic.
    """
    q = quote_mod()
    y = n = None
    try:
        y = q.from_yahoo(symbol)
    except (Exception, SystemExit):  # siblings raise SystemExit, not an Exception
        y = None
    try:
        n = q.from_nasdaq(symbol.replace("-", ".")) if "." not in symbol else None
    except (Exception, SystemExit):  # siblings raise SystemExit, not an Exception
        n = None

    primary = y or n
    if not primary or primary.get("price") is None:
        return None

    if y and y.get("price") is not None:
        return {"price": y["price"], "currency": y.get("currency"),
                "source_key": "yahoo", "source_label": y.get("source"),
                "as_of": (y.get("as_of_utc") or "")[:10] or None}
    return {"price": n["price"], "currency": n.get("currency") or "USD",
            "source_key": "nasdaq_cns", "source_label": n.get("source"),
            "as_of": None}


def price_fact(price_info):
    """One FinancialFact for a fetched price, or None if there is nothing to
    build one from. SINGLE SOURCE always: this script does not attempt the
    two-source cross-check `quote.py`'s own CLI report performs."""
    if not price_info or price_info.get("price") is None:
        return None
    as_of = price_info.get("as_of") or datetime.date.today().isoformat()
    try:
        return FinancialFact(
            "price", price_info["price"], price_info["source_key"],
            period_end=as_of, unit="currency", currency=price_info.get("currency"),
            publication_date=as_of, freshness_key="price",
            verification=Verification.SINGLE_SOURCE,
            source_detail=price_info.get("source_label"))
    except ValueError:
        return None


# ==========================================================================
# FX. One seam (`fetch_fx`) so tests can supply rates offline.
# ==========================================================================

def fetch_fx(currencies):
    """{ccy: {"sek_per_unit","obs_date","source","status"}} for every
    non-SEK currency in `currencies`. Never raises -- an unreachable
    Riksbanken/ECB degrades to a per-currency DATA NOT AVAILABLE row, handled
    by `sek_per()` below, not a crash of the whole report."""
    wanted = sorted({c for c in currencies if c and c != "SEK"})
    if not wanted:
        return {}
    macro = macro_se_mod()
    try:
        extra = any(c not in ("EUR", "USD") for c in wanted)
        fx = macro.sek_fx(extra_fx=extra)
    except (Exception, SystemExit):  # siblings raise SystemExit, not an Exception
        return {}
    return fx


def sek_per(ccy, fx_table):
    """(rate, detail) converting one unit of `ccy` into SEK, or (None, None)
    if the rate could not be sourced. SEK itself is always rate 1.0."""
    if ccy in (None, "SEK"):
        return 1.0, {"source": "SEK - no conversion needed", "obs_date": None,
                     "status": "OK"}
    row = (fx_table or {}).get(ccy)
    if not row or row.get("sek_per_unit") is None:
        return None, row
    return row["sek_per_unit"], row


def fx_fact(ccy, fx_row):
    if not fx_row or fx_row.get("sek_per_unit") is None:
        return None
    source = fx_row.get("source") or ""
    source_key = "riksbank" if "Riksbanken" in source else "ecb"
    obs = fx_row.get("obs_date")
    if not obs:
        return None
    try:
        return FinancialFact(
            "fx_%s" % ccy, fx_row["sek_per_unit"], source_key, period_end=obs,
            unit="rate", publication_date=obs, freshness_key="macro_rate",
            verification=Verification.SINGLE_SOURCE, source_detail=source)
    except ValueError:
        return None


# ==========================================================================
# Sector. One seam (`fetch_sector`); ICB code from Nasdaq Nordic reference
# data first (tier 1 -- exactly what `nordic_shares.py` already reads),
# Spotlight's own classification second (MTF names Nasdaq does not list).
# ==========================================================================

ICB_INDUSTRIES = {
    10: "Technology", 15: "Telecommunications", 20: "Health Care",
    30: "Financials", 35: "Real Estate", 40: "Consumer Discretionary",
    45: "Consumer Staples", 50: "Industrials", 55: "Basic Materials",
    60: "Energy", 65: "Utilities",
}


def icb_industry(code):
    try:
        prefix = int(str(code).strip()[:2])
    except (ValueError, TypeError, AttributeError):
        return None
    return ICB_INDUSTRIES.get(prefix)


def fetch_sector(name, symbol):
    """(label, source_key) or (None, None). Never raises -- `nordic_shares`'s
    own `api()` raises SystemExit on an unreachable endpoint, which this
    catches rather than letting kill the whole portfolio run."""
    try:
        ns = nordic_shares_mod()
        hits = ns.search(name) or []
    except (SystemExit, Exception):
        hits = []
    for h in hits:
        try:
            summ = ns.summary(h["orderbookId"])
        except (SystemExit, Exception):
            continue
        label = icb_industry((summ or {}).get("icb"))
        if label:
            return label, "nasdaq_reference"

    try:
        vs = venues_se_mod()
        spot = vs.spotlight_companies() or {}
        row = spot.get(vs.normalise(name))
        if row and row.get("sector"):
            return row["sector"], "press"
    except (SystemExit, Exception):
        pass
    return None, None


# ==========================================================================
# Pure math -- concentration, downside. Deliberately free of any I/O so the
# arithmetic can be hand-checked and unit tested without a network or a
# portfolio_store fixture.
# ==========================================================================

def herfindahl(weights):
    """Sigma w_i^2. portfolio.md: 0.10 = ~10 equal positions; >0.25 concentrated."""
    return sum(w * w for w in weights)


def effective_n(hhi):
    """1 / HHI, or None for an empty/zero book."""
    return (1.0 / hhi) if hhi else None


def bear_case(entries, cash_weight):
    """portfolio.md's downside formulas, extended to a partly-covered book.

    entries: iterable of {"label","weight","bear_value","price"}. A holding
    with no `bear=` tag on file (bear_value is None) is EXCLUDED from the
    ratio rather than assumed either safe or a total loss -- `coverage_weight`
    reports how much of the account the figure actually describes, so a
    partial figure is never mistaken for the whole portfolio.

    ratio     = Sigma weight_i * bear_value_i / price_i, cash included at a
                trivial ratio of 1.0 (cash does not fall in a bear case)
    drawdown  = 1 - ratio                                (a different number,
                asserted separately in the tests)
    """
    ratio = cash_weight * 1.0
    coverage = cash_weight
    contributions = []
    holdings_covered = 0
    for e in entries:
        bear_value, price, w = e.get("bear_value"), e.get("price"), e["weight"]
        if bear_value is None or not price:
            continue
        r = bear_value / price
        contrib = w * r
        ratio += contrib
        coverage += w
        contributions.append((e["label"], w - contrib))
        holdings_covered += 1
    contributions.sort(key=lambda t: -t[1])

    if holdings_covered == 0:
        # Zero bear-value coverage is the NORMAL state of this feature --
        # nothing in the reference docs or command files tells an analyst
        # `bear=` exists, and `--paste` on portfolio_store.py wipes the note
        # field on every use. Left as-was, `ratio` here is just
        # `cash_weight * 1.0` (no holding contributed anything) and
        # `drawdown = 1 - cash_weight`: on a typical ~95%-invested, entirely
        # untagged book that renders as a fabricated "95% bear-case drawdown"
        # headline number, backed by nothing. A ratio computed over cash
        # alone is not a portfolio downside figure, so none is reported.
        return {"ratio": None, "drawdown": None, "coverage_weight": coverage,
                "status": "DATA NOT AVAILABLE",
                "reason": "no holding carries a bear-case value (bear_value "
                          "or a bear= note tag) -- a ratio computed over "
                          "cash alone is not a portfolio downside figure",
                "top_contributors": [], "impairment_flags": []}

    return {"ratio": ratio, "drawdown": 1 - ratio, "coverage_weight": coverage,
            "status": "OK",
            "top_contributors": contributions[:3],
            "impairment_flags": [c for c in contributions if c[1] > 0.10]}


CASH_CONFIDENCE = 60
# Cash is a single-sourced figure from the portfolio record, never
# independently cross-checked against a custodian statement -- the same
# posture `finfact.confidence_score` gives one SINGLE-SOURCE, tier-2-ish
# fact (100 - (1-0)*30 tier penalty avoided since there's no market/vendor
# risk, but -25 unverified, -15 for carrying no corroborating source at all).
# Documented here, not asserted by tone: a constant, not a computation.


def holding_confidence(facts, sector_known, bear_known):
    """0-100, reusing `finfact.confidence_score` over this holding's own
    facts (price, fx), then penalising the two things that score does not
    see: whether the hidden-overlap grouping could place this holding at
    all, and whether a bear case exists for the downside section."""
    if not facts:
        return 0.0
    score, _detail = confidence_score(facts)
    if not sector_known:
        score = max(0, score - 10)
    if not bear_known:
        score = max(0, score - 15)
    return float(score)


def portfolio_confidence(weighted_scores):
    """Value-weighted average of per-holding (weight, confidence) pairs.
    Confidence-carrying weight need not sum to 1 (an unpriced holding
    contributes no weight and no confidence, rather than being guessed at)."""
    total_w = sum(w for w, _c in weighted_scores)
    if not total_w:
        return None
    return sum(w * c for w, c in weighted_scores) / total_w


# ==========================================================================
# Assembly -- turns a portfolio_store dict into everything the report needs.
# ==========================================================================

def build(portfolio, as_of=None, foreign_threshold_pct=20.0):
    """The whole pipeline: enrich each holding, then compute every metric.

    Returns one dict, shaped for both the text renderer and --json. Never
    raises on a single bad holding -- a missing price or FX rate degrades
    that ONE line to DATA NOT AVAILABLE and is excluded from the value-
    weighted totals, with the exclusion stated, never silent.
    """
    as_of = as_of or datetime.date.today().isoformat()
    ccy = portfolio.get("currency") or "SEK"
    cash = portfolio.get("cash") or {}
    cash_amount = cash.get("amount") or 0.0
    cash_ccy = cash.get("currency") or ccy

    raw_holdings = portfolio.get("holdings") or []
    currencies_needed = set()
    prelim = []
    for h in raw_holdings:
        price_info = fetch_price(h.get("symbol"))
        tags = parse_tags(h.get("note"))
        if price_info and price_info.get("currency"):
            currencies_needed.add(price_info["currency"])
        prelim.append((h, price_info, tags))
    if cash_ccy:
        currencies_needed.add(cash_ccy)
    currencies_needed.add(ccy)

    fx_table = fetch_fx(currencies_needed)
    cash_rate, cash_fx_row = sek_per(cash_ccy, fx_table)
    cash_value_sek = cash_amount * cash_rate if cash_rate is not None else None

    holdings = []
    unresolved = []
    for h, price_info, tags in prelim:
        label = h.get("symbol") or h.get("name") or h.get("isin") or "?"
        sector_label = tags.get("sector")
        sector_source = "note tag (analyst override)" if sector_label else None
        if not sector_label:
            sector_label, sector_source_key = fetch_sector(h.get("name") or label,
                                                            h.get("symbol"))
            sector_source = sector_source_key

        driver_label = tags.get("driver")
        # bear_value: the structured field wins when present; the `bear=`
        # note tag is a fallback only, for a holding whose store record
        # predates the field or was hand-edited without it.
        bear_value = h.get("bear_value") if h.get("bear_value") is not None \
            else tags.get("bear")

        if not price_info:
            unresolved.append({
                "label": label, "name": h.get("name"), "isin": h.get("isin"),
                "quantity": h.get("quantity"),
                "status": "DATA NOT AVAILABLE",
                "reason": "no price source returned a quote for %r" % (h.get("symbol"),),
                "sector": sector_label, "driver": driver_label,
            })
            continue

        quote_ccy = price_info.get("currency") or ccy
        rate, fx_row = sek_per(quote_ccy, fx_table)
        if rate is None:
            unresolved.append({
                "label": label, "name": h.get("name"), "isin": h.get("isin"),
                "quantity": h.get("quantity"), "status": "DATA NOT AVAILABLE",
                "reason": "priced in %s but no FX rate to %s was available"
                          % (quote_ccy, ccy),
                "sector": sector_label, "driver": driver_label,
            })
            continue

        quantity = h.get("quantity")
        if quantity is None:
            unresolved.append({
                "label": label, "name": h.get("name"), "isin": h.get("isin"),
                "quantity": quantity, "status": "DATA NOT AVAILABLE",
                "reason": "quantity is missing/None for %r -- cannot compute "
                          "a market value" % (h.get("symbol") or label,),
                "sector": sector_label, "driver": driver_label,
            })
            continue

        market_value_sek = quantity * price_info["price"] * rate

        facts = [f for f in (price_fact(price_info), fx_fact(quote_ccy, fx_row)) if f]
        conf = holding_confidence(facts, bool(sector_label), bear_value is not None)

        holdings.append({
            "label": label, "name": h.get("name"), "isin": h.get("isin"),
            "symbol": h.get("symbol"), "quantity": h.get("quantity"),
            "price": price_info["price"], "quote_currency": quote_ccy,
            "price_source": price_info.get("source_label"),
            "fx_rate_to_sek": rate,
            "market_value": market_value_sek,
            "sector": sector_label, "sector_source": sector_source,
            "driver": driver_label, "bear_value": bear_value,
            "confidence": conf,
        })

    priced_total = sum(hd["market_value"] for hd in holdings)
    total_value = priced_total + (cash_value_sek or 0.0)

    if total_value <= 0:
        weights_note = "no priced value in the account - nothing to weight"
        for hd in holdings:
            hd["weight"] = None
        cash_weight = None
    else:
        for hd in holdings:
            hd["weight"] = hd["market_value"] / total_value
        cash_weight = (cash_value_sek or 0.0) / total_value
        weights_note = None

    result = {
        "as_of": as_of, "portfolio_name": portfolio.get("name"),
        "account_type": portfolio.get("account_type"), "currency": ccy,
        "cash": {"amount": cash_amount, "currency": cash_ccy,
                 "value_sek": cash_value_sek, "weight": cash_weight,
                 "fx": cash_fx_row},
        "holdings": holdings, "unresolved": unresolved,
        "priced_total": priced_total, "total_value": total_value,
        "weights_note": weights_note,
        "coverage": {"priced": len(holdings), "unresolved": len(unresolved),
                     "total_holdings": len(raw_holdings)},
    }

    if cash_weight is None:
        result["concentration"] = None
        result["overlap"] = None
        result["currency_exposure"] = None
        result["downside"] = None
        result["data_confidence"] = None
    else:
        weights = [hd["weight"] for hd in holdings] + [cash_weight]
        hhi = herfindahl(weights)
        sorted_w = sorted((hd["weight"] for hd in holdings), reverse=True)
        result["concentration"] = {
            "hhi": hhi, "effective_n": effective_n(hhi),
            "top1": sorted_w[0] if sorted_w else None,
            "top3": sum(sorted_w[:3]) if sorted_w else None,
            "top5": sum(sorted_w[:5]) if sorted_w else None,
            "cash_included_as_a_position": True,
        }

        result["overlap"] = _overlap(holdings)
        result["currency_exposure"] = _currency_exposure(holdings, cash_ccy, cash_weight)

        entries = [{"label": hd["label"], "weight": hd["weight"],
                    "bear_value": hd["bear_value"], "price": hd["price"]}
                   for hd in holdings]
        result["downside"] = bear_case(entries, cash_weight)

        weighted_scores = [(hd["weight"], hd["confidence"]) for hd in holdings]
        weighted_scores.append((cash_weight, float(CASH_CONFIDENCE)))
        result["data_confidence"] = portfolio_confidence(weighted_scores)

    result["foreign_flag"] = _foreign_flag(holdings, cash_ccy, cash_weight, ccy,
                                           foreign_threshold_pct)
    return result


def _overlap(holdings):
    sectors, drivers = {}, {}
    for hd in holdings:
        if hd["sector"]:
            sectors.setdefault(hd["sector"], []).append(hd)
        if hd["driver"]:
            drivers.setdefault(hd["driver"], []).append(hd)
        elif hd["sector"]:
            # No explicit shared-driver tag: fall back to the sector group so
            # the grouping is never silently empty for a holding that has at
            # least a sector.
            drivers.setdefault("(by sector) " + hd["sector"], []).append(hd)

    def group_summary(groups):
        out = []
        for label, members in groups.items():
            out.append({"label": label,
                        "weight": sum(m["weight"] for m in members),
                        "members": [m["label"] for m in members]})
        out.sort(key=lambda g: -g["weight"])
        return out

    sector_groups = group_summary(sectors)
    driver_groups = group_summary(drivers)
    largest_single = max((h["weight"] for h in holdings), default=None)
    # Take the max across BOTH groupings, not driver-first: a driver group
    # exists the moment any one holding carries a driver= tag (the sector
    # fallback above guarantees drivers is never empty once any sector is
    # known), so picking driver_groups[0] unconditionally silently discarded
    # a larger, real sector concentration the instant one holding was tagged
    # more precisely -- tagging a portfolio more accurately made it look
    # safer, which is backwards for a concentration headline.
    largest_true_exposure = max(driver_groups + sector_groups,
                                key=lambda g: g["weight"], default=None)
    return {"sector_groups": sector_groups, "driver_groups": driver_groups,
            "largest_single_holding_weight": largest_single,
            "largest_true_exposure": largest_true_exposure}


def _currency_exposure(holdings, cash_ccy, cash_weight):
    by_ccy = {}
    for hd in holdings:
        by_ccy[hd["quote_currency"]] = by_ccy.get(hd["quote_currency"], 0.0) + hd["weight"]
    by_ccy[cash_ccy] = by_ccy.get(cash_ccy, 0.0) + cash_weight
    rows = sorted(({"currency": c, "weight": w} for c, w in by_ccy.items()),
                  key=lambda r: -r["weight"])
    return rows


def _foreign_flag(holdings, cash_ccy, cash_weight, portfolio_ccy, threshold_pct):
    """A FLAG, not a computation: in an ISK, foreign dividend withholding is
    creditable only up to the schablon amount, so a portfolio heavy in
    non-Swedish payers can lose part of that credit. portfolio.md gives no
    numeric threshold for this; 20% is this script's own judgement call, not
    a sourced figure -- see the report's ambiguity note."""
    if cash_weight is None:
        return {"status": "DATA NOT AVAILABLE",
                "reason": "no priced value in the account"}
    foreign_weight = sum(hd["weight"] for hd in holdings
                         if hd["quote_currency"] != portfolio_ccy)
    # Cash held in a non-portfolio currency is foreign exposure too (it sits
    # unhedged in that currency exactly like a holding priced in it) -- a
    # holdings-only sum understated the flag for any account carrying, say,
    # USD cash.
    if cash_ccy != portfolio_ccy:
        foreign_weight += cash_weight
    material = foreign_weight * 100 >= threshold_pct
    return {"foreign_weight": foreign_weight, "threshold_pct": threshold_pct,
            "material": material}


# ==========================================================================
# Rendering
# ==========================================================================

WIDTH = 88


def _bar(weight, max_weight, cells=20):
    if not max_weight:
        return " " * cells
    n = int(round((weight / max_weight) * cells)) if weight else 0
    n = max(0, min(cells, n))
    return "#" * n + "-" * (cells - n)


def _pct(x):
    return "DATA N/A" if x is None else "%5.1f%%" % (x * 100)


def _members(members, budget):
    """Join member labels for display, truncating with an explicit count
    (never a silent drop) so a group line never breaks the 88-char width."""
    joined = ", ".join(members)
    if len(joined) <= budget:
        return joined
    kept = []
    for m in members:
        candidate = ", ".join(kept + [m])
        if len(candidate) + len(" ... (+N more)") > budget:
            break
        kept.append(m)
    remaining = len(members) - len(kept)
    return "%s ... (+%d more)" % (", ".join(kept), remaining) if kept else \
        "%d holdings" % len(members)


def render_text(r):
    out = []
    out.append("PORTFOLIO METRICS -- %s (%s, %s) -- as of %s" % (
        r["portfolio_name"] or "?", r["account_type"] or "?", r["currency"], r["as_of"]))
    out.append("=" * WIDTH)

    cov = r["coverage"]
    out.append("Coverage: %d/%d holdings priced" % (cov["priced"], cov["total_holdings"]))
    if r["weights_note"]:
        out.append("  %s" % r["weights_note"])
    else:
        out.append("  known account value %.2f %s (cash %.2f, holdings %.2f)"
                    % (r["total_value"], r["currency"], r["cash"]["value_sek"] or 0.0,
                       r["priced_total"]))
    if r["unresolved"]:
        out.append("  DATA NOT AVAILABLE, excluded from every weight below:")
        for u in r["unresolved"]:
            out.append("    - %-14s %s" % (u["label"], u["reason"]))
    out.append("")

    if r["holdings"]:
        out.append("HOLDINGS  (weights are of known account value, market-value based)")
        out.append("-" * WIDTH)
        out.append("%-10s %7s  %-20s %-16s %5s" % ("SYMBOL", "WEIGHT", "BAR", "SECTOR", "CONF"))
        max_w = max((hd["weight"] for hd in r["holdings"]), default=0.0)
        for hd in sorted(r["holdings"], key=lambda h: -(h["weight"] or 0)):
            out.append("%-10s %7s  %s %-16s %5.0f" % (
                hd["label"][:10], _pct(hd["weight"]), _bar(hd["weight"], max_w),
                (hd["sector"] or "DATA N/A")[:16], hd["confidence"]))
        cw = r["cash"]["weight"]
        out.append("%-10s %7s  %s %-16s %5.0f" % (
            "Cash", _pct(cw), _bar(cw, max_w), r["cash"]["currency"], CASH_CONFIDENCE))
        out.append("")

    conc = r["concentration"]
    if conc:
        out.append("CONCENTRATION  (portfolio.md: HHI 0.10 ~ 10 equal positions; >0.25 concentrated)")
        out.append("-" * WIDTH)
        out.append("  Top 1  %s   Top 3  %s   Top 5  %s" % (
            _pct(conc["top1"]), _pct(conc["top3"]), _pct(conc["top5"])))
        out.append("  HHI (cash included as a position)  %.4f   Effective N  %.2f"
                    % (conc["hhi"], conc["effective_n"]))
        out.append("")

    ov = r["overlap"]
    if ov:
        out.append("HIDDEN OVERLAP")
        out.append("-" * WIDTH)
        out.append("  Largest single holding   %s" % _pct(ov["largest_single_holding_weight"]))
        if ov["largest_true_exposure"]:
            lg = ov["largest_true_exposure"]
            label = lg["label"][:24]
            prefix = "  Largest TRUE exposure    %s  %-24s (" % (_pct(lg["weight"]), label)
            out.append(prefix + _members(lg["members"], WIDTH - len(prefix) - 1) + ")")
        else:
            out.append("  Largest TRUE exposure    DATA NOT AVAILABLE (no sector/driver resolved)")
        if ov["sector_groups"]:
            out.append("  By sector:")
            for g in ov["sector_groups"]:
                prefix = "    %-24s %s  (" % (g["label"][:24], _pct(g["weight"]))
                out.append(prefix + _members(g["members"], WIDTH - len(prefix) - 1) + ")")
        if ov["driver_groups"]:
            out.append("  By shared driver:")
            for g in ov["driver_groups"]:
                prefix = "    %-24s %s  (" % (g["label"][:24], _pct(g["weight"]))
                out.append(prefix + _members(g["members"], WIDTH - len(prefix) - 1) + ")")
        out.append("")

    cur = r["currency_exposure"]
    if cur:
        out.append("CURRENCY EXPOSURE  (quote currency, not underlying revenue currency --")
        out.append("  portfolio.md: a Nasdaq Stockholm industrial may earn most revenue in USD)")
        out.append("-" * WIDTH)
        for row in cur:
            out.append("  %-6s %s" % (row["currency"], _pct(row["weight"])))
        out.append("")

    dd = r["downside"]
    if dd:
        out.append("DOWNSIDE  (Sigma weight_i * bear_i/price_i; cash counted at ratio 1.0)")
        out.append("-" * WIDTH)
        if dd.get("status") == "DATA NOT AVAILABLE":
            out.append("  Bear-case value ratio   DATA NOT AVAILABLE")
            out.append("  Bear-case drawdown      DATA NOT AVAILABLE")
            out.append("  %s" % dd["reason"])
            out.append("  No holding on file carries a bear_value or a bear= note tag; add")
            out.append("  one to at least one holding to get a figure here.")
        else:
            out.append("  Bear-case value ratio   %.4f" % dd["ratio"])
            out.append("  Bear-case drawdown      %.4f  (= 1 - ratio, a different number)"
                        % dd["drawdown"])
            out.append("  Coverage                %s of account weight has a bear case on file"
                        % _pct(dd["coverage_weight"]).strip())
            if dd["coverage_weight"] < 0.999:
                out.append("  NOTE: the figures above describe only the covered weight above --")
                out.append("  they are a partial, conservative read, not a whole-portfolio number.")
            if dd["top_contributors"]:
                out.append("  Top contributors to drawdown:")
                for label, contrib in dd["top_contributors"]:
                    out.append("    %-20s %.4f" % (label[:20], contrib))
            if dd["impairment_flags"]:
                out.append("  >10% single-position impairment:")
                for label, contrib in dd["impairment_flags"]:
                    out.append("    %-20s %.4f" % (label[:20], contrib))
            out.append("  Historical max drawdown of comparable exposure: DATA NOT AVAILABLE")
            out.append("  (this script does not fetch a historical return series)")
        out.append("")

    if r["data_confidence"] is not None:
        out.append("PORTFOLIO DATA CONFIDENCE  %.0f/100  (value-weighted per-holding scores)"
                    % r["data_confidence"])
        out.append("  Unpriced holdings carry no weight and no confidence here -- they are")
        out.append("  excluded rather than scored, so a book with real coverage gaps can")
        out.append("  still show a high number. Coverage is reported separately above;")
        out.append("  read the two together, not this score alone.")
        out.append("")

    out.append("CASH DRAG (ISK)")
    out.append("-" * WIDTH)
    out.append("  Cash  %.2f %s  =  %s of the account"
                % (r["cash"]["amount"], r["cash"]["currency"], _pct(r["cash"]["weight"]).strip()))
    out.append("  An ISK is schablon-taxed on total account value INCLUDING cash, so idle")
    out.append("  cash is not costless even though it earns no return. This is an")
    out.append("  observation, not a tax calculation.")
    if r.get("schablon"):
        s = r["schablon"]
        if s.get("illustrative_annual_cost") is not None:
            out.append("  Illustrative schablon drag on this cash: ~%.2f %s/yr at %.2f%%"
                        % (s["illustrative_annual_cost"], r["currency"], s["rate_pct"]))
            out.append("  ASSUMPTION -- rate supplied by the caller; ignores quarterly")
            out.append("  kapitalunderlag averaging.")
            if s.get("allowance") is not None:
                if s.get("likely_untaxed"):
                    out.append("  Total account value is at or below the %.0f %s allowance "
                                "supplied --" % (s["allowance"], r["currency"]))
                    out.append("  this drag is likely untaxed in practice this year.")
                else:
                    out.append("  Total account value exceeds the %.0f %s allowance supplied; "
                                "this" % (s["allowance"], r["currency"]))
                    out.append("  figure does not net that allowance off cash specifically.")
        else:
            out.append("  Schablon rate: ASSUMPTION NOT SUPPLIED -- pass --schablon-rate")
            out.append("  (statslaanteraentan for the preceding November, plus the statutory")
            out.append("  uplift, subject to a statutory floor that has changed before and may")
            out.append("  change again -- do not hardcode either as a remembered number; check")
            out.append("  Skatteverket / Riksgaelden) to size the drag.")
    out.append("")

    ff = r["foreign_flag"]
    out.append("FOREIGN WITHHOLDING FLAG")
    out.append("-" * WIDTH)
    if ff.get("status") == "DATA NOT AVAILABLE":
        out.append("  DATA NOT AVAILABLE: %s" % ff["reason"])
    else:
        out.append("  Non-%s holdings: %s of the account (flag threshold %.0f%%, this"
                    % (r["currency"], _pct(ff["foreign_weight"]).strip(), ff["threshold_pct"]))
        out.append("  script's own judgement call, not a sourced figure)")
        if ff["material"]:
            out.append("  FLAG: material foreign exposure. In an ISK, foreign dividend")
            out.append("  withholding is creditable only up to the schablon amount -- a")
            out.append("  portfolio this heavy in non-Swedish payers can lose part of that")
            out.append("  credit. This is a flag, not a computed loss.")
        else:
            out.append("  Below the flag threshold; no action noted.")
    out.append("")

    out.append("This does not account for the user's tax position, time horizon, income")
    out.append("needs or existing outside exposure (references/portfolio.md). Cost basis")
    out.append("and unrealised P&L are intentionally not shown here.")
    return "\n".join(out)


def render_json(r):
    return json.dumps(r, indent=2, default=str)


# ==========================================================================
# CLI
# ==========================================================================

def apply_schablon(result, rate_pct, allowance):
    """One illustrative line, never a tax calculation: this deliberately does
    NOT model the account-wide tax-free allowance against the cash slice
    alone (kapitalunderlag is computed over the whole account, quarterly,
    not per holding) -- it prices this cash's own schablon exposure at the
    given rate and states the allowance as a separate qualifier instead of
    silently netting it off a number it was never meant to offset."""
    if rate_pct is None:
        result["schablon"] = {"illustrative_annual_cost": None}
        return
    cash_value = result["cash"]["value_sek"] or 0.0
    total_value = result.get("total_value") or 0.0
    result["schablon"] = {
        "rate_pct": rate_pct, "allowance": allowance,
        "illustrative_annual_cost": cash_value * rate_pct / 100.0,
        "likely_untaxed": allowance is not None and total_value <= allowance,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="default", help="portfolio name (portfolio_store.py)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--schablon-rate", type=float, default=None, metavar="PCT",
                    help="ISK schablon rate as a percent, e.g. 3.62. Never defaulted "
                         "in code -- source it from Skatteverket/Riksgaelden yourself.")
    ap.add_argument("--schablon-allowance", type=float, default=None, metavar="SEK",
                    help="Tax-free allowance in SEK, if you want it netted off the "
                         "illustrative figure. Also never defaulted in code.")
    ap.add_argument("--foreign-threshold", type=float, default=20.0, metavar="PCT",
                    help="Foreign-withholding flag threshold, as a percent of account "
                         "weight (default 20; a judgement call, not a sourced figure).")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        _selftest()
        return

    try:
        store = portfolio_store_mod()
    except ImportError as exc:
        print("DATA NOT AVAILABLE: portfolio_store.py is not available yet (%s)." % exc)
        print("This script depends on it for the holdings, cash and quantities; "
              "nothing else here can run without it.")
        sys.exit(1)

    portfolio = store.load(args.name)
    result = build(portfolio, foreign_threshold_pct=args.foreign_threshold)
    apply_schablon(result, args.schablon_rate, args.schablon_allowance)

    if args.as_json:
        print(render_json(result))
    else:
        print(render_text(result))


# ==========================================================================
# Self-test -- the guarantees this module rests on, hand-checkable.
# ==========================================================================

def _selftest():
    ok = 0

    # -- Herfindahl / effective N, hand-computed --------------------------
    hhi = herfindahl([0.5, 0.3, 0.2])
    assert abs(hhi - 0.38) < 1e-9, hhi
    ok += 1
    n = effective_n(hhi)
    assert abs(n - (1 / 0.38)) < 1e-9, n
    ok += 1
    assert herfindahl([0.25, 0.25, 0.25, 0.25]) == 0.25
    assert effective_n(0.25) == 4.0
    ok += 2

    # -- bear-case ratio AND drawdown, hand-computed -----------------------
    # cash 10%, A 50% weight bear/price = 60/100 = 0.6, B 40% weight 80/100=0.8
    # ratio = 0.10*1 + 0.50*0.6 + 0.40*0.8 = 0.10+0.30+0.32 = 0.72
    # drawdown = 1 - 0.72 = 0.28  (a different number from the ratio)
    entries = [{"label": "A", "weight": 0.50, "bear_value": 60, "price": 100},
              {"label": "B", "weight": 0.40, "bear_value": 80, "price": 100}]
    dd = bear_case(entries, cash_weight=0.10)
    assert abs(dd["ratio"] - 0.72) < 1e-9, dd["ratio"]
    assert abs(dd["drawdown"] - 0.28) < 1e-9, dd["drawdown"]
    assert dd["ratio"] != dd["drawdown"]
    ok += 3
    assert abs(dd["coverage_weight"] - 1.0) < 1e-9
    ok += 1
    # A loses 0.50 - 0.30 = 0.20 of the portfolio, B loses 0.40-0.32=0.08
    assert dd["top_contributors"][0] == ("A", 0.5 - 0.3)
    assert dd["top_contributors"][1][0] == "B"
    ok += 2
    # A's own contribution (0.20) exceeds the 10% single-position threshold
    assert ("A", 0.2) in dd["impairment_flags"]
    assert all(label != "B" for label, _c in dd["impairment_flags"])
    ok += 2

    # partial coverage: a holding with no bear= tag is excluded, not assumed
    entries2 = [{"label": "A", "weight": 0.50, "bear_value": 60, "price": 100},
               {"label": "B", "weight": 0.40, "bear_value": None, "price": 100}]
    dd2 = bear_case(entries2, cash_weight=0.10)
    assert abs(dd2["coverage_weight"] - 0.60) < 1e-9, dd2["coverage_weight"]
    assert abs(dd2["ratio"] - (0.10 + 0.30)) < 1e-9
    ok += 2

    # -- FX conversion -----------------------------------------------------
    fx_table = {"EUR": {"sek_per_unit": 11.0, "obs_date": "2026-08-28",
                       "source": "Riksbanken SWEA", "status": "OK"}}
    rate, row = sek_per("EUR", fx_table)
    assert rate == 11.0
    market_value_sek = 10 * 1000 * rate       # 10 shares @ 1000 EUR
    assert market_value_sek == 110000.0, market_value_sek
    ok += 2
    rate_sek, _ = sek_per("SEK", fx_table)
    assert rate_sek == 1.0
    rate_missing, _ = sek_per("NOK", fx_table)
    assert rate_missing is None
    ok += 2

    # -- weighted Data Confidence, hand-computed ---------------------------
    # Published TODAY, not a fixed past date: FRESHNESS_DAYS["price"] is 1
    # day, so a hardcoded date would silently become STALE (and the hand-
    # computed 75/20/51.5 below wrong) the day after this file was written.
    today_s = datetime.date.today().isoformat()
    fact_a = FinancialFact("price", 100, "nasdaq_reference", today_s,
                           publication_date=today_s, currency="SEK",
                           freshness_key="price", verification=Verification.SINGLE_SOURCE)
    conf_a = holding_confidence([fact_a], sector_known=True, bear_known=True)
    assert conf_a == 75.0, conf_a          # 100 - 0 (tier1) - 25 (unverified) = 75
    ok += 1
    fact_b = FinancialFact("price", 50, "yahoo", today_s,
                           publication_date=today_s, currency="USD",
                           freshness_key="price", verification=Verification.SINGLE_SOURCE)
    conf_b = holding_confidence([fact_b], sector_known=False, bear_known=False)
    # 100 - 30 (tier4, all-facts-not-tier1) - 25 (unverified) = 45, then -10 -15 = 20
    assert conf_b == 20.0, conf_b
    ok += 1
    weighted = [(0.50, conf_a), (0.40, conf_b), (0.10, float(CASH_CONFIDENCE))]
    agg = portfolio_confidence(weighted)
    assert abs(agg - 51.5) < 1e-9, agg
    ok += 1

    # -- sector/driver overlap: three same-sector holdings are one exposure
    holdings = [
        {"label": "A", "weight": 0.30, "sector": "Industrials", "driver": None},
        {"label": "B", "weight": 0.25, "sector": "Industrials", "driver": None},
        {"label": "C", "weight": 0.20, "sector": "Industrials", "driver": None},
        {"label": "D", "weight": 0.25, "sector": "Financials", "driver": None},
    ]
    ov = _overlap(holdings)
    assert ov["largest_single_holding_weight"] == 0.30
    assert abs(ov["largest_true_exposure"]["weight"] - 0.75) < 1e-9, ov["largest_true_exposure"]
    assert set(ov["largest_true_exposure"]["members"]) == {"A", "B", "C"}
    # the true exposure (0.75) is materially larger than the single largest
    # holding (0.30) -- the whole point of the hidden-overlap section
    assert ov["largest_true_exposure"]["weight"] > ov["largest_single_holding_weight"]
    ok += 4

    # explicit driver= tags override the sector fallback and can span sectors
    holdings2 = [
        {"label": "X", "weight": 0.30, "sector": "Industrials", "driver": "mining capex"},
        {"label": "Y", "weight": 0.20, "sector": "Basic Materials", "driver": "mining capex"},
    ]
    ov2 = _overlap(holdings2)
    assert abs(ov2["driver_groups"][0]["weight"] - 0.50) < 1e-9
    assert set(ov2["driver_groups"][0]["members"]) == {"X", "Y"}
    ok += 2

    # -- note tag parsing ---------------------------------------------------
    tags = parse_tags("bear=210; driver=mining capex; core position since 2019")
    assert tags["bear"] == 210.0
    assert tags["driver"] == "mining capex"
    ok += 2
    assert parse_tags(None) == {}
    assert parse_tags("just a plain note") == {}
    ok += 2

    # -- a missing price is reported, never silently dropped ---------------
    portfolio = {
        "name": "t", "account_type": "ISK", "currency": "SEK",
        "cash": {"amount": 1000.0, "currency": "SEK"},
        "holdings": [{"lei": None, "isin": "SE0000000000", "name": "Ghost AB",
                     "symbol": "GHOST.ST", "quantity": 10, "cost_per_share": None,
                     "cost_currency": None, "acquired": None, "note": None}],
    }
    old_fetch_price = globals()["fetch_price"]
    old_fetch_sector = globals()["fetch_sector"]
    old_fetch_fx = globals()["fetch_fx"]
    globals()["fetch_price"] = lambda symbol: None
    globals()["fetch_sector"] = lambda name, symbol: (None, None)
    globals()["fetch_fx"] = lambda currencies: {}
    try:
        r = build(portfolio)
    finally:
        globals()["fetch_price"] = old_fetch_price
        globals()["fetch_sector"] = old_fetch_sector
        globals()["fetch_fx"] = old_fetch_fx
    assert r["holdings"] == []
    assert len(r["unresolved"]) == 1, r["unresolved"]
    assert r["unresolved"][0]["status"] == "DATA NOT AVAILABLE"
    assert r["unresolved"][0]["label"] == "GHOST.ST"
    ok += 3

    # cost basis never appears anywhere in a rendered report
    portfolio2 = {
        "name": "t2", "account_type": "ISK", "currency": "SEK",
        "cash": {"amount": 500.0, "currency": "SEK"},
        "holdings": [{"lei": None, "isin": "SE1", "name": "Priced AB", "symbol": "PRICED.ST",
                     "quantity": 5, "cost_per_share": 999999.0, "cost_currency": "SEK",
                     "acquired": "2020-01-01", "note": "bear=80"}],
    }
    globals()["fetch_price"] = lambda symbol: {"price": 100.0, "currency": "SEK",
                                              "source_key": "nasdaq_reference",
                                              "source_label": "test", "as_of": "2026-08-28"}
    globals()["fetch_sector"] = lambda name, symbol: ("Industrials", "nasdaq_reference")
    globals()["fetch_fx"] = lambda currencies: {}
    try:
        r2 = build(portfolio2)
        text = render_text(r2)
    finally:
        globals()["fetch_price"] = old_fetch_price
        globals()["fetch_sector"] = old_fetch_sector
        globals()["fetch_fx"] = old_fetch_fx
    assert "999999" not in text
    ok += 1

    print("portfolio_metrics selftest: %d assertions passed" % ok)


if __name__ == "__main__":
    main()
