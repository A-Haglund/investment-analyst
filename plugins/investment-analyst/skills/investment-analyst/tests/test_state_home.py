#!/usr/bin/env python3
"""INVESTMENT_ANALYST_HOME: the shared override for the plugin's persistent
state root, added so a scheduled/cloud run (a fresh container every time)
can point every store at a mounted, persistent directory instead of the
container's throwaway home. Without it, ~/.investment-analyst.py resolves
under an ephemeral home on every run, the thesis ledger is always empty, and
the daily portfolio review's layer 2/3 re-runs full analysis on every
holding instead of a cheap HOLD - the defect this variable exists to fix.

_bootstrap.state_home() is the single home for the resolution logic; every
store (portfolio_store.py, thesis_ledger.py, watchlist_store.py,
guidance_track.py) calls it as the fallback below its own pre-existing
per-store override (PORTFOLIO_STORE_HOME etc.), which still takes priority
when set - that ordering is unchanged and re-checked here too.

Covers:
  * unset -> resolves under the real home directory, ending in
    .investment-analyst
  * set to a tmp dir -> every one of the four stores reads/writes there
  * set to a path containing "~" or an environment variable -> both expanded
  * set to "" -> falls back to the default, not to the empty string
  * a save/load round trip under the override, for portfolio_store.py and
    watchlist_store.py

Entirely offline. Uses helpers.load(), which returns a FRESH module object
per call, so each test gets its own copy and monkeypatched/reloaded state
from one test can never leak into another - this matters here specifically
because every test in this file mutates os.environ.
"""
import contextlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

ENV_VAR = "INVESTMENT_ANALYST_HOME"
_PER_STORE_VARS = (
    "PORTFOLIO_STORE_HOME", "THESIS_LEDGER_HOME",
    "WATCHLIST_STORE_HOME", "GUIDANCE_STORE_HOME",
)


@contextlib.contextmanager
def cleared_env(*names):
    """Remove `names` from os.environ for the life of the with-block and
    restore whatever was there (present or absent) afterwards."""
    saved = {n: os.environ.get(n) for n in names}
    for n in names:
        os.environ.pop(n, None)
    try:
        yield
    finally:
        for n, v in saved.items():
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v


@contextlib.contextmanager
def env_var(name, value):
    """Set `name` to `value` for the life of the with-block, restoring the
    previous value (or absence) afterwards."""
    old = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


# --------------------------------------------------------------------------

class StateHomeResolution(unittest.TestCase):
    """_bootstrap.state_home() directly - the one function every store's
    default falls back to."""

    def test_unset_resolves_under_the_real_home_directory(self):
        bs = load("_bootstrap")
        with cleared_env(ENV_VAR):
            home = bs.state_home()
        self.assertTrue(
            home.rstrip("/\\").endswith(".investment-analyst"), home)
        self.assertTrue(
            home.startswith(os.path.expanduser("~")), home)

    def test_set_to_tmp_dir_is_used_verbatim(self):
        bs = load("_bootstrap")
        with tempfile.TemporaryDirectory() as tmp:
            with env_var(ENV_VAR, tmp):
                home = bs.state_home()
            self.assertEqual(os.path.normcase(os.path.normpath(home)),
                             os.path.normcase(os.path.normpath(tmp)))

    def test_tilde_in_override_is_expanded(self):
        bs = load("_bootstrap")
        with env_var(ENV_VAR, os.path.join("~", "ia-state-home-test")):
            home = bs.state_home()
        self.assertNotIn("~", home)
        self.assertTrue(home.startswith(os.path.expanduser("~")), home)

    def test_env_var_in_override_is_expanded(self):
        bs = load("_bootstrap")
        with tempfile.TemporaryDirectory() as tmp:
            with env_var("IA_STATE_HOME_TEST_PROBE", tmp):
                override = os.path.join("$IA_STATE_HOME_TEST_PROBE", "sub")
                with env_var(ENV_VAR, override):
                    home = bs.state_home()
            self.assertEqual(
                os.path.normcase(os.path.normpath(home)),
                os.path.normcase(os.path.normpath(os.path.join(tmp, "sub"))))

    def test_empty_string_falls_back_to_default_not_to_empty(self):
        bs = load("_bootstrap")
        with env_var(ENV_VAR, ""):
            home = bs.state_home()
        self.assertNotEqual(home, "")
        self.assertTrue(
            home.rstrip("/\\").endswith(".investment-analyst"), home)


class EveryStoreHonoursTheOverride(unittest.TestCase):
    """With no per-store override set, every store's own *_home() must fall
    back through _bootstrap.state_home(), so INVESTMENT_ANALYST_HOME moves
    all four at once."""

    def test_portfolio_store(self):
        ps = load("portfolio_store")
        with cleared_env(*_PER_STORE_VARS), tempfile.TemporaryDirectory() as tmp:
            with env_var(ENV_VAR, tmp):
                home = ps.store_home()
            self.assertEqual(
                os.path.normcase(os.path.normpath(home)),
                os.path.normcase(os.path.normpath(os.path.join(tmp, "portfolio"))))

    def test_thesis_ledger(self):
        tl = load("thesis_ledger")
        with cleared_env(*_PER_STORE_VARS), tempfile.TemporaryDirectory() as tmp:
            with env_var(ENV_VAR, tmp):
                home = tl.ledger_home()
            self.assertEqual(
                os.path.normcase(os.path.normpath(home)),
                os.path.normcase(os.path.normpath(os.path.join(tmp, "thesis-ledger"))))

    def test_watchlist_store(self):
        ws = load("watchlist_store")
        with cleared_env(*_PER_STORE_VARS), tempfile.TemporaryDirectory() as tmp:
            with env_var(ENV_VAR, tmp):
                home = ws.store_home()
            self.assertEqual(
                os.path.normcase(os.path.normpath(home)),
                os.path.normcase(os.path.normpath(os.path.join(tmp, "watchlist"))))

    def test_guidance_track(self):
        gt = load("guidance_track")
        with cleared_env(*_PER_STORE_VARS), tempfile.TemporaryDirectory() as tmp:
            with env_var(ENV_VAR, tmp):
                home = gt.guidance_store_home()
            self.assertEqual(
                os.path.normcase(os.path.normpath(home)),
                os.path.normcase(os.path.normpath(os.path.join(tmp, "guidance"))))

    def test_per_store_override_still_wins_over_the_shared_one(self):
        """PORTFOLIO_STORE_HOME etc. must still take priority when set - the
        fix is additive, not a change to the existing precedence."""
        ps = load("portfolio_store")
        with tempfile.TemporaryDirectory() as shared, \
             tempfile.TemporaryDirectory() as specific:
            with env_var(ENV_VAR, shared), env_var("PORTFOLIO_STORE_HOME", specific):
                home = ps.store_home()
            self.assertEqual(
                os.path.normcase(os.path.normpath(home)),
                os.path.normcase(os.path.normpath(specific)))


class StoreRoundTripUnderTheOverride(unittest.TestCase):
    """A real save() then load(), with only INVESTMENT_ANALYST_HOME set (no
    per-store override) - proves the override is not just returned by
    store_home() but actually used for I/O."""

    def test_portfolio_store_round_trip(self):
        ps = load("portfolio_store")
        with cleared_env(*_PER_STORE_VARS), tempfile.TemporaryDirectory() as tmp:
            with env_var(ENV_VAR, tmp):
                doc = {"account_type": "ISK", "currency": "SEK",
                       "cash": {"amount": 1000.0, "currency": "SEK"},
                       "holdings": [
                           {"lei": None, "isin": None, "name": "Test Co",
                            "symbol": None, "quantity": 10,
                            "cost_per_share": None, "cost_currency": None,
                            "acquired": None, "note": ""}]}
                ps.save(doc, "ia-home-test")
                back = ps.load("ia-home-test")
            self.assertEqual(back["holdings"][0]["name"], "Test Co")
            self.assertEqual(back["cash"]["amount"], 1000.0)
            written = os.path.join(tmp, "portfolio", "ia-home-test.json")
            self.assertTrue(os.path.isfile(written), written)

    def test_watchlist_store_round_trip(self):
        ws = load("watchlist_store")
        with cleared_env(*_PER_STORE_VARS), tempfile.TemporaryDirectory() as tmp:
            with env_var(ENV_VAR, tmp):
                doc = {"entries": [{"name": "Test Watch Co", "lei": None,
                                    "isin": None, "symbol": None,
                                    "why": "", "target_price": None,
                                    "target_currency": None,
                                    "last_checked": None}]}
                ws.save(doc, "ia-home-test")
                back = ws.load("ia-home-test")
            self.assertEqual(back["entries"][0]["name"], "Test Watch Co")
            written = os.path.join(tmp, "watchlist", "ia-home-test.json")
            self.assertTrue(os.path.isfile(written), written)


if __name__ == "__main__":
    unittest.main()
