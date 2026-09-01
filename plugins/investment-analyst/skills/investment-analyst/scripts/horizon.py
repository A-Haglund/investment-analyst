#!/usr/bin/env python3
"""The holding horizon: the next dated event that resolves or breaks a thesis.

A thesis is not held for "6-12 months". It is held UNTIL something specific
happens - the next report, an AGM vote, a financing deadline - because that is
when the case is actually settled. This script finds the nearest dated
corporate event for a Nordic-listed company, so the horizon can be written as
a date, not a guess.

RESEARCH BEHIND THIS FILE (verified 2026-09-01, live)

Three free, keyless sources were checked, in the order the task specified:

  1. NASDAQ NORDIC (api.nasdaq.com/api/nordic, see nordic_shares.py). NO
     calendar or events endpoint exists. Every plausible path was tried
     against a real orderbookId (Evolution, TX1757078) and every one 404s:
     /company-events, /financial-calendar, /calendar, /events, /reports,
     /corporate-actions, /news, /press-releases, /company-info, /profile,
     /financial-reports. The two endpoints that DO exist and answer 200 -
     /instruments/{id}/info and /instruments/{id}/summary - carry price,
     52-week range and segment data only; no scheduled-date field of any kind.
     Verdict: NOT a source for this metric.

  2. MFN (mfn.se, see mfn_news.py). Many issuers DO publish a "Finansiell
     kalender" / "Financial calendar" release (verified live: Nordea Bank,
     SpareBank 1 Ostlandet, Detection Technology, Kreate Group, Duell Oyj all
     have one dated 2026-08 or 2026-09 for FY2027). But it is not a usable
     PROGRAMMATIC source:
       - it is not universal. KebNi (First North Sweden, the case this task
         named) has never published one in nine years of releases (600 items
         checked back to 2016) - its only forward-looking signal is an
         occasional single-report preview such as "Kvartalsrapport for
         forsta kvartalet 2019 publiceras den 2 maj 2019", posted irregularly
         and not for every quarter.
       - where it exists, the format is free text in Swedish, Norwegian,
         Finnish or English, mixing "22 april 2027", "04.11.2026" and
         "vecka 8, 2027" in the same release (see the Nordea and SpareBank 1
         examples fetched live during this investigation). Parsing that
         reliably across issuers is exactly the fragile-scraper this toolkit
         exists to avoid - a misparsed month name is a wrong date with a
         confident-looking citation, the same failure class documented
         throughout mfn_news.py's figure extractor.
     Verdict: a real disclosure channel, but not one this script parses. Read
     it directly (mfn_news.py SLUG --search "kalender") when a citation to the
     issuer's own words is needed; do not expect it to answer every issuer.

  3. THE ISSUER'S OWN IR SITE (see ir_discovery.py). It already locates the
     "financial calendar" PAGE (the `financial_calendar` section) but
     deliberately reports only the URL, never a parsed date - the page is
     often JavaScript-rendered and hand-built, and ir_discovery.py's own
     documented policy is "never construct a claim past what a crawl actually
     answered". That policy is correct and this script does not override it.

  THE ANSWER THAT ACTUALLY WORKS: ir_discovery.py's Avanza route
  (avanza_lookup) already pulls `companyEvents.events` from Avanza's
  market-guide API - a STRUCTURED, dated, typed calendar
  (INTERIM_REPORT / ANNUAL_REPORT / GENERAL_MEETING /
  EXTRAORDINARY_GENERAL_MEETING, each with an ISO date and an isConfirmed
  flag). Verified live against Evolution, Volvo B, KebNi (First North),
  NanoEcho (First North, micro-cap) and Gabather (First North): the raw feed
  carries 7-9 events per issuer, but most of those are past - Volvo returns 2
  future-dated events, KebNi 4, Evolution 3. No parsing is required either
  way because the feed is already JSON. This is the one free, keyless,
  STRUCTURED source found for this metric, and it is what this script uses.

  THE CAVEAT THAT MUST TRAVEL WITH IT: Avanza is a broker redistributing
  licensed market data, not the issuer or a regulator - ir_discovery.py's own
  docstring already calls every Avanza figure "a CROSS-CHECK, never a primary
  citation". This script keeps that rule: every date it returns carries that
  provenance note, and the output tells the reader where to verify it (the
  issuer's own "Finansiell kalender" page, or an MFN/Cision calendar release
  where one exists).

WHAT THIS SCRIPT DOES NOT DO

It never guesses a duration. If Avanza has no match, or the match has no
future-dated event, the result says DATA NOT AVAILABLE and why - it does not
fall back to "6-12 months" or any other invented horizon.

Usage:
    python horizon.py --company "Volvo"
    python horizon.py --company "Evolution" --json
    python horizon.py --selftest

Free, no API key. Depends on ir_discovery.py (same folder) for the Avanza
route; nothing here talks to the network on its own.
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

NA = "DATA NOT AVAILABLE"

EVENT_LABELS = {
    "INTERIM_REPORT": "interim report",
    "ANNUAL_REPORT": "year-end report",
    "GENERAL_MEETING": "annual general meeting",
    "EXTRAORDINARY_GENERAL_MEETING": "extraordinary general meeting",
}

SOURCE_NOTE = (
    "Avanza market-guide company-events feed (reached via ir_discovery.py's "
    "avanza_lookup) -- a broker's redistribution of licensed market data, "
    "not the issuer or a regulator. Cross-check against the issuer's own "
    "\"Finansiell kalender\" page (ir_discovery.py locates it) or an "
    "MFN/Cision \"Finansiell kalender\" release before relying on this date "
    "for a live decision.")

# Per source-registry.md: Avanza is tier 4, and there is no fallback source
# for this data type (see the docstring above) - so unlike every other tier-4
# use in this toolkit, this date is not a cross-check on something else, it
# is the only thing there is. That must travel with the date itself, not only
# in a footer paragraph below it.
TIER_NOTE = "[SINGLE SOURCE - tier 4, Avanza; unverified against the issuer]"

# How many future events to keep beyond the nearest one, for context (a
# second catalyst - an AGM, the following report - is often part of the same
# thesis window).
KEEP_UPCOMING = 5

# How many runner-up candidates (companies Avanza's search also matched,
# ranked lower than the winner) to disclose alongside the match. "Volvo"
# matches AB Volvo, Volvo Car and others - the winner may be right, but a
# reader cannot judge that unless the contenders it beat are named too.
KEEP_CONTENDERS = 4


def _import_ir_discovery():
    import ir_discovery
    return ir_discovery


def label(event_type):
    return EVENT_LABELS.get(event_type, (event_type or "event").replace("_", " ").lower())


def parse_date(raw):
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw[:10])
    except ValueError:
        return None


def select_upcoming(events, as_of):
    """Future (>= as_of) events, sorted ascending, malformed dates dropped."""
    out = []
    for e in events or []:
        d = parse_date(e.get("date"))
        if d is None or d < as_of:
            continue
        out.append({"date": d.isoformat(), "type": e.get("type"),
                    "type_label": label(e.get("type")),
                    "confirmed": bool(e.get("isConfirmed"))})
    out.sort(key=lambda e: e["date"])
    return out


def avanza_events(company, delay=1.0, lookup=None):
    """Return (matched_name, events, match_score, contenders, reason).

    `events` is Avanza's raw companyEvents list (unfiltered). On any failure
    - no match, low-confidence match, or a network/parsing problem - returns
    (None, [], None, [], reason) rather than raising: this is a degrade path,
    not an error path, exactly like the sibling scripts' own DATA NOT
    AVAILABLE branches.

    `avanza_lookup` returns (None, ...) for four distinct reasons - the
    search itself raised, no hit at all, the best hit scored under 25, or the
    per-instrument details fetch failed - and it records which one actually
    happened via ir_discovery's module-level `note()` / `_notes`. That is the
    only place the true cause lives; without draining it, a network failure
    (details fetch) and "not listed" (no hit) are indistinguishable, and this
    script would report the wrong one as fact. `_notes` only grows across a
    process's lifetime, so only the notes appended during *this* call are
    read off (`before`/`after`), never the whole history.

    `contenders` lists the runner-up candidates Avanza's search returned
    alongside the winner (each with its own score), so an ambiguous brand -
    "Volvo" resolving to AB Volvo over Volvo Car, a different issuer - is
    disclosed rather than picked silently. Empty when there was no winner to
    be ambiguous about.

    `lookup` is dependency-injected for tests; production leaves it None and
    lazily imports ir_discovery, so importing this module never touches the
    network by itself.
    """
    try:
        ird = lookup
        if ird is None:
            ird = _import_ir_discovery()
        score_fn = getattr(ird, "score_candidate", None)
        before = len(getattr(ird, "_notes", []))
        best, ranked = ird.avanza_lookup(company, delay)
        why = "; ".join(getattr(ird, "_notes", [])[before:]) or None
    except (Exception, SystemExit) as e:  # siblings raise SystemExit, not Exception
        return None, [], None, [], "Avanza lookup failed (%s)" % e

    if not best:
        return None, [], None, [], (why or "Avanza returned no match for %r" % company)

    score = score_fn(company, best) if score_fn else None
    contenders = []
    if score_fn:
        for cand in (ranked or []):
            if cand is best:
                continue
            contenders.append({"name": cand.get("name"), "ticker": cand.get("ticker"),
                               "score": score_fn(company, cand)})
    contenders = contenders[:KEEP_CONTENDERS]
    events = best.get("events") or []
    matched = "%s (%s)" % (best.get("name"), best.get("ticker")) \
        if best.get("ticker") else (best.get("name") or company)
    return matched, events, score, contenders, None


def resolve_horizon(company, as_of=None, delay=1.0, lookup=None):
    """The full answer: next dated event, source, as-of, or a stated reason
    why none could be sourced. Never guesses a duration."""
    today = as_of or datetime.date.today()
    matched, raw_events, score, contenders, reason = avanza_events(
        company, delay=delay, lookup=lookup)

    result = {
        "company_query": company,
        "matched_name": matched,
        "match_score": score,
        "contenders": contenders,
        "as_of": today.isoformat(),
        "available": False,
        "reason": reason,
        "next_event": None,
        "upcoming": [],
        "source": SOURCE_NOTE,
        "tier_note": TIER_NOTE,
    }

    if reason:
        return result

    upcoming = select_upcoming(raw_events, today)
    if not upcoming:
        result["reason"] = ("Avanza matched %r to %s but its company-events "
                             "feed has no future-dated event (feed may be "
                             "stale, or the issuer has none scheduled)"
                             % (company, matched))
        return result

    result["available"] = True
    result["next_event"] = upcoming[0]
    result["upcoming"] = upcoming[:KEEP_UPCOMING]
    return result


def format_text(result):
    lines = []
    if not result["available"]:
        lines.append("%s: no dated catalyst could be sourced for %r."
                      % (NA, result["company_query"]))
        lines.append("")
        lines.append("  %s" % result["reason"])
        lines.append("")
        lines.append("  What would settle the case is still worth stating even")
        lines.append("  though its date is unknown - check the issuer's own")
        lines.append("  investor-relations \"Finansiell kalender\" page directly")
        lines.append("  (ir_discovery.py can locate it) or an MFN/Cision")
        lines.append("  \"Finansiell kalender\" release, and name the catalyst")
        lines.append("  itself (a financing deadline, an option window, a named")
        lines.append("  event) instead of a guessed duration.")
        return "\n".join(lines)

    ev = result["next_event"]
    score = result.get("match_score")
    header = "%s -- next dated catalyst" % result["matched_name"]
    if score is not None:
        header += "  (match score %.0f)" % score
    lines.append(header)
    lines.append("")
    conf = "confirmed" if ev["confirmed"] else "not yet confirmed"
    lines.append("  %s  %-32s  (%s)  %s"
                 % (ev["date"], ev["type_label"], conf, result["tier_note"]))
    rest = result["upcoming"][1:]
    if rest:
        lines.append("")
        lines.append("  Also on the calendar:")
        for e in rest:
            conf = "confirmed" if e["confirmed"] else "not yet confirmed"
            lines.append("    %s  %-32s  (%s)" % (e["date"], e["type_label"], conf))
    if result["contenders"]:
        lines.append("")
        lines.append("  Avanza's search also matched, ranked lower:")
        for c in result["contenders"]:
            lines.append("    %s (%s)  score %.0f"
                         % (c["name"], c["ticker"] or "-", c["score"]))
        lines.append("")
        lines.append("  If %r is the wrong issuer, re-run with the full legal"
                     % result["company_query"])
        lines.append("  name (e.g. \"AB Volvo\" rather than \"Volvo\") to")
        lines.append("  disambiguate.")
    lines.append("")
    lines.append("  Source: %s" % result["source"])
    lines.append("  Retrieved %s." % result["as_of"])
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--company", help="company or ticker, e.g. Volvo")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="politeness delay in seconds before the Avanza request")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(0 if _selftest() else 1)

    if not args.company:
        ap.error("give --company NAME, or --selftest")

    result = resolve_horizon(args.company, delay=args.delay)
    if args.as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(format_text(result))


# --------------------------------------------------------------------------
# Selftest - pure logic, no network. Mirrors the style of portfolio_metrics.py
# and ttm_engine.py's own --selftest. Uses _assert(), not a bare `assert`,
# because `assert` is compiled out entirely under `python -O` - a stripped
# assertion never fails, which would make --selftest lie about its own
# health. Matches screen_digest.py's _selftest(): returns True/raises, and
# main() turns that into a process exit code rather than ignoring it.
# --------------------------------------------------------------------------

def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _selftest():
    ok = 0

    # -- parse_date ---------------------------------------------------------
    _assert(parse_date("2026-10-23") == datetime.date(2026, 10, 23), "plain ISO date")
    _assert(parse_date("2026-10-23T00:00:00") == datetime.date(2026, 10, 23),
            "ISO datetime truncated to date")
    _assert(parse_date(None) is None, "None input")
    _assert(parse_date("not-a-date") is None, "garbage input")
    ok += 4

    # -- select_upcoming: past events dropped, future ones sorted -----------
    today = datetime.date(2026, 9, 1)
    events = [
        {"date": "2026-07-17", "type": "INTERIM_REPORT", "isConfirmed": True},   # past
        {"date": "2026-10-23", "type": "INTERIM_REPORT", "isConfirmed": True},
        {"date": "2026-09-22", "type": "EXTRAORDINARY_GENERAL_MEETING",
         "isConfirmed": True},
        {"date": "garbage", "type": "ANNUAL_REPORT", "isConfirmed": True},       # malformed
        {"date": "2027-02-04", "type": "ANNUAL_REPORT", "isConfirmed": False},
    ]
    up = select_upcoming(events, today)
    _assert([e["date"] for e in up] == ["2026-09-22", "2026-10-23", "2027-02-04"],
            "past/malformed dropped, future sorted ascending: %r" % up)
    _assert(up[0]["type"] == "EXTRAORDINARY_GENERAL_MEETING", "nearest event's type")
    _assert(up[0]["confirmed"] is True, "nearest event's confirmed flag")
    _assert(up[-1]["confirmed"] is False, "farthest event's confirmed flag")
    ok += 4

    # -- select_upcoming: an event dated exactly today counts as upcoming ---
    same_day = select_upcoming(
        [{"date": "2026-09-01", "type": "INTERIM_REPORT", "isConfirmed": True}], today)
    _assert(len(same_day) == 1, "same-day event counts as upcoming")
    ok += 1

    # -- resolve_horizon: happy path via an injected fake lookup ------------
    class _FakeIR:
        @staticmethod
        def avanza_lookup(query, delay):
            return ({"name": "Volvo", "ticker": "VOLV B", "events": events}, [])

        @staticmethod
        def score_candidate(query, cand):
            return 100.0

    res = resolve_horizon("Volvo", as_of=today, lookup=_FakeIR)
    _assert(res["available"] is True, "happy path is available")
    _assert(res["next_event"]["date"] == "2026-09-22", "nearest event's date")
    _assert(res["matched_name"] == "Volvo (VOLV B)", "matched name includes ticker")
    _assert(res["source"] == SOURCE_NOTE, "source note attached")
    _assert(res["tier_note"] == TIER_NOTE, "tier note attached")
    _assert(len(res["upcoming"]) == 3, "three future events kept")
    _assert(res["contenders"] == [], "a single unambiguous candidate has no contenders")
    ok += 7

    # -- resolve_horizon: Avanza found nothing -> DATA NOT AVAILABLE, no guess
    class _NoMatchIR:
        @staticmethod
        def avanza_lookup(query, delay):
            return (None, [])

    res = resolve_horizon("Totally Made Up Company XYZ", as_of=today, lookup=_NoMatchIR)
    _assert(res["available"] is False, "no-match path is unavailable")
    _assert(res["next_event"] is None, "no-match path has no next event")
    _assert("no match" in res["reason"], "no-match reason: %r" % res["reason"])
    ok += 3

    # -- resolve_horizon: matched, but every event is in the past -----------
    class _StaleIR:
        @staticmethod
        def avanza_lookup(query, delay):
            return ({"name": "Stale Co", "ticker": "STALE",
                     "events": [{"date": "2020-01-01", "type": "ANNUAL_REPORT",
                                 "isConfirmed": True}]}, [])

        @staticmethod
        def score_candidate(query, cand):
            return 100.0

    res = resolve_horizon("Stale Co", as_of=today, lookup=_StaleIR)
    _assert(res["available"] is False, "stale-events path is unavailable")
    _assert("no future-dated event" in res["reason"], "stale-events reason")
    ok += 2

    # -- resolve_horizon: the lookup itself blows up (network, or a sibling's
    # SystemExit) must degrade, never propagate ------------------------------
    class _BrokenIR:
        @staticmethod
        def avanza_lookup(query, delay):
            raise SystemExit("DATA NOT AVAILABLE: Avanza unreachable")

    res = resolve_horizon("Anything", as_of=today, lookup=_BrokenIR)
    _assert(res["available"] is False, "SystemExit degrades, not propagates")
    _assert("Avanza lookup failed" in res["reason"], "SystemExit reason")
    ok += 2

    class _BrokenIR2:
        @staticmethod
        def avanza_lookup(query, delay):
            raise ValueError("boom")

    res = resolve_horizon("Anything", as_of=today, lookup=_BrokenIR2)
    _assert(res["available"] is False, "plain exception degrades, not propagates")
    _assert("Avanza lookup failed" in res["reason"], "plain-exception reason")
    ok += 2

    # -- M8: "not listed" and "the network failed" must not read the same --
    # avanza_lookup returns (None, ...) for four different reasons and
    # records the true one via note()/_notes; draining only what a single
    # call appended must make the two cases say different things.
    class _NoHitIR:
        _notes = []

        @staticmethod
        def avanza_lookup(query, delay):
            _NoHitIR._notes.append(
                "Avanza search returned no listed equity for %r" % query)
            return (None, [])

        @staticmethod
        def score_candidate(query, cand):
            return 100.0

    class _DetailsFailedIR:
        _notes = []

        @staticmethod
        def avanza_lookup(query, delay):
            _DetailsFailedIR._notes.append("Avanza details failed - HTTP 503")
            return (None, [])

        @staticmethod
        def score_candidate(query, cand):
            return 100.0

    res_no_hit = resolve_horizon("Nope", as_of=today, lookup=_NoHitIR)
    res_net_fail = resolve_horizon("Nope", as_of=today, lookup=_DetailsFailedIR)
    _assert(res_no_hit["reason"] != res_net_fail["reason"],
            "a network failure must not read identically to 'not listed': "
            "%r vs %r" % (res_no_hit["reason"], res_net_fail["reason"]))
    _assert("no listed equity" in res_no_hit["reason"], "no-hit reason surfaced")
    _assert("details failed" in res_net_fail["reason"], "network-failure reason surfaced")
    ok += 3

    # -- M9: an ambiguous match must expose the runners-up it beat, not just
    # silently pick a winner --------------------------------------------------
    class _AmbiguousIR:
        @staticmethod
        def avanza_lookup(query, delay):
            winner = {"name": "Volvo B", "ticker": "VOLV B", "events": events}
            runner_up = {"name": "Volvo A", "ticker": "VOLV A", "events": []}
            other_issuer = {"name": "Volvo Car B", "ticker": "VOLCAR B", "events": []}
            return winner, [winner, runner_up, other_issuer]

        @staticmethod
        def score_candidate(query, cand):
            return {"Volvo B": 104.0, "Volvo A": 103.0,
                   "Volvo Car B": 70.0}[cand["name"]]

    res = resolve_horizon("Volvo", as_of=today, lookup=_AmbiguousIR)
    _assert(res["matched_name"] == "Volvo B (VOLV B)", "the winner is still picked")
    names = [c["name"] for c in res["contenders"]]
    _assert(names == ["Volvo A", "Volvo Car B"], "runners-up, winner excluded: %r" % names)
    _assert(res["contenders"][0]["score"] == 103.0, "runner-up score carried")
    ambiguous_text = format_text(res)
    _assert("Volvo A" in ambiguous_text and "Volvo Car B" in ambiguous_text,
            "format_text must name the contenders, not just count them")
    _assert("match score" in ambiguous_text, "format_text must show the winner's score")
    ok += 4

    # -- format_text renders both branches without raising -------------------
    happy = resolve_horizon("Volvo", as_of=today, lookup=_FakeIR)
    text = format_text(happy)
    _assert("2026-09-22" in text and "Also on the calendar" in text,
            "happy-path text has the date and the rest of the calendar")
    _assert(TIER_NOTE in text, "happy-path text carries the tier-4 single-source note")
    sad = resolve_horizon("Nope", as_of=today, lookup=_NoMatchIR)
    text2 = format_text(sad)
    _assert(NA in text2 and "What would settle the case" in text2,
            "DATA NOT AVAILABLE text names the reason and what would settle it")
    ok += 3

    print("horizon selftest: %d assertions passed" % ok)
    return True


if __name__ == "__main__":
    main()
