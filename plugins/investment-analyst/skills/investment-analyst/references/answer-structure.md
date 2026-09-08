# The underlying material and the charts

Loaded at STANDARD and DEEP. The seven sections themselves, their word budgets
and the shallow-depth shape are in `SKILL.md` §6 — every depth that writes a
sectioned answer needs those, so they are not gated here. What *is* gated is
the material below: TLDR and QUICK produce no snapshot table, no financial
statements, no scorecard and no chart.

### The underlying material — produced, printed on request

The snapshot table, the financial statements and their sparklines, moat scoring,
owners and management, the full valuation build with peers and history, the
nine-category scorecard, the **Evidence block** and the **decision record**
(§9). Every figure in the answer traces to something here.

None of it is printed unless asked for. Every run therefore closes, after
`Viktigast`, with exactly one line naming what is available:

```
Vill du se underlaget — siffror, värdering, källor och Evidence-block — säg **visa underlaget**.
```

On that request, print the underlying material in the order above, in full,
with tags — that is the one place the reader has asked for the analyst's view
rather than the answer.

**One number, one home.** The **decision record** (§9) holds the
recommendation, the conviction, the fair-value range, the expected return and
both scores; it is validated before anything is written, and the **verdict
block** is where the reader meets those numbers. The rest of the answer
references the verdict block and never restates it. Two exceptions, both
deliberate: `Viktigast` repeats Data Confidence for the reader who skips to the
end, and the Evidence block repeats what it verified when the underlying
material is printed. **Every copy is read off the record — a copy that
disagrees with it is a defect**, which is what makes the repetition a check
rather than a second opinion.

**Do not dump the raw data into the answer.** The engine collects far more than
belongs in a readable analysis. The answer carries what matters, what could go
wrong, and what would change the call. Everything else waits in the underlying
material.

Length is not evidence of rigour. A reader who cannot find the conclusion has
been given a worse product, however complete it is.

### Charts

**The answer carries one chart: the scenario range marker in section 5.**
Sparklines, the P/E range bar and the scorecard bar column belong to the
underlying material. A chart in a 700-word answer costs lines the argument
needs.

Text only. Unicode blocks and aligned columns, **nothing wider than 88
characters**, bars at most 40 cells, and every sparkline labelled with real
numbers at both ends — bare block characters are shape, not data.

Where a chart is positional, a marker must sit within one cell of its true
position. **No label may sit between the track's end caps** — a label there
occupies cells and pushes every marker after it out of true. Endpoint values
and a trailing legend may share the scale line itself, since they sit outside
the end caps and occupy no track cells — both range-marker charts do this. On
the scenario ladder, the legend sits beneath instead. If a chart cannot be
both accurate and under 88 characters, the chart is wrong — never the width
limit.

Four chart forms earn their place, and only these:

```
Revenue      99 ▃▅▇█▇▆ 120  SEK bn   FY2020->FY2025 · +3.9%/yr
EBIT margin  12.1 ▂▄▆▇█ 16.9   %     FY2020->FY2024 · no FY2025 EBIT disclosed
```

```
P/E vs own 10-year range
  8.9 ├──────────────────●────────────┤ 22.4     now 16.8 · median 14.2 · 64th pctile
```

```
  310 ──────────●─────────────├══════════┤──────────────── 540
  bear 310 · nu 356 · bas 420-470 · bull 540
```

**The scenario ladder's legend is in the reader's language**, since it is the
one chart the answer carries. `bear` and `bull` stay — they are the scenario
names in Swedish market usage too, and no native form is in circulation — but
`now` is `nu` and `base` is `bas`. Charts in the underlying material follow
that material's own convention.

The fourth is a bar column inside the scorecard table. Nothing else: no chart for a
single number, no ownership chart over a register that is explicitly a floor
rather than a total, no peer bars over a set that reports itself as low
confidence, and nothing at all in the verdict or Evidence blocks.
