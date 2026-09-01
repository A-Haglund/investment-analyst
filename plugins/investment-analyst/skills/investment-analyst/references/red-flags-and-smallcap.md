# Red flags and Swedish small-cap mode

Two things live here because they are the same discipline pointed at two
problems.

**Part 1** is a screen run on every company, at every depth. It catches the
patterns that precede a permanent loss of capital, and it is deliberately
mechanical: an observation crosses a stated threshold, or it does not.

**Part 2** is a posture for issuers on Small Cap, First North, Spotlight and
NGM. Applying large-cap methodology to a company with two years of history, no
tagged filing and SEK 1m of daily turnover produces numbers that look like
research and are not. The fix is not to refuse the question — it is to change
what the output claims.

---

## The wording rule — non-negotiable, read before anything else

You are writing about named public companies and named individuals. You have
access to filings and registers. You have **no access to intent**, and nothing
in the free data can establish it.

Every finding in Part 1 is published as:

```
RED FLAG — REQUIRES INVESTIGATION
  Observation       DSO rose from 62 to 91 days across FY2023-FY2025 while
                    revenue grew 4%
  Source            ESEF FY2025, tags TradeAndOtherCurrentReceivables and
                    Revenue; recomputed from the tagged values
  Could mean        looser credit terms to hold volume; a mix shift toward
                    slower-paying public-sector customers; one large late
                    account; or revenue recognised ahead of collection
  Would resolve it  the receivables ageing note in the annual report, and
                    management's answer on the next call
  Status            UNRESOLVED
```

Four parts, always: what you observed, where it is visible, the range of
innocent and non-innocent explanations, and the specific document that would
settle it.

**Never written, in any language, under any framing:** fraud, fraudulent,
cooking the books, misleading investors, manipulation, scam, lying, hiding,
covering up. Not as a hedge ("appears to be"), not as a question ("is this
fraud?"), not as a hypothetical the reader is invited to complete. A red flag is
an observation that raises the cost of being wrong. It is not an allegation, and
the analytical value is entirely in the observation.

Two consequences that follow:

- **A flag that resolves is reported as resolved.** If the ageing note shows one
  large public-sector customer on 120-day terms, write `RESOLVED — receivables
  ageing note, AR 2025 p.61` and move on. A screen that only ever accumulates
  flags is not being run honestly.
- **A flag is not a verdict.** Flags route into the devil's advocate section of
  `bear-case-and-scoring.md`, and where quantifiable, into the bear scenario.
  They do not sum to a recommendation. The one arithmetic rule: **three or more
  unresolved flags from the cash-and-earnings-quality group (§2 below) cap
  conviction at LOW**, because that combination means the reported profit and
  the cash are telling different stories and you cannot say which is right.

---

# Part 1 — The red-flag screen

## Run these first

For a Swedish issuer, four commands cover roughly half the screen before you
open a single PDF:

```bash
python scripts/insider_se.py --issuer "NAME" --months 12   # flags 17
python scripts/short_se.py "NAME" --history                # flag 18
python scripts/mfn_news.py <slug> --regulatory --limit 40   # flags 1, 16, 19
python scripts/ownership_se.py --name "NAME"                # context for 11, 12
```

Then `esef_fundamentals.py` for the ratio-based flags
in §2 and §3. Everything left over is in the notes to the annual report, and
§7 lists exactly which ones.

## §1 Capital structure and financing

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

**2. High dilution.** Trigger: **diluted share count up more than 5% in a year**
with no matching acquisition or capital programme; **more than 15% in one year**
is material at any size and for any reason. 5% is not arbitrary — it is roughly
the whole equity risk premium of a mature business, so a shareholder diluted at
that rate is left with roughly the **risk-free** return while still bearing
full equity risk. Where: the diluted
weighted-average share count on the face of the income statement, compared
across filings, and `nordic_shares.py "NAME"` for the current registered count.
Do not use the basic count and do not use a quote site.

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

This group is where the screen earns its keep. Three unresolved flags here cap
conviction at LOW.

**5. Weak FCF conversion.** Trigger: **FCF / net income below 60% averaged over
three years**, or below 80% for a business that presents itself as asset-light.
Three years, not one: a single year below 60% is usually working capital
absorbed by growth and is not a finding. Where: computed from CFO and capex,
both tagged in ESEF, so `esef_fundamentals.py` gives it directly. Follow the
cumulative accrual gap procedure in `fundamentals.md` and name the
balance-sheet line that absorbs the difference — a flag that cannot name the
line is not yet a flag.

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
`fundamentals.md`; the inputs are tagged. `worked-example.md` shows the register
for reporting this well — a receivables build during a demand ramp is not
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
accounting-principles note (`sweden.md`) to see which model applies before
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
as the outer bound (`sweden.md`) — so neither trigger applies as written; check
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
FCF as an **upper bound** and say so, exactly as `worked-example.md` extract 2
does. Pair this with flag 2: buybacks that leave the diluted count flat are
funding compensation, not returning capital, and the analysis says that in those
words.

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
filer (`sweden.md`) is a framework fact, not evidence of low concentration —
say so rather than marking the flag CLEAR.

**16. Supplier concentration.** Trigger: **one supplier above 20% of COGS**, a
**sole-source component with no qualified second source**, or a **single
manufacturing or assembly site**. This is rarely quantified anywhere: companies
disclose it narratively in the risk-factor section or not at all. Treat absence
of disclosure as unknown rather than as absence, and say `DATA NOT AVAILABLE`.
For a hardware small cap this is often the largest operational risk in the
business and the one least visible in any ratio.

## §7 External signals

**17. Unusual insider activity.** Trigger: **three or more PDMRs selling within
a 30-day window**; **any PDMR sale in the 60 days before a profit warning or a
materially weak report**; or a **CEO or CFO sale exceeding 25% of their
disclosed holding**. Where: `insider_se.py --issuer "NAME" --months 12` for
Sweden, which reads FI's Insynsregistret and covers Nasdaq Stockholm, First
North, Spotlight and NGM from 2016-07-03; BaFin Directors' Dealings for
Germany; AMF for France.

**Read the price column before reading the direction.** Verified on KebNi
2026-08-31: the register shows PDMR purchases at 0.14 and 0.19 SEK in periods
when the shares traded above 1.00 SEK. Those are warrant subscriptions or
incentive-programme exercises, not open-market conviction buys, and counting
them as insider buying inverts the signal. The same applies in reverse to sales
made to cover tax on a vesting. The script surfaces `BUY`, `SELL` and `OTHER`
from the register's own transaction-type field — use it, and where the price
sits far from the market price on that date, say what the transaction actually
was. Insider selling on its own is weak evidence in either direction; people
sell shares for reasons that have nothing to do with the company. The
*clustering* and the *timing relative to disclosure* are the signal.

**18. Rising short interest.** Trigger: **aggregate net short above 3%** is
notable; **above 5%** means a funded professional bear case exists and the
devil's advocate section must engage with it specifically; **a rise of more than
1.5 percentage points in a quarter** matters more than the level. Where:
`short_se.py "NAME"` and `short_se.py "NAME" --history` for the trend. Quote the
aggregate, not the sum of named holders — `sweden.md` documents why the named
list can understate the base by nearly half. **Absence is information and gets
stated**: verified 2026-08-31, KebNi does not appear in FI's blankningsregister
at all, meaning no holder has reported a position at or above the 0.1%
notification threshold, so the issuer does not appear in FI's aggregate file —
no professional has put capital behind the bear case. Note also what short
interest is *not*: a small-cap short base is often a convertible or
issue-related hedge rather than a directional view, and the register does not
distinguish them.

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

## §9 Reporting the screen

At QUICK depth, run flags 2, 5, 17 and 18 — the ones reachable from QUICK's own
step list — and report the count of items not screened. Flags 1 and 19 both
key off `mfn_news.py --regulatory`, which is step 6 of `SKILL.md`'s Swedish
routing table and only enters at STANDARD depth, so they defer to STANDARD
along with the rest. At STANDARD and DEEP, run all twenty. Publish a single
block, unresolved flags first:

```
RED FLAG SCREEN — 20 items
  RED FLAG — REQUIRES INVESTIGATION   2   (7 receivables, 13 CFO departure)
  RESOLVED ON INSPECTION              1   (5 conversion — Q4 shipment timing,
                                           collected in January, AR note 18)
  CLEAR                              11
  DATA NOT AVAILABLE                  6   (3, 4, 9, 10, 14, 16 — notes not read)
```

Six `DATA NOT AVAILABLE` entries on a twenty-item screen is a real statement
about how much of this company you can see, and it belongs next to the
recommendation rather than inside it.

---

# Part 2 — Swedish small-cap mode

## When this mode applies

Any issuer on **First North, Spotlight or NGM**; any issuer on the regulated
market's **Small Cap** segment; and any issuer with **fewer than three years of
reported history** or **no analyst coverage**, whichever venue it sits on. When
it applies, say so in the first line of the output alongside the depth, because
everything the reader should discount about the numbers follows from it.

`SKILL.md` already sets the depth rule: a company with very thin data does not
get a DEEP run. This section is what to do instead.

## 1. No ESEF exists — and that is not a data gap about the company

MTFs are not regulated markets, so the ESEF mandate does not reach them.
`esef_fundamentals.py` will return nothing for a First North issuer, and **that
result carries no information about the company whatsoever.** It is a fact about
the venue. Never write, imply, or let the Evidence block suggest that
financials are missing, unavailable or unusually opaque because the ESEF search
came back empty.

**Check the accounting-principles note before reading anything else.** It sits
on the first page of the notes in every årsredovisning, so confirming the
framework costs nothing. These venues are exactly where a Swedish GAAP (K3)
filer is likely to sit rather than an IFRS one — `sweden.md`'s "Accounting
basis" section has the framework detail. On a K3 filer, restate goodwill
amortisation and imputed lease costs before comparing to an IFRS peer, and
read flags 9, 10 and 15 in Part 1 with their K3 caveats rather than the IFRS
wording as written.

The primary source is the **MAR-regulated report release and the report PDF
attached to it**, exactly as `sweden.md` §2b sets out:

```bash
python scripts/mfn_news.py --search "KebNi"                     # resolve the slug
python scripts/mfn_news.py kebni --reports --lang en --figures  # headline figures
python scripts/mfn_news.py kebni --reports --lang en --text     # the release body
python scripts/mfn_news.py kebni --reports --pdf ./reports      # the statements
```

MFN coverage of these venues is good but not universal — verified 2026-08-31,
SpectraCure, Zaplox and Hamlet BioPharma all resolve to MFN slugs. Where a
search returns nothing, the issuer distributes through Cision or beQuoted, or
publishes only on its own IR page; try `cision_news.py --search "NAME"` before
concluding anything.

**Two `--figures` traps, both observed on the KebNi Q2 2026 release, both
capable of putting a wrong number in the output:**

- The extractor reports **the quarter column and the six-month column under
  identical labels**. KebNi's Q2 2026 release yields `Net sales 28,838` (the
  quarter) and `Net sales 41,881` (the half-year) as two entries with the same
  name. Read the source line printed beneath each figure — it is there for this
  reason — and confirm which period you have before anything enters a model.
- A line the extractor cannot parse cleanly comes out mislabelled. The same
  release produced `Adjusted net profit for the period -3 798` typed as a
  percentage with a `-13%` margin. That is a parse artefact, not a company
  figure. Discard it and read the PDF.

Both are consequences of a best-effort extractor working across issuer-specific
release formats, and both are why `sweden.md` rule 1 says to read the source
line before using a figure. Treat `--figures` as a fast index into the release,
never as a substitute for the statements.

Status vocabulary for these figures: the release and the PDF behind it are not
two independent paths — they are one document in two forms. That makes the
figure `CROSS-CHECKED` at best, never `VERIFIED`, in the sense
`data-quality.md` §2 defines. Say `FACT — interim report release, 2026-08-14`
and record the status honestly.

## 2. Liquidity — a recommendation you cannot act on is not a recommendation

Judge it from turnover, not from the bid-ask spread alone and never from market
cap. Nasdaq's own daily bars carry volume:

```bash
python scripts/nordic_shares.py "NAME" --history 1 --json
```

The JSON output carries `volume` on every bar (the text mode does not). Compute
**median daily turnover = median of (volume x close)** across the last twelve
months. Median, not mean: one placement day dominates a mean and flatters a
microcap badly. Verified on KebNi 2026-08-31 — 253 bars from 2025-08-26 to
2026-08-28 give a median daily turnover of roughly **SEK 1.26m** against a mean
of **SEK 1.77m**, the gap driven by a single 12.8m-share session on 2026-08-28.

**The sizing rule.** To exit inside five trading sessions at no more than 20% of
each day's volume, a position cannot exceed 5 x 0.20 = one day's median
turnover. So: **maximum defensible position ≈ one day of median turnover**, and
the analysis states that number in SEK rather than as a percentage of a
portfolio the reader has not described.

| Median daily turnover | Flag | What it changes |
|---|---|---|
| Above SEK 5m | none | Size normally |
| SEK 1m – 5m | `LIQUIDITY RISK` | State the maximum position in SEK; conviction cannot exceed MEDIUM |
| Below SEK 1m | `LIQUIDITY RISK — SEVERE` | Not institutionally investable; conviction LOW; say a position may not be exitable at the quoted price |
| More than 20 sessions in the year with zero or near-zero volume | `STALE PRICE` | The last price is not a clearing price; do not compute a margin of safety against it to two decimals |

KebNi at SEK 1.26m sits in the middle band: a SEK 1m position is approximately
one entire day's turnover in the stock. That is a fact about the position size,
not about the company, and it belongs in the recommendation rather than in a
footnote.

The bid-ask spread is a separate cost and is not in the daily bars. Fetch it
from `https://api.nasdaq.com/api/nordic/instruments/{obId}/info?assetClass=SHARES`
(browser-shaped User-Agent required — see `data-sources.md`). A spread above 2%
is a round-trip cost of 4%, which is material against most margins of safety and
must be netted off before the expected return is quoted.

## 3. Dilution and financing risk — check corporate actions before any per-share figure

Micro caps fund themselves by issuing shares. This is not a criticism; it is the
business model of a pre-profitability listed company, and the analysis should
treat the next issue as a base-case event rather than a bear-case one.

The operational rule: **before quoting any per-share figure — EPS, book value
per share, fair value per share, market cap — sweep `mfn_news.py <slug>
--regulatory` for corporate actions since the period end of the report you are
reading.** A directed issue between the report date and today makes every
per-share number in that report wrong, and nothing in the report will warn you.

Worked case. KebNi's Q2 2026 report was published 2026-08-14. On 2026-08-27 the
company announced and then completed a **directed share issue of SEK 55m** (two
releases the same day, both `[REGULATORY]`, verified on MFN 2026-08-31). Any EPS
or per-share value derived from the Q2 report and applied to today's share
register is stale by the size of that issue.

**The share count itself needs care in the days after an issue.**
`nordic_shares.py "KebNi"` returned **273,325,143 shares** on 2026-08-31, four
days after the completion release. Whether that figure already includes the new
shares depends on registration with Bolagsverket and Euroclear, which the
exchange reference data follows rather than leads. Do not assume either way.
Swedish issuers must publish a **"Total number of shares and votes"**
(`Ändring av antalet aktier och röster`) disclosure on the last business day of
the month in which the count changed — that release is authoritative, it comes
through the same MFN feed, and it is what resolves the question. Until it is
read, mark the share count `SINGLE SOURCE` and state the pending issue next to
it.

Two further items for the financing picture:

- **Warrants, convertibles and incentive programmes** are dilution already
  contracted for. They sit in the equity note and the AGM resolutions, not in
  any structured feed. Compute a **fully diluted, post-money** count and use it
  for every per-share figure; the difference from the registered count is often
  double digits on these venues.
- **The terms of the last issue are a price signal.** The discount to market at
  which an issue cleared tells you what capital costs this company. State the
  discount if the release discloses it; do not assume a customary level.

## 4. Going-concern risk and cash runway

```
monthly burn  = -(CFO - maintenance capex) over the last 12 months / 12
runway months = (cash + short-term investments + undrawn committed facilities)
                / monthly burn
```

Use twelve months of cash flow, not the latest quarter annualised — small-cap
quarterly cash flow is dominated by the timing of single orders and single
payments.

**Under 12 months of runway:**

1. State it in the **first paragraph** of the analysis, in months, with the
   as-of date of the cash balance. Not in the risk section.
2. Model a **dilutive raise in the base case**, not the bear case, sized to
   eighteen months of burn.
3. Cap conviction at **LOW**.
4. Reflect it in the Balance Sheet score, and say in the justification that the
   score reflects funding rather than leverage.

**Under 6 months:** no BUY unless a financing solution is already announced and
its terms disclosed. Where the auditor has included a material-uncertainty
paragraph, quote it and let it stand — the auditor has more information than you
do and has chosen the strongest language available to them.

Where the analysis cannot obtain the cash balance, say so rather than inferring
one. KebNi illustrates the split: the release-level data gives you the burn side
cleanly — H1 2026 operating cash flow of **−14,793 KSEK** and Q2 alone of
**−11,471 KSEK** against Q2 net sales of **28,838 KSEK** versus **33,677 KSEK**
a year earlier (all `FACT — Q2 2026 report release, 2026-08-14`) — which is an
operating burn of roughly SEK 2.5m per month across the half-year. It does not
give you the cash balance, which is in the balance sheet inside the PDF. So the
honest output is the burn rate, the SEK 55m raised on 2026-08-27, and
`DATA NOT AVAILABLE — cash balance; Q2 balance sheet not read` for the runway
itself. Do not divide 55 by 2.5 and present the answer as a runway: it ignores
issue costs, capex, working capital and any change in the burn rate, and the
number it produces looks far more precise than the inputs allow.

## 5. Governance on thin venues

Run the whole of Part 1 §5 — the flags do not get easier because the company is
small; they get more consequential, because there is no institutional owner base
to notice. Add three small-cap-specific items:

- **Concentrated insider or founder control.** Ownership above 30% by one person
  or sphere means minority holders cannot influence outcomes, and the exit
  depends on that holder's intentions. Record it as a governance fact, as
  `sweden.md` instructs for the large-cap spheres, then say plainly what it
  means for a minority position. Source: the annual report's `Ägarförteckning`.
  `holdings.se` has no public API and is login-gated, so it is not usable by
  this plugin (`source-registry.md`); interim ownership changes between the
  annual `Ägarförteckning` and the quarterly FI Fondinnehav files are
  `DATA NOT AVAILABLE`.
- **Board turnover and board capacity.** Three or more board changes in two
  years, or a board with no member holding a material stake, is a flag.
  Bolagsverket and allabolag.se give the current board and its other mandates.
- **Institutional ownership as a floor, and its absence as information.**
  Verified 2026-08-31: `ownership_se.py --isin SE0012904803` returns **two
  Swedish fund positions in KebNi totalling 32,368 shares and SEK 40,719** —
  index-driven tails, not conviction. `sweden.md` warns that the register is a
  floor because foreign institutions and private owners sit outside it; that
  warning holds. But a floor of effectively zero on a Swedish-listed,
  Swedish-domiciled company means no domestic professional has done the work and
  concluded in favour. That is worth one sentence, and it is not the same claim
  as "nobody owns this".

## 6. Disclosure quality — plan around it rather than complaining about it

What is systematically thinner on these venues, and what to do:

| Missing | Consequence | What to do instead |
|---|---|---|
| Segment reporting | No mix analysis; no way to see which line is deteriorating | Use the CEO letter's own product commentary and label it management narrative, not disclosure |
| Note detail | Most of §8 above is unanswerable | Record `DATA NOT AVAILABLE` per item; do not soften the count |
| Interim balance sheets | Runway and leverage unavailable between reports | Read the PDF; the release rarely carries them |
| Order backlog definition | "Order intake" may not be comparable period to period | Quote it, state that the definition is the company's own and unaudited |
| Analyst coverage | No consensus, no `ESTIMATE` tier at all | Say there is no consensus. Never substitute a commissioned research note as consensus — a paid-for note is company-sponsored material and sits at the bottom of the source hierarchy |
| Audited interims | Interim figures are unaudited | Label them unaudited wherever they enter a valuation, per `verification.md` |

Commissioned research deserves the explicit warning: several Swedish small caps
pay for coverage, and those notes carry price targets that read exactly like
independent ones. Check the disclosure line on the note. If the issuer paid for
it, it is company communication and it never supplies a number that enters the
model.

## 7. Valuation posture — what to do instead of a DCF

A DCF on a company with two years of history is false precision, and
`SKILL.md` already rules it out at DEEP depth. The reason is worth stating so
the rule survives contact with a user who asks for one: a two-stage DCF's value
is dominated by the terminal value, the terminal value is a function of a margin
and a growth rate the company has never demonstrated, and the output is
therefore an elaborate restatement of the analyst's prior. It is not more
rigorous than saying what you think; it is the same statement with three
decimals attached.

Do these instead, in this order:

1. **A reverse test with no terminal value.** At the current enterprise value,
   what revenue at what EBIT margin, in what year, is required to justify the
   price at a defensible exit multiple? This is the honest small-cap cousin of
   the reverse DCF in `valuation.md`, it needs no ten-year forecast, and it
   converts the question from "what is it worth" into "is that outcome plausible
   from here" — which is a question the evidence can actually address.
2. **EV/Sales and EV/gross profit against a named peer set**, computed on a
   fully diluted post-money share count and a net-debt figure adjusted for any
   issue since the balance-sheet date. State explicitly that the peers are
   larger and better funded, because they always are, and that this argues for a
   discount rather than parity.
3. **A scenario grid on the two or three variables that actually decide it** —
   typically order intake, gross margin and the terms of the next financing —
   rather than a ten-year model of everything. Three scenarios, each with the
   share count that scenario implies after its financing.
4. **Cash-adjusted EV** where the company is pre-revenue or near it: what is the
   market paying for the operating business once the cash raised is netted off.

If the user insists on a DCF, run it, label **every** input `ASSUMPTION`, and
publish the sensitivity band. **If the band spans more than roughly 3x from low
to high, say that the model does not discriminate between outcomes and do not
quote a point value from it.** A range of SEK 0.40 to SEK 3.20 is not a fair
value; it is a statement that the method does not apply here, and saying so is
the more useful answer.

## 8. Conviction ceiling

`data-quality.md` §7 sets the hard cap: **a First North or Spotlight microcap
caps at MEDIUM.** This file supplies the reason, because a rule whose reason is
understood survives cases the rule did not anticipate.

The cap is not about sourcing. The primary document exists and is obtainable —
the MAR release and the report PDF. The cap is about **corroboration**.
`verification.md` cross-check 1 requires the same figure through two independent
extraction paths, and on an MTF both paths terminate in the same document. There
is no tagged filing to check the PDF against, no ESEF restatement check, no
`verify_filing.py` run, and usually no consensus to notice an outlier. A figure
can be perfectly sourced and structurally impossible to verify at the same time,
and MEDIUM is what that combination is worth.

Push down to **LOW** where any one of these holds:

- median daily turnover below SEK 1m, or a `STALE PRICE` flag
- cash runway under 12 months
- fewer than three years of reported history
- a material figure taken only from `--figures` with the PDF unread
- three or more unresolved flags from Part 1 §2

Push to **VERY LOW** where the business model is unproven — pre-revenue, or
revenue from a single contract that has not repeated — or where the scenario
values span a very wide range, per the `data-quality.md` ladder.

**MEDIUM is a ceiling, not a floor.** Nothing here prevents a LOW-conviction
BUY, and `data-quality.md` is explicit that weak evidence does not veto a
positive call. It changes how the call is written:
`BUY — LOW CONVICTION (data confidence 44/100)`, with the position size
constrained by the liquidity rule in §2 and stated in SEK. A reader who sees
only the recommendation line must still see that the second number is low.

## 9. What the small-cap output adds

On top of the standard closing block, a small-cap analysis carries these lines.
They are short because each one is a number the reader would otherwise have to
reconstruct:

```
SMALL-CAP RISK BLOCK
  Venue                  First North Growth Market (MTF) — no ESEF filing exists
  Shares outstanding     273,325,143  (Nasdaq reference data, 2026-08-31;
                         SEK 55m directed issue completed 2026-08-27 —
                         inclusion in this count NOT CONFIRMED)
  Fully diluted          DATA NOT AVAILABLE — warrant programmes not read
  Median daily turnover  SEK 1.26m  (253 sessions to 2026-08-28)
  Max defensible size    SEK ~1.3m at 20% of volume over 5 sessions
  Liquidity              LIQUIDITY RISK
  Cash runway            DATA NOT AVAILABLE — cash balance not read;
                         operating burn ~SEK 2.5m/month (H1 2026)
  Disclosed short        NONE — absent from FI's blankningsregister
  Institutional owners   2 Swedish funds, SEK 40,719 total (FI 2026Q1)
  Analyst coverage       none identified
  Valuation basis        reverse test + EV/Sales vs peers — no DCF
```

Every line above is either a verified figure or an explicit gap. That is the
whole point of the block: on a company this thin, the shape of what you do not
know is as load-bearing as the numbers you have, and a reader who can see both
can decide how much weight the recommendation deserves.
