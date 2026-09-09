# Migrating to v3.0.0

Three things changed in a way that can break an existing habit or an existing
script. Stored portfolios and theses written by 2.6 load unchanged — no
conversion step, no export, nothing to run before upgrading.

## 1. The decision record is emitted, not written

**Before.** `SKILL.md` §9 specified a machine-comparable decision record and
the model typed it. The verdict block, the signal line and that record were
three copies of the same numbers, described as a checksum and cross-checked by
nothing.

**Now.** The record is a validated object. The model emits it as JSON,
`scripts/decision_record.py` recomputes its arithmetic, and the printed block
is rendered from the validated record:

```bash
python scripts/decision_record.py decision.json --render   # the block
python scripts/decision_record.py decision.json --json     # the stored form
python scripts/decision_record.py --fixture                # a valid record to work from
```

**What you must do differently.** Nothing, if you only read the output — the
block looks the way it always did. If you have written your own prompts,
wrappers or scheduled jobs that ask for a decision record as prose, they now
get one rendered from a record, and a record that cannot be validated produces
a refusal instead of a block. The module refuses:

- an expected return or margin of safety that disagrees with the price, the
  scenario values and the weights it was given (tolerance 0.15pp);
- scenario weights that do not sum to 1;
- a conviction above the ceiling its depth and reason codes set;
- a reason code outside `decision_record.REASON_CODES`;
- an identity with neither a LEI nor an ISIN.

A refusal writes nothing. That is the point: a persisted number that disagrees
with its own inputs is worse than no record.

**Conviction caps moved with it.** They were prose the model was asked to
remember — `grep conviction scripts/*.py` returned nothing before this release.
They are now computed from the depth and the run's reason codes, capped by the
weakest input rather than averaged, and the full ladder lives in one place:
`references/conviction.md`.

**Decisions and theses are both stored, and they are different objects.**
`thesis_ledger.py --decide` files a validated decision; `--add` files a
price-free, falsifiable thesis. `/analyze` now writes both on a BUY or SELL
call, which is what gives `/portfolio`'s breaker check something to test. A
thesis never carries a price.

## 2. `screen_digest.py` is no longer the screen you run

**Before.** `screen_digest.py` was both the unattended daily digest and, in
practice, the script a person reached for when they wanted a screen.

**Now.** Run `/screen`. It drives `scripts/screen_value.py`, which goes deeper
than the digest ever did: a multi-year price history, a drawdown-and-negative-
12-month value filter, a liquidity floor, and ESEF margins on the survivors.
The universe, liquidity and returns pipeline both screens depend on has been
pulled out into `scripts/market_universe.py`, so there is one implementation
instead of two that could drift.

**What you must do differently.** Stop invoking `screen_digest.py` by hand.
It stays as the scheduled daily digest's implementation, with its wall-clock
budget and its shallow per-name check, and the flags a scheduled job passes it
still behave as before. Anything you used to read out of it as a screen result
comes from `/screen` now.

## 3. Price-series adjustment semantics are pinned down

**Before.** The docs said the daily price series was unadjusted for splits.
That claim was undated and circular: `corporate_actions.py` cited
`nordic_shares.py`'s docstring, which was the same unmeasured assertion.

**Now.** It is measured. Four dated splits in both directions — Mycronic 2:1,
Investor A/B 4:1, Bambuser 1:30 reverse, Nobia 1:10 reverse — show no price
discontinuity at the effective date. **The series from
`nordic_shares.price_history()` is back-adjusted for splits.** Dividends are
unverified either way and treated as unadjusted.

**What you must do differently.**

- A return, a drawdown-from-high or a percentile against a company's own
  history computed straight off these closes is already correct across a
  split. Remove any manual split correction you were applying.
- **Never apply `corporate_actions.split_adjustment_factor()` to a price or a
  price ratio.** That factor is for a per-share *fundamental* — EPS, dividend
  per share, book value per share — which comes from a filing and is never
  itself restated for a split. Applied to a price, it double-adjusts it.
- Still no total return and no dividend yield off this series, and a
  comparison spanning an ex-dividend date is likely wrong by the dividend.
- Rights issues are a separate matter and still need a TERP adjustment.

## What loads unchanged

- Portfolios in `~/.investment-analyst/portfolio/<name>.json`.
- Theses in `~/.investment-analyst/thesis-ledger/`. Decisions were added as a
  fourth top-level key without a schema bump, read everywhere as
  `.get("decisions") or []`, so a 2.6 file loads untouched and 2.6 can still
  read a file this version has written.
- Every command name, every command flag, and the delivered output format.
