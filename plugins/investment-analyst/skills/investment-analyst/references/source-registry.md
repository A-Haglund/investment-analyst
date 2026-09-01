# Source registry — which source is authoritative for what

`data-sources.md` lists the endpoints. This file decides **who wins**. When two
sources disagree about the same figure, the answer must not depend on which one
happened to be fetched first.

Every source carries a tier. Every data type has a named authority and a named
fallback. A figure taken from a lower tier when a higher tier was available is a
defect, not a shortcut.

## The tiers

| Tier | Meaning | Examples |
|---|---|---|
| **1 — Primary / regulatory** | The issuer's own audited filing, or a regulator's or exchange's own register | Annual report, ESEF, Finansinspektionen registers, Nasdaq reference data, Riksbanken, SCB, ESMA |
| **2 — Primary distribution** | Company-issued information carried by a distributor. Same content, one hop from origin | MFN.se, Cision |
| **3 — Reputable secondary** | Professional financial journalism, freely readable | Reuters, Associated Press, DI, Affärsvärlden |
| **4 — Low confidence** | Aggregators, blogs, forums, social media, brokers redistributing licensed data | Seeking Alpha, Reddit, Avanza, Nordnet, Yahoo, borskollen.se, allaaktier.se, aktiespararna.se |

A paywalled article is not a tier-3 source. Never route around a paywall, a
metered limit, an archive mirror or a cache URL — if the page will not load
without a subscription, the source is `DATA NOT AVAILABLE`. FT is hard-
paywalled and Bloomberg's public pages are metered; neither belongs in the
tier-3 examples, and DI or Affärsvärlden only qualify for the specific
article that actually loads free.

Tier 2 is **not** a lower-quality tier 1 — the PDF MFN carries *is* the company's
report. What tier 2 loses is provenance: a distributor can lag, can carry a
marketing release next to a regulated one, and in Cision's case exposes no
regulatory flag at all. Where the company's own IR site carries the same
document, cite the company.

**Tier 4 may never be the sole source of a material financial figure.** It can
supply a lead, a cross-check, or a pointer. Avanza's `homepage` field pointing
at a company's IR site is a legitimate tier-4 use; Avanza's share count as the
basis for market cap is not.

## Resolution table — the authority for each data type

| Data type | Authority | Fallback | Never |
|---|---|---|---|
| **Share count, share classes** | Nasdaq Nordic reference data (`nordic_shares.py`), cross-checked against the issuer's "Total number of shares and votes" disclosure | The filing cover page | A quote site; a single share class |
| **Market cap** | Computed per `references/valuation.md`, the single home for the formula | Where the point-in-time treasury count cannot be sourced, registered shares (including treasury), labelled `ASSUMPTION — treasury holdings unknown, assumed immaterial`, with market cap and EV stated as overstated | Any pre-computed market cap, including the exchange's own (Nasdaq's `marketCap` field included); a single class's price applied to a class that trades separately — an unlisted class taking its listed sibling's price is fine when stated as an `ASSUMPTION` |
| **Financial statements, annual** | Audited annual report | ESEF tagged filing (`esef_fundamentals.py`), then the year-end release | A press summary |
| **Financial statements, quarterly** | Interim report PDF | The MAR release body (`mfn_news.py --figures`, `cision_news.py`) | ESEF — it is annual only |
| **Insider transactions (SE)** | Finansinspektionen Insynsregistret (`insider_se.py`) | — | Any secondary aggregator |
| **Short interest (SE)** | FI Blankningsregistret (`short_se.py`) | — | Named holders presented as the total |
| **Institutional ownership (SE)** | FI Fondinnehav (`ownership_se.py`) + the annual report ownership table | Company IR "Aktien" page | Any claim of *complete* ownership |
| **Risk-free rate (SEK)** | Riksbanken SWEA `SEGVB10YC` (`macro_se.py --dcf-inputs`) | — | A remembered or assumed rate |
| **Risk-free rate (EUR)** | ECB Data Portal AAA euro-area spot curve (`macro_se.py --euro`) — a Bund proxy; for a French issuer the relevant sovereign is the OAT, which this does not supply | The AAA curve, stated as a proxy | A remembered or assumed rate |
| **Macro, Sweden** | SCB, Riksbanken | Eurostat, ECB for comparability | — |
| **Industry benchmark** | SCB by SNI (`macro_se.py --industry`, `--all-industries`) | Computed peer aggregate | A remembered "typical" margin |
| **Price, current** | Nasdaq (`nordic_shares.py`, `quote.py`) | Nordic tickers when `quote.py` fails: the tier-4 HTML fallbacks below, in the order `SKILL.md` gives (`borskollen.se`, `allaaktier.se`, `aktiespararna.se`) — **manual lookups, not fetched by any script** — recorded as `SINGLE SOURCE` | Any price without a timestamp |
| **Price, historical** | Nasdaq price history — **unadjusted** | — | Raw ratios across a split |
| **Corporate actions** | Nasdaq CNS "Total number of voting rights and capital"; MFN `sub:ca:*` tags | Cision releases | Inference from a price gap alone |
| **Legal entity, orgnr** | EU VIES; ESMA FIRDS for ISIN↔LEI↔MIC | GLEIF | A name match alone |
| **Company identity** | `company_resolve.py` — must reach sufficient confidence before analysis starts | — | Proceeding on an ambiguous name |
| **Financial targets, guidance** | The company's own IR site and annual report | The MAR release body | Treating guidance as verified |
| **Next scheduled report / calendar date** | The issuer's own "Finansiell kalender" page (`ir_discovery.py` locates it) | Avanza `companyEvents` (`horizon.py`) — **tier 4, SINGLE SOURCE**, an unofficial `_api` endpoint; a lead to verify, never a cited date | A guessed duration; a date without its source |
| **Consensus estimates** | **None available free** | Reverse DCF as a *substitute question* | Calling a reverse DCF "consensus" |
| **Earnings transcripts** | **None available free** | The report PDF and investor presentation | A third-party summary of a call |

A single price applied to a multi-class issuer's total share count is wrong
whenever classes trade apart, which is the normal Swedish case — A/B classes,
and several Stockholm issuers also list D-shares or preference shares priced
off a coupon rather than off the ordinaries (Sagax, Corem). Treasury shares
are also not outstanding. For an issuer with 100m A shares at SEK 210, 300m B
shares at SEK 200 and 50m D shares at SEK 320, the correct market cap is
SEK 97.0bn (21.0bn + 60.0bn + 16.0bn); a single-price formula applied across
all 450m shares at the B price gives SEK 90.0bn — SEK 7bn understated, straight
into EV and every EV multiple.

## Per-source detail

Country codes: SE Sweden · NO/DK/FI Nordics · FR France · EU · GLOBAL.
All entries below are free and keyless unless stated.

### Tier 1 — regulatory and exchange

| Source | Country | Supplies | Coverage | Frequency | Key limitation |
|---|---|---|---|---|---|
| **ESEF / filings.xbrl.org** | SE NO DK FI FR + EU | Tagged IFRS annual financials | Regulated markets only | Annual, **lags unevenly** | No MTFs, no quarters, primary statements only, extension tags invisible |
| **Nasdaq Nordic** | SE NO DK FI IS | Registered share count per listed class (incl. treasury), segment, ICB, index membership, price, 10y history, observation status, CNS announcements | 743 Stockholm lines incl. First North | Live / daily | Unlisted classes invisible; the endpoint's own `marketCap` figure must not be used (see Market cap row above); prices unadjusted; default urllib UA hangs |
| **FI Insynsregistret** | SE | PDMR insider transactions | All Swedish venues incl. MTFs | T+1 | UTF-16 CSV; export capped at 1000 rows |
| **FI Blankningsregistret** | SE | Short positions, history to 2010-05-10 per the file (predates the EU Short Selling Regulation's 2012-11-01 application — verify against the file, not assumed exact) | Swedish issuers | T+1 | Names only ≥0.5%; aggregate from 0.1% |
| **FI Fondinnehav** | SE | Swedish fund holdings per ISIN | Swedish UCITS only | Quarterly, 4–5 month lag | A floor on ownership, not the total |
| **Riksbanken SWEA** | SE | Rates, FX, yield curve | — | Daily | — |
| **SCB** | SE | CPI, PPI, sector margins by SNI, retail, housing, orders | — | Monthly / annual | Sector aggregates are not a peer set |
| **ESMA FIRDS** | EU | ISIN ↔ LEI ↔ MIC ↔ CFI, first trading date | EU venues | Continuous | Includes delisted noise, no sector |
| **GLEIF** | GLOBAL | LEI ↔ legal name | — | Continuous | A group holds several LEIs; only one files ESEF |
| **EU VIES** | EU | Registered legal name from orgnr | — | Continuous | Name and address only |
| **ECB / Eurostat** | EU | Euro-area rates, HICP, industrial production | — | Varies | Use only where relevant to the company |
| **Company IR site** | ALL | Reports, presentations, targets, dividend policy, share-and-votes disclosures | Per company | Per company | Structure varies; locate, never guess the URL |

### Tier 2 — distribution

| Source | Country | Supplies | Limitation |
|---|---|---|---|
| **MFN.se** | Nordics | MAR releases with `:regulatory` and `sub:ca:*` tags, report PDFs | Company feed capped at ~30 items, ignores `offset`; **empty for Sandvik, Atlas Copco, Hexagon, AB Volvo** |
| **Cision** | SE | Releases and report PDFs for the issuers MFN misses | **No regulatory flag**; mixes marketing PR with disclosure |

### Tier 4 — pointers and cross-checks only

| Source | Legitimate use | Never |
|---|---|---|
| **Avanza public endpoints** | The `homepage` field to locate an IR site; a cross-check on share count | A cited share count or ownership figure |
| **Yahoo Finance** | Would be a second opinion on a price, but is confirmed **unreachable from the app container (2026-08-31)** — documented for completeness, not currently usable | Fundamentals; a price without its timestamp |
| **borskollen.se / allaaktier.se** | Nordic HTML price lookup — `SKILL.md`'s order-2 fallback when `quote.py` fails, a manual lookup no script performs | Citing the page itself as a source; anything beyond the single price figure |
| **aktiespararna.se** | Nordic HTML price lookup — `SKILL.md`'s order-3 fallback, last resort before `DATA NOT AVAILABLE`, also a manual lookup | Citing the page itself as a source; anything beyond the single price figure |

## Paid sources — deliberately excluded

Documented so nobody re-investigates them. None is part of the system.

| Source | What it would add | Why excluded |
|---|---|---|
| Bolagsverket Företagsinformation v4 | Share capital, board, CEO, auditor, filing timeliness | Signed agreement and per-transaction fee |
| Bolagsverket Värdefulla datamängder | Liquidation status, filed iXBRL reports | Free of charge but requires a registration form and client credentials |
| SCB Företagsregistret API | Company register with SNI and size class | Free but requires a client certificate |
| S&P Capital IQ, FactSet, LSEG, Daloopa, Morningstar, Moody's, PitchBook, Aiera | Consensus, normalised peer multiples, transcripts | Institutional licences |
| Börsdata | Nordic fundamentals API | Paid tier |
| Holdings / Modular Finance | Full ownership | No public API, login required |
| Euroclear Vantage | Shareholder register | Commercial, issuer-ordered |

If the user later acquires one of these, it slots in at tier 1 for its data type
and displaces the fallback. Nothing else changes.

## Using the registry

1. Before fetching, identify the data type and read its authority from the
   resolution table.
2. Fetch the authority. If it fails, fetch the fallback **and record that you
   did** — the datapoint's source tier changes, and so does its confidence.
3. Where a second independent path exists cheaply, take it. Agreement is
   evidence; disagreement is a finding.
4. Record tier and primary/secondary on every material datapoint. See
   `references/data-quality.md`.

A figure whose tier is not recorded cannot be audited later, which means it
cannot be trusted later.
