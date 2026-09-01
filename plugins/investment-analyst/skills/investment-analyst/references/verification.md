# Verification — proving the numbers, not just sourcing them

A `FACT` tag says where a number came from. It does not say the number is right.
An OCR slip, a mis-parsed thousands separator, a stale tag, a restatement you
did not notice — all produce a figure with impeccable provenance and the wrong
value.

This file is the discipline that closes that gap. It costs little and it is the
difference between research a professional will act on and research they will
have to redo.

## The source-authority ladder

When two sources disagree, the higher one wins. Say which you used and why.
This answers a different question from the four-tier `source_tier` recorded on
every datapoint: the numbered **tiers** are the four defined in
`references/source-registry.md`, and they classify a source's kind. This table
instead ranks specific *documents* against each other within and across those
tiers — which one wins when two disagree.

| Order | Source | Why it ranks here |
|---|---|---|
| 1 | **Audited annual report** (årsredovisning, 10-K, Geschäftsbericht) | Audited, complete, includes the notes |
| 2 | **ESEF / SEC XBRL tagged filing** | Same document, machine-readable, but only primary statements are tagged |
| 3 | **Interim report PDF** | Company-prepared, usually unaudited — the notes are thinner |
| 4 | **Regulatory press release** (MFN, 8-K, Cision) | Same figures, management framing, no notes |
| 5 | **Exchange or regulator register** | Authoritative for its own field only — shares, insiders, short positions |
| 6 | **Quote feeds** | Prices only. Never a source for fundamentals |
| 7 | **Financial press** | Context and dates. Never a figure that enters the model |

Two rules follow:

- An **unaudited** figure used in a valuation must be labelled as unaudited.
- A **restated** figure supersedes the original. Note the restatement and its
  size — a restatement is itself a finding about the company.

## Cross-checks to run, and what to do when they fail

These are cheap. Run them; report the result explicitly, including when
everything agrees. "Revenue confirmed against two independent sources" is a
sentence that earns trust.

### 0. Run the automated checks first

For any ESEF filer, `scripts/verify_filing.py --lei <LEI> --slug <mfn-slug>`
runs three checks and prints the block:

- **Restatement check** — filing N's prior-year comparative against filing N-1's
  own figure for that year. Two separately prepared documents, so agreement is
  real corroboration and disagreement is a restatement worth reporting.
- **Internal ties** — assets = liabilities + equity; revenue − cost of sales =
  gross profit; and the cash roll-forward including the IFRS FX-on-cash line.
- **Release cross-check** — against the company's own report release where MFN
  still carries it.

The script asserts a failure only when every term of an equation is present;
otherwise it reports INCOMPLETE. A check that cries wolf teaches you to ignore
it, so an unprovable check is never reported as a break.

### 1. Same figure, two extraction paths

Where both exist for the same fiscal year, compare:

| Market | Path A | Path B |
|---|---|---|
| Sweden / Nordics / France | `esef_fundamentals.py` (tagged XBRL) | the annual report PDF, or the MFN year-end release |
| US | `sec_fundamentals.py` (tagged XBRL) | the 10-K itself, or the 8-K earnings release |
| First North / Spotlight | `mfn_news.py --figures` | the report PDF behind the same release |

Agreement is the expected result. **A mismatch above 1% is a finding**, not a
rounding artefact — investigate before using either number. The usual causes,
in order of likelihood: a thousands-separator misparse, a scale error
(KSEK vs MSEK), continuing vs total operations, or a genuine restatement.

### 2. Internal consistency

Arithmetic the statements must satisfy. A break means a parsing error or a
misunderstood line item:

```
revenue - cogs                 = gross_profit
gross_profit - opex            = operating_income
cfo - capex                    = FCF
assets                         = liabilities + equity
cash_open + cfo + cfi + cff    = cash_close
sum of segment revenue         = total revenue   (allow for eliminations)
```

### 3. Market cap, three ways

Getting shares outstanding wrong corrupts every multiple downstream, and it is
the single most common silent error in Swedish analysis because of dual share
classes.

```
market cap = price x total shares across ALL classes
```

For Nordic issuers this is automated: `scripts/nordic_shares.py "NAME"` reads
the exchange's reference data and sums every listed class. Cross-check it
against the filing cover page and the company's own "Aktien" page.

The check the script cannot do for you: **unlisted share classes**. NIBE's
listed B class is 1,782,936,128 shares, but the registered total also includes
an unlisted A class worth roughly 12% more. Whenever only one class is found,
confirm against the issuer's latest "Total number of voting rights and capital"
disclosure before using the number.

### 4. Price sanity

Already automated in `quote.py`: two independent feeds, flagged if they diverge
by more than 0.5%. Always print the as-of timestamp and the staleness note.

### 5. Legal entity

For a Swedish company, confirm the organisationsnummer resolves to the legal
name you think it does:
`https://ec.europa.eu/taxation_customs/vies/rest-api/ms/SE/vat/<orgnr>01`
returns the officially registered name and address, free and keyless. Cheap
insurance against analysing the wrong entity in a group — Volvo Car AB and
AB Volvo are different companies with similar names and separate filings.

### 6. Currency

Confirm the reporting currency from the filing, not the listing venue. Evolution
AB is listed in Stockholm, quoted in SEK, and reports in EUR. Both fundamentals
scripts print the currency; read it.

## Reporting it — the Evidence block

One block carries all of it: the data-confidence score, the verification result
for every material figure, and everything that could not be checked. It sits at
section 11. There is no separate `DATA QUALITY` block and no "data confidence"
paragraph elsewhere in the body — **this is the single home**.

Figures are **grouped by status**, strongest first, so the reader sees the shape
of the evidence before reading a single line. The score opens the block; the
`TALLY` closes it.

```
EVIDENCE — Data Confidence 61/100

  IDENTITY   Sandvik AB · 556000-3468 · LEI 213800XKMBRWQHXQBK73     VERIFIED
             Reports in SEK · quoted in SEK · FY ends 31 December
             1,253,335,838 shares, single class, cover page 2026-02-06
  PRICE      SEK 356.00 · 2026-08-31 07:14 UTC · Nasdaq · 2 feeds, 0.02% apart

  VERIFIED           two independent origins agree within 1%
    Revenue FY2024          126,503 MSEK    ESEF | annual report p.7
    Operating income        21,378 MSEK     ESEF | annual report p.7
    Equity 2024-12-31       98,214 MSEK     ESEF | annual report p.9
    Balance sheet ties      assets = liabilities + equity, 0.00%
    Cash roll-forward       incl. the IFRS FX line, 0.61%

  CROSS-CHECKED      a second source agrees but shares an origin
    Net income FY2024       14,204 MSEK     ESEF | year-end release
    FY2023 comparative      unchanged — no restatement

  SINGLE SOURCE      no independent check is possible
    2026 guidance           management statement, no filing behind it yet
    SBC FY2024              AR note 8, not tagged in ESEF
    Ownership               Swedish UCITS funds only — a floor, not a total

  CONFLICT           none
  STALE              none

  DATA NOT AVAILABLE
    EV/EBIT, current        interim reports do not disclose EBIT
    Segment detail          not tagged; no segment reconciliation possible

  TALLY   5 of 12 material figures VERIFIED · 2 cross-checked · 3 single-source
          · 0 conflicts · 0 stale · 2 not available
```

Rules:

- **The score is the header.** Data Confidence appears here and in the verdict
  and decision blocks — nowhere else, and identical in all three.
- **Use the seven canonical statuses** from `references/data-quality.md` §2 and
  no others: `VERIFIED`, `CROSS-CHECKED`, `SINGLE SOURCE`, `CONFLICT`, `STALE`,
  `INCOMPLETE`, `DATA NOT AVAILABLE`. Not `MATCH`, not `OK`, not `PARTIAL` —
  a private vocabulary is how an unverified figure ends up reading as a verified
  one.
- **`INCOMPLETE` describes a check, not a figure**, and never gets its own
  group here. A figure whose cross-check could not run is grouped by what its
  source actually earns — normally `SINGLE SOURCE` — with the incomplete check
  noted inline on its line.
- **`SINGLE SOURCE`, `CONFLICT`, `STALE` and `DATA NOT AVAILABLE` are printed
  even when empty**, as `none`. An absent group reads as an absent problem; an
  explicit `none` is a claim you have made and can be held to.
- **The `TALLY` line is mandatory, always.** It is the structural defence
  against the all-green failure mode: a run where nothing was cross-checked must
  print `0 of N VERIFIED`, which cannot be mistaken for a clean bill of health.
  Every material figure appears in exactly one group and the tally sums to the
  total.
- **`VERIFIED` requires two independent origins**, not two sources. A release
  quoting its own filing is `CROSS-CHECKED`. `finfact.py` enforces this
  distinction in code — do not overrule it in prose.
- Identity and price are context lines rather than tallied figures, which is why
  they sit above the groups.

A block reading `VERIFIED` on every line for a First North microcap with no ESEF
filing is not a verification; it is a formatting exercise. The honest entries
are what make the verified ones worth anything.

## What cannot be verified — say so

Some figures have exactly one source and no independent check is possible.
Name them rather than letting them pass as equally solid:

- Management guidance — one source by definition
- TAM and market-share estimates — usually company- or vendor-supplied
- Any figure taken from a single press release with no filing behind it yet
- Extracted figures from a First North release where the PDF was not read

These go in the `SINGLE SOURCE` group of the Evidence block, and they count
towards the single-source figure in the tally. That is not a weakness in the
analysis; it is the analysis being honest about its own basis.

## The standard this sets

An analyst reading the output should be able to answer three questions without
asking you:

1. Where did each material number come from?
2. Was it confirmed anywhere else?
3. What in here is unverified, and how much does the conclusion depend on it?

If all three are answerable from the document, the research is credible whether
or not the conclusion turns out to be right. That is the only kind of
credibility that survives being wrong.
