# Conviction — the ladder and its caps

This file is the normative source for the conviction ladder and its caps. It
is loaded at every depth of every command, on its own, so that a depth does
not have to read the rest of `data-quality.md` to get to it. The caps are
enforced in `scripts/decision_record.py`, which refuses a decision record
whose conviction exceeds the ceiling.

## Conviction

**This section is the normative source for the conviction ladder and its
caps.** SKILL.md states no cap value and points here. Where a command file
names its own depth's ceiling, it is quoting this table, not setting it.

Conviction is confidence in the analysis, not enthusiasm for the stock.

| Level | Requires |
|---|---|
| **VERY HIGH** | Data confidence ≥ 85, no conflicts, predictable business, wide margin of safety, liquid |
| **HIGH** | Data confidence ≥ 70, material figures verified, thesis rests on one or two well-understood drivers |
| **MEDIUM** | Data confidence ≥ 55, some single-source figures, or a wide scenario range |
| **LOW** | Data confidence ≥ 40, or material gaps, or an unresolved conflict, or thin liquidity |
| **VERY LOW** | Data confidence < 40, opaque accounting, unproven model, or scenario values spanning a very wide range |

### The caps are enforced, not self-applied

Until v3.0.0 the caps below were prose the model was asked to remember, and
`grep conviction scripts/*.py` returned nothing. They are now computed by
`scripts/decision_record.py`, which **refuses a decision record whose
conviction exceeds the ceiling** rather than storing it and printing a warning.

| Cap | Fires on | Ceiling |
|---|---|---|
| `CAP_DEPTH_TLDR` | depth TLDR | MEDIUM |
| `CAP_DEPTH_QUICK` | depth QUICK | MEDIUM |
| `CAP_DEPTH_COMPARE` | depth COMPARE | MEDIUM |
| `CAP_VENUE_MICROCAP` | reason code `VENUE_MICROCAP` — a microcap on First North, Spotlight or NGM | MEDIUM |
| `CAP_CONFLICT` | reason code `CONFLICT_UNRESOLVED` on a material figure | LOW |
| `CAP_THESIS_BROKEN` | reason code `THESIS_BROKEN` — a stored breaker has fired | LOW |
| `CAP_DATA_CONFIDENCE` | reason code `DATA_CONFIDENCE_LOW` — data confidence below the floor of 40 — see "Data confidence" below | LOW |

**The weakest input sets the ceiling; the caps are never averaged.** A microcap
run at QUICK depth with an unresolved conflict is capped at LOW by the
conflict, not at MEDIUM by the average of the three. The record prints every
cap that applied, not only the binding one, so a later reader can see which
constraint did the work.

Three consequences worth stating plainly:

- **A depth cap needs nothing from you.** The depth is a field on the record;
  the ceiling follows from it.
- **Every other cap keys on a reason code**, so a code you fail to attach is a
  cap that does not fire. Attaching them is part of the analysis, not
  bookkeeping — this is where a confident-looking wrong answer would still get
  through. No ESEF and no verified financials is exactly the case that belongs
  under `DATA_CONFIDENCE_LOW`.
- **The ceiling is a ceiling.** It permits a lower conviction and never raises
  one: the ladder above still has to be satisfied on its own terms.

**A strong valuation with weak evidence is `KÖP — LÅG ÖVERTYGELSE`, not a strong
buy** (SKILL.md §13). Write it that way. Hiding uncertainty behind a confident
recommendation is the specific failure this whole framework exists to prevent.

Conviction also sets a ceiling on position size, not only on the words used to
describe it. That ladder of position-size ceilings is enforced in
`scripts/position_sizing.py`, the same way the conviction caps above are
enforced in `decision_record.py`; the values live there and are deliberately
not restated here.

## Data confidence — how the score the ladder keys on is computed

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
