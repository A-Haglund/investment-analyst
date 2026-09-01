#!/usr/bin/env python3
"""Portfolio review: triage, not twenty analyses.

A portfolio review asks one question per holding - "has anything changed
since I last formed a view?" - and only does real work where the answer is
yes. This module is the triage engine; it does not write the analysis.

THREE LAYERS (references/portfolio.md documents the same scheme):

  LAYER 1 - BREAKERS. For every holding with a stored thesis, read
  thesis_ledger.py's own current status for that issuer (no re-fetch: the
  ledger already carries whatever the last `--evaluate` run found, and
  freshness of THAT is exactly what layer 2's valuation_gate check is for).
  A thesis that is BROKEN short-circuits to EXIT, whatever the price says -
  this is the only layer that produces a final decision on its own.

  LAYER 2 - ALERTS. One cheap question per holding, for anything layer 1
  did not already decide: price outside the fair-value range recorded at
  the last review, a new report since then (mfn_news), short interest up
  materially (short_se), net insider selling (insider_se), valuation_gate
  calling the inputs stale, or no stored view at all. Alerts flag a holding
  for depth; they do not decide the action (only a fired breaker, or a
  clean pass, does that mechanically).

  LAYER 3 - DEPTH EVIDENCE. For every holding layer 1 or 2 flagged, this
  emits the evidence and the flags that justify further work, plus a
  proposed action where the mechanical signal is strong enough to name one,
  and a one-line reason. Where it cannot judge, it says so - it never
  invents a fair value, and it never invents a conviction it has no basis
  for. The STANDARD-depth write-up itself is the calling skill's job, not
  this script's.

A holding that clears layer 1 clean and trips no layer-2 alert gets
`HOLD - nothing has changed`, dated to when its thesis was last evaluated.
That date is what makes the answer honest rather than a skipped step.

COST BASIS - a hard rule (references/portfolio.md, "the disposition effect"):
`cost_per_share` (and its metadata, `cost_currency` / `acquired`) is used
ONLY inside the `performance` block, computed and printed strictly after the
per-holding action records. It never enters the decision logic, and it never
appears in the same record as a proposed action - printing "-34%" beside an
action lets an LLM reading the output rationalise "despite the decline"
instead of reasoning from the evidence.

USAGE
    python portfolio_review.py --layer 1                 # breakers only
    python portfolio_review.py --layer 2                 # breakers + alerts
    python portfolio_review.py --layer all                # + depth evidence (default)
    python portfolio_review.py --name my-portfolio --json
    python portfolio_review.py --selftest

EXIT CODES
    0  ok, no holding requires EXIT
    2  the portfolio could not be loaded (portfolio_store.py missing, or the
       named portfolio does not exist)
    4  at least one holding's thesis breaker fired - EXIT is on the table

Python 3 standard library only. Free, keyless. Every live check below shells
out to, or imports, a sibling script - nothing here re-implements FI's
registers, MFN's feed, or thesis_ledger's own breaker evaluation.
"""
import argparse
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _soft_load(name):
    try:
        return _load(name)
    except (Exception, SystemExit) as exc:  # sibling scripts raise SystemExit, which is not an Exception
        print("(portfolio_review: %s not available - %s)" % (name, exc), file=sys.stderr)
        return None


# thesis_ledger.py already exists and is the backbone this whole file leans
# on - soft-loaded anyway (never let an import wobble crash the CLI), but its
# absence is treated as the hard error it would actually be.
thesis_ledger = _soft_load("thesis_ledger")

# portfolio_store.py is being built in parallel by a sibling agent: it may not
# exist yet, or may still be mid-edit. Degrade with a clear message rather
# than crash - see load_portfolio() below.
portfolio_store = _soft_load("portfolio_store")

# Pure-function siblings, called directly (same pattern valuation_gate.py
# uses for quote.py): no CLI orchestration of their own to keep decoupled
# from, so importing is simpler and cheaper than a subprocess.
quote = _soft_load("quote")
mfn_news = _soft_load("mfn_news")
# insider_se is soft-loaded a second time here (fetching itself goes through
# the CLI - see fetch_insider_activity) purely to reuse its own
# NET BUYING/SELLING/FLAT threshold via direction_word(), rather than
# re-deciding independently what "material" means for the same number.
insider_se_mod = _soft_load("insider_se")


# ===========================================================================
# Triage thresholds - each one is a judgement call, so each is justified.
# Get these wrong and the tool is either useless (misses what moved) or
# unusable (full STANDARD-depth analysis on every holding, every time).
# ===========================================================================

# --- Layer-2 alert: price outside the fair-value range recorded at the last
# review --------------------------------------------------------------------
#
# A fair-value range is already an admission of uncertainty (see
# references/valuation.md: "a fair value carried to two decimals implies a
# precision the inputs cannot support"). Firing the alert the instant price
# pokes a single percent through either edge would mean this alert never
# stops firing near the boundary - useless noise on every holding trading
# near fair value, which is exactly where a well-priced holding should sit.
# 5% past the recorded edge is roughly one adverse trading day's noise for a
# typical Nordic large/mid cap and is small next to the width a competent
# range is given in the first place (springs.md worked example: a base-case
# range spans 10-20% top to bottom) - a breach has to clear the edge by more
# than ordinary noise before it is worth a human's attention.
FAIR_VALUE_BREACH_PCT = 5.0

# --- Layer-2 alert: disclosed short interest materially up ------------------
#
# short_se.py's own trend() reconstructs the >=0.5%-named short base at 30
# and 90 days back (references/portfolio.md: "short interest has moved
# materially"). FI discloses named positions in increments that make a
# single holder crossing a round threshold move the total by roughly a
# percentage point on its own, so a 30-day rise under ~1pp is indistinguishable
# from one holder's ordinary position management, not a thesis being
# expressed against the stock. The 90-day bar is wider (2pp) because it is
# the one built to catch a SLOW build a 30-day window would still read as
# flat - the two windows deliberately catch different shapes of move.
SHORT_INTEREST_RISE_PP_30D = 1.0
SHORT_INTEREST_RISE_PP_90D = 2.0

# --- Layer-2 alert: net insider selling -------------------------------------
#
# insider_se.py's own direction_word() already refuses to call a net
# "selling" unless it clears 2% of the period's gross flow - a net that
# rounds to nothing against gross is FLAT, not a weak signal (its own
# docstring: "a net that rounds to nothing against the period's gross flow
# is FLAT, not a weak signal. Two insiders crossing 100m each is not
# conviction."). Re-using that threshold rather than inventing a second one
# means the same number is never called "selling" here and "flat" there.
INSIDER_LOOKBACK_MONTHS = 6

# --- Layer 1: what counts as a "clean" thesis, vs. one that needs a look ---
#
# BROKEN is the short-circuit (EXIT, whatever the price says). WARNING (near
# a breaker, or breached but short of the persistence run) is not yet a
# breaker but is exactly the "something changed" layer 2 exists to catch
# mechanically for theses this ledger CAN evaluate machine-side - so it is
# flagged for depth, same as a layer-2 alert, rather than silently passed.
# UNKNOWN is explicitly never a synonym for "fine" in thesis_ledger's own
# vocabulary (its docstring: "Never used as a synonym for 'fine'") - an
# un-evaluable or never-evaluated thesis is withheld from the clean-pass path
# for the same reason an unreviewed holding is.
THESIS_BROKEN = {"BROKEN"}
THESIS_CLEAN = {"STABLE", "IMPROVING", "CONFIRMED"}
# everything else (WARNING, UNKNOWN, None) needs a look.


# ===========================================================================
# Small helpers
# ===========================================================================

def today():
    return datetime.date.today()


FV_RANGE_RE = re.compile(
    r"fair[\s\-]*value[^0-9\-]{0,25}?"
    r"([-−]?\d[\d.,]*(?:[    ]\d{3})*)"
    r"\s*(?:-|–|—|to)\s*"
    r"([-−]?\d[\d.,]*(?:[    ]\d{3})*)", re.IGNORECASE)


def _num(raw):
    """The one number parser (mfn_news.to_number): Swedish 1 234,5, English
    1,234.5 / 2,063.1, NBSP and narrow spaces, U+2212. Never a second one."""
    return mfn_news.to_number(raw) if mfn_news is not None else None


def parse_fair_value_range(*texts):
    """Best-effort scrape of a "fair value 190-215" style range out of free
    thesis text - a fallback ONLY, used when the holding carries no
    structured fair_value_low/fair_value_high (portfolio_store's schema).

    A holding's own freeform `note` is deliberately NOT among the sources
    scraped here (see alert_price_range): scraping a decision input out of a
    freeform field is exactly the silent-failure class this module exists to
    remove. This is a heuristic over prose, not a stored structured field.
    Returns (low, high) or None; never guesses when no range is written down
    anywhere - "it never invents a fair value" applies here as much as
    anywhere else in this script.
    """
    for text in texts:
        if not text:
            continue
        m = FV_RANGE_RE.search(text)
        if not m:
            continue
        lo, hi = _num(m.group(1)), _num(m.group(2))
        if lo is None or hi is None:
            continue
        if lo > hi:
            lo, hi = hi, lo
        return (lo, hi)
    return None


# ===========================================================================
# Portfolio loading
# ===========================================================================

def load_portfolio(name="default"):
    """Wraps portfolio_store.load(name). Returns (portfolio_dict_or_None, error)."""
    if portfolio_store is None or not hasattr(portfolio_store, "load"):
        return None, ("portfolio_store.py is not available yet (it is being built "
                       "in parallel) - nothing can be reviewed until it exists.")
    try:
        data = portfolio_store.load(name)
    except (Exception, SystemExit) as exc:  # sibling scripts raise SystemExit, which is not an Exception
        return None, "portfolio_store.load(%r) raised %s: %s" % (
            name, type(exc).__name__, exc)
    if not hasattr(data, "get") or "holdings" not in data:
        return None, ("portfolio_store.load(%r) did not return the expected "
                       "{'holdings': [...]} shape." % name)
    return data, None


# ===========================================================================
# LAYER 1 - breakers, read from the thesis ledger. Disk only, no network:
# whatever thesis_ledger's own --evaluate last found is what is read here.
# ===========================================================================

def thesis_identity(holding):
    return {"lei": holding.get("lei"), "isin": holding.get("isin")}


def thesis_view(holding):
    """The stored thesis-ledger view for one holding - current status of
    every active thesis, and whether any is BROKEN. Never touches the
    network: reads whatever JSON is already on disk under
    thesis_ledger.ledger_home() (THESIS_LEDGER_HOME env var, or
    ~/.investment-analyst/thesis-ledger).
    """
    out = {"has_ledger_entry": False, "ledger_key": None, "theses": [],
           "breaker_fired": False, "triggered": [], "warning": False,
           "unknown_or_unevaluated": False, "clean": False,
           "last_reviewed": None, "reason": None}

    if thesis_ledger is None:
        out["reason"] = "thesis_ledger.py is not available."
        return out

    identity = thesis_identity(holding)
    try:
        key = thesis_ledger.ledger_key(identity)
    except thesis_ledger.Ambiguous:
        out["reason"] = ("holding carries no LEI or ISIN - a thesis cannot be "
                          "looked up without an identity.")
        return out
    out["ledger_key"] = key

    led = thesis_ledger.read_ledger(key)
    if led is None:
        out["reason"] = "no ledger entry on file for this identity."
        return out
    out["has_ledger_entry"] = True

    active = [t for t in led.get("theses", []) if t.get("active", True)]
    if not active:
        out["reason"] = "ledger entry exists but carries no active thesis."
        return out

    reviewed_dates = []
    for t in active:
        status = t.get("status")
        entry = {"id": t["id"], "thesis": t.get("thesis"), "status": status,
                  "action": t.get("action"), "confidence": t.get("confidence"),
                  "status_since": t.get("status_since"),
                  "last_evaluated": t.get("last_evaluated"),
                  "triggered_breakers": t.get("triggered_breakers", [])}
        out["theses"].append(entry)
        if t.get("last_evaluated"):
            reviewed_dates.append(t["last_evaluated"])
        if status in THESIS_BROKEN:
            out["breaker_fired"] = True
            out["triggered"].extend(t.get("triggered_breakers", []))
        elif status == "WARNING":
            out["warning"] = True
        elif status not in THESIS_CLEAN:
            out["unknown_or_unevaluated"] = True

    out["clean"] = (not out["breaker_fired"] and not out["warning"]
                     and not out["unknown_or_unevaluated"])
    if reviewed_dates:
        out["last_reviewed"] = max(reviewed_dates)
    if out["breaker_fired"]:
        out["reason"] = "thesis breaker fired: %s" % "; ".join(out["triggered"][:2])
    elif out["warning"]:
        out["reason"] = "thesis status WARNING - near a breaker."
    elif out["unknown_or_unevaluated"]:
        out["reason"] = ("thesis status UNKNOWN or never evaluated - not "
                          "machine-confirmed as intact.")
    return out


# ===========================================================================
# LAYER 2 - alerts. Each fetch_* wraps exactly one sibling script and is the
# thing a test monkeypatches; each alert_* turns that data into a fired/not
# verdict against the thresholds above.
# ===========================================================================

def _run_cli_json(script, args, timeout=90):
    """Subprocess a sibling CLI that owns its own argparse/identity-resolution
    orchestration (the same reason valuation_gate.py subprocesses
    company_resolve.py rather than importing it) and parse its --json output.
    Returns (payload_or_None, error_or_None).
    """
    path = os.path.join(HERE, script)
    if not os.path.isfile(path):
        return None, "%s not found" % script
    try:
        proc = subprocess.run([sys.executable, path] + list(args) + ["--json"],
                               capture_output=True, text=True, timeout=timeout)
    except (Exception, SystemExit) as exc:  # sibling scripts raise SystemExit, which is not an Exception
        return None, "%s could not be run: %s" % (script, exc)
    out = (proc.stdout or "").strip()
    if not out:
        err = (proc.stderr or "").strip()
        return None, "%s produced no output%s" % (
            script, (": " + err[:200]) if err else "")
    try:
        return json.loads(out), None
    except json.JSONDecodeError:
        return None, "%s did not return valid JSON" % script


def fetch_quote(holding):
    if quote is None:
        return None, "quote.py not available"
    symbol = holding.get("symbol")
    if not symbol:
        return None, "no symbol on this holding"
    try:
        y = quote.from_yahoo(symbol)
    except (Exception, SystemExit) as exc:  # sibling scripts raise SystemExit, which is not an Exception
        return None, "quote lookup raised %s" % exc
    if not y or y.get("price") is None:
        return None, "no live quote returned for %s" % symbol
    return y, None


def fetch_news_since(holding):
    if mfn_news is None:
        return None, "mfn_news.py not available"
    name = holding.get("name") or holding.get("symbol")
    if not name:
        return None, "no company name to search MFN for"
    try:
        hits = mfn_news.search(name)
    except (Exception, SystemExit) as exc:  # sibling scripts raise SystemExit, which is not an Exception
        return None, "MFN search failed: %s" % exc
    if not hits:
        return None, "no MFN slug found for %r" % name
    slug = hits[0]["slug"]
    try:
        raw = mfn_news.fetch_company_pages(slug, pages=1)
    except (Exception, SystemExit) as exc:  # sibling scripts raise SystemExit, which is not an Exception
        return None, "MFN fetch failed for slug %s: %s" % (slug, exc)
    try:
        items = [mfn_news.flatten(i) for i in raw]
    except (Exception, SystemExit) as exc:  # sibling scripts raise SystemExit, which is not an Exception
        return None, "MFN item parsing failed: %s" % exc
    return {"slug": slug, "items": items}, None


def fetch_short_interest(holding, timeout=90):
    lei, isin = holding.get("lei"), holding.get("isin")
    if not lei and not isin:
        return None, "no LEI/ISIN - short interest cannot be pinned to an issuer"
    args = ["--lei", lei] if lei else ["--isin", isin]
    return _run_cli_json("short_se.py", args, timeout=timeout)


def fetch_insider_activity(holding, months=INSIDER_LOOKBACK_MONTHS, timeout=90):
    lei, isin = holding.get("lei"), holding.get("isin")
    if not lei and not isin:
        return None, "no LEI/ISIN - insider activity cannot be pinned to an issuer"
    args = (["--lei", lei] if lei else ["--isin", isin]) + ["--months", str(months)]
    return _run_cli_json("insider_se.py", args, timeout=timeout)


def fetch_valuation_gate(holding, timeout=90):
    name = holding.get("name") or holding.get("symbol")
    if not name:
        return None, "no company name/symbol to check"
    return _run_cli_json("valuation_gate.py", [name], timeout=timeout)


def _alert(code, fired, checked, detail, evidence=None):
    return {"code": code, "fired": bool(fired), "checked": bool(checked),
            "detail": detail, "evidence": evidence}


def alert_price_range(holding, thesis_view_result, price_info):
    """The recorded fair-value range, structured fields first.

    portfolio_store's fair_value_low/fair_value_high (in the holding's own
    quote currency) are the source of truth when both are set. Only when
    neither is on record does this fall back to parse_fair_value_range over
    the THESIS text - never over holding["note"]: a freeform note is not a
    decision input, and scraping one out of it is exactly the silent-failure
    class this module was rewritten to remove.
    """
    fv_low, fv_high = holding.get("fair_value_low"), holding.get("fair_value_high")
    if fv_low is not None and fv_high is not None:
        rng = (float(fv_low), float(fv_high))
        if rng[0] > rng[1]:
            rng = (rng[1], rng[0])
    else:
        fv_texts = [t.get("thesis") for t in thesis_view_result.get("theses", [])]
        rng = parse_fair_value_range(*fv_texts)
    if rng is None:
        return _alert("price_range", False, False,
                       "no fair-value range on record for this holding - "
                       "cannot check price against it.")
    if price_info is None:
        return _alert("price_range", False, False,
                       "no live price available - cannot check against the "
                       "recorded range %.2f-%.2f." % rng)
    price = price_info.get("price")
    lo, hi = rng
    hi_edge = hi * (1 + FAIR_VALUE_BREACH_PCT / 100.0)
    lo_edge = lo * (1 - FAIR_VALUE_BREACH_PCT / 100.0)
    if price > hi_edge:
        return _alert("price_range", True, True,
                       "price %.2f is more than %.0f%% above the recorded "
                       "fair-value range %.2f-%.2f." % (price, FAIR_VALUE_BREACH_PCT, lo, hi),
                       {"price": price, "range": [lo, hi], "side": "above"})
    if price < lo_edge:
        return _alert("price_range", True, True,
                       "price %.2f is more than %.0f%% below the recorded "
                       "fair-value range %.2f-%.2f." % (price, FAIR_VALUE_BREACH_PCT, lo, hi),
                       {"price": price, "range": [lo, hi], "side": "below"})
    return _alert("price_range", False, True,
                  "price %.2f is inside the recorded range %.2f-%.2f." % (price, lo, hi))


def alert_new_report(holding, last_reviewed):
    if not last_reviewed:
        return _alert("new_report", False, False,
                       "no last-reviewed date on record - cannot tell what counts "
                       "as 'new'.")
    data, err = fetch_news_since(holding)
    if err:
        return _alert("new_report", False, False, err)
    cutoff = str(last_reviewed)[:10]
    new_items = [i for i in data.get("items", [])
                 if (i.get("is_report") or i.get("regulatory")) and i.get("date", "")[:10] > cutoff]
    if new_items:
        titles = "; ".join("%s (%s)" % (i.get("title") or "?", i.get("date", "")[:10])
                            for i in new_items[:3])
        return _alert("new_report", True, True,
                      "%d report(s)/regulatory release(s) since the last review: %s"
                      % (len(new_items), titles),
                      {"slug": data.get("slug"), "items": new_items[:5]})
    return _alert("new_report", False, True,
                  "no report or regulatory release since the last review (%s)." % cutoff)


def alert_short_interest(holding):
    data, err = fetch_short_interest(holding)
    if err:
        return _alert("short_interest", False, False, err)
    trend = data.get("trend")
    if not trend:
        return _alert("short_interest", False, True,
                      "no short-interest history to reconstruct a trend from.")
    w30 = (trend.get("windows") or {}).get("30") or {}
    w90 = (trend.get("windows") or {}).get("90") or {}
    d30, d90 = w30.get("change_pp"), w90.get("change_pp")
    fired = ((d30 is not None and d30 >= SHORT_INTEREST_RISE_PP_30D)
             or (d90 is not None and d90 >= SHORT_INTEREST_RISE_PP_90D))
    detail = ("30d %s, 90d %s (named >=0.5%% base)."
              % ("%+.2fpp" % d30 if d30 is not None else "n/a",
                 "%+.2fpp" % d90 if d90 is not None else "n/a"))
    return _alert("short_interest", fired, True, detail,
                  {"change_30d_pp": d30, "change_90d_pp": d90,
                   "aggregate_pct": data.get("aggregate_pct")})


def alert_insider_selling(holding):
    data, err = fetch_insider_activity(holding)
    if err:
        return _alert("insider_selling", False, False, err)
    analysis = (data or {}).get("analysis")
    if not analysis:
        return _alert("insider_selling", False, True,
                      "no insider transactions on record in the lookback window.")
    disc = analysis.get("discretionary") or {}
    net = disc.get("net_value", 0.0)
    gross = disc.get("buy_value", 0.0) + disc.get("sell_value", 0.0)
    if insider_se_mod is not None and hasattr(insider_se_mod, "direction_word"):
        word = insider_se_mod.direction_word(net, gross)
    else:
        word = "FLAT" if gross <= 0 or abs(net) < 0.02 * gross else (
            "NET BUYING" if net > 0 else "NET SELLING")
    fired = (word == "NET SELLING")
    return _alert("insider_selling", fired, True,
                  "%s over the last %d months (net %.0f vs gross %.0f, %s)."
                  % (word, INSIDER_LOOKBACK_MONTHS, net, gross, analysis.get("currency") or "?"),
                  {"net_value": net, "gross_value": gross, "direction": word})


def alert_valuation_stale(holding):
    data, err = fetch_valuation_gate(holding)
    if err:
        return _alert("valuation_stale", False, False, err)
    passed = data.get("passed")
    detail = (data.get("report") or "").splitlines()[0] if data.get("report") else (
        "valuation inputs pass integrity checks." if passed
        else "valuation inputs fail integrity checks.")
    return _alert("valuation_stale", not passed, True, detail,
                  {"passed": passed, "states": data.get("states")})


def run_layer2(holding, view1, price_info=None):
    """One cheap question per holding, only for holdings layer 1 did not
    already decide (a BROKEN thesis is EXIT regardless, so running these five
    live checks on top of it would be work for an answer already given)."""
    return [
        alert_price_range(holding, view1, price_info),
        alert_new_report(holding, view1.get("last_reviewed")),
        alert_short_interest(holding),
        alert_insider_selling(holding),
        alert_valuation_stale(holding),
    ]


# ===========================================================================
# LAYER 3 - evidence + a proposed action, never the write-up itself.
#
# Only two mechanical signals are strong enough to name a final action on
# their own (references/portfolio.md's ADD/HOLD/TRIM/EXIT table):
#   - a fired breaker            -> EXIT (layer 1; the only layer that can
#                                    decide alone)
#   - price above the recorded fair-value range by more than the threshold,
#     on an otherwise-clean thesis -> TRIM ("Conviction is HOLD but valuation
#     has moved above range" is TRIM's own criterion in references/portfolio.md
#     - a WARNING/UNKNOWN thesis is not "conviction is HOLD", so it is checked
#     FIRST and must not be shadowed by a price signal underneath it)
# Everything else that gets flagged (new report, short interest up, net
# insider selling, stale valuation inputs, price below range, no stored
# view, a thesis in WARNING/UNKNOWN, or ANY check that could not even run)
# needs conviction and sizing judgement this script has no basis to
# fabricate - it flags, gives the evidence, and leaves the action blank for
# the calling skill's STANDARD-depth work. A check that could not run is not
# a check that passed: an alert with checked=False is treated exactly like a
# fired one for flagging purposes, never silently folded into a clean pass.
# A clean holding (every alert checked, none fired, thesis clean) gets HOLD,
# dated.
# ===========================================================================

def decide_action(view1, alerts):
    if view1.get("breaker_fired"):
        return "EXIT", "thesis breaker fired: %s" % (
            "; ".join(view1.get("triggered", [])[:2]) or "see thesis ledger")

    if not view1.get("has_ledger_entry") or not view1.get("theses"):
        return None, "no stored thesis for this holding - an unreviewed " \
                     "position is an alert, not a pass; run an initial analysis."

    fired = [a for a in alerts if a["fired"]]
    unchecked = [a for a in alerts if not a["checked"]]

    # WARNING/UNKNOWN is checked before the price signal below: a thesis that
    # is not machine-confirmed intact must never be shadowed by TRIM, and it
    # must never be silently dropped from the text report either.
    if view1.get("warning") or view1.get("unknown_or_unevaluated"):
        reasons = []
        if view1.get("warning"):
            reasons.append("thesis WARNING")
        if view1.get("unknown_or_unevaluated"):
            reasons.append("thesis UNKNOWN/unevaluated")
        reasons.extend(a["code"] for a in fired)
        if unchecked:
            reasons.append("could not check: %s" % ", ".join(a["code"] for a in unchecked))
        return None, ("flagged for depth review (%s) - conviction/sizing judgement "
                       "needed before naming ADD/HOLD/TRIM/EXIT." % ", ".join(reasons))

    price_alert = next((a for a in alerts if a["code"] == "price_range"), None)
    if price_alert and price_alert["fired"] and (price_alert.get("evidence") or {}).get("side") == "above":
        return "TRIM", price_alert["detail"]

    if fired or unchecked:
        reasons = [a["code"] for a in fired]
        if unchecked:
            reasons.append("could not check: %s" % ", ".join(a["code"] for a in unchecked))
        return None, ("flagged for depth review (%s) - conviction/sizing judgement "
                       "needed before naming ADD/HOLD/TRIM/EXIT." % ", ".join(reasons))

    last = view1.get("last_reviewed")
    when = (str(last)[:10] if last else "an unknown date")
    return "HOLD", "nothing has changed since the thesis was last evaluated (%s)." % when


def build_holding_record(holding, layer, use_layer2=True, price_info=None):
    """One holding's action record. Deliberately carries no cost_per_share,
    cost_currency or acquired - see the module docstring's COST BASIS rule."""
    record = {"lei": holding.get("lei"), "isin": holding.get("isin"),
              "name": holding.get("name"), "symbol": holding.get("symbol"),
              "quantity": holding.get("quantity")}

    # portfolio_store marks a holding "resolved": False when its name matched
    # no listed issuer. Such a holding has no identity - no LEI/ISIN can ever
    # be pinned to it, so no thesis, price, short-interest or insider check
    # can be either. thesis_view() below will (correctly) find no identity
    # and report "no LEI or ISIN", but decide_action's generic "run an
    # initial analysis" message is the wrong diagnosis for this: the fix is
    # not another analysis, it is re-adding the holding with an identity.
    identity_unresolved = holding.get("resolved") is False
    record["identity_unresolved"] = identity_unresolved

    view1 = thesis_view(holding)
    record["layer1"] = view1
    if layer == 1:
        return record

    run_alerts = (use_layer2 and not identity_unresolved
                  and not view1.get("breaker_fired")
                  and (view1.get("has_ledger_entry") and view1.get("theses")))
    alerts = run_layer2(holding, view1, price_info) if run_alerts else []
    record["layer2"] = {"alerts": alerts, "any_fired": any(a["fired"] for a in alerts)}
    if layer == 2:
        return record

    if identity_unresolved:
        action, reason = None, (
            "holding identity was never resolved to a listed issuer - no "
            "thesis, price, short-interest or insider check can be pinned to "
            "it; re-add the holding with its exact legal name, ticker or ISIN.")
    else:
        action, reason = decide_action(view1, alerts)
    record["action"] = action
    record["reason"] = reason
    record["flagged"] = (identity_unresolved or view1.get("breaker_fired")
                          or view1.get("warning") or view1.get("unknown_or_unevaluated")
                          or not view1.get("has_ledger_entry") or not view1.get("theses")
                          or any(a["fired"] for a in alerts)
                          or any(not a["checked"] for a in alerts))
    return record


# ===========================================================================
# Performance block - unrealised result only, computed and shown strictly
# after the action records, and never fed back into the decision logic.
# ===========================================================================

def build_performance(holding, price_info):
    cps = holding.get("cost_per_share")
    entry = {"lei": holding.get("lei"), "isin": holding.get("isin"),
             "name": holding.get("name"), "symbol": holding.get("symbol"),
             "quantity": holding.get("quantity"),
             "cost_per_share": cps, "cost_currency": holding.get("cost_currency"),
             "acquired": holding.get("acquired")}
    if cps is None or price_info is None or price_info.get("price") is None:
        entry["unrealised_pct"] = None
        entry["market_price"] = price_info.get("price") if price_info else None
        entry["note"] = "no cost basis and/or no live price - unrealised result not computable."
        return entry
    price = price_info["price"]
    entry["market_price"] = price
    entry["market_currency"] = price_info.get("currency")
    entry["unrealised_pct"] = round(100.0 * (price - cps) / cps, 2) if cps else None
    qty = holding.get("quantity")
    if qty is not None:
        entry["unrealised_value"] = round((price - cps) * qty, 2)
    if (price_info.get("currency") and holding.get("cost_currency")
            and price_info["currency"] != holding["cost_currency"]):
        entry["note"] = ("market currency (%s) differs from cost currency (%s) - "
                          "figure is NOT FX-adjusted." % (price_info["currency"],
                                                          holding["cost_currency"]))
    return entry


# ===========================================================================
# Printing (text mode)
# ===========================================================================

def _line(*parts):
    print("  ".join(str(p) for p in parts if p is not None))


def print_report(portfolio, records, layer, performance=None):
    print("=" * 80)
    print("PORTFOLIO REVIEW  %s   (%s, %s)   layer %s"
          % (portfolio.get("name") or "?", portfolio.get("account_type") or "?",
             portfolio.get("currency") or "?", layer))
    print("=" * 80)
    for r in records:
        v1 = r.get("layer1", {})
        head = "%-28s %-8s" % ((r.get("name") or r.get("symbol") or "?")[:28],
                                r.get("symbol") or "")
        if layer == 1:
            status = ("BROKEN" if v1.get("breaker_fired") else
                      "WARNING" if v1.get("warning") else
                      "UNKNOWN" if v1.get("unknown_or_unevaluated") else
                      "CLEAN" if v1.get("clean") else "NO THESIS")
            print("%s  thesis: %s" % (head, status))
            if v1.get("reason"):
                print("    %s" % v1["reason"])
            continue
        if layer == 2:
            l2 = r.get("layer2", {})
            fired = [a["code"] for a in l2.get("alerts", []) if a["fired"]]
            print("%s  alerts: %s" % (head, ", ".join(fired) if fired else "none"))
            for a in l2.get("alerts", []):
                mark = "!!" if a["fired"] else (".." if a["checked"] else "??")
                print("    %s %-16s %s" % (mark, a["code"], a["detail"]))
            continue
        action = r.get("action") or "(needs depth)"
        print("%s  ACTION: %-6s %s" % (head, action, r.get("reason") or ""))
    if layer == "all" and performance:
        print()
        print("-" * 80)
        print("UNREALISED RESULT (cost basis - never part of the decision above)")
        print("-" * 80)
        for p in performance:
            if p.get("unrealised_pct") is None:
                _line("%-28s" % (p.get("name") or p.get("symbol") or "?"), p.get("note"))
            else:
                _line("%-28s" % (p.get("name") or p.get("symbol") or "?"),
                      "%+.1f%%" % p["unrealised_pct"],
                      "(cost %.2f %s -> %.2f %s)"
                      % (p.get("cost_per_share"), p.get("cost_currency") or "?",
                         p.get("market_price"), p.get("market_currency") or "?"))


# ===========================================================================
# CLI
# ===========================================================================

def run(name, layer, as_json, fetch_prices=True):
    portfolio, err = load_portfolio(name)
    if err:
        if as_json:
            print(json.dumps({"ok": False, "error": err}, indent=2, ensure_ascii=False))
        else:
            print("PORTFOLIO REVIEW: FAILED")
            print("Reason: %s" % err)
        return 2

    holdings = portfolio.get("holdings", [])
    records = []
    performance = []
    for h in holdings:
        price_info = None
        if fetch_prices and layer in (2, "all"):
            price_info, _perr = fetch_quote(h)
        rec = build_holding_record(h, layer if layer in (1, 2) else "all",
                                    price_info=price_info)
        records.append(rec)
        if layer == "all":
            performance.append(build_performance(h, price_info))

    # Computed from the layer-1 view as well as `action`: build_holding_record
    # only sets `action` at layer "all" (layers 1 and 2 return early), so
    # relying on `action` alone made exit code 4 unreachable at --layer 1 and
    # --layer 2 - exactly the cheap breaker sweep a script would automate.
    # A fired breaker is EXIT regardless of which layer stopped early.
    any_exit = any(r.get("action") == "EXIT"
                   or r.get("layer1", {}).get("breaker_fired") for r in records)

    if as_json:
        payload = {"ok": True, "portfolio": portfolio.get("name"),
                   "account_type": portfolio.get("account_type"),
                   "currency": portfolio.get("currency"),
                   "layer": layer, "holdings": records}
        if layer == "all":
            payload["performance"] = performance
            payload["note"] = ("performance is unrealised result only; cost_per_share "
                                "never appears in the holdings/action records above.")
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    else:
        print_report(portfolio, records, layer, performance if layer == "all" else None)

    return 4 if any_exit else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="default", help="portfolio name (portfolio_store.py)")
    ap.add_argument("--layer", default="all", choices=("1", "2", "all"))
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--no-price", action="store_true", dest="no_price",
                    help="skip live price fetches (layer 2/3 alerts that need "
                         "no price still run)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(_selftest())

    layer = 1 if args.layer == "1" else (2 if args.layer == "2" else "all")
    sys.exit(run(args.name, layer, args.as_json, fetch_prices=not args.no_price))


# ===========================================================================
# --selftest - offline, no network, no portfolio_store dependency.
# ===========================================================================

def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _selftest():
    n = 0

    def check(cond, msg):
        nonlocal n
        _assert(cond, msg)
        n += 1

    rng = parse_fair_value_range("Base case fair value 190-215 (SEK), see model.")
    check(rng == (190.0, 215.0), "fair-value range parse: %r" % (rng,))

    rng2 = parse_fair_value_range("Quality compounder, no numbers here.")
    check(rng2 is None, "fair-value range must not be invented from prose with no range")

    rng3 = parse_fair_value_range("fair value range 220 to 180")
    check(rng3 == (180.0, 220.0), "fair-value range must normalise low/high order: %r" % (rng3,))

    # Breaker beats everything, including a TRIM-shaped price alert.
    v1_broken = {"breaker_fired": True, "triggered": ["ebit_margin < 15%"],
                 "has_ledger_entry": True, "theses": [{"id": "T1"}],
                 "warning": False, "unknown_or_unevaluated": False,
                 "last_reviewed": "2026-01-01T00:00:00Z"}
    alerts_trim_shaped = [_alert("price_range", True, True, "above range",
                                 {"side": "above"})]
    action, reason = decide_action(v1_broken, alerts_trim_shaped)
    check(action == "EXIT", "a fired breaker must force EXIT regardless of price: %r" % action)

    # No stored thesis at all -> flagged, not a clean pass, no invented action.
    v1_none = {"breaker_fired": False, "has_ledger_entry": False, "theses": [],
               "warning": False, "unknown_or_unevaluated": False, "last_reviewed": None}
    action2, reason2 = decide_action(v1_none, [])
    check(action2 is None, "an unreviewed holding must not get a decided action: %r" % action2)
    check("unreviewed" in reason2 or "no stored thesis" in reason2, "reason must say why")

    # Clean thesis, clean alerts -> HOLD, dated.
    v1_clean = {"breaker_fired": False, "has_ledger_entry": True,
                "theses": [{"id": "T1"}], "warning": False,
                "unknown_or_unevaluated": False, "last_reviewed": "2026-03-15T00:00:00Z"}
    action3, reason3 = decide_action(v1_clean, [_alert("price_range", False, True, "inside range")])
    check(action3 == "HOLD", "clean thesis + clean alerts must yield HOLD: %r" % action3)
    check("2026-03-15" in reason3, "HOLD reason must carry the last-reviewed date: %r" % reason3)

    # Price moved above range -> TRIM, on an otherwise clean thesis.
    action4, reason4 = decide_action(v1_clean, [_alert(
        "price_range", True, True, "price above range", {"side": "above"})])
    check(action4 == "TRIM", "price above the recorded range must propose TRIM: %r" % action4)

    # Price moved below range -> flagged, but NOT auto-ADD (needs conviction).
    action5, reason5 = decide_action(v1_clean, [_alert(
        "price_range", True, True, "price below range", {"side": "below"})])
    check(action5 is None, "price below range must not auto-decide ADD: %r" % action5)

    # cost_per_share must never appear in an action record.
    holding = {"lei": "5493001234567890ABCD", "isin": "SE0000000000",
               "name": "Test AB", "symbol": "TEST.ST", "quantity": 100,
               "cost_per_share": 42.0, "cost_currency": "SEK",
               "acquired": "2024-01-01", "note": "fair value 190-215"}
    record = build_holding_record(holding, "all", use_layer2=False)
    blob = json.dumps(record)
    check("cost_per_share" not in blob, "cost_per_share leaked into an action record")
    check("42.0" not in blob, "the cost-per-share figure leaked into an action record")

    # Performance block is the only place cost basis is allowed to appear.
    perf = build_performance(holding, {"price": 200.0, "currency": "SEK"})
    check(perf["cost_per_share"] == 42.0, "performance block must carry cost basis")
    check(abs(perf["unrealised_pct"] - 376.19) < 0.1, "unrealised %% miscalculated: %r" % perf)

    print("portfolio_review --selftest: %d checks passed" % n)
    return 0


if __name__ == "__main__":
    main()
