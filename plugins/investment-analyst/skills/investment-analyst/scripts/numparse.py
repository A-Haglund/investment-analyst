#!/usr/bin/env python3
"""THE one number parser for this toolkit - stdlib only, no network, no I/O.

venues_se.py:99-101 already says "the one number parser in this toolkit -
never a second parser", meaning mfn_news.to_number(). It was wrong: by the
time this module was written there were FOUR, and they did not all agree.

    mfn_news.py:299        to_number()          - the one venues_se.py names
    ttm_engine.py:269      parse_number()       - (value, truncated) instead
                                                   of a bare value or None
    insider_se.py:139      parse_fi_number()    - (value, ok); "" -> 0.0
    corporate_actions.py:523  _to_int()         - share counts, int only

Each is a genuine, independently-motivated fix for a genuine bug (see the
comments beside each), and each fix is a strict superset of an older, more
naive `.replace(",", ".")`. But three of the four fixes were never shared, so
a new script had a one-in-four chance of copying the naive version instead of
the fixed one. This module ends that: `parse_number()` below is the union of
all four - built on ttm_engine.py's algorithm, which was already the most
general of the four (the only one that both disambiguates comma-vs-decimal
AND validates space-grouping strictly enough to catch a footnote marker) -
and the other three (`to_number`, `parse_fi_number`, `to_int`) are now thin
wrappers around it that keep their own historical return shape.

Verified against every documented edge case behind that quartet of bugs:

  * SWEDISH SPACE-GROUPED THOUSANDS ("28 838") vs ENGLISH COMMA-GROUPED
    ("24,297") vs NORDIC DECIMAL COMMA ("-0,05", "16,6") vs the mixed English
    form ("2,063.1") vs the mixed CONTINENTAL form ("1.030,8"). Reading a
    comma as a decimal separator when it was really a thousands separator is
    a 1000x error that looks entirely plausible on the printed page -
    mfn_news.py's THOUSANDS_COMMA and this module's identical rule exist
    only to prevent it.

  * THE FOOTNOTE TRAP. H&M prints "MSEK 2 983 1" where the trailing "1" is a
    footnote marker for a restated comparative. Read naively that is 29,831
    - a tenfold error in the prior-year figure that a growth-rate sanity
    check would not catch, because the wrong number still looks like a
    plausible one. A space-separated group must be EXACTLY three digits or
    the number ends there; parse_number() reports this with `truncated=True`
    rather than silently absorbing the extra digit.

  * THE TYPOGRAPHIC MINUS. Typeset financial documents use U+2212 MINUS
    SIGN, an en/em dash, or (rarer) a fullwidth or modifier-letter minus
    where a keyboard writes a plain hyphen - and Nordic releases routinely
    put a SPACE after it ("- 11 471"). Left unnormalised, "− 11 471" parsed
    as +11,471 - a cash OUTFLOW emitted as an inflow, with a correct-looking
    source citation beside it, and mfn_news.py's own sign-change sanity
    checks explicitly decline to flag it because a real swing to profit
    looks identical. normalise_minus() below is the union of mfn_news.py's
    MINUS_CHARS and ttm_engine.py's MINUS_CHARS_RE - each set was missing
    characters the other one had.

  * PARENTHESISED NEGATIVES ("(1 234)" == -1234, the accounting convention)
    and non-breaking / narrow-no-break / thin space thousands grouping -
    none of the four originals handled every one of these; parse_number()
    handles all of them.

  * A TRAILING "%" is stripped before parsing rather than left to blow up
    float() - several extraction pipelines hand this function a percentage
    figure with the sign attached.

  * SCALE SUFFIXES (KSEK/TSEK/MSEK/MEUR/MNOK/.../bn/mdr/tkr/mkr/million/
    miljoner/...). mfn_news.py's SCALE and ttm_engine.py's CUR_SCALE /
    WORD_SCALE disagreed on which tokens carry a currency and which do not;
    CUR_SCALE and WORD_SCALE below are their union. parse_scaled() applies a
    suffix it recognises, and - this is the part none of the four originals
    did - REFUSES rather than guesses when a suffix-shaped token is present
    that it does NOT recognise: silently treating an unfamiliar token as
    "no scale" (multiplier 1x) can be a bigger error than refusing outright,
    because a plausible-looking unscaled number is exactly what a real
    thousandfold error looks like.

  * GENUINE AMBIGUITY IS REFUSED, NOT GUESSED. Anything that does not reduce
    to a well-formed float under the rules above - garbage input, an
    unrecognised scale token - comes back as None (or a result whose
    `.reason` explains the refusal), never a best-effort guess.

API:
    parse_number(raw)   -> (value_or_None, truncated)   ttm_engine.py's shape
    to_number(raw)      -> value_or_None                mfn_news.py's shape
    parse_fi_number(raw)-> (value, ok)                   insider_se.py's shape
    to_int(raw)         -> int_or_None                   corporate_actions.py's shape
    parse_scaled(raw)   -> ScaledNumber
    parse_scale_token(token) -> (multiplier, currency_or_None) or None
    normalise_minus(text) -> text with every minus variant folded to "-"
"""
import re
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


# --------------------------------------------------------------------------
# Sign and whitespace normalisation.
# --------------------------------------------------------------------------

# Union of mfn_news.py's MINUS_CHARS ("−–‐‑‒˗﹣－") and ttm_engine.py's
# MINUS_CHARS_RE ("[−–—‐‑‒]"): mfn_news.py was missing the em dash (—),
# ttm_engine.py was missing the modifier-letter minus (˗), small hyphen-minus
# (﹣) and fullwidth hyphen-minus (－).
MINUS_CHARS = "−–—‐‑‒˗﹣－"
MINUS_CHARS_RE = re.compile("[" + MINUS_CHARS + "]")

# Non-breaking, narrow no-break, thin and figure space: all fold to an
# ordinary ASCII space before the digit-grouping walk below runs. An ordinary
# ASCII space is deliberately NOT in this list - it is handled, one group at
# a time, by the grouping walk itself, which is what lets a malformed group
# (the footnote trap) be told apart from a real one.
_SPACE_TO_ASCII = (" ", " ", " ", " ")


def normalise_minus(text):
    """Fold every typographic minus/dash variant this toolkit has met to an
    ASCII "-". See the module docstring's TYPOGRAPHIC MINUS section."""
    if not text:
        return text
    return MINUS_CHARS_RE.sub("-", text)


# --------------------------------------------------------------------------
# The core parser. Ported from ttm_engine.py's parse_number(), which was
# already the most general of the four originals, plus the fixes above.
# --------------------------------------------------------------------------

def parse_number(raw):
    """Return (value, truncated). `truncated` means trailing junk (typically
    a footnote marker) was dropped rather than folded into the number.

    Refuses (returns (None, False)) on empty input, on input with no leading
    digit, or on a final float() conversion that still fails - never a
    best-effort guess.
    """
    if raw is None:
        return None, False
    s = raw
    for ch in _SPACE_TO_ASCII:
        s = s.replace(ch, " ")
    s = s.strip()
    if not s:
        return None, False

    # A trailing percent sign carries no numeric information once the caller
    # knows the field is a percentage; drop it rather than let it reach
    # float() and blow up a value that was otherwise perfectly parseable.
    if s.endswith("%"):
        s = s[:-1].rstrip()
        if not s:
            return None, False

    # Accounting convention: a value wrapped in parentheses is negative.
    paren_negative = False
    if s.startswith("(") and s.endswith(")"):
        paren_negative = True
        s = s[1:-1].strip()
        if not s:
            return None, False

    s = normalise_minus(s)
    sign_negative = s.startswith("-")
    s = s.lstrip("-").strip()
    if not s:
        return None, False

    m = re.match(r"\d+", s)
    if not m:
        return None, False
    integer = m.group(0)
    pos = m.end()
    truncated = False
    decimal = None

    # Space-grouped thousands, strictly three digits per group. H&M prints
    # "MSEK 2 983 1" where the trailing "1" is a footnote marker for a
    # restated comparative - read naively that is 29,831, a tenfold error a
    # growth-rate check would not catch. A group that is not exactly three
    # digits ends the number here instead.
    while pos < len(s) and s[pos] == " ":
        nxt = re.match(r"\d+", s[pos + 1:])
        if not nxt:
            break
        if len(nxt.group(0)) != 3:
            truncated = True          # footnote marker or an adjacent number
            break
        integer += nxt.group(0)
        pos += 1 + nxt.end()

    rest = s[pos:]
    seps = re.findall(r"[.,](?=\d)", rest)
    if seps:
        groups = re.findall(r"[.,](\d+)", rest)
        if len({c for c in seps}) > 1:
            # Both a comma and a dot are present: the LAST one is the
            # decimal point ("2,063.1" English; "1.030,8" continental).
            last = rest.rfind(seps[-1])
            integer += re.sub(r"[.,]", "", rest[:last])
            decimal = re.sub(r"\D", "", rest[last:])
        elif len(seps) > 1:
            integer += "".join(groups)          # repeated separator = thousands
        elif len(groups[0]) == 3 and len(integer) <= 3:
            # "37,799" / "24,297" - a comma (or dot) followed by EXACTLY
            # three digits, with no more than three digits already
            # collected, is a thousands separator. Reading it as a decimal
            # point is the 1000x error mfn_news.py's THOUSANDS_COMMA exists
            # to prevent.
            integer += groups[0]
        else:
            decimal = groups[0]                 # "16,6" / "3.35" - decimal

    try:
        value = float(integer + ("." + decimal if decimal else ""))
    except ValueError:
        return None, truncated
    negative = sign_negative or paren_negative
    return (-value if negative else value), truncated


def to_number(raw):
    """mfn_news.py's to_number() shape: a bare value, or None. The truncation
    flag is discarded here - a caller that needs it should call
    parse_number() directly."""
    value, _truncated = parse_number(raw)
    return value


def parse_fi_number(raw):
    """insider_se.py's parse_fi_number() shape: (value, ok).

    An empty or missing field is 0.0/True by FI Volume/Price convention -
    that is a deliberate "blank means zero" reading, not a parse failure,
    and is preserved here exactly as insider_se.py defined it. `ok` is False
    only when the field was non-empty and still could not be parsed, so a
    caller can count and report the failure rather than silently
    substituting 0.0 for genuinely bad data.
    """
    if raw is None:
        return 0.0, True
    if not raw.strip():
        return 0.0, True
    value, _truncated = parse_number(raw)
    if value is None:
        return 0.0, False
    return value, True


def to_int(raw):
    """corporate_actions.py's _to_int() shape: an int, or None.

    A share count is never fractional, so this does not go through
    parse_number() at all - it strips grouping characters (space, nbsp,
    narrow nbsp, thin space, comma, dot - corporate_actions.py's releases
    use both "," and "." purely as thousands separators, never as a decimal
    point, since a share count has no fractional part) and requires the
    result to be a bare integer literal. A genuine decimal point surviving
    that strip (an OCR artefact, a stray percentage) is a refusal, not a
    round-off.
    """
    if raw is None:
        return None
    cleaned = raw
    for ch in (" ", " ", " ", " ", ",", "."):
        cleaned = cleaned.replace(ch, "")
    cleaned = normalise_minus(cleaned)
    try:
        return int(cleaned)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Scale / currency suffixes. Union of mfn_news.py's SCALE and ttm_engine.py's
# CUR_SCALE + WORD_SCALE.
# --------------------------------------------------------------------------

# token -> (multiplier, ISO-ish currency code)
CUR_SCALE = {
    "MSEK": (1e6, "SEK"), "MKR": (1e6, "SEK"), "MDKR": (1e9, "SEK"),
    "KSEK": (1e3, "SEK"), "TSEK": (1e3, "SEK"), "TKR": (1e3, "SEK"),
    "MEUR": (1e6, "EUR"), "KEUR": (1e3, "EUR"), "TEUR": (1e3, "EUR"),
    "MNOK": (1e6, "NOK"), "TNOK": (1e3, "NOK"),
    "MDKK": (1e6, "DKK"), "TDKK": (1e3, "DKK"),
    "MUSD": (1e6, "USD"), "KUSD": (1e3, "USD"),
    "SEK": (1.0, "SEK"), "EUR": (1.0, "EUR"), "NOK": (1.0, "NOK"),
    "DKK": (1.0, "DKK"), "USD": (1.0, "USD"), "KR": (1.0, "SEK"),
}
# token -> multiplier, no currency implied (bare magnitude words)
WORD_SCALE = {
    "THOUSAND": 1e3, "THOUSANDS": 1e3, "TUSEN": 1e3,
    "MILLION": 1e6, "MILLIONS": 1e6, "MILJONER": 1e6, "MN": 1e6,
    # H&M's English releases write "SEK 49,607 m" - without the bare "m"
    # every H&M figure comes out a million times too small, and because
    # every term of a TTM is wrong by the same factor no ratio check
    # catches it (see ttm_engine.py's WORD_SCALE comment).
    "M": 1e6,
    "BILLION": 1e9, "BILLIONS": 1e9, "MILJARD": 1e9, "MILJARDER": 1e9,
    "MDR": 1e9, "BN": 1e9, "MD": 1e9,
}

_LETTERS = "A-Za-zÅÄÖåäö"
_PREFIX_RE = re.compile(r"^([%s]+)\s+(.*\d.*)$" % _LETTERS)
_SUFFIX_RE = re.compile(r"^(.*\d)\s*([%s]+)$" % _LETTERS)


def parse_scale_token(token):
    """One scale/currency suffix token -> (multiplier, currency_or_None), or
    None if the token is not one this toolkit recognises."""
    if not token:
        return None
    t = token.upper()
    if t in CUR_SCALE:
        return CUR_SCALE[t]
    if t in WORD_SCALE:
        return (WORD_SCALE[t], None)
    return None


class ScaledNumber(object):
    """The result of parse_scaled(): a value plus what scale was applied (or
    why none was), so a caller can report the caveat instead of silently
    treating an unrecognised suffix as unscaled.

    `value` is the number as printed on the page, before any scale is
    applied - it is set whenever the digits parsed, even when the suffix
    could not be resolved, so a caller can still see what was found.
    `value_scaled` (value * scale) and `scale` are None exactly when this
    was refused; `reason` then explains why.
    """

    def __init__(self, value=None, scale=None, currency=None,
                 value_scaled=None, reason=None):
        self.value = value
        self.scale = scale
        self.currency = currency
        self.value_scaled = value_scaled
        self.reason = reason

    def __bool__(self):
        return self.value_scaled is not None

    def __repr__(self):
        if self.value_scaled is None:
            return "ScaledNumber(refused: %s)" % (self.reason,)
        return "ScaledNumber(%.6g, scale=%.0f, currency=%s)" % (
            self.value_scaled, self.scale, self.currency)


def _split_suffix(raw):
    """Return (number_part, suffix_token_or_None) - a suffix is a leading or
    trailing alphabetic run separated from the digits by whitespace ("MSEK
    123.4", "123.4 MSEK", "123.4 million"). A bare number with no such run
    returns (raw, None)."""
    s = raw.strip()
    m = _PREFIX_RE.match(s)
    if m:
        return m.group(2).strip(), m.group(1)
    m = _SUFFIX_RE.match(s)
    if m:
        return m.group(1).strip(), m.group(2)
    return s, None


def parse_scaled(raw):
    """Parse a figure that may carry ONE leading or trailing scale/currency
    suffix ("123.4 MSEK", "MSEK 123.4", "123.4 million"). See ScaledNumber
    and the module docstring's SCALE SUFFIXES section: an unrecognised
    suffix-shaped token REFUSES rather than being silently treated as an
    unscaled (1x) value.

    KNOWN LIMITATION, deliberately out of scope: a currency prefix AND a
    magnitude suffix together on the SAME figure ("SEK 49,607 m", H&M's own
    English-language convention) is not decomposed by a single call here -
    only one adjacent token is stripped, so the "m" would be left attached
    to the digits and silently ignored by parse_number() rather than
    applied. mfn_news.py's and ttm_engine.py's own extraction regexes
    already split a currency prefix and a magnitude suffix into separate
    capture groups before either reaches a number parser; a caller with that
    three-part shape should do the same and call parse_scale_token() on the
    magnitude word directly, rather than handing this function the whole
    "SEK 49,607 m" string.
    """
    if raw is None:
        return ScaledNumber(reason="missing input")
    number_part, token = _split_suffix(raw)
    value, _truncated = parse_number(number_part)
    if value is None:
        return ScaledNumber(reason="could not parse a number from %r" % (raw,))
    if token is None:
        return ScaledNumber(value=value, scale=1.0, currency=None, value_scaled=value)
    found = parse_scale_token(token)
    if found is None:
        return ScaledNumber(
            value=value,
            reason=("unrecognised scale/currency token %r - refusing to "
                     "guess a multiplier rather than silently treating it "
                     "as unscaled" % token))
    mult, ccy = found
    return ScaledNumber(value=value, scale=mult, currency=ccy, value_scaled=value * mult)


# --------------------------------------------------------------------------
# Selftest
# --------------------------------------------------------------------------

def _selftest():
    ok = 0

    cases = [("1,030.8", 1030.8), ("37,799", 37799.0), ("28 838", 28838.0),
             ("16,6", 16.6), ("3.35", 3.35), ("-0,05", -0.05),
             ("- 11 471", -11471.0), ("2 066,5", 2066.5), ("104 435", 104435.0),
             ("1.030,8", 1030.8), ("24,297", 24297.0), ("1,234,567", 1234567.0),
             ("-24,297", -24297.0), ("3,5", 3.5)]
    for raw, want in cases:
        got, _ = parse_number(raw)
        assert got == want, (raw, got, want)
        ok += 1

    # the H&M footnote trap
    got, truncated = parse_number("2 983 1")
    assert got == 2983.0 and truncated, (got, truncated)
    ok += 1

    # typographic minus variants, with and without a following space
    for raw, want in (("−139", -139.0), ("– 11 471", -11471.0),
                      ("—139", -139.0)):
        got, _ = parse_number(raw)
        assert got == want, (raw, got, want)
        ok += 1

    # parenthesised negative
    got, _ = parse_number("(1 234)")
    assert got == -1234.0, got
    ok += 1

    # trailing percent
    got, _ = parse_number("-3.2%")
    assert got == -3.2, got
    ok += 1

    # non-breaking / narrow no-break / thin space thousands grouping
    for raw in ("24 297", "24 297", "24 297"):
        got, _ = parse_number(raw)
        assert got == 24297.0, (raw, got)
        ok += 1

    # None / empty / unparseable
    for raw in (None, "", "n/a", "garbage"):
        got, truncated_flag = parse_number(raw)
        assert got is None, (raw, got)
        ok += 1

    assert to_number("24,297") == 24297.0
    assert to_number(None) is None
    ok += 2

    # insider_se.py's parse_fi_number() contract
    for raw, want in ((None, (0.0, True)), ("", (0.0, True)), ("1,000", (1000.0, True)),
                      ("1 234,5", (1234.5, True)), ("16,6", (16.6, True)),
                      ("garbage", (0.0, False))):
        got = parse_fi_number(raw)
        assert got == want, (raw, got, want)
        ok += 1

    # corporate_actions.py's _to_int() contract
    for raw, want in (("181,284,725", 181284725), ("55 000 000", 55000000),
                      ("1 234", 1234), (None, None), ("not a number", None)):
        got = to_int(raw)
        assert got == want, (raw, got, want)
        ok += 1

    # scale suffixes
    r = parse_scaled("123.4 MSEK")
    assert r.value_scaled == 123400000.0 and r.currency == "SEK", r
    r = parse_scaled("MSEK 123.4")
    assert r.value_scaled == 123400000.0 and r.currency == "SEK", r
    ok += 2

    r = parse_scaled("123.4 million")
    assert r.value_scaled == 123400000.0 and r.currency is None, r
    ok += 1

    r = parse_scaled("42")
    assert r.value_scaled == 42.0 and r.scale == 1.0, r
    ok += 1

    # genuine ambiguity: an unrecognised scale-shaped suffix refuses rather
    # than being silently treated as unscaled.
    r = parse_scaled("123.4 GBX")
    assert r.value_scaled is None and r.value == 123.4 and r.reason, r
    ok += 1

    print("numparse selftest: %d assertions ok" % ok)
    return 0


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("value", nargs="?", help="a figure to parse")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return _selftest()
    if args.value is None:
        parser.print_help()
        return 1
    value, truncated = parse_number(args.value)
    print("value=%r truncated=%r" % (value, truncated))
    scaled = parse_scaled(args.value)
    print("scaled=%r" % (scaled,))
    return 0 if value is not None else 1


if __name__ == "__main__":
    sys.exit(main())
