#!/usr/bin/env python3
"""research_delta.py - "what changed since last time?", answered mechanically.

WHY THIS EXISTS

Before v3.0.0 every run started from scratch. An investor re-reading a
company after a results release had to remember last quarter's conclusion
themselves, because nothing in the system could compare two runs - there was
no persisted decision to compare against. decision_record.py (this file's
sibling) fixed that by giving the decision a validated, storable shape, and
thesis_ledger.py's decisions store (add_decision / list_decisions /
latest_decision / get_decision / supersede_decision) gives it a place to
live. This script is the payoff: it diffs two decision records and prints
the one block that matters on results day - not the whole analysis again,
just what moved.

PURE CORE, THIN SHELL

diff(old_rec, new_rec) is the entire engine: two dicts in, one structured
delta out, no network, no filesystem, no clock. Everything below it
(resolving a company, opening the ledger, pulling stored decisions, printing)
is a thin shell around that pure function, so the comparison itself is fully
unit-testable offline - see tests/test_research_delta.py. Keeping the split
this sharp is deliberate: a diff engine that quietly does I/O is a diff
engine nobody can test against a hand-built pair of records.

THREE RULES THAT MATTER MORE THAN ANY OUTPUT FORMAT

1. NEVER FABRICATE A COMPARISON. A field absent from either record is
   reported as unavailable, never as zero and never as "unchanged" - reading
   silent absence as no-change is the exact failure mode a persistent-decision
   system is built to prevent. See _compare_level() and every _diff_* helper:
   each returns one of three states - "ok" (compared), "unavailable" (missing
   on one or both sides), "not_comparable" (present on both sides but not the
   same thing - see rule 2).

2. NEVER COMPARE ACROSS A BASIS CHANGE. Two numbers that look like the same
   metric are not automatically the same metric. This toolkit has already
   been burned twice: peers_se.py's revenue CAGR used to compound SEK against
   post-redenomination EUR and printed "-35.5%" for a company growing +16% a
   year (peers_se.py ~line 1104), and guidance_track.py refuses to score an
   "adjusted, constant-currency" target against an IFRS-reported actual for
   the same reason (guidance_track.py's ADJUSTED_CUE). Here: a currency
   change on price or fair_value, or a currency/reporting-basis/fiscal-period
   change on fundamentals, marks the whole affected group "not_comparable"
   and NAMES the reason rather than printing a percentage. (Share-count
   semantics are the fourth basis dimension SKILL.md and valuation_gate.py
   both care about - GATE_SHARE_COUNT_UNCERTAIN - but decision_record.py's
   schema does not yet carry a share-count field to compare, so that
   dimension is not checked here. Noted rather than silently skipped.)

3. PERCENTAGE POINTS ARE NOT PERCENT. A margin moving 18.0% -> 18.7% is
   +0.7pp, not +3.9%. Anything that is ALREADY a ratio/percentage in the
   record - margins, expected_return, margin_of_safety, implied-expectation
   rates, investment/data-confidence scores - is diffed as a point delta
   (pp for a 0-1 ratio, pt for a 0-100 score). Anything that is an absolute
   level - price, revenue, EBIT, net debt, a fair-value figure - is diffed as
   a percent change. _compare_level()'s `unit` argument is the one place this
   is decided; get it right there and every caller inherits it.

FIELDS THIS SCRIPT READS THAT decision_record.py's OWN SCHEMA DOES NOT DEFINE

decision_record.validate() does not require or compute `fundamentals`,
`guidance`, `consensus` or `implied_expectations` - as of this writing its
REQUIRED tuple and its rendering only cover verdict/conviction/price/
fair_value/scenario_weights/scores/rests_on/reason_codes. Because validate()
copies unknown keys through untouched (`out = dict(rec)`), a producer is free
to attach these anyway, and this script diffs them WHEN PRESENT, never
assuming their shape is final:

  fundamentals: {revenue, ebit, ebit_margin, net_debt, currency,
                 fiscal_period, fiscal_period_type, basis, share_basis}
  guidance / consensus: {status, text} or a bare status string. references/
                 valuation.md is explicit that consensus is usually
                 "CONSENSUS DATA NOT AVAILABLE" for this toolkit's coverage -
                 that string compares like any other categorical value.
  implied_expectations: canonically a LIST of {metric, value, unit, period,
                 method, note} entries (decision_record.
                 normalise_implied_expectations) - the reverse-DCF surface
                 references/valuation.md describes ("MARKET-IMPLIED
                 EXPECTATION ... optimistic / approximately fair /
                 pessimistic"). Metric names are free text, so the union of
                 both sides is diffed rather than a fixed key list. The
                 older flat {metric: value} and single-entry shapes are
                 still read, for records stored before that landed.

`thesis_ref`, `decision_id`, `superseded_by`/`superseded_at` and
`validation_warnings` are NOT informal, though - thesis_ledger.py's decisions
store (add_decision / list_decisions / latest_decision / get_decision /
supersede_decision) stamps all four onto every stored record, so their shape
is authoritative there, not guessed here:

  decision_id: "<ledger_key>:<created ISO-8601>", e.g.
               "LEI-213800Y2XLTQMHLB5J34:2026-08-31T09:12:04Z", with a
               zero-padded "#0002" suffix for a same-second collision. Plain
               string comparison IS chronological order (thesis_ledger.py's
               own guarantee) - _count_between() relies on that rather than
               re-parsing the timestamp.
  thesis_ref:  {thesis_id, status, status_since, as_of, last_evaluated,
               action, active_theses:[{thesis_id, status}], captured_at} -
               the WORST-standing active thesis at decision time (thesis_
               ledger.decision_thesis_ref), status being one of thesis_
               ledger's derive_status outcomes (BROKEN/WARNING/UNKNOWN/
               STABLE/IMPROVING/CONFIRMED). Its ordering is read from
               thesis_ledger.STATUS_ORDER when reachable, else a local copy
               (see _thesis_status_order()). A decision filed with no active
               thesis carries no thesis_ref at all - absence here is a real
               state ("nothing was tracked yet"), not a gap to guess past.
  superseded_by/superseded_at: set only by an EXPLICIT supersede_decision()
               call - never implied by a newer decision existing alongside
               it. See SUPERSEDE SEMANTICS below.
  validation_warnings: decision_record.validate()'s own warnings (e.g. "no
               rationale"), carried onto the stored record so they survive
               past the run that produced them.

SUPERSEDE SEMANTICS - READ THIS BEFORE TOUCHING --between OR --against

Storing a new decision does NOT retire the previous one. `superseded_by`
means "explicitly withdrawn or corrected" (thesis_ledger.supersede_decision),
not "merely older" - a company can legitimately carry several standing,
non-superseded decisions at once, dated differently. Two consequences this
file's shell code honours:

  - `--between`/`--against` never filter or reorder by supersession status.
    Each stored decision is a valid observation at its own date; the two ids
    a user names are diffed exactly as named, superseded or not.
  - diff() itself, given the two record dicts, surfaces `superseded_by`/
    `superseded_at` WHEN PRESENT on either side (see the "superseded" key in
    its result and the render's WARNING line) - a reader comparing against a
    decision that was later withdrawn needs to be told, not left to notice
    it was never removed from the ledger.

`latest_decision()` already returns the newest decision that is NOT
superseded, so the default (no --against, no --between) path needs no
extra handling: it compares against whatever currently stands.

VERDICT AND CONVICTION ORDERING

decision_record.CONVICTIONS is already ordered weakest to strongest, so a
conviction "upgrade" is a rise in that tuple's index. decision_record.VERDICTS
runs STRONG BUY -> STRONG SELL - i.e. index 0 is the MOST bullish - so a
verdict "upgrade" is a FALL in that tuple's index. Getting this backwards
would silently swap "upgraded to HOLD" for "downgraded to HOLD"; both ladders
and their directions are centralised in verdict_direction() / conviction_
direction() rather than re-derived at each call site.

DEGRADE, NEVER HANG

Every sibling import below is defensive - loaded through _bootstrap.load()
where _bootstrap.py itself is reachable (v3.0.0's centralised version of the
sibling-import idiom), falling back to this file's own copy of the same
pattern otherwise - and every caller catches `(Exception, SystemExit)`, since
these scripts raise SystemExit as their error convention and that does not
inherit Exception (this is precisely the bug _bootstrap.py's own docstring
calls out in an older sibling loader). A missing or mid-edit
decision_record.py falls back to a hard-coded copy of the
two controlled vocabularies (kept byte-identical to decision_record.py's own
tuples) so the pure diff engine still runs; a missing thesis_ledger.py, or one
whose decisions-store API (list_decisions / latest_decision / get_decision -
now landed in thesis_ledger.py) cannot be reached for any reason, degrades
the ledger-backed commands to a clear "no baseline yet" rather than a crash.
That state is the ordinary, expected shape of a first run - not a bug in this
script - and is reported as such (exit 3, not a traceback). The defensive
loading stays even now that the store exists: a parallel edit to
thesis_ledger.py mid-session should degrade this script's commands, not
crash them.
"""
import argparse
import datetime
import importlib.util
import json
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


def _load_sibling(name):
    """Import scripts/<name>.py by file path - the scripts folder is not a
    package, and a parallel agent may be mid-edit on a sibling, so every
    caller of this wraps it in try/except, matching screen_value.py's own
    convention for the same situation. Used only as the fallback if
    _bootstrap.py itself (below) cannot be loaded; otherwise this file uses
    _bootstrap.load(), the centralised version of the same idiom that v3.0.0
    introduced for new code."""
    path = os.path.join(_HERE, name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


try:
    _bootstrap = _load_sibling("_bootstrap")
except (Exception, SystemExit):                               # pragma: no cover
    _bootstrap = None

_load = _bootstrap.load if _bootstrap is not None else _load_sibling

try:
    decision_record = _load("decision_record")
    _DR_IMPORT_ERROR = None
except (Exception, SystemExit) as _exc:                      # pragma: no cover
    decision_record = None
    _DR_IMPORT_ERROR = str(_exc)

try:
    thesis_ledger = _load("thesis_ledger")
    _TL_IMPORT_ERROR = None
except (Exception, SystemExit) as _exc:                       # pragma: no cover
    thesis_ledger = None
    _TL_IMPORT_ERROR = str(_exc)

# Fallback copies, used only if decision_record.py cannot be loaded at all.
# Kept byte-identical to decision_record.py's own tuples on purpose - see
# that file's VERDICTS / CONVICTIONS.
_FALLBACK_VERDICTS = ("STRONG BUY", "BUY", "HOLD", "TRIM", "SELL", "STRONG SELL")
_FALLBACK_CONVICTIONS = ("VERY LOW", "LOW", "MEDIUM", "HIGH", "VERY HIGH")
_FALLBACK_STATUS_ORDER = ["BROKEN", "WARNING", "UNKNOWN", "STABLE", "IMPROVING", "CONFIRMED"]


def _verdicts():
    return decision_record.VERDICTS if decision_record is not None else _FALLBACK_VERDICTS


def _convictions():
    return decision_record.CONVICTIONS if decision_record is not None else _FALLBACK_CONVICTIONS


def _thesis_status_order():
    if thesis_ledger is not None:
        order = getattr(thesis_ledger, "STATUS_ORDER", None)
        if order:
            return order
    return _FALLBACK_STATUS_ORDER


class DeltaError(ValueError):
    """The two records cannot be diffed at all, or an input could not be
    read/validated. Raised instead of guessing."""


class NoBaselineError(DeltaError):
    """There is no stored decision to compare against. This is the expected
    state on a first run, not a defect in the analysis - callers must report
    it as such, not as a failure."""


# --------------------------------------------------------------------------
# Small arithmetic helpers - the pp-vs-percent decision lives here, once.
# --------------------------------------------------------------------------

def _num(x):
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _compare_level(old_val, new_val, unit):
    """Compare one field. unit is one of:
      "pct" - percent change of an absolute LEVEL (price, revenue, EBIT,
              net debt, a fair-value figure).
      "pp"  - percentage-POINT change of a value that is ALREADY a ratio
              0..1 (a margin, expected_return, margin_of_safety, a
              reverse-DCF rate).
      "pt"  - point change of a 0..100 score (Investment Score, Data
              Confidence).

    Returns {"status": "unavailable"} if either side is missing or not
    numeric - never a fabricated 0. Never returns "not_comparable" itself;
    basis mismatches are decided by the caller BEFORE this is reached (see
    _price_basis_issues / _fundamentals_basis_issues) because a unit clash is
    a property of the pair of records, not of one field's arithmetic.
    """
    o, n = _num(old_val), _num(new_val)
    if o is None or n is None:
        return {"status": "unavailable", "old": old_val, "new": new_val}
    if unit == "pct":
        if o == 0:
            return {"status": "unavailable", "old": old_val, "new": new_val,
                    "note": "cannot form a percent change from a zero base"}
        return {"status": "ok", "old": o, "new": n, "change": (n - o) / abs(o), "unit": "pct"}
    if unit == "pp":
        return {"status": "ok", "old": o, "new": n, "change": (n - o) * 100.0, "unit": "pp"}
    if unit == "pt":
        return {"status": "ok", "old": o, "new": n, "change": n - o, "unit": "pt"}
    raise ValueError("unknown unit %r" % unit)


def _not_comparable(reasons):
    return {"status": "not_comparable", "reasons": list(reasons)}


def _base_mid(fv):
    if not isinstance(fv, dict):
        return None
    lo, hi = _num(fv.get("base_low")), _num(fv.get("base_high"))
    if lo is None or hi is None:
        return None
    return (lo + hi) / 2.0


# --------------------------------------------------------------------------
# Basis checks - rule 2. Each returns a list of human-readable reasons; an
# empty list means "no basis objection found", not "confirmed comparable".
# --------------------------------------------------------------------------

def _identity_mismatch(old_ident, new_ident):
    old_ident = old_ident or {}
    new_ident = new_ident or {}
    for key in ("lei", "isin"):
        o, n = old_ident.get(key), new_ident.get(key)
        if o and n and o != n:
            return "identity.%s differs: %s vs %s - these are two different issuers, not a delta" % (key, o, n)
    return None


def _price_basis_issues(old, new):
    op, npr = old.get("price") or {}, new.get("price") or {}
    issues = []
    oc, nc = op.get("currency"), npr.get("currency")
    if oc and nc and oc != nc:
        issues.append("price currency changed from %s to %s" % (oc, nc))
    orep, nrep = op.get("reporting_currency"), npr.get("reporting_currency")
    if orep and nrep and orep != nrep:
        issues.append("reporting currency changed from %s to %s" % (orep, nrep))
    return issues


def _fair_value_basis_issues(old, new):
    op = (old.get("price") or {}).get("currency")
    npc = (new.get("price") or {}).get("currency")
    ofv, nfv = old.get("fair_value") or {}, new.get("fair_value") or {}
    oc = ofv.get("currency") or op
    nc = nfv.get("currency") or npc
    if oc and nc and oc != nc:
        return ["fair value currency changed from %s to %s" % (oc, nc)]
    return []


_FUNDAMENTALS_BASIS_FIELDS = (
    ("currency", "currency"),
    ("basis", "reporting basis (e.g. adjusted vs IFRS)"),
    ("fiscal_period_type", "fiscal-period definition (e.g. annual vs quarterly vs TTM)"),
    ("share_basis", "share-count basis"),
)


def _fundamentals_basis_issues(old, new):
    of, nf = old.get("fundamentals"), new.get("fundamentals")
    if not isinstance(of, dict) or not isinstance(nf, dict):
        return []
    issues = []
    for key, label in _FUNDAMENTALS_BASIS_FIELDS:
        o, n = of.get(key), nf.get(key)
        if o and n and o != n:
            issues.append("fundamentals %s changed from %s to %s" % (label, o, n))
    return issues


# --------------------------------------------------------------------------
# Section diffs
# --------------------------------------------------------------------------

def _diff_fundamentals(old, new):
    of, nf = old.get("fundamentals"), new.get("fundamentals")
    if not isinstance(of, dict) or not isinstance(nf, dict):
        return {"status": "unavailable",
                "note": "fundamentals not carried on both decision records"}
    issues = _fundamentals_basis_issues(old, new)
    if issues:
        return _not_comparable(issues)
    return {
        "status": "ok",
        "revenue": _compare_level(of.get("revenue"), nf.get("revenue"), "pct"),
        "ebit": _compare_level(of.get("ebit"), nf.get("ebit"), "pct"),
        "ebit_margin": _compare_level(of.get("ebit_margin"), nf.get("ebit_margin"), "pp"),
        "net_debt": _compare_level(of.get("net_debt"), nf.get("net_debt"), "pct"),
        "fiscal_period": {"old": of.get("fiscal_period"), "new": nf.get("fiscal_period")},
    }


def _cat_status(v):
    return v.get("status") if isinstance(v, dict) else v


def _diff_categorical(old, new, key):
    o, n = _cat_status(old.get(key)), _cat_status(new.get(key))
    if o is None and n is None:
        return {"status": "unavailable"}
    if o is None or n is None:
        return {"status": "unavailable", "old": o, "new": n,
                "note": "not carried on both decision records"}
    return {"status": "ok", "old": o, "new": n,
            "direction": "unchanged" if o == n else "changed"}


def _diff_valuation(old, new):
    issues = _price_basis_issues(old, new) + _fair_value_basis_issues(old, new)
    if issues:
        return _not_comparable(issues)
    op, npr = old.get("price") or {}, new.get("price") or {}
    ofv, nfv = old.get("fair_value") or {}, new.get("fair_value") or {}
    fair_value = {k: _compare_level(ofv.get(k), nfv.get(k), "pct")
                  for k in ("bear", "base_low", "base_high", "bull")}
    omos, nmos = old.get("margin_of_safety"), new.get("margin_of_safety")
    omos = omos if isinstance(omos, dict) else {}
    nmos = nmos if isinstance(nmos, dict) else {}
    return {
        "status": "ok",
        "price": _compare_level(op.get("value"), npr.get("value"), "pct"),
        "fair_value": fair_value,
        "fair_value_base_mid": _compare_level(_base_mid(ofv), _base_mid(nfv), "pct"),
        "expected_return": _compare_level(old.get("expected_return"), new.get("expected_return"), "pp"),
        "margin_of_safety": {
            "to_base_low": _compare_level(omos.get("to_base_low"), nmos.get("to_base_low"), "pp"),
            "to_base_high": _compare_level(omos.get("to_base_high"), nmos.get("to_base_high"), "pp"),
        },
    }


def _diff_scores(old, new):
    os_, ns_ = old.get("scores") or {}, new.get("scores") or {}
    return {
        "investment_score": _compare_level(os_.get("investment_score"), ns_.get("investment_score"), "pt"),
        "data_confidence": _compare_level(os_.get("data_confidence"), ns_.get("data_confidence"), "pt"),
    }


def _diff_assumptions(old, new):
    oa = {a["key"]: a for a in (old.get("assumptions") or []) if isinstance(a, dict) and a.get("key")}
    na = {a["key"]: a for a in (new.get("assumptions") or []) if isinstance(a, dict) and a.get("key")}
    added = sorted(set(na) - set(oa))
    removed = sorted(set(oa) - set(na))
    changed, unchanged = [], []
    for k in sorted(set(oa) & set(na)):
        ov, nv = oa[k].get("value"), na[k].get("value")
        if ov == nv:
            unchanged.append(k)
        else:
            changed.append({"key": k, "old": ov, "new": nv,
                            "unit": na[k].get("unit") or oa[k].get("unit")})
    return {"added": added, "removed": removed, "changed": changed, "unchanged": unchanged}


def _rests_on_texts(entries):
    out = []
    for r in entries or []:
        out.append(r.get("text") if isinstance(r, dict) else str(r))
    return out


def _diff_rests_on(old, new):
    ro, rn = _rests_on_texts(old.get("rests_on")), _rests_on_texts(new.get("rests_on"))
    return {
        "still_rests_on": [t for t in rn if t in ro],
        "dropped": [t for t in ro if t not in rn],
        "new": [t for t in rn if t not in ro],
    }


def _diff_reason_codes(old, new):
    def sev_map(rec):
        return {c.get("code"): c.get("severity") for c in (rec.get("reason_codes") or [])
                if isinstance(c, dict)}
    osev, nsev = sev_map(old), sev_map(new)
    oc, nc = set(osev), set(nsev)
    appeared = sorted(nc - oc)
    resolved = sorted(oc - nc)
    return {
        "appeared": [{"code": c, "severity": nsev.get(c)} for c in appeared],
        "resolved": [{"code": c, "severity": osev.get(c)} for c in resolved],
        "unchanged": sorted(oc & nc),
    }


def _diff_caps(old, new):
    oc = {c.get("cap") for c in (old.get("caps_applied") or []) if isinstance(c, dict)}
    nc = {c.get("cap") for c in (new.get("caps_applied") or []) if isinstance(c, dict)}
    return {"appeared": sorted(nc - oc), "resolved": sorted(oc - nc), "unchanged": sorted(oc & nc)}


def _implied_as_map(raw):
    """implied_expectations, in any authored shape, as {metric: value}.

    decision_record.normalise_implied_expectations() canonicalises the field
    to a LIST of {metric, value, unit, period, method, note} entries, so that
    is the shape that actually arrives from the store. The earlier flat
    mapping and single-entry shapes are still accepted here, because a record
    written before the canonicalisation landed carries one of them and a
    delta against an older stored decision must not silently degrade.

    Anything that is neither - a bare "DATA NOT AVAILABLE"-style string, for
    instance - yields {} and the caller reports it as unavailable rather than
    comparing it.
    """
    if not raw:
        return {}
    if isinstance(raw, dict):
        if "metric" in raw or "value" in raw:
            raw = [raw]
        else:
            return {k: v for k, v in raw.items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)}
    if not isinstance(raw, (list, tuple)):
        return {}
    out = {}
    for entry in raw:
        if isinstance(entry, dict) and entry.get("metric"):
            out[entry["metric"]] = entry.get("value")
    return out


def _diff_implied_expectations(old, new):
    """What the MARKET was assuming, then versus now.

    Metric names are NOT hardcoded: the canonical entries carry free-text
    metric names resolved against the ledger vocabulary at read time, so a
    reverse DCF that solved for something this file has never heard of is
    still diffed. The union of both sides is compared, and a metric present
    on only one side is reported as unavailable rather than as a move from
    or to zero.

    Values are ratios, so every comparison is in PERCENTAGE POINTS.
    """
    oe_raw, ne_raw = old.get("implied_expectations"), new.get("implied_expectations")
    om, nm = _implied_as_map(oe_raw), _implied_as_map(ne_raw)

    if not om and not nm:
        if oe_raw is None and ne_raw is None:
            return {"status": "unavailable"}
        if oe_raw is None or ne_raw is None:
            return {"status": "unavailable", "old": oe_raw, "new": ne_raw}
        # Both sides present but neither parseable into metrics - compare
        # them whole rather than pretending to a per-metric answer.
        return {"status": "ok", "old": oe_raw, "new": ne_raw,
                "direction": "unchanged" if oe_raw == ne_raw else "changed"}

    out = {"status": "ok", "metrics": {}}
    for metric in sorted(set(om) | set(nm)):
        out["metrics"][metric] = _compare_level(om.get(metric), nm.get(metric), "pp")
    out["dropped"] = sorted(set(om) - set(nm))
    out["added"] = sorted(set(nm) - set(om))

    # Kept for records authored in the old flat shape, which carried a
    # verdict alongside the rates.
    ov = oe_raw.get("verdict") if isinstance(oe_raw, dict) else None
    nv = ne_raw.get("verdict") if isinstance(ne_raw, dict) else None
    if ov is not None or nv is not None:
        out["verdict"] = {"old": ov, "new": nv,
                          "direction": "unchanged" if ov == nv else "changed"}
    return out


def _diff_thesis_ref(old, new):
    """thesis_ledger.decision_thesis_ref()'s shape: {thesis_id, status,
    status_since, as_of, last_evaluated, action, active_theses, captured_at}.
    Absence on one side is a real state - "no active thesis was linked at
    that decision" - not a gap to guess past, so it is "unavailable", never
    treated as "no change"."""
    ot, nt = old.get("thesis_ref"), new.get("thesis_ref")
    if not isinstance(ot, dict) and not isinstance(nt, dict):
        return {"status": "unavailable"}
    if not isinstance(ot, dict) or not isinstance(nt, dict):
        return {"status": "unavailable", "old": ot, "new": nt,
                "note": "no active thesis was linked to one of the two decisions"}
    ost, nst = ot.get("status"), nt.get("status")
    order = _thesis_status_order()
    direction = "unknown"
    if ost in order and nst in order:
        oi, ni = order.index(ost), order.index(nst)
        direction = "unchanged" if oi == ni else ("improving" if ni > oi else "deteriorating")
    return {
        "status": "ok",
        "thesis_id": {"old": ot.get("thesis_id"), "new": nt.get("thesis_id")},
        "old_status": ost, "new_status": nst, "direction": direction,
        "action": {"old": ot.get("action"), "new": nt.get("action")},
    }


def _diff_validation_warnings(old, new):
    """decision_record.validate()'s own warnings, carried onto the stored
    record by thesis_ledger.add_decision(). A drop in count is evidence
    quality IMPROVING, a rise is evidence quality WORSENING - named, not just
    counted, so a reader can see WHICH warning appeared or went away."""
    ow = set(old.get("validation_warnings") or [])
    nw = set(new.get("validation_warnings") or [])
    if not ow and not nw:
        return {"status": "unavailable"}
    return {"status": "ok", "appeared": sorted(nw - ow),
            "resolved": sorted(ow - nw), "unchanged": sorted(ow & nw)}


def _superseded_note(rec, label):
    by = rec.get("superseded_by")
    if not by:
        return None
    return {"which": label, "by": by, "at": rec.get("superseded_at"),
            "withdrawn": by == "WITHDRAWN"}


def verdict_direction(old_v, new_v):
    """decision_record.VERDICTS runs STRONG BUY -> STRONG SELL: index 0 is
    the MOST bullish, so an upgrade is a FALL in index - the opposite of
    conviction_direction()."""
    order = _verdicts()
    if old_v not in order or new_v not in order:
        return "unknown"
    oi, ni = order.index(old_v), order.index(new_v)
    if oi == ni:
        return "unchanged"
    return "upgrade" if ni < oi else "downgrade"


def conviction_direction(old_c, new_c):
    """decision_record.CONVICTIONS is ordered weakest to strongest: an
    upgrade is a RISE in index."""
    order = _convictions()
    if old_c not in order or new_c not in order:
        return "unknown"
    oi, ni = order.index(old_c), order.index(new_c)
    if oi == ni:
        return "unchanged"
    return "upgrade" if ni > oi else "downgrade"


def _thesis_scorecard(rests_on, reason_codes):
    """Confirmations: assumptions the thesis still rests on that were also
    named last time (still true, mechanically - never "probably still true").
    Concerns: reason codes that were NOT present before and carry WARN or
    BLOCK severity. Deliberately narrow and mechanical rather than an
    editorial judgement call, so the count cannot be gamed by wording."""
    confirmations = len(rests_on["still_rests_on"])
    concerns = len([c for c in reason_codes["appeared"] if c.get("severity") in ("WARN", "BLOCK")])
    return confirmations, concerns


def diff(old_rec, new_rec):
    """The pure core. Two decision-record dicts in, one structured delta out.

    Raises DeltaError only when the two records cannot be compared AT ALL
    (not a dict, or a hard identity mismatch - diffing Ericsson B against
    Ericsson A would silently average two different economic claims into one
    number). Anything less than that degrades to a per-field/per-group
    "unavailable" or "not_comparable" inside the returned structure - see the
    module docstring's rule 1 and rule 2.
    """
    if not isinstance(old_rec, dict) or not isinstance(new_rec, dict):
        raise DeltaError("both records must be JSON objects")

    mismatch = _identity_mismatch(old_rec.get("identity"), new_rec.get("identity"))
    if mismatch:
        raise DeltaError("cannot diff two different companies - %s" % mismatch)

    verdict = {"old": old_rec.get("verdict"), "new": new_rec.get("verdict")}
    verdict["direction"] = verdict_direction(verdict["old"], verdict["new"])
    conviction = {"old": old_rec.get("conviction"), "new": new_rec.get("conviction")}
    conviction["direction"] = conviction_direction(conviction["old"], conviction["new"])

    rests_on = _diff_rests_on(old_rec, new_rec)
    reason_codes = _diff_reason_codes(old_rec, new_rec)
    confirmations, concerns = _thesis_scorecard(rests_on, reason_codes)

    old_as_of, new_as_of = old_rec.get("as_of"), new_rec.get("as_of")
    interval = {"status": "unavailable"}
    if old_as_of and new_as_of:
        try:
            od = datetime.date.fromisoformat(str(old_as_of)[:10])
            nd = datetime.date.fromisoformat(str(new_as_of)[:10])
            interval = {"status": "ok", "from": old_as_of, "to": new_as_of,
                       "days": (nd - od).days, "reports_between": None}
        except ValueError:
            interval = {"status": "unavailable", "note": "as_of not a parseable date"}

    superseded = [n for n in (
        _superseded_note(old_rec, "old"), _superseded_note(new_rec, "new")) if n]

    result = {
        "schema_version": 1,
        "old_decision_id": old_rec.get("decision_id"),
        "new_decision_id": new_rec.get("decision_id"),
        "identity": new_rec.get("identity") or old_rec.get("identity"),
        "interval": interval,
        "superseded": superseded,
        "fundamentals": _diff_fundamentals(old_rec, new_rec),
        "guidance": _diff_categorical(old_rec, new_rec, "guidance"),
        "consensus": _diff_categorical(old_rec, new_rec, "consensus"),
        "valuation": _diff_valuation(old_rec, new_rec),
        "scores": _diff_scores(old_rec, new_rec),
        "validation_warnings": _diff_validation_warnings(old_rec, new_rec),
        "thesis": {
            "assumptions": _diff_assumptions(old_rec, new_rec),
            "rests_on": rests_on,
            "reason_codes": reason_codes,
            "confirmations": confirmations,
            "concerns": concerns,
            "implied_expectations": _diff_implied_expectations(old_rec, new_rec),
            "thesis_ref": _diff_thesis_ref(old_rec, new_rec),
        },
        "decision": {
            "verdict": verdict,
            "conviction": conviction,
            "caps_applied": _diff_caps(old_rec, new_rec),
        },
    }
    result["material"] = not (verdict["direction"] == "unchanged"
                              and conviction["direction"] == "unchanged")
    return result


# --------------------------------------------------------------------------
# Rendering - human block, <=88 columns, matching the toolkit's text-chart
# rule. A hard clip is the safety net: verdict/conviction strings and
# free-text reasons vary in length, and rather than hand-tune padding
# budgets for every combination, every finished line is clipped to the
# limit. JSON mode (--json) always carries the untruncated data.
# --------------------------------------------------------------------------

_LINE_WIDTH = 88


def _clip(line, width=_LINE_WIDTH):
    return line if len(line) <= width else line[:width - 1] + "…"


def _fmt_field(field, unit):
    if not field:
        return "n/a"
    status = field.get("status")
    if status == "not_comparable":
        return "N/C"
    if status != "ok":
        return "n/a"
    x = field.get("change")
    if x is None:
        return "n/a"
    if unit == "pct":
        return "%+.0f%%" % (100.0 * x)
    if unit == "pp":
        return "%+.1fpp" % x
    if unit == "pt":
        return "%+d" % int(round(x))
    return str(x)


def _fmt_categorical(cat):
    if cat.get("status") != "ok":
        return "n/a"
    return cat["new"] if cat["direction"] == "changed" else "unchanged"


def _row3(l1, v1, l2, v2, l3, v3):
    return "%-13s %-10s %-11s %-10s %-10s %s" % (l1, v1, l2, v2, l3, v3)


def _plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def _headline(delta):
    v, c = delta["decision"]["verdict"], delta["decision"]["conviction"]
    if v["direction"] == "unknown" or c["direction"] == "unknown":
        return "Decision comparison incomplete: verdict or conviction missing on one side."
    if not delta["material"]:
        return ("NO CHANGE TO THE CALL: still %s, %s conviction."
                % (v["new"], c["new"]))
    bits = []
    if v["direction"] != "unchanged":
        bits.append("verdict %s -> %s (%s)" % (v["old"], v["new"], v["direction"].upper()))
    else:
        bits.append("verdict unchanged (%s)" % v["new"])
    if c["direction"] != "unchanged":
        bits.append("conviction %s -> %s (%s)" % (c["old"], c["new"], c["direction"].upper()))
    else:
        bits.append("conviction unchanged (%s)" % c["new"])
    return "THE CALL MOVED: " + "; ".join(bits)


def _fmt_caps(caps):
    if not caps["appeared"] and not caps["resolved"]:
        return "unchanged"
    bits = []
    if caps["appeared"]:
        bits.append("+" + ",".join(caps["appeared"]))
    if caps["resolved"]:
        bits.append("-" + ",".join(caps["resolved"]))
    return _clip(" ".join(bits), 28)


def render_human(delta):
    ident = delta.get("identity") or {}
    name = (ident.get("ticker") or ident.get("name") or ident.get("company_name")
           or ident.get("legal_name") or "?")
    lines = []

    interval = delta.get("interval") or {}
    head = "WHAT CHANGED? — %s" % name
    if interval.get("status") == "ok":
        head += " · %s -> %s (%d days" % (interval["from"], interval["to"], interval["days"])
        rb = interval.get("reports_between")
        head += ", %s in between)" % _plural(rb, "report") if rb is not None else ")"
    lines.append(_clip(head))
    for note in delta.get("superseded") or []:
        tag = "WITHDRAWN" if note["withdrawn"] else ("superseded by %s" % note["by"])
        lines.append(_clip("  WARNING: the %s decision was %s (%s)"
                           % (note["which"], tag, note.get("at") or "date unknown")))
    lines.append("")

    # --- facts changed ------------------------------------------------
    lines.append("FACTS")
    f = delta["fundamentals"]
    if f["status"] == "not_comparable":
        lines.append(_clip("  NOT COMPARABLE — " + "; ".join(f["reasons"])))
    elif f["status"] == "unavailable":
        lines.append("  DATA NOT AVAILABLE — fundamentals not carried on both decisions")
    else:
        lines.append(_clip("  " + _row3(
            "Revenue", _fmt_field(f["revenue"], "pct"),
            "EBIT", _fmt_field(f["ebit"], "pct"),
            "Margin", _fmt_field(f["ebit_margin"], "pp"))))
        lines.append(_clip("  " + _row3(
            "Net debt", _fmt_field(f["net_debt"], "pct"),
            "Guidance", _fmt_categorical(delta["guidance"]),
            "Consensus", _fmt_categorical(delta["consensus"]))))
    lines.append("")

    # --- valuation changed ---------------------------------------------
    lines.append("VALUATION")
    val = delta["valuation"]
    if val["status"] == "not_comparable":
        lines.append(_clip("  NOT COMPARABLE — " + "; ".join(val["reasons"])))
    else:
        lines.append(_clip("  " + _row3(
            "Price", _fmt_field(val["price"], "pct"),
            "Fair value", _fmt_field(val["fair_value_base_mid"], "pct"),
            "Exp. return", _fmt_field(val["expected_return"], "pp"))))
        lines.append(_clip("  " + _row3(
            "MoS (low)", _fmt_field(val["margin_of_safety"]["to_base_low"], "pp"),
            "MoS (high)", _fmt_field(val["margin_of_safety"]["to_base_high"], "pp"),
            "", "")))
    lines.append("")

    # --- thesis moved ----------------------------------------------------
    lines.append("THESIS")
    th = delta["thesis"]
    lines.append("  %s, %s" % (_plural(th["confirmations"], "confirmation"),
                               _plural(th["concerns"], "concern")))
    # What the MARKET was assuming, then versus now. This belongs in the
    # rendered block and not only in the JSON: "Inprisat" is a mandatory line
    # of the verdict block, so a reader who is told the thesis moved needs to
    # see whether the expectations it is judged against moved with it.
    ie = th.get("implied_expectations") or {}
    if ie.get("status") == "ok" and ie.get("metrics"):
        moved = [(m, f) for m, f in sorted(ie["metrics"].items())
                 if (f or {}).get("status") == "ok" and (f or {}).get("change")]
        if moved:
            shown = " · ".join("%s %s" % (m, _fmt_field(f, "pp"))
                               for m, f in moved[:3])
            if len(moved) > 3:
                shown += " · +%d more" % (len(moved) - 3)
            lines.append(_clip("  Priced in: " + shown))
        if ie.get("dropped"):
            lines.append(_clip("  WARNING: implied expectation(s) no longer "
                               "stated — " + ", ".join(ie["dropped"])))
    if th["assumptions"]["removed"]:
        lines.append(_clip("  WARNING: assumption(s) vanished — "
                           + ", ".join(th["assumptions"]["removed"])))
    if th["reason_codes"]["appeared"]:
        lines.append(_clip("  New flags: " + ", ".join(
            "%s(%s)" % (c["code"], c["severity"]) for c in th["reason_codes"]["appeared"])))
    if th["reason_codes"]["resolved"]:
        # Both appeared and resolved carry {code, severity} - symmetric on
        # purpose, since "a BLOCK cleared" and "a WARN cleared" are not the
        # same news. Read the code out rather than joining the dicts.
        lines.append(_clip("  Resolved: " + ", ".join(
            "%s(%s)" % (c["code"], c["severity"]) if c.get("severity")
            else c["code"] for c in th["reason_codes"]["resolved"])))
    tref = th["thesis_ref"]
    if tref["status"] == "ok" and tref["direction"] != "unchanged":
        lines.append(_clip("  Thesis %s: %s -> %s"
                           % (tref["direction"], tref["old_status"], tref["new_status"])))
    lines.append("")

    # --- decision moved (impossible to miss) ------------------------------
    lines.append("DECISION")
    lines.append(_clip("  " + _headline(delta)))
    d = delta["decision"]
    lines.append(_clip("  Verdict: %s -> %s     Conviction: %s -> %s"
                       % (d["verdict"]["old"], d["verdict"]["new"],
                          d["conviction"]["old"], d["conviction"]["new"])))
    lines.append("")

    # --- evidence quality moved -------------------------------------------
    lines.append("EVIDENCE QUALITY")
    sc = delta["scores"]
    lines.append(_clip("  " + _row3(
        "Inv. Score", _fmt_field(sc["investment_score"], "pt"),
        "Data Conf.", _fmt_field(sc["data_confidence"], "pt"),
        "Caps", _fmt_caps(d["caps_applied"]))))
    vw = delta["validation_warnings"]
    if vw["status"] == "ok" and (vw["appeared"] or vw["resolved"]):
        if vw["appeared"]:
            lines.append(_clip("  New warning(s): " + "; ".join(vw["appeared"])))
        if vw["resolved"]:
            lines.append(_clip("  Warning(s) resolved: " + "; ".join(vw["resolved"])))

    return "\n".join(l.rstrip() for l in lines).rstrip("\n")


# --------------------------------------------------------------------------
# Ledger-backed shell - all the I/O lives here, none of it in diff().
# --------------------------------------------------------------------------

def _decisions_api():
    if thesis_ledger is None:
        return None, None, None
    return (getattr(thesis_ledger, "list_decisions", None),
            getattr(thesis_ledger, "latest_decision", None),
            getattr(thesis_ledger, "get_decision", None))


def _load_ledger_fn():
    """thesis_ledger's ledger-read function. The brief for this store names
    it load_ledger; today's thesis_ledger.py (as of this writing) calls it
    read_ledger. Accept either, so a rename on either side does not break
    this script."""
    if thesis_ledger is None:
        return None
    return getattr(thesis_ledger, "load_ledger", None) or getattr(thesis_ledger, "read_ledger", None)


def _open_ledger(company, country=None, offline=False):
    if thesis_ledger is None:
        raise NoBaselineError(
            "thesis_ledger.py could not be loaded (%s) - there is no ledger "
            "to compare against yet." % _TL_IMPORT_ERROR)
    load_ledger = _load_ledger_fn()
    resolve_identity = getattr(thesis_ledger, "resolve_identity", None)
    ledger_key = getattr(thesis_ledger, "ledger_key", None)
    if not (load_ledger and resolve_identity and ledger_key):
        raise NoBaselineError(
            "thesis_ledger.py does not yet expose the ledger read API this "
            "script needs (a ledger loader, resolve_identity, ledger_key).")
    try:
        identity = resolve_identity(company, country=country, offline=offline)
        key = ledger_key(identity)
    except (Exception, SystemExit) as exc:
        raise DeltaError("could not resolve %r: %s" % (company, exc))
    led = load_ledger(key)
    if led is None:
        raise NoBaselineError(
            "no ledger for %r yet (resolved to %s). This is the expected "
            "state on a first run, not an error: nothing has been tracked "
            "for this company, so there is no baseline to diff against."
            % (company, key))
    return led, key, identity


def _read_record(path_or_dash):
    try:
        if path_or_dash == "-":
            raw = sys.stdin.read()
        else:
            with open(path_or_dash, encoding="utf-8") as fh:
                raw = fh.read()
    except OSError as exc:
        raise DeltaError("could not read %r: %s" % (path_or_dash, exc))
    try:
        rec = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeltaError("%r is not valid JSON: %s" % (path_or_dash, exc))
    if decision_record is not None:
        try:
            rec, _warns = decision_record.validate(rec, strict=False)
        except decision_record.DecisionError as exc:
            raise DeltaError("the new record was refused by decision_record.validate: %s" % exc)
    return rec


def _count_between(all_decisions, old_rec, new_rec):
    """Stored decisions strictly between the two endpoints, excluding the
    endpoints themselves. None (not zero) when there is not enough
    information to count - see rule 1.

    Ordered by decision_id, not as_of: thesis_ledger.py's decision_id is
    "<ledger_key>:<created ISO-8601>[#NNNN]", and plain string comparison of
    that IS chronological order (its own stated guarantee) - re-deriving
    order from as_of (the ANALYSIS date, which two decisions filed the same
    day would tie on) would be both redundant and less precise than what the
    store already guarantees.
    """
    oid, nid = old_rec.get("decision_id"), new_rec.get("decision_id")
    if not oid or not nid or not all_decisions:
        return None
    lo, hi = (oid, nid) if oid <= nid else (nid, oid)
    excl = {oid, nid}
    return sum(1 for d in all_decisions
              if d.get("decision_id") not in excl
              and lo < (d.get("decision_id") or "") < hi)


def _finish(delta, led, list_decisions, old_rec, new_rec):
    if delta["interval"]["status"] == "ok" and list_decisions is not None:
        try:
            all_decisions = list_decisions(led)
        except (Exception, SystemExit):
            all_decisions = None
        if all_decisions is not None:
            delta["interval"]["reports_between"] = _count_between(all_decisions, old_rec, new_rec)
    return delta


def _emit(delta, as_json):
    if as_json:
        print(json.dumps(delta, indent=2, ensure_ascii=False, default=str))
    else:
        print(render_human(delta))


def _cmd_new_vs_stored(args):
    led, key, _identity = _open_ledger(args.company, country=args.country, offline=args.offline)
    list_decisions, latest_decision, get_decision = _decisions_api()
    if not (list_decisions and latest_decision and get_decision):
        raise NoBaselineError(
            "thesis_ledger.py does not yet expose a decisions store "
            "(list_decisions / latest_decision / get_decision) - nothing to "
            "compare against.")
    if args.against:
        old_rec = get_decision(led, args.against)
        if old_rec is None:
            raise NoBaselineError("no stored decision %r for %r." % (args.against, args.company))
    else:
        old_rec = latest_decision(led)
        if old_rec is None:
            raise NoBaselineError(
                "no stored decision for %r yet (ledger key %s). This is the "
                "expected state on a first run: run analyze/quick/etc. once "
                "to create a decision record, then re-run research_delta "
                "after the next one." % (args.company, key))
    new_rec = _read_record(args.new)
    delta = diff(old_rec, new_rec)
    return _finish(delta, led, list_decisions, old_rec, new_rec)


def _cmd_between(args):
    led, _key, _identity = _open_ledger(args.company, country=args.country, offline=args.offline)
    list_decisions, _latest, get_decision = _decisions_api()
    if not get_decision:
        raise NoBaselineError(
            "thesis_ledger.py does not yet expose a decisions store "
            "(get_decision) - nothing to compare against.")
    id_a, id_b = args.between
    old_rec, new_rec = get_decision(led, id_a), get_decision(led, id_b)
    if old_rec is None or new_rec is None:
        missing = id_a if old_rec is None else id_b
        raise NoBaselineError("decision %r not found for %r." % (missing, args.company))
    delta = diff(old_rec, new_rec)
    return _finish(delta, led, list_decisions, old_rec, new_rec)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("company", nargs="?",
                    help="company name, ticker, ISIN or LEI - resolved the "
                         "same way thesis_ledger.py resolves one")
    ap.add_argument("new", nargs="?", default="-",
                    help="the NEW decision record: a JSON file path, or - "
                         "for stdin (default). Ignored with --between.")
    ap.add_argument("--against", metavar="DECISION_ID",
                    help="diff the new record against this stored decision "
                         "instead of the latest")
    ap.add_argument("--between", nargs=2, metavar=("OLD_ID", "NEW_ID"),
                    help="diff two stored decisions directly, OLD_ID treated "
                         "as the earlier one regardless of the ledger's own "
                         "ordering")
    ap.add_argument("--country", help="ISO-2 hint for identity resolution, e.g. SE")
    ap.add_argument("--offline", action="store_true",
                    help="use only cached data when resolving identity")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.company:
        ap.error("give a company (or --selftest)")

    try:
        if args.between:
            delta = _cmd_between(args)
        else:
            delta = _cmd_new_vs_stored(args)
    except NoBaselineError as exc:
        print("NO BASELINE YET: %s" % exc, file=sys.stderr)
        return 3
    except DeltaError as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        return 2

    _emit(delta, args.as_json)
    return 0


# --------------------------------------------------------------------------
# Selftest - a smoke test against decision_record's own canonical fixture.
# The dedicated suite (tests/test_research_delta.py) is the thorough one;
# this is the quick, dependency-light check the house --selftest convention
# asks every script to carry.
# --------------------------------------------------------------------------

def _fixture():
    """decision_record's own canonical fixture, run through validate() so
    expected_return / margin_of_safety are populated exactly as a real
    caller would receive them - _sandvik_fixture() is the raw INPUT shape,
    not the normalised record diff() is meant to see."""
    if decision_record is None:
        return None
    import copy
    rec, _warns = decision_record.validate(copy.deepcopy(decision_record._sandvik_fixture()))
    return rec


def selftest():
    if decision_record is None:
        print("research_delta selftest: SKIPPED - decision_record.py could "
             "not be loaded (%s)" % _DR_IMPORT_ERROR)
        return 0

    fails = []

    def check(label, cond):
        if not cond:
            fails.append(label)

    old = _fixture()
    new = _fixture()

    identical = diff(old, new)
    check("identical records must show no material change", not identical["material"])
    check("identical records: verdict direction unchanged",
          identical["decision"]["verdict"]["direction"] == "unchanged")
    check("identical records: no fabricated fundamentals when none carried",
          identical["fundamentals"]["status"] == "unavailable")

    # Verdict upgrade (index falls: BUY -> STRONG BUY).
    up = _fixture()
    up["verdict"] = "STRONG BUY"
    up["conviction"] = "MEDIUM"
    d = diff(old, up)
    check("BUY -> STRONG BUY must read as an upgrade",
          d["decision"]["verdict"]["direction"] == "upgrade")

    # Verdict downgrade.
    down = _fixture()
    down["verdict"] = "SELL"
    down["conviction"] = "LOW"
    d = diff(old, down)
    check("BUY -> SELL must read as a downgrade",
          d["decision"]["verdict"]["direction"] == "downgrade")

    # Conviction upgrade / downgrade on the real ladder.
    hi = _fixture()
    hi["conviction"] = "HIGH"
    d = diff(old, hi)
    check("MEDIUM -> HIGH conviction must read as an upgrade",
          d["decision"]["conviction"]["direction"] == "upgrade")
    lo = _fixture()
    lo["conviction"] = "LOW"
    d = diff(old, lo)
    check("MEDIUM -> LOW conviction must read as a downgrade",
          d["decision"]["conviction"]["direction"] == "downgrade")

    # Currency change refuses the comparison rather than printing a number.
    ccy = _fixture()
    ccy["price"]["currency"] = "EUR"
    d = diff(old, ccy)
    check("a currency change must mark valuation NOT COMPARABLE",
          d["valuation"]["status"] == "not_comparable")

    # Missing field never reads as zero/unchanged.
    bare = _fixture()
    bare.pop("fair_value", None)
    bare.pop("scenario_weights", None)
    bare["expected_return"] = None
    bare["margin_of_safety"] = None
    d = diff(old, bare)
    check("a dropped expected_return must be unavailable, not zero",
          d["valuation"]["expected_return"]["status"] == "unavailable")

    # pp vs percent on a margin.
    of = _fixture()
    of["fundamentals"] = {"ebit_margin": 0.180, "currency": "SEK", "basis": "IFRS"}
    nf = _fixture()
    nf["fundamentals"] = {"ebit_margin": 0.187, "currency": "SEK", "basis": "IFRS"}
    d = diff(of, nf)
    field = d["fundamentals"]["ebit_margin"]
    check("18.0%% -> 18.7%% margin must be +0.7pp, got %r" % field.get("change"),
          field["status"] == "ok" and abs(field["change"] - 0.7) < 1e-6)

    block = render_human(diff(old, hi))
    check("no rendered line may exceed 88 columns",
          all(len(l) <= 88 for l in block.splitlines()))
    check("render must carry the WHAT CHANGED header", block.startswith("WHAT CHANGED?"))

    # A superseded decision is surfaced, never silently diffed as if standing.
    withdrawn = _fixture()
    withdrawn["superseded_by"] = "WITHDRAWN"
    withdrawn["superseded_at"] = "2026-09-01T00:00:00Z"
    d = diff(withdrawn, new)
    check("a withdrawn old decision must be surfaced in 'superseded'",
          any(n["which"] == "old" and n["withdrawn"] for n in d["superseded"]))
    check("the withdrawn warning must reach the render",
          "WITHDRAWN" in render_human(d))

    # thesis_ref transitions use thesis_ledger's real shape and STATUS_ORDER.
    worsened_old = _fixture()
    worsened_old["thesis_ref"] = {"thesis_id": "T1", "status": "CONFIRMED"}
    worsened_new = _fixture()
    worsened_new["thesis_ref"] = {"thesis_id": "T1", "status": "WARNING"}
    d = diff(worsened_old, worsened_new)
    check("CONFIRMED -> WARNING must read as the thesis deteriorating",
          d["thesis"]["thesis_ref"]["direction"] == "deteriorating")

    if fails:
        print("SELFTEST FAILED (%d)" % len(fails))
        for f in fails:
            print("  - %s" % f)
        return 1
    print("research_delta selftest: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
