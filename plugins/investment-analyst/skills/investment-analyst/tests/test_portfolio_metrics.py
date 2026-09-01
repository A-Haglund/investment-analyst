#!/usr/bin/env python3
"""portfolio_metrics.py - concentration, hidden overlap, downside, confidence.

Covers, per the task brief:
  - HHI and effective-N against hand-computed values
  - the bear-case ratio AND drawdown (different numbers, asserted separately)
  - overlap grouping catching three same-sector holdings as one exposure
  - a non-SEK holding converting correctly
  - the weighted Portfolio Data Confidence
  - a holding with a missing price reported as DATA NOT AVAILABLE, not
    dropped silently from the weights

All offline: every price/sector/FX fetch is monkeypatched on the loaded
module before `build()` runs, and restored in tearDown - no network call
anywhere in this file. portfolio_metrics.py talks to portfolio_store.py only
through `store.load()` inside main(), never inside `build()`, so these tests
construct the portfolio dict in the fixed contract shape directly and never
need portfolio_store.py to exist.
"""
import datetime
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

pm = load("portfolio_metrics")
finfact = load("finfact")

TODAY = datetime.date.today().isoformat()


def make_portfolio(holdings, cash_amount=0.0, cash_currency="SEK", currency="SEK"):
    return {
        "name": "test", "account_type": "ISK", "currency": currency,
        "cash": {"amount": cash_amount, "currency": cash_currency},
        "holdings": holdings,
    }


def holding(name, symbol, quantity, isin="SE0000000000", note=None):
    return {"lei": None, "isin": isin, "name": name, "symbol": symbol,
            "quantity": quantity, "cost_per_share": 1234.5, "cost_currency": "SEK",
            "acquired": "2020-01-01", "note": note}


class Patched(unittest.TestCase):
    """Swaps every network-facing seam on the loaded module for a stub, and
    always restores it - even a failing test must not leak a monkeypatch
    into a later test in the same process."""

    def setUp(self):
        self._orig_price = pm.fetch_price
        self._orig_sector = pm.fetch_sector
        self._orig_fx = pm.fetch_fx
        self.prices = {}
        self.sectors = {}
        self.fx = {}
        pm.fetch_price = lambda symbol: self.prices.get(symbol)
        pm.fetch_sector = lambda name, symbol: self.sectors.get(name, (None, None))
        pm.fetch_fx = lambda currencies: {c: v for c, v in self.fx.items()
                                         if c in currencies}

    def tearDown(self):
        pm.fetch_price = self._orig_price
        pm.fetch_sector = self._orig_sector
        pm.fetch_fx = self._orig_fx

    def set_price(self, symbol, price, currency="SEK", source_key="nasdaq_reference"):
        self.prices[symbol] = {"price": price, "currency": currency,
                               "source_key": source_key, "source_label": "test",
                               "as_of": TODAY}

    def set_fx(self, ccy, sek_per_unit, source="Riksbanken SWEA", obs_date="2026-08-28"):
        self.fx[ccy] = {"sek_per_unit": sek_per_unit, "obs_date": obs_date,
                        "source": source, "status": "OK"}


# --------------------------------------------------------------------------
# Herfindahl / effective number of positions - pure functions, hand-computed
# --------------------------------------------------------------------------

class Concentration(unittest.TestCase):
    def test_hhi_hand_computed(self):
        # 0.5^2 + 0.3^2 + 0.2^2 = 0.25 + 0.09 + 0.04 = 0.38
        hhi = pm.herfindahl([0.5, 0.3, 0.2])
        self.assertAlmostEqual(hhi, 0.38, places=9)

    def test_effective_n_hand_computed(self):
        # 1 / 0.38 = 2.631578947368421...
        n = pm.effective_n(0.38)
        self.assertAlmostEqual(n, 1 / 0.38, places=9)
        self.assertAlmostEqual(n, 2.6315789473684212, places=9)

    def test_equal_weights_are_the_textbook_case(self):
        # portfolio.md: "0.10 ~ 10 equal positions" - the same identity at
        # a smaller N: 4 equal positions -> HHI 0.25, effective N exactly 4.
        hhi = pm.herfindahl([0.25, 0.25, 0.25, 0.25])
        self.assertAlmostEqual(hhi, 0.25, places=9)
        self.assertEqual(pm.effective_n(hhi), 4.0)

    def test_ten_equal_positions_matches_the_reference_file(self):
        hhi = pm.herfindahl([0.10] * 10)
        self.assertAlmostEqual(hhi, 0.10, places=9)
        self.assertAlmostEqual(pm.effective_n(hhi), 10.0, places=9)

    def test_end_to_end_concentration_via_build(self):
        p = Patched()
        p.setUp()
        try:
            p.set_price("A.ST", 100.0)
            p.set_price("B.ST", 100.0)
            portfolio = make_portfolio(
                [holding("A", "A.ST", 60), holding("B", "B.ST", 40)],
                cash_amount=0.0)
            r = pm.build(portfolio)
            # weights: A 6000/10000=0.6, B 4000/10000=0.4, cash 0
            # HHI = 0.6^2 + 0.4^2 + 0^2 = 0.36 + 0.16 = 0.52
            self.assertAlmostEqual(r["concentration"]["hhi"], 0.52, places=9)
            self.assertAlmostEqual(r["concentration"]["effective_n"], 1 / 0.52, places=9)
            self.assertAlmostEqual(r["concentration"]["top1"], 0.6, places=9)
        finally:
            p.tearDown()


# --------------------------------------------------------------------------
# Downside: bear-case ratio AND drawdown are different numbers
# --------------------------------------------------------------------------

class BearCase(unittest.TestCase):
    def test_ratio_and_drawdown_are_different_numbers_hand_computed(self):
        # cash 10% (ratio contribution 1.0), A 50%@0.6, B 40%@0.8
        # ratio = 0.10*1 + 0.50*0.6 + 0.40*0.8 = 0.10 + 0.30 + 0.32 = 0.72
        # drawdown = 1 - 0.72 = 0.28
        entries = [{"label": "A", "weight": 0.50, "bear_value": 60, "price": 100},
                  {"label": "B", "weight": 0.40, "bear_value": 80, "price": 100}]
        dd = pm.bear_case(entries, cash_weight=0.10)
        self.assertAlmostEqual(dd["ratio"], 0.72, places=9)
        self.assertAlmostEqual(dd["drawdown"], 0.28, places=9)
        self.assertNotAlmostEqual(dd["ratio"], dd["drawdown"], places=2)
        self.assertAlmostEqual(dd["ratio"] + dd["drawdown"], 1.0, places=9)

    def test_top_contributors_and_ten_percent_impairment_flag(self):
        entries = [{"label": "A", "weight": 0.50, "bear_value": 60, "price": 100},
                  {"label": "B", "weight": 0.40, "bear_value": 80, "price": 100}]
        dd = pm.bear_case(entries, cash_weight=0.10)
        # A loses 0.50 - 0.30 = 0.20 of the whole account; B loses 0.08
        self.assertEqual(dd["top_contributors"][0][0], "A")
        self.assertAlmostEqual(dd["top_contributors"][0][1], 0.20, places=9)
        self.assertEqual(dd["top_contributors"][1][0], "B")
        self.assertAlmostEqual(dd["top_contributors"][1][1], 0.08, places=9)
        flagged = [label for label, _c in dd["impairment_flags"]]
        self.assertIn("A", flagged)
        self.assertNotIn("B", flagged)

    def test_missing_bear_value_is_excluded_not_assumed(self):
        entries = [{"label": "A", "weight": 0.50, "bear_value": 60, "price": 100},
                  {"label": "B", "weight": 0.40, "bear_value": None, "price": 100}]
        dd = pm.bear_case(entries, cash_weight=0.10)
        # coverage = cash 0.10 + A 0.50 = 0.60; B is excluded entirely
        self.assertAlmostEqual(dd["coverage_weight"], 0.60, places=9)
        self.assertAlmostEqual(dd["ratio"], 0.10 + 0.30, places=9)
        self.assertEqual(len(dd["top_contributors"]), 1)

    def test_end_to_end_via_build_with_bear_tag_on_the_note_field(self):
        p = Patched()
        p.setUp()
        try:
            p.set_price("A.ST", 100.0)
            portfolio = make_portfolio(
                [holding("A", "A.ST", 100, note="bear=60")],
                cash_amount=0.0)
            r = pm.build(portfolio)
            dd = r["downside"]
            # single holding, full weight 1.0, bear/price = 0.6
            self.assertAlmostEqual(dd["ratio"], 0.6, places=9)
            self.assertAlmostEqual(dd["drawdown"], 0.4, places=9)
        finally:
            p.tearDown()

    def test_zero_bear_coverage_reports_data_not_available_not_a_fabricated_ratio(self):
        # B3: this is the REALISTIC default -- nothing in the reference docs
        # tells an analyst `bear=` exists, and --paste wipes the note field
        # on every use, so a typical book has no bear value anywhere. Old
        # code seeded `ratio = cash_weight * 1.0` and just never added
        # anything to it, so a 95%-invested, entirely untagged portfolio
        # reported a ratio of 0.05 and a "95% bear-case drawdown" headline
        # backed by nothing at all. Ratio and drawdown must come back None,
        # with a status flag the renderer can key off of.
        entries = [{"label": "A", "weight": 0.60, "bear_value": None, "price": 100},
                  {"label": "B", "weight": 0.35, "bear_value": None, "price": 100}]
        dd = pm.bear_case(entries, cash_weight=0.05)
        self.assertIsNone(dd["ratio"])
        self.assertIsNone(dd["drawdown"])
        self.assertEqual(dd["status"], "DATA NOT AVAILABLE")
        self.assertIn("reason", dd)

    def test_end_to_end_zero_coverage_via_build_and_render(self):
        p = Patched()
        p.setUp()
        try:
            p.set_price("A.ST", 100.0)
            p.set_price("B.ST", 100.0)
            portfolio = make_portfolio(
                [holding("A", "A.ST", 60), holding("B", "B.ST", 35)],
                cash_amount=5.0)
            r = pm.build(portfolio)
            dd = r["downside"]
            self.assertIsNone(dd["ratio"])
            self.assertIsNone(dd["drawdown"])
            self.assertEqual(dd["status"], "DATA NOT AVAILABLE")
            pm.apply_schablon(r, None, None)
            text = pm.render_text(r)
            self.assertIn("DATA NOT AVAILABLE", text)
            # the fabricated headline must never appear: no bare "0.9500" or
            # "0.05" masquerading as ratio/drawdown in the DOWNSIDE section
            downside_section = text.split("DOWNSIDE")[1].split("PORTFOLIO DATA")[0]
            self.assertNotIn("0.9500", downside_section)
            self.assertNotIn("0.0500", downside_section)
        finally:
            p.tearDown()


# --------------------------------------------------------------------------
# Hidden overlap: three same-sector holdings are one exposure
# --------------------------------------------------------------------------

class HiddenOverlap(unittest.TestCase):
    def test_three_same_sector_holdings_group_as_one_exposure(self):
        holdings = [
            {"label": "SAND", "weight": 0.30, "sector": "Industrials", "driver": None},
            {"label": "ATCO", "weight": 0.25, "sector": "Industrials", "driver": None},
            {"label": "EPI", "weight": 0.20, "sector": "Industrials", "driver": None},
            {"label": "SEB", "weight": 0.25, "sector": "Financials", "driver": None},
        ]
        ov = pm._overlap(holdings)
        self.assertAlmostEqual(ov["largest_single_holding_weight"], 0.30, places=9)
        largest = ov["largest_true_exposure"]
        self.assertAlmostEqual(largest["weight"], 0.75, places=9)
        self.assertEqual(set(largest["members"]), {"SAND", "ATCO", "EPI"})
        # the whole point: the true exposure is larger than any single holding
        self.assertGreater(largest["weight"], ov["largest_single_holding_weight"])

    def test_explicit_driver_tag_spans_sectors(self):
        holdings = [
            {"label": "X", "weight": 0.30, "sector": "Industrials", "driver": "mining capex"},
            {"label": "Y", "weight": 0.20, "sector": "Basic Materials", "driver": "mining capex"},
            {"label": "Z", "weight": 0.15, "sector": "Industrials", "driver": None},
        ]
        ov = pm._overlap(holdings)
        driver_group = ov["driver_groups"][0]
        self.assertAlmostEqual(driver_group["weight"], 0.50, places=9)
        self.assertEqual(set(driver_group["members"]), {"X", "Y"})

    def test_tagging_one_holding_with_a_driver_cannot_lower_reported_concentration(self):
        # M3: three Industrials at 30% each (90% sector exposure); ONE of
        # them also carries an accurate driver= tag. Old code took
        # driver_groups[0] unconditionally the instant any driver group
        # existed, which fragmented the 90% Industrials exposure into a 60%
        # "(by sector)" group plus a 30% "mining capex" group and reported
        # the headline "largest true exposure" as 60% -- tagging the
        # portfolio MORE accurately made it look SAFER. The true, undiluted
        # 90% sector exposure must still win.
        holdings = [
            {"label": "A", "weight": 0.30, "sector": "Industrials", "driver": "mining capex"},
            {"label": "B", "weight": 0.30, "sector": "Industrials", "driver": None},
            {"label": "C", "weight": 0.30, "sector": "Industrials", "driver": None},
        ]
        ov = pm._overlap(holdings)
        # sanity: the driver grouping alone would have under-reported this
        self.assertAlmostEqual(ov["driver_groups"][0]["weight"], 0.60, places=9)
        largest = ov["largest_true_exposure"]
        self.assertAlmostEqual(largest["weight"], 0.90, places=9)
        self.assertEqual(set(largest["members"]), {"A", "B", "C"})

    def test_end_to_end_via_build_with_note_tagged_sector_and_driver(self):
        p = Patched()
        p.setUp()
        try:
            p.set_price("SAND.ST", 100.0)
            p.set_price("ATCO.ST", 100.0)
            p.set_price("EPI.ST", 100.0)
            portfolio = make_portfolio([
                holding("Sandvik", "SAND.ST", 30, note="sector=Industrials; driver=mining capex"),
                holding("Atlas Copco", "ATCO.ST", 25, note="sector=Industrials; driver=mining capex"),
                holding("Epiroc", "EPI.ST", 20, note="sector=Industrials; driver=mining capex"),
            ], cash_amount=2500.0)
            r = pm.build(portfolio)
            sector_groups = r["overlap"]["sector_groups"]
            self.assertEqual(len(sector_groups), 1)
            self.assertEqual(sector_groups[0]["label"], "Industrials")
            self.assertEqual(set(sector_groups[0]["members"]),
                             {"SAND.ST", "ATCO.ST", "EPI.ST"})
        finally:
            p.tearDown()


# --------------------------------------------------------------------------
# A non-SEK holding converts correctly
# --------------------------------------------------------------------------

class CurrencyConversion(unittest.TestCase):
    def test_sek_per_returns_the_riksbanken_rate(self):
        fx_table = {"EUR": {"sek_per_unit": 11.0, "obs_date": "2026-08-28",
                            "source": "Riksbanken SWEA", "status": "OK"}}
        rate, row = pm.sek_per("EUR", fx_table)
        self.assertEqual(rate, 11.0)
        self.assertIsNotNone(row)

    def test_sek_needs_no_conversion(self):
        rate, _row = pm.sek_per("SEK", {})
        self.assertEqual(rate, 1.0)

    def test_missing_rate_is_none_not_a_guess(self):
        rate, _row = pm.sek_per("NOK", {"EUR": {"sek_per_unit": 11.0}})
        self.assertIsNone(rate)

    def test_end_to_end_eur_holding_market_value_in_sek(self):
        p = Patched()
        p.setUp()
        try:
            p.set_price("EVO.ST", 1000.0, currency="EUR", source_key="yahoo")
            p.set_fx("EUR", 11.0)
            portfolio = make_portfolio(
                [holding("Evolution", "EVO.ST", 10)], cash_amount=0.0)
            r = pm.build(portfolio)
            self.assertEqual(len(r["holdings"]), 1)
            hd = r["holdings"][0]
            # 10 shares * 1000 EUR * 11.0 SEK/EUR = 110,000 SEK
            self.assertAlmostEqual(hd["market_value"], 110000.0, places=6)
            self.assertEqual(hd["quote_currency"], "EUR")
        finally:
            p.tearDown()

    def test_end_to_end_currency_exposure_table_includes_the_foreign_slice(self):
        p = Patched()
        p.setUp()
        try:
            p.set_price("EVO.ST", 1000.0, currency="EUR", source_key="yahoo")
            p.set_price("SAND.ST", 100.0, currency="SEK")
            p.set_fx("EUR", 11.0)
            portfolio = make_portfolio([
                holding("Evolution", "EVO.ST", 10),
                holding("Sandvik", "SAND.ST", 100),
            ], cash_amount=0.0)
            r = pm.build(portfolio)
            by_ccy = {row["currency"]: row["weight"] for row in r["currency_exposure"]}
            # EUR leg = 110,000; SEK leg = 100*100 = 10,000; total 120,000
            self.assertAlmostEqual(by_ccy["EUR"], 110000.0 / 120000.0, places=6)
            self.assertAlmostEqual(by_ccy["SEK"], 10000.0 / 120000.0, places=6)
        finally:
            p.tearDown()

    def test_holding_priced_in_an_unconvertible_currency_is_data_not_available(self):
        p = Patched()
        p.setUp()
        try:
            p.set_price("XYZ.OL", 100.0, currency="NOK", source_key="yahoo")
            # fetch_fx stub returns nothing for NOK - rate genuinely unknown
            portfolio = make_portfolio([holding("Ghost NO", "XYZ.OL", 10)])
            r = pm.build(portfolio)
            self.assertEqual(r["holdings"], [])
            self.assertEqual(len(r["unresolved"]), 1)
            self.assertEqual(r["unresolved"][0]["status"], "DATA NOT AVAILABLE")
            self.assertIn("FX", r["unresolved"][0]["reason"].upper())
        finally:
            p.tearDown()


# --------------------------------------------------------------------------
# Weighted Portfolio Data Confidence
# --------------------------------------------------------------------------

class DataConfidence(unittest.TestCase):
    def _fact(self, source, currency="SEK"):
        return finfact.FinancialFact(
            "price", 100, source, TODAY, publication_date=TODAY,
            currency=currency, freshness_key="price",
            verification=finfact.Verification.SINGLE_SOURCE)

    def test_single_holding_confidence_hand_computed(self):
        # confidence_score(): tier1 fraction 1.0 -> no tier penalty; not
        # verified -> -25; not stale (published today); nothing missing.
        # 100 - 0 - 25 = 75
        conf = pm.holding_confidence([self._fact("nasdaq_reference")],
                                     sector_known=True, bear_known=True)
        self.assertEqual(conf, 75.0)

    def test_tier4_source_plus_missing_sector_and_bear_penalties(self):
        # tier4 (yahoo): (1 - 0)*30 = -30 tier penalty, -25 unverified = 45
        # base, then -10 (no sector) -15 (no bear case) = 20
        conf = pm.holding_confidence([self._fact("yahoo", currency="USD")],
                                     sector_known=False, bear_known=False)
        self.assertEqual(conf, 20.0)

    def test_value_weighted_aggregate_hand_computed(self):
        # cash 10% @ CASH_CONFIDENCE(60), A 50% @ 75, B 40% @ 20
        # 0.10*60 + 0.50*75 + 0.40*20 = 6 + 37.5 + 8 = 51.5
        weighted = [(0.50, 75.0), (0.40, 20.0), (0.10, float(pm.CASH_CONFIDENCE))]
        agg = pm.portfolio_confidence(weighted)
        self.assertAlmostEqual(agg, 51.5, places=9)

    def test_end_to_end_portfolio_data_confidence(self):
        p = Patched()
        p.setUp()
        try:
            p.set_price("A.ST", 100.0, source_key="nasdaq_reference")
            p.set_price("B.ST", 100.0, source_key="yahoo")
            p.sectors["A"] = ("Industrials", "nasdaq_reference")
            # B: no sector resolved, no bear tag -> the weaker holding
            portfolio = make_portfolio([
                holding("A", "A.ST", 50, note="bear=90"),
                holding("B", "B.ST", 40),
            ], cash_amount=1000.0)
            r = pm.build(portfolio)
            # weights: total = 5000+4000+1000 = 10000
            # A 0.50 conf 75, B 0.40 conf 20, cash 0.10 conf CASH_CONFIDENCE(60)
            expected = 0.50 * 75.0 + 0.40 * 20.0 + 0.10 * float(pm.CASH_CONFIDENCE)
            self.assertAlmostEqual(r["data_confidence"], expected, places=6)
            self.assertAlmostEqual(expected, 51.5, places=9)
        finally:
            p.tearDown()


# --------------------------------------------------------------------------
# A missing price is reported as DATA NOT AVAILABLE, never dropped silently
# --------------------------------------------------------------------------

class MissingPrice(unittest.TestCase):
    def test_unpriced_holding_appears_in_unresolved_not_vanished(self):
        p = Patched()
        p.setUp()
        try:
            p.set_price("A.ST", 100.0)
            # B.ST is deliberately left out of self.prices -> fetch_price
            # returns None for it, as it would for a genuinely dead symbol.
            portfolio = make_portfolio([
                holding("A", "A.ST", 10),
                holding("Ghost", "B.ST", 5),
            ], cash_amount=0.0)
            r = pm.build(portfolio)
            self.assertEqual(len(r["holdings"]), 1)
            self.assertEqual(r["holdings"][0]["label"], "A.ST")
            self.assertEqual(len(r["unresolved"]), 1)
            unresolved = r["unresolved"][0]
            self.assertEqual(unresolved["status"], "DATA NOT AVAILABLE")
            self.assertEqual(unresolved["label"], "B.ST")
        finally:
            p.tearDown()

    def test_unpriced_holding_is_excluded_from_weights_not_counted_as_zero(self):
        p = Patched()
        p.setUp()
        try:
            p.set_price("A.ST", 100.0)
            portfolio = make_portfolio([
                holding("A", "A.ST", 10),
                holding("Ghost", "B.ST", 5),
            ], cash_amount=0.0)
            r = pm.build(portfolio)
            # total_value must reflect ONLY the known 1000, not a phantom
            # zero-value row that would otherwise dilute every weight
            self.assertAlmostEqual(r["total_value"], 1000.0, places=6)
            self.assertAlmostEqual(r["holdings"][0]["weight"], 1.0, places=6)
        finally:
            p.tearDown()

    def test_rendered_text_shows_the_gap_explicitly(self):
        p = Patched()
        p.setUp()
        try:
            portfolio = make_portfolio([holding("Ghost", "GHOST.ST", 5)])
            r = pm.build(portfolio)
            pm.apply_schablon(r, None, None)
            text = pm.render_text(r)
            self.assertIn("DATA NOT AVAILABLE", text)
            self.assertIn("GHOST.ST", text)
        finally:
            p.tearDown()

    def test_quantity_none_is_routed_to_unresolved_never_raises(self):
        # M6: `h.get("quantity", 0) * price * rate` reads as a None-guard but
        # is not one -- the key IS present with value None on a hand-edited
        # or externally written store file, so the default never fires and
        # `None * float` raises TypeError, taking down the WHOLE report over
        # one bad row. build()'s own docstring promises it never does that.
        p = Patched()
        p.setUp()
        try:
            p.set_price("A.ST", 100.0)
            portfolio = make_portfolio([
                holding("A", "A.ST", 10),
                holding("NoQty", "NOQTY.ST", None),
            ], cash_amount=0.0)
            r = pm.build(portfolio)  # must not raise TypeError
            self.assertEqual(len(r["holdings"]), 1)
            self.assertEqual(r["holdings"][0]["label"], "A.ST")
            self.assertEqual(len(r["unresolved"]), 1)
            self.assertEqual(r["unresolved"][0]["label"], "NOQTY.ST")
            self.assertEqual(r["unresolved"][0]["status"], "DATA NOT AVAILABLE")
        finally:
            p.tearDown()


# --------------------------------------------------------------------------
# Cost basis must never appear in this script's output
# --------------------------------------------------------------------------

class CostBasisNeverShown(unittest.TestCase):
    def test_cost_per_share_absent_from_rendered_text(self):
        p = Patched()
        p.setUp()
        try:
            p.set_price("A.ST", 100.0)
            portfolio = make_portfolio(
                [holding("A", "A.ST", 10, note="bear=80")], cash_amount=500.0)
            portfolio["holdings"][0]["cost_per_share"] = 777.77
            r = pm.build(portfolio)
            pm.apply_schablon(r, None, None)
            text = pm.render_text(r)
            self.assertNotIn("777.77", text)
            # A substring probe on a formatted number is not a real guarantee:
            # 777.77 happens to render unchanged here, but it would just as
            # easily pass if the value were formatted as "777,77", "777.8" or
            # "778" while still leaking cost data by a different route. Assert
            # structurally instead -- neither the specific cost_per_share
            # figure nor any cost-basis KEY (as opposed to the unrelated
            # "illustrative_annual_cost" schablon-drag field, which legitimately
            # contains the substring "cost") appears anywhere in the result.
            dumped = json.dumps(r, default=str)
            self.assertNotIn("777.77", dumped)
            self.assertNotIn("cost_per_share", dumped)
            self.assertNotIn("cost_basis", dumped)
        finally:
            p.tearDown()


# --------------------------------------------------------------------------
# note-tag parsing
# --------------------------------------------------------------------------

class NoteTags(unittest.TestCase):
    def test_all_three_tags_parsed_together(self):
        tags = pm.parse_tags("bear=210; driver=mining capex; sector=Industrials; free text")
        self.assertEqual(tags["bear"], 210.0)
        self.assertEqual(tags["driver"], "mining capex")
        self.assertEqual(tags["sector"], "Industrials")

    def test_no_tags_is_an_empty_dict(self):
        self.assertEqual(pm.parse_tags("just a plain note"), {})
        self.assertEqual(pm.parse_tags(None), {})
        self.assertEqual(pm.parse_tags(""), {})

    def test_malformed_bear_number_is_ignored_not_a_crash(self):
        tags = pm.parse_tags("bear=not-a-number")
        self.assertNotIn("bear", tags)

    def test_swedish_decimal_comma_is_not_read_as_a_thousands_separator(self):
        # B1: a naive `.replace(",", "")` turns "210,5" into 2105.0 -- 10x
        # too high, and enough to send bear/price above 1 on a real holding.
        tags = pm.parse_tags("bear=210,5")
        self.assertEqual(tags["bear"], 210.5)

    def test_space_grouped_thousands_after_bear(self):
        # B1: the old regex's character class stopped at the first space, so
        # "bear=1 240" captured only "1" and silently dropped " 240" -- this
        # asserts the whole space-grouped figure is captured and parsed.
        tags = pm.parse_tags("bear=1 240")
        self.assertEqual(tags["bear"], 1240.0)


if __name__ == "__main__":
    unittest.main()
