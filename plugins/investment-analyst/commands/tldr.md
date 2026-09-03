---
description: The shortest honest answer on a stock — the call, the reason, the catch
argument-hint: <ticker or company name>
---

Give a TL;DR on: **$ARGUMENTS**

Use the `investment-analyst` skill at **TLDR** depth (SKILL.md §4). Target 60–90
seconds, **under 150 words** (SKILL.md §4). This is the shortest form the system offers.

## Output: sections 1 and 7 only per SKILL.md §6

1. **Verdict block** (SKILL.md §5) — the call in ten seconds
7. **Slutsats** — signal line, Viktigast per SKILL.md §9

Then **three to five plain sentences** between them, written for someone who is not an
analyst. No jargon that a non-specialist would have to look up, no tables, no
bullet lists of metrics. The verdict block carries all four lines verbatim: identity, 
call with conviction, price with as-of timestamp and fair-value range (from multiples 
against the company's own history), and scores.

The prose must cover, in this order:

- what the company does and how it is doing
- why the call is what it is
- the single biggest thing that could make it wrong
- the largest data gap, named in plain words

## Tagging and uncertainty (SKILL.md §§1, 7)

The tagging discipline governs the work but does not appear in the delivered answer. Uncertainty reaches the reader in plain words per SKILL.md §7. The Evidence block and decision record are produced but printed only on request.

## Reader model (SKILL.md §7)

Write for someone who owns shares and follows the news, not an analyst. Every financial term is glossed in six words or fewer on first use, or avoided entirely.

## Non-negotiable at this length

Brevity is where uncertainty gets quietly dropped. It must not be dropped here.

- **Conviction sits on the recommendation line**, never implied. `KÖP — LÅG ÖVERTYGELSE` is a different instruction from `KÖP` (SKILL.md §5, §13).
- **The largest data gap is named in the prose**, in plain words. A short answer that reads as complete is worse than no answer.
- **Never a point estimate for fair value.** A range, always.
- If identity is ambiguous, this command produces **nothing but the ambiguity**.
- **Data confidence below 40 caps conviction at LOW** — say so in the prose.
  The cap is enforced, not remembered: attach the `DATA_CONFIDENCE_LOW` reason
  code to the decision record and `decision_record.py` refuses a conviction
  above the ceiling (`references/data-quality.md` §7). TLDR depth carries a
  ceiling of MEDIUM on its own.
- **Emit the decision record even here** (SKILL.md §9), with no `fair_value`
  and no `scenario_weights` — the module records `DEPTH_NO_SCENARIOS` — and with
  `DEPTH_NO_SCORECARD` attached. Render the block; never type it. Seed no
  thesis: a TLDR built no scenarios and no trigger table.

## Closing line

Every run closes with exactly one trailing offer line (SKILL.md §4), with the deeper-run offer folded in since TLDR is not DEEP:

```
Vill du se underlaget — siffror, värdering, källor och Evidence-block — säg **visa underlaget**.
En snabbare QUICK-körning (~2–4 min) lägger till finansiell data; en STANDARD (~8–12 min) lägger till full analys.
```

**Nothing follows this line.** No trailing "Sources:" line, no bibliography, no
source list of any kind. Sources live in the Evidence block, which is
underlying material — a trailing source list is that block leaking into the
answer, and it is the single most common way this format fails.

Answer in the language the user wrote in. For a Swedish question, every word of
the answer is Swedish — the call, the conviction and both score names included,
per SKILL.md §13's term table. Only the depth token (`TLDR`) stays as written,
and the decision record and Evidence block, printed only on request, keep their
English fields.
