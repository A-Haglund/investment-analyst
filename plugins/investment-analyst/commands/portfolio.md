---
description: Review holdings and decide ADD / HOLD / TRIM / EXIT on each
argument-hint: <portfolio name>
---

Review the portfolio: **$ARGUMENTS**

Use the `investment-analyst` skill at **PORTFOLIO** depth (see `SKILL.md` §4),
relying on `scripts/portfolio_store.py`, `scripts/portfolio_review.py` and
`scripts/portfolio_metrics.py`.

**Delivered length: the action table, plus 80 words per holding** (SKILL.md §4).

A portfolio review asks what to do with what you already own. It is not twenty
stock analyses, and it asks a different question from `/screen`. A screen ranks
what you could buy. A review asks whether each holding belongs and, for those
that do, whether to ÖKA, BEHÅLL, MINSKA or SÄLJ HELT (SKILL.md §13).

## Tagging and uncertainty: SKILL.md §1 and §7.1

## Reader model: SKILL.md §7

Cap prose per holding at 80 words; any longer, the review is unreadable.

## Review process

The review runs in three layers, each answering a different question and
costing different effort:

1. **Breakers** — test each holding against its stored thesis using
   `thesis_ledger.py --evaluate`. A fired breaker short-circuits the decision to
   SÄLJ HELT regardless of price. Theses reach the ledger from `/analyze`, which
   writes one on every BUY or SELL call; a holding that has never been analysed
   has none, and layer 1 can only report that (⚪), not clear it.

2. **Alerts** — one cheap question per holding: price outside the last
   recorded range, a new report since the last review, short interest up,
   net insider selling, `valuation_gate` calling inputs stale, or no stored
   view. Alerts flag holdings that warrant deeper review.

3. **Depth** — STANDARD depth only on holdings flagged by layer 1 or 2. A clean
   holding gets `BEHÅLL — nothing has changed` **with the date it was last
   reviewed**, which is what makes that an honest answer rather than a
   skipped step.

Process:

1. **Load the portfolio.** Store it with `portfolio_store.py --name <name>`. It
   accepts pasted text from Avanza or Nordnet, or typed by hand, and resolves
   identity through `company_resolve.resolve()`, refusing ambiguous names.
   Each holding carries optional entry price, stored but never used in the
   decision.

2. **Run layer 1 and layer 2 across all holdings.** Collect breaker firings
   and alerts.

3. **Take to STANDARD depth only what layer 1 and 2 flagged.** Analyse each
   flagged holding on the full model and assign ÖKA / BEHÅLL / MINSKA / SÄLJ HELT.

4. **For unchanged holdings, state the last-reviewed date.** That date is the
   evidence for `BEHÅLL`. Without it the answer reads as skipped.

5. **Run portfolio metrics** with `portfolio_metrics.py --name <name> --json`:
   The output carries weight and price for each holding, keyed on ISIN.

6. **Open with the summary in plain prose.** Two short paragraphs, before any
   list. Name the one or two holdings that are genuinely a problem, name the
   strongest case, and say what the portfolio looks like as a whole. Someone
   who reads only this must come away with the right instruction. No tags, no
   jargon a non-specialist would have to look up.

7. **Then the actions, one block per holding**, ordered by weight. The header
   line carries the identity, the action, the weight and the price; the bullets
   carry the reasons, three or four at most, each specific to this company and
   checkable. Where a trigger applies, close the block with it.

   ```
   ## Åtgärder

   🔴 KebNi B — SÄLJ HELT · 4% · SEK 28
      - Kraftigt försämrad kassa och nyemission med stor rabatt.
      - Stor utspädning för befintliga aktieägare.
      - Omsättningen faller kraftigt.

   🟢 NIBE B — ÖKA · 11% · SEK 52
      - EBIT och marginal förbättras tydligt.
      - Värderingen har kommit ned mot bolagets egen historik.
      - Bästa risk/reward i portföljen.
      Bevaka: Q3-marginalen under 12% försvagar caset (trigger, rad 1).

   ⚪ Sagax D — FLAGGAD · 6% · SEK 27
      - Ingen lagrad tes; positionen har aldrig granskats.
      - Kort- och blankningsregistren gick inte att nå denna körning.
      - Ingen åtgärd föreslås förrän underlaget finns.
   ```

   That third block is what a missing thesis looks like, and it has a fix
   rather than an excuse: run `/analyze` on the holding. A BUY or SELL there
   writes the thesis with its breakers, and the next review's layer 1 has
   something to test. Say that in the block — a ⚪ that names its own remedy is
   an action, and one that does not is a shrug.

   The colour vocabulary is defined once, in `SKILL.md` §9, and is the same one
   every command uses: 🔴 SÄLJ HELT · 🟠 MINSKA · 🟢 ÖKA · 🟡 BEHÅLL · ⚪ flaggad.

   **⚪ is not optional.** `portfolio_review.py` returns no action whenever a
   check could not run, a thesis is missing, or the judgement needs depth the
   run did not reach. Those holdings must appear with the others and say why —
   dropping them, or quietly calling them BEHÅLL, is the failure the whole triage
   exists to prevent.

8. **Then one line, so the whole review fits in a glance.**

   ```
   SÄLJ HELT: KebNi · MINSKA: Handelsbanken, Kambi · ÖKA: NIBE
   BEHÅLL: Axfood, Betsson, Nelly · FLAGGADE: Sagax D
   ```

9. **Then the portfolio block** — Herfindahl concentration, effective number of
   positions, largest true exposure, currency exposure, downside, cash drag and
   Data Confidence.

10. **Close with "Viktigast" — what would change this reading.** The real
    limitations of this run, not a boilerplate disclaimer: the Data Confidence
    score and what it rests on, any holding whose checks could not run, the
    single largest concentration, and the one thing most likely to change the
    conclusion. Say plainly whether the recommendations are a direction or a
    verdict.

    Note what does **not** belong here: position sizes are known, because
    `portfolio_store.py` records quantity. A review that says it cannot judge
    over- or underweighting is describing a different tool.

Rules:

- **Emoji belong in the action list and nowhere else.** The no-emoji rule
  governs this plugin's documentation, not its output, and the colour is the
  fastest signal a reader gets. It carries no information beyond the action, so
  it never replaces the word.

- **The action word is Swedish, with no English token and no gloss.** `SÄLJ
  HELT`, not `EXIT (sälj)` and not `EXIT`. `SKILL.md` §13 keeps the standard
  terms in English only inside the decision record and the Evidence block —
  underlying material, printed on request — which is where cross-run,
  cross-language comparability lives; the delivered action list carries the
  Swedish word alone.

- **Prose first, list second, table never.** The action list carries identity,
  action, weight, price and reasons, so a separate action table would be the
  same numbers in a second place. One number, one home.

- **Entry price is for dating the thesis and calibrating process, never for
  the decision.** Anchoring on what you paid is the disposition effect:
  selling because you are up, holding because you are down. Unrealised result
  is printed in a separate block after the actions are decided.

- **The account is an ISK.** Two ISK facts belong at portfolio level: cash is
  taxed as if invested, so idle cash carries a real cost; and foreign
  dividend withholding is creditable only up to the schablon amount, so a
  portfolio heavy in non-Swedish payers can lose credit. Both are observations,
  not calculations. Do not state a schablon rate or tax-free allowance as a
  number.

- **Conviction caps at MEDIUM** for any holding not taken to depth: a breaker
  firing or an alert triggering is not a full analysis. Where a holding's
  decision record is emitted, `decision_record.py` enforces the ceiling
  (`references/conviction.md`) — and a fired breaker carries
  `THESIS_BROKEN`, which caps it at LOW, not MEDIUM.

- **Data Confidence** sits on the portfolio line, reflecting the lowest
  confidence among all holdings taken to depth plus the cost of the layered
  approach itself.

Where a previous review exists, name the two deepest changes since it and what
would have to happen for either to shift the decision on the largest position.

## Closing line

Close each holding's action block with triggers from the layer-1 and layer-2 review. Finish the entire review with:

```
Vill du se underlaget — siffror, värdering, källor och Evidence-block — säg **visa underlaget**.
```

**Nothing follows this line.** No trailing "Sources:" line, no bibliography, no
source list of any kind. Sources live in the Evidence block, which is
underlying material — a trailing source list is that block leaking into the
answer, and it is the single most common way this format fails.
