# Swedish equities — Nasdaq Stockholm

Regulated-market issuers (Large Cap, Mid Cap, Small Cap) report under IFRS.
First North (outside Premier), Spotlight and NGM issuers may report under
Swedish GAAP instead — see `references/sweden-deep.md`'s "Accounting basis:
IFRS or K3" section. Two structured routes exist, plus the report PDFs.

## Source chain

### 1. ESEF / Inline XBRL — the structured route

Since FY2021 every issuer on a Swedish regulated market must file its annual
financial report in ESEF with IFRS concepts tagged — the EU-wide mandate began
a year earlier, at FY2020, but Sweden used the member-state COVID deferral
available under the ESEF regulation (`europe.md` has the EU-wide detail).
Retrieve it with:

```bash
python esef_fundamentals.py --search "Evolution" --country SE --json
python esef_fundamentals.py 549300SUH6ZR1RF6TA88 --filings 3 --json
```

Two limits, both real:

- **The index lags.** As of 2026-08-31 the newest Swedish filings on
  filings.xbrl.org were FY2024, while French filings had reached FY2025. Never
  assume the latest year is present — check `period_end`.
- **Only primary statements are tagged.** Cost of sales, gross profit, SBC,
  payables and lease liabilities are frequently absent. The script reports these
  as `DATA NOT AVAILABLE`; get them from the PDF.

ESEF covers **annual** reports only. Quarterly figures always come from the
interim report.

**§2, the general MFN.se/Cision route to the latest quarter, is STANDARD/DEEP
material (step 6 below) — see `references/sweden-deep.md`.** §2b continues the
numbering because other files cite it by that name; it stands on its own.

### 2b. First North, Spotlight and NGM — no ESEF at all

These are MTFs, not regulated markets, so the ESEF mandate does not apply and
`esef_fundamentals.py` will find nothing. There is no tagged XBRL for these
companies anywhere. The MAR-regulated report release **is** the primary source.

```bash
python mfn_news.py --search "KebNi" --json                        # resolve the slug
python mfn_news.py kebni --reports --lang en --figures --json     # headline figures
python mfn_news.py kebni --reports --lang en --text --json        # full release body
python mfn_news.py kebni --reports --pdf ./reports --json         # save the PDFs
```

`--figures` extracts the headline numbers from the release body and **prints the
raw source line beneath each one**. It is deliberately best-effort: release
formats vary widely between issuers, so a misparse is possible — the source line
is there so you can see it. Rules for using it:

1. **Read the source line before using a figure.** If it does not support the
   number, discard the number.
2. Figures are scaled to absolute units (a KSEK line and an MSEK line both come
   out in SEK) so they are directly comparable.
3. A bracket containing a percentage is a **margin**, not a prior-year
   comparative; it is reported separately as `margin`.
4. Where the extractor finds nothing, fall back to `--text`, and to the PDF for
   the full statements. Large caps often publish a short release that only
   points at the PDF.
5. These figures come from the release, not from an audited tagged filing.
   Label them `FACT — interim report release, <date>` and note that the full
   statements are in the attached PDF.

Expect thinner coverage generally on these venues: lighter disclosure
requirements, no ESEF, sparse or no analyst coverage, and wide spreads. A
`Conviction: Low` verdict is often the honest one.

### 3. Finansinspektionen — the regulatory sources

| Register | Contents | Access |
|---|---|---|
| Insynsregistret | PDMR insider transactions (MAR Art. 19), classified and trended | `scripts/insider_se.py` |
| Blankningsregistret | Net short positions | `scripts/short_se.py` — three keyless ODS endpoints |
| Prospektregistret | Prospectuses, rights issues | fi.se |

### Insider activity — read the classification, not the total

```bash
python insider_se.py --issuer "Volvo" --months 6 --json --summary
python insider_se.py --issuer "Evolution AB" --months 12 --json --summary
```

`--summary` drops the per-transaction rows from the JSON payload and keeps
only the aggregate below (net direction, counts, totals, windows, source and
as-of) — everything this section reads. Drop `--summary` when you need the
individual PDMR/date rows instead, e.g. for the red-flag #17 thresholds
("three or more PDMRs selling within a 30-day window").

The register mixes decisions with mechanics. Option exercises, RSU allotments,
sell-to-cover, internal transfers, pledges and rights-issue subscriptions all
appear as transactions, and none of them is a view on the share price. The
script separates them:

- **DISCRETIONARY** — open-market buys and sells of the share. This is the signal.
- **MECHANICAL** — compensation and administration. Carries no view.
- **DERIVATIVE** — the same decision expressed in a warrant or option.

The headline net is discretionary-only. Why this matters, from the live data:
Evolution's raw register total over twelve months is **+293 MSEK net buying**.
The discretionary signal over the same period is **−87 MSEK**, all sells, one
board member, ten tickets. The raw figure inverts the conclusion.

Also reported: role split (CEO / CFO / board / other, with "deputy" excluded
from CEO), distinct insider counts, median ticket size, and rolling 30/90/365-day
net against the equally long prior window with an ACCELERATING / DECELERATING
read. Mixed-currency issuers use the dominant currency and name what was
excluded rather than adding SEK to EUR.

A KebNi caution from the same data: PDMR "buys" priced at 0.14–0.19 SEK while
the share traded above 1.00 are warrant exercises, not conviction purchases.
Always check the price against the market price of the day.

### Short interest — run this on every Swedish bear case

```bash
python short_se.py "Embracer" --json            # aggregate + named holders
python short_se.py "Elekta" --history --json    # the trend, which matters more than the level
python short_se.py --top 20 --json              # most-shorted issuers
```

A named professional filing a short position with the regulator is the
strongest free evidence a bear case can have. Three rules for reading it:

1. **Quote the aggregate, not the sum of named holders.** Holder names appear
   only at 0.5% and above; the aggregate file includes everything from 0.1%.
   The gap is large — Embracer showed 7.98% aggregate against 4.50% from four
   named holders, so the named list understated the short base by nearly half.
2. **Read the trend.** A short base building into a results date is a different
   signal from a stable one. The script reports 30- and 90-day change plus a
   per-holder table (NEW / RE-ENTERED / INCREASED / REDUCED / CLOSED), and ranks
   holders with each one's all-time peak. Embracer's named base fell from 6.06%
   to 4.49% over thirty days — shorts covering, which reads very differently
   from the static 7.98% aggregate.

   **The trend is computed on the named ≥0.5% base, not the aggregate.** FI
   publishes the aggregate as a single snapshot with no history, so no aggregate
   time series exists anywhere. The output says so in three places; do not
   present the named trend as the aggregate trend.
3. **Absence is information.** A company with no entry has no disclosed short
   interest at all. Say that explicitly rather than leaving the section blank.

`<0,5` in the history is a sentinel meaning the holder fell below the disclosure
threshold — the position was cut or closed, not reduced to 0.5%.

Cite an issuer as
`https://www.fi.se/sv/vara-register/blankningsregistret/emittent?id=<LEI>`.

### 4. Shares outstanding and market cap — get this from the exchange

The most common silent error in Swedish analysis. Most large caps carry two
listed classes, and counting only the liquid one understates market cap and
makes every multiple look cheap.

```bash
python nordic_shares.py "Volvo" --json        # sums VOLV A + VOLV B
python nordic_shares.py --universe STO --json # 743 listed lines across all segments
```

Nasdaq Nordic's own reference data, keyless. Verified against issuers' statutory
disclosures: Evolution 199,226,613 exact; Volvo A+B 2,033,451,933, within 151
shares of the company's own filing.

**The one trap it cannot see: unlisted share classes.** NIBE returns
1,782,936,128 listed B shares, but the registered total also includes an
unlisted A class — roughly 12% more. Fenix Outdoor is the same shape. The script
warns whenever it finds only one class; when it does, confirm against the
issuer's latest **"Total number of shares and votes"** disclosure (also seen
as "Total number of voting rights and capital") before using the figure.

The register gives registered shares **including treasury** — usable as the
registered total, but treasury holdings must be subtracted before computing
market cap, EV or any per-share figure. The company holds them against itself;
they carry no external claim and are not outstanding. Get the treasury count
from the issuer's own "Total number of shares and votes" disclosure, or the
equity note, not from the exchange feed (see `valuation-core.md` for the full
market-cap definition).

The size of the error is not trivial. Take 1,000m registered shares of which
30m are treasury, price SEK 100, EBIT SEK 6bn and no net debt: the correct
970m shares outstanding gives a market cap (and EV) of SEK 97.0bn and EV/EBIT
of 16.2x, against SEK 100.0bn and 16.7x if the 1,000m registered count is left
in unadjusted — a 3% overstatement carried through every EV multiple, through
P/FCF, and through the DCF per-share bridge. Swedish large caps do hold
treasury shares for incentive programmes — Atlas Copco among them — so this is
not a theoretical case.

The exchange note field also carries **Observation status**, which is a
surveillance flag worth reporting. Evolution currently carries one.

Institutional ownership (§5), macro/rates for DCF (§6) and secondary sources
(§7) are STANDARD/DEEP material — see `references/sweden-deep.md`.

## Market segments

| Segment | Definition | Analytical implication |
|---|---|---|
| Large Cap | Market cap > EUR 1bn | Good coverage, liquid, efficiently priced |
| Mid Cap | EUR 150m – 1bn | Thinner coverage; more mispricing |
| Small Cap | < EUR 150m | Sparse coverage, wide spreads, liquidity constraints |
| First North | Growth market, **not** a regulated market | Lighter disclosure; **no ESEF requirement** — PDFs only |
| Spotlight / NGM | Alternative venues | Same caution, often more so |

Below Large Cap, check average daily turnover before treating any position size
as realistic.

## Accounting basis: IFRS or K3

The K3 goodwill-amortisation, lease and red-flag-caveat rules (STANDARD/DEEP)
are in `references/sweden-deep.md`'s "Accounting basis: IFRS or K3" section.

## Reporting conventions

- **Fiscal year — verify it, do not assume.** Most Swedish companies use the
  calendar year, but a meaningful set does not: H&M (Dec–Nov), Sectra,
  Addtech, Lagercrantz, Clas Ohlson, Systemair and others. Check the fiscal
  year end in the report before labelling any period or computing YoY.
- **Quarters**: for calendar-year filers, Q1 Jan–Mar, Q2 Apr–Jun, Q3 Jul–Sep,
  and a **bokslutskommuniké** (year-end report) instead of a Q4 report. The full
  **årsredovisning** follows weeks later and contains the notes — the year-end
  release alone is not enough for balance-sheet detail.
- **The five-year summary.** Nearly every Swedish annual report contains a
  `Flerårsöversikt` / five-year summary table with revenue, margins, returns and
  per-share data already assembled. Use it — it saves reading five PDFs, and it
  is the company's own audited presentation. Verify the latest year against the
  primary statements.
- **Currency**: SEK unless stated. Many industrials report in SEK but earn in
  EUR and USD, so FX is often a large part of reported growth. Companies
  normally disclose organic versus currency versus M&A; use that split.
- **IFRS 16 leases** are on balance sheet for IFRS filers — see the lease
  section in `fundamentals.md` before comparing a Swedish company against a
  peer under a different lease-accounting framework. K3 filers have no IFRS 16
  equivalent; see "Accounting basis" above.
- **Dividends**: approved at the AGM (`årsstämma`) in spring. Historically a
  single annual payment, but many large caps now split it into two or four
  instalments — Atlas Copco, Epiroc, Essity and Sandvik among them. Check the
  dividend policy; do not annualise one instalment as if it were the full year,
  and do not assume a single ex-date.
- **Share classes**: A and B differ in voting rights (commonly 10:1). B is
  usually the liquid class. Market cap must count **all** classes — using only
  the B-share count understates it materially.
- **Voting control**: Swedish large caps are frequently controlled by a sphere —
  Wallenberg (Investor AB), Industrivärden, Lundberg, Latour. State it plainly
  as a governance fact: it brings long-termism and reduces takeover probability.
  Note it; do not moralise about it.

## Swedish terms in filings

| Swedish | English |
|---|---|
| Nettoomsättning | Net revenue |
| Rörelseresultat | Operating profit (EBIT) |
| Rörelsemarginal | Operating margin |
| Resultat efter finansiella poster | Profit after financial items |
| Periodens resultat | Net profit for the period |
| Resultat per aktie | Earnings per share |
| Kassaflöde från den löpande verksamheten | Cash flow from operating activities |
| Fritt kassaflöde | Free cash flow |
| Investeringar | Capital expenditure |
| Eget kapital | Equity |
| Nettoskuld | Net debt |
| Soliditet | Equity ratio (equity / total assets) |
| Avkastning på eget kapital | Return on equity |
| Avkastning på sysselsatt kapital | Return on capital employed |
| Organisk tillväxt | Organic growth |
| Jämförelsestörande poster | Items affecting comparability |
| Flerårsöversikt | Five-year summary |
| Ägarförteckning | Shareholder register |
| Utdelning | Dividend |
| Återköp av egna aktier | Share buyback |
| Delårsrapport | Interim report |
| Bokslutskommuniké | Year-end report |
| Årsredovisning | Annual report |
| Insynshandel | Reported insider trading |
| Vinstvarning | Profit warning |
| Nyemission | Share issue |
| Företrädesemission | Rights issue |

`Jämförelsestörande poster` is the Swedish add-back line. Apply the same
scrutiny as to any "non-recurring" item: if it recurs, it is an operating cost.

`Soliditet` is quoted far more often than net debt/EBITDA in Swedish reports.
Compute the leverage metrics yourself for comparability.

## Valuation notes

- Discount SEK cash flows at a SEK rate, using Riksbanken's `SEGVB10YC` for the
  risk-free component.
- Swedish quality industrials have historically traded at a premium to European
  peers. Use Nordic peers where they exist.
- For sphere-controlled companies a takeover premium is unlikely to be
  realised — do not put one in the bull case without saying why it could happen.
- **Investment companies** (Investor, Industrivärden, Latour, Lundbergs) are
  valued on **net asset value and the discount to NAV**, not P/E. Switch to a
  NAV framework: substance value per share, the historical discount range, and
  where the current discount sits in it.

## Common tickers

A ticker cheat-sheet and MFN-slug resolution notes are in
`references/sweden-deep.md`.

## The default run — step order

*Moved here from SKILL.md §14: it is Swedish-specific routing, and this file is already loaded for exactly the runs that need it.*

When the user asks for a Swedish company by name, this is the order. Each step
either produces a dated, sourced figure or a stated gap — never a silent one.

| # | Step | Tool |
|---|---|---|
| 1 | Resolve legal entity, ISIN, LEI, orgnr, share classes, currencies, fiscal year | `company_resolve.py` |
| 2 | Identify venue and whether ESEF applies | `venues_se.py` |
| 3 | Locate the issuer's own IR site | `ir_discovery.py` |
| 4 | Price with timestamp; shares outstanding across all classes | `quote.py`, `nordic_shares.py` |
| 5 | Annual financials | `esef_fundamentals.py`, or the report PDF on an MTF |
| 6 | Latest quarter and regulatory releases | `mfn_news.py` or `cision_news.py` — see `references/sweden-deep.md` §2 for the Cision gap |
| 7 | Corporate actions and the dilution log | `corporate_actions.py` |
| 8 | Insider activity, classified | `insider_se.py` |
| 9 | Short interest and its trend | `short_se.py` |
| 10 | Institutional ownership and its trend | `ownership_se.py` — see `references/sweden-deep.md` §5 |
| 11 | Financial targets and the delivery record | `guidance_track.py` |
| 12 | Peer set | `peers_se.py` |
| 13 | DCF inputs and industry benchmark | `macro_se.py` — see `references/sweden-deep.md` §6 |
| 14 | Verification: restatements, ties, cross-checks | `verify_filing.py` |
| 15 | Red-flag screen | `references/red-flags-quick.md` + `references/red-flags-general.md` (+ `references/red-flags-smallcap.md` for MTF/small-cap issuers) |
| 16 | Valuation, reverse DCF, scenarios, scorecard, recommendation | — |
| 17 | Emit and validate the decision record; on a BUY or SELL, seed the thesis | `decision_record.py`, `thesis_ledger.py` |

QUICK runs 1, 2, 4, 5, 8, 9, the recommendation and step 17. COMPARE runs the
same steps plus the moat assessment and a light bear/base/bull scenario build —
neither has its own numbered step in this table, since this list is the
Swedish data-gathering sequence, not the analysis phases. STANDARD adds 3, 6,
10, 12, 14, 15, and step 16 without the reverse DCF. DEEP adds 7, 11, 13 and
the reverse DCF.

Step 17 runs at every depth, in the depth-appropriate form: a QUICK or TLDR
record carries no scenarios and no scorecard and says so with a reason code
(§9), and only a BUY or SELL at STANDARD or DEEP seeds a thesis.

Two things are never skipped at any depth: **step 1**, because analysing the
wrong entity fast is worse than analysing the right one slowly, and **the honest
statement of what could not be obtained**.
