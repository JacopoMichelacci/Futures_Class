"""Synthetic matched experiment for the Part 3 capacity harness.

It tests plumbing only.  It is deliberately not historical market data and its
output must never be reported as a capacity finding.
"""
from datetime import date, timedelta
from decimal import Decimal

from src.capacity.analysis import CapacityExperiment
from src.contracts.eligibility import Contract
from src.data.asof import ObservationStore, normalise
from src.data.calendar import SessionCalendar, TradingSession
from src.portfolio.holdings import Holding
from src.validation.schemas import Identity, ROOTS

N_SESSIONS = 126


def _sessions():
    current, dates = date(2031, 1, 2), []
    while len(dates) < N_SESSIONS:
        if current.weekday() < 5:
            dates.append(current.isoformat())
        current += timedelta(days=1)
    return dates


def build_synthetic_experiment():
    dates = _sessions()
    calendar = SessionCalendar(TradingSession(day, f"{day}T18:00:00Z", f"{day}T17:30:00Z") for day in dates)
    catalogue, identities, contract_specs = {}, {}, {}
    for root_index, root in enumerate(ROOTS):
        for rank, tag in enumerate(("NEAR", "NEXT", "FAR"), start=1):
            symbol = f"{root}_CAP_{tag}"
            instrument_id = (root_index + 1) * 10 + rank
            anchor = (date.fromisoformat(dates[-1]) + timedelta(days=10 + rank * 30)).isoformat()
            identity = Identity("GLBX.MDP3", 1, instrument_id, symbol, root)
            catalogue[symbol] = Contract(identity, dates[0], anchor,
                                         (date.fromisoformat(anchor) + timedelta(days=3)).isoformat(),
                                         "50", "synthetic Part 3 harness, not CME metadata")
            identities[symbol] = identity
            # Settlement cents, ADV20 contracts. The near contract binds at $1bn.
            contract_specs[symbol] = (460 + root_index * 110 + rank * 7, (200000, 80000, 100000)[rank - 1])

    def raw(symbol, day, stat_type, value):
        return {"symbol": symbol,
                "hd": {"publisher_id": 1, "instrument_id": identities[symbol].instrument_id,
                       "ts_event": f"{day}T16:00:00Z"},
                "ts_recv": f"{day}T17:00:00Z", "ts_ref": f"{day}T00:00:00Z",
                "stat_type": stat_type, "price": value if stat_type == 3 else None,
                "quantity": value if stat_type != 3 else None,
                "stat_flags": 3 if stat_type == 3 else 0, "update_action": 1,
                "sequence": 1, "channel_id": 1}

    observations = []
    for index, day in enumerate(dates):
        for symbol, (base_price, base_volume) in contract_specs.items():
            price = Decimal(base_price) + Decimal(index) * Decimal("0.17")
            volume = base_volume + (index % 7) * 1000
            observations.extend((normalise(raw(symbol, day, 3, str(price)), identities, calendar),
                                 normalise(raw(symbol, day, 6, volume), identities, calendar)))
    store = ObservationStore(observations, calendar, 1)
    evaluation_dates = tuple(dates[21:])
    decision_dates = tuple(dates[20:-1])
    curve = []
    for day in decision_dates:
        for root in ROOTS:
            for rank, tag in enumerate(("NEAR", "NEXT", "FAR"), start=1):
                symbol = f"{root}_CAP_{tag}"
                curve.append({"date": day, "commodity": root, "contract": symbol,
                              "anchor": catalogue[symbol].anchor, "eligible": True,
                              "maturity_rank": rank, "days_to_anchor": 60 + rank * 30})

    weights = {"ZC": Decimal("0.333333"), "ZS": Decimal("0.333333"), "ZW": Decimal("0.333334")}

    def value_on(root, day):
        symbol = f"{root}_CAP_NEAR"
        return store.latest_available(symbol, "settlement", calendar.decision(day)).observation.statistic_value * catalogue[symbol].dollars_per_price_unit

    def targets_for_aum(aum):
        return tuple({"date": day, "strategy": "capacity", "commodity": root,
                      "primary_contract": f"{root}_CAP_NEAR", "target_weight": weights[root],
                      "target_notional": Decimal(aum) * weights[root]}
                     for day in evaluation_dates for root in ROOTS)

    def benchmark_for_aum(aum):
        rows = []
        for day in evaluation_dates:
            cutoff = calendar.decision(day).cutoff_utc
            for root in ROOTS:
                symbol = f"{root}_CAP_NEAR"
                target_notional = Decimal(aum) * weights[root]
                rows.append(Holding(day, "benchmark", root, symbol, cutoff, cutoff, cutoff,
                                    benchmark_fractional_contracts=target_notional / value_on(root, day),
                                    details={"flags": ["SYNTHETIC_PART3_HARNESS"]}))
        return tuple(rows)

    last = date.fromisoformat(dates[-1]) + timedelta(days=1)
    return CapacityExperiment(calendar, catalogue, store, tuple(curve), targets_for_aum,
                              benchmark_for_aum, f"{last.isoformat()}T00:00:00Z",
                              "synthetic_part3_harness")
