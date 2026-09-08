# Red flags — quick screen (flags 2, 5, 17, 18)

This file holds four of the twenty red flags from the general screen — **flags
2, 5, 17 and 18** — plus the wording rule and the reporting rules that both
red-flag files share. **QUICK depth loads only this file.** **STANDARD and
DEEP depth must load this file AND `red-flags-general.md` to get all twenty
flags** — reading only one of the two at those depths is an incomplete screen
run while believing it is complete. Flag numbering is non-contiguous by
design: flags 1, 3, 4, 6-16, 19 and 20 are in `red-flags-general.md`, not
missing.

---

# The wording rule — non-negotiable, read before anything else

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
  unresolved flags from the cash-and-earnings-quality group (flag 5 below;
  flags 6-9 in `red-flags-general.md` §2) cap conviction at LOW**, because that
  combination means the reported profit and the cash are telling different
  stories and you cannot say which is right.

---

# Flags run at QUICK depth

**2. High dilution.** Trigger: **diluted share count up more than 5% in a year**
with no matching acquisition or capital programme; **more than 15% in one year**
is material at any size and for any reason. 5% is not arbitrary — it is roughly
the whole equity risk premium of a mature business, so a shareholder diluted at
that rate is left with roughly the **risk-free** return while still bearing
full equity risk. Where: the diluted
weighted-average share count on the face of the income statement, compared
across filings, and `nordic_shares.py "NAME"` for the current registered count.
Do not use the basic count and do not use a quote site.

**5. Weak FCF conversion.** Trigger: **FCF / net income below 60% averaged over
three years**, or below 80% for a business that presents itself as asset-light.
Three years, not one: a single year below 60% is usually working capital
absorbed by growth and is not a finding. Where: computed from CFO and capex,
both tagged in ESEF, so `esef_fundamentals.py` gives it directly. Follow the
cumulative accrual gap procedure in `fundamentals.md` and name the
balance-sheet line that absorbs the difference — a flag that cannot name the
line is not yet a flag.

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

---

# Reporting the screen

At QUICK depth, run flags 2, 5, 17 and 18 — the ones reachable from QUICK's own
step list — and report the count of items not screened. Flags 1 and 19 both
key off `mfn_news.py --regulatory`, which is step 6 of `SKILL.md`'s Swedish
routing table and only enters at STANDARD depth, so they defer to STANDARD
along with the rest. At STANDARD and DEEP, run all twenty.

**Unresolved flags that are material to the recommendation surface in the answer**
through the `Talar emot` list in section 4, or in the `Viktigast` closing line.
Resolved flags confirm a benign explanation. A tally of unresolved, resolved,
clear and not-available items is underlying material (printed when the reader
requests it), together with the full flag-by-flag record.

Where the red flag screen runs materially clean, that is worth stating in the
answer rather than leaving silence to imply the usual concerns. A reader who sees
no flags printed may reasonably wonder whether they were looked for.

In the printed answer, do not dump the raw flag list. Instead, fold material
findings into `Talar emot` with their own sourcing and reasoning, just as any
other challenge to the thesis would appear there. Unresolved flags that do not
crack the call live in `Viktigast`, which is where the reader should see the
real limitations of this run.
