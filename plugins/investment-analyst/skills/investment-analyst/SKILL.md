---
name: investment-analyst
description: Deep equity research on US, Nordic (Swedish, Norwegian, Danish, Finnish), German and French listed companies, ending in a sourced BUY/HOLD/SELL call. Use when the user asks to analysera or analyze a company, aktie or ticker, wants an aktieanalys, bolagsanalys, fundamental analys, värdering/valuation, DCF, reverse DCF, fair value, riktkurs/price target, moat or vallgrav assessment, bull/base/bear case, investment thesis, investeringscase, scorecard, margin of safety, or asks whether a stock is köpvärd, whether to köpa/behålla/sälja or buy/hold/sell it, or what they should do with a holding. Also use for comparing several stocks on the same model, screening for the best risk/reward, and reviewing a portfolio's position sizing, concentration and downside risk.
---

# Investment Analyst

Produce institutional-grade equity research that a portfolio manager could act on:
every material number traced to a filing, every judgement labelled as judgement,
and a recommendation that follows from the analysis rather than from sentiment.

**This is analysis, not investment advice.** Say so once, at the end, and move on.

## 1. Evidence discipline — applies to every sentence you write

Tag every material claim. This is the core of the skill; without it the output is
an opinion piece.

| Tag | Means | Requires |
|---|---|---|
| `FACT` | Reported in a filing or official release | Source + period + document |
| `ESTIMATE` | Consensus or a named analyst's number | Who estimated it, as of when |
| `ASSUMPTION` | Your input to a model | The value and why it is defensible |
| `OPINION` | Your analytical judgement | The reasoning that produced it |

Rules that override any instinct to be helpful:

- **Never invent a number.** If you cannot source it, write `DATA NOT AVAILABLE`
  and continue. A gap that is visible is worth more than a plausible fabrication.
- **Never present a stale price as current.** Always print the as-of timestamp.
  Prices older than one trading session get an explicit staleness note.
- **Never let an estimate drift into a fact.** A guided figure is `ESTIMATE`
  until the company reports it.
- **Distinguish reported from adjusted.** If management presents "adjusted
  EBITDA", show the reported figure alongside it and state what was excluded.
- **Restatements win.** Where a later filing revises an earlier number, use the
  revised one and note the revision.
- **Sourced is not verified.** A `FACT` tag records origin, not correctness.
  Material figures get a second independent check — see
  `references/verification.md`. Where no second source exists, mark the figure
  `SINGLE SOURCE` rather than letting it pass as confirmed.
- **Never resolve a conflict silently.** Two sources disagreeing is a finding.
  Work the causes in `references/data-quality.md` §3 — period mismatch and unit
  scale first, they are the common false alarms — and if it stays unexplained,
  the figure is `CONFLICT`, it does not enter the valuation, and it appears in
  the `CONFLICT` group of the Evidence block.
- **The tier decides, not the fetch order.** `references/source-registry.md`
  names the authority for each data type. A figure taken from a lower tier when
  a higher one was available is a defect.

## 2. Where to look, in order

Work down this list; stop as soon as a tier answers the question.

1. Company IR — annual report, interim report, earnings release, investor presentation
2. Regulatory filings — SEC EDGAR (US); ESEF and the national regulator (Nordics, France); MFN.se (Nordics); Bundesanzeiger and BaFin (Germany)
3. Earnings call transcript and management guidance
4. Professional databases — only those the user is licensed for (see `references/data-sources.md`)
5. Reputable financial press
6. Everything else — usable for context, never as the basis for a number

Blogs, forums and aggregator summaries never supply a figure that enters the model.

This is the order to search in, not a tier scale. The numbered tiers recorded
on every datapoint are the four defined in `references/source-registry.md`;
this list does not renumber them.

## 3. Route the company first

Covered markets: **US, Nordics (SE/NO/DK/FI), Germany, France.** Establish which
one the issuer files in before gathering anything — the source chain differs
completely.

| Issuer | Structured data | Reference |
|---|---|---|
| **US** (NYSE/Nasdaq) | `scripts/sec_fundamentals.py TICKER` — SEC XBRL | — |
| **Swedish, regulated market** (Large/Mid/Small Cap) | `scripts/esef_fundamentals.py --country SE`; quarters from `mfn_news.py`, or `cision_news.py` for Sandvik, Atlas Copco, Hexagon and AB Volvo | `references/sweden.md` |
| **First North, Spotlight, NGM** | **No ESEF exists.** Route with `scripts/venues_se.py NAME`, then `scripts/mfn_news.py SLUG --reports --figures --text` — the release is the primary source | `references/sweden.md` §2b, `references/red-flags-and-smallcap.md` Part 2 |
| **Norwegian, Danish, Finnish** | `scripts/esef_fundamentals.py --country NO\|DK\|FI`, plus MFN | `references/europe.md` |
| **French** | `scripts/esef_fundamentals.py --country FR` | `references/europe.md` |
| **German** | **No ESEF index coverage.** Bundesanzeiger + IR PDFs | `references/europe.md` |
| **Dual-listed / foreign private issuer** | Check for a 20-F on EDGAR first; if absent treat as a local filer | — |

Outside these markets, say so plainly and offer what the free sources can
still support rather than pretending to equivalent depth.

**Check the reporting currency, always.** It does not follow the listing venue —
Evolution AB is listed in Stockholm, quoted in SEK and reports in EUR. Both
fundamentals scripts print the currency. Never compare a per-share figure in one
currency to a price in another without converting and stating the rate.

## 4. Depth — decide this before starting

A full run is four to six hours of analyst work compressed into one session, and
it is the wrong answer to most questions. Pick a depth — it is named on the
verdict block's identity line (§5) — and offer the next level up at the end.

| Depth | Time | What it runs |
|---|---|---|
| **TLDR** | 60–90 s | Phase 0 identity · price · fair value from multiples against own history, with the upside range · the call · three to five plain sentences. Under 150 words, no tables. Conviction and the largest data gap stated in the prose |
| **QUICK** | 2–4 min | Phase 0 identity · price · headline financials · multiples against own history · the single biggest risk · for a Swedish name, short interest and insider net · Data Confidence stated. No Evidence block, no scenarios, no scorecard |
| **COMPARE** | 4–6 min per company | Everything in QUICK, plus the Moat Score and a light bear/base/bull, so downside and risk/reward are real. No DCF, no reverse DCF, no peer set, no full nine-category scorecard — no Investment Score. Conviction capped at MEDIUM, as at QUICK |
| **STANDARD** | 8–12 min | Phases 0–9 · full source chain per the registry · fundamentals · moat, growth, management · valuation from multiples · bear/base/bull · devil's advocate · scorecard · Evidence block |
| **DEEP** | 25–35 min | Everything in STANDARD plus DCF and reverse DCF with sensitivities · peer multiples recomputed from filings rather than taken from the scored peer set · 10-year valuation history · ownership and its trend · short-interest trend · guidance record · corporate actions · industry benchmark · restatement check |
| **PORTFOLIO** | variable | Layers 1–2 across all holdings (breakers and alerts), then STANDARD on what they flag. Cost scales with what changed rather than with holding count. Conviction capped at MEDIUM for holdings not taken to depth. Runs the three-layer triage. See `references/portfolio.md`. |

COMPARE exists so `/compare` can rank companies on real numbers rather than
fabricated ones: the Moat Score and a light scenario build give a genuine base
fair value, downside and risk/reward without the time of a full STANDARD run.
The comparison table it feeds carries Moat Score, base fair value, upside,
downside, risk/reward, conviction and the call — never an Investment Score,
which needs the full scorecard COMPARE does not run. A column no depth
produces is dropped from the table, never estimated.

PORTFOLIO exists so `/portfolio` can review existing holdings rather than
screening potential ones. It runs the three-layer triage: layer 1 tests each
holding against its stored thesis and may fire a breaker to EXIT; layer 2
runs cheap alerts on all holdings; layer 3 takes to STANDARD depth only what
layers 1 and 2 flagged. The cost scales with what changed rather than with the
number of holdings — a clean 20-position portfolio costs much less than 20
individual STANDARD analyses. Conviction caps at MEDIUM for any holding not
taken to depth. See `references/portfolio.md` for the ADD/HOLD/TRIM/EXIT
criteria, the cost-basis rule, and ISK-specific observations.

Phase 0 runs at every depth. It is the one thing that is never traded for speed.

**Choosing when the user did not say:**

- Explicit words win. `tldr`, `sammanfattning`, `kort svar`, `i korthet`,
  `bara svaret` → TLDR. `snabb`, `snabbkoll`, `quick` → QUICK.
  `djupanalys`, `deep`, `fullständig`, `grundlig`, `DCF` → DEEP.
- `/analyze` → STANDARD. `/analyze --quick` or `/analyze --deep` override.
- A bare question — "är X köpvärd?", "vad tycker du om Y?" → **STANDARD**.
- Comparing several companies → COMPARE per company, then DEEP on the winner if
  the user asks.
- A company with very thin data (First North microcap, recent IPO) → do not go
  DEEP. A DCF on a company with two years of history is false precision. Say so
  and stop at STANDARD.

**What each depth drops — be explicit, never silent:**

- TLDR omits everything except identity, price, fair value from multiples
  against the company's own history with its upside range, the call and its
  single largest risk. Conviction caps at **MEDIUM**, and the biggest data gap
  must appear in the prose — a short answer that reads as complete is worse
  than none. TLDR is also the one depth that carries no inline tags: the
  prose is written for a non-specialist and the figures are named in plain
  words. Every other depth tags in full.
- QUICK omits moat scoring, growth decomposition, management analysis, scenarios
  and the scorecard. It still gives a recommendation, but conviction is capped
  at **MEDIUM** and the output must say which sections were skipped.
- COMPARE omits the DCF, the reverse DCF, the peer set and the nine-category
  scorecard — so it produces no Investment Score. It adds the Moat Score and a
  light bear/base/bull to QUICK, which makes downside and risk/reward real.
  Conviction caps at **MEDIUM**, as at QUICK.
- STANDARD omits the DCF and reverse DCF. Fair value comes from multiples
  against the company's own history and peers. Say that is the basis.
- DEEP omits nothing.

End every TLDR, QUICK, COMPARE and STANDARD run with one line: what a deeper
run would add and roughly how long it takes. Let the user ask; do not
escalate on your own.

## 5. The verdict block — first, always, every depth

Read in ten seconds and enough on its own. Everything after it is the evidence.

Monospace holds the three aligned numeric lines only. The prose sits outside the
fence so it wraps at any width.

The example below is a STANDARD-depth analysis.

````
```
VERDICT — Sandvik AB (SAND.ST, Nasdaq Stockholm Large Cap) · STANDARD · 2026-08-31

  BUY — MEDIUM CONVICTION
  SEK 356.00 now -> fair value 420-470 (base) · +18% to +32% · exp. return +20.9%
  Investment Score 74/100 · Data Confidence 61/100
```

**Why.** Order intake has turned while the shares still price the 2024 trough.
**Risk.** Mining capex is the whole thesis; a cut takes fair value to the bear
case, SEK 310 (-13%).
**Priced in.** ~4% long-run growth - below the company's own 10-year record.
`OPINION - implied growth from current multiple`
**Watch.** EBIT margin below 15% for two consecutive quarters ends the case
(Bevakning, row 1, in the closing block).
**Unverified.** EV/EBIT not computable - interim reports do not disclose EBIT.
SBC is single-source.
````

**The "Priced in" method and its tag depend on depth.** At STANDARD it comes
from inverting the current multiple into an implied long-run growth rate,
tagged `OPINION - implied growth from current multiple`. At DEEP it comes from
the reverse DCF instead, tagged `OPINION - reverse DCF`. Use whichever method
the depth actually ran; never tag a STANDARD run with the DEEP method's name.

Rules:

- **Conviction on the recommendation line.** `BUY — LOW CONVICTION` is a
  different instruction from `BUY`, and a reader who stops after one line must
  still receive it.
- **Both scores side by side.** A 90/45 pair says more than either alone.
- **Fair value is a range**, matched to the sensitivity analysis. A point
  estimate in a summary is where false precision does the most damage.
- **Expected return belongs here**, not only in the decision record. The
  ten-second reader is the one who needs it most.
- **Name what is missing.** Silence at the top reads as completeness.
- Five labelled lines at most. A case that cannot be stated that briefly is not
  understood well enough to act on.

The header is exactly four content lines — identity, the call, price and fair
value, the two scores — and TLDR carries all four.

### At QUICK, COMPARE and TLDR depth

None of the three runs the nine-category scorecard, so the third line cannot
carry an Investment Score — it is defined as the scorecard's sum — and
printing one would be inventing a number. Instead the third line names the
gap. COMPARE runs light scenarios, so it keeps the expected return on the
second line; QUICK and TLDR do not, since expected return is defined as the
probability-weighted result across scenarios neither builds, and their second
line ends at the upside range instead:

```
SEK 356.00 now -> fair value 420-470 (base, multiples vs 10y own history) · +18% to +32%
Investment Score n/a — no scorecard at this depth · Data Confidence 61/100
```

The fair-value range itself comes from multiples against the company's own
history, never peers or a DCF, and the line must name that basis rather than
leaving the reader to assume one.

## 6. Structure of a STANDARD or DEEP analysis

Twelve sections. **DEEP deepens these sections; it never adds new ones.** Omit
one only when it genuinely does not apply, and say so rather than dropping it
silently.

| # | Section | Carries | Form |
|---|---|---|---|
| 1 | **Verdict** | the call, in ten seconds | monospace header + labelled prose |
| 2 | **Snapshot** | identity, market cap, headline multiples | one table |
| 3 | **Business and moat** | what it does, why it persists, Moat Score | prose |
| 4 | **Financials** | quality, growth, cash conversion, balance sheet | table + sparklines |
| 5 | **Owners and management** | capital allocation, insiders, short interest, ownership | prose + small table |
| 6 | **Valuation** | multiples vs history and peers; DEEP adds DCF, reverse DCF, sensitivities, industry benchmark | table + range marker |
| 7 | **Scenarios** | bear, base, bull with their drivers | table |
| 8 | **Bear case and red flags** | the steelman, the red-flag screen | prose |
| 9 | **Scorecard** | nine categories | table with a bar column |
| 10 | **Thesis and triggers** | the thesis and **every** invalidation trigger | prose + one trigger table |
| 11 | **Evidence** | data confidence, verification, sources | grouped monospace block |
| 12 | **Decision record** | the fixed-shape record | monospace |

**One number, one home — with three named exceptions.** The recommendation, the
two scores and the triggers each live in exactly one canonical place. Sections
that need them reference that place rather than restating them. Price is not
single-homed: it is carried by the verdict and the decision record as a
deliberate checksum (below), and the Evidence block's `IDENTITY` and `PRICE`
lines carry it again as context for the reader, not as a third figure to
reconcile against the other two. Any copy of price beyond those three, or any
copy of the recommendation, the scores or the triggers beyond their own named
exception below, is a defect.

The single deliberate exception for the recommendation and the two scores: the
**verdict** (section 1, written for a human) and the **decision record**
(section 12, a fixed shape that can be compared across companies) carry the
same figures. **They must be numerically identical — any divergence is a
defect**, which turns the repetition into a checksum. Data Confidence carries
a third home besides those two: the **Evidence block**'s header (section 11)
states it again for the reader who arrives there directly. All three copies of
Data Confidence must agree; Investment Score keeps only the first two.

**Do not dump the raw data into the body.** The engine collects far more than
belongs in a readable analysis. The body carries what matters, what is
verified, what is uncertain, what the market prices in, what could go wrong,
and what would change the call. Full provenance lives in Evidence.

Length is not evidence of rigour. A reader who cannot find the conclusion has
been given a worse product, however complete it is.

### Charts

Text only. Unicode blocks and aligned columns, **nothing wider than 88
characters**, bars at most 40 cells, and every sparkline labelled with real
numbers at both ends — bare block characters are shape, not data.

Where a chart is positional, a marker must sit within one cell of its true
position. **No label may sit between the track's end caps** — a label there
occupies cells and pushes every marker after it out of true. Endpoint values
and a trailing legend may share the scale line itself, since they sit outside
the end caps and occupy no track cells — both range-marker charts do this. On
the scenario ladder, the legend sits beneath instead. If a chart cannot be
both accurate and under 88 characters, the chart is wrong — never the width
limit.

Four chart forms earn their place, and only these:

```
Revenue      99 ▃▅▇█▇▆ 120  SEK bn   FY2020->FY2025 · +3.9%/yr
EBIT margin  12.1 ▂▄▆▇█ 16.9   %     FY2020->FY2024 · no FY2025 EBIT disclosed
```

```
P/E vs own 10-year range
  8.9 ├──────────────────●────────────┤ 22.4     now 16.8 · median 14.2 · 64th pctile
```

```
  310 ──────────●─────────────├══════════┤──────────────── 540
  bear 310 · now 356 · base 420-470 · bull 540
```

The fourth is a bar column inside the scorecard table. Nothing else: no chart for a
single number, no ownership chart over a register that is explicitly a floor
rather than a total, no peer bars over a set that reports itself as low
confidence, and nothing at all in the verdict or Evidence blocks.

## 7. Output format

Deliver the analysis as **text in the conversation** by default. Do not build an
HTML artifact, charts, or a rendered report unless the user asks for one —
rendering and verifying a visual report can cost more time than the analysis
itself, and it is not what most questions need.

Build an artifact only when the user asks for a report, a document, a deck, a
one-pager or something to share.

## 8. Research process

On the first analysis of a session, skim `references/worked-example.md` — it
calibrates how densely to tag and what the closing block looks like.

Run the phases your chosen depth includes, in order. Load the reference file for a phase when you reach it,
not before.

### Phase 0 — Resolve identity. Non-skippable, every depth.
→ `references/source-registry.md`

Do not begin analysis on an ambiguous name. "Volvo" is **AB Volvo** *or*
**Volvo Car AB** — two listed companies with separate filings, separate share
structures and separate registers. "Atlas" could be Atlas Copco A, Atlas Copco B
or something else entirely.

Establish and state, before any figure is fetched:

```
legal_name · ticker · ISIN · LEI · organisationsnummer
exchange (MIC) · market segment · share classes
quote currency · REPORTING currency · fiscal year end
```

Reporting currency does not follow the listing venue — Evolution is quoted in
SEK and reports in EUR. Fiscal year is not necessarily the calendar year — H&M
runs December to November, and Sectra, Addtech, Lagercrantz, Clas Ohlson and
Systemair are all non-calendar.

**If confidence is low, stop and ask.** Analysing the wrong entity quickly is
worse than analysing the right one slowly.

`scripts/company_resolve.py "NAME"` does this and refuses when the name is
ambiguous — "Volvo" returns both AB Volvo and Volvo Car with their identifiers
and resolves neither. `scripts/venues_se.py "NAME"` then states which venue the
issuer is on and, critically, whether ESEF applies at all.

### Phase 1 — Establish the current picture
Price with timestamp, shares outstanding, market cap, enterprise value.
`scripts/quote.py TICKER` gives price, previous close, 52-week position and a
two-source cross-check.

**Shares outstanding.** US → the latest filing cover page. Nordics →
`scripts/nordic_shares.py "NAME"`, which reads the exchange's own reference data
and sums **every listed class**. Never take a share count from a quote site, and
never count only the liquid class — most Swedish large caps have two, and using
one understates market cap enough to make the stock look cheap. Heed the
script's warning about unlisted classes.

**If `quote.py` is unavailable or Yahoo is unreachable** — Yahoo is blocked from
the Claude app's container, confirmed 2026-08-31 — work down this list and
**stop at the first success. Never try more than three sources for a price.**

| Order | Source | Notes |
|---|---|---|
| 1 | `scripts/quote.py` | Best: two-source cross-check and staleness note |
| 2 | `api.nasdaq.com/api/quote/<SYM>/info?assetclass=stocks` | US only, JSON |
| 3 | `borskollen.se/aktie/<slug>` or `allaaktier.se/<slug>` | Nordic, HTML, reachable from the app |
| 4 | `aktiespararna.se/bolag/<slug>` | Nordic, HTML |

Confirmed **unreachable** from the app container — do not spend turns on them:
`query1.finance.yahoo.com`, `stooq.com`, `marketscreener.com`, `morningstar.com`,
`privataaffarer.se`.

A price is one number. If three sources have failed, write
`Current Price: DATA NOT AVAILABLE` with the reason and continue the analysis —
do not open a browser and hunt. Everything else in the report is still useful,
and the user can supply the price in one line.

**Do not guess IR URLs.** Guessing `/investor-relations/`, `/the-share/` and
similar burns turns on 404s. Fetch the company's home page and follow the
investor link, or search for it once.

### Phase 2 — Gather primary documents
`scripts/ir_discovery.py "NAME"` locates the issuer's own IR site and verifies
it resolves, rather than guessing URL patterns. **The company is the primary
source; MFN and Cision are distribution.** Where both carry the same document,
cite the company.

Latest annual report, latest interim report, earnings release, investor
presentation, guidance, transcript, and a sweep of material news since the last
report.

- **US**: `scripts/sec_fundamentals.py` plus the EDGAR filing index. Recent
  8-Ks are the news sweep; earnings releases arrive as 8-K exhibits.
- **Nordics / France**: `scripts/esef_fundamentals.py` for the annual figures,
  `scripts/mfn_news.py SLUG --reports` for report PDFs, and the same feed
  without `--reports` for the news sweep.
- **Germany**: IR page plus EQS/DGAP — see `references/europe.md`.

Transcripts have no free structured source. Check the company's IR page for a
webcast replay or transcript PDF first; if none exists, write
`DATA NOT AVAILABLE` for transcript-derived points and rely on the earnings
release and presentation instead. Never substitute a secondary summary.

### Phase 3 — Fundamental analysis
→ `references/fundamentals.md`, and `references/red-flags-and-smallcap.md` Part 1
All metrics in the user's brief, plus the quality-of-earnings check that
compares reported profit against actual cash generation.

### Phase 4 — Competitive advantage
→ `references/moat-growth-management.md` (moat section)
Produces a justified **Moat Score 0–10**.

### Phase 5 — Growth analysis
→ `references/moat-growth-management.md` (growth section)
TAM/SAM, share trend, and what is actually driving growth — volume, price, mix,
acquisition or geography.

### Phase 6 — Management and capital allocation
→ `references/moat-growth-management.md` (management section)
Insider ownership and trading, guidance accuracy, M&A record, buyback
discipline, SBC.

`scripts/guidance_track.py "NAME" --targets` extracts the company's standing
financial targets from its own IR pages with the source sentence attached, and
`--history` compares them against what was delivered. Everything it returns is
`SINGLE SOURCE — MANAGEMENT GUIDANCE`.

`scripts/corporate_actions.py "NAME"` classifies recent issues, buybacks and
splits; `--shares` returns the share-count disclosure log, which is the
authoritative dilution record.

Insiders: US → Form 4 on EDGAR. Sweden →
`scripts/insider_se.py --issuer "NAME"`. **Read the classification, not the
total** — the register mixes discretionary open-market trades with option
exercises and sell-to-cover. Evolution's raw twelve-month net is +293 MSEK
buying; the discretionary signal is −87 MSEK selling.

**Swedish ownership** — `scripts/ownership_se.py --isin <ISIN>` gives domestic
institutional holders and, more usefully, which of them hold a high-conviction
weight. It is a floor, not the full register.

**Swedish short interest** — run `scripts/short_se.py "NAME"` on every Swedish
company and report the result, including when the company is absent from the
register. A disclosed short position is a named professional betting against the
thesis, and it belongs in the devil's advocate section rather than buried here.
Norway's net-short register sits with Finanstilsynet under the EU Short Selling
Regulation, not Oslo Børs NewsWeb, which is the disclosure feed — see
`references/europe.md` "Short-selling registers" for NO/DK/FI. Denmark →
Finanstilsynet. Finland → Finanssivalvonta. France → AMF. Germany → BaFin
Directors' Dealings. Insider *ownership percentage* is separate from
transactions — see `references/sweden.md` and `references/europe.md`.

### Phase 7 — Valuation and scenarios
→ `references/valuation.md`

`scripts/macro_se.py --dcf-inputs` supplies the risk-free rate, policy rate and
FX as dated facts, separated in its own output from the assumptions you must
defend. `scripts/peers_se.py "NAME"` builds a peer set scored on business
archetype rather than ICB sector, and says `PEER SET LOW CONFIDENCE` when the
result does not deserve trust. `scripts/macro_se.py --industry <term>` gives the
official SCB sector benchmark with its classification confidence.
Multiples versus history and peers, DCF, reverse DCF, and bear/base/bull
scenarios with expected value.

### Phase 8 — Devil's advocate, scoring, recommendation
→ `references/bear-case-and-scoring.md`, plus the red-flag screen in
`references/red-flags-and-smallcap.md`
**Mandatory.** Argue actively against the case before scoring it. Run the
red-flag screen and report what it found — including when it found nothing.

For a Small Cap, First North, Spotlight or NGM issuer, Part 2 of that file
applies: a different posture on liquidity, dilution, runway, governance and
valuation, and a conviction ceiling. Applying large-cap methodology to a
microcap produces false precision.

### Phase 9 — Verification
→ `references/verification.md`
**Mandatory at STANDARD and DEEP.** For any ESEF filer, `scripts/verify_filing.py
--lei <LEI> --slug <mfn-slug>` runs the checks and prints the block. Cross-check the material figures against a
second independent path, confirm the statements tie, confirm the share count
covers all classes, and publish the Evidence block. Name what could not
be verified rather than letting single-sourced figures pass as equally solid.

A `FACT` tag records where a number came from; it does not establish that the
number is right. This phase is what separates sourced research from verified
research.

For portfolio questions → `references/portfolio.md`.
For multi-company comparison, run COMPARE depth per company (see §4) and rank
on expected return per unit of downside.

## 9. Output contract — the decision record

Section 12 closes every STANDARD and DEEP analysis in exactly this shape. It is
the machine-comparable record: same fields, same order, every company.

```
DECISION — SAND.ST · Sandvik AB (556000-3468) · STANDARD · 2026-08-31

RECOMMENDATION    BUY — MEDIUM CONVICTION
Price             SEK 356.00   (2026-08-31 07:14 UTC, Nasdaq · reports in SEK)
Fair value        SEK 420-470 base (55%) · 310 bear (25%) · 540 bull (20%)

                  310 ──────────●─────────────├══════════┤──────────────── 540

Expected return   +20.9%   (probability-weighted across scenarios)
Margin of safety  +15% to base-low · +24% to base-high
Investment Score  74/100        Data Confidence  61/100

Rests on          1. Mining capex holds through 2027           ASSUMPTION
                  2. EBIT margin >= 15% through the cycle      ASSUMPTION
                  3. Multiple reverts to 10y median, not peak  ASSUMPTION

Triggers          see Bevakning in the closing block — 5 rows
```

*This is analysis, not investment advice.*

Rules:

- **Numerically identical to the verdict.** Same recommendation, same
  conviction, same fair-value range, same expected return, same two scores. A
  divergence is a defect. The verdict's upside (fair value over price, minus 1)
  and this record's margin of safety (1 minus price over fair value) are
  different quantities that will not match to the digit — that is not a
  divergence, provided each is labelled for what it is.
- **Data Confidence is never omitted.** It sits beside the Investment Score,
  not in a footnote.
- **No point estimate on the fair-value line.** A range, always.
- **`Rests on` are assumptions, not triggers.** Name the two or three the call
  actually depends on, each tagged. Triggers live in the closing block's
  Bevakning table and are referenced here, never restated.
- The recommendation follows from valuation, expected return, downside, margin
  of safety, business quality, balance sheet and data confidence together —
  never from the Investment Score alone. Where your judgement departs from what
  the numbers suggest, say so and give the reason.

### The signal line — every depth closes with it

Whatever came before, the last thing a run prints is the call in a form that
survives skimming. At STANDARD and DEEP it follows the decision record; at
TLDR, QUICK, COMPARE and PORTFOLIO it is the close on its own.

```
## Slutsats

🟢 **NIBE B — BUY (köp)** · MEDIUM CONVICTION
Marginalen har vänt och värderingen ligger under bolagets egen tioårshistorik.

Viktigast: Data Confidence 61/100. EV/EBIT går inte att beräkna eftersom
delårsrapporterna inte redovisar EBIT. Detta är en riktning, inte ett facit.
```

At STANDARD and DEEP on a single company the signal line opens a fuller close.
The order is fixed, because it is the order a reader needs it in:

```
## Slutsats

Obducat har äntligen fått upp farten operativt: H1 visar +87% intäkter och en
orderstock på 147 MSEK. Problemet är att bolaget fortfarande inte är lönsamt,
har negativt kassaflöde och behöver mer kapital för Portugal-expansionen.

Aktien är samtidigt högt värderad och marknaden räknar med starkare tillväxt än
bolagets eget mål. I november kan omkring 116 miljoner nya aktier emitteras till
högst 0,18 kr, vilket ger både utspädning och säljtryck.

🔴 **Obducat B — SELL (sälj/minska)** · LOW CONVICTION
SEK 0,546 nu · rimligt värde 0,38-0,48 · Score 41/100 · Data Confidence 38/100

**Talar för**
🟢 Orderstock 147 MSEK, klart över historiken
🟢 Intäkter +87% i H1; Q2 nära operativt break-even
🟡 Foundry-avtal på minst 115 MSEK — skalbarhet visad, men ett enda avtal

**Talar emot**
🔴 Kassaflöde -19,3 MSEK i H1; 9-10 månaders kassa på nuvarande burn
🔴 123 MSEK till Portugal är ofinansierat
🟠 +260% fler aktier på sex månader

**Bevakning** — trigger-tabellen, printed here and nowhere else

| # | Utlösare | Tröskel | Följd |
|---|---|---|---|
| 1 | Q3-intäkter | < 30 MSEK | tesen bryts -> EXIT |
| 2 | Nyemission | > 75 MSEK under 0,45 kr | bear-caset |
| 3 | Kurs | > 0,80 kr | bull-värdering -> exit |

**Äger du den redan:** trimma nu, låt Q3 och novemberoptionerna avgöra resten.

**Horisont:** till Q3 den 13 november. Då avgörs om intäktstakten håller, och
novemberoptionerna visar om utspädningen blir så stor som marknaden fruktar.

Viktigast: Data Confidence 38/100. Bolaget är en MTF-notering utan ESEF, så
siffrorna är lästa ur delårsrapportens prosa. Detta är en riktning, inte ett facit.
```

Four rules govern the added blocks:

- **`Talar för` / `Talar emot` compress evidence already presented.** No claim may
  appear there that is not established earlier in the analysis, exactly as the
  scorecard's justifications may not introduce new claims. Three to five items a
  side. If one side is empty, say so — an analysis with nothing against it has
  not looked.
- **Every item in those two lists carries a mark, and the mark grades the
  item.** A bare 🟢 under `Talar för` would only repeat the heading, so the
  scale is what makes it informative: 🟢 strong and established · 🟡 real but
  qualified, single-sourced or unproven · 🟠 a concern short of a threat ·
  🔴 material. A qualified positive under `Talar för` is 🟡, not 🟢 — that is
  the whole point of marking each line rather than the section.
- **`Äger du den redan` is mandatory on SELL and STRONG SELL**, and belongs
  wherever the reader plausibly holds the security. A call on a security is not
  an instruction to a holder: SELL says the price exceeds the value, not "sell
  the whole position before close". Name what to do now and what to wait for.
  This is the one line that turns an opinion into something actionable, and it
  is the bridge between `/analyze` and `/portfolio`.
- **A `Horisont` date sourced from `horizon.py` carries its provenance inline.**
  Write `[SINGLE SOURCE - tier 4, Avanza; overifierat mot bolaget]` on the same
  line as the date. The script prints this automatically in its own output, but
  the analyst composes the Swedish `Horisont` line by hand — and a disclosure
  that survives only in the tool's output is a disclosure that gets dropped in
  translation. A date whose tier is not recorded cannot be audited later.
- **`Horisont` is mandatory at STANDARD and DEEP**, directly beneath `Äger du
  den redan` (or in its place when that line does not apply). It is derived
  from the trigger table already printed as `Bevakning` — not invented — and
  states the nearest dated event that would resolve or break the thesis: the
  next report, a financing deadline, an option window, a named catalyst.
  Express it as a date or a quarter plus what happens then, never as a vague
  duration ("6-12 months" says nothing about what ends the position).
  `scripts/horizon.py` sources the next scheduled report date for a
  Nordic-listed company where one can be found (see `references/portfolio.md`
  for what it checked and why most free sources do not qualify). Where no
  dated catalyst could be sourced, say so plainly —
  `Horisont: DATA NOT AVAILABLE — ingen daterad katalysator kunde hittas` —
  and still name what would settle the case, since a date being unknown does
  not mean the catalyst is.

One colour vocabulary, used by every command:

| | One company | One holding in a portfolio |
|---|---|---|
| 🟢 | STRONG BUY, BUY | ADD |
| 🟡 | HOLD | HOLD |
| 🟠 | SELL | TRIM |
| 🔴 | STRONG SELL | EXIT |
| ⚪ | no call — the evidence does not support one | flagged, no action yet |

Rules:

- **The colour is a function of the call and carries nothing else.** It never
  replaces the word, and two calls that share a colour are distinguished by the
  word alone. Emoji appear here and in the portfolio action list; nowhere else
  in the output, and never in this plugin's own documentation.
- **⚪ is mandatory when it applies.** A run that could not reach a call says so
  in the same place a call would have gone. Omitting the line, or downgrading
  the uncertainty to HOLD, is the failure the whole framework exists to
  prevent — HOLD is a judgement, ⚪ is the absence of one.
- **A multi-name run groups instead of repeating**, one line, ordered by
  conviction in the call:
  `🟢 KÖP: NIBE · 🟡 HOLD: Axfood, Betsson · 🟠 SÄLJ: Kambi · ⚪ FLAGGAD: Sagax D`
- **The token stays English, the gloss is Swedish** — `BUY (köp)`, per §13. The
  parenthetical is for the reader; the token is what makes runs comparable.
- **One sentence of plain language under the flag.** No tags, no jargon a
  non-specialist would have to look up. Someone who reads only the signal line
  must come away with the right instruction.
- **`Viktigast` names the real limitations of this run**, never a boilerplate
  disclaimer: the Data Confidence score and what it rests on, anything material
  that could not be checked, and the one thing most likely to change the
  conclusion. A caveat that would be true of every run informs about none of
  them.
- **Numerically identical to the verdict block and the decision record** on the
  call, the conviction and Data Confidence. It is the third face of the same
  checksum, not a fourth opinion.


## 10. Working without the scripts

The scripts are an accelerator for Claude Code, not a dependency. In the Claude
app, or when Bash is unavailable, fetch the same endpoints directly — every URL
is listed in `references/data-sources.md`. The analysis and its standards are
identical; only the retrieval mechanism changes.

## 11. Reference files

| File | Covers |
|---|---|
| `references/worked-example.md` | **Read first** — calibrates tagging density and output format |
| `references/source-registry.md` | Which source is authoritative for which data type, and the tier ladder |
| `references/data-quality.md` | The datapoint model, conflict resolution, data confidence, conviction |
| `references/red-flags-and-smallcap.md` | The 20-item red-flag screen, and small-cap / MTF posture |
| `references/data-sources.md` | Every endpoint, what is free, what needs credentials |
| `references/verification.md` | Source-authority ladder, cross-checks, the Evidence block |
| `references/fundamentals.md` | Metric definitions, formulas, quality of earnings |
| `references/moat-growth-management.md` | Moat scoring, growth drivers, management |
| `references/valuation.md` | Multiples, DCF, reverse DCF, scenarios |
| `references/bear-case-and-scoring.md` | Devil's advocate, scorecard, recommendation |
| `references/portfolio.md` | Position sizing, concentration, factor and downside risk |
| `references/sweden.md` | Nasdaq Stockholm source chain and Swedish reporting conventions |
| `references/europe.md` | Nordics, Germany and France — routing, ESEF, currency traps |

## 12. Scripts

| Script | Purpose |
|---|---|
| `scripts/sec_fundamentals.py` | US fundamentals from SEC XBRL, with per-figure provenance |
| `scripts/esef_fundamentals.py` | Nordic and French fundamentals from ESEF Inline XBRL |
| `scripts/verify_filing.py` | Restatement check, internal ties, release cross-check |
| `scripts/short_se.py` | Swedish disclosed short interest from Finansinspektionen |
| `scripts/nordic_shares.py` | Shares outstanding per class and market cap, from Nasdaq |
| `scripts/ownership_se.py` | Swedish institutional ownership from FI fund holdings |
| `scripts/cision_news.py` | Releases for Swedish issuers that publish via Cision, not MFN |
| `scripts/company_resolve.py` | **Phase 0.** Canonical identity; refuses to resolve an ambiguous name |
| `scripts/venues_se.py` | Which Swedish venue an issuer is on, and the source chain that follows |
| `scripts/ir_discovery.py` | The issuer's own IR site, verified — reports, targets, calendar |
| `scripts/corporate_actions.py` | Splits, issues, buybacks, and the share-count disclosure log |
| `scripts/guidance_track.py` | Standing financial targets and the delivery record against them |
| `scripts/peers_se.py` | Scored peer set by business archetype, not ICB sector |
| `scripts/macro_se.py` | DCF inputs from Riksbanken; official SCB industry benchmarks |
| `scripts/quote.py` | Price with as-of timestamp, staleness note, two-source cross-check |
| `scripts/insider_se.py` | Swedish PDMR insider transactions from Finansinspektionen |
| `scripts/mfn_news.py` | Swedish regulatory releases and report PDFs from MFN.se |
| `scripts/finfact.py` | Provenance and temporal-validity core the other scripts import — not run directly (`--selftest` only) |
| `scripts/share_semantics.py` | Resolves which of six competing "shares outstanding" figures applies, and flags unlisted classes |
| `scripts/ttm_engine.py` | Assembles trailing twelve months from interim reports, since ESEF carries annual figures only |
| `scripts/valuation_gate.py` | Refuses to print a multiple when price and earnings do not share a compatible period |
| `scripts/earnings_quality.py` | Cash-conversion and accrual ratios that separate reported profit from actual cash |
| `scripts/thesis_ledger.py` | Persistent, falsifiable thesis keyed on LEI, re-testable against a later filing |
| `scripts/portfolio_store.py` | Store and manage a portfolio at `~/.investment-analyst/portfolio/<name>.json`; accepts pasted Avanza/Nordnet text or typed positions; resolves identity and refuses ambiguous names |
| `scripts/portfolio_review.py` | The three-layer triage: layer 1 breakers via thesis_ledger, layer 2 alerts, layer 3 STANDARD depth on flagged holdings; returns EXIT, TRIM or HOLD per position, or leaves the action open for depth review |
| `scripts/portfolio_metrics.py` | Portfolio-level analysis: Herfindahl concentration, effective position count, sector and geographic exposure, correlation and hidden overlap, downside risk, Data Confidence, cash drag |

`sec_fundamentals.py` requires `SEC_USER_AGENT` to be set (SEC fair-access
policy). If it is unset the script says so and exits; tell the user the exact
export line rather than working around it.

Read the warnings the scripts print — they are not decoration. A currency
warning, a stock-split warning, a truncation warning or a multi-tag warning each
means a specific number in the table cannot be used the way it looks.

## 13. Output language

Answer in the language the user wrote in. For a Swedish question, write the
analysis in Swedish but keep the standard financial terms and the final
decision record in English — `RECOMMENDATION    BUY — MEDIUM CONVICTION`,
`Margin of safety`,
`FACT`/`ESTIMATE`/`ASSUMPTION`/`OPINION` — so the output stays comparable across
companies and matches how the terms appear in the sources.

## 14. The Swedish default run

When the user asks for a Swedish company by name, this is the order. Each step
either produces a dated, sourced figure or a stated gap — never a silent one.

| # | Step | Tool |
|---|---|---|
| 1 | Resolve legal entity, ISIN, LEI, orgnr, share classes, currencies, fiscal year | `company_resolve.py` |
| 2 | Identify venue and whether ESEF applies | `venues_se.py` |
| 3 | Locate the issuer's own IR site | `ir_discovery.py` |
| 4 | Price with timestamp; shares outstanding across all classes | `quote.py`, `nordic_shares.py` |
| 5 | Annual financials | `esef_fundamentals.py`, or the report PDF on an MTF |
| 6 | Latest quarter and regulatory releases | `mfn_news.py` or `cision_news.py` |
| 7 | Corporate actions and the dilution log | `corporate_actions.py` |
| 8 | Insider activity, classified | `insider_se.py` |
| 9 | Short interest and its trend | `short_se.py` |
| 10 | Institutional ownership and its trend | `ownership_se.py` |
| 11 | Financial targets and the delivery record | `guidance_track.py` |
| 12 | Peer set | `peers_se.py` |
| 13 | DCF inputs and industry benchmark | `macro_se.py` |
| 14 | Verification: restatements, ties, cross-checks | `verify_filing.py` |
| 15 | Red-flag screen | `references/red-flags-and-smallcap.md` |
| 16 | Valuation, reverse DCF, scenarios, scorecard, recommendation | — |

QUICK runs 1, 2, 4, 5, 8, 9 and the recommendation. COMPARE runs the same
steps plus the moat assessment and a light bear/base/bull scenario build —
neither has its own numbered step in this table, since this list is the
Swedish data-gathering sequence, not the analysis phases. STANDARD adds 3, 6,
10, 12, 14, 15, and step 16 without the reverse DCF. DEEP adds 7, 11, 13 and
the reverse DCF.

Two things are never skipped at any depth: **step 1**, because analysing the
wrong entity fast is worse than analysing the right one slowly, and **the honest
statement of what could not be obtained**.
