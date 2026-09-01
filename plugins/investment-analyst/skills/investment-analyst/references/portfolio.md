# Portfolio and risk analysis

Used when the user supplies holdings, reviews what they own, or asks which of
several names is most attractive. Ask for the data you need rather than
assuming: ticker, share count or weight, and average cost if position-level
P&L matters.

## Portfolio review — the three-layer triage

A portfolio review asks what to do with holdings you already own. It runs in
three layers: breakers, alerts, and depth analysis on what they flag.

### Layer 1: Breakers

Test each holding against its stored thesis using `thesis_ledger.py`. A fired
breaker (thesis condition invalidated) short-circuits the decision to EXIT
regardless of current price or valuation. This is the only layer that produces
a final decision on its own.

### Layer 2: Alerts

One cheap question per holding. Flag for deeper review if any is true:
- Price outside the last recorded range
- A new report or disclosure since the last review
- Short interest has moved materially
- Net insider selling (classified)
- `valuation_gate` calling the price/earnings period incompatible (inputs stale)
- No stored view at all (first time holding is seen)

Alerts trigger deeper review but do not themselves decide the action.

### Layer 3: Depth

STANDARD depth (`SKILL.md` §4) on holdings flagged by layer 1 or 2. A clean
holding gets `HOLD — nothing has changed` **with the date it was last
reviewed**. That date is what makes the answer honest rather than a skipped
step. Conviction for holdings not taken to depth is capped at **MEDIUM**.

The three-layer approach means the cost scales with what changed rather than
with the number of holdings. A 20-position portfolio where 3 are flagged costs
the same effort as a careful review of those 3 names.

## ADD / HOLD / TRIM / EXIT criteria

| Action | Means |
|---|---|
| **ADD** | Conviction is BUY or at least MEDIUM, and position weight is below target for that conviction and the time horizon. Clear case to increase exposure. |
| **HOLD** | Conviction is HOLD or thesis is intact (MEDIUM or better), and position is appropriately sized. Nothing has changed materially since the last review. Date the review. |
| **TRIM** | Conviction is HOLD but valuation has moved above range, or concentration has drifted above the target weight, or a fresh analysis shows lower conviction than the earlier thesis. Reduce without exiting. |
| **EXIT** | Conviction has fallen to SELL, or thesis has breached (layer 1 breaker), or valuation has moved into the tail of downside risk (bear case materially worse), or position has become illiquid. Close the position. |

### Entry price — the cost-basis rule

Entry price is optional and is stored, but it **never** enters the ADD/HOLD/TRIM/EXIT
decision and **never** appears in the same table row as an action. Anchoring on
what you paid is the disposition effect: selling winners and holding losers when
the right decision is the opposite. Unrealised result is printed in a separate
block **after** the actions are decided.

Entry price is legitimate for two purposes:
- **Dating the thesis.** Roughly when was the view formed? A thesis from
  2024 tested against 2026 data carries different evidence than a three-month
  view.
- **Calibrating the process.** Comparing actual entries against later fair-value
  ranges is how you learn whether you are buying value or chasing.

Placing a −34% loss beside an action will surface in the reasoning as "despite
the decline" rather than as part of the decision. Process quality requires
that anchoring bias be visible and then rejected, not baked into the verdict.

## Holding valuation fields

Three optional fields — `fair_value_low`, `fair_value_high` and `bear_value` —
define the valuation envelope for a holding. All are optional and nullable, all
are expressed in the holding's quote currency. The price-range alert reads
`fair_value_low` and `fair_value_high` to flag when current price strays outside
the last recorded range. The portfolio downside calculation reads `bear_value`
from each holding to compute the portfolio-level drawdown.

Set them with `portfolio_store.py --fair-value LO-HI` and `--bear N`, or with
`--fair-value-for NAME` and `--bear-for NAME` on an existing holding. Decimals
use the separator native to your system (`.` or `,`).

**Why they are fields, not prose.** Valuation inputs began as note tags
(`[FV:600-700]`, `[BEAR:400]`) scraped with regex. That approach produced a
10× error rate on Swedish-written decimals and fabricated a reassuring 95%
drawdown ratio whenever the bear tag was absent. A decision input belongs in
a typed field with validation, never embedded in free text.

**When they are absent.** The price-range alert reports itself as not checked,
and the portfolio downside figure reads `DATA NOT AVAILABLE` rather than
computing a ratio. A missing bear case means no downside figure — not a falsely
reassuring one. This is honest: you do not have a view on the worst case yet.

## Portfolio drivers and overlap detection

The `sector=` and `driver=` tags in a holding's note field tell
`portfolio_metrics.py` how to group for hidden overlap. The portfolio's **true
maximum exposure** is the largest weight across both the sector grouping and
the driver grouping — it is the answer to "if this single theme broke, what
would I lose?" Set them as `sector=Consumer, driver=iGaming regulation` or
similar; any holding can have both, either, or neither.

## ISK-specific: schablon rate and allowance

The ISK carries two tax facts that are observations, not calculations:

1. Cash idle in the account is taxed as if it were invested at the schablon
   rate. Idle cash has a real cost.
2. Dividend withholding on non-Swedish payers is creditable only up to the
   schablon allowance. A portfolio heavy in US dividend payers can lose credit.

`portfolio_store.py` observes both when run with `--schablon-rate RATE` (the
annual percentage, e.g. 2.25) and `--schablon-allowance AMOUNT` (in SEK, e.g.
100000). The portfolio's own rule forbids a remembered rate — tell the script
each time with these flags. Without them, the output names Skatteverket and
Riksgälden as where to get the current figures and reports the observation as
not supplied.

## Share class as position identity

In a portfolio, the share class **is** the position, not a detail. `Investor A`
and `Investor B` of Dividendaktier AB trade at different prices and carry
different dividend rights; they are separate holdings with separate decision
records. Where a typed class is not one of the issuer's listed lines of shares,
the row is refused and not stored.

## Position sizing

Assess each holding against:

- **Weight versus conviction.** The largest position should be the highest
  conviction, not the one that has run the most. Flag any position that grew
  into its size rather than being sized deliberately.
- **Weight versus downside.** Size on bear-case loss, not on volatility.
  A position where the bear case is −60% carries twice the portfolio risk of one
  at −30% at the same weight.
- **Kelly sanity check.** Full Kelly is far too aggressive for equities; use it
  only as an upper bound and note that a quarter-Kelly is the practical version.
- **Liquidity.** For Swedish small caps in particular, compare position size
  against average daily volume. A position taking more than a few days to exit
  is illiquid regardless of its market cap.

## Concentration risk

```
Top 1 / top 3 / top 5 weights
Herfindahl index = Σ wᵢ²   (0.10 ≈ 10 equal positions; above 0.25 is concentrated)
Effective number of positions = 1 / Σ wᵢ²
```

Concentration is not automatically bad — it is bad when unintentional or
unrewarded. Distinguish deliberate concentration in high-conviction names from
drift.

## Exposure analysis

Run each of these and show the weights:

| Dimension | Buckets |
|---|---|
| **Sector** | GICS sectors; flag anything above ~25% |
| **Geography** | By revenue exposure, not listing venue — a Nasdaq Stockholm industrial may earn most of its revenue in USD |
| **Currency** | The real FX exposure, which follows revenue and costs |
| **Factor** | Growth / value / quality / momentum / size tilts |
| **Valuation** | Weighted P/E, EV/EBIT, FCF yield versus the market |
| **Business model** | Cyclical vs defensive, capital-light vs heavy, subscription vs transactional |

The listing-venue error matters for Swedish portfolios: a portfolio of Nasdaq
Stockholm names can be overwhelmingly exposed to European industrial capex and
the US dollar. Report exposure by underlying revenue.

## Correlation and hidden overlap

Look past sector labels for shared drivers:

- Same end market (e.g. semiconductor capex, housing starts, iGaming regulation)
- Same customer concentration
- Same input cost or commodity
- Same interest-rate sensitivity
- Same regulatory regime

Two names in different sectors that both depend on data-centre buildout are one
bet, not two. Note pairwise correlation where you have return data, but explain
the *mechanism* — correlation without a reason is unstable.

## Downside risk

```
Portfolio bear-case value ratio = Σ (weight_i x bear_value_i / current_price_i)
Portfolio bear-case drawdown = 1 − that ratio
```

Report:
- Portfolio-level bear-case drawdown
- The three positions contributing most of that drawdown
- Whether any single position could impair the portfolio by more than ~10%
- Historical maximum drawdown of comparable exposure, as a reality check

## Comparative ranking — "which is most attractive right now?"

Rank only companies analysed to the same depth on the same model. Mixing a
thorough analysis with a superficial one produces a ranking that reflects effort,
not opportunity.

Rank primarily on **expected return per unit of downside**:

```
R/R = expected_return / |downside|
```

Then present a table:

| Ticker | Price | Base FV | Upside | Downside | R/R | Score /100 | Conviction | Call |
|---|---|---|---|---|---|---|---|---|

Adjust the ranking for:
- **Conviction** — a lower expected return with high conviction often beats the
  reverse
- **Portfolio fit** — the best standalone idea may be the worst addition if it
  doubles an existing exposure
- **Catalyst timing** — value that takes five years to realise is worth less than
  the same value in one

State the recommendation as: **the most attractive name, why, and what would
have to be true for the runner-up to overtake it.**

Never rank on Investment Score alone. The score measures the company; the
opportunity is the gap between price and value.

## What this analysis does not do

It does not account for the user's tax position, time horizon, income needs,
existing outside exposure (employer stock, property, pension) or risk tolerance.
Say so when giving portfolio-level conclusions, and ask if any of it would change
the answer materially.
