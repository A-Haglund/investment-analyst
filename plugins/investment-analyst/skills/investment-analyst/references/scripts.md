# Scripts — the full catalogue, grouped by role

Loaded on demand. The research phases in `SKILL.md` §8 name the script each
phase needs, so a normal run never reads this file; reach for it when you need
a tool the phase did not name, or want to know what else exists.

Every script is a standalone CLI, not a package. Most expose `--json` and
`--selftest`. Call them with `--json`: the human-readable default repeats
banners and column padding that carry no information the analysis uses.

**Identity, venue and the issuer's own material**

| Script | Purpose |
|---|---|
| `scripts/company_resolve.py` | **Phase 0.** Canonical identity; refuses to resolve an ambiguous name |
| `scripts/venues_se.py` | Which Swedish venue an issuer is on, and the source chain that follows |
| `scripts/ir_discovery.py` | The issuer's own IR site, verified — reports, targets, calendar |
| `scripts/mfn_news.py` | Swedish regulatory releases and report PDFs from MFN.se |
| `scripts/cision_news.py` | Releases for Swedish issuers that publish via Cision, not MFN |

**Prices, shares and fundamentals**

| Script | Purpose |
|---|---|
| `scripts/quote.py` | Price with as-of timestamp and staleness note; cross-checked against Nasdaq Nordic reference data on a Nordic ticker, `not checked` elsewhere |
| `scripts/nordic_shares.py` | Shares outstanding per class and market cap, from Nasdaq; daily bars back-adjusted for splits, dividend treatment unverified |
| `scripts/share_semantics.py` | Resolves which of six competing "shares outstanding" figures applies, and flags unlisted classes |
| `scripts/esef_fundamentals.py` | Nordic and French fundamentals from ESEF Inline XBRL |
| `scripts/ttm_engine.py` | Assembles trailing twelve months from interim reports, since ESEF carries annual figures only |
| `scripts/corporate_actions.py` | Splits, issues, buybacks, and the share-count disclosure log |

**Gates and checks — the parts that refuse**

| Script | Purpose |
|---|---|
| `scripts/valuation_gate.py` | Refuses to print a multiple when price and earnings do not share a compatible period |
| `scripts/verify_filing.py` | Restatement check, internal ties, release cross-check |
| `scripts/earnings_quality.py` | Cash-conversion and accrual ratios that separate reported profit from actual cash |
| `scripts/decision_record.py` | **§9.** The decision's schema, its arithmetic identities, the enforced conviction ceiling, and the renderer for the fixed-shape block. Refuses a record that disagrees with its own inputs |

**Ownership, insiders, guidance, macro**

| Script | Purpose |
|---|---|
| `scripts/insider_se.py` | Swedish PDMR insider transactions from Finansinspektionen |
| `scripts/short_se.py` | Swedish disclosed short interest from Finansinspektionen |
| `scripts/ownership_se.py` | Swedish institutional ownership from FI fund holdings |
| `scripts/guidance_track.py` | Standing financial targets and the delivery record against them; persists each extracted statement with the vintage it was made in, so a target is compared against what was said then |
| `scripts/peers_se.py` | Scored peer set by business archetype, not ICB sector |
| `scripts/macro_se.py` | DCF inputs from Riksbanken; official SCB industry benchmarks |
| `scripts/horizon.py` | The next scheduled report date for a Nordic-listed issuer, with its source named |

**Screening**

| Script | Purpose |
|---|---|
| `scripts/screen_value.py` | **`/screen`.** The on-demand deep-value screen over the whole listed universe: universe → multi-year history → value filter → liquidity floor → size band → corporate actions → ESEF margins → rank, printing what each stage cut and why |
| `scripts/screen_digest.py` | The unattended daily fell-and-might-be-cheap digest, wall-clock bounded. Run by the scheduled job, not by a command |
| `scripts/screen_metrics.py` | Drawdown, return windows and margin trends as pure functions — no network |
| `scripts/market_universe.py` | The shared universe, liquidity, size and returns layer both screens build on: Nasdaq snapshot plus FIRDS → issuers grouped by LEI → per-class market cap and history → tradeable primary line → SEK-equivalent liquidity floor |

**Decisions, theses and portfolios**

| Script | Purpose |
|---|---|
| `scripts/thesis_ledger.py` | Both persisted objects, keyed on LEI: the falsifiable, price-free thesis with its numeric breakers (`--add`, `--evaluate`), and the price-stamped decision record (`--decide`, `--decisions`, `--decision-latest`, `--supersede`). Append-only; nothing is rewritten or dropped |
| `scripts/watchlist_store.py` | Issuers followed but not owned — stored and listed, with identity resolved the same way and an ambiguous name refused. No quantity, no cost basis, and it never values or scores |
| `scripts/portfolio_store.py` | Store and manage a portfolio at `~/.investment-analyst/portfolio/<name>.json`; accepts pasted Avanza/Nordnet text or typed positions; resolves identity and refuses ambiguous names |
| `scripts/portfolio_review.py` | The three-layer triage: layer 1 breakers via thesis_ledger, layer 2 alerts, layer 3 STANDARD depth on flagged holdings; returns EXIT, TRIM or HOLD per position, or leaves the action open for depth review |
| `scripts/portfolio_metrics.py` | Portfolio-level analysis: Herfindahl concentration, effective position count, sector and geographic exposure, correlation and hidden overlap, downside risk, Data Confidence, cash drag |
| `scripts/research_delta.py` | What changed since last time: diffs a new decision record against the stored one and prints only what moved — the call, the conviction, the price, the fair value, the reason codes |
| `scripts/calibration.py` | Forward-only outcome and calibration reporting on stored decisions. **Not a backtester**, deliberately: it attaches a realised outcome at 3, 6 and 12 months to decisions made from v3.0.0 on, prints `INSUFFICIENT SAMPLE` rather than a hit rate below its minimum, and feeds nothing back into a score, a cap or a threshold |

**Shared core — imported, not run**

| Script | Purpose |
|---|---|
| `scripts/finfact.py` | Provenance and temporal-validity core every fetching script imports (`--selftest` only) |
| `scripts/numparse.py` | The one number parser: comma-versus-decimal, space grouping, the typographic minus |
| `scripts/finmath.py` | Shared financial math — CAGR from elapsed days rather than a period count, with a currency check |
| `scripts/http_util.py` | One fetcher: retry with backoff on 429 and 5xx, and a collision-free cache key |
| `scripts/_bootstrap.py` | The sibling-import idiom, written once and correctly |

**Persistent state and `INVESTMENT_ANALYST_HOME`.** `portfolio_store.py`,
`thesis_ledger.py`, `watchlist_store.py` and `guidance_track.py` all keep their
state under `~/.investment-analyst/<store>` by default, resolved through
`_bootstrap.state_home()`. Set `INVESTMENT_ANALYST_HOME` to move that whole
tree — `~` and environment variables in the value are expanded. This matters
for a scheduled or cloud job: each run starts in a fresh container, so an
unset override means every run sees an empty thesis ledger, and the daily
portfolio review's layer 2/3 then re-runs full analysis on every holding
instead of a cheap HOLD. Point it at a mounted, persistent directory to avoid
that. Each store's own per-store override (`PORTFOLIO_STORE_HOME`,
`THESIS_LEDGER_HOME`, `WATCHLIST_STORE_HOME`, `GUIDANCE_STORE_HOME`) still
takes priority when set, unchanged from before.

Read the warnings the scripts print — they are not decoration. A currency
warning, a stock-split warning, a truncation warning or a multi-tag warning each
means a specific number in the table cannot be used the way it looks.

**One adjustment rule, because getting it backwards is silent.** The daily
closes from `nordic_shares.py` are already back-adjusted for splits — measured
against four dated splits in both directions, with no discontinuity at any
effective date — so a return, a drawdown or a percentile against the company's
own history is correct off those closes as they stand. Never apply
`corporate_actions.split_adjustment_factor()` to a price or a price ratio; that
factor exists for a **per-share fundamental** — EPS, dividend per share, book
value per share — which comes from a filing and is never restated for a split.
Applied to a price it double-adjusts it. Dividends are a separate matter: their
treatment in the series is unverified, so it carries a price range, never a
total return.
