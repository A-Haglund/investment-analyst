# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

A Claude Code **plugin marketplace** containing one plugin, `investment-analyst`:
equity research on Nordic, German and French listed companies that ends in a
sourced BUY/HOLD/SELL call. The repository root *is* the marketplace
(`.claude-plugin/marketplace.json`, marketplace name `finance-local`).

There is no application to run. The deliverable is a plugin: Markdown that
instructs the model plus Python CLI scripts it calls.

## Commands

Python is stdlib-only, no dependencies, no virtualenv. In this sandbox the
interpreter is `python3` (`python` is not on PATH).

```bash
# Tests — from plugins/investment-analyst/skills/investment-analyst/tests/
python3 run_tests.py                    # offline suite: ~856 tests, ~10s
python3 run_tests.py --network          # also hit live free endpoints, ~2 min
python3 run_tests.py -k share_class     # single test / subset by method-name substring
python3 run_tests.py -v                 # per-test names
python3 -m unittest test_ttm_engine     # one file directly

# A script's own parsing edge cases (20 scripts carry one)
python3 scripts/quote.py --selftest

# Build the desktop-app zip (writes dist/, gitignored)
python3 tools/pack.py
```

Network tests are gated on `INVESTMENT_ANALYST_NETWORK_TESTS=1` and are
deliberately not in CI: hammering Finansinspektionen and Nasdaq on every push
would contradict the terms-of-service posture the plugin commits to.

### Install / reinstall loop

The plugin is **copied into `~/.claude/plugins/cache/` at install time**, so
edits in this working tree do nothing until:

```bash
claude plugin marketplace update finance-local
claude plugin update investment-analyst@finance-local
```

The cache is keyed on version, so a same-version reinstall does not reliably
refresh — bump `version` in `plugins/investment-analyst/.claude-plugin/plugin.json`
when the content changed (see `git log` for how releases are worded).

## The hard constraint

**Only free, keyless, public sources.** No paid data, no API keys, no
trials-that-later-charge, no paywall/robots.txt/rate-limit circumvention.
Bloomberg, FactSet, Capital IQ, LSEG, Refinitiv and Börsdata are excluded by
design. Sources used instead: ESEF/XBRL filings, Finansinspektionen's registers,
MFN/Cision releases, Nasdaq Nordic and NGM reference data, Riksbanken, SCB,
ESMA FIRDS, GLEIF.

Two consequences that look like gaps and are not:

- **No identifying request headers.** No contact address, no
  email-bearing User-Agent (`http_util.py` refuses to provide one). SEC EDGAR
  requires exactly that, so **US equities are out of scope** — SEC is never
  queried.
- **No point-in-time history, so no backtest.** `calibration.py` measures
  decisions *forward* from v3.0.0 and is not a backtester.

`PLUGIN-BRIEF.md` §6 lists ~20 real defects already found and fixed (share-class
market cap, insider-signal contamination, comma-vs-decimal parsing, look-ahead
bias in ESEF timestamps, and more). Read it before proposing a fix in those
areas — it is also the calibration for the level of rigour expected here.

## Architecture — two layers

### Instruction layer (Markdown, read by the model)

`plugins/investment-analyst/`
- `commands/` — six slash commands: `analyze`, `quick`, `tldr`, `compare`,
  `screen`, `portfolio`. Each is thin: it sets depth, points at SKILL.md
  sections, and does not restate their content.
- `skills/investment-analyst/SKILL.md` — the spine (~1250 lines): evidence
  tagging, source order, market routing, the six depths, the verdict block, the
  seven-section output contract, the decision-record contract, the 11 research
  phases, the Swedish default run.
- `skills/investment-analyst/references/` — 13 files loaded *on demand*
  (progressive disclosure, to control context cost). `SKILL.md` §11 maps each
  file to its phase.

Numbered sections are cross-referenced by number from commands and other
references. **A value has exactly one home** — e.g. the conviction ladder and
every cap live only in `references/data-quality.md` §7; `SKILL.md` states that a
cap exists and refuses to restate its value. Preserve that when editing: adding
a second copy of a number is the failure mode the structure exists to prevent.

### Data layer (Python, `skills/investment-analyst/scripts/`, ~40 files)

Standalone CLI tools, not a package. Grouped in `SKILL.md` §12 by role:
identity/venue, prices/fundamentals, **gates that refuse**, ownership/insiders/
macro, screening, decisions/theses/portfolios, and the shared core.

Load-bearing pieces:
- `finfact.py` — the provenance core (`FinancialFact`, `Verification`,
  `corroborate()`): value, unit, currency, period, source, tier and verification
  status are separate fields. Imported by every fetcher; never run directly.
- `decision_record.py` — pure module, no network, no filesystem. The model
  *emits* a JSON decision record; this recomputes its arithmetic identities,
  enforces the conviction ceiling from depth + reason codes, and renders the
  printed block. A record disagreeing with its own inputs is **refused**, and a
  refusal writes nothing.
- `market_universe.py` — the one universe/liquidity/returns layer both screens
  build on (extracted in v3.0.0 so it cannot drift in two copies).
- `numparse.py`, `finmath.py`, `http_util.py`, `_bootstrap.py` — the v3.0.0
  shared core, each replacing several hand-rolled copies.

Persistent user state lives outside the repo, under `~/.investment-analyst/`:
`portfolio/<name>.json`, `thesis-ledger/`, `watchlist/`, `guidance/`.

## Code conventions specific to this codebase

- **Sibling imports go by file path, never `import scriptname`.** New code uses
  `_bootstrap.load()` / `soft_load()`; tests use `helpers.load()` /
  `try_load()`. `helpers.load()` deliberately returns a *fresh* module object per
  call so one test's monkeypatching cannot leak into another's.
- `SCRIPTS_DIR` is **appended** to `sys.path`, never inserted at 0 — a file in
  `scripts/` must never shadow a stdlib module.
- **`SystemExit` does not inherit from `Exception`.** Scripts signal hard,
  user-facing failure (`DATA NOT AVAILABLE: ...`) by raising `SystemExit`, which
  is also their CLI behaviour. A soft loader that only means to degrade must
  catch `(Exception, SystemExit)` explicitly. `_bootstrap.soft_load()` is the
  correct reference implementation; roughly a dozen older per-script `_load`
  copies still exist and migrating them is a deliberately deferred task.
- **Refuse, never guess.** Every resolver refuses an ambiguous name and names
  the candidates ("Volvo" → AB Volvo *or* Volvo Car AB). Two share classes of
  one issuer are one company, though — a false refusal there is a real defect.
- **A check that could not run is `not checked`**, counted separately, never
  folded into a clean result.
- Most scripts expose `--json` and `--selftest`; keep both when adding one.
- `try_load()` returning `None` means "module not written yet"; a module that
  exists but fails to import must re-raise, so a broken refactor fails loudly
  instead of showing up as "skipped".

## One trap worth knowing before touching prices

Daily closes from `nordic_shares.price_history()` are **already back-adjusted
for splits** (measured against four dated splits in both directions). Returns,
drawdowns and own-history percentiles are correct off those closes as they
stand. **Never apply `corporate_actions.split_adjustment_factor()` to a price or
a price ratio** — that factor is for a per-share *fundamental* (EPS, DPS, BVPS),
which comes from a filing and is never restated for a split; applied to a price
it double-adjusts. Dividend treatment is unverified, so the series supports a
price range and never a total return.

## Other paths

- `MIGRATION.md` — the three breaking changes in v3.0.0 (emitted decision
  record, `/screen` replacing hand-run `screen_digest.py`, pinned price-series
  adjustment semantics).
- `scheduled/` — Windows Task Scheduler wrappers (PowerShell) for the unattended
  daily screen, plus its prompt and logs.
- `_v*-backup-*/`, `_config-backup-*/`, `dist/`, `*.zip` — gitignored; git is
  the history. Don't read the backup trees for current behaviour.

## Output-language rule (easy to break)

The answer is written in the language of the question; for a Swedish question
every word is Swedish, per the fixed term table in `SKILL.md` §13. Only two
things stay English: the fixed-shape blocks in the underlying material (decision
record, Evidence block) and the depth tokens. The scripts return English tokens
on purpose — `portfolio_review.py` returns `EXIT`/`TRIM`/`HOLD` — and copying one
straight into a Swedish answer is the most likely way the rule gets broken:
`EXIT` from the script is `SÄLJ HELT` in the answer.
