# Devil's advocate, scorecard and recommendation

## Part 1 — Bear case / devil's advocate (working discipline, underlying material)

**Mandatory. Never skip, never soften, and never write it after the
recommendation is already formed.** Write it before scoring.

The devil's advocate work is working discipline and underlying material. What
reaches the reader in the answer is not the full interrogation but its summary:
the bear-scenario valuation, the single largest risk stated in the verdict's
`Risk` line, and material challenges surfaced in the `Talar emot` list
(SKILL.md §6 section 4).

The purpose is adversarial: actively try to prove the investment case wrong. If
this section reads like a list of generic risks ("competition", "macro
headwinds", "regulation"), it has failed. Every item must be specific to this
company and falsifiable.

### Required questions

1. **What is the single largest risk?** Not a list — the one that would do the
   most damage. Quantify its impact on fair value.
2. **What is the weakest assumption in my own case?** Name the input where being
   wrong hurts most, and how wrong it plausibly could be.
3. **What might the market have missed?** Something you can point to in a filing
   or a data series, not a feeling.
4. **What is already priced in?** At DEEP depth, use the reverse DCF. At
   STANDARD, where the DCF is skipped, invert the current multiple into an
   implied long-run growth rate instead, and name whichever method you used in
   the tag. If the market implies 20% growth for a decade, "the company is
   growing fast" is not an insight — it is the consensus.
5. **Which competitor could damage this company, and how?** Named, with the
   specific mechanism.
6. **Which macro factors genuinely matter here?** Rates, FX, commodity inputs,
   cycle position. Only those with a traceable line to this company's P&L —
   for a Swedish exporter, SEK/EUR belongs here; for a domestic retailer it
   probably does not.
7. **What does the bear on the other side of my trade believe?** Steelman it.
   Write the two strongest sentences a well-informed short seller would write.

   For a Swedish company this is not hypothetical — run
   `scripts/short_se.py "NAME"` and find out whether one actually exists. Report
   the aggregate short interest, the named holders above 0.5%, and the trend.
   Absence of any disclosed short position is equally worth stating: it means no
   professional has been willing to put capital behind the bear case.

### The trigger table — the single home for every invalidation condition

One table carries every invalidation condition, in both directions, and
everything else in the document references it.

It is printed once, in the closing block's `Bevakning` table (`SKILL.md` §9),
where the reader needs it. Section 10 carries the thesis prose and points
forward to it. Both directions belong in the table —
what would make you sell, and what would make you buy more.

```markdown
| # | Utlösare                   | Tröskel                    | Följd                 | Kontrolleras i   |
|---|----------------------------|----------------------------|-----------------------|------------------|
| 1 | Rörelsemarginal            | <15% två kvartal i rad     | tesen bryts -> SÄLJ   | kvartalsrapport  |
| 2 | Största gruvkundernas capex| guidning sänks >10%        | bas -> bear           | kunders kapitalmarknadsdagar |
| 3 | Utspädande M&A utanför kärnan | affär >5% av börsvärdet | övertygelse -> LÅG    | pressmeddelande  |
| 4 | Över bull-värdet           | > SEK 540                  | värderingsexit        | kurs             |
| 5 | Price below                 | < SEK 330, thesis intact   | conviction -> HIGH    | quote            |
```

Rules:

- **Every threshold numeric and checkable against a future filing.** "Growth
  slows" is not a trigger. "Organic growth below 3% for two consecutive
  quarters" is. If you could not verify a row six months from now, rewrite it.
- **This table is also the thesis ledger's input contract.** A row of the form
  `<metric> <op> <value>[%|x] [for <n> consecutive quarters|years]` is exactly
  what `thesis_ledger.py --breaker` parses, which is why the numeric-threshold
  rule above is not a style preference: on a BUY or SELL call, `/analyze` stores
  these rows and `/portfolio`'s layer 1 re-tests them against the next filing.
  A row that cannot be expressed as a breaker was never a trigger.
- **The price rows stay out of the ledger.** Rows 4 and 5 above are valuation
  exits and belong in the delivered table, but the ledger stores no prices at
  all — a thesis that flips on a quote is a trade, not a thesis — so only the
  fundamental rows are passed to `--breaker`. Run `thesis_ledger.py --metrics`
  to see which metrics can be tested automatically and which need a hand-read
  figure through `--observe`.
- **Name where it is reported**, so the reader knows when to look.
- Three to six rows. More than that is a watchlist, not a thesis.
- **This table is the only place a threshold is written.** The verdict's
  `Watch` line quotes row 1 in full — the ten-second reader needs it — and
  nothing else restates a number. The decision record references the table
  without repeating a threshold, in the form
  `Triggers   see Bevakning in the closing block — N rows`.

## Part 2 — Investment scorecard (underlying material)

The scorecard is underlying material per SKILL.md §6: produced on every STANDARD
and DEEP run, printed only when the reader requests the underlying material. The
**Investment Score derived from it** (and capped by Data Confidence) appears on
the verdict block in the delivered answer; the nine-category detail does not.

Nine categories, each 0-10 with one sentence of justification drawn from
evidence already presented. No new claims here.

```markdown
| Category           | /10 |            | Justification                              |
|--------------------|-----|------------|--------------------------------------------|
| Business quality   | 8   | ████████░░ | Aftermarket ~40% of revenue smooths cycle  |
| Growth             | 7   | ███████░░░ | Orders +9% organic; mining-capex dependent |
| Profitability      | 8   | ████████░░ | 16.9% EBIT vs 14.8% own 10y median         |
| Balance sheet      | 8   | ████████░░ | Net debt/EBITDA 1.1x; no debt wall to 2029 |
| Moat               | 7   | ███████░░░ | Installed base, switching costs; see §3    |
| Management         | 8   | ████████░░ | 7 of 8 guidance periods delivered          |
| Capital allocation | 7   | ███████░░░ | Bolt-ons at sane multiples; no buybacks    |
| Valuation          | 7   | ███████░░░ | 64th pctile own 10y P/E — fair, not cheap  |
| Risk/reward        | 7   | ███████░░░ | +32% to base-high vs -13% bear, 55/25 wtd  |
```

The nine category scores sum to 67 of 90, and

```
INVESTMENT SCORE = sum of the nine categories x (100 / 90)
```

carries that forward to the verdict and the decision record.

There is no tenth "data quality" row. It would be Data Confidence divided by
ten — the same number in a second place, and the whole point is that the two
answer different questions. Keeping them apart is what stops good evidence
about a mediocre business flattering the score, and stops a cheap stock with no
verifiable numbers scoring like one with audited ones.

Two failure modes to guard against:

- **Halo scoring** — a great business scoring 9 on valuation because it is a
  great business. Valuation scores the price, not the company.
- **Compensating errors** — adjusting one score to make the total feel right.

## Part 3 — Conviction

Conviction measures confidence in the analysis, not enthusiasm for the stock,
and it is **capped by the weakest input rather than averaged**. The full ladder
and its hard caps live in `references/data-quality.md` §7 — this section does
not restate them.

A high score with low conviction is not a strong buy — it is a small position,
or none. Say that in those words.

## Part 4 — Recommendation

Derive it. Do not feel it.

| Call | Typical conditions |
|---|---|
| **STRONG BUY** | Score ≥ 80, margin of safety ≥ 30%, risk/reward ≥ 3:1, conviction HIGH |
| **BUY** | Score ≥ 70, margin of safety ≥ 15%, risk/reward ≥ 2:1 |
| **HOLD** | Fairly valued, or good business at a full price, or a cheap price with unresolved risk |
| **SELL** | Trading above base-case value, or deteriorating fundamentals with no offsetting discount |
| **STRONG SELL** | Above bull-case value, or a broken thesis, or balance-sheet distress |

These are guides, not a lookup table. Where your judgement departs from what the
numbers suggest, state that explicitly and give the reason. An unexplained
override is the failure mode this whole framework exists to prevent.

**The recommendation is not a function of the investment score.** It is driven
by valuation, expected return, downside, margin of safety, business quality,
balance sheet and — explicitly — data confidence. Name the two or three
assumptions the call actually rests on. If the recommendation would flip when
one of them moves by a realistic amount, say which one and by how much.

Low data confidence does not veto a BUY, but it does cap the conviction, and
the conviction — not the data confidence number, which already sits on the
scores line directly beneath — must appear on the recommendation line, not in a
footnote: `KÖP — LÅG ÖVERTYGELSE` (SKILL.md §13).

### The decision record (underlying material)

The decision record is underlying material per SKILL.md §6: printed only when the
reader requests to see the underlying material. Its shape is defined once, in
`SKILL.md` §9, and is not restated here — one template with one owner.

Three rules bear repeating because they are the ones most easily lost:

- **The record is emitted as JSON and the block is rendered from it**, so the
  verdict block's numbers are copies of the record's rather than the other way
  round. `decision_record.py` recomputes the expected return and the margins of
  safety and refuses the record when they disagree with its own inputs.
- **The conviction ceiling is enforced there too**, from the depth and the
  reason codes. A record above it is refused rather than warned about, so a
  weak-evidence call cannot be filed as a confident one.
- **Data Confidence is never omitted**, and no point estimate appears on the
  fair-value line.

`Rests on` names the two or three assumptions the call actually depends on,
each with its basis. Triggers follow the reference form given in Part 1's
trigger-table rules — never a restated threshold.

Close with one line: this is analysis, not investment advice.
