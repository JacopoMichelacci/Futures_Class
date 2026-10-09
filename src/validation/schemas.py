"""Shared value types and one schema-name registry for Phase A and deferred exports."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re

ROOTS = ("ZC", "ZS", "ZW")


class ContractError(ValueError):
    """An input violates the shared contract and cannot be used silently."""


def decimal_value(value, name="value") -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ContractError(f"{name} must be numeric") from exc
    if not number.is_finite():
        raise ContractError(f"{name} must be finite")
    return number


def utc_ns(value: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z", value):
        raise ContractError("Explicit UTC timestamp required")
    try:
        moment = datetime.fromisoformat(value[:19] + "+00:00")
    except ValueError as exc:
        raise ContractError("Invalid UTC timestamp") from exc
    delta = moment - datetime(1970, 1, 1, tzinfo=timezone.utc)
    fraction = value[20:-1] if value[19:20] == "." else ""
    return (delta.days * 86400 + delta.seconds) * 10**9 + int(fraction.ljust(9, "0") or "0")


@dataclass(frozen=True)
class Identity:
    dataset: str
    publisher_id: int
    instrument_id: int
    contract: str
    root: str

    def __post_init__(self):
        if self.root not in ROOTS or not self.dataset or not self.contract:
            raise ContractError("Explicit dataset, contract and locked commodity root required")
        if type(self.publisher_id) is not int or type(self.instrument_id) is not int or min(self.publisher_id, self.instrument_id) < 0:
            raise ContractError("Non-negative integer publisher/instrument IDs required")


# Optional descriptive fields may be null in fixture/engine adapters. Identity,
# quantities and decision provenance are mandatory before accounting.
HOLDINGS_COLUMNS = (
    "date", "strategy", "commodity", "contract", "target_weight", "target_notional",
    "benchmark_fractional_contracts", "pre_capacity_contracts", "capacity_limit_contracts",
    "implemented_contracts", "price", "dollar_contract_notional", "implemented_notional",
    "maturity_rank", "days_to_anchor", "flags", "decision_cutoff", "formed_at", "inputs_as_of",
)
# Observation.view() is a revision-level/as-of record, NOT the wide processed
# daily_contract_data table. Keep its separate name and its explicit source root.
OBSERVATION_COLUMNS = (
    "dataset", "publisher_id", "instrument_id", "contract", "root", "observation_id",
    "trading_date", "publication_timestamp", "publication_timestamp_basis", "venue_event_timestamp",
    "statistic_type", "statistic_value", "value_unit", "stat_flags", "settlement_finality",
    "settlement_quality", "update_action", "sequence", "channel_id", "session_index", "is_session",
    "invalid_reason", "decision_date", "return_interval_end", "decision_cutoff", "calendar_id",
    "decision_session_index", "return_interval_session_index", "available_as_of", "eligible_as_of",
    "selected_as_of", "lag_from_decision_sessions", "lag_from_interval_end_sessions", "adv20_window_index",
)

# Version 8 Appendix B names are reserved here even where Phase A does not
# materialise a table. Declarations do not imply production validation or data.
# HOLDINGS_COLUMNS remains the unified engine representation from Section 36;
# the dedicated benchmark export is a distinct, explicitly deferred view.
TABLE_COLUMNS = {
    "statistic_observations": OBSERVATION_COLUMNS,
    "contracts": (
        "commodity", "root", "contract", "maturity_month", "maturity_year", "activation",
        "expiration", "first_notice", "anchor", "min_price_increment", "min_price_increment_amount",
        "display_factor", "main_fraction", "sub_fraction", "unit_of_measure", "unit_of_measure_qty",
        "derived_price_scale", "tick_value", "source_note", "first_notice_source_url", "first_notice_retrieved_at",
        "dataset", "publisher_id", "instrument_id", "dollars_per_price_unit", "anchor_source",
    ),
    "daily_contract_data": (
        "trading_date", "commodity", "contract", "eligible", "settlement", "settlement_quality",
        "cleared_volume", "open_interest", "days_to_anchor", "maturity_rank", "dollar_contract_notional",
    ),
    "eligible_curve": ("date", "commodity", "contract", "anchor", "days_to_anchor", "maturity_rank", "eligible"),
    "roll_events": ("date", "commodity", "outgoing_contract", "baseline_destination", "anchor"),
    "benchmark_holdings": ("date", "commodity", "contract", "target_weight", "interpolation_weight", "target_notional", "fractional_contracts"),
    "baseline_holdings": HOLDINGS_COLUMNS,
    "strategy_holdings": HOLDINGS_COLUMNS,
    "strategy_daily": ("date", "strategy", "benchmark_daily_return", "strategy_daily_return", "daily_tracking_difference", "squared_tracking_difference", "common_valid_date", "implemented_notional_share", "quality_flags", "fixed_aum", "invalid_reasons"),
    "contract_returns": ("trading_date", "commodity", "contract", "contract_return", "tracking_price_eligible", "quality_flags"),
}

# A single readiness map prevents a reserved production name being mistaken for
# a completed export or validator. No Phase B table is built by these constants.
TABLE_SCHEMA_STATUS = {
    name: "phase_b_deferred_declaration" for name in TABLE_COLUMNS
}
TABLE_SCHEMA_STATUS.update({
    "statistic_observations": "phase_a_asof_interface",
    "eligible_curve": "phase_a_synthetic_input",
    "roll_events": "phase_a_generated_contract_path",
    "baseline_holdings": "phase_a_holdings_interface",
    "strategy_holdings": "phase_a_holdings_interface",
    "strategy_daily": "phase_a_returns_with_deferred_diagnostics",
})
DEFERRED_STRATEGY_DAILY_FIELDS = ("implemented_notional_share", "quality_flags")


def load_project_config(path: str | Path) -> dict:
    """project.yaml uses JSON syntax, a YAML subset, to avoid a new dependency."""
    with Path(path).open() as source:
        config = json.load(source, parse_float=Decimal)
    if config.get("project_spec_version") != "8.0" or config.get("architecture") != "single_portfolio_four_independent_strands":
        raise ContractError("Version 8 single-portfolio architecture required")
    if config.get("dataset") != "GLBX.MDP3" or tuple(config.get("roots", [])) != ROOTS:
        raise ContractError("The dataset and commodity universe are locked")
    if sum(decimal_value(config["target_weights"][r]) for r in ROOTS) != 1:
        raise ContractError("Target weights must sum exactly to one")
    if config.get("benchmark_target_days") != 60 or config.get("adv_lookback_days") != 20:
        raise ContractError("Benchmark and ADV horizons are locked")
    expected = {
        "baseline_roll_offset_trading_days": -5,
        "capacity_participation_rate": Decimal("0.025"), "capacity_liquidation_horizon_days": 1,
        "tracking_min_common_dates": 100, "tracking_min_candidate_survival": Decimal("0.70"),
        "rounding_reference_aum_usd": 1000000, "rounding_fallback_reference_aum_usd": 250000,
        "selection_roll_reference_aum_usd": 100000000, "selection_roll_fallback_reference_aum_usd": 10000000,
        "capacity_reference_aum_usd": 1000000000, "capacity_fallback_reference_aum_usd": 2500000000,
        "aum_grid_usd": [1000000, 10000000, 100000000, 1000000000],
        "tracking_rmse_tie_tolerance_bps_daily": Decimal("0.1"),
    }
    if any(config.get(key) != value for key, value in expected.items()):
        raise ContractError("A locked Version 8 baseline, AUM or metric parameter differs")
    if [decimal_value(config["target_weights"][r]) for r in ROOTS] != [Decimal("0.333333"), Decimal("0.333333"), Decimal("0.333334")]:
        raise ContractError("Locked target weights differ")
    timing_policy = {
        "max_liquidity_source_age_sessions": 1,
        "decision_cutoff_policy": "explicit_per_session_at_or_before_starting_settlement",
        "real_data_timing_status": "phase_b_verification_required",
    }
    for key, value in timing_policy.items():
        if type(config.get(key)) is not type(value) or config[key] != value:
            raise ContractError(f"Shared Phase A timing policy differs: {key}")
    return config
