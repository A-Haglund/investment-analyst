#!/usr/bin/env python3
"""A persistent, falsifiable investment thesis that can be re-tested later.

An analysis is a document. Six months later nobody can answer the only question
that matters - "is the case I wrote still true?" - because the case was prose,
and prose does not re-run.

This module turns the case into an object. Every thesis carries the specific
measurable series that decides it and the numeric triggers that would kill it,
so the same claim can be re-tested against tomorrow's filing without a human
remembering what was meant.

Four things are enforced here rather than requested (spec SS13, SS14):

  1. FALSIFIABILITY AT CREATION. A thesis with no numeric invalidation trigger
     is an opinion. It is rejected with the reason, not stored. "Sandvik is a
     quality compounder" cannot be wrong, so it cannot be right either.

  2. IDENTITY, NOT NAME. The ledger is keyed on LEI (or ISIN) resolved through
     company_resolve.py. "Volvo" is two listed companies with different
     accounts; a ledger keyed on a display name merges them silently. Display
     names are recorded as aliases only.

  3. SILENCE IS NOT CONFIRMATION. A thesis whose evidence cannot be re-fetched
     is UNKNOWN, never STABLE. Likewise a quarterly persistence requirement
     tested against annual-only data is UNKNOWN, not CLEAR: an annual figure
     above a threshold does not prove that no three consecutive quarters were
     below it.

  4. AUDITABILITY. Every status carries the timestamp it was set, the as-of
     date and mode it was computed under, the breaker outcomes that produced
     it, and the facts behind those outcomes. `--history` reads it back.

Evidence is stored as finfact.FinancialFact records, so a source, a period, a
publication date and a verification grade travel with every number. That is
what makes `--evaluate --as-of 2024-06-30` honest: Mode.HISTORICAL drops any
fact the world could not have known on that date.

STORAGE
    One JSON file per company under the user's home directory:

        ~/.investment-analyst/thesis-ledger/<KEY>.json      (KEY = LEI-xxx or
                                                             ISIN-xxx)
        ~/.investment-analyst/thesis-ledger/index.json      (alias -> key map)

    Created lazily on first write, survives between runs, one file per issuer,
    and every file carries "schema_version" so the format can move.
    Override the root with THESIS_LEDGER_HOME.

USAGE
    thesis_ledger.py "Sandvik" --add "Mining aftermarket holds group EBIT
        margin at or above 15% through the capex cycle."
        --metric ebit_margin --breaker "ebit_margin < 15% for 2 consecutive years"
    thesis_ledger.py "Sandvik" --list
    thesis_ledger.py "Sandvik" --evaluate            # re-test every breaker
    thesis_ledger.py "Sandvik" --evaluate --as-of 2024-06-30    # HISTORICAL
    thesis_ledger.py "Sandvik" --history
    thesis_ledger.py --all                           # every tracked company
    thesis_ledger.py "Sandvik" --observe "organic_revenue_growth=15.0@2026-03-31;source=cision;pub=2026-04-22"
    thesis_ledger.py "Sandvik" --retire T3 --reason "..."   # kept, never deleted
    thesis_ledger.py --metrics                       # what can be tested, and what cannot
    thesis_ledger.py --selftest                      # parser + status assertions, offline

    --json works on every command. --offline uses only the on-disk cache.

--as-of IS A BACK-TEST, NOT AN UPDATE. It answers "what would this thesis have
said then", is written into the history labelled HISTORICAL, and deliberately
does NOT overwrite the live status - otherwise asking a question about the past
silently rewrites the present.

BREAKER GRAMMAR
    <metric> <op> <value>[%|x] [for <n> [consecutive] quarters|years]
    joined by OR / AND, one logical group per --breaker.
    Operators: < <= > >= == != and the words below/under/less than/falls
    below/at most/above/over/exceeds/greater than/at least/no less than.
    Metric names accept aliases: "operating margin", "net debt / EBITDA",
    "organic revenue growth". Run --metrics for the full list.

STATUSES
    BROKEN     a breaker fired: the condition held for the full persistence run
    WARNING    breached in the latest period but not yet for the full run, or
               within 2pp / 0.25x of the trigger
    UNKNOWN    the data could not be fetched, the inputs are untagged, the
               metric needs a human, or the periodicity cannot answer the
               question. Never used as a synonym for "fine"
    STABLE     every breaker clear
    IMPROVING  every breaker clear and every headroom widened
    CONFIRMED  every breaker clear with >=25% headroom and none narrowing

EXIT CODES
    0  ok
    2  company identity ambiguous or unresolvable - nothing was written
    3  no such company in the ledger, or no such thesis
    4  --evaluate completed and AT LEAST ONE BREAKER TRIGGERED
    5  thesis rejected at creation as not falsifiable

Python 3 standard library only. Free, keyless.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import time

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_VERSION = 1
NA = "DATA NOT AVAILABLE"


def load(name):
    """Import a sibling script by path. Same helper company_resolve.py uses."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


finfact = load("finfact")
FinancialFact = finfact.FinancialFact
Verification = finfact.Verification
State = finfact.State
Mode = finfact.Mode

# finfact grew publication_is_upper_bound so a harvest date can be used as a
# point-in-time cutoff without being credited as a real publication date.
# Probe rather than assume, so an older finfact.py still imports.
HAS_UPPER_BOUND = "publication_is_upper_bound" in getattr(
    FinancialFact, "__slots__", ())


def mk_fact(**kw):
    if not HAS_UPPER_BOUND:
        kw.pop("publication_is_upper_bound", None)
    return FinancialFact(**kw)


_LAZY = {}


def lazy(name):
    if name not in _LAZY:
        _LAZY[name] = load(name)
    return _LAZY[name]


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def ledger_home():
    override = os.environ.get("THESIS_LEDGER_HOME")
    if override:
        return os.path.abspath(override)
    return os.path.join(os.path.expanduser("~"), ".investment-analyst", "thesis-ledger")


CACHE_DIR = os.path.join(tempfile.gettempdir(), "investment-analyst-cache", "thesis_ledger")
FUNDAMENTALS_TTL = 7 * 86400        # annual filings do not move within a week
IDENTITY_TTL = 30 * 86400


def _cache_path(key):
    return os.path.join(CACHE_DIR, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")


def cached(key, ttl, produce, offline=False):
    """Disk cache. None is never cached - a source that was down must be
    retried, not remembered as an absence."""
    path = _cache_path(key)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            blob = json.load(fh)
        if offline or time.time() - blob["t"] < ttl:
            return blob["v"]
    except (OSError, ValueError, KeyError):
        pass
    if offline:
        return None
    value = produce()
    if value is not None:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"t": time.time(), "v": value}, fh)
            os.replace(tmp, path)
        except OSError:
            pass
    return value


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)          # atomic: a crash never leaves half a ledger


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today():
    return datetime.date.today()


# --------------------------------------------------------------------------
# Metric registry
#
# Split three ways on purpose, because the honest answer to "can the ledger
# re-test this by itself?" is different for each:
#
#   REPORTED  a line item tagged in ESEF. Machine-readable.
#   DERIVED   arithmetic over reported line items. Machine-readable, but only
#             where every input is actually tagged - ESEF Phase 1 mandates the
#             primary statements only, so D&A and non-current borrowings are
#             frequently absent and anything needing them degrades to UNKNOWN.
#   MANUAL    never tagged anywhere free: organic growth (excludes FX and M&A
#             by management's own definition), order intake, market share,
#             churn. A human reads them out of the report and enters them with
#             --observe; until then the breaker is UNKNOWN, not CLEAR.
# --------------------------------------------------------------------------

PERCENT, RATIO, MULTIPLE, MONEY, PERSHARE, COUNT = (
    "percent", "ratio", "x", "currency", "per_share", "count")


class MetricDef(object):
    __slots__ = ("mid", "label", "unit", "needs", "optional", "fn", "yoy",
                 "better", "manual_reason", "note")

    def __init__(self, mid, label, unit, needs=(), fn=None, optional=(),
                 yoy=False, better="high", manual_reason=None, note=None):
        self.mid = mid
        self.label = label
        self.unit = unit
        self.needs = tuple(needs)
        self.optional = tuple(optional)
        self.fn = fn
        self.yoy = yoy
        self.better = better           # "high" or "low"
        self.manual_reason = manual_reason
        self.note = note

    @property
    def kind(self):
        if self.manual_reason:
            return "MANUAL"
        if self.yoy or len(self.needs) + len(self.optional) > 1:
            return "DERIVED"
        return "REPORTED"


def _div(a, b):
    return None if (a is None or b in (None, 0)) else a / b


def _pct(a, b):
    v = _div(a, b)
    return None if v is None else 100.0 * v


M = {}


def _reg(*defs):
    for d in defs:
        M[d.mid] = d


_reg(
    # -- reported -------------------------------------------------------
    MetricDef("revenue", "Revenue", MONEY, ["revenue"], lambda r: r["revenue"]),
    MetricDef("ebit", "EBIT (operating income)", MONEY, ["ebit"], lambda r: r["ebit"]),
    MetricDef("net_income", "Net income", MONEY, ["net_income"], lambda r: r["net_income"]),
    MetricDef("cfo", "Cash from operations", MONEY, ["cfo"], lambda r: r["cfo"]),
    MetricDef("capex", "Capital expenditure", MONEY, ["capex"], lambda r: r["capex"], better="low"),
    MetricDef("equity", "Total equity", MONEY, ["equity"], lambda r: r["equity"]),
    MetricDef("cash", "Cash and equivalents", MONEY, ["cash"], lambda r: r["cash"]),
    MetricDef("eps_diluted", "Diluted EPS", PERSHARE, ["eps_diluted"], lambda r: r["eps_diluted"]),
    MetricDef("eps_basic", "Basic EPS", PERSHARE, ["eps_basic"], lambda r: r["eps_basic"]),
    MetricDef("total_assets", "Total assets", MONEY, ["total_assets"], lambda r: r["total_assets"]),
    MetricDef("interest_expense", "Interest expense", MONEY, ["interest_expense"],
              lambda r: r["interest_expense"], better="low"),
    MetricDef("dividends_paid", "Dividends paid", MONEY, ["dividends_paid"],
              lambda r: r["dividends_paid"]),

    # -- derived --------------------------------------------------------
    MetricDef("ebit_margin", "EBIT margin", PERCENT, ["ebit", "revenue"],
              lambda r: _pct(r["ebit"], r["revenue"])),
    MetricDef("gross_margin", "Gross margin", PERCENT, ["gross_profit", "revenue"],
              lambda r: _pct(r["gross_profit"], r["revenue"])),
    MetricDef("net_margin", "Net margin", PERCENT, ["net_income", "revenue"],
              lambda r: _pct(r["net_income"], r["revenue"])),
    MetricDef("ebitda", "EBITDA", MONEY, ["ebit", "dnda"],
              lambda r: r["ebit"] + r["dnda"],
              note="EBIT plus tagged D&A. ESEF often does not tag D&A."),
    MetricDef("ebitda_margin", "EBITDA margin", PERCENT, ["ebit", "dnda", "revenue"],
              lambda r: _pct(r["ebit"] + r["dnda"], r["revenue"])),
    MetricDef("fcf", "Free cash flow", MONEY, ["cfo", "capex"],
              lambda r: r["cfo"] - abs(r["capex"])),
    MetricDef("fcf_margin", "FCF margin", PERCENT, ["cfo", "capex", "revenue"],
              lambda r: _pct(r["cfo"] - abs(r["capex"]), r["revenue"])),
    MetricDef("fcf_conversion", "FCF / net income", PERCENT, ["cfo", "capex", "net_income"],
              lambda r: _pct(r["cfo"] - abs(r["capex"]), r["net_income"])),
    MetricDef("roe", "Return on equity", PERCENT, ["net_income", "equity"],
              lambda r: _pct(r["net_income"], r["equity"])),
    MetricDef("roa", "Return on assets", PERCENT, ["net_income", "total_assets"],
              lambda r: _pct(r["net_income"], r["total_assets"])),
    MetricDef("roce", "Return on capital employed", PERCENT,
              ["ebit", "total_assets", "current_liabilities"],
              lambda r: _pct(r["ebit"], r["total_assets"] - r["current_liabilities"]),
              note="EBIT / (total assets - current liabilities). Pre-tax, "
                   "balance-sheet-date basis, not an average."),
    MetricDef("equity_ratio", "Equity / total assets", PERCENT, ["equity", "total_assets"],
              lambda r: _pct(r["equity"], r["total_assets"])),
    MetricDef("current_ratio", "Current ratio", RATIO,
              ["current_assets", "current_liabilities"],
              lambda r: _div(r["current_assets"], r["current_liabilities"])),
    MetricDef("net_debt", "Net debt", MONEY, ["debt_lt", "debt_st", "cash"],
              lambda r: r["debt_lt"] + r["debt_st"] + r.get("lease_liabilities", 0.0)
              - r["cash"], optional=["lease_liabilities"], better="low",
              note="Excludes lease liabilities where IFRS 16 leases are untagged; "
                   "the figure is then understated and marked INCOMPLETE."),
    MetricDef("net_debt_ebitda", "Net debt / EBITDA", MULTIPLE,
              ["debt_lt", "debt_st", "cash", "ebit", "dnda"],
              lambda r: _div(r["debt_lt"] + r["debt_st"] + r.get("lease_liabilities", 0.0)
                             - r["cash"], r["ebit"] + r["dnda"]),
              optional=["lease_liabilities"], better="low"),
    MetricDef("interest_cover", "EBIT / interest expense", MULTIPLE,
              ["ebit", "interest_expense"],
              lambda r: _div(r["ebit"], abs(r["interest_expense"]))),
    MetricDef("capex_intensity", "Capex / revenue", PERCENT, ["capex", "revenue"],
              lambda r: _pct(abs(r["capex"]), r["revenue"]), better="low"),
    MetricDef("dividend_payout", "Dividends / net income", PERCENT,
              ["dividends_paid", "net_income"],
              lambda r: _pct(abs(r["dividends_paid"]), r["net_income"]), better="low"),

    # -- derived, year-on-year -------------------------------------------
    MetricDef("revenue_growth", "Revenue growth (reported, YoY)", PERCENT, ["revenue"],
              lambda cur, prev: _pct(cur["revenue"] - prev["revenue"], abs(prev["revenue"])),
              yoy=True,
              note="REPORTED growth. Includes FX and acquisitions - it is NOT "
                   "organic growth and must not be substituted for it."),
    MetricDef("ebit_growth", "EBIT growth (YoY)", PERCENT, ["ebit"],
              lambda cur, prev: _pct(cur["ebit"] - prev["ebit"], abs(prev["ebit"])), yoy=True),
    MetricDef("eps_growth", "Diluted EPS growth (YoY)", PERCENT, ["eps_diluted"],
              lambda cur, prev: _pct(cur["eps_diluted"] - prev["eps_diluted"],
                                     abs(prev["eps_diluted"])), yoy=True),
    MetricDef("net_income_growth", "Net income growth (YoY)", PERCENT, ["net_income"],
              lambda cur, prev: _pct(cur["net_income"] - prev["net_income"],
                                     abs(prev["net_income"])), yoy=True),

    # -- manual: no free machine-readable source exists ------------------
    MetricDef("organic_revenue_growth", "Organic revenue growth", PERCENT,
              manual_reason="Management-defined (excludes FX and structure). Never "
                            "tagged in ESEF. Read it from the interim "
                            "report and enter it with --observe."),
    MetricDef("order_intake", "Order intake", MONEY,
              manual_reason="Disclosed in the report text, not in the tagged primary "
                            "statements. Enter with --observe."),
    MetricDef("order_intake_growth", "Order intake growth", PERCENT,
              manual_reason="As order_intake. Enter with --observe."),
    MetricDef("book_to_bill", "Book-to-bill", RATIO,
              manual_reason="Derived from untagged order intake. Enter with --observe."),
    MetricDef("market_share", "Market share", PERCENT,
              manual_reason="Third-party industry data. No free authoritative source."),
    MetricDef("churn", "Customer churn", PERCENT,
              manual_reason="Disclosed inconsistently, never tagged. Enter with --observe."),
    MetricDef("regulated_revenue_share", "Revenue from regulated markets", PERCENT,
              manual_reason="Segment/geography disclosure in the report text. "
                            "Enter with --observe."),
    MetricDef("customer_concentration", "Largest customer share of revenue", PERCENT,
              manual_reason="Note-level disclosure, not tagged under ESEF Phase 1.",
              better="low"),
    MetricDef("employees", "Employees", COUNT,
              manual_reason="Not tagged. Enter with --observe."),
    MetricDef("pe", "P/E", MULTIPLE,
              manual_reason="Needs a price feed. The ledger deliberately stores no "
                            "prices: a thesis that flips on a quote is a trade, not a "
                            "thesis. Use quote.py at read time.", better="low"),
    MetricDef("ev_ebit", "EV / EBIT", MULTIPLE,
              manual_reason="As pe - needs price and a full net-debt bridge.", better="low"),
    MetricDef("fcf_yield", "FCF yield", PERCENT,
              manual_reason="As pe - needs price."),
    MetricDef("dividend_yield", "Dividend yield", PERCENT,
              manual_reason="As pe - needs price."),
)

# Free-text names an analyst actually writes, mapped to metric ids. Matching is
# done on a squashed form (lowercase, alphanumerics only), so "EBIT margin",
# "ebit-margin" and "EBITmargin" all land on the same id.
ALIASES = {
    "operatingmargin": "ebit_margin", "ebitmargin": "ebit_margin",
    "operatingprofitmargin": "ebit_margin", "rorelsemarginal": "ebit_margin",
    "ebitamargin": "ebit_margin",
    "grossmargin": "gross_margin", "bruttomarginal": "gross_margin",
    "netmargin": "net_margin", "profitmargin": "net_margin",
    "ebitdamargin": "ebitda_margin",
    "salesgrowth": "revenue_growth", "revenuegrowth": "revenue_growth",
    "topline": "revenue_growth", "toplinegrowth": "revenue_growth",
    "reportedrevenuegrowth": "revenue_growth",
    "organicgrowth": "organic_revenue_growth",
    "organicrevenuegrowth": "organic_revenue_growth",
    "organicsalesgrowth": "organic_revenue_growth",
    "netdebtebitda": "net_debt_ebitda", "netdebttoebitda": "net_debt_ebitda",
    "leverage": "net_debt_ebitda", "gearing": "net_debt_ebitda",
    "netdebt": "net_debt",
    "interestcover": "interest_cover", "interestcoverage": "interest_cover",
    "equityratio": "equity_ratio", "soliditet": "equity_ratio",
    "operatingincome": "ebit", "operatingprofit": "ebit", "ebita": "ebit",
    "returnonequity": "roe", "returnonassets": "roa",
    "returnoncapitalemployed": "roce", "capitalefficiency": "roce",
    "freecashflow": "fcf", "fcfmargin": "fcf_margin",
    "cashconversion": "fcf_conversion", "fcfconversion": "fcf_conversion",
    "epsgrowth": "eps_growth", "earningsgrowth": "eps_growth",
    "dilutedeps": "eps_diluted", "eps": "eps_diluted",
    "sales": "revenue", "turnover": "revenue", "omsattning": "revenue",
    "payoutratio": "dividend_payout", "dividendpayout": "dividend_payout",
    "capexintensity": "capex_intensity",
    "orderintake": "order_intake", "orders": "order_intake",
    "booktobill": "book_to_bill",
    "marketshare": "market_share",
    "customerchurn": "churn",
    "priceearnings": "pe", "pertal": "pe", "pe": "pe",
    "evebit": "ev_ebit", "evtoebit": "ev_ebit",
}


def squash(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def resolve_metric(text):
    """Free text -> metric id, or None."""
    s = squash(text)
    if not s:
        return None
    if s in M:
        return s
    if s in ALIASES:
        return ALIASES[s]
    for mid in M:
        if squash(mid) == s:
            return mid
    for mid, d in M.items():
        if squash(d.label) == s:
            return mid
    return None


# Raw XBRL line items, normalised to one vocabulary so a breaker written for a
# Swedish ESEF filer reads the same as one written for a US 10-K filer.
ESEF_TO_RAW = {
    "revenue": "revenue", "operating_income": "ebit", "net_income": "net_income",
    "gross_profit": "gross_profit", "equity": "equity", "total_assets": "total_assets",
    "total_liabilities": "total_liabilities", "cash": "cash", "cfo": "cfo",
    "capex": "capex", "depreciation_amort": "dnda",
    "interest_expense": "interest_expense", "borrowings": "debt_lt",
    "borrowings_current": "debt_st", "lease_liabilities": "lease_liabilities",
    "current_assets": "current_assets", "current_liabilities": "current_liabilities",
    "inventory": "inventory", "receivables": "receivables", "payables": "payables",
    "eps_diluted": "eps_diluted", "eps_basic": "eps_basic",
    "dividends_paid": "dividends_paid", "tax": "tax", "pretax_income": "pretax_income",
    "goodwill": "goodwill", "intangibles": "intangibles", "cost_of_sales": "cogs",
}

# --------------------------------------------------------------------------
# Breaker parsing (spec SS14)
#
#   <metric> <op> <value>[unit] [for <n> [consecutive] <period>]
#
# joined by OR / AND. Everything is required to be numeric: "margins weaken" is
# not a breaker, it is a mood.
# --------------------------------------------------------------------------

SYM_OPS = [("<=", "le"), (">=", "ge"), ("==", "eq"), ("!=", "ne"),
           ("=<", "le"), ("=>", "ge"), ("<", "lt"), (">", "gt"), ("=", "eq")]

WORD_OPS = [
    (r"falls?\s+below", "lt"), (r"drops?\s+below", "lt"), (r"goes?\s+below", "lt"),
    (r"is\s+below", "lt"), (r"below", "lt"), (r"under", "lt"),
    (r"less\s+than", "lt"), (r"lower\s+than", "lt"), (r"beneath", "lt"),
    (r"no\s+more\s+than", "le"), (r"at\s+most", "le"), (r"not\s+above", "le"),
    (r"rises?\s+above", "gt"), (r"climbs?\s+above", "gt"), (r"goes?\s+above", "gt"),
    (r"is\s+above", "gt"), (r"exceeds?", "gt"), (r"above", "gt"), (r"over", "gt"),
    (r"greater\s+than", "gt"), (r"higher\s+than", "gt"), (r"more\s+than", "gt"),
    (r"no\s+less\s+than", "ge"), (r"at\s+least", "ge"), (r"not\s+below", "ge"),
]

OP_SYMBOL = {"lt": "<", "le": "<=", "gt": ">", "ge": ">=", "eq": "==", "ne": "!="}

PERIOD_WORDS = {
    "quarter": "quarters", "quarters": "quarters", "q": "quarters",
    "kvartal": "quarters",
    "year": "years", "years": "years", "yr": "years", "fy": "years", "ar": "years",
    "half": "halves", "halves": "halves", "h1": "halves",
    "period": "periods", "periods": "periods",
    "month": "months", "months": "months",
    "report": "periods", "reports": "periods",
}

PERSIST_RE = re.compile(
    r"\b(?:for|over|across|in|during)\s+(\d+)\s*(?:consecutive\s+|straight\s+|"
    r"successive\s+|rolling\s+)?([a-z]+)(?:\s+in\s+a\s+row)?\b")
PERSIST_RE2 = re.compile(r"\b(\d+)\s*(?:consecutive\s+|straight\s+|successive\s+)"
                         r"([a-z]+)(?:\s+in\s+a\s+row)?\b")

NUM_RE = re.compile(r"(-?\d+(?:[.,]\d+)?)\s*"
                    r"(%|pp|pct|percent|bps?|x|times|sek|eur|usd|nok|dkk|"
                    r"msek|meur|musd|mnok|mdkk|bnsek|bneur|bnusd|m|bn|bln|k)?\b")

SCALE = {"m": 1e6, "msek": 1e6, "meur": 1e6, "musd": 1e6, "mnok": 1e6, "mdkk": 1e6,
         "bn": 1e9, "bln": 1e9, "bnsek": 1e9, "bneur": 1e9, "bnusd": 1e9, "k": 1e3}


class BreakerError(ValueError):
    pass


class Clause(object):
    __slots__ = ("raw", "metric", "op", "threshold", "unit_token", "persistence",
                 "persist_unit")

    def __init__(self, raw, metric, op, threshold, unit_token, persistence, persist_unit):
        self.raw = raw
        self.metric = metric
        self.op = op
        self.threshold = threshold
        self.unit_token = unit_token
        self.persistence = persistence
        self.persist_unit = persist_unit

    def text(self):
        d = M[self.metric]
        val = fmt_value(d.unit, self.threshold)
        s = "%s %s %s" % (self.metric, OP_SYMBOL[self.op], val)
        if self.persistence > 1:
            s += " for %d consecutive %s" % (self.persistence, self.persist_unit)
        return s

    def to_dict(self):
        d = M[self.metric]
        return {"raw": self.raw, "metric": self.metric, "metric_label": d.label,
                "metric_kind": d.kind, "op": self.op, "op_symbol": OP_SYMBOL[self.op],
                "threshold": self.threshold, "unit": d.unit,
                "persistence": self.persistence, "persist_unit": self.persist_unit,
                "normalised": self.text(),
                "machine_evaluable": d.kind != "MANUAL",
                "manual_reason": d.manual_reason}


class Breaker(object):
    """One or more clauses joined by OR / AND. One --breaker argument."""
    __slots__ = ("raw", "logic", "clauses")

    def __init__(self, raw, logic, clauses):
        self.raw = raw
        self.logic = logic
        self.clauses = clauses

    def text(self):
        return (" %s " % self.logic).join(c.text() for c in self.clauses)

    def to_dict(self):
        return {"raw": self.raw, "logic": self.logic,
                "normalised": self.text(),
                "clauses": [c.to_dict() for c in self.clauses],
                "machine_evaluable": all(M[c.metric].kind != "MANUAL"
                                         for c in self.clauses)}

    @staticmethod
    def from_dict(d):
        return Breaker(d["raw"], d["logic"],
                       [Clause(c["raw"], c["metric"], c["op"], c["threshold"],
                               c.get("unit"), c["persistence"], c["persist_unit"])
                        for c in d["clauses"]])


def parse_clause(raw):
    text = " ".join(raw.strip().rstrip(".").split()).lower()
    text = text.replace("−", "-").replace("≤", "<=").replace("≥", ">=")

    persistence, persist_unit = 1, None
    for rx in (PERSIST_RE, PERSIST_RE2):
        m = rx.search(text)
        if m and m.group(2) in PERIOD_WORDS:
            persistence = int(m.group(1))
            persist_unit = PERIOD_WORDS[m.group(2)]
            text = (text[:m.start()] + " " + text[m.end():]).strip()
            break
    if persistence < 1:
        raise BreakerError("persistence must be at least 1 period: %r" % raw)

    op, left, right = None, None, None
    for sym, code in SYM_OPS:
        i = text.find(sym)
        if i >= 0:
            op, left, right = code, text[:i], text[i + len(sym):]
            break
    if op is None:
        for pattern, code in WORD_OPS:
            m = re.search(r"\b" + pattern + r"\b", text)
            if m:
                op, left, right = code, text[:m.start()], text[m.end():]
                break
    if op is None:
        raise BreakerError(
            "no comparison found in %r. A breaker needs an operator and a "
            "number, e.g. 'ebit_margin < 15%% for 2 consecutive years'." % raw)

    metric = resolve_metric(left)
    if metric is None:
        raise BreakerError(
            "unknown metric %r in breaker %r. Run --metrics for the list of "
            "metrics the ledger can hold." % (left.strip(), raw))

    nm = NUM_RE.search(right)
    if not nm:
        raise BreakerError(
            "no numeric threshold in %r. 'ebit margin falls' is a direction, "
            "not a trigger; write 'ebit_margin < 15%%'." % raw)
    threshold = float(nm.group(1).replace(",", "."))
    token = (nm.group(2) or "").lower()

    d = M[metric]
    if token in ("bp", "bps"):
        threshold /= 100.0
        token = "%"
    if token in SCALE:
        threshold *= SCALE[token]
    if d.unit == PERCENT:
        if token in ("x", "times"):
            raise BreakerError("%s is a percentage; %r reads as a multiple." % (metric, raw))
        if not token and abs(threshold) <= 1.0 and threshold != 0:
            raise BreakerError(
                "%s is stored in PERCENT, so %g is ambiguous in %r - write "
                "'%g%%' if you mean %g percent, or '%g%%' if you meant %g."
                % (metric, threshold, raw, threshold, threshold,
                   threshold * 100, threshold * 100))
    if d.unit in (MULTIPLE, RATIO) and token in ("%", "pct", "percent", "pp"):
        raise BreakerError("%s is a multiple, not a percentage: %r" % (metric, raw))

    return Clause(raw.strip(), metric, op, threshold, token or None,
                  persistence, persist_unit)


SPLIT_RE = re.compile(r"\s+(or|and)\s+|\s*(\|\|)\s*|\s*(&&)\s*", re.I)


def parse_breaker(raw):
    if not raw or not raw.strip():
        raise BreakerError("empty breaker")
    parts, ops, last = [], [], 0
    for m in SPLIT_RE.finditer(raw):
        token = (m.group(1) or m.group(2) or m.group(3) or "").lower()
        logic = "OR" if token in ("or", "||") else "AND"
        # "3 and a half" style false positives are not a risk here because a
        # clause must contain a comparison operator; a fragment without one
        # rejoins its neighbour.
        head = raw[last:m.start()]
        if not _has_comparison(head):
            continue
        parts.append(head)
        ops.append(logic)
        last = m.end()
    parts.append(raw[last:])
    if len(set(ops)) > 1:
        raise BreakerError(
            "mixed OR and AND in one breaker: %r. Give each logical group its "
            "own --breaker so the trigger is unambiguous." % raw)
    logic = ops[0] if ops else "OR"
    clauses = [parse_clause(p) for p in parts if p.strip()]
    if not clauses:
        raise BreakerError("no clauses parsed from %r" % raw)
    return Breaker(raw.strip(), logic, clauses)


def _has_comparison(text):
    t = text.lower()
    if any(sym in t for sym, _ in SYM_OPS):
        return True
    return any(re.search(r"\b" + p + r"\b", t) for p, _ in WORD_OPS)


# --------------------------------------------------------------------------
# Falsifiability gate (spec SS13)
#
# HARD rules cannot be overridden: without them there is nothing to re-test.
# SOFT rules are linguistic and CAN be overridden with --force, which is
# recorded on the thesis so a reader knows the gate was bypassed.
# --------------------------------------------------------------------------

HEDGES = ["may ", "might ", "could ", "should ", "possibly", "potentially",
          "perhaps", "arguably", "hopefully", "i think", "we think", "i believe",
          "we believe", "seems", "appears to", "probably", "likely to"]

VAGUE = ["quality", "great", "strong", "solid", "good", "excellent", "attractive",
         "compelling", "robust", "healthy", "best in class", "best-in-class",
         "wonderful", "impressive", "exciting", "undervalued", "cheap",
         "expensive", "well positioned", "well-positioned", "moat",
         "durable competitive advantage", "wide moat", "world class",
         "world-class", "market leader", "strong management"]

DIRECTION = ["above", "below", "at least", "at most", "exceed", "under", "over",
             "grow", "growth", "expand", "contract", "decline", "improve",
             "hold", "holds", "maintain", "sustain", "stay", "stays", "keep",
             "keeps", "remain", "remains", "rise", "fall", "higher", "lower",
             "faster", "slower", "more than", "less than", "per year",
             "compound", "widen", "narrow", "offset", "absorb", "defend"]


def falsifiability_report(text, metrics, breakers):
    """Return (hard_problems, soft_problems). Empty lists means acceptable."""
    hard, soft = [], []
    body = (text or "").strip()
    low = body.lower()
    words = [w for w in re.split(r"\s+", body) if w]

    if len(words) < 6:
        hard.append(("TOO_SHORT",
                     "A thesis is a sentence, not a label. %d words is not a "
                     "claim that can be tested." % len(words)))
    if not breakers:
        hard.append(("NO_BREAKER",
                     "No --breaker given. A thesis that cannot be falsified is "
                     "not a thesis: give at least one numeric invalidation "
                     "trigger, e.g. --breaker \"ebit_margin < 15% for 2 "
                     "consecutive years\"."))
    if not metrics:
        hard.append(("NO_METRIC",
                     "No --metric given. Name the measurable series that decides "
                     "this thesis, so a later run knows what to re-fetch."))

    if any(h in low for h in HEDGES):
        found = [h.strip() for h in HEDGES if h in low]
        soft.append(("HEDGED",
                     "Hedging (%s) makes the claim unfalsifiable - it is true "
                     "whatever happens. State what WILL hold, and let the "
                     "breaker carry the uncertainty."
                     % ", ".join(sorted(set(found))[:3])))

    has_digit = bool(re.search(r"\d", body))
    has_direction = any(d in low for d in DIRECTION)
    vague_hits = [v for v in VAGUE if v in low]
    if vague_hits and not has_digit:
        soft.append(("VAGUE",
                     "Uses %s with no number. That is a description of a feeling "
                     "about the company, not a claim about it."
                     % ", ".join("'%s'" % v for v in sorted(set(vague_hits))[:3])))
    if not has_digit and not has_direction:
        soft.append(("NO_DIRECTION",
                     "The sentence states no direction and no magnitude. What "
                     "specifically has to keep being true?"))
    named = [mid for mid in metrics if mid and
             (squash(mid) in squash(body) or squash(M[mid].label) in squash(body)
              or any(squash(w) and squash(w) in squash(M[mid].label)
                     for w in words if len(w) > 4))]
    if not named and not has_digit:
        soft.append(("METRIC_NOT_IN_TEXT",
                     "The sentence never mentions the metric that decides it. "
                     "A reader six months from now will not connect the two."))
    return hard, soft


# --------------------------------------------------------------------------
# Identity (spec: LEI or ISIN, never a display name)
# --------------------------------------------------------------------------

LEI_RE = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


class Ambiguous(Exception):
    def __init__(self, reason, candidates):
        Exception.__init__(self, reason)
        self.reason = reason
        self.candidates = candidates


def ledger_key(identity):
    """LEI first, ISIN second. Never the name."""
    if identity.get("lei") and identity["lei"] != NA:
        return "LEI-" + identity["lei"]
    if identity.get("isin") and identity["isin"] != NA:
        return "ISIN-" + identity["isin"]
    raise Ambiguous("no LEI or ISIN could be established, so this company "
                    "cannot be keyed. A ledger keyed on a display name is a "
                    "ledger that will merge two issuers.", [])


def index_path():
    return os.path.join(ledger_home(), "index.json")


def read_index():
    idx = _read_json(index_path())
    if not idx or idx.get("schema_version") != SCHEMA_VERSION:
        idx = rebuild_index()
    return idx


def rebuild_index():
    idx = {"schema_version": SCHEMA_VERSION, "rebuilt": now_iso(), "companies": {}}
    home = ledger_home()
    if os.path.isdir(home):
        for fn in sorted(os.listdir(home)):
            if not fn.endswith(".json") or fn == "index.json":
                continue
            led = _read_json(os.path.join(home, fn))
            if not led or "ledger_key" not in led:
                continue
            idx["companies"][led["ledger_key"]] = _index_entry(led)
    return idx


def _index_entry(led):
    ident = led.get("identity", {})
    return {"file": led["ledger_key"] + ".json",
            "company_name": ident.get("company_name"),
            "legal_name": ident.get("legal_name"),
            "ticker": ident.get("ticker"), "lei": ident.get("lei"),
            "isin": ident.get("isin"),
            "aliases": led.get("aliases", []),
            "theses": len([t for t in led.get("theses", []) if t.get("active", True)]),
            "last_updated": led.get("last_updated"),
            "last_evaluated": led.get("last_evaluated")}


def write_index(idx):
    _write_json(index_path(), idx)


def index_lookup(query):
    idx = read_index()
    q = squash(query)
    if not q:
        return None
    for key, entry in idx["companies"].items():
        if squash(key) == q:
            return key
        for field in ("lei", "isin", "ticker"):
            if entry.get(field) and squash(str(entry[field])) == q:
                return key
        for alias in entry.get("aliases", []):
            if squash(alias) == q:
                return key
        for field in ("company_name", "legal_name"):
            if entry.get(field) and squash(entry[field]) == q:
                return key
    return None


def resolve_identity(query, country=None, offline=False, refresh=False):
    """Canonical identity for a query string. Raises Ambiguous rather than guess."""
    raw = query.strip()
    up = raw.upper().replace("-", "").replace(" ", "")

    if LEI_RE.match(up):
        return _identity_from_lei(up, offline=offline)
    if ISIN_RE.match(up):
        ident = _identity_via_company_resolve(up, country, offline, refresh)
        if ident:
            return ident
        return {"lei": NA, "isin": up, "company_name": up, "legal_name": NA,
                "ticker": NA, "country": up[:2], "reporting_currency": NA,
                "fiscal_year_end": NA, "identity_source": "ISIN supplied directly",
                "identity_confidence": 0.5,
                "identity_warnings": ["Only the ISIN is known; no register was "
                                      "reachable to confirm the issuer."]}

    if not refresh:
        key = index_lookup(raw)
        if key:
            led = read_ledger(key)
            if led:
                ident = dict(led["identity"])
                ident["identity_source"] = "existing ledger entry (%s)" % key
                return ident

    if offline:
        raise Ambiguous("offline and %r is not in the ledger index; identity "
                        "cannot be established without a register." % raw, [])

    ident = _identity_via_company_resolve(raw, country, offline, refresh)
    if ident:
        return ident
    ident = _identity_via_esef_index(raw, country)
    if ident:
        return ident
    raise Ambiguous("%s: no issuer could be resolved for %r. company_resolve.py "
                    "and the ESEF filing index were both checked. This toolkit "
                    "covers European (Nordic/French ESEF) issuers only - a US "
                    "ticker or CIK is out of scope, not merely unresolved."
                    % (NA, raw), [])


def _identity_via_company_resolve(query, country, offline, refresh):
    def produce():
        try:
            cr = lazy("company_resolve")
        except Exception as exc:                      # noqa: BLE001
            return {"_error": "company_resolve.py could not be imported: %s" % exc}
        try:
            kind, needle = cr.classify_query(query)
            if country:
                pass
            entities = cr.mfn_entities(needle)
            lines = cr.nasdaq_lines(needle)
            if kind in ("lei", "orgnr") and not any(
                    needle in ((e.get("leis") or []) +
                               [r.split(":")[-1] for r in e.get("local_refs") or []])
                    for e in entities):
                seed = (cr.gleif(needle).get("legal_name") if kind == "lei"
                        else cr.gleif_by_orgnr(needle))
                if seed:
                    entities += cr.mfn_entities(seed)
                    lines += cr.nasdaq_lines(seed)
            cands = cr.build_candidates(entities, lines)
            if country:
                want = country.upper()
                kept = [c for c in cands
                        if any(i.startswith(want) for i in c.isins)
                        or any((e or "").startswith(want)
                               for e in (c.mfn or {}).get("local_refs") or [])
                        or any((l.get("isin") or "").startswith(want) for l in c.lines)]
                if kept:
                    cands = kept
            listed = [c for c in cands if c.lines or c.symbols]
            if listed:
                cands = listed
            winner, reason, conf, contenders = cr.resolve_candidates(cands, kind, needle)
            if winner is None:
                return {"_ambiguous": reason,
                        "_candidates": [{"company_name": c.display(),
                                         "tickers": sorted(c.symbols),
                                         "isins": sorted(c.isins),
                                         "leis": sorted(c.leis),
                                         "organisation_numbers": sorted(c.orgnrs)}
                                        for c in contenders]}
            rec = cr.assemble(winner, reason, conf)
            return {"lei": rec.get("lei"), "isin": rec.get("isin"),
                    "company_name": rec.get("company_name"),
                    "legal_name": rec.get("legal_name"),
                    "ticker": rec.get("ticker"), "country": rec.get("country"),
                    "exchange": rec.get("exchange"),
                    "organisation_number": rec.get("organisation_number"),
                    "reporting_currency": rec.get("reporting_currency"),
                    "quote_currency": rec.get("currency"),
                    "fiscal_year_end": rec.get("fiscal_year_end"),
                    "fiscal_year_end_source": rec.get("fiscal_year_end_source"),
                    "identity_confidence": rec.get("confidence"),
                    "identity_basis": rec.get("confidence_basis"),
                    "identity_warnings": rec.get("warnings") or []}
        except Exception as exc:                      # noqa: BLE001
            return {"_error": "company_resolve failed: %s: %s"
                              % (type(exc).__name__, exc)}

    blob = cached("identity:%s:%s" % (query.lower(), country or ""),
                  IDENTITY_TTL, produce, offline=offline) if not refresh else produce()
    if not blob:
        return None
    if blob.get("_ambiguous"):
        # Two different failures wear the same label. Several contenders is a
        # real ambiguity and must refuse. NO contenders means only that the
        # Nordic registers do not know this name - a US ticker, say - so fall
        # through to the other resolvers rather than blocking on it.
        if blob.get("_candidates"):
            raise Ambiguous(blob["_ambiguous"], blob["_candidates"])
        return None
    if blob.get("_error"):
        return None
    blob = dict(blob)
    blob["identity_source"] = "company_resolve.py"
    blob["resolved_at"] = now_iso()
    return blob


def _identity_from_lei(lei, offline=False):
    def produce():
        try:
            cr = lazy("company_resolve")
            gl = cr.gleif(lei) or {}
        except Exception:                             # noqa: BLE001
            gl = {}
        return {"lei": lei, "isin": NA,
                "company_name": gl.get("legal_name") or lei,
                "legal_name": gl.get("legal_name") or NA,
                "ticker": NA, "country": gl.get("country") or NA,
                "organisation_number": gl.get("registered_as") or NA,
                "reporting_currency": NA, "fiscal_year_end": NA,
                "identity_confidence": 0.9 if gl.get("legal_name") else 0.5,
                "identity_warnings": ([] if gl.get("legal_name") else
                                      ["GLEIF was not reachable; only the LEI "
                                       "string itself is confirmed."])}
    blob = cached("identity-lei:" + lei, IDENTITY_TTL, produce, offline=offline)
    if not blob:
        return None
    blob = dict(blob)
    blob["identity_source"] = "LEI supplied directly, name from GLEIF"
    return blob


def _identity_via_esef_index(query, country):
    if not country:
        return None
    try:
        esef = lazy("esef_fundamentals")
        hits = esef.search_index(query, country.upper())
    except Exception:                                 # noqa: BLE001
        return None
    if not hits:
        return None
    if len(hits) > 1:
        raise Ambiguous("%d ESEF filers in %s match %r"
                        % (len(hits), country.upper(), query),
                        [{"company_name": h["name"], "leis": [h["lei"]],
                          "isins": [], "tickers": [],
                          "organisation_numbers": []} for h in hits])
    h = hits[0]
    return {"lei": h["lei"], "isin": NA, "company_name": h["name"],
            "legal_name": h["name"], "ticker": NA, "country": h.get("country"),
            "reporting_currency": NA, "fiscal_year_end": NA,
            "identity_source": "ESEF filing index (fallback: company_resolve "
                               "did not answer)",
            "identity_confidence": 0.7,
            "identity_warnings": ["Identity came from the ESEF index alone. "
                                  "Ticker, ISIN and share classes were NOT "
                                  "cross-checked."]}


# --------------------------------------------------------------------------
# Ledger read / write
# --------------------------------------------------------------------------

def ledger_path(key):
    return os.path.join(ledger_home(), key + ".json")


def read_ledger(key):
    led = _read_json(ledger_path(key))
    if led is None:
        return None
    if led.get("schema_version") != SCHEMA_VERSION:
        led = migrate(led)
    return led


def migrate(led):
    """No older formats exist yet; the hook does, so v2 has somewhere to land."""
    led["schema_version"] = SCHEMA_VERSION
    led.setdefault("migrated", []).append(
        {"at": now_iso(), "to": SCHEMA_VERSION, "note": "no field changes required"})
    return led


def new_ledger(key, identity, alias):
    return {"schema_version": SCHEMA_VERSION,
            "ledger_key": key,
            "identity": identity,
            "aliases": [alias] if alias else [],
            "created": now_iso(),
            "last_updated": now_iso(),
            "last_evaluated": None,
            "theses": [],
            "observations": {}}


def save_ledger(led):
    led["last_updated"] = now_iso()
    _write_json(ledger_path(led["ledger_key"]), led)
    idx = read_index()
    idx["companies"][led["ledger_key"]] = _index_entry(led)
    write_index(idx)


def load_or_create(query, country=None, offline=False, refresh=False, create=False):
    identity = resolve_identity(query, country=country, offline=offline, refresh=refresh)
    key = ledger_key(identity)
    led = read_ledger(key)
    if led is None:
        if not create:
            return None, key, identity
        led = new_ledger(key, identity, query.strip())
    else:
        if refresh:
            led["identity"] = identity
        alias = query.strip()
        if alias and alias.lower() not in [a.lower() for a in led.get("aliases", [])]:
            led.setdefault("aliases", []).append(alias)
    return led, key, identity


def next_thesis_id(led):
    used = {t["id"] for t in led.get("theses", [])}
    n = 1
    while ("T%d" % n) in used:
        n += 1
    return "T%d" % n


def find_thesis(led, tid):
    for t in led.get("theses", []):
        if t["id"].lower() == tid.lower():
            return t
    return None


# --------------------------------------------------------------------------
# Fundamentals -> FinancialFact series
# --------------------------------------------------------------------------

FRESHNESS = {"annual": "annual_financials", "quarterly": "interim_financials",
             "observed": "interim_financials"}


def fetch_esef(lei, filings=5, offline=False):
    """Merged ESEF fundamentals, with the harvest date of each filing.

    filings.xbrl.org publishes `date_added` (when it harvested the report), not
    the issuer's publication date. It is always LATER than publication, so
    using it as the publication date is conservative: HISTORICAL mode may drop
    a fact that was in fact already public, but it can never admit one that was
    not. That is the correct direction of error for a point-in-time test.
    """
    def produce():
        esef = lazy("esef_fundamentals")
        try:
            import urllib.parse
            params = {"filter[entity.identifier]": lei,
                      "page[size]": str(max(filings, 10)), "sort": "-period_end"}
            api = esef.FILINGS_API + "?" + urllib.parse.urlencode(params)
            listing = esef.get_json(api)
        except (SystemExit, Exception):               # noqa: BLE001
            return None
        rows = []
        for f in (listing.get("data") or []):
            a = f["attributes"]
            if not a.get("json_url"):
                continue
            rows.append({"period_end": a["period_end"], "json_url": a["json_url"],
                         "fxo_id": a.get("fxo_id"), "country": a.get("country"),
                         "date_added": (a.get("date_added") or "")[:10] or None,
                         "processed": (a.get("processed") or "")[:10] or None,
                         "errors": a.get("error_count")})
        rows = rows[:filings]
        if not rows:
            return None
        data, units = {}, set()
        for f in rows:
            try:
                doc = esef.get_json(esef.FILINGS_BASE + f["json_url"])
            except (SystemExit, Exception):           # noqa: BLE001
                continue
            facts = esef.extract(doc)
            for metric, names in esef.CONCEPTS.items():
                found = esef.pick(facts, names, metric in esef.DURATION)
                for period, (value, unit, concept) in found.items():
                    if unit:
                        units.add(unit)
                    data.setdefault(metric, {}).setdefault(period, []).append(
                        {"val": value, "unit": unit, "concept": concept,
                         "filing": f["fxo_id"], "filing_date": f["date_added"]})
        if not data:
            return None
        currencies = sorted({u.split(":", 1)[1] for u in units
                             if u.startswith("iso4217:") and "/" not in u})
        return {"basis": "annual", "source": "esef",
                "currency": "/".join(currencies) or None,
                "filings": rows, "data": data,
                "retrieved": now_iso()}

    return cached("esef:%s:%d" % (lei, filings), FUNDAMENTALS_TTL, produce, offline=offline)


# Plain-English names for the normalised raw line items, so "missing debt_lt"
# becomes something an analyst can act on.
RAW_LABEL = {
    "revenue": "revenue", "ebit": "operating income", "net_income": "net income",
    "gross_profit": "gross profit", "equity": "total equity",
    "total_assets": "total assets", "total_liabilities": "total liabilities",
    "cash": "cash and equivalents", "cfo": "cash from operations",
    "capex": "capital expenditure",
    "dnda": "depreciation and amortisation (ESEF Phase 1 rarely tags this)",
    "interest_expense": "interest expense",
    "debt_lt": "non-current borrowings (ESEF Phase 1 rarely tags this)",
    "debt_st": "current borrowings",
    "lease_liabilities": "IFRS 16 lease liabilities",
    "current_assets": "current assets", "current_liabilities": "current liabilities",
    "inventory": "inventories", "receivables": "trade receivables",
    "payables": "trade payables", "eps_diluted": "diluted EPS",
    "eps_basic": "basic EPS", "dividends_paid": "dividends paid",
    "tax": "income tax", "pretax_income": "profit before tax",
    "goodwill": "goodwill", "intangibles": "intangible assets", "cogs": "cost of sales",
}


def raw_label(key):
    return "%s [%s]" % (RAW_LABEL.get(key, key), key)


def _grade(rows, source, period, fkey):
    """Verification for one metric-period that several filings report.

    Graded by finfact.corroborate, not by counting sources: every ESEF
    filing shares origin_group 'issuer_filing', so the best available grade is
    CROSS-CHECKED. An issuer restating its own comparative is not independent
    confirmation of anything.

    corroborate returns CONFLICT when the values disagree. Here the cause is
    known - the later filing restated the earlier comparative - and SKILL.md
    SS1 says restatements win, so the newest value is used and the revision is
    written into the note instead of being downgraded to an unexplained
    conflict or, worse, silently dropped.
    """
    if len(rows) == 1:
        return Verification.SINGLE_SOURCE, None
    probes = []
    for r in rows:
        try:
            probes.append(FinancialFact(
                metric="probe", value=float(r["val"]), source=source,
                period_end=period, unit="pure",
                publication_date=r.get("filing_date"), freshness_key=fkey))
        except ValueError:
            continue
    if len(probes) < 2:
        return Verification.SINGLE_SOURCE, None
    grade, detail = finfact.corroborate(probes)
    if grade == Verification.CONFLICT:
        return Verification.CROSS_CHECKED, (
            "RESTATED between filings (%s); the most recently filed value is "
            "used, per 'restatements win'"
            % ", ".join("%s=%.6g" % (r["filing"], r["val"]) for r in rows))
    return grade, ("agrees across %d filings of the same issuer, which is one "
                   "origin - transcription confirmed, not the number"
                   % detail.get("n_facts", len(probes))
                   if grade == Verification.CROSS_CHECKED else None)


def raw_facts(bundle, currency):
    """{raw_key: {period_end: FinancialFact}} from a fetched ESEF bundle."""
    if not bundle:
        return {}, "annual"
    source = "esef"
    fkey = FRESHNESS.get(bundle.get("basis"), "annual_financials")
    out = {}
    for native, periods in bundle["data"].items():
        raw_key = ESEF_TO_RAW.get(native)
        if not raw_key:
            continue
        for period, rows in periods.items():
            rows = sorted(rows, key=lambda r: (r.get("filing_date") or ""), reverse=True)
            best = rows[0]
            verification, note = _grade(rows, source, period, fkey)
            unit = best.get("unit") or ""
            is_money = unit.startswith("iso4217:") and "/" not in unit
            cur = None
            if is_money:
                cur = unit.split(":", 1)[1]
            elif unit == "USD":
                cur = "USD"
            kind = "currency" if cur else ("per_share" if "/" in unit or "eps" in native
                                           else "pure")
            try:
                fact = mk_fact(
                    metric=raw_key, value=float(best["val"]), source=source,
                    period_end=period, unit=kind, currency=cur or (currency if kind == "currency" else None),
                    publication_date=best.get("filing_date"),
                    source_detail="%s / %s" % (best.get("concept"), best.get("filing")),
                    verification=verification, note=note, freshness_key=fkey,
                    publication_is_upper_bound=True)
            except ValueError:
                continue
            prev = out.setdefault(raw_key, {}).get(period)
            if prev is None:
                out[raw_key][period] = fact
    return out, bundle.get("basis", "annual")


def observed_facts(led, metric):
    """FinancialFacts a human entered with --observe."""
    rows = (led.get("observations") or {}).get(metric) or []
    out = {}
    for r in rows:
        try:
            fact = mk_fact(
                metric=metric, value=float(r["value"]), source=r.get("source", "interim_report"),
                period_end=r["period_end"],
                unit=M[metric].unit if metric in M else "pure",
                currency=r.get("currency"),
                publication_date=r.get("publication_date"),
                source_detail=r.get("note") or "analyst observation",
                verification=Verification.SINGLE_SOURCE,
                freshness_key=FRESHNESS.get(r.get("basis", "quarterly"),
                                            "interim_financials"),
                note="entered by hand with --observe on %s" % r.get("recorded", "?"))
        except ValueError:
            continue
        out[r["period_end"]] = fact
    return out


def _gap_ok(later, earlier, basis):
    a = datetime.date.fromisoformat(later)
    b = datetime.date.fromisoformat(earlier)
    days = (a - b).days
    if basis == "annual":
        return 300 <= days <= 430
    if basis == "quarterly":
        return 75 <= days <= 110
    return True


# Maps a breaker's persist_unit vocabulary onto the basis vocabulary _gap_ok
# understands, so "N consecutive quarters/years" can be checked for actual
# adjacency rather than just counted across whatever periods happen to exist.
PERSIST_UNIT_TO_GAP_BASIS = {"quarters": "quarterly", "years": "annual"}


def metric_series(mid, raw, basis, currency):
    """{period_end: FinancialFact} for one metric, computed from raw facts."""
    d = M[mid]
    if d.manual_reason:
        return {}, "manual metric: no machine-readable source"
    periods = sorted({p for k in d.needs for p in raw.get(k, {})})
    if not periods:
        missing = [k for k in d.needs if not raw.get(k)]
        return {}, ("not computable: these inputs are not tagged in the "
                    "filings: %s" % ", ".join(raw_label(k) for k in missing))
    out, blocked = {}, set()
    for i, period in enumerate(periods):
        vals, inputs, incomplete = {}, [], []
        ok = True
        for k in d.needs:
            f = raw.get(k, {}).get(period)
            if f is None:
                blocked.add(k)
                ok = False
                break
            vals[k] = f.value
            inputs.append(f)
        if not ok:
            continue
        for k in d.optional:
            f = raw.get(k, {}).get(period)
            if f is None:
                incomplete.append(k)
            else:
                vals[k] = f.value
                inputs.append(f)
        if d.yoy:
            if i == 0:
                continue
            prev_period = periods[i - 1]
            if not _gap_ok(period, prev_period, basis):
                continue
            pvals = {}
            ok = True
            for k in d.needs:
                f = raw.get(k, {}).get(prev_period)
                if f is None:
                    ok = False
                    break
                pvals[k] = f.value
                inputs.append(f)
            if not ok:
                continue
            try:
                value = d.fn(vals, pvals)
            except (TypeError, ZeroDivisionError):
                value = None
        else:
            try:
                value = d.fn(vals)
            except (TypeError, ZeroDivisionError):
                value = None
        if value is None:
            continue

        verification = _worst([f.verification for f in inputs])
        if incomplete:
            verification = Verification.INCOMPLETE
        notes = [n for n in (f.note for f in inputs) if n]
        if incomplete:
            notes.append("computed WITHOUT %s (not tagged) - the figure is "
                         "understated" % ", ".join(incomplete))
        if d.note:
            notes.append(d.note)
        pubs = [f.publication_date for f in inputs if f.publication_date]
        unit_kind = {PERCENT: "percent", RATIO: "pure", MULTIPLE: "pure",
                     MONEY: "currency", PERSHARE: "per_share",
                     COUNT: "pure"}[d.unit]
        try:
            fact = mk_fact(
                metric=mid, value=value, source=inputs[0].source, period_end=period,
                unit=unit_kind,
                currency=currency if unit_kind in ("currency", "per_share") else None,
                publication_date=max(pubs) if pubs else None,
                source_detail="; ".join(sorted({f.source_detail or "" for f in inputs}))[:400],
                verification=verification, note=" | ".join(notes)[:600] or None,
                freshness_key=FRESHNESS.get(basis, "annual_financials"),
                publication_is_upper_bound=any(
                    getattr(f, "publication_is_upper_bound", False) for f in inputs))
        except ValueError:
            continue
        out[period] = fact
    if not out:
        if blocked:
            return {}, ("not computable in any period: these inputs are not "
                        "tagged in the filings: %s"
                        % ", ".join(raw_label(k) for k in sorted(blocked)))
        return {}, "not computable in any period"
    return out, None


_VORDER = [Verification.CONFLICT, Verification.UNVERIFIED, Verification.INCOMPLETE,
           Verification.STALE, Verification.SINGLE_SOURCE, Verification.CROSS_CHECKED,
           Verification.VERIFIED]


def _worst(vs):
    for v in _VORDER:
        if v in vs:
            return v
    return Verification.SINGLE_SOURCE


class DataContext(object):
    """Everything one --evaluate run needs, fetched once."""

    def __init__(self, led, mode=Mode.CURRENT, as_of=None, offline=False):
        self.led = led
        self.mode = mode
        self.as_of = as_of or today().isoformat()
        self.offline = offline
        self.identity = led.get("identity", {})
        self.source = None
        self.basis = "annual"
        self.currency = None
        self.raw = {}
        self.errors = []
        self.rejected = []
        self._series = {}
        self._fetch()

    def _fetch(self):
        ident = self.identity
        bundle = None
        if ident.get("lei") and ident["lei"] != NA:
            bundle = fetch_esef(ident["lei"], offline=self.offline)
            if bundle is None:
                self.errors.append(
                    "SOURCE_UNAVAILABLE: no ESEF fundamentals for LEI %s "
                    "(filings.xbrl.org unreachable, or the issuer files "
                    "outside its coverage - Germany and Ireland are not "
                    "harvested)." % ident["lei"])
        if bundle is None:
            self.state = State.SOURCE_UNAVAILABLE
            return
        self.source = bundle["source"]
        self.basis = bundle.get("basis", "annual")
        self.currency = bundle.get("currency") or (
            ident.get("reporting_currency") if ident.get("reporting_currency") != NA else None)
        self.raw, self.basis = raw_facts(bundle, self.currency)
        if self.mode != Mode.CURRENT:
            # finfact owns the point-in-time gate; do not reimplement it here.
            for key, periods in list(self.raw.items()):
                kept, rejected = finfact.filter_as_of(
                    list(periods.values()), self.as_of, self.mode)
                self.raw[key] = {f.period_end.isoformat(): f for f in kept}
                for fact, state, reason in rejected:
                    self.rejected.append((key, fact.period_end.isoformat(),
                                          reason, state.value))
        self.state = State.OK if self.raw else State.DATA_NOT_AVAILABLE

    @property
    def available(self):
        return bool(self.raw)

    def series(self, mid):
        """(basis, {period: fact}, reason_if_empty, provenance)"""
        if mid in self._series:
            return self._series[mid]
        obs = observed_facts(self.led, mid)
        if self.mode != Mode.CURRENT:
            kept, _rej = finfact.filter_as_of(list(obs.values()), self.as_of,
                                              self.mode)
            obs = {f.period_end.isoformat(): f for f in kept}
        d = M.get(mid)
        if d is None:
            res = ("annual", {}, "unknown metric %r" % mid, "none")
        elif d.manual_reason:
            if obs:
                res = (_infer_basis(obs), obs, None, "observed")
            else:
                res = ("annual", {}, d.manual_reason, "manual")
        else:
            computed, reason = metric_series(mid, self.raw, self.basis, self.currency)
            if computed and obs:
                merged = dict(computed)
                extra = {p: f for p, f in obs.items() if p not in merged}
                merged.update(extra)
                res = (self.basis, merged, None,
                       "filings" + (" + observed" if extra else ""))
            elif computed:
                res = (self.basis, computed, None, "filings")
            elif obs:
                res = (_infer_basis(obs), obs, None, "observed")
            else:
                res = (self.basis, {}, reason, "none")
        self._series[mid] = res
        return res


def _infer_basis(facts):
    periods = sorted(facts)
    if len(periods) < 2:
        return "quarterly"
    gaps = [(datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days
            for a, b in zip(periods, periods[1:])]
    avg = sum(gaps) / len(gaps)
    if avg >= 300:
        return "annual"
    if avg >= 150:
        return "halves"
    return "quarterly"


# --------------------------------------------------------------------------
# Breaker evaluation
# --------------------------------------------------------------------------

TRIGGERED = "TRIGGERED"
PERSISTENCE_PENDING = "PERSISTENCE_PENDING"
NEAR = "NEAR"
CLEAR = "CLEAR"
UNKNOWN_OUT = "UNKNOWN"

# How close to a trigger counts as NEAR (a WARNING, not a breach). For a
# metric measured in percentage points or turns, the meaningful distance is
# absolute - 2pp from a margin trigger is close whether the trigger is 15% or
# 65%. A relative band would call 6pp "near" on a 65% threshold and 1.5pp
# "near" on a 15% one, which is backwards. The relative band is the fallback
# for metrics with no natural absolute scale (currency amounts, counts).
NEAR_BAND_REL = 0.10
NEAR_BAND_ABS = {PERCENT: 2.0, MULTIPLE: 0.25, RATIO: 0.15, MONEY: 0.0,
                 PERSHARE: 0.25, COUNT: 0.0}

# How coarse a periodicity is, on one scale, so a breaker written in QUARTERS
# can be checked against the periodicity the data actually has. Both
# vocabularies live here: the data's ("annual") and the breaker's ("years").
# "periods" means "whatever the data reports", so it never blocks.
BASIS_RANK = {"months": 0, "monthly": 0,
              "quarters": 1, "quarterly": 1,
              "halves": 2,
              "years": 3, "annual": 3,
              "periods": 0}


BASIS_WORD = {"annual": "years", "quarterly": "quarters", "halves": "half-years",
              "monthly": "months", "periods": "periods"}


def period_word(want, basis, n=2):
    word = want or BASIS_WORD.get(basis, "periods")
    return word if n != 1 else word.rstrip("s").replace("half-year", "half-year")


def compare(value, op, threshold):
    return {"lt": value < threshold, "le": value <= threshold,
            "gt": value > threshold, "ge": value >= threshold,
            "eq": value == threshold, "ne": value != threshold}[op]


def headroom(value, op, threshold):
    """Distance from the threshold in the SAFE direction.

    Positive = still safe. Negative = already on the wrong side.
    """
    if op in ("lt", "le"):
        gap = value - threshold
    elif op in ("gt", "ge"):
        gap = threshold - value
    else:
        gap = abs(value - threshold)
    rel = None if threshold == 0 else gap / abs(threshold)
    return gap, rel


def evaluate_clause(clause, ctx):
    d = M[clause.metric]
    basis, facts, reason, provenance = ctx.series(clause.metric)
    out = {"clause": clause.to_dict(), "outcome": UNKNOWN_OUT, "reason": None,
           "value": None, "period": None, "headroom": None, "headroom_pct": None,
           "series": [], "provenance": provenance, "basis": basis,
           "persistence_verifiable": None, "facts": []}

    if not facts:
        out["reason"] = reason or "no data"
        out["state"] = (State.DATA_NOT_AVAILABLE.value if provenance != "manual"
                        else State.UNVERIFIED_DATA.value)
        return out

    periods = sorted(facts)
    out["series"] = [{"period": p, "value": facts[p].value,
                      "confidence": round(facts[p].confidence, 2),
                      "verification": facts[p].verification.value,
                      "published": facts[p].publication_date.isoformat()
                      if facts[p].publication_date else None}
                     for p in periods[-6:]]
    latest = periods[-1]
    lf = facts[latest]
    out["value"] = lf.value
    out["period"] = latest
    out["facts"] = [lf.to_dict()]
    gap, rel = headroom(lf.value, clause.op, clause.threshold)
    out["headroom"] = round(gap, 4)
    out["headroom_pct"] = None if rel is None else round(100.0 * rel, 1)

    # Persistence has to be tested on the periodicity the breaker was written
    # for. An ANNUAL figure above the threshold does not prove that no three
    # consecutive QUARTERS were below it, so that case is UNKNOWN, not CLEAR.
    want = clause.persist_unit
    verifiable = True
    if clause.persistence > 1 and want:
        if BASIS_RANK.get(basis, 0) > BASIS_RANK.get(want, 0):
            verifiable = False
    out["persistence_verifiable"] = verifiable

    breached = [compare(facts[p].value, clause.op, clause.threshold)
                for p in periods]
    # "Consecutive" must mean adjacent in time, not merely adjacent in a list
    # that can have holes (fetched figures plus whatever was --observe'd).
    # Three breaching quarters with a missing quarter between two of them are
    # not three consecutive quarters - they are two runs separated by a gap
    # in the record, and only the true run counts toward persistence.
    gap_basis = PERSIST_UNIT_TO_GAP_BASIS.get(want)
    run = 0
    prev_period = None
    for p, b in zip(reversed(periods), reversed(breached)):
        if not b:
            break
        if prev_period is not None and gap_basis and not _gap_ok(prev_period, p, gap_basis):
            break
        run += 1
        prev_period = p
    out["consecutive_breaches"] = run
    out["required_run"] = clause.persistence

    if breached[-1]:
        if not verifiable:
            out["outcome"] = PERSISTENCE_PENDING
            out["reason"] = (
                "%s is %s in the latest %s period (%s), which breaches the "
                "threshold - but the breaker requires %d consecutive %s and only "
                "%s data exists. Persistence CANNOT be confirmed from a free "
                "source; read the interim reports and use --observe."
                % (clause.metric, fmt_value(d.unit, lf.value), basis, latest,
                   clause.persistence, want, basis))
        elif run >= clause.persistence:
            out["outcome"] = TRIGGERED
            out["reason"] = ("%s %s %s held for %d consecutive %s (through %s)"
                             % (clause.metric, OP_SYMBOL[clause.op],
                                fmt_value(d.unit, clause.threshold), run,
                                period_word(want, basis, run), latest))
        else:
            out["outcome"] = PERSISTENCE_PENDING
            out["reason"] = ("%s breached the threshold in %d of the required %d "
                             "consecutive %s (latest %s = %s)"
                             % (clause.metric, run, clause.persistence,
                                period_word(want, basis, clause.persistence),
                                latest, fmt_value(d.unit, lf.value)))
    else:
        near_abs = NEAR_BAND_ABS.get(d.unit, 0.0)
        if near_abs:
            is_near = 0 <= gap < near_abs
        else:
            is_near = rel is not None and 0 <= rel < NEAR_BAND_REL
        if is_near:
            out["outcome"] = NEAR
            out["reason"] = ("%s = %s at %s, only %s from the %s trigger"
                             % (clause.metric, fmt_value(d.unit, lf.value), latest,
                                fmt_value(d.unit, abs(gap)),
                                fmt_value(d.unit, clause.threshold)))
        elif not verifiable:
            out["outcome"] = UNKNOWN_OUT
            out["reason"] = (
                "%s = %s at %s is clear of the threshold on %s data, but the "
                "breaker requires %d consecutive %s. An annual figure cannot "
                "rule out a run of weak quarters - silence is not confirmation."
                % (clause.metric, fmt_value(d.unit, lf.value), latest, basis,
                   clause.persistence, want))
        else:
            out["outcome"] = CLEAR
            out["reason"] = ("%s = %s at %s, %s clear of the %s trigger"
                             % (clause.metric, fmt_value(d.unit, lf.value), latest,
                                fmt_value(d.unit, gap),
                                fmt_value(d.unit, clause.threshold)))
            if len(periods) < clause.persistence:
                out["short_history"] = True
                out["reason"] += (" (only %d %s on record against a %d-%s "
                                  "persistence window - the latest period is "
                                  "clear, but a full run has not yet been "
                                  "observed either way)"
                                  % (len(periods), period_word(want, basis,
                                                               len(periods)),
                                     clause.persistence,
                                     period_word(want, basis, 1)))

    # Direction of travel: is the headroom widening or narrowing?
    if len(periods) >= 2:
        pf = facts[periods[-2]]
        pgap, _prel = headroom(pf.value, clause.op, clause.threshold)
        out["previous_period"] = periods[-2]
        out["previous_value"] = pf.value
        out["previous_headroom"] = round(pgap, 4)
        out["headroom_direction"] = ("widening" if gap > pgap else
                                     "narrowing" if gap < pgap else "flat")
    return out


def evaluate_breaker(breaker, ctx):
    results = [evaluate_clause(c, ctx) for c in breaker.clauses]
    outs = [r["outcome"] for r in results]
    if breaker.logic == "OR":
        if TRIGGERED in outs:
            group = TRIGGERED
        elif PERSISTENCE_PENDING in outs:
            group = PERSISTENCE_PENDING
        elif UNKNOWN_OUT in outs:
            group = UNKNOWN_OUT
        elif NEAR in outs:
            group = NEAR
        else:
            group = CLEAR
    else:
        if all(o == TRIGGERED for o in outs):
            group = TRIGGERED
        elif CLEAR in outs:
            group = CLEAR              # one clear clause defeats an AND group
        elif UNKNOWN_OUT in outs:
            group = UNKNOWN_OUT
        elif PERSISTENCE_PENDING in outs or TRIGGERED in outs:
            group = PERSISTENCE_PENDING
        else:
            group = NEAR
    return {"breaker": breaker.to_dict(), "outcome": group, "clauses": results}


STATUS_FACTOR = {"CONFIRMED": 1.0, "IMPROVING": 0.92, "STABLE": 0.80,
                 "WARNING": 0.45, "BROKEN": 0.05, "UNKNOWN": 0.30}

ACTION = {"BROKEN": "EXIT_OR_REUNDERWRITE", "WARNING": "REDUCE_CONVICTION",
          "UNKNOWN": "INVESTIGATE", "STABLE": "MAINTAIN",
          "IMPROVING": "MAINTAIN", "CONFIRMED": "MAINTAIN_OR_ADD"}


def derive_status(breaker_results, ctx, evidence_ok):
    outs = [b["outcome"] for b in breaker_results]
    triggered = [b for b in breaker_results if b["outcome"] == TRIGGERED]
    reasons = []
    if TRIGGERED in outs:
        status = "BROKEN"
        reasons = [c["reason"] for b in triggered for c in b["clauses"]
                   if c["outcome"] == TRIGGERED]
    elif PERSISTENCE_PENDING in outs or NEAR in outs:
        status = "WARNING"
        reasons = [c["reason"] for b in breaker_results for c in b["clauses"]
                   if c["outcome"] in (PERSISTENCE_PENDING, NEAR)]
    elif UNKNOWN_OUT in outs or not evidence_ok:
        status = "UNKNOWN"
        reasons = [c["reason"] for b in breaker_results for c in b["clauses"]
                   if c["outcome"] == UNKNOWN_OUT]
        if not evidence_ok:
            reasons.append("the thesis's evidence could not be re-fetched; "
                           "silence is not confirmation")
    else:
        rels = [c["headroom_pct"] for b in breaker_results for c in b["clauses"]
                if c.get("headroom_pct") is not None]
        dirs = [c.get("headroom_direction") for b in breaker_results
                for c in b["clauses"] if c.get("headroom_direction")]
        deteriorating = any(d == "narrowing" for d in dirs)
        widening = bool(dirs) and all(d == "widening" for d in dirs)
        comfortable = bool(rels) and min(rels) >= 25.0
        if comfortable and not deteriorating:
            status = "CONFIRMED"
        elif widening:
            status = "IMPROVING"
        else:
            status = "STABLE"
        reasons = [c["reason"] for b in breaker_results for c in b["clauses"]
                   if c["outcome"] == CLEAR]
    return status, reasons


def evaluate_thesis(thesis, ctx):
    breakers = [Breaker.from_dict(b) for b in thesis.get("breakers", [])]
    results = [evaluate_breaker(b, ctx) for b in breakers]

    facts_used, key_state = [], {}
    evidence_ok = False
    for mid in thesis.get("key_metrics", []):
        basis, facts, reason, provenance = ctx.series(mid)
        if facts:
            evidence_ok = True
            latest = sorted(facts)[-1]
            key_state[mid] = facts[latest].to_dict()
            facts_used.append(facts[latest])
        else:
            key_state[mid] = {"metric": mid, "value": None,
                              "state": State.DATA_NOT_AVAILABLE.value,
                              "reason": reason}
    status, reasons = derive_status(results, ctx, evidence_ok)

    data_conf = (sum(f.confidence for f in facts_used) / len(facts_used)
                 if facts_used else 0.0)
    conf = round(data_conf * STATUS_FACTOR[status], 2)

    supporting, contradicting = [], []
    for b in results:
        for c in b["clauses"]:
            if not c.get("facts"):
                continue
            fact = dict(c["facts"][0])
            fact["against_breaker"] = c["clause"]["normalised"]
            fact["headroom"] = c.get("headroom")
            if c["outcome"] in (TRIGGERED, PERSISTENCE_PENDING, NEAR):
                contradicting.append(fact)
            elif c["outcome"] == CLEAR:
                supporting.append(fact)
    for mid, snap in key_state.items():
        if snap.get("value") is None:
            continue
        if any(f["metric"] == mid for f in supporting + contradicting):
            continue
        supporting.append(dict(snap, against_breaker=None))

    return {"status": status, "confidence": conf,
            "data_confidence": round(data_conf, 2),
            "reasons": reasons, "breaker_results": results,
            "key_metric_state": key_state,
            "supporting_evidence": supporting,
            "contradicting_evidence": contradicting,
            "evidence_refetched": evidence_ok,
            "triggered": [c["clause"]["normalised"]
                          for b in results for c in b["clauses"]
                          if c["outcome"] == TRIGGERED],
            "action": ACTION[status]}


def apply_evaluation(thesis, ev, ctx):
    """Write the result back and keep the audit trail.

    A HISTORICAL run is a back-test: it answers "what would this thesis have
    said in June 2024". It is recorded, and labelled, but it must NOT become
    the thesis's live status - otherwise asking a question about the past
    silently rewrites the present.
    """
    stamp = now_iso()
    if ctx.mode != Mode.CURRENT:
        thesis.setdefault("status_history", []).append({
            "at": stamp, "as_of": ctx.as_of, "mode": ctx.mode.value,
            "historical": True,
            "status": ev["status"], "previous_status": thesis.get("status"),
            "confidence": ev["confidence"],
            "data_confidence": ev["data_confidence"],
            "triggered": sorted(ev["triggered"]),
            "reason": "BACK-TEST as of %s (live status left untouched): %s"
                      % (ctx.as_of, "; ".join(ev["reasons"][:2])),
            "source": ctx.source, "evidence_refetched": ev["evidence_refetched"]})
        del thesis["status_history"][:-200]
        thesis["last_historical_evaluation"] = {
            "at": stamp, "as_of": ctx.as_of, "mode": ctx.mode.value,
            "status": ev["status"], "confidence": ev["confidence"],
            "triggered": sorted(ev["triggered"]),
            "breaker_results": ev["breaker_results"]}
        return False

    prev_status = thesis.get("status")
    prev_conf = thesis.get("confidence")
    thesis["status"] = ev["status"]
    thesis["confidence"] = ev["confidence"]
    thesis["data_confidence"] = ev["data_confidence"]
    thesis["as_of"] = ctx.as_of
    thesis["mode"] = ctx.mode.value
    thesis["last_updated"] = stamp
    thesis["last_evaluated"] = stamp
    thesis["evaluation_count"] = thesis.get("evaluation_count", 0) + 1
    thesis["supporting_evidence"] = ev["supporting_evidence"]
    thesis["contradicting_evidence"] = ev["contradicting_evidence"]
    thesis["key_metric_state"] = ev["key_metric_state"]
    thesis["action"] = ev["action"]
    thesis["last_evaluation"] = {
        "at": stamp, "as_of": ctx.as_of, "mode": ctx.mode.value,
        "source": ctx.source, "basis": ctx.basis,
        "breaker_results": ev["breaker_results"],
        "errors": ctx.errors}

    triggered = sorted(ev["triggered"])
    changed = (prev_status != ev["status"]
               or triggered != sorted(thesis.get("triggered_breakers", [])))
    thesis["triggered_breakers"] = triggered
    if changed or not thesis.get("status_history"):
        thesis["status_since"] = stamp
        thesis["status_confirmations"] = 1
        thesis.setdefault("status_history", []).append({
            "at": stamp, "as_of": ctx.as_of, "mode": ctx.mode.value,
            "status": ev["status"], "previous_status": prev_status,
            "confidence": ev["confidence"], "previous_confidence": prev_conf,
            "data_confidence": ev["data_confidence"],
            "triggered": triggered,
            "reason": "; ".join(ev["reasons"][:3]) or None,
            "source": ctx.source, "evidence_refetched": ev["evidence_refetched"]})
        del thesis["status_history"][:-200]
    else:
        thesis["status_confirmations"] = thesis.get("status_confirmations", 1) + 1
    return changed


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------

STATUS_ORDER = ["BROKEN", "WARNING", "UNKNOWN", "STABLE", "IMPROVING", "CONFIRMED"]
STATUS_MARK = {"BROKEN": "[BROKEN ]", "WARNING": "[WARNING]", "UNKNOWN": "[UNKNOWN]",
               "STABLE": "[STABLE ]", "IMPROVING": "[IMPROV.]",
               "CONFIRMED": "[CONFIRM]", None: "[NEW    ]"}
OUTCOME_MARK = {TRIGGERED: "!! TRIGGERED", PERSISTENCE_PENDING: " ~ PENDING  ",
                NEAR: " ~ NEAR     ", CLEAR: " . clear    ",
                UNKNOWN_OUT: " ? UNKNOWN  "}


def fmt_value(unit, v):
    if v is None:
        return "n/a"
    if unit == PERCENT:
        return "%.2f%%" % v
    if unit == MULTIPLE:
        return "%.2fx" % v
    if unit == RATIO:
        return "%.2f" % v
    if unit == PERSHARE:
        return "%.2f" % v
    if unit == COUNT:
        return "{:,.0f}".format(v)
    if abs(v) >= 1e6:
        return "{:,.0f}M".format(v / 1e6)
    return "{:,.2f}".format(v)


def wrap(text, width, indent=""):
    words, lines, cur = text.split(), [], indent
    for w in words:
        if len(cur) + len(w) + 1 > width and cur.strip():
            lines.append(cur.rstrip())
            cur = indent + w + " "
        else:
            cur += w + " "
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


def print_thesis(t, verbose=False, override=None):
    """override = (status, confidence, breaker_results, header) for a run whose
    result is deliberately NOT written to the thesis, i.e. a back-test."""
    if override:
        status, conf, results, header = override
        print("%s %s  conf %.2f   %s"
              % (STATUS_MARK.get(status), t["id"], conf or 0.0, header))
    else:
        print("%s %s  conf %.2f   %s"
              % (STATUS_MARK.get(t.get("status")), t["id"],
                 t.get("confidence") or 0.0,
                 "since %s" % (t.get("status_since") or t.get("created"))))
    for line in wrap(t["thesis"], 78, "          "):
        print(line)
    print("          metrics : %s" % ", ".join(t.get("key_metrics", [])))
    for b in t.get("breakers", []):
        print("          breaker : %s" % b["normalised"])
        if not b.get("machine_evaluable"):
            for c in b["clauses"]:
                if c.get("manual_reason"):
                    for line in wrap("MANUAL - " + c["manual_reason"], 78,
                                     "                      "):
                        print(line)
    last = ({"breaker_results": override[2]} if override
            else t.get("last_evaluation"))
    if last:
        for b in last.get("breaker_results", []):
            for c in b["clauses"]:
                print("          %s  %s" % (OUTCOME_MARK[c["outcome"]],
                                            c["clause"]["normalised"]))
                for line in wrap(c["reason"] or "", 76, "                        "):
                    print(line)
                if verbose and c.get("series"):
                    d = M[c["clause"]["metric"]]
                    trail = "  ".join("%s %s" % (s["period"][:7],
                                                 fmt_value(d.unit, s["value"]))
                                      for s in c["series"])
                    print("                        series: %s" % trail)
        if override:
            print("          BACK-TEST ONLY: the live status of %s remains %s. "
                  "Nothing above was written as the current view."
                  % (t["id"], t.get("status")))
        else:
            if t.get("action"):
                print("          action  : %s" % t["action"])
            print("          evaluated %s (as-of %s, %s, %d run%s)"
                  % (last["at"], last["as_of"], last["mode"],
                     t.get("evaluation_count", 1),
                     "" if t.get("evaluation_count", 1) == 1 else "s"))
    else:
        print("          never evaluated - run --evaluate")
    if t.get("notes"):
        for n in t["notes"]:
            for line in wrap("note: " + n, 78, "          "):
                print(line)
    if t.get("falsifiability_override"):
        print("          !! falsifiability warnings were overridden with --force: %s"
              % ", ".join(t["falsifiability_override"]))
    if not t.get("active", True):
        print("          RETIRED %s: %s" % (t.get("retired_at"), t.get("retired_reason")))
    print()


def print_header(led):
    ident = led["identity"]
    print("=" * 80)
    print("%s  |  ledger key %s" % (ident.get("company_name") or "?", led["ledger_key"]))
    bits = [("LEI", ident.get("lei")), ("ISIN", ident.get("isin")),
            ("ticker", ident.get("ticker")),
            ("reports in", ident.get("reporting_currency")),
            ("FY end", ident.get("fiscal_year_end"))]
    print("  " + "  |  ".join("%s %s" % (k, v) for k, v in bits
                              if v and v != NA))
    print("  identity from %s  |  file %s"
          % (ident.get("identity_source", "?"), ledger_path(led["ledger_key"])))
    print("=" * 80)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_add(args, led, key, identity):
    text = " ".join(args.add.split())
    breakers, errors = [], []
    for raw in args.breaker or []:
        try:
            breakers.append(parse_breaker(raw))
        except BreakerError as exc:
            errors.append(str(exc))

    metrics, unknown = [], []
    for m in args.metric or []:
        mid = resolve_metric(m)
        (metrics if mid else unknown).append(mid or m)
    for b in breakers:
        for c in b.clauses:
            if c.metric not in metrics:
                metrics.append(c.metric)

    hard, soft = falsifiability_report(text, metrics, breakers)
    for e in errors:
        hard.append(("BREAKER_UNPARSED", e))
    for u in unknown:
        hard.append(("UNKNOWN_METRIC",
                     "unknown metric %r - run --metrics for the list." % u))

    if hard or (soft and not args.force):
        payload = {"accepted": False, "thesis": text,
                   "hard_problems": [{"code": c, "message": m} for c, m in hard],
                   "soft_problems": [{"code": c, "message": m} for c, m in soft],
                   "overridable": not hard and bool(soft)}
        if args.as_json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print("THESIS REJECTED - not falsifiable as written.")
            print()
            for line in wrap(text, 78, "    "):
                print(line)
            print()
            for code, msg in hard:
                print("  [%s]" % code)
                for line in wrap(msg, 76, "      "):
                    print(line)
            for code, msg in soft:
                print("  [%s]" % code)
                for line in wrap(msg, 76, "      "):
                    print(line)
            print()
            if not hard and soft:
                print("  These are linguistic objections. If the sentence really is")
                print("  testable as written, re-run with --force; the override is")
                print("  recorded on the thesis.")
            print("  Nothing was written to the ledger.")
        return 5

    tid = args.id or next_thesis_id(led)
    if find_thesis(led, tid):
        print("A thesis %s already exists. Pick another --id." % tid)
        return 3
    stamp = now_iso()
    thesis = {
        "id": tid,
        "thesis": text,
        "created": stamp,
        "created_as_of": today().isoformat(),
        "last_updated": stamp,
        "as_of": today().isoformat(),
        "status": "UNKNOWN",
        "status_since": stamp,
        "status_confirmations": 0,
        "confidence": round(args.confidence, 2) if args.confidence is not None else None,
        "prior_confidence": round(args.confidence, 2) if args.confidence is not None else None,
        "key_metrics": metrics,
        "breakers": [b.to_dict() for b in breakers],
        "supporting_evidence": [],
        "contradicting_evidence": [],
        "notes": list(args.note or []),
        "active": True,
        "evaluation_count": 0,
        "status_history": [{"at": stamp, "as_of": today().isoformat(),
                            "mode": "CURRENT", "status": "UNKNOWN",
                            "previous_status": None,
                            "confidence": args.confidence,
                            "reason": "thesis created; not yet evaluated",
                            "triggered": []}],
    }
    if soft and args.force:
        thesis["falsifiability_override"] = [c for c, _ in soft]

    led.setdefault("theses", []).append(thesis)

    # Snapshot the evidence as it stands at creation, so "has the case changed"
    # has something to compare against.
    ctx = None
    if not args.offline:
        ctx = DataContext(led, mode=Mode.CURRENT, offline=args.offline)
        ev = evaluate_thesis(thesis, ctx)
        apply_evaluation(thesis, ev, ctx)
        thesis["evidence_at_creation"] = {
            "at": stamp,
            "supporting": ev["supporting_evidence"],
            "contradicting": ev["contradicting_evidence"]}
        led["last_evaluated"] = thesis["last_evaluated"]
        if ev["status"] == "BROKEN":
            thesis.setdefault("notes", []).append(
                "Created already BROKEN: a breaker was triggered by data that "
                "predates the thesis. Re-underwrite before relying on it.")
    save_ledger(led)

    if args.as_json:
        print(json.dumps({"accepted": True, "ledger_key": key,
                          "file": ledger_path(key), "thesis": thesis},
                         indent=2, ensure_ascii=False))
    else:
        print_header(led)
        print("THESIS %s ACCEPTED and stored." % tid)
        print()
        print_thesis(thesis, verbose=args.verbose)
        if ctx and ctx.errors:
            for e in ctx.errors:
                for line in wrap(e, 78, "  "):
                    print(line)
    return 0


def cmd_list(args, led):
    theses = [t for t in led.get("theses", []) if args.all_theses or t.get("active", True)]
    theses.sort(key=lambda t: (STATUS_ORDER.index(t.get("status"))
                               if t.get("status") in STATUS_ORDER else 9, t["id"]))
    if args.as_json:
        print(json.dumps({"ledger_key": led["ledger_key"],
                          "identity": led["identity"],
                          "file": ledger_path(led["ledger_key"]),
                          "last_evaluated": led.get("last_evaluated"),
                          "observations": led.get("observations", {}),
                          "theses": theses}, indent=2, ensure_ascii=False))
        return 0
    print_header(led)
    if not theses:
        print("No theses recorded. Add one with --add.")
        return 0
    counts = {}
    for t in theses:
        counts[t.get("status")] = counts.get(t.get("status"), 0) + 1
    print("%d thes%s: %s" % (len(theses), "is" if len(theses) == 1 else "es",
                             ", ".join("%s %s" % (v, k) for k, v in
                                       sorted(counts.items(), key=lambda kv: str(kv[0])))))
    print("last evaluated: %s" % (led.get("last_evaluated") or "never"))
    print()
    for t in theses:
        print_thesis(t, verbose=args.verbose)
    obs = led.get("observations") or {}
    if obs:
        print("Analyst observations on record (entered with --observe):")
        for mid, rows in sorted(obs.items()):
            for r in sorted(rows, key=lambda r: r["period_end"]):
                print("  %-26s %s  %s   %s (pub %s)"
                      % (mid, r["period_end"],
                         fmt_value(M[mid].unit if mid in M else RATIO, r["value"]),
                         r.get("source"), r.get("publication_date") or "?"))
        print()
    return 0


def cmd_evaluate(args, led):
    mode = Mode.HISTORICAL if args.as_of else Mode.CURRENT
    ctx = DataContext(led, mode=mode, as_of=args.as_of, offline=args.offline)
    targets = [t for t in led.get("theses", []) if t.get("active", True)]
    if args.thesis:
        targets = [t for t in targets if t["id"].lower() == args.thesis.lower()]
        if not targets:
            print("No active thesis %r in this ledger." % args.thesis)
            return 3
    if not targets:
        print("No active theses to evaluate.")
        return 3

    report, any_triggered, changes = [], False, []
    for t in targets:
        ev = evaluate_thesis(t, ctx)
        changed = apply_evaluation(t, ev, ctx)
        if changed:
            changes.append((t["id"], t["status_history"][-1]))
        if ev["triggered"]:
            any_triggered = True
        report.append((t, ev))
    led["last_evaluated"] = now_iso()
    save_ledger(led)

    if args.as_json:
        print(json.dumps({
            "ledger_key": led["ledger_key"],
            "company": led["identity"].get("company_name"),
            "identity": led["identity"],
            "evaluated_at": led["last_evaluated"],
            "as_of": ctx.as_of, "mode": ctx.mode.value,
            "data_source": ctx.source, "basis": ctx.basis,
            "currency": ctx.currency,
            "data_errors": ctx.errors,
            "facts_excluded_by_as_of": [{"metric": m, "period": p,
                                         "reason": why, "state": st}
                                        for m, p, why, st in ctx.rejected],
            "back_test": ctx.mode != Mode.CURRENT,
            "live_status_written": ctx.mode == Mode.CURRENT,
            "any_breaker_triggered": any_triggered,
            "triggered": [{"thesis": t["id"], "breaker": b}
                          for t, ev in report for b in ev["triggered"]],
            "status_changes": [{"thesis": tid, **h} for tid, h in changes],
            "theses": [{"id": t["id"], "thesis": t["thesis"],
                        "status": ev["status"],
                        "live_status": t.get("status"),
                        "previous_status": next(
                            (h["previous_status"] for tid, h in changes
                             if tid == t["id"]), ev["status"]),
                        "confidence": ev["confidence"],
                        "data_confidence": ev["data_confidence"],
                        "action": ev["action"],
                        "evidence_refetched": ev["evidence_refetched"],
                        "triggered": ev["triggered"],
                        "reasons": ev["reasons"],
                        "breaker_results": ev["breaker_results"],
                        "key_metric_state": ev["key_metric_state"],
                        "supporting_evidence": ev["supporting_evidence"],
                        "contradicting_evidence": ev["contradicting_evidence"]}
                       for t, ev in report]},
            indent=2, ensure_ascii=False, default=str))
        return 4 if any_triggered else 0

    print_header(led)
    print("RE-EVALUATION  %s   as-of %s   mode %s"
          % (led["last_evaluated"], ctx.as_of, ctx.mode.value))
    print("data: %s, %s basis%s"
          % (ctx.source or "NONE", ctx.basis,
             ", currency %s" % ctx.currency if ctx.currency else ""))
    if ctx.rejected and ctx.mode == Mode.HISTORICAL:
        print("point-in-time gate: %d facts dropped as published after %s"
              % (len(ctx.rejected), ctx.as_of))
    for e in ctx.errors:
        for line in wrap("!! " + e, 78, "   ")[:1] + wrap(e, 74, "   ")[1:]:
            print(line)
    print()
    for t, ev in report:
        if ctx.mode == Mode.CURRENT:
            print_thesis(t, verbose=args.verbose)
        else:
            print_thesis(t, verbose=args.verbose,
                         override=(ev["status"], ev["confidence"],
                                   ev["breaker_results"],
                                   "as it stood on %s" % ctx.as_of))
    if any_triggered:
        print("-" * 80)
        print("THESIS BREAKER TRIGGERED")
        for t, ev in report:
            for b in ev["triggered"]:
                print("  %s  %s" % (t["id"], b))
                for line in wrap(t["thesis"], 74, "        "):
                    print(line)
        print()
        print("  Conviction impact is machine-readable: exit code 4, and every")
        print("  --json payload carries any_breaker_triggered + per-thesis action.")
        print("-" * 80)
    if changes:
        print("Status changes recorded this run:")
        for tid, h in changes:
            print("  %s  %s -> %s   %s"
                  % (tid, h["previous_status"] or "NEW", h["status"], h["at"]))
        print()
    return 4 if any_triggered else 0


def cmd_history(args, led):
    theses = [t for t in led.get("theses", [])
              if (not args.thesis or t["id"].lower() == args.thesis.lower())]
    if not theses:
        print("No such thesis.")
        return 3
    if args.as_json:
        print(json.dumps({"ledger_key": led["ledger_key"],
                          "company": led["identity"].get("company_name"),
                          "history": [{"id": t["id"], "thesis": t["thesis"],
                                       "created": t["created"],
                                       "current_status": t.get("status"),
                                       "status_since": t.get("status_since"),
                                       "evaluations": t.get("evaluation_count", 0),
                                       "status_history": t.get("status_history", [])}
                                      for t in theses]},
                         indent=2, ensure_ascii=False))
        return 0
    print_header(led)
    for t in theses:
        print("%s  %s" % (t["id"], STATUS_MARK.get(t.get("status"))))
        for line in wrap(t["thesis"], 78, "    "):
            print(line)
        print("    created %s   %d evaluation(s)   status held since %s"
              % (t["created"], t.get("evaluation_count", 0),
                 t.get("status_since")))
        print()
        print("    %-22s %-10s %-10s %-6s %s"
              % ("when", "status", "was", "conf", "as-of / mode"))
        print("    " + "-" * 74)
        for h in t.get("status_history", []):
            print("    %-22s %-10s %-10s %-6s %s / %s"
                  % (h["at"], h["status"], h.get("previous_status") or "-",
                     ("%.2f" % h["confidence"]) if h.get("confidence") is not None else "-",
                     h.get("as_of"), h.get("mode")))
            if h.get("reason"):
                for line in wrap(h["reason"], 72, "        "):
                    print(line)
            for b in h.get("triggered", []):
                print("        TRIGGERED: %s" % b)
        if t.get("status_confirmations", 0) > 1:
            print("    (re-confirmed %d times since the last change; only changes "
                  "are recorded)" % (t["status_confirmations"] - 1))
        print()
    return 0


def cmd_all(args):
    idx = read_index()
    rows = []
    for key in sorted(idx["companies"]):
        led = read_ledger(key)
        if not led:
            continue
        counts, oldest = {}, None
        triggered = []
        for t in led.get("theses", []):
            if not t.get("active", True):
                continue
            counts[t.get("status")] = counts.get(t.get("status"), 0) + 1
            for b in t.get("triggered_breakers", []):
                triggered.append({"thesis": t["id"], "breaker": b})
            le = t.get("last_evaluated")
            if le and (oldest is None or le < oldest):
                oldest = le
        rows.append({"ledger_key": key,
                     "company": led["identity"].get("company_name"),
                     "lei": led["identity"].get("lei"),
                     "isin": led["identity"].get("isin"),
                     "theses": sum(counts.values()),
                     "status_counts": counts,
                     "worst_status": next((s for s in STATUS_ORDER if counts.get(s)), None),
                     "triggered": triggered,
                     "last_evaluated": led.get("last_evaluated"),
                     "oldest_thesis_evaluation": oldest,
                     "file": ledger_path(key)})
    rows.sort(key=lambda r: (STATUS_ORDER.index(r["worst_status"])
                             if r["worst_status"] in STATUS_ORDER else 9,
                             r["company"] or ""))
    if args.as_json:
        print(json.dumps({"ledger_home": ledger_home(),
                          "schema_version": SCHEMA_VERSION,
                          "companies": rows,
                          "any_breaker_triggered": any(r["triggered"] for r in rows)},
                         indent=2, ensure_ascii=False))
        return 4 if any(r["triggered"] for r in rows) else 0
    print("Thesis ledger: %s" % ledger_home())
    print()
    if not rows:
        print("Nothing tracked yet. Add a thesis with:")
        print('  thesis_ledger.py "Sandvik" --add "..." --metric ebit_margin '
              '--breaker "ebit_margin < 15% for 2 consecutive years"')
        return 0
    print("%-28s %-24s %-5s %s" % ("company", "key", "n", "status"))
    print("-" * 80)
    for r in rows:
        summary = "  ".join("%s:%d" % (k, v) for k, v in
                            sorted(r["status_counts"].items(),
                                   key=lambda kv: (STATUS_ORDER.index(kv[0])
                                                   if kv[0] in STATUS_ORDER else 9)))
        print("%-28.28s %-24.24s %-5d %s"
              % (r["company"] or "?", r["ledger_key"], r["theses"], summary))
        if r["triggered"]:
            for t in r["triggered"]:
                print("      !! THESIS BREAKER TRIGGERED  %s: %s"
                      % (t["thesis"], t["breaker"]))
        print("      last evaluated %s" % (r["last_evaluated"] or "never"))
    print()
    triggered_any = any(r["triggered"] for r in rows)
    if triggered_any:
        print("At least one breaker is live. Exit code 4.")
    return 4 if triggered_any else 0


OBS_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_ /]*?)\s*=\s*(-?\d+(?:[.,]\d+)?)\s*%?\s*"
                    r"@\s*(\d{4}-\d{2}-\d{2})\s*(?:;(.*))?$")


def cmd_observe(args, led):
    added = []
    for spec in args.observe:
        m = OBS_RE.match(spec)
        if not m:
            print("Could not parse --observe %r.\n"
                  "  Form: metric=value@YYYY-MM-DD[;source=interim_report]"
                  "[;pub=YYYY-MM-DD][;basis=quarterly][;note=...]" % spec)
            return 3
        mid = resolve_metric(m.group(1))
        if mid is None:
            print("Unknown metric %r in --observe. Run --metrics." % m.group(1).strip())
            return 3
        extras = {}
        for part in (m.group(4) or "").split(";"):
            if "=" in part:
                k, _, v = part.partition("=")
                extras[k.strip().lower()] = v.strip()
        source = extras.get("source", "interim_report")
        if source not in finfact.TIER:
            print("Unknown source %r. finfact.TIER knows: %s"
                  % (source, ", ".join(sorted(finfact.TIER))))
            return 3
        row = {"period_end": m.group(3), "value": float(m.group(2).replace(",", ".")),
               "source": source, "publication_date": extras.get("pub") or extras.get("published"),
               "basis": extras.get("basis", "quarterly"),
               "note": extras.get("note"), "currency": extras.get("currency"),
               "recorded": now_iso()}
        rows = led.setdefault("observations", {}).setdefault(mid, [])
        rows[:] = [r for r in rows if r["period_end"] != row["period_end"]]
        rows.append(row)
        rows.sort(key=lambda r: r["period_end"])
        added.append((mid, row))
    save_ledger(led)
    if args.as_json:
        print(json.dumps({"ledger_key": led["ledger_key"],
                          "recorded": [{"metric": mid, **row} for mid, row in added]},
                         indent=2, ensure_ascii=False))
    else:
        print("Recorded %d observation(s) in %s"
              % (len(added), ledger_path(led["ledger_key"])))
        for mid, row in added:
            print("  %-26s %s  %s  source=%s  published=%s"
                  % (mid, row["period_end"],
                     fmt_value(M[mid].unit, row["value"]), row["source"],
                     row["publication_date"] or "UNKNOWN (no point-in-time claim "
                                                "can be made for this figure)"))
        print()
        print("Re-run --evaluate: breakers on manual metrics can now be tested.")
    return 0


def cmd_retire(args, led):
    t = find_thesis(led, args.retire)
    if not t:
        print("No thesis %r." % args.retire)
        return 3
    t["active"] = False
    t["retired_at"] = now_iso()
    t["retired_reason"] = args.reason or "no reason given"
    t.setdefault("status_history", []).append(
        {"at": t["retired_at"], "status": "RETIRED", "previous_status": t.get("status"),
         "reason": t["retired_reason"], "triggered": [],
         "as_of": today().isoformat(), "mode": "CURRENT"})
    save_ledger(led)
    print("Retired %s. It stays in the file so the history remains auditable; "
          "--list --all-theses shows it." % t["id"])
    return 0


def cmd_metrics(args):
    groups = {"REPORTED": [], "DERIVED": [], "MANUAL": []}
    for mid, d in sorted(M.items()):
        groups[d.kind].append((mid, d))
    if args.as_json:
        print(json.dumps({"metrics": [
            {"id": mid, "label": d.label, "unit": d.unit, "kind": d.kind,
             "auto_evaluable": d.kind != "MANUAL",
             "inputs": list(d.needs) + list(d.optional),
             "yoy": d.yoy, "better": d.better,
             "manual_reason": d.manual_reason, "note": d.note}
            for mid, d in sorted(M.items())],
            "aliases": ALIASES}, indent=2, ensure_ascii=False))
        return 0
    print("Metrics the ledger can hold, and whether a breaker on them can be")
    print("re-tested WITHOUT a human.")
    print()
    for kind in ("REPORTED", "DERIVED", "MANUAL"):
        print("%s%s" % (kind, {
            "REPORTED": "  - a tagged line item; evaluated automatically",
            "DERIVED": "  - arithmetic on tagged line items; automatic WHERE the "
                       "inputs are tagged",
            "MANUAL": "  - no free machine-readable source; needs --observe"}[kind]))
        for mid, d in groups[kind]:
            print("  %-26s %-30.30s %s" % (mid, d.label, d.unit))
            if d.manual_reason:
                for line in wrap(d.manual_reason, 76, "        "):
                    print(line)
            elif d.note:
                for line in wrap(d.note, 76, "        "):
                    print(line)
        print()
    print("Aliases are accepted in --metric and inside breakers, e.g. "
          "'operating margin' -> ebit_margin.")
    return 0


# --------------------------------------------------------------------------
# Self-test: parser and status logic, offline and deterministic
# --------------------------------------------------------------------------

def _selftest():
    ok = 0
    b = parse_breaker("ebit_margin < 15% for 2 consecutive quarters")
    c = b.clauses[0]
    assert (c.metric, c.op, c.threshold, c.persistence, c.persist_unit) == \
        ("ebit_margin", "lt", 15.0, 2, "quarters"), c.to_dict()
    ok += 1

    b = parse_breaker("organic revenue growth < 3% for 3 consecutive quarters "
                      "OR EBIT margin < 15%")
    assert b.logic == "OR" and len(b.clauses) == 2
    assert b.clauses[0].metric == "organic_revenue_growth"
    assert b.clauses[1].metric == "ebit_margin"
    ok += 1

    b = parse_breaker("net debt / EBITDA > 3.0x")
    assert b.clauses[0].metric == "net_debt_ebitda" and b.clauses[0].threshold == 3.0
    ok += 1

    for phrase, op in (("falls below", "lt"), ("exceeds", "gt"),
                       ("at least", "ge"), ("at most", "le")):
        cl = parse_clause("roce %s 12%%" % phrase)
        assert cl.op == op and cl.metric == "roce", (phrase, cl.op)
    ok += 1

    for bad in ("margins weaken materially",
                "ebit_margin < 15% for 0 quarters",
                "unicorn_index < 4",
                "ebit_margin < 0.15"):
        try:
            parse_breaker(bad)
            raise AssertionError("should have rejected %r" % bad)
        except BreakerError:
            ok += 1

    hard, soft = falsifiability_report(
        "Sandvik is a quality compounder with a wide moat.", [], [])
    assert any(c == "NO_BREAKER" for c, _ in hard)
    assert any(c == "VAGUE" for c, _ in soft)
    ok += 1

    hard, soft = falsifiability_report(
        "Mining aftermarket revenue keeps group EBIT margin at or above 15% "
        "through the capex cycle.", ["ebit_margin"],
        [parse_breaker("ebit_margin < 15% for 2 consecutive years")])
    assert not hard and not soft, (hard, soft)
    ok += 1

    assert compare(14.99, "lt", 15.0) and not compare(15.01, "lt", 15.0)
    assert NEAR_BAND_ABS[PERCENT] == 2.0
    g, r = headroom(52.6, "lt", 40.0)
    assert abs(g - 12.6) < 1e-9 and abs(r - 0.315) < 0.001
    g2, r2 = headroom(3.4, "gt", 3.0)
    assert abs(g2 + 0.4) < 1e-9
    ok += 1

    def fake(outcomes):
        return [{"outcome": o, "clauses": [
            {"outcome": o, "reason": "x", "headroom_pct": hp,
             "headroom_direction": hd}]}
            for o, hp, hd in outcomes]

    assert derive_status(fake([(TRIGGERED, None, None)]), None, True)[0] == "BROKEN"
    assert derive_status(fake([(NEAR, 5.0, "narrowing")]), None, True)[0] == "WARNING"
    assert derive_status(fake([(UNKNOWN_OUT, None, None)]), None, True)[0] == "UNKNOWN"
    assert derive_status(fake([(CLEAR, 40.0, "widening")]), None, True)[0] == "CONFIRMED"
    assert derive_status(fake([(CLEAR, 12.0, "widening")]), None, True)[0] == "IMPROVING"
    assert derive_status(fake([(CLEAR, 12.0, "narrowing")]), None, True)[0] == "STABLE"
    # the non-negotiable: evidence that cannot be re-fetched is never STABLE
    assert derive_status(fake([(CLEAR, 12.0, "narrowing")]), None, False)[0] == "UNKNOWN"
    ok += 1

    # a fact published after the as-of date must not reach a breaker
    f = FinancialFact("ebit_margin", 14.99, "esef", "2024-12-31",
                      unit="percent", publication_date="2025-05-08",
                      freshness_key="annual_financials")
    assert f.is_available_as_of("2024-06-30")[0] is False
    assert f.is_available_as_of("2025-06-30")[0] is True
    ok += 1

    assert ledger_key({"lei": "5299008ZUAXN43LVZF54"}) == "LEI-5299008ZUAXN43LVZF54"
    assert ledger_key({"lei": NA, "isin": "SE0000667891"}) == "ISIN-SE0000667891"
    try:
        ledger_key({"lei": NA, "isin": NA, "company_name": "Volvo"})
        raise AssertionError("a display name must never become a ledger key")
    except Ambiguous:
        ok += 1

    print("thesis_ledger selftest: %d assertions passed" % ok)
    return 0


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("company", nargs="?",
                    help="European (Nordic/French ESEF) company name, ticker, "
                         "ISIN or LEI. Resolved through company_resolve.py; "
                         "the ledger is keyed on LEI/ISIN.")
    ap.add_argument("--add", metavar="THESIS",
                    help="add one falsifiable thesis sentence")
    ap.add_argument("--metric", action="append", metavar="ID",
                    help="measurable series that decides the thesis (repeatable)")
    ap.add_argument("--breaker", action="append", metavar="EXPR",
                    help='numeric invalidation trigger, e.g. "ebit_margin < 15%% '
                         'for 2 consecutive years" (repeatable)')
    ap.add_argument("--confidence", type=float, help="prior confidence 0-1")
    ap.add_argument("--note", action="append", help="free-text note (repeatable)")
    ap.add_argument("--id", help="thesis id to use instead of the next free one")
    ap.add_argument("--force", action="store_true",
                    help="override LINGUISTIC falsifiability objections only; "
                         "recorded on the thesis")
    ap.add_argument("--list", action="store_true", dest="do_list")
    ap.add_argument("--all-theses", action="store_true",
                    help="include retired theses in --list")
    ap.add_argument("--evaluate", action="store_true",
                    help="re-test every breaker against current data")
    ap.add_argument("--as-of", metavar="YYYY-MM-DD",
                    help="evaluate in HISTORICAL mode: facts published after this "
                         "date are excluded")
    ap.add_argument("--thesis", metavar="ID", help="restrict --evaluate/--history")
    ap.add_argument("--history", action="store_true", dest="do_history")
    ap.add_argument("--all", action="store_true", dest="do_all",
                    help="status summary for every tracked company")
    ap.add_argument("--observe", action="append", metavar="SPEC",
                    help="record a hand-read datapoint: "
                         "metric=value@YYYY-MM-DD[;source=..][;pub=..][;basis=..]")
    ap.add_argument("--retire", metavar="ID", help="retire a thesis (kept, not deleted)")
    ap.add_argument("--reason", help="reason for --retire")
    ap.add_argument("--metrics", action="store_true", dest="do_metrics",
                    help="list every metric and whether it can be auto-tested")
    ap.add_argument("--country", help="ISO-2 hint for identity resolution, e.g. SE")
    ap.add_argument("--refresh-identity", action="store_true",
                    help="re-resolve the company instead of trusting the ledger")
    ap.add_argument("--offline", action="store_true",
                    help="use only cached data; never touch the network")
    ap.add_argument("--verbose", action="store_true", help="show metric series")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()
    if args.do_metrics:
        return cmd_metrics(args)
    if args.do_all:
        return cmd_all(args)
    if not args.company:
        ap.error("give a company, or use --all / --metrics / --selftest")

    creating = bool(args.add)
    try:
        led, key, identity = load_or_create(
            args.company, country=args.country, offline=args.offline,
            refresh=args.refresh_identity, create=creating or bool(args.observe))
    except Ambiguous as exc:
        if args.as_json:
            print(json.dumps({"resolved": False, "query": args.company,
                              "reason": exc.reason,
                              "candidates": exc.candidates,
                              "state": State.COMPANY_IDENTITY_AMBIGUOUS.value},
                             indent=2, ensure_ascii=False))
        else:
            print("REFUSING TO OPEN A LEDGER FOR %r: %s" % (args.company, exc.reason))
            print()
            for c in exc.candidates:
                print("  %-40.40s %s" % (c.get("company_name"),
                                         " ".join(filter(None, [
                                             ",".join(c.get("tickers") or []),
                                             ",".join(c.get("isins") or []),
                                             ",".join(c.get("leis") or [])]))))
            print()
            print("  A thesis ledger keyed on the wrong entity is worse than no")
            print("  ledger. Re-run with the LEI, the ISIN or the full legal name.")
        return 2

    if led is None:
        print("No ledger for %r (resolved to %s)." % (args.company, key))
        print("Nothing has been tracked for this company yet. Create the first")
        print("thesis with --add.")
        return 3

    if args.observe:
        rc = cmd_observe(args, led)
        if rc or not (args.add or args.evaluate or args.do_list or args.do_history):
            return rc
    if args.retire:
        return cmd_retire(args, led)
    if args.add:
        return cmd_add(args, led, key, identity)
    if args.evaluate:
        return cmd_evaluate(args, led)
    if args.do_history:
        return cmd_history(args, led)
    return cmd_list(args, led)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
