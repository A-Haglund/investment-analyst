# Red flags — general screen (sixteen of twenty flags)

**Flags 2, 5, 17 and 18 — plus the wording rule and the reporting rules that
govern both files — are in `red-flags-quick.md`, not here.** A STANDARD or
DEEP run must load BOTH this file and `red-flags-quick.md` to have all twenty
flags; a run that reads only this file and believes it has the full screen is
running an incomplete screen. This file holds flags 1, 3, 4, 6-16, 19 and 20.
Numbering is non-contiguous within this file by design — flags 2, 5, 17 and 18
are not missing, they live in the other file. Governance and external-signal
flags are particularly load-bearing for smaller issuers.

The wording rule that governs every flag in both files is in
`red-flags-quick.md` — read it there before anything else in this file.

---

# Part 1 — The red-flag screen

## Run these first

For a Swedish issuer, four commands cover roughly half the screen before you
open a single PDF:

```bash
python scripts/insider_se.py --issuer "NAME" --months 12   # flag 17 (in red-flags-quick.md)
python scripts/short_se.py "NAME" --history                # flag 18 (in red-flags-quick.md)
python scripts/mfn_news.py <slug> --regulatory --limit 40   # flags 1, 16, 19
python scripts/ownership_se.py --name "NAME"                # context for 11, 12
```

Then `esef_fundamentals.py` for the ratio-based flags
in §2 and §3. Everything left over is in the notes to the annual report, and
§7 lists exactly which ones.

## §1 Capital structure and financing

*(Flag 2 — High dilution — is in `red-flags-quick.md`.)*

**1. Repeated equity raises.** Trigger: **two or more issues for cash inside 24
months**, or **any** issue for cash within twelve months of management stating
that existing funding was sufficient. Where: `mfn_news.py <slug> --regulatory`
carries every Swedish issue as a MAR release (`riktad nyemission`,
`företrädesemission`, `konvertibel`). Cross-check against the financing line
of the cash-flow statement, which cannot be omitted the way a release can be
overlooked. Why it matters: an issuer returning repeatedly to the market is
not funding itself from operations, and each round resets the per-share
arithmetic of every prior estimate. The pattern also tells you the terms
available to the company, which is a harder fact about its prospects than
anything in the CEO letter.

**3. Covenant pressure.** Trigger: **headroom below 20% on any disclosed
covenant**; any waiver, reset or amendment in the last twelve months; or the
company **ceasing to disclose headroom it previously disclosed**. That third one
is the most informative and the easiest to miss — compare this year's financing
note against last year's, not against a blank page. Where: the financial-risk /
financing note in the annual report. **No free structured source. A human must
read the note** — covenants are not tagged in ESEF.

**4. Heavy near-term refinancing.** Trigger: **more than 30% of gross debt
maturing within twelve months**, or maturing debt exceeding cash plus undrawn
committed facilities plus one year of FCF. Where: the maturity table in the
financial-risk note. Why the ratio and not the leverage level: `fundamentals.md`
already makes the point that 1.5x leverage with a wall next year is riskier than
2.5x termed out to 2032. Refinancing risk is a timing problem, and the leverage
ratio is blind to timing.

## §2 Cash and earnings quality

*(Flag 5 — Weak FCF conversion, also part of this group — is in
`red-flags-quick.md`.)*

This group is where the screen earns its keep. Three unresolved flags here cap
conviction at LOW.

**6. Persistently negative FCF.** Trigger: **negative FCF in three of the last
five years** without an identified investment programme that has a stated end
date and a return case management has committed to in public. That qualifier is
the whole test. A capacity build with a disclosed completion year and a target
return is an investment; five years of negative FCF with the explanation
changing annually is a business that consumes capital. Where: same computation
as flag 5.

**7. Receivables growing faster than revenue.** Trigger: **receivables growth
exceeding revenue growth by more than 10 percentage points for two consecutive
periods**, or **DSO up more than 15% across two years**. Two consecutive
periods, because a single quarter's gap of that size is routinely produced by
one large invoice landing either side of a period end. Where: DSO from
`fundamentals.md`; the inputs are tagged. SKILL.md §1's bound rule shows the
register for reporting this well — a receivables build during a demand ramp is not
automatically a flag, but it is always the line to watch if growth decelerates.

**8. Inventory build into slowing demand.** Trigger: the **combined** condition
— **DIO up more than 20%** *while* revenue growth decelerates or turns negative.
Inventory building into accelerating demand is a supply-chain decision, not a
finding. The combination is what precedes write-downs, because inventory built
for a demand level that did not arrive is valued at a cost the market will not
pay. Where: DIO from tagged inventory and cost of sales — note that cost of
sales is **frequently untagged in Swedish ESEF filings** (`sweden.md`), so this
often needs the PDF.

**9. Aggressive capitalisation of costs.** Trigger: any of — **capitalised
development or contract costs growing faster than revenue for two years**;
**capitalised development above 25% of total R&D spend**; or the **capitalised
intangible balance exceeding one year of EBIT**. Why it matters mechanically:
every krona moved from the income statement to the balance sheet is a krona of
reported profit that did not happen this year and a krona of amortisation that
will happen later, and IAS 38 gives management real discretion over where the
line sits. Where: the intangible-assets note, specifically the internally
generated development line and its additions. **No free structured source —
ESEF tags the balance, not the policy or the additions split. A human must read
the note.** Also read the accounting-policy section for a change in
capitalisation policy; a policy change mid-series makes the trend meaningless
and must be stated. **On a K3 filer** the citation is different but the
discretion problem is the same one: K3 offers an accounting-policy choice
between capitalising and expensing development costs outright, so read the
accounting-principles note (`sweden-deep.md`) to see which model applies before
judging the trend.

## §3 Balance-sheet composition

**10. Goodwill concentration and impairment risk.** Trigger: **goodwill plus
acquired intangibles above 40% of total assets**, or **goodwill exceeding total
equity** (equivalently, negative tangible equity). The second threshold is the
severe one: at that point a single impairment can eliminate the equity base and
trip a covenant written on equity ratio (`soliditet`), which is the covenant
Swedish lenders most commonly write. Where: the balance sheet for the ratio —
tagged, so both fundamentals scripts give it. The **impairment-test note** is
where the risk actually lives: CGU allocation, the discount rate used, the
terminal growth rate, and the sensitivity disclosure stating how much headroom
exists. **A human must read that note.** Two secondary signals inside it: a
discount rate that has not moved across a rate cycle, and a headroom disclosure
that disappears from one year to the next. Also apply the `fundamentals.md` rule
of showing ROIC both including and excluding goodwill — the gap is what
management paid for growth, and a large gap alongside a large goodwill balance
is the same finding seen twice. **This flag and the impairment-test-note
routine are written for IFRS goodwill (IAS 36).** A K3 filer amortises
goodwill instead of testing it for impairment — Swedish law presumes a
five-year useful life where it cannot be reliably established, with ten years
as the outer bound (`sweden-deep.md`) — so neither trigger applies as written; check
the actual amortisation period in the note, and add the amortisation charge
back before comparing EBIT to an IFRS peer's.

## §4 Compensation

**11. High share-based compensation.** Trigger, carried unchanged from
`fundamentals.md` so the two files cannot drift: **above 5% of revenue is
material; above 10% demands an explicit adjustment in the valuation.** Add one
more: **SBC above 25% of CFO**, which is the form that matters when revenue is
small and cash flow is the binding constraint. Where: **Swedish and Nordic
ESEF filers frequently do not tag SBC** — `esef_fundamentals.py` returns
`DATA NOT AVAILABLE` and the figure lives in the annual report note on
incentive programmes. A human must read it. Until then, report owner-adjusted
FCF as an **upper bound** and say so, per SKILL.md §1's bound rule and its
gap-as-bound rule. Pair this with flag 2 (in `red-flags-quick.md`): buybacks
that leave the diluted count flat are funding compensation, not returning
capital, and the analysis says that in those words.

## §5 Governance and people

**12. Auditor change.** Trigger: **any change of audit firm outside the
statutory rotation cycle**. For EU public-interest entities the mandatory
rotation period is ten years, extendable by tender or joint audit, so a change
at year three or year six is off-cycle and warrants the flag; a change at year
ten is routine and does not. Where: the AGM notice (`Kallelse till årsstämma`)
and the nomination committee's proposal, both of which come through
`mfn_news.py <slug> --regulatory`, plus the signature page of the audit report
in each annual report. Higher-order signals sit in the audit report itself and
outrank the change: a **modified opinion**, an **emphasis of matter**, a
**material uncertainty related to going concern** paragraph, or a key audit
matter that is new this year. Those are the auditor telling you, in the only
language available to them, where they had difficulty.

**13. Management turnover.** Trigger: a **CFO departure announced without a
named successor and without a stated reason**; **two CFOs in three years**; or
**CEO and CFO both changing within twelve months**. The CFO is singled out
because `bear-case-and-scoring.md` already carries an unexplained CFO departure
as a standing sell trigger, and because the CFO is the person who signs off on
everything the screen above measures. Where: MAR releases via `mfn_news.py
--regulatory` or Cision. Note the phrasing of the release, and note when a
departure is effective immediately.

**14. Related-party transactions.** Trigger: **any loan to a board member,
executive or controlling owner** — the threshold is existence, not size; or
transactions with entities connected to the board or controlling owner **above
1% of revenue**; or **any material transaction not put to a general meeting**.
Where: the related-party note (`närståendetransaktioner`) in the annual report,
and the corporate-governance report. **No free structured source. A human must
read the note.** `allabolag.se` and Bolagsverket can corroborate shared
directorships and group structure, which is how you find the connection the note
describes in general terms. Distinguish carefully between a controlling sphere —
which `sweden.md` tells you to record as a governance fact without moralising —
and a transaction that moves value toward that sphere. The first is ownership
structure; only the second is a flag.

## §6 Concentration

**15. Customer concentration.** Trigger: **one customer above 10% of revenue**
(the IFRS 8 major-customer disclosure threshold, so it is disclosed when it
occurs); **above 20%** makes it a structural risk that belongs in the bear case
with a quantified impact; **top three above 50%** means the investment thesis is
a thesis about a customer relationship, and the analysis should say so in the
first paragraph rather than in a risk list. Where: the IFRS 8 major-customer
note and the segment note. Check whether the concentration is a *customer* or
a *contract* — a framework agreement with a renewal date is a dated risk, and
the date belongs in the invalidating-KPI table. **K3 carries no
equivalent mandatory major-customer disclosure**, so a blank note on a K3
filer (`sweden-deep.md`) is a framework fact, not evidence of low concentration —
say so rather than marking the flag CLEAR.

**16. Supplier concentration.** Trigger: **one supplier above 20% of COGS**, a
**sole-source component with no qualified second source**, or a **single
manufacturing or assembly site**. This is rarely quantified anywhere: companies
disclose it narratively in the risk-factor section or not at all. Treat absence
of disclosure as unknown rather than as absence, and say `DATA NOT AVAILABLE`.
For a hardware small cap this is often the largest operational risk in the
business and the one least visible in any ratio.

## §7 External signals

*(Flags 17 and 18 — Unusual insider activity, Rising short interest — are in
`red-flags-quick.md`.)*

**19. Repeated guidance cuts.** Trigger: **two consecutive cuts to the same
fiscal-year target**, or **any cut within 90 days of reaffirming** the same
target. Where: MAR releases via `mfn_news.py --regulatory` — a Swedish profit
warning (`vinstvarning`) is disclosable and cannot be buried in a slide deck.
Why the pattern rather than the single cut: one cut is a forecasting error,
and every company makes them. Two in a row
on the same target says management does not have visibility into its own
business, and that invalidates every forward number you would otherwise take
from them at `ESTIMATE`. Feed it into the guidance-accuracy assessment in
`moat-growth-management.md` and reflect it in the Management score.

**20. Regulatory investigations.** Trigger: existence. Any opened investigation,
inspection, dawn raid, tax reassessment, sanction procedure or enforcement
decision. Where: MAR releases via `mfn_news.py --regulatory` or
`cision_news.py`; Finansinspektionen's sanction decisions at fi.se;
Konkurrensverket for Swedish competition matters. Report the fact, the
authority, the date, the disclosed provision if any, and nothing else. Quantify
only what the company has itself provided for or what a published decision
states. An open investigation has no established outcome, and writing as though
it does is exactly the wording failure this file opens with.

## §8 Where no free source exists

Say this plainly in the output rather than letting silence imply a clean screen.
These items are **not** obtainable from any free structured source in this
plugin, and the analysis either reads the annual report note or records the gap:

| Flag | The note a human must read |
|---|---|
| 3 Covenants | Financial-risk / financing note — terms and headroom |
| 4 Maturity profile | Maturity table in the financial-risk note |
| 9 Capitalisation | Intangible-assets note; accounting-policy section |
| 10 Impairment headroom | Goodwill impairment-test note — CGUs, discount rate, sensitivity |
| 11 SBC (Nordic filers) | Incentive-programme note; not tagged in ESEF |
| 12 Audit opinion wording | The audit report itself, in the annual report |
| 14 Related parties | `Närståendetransaktioner` note; corporate-governance report |
| 15 Customers | IFRS 8 major-customer note |
| 16 Suppliers | Risk-factor narrative, if disclosed at all |

Where the note was not read, the correct entry is `DATA NOT AVAILABLE — annual
report note not read`, and it reduces Completeness in the data-confidence score
of `data-quality.md` §5. It is not a pass.

## §9 Reporting the screen — moved

The QUICK/STANDARD/DEEP flag-routing rule and the reporting rules (how to
report a finding, tally results, and fold them into the printed answer) are
shared by both red-flag files and live in `red-flags-quick.md` under
"Reporting the screen" — every depth reads them there. This file does not
restate them.
