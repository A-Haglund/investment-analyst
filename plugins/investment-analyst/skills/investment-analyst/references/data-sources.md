# Data sources

Status verified 2026-08-31 against the live endpoints. Anything marked
"needs credentials" must not be worked around — document what the user needs
and continue with the free tier.

## Free, no credentials — the working base

### SEC EDGAR (US filers)

No API key. SEC's fair-access policy requires a descriptive `User-Agent`
containing a real contact address, and rate-limits to ~10 requests/second.

| Endpoint | Returns |
|---|---|
| `https://www.sec.gov/files/company_tickers.json` | ticker → CIK for every filer |
| `https://data.sec.gov/submissions/CIK##########.json` | every filing, newest first |
| `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` | full tagged financial history |
| `https://data.sec.gov/api/xbrl/companyconcept/CIK##########/us-gaap/TAG.json` | one concept's history |
| `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=...&type=4` | insider Form 4 filings |

CIK must be zero-padded to 10 digits in `data.sec.gov` paths.

Forms that matter: **10-K** annual, **10-Q** quarterly, **8-K** material events
and earnings releases, **DEF 14A** proxy (compensation, insider ownership),
**Form 4** insider transactions, **13F-HR** institutional holdings, **20-F**
foreign private issuers, **S-1** IPOs.

Beware: filers switch XBRL tags mid-history. Never rely on a single tag for a
concept — `scripts/sec_fundamentals.py` merges a prioritised fallback list per
metric for exactly this reason.

### Finansinspektionen — Insynsregistret (Swedish insiders)

The Swedish equivalent of Form 4, mandated by MAR Art. 19. Free, public,
no key. Covers Nasdaq Stockholm, First North, Spotlight and NGM issuers
from 2016-07-03.

```
https://marknadssok.fi.se/publiceringsklient/en-GB/Search/Search
  ?SearchFunctionType=Insyn
  &Utgivare=<issuer name>
  &Transaktionsdatum.From=dd/mm/yyyy
  &Transaktionsdatum.To=dd/mm/yyyy
  &button=export
```

Returns **UTF-16 encoded** CSV, semicolon-delimited, dates as dd/mm/yyyy.
The issuer filter is a loose substring match — "Volvo" returns both AB Volvo
and Volvo Car AB, which are different companies. Always filter again by exact
issuer.

Use `scripts/insider_se.py --issuer "NAME" --months 12`.

### Finansinspektionen — Blankningsregistret (Swedish short interest)

Free, no key, plain GET. Three ODS files (a zip containing UTF-8 `content.xml`):

| Endpoint | Contents |
|---|---|
| `https://www.fi.se/BlankningsRegister/GetAktuellFile` | current holder-level positions >= 0.5% |
| `https://www.fi.se/BlankningsRegister/GetHistFile` | holder-level history back to 2010-05-10 (as returned by the file; this predates the EU Short Selling Regulation's 2012-11-01 application date, so treat the exact start date as unverified until checked against the file itself) |
| `https://www.fi.se/BlankningsRegister/GetBlankningsregisterAggregat` | per-issuer aggregate from 0.1%, with LEI |

Parsing traps, all handled by `scripts/short_se.py`: rows end with cells
carrying `number-columns-repeated="16379"` and sheets end with rows repeated
thousands of times, so naive expansion exhausts memory; the same column mixes
`0.62` and `0,58`; `<0,5` is a sentinel, not a number; issuer names contain
non-breaking spaces. FI throttles heavy users — the script caches for an hour.

Static HTML drill-downs, useful for citation and for previous-value deltas:
`.../blankningsregistret/emittent?id=<LEI>` per issuer, and
`.../blankningsregistret/Positionsinnehavare?id=<GUID>` for everything one fund
is short across Sweden.

There is **no centralised ESMA dataset** for net short positions — ESMA
publishes only a list of links to national regulators. FI is authoritative.

### MFN.se (Nordic regulatory releases)

JSON Feed 1.0. Free, no key.

| Endpoint | Returns |
|---|---|
| `https://mfn.se/a/<slug>.json?limit=40` | one issuer's releases |
| `https://mfn.se/all/s.json?query=<name>&limit=10` | search, to resolve a slug |
| `https://mfn.se/all/s/nordic.json?limit=20` | all Nordic releases |

**A company feed is capped at roughly 30 recent items** and ignores `offset`;
`query=` searches only within that window. Historical reports are therefore not
reachable through this feed — use ESEF for prior years, or the annual report PDF.

Item shape: `content.publish_date`, `content.title`, `content.preamble`,
`content.text`, `content.attachments[].url` (the report PDF),
`properties.tags`, `properties.lang`, `author.slug`.

Tag `:regulatory` marks MAR-regulated disclosure. Tags starting `sub:report`
mark interim and annual reports. Companies publish Swedish and English versions
as separate items — prefer English for analysis, and never treat the pair as two
separate events.

Use `scripts/mfn_news.py <slug> --reports`.

### ESEF — European Inline XBRL (Nordics, France, most of the EU)

Free, no key. Annual reports only. **Germany and Ireland are not covered.**

| Endpoint | Returns |
|---|---|
| `https://filings.xbrl.org/api/filings?filter[country]=SE&include=entity` | filings for one country, with issuer names |
| `https://filings.xbrl.org/api/filings?filter[entity.identifier]=<LEI>` | one issuer's filings |
| `https://filings.xbrl.org<json_url>` | the filing rendered as xBRL-JSON |
| `https://api.gleif.org/api/v1/lei-records?filter[entity.legalName]=<name>` | GLEIF LEI lookup |

Pagination is `page[number]` with `page[size]` (max 500); `page[offset]` returns
HTTP 400. Bracketed parameters must be URL-encoded. Only exact `entity.name`
matching is supported — `contains` returns nothing — so substring search means
paging a country and filtering locally, which `esef_fundamentals.py` does.

Use `scripts/esef_fundamentals.py`. Read `references/europe.md` for the limits.

### Nasdaq Nordic reference data

Free, no key. `https://api.nasdaq.com/api/nordic`

**The default `Python-urllib` User-Agent is blocklisted and the request hangs
until timeout rather than erroring.** Send a browser-shaped UA.

| Endpoint | Returns |
|---|---|
| `/search?searchText=<name>` | orderbookId, ISIN, symbol — filter `assetClass == "SHARES"`, a name search also returns warrants |
| `/instruments/{obId}/summary?assetClass=SHARES` | **registered shares per listed class, including treasury** (not shares outstanding), plus a precomputed market cap, segment, ICB, observation status |
| `/instruments/{obId}/info?assetClass=SHARES` | last, bid/ask, day and 52-week range |
| `/instruments/{obId}/price-history?...&fromDate=&toDate=` | daily OHLC and turnover |
| `/screener/shares?category=MAIN_MARKET&market=STO&segment=LARGE_CAP` | the listed universe; `category=FIRST_NORTH` for the growth market |
| `/instruments/{obId}/chart?assetClass=SHARES&fromDate=&toDate=` | daily OHLCV; **both dates required** or you get intraday minute bars |
| `/instruments/IX447/index-children` | OMXS30 constituents (`IX555` = OMXSPI) |

Share classes share no issuer id — group on the symbol root (`VOLV A`,
`VOLV B` → `VOLV`). Use `scripts/nordic_shares.py`.

The `shares` field is the registered count including treasury, not shares
outstanding — see `references/sweden.md` for the treasury correction. The
endpoint's `marketCap` is registered shares times price with no treasury
deduction and no allowance for an unlisted class: never use it. Compute
market cap per `references/valuation.md` instead.

The old `nasdaqomxnordic.com` DataFeedProxy is retired; every path redirects.

### Cision (Swedish issuers not on MFN)

Free, no key.

| Endpoint | Returns |
|---|---|
| `https://news.cision.com/se/_ta/Newsroom?q=<name>` | JSON slug resolver |
| `https://news.cision.com/se/<slug>/ListItems?format=rss` | the company's releases |
| `...?format=rss&pageIx=N` | pagination, 24 items per page |
| `https://mb.cision.com/Main/<cust>/<rel>/<file>.pdf` | attachments, linked from the release page |

Drop `se/` for the English newsroom. `&r=true`, `startDate` and `endDate` are
accepted but have **no effect** — only `pageIx` works. `news.cision.com/se/rss/<x>`
returns the global Swedish firehose, not the company; do not use it.

No regulatory flag exists in the feed. Use `scripts/cision_news.py`.

### Finansinspektionen — Fondinnehav (Swedish institutional ownership)

Free, no key. Quarterly, back to 2018Q4. The list page at
`https://www.fi.se/sv/vara-register/fondinnehav-per-kvartal/` carries download
links of the form `/FondInnehavLista/download?filnamn=Fondinnehav_<YYYY>Q<N>_<timestamp>.zip`.
The timestamp cannot be constructed, so the list page is the only index.

Each zip holds ~711 fund XMLs. Per instrument: `ISIN-kod_instrument`, `Antal`,
`Marknadsvärde_instrument`, `Andel_av_fondförmögenhet_instrument`. Per fund:
`Fond_namn`, `Fondförmögenhet`. Use `scripts/ownership_se.py`.

### SCB — Swedish official statistics

Free, keyless, CC0. `https://api.scb.se/OV0104/v2beta/api/v2`, 30 calls per
10 seconds, `?lang=en` for English labels. Verified working. Scripted in
`scripts/macro_se.py`: `--industry <SNI code|section letter|term>` for the
sector benchmark below, `--all-industries` for the whole grid at one size
class, `--cpi`, `--ppi` (with `--find-product` to locate a SPIN 2015 code),
and `--indicator {retail,orders,housing}`.

The table worth knowing about is **TAB6273** (Företagens ekonomi): revenue and
operating profit by SNI section and size class, which gives an **official
sector margin benchmark** to compare a company against — a defensible yardstick
where a hand-picked peer set is arguable. FY2024, firms with 250+ employees:

```
C  tillverkning              6.7% EBIT margin
G  handel                    1.8%
J  information/kommunikation 10.6%
L  fastighetsverksamhet     21.8%
```

Also verified: TAB6596 CPI, TAB3184 PPI at 432 product levels with separate
home/export/import indices (an input-cost versus output-price squeeze read),
TAB3948 retail sales, TAB4572 housing starts, TAB1710 industrial new orders.
Every mandatory dimension must appear in the selection or the API returns 400.

### EU VIES — free legal-entity verification

`https://ec.europa.eu/taxation_customs/vies/rest-api/ms/SE/vat/<orgnr><01>`
returns the officially registered legal name and address for a Swedish
organisationsnummer. Free, keyless. Bolagsverket has no keyless data at all, so
this is the free substitute for confirming that an entity is what it claims.

### Riksbanken SWEA — Swedish rates and FX

Free, official, no key.
`https://api.riksbank.se/swea/v1/Observations/<series>/<from>/<to>`

`SEGVB10YC` 10-year Swedish government bond (the SEK risk-free rate) ·
`SECBREPOEFF` policy rate · `SEKEURPMI` SEK/EUR · `SEKUSDPMI` SEK/USD.

Use `scripts/macro_se.py --dcf-inputs` as the entry point for a Swedish DCF:
it assembles the risk-free rate, policy rate and FX, each with its own
observation date, and keeps them visibly separate from the equity-risk-premium
and beta ASSUMPTIONs (`--erp`, `--beta`) a DCF still has to supply. `--curve`
gives the full SEK yield curve; `--peers` adds the foreign 10-year benchmarks
Riksbanken itself republishes, for a spread read on the same convention.

### US Treasury and ECB — USD and EUR risk-free rates

`references/valuation.md` requires a 10-year yield in the cash flows' own
currency. The two legs are not in the same state.

- **EUR — scripted and working.** `scripts/macro_se.py --euro` reads the ECB
  Data Portal (`data-api.ecb.europa.eu/service/data`, dataflow `YC`, key
  `B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y` for the 10-year point; the trailing code
  varies by tenor, `SR_3M` through `SR_30Y`). This is the **AAA-rated
  euro-area sovereign spot curve** — a Bund proxy, not a blend of every euro
  member. For a French issuer this is **not** the OAT that
  `references/valuation.md` calls "the relevant sovereign": France is not in
  the AAA sub-sample the ECB curve is built from, so the ECB figure there is a
  proxy and the OAT itself would need its own (currently unverified) fetch.
- **USD — still unscripted.** No free, keyless endpoint for the daily par
  yield curve has been verified yet. The U.S. Department of the Treasury
  publishes it itself via treasury.gov and fiscaldata.treasury.gov; the exact
  dataset and query remain **unverified** — confirm the endpoint before a
  script reads it. A remembered or assumed rate is forbidden, so until this is
  verified: fetch the Treasury's published curve at analysis time by hand and
  cite it with its observation date, or mark the discount rate `ASSUMPTION`
  with the source named, per `references/valuation.md`.

FRED is deliberately not used for either leg — see "Not configured" below.

### Market prices

| Source | Coverage | Notes |
|---|---|---|
| `https://api.nasdaq.com/api/quote/<SYM>/info?assetclass=stocks` | US listings | Free, no key, needs a browser `User-Agent` |
| `https://query1.finance.yahoo.com/v8/finance/chart/<SYM>?range=1mo&interval=1d` | US + Nasdaq Stockholm (`.ST`) | Unofficial, unsupported, and **confirmed unreachable from the app container on 2026-08-31** — documented for completeness only, not currently usable |

Swedish tickers use the `.ST` suffix with a hyphen for share classes:
`VOLV-B.ST`, `INVE-B.ST`, `ATCO-A.ST`, `ERIC-B.ST`, `EVO.ST`, `SAND.ST`.

`meta.chartPreviousClose` is the close *before the requested range*, not
yesterday's close. Take the prior session off the close series instead —
`scripts/quote.py` already does this.

Yahoo being unreachable means no second US price feed is currently reachable
from this container. A US price from `api.nasdaq.com` is `SINGLE SOURCE` —
record it as such rather than implying a cross-check was run. For Nordic
tickers, `SKILL.md`'s price-fallback order (`borskollen.se`, `allaaktier.se`,
`aktiespararna.se`) is the working alternative when `quote.py` fails; see
`references/source-registry.md`'s tier-4 table.

## Needs credentials — document, never circumvent

These are institutional licences. Anthropic ships the MCP connectors; the data
entitlement is the customer's own. All are registered on this machine via the
official `financial-analysis` plugin and will authenticate the moment a licence
exists.

| Connector | Endpoint | What unlocks it |
|---|---|---|
| S&P Capital IQ | `kfinance.kensho.com/integrations/mcp` | Capital IQ licence, then OAuth in `/mcp` |
| FactSet | `mcp.factset.com/mcp` | FactSet subscription |
| LSEG | `api.analytics.lseg.com/lfa/mcp` | LSEG/Refinitiv licence |
| Daloopa | `mcp.daloopa.com/server/mcp` | Daloopa account |
| Morningstar | `mcp.morningstar.com/mcp` | Morningstar Direct |
| Moody's | `api.moodys.com/genai-ready-data/m1/mcp` | Moody's subscription |
| PitchBook | `premium.mcp.pitchbook.com/mcp` | PitchBook Premium |
| Aiera | `mcp-pub.aiera.com` | Aiera account (earnings transcripts) |
| Chronograph | `ai.chronograph.pe/mcp` | Chronograph (PE portfolio monitoring) |

To connect one once licensed: run `/mcp` in Claude Code and authenticate the
server, or add it as a Connector in the Claude app.

If the user asks for something only these can answer — consensus estimates,
normalised peer multiples, full transcripts — say plainly which source is
required and what it costs to enable, then deliver what the free tier supports.

## Earnings call transcripts

There is no free structured source. Aiera and S&P cover this, and both need a
licence. In their absence:

1. **Company IR page** — many issuers publish a webcast replay and, increasingly,
   a transcript PDF. Check first; it is a primary source.
2. **US** — the earnings release arrives as an **8-K exhibit** on EDGAR and
   carries the prepared remarks' substance. Guidance history for the
   guidance-accuracy analysis lives in those 8-Ks.
3. **Nordics** — the interim report PDF and the accompanying investor
   presentation on MFN carry the same guidance content.

If none of these yields what a transcript would, write `DATA NOT AVAILABLE` for
the transcript-derived point. Do not substitute a third-party summary of a call.

## Consensus estimates

Not obtainable without a licensed source. See the sourcing rule in
`references/valuation.md`: use your own base-case estimate labelled
`ASSUMPTION`, or company guidance labelled `ESTIMATE`, and substitute the
reverse DCF when the question is what the market expects. Never present either
as consensus.

## Not configured

- **FRED** (Federal Reserve macro data) — deliberately skipped. A free key from
  `fredaccount.stlouisfed.org/apikey` and `FRED_API_KEY` in the environment would
  enable it. Mention it only if a question genuinely turns on macro series.

## Terms-of-service posture

Two endpoints in this file are grey rather than clearly official: the
browser-shaped User-Agent sent to `api.nasdaq.com` (the default
`Python-urllib` agent is blocklisted), and the Yahoo v8 chart endpoint, which
is unofficial and unsupported. Neither is paid or keyed, so neither breaches
the no-paid-data rule, but the plugin's constraint also forbids bypassing
terms of service, so the posture is stated rather than left implicit:

- It identifies itself honestly. Sending a browser-shaped User-Agent to get
  past a blocklisted default agent is not impersonating a specific browser
  build, and it never evades authentication.
- It respects published rate limits and caches results rather than hammering
  an endpoint.
- It uses only endpoints the publisher serves to the public without a key.
- Unofficial endpoints, such as the Yahoo chart API, would be a cross-check,
  never the sole source for a figure that enters the model — moot for Yahoo
  specifically while it is unreachable, but the rule stands for anything
  unofficial that does answer.
- If a publisher's terms or `robots.txt` forbid programmatic access, the
  source is dropped rather than worked around.
- A paywalled article is not a source. Never route around a paywall, a
  metered limit, an archive mirror or a cache URL — if the page will not load
  without a subscription, the source is `DATA NOT AVAILABLE`.

## Retrieval rules

- Time-sensitive figures — price, market cap, shares outstanding, guidance —
  must be re-fetched at analysis time. Never carry them from memory.
- Record the retrieval timestamp for anything that moves.
- When two sources disagree on a reported figure, the filing wins.
- When a filing and a press release disagree, the filing wins.
