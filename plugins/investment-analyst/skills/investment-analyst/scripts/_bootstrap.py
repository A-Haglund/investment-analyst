#!/usr/bin/env python3
"""One sibling-loading idiom, for new code, replacing the pattern that has
been copy-pasted into roughly a dozen scripts in this folder (portfolio_
review.py, valuation_gate.py, ttm_engine.py, earnings_quality.py, venues_se.py
and others each define their own near-identical `_load`/`load`).

There is exactly one behavioural detail worth centralising, because two of
those existing copies disagree on it: SystemExit does NOT inherit from
Exception. Every sibling script in this toolkit signals a hard, user-facing
failure - "DATA NOT AVAILABLE: ..." - by raising SystemExit, because that is
also how each behaves when run directly as a CLI. So a "soft" loader that is
only supposed to degrade gracefully when an optional sibling is missing or
broken must catch SystemExit explicitly, or it does not degrade at all:

    portfolio_review.py:87   catches (Exception, SystemExit)   -- correct
    valuation_gate.py:100    catches  Exception  only           -- wrong

valuation_gate.py's version will crash the whole CLI the moment an optional
sibling raises SystemExit during import-time setup, exactly the case a soft
loader exists to prevent. soft_load() below is written the correct way once,
so new code no longer has roughly even odds of copying the wrong example.

This module is for NEW code - numparse.py, http_util.py, finmath.py, and the
scripts wired in v3.0.0 to call them. It does not touch or replace the
existing dozen copies; those belong to work already in flight elsewhere in
this codebase, and migrating them is a separate, deliberately deferred task.
"""
import importlib.util
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def ensure_path():
    """Put SCRIPTS_DIR on sys.path if it is not already there.

    Appended, never inserted at position 0: a same-named module living in
    this folder must never be able to shadow a real stdlib module. Nothing
    in scripts/ collides with the standard library today, but that is a
    property of today's filenames, not a guarantee - the append-only rule is
    the actual safeguard.
    """
    if SCRIPTS_DIR not in sys.path:
        sys.path.append(SCRIPTS_DIR)


def load(name):
    """Import scripts/<name>.py as a standalone module and return it.

    Hard-required: raises exactly like a normal import would - ImportError
    for a missing/unloadable file, or whatever exception (including
    SystemExit) the sibling itself raises while executing at import time.
    Use this for a sibling this script cannot function without.
    """
    ensure_path()
    path = os.path.join(SCRIPTS_DIR, name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load %r from %s" % (name, path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def soft_load(name):
    """Like load(), but degrades to None with a one-line stderr note instead
    of raising - for an optional sibling that may be absent, mid-edit, or
    broken. Catches (Exception, SystemExit) - see the module docstring for
    why SystemExit must be listed explicitly, not assumed to be covered by
    Exception.
    """
    try:
        return load(name)
    except (Exception, SystemExit) as exc:
        print("(_bootstrap: %s not available - %s)" % (name, exc), file=sys.stderr)
        return None


def state_home():
    """Root directory for this plugin's persistent state - portfolios, the
    thesis ledger, watchlists, guidance tracking - normally
    ~/.investment-analyst.

    Overridable via the INVESTMENT_ANALYST_HOME environment variable. This
    is the one home shared by every store; the per-store env vars
    (PORTFOLIO_STORE_HOME, THESIS_LEDGER_HOME, WATCHLIST_STORE_HOME,
    GUIDANCE_STORE_HOME) are checked by each store BEFORE this function is
    even called and continue to take priority when set - they exist so the
    test suite never touches a real home directory, and this function does
    not change that.

    The real reason this exists: a scheduled/cloud run starts in a fresh
    container every time, so an unset override resolves under that
    container's throwaway home directory and every run sees an empty store
    - the daily portfolio job then finds no stored thesis for any holding
    and re-runs full analysis on all of them instead of a cheap HOLD.
    Pointing INVESTMENT_ANALYST_HOME at a mounted, persistent directory
    fixes that.

    `~` and environment variables are expanded in the override, same as a
    shell would. An unset or empty override reproduces today's exact
    ~/.investment-analyst path - this function is purely additive.
    """
    override = os.environ.get("INVESTMENT_ANALYST_HOME")
    if override:
        expanded = os.path.expandvars(os.path.expanduser(override))
        return os.path.abspath(expanded)
    return os.path.join(os.path.expanduser("~"), ".investment-analyst")


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if not args.selftest:
        parser.print_help()
        return 1

    ok = 0
    mod = load("numparse")
    assert mod.to_number("28 838") == 28838.0
    ok += 1

    missing = soft_load("this_module_does_not_exist_anywhere")
    assert missing is None
    ok += 1

    # Simulate a sibling that raises SystemExit during exec_module - the
    # real failure mode soft_load() must survive (see the module docstring).
    import tempfile
    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", dir=SCRIPTS_DIR, delete=False) as fh:
        fh.write("raise SystemExit('DATA NOT AVAILABLE: simulated failure')\n")
        temp_path = fh.name
    temp_name = os.path.splitext(os.path.basename(temp_path))[0]
    try:
        result = soft_load(temp_name)
        assert result is None, result
        ok += 1
    finally:
        os.remove(temp_path)

    ensure_path()
    ensure_path()  # calling twice must not duplicate the entry
    assert sys.path.count(SCRIPTS_DIR) == 1
    assert sys.path.index(SCRIPTS_DIR) != 0, "must never be inserted at position 0"
    ok += 1

    print("_bootstrap selftest: %d assertions ok" % ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
