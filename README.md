# investment-analyst

A Claude Code plugin for equity research on Swedish, Nordic, German and French
listed companies, ending in a sourced BUY / HOLD / SELL call.

It is built on one constraint, and everything else follows from it:

> **Only free, keyless, public sources.** No paid data, no API keys, no trials
> that later require payment, no scraping that breaches terms of service, and
> no bypassing paywalls, authentication, robots.txt or rate limits.

Bloomberg, FactSet, Capital IQ, LSEG, Refinitiv and Börsdata are excluded by
design, not by oversight. What is used instead is the primary material those
vendors resell: ESEF/XBRL filings, Finansinspektionen's insider,
short-selling and fund-holdings registers, MFN and Cision regulatory releases,
Nasdaq Nordic and NGM reference data, Riksbanken, SCB, ESMA FIRDS and GLEIF.

## Why it is built the way it is

An analysis is only worth as much as the weakest number in it. The hard part is
not fetching data — it is refusing to present a number as better evidenced than
it is. That discipline lives in code rather than in instructions:

- **Provenance on every figure.** `finfact.py` carries the value, its unit,
  currency, period, source, tier and verification status as separate fields.
  A figure whose tier is not recorded cannot be audited later.
- **Authority is not independence.** An annual report and its ESEF filing agree
  because they are the same document. That is `CROSS-CHECKED`, not `VERIFIED`.
- **Temporal integrity.** Every datapoint knows when it was *published*, not
  just when it was fetched, and a point-in-time run refuses figures that did
  not exist yet.
- **Refuse, never guess.** "Volvo" matches both AB Volvo and Volvo Car AB.
  Every resolver in the toolkit refuses and names the candidates rather than
  taking the first hit — but two share classes of one issuer are one company,
  and a false refusal there is treated as a real defect.
- **A check that could not run is not a check that passed.** Unreachable
  sources degrade to `not checked` and are counted, never folded into a clean
  result.
- **The decision is an object, not prose.** The recommendation, the conviction,
  the fair-value range and the expected return are emitted as a validated
  record; the human-readable block is rendered from it. `decision_record.py`
  recomputes the arithmetic and refuses a record that disagrees with its own
  inputs, so the numbers in the answer cannot drift from the numbers on file.
- **Conviction is capped by the weakest input, in code.** Depth, an unresolved
  conflict, a fired thesis breaker, an MTF microcap and data confidence below
  the floor each set a ceiling, and a record above it is refused rather than
  warned about.
- **No identifying User-Agent, so no SEC EDGAR — and no US coverage.** SEC's
  fair-access policy requires a real contact address on every request, which
  would identify the analyst behind each run; a fabricated address would
  breach the policy this tool is bound by. US equities are out of scope as a
  result — a deliberate boundary, not a gap. See `SKILL.md` §3.

## Commands

| Command | What it does |
|---|---|
| `/investment-analyst:analyze` | Full analysis of one company, ending in a call |
| `/investment-analyst:quick` | Price, key metrics, valuation, top risk, call |
| `/investment-analyst:tldr` | The shortest honest answer: the call, the reason, the catch |
| `/investment-analyst:compare` | Several companies on one model, ranked by risk/reward |
| `/investment-analyst:screen` | The most attractive names from a candidate list |
| `/investment-analyst:portfolio` | Review holdings: ADD / HOLD / TRIM / EXIT on each |

Every run closes the same way — a colour-flagged call, one plain sentence, and
`Viktigast`, which states the real limitations of that particular run rather
than a boilerplate disclaimer.

## Install

The plugin is distributed through the marketplace declared at the repository
root, so the repository *is* the marketplace.

```bash
git clone <this-repo> ~/finance
claude plugin marketplace add ~/finance
claude plugin install investment-analyst@finance-local
```

To update after pulling:

```bash
claude plugin marketplace update finance-local
claude plugin update investment-analyst@finance-local
```

The plugin is copied into `~/.claude/plugins/cache/` at install time, so edits
to a clone do not take effect until the two commands above are run.

For the Claude desktop app, build a zip and upload it:

```bash
python tools/pack.py        # writes dist/investment-analyst-<version>.zip
```

## Layout

```
.claude-plugin/marketplace.json   the marketplace manifest
plugins/investment-analyst/
  .claude-plugin/plugin.json      the plugin manifest
  commands/                       the six slash commands
  skills/investment-analyst/
    SKILL.md                      method, depths, output contract
    references/                   13 files: valuation, fundamentals, Sweden,
                                  Europe, data quality, verification, sources
    scripts/                      38 Python scripts, stdlib only
    tests/                        32 test files
tools/pack.py                     builds the distributable zip
PLUGIN-BRIEF.md                   a detailed technical brief
MIGRATION.md                      what changed in 3.0.0 and what to do about it
```

## Tests

Over 850 test functions across 32 test files, all passing against live free
endpoints.

```bash
cd plugins/investment-analyst/skills/investment-analyst/tests
python run_tests.py             # offline, seconds
python run_tests.py --network   # against live endpoints, ~2 minutes
```

Most scripts also carry a `--selftest` covering their own parsing edge cases.

The network tests are not wired into CI on purpose: having a build server hit
Finansinspektionen and Nasdaq on every push would be discourteous to the
sources and inconsistent with the terms-of-service posture the plugin commits
to elsewhere.

## Requirements

Python 3, standard library only. No pip install, no virtualenv, no lockfile.

## Upgrading from 2.6

v3.0.0 carries three breaking changes: the decision record is emitted as JSON
and rendered rather than written by hand, the market screen is `/screen` rather
than `screen_digest.py` run by hand, and the price-series adjustment semantics
are pinned down. Stored portfolios and theses from 2.6 load unchanged. See
[MIGRATION.md](MIGRATION.md).

## Known limits

Stated plainly, because a tool that hides its gaps is worse than one that has
them:

- **ESEF is annual only and lags.** Fundamentals from the structured route can
  be up to fifteen months old.
- **EV/EBIT cannot be made current for Nordic issuers** — interim reports do
  not disclose EBIT. Net debt / EBITDA is not computable from ESEF either:
  depreciation and non-current borrowings are untagged.
- **First North, Spotlight and Nordic SME have no ESEF at all.** Figures there
  are parsed from release prose, and those issuers may report under Swedish
  GAAP K3 rather than IFRS, which changes goodwill and lease treatment.
- **Spotlight has no free price or turnover feed.** Nasdaq and NGM do.
- **No consensus estimates.** Nothing free and licensable exists, so "versus
  expectations" is always versus the company's own history.
- **No point-in-time history, so no backtest.** The toolkit carries publication
  dates and refuses to claim historical knowledge it cannot evidence. Three
  facts keep it that way: fundamentals come back restated rather than
  as-originally-reported, every universe build queries the live listing so
  survivorship bias is structural, and there is no consensus history or
  historical share register. `calibration.py` measures decisions **forward**
  from v3.0.0 — a dated verdict, a validated price and a real future close —
  and is not a backtester.
- **Price history is back-adjusted for splits, and that is measured** against
  four dated splits in both directions with no discontinuity at any effective
  date. Dividend treatment is unverified, so the series carries a price range
  and never a total return, and there is still no dividend-per-share history.

## Disclaimer

This is analysis tooling, not investment advice. Every output says so. The
figures come from primary sources but the interpretation is generated, and
neither the author nor the tool takes responsibility for decisions made with
it. Verify anything you intend to act on against the issuer's own filings.
