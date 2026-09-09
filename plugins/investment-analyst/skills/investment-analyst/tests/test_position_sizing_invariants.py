#!/usr/bin/env python3
"""Position sizing: invariant tests, not example tests.

WHY THIS FILE EXISTS

test_position_sizing.py (written separately) checks that position_sizing.py
gets specific worked examples right. An example test passes on the one case
someone thought of - a bear/base/bull at 25/50/25, a HIGH conviction, a
sector at 18% - and says nothing about the thousands of other combinations
the module will actually be fed. A capital-allocation engine that is wrong
"only" on inputs nobody enumerated in a fixture is still wrong: it still
pays out a bad position size, just on a Tuesday nobody tested.

So this file states the properties position_sizing.size() must hold no
matter what is thrown at it - target is never negative, a hard cap can never
be walked past by configuration, lower confidence can never buy a larger
position, an active thesis breaker can never say ADD - and then sweeps a
deterministic grid of inputs (itertools.product, never random.random()
un-seeded) checking every combination rather than one hand-picked case.
Where a distribution of scenarios is randomised for coverage, it is seeded
with random.Random(12345) so a failure reproduces bit for bit.

Every invariant is checked against config loaded with load_config(path="")
so a real ~/.investment-analyst config on the machine running the suite can
never change whether these tests pass.

If a swept combination breaks an invariant, that is a defect in
position_sizing.py, not in the test. This file does not weaken an
assertion to make a red case go away.
"""
from __future__ import annotations

import copy
import itertools
import json
import math
import random
import unittest

import helpers

helpers.bootstrap_path()

ps = helpers.load("position_sizing")
kelly_mod = helpers.load("kelly")
scoring = helpers.load("forecast_scoring")

CFG = ps.load_config(path="")  # never read a real config in these tests


# --------------------------------------------------------------------------
# Request builder. Every invariant sweep goes through this so the grids stay
# readable: only the dimension under test is spelled out at the call site.
# --------------------------------------------------------------------------

DEFAULT_SCENARIOS = [
    {"name": "bear", "probability": 0.25, "return": -0.30},
    {"name": "base", "probability": 0.50, "return": 0.15},
    {"name": "bull", "probability": 0.25, "return": 0.50},
]

# A thinner edge whose raw Kelly is an interior root rather than clamped at
# full capital - useful where a test wants headroom for the caps to bind
# without a clamp already hiding it.
THIN_SCENARIOS = [
    {"name": "bear", "probability": 0.35, "return": -0.50},
    {"name": "base", "probability": 0.45, "return": 0.15},
    {"name": "bull", "probability": 0.20, "return": 0.60},
]

# Non-positive expected return, still a valid distribution (probabilities
# sum to 1, has a downside scenario) so kelly_scenarios does not raise.
ZERO_EDGE_SCENARIOS = [
    {"name": "up", "probability": 0.5, "return": 0.20},
    {"name": "down", "probability": 0.5, "return": -0.20},
]
NEGATIVE_EDGE_SCENARIOS = [
    {"name": "bear", "probability": 0.6, "return": -0.30},
    {"name": "bull", "probability": 0.4, "return": 0.20},
]
NEGATIVE_EDGE_SCENARIOS_2 = [
    {"name": "bear", "probability": 0.7, "return": -0.10},
    {"name": "base", "probability": 0.2, "return": 0.0},
    {"name": "bull", "probability": 0.1, "return": 0.05},
]
# No downside at all -> INSUFFICIENT_DATA, not a guess. Still relevant to
# "must never be positive": it must be exactly zero.
NO_DOWNSIDE_SCENARIOS = [
    {"name": "a", "probability": 0.5, "return": 0.10},
    {"name": "b", "probability": 0.5, "return": 0.30},
]

EDGE_LE_ZERO_SCENARIO_SETS = (
    ZERO_EDGE_SCENARIOS, NEGATIVE_EDGE_SCENARIOS, NEGATIVE_EDGE_SCENARIOS_2,
)

# Same shape as DEFAULT_SCENARIOS / THIN_SCENARIOS but with a basis on every
# row, so probability_support() reports SUPPORTED rather than UNKNOWN. Used
# to isolate the effect of a missing basis from every other input.
SUPPORTED_DEFAULT_SCENARIOS = [dict(row, basis="x") for row in DEFAULT_SCENARIOS]
SUPPORTED_THIN_SCENARIOS = [dict(row, basis="x") for row in THIN_SCENARIOS]

# A mutation-testing audit found that most of this file's "monotone" sweeps
# were statistically vacuous: DEFAULT_SCENARIOS always lands on a target
# pinned by a portfolio/conviction cap (adjusted Kelly is far above every
# cap, so the cap always wins) and THIN_SCENARIOS always lands on a target
# pinned by the uncertainty floor (raw Kelly is thin enough that the
# uncertainty product never clears cfg.uncertainty.floor=0.35). Neither
# leaves the *adjusted Kelly* itself as the binding constraint, so sweeping
# confidence, liquidity or overlap could not move the answer - the
# assertions held, but only because every combination landed on the same
# number. This fixture is a middling case, found empirically (see
# tests/probe scripts referenced in the PR that added it): a real but
# modest edge (raw_kelly ~= 0.34) with a genuine downside (worst = -25%),
# tuned so that for the MEDIUM/HIGH/VERY HIGH conviction rungs the adjusted
# Kelly - not a cap, not the floor - sets the target over a useful part of
# the grid, so confidence, liquidity and overlap sweeps built on it actually
# move the number.
MODERATE_SCENARIOS = [
    {"name": "bear", "probability": 0.30, "return": -0.25},
    {"name": "base", "probability": 0.45, "return": 0.08},
    {"name": "bull", "probability": 0.25, "return": 0.20},
]
# Same shape, with a stated basis on every row. Without one, probability
# support is UNKNOWN, and the probability-support and incomplete-inputs
# charges alone are enough to floor the uncertainty factor at
# cfg.uncertainty.floor for the *entire* confidence.thesis / .valuation
# range - which would silently reproduce the exact flatness this fixture
# exists to avoid. A stated basis is what frees the uncertainty factor from
# the floor so a confidence sweep has anything to move.
SUPPORTED_MODERATE_SCENARIOS = [dict(row, basis="x") for row in MODERATE_SCENARIOS]
# The four bucket caps isolated to None, for a base request built on
# MODERATE_SCENARIOS: sector/country/gross/cash all default to nonzero
# weights in request(), any one of which can bind ahead of the Kelly figure
# and hide the movement this fixture exists to show.
ISOLATE_PORTFOLIO_CAPS = dict(sector_weight=None, country_weight=None,
                              gross_weight=None, cash_weight=None)


def _bear_ladder_scenarios(bear_return):
    """THIN_SCENARIOS-shaped distribution with only the bear return varied.

    Lets a sweep deepen the bear case one lever at a time without touching
    the probabilities, for the risk-budget invariants.
    """
    return [
        {"name": "bear", "probability": 0.35, "return": bear_return},
        {"name": "base", "probability": 0.45, "return": 0.15},
        {"name": "bull", "probability": 0.20, "return": 0.60},
    ]


def request(**overrides):
    """A complete, valid sizing request with every field defaulted sanely.

    Keyword overrides address the flattened field, not the nested shape
    (e.g. sector_weight=, not portfolio={"sector_weight": ...}), so a sweep
    reads as request(conviction=c, sector_weight=w) rather than a wall of
    nested dict literals.
    """
    scenarios = overrides.pop("scenarios", DEFAULT_SCENARIOS)
    reason_codes = overrides.pop("reason_codes", [])
    explicit_buckets = {key for key in
                        ("sector_weight", "country_weight", "gross_weight")
                        if key in overrides}
    req = {
        "instrument": overrides.pop("instrument", "TEST AB"),
        "as_of": overrides.pop("as_of", "2026-09-09"),
        "depth": overrides.pop("depth", None),
        "scenarios": scenarios,
        "conviction": overrides.pop("conviction", "HIGH"),
        "reason_codes": reason_codes,
        "thesis_status": overrides.pop("thesis_status", None),
        "confidence": {
            "data": overrides.pop("confidence_data", 80),
            "thesis": overrides.pop("confidence_thesis", 0.7),
            "valuation": overrides.pop("confidence_valuation", 0.6),
        },
        "valuation": {
            "price": overrides.pop("price", 100.0),
            "base_low": overrides.pop("base_low", 108.0),
            "base_high": overrides.pop("base_high", 122.0),
        },
        "liquidity": {
            "adv_sek": overrides.pop("adv_sek", 40_000_000),
            "turnover_sek": overrides.pop("turnover_sek", None),
            "market_cap_sek": overrides.pop("market_cap_sek", 9_000_000_000),
        },
        "portfolio": {
            "total_value_sek": overrides.pop("total_value_sek", 2_000_000),
            "current_weight": overrides.pop("current_weight", 0.02),
            "sector": overrides.pop("sector", "Industrials"),
            "sector_weight": overrides.pop("sector_weight", 0.18),
            "country": overrides.pop("country", "SE"),
            "country_weight": overrides.pop("country_weight", 0.55),
            "gross_weight": overrides.pop("gross_weight", 0.95),
            "cash_weight": overrides.pop("cash_weight", 0.05),
            "shares_sector_with": overrides.pop("shares_sector_with", 0),
            "shares_driver_with": overrides.pop("shares_driver_with", 0),
        },
    }
    if overrides:
        raise TypeError("unknown request() overrides: %s" % sorted(overrides))

    # A bucket weight must include this position, and the engine now refuses
    # an input where it does not. A sweep over current_weight would otherwise
    # be testing that refusal rather than the invariant it names, so the
    # defaulted buckets grow to stay consistent with whatever weight the sweep
    # picked. An explicitly overridden bucket is left exactly as given, so a
    # test that means to supply a contradictory pair still can.
    pf = req["portfolio"]
    for key in ("sector_weight", "country_weight", "gross_weight"):
        if key in explicit_buckets:
            continue
        if pf[key] is not None and pf["current_weight"] > pf[key]:
            pf[key] = pf["current_weight"]
    return req


def _bucket(value, current):
    """A swept bucket weight, kept consistent with the swept position weight.

    A sector weight below the position's own weight is a contradiction the
    engine refuses, so feeding one into a sweep would test the refusal instead
    of the invariant. Raising the bucket to the position keeps every
    combination valid without dropping any of them - skipping them would be
    the weaker fix, since the skipped cells are the crowded ones.
    """
    return None if value is None else max(value, current)


CONVICTIONS_NO_NONE = tuple(ps.CONVICTIONS)
LEVEL_INDEX = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def _find_keys_containing(obj, needle, path=""):
    """Walk a nested dict/list, listing the path of every key containing
    `needle` (case-insensitive). Used to prove a figure was never invented -
    e.g. that no key naming a correlation appears anywhere in a result."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = "%s.%s" % (path, k) if path else str(k)
            if needle.lower() in str(k).lower():
                found.append(here)
            found.extend(_find_keys_containing(v, needle, here))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(_find_keys_containing(v, needle, "%s[%d]" % (path, i)))
    return found


class PositionSizingInvariants(unittest.TestCase):

    # ----------------------------------------------------------------
    # 1. Target is never negative, over a pathological grid.
    # ----------------------------------------------------------------
    def test_target_never_negative(self):
        """final target >= 0 for every swept combination, with the non-refused region checked separately so the invariant is not only ever exercised on zeros."""
        conviction_opts = (None,) + CONVICTIONS_NO_NONE
        scenario_opts = (DEFAULT_SCENARIOS, THIN_SCENARIOS, ZERO_EDGE_SCENARIOS,
                         NEGATIVE_EDGE_SCENARIOS, NO_DOWNSIDE_SCENARIOS, None)
        # "INTACT" used to sit here as if it were a recognised third thesis
        # status. normalise_request only ever checks for the literal string
        # "BROKEN" (position_sizing.py's normalise_request /
        # no_bet_reasons); any other non-empty string, "INTACT" included,
        # silently collapses onto the same behaviour as None. That grid cell
        # was not testing what its name implied, so only the two thesis
        # states this module actually distinguishes are swept here.
        thesis_opts = (None, "BROKEN")
        # Every value here is inside the input contract. Values outside it -
        # a weight of 150, a data score of -50 or 200 - are refused rather
        # than coerced, and that refusal is asserted separately below; a
        # sweep that fed them in was testing the coercion, not the invariant.
        current_weight_opts = (0.0, 0.02, 0.5, 1.0)
        confidence_data_opts = (0, 40, 100)
        n = 0
        n_ok = 0
        ok_targets = []
        for conv, scen, thesis, cw, cd in itertools.product(
                conviction_opts, scenario_opts, thesis_opts,
                current_weight_opts, confidence_data_opts):
            n += 1
            req = request(conviction=conv, scenarios=scen, thesis_status=thesis,
                         current_weight=cw, confidence_data=cd)
            result = ps.size(req, config=CFG)
            self.assertGreaterEqual(
                result["target"], 0.0,
                "negative target for conviction=%r scenarios=%r thesis=%r "
                "current_weight=%r confidence_data=%r -> %r"
                % (conv, scen, thesis, cw, cd, result))
            if result["status"] == ps.OK:
                n_ok += 1
                ok_targets.append(result["target"])
        self.assertGreater(n, 100)
        # Most of the grid above refuses outright (target pinned at exactly
        # 0), so ">= 0" is trivially true there and proves nothing on its
        # own. The invariant only says something for real in the
        # non-refused region - assert that region actually exists and is a
        # meaningful slice of the sweep, not a handful of stragglers.
        self.assertGreater(
            n_ok, 20,
            "only %d of %d swept combinations reached status=ok - the >= 0 "
            "check above was exercised almost entirely on refusals (target "
            "pinned at 0), which is not a check on this invariant" % (n_ok, n))
        for t in ok_targets:
            self.assertGreaterEqual(t, 0.0)
        self.assertTrue(
            any(t > 0.0 for t in ok_targets),
            "every non-refused (status=ok) combination still sized to "
            "exactly 0.0 - the non-refusal region is itself degenerate")

    def test_out_of_contract_inputs_are_refused_not_coerced(self):
        """A weight or a confidence off its declared scale must raise, never
        be quietly rescaled into a different position size."""
        for kwargs in ({"current_weight": 150.0},
                       {"current_weight": 2.0},
                       {"confidence_data": -50},
                       {"confidence_data": 200},
                       {"confidence_thesis": 70},
                       {"confidence_valuation": 60}):
            with self.assertRaises(ps.SizingError, msg=repr(kwargs)):
                ps.size(request(**kwargs), config=CFG)

    # ----------------------------------------------------------------
    # 2. Target never exceeds any reported cap or the hard single-name cap.
    # ----------------------------------------------------------------
    def test_target_never_exceeds_reported_caps_or_hard_cap(self):
        """final target <= every non-None value in caps, and <= HARD_MAX_SINGLE_POSITION."""
        conviction_opts = CONVICTIONS_NO_NONE
        scenario_opts = (DEFAULT_SCENARIOS, THIN_SCENARIOS)
        sector_weight_opts = (None, 0.0, 0.10, 0.25, 0.40)
        country_weight_opts = (None, 0.0, 0.90)
        gross_weight_opts = (None, 0.5, 0.99)
        current_weight_opts = (0.0, 0.02, 0.09)
        n = 0
        for conv, scen, sw, cow, gw, cw in itertools.product(
                conviction_opts, scenario_opts, sector_weight_opts,
                country_weight_opts, gross_weight_opts, current_weight_opts):
            n += 1
            req = request(conviction=conv, scenarios=scen,
                         sector_weight=_bucket(sw, cw),
                         country_weight=_bucket(cow, cw),
                         gross_weight=_bucket(gw, cw),
                         current_weight=cw)
            result = ps.size(req, config=CFG)
            target = result["target"]
            self.assertLessEqual(
                target, ps.HARD_MAX_SINGLE_POSITION + 1e-12,
                "target exceeds hard cap for %r -> %r" % (req, result))
            for cap_name, cap_value in result["caps"].items():
                if cap_value is None:
                    continue
                self.assertLessEqual(
                    target, cap_value + 1e-9,
                    "target %r exceeds cap %s=%r for %r"
                    % (target, cap_name, cap_value, req))
        self.assertGreater(n, 100)

    # ----------------------------------------------------------------
    # 3. The conviction ceiling - not the 10% hard cap - is the true
    # maximum reachable through the public path, even when config tries to
    # raise every limit.
    #
    # This used to be named test_hard_caps_bind_even_when_config_maximised
    # and asserted only target <= HARD_MAX_SINGLE_POSITION. That assertion
    # is real but was never close to being exercised: the conviction ladder
    # tops out at CONVICTION_POSITION_CEILING["VERY HIGH"] == 0.08, which no
    # config override can raise (it is a module constant, not a config
    # value - see conviction_cap()), and concentration only ever multiplies
    # down from there. So even with every configurable limit maximised, the
    # measured target never got within about 4 percentage points of the
    # 0.10 hard cap it claimed to be testing. The HARD_MAX_SINGLE_POSITION
    # assertion is kept below as defence in depth, but the assertion that
    # actually says something about this maximised config is the one
    # against the conviction ceiling.
    # ----------------------------------------------------------------
    def test_conviction_ceiling_is_the_true_maximum_even_when_config_maximised(self):
        """with every configurable limit maximised, target never exceeds CONVICTION_POSITION_CEILING[conviction], and gets right up against it once concentration is isolated."""
        loose_overrides = {
            "kelly_fraction": 0.99,
            "kelly_fraction_by_conviction": {level: 0.99 for level in ps.CONVICTIONS},
            "allow_full_kelly": True,
            "uncertainty": {"floor": 0.99, "robustness_floor": 0.99,
                           "estimate_width_floor": 0.99, "thesis_floor": 0.99,
                           "incomplete_inputs": 0.99},
            "liquidity": {"participation_rate": 0.99,
                         "microcap_max_position": 0.99},
            "portfolio": {"max_single_position": 0.99, "max_sector_exposure": 0.99,
                         "max_country_exposure": 0.99, "max_gross_exposure": 0.99},
            "concentration": {"low": 0.99, "medium": 0.99, "high": 0.99,
                             "unknown": 0.99},
        }
        loose_cfg = ps.load_config(loose_overrides, path="")
        # The harden() clamp itself must hold on the config values.
        self.assertLessEqual(loose_cfg["portfolio"]["max_single_position"],
                             ps.HARD_MAX_SINGLE_POSITION)
        self.assertLessEqual(loose_cfg["kelly_fraction"], 1.0)

        conviction_opts = CONVICTIONS_NO_NONE
        scenario_opts = (DEFAULT_SCENARIOS, THIN_SCENARIOS)
        current_weight_opts = (0.0, 0.02, 0.08)
        n = 0
        for conv, scen, cw in itertools.product(
                conviction_opts, scenario_opts, current_weight_opts):
            n += 1
            req = request(conviction=conv, scenarios=scen, current_weight=cw)
            result = ps.size(req, config=loose_cfg)
            # Defence in depth: kept from the original test, still true, but
            # - see the docstring above - never close to binding through the
            # public path.
            self.assertLessEqual(
                result["target"], ps.HARD_MAX_SINGLE_POSITION + 1e-12,
                "hard cap breached with maximised config for %r -> %r"
                % (req, result))
            # The assertion that actually says something for this
            # maximised config: the conviction ceiling is a module
            # constant no config override can touch, so it is the real
            # maximum reachable here, well inside the 10% hard cap.
            self.assertLessEqual(
                result["target"], ps.CONVICTION_POSITION_CEILING[conv] + 1e-9,
                "target %r exceeded the conviction ceiling %r for %r -> %r "
                "even with every configurable limit maximised"
                % (result["target"], ps.CONVICTION_POSITION_CEILING[conv],
                   req, result))
        self.assertGreater(n, 10)

        # Proof, not just an upper bound: with concentration isolated (no
        # portfolio context to grade overlap against), a maximised config
        # drives the target to within 1% of the conviction ceiling itself -
        # the ceiling is not merely never exceeded, it is the thing that
        # actually binds.
        isolated_req = request(conviction="LOW", scenarios=DEFAULT_SCENARIOS,
                               current_weight=0.0, **ISOLATE_PORTFOLIO_CAPS)
        isolated_result = ps.size(isolated_req, config=loose_cfg)
        ceiling = ps.CONVICTION_POSITION_CEILING["LOW"]
        self.assertEqual(
            isolated_result["binding_constraint"], "conviction",
            "expected the conviction cap to bind with concentration "
            "isolated and every other limit maximised, got %r -> %r"
            % (isolated_result["binding_constraint"], isolated_result))
        self.assertGreaterEqual(
            isolated_result["target"], 0.99 * ceiling,
            "target %r stayed well below the conviction ceiling %r even "
            "with concentration isolated and every configurable limit "
            "maximised - the ceiling never actually got exercised"
            % (isolated_result["target"], ceiling))

    # ----------------------------------------------------------------
    # 4. Monotone non-increasing in confidence.
    # ----------------------------------------------------------------
    def test_monotone_in_thesis_confidence(self):
        """lowering confidence.thesis must never increase the target, and must actually move it on at least one base."""
        ladder = (1.0, 0.8, 0.6, 0.4, 0.2, 0.0)  # descending
        base_requests = [
            dict(conviction="HIGH", scenarios=DEFAULT_SCENARIOS, current_weight=0.02),
            dict(conviction="MEDIUM", scenarios=THIN_SCENARIOS, current_weight=0.0),
            dict(conviction="VERY HIGH", scenarios=DEFAULT_SCENARIOS,
                current_weight=0.05, sector_weight=0.20),
            # DEFAULT_SCENARIOS above is cap-pinned and THIN_SCENARIOS is
            # floor-pinned (see MODERATE_SCENARIOS' docstring), so neither
            # ladder can move at all - confidence.thesis is not the binding
            # constraint for either. This base sits on the adjusted-Kelly
            # path instead, with confidence.valuation held generously high
            # so it does not itself floor the uncertainty factor.
            dict(conviction="MEDIUM", scenarios=SUPPORTED_MODERATE_SCENARIOS,
                current_weight=0.0, confidence_valuation=0.9,
                **ISOLATE_PORTFOLIO_CAPS),
        ]
        for base in base_requests:
            targets = []
            for conf in ladder:
                req = request(confidence_thesis=conf, **base)
                targets.append(ps.size(req, config=CFG)["target"])
            for i in range(1, len(targets)):
                self.assertLessEqual(
                    targets[i], targets[i - 1] + 1e-12,
                    "target rose as thesis confidence fell %r -> %r on "
                    "ladder %r for base %r"
                    % (ladder[i - 1], ladder[i], ladder, base))
            if base["scenarios"] is SUPPORTED_MODERATE_SCENARIOS:
                self.assertGreater(
                    len(set(round(t, 9) for t in targets)), 1,
                    "the confidence.thesis ladder produced a single "
                    "constant target %r across the whole sweep for base "
                    "%r - a monotone test that only ever sees a constant "
                    "proves nothing about monotonicity" % (targets[0], base))

    def test_monotone_in_valuation_confidence(self):
        """lowering confidence.valuation must never increase the target, and must actually move it on at least one base."""
        ladder = (1.0, 0.8, 0.6, 0.4, 0.2, 0.0)  # descending
        base_requests = [
            dict(conviction="HIGH", scenarios=DEFAULT_SCENARIOS, current_weight=0.02),
            dict(conviction="MEDIUM", scenarios=THIN_SCENARIOS, current_weight=0.0),
            # See test_monotone_in_thesis_confidence: DEFAULT_SCENARIOS and
            # THIN_SCENARIOS above are both pinned off the adjusted-Kelly
            # path, so this base is what actually exercises the invariant.
            dict(conviction="VERY HIGH", scenarios=SUPPORTED_MODERATE_SCENARIOS,
                current_weight=0.0, confidence_thesis=0.9,
                **ISOLATE_PORTFOLIO_CAPS),
        ]
        for base in base_requests:
            targets = []
            for conf in ladder:
                req = request(confidence_valuation=conf, **base)
                targets.append(ps.size(req, config=CFG)["target"])
            for i in range(1, len(targets)):
                self.assertLessEqual(
                    targets[i], targets[i - 1] + 1e-12,
                    "target rose as valuation confidence fell %r -> %r on "
                    "ladder %r for base %r"
                    % (ladder[i - 1], ladder[i], ladder, base))
            if base["scenarios"] is SUPPORTED_MODERATE_SCENARIOS:
                self.assertGreater(
                    len(set(round(t, 9) for t in targets)), 1,
                    "the confidence.valuation ladder produced a single "
                    "constant target %r across the whole sweep for base "
                    "%r - a monotone test that only ever sees a constant "
                    "proves nothing about monotonicity" % (targets[0], base))

    # ----------------------------------------------------------------
    # 5. Non-positive expected edge never produces a positive target.
    # ----------------------------------------------------------------
    def test_nonpositive_edge_never_sizes(self):
        """expected return <= 0 must never produce a positive target."""
        conviction_opts = (None,) + CONVICTIONS_NO_NONE
        current_weight_opts = (0.0, 0.02, 0.5)
        n = 0
        for scen, conv, cw in itertools.product(
                EDGE_LE_ZERO_SCENARIO_SETS, conviction_opts, current_weight_opts):
            n += 1
            req = request(scenarios=scen, conviction=conv, current_weight=cw)
            result = ps.size(req, config=CFG)
            self.assertEqual(
                result["target"], 0.0,
                "non-positive edge produced target %r for %r"
                % (result["target"], req))
        self.assertGreater(n, 20)

    # ----------------------------------------------------------------
    # 6. Active thesis breaker never INITIATE / ADD.
    # ----------------------------------------------------------------
    def test_active_thesis_breaker_never_initiates_or_adds(self):
        """an active thesis breaker must never produce INITIATE or ADD."""
        conviction_opts = (None,) + CONVICTIONS_NO_NONE
        current_weight_opts = (0.0, 0.005, 0.02, 0.08)
        confidence_thesis_opts = (0.1, 0.5, 0.9)
        n = 0
        for conv, cw, ct in itertools.product(
                conviction_opts, current_weight_opts, confidence_thesis_opts):
            n += 1
            req = request(conviction=conv, current_weight=cw,
                         confidence_thesis=ct, thesis_status="BROKEN")
            result = ps.size(req, config=CFG)
            self.assertNotIn(
                result["action"], ("INITIATE", "ADD"),
                "broken thesis produced action %r for %r"
                % (result["action"], req))
        self.assertGreater(n, 20)

        # Same, triggered via the reason code rather than thesis_status.
        for conv, cw in itertools.product(conviction_opts, current_weight_opts):
            req = request(conviction=conv, current_weight=cw,
                         reason_codes=[{"code": "THESIS_BROKEN",
                                       "severity": "WARN"}])
            result = ps.size(req, config=CFG)
            self.assertNotIn(result["action"], ("INITIATE", "ADD"),
                             "THESIS_BROKEN reason code produced action %r "
                             "for %r" % (result["action"], req))

    # ----------------------------------------------------------------
    # 7. Monotone non-increasing in concentration; level non-decreasing.
    # ----------------------------------------------------------------
    def test_monotone_in_sector_weight_overlap(self):
        """raising sector_weight must never increase target; level never falls."""
        ladder = (0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40)  # ascending
        base_requests = [
            dict(conviction="HIGH", scenarios=DEFAULT_SCENARIOS, current_weight=0.0),
            dict(conviction="VERY HIGH", scenarios=THIN_SCENARIOS, current_weight=0.02),
        ]
        for base in base_requests:
            targets, levels = [], []
            for sw in ladder:
                req = request(
                    sector_weight=_bucket(sw, base["current_weight"]),
                    **base)
                result = ps.size(req, config=CFG)
                targets.append(result["target"])
                levels.append(LEVEL_INDEX[result["concentration"]["level"]])
            for i in range(1, len(targets)):
                self.assertLessEqual(
                    targets[i], targets[i - 1] + 1e-12,
                    "target rose as sector_weight rose %r -> %r for base %r"
                    % (ladder[i - 1], ladder[i], base))
                self.assertGreaterEqual(
                    levels[i], levels[i - 1],
                    "concentration level fell as sector_weight rose %r -> "
                    "%r for base %r" % (ladder[i - 1], ladder[i], base))

    def test_monotone_in_shares_sector_with(self):
        """raising shares_sector_with must never increase target; level never falls; and target must actually move on at least one base."""
        ladder = (0, 1, 2, 3, 5)  # ascending
        base_requests = [
            dict(conviction="HIGH", scenarios=DEFAULT_SCENARIOS, current_weight=0.0),
            dict(conviction="VERY HIGH", scenarios=THIN_SCENARIOS,
                current_weight=0.0, sector_weight=0.10),
            # See MODERATE_SCENARIOS' docstring: the two bases above are
            # cap-pinned / floor-pinned and cannot move. country/gross/cash
            # are isolated so only the swept overlap count can change the
            # concentration multiplier that acts on the adjusted Kelly.
            dict(conviction="HIGH", scenarios=MODERATE_SCENARIOS,
                current_weight=0.0, sector_weight=0.10, country_weight=None,
                gross_weight=None, cash_weight=None),
        ]
        for base in base_requests:
            targets, levels = [], []
            for n_shared in ladder:
                req = request(shares_sector_with=n_shared, **base)
                result = ps.size(req, config=CFG)
                targets.append(result["target"])
                levels.append(LEVEL_INDEX[result["concentration"]["level"]])
            for i in range(1, len(targets)):
                self.assertLessEqual(
                    targets[i], targets[i - 1] + 1e-12,
                    "target rose as shares_sector_with rose %r -> %r for "
                    "base %r" % (ladder[i - 1], ladder[i], base))
                self.assertGreaterEqual(
                    levels[i], levels[i - 1],
                    "concentration level fell as shares_sector_with rose "
                    "%r -> %r for base %r" % (ladder[i - 1], ladder[i], base))
            if base["scenarios"] is MODERATE_SCENARIOS:
                self.assertGreater(
                    len(set(round(t, 9) for t in targets)), 1,
                    "the shares_sector_with ladder produced a single "
                    "constant target %r across the whole sweep for base "
                    "%r" % (targets[0], base))

    def test_monotone_in_shares_driver_with(self):
        """raising shares_driver_with must never increase target; level never falls; and target must actually move on at least one base."""
        ladder = (0, 1, 2, 3, 5)  # ascending
        base_requests = [
            dict(conviction="HIGH", scenarios=DEFAULT_SCENARIOS, current_weight=0.0),
            dict(conviction="VERY HIGH", scenarios=THIN_SCENARIOS,
                current_weight=0.0, sector_weight=0.10),
            # See test_monotone_in_shares_sector_with above.
            dict(conviction="HIGH", scenarios=MODERATE_SCENARIOS,
                current_weight=0.0, sector_weight=0.10, country_weight=None,
                gross_weight=None, cash_weight=None),
        ]
        for base in base_requests:
            targets, levels = [], []
            for n_shared in ladder:
                req = request(shares_driver_with=n_shared, **base)
                result = ps.size(req, config=CFG)
                targets.append(result["target"])
                levels.append(LEVEL_INDEX[result["concentration"]["level"]])
            for i in range(1, len(targets)):
                self.assertLessEqual(
                    targets[i], targets[i - 1] + 1e-12,
                    "target rose as shares_driver_with rose %r -> %r for "
                    "base %r" % (ladder[i - 1], ladder[i], base))
                self.assertGreaterEqual(
                    levels[i], levels[i - 1],
                    "concentration level fell as shares_driver_with rose "
                    "%r -> %r for base %r" % (ladder[i - 1], ladder[i], base))
            if base["scenarios"] is MODERATE_SCENARIOS:
                self.assertGreater(
                    len(set(round(t, 9) for t in targets)), 1,
                    "the shares_driver_with ladder produced a single "
                    "constant target %r across the whole sweep for base "
                    "%r" % (targets[0], base))

    # ----------------------------------------------------------------
    # 8. Monotone non-decreasing in conviction, all else equal.
    #
    # Both the conviction position ceiling and kelly_fraction_by_conviction
    # rise (or stay flat) with conviction, so this SHOULD hold end to end.
    # If a swept combination breaks it, that is a real finding to report,
    # not a reason to weaken the assertion.
    # ----------------------------------------------------------------
    def test_monotone_in_conviction(self):
        """higher conviction must never produce a smaller target, all else equal."""
        scenario_opts = (DEFAULT_SCENARIOS, THIN_SCENARIOS)
        current_weight_opts = (0.0, 0.02, 0.06)
        confidence_thesis_opts = (0.3, 0.9)
        sector_weight_opts = (None, 0.10)
        failures = []
        n = 0
        for scen, cw, ct, sw in itertools.product(
                scenario_opts, current_weight_opts, confidence_thesis_opts,
                sector_weight_opts):
            targets = []
            for conv in CONVICTIONS_NO_NONE:  # VERY LOW..VERY HIGH, ascending
                req = request(conviction=conv, scenarios=scen, current_weight=cw,
                             confidence_thesis=ct, sector_weight=sw)
                targets.append(ps.size(req, config=CFG)["target"])
            for i in range(1, len(targets)):
                n += 1
                if targets[i] < targets[i - 1] - 1e-12:
                    failures.append(
                        (CONVICTIONS_NO_NONE[i - 1], CONVICTIONS_NO_NONE[i],
                         targets[i - 1], targets[i],
                         dict(scenarios=scen, current_weight=cw,
                             confidence_thesis=ct, sector_weight=sw)))
        self.assertGreater(n, 20)
        self.assertEqual(
            failures, [],
            "conviction is not monotone in target for these combinations "
            "(lower_conv, higher_conv, target_lower, target_higher, rest of "
            "request): %r" % (failures,))

    # ----------------------------------------------------------------
    # 9. Tightening any single cap must never increase the target.
    # ----------------------------------------------------------------
    def test_tightening_any_single_cap_never_increases_target(self):
        """tightening one config cap at a time must never increase the target; where the tightened value is meant to be the binding constraint, it must actually strictly reduce the target and bind."""
        # (override path as nested dict, request kwargs that make that cap
        # or factor relevant, optional expected binding_constraint after
        # tightening). A mutation-testing audit found three of these ten
        # cases had literally no effect - kelly_fraction with
        # conviction=None, liquidity.participation_rate, and
        # uncertainty.floor - because a *different*, unrelated cap (usually
        # the default request()'s gross or cash bucket weight, or too loose
        # an adv/total_value ratio) bound ahead of the one under test in
        # both arms. Those three are rebuilt below with the other caps
        # isolated and, where needed, a request shape that actually lets the
        # named cap bind, and checked with assertLess plus an explicit
        # binding_constraint assertion rather than the weaker
        # assertLessEqual every other case still uses.
        cases = [
            ({"portfolio": {"max_single_position": 0.02}},
             dict(conviction="VERY HIGH", scenarios=DEFAULT_SCENARIOS)),
            ({"portfolio": {"max_sector_exposure": 0.05}},
             dict(conviction="HIGH", scenarios=DEFAULT_SCENARIOS,
                 sector_weight=0.20)),
            ({"portfolio": {"max_country_exposure": 0.10}},
             dict(conviction="HIGH", scenarios=DEFAULT_SCENARIOS,
                 country_weight=0.90)),
            ({"portfolio": {"max_gross_exposure": 0.10}},
             dict(conviction="HIGH", scenarios=DEFAULT_SCENARIOS,
                 gross_weight=0.95)),
            # Was: conviction=None, scenarios=DEFAULT_SCENARIOS - the
            # conviction ceiling (LOW == 0.02, since None assumes the
            # lowest permitting rung) bound in both arms regardless of
            # kelly_fraction, so tightening it changed nothing. THIN_SCENARIOS
            # has a small enough raw Kelly, and the caps isolated, that
            # kelly_fraction reaches all the way to the uncertainty-bound
            # target instead of being shadowed by the conviction cap.
            ({"kelly_fraction": 0.05},
             dict(conviction=None, scenarios=THIN_SCENARIOS,
                 **ISOLATE_PORTFOLIO_CAPS),
             "uncertainty"),
            ({"kelly_fraction_by_conviction": {"HIGH": 0.05}},
             dict(conviction="HIGH", scenarios=DEFAULT_SCENARIOS)),
            # Was: adv_sek=5_000_000, total_value_sek=2_000_000 - that ratio
            # makes the participation cap so loose (>= 1.0 of the whole
            # portfolio) that it never bound even after tightening to 0.01,
            # and request()'s default cash bucket (5%) bound instead in both
            # arms. A smaller adv relative to a larger portfolio makes the
            # participation cap itself the tightest one, before and after.
            ({"liquidity": {"participation_rate": 0.01}},
             dict(conviction="VERY HIGH", scenarios=DEFAULT_SCENARIOS,
                 adv_sek=2_500_000, total_value_sek=50_000_000,
                 **ISOLATE_PORTFOLIO_CAPS),
             "liquidity"),
            ({"liquidity": {"microcap_max_position": 0.005}},
             dict(conviction="VERY HIGH", scenarios=DEFAULT_SCENARIOS,
                 market_cap_sek=100_000_000)),
            # Was bound by the conviction cap in both arms (default
            # uncertainty.floor=0.35 never got low enough to matter at
            # confidence 0). Isolating the other buckets and tightening the
            # floor to 0.05 lets the uncertainty factor itself fall below
            # the conviction cap and bind.
            ({"uncertainty": {"floor": 0.05}},
             dict(conviction="HIGH", scenarios=DEFAULT_SCENARIOS,
                 confidence_thesis=0.0, confidence_valuation=0.0,
                 **ISOLATE_PORTFOLIO_CAPS),
             "uncertainty"),
            ({"concentration": {"medium": 0.50}},
             dict(conviction="HIGH", scenarios=DEFAULT_SCENARIOS,
                 shares_sector_with=1)),
        ]
        for case in cases:
            if len(case) == 3:
                override, req_kwargs, expected_binding = case
            else:
                override, req_kwargs = case
                expected_binding = None
            baseline_result = ps.size(request(**req_kwargs), config=CFG)
            baseline_target = baseline_result["target"]
            tightened_cfg = ps.load_config(override, path="")
            tightened_result = ps.size(request(**req_kwargs), config=tightened_cfg)
            tightened_target = tightened_result["target"]
            if expected_binding is not None:
                self.assertLess(
                    tightened_target, baseline_target - 1e-12,
                    "tightening %r did not strictly reduce the target "
                    "(%r -> %r) for %r - the tightened value never became "
                    "the binding constraint"
                    % (override, baseline_target, tightened_target, req_kwargs))
                self.assertEqual(
                    tightened_result["binding_constraint"], expected_binding,
                    "tightening %r reduced the target, but %r - not %r - "
                    "was the binding constraint for %r -> %r; the cap "
                    "under test did not actually fire"
                    % (override, tightened_result["binding_constraint"],
                       expected_binding, req_kwargs, tightened_result))
            else:
                self.assertLessEqual(
                    tightened_target, baseline_target + 1e-12,
                    "tightening %r raised the target from %r to %r for %r"
                    % (override, baseline_target, tightened_target, req_kwargs))

    # ----------------------------------------------------------------
    # 10. delta == target - current, exactly, in every status.
    # ----------------------------------------------------------------
    def test_delta_is_always_target_minus_current(self):
        """delta == target - current exactly (within 1e-12), in every status."""
        conviction_opts = (None,) + CONVICTIONS_NO_NONE
        scenario_opts = (DEFAULT_SCENARIOS, ZERO_EDGE_SCENARIOS,
                         NEGATIVE_EDGE_SCENARIOS, NO_DOWNSIDE_SCENARIOS, None)
        thesis_opts = (None, "BROKEN")
        current_weight_opts = (0.0, 0.003, 0.02, 0.5)
        n = 0
        for conv, scen, thesis, cw in itertools.product(
                conviction_opts, scenario_opts, thesis_opts, current_weight_opts):
            n += 1
            req = request(conviction=conv, scenarios=scen, thesis_status=thesis,
                         current_weight=cw)
            result = ps.size(req, config=CFG)
            self.assertAlmostEqual(
                result["delta"], result["target"] - result["current"],
                delta=1e-12,
                msg="delta != target - current for status=%s, %r -> %r"
                    % (result["status"], req, result))
        self.assertGreater(n, 100)

    # ----------------------------------------------------------------
    # 11. action is always one of ps.ACTIONS.
    # ----------------------------------------------------------------
    def test_action_always_known_token(self):
        """action is always one of ps.ACTIONS, in every combination."""
        conviction_opts = (None,) + CONVICTIONS_NO_NONE
        scenario_opts = (DEFAULT_SCENARIOS, ZERO_EDGE_SCENARIOS,
                         NEGATIVE_EDGE_SCENARIOS, NO_DOWNSIDE_SCENARIOS, None)
        thesis_opts = (None, "BROKEN")
        current_weight_opts = (0.0, 0.003, 0.02, 0.5)
        n = 0
        for conv, scen, thesis, cw in itertools.product(
                conviction_opts, scenario_opts, thesis_opts, current_weight_opts):
            n += 1
            req = request(conviction=conv, scenarios=scen, thesis_status=thesis,
                         current_weight=cw)
            result = ps.size(req, config=CFG)
            self.assertIn(result["action"], ps.ACTIONS,
                         "unknown action %r for %r" % (result["action"], req))
        self.assertGreater(n, 100)

    # ----------------------------------------------------------------
    # 12. status is always one of ok / no_bet / insufficient_data.
    # ----------------------------------------------------------------
    def test_status_always_known_token(self):
        """status is always one of ok / no_bet / insufficient_data."""
        known = {ps.OK, ps.NO_BET, ps.INSUFFICIENT_DATA}
        conviction_opts = (None,) + CONVICTIONS_NO_NONE
        scenario_opts = (DEFAULT_SCENARIOS, ZERO_EDGE_SCENARIOS,
                         NEGATIVE_EDGE_SCENARIOS, NO_DOWNSIDE_SCENARIOS, None)
        reason_code_opts = ([], [{"code": "GATE_PRICE_STALE", "severity": "BLOCK"}])
        n = 0
        for conv, scen, rc in itertools.product(
                conviction_opts, scenario_opts, reason_code_opts):
            n += 1
            req = request(conviction=conv, scenarios=scen, reason_codes=rc)
            result = ps.size(req, config=CFG)
            self.assertIn(result["status"], known,
                         "unknown status %r for %r" % (result["status"], req))
        self.assertGreater(n, 20)

    # ----------------------------------------------------------------
    # 13. A refusal always has target == 0.0 and non-empty no_bet_reasons.
    # ----------------------------------------------------------------
    def test_refusal_always_zero_target_and_reasons(self):
        """a refusal (status != ok) always has target == 0.0 and non-empty no_bet_reasons."""
        conviction_opts = (None,) + CONVICTIONS_NO_NONE
        scenario_opts = (DEFAULT_SCENARIOS, ZERO_EDGE_SCENARIOS,
                         NEGATIVE_EDGE_SCENARIOS, NO_DOWNSIDE_SCENARIOS, None)
        thesis_opts = (None, "BROKEN")
        reason_code_opts = ([], [{"code": "GATE_PRICE_STALE", "severity": "BLOCK"}])
        confidence_data_opts = (5, 80)
        n_refused = 0
        for conv, scen, thesis, rc, cd in itertools.product(
                conviction_opts, scenario_opts, thesis_opts, reason_code_opts,
                confidence_data_opts):
            req = request(conviction=conv, scenarios=scen, thesis_status=thesis,
                         reason_codes=rc, confidence_data=cd)
            result = ps.size(req, config=CFG)
            if result["status"] != ps.OK:
                n_refused += 1
                self.assertEqual(
                    result["target"], 0.0,
                    "refused (%s) but target != 0 for %r -> %r"
                    % (result["status"], req, result))
                self.assertTrue(
                    result["no_bet_reasons"],
                    "refused (%s) with empty no_bet_reasons for %r -> %r"
                    % (result["status"], req, result))
        self.assertGreater(n_refused, 20)

    # ----------------------------------------------------------------
    # 14. Adding a NO_BET condition drives an otherwise-sizing target to 0.
    # ----------------------------------------------------------------
    def test_no_bet_triggers_drive_target_to_zero(self):
        """adding any single NO_BET condition must drive the target to 0."""
        base_requests = [
            dict(conviction="HIGH", scenarios=DEFAULT_SCENARIOS, current_weight=0.0),
            dict(conviction="VERY HIGH", scenarios=THIN_SCENARIOS, current_weight=0.02),
            dict(conviction="MEDIUM", scenarios=DEFAULT_SCENARIOS,
                current_weight=0.01, sector_weight=0.10),
        ]

        # Sanity: every base request actually sizes something before a
        # trigger is added, otherwise this test would pass vacuously.
        for base in base_requests:
            baseline = ps.size(request(**base), config=CFG)
            self.assertGreater(baseline["target"], 0.0,
                              "base request does not size at all: %r" % base)

        triggers = [
            ("BLOCK reason code",
             lambda kw: dict(kw, reason_codes=[{"code": "GATE_PRICE_STALE",
                                               "severity": "BLOCK"}])),
            ("thesis_status BROKEN",
             lambda kw: dict(kw, thesis_status="BROKEN")),
            ("THESIS_BROKEN reason code",
             lambda kw: dict(kw, reason_codes=[{"code": "THESIS_BROKEN",
                                               "severity": "WARN"}])),
            ("CONFLICT_UNRESOLVED reason code",
             lambda kw: dict(kw, reason_codes=[{"code": "CONFLICT_UNRESOLVED",
                                               "severity": "WARN"}])),
            ("data confidence below floor",
             lambda kw: dict(kw, confidence_data=10)),
            ("DATA_CONFIDENCE_LOW reason code",
             lambda kw: dict(kw, confidence_data=80,
                             reason_codes=[{"code": "DATA_CONFIDENCE_LOW",
                                           "severity": "WARN"}])),
            ("conviction VERY LOW",
             lambda kw: dict(kw, conviction="VERY LOW")),
            ("negative-edge scenarios",
             lambda kw: dict(kw, scenarios=NEGATIVE_EDGE_SCENARIOS)),
        ]

        liquidity_floor = getattr(
            helpers.try_load("market_universe"),
            "DEFAULT_LIQUIDITY_FLOOR_SEK", None)
        if liquidity_floor is not None:
            triggers.append(
                ("adv below the universe liquidity floor",
                 lambda kw: dict(kw, adv_sek=liquidity_floor / 2.0,
                                 turnover_sek=None)))

        n = 0
        for base, (name, apply_trigger) in itertools.product(
                base_requests, triggers):
            n += 1
            req = request(**apply_trigger(dict(base)))
            result = ps.size(req, config=CFG)
            self.assertEqual(
                result["target"], 0.0,
                "trigger %r did not drive target to 0 for base %r -> %r"
                % (name, base, result))
        self.assertGreater(n, 10)

    # ----------------------------------------------------------------
    # 15. Less liquidity never raises the target.
    # ----------------------------------------------------------------
    def test_monotone_in_adv(self):
        """lower adv_sek must never increase the target, and must actually move it on at least one base."""
        ladder = (100_000, 1_000_000, 1_999_999, 2_000_001, 10_000_000,
                 40_000_000, 500_000_000)  # ascending
        base_requests = [
            dict(conviction="VERY HIGH", scenarios=DEFAULT_SCENARIOS,
                total_value_sek=2_000_000, current_weight=0.0),
            dict(conviction="HIGH", scenarios=THIN_SCENARIOS,
                total_value_sek=50_000_000, current_weight=0.01),
            # The two bases above only ever move on the universe-floor
            # refusal (0 -> nonzero at the 2,000,000 SEK boundary); the
            # participation-rate cap itself never binds anywhere in this
            # suite. This base sits on the adjusted-Kelly path with the
            # other caps isolated, so the same floor-crossing is visible
            # without being the only thing that can move.
            dict(conviction="VERY HIGH", scenarios=MODERATE_SCENARIOS,
                total_value_sek=2_000_000, current_weight=0.0,
                **ISOLATE_PORTFOLIO_CAPS),
        ]
        for base in base_requests:
            targets = []
            for adv in ladder:
                req = request(adv_sek=adv, **base)
                targets.append(ps.size(req, config=CFG)["target"])
            for i in range(1, len(targets)):
                self.assertGreaterEqual(
                    targets[i], targets[i - 1] - 1e-12,
                    "target fell as adv_sek rose %r -> %r for base %r"
                    % (ladder[i - 1], ladder[i], base))
            if base["scenarios"] is MODERATE_SCENARIOS:
                self.assertGreater(
                    len(set(round(t, 9) for t in targets)), 1,
                    "the adv_sek ladder produced a single constant target "
                    "%r across the whole sweep for base %r"
                    % (targets[0], base))

    def test_monotone_in_market_cap(self):
        """lower market_cap_sek must never increase the target, and must actually move it on at least one base."""
        ladder = (100_000_000, 400_000_000, 499_999_999, 500_000_001,
                 1_000_000_000, 9_000_000_000, 100_000_000_000)  # ascending
        base_requests = [
            dict(conviction="VERY HIGH", scenarios=DEFAULT_SCENARIOS,
                total_value_sek=2_000_000, current_weight=0.0,
                adv_sek=40_000_000),
            dict(conviction="HIGH", scenarios=THIN_SCENARIOS,
                total_value_sek=2_000_000, current_weight=0.01,
                adv_sek=40_000_000),
            # See test_monotone_in_adv above for why a third, isolated base
            # on the adjusted-Kelly path is needed here too.
            dict(conviction="VERY HIGH", scenarios=MODERATE_SCENARIOS,
                total_value_sek=2_000_000, current_weight=0.0,
                adv_sek=40_000_000, **ISOLATE_PORTFOLIO_CAPS),
        ]
        for base in base_requests:
            targets = []
            for mcap in ladder:
                req = request(market_cap_sek=mcap, **base)
                targets.append(ps.size(req, config=CFG)["target"])
            for i in range(1, len(targets)):
                self.assertGreaterEqual(
                    targets[i], targets[i - 1] - 1e-12,
                    "target fell as market_cap_sek rose %r -> %r for base %r"
                    % (ladder[i - 1], ladder[i], base))
            if base["scenarios"] is MODERATE_SCENARIOS:
                self.assertGreater(
                    len(set(round(t, 9) for t in targets)), 1,
                    "the market_cap_sek ladder produced a single constant "
                    "target %r across the whole sweep for base %r"
                    % (targets[0], base))

    # ----------------------------------------------------------------
    # 16. size() is pure: repeatable, and never mutates its input.
    # ----------------------------------------------------------------
    def test_size_is_pure(self):
        """calling size() twice on the same request gives an identical result and never mutates the input."""
        conviction_opts = CONVICTIONS_NO_NONE
        scenario_opts = (DEFAULT_SCENARIOS, ZERO_EDGE_SCENARIOS, NO_DOWNSIDE_SCENARIOS)
        thesis_opts = (None, "BROKEN")
        n = 0
        for conv, scen, thesis in itertools.product(
                conviction_opts, scenario_opts, thesis_opts):
            n += 1
            req = request(conviction=conv, scenarios=scen, thesis_status=thesis)
            before = copy.deepcopy(req)
            first = ps.size(req, config=CFG)
            self.assertEqual(
                req, before,
                "size() mutated its input request for %r" % (before,))
            second = ps.size(req, config=CFG)
            self.assertEqual(
                req, before,
                "size() mutated its input request on the second call for %r"
                % (before,))
            self.assertEqual(
                first, second,
                "size() gave different results on repeated calls for %r: "
                "%r vs %r" % (req, first, second))
        self.assertGreater(n, 10)

    # ----------------------------------------------------------------
    # 17. Reported caps are always None or >= 0.
    # ----------------------------------------------------------------
    def test_caps_always_none_or_nonnegative(self):
        """reported caps are all either None or >= 0."""
        conviction_opts = (None,) + CONVICTIONS_NO_NONE
        scenario_opts = (DEFAULT_SCENARIOS, THIN_SCENARIOS)
        sector_weight_opts = (None, 0.0, 0.30, 0.90)  # 0.90 > limit -> "others" clamp
        country_weight_opts = (None, 0.0, 0.99)
        gross_weight_opts = (None, 0.0, 0.99)
        current_weight_opts = (0.0, 0.02, 0.09)
        n = 0
        for conv, scen, sw, cow, gw, cw in itertools.product(
                conviction_opts, scenario_opts, sector_weight_opts,
                country_weight_opts, gross_weight_opts, current_weight_opts):
            n += 1
            req = request(conviction=conv, scenarios=scen,
                         sector_weight=_bucket(sw, cw),
                         country_weight=_bucket(cow, cw),
                         gross_weight=_bucket(gw, cw),
                         current_weight=cw)
            result = ps.size(req, config=CFG)
            for cap_name, cap_value in result["caps"].items():
                if cap_value is None:
                    continue
                self.assertGreaterEqual(
                    cap_value, 0.0,
                    "cap %s is negative (%r) for %r"
                    % (cap_name, cap_value, req))
        self.assertGreater(n, 100)

    # ----------------------------------------------------------------
    # Bonus coverage: a seeded random sweep of scenario distributions, for
    # invariant 1 and 17 together, so the grid above (built from a handful
    # of hand-picked distributions) is not the only shape of input tried.
    # Deterministic: random.Random(12345) reproduces any failure exactly.
    # ----------------------------------------------------------------
    def test_target_bounds_hold_over_random_scenario_distributions(self):
        """target stays within [0, HARD_MAX_SINGLE_POSITION] for randomised scenario sets."""
        rng = random.Random(12345)
        n = 0
        for _ in range(200):
            n += 1
            bear_p = rng.uniform(0.05, 0.6)
            base_p = rng.uniform(0.05, 0.9 - bear_p)
            bull_p = 1.0 - bear_p - base_p
            scen = [
                {"name": "bear", "probability": bear_p,
                 "return": -rng.uniform(0.01, 0.9)},
                {"name": "base", "probability": base_p,
                 "return": rng.uniform(-0.5, 0.5)},
                {"name": "bull", "probability": bull_p,
                 "return": rng.uniform(0.0, 2.0)},
            ]
            conv = rng.choice((None,) + CONVICTIONS_NO_NONE)
            cw = rng.choice((0.0, 0.02, 0.05))
            req = request(conviction=conv, scenarios=scen, current_weight=cw)
            result = ps.size(req, config=CFG)
            self.assertGreaterEqual(
                result["target"], 0.0,
                "randomised scenario set gave negative target: seed step "
                "%d, %r -> %r" % (n, req, result))
            self.assertLessEqual(
                result["target"], ps.HARD_MAX_SINGLE_POSITION + 1e-12,
                "randomised scenario set exceeded the hard cap: seed step "
                "%d, %r -> %r" % (n, req, result))
        self.assertEqual(n, 200)


# ==========================================================================
# Kelly / sizing relationships. The chain raw -> fractional -> adjusted ->
# capped -> target only ever multiplies by <= 1 or takes a min, so it can
# never grow at any step.
# ==========================================================================

class PositionSizingKellyChainInvariants(unittest.TestCase):

    def test_fractional_and_adjusted_kelly_chain_never_grows(self):
        """fractional_kelly <= raw_kelly (raw > 0), adjusted_kelly <= fractional_kelly."""
        conviction_opts = (None,) + CONVICTIONS_NO_NONE
        scenario_opts = (DEFAULT_SCENARIOS, THIN_SCENARIOS)
        confidence_thesis_opts = (0.0, 0.4, 1.0)
        sector_weight_opts = (None, 0.10, 0.30)
        current_weight_opts = (0.0, 0.02)
        n = 0
        for conv, scen, ct, sw, cw in itertools.product(
                conviction_opts, scenario_opts, confidence_thesis_opts,
                sector_weight_opts, current_weight_opts):
            req = request(conviction=conv, scenarios=scen, confidence_thesis=ct,
                         sector_weight=sw, current_weight=cw)
            result = ps.size(req, config=CFG)
            if result["status"] != ps.OK:
                continue
            n += 1
            raw, frac, adj = (result["raw_kelly"], result["fractional_kelly"],
                              result["adjusted_kelly"])
            self.assertGreater(
                raw, 0.0,
                "status ok but raw_kelly not positive for %r -> %r" % (req, result))
            self.assertLessEqual(
                frac, raw + 1e-12,
                "fractional_kelly %r exceeded raw_kelly %r for %r"
                % (frac, raw, req))
            self.assertLessEqual(
                adj, frac + 1e-12,
                "adjusted_kelly %r exceeded fractional_kelly %r for %r"
                % (adj, frac, req))
        self.assertGreater(n, 20)

    def test_target_never_exceeds_adjusted_kelly(self):
        """target <= adjusted_kelly always: every step below it only caps or scales down."""
        conviction_opts = CONVICTIONS_NO_NONE
        scenario_opts = (DEFAULT_SCENARIOS, THIN_SCENARIOS)
        sector_weight_opts = (None, 0.10, 0.30)
        current_weight_opts = (0.0, 0.02, 0.06)
        n = 0
        for conv, scen, sw, cw in itertools.product(
                conviction_opts, scenario_opts, sector_weight_opts,
                current_weight_opts):
            req = request(conviction=conv, scenarios=scen, sector_weight=sw,
                         current_weight=cw)
            result = ps.size(req, config=CFG)
            if result["status"] != ps.OK:
                continue
            n += 1
            self.assertLessEqual(
                result["target"], result["adjusted_kelly"] + 1e-9,
                "target %r exceeded adjusted_kelly %r for %r -> %r"
                % (result["target"], result["adjusted_kelly"], req, result))
        self.assertGreater(n, 20)


# ==========================================================================
# Risk budget. cap = max_position_loss / |worst_return|; deepening the bear
# case must never buy a bigger position, and the cap itself can never be
# raised past HARD_MAX_POSITION_LOSS by configuration.
# ==========================================================================

class PositionSizingRiskBudgetInvariants(unittest.TestCase):

    def test_deeper_bear_case_never_raises_target(self):
        """a more negative worst-case return must never increase the target."""
        ladder = (-0.10, -0.20, -0.30, -0.40, -0.50, -0.60, -0.70, -0.80)  # descending
        base_requests = [
            dict(conviction="HIGH", current_weight=0.0),
            dict(conviction="VERY HIGH", current_weight=0.02, sector_weight=0.10),
        ]
        for base in base_requests:
            targets = []
            for bear_r in ladder:
                req = request(scenarios=_bear_ladder_scenarios(bear_r), **base)
                targets.append(ps.size(req, config=CFG)["target"])
            for i in range(1, len(targets)):
                self.assertLessEqual(
                    targets[i], targets[i - 1] + 1e-12,
                    "target rose as the bear return deepened %r -> %r for base %r"
                    % (ladder[i - 1], ladder[i], base))

    def test_risk_budget_cap_matches_formula_and_none_cases(self):
        """cap == max_position_loss / |worst_return| when worst < 0; None otherwise."""
        scenario_opts = (DEFAULT_SCENARIOS, THIN_SCENARIOS,
                         _bear_ladder_scenarios(-0.05), _bear_ladder_scenarios(-0.95),
                         NO_DOWNSIDE_SCENARIOS)
        n = 0
        for scen in scenario_opts:
            kelly_result = kelly_mod.kelly_scenarios(scen)
            cap, detail = ps.risk_budget_cap(kelly_result, CFG)
            n += 1
            worst = kelly_result["worst_return"]
            if worst is not None and worst < 0:
                expected = CFG["risk_budget"]["max_position_loss"] / abs(worst)
                self.assertIsNotNone(
                    cap, "expected a risk_budget cap for worst_return=%r, scen=%r"
                        % (worst, scen))
                self.assertAlmostEqual(
                    cap, expected, delta=1e-9,
                    msg="risk_budget cap %r != formula result %r for worst=%r, scen=%r"
                        % (cap, expected, worst, scen))
            else:
                self.assertIsNone(
                    cap, "expected no risk_budget cap for worst_return=%r, scen=%r"
                        % (worst, scen))
        self.assertGreaterEqual(n, 5)

        # A config cannot switch the risk budget off: load_config forces it
        # back on, because a cap a config file can disable is not a cap.
        disabled_cfg = ps.load_config({"risk_budget": {"enabled": False}},
                                      path="")
        self.assertTrue(disabled_cfg["risk_budget"]["enabled"])
        for scen in (DEFAULT_SCENARIOS, THIN_SCENARIOS,
                     _bear_ladder_scenarios(-0.90)):
            kelly_result = kelly_mod.kelly_scenarios(scen)
            cap, detail = ps.risk_budget_cap(kelly_result, disabled_cfg)
            self.assertIsNotNone(
                cap, "the risk budget must survive a config that disables it,"
                     " for scen=%r" % (scen,))
            self.assertTrue(detail["enabled"])

        # Only a direct call with a hand-built config can disable it, and then
        # it reports None rather than a silent zero.
        raw = copy.deepcopy(ps.DEFAULTS)
        raw["risk_budget"]["enabled"] = False
        cap, detail = ps.risk_budget_cap(
            kelly_mod.kelly_scenarios(DEFAULT_SCENARIOS), raw)
        self.assertIsNone(cap)
        self.assertEqual(detail, {"enabled": False})

    def test_max_position_loss_clamped_and_monotone(self):
        """config cannot raise max_position_loss past the hard cap; lowering it never raises target."""
        loose_cfg = ps.load_config({"risk_budget": {"max_position_loss": 5.0}}, path="")
        self.assertLessEqual(
            loose_cfg["risk_budget"]["max_position_loss"],
            ps.HARD_MAX_POSITION_LOSS + 1e-12,
            "config raised max_position_loss past HARD_MAX_POSITION_LOSS")

        ladder = (0.05, 0.02, 0.01, 0.005, 0.001)  # descending allowance
        base = dict(conviction="VERY HIGH", scenarios=THIN_SCENARIOS,
                   current_weight=0.0, sector_weight=None, country_weight=None,
                   gross_weight=None, cash_weight=None)
        targets = []
        for allowance in ladder:
            cfg = ps.load_config({"risk_budget": {"max_position_loss": allowance}},
                                 path="")
            targets.append(ps.size(request(**base), config=cfg)["target"])
        for i in range(1, len(targets)):
            self.assertLessEqual(
                targets[i], targets[i - 1] + 1e-12,
                "target rose as max_position_loss fell %r -> %r"
                % (ladder[i - 1], ladder[i]))

    def test_risk_budget_binding_never_exceeds_configured_loss(self):
        """when risk_budget is the binding cap, target * |worst_return| <= max_position_loss."""
        conviction_opts = ("HIGH", "VERY HIGH")
        scenario_opts = (DEFAULT_SCENARIOS, THIN_SCENARIOS,
                         _bear_ladder_scenarios(-0.10), _bear_ladder_scenarios(-0.50),
                         _bear_ladder_scenarios(-0.90))
        current_weight_opts = (0.0, 0.02)
        n_binding = 0
        for conv, scen, cw in itertools.product(
                conviction_opts, scenario_opts, current_weight_opts):
            req = request(conviction=conv, scenarios=scen, current_weight=cw,
                         sector_weight=None, country_weight=None,
                         gross_weight=None, cash_weight=None)
            result = ps.size(req, config=CFG)
            if result.get("binding_constraint") != "risk_budget":
                continue
            n_binding += 1
            worst = result["cap_detail"]["risk_budget"]["bear_case_return"]
            implied_loss = result["target"] * abs(worst)
            self.assertLessEqual(
                implied_loss, CFG["risk_budget"]["max_position_loss"] + 1e-9,
                "implied bear-case loss %r exceeds configured max_position_loss "
                "%r for %r -> %r"
                % (implied_loss, CFG["risk_budget"]["max_position_loss"], req, result))
        self.assertGreater(
            n_binding, 0,
            "no swept combination actually bound on risk_budget - the "
            "invariant was never exercised")


# ==========================================================================
# Calibration seam. forecast_scoring.calibration_table is inert unless a
# caller supplies a table that clears the sample floor - no-false-precision
# by construction.
# ==========================================================================

class PositionSizingCalibrationInvariants(unittest.TestCase):

    def test_no_calibration_table_leaves_probabilities_identical(self):
        """with no calibration_table, scenario_distribution == the input probabilities."""
        scenario_opts = (DEFAULT_SCENARIOS, THIN_SCENARIOS, ZERO_EDGE_SCENARIOS,
                         NEGATIVE_EDGE_SCENARIOS, NO_DOWNSIDE_SCENARIOS)
        n = 0
        for scen in scenario_opts:
            req = request(scenarios=scen)
            result = ps.size(req, config=CFG)
            n += 1
            self.assertEqual(
                result["calibration"]["status"], "unavailable",
                "calibration status not 'unavailable' with no table for %r -> %r"
                % (req, result["calibration"]))
            by_name = {row["name"]: row["probability"] for row in scen}
            for row in result["scenario_distribution"]:
                self.assertAlmostEqual(
                    row["probability"], by_name[row["name"]], delta=1e-12,
                    msg="probability for %s changed with no calibration table: "
                        "%r -> %r for %r"
                        % (row["name"], by_name[row["name"]],
                           row["probability"], req))
        self.assertGreater(n, 3)

    def test_unavailable_table_from_small_sample_leaves_probabilities_untouched(self):
        """a table built from a below-floor sample also leaves probabilities untouched."""
        rows = [
            {"forecast": {"bear": 0.25, "base": 0.50, "bull": 0.25},
             "realised": "base"},
            {"forecast": {"bear": 0.25, "base": 0.50, "bull": 0.25},
             "realised": "bull"},
            {"forecast": {"bear": 0.25, "base": 0.50, "bull": 0.25},
             "realised": "base"},
        ]
        table = scoring.calibration_table(rows)
        self.assertEqual(
            table["status"], scoring.UNAVAILABLE,
            "fixture sample of %d rows unexpectedly cleared the calibration "
            "floor (min_n=%r) - table: %r" % (len(rows), table.get("min_n"), table))

        req = request(scenarios=DEFAULT_SCENARIOS)
        req["calibration_table"] = table
        result = ps.size(req, config=CFG)
        self.assertEqual(result["calibration"]["status"], "unavailable")
        by_name = {row["name"]: row["probability"] for row in DEFAULT_SCENARIOS}
        for row in result["scenario_distribution"]:
            self.assertAlmostEqual(
                row["probability"], by_name[row["name"]], delta=1e-12,
                msg="probability for %s changed with a below-floor table"
                    % row["name"])

    def test_available_table_probabilities_still_sum_to_one(self):
        """with an available calibration table, scenario probabilities still sum to 1."""
        pattern = ["bull"] * 12 + ["base"] * 6 + ["bear"] * 3  # n=21, clears min_n=20
        rows = [{"forecast": {"bear": 0.25, "base": 0.50, "bull": 0.25},
                "realised": r} for r in pattern]
        table = scoring.calibration_table(rows)
        self.assertEqual(
            table["status"], scoring.AVAILABLE,
            "fixture sample of %d rows did not clear the calibration floor "
            "(min_n=%r) - table: %r" % (len(rows), table.get("min_n"), table))

        for scen in (DEFAULT_SCENARIOS, THIN_SCENARIOS):
            req = request(scenarios=scen)
            req["calibration_table"] = table
            result = ps.size(req, config=CFG)
            self.assertEqual(
                result["calibration"]["status"], "applied",
                "calibration not applied with an available table for %r -> %r"
                % (req, result["calibration"]))
            total = sum(row["probability"] for row in result["scenario_distribution"])
            self.assertAlmostEqual(
                total, 1.0, delta=1e-9,
                msg="calibrated probabilities do not sum to 1 (%r) for %r"
                    % (total, req))


# ==========================================================================
# Probability support. A declared basis is never rewarded with a bigger
# target than its absence is punished with - the missing basis is charged,
# never the presence of one credited beyond what the edge itself justifies.
# ==========================================================================

class PositionSizingProbabilitySupportInvariants(unittest.TestCase):

    def test_probability_support_always_known_token(self):
        """probability_support is always one of SUPPORTED / ASSERTED / UNKNOWN."""
        known = {ps.PROBABILITY_SUPPORTED, ps.PROBABILITY_ASSERTED,
                ps.PROBABILITY_UNKNOWN}
        scenario_opts = (DEFAULT_SCENARIOS, SUPPORTED_DEFAULT_SCENARIOS,
                         THIN_SCENARIOS, NO_DOWNSIDE_SCENARIOS)
        basis_opts = (None, "scenario_analysis", "asserted", "something_else")
        n = 0
        for scen, basis in itertools.product(scenario_opts, basis_opts):
            req = request(scenarios=scen)
            req["probability_basis"] = basis
            result = ps.size(req, config=CFG)
            n += 1
            self.assertIn(
                result["probability_support"], known,
                "unknown probability_support %r for %r"
                % (result["probability_support"], req))
        self.assertGreater(n, 10)

    def test_missing_basis_never_produces_a_larger_target(self):
        """the same request without a stated probability basis never sizes larger, and on MODERATE_SCENARIOS the missing-basis charge actually bites."""
        conviction_opts = CONVICTIONS_NO_NONE
        current_weight_opts = (0.0, 0.02)
        scenario_pairs = (
            (DEFAULT_SCENARIOS, SUPPORTED_DEFAULT_SCENARIOS),
            (THIN_SCENARIOS, SUPPORTED_THIN_SCENARIOS),
        )
        n = 0
        for conv, cw, (bare, supported) in itertools.product(
                conviction_opts, current_weight_opts, scenario_pairs):
            unsupported_target = ps.size(
                request(conviction=conv, current_weight=cw, scenarios=bare),
                config=CFG)["target"]
            supported_target = ps.size(
                request(conviction=conv, current_weight=cw, scenarios=supported),
                config=CFG)["target"]
            n += 1
            self.assertLessEqual(
                unsupported_target, supported_target + 1e-12,
                "missing basis produced a larger target (%r) than a declared "
                "basis (%r) for conviction=%r current_weight=%r scenarios=%r"
                % (unsupported_target, supported_target, conv, cw, bare))
        self.assertGreater(n, 5)

        # All 20 comparisons above are equalities, not inequalities: every
        # DEFAULT_SCENARIOS/THIN_SCENARIOS combination is cap-pinned or
        # floor-pinned (see MODERATE_SCENARIOS' docstring), so the
        # missing-basis charge never actually reaches the target - the
        # assertion above proves the charge is never harmful, not that it
        # does anything. MODERATE_SCENARIOS sits on the adjusted-Kelly path,
        # so here the charge must strictly reduce the target: unsupported
        # carries the probability-support and incomplete-inputs penalties
        # that a stated basis removes, and with confidence held generously
        # high neither arm is pinned at the uncertainty floor. VERY LOW and
        # LOW conviction are excluded - their ceilings (0.00 and 0.02) are
        # low enough that MODERATE_SCENARIOS' raw Kelly clears them even
        # without a basis, so the charge cannot bite there either.
        n_strict = 0
        for conv in ("MEDIUM", "HIGH", "VERY HIGH"):
            for cw in current_weight_opts:
                unsupported_target = ps.size(
                    request(conviction=conv, current_weight=cw,
                           scenarios=MODERATE_SCENARIOS,
                           confidence_thesis=0.9, confidence_valuation=0.9,
                           **ISOLATE_PORTFOLIO_CAPS),
                    config=CFG)["target"]
                supported_target = ps.size(
                    request(conviction=conv, current_weight=cw,
                           scenarios=SUPPORTED_MODERATE_SCENARIOS,
                           confidence_thesis=0.9, confidence_valuation=0.9,
                           **ISOLATE_PORTFOLIO_CAPS),
                    config=CFG)["target"]
                n_strict += 1
                self.assertLess(
                    unsupported_target, supported_target - 1e-12,
                    "missing basis did not reduce the target at all (%r vs "
                    "%r, declared basis) for conviction=%r current_weight=%r "
                    "on the fixture built to keep the missing-basis charge "
                    "on the adjusted-Kelly path"
                    % (unsupported_target, supported_target, conv, cw))
        self.assertGreater(n_strict, 5)

    def test_require_basis_config_refuses_unsupported_requests(self):
        """probability.require_basis=true always refuses an unsupported request."""
        strict_cfg = ps.load_config({"probability": {"require_basis": True}},
                                    path="")
        conviction_opts = (None,) + CONVICTIONS_NO_NONE
        scenario_opts = (DEFAULT_SCENARIOS, THIN_SCENARIOS, ZERO_EDGE_SCENARIOS)
        current_weight_opts = (0.0, 0.02, 0.5)
        n = 0
        for conv, scen, cw in itertools.product(
                conviction_opts, scenario_opts, current_weight_opts):
            req = request(conviction=conv, scenarios=scen, current_weight=cw)
            result = ps.size(req, config=strict_cfg)
            n += 1
            self.assertEqual(
                result["status"], ps.INSUFFICIENT_DATA,
                "unsupported probability not refused under require_basis for "
                "%r -> %r" % (req, result))
            self.assertEqual(
                result["target"], 0.0,
                "unsupported probability gave a nonzero target under "
                "require_basis for %r -> %r" % (req, result))
        self.assertGreater(n, 15)


# ==========================================================================
# Edge cases named explicitly in review: degenerate probabilities, extreme
# returns, deep microcaps, a current position already above target,
# conflicting tight caps, a 0% position crossing min_position, and missing
# correlation data.
# ==========================================================================

class PositionSizingEdgeCaseInvariants(unittest.TestCase):

    def test_degenerate_scenario_probabilities_handled_or_refused(self):
        """a scenario probability of exactly 0 or 1 sizes to a specific, pinned status and reason - never silently guessed, and never silently skipped by this test either."""
        # Each entry pairs a degenerate distribution with the status and
        # no_bet_reasons[0]["code"] the real code actually returns for it
        # (confirmed by running position_sizing.size() directly, not
        # assumed). The old version of this test wrapped the call in
        # `except SizingError: continue` "because a refusal is an acceptable
        # outcome" - but ps.size() never raises SizingError for any of these
        # three sets, so the except clause was dead, and every one of the 18
        # (scenario, conviction) results has target == 0.0 regardless, which
        # made every assertion below it trivially true. Asserting the exact
        # status/reason pins down what "handled cleanly" concretely means
        # here instead of accepting anything that didn't crash.
        degenerate_sets_and_expectations = (
            # bear carries a real -30% return but probability 0: Kelly
            # correctly refuses to count an impossible scenario as the
            # downside, so this reads as "no losing case" - a
            # zero-probability negative scenario must not be treated as
            # the downside.
            ([{"name": "bear", "probability": 0.0, "return": -0.30},
              {"name": "bull", "probability": 1.0, "return": 0.50}],
             ps.INSUFFICIENT_DATA, "NO_DOWNSIDE_SCENARIO"),
            # bear is certain, bull impossible: a guaranteed loss, refused
            # as a negative-edge bet rather than sized to any positive
            # figure.
            ([{"name": "bear", "probability": 1.0, "return": -0.30},
              {"name": "bull", "probability": 0.0, "return": 0.50}],
             ps.NO_BET, "NEGATIVE_EDGE"),
            # Same shape as the first, with a third row also at
            # probability 0 - the zero-probability -99% bear must still not
            # count as the downside.
            ([{"name": "bear", "probability": 0.0, "return": -0.99},
              {"name": "base", "probability": 0.0, "return": 0.0},
              {"name": "bull", "probability": 1.0, "return": 2.0}],
             ps.INSUFFICIENT_DATA, "NO_DOWNSIDE_SCENARIO"),
        )
        conviction_opts = (None,) + CONVICTIONS_NO_NONE
        n = 0
        for scen, expected_status, expected_binding in degenerate_sets_and_expectations:
            for conv in conviction_opts:
                req = request(scenarios=scen, conviction=conv)
                n += 1
                result = ps.size(req, config=CFG)
                # VERY LOW conviction fires CONVICTION_BELOW_FLOOR ahead of
                # the scenario-level reason (it is prepended to
                # no_bet_reasons), but the overall status is unaffected -
                # this is still the same refusal, arrived at one stop
                # earlier.
                binding = ("CONVICTION_BELOW_FLOOR" if conv == "VERY LOW"
                          else expected_binding)
                self.assertEqual(
                    result["status"], expected_status,
                    "degenerate distribution %r with conviction=%r gave "
                    "status %r, expected %r -> %r"
                    % (scen, conv, result["status"], expected_status, result))
                self.assertEqual(
                    result["binding_constraint"], binding,
                    "degenerate distribution %r with conviction=%r gave "
                    "binding_constraint %r, expected %r -> %r"
                    % (scen, conv, result["binding_constraint"], binding, result))
                self.assertEqual(
                    result["target"], 0.0,
                    "degenerate distribution gave a nonzero target for %r "
                    "-> %r" % (req, result))
                self.assertLessEqual(
                    result["target"], ps.HARD_MAX_SINGLE_POSITION + 1e-12,
                    "degenerate distribution exceeded the hard cap for %r "
                    "-> %r" % (req, result))
                self.assertAlmostEqual(
                    result["delta"], result["target"] - result["current"],
                    delta=1e-12,
                    msg="delta != target - current for a degenerate "
                        "distribution %r -> %r" % (req, result))
        self.assertGreater(n, 5)

    def test_extreme_returns_stay_finite_and_within_caps(self):
        """a total-loss bear (-1.0) and a +1000% bull still give a finite, capped target."""
        extreme = [
            {"name": "bear", "probability": 0.3, "return": -1.0},
            {"name": "base", "probability": 0.4, "return": 0.10},
            {"name": "bull", "probability": 0.3, "return": 10.0},
        ]
        conviction_opts = (None,) + CONVICTIONS_NO_NONE
        current_weight_opts = (0.0, 0.02)
        n = 0
        for conv, cw in itertools.product(conviction_opts, current_weight_opts):
            req = request(scenarios=extreme, conviction=conv, current_weight=cw)
            result = ps.size(req, config=CFG)
            n += 1
            self.assertTrue(
                math.isfinite(result["target"]),
                "non-finite target %r for %r" % (result["target"], req))
            self.assertGreaterEqual(result["target"], 0.0)
            self.assertLessEqual(result["target"], ps.HARD_MAX_SINGLE_POSITION + 1e-12)
            for cap_name, cap_value in result["caps"].items():
                if cap_value is None:
                    continue
                self.assertTrue(
                    math.isfinite(cap_value),
                    "non-finite cap %s=%r for %r" % (cap_name, cap_value, req))
                self.assertLessEqual(
                    result["target"], cap_value + 1e-9,
                    "target exceeds cap %s under extreme returns, %r -> %r"
                    % (cap_name, req, result))
        self.assertGreater(n, 5)

    def test_deep_microcap_never_exceeds_microcap_max_position(self):
        """market cap well below microcap_sek always yields target <= microcap_max_position."""
        microcap_ceiling = CFG["liquidity"]["microcap_max_position"]
        conviction_opts = CONVICTIONS_NO_NONE
        scenario_opts = (DEFAULT_SCENARIOS, THIN_SCENARIOS)
        market_cap_opts = (1_000_000, 50_000_000, 400_000_000)  # all below microcap_sek
        n = 0
        for conv, scen, mcap in itertools.product(
                conviction_opts, scenario_opts, market_cap_opts):
            req = request(conviction=conv, scenarios=scen, market_cap_sek=mcap)
            result = ps.size(req, config=CFG)
            n += 1
            self.assertLessEqual(
                result["target"], microcap_ceiling + 1e-9,
                "target exceeded microcap_max_position for market_cap_sek=%r, "
                "%r -> %r" % (mcap, req, result))
        self.assertGreater(n, 5)

    def test_current_above_target_never_adds_or_initiates(self):
        """current weight already above the computed target always yields TRIM or EXIT."""
        isolate = dict(sector_weight=None, country_weight=None, gross_weight=None,
                       cash_weight=None)
        base_requests = [
            dict(conviction="HIGH", scenarios=DEFAULT_SCENARIOS, **isolate),
            dict(conviction="VERY HIGH", scenarios=THIN_SCENARIOS, **isolate),
            dict(conviction="MEDIUM", scenarios=DEFAULT_SCENARIOS, **isolate),
        ]
        above_deltas = (0.01, 0.03, 0.10)
        n = 0
        for base in base_requests:
            target0 = ps.size(request(current_weight=0.0, **base),
                              config=CFG)["target"]
            for extra in above_deltas:
                cw = target0 + extra
                req = request(current_weight=cw, **base)
                result = ps.size(req, config=CFG)
                n += 1
                self.assertAlmostEqual(
                    result["target"], target0, delta=1e-9,
                    msg="target depended on current_weight despite isolating "
                        "every portfolio cap: %r vs baseline %r for %r"
                        % (result["target"], target0, req))
                self.assertIn(
                    result["action"], ("TRIM", "EXIT"),
                    "current weight %r above target %r produced action %r "
                    "for %r" % (cw, target0, result["action"], req))
        self.assertGreater(n, 5)

    def test_tightest_of_two_caps_binds_not_the_looser(self):
        """with two tight caps, the target equals the tighter one (x concentration), never the looser."""
        tight_cfg = ps.load_config(
            {"portfolio": {"max_single_position": 0.03},
             "liquidity": {"microcap_max_position": 0.005}}, path="")
        req = request(conviction="VERY HIGH", scenarios=THIN_SCENARIOS,
                     market_cap_sek=100_000_000,  # below microcap_sek -> microcap applies
                     sector_weight=None, country_weight=None, gross_weight=None,
                     cash_weight=None, current_weight=0.0)
        result = ps.size(req, config=tight_cfg)
        non_none_caps = {k: v for k, v in result["caps"].items() if v is not None}
        tightest_name = min(non_none_caps, key=non_none_caps.get)
        tightest_value = non_none_caps[tightest_name]
        conc_mult = result["concentration"]["multiplier"]

        self.assertLess(
            tightest_value, 0.03 - 1e-9,
            "test fixture did not make the liquidity cap the tighter one - "
            "caps: %r" % (result["caps"],))
        self.assertAlmostEqual(
            result["target"], tightest_value * conc_mult, delta=1e-9,
            msg="target %r did not equal the tightest cap (%s=%r) x "
                "concentration (%r) for %r -> %r"
                % (result["target"], tightest_name, tightest_value, conc_mult,
                   req, result))

    def test_zero_current_position_above_min_gives_initiate_not_add(self):
        """a 0% current position with a target above min_position always yields INITIATE."""
        isolate = dict(sector_weight=None, country_weight=None, gross_weight=None,
                       cash_weight=None)
        conviction_opts = ("MEDIUM", "HIGH", "VERY HIGH")
        scenario_opts = (DEFAULT_SCENARIOS, THIN_SCENARIOS)
        n_initiated = 0
        for conv, scen in itertools.product(conviction_opts, scenario_opts):
            req = request(conviction=conv, scenarios=scen, current_weight=0.0,
                         **isolate)
            result = ps.size(req, config=CFG)
            if result["target"] <= CFG["rebalance_band"] + 1e-12:
                continue
            n_initiated += 1
            self.assertEqual(
                result["action"], "INITIATE",
                "target %r above the rebalance band but action was %r (not "
                "INITIATE) for %r -> %r"
                % (result["target"], result["action"], req, result))
        self.assertGreater(n_initiated, 0)

    def test_missing_portfolio_context_never_invents_a_correlation(self):
        """no portfolio context -> concentration is exactly UNKNOWN, no correlation figure anywhere."""
        req = request(conviction="HIGH", scenarios=DEFAULT_SCENARIOS)
        req["portfolio"] = {}
        result = ps.size(req, config=CFG)
        self.assertEqual(
            result["concentration"]["level"], "UNKNOWN",
            "missing portfolio context did not produce UNKNOWN concentration: "
            "%r" % (result["concentration"],))
        offenders = _find_keys_containing(result, "correl")
        self.assertEqual(
            offenders, [],
            "found a key naming a correlation with no portfolio context "
            "supplied: %r" % (offenders,))


# ==========================================================================
# Machine-readable contract: the result is JSON, not merely JSON-shaped.
# ==========================================================================

class PositionSizingContractInvariants(unittest.TestCase):

    def test_result_is_always_json_serialisable(self):
        """the full result dict round-trips through json.dumps/json.loads."""
        conviction_opts = (None,) + CONVICTIONS_NO_NONE
        scenario_opts = (DEFAULT_SCENARIOS, THIN_SCENARIOS, ZERO_EDGE_SCENARIOS,
                         NEGATIVE_EDGE_SCENARIOS, NO_DOWNSIDE_SCENARIOS, None)
        thesis_opts = (None, "BROKEN")
        current_weight_opts = (0.0, 0.02, 0.5)
        n = 0
        for conv, scen, thesis, cw in itertools.product(
                conviction_opts, scenario_opts, thesis_opts, current_weight_opts):
            n += 1
            req = request(conviction=conv, scenarios=scen, thesis_status=thesis,
                         current_weight=cw)
            result = ps.size(req, config=CFG)
            try:
                text = json.dumps(result, allow_nan=False)
            except (TypeError, ValueError) as exc:
                self.fail("result is not JSON-serialisable for %r -> %s"
                          % (req, exc))
            round_tripped = json.loads(text)
            self.assertEqual(round_tripped["target"], result["target"])
            self.assertEqual(round_tripped["action"], result["action"])
            self.assertEqual(round_tripped["status"], result["status"])
        self.assertGreater(n, 50)


if __name__ == "__main__":
    unittest.main()
