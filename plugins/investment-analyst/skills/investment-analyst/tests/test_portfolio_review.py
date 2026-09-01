#!/usr/bin/env python3
"""portfolio_review.py - the triage engine, offline.

Everything here runs with THESIS_LEDGER_HOME pointed at a throwaway temp
directory (so a fired breaker / clean thesis / no-thesis holding can be set
up on disk without touching the user's real ledger) and every live-data
fetch (fetch_quote, fetch_news_since, fetch_short_interest,
fetch_insider_activity, fetch_valuation_gate) monkeypatched - no subprocess
is ever spawned and no socket is ever opened by this suite.

Covers, per the spec this script was built against:
  - a fired breaker producing EXIT regardless of a cheap price
  - a clean holding producing HOLD with a last-reviewed date
  - a holding with no stored view being flagged, not passed
  - each of the five layer-2 alerts firing individually
  - layer 1 and layer 2 running without any network
  - cost_per_share appearing nowhere in an action record
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

pr = load("portfolio_review")
thesis_ledger = pr.thesis_ledger


def make_holding(**kw):
    base = {"lei": "549300ABCDEF1234ABCD", "isin": "SE0000111222",
            "name": "Test AB", "symbol": "TEST.ST", "quantity": 100,
            "cost_per_share": None, "cost_currency": None, "acquired": None,
            "note": None}
    base.update(kw)
    return base


def ledger_key_for(holding):
    return thesis_ledger.ledger_key({"lei": holding.get("lei"), "isin": holding.get("isin")})


def write_ledger(holding, status, triggered=None, last_evaluated="2026-02-10T00:00:00Z",
                  active=True, thesis_text=None):
    """Write a minimal but schema-shaped ledger file straight to disk - no
    network, no --evaluate run: this stands in for whatever thesis_ledger's
    own --evaluate last found, which is exactly what portfolio_review's
    layer 1 reads."""
    key = ledger_key_for(holding)
    identity = {"lei": holding.get("lei"), "isin": holding.get("isin"),
                "company_name": holding.get("name")}
    led = thesis_ledger.new_ledger(key, identity, holding.get("name"))
    thesis = {"id": "T1",
              "thesis": thesis_text or "EBIT margin holds above 15% through the cycle.",
              "status": status, "active": active, "action": None, "confidence": 0.5,
              "status_since": last_evaluated, "last_evaluated": last_evaluated,
              "triggered_breakers": triggered or []}
    led["theses"] = [thesis]
    thesis_ledger.save_ledger(led)
    return key


NO_ALERTS_PATCHES = {
    "fetch_news_since": lambda holding: ({"slug": "test", "items": []}, None),
    "fetch_short_interest": lambda holding: (
        {"trend": {"windows": {"30": {"change_pp": 0.0}, "90": {"change_pp": 0.0}}},
         "aggregate_pct": 0.5}, None),
    "fetch_insider_activity": lambda holding, months=6, timeout=90: (
        {"analysis": {"discretionary": {"net_value": 0.0, "buy_value": 0.0,
                                         "sell_value": 0.0}, "currency": "SEK"}}, None),
    "fetch_valuation_gate": lambda holding: (
        {"passed": True, "report": "VALUATION INTEGRITY: PASSED", "states": []}, None),
}


class _TempLedgerCase(unittest.TestCase):
    """Redirects thesis_ledger's storage to a throwaway temp dir for the
    duration of the test, so writing a fixture ledger never touches the
    real ~/.investment-analyst/thesis-ledger."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="portfolio-review-test-")
        self._old = os.environ.get("THESIS_LEDGER_HOME")
        os.environ["THESIS_LEDGER_HOME"] = self.tmp

    def tearDown(self):
        if self._old is None:
            os.environ.pop("THESIS_LEDGER_HOME", None)
        else:
            os.environ["THESIS_LEDGER_HOME"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def patch_no_alerts(self):
        """Apply every layer-2 fetch patch that reports "nothing changed".
        Returns the list of active mock.patch context managers (already
        entered) so a test can override one of them afterwards."""
        patchers = [mock.patch.object(pr, name, fn) for name, fn in NO_ALERTS_PATCHES.items()]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)


class BreakerShortCircuitsToExit(_TempLedgerCase):
    def test_fired_breaker_forces_exit_regardless_of_a_cheap_price(self):
        holding = make_holding()
        write_ledger(holding, status="BROKEN",
                     triggered=["ebit_margin < 15% for 2 consecutive years"])
        self.patch_no_alerts()
        # A price screaming BUY on pure value grounds must not move the needle.
        cheap_price = {"price": 1.0, "currency": "SEK"}
        record = pr.build_holding_record(holding, "all", price_info=cheap_price)
        self.assertEqual(record["action"], "EXIT")
        self.assertIn("breaker fired", record["reason"])
        # The breaker short-circuits BEFORE layer 2 spends anything on it.
        self.assertEqual(record["layer2"]["alerts"], [])
        self.assertFalse(record["layer2"]["any_fired"])


class CleanHoldingHolds(_TempLedgerCase):
    def test_clean_holding_produces_hold_with_last_reviewed_date(self):
        # A structured fair-value range is on record (portfolio_store's
        # schema fields) and the live price sits inside it, so price_range
        # comes back genuinely checked=True/fired=False - a real clean pass,
        # not "cannot judge" masquerading as one.
        holding = make_holding(fair_value_low=90.0, fair_value_high=110.0)
        write_ledger(holding, status="STABLE", last_evaluated="2026-02-10T00:00:00Z")
        self.patch_no_alerts()
        record = pr.build_holding_record(holding, "all",
                                         price_info={"price": 100.0, "currency": "SEK"})
        self.assertEqual(record["action"], "HOLD")
        self.assertIn("2026-02-10", record["reason"])
        self.assertFalse(record["flagged"])


class UnreviewedHoldingIsFlaggedNotPassed(_TempLedgerCase):
    def test_no_stored_view_is_an_alert_not_a_pass(self):
        holding = make_holding()  # nothing written to the ledger at all
        self.patch_no_alerts()
        record = pr.build_holding_record(holding, "all", price_info=None)
        self.assertIsNone(record["action"], "an unreviewed holding must not get a decided action")
        self.assertTrue(record["flagged"])
        self.assertFalse(record["layer1"]["has_ledger_entry"])
        # No thesis to compare against -> layer 2 is skipped as wasted work,
        # not silently treated as "checked and clean".
        self.assertEqual(record["layer2"]["alerts"], [])


class Layer1AndLayer2NeedNoNetwork(_TempLedgerCase):
    def test_layer_one_alone_touches_no_fetch_function(self):
        holding = make_holding()
        write_ledger(holding, status="STABLE")
        # Deliberately do NOT patch any fetch_* - layer 1 must not need them.
        record = pr.build_holding_record(holding, 1)
        self.assertIn("layer1", record)
        self.assertNotIn("layer2", record)
        self.assertNotIn("action", record)

    def test_layer_two_runs_fully_offline_when_every_fetch_is_patched(self):
        holding = make_holding()
        write_ledger(holding, status="STABLE")
        self.patch_no_alerts()
        record = pr.build_holding_record(holding, 2, price_info={"price": 100.0})
        self.assertIn("layer2", record)
        self.assertFalse(record["layer2"]["any_fired"])
        # price_range legitimately reports checked=False (no fair-value range
        # is on record for this fixture thesis) - that is an honest "cannot
        # judge", not a broken offline run. Every alert that a fetch was
        # actually patched for must come back checked.
        checked_by_code = {a["code"]: a["checked"] for a in record["layer2"]["alerts"]}
        for code in ("new_report", "short_interest", "insider_selling", "valuation_stale"):
            self.assertTrue(checked_by_code[code], checked_by_code)


class EachLayer2AlertFiresIndividually(unittest.TestCase):
    """These call the alert_* functions directly against a hand-built
    layer-1 view, so each threshold is exercised in isolation."""

    # A holding's freeform `note` is never scraped for a fair-value range
    # (removed entirely - see alert_price_range's docstring), so these drive
    # the fallback path through the THESIS text instead, via a hand-built
    # layer-1 view - exactly what parse_fair_value_range is for.

    def test_price_range_alert_fires_above_the_recorded_range(self):
        holding = make_holding(note="fair value 999-999 per share, ignored")
        view1 = {"theses": [{"thesis": "fair value 100-120 per share"}]}
        alert = pr.alert_price_range(holding, view1, {"price": 200.0})
        self.assertTrue(alert["fired"])
        self.assertEqual(alert["evidence"]["side"], "above")

    def test_price_range_alert_fires_below_the_recorded_range(self):
        holding = make_holding(note="fair value 999-999 per share, ignored")
        view1 = {"theses": [{"thesis": "fair value 100-120 per share"}]}
        alert = pr.alert_price_range(holding, view1, {"price": 50.0})
        self.assertTrue(alert["fired"])
        self.assertEqual(alert["evidence"]["side"], "below")

    def test_price_range_alert_clean_inside_the_range(self):
        holding = make_holding(note="fair value 999-999 per share, ignored")
        view1 = {"theses": [{"thesis": "fair value 100-120 per share"}]}
        alert = pr.alert_price_range(holding, view1, {"price": 110.0})
        self.assertFalse(alert["fired"])

    def test_price_range_alert_never_scrapes_the_holding_note(self):
        # note carries a range; thesis text carries none; schema fields are
        # unset. If note were still scraped this would fire on note's range -
        # instead it must come back "cannot check", proving note is dead as
        # a source.
        holding = make_holding(note="fair value 100-120 per share")
        view1 = {"theses": [{"thesis": "no numbers here at all"}]}
        alert = pr.alert_price_range(holding, view1, {"price": 200.0})
        self.assertFalse(alert["checked"])
        self.assertFalse(alert["fired"])

    def test_swedish_written_range_parses_correctly(self):
        # The old second number parser turned a Swedish decimal comma into a
        # thousands separator (190,5 -> 1905.0). The one true parser
        # (mfn_news.to_number) must read it correctly.
        rng = pr.parse_fair_value_range("fair value 190,5-215,5")
        self.assertEqual(rng, (190.5, 215.5))

    def test_schema_fields_take_precedence_over_scraped_thesis_text(self):
        # fair_value_low/fair_value_high (portfolio_store's schema) must win
        # over anything found in thesis text, even a thesis range that would
        # produce a different verdict at the same price.
        holding = make_holding(fair_value_low=150.0, fair_value_high=160.0)
        view1 = {"theses": [{"thesis": "fair value 10-20 per share"}]}
        alert = pr.alert_price_range(holding, view1, {"price": 155.0})
        self.assertTrue(alert["checked"])
        self.assertFalse(alert["fired"], "price 155 sits inside the schema range "
                          "150-160, not the scraped 10-20: %r" % alert)

    def test_new_report_alert_fires_on_a_release_after_last_review(self):
        holding = make_holding()
        with mock.patch.object(pr, "fetch_news_since", lambda h: (
                {"slug": "test", "items": [
                    {"title": "Interim report Q2", "date": "2026-07-15T08:00:00",
                     "is_report": True, "regulatory": True}]}, None)):
            alert = pr.alert_new_report(holding, "2026-01-01T00:00:00Z")
        self.assertTrue(alert["fired"])
        self.assertIn("2026-07-15", alert["detail"])

    def test_new_report_alert_clean_when_nothing_newer(self):
        holding = make_holding()
        with mock.patch.object(pr, "fetch_news_since", lambda h: (
                {"slug": "test", "items": [
                    {"title": "Old release", "date": "2025-01-01T08:00:00",
                     "is_report": True, "regulatory": False}]}, None)):
            alert = pr.alert_new_report(holding, "2026-01-01T00:00:00Z")
        self.assertFalse(alert["fired"])

    def test_short_interest_alert_fires_on_a_material_30d_rise(self):
        holding = make_holding()
        with mock.patch.object(pr, "fetch_short_interest", lambda h, timeout=90: (
                {"trend": {"windows": {"30": {"change_pp": 2.5},
                                       "90": {"change_pp": 2.5}}},
                 "aggregate_pct": 6.0}, None)):
            alert = pr.alert_short_interest(holding)
        self.assertTrue(alert["fired"])

    def test_short_interest_alert_clean_on_a_small_move(self):
        holding = make_holding()
        with mock.patch.object(pr, "fetch_short_interest", lambda h, timeout=90: (
                {"trend": {"windows": {"30": {"change_pp": 0.2},
                                       "90": {"change_pp": 0.3}}},
                 "aggregate_pct": 1.0}, None)):
            alert = pr.alert_short_interest(holding)
        self.assertFalse(alert["fired"])

    def test_insider_selling_alert_fires_on_net_selling(self):
        holding = make_holding()
        with mock.patch.object(pr, "fetch_insider_activity", lambda h, months=6, timeout=90: (
                {"analysis": {"discretionary": {"net_value": -5_000_000.0,
                                                "buy_value": 0.0,
                                                "sell_value": 5_000_000.0},
                              "currency": "SEK"}}, None)):
            alert = pr.alert_insider_selling(holding)
        self.assertTrue(alert["fired"])
        self.assertEqual(alert["evidence"]["direction"], "NET SELLING")

    def test_insider_selling_alert_clean_when_flat(self):
        holding = make_holding()
        with mock.patch.object(pr, "fetch_insider_activity", lambda h, months=6, timeout=90: (
                {"analysis": {"discretionary": {"net_value": 1000.0,
                                                "buy_value": 500_000.0,
                                                "sell_value": 499_000.0},
                              "currency": "SEK"}}, None)):
            alert = pr.alert_insider_selling(holding)
        self.assertFalse(alert["fired"])

    def test_valuation_stale_alert_fires_when_gate_fails(self):
        holding = make_holding()
        with mock.patch.object(pr, "fetch_valuation_gate", lambda h, timeout=90: (
                {"passed": False,
                 "report": "VALUATION INTEGRITY: FAILED\nReason: TTM EBIT is 608 days stale.",
                 "states": ["DATA_STALE"]}, None)):
            alert = pr.alert_valuation_stale(holding)
        self.assertTrue(alert["fired"])

    def test_valuation_stale_alert_clean_when_gate_passes(self):
        holding = make_holding()
        with mock.patch.object(pr, "fetch_valuation_gate", lambda h, timeout=90: (
                {"passed": True, "report": "VALUATION INTEGRITY: PASSED", "states": []}, None)):
            alert = pr.alert_valuation_stale(holding)
        self.assertFalse(alert["fired"])


class CostBasisNeverEntersAnActionRecord(_TempLedgerCase):
    def test_cost_per_share_appears_nowhere_in_an_action_record(self):
        holding = make_holding(cost_per_share=42.0, cost_currency="SEK",
                               acquired="2024-01-01")
        write_ledger(holding, status="STABLE")
        self.patch_no_alerts()
        record = pr.build_holding_record(holding, "all",
                                         price_info={"price": 100.0, "currency": "SEK"})
        import json
        blob = json.dumps(record)
        self.assertNotIn("cost_per_share", blob)
        self.assertNotIn("cost_currency", blob)
        self.assertNotIn("42.0", blob)
        # The performance block is the one place it IS allowed to appear.
        perf = pr.build_performance(holding, {"price": 100.0, "currency": "SEK"})
        self.assertEqual(perf["cost_per_share"], 42.0)

    def test_action_record_never_carries_acquired_either(self):
        holding = make_holding(acquired="2024-01-01")
        write_ledger(holding, status="STABLE")
        self.patch_no_alerts()
        record = pr.build_holding_record(holding, "all",
                                         price_info={"price": 100.0, "currency": "SEK"})
        import json
        self.assertNotIn("acquired", json.dumps(record))


class EveryFetchFailingIsNotACleanPass(_TempLedgerCase):
    """The bug the review found: with every live source down, decide_action
    used to read only a["fired"] and never a["checked"], so zero questions
    asked came back as HOLD/flagged=False. A check that could not run is not
    a check that passed - this must give action None and flagged True."""

    def test_every_fetch_failing_gives_no_action_and_flagged_true(self):
        holding = make_holding()
        write_ledger(holding, status="STABLE", last_evaluated="2026-02-10T00:00:00Z")
        failing = {
            "fetch_news_since": lambda holding: (None, "MFN unreachable"),
            "fetch_short_interest": lambda holding, timeout=90: (None, "FI register unreachable"),
            "fetch_insider_activity": lambda holding, months=6, timeout=90: (
                None, "FI register unreachable"),
            "fetch_valuation_gate": lambda holding, timeout=90: (
                None, "valuation_gate did not return valid JSON"),
        }
        for name, fn in failing.items():
            patcher = mock.patch.object(pr, name, fn)
            patcher.start()
            self.addCleanup(patcher.stop)
        # No live price either, so price_range also cannot check.
        record = pr.build_holding_record(holding, "all", price_info=None)
        self.assertIsNone(record["action"],
                           "zero of five checks ran - no action may be decided: %r" % record)
        self.assertTrue(record["flagged"], "an all-unchecked holding must be flagged, not a "
                         "silent clean pass")
        checked = {a["code"]: a["checked"] for a in record["layer2"]["alerts"]}
        self.assertFalse(any(checked.values()), "every alert should honestly report "
                          "checked=False: %r" % checked)
        self.assertIn("could not check", record["reason"])


class WarningThesisIsNotShadowedByATrimPriceSignal(unittest.TestCase):
    def test_warning_thesis_with_price_above_range_does_not_return_trim(self):
        view1 = {"breaker_fired": False, "has_ledger_entry": True,
                 "theses": [{"id": "T1"}], "warning": True,
                 "unknown_or_unevaluated": False, "last_reviewed": "2026-01-01T00:00:00Z"}
        alerts = [pr._alert("price_range", True, True, "price above range",
                             {"side": "above"})]
        action, reason = pr.decide_action(view1, alerts)
        self.assertNotEqual(action, "TRIM",
                             "a WARNING thesis must not be shadowed by the price signal")
        self.assertIsNone(action)
        self.assertIn("WARNING", reason)


class UnresolvedIdentityGetsTheIdentityDiagnosis(unittest.TestCase):
    def test_resolved_false_holding_gets_identity_diagnosis_not_run_initial_analysis(self):
        # portfolio_store writes "resolved": false when a holding's name
        # matched no listed issuer - it has no LEI/ISIN and never will until
        # it is re-added. "run an initial analysis" is the wrong fix for a
        # holding that has no identity to analyse.
        holding = make_holding(lei=None, isin=None, resolved=False)
        record = pr.build_holding_record(holding, "all", price_info=None)
        self.assertTrue(record["identity_unresolved"])
        self.assertIsNone(record["action"])
        self.assertIn("never resolved to a listed issuer", record["reason"])
        self.assertNotIn("run an initial analysis", record["reason"])
        self.assertTrue(record["flagged"])


class ExitCodeReflectsLayer1EvenWhenLaterLayersDoNotRun(_TempLedgerCase):
    """run() is what the CLI actually calls - nothing in the suite pinned its
    exit codes or --json shape before. A BROKEN thesis must give exit 4 at
    --layer 1 and --layer 2, not only at --layer all, since
    build_holding_record only sets `action` at "all"."""

    def setUp(self):
        super().setUp()
        self.store_tmp = tempfile.mkdtemp(prefix="portfolio-store-test-")
        self._old_store_home = os.environ.get("PORTFOLIO_STORE_HOME")
        os.environ["PORTFOLIO_STORE_HOME"] = self.store_tmp

    def tearDown(self):
        if self._old_store_home is None:
            os.environ.pop("PORTFOLIO_STORE_HOME", None)
        else:
            os.environ["PORTFOLIO_STORE_HOME"] = self._old_store_home
        shutil.rmtree(self.store_tmp, ignore_errors=True)
        super().tearDown()

    def _save_portfolio(self, holdings):
        doc = pr.portfolio_store._new_doc("test")
        doc["holdings"] = holdings
        pr.portfolio_store.save(doc, "test")

    def test_exit_code_4_at_layer_1_with_a_broken_thesis_on_disk(self):
        holding = make_holding()
        write_ledger(holding, status="BROKEN",
                     triggered=["ebit_margin < 15% for 2 consecutive years"])
        self._save_portfolio([holding])
        with contextlib.redirect_stdout(io.StringIO()):
            code = pr.run("test", 1, as_json=True, fetch_prices=False)
        self.assertEqual(code, 4, "a BROKEN thesis must give exit 4 at --layer 1 too")

    def test_exit_code_4_at_layer_2_with_a_broken_thesis_on_disk(self):
        holding = make_holding()
        write_ledger(holding, status="BROKEN",
                     triggered=["ebit_margin < 15% for 2 consecutive years"])
        self._save_portfolio([holding])
        with contextlib.redirect_stdout(io.StringIO()):
            code = pr.run("test", 2, as_json=True, fetch_prices=False)
        self.assertEqual(code, 4, "a BROKEN thesis must give exit 4 at --layer 2 too")

    def test_exit_code_0_with_no_broken_thesis(self):
        holding = make_holding()
        write_ledger(holding, status="STABLE")
        self._save_portfolio([holding])
        with contextlib.redirect_stdout(io.StringIO()):
            code = pr.run("test", 1, as_json=True, fetch_prices=False)
        self.assertEqual(code, 0)

    def test_run_json_shape_at_layer_all(self):
        holding = make_holding()
        write_ledger(holding, status="STABLE", last_evaluated="2026-02-10T00:00:00Z")
        self._save_portfolio([holding])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = pr.run("test", "all", as_json=True, fetch_prices=False)
        self.assertEqual(code, 0)
        import json
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["holdings"]), 1)
        self.assertIn("performance", payload)


class SelfTestPasses(unittest.TestCase):
    def test_builtin_selftest_passes(self):
        self.assertEqual(pr._selftest(), 0)


if __name__ == "__main__":
    unittest.main()


class SiblingSystemExitDegrades(PortfolioReviewTestCase
                                if "PortfolioReviewTestCase" in dir() else unittest.TestCase):
    """A sibling script that exits must not kill the review.

    mfn_news, quote, esef_fundamentals and the FI readers all use SystemExit as
    their error convention. SystemExit does NOT inherit from Exception, so a
    bare `except Exception` around a sibling call silently misses it. A live
    HTTP 500 from MFN took down a whole portfolio run this way.
    """

    def test_systemexit_from_mfn_degrades_to_an_unchecked_alert(self):
        class Boom(object):
            def search(self, name):
                raise SystemExit("DATA NOT AVAILABLE: MFN unreachable (HTTP 500)")
        prev = pr.mfn_news
        pr.mfn_news = Boom()
        try:
            data, err = pr.fetch_news_since({"name": "Sandvik AB"})
            self.assertIsNone(data)
            self.assertTrue(err)
            alert = pr.alert_new_report({"name": "Sandvik AB"}, "2026-05-12")
        finally:
            pr.mfn_news = prev
        self.assertFalse(alert["checked"],
                         "a check that could not run must not read as checked")
        self.assertFalse(alert["fired"])
