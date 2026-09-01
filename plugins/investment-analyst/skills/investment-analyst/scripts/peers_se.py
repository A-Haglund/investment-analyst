#!/usr/bin/env python3
"""Peer selection for Nordic equities, scored on the dimensions that decide
whether two companies actually trade against each other.

WHY THIS EXISTS
---------------
Relative valuation is one of the three legs of any valuation, and it is only
as good as the peer set. The peer set is almost always built badly: someone
takes the ICB sector off the exchange listing and calls the other 147 names in
"Industrials" comparable. They are not. Evolution and a Swedish sports-betting
operator sit in the same ICB supersector (4050 Travel and Leisure) and share
almost nothing economically - one sells studio capacity to licensees at ~65%
EBITDA margin with no player liability, the other buys traffic and carries
regulatory and player risk on its own balance sheet. On the other side, Nasdaq
files Addtech under Industrials, Lagercrantz under Technology, Storskogen and
Roeko under Financials - four companies running the identical decentralised
serial-acquirer model. Sector matching gets both cases exactly wrong.

So this script scores peer relevance on eight dimensions and is explicit about
where each number came from:

  COMPUTED from free public data
    size                 log10 market-cap distance   Nasdaq Nordic reference data
    growth               3y revenue CAGR             ESEF (IFRS, filings.xbrl.org)
    margin               EBIT margin                 ESEF
    ROIC                 NOPAT / invested capital    ESEF
    capital intensity    capex / revenue             ESEF
    co-cyclicality       residual return correlation Nasdaq daily closes
    ICB proximity        supersector / sector match  Nasdaq reference data

  CURATED - an editorial judgement encoded in a table in this file, NOT a
  computation. Shown separately in every output so it can be argued with:
    business archetype   revenue model / how the company earns

  NOT COMPUTED AT ALL - free structured data does not contain these, and this
  script refuses to invent a number for them. They are printed as an explicit
  checklist for a human or model to close before the peer set is used:
    end-market mix, geographic revenue split, customer concentration,
    competitive position, accounting comparability, regulatory exposure.

THE CO-CYCLICALITY MEASURE is worth explaining because it is the one dimension
usually claimed to be uncomputable. Raw return correlation between two
Stockholm large caps is dominated by the index and says nothing: Sandvik and
H&M correlate 0.42. So a single equal-weighted factor is built from the
candidate pool itself, every candidate is regressed on it, and the RESIDUALS
are correlated. What survives is co-movement that the common factor does not
explain - shared end-market shocks. Empirically (2023-08 to 2026-08, weekly):
Sandvik's top residual correlation is Epiroc at +0.51; Evolution's are Hacksaw
+0.23, Betsson +0.20, Kambi +0.18 and everything else negative. The LEVEL is
set-relative (residuals sum to roughly zero across the pool) so only the
RANKING within one run is meaningful. That caveat is printed with the numbers.

HONESTY RULES
-------------
  * A peer's score is renormalised over the dimensions actually available for
    it, and the coverage fraction is printed. A peer scored on three of eight
    dimensions is not the same evidence as one scored on eight.
  * PEER SET LOW CONFIDENCE is emitted, with reasons, when the set is thin,
    dispersed, or resting on sector matching alone. A three-name honest peer
    set beats a twelve-name invented one.
  * DATA NOT AVAILABLE, never a guess.

WHAT A MULTIPLE HERE IS MADE OF, AND WHAT WILL SUPPRESS IT
----------------------------------------------------------
Each of these is a place where the arithmetic can be silently wrong, so each
is stated and each is enforced rather than assumed:

  numerator     last traded price x registered shares of every listed class,
                priced at the exchange's own trade timestamp, which is printed.
                The reference-data marketCap is cached for a week and is used
                only as a fallback - labelled as such, with the age of the copy.
  currency      the LISTING's quote currency (Verisure is a EUR line on
                Stockholm) against the currency of the filer's LATEST fiscal
                year (Betsson redenominated SEK->EUR in 2021, so its merged
                fact set carries both). Where they differ the cap is converted
                at the ECB fixing before any division and the rate is printed.
                Where either is unknown the row is suppressed entirely.
  net debt      needs BOTH borrowings legs and a cash figure to be tagged.
                One leg is not enough and an untagged cash balance is not zero;
                either way net debt and all EV multiples are suppressed and the
                reason is printed. Enterprise value adds non-controlling
                interests, since EBIT and revenue are consolidated in full.
  earnings      profit attributable to OWNERS OF THE PARENT, not the group
                total: a market cap does not buy the minorities' share.
  the median    runs over positive denominators only, inside a sanity bound,
                excluding peers a reporting period behind the target - with
                every exclusion named and a separate n per column.

COST AND CACHING
----------------
The Nasdaq universe is 4 calls per market. Per-instrument detail is one call
each at roughly 2-4 seconds, so a naive fan-out over 148 industrials would take
ten minutes. Instead: a priority-ordered scan capped by --scan-limit (default
70) for market caps, then full detail on --max-candidates (default 14) only.
Everything lands in a disk cache in the system temp directory - universe 12h,
instrument reference data 7d, LAST TRADED PRICE 15 MINUTES, price history 12h,
ESEF entity index 7d, ESEF filings 30d. The price is deliberately outside the
reference-data TTL because it is the numerator of every multiple; share counts
and ICB codes are not, and are stamped with the age of the cached copy. A warm
second run is dominated by the price-history refresh.

Usage:
    python peers_se.py "Sandvik"
    python peers_se.py "Evolution" --nordic
    python peers_se.py "Addtech" --multiples
    python peers_se.py "Sandvik" --multiples --json
    python peers_se.py "Indutrade" --scan-limit 40 --max-candidates 10

Free, no API key. Sources: Nasdaq Nordic reference data; ESEF Inline XBRL
annual reports via filings.xbrl.org.
"""
import argparse
import datetime
import json
import math
import os
import re
import statistics
import sys
import tempfile
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import esef_fundamentals as ef          # noqa: E402
import finfact as ff                    # noqa: E402
import nordic_shares as ns              # noqa: E402

from concurrent.futures import ThreadPoolExecutor   # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

CACHE = os.path.join(tempfile.gettempdir(), "nordic-peers-cache")
TTL_UNIVERSE = 12 * 3600
# Share counts and ICB codes move slowly, so the instrument summary keeps a
# long TTL. Its marketCap field does NOT: it is the numerator of every
# multiple, and a week-old cap sold as live is a silently wrong number
# (measured drift over three hours: Sandvik -0.382%, Verisure -0.921%). The
# cap used in a multiple is therefore recomputed as live price x registered
# shares, and where that is impossible the cache's own write time is printed.
TTL_INSTRUMENT = 7 * 24 * 3600
TTL_QUOTE = 900                     # the price leg of every multiple
TTL_PRICES = 12 * 3600
TTL_ESEF_INDEX = 7 * 24 * 3600
TTL_ESEF_FACTS = 30 * 24 * 3600     # a filed annual report never changes
WORKERS = 4                         # deliberately polite; Nasdaq is unmetered

# Substituted when the filed effective tax rate is not usable. Every ROIC
# computed on it is marked in the output - see derive().
DEFAULT_TAX_RATE = 0.21

# A multiple above this is arithmetic, not a valuation, and has no business
# inside a median. Reported as an exclusion, never silently dropped.
SANITY_MAX = {"ev_ebit": 100.0, "ev_sales": 50.0, "pe": 100.0}

# A peer whose latest annual report predates the target's by more than this is
# reporting a different economic year, not a comparable one.
PEER_BEHIND_DAYS = 300

MARKET_COUNTRY = {"STO": "SE", "CPH": "DK", "HEL": "FI", "ICE": "IS"}
MARKET_CCY = {"STO": "SEK", "CPH": "DKK", "HEL": "EUR", "ICE": "ISK"}
MARKET_NAME = {"STO": "Stockholm", "CPH": "Copenhagen", "HEL": "Helsinki",
               "ICE": "Reykjavik"}
SEGMENT_ORDER = ["LARGE_CAP", "MID_CAP", "SMALL_CAP", "FIRST_NORTH"]

# ICB supersector labels for the 4-digit codes Nasdaq exposes. Only the ones
# that turn up on Nordic equity lines; anything else prints the bare code.
ICB4 = {
    "1010": "Technology", "1510": "Telecommunications", "2010": "Health Care",
    "3010": "Banks", "3020": "Financial Services", "3510": "Real Estate",
    "4010": "Automobiles and Parts", "4020": "Consumer Products and Services",
    "4030": "Media", "4040": "Retailers", "4050": "Travel and Leisure",
    "4510": "Food, Beverage and Tobacco", "4520": "Personal Care and Grocery",
    "5010": "Construction and Materials", "5020": "Industrial Goods and Services",
    "5510": "Basic Resources", "5520": "Chemicals", "6010": "Energy",
    "6510": "Utilities",
}

# ---------------------------------------------------------------------------
# CURATED BUSINESS ARCHETYPES - editorial input, not a computation.
#
# This is the one place where judgement is hard-coded, because how a company
# earns its revenue is simply not present in any free structured dataset. Each
# entry is a revenue-model bucket, the end markets it serves (printed as
# EVIDENCE only - never scored), and name patterns matched against the Nasdaq
# listing name. Two properties matter:
#
#   * It drives ADMISSION as well as scoring. An archetype match pulls a
#     candidate in regardless of ICB sector or segment, which is the only way
#     Addtech (Industrials) ever sees Lagercrantz (Technology), Storskogen
#     (Financials) or Alligo (Consumer Discretionary).
#   * It is NOT exhaustive and never will be. An untagged candidate is scored
#     0.25 on this dimension and flagged '?', not excluded - the table's
#     silence is not evidence against a company.
#
# Reviewed 2026-08-31. Argue with it, edit it, and re-run.
# ---------------------------------------------------------------------------
ARCHETYPES = [
    {
        "key": "serial_acquirer_niche_industrial",
        "label": "decentralised serial acquirer of niche B2B businesses",
        "model": "buys small, high-margin niche product/distribution businesses "
                 "at low single-digit EV/EBIT, runs them autonomously, compounds "
                 "cash flow into the next deal; goodwill-heavy balance sheet",
        "end_markets": "fragmented industrial/technical B2B aftermarket across "
                       "the Nordics and northern Europe; no single end market "
                       "dominates by design",
        "patterns": [r"\baddtech\b", r"\blagercrantz\b", r"\bindutrade\b",
                     r"\blifco\b", r"bergman\s*&?\s*beving", r"\boem international\b",
                     r"momentum group", r"\balligo\b", r"\bvolati\b",
                     r"storskogen", r"r[oö]ko", r"\bteqnion\b", r"idun industrier",
                     r"karnell", r"\bsdiptech\b", r"\bxano\b", r"christian berner",
                     r"\bnordic fastening\b", r"\bfagerhult\b"],
    },
    {
        "key": "serial_acquirer_installation_services",
        "label": "serial acquirer of installation / site-services contractors",
        "model": "rolls up local installation, facade, landscaping or technical "
                 "service contractors; labour-driven, project revenue, thin "
                 "margins, working-capital light but cyclical with construction",
        "end_markets": "Nordic new-build and renovation construction, public "
                       "infrastructure maintenance",
        "patterns": [r"\binstalco\b", r"fasadgruppen", r"green landscaping",
                     r"\bbravida\b", r"nordic waterproofing", r"\bsalix\b",
                     r"\bcoor\b", r"\bcaverion\b", r"\bassemblin\b"],
    },
    {
        "key": "b2b_igaming_supplier",
        "label": "B2B iGaming content / platform supplier",
        "model": "sells game content or sportsbook platform to licensed "
                 "operators on a revenue-share; no player liability, no "
                 "marketing spend against end users, very high incremental "
                 "margin, capex is studios and headcount",
        "end_markets": "licensed online casino and sportsbook operators "
                       "worldwide; concentrated customer base; exposure to "
                       "regulatory change in the operators' markets",
        "patterns": [r"\bevolution\b", r"\bhacksaw\b", r"\bkambi\b",
                     r"\bplaytech\b", r"\bnetent\b", r"\bevoke\b",
                     r"\benlabs\b", r"gaming corps"],
    },
    {
        "key": "b2c_online_gambling_operator",
        "label": "B2C online gambling operator",
        "model": "acquires players with paid marketing, carries player "
                 "liability, gaming duties and licence risk directly; revenue "
                 "is net gaming revenue after bonuses",
        "end_markets": "retail gamblers in specific licensed jurisdictions; "
                       "revenue mix by regulated vs grey market is the single "
                       "biggest valuation driver and is not in structured data",
        "patterns": [r"\bbetsson\b", r"\bleovegas\b", r"\bkindred\b",
                     r"angler gaming", r"\bgentoo\b", r"\bgig\b",
                     r"gaming innovation"],
    },
    {
        "key": "igaming_affiliate",
        "label": "iGaming affiliate / lead generation",
        "model": "owns SEO and media assets, sells qualified player traffic to "
                 "operators for revenue share or CPA; asset-light, capex is "
                 "content and acquired domains, highly exposed to search "
                 "algorithm and operator marketing budgets",
        "end_markets": "online gambling operators; concentrated in a handful of "
                       "regulated markets",
        "patterns": [r"catena media", r"\braketech\b", r"better collective",
                     r"\bacroud\b"],
    },
    {
        "key": "mining_construction_equipment",
        "label": "mining and rock-excavation equipment and consumables",
        "model": "capital equipment plus a large, stickier consumables and "
                 "service aftermarket; heavy installed base, distribution and "
                 "service network is the moat",
        "end_markets": "mining producers and infrastructure/tunnelling "
                       "contractors; driven by miners' capex and production "
                       "volumes, not by GDP",
        "patterns": [r"\bsandvik\b", r"\bepiroc\b", r"\bmetso\b",
                     r"\balimak\b", r"\bnordic iron\b", r"\bmetso outotec\b"],
    },
    {
        "key": "industrial_compressors_flow_thermal",
        "label": "compressors, flow, separation and thermal equipment",
        "model": "engineered industrial equipment with a large service and "
                 "spare-parts annuity; process-critical, price-led rather than "
                 "volume-led",
        "end_markets": "broad process industry, semiconductors, HVAC, marine, "
                       "food and energy; genuinely diversified end-market mix",
        "patterns": [r"atlas copco", r"alfa laval", r"\bmunters\b",
                     r"\bsystemair\b", r"\bnederman\b", r"\bnibe\b",
                     r"\bficosa\b", r"\bvaisala\b"],
    },
    {
        "key": "commercial_vehicles",
        "label": "heavy commercial vehicles and powertrain",
        "model": "cyclical unit sales of trucks/buses/machines plus a growing "
                 "service and parts base; heavy fixed cost, financial-services "
                 "arm distorts consolidated returns",
        "end_markets": "hauliers and construction contractors in Europe, North "
                       "and South America; freight cycle driven",
        "patterns": [r"^volvo(?! car)", r"\btraton\b", r"\bscania\b",
                     r"\bkonecranes\b", r"\bcargotec\b"],
    },
    {
        "key": "engineering_consultancy",
        "label": "engineering / technical consultancy",
        "model": "billable-hour professional services; value is utilisation "
                 "rate and price per hour, essentially no capital employed "
                 "beyond goodwill from acquisitions",
        "end_markets": "public infrastructure, energy and industrial capex "
                       "programmes in the Nordics",
        "patterns": [r"\bafry\b", r"\bsweco\b", r"\brejlers\b", r"\bknowit\b",
                     r"ework group", r"\bsemcon\b", r"\bcombitech\b"],
    },
    {
        "key": "construction_contractor",
        "label": "construction contractor and property developer",
        "model": "low-margin project contracting, often with an own-development "
                 "arm whose earnings are lumpy and balance-sheet heavy; "
                 "profitability is a function of project risk management",
        "end_markets": "Nordic residential, commercial and civil construction; "
                       "directly rate-sensitive",
        "patterns": [r"\bskanska\b", r"\bncc\b", r"\bpeab\b", r"^jm$", r"^jm ",
                     r"\bbonava\b", r"\bveidekke\b", r"\bconsti\b", r"\byit\b"],
    },
    {
        "key": "guarding_cash_alarm_services",
        "label": "manned guarding, cash handling and monitored alarms",
        "model": "long-contract recurring services; guarding is labour "
                 "arbitrage with thin margins, monitored alarms are a "
                 "subscription annuity with very different economics",
        "end_markets": "corporate and public-sector security budgets, "
                       "residential alarm subscribers",
        "patterns": [r"\bsecuritas\b", r"\bloomis\b", r"\bverisure\b",
                     r"\bavarn\b"],
    },
    {
        "key": "access_and_safety_products",
        "label": "access, locking and personal safety products",
        "model": "branded hardware with a specification-led sales cycle and a "
                 "long replacement tail; bolt-on M&A into adjacent niches",
        "end_markets": "commercial and residential building stock, defence and "
                       "first responders",
        "patterns": [r"assa abloy", r"\binvisio\b", r"\bmips\b", r"\bdometic\b",
                     r"\bthule\b", r"\bhusqvarna\b"],
    },
    {
        "key": "vertical_market_software",
        "label": "vertical-market software / recurring licence",
        "model": "niche mission-critical software sold to one industry, high "
                 "gross retention, ARR compounding plus tuck-in acquisitions; "
                 "capitalised development is the accounting swing factor",
        "end_markets": "one narrow professional or public-sector vertical per "
                       "product line",
        "patterns": [r"vitec software", r"\baddnode\b", r"\bkarnov\b",
                     r"lime technologies", r"\bsmartcraft\b", r"\benea\b",
                     r"\bformpipe\b", r"\bfortnox\b", r"\bupsales\b",
                     r"\badmicom\b", r"\bqt group\b"],
    },
    {
        "key": "it_services_consultancy",
        "label": "IT services and consulting",
        "model": "billable consultants and managed services; headcount-linked "
                 "revenue, utilisation-driven margin, no operating leverage",
        "end_markets": "enterprise and public-sector IT budgets in the Nordics",
        "patterns": [r"\btieto\b", r"\btietoevry\b", r"\bproact\b",
                     r"\bdustin\b", r"\bhiq\b", r"\bcygni\b", r"\bnetcompany\b",
                     r"\bcgi\b"],
    },
    {
        "key": "game_developer_publisher",
        "label": "video game developer / publisher",
        "model": "hit-driven content with capitalised development cost and "
                 "long, uncertain amortisation; user acquisition spend for "
                 "mobile, back-catalogue annuity for PC/console",
        "end_markets": "global consumer entertainment spend; platform-holder "
                       "dependency (Steam, Apple, Google)",
        "patterns": [r"\bembracer\b", r"paradox interactive", r"\bstillfront\b",
                     r"modern times group", r"\bmtg\b", r"\bremedy\b",
                     r"\brovio\b", r"\bg5 entertainment\b", r"\basmodee\b",
                     r"\bstarbreeze\b", r"\bthunderful\b"],
    },
    {
        "key": "apparel_and_sportswear_retail",
        "label": "apparel / sportswear retail and brands",
        "model": "seasonal inventory risk, gross-margin-led P&L, store or "
                 "e-commerce fixed-cost base; brand strength decides pricing",
        "end_markets": "consumer discretionary spend, weather and fashion cycle",
        "patterns": [r"hennes\s*&?\s*mauritz", r"^h\s*&\s*m", r"\bboozt\b",
                     r"\bnelly\b", r"\brvrc\b", r"\bnew wave\b",
                     r"fenix outdoor", r"\bbjorn borg\b", r"\bbj[oö]rn borg\b"],
    },
    {
        "key": "value_and_hardware_retail",
        "label": "value / hardware big-box retail",
        "model": "volume retail on thin gross margin with a large store estate; "
                 "like-for-like growth and store rollout are the whole model",
        "end_markets": "Nordic household spend on home, garden and DIY",
        "patterns": [r"clas ohlson", r"\bbyggmax\b", r"\brusta\b",
                     r"\bmekonomen\b", r"\bmeko\b", r"\bjula\b", r"\bkesko\b"],
    },
    {
        "key": "property_landlord",
        "label": "property owner / landlord",
        "model": "rental income against leveraged asset base; earnings are an "
                 "interest-rate and valuation story, EBIT multiples are close "
                 "to meaningless and EPRA NAV and yield gap are the metrics",
        "end_markets": "Nordic commercial, residential, logistics or social "
                       "infrastructure tenants",
        "patterns": [r"\bbalder\b", r"\bcastellum\b", r"\bfabege\b",
                     r"\bwihlborgs\b", r"\bhufvudstaden\b", r"\bkl[oö]vern\b",
                     r"\bpandox\b", r"\bcatena\b(?! media)", r"\bsagax\b",
                     r"\bdiös\b", r"\bdios\b", r"\bnyfosa\b", r"\bcibus\b",
                     r"\bplatzer\b", r"\btrianon\b", r"\bemilshus\b",
                     r"\bstendörren\b", r"\bheba\b", r"\bjohn mattson\b",
                     r"\bsveafastigheter\b", r"\bal[tm]ra fastigheter\b"],
    },
    {
        "key": "nordic_bank",
        "label": "deposit-taking bank",
        "model": "net interest income on a leveraged balance sheet under "
                 "capital regulation; EV and EBIT multiples do not apply, "
                 "P/E, P/TBV and RoTE do",
        "end_markets": "Nordic household and corporate credit demand",
        "patterns": [r"\bnordea\b", r"handelsbanken", r"\bswedbank\b", r"^seb",
                     r"\bnoba\b", r"\bavanza\b", r"\bnordnet\b", r"\bresurs\b",
                     r"\bcollector\b", r"\bnorion\b", r"\bdanske bank\b",
                     r"\bjyske\b", r"\bsydbank\b", r"\bop \b", r"\bmorrow bank\b"],
    },
    {
        "key": "medtech_devices",
        "label": "medical device / diagnostics manufacturer",
        "model": "regulated hardware plus a consumables razor-blade tail; long "
                 "sales cycles into hospital capex budgets, high gross margin",
        "end_markets": "hospital systems and reimbursement regimes, mostly "
                       "Europe and North America",
        "patterns": [r"\bgetinge\b", r"\belekta\b", r"\barjo\b", r"\bbiotage\b",
                     r"\bcellavision\b", r"\bboule\b", r"\bxvivo\b",
                     r"\bmaquet\b", r"\bambu\b", r"\bcoloplast\b",
                     r"\bdemant\b", r"\bgn store nord\b"],
    },
    {
        "key": "medtech_distribution",
        "label": "medical technology distribution",
        "model": "third-party medtech distribution and own-brand niches; "
                 "working-capital heavy, margin is service and logistics, "
                 "acquisitive",
        "end_markets": "Nordic and European hospitals and laboratories",
        "patterns": [r"\baddlife\b", r"\bmedcap\b", r"asker healthcare",
                     r"\bvimian\b", r"\bmedistim\b"],
    },
]

_ARCH_BY_KEY = {a["key"]: a for a in ARCHETYPES}
_ARCH_COMPILED = [(a["key"], [re.compile(p, re.I) for p in a["patterns"]])
                  for a in ARCHETYPES]

# Weights. They sum to 1.0 and are RENORMALISED per peer over the dimensions
# that were actually available, so a peer missing ESEF data is not silently
# rewarded with a zero it never earned - its coverage fraction is printed
# instead.
WEIGHTS = {
    "archetype": 0.22,      # CURATED
    "size": 0.15,
    "cocyclicality": 0.13,
    "margin": 0.12,
    "roic": 0.12,
    "growth": 0.11,
    "capital_intensity": 0.08,
    "icb": 0.07,
}
CURATED_DIMS = {"archetype"}

# Tolerances: the difference at which similarity reaches zero.
TOL_GROWTH = 0.20        # 20pp of revenue CAGR
TOL_MARGIN = 0.15        # 15pp of EBIT margin
TOL_ROIC = 0.15          # 15pp of ROIC
TOL_CAPEX = 0.06         # 6pp of capex/sales - a wide gap in practice

NOT_COMPUTED = [
    ("end-market mix",
     "which industries and geographies the revenue actually comes from; ESEF "
     "segment tagging is optional and mostly absent"),
    ("geographic revenue split",
     "filer domicile is known, revenue by country is not"),
    ("customer concentration",
     "not tagged; decisive for B2B suppliers with a handful of licensees"),
    ("competitive position",
     "market share, pricing power and switching costs are qualitative"),
    ("accounting comparability",
     "capitalised development, IFRS 16 treatment, goodwill from acquisitions "
     "and non-IFRS 'adjusted' bridges differ between these companies"),
    ("regulatory exposure",
     "licence regimes, tariffs and gaming duties are company-specific text"),
]

# ESEF concept fallbacks beyond what esef_fundamentals.py already defines.
# Sandvik tags LongtermBorrowings, which the shared module's list misses, so
# net debt would come out DATA NOT AVAILABLE without these.
EXTRA_CONCEPTS = {
    "borrowings_noncurrent": ["LongtermBorrowings", "NoncurrentBorrowings",
                              "BorrowingsNoncurrent", "Borrowings"],
    "borrowings_current": ["CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
                           "ShorttermBorrowings", "CurrentBorrowings"],
    "lease_noncurrent": ["LeaseLiabilitiesNoncurrent", "NoncurrentLeaseLiabilities"],
    "lease_current": ["LeaseLiabilitiesCurrent", "CurrentLeaseLiabilities"],
    # MINORITIES. esef_fundamentals resolves net_income as ProfitLoss first,
    # which is the GROUP result including non-controlling interests. A market
    # cap buys the PARENT's share of that, so dividing one by the other prints
    # earnings the shareholder does not own. Storskogen FY2024: group +116m
    # SEK, attributable to owners of the parent -52m - a plausible positive
    # P/E for a year in which shareholders lost money. Both legs are pulled
    # separately here so the attributable one can be preferred and the gap
    # disclosed, and so the NCI stake can be added to enterprise value where
    # EV is compared against a wholly-consolidated EBIT.
    "net_income_owners": ["ProfitLossAttributableToOwnersOfParent"],
    "net_income_group": ["ProfitLoss"],
    "nci_profit": ["ProfitLossAttributableToNoncontrollingInterests"],
    "nci_equity": ["EquityAttributableToNoncontrollingInterests",
                   "NoncontrollingInterests"],
}
# Flow concepts among the additions above; the rest are balance-sheet instants.
EXTRA_DURATION = {"net_income_owners", "net_income_group", "nci_profit"}
WANTED = ["revenue", "operating_income", "net_income", "cfo", "capex", "equity",
          "total_assets", "current_liabilities", "cash", "tax", "pretax_income",
          "goodwill", "depreciation_amort"]

CALLS = {"nasdaq": 0, "esef": 0}


# ---------------------------------------------------------------------------
# disk cache
# ---------------------------------------------------------------------------
def _cache_path(key):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:120]
    return os.path.join(CACHE, safe + ".json")


def cached(key, ttl, producer):
    """Read-through JSON cache. A miss calls producer(); a producer failure is
    cached as nothing, so the next run retries."""
    os.makedirs(CACHE, exist_ok=True)
    path = _cache_path(key)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (ValueError, OSError):
            pass
    value = producer()
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass
    return value


def cache_age(key):
    """Seconds since this cache entry was written, or None if it is not on disk.

    Needed because 'retrieved today' was hard-coded next to figures that could
    be a week old.
    """
    try:
        return time.time() - os.path.getmtime(_cache_path(key))
    except OSError:
        return None


def stamp_from_age(age, fmt="%Y-%m-%d %H:%M"):
    if age is None:
        return None
    return (datetime.datetime.now()
            - datetime.timedelta(seconds=age)).strftime(fmt)


def guarded(fn, *a, **kw):
    """nordic_shares and esef_fundamentals raise SystemExit on a bad response.
    Inside a worker thread that is just an exception; turn it into None so one
    dead instrument does not kill the run."""
    try:
        return fn(*a, **kw)
    except (SystemExit, Exception):        # noqa: BLE001 - deliberate catch-all
        return None


def pmap(fn, items):
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return list(pool.map(fn, items))


# ---------------------------------------------------------------------------
# Nasdaq layer
# ---------------------------------------------------------------------------
def _screener_currencies(market):
    """orderbookId -> quote currency, read straight off the Nasdaq screener.

    A FALLBACK ONLY. nordic_shares.universe() is expected to carry `currency`
    per row; a revision that dropped it is why the currency of a listing used
    to be assumed from the venue, and that assumption is wrong often enough to
    matter: Verisure is a EUR line on Stockholm, so its 9.4bn EUR market cap
    was labelled and compared as 9.4bn SEK - an 11x understatement that then
    drove the size gate and scored Securitas, its one genuine comparable, as
    8.8x away on size.
    """
    def go():
        out = {}
        for category, segment in ns.SEGMENTS:
            params = {"category": category, "market": market,
                      "tableonly": "false"}
            if segment:
                params["segment"] = segment
            CALLS["nasdaq"] += 1
            data = guarded(ns.api, "/screener/shares", **params)
            for r in ((data or {}).get("instrumentListing") or {}).get("rows") or []:
                if r.get("orderbookId") and r.get("currency"):
                    out[r["orderbookId"]] = r["currency"]
        return out
    return cached("screener-ccy-%s" % market, TTL_UNIVERSE, go)


def universe(market):
    def go():
        CALLS["nasdaq"] += 4       # charged on a MISS only, not on cache hits
        return ns.universe(market)
    rows = cached("universe-%s" % market, TTL_UNIVERSE, go) or []
    backfill = None
    if rows and not any(r.get("currency") for r in rows):
        backfill = _screener_currencies(market)
    for r in rows:
        r["market"] = market
        if not r.get("currency") and backfill:
            r["currency"] = backfill.get(r["orderbookId"])
    return rows


def instrument(obid):
    """Nasdaq reference data for one listed class, with the age of the copy.

    marketCap travels in this payload and is cached for a week alongside the
    share count. It is fine as a size-gate input and NOT fine as the numerator
    of a multiple, so every consumer is handed `_as_of` and `_age_hours` and
    has to say which it is using.
    """
    key = "summary-%s" % obid

    def go():
        CALLS["nasdaq"] += 1
        return guarded(ns.summary, obid)
    val = cached(key, TTL_INSTRUMENT, go)
    if not val:
        return val
    age = cache_age(key)
    val = dict(val)
    val["_as_of"] = stamp_from_age(age)
    val["_age_hours"] = None if age is None else round(age / 3600.0, 1)
    return val


def closes(obid, frm, to):
    def go():
        CALLS["nasdaq"] += 1
        bars = guarded(ns.price_history, obid, frm, to) or []
        return {b["date"]: b["close"] for b in bars}
    return cached("px-%s-%s" % (obid, to), TTL_PRICES, go)


_PRICE_RE = re.compile(r"-?[\d,]*\d(?:\.\d+)?")


def live_quote(obid):
    """Last traded price, its currency and the exchange's own timestamp for it.

    nordic_shares.quote() reads qdHeader['lastPrice'], a key the endpoint does
    not publish: the price is at qdHeader.primaryData.lastSalePrice as
    'SEK 390.60' and the moment of it at .lastTradeTimestamp. So the old call
    returned last=None and an as_of of 'Trading' - which is exactly why the
    table's PRICE AS OF column said "Trading", and why the market cap in every
    multiple silently came from the seven-day instrument cache instead of from
    a price. Parsed here rather than there because this file owns neither
    module; ns.quote() is still tried if the payload shape changes.
    """
    key = "quote2-%s" % obid

    def go():
        CALLS["nasdaq"] += 1
        d = guarded(ns.api, "/instruments/%s/info" % obid, assetClass="SHARES")
        h = (d or {}).get("qdHeader") or {}
        prim = h.get("primaryData") or {}
        m = _PRICE_RE.search(str(prim.get("lastSalePrice") or ""))
        last = ns.num(m.group(0)) if m else None
        if last is not None:
            return {"last": last, "currency": h.get("currency"),
                    "as_of": prim.get("lastTradeTimestamp"),
                    "market_status": h.get("marketStatus"),
                    "source": "Nasdaq instrument info (lastSalePrice)"}
        q = guarded(ns.quote, obid) or {}
        return {"last": q.get("last"), "currency": q.get("currency"),
                "as_of": q.get("as_of"), "market_status": q.get("as_of"),
                "source": "nordic_shares.quote"}
    # Deliberately short TTL: this is the price leg of every multiple.
    val = cached(key, TTL_QUOTE, go)
    if not val:
        return val
    val = dict(val)
    age = cache_age(key)
    val["_age_seconds"] = None if age is None else int(age)
    val["_retrieved"] = stamp_from_age(age)
    return val


def fx_rates():
    """Units of each currency per EUR, from ECB reference rates.

    Needed the moment --nordic is used: an Icelandic issuer's market cap comes
    back in ISK and a Swedish one in SEK, so an unconverted size comparison
    puts a 900m EUR grocer next to a 15bn EUR target and calls them the same
    size. Two free keyless sources, tried in order; if both fail, the size
    dimension is dropped for cross-currency candidates rather than faked.

    api.frankfurter.app republishes the ECB daily fixing. www.ecb.europa.eu
    itself is not used: its TLS chain does not validate against the default
    Windows certificate store in this environment.
    """
    def go():
        import urllib.request
        ua = {"User-Agent": "Mozilla/5.0 (compatible; investment-analyst-skill/1.0)",
              "Accept": "application/json"}
        for url, parse in (
            ("https://api.frankfurter.app/latest?base=EUR",
             lambda d: (d.get("rates"), d.get("date"), "ECB via frankfurter.app")),
            ("https://open.er-api.com/v6/latest/EUR",
             lambda d: (d.get("rates"), d.get("time_last_update_utc"),
                        "exchangerate-api.com")),
        ):
            try:
                req = urllib.request.Request(url, headers=ua)
                with urllib.request.urlopen(req, timeout=25) as r:
                    rates, date, src = parse(json.loads(r.read()))
                if rates:
                    rates["EUR"] = 1.0
                    return {"rates": rates, "date": date, "source": src}
            except Exception:      # noqa: BLE001 - any failure falls through
                continue
        return None
    return cached("fx-eur", 12 * 3600, go)


_FX = {"loaded": False, "table": None}


def to_eur(amount, ccy):
    if amount is None or not ccy:
        return None
    if ccy == "EUR":
        return amount
    if not _FX["loaded"]:
        _FX["table"] = fx_rates()
        _FX["loaded"] = True
    tbl = _FX["table"]
    rate = (tbl or {}).get("rates", {}).get(ccy)
    return (amount / rate) if rate else None


def issuer_key(row):
    """Group listed classes into issuers. No issuer id is exposed, so the
    symbol root is the only key available - see nordic_shares.root_symbol."""
    return (row["market"], ns.root_symbol(row["symbol"]))


def build_issuers(markets):
    issuers = {}
    for m in markets:
        for r in universe(m):
            k = issuer_key(r)
            it = issuers.setdefault(k, {"market": m, "root": k[1], "classes": [],
                                        "name": r["name"], "sector": r["sector"],
                                        "segment": r["segment"]})
            it["classes"].append(r)
            # Preserve the highest segment seen across classes.
            if SEGMENT_ORDER.index(r["segment"]) < SEGMENT_ORDER.index(it["segment"]):
                it["segment"] = r["segment"]
    for it in issuers.values():
        it["display"] = clean_name(it["name"])
        it["primary"] = pick_primary(it["classes"])
        it["archetype"] = archetype_of(it["display"])
    return issuers


def pick_primary(classes):
    """The class that carries the meaningful price series. For a dual-class
    Swedish issuer that is normally the B share; otherwise the plain line."""
    b = [c for c in classes if (c["symbol"] or "").endswith(" B")]
    if b:
        return b[0]
    plain = [c for c in classes if " " not in (c["symbol"] or "")]
    return (plain or classes)[0]


CLASS_SUFFIX = re.compile(r"\s+(A|B|C|D|R|SDB|PREF|Pref)$")


def clean_name(name):
    return CLASS_SUFFIX.sub("", (name or "").strip())


def archetype_of(name):
    for key, pats in _ARCH_COMPILED:
        for p in pats:
            if p.search(name or ""):
                return key
    return None


# ---------------------------------------------------------------------------
# ESEF layer
# ---------------------------------------------------------------------------
_STOPWORDS = re.compile(
    r"\b(ab|abp|oyj|oy|plc|asa|a/s|as|publ|aktiebolag|holding|holdings|group|"
    r"groups|international|industrier|sdb|nv|corporation|corp|company|the|"
    r"koncern|bolaget|i)\b")


_NORM_CACHE = {}


def norm_name(s):
    # Memoised: lei_for() now runs across the whole country index once per
    # surviving candidate, so the same few thousand filer names are normalised
    # dozens of times per run.
    key = s
    hit = _NORM_CACHE.get(key)
    if hit is not None:
        return hit
    s = (s or "").lower()
    s = CLASS_SUFFIX.sub("", s).lower()
    s = s.replace("&", " och ").replace(".", " ")
    s = re.sub(r"[^a-z0-9åäöøæ ]", " ", s)
    s = _STOPWORDS.sub(" ", s)
    out = " ".join(s.split())
    _NORM_CACHE[key] = out
    return out


def esef_index(country):
    """Name -> LEI for every ESEF filer in one country.

    esef_fundamentals.search_index() pages the same data but re-fetches it per
    lookup, and each page is ~10 seconds. A peer run resolves a dozen names, so
    the whole country index is built once and cached for a week instead.
    """
    def go():
        out = {}
        for page in range(1, 9):
            params = {"filter[country]": country, "include": "entity",
                      "page[size]": "500", "page[number]": str(page),
                      "sort": "-period_end"}
            CALLS["esef"] += 1
            data = guarded(ef.get_json,
                           ef.FILINGS_API + "?" + urllib.parse.urlencode(params))
            if not data:
                break
            rows = data.get("data") or []
            if not rows:
                break
            names = {e["id"]: e["attributes"].get("name")
                     for e in data.get("included", [])}
            for it in rows:
                rel = ((it.get("relationships") or {}).get("entity") or {}).get("data") or {}
                nm = names.get(rel.get("id"))
                if not nm:
                    continue
                a = it["attributes"]
                lei = (a.get("fxo_id") or "").split("-")[0]
                prev = out.get(lei)
                if prev is None or a["period_end"] > prev["latest"]:
                    out[lei] = {"name": nm, "latest": a["period_end"]}
            if len(rows) < 500:
                break
        return out
    return cached("esef-index-%s" % country, TTL_ESEF_INDEX, go)


def lei_for(name, indices):
    """Resolve a Nasdaq listing name to an ESEF filer LEI.

    Three passes, tightest first. Exact normalised equality, then prefix
    containment, then token-subset ("Ericsson" inside "Telefonaktiebolaget LM
    Ericsson"). An ambiguous token-subset match is rejected rather than
    guessed - measured hit rate on Stockholm large+mid cap is ~85%, and the
    misses are mostly foreign filers (ABB, TRATON, Nordea) and recent IPOs
    with no annual report filed yet.
    """
    key = norm_name(name)
    if not key:
        return None
    exact, near = [], []
    ktoks = set(key.split())
    for country, idx in indices.items():
        for lei, v in idx.items():
            nk = norm_name(v["name"])
            if not nk:
                continue
            if nk == key:
                exact.append((lei, v, country))
                continue
            ntoks = set(nk.split())
            # One-directional prefix matching is what broke this. "Volvo" is a
            # prefix of "Volvo Car", so Volvo Car landed in a bucket that was
            # consulted BEFORE the bucket holding "Aktiebolaget Volvo" - and the
            # first non-empty bucket won. AB Volvo's market cap was then divided
            # by Volvo Car's earnings and printed as a P/E of 44.5x against a
            # true 14.7x, citing a real LEI. Four such collisions exist on
            # Stockholm alone. Everything short of exact equality is now a
            # single "near" pool, and a near pool with more than one distinct
            # issuer resolves to nothing at all.
            if ktoks and (ktoks <= ntoks or ntoks <= ktoks):
                near.append((lei, v, country))

    if len(exact) == 1:
        lei, v, country = exact[0]
        return {"lei": lei, "esef_name": v["name"], "latest": v["latest"],
                "country": country}

    pool = exact or near
    distinct = {l for l, _v, _c in pool}
    if len(distinct) == 1:
        lei, v, country = pool[0]
        return {"lei": lei, "esef_name": v["name"], "latest": v["latest"],
                "country": country}
    if len(distinct) > 1:
        # Refuse. A wrong filer is far worse than no filer: it publishes another
        # company's fundamentals under this one's market cap, with a real LEI
        # attached and nothing on the face of the output to reveal the swap.
        return {"lei": None, "esef_name": None, "latest": None, "country": None,
                "ambiguous_candidates": sorted(
                    (v["name"], l) for l, v, _c in pool)[:6]}
    return None


def esef_facts(lei, filings=3):
    """Merged annual facts for one filer, newest restatement winning.

    Each annual report carries the prior year as a comparative, so three
    filings give roughly four fiscal years - enough for a 3-year revenue CAGR
    and an average invested-capital base.
    """
    def go():
        CALLS["esef"] += 1
        fl = guarded(ef.list_filings, lei, filings) or []
        merged, periods_seen = {}, []
        concepts = dict(ef.CONCEPTS)
        concepts.update(EXTRA_CONCEPTS)
        duration = set(ef.DURATION) | EXTRA_DURATION
        for f in fl:
            CALLS["esef"] += 1
            doc = guarded(ef.get_json, ef.FILINGS_BASE + f["json_url"])
            if not doc:
                continue
            facts = ef.extract(doc)
            periods_seen.append(f["period_end"])
            for metric in list(WANTED) + list(EXTRA_CONCEPTS):
                names = concepts.get(metric)
                if not names:
                    continue
                for period, (val, unit, concept) in ef.pick(
                        facts, names, metric in duration).items():
                    merged.setdefault(metric, {}).setdefault(
                        period, {"v": val, "u": unit, "c": concept})
        if not merged:
            return None
        units = {d["u"] for m in merged.values() for d in m.values() if d.get("u")}
        ccy = sorted({u.split(":", 1)[1] for u in units
                      if u.startswith("iso4217:") and "/" not in u})
        # `currency` here is the currency of the WHOLE three-filing merge and
        # is therefore None the moment any one stray comparative is tagged in
        # another unit. It is kept for provenance only. The figure that decides
        # whether a multiple may be formed is the LATEST fiscal year's currency
        # - see reporting_currency() and derive().
        return {"lei": lei, "currency": ccy[0] if len(ccy) == 1 else None,
                "currencies": ccy, "filings": [f["fxo_id"] for f in fl],
                "data": merged}
    # v2: carries the attributable-to-owners and non-controlling-interest legs.
    return cached("esef-facts-v2-%s-%d" % (lei, filings), TTL_ESEF_FACTS, go)


def series(pack, metric):
    """{period_end: value} for one metric, chronologically usable."""
    if not pack:
        return {}
    return {p: d["v"] for p, d in (pack["data"].get(metric) or {}).items()}


def series_units(pack, metric):
    """{period_end: xbrl unit} for one metric - the other half of every value."""
    if not pack:
        return {}
    return {p: d.get("u") for p, d in (pack["data"].get(metric) or {}).items()}


def series_concepts(pack, metric):
    """{period_end: the IFRS concept that actually matched}.

    Which fallback won changes the meaning: ifrs-full:Borrowings is TOTAL
    borrowings, while LongtermBorrowings is one leg of them, and they sit in
    the same fallback list.
    """
    if not pack:
        return {}
    return {p: d.get("c") for p, d in (pack["data"].get(metric) or {}).items()}


def ccy_of_unit(unit):
    """'iso4217:SEK' -> 'SEK'. Per-share and ratio units ('iso4217:SEK/shares')
    are not a reporting currency and are ignored."""
    if not unit or not unit.startswith("iso4217:") or "/" in unit:
        return None
    return unit.split(":", 1)[1]


def reporting_currency(pack, period):
    """Every ISO currency appearing in ONE fiscal year's facts, sorted.

    Reading it across the whole merge instead is how Betsson lost its FX
    conversion. Betsson redenominated from SEK to EUR in 2021, so the FY2020
    comparative inside the oldest filing is still SEK; the merged fact set then
    held two currencies, the reporting currency was set to None, multiples()
    failed its truthiness test on it and did NO conversion at all - printing a
    SEK market cap over EUR earnings as P/E 63.0x against a true 5.7x, with a
    blank currency label where the mismatch should have been. One year's facts
    are internally consistent; the merge is not.
    """
    found = set()
    for periods in (pack.get("data") or {}).values():
        d = periods.get(period)
        if not d:
            continue
        c = ccy_of_unit(d.get("u"))
        if c:
            found.add(c)
    return sorted(found)


def derive(pack):
    """Turn merged ESEF facts into the comparable ratios the score needs.

    Every definition here is stated so it can be argued with:
      reporting currency  the currency of the LATEST fiscal year's facts only.
                         Not of the merged set: a redenomination leaves the old
                         unit in an old comparative and would make the merge
                         look ambiguous forever after.
      EBIT margin        operating income / revenue, latest FY
      revenue CAGR       latest FY over the oldest FY REPORTED IN THE SAME UNIT,
                         annualised over ELAPSED CALENDAR YEARS between the two
                         balance-sheet dates - not over the number of
                         observations, which is a different and usually smaller
                         number when a year is missing from the merge.
      ROIC               EBIT x (1 - effective tax) / average invested capital,
                         invested capital = total assets - current liabilities.
                         This is a returns-on-total-capital measure and it
                         INCLUDES acquisition goodwill, so a serial acquirer
                         will look far worse here than on the "ROCE excluding
                         goodwill" it reports itself. That is deliberate - the
                         goodwill was paid for in cash. Where the filed
                         effective tax rate is unusable a flat rate is assumed
                         and the fact is flagged, never buried.
      net income         profit ATTRIBUTABLE TO OWNERS OF THE PARENT wherever
                         that line is tagged, because the market cap in the
                         numerator buys that and not the group total.
      net debt           borrowings (non-current + current) + separately tagged
                         lease liabilities - cash. If either borrowings leg or
                         cash is untagged this is DATA NOT AVAILABLE and EV
                         multiples are suppressed rather than approximated.
    """
    if not pack:
        return None
    rev = series(pack, "revenue")
    if not rev:
        return None
    periods = sorted(rev)
    last = periods[-1]
    first = periods[0]

    ccy_last = reporting_currency(pack, last)
    out = {"fy_end": last, "fy_first": first, "years": len(periods),
           "currency": ccy_last[0] if len(ccy_last) == 1 else None,
           "currencies_latest_fy": ccy_last,
           "currencies_all_years": pack.get("currencies") or [],
           "lei": pack["lei"], "filings": pack.get("filings", [])}
    if len(ccy_last) != 1:
        out["currency_note"] = (
            "the latest fiscal year (%s) carries %s, so there is no single "
            "reporting currency to convert a market cap into"
            % (last, ", ".join(ccy_last) if ccy_last
               else "no ISO-4217 monetary unit"))
    out["revenue"] = rev[last]

    # --- revenue CAGR ---------------------------------------------------
    # Two separate errors lived on this line.
    #   (1) The exponent was 1/(observations - 1). Investor's merge holds
    #       2021, 2022 and 2024 - three elapsed years, two gaps - and printed
    #       "2y CAGR +24.6%" for a true +15.9% over the actual period.
    #   (2) There was no unit check. Betsson's rev[2020] is in SEK and
    #       rev[2024] in EUR after the 2021 redenomination, and compounding
    #       one against the other printed "4y CAGR -35.5%" for a company
    #       growing around +16% a year.
    # Elapsed calendar time now sets the exponent, and any period whose unit
    # differs from the latest year's is dropped from the base entirely.
    runits = series_units(pack, "revenue")
    ulast = runits.get(last)
    same_unit = [p for p in periods if runits.get(p) == ulast]
    out["revenue_unit"] = ulast
    out["cagr"] = None
    out["cagr_years"] = None
    out["cagr_periods_dropped"] = [{"period": p, "unit": runits.get(p)}
                                   for p in periods if runits.get(p) != ulast]
    base = same_unit[0] if same_unit else None
    if base is not None and base != last and rev[base] > 0 and rev[last] > 0:
        try:
            elapsed = ((datetime.date.fromisoformat(last)
                        - datetime.date.fromisoformat(base)).days / 365.2425)
        except ValueError:
            elapsed = None
        if elapsed and elapsed >= 0.75:
            out["cagr"] = (rev[last] / rev[base]) ** (1.0 / elapsed) - 1.0
            out["cagr_years"] = round(elapsed, 1)
            out["cagr_from"] = base
            out["cagr_to"] = last
            out["cagr_observations"] = len(same_unit)

    ebit = series(pack, "operating_income").get(last)
    out["ebit"] = ebit
    out["ebit_margin"] = (ebit / rev[last]) if (ebit is not None and rev[last]) else None

    ta = series(pack, "total_assets")
    cl = series(pack, "current_liabilities")
    ic = {p: ta[p] - cl[p] for p in ta if p in cl}
    out["invested_capital"] = ic.get(last)

    # --- effective tax rate ---------------------------------------------
    # abs(tax)/abs(pre) turned a tax CREDIT into a positive rate: IFRS tags a
    # tax expense positive, so the SIGNED ratio is the effective rate and a
    # negative one is real information, not noise. Where the result is not
    # usable a flat rate is substituted - which is a legitimate thing to do
    # and an illegitimate thing to hide, since ROIC then carries 0.12 of the
    # score. tax_rate_imputed travels with it into both outputs.
    tax = series(pack, "tax").get(last)
    pre = series(pack, "pretax_income").get(last)
    raw = (tax / pre) if (tax is not None and pre) else None
    out["tax_rate_effective"] = raw
    rate = raw if (raw is not None and 0.10 <= raw <= 0.40) else None
    out["tax_rate"] = rate
    eff = rate if rate is not None else DEFAULT_TAX_RATE
    out["tax_rate_used"] = eff
    out["tax_rate_imputed"] = rate is None
    out["tax_rate_note"] = None if rate is not None else (
        "filed effective tax rate %s is outside 10-40%% (loss year, credit or "
        "one-off), so ROIC assumes %.0f%%"
        % ("not computable" if raw is None else "%+.1f%%" % (100 * raw),
           100 * DEFAULT_TAX_RATE))

    # `ic` is keyed by period and may simply not contain `last`: Embracer has
    # revenue and total assets for FY2024-03-31 but current_liabilities only to
    # 2023-03-31, so invested capital has no entry for the latest year. Guarding
    # only on `ic` being non-empty crashed every run whose candidate pool
    # contained such a filer.
    if ebit is not None and ic and last in ic:
        prior = sorted(p for p in ic if p < last)
        basis = ((ic[last] + ic[prior[-1]]) / 2.0) if prior else ic[last]
        out["roic"] = (ebit * (1 - eff) / basis) if basis else None
        out["roic_basis"] = "average" if prior else "closing"
    else:
        out["roic"] = None

    capex = series(pack, "capex").get(last)
    out["capex"] = capex
    out["capex_sales"] = (abs(capex) / rev[last]) if (capex is not None and rev[last]) else None
    out["asset_turnover"] = (rev[last] / ta[last]) if ta.get(last) else None

    # (goodwill or 0) reported a serial acquirer with an untagged Goodwill line
    # as 0% goodwill - the exact case the archetype table exists to surface.
    # Untagged is not zero.
    gw = series(pack, "goodwill").get(last)
    out["goodwill"] = gw
    out["goodwill_share"] = ((gw / ta[last])
                             if (gw is not None and ta.get(last)) else None)
    out["goodwill_note"] = None if gw is not None else "Goodwill not tagged in ESEF"

    # --- net debt --------------------------------------------------------
    bn = series(pack, "borrowings_noncurrent").get(last)
    bc = series(pack, "borrowings_current").get(last)
    ln = series(pack, "lease_noncurrent").get(last)
    lc = series(pack, "lease_current").get(last)
    cash = series(pack, "cash").get(last)
    out["cash"] = cash
    out["borrowings_noncurrent"] = bn
    out["borrowings_current"] = bc
    out["lease_noncurrent"] = ln
    out["lease_current"] = lc
    out["gross_debt"] = None
    # ifrs-full:Borrowings is TOTAL borrowings and sits at the end of the
    # non-current fallback list, so a filer that tags only the total must not
    # be treated as one missing a leg - nor may the current leg be added to it
    # a second time.
    cc = out.get("currency") or ""
    bn_concept = series_concepts(pack, "borrowings_noncurrent").get(last)
    is_total = (bn_concept == "Borrowings")
    borrow = None
    note = None
    if bn is None and bc is None:
        note = "borrowings not tagged in ESEF"
    elif is_total:
        borrow = bn
        note = "ifrs-full:Borrowings (total) tagged; legs not split"
    elif bn is None:
        # THE DEFECT. The old guard was `bn is None and bc is None`, so a
        # filing that tags only the CURRENT leg passed straight through and the
        # entire long-term debt stack was dropped from enterprise value with
        # net_debt_note saying nothing at all. Twenty-six Stockholm filers sit
        # on this path - Beijer Ref, Latour, Byggmax, Axfood, Inwido, Ellos
        # among them - and every one of them carries long-term debt. Short-term
        # borrowings with genuinely no long-term leg is rare enough that the
        # far likelier reading of this shape is incomplete tagging.
        note = ("only the CURRENT borrowings leg is tagged in ESEF (%s); the "
                "non-current leg - normally the larger of the two - is absent, "
                "so net debt and every EV multiple would be understated. "
                "Suppressed rather than approximated" % money(bc, cc))
    else:
        # The mirror case is NOT symmetric: a balance sheet with long-term debt
        # and no current portion at all is an ordinary state (a bullet facility
        # with nothing due inside a year), so this one is computed and flagged
        # rather than suppressed.
        borrow = bn + (bc or 0)
        if bc is None:
            note = ("no current borrowings line tagged - normal for a filer "
                    "with nothing due within a year, but a current portion "
                    "that exists and is untagged would be missing here")
    if borrow is None:
        out["net_debt"] = None
        out["net_debt_note"] = note
    elif cash is None:
        # This line used to treat an untagged cash balance as zero, which
        # overstates net debt and EV by the whole cash pile without saying so.
        gross = borrow + (ln or 0) + (lc or 0)
        out["gross_debt"] = gross
        out["net_debt"] = None
        out["net_debt_note"] = (
            "cash and cash equivalents not tagged in ESEF; gross debt is %s, "
            "but netting it needs a cash figure this filing does not give - "
            "suppressed rather than treating cash as zero" % money(gross, cc))
    else:
        gross = borrow + (ln or 0) + (lc or 0)
        out["gross_debt"] = gross
        out["net_debt"] = gross - cash
        lease_note = ("leases included" if (ln or lc)
                      else "no separately tagged lease liability - IFRS 16 debt "
                           "may be inside borrowings or missing entirely")
        out["net_debt_note"] = ("%s; %s" % (note, lease_note)) if note else lease_note

    # --- earnings attributable to whom -----------------------------------
    ni_owners = series(pack, "net_income_owners").get(last)
    ni_group = series(pack, "net_income_group").get(last)
    if ni_group is None:
        ni_group = series(pack, "net_income").get(last)
    out["net_income_owners"] = ni_owners
    out["net_income_group"] = ni_group
    out["net_income"] = ni_owners if ni_owners is not None else ni_group
    out["net_income_basis"] = (
        "attributable to owners of the parent" if ni_owners is not None
        else "GROUP profit including non-controlling interests - the "
             "attributable-to-owners line is not tagged in this filing")
    out["minority_interest"] = series(pack, "nci_equity").get(last)
    out["minority_profit"] = series(pack, "nci_profit").get(last)

    cfo = series(pack, "cfo").get(last)
    out["cfo"] = cfo
    out["fcf"] = (cfo - abs(capex)) if (cfo is not None and capex is not None) else None
    return out


# ---------------------------------------------------------------------------
# co-cyclicality
# ---------------------------------------------------------------------------
def residual_correlations(price_map, target_key):
    """Weekly log returns, one equal-weighted factor built from the pool, then
    correlate the residuals.

    Raw correlation between two Stockholm names is mostly index beta and does
    not discriminate. Stripping a pool-level factor leaves the co-movement that
    common market direction does not explain, which is the closest free proxy
    for shared end-market cyclicality. Because the factor is the pool's own
    mean, residuals sum to about zero across the pool: the LEVEL is
    set-relative and only the RANKING within one run means anything.
    """
    keys = [k for k, v in price_map.items() if v]
    if target_key not in keys or len(keys) < 3:
        return {}, None

    # The grid is the TARGET's own weekly dates. Intersecting every series
    # instead would let one recent IPO in the pool truncate the window for
    # everybody - Roeko listed in 2025 and collapsed an Addtech run from 150
    # weekly observations to 73. Short-history names are handled per name.
    tdates = sorted(price_map[target_key])
    grid = tdates[::5]
    if len(grid) < 41:
        return {}, None

    aligned = {}
    for k in keys:
        s = price_map[k]
        row = []
        for i in range(len(grid) - 1):
            a, b = s.get(grid[i]), s.get(grid[i + 1])
            row.append(math.log(b / a) if (a and b and a > 0 and b > 0) else None)
        if sum(1 for x in row if x is not None) >= 40:
            aligned[k] = row
    if target_key not in aligned or len(aligned) < 3:
        return {}, None
    n = len(grid) - 1

    # One equal-weighted factor, built only from names with near-complete
    # history so a stub series cannot distort it.
    full = [k for k, r in aligned.items()
            if sum(1 for x in r if x is not None) >= 0.9 * n] or list(aligned)
    factor = []
    for i in range(n):
        vals = [aligned[k][i] for k in full if aligned[k][i] is not None]
        factor.append(statistics.fmean(vals) if len(vals) >= 3 else None)

    def regress(row):
        idx = [i for i in range(n) if row[i] is not None and factor[i] is not None]
        if len(idx) < 40:
            return None, None, idx
        fm = statistics.fmean([factor[i] for i in idx])
        am = statistics.fmean([row[i] for i in idx])
        fvar = sum((factor[i] - fm) ** 2 for i in idx)
        if fvar <= 0:
            return None, None, idx
        beta = sum((factor[i] - fm) * (row[i] - am) for i in idx) / fvar
        alpha = am - beta * fm
        return {i: row[i] - alpha - beta * factor[i] for i in idx}, beta, idx

    resid, betas, spans = {}, {}, {}
    for k, row in aligned.items():
        r, b, idx = regress(row)
        if r is None:
            continue
        resid[k], betas[k], spans[k] = r, b, idx
    if target_key not in resid:
        return {}, None

    def corr(pairs):
        if len(pairs) < 40:
            return None
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        vx = sum((x - mx) ** 2 for x in xs)
        vy = sum((y - my) ** 2 for y in ys)
        if vx <= 0 or vy <= 0:
            return None
        return sum((xs[i] - mx) * (ys[i] - my)
                   for i in range(len(xs))) / math.sqrt(vx * vy)

    tres, traw = resid[target_key], aligned[target_key]
    out = {}
    for k in resid:
        if k == target_key:
            continue
        shared = sorted(set(spans[target_key]) & set(spans[k]))
        out[k] = {
            "resid_corr": corr([(tres[i], resid[k][i]) for i in shared]),
            "raw_corr": corr([(traw[i], aligned[k][i]) for i in shared]),
            "factor_beta": betas[k],
            "observations": len(shared),
            "partial_history": len(shared) < 0.9 * n,
        }
    meta = {"observations": n, "from": grid[0], "to": grid[-1],
            "pool": len(resid), "factor_names": len(full)}
    return out, meta


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
def band(diff, tol):
    if diff is None:
        return None
    return max(0.0, 1.0 - abs(diff) / tol)


def score_peer(t, p, size_band):
    """Per-dimension similarity in [0,1], then a weight-renormalised total.

    A dimension that could not be computed is left out of both the numerator
    and the denominator. The coverage fraction that results is reported, so a
    0.80 on three dimensions can be told apart from a 0.80 on eight.
    """
    s, why = {}, {}

    # size - the hard one. Two orders of magnitude apart is not a peer set.
    # Compared in EUR so a Stockholm and a Reykjavik line are on one scale.
    if t.get("cap_eur") and p.get("cap_eur"):
        dex = abs(math.log10(p["cap_eur"] / t["cap_eur"]))
        s["size"] = max(0.0, 1.0 - dex / size_band)
        why["size"] = "%.1fx" % (max(p["cap_eur"], t["cap_eur"])
                                 / min(p["cap_eur"], t["cap_eur"]))

    # ICB proximity - kept at a low weight precisely because it is the input
    # everyone over-trusts.
    if t.get("icb") and p.get("icb"):
        if t["icb"] == p["icb"]:
            s["icb"] = 1.0
        elif t["icb"][:2] == p["icb"][:2]:
            s["icb"] = 0.5
        else:
            s["icb"] = 0.0
        why["icb"] = p["icb"]
    elif t.get("sector") and p.get("sector"):
        s["icb"] = 1.0 if t["sector"] == p["sector"] else 0.0
        why["icb"] = p["sector"]

    # archetype - CURATED
    if t.get("archetype"):
        if p.get("archetype") == t["archetype"]:
            s["archetype"] = 1.0
            why["archetype"] = "same"
        elif p.get("archetype"):
            s["archetype"] = 0.0
            why["archetype"] = p["archetype"]
        else:
            # Silence in the table is not evidence against the company.
            s["archetype"] = 0.25
            why["archetype"] = "untagged?"

    tf, pf = t.get("fin"), p.get("fin")
    if tf and pf:
        if tf.get("cagr") is not None and pf.get("cagr") is not None:
            s["growth"] = band(pf["cagr"] - tf["cagr"], TOL_GROWTH)
            why["growth"] = "%+.0f%%" % (100 * pf["cagr"])
        if tf.get("ebit_margin") is not None and pf.get("ebit_margin") is not None:
            s["margin"] = band(pf["ebit_margin"] - tf["ebit_margin"], TOL_MARGIN)
            why["margin"] = "%.0f%%" % (100 * pf["ebit_margin"])
        if tf.get("roic") is not None and pf.get("roic") is not None:
            s["roic"] = band(pf["roic"] - tf["roic"], TOL_ROIC)
            why["roic"] = "%.0f%%" % (100 * pf["roic"])
        if tf.get("capex_sales") is not None and pf.get("capex_sales") is not None:
            s["capital_intensity"] = band(pf["capex_sales"] - tf["capex_sales"],
                                          TOL_CAPEX)
            why["capital_intensity"] = "%.1f%%" % (100 * pf["capex_sales"])

    rc = (p.get("cocyc") or {}).get("resid_corr")
    if rc is not None:
        # -0.10 -> 0, +0.40 -> 1. Calibrated on observed Stockholm residuals,
        # where a genuine sub-industry pair lands at +0.2 to +0.5.
        s["cocyclicality"] = min(1.0, max(0.0, (rc + 0.10) / 0.50))
        why["cocyclicality"] = "%+.2f" % rc

    avail = {k: v for k, v in s.items() if v is not None}
    wsum = sum(WEIGHTS[k] for k in avail)
    if not wsum:
        return None
    known = sum(WEIGHTS[k] * avail[k] for k in avail) / wsum

    # A missing dimension is imputed at 0.5 - "no information", neither
    # evidence for nor against. Renormalising over the available weights
    # instead would REWARD missing data: a company with no ESEF filing would
    # be scored only on the dimensions where it happens to look similar, and
    # would out-rank a fully-documented peer. Both numbers are reported.
    missing_w = 1.0 - wsum
    total = sum(WEIGHTS[k] * avail[k] for k in avail) + 0.5 * missing_w

    computed_w = sum(WEIGHTS[k] for k in avail if k not in CURATED_DIMS)
    computed_total = sum(WEIGHTS[k] for k in WEIGHTS if k not in CURATED_DIMS)
    return {"score": total, "score_on_known": known, "components": avail,
            "evidence": why, "coverage": wsum,
            "computed_coverage": computed_w / computed_total,
            "dims": len(avail)}


# ---------------------------------------------------------------------------
# multiples
# ---------------------------------------------------------------------------
def multiples(entry):
    """Trailing multiples from ESEF fundamentals, the live price and the
    exchange's own share count. Every leg carries its own as-of date, and they
    do not line up - that mismatch is the point of showing them.

    The numerator is a LIVE market cap (last traded price x registered shares)
    wherever a quote could be read, because the reference-data marketCap that
    used to fill this slot is cached for a week and drifts (Sandvik -0.382% in
    three hours). Where no quote was available the reference cap is used and
    market_cap_basis says so, with the age of the copy.
    """
    f = entry.get("fin")
    cap = entry.get("cap_live") or entry.get("market_cap")
    out = {"market_cap": cap,
           "market_cap_basis": entry.get("cap_basis"),
           "market_cap_reference": entry.get("market_cap"),
           "price_ccy": entry.get("ccy"),
           "price_as_of": entry.get("price_as_of"),
           "price_retrieved": entry.get("price_retrieved"),
           "shares": entry.get("shares"),
           "shares_as_of": entry.get("shares_as_of"),
           "fundamentals_as_of": (f or {}).get("fy_end"),
           "report_ccy": (f or {}).get("currency")}
    if not f or not cap:
        out["error"] = "DATA NOT AVAILABLE: no ESEF fundamentals or no market cap"
        return out

    # Evolution quotes in SEK and reports in EUR; Stora Enso quotes in SEK and
    # reports in EUR too. The numerator must be moved into the reporting
    # currency before it is divided by anything, and the rate must be shown.
    #
    # Both currencies must therefore be KNOWN. The old code tested
    # `if pccy and rccy and pccy != rccy`, so an unknown reporting currency
    # fell straight through to the divisions with no conversion and no error -
    # printing Betsson at P/E 63.0x against a true 5.7x and Orron Energy at
    # EV/Sales 86.6x against roughly 7.8x, each with a blank currency label
    # where the mismatch should have been. An unknown currency is now fatal to
    # the row, which is the whole point of DATA NOT AVAILABLE.
    pccy, rccy = entry.get("ccy"), f.get("currency")
    if not rccy:
        out["error"] = ("DATA NOT AVAILABLE: %s - no multiple can be formed "
                        "against an unknown reporting currency"
                        % (f.get("currency_note")
                           or "the reporting currency of the latest fiscal "
                              "year is ambiguous"))
        return out
    if not pccy:
        out["error"] = ("DATA NOT AVAILABLE: the quote currency of this listing "
                        "is unknown, so the market cap cannot be put into %s"
                        % rccy)
        return out
    if pccy != rccy:
        eur = to_eur(cap, pccy)
        conv = None
        if eur is not None:
            tbl = _FX["table"] or {}
            r = tbl.get("rates", {}).get(rccy)
            conv = (eur * r) if r else (eur if rccy == "EUR" else None)
        if conv is None:
            out["error"] = ("DATA NOT AVAILABLE: reports in %s, quoted in %s and "
                            "no FX rate available - convert manually" % (rccy, pccy))
            return out
        out["fx"] = "%s->%s at ECB %s" % (pccy, rccy,
                                          (_FX["table"] or {}).get("date", "?"))
        cap = conv
        out["market_cap_in_report_ccy"] = cap

    nd = f.get("net_debt")
    # Minority interest is a claim on the consolidated assets that EBIT and
    # revenue are struck on, so it belongs in the numerator of an EV multiple
    # for the same reason net debt does. Omitting it understates EV for every
    # group with a material NCI stake.
    mi = f.get("minority_interest")
    ev = (cap + nd + (mi or 0)) if nd is not None else None
    out["net_debt"] = nd
    out["net_debt_note"] = f.get("net_debt_note")
    out["gross_debt"] = f.get("gross_debt")
    out["minority_interest"] = mi
    out["minority_interest_in_ev"] = mi is not None
    out["ev"] = ev

    # The denominators travel with the ratios: a median cannot tell a cheap
    # multiple from a loss without them.
    out["ebit"] = f.get("ebit")
    out["revenue"] = f.get("revenue")
    out["net_income"] = f.get("net_income")
    out["net_income_basis"] = f.get("net_income_basis")
    out["net_income_group"] = f.get("net_income_group")
    out["net_income_owners"] = f.get("net_income_owners")
    out["fcf"] = f.get("fcf")
    out["tax_rate_used"] = f.get("tax_rate_used")
    out["tax_rate_imputed"] = f.get("tax_rate_imputed")

    out["ev_ebit"] = (ev / f["ebit"]) if (ev is not None and f.get("ebit")) else None
    out["ev_sales"] = (ev / f["revenue"]) if (ev is not None and f.get("revenue")) else None
    out["pe"] = (cap / f["net_income"]) if f.get("net_income") else None
    # `if f.get("fcf")` printed n/a for an exactly-zero free cash flow, which is
    # a real and quite interesting number.
    out["fcf_yield"] = (f["fcf"] / cap) if (f.get("fcf") is not None and cap) else None
    return out


def median_eligible(field, m):
    """(eligible, reason) for one multiple entering a peer median.

    A median over mixed signs is not a valuation. EV/EBIT of -8.5x is a loss,
    not a cheap multiple, and medianing it with profitable names produced a
    peer median of -8.5x for a housing set whose profitable members traded at
    10.7-23.8x. Two names with negative EV and negative EBIT would produce a
    POSITIVE ratio that is pure arithmetic, so both legs are tested. An
    unbounded upside let 193.8x, 151.2x and 250.4x into the same statistic.
    """
    v = m.get(field)
    if v is None:
        return False, None
    if field == "fcf_yield":
        return True, None          # the denominator is market cap: always > 0
    den = {"ev_ebit": m.get("ebit"), "ev_sales": m.get("revenue"),
           "pe": m.get("net_income")}[field]
    if den is None or den <= 0:
        return False, ("loss-making" if field != "ev_sales"
                       else "no positive revenue")
    if field != "pe":
        ev = m.get("ev")
        if ev is None or ev <= 0:
            return False, "negative enterprise value"
    bound = SANITY_MAX[field]
    if v > bound:
        return False, "%.0fx is beyond the %.0fx sanity bound" % (v, bound)
    return True, None


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------
def money(v, ccy=""):
    if v is None:
        return "n/a"
    a = abs(v)
    for div, suf in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
        if a >= div:
            return "%.1f%s %s" % (v / div, suf, ccy)
    return "%.0f %s" % (v, ccy)


def pct(v, digits=1):
    return "n/a" if v is None else ("%.*f%%" % (digits, 100 * v))


def ratio(v, digits=1):
    return "n/a" if v is None else ("%.*fx" % (digits, v))


def icb_label(code):
    if not code:
        return "unclassified"
    return "%s %s" % (code, ICB4.get(str(code), ""))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

class _AmbiguousTarget(object):
    """Sentinel resolve_target() returns instead of None when it has already
    printed a candidate list and refused to guess - lets main() skip printing
    a second, misleading "no match" message for a query that in fact matched
    too many issuers, not too few."""


AMBIGUOUS_TARGET = _AmbiguousTarget()


def resolve_target(query, issuers):
    """The issuer key `query` names, or None if nothing matches.

    lei_for() (above) already refuses rather than guesses when a name search
    ties between distinct issuers - "Volvo" matching both AB Volvo and Volvo
    Car AB, four such collisions on Stockholm alone. This predates that fix
    and used `best.sort(...)[0]` to silently pick whichever tied candidate
    had the shortest display name, with no ambiguity check and nothing
    printed about the runner-up. Brought into line with lei_for(): the exact/
    prefix/substring tiers are unchanged, but a tie WITHIN the winning tier
    across more than one distinct issuer now refuses and prints every
    candidate instead of picking one.
    """
    q = query.strip().lower()
    qn = norm_name(query)
    best = []
    for key, it in issuers.items():
        nm = (it["display"] or "").lower()
        sym = (it["root"] or "").lower()
        if nm == q or sym == q or norm_name(it["display"]) == qn:
            best.append((0, key))
        elif nm.startswith(q) or sym.startswith(q):
            best.append((1, key))
        elif q in nm:
            best.append((2, key))
    if not best:
        return None
    best.sort(key=lambda x: (x[0], len(issuers[x[1]]["display"])))
    top_tier = best[0][0]
    tied = [key for tier, key in best if tier == top_tier]
    # A preference line and its ordinary line can land as two DIFFERENT issuer
    # keys (build_issuers/issuer_key does not always merge them) while sharing
    # the identical display name - that is one company, not an identity
    # collision, and must not trip the refusal below. Only refuse when the
    # tied keys resolve to more than one DISTINCT company name.
    distinct_names = {issuers[k]["display"] for k in tied}
    if len(distinct_names) > 1:
        # One row per distinct issuer - a preference/ordinary pair sharing a
        # display name (see above) collapses to its first key here rather
        # than printing the same company twice.
        by_name = {}
        for key in tied:
            by_name.setdefault(issuers[key]["display"], key)
        print("COMPANY_IDENTITY_AMBIGUOUS: %d distinct listed issuers match %r "
              "equally closely. Refusing to guess - building a peer set around "
              "the wrong one looks identical to a correct answer."
              % (len(distinct_names), query))
        print()
        print("  %-30s %-10s %-8s %s" % ("COMPANY", "TICKER", "SEGMENT", "SECTOR"))
        print("  " + "-" * 70)
        for name in sorted(by_name):
            it = issuers[by_name[name]]
            print("  %-30s %-10s %-8s %s"
                  % (it["display"][:30], it.get("root") or "-",
                     it.get("segment") or "-", it.get("sector") or "-"))
        print()
        print("  Re-run with the exact Nasdaq listing name, the ticker, or --nordic")
        print("  narrowed further so only one issuer matches.")
        return AMBIGUOUS_TARGET
    return best[0][1]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("company", help="company name or ticker root, e.g. Sandvik")
    ap.add_argument("--nordic", action="store_true",
                    help="widen the candidate pool to Copenhagen, Helsinki and "
                         "Reykjavik as well as Stockholm")
    ap.add_argument("--multiples", action="store_true",
                    help="EV/EBIT, EV/Sales, P/E and FCF yield for the selected "
                         "peers, with the as-of date of every input")
    ap.add_argument("--scan-limit", type=int, default=70, metavar="N",
                    help="max instruments to pull reference data for (default 70)")
    ap.add_argument("--max-candidates", type=int, default=14, metavar="N",
                    help="max candidates scored in full detail (default 14)")
    ap.add_argument("--top", type=int, default=8, metavar="N",
                    help="peers listed in the selected set (default 8)")
    ap.add_argument("--min-score", type=float, default=0.50, metavar="X",
                    help="score floor for the selected set (default 0.50)")
    ap.add_argument("--size-band", type=float, default=1.0, metavar="DEX",
                    help="market-cap distance in log10 at which size similarity "
                         "hits zero and the candidate is gated out; 1.0 = 10x "
                         "(default)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    t0 = time.time()
    markets = ["STO"] + (["CPH", "HEL", "ICE"] if args.nordic else [])
    issuers = build_issuers(markets)

    tkey = resolve_target(args.company, issuers)
    if tkey is AMBIGUOUS_TARGET:
        return 1
    if not tkey:
        print("DATA NOT AVAILABLE: no listed Nordic issuer matching %r."
              % args.company)
        print("Try the exact Nasdaq listing name, or --nordic to widen the search.")
        return 1
    target = issuers[tkey]

    # ---- candidate admission -------------------------------------------
    # Priority 0 admits on archetype alone, across sector and segment, because
    # ICB scatters one business model across four sectors. Priorities 1 and 2
    # are the conventional sector/segment neighbourhood.
    tseg = SEGMENT_ORDER.index(target["segment"])
    pool = []
    for key, it in issuers.items():
        if key == tkey:
            continue
        prio = None
        if target["archetype"] and it["archetype"] == target["archetype"]:
            prio = 0
        elif it["sector"] == target["sector"]:
            d = abs(SEGMENT_ORDER.index(it["segment"]) - tseg)
            if d == 0:
                prio = 1
            elif d == 1 and it["segment"] != "FIRST_NORTH":
                prio = 2
        if prio is not None:
            pool.append((prio, it["display"], key))
    pool.sort()
    scanned = pool[:args.scan_limit]
    truncated = len(pool) - len(scanned)

    # ---- reference data (market cap + ICB supersector) -----------------
    want = [tkey] + [k for _, _, k in scanned]
    jobs = [(k, c) for k in want for c in issuers[k]["classes"]]
    results = pmap(lambda j: (j[0], j[1], instrument(j[1]["orderbookId"])), jobs)
    for key, cls, summ in results:
        if not summ:
            continue
        it = issuers[key]
        # Nasdaq reports marketCap in the LISTING's own currency, which is not
        # always the venue's. Each class is converted on its own currency
        # before anything is summed - assuming the venue is what printed
        # Verisure, a EUR line on Stockholm, as "9.4bn SEK".
        ccy = cls.get("currency") or MARKET_CCY.get(it["market"])
        cap = summ.get("market_cap")
        it.setdefault("class_ccys", set())
        if ccy:
            it["class_ccys"].add(ccy)
        it["market_cap"] = (it.get("market_cap") or 0) + (cap or 0)
        in_eur = to_eur(cap, ccy)
        if in_eur is not None:
            it["cap_eur_sum"] = (it.get("cap_eur_sum") or 0) + in_eur
        it["shares"] = (it.get("shares") or 0) + (summ.get("shares") or 0)
        it["icb"] = it.get("icb") or summ.get("icb")
        it["classes_priced"] = it.get("classes_priced", 0) + 1
        # The oldest copy in the set is the honest as-of for the summed cap.
        prev = it.get("reference_as_of")
        if summ.get("_as_of") and (prev is None or summ["_as_of"] < prev):
            it["reference_as_of"] = summ["_as_of"]
        prev_age = it.get("reference_age_hours")
        if summ.get("_age_hours") is not None:
            it["reference_age_hours"] = max(prev_age or 0, summ["_age_hours"])

    # Caps are then put on one EUR scale before any size comparison is made.
    for key in want:
        it = issuers[key]
        ccys = sorted(it.get("class_ccys") or [])
        it["class_currencies"] = ccys
        it["mixed_class_currencies"] = len(ccys) > 1
        it["ccy_ref"] = (ccys[0] if len(ccys) == 1
                         else (it["primary"].get("currency")
                               or MARKET_CCY.get(it["market"])))
        it["cap_eur"] = it.get("cap_eur_sum") or None

    if not target.get("market_cap"):
        print("DATA NOT AVAILABLE: Nasdaq returned no market cap for %s."
              % target["display"])
        return 1
    if not target.get("cap_eur"):
        print("DATA NOT AVAILABLE: no FX rate for %s, so market caps cannot be "
              "put on one scale." % target.get("ccy_ref"))
        return 1

    # ---- size gate ------------------------------------------------------
    survivors, dropped_size, no_cap = [], [], []
    for _, _, key in scanned:
        it = issuers[key]
        cap = it.get("cap_eur")
        if not cap:
            no_cap.append("%s (%s)" % (it["display"],
                                       "no market cap" if not it.get("market_cap")
                                       else "no FX rate for %s" % it["ccy_ref"]))
            continue
        dex = abs(math.log10(cap / target["cap_eur"]))
        it["size_dex"] = dex
        if dex > args.size_band:
            dropped_size.append((it["display"], it.get("market_cap"),
                                 it.get("ccy_ref"), dex,
                                 it["archetype"] == target["archetype"]
                                 and bool(target["archetype"])))
        else:
            survivors.append(key)

    # ---- ESEF filer index (cached per country for a week) ---------------
    # Built BEFORE the survivors are ranked, because whether a candidate has a
    # filing at all is the single most useful thing known about it at this
    # point and it costs nothing here. A candidate with no ESEF filer can be
    # scored on size, co-cyclicality and ICB only - 0.35 of the 0.78 computed
    # weight, which is below the 0.50 evidence floor - so it can NEVER be
    # selected however well it ranks. Letting one take a detail slot ahead of
    # a fully documented name spends the run's budget on a row that is
    # guaranteed to print as "EVIDENCE TOO THIN".
    countries = sorted({MARKET_COUNTRY.get(m) for m in markets} - {None, "IS"})
    indices = {}
    for c in countries:
        idx = esef_index(c)
        if idx:
            indices[c] = idx
    if "IS" in {MARKET_COUNTRY.get(m) for m in markets}:
        pass    # Iceland is not harvested by filings.xbrl.org - noted in output

    esef_hit = {}
    for key in survivors + [tkey]:
        esef_hit[key] = lei_for(issuers[key]["display"], indices)

    # One filer is one candidate. Two listed lines of the same issuer can
    # survive as separate candidates because the symbol root is the only
    # grouping key available: Volati's preference share is "VOLO PREF", a
    # different root from "VOLO", and CLASS_SUFFIX then strips " Pref" from the
    # name so both print as "Volati". Both resolve to the same ESEF filer, and
    # the pair entered the same peer median twice - the second time with the
    # preference line's own market cap as the numerator, which is not the
    # company's. The bigger line wins; the target's own second line is dropped
    # outright.
    tlei = (esef_hit.get(tkey) or {}).get("lei")
    best, duplicates = {}, []
    for key in survivors:
        lei = (esef_hit.get(key) or {}).get("lei")
        if not lei:
            continue
        if tlei and lei == tlei:
            duplicates.append((issuers[key]["display"],
                               "resolves to the target's own ESEF filer"))
            continue
        cur = best.get(lei)
        if cur is None:
            best[lei] = key
        elif (issuers[key].get("cap_eur") or 0) > (issuers[cur].get("cap_eur") or 0):
            duplicates.append((issuers[cur]["display"],
                               "second listed line of the same ESEF filer as %s"
                               % issuers[key]["display"]))
            best[lei] = key
        else:
            duplicates.append((issuers[key]["display"],
                               "second listed line of the same ESEF filer as %s"
                               % issuers[cur]["display"]))
    if duplicates:
        keep = set(best.values())
        survivors = [k for k in survivors
                     if not (esef_hit.get(k) or {}).get("lei") or k in keep]

    # Rank survivors by a cheap prior before spending ESEF and price calls.
    def prior(key):
        it = issuers[key]
        s = 0.0
        if target["archetype"] and it["archetype"] == target["archetype"]:
            s += 2.0
        if it.get("icb") and target.get("icb") and it["icb"] == target["icb"]:
            s += 1.0
        if (esef_hit.get(key) or {}).get("lei"):
            s += 1.5
        s += 1.0 - min(1.0, it.get("size_dex", 1.0) / args.size_band)
        return -s
    survivors.sort(key=prior)
    finalists = survivors[:args.max_candidates]

    # ---- fundamentals ---------------------------------------------------
    def load_fin(key):
        hit = esef_hit.get(key)
        if not hit or not hit.get("lei"):
            return key, hit, None      # hit may carry ambiguous_candidates
        pack = esef_facts(hit["lei"])
        return key, hit, derive(pack)

    for key, hit, fin in pmap(load_fin, [tkey] + finalists):
        issuers[key]["esef"] = hit
        issuers[key]["fin"] = fin

    # ---- prices ---------------------------------------------------------
    today = datetime.date.today()
    frm = (today - datetime.timedelta(days=365 * 3 + 5)).isoformat()
    to = today.isoformat()
    px_jobs = [(k, issuers[k]["primary"]["orderbookId"]) for k in [tkey] + finalists]
    price_map = {}
    for key, s in pmap(lambda j: (j[0], closes(j[1], frm, to)), px_jobs):
        price_map[key] = s
    cocyc, cometa = residual_correlations(price_map, tkey)
    for key, v in cocyc.items():
        if key in issuers:
            issuers[key]["cocyc"] = v

    # ---- score ----------------------------------------------------------
    scored = []
    for key in finalists:
        it = issuers[key]
        res = score_peer(target, it, args.size_band)
        if not res or res["score"] is None:
            continue
        scored.append((res["score"], key, res))
    scored.sort(reverse=True, key=lambda x: (x[0], x[2]["coverage"]))
    # Evidence floor: a name scored on less than half of the COMPUTED weight is
    # a candidate, not a comparable. It stays in the ranked list, marked, but
    # never enters the set whose multiples get medianed.
    thin = [k for _, k, r in scored if r["computed_coverage"] < 0.50]
    selected = [(s, k, r) for s, k, r in scored
                if s >= args.min_score and r["computed_coverage"] >= 0.50][:args.top]

    # ---- confidence -----------------------------------------------------
    reasons = []
    if len(selected) < 4:
        reasons.append("%s cleared the %.2f score floor with enough measured "
                       "evidence - too few for a multiple median to mean anything"
                       % ("no peer" if not selected else
                          "only %d peer%s" % (len(selected),
                                              "" if len(selected) == 1 else "s"),
                          args.min_score))
    caps = [issuers[k].get("cap_eur") for _, k, _ in selected
            if issuers[k].get("cap_eur")]
    if len(caps) >= 2:
        disp = max(caps) / min(caps)
        if disp > 20:
            reasons.append("market caps in the selected set span %.0fx - size "
                           "dispersion this wide breaks comparability" % disp)
    else:
        disp = None
    covs = [r["computed_coverage"] for _, _, r in selected]
    med_cov = statistics.median(covs) if covs else 0.0
    if covs and med_cov < 0.60:
        reasons.append("median computed-dimension coverage is only %.0f%% - most "
                       "of these peers are being ranked on sector, size and price "
                       "co-movement alone" % (100 * med_cov))
    if not target.get("archetype"):
        reasons.append("%s matches no curated business archetype, so candidate "
                       "admission fell back to ICB sector - the one input this "
                       "framework exists to distrust" % target["display"])
    if not target.get("fin"):
        reasons.append("no ESEF fundamentals resolved for the target itself, so "
                       "growth, margin, ROIC and capital intensity were not "
                       "scored at all")
    same_arch = [k for _, k, _ in selected
                 if target.get("archetype")
                 and issuers[k]["archetype"] == target["archetype"]]
    if target.get("archetype") and len(same_arch) < 2:
        reasons.append("%s in the selected set shares %s's business archetype "
                       "(%s) in the markets searched" % (
                           "no listed name" if not same_arch
                           else "only %d listed name" % len(same_arch),
                           target["display"], target["archetype"]))
    low_conf = bool(reasons)

    # ---- fiscal-period alignment ----------------------------------------
    # TWO different problems used to be conflated into one warning that read as
    # a no-op. The month gap was computed INCLUDING the year difference but
    # labelled with month-day only, so a peer a full year behind rendered as
    # "ends 12-31" against a target also ending 12-31 - and a genuinely stale
    # peer (Ferronordic's FY2023 medianed against everyone's FY2024) was never
    # age-checked at all. They are separated here: a different fiscal CALENDAR
    # is a month-of-year question, being a period BEHIND is a date question.
    tfy_end = (target.get("fin") or {}).get("fy_end")
    skew, peers_behind, behind_keys = [], [], set()
    try:
        tdate = datetime.date.fromisoformat(tfy_end) if tfy_end else None
    except ValueError:
        tdate = None
    for _, k, _ in scored:
        fe = (issuers[k].get("fin") or {}).get("fy_end")
        if not fe or not tdate:
            continue
        try:
            d1 = datetime.date.fromisoformat(fe)
        except ValueError:
            continue
        dm = (d1.month - tdate.month) % 12
        if min(dm, 12 - dm) >= 3:
            skew.append((fe[5:], issuers[k]["display"]))
        if (tdate - d1).days > PEER_BEHIND_DAYS:
            peers_behind.append((issuers[k]["display"], fe, (tdate - d1).days))
            behind_keys.add(k)

    elapsed = time.time() - t0

    # ---- multiples ------------------------------------------------------
    mult = {}
    if args.multiples:
        for key in [tkey] + [k for _, k, _ in selected]:
            it = issuers[key]
            # The numerator of every multiple is rebuilt from a live price
            # here. It used to come from the instrument summary, cached for
            # SEVEN DAYS, while a quote was fetched, its price thrown away and
            # only its label printed - and shares_as_of said "retrieved today"
            # regardless. Registered shares genuinely do move slowly, so they
            # keep the long cache and are stamped with its real write time;
            # the price does not, so it is refetched every 15 minutes and the
            # exchange's own trade timestamp is what reaches the table.
            live_cap, quotes, complete = 0.0, [], True
            for c in it["classes"]:
                q = live_quote(c["orderbookId"])
                s = instrument(c["orderbookId"]) or {}
                px, sh = (q or {}).get("last"), s.get("shares")
                if px is None or not sh:
                    complete = False
                else:
                    live_cap += px * sh
                if q:
                    quotes.append(q)
            first = quotes[0] if quotes else {}
            it["ccy"] = (first.get("currency") or it.get("ccy_ref")
                         or MARKET_CCY.get(it["market"]))
            stamps = sorted({str(q.get("as_of")) for q in quotes if q.get("as_of")})
            if complete and live_cap > 0:
                it["cap_live"] = live_cap
                it["cap_basis"] = ("last traded price x registered shares, %d "
                                   "listed class%s"
                                   % (len(it["classes"]),
                                      "" if len(it["classes"]) == 1 else "es"))
                it["price_as_of"] = " / ".join(stamps) if stamps else "n/a"
            else:
                it["cap_live"] = None
                it["cap_basis"] = ("Nasdaq reference marketCap cached %s - NOT "
                                   "a live price"
                                   % (it.get("reference_as_of") or "unknown"))
                it["price_as_of"] = it.get("reference_as_of") or "n/a"
            it["price_retrieved"] = first.get("_retrieved")
            it["shares_as_of"] = ("Nasdaq reference data, cached %s (%s h old)"
                                  % (it.get("reference_as_of") or "unknown",
                                     it.get("reference_age_hours")
                                     if it.get("reference_age_hours") is not None
                                     else "?"))
            mult[key] = multiples(it)

    if args.as_json:
        def pack_issuer(key, res=None):
            it = issuers[key]
            d = {"name": it["display"], "symbol": it["root"],
                 "market": it["market"], "segment": it["segment"],
                 "sector": it["sector"], "icb": it.get("icb"),
                 "archetype": it.get("archetype"),
                 "archetype_curated": True,
                 "market_cap": it.get("market_cap"),
                 "market_cap_currency": it.get("ccy_ref"),
                 "market_cap_currency_source": (
                     "listing currency from Nasdaq reference data"
                     if it.get("class_currencies")
                     else "assumed from the venue - no per-listing currency"),
                 "market_cap_as_of": it.get("reference_as_of"),
                 "market_cap_age_hours": it.get("reference_age_hours"),
                 "market_cap_live": it.get("cap_live"),
                 "market_cap_live_basis": it.get("cap_basis"),
                 "class_currencies": it.get("class_currencies"),
                 "market_cap_eur": it.get("cap_eur"), "shares": it.get("shares"),
                 "esef": it.get("esef"), "fundamentals": it.get("fin"),
                 "cocyclicality": it.get("cocyc")}
            if res:
                d.update({"score": res["score"],
                          "score_on_measured_dimensions_only": res["score_on_known"],
                          "components": res["components"],
                          "evidence": res["evidence"],
                          "weight_measured": res["coverage"],
                          "computed_coverage": res["computed_coverage"],
                          "evidence_too_thin": key in thin})
            if key in mult:
                d["multiples"] = mult[key]
            return d
        print(json.dumps({
            "target": pack_issuer(tkey),
            "method": {"weights": WEIGHTS, "curated_dimensions": sorted(CURATED_DIMS),
                       "not_computed": [{"dimension": d, "why": w}
                                        for d, w in NOT_COMPUTED],
                       "size_band_dex": args.size_band,
                       "cocyclicality": cometa},
            "markets": markets,
            "scanned": len(scanned), "pool": len(pool),
            "excluded_on_size": [{"name": n, "market_cap": c, "currency": cc,
                                  "log10_gap": d, "same_archetype": a}
                                 for n, c, cc, d, a in dropped_size],
            "selected": [pack_issuer(k, r) for _, k, r in selected],
            "ranked_all": [pack_issuer(k, r) for _, k, r in scored],
            "collapsed_duplicate_listings": [{"name": n, "why": w}
                                             for n, w in duplicates],
            "fiscal_calendar_misaligned": [{"ends": fe, "name": nm}
                                           for fe, nm in skew],
            "peers_a_period_behind": [{"name": nm, "fy_end": fe,
                                       "days_behind_target": g}
                                      for nm, fe, g in peers_behind],
            "low_confidence": low_conf, "low_confidence_reasons": reasons,
            "calls": dict(CALLS), "elapsed_seconds": round(elapsed, 1),
            "generated_utc": datetime.datetime.now(
                datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        }, indent=2, ensure_ascii=False, default=str))
        return 0

    # ---- text report ----------------------------------------------------
    tf = target.get("fin") or {}
    print("=" * 100)
    print("PEER SET  —  %s (%s, %s %s)"
          % (target["display"], target["root"],
             MARKET_NAME.get(target["market"], target["market"]),
             target["segment"]))
    print("=" * 100)
    print("  market cap        %s   (%d listed class%s summed, Nasdaq reference "
          "data)" % (money(target.get("market_cap"), target.get("ccy_ref")),
                     target.get("classes_priced", 0),
                     "" if target.get("classes_priced") == 1 else "es"))
    print("  ICB               %s   |  Nasdaq sector: %s"
          % (icb_label(target.get("icb")), target["sector"]))
    arch = _ARCH_BY_KEY.get(target.get("archetype"))
    if arch:
        print("  archetype         %s   [CURATED]" % arch["key"])
        print("                    %s" % arch["label"])
        print("                    model: %s" % arch["model"])
        print("                    end markets (EVIDENCE ONLY, not scored):")
        print("                      %s" % arch["end_markets"])
    else:
        print("  archetype         DATA NOT AVAILABLE — no curated archetype "
              "matches this name")
    if tf:
        print("  fundamentals      FY %s, %s, ESEF LEI %s"
              % (tf.get("fy_end"),
                 tf.get("currency")
                 or ("AMBIGUOUS (%s)" % ", ".join(tf.get("currencies_latest_fy") or [])
                     or "?"),
                 tf.get("lei")))
        print("                    revenue %s | EBIT margin %s | %s CAGR %s | "
              "ROIC %s%s | capex/sales %s"
              % (money(tf.get("revenue"), tf.get("currency") or ""),
                 pct(tf.get("ebit_margin"), 1),
                 ("%.1fy" % tf["cagr_years"]) if tf.get("cagr_years") else "n/a",
                 pct(tf.get("cagr"), 1), pct(tf.get("roic"), 1),
                 "†" if tf.get("tax_rate_imputed") and tf.get("roic") is not None else "",
                 pct(tf.get("capex_sales"), 1)))
        if tf.get("cagr_from"):
            print("                    CAGR measured %s → %s over %.1f elapsed "
                  "years (%d annual observations)"
                  % (tf["cagr_from"], tf["cagr_to"], tf["cagr_years"],
                     tf.get("cagr_observations") or 0))
        if tf.get("cagr_periods_dropped"):
            print("                    !! %d earlier year(s) dropped from the "
                  "CAGR base — reported in a different unit (%s) from the "
                  "latest year"
                  % (len(tf["cagr_periods_dropped"]),
                     ", ".join("%s %s" % (d["period"], d["unit"])
                               for d in tf["cagr_periods_dropped"])))
        if tf.get("currency_note"):
            print("                    !! REPORTING CURRENCY: %s"
                  % tf["currency_note"])
    else:
        print("  fundamentals      DATA NOT AVAILABLE — no ESEF filing resolved")
    print()
    print("  pool %d candidates admitted → %d scanned for reference data → "
          "%d survived the %.0fx size gate → %d scored in detail"
          % (len(pool), len(scanned), len(survivors),
             10 ** args.size_band, len(finalists)))
    if truncated:
        print("  %d further candidates were not scanned (--scan-limit %d). Raise "
              "it if you want a wider sweep." % (truncated, args.scan_limit))
    print("  %d Nasdaq calls, %d filings.xbrl.org calls, %.0fs elapsed "
          "(cache: %s)" % (CALLS["nasdaq"], CALLS["esef"], elapsed, CACHE))
    ccys = sorted({issuers[k].get("ccy_ref") for k in [tkey] + finalists
                   if issuers[k].get("ccy_ref")})
    if len(ccys) > 1:
        tbl = _FX["table"] or {}
        if tbl:
            print("  FX for the size comparison: %s, fixing %s. Caps are shown in "
                  "their own currency" % (tbl.get("source"), tbl.get("date")))
            print("  and compared in EUR: %s"
                  % ", ".join("1 EUR = %.4g %s" % (tbl["rates"][c], c)
                              for c in ccys
                              if c != "EUR" and c in tbl.get("rates", {})))
        else:
            print("  !! DATA NOT AVAILABLE: no FX rates, so cross-currency size "
                  "comparison was skipped.")
    print()

    if not scored:
        print("PEER SET LOW CONFIDENCE")
        print("  Nothing scoreable survived. No comparable listed name was found "
              "for %s in %s." % (target["display"], "/".join(markets)))
        return 0

    sel_keys = {k for _, k, _ in selected}
    hdr = ("RANK  PEER                          MKT SEG    SCORE  arch  size  "
           "cocy  marg  roic  grow  capi  icb   COV")
    print("RANKED CANDIDATES   (COV = share of total weight actually measured; "
           "unmeasured dimensions")
    print("are imputed at 0.5 = no information, so missing data can neither "
          "help nor hurt a candidate)")
    print(hdr)
    print("-" * len(hdr))
    for i, (sc, key, res) in enumerate(scored, 1):
        it = issuers[key]
        c = res["components"]
        mark = "*" if key in sel_keys else ("~" if key in thin else " ")

        def cell(k):
            return ("%.2f" % c[k]) if k in c else "  — "
        print("%s%3d  %-29.29s %-3s %-6.6s %.2f  %s  %s  %s  %s  %s  %s  %s  %s  "
              "%3.0f%%"
              % (mark, i, it["display"], it["market"],
                 it["segment"].replace("_CAP", "").replace("FIRST_NORTH", "FN"),
                 sc, cell("archetype"), cell("size"), cell("cocyclicality"),
                 cell("margin"), cell("roic"), cell("growth"),
                 cell("capital_intensity"), cell("icb"),
                 100 * res["coverage"]))
    print("-" * len(hdr))
    print("  * = selected  (score >= %.2f AND at least half the computed weight "
          "measured, top %d)" % (args.min_score, args.top))
    if thin:
        print("  ~ = EVIDENCE TOO THIN to be a comparable — no ESEF filing "
              "resolved, so growth, margin,")
        print("      ROIC and capital intensity were never tested: %s"
              % ", ".join(issuers[k]["display"] for k in thin))
    print()

    print("EVIDENCE BEHIND THE SCORES")
    ehdr = ("  PEER                          MKT CAP                   EBIT%   "
            "ROIC    CAGR   CAPEX%  RESID-CORR  FY")
    print(ehdr)
    print("  " + "-" * (len(ehdr) - 2))
    rows = [(target["display"], target, None)] + \
           [(issuers[k]["display"], issuers[k], r) for _, k, r in scored]
    partial, imputed_tax, ccy_amb = [], [], []
    for name, it, _ in rows:
        f = it.get("fin") or {}
        cc = it.get("cocyc") or {}
        rc = cc.get("resid_corr")
        cell_rc = "n/a"
        if rc is not None:
            cell_rc = "%+.2f" % rc
            if cc.get("partial_history"):
                cell_rc += " (part)"
                partial.append("%s %d obs" % (name, cc.get("observations", 0)))
        tag = "  <== TARGET" if it is target else ""
        capcell = money(it.get("market_cap"), it.get("ccy_ref") or "")
        if (it.get("ccy_ref") not in (target.get("ccy_ref"), "EUR")
                and it.get("cap_eur")):
            capcell += " (=%s)" % money(it["cap_eur"], "EUR")
        cell_roic = pct(f.get("roic"), 0)
        if f.get("roic") is not None and f.get("tax_rate_imputed"):
            cell_roic += "†"
            imputed_tax.append("%s (%s)" % (name, f.get("tax_rate_note")))
        if f and not f.get("currency"):
            ccy_amb.append(name)
        print("  %-29.29s %-24s  %-6s  %-6s  %-5s  %-6s  %-10s  %-10s%s"
              % (name, capcell,
                 pct(f.get("ebit_margin"), 0), cell_roic,
                 pct(f.get("cagr"), 0), pct(f.get("capex_sales"), 1),
                 cell_rc, f.get("fy_end") or "no ESEF", tag))
    print()
    if imputed_tax:
        # ROIC carries 0.12 of the score. A ROIC struck on an assumed tax rate
        # is not the same evidence as one struck on the filed rate, and used to
        # reach --json only.
        print("  † = ROIC uses an ASSUMED %.0f%% tax rate, not the filed "
              "effective rate:" % (100 * DEFAULT_TAX_RATE))
        for line in imputed_tax:
            print("      %s" % line)
    if ccy_amb:
        print("  !! No single reporting currency for the latest fiscal year of: "
              "%s." % ", ".join(ccy_amb))
        print("     Their multiples are suppressed rather than divided across "
              "currencies.")
    if partial:
        print("  (part) = short listing history, correlated over fewer weeks "
              "than the rest: %s" % "; ".join(partial))
    if cometa:
        print("  RESID-CORR is the correlation of weekly return residuals after "
              "removing one equal-weighted")
        print("  factor built from this pool of %d names (%d weekly observations, "
              "%s to %s)."
              % (cometa["pool"], cometa["observations"], cometa["from"],
                 cometa["to"]))
        print("  Residuals sum to roughly zero across the pool, so the LEVEL is "
              "set-relative — compare the")
        print("  ranking within this run, never the number against another run.")
    else:
        print("  RESID-CORR: DATA NOT AVAILABLE — too few overlapping price "
              "histories in this pool.")
    # TWO different problems used to be conflated into one warning that read as
    # a no-op. The month gap was computed INCLUDING the year difference but
    # labelled with month-day only, so a peer a full year behind rendered as
    # "ends 12-31" against a target also ending 12-31 - and a genuinely stale
    # peer (Ferronordic's FY2023 medianed against everyone's FY2024) was never
    # age-checked at all. They are now separate: a different fiscal CALENDAR is
    # a month-of-year question, and being a period BEHIND is a date question.
    if tf.get("fy_end"):
        if skew:
            groups = {}
            for fe, nm in skew:
                groups.setdefault(fe, []).append(nm)
            print()
            print("  !! FISCAL-YEAR MISALIGNMENT — the target's year ends %s"
                  % tf["fy_end"])
            for fe, nms in sorted(groups.items()):
                print("     ends %s: %s" % (fe, ", ".join(sorted(nms))))
            print("     Those are different economic periods. Do not median "
                  "their margins against the target's.")
        if peers_behind:
            print()
            print("  !! PEER FUNDAMENTALS A REPORTING PERIOD BEHIND — the "
                  "target's latest annual report ends %s" % tf["fy_end"])
            for nm, fe, gap in sorted(peers_behind, key=lambda x: -x[2]):
                print("     %-29.29s latest ESEF annual report ends %s, %d days "
                      "earlier than the target's" % (nm, fe, gap))
            print("     These are a different year, not a different quarter. "
                  "They are excluded from the")
            print("     multiple medians below and their margins should not be "
                  "averaged with the rest either.")
    print()

    arch_dropped = [d for d in dropped_size if d[4]]
    if arch_dropped:
        # The most important thing this script can tell you: the companies that
        # really do the same thing exist, and they are the wrong size to price
        # against. Never bury that in the tail of a long exclusion list.
        print("!! SAME BUSINESS MODEL BUT EXCLUDED ON SIZE")
        for n, c, cc, d, _ in sorted(arch_dropped, key=lambda x: -x[3]):
            print("   %-29.29s %-13s  %5.0fx away from the target"
                  % (n, money(c, cc), 10 ** d))
        print("   These share %s's archetype and nothing else in the ranked list "
              "does as convincingly." % target["display"])
        print("   Their multiples carry a size and liquidity discount that is not "
              "a business-quality signal.")
        print("   Look at them, adjust for size explicitly, or raise --size-band "
              "and accept the distortion.")
        print()
    if dropped_size:
        print("EXCLUDED ON SIZE  (outside the %.0fx market-cap band)"
              % (10 ** args.size_band))
        for n, c, cc, d, a in sorted(dropped_size, key=lambda x: x[3])[:12]:
            print("  %-29.29s %-13s  %4.0fx away%s"
                  % (n, money(c, cc), 10 ** d,
                     "   [same archetype]" if a else ""))
        if len(dropped_size) > 12:
            print("  ... and %d more" % (len(dropped_size) - 12))
        print("  Raise --size-band if you want them back; a 2bn and a 700bn "
              "company are not peers for a multiple.")
        print()
    if no_cap:
        print("  No market cap returned by Nasdaq for: %s"
              % ", ".join(sorted(no_cap)[:8]))
        print()
    if duplicates:
        print("  Collapsed to one candidate per ESEF filer: %s"
              % "; ".join("%s (%s)" % (n, w) for n, w in duplicates))
        print()

    print("SCORING METHOD")
    print("  dimension           weight  source")
    order = sorted(WEIGHTS, key=lambda k: -WEIGHTS[k])
    labels = {
        "archetype": ("CURATED  editorial table in this file — NOT computed"),
        "size": "COMPUTED  log10 market-cap distance, Nasdaq reference data",
        "cocyclicality": "COMPUTED  residual weekly-return correlation, Nasdaq closes",
        "margin": "COMPUTED  EBIT margin, ESEF latest FY",
        "roic": "COMPUTED  EBIT x (1-tax) / avg (assets - current liabilities), ESEF",
        "growth": "COMPUTED  revenue CAGR over the ESEF years available",
        "capital_intensity": "COMPUTED  capex / revenue, ESEF latest FY",
        "icb": "COMPUTED  ICB supersector match, Nasdaq reference data",
    }
    for k in order:
        print("  %-19s %5.2f   %s" % (k, WEIGHTS[k], labels[k]))
    print("  Weights are renormalised per peer over the dimensions actually "
          "available, so COV matters.")
    print("  ROIC here is on TOTAL capital INCLUDING acquisition goodwill. A "
          "serial acquirer will look")
    print("  far worse than the 'ROCE excluding goodwill' it reports itself — "
          "that is deliberate.")
    print()

    print("DIMENSIONS NOT COMPUTED — CLOSE THESE BY HAND BEFORE USING THE SET")
    for dim, why in NOT_COMPUTED:
        print("  * %-26s %s" % (dim, why))
    print("  No free structured source carries them. This script will not "
          "manufacture a score for them.")
    print()

    if args.multiples:
        print("TRAILING MULTIPLES  (each leg carries its own as-of date — they do "
              "NOT line up)")
        mhdr = ("  NAME                          EV/EBIT  EV/SALES   P/E    FCF "
                "YLD  NET DEBT       FY        PRICE AS OF")
        print(mhdr)
        print("  " + "-" * (len(mhdr) - 2))
        pack = [(target["display"], tkey)] + [(issuers[k]["display"], k)
                                              for _, k, _ in selected]
        fields = ("ev_ebit", "ev_sales", "pe", "fcf_yield")
        vals = {f: [] for f in fields}
        excl = {f: [] for f in fields}
        fx_notes, nd_notes, nd_caveats, ni_notes = [], [], [], []
        for name, key in pack:
            m = mult.get(key) or {}
            if m.get("error"):
                print("  %-29.29s %s" % (name, m["error"]))
                continue
            if m.get("fx"):
                fx_notes.append("%s: market cap converted %s" % (name, m["fx"]))
            nd_note = m.get("net_debt_note")
            if m.get("net_debt") is None and nd_note:
                nd_notes.append("%s: %s" % (name, nd_note))
            elif nd_note and not nd_note.startswith(("leases included",
                                                     "no separately tagged")):
                nd_caveats.append("%s: %s" % (name, nd_note))
            if m.get("net_income_owners") is None and m.get("net_income") is not None:
                ni_notes.append("%s: P/E is on %s" % (name, m.get("net_income_basis")))
            elif (m.get("net_income_group") is not None
                  and m.get("net_income_owners") is not None
                  and m["net_income_group"] * m["net_income_owners"] <= 0):
                ni_notes.append(
                    "%s: group profit %s but %s attributable to owners — the "
                    "P/E above is on the attributable line"
                    % (name, money(m["net_income_group"], m.get("report_ccy") or ""),
                       money(m["net_income_owners"], m.get("report_ccy") or "")))
            if key != tkey:
                if key in behind_keys:
                    for f in fields:
                        if m.get(f) is not None:
                            excl[f].append((name, "FY %s is a reporting period "
                                                  "behind the target"
                                            % m.get("fundamentals_as_of")))
                else:
                    for f in fields:
                        ok, why = median_eligible(f, m)
                        if ok:
                            vals[f].append((name, m[f]))
                        elif why:
                            excl[f].append((name, why))
            mark = "!" if key in behind_keys else " "
            print(" %s%-29.29s %-8s %-9s %-6s %-8s %-14s %-9s %s"
                  % (mark, name, ratio(m.get("ev_ebit")), ratio(m.get("ev_sales")),
                     ratio(m.get("pe")), pct(m.get("fcf_yield"), 1),
                     money(m.get("net_debt"), m.get("report_ccy") or ""),
                     m.get("fundamentals_as_of") or "n/a",
                     m.get("price_as_of") or "n/a"))
        print("  " + "-" * (len(mhdr) - 2))
        med = {}
        for f in fields:
            xs = sorted(v for _, v in vals[f])
            med[f] = statistics.median(xs) if xs else None
        print("  %-29.29s %-8s %-9s %-6s %-8s  median of the ELIGIBLE peers only"
              % ("PEER MEDIAN", ratio(med["ev_ebit"]), ratio(med["ev_sales"]),
                 ratio(med["pe"]), pct(med["fcf_yield"], 1)))
        # The old line printed len(vals["ev_ebit"]) as the n for EVERY column,
        # so the label read n=3 while the P/E median was struck over six values.
        # Each column now carries its own count.
        print("  %-29.29s %-8s %-9s %-6s %-8s  names behind each median"
              % ("n =", "n=%d" % len(vals["ev_ebit"]),
                 "n=%d" % len(vals["ev_sales"]), "n=%d" % len(vals["pe"]),
                 "n=%d" % len(vals["fcf_yield"])))
        print()
        # Negative and near-zero denominators used to enter the median
        # unfiltered. A housing set whose profitable members traded at
        # 10.7-23.8x produced a peer median EV/EBIT of -8.5x, and 193.8x,
        # 151.2x and 250.4x were admitted with no upper bound at all.
        any_excl = any(excl[f] for f in fields)
        if any_excl:
            print("  EXCLUDED FROM THE MEDIANS  (kept in the table above — a "
                  "loss is information, it is just")
            print("  not a multiple, and a median that mixes signs is neither)")
            for f in fields:
                if not excl[f]:
                    continue
                label = {"ev_ebit": "EV/EBIT", "ev_sales": "EV/SALES",
                         "pe": "P/E", "fcf_yield": "FCF YIELD"}[f]
                print("    %-9s %d name%s: %s"
                      % (label, len(excl[f]), "" if len(excl[f]) == 1 else "s",
                         "; ".join("%s (%s)" % (n, w) for n, w in excl[f])))
            print()
        if any(k in behind_keys for _, k, _ in selected):
            print("  ! = fundamentals a reporting period behind the target; "
                  "excluded from every median above.")
            print()
        for note in fx_notes:
            print("  %s" % note)
        if fx_notes:
            print()
        if ni_notes:
            print("  EARNINGS ATTRIBUTION")
            for note in ni_notes:
                print("    %s" % note)
            print()
        if nd_notes:
            print("  NET DEBT NOT AVAILABLE, SO EV MULTIPLES ARE SUPPRESSED FOR")
            for note in nd_notes:
                print("    %s" % note)
            print()
        if nd_caveats:
            print("  NET DEBT COMPUTED BUT WITH A CAVEAT")
            for note in nd_caveats:
                print("    %s" % note)
            print()
        tfy = (target.get("fin") or {}).get("fy_end")
        if tfy:
            try:
                age = (today - datetime.date.fromisoformat(tfy)).days
            except ValueError:
                age = None
            limit = ff.FRESHNESS_DAYS.get("annual_financials", 460)
            if age and age > limit:
                print("  !! STALE FUNDAMENTALS: the latest ESEF annual report for "
                      "the target ends %s," % tfy)
                print("     %d days ago, past the %d-day life of an annual figure "
                      "(finfact.FRESHNESS_DAYS)." % (age, limit))
                print("     filings.xbrl.org harvests with a lag and these are "
                      "ANNUAL reports only — no interims.")
                print("     Every multiple above is today's price over stale "
                      "earnings. Roll forward with the")
                print("     latest interim report before using them.")
                print()
        tm = mult.get(tkey) or {}
        print("  Market cap: %s." % (tm.get("market_cap_basis") or "n/a"))
        print("  Price as of is the exchange's own last-trade timestamp for that "
              "listing, not the time of")
        print("  this run. Share count: registered shares including treasury, "
              "all listed classes summed —")
        print("  %s." % (tm.get("shares_as_of") or "n/a"))
        print("  Unlisted classes are invisible to Nasdaq reference data (NIBE "
              "and Fenix Outdoor are known")
        print("  cases) and would understate market cap.")
        print("  Net debt: ESEF-tagged borrowings (BOTH the current and the "
              "non-current leg) + separately")
        print("  tagged lease liabilities - cash. If either borrowings leg or "
              "cash is untagged, net debt")
        print("  and every EV multiple are suppressed rather than approximated. "
              "Enterprise value adds")
        print("  non-controlling interests where they are tagged, because EBIT "
              "and revenue below are")
        print("  consolidated in full.")
        print("  FX: where the listing is quoted in a different currency from "
              "the one the filer reports in,")
        print("  the market cap is converted at the ECB daily fixing BEFORE it "
              "is divided by anything, and")
        print("  the conversion is listed above. Where the latest fiscal year's "
              "reporting currency is not")
        print("  unambiguous the whole row is suppressed as DATA NOT AVAILABLE — "
              "it is never divided")
        print("  across two currencies.")
        print()

    print("=" * 100)
    if low_conf:
        print("PEER SET LOW CONFIDENCE")
        for r in reasons:
            print("  - %s" % r)
        print()
        print("  Treat the names above as a shortlist for judgement, not as a "
              "comparable set. A three-name")
        print("  honest peer group beats an eight-name invented one; consider "
              "widening with --nordic, or")
        print("  reaching outside the Nordics entirely, which this script cannot do.")
    else:
        print("PEER SET USABLE AS A STARTING POINT")
        # `disp or 1` printed a fabricated "1x cap dispersion" - a claim of a
        # perfectly matched set - whenever fewer than two peers had a cap.
        print("  %d peers cleared the score floor with median computed coverage "
              "%.0f%% and %s."
              % (len(selected), 100 * med_cov,
                 ("%.0fx cap dispersion" % disp) if disp
                 else "cap dispersion DATA NOT AVAILABLE (fewer than two "
                      "selected peers have a market cap)"))
        print("  Still close the six uncomputed dimensions above before you "
              "trust the multiple median.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
