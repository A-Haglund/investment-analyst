# European coverage — Nordics, Germany, France

Scope of this skill: **the Nordics (SE, NO, DK, FI), Germany and France.**
Other European markets work through the same ESEF route where indexed, but they
are not the target and are not verified. US equities are out of scope — see
`SKILL.md` §3.

Sweden has its own file — `sweden.md` — because it has the deepest free source
chain. This file covers the rest.

## Routing

| Market | Structured data | Releases and reports | Insiders |
|---|---|---|---|
| **Sweden** | `esef_fundamentals.py` | `mfn_news.py` | `insider_se.py` |
| **Norway** | `esef_fundamentals.py --country NO` | Oslo Børs NewsWeb; MFN | Oslo Børs / Finanstilsynet |
| **Denmark** | `esef_fundamentals.py --country DK` | MFN; Nasdaq Copenhagen | Finanstilsynet |
| **Finland** | `esef_fundamentals.py --country FI` | MFN; Nasdaq Helsinki | Finanssivalvonta |
| **France** | `esef_fundamentals.py --country FR` | AMF; Euronext; IR | AMF |
| **Germany** | **not in ESEF index — see below** | Bundesanzeiger; EQS/DGAP; IR | BaFin |

## ESEF — the European XBRL route

Since FY2020, issuers on EU/EEA regulated markets file their annual financial
report in Inline XBRL with IFRS concepts tagged — a member-state COVID
deferral let some countries push their first filing year to FY2021, which is
why `sweden.md` gives FY2021 for Sweden rather than FY2020. Block-tagging of
the **notes**, the second phase of the mandate, began a year later still, at
FY2022. XBRL International aggregates these filings at `filings.xbrl.org`.

```bash
python esef_fundamentals.py --search "Hermes" --country FR
python esef_fundamentals.py 969500Y4IJGHJE2MTJ13 --filings 3
```

Verified filing counts, 2026-08-31:

| FR | SE | NO | DK | FI | AT | NL | BE | IT | ES | LU | **DE** | **IE** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1178 | 1415 | 958 | 2126 | 1168 | 601 | 656 | 709 | 872 | 542 | 266 | **0** | **0** |

Three limits that matter:

1. **Annual reports only.** Quarterly figures never come from ESEF — use the
   interim report PDF.
2. **The index lags, unevenly.** French filings had reached FY2025 while Swedish
   ones stopped at FY2024. Always check `period_end` before assuming the latest
   year is present.
3. **Primary statements only.** ESEF Phase 1 mandates tagging of the income
   statement, balance sheet, cash-flow statement and equity statement — not the
   notes. Cost of sales, SBC, lease liabilities and segment detail are often
   untagged. Issuers may also use their own **extension concepts** invisible to
   a standard-taxonomy lookup. The script lists what it could not find as
   `DATA NOT AVAILABLE`; read the PDF for those, never infer them.

### Resolving a company to its filer

Use `--search NAME --country XX`, which searches the ESEF index itself. Do not
resolve through GLEIF alone: a group holds several LEIs and only one of them is
the ESEF filer. Evolution AB is the example — its GLEIF legal-entity LEI
(`894500KNUA250CNXIT79`) has no filings, while its ESEF filer LEI
(`549300SUH6ZR1RF6TA88`) has several.

## Germany — the exception

German issuers file their ESEF reports with the **Bundesanzeiger**, which is not
harvested by filings.xbrl.org. `esef_fundamentals.py` returns nothing for a
German company, and says so. Confirmed for SAP SE.

Use this chain instead:

1. **Company IR page** — Geschäftsbericht (annual report), Quartalsmitteilung
   / Zwischenbericht (interim), Investor presentations. German large caps
   publish full English versions. This is the primary source.
2. **Bundesanzeiger** (`bundesanzeiger.de`) and **Unternehmensregister**
   (`unternehmensregister.de`) — the statutory archive for annual accounts,
   including unlisted GmbHs. Search UI, no clean public API; use it manually.
3. **EQS News / DGAP** (`eqs-news.com`) — the German distribution channel for
   ad-hoc disclosures under MAR Art. 17. The counterpart to MFN.
4. **BaFin** (`portal.mvp.bafin.de`) — Directors' Dealings (the German PDMR
   register), voting-rights notifications, short positions.
5. **Deutsche Börse** (`deutsche-boerse-cash-market.com`) — index membership,
   segment (Prime Standard / General Standard / Scale), turnover.

For German fundamentals, extract from the annual report PDF and cross-check the
income statement against the company's own multi-year summary
(`Mehrjahresübersicht`), which most Geschäftsberichte include.

German tickers on Yahoo use the `.DE` suffix: `SAP.DE`, `SIE.DE`, `ALV.DE`,
`BMW.DE`, `MBG.DE`, `DTE.DE`. Xetra is the reference venue.

## France

`esef_fundamentals.py --country FR` works well and is the most current of the
European markets in the index.

Supplementary sources:

- **AMF** (`amf-france.org`) — the regulator. Décisions et informations
  financières, plus the register of Déclarations de dirigeants (PDMR).
- **info-financiere.fr** — the French officially appointed mechanism for
  regulated information.
- **Euronext Paris** (`live.euronext.com`) — segment, index membership,
  turnover.
- Company IR: Document d'enregistrement universel (URD) is the French annual
  report and is unusually comprehensive.

Yahoo suffix `.PA`: `MC.PA` (LVMH), `RMS.PA` (Hermès), `OR.PA` (L'Oréal),
`AIR.PA`, `SU.PA`.

## Norway, Denmark, Finland

All three are in the ESEF index and all three appear on MFN.se, which is a
Nordic feed rather than a Swedish one — confirmed for Novo Nordisk (DK), Nokia
(FI) and DNB Bank (NO).

```bash
python mfn_news.py --search "Novo Nordisk"
python mfn_news.py novo-nordisk --reports
```

Additional national sources:

| Country | Releases | Insiders / regulator | Yahoo suffix |
|---|---|---|---|
| Norway | Oslo Børs NewsWeb (`newsweb.oslobors.no`) | Finanstilsynet; primary insiders published via NewsWeb | `.OL` |
| Denmark | Nasdaq Copenhagen; MFN | Finanstilsynet | `.CO` |
| Finland | Nasdaq Helsinki; MFN | Finanssivalvonta | `.HE` |

Norway is **not** in the EU but is in the EEA, so MAR and the Transparency
Directive apply and ESEF is required. Note that Norway is outside the euro and
reports in NOK, and that Oslo's index is heavily weighted to energy and
shipping — peer selection needs care.

### Short-selling registers

All three publish a net short-position register under the EU Short Selling
Regulation, but **not** on Sweden's thresholds. Under the SSR itself, a
position holder must notify the competent authority at 0.1% of the issuer's
share capital — that notification is private and triggers no publication at
all — and public disclosure follows only at 0.5% per holder. The per-issuer
**aggregate** that `short_se.py` quotes for Sweden, published from 0.1%, is a
Finansinspektionen practice with no counterpart in Norway, Denmark or Finland:
for those three, only named holders at or above 0.5% are public, and the
aggregate short base is `DATA NOT AVAILABLE`. None of the three is wired into
a script in this plugin, unlike `short_se.py` for Sweden.

| Country | Regulator | Register |
|---|---|---|
| Norway | Finanstilsynet | Short-sale register, reported to be at `ssr.finanstilsynet.no` — endpoint unverified |
| Denmark | Finanstilsynet | Net short-position register — endpoint unverified |
| Finland | Finanssivalvonta | Net short-position register — endpoint unverified |

Each regulator publishes its own file in its own format. Treat the entries
above as documented but unverified until a script actually reads one — do not
assume the Swedish ODS format, or any other, carries over.

## Currency traps

The reporting currency is **not** implied by the listing venue. Check it every
time; the scripts print it.

- **Evolution AB** — listed in Stockholm, quoted in SEK, **reports in EUR**.
- Several Nordic industrials and shipping companies report in **USD**.
- Novo Nordisk reports in **DKK** but is widely quoted in USD via its ADR.

Rules:

1. Take the reporting currency from the filing, not the quote.
2. Compute margins and returns in the reporting currency — they are ratios and
   FX cancels.
3. Convert only at the final step, when comparing a per-share value to a market
   price, and state the FX rate and its date.
4. Discount cash flows at a rate in their own currency (see `valuation.md`).

## Accounting basis

Regulated-market issuers in all of these markets report under **IFRS**. MTF
issuers across the Nordics may report under national GAAP instead — Swedish
K3 is the case this plugin documents in depth (`sweden.md`'s "Accounting
basis" section), but Euronext Growth Oslo and First North Denmark and Finland
issuers commonly report under Norwegian, Danish or Finnish national GAAP as
well. For a Norwegian, Danish or Finnish MTF issuer, read the
accounting-principles note before assuming IFRS. Before any cross-framework
comparison, read the lease section in `fundamentals.md` — IFRS 16 inflates
EBITDA relative to a framework that leaves operating leases off balance sheet
(K3 among them), and EV/EBITDA comparisons across frameworks are not like for
like. Prefer **EV/EBIT**.
