# Ranking method

Covers only the comparison basis and the ranking method used to order several
companies against each other. Loaded by `/compare` and `/screen`; `/portfolio`
does not load this file.

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
