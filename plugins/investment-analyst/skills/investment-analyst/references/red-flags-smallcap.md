# Red flags — small-cap and MTF screen

Additional screening for First North, Spotlight, NGM and Small Cap issuers, and for any listed company with fewer than three years of reported history. This file is loaded alongside `red-flags-general.md`, which applies to all companies — these items are additional, never a replacement. Together they set the complete screen for thin-venue companies.

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
filer is likely to sit rather than an IFRS one — `sweden-deep.md`'s "Accounting
basis" section has the framework detail. On a K3 filer, restate goodwill
amortisation and imputed lease costs before comparing to an IFRS peer, and
read flags 9, 10 and 15 in `red-flags-general.md` with their K3 caveats rather than the IFRS
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

Run the whole of Part 1 §5 from `red-flags-general.md` — the flags do not get easier because the company is
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
   the reverse DCF in `valuation-dcf.md`, it needs no ten-year forecast, and it
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

`references/conviction.md` sets the hard cap: **a microcap on First North, Spotlight
or NGM caps at MEDIUM**, enforced by `decision_record.py` when the decision
record carries the `VENUE_MICROCAP` reason code. This file supplies the reason,
because a rule whose reason is understood survives cases the rule did not
anticipate.

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
- three or more unresolved flags from the cash-and-earnings-quality group
  (flag 5 in `red-flags-quick.md`; flags 6-9 in Part 1 §2 of `red-flags-general.md`)

These five are judgement, not code: each one belongs on the record as the
reason code that fits it — `DATA_CONFIDENCE_LOW` for the evidence cases,
`SINGLE_SOURCE_MATERIAL` for the unread PDF — and the enforced ceiling follows
from what you attach. A push-down you keep in your head is one the record
cannot show a later reader.

Push to **VERY LOW** where the business model is unproven — pre-revenue, or
revenue from a single contract that has not repeated — or where the scenario
values span a very wide range, per the `references/conviction.md` ladder.

**MEDIUM is a ceiling, not a floor.** Nothing here prevents a LOW-conviction
BUY, and `references/conviction.md` is explicit that weak evidence does not veto a
positive call. It changes how the call is written:
`KÖP — LÅG ÖVERTYGELSE`, with `Datasäkerhet 44/100` on the scores line and the position size
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
