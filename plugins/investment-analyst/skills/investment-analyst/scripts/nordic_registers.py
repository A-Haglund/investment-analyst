#!/usr/bin/env python3
"""Official company-register lookup for Norway and Finland.

Tier-1 regulator sources: legal name, registration number, status, industry
code, homepage.  This is the keyless equivalent of what EU VIES already does
for Sweden in this toolkit.

ENDPOINTS (verified live, keyless, no auth, plain User-Agent)

  NORWAY - Brønnøysundregistrene (Enhetsregisteret)
    https://data.brreg.no/enhetsregisteret/api/enheter?navn=<name>&size=<n>
    https://data.brreg.no/enhetsregisteret/api/enheter/<organisasjonsnummer>
    Search answers a HAL envelope: `_embedded.enheter` holds the window and
    `page.totalElements` says how many entities matched in total.

  FINLAND - PRH avoindata (YTJ)
    https://avoindata.prh.fi/opendata-ytj-api/v3/companies?name=<name>&limit=<n>
    https://avoindata.prh.fi/opendata-ytj-api/v3/companies?businessId=<id>
    Answers `{"companies": [...], "totalResults": <n>}`.  There is no
    /companies/<id> path - it replies errorcode 1003 - and the businessId
    query needs the hyphen: "0112038-9" matches, "01120389" returns zero.

  DENMARK IS DELIBERATELY EXCLUDED.  cvrapi.dk answers QUOTA_EXCEEDED and its
  terms require an identifying User-Agent.  Both break hard project
  constraints: no metered-limit circumvention, no identifying request
  headers.  This is a known gap, not an oversight.

WHAT THIS MODULE HAS TO GET RIGHT

1. NORMALISE BEFORE YOU RANK.  The two register vocabularies do not overlap:
   a Brreg hit is {navn, organisasjonsnummer}, a PRH hit is {names,
   businessId}, and neither carries `legal_name` or `registration_number`.  An
   earlier version handed RAW hits to the matcher, which read the normalised
   keys - so every name compared against "" and nothing ever matched.  An
   exact legal name could not resolve, and the refusal it produced named
   nothing: "10 distinct companies (? (?), ? (?), ...)".  The ladder below
   only ever sees records that have already been through normalise_norway()
   or normalise_finland().

2. A STATUS IS DERIVED, NEVER COPIED.  Brreg has no `status` field at all; it
   has `konkurs`, `underAvvikling`, `underTvangsavviklingEllerTvangsopplosning`
   and `slettedato`.  PRH's `status` is an undocumented numeric code (live:
   "2" for every company sampled, active and long-ceased alike).  Both are
   derived here from fields whose meaning is legible, and a register-internal
   code is never handed out as though it were the common-vocabulary value - it
   is kept, clearly fenced off, under `register_codes`.

3. AN OUTAGE IS NOT AN ANSWER.  A DNS failure, a timeout, a 503 and a genuine
   zero-hit search are four different facts.  Transport failures raise
   RegisterOutage, which becomes SystemExit("DATA NOT AVAILABLE: ..."), and
   only a register that actually answered can establish an absence.

4. A WINDOW THAT WAS TRUNCATED DID NOT PROVE UNIQUENESS.  Both searches report
   how many entities matched in total, and both routinely return far fewer:
   "Equinor" is 179 matches in a window of 10, "Nokia" is 991 in a window of
   63 (PRH ignores `limit` outright - 3 and 100 both returned the same 63).
   Discarding that number lets a truncated window turn a refusal into a
   confident answer, so the rungs that depend on having seen every competitor
   refuse when it was truncated.  A rung that could not run is not a rung that
   passed.

THE RULE: refuse, never guess - the discipline issuer_feed.choose() applies to
newsrooms and company_resolve.py applies to "Volvo".  Attributing a register
record to the wrong entity is silent and looks identical to a correct answer.

    python nordic_registers.py "TELENOR ASA" --country NO   # resolves
    python nordic_registers.py Telenor --country NO         # refuses, names them
    python nordic_registers.py "Nokia Oyj" --country FI
    python nordic_registers.py 923609016 --country NO       # by orgnr
    python nordic_registers.py --selftest                   # offline, no socket

Python 3 standard library only.  Free, keyless.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

# No sibling imports and no sys.path surgery: this module is stdlib-only end
# to end.  It used to import _bootstrap without ever calling it, which is an
# invitation to grow a dependency by accident.

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Browser-shaped, with no identifying contact information in it.  The same
# string every other fetcher in this toolkit sends; changing it changes how
# every source sees the toolkit.
UA = "Mozilla/5.0 (compatible; investment-analyst-skill/1.0)"

TTL_REGISTER = 86400  # company registers move slowly; 24 hours
CACHE_DIR = os.path.join(tempfile.gettempdir(), "investment-analyst-cache",
                         "nordic_registers")

NO_REGISTER = "Brønnøysundregistrene (Enhetsregisteret)"
FI_REGISTER = "PRH avoindata (YTJ)"

_BRREG_SEARCH = "https://data.brreg.no/enhetsregisteret/api/enheter"
_BRREG_ENTITY = "https://data.brreg.no/enhetsregisteret/api/enheter/%s"
_PRH_SEARCH = "https://avoindata.prh.fi/opendata-ytj-api/v3/companies"


class RegisterOutage(Exception):
    """The register could not be asked.

    Deliberately its own type rather than a None return.  "The register did
    not answer" and "the register answered, with nothing" are different facts
    with different consequences, and the first must never be reported as the
    second - a network blip would then read as "this company does not exist".
    The previous implementation ended both fetchers with `... or []`, so they
    could not return None at all, the SystemExit guards downstream were
    unreachable dead code, and a 404, a timeout, a DNS failure and a genuine
    zero-hit search all produced the same silent (None, None).
    """


# --------------------------------------------------------------------------
# HTTP and cache
# --------------------------------------------------------------------------

def cache_path(key):
    """Hashed cache-key path.

    The full key is hashed, never truncated: two register URLs can share a
    long prefix and differ only in a trailing parameter, and a truncating key
    would serve one query's answer for the other.
    """
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, digest + ".json")


def http_get_json(url, timeout=60):
    """GET and parse JSON.  Returns (http_status, payload).

    Raises RegisterOutage for everything that means "the register did not
    answer": DNS failure, timeout, connection reset, 429, 5xx, or a 200 whose
    body is not JSON.  A 4xx other than 429 IS an answer - notably Brreg's 404
    for an organisation number that does not exist - so it comes back as
    (status, None) for the caller to interpret.
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, body = resp.getcode(), resp.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        if status == 429 or status >= 500:
            raise RegisterOutage("HTTP %s from %s" % (status, url))
        return status, None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise RegisterOutage("%s: %s" % (type(exc).__name__, exc))

    try:
        return status, json.loads(body)
    except ValueError as exc:
        # A 200 whose body is not JSON is a captive portal, an error page or a
        # partial read - an outage wearing a success code, not an empty
        # register.
        raise RegisterOutage("HTTP %s from %s: unparseable body (%s)"
                             % (status, url, exc))


def cached_get_json(url, timeout=60, ttl=TTL_REGISTER):
    """http_get_json() behind a 24h on-disk cache keyed on the FULL url.

    Keyed on the URL and nothing else, which is what fixes a real defect: the
    old keys were "no_name:<query>" and "fi_name:<query>", with the page size
    left out, so a limit=3 lookup poisoned a limit=50 search of the same name
    for twenty-four hours - the second caller silently received the first
    caller's three results, and computed its truncation flag off them.  The
    size is part of the URL, so keying on the URL cannot drop it.

    An outage is never cached: RegisterOutage propagates before anything is
    written, so a failed source is retried rather than remembered as an
    absence.
    """
    path = cache_path(url)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            blob = json.load(fh)
        if time.time() - blob["t"] < ttl:
            return blob["s"], blob["v"]
    except (OSError, ValueError, KeyError):
        pass

    status, payload = http_get_json(url, timeout=timeout)
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"t": time.time(), "s": status, "v": payload}, fh)
    except OSError:
        pass
    return status, payload


# --------------------------------------------------------------------------
# Name normalisation
# --------------------------------------------------------------------------

# Nordic legal forms only, and deliberately WITHOUT "group" and "holding".
# issuer_feed strips those two because MFN files a group and its parent under
# a single newsroom; a company register does the exact opposite - "X Holding
# AS" and "X AS" are two legal entities with two organisation numbers, and
# collapsing them here would merge the identities this module exists to keep
# apart.
_LEGAL_SUFFIX = re.compile(
    r"(?i)(?<![\w/])(asa|as|a/s|ans|nuf|sa|ba|da|kf|iks|sf|"
    r"oyj|oy|abp|ab|ky|ry|"
    r"aps|k/s|p/s|amba|fmba|smba|"
    r"plc|ltd|inc|corp|nv|se|publ)(?![\w/])\.?", re.UNICODE)


def fold(text):
    """Lowercase, strip diacritics, squeeze whitespace.

    A register spells "Orrön" with the umlaut and a caller typing from memory
    or from a ticker spells it "Orron"; NFKD splits the letter from its
    combining accent and the Mn filter drops the accent.  Deliberately not an
    ä->ae transliteration: the Nordic registers use the plain vowel when they
    drop the accent at all, never the digraph.  Note that this reaches å/ä/ö
    and not ø/æ/ð - those are letters in their own right, with no combining
    form for NFKD to split off, so "Bronnoysund" does not fold onto
    "Brønnøysund".  Handling them would mean a per-language transliteration
    table, which is a guess about which language a name is in.
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped).strip().lower()


def normalise(name):
    """The comparison form of a company name: folded, legal form removed.

    Parentheses go BEFORE the suffix regex, never after - "publ" is one of the
    alternatives and matches the bare word inside "(publ)", leaving orphan
    parentheses behind that then equal nothing.
    """
    n = fold(name).replace(",", " ").replace("(", " ").replace(")", " ")
    n = _LEGAL_SUFFIX.sub(" ", n)
    return re.sub(r"\s+", " ", n).strip()


def _text(value):
    """A stripped string, or None.  Never the empty string."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value or None


# --------------------------------------------------------------------------
# Registration numbers
# --------------------------------------------------------------------------

def looks_like_number(query):
    """True when the query is digits and separators only, not a name."""
    return bool(re.fullmatch(r"[\d\s.\-]+", (query or "").strip() or "x"))


def no_orgnr(query):
    """A Norwegian organisation number as nine bare digits, or None."""
    digits = re.sub(r"\D", "", query or "")
    return digits if len(digits) == 9 else None


def fi_business_id(query):
    """A Finnish businessId as PRH spells it - "0112038-9" - or None.

    The hyphen is load-bearing, and is why the previous implementation's
    direct lookup never worked: it stripped separators, and PRH answers
    `?businessId=01120389` with totalResults 0 while `?businessId=0112038-9`
    returns the company.
    """
    digits = re.sub(r"\D", "", query or "")
    if len(digits) != 8:
        return None
    return "%s-%s" % (digits[:7], digits[7])


# --------------------------------------------------------------------------
# The search window
# --------------------------------------------------------------------------

class Window(object):
    """What one search returned, plus how many entities it actually matched.

    `total` is the register's own count of matching entities and `entities` is
    what fitted in the page.  `truncated` answers "did I see every
    competitor?", which is a separate question from "did I find a match?" -
    the ladder below refuses on the former.  `total` is None when the register
    did not report one; unknown is not the same as "nothing was hidden", so
    `truncated` is None too and the rungs that need it refuse.
    """

    __slots__ = ("entities", "total", "source_url")

    def __init__(self, entities, total=None, source_url=None):
        self.entities = list(entities or [])
        self.total = total
        self.source_url = source_url

    @property
    def returned(self):
        return len(self.entities)

    @property
    def truncated(self):
        if self.total is None:
            return None
        return self.total > self.returned

    def as_dict(self):
        return {"returned": self.returned, "total": self.total,
                "truncated": self.truncated}

    def __repr__(self):
        return "Window(returned=%d, total=%r)" % (self.returned, self.total)


# --------------------------------------------------------------------------
# Norway - Brønnøysundregistrene
# --------------------------------------------------------------------------

def fetch_norway(query, limit=20, get=None):
    """Search Enhetsregisteret.  Returns a Window of RAW Brreg entities.

    Raises RegisterOutage when the register could not be reached.  `get` is
    the transport seam - a callable (url, timeout) -> (status, payload) - so
    the offline tests drive this function itself against captured payloads
    instead of reimplementing it.
    """
    get = get or cached_get_json
    query = (query or "").strip()
    if not query:
        return Window([], total=0)

    orgnr = no_orgnr(query) if looks_like_number(query) else None
    if orgnr:
        url = _BRREG_ENTITY % orgnr
        status, payload = get(url, 30)
        if status == 404:
            # The register answered, and its answer is "no such unit". That is
            # a finding, not a failure: an empty window with a total of zero.
            return Window([], total=0, source_url=url)
        if isinstance(payload, dict) and payload.get("organisasjonsnummer"):
            return Window([payload], total=1, source_url=url)
        # It answered, but not with a unit. Nothing was established either way.
        raise RegisterOutage("unexpected Enhetsregisteret reply (HTTP %s) for %s"
                             % (status, orgnr))

    url = _BRREG_SEARCH + "?" + urllib.parse.urlencode(
        {"navn": query, "size": str(int(limit))})
    status, payload = get(url, 30)
    if not isinstance(payload, dict):
        raise RegisterOutage("HTTP %s from the Enhetsregisteret name search"
                             % status)
    page = payload.get("page")
    total = page.get("totalElements") if isinstance(page, dict) else None
    entities = (payload.get("_embedded") or {}).get("enheter") or []
    return Window(entities, total=total, source_url=url)


def _no_flag(entity, key):
    """A Brreg boolean flag, or None when the payload does not carry it.

    None is the whole point.  `bool(entity.get("konkurs"))` reports False for
    a payload that never mentioned bankruptcy, which asserts "not bankrupt"
    about a field nobody consulted.  A check that could not run is not a check
    that passed.
    """
    if key not in entity:
        return None
    value = entity.get(key)
    return bool(value) if value is not None else None


def _no_status(entity):
    """Derive the common-vocabulary status from the fields Brreg really has.

    Brreg has NO `status` field.  The previous implementation read one anyway,
    ran a three-way mapping ladder over the string it never got, and stamped
    "UNKNOWN" on every Norwegian record it ever produced - including records
    whose own `bankrupt` field said True, two fields of one record openly
    contradicting each other.  The live flags are `konkurs`, `underAvvikling`,
    `underTvangsavviklingEllerTvangsopplosning` and `slettedato`; all four are
    present on a name-search hit, and each is checked here.

    Returns (status, basis).  Status is None - not the string "UNKNOWN" - when
    none of the flags was present, so an absent check stays absent instead of
    becoming a claim.
    """
    if _text(entity.get("slettedato")):
        return "DEREGISTERED", "slettedato=%s" % _text(entity.get("slettedato"))

    flags = [("konkurs", "BANKRUPTCY"),
             ("underTvangsavviklingEllerTvangsopplosning",
              "COMPULSORY_LIQUIDATION"),
             ("underAvvikling", "LIQUIDATION")]
    seen = []
    for key, label in flags:
        value = _no_flag(entity, key)
        if value is None:
            continue
        seen.append(key)
        if value:
            return label, "%s=true" % key
    if not seen:
        return None, None
    if len(seen) < len(flags):
        # Part of the ladder ran and part of it did not. "ACTIVE" here would
        # claim that the rungs which never executed came back clean.
        return None, "only %s present in the payload" % ", ".join(seen)
    return "ACTIVE", "konkurs, underAvvikling and tvangsavvikling all false"


def normalise_norway(entity, window=None):
    """One raw Brreg entity -> the common record shape."""
    if not entity:
        return None

    orgnr = _text(entity.get("organisasjonsnummer"))
    legal_name = _text(entity.get("navn"))
    form = entity.get("organisasjonsform") or {}
    # naeringskode1 is a dict {"kode": "06.100", "beskrivelse": ...}, not a
    # string. Passed through whole it reached the CLI and printed as a Python
    # dict repr under a field the record calls "industry_code".
    industry = entity.get("naeringskode1") or {}
    status, basis = _no_status(entity)

    homepage = _text(entity.get("hjemmeside"))
    if homepage and not re.match(r"(?i)^https?://", homepage):
        # Brreg stores "www.equinor.com", with no scheme.
        homepage = "https://" + homepage

    return {
        "country": "NO",
        "register": NO_REGISTER,
        "legal_name": legal_name,
        "name_forms": [legal_name] if legal_name else [],
        "registration_number": orgnr,
        "status": status,
        "status_basis": basis,
        "bankrupt": _no_flag(entity, "konkurs"),
        "homepage": homepage,
        "industry_code": _text(industry.get("kode")),
        "industry_description": _text(industry.get("beskrivelse")),
        "legal_form_code": _text(form.get("kode")),
        "legal_form": _text(form.get("beskrivelse")),
        "employees": entity.get("antallAnsatte"),
        "registration_date": _text(
            entity.get("registreringsdatoEnhetsregisteret")),
        "end_date": _text(entity.get("slettedato")),
        # Per-company provenance: the URL that returns this one entity, not a
        # search landing page that would be identical for every company.
        "source_url": (_BRREG_ENTITY % orgnr) if orgnr else None,
        "register_codes": {
            "organisasjonsform": _text(form.get("kode")),
            "konkurs": entity.get("konkurs"),
            "underAvvikling": entity.get("underAvvikling"),
            "underTvangsavviklingEllerTvangsopplosning":
                entity.get("underTvangsavviklingEllerTvangsopplosning"),
        },
        "search_window": window.as_dict() if window is not None else None,
        "raw": entity,
    }


# --------------------------------------------------------------------------
# Finland - PRH avoindata (YTJ)
# --------------------------------------------------------------------------

# PRH name records carry a type code. Verified against live payloads:
#   "1" the company name, "2" a parallel company name, "3" an auxiliary trade
#   name (a branch or shop sign, NOT the company's legal identity).
_FI_NAME_COMPANY = "1"
_FI_NAME_PARALLEL = "2"

# Language codes on PRH's per-language description lists.
_FI_LANG_FI, _FI_LANG_SV, _FI_LANG_EN = "1", "2", "3"


def fetch_finland(query, limit=50, get=None):
    """Search PRH YTJ.  Returns a Window of RAW PRH company records.

    `limit` is sent because PRH documents it, and is NOT trusted: live, both
    limit=3 and limit=100 returned the same 63 companies out of a reported 991
    for "Nokia".  That is exactly why the window's `total` must come from
    PRH's own `totalResults` and never from the limit we asked for.
    """
    get = get or cached_get_json
    query = (query or "").strip()
    if not query:
        return Window([], total=0)

    business_id = fi_business_id(query) if looks_like_number(query) else None
    if business_id:
        url = _PRH_SEARCH + "?" + urllib.parse.urlencode(
            {"businessId": business_id})
    else:
        url = _PRH_SEARCH + "?" + urllib.parse.urlencode(
            {"name": query, "limit": str(int(limit))})

    # PRH is slow and verbose - 208 KB for a two-result query was the measured
    # figure - so the timeout is generous rather than optimistic.
    status, payload = get(url, 120)
    if not isinstance(payload, dict):
        raise RegisterOutage("HTTP %s from PRH avoindata" % status)
    companies = payload.get("companies")
    if companies is None:
        raise RegisterOutage("the PRH avoindata reply carried no 'companies' key")
    return Window(companies, total=payload.get("totalResults"), source_url=url)


def _fi_description(descriptions, prefer=(_FI_LANG_EN, _FI_LANG_FI, _FI_LANG_SV)):
    """One human-readable string out of PRH's per-language description list."""
    by_lang = {}
    for item in descriptions or []:
        if isinstance(item, dict):
            by_lang.setdefault(_text(item.get("languageCode")),
                               _text(item.get("description")))
    for lang in prefer:
        if by_lang.get(lang):
            return by_lang[lang]
    for value in by_lang.values():
        if value:
            return value
    return None


def _fi_names(names_list, name_type):
    """PRH name records of one type, most recent first.

    Sorted on (still current, endDate, registrationDate) descending, so a
    record with no endDate outranks every ended one.  Position in the list is
    deliberately not consulted: PRH does not return the current name first,
    and on businessId 0194099-3 - 34 name records, every single one carrying
    an endDate - the old `names_list[0]` fallback fired live and picked an
    auxiliary trade name to report as the company's legal name.
    """
    typed = [n for n in (names_list or [])
             if isinstance(n, dict) and _text(n.get("type")) == name_type]
    return sorted(typed,
                  key=lambda n: (0 if _text(n.get("endDate")) else 1,
                                 _text(n.get("endDate")) or "",
                                 _text(n.get("registrationDate")) or ""),
                  reverse=True)


def pick_fi_name(names_list):
    """The company's own name (type 1), the current one where there is one.

    Returns None when the payload holds no type-1 record at all.  It never
    falls back to "whatever came first": an auxiliary trade name is a shop
    sign, not a legal identity, and returning one under `legal_name` is the
    kind of wrong answer that looks exactly like a right one.
    """
    ranked = _fi_names(names_list, _FI_NAME_COMPANY)
    return _text(ranked[0].get("name")) if ranked else None


def _fi_status(entity):
    """Derive the status from the PRH fields whose meaning is legible.

    PRH's own `status` is an undocumented numeric code.  Sampled live across
    roughly 470 companies it was "2" for every one of them, active and
    long-ceased alike, so it discriminates nothing; `tradeRegisterStatus` is
    likewise a bare code ("1", "3", "4").  The previous implementation
    compared `status` against the string "ACTIVE", never matched, and then
    upper-cased the raw code into the record - publishing "2" in the field
    callers read as the normalised status.

    So the derivation uses the two fields that say what they mean:
      - `endDate`, the register's own end date for the company;
      - `companySituations`, whose "KONK" entry is a bankruptcy (verified on
        businessId 0211270-0, registered 2020-09-18).
    Any other situation type is not guessed at: an unrecognised entry means
    the register is recording something about this company that this code
    cannot read, and answering ACTIVE over it would be a guess.

    Returns (status, basis, bankrupt).  `bankrupt` is None when
    `companySituations` was absent from the payload - the field was not
    consulted, so nothing about bankruptcy was established.
    """
    situations = entity.get("companySituations")
    types = ([_text(s.get("type")) for s in situations if isinstance(s, dict)]
             if isinstance(situations, list) else None)
    bankrupt = ("KONK" in types) if types is not None else None

    end_date = _text(entity.get("endDate"))
    if end_date:
        return "CEASED", "endDate=%s" % end_date, bankrupt
    if types is None:
        return None, "companySituations absent from the payload", bankrupt
    if "KONK" in types:
        return "BANKRUPTCY", "companySituations includes KONK", bankrupt
    unknown = sorted(t for t in types if t)
    if unknown:
        return (None,
                "unrecognised companySituations %s" % ", ".join(unknown),
                bankrupt)
    return "ACTIVE", "no endDate and no company situations recorded", bankrupt


def normalise_finland(entity, window=None):
    """One raw PRH company record -> the common record shape."""
    if not entity:
        return None

    # businessId is a dict {"value": "0112038-9", "registrationDate": ...,
    # "source": "3"}, not a string. Passed through whole it made the primary
    # key of every Finnish record an unusable object.
    business_id = entity.get("businessId")
    if isinstance(business_id, dict):
        registration_number = _text(business_id.get("value"))
        id_registered = _text(business_id.get("registrationDate"))
    else:
        registration_number = _text(business_id)
        id_registered = None

    names = entity.get("names") or []
    legal_name = pick_fi_name(names)
    # A current parallel company name is a name the company is registered
    # under, so it can legitimately match a query; an auxiliary trade name
    # (type 3) cannot, and is left out.
    parallel = [_text(n.get("name"))
                for n in _fi_names(names, _FI_NAME_PARALLEL)
                if not _text(n.get("endDate"))]
    name_forms = [n for n in ([legal_name] + parallel) if n]

    # mainBusinessLine has no "code"/"line" keys - both were always None. The
    # live shape is {"type": "86950", "descriptions": [...], "typeCodeSet":
    # "TOIMI4"}, where `type` is the TOL industry code.
    business_line = entity.get("mainBusinessLine") or {}
    current_form = None
    for form in entity.get("companyForms") or []:
        if isinstance(form, dict) and not _text(form.get("endDate")):
            current_form = form
            break

    status, basis, bankrupt = _fi_status(entity)

    return {
        "country": "FI",
        "register": FI_REGISTER,
        "legal_name": legal_name,
        "name_forms": name_forms,
        "registration_number": registration_number,
        "status": status,
        "status_basis": basis,
        "bankrupt": bankrupt,
        # PRH publishes no homepage field. None here means "this register does
        # not carry it", which is what the caller needs to know.
        "homepage": None,
        "industry_code": _text(business_line.get("type")),
        "industry_description": _fi_description(business_line.get("descriptions")),
        "legal_form_code": _text((current_form or {}).get("type")),
        "legal_form": _fi_description((current_form or {}).get("descriptions")),
        "employees": None,  # not published by PRH
        "registration_date": _text(entity.get("registrationDate")) or id_registered,
        "end_date": _text(entity.get("endDate")),
        # Per-company provenance. The old value was PRH's generic search
        # landing page - the same URL for every company, which documents the
        # register rather than the record.
        "source_url": ((_PRH_SEARCH + "?" + urllib.parse.urlencode(
            {"businessId": registration_number}))
            if registration_number else None),
        # The register's own codes, kept because they are the source's own
        # words, and fenced off here so they can never be mistaken for the
        # normalised `status` above.
        "register_codes": {
            "status": _text(entity.get("status")),
            "tradeRegisterStatus": _text(entity.get("tradeRegisterStatus")),
            "companyForm": _text((current_form or {}).get("type")),
            "mainBusinessLineCodeSet": _text(business_line.get("typeCodeSet")),
            "companySituations": [_text(s.get("type"))
                                  for s in (entity.get("companySituations") or [])
                                  if isinstance(s, dict)],
        },
        "search_window": window.as_dict() if window is not None else None,
        "raw": entity,
    }


# --------------------------------------------------------------------------
# The refusal ladder
# --------------------------------------------------------------------------

def _country_word(country):
    return {"NO": "Norwegian", "FI": "Finnish"}.get(country, country)


def _listing(records, cap=8):
    """Name the candidates.  Reads NORMALISED records, which is the fix.

    The message is the entire product of a refusal, and "10 distinct companies
    (? (?), ? (?), ...)" tells the caller nothing and cannot be acted on.
    These fields have values here only because normalisation runs first.
    """
    shown = ", ".join("%s (%s)" % (r.get("legal_name") or "unnamed entity",
                                   r.get("registration_number") or "no number")
                      for r in records[:cap])
    if len(records) > cap:
        shown += ", and %d more" % (len(records) - cap)
    return shown


def ambiguous_note(country, name, records):
    return ("COMPANY_IDENTITY_AMBIGUOUS: %d distinct %s companies match %r (%s). "
            "Attributing a register record to the wrong entity is silent and "
            "looks identical to a correct answer - re-run with the exact "
            "registered legal name, or with the registration number."
            % (len(records), _country_word(country), name, _listing(records)))


def unmatched_note(country, name, records, window=None):
    """The window held candidates and none of them is the company.

    Kept separate from the ambiguous case because the remedy differs: nothing
    here IS the company, so the listing is a lead rather than a shortlist.
    The single-candidate case is included on purpose - one unrelated hit is an
    absence of alternatives, not evidence.

    When the window was truncated the truncation is APPENDED rather than
    substituted for this message.  Both facts are true and the caller needs
    both: what the register did return, and that it did not return all of it.
    Brreg's name search in particular is a relevance-ranked full-text match -
    a nonsense query still reports hundreds of thousands of "matches" - so
    over a truncated window it can never establish that a company does not
    exist, and saying only "nothing matched" would overstate what ran.
    """
    note = ("COMPANY_IDENTITY_UNMATCHED: no %s register entity matches %r. "
            "The search returned %d unrelated result(s) (%s); none of them is "
            "taken, because a result set with no match is not evidence for its "
            "first entry."
            % (_country_word(country), name, len(records), _listing(records)))
    if window is not None and window.truncated:
        note += (" This is also not an established absence: %d of %s matching "
                 "entities were returned, so the rest were never examined."
                 % (window.returned, window.total))
    return note


def truncated_note(country, name, window, rung):
    """A rung that needed the whole result set, run against part of it.

    This is the "a check that could not run is not a check that passed" case,
    and it is a refusal rather than a footnote because the alternative is a
    confident answer resting on entities nobody looked at.
    """
    return ("REGISTER_WINDOW_TRUNCATED: the %s register matched %s entities "
            "for %r but returned only %d, so %s. Re-run with the exact "
            "registered legal name or the registration number, either of "
            "which identifies one entity outright."
            % (_country_word(country),
               "an unreported number of" if window.total is None
               else str(window.total),
               name, window.returned, rung))


def invalid_query_note(country, name):
    return ("COMPANY_IDENTITY_UNMATCHED: %r is not a searchable %s company "
            "name or registration number." % (name, _country_word(country)))


def rank(query, records):
    """Split normalised records into (exact, extensions) on name.

    `exact`      - a registered name form whose normalised value EQUALS the
                   query's.
    `extensions` - one the query is a word-boundary prefix of: "Telenor"
                   against "Telenor Norge".  Candidates the query might be a
                   shortening of, and therefore does not distinguish.

    Unanchored substring containment is deliberately absent in both
    directions.  `needle in name` puts "Telenor Pensjonskasse" on equal terms
    with "TELENOR ASA" for the query "Telenor"; `name in needle` is worse
    still, since any short unrelated entity whose name happens to be a token
    of the query wins outright.  A word-boundary prefix is the only
    containment relation that carries evidence about identity.
    """
    needle = normalise(query)
    exact, extensions = [], []
    if not needle:
        return [], []
    for record in records:
        forms = [f for f in (normalise(x)
                             for x in record.get("name_forms") or []) if f]
        if any(f == needle for f in forms):
            exact.append(record)
        elif any(f.startswith(needle + " ") for f in forms):
            extensions.append(record)
    key = lambda r: (r.get("registration_number") or "")  # noqa: E731
    return sorted(exact, key=key), sorted(extensions, key=key)


def choose(country, query, records, window=None):
    """One record out of a NORMALISED candidate set, or a refusal.

    Returns (record, note); at most one of the two is ever set.

    THE LADDER, strictest rung first.

      0. The query is a registration number and one record carries it.  A
         registration number is the register's own primary key, so this rung
         is immune to everything below, a truncated window included.

      1. LITERAL name equality, before any legal form is stripped.  normalise()
         collapses "TELENOR ASA" and "TELENOR A/S" onto the same "telenor",
         which is right for matching and wrong for discrimination - one is a
         complete registered name, the other is a different company.  The
         folded-but-unstripped form still tells them apart, so it is consulted
         first, and it is what makes the refusal's own advice ("re-run with
         the exact registered legal name") an instruction with a valid input.
         Taken even on a truncated window: both registers rank literal name
         matches at the top of the result set, so a same-named competitor
         comes back alongside - observed directly, the two distinct entities
         both registered as "TELENOR PAKISTAN" appear together in a window of
         ten out of ninety-five matches.

      2. Normalised equality, no competing extension, and a window that was
         NOT truncated.  Uniqueness is the claim this rung makes, and it can
         only be made about a result set that was seen in full.

      3. The same match against a truncated window -> refuse.  The match may
         well be right; what is missing is any evidence that it is the only
         one, which is precisely what the rung asserts.

      4. Several exact matches, or one exact plus an extension the query is
         also a prefix of -> refuse: the query does not discriminate.

      5. Extensions only -> refuse.  A word-boundary prefix says two names are
         related, not that they are one company; "Telenor" is a prefix of
         "Telenor Pensjonskasse", which is a pension fund.

      6. Nothing matched -> refuse whatever the result-set size, naming what
         was returned, and add the truncation when there was one: an absence
         established over part of a result set is not an absence.  An empty
         window is the one case with nothing to name, so it reports the
         truncation alone.
    """
    query = _text(query)
    if not query:
        return None, invalid_query_note(country, query)

    if not records:
        if window is not None and window.truncated:
            return None, truncated_note(
                country, query, window,
                "the absence of a match was established over part of the "
                "result set only")
        # A register that answered with nothing is a clean, honest absence.
        return None, None

    # Rung 0 - the register's own primary key.
    if looks_like_number(query):
        wanted = no_orgnr(query) if country == "NO" else fi_business_id(query)
        if wanted:
            hits = [r for r in records
                    if _text(r.get("registration_number")) == wanted]
            if len(hits) == 1:
                return hits[0], None
            if len(hits) > 1:
                return None, ambiguous_note(country, query, hits)

    # Rung 1 - the literal registered name.
    literal = fold(query)
    if literal:
        strict = [r for r in records
                  if any(fold(f) == literal for f in r.get("name_forms") or [])]
        if len(strict) == 1:
            return strict[0], None
        if len(strict) > 1:
            return None, ambiguous_note(country, query, strict)

    exact, extensions = rank(query, records)
    truncated = window.truncated if window is not None else None

    if len(exact) == 1 and not extensions:
        if truncated:
            return None, truncated_note(
                country, query, window,
                "%r matched only after its legal form was stripped, and the "
                "entities that were never returned could match it the same "
                "way" % query)
        return exact[0], None
    if exact:
        return None, ambiguous_note(country, query, exact + extensions)
    if extensions:
        return None, ambiguous_note(country, query, extensions)
    return None, unmatched_note(country, query, records, window)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

_COUNTRIES = {
    "NO": (fetch_norway, normalise_norway, NO_REGISTER, 20),
    "FI": (fetch_finland, normalise_finland, FI_REGISTER, 50),
}


def _country(country):
    country = (country or "").strip().upper()
    if country not in _COUNTRIES:
        raise SystemExit("DATA NOT AVAILABLE: country %r not supported "
                         "(use NO or FI; Denmark is out of scope - see the "
                         "module docstring)" % country)
    return country


def search(name, country, limit=None, get=None):
    """Every candidate the register returned, normalised.  No disambiguation.

    Returns (records, window).  The caller decides what to do with several
    candidates, and `window.truncated` tells it whether it is looking at all
    of them.

    Raises SystemExit("DATA NOT AVAILABLE: ...") when the register could not
    be reached - never an empty list, because an outage and an empty register
    are not the same answer.
    """
    country = _country(country)
    fetch, normalise_one, register, default_limit = _COUNTRIES[country]
    name = _text(name)
    if name is None:
        return [], Window([], total=0)
    try:
        window = fetch(name, limit=default_limit if limit is None else limit,
                       get=get)
    except RegisterOutage as exc:
        raise SystemExit("DATA NOT AVAILABLE: %s could not be reached (%s). "
                         "This is an outage, not a finding about %r."
                         % (register, exc, name))
    records = [r for r in (normalise_one(e, window) for e in window.entities)
               if r]
    return records, window


def lookup(name_or_number, country, limit=None, get=None):
    """Resolve one company.  ALWAYS returns (record, note).

    At most one of the pair is set; both are None when the register answered
    cleanly and nothing matched at all.  The previous version returned three
    different shapes out of the same function - a bare dict on success,
    (None, note) on a refusal and (None, None) on an empty result - so every
    call site had to guess what it had been handed, and the CLI's isinstance()
    dance was that guess written down.

    Raises SystemExit("DATA NOT AVAILABLE: ...") on an outage, per this
    toolkit's convention.  Note that SystemExit does not inherit from
    Exception, so a soft caller must catch it explicitly.
    """
    country = _country(country)
    name = _text(name_or_number)
    if name is None:
        # None, "" or "   ". Previously an AttributeError out of the matcher.
        return None, invalid_query_note(country, name_or_number)
    records, window = search(name, country, limit=limit, get=get)
    return choose(country, name, records, window)


# --------------------------------------------------------------------------
# Selftest - offline.  No socket is opened: every case drives the real
# fetch / normalise / choose path against a captured register payload,
# injected through the `get` transport seam that fetch_*() already provides.
# --------------------------------------------------------------------------

def _no_entity(navn, orgnr, **over):
    """A Brreg name-search hit, trimmed to the fields this module reads."""
    entity = {
        "organisasjonsnummer": orgnr,
        "navn": navn,
        "organisasjonsform": {"kode": "ASA",
                              "beskrivelse": "Allmennaksjeselskap"},
        "naeringskode1": {"kode": "61.100",
                          "beskrivelse": "Kabelbasert, satellittbasert og "
                                         "trådløs telekommunikasjon"},
        "konkurs": False,
        "underAvvikling": False,
        "underTvangsavviklingEllerTvangsopplosning": False,
        "slettedato": None,
        "registreringsdatoEnhetsregisteret": "1995-03-12",
    }
    entity.update(over)
    return entity


def _fi_entity(name, business_id, **over):
    """A PRH company record, trimmed to the fields this module reads."""
    entity = {
        "businessId": {"value": business_id, "registrationDate": "1978-03-15",
                       "source": "3"},
        "names": [{"name": name, "type": "1", "registrationDate": "1997-09-01",
                   "version": 1, "source": "1"}],
        "companyForms": [{"type": "17", "registrationDate": "1997-09-01",
                          "descriptions": [
                              {"languageCode": "1",
                               "description": "Julkinen osakeyhtiö"},
                              {"languageCode": "3",
                               "description": "Public limited company"}]}],
        "mainBusinessLine": {"type": "70100", "typeCodeSet": "TOIMI4",
                             "descriptions": [
                                 {"languageCode": "3",
                                  "description": "Activities of head offices"},
                                 {"languageCode": "1",
                                  "description": "Pääkonttorien toiminta"}]},
        "companySituations": [],
        "status": "2",
        "tradeRegisterStatus": "1",
        "registrationDate": "1978-03-15",
        "endDate": None,
    }
    entity.update(over)
    return entity


# Observed on data.brreg.no: "Telenor" matches 95 entities, and the window of
# ten holds two whose registered names collapse to "telenor" once the legal
# form is stripped, plus two distinct companies literally named TELENOR
# PAKISTAN. This is the module's worked example, and it is Norwegian - the
# previous file's fixture used Swedish organisation numbers and a Swedish
# company form ("AB") that Brreg never returns.
_NO_TELENOR = [
    _no_entity("TELENOR ASA", "982463718", hjemmeside="www.telenor.com",
               antallAnsatte=15000),
    _no_entity("TELENOR A/S", "814742342", naeringskode1=None,
               organisasjonsform={"kode": "UTLA",
                                  "beskrivelse": "Utenlandsk enhet"}),
    _no_entity("TELENOR PENSJONSKASSE", "947316281",
               organisasjonsform={"kode": "PK", "beskrivelse": "Pensjonskasse"},
               naeringskode1={"kode": "65.300",
                              "beskrivelse": "Pensjonskasser"}),
    _no_entity("TELENOR PAKISTAN", "993373206",
               organisasjonsform={"kode": "NUF",
                                  "beskrivelse": "Norskregistrert "
                                                 "utenlandsk foretak"}),
    _no_entity("TELENOR PAKISTAN", "993373257",
               organisasjonsform={"kode": "UTLA",
                                  "beskrivelse": "Utenlandsk enhet"}),
]


def _fake_get(routes):
    """A transport answering from {url_fragment: (status, payload) | error}."""
    def get(url, timeout=None):
        for fragment, reply in routes.items():
            if fragment in url:
                if isinstance(reply, BaseException):
                    raise reply
                return reply
        return 404, None
    return get


def _no_search_body(entities, total):
    return {"_embedded": {"enheter": list(entities)},
            "page": {"size": len(entities), "totalElements": total}}


def _selftest():
    ok = 0

    # --- name normalisation -------------------------------------------------
    assert fold("Orrön Energy") == "orron energy"
    assert normalise("TELENOR ASA") == "telenor"
    assert normalise("Telenor A/S") == "telenor"
    assert normalise("Nokia Oyj") == "nokia"
    assert normalise("Scandinavian Enviro Systems AB (publ)") == \
        "scandinavian enviro systems"
    # "Holding" is NOT stripped: in a company register the holding company and
    # its operating subsidiary are two entities with two numbers.
    assert normalise("Aker Holding AS") == "aker holding"
    assert normalise("") == ""
    ok += 1

    # --- registration numbers ----------------------------------------------
    assert no_orgnr("923 609 016") == "923609016"
    assert no_orgnr("12345") is None
    # PRH needs the hyphen; without it the live query returns zero results.
    assert fi_business_id("01120389") == "0112038-9"
    assert fi_business_id("0112038-9") == "0112038-9"
    assert fi_business_id("0112038") is None
    ok += 1

    # --- Norway: the normalised shape --------------------------------------
    record = normalise_norway(_NO_TELENOR[0])
    assert record["legal_name"] == "TELENOR ASA"
    assert record["registration_number"] == "982463718"
    # industry_code is the code, not the {kode, beskrivelse} dict.
    assert record["industry_code"] == "61.100", record["industry_code"]
    assert record["industry_description"].startswith("Kabelbasert")
    assert record["homepage"] == "https://www.telenor.com"
    assert record["source_url"].endswith("/enheter/982463718")
    # Brreg has no `status` field: this is derived from the live flags.
    assert record["status"] == "ACTIVE", record["status"]
    assert record["bankrupt"] is False
    ok += 1

    # A bankrupt unit must not report two fields that contradict each other.
    bankrupt = normalise_norway(_no_entity("1VASK AS", "915330193",
                                           konkurs=True))
    assert bankrupt["status"] == "BANKRUPTCY", bankrupt["status"]
    assert bankrupt["bankrupt"] is True
    liq = normalise_norway(_no_entity("&MORE AS", "999888777",
                                      underAvvikling=True))
    assert liq["status"] == "LIQUIDATION"
    assert liq["bankrupt"] is False
    gone = normalise_norway(_no_entity("SLETTET AS", "999888666",
                                       slettedato="2020-01-01"))
    assert gone["status"] == "DEREGISTERED"
    ok += 1

    # A field nobody consulted is None, never False. `bool(entity.get(...))`
    # would assert "not bankrupt" about a payload that never said so.
    sparse = normalise_norway({"organisasjonsnummer": "123456789",
                               "navn": "Test AS"})
    assert sparse["bankrupt"] is None, sparse["bankrupt"]
    assert sparse["status"] is None, sparse["status"]
    assert sparse["industry_code"] is None
    assert sparse["homepage"] is None
    ok += 1

    # --- Finland: the normalised shape -------------------------------------
    nokia = normalise_finland(_fi_entity("Nokia Oyj", "0112038-9"))
    # businessId is a dict in the live payload; the record holds a string.
    assert nokia["registration_number"] == "0112038-9", nokia
    assert isinstance(nokia["registration_number"], str)
    assert nokia["legal_name"] == "Nokia Oyj"
    assert nokia["industry_code"] == "70100", nokia["industry_code"]
    assert nokia["industry_description"] == "Activities of head offices"
    assert nokia["legal_form"] == "Public limited company"
    # The register-internal code is kept, and is never the normalised status.
    assert nokia["status"] == "ACTIVE", nokia["status"]
    assert nokia["register_codes"]["status"] == "2"
    assert nokia["register_codes"]["tradeRegisterStatus"] == "1"
    assert nokia["bankrupt"] is False
    assert "businessId=0112038-9" in nokia["source_url"]
    ok += 1

    # PRH name records carry a type. Auxiliary trade names (type 3) are shop
    # signs, not identities, and here every record has an endDate - the exact
    # shape that made the old `names_list[0]` fallback fire live.
    ceased = normalise_finland(_fi_entity(
        "ignored", "0194099-3", endDate="2026-04-30", tradeRegisterStatus="4",
        names=[{"name": "NeuroFysio Nokia", "type": "3",
                "registrationDate": "2025-03-31", "endDate": "2026-04-30"},
               {"name": "Fysios Oy", "type": "1",
                "registrationDate": "2017-12-31", "endDate": "2024-07-16"},
               {"name": "Fysios Mehiläinen Oy", "type": "1",
                "registrationDate": "2024-07-16", "endDate": "2026-04-30"}]))
    assert ceased["legal_name"] == "Fysios Mehiläinen Oy", ceased["legal_name"]
    assert ceased["status"] == "CEASED", ceased["status"]
    ok += 1

    # A KONK company situation is the Finnish bankruptcy signal.
    konk = normalise_finland(_fi_entity(
        "Konkurssi Oy", "0211270-0",
        companySituations=[{"type": "KONK", "registrationDate": "2020-09-18"}]))
    assert konk["status"] == "BANKRUPTCY", konk["status"]
    assert konk["bankrupt"] is True
    # An unreadable situation type is not silently treated as "fine".
    odd = normalise_finland(_fi_entity("Odd Oy", "0000001-9",
                                       companySituations=[{"type": "ZZZZ"}]))
    assert odd["status"] is None, odd["status"]
    assert odd["bankrupt"] is False
    # No companySituations key at all: nothing checked, so nothing claimed.
    unchecked = normalise_finland({"businessId": {"value": "0000002-7"},
                                   "names": [{"name": "Bare Oy", "type": "1"}]})
    assert unchecked["bankrupt"] is None, unchecked["bankrupt"]
    assert unchecked["status"] is None
    ok += 1

    # --- lookup(): the exact legal name resolves ----------------------------
    wide = _fake_get({"enheter?": (200, _no_search_body(_NO_TELENOR, 95))})
    record, note = lookup("TELENOR ASA", "NO", get=wide)
    assert note is None, note
    assert record["registration_number"] == "982463718", record
    # Case and diacritics must not matter.
    record, note = lookup("telenor asa", "NO", get=wide)
    assert note is None and record["registration_number"] == "982463718"
    ok += 1

    # --- lookup(): the shared brand refuses, and NAMES the candidates -------
    record, note = lookup("Telenor", "NO", get=wide)
    assert record is None
    assert note.startswith("COMPANY_IDENTITY_AMBIGUOUS"), note
    assert "TELENOR ASA" in note and "982463718" in note, note
    assert "TELENOR A/S" in note and "814742342" in note, note
    ok += 1

    # Two entities with the identical registered name: literal equality cannot
    # separate them either, so this refuses rather than taking the first.
    record, note = lookup("Telenor Pakistan", "NO", get=wide)
    assert record is None
    assert note.startswith("COMPANY_IDENTITY_AMBIGUOUS"), note
    assert "993373206" in note and "993373257" in note, note
    ok += 1

    # A word-boundary prefix is not an identity: a pension fund is not the
    # telco, even when it is the only candidate in the window.
    only_fund = _fake_get(
        {"enheter?": (200, _no_search_body([_NO_TELENOR[2]], 1))})
    record, note = lookup("Telenor", "NO", get=only_fund)
    assert record is None, record
    assert note.startswith("COMPANY_IDENTITY_AMBIGUOUS"), note
    ok += 1

    # --- a truncated window cannot prove uniqueness -------------------------
    partial = _fake_get({
        "/enheter/982463718": (200, _NO_TELENOR[0]),
        "enheter?": (200, _no_search_body([_NO_TELENOR[0]], 95))})
    record, note = lookup("Telenor", "NO", get=partial)
    assert record is None, record
    assert note.startswith("REGISTER_WINDOW_TRUNCATED"), note
    assert "95" in note, note
    # The literal registered name still resolves off the same window: rung 1
    # does not depend on having seen every competitor.
    record, note = lookup("TELENOR ASA", "NO", get=partial)
    assert note is None and record["registration_number"] == "982463718"
    # Neither does the registration number.
    record, note = lookup("982463718", "NO", get=partial)
    assert note is None and record["legal_name"] == "TELENOR ASA", (record, note)
    ok += 1

    # An absence found inside a truncated window is not an absence.
    empty_window = _fake_get({"enheter?": (200, _no_search_body([], 95))})
    record, note = lookup("Nothing Here AS", "NO", get=empty_window)
    assert record is None
    assert note.startswith("REGISTER_WINDOW_TRUNCATED"), note
    # A genuinely empty result set from a register that answered is clean.
    truly_empty = _fake_get({"enheter?": (200, _no_search_body([], 0))})
    record, note = lookup("Nothing Here AS", "NO", get=truly_empty)
    assert record is None and note is None, (record, note)
    # Unrelated hits in a truncated window: both facts belong in the refusal -
    # what came back, and that not all of it did. Brreg's full-text name
    # search reports six figures of "matches" for a nonsense query, so it can
    # never establish that a company does not exist.
    record, note = lookup("Zzzqq Notacompany AS", "NO", get=wide)
    assert record is None, record
    assert note.startswith("COMPANY_IDENTITY_UNMATCHED"), note
    assert "TELENOR ASA" in note, note
    assert "not an established absence" in note, note
    # The same set, seen in full: an ordinary unmatched refusal.
    complete = _fake_get({"enheter?": (200, _no_search_body(_NO_TELENOR, 5))})
    record, note = lookup("Zzzqq Notacompany AS", "NO", get=complete)
    assert note.startswith("COMPANY_IDENTITY_UNMATCHED"), note
    assert "not an established absence" not in note, note
    ok += 1

    # --- an outage is not an absence ---------------------------------------
    # 503, a timeout and an unparseable body must all reach the caller as
    # SystemExit, never as "no such company". SystemExit does not inherit
    # from Exception, so a soft caller has to name it.
    for reply in ((503, None),
                  RegisterOutage("TimeoutError: timed out"),
                  RegisterOutage("HTTP 200: unparseable body")):
        down = _fake_get({"enheter?": reply})
        try:
            lookup("TELENOR ASA", "NO", get=down)
        except SystemExit as exc:
            assert "DATA NOT AVAILABLE" in str(exc), exc
        else:
            raise AssertionError("an outage must not return an answer: %r"
                                 % (reply,))
    ok += 1

    # A 404 on a direct organisation-number lookup IS an answer: no such unit.
    missing = _fake_get({"/enheter/999999999": (404, None)})
    record, note = lookup("999999999", "NO", get=missing)
    assert record is None and note is None, (record, note)
    ok += 1

    # --- Finland end to end -------------------------------------------------
    fi_get = _fake_get({"companies?": (200, {
        "companies": [_fi_entity("Nokia Oyj", "0112038-9")],
        "totalResults": 1})})
    record, note = lookup("Nokia Oyj", "FI", get=fi_get)
    assert note is None, note
    assert record["registration_number"] == "0112038-9"
    assert record["status"] == "ACTIVE"
    # 63 of 991 is the live "Nokia" window: the bare brand must refuse.
    fi_wide = _fake_get({"companies?": (200, {
        "companies": [_fi_entity("Nokia Oyj", "0112038-9")],
        "totalResults": 991})})
    record, note = lookup("Nokia", "FI", get=fi_wide)
    assert record is None, record
    assert note.startswith("REGISTER_WINDOW_TRUNCATED"), note
    ok += 1

    # --- the contract itself ------------------------------------------------
    # lookup() returns a 2-tuple, always, and never both halves at once.
    for query, country, transport in (("TELENOR ASA", "NO", wide),
                                      ("Telenor", "NO", wide),
                                      ("Nothing Here AS", "NO", truly_empty),
                                      (None, "NO", wide),
                                      ("", "FI", fi_get),
                                      ("   ", "NO", wide)):
        result = lookup(query, country, get=transport)
        assert isinstance(result, tuple) and len(result) == 2, (query, result)
        record, note = result
        assert record is None or note is None, (query, record, note)
    # A None query used to raise AttributeError inside the matcher.
    record, note = lookup(None, "NO", get=wide)
    assert record is None and note.startswith("COMPANY_IDENTITY_UNMATCHED"), note
    ok += 1

    # An unsupported country refuses before any transport is touched.
    for bad in ("DK", "", None, "xx"):
        try:
            lookup("Anything", bad, get=wide)
        except SystemExit as exc:
            assert "DATA NOT AVAILABLE" in str(exc), exc
        else:
            raise AssertionError("country %r must not resolve" % (bad,))
    ok += 1

    print("nordic_registers selftest: %d assertion groups ok" % ok)
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _render(record):
    lines = ["%s (%s)" % (record.get("legal_name") or "unnamed entity",
                          record.get("registration_number") or "no number"),
             "  register:  %s" % record.get("register")]
    basis = record.get("status_basis")
    lines.append("  status:    %s%s" % (record.get("status") or "not established",
                                        " [%s]" % basis if basis else ""))
    bankrupt = record.get("bankrupt")
    lines.append("  bankrupt:  %s" % ("not checked" if bankrupt is None
                                      else ("yes" if bankrupt else "no")))
    if record.get("legal_form"):
        lines.append("  form:      %s (%s)" % (record["legal_form"],
                                               record.get("legal_form_code")))
    if record.get("industry_code"):
        lines.append("  industry:  %s %s"
                     % (record["industry_code"],
                        record.get("industry_description") or ""))
    if record.get("homepage"):
        lines.append("  homepage:  %s" % record["homepage"])
    if record.get("employees") is not None:
        lines.append("  employees: %s" % record["employees"])
    window = record.get("search_window") or {}
    if window.get("truncated"):
        lines.append("  window:    %s of %s matching entities seen"
                     % (window.get("returned"), window.get("total")))
    lines.append("  source:    %s" % (record.get("source_url") or "n/a"))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("query", nargs="?",
                        help="company name or registration number")
    parser.add_argument("--country", choices=["NO", "FI"],
                        help="NO (Norway) or FI (Finland)")
    parser.add_argument("--limit", type=int, default=None,
                        help="search window size (NO default 20, FI default 50)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--selftest", action="store_true",
                        help="run the offline tests; opens no socket")
    args = parser.parse_args()

    if args.selftest:
        return _selftest()
    if not args.query or not args.country:
        parser.print_help()
        return 1

    try:
        record, note = lookup(args.query, args.country, limit=args.limit)
    except SystemExit as exc:
        # An outage, reported as one - never as "no such company".
        if args.json:
            print(json.dumps({"query": args.query, "country": args.country,
                              "found": False, "record": None, "note": None,
                              "error": str(exc)}, indent=2, ensure_ascii=False))
        else:
            print("ERROR: %s" % exc)
        return 1

    payload = {"query": args.query, "country": args.country,
               "found": record is not None, "record": record, "note": note,
               "error": None}
    if args.json:
        # `raw` is the whole register payload and drowns the normalised
        # record; it stays available to importers and off the CLI.
        if record is not None:
            payload["record"] = {k: v for k, v in record.items() if k != "raw"}
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif note:
        print("REFUSING: %s" % note)
    elif record:
        print(_render(record))
    else:
        print("no %s register entity found for %r" % (args.country, args.query))
    return 0 if record is not None else 2


if __name__ == "__main__":
    sys.exit(main())
