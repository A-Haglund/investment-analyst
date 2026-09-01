# Data quality — the datapoint model, conflicts, and confidence

`verification.md` covers the checks. This file defines what a datapoint *is*,
how conflicts are resolved, and how the quality of the evidence feeds the
recommendation.

The governing idea: **a cheap stock with poor evidence is not the same
opportunity as a cheap stock with verified evidence, and the output must not
present them as if they were.**

## 1. The datapoint

Every material figure carries metadata, not just a value. Material means: it
enters the model, appears in the scorecard, or supports the recommendation.

```
value                 215938000000
unit                  currency | shares | ratio | percent | per-share
currency              SEK | EUR | USD | n/a
period                FY2024 | Q2 2026 | instant 2026-03-31
as_of_date            the date the figure describes
retrieved_at          when it was fetched
published_at          when the source made it public   (see §6)
source                Annual report 2024, p.42
source_tier           1 | 2 | 3 | 4
primary_or_secondary  primary | secondary
verification_status   see §2
confidence            high | medium | low
```

In the analysis body this compresses to a tag plus a source. The full record is
what the Evidence block is built from.

```
Revenue FY2024   SEK 126,503m   FACT
  Source: ESEF tagged filing, Sandvik AB, FY2024
  Cross-check: annual report p.7
  Status: VERIFIED
```

## 2. Verification statuses

| Status | Meaning | When to use it |
|---|---|---|
| `VERIFIED` | Two independent paths agree within tolerance | The default goal for revenue, EBIT, net income, equity, share count |
| `CROSS-CHECKED` | A second source agrees but is not fully independent — e.g. the release quoting the filing | Better than single source, weaker than verified |
| `SINGLE SOURCE` | Only one source exists, and no independent check is possible | Guidance, TAM, a figure only in a press release |
| `CONFLICT` | Two sources disagree beyond tolerance and it is unresolved | Must be visible in the output, never silently resolved |
| `STALE` | The figure is older than its natural refresh cycle | A price older than one session; ESEF where a newer report exists |
| `INCOMPLETE` | A check could not run because an input was missing. This describes a check, not a figure | Never report this as a failure |
| `DATA NOT AVAILABLE` | The figure could not be obtained at all | State it; do not estimate around it silently |

Tolerance for "agree" is **1%** on reported financials, except the cash
roll-forward where FX translation justifies a wider band and the FX line must be
present before a break is asserted.

`INCOMPLETE` describes a check, not a figure: a figure whose cross-check could
not run is grouped by what its source actually earns — normally
`SINGLE SOURCE` — with the incomplete check noted inline on its line. That
keeps the `TALLY` in the Evidence block (`references/verification.md`) summing
over figures, and it matches what `verify_filing.py` already does: report
`INCOMPLETE` rather than assert a break when an equation is missing a term.

## 3. Conflict resolution

When two sources disagree, do not pick one quietly.

**Step 1 — classify the conflict by tier.**

- *Different tiers.* The higher tier wins. The figure keeps the status its
  primary source earns; note the disagreement inline on that figure's Evidence
  line — for example an indented `(tier-4 feed disagrees; higher tier wins,
  see §3)` — and state both values. A tier-4 aggregator disagreeing with a
  filing is not a finding about the company; it is a finding about the
  aggregator.
- *Same tier, both primary.* This is a real finding. Do not average, do not
  pick. Status is `CONFLICT` until explained.

**Step 2 — work through the causes, in order of likelihood.**

1. Period mismatch — a full year compared against a quarter. Check the periods
   before anything else; this is the most common false alarm.
2. Unit or scale — KSEK against MSEK, or an English thousands separator read as
   a decimal comma.
3. Continuing versus total operations.
4. Currency — the reporting currency is not implied by the listing venue.
5. Restatement — see §4.
6. Taxonomy mapping — an extension tag or a different IFRS concept.
7. Parsing error in this toolkit.
8. A genuine discrepancy in the company's own disclosure.

**Step 3 — report the outcome.** If explained, say which cause and use the
correct figure. If unexplained, the figure stays `CONFLICT`, it does not enter
the valuation, and the unresolved conflict appears in the `CONFLICT` group of
the Evidence block and reduces data confidence.

## 4. Restatements

When a new annual report's prior-year comparative differs materially from what
the previous report originally stated, that is a restatement, and it is an
analytical finding about the company rather than a data problem.

`verify_filing.py` detects this automatically for ESEF filers. Report:

```
RESTATEMENT DETECTED — Revenue FY2023
  As originally filed   SEK 118,000m
  As restated           SEK 116,400m
  Difference            SEK -1,600m   (-1.4%)
  Reason disclosed      reclassification of a divested segment (AR 2024, note 3)
```

Always use the restated figure. Always say the restatement happened. A company
that restates repeatedly, or restates without a clear explanation, has told you
something about its reporting quality that no ratio will.

## 5. Data confidence

Scored out of 100, separately from the investment score. They measure different
things: the investment score is about the company, data confidence is about how
well we know it.

| Component | Weight | What full marks looks like |
|---|---|---|
| Primary-source coverage | 30 | Every material figure from tier 1 |
| Cross-verification | 25 | Revenue, EBIT, net income, equity and share count all `VERIFIED` |
| Identity certainty | 10 | Legal entity, ISIN, LEI, orgnr, share classes and fiscal year all confirmed |
| Completeness | 15 | No material metric is `DATA NOT AVAILABLE` |
| Freshness | 10 | Latest report and a same-session price |
| Conflicts | 10 | No unresolved `CONFLICT` |

The table above is **how the score is computed, not how it is printed.** The
printed form is the `EVIDENCE` block in `references/verification.md`: the score
in its header, the grouped figures beneath it, and the `TALLY` line closing it.
There is no second `DATA QUALITY` block — the component weights are working
notes, and publishing both invites the two to drift apart.

If a component is worth showing, show it in the tally. `Cross-verified figures
7 of 9` belongs on the tally line; `Cross-verification weight 25` does not
belong anywhere in the output.

Rough calibration. A Swedish large cap with ESEF, an annual report and a live
price lands roughly 70–95. Missing interim EBIT disclosure, untagged notes, or
an ownership register that is only a floor can pull an otherwise well-covered
large cap toward 60 — as the Sandvik exemplar used throughout this repo shows,
at 61/100. A First North microcap with no ESEF, figures extracted from a
release and no short or ownership data lands 35–55. If it lands below 40, say
plainly that the evidence does not support a confident view.

## 6. Point in time

The system must distinguish **what happened** from **what was known**. Using
today's knowledge to describe a past decision is look-ahead bias, and it makes
any historical assessment worthless.

Rules:

- Record `published_at` wherever the source exposes it — MFN and Cision
  publish timestamps, FI's separate
  publication and transaction dates, the ESEF index's added date.
- A historical analysis may use only information published on or before the
  analysis date.
- Where publication date cannot be established, flag
  `POINT-IN-TIME UNVERIFIED` and do not present the analysis as what would have
  been known then.
- Never reconstruct a past view from present data and describe it as
  contemporaneous.

The toolkit does not currently store a point-in-time history. What it can do is
carry publication dates on the datapoints it fetches and refuse to claim
historical knowledge it cannot evidence. Say so rather than implying a
backtesting capability that does not exist.

## 7. Conviction

Conviction is confidence in the analysis, not enthusiasm for the stock. It is
capped by the weakest input, not averaged across them.

| Level | Requires |
|---|---|
| **VERY HIGH** | Data confidence ≥ 85, no conflicts, predictable business, wide margin of safety, liquid |
| **HIGH** | Data confidence ≥ 70, material figures verified, thesis rests on one or two well-understood drivers |
| **MEDIUM** | Data confidence ≥ 55, some single-source figures, or a wide scenario range |
| **LOW** | Data confidence ≥ 40, or material gaps, or an unresolved conflict, or thin liquidity |
| **VERY LOW** | Data confidence < 40, opaque accounting, unproven model, or scenario values spanning a very wide range |

Hard caps, regardless of anything else:

- A QUICK-depth run caps at **MEDIUM**.
- A TLDR-depth run caps at **MEDIUM**.
- A COMPARE-depth run caps at **MEDIUM** — it produces no scorecard and
  therefore no Investment Score.
- An unresolved `CONFLICT` on a material figure caps at **LOW**.
- No ESEF and no verified financials caps at **LOW**.
- A First North or Spotlight microcap caps at **MEDIUM**.

**A strong valuation with weak evidence is `BUY — LOW CONVICTION`, not a strong
buy.** Write it that way. Hiding uncertainty behind a confident recommendation
is the specific failure this whole framework exists to prevent.

## 8. What this changes in the output

Two numbers, never merged and never averaged:

- **Investment Score** — how good the opportunity looks.
- **Data Confidence** — how well we actually know it.

The verdict and the decision record carry the pair together — on the third
line of the verdict block, and again in the decision record. The Evidence
header carries Data Confidence alone. Those are the only three places, and all
three must agree on the value. Anywhere else is a fourth home for a number that
already has one.

A reader who stops after the recommendation line must still be able to see that
the second number is low. That is why the conviction — which is capped by data
confidence, per §7 — sits on the recommendation line itself rather than in a
footnote.
