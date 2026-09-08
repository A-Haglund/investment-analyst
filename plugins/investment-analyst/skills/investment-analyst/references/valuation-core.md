# Valuation

Every valuation output must expose the assumptions that drive it. A fair value
without visible inputs is a guess wearing a suit.

## Setup

```
Market cap = Σ over share classes ( shares_outstanding_class × price_class )
  shares_outstanding = the class's registered shares LESS treasury shares
  an unlisted class is valued at its listed sibling's price, stated as an ASSUMPTION

Enterprise value  = market cap + total debt (incl. lease liabilities where
                    capitalised — see the lease-consistency requirement below
                    and `references/fundamentals.md`; a K3 filer has no
                    IFRS 16 liability to include, see `references/sweden.md`)
                    + minorities + pension deficit + preferred stock
                    - cash - short-term investments - non-operating assets
```

No free source publishes a point-in-time treasury-share balance for most
issuers — ESEF tags only the cash-flow movement (`PurchaseOfTreasuryShares`),
never the balance held (`scripts/share_semantics.py`). Where the treasury
count cannot be sourced, use registered shares in place of shares
outstanding, label the resulting market cap
`ASSUMPTION — treasury holdings unknown, assumed immaterial`, and state the
direction of the error: market cap and EV are both **overstated**, because
registered shares are always at least as large as the true outstanding count.

Diluted shares are for the per-share DCF bridge, never for market cap: a
multi-class issuer trades each class at its own price, so 100m A shares at
SEK 210 plus 300m B shares at SEK 200 is SEK 81.0bn (21.0bn + 60.0bn) — not
400m x 200 = SEK 80.0bn from a single blended count.

The EV→equity bridge in the DCF section below unwinds this same identity in
the opposite direction: it builds equity value from a DCF-derived EV by
subtracting what is added here and adding what is subtracted here. Where
preference shares or D-shares are listed, they already sit inside "market cap
across all classes" — never subtract them again as preferred stock in the
bridge, or they are counted twice. This file's convention is: market cap
always means the sum across all classes at each class's own price; "preferred
stock" in the bridge refers only to *unlisted* preferred instruments not
already captured in market cap.

Use diluted shares from the latest filing cover page for the per-share bridge.
Where SBC is material, add expected future dilution rather than using today's
count as if it were static.

## Financials and real estate

Only the **EV and net-debt machinery** stops applying to banks, insurers and
real-estate companies — not the Setup section as a whole. The **market-cap
definition above applies to every issuer without exception**: each share
class priced at its own price, treasury excluded, unlisted classes valued
as an assumption. Property companies and banks are not excluded from that
rule — several are precisely the multi-class cases it exists for. Sagax and
Corem, both property companies, are the canonical Stockholm example
(`references/source-registry.md`): Sagax has 80m A shares at SEK 300, 224m B
shares at SEK 290 and 236m D shares at SEK 27, giving 24.00 + 64.96 + 6.37 =
SEK 95.33bn priced correctly by class. Blending all 540m shares at the
ordinary (B) price instead gives SEK 156.60bn — a 64% overstatement, which
would run straight into the NAV premium or discount this section prescribes
below.

What does not apply to banks, insurers or real-estate companies — SEB-A.ST,
SHB-A.ST and SWED-A.ST are covered names, and real estate is a large slice
of the Stockholm list — is EV, net debt, EBITDA, FCFF and the EV→equity
bridge: a bank's debt is its raw material and its cash is its inventory.
Use the sector's own framework instead, built on the same correctly-priced
market cap:

- **Banks** — P/TBV against ROTE, CET1 headroom, dividend capacity.
- **Insurers** — P/B against ROE, solvency ratio.
- **Real estate** — discount or premium to EPRA NTA, interest-coverage
  ratio, loan-to-value.

Every input above is disclosed free in the companies' own quarterly reports,
which matters because this plugin is bound to free, keyless sources.

## Multiples

Compute all of these:

| Multiple | Formula |
|---|---|
| P/E | price / diluted EPS (trailing) |
| Forward P/E | price / next-12-month EPS — see the sourcing rule below |
| EV/Sales | EV / revenue |
| EV/EBITDA | EV / (EBIT + D&A) |
| EV/EBIT | EV / operating income |
| Price/FCF | market cap / FCF |
| FCF yield | FCF / market cap |

Prefer EV-based multiples for cross-company comparison — they are neutral to
capital structure. P/E on a heavily indebted company is not comparable to P/E on
a net-cash one.

### Compare against four things

1. **Own history** — 5- and 10-year ranges. Where does today sit in the
   distribution? A stock at the top decile of its own history needs a reason.
2. **Peers** — a genuinely comparable set. State the selection criteria. Three
   close peers beat ten loose ones.
3. **Growth** — a 25x multiple on 30% growth is not the same as 25x on 5%.
4. **Returns and margins** — high-ROIC businesses deserve higher multiples;
   quantify roughly how much.

Never conclude "cheap versus history" without asking whether the business is the
same business it was. Multiple compression is often correct.

### Sourcing rule for forward and peer multiples

Consensus estimates and pre-computed peer multiples come from licensed
databases the user may not have. Without them, do this rather than stalling or
inventing a number:

- **Forward P/E** — use your own base-case next-year EPS and label it
  `ASSUMPTION`, stating that it is your estimate and not consensus. Where
  company guidance exists, anchor on it and label it `ESTIMATE` with the
  guidance date. Never present either as consensus.
- **Consensus** — if the user asks specifically what the market expects, and no
  licensed source is available, write `DATA NOT AVAILABLE` for the consensus
  figure and substitute the **reverse DCF**, which derives implied expectations
  from the price itself. That is often the better answer anyway.
- **Peer multiples** — compute them yourself from the peers' own filings and
  live prices. Cap the set at three to five genuinely comparable names; a
  smaller set you actually computed beats a larger one you guessed. State the
  date and that the multiples are your own calculation.
- **Historical multiple ranges** — for Nordic issuers, `nordic_shares.py "NAME"
  --history 10` returns ten years of daily closes from the exchange itself,
  with the current price's percentile in that distribution. Pair each close
  with the share count and earnings of the time.

  The series is **back-adjusted for splits**. That is measured, not assumed:
  four dated splits in both directions — Mycronic 2:1, Investor A/B 4:1,
  Bambuser 1:30 reverse, Nobia 1:10 reverse — show no price discontinuity at
  the effective date. So a return, a drawdown-from-high or a
  percentile-of-own-history computed straight off these closes is already
  correct across a split, and **no manual split adjustment goes on top of it**:
  `corporate_actions.split_adjustment_factor()` is for a per-share fundamental
  (EPS, dividend per share, book value per share), which comes from a filing
  and is never itself restated for a split. Applying it to a price
  double-adjusts it.

  Two things the series does not fix. **Dividends are unverified either way**
  and treated as unadjusted, so a comparison spanning an ex-dividend date is
  likely wrong by the dividend and nothing here corrects it — use these closes
  for a price or a price-based multiple range, never for a total return.
  **Rights issues need a TERP adjustment** and are common on the venues this
  plugin covers. Where the full ten-year window is not available from a
  compliant source, give the shorter one and say so rather than filling the
  gap from an unofficial endpoint.

The rule is unchanged: an unsourceable number is `DATA NOT AVAILABLE`, never a
plausible-looking invention.

## Reverse DCF — the most useful output when no consensus exists

Instead of producing a value, solve for what today's price already assumes.

> **What future performance is today's share price requiring?**

### There is no single implied future

This is the mistake to avoid. Price is one number; the inputs are many. Any
combination of growth, margin and terminal assumption that produces the same
present value is equally consistent with the price. Solving for one variable
while freezing the others produces a precise answer to a question nobody asked.

So solve for **combinations and report a surface**, not a point:

| Terminal EBIT margin | Implied 10y revenue CAGR |
|---|---|
| 15% | 14% |
| 18% | 10% |
| 21% | 7% |
| 24% | 5% |

Then place the company on it. If it has delivered 8% growth at a 19% margin for
a decade, the row that matches its own history tells you whether the price is
demanding more than the business has ever produced.

### What to report

```
                        MARKET-IMPLIED    ACTUAL (TTM)    HISTORICAL (5y)
Revenue CAGR                   ~10%             6.4%              8.1%
EBIT margin                     18%            17.2%             16.4%
FCF margin                      12%            11.0%             10.2%
Terminal growth              2.5% (assumption, not solved)
```

Then a verdict in one sentence: the price requires **optimistic**,
**approximately fair**, or **pessimistic** performance relative to the
company's own record. Not relative to your hopes for it.

### Never call this consensus

A reverse DCF is a `MARKET-IMPLIED EXPECTATION`. Analyst consensus is a
different object that this system cannot obtain. Where the user asks what the
market expects and no licensed source exists, write
`CONSENSUS DATA NOT AVAILABLE` and give the reverse DCF explicitly labelled as
the substitute. The two must never be conflated in wording or in a table
heading.

## Scenarios

Build three complete, internally consistent cases. Each must specify all six
rows — a scenario that only changes the multiple is not a scenario.

| | Bear | Base | Bull |
|---|---|---|---|
| Revenue (yr 5) | | | |
| Revenue CAGR | | | |
| EBIT margin | | | |
| FCF | | | |
| Exit multiple | | | |
| **Fair value** | | | |

Consistency rule: a bear case with collapsing revenue **and** an unchanged
multiple is incoherent. Multiples compress when growth disappoints. Make the
scenarios hang together economically.

Assign probabilities that sum to 1.0 and justify them. Base case should not
automatically be 60% — if the outcome is genuinely bimodal, say so.

## Expected value and risk/reward

```
expected_value    = Σ (probability_i x fair_value_i)
Expected return   = expected_value / current_price - 1
Upside            = bull_value / current_price - 1
Downside          = bear_value / current_price - 1
Margin of safety  = 1 - current_price / base_value
Risk/reward       = upside / |downside|
```

`EV` means enterprise value everywhere else in this file — use
`expected_value` here to avoid the collision.

Interpretation:

- Margin of safety below 0 means you are paying above base-case value.
- Risk/reward below 2:1 rarely justifies a BUY on a single name.
- A high expected_value driven entirely by a low-probability bull case is not
  the same as a robust one. Show expected_value with and without the bull case.

## Ranges, not false precision

A fair value carried to two decimals implies a precision the inputs cannot
support. `Fair value = SEK 183.47` is not more informative than `SEK 175–200`;
it is less honest, because it hides how wide the real uncertainty is.

Report scenarios as ranges:

```
Bear    SEK 125–145
Base    SEK 175–200
Bull    SEK 240–275
```

Width the range from the sensitivity you already computed: if fair value moves
from 176 to 198 across WACC ±1.5% and terminal growth ±1% — the same spread
the mandatory sensitivity table above requires — that spread **is** the base
range. A range narrower than your own sensitivity table is a contradiction.

Give a point estimate only where the inputs justify it — a net-cash company on a
stable multiple, say — and state why.

## Presenting it

State, in one sentence, **which two or three assumptions drive most of the
valuation** — usually terminal margin, terminal growth and the discount rate.
Then show what fair value becomes if each is wrong by a realistic margin.

If the DCF and the multiples analysis disagree materially, do not average them.
Explain which you trust for this business and why.
