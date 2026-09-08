# Output contract — compact rules skeleton

Skim first, every session — calibrates tagging density and output shape.
Skeleton with placeholders, not a filled example; full worked case (human
reference, not loaded at runtime): `tests/fixtures/worked-example.md`. Every
value has one home — where this file only points, the pointer is the rule.

**Tagging density.** One tag (`FACT`/`ESTIMATE`/`ASSUMPTION`/`OPINION`) per
material claim — not per sentence, not per paragraph. A number entering the
model gets a tag; narrative connective tissue does not. Tags never reach the
answer (SKILL.md §7); they surface only in the Evidence block and the
decision record.

## 1. Verdict block — every depth, first thing printed

```
OMDÖME — <Company> (<TICKER>, <venue>) · <DEPTH> · <YYYY-MM-DD>

  <KÖP/BEHÅLL/SÄLJ etc> — <ÖVERTYGELSE>
  <CCY X> nu -> rimligt värde <low-high> (bas) · <down%> till <up%> · förv. avk. <±%>
  Investeringsbetyg <N>/100 · Datasäkerhet <N>/100
```
+ 5 labelled plain lines, no tags: **Varför / Risk / Inprisat / Bevaka /
Overifierat** (English: Why/Risk/Priced in/Watch/Unverified — never mixed).

At QUICK/COMPARE/TLDR (no scorecard): line 3 names the gap instead of a score
— `Investeringsbetyg saknas — inget scorecard på denna nivå`. Full rules →
SKILL.md §5; translation table → §13; ceiling values → `references/conviction.md`.

## 2. Seven-section answer, word caps, chart forms

Order, section names and per-section word budgets → `references/answer-structure.md`; per-depth caps → SKILL.md §4
and §4 tables. **Not restated here** — one home only. Chart forms (sparkline,
P/E range, scenario ladder, scorecard bar — no others), 88-char/40-cell
limits → `references/answer-structure.md`, "Charts".

## 3. Talar för / Talar emot — the mark scale

The only place this scale is defined; every marked list uses it. The mark
grades the **item**, not the section heading — a real-but-single-sourced
point under "för" gets 🟡, not a bare 🟢:

| Mark | Meaning |
|---|---|
| 🟢 | Strong and established |
| 🟡 | Real but qualified, single-sourced, or unproven |
| 🟠 | A concern short of a threat |
| 🔴 | Material |

## 4. Naming a data gap in the answer

Tag → plain-clause conversions (`SINGLE SOURCE`, `ESTIMATE`, `ASSUMPTION`,
`DATA NOT AVAILABLE`, `CONFLICT`) → SKILL.md §7 rule 2. **Addendum, not
stated there:** when a gap biases a *derived* figure in a known direction,
recharacterize the figure as a bound, not just flag the gap: "<figure> is
likely too {high/low} by an unknown amount, because <cause> — treat as an
{upper/lower} bound, not a result."

## 5. Scenarios and section 6 vs Slutsats

One table (bear/base/bull, ≤4 cols) + the range-marker chart, nothing else →
`references/answer-structure.md`, "Charts". Weighted value reconciles to the verdict block's
expected return to the decimal — arithmetic enforced by `decision_record.py`,
never hand-checked → SKILL.md §9.

Section 6 (**Vad rekommendationen betyder i praktiken**) explains what the
call *means*; `Äger du den redan` in `Slutsats` says what to *do*. The two
must not converge → `references/answer-structure.md`.

## 6. Closing block (Slutsats)

Short form (no full close) vs full form (`Bevakning` table, `Äger du den
redan`, `Horisont`, `Viktigast`) and when each applies → SKILL.md §9 "The
signal line". Skeleton of the full form:

```
🟢/🟠/🔴 **<TICKER> — <VERDICT>** · <CONVICTION>
<one-line why>

**Bevakning** | # | Utlösare | Tröskel | Följd |
**Äger du den redan:** <instruction>
**Horisont:** <date + what resolves then>
Viktigast: Datasäkerhet <N>/100. <biggest gap>. Detta är en riktning, inte ett facit.
```

**Nothing follows this block.** No source list, no bibliography. Exactly
**one** trailing offer line (underlying material + next depth up, folded into
one) → SKILL.md §4 end, and `references/answer-structure.md` "underlying material".

## 7. Underlying material — printed only on "visa underlaget"

- **Evidence block** (groups, `VERIFIED`/`CROSS-CHECKED`/`SINGLE SOURCE`/
  `CONFLICT`/`STALE`/`DATA NOT AVAILABLE`, the mandatory `TALLY` line) → its
  one home is `references/verification.md` "Reporting it".
- **Decision record** — JSON field list, closed vocabularies, what gets
  refused → SKILL.md §9 "The flow". Rendered `DECISION —` block shape → same
  section, "The block". Never hand-type either.
- **Checksum rule:** every number in the verdict block and the closing block
  is read off the validated decision record, never retyped or recomputed
  independently → SKILL.md §9. A mismatch is a defect, not a second opinion.
- The decision record is the one fixed-shape block that stays **English**
  even in a Swedish answer → SKILL.md §13.

