# Briefing: "Investment Analyst" v2.4.0 — a Claude Code / Claude Cowork plugin

I am going to describe a software system I have built. At the end I will ask you
for improvement suggestions. Please read the whole thing first, including the
constraints and the known-gaps sections, because they rule out most of the
obvious advice.

---

## 1. What it is

A plugin for Claude (Anthropic's assistant) that performs fundamental equity
research and ends in a BUY / HOLD / SELL recommendation with a fair value per
share. It is optimised for **Swedish listed equities**, with secondary support
for US, Nordic (Norway, Denmark, Finland), German and French issuers.

It is not a trading system, a screener product or a data vendor. It is a
structured research process that a language model follows, backed by 23 Python
scripts that fetch and verify data from official sources — and, increasingly,
that enforce discipline in code rather than only asking the model to remember
it: a shared provenance/temporal-validity core (`finfact.py`), a trailing-
twelve-months assembler (`ttm_engine.py`), a hard gate that refuses a multiple
built on an incompatible price/earnings period (`valuation_gate.py`), and a
persistent, falsifiable thesis ledger (`thesis_ledger.py`). A 57-test
regression suite backs this layer.

Scale: 51 files, ~32,100 lines. Roughly 26,600 lines of Python across 23
scripts, ~4,100 lines of Markdown instruction files the language model reads
(`SKILL.md` plus 13 reference files), ~250 lines of command definitions, and a
~1,100-line, 57-test regression suite.

**Design philosophy, in the system's own words:** the goal is not to predict the
future accurately. The goal is to make bad investment decisions harder.

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
| `SKILL.md` | 638 | The spine: evidence rules, source hierarchy, market routing, depth selection, the verdict block, the twelve-section output contract, the research phases |
| `references/red-flags-and-smallcap.md` | 719 | A 20-item red-flag screen with numeric thresholds, plus a distinct posture for small caps and MTF venues |
| `references/sweden.md` | 428 | Swedish source chain, market segments, IFRS/K3 terminology, reporting conventions |
| `references/valuation.md` | 333 | Multiples, DCF, reverse DCF, scenarios, the enterprise-to-equity bridge, the financials/real-estate carve-out |
| `references/data-sources.md` | 326 | Every endpoint, its quirks, and its limits |
| `references/worked-example.md` | 270 | Calibrates output format and tagging density |
| `references/verification.md` | 231 | Cross-checks and the mandatory Evidence block |
| `references/data-quality.md` | 215 | The datapoint metadata model, conflict resolution, data confidence scoring, conviction ladder |
| `references/europe.md` | 180 | Nordics / Germany / France routing and currency traps |
| `references/fundamentals.md` | 173 | Metric definitions, formulas, quality-of-earnings tests, lease treatment |
| `references/moat-growth-management.md` | 163 | Moat scoring 0–10, growth decomposition, management assessment |
| `references/bear-case-and-scoring.md` | 164 | Devil's advocate section, the trigger table, 9-category scorecard, recommendation logic |
| `references/source-registry.md` | 134 | Which source is *authoritative* for which data type |
| `references/portfolio.md` | 114 | Position sizing, concentration, exposure, ranking |

The model loads `SKILL.md` always and pulls a reference file only when it
reaches the phase that needs it (progressive disclosure, to control context
cost).

### 3b. The data layer (23 Python scripts, stdlib only)

| Script | Lines | What it does |
|---|---|---|
| `thesis_ledger.py` | 2852 | Persistent, falsifiable thesis keyed on LEI/ISIN/CIK, with numeric invalidation breakers, re-testable against a later filing; `--as-of` re-plays a past evaluation without overwriting the live status |
| `peers_se.py` | 2633 | Scores a peer set on eight dimensions, business archetype among them, not sector code |
| `ttm_engine.py` | 2450 | Assembles trailing twelve months from the latest annual plus interim reports, since ESEF carries annual figures only |
| `guidance_track.py` | 2123 | Extracts a company's standing financial targets from its own IR pages, keeps them separate from period guidance and delivered outcome, and scores management execution |
| `corporate_actions.py` | 1964 | Splits, rights issues, directed issues, buybacks; the share-count disclosure log |
| `macro_se.py` | 1645 | Riksbank rates/FX/yield curve; SCB official industry margin benchmarks by SNI; ECB/Eurostat |
| `company_resolve.py` | 1550 | Canonical identity: legal name, ISIN, LEI, org number, MIC, share classes, quote vs reporting currency, fiscal year end — refuses on an ambiguous name |
| `ir_discovery.py` | 1468 | Locates and verifies the issuer's own Investor Relations site and report archive, rather than guessing URLs |
| `share_semantics.py` | 1069 | Resolves which of six competing "shares outstanding" figures applies, and computes market cap per class rather than a blended price times a total |
| `venues_se.py` | 995 | Routes an issuer to its listing venue and states whether ESEF applies |
| `insider_se.py` | 1013 | Swedish insider (PDMR) transactions, classified DISCRETIONARY / MECHANICAL / DERIVATIVE |
| `valuation_gate.py` | 1050 | Refuses to print a multiple when price and earnings do not share a compatible period — eight checks, all-or-nothing |
| `short_se.py` | 965 | Swedish disclosed short positions with holder-level trend |
| `earnings_quality.py` | 919 | Cash-conversion and accrual ratios that separate reported profit from actual cash |
| `mfn_news.py` | 647 | Nordic regulatory releases; extracts headline figures from release text |
| `ownership_se.py` | 634 | Swedish fund ownership per ISIN, quarterly, with concentration and trend |
| `sec_fundamentals.py` | 453 | US financials from SEC EDGAR XBRL |
| `finfact.py` | 454 | The shared provenance, temporal-validity and corroboration core (`FinancialFact`, `Verification`, `corroborate()`) every other script imports; not run directly except `--selftest` |
| `verify_filing.py` | 437 | Restatement detection, internal statement ties, release cross-check |
| `esef_fundamentals.py` | 405 | IFRS annual financials from ESEF Inline XBRL |
| `nordic_shares.py` | 507 | Shares outstanding per share class, market cap, 10-year price history |
| `cision_news.py` | 223 | Releases for Swedish issuers that distribute via Cision, not MFN |
| `quote.py` | 179 | Current price with timestamp, staleness note, two-source cross-check |

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
- **SEC EDGAR XBRL** — US filers
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

A STANDARD or DEEP analysis has **twelve sections**, not more: verdict,
snapshot, business and moat, financials, owners and management, valuation,
scenarios, bear case and red flags, scorecard, thesis and triggers, Evidence,
decision record. DEEP deepens these sections (full DCF and reverse DCF,
computed peer set, 10-year history, ownership and guidance trends, industry
benchmark); it never adds new ones.

Every invalidation condition lives in **one trigger table**, in section 10 —
previously the same conditions were written in four different formats across
the document. Section 11, the **Evidence block**, carries the Data Confidence
score, every material figure grouped by verification status (`VERIFIED`,
`CROSS-CHECKED`, `SINGLE SOURCE`, `CONFLICT`, `STALE`, `INCOMPLETE`,
`DATA NOT AVAILABLE`), with `SINGLE SOURCE`, `CONFLICT`, `STALE` and
`DATA NOT AVAILABLE` printed even when empty (as `none`), and closes with a
mandatory `TALLY` line — the structural defence against a run where nothing
was cross-checked reading as clean, since it must print `0 of N verified`.
Section 12, the **decision record**, is a fixed machine-comparable shape that
must be **numerically identical to the verdict block**; any divergence between
the two is treated as a defect rather than a nuance, which is what turns the
repetition into a checksum.

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
- **TLDR** (60–90 s): identity, price, the call, its single biggest risk.
  Conviction capped at MEDIUM.
- **QUICK** (2–4 min): identity, price, headline financials, multiples against
  own history, the single biggest risk, short interest and insider net for a
  Swedish name. No Evidence block, no scenarios, no scorecard. Conviction
  capped at MEDIUM.
- **COMPARE** (4–6 min per company): everything in QUICK plus the Moat Score
  and a light bear/base/bull, so downside and risk/reward are real — but no
  DCF, no reverse DCF, no peer set and no nine-category scorecard, and
  therefore **no Investment Score**. Conviction capped at MEDIUM. It exists
  specifically because QUICK runs no scorecard and no scenarios, so it cannot
  honestly produce an Investment Score or an expected return; a comparison
  table demanding either from a QUICK run would have to invent it.
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
weakest input rather than averaged**. Hard caps: an unresolved conflict caps at
LOW; a First North or Spotlight microcap caps at MEDIUM; QUICK, TLDR and
COMPARE depth all cap at MEDIUM.

Decision record: ticker, legal entity, current price with timestamp, reporting
currency, bear/base/bull fair value, expected return, margin of safety,
investment score, data confidence, conviction, recommendation, and the two or
three assumptions the call actually rests on — numerically identical to the
verdict block that opened the analysis.

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

---

## 7. Known gaps — this is where I want your help

Be aware these are known. Tell me how to solve them *within the constraints*, or
tell me honestly that one cannot be solved.

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
- **No fair-value calculator.** The valuation arithmetic — the DCF, the
  reverse DCF, the scenario weighting — is still done by the language model in
  prose, checked by the valuation gate for temporal validity but not
  independently computed by a script.
- **No analyst consensus.** None is available free. The system substitutes a
  reverse DCF and is explicit that it is not consensus.
- **No earnings call transcripts.** None available free.
- **Ownership is partial.** Only Swedish UCITS funds file holdings; foreign
  institutions and direct holdings are invisible. Reported as a floor, not a
  total.
- **No aggregate short-interest history.** The regulator publishes only a
  current snapshot; the trend is computed on the named ≥0.5% base.
- **No point-in-time storage, in the sense of a backtest engine.** The
  toolkit carries publication dates on the datapoints it fetches and refuses
  to claim historical knowledge it cannot evidence. `thesis_ledger.py` adds a
  genuinely new piece — a stored, falsifiable thesis with numeric breakers
  that can be re-evaluated in HISTORICAL mode against what was knowable on a
  past date — but that is a re-test of one stated thesis, not a backtesting
  capability across arbitrary strategies, and it is not presented as one.
- **Price history is unadjusted** for splits and dividends.
- **No dividend-per-share history**, so no yield or total-return analysis.
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
