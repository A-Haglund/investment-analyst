# Equity Research environment

Setup for deep fundamental analysis of **US, Nordic (SE/NO/DK/FI), German and
French** listed companies in Claude Code and the Claude app.

Plugin version 2.4.0.

Installed 2026-08-31. Config backups in `_config-backup-20260831-081419/`.

## What is installed

| Component | Source | Scope |
|---|---|---|
| `investment-analyst` | `finance-local` (this directory) | user |
| `financial-analysis` | `anthropics/financial-services` (official) | user |
| `equity-research` | `anthropics/financial-services` (official) | user |

Marketplaces are declared in `~/.claude/settings.json` under
`extraKnownMarketplaces`. Installed copies live in
`~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`.

## Data sources

### Working now — free, no credentials

| Source | Used for | Auth |
|---|---|---|
| SEC EDGAR (`data.sec.gov`) | US fundamentals, filings, Form 4 insiders | `SEC_USER_AGENT` only |
| ESEF (`filings.xbrl.org`) | Nordic + French annual fundamentals (IFRS XBRL) | none |
| GLEIF (`api.gleif.org`) | LEI resolution | none |
| ESMA FIRDS | ISIN/LEI/MIC/CFI resolution, venue identity | none |
| Riksbanken SWEA | SEK risk-free rate, policy rate, FX | none |
| SCB (Statistics Sweden) | CPI, PPI, official operating-margin benchmarks by industry | none |
| Nasdaq Nordic reference data | Shares outstanding per class, market cap, segment, index membership | none |
| Nasdaq CNS announcement feed | Corporate actions, share-count disclosure log | none |
| FI Blankningsregistret | Swedish disclosed short interest, back to 2010 | none |
| FI Fondinnehav | Swedish institutional ownership per ISIN, quarterly | none |
| EU VIES | Legal-entity verification from organisationsnummer | none |
| Cision | Releases for Swedish issuers not on MFN (Sandvik, Atlas Copco, Hexagon, AB Volvo) | none |
| Finansinspektionen Insynsregistret | Swedish PDMR insider transactions | none |
| MFN.se | Swedish regulatory releases and report PDFs | none |
| Company IR sites | Primary source for reports, targets, calendar | none |
| Nasdaq (`api.nasdaq.com`) | US quotes | none |
| Yahoo Finance | US + Nasdaq Stockholm quotes | none (unofficial) |

`SEC_USER_AGENT` is set in `~/.claude/settings.json`. The SEC requires a real
contact address; requests without one are rejected.

The free-and-keyless constraint is the plugin's defining rule, not a budget
choice: no paid data, no API keys, no trials that later require payment, no
scraping that breaches a site's terms. Bloomberg, FactSet, Capital IQ, LSEG,
Refinitiv and Börsdata are excluded by design.

### Registered, awaiting a licence

Official vendor connectors ship with the `financial-analysis` plugin. They are
configured and will authenticate as soon as an entitlement exists — no further
setup needed on this machine.

| Connector | Needs |
|---|---|
| S&P Capital IQ | Capital IQ licence |
| FactSet | FactSet subscription |
| LSEG | LSEG/Refinitiv licence |
| Daloopa | Daloopa account |
| Morningstar | Morningstar Direct |
| Moody's, PitchBook, Aiera, Chronograph | respective subscriptions |

To connect one once licensed: run `/mcp` and authenticate the server.

### Not configured

FRED (Federal Reserve macro data) — skipped by choice. To enable: get a free key
at `fredaccount.stlouisfed.org/apikey` and add `FRED_API_KEY` to the `env` block
in `~/.claude/settings.json`.

## Using it

The skill triggers on natural language — no command needed:

```
Analysera NVIDIA
Är Evolution köpvärd på dagens kurs?
Ge mig en DCF på Atlas Copco
Jämför Hermès och LVMH
Vad tycker du om SAP-aktien?
```

Explicit commands:

| Command | Depth | Purpose |
|---|---|---|
| `/tldr <ticker>` | TLDR, 60-90 s | The shortest honest answer, under 150 words |
| `/quick <ticker>` | QUICK, 2-4 min | Fast read: price, headline financials, top risk |
| `/analyze <ticker>` | STANDARD, 8-12 min | Full analysis, fair value from multiples |
| `/analyze <ticker> --deep` | DEEP, 25-35 min | Adds DCF, reverse DCF, peer set, industry benchmark |
| `/analyze <ticker> --quick` | QUICK | Same as `/quick` |
| `/compare <t1> <t2> ...` | COMPARE, 4-6 min/company | Several names ranked on real risk/reward, no Investment Score |
| `/screen <tickers or sector>` | mixed | Cheap first-pass filter, then full second-pass analysis on the shortlist |

## Output format

Every run opens with a **verdict block**: a four-line fenced header (identity
and date; the call with its conviction; price, fair-value range and upside;
Data Confidence and Investment Score) followed by five labelled prose lines —
Why, Risk, Priced in, Watch, Unverified. At QUICK and TLDR depth, which run no
scorecard and no scenarios, the scores line reads `Investment Score n/a — no
scorecard at this depth` rather than inventing one.

A STANDARD or DEEP analysis has **twelve sections** — verdict, snapshot,
business and moat, financials, owners and management, valuation, scenarios,
bear case and red flags, scorecard, thesis and triggers, Evidence, decision
record. DEEP deepens these sections (DCF, reverse DCF, peer set, ownership and
guidance trends); it never adds new ones.

Every invalidation condition lives in one trigger table (section 10) instead
of being restated in several places. Section 11, the **Evidence block**,
carries the Data Confidence score, every material figure grouped by
verification status (`VERIFIED`, `CROSS-CHECKED`, `SINGLE SOURCE`, `CONFLICT`,
`STALE`, `DATA NOT AVAILABLE` — the last four printed even when empty, as
`none`), and a mandatory `TALLY` line, e.g. `5 of 12 material figures VERIFIED
· 0 conflicts · 2 not available`. Section 12, the decision record, is a fixed
machine-comparable shape that must be numerically identical to the verdict
block — any divergence between the two is treated as a defect.

Investment Score (how good the opportunity looks) and Data Confidence (how
well the evidence supports it) are never merged into one number.

Charts are text-only, nothing wider than 88 characters, and limited to four
forms: a labelled sparkline, a range marker against own history, a
bear/base/bull scenario ladder, and the bar column inside the scorecard.

**Output is text by default.** The skill does not build an HTML artifact
unless you ask for a report or something to share.

## Depth levels

A full run is four to six hours of analyst work. There are five depths, chosen
from how the question is phrased:

| Depth | Time | What it adds over the level below |
|---|---|---|
| TLDR | 60-90 s | Identity, price, the call, its biggest risk. Conviction capped at MEDIUM |
| QUICK | 2-4 min | Headline financials, multiples vs own history, short interest and insider net for Swedish names |
| COMPARE | 4-6 min/company | Moat Score and a light bear/base/bull, so downside and risk/reward are real — still no DCF, peer set or scorecard |
| STANDARD (default) | 8-12 min | Full source chain, moat/growth/management, scenarios, devil's advocate, scorecard, Evidence block. Fair value from multiples |
| DEEP | 25-35 min | DCF and reverse DCF with sensitivities, computed peer set, 10-year valuation history, ownership and guidance trends, corporate actions, industry benchmark |

COMPARE exists because QUICK runs no scorecard and no scenarios, so it produces
neither an Investment Score nor an expected return — inventing either for a
comparison table would be the exact failure the toolkit exists to prevent.
COMPARE adds just enough (Moat Score, a light scenario build) for `/compare` to
rank on a real base fair value and risk/reward without a full STANDARD run per
name.

Swedish trigger words work: "snabbkoll" -> QUICK, "djupanalys" -> DEEP.
Every run states its depth in the first line and says what it skipped.

## Scripts

Run directly for quick lookups. Paths relative to
`plugins/investment-analyst/skills/investment-analyst/scripts/`.

```bash
python company_resolve.py "Volvo"              # refuses - AB Volvo or Volvo Car
python venues_se.py "Kopparbergs"              # which venue, whether ESEF applies
python ir_discovery.py "Sandvik"               # the issuer's own IR site, verified
python sec_fundamentals.py NVDA --years 5      # US fundamentals with provenance
python esef_fundamentals.py --search "Evolution" --country SE   # Nordic/French
python ttm_engine.py "Sandvik" --explain       # trailing twelve months from interims
python valuation_gate.py "Sandvik"             # refuses a multiple on a stale period
python earnings_quality.py "Sandvik"           # cash-conversion and accrual checks
python quote.py NVDA VOLV-B.ST                 # price with as-of timestamp
python nordic_shares.py "Volvo"                # shares outstanding, all classes
python share_semantics.py "NIBE" --market-cap  # resolves which share count applies
python corporate_actions.py "Sandvik" --shares # splits, issues, dilution log
python guidance_track.py "Sandvik" --history   # targets vs delivery
python peers_se.py "Sandvik" --multiples       # peer set by business archetype
python macro_se.py --dcf-inputs                # risk-free rate, policy rate, FX
python insider_se.py --issuer "Volvo" --months 12
python mfn_news.py --search "atlas copco"      # resolve MFN slug
python mfn_news.py evolution --reports         # report PDFs
python mfn_news.py kebni --reports --figures   # First North: figures from the release
python cision_news.py --search "Sandvik"       # resolve Cision newsroom slug
python short_se.py "Embracer"                  # disclosed short interest
python ownership_se.py --isin SE0012673267     # Swedish institutional owners
python verify_filing.py --lei <LEI>            # restatement + internal ties
python thesis_ledger.py "Sandvik" --evaluate   # re-test a stored thesis's breakers
```

`finfact.py` is the provenance and corroboration core the other scripts import;
it is not run directly except `--selftest`.

## Editing the skill

The plugin is **copied** into the cache at install time. Editing files here does
not take effect until you reinstall:

```bash
# 1. edit files under plugins/investment-analyst/
# 2. bump "version" in plugins/investment-analyst/.claude-plugin/plugin.json
claude plugin marketplace update finance-local
claude plugin update investment-analyst@finance-local
# 3. restart Claude Code
```

Skipping the version bump means the update is a no-op.

## Using it from the Claude app

The plugin is a standard Claude Code plugin and installs into Cowork the same
way. In Claude Desktop: **Cowork → Customize → Browse plugins → Personal → Add
marketplace**, then point at this directory or a Git remote.

Note: the Python scripts run in Claude Code. In the Claude app the skill falls
back to fetching the same endpoints directly — every URL is documented in
`references/data-sources.md`, so the analysis is identical and only the
retrieval mechanism differs.

To share across machines, push this directory to a Git repo and add the
marketplace from GitHub instead of from a local path.

## Structure

```
C:\Finance\
  .claude-plugin\marketplace.json
  plugins\investment-analyst\
    .claude-plugin\plugin.json
    commands\            analyze.md, compare.md, quick.md, screen.md, tldr.md
    skills\investment-analyst\
      SKILL.md           process spine, depth levels, verdict block, evidence rules
      references\        13 files, loaded per phase
        worked-example.md            calibrates tagging density and output format
        source-registry.md           which source is authoritative for which data
        data-quality.md              datapoint model, conflict resolution, confidence
        red-flags-and-smallcap.md    20-item red-flag screen, small-cap/MTF posture
        data-sources.md              every endpoint, what is free, what needs credentials
        verification.md              cross-checks and the Evidence block
        fundamentals.md               metric definitions, formulas, quality of earnings
        moat-growth-management.md    moat scoring, growth drivers, management
        valuation.md                 multiples, DCF, reverse DCF, scenarios
        bear-case-and-scoring.md     devil's advocate, scorecard, recommendation
        portfolio.md                 sizing, concentration, exposure, ranking
        sweden.md                    Nasdaq Stockholm source chain, IFRS/K3 terms
        europe.md                    Nordics, Germany, France; ESEF; currency traps
      scripts\           23 scripts, stdlib only — company_resolve.py, venues_se.py,
                         ir_discovery.py, sec_fundamentals.py, esef_fundamentals.py,
                         ttm_engine.py, valuation_gate.py, earnings_quality.py,
                         share_semantics.py, corporate_actions.py, guidance_track.py,
                         peers_se.py, macro_se.py, quote.py, insider_se.py,
                         mfn_news.py, cision_news.py, short_se.py, ownership_se.py,
                         nordic_shares.py, verify_filing.py, thesis_ledger.py, finfact.py
      tests\             57-test regression suite — run_tests.py, helpers.py,
                         test_fiscal_year.py, test_first_north.py,
                         test_multi_share_class.py, test_parser_regressions.py,
                         test_stale_valuation.py, test_temporal_integrity.py
  _config-backup-*\      pre-change copies of all Claude config
```

## Restoring

```bash
cp _config-backup-20260831-081419/settings.json.bak ~/.claude/settings.json
cp _config-backup-20260831-081419/claude.json.bak ~/.claude.json
```

`financial-analysis.mcp.json.bak` and `financial-analysis-INSTALLED.mcp.json.bak`
are the original (malformed) upstream connector configs, kept for reference.

## Verification

The toolkit verifies figures rather than only sourcing them. A `FACT` tag
records origin; these checks establish correctness, and `finfact.py` is the
shared provenance and corroboration core every data script imports.

- **Restatement check** — a filing's prior-year comparative against the previous
  filing's own figure for that year. Two separately prepared documents, so
  agreement corroborates and disagreement is a restatement worth reporting.
- **Internal ties** — assets = liabilities + equity, revenue − cost of sales =
  gross profit, and the cash roll-forward including the IFRS FX-on-cash line.
- **Release cross-check** — against the company's own report release, where MFN
  still carries it within its roughly 30-item window.

`scripts/verify_filing.py --lei <LEI> --slug <mfn-slug>` runs all three and
prints the Evidence block. Phase 9 is mandatory at STANDARD and DEEP depth.

A check only asserts a failure when every term of its equation is present;
otherwise it reports `INCOMPLETE`. A check that cries wolf teaches you to
ignore it.

The regression suite carries 57 tests (`skills/investment-analyst/tests/`),
covering fiscal-year-end detection, multi-share-class totals, First North/MTF
routing, parser regressions and temporal integrity (stale-price and
stale-valuation refusals). Run it with `python run_tests.py`; add `--network`
to include the handful of tests that hit live free endpoints.

## Running in the Claude app (Cowork)

Verified 2026-08-31 from an actual Cowork run:

- **Yahoo Finance is blocked** from the app container. The skill now has an
  ordered price fallback (Nasdaq API, then borskollen/allaaktier/aktiespararna)
  with a hard stop after three attempts.
- Confirmed unreachable and skipped: `query1.finance.yahoo.com`, `stooq.com`,
  `marketscreener.com`, `morningstar.com`, `privataaffarer.se`.
- Confirmed reachable: `mfn.se`, `storage.mfn.se`, `api.riksbank.se`,
  `marknadssok.fi.se`, `fi.se`, `borskollen.se`, `allaaktier.se`,
  `aktiespararna.se`, company IR sites.
- The skill no longer guesses IR URLs - it follows the link from the home page.

## Coverage limits worth knowing

- **First North, Spotlight and NGM have no ESEF at all** — they are MTFs, not
  regulated markets, so the mandate does not apply. The MAR report release is
  the primary source; use `mfn_news.py --figures --text --pdf`.
- **First North, Spotlight and NGM issuers may report under Swedish GAAP (K3)**
  rather than IFRS, and often do — First North Premier requires IFRS, the rest
  do not. K3 has no IFRS 16 equivalent (leases stay off balance sheet) and its
  own goodwill-amortisation rules, so ratios written for IFRS can misfire on a
  K3 filer; `references/sweden.md` has the framework detail.
- **Banks, insurers and real-estate companies use a different framework
  entirely.** EV, net debt, EBITDA and FCFF are meaningless for them — a
  bank's debt is its raw material. Use P/TBV vs ROTE (banks), P/B vs ROE and
  solvency (insurers), or discount/premium to EPRA NTA (real estate) instead.
- **Market cap sums every share class separately** — outstanding shares (net
  of treasury) times that class's own price, then summed — never one price
  times a blended total, which is wrong the moment A/B or preference classes
  trade at different prices.
- **MFN does not carry every Swedish issuer.** Sandvik, Atlas Copco, Hexagon and
  AB Volvo distribute via Cision and return an empty MFN feed (they still show
  up in MFN search). `cision_news.py` closes that gap — but Cision publishes no
  regulatory flag, so its labels are keyword heuristics, not MFN's `:regulatory`
  tag.
- **MFN's per-company feed is capped at roughly 30 recent items** and ignores
  offset, so historical releases are unreachable through it.
- **Nasdaq price history is unadjusted** for splits and dividends. Fine for a
  multiple range when paired with the share count of the time; wrong for total
  return.
- **Bolagsverket has no keyless data at all.** Share capital, board and auditor
  need a paid Företagsinformation v4 agreement. EU VIES gives free legal-name
  verification from an organisationsnummer as a partial substitute.
- **Germany is not in the ESEF index** — German issuers file with the
  Bundesanzeiger. `esef_fundamentals.py` returns nothing for them and says so;
  use the IR/Bundesanzeiger route in `references/europe.md`.
- **ESEF covers annual reports only** and the index lags unevenly. `ttm_engine.py`
  assembles a trailing-twelve-months figure from the latest annual plus interim
  reports where the arithmetic and the fiscal calendar allow it, but the interim
  terms are parsed from press-release prose rather than tagged data, so treat a
  TTM figure as tier-2 evidence, not as solid as the annual filing beneath it.
- **EV/EBIT still cannot be made fully current for most Nordic issuers**,
  because interim reports commonly do not disclose EBIT on its own, and net
  debt/EBITDA is not computable from ESEF alone, because depreciation and
  non-current borrowings are largely untagged in the notes.
- **ESEF tags primary statements only** — notes are largely untagged, so SBC,
  cost of sales and lease detail are often missing. Reported as
  `DATA NOT AVAILABLE`, never inferred.
- **No consensus estimates and no earnings transcripts** without a licensed
  source. The skill substitutes the reverse DCF and company guidance, and says
  when it has done so.
- **No point-in-time history is stored.** The toolkit carries publication
  dates on what it fetches and refuses to claim historical knowledge it cannot
  evidence — `thesis_ledger.py` can re-test a stored thesis's numeric breakers
  against a later filing, but that is not the same as a backtesting capability.

## Known upstream issue

`anthropics/financial-services` ships `financial-analysis/.mcp.json` with a
JSON syntax error — a missing comma after the `egnyte` block and an unbalanced
brace in `box`. This silently prevented all 12 connectors from loading. Patched
locally in both the marketplace copy and the installed copy. A
`claude plugin marketplace update claude-for-financial-services` will overwrite
the fix; reapply it if the connectors disappear from `claude mcp list`.

---

Analysis output from this toolkit is research, not investment advice.
