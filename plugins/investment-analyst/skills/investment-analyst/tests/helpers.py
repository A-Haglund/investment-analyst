#!/usr/bin/env python3
"""Shared plumbing for the investment-analyst regression suite.

Not a test file itself (unittest discover's default pattern is test_*.py, so
this is never collected).

Sibling scripts are not a package - they are a folder of standalone CLI tools
- so they are imported the same way verify_filing.py imports esef_fundamentals
and mfn_news: by file path, via importlib, never by `import scriptname`.

The shared modules added in v3.0.0 (_bootstrap, numparse, http_util, finmath)
are the exception: they are imported by name, so SCRIPTS_DIR must be on
sys.path for a script loaded by file path to reach them. bootstrap_path()
puts it there - appended, never inserted at 0, so a scripts/ filename can
never shadow a stdlib module of the same name.
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

    Deliberately still file-path based rather than importlib.import_module:
    every existing test relies on getting a FRESH module object per call, so
    that monkeypatching one test file's copy cannot leak into another's.
    """
    bootstrap_path()
    path = os.path.join(SCRIPTS_DIR, name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def try_load(name):
    """Like load(), but returns None when the module does not EXIST yet.

    A module that is absent is a legitimate "not written yet" state and
    returns None so the caller can skip with a clear reason.

    A module that exists but fails to import is a BUG, and is re-raised.
    This used to swallow every exception, which meant a broken
    valuation_gate.py surfaced as "skipped" rather than "failed" - the suite
    hid the breakage instead of catching it. A refactor that breaks an import
    must fail loudly; that is the entire point of having 418 tests.
    """
    path = os.path.join(SCRIPTS_DIR, name + ".py")
    if not os.path.isfile(path):
        return None
    return load(name)


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
    """Make both `import helpers` and `import <shared script module>` resolve,
    regardless of how a test file is invoked (unittest discover, run_tests.py,
    or `python test_x.py` directly).

    SCRIPTS_DIR is APPENDED: stdlib must win any name collision.
    """
    if TESTS_DIR not in sys.path:
        sys.path.insert(0, TESTS_DIR)
    if SCRIPTS_DIR not in sys.path:
        sys.path.append(SCRIPTS_DIR)
