#!/usr/bin/env python3
"""One issuer-to-newsroom-slug resolver, for MFN and Cision.

WHY THIS EXISTS

Three scripts in this folder resolved the same question - "which MFN (or
Cision) newsroom belongs to this issuer?" - in three different ways, of
increasing correctness: one took the search engine's top hit, one sorted
exact name matches first, one refused when more than one issuer matched.
They are now one implementation.

MFN's /all/s.json is relevance-ranked by something other than exact issuer
name, so the top hit is regularly a research publisher, a broker or a
regulator that merely mentioned the company.  Measured over a twelve-name
Nasdaq Stockholm test set, taking the top hit named the WRONG issuer for
seven of them - resolving listed companies to a broker's, a fund manager's,
a research site's or Finansinspektionen's newsroom.  The correct slug was
present in every one of those result sets, at position two, three or four.
It simply was not selected.

The consequence is not a missed check.  A caller then reads that other
organisation's newsroom, finds real regulatory releases in it, and reports
them as the queried issuer's own with `checked=True` - a confident wrong
answer rather than an honest "could not check".

The same defect class is documented elsewhere in this toolkit from other
angles: corporate_actions._norm carries a comment about it for MFN slugs,
market_universe describes it for Nasdaq CNS, and screen_digest asserts
against it.  It had been found and fixed several times in several places.
This module is the shared fix, so there is no next place for it to survive.

THE RULE, IN ONE LINE

Refuse, never guess - the same discipline company_resolve.py applies to
"Volvo" (AB Volvo or Volvo Car AB?).  A newsroom attributed to the wrong
issuer is silent and looks identical to a correct answer.

USAGE
    python issuer_feed.py "Sensys Gatso"            # resolve, human-readable
    python issuer_feed.py "Sensys Gatso" --json
    python issuer_feed.py "Volvo"                   # refuses, names both
    python issuer_feed.py --selftest                # offline, no network

Python 3 standard library only.  Free, keyless.
"""
import argparse
import json
import os
import re
import sys
import unicodedata

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import _bootstrap  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Loaded lazily and softly: a caller that only wants normalise() or a resolve()
# against injected search functions (every test does) must not pay either
# module's import cost, and must not fail when one of them is mid-edit.
_MFN = None
_CIS = None


def _mfn():
    global _MFN
    if _MFN is None:
        _MFN = _bootstrap.soft_load("mfn_news") or False
    return _MFN or None


def _cision():
    global _CIS
    if _CIS is None:
        _CIS = _bootstrap.soft_load("cision_news") or False
    return _CIS or None


# Kept verbatim from corporate_actions._LEGAL_SUFFIX so the two normalisations
# cannot diverge; "group" and "holding" are in it on purpose ("Kambi Group" and
# "Kambi" are one issuer, and MFN records only one of the two spellings).
_LEGAL_SUFFIX = re.compile(
    r"(?i)\s*\b(ab|abp|a/s|asa|hf\.?|oyj|oy|plc|publ|se|ltd|inc|corp|nv|"
    r"holding|group|\(publ\))\b\.?", re.UNICODE)


def fold(text):
    """Lowercase and strip diacritics, so "Orron" matches "Orrön".

    Swedish issuer names reach this module from three directions - a portfolio
    the user typed, MFN's own register, and a ticker - and they do not agree
    on å/ä/ö.  "Orron Energy" against MFN's "Orrön Energy AB" is a real case
    on Nasdaq Stockholm, and without folding it falls through exact matching
    into the ambiguity branch and refuses a name that is not ambiguous at all.

    NFKD splits a letter from its combining accent; the Mn filter drops the
    accent and keeps the letter.  Deliberately not a Swedish-specific
    ä->ae/ö->oe transliteration: MFN spells these names with the plain vowel
    when it spells them without the accent at all, never with the digraph.
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed
                   if not unicodedata.combining(c)).lower()


def normalise(name):
    """The comparison form of an issuer name.

    Parentheses are stripped BEFORE the legal-suffix regex runs, never after -
    that regex's "publ" alternative matches the bare word inside "(publ)" and
    consumes it, leaving orphan parentheses behind ("scandinavian enviro
    systems ( )") which then equal nothing.  corporate_actions._norm carries
    the same ordering and the same reason; this is that function plus fold().
    """
    n = fold(name).replace(",", " ").replace("(", " ").replace(")", " ")
    n = _LEGAL_SUFFIX.sub(" ", n)
    return re.sub(r"\s+", " ", n).strip()


def dedupe_by_slug(hits):
    out, seen = [], set()
    for h in hits or []:
        slug = h.get("slug")
        if slug and slug not in seen:
            seen.add(slug)
            out.append(h)
    return out


def _listing(candidates):
    return ", ".join("%s (%s)" % (c.get("name") or "?", c.get("slug"))
                     for c in candidates)


def ambiguous_note(venue, name, candidates):
    return ("COMPANY_IDENTITY_AMBIGUOUS: %d distinct %s issuers match %r (%s). "
            "Attributing a newsroom to the wrong issuer is silent and looks "
            "identical to a correct answer - re-run with the exact legal name, "
            "or pin the holding's newsroom with feed_slug."
            % (len(candidates), venue, name, _listing(candidates)))


def unmatched_note(venue, name, candidates):
    """The result set matched nothing.  A refusal, not a fallback.

    Kept separate from ambiguous_note because it is a different diagnosis and
    has a different remedy: nothing here IS the issuer, so naming a candidate
    is a lead, not a shortlist.  The rung this replaces returned the sole hit
    when the set held one, which is how a regulator's newsroom was attributed
    to a listed company - one unrelated result is not evidence, it is only an
    absence of alternatives.
    """
    return ("COMPANY_IDENTITY_UNMATCHED: no %s newsroom matches %r. "
            "The search returned %d unrelated result(s) (%s); none of them is "
            "taken, because a result set with no match is not evidence for its "
            "first entry. Re-run with the exact legal name, or pin the "
            "holding's newsroom with feed_slug."
            % (venue, name, len(candidates), _listing(candidates[:6])))


def _labels(hit):
    """Every string this hit could legitimately be matched on."""
    out = [hit.get("name"), hit.get("slug")]
    out.extend(hit.get("aliases") or [])
    return [x for x in out if x]


def rank(name, hits):
    """Split `hits` into (exact, extensions), deduped by slug.

    `exact`      - a name, slug or alias whose normalised form EQUALS the
                   query's.
    `extensions` - one whose normalised form starts with the query plus a word
                   boundary: query "NIBE" against "NIBE Industrier AB".  These
                   are candidates the query might be a shortening of.

    Unanchored substring containment is deliberately NOT used, in either
    direction.  It was, and it silently returned the wrong issuer:

        "Handelsbanken" -> handelsbanken-fonder   (the fund manager, not the bank)
        "Volvo Car"     -> volvo   (AB Volvo, because "volvo" is inside it)
        "Bio"           -> bio-stock

    The `f in needle` direction is the worse half - any short unrelated
    newsroom whose name happens to be a token of the query wins outright.  A
    word-boundary prefix is the only containment relation that carries
    evidence about identity; the rest is coincidence.
    """
    needle = normalise(name)
    exact, extensions = [], []
    if not needle:
        return [], []
    for h in dedupe_by_slug(hits):
        forms = [f for f in (normalise(x) for x in _labels(h)) if f]
        if any(f == needle for f in forms):
            exact.append(h)
        elif any(f.startswith(needle + " ") for f in forms):
            extensions.append(h)
    key = lambda h: (len(h.get("slug") or ""), h.get("slug") or "")  # noqa: E731
    return sorted(exact, key=key), sorted(extensions, key=key)


def choose(venue, name, hits):
    """One issuer from a search result set, or a refusal.  Returns (hit, note).

    Two rules, and the second is the one that took work to get right.

    RULE ONE - a hit is only taken on evidence.  An exact normalised match, or
    a single candidate the query is an unambiguous shortening of.  Never a
    loose substring, and never `hits[0]`.  Taking the top-ranked hit when
    nothing matched is what attributed Finansinspektionen's releases to a
    listed company; a result set of one unrelated newsroom is not evidence
    that it is the right one, it is only an absence of alternatives.

    RULE TWO - the query must DISCRIMINATE.  An exact match is not enough on
    its own if the query is also a word-boundary prefix of a competing
    candidate, because then it does not distinguish them:

        "Volvo"        -> AB Volvo (exact) and Volvo Car AB   -> refuse
        "Volvo Car AB" -> Volvo Car AB (exact); "volvo" is not
                          an extension of "volvo car"          -> resolves

    That asymmetry is the point.  The old rule refused BOTH, which left the
    refusal's own advice - "re-run with the exact legal name" - impossible to
    follow for the module's own worked example.

    The ladder:
      1. One exact, no competing extension  -> take it.
      2. One exact, but an extension exists -> refuse; the query is a brand
         several issuers share.
      3. More than one exact                -> refuse.
      4. No exact, exactly one extension    -> take it ("NIBE" -> NIBE Industrier).
      5. No exact, several extensions       -> refuse.
      6. Nothing matched at all             -> refuse, whatever the result set
         size.  This rung used to return hits[0] when the set held one hit.
    """
    hits = dedupe_by_slug(hits)
    if not hits:
        return None, None

    # Rung 0 - literal match, before any suffix stripping.
    #
    # normalise() strips a legal suffix wherever it appears, so "AB Volvo"
    # and "Volvo" both collapse to "volvo" and become indistinguishable. That
    # is right for matching and wrong for discrimination: one is a complete
    # legal name, the other a brand two issuers share. The unstripped folded
    # form still tells them apart, so it is consulted first. This is what
    # makes the refusal's own advice - "re-run with the exact legal name" -
    # an instruction with a valid input.
    # Names and aliases only, never the slug. MFN's slug for AB Volvo is
    # literally "volvo", so including it here would let the bare brand match
    # it outright and resolve the one query this module must refuse. A slug is
    # a URL token, not a legal name; it still participates in the normalised
    # rungs below, where a caller passing a slug is matched on equal terms.
    literal = fold(name).strip()
    if literal:
        strict = [h for h in hits
                  if any(fold(x).strip() == literal
                         for x in ([h.get("name")] + list(h.get("aliases") or []))
                         if x)]
        if len(strict) == 1:
            return strict[0], None
        if len(strict) > 1:
            return None, ambiguous_note(venue, name, strict)

    exact, extensions = rank(name, hits)
    if len(exact) == 1 and not extensions:
        return exact[0], None
    if len(exact) >= 1:
        # Either several issuers match exactly, or one does while the query is
        # also a prefix of another. Both mean the query does not discriminate.
        return None, ambiguous_note(venue, name, exact + extensions)
    if extensions:
        # Extension-only, e.g. "Handelsbanken" against "Handelsbanken Fonder
        # AB". A word-boundary prefix says the two strings are related; it
        # does not say they are the same issuer, and a bank is not its fund
        # manager. Taking the sole extension was how that pair resolved wrong.
        # A caller who really means the shortened form has the full name
        # available, and a stored feed_slug settles it permanently.
        return None, ambiguous_note(venue, name, extensions)
    return None, unmatched_note(venue, name, hits)


def resolve(name, search_mfn=None, search_cision=None, limit=12):
    """Find `name`'s newsroom, MFN first then Cision.

    Returns (venue, slug, label, note) - the shape guidance_track.
    resolve_company() already returned, so that call site is a drop-in.
    `note` is None on a clean match; on a refusal venue/slug/label are all
    None and `note` names every candidate seen.

    `search_mfn` / `search_cision` are injection points for tests: each takes
    the query string and returns a list of {"slug", "name", "aliases"} dicts.
    Left as None they use mfn_news.search() and cision_news.resolve().
    """
    # Each default searcher binds its module as a default argument, never by
    # closing over a shared local: two lambdas closing over the same `mod`
    # name both see whichever module was assigned last, so the MFN searcher
    # silently ends up calling cision_news.search() - which does not exist,
    # raises AttributeError, and degrades every MFN lookup to Cision.
    if search_mfn is None:
        mfn_mod = _mfn()
        search_mfn = ((lambda q, _m=mfn_mod: _m.search(q, limit=limit))
                      if mfn_mod else None)
    if search_cision is None:
        cis_mod = _cision()
        search_cision = ((lambda q, _m=cis_mod: _m.resolve(q))
                         if cis_mod else None)

    unmatched, outages = [], []
    for venue, searcher in (("MFN", search_mfn), ("Cision", search_cision)):
        if searcher is None:
            outages.append("%s: no searcher available" % venue)
            continue
        try:
            hits = searcher(name) or []
        except (Exception, SystemExit) as exc:  # siblings signal failure with SystemExit
            # An outage is NOT an answer. Collected and reported below rather
            # than swallowed: "the search failed" and "this issuer has no
            # newsroom" are different facts, and reporting the second when the
            # first happened is a false claim about the company.
            outages.append("%s: %s" % (venue, exc))
            continue

        hit, note = choose(venue, name, hits)

        # Ambiguity is terminal - a query that cannot distinguish two issuers
        # on MFN will not become unambiguous by asking Cision, and masking it
        # behind a Cision hit is how the wrong newsroom gets attributed.
        if note and note.startswith("COMPANY_IDENTITY_AMBIGUOUS"):
            return None, None, None, note

        # "Nothing here matches" is NOT terminal. MFN's search returns brokers
        # and regulators for perfectly good names, and Cision carries the
        # issuers MFN does not. Falling through is what keeps Cision-
        # distributed and Danish/Norwegian issuers resolvable.
        if note:
            unmatched.append(note)
            continue
        if hit:
            return venue, hit["slug"], hit.get("name") or hit["slug"], None

    if outages:
        # Every venue that could have answered failed, or the ones that
        # answered matched nothing while another was down. Either way this run
        # did not establish an absence - say so, so the caller records "not
        # checked" rather than "no newsroom exists".
        return None, None, None, (
            "FEED_LOOKUP_FAILED: could not search for %r (%s). This is an "
            "outage, not a finding: no conclusion about the issuer's newsroom "
            "follows from it." % (name, "; ".join(outages)))
    if unmatched:
        return None, None, None, unmatched[0]
    return None, None, None, None


def resolve_slug(name, **kwargs):
    """Just the slug, or None.  For a caller that has already decided what a
    missing newsroom means to it and does not want the tuple."""
    return resolve(name, **kwargs)[1]


# --------------------------------------------------------------------------
# Selftest - offline only.  Every case below is a result set observed on
# mfn.se, trimmed to the fields this module reads.
# --------------------------------------------------------------------------

_OBSERVED_CASES = {
    "Axfood": ([
        {"slug": "nordnet", "name": "Nordnet", "aliases": ["nordnet"]},
        {"slug": "avanza-bank-holding", "name": "Avanza Bank Holding AB", "aliases": []},
        {"slug": "axfood", "name": "Axfood", "aliases": ["axfood"]},
        {"slug": "stockpicker", "name": "Stockpicker", "aliases": []},
    ], "axfood"),
    "Pricer": ([
        {"slug": "fi-se", "name": "Finansinspektionen", "aliases": ["fi-se"]},
        {"slug": "q-linea", "name": "Q-linea", "aliases": []},
        {"slug": "carnegie", "name": "DNB Carnegie Access",
         "aliases": ["carnegie", "carnegie-commissioned-research"]},
        {"slug": "pricer", "name": "Pricer", "aliases": ["pricer"]},
    ], "pricer"),
    "Sensys Gatso": ([
        {"slug": "carnegie", "name": "DNB Carnegie Access", "aliases": ["carnegie"]},
        {"slug": "sensys-gatso-group", "name": "Sensys Gatso Group", "aliases": []},
    ], "sensys-gatso-group"),
    "Saniona": ([
        {"slug": "bio-stock", "name": "BioStock", "aliases": []},
        {"slug": "saniona", "name": "Saniona", "aliases": ["saniona"]},
    ], "saniona"),
    # The diacritic case: the caller types a plain o, MFN records the umlaut.
    "Orron Energy": ([
        {"slug": "orron-energy", "name": "Orrön Energy AB", "aliases": []},
    ], "orron-energy"),
    # Legal-suffix stripping: "Kambi Group" and "Kambi" are one issuer.
    "Kambi": ([
        {"slug": "kambi-group", "name": "Kambi Group plc", "aliases": []},
    ], "kambi-group"),
}


def _selftest():
    ok = 0

    assert normalise("Orrön Energy AB") == "orron energy"
    assert normalise("Kambi Group plc") == "kambi"
    assert normalise("Scandinavian Enviro Systems AB (publ)") == "scandinavian enviro systems"
    assert normalise("") == ""
    ok += 1

    for query, (hits, want) in _OBSERVED_CASES.items():
        venue, slug, _label, note = resolve(
            query, search_mfn=lambda q, _h=hits: _h, search_cision=lambda q: [])
        assert note is None, (query, note)
        assert slug == want, (query, slug, want)
        assert venue == "MFN", (query, venue)
    ok += 1

    # --- Rule two: the query must DISCRIMINATE ------------------------------
    volvo = [{"slug": "volvo", "name": "AB Volvo", "aliases": []},
             {"slug": "volvo-car", "name": "Volvo Car AB", "aliases": []}]
    # A bare brand shared by two issuers refuses...
    venue, slug, label, note = resolve(
        "Volvo", search_mfn=lambda q: volvo, search_cision=lambda q: [])
    assert (venue, slug, label) == (None, None, None), (venue, slug, label)
    assert note.startswith("COMPANY_IDENTITY_AMBIGUOUS"), note
    assert "volvo-car" in note and "volvo" in note, note
    # ...but the refusal's own advice must be followable. The full legal name
    # of EITHER issuer has to resolve, or "re-run with the exact legal name"
    # is an instruction with no valid input. An earlier ladder refused both.
    for query, want in (("Volvo Car AB", "volvo-car"), ("AB Volvo", "volvo")):
        _v, slug, _l, note = resolve(
            query, search_mfn=lambda q, _h=volvo: _h, search_cision=lambda q: [])
        assert note is None, (query, note)
        assert slug == want, (query, slug, want)
    ok += 1

    # --- Rule one: a hit is only taken on evidence -------------------------
    # Nothing matched, several results: refuse.
    unrelated = [{"slug": "nordnet", "name": "Nordnet", "aliases": []},
                 {"slug": "redeye", "name": "Redeye", "aliases": []}]
    _v, slug, _l, note = resolve(
        "Some Unlisted Issuer", search_mfn=lambda q: unrelated,
        search_cision=lambda q: [])
    assert slug is None, slug
    assert note.startswith("COMPANY_IDENTITY_UNMATCHED"), note
    ok += 1

    # Nothing matched, exactly ONE result: still refuse. This rung used to
    # return the sole hit, which is how a regulator's newsroom was attributed
    # to a listed company - the search returning nothing else is not evidence.
    _v, slug, _l, note = resolve(
        "Arla Plast", search_mfn=lambda q: [{"slug": "fi-se",
                                             "name": "Finansinspektionen",
                                             "aliases": []}],
        search_cision=lambda q: [])
    assert slug is None, slug
    assert note.startswith("COMPANY_IDENTITY_UNMATCHED"), note
    ok += 1

    # Loose substring containment must not win, in either direction. Both of
    # these resolved to the wrong issuer under an unanchored `in` test.
    _v, slug, _l, _n = resolve(
        "Handelsbanken",
        search_mfn=lambda q: [{"slug": "handelsbanken-fonder",
                               "name": "Handelsbanken Fonder AB", "aliases": []}],
        search_cision=lambda q: [])
    assert slug is None, "a fund manager is not the bank: %r" % slug
    _v, slug, _l, _n = resolve(
        "Bio", search_mfn=lambda q: [{"slug": "bio-stock", "name": "BioStock",
                                      "aliases": []}],
        search_cision=lambda q: [])
    assert slug is None, "a token of the query is not a match: %r" % slug
    ok += 1

    # A bare shortening does NOT resolve on its own - a word-boundary prefix
    # says two strings are related, not that they are the same issuer.
    _v, slug, _l, _n = resolve(
        "NIBE", search_mfn=lambda q: [{"slug": "nibe-industrier",
                                       "name": "NIBE Industrier AB",
                                       "aliases": []}],
        search_cision=lambda q: [])
    assert slug is None, "a prefix is not an identity: %r" % slug
    # The full name resolves, which is what every caller in this toolkit
    # actually holds - portfolio_store records the resolved legal name.
    _v, slug, _l, note = resolve(
        "NIBE Industrier AB", search_mfn=lambda q: [{"slug": "nibe-industrier",
                                                     "name": "NIBE Industrier AB",
                                                     "aliases": []}],
        search_cision=lambda q: [])
    assert slug == "nibe-industrier" and note is None, (slug, note)
    ok += 1

    # --- Venue fall-through -------------------------------------------------
    # Empty MFN falls through to Cision.
    _v, slug, _l, note = resolve(
        "Elsewhere", search_mfn=lambda q: [],
        search_cision=lambda q: [{"slug": "els", "name": "Elsewhere", "aliases": []}])
    assert slug == "els" and note is None, (slug, note)

    # So does MFN returning noise. MFN's search answers with brokers and
    # regulators for real names, and Cision carries the issuers MFN does not;
    # refusing at MFN would strand every Cision-distributed issuer.
    venue, slug, _l, note = resolve(
        "Elsewhere", search_mfn=lambda q: unrelated,
        search_cision=lambda q: [{"slug": "els", "name": "Elsewhere", "aliases": []}])
    assert (venue, slug) == ("Cision", "els"), (venue, slug, note)

    # Ambiguity does NOT fall through: a query that cannot tell two issuers
    # apart on MFN is not made unambiguous by Cision having one of them.
    _v, slug, _l, note = resolve(
        "Volvo", search_mfn=lambda q: volvo,
        search_cision=lambda q: [{"slug": "volvo-cision", "name": "Volvo",
                                  "aliases": []}])
    assert slug is None and note.startswith("COMPANY_IDENTITY_AMBIGUOUS"), (slug, note)
    ok += 1

    # --- An outage is not a finding ----------------------------------------
    def _boom(_q):
        raise SystemExit("DATA NOT AVAILABLE: simulated")
    # One venue down, the other answers: that is still a clean answer.
    _v, slug, _l, note = resolve(
        "Elsewhere", search_mfn=_boom,
        search_cision=lambda q: [{"slug": "els", "name": "Elsewhere", "aliases": []}])
    assert slug == "els", slug
    # Both down: the caller must be able to tell "could not check" from "this
    # issuer has no newsroom". Reporting the second when the first happened is
    # a false factual claim about the company.
    _v, slug, _l, note = resolve("Elsewhere", search_mfn=_boom, search_cision=_boom)
    assert slug is None, slug
    assert note.startswith("FEED_LOOKUP_FAILED"), note
    assert "simulated" in note, note
    ok += 1

    # No newsroom anywhere is a clean "not found", not a refusal: the caller
    # decides what a missing newsroom means to it.
    _v, slug, _l, note = resolve(
        "Nowhere", search_mfn=lambda q: [], search_cision=lambda q: [])
    assert slug is None and note is None, (slug, note)
    ok += 1

    print("issuer_feed selftest: %d assertion groups ok" % ok)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", nargs="?", help="issuer name to resolve")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return _selftest()
    if not args.name:
        parser.print_help()
        return 1

    venue, slug, label, note = resolve(args.name)
    payload = {"query": args.name, "venue": venue, "slug": slug,
               "label": label, "note": note,
               "resolved": bool(slug) and note is None}
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif note:
        print("REFUSING: %s" % note)
    elif slug:
        print("%s  %s  (%s)" % (venue, slug, label))
    else:
        print("no MFN or Cision newsroom found for %r" % args.name)
    return 0 if (slug and not note) else 2


if __name__ == "__main__":
    sys.exit(main())
