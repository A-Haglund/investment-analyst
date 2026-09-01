---
description: Find the most attractive names by risk/reward from a candidate list, watchlist or portfolio
argument-hint: [tickers or sector]
---

Find the most attractive opportunities on a risk/reward basis: **$ARGUMENTS**

Use the `investment-analyst` skill with `references/portfolio.md`.

To review holdings you already own, use `/portfolio` instead.

Process:

1. **Establish the candidate set.** If tickers were given, use them. If a sector
   or theme was given, propose a candidate list and state your selection
   criteria before analysing — the screen is only as good as its universe. If the
   user referred to a portfolio or watchlist you do not have, ask for it rather
   than inventing holdings.

2. **First pass — cheap filters.** For every candidate pull price, market cap,
   EV/EBIT, FCF yield, revenue growth and net debt/EBITDA. Rank and shortlist.
   Show the full first-pass table so the cut is visible. This pass runs no
   `SKILL.md` §4 depth — it is screening only, which is why every figure below
   is marked `ESTIMATE`.

   The first pass is the one place lower-tier data is permitted, because
   full filing pulls for 15 names to make a cut is not a sensible use of
   effort. Rules for it:
   - Mark the whole first-pass table `ESTIMATE — screening data, unverified`.
   - `sec_fundamentals.py` and `esef_fundamentals.py` are still the fastest
     route for a handful of names — use them where the list is short.
   - **Every figure that survives into the shortlist must be re-derived from
     filings in the second pass.** No screening number may reach the
     recommendation unverified.
   - If a candidate's screening data cannot be obtained at all, keep it in the
     list marked `DATA NOT AVAILABLE` rather than dropping it silently — an
     unscreened name is not the same as a rejected one.

   **For a Swedish universe the first pass has better free inputs than most
   screens.** Use them rather than generic aggregator data:
   - the Nasdaq Stockholm universe (743 listed lines with ISIN, ICB sector,
     segment and last price) as the candidate set
   - ESEF for revenue, EBIT, equity and cash flow on regulated-market names
   - disclosed short interest and insider net activity as free, regulator-sourced
     screening dimensions almost no retail screen carries
   - price percentile against the company's own 10-year range

3. **Second pass — STANDARD depth** (`SKILL.md` §4) on the shortlist: valuation,
   scenarios, bear case, scorecard.

4. **Rank** on expected return per unit of downside. Present:

   | Ticker | Price | Base FV | Upside | Downside | R/R | Score | Data Conf | Conviction | Call |

5. **Conclude** with the top three, each in two sentences: why it is attractive,
   and the single thing most likely to break it.

Rules:

- Fetch all prices in one pass; print the as-of timestamp once.
- Print expected return to one decimal place; upside, downside and margin of
  safety to whole percent.
- Never present a screen output as a recommendation to buy without the
  second-pass analysis behind it.
- If the honest answer is that none of the candidates offers adequate
  risk/reward at current prices, say so. "Nothing here is attractive right now"
  is a legitimate and useful result.
- Close with the grouped signal line defined in `SKILL.md` §9. A shortlisted
  name that the second pass could not reach closes as ⚪, never as HOLD.
