#!/usr/bin/env python3
"""Shared financial math - stdlib only, no network, no I/O, pure functions.

peers_se.py's revenue CAGR (peers_se.py:1104-1135) carried two bugs before it
was fixed there, and earnings_quality.py's own cagr_fact() (independently,
never having seen peers_se.py's fix) carried BOTH of the same two bugs at the
time this module was written:

  1. INDEX EXPONENT, NOT ELAPSED TIME. Using an observation count - or a
     caller-chosen "N-year window" - as the compounding exponent silently
     assumes the two periods compared are exactly N calendar years apart.
     Investor's merged ESEF series holds 2021, 2022 and 2024 - 2023 is
     missing - so a 2-period-back "2-year" window actually spans three
     elapsed years. The count-based exponent printed "2y CAGR +24.6%" for a
     true +15.9% over the real 3-year span. cagr_between() below always
     derives the exponent from (end_date - start_date).days / 365.2425,
     never from a count of periods or a caller-supplied window.

  2. NO CURRENCY CHECK. Betsson redenominated its accounts from SEK to EUR
     at the start of 2021: revenue for FY2020 is reported in SEK, revenue
     for FY2024 in EUR. Compounding one against the other - simply because
     both numbers sat in the same "revenue" series - printed "4y CAGR
     -35.5%" for a company that was actually growing about +16%/year.
     cagr_between() refuses (returns a CagrResult with value=None) rather
     than compound across a currency change; cagr() - the whole-series
     version, ported directly from peers_se.py's algorithm - drops every
     period whose currency does not match the latest period's and reports
     what it dropped, so the caller can print the caveat instead of a
     silently wrong percentage.

Also provided: margin(), yoy() and multiple() - each returns a structured
result (a value, or a refusal reason) rather than a bare float or a bare
None, on the same "refuse rather than guess" principle: a caller that only
checked `if result:` before printing a percentage would otherwise have no
way to tell "zero" apart from "could not be computed", and no way to surface
why not.

Import it, do not run it - `python finmath.py --selftest` runs the
assertions this module's guarantees rest on.
"""
import datetime
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

DAYS_PER_YEAR = 365.2425
DEFAULT_MIN_YEARS = 0.75  # peers_se.py's own floor: below this, ordinary
                          # quarter-to-quarter noise dominates an annualised
                          # rate more than the underlying trend does.


def _as_date(value):
    """Accept a date, a datetime, or an ISO 'YYYY-MM-DD' (or longer,
    truncated) string. Anything else - or a string that does not parse -
    returns None: a period this function cannot date is a period it refuses
    to compound across, never a guessed one."""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


class CagrResult(object):
    """The outcome of a CAGR computation: a value plus what was dropped and
    why, so a caller can print the caveat instead of a bare, unqualified
    percentage.

    `value` is None exactly when the computation was refused; `reason` then
    explains why. `dropped` lists the periods excluded on a currency
    mismatch (see cagr() below) - always a list, empty when nothing was
    dropped. `observations` is the count of periods that shared the latest
    period's currency (cagr() only; None from cagr_between()).
    """

    def __init__(self, value=None, years=None, start_period=None, end_period=None,
                 start_value=None, end_value=None, observations=None,
                 dropped=None, reason=None):
        self.value = value
        self.years = years
        self.start_period = start_period
        self.end_period = end_period
        self.start_value = start_value
        self.end_value = end_value
        self.observations = observations
        self.dropped = dropped or []
        self.reason = reason

    def __bool__(self):
        return self.value is not None

    def __repr__(self):
        if self.value is None:
            return "CagrResult(refused: %s)" % (self.reason,)
        extra = ", %d dropped" % len(self.dropped) if self.dropped else ""
        return ("CagrResult(%.4f over %.2fy, %s -> %s%s)"
                % (self.value, self.years, self.start_period, self.end_period, extra))


def cagr_between(v0, v1, period0, period1, currency0=None, currency1=None,
                  min_years=DEFAULT_MIN_YEARS):
    """The compound annual growth rate between exactly two observations.

    Refuses (value=None, `reason` set) rather than guesses when:
      * either value is missing;
      * either value is not strictly positive (a CAGR through a loss year or
        a sign change is not a growth rate - it is a discontinuity dressed
        as one);
      * either period does not parse to a date (see _as_date);
      * period1 is not strictly after period0;
      * the elapsed span is below `min_years`;
      * currency0 and currency1 are both given and differ - see BUG 2 in the
        module docstring.
    """
    if v0 is None or v1 is None:
        return CagrResult(reason="missing value")
    if v0 <= 0 or v1 <= 0:
        return CagrResult(reason="non-positive base or end value (%r, %r)" % (v0, v1))
    d0, d1 = _as_date(period0), _as_date(period1)
    if d0 is None or d1 is None:
        return CagrResult(reason="a period did not parse to a date (%r, %r)"
                                  % (period0, period1))
    if d1 <= d0:
        return CagrResult(reason="end period %s is not after start period %s"
                                  % (period1, period0))
    if currency0 and currency1 and currency0 != currency1:
        return CagrResult(
            reason=("currency changed (%s -> %s); refusing to compound across "
                     "a redenomination rather than print a fabricated rate"
                     % (currency0, currency1)),
            dropped=[{"period": period0, "currency": currency0}])
    elapsed = (d1 - d0).days / DAYS_PER_YEAR
    if elapsed < min_years:
        return CagrResult(reason="elapsed span %.2fy is below the %.2fy minimum"
                                  % (elapsed, min_years))
    value = (v1 / v0) ** (1.0 / elapsed) - 1.0
    return CagrResult(value=value, years=elapsed, start_period=period0, end_period=period1,
                      start_value=v0, end_value=v1)


def cagr(series, currencies=None, min_years=DEFAULT_MIN_YEARS):
    """CAGR of a whole {period: value} series, base chosen the way
    peers_se.py:1104-1135 chooses it: the EARLIEST period whose currency
    matches the LATEST period's - never an arbitrary index N periods back -
    with every period whose currency differs reported in `.dropped` rather
    than silently included or silently omitted.

    `currencies`, if given, is a parallel {period: currency_label} mapping.
    A period is dropped only on an AFFIRMATIVE mismatch - both its own and
    the latest period's currency are known, and they differ. A period whose
    currency is absent from the mapping (or mapped to None) is "currency
    unknown": there is nothing to compare it against, so it is never dropped
    on that basis alone, and in particular an unknown-currency LATEST period
    means no mismatch can ever be detected - callers that need the currency
    check to be load-bearing must supply a currency label for every period,
    including the latest one.

    Periods are compared with the ISO-string/date ordering `sorted()` gives
    them; pass period keys as ISO 'YYYY-MM-DD' strings or as date/datetime
    objects, not a mix of the two (a mixed key type breaks Python's sort).
    """
    periods = sorted(series)
    if not periods:
        return CagrResult(reason="empty series")
    latest = periods[-1]
    currencies = currencies or {}
    latest_ccy = currencies.get(latest)
    dropped = []
    same_ccy_periods = []
    for p in periods:
        p_ccy = currencies.get(p)
        if latest_ccy is not None and p_ccy is not None and p_ccy != latest_ccy:
            dropped.append({"period": p, "currency": p_ccy})
        else:
            same_ccy_periods.append(p)
    base_candidates = [p for p in same_ccy_periods if p != latest]
    if not base_candidates:
        return CagrResult(reason="no earlier period with a matching currency",
                          dropped=dropped, observations=len(same_ccy_periods))
    base = base_candidates[0]
    result = cagr_between(series[base], series[latest], base, latest,
                          currency0=currencies.get(base), currency1=latest_ccy,
                          min_years=min_years)
    result.dropped = dropped
    result.observations = len(same_ccy_periods)
    return result


class RatioResult(object):
    """Common wrapper for margin()/yoy()/multiple(): a value, or a refusal
    reason - so a caller can distinguish "computed to zero" from "could not
    be computed" without a separate None check racing a ZeroDivisionError."""

    def __init__(self, value=None, reason=None):
        self.value = value
        self.reason = reason

    def __bool__(self):
        return self.value is not None

    def __repr__(self):
        if self.value is None:
            return "RatioResult(refused: %s)" % (self.reason,)
        return "RatioResult(%.6g)" % (self.value,)


def margin(numerator, denominator):
    """numerator / denominator, refusing on a missing or zero denominator.
    The numerator may be negative - a loss margin is real information - a
    zero denominator is not something to divide by."""
    if numerator is None or denominator is None:
        return RatioResult(reason="missing input")
    if denominator == 0:
        return RatioResult(reason="zero denominator")
    return RatioResult(value=numerator / denominator)


def yoy(current, previous):
    """(current / previous) - 1, refusing when `previous` is not strictly
    positive. A negative or zero prior-year base makes a percentage change
    sign-flipped or undefined rather than merely large, so it is refused
    rather than reported as some technically-computable number."""
    if current is None or previous is None:
        return RatioResult(reason="missing input")
    if previous <= 0:
        return RatioResult(reason="prior-year base is not positive (%r)" % (previous,))
    return RatioResult(value=current / previous - 1.0)


def multiple(numerator, denominator, numerator_currency=None, denominator_currency=None):
    """numerator / denominator, for a valuation multiple (P/E, EV/Sales, ...).

    Refuses (value=None) rather than guesses when:
      * either input is missing;
      * the denominator is not strictly positive - a P/E on a loss is not a
        multiple, it is a sign flip wearing one's clothes;
      * the denominator's currency is not given - an unlabelled denominator
        cannot be certified against the numerator, so an unknown currency is
        itself a refusal condition, not "assume it matches";
      * both currencies are given and differ.
    """
    if numerator is None or denominator is None:
        return RatioResult(reason="missing input")
    if denominator <= 0:
        return RatioResult(reason="denominator is not positive (%r)" % (denominator,))
    if not denominator_currency:
        return RatioResult(reason="denominator currency unknown")
    if numerator_currency and numerator_currency != denominator_currency:
        return RatioResult(reason="currency mismatch (%s over %s)"
                                  % (numerator_currency, denominator_currency))
    return RatioResult(value=numerator / denominator)


# --------------------------------------------------------------------------
# Selftest
# --------------------------------------------------------------------------

def _selftest():
    ok = 0

    # The Investor case: periods 2021, 2022, 2024 - a gap at 2023. A true
    # +15.9%/year growth rate compounded from 2021 to 2024 (elapsed 3 years).
    v2021 = 100.0
    true_rate = 0.159
    v2024 = v2021 * (1.0 + true_rate) ** 3
    series = {"2021-12-31": v2021, "2022-12-31": v2021 * 1.05, "2024-12-31": v2024}
    result = cagr_between(v2021, v2024, "2021-12-31", "2024-12-31")
    assert result.value is not None
    # elapsed is (days between the two dates)/365.2425, not an exact integer
    # 3.0 - a leap year in the span makes it slightly over 3 years - so the
    # recovered rate is close to, not bit-for-bit equal to, true_rate.
    assert abs(result.value - true_rate) < 1e-3, result
    assert abs(result.years - 3.0) < 0.01, result.years
    # The bug this replaces: treating the 2-period gap as if it were 2 years
    # would have solved (v2024/v2021)**(1/2) - 1, well above the true rate.
    wrong_two_year_exponent = (v2024 / v2021) ** (1.0 / 2.0) - 1.0
    assert wrong_two_year_exponent > result.value + 0.05
    ok += 1

    r = cagr(series)
    assert r.value is not None and abs(r.value - true_rate) < 1e-3, r
    assert r.start_period == "2021-12-31" and r.end_period == "2024-12-31"
    ok += 1

    # The Betsson case: FY2020 in SEK, FY2024 in EUR after a redenomination.
    # A real ~+16%/year grower must not compound to a negative rate just
    # because the unit changed underneath it.
    betsson_series = {"2020-12-31": 6000.0, "2021-12-31": 620.0, "2022-12-31": 700.0,
                      "2023-12-31": 780.0, "2024-12-31": 870.0}
    betsson_currency = {"2020-12-31": "SEK", "2021-12-31": "EUR", "2022-12-31": "EUR",
                        "2023-12-31": "EUR", "2024-12-31": "EUR"}
    r = cagr(betsson_series, currencies=betsson_currency)
    assert r.value is not None
    assert r.value > 0, r  # 620 -> 870 over 3 years is real growth, not the
                           # -35.5% a naive SEK/EUR compound would print
    assert any(d["period"] == "2020-12-31" for d in r.dropped), r.dropped
    assert r.start_period == "2021-12-31"
    ok += 1

    # A direct currency mismatch between exactly two points refuses outright.
    r = cagr_between(6000.0, 870.0, "2020-12-31", "2024-12-31",
                     currency0="SEK", currency1="EUR")
    assert r.value is None and "currency" in r.reason, r
    ok += 1

    # Minimum elapsed span.
    r = cagr_between(100.0, 101.0, "2024-01-01", "2024-06-01")
    assert r.value is None and "minimum" in r.reason, r
    ok += 1

    # Non-positive base/end refuses.
    r = cagr_between(-10.0, 20.0, "2020-01-01", "2024-01-01")
    assert r.value is None, r
    r = cagr_between(10.0, 0.0, "2020-01-01", "2024-01-01")
    assert r.value is None, r
    ok += 2

    # margin / yoy / multiple
    assert margin(50.0, 200.0).value == 0.25
    assert margin(-10.0, 100.0).value == -0.10
    assert margin(10.0, 0.0).value is None
    ok += 1

    assert abs(yoy(110.0, 100.0).value - 0.10) < 1e-12
    assert yoy(110.0, -5.0).value is None
    assert yoy(110.0, 0.0).value is None
    ok += 1

    assert multiple(1000.0, 100.0, "SEK", "SEK").value == 10.0
    assert multiple(1000.0, -5.0, "SEK", "SEK").value is None
    assert multiple(1000.0, 100.0, "SEK", None).value is None
    assert multiple(1000.0, 100.0, "EUR", "SEK").value is None
    ok += 1

    print("finmath selftest: %d assertions ok" % ok)
    return 0


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return _selftest()
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
