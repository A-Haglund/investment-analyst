---
description: Equity research analysis of one company, ending in a BUY/HOLD/SELL call
argument-hint: <ticker or company name> [--quick | --deep]
---

Analyse: **$ARGUMENTS**

Use the `investment-analyst` skill.

**Depth** — read `SKILL.md` §4 and apply it:

- `--quick` in the arguments → QUICK (2–4 min, 350 words)
- `--deep` in the arguments → DEEP (25–35 min, 1200 words, full DCF and reverse DCF)
- neither → **STANDARD** (8–12 min, 800 words): all phases, fair value from multiples rather than a DCF

Strip the flag from the company name before resolving the ticker. State the
depth you are running in the first line of the output.

## Output: seven sections per SKILL.md §6

1. **Verdict block** (SKILL.md §5) — the call in ten seconds
2. **What the company is** — what it owns and does now, 3–5 marked bullets
3. **Why the price is where it is** — the one thing driving the case, 2–4 sentences
4. **Talar för / Talar emot** — evidence already established, two marked lists, 3–5 items each
5. **Scenarios** — bear, base, bull with a value, one table plus range marker
6. **What the call means in practice** — the call translated into action, 2–4 sentences
7. **Slutsats** — signal line, Bevakning, Horisont, Viktigast per SKILL.md §9

Each section carries its own word budget — SKILL.md §6 sets them, do not restate
the table here. At DEEP every budget scales ×1.5. At QUICK, sections 3 and 5 are
dropped and the rest are halved.

## Tagging and uncertainty (SKILL.md §§1, 7)

The tagging discipline (FACT / ESTIMATE / ASSUMPTION / OPINION) governs the work but does not appear in the delivered answer. Uncertainty reaches the reader in plain words per SKILL.md §7: a warning about a single-sourced figure becomes "bolagets egen siffra, ingen oberoende källa bekräftar den", an estimate becomes "analytikernas prognos, inte ett utfall", and so on. The Evidence block and the decision record are produced in full but printed only when the reader asks to see the underlying material.

## Reader model (SKILL.md §7)

Write for someone who owns shares and follows the news, not an analyst. Every financial term is glossed in six words or fewer on first use, or avoided entirely.

## Non-negotiable requirements

- **Resolve identity before anything else.** Run the company-resolution step and
  do not begin analysis on an ambiguous name. "Volvo" is AB Volvo *or* Volvo Car
  AB — two listed companies, two sets of filings. If confidence is low, stop and
  ask.
- Route the company (Swedish regulated market, Swedish MTF, other Nordic, French, German) and use the matching source chain. Read
  `references/source-registry.md` for which source is authoritative per data
  type — do not decide that ad hoc.
- Fetch the price fresh and print its as-of timestamp.
- Write `DATA NOT AVAILABLE` for anything you cannot source. Do not estimate
  around a gap without saying you are doing so.
- Include the trigger table as `Bevakning` in the closing block — the single
  home for every invalidation condition, in both directions (SKILL.md §9). Its
  rows are also what the thesis ledger stores as breakers, so every threshold
  must be numeric and checkable against a future filing.
- Report **Investment Score and Data Confidence separately**, and put conviction
  on the recommendation line. A strong valuation on weak evidence is
  `KÖP — LÅG ÖVERTYGELSE` (SKILL.md §13), never a strong buy. The conviction
  ceiling is computed and enforced by `decision_record.py`
  (`references/data-quality.md` §7); a record above it is refused.

## Record the decision, seed the thesis

This is the last step of the run and it is not optional. Everything the
analysis established is an outcome, and an outcome that stays in the prose is
lost — before v3.0.0 the trigger table was computed on every run and then
discarded, so the ledger stayed empty and `/portfolio`'s breaker check had
nothing to test against.

**1. Emit the decision record as JSON and render the block** — the full field
list and the closed vocabularies are in `SKILL.md` §9.

```
python scripts/decision_record.py decision.json --render
```

Read the verdict block's numbers off the validated record, never the other way
round. If the record is refused, fix the record: the arithmetic, the conviction
ceiling, the reason codes and the identity are all checked, and a refusal names
which one failed.

**2. On a BUY or SELL call at STANDARD or DEEP depth, write the thesis** — one
falsifiable sentence, with the breakers taken verbatim from the trigger table
you already printed as `Bevakning`. That table's rows are numeric and
filing-checkable because they are the ledger's input contract
(`references/bear-case-and-scoring.md` Part 1); if a row cannot be expressed as
a breaker, the row was never a trigger. Pass the fundamental rows only — the
ledger stores no prices, so a "kurs över SEK 540" row stays in the delivered
table and out of `--breaker`.

```
python scripts/thesis_ledger.py "NAME" --metric ebit_margin \
    --add "Mining aftermarket holds group EBIT margin above 15% through the cycle." \
    --breaker "ebit_margin < 15% for 2 consecutive quarters"
```

The ledger rejects a thesis with no numeric breaker and stores nothing, with
the reason — "a quality compounder" cannot be wrong, so it cannot be right
either. Run `--metrics` to see which metrics can be tested automatically and
which need a hand-read figure through `--observe`.

**3. Then store the decision.** In that order: the decision records a reference
to the thesis that stood when it was made, so a decision filed first carries no
thesis reference.

```
python scripts/thesis_ledger.py "NAME" --decide decision.json
```

`--decide` re-validates through `decision_record.py` and writes nothing if the
record is refused. Both objects live in the same per-issuer file and both are
append-only: `--decisions` reads the calls back newest first,
`--decision-latest` gives the one that currently stands, and `--supersede`
retires a call you no longer stand behind without deleting it.

**A thesis is price-free and durable. A decision is price-stamped and
superseded.** Never put a price into a thesis — `thesis_ledger.py` states the
reason plainly: *"a thesis that flips on a quote is a trade, not a thesis."*
The price, the fair-value range and the call belong to the decision, which the
next decision supersedes. The claim and its breakers belong to the thesis,
which survives every re-run.

**When to record less, and say so:**

- **HOLD, or no call at all (⚪).** Store the decision; seed no thesis. A HOLD
  is a statement about a price, and the ledger holds claims about a business.
- **TLDR and QUICK depth.** No scenarios and no scorecard ran, so the record
  carries no expected return and no Investment Score. Omit `fair_value` and
  `scenario_weights` and let the module record `DEPTH_NO_SCENARIOS`; attach
  `DEPTH_NO_SCORECARD` yourself. Do not invent either figure to fill the shape,
  and seed no thesis from a run that built no scenarios.
- **No LEI and no ISIN.** Nothing is stored, by design: a decision keyed on a
  display name cannot be matched to a later outcome. Report that the run was
  not recorded rather than storing it under a name.
- **A re-analysis of a company already in the ledger.** Run `--list` first and
  add only a thesis that is not already there. A standing thesis is re-tested
  with `--evaluate`, not replaced by a fresh copy of itself.

None of this appears in the delivered answer. It is bookkeeping the next run
depends on, and it is reported to the user in one short line at most.

## Closing line

Every run closes with exactly one trailing offer line (SKILL.md §4):

```
Vill du se underlaget — siffror, värdering, källor och Evidence-block — säg **visa underlaget**.
```

Fold any deeper-run offer into this same line if the current depth is not DEEP.

**Nothing follows the closing block.** No trailing "Sources:" line, no
bibliography, no appendix, no source list of any kind after `Slutsats`. Sources
live in the Evidence block, which is underlying material — a trailing source
list is that block leaking into the answer, and it is the single most common
way this format fails.
