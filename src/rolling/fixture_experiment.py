"""Entirely synthetic matched experiment for the Part 3 timing harness.

Stands in for the frozen Section 0 real-data handoff until it exists. It uses
only shared classes and the shared benchmark-weight helper; it is not market
data, not a Section 0 rebuild and its results must not be read as findings.
Sizing is a supplied fixed contract count per root (the same for every timing
rule), standing in for the Section 0 capacity -> rounding baseline.
"""
from datetime import date, timedelta
from decimal import Decimal
import math

from src.benchmark.constant_maturity import constant_maturity_weights
from src.contracts.eligibility import Contract, is_eligible
from src.contracts.maturity import days_to_anchor
from src.data.asof import ObservationStore, normalise
from src.data.calendar import SessionCalendar, TradingSession
from src.portfolio.baseline import build_baseline_path
from src.portfolio.holdings import Holding
from src.rolling.analysis import TimingExperiment
from src.validation.schemas import Identity, ROOTS

HOLIDAYS = {"2031-01-20", "2031-02-17", "2031-04-18"}
N_SESSIONS, N_DECISIONS = 140, 106
# Root -> (outgoing anchor session, base price in cents, contracts held, volume cross, OI cross)
# Cross = sessions before the anchor at which the incoming leads (None = never).
SPEC = {"ZC": (30, 450, 1481, 9, 3), "ZS": (45, 1000, 667, 12, 7), "ZW": (60, 550, 1212, None, 8)}
NEXT_ANCHOR, FAR_ANCHOR = 125, 138


def _sessions():
    day, out = date(2031, 1, 2), []
    while len(out) < N_SESSIONS:
        if day.weekday() < 5 and day.isoformat() not in HOLIDAYS:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _price(base, i, contract_offset):
    raw = base + contract_offset + 0.15 * i * (1 + contract_offset / 40) + 6 * math.sin(i / (3 + contract_offset / 10))
    return Decimal(round(raw * 4)) / 4  # quarter-cent ticks


def build_synthetic_experiment(fixed_aum=Decimal("100000000")):
    dates = _sessions()
    calendar = SessionCalendar(TradingSession(d, f"{d}T18:15:00Z", f"{d}T18:15:00Z") for d in dates)
    catalogue, ids = {}, {}
    for r, root in enumerate(ROOTS):
        anchors = {"NEAR": SPEC[root][0], "NEXT": NEXT_ANCHOR, "FAR": FAR_ANCHOR}
        for k, (tag, a) in enumerate(anchors.items()):
            symbol = f"{root}_SYN_{tag}"
            ids[symbol] = 100 * (r + 1) + k
            expiration = (date.fromisoformat(dates[a]) + timedelta(days=3)).isoformat()
            catalogue[symbol] = Contract(Identity("GLBX.MDP3", 1, ids[symbol], symbol, root), dates[0],
                                         dates[a], expiration, "50", "synthetic Part 3 harness, not CME metadata")
    identities = {s: c.identity for s, c in catalogue.items()}

    def raw(symbol, day, stat, value, published):
        return {"symbol": symbol, "hd": {"publisher_id": 1, "instrument_id": ids[symbol], "ts_event": published},
                "ts_recv": published, "ts_ref": f"{day}T00:00:00Z", "stat_type": stat,
                "price": value if stat == 3 else None, "quantity": value if stat != 3 else None,
                "stat_flags": 3 if stat == 3 else 0, "update_action": 1, "sequence": 1, "channel_id": 1}

    records = []
    for i, day in enumerate(dates):
        lagged = f"{(date.fromisoformat(day) + timedelta(days=1)).isoformat()}T10:00:00Z"
        for root in ROOTS:
            anchor, base, _, vol_cross, oi_cross = SPEC[root]
            for k, tag in enumerate(("NEAR", "NEXT", "FAR")):
                symbol = f"{root}_SYN_{tag}"
                records.append(raw(symbol, day, 3, str(_price(base, i, 8 * k)), f"{day}T19:00:00Z"))
            for stat, cross in ((6, vol_cross), (9, oi_cross)):
                leads = cross is not None and i >= anchor - cross
                near, nxt = (5000, 4000) if stat == 6 else (20000, 15000)
                records += [raw(f"{root}_SYN_NEAR", day, stat, near, lagged),
                            raw(f"{root}_SYN_NEXT", day, stat, near + 1000 if leads else nxt, lagged),
                            raw(f"{root}_SYN_FAR", day, stat, nxt // 4, lagged)]
    store = ObservationStore([normalise(r, identities, calendar) for r in records], calendar, 1)

    decision_dates = dates[1:1 + N_DECISIONS]
    curve = [{"date": d, "commodity": c.identity.root, "contract": s, "anchor": c.anchor,
              "days_to_anchor": days_to_anchor(calendar, d, c.anchor), "eligible": is_eligible(c, d)}
             for d in decision_dates for s, c in catalogue.items()]
    path = build_baseline_path(curve, decision_dates, ROOTS, calendar, catalogue)

    benchmark = []
    for i, d in enumerate(decision_dates):
        cutoff = calendar.sessions[d].decision_cutoff
        for root in ROOTS:
            eligible = {s: days_to_anchor(calendar, d, c.anchor) for s, c in catalogue.items()
                        if c.identity.root == root and is_eligible(c, d)}
            for symbol, weight in constant_maturity_weights(eligible, 60).weights.items():
                benchmark.append(Holding(date=dates[calendar.indices[d] + 1], strategy="benchmark",
                                         commodity=root, contract=symbol, decision_cutoff=cutoff,
                                         formed_at=cutoff, inputs_as_of=cutoff,
                                         benchmark_fractional_contracts=SPEC[root][2] * weight,
                                         details={"flags": ["SYNTHETIC_PART3_HARNESS"]}))

    def quantity_for(commodity, contract, decision_date):
        return SPEC[commodity][2]

    last = date.fromisoformat(dates[-1]) + timedelta(days=1)
    return TimingExperiment(calendar, catalogue, store, path, tuple(benchmark), quantity_for,
                            Decimal(fixed_aum), f"{last.isoformat()}T00:00:00Z", "synthetic_part3_harness")
