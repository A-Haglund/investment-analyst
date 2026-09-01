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
and the mismatch between reporting currency and quote currency is named rather
than assumed away.

````
```
VERDICT — Evolution AB (EVO, Nasdaq Stockholm Large Cap) · 2026-08-28

  HOLD — MEDIUM CONVICTION
  SEK 750.00 now -> fair value 694-829 (base) · -7% to +11% · exp. return -1.7%
  Investment Score 70/100 · Data Confidence 82/100
```

**Why.** Best-in-class economics, but the price has recovered to sit inside
fair value with no margin of safety in either direction.
**Risk.** Regulatory exposure to unlicensed markets is the single largest
swing factor, and it cannot be independently sized.
**Priced in.** ~6% revenue CAGR and a 62% terminal EBIT margin.
`OPINION - reverse DCF`
**Watch.** EBITDA margin below 65% for two consecutive quarters ends the case
(Bevakning, row 1, in the closing block).
**Unverified.** Share-based compensation is `DATA NOT AVAILABLE` — ESEF tags
primary statements only.
````

A 70/100 company whose price sits inside its own fair-value range is a HOLD.
The block says that in the first two lines rather than arriving at it on page
four. Note also that the expected return is **slightly negative while the
score is 70/100** — that is the intended behaviour: the scorecard measures the
company, the recommendation measures the opportunity at this price. And the
second line only means anything because it is stated in one currency
throughout: Evolution reports in EUR, the fair value is derived in EUR, and
the SEK figure quoted here is that value converted at a stated, dated rate
(Extract 4) — never a EUR number read against a SEK price as if they shared a
unit.

## Extract 0b — a TLDR answer in full

Everything the system will say at TLDR depth. The header is the same fenced
shape the verdict block always uses — TLDR rewrites the second and third lines
for a depth that has no scorecard and no scenarios, never the header itself —
and the data gap is named in the prose, not dropped to fit.

````
```
VERDICT — Evolution AB (EVO, Nasdaq Stockholm Large Cap) · 2026-08-28

  HOLD — MEDIUM CONVICTION
  SEK 750.00 now -> fair value 660-870 (base, multiples vs own history) · -12% to +16%
  Investment Score n/a — no scorecard at this depth · Data Confidence 82/100
```

Evolution builds and streams the live-dealer casino games that online gambling
operators plug into their own platforms, and it does so more profitably than
any other listed operator in the space — EBITDA margin was 68.5% last year on
EUR 2,090m of revenue, up 9.7%. The business is excellent; the price is the
problem. At today's level the shares have recovered most of the way back from
the sell-off that followed allegations that its games were reachable in
markets without a local licence, and that recovery has closed most of the gap
to fair value: SEK 750.00 against a base-case range of SEK 694-829 (that
range is EUR 62-74 per share, converted at SEK 11.20 per EUR, Riksbanken
reference rate, 2026-08-28). The main risk is the same one that caused the
sell-off: nobody outside the company can independently size how much revenue
comes from markets where its licence status is contested, and a regulator
that forces exits from those markets would cut into the growth the market is
still paying for. I would turn more constructive below SEK 620, or if the
company discloses an independently checkable breakdown of licensed-market
revenue.

*A standard run would add the moat, growth and management sections and a
proper scenario range — about ten minutes.*
````

Under 150 words, no table, no jargon a non-specialist would have to look up —
and it still says HOLD, why, what breaks it, and what would change it. This
range (660-870) is wider than Extract 0's DCF-derived 694-829, and that is
correct, not a contradiction: different depths derive fair value by different
methods — multiples here, DCF at DEEP — and the checksum rule that binds the
verdict to the decision record holds within one analysis, not across depths.
Note too that the TLDR prose still carries the FX rate and its date in plain
words, even with no inline tags: the currency conversion is not a DEEP-only
courtesy.

## Extract 1 — a fundamentals paragraph

> Revenue reached EUR 2,090m in FY2025 (year ended 31 Dec 2025), up 9.7% on
> FY2024's EUR 1,905m `FACT — ESEF tagged filing, tag Revenue | MFN release`.
> EBITDA margin was 68.5%, down from 71.2% `FACT — same filing`, which
> management attributes to higher personnel and studio-localisation costs
> during its regulated-market production build-out `FACT — annual report,
> operational review`. Whether that cost step unwinds as the new studios
> reach scale is the open question `OPINION`; I assume partial recovery to
> 70% by FY2027 `ASSUMPTION — midpoint between the FY2024 level and the
> FY2025 low, with no company guidance on steady-state margin`.
>
> Free cash flow was EUR 1,040m (CFO 1,250 less capex 210) `FACT — same
> filing`, a 49.8% FCF margin. FCF conversion against net income (EUR
> 1,120m) was 92.9% `FACT, derived`. That gap is not a red flag on its own —
> it reflects capex timing for studios still ramping `FACT` — but it is the
> line to watch if regulatory costs rise faster than revenue, because
> capacity built for growth markets only pays back if access to those
> markets holds `OPINION`.

Note what earns a tag: every figure, plus the two judgements. Note also that
the derived figure is marked `FACT, derived` — computed from tagged inputs, not
read off a filing.

## Extract 2 — handling a gap

> Share-based compensation for Evolution is `DATA NOT AVAILABLE`. ESEF tags
> primary statements only, and the interim report does not break SBC out
> separately. It is disclosed in the annual report note on incentive
> programmes, which is outside the period analysed here. The owner-adjusted
> FCF figure below therefore **overstates** cash available to shareholders by
> an unknown amount; treat it as an upper bound.

The gap is named, its cause is explained, and its direction of error is stated.
That is the standard — not silence, and not a plugged estimate.

## Extract 3 — reverse DCF framing

> At SEK 750.00 (as of 2026-08-28 15:30 UTC, Nasdaq Stockholm/Refinitiv
> cross-checked) — equivalent to EUR 66.96 at SEK 11.20 per EUR (Riksbanken
> reference rate, same date) `FACT, derived` — the bottom-up build works in
> the reporting currency, not the quote currency: a 2.6% risk-free rate from
> the 10-year German Bund `FACT`, a beta of 1.1 per a 5-year weekly
> regression against the OMX Stockholm All-Share `FACT — Bloomberg`, and an
> ERP of 5.0% `ASSUMPTION — default, not sourced to a specific study`, for a
> cost of equity of 8.1% (2.6 + 1.1 × 5.0) `FACT, derived`. Debt is
> negligible against an equity value of roughly EUR 13.5bn `FACT — Extract
> 4`, so the bottom-up WACC is essentially that same 8.1%. The DCF below
> instead holds WACC at 9.0% `ASSUMPTION — house rate, carrying an explicit
> premium over the bottom-up 8.1% for regulatory risk that beta does not
> price` and terminal growth at 2.0% `ASSUMPTION`. On that basis, the market
> is pricing a 10-year revenue CAGR of roughly 6% with terminal EBIT margins
> near 62% `OPINION — output of the reverse DCF, sensitive to both inputs`.
> At the bottom-up 8.1%, fair value rises well above the 62-74 range shown
> here, and the growth rate the market would need to be pricing falls
> further still — so the 9.0% choice is doing a great deal of work, and it is
> the single most sensitive input in the whole valuation `OPINION`.
>
> The company has delivered a 4-yr CAGR (FY2021→FY2025) of 24% `FACT`. So the
> implied expectation is a *sharp deceleration*, not a continuation of the
> recent past. That reframes the question from "is this expensive" to "has
> the market already priced in enough of a slowdown, or is a further
> step-down likely if licensed-market access narrows" `OPINION`.

This is what a stated override looks like: the number the method gives (8.1%),
the number actually used (9.0%), and the effect of the difference — all three,
not the assumption tag alone. It is also what a stated currency choice looks
like: the discount rate is built in EUR because the cash flows are in EUR, and
only the final output crosses into SEK, once, clearly, at Extract 4 — never
mid-calculation.

## Extract 4 — the enterprise-to-equity bridge

Show it. Do not jump from a DCF to a per-share number — and do not let a
per-share number cross a currency boundary silently, either.

```
PV of explicit FCFF (yr 1-10)              5,850
PV of terminal value                       7,230
Enterprise value                          13,080
  less total debt (incl. leases)            -165
  less minority interests                      0
  plus cash and short-term investments       +632
Equity value                              13,547
÷ diluted shares (199,226,613)
= Fair value per share                  EUR 68.00
× SEK per EUR (11.20, Riksbanken reference rate, 2026-08-28)
= Fair value per share                  SEK 761.60
```

The bridge produces a point. **The output never does.** EUR 68.00 (SEK 761.60
at the stated rate) is the midpoint of the base case; the range carried
forward everywhere else — SEK 694-829 — comes from the WACC and
terminal-growth sensitivity grid around it, run in EUR and converted at that
same 11.20 rate throughout, so the point and the range are never mixed across
currencies. A point estimate that survives into a summary is where false
precision does the most damage — and converting only the point while leaving
the range in EUR would be a second, quieter way to let the same error back in.


## Extract 5 — the Evidence block

Runs at STANDARD and DEEP as section 11, immediately before the decision record.
A control record, not prose. Grouped by status, strongest first, with the score
in the header and the tally closing it.

```
EVIDENCE — Data Confidence 82/100

  IDENTITY   Evolution AB (publ) · orgnr 556994-5792 · LEI 549300SUH6ZR1RF6TA88   VERIFIED
             Reports in EUR · quoted in SEK · FY ends 31 December
             199,226,613 diluted shares, single class, ESEF cover page
  PRICE      SEK 750.00 · 2026-08-28 15:30 UTC · Nasdaq Stockholm · 2 feeds, 0.02% apart

  VERIFIED           two independent origins agree within 1%
    Revenue FY2025           2,090 EURm   ESEF tag Revenue | MFN release
    EBITDA FY2025             1,432 EURm  ESEF | MFN release
    Net income FY2025         1,120 EURm  ESEF | MFN release
    Balance sheet ties       assets = liabilities + equity, 0.00%
    Cash roll-forward        0.00%

  CROSS-CHECKED      a second source agrees but shares an origin
    Diluted shares            199,226,613   ESEF cover page | Euroclear register
    EUR/SEK reference rate    11.20         Riksbanken | ECB, agree within 0.1%

  SINGLE SOURCE      no independent check is possible
    FY2026 revenue outlook    management commentary, Q2 2026 call transcript
    Addressable market (TAM)  investor presentation, not independently sourced
    Licensed-market revenue mix  company disclosure, no independent breakdown

  CONFLICT           none
  STALE              none

  DATA NOT AVAILABLE
    Share-based compensation  annual report note only; interim ESEF tags
                               primary statements only

  TALLY   5 of 11 material figures VERIFIED · 2 cross-checked · 3 single-source
          · 0 conflicts · 0 stale · 1 not available
```

Three things to notice. The tally makes the shape of the evidence unmissable:
five verified out of eleven is a good result honestly stated, and a run with
nothing cross-checked would have to print `0 of 11`, which cannot read as
clean. The empty groups are printed as `none` rather than dropped — an absent
group reads as an absent problem. And the EUR/SEK rate is `CROSS-CHECKED`, not
`VERIFIED`, for the same reason the diluted share count is: Riksbanken's
reference rate and the ECB's own reference rate are both fixed from the same
interbank quotes on the same day — two publications, one origin. A currency
conversion inherits the data-quality status of the rate it uses; it is not
`VERIFIED` just because the arithmetic that follows it is exact.

## Extract 6 — charts, where they earn their place

Four forms, all text, and only where a shape says something a number cannot:
the two shown here, the scenario ladder in the decision record (Extract 7),
and the bar column in the scorecard (`references/bear-case-and-scoring.md`
Part 2).

```
Revenue      884.0 ▁▃▅▇█ 2,090  EUR m    4-yr CAGR (FY2021→FY2025) · +24%/yr
EBITDA %      62.0 ▁▃▆█▆  68.5  %        trough FY2021 · peak FY2024
```

```
EV/EBITDA vs own 5-year range
  14.2 ├──────●──────────────────┤ 38.6    now 19.8 · median 24.5 · 23rd pctile
```

The scenario ladder lives in the decision record (Extract 7), not here —
repeating a chart is not the same as repeating a figure.

## Extract 7 — the decision record

The fixed shape defined in `SKILL.md` §9. Numerically identical to the verdict
block — same call, same conviction, same range, same two scores.

```
DECISION — EVO · Evolution AB (orgnr 556994-5792) · 2026-08-28

RECOMMENDATION    HOLD — MEDIUM CONVICTION
Price             SEK 750.00   (2026-08-28 15:30 UTC, Nasdaq Stockholm · reports in EUR)
Fair value        SEK 694-829 base (50%) · 470 bear (30%) · 1,075 bull (20%)

                  470 ──────────────────────├═════●═══════┤─────────────────────── 1,075
                  bear 470 · base 694-829 · now 750.00 · bull 1,075

Expected return   -1.7%  (probability-weighted across scenarios)
Margin of safety  -8.0% to base-low · +9.5% to base-high
Investment Score  70/100        Data Confidence  82/100

Rests on          1. EBITDA margin holds >= 65% through the cycle          ASSUMPTION
                  2. Unlicensed-market revenue stays a low single-digit
                     share; not independently checkable                    ASSUMPTION
                  3. WACC 9.0% (house rate; bottom-up build implies
                     8.1%), terminal growth 2.0%                           ASSUMPTION

Triggers          see Bevakning in the closing block — 5 rows
```

*This is analysis, not investment advice.*

The ladder is the one chart that carries the whole recommendation: the marker
sits inside the base range rather than outside it in either direction, which
is the HOLD, visible before a word is read. Contrast this with `SKILL.md`'s
own Sandvik exemplar, a BUY whose marker sits to the left of the base range
entirely — the same chart form, two different shapes, because the two
companies are priced differently relative to their own fair value.

## Extract 8 — the closing block

Referenced twice already — Extract 0's `Watch` line and Extract 7's
`Triggers` line both point here — and shown in full for once. The fixed shape
is `SKILL.md` §9's signal line: the call in a form that survives skimming,
`Talar för` / `Talar emot` with a graded mark on every line, the `Bevakning`
trigger table (one home for every threshold in this analysis), `Äger du den
redan`, `Horisont`, and `Viktigast`.

````
## Slutsats

Evolution driver världens ledande livecasinoplattform med en lönsamhet ingen
konkurrent kommer i närheten av, men aktien har återhämtat sig till en nivå
där uppsidan och nedsidan i det närmaste tar ut varandra.

🟡 **Evolution AB — HOLD (behåll)** · MEDIUM CONVICTION
SEK 750,00 nu · rimligt värde 694-829 · Score 70/100 · Data Confidence 82/100

**Talar för**
🟢 EBITDA-marginal 68,5% — högst i sektorn, långt före konkurrenterna
🟢 Nettokassa, inget refinansieringsbehov till 2029
🟡 Studioexpansionen börjar amorteras av — men bara ett kvartals data
   stödjer det

**Talar emot**
🟠 Tillväxten har mer än halverats sedan 2021 (24% fyraårig CAGR ner till
   9,7% i år)
🔴 Regulatorisk exponering mot olicensierade marknader går inte att
   storleksbestämma oberoende
🔴 Ingen marginalsäkerhet kvar vid nuvarande kurs (-7% till +11%, väntad
   avkastning -1,7%)

**Bevakning**

| # | Utlösare | Tröskel | Följd |
|---|---|---|---|
| 1 | EBITDA-marginal | < 65% två kvartal i rad | tesen bryts -> SELL |
| 2 | Olicensierad andel av intäkter | > 15% enligt bolagets egen rapportering | base -> bear |
| 3 | Nytt studioinvesteringsprogram | > EUR 150m utanför plan | conviction -> LOW |
| 4 | Kurs över bull-nivå | > SEK 1 075 | värderingsmässig exit |
| 5 | Kurs under | < SEK 620, tesen intakt | conviction -> HIGH |

**Äger du den redan:** behåll positionen, men lägg inte till mer på
nuvarande nivå — låt Q3-rapporten och den regulatoriska bevakningen avgöra
riktningen.

**Horisont:** till Q3-rapporten den 23 oktober 2026
`[SINGLE SOURCE - tier 4, Avanza; overifierat mot bolaget]`.

Viktigast: Data Confidence 82/100. Aktiebaserad ersättning kunde inte
verifieras separat (`DATA NOT AVAILABLE`) eftersom ESEF bara taggar
huvudräkenskaperna. Det rimliga värdet är beräknat i EUR och omvandlat till
SEK till kursen 11,20 (Riksbanken, 2026-08-28) — jämför aldrig ett EUR-tal
mot SEK-kursen utan att ange växelkursens datum. Detta är en riktning, inte
ett facit.
````

Row 1 of `Bevakning` is the same EBITDA-margin threshold Extract 0's `Watch`
line quotes in full — the table is the only place a threshold is written, and
the verdict merely points at it. `Äger du den redan` earns its place even on a
HOLD: `SKILL.md` makes it mandatory on SELL and STRONG SELL, and says it
belongs "wherever the reader plausibly holds the security" — a HOLD call is
exactly that case. `Horisont` carries its tier-4 provenance inline because a
disclosure that survives only in a script's own output is a disclosure that
gets dropped in translation.

---

Check the record against Extract 0 line by line: `HOLD — MEDIUM CONVICTION`,
`694-829`, `70/100`, `82/100`, `-1.7%`. Five figures, both places, identical.
That repetition is the only one the format allows, and it exists precisely so
that a divergence is caught. **Data Confidence appears in both.** Extract 8's
`Slutsats` repeats four of the five again in Swedish-labelled form —
everything but the expected return, which `SKILL.md`'s own signal-line shape
does not carry — and that repetition is not required to check, only welcome
when it happens to hold.
