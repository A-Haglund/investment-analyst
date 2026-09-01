# Swedish equities — Nasdaq Stockholm

Regulated-market issuers (Large Cap, Mid Cap, Small Cap) report under IFRS.
First North (outside Premier), Spotlight and NGM issuers may report under
Swedish GAAP instead — see "Accounting basis: IFRS or K3" below. Two structured
routes exist, plus the report PDFs.

## Source chain

### 1. ESEF / Inline XBRL — the structured route

Since FY2021 every issuer on a Swedish regulated market must file its annual
financial report in ESEF with IFRS concepts tagged — the EU-wide mandate began
a year earlier, at FY2020, but Sweden used the member-state COVID deferral
available under the ESEF regulation (`europe.md` has the EU-wide detail).
Retrieve it with:

```bash
python esef_fundamentals.py --search "Evolution" --country SE
python esef_fundamentals.py 549300SUH6ZR1RF6TA88 --filings 3
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

### 2. MFN.se — releases and report PDFs

`scripts/mfn_news.py <slug> --reports` returns interim and annual reports with
the PDF attached, in Swedish and English. This is the fastest route to the most
recent quarter, and the only route to quarterly detail.

MFN is a private distribution channel, not the regulatory archive — but it is
where the issuer publishes, so the PDF it carries is the primary document.

**MFN does not cover every Swedish issuer.** Verified 2026-08-31: Sandvik,
Atlas Copco, Hexagon and AB Volvo all return an *empty feed* — they distribute
through **Cision** instead. They still appear in MFN `--search` because other
issuers reference them, which makes the gap easy to miss. For those companies:

- annual figures → `esef_fundamentals.py --search "NAME" --country SE`
- releases → `scripts/cision_news.py --search "NAME"` then
  `scripts/cision_news.py <slug> --reports --pdf ./reports`

```bash
python cision_news.py --search "Sandvik"        # resolve the newsroom slug
python cision_news.py sandvik --reports         # interim and annual reports
python cision_news.py sandvik --reports --pdf . # save the PDFs
```

**One real difference from MFN.** Cision publishes no regulatory flag, and a
newsroom mixes MAR disclosure with marketing PR — `/se/volvo` carries Volvo
Trucks product releases next to financial reports. MFN's `:regulatory` tag has
no equivalent, so the script's labels are keyword heuristics. Confirm before
citing a Cision release as regulated information.

The two sources are complementary rather than overlapping: ESEF reaches the
regulated-market large caps, MFN reaches the small caps and growth markets where
ESEF does not apply at all.

### 2b. First North, Spotlight and NGM — no ESEF at all

These are MTFs, not regulated markets, so the ESEF mandate does not apply and
`esef_fundamentals.py` will find nothing. There is no tagged XBRL for these
companies anywhere. The MAR-regulated report release **is** the primary source.

```bash
python mfn_news.py --search "KebNi"                        # resolve the slug
python mfn_news.py kebni --reports --lang en --figures     # headline figures
python mfn_news.py kebni --reports --lang en --text        # full release body
python mfn_news.py kebni --reports --pdf ./reports         # save the PDFs
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
python insider_se.py --issuer "Volvo" --months 6
python insider_se.py --issuer "Evolution AB" --months 12
```

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
python short_se.py "Embracer"            # aggregate + named holders
python short_se.py "Elekta" --history    # the trend, which matters more than the level
python short_se.py --top 20              # most-shorted issuers
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
python nordic_shares.py "Volvo"        # sums VOLV A + VOLV B
python nordic_shares.py --universe STO # 743 listed lines across all segments
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
equity note, not from the exchange feed (see `valuation.md` for the full
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

### 5. Ownership

```bash
python ownership_se.py --isin SE0012673267
python ownership_se.py --name "Addtech"
python ownership_se.py --quarters
```

Every Swedish UCITS fund files complete line-item holdings with FI quarterly.
Reverse-indexed by ISIN this gives domestic institutional ownership: which funds
hold the name, how many shares, and what share of each fund's NAV.

Read three things beyond the list.

**Concentration** — top-1/3/5/10 share of the disclosed base plus HHI. Sandvik
is diffuse (top-5 = 27.1%, HHI 284); Evolution is concentrated (top-5 = 95.3%,
HHI 5386). Those are different ownership situations with different behaviour
under stress.

**Conviction** — a fund with 4%+ of NAV in one company has done real work, and
its view deserves engagement rather than dismissal.

**Trend** — quarter-over-quarter against one and four quarters back: funds
added, funds exited, and net share change. Draw conclusions from **share
counts, not value**, because value moves with the price. The two horizons can
disagree usefully: Sandvik is +1.5% over one quarter but −8.6% over four.

Two data traps the script handles, both of which would otherwise fabricate
turnover: a fund manager rebranding (Storebrand Fonder AB → Storebrand Asset
Management AS invented 11 exits and 11 additions in Sandvik alone), and
`Marknadsvärde_instrument` being denominated in the **fund's** reporting
currency (SEK) rather than the instrument's quote currency.

Swedish funds only. Foreign institutions, AP-fund direct holdings and private
owners are outside the register, so treat it as a **floor** on institutional
ownership. For the full picture add:

- **The annual report's ownership table** (`Ägarförteckning`) — largest holders
  with votes and capital, authoritative and annual.

`holdings.se` (Modular Finance) has no public API and is login-gated, so it is
not usable by this plugin — see `source-registry.md`. Interim ownership changes
between the quarterly FI Fondinnehav files and the annual Ägarförteckning are
`DATA NOT AVAILABLE`.

`insider_se.py` gives insider *transactions*; ownership *percentage* comes from
these. The management section needs both.

### 6. Macro and rates

**Riksbanken SWEA API** — free, official, no key. This is the Swedish
equivalent of FRED and supplies the discount-rate inputs:

```
https://api.riksbank.se/swea/v1/Observations/<series>/<from>/<to>
```

| Series | Meaning |
|---|---|
| `SEGVB10YC` | 10-year Swedish government bond — the SEK risk-free rate for DCF |
| `SECBREPOEFF` | Riksbank policy rate |
| `SEKEURPMI` | SEK/EUR |
| `SEKUSDPMI` | SEK/USD |

Use `SEGVB10YC` for any SEK-denominated DCF. **Statistics Sweden (SCB)** at
`api.scb.se` provides CPI, wages and industrial production, free.

### 7. Other Swedish sources

| Source | Use | Cost |
|---|---|---|
| **Börsdata** (borsdata.se) | Nordic fundamentals and ratios with an official API | Paid — deliberately excluded by design, see `source-registry.md` |
| **allabolag.se / Bolagsverket** | Legal entity data, board, subsidiary annual accounts | Free / per-document fee |
| **Nasdaq OMX Nordic** | Segment, share classes, index membership, turnover | Free |
| **Avanza** | IR homepage pointer and share-count cross-check, fetched by `ir_discovery.py`; the next-report date, fetched by `horizon.py` | Unofficial |
| **Nordnet** | Quotes, holder counts — not fetched by any script here; manual lookup only | Unofficial |
| **DI, Affärsvärlden, Placera** | News and commentary | Secondary tier — context only |

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

The IAS Regulation binds **regulated-market** issuers (Large Cap, Mid Cap,
Small Cap) to IFRS. It does not reach the MTFs: First North (outside Premier),
Spotlight and NGM issuers may report consolidated accounts under Swedish GAAP
**K3** instead, and many do. **First North Premier requires IFRS.**

Check the accounting-principles note before anything else — it sits on the
first page of the notes in every årsredovisning, so confirming the framework
costs nothing. Under K3:

- **Goodwill is amortised**, rather than impairment-tested. Swedish law
  presumes a **five-year** useful life where it cannot be reliably established,
  with ten years as the outer bound — read the actual period from the note
  rather than assuming either figure. The "goodwill exceeding equity" trigger
  and the impairment-test-note routine in `red-flags-and-smallcap.md` flag 10
  are written for IFRS and misfire on a K3 filer. K3 EBIT also carries an
  amortisation charge an IFRS peer's does not — restate before comparing.
- **There is no IFRS 16.** K3 chapter 20 requires **finance** leases to be
  capitalised in the consolidated accounts; only **operating** leases stay off
  balance sheet, and most K3 filers' lease exposure is operating. Do not add a
  lease liability the filing does not carry. Capitalising rent for comparison
  against an IFRS peer is an **analytical adjustment only** — done on the side,
  for the comparison — never restated onto the company's reported balance
  sheet.
- The IFRS-specific red flags that cite IAS 38, IFRS 8 and goodwill
  impairment (`red-flags-and-smallcap.md` flags 9, 10 and 15) apply only in
  their K3 form — each carries the caveat where it is stated.

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

`VOLV-B.ST` · `INVE-B.ST` · `ATCO-A.ST` · `ERIC-B.ST` · `SAND.ST` · `EVO.ST` ·
`HEXA-B.ST` · `ASSA-B.ST` · `SEB-A.ST` · `SHB-A.ST` · `ESSITY-B.ST` ·
`EPI-A.ST` · `NIBE-B.ST` · `SWED-A.ST` · `HM-B.ST`

MFN slugs are name-based and rarely match the ticker: `volvo`, `investor-ab`,
`atlas-copco`, `evolution`, `sandvik`. Resolve with
`scripts/mfn_news.py --search "NAME"` rather than guessing.
