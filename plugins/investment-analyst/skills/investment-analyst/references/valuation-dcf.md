# Valuation — DCF and scenario work

This file extends `valuation-core.md`, which covers setup, market-cap and EV
definitions, multiples, and peer comparison. Read that first; this document
assumes you have.

## DCF

Build it explicitly so every input is visible and challengeable.

```
Years 1-5     explicit forecast: revenue, margin, tax, capex, working capital
Years 6-10    fade toward terminal growth
Terminal      Gordon growth, or an exit multiple as a cross-check

FCFF          = EBIT x (1-t) + D&A - capex - change in working capital
WACC          = E/V x cost_of_equity + D/V x cost_of_debt x (1-t)
Cost of equity= risk-free + beta x equity risk premium
```

### The bridge from enterprise value to fair value per share

**Do not skip this. Discounting FCFF at WACC produces an enterprise value, not
an equity value.** Comparing that number to the share price is the single most
common way a DCF produces a wrong answer.

```
Enterprise value      = PV(explicit FCFF) + PV(terminal value)
  - total debt                       (including lease liabilities — see the
                                       lease-consistency requirement below and
                                       `references/fundamentals.md`)
  - minority interests
  - pension deficit
  - preferred stock
  + cash and short-term investments
  + non-operating assets             (associates, investment portfolio)
= Equity value
÷ diluted shares outstanding
= FAIR VALUE PER SHARE
```

Only that last figure is comparable to the current price. Show the bridge
explicitly in the output — the intermediate lines are where errors hide.

For a company with material future dilution, use expected diluted shares at the
mid-point of the forecast, not today's count.

### Leases must be treated consistently across FCFF, WACC and the bridge

A lease has three legs in this model — the FCFF add-back, the `total_debt`
weight inside WACC, and the bridge subtraction — and all three must follow
the *same* regime. Picking correctly on one leg and defaulting on another is
the common failure mode: `fundamentals.md` defaults `total_debt` to include
lease liabilities, which is right under one regime and wrong under the
other, so the choice cannot be left implicit.

**Regime A — lease as debt.** Add back *all* D&A, including right-of-use
depreciation; do not treat right-of-use additions as capex (the liability
already captures them); include lease liabilities in `total_debt`, so they
sit inside the WACC weights; subtract the lease liability in the bridge.

**Regime B — lease as operating cost.** Add ROU depreciation back to EBIT
and deduct the actual cash lease payment instead, so the cash flow reflects
rent rather than depreciation and interest; exclude lease liabilities from
`total_debt` and therefore from WACC; subtract nothing for leases in the
bridge, because the obligation was never treated as debt.

Never mix legs across the two regimes, and state which regime was used. The
magnitude is what makes the rule stick. Retailer: EBIT 100 (after ROU
depreciation 50 and other D&A 20), tax 25%, capex 20, ΔWC 0, WACC 8%, g 2%,
lease liability 400 at roughly 4% (interest ≈16), cash rent roughly 66, no
other net debt.

| | A — lease as debt | B — lease as operating cost | Hybrid (wrong) |
|---|---|---|---|
| EBIT used | 100 | 100 + 50 − 66 = 84 | 100 |
| FCFF | 100×0.75 + 70 − 20 = **125** | 84×0.75 + 20 − 20 = **63** | 100×0.75 + 70 − 20 − 50 = **75** |
| Terminal (÷0.06) | **2,083** | **1,050** | **1,250** |
| Bridge | − 400 | − 0 | − 400 |
| **Equity value** | **1,683** | **1,050** | **850** |

Regime B's EBIT restates the IFRS-16 income statement as if rent, not
depreciation, were the expense: add back the ROU depreciation that is
embedded in the reported 100, then deduct the actual cash rent. Its FCFF
then adds back only the non-lease D&A (20) — there is no ROU depreciation
left to add back, because the asset was never capitalised in this regime.

Regime A (1,683) is the naive error this section used to warn against: the
lease is added back in FCFF and never charged again until the bridge, which
is one charge total and is internally consistent — **but only if WACC's
weights also include the lease as debt**, which is the point most often
missed. The hybrid (850) is not a compromise between A and B; it is a
distinct third error, because it uses regime-B-flavoured FCFF (netting the
lease out of the perpetuity via the reduced add-back) while still treating
the lease as debt in the bridge and — by `fundamentals.md`'s stated default —
in WACC, so the lease's after-tax cost is charged in WACC, its principal is
charged again in FCFF, and its liability is subtracted a third time in the
bridge. That the hybrid (850) sits between A (1,683) and B (1,050) makes it
look like a reasonable middle answer; it is not — it is 19% below the
consistent operating-cost answer (1,050) for reasons that have nothing to
do with prudence.

One equivalence does not hold outside steady state: "treat ROU additions as
capex" and "exclude ROU depreciation from the add-back" are the same thing
only when the lease base is flat. With a growing lease base, additions
exceed depreciation, and the two prescriptions diverge — use whichever one
actually appears in the forecast, not whichever is easier to state.

`fundamentals.md` already handles the equivalent discipline correctly for
multiples (see its Leases section); this is the DCF version of the same
rule, extended to cover WACC as the third leg.

Requirements:

- **Terminal growth must not exceed long-run inflation plus a little** (~2–3%).
  Long-run *nominal* GDP growth is nearer 4%; the tighter cap is deliberate
  conservatism, not an identity. A higher figure assumes the company eventually
  becomes the economy.
- **Use the right risk-free rate, in the cash flows' own currency.**
  - Sweden: 10-year Swedish government bond. Fetch it live from Riksbanken —
    `https://api.riksbank.se/swea/v1/Observations/SEGVB10YC/<from>/<to>`
    (free, official, no key).
  - Euro area: the ECB's AAA euro-area sovereign curve (`macro_se.py --euro`)
    — a Bund proxy; for a French issuer this is not the OAT, see
    `references/data-sources.md`.

  Never discount SEK cash flows at a rate from another currency.
- **Beta and equity risk premium** must be stated as `ASSUMPTION` with a value,
  never left implicit. Defensible defaults when you cannot source a specific
  figure: ERP 4.5–5.5% for developed markets; beta from the company's own
  disclosure or a named peer average. If you use a default, say it is a default.
- **Subtract SBC**, or model the dilution. Do not do neither.
- **Terminal value share**: if more than ~75% of value sits in the terminal, say
  so — the DCF is really a statement about the terminal assumption.
- **Sensitivity table**: fair value across WACC ±1.5% and terminal growth ±1%.
  A DCF without one hides its own fragility.

Tag every input `ASSUMPTION` with its justification.

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
