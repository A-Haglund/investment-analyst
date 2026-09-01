#!/usr/bin/env python3
"""insider_se.py's FI Volume/Price number parsing.

FI's export is usually decimal-comma with no grouping ("16,6"), but a
comma-grouped thousands separator ("1,000") and a space-grouped one
("1 234,5") both appear too. Old behaviour: a bare `.replace(",", ".")` read
"1,000" as 1.0 - a silent thousandfold error - and raised a ValueError on
"1 234,5" (comma preceded by a 4-digit integer part it could not turn into a
float), which was then swallowed into a silent 0.0 with no sign anything
had gone wrong.

The fix (parse_fi_number) distinguishes a comma followed by groups of
exactly three digits (thousands) from one followed by one or two digits
(decimal), the same rule mfn_news.to_number() uses for MFN release text, and
reports every field it genuinely could not parse in parse_stats rather than
folding it into 0.0.

All offline: pure string/CSV parsing, no network.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import load

insider_se = load("insider_se")

CSV_HEADER = (
    "Issuer;Person discharging managerial responsibilities;Position;"
    "Closely associated;Nature of transaction;Instrument name;"
    "Intrument type;ISIN;LEI-code;Transaction date;Publication date;"
    "Volume;Unit;Price;Currency;Linked to share option programme;"
    "Amendment;Status")


def csv_row(**fields):
    order = ["Issuer", "Person discharging managerial responsibilities",
             "Position", "Closely associated", "Nature of transaction",
             "Instrument name", "Intrument type", "ISIN", "LEI-code",
             "Transaction date", "Publication date", "Volume", "Unit",
             "Price", "Currency", "Linked to share option programme",
             "Amendment", "Status"]
    return ";".join(fields.get(k, "") for k in order)


class ParseFiNumber(unittest.TestCase):
    def test_comma_grouped_thousands_is_not_read_as_a_decimal(self):
        value, ok = insider_se.parse_fi_number("1,000")
        self.assertTrue(ok)
        self.assertEqual(value, 1000.0,
                         "'1,000' must parse as one thousand, not 1.0 (the "
                         "old .replace(',', '.') bug)")

    def test_space_grouped_thousands_with_a_decimal_comma(self):
        value, ok = insider_se.parse_fi_number("1 234,5")
        self.assertTrue(ok, "'1 234,5' must parse successfully, not raise "
                        "and silently become 0.0")
        self.assertEqual(value, 1234.5)

    def test_plain_decimal_comma_is_unaffected(self):
        """Control: the ordinary FI case ("16,6") must still work."""
        value, ok = insider_se.parse_fi_number("16,6")
        self.assertTrue(ok)
        self.assertEqual(value, 16.6)

    def test_genuinely_unparseable_value_is_reported_not_zeroed(self):
        value, ok = insider_se.parse_fi_number("garbage")
        self.assertFalse(ok, "an unparseable field must come back with "
                         "ok=False so the caller can count it")
        self.assertEqual(value, 0.0)


class ParseStatsCountUnparseableFields(unittest.TestCase):
    """parse() must surface an unparseable Volume/Price field in
    parse_stats, never fold it into a value-looking 0.0 with no trace."""

    def test_an_unparseable_volume_is_counted_not_silently_zeroed(self):
        row = csv_row(Issuer="Test AB", Position="CEO",
                      **{"Nature of transaction": "Acquisition"},
                      **{"Instrument name": "Shares"},
                      **{"Intrument type": "Share"},
                      ISIN="SE0000000001",
                      **{"Transaction date": "2026-01-01"},
                      **{"Publication date": "2026-01-02"},
                      Volume="garbage", Unit="Shares", Price="10,5",
                      Currency="SEK",
                      **{"Linked to share option programme": "No"},
                      Status="NEW")
        text = CSV_HEADER + "\n" + row
        rows, stats = insider_se.parse(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["volume"], 0.0)
        self.assertEqual(rows[0]["price"], 10.5)
        self.assertEqual(stats["unparsed_volume"], 1,
                         "an unparseable Volume must be counted in "
                         "parse_stats, not silently treated as a real zero")
        self.assertEqual(stats["unparsed_price"], 0)

    def test_a_fully_parseable_row_counts_nothing_as_unparsed(self):
        """Control: ordinary rows must not spuriously count as unparsed."""
        row = csv_row(Issuer="Test AB", Position="CEO",
                      **{"Nature of transaction": "Acquisition"},
                      **{"Instrument name": "Shares"},
                      **{"Intrument type": "Share"},
                      ISIN="SE0000000001",
                      **{"Transaction date": "2026-01-01"},
                      **{"Publication date": "2026-01-02"},
                      Volume="1,000", Unit="Shares", Price="16,6",
                      Currency="SEK",
                      **{"Linked to share option programme": "No"},
                      Status="NEW")
        text = CSV_HEADER + "\n" + row
        rows, stats = insider_se.parse(text)
        self.assertEqual(rows[0]["volume"], 1000.0)
        self.assertEqual(rows[0]["price"], 16.6)
        self.assertEqual(stats["unparsed_volume"], 0)
        self.assertEqual(stats["unparsed_price"], 0)


if __name__ == "__main__":
    unittest.main()
