---
description: Search the whole listed universe for the best risk/reward — out-of-favour names with high margins — or rank a candidate list, watchlist or portfolio you supply
argument-hint: [nothing to search the market, or tickers/sector/size]
---

Find the most attractive opportunities on a risk/reward basis: **$ARGUMENTS**

Use the `investment-analyst` skill with `references/ranking.md` for the
ranking method.

**Delivered length: the ranking table, plus 80 words per top candidate** (SKILL.md §4).

## Tagging and uncertainty: SKILL.md §1 and §7.1

## Reader model: SKILL.md §7

Keep prose per candidate to 80 words or less; longer, it blocks the reader.

To review holdings you already own, use `/portfolio` instead.

Process:

1. **Establish the candidate set. Discovery is the default — do not propose a
   universe from memory.** A list of names you can recall is a list of names you
   already know, which is the one thing a screen exists to get past. Three modes:

   - **No list given** (`/screen` bare, or "find me something") — run
     `scripts/screen_value.py`. It walks the whole listed universe itself:
     ~997 ISIN lines from Nasdaq Stockholm and First North, grouped into issuers
     by LEI so Investor A and B are one candidate, not two. It never asks you
     for candidates and you never supply any.
   - **Tickers given** — use exactly those and go to step 3. This is ranking a
     list, not screening a market; say so, so the user knows which they got.
   - **Sector or theme given** — run the discovery screen and filter its output
     to that sector. State the filter. Only if it returns nothing there do you
     name candidates yourself, and then you say plainly that they came from your
     own knowledge rather than from the universe, which makes them a starting
     point and not a screen result.
   - **Size asked for** ("småbolag", "small caps", "smaller companies", "inte
     de stora bolagen") — run the discovery screen with `--small-cap`, which
     bands the universe by market cap and drops the margin floors to the level
     the segment actually earns (see the preset below). Say that you applied it
     and name the band, because the answer would otherwise look like a
     whole-market screen that happened to return small names. A bare `/screen`
     is NOT this: its floors are calibrated to the whole market and its output
     is dominated by large caps, which is the right default and the wrong
     answer to "find me a small company".

   If the user referred to a portfolio or watchlist you do not have, ask for it
   rather than inventing holdings. A watchlist can be kept:
   `scripts/watchlist_store.py` stores issuers the user follows but does not
   own, with identity resolved and an ambiguous name refused. It holds no
   quantity and no cost basis, so it is a list of names to rank, never a
   portfolio to weight.

2. **First pass — `screen_value.py`.** Cheap axes first, expensive axes only on
   the survivors: per-name fundamentals cannot be pulled for 400 issuers, but on
   the 20–80 that survive the price filters they are affordable. The script runs
   universe → 3-year history → value filter → liquidity floor → size band →
   corporate actions → ESEF margins → rank, and prints how many issuers each
   stage cut and why.

   ```
   python scripts/screen_value.py [--drawdown-floor 30] [--gross-floor 40]
       [--op-floor 15] [--cap-floor SEK] [--cap-ceiling SEK]
       [--margin-mode either|both|operating] [--small-cap]
       [--history-years 3] [--limit 20] [--json]
   ```

   The value filter requires **both** a drawdown from the 3-year high **and** a
   negative 12-month return. Either alone is the wrong screen: "down from the
   high" on its own keeps names that have already turned back up, and "down over
   12m" on its own keeps names that fell last month and are still expensive. The
   profile being hunted is out of favour *and* not recovered.

   Reading its output:
   - The whole first-pass table is `ESTIMATE — screening data, unverified`.
   - **Margins are the hard axis.** They come from ESEF primary statements, and
     every margin carries the fiscal period it came from and that period's age
     in months. A margin off a 20-month-old annual is still a fact about the
     business; margin is a slow-moving property and an audited annual is the
     right instrument for it.
   - **Valuation is the soft axis, deliberately.** The script never forms a
     price/earnings ratio, because a live price over a 20-month-old earnings
     figure is exactly what `valuation_gate.py` exists to refuse. It prints the
     price and the period side by side instead. Any multiple you quote from this
     stage is `ESTIMATE` and must be re-derived in step 3 from the latest
     interim report.
   - **Every figure that survives into the shortlist must be re-derived from
     filings in the second pass.** No screening number may reach the
     recommendation unverified.
   - `NOT CLASSIFIED` is not a rejection. An MTF issuer files no ESEF and can
     never be margin-screened here; report it as unscreened, never as failed.
     An unscreened name is not the same as a rejected one.
   - `TECHNICAL` is not an opportunity. The series is back-adjusted for splits,
     so a 3-year "-70%" is not a split artefact — but dividend treatment is
     unverified, so it can still be years of ordinary dividends wearing a
     crash costume. The script routes those out; do not argue them back in.

   **Sizing filters** — `--cap-floor` and `--cap-ceiling` limit the screen by
   market cap, summed per share class (each class's shares times its own price).
   Market cap that could not be resolved is reported as unscreened on size, never
   as a rejection; a partial cap is treated as a floor and can justify cutting a
   name for being too large, never too small.

   **Margin mode** — the default `--margin-mode either` means a name clears if
   either gross margin **or** operating margin meets its floor. That default is
   permissive on purpose, but it is why a loss-making name can survive: a high
   gross margin alone carries it through. `--margin-mode both` demands both
   floors. `--margin-mode operating` ignores gross entirely and demands a
   positive operating margin at or above the floor — use it when the question is
   whether the business actually earns money, and pair it with `--small-cap`.

   **Small-cap preset** — `--small-cap` combines a size band with margin floors
   calibrated to the small-cap segment itself rather than to the whole market.
   Swedish manufacturers with 20–49 employees (a typical small-cap size) report
   an aggregate operating margin of 6.10% (SCB 2024). The standard 15% operating
   floor is roughly 2.5× that benchmark and rejects most of the segment a
   small-cap screen is meant to surface. The preset sets `--cap-ceiling`
   5,000,000,000, `--cap-floor` 300,000,000, `--gross-floor` 25, `--op-floor` 8
   and `--margin-mode operating`. Any of those passed explicitly wins over the
   preset. Use `--small-cap` when looking for value in that size band, and run a
   bare screen to search the entire listed universe instead.

   **This universe has better free inputs than most screens** — use them rather
   than aggregator data: ESEF for revenue, EBIT, equity and cash flow on
   regulated-market names; disclosed short interest and insider net activity,
   both regulator-sourced, which almost no retail screen carries; and the price
   percentile against the company's own multi-year range.

3. **Second pass — STANDARD depth** (`SKILL.md` §4) on the shortlist: valuation,
   scenarios, bear case, scorecard.

4. **Rank** on expected return per unit of downside. Present:

   | Ticker | Price | Base FV | Upside | Downside | R/R | Score | Data Conf | Conviction | Call |

5. **Conclude** with the top three in plain language, 80 words or less per name: why it is attractive,
   and the single thing most likely to break it.

## Rules

- Fetch all prices in one pass; print as-of timestamp once (SKILL.md §8 Phase 1).
- Print expected return to one decimal place; upside, downside and margin of
  safety to whole percent.
- Never present a screen output as a recommendation to buy without the
  second-pass analysis behind it.
- If the honest answer is that none of the candidates offers adequate
  risk/reward at current prices, say so. "Nothing here is attractive right now"
  is a legitimate and useful result.
- Close with the grouped signal line defined in `SKILL.md` §9. A shortlisted
  name that the second pass could not reach closes as ⚪, never as BEHÅLL.

## Closing line

Finish with the single trailing offer line (SKILL.md §4):

```
Vill du se underlaget — siffror, värdering, källor och Evidence-block — säg **visa underlaget**.
```

**Nothing follows this line.** No trailing "Sources:" line, no bibliography, no
source list of any kind. Sources live in the Evidence block, which is
underlying material — a trailing source list is that block leaking into the
answer, and it is the single most common way this format fails.
