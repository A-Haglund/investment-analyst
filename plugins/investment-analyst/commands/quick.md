---
description: Fast read on a stock — price, key metrics, valuation, top risk, call
argument-hint: <ticker or company name>
---

Give a QUICK read on: **$ARGUMENTS**

Use the `investment-analyst` skill at **QUICK** depth (see `SKILL.md` §4).
Target 2–4 minutes, **350 words maximum** (SKILL.md §4).

## Output: seven sections per SKILL.md §6, minus sections 3 and 5

1. **Verdict block** (SKILL.md §5) — the call in ten seconds
2. **What the company is** — what it owns and does now, 3–5 marked bullets
4. **Talar för / Talar emot** — evidence already established, two marked lists, 3–5 items each
6. **What the call means in practice** — the call translated into action, 2–4 sentences
7. **Slutsats** — signal line, Viktigast per SKILL.md §9

Sections 3 (Why the price is where it is) and 5 (Scenarios) are skipped at QUICK
depth, and the remaining sections carry half the STANDARD word budget set in
SKILL.md §6 — do not restate that table here.

At this depth the verdict block's second line ends at the upside range, with no expected return, and names the fair-value basis (multiples against the company's own history); the third line reads `Investeringsbetyg saknas — inget scorecard på denna nivå` beside `Datasäkerhet`, per SKILL.md §5.

## Tagging and uncertainty (SKILL.md §§1, 7)

The tagging discipline governs the work but does not appear in the delivered answer. Uncertainty reaches the reader in plain words per SKILL.md §7. The Evidence block and decision record are produced but printed only on request.

## Reader model (SKILL.md §7)

Write for someone who owns shares and follows the news, not an analyst. Every financial term is glossed in six words or fewer on first use, or avoided entirely.

## Non-negotiable requirements

- Identity resolution is **not** skippable, even at QUICK depth. Analysing the wrong legal entity fast is worse than analysing the right one slowly.
- Fetch the price fresh and print its as-of timestamp
- `DATA NOT AVAILABLE` rather than a guessed number
- Data Confidence sits on the scores line. QUICK depth carries a conviction
  ceiling of MEDIUM, computed and enforced by `decision_record.py` — a record
  above it is refused (`references/data-quality.md` §7)
- Text output only, no artifact

## Record the decision (SKILL.md §9)

Emit the decision record as JSON and render the block from it; never type the
block. A QUICK record runs no scenarios and no scorecard, so omit `fair_value`
and `scenario_weights` — the module records `DEPTH_NO_SCENARIOS` and leaves
expected return empty — and attach `DEPTH_NO_SCORECARD` yourself. Store it with
`thesis_ledger.py "NAME" --decide decision.json`. **Seed no thesis from a QUICK
run:** it built no scenarios, and a thesis is a claim to be re-tested rather
than a first impression. `commands/analyze.md` carries the full sequence.

## Closing line

Every run closes with exactly one trailing offer line (SKILL.md §4), with the deeper-run offer folded in since QUICK is not DEEP:

```
Vill du se underlaget — siffror, värdering, källor och Evidence-block — säg **visa underlaget**.
En djupare körning (STANDARD, ~8–12 min) lägger till full moat-analys, kassaflödesvärdering och investeringspoäng.
```

**Nothing follows this line.** No trailing "Sources:" line, no bibliography, no
source list of any kind. Sources live in the Evidence block, which is
underlying material — a trailing source list is that block leaking into the
answer, and it is the single most common way this format fails.
