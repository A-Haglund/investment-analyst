---
description: Equity research analysis of one company, ending in a BUY/HOLD/SELL call
argument-hint: <ticker or company name> [--quick | --deep]
---

Analyse: **$ARGUMENTS**

Use the `investment-analyst` skill.

**Depth** — read `SKILL.md` §4 and apply it:

- `--quick` in the arguments → QUICK (2–4 min)
- `--deep` in the arguments → DEEP (25–35 min, full DCF and reverse DCF)
- neither → **STANDARD** (8–12 min): all phases, fair value from multiples
  rather than a DCF

Strip the flag from the company name before resolving the ticker. State the
depth you are running in the first line of the output.

Open with the **verdict block** (SKILL.md §5) before any working. A reader who
stops after ten seconds must still get the call, the number, the conviction and
the data confidence.

Requirements:

- **Resolve identity before anything else.** Run the company-resolution step and
  do not begin analysis on an ambiguous name. "Volvo" is AB Volvo *or* Volvo Car
  AB — two listed companies, two sets of filings. If confidence is low, stop and
  ask.
- Route the company (US filer, Swedish regulated market, Swedish MTF, other
  Nordic, French, German) and use the matching source chain. Read
  `references/source-registry.md` for which source is authoritative per data
  type — do not decide that ad hoc.
- Fetch the price fresh and print its as-of timestamp.
- Label every material claim FACT / ESTIMATE / ASSUMPTION / OPINION.
- Write `DATA NOT AVAILABLE` for anything you cannot source. Do not estimate
  around a gap without saying you are doing so.
- Include the trigger table as `Bevakning` in the closing block — the single
  home for every
  invalidation condition, in both directions.
- Run the verification phase and publish the Evidence block, grouped by status
  and closed by the mandatory TALLY line.
- Report **Investment Score and Data Confidence separately**, and put conviction
  on the recommendation line. A strong valuation on weak evidence is
  `BUY — LOW CONVICTION`, never a strong buy.
- End with the decision record defined in `SKILL.md` §9 — numerically
  identical to the verdict block that opened the analysis — and then the
  signal line defined in the same section.
