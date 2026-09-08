#!/usr/bin/env python3
"""Regression tests for the instruction layer — the Markdown the model reads.

The Python layer has ~1000 tests. The Markdown had none, which meant a rename,
a section renumbering or a moved rule could break a run with the whole suite
still green. Progressive disclosure makes that worse: once a rule lives in a
file that is loaded conditionally, a broken pointer is a rule that silently
never arrives.

Four things are asserted here.

  Resolution   every reference file, script and section number a load
               instruction points at actually exists.

  Reachability every file in references/ is named by some load instruction, so
               a file cannot quietly stop being loaded and cannot linger after
               its last caller is gone.

  Single home  a rule with exactly one home has exactly one home. The
               conviction ladder and its caps in conviction.md are the
               canonical case.

  No drift     a constant stated in prose equals the constant in the code that
               enforces it. verification.md claimed 0.5% while quote.py used
               2%; nothing caught it.

The size budgets at the end are a *token* regression test. Progressive
disclosure only pays as long as the files stay small; a budget makes regrowth
a test failure rather than an invisible cost.
"""
import os
import re
import unittest

import helpers

SKILL_DIR = helpers.SKILL_DIR
PLUGIN_DIR = os.path.dirname(os.path.dirname(SKILL_DIR))
COMMANDS_DIR = os.path.join(PLUGIN_DIR, "commands")
REFERENCES_DIR = os.path.join(SKILL_DIR, "references")
SCRIPTS_DIR = helpers.SCRIPTS_DIR
SKILL_MD = os.path.join(SKILL_DIR, "SKILL.md")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def instruction_files():
    """Every Markdown file the model can be asked to read, as {label: text}."""
    out = {"SKILL.md": read(SKILL_MD)}
    for directory, prefix in ((COMMANDS_DIR, "commands"),
                              (REFERENCES_DIR, "references")):
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if name.endswith(".md"):
                out[f"{prefix}/{name}"] = read(os.path.join(directory, name))
    return out


# A path token in prose: `references/foo.md`, scripts/bar.py, and so on. Both
# backticked and bare forms occur, so the delimiter is not part of the match.
REF_TOKEN = re.compile(r"references/([A-Za-z0-9_.-]+\.md)")
SCRIPT_TOKEN = re.compile(r"scripts/([A-Za-z0-9_.-]+\.py)")
# "§7", "§ 7", "§9.4", "SKILL.md §3"
SECTION_TOKEN = re.compile(r"§\s*(\d+(?:\.\d+)?)")
NUMBERED_HEADING = re.compile(r"^##\s+(\d+)\.\s", re.MULTILINE)


class ReferenceResolution(unittest.TestCase):
    """Every pointer resolves to something that exists."""

    def test_referenced_reference_files_exist(self):
        missing = []
        for label, text in instruction_files().items():
            for name in set(REF_TOKEN.findall(text)):
                if not os.path.exists(os.path.join(REFERENCES_DIR, name)):
                    missing.append(f"{label} -> references/{name}")
        self.assertEqual([], sorted(missing),
                         "load instructions point at reference files that do "
                         "not exist; the rules in them never reach the model")

    def test_referenced_scripts_exist(self):
        missing = []
        for label, text in instruction_files().items():
            for name in set(SCRIPT_TOKEN.findall(text)):
                if not os.path.exists(os.path.join(SCRIPTS_DIR, name)):
                    missing.append(f"{label} -> scripts/{name}")
        self.assertEqual([], sorted(missing),
                         "instructions name scripts that do not exist")

    def test_no_orphan_reference_files(self):
        """A reference no LOAD INSTRUCTION points at is silently dropped.

        Only SKILL.md and the command files cause a load. A sibling reference
        citing a file is a cross-reference, read after the file is already in
        context; counting those as "named" let a file whose every load
        instruction had been deleted still pass.
        """
        named = set()
        for label, text in instruction_files().items():
            if label == "SKILL.md" or label.startswith("commands/"):
                named.update(REF_TOKEN.findall(text))
        on_disk = {n for n in os.listdir(REFERENCES_DIR) if n.endswith(".md")}
        orphans = sorted(on_disk - named)
        self.assertEqual([], orphans,
                         "reference files no load instruction reaches: wire "
                         "them into a phase or the §11 table, or delete them")

    def test_every_reference_declares_a_load_condition(self):
        """The §11 table is where "load when" lives; a file missing from it
        has no declared condition, so nothing says when its rules arrive."""
        skill = read(SKILL_MD)
        table = skill[skill.index("## 11."):skill.index("## 12.")]
        declared = set(REF_TOKEN.findall(table))
        on_disk = {n for n in os.listdir(REFERENCES_DIR) if n.endswith(".md")}
        missing = sorted(on_disk - declared)
        self.assertEqual([], missing,
                         "reference files with no row in the §11 load table")

    def test_skill_section_crossrefs_resolve(self):
        """A `§N` pointing at SKILL.md must match a numbered heading there."""
        skill = read(SKILL_MD)
        existing = {m for m in NUMBERED_HEADING.findall(skill)}
        broken = []
        for label, text in instruction_files().items():
            for line in text.splitlines():
                if "SKILL.md" not in line:
                    continue
                for sec in SECTION_TOKEN.findall(line):
                    # Exact match, not the part before the dot: SKILL.md
                    # numbers only top-level sections, so "§6.1" points at
                    # nothing and must not pass as "§6".
                    if sec not in existing:
                        broken.append(f"{label}: SKILL.md §{sec}")
        self.assertEqual([], sorted(set(broken)),
                         "cross-references point at SKILL.md sections that do "
                         "not exist")


class SingleHome(unittest.TestCase):
    """A value has exactly one home (CLAUDE.md's stated invariant)."""

    def test_data_quality_declares_itself_normative(self):
        """The canonical home must say so, or "one home" is only a habit."""
        text = read(os.path.join(REFERENCES_DIR, "conviction.md")).lower()
        self.assertIn("normative source", text,
                      "conviction.md no longer claims to be the normative "
                      "source for the conviction ladder")

    def test_conviction_ladder_tabulated_only_in_data_quality(self):
        """The ladder is canonical in conviction.md alone.

        Naming one cap for one depth is sanctioned quoting; re-tabulating the
        ladder is the duplication the structure exists to prevent. The test
        looks for that table shape — a header row pairing conviction with a
        cap or ceiling — and not for the loose word "cap", which also occurs
        inside "capital allocation" and "market cap".
        """
        wanted = re.compile(r"\bconviction\b", re.IGNORECASE)
        bound = re.compile(r"\b(caps?|ceilings?)\b", re.IGNORECASE)
        separator = re.compile(r"^\|[\s:|-]+\|?\s*$")
        offenders = []
        for label, text in instruction_files().items():
            if label == "references/conviction.md":
                continue
            lines = text.splitlines()
            for i, line in enumerate(lines):
                # A header row, not any row: the line after it is the
                # |---|---| separator. Catalog rows that merely describe a
                # script's job are prose, not a re-tabulated ladder.
                if not line.startswith("|") or i + 1 >= len(lines):
                    continue
                if not separator.match(lines[i + 1]):
                    continue
                if wanted.search(line) and bound.search(line):
                    offenders.append(f"{label}: {line.strip()[:60]}")
                    break
        self.assertEqual([], sorted(offenders),
                         "conviction ladder re-tabulated outside "
                         "conviction.md; it has exactly one home")


class ConstantDrift(unittest.TestCase):
    """A constant stated in prose must equal the constant the code enforces.

    This is the class of defect that shipped undetected: verification.md
    described the price cross-check as 0.5% while quote.py compared at 2%.
    """

    def stated_after(self, text, phrase):
        """Percentages governed by `phrase`, within the 40 chars after it.

        Adjacency, not paragraph scope: a paragraph routinely quotes several
        unrelated percentages (a worked example's feed spread, a tally), and
        scoping to the paragraph picks those up too. Newlines are folded first
        because prose is hard-wrapped between the phrase and its number.
        """
        flat = re.sub(r"\s+", " ", text)
        found = set()
        for match in re.finditer(phrase, flat, re.IGNORECASE):
            window = flat[match.end():match.end() + 40]
            hit = re.match(r"[^%]{0,30}?(\d+(?:\.\d+)?)\s*%", window)
            if hit:
                found.add(float(hit.group(1)))
        return found

    def test_price_cross_check_tolerance_matches_quote_py(self):
        quote = helpers.load("quote")
        code_pct = quote.CROSS_CHECK_TOLERANCE * 100
        text = read(os.path.join(REFERENCES_DIR, "verification.md"))
        stated = self.stated_after(text, r"diverge\w*\s+by\s+more\s+than")
        self.assertTrue(stated, "verification.md no longer states a "
                                "divergence threshold; this test needs a new "
                                "anchor or the rule was lost")
        for value in stated:
            self.assertAlmostEqual(
                code_pct, value, places=6,
                msg=f"verification.md says {value}% but "
                    f"quote.CROSS_CHECK_TOLERANCE is {code_pct}%")

    def test_corroboration_tolerance_matches_finfact(self):
        finfact = helpers.load("finfact")
        default = None
        for attr in ("CORROBORATION_TOLERANCE", "DEFAULT_TOLERANCE"):
            if hasattr(finfact, attr):
                default = getattr(finfact, attr)
                break
        if default is None:
            import inspect
            sig = inspect.signature(finfact.corroborate)
            param = sig.parameters.get("tolerance")
            if param is None or param.default is inspect.Parameter.empty:
                self.skipTest("finfact.corroborate has no default tolerance "
                              "to compare against")
            default = param.default
        text = read(os.path.join(REFERENCES_DIR, "verification.md"))
        stated = self.stated_after(text, r"origins\s+agree\s+within")
        for value in stated:
            self.assertAlmostEqual(
                default * 100, value, places=6,
                msg=f"verification.md says VERIFIED within {value}% but "
                    f"finfact corroborates at {default * 100}%")

    def test_margin_tolerance_matches_decision_record(self):
        record = helpers.load("decision_record")
        if not hasattr(record, "TOLERANCE_PP"):
            self.skipTest("decision_record.TOLERANCE_PP is gone; the prose "
                          "anchor needs updating with it")
        # TOLERANCE_PP is held as a fraction (0.0015), while the prose states
        # percentage points (0.15pp). Same quantity, different unit.
        code_pp = record.TOLERANCE_PP * 100
        stated = set()
        for label, text in instruction_files().items():
            for line in text.splitlines():
                if "tolerance" in line.lower():
                    for value in re.findall(r"(\d+(?:\.\d+)?)\s*pp\b", line):
                        stated.add(float(value))
        for value in stated:
            self.assertAlmostEqual(
                code_pp, value, places=6,
                msg=f"prose states a {value}pp tolerance but "
                    f"decision_record.TOLERANCE_PP is {code_pp}pp")


class RequiredRules(unittest.TestCase):
    """Rules whose loss would change a verdict must still be somewhere."""

    def corpus(self):
        return "\n".join(instruction_files().values()).lower()

    def test_core_rules_survive(self):
        corpus = self.corpus()
        required = {
            "no fabricated numbers": ("never invent", "do not invent",
                                      "never fabricat"),
            "sourced is not verified": ("sourced is not verified",
                                        "sourced but not verified"),
            "hard refusal token": ("data not available",),
            "as-of timestamp": ("as-of", "as of"),
            "conviction ceiling": ("conviction ceiling", "conviction cap"),
            "ambiguity refusal": ("refuse", "ambiguous"),
            "not-checked is its own state": ("not checked",),
        }
        for rule, variants in sorted(required.items()):
            self.assertTrue(any(v in corpus for v in variants),
                            f"the instruction layer no longer states: {rule}")

    def test_all_six_depths_defined(self):
        skill = read(SKILL_MD)
        for depth in ("TLDR", "QUICK", "COMPARE", "STANDARD", "DEEP",
                      "PORTFOLIO"):
            self.assertIn(depth, skill,
                          f"depth token {depth} is not defined in SKILL.md")

    def test_every_research_phase_present(self):
        skill = read(SKILL_MD)
        for phase in range(11):
            self.assertRegex(
                skill, rf"Phase\s+{phase}\b",
                f"research phase {phase} is missing from SKILL.md")

    def test_output_language_rule_present(self):
        corpus = self.corpus()
        self.assertTrue(
            "language of the question" in corpus
            or "user's language" in corpus
            or "language the question" in corpus,
            "the output-language rule is gone; Swedish questions would get "
            "English answers")


class ContextBudgets(unittest.TestCase):
    """Token regression: the instruction layer must not silently regrow.

    Budgets are characters, generous enough not to fire on ordinary editing
    and tight enough that a monolith cannot come back. Raising one is a
    deliberate act with a reason; that is the point.
    """

    # file -> max characters
    BUDGETS = {
        "SKILL.md": 62_000,
        "references/answer-structure.md": 6_000,
        "references/bear-case-and-scoring.md": 12_000,
        "references/conviction.md": 6_500,
        "references/data-quality.md": 11_000,
        "references/data-sources.md": 26_000,
        "references/europe.md": 10_000,
        "references/fundamentals.md": 10_000,
        "references/moat-growth-management.md": 9_000,
        "references/portfolio.md": 18_000,
        "references/ranking.md": 3_000,
        "references/red-flags-general.md": 18_500,
        "references/red-flags-quick.md": 9_200,
        "references/red-flags-smallcap.md": 22_000,
        "references/scripts.md": 9_000,
        "references/source-registry.md": 15_000,
        "references/sweden.md": 21_000,
        "references/sweden-deep.md": 9_200,
        "references/valuation-core.md": 14_000,
        "references/valuation-dcf.md": 9_000,
        "references/verification.md": 13_000,
    }

    def test_no_file_exceeds_its_budget(self):
        over = []
        for label, text in instruction_files().items():
            budget = self.BUDGETS.get(label)
            if budget is None:
                continue
            if len(text) > budget:
                over.append(f"{label}: {len(text)} > {budget}")
        self.assertEqual([], sorted(over),
                         "instruction files grew past their context budget; "
                         "every character here is re-sent on every tool call")

    def test_every_instruction_file_has_a_budget(self):
        """A new reference must declare a budget, or it escapes the guard."""
        unbudgeted = sorted(
            label for label in instruction_files()
            if label not in self.BUDGETS and not label.startswith("commands/")
        )
        self.assertEqual([], unbudgeted,
                         "instruction files with no context budget; add them "
                         "to BUDGETS so regrowth is caught")

    def test_commands_stay_thin(self):
        """A command routes; it does not restate the skill."""
        fat = []
        for label, text in instruction_files().items():
            if label.startswith("commands/") and len(text) > 9_500:
                fat.append(f"{label}: {len(text)}")
        self.assertEqual([], sorted(fat),
                         "command files should route to SKILL.md sections, "
                         "not reproduce their content")


if __name__ == "__main__":
    unittest.main(verbosity=2)
