#!/usr/bin/env python3
"""Shared plumbing for the investment-analyst regression suite.

Not a test file itself (unittest discover's default pattern is test_*.py, so
this is never collected).

Sibling scripts are not a package - they are a folder of standalone CLI tools
- so they are imported the same way verify_filing.py imports esef_fundamentals
and mfn_news: by file path, via importlib, never by `import scriptname`.
"""
import importlib.util
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(TESTS_DIR)
SCRIPTS_DIR = os.path.join(SKILL_DIR, "scripts")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def load(name):
    """Import scripts/<name>.py as a standalone module and return it.

    Raises like a normal import if the file is missing or fails to execute -
    use try_load() instead when the module is allowed to not exist yet.
    """
    path = os.path.join(SCRIPTS_DIR, name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def try_load(name):
    """Like load(), but returns None instead of raising.

    For a sibling script another agent is writing in parallel (valuation_gate.py
    at the time this suite was built) that may not exist yet, or may not import
    cleanly yet. A missing/broken dependency here must show up as a skipped
    test with a clear reason, never as a failure that looks like a bug in the
    suite itself.
    """
    path = os.path.join(SCRIPTS_DIR, name + ".py")
    if not os.path.isfile(path):
        return None
    try:
        return load(name)
    except Exception:
        return None


# --------------------------------------------------------------------------
# Network gating. Pure-logic tests (number parsing, temporal gates, the
# corroboration graph) must run offline, fast, every time. Tests that hit a
# live free endpoint (Nasdaq Nordic, filings.xbrl.org, MFN, FI, ESMA FIRDS,
# GLEIF...) are opt-in, so a network outage reads as "0 run, not applicable"
# rather than as a logic failure.
# --------------------------------------------------------------------------
NETWORK_ENV = "INVESTMENT_ANALYST_NETWORK_TESTS"
RUN_NETWORK = os.environ.get(NETWORK_ENV, "") == "1"

network = unittest.skipUnless(
    RUN_NETWORK,
    "network test skipped - set %s=1 (or run_tests.py --network) to run "
    "it against live free endpoints" % NETWORK_ENV)


def bootstrap_path():
    """Ensure `import helpers` resolves regardless of how a test file is
    invoked (unittest discover, run_tests.py, or `python test_x.py` directly)."""
    if TESTS_DIR not in sys.path:
        sys.path.insert(0, TESTS_DIR)
