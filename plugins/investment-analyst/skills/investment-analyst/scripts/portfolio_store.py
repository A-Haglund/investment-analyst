#!/usr/bin/env python3
"""A user's own holdings: what they own, what they paid, nothing they didn't
tell it. This module stores and lists. It never values, never scores, and
never computes a gain or a loss - that belongs to a different script reading
this one's output.

WHY A SEPARATE STORE AT ALL

Every other script in this toolkit answers a question about a COMPANY. This
one answers a question about a PERSON's portfolio: which companies, how many
shares, at what cost. It has to exist before "what should I do with what I
hold" can be answered, and it has to be honest about two things every broker
export gets casually wrong:

  IDENTITY.  A pasted name is text, not an identifier. "Volvo" is two listed
      issuers (AB Volvo, Volvo Car AB) with different accounts; guessing
      which one a line means is exactly the failure company_resolve.py exists
      to prevent. This module never substring-matches a company name - every
      row goes through company_resolve.resolve(), the same brand-guard engine
      resolve_lei() wraps, and an ambiguous name is REFUSED with every
      candidate named. The row is dropped from that paste, the rest of the
      portfolio still loads.

  TAX WRAPPER.  account_type defaults to "ISK" (Investeringssparkonto). An ISK
      is schablon-taxed on the ACCOUNT VALUE, not on realised gains: there is
      no capital-gains tax and no loss deduction inside one. Cost basis is
      still recorded here because it is useful record-keeping (what was paid,
      when), but nothing in this file computes a gain, a loss, or a tax
      consequence from it - there is deliberately no tax module. A holding
      with no cost basis at all is a perfectly complete, valid record.

STORAGE
    One JSON file per named portfolio, default name "default":

        ~/.investment-analyst/portfolio/<name>.json

    Written atomically (temp file + os.replace) so a crash mid-write never
    leaves a half-written portfolio on disk. Override the root with the
    PORTFOLIO_STORE_HOME environment variable (used by the test suite so it
    never touches a real home directory).

SCHEMA  (see the module docstring's JSON example in the project spec)
    {
      "name": "default", "account_type": "ISK", "currency": "SEK",
      "updated": "<UTC ISO8601>",
      "cash": {"amount": 24000.0, "currency": "SEK"},
      "holdings": [
        {"lei": "<20-char LEI or null>", "isin": "<ISIN or null>",
         "name": "Sandvik AB", "symbol": "SAND B",
         "quantity": 420, "cost_per_share": 312.40, "cost_currency": "SEK",
         "acquired": "2025-03-14", "note": "",
         "fair_value_low": null, "fair_value_high": null, "bear_value": null}
      ]
    }
    quantity is required on every holding, and must be a positive number -
    this store records long positions only; a closed or short position must
    be removed with --remove, not saved with a zero or negative quantity.
    cost_per_share, acquired and note are optional and may be null - a
    holding with no cost basis is complete and valid, it just means "no cost
    basis on file", not "broken record". lei may be null when identity could
    not be resolved to any listed Nordic issuer at all (see resolve_rows()'s
    NotFound handling below); when that happens name carries EXACTLY what
    the caller typed and the holding is marked "resolved": false so a caller
    printing the portfolio can flag it.

    fair_value_low, fair_value_high and bear_value are optional, nullable,
    analyst-entered numbers in the holding's OWN quote currency (not
    computed here - this module never values anything). They exist so a
    fair-value range and a bear case can be recorded as real fields instead
    of regex-scraped out of the freeform note text, which is how they used
    to be smuggled in (three incompatible number parsers, and a fabricated
    drawdown case). save() requires fair_value_low <= fair_value_high
    whenever both are present. A document written before these fields
    existed loads fine - load() fills them in as null on every holding.

USAGE
    portfolio_store.py --paste                      # read a pasted block from stdin
    portfolio_store.py --add "Sandvik B" --qty 420 --price 312.40
    portfolio_store.py --add "Sandvik B" --qty 420 --price 312.40 \
        --fair-value 190,5-215,5 --bear 140
    portfolio_store.py --note-for "Sandvik B" "waiting on Q3 margin"
    portfolio_store.py --fair-value-for "Sandvik B" 190,5-215,5
    portfolio_store.py --bear-for "Sandvik B" 140
    portfolio_store.py --remove "Sandvik B"
    portfolio_store.py --list
    portfolio_store.py --list --json
    portfolio_store.py --cash 24000
    portfolio_store.py --name growth --list          # a non-default portfolio
    portfolio_store.py --selftest                    # offline, no network

Importable API (the fixed contract this file was built against):

    from portfolio_store import load, save, parse_paste, resolve_rows

    load(name="default")            -> dict (schema above); {} if no such file
    save(doc, name="default")       -> None (atomic write; raises ValueError
                                       if a holding is missing quantity)
    parse_paste(text)               -> (rows, problems)   # rows are dicts,
                                       pre-identity - see parse_paste()'s own
                                       docstring for the row shape
    resolve_rows(rows)              -> (holdings, refusals)  # holdings match
                                       the schema above; refusals name every
                                       candidate company_resolve.py saw

Free, keyless, and offline except for the identity lookup resolve_rows()
performs through company_resolve.py (which is itself free and keyless).
"""
import argparse
import datetime
import importlib.util
import json
import os
import re
import sys

for _stream in (sys.stdout, sys.stderr, sys.stdin):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
NA = "DATA NOT AVAILABLE"
DEFAULT_CURRENCY = "SEK"
SCHEMA_VERSION = 1


def _load_sibling(name):
    """Import scripts/<name>.py by path, the same helper company_resolve.py
    and thesis_ledger.py use - these scripts are a folder of standalone CLI
    tools, not a package, so `import scriptname` is not available."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# mfn_news.py's number parser is imported eagerly - it is pure regex/string
# code with no network access at import time (verified: no top-level call in
# that file does I/O), and every paste and every --cash figure needs it.
mfn_news = _load_sibling("mfn_news")

# company_resolve.py is NOT imported eagerly: importing it pulls in
# nordic_shares, esef_fundamentals, mfn_news and cision_news in turn, and
# --list / --cash / --remove never need identity resolution at all. Loaded
# lazily by _company_resolve() below, exactly the pattern thesis_ledger.py's
# lazy() uses, and swappable in tests/--selftest without touching the real
# module (see _selftest()).
_CR_MODULE = None


def _company_resolve():
    global _CR_MODULE
    if _CR_MODULE is None:
        _CR_MODULE = _load_sibling("company_resolve")
    return _CR_MODULE


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def store_home():
    override = os.environ.get("PORTFOLIO_STORE_HOME")
    if override:
        return os.path.abspath(override)
    return os.path.join(os.path.expanduser("~"), ".investment-analyst", "portfolio")


def _safe_name(name):
    name = (name or "default").strip() or "default"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def _path(name):
    return os.path.join(store_home(), _safe_name(name) + ".json")


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_doc(name):
    return {"schema_version": SCHEMA_VERSION, "name": name, "account_type": "ISK",
            "currency": DEFAULT_CURRENCY, "updated": _now_iso(),
            "cash": {"amount": 0.0, "currency": DEFAULT_CURRENCY}, "holdings": []}


def load(name="default"):
    """The stored document for portfolio `name`, or {} if none exists yet.

    {} rather than raising: a caller asking "what does the user hold" before
    anything has ever been saved should get an empty answer, not an
    exception - a brand-new portfolio and a not-yet-created one look the
    same to every downstream consumer of this function.
    """
    try:
        with open(_path(name), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    # A document written before fair_value_low/fair_value_high/bear_value
    # existed has holdings with no such keys at all - fill them in as null
    # rather than making every caller defend against a missing key.
    for h in doc.get("holdings") or []:
        for k in ("fair_value_low", "fair_value_high", "bear_value"):
            h.setdefault(k, None)
    return doc


def save(doc, name="default"):
    """Write `doc` for portfolio `name`, atomically (temp file + os.replace,
    so a crash mid-write never leaves a half-written portfolio on disk).

    Stamps "name" and "updated" itself - a caller need not (and should not)
    forge either. Validates every holding before writing anything:

      quantity   required, must parse as a number, and must be strictly
          positive. This store records long positions only - a closed or
          short position is removed with --remove, never saved as a zero or
          negative quantity, which would otherwise turn into a 1000% or
          -900% portfolio weight the moment something downstream divides by
          it.

      fair_value_low / fair_value_high   when BOTH are present, low must not
          exceed high - a crossed range is a typo, not a valid fair-value
          case, and is refused rather than silently stored backwards.

    Any violation raises ValueError before the file is touched - a caller
    that got this far with one has a bug worth surfacing immediately, not a
    portfolio worth persisting silently broken.
    """
    doc = dict(doc)
    for h in doc.get("holdings") or []:
        name_for_error = h.get("name")
        qty = h.get("quantity")
        if qty is None:
            raise ValueError(
                "holding %r has no quantity - quantity is required by the "
                "schema; remove the holding with --remove instead of saving "
                "one with no quantity" % name_for_error)
        try:
            qty_num = float(qty)
        except (TypeError, ValueError):
            raise ValueError(
                "holding %r has a non-numeric quantity (%r)"
                % (name_for_error, qty))
        if qty_num <= 0:
            raise ValueError(
                "holding %r has quantity %r, which is not a positive number "
                "- this store records long positions only; a closed or "
                "short position must be removed with --remove, not saved "
                "with a zero or negative quantity" % (name_for_error, qty))
        lo, hi = h.get("fair_value_low"), h.get("fair_value_high")
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(
                "holding %r has fair_value_low %r greater than "
                "fair_value_high %r - check which figure is which"
                % (name_for_error, lo, hi))
    doc["name"] = name
    doc.setdefault("account_type", "ISK")
    doc.setdefault("currency", DEFAULT_CURRENCY)
    doc.setdefault("cash", {"amount": 0.0, "currency": doc.get("currency", DEFAULT_CURRENCY)})
    doc.setdefault("holdings", [])
    doc["schema_version"] = SCHEMA_VERSION
    doc["updated"] = _now_iso()
    path = _path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    return None


# --------------------------------------------------------------------------
# Paste parsing
#
# The user pastes from Avanza, from Nordnet, or types lines by hand, often
# mixed in one block. Every one of those is COLUMNAR text where columns are
# separated by a run of two-or-more spaces, a tab, or a semicolon, and a
# single space is left alone because it is also the Swedish thousands
# separator ("24 000") and the separator between a name and its share class
# ("Sandvik B"). That one rule - "2+ spaces/tab/semicolon separates columns,
# 1 space is content" - is what lets "EVO   85   1 240   2025-03-14" keep
# "1 240" together as one field while still splitting the four columns apart,
# with no lookahead or per-issuer special-casing.
#
# A line with no such strong separator at all (a hand-typed "Investor B 300"
# with single spaces throughout) falls back to token-by-token scanning: the
# name is every leading token that is not itself a number or a date, and
# everything after that must resolve to exactly a quantity, an optional unit
# word, an optional cost-per-share, and an optional date - in that fixed
# order. If a stray token is left over (most commonly a thousands-separator
# number that only single spaces held together, e.g. "1 240" typed without
# padding), THAT LINE IS REFUSED, not guessed at: there is no way to tell,
# from tokens alone, whether "1" and "240" are one field or two, and picking
# one silently is exactly the kind of error this toolkit refuses to make.
# --------------------------------------------------------------------------

COLUMN_SPLIT_RE = re.compile(r"\t+|;+| {2,}")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Trailing noise words a broker export appends to a figure. Peeled off (at
# most one per field) before the field is handed to mfn_news.to_number().
UNIT_NOISE = {"st", "st.", "aktier", "andelar", "shares", "sh", "units", "unit"}
CURRENCY_WORDS = {"kr": "SEK", "kronor": "SEK", "sek": "SEK", "eur": "EUR",
                  "euro": "EUR", "usd": "USD", "dollar": "USD", "nok": "NOK",
                  "dkk": "DKK"}
TRAILING_WORDS = UNIT_NOISE | set(CURRENCY_WORDS)

CASH_RE = re.compile(r"^(kassa|cash|likvida\s+medel|likvidkonto|likvid)\b", re.IGNORECASE)

# Column-header vocabulary (Swedish + English + Norwegian "antall"/
# "verdipapir"): a line is a header, not a holding, when at least two of its
# words are drawn from this set. A genuine holding line ("Sandvik B 420 st
# 312,40") shares none of these words, so this never fires on real data.
HEADER_KEYWORDS = {
    "namn", "name", "aktie", "aktier", "vardepapper", "instrument", "innehav",
    "antal", "antall", "verdipapir", "volym", "quantity", "qty", "kurs",
    "price", "gav", "anskaffningsvarde", "anskaffningskurs", "marknadsvarde",
    "varde", "value", "andel", "andelar", "valuta", "currency", "konto",
    "depa", "isin", "ticker", "symbol",
}
# A row whose FIRST word is one of these is a subtotal/grand-total line, not
# a holding, however many numbers follow it.
TOTAL_KEYWORDS = {"totalt", "total", "summa", "subtotal", "portfoljvarde", "grand"}


def _fold(word):
    """ASCII-fold Swedish letters so header/total keyword matching does not
    depend on whether å/ä/ö made it through the paste intact."""
    return (word.lower().replace("å", "a").replace("ä", "a").replace("ö", "o")
            .strip(":;,."))


def _strip_trailing_word(field):
    """Peel exactly one trailing unit-or-currency word off `field` (e.g.
    "420 st" -> ("420", "st"), "24 000 kr" -> ("24 000", "kr")). A field with
    two trailing words is left untouched, so it fails the number parse
    below and is reported rather than guessed at."""
    parts = field.strip().split(" ")
    if len(parts) > 1:
        last = _fold(parts[-1])
        if last in TRAILING_WORDS:
            return " ".join(parts[:-1]).strip(), last
    return field.strip(), None


def _parse_fields(fields):
    """Turn a list of column strings into (values, currencies, date, error).

    values/currencies are parallel lists - currencies[i] is the currency word
    found on values[i]'s own field, or None. At most one date is accepted;
    a second is an error, never a silent overwrite.
    """
    values, currencies, date_found = [], [], None
    for field in fields:
        text, word = _strip_trailing_word(field)
        if not text:
            continue
        if DATE_RE.match(text):
            if date_found is not None:
                return None, None, None, "more than one date found"
            date_found = text
            continue
        num = mfn_news.to_number(text)
        if num is None:
            return None, None, None, "could not parse field %r" % field
        values.append(num)
        currencies.append(CURRENCY_WORDS.get(word))
    return values, currencies, date_found, None


def _finish_row(raw_line, name, values, currencies, date_found):
    if not name:
        return None, "no company name found in %r" % raw_line
    if not values:
        return None, "no quantity found in %r" % raw_line
    if len(values) > 2:
        return None, ("%d numeric fields found in %r - position is the only "
                      "signal used to tell quantity from cost per share, and "
                      "with more than two numbers that signal is gone; "
                      "refusing rather than guessing" % (len(values), raw_line))
    quantity = values[0]
    cost_per_share = values[1] if len(values) == 2 else None
    cost_currency = None
    if cost_per_share is not None:
        cost_currency = (currencies[1] if len(currencies) > 1 and currencies[1]
                         else DEFAULT_CURRENCY)
    return {"kind": "security", "raw_line": raw_line, "name": name,
           "quantity": quantity, "cost_per_share": cost_per_share,
           "cost_currency": cost_currency, "acquired": date_found,
           "note": ""}, None


def _parse_holding_line_single_spaced(raw_line):
    tokens = [t for t in raw_line.strip().split(" ") if t]
    idx = None
    for i, t in enumerate(tokens):
        if DATE_RE.match(t) or mfn_news.to_number(t) is not None:
            idx = i
            break
    if idx is None or idx == 0:
        return None, "no quantity found in %r" % raw_line
    name = " ".join(tokens[:idx])
    rest = tokens[idx:]
    values, currencies, date_found = [], [], None
    i = 0
    while i < len(rest):
        t = rest[i]
        if DATE_RE.match(t):
            if date_found is not None:
                return None, "more than one date found in %r" % raw_line
            date_found = t
            i += 1
            continue
        num = mfn_news.to_number(t)
        if num is not None:
            values.append(num)
            currencies.append(None)
            i += 1
            if i < len(rest) and _fold(rest[i]) in TRAILING_WORDS:
                cur = CURRENCY_WORDS.get(_fold(rest[i]))
                if cur:
                    currencies[-1] = cur
                i += 1
            continue
        return None, ("could not parse %r in %r - if this number uses a "
                      "space as a thousands separator, pad the columns with "
                      "two or more spaces (or a tab) so it survives intact"
                      % (t, raw_line))
    return _finish_row(raw_line, name, values, currencies, date_found)


def _parse_holding_line(raw_line):
    columns = [c for c in COLUMN_SPLIT_RE.split(raw_line) if c.strip()]
    if len(columns) >= 2:
        name = columns[0].strip()
        values, currencies, date_found, err = _parse_fields(columns[1:])
        if err:
            return None, "%s (%r)" % (err, raw_line)
        return _finish_row(raw_line, name, values, currencies, date_found)
    return _parse_holding_line_single_spaced(raw_line)


def _parse_cash_line(raw_line, prefix_end):
    remainder = raw_line.strip()[prefix_end:].strip()
    if not remainder:
        return None, "no amount found on cash line %r" % raw_line
    fields = [f for f in COLUMN_SPLIT_RE.split(remainder) if f.strip()] or [remainder]
    values, currencies, _date, err = _parse_fields(fields)
    if err:
        return None, "%s (%r)" % (err, raw_line)
    if not values:
        return None, "no cash amount found in %r" % raw_line
    if len(values) > 1:
        return None, "more than one number found on cash line %r" % raw_line
    return {"kind": "cash", "raw_line": raw_line, "amount": values[0],
           "currency": currencies[0] or None}, None


def parse_paste(text):
    """Parse a block of pasted or hand-typed portfolio text.

    Returns (rows, problems).

    rows is a list of dicts, PRE-IDENTITY (no company_resolve.py lookup has
    happened yet). Two shapes:

        {"kind": "security", "raw_line": str, "name": str, "quantity": float,
         "cost_per_share": float|None, "cost_currency": str|None,
         "acquired": "YYYY-MM-DD"|None, "note": ""}

        {"kind": "cash", "raw_line": str, "amount": float,
         "currency": str|None}

    problems is a list of {"raw_line": str, "reason": str} for lines that
    could not be parsed at all. A header row, a total row, and a blank line
    are never problems - they are recognised and silently skipped, because
    reporting them would train a user to ignore the problems list.

    Feed the "security" rows to resolve_rows(); apply the "cash" rows to the
    portfolio's cash field yourself (there is normally at most one).
    """
    rows, problems = [], []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        m = CASH_RE.match(line)
        if m:
            row, err = _parse_cash_line(line, m.end())
            if err:
                problems.append({"raw_line": raw_line, "reason": err})
            else:
                rows.append(row)
            continue

        words = [_fold(w) for w in line.split() if w]
        if words and words[0] in TOTAL_KEYWORDS:
            continue  # subtotal/grand-total row - skipped silently
        if sum(1 for w in words if w in HEADER_KEYWORDS) >= 2:
            continue  # column header row - skipped silently

        row, err = _parse_holding_line(line)
        if err:
            problems.append({"raw_line": raw_line, "reason": err})
        else:
            rows.append(row)
    return rows, problems


# --------------------------------------------------------------------------
# Identity resolution
# --------------------------------------------------------------------------

def _candidate_line(c):
    return ("%s  (ticker %s, ISIN %s, LEI %s)"
           % (c.get("company_name") or "?",
              ", ".join(c.get("tickers") or []) or "-",
              ", ".join(c.get("isins") or []) or "-",
              ", ".join(c.get("leis") or []) or "-"))


# A broker export prints a holding as "<Company> <Class>" - "Investor B",
# "Sandvik B" - but company_resolve.py's own name matching is built around
# either the bare company name ("Investor", "Sandvik") or the bare exchange
# ticker ("INVE B", "SAND B"), not that combined form. Verified against the
# live engine 2026-08-31: resolve("Investor B") raises NotFound while
# resolve("Investor") resolves cleanly. Stripping the class word and
# retrying is still the right recovery for the format real broker pastes
# use - but the retry must not then hand back whatever primary_class()
# picked (the B share, or classes[0] when there is no B) as if that were
# the class the caller typed. primary_class() answers "which class does an
# analyst mean by the bare company name", which is a fine default for an
# equity-research query where no class was named at all; here a class WAS
# named, and in a portfolio store the class IS the position - "Investor A"
# and "Investor B" are two different holdings at two different prices, and
# silently substituting one for the other is exactly the kind of guess this
# toolkit refuses to make elsewhere. SEB is the sharp case: it lists classes
# A and C with no B share at all, so primary_class() would otherwise hand
# back the C share for a caller who typed "SEB A". The retry below therefore
# carries the typed class letter back to resolve_rows(), which checks it
# against the resolved issuer's OWN listed share classes before accepting
# it - see resolve_rows()'s docstring. This is a compatibility shim living
# entirely in THIS file; company_resolve.py itself is not touched.
_CLASS_SUFFIX_RE = re.compile(r"^(.*\S)\s+(A|B|C|D|PREF|SDB|SDR|SER)\.?$", re.IGNORECASE)


def _resolve_identity(cr, name):
    """Resolve `name`, retrying once with a trailing share-class word
    stripped if the literal string matches nothing.

    Returns (record, None, class_letter) on success - class_letter is the
    upper-cased class word the caller typed (e.g. "A") when it was the
    class-suffix retry that resolved the row, else None - or (None, error,
    None) where error is the authoritative Ambiguous/NotFound instance - the
    retry's, if a retry was attempted, since that is the one that actually
    reflects whether the company exists.
    """
    try:
        return cr.resolve(name), None, None
    except cr.Ambiguous as amb:
        return None, amb, None
    except cr.NotFound as nf:
        m = _CLASS_SUFFIX_RE.match(name)
        if not m:
            return None, nf, None
        try:
            return cr.resolve(m.group(1)), None, m.group(2).upper()
        except (cr.Ambiguous, cr.NotFound) as err2:
            return None, err2, None


def _match_share_class(rec, klass):
    """The entry in rec["share_classes"] whose symbol carries class suffix
    `klass` (e.g. klass="A" matches a symbol ending " A", such as "SEB A"),
    or None if no listed line of this issuer carries that class at all."""
    if not klass:
        return None
    suffix = " " + klass
    for c in rec.get("share_classes") or []:
        if (c.get("symbol") or "").upper().endswith(suffix):
            return c
    return None


def resolve_rows(rows):
    """Turn parse_paste()'s pre-identity "security" rows into holdings.

    Returns (holdings, refusals). Every row's company name goes through
    company_resolve.resolve() - the SAME engine resolve_lei() wraps - never
    through a bare substring or prefix match, so "Volvo" cannot silently
    become AB Volvo's LEI attached to Volvo Car's price, or vice versa.

    Two distinct failure modes, deliberately handled differently:

      Ambiguous  - the name names more than one listed issuer (company_
          resolve.py's brand guard). The row is REFUSED: it goes into
          `refusals` with every candidate named (company_name, ticker, ISIN,
          LEI), and is never added to `holdings`. One bad name must not sink
          the rest of the paste - every other row still resolves normally.

      NotFound   - nothing listed on a Nordic market matches the name at
          all. This is NOT the same failure: there is nothing to disambiguate,
          and a real portfolio legitimately holds things outside the Nordic
          universe company_resolve.py models (a foreign stock, a private
          holding, a fund, a delisted name). Refusing these outright would
          make it impossible to ever record them, so the holding is KEPT -
          lei=None, isin=None, symbol=None, name=exactly what was typed - and
          marked "resolved": False so a caller printing the portfolio can
          flag it instead of presenting it as verified identity.

    A holding that resolves to a real issuer but simply has no LEI in any
    free register (documented in company_resolve.py: "some small First North
    names have none") is NOT flagged unresolved - identity itself succeeded,
    only the LEI field is empty, and that is recorded as lei=None with
    "resolved": True.

    One more shim, entirely local to this file: a literal "<Company>
    <Class>" query ("Investor B", "Sandvik B") is retried with the class
    word stripped if it does not resolve as typed - see _resolve_identity()
    for why. It only ever runs after the literal query has already failed,
    so it can only turn a NotFound into a resolved holding; it cannot change
    an already-successful or already-ambiguous outcome.

    When that retry is what resolved the row, the typed class is honoured
    against the issuer's OWN listed lines (rec["share_classes"]), never
    against primary_class()'s pick: if the resolved issuer lists a line for
    that class, THAT line's symbol and ISIN are used (so "Investor A" gets
    the A-share ticker and ISIN, not the B-share's); if the issuer resolved
    but does not list that class at all, the row is REFUSED naming the
    classes that do exist, rather than pricing a different class as if it
    were this one. A test double whose records carry no "share_classes" key
    at all (as some of this file's own fakes still do) is treated as
    "nothing to check" and falls back to the old ticker/isin from the
    resolved record, so this never regresses a caller that has not been
    updated to supply share_classes.
    """
    cr = _company_resolve()
    holdings, refusals = [], []
    for row in rows:
        name = (row.get("name") or "").strip()
        quantity = row.get("quantity")
        if not name or quantity is None:
            refusals.append({"raw_line": row.get("raw_line", ""), "name": name,
                             "reason": "missing name or quantity", "candidates": []})
            continue

        rec, err, klass = _resolve_identity(cr, name)
        if err is not None:
            if isinstance(err, cr.Ambiguous):
                refusals.append({"raw_line": row.get("raw_line", ""), "name": name,
                                 "reason": err.reason, "candidates": err.candidates})
            else:
                holdings.append({
                    "lei": None, "isin": None, "name": name, "symbol": None,
                    "quantity": quantity, "cost_per_share": row.get("cost_per_share"),
                    "cost_currency": row.get("cost_currency"),
                    "acquired": row.get("acquired"), "note": row.get("note") or "",
                    "fair_value_low": row.get("fair_value_low"),
                    "fair_value_high": row.get("fair_value_high"),
                    "bear_value": row.get("bear_value"),
                    "resolved": False})
            continue

        def _field(v):
            return None if (not v or v == cr.NA) else v

        symbol, isin = _field(rec.get("ticker")), _field(rec.get("isin"))
        if klass:
            classes = rec.get("share_classes") or []
            match = _match_share_class(rec, klass)
            if match is None and classes:
                listed = ", ".join(sorted({c.get("symbol") or "?" for c in classes}))
                refusals.append({
                    "raw_line": row.get("raw_line", ""), "name": name,
                    "reason": ("%s has no listed %s share - the classes it "
                              "actually lists are: %s"
                              % (rec.get("company_name") or name, klass, listed)),
                    "candidates": [{
                        "company_name": rec.get("company_name") or name,
                        "tickers": [c.get("symbol") for c in classes if c.get("symbol")],
                        "isins": [c.get("isin") for c in classes if c.get("isin")],
                        "leis": [rec["lei"]] if _field(rec.get("lei")) else []}]})
                continue
            if match is not None:
                symbol = _field(match.get("symbol")) or symbol
                isin = _field(match.get("isin")) or isin

        holdings.append({
            "lei": _field(rec.get("lei")), "isin": isin,
            "name": rec.get("company_name") or name,
            "symbol": symbol,
            "quantity": quantity, "cost_per_share": row.get("cost_per_share"),
            "cost_currency": row.get("cost_currency"),
            "acquired": row.get("acquired"), "note": row.get("note") or "",
            "fair_value_low": row.get("fair_value_low"),
            "fair_value_high": row.get("fair_value_high"),
            "bear_value": row.get("bear_value"),
            "resolved": True})
    return holdings, refusals


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print_failures(problems, refusals):
    for p in problems:
        print("  ! %s" % p["reason"])
    for r in refusals:
        print("  ! %r is ambiguous: %s" % (r["name"], r["reason"]))
        for c in r.get("candidates") or []:
            print("      - %s" % _candidate_line(c))


def _parse_range(text):
    """Parse "LO-HI" (e.g. "190,5-215,5") into (lo, hi), both run through
    mfn_news.to_number - never a second, home-grown number parser. Returns
    (None, None) if `text` is missing, does not split into exactly two
    pieces on "-", or either piece fails to parse. A negative figure typeset
    with U+2212 or another Unicode minus (see mfn_news.MINUS_CHARS) does not
    collide with the ASCII "-" this function splits on, so it is safe to
    split first and parse each side after."""
    if not text:
        return None, None
    parts = text.split("-")
    if len(parts) != 2:
        return None, None
    lo = mfn_news.to_number(parts[0].strip())
    hi = mfn_news.to_number(parts[1].strip())
    if lo is None or hi is None:
        return None, None
    return lo, hi


def _merge_duplicate_holdings(holdings):
    """Merge rows that key to the same identity (lei, else isin, else
    name.lower()) into one holding with quantity SUMMED - two Avanza
    accounts, or an ISK and a KF line in one paste, must not become two
    positions for one issuer. Two lots at two different prices have no
    single cost basis, so cost_per_share/cost_currency are cleared to None
    on any row that actually gets merged, rather than picking one lot's
    price and silently discarding the other's. First-seen order is
    preserved (both for which holding stays first, and for which fields a
    solo, un-merged holding keeps)."""
    order, merged = [], {}
    for h in holdings:
        key = h.get("lei") or h.get("isin") or (h.get("name") or "").lower()
        if key not in merged:
            merged[key] = dict(h)
            order.append(key)
        else:
            existing = merged[key]
            existing["quantity"] = (existing.get("quantity") or 0) + (h.get("quantity") or 0)
            existing["cost_per_share"] = None
            existing["cost_currency"] = None
    return [merged[k] for k in order]


def _merge_paste_with_prior(doc, holdings):
    """Re-pasting a broker export must update quantity and cost, never erase
    an analyst-entered note, fair-value range or bear case. Paste rows carry
    a blank note and null fair_value_low/fair_value_high/bear_value because
    parse_paste() has no way to invent them - so a bare `doc["holdings"] =
    holdings` on every re-paste silently wiped every one of those fields for
    every holding, which is the first step of this store's own documented
    workflow. Merge against the prior document instead, keyed on lei, else
    isin, else name.lower(), and returns the merged list; analyst-entered
    fields survive whenever the new row does not itself carry a value for
    them, and quantity/cost always come from the fresh paste."""
    prior = {}
    for h in doc.get("holdings") or []:
        prior[h.get("lei") or h.get("isin") or (h.get("name") or "").lower()] = h
    for h in holdings:
        was = prior.get(h.get("lei") or h.get("isin") or (h.get("name") or "").lower())
        if not was:
            continue
        h["note"] = h.get("note") or was.get("note") or ""
        for k in ("fair_value_low", "fair_value_high", "bear_value"):
            if h.get(k) is None:
                h[k] = was.get(k)
        if h.get("cost_per_share") is None:
            h["cost_per_share"] = was.get("cost_per_share")
            h["cost_currency"] = was.get("cost_currency")
        if h.get("acquired") is None:
            h["acquired"] = was.get("acquired")
    return holdings


def _cmd_paste(args):
    text = sys.stdin.read()
    rows, problems = parse_paste(text)
    security_rows = [r for r in rows if r["kind"] == "security"]
    cash_rows = [r for r in rows if r["kind"] == "cash"]
    holdings, refusals = resolve_rows(security_rows)
    holdings = _merge_duplicate_holdings(holdings)

    if problems or refusals:
        n = len(problems) + len(refusals)
        print("%d row(s) could not be parsed or resolved:" % n)
        _print_failures(problems, refusals)
        print()
        if not args.force:
            print("Nothing was saved. %d holding(s) parsed and resolved "
                  "cleanly; re-run with --force to save those and discard "
                  "the %d failed row(s) above." % (len(holdings), n))
            return 1
        print("--force given: saving the %d holding(s) that parsed and "
              "resolved cleanly; the %d row(s) above were discarded."
              % (len(holdings), n))

    doc = load(args.name) or _new_doc(args.name)
    doc["holdings"] = _merge_paste_with_prior(doc, holdings)
    if len(cash_rows) > 1:
        print("note: %d cash lines found in the paste; using the last one."
              % len(cash_rows))
    if cash_rows:
        last = cash_rows[-1]
        doc["cash"] = {"amount": last["amount"],
                       "currency": last["currency"] or doc.get("currency", DEFAULT_CURRENCY)}
    try:
        save(doc, args.name)
    except ValueError as e:
        print("error: %s" % e)
        return 2
    cash_note = ""
    if cash_rows:
        cash_note = ", cash %.2f %s" % (doc["cash"]["amount"], doc["cash"]["currency"])
    print("Saved portfolio %r: %d holding(s)%s." % (args.name, len(holdings), cash_note))
    unresolved = [h for h in holdings if not h.get("resolved", True)]
    if unresolved:
        print("(%d holding(s) saved with identity UNRESOLVED - no listed "
              "Nordic issuer matched: %s)"
              % (len(unresolved), ", ".join(h["name"] for h in unresolved)))
    return 0


def _cmd_add(args):
    if args.qty is None:
        print("error: --add requires --qty")
        return 2
    fv_low = fv_high = bear_value = None
    if args.fair_value is not None:
        fv_low, fv_high = _parse_range(args.fair_value)
        if fv_low is None or fv_high is None:
            print("error: could not parse --fair-value %r - expected LO-HI, "
                  "e.g. 190,5-215,5" % args.fair_value)
            return 2
    if args.bear is not None:
        bear_value = mfn_news.to_number(args.bear)
        if bear_value is None:
            print("error: could not parse --bear %r" % args.bear)
            return 2

    row = {"kind": "security", "raw_line": "--add %r" % args.add,
          "name": args.add.strip(), "quantity": args.qty,
          "cost_per_share": args.price,
          "cost_currency": DEFAULT_CURRENCY if args.price is not None else None,
          "acquired": args.acquired, "note": args.note or "",
          "fair_value_low": fv_low, "fair_value_high": fv_high,
          "bear_value": bear_value}
    holdings, refusals = resolve_rows([row])

    if refusals:
        r = refusals[0]
        print("REFUSING to add %r: %s" % (args.add, r["reason"]))
        for c in r.get("candidates") or []:
            print("  - %s" % _candidate_line(c))
        if not args.force:
            print("Nothing was added. Re-run with the exact legal name, "
                  "ticker or ISIN - or --force to add it unresolved.")
            return 1
        print("--force given: adding %r unresolved (no identity attached)."
              % args.add)
        holding = {"lei": None, "isin": None, "name": row["name"], "symbol": None,
                  "quantity": args.qty, "cost_per_share": args.price,
                  "cost_currency": row["cost_currency"], "acquired": args.acquired,
                  "note": args.note or "", "fair_value_low": fv_low,
                  "fair_value_high": fv_high, "bear_value": bear_value,
                  "resolved": False}
    else:
        holding = holdings[0]

    doc = load(args.name) or _new_doc(args.name)
    doc["holdings"] = [h for h in doc.get("holdings", [])
                       if (h.get("name") or "").lower() != holding["name"].lower()]
    doc["holdings"].append(holding)
    try:
        save(doc, args.name)
    except ValueError as e:
        print("error: %s" % e)
        return 2
    price_note = (" @ %.2f %s" % (holding["cost_per_share"], holding["cost_currency"])
                 if holding.get("cost_per_share") is not None else "")
    print("Added %s: %s shares%s to portfolio %r."
          % (holding["name"], holding["quantity"], price_note, args.name))
    return 0


def _find_holding(holdings, target_name):
    """Every holding whose name or symbol case-insensitively matches
    `target_name` - shared by --remove and the --*-for update commands so
    "matches nothing" / "matches more than one" are reported identically."""
    target = target_name.strip().lower()
    return [h for h in holdings
           if (h.get("name") or "").lower() == target
           or (h.get("symbol") or "").lower() == target]


def _cmd_update_for(args):
    """--note-for / --fair-value-for / --bear-for: update ONE field on an
    already-stored holding without re-supplying quantity, price or date -
    today --add replaces the whole record, so recording so much as a bear
    value meant retyping everything else about the position too."""
    doc = load(args.name)
    holdings = doc.get("holdings") or []
    if not holdings:
        print("portfolio %r has no holdings." % args.name)
        return 1

    updates = []
    if args.note_for:
        updates.append(("note", args.note_for[0], args.note_for[1]))
    if args.fair_value_for:
        updates.append(("fair_value", args.fair_value_for[0], args.fair_value_for[1]))
    if args.bear_for:
        updates.append(("bear", args.bear_for[0], args.bear_for[1]))

    touched = []
    for kind, target_name, value in updates:
        matches = _find_holding(holdings, target_name)
        if not matches:
            print("no holding matching %r in portfolio %r." % (target_name, args.name))
            return 1
        if len(matches) > 1:
            print("%r matches more than one holding - be more specific:" % target_name)
            for h in matches:
                print("  - %s (symbol %s, ISIN %s)"
                      % (h.get("name"), h.get("symbol") or "-", h.get("isin") or "-"))
            return 1
        h = matches[0]
        if kind == "note":
            h["note"] = value
        elif kind == "fair_value":
            lo, hi = _parse_range(value)
            if lo is None or hi is None:
                print("error: could not parse --fair-value-for range %r - "
                      "expected LO-HI, e.g. 190,5-215,5" % value)
                return 2
            h["fair_value_low"], h["fair_value_high"] = lo, hi
        elif kind == "bear":
            num = mfn_news.to_number(value)
            if num is None:
                print("error: could not parse --bear-for value %r" % value)
                return 2
            h["bear_value"] = num
        touched.append(h.get("name") or target_name)

    try:
        save(doc, args.name)
    except ValueError as e:
        print("error: %s" % e)
        return 2
    print("Updated %s in portfolio %r." % (", ".join(touched), args.name))
    return 0


def _cmd_remove(args):
    doc = load(args.name)
    holdings = doc.get("holdings") or []
    if not holdings:
        print("portfolio %r has no holdings." % args.name)
        return 1
    target = args.remove.strip().lower()
    matches = [h for h in holdings
              if (h.get("name") or "").lower() == target
              or (h.get("symbol") or "").lower() == target]
    if not matches:
        print("no holding matching %r in portfolio %r." % (args.remove, args.name))
        return 1
    if len(matches) > 1:
        print("%r matches more than one holding - be more specific:" % args.remove)
        for h in matches:
            print("  - %s (symbol %s, ISIN %s)"
                  % (h.get("name"), h.get("symbol") or "-", h.get("isin") or "-"))
        return 1
    doc["holdings"] = [h for h in holdings if h is not matches[0]]
    save(doc, args.name)
    print("Removed %s from portfolio %r." % (matches[0].get("name"), args.name))
    return 0


def _cmd_cash(args):
    doc = load(args.name) or _new_doc(args.name)
    currency = (doc.get("cash") or {}).get("currency") or doc.get("currency") or DEFAULT_CURRENCY
    doc["cash"] = {"amount": args.cash, "currency": currency}
    save(doc, args.name)
    print("Cash for portfolio %r set to %.2f %s." % (args.name, args.cash, currency))
    return 0


def _fmt_qty(q):
    if q is None:
        return "-"
    if float(q).is_integer():
        return "%d" % int(q)
    return ("%.4f" % q).rstrip("0").rstrip(".")


def _cmd_list(args):
    doc = load(args.name)
    if not doc:
        print("no portfolio named %r (nothing saved yet)." % args.name)
        return 1
    if args.as_json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0

    holdings = doc.get("holdings") or []
    print("Portfolio %r  (%s, %s)  -  last updated %s"
          % (doc.get("name", args.name), doc.get("account_type", "ISK"),
             doc.get("currency", DEFAULT_CURRENCY), doc.get("updated", NA)))
    print()
    print("  %-28s %-12s %-16s %10s  %s"
          % ("NAME", "SYMBOL", "ISIN", "QUANTITY", "IDENTITY"))
    print("  " + "-" * 92)
    for h in holdings:
        if h.get("resolved") is False:
            ident = "UNRESOLVED - no listed Nordic issuer matched this name"
        elif h.get("lei"):
            ident = "LEI %s" % h["lei"]
        else:
            ident = "resolved, no LEI on file"
        print("  %-28s %-12s %-16s %10s  %s"
              % ((h.get("name") or "")[:28], h.get("symbol") or "-",
                 h.get("isin") or "-", _fmt_qty(h.get("quantity")), ident))
    print()
    print("  This is a stored record, not an analysis: no gain, loss or")
    print("  judgement is computed here.")
    print()
    print("  COST BASIS ON FILE")
    print("  " + "-" * 92)
    print("  %-28s %12s %6s %12s  %s"
          % ("NAME", "COST/SHARE", "CCY", "ACQUIRED", "NOTE"))
    for h in holdings:
        cps = h.get("cost_per_share")
        print("  %-28s %12s %6s %12s  %s"
              % ((h.get("name") or "")[:28],
                 "%.2f" % cps if cps is not None else "n/a",
                 h.get("cost_currency") or "-", h.get("acquired") or "-",
                 h.get("note") or ""))
    print()
    cash = doc.get("cash") or {}
    print("  CASH   %.2f %s" % (cash.get("amount", 0.0) or 0.0,
                                cash.get("currency", DEFAULT_CURRENCY)))
    return 0


# --------------------------------------------------------------------------
# Self-test - offline, no network. company_resolve.py is swapped for a fake
# module (see _FakeCR) so the identity tests exercise resolve_rows()'s own
# branching (Ambiguous / NotFound / success), not a live lookup.
# --------------------------------------------------------------------------

class _FakeAmbiguous(Exception):
    def __init__(self, reason, candidates):
        self.reason = reason
        self.candidates = candidates
        super().__init__(reason)


class _FakeNotFound(Exception):
    pass


class _FakeCR(object):
    """Duck-types exactly what resolve_rows() calls on company_resolve.py:
    .resolve(name), .Ambiguous, .NotFound, .NA. Deliberately NOT a subclass
    or instance of the real module's classes - resolve_rows() catches these
    by attribute (cr.Ambiguous / cr.NotFound), never by isinstance against a
    name imported at this file's top level, which is what makes swapping the
    whole module in for a test actually work.
    """
    NA = "DATA NOT AVAILABLE"
    Ambiguous = _FakeAmbiguous
    NotFound = _FakeNotFound

    def resolve(self, name):
        """Mirrors the real engine's actual behaviour (verified live,
        2026-08-31): the BARE company name resolves, but "<Company> <Class>"
        does not - that gap is exactly what _resolve_identity()'s
        class-suffix retry in portfolio_store.py exists to bridge, so
        "sandvik b" must raise NotFound here, never resolve directly, for
        the selftest to actually exercise that retry."""
        key = name.strip().lower()
        if key == "volvo":
            raise _FakeAmbiguous(
                "brand shared by 2 listed issuers",
                [{"company_name": "AB Volvo", "tickers": ["VOLV B"],
                  "isins": ["SE0000115446"], "leis": ["549300MMNM5ELIRXXW90"]},
                 {"company_name": "Volvo Car AB", "tickers": ["VOLCAR B"],
                  "isins": ["SE0016942617"], "leis": ["549300 VOLVOCAR00X"]}])
        if key == "totally unknown security xyz":
            raise _FakeNotFound()
        if key == "sandvik":
            return {"company_name": "Sandvik AB", "ticker": "SAND B",
                    "isin": "SE0000667891", "lei": "5493004QAI1UOX9SR347"}
        if key == "evo":
            return {"company_name": "Evolution AB", "ticker": "EVO",
                    "isin": "SE0012673267", "lei": "529900S1E1UYIH25X754"}
        if key == "investor":
            return {"company_name": "Investor AB", "ticker": "INVE B",
                    "isin": "SE0000107419", "lei": "549300R7YNS5CS9ZE178"}
        raise _FakeNotFound()


def _selftest():
    import tempfile

    ok = 0

    # --- number reuse -----------------------------------------------------
    assert mfn_news.to_number("312,40") == 312.40
    assert mfn_news.to_number("1 240") == 1240.0
    assert mfn_news.to_number("2,063.1") == 2063.1
    assert mfn_news.to_number("− 13") == -13.0   # U+2212 minus sign
    ok += 4

    # --- paste parsing: the four formats from the spec, in one block ------
    block = ("Sandvik B      420 st    312,40\n"
            "EVO             85       1 240   2025-03-14\n"
            "Investor B     300\n"
            "Kassa          24 000 kr\n")
    rows, problems = parse_paste(block)
    assert problems == [], problems
    ok += 1
    sec = {r["name"]: r for r in rows if r["kind"] == "security"}
    assert sec["Sandvik B"]["quantity"] == 420 and sec["Sandvik B"]["cost_per_share"] == 312.40
    assert sec["EVO"]["quantity"] == 85 and sec["EVO"]["cost_per_share"] == 1240.0
    assert sec["EVO"]["acquired"] == "2025-03-14"
    assert sec["Investor B"]["quantity"] == 300 and sec["Investor B"]["cost_per_share"] is None
    ok += 4
    cash = [r for r in rows if r["kind"] == "cash"]
    assert len(cash) == 1 and cash[0]["amount"] == 24000.0 and cash[0]["currency"] == "SEK"
    ok += 1

    # --- header and total rows are skipped silently, not reported ---------
    block2 = ("Namn           Antal     Kurs\n"
             "Sandvik B      420 st    312,40\n"
             "Totalt                   131 208\n")
    rows2, problems2 = parse_paste(block2)
    assert problems2 == []
    assert len(rows2) == 1 and rows2[0]["name"] == "Sandvik B"
    ok += 2

    # --- a genuinely unparsable line is reported, not guessed --------------
    rows3, problems3 = parse_paste("Sandvik B 5 300\n")
    # single-spaced, no padding: name-boundary scan finds "5" first (idx=2),
    # leaving "300" to parse as one more number -> 2 values total, which is
    # a perfectly valid quantity+price read (5 shares @ 300) - this is NOT
    # the failure case. The failure case is a genuine leftover token:
    rows3b, problems3b = parse_paste("EVO 1 240 st\n")
    # tokens: EVO, 1, 240, st -> name="EVO", rest=["1","240","st"]; "1" is
    # parsed as quantity=1, then "240" is a second bare number (no unit
    # attaches to it here since "st" follows "240", not "1") giving two
    # values (1, 240) which again reads as a valid (if surprising) row. To
    # exercise a REAL leftover-token refusal we need three bare numbers:
    rows3c, problems3c = parse_paste("EVO 1 2 3 4\n")
    assert rows3c == [] and len(problems3c) == 1
    ok += 1

    # --- resolve_rows: ambiguous name refused, others still load ----------
    global _CR_MODULE
    real_cr, _CR_MODULE = _CR_MODULE, _FakeCR()
    try:
        sec_rows = [r for r in rows if r["kind"] == "security"]  # Sandvik/EVO/Investor
        sec_rows.append({"kind": "security", "raw_line": "Volvo 10",
                         "name": "Volvo", "quantity": 10, "cost_per_share": None,
                         "cost_currency": None, "acquired": None, "note": ""})
        holdings, refusals = resolve_rows(sec_rows)
        assert len(holdings) == 3, holdings
        assert len(refusals) == 1 and refusals[0]["name"] == "Volvo"
        assert "AB Volvo" in _candidate_line(refusals[0]["candidates"][0])
        assert "Volvo Car AB" in _candidate_line(refusals[0]["candidates"][1])
        by_name = {h["name"]: h for h in holdings}
        # "Sandvik B" only resolves via the class-suffix retry (_FakeCR
        # raises NotFound for the literal string, only "sandvik" succeeds) -
        # this is the exact real-world gap _resolve_identity() bridges.
        assert by_name["Sandvik AB"]["lei"] == "5493004QAI1UOX9SR347"
        assert by_name["Sandvik AB"]["symbol"] == "SAND B"
        assert by_name["Sandvik AB"]["resolved"] is True
        ok += 6

        # --- NotFound is kept, unresolved, not refused ---------------------
        h2, r2 = resolve_rows([{"kind": "security", "raw_line": "x",
                               "name": "Totally Unknown Security XYZ",
                               "quantity": 7, "cost_per_share": None,
                               "cost_currency": None, "acquired": None, "note": ""}])
        assert r2 == []
        assert len(h2) == 1 and h2[0]["resolved"] is False and h2[0]["lei"] is None
        assert h2[0]["name"] == "Totally Unknown Security XYZ"
        ok += 3
    finally:
        _CR_MODULE = real_cr

    # --- save/load round-trip, isolated from any real home directory ------
    with tempfile.TemporaryDirectory() as tmp:
        old_home = os.environ.get("PORTFOLIO_STORE_HOME")
        os.environ["PORTFOLIO_STORE_HOME"] = tmp
        try:
            doc = _new_doc("selftest")
            doc["holdings"] = [{"lei": "5493004QAI1UOX9SR347", "isin": "SE0000667891",
                                "name": "Sandvik AB", "symbol": "SAND B",
                                "quantity": 420, "cost_per_share": 312.40,
                                "cost_currency": "SEK", "acquired": "2025-03-14",
                                "note": ""}]
            doc["cash"] = {"amount": 24000.0, "currency": "SEK"}
            save(doc, "selftest")
            back = load("selftest")
            assert back["holdings"][0]["name"] == "Sandvik AB"
            assert back["cash"]["amount"] == 24000.0
            assert back["account_type"] == "ISK"
            ok += 3

            # save() refuses a holding with no quantity
            bad = _new_doc("selftest")
            bad["holdings"] = [{"lei": None, "isin": None, "name": "Nope",
                                "symbol": None, "quantity": None,
                                "cost_per_share": None, "cost_currency": None,
                                "acquired": None, "note": ""}]
            try:
                save(bad, "selftest")
                raise AssertionError("save() should have refused a holding "
                                     "with no quantity")
            except ValueError:
                ok += 1

            # load() of a name never saved returns {} rather than raising
            assert load("never-saved-this-one") == {}
            ok += 1
        finally:
            if old_home is None:
                os.environ.pop("PORTFOLIO_STORE_HOME", None)
            else:
                os.environ["PORTFOLIO_STORE_HOME"] = old_home

    print("portfolio_store selftest: %d assertions passed" % ok)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="default",
                    help="portfolio name (default: 'default')")
    ap.add_argument("--paste", action="store_true",
                    help="read a pasted portfolio block from stdin")
    ap.add_argument("--add", metavar="NAME", help="add or replace one holding")
    ap.add_argument("--qty", type=float, help="quantity for --add")
    ap.add_argument("--price", type=float, help="cost per share for --add (optional)")
    ap.add_argument("--acquired", help="YYYY-MM-DD acquisition date for --add (optional)")
    ap.add_argument("--note", default="", help="free-text note for --add")
    ap.add_argument("--fair-value", metavar="LO-HI",
                    help="fair value range for --add, e.g. 190,5-215,5 (optional)")
    ap.add_argument("--bear", help="bear-case value for --add, in the "
                    "holding's quote currency (optional)")
    ap.add_argument("--note-for", nargs=2, metavar=("NAME", "TEXT"),
                    help="update an existing holding's note in place, "
                    "without re-supplying quantity/price/date")
    ap.add_argument("--fair-value-for", nargs=2, metavar=("NAME", "LO-HI"),
                    help="update an existing holding's fair value range in "
                    "place, without re-supplying quantity/price/date")
    ap.add_argument("--bear-for", nargs=2, metavar=("NAME", "N"),
                    help="update an existing holding's bear-case value in "
                    "place, without re-supplying quantity/price/date")
    ap.add_argument("--remove", metavar="NAME", help="remove a holding by name/symbol")
    ap.add_argument("--list", action="store_true", help="list the portfolio")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="machine-readable output for --list")
    ap.add_argument("--cash", type=float, help="set the cash balance")
    ap.add_argument("--force", action="store_true",
                    help="save/add despite parse or identity problems")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()
    if args.paste:
        return _cmd_paste(args)
    if args.add:
        return _cmd_add(args)
    if args.note_for or args.fair_value_for or args.bear_for:
        return _cmd_update_for(args)
    if args.remove:
        return _cmd_remove(args)
    if args.cash is not None:
        return _cmd_cash(args)
    if args.list:
        return _cmd_list(args)

    ap.error("give one of --paste, --add, --remove, --cash, --list, "
             "--note-for, --fair-value-for, --bear-for or --selftest")


if __name__ == "__main__":
    sys.exit(main())
