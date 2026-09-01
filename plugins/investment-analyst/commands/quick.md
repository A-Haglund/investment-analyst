---
description: Fast read on a stock — price, key metrics, valuation, top risk, call
argument-hint: <ticker or company name>
---

Give a QUICK read on: **$ARGUMENTS**

Use the `investment-analyst` skill at **QUICK** depth (see `SKILL.md` §4).
Target 2–4 minutes.

Open with the **verdict block** (SKILL.md §5) — at QUICK depth that block is
most of the answer. At this depth the second line ends at the upside range,
with no expected return, and names the fair-value basis (multiples against
the company's own history); the third line reads `Investment Score n/a — no
scorecard at this depth` beside Data Confidence. Then include only:

1. Price with as-of timestamp, market cap, net debt
2. Headline fundamentals: revenue growth, EBIT margin, FCF, net debt/EBITDA —
   latest year plus the trend
3. Valuation: P/E, EV/EBIT, FCF yield, against the company's own history
4. The single biggest risk, in two sentences
5. For a Swedish company: disclosed short interest and recent insider net
   activity — both are one call each and change the risk picture materially
6. There is no decision record at QUICK depth — the verdict block **is** the
   record. Conviction is capped at **MEDIUM**, and Data Confidence sits on the
   scores line, where the Investment Score reads `n/a — no scorecard at this
   depth`

Skip moat scoring, growth decomposition, management analysis, scenarios, the
full scorecard, the Evidence block and any DCF. Say in one line which
sections were skipped.

Identity resolution is **not** skippable, even at QUICK depth. Analysing the
wrong legal entity fast is worse than analysing the right one slowly.

Rules that still apply in full:

- Fetch the price fresh and print its timestamp
- Label FACT / ESTIMATE / ASSUMPTION / OPINION
- `DATA NOT AVAILABLE` rather than a guessed number
- Text output only, no artifact

Close with the signal line defined in `SKILL.md` §9 — the colour flag, the
call, one plain sentence, and `Viktigast`. Then one line offering the deeper
run and what it would add.
