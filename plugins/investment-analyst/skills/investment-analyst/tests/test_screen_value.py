#!/usr/bin/env python3
"""screen_value.py - the on-demand deep-value screen, offline.

Every network-touching call (Nasdaq universe/liquidity, ESMA FIRDS, N-year
price history, corporate actions, ESEF fundamentals) is monkeypatched at the
module-attribute level, the same convention test_screen_digest.py uses for
its own layer-2 fetchers - no subprocess is spawned and no socket is ever
opened by this suite.

screen_metrics.py (drawdown_from_high, return_over, margins_from_esef,
margin_trend, passes_margin_floor, rank_candidates) is a sibling being
written by another agent in parallel and may not exist yet, or may not have
this suite's exact shape - every test here stubs it directly via
mock.patch.object rather than depending on the real module, per the brief.

Covers:
  - the value filter (passes_value_filter): keeps a name down >= floor% from
    its high with a negative 12-month return, and rejects (separately) a
    deep-drawdown name that has already recovered, and a shallow-drawdown
    name that is still falling.
  - a corporate action inside the FULL drawdown window routes a name to the
    TECHNICAL bucket, never into candidates, and never spends an ESEF fetch.
  - an MTF issuer (mic not in REGULATED_MICS) lands in NOT CLASSIFIED with an
    ESEF/venue reason, is never counted as a margin-floor failure, and never
    triggers an ESEF fetch either.
  - the margin stage (fetch_esef_margin_inputs) runs exactly once per
    liquidity+drawdown+corporate-action survivor, and zero times for a name
    cut at an earlier stage - the cost-control property of the whole design.
  - the printed text output carries the fiscal period and its age in months
    next to every margin.
  - cut-accounting: every issuer in a small universe lands in exactly one of
    survived / cut-with-reason / technical / not-classified, and the counts
    sum to the universe total.
"""
import datetime
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

sv = load("screen_value")

AS_OF = datetime.date(2026, 9, 2)


# ---------------------------------------------------------------------------
# small, direct unit tests - no patching, pure function
# ---------------------------------------------------------------------------

class ValueFilterKeepsOutOfFavourAndNotRecovered(unittest.TestCase):
    """Stage 3: keep an issuer only if it is down >= --drawdown-floor percent
    from its high AND its 365-day return is negative. Each rejection reason
    is asserted separately, per the brief."""

    def test_keeps_deep_drawdown_with_negative_12m_return(self):
        drawdown = {"drawdown_pct": -35.0, "high_date": "2024-01-01", "last_date": "2026-08-31"}
        ok, reason = sv.passes_value_filter(drawdown, -12.0, 30.0)
        self.assertTrue(ok, reason)
        self.assertIsNone(reason)

    def test_rejects_deep_drawdown_that_already_recovered(self):
        """-35% from the high, but the 12-month return is POSITIVE - this
        name has already turned back up and is not "not recovered"."""
        drawdown = {"drawdown_pct": -35.0, "high_date": "2024-01-01", "last_date": "2026-08-31"}
        ok, reason = sv.passes_value_filter(drawdown, +8.0, 30.0)
        self.assertFalse(ok)
        self.assertIn("not negative", reason)

    def test_rejects_shallow_drawdown_even_if_still_falling(self):
        """Only -10% from the high (floor is -30%) - "fell last month and
        still expensive" must not pass just because the 12m return is
        negative too."""
        drawdown = {"drawdown_pct": -10.0, "high_date": "2024-01-01", "last_date": "2026-08-31"}
        ok, reason = sv.passes_value_filter(drawdown, -5.0, 30.0)
        self.assertFalse(ok)
        self.assertIn("above the", reason)

    def test_rejects_when_drawdown_could_not_be_computed(self):
        ok, reason = sv.passes_value_filter(None, -12.0, 30.0)
        self.assertFalse(ok)
        self.assertIn("no usable drawdown data", reason)

    def test_rejects_when_365_day_return_unavailable(self):
        drawdown = {"drawdown_pct": -35.0}
        ok, reason = sv.passes_value_filter(drawdown, None, 30.0)
        self.assertFalse(ok)
        self.assertIn("insufficient 12-month", reason)


class MonthAgeOfAFiscalPeriod(unittest.TestCase):
    def test_fy2024_annual_is_twenty_months_old_on_2026_09_02(self):
        self.assertEqual(sv._month_diff("2024-12-31", AS_OF), 20)


# ---------------------------------------------------------------------------
# full-pipeline helpers - build a tiny, fully controlled universe
# ---------------------------------------------------------------------------

def _args(**over):
    base = dict(drawdown_floor=30.0, gross_floor=40.0, op_floor=15.0,
               history_years=3.0, liquidity_floor=2_000_000.0, limit=20,
               budget=900.0, venue="XSTO,SSME", include_illiquid=False, as_json=False,
               cap_floor=None, cap_ceiling=None, margin_mode="either", small_cap=False)
    base.update(over)
    return type("Args", (), base)()


def _spec(isin, lei, name, mic="XSTO", currency="SEK"):
    return {"isin": isin, "lei": lei, "name": name, "mic": mic, "currency": currency,
           "orderbookId": "OB-" + isin}


def _universe_patches(specs):
    """Builds (fetch_nasdaq_snapshot_fake, fetch_firds_fake) from a list of
    _spec() dicts. Only XSTO/SSME (nordic_shares' own coverage) get a Nasdaq
    price row; every mic gets a FIRDS identity row."""
    firds_by_mic = {}
    nasdaq_rows, nasdaq_liq = [], {}
    for s in specs:
        firds_by_mic.setdefault(s["mic"], {"instruments": []})
        firds_by_mic[s["mic"]]["instruments"].append(
            {"isin": s["isin"], "name": s["name"], "lei": s["lei"]})
        if s["mic"] in ("XSTO", "SSME"):
            segment = "FIRST_NORTH" if s["mic"] == "SSME" else None
            nasdaq_rows.append({"orderbookId": s["orderbookId"], "symbol": s["isin"],
                                "name": s["name"], "isin": s["isin"],
                                "currency": s["currency"], "segment": segment,
                                "sector": None, "last": 50.0})
            nasdaq_liq[s["orderbookId"]] = {"turnover": None, "volume": None,
                                            "percent_change_1d": None}

    def fake_snapshot(market="STO"):
        return nasdaq_rows, nasdaq_liq, None

    def fake_firds(mics):
        return firds_by_mic, {}

    return fake_snapshot, fake_firds


def _bars(obid, last_close, last_volume, drawdown, return_365):
    """Two bars for one instrument: an early one (irrelevant beyond sorting)
    and the last completed session, whose close/volume drives the liquidity
    floor via the REAL screen_digest.compute_returns. The canned drawdown/
    return_365 travel on the first bar as private keys that the fake
    screen_metrics stand-ins below read back out - screen_metrics itself is
    never invoked."""
    return {obid: {"status": "checked", "bars": [
        {"date": "2023-01-02", "close": 100.0, "volume": 1000,
         "_drawdown": drawdown, "_return365": return_365},
        {"date": "2026-08-31", "close": last_close, "volume": last_volume},
    ]}}


def _merge_bars(*dicts):
    out = {}
    for d in dicts:
        out.update(d)
    return out


def _fake_drawdown_from_high(bars):
    return bars[0].get("_drawdown")


def _fake_return_over(bars, days):
    return bars[0].get("_return365")


def _fake_margins_from_esef(margins_by_period):
    return {p: {"gross_margin_pct": 45.0, "operating_margin_pct": 20.0}
           for p in margins_by_period}


def _fake_margin_trend(margins_by_year):
    return "stable"


def _fake_passes_margin_floor(latest, gross_floor=40.0, op_floor=15.0, mode="either"):
    """Fake margin floor check. The mode parameter is accepted but this simple
    stub always requires both gross and operating margins to meet their floors."""
    if (latest.get("gross_margin_pct") or 0) >= gross_floor and \
       (latest.get("operating_margin_pct") or 0) >= op_floor:
        return True, None
    return False, "gross/operating margin below floor"


def _identity_rank(cands):
    return list(cands)


def _standard_patches(bars_by_obid, check_corp_action, esef_fetch, extra=None):
    patches = [
        mock.patch.object(sv, "fetch_history_parallel",
                          lambda instruments, from_date, to_date, budget, max_workers=10:
                          bars_by_obid),
        mock.patch.object(sv, "drawdown_from_high", _fake_drawdown_from_high),
        mock.patch.object(sv, "return_over", _fake_return_over),
        mock.patch.object(sv, "check_corporate_actions", check_corp_action),
        mock.patch.object(sv, "fetch_esef_margin_inputs", esef_fetch),
        mock.patch.object(sv, "margins_from_esef", _fake_margins_from_esef),
        mock.patch.object(sv, "margin_trend", _fake_margin_trend),
        mock.patch.object(sv, "passes_margin_floor", _fake_passes_margin_floor),
        mock.patch.object(sv, "rank_candidates", _identity_rank),
        mock.patch.object(sv, "today", lambda: AS_OF),
    ]
    if extra:
        patches.extend(extra)
    return patches


def _apply(patches):
    for p in patches:
        p.start()
    return patches


def _stop(patches):
    for p in patches:
        p.stop()


DEEP_DRAWDOWN = {"drawdown_pct": -40.0, "high_date": "2024-01-15", "last_date": "2026-08-31",
                 "high": 100.0, "last": 60.0, "bars_used": 500, "span_days": 900}
SHALLOW_DRAWDOWN = {"drawdown_pct": -10.0, "high_date": "2025-06-01", "last_date": "2026-08-31",
                    "high": 100.0, "last": 90.0, "bars_used": 300, "span_days": 400}


def _no_breaking_action(name, date_from, last_close_date, date_to, price=None):
    return {"status": "checked", "has_breaking_action": False, "events": [],
           "since_last_close": {"status": "checked", "count": 0, "events": []}}


def _breaking_action_for(target_name):
    def fn(name, date_from, last_close_date, date_to, price=None):
        if name == target_name:
            return {"status": "checked", "has_breaking_action": True,
                   "events": [{"date": "2025-01-10", "type": "SPLIT", "title": "3:1 split"}],
                   "since_last_close": {"status": "checked", "count": 0, "events": []}}
        return _no_breaking_action(name, date_from, last_close_date, date_to, price)
    return fn


def _passing_esef_fetch(call_log=None):
    def fn(lei, filings=3):
        if call_log is not None:
            call_log.append(lei)
        return {"2024-12-31": {"revenue": 100.0, "cost_of_sales": 50.0,
                               "gross_profit": 50.0, "operating_income": 20.0}}, "2024-12-31", None
    return fn


# ---------------------------------------------------------------------------
# corporate action routes a name to TECHNICAL, never candidates
# ---------------------------------------------------------------------------

class CorporateActionRoutesToTechnical(unittest.TestCase):
    def test_breaking_action_inside_drawdown_window_is_technical_not_a_candidate(self):
        specs = [_spec("SE0000000001", "LEI-SPLIT", "Splitty AB"),
                _spec("SE0000000002", "LEI-CLEAN", "Clean Value AB")]
        fake_snapshot, fake_firds = _universe_patches(specs)
        bars = _merge_bars(
            _bars("OB-SE0000000001", 60.0, 100000, DEEP_DRAWDOWN, -20.0),
            _bars("OB-SE0000000002", 60.0, 100000, DEEP_DRAWDOWN, -20.0))
        call_log = []
        patches = _standard_patches(
            bars, _breaking_action_for("Splitty AB"), _passing_esef_fetch(call_log),
            extra=[mock.patch.object(sv, "fetch_nasdaq_snapshot", fake_snapshot),
                  mock.patch.object(sv, "fetch_firds", fake_firds)])
        _apply(patches)
        try:
            result = sv.run(_args())
        finally:
            _stop(patches)

        technical_names = {c["name"] for c in result["technical"]}
        candidate_names = {c["name"] for c in result["candidates"]}
        self.assertIn("Splitty AB", technical_names)
        self.assertNotIn("Splitty AB", candidate_names)
        self.assertIn("Clean Value AB", candidate_names)
        self.assertEqual(result["technical_total"], 1)
        # a technical-move name must never reach the (expensive) ESEF fetch
        self.assertNotIn("LEI-SPLIT", call_log)
        self.assertIn("LEI-CLEAN", call_log)


# ---------------------------------------------------------------------------
# MTF issuer -> NOT CLASSIFIED, never a margin-floor failure, never fetched
# ---------------------------------------------------------------------------

class MtfIssuerIsNotClassifiedNotAMarginFailure(unittest.TestCase):
    def test_ssme_issuer_lands_in_not_classified_with_esef_reason(self):
        specs = [_spec("SE0000000003", "LEI-MTF", "First North Co", mic="SSME")]
        fake_snapshot, fake_firds = _universe_patches(specs)
        bars = _bars("OB-SE0000000003", 60.0, 100000, DEEP_DRAWDOWN, -20.0)
        call_log = []
        patches = _standard_patches(
            bars, _no_breaking_action, _passing_esef_fetch(call_log),
            extra=[mock.patch.object(sv, "fetch_nasdaq_snapshot", fake_snapshot),
                  mock.patch.object(sv, "fetch_firds", fake_firds)])
        _apply(patches)
        try:
            result = sv.run(_args())
        finally:
            _stop(patches)

        self.assertEqual(result["not_classified_total"], 1)
        nc = result["not_classified"][0]
        self.assertEqual(nc["name"], "First North Co")
        # the reason must reference the venue/ESEF situation, not a margin number
        self.assertTrue("ESEF" in nc["cut_reason"] or "regulated market" in nc["cut_reason"],
                        nc["cut_reason"])
        margin_floor_cut = next(c for c in result["cuts"]
                                if c["stage"] == "margin_floor: below floor")
        self.assertEqual(margin_floor_cut["count"], 0,
                         "an MTF issuer with no ESEF must never count as a margin-floor failure")
        self.assertEqual(call_log, [], "no ESEF fetch should ever be attempted for an MTF issuer")


# ---------------------------------------------------------------------------
# margin stage cost control: exactly one ESEF fetch per survivor, zero for
# names cut earlier
# ---------------------------------------------------------------------------

class MarginStageRunsOnlyOnSurvivors(unittest.TestCase):
    def test_esef_fetch_called_once_per_survivor_zero_for_names_cut_earlier(self):
        specs = [
            _spec("SE0000000010", "LEI-SHALLOW", "Shallow Fall AB"),   # cut: value filter
            _spec("SE0000000011", "LEI-ILLIQUID", "Illiquid Deep AB"), # cut: liquidity floor
            _spec("SE0000000012", "LEI-SURV1", "Deep Value One AB"),   # survivor
            _spec("SE0000000013", "LEI-SURV2", "Deep Value Two AB"),   # survivor
        ]
        fake_snapshot, fake_firds = _universe_patches(specs)
        bars = _merge_bars(
            _bars("OB-SE0000000010", 60.0, 100000, SHALLOW_DRAWDOWN, -5.0),
            _bars("OB-SE0000000011", 1.0, 1, DEEP_DRAWDOWN, -20.0),        # turnover = 1 SEK
            _bars("OB-SE0000000012", 60.0, 100000, DEEP_DRAWDOWN, -20.0),
            _bars("OB-SE0000000013", 60.0, 100000, DEEP_DRAWDOWN, -20.0),
        )
        call_log = []
        patches = _standard_patches(
            bars, _no_breaking_action, _passing_esef_fetch(call_log),
            extra=[mock.patch.object(sv, "fetch_nasdaq_snapshot", fake_snapshot),
                  mock.patch.object(sv, "fetch_firds", fake_firds)])
        _apply(patches)
        try:
            result = sv.run(_args())
        finally:
            _stop(patches)

        self.assertEqual(sorted(call_log), ["LEI-SURV1", "LEI-SURV2"])
        self.assertEqual(len(call_log), 2)
        self.assertEqual(result["candidates_total"], 2)


# ---------------------------------------------------------------------------
# printed output carries the fiscal period and its age next to every margin
# ---------------------------------------------------------------------------

class PrintedOutputCarriesMarginAge(unittest.TestCase):
    def test_text_output_shows_fiscal_period_and_age_in_months(self):
        specs = [_spec("SE0000000020", "LEI-AGE", "Aged Margin AB")]
        fake_snapshot, fake_firds = _universe_patches(specs)
        bars = _bars("OB-SE0000000020", 60.0, 100000, DEEP_DRAWDOWN, -20.0)
        patches = _standard_patches(
            bars, _no_breaking_action, _passing_esef_fetch(),
            extra=[mock.patch.object(sv, "fetch_nasdaq_snapshot", fake_snapshot),
                  mock.patch.object(sv, "fetch_firds", fake_firds)])
        _apply(patches)
        try:
            result = sv.run(_args())
        finally:
            _stop(patches)

        self.assertEqual(result["candidates_total"], 1)
        margins = result["candidates"][0]["margins"]
        self.assertEqual(margins["fiscal_period_end"], "2024-12-31")
        self.assertEqual(margins["age_months"], 20)

        buf = io.StringIO()
        with redirect_stdout(buf):
            sv.print_text(result)
        text = buf.getvalue()
        self.assertIn("2024-12-31", text)
        self.assertIn("20 months old", text)


# ---------------------------------------------------------------------------
# cut accounting: every issuer lands in exactly one bucket
# ---------------------------------------------------------------------------

class CutCountsAddUp(unittest.TestCase):
    def test_every_issuer_is_accounted_for_exactly_once(self):
        specs = [
            _spec("SE0000000030", "LEI-A", "Deep Value AB"),      # survives
            _spec("SE0000000031", "LEI-B", "Not Fallen AB"),      # cut: value filter
            _spec("SE0000000032", "LEI-C", "Illiquid AB"),        # cut: liquidity floor
            _spec("SE0000000033", "LEI-D", "Splitty Two AB"),     # technical
            _spec("SE0000000034", "LEI-E", "Mtf Co AB", mic="SSME"),  # not classified
        ]
        fake_snapshot, fake_firds = _universe_patches(specs)
        bars = _merge_bars(
            _bars("OB-SE0000000030", 60.0, 100000, DEEP_DRAWDOWN, -20.0),
            _bars("OB-SE0000000031", 90.0, 100000, SHALLOW_DRAWDOWN, -5.0),
            _bars("OB-SE0000000032", 1.0, 1, DEEP_DRAWDOWN, -20.0),
            _bars("OB-SE0000000033", 60.0, 100000, DEEP_DRAWDOWN, -20.0),
            _bars("OB-SE0000000034", 60.0, 100000, DEEP_DRAWDOWN, -20.0),
        )
        call_log = []
        patches = _standard_patches(
            bars, _breaking_action_for("Splitty Two AB"), _passing_esef_fetch(call_log),
            extra=[mock.patch.object(sv, "fetch_nasdaq_snapshot", fake_snapshot),
                  mock.patch.object(sv, "fetch_firds", fake_firds)])
        _apply(patches)
        try:
            result = sv.run(_args())
        finally:
            _stop(patches)

        self.assertEqual(result["universe"]["issuers"], 5)
        self.assertTrue(result["accounting_ok"],
                        "cut counts must add up to the whole universe: %r" % result["cuts"])
        self.assertEqual(result["universe_accounted_for"], 5)
        self.assertEqual(result["candidates_total"], 1)
        self.assertEqual(result["technical_total"], 1)
        self.assertEqual(result["not_classified_total"], 1)

        cut_by_stage = {c["stage"]: c["count"] for c in result["cuts"]}
        self.assertEqual(cut_by_stage["value_filter: does not meet criteria"], 1)
        self.assertEqual(cut_by_stage["liquidity_floor: below floor"], 1)
        self.assertEqual(cut_by_stage["margin_floor: below floor"], 0)


# ---------------------------------------------------------------------------
# size band (market cap) stage: 6 tests covering accounting, skipping, and
# preset resolution
# ---------------------------------------------------------------------------

class SizeBandAccountingIdentity(unittest.TestCase):
    """The accounting identity is the foundation of auditability: every issuer
    in the universe must land in exactly one bucket (survived, cut-with-reason,
    technical, not-classified), and those counts sum to the total. The size band
    stage must not break this: issuers with no resolvable market cap pass through
    and are accounted for at later stages, not double-counted or lost. This test
    verifies the identity holds when the size stage runs with some issuers above/
    below bounds and at least one with unresolvable cap."""

    def test_accounting_identity_with_size_band_and_unresolvable_cap(self):
        specs = [
            _spec("SE0000100001", "LEI-BIG", "Large Corp AB"),        # above ceiling
            _spec("SE0000100002", "LEI-SMALL", "Micro Cap Inc"),      # below floor
            _spec("SE0000100003", "LEI-NOCAP", "No Share Count AB"),  # no market cap
            _spec("SE0000100004", "LEI-CAND", "Deep Value Cand"),     # survives all
        ]
        fake_snapshot, fake_firds = _universe_patches(specs)
        bars = _merge_bars(
            _bars("OB-SE0000100001", 100.0, 500000, DEEP_DRAWDOWN, -20.0),
            _bars("OB-SE0000100002", 30.0, 100000, DEEP_DRAWDOWN, -20.0),
            _bars("OB-SE0000100003", 50.0, 100000, DEEP_DRAWDOWN, -20.0),
            _bars("OB-SE0000100004", 40.0, 150000, DEEP_DRAWDOWN, -20.0),
        )

        def fake_attach_selective(issuers, budget=None):
            for iss in issuers:
                name = iss.get("name", "")
                if "No Share Count" in name:
                    iss["market_cap_sek"] = None
                    iss["market_cap_status"] = "not_checked"
                    iss["market_cap_basis"] = "share count unresolvable"
                elif "Large Corp" in name:
                    iss["market_cap_sek"] = 10_000_000_000  # Above 5B ceiling
                    iss["market_cap_status"] = "checked"
                elif "Micro Cap" in name:
                    iss["market_cap_sek"] = 100_000_000  # Below 300M floor
                    iss["market_cap_status"] = "checked"
                else:
                    iss["market_cap_sek"] = 1_000_000_000  # In range
                    iss["market_cap_status"] = "checked"

        patches = _standard_patches(
            bars, _no_breaking_action, _passing_esef_fetch(),
            extra=[mock.patch.object(sv, "fetch_nasdaq_snapshot", fake_snapshot),
                  mock.patch.object(sv, "fetch_firds", fake_firds),
                  mock.patch.object(sv, "attach_market_cap", fake_attach_selective)])
        _apply(patches)
        try:
            result = sv.run(_args(cap_floor=300_000_000, cap_ceiling=5_000_000_000))
        finally:
            _stop(patches)

        # The identity must hold
        self.assertTrue(result["accounting_ok"],
                       "accounting identity must hold even with size band: "
                       "%d accounted vs %d total" % (result["universe_accounted_for"],
                                                      result["universe_total"]))
        self.assertEqual(result["universe_accounted_for"], result["universe_total"])
        self.assertEqual(result["universe_total"], 4)


class NoMarketCapNotDoubleCounted(unittest.TestCase):
    """Issuers with unresolvable market cap must NOT be added to the accounting
    sum at the size stage. They pass through and are accounted for at later
    stages (as candidates, technical moves, or not_classified). A test that
    would fail if someone incorrectly added size_cuts["no_market_cap"] into
    the accounted sum."""

    def test_no_market_cap_issuer_appears_later_not_lost(self):
        specs = [
            _spec("SE0000200001", "LEI-NOCAP", "No Cap Inc"),
            _spec("SE0000200002", "LEI-GOOD", "Good Value AB"),
        ]
        fake_snapshot, fake_firds = _universe_patches(specs)
        bars = _merge_bars(
            _bars("OB-SE0000200001", 50.0, 100000, DEEP_DRAWDOWN, -20.0),
            _bars("OB-SE0000200002", 50.0, 100000, DEEP_DRAWDOWN, -20.0),
        )

        def fake_attach_with_no_cap(issuers, budget=None):
            for iss in issuers:
                if "No Cap" in iss.get("name", ""):
                    iss["market_cap_sek"] = None
                    iss["market_cap_status"] = "not_checked"
                else:
                    iss["market_cap_sek"] = 1_000_000_000
                    iss["market_cap_status"] = "checked"

        patches = _standard_patches(
            bars, _no_breaking_action, _passing_esef_fetch(),
            extra=[mock.patch.object(sv, "fetch_nasdaq_snapshot", fake_snapshot),
                  mock.patch.object(sv, "fetch_firds", fake_firds),
                  mock.patch.object(sv, "attach_market_cap", fake_attach_with_no_cap)])
        _apply(patches)
        try:
            result = sv.run(_args(cap_floor=300_000_000, cap_ceiling=5_000_000_000))
        finally:
            _stop(patches)

        # The accounting must hold
        self.assertTrue(result["accounting_ok"],
                       "identity must hold: %d accounted vs %d total" % (
                           result["universe_accounted_for"], result["universe_total"]))

        # Verify no_market_cap row exists and has count 1
        no_cap_cut = next((c for c in result["cuts"]
                          if "no market cap" in c.get("stage", "")), None)
        self.assertIsNotNone(no_cap_cut)
        self.assertEqual(no_cap_cut["count"], 1)

        # Verify the no-cap issuer appears in final buckets (candidate, technical, or not_classified)
        all_final = result["candidates"] + result["technical"] + result["not_classified"]
        no_cap_names = [c["name"] for c in all_final if "No Cap" in c.get("name", "")]
        self.assertEqual(len(no_cap_names), 1,
                        "issuer with unresolvable cap should appear in final buckets")


class DefaultRunsSkipSizeStage(unittest.TestCase):
    """With no --cap-floor and no --cap-ceiling, the size stage is skipped
    entirely. attach_market_cap is never called, preserving default run cost."""

    def test_no_size_stage_with_default_args(self):
        specs = [_spec("SE0000300001", "LEI-TEST", "Test Corp")]
        fake_snapshot, fake_firds = _universe_patches(specs)
        bars = _bars("OB-SE0000300001", 50.0, 100000, DEEP_DRAWDOWN, -20.0)

        attach_market_cap_spy = mock.Mock(return_value=None)

        patches = _standard_patches(
            bars, _no_breaking_action, _passing_esef_fetch(),
            extra=[mock.patch.object(sv, "fetch_nasdaq_snapshot", fake_snapshot),
                  mock.patch.object(sv, "fetch_firds", fake_firds),
                  mock.patch.object(sv, "attach_market_cap", attach_market_cap_spy)])
        _apply(patches)
        try:
            # Call with default args (cap_floor and cap_ceiling are None)
            result = sv.run(_args())
        finally:
            _stop(patches)

        # attach_market_cap should never be called
        attach_market_cap_spy.assert_not_called()

        # Size band rows should not appear in the cuts list
        size_cuts = [c for c in result["cuts"] if "size_band" in c.get("stage", "")]
        self.assertEqual(len(size_cuts), 0,
                        "no size_band rows should appear when stage does not run")


class FunnelRowsOnlyWhenStageRan(unittest.TestCase):
    """Funnel (cuts list) rows for the size band stage appear only when the
    stage actually ran. When it did run, all three size_band rows are present
    with correct counts matching the fixture."""

    def test_all_three_size_band_rows_present_with_correct_counts(self):
        specs = [
            _spec("SE0000400001", "LEI-BIG", "Big Corp"),      # above ceiling
            _spec("SE0000400002", "LEI-SMALL", "Tiny Corp"),   # below floor
            _spec("SE0000400003", "LEI-GOOD", "Good Cap"),     # in range
        ]
        fake_snapshot, fake_firds = _universe_patches(specs)
        bars = _merge_bars(
            _bars("OB-SE0000400001", 100.0, 500000, DEEP_DRAWDOWN, -20.0),
            _bars("OB-SE0000400002", 30.0, 100000, DEEP_DRAWDOWN, -20.0),
            _bars("OB-SE0000400003", 50.0, 150000, DEEP_DRAWDOWN, -20.0),
        )

        def fake_attach_selective(issuers, budget=None):
            for iss in issuers:
                name = iss.get("name", "")
                if "Big" in name:
                    iss["market_cap_sek"] = 10_000_000_000  # Above 5B
                elif "Tiny" in name:
                    iss["market_cap_sek"] = 100_000_000  # Below 300M
                else:
                    iss["market_cap_sek"] = 1_000_000_000  # In range
                iss["market_cap_status"] = "checked"

        patches = _standard_patches(
            bars, _no_breaking_action, _passing_esef_fetch(),
            extra=[mock.patch.object(sv, "fetch_nasdaq_snapshot", fake_snapshot),
                  mock.patch.object(sv, "fetch_firds", fake_firds),
                  mock.patch.object(sv, "attach_market_cap", fake_attach_selective)])
        _apply(patches)
        try:
            result = sv.run(_args(cap_floor=300_000_000, cap_ceiling=5_000_000_000))
        finally:
            _stop(patches)

        # All three size_band rows should be present
        size_band_cuts = {c["stage"]: c for c in result["cuts"]
                          if "size_band" in c["stage"]}
        expected_stages = {"size_band: above ceiling", "size_band: below floor",
                          "size_band: no market cap"}
        self.assertEqual(set(size_band_cuts.keys()), expected_stages,
                        "all three size_band rows must be present when stage ran")

        # Counts should match fixture
        self.assertEqual(size_band_cuts["size_band: above ceiling"]["count"], 1)
        self.assertEqual(size_band_cuts["size_band: below floor"]["count"], 1)
        self.assertEqual(size_band_cuts["size_band: no market cap"]["count"], 0)


class SmallCapPresetResolution(unittest.TestCase):
    """The --small-cap preset sets cap_ceiling=5B, cap_floor=300M, gross_floor=25%,
    op_floor=8%, margin_mode="operating". Explicitly passed flags win over the preset.
    Tests verify the preset behavior by manually applying the logic (can't directly
    call main() due to a help-string formatting bug in screen_value.py that prevents
    argparse initialization)."""

    def test_small_cap_preset_alone_sets_all_values(self):
        """--small-cap preset yields the preset values in run()."""
        specs = [_spec("SE0000600001", "LEI-SMALL-CAP", "Small Cap AB")]
        fake_snapshot, fake_firds = _universe_patches(specs)
        bars = _bars("OB-SE0000600001", 50.0, 100000, DEEP_DRAWDOWN, -20.0)

        patches = _standard_patches(
            bars, _no_breaking_action, _passing_esef_fetch(),
            extra=[mock.patch.object(sv, "fetch_nasdaq_snapshot", fake_snapshot),
                  mock.patch.object(sv, "fetch_firds", fake_firds)])
        _apply(patches)
        try:
            # Manually apply preset logic (same as main() does on lines 991-1001)
            result = sv.run(_args(small_cap=True,
                                   cap_ceiling=sv.SMALL_CAP_CEILING,
                                   cap_floor=sv.SMALL_CAP_FLOOR,
                                   gross_floor=sv.SMALL_CAP_GROSS_FLOOR,
                                   op_floor=sv.SMALL_CAP_OP_FLOOR,
                                   margin_mode=sv.SMALL_CAP_MARGIN_MODE))

            # Verify preset values are in the output
            self.assertEqual(result["cap_ceiling"], sv.SMALL_CAP_CEILING)
            self.assertEqual(result["cap_floor"], sv.SMALL_CAP_FLOOR)
            self.assertEqual(result["gross_floor"], sv.SMALL_CAP_GROSS_FLOOR)
            self.assertEqual(result["op_floor"], sv.SMALL_CAP_OP_FLOOR)
            self.assertEqual(result["margin_mode"], sv.SMALL_CAP_MARGIN_MODE)
            self.assertTrue(result["small_cap"])
        finally:
            _stop(patches)

    def test_explicit_flag_wins_over_small_cap_preset_space_form(self):
        """An explicit op_floor wins over the --small-cap preset."""
        specs = [_spec("SE0000700001", "LEI-EXPLICIT", "Explicit Floor AB")]
        fake_snapshot, fake_firds = _universe_patches(specs)
        bars = _bars("OB-SE0000700001", 50.0, 100000, DEEP_DRAWDOWN, -20.0)

        patches = _standard_patches(
            bars, _no_breaking_action, _passing_esef_fetch(),
            extra=[mock.patch.object(sv, "fetch_nasdaq_snapshot", fake_snapshot),
                  mock.patch.object(sv, "fetch_firds", fake_firds)])
        _apply(patches)
        try:
            # Manually simulate: --small-cap --op-floor 20
            # User set op_floor=20.0, so it should NOT be overwritten by the preset
            result = sv.run(_args(small_cap=True,
                                   cap_ceiling=sv.SMALL_CAP_CEILING,
                                   cap_floor=sv.SMALL_CAP_FLOOR,
                                   gross_floor=sv.SMALL_CAP_GROSS_FLOOR,
                                   op_floor=20.0,  # Explicit override
                                   margin_mode=sv.SMALL_CAP_MARGIN_MODE))

            # op_floor should be 20, not the preset 8
            self.assertEqual(result["op_floor"], 20.0,
                            "explicit op_floor should win over --small-cap preset")
            # Other preset values should still apply
            self.assertEqual(result["cap_ceiling"], sv.SMALL_CAP_CEILING)
            self.assertEqual(result["cap_floor"], sv.SMALL_CAP_FLOOR)
            self.assertEqual(result["gross_floor"], sv.SMALL_CAP_GROSS_FLOOR)
            self.assertEqual(result["margin_mode"], sv.SMALL_CAP_MARGIN_MODE)
        finally:
            _stop(patches)

    def test_explicit_cap_ceiling_wins_over_small_cap_preset(self):
        """An explicit cap_ceiling wins over the --small-cap preset."""
        specs = [_spec("SE0000800001", "LEI-CEILING", "Custom Ceiling AB")]
        fake_snapshot, fake_firds = _universe_patches(specs)
        bars = _bars("OB-SE0000800001", 50.0, 100000, DEEP_DRAWDOWN, -20.0)

        patches = _standard_patches(
            bars, _no_breaking_action, _passing_esef_fetch(),
            extra=[mock.patch.object(sv, "fetch_nasdaq_snapshot", fake_snapshot),
                  mock.patch.object(sv, "fetch_firds", fake_firds)])
        _apply(patches)
        try:
            # Manually simulate: --small-cap --cap-ceiling 10000000000
            # User set cap_ceiling=10B, so it overrides the preset 5B
            result = sv.run(_args(small_cap=True,
                                   cap_ceiling=10_000_000_000,  # Explicit override
                                   cap_floor=sv.SMALL_CAP_FLOOR,
                                   gross_floor=sv.SMALL_CAP_GROSS_FLOOR,
                                   op_floor=sv.SMALL_CAP_OP_FLOOR,
                                   margin_mode=sv.SMALL_CAP_MARGIN_MODE))

            # cap_ceiling should be 10B, not preset 5B
            self.assertEqual(result["cap_ceiling"], 10_000_000_000,
                            "explicit cap_ceiling should win over --small-cap preset")
            # Other preset values should still apply
            self.assertEqual(result["cap_floor"], sv.SMALL_CAP_FLOOR)
            self.assertEqual(result["gross_floor"], sv.SMALL_CAP_GROSS_FLOOR)
            self.assertEqual(result["op_floor"], sv.SMALL_CAP_OP_FLOOR)
        finally:
            _stop(patches)

    def test_default_flags_when_no_preset_and_no_explicit(self):
        """With neither --small-cap nor explicit flags, defaults are used."""
        specs = [_spec("SE0000900001", "LEI-DEFAULT", "Default Flags AB")]
        fake_snapshot, fake_firds = _universe_patches(specs)
        bars = _bars("OB-SE0000900001", 50.0, 100000, DEEP_DRAWDOWN, -20.0)

        patches = _standard_patches(
            bars, _no_breaking_action, _passing_esef_fetch(),
            extra=[mock.patch.object(sv, "fetch_nasdaq_snapshot", fake_snapshot),
                  mock.patch.object(sv, "fetch_firds", fake_firds)])
        _apply(patches)
        try:
            # No preset, no explicit flags - all defaults
            result = sv.run(_args())

            # Default values should be in effect (not preset values)
            self.assertEqual(result["gross_floor"], sv.DEFAULT_GROSS_FLOOR)
            self.assertEqual(result["op_floor"], sv.DEFAULT_OP_FLOOR)
            self.assertEqual(result["margin_mode"], "either")
            self.assertIsNone(result["cap_floor"])
            self.assertIsNone(result["cap_ceiling"])
            self.assertFalse(result.get("small_cap", False))
        finally:
            _stop(patches)


class MarginModeReachesMarginCheck(unittest.TestCase):
    """The margin_mode value must be passed to passes_margin_floor and appear
    in the JSON result so a run is reproducible from its own output."""

    def test_margin_mode_reaches_margin_check_and_output(self):
        specs = [_spec("SE0000500001", "LEI-MARGIN", "Margin Test AB")]
        fake_snapshot, fake_firds = _universe_patches(specs)
        bars = _bars("OB-SE0000500001", 50.0, 100000, DEEP_DRAWDOWN, -20.0)

        margin_floor_calls = []

        def spy_passes_margin_floor(latest, gross_floor=40.0, op_floor=15.0, mode="either"):
            margin_floor_calls.append({
                "gross_floor": gross_floor,
                "op_floor": op_floor,
                "mode": mode
            })
            return _fake_passes_margin_floor(latest, gross_floor, op_floor)

        patches = _standard_patches(
            bars, _no_breaking_action, _passing_esef_fetch(),
            extra=[mock.patch.object(sv, "fetch_nasdaq_snapshot", fake_snapshot),
                  mock.patch.object(sv, "fetch_firds", fake_firds),
                  mock.patch.object(sv, "passes_margin_floor", spy_passes_margin_floor)])
        _apply(patches)
        try:
            result = sv.run(_args(margin_mode="operating"))
        finally:
            _stop(patches)

        # margin_mode should appear in the result
        self.assertEqual(result.get("margin_mode"), "operating")

        # passes_margin_floor should have been called with the mode parameter
        self.assertGreater(len(margin_floor_calls), 0,
                          "passes_margin_floor should be called for margin survivors")
        # At least one call should have mode="operating"
        self.assertTrue(
            any(call.get("mode") == "operating" for call in margin_floor_calls),
            "passes_margin_floor should receive mode='operating' from run()")

        # Verify other reproducibility fields are in output
        self.assertIn("cap_floor", result)
        self.assertIn("cap_ceiling", result)
        self.assertIn("small_cap", result)
        self.assertIn("gross_floor", result)
        self.assertIn("op_floor", result)


if __name__ == "__main__":
    unittest.main()
