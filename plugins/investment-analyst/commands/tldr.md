---
description: The shortest honest answer on a stock — the call, the reason, the catch
argument-hint: <ticker or company name>
---

Give a TL;DR on: **$ARGUMENTS**

Use the `investment-analyst` skill at **TLDR** depth (SKILL.md §4). Target 60–90
seconds and under 150 words. This is the shortest form the system offers.

## What to produce

1. The **full fenced header of the verdict block** (SKILL.md §5), verbatim, all
   four lines:
   - the identity line — `VERDICT — <company> (<ticker>, <venue>) · <date>`
   - the call with conviction
   - the price, with its as-of timestamp, and the fair-value range — at this
     depth the range comes from multiples against the company's own history,
     the basis is named on the line, and the line ends at the upside range
     with no expected return
   - the scores: `Data Confidence NN/100 · Investment Score n/a — no
     scorecard at this depth`

   Nothing is trimmed away. TLDR carries the header exactly as SKILL.md §5
   defines it, price timestamp included — it is what comes after the header
   that is short.
2. Then **three to five plain sentences**, written for someone who is not an
   analyst. No jargon that a non-specialist would have to look up, no tables, no
   bullet lists of metrics.

The sentences must cover, in this order:

- what the company does and how it is doing
- why the call is what it is
- the single biggest thing that could make it wrong
- what would have to happen for you to change your mind

## Non-negotiable at this length

Brevity is where uncertainty gets quietly dropped. It must not be dropped here.

- **Conviction sits on the recommendation line**, never implied. `BUY — LOW
  CONVICTION` is a different instruction from `BUY`.
- **The largest data gap is named in the prose**, in plain words. If EV/EBIT
  could not be computed, if the latest annual figures are two years old, if the
  company files no ESEF at all — say it in a sentence a non-specialist
  understands. A short answer that reads as complete is worse than no answer.
- **Never a point estimate for fair value.** A range, always.
- If identity is ambiguous, this command produces **nothing but the ambiguity**.
  "Volvo" is two listed companies; resolving it wrongly at speed is the failure
  this system exists to prevent.
- Data confidence below 40 is **VERY LOW** on the conviction ladder in
  `references/data-quality.md` §7, not LOW — cap the conviction there and say
  so in the prose. Do not let the short form sound more certain than the
  evidence.

## What to skip

Scorecard, scenarios, DCF, peer set, Evidence block, sources. Close with one
line naming what a fuller run would add and roughly how long it takes.

Close with the signal line defined in `SKILL.md` §9. At this depth it is the
whole close: the colour flag, the call, one plain sentence, and `Viktigast`.

Answer in the language the user wrote in, keeping the standard terms
(`BUY`/`HOLD`/`SELL`, conviction, data confidence) in English.
