#!/usr/bin/env python3
"""insider_se.py — --limit was honoured in text mode but not in --json mode.

Confirmed defect (pre-fix): report_one() sliced `displayed[:args.limit]`
before printing the text table (then at line ~797), but the --json branch
(then at lines ~936-956, and the ambiguous branch at ~910-919) always dumped
every transaction in the display range regardless of --limit. SKILL.md §12
tells the model to *always* call scripts with --json, and the insider
section (SKILL.md §"Read the classification, not the total") tells it to
read the aggregate, not the transaction list — so a busy large cap could
dump every PDMR row (tens of thousands of characters) into context for
nothing that read it.

The fix:
  1. --json now slices "transactions" to args.limit, exactly like text mode
     (same default: 40). A new "transactions_returned" / "transactions_truncated"
     pair says so explicitly; "count" keeps its old meaning (rows in the
     display range, NOT capped by --limit), so old callers reading "count"
     see the same number as before.
  2. A new --summary flag drops "transactions" to [] entirely (in both --json
     and text mode) while leaving "analysis" (the aggregate/classification -
     net direction, counts, totals, windows, source, retrieved_utc) untouched.
  3. Plain --json with no --summary and a small (under-the-default-limit)
     row set is byte-for-byte unaffected: this is what "preserve the
     existing --json shape" means in practice - old callers with typical,
     small transaction counts see no behaviour change at all.

All offline: fetch_csv() is monkeypatched with an in-memory FI-shaped CSV,
following the run_main() pattern established in test_ownership_se.py.
"""
import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

m = load("insider_se")

HEADER = (
    "Issuer;Person discharging managerial responsibilities;Position;"
    "Closely associated;Nature of transaction;Instrument name;"
    "Intrument type;ISIN;LEI-code;Transaction date;Publication date;"
    "Volume;Unit;Price;Currency;Linked to share option programme;"
    "Amendment;Status")


def fi_row(pdmr, day, nature="Acquisition", status="Current"):
    """One FI export row: an open-market share trade, dated 2026-09-<day>."""
    return ";".join([
        "Test AB", pdmr, "CEO", "", nature, "Shares", "Share",
        "SE0000000001", "LEI1", "2026-09-%02d" % day, "2026-09-%02d" % day,
        "100", "Shares", "10,0", "SEK", "No", "", status])


def csv_with(n):
    """n distinct discretionary BUY rows, one PDMR each, days 1..n."""
    rows = [fi_row("Person%d" % i, i) for i in range(1, n + 1)]
    return HEADER + "\n" + "\n".join(rows)


def run_main(argv, text):
    """Run main() end-to-end against canned FI CSV text, capturing stdout."""
    buf = io.StringIO()
    with mock.patch.object(m, "fetch_csv", lambda *a, **k: text), \
         mock.patch.object(sys, "argv", ["insider_se.py"] + argv), \
         contextlib.redirect_stdout(buf):
        m.main()
    return buf.getvalue()


BASE_ARGV = ["--issuer", "Test", "--json", "--no-widen",
             "--from", "2026-09-01", "--to", "2026-09-30"]


class JsonLimitIsHonoured(unittest.TestCase):
    """The reported defect: --limit was applied in text mode only."""

    def test_json_transactions_are_capped_at_the_default_limit(self):
        text = csv_with(60)
        out = run_main(list(BASE_ARGV), text)
        d = json.loads(out)
        self.assertEqual(d["count"], 60,
                          "'count' must still report the full display-range "
                          "row count, unaffected by --limit")
        self.assertEqual(len(d["transactions"]), 40,
                          "the JSON 'transactions' array must be capped at "
                          "the default --limit (40), exactly like text mode")
        self.assertTrue(d["transactions_truncated"])
        self.assertEqual(d["transactions_returned"], 40)

    def test_json_respects_an_explicit_limit(self):
        text = csv_with(60)
        out = run_main(list(BASE_ARGV) + ["--limit", "5"], text)
        d = json.loads(out)
        self.assertEqual(len(d["transactions"]), 5,
                          "--limit 5 must cap the JSON transactions array at "
                          "5 rows, not dump all 60")
        self.assertEqual(d["count"], 60)
        self.assertTrue(d["transactions_truncated"])

    def test_ambiguous_json_branch_also_honours_limit(self):
        """The second dump site the report named: the per-cluster
        'issuer_matches[].transactions' list in the ambiguous-issuer branch.

        cluster_issuers() groups by raw issuer STRING first, then merges
        different strings only when they share an ISIN/LEI - so two rows
        under the identical string "Test AB" are always one cluster. To
        force the ambiguous branch (>1 real cluster under one --issuer
        query) the two issuers need distinct raw strings that both still
        match the "Test" substring filter and carry distinct ISIN/LEI.
        """
        rows_a = [fi_row("PersonA%d" % i, i) for i in range(1, 45)]
        rows_b = ";".join(["Testing Group AB", "PersonB1", "CFO", "", "Disposal",
                            "Shares", "Share", "SE0000000002", "LEI2",
                            "2026-09-05", "2026-09-05", "50", "Shares",
                            "20,0", "SEK", "No", "", "Current"])
        text = HEADER + "\n" + "\n".join(rows_a) + "\n" + rows_b
        out = run_main(list(BASE_ARGV) + ["--limit", "10"], text)
        d = json.loads(out)
        self.assertTrue(d["ambiguous"])
        self.assertGreaterEqual(len(d["issuer_matches"]), 2)
        big = max(d["issuer_matches"], key=lambda x: x["displayed_count"])
        self.assertEqual(big["displayed_count"], 44)
        self.assertEqual(len(big["transactions"]), 10,
                          "each cluster's transaction list must respect "
                          "--limit too, not just the single-issuer branch")


class SummaryFlagOmitsRowsKeepsAggregate(unittest.TestCase):
    def test_summary_json_has_no_rows_but_keeps_the_aggregate(self):
        text = csv_with(30)
        out = run_main(list(BASE_ARGV) + ["--summary"], text)
        d = json.loads(out)
        self.assertEqual(d["transactions"], [],
                         "--summary must omit every per-transaction row")
        self.assertIn("transactions_omitted", d)
        self.assertIsNotNone(d["analysis"],
                             "the aggregate/classification must survive "
                             "--summary intact")
        disc = d["analysis"]["discretionary"]
        self.assertEqual(disc["buy_rows"], 30)
        self.assertEqual(disc["buy_value"], 30000.0)
        self.assertEqual(disc["net_value"], 30000.0)
        # Provenance the aggregate needs must still be present.
        self.assertIn("source", d)
        self.assertIn("retrieved_utc", d)
        self.assertEqual(d["analysis_row_count"], 30)

    def test_summary_shrinks_output_a_lot_for_a_busy_issuer(self):
        """The whole point: a busy large cap must not dump thousands of
        characters of rows nothing reads. Compare against an explicit large
        --limit so the comparison is against the full row dump (the old,
        unbounded --json behaviour), not against the new default-40 cap."""
        text = csv_with(200)
        full = run_main(list(BASE_ARGV) + ["--limit", "1000"], text)
        summary = run_main(list(BASE_ARGV) + ["--summary"], text)
        self.assertGreater(len(full), 20000,
                           "sanity check: the uncapped dump should itself be "
                           "tens of thousands of characters, matching the "
                           "reported defect")
        self.assertLess(len(summary), len(full) / 3,
                        "--summary should cut the JSON payload drastically "
                        "by dropping the row dump")

    def test_summary_also_suppresses_the_text_mode_table(self):
        text = csv_with(10)
        out = run_main(["--issuer", "Test", "--no-widen",
                        "--from", "2026-09-01", "--to", "2026-09-30",
                        "--summary"], text)
        self.assertNotIn("Person1", out,
                         "--summary must drop individual PDMR rows from the "
                         "text listing too")
        self.assertIn("omitted", out)


class PlainJsonUnchangedFromBefore(unittest.TestCase):
    """For a small, ordinary transaction count (under the default limit of
    40 - the common case), the fix must be invisible: every previously
    present key keeps its previous meaning and value."""

    def test_small_result_set_is_returned_in_full_as_before(self):
        text = csv_with(7)
        out = run_main(list(BASE_ARGV), text)
        d = json.loads(out)
        self.assertEqual(len(d["transactions"]), 7)
        self.assertEqual(d["count"], 7)
        self.assertFalse(d["transactions_truncated"])
        self.assertFalse(d["summary"])
        # Original shape: same top-level keys still present.
        for key in ("issuer_query", "lei_query", "isin_query", "from", "to",
                    "analysis_from", "ambiguous", "count", "analysis_row_count",
                    "excluded_cancelled_or_revised", "export_truncated_at_1000",
                    "unparsed_volume_rows", "unparsed_price_rows", "source",
                    "basis", "retrieved_utc", "analysis", "transactions"):
            self.assertIn(key, d, "%r must still be present in the JSON shape" % key)

    def test_no_summary_flag_means_summary_is_false(self):
        text = csv_with(3)
        out = run_main(list(BASE_ARGV), text)
        d = json.loads(out)
        self.assertFalse(d["summary"])
        self.assertNotIn("transactions_omitted", d)


if __name__ == "__main__":
    unittest.main()
