# Competitive advantage, growth and management

## Part 1 — Competitive advantage (moat)

A moat is a structural reason why competitors cannot compete away excess
returns. High margins alone are not a moat; they are the symptom you must
explain. If you cannot name the mechanism, the moat score is low regardless of
how profitable the company looks today.

### Sources of advantage

Assess each. Say "none" where none exists — a padded list is worse than a short
honest one.

| Source | Evidence that it is real |
|---|---|
| **Pricing power** | Price increases taken without volume loss; margin held through input-cost inflation |
| **Switching costs** | Retention/churn figures, contract length, integration depth, cost to migrate |
| **Network effects** | Value per user rising with user count; density economics in the numbers |
| **Brand** | Sustained price premium over functionally equivalent products |
| **Scale advantages** | Unit costs falling with volume; fixed-cost leverage visible in margin trend |
| **Intellectual property** | Patents with real remaining life and enforcement history; regulatory approvals |
| **Distribution** | Shelf space, installed base, exclusive channels competitors cannot replicate |
| **Cost position** | Structural input, location or process advantage — not temporary |

### Concentration risk

- **Customers**: revenue share of top 1, 5 and 10. Above 10% from one customer
  is a material dependency; disclose it as such.
- **Suppliers**: single-source components, foundry dependency, key licensors.
- **Channel**: reliance on one distributor, platform or app store.
- **Geography and regulation**: exposure to a single jurisdiction's rules.

### Competitive threats

Name actual competitors and what each could do to this company. Cover new
entrants, substitutes, vertical integration by customers or suppliers, and
technology shifts that could reset the industry. Vague threats are not analysis —
"AI could disrupt them" is not a finding; "their largest customer is building an
in-house replacement, disclosed in that customer's own filings" is.

### Moat Score 0–10

| Range | Meaning |
|---|---|
| 9–10 | Multiple reinforcing advantages; sustained high ROIC through a full cycle; no credible challenger |
| 7–8 | One or two clear structural advantages; returns durable but attackable at the edges |
| 5–6 | Narrow advantage; visible in the numbers but eroding or dependent on execution |
| 3–4 | Weak; the business competes largely on price or execution |
| 0–2 | Commodity economics; no defensible position |

Justify the score against **evidence of durability**, not against current
profitability. Then state what would move the score up or down two points.

## Part 2 — Growth analysis

### Market

- **TAM** — total addressable market, with the source and the definition used.
  Company-supplied TAM is a marketing number; label it `ESTIMATE` and say who
  produced it.
- **SAM** — the realistically serviceable part given product, geography and
  channel.
- **Market share** — current level and the direction over 3–5 years. Share
  losses inside a growing market are the important case; a company can grow
  revenue while losing.

### Unit-level drivers

| Driver | Question |
|---|---|
| Customer growth | Net adds, and are they slowing? |
| ARPU | Rising from price, upsell or mix? |
| Retention / churn | Gross and net retention; net above 100% means expansion |
| Pricing | Realised price change versus list |
| Volume | Units, and whether growth is volume or price |
| Geographic expansion | New markets, and the economics of entry |
| Product expansion | Attach rates, cross-sell into the installed base |

### The decomposition — the point of this section

Write one paragraph stating what actually drives growth, split into:

```
volume  +  price/mix  +  new products  +  new geographies  +  M&A  +/-  FX
```

Then answer: **which of these is durable and which is one-off?** A company
growing on a single price increase and an acquisition is not a compounder,
whatever the headline CAGR says.

Finish with the reinvestment question: can this business deploy incremental
capital at its current ROIC? A high-return business with nowhere to reinvest is
a cash cow, valued differently from a compounder.

## Part 3 — Management and capital allocation

### Capital allocation record

The most reliable signal about management. Trace the last five years of cash and
where it went:

```
CFO  ->  capex  |  acquisitions  |  buybacks  |  dividends  |  debt paydown  |  cash build
```

Judge each: did organic capex earn a return? Did acquisitions add value or
goodwill? Were buybacks executed at sensible valuations or at the peak?

### Insider ownership and trading

- **Ownership**: percentage held by management and board, and its direction.
  Meaningful ownership relative to the individual's net worth beats a large
  absolute number.
- **Transactions**:
  - US → Form 4 filings on EDGAR
  - Sweden → `scripts/insider_se.py --issuer "NAME" --months 12`
  - Germany, Norway, Denmark, Finland → BaFin, Finanstilsynet and
    Finanssivalvonta each run the equivalent PDMR register; France → AMF's
    Déclarations de dirigeants — see `europe.md` and
    `red-flags-and-smallcap.md` for the endpoints, not restated here

Read them correctly. **Open-market purchases are the signal.** Option exercises,
RSU vesting and scheduled 10b5-1 sales carry little information. The Swedish
register flags option-programme rows explicitly — separate them before drawing a
conclusion. Cluster buying by several executives near a low is the strongest
version of the signal.

### Guidance accuracy

Pull guidance issued over the last 8–12 quarters and compare it against what was
delivered. Chronic over-promising is a durable trait. So is sandbagging — a
company that always beats its own guidance by the same margin is managing
expectations, and consensus already knows.

The MFN company feed is capped at roughly 30 items with no `offset`, so for an
active issuer that window can cover under a year of releases.
`scripts/guidance_track.py --history` recovers some of the earlier record from
IR pages, but not reliably. Where fewer than the requested quarters could be
sourced, mark the guidance record `INCOMPLETE` rather than presenting a short
record as a full one.

### M&A track record

For each material acquisition: price paid, multiple, what was promised, what was
delivered, and whether goodwill was later impaired. Impairments are management
admitting overpayment.

### Compensation and incentives

Read the proxy (DEF 14A) or the Swedish remuneration report. What are executives
actually paid for? Targets on revenue or adjusted EPS encourage empire-building
and add-backs. Targets on ROIC, FCF per share or relative TSR align with owners.
Note the gap between the metrics management is paid on and the metrics that
create value.

### Communication quality

Compare the tone of releases in good and bad quarters. Does management name its
own mistakes? Do the risk factors change when the risks change? Are difficult
analyst questions answered or deflected? Consistency through a bad quarter is
worth more than polish in a good one.

### Output

A short verdict on management with a score 0–10 and a separate score for capital
allocation, each backed by the specific evidence above.
