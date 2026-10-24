"""Offline input contracts. No acquisition, portfolio construction or sample choice."""
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import re
from types import MappingProxyType

from src.contracts.eligibility import Contract, is_eligible
from src.contracts.maturity import days_to_anchor
from src.contracts.value import exact_decimal_value, validate_contract_value
from src.data.asof import Observation, STATISTICS, Selection, normalise
from src.validation.schemas import (ContractError, Identity, ROOTS, OBSERVATION_EVENT_COLUMNS,
                                    POST_CAPACITY_TARGET_COLUMNS, decimal_value, utc_ns)

IDENTITY_FIELDS = ("dataset", "publisher_id", "instrument_id", "contract", "root")
HISTORY_FORMAT = "section0_observation_history_v1"
POST_CAPACITY_NUMERIC_FIELDS = (
    "post_capacity_fractional_contracts", "dollar_contract_notional",
    "capacity_limit_contracts", "target_weight", "target_notional",
)
MetadataScalar = str | int | bool | Decimal | None


def _records(value, name):
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        raise ContractError(f"{name} must be a collection of records")
    return value


def _mapping(value, name):
    if not isinstance(value, Mapping):
        raise ContractError(f"{name} must be a mapping")
    return value


def _flat_metadata(record):
    for key, value in record.items():
        if type(key) is not str or type(value) not in (str, int, bool, Decimal, type(None)):
            raise ContractError("Catalogue metadata requires string keys and flat immutable scalar values")
        if isinstance(value, Decimal) and not value.is_finite():
            raise ContractError(f"Catalogue metadata must be finite: {key}")
    return dict(record)


def _date(value, name):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError as exc:
        raise ContractError(f"Explicit ISO date required: {name}") from exc
    return value


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"Explicit source/identifier required: {name}")
    return value


def _identity(record):
    if record.get("dataset") != "GLBX.MDP3":
        raise ContractError("GLBX.MDP3 identity required")
    _text(record.get("contract"), "contract")
    return Identity(**{k: record.get(k) for k in IDENTITY_FIELDS})


@dataclass(frozen=True)
class ProductionCatalogue:
    contracts: Mapping[str, Contract]
    records: Mapping[str, Mapping[str, MetadataScalar]]
    catalogue_id: str


def validate_catalogue(records):
    """Validate supplied records and preserve FND/value provenance.

    This verifies the input catalogue, not its completeness against CME listings.
    An acquisition/source-register check must establish that separate fact.
    """
    contracts, metadata, identities = {}, {}, set()
    for raw in _records(records, "Catalogue"):
        row = _flat_metadata(validate_contract_value(raw))
        identity = _identity(row)
        if row.get("commodity") != identity.root:
            raise ContractError("Catalogue commodity/root mismatch")
        symbol = identity.contract
        key = (identity.publisher_id, identity.instrument_id)
        if symbol in contracts or key in identities:
            raise ContractError("Duplicate or aliased catalogue identity")
        first_notice = _date(row.get("first_notice"), "first_notice")
        expiration = _date(row.get("expiration"), "expiration")
        if first_notice >= expiration:
            raise ContractError("Grain first_notice must precede expiration; expiration is not a substitute")
        if row.get("anchor") != first_notice:
            raise ContractError("Delivery anchor must equal the explicitly sourced first_notice")
        for field in ("first_notice_source_url", "anchor_source", "source_note"):
            _text(row.get(field), field)
        if not re.fullmatch(r"https?://[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?"
                            r"(?::[0-9]+)?(?:[/?#][^\s]*)?",
                            row["first_notice_source_url"], re.IGNORECASE):
            raise ContractError("first_notice_source_url requires HTTP(S), a host and no whitespace")
        retrieved = _text(row.get("first_notice_retrieved_at"), "first_notice_retrieved_at")
        try:
            if len(retrieved) == 10:
                _date(retrieved, "first_notice_retrieved_at")
            elif datetime.fromisoformat(retrieved).utcoffset() is None:
                raise ValueError
        except ValueError as exc:
            raise ContractError("FND retrieval requires an ISO date or timezone-aware timestamp") from exc
        activation = _date(row.get("activation"), "activation")
        contracts[symbol] = Contract(identity, activation, first_notice, expiration,
                                     row["dollars_per_price_unit"], row["anchor_source"])
        identities.add(key)
        metadata[symbol] = MappingProxyType(row)
    if not contracts:
        raise ContractError("A nonempty production catalogue is required")
    # Explicit Decimal tagging also distinguishes numeric evidence from text.
    payload = json.dumps({s: {k: {"decimal": str(v)} if isinstance(v, Decimal) else v
                             for k, v in metadata[s].items()} for s in sorted(metadata)},
                         sort_keys=True, allow_nan=False)
    return ProductionCatalogue(MappingProxyType(contracts), MappingProxyType(metadata),
                               hashlib.sha256(payload.encode()).hexdigest())


def validate_complete_curve(rows, decision_dates, catalogue, calendar):
    """Check all three roots against every active contract in this catalogue.

    Active means activation <= day <= expiration. Present post-FND rows remain
    explicit eligible=False. Unsupported eligibility overrides fail closed.
    The returned evidence certifies only this catalogue/calendar/date scope.
    """
    dates = tuple(_date(d, "decision date") for d in _records(decision_dates, "Decision dates"))
    if not dates or len(set(dates)) != len(dates) or any(d not in calendar.indices for d in dates):
        raise ContractError("Unique explicit decision sessions required")
    groups = {(d, r): {} for d in dates for r in ROOTS}
    for row in _records(rows, "Curve"):
        _mapping(row, "Curve row")
        key = (_date(row.get("date"), "curve date"), _text(row.get("commodity"), "commodity"))
        symbol = _text(row.get("contract"), "contract")
        contract = catalogue.contracts.get(symbol)
        if key not in groups or contract is None or contract.identity.root != key[1]:
            raise ContractError("Curve date/root/contract lies outside the declared scope")
        if symbol in groups[key]:
            raise ContractError("Duplicate curve contract row")
        if type(row.get("eligible")) is not bool:
            raise ContractError("Curve eligibility must be explicit, not missing")
        if row["eligible"] != is_eligible(contract, key[0]):
            raise ContractError("Eligibility disagrees with sourced activation/FND; no missing-data override")
        if row.get("anchor") != contract.anchor:
            raise ContractError("Curve anchor disagrees with sourced FND")
        distance = row.get("days_to_anchor")
        if type(distance) is not int or distance != days_to_anchor(calendar, key[0], contract.anchor):
            raise ContractError("Curve distance disagrees with verified session calendar")
        groups[key][symbol] = row
    checks = []
    for (day, root), supplied in sorted(groups.items()):
        expected = {s for s, c in catalogue.contracts.items() if c.identity.root == root
                    and c.activation <= day <= c.expiration}
        if not expected:
            raise ContractError(f"Missing active catalogue coverage for {root} on {day}")
        missing, unexpected = expected - supplied.keys(), supplied.keys() - expected
        if missing or unexpected:
            raise ContractError(f"Incomplete curve {day} {root}: missing_required_contract_rows={sorted(missing)}, "
                                f"unexpected_contract_rows={sorted(unexpected)}")
        checks.append({"date": day, "commodity": root, "contracts": tuple(sorted(expected))})
    return {"catalogue_id": catalogue.catalogue_id, "calendar_id": calendar.calendar_id,
            "complete_relative_to_catalogue": True, "checks": tuple(checks)}


def validate_experiment_targets(rows, dates):
    """One explicit commodity target per date/root; zero is data, absence is an error."""
    dates = tuple(_date(d, "experiment date") for d in _records(dates, "Experiment dates"))
    if not dates or len(set(dates)) != len(dates):
        raise ContractError("Unique experiment dates required")
    expected = {(d, r) for d in dates for r in ROOTS}
    supplied = {}
    for raw in _records(rows, "Experiment targets"):
        row = dict(_mapping(raw, "Experiment target"))
        key = (_date(row.get("date"), "target date"), _text(row.get("commodity"), "commodity"))
        if key not in expected or key in supplied:
            raise ContractError("Unexpected or duplicate experiment date/root")
        row["target_notional"] = exact_decimal_value(row.get("target_notional"), "target_notional")
        if row["target_notional"] < 0:
            raise ContractError("Commodity target notional must be non-negative")
        supplied[key] = row
    if expected - supplied.keys():
        raise ContractError(f"Missing required commodity targets: {sorted(expected - supplied.keys())}")
    return tuple(supplied[key] for key in sorted(supplied))


def validate_post_capacity_targets(rows):
    """Validate the expected rounding metadata, without deriving or rounding it.

    This boundary accepts subsets of roots; experiment completeness is separate.
    No adapter for an unmerged capacity implementation is supplied here.
    """
    result, contracts, ranks = [], set(), set()
    for raw in _records(rows, "Post-capacity targets"):
        _mapping(raw, "Post-capacity target")
        if any(k not in raw for k in POST_CAPACITY_TARGET_COLUMNS):
            raise ContractError("Incomplete post-capacity target metadata")
        row = dict(raw)
        root, symbol, rank = row["commodity"], row["contract"], row["maturity_rank"]
        if root not in ROOTS or not isinstance(symbol, str) or not symbol.strip():
            raise ContractError("Explicit root and exact contract required")
        if type(rank) is not int or rank < 1 or symbol in contracts or (root, rank) in ranks:
            raise ContractError("Duplicate contract or invalid/ambiguous maturity rank")
        for field in POST_CAPACITY_NUMERIC_FIELDS:
            row[field] = exact_decimal_value(row[field], field)
        q, cap = row["post_capacity_fractional_contracts"], row["capacity_limit_contracts"]
        if q < 0 or cap < 0 or cap != cap.to_integral_value() or q > cap:
            raise ContractError("Post-capacity quantity must be non-negative and within an integer ceiling")
        if (row["dollar_contract_notional"] <= 0 or row["target_notional"] < 0
                or not 0 <= row["target_weight"] <= 1):
            raise ContractError("Invalid post-capacity contract value or target metadata")
        contracts.add(symbol)
        ranks.add((root, rank))
        result.append(row)
    if not result:
        raise ContractError("Explicit post-capacity targets required")
    return tuple(result)


def _event_from_record(row, identities, calendar):
    _mapping(row, "Observation event")
    if set(row) != set(OBSERVATION_EVENT_COLUMNS):
        raise ContractError("Revision-level observation fields required; daily snapshots are not history")
    identity = _identity(row)
    if identities.get(identity.contract) != identity:
        raise ContractError("Observation identity does not match the supplied catalogue")
    day = _date(row["trading_date"], "trading_date")
    if utc_ns(row["publication_timestamp"]) < utc_ns(row["venue_event_timestamp"]):
        raise ContractError("Observation capture precedes venue event")
    if row["statistic_type"] not in STATISTICS.values():
        raise ContractError("Unknown statistic type")
    unit = "cents_per_bushel" if row["statistic_type"] == "settlement" else "contracts"
    if row["value_unit"] != unit:
        raise ContractError("Statistic unit mismatch")
    for field in ("stat_flags", "update_action", "sequence", "channel_id"):
        if type(row[field]) is not int or row[field] < 0:
            raise ContractError(f"Invalid observation provenance: {field}")
    if row["stat_flags"] > 255 or row["update_action"] not in (1, 2):
        raise ContractError("Invalid flags/action")
    if (row["session_index"] is not None and type(row["session_index"]) is not int
            or row["session_index"] != calendar.indices.get(day)):
        raise ContractError("Observation session index disagrees with calendar")
    reason = row["invalid_reason"]
    if reason is not None:
        _text(reason, "invalid_reason")
    raw = row["statistic_value"]
    if raw is not None and not isinstance(raw, str):
        raise ContractError("Observation values must be exact decimal strings or null")
    value = None if raw is None else decimal_value(raw, "statistic_value")
    if value is None and reason is None and row["update_action"] != 2:
        raise ContractError("Missing observation value needs an explicit invalid_reason")
    if value is not None and unit == "contracts" and (value < 0 or value != value.to_integral_value()):
        raise ContractError("Volume/OI must be non-negative whole contracts")
    if value is not None:
        # Reuse the raw normalizer's existing undefined-value convention. Do not
        # maintain another list of sentinels or convert imported events silently.
        checked = normalise({
            "symbol": identity.contract,
            "hd": {"publisher_id": identity.publisher_id, "instrument_id": identity.instrument_id,
                   "ts_event": row["venue_event_timestamp"]},
            "ts_recv": row["publication_timestamp"], "ts_ref": day + "T00:00:00Z",
            "stat_type": next(k for k, v in STATISTICS.items() if v == row["statistic_type"]),
            "price": raw, "quantity": raw, "stat_flags": row["stat_flags"],
            "update_action": row["update_action"], "sequence": row["sequence"],
            "channel_id": row["channel_id"],
        }, identities, calendar)
        if checked.statistic_value is None:
            raise ContractError("Undefined statistic value: supply normalized null with its invalid reason")
    fields = {k: row[k] for k in OBSERVATION_EVENT_COLUMNS if k not in IDENTITY_FIELDS + ("observation_id",)}
    fields["statistic_value"] = value
    observation = Observation(identity=identity, **fields)
    if observation.observation_id != row["observation_id"]:
        raise ContractError("Observation content/provenance hash mismatch")
    return observation


def serialize_observations(observations, identities, calendar):
    """Lossless JSON envelope of ALL supplied revisions, not an as-of selection."""
    rows = []
    _mapping(identities, "Observation identities")
    for observation in _records(observations, "Observation history"):
        if not isinstance(observation, Observation):
            raise ContractError("Observation history must contain shared Observation instances")
        if observation.statistic_value is not None:
            exact_decimal_value(observation.statistic_value, "statistic_value")
        row = asdict(observation)
        row.update(row.pop("identity"))
        row["statistic_value"] = (None if observation.statistic_value is None
                                  else str(observation.statistic_value))
        row["observation_id"] = observation.observation_id
        _event_from_record(row, identities, calendar)
        rows.append(row)
    return json.dumps({"format": HISTORY_FORMAT, "project_spec_version": "8.0",
                       "calendar_id": calendar.calendar_id, "observations": rows}, sort_keys=True)


def _unique_json_members(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"Duplicate JSON member: {key}")
        result[key] = value
    return result


def deserialize_observations(payload, identities, calendar):
    """Reconstruct source events; selecting versions remains ObservationStore's job."""
    _mapping(identities, "Observation identities")
    try:
        envelope = json.loads(payload, object_pairs_hook=_unique_json_members)
    except ContractError:
        raise
    except (ValueError, TypeError) as exc:
        raise ContractError("Observation history must be a JSON envelope") from exc
    if (not isinstance(envelope, dict)
            or set(envelope) != {"format", "project_spec_version", "calendar_id", "observations"}
            or envelope["format"] != HISTORY_FORMAT or envelope["project_spec_version"] != "8.0"
            or envelope["calendar_id"] != calendar.calendar_id
            or not isinstance(envelope["observations"], list)):
        raise ContractError("Versioned revision history and matching calendar required, not a daily snapshot")
    if any(not isinstance(row, dict) for row in envelope["observations"]):
        raise ContractError("Observation records must be objects")
    return tuple(_event_from_record(row, identities, calendar) for row in envelope["observations"])


def observation_availability(store, contract, trading_date, statistic_type, context):
    """Distinguish absent history from a record not yet published; never infer eligibility."""
    store.validate_context(context)
    _text(contract, "contract")
    _date(trading_date, "trading_date")
    if statistic_type not in STATISTICS.values():
        raise ContractError("Unknown statistic type")
    if trading_date not in store.calendar.indices:
        return Selection(None, "non_session_or_outside_verified_calendar")
    if store.calendar.indices[trading_date] > context.decision_index:
        return Selection(None, "future_trading_date")
    rows = store.groups.get((contract, trading_date, statistic_type), ())
    if not rows:
        return Selection(None, "missing_observation")
    if not any(utc_ns(o.publication_timestamp) <= context.cutoff_ns for o in rows):
        return Selection(None, "unavailable_by_decision_cutoff")
    return store.select_version(contract, trading_date, statistic_type, context.cutoff_ns)
