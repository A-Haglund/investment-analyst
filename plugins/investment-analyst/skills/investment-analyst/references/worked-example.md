# Worked example — calibrating the output

An abridged extract showing the expected working discipline — tagging density,
sourcing style — and how that work turns into two different things: the short
**answer** the reader gets, and the **underlying material** printed only when
asked. It is a format reference, not a template to copy: the figures below are
illustrative.

**The word caps are hard, per depth:** TLDR 150 words · QUICK 350 · COMPARE the
ranking table plus 80 words per company · STANDARD 800 · DEEP 1200 · PORTFOLIO
the action table plus 80 words per holding. See `SKILL.md` §4. Exceeding the
cap is a defect in the same way an unsourced number is — a reader who stops
reading has been given nothing.

**The 800-word STANDARD cap is enforced per section, not as one global
count:** Omdöme 150 · Vad bolaget är 120 · Varför priset ligger där det
ligger 90 · Talar för / Talar emot 180 · Scenarier 60 · Vad rekommendationen
betyder i praktiken 90 · Slutsats 110 — see `SKILL.md` §6. A global cap can
only be checked after the fact, once the draft is already too long to fix
without a rewrite; a section budget can be checked the moment that section
closes, which is why the per-section form is the one that actually gets
enforced.

Rule of thumb for tagging: **one tag per material claim**, not per sentence and
not per paragraph. A number that enters the model gets a tag. Narrative
connective tissue does not. The tags are working discipline only (`SKILL.md`
§1) — they never reach the answer (§7); they surface only in the Evidence
block and the decision record, both underlying material, both shown trimmed in
Extract 7.

---

## Extract 0 — the verdict block that opens every analysis

Four lines inside the fence — identity and date, the call with conviction,
price and fair value, and the two scores — the prose outside it so it wraps at
any width. Then five labelled plain-language lines, **no tags** — the reader of
this block is not an analyst. Note what it still refuses to hide: conviction
is on the recommendation line, data confidence sits beside the investment
score, fair value is a range, and the mismatch between reporting currency and
quote currency is named rather than assumed away.

````
```
OMDÖME — Evolution AB (EVO, Nasdaq Stockholm Large Cap) · DEEP · 2026-08-28

  BEHÅLL — MEDEL ÖVERTYGELSE
  SEK 750,00 nu -> rimligt värde 694-829 (bas) · -7% till +11% · förv. avk. -1,8%
  Investeringsbetyg 70/100 · Datasäkerhet 82/100
```

**Varför.** Bolagets lönsamhet är i särklass bäst i branschen, men kursen har
återhämtat sig till att ligga mitt i intervallet för rimligt värde, utan
marginal åt något håll.
**Risk.** Hur stor andel av intäkterna som kommer från marknader där bolagets
licens ifrågasätts går inte att kontrollera oberoende — det är den enskilt
största osäkerheten i caset.
**Inprisat.** Till dagens kurs räknar marknaden med ungefär 6% intäktstillväxt
om året, med lönsamheten kvar nära dagens redan exceptionella nivå — båda en
tydlig inbromsning från de 24% om året bolaget faktiskt levererat de senaste
fyra åren.
**Bevaka.** Två kvartal i rad med en vinstmarginal under 65% bryter caset
(`Bevakning`, rad 1, i det avslutande blocket).
**Overifierat.** Hur stor del av ersättningen som betalas i egna aktier gick
inte att kontrollera för den här perioden — det redovisas bara en gång om
året, inte varje kvartal.
````

A 70/100 company whose price sits inside its own fair-value range is a HOLD.
The block says that in the first two lines rather than arriving at it on page
four. Note what changed from the analyst's working notes: the `OPINION -
reverse DCF` tag that used to sit under `Priced in` is gone, and the number it
guarded is now a sentence that says what it means and what it is compared
against, not a bare ratio. Note also that the expected return is **slightly
negative while the score is 70/100** — the scorecard measures the company, the
recommendation measures the opportunity at this price. And the second line
only means anything because it holds one currency throughout: Evolution
reports in EUR, the fair value is derived in EUR, and the SEK figure quoted
here is that value converted at a stated, dated rate (the build behind it is
folded into Extract 7) — never a EUR number read against a SEK price as if
they shared a unit.

## Extract 1 — what the company is, and why the price is where it is

Sections 2 and 3 of the answer. Section 2 is 3–5 short bullets on what the
company owns and does *now* — not its history, not its strategy slide.
Section 3 is 2–4 sentences on the one thing driving the case. Both in the
reader's own language.

> **Vad bolaget är**
> - Sandvik tillverkar utrustning och verktyg för gruvdrift, bergarbete och
>   metallbearbetning — en av få globala leverantörer i flera av sina nischer.
> - Ungefär hälften av intäkterna kommer från eftermarknad: reservdelar,
>   service och förbrukningsverktyg som kunderna köper oavsett konjunktur.
> - Kunderna finns bland gruvbolag och verkstadsindustri över hela världen —
>   caset står inte eller faller med en enda marknad eller råvara.
> - Noterat på Nasdaq Stockholm Large Cap, rapporterar i SEK.
>
> **Varför priset ligger där det ligger**
> Orderingången har vänt uppåt igen efter en svag period, men aktiekursen
> speglar fortfarande det svaga året 2024 och har inte hunnit ikapp
> förbättringen. Till dagens kurs, SEK 356, prisar marknaden in en långsiktig
> tillväxt på ungefär 4% om året — lägre än vad bolaget faktiskt levererat de
> senaste tio åren. Den kombinationen — en verksamhet som förbättras och ett
> pris som inte hängt med — är hela grunden för att kalla aktien en köp.

Nothing here is new evidence. Section 2 could be written from the annual
report's first page; section 3 is the one sentence from `SKILL.md` §5's own
`Why` line, expanded into something a non-analyst can act on without first
learning what an order book is.

## Extract 2 — handling a gap, the old way and the new way

The same gap, written twice, so the calibration between the two registers is
unmistakable. The facts do not change between them — only the notation.

**The old, tagged way** — an analyst's working note. This never reaches the
reader; it is what the tag discipline in `SKILL.md` §1 produces while the work
is being done.

> Share-based compensation for Evolution is `DATA NOT AVAILABLE`. ESEF tags
> primary statements only, and the interim report does not break SBC out
> separately. It is disclosed in the annual report note on incentive
> programmes, which is outside the period analysed here. The owner-adjusted
> FCF figure below therefore **overstates** cash available to shareholders by
> an unknown amount; treat it as an upper bound.

**The new, plain way** — what the reader actually sees, per `SKILL.md` §7 rule
2: `DATA NOT AVAILABLE` becomes "det gick inte att få fram", stated as a
clause, not a stamp.

> Hur mycket Evolution betalar de anställda i egna aktier går inte att få fram
> separat för den här perioden — det redovisas bara en gång om året, i en not
> som ligger utanför rapporterna jag har haft tillgång till. Det betyder att
> det kassaflöde jag räknar med sannolikt är högre än det verkliga, med ett
> belopp jag inte kan ange. Se det som ett tak, inte som ett facit.

Same gap, same cause, same direction of error — "overstates … upper bound"
survives intact as "högre än det verkliga … ett tak, inte ett facit". Nothing
about the gap got softer; only the notation carrying it changed. A reader who
skips technical notation entirely still cannot skip that sentence.

## Extract 3 — Talar för / Talar emot, with the mark scale

Two marked lists, 3–5 items each, compressing evidence already established
elsewhere in the analysis — nothing appears here for the first time. Every
item carries a mark, and the mark grades the item, not the section: 🟢 strong
and established · 🟡 real but qualified, single-sourced or unproven · 🟠 a
concern short of a threat · 🔴 material.

```
**Talar för**
🟢 Orderstock 147 MSEK, klart över historiken
🟢 Intäkter +87% i H1; Q2 nära operativt break-even
🟡 Foundry-avtal på minst 115 MSEK — skalbarhet visad, men ett enda avtal

**Talar emot**
🔴 Kassaflöde -19,3 MSEK i H1; 9-10 månaders kassa på nuvarande burn
🔴 123 MSEK till Portugal är ofinansierat
🟠 +260% fler aktier på sex månader
```

Note the 🟡 in `Talar för`: a real signal (a foundry contract of scale) marked
down from 🟢 because it rests on one agreement, single-sourced. That is the
whole point of grading the line rather than the heading — a bare 🟢 under
`Talar för` would only repeat the word "för".

## Extract 4 — scenarios: one table, one chart

Bear, base and bull, each with a value — one table of at most four columns,
plus the range-marker chart. No other chart belongs in the answer (`SKILL.md`
§6).

| Scenario | Värde (SEK) | Sannolikhet | Vad som krävs |
|---|---|---|---|
| Bear | 310 | 25% | Gruvbolagens investeringar viker |
| Bas | 420-470 | 55% | Orderingången håller i sig |
| Bull | 540 | 20% | Marginalen når tillbaka mot tidigare toppar |

```
  310 ──────────●─────────────├══════════┤──────────────── 540
  bear 310 · nu 356 · bas 420-470 · bull 540
```

The weighting is not decorative: 0.25×310 + 0.55×445 (base midpoint) +
0.20×540 = 430, which is +20.9% above today's SEK 356 — the exact expected
return on Sandvik's verdict line in `SKILL.md` §5. The chart and the table
say the same thing twice, once as a picture and once as a number, and both
must check against the verdict block above them.

## Extract 5 — what the call means in practice

Section 6 of the answer, and the section the old tagged report never had.
`HOLD` is a word about a price, not an instruction to a holder; this section
translates the call into what it actually means for two to four sentences.

> Ett BEHÅLL är ett omdöme om priset, inte en instruktion till den som
> redan äger aktien. Evolution är ett av de mest lönsamma bolagen i sin
> bransch, men till dagens kurs, SEK 750, ligger den mitt i mitt intervall för
> rimligt värde, 694–829 — varken dyr nog att sälja eller billig nog att köpa
> mer. Det som skulle flytta mig åt endera hållet är ett fall under ungefär
> SEK 620, eller ett kvartal där rörelsemarginalen — hur stor del av
> intäkterna som blir vinst — sjunker under 65%.

Note what this is not. It explains **what the call means**; it does not say
what to do about it. That is `Äger du den redan` in `Slutsats`, and the two
must not converge — a section 6 that ends in advice has taken the closing
block's job and left the reader with the same sentence twice.

## Extract 6 — the closing block

Every run ends with the signal line; `SKILL.md` §9 shows it in two forms. The
short form — no `Bevakning`, no `Horisont` — is what a run without a full
close prints:

**Nothing follows section 7.** No trailing "Sources:" line, no bibliography,
no appendix, after `Slutsats`. Sources live in the Evidence block (Extract 7
below), which is underlying material — a trailing source list in the answer
is that block leaking out, and it is the single most common way this format
fails (`SKILL.md` §6).

```
## Slutsats

🟢 **NIBE B — KÖP** · MEDEL ÖVERTYGELSE
Marginalen har vänt och värderingen ligger under bolagets egen tioårshistorik.

Viktigast: Datasäkerhet 61/100. EV/EBIT går inte att beräkna eftersom
delårsrapporterna inte redovisar EBIT. Detta är en riktning, inte ett facit.
```

At STANDARD and DEEP on a single company, the signal line opens the full
close instead — `Bevakning`, `Äger du den redan`, `Horisont`, `Viktigast`,
then the single trailing offer line. `Talar för` / `Talar emot` sit above this
block (Extract 3); they are not repeated here.

````
## Slutsats

Obducat har äntligen fått upp farten operativt: H1 visar +87% intäkter och en
orderstock på 147 MSEK. Problemet är att bolaget fortfarande inte är lönsamt,
har negativt kassaflöde och behöver mer kapital för Portugal-expansionen.

Aktien är samtidigt högt värderad och marknaden räknar med starkare tillväxt än
bolagets eget mål. I november kan omkring 116 miljoner nya aktier emitteras till
högst 0,18 kr, vilket ger både utspädning och säljtryck.

🔴 **Obducat B — SÄLJ** · LÅG ÖVERTYGELSE
SEK 0,546 nu · rimligt värde 0,38-0,48 · Investeringsbetyg 41/100 · Datasäkerhet 38/100

**Bevakning**

| # | Utlösare | Tröskel | Följd |
|---|---|---|---|
| 1 | Q3-intäkter | < 30 MSEK | tesen bryts -> SÄLJ HELT |
| 2 | Nyemission | > 75 MSEK under 0,45 kr | bear-caset |
| 3 | Kurs | > 0,80 kr | bull-värdering -> sälj |

**Äger du den redan:** trimma nu, låt Q3 och novemberoptionerna avgöra resten.

**Horisont:** till Q3 den 13 november. Då avgörs om intäktstakten håller, och
novemberoptionerna visar om utspädningen blir så stor som marknaden fruktar.
Datumet kommer från Avanzas kalender och är inte bekräftat mot bolaget.

Viktigast: Datasäkerhet 38/100. Bolaget är en MTF-notering utan ESEF, så
siffrorna är lästa ur delårsrapportens prosa. Detta är en riktning, inte ett facit.

Vill du se underlaget — siffror, värdering, källor och Evidence-block — säg **visa underlaget**.
````

One trailing offer line, never two — and here it stops at one because Obducat
is thin-data enough that DEEP is the wrong offer (`SKILL.md` §4: "do not go
DEEP" on a First North/NGM microcap). `Äger du den redan` earns its place even
though the call is SELL, not HOLD: it is mandatory on SELL and STRONG SELL,
and belongs "wherever the reader plausibly holds the security". `Horisont`
carries its provenance in words in the fuller Obducat close above — never as
the bracketed `[SINGLE SOURCE - tier 4, ...]` form, which is notation and
belongs to the Evidence block — because a disclosure that survives only in a
script's own output is a disclosure that gets dropped in translation.

## Extract 7 — the underlying material, printed only on request

Everything from here down is produced on every STANDARD and DEEP run and
printed **only** when the reader says "visa underlaget" — the snapshot table,
the statements and their sparklines, moat scoring, owners and management, the
full valuation build, the scorecard, and the two blocks below. Tags stay
intact here; this is the one place the reader has asked for the analyst's
working view rather than the answer.

The valuation build behind Extract 0's fair-value range still runs a reverse
DCF and an enterprise-to-equity bridge exactly as before: the reverse DCF
states the bottom-up cost of equity (8.1%) against the house rate actually
used (9.0%) and what the 0.9-point gap does to the answer, and the bridge
carries PV of cash flows through to a per-share point (EUR 68.00, SEK 761.60
at the stated rate) that is only ever the base-case midpoint feeding the
694-829 range — never a number printed on its own. Both live in
`references/valuation.md`; neither needs a standalone extract at 800 words.
The remaining chart forms — revenue and margin sparklines, the P/E range bar,
the scorecard's bar column — are unchanged from `SKILL.md` §6 and sit here
too; the answer keeps only the scenario range marker (Extract 4).

**The Evidence block**, trimmed to shape:

```
EVIDENCE — Data Confidence 82/100

  IDENTITY   Evolution AB (publ) · orgnr 556994-5792 · LEI 549300SUH6ZR1RF6TA88   VERIFIED
             Reports in EUR · quoted in SEK · FY ends 31 December
  PRICE      SEK 750.00 · 2026-08-28 15:30 UTC · Nasdaq Stockholm · 2 feeds, 0.02% apart

  VERIFIED           two independent origins agree within 1%
    Revenue FY2025           2,090 EURm   ESEF tag Revenue | MFN release
    EBITDA FY2025             1,432 EURm  ESEF | MFN release

  CROSS-CHECKED      a second source agrees but shares an origin
    Diluted shares            199,226,613   ESEF cover page | Euroclear register
    EUR/SEK reference rate    11.20         Riksbanken | ECB, agree within 0.1%

  SINGLE SOURCE      no independent check is possible
    FY2026 revenue outlook    management commentary, Q2 2026 call transcript

  CONFLICT           none
  STALE              none

  DATA NOT AVAILABLE
    Share-based compensation  annual report note only; interim ESEF tags
                               primary statements only

  TALLY   5 of 11 material figures VERIFIED · 2 cross-checked · 3 single-source
          · 0 conflicts · 0 stale · 1 not available
```

The tally makes the shape of the evidence unmissable, and empty groups print
`none` rather than get dropped. The EUR/SEK rate is `CROSS-CHECKED`, not
`VERIFIED`, because Riksbanken's and the ECB's reference rates are both fixed
from the same interbank quotes the same day — two publications, one origin.

**The decision record.** You write it as JSON, `decision_record.py` validates
it, and the block below is rendered from it — the direction of authority
`SKILL.md` §9 sets. The record, trimmed to the fields that matter here:

```json
{
  "as_of": "2026-08-28", "depth": "DEEP", "producer": "analyze",
  "identity": {"name": "Evolution AB", "ticker": "EVO",
               "org_number": "556994-5792", "isin": "SE0012673267"},
  "verdict": "HOLD", "conviction": "MEDIUM",
  "price": {"value": 750.00, "currency": "SEK", "as_of": "2026-08-28 15:30 UTC",
            "source": "Nasdaq Stockholm", "reporting_currency": "EUR"},
  "fair_value": {"bear": 470, "base_low": 694, "base_high": 829, "bull": 1075,
                 "currency": "SEK"},
  "scenario_weights": {"bear": 0.30, "base": 0.50, "bull": 0.20},
  "scores": {"investment_score": 70, "data_confidence": 82},
  "rests_on": [
    {"text": "EBITDA margin holds >= 65%", "basis": "ASSUMPTION"},
    {"text": "Unlicensed revenue stays low single-digit", "basis": "ASSUMPTION"},
    {"text": "WACC 9.0%, terminal growth 2.0%", "basis": "ASSUMPTION"}],
  "reason_codes": [{"code": "SINGLE_SOURCE_MATERIAL", "severity": "WARN",
                    "detail": "Licensed-market revenue split is the company's own"}],
  "triggers": 5
}
```

No `expected_return` and no `margin_of_safety`: both are recomputed from the
price, the scenario values and the weights, and a stated figure that disagrees
with them by more than 0.15pp is refused. `rests_on` entries are one short line
each because the block gives each one a single line.

The rendered block. The renderer owns the column positions, the number
formatting and the ladder's width — read this as the shape, not as something to
retype:

```
DECISION — EVO · Evolution AB · (556994-5792) · DEEP · 2026-08-28

RECOMMENDATION    HOLD — MEDIUM CONVICTION
Price             SEK 750   (2026-08-28 15:30 UTC, Nasdaq Stockholm · reports in EUR)
Fair value        SEK 694-829 base (50%) · 470 bear (30%) · 1075 bull (20%)

                  470 ──────────────────────├═════●═══════┤─────────────────────── 1075

Expected return   -1.8%   (probability-weighted across scenarios)
Margin of safety  -8% to base-low · +10% to base-high
Investment Score  70/100        Data Confidence  82/100

Rests on          1. EBITDA margin holds >= 65%                ASSUMPTION
                  2. Unlicensed revenue stays low single-digit  ASSUMPTION
                  3. WACC 9.0%, terminal growth 2.0%           ASSUMPTION

Flags             WARN  SINGLE_SOURCE_MATERIAL
Triggers          see Bevakning in the closing block — 5 rows

*This is analysis, not investment advice.*
```

The ladder's marker sits inside the base range rather than outside it — the
HOLD is visible before a word is read. Note the expected return: the identity
gives -1.77%, and the block prints the computed figure, so it reads -1.8% and
not the -1.7% a hand transcription reaches by truncating instead of rounding.
The rendered number is always the recomputed one; that is why the verdict
block's figure is copied from here rather than worked out a second time. This
is also the one place where an English record survives untranslated even in a
Swedish-language run (§13).

## Extract 8 — a TLDR answer in full

Everything the system says at TLDR depth. The header is the same fenced shape
the verdict block always uses — TLDR rewrites the second and third lines for
a depth with no scorecard and no scenarios, never the header itself — and the
data gap is named in the prose, not dropped to fit the 150-word cap.

````
```
OMDÖME — Evolution AB (EVO, Nasdaq Stockholm Large Cap) · TLDR · 2026-08-28

  BEHÅLL — MEDEL ÖVERTYGELSE
  SEK 750,00 nu -> rimligt värde 660-870 (bas, multiplar mot egen historik) · -12% till +16%
  Investeringsbetyg saknas — inget scorecard på denna nivå · Datasäkerhet 82/100
```

Evolution utvecklar och sänder de live dealer-casinospel som speloperatörer
kopplar in på sina egna plattformar, och gör det mer lönsamt än någon annan
börsnoterad aktör i branschen — EBITDA-marginalen var 68,5% förra året på
EUR 2 090 miljoner i intäkter, upp 9,7%. Verksamheten är utmärkt; priset är
problemet. Till dagens kurs har aktien återhämtat det mesta av nedgången som
följde på anklagelser om att spelen nåddes i marknader utan lokal licens, och
den återhämtningen har slutit det mesta av gapet till rimligt värde: SEK
750,00 mot ett basintervall på SEK 694-829 (det intervallet är EUR 62-74 per
aktie, omräknat till SEK 11,20 per EUR, Riksbankens referenskurs,
2026-08-28). Den största risken är densamma som orsakade nedgången: ingen
utanför bolaget kan oberoende uppskatta hur stor andel av intäkterna som
kommer från marknader där licensstatusen ifrågasätts, och en
tillsynsmyndighet som tvingar fram utträde från de marknaderna skulle skära
in i den tillväxt marknaden fortfarande betalar för. Jag skulle bli mer
positiv under SEK 620, eller om bolaget redovisar en oberoende kontrollerbar
uppdelning av intäkter från licensierade marknader.

*En STANDARD-körning skulle lägga till vallgravs-, tillväxt- och
ledningsavsnitten samt ett riktigt scenariointervall — omkring tio minuter.*
````

Under 150 words, no table, no jargon a non-specialist would have to look up —
and it still says HOLD, why, what breaks it, and what would change it. This
range (660-870) is wider than Extract 0's DCF-derived 694-829, and that is
correct, not a contradiction: different depths derive fair value by different
methods — multiples here, DCF at DEEP — and the checksum rule that binds the
verdict to the decision record holds within one analysis, not across depths.
Note too that the TLDR prose still carries the FX rate and its date in plain
words, even with no inline tags: the currency conversion is not a DEEP-only
courtesy.

---

Check the verdict block (Extract 0) against the decision record (Extract 7)
line by line: `HOLD — MEDIUM CONVICTION`, `694-829`, `70/100`, `82/100`,
`-1,8%`. Five figures, two places, identical — because the verdict block is
written from the validated record, which is the one direction that makes the
repetition a check rather than a second opinion. Extract 6's Obducat close obeys the same
rule with its own numbers — `SEK 0,546`, `0,38-0,48`, `41/100`, `38/100` —
which would match whatever verdict block and decision record a full Obducat
run had printed above them, even though this file does not carry Obducat's in
full.
