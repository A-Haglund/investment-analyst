#!/usr/bin/env python3
"""The trust boundary: every number that reaches a valuation carries its origin.

v2.0 enforced its research discipline in prose that the language model was asked
to follow. This module moves the load-bearing parts into code, because a rule a
model must remember is a rule that will eventually be forgotten - and the
failures that follow look exactly like correct answers.

Three things are enforced here rather than requested:

  1. PROVENANCE. A FinancialFact cannot exist without a source, a period and a
     retrieval date. A bare float never enters a valuation.

  2. TEMPORAL VALIDITY. is_available_as_of() rejects any fact published after
     the analysis date. Historical analysis that quietly uses tomorrow's filing
     is not analysis; it is a demonstration of hindsight.

  3. INDEPENDENCE. A company's IR page, its MFN release and its Cision release
     are one disclosure carried by three channels. Counting them as three
     confirmations is the most flattering mistake this system could make, so
     corroboration is counted by origin group, not by URL.

Import it, do not run it - though `python finfact.py --selftest` runs the
assertions this module's guarantees rest on.
"""
import datetime
import enum
import json


# --------------------------------------------------------------------------
# Error states (spec §32). These propagate into the final analysis rather than
# being swallowed. A caller that cannot produce a number must say which of
# these applies.
# --------------------------------------------------------------------------

class State(str, enum.Enum):
    OK = "OK"
    DATA_NOT_AVAILABLE = "DATA_NOT_AVAILABLE"
    DATA_STALE = "DATA_STALE"
    DATA_CONFLICT = "DATA_CONFLICT"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    POINT_IN_TIME_UNVERIFIED = "POINT_IN_TIME_UNVERIFIED"
    VALUATION_INTEGRITY_FAILED = "VALUATION_INTEGRITY_FAILED"
    COMPANY_IDENTITY_AMBIGUOUS = "COMPANY_IDENTITY_AMBIGUOUS"
    SHARE_COUNT_UNCERTAIN = "SHARE_COUNT_UNCERTAIN"
    TTM_INCOMPLETE = "TTM_INCOMPLETE"
    UNVERIFIED_DATA = "UNVERIFIED_DATA"


class Verification(str, enum.Enum):
    VERIFIED = "VERIFIED"              # two INDEPENDENT origins agree
    CROSS_CHECKED = "CROSS-CHECKED"    # a second source agrees but shares an origin
    SINGLE_SOURCE = "SINGLE SOURCE"
    CONFLICT = "CONFLICT"
    STALE = "STALE"
    INCOMPLETE = "INCOMPLETE"
    UNVERIFIED = "UNVERIFIED"


class Mode(str, enum.Enum):
    """Spec §34. Never mixed, and the mode travels with the output."""
    CURRENT = "CURRENT"        # latest available information
    HISTORICAL = "HISTORICAL"  # only what was published by as_of
    HINDSIGHT = "HINDSIGHT"    # later information allowed, and labelled as such


# --------------------------------------------------------------------------
# Source authority and independence (spec §19)
#
# AUTHORITY answers "how close is this to the issuer's audited record".
# INDEPENDENCE answers "if this and that agree, is that two facts or one fact
# twice". They are different questions and conflating them manufactures
# confidence out of nothing.
# --------------------------------------------------------------------------

TIER = {
    "annual_report": 1, "esef": 1, "interim_report": 1,
    "fi_register": 1, "nasdaq_reference": 1, "riksbank": 1, "scb": 1,
    "esma_firds": 1, "gleif": 1, "vies": 1, "ecb": 1, "eurostat": 1,
    "company_ir": 1,
    "mfn": 2, "cision": 2, "nasdaq_cns": 2,
    "press": 3,
    "aggregator": 4, "avanza": 4, "yahoo": 4,
}

# Sources sharing an origin group corroborate nothing. The issuer's own
# disclosure is one act of reporting however many channels carry it.
ORIGIN = {
    "annual_report": "issuer_filing",
    "interim_report": "issuer_filing",
    "esef": "issuer_filing",        # the tagged rendering of the same document
    "company_ir": "issuer_disclosure",
    "mfn": "issuer_disclosure",
    "cision": "issuer_disclosure",
    "nasdaq_cns": "issuer_disclosure",
    "fi_register": "regulator",
    "esma_firds": "regulator",
    "vies": "regulator",
    "nasdaq_reference": "exchange",
    "riksbank": "central_bank",
    "ecb": "central_bank",
    "scb": "statistics_office",
    "eurostat": "statistics_office",
    "gleif": "identifier_registry",
    "press": "media",
    "aggregator": "vendor", "avanza": "vendor", "yahoo": "vendor",
}

# How long a figure stays current before it must be re-fetched or marked stale.
FRESHNESS_DAYS = {
    "price": 1,
    "shares_outstanding": 120,
    "interim_financials": 200,   # a quarter plus reporting lag plus slack
    "annual_financials": 460,    # a year plus reporting lag
    "insider": 7,
    "short_interest": 7,
    "ownership": 200,            # quarterly with a long publication lag
    "macro_rate": 7,
    "industry_benchmark": 500,
}


def _as_date(value):
    if value is None:
        return None
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


class FinancialFact(object):
    """One number, with everything needed to defend or reject it.

    Deliberately not a plain dict: constructing one without a source or a
    period raises, which is the whole point of a trust boundary.
    """

    __slots__ = ("metric", "value", "unit", "currency", "period_start",
                 "period_end", "publication_date", "effective_date",
                 "retrieval_date", "source", "source_detail", "source_tier",
                 "origin_group", "verification", "confidence", "note",
                 "freshness_key", "publication_is_upper_bound")

    def __init__(self, metric, value, source, period_end,
                 unit="currency", currency=None, period_start=None,
                 publication_date=None, effective_date=None,
                 retrieval_date=None, source_detail=None,
                 verification=Verification.SINGLE_SOURCE, note=None,
                 freshness_key=None, publication_is_upper_bound=False):
        if not metric:
            raise ValueError("FinancialFact requires a metric name")
        if source not in TIER:
            raise ValueError(
                "unknown source %r. Add it to TIER and ORIGIN so its authority "
                "and its independence group are both explicit." % (source,))
        if period_end is None:
            raise ValueError("FinancialFact requires period_end: a number "
                             "without a period cannot be compared to anything")

        self.metric = metric
        self.value = value
        self.unit = unit
        self.currency = currency
        self.period_start = _as_date(period_start)
        self.period_end = _as_date(period_end)
        # publication_date is when the world could first know this. Absent it,
        # no honest historical claim can be made about the fact.
        self.publication_date = _as_date(publication_date)
        self.effective_date = _as_date(effective_date) or self.period_end
        self.retrieval_date = _as_date(retrieval_date) or datetime.date.today()
        self.source = source
        self.source_detail = source_detail
        self.source_tier = TIER[source]
        self.origin_group = ORIGIN[source]
        self.verification = verification
        self.note = note
        self.freshness_key = freshness_key
        # Some sources give only a bound. filings.xbrl.org reports when it
        # HARVESTED a filing, months after the issuer published it. Used as a
        # cutoff that errs safely - it can exclude a filing you might have had,
        # never admit one you could not - but it is not the publication date
        # and must not earn the confidence of one.
        self.publication_is_upper_bound = bool(publication_is_upper_bound)
        self.confidence = self._confidence()

    # -- temporal ---------------------------------------------------------

    def is_available_as_of(self, as_of):
        """Spec §3. The hard gate. Unknown publication date is not a pass.

        Returns (bool, State). A fact with no publication date can never be
        asserted to have been available historically, because nothing in the
        record says when it became knowable.
        """
        as_of = _as_date(as_of)
        if self.publication_date is None:
            return False, State.POINT_IN_TIME_UNVERIFIED
        if self.publication_date > as_of:
            return False, State.OK          # simply not yet published
        return True, State.OK

    def age_days(self, reference=None):
        ref = _as_date(reference) or datetime.date.today()
        basis = self.publication_date or self.period_end
        return (ref - basis).days

    def staleness(self, reference=None):
        """(is_stale, days_old, limit). Unknown freshness class never claims fresh."""
        limit = FRESHNESS_DAYS.get(self.freshness_key)
        days = self.age_days(reference)
        if limit is None:
            return None, days, None
        return days > limit, days, limit

    # -- confidence -------------------------------------------------------

    def _confidence(self):
        """Spec §31. Derived from evidence, never asserted by tone.

        Starts from the source tier and is penalised for every specific way the
        record is weaker than it looks. Deliberately harsh: a figure with no
        publication date is not a confident figure, however authoritative the
        source.
        """
        base = {1: 0.90, 2: 0.75, 3: 0.45, 4: 0.25}[self.source_tier]
        penalty = 0.0
        if self.publication_date is None:
            penalty += 0.15                      # cannot be placed in time
        elif self.publication_is_upper_bound:
            penalty += 0.05                      # placed in time only loosely
        if self.verification == Verification.CONFLICT:
            penalty += 0.45
        elif self.verification == Verification.SINGLE_SOURCE:
            penalty += 0.08
        elif self.verification == Verification.INCOMPLETE:
            penalty += 0.20
        elif self.verification == Verification.UNVERIFIED:
            penalty += 0.30
        elif self.verification == Verification.VERIFIED:
            penalty -= 0.05
        stale, _days, _limit = self.staleness()
        if stale:
            penalty += 0.20
        if self.currency is None and self.unit == "currency":
            penalty += 0.10                      # an unlabelled currency is a trap
        return max(0.0, min(1.0, base - penalty))

    # -- output -----------------------------------------------------------

    def to_dict(self):
        d = {}
        for k in self.__slots__:
            v = getattr(self, k)
            d[k] = v.isoformat() if isinstance(v, datetime.date) else (
                v.value if isinstance(v, enum.Enum) else v)
        return d

    def __repr__(self):
        return "<%s %s %s %s @%s %s conf=%.2f>" % (
            self.metric, self.value, self.currency or self.unit,
            self.period_end, self.source, self.verification.value,
            self.confidence)


# --------------------------------------------------------------------------
# Corroboration by origin, not by count (spec §19)
# --------------------------------------------------------------------------

def corroborate(facts, tolerance=0.01):
    """Grade agreement among facts for the same metric and period.

    Returns (Verification, detail). Two facts sharing an origin_group are one
    piece of evidence: an ESEF filing and the annual report it was tagged from
    do not independently confirm each other.
    """
    usable = [f for f in facts if f.value is not None]
    if not usable:
        return Verification.UNVERIFIED, {"reason": "no values"}

    groups = {}
    for f in usable:
        groups.setdefault(f.origin_group, []).append(f)

    values = [f.value for f in usable]
    lo, hi = min(values), max(values)
    denom = max(abs(v) for v in values)
    spread = abs(hi - lo) / denom if denom else 0.0

    detail = {"origin_groups": sorted(groups), "independent_origins": len(groups),
              "n_facts": len(usable), "spread": spread,
              "values": {f.source: f.value for f in usable}}

    if spread > tolerance:
        return Verification.CONFLICT, detail
    if len(groups) >= 2:
        return Verification.VERIFIED, detail
    # Agreement within one origin is reassurance about transcription, not about
    # the underlying number.
    if len(usable) >= 2:
        return Verification.CROSS_CHECKED, detail
    return Verification.SINGLE_SOURCE, detail


def filter_as_of(facts, as_of, mode=Mode.HISTORICAL):
    """Spec §3 and §34. Returns (kept, rejected_with_reason)."""
    if mode == Mode.CURRENT:
        return list(facts), []
    if mode == Mode.HINDSIGHT:
        return list(facts), []
    kept, rejected = [], []
    for f in facts:
        ok, state = f.is_available_as_of(as_of)
        if ok:
            kept.append(f)
        else:
            reason = ("published %s, after as-of %s" % (f.publication_date, as_of)
                      if f.publication_date else "no publication date on record")
            rejected.append((f, state, reason))
    return kept, rejected


def confidence_score(facts, required_metrics=()):
    """Spec §18/§31. A 0-100 data-confidence figure derived from the record."""
    if not facts:
        return 0, {"reason": "no facts"}
    by_metric = {}
    for f in facts:
        by_metric.setdefault(f.metric, []).append(f)

    tier1 = sum(1 for f in facts if f.source_tier == 1) / len(facts)
    verified = sum(1 for f in facts if f.verification == Verification.VERIFIED)
    conflicts = sum(1 for f in facts if f.verification == Verification.CONFLICT)
    undated = sum(1 for f in facts if f.publication_date is None)
    stale = sum(1 for f in facts if f.staleness()[0])
    missing = [m for m in required_metrics if m not in by_metric]

    score = 100.0
    score -= (1 - tier1) * 30
    score -= (1 - (verified / max(len(by_metric), 1))) * 25
    score -= min(conflicts, 3) * 12
    score -= (undated / len(facts)) * 12
    score -= (stale / len(facts)) * 12
    score -= min(len(missing), 5) * 4
    score = int(max(0, min(100, round(score))))

    return score, {"tier1_share": round(tier1, 2), "verified_metrics": verified,
                   "conflicts": conflicts, "undated": undated, "stale": stale,
                   "missing_required": missing, "metrics": len(by_metric)}


# --------------------------------------------------------------------------

def _selftest():
    today = datetime.date(2026, 8, 31)
    ok = 0

    # provenance is mandatory
    for bad in (lambda: FinancialFact("revenue", 1, "esef", None),
                lambda: FinancialFact("revenue", 1, "not_a_source", "2024-12-31"),
                lambda: FinancialFact("", 1, "esef", "2024-12-31")):
        try:
            bad(); raise AssertionError("should have raised")
        except ValueError:
            ok += 1

    # temporal gate
    late = FinancialFact("revenue", 100, "esef", "2024-12-31",
                         publication_date="2025-02-20", freshness_key="annual_financials")
    assert late.is_available_as_of("2025-03-01")[0] is True
    assert late.is_available_as_of("2025-01-15")[0] is False
    ok += 2

    # a fact with no publication date can never be claimed historically
    undated = FinancialFact("revenue", 100, "esef", "2024-12-31")
    avail, state = undated.is_available_as_of("2026-01-01")
    assert avail is False and state == State.POINT_IN_TIME_UNVERIFIED
    ok += 1

    # independence: same origin does not verify
    a = FinancialFact("revenue", 100, "esef", "2024-12-31", publication_date="2025-02-20")
    b = FinancialFact("revenue", 100, "annual_report", "2024-12-31", publication_date="2025-03-10")
    v, d = corroborate([a, b])
    assert v == Verification.CROSS_CHECKED, v
    assert d["independent_origins"] == 1
    ok += 1

    # independence: different origins do verify
    c = FinancialFact("revenue", 100, "mfn", "2024-12-31", publication_date="2025-02-20")
    v2, d2 = corroborate([a, c])
    assert v2 == Verification.VERIFIED and d2["independent_origins"] == 2
    ok += 1

    # disagreement beyond tolerance is a conflict regardless of origins
    e = FinancialFact("revenue", 103, "mfn", "2024-12-31", publication_date="2025-02-20")
    assert corroborate([a, e])[0] == Verification.CONFLICT
    ok += 1

    # a zero from one source is NOT agreement with a large real value from
    # another origin, however the spread is denominated
    zero = FinancialFact("revenue", 0.0, "esef", "2024-12-31", publication_date="2025-02-20")
    big = FinancialFact("revenue", 5_000_000, "mfn", "2024-12-31", publication_date="2025-02-20")
    v3, d3 = corroborate([zero, big])
    assert v3 != Verification.VERIFIED, v3
    assert v3 == Verification.CONFLICT, v3
    ok += 1

    # every value zero really is agreement - denom==0 must not raise or conflict
    zero2 = FinancialFact("revenue", 0.0, "mfn", "2024-12-31", publication_date="2025-02-20")
    v4, d4 = corroborate([zero, zero2])
    assert v4 == Verification.VERIFIED, v4
    assert d4["spread"] == 0.0
    ok += 1

    # a normal agreeing pair (same as the independence check above) still
    # returns VERIFIED - the denominator change must not disturb this
    assert v2 == Verification.VERIFIED and d2["independent_origins"] == 2
    ok += 1

    # staleness
    old = FinancialFact("price", 100, "nasdaq_reference", "2026-01-02",
                        publication_date="2026-01-02", freshness_key="price")
    assert old.staleness(today)[0] is True
    ok += 1

    # filter_as_of respects mode
    kept, rej = filter_as_of([late], "2025-01-15", Mode.HISTORICAL)
    assert kept == [] and len(rej) == 1
    kept2, _ = filter_as_of([late], "2025-01-15", Mode.HINDSIGHT)
    assert len(kept2) == 1
    ok += 2

    # an upper-bound publication date still gates correctly, but is penalised
    exact = FinancialFact("revenue", 100, "esef", "2024-12-31",
                          publication_date="2025-05-08")
    bound = FinancialFact("revenue", 100, "esef", "2024-12-31",
                          publication_date="2025-05-08",
                          publication_is_upper_bound=True)
    assert bound.is_available_as_of("2025-06-01")[0] is True
    assert bound.is_available_as_of("2025-03-01")[0] is False
    assert bound.confidence < exact.confidence
    ok += 3

    print("finfact selftest: %d assertions passed" % ok)


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(__doc__)
        print("Run with --selftest to verify the guarantees.")
