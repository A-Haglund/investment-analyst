# Worked example — calibrating the output

An abridged extract showing the expected **tagging density, sourcing style and
closing block**. It is a format reference, not a template to copy: the figures
below are illustrative.

Rule of thumb for tagging: **one tag per material claim**, not per sentence and
not per paragraph. A number that enters the model gets a tag. Narrative
connective tissue does not.

---

## Extract 0 — the verdict block that opens every analysis

Four lines inside the fence — identity and date, the call with conviction,
price and fair value, and the two scores — the prose outside it so it wraps at
any width. Note what it refuses to hide: conviction is on the recommendation
line, data confidence sits beside the investment score, fair value is a range,
and the missing multiple is named rather than omitted.

````
```
VERDICT — NVIDIA Corp (NVDA, NasdaqGS) · 2026-08-28

  HOLD — MEDIUM CONVICTION
  USD 217.55 now -> fair value 190-215 (base) · -13% to -1% · exp. return -7.5%
  Investment Score 78/100 · Data Confidence 84/100
```

**Why.** Best-in-class economics, but the price already requires a decade of 21%
growth from a USD 216bn base.
**Risk.** Hyperscaler capex is the entire demand base, and four customers fund
most of it.
**Priced in.** ~21% revenue CAGR and a 55% terminal EBIT margin.
`OPINION - reverse DCF`
**Watch.** Gross margin below 65% for two consecutive quarters ends the case
(Bevakning, row 1, in the closing block).
**Unverified.** SBC is single-source, taken from the 10-K note.
````

A 78/100 company at a price that offers no margin of safety is a HOLD. The block
says that in the first two lines rather than arriving at it on page four. Note
also that the expected return is **negative while the score is 78/100** — that is
the intended behaviour: the scorecard measures the company, the recommendation
measures the opportunity at this price.

## Extract 0b — a TLDR answer in full

Everything the system will say at TLDR depth. The header is the same fenced
shape the verdict block always uses — TLDR rewrites the second and third lines
for a depth that has no scorecard and no scenarios, never the header itself —
and the data gap is named in the prose, not dropped to fit.

````
```
VERDICT — NVIDIA Corp (NVDA, NasdaqGS) · 2026-08-28

  HOLD — MEDIUM CONVICTION
  USD 217.55 now -> fair value 175-215 (base, multiples vs own history) · -20% to -1%
  Investment Score n/a — no scorecard at this depth · Data Confidence 84/100
```

NVIDIA sells the chips that AI data centres are built on, and it is still
growing fast — revenue rose 65% last year to USD 216bn. The business is
excellent; the price is the problem. At today's level, the shares trade near
the top of their own five-year EV/EBIT range, so the price already assumes the
current growth rate persists — a demanding ask from a USD 216bn base. The main
risk is concentration: four customers fund most of the demand and their
spending plans can change in a quarter — and the 10-K doesn't even name them,
so that concentration can't be independently checked. I would turn positive
below USD 165, or if gross margin recovers above 73% for two quarters.

*A standard run would add the moat, growth and management sections and a
proper scenario range — about ten minutes.*
````

Under 150 words, no table, no jargon a non-specialist would have to look up —
and it still says HOLD, why, what breaks it, and what would change it. This
range (175-215) is wider than Extract 0's DCF-derived 190-215, and that is
correct, not a contradiction: different depths derive fair value by different
methods — multiples here, DCF at DEEP — and the checksum rule that binds the
verdict to the decision record holds within one analysis, not across depths.

## Extract 1 — a fundamentals paragraph

> Revenue reached USD 215,938m in FY2026 (year ended 25 Jan 2026), up 65.5% on
> FY2025's USD 130,497m `FACT — 10-K, accession 0001045810-26-000021, tag
> Revenues`. Gross margin was 71.1%, down from 75.0% `FACT — same filing`, which
> management attributes to product mix during the Blackwell ramp `FACT — 10-K
> MD&A`. Whether that mix effect reverses as the ramp matures is the open
> question `OPINION`; I assume partial recovery to 73% by FY2028
> `ASSUMPTION — midpoint between the FY2025 peak and the FY2026 low, with no
> company guidance on steady-state mix`.
>
> Free cash flow was USD 96,676m (CFO 102,718 less capex 6,042) `FACT — same
> filing`, a 44.8% FCF margin. FCF conversion against net income was 80.5%
> `FACT, derived`. That gap is not a red flag on its own — it reflects working
> capital absorbed by a 96% increase in receivables `FACT` — but it is the line
> to watch if growth decelerates, because receivables built during a ramp
> convert to cash only if the demand behind them holds `OPINION`.

Note what earns a tag: every figure, plus the two judgements. Note also that
the derived figure is marked `FACT, derived` — computed from tagged inputs, not
read off a filing.

## Extract 2 — handling a gap

> Share-based compensation for the Swedish peer is `DATA NOT AVAILABLE`. ESEF
> tags primary statements only, and the interim report does not break SBC out
> separately. It is disclosed in the annual report note on incentive programmes,
> which is outside the period analysed here. The owner-adjusted FCF figure below
> therefore **overstates** cash available to shareholders by an unknown amount;
> treat it as an upper bound.

The gap is named, its cause is explained, and its direction of error is stated.
That is the standard — not silence, and not a plugged estimate.

## Extract 3 — reverse DCF framing

> At USD 217.55 (as of 2026-08-28 20:00 UTC, Nasdaq/Yahoo cross-checked), the
> bottom-up build gives a 4.2% risk-free rate from the 10y Treasury `FACT`, a
> beta of 1.6 per the company's own disclosure `FACT`, and an ERP of 5.0%
> `ASSUMPTION — default, not sourced to a specific study`, for a cost of equity
> of 12.2% (4.2 + 1.6 × 5.0) `FACT, derived`. Debt is negligible against an
> equity value of roughly USD 5.0tn `FACT — Extract 4`, so the bottom-up WACC is
> essentially that same 12.2%. The DCF below instead holds WACC at 9.5%
> `ASSUMPTION — house rate, not the bottom-up 12.2% figure` and terminal growth
> at 2.5% `ASSUMPTION`. On that basis, the market is pricing a 10-year revenue
> CAGR of roughly 21% with terminal EBIT margins near 55% `OPINION — output of
> the reverse DCF, sensitive to both inputs`. At the bottom-up 12.2%, fair value
> falls well below the 190-215 range shown here and the growth rate the market
> would need to be pricing rises materially — so the 9.5% choice is doing a
> great deal of work, and it is the single most sensitive input in the whole
> valuation `OPINION`.
>
> The company has delivered a 4-yr CAGR (FY2022→FY2026) of 68% `FACT`. So the
> implied expectation is a *deceleration*, not a heroic extrapolation. That
> reframes the question from "is this expensive" to "is 21% for a decade
> achievable from a USD 216bn base" `OPINION`.

This is what a stated override looks like: the number the method gives (12.2%),
the number actually used (9.5%), and the effect of the difference — all three,
not the assumption tag alone.

## Extract 4 — the enterprise-to-equity bridge

Show it. Do not jump from a DCF to a per-share number.

```
PV of explicit FCFF (yr 1-10)              1,842,000
PV of terminal value                       3,118,000
Enterprise value                           4,960,000
  less total debt (incl. leases)             -8,468
  less minority interests                         0
  plus cash and short-term investments      +31,032
Equity value                               4,982,564
÷ diluted shares (24,514m)
= Fair value per share                     USD 203.25
```

The bridge produces a point. **The output never does.** USD 203.25 is the
midpoint of the base case; the range carried forward everywhere else — 190 to
215 — comes from the WACC and terminal-growth sensitivity grid around it. A point
estimate that survives into a summary is where false precision does the most
damage.


## Extract 5 — the Evidence block

Runs at STANDARD and DEEP as section 11, immediately before the decision record.
A control record, not prose. Grouped by status, strongest first, with the score
in the header and the tally closing it.

```
EVIDENCE — Data Confidence 84/100

  IDENTITY   NVIDIA Corp · CIK 0001045810 · LEI 549300S4KLFTLO7GAT8   VERIFIED
             Reports in USD · quoted in USD · FY ends last Sunday of January
             24,514m diluted shares, single class, 10-K cover page
  PRICE      USD 217.55 · 2026-08-28 20:00 UTC · Nasdaq · 2 feeds, 0.04% apart

  VERIFIED           two independent origins agree within 1%
    Revenue FY2026          215,938 USDm   XBRL tag Revenues | 8-K release
    Operating income        139,204 USDm   XBRL | 8-K release
    Net income FY2026       120,101 USDm   XBRL | 8-K release
    Balance sheet ties      assets = liabilities + equity, 0.00%
    Cash roll-forward       0.00%

  CROSS-CHECKED      a second source agrees but shares an origin
    Diluted shares          24,514m        10-K cover | XBRL dei tag
    FY2025 comparative      unchanged — no restatement

  SINGLE SOURCE      no independent check is possible
    SBC FY2026              10-K note 4, no second filing carries it
    FY2027 guidance         management statement on the Q4 call
    Datacentre TAM          company investor deck, not independently sourced

  CONFLICT           none
  STALE              none

  DATA NOT AVAILABLE
    Customer names          "four customers >10% of revenue", unnamed in the 10-K

  TALLY   5 of 11 material figures VERIFIED · 2 cross-checked · 3 single-source
          · 0 conflicts · 0 stale · 1 not available
```

Three things to notice. The tally makes the shape of the evidence unmissable:
five verified out of eleven is a good result honestly stated, and a run with
nothing cross-checked would have to print `0 of 11`, which cannot read as clean.
The empty groups are printed as `none` rather than dropped — an absent group
reads as an absent problem. And the diluted share count is `CROSS-CHECKED`, not
`VERIFIED`, because the cover page and the XBRL tag come from the same document:
one origin, two paths.

## Extract 6 — charts, where they earn their place

Four forms, all text, and only where a shape says something a number cannot:
the two shown here, the scenario ladder in the decision record (Extract 7),
and the bar column in the scorecard (`references/bear-case-and-scoring.md`
Part 2).

```
Revenue      26.9 ▁▁▃▅█ 215.9  USD bn   4-yr CAGR (FY2022→FY2026) · +68%/yr
GM %         64.9 ▅▁▇█▇  71.1  %        trough FY2023 · peak FY2025
```

```
EV/EBIT vs own 5-year range
  22.1 ├───────●───────────────────────┤ 78.4    now 34.6 · median 41.2 · 31st pctile
```

The scenario ladder lives in the decision record (Extract 7), not here —
repeating a chart is not the same as repeating a figure.

## Extract 7 — the decision record

The fixed shape defined in `SKILL.md` §9. Numerically identical to the verdict
block — same call, same conviction, same range, same two scores.

```
DECISION — NVDA · NVIDIA Corp (CIK 0001045810) · 2026-08-28

RECOMMENDATION    HOLD — MEDIUM CONVICTION
Price             USD 217.55   (2026-08-28 20:00 UTC, Nasdaq · reports in USD)
Fair value        USD 190-215 base (50%) · 121 bear (30%) · 318 bull (20%)

                  121 ──────────────────├═════┤●────────────────────────── 318
                  bear 121 · base 190-215 · now 217.55 · bull 318

Expected return   -7.5%  (probability-weighted across scenarios)
Margin of safety  -14% to base-low · -1% to base-high
Investment Score  78/100        Data Confidence  84/100

Rests on          1. Blackwell mix recovers GM to ~73% by FY2028   ASSUMPTION
                  2. Top-four hyperscaler capex holds through 2027 ASSUMPTION
                  3. WACC 9.5% (house rate; bottom-up build implies
                     12.2%), terminal growth 2.5%                  ASSUMPTION

Triggers          see Bevakning in the closing block — 5 rows
```

*This is analysis, not investment advice.*

The ladder is the one chart that carries the whole recommendation: the marker
sits just past the base range's high edge, which is the HOLD, visible before a
word is read.

---

Check the record against Extract 0 line by line: `HOLD — MEDIUM CONVICTION`,
`190-215`, `78/100`, `84/100`, `-7.5%`. Five figures, both places, identical.
That repetition is the only one the format allows, and it exists precisely so
that a divergence is caught. **Data Confidence appears in both.**
