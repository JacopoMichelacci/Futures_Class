"""Load the small shared synthetic fixture, with no implicit real-data fallback."""
from dataclasses import dataclass
import json
from pathlib import Path

from src.contracts.eligibility import Contract
from src.data.asof import ObservationStore, normalise
from src.data.calendar import SessionCalendar, TradingSession
from src.portfolio.holdings import Holding, HoldingsBook
from src.validation.schemas import ContractError, Identity


@dataclass(frozen=True)
class FixtureBundle:
    raw: dict
    calendar: SessionCalendar
    catalogue: dict
    observations: ObservationStore
    holdings: HoldingsBook


def load_fixture(path: str | Path, max_source_age_sessions=1) -> FixtureBundle:
    with Path(path).open() as source:
        raw = json.load(source)
    if (raw.get("spec_version") != "8.0"
            or raw.get("fixture_format") != "section0_phase_a_cases_v1"
            or not raw.get("provenance", "").startswith("Entirely synthetic")):
        raise ContractError("This loader accepts the explicit Phase A synthetic fixture only")
    calendar = SessionCalendar(TradingSession(**r) for r in raw["calendar"])
    catalogue = {}
    for r in raw["contracts"]:
        identity = Identity(**{k: r[k] for k in ("dataset", "publisher_id", "instrument_id", "contract", "root")})
        catalogue[r["contract"]] = Contract(identity, **{k: r[k] for k in ("activation", "anchor", "expiration", "dollars_per_price_unit", "anchor_source")})
    identities = {s: c.identity for s, c in catalogue.items()}
    observations = []
    stat_types = {"settlement": 3, "cleared_volume": 6, "open_interest": 9}
    for r in raw["observations"]:
        identity = identities[r["contract"]]
        record = {"symbol": r["contract"], "hd": {"publisher_id": identity.publisher_id,
                  "instrument_id": identity.instrument_id, "ts_event": r["publication_timestamp"]},
                  "ts_recv": r["publication_timestamp"], "ts_ref": r["trading_date"] + "T00:00:00Z",
                  "stat_type": stat_types[r["statistic_type"]], "price": r["statistic_value"] if r["statistic_type"] == "settlement" else None,
                  "quantity": r["statistic_value"] if r["statistic_type"] != "settlement" else None,
                  "stat_flags": r["stat_flags"], "update_action": 1, "sequence": r["sequence"], "channel_id": 1}
        observations.append(normalise(record, identities, calendar))
    store = ObservationStore(observations, calendar, max_source_age_sessions)
    holdings = HoldingsBook([Holding(**r) for r in raw["holdings"]], calendar, catalogue, raw["benchmark_strategy"])
    return FixtureBundle(raw, calendar, catalogue, store, holdings)
