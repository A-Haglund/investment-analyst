#!/usr/bin/env python3
"""position_sizing.py: the capital-allocation layer over a BUY/HOLD/SELL call.

WHY THIS FILE EXISTS

The module's own docstring names the one failure mode it is built to refuse:
penalising the same doubt twice. Data confidence already lowers the
conviction ceiling (references/conviction.md); if the uncertainty-adjustment
stage also multiplied the size down for the same `confidence.data` number,
a shaky filing would be charged for once as a lower ceiling and a second
time, invisibly, as a smaller multiplier - and nobody reading the output
could tell the size had been discounted twice for one fact. That guarantee
(confidence.data is accepted, echoed under `owned_elsewhere`, and never
multiplied into the factor) is exactly the kind of thing a refactor could
silently break without any single line looking wrong, which is why it gets
its own prominent test below rather than a footnote in a bigger one.

The rest of this file covers each pipeline stage in isolation - config
hardening, request normalisation, the uncertainty sub-factors, the caps,
concentration grading, the action state machine, the end-to-end refusals,
and the decision-record bridge - on the theory that a bug in any one stage
should fail close to where it lives, not surface three stages later as an
unexplained target.

This file is UNIT-level only. Property/invariant sweeps (e.g. "more overlap
never raises the target" run over many random inputs) belong in
test_position_sizing_invariants.py, written separately, and are deliberately
not duplicated here.

Every test builds its config with `ps.load_config(path="")` or an explicit
`config=` object. Never the bare default, which would resolve against
`_bootstrap.state_home()` and could read a real file from the user's home
directory - ConfigNeverReadsRealHome below asserts that path="" really does
bypass that resolution, not just that it happens to return DEFAULTS.
"""
import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers

helpers.bootstrap_path()
ps = helpers.load("position_sizing")
kelly_mod = helpers.load("kelly")


def base_request():
    """A fresh, mutable copy of the worked fixture request."""
    return copy.deepcopy(ps.fixture())


def cfg():
    """The default config, never read from a real file."""
    return ps.load_config(path="")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class ConfigDefaults(unittest.TestCase):
    def test_kelly_fraction_default_is_a_quarter(self):
        """The standing house figure is fractional, never full Kelly."""
        self.assertEqual(ps.DEFAULTS["kelly_fraction"], 0.25)


class ConfigNeverReadsRealHome(unittest.TestCase):
    """path="" must never resolve against the real, user-owned state home."""

    def test_path_empty_string_bypasses_config_path_resolution(self):
        tmp = tempfile.mkdtemp()
        poison = os.path.join(tmp, "position-sizing.json")
        with open(poison, "w", encoding="utf-8") as fh:
            fh.write('{"kelly_fraction": 0.49}')

        saved_home = os.environ.get("INVESTMENT_ANALYST_HOME")
        saved_cfg_env = os.environ.get(ps.CONFIG_ENV)
        os.environ["INVESTMENT_ANALYST_HOME"] = tmp
        os.environ[ps.CONFIG_ENV] = poison
        try:
            # config_path() itself WOULD pick up the poisoned file...
            self.assertEqual(ps.config_path(), poison)
            # ...but load_config(path="") must never call config_path() at all.
            got = ps.load_config(path="")
        finally:
            if saved_home is None:
                os.environ.pop("INVESTMENT_ANALYST_HOME", None)
            else:
                os.environ["INVESTMENT_ANALYST_HOME"] = saved_home
            if saved_cfg_env is None:
                os.environ.pop(ps.CONFIG_ENV, None)
            else:
                os.environ[ps.CONFIG_ENV] = saved_cfg_env

        self.assertEqual(got["kelly_fraction"], ps.DEFAULTS["kelly_fraction"],
                          "load_config(path='') read a config file from the "
                          "environment-resolved home; tests must never do "
                          "this")


class ConfigHardening(unittest.TestCase):
    def test_cannot_raise_max_single_position_above_hard_cap(self):
        loose = ps.load_config({"portfolio": {"max_single_position": 0.90}},
                                path="")
        self.assertEqual(loose["portfolio"]["max_single_position"],
                          ps.HARD_MAX_SINGLE_POSITION)

    def test_cannot_raise_max_sector_exposure_above_hard_cap(self):
        loose = ps.load_config({"portfolio": {"max_sector_exposure": 0.90}},
                                path="")
        self.assertEqual(loose["portfolio"]["max_sector_exposure"],
                          ps.HARD_MAX_SECTOR_EXPOSURE)

    def test_cannot_raise_max_gross_exposure_above_hard_cap(self):
        loose = ps.load_config({"portfolio": {"max_gross_exposure": 5.0}},
                                path="")
        self.assertEqual(loose["portfolio"]["max_gross_exposure"],
                          ps.HARD_MAX_GROSS_EXPOSURE)

    def test_kelly_fraction_clamped_without_allow_full_kelly(self):
        loose = ps.load_config({"kelly_fraction": 0.99}, path="")
        self.assertEqual(loose["kelly_fraction"], ps.HARD_MAX_KELLY_FRACTION)

    def test_kelly_fraction_by_conviction_clamped_without_allow_full_kelly(self):
        loose = ps.load_config(
            {"kelly_fraction_by_conviction": {"VERY HIGH": 0.99}}, path="")
        self.assertEqual(loose["kelly_fraction_by_conviction"]["VERY HIGH"],
                          ps.HARD_MAX_KELLY_FRACTION)

    def test_kelly_fraction_reaches_full_with_allow_full_kelly(self):
        full = ps.load_config({"allow_full_kelly": True, "kelly_fraction": 1.0},
                               path="")
        self.assertEqual(full["kelly_fraction"], 1.0)

    def test_config_can_tighten_every_cap(self):
        tight = ps.load_config({
            "kelly_fraction": 0.01,
            "portfolio": {"max_single_position": 0.01,
                          "max_sector_exposure": 0.01,
                          "max_gross_exposure": 0.01},
        }, path="")
        self.assertEqual(tight["kelly_fraction"], 0.01)
        self.assertEqual(tight["portfolio"]["max_single_position"], 0.01)
        self.assertEqual(tight["portfolio"]["max_sector_exposure"], 0.01)
        self.assertEqual(tight["portfolio"]["max_gross_exposure"], 0.01)


class HardenDirection(unittest.TestCase):
    """_harden()'s stated rule, one test per group: a value with no HARD_
    constant is clamped at its own DEFAULT in whichever direction loosens
    the engine, and each group's loosening direction differs, which is
    exactly what makes 'a config can only tighten' easy to get backwards in
    a refactor."""

    def test_min_position_may_rise_above_default_but_not_fall(self):
        raised = ps.load_config({"min_position": 0.02}, path="")
        self.assertEqual(raised["min_position"], 0.02)
        lowered = ps.load_config({"min_position": 0.001}, path="")
        self.assertEqual(lowered["min_position"], ps.DEFAULTS["min_position"])

    def test_rebalance_band_and_exit_below_may_fall_but_not_rise(self):
        raised = ps.load_config(
            {"rebalance_band": 0.02, "exit_below": 0.02}, path="")
        self.assertEqual(raised["rebalance_band"], ps.DEFAULTS["rebalance_band"])
        self.assertEqual(raised["exit_below"], ps.DEFAULTS["exit_below"])
        lowered = ps.load_config(
            {"rebalance_band": 0.001, "exit_below": 0.0005}, path="")
        self.assertEqual(lowered["rebalance_band"], 0.001)
        self.assertEqual(lowered["exit_below"], 0.0005)

    def test_uncertainty_floors_may_fall_but_not_rise(self):
        udef = ps.DEFAULTS["uncertainty"]
        for key in ("floor", "robustness_floor", "estimate_width_floor",
                    "thesis_floor", "incomplete_inputs"):
            raised = ps.load_config({"uncertainty": {key: 0.999}}, path="")
            self.assertEqual(raised["uncertainty"][key], udef[key],
                              "uncertainty.%s rose above its default" % key)
            lowered = ps.load_config({"uncertainty": {key: 0.01}}, path="")
            self.assertEqual(lowered["uncertainty"][key], 0.01,
                              "uncertainty.%s could not be tightened" % key)

    def test_concentration_multipliers_may_fall_but_not_rise(self):
        cdef = ps.DEFAULTS["concentration"]
        for level in ("low", "medium", "high", "unknown"):
            raised = ps.load_config({"concentration": {level: 1.5}}, path="")
            self.assertEqual(raised["concentration"][level], cdef[level])
            lowered = ps.load_config({"concentration": {level: 0.1}}, path="")
            self.assertEqual(lowered["concentration"][level], 0.1)

    def test_probability_shift_may_rise_but_not_fall(self):
        raised = ps.load_config(
            {"uncertainty": {"probability_shift": 0.3}}, path="")
        self.assertEqual(raised["uncertainty"]["probability_shift"], 0.3)
        lowered = ps.load_config(
            {"uncertainty": {"probability_shift": 0.01}}, path="")
        self.assertEqual(lowered["uncertainty"]["probability_shift"],
                          ps.DEFAULTS["uncertainty"]["probability_shift"])

    def test_wide_band_may_fall_but_not_rise(self):
        raised = ps.load_config({"uncertainty": {"wide_band": 2.0}}, path="")
        self.assertEqual(raised["uncertainty"]["wide_band"],
                          ps.DEFAULTS["uncertainty"]["wide_band"])
        lowered = ps.load_config({"uncertainty": {"wide_band": 0.5}}, path="")
        self.assertEqual(lowered["uncertainty"]["wide_band"], 0.5)

    def test_microcap_max_position_may_fall_but_not_rise(self):
        raised = ps.load_config(
            {"liquidity": {"microcap_max_position": 0.5}}, path="")
        self.assertEqual(raised["liquidity"]["microcap_max_position"],
                          ps.DEFAULTS["liquidity"]["microcap_max_position"])
        lowered = ps.load_config(
            {"liquidity": {"microcap_max_position": 0.001}}, path="")
        self.assertEqual(lowered["liquidity"]["microcap_max_position"], 0.001)

    def test_no_bet_booleans_are_forced_true(self):
        loosened = ps.load_config({"no_bet": {
            "critical_conflict": False, "active_thesis_breaker": False,
            "blocking_reason_codes": False}}, path="")
        self.assertTrue(loosened["no_bet"]["critical_conflict"])
        self.assertTrue(loosened["no_bet"]["active_thesis_breaker"])
        self.assertTrue(loosened["no_bet"]["blocking_reason_codes"])

    def test_risk_budget_enabled_is_forced_true(self):
        loosened = ps.load_config(
            {"risk_budget": {"enabled": False}}, path="")
        self.assertTrue(loosened["risk_budget"]["enabled"])

    def test_no_bet_min_data_confidence_may_rise_but_not_fall(self):
        """Lowering the floor lets more (weaker-data) bets through, so that
        is the loosening direction and must clamp back to the default;
        raising the floor is stricter and must pass through unchanged."""
        ddef = ps.DEFAULTS["no_bet"]["min_data_confidence"]
        raised = ps.load_config({"no_bet": {"min_data_confidence": 60}},
                                 path="")
        self.assertEqual(raised["no_bet"]["min_data_confidence"], 60)
        lowered = ps.load_config({"no_bet": {"min_data_confidence": 10}},
                                  path="")
        self.assertEqual(lowered["no_bet"]["min_data_confidence"], ddef)

    def test_no_bet_conviction_floor_may_rise_but_not_fall(self):
        """A lower conviction_floor admits weaker-conviction bets, so that
        is the loosening direction and must clamp back to the default;
        raising it up the ladder is stricter and must pass through."""
        ddef = ps.DEFAULTS["no_bet"]["conviction_floor"]
        raised = ps.load_config({"no_bet": {"conviction_floor": "HIGH"}},
                                 path="")
        self.assertEqual(raised["no_bet"]["conviction_floor"], "HIGH")
        lowered = ps.load_config(
            {"no_bet": {"conviction_floor": "VERY LOW"}}, path="")
        self.assertEqual(lowered["no_bet"]["conviction_floor"], ddef)

    def test_liquidity_participation_rate_and_exit_days_may_fall_but_not_rise(self):
        """Each factor loosens the liquidity cap as it rises (a bigger
        participation rate or more exit days both raise the cap), so each
        is clamped at its own default; lowering either is stricter."""
        ldef = ps.DEFAULTS["liquidity"]
        raised = ps.load_config(
            {"liquidity": {"participation_rate": 0.9, "exit_days": 30}},
            path="")
        self.assertEqual(raised["liquidity"]["participation_rate"],
                          ldef["participation_rate"])
        self.assertEqual(raised["liquidity"]["exit_days"], ldef["exit_days"])
        lowered = ps.load_config(
            {"liquidity": {"participation_rate": 0.05, "exit_days": 1}},
            path="")
        self.assertEqual(lowered["liquidity"]["participation_rate"], 0.05)
        self.assertEqual(lowered["liquidity"]["exit_days"], 1)

    def test_liquidity_microcap_sek_may_rise_but_not_fall(self):
        """A higher threshold catches more companies as microcap (stricter);
        a lower one excludes companies that should be caught (looser), so
        lowering must clamp back to the default and raising must pass."""
        ldef = ps.DEFAULTS["liquidity"]["microcap_sek"]
        raised = ps.load_config(
            {"liquidity": {"microcap_sek": ldef * 10}}, path="")
        self.assertEqual(raised["liquidity"]["microcap_sek"], ldef * 10)
        lowered = ps.load_config(
            {"liquidity": {"microcap_sek": 100}}, path="")
        self.assertEqual(lowered["liquidity"]["microcap_sek"], ldef)


class HardenRefusals(unittest.TestCase):
    """_harden() type-checks every section before touching it, and refuses
    a Kelly fraction of zero rather than let it reach kelly.fractional as a
    raw KellyError traceback."""

    def test_config_section_set_to_null_is_refused(self):
        with self.assertRaises(ps.SizingError):
            ps.load_config({"uncertainty": None}, path="")

    def test_kelly_fraction_of_zero_is_refused(self):
        with self.assertRaises(ps.SizingError):
            ps.load_config({"kelly_fraction": 0.0}, path="")

    def test_kelly_fraction_by_conviction_of_zero_is_refused(self):
        with self.assertRaises(ps.SizingError):
            ps.load_config(
                {"kelly_fraction_by_conviction": {"HIGH": 0.0}}, path="")

    def test_exit_below_at_or_above_min_position_is_refused(self):
        """Unreachable through load_config()'s public path under the
        shipped DEFAULTS: exit_below's own clamp ceiling (its DEFAULT,
        0.0025) sits strictly below min_position's own clamp floor (its
        DEFAULT, 0.005), so no caller-supplied override can ever make the
        clamped pair collide. The guard itself is exercised directly here,
        on a fresh module instance with its own DEFAULTS nudged so the two
        clamp bounds coincide - proving the check fires if that gap is ever
        closed, rather than only trusting that it currently never runs."""
        fresh = helpers.load("position_sizing")
        fresh.DEFAULTS = copy.deepcopy(fresh.DEFAULTS)
        fresh.DEFAULTS["min_position"] = 0.01
        fresh.DEFAULTS["exit_below"] = 0.01
        cfg_in = copy.deepcopy(fresh.DEFAULTS)
        with self.assertRaises(fresh.SizingError):
            fresh._harden(cfg_in)


class SizeHardensCallerSuppliedConfig(unittest.TestCase):
    """The 'config can only tighten' guarantee applies to size()'s in-process
    config= argument too, not merely to the CLI's config file. A caller-built
    dict with every cap maximised must still be hardened before it is used."""

    def test_caller_supplied_dict_is_hardened_not_trusted_as_is(self):
        raw = copy.deepcopy(ps.DEFAULTS)
        raw["portfolio"]["max_single_position"] = 0.9
        raw["uncertainty"]["floor"] = 1.0
        for level in raw["concentration"]:
            raw["concentration"][level] = 1.0

        req = base_request()
        req["confidence"] = {"data": 78}  # no thesis/valuation: uncertainty applies
        req["portfolio"]["shares_sector_with"] = 3
        req["portfolio"]["shares_driver_with"] = 2

        result = ps.size(req, config=raw)
        self.assertLessEqual(result["target"], ps.HARD_MAX_SINGLE_POSITION,
                              "hard cap did not survive a caller-supplied "
                              "config dict")
        self.assertLess(result["uncertainty_factor"], 1.0,
                         "uncertainty factor was not applied despite the "
                         "caller's dict setting its floor to 1.0")
        self.assertLess(result["concentration"]["multiplier"], 1.0,
                         "concentration multiplier was not applied despite "
                         "the caller's dict setting it to 1.0")


class ConfigFile(unittest.TestCase):
    def test_valid_config_file_is_merged(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"min_position": 0.02}')
            got = ps.load_config(path=path)
            self.assertEqual(got["min_position"], 0.02)
        finally:
            os.remove(path)

    def test_malformed_config_file_raises_sizing_error(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{not valid json")
            with self.assertRaises(ps.SizingError):
                ps.load_config(path=path)
        finally:
            os.remove(path)

    def test_unreadable_config_path_raises_sizing_error(self):
        # A directory used as the config path is a real, cross-platform way
        # to make open() fail with an OSError rather than a JSONDecodeError.
        directory = tempfile.mkdtemp()
        with self.assertRaises(ps.SizingError):
            ps.load_config(path=directory)


# ---------------------------------------------------------------------------
# normalise_request / normalise_reason_codes
# ---------------------------------------------------------------------------

class NormaliseRequestValidation(unittest.TestCase):
    def test_rejects_non_object_request(self):
        with self.assertRaises(ps.SizingError):
            ps.normalise_request(["not", "an", "object"])

    def test_rejects_bad_conviction_token(self):
        with self.assertRaises(ps.SizingError):
            ps.normalise_request({"conviction": "SUPER HIGH"})

    def test_rejects_negative_weight(self):
        with self.assertRaises(ps.SizingError):
            ps.normalise_request({"portfolio": {"current_weight": -0.05}})


class NormaliseRequestWeights(unittest.TestCase):
    def test_weight_above_one_is_refused_not_read_as_percent(self):
        """Guessing that 2.0 meant 2% turned a geared gross exposure of 1.30
        into 0.013 and silently removed the gross cap. Refuse instead."""
        with self.assertRaises(ps.SizingError) as caught:
            ps.normalise_request({"portfolio": {"current_weight": 2.0}})
        self.assertIn("fractions", str(caught.exception))

    def test_geared_gross_exposure_is_allowed_above_one(self):
        """A 130%-gross book is the case the gross cap exists for."""
        req = ps.normalise_request({"portfolio": {"gross_weight": 1.30}})
        self.assertAlmostEqual(req["portfolio"]["gross_weight"], 1.30)

    def test_weight_at_or_below_one_is_read_as_fraction(self):
        req = ps.normalise_request({"portfolio": {"sector_weight": 0.06}})
        self.assertAlmostEqual(req["portfolio"]["sector_weight"], 0.06)


class NormaliseReasonCodes(unittest.TestCase):
    def test_bare_string_defaults_to_warn(self):
        out = ps.normalise_reason_codes(["THESIS_BROKEN"])
        self.assertEqual(out, [{"code": "THESIS_BROKEN", "severity": "WARN"}])

    def test_code_severity_object_is_kept(self):
        out = ps.normalise_reason_codes(
            [{"code": "GATE_PRICE_STALE", "severity": "BLOCK"}])
        self.assertEqual(out, [{"code": "GATE_PRICE_STALE",
                                "severity": "BLOCK"}])

    def test_anything_else_is_rejected(self):
        for bad in (123, None, 1.5, {"severity": "WARN"}, ["nested"]):
            with self.assertRaises(ps.SizingError):
                ps.normalise_reason_codes([bad])


# ---------------------------------------------------------------------------
# uncertainty_adjustment
# ---------------------------------------------------------------------------

# Scenarios whose Kelly figure collapses (flips to NO_BET) once the default
# probability_shift is moved from the best to the worst outcome.
_FRAGILE_SCENARIOS = [{"name": "bear", "probability": 0.20, "return": -0.40},
                      {"name": "base", "probability": 0.55, "return": 0.10},
                      {"name": "bull", "probability": 0.25, "return": 0.30}]


class UncertaintyAdjustmentThesisConfidence(unittest.TestCase):
    def test_higher_thesis_confidence_gives_a_higher_factor(self):
        c = cfg()
        high = ps.normalise_request({"scenarios": ps._SCENARIOS,
                                     "confidence": {"thesis": 0.95}})
        low = ps.normalise_request({"scenarios": ps._SCENARIOS,
                                    "confidence": {"thesis": 0.05}})
        f_high = ps.uncertainty_adjustment(high, c, ps._SCENARIOS)["factor"]
        f_low = ps.uncertainty_adjustment(low, c, ps._SCENARIOS)["factor"]
        self.assertGreater(f_high, f_low)


class UncertaintyAdjustmentMissingInputs(unittest.TestCase):
    def test_two_missing_inputs_charge_incomplete_inputs_exactly_once(self):
        c = cfg()
        # No valuation band, no explicit valuation confidence (estimate_width
        # is not checkable) and no thesis confidence: two missing checks.
        req = ps.normalise_request({"scenarios": ps._SCENARIOS})
        unc = ps.uncertainty_adjustment(req, c, ps._SCENARIOS)

        self.assertIn("estimate_width", unc["not_checked"])
        self.assertIn("thesis_confidence", unc["not_checked"])
        self.assertEqual(len(unc["not_checked"]), 2)
        self.assertEqual(unc["components"]["incomplete_inputs"]["input"], 2)
        self.assertEqual(unc["components"]["incomplete_inputs"]["factor"],
                          c["uncertainty"]["incomplete_inputs"],
                          "the incomplete_inputs penalty must be charged "
                          "once, not once per missing input")


class UncertaintyAdjustmentAntiDoublePenalty(unittest.TestCase):
    """The guarantee this whole module exists to protect: confidence.data
    must never move the size. It is owned entirely by the conviction
    ceiling elsewhere; if this test ever fails, the double-penalty defect
    the module's docstring warns about has come back."""

    def test_confidence_data_never_changes_the_uncertainty_factor_or_target(self):
        c = cfg()
        req_a = base_request()
        req_a["confidence"]["data"] = 90
        req_b = base_request()
        req_b["confidence"]["data"] = 45  # both above the no-bet floor of 40

        norm_a = ps.normalise_request(req_a)
        norm_b = ps.normalise_request(req_b)
        unc_a = ps.uncertainty_adjustment(norm_a, c, norm_a["scenarios"])
        unc_b = ps.uncertainty_adjustment(norm_b, c, norm_b["scenarios"])
        self.assertEqual(
            unc_a["factor"], unc_b["factor"],
            "ANTI-DOUBLE-PENALTY VIOLATION: confidence.data changed the "
            "uncertainty factor. confidence.data is owned by the "
            "conviction ceiling, not by uncertainty_adjustment - see the "
            "module docstring's 'PENALISE THE SAME DOUBT TWICE' section.")

        result_a = ps.size(req_a, config=c)
        result_b = ps.size(req_b, config=c)
        self.assertEqual(result_a["status"], ps.OK)
        self.assertEqual(result_b["status"], ps.OK)
        self.assertEqual(
            result_a["target"], result_b["target"],
            "ANTI-DOUBLE-PENALTY VIOLATION: two requests differing only in "
            "confidence.data produced different targets.")


class UncertaintyAdjustmentFlooring(unittest.TestCase):
    def test_factor_is_floored_and_flooring_is_reported(self):
        c = cfg()
        req = ps.normalise_request({
            "scenarios": _FRAGILE_SCENARIOS,
            "confidence": {"thesis": 0.0, "valuation": 0.0},
        })
        unc = ps.uncertainty_adjustment(req, c, _FRAGILE_SCENARIOS)
        self.assertEqual(unc["factor"], c["uncertainty"]["floor"])
        self.assertTrue(unc["floored"])

    def test_factor_never_drops_below_floor_when_not_floored(self):
        c = cfg()
        req = ps.normalise_request({"scenarios": ps._SCENARIOS,
                                    "confidence": {"thesis": 0.9}})
        unc = ps.uncertainty_adjustment(req, c, ps._SCENARIOS)
        self.assertGreaterEqual(unc["factor"], c["uncertainty"]["floor"])


class UncertaintyAdjustmentOmissionNeverBeatsHonesty(unittest.TestCase):
    """ANTI-GAMING GUARANTEE: an omitted confidence.thesis or
    confidence.valuation must never score higher than the factor a stated
    0.5 would earn, and must always score strictly below a stated 0.9. The
    stand-in for 'unknown' is what a confidence of 0.5 is worth - no
    information, not good news - so omission can never be strictly better
    than reporting the honest, mediocre number, and a caller cannot buy a
    bigger position by leaving the field out rather than stating a weak
    figure. Swept over a ladder of stated values on both fields."""

    LADDER = (0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0)

    def test_omitted_thesis_confidence_never_beats_a_stated_value(self):
        c = cfg()
        omitted = ps.normalise_request({"scenarios": ps._SCENARIOS,
                                        "confidence": {"valuation": 0.6}})
        omitted_factor = ps.uncertainty_adjustment(
            omitted, c, ps._SCENARIOS)["factor"]
        for stated in self.LADDER:
            with self.subTest(stated_thesis=stated):
                req = ps.normalise_request({
                    "scenarios": ps._SCENARIOS,
                    "confidence": {"thesis": stated, "valuation": 0.6}})
                factor = ps.uncertainty_adjustment(
                    req, c, ps._SCENARIOS)["factor"]
                if abs(stated - 0.5) < 1e-9:
                    self.assertLessEqual(
                        omitted_factor, factor + 1e-12,
                        "ANTI-GAMING VIOLATION: omitting confidence.thesis "
                        "scored higher than honestly stating 0.5 - omission "
                        "must never beat a mediocre honest answer")
                if abs(stated - 0.9) < 1e-9:
                    self.assertLess(
                        omitted_factor, factor,
                        "ANTI-GAMING VIOLATION: omitting confidence.thesis "
                        "was not strictly worse than honestly stating 0.9 - "
                        "a caller could buy a bigger position by staying "
                        "silent than by admitting high confidence honestly")

    def test_omitted_valuation_confidence_never_beats_a_stated_value(self):
        c = cfg()
        omitted = ps.normalise_request({"scenarios": ps._SCENARIOS,
                                        "confidence": {"thesis": 0.6}})
        omitted_factor = ps.uncertainty_adjustment(
            omitted, c, ps._SCENARIOS)["factor"]
        for stated in self.LADDER:
            with self.subTest(stated_valuation=stated):
                req = ps.normalise_request({
                    "scenarios": ps._SCENARIOS,
                    "confidence": {"thesis": 0.6, "valuation": stated}})
                factor = ps.uncertainty_adjustment(
                    req, c, ps._SCENARIOS)["factor"]
                if abs(stated - 0.5) < 1e-9:
                    self.assertLessEqual(
                        omitted_factor, factor + 1e-12,
                        "ANTI-GAMING VIOLATION: omitting confidence.valuation "
                        "scored higher than honestly stating 0.5 - omission "
                        "must never beat a mediocre honest answer")
                if abs(stated - 0.9) < 1e-9:
                    self.assertLess(
                        omitted_factor, factor,
                        "ANTI-GAMING VIOLATION: omitting confidence.valuation "
                        "was not strictly worse than honestly stating 0.9 - "
                        "a caller could buy a bigger position by staying "
                        "silent than by admitting high confidence honestly")


class ProbabilityRobustness(unittest.TestCase):
    def test_ratio_is_in_zero_one_and_lower_for_a_fragile_distribution(self):
        c = cfg()
        robust, _ = ps.probability_robustness(ps._SCENARIOS, c)
        fragile, _ = ps.probability_robustness(_FRAGILE_SCENARIOS, c)
        self.assertIsNotNone(robust)
        self.assertIsNotNone(fragile)
        self.assertGreaterEqual(robust, 0.0)
        self.assertLessEqual(robust, 1.0)
        self.assertGreaterEqual(fragile, 0.0)
        self.assertLessEqual(fragile, 1.0)
        self.assertLess(fragile, robust)

    def test_uses_the_unclamped_kelly_pair_when_both_ends_clamp(self):
        """Both the base and the stressed Kelly clamp at full capital here -
        an ordinary-looking spread with a very large bull case - so a version
        comparing the CLAMPED figures would divide 1.0/1.0 and report
        'perfectly robust' exactly where the real edge nearly halved. The
        unclamped pair must be used instead, so the reported robustness is
        strictly below 1.0."""
        c = cfg()
        huge = [{"name": "bear", "probability": 0.05, "return": -0.10},
                {"name": "base", "probability": 0.55, "return": 0.30},
                {"name": "bull", "probability": 0.40, "return": 5.0}]
        base = kelly_mod.kelly_scenarios(huge)
        self.assertTrue(base["clamped"], "fixture must clamp the base Kelly "
                                          "for this test to be meaningful")
        robust, base_result = ps.probability_robustness(huge, c)
        self.assertIsNotNone(robust)
        self.assertLess(
            robust, 1.0,
            "probability_robustness read 1.0 (or unclamped) for a "
            "distribution where both ends clamp at full capital - it must "
            "be comparing raw_kelly instead of unclamped_kelly")


# ---------------------------------------------------------------------------
# estimate_width_confidence
# ---------------------------------------------------------------------------

class EstimateWidthConfidence(unittest.TestCase):
    def test_explicit_confidence_wins_over_the_band(self):
        c = cfg()
        req = ps.normalise_request({
            "confidence": {"valuation": 0.77},
            "valuation": {"price": 100, "base_low": 50, "base_high": 150},
        })
        value, source = ps.estimate_width_confidence(req, c)
        self.assertEqual(value, 0.77)
        self.assertEqual(source, "confidence.valuation")

    def test_wide_band_gives_lower_confidence_than_narrow_band(self):
        c = cfg()
        narrow = ps.normalise_request({
            "valuation": {"price": 100, "base_low": 95, "base_high": 105}})
        wide = ps.normalise_request({
            "valuation": {"price": 100, "base_low": 50, "base_high": 150}})
        narrow_conf, _ = ps.estimate_width_confidence(narrow, c)
        wide_conf, _ = ps.estimate_width_confidence(wide, c)
        self.assertLess(wide_conf, narrow_conf)

    def test_missing_band_returns_none_not_a_guess(self):
        c = cfg()
        req = ps.normalise_request({})
        value, source = ps.estimate_width_confidence(req, c)
        self.assertIsNone(value)
        self.assertIsNone(source)

    def test_inverted_band_raises_sizing_error(self):
        c = cfg()
        req = ps.normalise_request({
            "valuation": {"price": 100, "base_low": 120, "base_high": 90}})
        with self.assertRaises(ps.SizingError):
            ps.estimate_width_confidence(req, c)


# ---------------------------------------------------------------------------
# Caps
# ---------------------------------------------------------------------------

class ConvictionCap(unittest.TestCase):
    def test_each_conviction_level_maps_to_its_ceiling(self):
        for level in ps.CONVICTIONS:
            cap, detail = ps.conviction_cap(level)
            self.assertEqual(cap, ps.CONVICTION_POSITION_CEILING[level])
            self.assertEqual(detail["conviction"], level)

    def test_quick_depth_lowers_high_conviction_to_medium(self):
        cap, detail = ps.conviction_cap("HIGH", depth="QUICK", reason_codes=[])
        self.assertEqual(cap, ps.CONVICTION_POSITION_CEILING["MEDIUM"])
        self.assertEqual(detail["conviction"], "MEDIUM")

    def test_unstated_conviction_maps_to_the_low_rung_not_the_highest(self):
        """conviction_cap(None) must return exactly
        CONVICTION_POSITION_CEILING[UNSTATED_CONVICTION_CEILING], and that
        constant must itself be "LOW" - an unstated conviction is an unrun
        check, never an entitlement to the top of the ladder. Setting
        UNSTATED_CONVICTION_CEILING to "VERY HIGH" would break neither this
        module's own arithmetic nor any other test, so it is asserted here
        by name, not merely by its current numeric value."""
        self.assertEqual(ps.UNSTATED_CONVICTION_CEILING, "LOW")
        cap, detail = ps.conviction_cap(None)
        self.assertEqual(cap, ps.CONVICTION_POSITION_CEILING[
            ps.UNSTATED_CONVICTION_CEILING])
        self.assertEqual(detail["assumed"], ps.UNSTATED_CONVICTION_CEILING)

    def test_omitting_conviction_never_buys_a_bigger_position_than_stating_one(self):
        """End-to-end: omitting conviction entirely must never size a larger
        position than explicitly stating LOW, and must always size strictly
        smaller than explicitly stating HIGH - otherwise a caller could get
        a bigger, not smaller, position by saying nothing."""
        c = cfg()

        def sized(conviction):
            req = base_request()
            if conviction is None:
                req.pop("conviction", None)
            else:
                req["conviction"] = conviction
            return ps.size(req, config=c)["target"]

        unstated = sized(None)
        low = sized("LOW")
        high = sized("HIGH")
        self.assertLessEqual(unstated, low)
        self.assertLess(unstated, high)


class HardSingleNameCapOrdering(unittest.TestCase):
    """HARD_MAX_SINGLE_POSITION (0.10) is unreachable through the conviction
    ladder today: conviction_cap() tops out at 0.08 (CONVICTION_POSITION_
    CEILING["VERY HIGH"]) and concentration only ever multiplies down, so
    the `capped > HARD_MAX_SINGLE_POSITION` line in size() never fires and
    could be deleted with nothing here failing. It is kept as defence in
    depth. This test proves the ordering still holds *if* the ladder is
    ever raised past the hard cap: it patches CONVICTION_POSITION_CEILING
    on a fresh module instance, and additionally neutralises the separate,
    redundant portfolio.max_single_position cap (which is independently
    hardened to never exceed HARD_MAX_SINGLE_POSITION and would otherwise
    also mask the line under test at the same numeric value) so the
    conviction-cap-versus-hard-cap interaction is isolated."""

    def test_conviction_above_hard_cap_is_clamped_to_hard_single_name(self):
        fresh = helpers.load("position_sizing")
        fresh.CONVICTION_POSITION_CEILING["VERY HIGH"] = 0.5

        # Neutralise the portfolio-level single_name cap, which is always
        # present in `caps` and is itself hardened to <= HARD_MAX_SINGLE_
        # POSITION - left in place it would bind at the same 0.10 value via
        # the "single_name" token, not "hard_single_name", proving nothing
        # about the hard-cap line this test exists to guard.
        original_portfolio_caps = fresh.portfolio_caps

        def uncapped_single_name(req, cfg):
            caps, detail = original_portfolio_caps(req, cfg)
            caps["single_name"] = 999.0
            return caps, detail

        fresh.portfolio_caps = uncapped_single_name

        # Deliberately minimal - no portfolio context, so none of sector,
        # country, gross or cash caps are in play either; only conviction,
        # liquidity (absent -> None), risk_budget and the neutralised
        # single_name compete with the hard cap.
        req = {
            "instrument": "TEST",
            "conviction": "VERY HIGH",
            "probability_basis": "scenario_analysis",
            "scenarios": [
                {"name": "bear", "probability": 0.2, "return": -0.30},
                {"name": "base", "probability": 0.5, "return": 0.20},
                {"name": "bull", "probability": 0.3, "return": 0.60},
            ],
        }
        result = fresh.size(req, config_overrides={
            "risk_budget": {"max_position_loss": fresh.HARD_MAX_POSITION_LOSS}})

        self.assertEqual(result["binding_constraint"], "hard_single_name")
        self.assertAlmostEqual(
            result["target"],
            fresh.HARD_MAX_SINGLE_POSITION
            * result["concentration"]["multiplier"],
            places=9)


class NotCheckedRecordsConvictionGaps(unittest.TestCase):
    """not_checked must record BOTH ways a conviction figure can be missing:
    the caller stating no conviction at all, and decision_record (which
    supplies the depth/reason-code ceiling) being unreachable."""

    def test_unstated_conviction_is_recorded_in_not_checked(self):
        c = cfg()
        req = base_request()
        req.pop("conviction", None)
        result = ps.size(req, config=c)
        self.assertIn("conviction", result["not_checked"])

    def test_unreachable_decision_record_is_recorded_as_conviction_ceiling(self):
        c = cfg()
        req = base_request()  # carries a depth and reason_codes
        original = ps._bootstrap.soft_load

        def patched(name):
            if name == "decision_record":
                return None
            return original(name)

        ps._bootstrap.soft_load = patched
        try:
            result = ps.size(req, config=c)
        finally:
            ps._bootstrap.soft_load = original
        self.assertIn("conviction ceiling", result["not_checked"])


class LiquidityCap(unittest.TestCase):
    def test_participation_model_arithmetic(self):
        c = cfg()
        req = ps.normalise_request({
            "liquidity": {"adv_sek": 10_000_000},
            "portfolio": {"total_value_sek": 100_000_000},
        })
        cap, detail = ps.liquidity_cap(req, c)
        cl = c["liquidity"]
        expected = min(
            cl["participation_rate"] * 10_000_000 * cl["exit_days"]
            / 100_000_000, 1.0)
        self.assertAlmostEqual(cap, expected)
        self.assertEqual(detail["basis"], "adv_sek")

    def test_clamped_at_one(self):
        c = cfg()
        req = ps.normalise_request({
            "liquidity": {"adv_sek": 500_000_000},
            "portfolio": {"total_value_sek": 1_000},
        })
        cap, _ = ps.liquidity_cap(req, c)
        self.assertEqual(cap, 1.0)

    def test_turnover_sek_used_as_fallback_basis_and_is_labelled(self):
        c = cfg()
        req = ps.normalise_request({
            "liquidity": {"turnover_sek": 5_000_000},
            "portfolio": {"total_value_sek": 100_000_000},
        })
        cap, detail = ps.liquidity_cap(req, c)
        self.assertIsNotNone(cap)
        self.assertIn("turnover_sek", detail["basis"])

    def test_missing_traded_value_is_recorded_in_not_checked(self):
        c = cfg()
        req = ps.normalise_request({
            "portfolio": {"total_value_sek": 100_000_000}})
        _, detail = ps.liquidity_cap(req, c)
        self.assertIn("traded value", detail["not_checked"])

    def test_missing_portfolio_value_is_recorded_in_not_checked(self):
        c = cfg()
        req = ps.normalise_request({"liquidity": {"adv_sek": 10_000_000}})
        _, detail = ps.liquidity_cap(req, c)
        self.assertIn("portfolio value", detail["not_checked"])

    def test_missing_market_cap_is_recorded_in_not_checked(self):
        c = cfg()
        req = ps.normalise_request({
            "liquidity": {"adv_sek": 10_000_000},
            "portfolio": {"total_value_sek": 100_000_000}})
        _, detail = ps.liquidity_cap(req, c)
        self.assertIn("market cap", detail["not_checked"])

    def test_microcap_applies_microcap_max_position(self):
        c = cfg()
        req = ps.normalise_request({
            "liquidity": {"adv_sek": 10_000_000,
                         "market_cap_sek": 100_000_000},  # below microcap_sek
            "portfolio": {"total_value_sek": 10_000_000},  # huge participation cap
        })
        cap, detail = ps.liquidity_cap(req, c)
        self.assertTrue(detail.get("microcap"))
        self.assertEqual(cap, c["liquidity"]["microcap_max_position"])


class PortfolioCaps(unittest.TestCase):
    def test_sector_headroom_subtracts_the_rest_of_the_bucket(self):
        c = cfg()
        req = ps.normalise_request({
            "portfolio": {"current_weight": 0.06, "sector_weight": 0.18}})
        caps, _ = ps.portfolio_caps(req, c)
        expected = c["portfolio"]["max_sector_exposure"] - (0.18 - 0.06)
        self.assertAlmostEqual(caps["sector"], expected)

    def test_cash_cap_is_current_plus_cash_weight(self):
        c = cfg()
        req = ps.normalise_request({
            "portfolio": {"current_weight": 0.03, "cash_weight": 0.10}})
        caps, _ = ps.portfolio_caps(req, c)
        self.assertAlmostEqual(caps["cash"], 0.13)

    def test_missing_inputs_land_in_not_checked_with_cap_none(self):
        c = cfg()
        req = ps.normalise_request({"portfolio": {"current_weight": 0.03}})
        caps, detail = ps.portfolio_caps(req, c)
        self.assertIsNone(caps["sector"])
        self.assertIsNone(caps["country"])
        self.assertIsNone(caps["gross"])
        self.assertIsNone(caps["cash"])
        self.assertIn("sector exposure", detail["not_checked"])
        self.assertIn("country exposure", detail["not_checked"])
        self.assertIn("gross exposure", detail["not_checked"])
        self.assertIn("cash", detail["not_checked"])


class PortfolioCapsBucketMustIncludePosition(unittest.TestCase):
    """A bucket weight smaller than the position's own current weight is a
    contradiction between two inputs that should agree - the caller measured
    two different things - and max(bucket - current, 0) would quietly hand
    back the whole limit as headroom, the loosest cap from the most obviously
    wrong input. Each of the three buckets must refuse it independently."""

    def test_sector_weight_below_current_weight_refuses(self):
        c = cfg()
        req = ps.normalise_request(
            {"portfolio": {"current_weight": 0.05, "sector_weight": 0.03}})
        with self.assertRaises(ps.SizingError):
            ps.portfolio_caps(req, c)

    def test_country_weight_below_current_weight_refuses(self):
        c = cfg()
        req = ps.normalise_request(
            {"portfolio": {"current_weight": 0.05, "country_weight": 0.03}})
        with self.assertRaises(ps.SizingError):
            ps.portfolio_caps(req, c)

    def test_gross_weight_below_current_weight_refuses(self):
        c = cfg()
        req = ps.normalise_request(
            {"portfolio": {"current_weight": 0.05, "gross_weight": 0.03}})
        with self.assertRaises(ps.SizingError):
            ps.portfolio_caps(req, c)


# ---------------------------------------------------------------------------
# concentration
# ---------------------------------------------------------------------------

class Concentration(unittest.TestCase):
    def test_no_portfolio_context_is_unknown(self):
        c = cfg()
        req = ps.normalise_request({})
        result = ps.concentration(req, c)
        self.assertEqual(result["level"], "UNKNOWN")
        self.assertEqual(result["multiplier"], c["concentration"]["unknown"])

    def test_no_overlap_is_low(self):
        c = cfg()
        req = ps.normalise_request({
            "portfolio": {"current_weight": 0.02, "sector_weight": 0.02,
                         "shares_sector_with": 0, "shares_driver_with": 0}})
        result = ps.concentration(req, c)
        self.assertEqual(result["level"], "LOW")
        self.assertEqual(result["multiplier"], 1.0)

    def test_sector_near_limit_plus_shared_holdings_is_high(self):
        c = cfg()
        max_sector = c["portfolio"]["max_sector_exposure"]
        req = ps.normalise_request({
            "portfolio": {"current_weight": 0.0,
                         "sector_weight": 0.8 * max_sector,
                         "shares_sector_with": 2, "shares_driver_with": 0}})
        result = ps.concentration(req, c)
        self.assertEqual(result["level"], "HIGH")

    def test_multipliers_come_from_config(self):
        custom = ps.load_config({"concentration": {"low": 0.42}}, path="")
        req = ps.normalise_request({
            "portfolio": {"current_weight": 0.0, "sector_weight": 0.0,
                         "shares_sector_with": 0, "shares_driver_with": 0}})
        result = ps.concentration(req, custom)
        self.assertEqual(result["level"], "LOW")
        self.assertEqual(result["multiplier"], 0.42)


# ---------------------------------------------------------------------------
# decide_action
# ---------------------------------------------------------------------------

class DecideAction(unittest.TestCase):
    def test_no_bet_when_blocked_and_not_held(self):
        c = cfg()
        action, _ = ps.decide_action(0.0, 0.0, c, blocked=True)
        self.assertEqual(action, "NO_BET")

    def test_exit_when_blocked_and_held(self):
        c = cfg()
        action, _ = ps.decide_action(0.0, 0.05, c, blocked=True)
        self.assertEqual(action, "EXIT")

    def test_watch_when_target_too_small_and_not_held(self):
        c = cfg()
        action, _ = ps.decide_action(0.001, 0.0, c)
        self.assertEqual(action, "WATCH")

    def test_initiate_when_not_held_and_target_clears_the_band(self):
        c = cfg()
        action, _ = ps.decide_action(0.03, 0.0, c)
        self.assertEqual(action, "INITIATE")

    def test_add_when_held_and_target_clears_the_band_upward(self):
        c = cfg()
        action, _ = ps.decide_action(0.03, 0.02, c)
        self.assertEqual(action, "ADD")

    def test_hold_when_drift_is_within_the_rebalance_band(self):
        c = cfg()
        action, delta = ps.decide_action(0.021, 0.02, c)
        self.assertEqual(action, "HOLD")
        self.assertLess(abs(delta), c["rebalance_band"])

    def test_trim_when_target_drops_but_stays_above_exit_floor(self):
        c = cfg()
        action, _ = ps.decide_action(0.01, 0.02, c)
        self.assertEqual(action, "TRIM")

    def test_exit_when_target_drops_below_the_exit_floor(self):
        c = cfg()
        action, _ = ps.decide_action(0.001, 0.02, c)
        self.assertEqual(action, "EXIT")

    def test_exit_via_delta_when_target_is_between_exit_below_and_min_position(self):
        """The distinguishing region the audit flagged as untested: target
        in [exit_below, min_position), held, where EXIT must come from the
        `delta < -band` disjunct rather than `target < floor` (which the
        test above exercises, with target 0.001 already below exit_below -
        the branch the old buggy code also happened to get right). A
        reverted fix that only checked `target < floor` would answer HOLD
        here instead of EXIT."""
        c = cfg()
        target, current = 0.003, 0.02
        self.assertTrue(c["exit_below"] <= target < c["min_position"],
                         "fixture numbers must sit inside the boundary "
                         "region for this test to mean anything")
        self.assertGreater(current, c["exit_below"])  # held
        self.assertLess(target - current, -c["rebalance_band"])  # 2nd disjunct
        action, delta = ps.decide_action(target, current, c)
        self.assertEqual(action, "EXIT")
        self.assertAlmostEqual(delta, 0.0 - current)

    def test_target_exactly_at_min_position_with_nothing_held_initiates(self):
        """Under the defaults min_position and rebalance_band are the same
        number (0.005), so target == min_position also sits exactly on the
        rebalance-band boundary. Not held, so the `target < min_position`
        branch must NOT be taken (0.005 is not < 0.005) and the position
        must INITIATE rather than fall through to WATCH."""
        c = cfg()
        self.assertEqual(c["min_position"], c["rebalance_band"])
        action, delta = ps.decide_action(c["min_position"], 0.0, c)
        self.assertEqual(action, "INITIATE")
        self.assertAlmostEqual(delta, c["min_position"])

    def test_current_exactly_at_exit_below_is_not_held(self):
        """The symmetric boundary: `held` is current > exit_below (strict),
        so a current sitting exactly ON exit_below is NOT held. With no
        target this must WATCH, not EXIT - EXIT is only for a position
        that IS held."""
        c = cfg()
        action, delta = ps.decide_action(0.0, c["exit_below"], c)
        self.assertEqual(action, "WATCH")
        self.assertAlmostEqual(delta, 0.0 - c["exit_below"])

    def test_delta_always_equals_target_minus_current(self):
        c = cfg()
        for target, current, blocked in ((0.03, 0.0, False), (0.0, 0.05, True),
                                         (0.021, 0.02, False), (0.0, 0.0, True)):
            _, delta = ps.decide_action(target, current, c, blocked=blocked)
            self.assertAlmostEqual(delta, target - current)

    def test_does_not_oscillate_forever_at_the_min_position_boundary(self):
        """A target hovering either side of min_position, with the resulting
        position carried forward into the next call, must settle rather than
        alternate INITIATE and EXIT forever - the historical bug the module's
        own comment describes ('a target wobbling either side of 0.5% would
        INITIATE and EXIT in turn'). Simulates 30 repeated runs feeding the
        prior action's outcome back in as the next call's `current`."""
        c = cfg()
        ladder = [0.0051, 0.0049] * 15
        current = 0.0
        actions = []
        for target in ladder:
            action, _ = ps.decide_action(target, current, c)
            actions.append(action)
            if action in ("INITIATE", "ADD", "TRIM"):
                current = target
            elif action == "EXIT":
                current = 0.0
            # HOLD / WATCH / NO_BET: the real position is unchanged.
        self.assertLessEqual(
            actions.count("INITIATE"), 1,
            "oscillated: INITIATE fired more than once for a target "
            "hovering around min_position - %r" % actions)
        self.assertLessEqual(
            actions.count("EXIT"), 1,
            "oscillated: EXIT fired more than once for a target hovering "
            "around min_position - %r" % actions)
        # And it must not still be trading on the very last step.
        self.assertIn(actions[-1], ("HOLD", "EXIT"))


# ---------------------------------------------------------------------------
# size(): end to end
# ---------------------------------------------------------------------------

class SizeEndToEnd(unittest.TestCase):
    def test_fixture_produces_ok_positive_target_bound_by_conviction(self):
        c = cfg()
        result = ps.size(base_request(), config=c)
        self.assertEqual(result["status"], ps.OK)
        self.assertGreater(result["target"], 0.0)
        self.assertEqual(result["binding_constraint"], "conviction")

    def test_no_scenarios_gives_insufficient_data_and_no_invented_probability(self):
        c = cfg()
        req = base_request()
        req["scenarios"] = None
        result = ps.size(req, config=c)
        self.assertEqual(result["status"], ps.INSUFFICIENT_DATA)
        self.assertEqual(result["target"], 0.0)
        self.assertIsNone(result["raw_kelly"])

    def test_invalid_scenario_distribution_raises_not_a_silent_zero(self):
        c = cfg()
        req = base_request()
        # Probabilities sum to 0.6, not 1: kelly.validate_scenarios refuses.
        req["scenarios"] = [{"name": "a", "probability": 0.3, "return": 0.1},
                            {"name": "b", "probability": 0.3, "return": -0.1}]
        with self.assertRaises(ps.SizingError):
            ps.size(req, config=c)


class SizeNoBetCases(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg()

    def test_no_bet_via_thesis_status_broken(self):
        req = base_request()
        req["thesis_status"] = "BROKEN"
        result = ps.size(req, config=self.cfg)
        self.assertEqual(result["status"], ps.NO_BET)
        self.assertEqual(result["target"], 0.0)

    def test_no_bet_via_thesis_broken_reason_code(self):
        req = base_request()
        req["reason_codes"] = [{"code": "THESIS_BROKEN", "severity": "WARN"}]
        result = ps.size(req, config=self.cfg)
        self.assertEqual(result["status"], ps.NO_BET)

    def test_no_bet_via_conflict_unresolved(self):
        req = base_request()
        req["reason_codes"] = [{"code": "CONFLICT_UNRESOLVED",
                                "severity": "WARN"}]
        result = ps.size(req, config=self.cfg)
        self.assertEqual(result["status"], ps.NO_BET)

    def test_no_bet_via_block_severity_reason_code(self):
        req = base_request()
        req["reason_codes"] = [{"code": "GATE_PRICE_STALE",
                                "severity": "BLOCK"}]
        result = ps.size(req, config=self.cfg)
        self.assertEqual(result["status"], ps.NO_BET)

    def test_no_bet_via_data_confidence_below_floor(self):
        req = base_request()
        req["confidence"]["data"] = 30  # floor is 40
        result = ps.size(req, config=self.cfg)
        self.assertEqual(result["status"], ps.NO_BET)

    def test_no_bet_via_conviction_very_low(self):
        req = base_request()
        req["conviction"] = "VERY LOW"
        result = ps.size(req, config=self.cfg)
        self.assertEqual(result["status"], ps.NO_BET)

    def test_no_bet_via_negative_expected_edge(self):
        req = base_request()
        req["scenarios"] = [{"name": "bear", "probability": 0.6,
                             "return": -0.30},
                            {"name": "bull", "probability": 0.4,
                             "return": 0.20}]
        result = ps.size(req, config=self.cfg)
        self.assertEqual(result["status"], ps.NO_BET)
        self.assertEqual(result["target"], 0.0)

    def test_no_bet_via_traded_value_below_universe_floor(self):
        req = base_request()
        req["liquidity"] = {"adv_sek": 100_000}  # below the 2,000,000 floor
        result = ps.size(req, config=self.cfg)
        self.assertEqual(result["status"], ps.NO_BET)

    def test_raw_kelly_still_reported_in_a_no_bet_case(self):
        """The 'Kelly suggests 4.2% BUT ...' shape: a refusal unrelated to
        the edge itself must not hide what the edge was worth."""
        req = base_request()
        req["thesis_status"] = "BROKEN"
        result = ps.size(req, config=self.cfg)
        self.assertEqual(result["status"], ps.NO_BET)
        self.assertEqual(result["target"], 0.0)
        self.assertIsNotNone(result["raw_kelly"])
        self.assertGreater(result["raw_kelly"], 0.0)

    def test_require_basis_refusal_still_reports_kelly_alongside_other_stops(self):
        """require_basis is evaluated alongside every other stop, not as an
        early return that would lose the Kelly figure and any other stop
        that also applied - the module's own docstring says the intended
        shape is 'Kelly suggests 4.2% BUT ...', not a bare early refusal."""
        strict = ps.load_config({"probability": {"require_basis": True}},
                                path="")
        req = base_request()
        req.pop("probability_basis", None)
        req["scenarios"] = [{k: v for k, v in s.items() if k != "basis"}
                            for s in ps._SCENARIOS]
        req["thesis_status"] = "BROKEN"  # a second, independent stop
        result = ps.size(req, config=strict)
        self.assertEqual(result["status"], ps.INSUFFICIENT_DATA)
        codes = {r["code"] for r in result["no_bet_reasons"]}
        self.assertIn("PROBABILITY_BASIS_MISSING", codes)
        self.assertIn("THESIS_BREAKER_ACTIVE", codes)
        self.assertIsNotNone(result["raw_kelly"])
        self.assertGreater(result["raw_kelly"], 0.0)


class ExitPersistsConsistentNumbers(unittest.TestCase):
    """When decide_action returns EXIT via the ordinary (non-blocked) path -
    a held position whose target has shrunk below the exit floor - size()
    must overwrite target to exactly 0.0 and delta to exactly -current, so
    the three numbers telemetry() persists always add up. Left at the
    computed (non-zero) target, the persisted record used to say
    target 0.28%, delta -0.40%, current 0.40% - three numbers that do not
    add up, stored forever."""

    def test_exit_forces_target_zero_and_delta_equals_minus_current(self):
        c = cfg()
        req = base_request()
        # Squeeze the liquidity cap far below both min_position and the
        # current holding, without tripping the LIQUIDITY_BELOW_FLOOR stop
        # (adv_sek stays well above the universe floor).
        req["portfolio"]["total_value_sek"] = 10_000_000_000
        req["portfolio"]["current_weight"] = 0.05
        for key in ("sector_weight", "country_weight", "gross_weight"):
            if req["portfolio"].get(key) is not None and \
                    req["portfolio"][key] < 0.05:
                req["portfolio"][key] = 0.05

        result = ps.size(req, config=c)
        self.assertEqual(result["status"], ps.OK)
        self.assertEqual(result["action"], "EXIT")
        self.assertEqual(result["target"], 0.0)
        self.assertEqual(result["delta"], -result["current"])

    def test_exit_from_inside_the_boundary_region_persists_a_consistent_triple(self):
        """A second, end-to-end EXIT case, distinct from the one above: here
        the pre-override (raw) target is confirmed to land INSIDE
        [exit_below, min_position) - not below exit_below - so EXIT can only
        have fired via the `delta < -band` disjunct in decide_action. size()
        must still force target to 0.0 and delta to -current, and the
        resulting triple must pass decision_record.normalise_position_sizing
        (the same identity check decision_record.validate() runs), exactly
        as the audit required."""
        c = cfg()
        req = base_request()
        req["portfolio"]["total_value_sek"] = 2_000_000_000
        req["portfolio"]["current_weight"] = 0.02
        req["liquidity"]["adv_sek"] = 10_500_000

        result = ps.size(req, config=c)

        applicable = [v for v in result["caps"].values() if v is not None]
        raw_capped = min([result["adjusted_kelly"]] + applicable
                          + [ps.HARD_MAX_SINGLE_POSITION])
        raw_target = raw_capped * result["concentration"]["multiplier"]
        self.assertTrue(
            c["exit_below"] <= raw_target < c["min_position"],
            "fixture drifted out of the boundary region under test: %r"
            % raw_target)

        self.assertEqual(result["status"], ps.OK)
        self.assertEqual(result["action"], "EXIT")
        self.assertEqual(result["target"], 0.0)
        self.assertAlmostEqual(result["delta"], -result["current"])

        decision_record = helpers.load("decision_record")
        normalised = decision_record.normalise_position_sizing({
            "action": result["action"], "target": result["target"],
            "current": result["current"], "delta": result["delta"]})
        self.assertEqual(normalised["action"], "EXIT")


# ---------------------------------------------------------------------------
# request_from_decision
# ---------------------------------------------------------------------------

def _decision_record():
    return {
        "identity": {"name": "Example AB"},
        "as_of": "2026-09-09",
        "depth": "STANDARD",
        "conviction": "HIGH",
        "reason_codes": [],
        "price": {"value": 100.0},
        "fair_value": {"bear": 70.0, "base_low": 108.0, "base_high": 122.0,
                       "bull": 150.0},
        "scenario_weights": {"bear": 0.25, "base": 0.50, "bull": 0.25},
    }


class RequestFromDecision(unittest.TestCase):
    def test_builds_scenarios_from_fair_value_and_weights_at_the_midpoint(self):
        rec = _decision_record()
        req = ps.request_from_decision(rec)
        names = {s["name"] for s in req["scenarios"]}
        self.assertEqual(names, {"bear", "base", "bull"})
        base_row = next(s for s in req["scenarios"] if s["name"] == "base")
        base_mid_price = 0.5 * (108.0 + 122.0)
        self.assertAlmostEqual(base_row["return"], base_mid_price / 100.0 - 1.0)
        self.assertEqual(req["valuation"]["base_low"], 108.0)
        self.assertEqual(req["valuation"]["base_high"], 122.0)

    def test_refuses_record_missing_price(self):
        rec = _decision_record()
        del rec["price"]
        with self.assertRaises(ps.SizingError):
            ps.request_from_decision(rec)

    def test_refuses_record_missing_fair_value_key(self):
        rec = _decision_record()
        del rec["fair_value"]["bull"]
        with self.assertRaises(ps.SizingError):
            ps.request_from_decision(rec)

    def test_refuses_record_missing_scenario_weight(self):
        rec = _decision_record()
        del rec["scenario_weights"]["base"]
        with self.assertRaises(ps.SizingError):
            ps.request_from_decision(rec)

    def test_refuses_a_fair_value_currency_that_differs_from_the_price(self):
        """A fair value in EUR against a price in SEK produces a scenario
        ladder of roughly -90% and a silent NO_BET; the reverse invents an
        enormous edge. Refused outright rather than assumed equal."""
        rec = _decision_record()
        rec["price"]["currency"] = "SEK"
        rec["fair_value"]["currency"] = "EUR"
        with self.assertRaises(ps.SizingError):
            ps.request_from_decision(rec)

    def test_accepts_a_matching_currency_pair(self):
        rec = _decision_record()
        rec["price"]["currency"] = "SEK"
        rec["fair_value"]["currency"] = "SEK"
        req = ps.request_from_decision(rec)
        self.assertEqual(len(req["scenarios"]), 3)


class RequestFromDecisionWeightSumAgreesWithDecisionRecord(unittest.TestCase):
    """A scenario-weight sum decision_record.validate() accepts (within its
    own WEIGHT_TOLERANCE) must never be refused by request_from_decision -
    the two modules must agree on what counts as 'close enough to 1'. Driven
    through decision_record.validate() first, exactly as a real caller
    would, then through request_from_decision and size()."""

    def test_a_sum_validate_accepts_sizes_without_raising(self):
        decision_record = helpers.load("decision_record")
        eps = decision_record.WEIGHT_TOLERANCE * 0.4
        rec = decision_record._sandvik_fixture()
        rec["scenario_weights"] = {"bear": 0.25, "base": 0.55 + eps,
                                   "bull": 0.20}
        self.assertGreater(
            abs(sum(rec["scenario_weights"].values()) - 1.0), 0.0,
            "test fixture must actually be off-by-a-hair for this test to "
            "mean anything")

        validated, _warnings = decision_record.validate(rec)

        req = ps.request_from_decision(validated)
        result = ps.size(req, config=cfg())
        self.assertIn(result["status"], (ps.OK, ps.NO_BET, ps.INSUFFICIENT_DATA))


# ---------------------------------------------------------------------------
# telemetry() and render()
# ---------------------------------------------------------------------------

class Telemetry(unittest.TestCase):
    def test_keys_are_exactly_the_documented_compact_set(self):
        result = ps.size(base_request(), config=cfg())
        expected = {
            "raw_kelly", "kelly_fraction", "fractional_kelly",
            "uncertainty_factor", "adjusted_kelly", "target", "current",
            "delta", "action", "binding_constraint", "status", "not_checked",
        }
        self.assertEqual(set(ps.telemetry(result).keys()), expected)


class Render(unittest.TestCase):
    def test_ok_case_contains_target_action_and_primary_constraint(self):
        result = ps.size(base_request(), config=cfg())
        text = ps.render(result)
        self.assertIn(result["action"], text)
        self.assertIn("Target:", text)
        self.assertIn("Primary constraint:", text)
        self.assertIn(result["binding_constraint"], text)

    def test_refusal_prints_reasons_and_the_overridden_kelly_figure(self):
        req = base_request()
        req["thesis_status"] = "BROKEN"
        result = ps.size(req, config=cfg())
        text = ps.render(result)
        self.assertIn("Refused:", text)
        for stop in result["no_bet_reasons"]:
            self.assertIn(stop["detail"], text)
        self.assertIn("Kelly would have suggested", text)


# ---------------------------------------------------------------------------
# CLI: main()
# ---------------------------------------------------------------------------

class MainRefusesBadStdinAndPayloadUsage(unittest.TestCase):
    """main() reads at most one input from stdin, and refuses a payload that
    is not a JSON object - both raised as SystemExit("DATA NOT AVAILABLE..."),
    never an uncaught traceback."""

    def test_two_dash_arguments_both_reading_stdin_refuses(self):
        stdin_text = json.dumps(ps.fixture())
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(stdin_text)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    ps.main(["-", "--portfolio", "-"])
        finally:
            sys.stdin = old_stdin
        self.assertIn("one input", str(caught.exception))

    def test_non_object_payload_refuses(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[1, 2, 3]")
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    ps.main([path])
            self.assertIn("JSON object", str(caught.exception))
        finally:
            os.remove(path)


class EmitRecordWritesAValidatablePositionSizing(unittest.TestCase):
    """--emit-record writes the original decision record back with a
    `position_sizing` key that decision_record.validate() then accepts -
    the whole point of --emit-record is to hand thesis_ledger.py something
    that survives the same validator every other record goes through."""

    def test_emitted_record_validates_with_the_position_sizing_key(self):
        decision_record = helpers.load("decision_record")
        rec = decision_record._sandvik_fixture()

        fd_in, in_path = tempfile.mkstemp(suffix=".json")
        os.close(fd_in)
        fd_out, out_path = tempfile.mkstemp(suffix=".json")
        os.close(fd_out)
        try:
            with open(in_path, "w", encoding="utf-8") as fh:
                json.dump(rec, fh)

            with contextlib.redirect_stdout(io.StringIO()):
                code = ps.main(["--from-decision", in_path,
                               "--emit-record", out_path, "--json"])
            self.assertEqual(code, 0)

            with open(out_path, encoding="utf-8") as fh:
                out_rec = json.load(fh)
            self.assertIn("position_sizing", out_rec)

            validated, _warnings = decision_record.validate(out_rec)
            self.assertIn(validated["position_sizing"]["action"],
                          decision_record.POSITION_SIZING_ACTIONS)
        finally:
            os.remove(in_path)
            os.remove(out_path)


if __name__ == "__main__":
    unittest.main()
