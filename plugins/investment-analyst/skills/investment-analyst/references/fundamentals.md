# Fundamental analysis

Compute every metric below over at least five years, or the full listed history
if shorter. A single year tells you nothing about a business; the trend and its
volatility are the analysis.

For a Swedish issuer, ESEF alone will not reach five years: the index runs
FY2021–FY2024, four years (`references/sweden.md`). For the fifth year, use
the **Flerårsöversikt** (five-year summary) in the annual report — nearly
every Swedish issuer publishes one, already assembled and audited; see
`references/sweden.md`'s "The five-year summary" note.

Present each as a time series, not a point. Where a figure is unavailable, write
`DATA NOT AVAILABLE` in that cell rather than interpolating.

## Growth

| Metric | Formula | What to look for |
|---|---|---|
| Revenue growth | `rev_t / rev_t-1 - 1` | YoY and 3/5-year CAGR |
| Organic growth | Reported growth less M&A and FX contribution | Companies that disclose it; if not disclosed, say so |
| EPS growth | Diluted EPS YoY | Compare against revenue growth — the gap is margin plus buybacks |
| FCF/share growth | FCF ÷ diluted shares | The cleanest per-owner growth measure |

Decompose reported growth into **organic volume, price/mix, acquisitions and
FX**. If a company grows 20% and 15 points come from acquisitions, the business
is not a 20% grower. State the split or state that it is not disclosed.

## Margins

| Metric | Formula |
|---|---|
| Gross margin | `gross_profit / revenue` |
| EBITDA margin | `(operating_income + D&A) / revenue` |
| EBIT margin | `operating_income / revenue` |
| Net margin | `net_income / revenue` |
| FCF margin | `FCF / revenue` |

Direction matters more than level. Expanding gross margin with flat EBIT margin
means opex is outgrowing the business — find out why.

## Cash generation

```
FCF               = CFO - capex
FCF (owner-adj.)  = CFO - capex - SBC
Conversion        = FCF / net_income
Cash EBIT ratio   = CFO / operating_income
```

Report both FCF definitions. Where SBC is large, the unadjusted figure overstates
the cash available to owners, because the company must buy back shares to prevent
dilution.

## Returns on capital

```
Invested capital  = total_debt (incl. lease liabilities where capitalised)
                  + total equity (including non-controlling interests)
                  - cash - short-term investments
NOPAT             = operating_income x (1 - effective_tax_rate)
ROIC              = NOPAT / average invested capital
ROE               = net_income / average equity
ROA               = net_income / average total_assets
```

Include non-controlling interests in the equity leg. NOPAT is built from
**consolidated** operating income, which includes the earnings of
partly-owned subsidiaries in full — so the denominator must include the
capital those minorities supplied, or ROIC is inflated by attributing all of
a partly-owned subsidiary's operating profit to a capital base that excludes
part of its funding. Subtract cash **and short-term investments** here, the
same pair used for net debt below — not cash alone.

The gap is not academic. Total debt 2,000; parent equity 3,000;
non-controlling interests 1,000; cash 500; EBIT 550; tax 25%, so NOPAT
412.5.

- Omitting non-controlling interests: IC = 2,000 + 3,000 − 500 = 4,500 →
  ROIC = 412.5 / 4,500 = **9.17%**.
- Correct: IC = 2,000 + 4,000 − 500 = 5,500 → ROIC = 412.5 / 5,500 =
  **7.50%**.

Against a roughly 8% WACC, those two numbers give **opposite verdicts** on
whether the business creates value — one says yes, the other says no, from
the same underlying figures.

Use **average** balance-sheet values across the period, not closing values —
closing values overstate returns for a growing company.

ROIC is the single most informative quality metric. Compare it against a rough
WACC: a business earning below its cost of capital destroys value as it grows.
Where goodwill from acquisitions is large, show ROIC both including and
excluding goodwill — the gap tells you what management paid for growth.

## Balance sheet

```
Net debt          = total_debt - cash - short-term investments
Leverage          = net_debt / EBITDA
Interest coverage = EBIT / interest_expense
Current ratio     = current_assets / current_liabilities
```

Also examine: debt maturity schedule, fixed versus floating mix, covenants,
operating lease obligations, and pension deficits. A company with 1.5x leverage
and a wall of maturities next year is riskier than one at 2.5x termed out to 2032.

Leverage and interest coverage as defined here do not apply to banks or
insurers — see `references/valuation.md`'s "Financials and real estate"
section for the substitute framework.

### Leases — required before any cross-framework comparison

IFRS 16 puts *all* leases on balance sheet as a lease liability, with
depreciation and interest replacing rent. Rent is therefore **excluded** from
opex, so **EBITDA is inflated** relative to a framework that leaves operating
leases off balance sheet, and lease interest sits in interest expense rather
than opex.

Rules for this skill:

1. **Establish the accounting framework before anything else.** A Swedish
   MTF issuer — First North (outside Premier), Spotlight or NGM — may report
   consolidated accounts under Swedish GAAP **K3**, which has no IFRS 16
   equivalent: operating leases stay off balance sheet and rent stays in
   operating expenses, and there is no lease liability to add to
   `total_debt`. See `references/sweden.md`'s "Accounting basis: IFRS or K3"
   section before applying any rule below.
2. For an IFRS filer, state explicitly whether `total_debt` includes lease
   liabilities. Default to **including** them — they are contractual
   obligations.
3. When comparing an IFRS filer against a K3 filer, or any peer whose
   framework does not capitalise operating leases, on EV/EBITDA or
   ND/EBITDA, say that the bases differ, and prefer **EV/EBIT** which is far
   less affected.
4. For interest coverage on an IFRS filer, note whether lease interest is in
   the denominator; it usually is, and it depresses the ratio versus a peer
   under a framework that keeps rent in opex.

Lease-heavy businesses — retail, restaurants, airlines, logistics — are where
this matters most. For an asset-light software company it is usually immaterial;
say so and move on.

## Working capital

```
DSO = receivables / revenue x 365
DIO = inventory / COGS x 365
DPO = payables / COGS x 365
Cash conversion cycle = DSO + DIO - DPO
```

Rising DSO alongside rising revenue is a classic early warning: the company may
be buying growth with looser credit terms, or channel-stuffing.

## Shareholder flows

| Item | Read as |
|---|---|
| SBC as % of revenue | Above 5% is material; above 10% demands adjustment in valuation |
| SBC as % of FCF | How much of "free" cash is really compensation |
| Buybacks | Compare spend against the change in diluted share count |
| Net dilution | Diluted share count YoY — the number that actually matters |
| Dividends | Payout ratio on FCF, not on earnings |
| Acquisitions | Cash spent versus incremental revenue and EBIT delivered |

A company spending 3bn on buybacks while diluted shares stay flat is not
returning capital — it is funding compensation. Say that explicitly.

Diluted share count above is for per-share and dilution metrics. Market cap
and the outstanding share count that feeds it follow the canonical
definition in `references/valuation.md` (outstanding, ex-treasury, per share
class at that class's own price) — do not substitute a diluted count for
either.

## Quality of earnings — mandatory

This is the check that catches most problems. Run it every time.

1. **Cumulative accrual gap.** Sum net income and sum CFO over five years. If
   CFO is persistently below net income, earnings are not converting to cash.
   Identify which balance-sheet line absorbs the difference.
2. **Receivables versus revenue.** Receivables growing materially faster than
   revenue over multiple periods is a red flag.
3. **Inventory versus COGS.** Same test. Rising inventory into slowing demand
   precedes write-downs.
4. **Capitalisation policy.** Development costs, contract costs and software
   moved to the balance sheet flatter current profit. Check whether the
   capitalised balance is growing faster than revenue.
5. **Reported versus adjusted.** List every add-back. "Non-recurring" charges
   that recur every year are operating costs.
6. **Tax rate.** A falling effective rate flattering EPS growth is not
   operational improvement.
7. **Segment consistency.** Reporting-segment changes often obscure a
   deteriorating business. Note any re-segmentation.

Conclude the section with one explicit sentence: **do reported earnings reflect
actual cash generation, yes or no**, and the evidence.

## Output shape

A table with years as columns and metrics as rows, followed by:

- the three metrics that improved most and why
- the three that deteriorated most and why
- the quality-of-earnings verdict
- anything you could not source, listed as `DATA NOT AVAILABLE`
