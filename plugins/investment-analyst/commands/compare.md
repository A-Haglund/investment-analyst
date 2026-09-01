---
description: Compare several companies on the same model and rank them by risk/reward
argument-hint: <ticker> <ticker> <ticker> ...
---

Compare these companies head to head: **$ARGUMENTS**

Use the `investment-analyst` skill at **COMPARE** depth (see `SKILL.md` §4),
and read `references/portfolio.md` for the ranking method.

Requirements:

- Default to **COMPARE depth per company** — QUICK plus the Moat Score plus a
  light bear/base/bull, so downside and risk/reward have a real basis. Roughly
  4–6 minutes per company; comparing five names at DEEP depth is over two
  hours. Go deeper only on the winner, and only if the user asks.
- COMPARE runs no DCF, no reverse DCF, no peer set and no nine-category
  scorecard — and therefore produces **no Investment Score**. Conviction caps
  at **MEDIUM** for every name in the run, the same cap QUICK carries.
- Analyse every company **to the same depth**. An uneven comparison ranks effort,
  not opportunity. If you must go shallower to cover them all, go shallower on
  all of them equally and say what depth you used.
- Fetch all prices in the same pass so the comparison is as of one moment.
  Print that timestamp once.
- Produce a side-by-side table covering: revenue growth, EBIT margin, FCF margin,
  ROIC, net debt/EBITDA, EV/EBIT, FCF yield. No Investment Score — COMPARE runs
  no scorecard.
- Then the ranking table: Ticker | Price | Moat | Base FV | Upside | Downside |
  R/R | Data Conf | Conviction | Call. The Moat Score, base fair value, upside,
  downside and Data Confidence live here and are not repeated in the side-by-side
  table — that table carries the operating metrics, this one carries the verdict.
  Data Confidence is not optional: COMPARE includes everything QUICK produces,
  and QUICK states it.
- Rank on expected return per unit of downside — COMPARE's light scenarios give
  this a real basis, per `references/portfolio.md`.
- Print expected return to one decimal place; upside, downside and margin of
  safety to whole percent.
- The general rule: a column that no depth in the run produces is dropped,
  never estimated. Inventing an Investment Score, a DCF-based fair value or a
  peer multiple that COMPARE never computed is the exact failure this system
  exists to prevent.
- State which name is most attractive, why, and what would have to be true for
  the runner-up to overtake it.
- Text output only. Build no artifact unless the user asks for one.
- Note explicitly where the companies are not truly comparable (different
  business models, cycle positions or accounting bases).
- Close with the grouped signal line defined in `SKILL.md` §9, one line for
  the whole comparison, then `Viktigast` for the run as a whole.
