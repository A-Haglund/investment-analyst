# Briefing: "Investment Analyst" v3.0.0 — a Claude Code / Claude Cowork plugin

I am going to describe a software system I have built. At the end I will ask you
for improvement suggestions. Please read the whole thing first, including the
constraints and the known-gaps sections, because they rule out most of the
obvious advice.

---

## 1. What it is

A plugin for Claude (Anthropic's assistant) that performs fundamental equity
research and ends in a BUY / HOLD / SELL recommendation with a fair value per
share. It is optimised for **Swedish listed equities**, with secondary support
for Nordic (Norway, Denmark, Finland), German and French issuers. US equities
are deliberately out of scope — SEC EDGAR requires an identifying contact
address on every request, and this system sends none.

It is not a trading system, a screener product or a data vendor. It is a
structured research process that a language model follows, backed by
38 Python scripts that fetch and verify data from official sources —
and, increasingly, that enforce discipline in code rather than only asking the
model to remember it: a shared provenance/temporal-validity core
(`finfact.py`), a trailing-twelve-months assembler (`ttm_engine.py`), a hard
gate that refuses a multiple built on an incompatible price/earnings period
(`valuation_gate.py`), a persistent falsifiable thesis ledger
(`thesis_ledger.py`), and — new in v3.0.0 — a validated decision record that
refuses arithmetic disagreeing with its own inputs and enforces the conviction
ceiling (`decision_record.py`).

Scale: 94 files, ~62,000 lines. Roughly 42,300 lines of Python across 38
scripts, ~5,400 lines of Markdown instruction files the language model reads
(`SKILL.md` plus 13 reference files), ~700 lines of command definitions, and a
~13,600-line regression suite of roughly 850 test functions across 32 files.

**Design philosophy, in the system's own words:** the goal is not to predict the
future accurately. The goal is to make bad investment decisions harder.

**What changed in v3.0.0**, since the rest of this brief describes the system as
it now stands: the decision record became a validated object the model emits
and a script renders, rather than prose the model typed three times; the
conviction caps moved from prose into enforced code; `/analyze` now writes the
thesis and stores the decision, which is what makes the portfolio review's
breaker check able to fire at all; and a shared core (`numparse.py`,
`finmath.py`, `http_util.py`, `market_universe.py`, `_bootstrap.py`) replaced
four number parsers, two CAGR implementations, fifteen hand-rolled HTTP
fetchers and a screen pipeline that existed in two divergent copies. See
`MIGRATION.md` for the three breaking changes.

---

## 2. The absolute constraint — read this before suggesting anything

The system uses **only free, keyless, legally accessible data**. This is not a
budget preference; it is a hard design constraint.

Specifically excluded, permanently:
- anything requiring an API key
- anything requiring payment, subscription, trial or commercial licence
- anything requiring registration, an account, or a signed agreement
- scraping that violates a site's terms of service
- unofficial or abandoned third-party APIs as *core* dependencies

So: **do not suggest Bloomberg, Refinitiv/LSEG, FactSet, S&P Capital IQ,
Morningstar, Börsdata, Alpha Vantage, Polygon, Financial Modeling Prep, IEX,
Quandl, or any "free tier with an API key" service.** Those have all been
evaluated and rejected. Suggestions that depend on them are not useful to me.

Also excluded for a specific reason:
- **Bolagsverket** (Swedish companies registry) — has no keyless access at all.
  Share capital, board, CEO and auditor data sit behind a paid, signed
  agreement.
- **FRED** — requires an API key.
- **Holdings / Modular Finance, Euroclear, Nordnet** — no public API.

Python **standard library only**. No pip installs. Runs on Windows with a
cp1252 console, so every script reconfigures stdout to UTF-8.

---

## 3. Architecture

Two halves.

### 3a. The instruction layer (Markdown, read by the language model)

| File | Lines | Purpose |
|---|---|---|
| `SKILL.md` | 1247 | The spine: evidence rules, source hierarchy, market routing, depth selection, the verdict block, the seven-section output contract, the decision-record contract, the research phases |
| `references/red-flags-and-smallcap.md` | 733 | A 20-item red-flag screen with numeric thresholds, plus a distinct posture for small caps and MTF venues |
| `references/worked-example.md` | 462 | Calibrates output format and tagging density; carries the decision record as JSON and as its rendered block |
| `references/sweden.md` | 434 | Swedish source chain, market segments, IFRS/K3 terminology, reporting conventions |
| `references/valuation.md` | 410 | Multiples, DCF, reverse DCF, scenarios, the enterprise-to-equity bridge, the financials/real-estate carve-out, price-series adjustment semantics |
| `references/data-sources.md` | 393 | Every endpoint, its quirks, and its limits |
| `references/portfolio.md` | 335 | Position sizing, concentration, exposure, ranking, the three-layer review |
| `references/data-quality.md` | 270 | The datapoint metadata model, conflict resolution, data confidence scoring, and the single home for the conviction ladder and its enforced caps |
| `references/verification.md` | 238 | Cross-checks and the mandatory Evidence block |
| `references/fundamentals.md` | 208 | Metric definitions, formulas, quality-of-earnings tests, lease treatment |
| `references/bear-case-and-scoring.md` | 195 | Devil's advocate section, the trigger table (also the thesis ledger's input contract), 9-category scorecard, recommendation logic |
| `references/europe.md` | 188 | Nordics / Germany / France routing and currency traps |
| `references/moat-growth-management.md` | 167 | Moat scoring 0–10, growth decomposition, management assessment |
| `references/source-registry.md` | 140 | Which source is *authoritative* for which data type |

The model loads `SKILL.md` always and pulls a reference file only when it
reaches the phase that needs it (progressive disclosure, to control context
cost).

### 3b. The data layer (38 Python scripts, stdlib only)

| Script | Lines | What it does |
|---|---|---|
| `thesis_ledger.py` | 3490 | Both persisted objects, keyed on LEI/ISIN: the price-free falsifiable thesis with numeric invalidation breakers, re-testable against a later filing (`--as-of` re-plays a past evaluation without overwriting the live status), and the price-stamped decision record (`--decide`). Append-only; nothing is rewritten or dropped |
| `peers_se.py` | 3207 | Scores a peer set on eight dimensions, business archetype among them, not sector code |
| `guidance_track.py` | 3016 | Extracts a company's standing financial targets from its own IR pages, persists each statement with the vintage it was made in, keeps targets separate from period guidance and delivered outcome, and scores management execution |
| `ttm_engine.py` | 2322 | Assembles trailing twelve months from the latest annual plus interim reports, since ESEF carries annual figures only |
| `screen_digest.py` | 1725 | The unattended daily fell-and-might-be-cheap digest, wall-clock bounded, run by the scheduled job rather than by hand |
| `corporate_actions.py` | 2116 | Splits, rights issues, directed issues, buybacks; the share-count disclosure log; the measured split-adjustment evidence for the price series |
| `macro_se.py` | 1651 | Riksbank rates/FX/yield curve; SCB official industry margin benchmarks by SNI; ECB/Eurostat |
| `company_resolve.py` | 1550 | Canonical identity: legal name, ISIN, LEI, org number, MIC, share classes, quote vs reporting currency, fiscal year end — refuses on an ambiguous name |
| `ir_discovery.py` | 1483 | Locates and verifies the issuer's own Investor Relations site and report archive, rather than guessing URLs |
| `valuation_gate.py` | 1454 | Refuses to print a multiple when price and earnings do not share a compatible period — eight checks, all-or-nothing |
| `portfolio_store.py` | 1367 | Stores a portfolio from pasted Avanza/Nordnet text or typed positions, resolving identity and refusing ambiguous names |
| `venues_se.py` | 1335 | Routes an issuer to its listing venue and states whether ESEF applies |
| `portfolio_metrics.py` | 1261 | Herfindahl concentration, effective position count, sector and geographic exposure, hidden overlap, downside risk, cash drag |
| `calibration.py` | 1218 | Forward-only outcome and calibration reporting on stored decisions at 3, 6 and 12 months. Not a backtester, and prints `INSUFFICIENT SAMPLE` rather than a hit rate below its minimum |
| `research_delta.py` | 1164 | Diffs a new decision record against the stored one and prints only what moved |
| `insider_se.py` | 1008 | Swedish insider (PDMR) transactions, classified DISCRETIONARY / MECHANICAL / DERIVATIVE |
| `share_semantics.py` | 1004 | Resolves which of six competing "shares outstanding" figures applies, and computes market cap per class rather than a blended price times a total |
| `short_se.py` | 965 | Swedish disclosed short positions with holder-level trend |
| `earnings_quality.py` | 932 | Cash-conversion and accrual ratios that separate reported profit from actual cash |
| `portfolio_review.py` | 910 | The three-layer triage: breakers, cheap alerts, then STANDARD depth only on what those flagged |
| `screen_value.py` | 900 | The on-demand `/screen` deep-value screen: universe → multi-year history → value filter → liquidity floor → corporate actions → ESEF margins → rank, printing what each stage cut |
| `market_universe.py` | 798 | The shared universe/liquidity/returns layer both screens build on, extracted in v3.0.0 so it exists once rather than in two divergent copies |
| `ownership_se.py` | 760 | Swedish fund ownership per ISIN, quarterly, with concentration and trend |
| `decision_record.py` | 830 | The decision's schema, its arithmetic identities, the enforced conviction ceiling, the closed reason-code vocabulary, and the renderer for the fixed-shape block. Pure module: no network, no filesystem |
| `watchlist_store.py` | 685 | Issuers followed but not owned — no quantity, no cost basis, and it never values or scores |
| `mfn_news.py` | 647 | Nordic regulatory releases; extracts headline figures from release text |
| `horizon.py` | 543 | The next scheduled report date for a Nordic-listed issuer, with its source named |
| `nordic_shares.py` | 526 | Shares outstanding per share class, market cap, 10-year daily price history (back-adjusted for splits) |
| `numparse.py` | 518 | The one number parser: comma-versus-decimal, space grouping, the typographic minus. Replaces four |
| `finfact.py` | 453 | The shared provenance, temporal-validity and corroboration core (`FinancialFact`, `Verification`, `corroborate()`); not run directly except `--selftest` |
| `verify_filing.py` | 437 | Restatement detection, internal statement ties, release cross-check |
| `quote.py` | 417 | Current price with timestamp and staleness note; for a Nasdaq Nordic ticker, corroborated against Nasdaq's own venue reference data as CROSS-CHECKED, CONFLICT or `not checked` |
| `esef_fundamentals.py` | 404 | IFRS annual financials from ESEF Inline XBRL |
| `finmath.py` | 360 | Shared financial math: CAGR from elapsed days rather than a period count, with a currency check |
| `screen_metrics.py` | 269 | Drawdown, return windows and margin trends as pure functions |
| `http_util.py` | 266 | One fetcher: retry with backoff on 429 and 5xx, and a collision-free cache key |
| `cision_news.py` | 226 | Releases for Swedish issuers that distribute via Cision, not MFN |
| `_bootstrap.py` | 124 | The sibling-import idiom, written once and correctly (`SystemExit` does not inherit from `Exception`, and two earlier copies disagreed about it) |

---

## 4. Data sources actually used (all free, all keyless, all verified working)

### Regulatory and exchange (tier 1)
- **Finansinspektionen** (Swedish FSA) — four registers:
  - Insynsregistret: insider/PDMR transactions, T+1
  - Blankningsregistret: short positions, history to 2010
  - Fondinnehav: every Swedish UCITS fund's full holdings, quarterly
  - Prospektregistret
- **Nasdaq Nordic** (`api.nasdaq.com/api/nordic`) — shares outstanding per share
  class, market cap, segment, ICB sector, index membership, 10 years of daily
  OHLCV, exchange observation-status flags, and the CNS announcement feed
  (splits, issues, buybacks, the mandatory "total voting rights and capital"
  disclosure that is the authoritative dilution log)
- **ESEF / filings.xbrl.org** — IFRS annual reports as tagged Inline XBRL for
  EU/EEA regulated-market issuers
- **ESMA FIRDS** — ISIN ↔ LEI ↔ MIC ↔ CFI resolution, and the anchor for venue
  identity where a name search on a news wire is unreliable
- **Riksbanken SWEA** — Swedish policy rate, 10-year government bond, FX
- **SCB** (Statistics Sweden) — CPI, PPI, and official operating-margin
  benchmarks by industry (SNI) and company size class
- **GLEIF** — LEI registry
- **EU VIES** — official registered legal name from a Swedish org number
- **ECB / Eurostat** — euro-area rates and prices
- **The company's own IR website** — treated as the primary source, above
  distributors, located by following the home-page link rather than guessing
  URL patterns

### Distribution channels (tier 2)
- **MFN.se** — Nordic MAR-regulated releases with structured tags and report
  PDFs. Covers small caps and growth-market issuers well. A company feed is
  capped at roughly 30 recent items and ignores offset.
- **Cision** — covers the Swedish large caps MFN does not (Sandvik, Atlas
  Copco, Hexagon, AB Volvo)

### Venue coverage
Nasdaq Stockholm Large/Mid/Small Cap · Nasdaq First North · Spotlight Stock
Market · NGM Equity · NGM Nordic SME.

---

## 5. The analytical framework

### Output contract — one number, one home

Every analysis opens with a **verdict block**: a fenced four-line header
(identity and date; the recommendation with its conviction; price, fair-value
range and upside; Investment Score and Data Confidence side by side) followed
by bold-labelled prose outside the fence — Why, Risk, Priced in, Watch,
Unverified.

The output is in **two layers**. What the reader gets is **seven sections**,
not more: verdict; what the company is; why the price is where it is;
for-and-against; scenarios; what the call means in practice; the closing block
with the signal line, the trigger table, the horizon and `Viktigast`. Each
carries a word budget, and the budgets are the enforceable form of the
delivered-length cap — a global cap can only be checked once the draft is
already too long to fix. Everything else — the snapshot, the statements, the
moat scoring, owners and management, the full valuation build, the scorecard,
the Evidence block and the decision record — is **underlying material**:
produced in full, printed only when the reader asks for it. DEEP deepens the
seven sections; it never adds new ones.

Every invalidation condition lives in **one trigger table**, printed once in
the closing block — previously the same conditions were written in four
different formats across the document. Its rows are numeric and
filing-checkable, which is not a style rule: they are the input contract of
`thesis_ledger.py --breaker`, and on a BUY or SELL call they are stored as the
thesis's breakers.

The **Evidence block** carries the Data Confidence score, every material figure
grouped by verification status (`VERIFIED`, `CROSS-CHECKED`, `SINGLE SOURCE`,
`CONFLICT`, `STALE`, `INCOMPLETE`, `DATA NOT AVAILABLE`), with `SINGLE SOURCE`,
`CONFLICT`, `STALE` and `DATA NOT AVAILABLE` printed even when empty (as
`none`), and closes with a mandatory `TALLY` line — the structural defence
against a run where nothing was cross-checked reading as clean, since it must
print `0 of N verified`.

### The decision record — the v3.0.0 inversion

The **decision record** is a fixed machine-comparable shape, and the direction
of authority over it was inverted in v3.0.0. It used to be prose the model
typed: the verdict block, the signal line and the record were three copies of
the same numbers, called a checksum and cross-checked by nothing. Persisting a
fourth copy would have inherited zero enforcement while adding a new failure
mode — a stored record that outlives the prose explaining it, and lies about
it.

So the model now emits the record as JSON first and `decision_record.py`
renders the human block from it. One source, and the copies cannot diverge.
The module **refuses** a record rather than storing a wrong one:

- `expected_return = Σ wᵢ(FVᵢ/P − 1)` with the base at its range midpoint,
  `margin_of_safety = 1 − P/FV` to base-low and base-high, and `Σ wᵢ = 1` are
  recomputed, and a stated figure disagreeing by more than 0.15pp is refused.
  This removes the weakest link in the pipeline — arithmetic performed in prose
  — without building a valuation engine.
- A conviction above the enforced ceiling is refused.
- A reason code outside the closed vocabulary is refused; a code invented at
  the call site cannot be counted later.
- An identity with neither LEI nor ISIN is refused: a decision keyed on a
  display name cannot be matched to a later outcome, and "Volvo" is two
  companies.

What is deliberately **not** mechanical is the call itself. There is no
weighted composite score and there must never be one — the recommendation is
not a function of the investment score, and a score that decided the call would
trade interpretability for the appearance of rigour. Code enforces the
*constraints* on the judgement: hard gates, conviction caps and thesis
breakers, each recorded as a **reason code** so a past decision can be audited
rather than re-litigated.

Reason codes record checks that already ran — `valuation_gate.py`'s eight
refusals, `peers_se.py`'s suppressed rows, `thesis_ledger.py`'s breaker status,
`finfact.py`'s conflicts, `earnings_quality.py`'s bands, `venues_se.py`'s
venue routing. Nothing new is computed to justify a decision; the gap was that
each outcome was printed as prose and then lost, so months later nobody could
say why a multiple was missing or why conviction was LOW.

The record is stored by `thesis_ledger.py --decide`, append-only, alongside the
thesis — two objects with opposite properties, deliberately kept apart. **A
thesis is price-free and durable; a decision is price-stamped and superseded.**
The ledger holds no prices at all: a thesis that flips on a quote is a trade,
not a thesis.

**Investment Score and Data Confidence are never merged.** The first measures
how good the opportunity looks; the second measures how well the evidence
supports it. Both appear together on the verdict's third line and again in the
decision record; the Evidence block's header carries Data Confidence alone.
Those are the only homes for either number.

Charts are text-only, unicode blocks and aligned columns, nothing wider than 88
characters, limited to four forms: a labelled sparkline, a range marker against
own history, a bear/base/bull scenario ladder, and the bar column inside the
scorecard table. On a positional chart the scale line carries the positions
and the legend line carries the numbers, so a marker's position is never pushed
out of true by an inline label.

### Evidence discipline
Every material claim is tagged `FACT`, `ESTIMATE`, `ASSUMPTION` or `OPINION`.
Additional rules the model must follow:
- Never invent a number. Unsourceable ⇒ `DATA NOT AVAILABLE`.
- Never present a stale price as current; always print the as-of timestamp.
- Sourced ≠ verified. A source tag records origin, not correctness.
- Never resolve a source conflict silently.

### Datapoint model
Material figures carry: value, unit, currency, period, as-of date, retrieved-at,
published-at, source, source tier (1–4), primary/secondary, confidence, and a
verification status from: `VERIFIED`, `CROSS-CHECKED`, `SINGLE SOURCE`,
`CONFLICT`, `STALE`, `INCOMPLETE`, `DATA NOT AVAILABLE`. `finfact.py` is now the
one shared implementation of this model — a `FinancialFact` object and a
`corroborate()` function that every data-fetching script imports, rather than
each script re-implementing the same comparison logic slightly differently.

### Verification engine (automated)
- **Restatement detection** — each annual filing carries the prior-year
  comparative; it is compared against what the previous filing originally
  stated. A difference is a restatement and becomes an analytical finding.
- **Internal ties** — assets = liabilities + equity; revenue − cost of sales =
  gross profit; cash roll-forward including the IFRS FX-on-cash line.
- **Release cross-check** — tagged filing against the company's own release.
- A check only asserts failure when every term of its equation is present;
  otherwise it reports `INCOMPLETE`. A check that produces false alarms teaches
  the user to ignore it.

### The valuation-integrity gate

`valuation_gate.py` is a hard gate, not a suggestion: it refuses to print P/E,
EV/EBIT, EV/EBITDA, EV/Sales, P/FCF, FCF yield or dividend yield as a precise
number unless eight conditions all hold — a fresh price timestamp, a
temporally compatible financial period, a known publication date on the
earnings fact, a certain share-count semantic, matched currencies, no
intervening share-count-changing corporate action, genuine TTM completeness
(four contiguous quarters, not an assumption), and no silently superseded
restatement. On failure it prints the state and the reason rather than a
number. A pass may still carry warnings, which are reported alongside it.

### Depth levels

Each depth also carries a conviction ceiling, enforced in code — see the
conviction paragraph further down rather than each bullet here.

- **TLDR** (60–90 s): identity, price, the call, its single biggest risk.
- **QUICK** (2–4 min): identity, price, headline financials, multiples against
  own history, the single biggest risk, short interest and insider net for a
  Swedish name. No verification phase, no scenarios, no scorecard.
- **COMPARE** (4–6 min per company): everything in QUICK plus the Moat Score
  and a light bear/base/bull, so downside and risk/reward are real — but no
  DCF, no reverse DCF, no peer set and no nine-category scorecard, and
  therefore **no Investment Score**. It exists specifically because QUICK runs
  no scorecard and no scenarios, so it cannot honestly produce an Investment
  Score or an expected return; a comparison table demanding either from a
  QUICK run would have to invent it.
- **STANDARD** (8–12 min, default): full source chain per the registry,
  fundamentals, moat, growth, management, valuation from multiples, bear/base/
  bull, devil's advocate, scorecard, Evidence block.
- **DEEP** (25–35 min): everything in STANDARD plus DCF and reverse DCF with
  sensitivities, a computed peer set, 10-year valuation history, ownership and
  guidance trends, corporate actions, industry benchmark.

At QUICK and TLDR, where no scorecard or scenarios run, the scores line reads
`Investment Score n/a — no scorecard at this depth` rather than inventing
either figure. Identity resolution runs at every depth and is never skipped.

### Output
A 9-category scorecard (business quality, growth, profitability, balance sheet,
moat, management, capital allocation, valuation, risk/reward) producing
`INVESTMENT SCORE /100` (the nine scores summed, then rescaled to /100), and —
separately, never merged — `DATA CONFIDENCE /100` measuring how well the
evidence supports it.

Conviction is VERY LOW / LOW / MEDIUM / HIGH / VERY HIGH and is **capped by the
weakest input rather than averaged** — never by the average of several caps.
Until v3.0.0 this was prose the model was asked to self-apply, and
`grep conviction scripts/*.py` returned nothing. It is now computed from the
depth and the run's reason codes, and a record above the ceiling is refused
rather than warned about: TLDR, QUICK and COMPARE depth cap at MEDIUM; an MTF
microcap caps at MEDIUM; an unresolved conflict, a fired thesis breaker, or
data confidence below the floor each cap at LOW. The ladder and the caps have
exactly one home, `references/data-quality.md` §7.

The decision record carries: ticker, legal entity, LEI or ISIN, current price
with timestamp and source, quote and reporting currency, bear/base/bull fair
value with scenario weights, expected return, margin of safety, investment
score, data confidence, conviction with the caps that applied, recommendation,
the reason codes with their severity, and the two or three assumptions the call
rests on. The verdict block that opened the analysis is written **from** it.

---

## 6. Problems already solved (do not re-suggest these)

These were real defects, found and fixed. Listing them so you can see the
standard of rigour expected and avoid proposing things already done.

### Methodology corrections

1. **Share classes.** Swedish large caps have two listed classes trading at
   different prices. Market cap is now computed as the sum, across share
   classes, of outstanding shares (registered minus treasury) times that
   class's own price — a single blended price is wrong the moment A/B or
   preference classes diverge, and it previously understated Volvo's market
   cap by SEK 154bn.
2. **Financials and real estate carve-out.** EV, net debt, EBITDA and FCFF are
   meaningless for a bank, an insurer or a real-estate company — a bank's debt
   is its raw material and its cash is its inventory. These now route to a
   sector-specific framework (P/TBV vs ROTE for banks, P/B vs ROE and solvency
   for insurers, discount/premium to EPRA NTA for real estate) instead of
   producing a nonsense multiple.
3. **IFRS 16 consistency between FCFF and the equity bridge.** Right-of-use
   depreciation sits inside the D&A add-back in the standard FCFF formula,
   while the lease liability is separately subtracted in the enterprise-to-
   equity bridge — added back once, then subtracted once more, which can
   overstate fair value by close to 2x on a lease-heavy retailer. Either the
   ROU depreciation is excluded from the add-back or the ROU addition is
   treated as capex, and the treatment used is stated.
4. **Swedish GAAP (K3) issuers.** First North, Spotlight and NGM issuers may
   report consolidated accounts under K3 rather than IFRS (First North Premier
   requires IFRS; the rest do not). K3 has no IFRS 16 equivalent — leases stay
   off balance sheet — and its own goodwill-amortisation rules, so ratios and
   red flags written for IFRS can misfire on a K3 filer unless flagged.
5. **Reporting currency ≠ quote currency.** Evolution is quoted in SEK and
   reports in EUR.
6. **Non-calendar fiscal years.** Addtech and Lagercrantz end 31 March, H&M
   ends 30 November, Sectra, Clas Ohlson and Systemair are also non-calendar.
   Detected from filings, never assumed.
7. **Insider signal contamination.** The register mixes discretionary
   open-market trades with option exercises and sell-to-cover. Evolution's raw
   12-month net reads **+293m SEK buying**; the discretionary signal is
   **−87m SEK selling**. Classified into DISCRETIONARY / MECHANICAL /
   DERIVATIVE before anything is summed.
8. **Short interest.** Named holders are disclosed only at ≥0.5% while the
   aggregate counts from 0.1%. Reporting the named sum as the total understates
   the short base by roughly half.
9. **Stale short positions.** A holder's last filing from 2015 with no closing
   row inflated Elekta's reconstructed short base from 8.63% to 17.10%.
10. **Fund manager rebrands** read as ownership turnover (Storebrand's rename
    fabricated 11 exits and 11 additions in one name).
11. **Number-format ambiguity.** Nordic releases in Swedish write `28 838`; the
    same company's English release writes `24,297`. Reading a comma as a
    decimal separator is a 1000× error that looks plausible.
12. **Period ambiguity.** A quarterly report states the quarter and the
    half-year under identical labels (`Net sales 28,838` and `Net sales
    41,881`).
13. **Ambiguous company names.** "Volvo" is AB Volvo *or* Volvo Car AB. The
    system refuses to resolve rather than guessing.
14. **Cash roll-forward false alarms.** IFRS reports FX translation on cash as
    a separate line; omitting it broke the tie for every multinational.

### Code hardening

An independent review of the codebase found nine real bugs, three of which
could silently produce a wrong number, and all nine passed the then-current
57-test suite. Finding bugs that a large regression suite did not catch is the
reason the review happened at all, and all nine are now fixed and covered:

15. A **typographic minus sign** (a Nordic release writes `- 11 471` with a
    space after the sign) that, unguarded, flipped the sign of a figure and
    turned a cash outflow into an inflow.
16. **ESEF observations stamped with the fiscal period end instead of the
    publication date** — which let a point-in-time run use figures that were
    not actually published until months later, a look-ahead-bias bug in the
    exact mechanism meant to prevent it.
17. **Split-year fiscal labels** — a filer writing "Q1 2025/26" (a fiscal year
    ending in 2026) had the bare year captured by the quarter regex resolve to
    2025, one year early.
18. A **corroboration check that graded a zero against any value as
    agreement** — a source reporting zero for a metric another source reports
    as a large real number is a conflict, not confirmation, however the
    percentage spread is denominated; the check now treats zero-versus-zero
    as legitimate agreement while still flagging zero-versus-real-value as a
    conflict.
19. **Five places where an ambiguous company name resolved to whichever
    candidate came first** instead of refusing — the same failure mode
    `company_resolve.py` was built to prevent, recurring in scripts that
    perform their own lighter-weight name matching.
20. **Silent partial share-class sums reported as confident totals** — a
    share-count total built from whichever classes happened to be found,
    presented with the same confidence as one confirmed against all listed
    classes.

### Structural corrections in v3.0.0

21. **The decision record was three uncrosschecked copies.** The verdict block,
    the signal line and the record carried the same numbers with any divergence
    treated as a defect — but three prose copies inside one document are
    cross-checked by nothing. The model now emits one validated record and the
    block is rendered from it.
22. **The conviction caps were unenforced.** They were documented in two places
    as prose the model should remember. They are now computed and a record
    above the ceiling is refused; the prose has one home instead of two.
23. **The trigger table was computed and then discarded.** Every STANDARD run
    produced numeric, filing-checkable thresholds that are literally the thesis
    ledger's input contract, and then threw them away — so the ledger stayed
    empty and the portfolio review's layer-1 breaker check could never fire on
    anything. `/analyze` now writes the thesis and stores the decision.
24. **The split-adjustment claim was undated and circular.**
    `corporate_actions.py` cited `nordic_shares.py`'s docstring, which was the
    same unmeasured assertion, and both said "unadjusted". Four dated splits in
    both directions (Mycronic 2:1, Investor A/B 4:1, Bambuser 1:30 reverse,
    Nobia 1:10 reverse) show no discontinuity at the effective date: the series
    **is** back-adjusted for splits. The hazard that replaces the old one is
    named in the instruction layer — never apply
    `split_adjustment_factor()` to a price, only to a per-share fundamental,
    or it double-adjusts.
25. **Four number parsers that did not agree.** `venues_se.py` said "the one
    number parser in this toolkit — never a second parser"; there were four,
    each a genuine independent fix, three of which were never shared. A new
    script had a one-in-four chance of copying the naive version.
    `numparse.py` is the union of all four.
26. **Two CAGR implementations carrying the same two bugs.** A period-count
    exponent printed "2y CAGR +24.6%" for a true +15.9% where a year was
    missing from the series, and neither checked currency, so Betsson's
    SEK-to-EUR redenomination compounded one unit against another.
    `finmath.py` derives the exponent from elapsed days and refuses a mixed-
    currency series.
27. **The screen pipeline existed in two divergent copies.**
    `screen_value.py` aliased 18 names out of `screen_digest.py`'s module
    globals, with a full second reimplementation in an `else:` branch — logic
    both scripts' correctness depends on, in two places that could silently
    drift, and a dependency invisible to grep. `market_universe.py` is now the
    one implementation.
28. **Fifteen hand-rolled HTTP fetchers, one of which retried.** The free,
    keyless, rate-limited sources this toolkit lives on return 429 under
    ordinary use and succeed on the next try; only `macro_se.py` retried. Three
    of the hand-rolled cache-key functions truncated a sanitised key without
    hashing it — the exact collision `macro_se.py`'s own docstring warns
    about. `http_util.py` generalises the retry and always hashes.
29. **A cross-check advertised after its removal.** `quote.py`'s US-listings
    cross-check went out with US coverage, but the docs kept promising a
    "two-source cross-check" on every price. There is now a real second source
    for a Nasdaq Nordic ticker — Nasdaq's own venue reference data — reported
    as CROSS-CHECKED, CONFLICT or `not checked`, with `not checked` never
    folded into a clean result.

---

## 7. Known gaps — this is where I want your help

Be aware these are known. Tell me how to solve them *within the constraints*, or
tell me honestly that one cannot be solved.

Four gaps from the previous version of this brief have moved into section 6 and
are no longer open: the decision record now persists in a validated shape, the
conviction caps are enforced, the thesis ledger is actually written to, and the
price series' split adjustment is measured rather than asserted. What remains
below is what those did not fix.

### The TTM bridge exists now, and its reliability is the open question

The previous version of this system had no trailing-twelve-months logic at
all: ESEF publishes annual reports only, the index lags, and every multiple was
today's price over up to two-year-old earnings. `ttm_engine.py` now assembles
TTM = latest YTD + previous full FY − previous-year YTD over the same months,
handling non-calendar fiscal years, cumulative-vs-discrete interim reporting,
missing quarters (flagged, never silently closed), restated priors, and
discontinued-operations basis changes.

The honest limitation: the annual term can be corroborated against tagged ESEF
XBRL, but the interim terms are parsed out of press-release prose, which
changes format without warning. A TTM figure is tier-2 evidence — good enough
to stop the system valuing a company on stale annual earnings, not as solid as
a tagged filing. `valuation_gate.py` enforces this at the point of use: it
refuses to print a multiple at all when the eight temporal/identity/currency
conditions it checks are not met, rather than silently accepting a
best-effort TTM. What I want help with is not "build an LTM bridge" any more —
it is: how would you raise confidence in a text-parsed interim figure without
a licensed source, and what additional cross-checks would catch a prose-parsing
misfire before it reaches a multiple?

### Other gaps

- **EV/EBIT still cannot be made fully current for most Nordic issuers.**
  Interim reports commonly do not disclose EBIT as a standalone line, so even
  with the TTM engine, a current EV/EBIT is frequently `DATA NOT AVAILABLE`.
- **Net debt/EBITDA is not computable from ESEF alone.** Depreciation and
  non-current borrowings are largely untagged in the notes, so this ratio
  depends on a human reading the report.
- **No fair-value calculator, though the last step of it is now checked.** The
  DCF and the reverse DCF are still done by the language model in prose. What
  changed is the arithmetic that turns scenarios into a headline number:
  `decision_record.py` recomputes the expected return, both margins of safety
  and the weight sum, and refuses the record when they disagree. The scenario
  *values* remain judgement; the weighting of them is no longer a hand
  calculation nobody checked.
- **No analyst consensus.** None is available free. The system substitutes a
  reverse DCF and is explicit that it is not consensus.
- **No earnings call transcripts.** None available free.
- **Ownership is partial.** Only Swedish UCITS funds file holdings; foreign
  institutions and direct holdings are invisible. Reported as a floor, not a
  total.
- **No aggregate short-interest history.** The regulator publishes only a
  current snapshot; the trend is computed on the named ≥0.5% base.
- **Historical backtesting is still not defensible, and three structural facts
  are why.** `esef_fundamentals.py` returns the latest restated figure, so the
  as-originally-reported number a past decision would have to be graded against
  does not exist. Every universe build queries the live listing, so a company
  that delisted, merged or failed is invisible to any "what would the screen
  have said" reconstruction — survivorship bias is structural, not a data gap
  a better query closes. And there is no consensus history and no historical
  share register, so "as investors knew it then" cannot be rebuilt for anything
  beyond price. What v3.0.0 adds is the one measurement that needs none of
  them: `calibration.py` attaches realised outcomes at 3, 6 and 12 months to
  decisions stored **from now on**, prints `INSUFFICIENT SAMPLE` below its
  minimum rather than a hit rate, and feeds nothing back into any score, cap or
  threshold. Forward-only, read-only, and not a backtester.
- **No point-in-time price and no point-in-time share count.** Publication
  dates travel with every datapoint and `thesis_ledger.py --evaluate --as-of`
  re-plays one stated thesis against what was knowable on a past date, but
  there is no stored history of what a price or a register said on an arbitrary
  earlier day.
- **Dividend adjustment in the price series is unverified**, so no total-return
  or dividend-yield analysis off it, and there is no dividend-per-share
  history. Splits are settled — see item 24 above.
- **No segment data** — ESEF tags primary statements only, not the notes.
- **MTF issuers (First North, Spotlight, NGM) have no ESEF at all**, and may
  additionally report under K3 rather than IFRS, so their financials come from
  report PDFs and release text, which is inherently more fragile, and ratios
  built for IFRS need the K3 adjustment stated above.
- **MFN's per-company feed is capped at ~30 recent items** and ignores offset,
  so historical releases are unreachable through it.

---

## 8. What I want from you

Given everything above — and respecting the free/keyless constraint absolutely —
please give me:

1. **The three highest-impact improvements** you would make, ranked, with your
   reasoning. Impact means: how much more likely is the final BUY/HOLD/SELL to
   be correct and well-founded.
2. **How to raise confidence in the TTM engine's interim-report parsing**
   without a licensed source — given the annual term is verifiable against
   tagged XBRL but the interim terms are parsed from prose that changes
   format without warning, what cross-checks would catch a misparse before it
   reaches a multiple, and how would you quantify the residual risk rather
   than leaving it as a qualitative "tier-2" label?
3. **Analytical blind spots.** What is a professional equity analyst doing that
   this framework does not do at all? Not tooling — thinking.
4. **Where the framework is over-engineered.** What adds ceremony without
   improving the decision? I would rather delete something than carry it.
5. **Free Swedish or European data sources I appear to have missed**, if any.
   Only if genuinely free and keyless.
6. **Failure modes I have not anticipated** — ways this system could produce a
   confident, well-formatted, wrong answer.

Please be blunt. I am more interested in what is wrong with it than in what is
good about it.
