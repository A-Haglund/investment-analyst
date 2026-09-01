---
description: Review holdings and decide ADD / HOLD / TRIM / EXIT on each
argument-hint: <portfolio name>
---

Review the portfolio: **$ARGUMENTS**

Use the `investment-analyst` skill at **PORTFOLIO** depth (see `SKILL.md` §4),
relying on `scripts/portfolio_store.py`, `scripts/portfolio_review.py` and
`scripts/portfolio_metrics.py`.

A portfolio review asks what to do with what you already own. It is not twenty
stock analyses, and it asks a different question from `/screen`. A screen ranks
what you could buy. A review asks whether each holding belongs and, for those
that do, whether to ADD, HOLD, TRIM or EXIT.

The review runs in three layers, each answering a different question and
costing different effort:

1. **Breakers** — test each holding against its stored thesis using
   `thesis_ledger.py`. A fired breaker short-circuits the decision to EXIT
   regardless of price.

2. **Alerts** — one cheap question per holding: price outside the last
   recorded range, a new report since the last review, short interest up,
   net insider selling, `valuation_gate` calling inputs stale, or no stored
   view. Alerts flag holdings that warrant deeper review.

3. **Depth** — STANDARD depth only on holdings flagged by layer 1 or 2. A clean
   holding gets `HOLD — nothing has changed` **with the date it was last
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
   flagged holding on the full model and assign ADD / HOLD / TRIM / EXIT.

4. **For unchanged holdings, state the last-reviewed date.** That date is the
   evidence for `HOLD`. Without it the answer reads as skipped.

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

   🔴 KebNi B — EXIT (sälj) · 4% · SEK 28
      - Kraftigt försämrad kassa och nyemission med stor rabatt.
      - Stor utspädning för befintliga aktieägare.
      - Omsättningen faller kraftigt.

   🟢 NIBE B — ADD (köp/öka) · 11% · SEK 52
      - EBIT och marginal förbättras tydligt.
      - Värderingen har kommit ned mot bolagets egen historik.
      - Bästa risk/reward i portföljen.
      Bevaka: Q3-marginalen under 12% försvagar caset (trigger, rad 1).

   ⚪ Sagax D — FLAGGAD · 6% · SEK 27
      - Ingen lagrad tes; positionen har aldrig granskats.
      - Kort- och blankningsregistren gick inte att nå denna körning.
      - Ingen åtgärd föreslås förrän underlaget finns.
   ```

   The colour vocabulary is defined once, in `SKILL.md` §9, and is the same one
   every command uses: 🔴 EXIT · 🟠 TRIM · 🟢 ADD · 🟡 HOLD · ⚪ flagged.

   **⚪ is not optional.** `portfolio_review.py` returns no action whenever a
   check could not run, a thesis is missing, or the judgement needs depth the
   run did not reach. Those holdings must appear with the others and say why —
   dropping them, or quietly calling them HOLD, is the failure the whole triage
   exists to prevent.

8. **Then one line, so the whole review fits in a glance.**

   ```
   SÄLJ: KebNi · MINSKA: Handelsbanken, Kambi · ÖKA: NIBE
   HOLD: Axfood, Betsson, Nelly · FLAGGADE: Sagax D
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

- **The action token stays in English, the gloss is Swedish.** `EXIT (sälj)`,
  not `SÄLJ`. `SKILL.md` §13 keeps the standard terms in English so the output
  stays comparable across companies and across runs; the parenthetical is for
  the reader, the token is for the record.

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

- **Conviction caps at MEDIUM** for any holding not taken to depth. A breaker
  fires or an alert triggers, but until the holding is analysed in full,
  conviction is capped.

- **Data Confidence** sits on the portfolio line, reflecting the lowest
  confidence among all holdings taken to depth plus the cost of the layered
  approach itself.

Where a previous review exists, name the two deepest changes since it and what
would have to happen for either to shift the decision on the largest position.
