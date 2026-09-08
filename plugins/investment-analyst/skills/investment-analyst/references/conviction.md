# Conviction — the ladder and its caps

This file is the normative source for the conviction ladder and its caps. It
is loaded at every depth of every command, on its own, so that a depth does
not have to read the rest of `data-quality.md` to get to it. The caps are
enforced in `scripts/decision_record.py`, which refuses a decision record
whose conviction exceeds the ceiling.

## 7. Conviction

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
| `CAP_DATA_CONFIDENCE` | reason code `DATA_CONFIDENCE_LOW` — data confidence below the floor of 40 (§5) | LOW |

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
