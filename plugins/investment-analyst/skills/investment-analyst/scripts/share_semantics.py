#!/usr/bin/env python3
"""Six different numbers get called "shares". Using the wrong one is silent.

The system had one generic share count. That is wrong, and for Swedish equities
it is dangerous, because at least six numbers compete for the name "shares
outstanding" and they routinely disagree by double-digit percentages:

    registered_shares        all classes, per the company's own "total number
                              of shares and votes" disclosure - the legal truth
    listed_shares             what the exchange has reference data for
    shares_outstanding        registered minus treasury - the economic count
    treasury_shares           held by the company itself, no economic claim
    weighted_average_shares   the denominator IFRS/US-GAAP uses for basic EPS
    diluted_shares            weighted average plus dilutive instruments

THE TWO RULES THAT MATTER

  1. MARKET CAP uses registered (or outstanding) shares ACROSS ALL CLASSES.
     Volvo A + Volvo B = 2,033,451,933. Pricing only the liquid B class
     understates market cap by roughly SEK 150bn.
  2. PER-SHARE EARNINGS use the WEIGHTED AVERAGE, never a point-in-time count.
     A count taken today divided into last year's profit is not this year's
     EPS and not last year's EPS - it is a number with no defined meaning.

UNLISTED CLASSES ARE INVISIBLE TO THE EXCHANGE. NIBE's listed B class is
1,782,936,128 shares; the registered total also includes an unlisted A class.
Whenever exactly one listed class is found this module raises
finfact.State.SHARE_COUNT_UNCERTAIN and points at the issuer's own "total
number of shares and votes" disclosure, because the exchange's reference data
cannot see what was never listed.

WHERE EACH FIELD COMES FROM

  registered_shares          corporate_actions.py --shares: the Nasdaq CNS
                              "Total number of voting rights and capital"
                              disclosure log. Origin group: issuer_disclosure.
  listed_shares               nordic_shares.py: Nasdaq Nordic reference data,
                              summed across every listed class of the issuer.
                              Origin group: exchange.
  shares_outstanding          registered_shares minus treasury_shares. No free
                              source publishes a point-in-time treasury
                              balance for a Nordic issuer, so this is normally
                              DATA NOT AVAILABLE and registered_shares is the
                              documented, explicit fallback for market cap.
  treasury_shares              same gap: ESEF only tags the cash-flow MOVEMENT
                              (PurchaseOfTreasuryShares), never the point-in-
                              time balance held. DATA NOT AVAILABLE by design,
                              not by oversight - the absence is reported, not
                              papered over.
  weighted_average_shares      esef_fundamentals-style extraction against IFRS
                              filings (filings.xbrl.org): either a directly
                              tagged concept (WeightedAverageShares and its
                              IFRS-standard synonyms) or, where ESEF Phase 1
                              leaves the EPS note untagged, DERIVED as
                              net_income / eps_basic and labelled as derived.
  diluted_shares               the diluted twin of the row above.

The exchange and the issuer's own disclosure are DIFFERENT ORIGIN GROUPS in
finfact.py's ORIGIN table - agreement between nordic_shares and
corporate_actions is genuine cross-origin verification, not the same fact
counted twice. corroborate() is used exactly for this.

Usage:
    python share_semantics.py "AB Volvo"
    python share_semantics.py "NIBE" --json
    python share_semantics.py "Atlas Copco" --market-cap
    python share_semantics.py "Volvo"          # ambiguous - AB Volvo or Volvo
                                                # Car - and correctly refuses

Python 3 stdlib only. Free, keyless. Windows console is reconfigured to UTF-8.
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import finfact
from finfact import FinancialFact, State, Verification, corroborate

# Sibling scripts are importable helpers, not subprocesses. A parallel agent
# may be mid-edit on one of them, so every import degrades this script to
# fewer sources rather than kill it - the same discipline corporate_actions.py
# uses for its own siblings.
try:
    import nordic_shares
except Exception:                                            # pragma: no cover
    nordic_shares = None
try:
    import corporate_actions
except Exception:                                            # pragma: no cover
    corporate_actions = None
try:
    import esef_fundamentals
except Exception:                                            # pragma: no cover
    esef_fundamentals = None
try:
    import company_resolve
except Exception:                                            # pragma: no cover
    company_resolve = None

TODAY = datetime.date.today()
NA = "DATA NOT AVAILABLE"

FIELD_ORDER = ["registered_shares", "listed_shares", "shares_outstanding",
               "treasury_shares", "weighted_average_shares", "diluted_shares"]

FIELD_DESCRIPTION = {
    "registered_shares": "all classes, per the issuer's own \"total number of "
                          "shares and votes\" disclosure - the legal truth",
    "listed_shares": "what the exchange has reference data for (listed "
                      "classes only)",
    "shares_outstanding": "registered minus treasury - the economic count",
    "treasury_shares": "held by the company itself, no economic claim",
    "weighted_average_shares": "the denominator IFRS/US-GAAP uses for basic "
                                "EPS",
    "diluted_shares": "weighted average plus dilutive instruments",
}

# IFRS taxonomy carries the point-in-time EPS on the face of the income
# statement, which ESEF Phase 1 mandates tagging - but the weighted-average
# SHARE COUNT behind it usually lives only in the EPS note, which Phase 1 does
# NOT mandate. Concrete filings (Evolution, NIBE) tag it under a company
# EXTENSION name rather than a standard ifrs-full concept, so both are tried.
WA_BASIC_CONCEPTS = [
    "WeightedAverageNumberOfOrdinarySharesOutstandingBasic",
    "WeightedAverageNumberOfSharesOutstandingBasic",
    "WeightedAverageNumberOfSharesOutstanding",
    "WeightedAverageShares",
]
WA_DILUTED_CONCEPTS = [
    "WeightedAverageNumberOfOrdinarySharesAdjustedForDilutiveEffectOfOrdinaryShares",
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "WeightedAverageNumberOfSharesOutstandingDiluted",
    "AdjustedWeightedAverageShares",
    "DilutedWeightedAverageShares",
]

# ---------------------------------------------------------------------------
# Company identity
# ---------------------------------------------------------------------------

class _NameHit(object):
    """Minimal stand-in for company_resolve.Candidate, used only when the
    nordic_shares-only fallback below (company_resolve unimportable or
    erroring) detects that a query matches more than one issuer. Exists so
    build_reconciliation()'s `[c.display() for c in contenders]` rendering
    works identically whichever code path found the ambiguity."""
    def __init__(self, hits):
        self.hits = hits

    def display(self):
        h = self.hits[0]
        return "%s (%s)" % (h.get("name") or "?", h.get("symbol") or "?")


def resolve_identity(query):
    """(record_or_None, contenders_or_None, reason, method).

    Tries company_resolve.py first - it is the only sibling that knows "Volvo"
    is two different listed issuers and refuses to guess between them. Falls
    back to nordic_shares.py's simpler name/root-symbol grouping if
    company_resolve is unavailable or errors, so a broken sibling degrades
    this script rather than killing it.
    """
    if company_resolve is not None:
        try:
            kind, needle = company_resolve.classify_query(query)
            entities = company_resolve.mfn_entities(needle)
            lines = company_resolve.nasdaq_lines(needle)
            cands = company_resolve.build_candidates(entities, lines)
            listed = [c for c in cands if c.lines or c.symbols]
            if listed:
                cands = listed
            winner, reason, confidence, contenders = company_resolve.resolve_candidates(
                cands, kind, needle)
            if winner is None:
                return None, contenders, reason, "company_resolve"
            classes = company_resolve.share_classes(winner)
            # An LEI resolved from MFN's issuer record (keyed by ISIN/orgnr, not
            # by name) is far safer to hand to ESEF than a fuzzy name search:
            # "Volvo" the display name is shared with Volvo Car, and a
            # name-substring search over the ESEF index cannot tell them apart.
            lei = sorted(winner.leis)[0] if winner.leis else None
            if not lei and classes:
                isin = classes[0].get("isin")
                if isin:
                    try:
                        lei = company_resolve.firds(isin).get("lei")
                    except Exception:
                        lei = None
            return ({"company_name": winner.display(), "classes": classes, "lei": lei,
                     "confidence": confidence}, None, reason, "company_resolve")
        except Exception as e:
            note = "company_resolve.py raised %s: %s - falling back to " \
                   "nordic_shares.py directly" % (type(e).__name__, e)
    else:
        note = "company_resolve.py not importable - using nordic_shares.py directly"

    if nordic_shares is None:
        return None, None, "no name-resolution source importable", "none"
    try:
        hits = nordic_shares.search(query)
    except SystemExit as e:
        return None, None, str(e), "nordic_shares"
    if not hits:
        return None, None, "%s: no listed Nordic issuer matched %r" % (NA, query), "nordic_shares"
    needle = query.lower()
    exact = [h for h in hits if needle in (h["name"] or "").lower()
             or needle in (h["symbol"] or "").lower()]
    pool = exact or hits
    # `chosen[0]` here used to pick the first hit regardless of how many
    # DIFFERENT issuers matched - the same failure company_resolve.py's brand
    # guard exists to stop ("Volvo" -> AB Volvo root VOLV, Volvo Car root
    # VOLCAR). Group by root symbol first: that collapses A/B share classes of
    # ONE issuer but not two different issuers, so refusing on >1 root is the
    # right test even without company_resolve's fuller name-compatibility
    # machinery.
    roots = {}
    for h in pool:
        roots.setdefault(nordic_shares.root_symbol(h["symbol"]), []).append(h)
    if len(roots) > 1:
        contenders = [_NameHit(group) for group in roots.values()]
        return None, contenders, (
            "%d distinct issuers on Nasdaq Nordic match %r (%s)"
            % (len(roots), query, note)), "nordic_shares"
    chosen = pool
    root = nordic_shares.root_symbol(chosen[0]["symbol"])
    same_root = [h for h in chosen if nordic_shares.root_symbol(h["symbol"]) == root]
    classes = []
    for c in same_root:
        try:
            s = nordic_shares.summary(c["orderbookId"])
        except SystemExit:
            s = {}
        classes.append({"symbol": c["symbol"], "isin": s.get("isin") or c.get("isin"),
                        "shares": s.get("shares"), "market_cap": s.get("market_cap"),
                        "segment": s.get("segment"), "orderbook_id": c["orderbookId"],
                        "cross_listed_as": [], "exchange_note": s.get("note") or ""})
    rec = {"company_name": chosen[0]["name"], "classes": classes, "lei": None,
           "confidence": None, "fallback_note": note}
    return rec, None, "single Nasdaq name match (unlisted classes not modelled here)", "nordic_shares"


# ---------------------------------------------------------------------------
# listed_shares - nordic_shares.py, origin group "exchange"
# ---------------------------------------------------------------------------

def listed_shares_fact(classes):
    """(fact_or_None, n_classes, missing_symbols).

    A class whose Nasdaq summary() lookup returned no share count must not be
    silently treated as zero: source_detail used to name EVERY listed class
    ("%d listed class(es): A, B") while the sum behind it quietly dropped
    whichever class had no count, understating the total with no visible
    sign of it. When fewer classes contributed a count than were found, the
    sum is now explicitly marked PARTIAL - naming the missing symbols in
    source_detail and downgrading verification to INCOMPLETE - the same
    discipline company_resolve.assemble() applies to total_listed_shares.
    """
    have = [c for c in classes if c.get("shares")]
    missing = [c.get("symbol") or "?" for c in classes if not c.get("shares")]
    n_classes = len(classes)
    fact = None
    if have:
        total = sum(c["shares"] for c in have)
        partial = bool(missing)
        if partial:
            source_detail = ("Nasdaq Nordic reference data, %d of %d listed class(es) "
                             "summed: %s (missing a share count for %s)"
                             % (len(have), n_classes,
                                ", ".join(c["symbol"] or "?" for c in have),
                                ", ".join(missing)))
        else:
            source_detail = ("Nasdaq Nordic reference data, %d listed class(es): %s"
                             % (n_classes, ", ".join(c["symbol"] or "?" for c in classes)))
        fact = FinancialFact(
            "listed_shares", total, source="nasdaq_reference",
            period_end=TODAY, publication_date=TODAY, unit="shares",
            source_detail=source_detail,
            verification=Verification.INCOMPLETE if partial else Verification.SINGLE_SOURCE,
            freshness_key="shares_outstanding")
    return fact, n_classes, missing


# ---------------------------------------------------------------------------
# registered_shares - corporate_actions.py --shares, origin group
# "issuer_disclosure" (the Nasdaq CNS "Total number of voting rights and
# capital" category, which every Swedish issuer is legally required to file)
# ---------------------------------------------------------------------------

VOTES_NOT_SHARES = re.compile(r"\bnumber of votes\b", re.I)


def registered_shares_fact(query, bodies=40, pages=3):
    if corporate_actions is None:
        return None, {"reason": "corporate_actions.py not importable"}
    try:
        hits = corporate_actions.resolve_company(query)
    except Exception as e:
        return None, {"reason": "resolve_company failed: %s" % e}
    if not hits:
        return None, {"reason": "%s: no Nasdaq CNS company matched %r" % (NA, query)}
    company = hits[0]["company"]
    try:
        events, unparsed_older = corporate_actions.share_history(company, fetch_bodies=bodies)
    except Exception as e:
        return None, {"reason": "share_history failed: %s" % e, "cns_company": company}

    parsed = [e for e in events if e.get("total_shares")]
    if not parsed:
        return None, {"reason": "%d disclosure(s) found for %r under \"Total number "
                                 "of voting rights and capital\", none parsed to a "
                                 "share count. Read the disclosures directly."
                                 % (len(events), company),
                       "cns_company": company, "n_disclosures": len(events)}

    latest = parsed[-1]
    caveat = None
    if latest.get("total_sentence") and VOTES_NOT_SHARES.search(latest["total_sentence"]) \
            and "share" not in latest["total_sentence"].lower():
        caveat = ("parsed from a sentence about VOTES, not shares outstanding - for "
                  "an issuer whose classes carry unequal votes per share this figure "
                  "may not equal the total share count")

    pub_date = latest["date"][:10] if latest.get("date") else None
    if pub_date is None:
        return None, {"reason": "latest parsed disclosure for %r has no publication "
                                 "date on record" % company, "cns_company": company,
                       "n_disclosures": len(events)}
    fact = FinancialFact(
        "registered_shares", latest["total_shares"], source="nasdaq_cns",
        period_end=pub_date, publication_date=pub_date, unit="shares",
        source_detail="%s (%s)" % (latest["title"], company),
        note=(latest.get("total_sentence") or "")[:300] + (
            "  !! " + caveat if caveat else ""),
        freshness_key="shares_outstanding")
    detail = {"cns_company": company, "n_disclosures": len(events),
              "n_unparsed_older": unparsed_older,
              "n_parsed": len(parsed), "caveat": caveat,
              "disclosure_date": pub_date, "disclosure_title": latest["title"]}
    return fact, detail


# ---------------------------------------------------------------------------
# weighted_average_shares / diluted_shares - ESEF (IFRS)
# ---------------------------------------------------------------------------

# Legal-form tokens stripped before comparing two company names. A plain
# lower().startswith() check is fooled by "Aktiebolaget Volvo" (the legal
# form comes FIRST in Swedish), which is exactly the collision this exists to
# catch: without stripping it, "Aktiebolaget Volvo" and "Volvo Car AB" score
# on different tiers (contains vs startswith) and one wins outright with no
# hint that a same-brand company was ever in the running.
_LEGAL_STOPWORDS = re.compile(
    r"\b(ab|abp|oyj|oy|plc|asa|a/s|as|publ|aktiebolaget|aktiebolag|holding|"
    r"holdings|group|groups|international|nv|sa|ag|spa|corporation|corp|"
    r"company|the|koncern|bolaget|inc|ltd|limited)\b", re.I)


def _core_tokens(name):
    s = re.sub(r"[^0-9A-Za-z ]+", " ", (name or "").lower())
    s = _LEGAL_STOPWORDS.sub(" ", s)
    return frozenset(s.split())


def _closest_hits(hits, query, name_key="name"):
    """Partition `hits` into (exact, near) by name closeness to `query`,
    mirroring peers_se.py's lei_for(): exact normalised-token equality first,
    then a bidirectional token-subset match ("Ericsson" inside
    "Telefonaktiebolaget LM Ericsson"). Recency plays no part - see
    _esef_lei's docstring for why that was the wrong axis.
    """
    qtoks = _core_tokens(query)
    exact, near = [], []
    for h in hits:
        ntoks = _core_tokens(h.get(name_key))
        if not ntoks:
            continue
        if ntoks == qtoks:
            exact.append(h)
        elif qtoks and (qtoks <= ntoks or ntoks <= qtoks):
            near.append(h)
    return exact, near


def _refuse_if_ambiguous(pool, query, extra_country=False):
    distinct = {h["lei"] for h in pool}
    if len(distinct) <= 1:
        return None
    if extra_country:
        names = ", ".join(sorted("%s (%s, %s)" % (h["name"], h["lei"], h.get("country") or "?")
                                 for h in pool))
    else:
        names = ", ".join(sorted("%s (%s)" % (h["name"], h["lei"]) for h in pool))
    return ("%d issuers match %r equally closely by name (%s); a name search "
           "cannot tell them apart - pass a precise legal name or LEI instead."
           % (len(distinct), query, names))


def _esef_lei(company_name):
    if esef_fundamentals is None:
        return None, None
    try:
        hits = esef_fundamentals.search_index(company_name, "SE")
    except Exception:
        hits = []
    if not hits:
        for country in ("NO", "DK", "FI"):
            try:
                hits = esef_fundamentals.search_index(company_name, country)
            except Exception:
                hits = []
            if hits:
                break
    if hits:
        # A name-substring search over the ESEF index returns every issuer
        # whose name CONTAINS the query - "Volvo" matches both "Aktiebolaget
        # Volvo" and "Volvo Car AB". Picking `max(hits, key=...latest)`
        # resolved by whose annual report happened to be filed most
        # recently, which has nothing to do with which company was meant.
        # Rank by name closeness instead (exact match, then token-subset -
        # same test as peers_se.lei_for), and refuse rather than guess when
        # more than one distinct issuer ties for the closest match.
        exact, near = _closest_hits(hits, company_name)
        pool = exact or near
        if pool:
            reason = _refuse_if_ambiguous(pool, company_name)
            if reason:
                return None, reason
            return pool[0]["lei"], None
        # Neither test scored a single hit (a substring match that landed
        # mid-token) - fall through to GLEIF rather than guess among `hits`.
    try:
        gl = esef_fundamentals.search_lei(company_name)
    except Exception:
        gl = []
    if not gl:
        return None, "%s: no LEI matched %r in GLEIF either" % (NA, company_name)
    # Same discipline for the GLEIF fallback: `gl[0]` picked whichever entity
    # GLEIF happened to list first, with no name-closeness check at all.
    gexact, gnear = _closest_hits(gl, company_name)
    gpool = gexact or gnear or gl
    reason = _refuse_if_ambiguous(gpool, company_name, extra_country=True)
    if reason:
        return None, reason
    best = gpool[0]
    country = best.get("country")
    note = esef_fundamentals.NOT_COVERED.get(country)
    if note:
        return None, ("GLEIF has an LEI (%s) but %s is not in the ESEF index (%s)"
                      % (best["lei"], country, note))
    return None, ("GLEIF has an LEI (%s) but no ESEF filing is indexed for it - "
                  "likely a First North / Spotlight issuer with no ESEF mandate"
                  % best["lei"])


def _esef_filings_with_date(lei, limit=1):
    """Like esef_fundamentals.list_filings but keeps date_added, which
    list_filings drops - the only publication-date proxy this feed offers."""
    params = {"filter[entity.identifier]": lei,
             "page[size]": str(max(limit, 10)), "sort": "-period_end"}
    data = esef_fundamentals.get_json(
        esef_fundamentals.FILINGS_API + "?" + urllib.parse.urlencode(params))
    out = []
    for f in data.get("data", []):
        a = f["attributes"]
        if not a.get("json_url"):
            continue
        out.append({"period_end": a["period_end"], "json_url": a["json_url"],
                    "fxo_id": a.get("fxo_id"), "date_added": (a.get("date_added") or "")[:10]})
    return out[:limit]


def weighted_average_and_diluted_facts(company_name, filings=1, known_lei=None):
    """Returns dict with 'basic' and 'diluted' -> (FinancialFact_or_None, detail).

    known_lei, when given, comes from MFN's issuer record or ESMA FIRDS - both
    keyed by ISIN/orgnr, not by name - and is used as-is. A name-substring
    search over the ESEF index is a LAST resort: "Volvo" the display name is
    shared with the separately listed Volvo Car, and fuzzy-matching that
    string against the ESEF index cannot tell the two apart.
    """
    out = {"basic": (None, {}), "diluted": (None, {})}
    if esef_fundamentals is None:
        out["basic"] = (None, {"reason": "esef_fundamentals.py not importable"})
        out["diluted"] = out["basic"]
        return out

    lei, why_not = (known_lei, None) if known_lei else _esef_lei(company_name)
    if not lei:
        reason = why_not or ("%s: could not resolve an LEI for %r" % (NA, company_name))
        out["basic"] = (None, {"reason": reason})
        out["diluted"] = (None, {"reason": reason})
        return out

    try:
        recs = _esef_filings_with_date(lei, filings)
    except Exception as e:
        reason = "ESEF filings.xbrl.org lookup failed: %s" % e
        out["basic"] = (None, {"reason": reason, "lei": lei})
        out["diluted"] = (None, {"reason": reason, "lei": lei})
        return out
    if not recs:
        reason = "%s: LEI %s has no indexed ESEF filing" % (NA, lei)
        out["basic"] = (None, {"reason": reason, "lei": lei})
        out["diluted"] = (None, {"reason": reason, "lei": lei})
        return out

    f = recs[0]
    try:
        doc = esef_fundamentals.get_json(esef_fundamentals.FILINGS_BASE + f["json_url"])
    except Exception as e:
        reason = "could not fetch %s: %s" % (f["json_url"], e)
        out["basic"] = (None, {"reason": reason, "lei": lei})
        out["diluted"] = (None, {"reason": reason, "lei": lei})
        return out
    facts = esef_fundamentals.extract(doc)
    pub = f.get("date_added") or None

    def build(names, label):
        found = esef_fundamentals.pick(facts, names, True)
        if not found:
            return None, {}
        end = sorted(found)[-1]
        val, unit, concept = found[end]
        fact = FinancialFact(
            label, val, source="esef", period_end=end, publication_date=pub,
            unit="shares", source_detail="%s (tag %s, filing %s)" % (company_name, concept, f["fxo_id"]),
            freshness_key="annual_financials")
        return fact, {"concept": concept, "period_end": end, "fxo_id": f["fxo_id"], "lei": lei}

    basic_fact, basic_detail = build(WA_BASIC_CONCEPTS, "weighted_average_shares")
    diluted_fact, diluted_detail = build(WA_DILUTED_CONCEPTS, "diluted_shares")

    # Phase 1 mandates the EPS figures on the face of the income statement but
    # not the note disclosing the share count behind them, so the tag search
    # above is frequently empty. Where it is, derive the count the same way an
    # analyst would by hand: shares = net income attributable / EPS. This is
    # NOT a tagged fact - it is arithmetic on two tagged facts - and is labelled
    # and confidence-penalised as such (verification=UNVERIFIED).
    ni = esef_fundamentals.pick(facts, esef_fundamentals.CONCEPTS["net_income"], True)
    eb = esef_fundamentals.pick(facts, esef_fundamentals.CONCEPTS["eps_basic"], True)
    ed = esef_fundamentals.pick(facts, esef_fundamentals.CONCEPTS["eps_diluted"], True)

    if basic_fact is None:
        common = sorted(set(ni) & set(eb))
        if common:
            end = common[-1]
            n, _, nconcept = ni[end]
            e, _, econcept = eb[end]
            if e:
                derived = n / e
                basic_fact = FinancialFact(
                    "weighted_average_shares", derived, source="esef", period_end=end,
                    publication_date=pub, unit="shares",
                    verification=Verification.UNVERIFIED,
                    source_detail="%s: derived %s / %s (not a directly tagged share "
                                  "count)" % (company_name, nconcept, econcept),
                    note="DERIVED, not tagged: net_income / eps_basic. Treat as an "
                         "approximation - it silently mis-states if EPS is computed "
                         "on profit attributable to a different base than the "
                         "net_income concept used here (material NCI, for example).",
                    freshness_key="annual_financials")
                basic_detail = {"derived_from": [nconcept, econcept], "period_end": end,
                                "fxo_id": f["fxo_id"], "lei": lei}
    if diluted_fact is None:
        common = sorted(set(ni) & set(ed))
        if common:
            end = common[-1]
            n, _, nconcept = ni[end]
            e, _, econcept = ed[end]
            if e:
                derived = n / e
                diluted_fact = FinancialFact(
                    "diluted_shares", derived, source="esef", period_end=end,
                    publication_date=pub, unit="shares",
                    verification=Verification.UNVERIFIED,
                    source_detail="%s: derived %s / %s (not a directly tagged share "
                                  "count)" % (company_name, nconcept, econcept),
                    note="DERIVED, not tagged: net_income / eps_diluted. Same caveat "
                         "as weighted_average_shares.",
                    freshness_key="annual_financials")
                diluted_detail = {"derived_from": [nconcept, econcept], "period_end": end,
                                  "fxo_id": f["fxo_id"], "lei": lei}

    if basic_fact is None:
        basic_detail = {"reason": "%s: neither a tagged weighted-average share count "
                                  "nor (net_income, eps_basic) both present in filing %s"
                                  % (NA, f["fxo_id"]), "lei": lei}
    if diluted_fact is None:
        diluted_detail = {"reason": "%s: neither a tagged diluted share count nor "
                                    "(net_income, eps_diluted) both present in filing %s"
                                    % (NA, f["fxo_id"]), "lei": lei}

    out["basic"] = (basic_fact, basic_detail)
    out["diluted"] = (diluted_fact, diluted_detail)
    return out


# ---------------------------------------------------------------------------
# shares_outstanding / treasury_shares - the two fields no free Nordic source
# publishes as a point-in-time balance. Reported absent, on purpose.
# ---------------------------------------------------------------------------

def treasury_and_outstanding(registered_fact):
    treasury_reason = ("%s: no free source publishes a point-in-time treasury-share "
                       "balance for a Nordic issuer. ESEF tags only the cash-flow "
                       "MOVEMENT (PurchaseOfTreasuryShares / SaleOrIssueOfTreasuryShares), "
                       "never the balance held. Read the equity note in the annual "
                       "report for the actual figure." % NA)
    if registered_fact is None:
        outstanding_reason = ("%s: registered_shares itself is unavailable, so "
                              "registered minus treasury cannot be computed" % NA)
    else:
        outstanding_reason = ("%s: treasury_shares is unavailable, so registered_shares "
                              "cannot be reduced to the economic count. registered_shares "
                              "is the documented fallback for market cap (spec §9) when "
                              "treasury is immaterial or unknown." % NA)
    return ({"reason": treasury_reason}, {"reason": outstanding_reason})


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def _field_state(fact, verification):
    if fact is None:
        return State.DATA_NOT_AVAILABLE
    if verification == Verification.CONFLICT:
        return State.DATA_CONFLICT
    stale, _days, _limit = fact.staleness()
    if stale:
        return State.DATA_STALE
    return State.OK


def build_reconciliation(query, bodies=40, esef_filings=1):
    identity, contenders, reason, method = resolve_identity(query)
    if identity is None:
        return {"query": query, "resolved": False,
                "state": State.COMPANY_IDENTITY_AMBIGUOUS.value
                        if contenders else State.DATA_NOT_AVAILABLE.value,
                "reason": reason,
                "candidates": [c.display() for c in (contenders or [])]}

    classes = identity["classes"]
    company_name = identity["company_name"]

    listed_fact, n_listed, missing_listed = listed_shares_fact(classes)
    registered_fact, registered_detail = registered_shares_fact(query, bodies=bodies)
    wa = weighted_average_and_diluted_facts(company_name, filings=esef_filings,
                                            known_lei=identity.get("lei"))
    weighted_fact, weighted_detail = wa["basic"]
    diluted_fact, diluted_detail = wa["diluted"]

    treasury_detail, outstanding_detail = treasury_and_outstanding(registered_fact)

    # Cross-check: exchange (listed_shares) vs issuer disclosure
    # (registered_shares) are DIFFERENT ORIGIN GROUPS - agreement here is a
    # real, independent confirmation, not the same fact counted twice.
    reg_vs_listed_facts = [f for f in (registered_fact, listed_fact) if f is not None]
    reg_vs_listed_verif, reg_vs_listed_detail = corroborate(reg_vs_listed_facts, tolerance=0.005)

    # Cross-check: a directly tagged weighted-average count vs the value
    # derived from net_income/EPS. Both come from the SAME filing (origin
    # group "issuer_filing"), so agreement is CROSS_CHECKED at best, never
    # VERIFIED - it confirms transcription, not the underlying number.
    wa_direct_vs_derived = None
    if weighted_fact is not None and weighted_fact.verification == Verification.UNVERIFIED:
        wa_direct_vs_derived = {"note": "only a derived estimate exists; no directly "
                                        "tagged count to cross-check it against"}

    fields = {
        "registered_shares": {"fact": registered_fact, "detail": registered_detail,
                              "state": _field_state(registered_fact, Verification.SINGLE_SOURCE)},
        "listed_shares": {"fact": listed_fact, "detail": {"n_listed_classes": n_listed,
                          "classes": classes, "missing_share_count_for": missing_listed},
                          "state": _field_state(listed_fact,
                                   listed_fact.verification if listed_fact
                                   else Verification.SINGLE_SOURCE)},
        "shares_outstanding": {"fact": None, "detail": outstanding_detail,
                               "state": State.DATA_NOT_AVAILABLE},
        "treasury_shares": {"fact": None, "detail": treasury_detail,
                           "state": State.DATA_NOT_AVAILABLE},
        "weighted_average_shares": {"fact": weighted_fact, "detail": weighted_detail,
                                    "state": _field_state(weighted_fact,
                                             weighted_fact.verification if weighted_fact else None)},
        "diluted_shares": {"fact": diluted_fact, "detail": diluted_detail,
                          "state": _field_state(diluted_fact,
                                   diluted_fact.verification if diluted_fact else None)},
    }
    # Override registered_shares' state with the real corroboration outcome.
    fields["registered_shares"]["state"] = _field_state(registered_fact, reg_vs_listed_verif)
    fields["registered_shares"]["verification"] = reg_vs_listed_verif.value
    fields["listed_shares"]["verification"] = (
        Verification.VERIFIED.value if reg_vs_listed_verif == Verification.VERIFIED
        else (listed_fact.verification.value if listed_fact
              else Verification.SINGLE_SOURCE.value))

    flags = []
    if listed_fact is not None and missing_listed:
        flags.append({"state": State.SHARE_COUNT_UNCERTAIN.value,
                      "detail": ("listed_shares is a PARTIAL sum for %s: no share "
                                "count for %s. It UNDERSTATES the true listed total - "
                                "do not use it for market cap until the missing "
                                "class's count is confirmed by hand."
                                % (company_name, ", ".join(missing_listed)))})
    if n_listed == 1:
        msg = ("only one listed class was found for %s. An UNLISTED class would be "
              "invisible to Nasdaq Nordic's reference data - confirm against the "
              "issuer's own \"total number of shares and votes\" disclosure before "
              "trusting listed_shares as the total." % company_name)
        if registered_fact is not None:
            if reg_vs_listed_verif == Verification.VERIFIED:
                msg += (" The issuer's own disclosure (%s, %s) independently agrees "
                       "with the listed total to within 0.5%%, which is reassuring but "
                       "not proof - the disclosure could itself be stale."
                       % (registered_fact.value, registered_fact.period_end))
            else:
                msg += (" The issuer's own disclosure (%s shares, %s) DISAGREES with "
                       "the listed total (%s shares) by %s shares (%.1f%%) - strong "
                       "evidence of exactly this: an unlisted class, a stale "
                       "disclosure, or both. Prefer the disclosure; if it predates "
                       "recent splits, neither number is safe to use as-is."
                       % (fmt_int(registered_fact.value), registered_fact.period_end,
                          fmt_int(listed_fact.value if listed_fact else None),
                          fmt_int(abs(registered_fact.value - (listed_fact.value if listed_fact else 0))),
                          100.0 * abs(registered_fact.value - (listed_fact.value if listed_fact else 0))
                          / registered_fact.value))
        else:
            msg += (" The issuer's own disclosure could not be obtained (%s) - this "
                   "flag cannot be resolved automatically." % registered_detail.get("reason", NA))
        flags.append({"state": State.SHARE_COUNT_UNCERTAIN.value, "detail": msg})

    usage = {
        "market_cap_should_use": "registered_shares (all classes) - falls back to "
                                 "listed_shares, explicitly flagged as a floor, when "
                                 "registered_shares is unavailable or stale",
        "per_share_metrics_should_use": "weighted_average_shares for basic EPS / "
                                        "per-share figures, diluted_shares for "
                                        "diluted EPS",
        "never_use_for_per_share_metrics": ["registered_shares", "listed_shares",
                                            "shares_outstanding"],
        "never_use_for_market_cap": ["weighted_average_shares", "diluted_shares"],
    }

    return {"query": query, "resolved": True, "company_name": company_name,
           "resolution_method": method, "resolution_note": reason,
           "n_listed_classes": n_listed, "classes": classes,
           "fields": fields, "flags": flags,
           "cross_checks": {"registered_vs_listed": reg_vs_listed_detail,
                            "weighted_average_direct_vs_derived": wa_direct_vs_derived},
           "usage_guidance": usage,
           "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()}


# ---------------------------------------------------------------------------
# Market cap - spec §9: compute it correctly and show the working
# ---------------------------------------------------------------------------

def compute_market_cap(rec):
    classes = rec["classes"]
    priced = [c for c in classes if c.get("market_cap") and c.get("shares")]
    listed_cap = sum(c["market_cap"] for c in priced)
    listed_shares = sum(c["shares"] for c in classes if c.get("shares"))
    working = ["listed classes priced individually (each class trades at its own "
              "price - summing per-class market cap, not one price times a total "
              "share count, because A and B shares do not trade at the same price):"]
    for c in classes:
        if c.get("market_cap") and c.get("shares"):
            px = c["market_cap"] / c["shares"]
            working.append("  %-10s %14s shares  x  %10.2f  =  %20s"
                           % (c["symbol"], fmt_int(c["shares"]), px, fmt_int(c["market_cap"])))
        else:
            working.append("  %-10s %s" % (c["symbol"], NA))
    working.append("  listed total".ljust(10) + " " * 15 + "= " + fmt_int(listed_cap))

    reg = rec["fields"]["registered_shares"]["fact"]
    result = {"listed_market_cap": listed_cap, "listed_shares": listed_shares,
             "working": working}

    if reg is None or not listed_shares:
        result["total_market_cap"] = listed_cap
        result["basis"] = ("listed classes only - registered_shares is %s, so this "
                          "is a FLOOR, not confirmed as the true market cap."
                          % (NA if reg is None else "unusable"))
        return result

    extra = reg.value - listed_shares
    if abs(extra) <= listed_shares * 0.001:
        result["total_market_cap"] = listed_cap
        result["basis"] = ("registered_shares (%s, %s) agrees with the listed total "
                          "to within 0.1%% - no material unlisted balance. Listed "
                          "market cap stands as the figure." % (fmt_int(reg.value), reg.period_end))
        return result

    if extra < 0:
        # registered_shares is LOWER than the current listed total: the
        # disclosure predates a split/issuance the exchange has already
        # priced in, or the parser mis-read the disclosure (see the "votes,
        # not shares" caveat on the registered_shares field). Either way, it
        # cannot be netted against the listed count - it is stale, not proof
        # of fewer shares. Report the listed floor, do not claim agreement.
        result["total_market_cap"] = listed_cap
        result["basis"] = ("registered_shares (%s, %s) is LOWER than the listed total "
                          "(%s) by %s shares (%.1f%%) - the disclosure is almost "
                          "certainly stale (it predates a split/issuance) or was "
                          "mis-parsed, not evidence the true count is smaller. "
                          "Reporting the listed total as a floor; treat it with the "
                          "same caution as the SHARE_COUNT_UNCERTAIN flag above."
                          % (fmt_int(reg.value), reg.period_end, fmt_int(listed_shares),
                             fmt_int(-extra), 100.0 * -extra / listed_shares))
        return result

    proxy = max(priced, key=lambda c: c["market_cap"]) if priced else None
    if proxy is None:
        result["total_market_cap"] = listed_cap
        result["basis"] = ("registered_shares exceeds the listed total by %s shares, "
                          "consistent with an unlisted class, but no listed class has "
                          "a price to value it with. Reporting the listed floor only."
                          % fmt_int(extra))
        return result

    proxy_price = proxy["market_cap"] / proxy["shares"]
    unlisted_value = extra * proxy_price
    total = listed_cap + unlisted_value
    working.append("")
    working.append("unlisted balance: registered_shares (%s, %s) minus listed total "
                   "(%s) = %s shares not on any order book"
                   % (fmt_int(reg.value), reg.period_end, fmt_int(listed_shares), fmt_int(extra)))
    working.append("  priced at %s's own price (%.2f) as the best available proxy - "
                   "this ASSUMES the unlisted class trades economically in line with "
                   "the listed one, ignoring any control premium or illiquidity discount:"
                   % (proxy["symbol"], proxy_price))
    working.append("  %14s shares  x  %10.2f  =  %20s (unlisted, estimated)"
                   % (fmt_int(extra), proxy_price, fmt_int(unlisted_value)))
    working.append("  TOTAL market cap  =  %s" % fmt_int(total))
    result["total_market_cap"] = total
    result["unlisted_shares_estimate"] = extra
    result["unlisted_value_estimate"] = unlisted_value
    result["proxy_class"] = proxy["symbol"]
    result["basis"] = ("registered_shares exceeds listed_shares by %s shares (%.1f%%). "
                      "Valued at the %s price as a proxy - a lower-confidence estimate, "
                      "not an exchange-quoted figure." % (fmt_int(extra), 100.0 * extra / listed_shares,
                                                          proxy["symbol"]))
    result["working"] = working
    return result


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def fmt_int(n):
    if n is None:
        return NA
    try:
        return "{:,.0f}".format(n)
    except (TypeError, ValueError):
        return str(n)


def fact_to_dict(fact):
    if fact is None:
        return None
    return fact.to_dict()


def rec_to_json(rec, market_cap=None):
    out = dict(rec)
    fields = {}
    for k, v in rec.get("fields", {}).items():
        fields[k] = {"value": v["fact"].value if v["fact"] else None,
                    "unit": "shares", "state": v["state"].value if hasattr(v["state"], "value") else v["state"],
                    "verification": v.get("verification"),
                    "source": v["fact"].source if v["fact"] else None,
                    "period_end": v["fact"].period_end.isoformat() if v["fact"] and v["fact"].period_end else None,
                    "publication_date": v["fact"].publication_date.isoformat()
                                        if v["fact"] and v["fact"].publication_date else None,
                    "confidence": v["fact"].confidence if v["fact"] else None,
                    "detail": v["detail"], "fact": fact_to_dict(v["fact"])}
    out["fields"] = fields
    if market_cap is not None:
        out["market_cap"] = market_cap
    return json.dumps(out, indent=2, ensure_ascii=False, default=str)


def print_report(rec, market_cap=None):
    if not rec.get("resolved"):
        print("%s: %s" % (rec.get("state", NA), rec.get("reason")))
        if rec.get("candidates"):
            print()
            print("Candidates (identity is ambiguous - refusing to guess):")
            for c in rec["candidates"]:
                print("  - %s" % c)
            print()
            print("Re-run with a more specific name, e.g. \"AB Volvo\" vs \"Volvo Car\".")
        return

    print("%s  -  share-count reconciliation" % rec["company_name"])
    print("resolved via: %s (%s)" % (rec["resolution_method"], rec["resolution_note"]))
    print("listed classes: %d  (%s)" % (rec["n_listed_classes"],
          ", ".join(c["symbol"] or "?" for c in rec["classes"])))
    print("generated: %s" % rec["generated_utc"])
    print()

    print("%-26s %18s  %-9s %-13s %-11s %s" %
         ("FIELD", "VALUE (shares)", "STATE", "VERIFICATION", "AS OF", "SOURCE"))
    print("-" * 118)
    for key in FIELD_ORDER:
        f = rec["fields"][key]
        fact = f["fact"]
        state = f["state"].value if hasattr(f["state"], "value") else f["state"]
        if fact is None:
            print("%-26s %18s  %-9s %-13s %-11s %s" %
                 (key, NA, state, "-", "-", f["detail"].get("reason", "")[:50]))
        else:
            verif = f.get("verification") or fact.verification.value
            print("%-26s %18s  %-9s %-13s %-11s %s (conf %.2f)" %
                 (key, fmt_int(fact.value), state, verif,
                  fact.period_end.isoformat() if fact.period_end else "-",
                  fact.source, fact.confidence))
    print()

    for key in FIELD_ORDER:
        f = rec["fields"][key]
        print("  %s: %s" % (key, FIELD_DESCRIPTION[key]))
        fact = f["fact"]
        if fact is not None and fact.source_detail:
            print("     %s" % fact.source_detail)
        if fact is not None and fact.note:
            print("     note: %s" % fact.note)
        if fact is None:
            print("     %s" % f["detail"].get("reason", NA))
    print()

    xc = rec["cross_checks"]["registered_vs_listed"]
    print("cross-check registered_shares (issuer_disclosure) vs listed_shares (exchange):")
    print("  independent origin groups: %s   spread: %.4f%%   values: %s"
         % (xc.get("origin_groups"), xc.get("spread", 0) * 100, xc.get("values")))

    if rec["flags"]:
        print()
        for fl in rec["flags"]:
            print("!! %s" % fl["state"])
            print("   %s" % fl["detail"])

    print()
    print("USAGE GUIDANCE (do not pick the wrong field):")
    ug = rec["usage_guidance"]
    print("  market cap        -> %s" % ug["market_cap_should_use"])
    print("  per-share metrics -> %s" % ug["per_share_metrics_should_use"])
    print("  NEVER for per-share: %s" % ", ".join(ug["never_use_for_per_share_metrics"]))
    print("  NEVER for market cap: %s" % ", ".join(ug["never_use_for_market_cap"]))

    if market_cap is not None:
        print()
        print("MARKET CAP - working:")
        for line in market_cap["working"]:
            print("  " + line)
        print()
        print("  TOTAL: %s   (%s)" % (fmt_int(market_cap["total_market_cap"]), market_cap["basis"]))


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("company", help='company name, e.g. "AB Volvo"')
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--market-cap", action="store_true",
                    help="compute market cap correctly and show the working")
    ap.add_argument("--bodies", type=int, default=40,
                    help="corporate_actions.py: disclosure bodies to fetch and parse")
    ap.add_argument("--esef-filings", type=int, default=1,
                    help="how many ESEF annual filings to pull (each carries 2 years)")
    args = ap.parse_args()

    rec = build_reconciliation(args.company, bodies=args.bodies, esef_filings=args.esef_filings)

    market_cap = None
    if args.market_cap and rec.get("resolved"):
        market_cap = compute_market_cap(rec)

    if args.as_json:
        print(rec_to_json(rec, market_cap))
        return
    print_report(rec, market_cap)


if __name__ == "__main__":
    main()
