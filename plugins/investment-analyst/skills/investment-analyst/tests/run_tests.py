#!/usr/bin/env python3
"""Run the investment-analyst regression suite and print a readable summary.

Usage:
    python run_tests.py                 # offline tests only (fast, no network)
    python run_tests.py --network       # also run tests hitting live free endpoints
    python run_tests.py -v              # verbose per-test output
    python run_tests.py -k share_class  # only tests whose name matches

Equivalent to (offline only):
    python -m unittest discover -s . -p "test_*.py"

Network tests (Nasdaq Nordic, filings.xbrl.org, MFN, FI, ESMA FIRDS, GLEIF)
are skipped by default. Set INVESTMENT_ANALYST_NETWORK_TESTS=1 or pass
--network to include them; a network outage during those should show up as
a clearly-labelled failure on a *_NETWORK* test, never as a silent pass and
never disguised as a logic bug elsewhere.
"""
import argparse
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--network", action="store_true",
                    help="also run tests that hit live free endpoints")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print each test's name and result as it runs")
    ap.add_argument("-k", metavar="SUBSTRING", default=None,
                    help="only run tests whose method name contains SUBSTRING")
    args = ap.parse_args()

    if args.network:
        os.environ["INVESTMENT_ANALYST_NETWORK_TESTS"] = "1"
    network_on = os.environ.get("INVESTMENT_ANALYST_NETWORK_TESTS") == "1"

    if HERE not in sys.path:
        sys.path.insert(0, HERE)

    loader = unittest.TestLoader()
    if args.k:
        loader.testNamePatterns = ["*%s*" % args.k]
    suite = loader.discover(start_dir=HERE, pattern="test_*.py", top_level_dir=HERE)

    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1, stream=sys.stdout)
    t0 = time.time()
    result = runner.run(suite)
    elapsed = time.time() - t0

    total = result.testsRun
    failed = len(result.failures)
    errored = len(result.errors)
    skipped = len(result.skipped)
    passed = total - failed - errored - skipped

    print()
    print("=" * 72)
    print("SUMMARY  (%.2fs elapsed, network tests %s)"
          % (elapsed, "ENABLED" if network_on
             else "DISABLED - pass --network to include them"))
    print("  ran       %d" % total)
    print("  passed    %d" % passed)
    print("  failed    %d" % failed)
    print("  errors    %d" % errored)
    print("  skipped   %d" % skipped)

    if skipped:
        print()
        print("  skipped:")
        for test, reason in result.skipped:
            print("    - %s" % test)
            print("        %s" % reason)

    if failed:
        print()
        print("  FAILURES (a genuine defect the suite found, or an outdated")
        print("  expectation in the suite itself - read each one):")
        for test, _tb in result.failures:
            print("    - %s" % test)

    if errored:
        print()
        print("  ERRORS (the test itself could not run - often a missing")
        print("  sibling module or an import problem, not necessarily a bug")
        print("  in the code under test):")
        for test, _tb in result.errors:
            print("    - %s" % test)

    print("=" * 72)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
