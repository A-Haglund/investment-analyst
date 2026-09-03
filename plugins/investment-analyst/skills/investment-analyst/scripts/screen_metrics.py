#!/usr/bin/env python3
"""screen_metrics.py - pure functions for screening fundamentals, offline.

Extracts and computes margin trends, drawdowns, and returns from bar series and
ESEF-format financial data. These are the atomic calculations that a screen
(screen_digest.py) uses to rank candidates. Pure functions only - no network,
no imports of sibling scripts.

Prevents: silent NaN or wrong-magnitude return windows, derived gross profit
calculated inconsistently, margin trends mis-categorized with fuzzy boundaries.
"""

from datetime import datetime, timedelta


def drawdown_from_high(bars):
    """bars: list of {"date": "YYYY-MM-DD", "close": float, "volume": int}.
    Bars may arrive unsorted and may contain close=None (skip those).
    Returns {"high": float, "high_date": str, "last": float, "last_date": str,
             "drawdown_pct": float, "bars_used": int, "span_days": int}
    drawdown_pct is NEGATIVE or 0.0: -34.2 means the last close is 34.2% below
    the highest close in the series. Returns None if fewer than 2 usable closes.
    The high is the highest CLOSE in the series (this toolkit has no intraday
    highs; do not pretend otherwise)."""
    if not bars:
        return None

    # Filter out None closes
    usable = [b for b in bars if b.get("close") is not None]
    if len(usable) < 2:
        return None

    # Sort by date to ensure chronological order
    sorted_bars = sorted(usable, key=lambda b: b["date"])

    # Find the highest close
    high_bar = max(sorted_bars, key=lambda b: b["close"])
    last_bar = sorted_bars[-1]

    high = high_bar["close"]
    high_date = high_bar["date"]
    last = last_bar["close"]
    last_date = last_bar["date"]

    # Calculate drawdown percent: (last - high) / high * 100
    # This will be negative or zero
    drawdown_pct = (last - high) / high * 100.0

    # Span in days from first to last
    first_date = datetime.strptime(sorted_bars[0]["date"], "%Y-%m-%d")
    last_date_obj = datetime.strptime(last_date, "%Y-%m-%d")
    span_days = (last_date_obj - first_date).days

    return {
        "high": high,
        "high_date": high_date,
        "last": last,
        "last_date": last_date,
        "drawdown_pct": drawdown_pct,
        "bars_used": len(usable),
        "span_days": span_days,
    }


def return_over(bars, days):
    """Simple percent return from the close nearest to (last_date - days) up to
    the last close. Pick the bar AT OR BEFORE that target date (the nearest
    earlier one); if no bar is at or before it, return None rather than
    silently measuring a shorter window than asked for - a "12-month return"
    computed over 5 months is a wrong number, not a partial one.
    Returns a float percent, or None."""
    # close=None has to be filtered here for the same reason drawdown_from_high
    # filters it: a Nordic daily series carries null closes on a halted or
    # non-traded session, and both endpoints of this calculation are read
    # directly. Sorting the raw list instead let a null become start_close or
    # last_close and raised TypeError on the subtraction - a crash on live
    # data that no clean fixture reaches.
    usable = [b for b in (bars or []) if b.get("close") is not None]
    if len(usable) < 2:
        return None

    sorted_bars = sorted(usable, key=lambda b: b["date"])

    # Get the last bar's date and calculate target date
    last_bar = sorted_bars[-1]
    last_date = datetime.strptime(last_bar["date"], "%Y-%m-%d")
    target_date = last_date - timedelta(days=days)

    # Find the bar at or before target_date (nearest earlier one)
    start_bar = None
    for bar in sorted_bars:
        bar_date = datetime.strptime(bar["date"], "%Y-%m-%d")
        if bar_date <= target_date:
            start_bar = bar
        else:
            break

    if start_bar is None:
        return None

    start_close = start_bar["close"]
    last_close = last_bar["close"]

    # Calculate simple percent return
    percent_return = (last_close - start_close) / start_close * 100.0

    return percent_return


def margins_from_esef(esef_by_year):
    """esef_by_year: {"2024-12-31": {"revenue": 526816000000.0,
                                     "cost_of_sales": ..., "gross_profit": ...,
                                     "operating_income": ...}, ...}
    Any field may be missing or None (ESEF mandates primary-statement tagging
    only; plenty of issuers never tag cost_of_sales).
    Returns {"2024-12-31": {"gross_margin_pct": float|None,
                            "operating_margin_pct": float|None}, ...}
    gross_profit/revenue, and operating_income/revenue.
    When gross_profit is absent but revenue and cost_of_sales are present,
    derive gross_profit = revenue - cost_of_sales. Never derive it any other way.
    A year with revenue missing, None, or 0 yields both margins None - never a
    ZeroDivisionError and never a fabricated 0.0."""
    result = {}

    for year_key, data in esef_by_year.items():
        revenue = data.get("revenue")
        cost_of_sales = data.get("cost_of_sales")
        gross_profit = data.get("gross_profit")
        operating_income = data.get("operating_income")

        # If revenue is missing, None, or 0, both margins are None
        if revenue is None or revenue == 0:
            result[year_key] = {
                "gross_margin_pct": None,
                "operating_margin_pct": None,
            }
            continue

        # Derive gross_profit if absent but revenue and cost_of_sales present
        if gross_profit is None and cost_of_sales is not None:
            gross_profit = revenue - cost_of_sales

        # Calculate gross margin
        gross_margin_pct = None
        if gross_profit is not None:
            gross_margin_pct = (gross_profit / revenue) * 100.0

        # Calculate operating margin
        operating_margin_pct = None
        if operating_income is not None:
            operating_margin_pct = (operating_income / revenue) * 100.0

        result[year_key] = {
            "gross_margin_pct": gross_margin_pct,
            "operating_margin_pct": operating_margin_pct,
        }

    return result


def margin_trend(margins_by_year):
    """Direction of operating_margin_pct from the EARLIEST to the LATEST year
    that both have a value. Returns one of:
      "expanding"    latest is more than 1.0 percentage points above earliest
      "eroding"      latest is more than 1.0 pp below earliest
      "stable"       within +/- 1.0 pp
      "insufficient" fewer than 2 years carry an operating margin
    Compare in percentage POINTS, not relative percent."""
    # Collect years with operating_margin_pct values
    years_with_margin = []
    for year_key in sorted(margins_by_year.keys()):
        op_margin = margins_by_year[year_key].get("operating_margin_pct")
        if op_margin is not None:
            years_with_margin.append((year_key, op_margin))

    # Need at least 2 years with data
    if len(years_with_margin) < 2:
        return "insufficient"

    # Get earliest and latest
    earliest_margin = years_with_margin[0][1]
    latest_margin = years_with_margin[-1][1]

    # Calculate difference in percentage points
    diff = latest_margin - earliest_margin

    if diff > 1.0:
        return "expanding"
    elif diff < -1.0:
        return "eroding"
    else:
        return "stable"


def passes_margin_floor(latest_margins, gross_floor=40.0, op_floor=15.0, mode="either"):
    """Checks if latest_margins passes minimum profitability floor.

    latest_margins: one year's dict from margins_from_esef, or None.

    Modes:
      "either" (default) — passes if gross_margin_pct >= gross_floor OR
        operating_margin_pct >= op_floor. A None margin can never satisfy its
        side of the OR. This is the default because it allows lower-margin
        businesses to pass if they show operating strength, and vice versa.

      "both" — passes only if BOTH gross_margin_pct >= gross_floor AND
        operating_margin_pct >= op_floor. A None margin fails this check.

      "operating" — ignores gross entirely; passes only if operating_margin_pct
        is not None AND >= op_floor AND > 0. The > 0 check ensures actual
        profitability, not merely a thin positive margin. A None operating
        margin can never pass.

    Returns (bool, reason_string). The reason must name which test passed or
    that it failed, with the numbers in it - this string is printed in the
    screen output and has to be auditable. For non-default modes, the reason
    also names the mode so the rule applied is clear.

    Raises ValueError if mode is not one of the allowed values."""

    # Validate mode parameter
    valid_modes = {"either", "both", "operating"}
    if mode not in valid_modes:
        raise ValueError(
            "mode must be one of {}, got {}".format(
                ", ".join(sorted(valid_modes)), repr(mode)))

    if latest_margins is None:
        return (False, "no margins available")

    gross_margin = latest_margins.get("gross_margin_pct")
    op_margin = latest_margins.get("operating_margin_pct")

    if mode == "either":
        # Default behavior: passes if either margin passes
        gross_passes = gross_margin is not None and gross_margin >= gross_floor
        op_passes = op_margin is not None and op_margin >= op_floor

        if gross_passes:
            return (True,
                    "gross margin {:.2f}% passes floor {:.2f}%".format(
                        gross_margin, gross_floor))

        if op_passes:
            return (True,
                    "operating margin {:.2f}% passes floor {:.2f}%".format(
                        op_margin, op_floor))

        # Both failed
        reason = "gross {:.2f}% < {:.2f}% floor, operating {:.2f}% < {:.2f}% floor".format(
            gross_margin if gross_margin is not None else 0.0,
            gross_floor,
            op_margin if op_margin is not None else 0.0,
            op_floor,
        )
        return (False, reason)

    elif mode == "both":
        # Both margins must pass
        gross_passes = gross_margin is not None and gross_margin >= gross_floor
        op_passes = op_margin is not None and op_margin >= op_floor

        if gross_passes and op_passes:
            return (True,
                    "mode=both: gross {:.2f}% >= {:.2f}%, operating {:.2f}% >= {:.2f}%".format(
                        gross_margin, gross_floor, op_margin, op_floor))

        # At least one failed. Name ONLY the side that actually failed, and
        # say "not available" rather than printing a missing margin as 0.00%:
        # this string is the audit trail for the cut, and a reason that claims
        # a passing margin failed - or that an untagged one was zero - is a
        # false statement in the screen output.
        failures = []
        if gross_margin is None:
            failures.append("gross margin not available")
        elif gross_margin < gross_floor:
            failures.append("gross {:.2f}% < {:.2f}%".format(
                gross_margin, gross_floor))
        if op_margin is None:
            failures.append("operating margin not available")
        elif op_margin < op_floor:
            failures.append("operating {:.2f}% < {:.2f}%".format(
                op_margin, op_floor))
        return (False, "mode=both: " + ", ".join(failures))

    elif mode == "operating":
        # Only operating margin matters, and it must be > 0
        if op_margin is None:
            return (False, "mode=operating: operating margin not available")

        if op_margin <= 0:
            return (False,
                    "mode=operating: operating {:.2f}% is not positive".format(op_margin))

        if op_margin >= op_floor:
            return (True,
                    "mode=operating: operating {:.2f}% passes floor {:.2f}%".format(
                        op_margin, op_floor))
        else:
            return (False,
                    "mode=operating: operating {:.2f}% < {:.2f}% floor".format(
                        op_margin, op_floor))


def rank_candidates(cands):
    """Sort most-attractive-first. Each candidate is a dict carrying at least
    drawdown_pct, operating_margin_pct (may be None) and margin_trend.
    Order:
      1. margin_trend == "eroding" sorts BELOW every non-eroding name. A cheap
         business with an eroding margin is usually cheap for a reason; this
         demotes it rather than excluding it, because the screen reports, it
         does not decide.
      2. then deeper (more negative) drawdown_pct first.
      3. then higher operating_margin_pct first; None sorts last.
    Must be STABLE (equal candidates keep input order) and must NOT mutate the
    input list. Return a new list."""
    # Create a new list to avoid mutating input
    result = list(cands)

    # Sort with stable=True (insertion sort preserves order of equal elements)
    # We need to sort by multiple criteria in reverse order of precedence
    # (apply innermost sort first)

    # 3. Sort by operating_margin_pct (None last, higher first)
    result.sort(key=lambda c: (
        c.get("operating_margin_pct") is None,
        -c.get("operating_margin_pct") if c.get("operating_margin_pct") is not None else 0,
    ), reverse=False)

    # 2. Sort by drawdown_pct (deeper/more negative first; None sorts last).
    # The None guard matters even though every candidate reaching the ranking
    # has passed a drawdown filter: `None < float` is a TypeError in Python 3,
    # so one unscored name would take down the whole ranking rather than
    # ranking itself last. A screen degrades one row, never the run.
    result.sort(key=lambda c: (
        c.get("drawdown_pct") is None,
        c.get("drawdown_pct") if c.get("drawdown_pct") is not None else 0,
    ), reverse=False)

    # 1. Sort by margin_trend (eroding last, stable/expanding first)
    result.sort(key=lambda c: c.get("margin_trend") == "eroding", reverse=False)

    return result
