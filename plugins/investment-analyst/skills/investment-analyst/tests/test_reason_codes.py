#!/usr/bin/env python3
"""Reason codes: the machine-readable half of every withheld number.

WHY THIS FILE EXISTS

v2.6 established that the valuation-integrity rules exist twice, in two
different modes, and that BOTH of them reported only in prose:

  * valuation_gate.py is a HARD gate - eight checks, all-or-nothing, and on
    failure it prints the state and the reason INSTEAD of a number. Its own
    docstring records the live outcomes: Sandvik, Evolution, AB Volvo and
    KebNi FAIL, only Assa Abloy PASSES.
  * peers_se.py runs a parallel WARN/SUPPRESS mode inline in the peer table -
    an unknown reporting currency is fatal to the row (the defect that printed
    Betsson at P/E 63.0x against a true 5.7x and Orron at EV/Sales 86.6x
    against roughly 7.8x), stale fundamentals warn, and net debt plus every EV
    multiple is suppressed when cash is untagged.

Neither mode is wrong - hard-gating the peer table would blank P/E, EV/EBIT
and EV/Sales for nearly every Nordic issuer, because nearly every one of them
fails at least one of the eight checks. What was wrong is that a refusal was
only ever a paragraph of English, so nothing downstream could record WHICH
check refused the number. A decision made after a blocked multiple could not
say what it had been denied.

Both files now emit decision_record.REASON_CODES entries alongside prose that
is unchanged. THE LOAD-BEARING TEST IN THIS FILE is
EveryEmittedCodeIsInTheVocabulary: decision_record.validate() raises on a code
that is not in its dict, precisely so an invented code cannot become an
uncountable string in a stored record - which means a code invented at a call
site here would make the gate block a number AND block the recording of why.

Everything below is offline and synthetic. The one live test is marked
@helpers.network.
"""
import copy
import datetime
import unittest

import helpers

helpers.bootstrap_path()

vg = helpers.load("valuation_gate")
finfact = helpers.load("finfact")
peers = helpers.load("peers_se")
decision_record = helpers.try_load("decision_record")

TODAY = datetime.date(2026, 8, 31)
Verification = finfact.Verification


# --------------------------------------------------------------------------
# Synthetic facts. Every one carries provenance, because gate_detail()
# refuses a bare float before it runs any check - that is its own pre-check
# and deliberately has no reason code (see CHECK_REASON_CODES' note).
# --------------------------------------------------------------------------

def price_fact(period_end=None, publication=None, currency="SEK"):
    period_end = period_end or TODAY.isoformat()
    return finfact.FinancialFact(
        "price", 250.0, "yahoo", period_end, currency=currency,
        publication_date=publication or period_end, freshness_key="price")


def earnings_fact(period_end="2026-06-30", publication="2026-07-20",
                  currency="SEK", verification=None):
    kw = {}
    if verification is not None:
        kw["verification"] = verification
    return finfact.FinancialFact(
        "net_income", 100.0, "esef", period_end, currency=currency,
        publication_date=publication, freshness_key="interim_financials", **kw)


def shares_fact(note="diluted_weighted_average", verification=None):
    return finfact.FinancialFact(
        "shares_outstanding", 1000000.0, "nasdaq_reference", TODAY.isoformat(),
        publication_date=TODAY.isoformat(), note=note,
        verification=verification or Verification.VERIFIED)


def share_disclosure(period_end):
    """A Nasdaq CNS share-count disclosure, as check 6 consumes them."""
    return finfact.FinancialFact(
        "shares_outstanding", 1200000.0, "nasdaq_cns", period_end,
        publication_date=period_end)


def codes_for(**context):
    """(results, {code: entry}) for one gate run.

    Defaults are a clean case, so each test below perturbs exactly one input
    and the code it asserts on is the only thing that changed.
    """
    price = context.pop("price", None) or price_fact()
    earnings = context.pop("earnings", "default")
    if earnings == "default":
        earnings = earnings_fact()
    shares = context.pop("shares", "default")
    if shares == "default":
        shares = shares_fact()
    context.setdefault("as_of", TODAY)
    passed, _states, report, results = vg.gate_detail(
        price, earnings, shares, **context)
    by_code = {c["code"]: c for c in vg.gate_reason_codes(results)}
    return passed, report, results, by_code


# ==========================================================================
# 1. Each of the eight checks emits its own code, at the right severity
# ==========================================================================

class EachGateCheckEmitsItsOwnCode(unittest.TestCase):
    """One check, one code, and the severity says whether the number stands.

    FAIL becomes BLOCK (the number cannot be printed as stated), WARN becomes
    WARN (it stands, and the reader must be told). A PASS is not a reason for
    anything and must emit nothing at all - otherwise a count of reason codes
    would measure how many checks ran rather than how many objected.
    """

    def test_the_mapping_covers_exactly_the_eight_checks(self):
        self.assertEqual(len(vg.CHECK_REASON_CODES), 8)
        self.assertEqual(sorted(vg.CHECK_REASON_CODES), sorted([
            "corporate_actions", "currency", "period_lag", "price_timestamp",
            "publication_date", "restatement", "share_count",
            "ttm_completeness"]))

    def test_the_provenance_pre_check_is_deliberately_unmapped(self):
        """input_types is not one of the eight: it fires when the CALLER
        passed a bare float instead of a fact. Filing a programming error
        under the nearest-looking GATE_* code would record it as a
        data-quality finding about the issuer, and minting a new code at a
        call site is what the closed vocabulary exists to prevent."""
        self.assertNotIn("input_types", vg.CHECK_REASON_CODES)
        passed, _states, report, results = vg.gate_detail(
            price_fact(), 42.0, shares_fact(), as_of=TODAY)
        self.assertFalse(passed)
        self.assertEqual([r["check"] for r in results], ["input_types"])
        self.assertEqual(vg.gate_reason_codes(results), [])
        self.assertIn("VALUATION INTEGRITY: FAILED", report)

    # -- check 1 ---------------------------------------------------------
    def test_price_stale_blocks(self):
        old = (TODAY - datetime.timedelta(days=10)).isoformat()
        _p, _r, _res, by_code = codes_for(price=price_fact(period_end=old))
        self.assertEqual(by_code["GATE_PRICE_STALE"]["severity"], "BLOCK")
        self.assertIn("10 days old", by_code["GATE_PRICE_STALE"]["detail"])

    def test_a_weekend_old_price_warns_rather_than_blocking(self):
        """FRESHNESS_DAYS['price'] is 1 day and the hard limit is 4, so a
        Friday close read on a Monday is a WARN. A gate that blocked every
        Monday would be turned off within a week."""
        two_days = (TODAY - datetime.timedelta(days=2)).isoformat()
        _p, _r, _res, by_code = codes_for(price=price_fact(period_end=two_days))
        self.assertEqual(by_code["GATE_PRICE_STALE"]["severity"], "WARN")

    def test_a_price_inside_the_freshness_limit_emits_nothing(self):
        _p, _r, _res, by_code = codes_for()
        self.assertNotIn("GATE_PRICE_STALE", by_code)

    # -- check 2 ---------------------------------------------------------
    def test_the_sandvik_shape_blocks_on_the_period_code(self):
        """The motivating case: FY2024-12-31 earnings under a live price, 608
        days apart, against a 135-day limit."""
        _p, _r, _res, by_code = codes_for(
            earnings=earnings_fact("2024-12-31", "2025-02-20"),
            metric_name="P/E")
        self.assertEqual(by_code["GATE_PERIOD_INCOMPATIBLE"]["severity"], "BLOCK")
        self.assertIn("608 days",
                      by_code["GATE_PERIOD_INCOMPATIBLE"]["detail"])

    def test_earnings_dated_after_the_price_also_block(self):
        _p, _r, _res, by_code = codes_for(
            earnings=earnings_fact("2026-12-31", "2026-08-01"))
        self.assertEqual(by_code["GATE_PERIOD_INCOMPATIBLE"]["severity"], "BLOCK")

    def test_approaching_the_limit_warns(self):
        """0.7 x 135 = 94.5 days. 2026-05-15 is 108 days before the price
        date: usable, but a fresher interim probably already exists."""
        _p, _r, _res, by_code = codes_for(
            earnings=earnings_fact("2026-05-15", "2026-06-01"))
        self.assertEqual(by_code["GATE_PERIOD_INCOMPATIBLE"]["severity"], "WARN")

    # -- check 3 ---------------------------------------------------------
    def test_a_figure_with_no_publication_date_blocks(self):
        _p, _r, _res, by_code = codes_for(
            earnings=earnings_fact("2026-06-30", publication=None))
        self.assertEqual(by_code["GATE_PUBLICATION_UNKNOWN"]["severity"], "BLOCK")

    def test_a_figure_published_after_the_as_of_date_blocks(self):
        """Hindsight, not analysis - and it is the same code, because the
        defect is the same one: the fact cannot be placed in time relative to
        the decision."""
        _p, _r, _res, by_code = codes_for(
            earnings=earnings_fact("2026-06-30", "2026-09-15"))
        self.assertEqual(by_code["GATE_PUBLICATION_UNKNOWN"]["severity"], "BLOCK")

    # -- check 4 ---------------------------------------------------------
    def test_a_missing_share_count_blocks(self):
        _p, _r, _res, by_code = codes_for(shares=None)
        self.assertEqual(by_code["GATE_SHARE_COUNT_UNCERTAIN"]["severity"],
                         "BLOCK")

    def test_a_registered_count_under_a_per_share_metric_blocks(self):
        """Registered shares include treasury: the right basis for market cap
        and the wrong one for an EPS-style multiple."""
        _p, _r, _res, by_code = codes_for(
            shares=shares_fact(note="listed-registered"))
        self.assertEqual(by_code["GATE_SHARE_COUNT_UNCERTAIN"]["severity"],
                         "BLOCK")

    def test_an_undeclared_share_semantic_warns(self):
        _p, _r, _res, by_code = codes_for(shares=shares_fact(note=None))
        self.assertEqual(by_code["GATE_SHARE_COUNT_UNCERTAIN"]["severity"],
                         "WARN")

    def test_a_single_sourced_share_count_warns(self):
        _p, _r, _res, by_code = codes_for(
            shares=shares_fact(verification=Verification.SINGLE_SOURCE))
        self.assertEqual(by_code["GATE_SHARE_COUNT_UNCERTAIN"]["severity"],
                         "WARN")

    # -- check 5 ---------------------------------------------------------
    def test_a_currency_mismatch_blocks(self):
        """Evolution's shape: SEK price, EUR books. A multiple mixing them is
        wrong by the whole FX rate."""
        _p, _r, _res, by_code = codes_for(
            earnings=earnings_fact(currency="EUR"))
        self.assertEqual(by_code["GATE_CURRENCY_MISMATCH"]["severity"], "BLOCK")

    def test_a_supplied_fx_rate_downgrades_it_to_a_warning(self):
        """Downgraded, never silenced: the caller owns that rate's own
        timestamp and the reader has to be told a conversion happened."""
        _p, _r, _res, by_code = codes_for(
            earnings=earnings_fact(currency="EUR"), fx_rate=11.3)
        self.assertEqual(by_code["GATE_CURRENCY_MISMATCH"]["severity"], "WARN")

    def test_an_unlabelled_currency_blocks(self):
        _p, _r, _res, by_code = codes_for(
            earnings=earnings_fact(currency=None))
        self.assertEqual(by_code["GATE_CURRENCY_MISMATCH"]["severity"], "BLOCK")

    # -- check 6 ---------------------------------------------------------
    def test_a_share_disclosure_inside_the_gap_blocks(self):
        _p, _r, _res, by_code = codes_for(
            corporate_action_facts=[share_disclosure("2026-07-15")])
        self.assertEqual(by_code["GATE_CORPORATE_ACTION"]["severity"], "BLOCK")

    def test_an_unchecked_disclosure_log_warns(self):
        """None means "this source was not consulted", which is not the same
        claim as "nothing happened" and must not be reported as one."""
        _p, _r, _res, by_code = codes_for(corporate_action_facts=None)
        self.assertEqual(by_code["GATE_CORPORATE_ACTION"]["severity"], "WARN")

    def test_a_disclosure_outside_the_gap_emits_nothing(self):
        _p, _r, _res, by_code = codes_for(
            corporate_action_facts=[share_disclosure("2024-01-31")])
        self.assertNotIn("GATE_CORPORATE_ACTION", by_code)

    # -- check 7 ---------------------------------------------------------
    def test_a_ttm_claim_with_no_constituent_quarters_blocks(self):
        _p, _r, _res, by_code = codes_for(is_ttm=True)
        self.assertEqual(by_code["GATE_TTM_INCOMPLETE"]["severity"], "BLOCK")

    def test_three_quarters_are_not_a_ttm(self):
        quarters = [earnings_fact(p, p) for p in
                    ("2025-09-30", "2025-12-31", "2026-03-31")]
        _p, _r, _res, by_code = codes_for(is_ttm=True, ttm_quarters=quarters)
        self.assertEqual(by_code["GATE_TTM_INCOMPLETE"]["severity"], "BLOCK")

    def test_a_skipped_quarter_blocks(self):
        """Four quarters, but one of the gaps is half a year - a quarter is
        missing or duplicated, which four period ends alone would hide."""
        quarters = [earnings_fact(p, p) for p in
                    ("2025-06-30", "2025-09-30", "2026-03-31", "2026-06-30")]
        _p, _r, _res, by_code = codes_for(is_ttm=True, ttm_quarters=quarters)
        self.assertEqual(by_code["GATE_TTM_INCOMPLETE"]["severity"], "BLOCK")

    def test_four_contiguous_quarters_emit_nothing(self):
        quarters = [earnings_fact(p, p) for p in
                    ("2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30")]
        _p, _r, _res, by_code = codes_for(is_ttm=True, ttm_quarters=quarters)
        self.assertNotIn("GATE_TTM_INCOMPLETE", by_code)

    def test_a_single_period_figure_warns_that_it_is_not_a_ttm(self):
        """No completeness claim is being made - but the figure must not be
        relabelled TTM downstream, so the warning is recorded rather than
        dropped."""
        _p, _r, _res, by_code = codes_for()
        self.assertEqual(by_code["GATE_TTM_INCOMPLETE"]["severity"], "WARN")

    # -- check 8 ---------------------------------------------------------
    def test_a_conflicted_figure_blocks_as_a_restatement(self):
        _p, _r, _res, by_code = codes_for(
            earnings=earnings_fact(verification=Verification.CONFLICT))
        self.assertEqual(by_code["GATE_RESTATEMENT_SUPERSEDED"]["severity"],
                         "BLOCK")

    def test_a_known_restatement_warns(self):
        _p, _r, _res, by_code = codes_for(restated=True,
                                          restatement_detail="EBIT +2.1%")
        self.assertEqual(by_code["GATE_RESTATEMENT_SUPERSEDED"]["severity"],
                         "WARN")

    def test_an_unchecked_restatement_status_warns(self):
        _p, _r, _res, by_code = codes_for()
        self.assertEqual(by_code["GATE_RESTATEMENT_SUPERSEDED"]["severity"],
                         "WARN")

    def test_a_checked_and_clean_restatement_emits_nothing(self):
        _p, _r, _res, by_code = codes_for(restated=False)
        self.assertNotIn("GATE_RESTATEMENT_SUPERSEDED", by_code)


class ReasonCodeShape(unittest.TestCase):
    """The entries have to be storable as they are, without a translation
    layer at the call site - a translation layer is where an invented code
    would be born."""

    def test_every_entry_has_code_severity_and_detail(self):
        _p, _r, results, _c = codes_for(
            earnings=earnings_fact("2024-12-31", "2025-02-20"))
        entries = vg.gate_reason_codes(results)
        self.assertTrue(entries)
        for e in entries:
            self.assertEqual(sorted(e), ["code", "detail", "severity"])
            self.assertIn(e["severity"], ("BLOCK", "WARN"))
            self.assertTrue(e["detail"])

    def test_details_are_one_short_sentence(self):
        """The full paragraph stays in `checks`; a code's detail is read next
        to a dozen others inside a stored record."""
        _p, _r, results, _c = codes_for(
            earnings=earnings_fact("2024-12-31", "2025-02-20"),
            shares=None, corporate_action_facts=None)
        for e in vg.gate_reason_codes(results):
            self.assertLessEqual(len(e["detail"]), 180)
            self.assertNotIn("\n", e["detail"])

    def test_passing_checks_contribute_nothing(self):
        _p, _r, results, _c = codes_for(
            restated=False,
            corporate_action_facts=[share_disclosure("2024-01-31")])
        objected = [r for r in results if r["status"] != "PASS"]
        self.assertEqual(len(vg.gate_reason_codes(results)), len(objected))

    def test_the_code_travels_on_each_check_result(self):
        """Carried ON the result rather than re-derived by every consumer, so
        the mapping cannot drift from the check that produced it."""
        _p, _r, results, _c = codes_for()
        for r in results:
            self.assertEqual(sorted(r), ["check", "detail", "reason_code",
                                         "state", "status"])
            self.assertEqual(r["reason_code"],
                             vg.CHECK_REASON_CODES.get(r["check"]))

    def test_block_wins_over_warn_for_the_same_code(self):
        """A caller counting BLOCKs must never be told that a blocking check
        merely warned."""
        results = [
            {"check": "currency", "status": "WARN", "state": None,
             "detail": "warned first", "reason_code": "GATE_CURRENCY_MISMATCH"},
            {"check": "currency", "status": "FAIL", "state": None,
             "detail": "then blocked", "reason_code": "GATE_CURRENCY_MISMATCH"},
        ]
        entries = vg.gate_reason_codes(results)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["severity"], "BLOCK")


# ==========================================================================
# 2. THE LOAD-BEARING TEST: no code may be invented
# ==========================================================================

@unittest.skipIf(decision_record is None,
                 "decision_record.py not found under scripts/ - it owns the "
                 "controlled vocabulary these codes must belong to")
class EveryEmittedCodeIsInTheVocabulary(unittest.TestCase):
    """decision_record.validate() REFUSES a record carrying a code that is
    not in REASON_CODES, on purpose: a free-text reason cannot be counted
    later. So a code invented in valuation_gate.py or peers_se.py would make
    the toolkit block a number and then block the recording of why.

    This is the test that catches that, and it is why it asserts over the
    mapping tables rather than over a sample of runs.
    """

    def test_every_gate_code_exists(self):
        for check, code in sorted(vg.CHECK_REASON_CODES.items()):
            self.assertIn(code, decision_record.REASON_CODES,
                          "valuation_gate check %r emits %r, which "
                          "decision_record.validate() would refuse" % (check, code))

    def test_every_peer_code_exists(self):
        for code in peers.PEER_REASON_CODES:
            self.assertIn(code, decision_record.REASON_CODES,
                          "peers_se emits %r, which "
                          "decision_record.validate() would refuse" % code)

    def test_the_severities_are_the_ones_decision_record_accepts(self):
        for severity in vg.STATUS_SEVERITY.values():
            self.assertIn(severity, decision_record.SEVERITIES)

    def test_all_eleven_codes_are_accounted_for(self):
        """The eight gate checks plus peers_se's three suppressions. If this
        count changes, a check was added or removed without its code."""
        emitted = set(vg.CHECK_REASON_CODES.values()) | set(peers.PEER_REASON_CODES)
        self.assertEqual(len(emitted), 11)

    def test_a_record_carrying_real_emitted_codes_validates(self):
        """End to end: codes that came out of an actual gate run, dropped into
        a decision record and put through the validator. This is the check
        that would have caught a plausible-looking but absent code."""
        _p, _r, results, _c = codes_for(
            earnings=earnings_fact("2024-12-31", "2025-02-20"),
            shares=shares_fact(note=None), corporate_action_facts=None)
        emitted = vg.gate_reason_codes(results)
        self.assertTrue(emitted)
        rec = {
            "as_of": "2026-08-31", "depth": "SCREEN", "producer": "screen",
            "identity": {"name": "Sandvik AB", "isin": "SE0000667891"},
            "verdict": "HOLD", "conviction": "LOW",
            "price": {"value": 356.0, "currency": "SEK",
                      "as_of": "2026-08-31 07:14 UTC"},
            "reason_codes": copy.deepcopy(emitted),
        }
        out, _warnings = decision_record.validate(rec)
        stored = {c["code"] for c in out["reason_codes"]}
        for e in emitted:
            self.assertIn(e["code"], stored)

    def test_peer_codes_survive_the_validator_too(self):
        m = peers.multiples({
            "cap_live": 1000.0, "ccy": "SEK",
            "fin": {"fy_end": "2024-12-31", "currency": "SEK",
                    "revenue": 500.0, "ebit": 50.0, "net_income": 40.0,
                    "net_debt": None,
                    "net_debt_note": "cash and cash equivalents not tagged "
                                     "in ESEF"}})
        emitted = peers.issuer_reason_codes({"fin": {"fy_end": "2024-12-31"}},
                                            m, TODAY)
        self.assertTrue(emitted)
        rec = {
            "as_of": "2026-08-31", "depth": "COMPARE", "producer": "compare",
            "identity": {"lei": "213800Y2XLTQMHLB5J34"},
            "verdict": "HOLD", "conviction": "LOW",
            "price": {"value": 356.0, "currency": "SEK",
                      "as_of": "2026-08-31 07:14 UTC"},
            "reason_codes": copy.deepcopy(emitted),
        }
        out, _warnings = decision_record.validate(rec)
        stored = {c["code"] for c in out["reason_codes"]}
        self.assertIn("PEER_FUNDAMENTALS_STALE", stored)
        self.assertIn("PEER_NET_DEBT_UNTAGGED", stored)


# ==========================================================================
# 3. The human output is unchanged
# ==========================================================================

class TheTextReportIsUnchanged(unittest.TestCase):
    """SKILL.md, references/verification.md and other scripts are written
    against the exact text this gate prints. The reason codes are an ADDITIONAL
    channel, so the report has to be byte-identical - which means asserting on
    the whole string, not on a substring of it.

    The expected value below is built from the module's own _wrap() over the
    details the checks produced, plus every literal line of the skeleton
    written out by hand. If a code, a status letter or a stray field leaked
    into the prose, the assembled string would not match.
    """

    def setUp(self):
        self.passed, self.report, self.results, self.by_code = codes_for(
            earnings=earnings_fact("2024-12-31", "2025-02-20"),
            metric_name="P/E")
        self.detail = {r["check"]: r["detail"] for r in self.results}

    def test_the_case_is_the_documented_one(self):
        self.assertFalse(self.passed)
        self.assertEqual(
            [r["check"] for r in self.results],
            ["price_timestamp", "period_lag", "publication_date",
             "ttm_completeness", "restatement", "share_count", "currency",
             "corporate_actions"])
        self.assertEqual(
            [r["status"] for r in self.results],
            ["PASS", "FAIL", "PASS", "WARN", "WARN", "PASS", "PASS", "WARN"])

    def test_the_period_lag_prose_is_verbatim_what_it_always_was(self):
        self.assertEqual(
            self.detail["period_lag"],
            "P/E is based on financial data ending 2024-12-31, 608 days "
            "before the price date. Roll forward with the interim report "
            "before using this multiple.")

    def test_the_whole_report_is_byte_identical(self):
        expected = "\n".join([
            "VALUATION INTEGRITY: FAILED",
            "Company: ?   Metric: P/E",
            vg._wrap("Reason:", self.detail["period_lag"]),
            "",
            "Warnings (do not block a PASS, but read before trusting the "
            "number):",
            vg._wrap("  -", "[ttm_completeness] %s"
                     % self.detail["ttm_completeness"]),
            vg._wrap("  -", "[restatement] %s" % self.detail["restatement"]),
            vg._wrap("  -", "[corporate_actions] %s"
                     % self.detail["corporate_actions"]),
        ])
        self.assertEqual(self.report, expected)

    def test_no_reason_code_leaks_into_the_prose(self):
        self.assertNotIn("GATE_", self.report)
        self.assertNotIn("BLOCK", self.report)
        self.assertNotIn("reason_code", self.report)

    def test_a_clean_case_still_says_no_warnings(self):
        """The positive control for the format: a gate that printed a warnings
        block on a clean case, or dropped the "No warnings." line, would be a
        text change even though no wording moved."""
        passed, report, _results, _c = codes_for(
            metric_name="P/E",
            restated=False,
            corporate_action_facts=[share_disclosure("2024-01-31")],
            is_ttm=True,
            ttm_quarters=[earnings_fact(p, p) for p in
                          ("2025-09-30", "2025-12-31", "2026-03-31",
                           "2026-06-30")])
        self.assertTrue(passed, report)
        self.assertEqual(report, "\n".join([
            "VALUATION INTEGRITY: PASSED",
            "Company: ?   Metric: P/E",
            "No warnings."]))


# ==========================================================================
# 4. peers_se: the three suppressions, in warn/suppress mode
# ==========================================================================

def peer_fin(**kw):
    """A derive()-shaped fundamentals dict, clean unless perturbed."""
    fin = {"fy_end": "2026-06-30", "currency": "SEK", "revenue": 500.0,
           "ebit": 50.0, "net_income": 40.0, "net_debt": 100.0,
           "net_debt_note": "leases included", "minority_interest": None,
           "fcf": 30.0}
    fin.update(kw)
    return fin


def peer_entry(**fin_kw):
    return {"cap_live": 1000.0, "ccy": "SEK", "cap_basis": "test",
            "fin": peer_fin(**fin_kw)}


class PeerSuppressionsEmitCodes(unittest.TestCase):
    """The peer table stays in warn/suppress mode - it is NOT routed through
    the hard gate, because nearly every Nordic issuer fails at least one of
    the eight checks and a hard gate there would blank P/E, EV/EBIT and
    EV/Sales for almost every row. What changed is only that each suppression
    now also says, in a countable form, which one it was.
    """

    def test_a_clean_row_emits_nothing_and_prints_numbers(self):
        m = peers.multiples(peer_entry())
        self.assertEqual(m["reason_codes"], [])
        self.assertIsNone(m.get("error"))
        self.assertAlmostEqual(m["ev_ebit"], 22.0)
        self.assertAlmostEqual(m["pe"], 25.0)

    # -- PEER_CURRENCY_UNKNOWN -------------------------------------------
    def test_an_unknown_reporting_currency_is_still_fatal_to_the_row(self):
        """The Betsson/Orron defect. The old code tested `if pccy and rccy and
        pccy != rccy`, so an unknown reporting currency fell straight through
        to the divisions with no conversion and no error - P/E 63.0x against a
        true 5.7x, EV/Sales 86.6x against roughly 7.8x. Fatal is the point;
        the code is additional to it, never instead of it."""
        m = peers.multiples(peer_entry(currency=None,
                                       currencies_latest_fy=["SEK", "EUR"]))
        self.assertTrue(m["error"].startswith("DATA NOT AVAILABLE"))
        for field in ("pe", "ev_ebit", "ev_sales", "fcf_yield", "ev"):
            self.assertIsNone(m.get(field),
                              "%s leaked past an unknown reporting currency"
                              % field)
        self.assertEqual([(c["code"], c["severity"]) for c in m["reason_codes"]],
                         [("PEER_CURRENCY_UNKNOWN", "BLOCK")])

    def test_an_unknown_quote_currency_is_also_fatal_and_coded(self):
        entry = peer_entry()
        entry["ccy"] = None
        m = peers.multiples(entry)
        self.assertTrue(m["error"].startswith("DATA NOT AVAILABLE"))
        self.assertIsNone(m.get("pe"))
        self.assertEqual([c["code"] for c in m["reason_codes"]],
                         ["PEER_CURRENCY_UNKNOWN"])

    def test_missing_fundamentals_is_an_absence_not_a_suppression(self):
        """No code, deliberately: nothing was withheld that could otherwise
        have been formed, and REASON_CODES has no entry for "there was no
        filing"."""
        m = peers.multiples({"cap_live": 1000.0, "ccy": "SEK", "fin": None})
        self.assertIn("DATA NOT AVAILABLE", m["error"])
        self.assertEqual(m["reason_codes"], [])

    # -- PEER_NET_DEBT_UNTAGGED ------------------------------------------
    def test_untagged_cash_blocks_the_ev_multiples(self):
        """An untagged cash balance is not zero. Treating it as zero
        overstated net debt and EV by the whole cash pile without saying so."""
        m = peers.multiples(peer_entry(
            net_debt=None, gross_debt=200.0,
            net_debt_note="cash and cash equivalents not tagged in ESEF; "
                          "gross debt is 200, but netting it needs a cash "
                          "figure this filing does not give"))
        self.assertEqual([(c["code"], c["severity"])
                          for c in m["reason_codes"]],
                         [("PEER_NET_DEBT_UNTAGGED", "BLOCK")])
        self.assertIsNone(m["ev"])
        self.assertIsNone(m["ev_ebit"])
        self.assertIsNone(m["ev_sales"])

    def test_pe_survives_an_untagged_net_debt(self):
        """P/E has no EV in it. Suppressing it too would be the mirror error -
        withholding a number that is not affected."""
        m = peers.multiples(peer_entry(net_debt=None, net_debt_note="x"))
        self.assertIsNotNone(m["pe"])

    def test_a_computed_but_caveated_net_debt_warns(self):
        """A balance sheet with long-term debt and no current portion is an
        ordinary state, so it is computed and flagged rather than suppressed -
        WARN, not BLOCK."""
        m = peers.multiples(peer_entry(
            net_debt_note="no current borrowings line tagged - normal for a "
                          "filer with nothing due within a year"))
        self.assertEqual([(c["code"], c["severity"])
                          for c in m["reason_codes"]],
                         [("PEER_NET_DEBT_UNTAGGED", "WARN")])
        self.assertIsNotNone(m["ev_ebit"])

    def test_the_routine_notes_are_not_caveats(self):
        """One function, used by both the WARN code and the report's own
        "NET DEBT COMPUTED BUT WITH A CAVEAT" section, so the two cannot
        drift apart - they were the same condition written twice."""
        self.assertFalse(peers.net_debt_note_is_caveat("leases included"))
        self.assertFalse(peers.net_debt_note_is_caveat(
            "no separately tagged lease liability - IFRS 16 debt may be "
            "inside borrowings"))
        self.assertFalse(peers.net_debt_note_is_caveat(None))
        self.assertFalse(peers.net_debt_note_is_caveat(""))
        self.assertTrue(peers.net_debt_note_is_caveat(
            "only the CURRENT borrowings leg is tagged in ESEF"))

    # -- PEER_FUNDAMENTALS_STALE -----------------------------------------
    def test_a_stale_annual_warns_on_the_row(self):
        codes = peers.issuer_reason_codes({"fin": {"fy_end": "2024-12-31"}},
                                          None, TODAY)
        self.assertEqual([(c["code"], c["severity"]) for c in codes],
                         [("PEER_FUNDAMENTALS_STALE", "WARN")])
        self.assertIn("608 days", codes[0]["detail"])

    def test_a_fresh_annual_emits_nothing(self):
        self.assertEqual(
            peers.issuer_reason_codes({"fin": {"fy_end": "2026-06-30"}},
                                      None, TODAY), [])

    def test_staleness_is_reported_without_the_multiples_pass(self):
        """--multiples is what builds the multiples dict, so the currency and
        net-debt codes only exist on a run that asked for it. The staleness
        code is derived from the fundamentals instead, and must survive
        without it."""
        codes = peers.issuer_reason_codes({"fin": {"fy_end": "2024-12-31"}},
                                          None, TODAY)
        self.assertTrue(codes)

    def test_a_row_carries_its_own_codes_and_only_its_own(self):
        """Per issuer, so a decision about one company picks up exactly the
        codes that applied to IT."""
        stale_and_untagged = peers.issuer_reason_codes(
            {"fin": peer_fin(fy_end="2024-12-31", net_debt=None,
                             net_debt_note="cash not tagged")},
            peers.multiples(peer_entry(fy_end="2024-12-31", net_debt=None,
                                       net_debt_note="cash not tagged")),
            TODAY)
        self.assertEqual([c["code"] for c in stale_and_untagged],
                         ["PEER_FUNDAMENTALS_STALE", "PEER_NET_DEBT_UNTAGGED"])
        clean = peers.issuer_reason_codes({"fin": peer_fin()},
                                          peers.multiples(peer_entry()), TODAY)
        self.assertEqual(clean, [])


class TheStalenessThresholdHasOneHome(unittest.TestCase):
    """Both files answered "is this annual figure past its life" inline, with
    peers_se.py carrying its own hardcoded 460-day fallback.
    finfact.FRESHNESS_DAYS was already the common constant; the comparison is
    now shared too.
    """

    def test_peers_se_reaches_the_gates_helper(self):
        """Identity cannot be asserted here: every script in this toolkit
        loads its siblings through spec_from_file_location, so the copy
        peers_se imports and the copy this test file loaded are different
        module objects with the same name. What matters is that the delegation
        target EXISTS and that both paths return the same triple."""
        loaded = peers._valuation_gate()
        self.assertIsNotNone(loaded, "peers_se could not reach "
                                     "valuation_gate.py at all")
        self.assertTrue(hasattr(loaded, "annual_figure_past_life"))
        self.assertEqual(peers.annual_fundamentals_stale("2024-12-31", TODAY),
                         vg.annual_figure_past_life("2024-12-31", TODAY))

    def test_the_limit_is_finfacts_constant_not_a_local_literal(self):
        for fn in (peers.annual_fundamentals_stale, vg.annual_figure_past_life):
            self.assertEqual(fn("2024-12-31", TODAY)[2],
                             finfact.FRESHNESS_DAYS["annual_financials"])

    def test_an_undated_figure_never_claims_freshness(self):
        """None, not False. "I do not know" is not "it is fresh"."""
        for value in (None, "", "n/a"):
            self.assertIsNone(
                peers.annual_fundamentals_stale(value, TODAY)[0])
            self.assertIsNone(vg.annual_figure_past_life(value, TODAY)[0])

    def test_the_boundary_is_inclusive_of_the_limit(self):
        limit = finfact.FRESHNESS_DAYS["annual_financials"]
        at_limit = (TODAY - datetime.timedelta(days=limit)).isoformat()
        past = (TODAY - datetime.timedelta(days=limit + 1)).isoformat()
        self.assertFalse(vg.annual_figure_past_life(at_limit, TODAY)[0])
        self.assertTrue(vg.annual_figure_past_life(past, TODAY)[0])

    def test_the_gates_own_price_check_reads_the_same_helper(self):
        stale, age, limit = vg.past_life(
            (TODAY - datetime.timedelta(days=3)).isoformat(), "price", TODAY)
        self.assertTrue(stale)
        self.assertEqual((age, limit), (3, finfact.FRESHNESS_DAYS["price"]))


# ==========================================================================
# 5. The CAGR delegation must not move a single output key
# ==========================================================================

def revenue_pack(rows, ccy=None):
    """A merged-facts pack in the shape esef_facts() returns and series()
    reads. Revenue only: every other metric then reads an empty series, which
    is exactly what a filing tagging nothing else produces."""
    ccy = ccy or {}
    data = {"revenue": {p: {"v": v, "u": "iso4217:%s" % ccy.get(p, "SEK"),
                            "c": "Revenue"}
                        for p, v in rows.items()}}
    return {"lei": "TESTLEI0000000000000", "data": data, "filings": [],
            "currency": None,
            "currencies": sorted({ccy.get(p, "SEK") for p in rows})}


CAGR_KEYS = ("cagr", "cagr_years", "cagr_periods_dropped", "cagr_from",
             "cagr_to", "cagr_observations")


class CagrOutputKeysAreUnchanged(unittest.TestCase):
    """The arithmetic moved into finmath.cagr(); the KEYS did not. The text
    report at the bottom of peers_se.py and the model's own instructions read
    cagr, cagr_years, cagr_periods_dropped, cagr_from, cagr_to and
    cagr_observations by name, and cagr_periods_dropped's entries are unpacked
    as {"period", "unit"}.
    """

    def test_a_plain_three_year_series_writes_every_key(self):
        out = peers.derive(revenue_pack({"2021-12-31": 100.0,
                                          "2022-12-31": 118.0,
                                          "2023-12-31": 136.0,
                                          "2024-12-31": 155.0}))
        for key in CAGR_KEYS:
            self.assertIn(key, out, "%s disappeared from derive()" % key)
        self.assertEqual(out["cagr_from"], "2021-12-31")
        self.assertEqual(out["cagr_to"], "2024-12-31")
        self.assertEqual(out["cagr_years"], 3.0)
        self.assertEqual(out["cagr_observations"], 4)
        self.assertEqual(out["cagr_periods_dropped"], [])
        self.assertAlmostEqual(out["cagr"], 0.1572, places=3)

    def test_the_gapped_series_still_uses_elapsed_time_not_observations(self):
        """Defect 1: the exponent was 1/(observations - 1). Investor's merge
        holds 2021, 2022 and 2024 - three elapsed years, two gaps - and
        printed "2y CAGR +24.6%" for a true +15.9% over the actual period."""
        out = peers.derive(revenue_pack({"2021-12-31": 100.0,
                                          "2022-12-31": 120.0,
                                          "2024-12-31": 155.0}))
        self.assertEqual(out["cagr_years"], 3.0)
        self.assertEqual(out["cagr_observations"], 3)
        self.assertAlmostEqual(out["cagr"], 0.1572, places=3)
        self.assertLess(out["cagr"], 0.20, "the exponent shrank back to the "
                                           "observation count")

    def test_a_redenomination_still_drops_the_old_unit(self):
        """Defect 2: there was no unit check. Betsson's rev[2020] is in SEK and
        rev[2024] in EUR after the 2021 redenomination, and compounding one
        against the other printed "4y CAGR -35.5%" for a company growing
        around +16% a year."""
        out = peers.derive(revenue_pack(
            {"2020-12-31": 100.0, "2022-12-31": 12.0, "2024-12-31": 16.0},
            ccy={"2020-12-31": "SEK", "2022-12-31": "EUR",
                 "2024-12-31": "EUR"}))
        self.assertEqual(out["cagr_periods_dropped"],
                         [{"period": "2020-12-31", "unit": "iso4217:SEK"}])
        self.assertEqual(out["cagr_from"], "2022-12-31")
        self.assertEqual(out["cagr_years"], 2.0)
        self.assertEqual(out["cagr_observations"], 2)
        self.assertGreater(out["cagr"], 0.0, "the SEK year is back in the base")

    def test_a_single_year_writes_the_keys_as_none_without_a_from(self):
        """cagr_from is absent, not None, when no CAGR was struck - the text
        report gates on `if tf.get("cagr_from")`, so the two states have to
        stay distinguishable."""
        out = peers.derive(revenue_pack({"2024-12-31": 155.0}))
        self.assertIsNone(out["cagr"])
        self.assertIsNone(out["cagr_years"])
        self.assertEqual(out["cagr_periods_dropped"], [])
        self.assertNotIn("cagr_from", out)

    def test_a_span_under_the_floor_is_refused_not_annualised(self):
        out = peers.derive(revenue_pack({"2024-12-31": 100.0,
                                          "2025-04-30": 110.0}))
        self.assertIsNone(out["cagr"])
        self.assertNotIn("cagr_from", out)


class CagrDelegation(unittest.TestCase):
    """finmath.cagr() is the one implementation; the local port is the
    degradation path. Both must produce the same keys, and an unusable
    finmath must never reach the output.
    """

    def setUp(self):
        self._real = peers.finmath

    def tearDown(self):
        peers.finmath = self._real

    def test_it_degrades_to_the_local_port_when_finmath_is_absent(self):
        peers.finmath = None
        got = peers.revenue_cagr(100.0, 155.0, "2021-12-31", "2024-12-31")
        self.assertAlmostEqual(got["cagr"], 0.1572, places=3)
        self.assertEqual(got["years"], 3.0)

    def test_a_series_shaped_finmath_is_used(self):
        class Series(object):
            @staticmethod
            def cagr(values, units=None):
                return {"cagr": 0.5, "years": 4.0}
        peers.finmath = Series
        self.assertEqual(
            peers.revenue_cagr(100.0, 155.0, "2021-12-31", "2024-12-31"),
            {"cagr": 0.5, "years": 4.0})

    def test_the_landed_cagr_between_object_is_used(self):
        """finmath landed as cagr_between(v0, v1, period0, period1, ...) ->
        CagrResult, an OBJECT carrying .value/.years - not a dict, not a
        tuple, not a scalar.

        This is a regression test for a real integration defect: the
        adapter was written before finmath existed and recognised only
        dict/scalar/tuple returns, so the object came back, failed to
        normalise, and the local port silently ran instead. Delegation
        looked fine because both paths return *a* number.

        Two earlier tests here stubbed `cagr(v0, v1, frm, to)` and
        `cagr(v0, v1, years)` - shapes finmath was guessed to might take
        and never did. They tested adapter branches that are now dead, so
        they are replaced by this one and the refusal case below."""
        class Landed(object):
            @staticmethod
            def cagr_between(v0, v1, period0, period1, currency0=None,
                             currency1=None, min_years=None):
                class CagrResult(object):
                    value, years = 0.25, 3.0
                    reason = None
                    def __bool__(self):
                        return self.value is not None
                return CagrResult()
        peers.finmath = Landed
        got = peers.revenue_cagr(100.0, 155.0, "2021-12-31", "2024-12-31")
        self.assertEqual(got["cagr"], 0.25)
        self.assertEqual(got["years"], 3.0)

    def test_a_refused_cagr_result_falls_back_instead_of_reading_as_zero(self):
        """CagrResult(value=None) means finmath REFUSED - a currency change,
        too short a span. A refusal must reach the local port, never be read
        as a growth rate of nothing."""
        class Refusing(object):
            @staticmethod
            def cagr_between(v0, v1, period0, period1, currency0=None,
                             currency1=None, min_years=None):
                class CagrResult(object):
                    value, years = None, None
                    reason = "currency changed between the two periods"
                    def __bool__(self):
                        return self.value is not None
                return CagrResult()
        peers.finmath = Refusing
        got = peers.revenue_cagr(100.0, 155.0, "2021-12-31", "2024-12-31")
        self.assertAlmostEqual(got["cagr"], 0.1572, places=3)

    def test_a_finmath_that_raises_degrades_rather_than_crashing(self):
        class Angry(object):
            @staticmethod
            def cagr(*a, **kw):
                raise RuntimeError("mid-edit")
        peers.finmath = Angry
        got = peers.revenue_cagr(100.0, 155.0, "2021-12-31", "2024-12-31")
        self.assertAlmostEqual(got["cagr"], 0.1572, places=3)

    def test_a_finmath_that_calls_sys_exit_degrades_too(self):
        """A sibling that fails a precondition in its module body calls
        sys.exit(), and SystemExit is not an Exception."""
        class Exiting(object):
            @staticmethod
            def cagr(*a, **kw):
                raise SystemExit(2)
        peers.finmath = Exiting
        got = peers.revenue_cagr(100.0, 155.0, "2021-12-31", "2024-12-31")
        self.assertAlmostEqual(got["cagr"], 0.1572, places=3)

    def test_a_finmath_returning_junk_is_refused_not_printed(self):
        class Junk(object):
            @staticmethod
            def cagr(*a, **kw):
                return "not a number"
        peers.finmath = Junk
        got = peers.revenue_cagr(100.0, 155.0, "2021-12-31", "2024-12-31")
        self.assertAlmostEqual(got["cagr"], 0.1572, places=3)

    def test_a_finmath_returning_a_non_finite_rate_is_refused(self):
        class Inf(object):
            @staticmethod
            def cagr(*a, **kw):
                return float("inf")
        peers.finmath = Inf
        got = peers.revenue_cagr(100.0, 155.0, "2021-12-31", "2024-12-31")
        self.assertAlmostEqual(got["cagr"], 0.1572, places=3)

    def test_delegation_does_not_move_the_output_keys(self):
        class Series(object):
            @staticmethod
            def cagr(values, units=None):
                return {"cagr": 0.5, "years": 4.0}
        peers.finmath = Series
        out = peers.derive(revenue_pack({"2021-12-31": 100.0,
                                          "2024-12-31": 155.0}))
        for key in CAGR_KEYS:
            self.assertIn(key, out)
        self.assertEqual(out["cagr"], 0.5)
        self.assertEqual(out["cagr_years"], 4.0)
        self.assertEqual(out["cagr_from"], "2021-12-31")
        self.assertEqual(out["cagr_to"], "2024-12-31")
        self.assertEqual(out["cagr_observations"], 2)


# ==========================================================================
# 6. _soft_load must survive a sibling that exits
# ==========================================================================

class SoftLoadSurvivesSystemExit(unittest.TestCase):
    """portfolio_review.py:87 catches (Exception, SystemExit) with the comment
    "sibling scripts raise SystemExit, which is not an Exception".
    valuation_gate._soft_load caught only Exception, so a sibling that exited
    while its module body ran killed the gate outright instead of degrading it
    - in the one file whose entire contract is that a missing sibling degrades
    a check rather than crashing the run.
    """

    def setUp(self):
        self._real_load = vg._load

    def tearDown(self):
        vg._load = self._real_load

    def test_a_sibling_that_exits_returns_none(self):
        def exiting(name):
            raise SystemExit("nordic_shares: no venue configured")
        vg._load = exiting
        self.assertIsNone(vg._soft_load("nordic_shares"))

    def test_a_sibling_that_exits_with_a_code_returns_none(self):
        def exiting(name):
            raise SystemExit(2)
        vg._load = exiting
        self.assertIsNone(vg._soft_load("quote"))

    def test_an_ordinary_exception_still_returns_none(self):
        def broken(name):
            raise ImportError("no module named nope")
        vg._load = broken
        self.assertIsNone(vg._soft_load("nope"))

    def test_a_working_sibling_is_returned_unchanged(self):
        sentinel = object()
        vg._load = lambda name: sentinel
        self.assertIs(vg._soft_load("finfact"), sentinel)

    def test_keyboardinterrupt_is_still_allowed_through(self):
        """(Exception, SystemExit) and not BaseException: a Ctrl-C must still
        stop the run rather than being swallowed as "sibling unavailable"."""
        def interrupted(name):
            raise KeyboardInterrupt
        vg._load = interrupted
        with self.assertRaises(KeyboardInterrupt):
            vg._soft_load("quote")


# ==========================================================================
# 7. Live, opt-in
# ==========================================================================

class LiveGateStillRefusesSandvik(unittest.TestCase):
    """valuation_gate.py's docstring records the live outcome as of 2026-09-01:
    Sandvik FAILS, because its latest structured earnings are an ESEF annual
    roughly twenty months older than its price. Opt-in, because a network
    outage must read as "not applicable" rather than as a logic failure.
    """

    @helpers.network
    def test_sandvik_fails_and_says_which_check_refused_it(self):
        bundle, notes, _path = vg.gather("Sandvik")
        if bundle is None:
            self.skipTest("could not gather live data for Sandvik: %s"
                          % "; ".join(notes or ["no notes"]))
        ctx = dict(bundle["context"])
        ctx["as_of"] = datetime.date.today()
        passed, _states, report, results = vg.gate_detail(
            bundle["price_fact"], bundle["earnings_fact"],
            bundle["shares_fact"], **ctx)
        codes = vg.gate_reason_codes(results)
        self.assertIn("VALUATION INTEGRITY:", report)
        if passed:
            self.skipTest("Sandvik passed the gate today - a fresh interim "
                          "was rolled forward; nothing to assert about a "
                          "refusal:\n%s" % report)
        blocking = [c for c in codes if c["severity"] == "BLOCK"]
        self.assertTrue(blocking, "the gate FAILED but named no blocking "
                                  "reason code:\n%s" % report)
        if decision_record is not None:
            for c in codes:
                self.assertIn(c["code"], decision_record.REASON_CODES)


if __name__ == "__main__":
    unittest.main()
