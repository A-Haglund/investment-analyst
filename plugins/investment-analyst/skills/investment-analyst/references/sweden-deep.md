# Sweden — deeper material (STANDARD and DEEP only)

Additional to `references/sweden.md`, which every Swedish run loads. This file
adds the material only STANDARD and DEEP steps need — the latest-quarter/Cision
route, institutional ownership, macro/DCF inputs, secondary sources, K3
accounting detail, and a ticker cheat-sheet. Load it once a run passes QUICK
(sweden.md §"The default run" says exactly which steps).

### 2. MFN.se — releases and report PDFs

`scripts/mfn_news.py <slug> --reports --json` returns interim and annual reports with
the PDF attached, in Swedish and English. This is the fastest route to the most
recent quarter, and the only route to quarterly detail.

MFN is a private distribution channel, not the regulatory archive — but it is
where the issuer publishes, so the PDF it carries is the primary document.

**MFN does not cover every Swedish issuer.** Verified 2026-08-31: Sandvik,
Atlas Copco, Hexagon and AB Volvo all return an *empty feed* — they distribute
through **Cision** instead. They still appear in MFN `--search` because other
issuers reference them, which makes the gap easy to miss. For those companies:

- annual figures → `esef_fundamentals.py --search "NAME" --country SE --json`
- releases → `scripts/cision_news.py --search "NAME" --json` then
  `scripts/cision_news.py <slug> --reports --pdf ./reports --json`

```bash
python cision_news.py --search "Sandvik" --json        # resolve the newsroom slug
python cision_news.py sandvik --reports --json         # interim and annual reports
python cision_news.py sandvik --reports --pdf . --json # save the PDFs
```

**One real difference from MFN.** Cision publishes no regulatory flag, and a
newsroom mixes MAR disclosure with marketing PR — `/se/volvo` carries Volvo
Trucks product releases next to financial reports. MFN's `:regulatory` tag has
no equivalent, so the script's labels are keyword heuristics. Confirm before
citing a Cision release as regulated information.

The two sources are complementary rather than overlapping: ESEF reaches the
regulated-market large caps, MFN reaches the small caps and growth markets where
ESEF does not apply at all.

### 5. Ownership

```bash
python ownership_se.py --isin SE0012673267 --json
python ownership_se.py --name "Addtech" --json
python ownership_se.py --quarters --json
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
  and the impairment-test-note routine in `red-flags-general.md` flag 10
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
  impairment (`red-flags-general.md` flags 9, 10 and 15) apply only in
  their K3 form — each carries the caveat where it is stated.

## Common tickers

`VOLV-B.ST` · `INVE-B.ST` · `ATCO-A.ST` · `ERIC-B.ST` · `SAND.ST` · `EVO.ST` ·
`HEXA-B.ST` · `ASSA-B.ST` · `SEB-A.ST` · `SHB-A.ST` · `ESSITY-B.ST` ·
`EPI-A.ST` · `NIBE-B.ST` · `SWED-A.ST` · `HM-B.ST`

MFN slugs are name-based and rarely match the ticker: `volvo`, `investor-ab`,
`atlas-copco`, `evolution`, `sandvik`. Resolve with
`scripts/mfn_news.py --search "NAME" --json` rather than guessing.
