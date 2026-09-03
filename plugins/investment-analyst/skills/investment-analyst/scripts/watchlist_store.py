#!/usr/bin/env python3
"""A user's watchlist: issuers they follow but do not own - no quantity, no
cost basis, nothing this store would have no way to know. It stores and
lists, exactly like portfolio_store.py does for actual holdings, and for the
same reason it never values, never scores, and never computes a verdict.

WHY A SEPARATE STORE FROM portfolio_store.py

portfolio_store.py's schema is built around a POSITION: quantity is
required, and save() refuses a holding with none. A watchlist entry has no
quantity at all - it is a name the user is watching, not a name they hold -
so bolting "watched, qty=0" onto the portfolio schema would either violate
that store's own required-field contract or quietly special-case a zero
quantity everywhere the portfolio schema is consumed downstream (weights,
concentration, bear-case coverage - see portfolio_metrics.py). A separate
store with its own schema keeps "what I own" and "what I am watching"
honestly distinct instead of overloading one shape to mean two things.

IDENTITY is resolved exactly the way portfolio_store.py resolves it: every
name goes through company_resolve.resolve() - the same brand-guard engine
resolve_lei() wraps - never a bare substring or prefix match. "Volvo" is two
listed issuers (AB Volvo, Volvo Car AB); an ambiguous name is REFUSED, naming
every candidate, not guessed at. The resolution CODE below
(_resolve_identity / _match_share_class / _candidate_line, and the
class-suffix retry) is copied from portfolio_store.py rather than imported:
this is a folder of standalone CLI tools (see portfolio_store.py's own
module docstring), not a shared package, and portfolio_store.py exposes none
of this as a public contract - it is all leading-underscore, private to that
file. What is genuinely reused, verbatim, is the ENGINE this calls into
(company_resolve.py) and the exact call pattern portfolio_store.py uses on
it - not a second, differently-behaved identity resolver.

A name that resolves to nothing listed on a Nordic market (NotFound) is kept
anyway, unresolved (lei=None, isin=None, name=exactly what was typed,
"resolved": false) - a watchlist legitimately follows things outside
company_resolve.py's Nordic universe: a foreign name, a pre-IPO company, a
fund. Refusing those outright would make it impossible to ever watch them.

STORAGE
    One JSON file per named watchlist, default name "default":

        ~/.investment-analyst/watchlist/<name>.json

    Written atomically (temp file + os.replace), mirroring
    portfolio_store.py's own save(). Override the root with the
    WATCHLIST_STORE_HOME environment variable (used by the test suite so it
    never touches a real home directory - mirrors PORTFOLIO_STORE_HOME).

SCHEMA
    {
      "schema_version": 1, "name": "default", "updated": "<UTC ISO8601>",
      "entries": [
        {"lei": "<20-char LEI or null>", "isin": "<ISIN or null>",
         "name": "Beijer Ref AB", "symbol": "BEIJ B",
         "date_added": "2026-09-03",
         "why": "waiting for a margin recovery before entering",
         "target_price": 210.0, "target_currency": "SEK",
         "last_checked": null, "resolved": true}
      ]
    }
    name is required on every entry (save() refuses an entry with none) -
    it is always either the resolved company_name or exactly what the user
    typed, never blank. Every other field is optional and may be null: an
    entry with no target price and no note is a complete, valid record, it
    just means "watching this name, nothing more recorded yet".

    why is free text - why this name is on the list, not a fetched fact.
    target_price / target_currency are an optional analyst-entered trigger
    price or valuation threshold to watch for (ASSUMPTION, never computed
    here). last_checked is null until something stamps it (see --touch
    below) - a placeholder for a future monitoring sweep that has not been
    built yet; this store only carries the field, it does not schedule or
    run any check itself.

USAGE
    watchlist_store.py --add "Beijer Ref" --why "waiting for margin recovery"
    watchlist_store.py --add "Beijer Ref" --why "..." --target 210,50
    watchlist_store.py --remove "Beijer Ref"
    watchlist_store.py --touch "Beijer Ref"          # stamp last_checked=now
    watchlist_store.py --list
    watchlist_store.py --list --json
    watchlist_store.py --name growth --list          # a non-default watchlist
    watchlist_store.py --selftest                    # offline, no network

Importable API (the fixed contract this file is built against):

    from watchlist_store import load, save, resolve_entry

    load(name="default")        -> dict (schema above); {} if no such file
    save(doc, name="default")   -> None (atomic write; raises ValueError if
                                    an entry is missing a name)
    resolve_entry(name)         -> (entry, None) | (None, refusal)
                                    refusal names every candidate
                                    company_resolve.py saw, exactly like
                                    portfolio_store.resolve_rows()'s refusals

Free, keyless, and offline except for the identity lookup resolve_entry()
performs through company_resolve.py (itself free and keyless).
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
SCHEMA_VERSION = 1
DEFAULT_CURRENCY = "SEK"


def _load_sibling(name):
    """Import scripts/<name>.py by path - see portfolio_store.py's own
    identically-named helper; this is a folder of standalone CLI tools, not
    a package, so `import scriptname` is not available."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# mfn_news.py's number parser: pure regex/string, no network at import time -
# imported eagerly, the same way portfolio_store.py does, since --target
# needs it on essentially every --add.
mfn_news = _load_sibling("mfn_news")

# company_resolve.py is loaded lazily - --list/--remove/--touch never need
# identity resolution, and importing it pulls in nordic_shares,
# esef_fundamentals, mfn_news and cision_news in turn. Swappable in tests and
# --selftest without touching the real module - see _selftest() below.
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
    override = os.environ.get("WATCHLIST_STORE_HOME")
    if override:
        return os.path.abspath(override)
    return os.path.join(os.path.expanduser("~"), ".investment-analyst", "watchlist")


def _safe_name(name):
    name = (name or "default").strip() or "default"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def _path(name):
    return os.path.join(store_home(), _safe_name(name) + ".json")


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today():
    return datetime.date.today().isoformat()


def _new_doc(name):
    return {"schema_version": SCHEMA_VERSION, "name": name,
           "updated": _now_iso(), "entries": []}


def load(name="default"):
    """The stored document for watchlist `name`, or {} if none exists yet -
    same contract as portfolio_store.load(): a brand-new watchlist and a
    not-yet-created one look the same to every downstream caller."""
    try:
        with open(_path(name), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    # A document written before target_price/target_currency/last_checked
    # existed has entries with no such keys - fill them in as null rather
    # than making every caller defend against a missing key (mirrors
    # portfolio_store.load()'s same treatment of its own optional fields).
    for e in doc.get("entries") or []:
        for k in ("target_price", "target_currency", "last_checked"):
            e.setdefault(k, None)
        e.setdefault("why", "")
    return doc


def save(doc, name="default"):
    """Write `doc` for watchlist `name`, atomically (temp file + os.replace).

    Stamps "name" and "updated" itself. Refuses to write any entry with no
    name at all (ValueError) - name is the one field this schema always
    requires, since it is what every lookup (--remove, --touch, the
    duplicate-add merge) keys on when lei/isin are unknown.
    """
    doc = dict(doc)
    for e in doc.get("entries") or []:
        if not (e.get("name") or "").strip():
            raise ValueError(
                "a watchlist entry has no name - every entry must carry the "
                "name it was resolved (or typed) under")
    doc["name"] = name
    doc.setdefault("entries", [])
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
# Identity resolution - see the module docstring's "IDENTITY" section for
# why this is a copy of portfolio_store.py's private _resolve_identity /
# _match_share_class / _candidate_line, not an import of them.
# --------------------------------------------------------------------------

def _candidate_line(c):
    return ("%s  (ticker %s, ISIN %s, LEI %s)"
           % (c.get("company_name") or "?",
              ", ".join(c.get("tickers") or []) or "-",
              ", ".join(c.get("isins") or []) or "-",
              ", ".join(c.get("leis") or []) or "-"))


# See portfolio_store.py's own _CLASS_SUFFIX_RE for the full "why": a broker
# export (and a watchlist entry typed the same way) writes "Investor B", but
# company_resolve.py's own matching wants either the bare company name or
# the bare exchange ticker - not that combined form. Verified against the
# live engine 2026-08-31: resolve("Investor B") raises NotFound while
# resolve("Investor") resolves cleanly.
_CLASS_SUFFIX_RE = re.compile(r"^(.*\S)\s+(A|B|C|D|PREF|SDB|SDR|SER)\.?$", re.IGNORECASE)


def _resolve_identity(cr, name):
    """Resolve `name`, retrying once with a trailing share-class word
    stripped if the literal string matches nothing.

    Returns (record, None, class_letter) on success - class_letter is the
    upper-cased class word the caller typed (e.g. "A") when it was the
    class-suffix retry that resolved the row, else None - or (None, error,
    None) where error is the authoritative Ambiguous/NotFound instance."""
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
    `klass` (e.g. klass="A" matches a symbol ending " A"), or None if no
    listed line of this issuer carries that class at all."""
    if not klass:
        return None
    suffix = " " + klass
    for c in rec.get("share_classes") or []:
        if (c.get("symbol") or "").upper().endswith(suffix):
            return c
    return None


def resolve_entry(name):
    """Resolve one typed name to a watchlist identity dict, or a refusal.

    Returns (entry, None) on success, or (None, refusal) on an AMBIGUOUS
    name - refusal is {"name", "reason", "candidates"}, naming every
    candidate company_resolve.py saw, exactly like
    portfolio_store.resolve_rows()'s refusals.

    A name matching NOTHING listed (NotFound) is NOT a refusal: it comes
    back as a normal entry with lei=None, isin=None, name=exactly what was
    typed, and "resolved": False, so a caller can flag it without being
    unable to record it at all - see the module docstring's IDENTITY
    section.
    """
    name = (name or "").strip()
    if not name:
        return None, {"name": name, "reason": "no name given", "candidates": []}

    cr = _company_resolve()
    rec, err, klass = _resolve_identity(cr, name)
    if err is not None:
        if isinstance(err, cr.Ambiguous):
            return None, {"name": name, "reason": err.reason,
                         "candidates": err.candidates}
        return {"lei": None, "isin": None, "name": name, "symbol": None,
               "resolved": False}, None

    def _field(v):
        return None if (not v or v == cr.NA) else v

    symbol, isin = _field(rec.get("ticker")), _field(rec.get("isin"))
    if klass:
        classes = rec.get("share_classes") or []
        match = _match_share_class(rec, klass)
        if match is None and classes:
            listed = ", ".join(sorted({c.get("symbol") or "?" for c in classes}))
            return None, {
                "name": name,
                "reason": ("%s has no listed %s share - the classes it "
                          "actually lists are: %s"
                          % (rec.get("company_name") or name, klass, listed)),
                "candidates": [{
                    "company_name": rec.get("company_name") or name,
                    "tickers": [c.get("symbol") for c in classes if c.get("symbol")],
                    "isins": [c.get("isin") for c in classes if c.get("isin")],
                    "leis": [rec["lei"]] if _field(rec.get("lei")) else []}]}
        if match is not None:
            symbol = _field(match.get("symbol")) or symbol
            isin = _field(match.get("isin")) or isin

    return {"lei": _field(rec.get("lei")), "isin": isin,
           "name": rec.get("company_name") or name, "symbol": symbol,
           "resolved": True}, None


# --------------------------------------------------------------------------
# Duplicate handling: adding an already-watched name must UPDATE it, never
# silently drop what is already on file - the append-only principle this
# store follows wherever it costs nothing to follow it.
# --------------------------------------------------------------------------

def _entry_key(e):
    return e.get("lei") or e.get("isin") or (e.get("name") or "").lower()


def _merge_with_prior(doc, entry):
    """Re-adding an already-watched issuer must update why/target in place
    without erasing the ORIGINAL date_added or an existing why/target the
    new call did not itself supply - the same "field survives unless the
    fresh row actually carries a replacement" rule
    portfolio_store._merge_paste_with_prior enforces for holdings. Returns
    the merged entry; does not mutate `doc`."""
    key = _entry_key(entry)
    for prior in doc.get("entries") or []:
        if _entry_key(prior) == key:
            entry["date_added"] = prior.get("date_added") or entry.get("date_added")
            entry["why"] = entry.get("why") or prior.get("why") or ""
            if entry.get("target_price") is None:
                entry["target_price"] = prior.get("target_price")
                entry["target_currency"] = prior.get("target_currency")
            entry["last_checked"] = prior.get("last_checked")
            break
    return entry


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _find_entries(entries, target_name):
    target = (target_name or "").strip().lower()
    return [e for e in entries
           if (e.get("name") or "").lower() == target
           or (e.get("symbol") or "").lower() == target]


def _cmd_add(args):
    entry, refusal = resolve_entry(args.add)
    if refusal:
        print("REFUSING to add %r: %s" % (args.add, refusal["reason"]))
        for c in refusal.get("candidates") or []:
            print("  - %s" % _candidate_line(c))
        print("Nothing was added. Re-run with the exact legal name, ticker "
              "or ISIN.")
        return 1

    target_price = None
    if args.target is not None:
        target_price = mfn_news.to_number(args.target)
        if target_price is None:
            print("error: could not parse --target %r" % args.target)
            return 2

    entry["date_added"] = _today()
    entry["why"] = args.why or ""
    entry["target_price"] = target_price
    entry["target_currency"] = DEFAULT_CURRENCY if target_price is not None else None
    entry["last_checked"] = None

    doc = load(args.name) or _new_doc(args.name)
    entry = _merge_with_prior(doc, entry)
    key = _entry_key(entry)
    doc["entries"] = [e for e in doc.get("entries", []) if _entry_key(e) != key]
    doc["entries"].append(entry)
    try:
        save(doc, args.name)
    except ValueError as e:
        print("error: %s" % e)
        return 2
    target_note = (" (target %.2f %s)" % (entry["target_price"], entry["target_currency"])
                  if entry.get("target_price") is not None else "")
    print("Added %s to watchlist %r%s." % (entry["name"], args.name, target_note))
    if entry.get("resolved") is False:
        print("(added with identity UNRESOLVED - no listed Nordic issuer "
              "matched %r)" % entry["name"])
    return 0


def _cmd_remove(args):
    doc = load(args.name)
    entries = doc.get("entries") or []
    if not entries:
        print("watchlist %r has no entries." % args.name)
        return 1
    matches = _find_entries(entries, args.remove)
    if not matches:
        print("no entry matching %r in watchlist %r." % (args.remove, args.name))
        return 1
    if len(matches) > 1:
        print("%r matches more than one entry - be more specific:" % args.remove)
        for e in matches:
            print("  - %s (symbol %s, ISIN %s)"
                  % (e.get("name"), e.get("symbol") or "-", e.get("isin") or "-"))
        return 1
    doc["entries"] = [e for e in entries if e is not matches[0]]
    save(doc, args.name)
    print("Removed %s from watchlist %r." % (matches[0].get("name"), args.name))
    return 0


def _cmd_touch(args):
    """Stamp last_checked=now on one entry - the hook a future monitoring
    sweep uses to record "this name was looked at, and when". Nothing in
    this file schedules or performs that sweep; it only carries the field
    and lets it be set."""
    doc = load(args.name)
    entries = doc.get("entries") or []
    if not entries:
        print("watchlist %r has no entries." % args.name)
        return 1
    matches = _find_entries(entries, args.touch)
    if not matches:
        print("no entry matching %r in watchlist %r." % (args.touch, args.name))
        return 1
    if len(matches) > 1:
        print("%r matches more than one entry - be more specific:" % args.touch)
        for e in matches:
            print("  - %s (symbol %s, ISIN %s)"
                  % (e.get("name"), e.get("symbol") or "-", e.get("isin") or "-"))
        return 1
    matches[0]["last_checked"] = _now_iso()
    save(doc, args.name)
    print("Marked %s checked in watchlist %r at %s."
          % (matches[0].get("name"), args.name, matches[0]["last_checked"]))
    return 0


def _fmt_target(e):
    if e.get("target_price") is None:
        return "-"
    return "%.2f %s" % (e["target_price"], e.get("target_currency") or "")


def _cmd_list(args):
    doc = load(args.name)
    if not doc:
        print("no watchlist named %r (nothing saved yet)." % args.name)
        return 1
    if args.as_json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0

    entries = doc.get("entries") or []
    print("Watchlist %r  -  last updated %s"
          % (doc.get("name", args.name), doc.get("updated", "DATA NOT AVAILABLE")))
    print()
    print("  %-28s %-12s %-16s %-12s %10s  %-24s %s"
          % ("NAME", "SYMBOL", "ISIN", "ADDED", "TARGET", "LAST CHECKED", "WHY"))
    print("  " + "-" * 120)
    for e in entries:
        ident = "" if e.get("resolved", True) else "  [UNRESOLVED]"
        print("  %-28s %-12s %-16s %-12s %10s  %-24s %s%s"
              % ((e.get("name") or "")[:28], e.get("symbol") or "-",
                 e.get("isin") or "-", e.get("date_added") or "-",
                 _fmt_target(e), e.get("last_checked") or "never",
                 e.get("why") or "", ident))
    return 0


# --------------------------------------------------------------------------
# Self-test - offline, no network. company_resolve.py is swapped for a fake
# module, the same duck-typed fake portfolio_store.py's own selftest uses
# (.resolve, .Ambiguous, .NotFound, .NA).
# --------------------------------------------------------------------------

class _FakeAmbiguous(Exception):
    def __init__(self, reason, candidates):
        self.reason = reason
        self.candidates = candidates
        super().__init__(reason)


class _FakeNotFound(Exception):
    pass


class _FakeCR(object):
    NA = "DATA NOT AVAILABLE"
    Ambiguous = _FakeAmbiguous
    NotFound = _FakeNotFound

    def resolve(self, name):
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
        if key == "beijer ref":
            return {"company_name": "Beijer Ref AB", "ticker": "BEIJ B",
                    "isin": "SE0015810245", "lei": "5493004QAI1UOX9SR999"}
        if key == "investor":
            return {"company_name": "Investor AB", "ticker": "INVE B",
                    "isin": "SE0000107419", "lei": "549300R7YNS5CS9ZE178"}
        raise _FakeNotFound()


def _selftest():
    import tempfile

    ok = 0
    global _CR_MODULE
    real_cr, _CR_MODULE = _CR_MODULE, _FakeCR()
    try:
        # --- ambiguous name refused, every candidate named -----------------
        entry, refusal = resolve_entry("Volvo")
        assert entry is None
        assert refusal is not None
        joined = " ".join(_candidate_line(c) for c in refusal["candidates"])
        assert "AB Volvo" in joined and "Volvo Car AB" in joined
        ok += 3

        # --- NotFound is kept, unresolved, not refused ---------------------
        entry2, refusal2 = resolve_entry("Totally Unknown Security XYZ")
        assert refusal2 is None
        assert entry2["resolved"] is False and entry2["lei"] is None
        assert entry2["name"] == "Totally Unknown Security XYZ"
        ok += 3

        # --- class-suffix retry: literal "Investor B" fails in _FakeCR (only
        # the bare "investor" key resolves - mirrors the live engine's own
        # behaviour, see portfolio_store.py's identical fake), so this only
        # succeeds if _resolve_identity()'s strip-and-retry actually runs ---
        entry3, refusal3 = resolve_entry("Investor B")
        assert refusal3 is None
        assert entry3["name"] == "Investor AB" and entry3["symbol"] == "INVE B"
        assert entry3["resolved"] is True
        ok += 3
    finally:
        _CR_MODULE = real_cr

    # --- save/load round-trip, isolated from any real home directory ------
    with tempfile.TemporaryDirectory() as tmp:
        old_home = os.environ.get("WATCHLIST_STORE_HOME")
        os.environ["WATCHLIST_STORE_HOME"] = tmp
        try:
            doc = _new_doc("selftest")
            doc["entries"] = [{"lei": "5493004QAI1UOX9SR999", "isin": "SE0015810245",
                              "name": "Beijer Ref AB", "symbol": "BEIJ B",
                              "date_added": "2026-09-01", "why": "margin recovery",
                              "target_price": 210.0, "target_currency": "SEK",
                              "last_checked": None}]
            save(doc, "selftest")
            back = load("selftest")
            assert back["entries"][0]["name"] == "Beijer Ref AB"
            assert back["schema_version"] == SCHEMA_VERSION
            ok += 2

            # save() refuses an entry with no name
            bad = _new_doc("selftest")
            bad["entries"] = [{"lei": None, "isin": None, "name": "",
                              "symbol": None}]
            try:
                save(bad, "selftest")
                raise AssertionError("save() should have refused a nameless entry")
            except ValueError:
                ok += 1

            # load() of a name never saved returns {} rather than raising
            assert load("never-saved-this-one") == {}
            ok += 1

            # --- duplicate add is idempotent: same identity, one entry ----
            # (_CR_MODULE is already declared global at the top of this
            # function; repeating it after the name has been used is a
            # SyntaxError that ast.parse does not report, only compile does.)
            real_cr2, _CR_MODULE = _CR_MODULE, _FakeCR()
            try:
                buf_doc = _new_doc("dupe")
                save(buf_doc, "dupe")
                args1 = argparse.Namespace(add="Beijer Ref", why="first look",
                                          target=None, name="dupe")
                _cmd_add(args1)
                args2 = argparse.Namespace(add="Beijer Ref", why="",
                                          target="210,5", name="dupe")
                _cmd_add(args2)
                d = load("dupe")
                assert len(d["entries"]) == 1, d["entries"]
                e = d["entries"][0]
                # second add supplied no --why, so the first add's why
                # survives; it DID supply --target, so target is updated
                assert e["why"] == "first look", e
                assert e["target_price"] == 210.5, e
                ok += 3

                # --- removal (by the RESOLVED name - "Beijer Ref" the typed
                # query is not what got stored once identity resolved it) --
                rm_args = argparse.Namespace(remove="Beijer Ref AB", name="dupe")
                code = _cmd_remove(rm_args)
                assert code == 0
                assert load("dupe")["entries"] == []
                ok += 2
            finally:
                _CR_MODULE = real_cr2
        finally:
            if old_home is None:
                os.environ.pop("WATCHLIST_STORE_HOME", None)
            else:
                os.environ["WATCHLIST_STORE_HOME"] = old_home

    print("watchlist_store selftest: %d assertions passed" % ok)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="default",
                    help="watchlist name (default: 'default')")
    ap.add_argument("--add", metavar="NAME", help="add or update one watchlist entry")
    ap.add_argument("--why", default="", help="free-text reason for --add")
    ap.add_argument("--target", help="target price or valuation threshold for "
                    "--add, in the issuer's quote currency (optional)")
    ap.add_argument("--remove", metavar="NAME", help="remove an entry by name/symbol")
    ap.add_argument("--touch", metavar="NAME",
                    help="stamp last_checked=now on an entry by name/symbol")
    ap.add_argument("--list", action="store_true", help="list the watchlist")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="machine-readable output for --list")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()
    if args.add:
        return _cmd_add(args)
    if args.remove:
        return _cmd_remove(args)
    if args.touch:
        return _cmd_touch(args)
    if args.list:
        return _cmd_list(args)

    ap.error("give one of --add, --remove, --touch, --list or --selftest")


if __name__ == "__main__":
    sys.exit(main())
