"""Offline Phase B construction and deterministic handoff reproduction.

No network, strand engines, RMSE aggregation or preferred-method selection.
Exact decimals are exported as decimal strings in Parquet to avoid rounding
when the integer engines consume monetary quantities.
"""
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
import csv
import hashlib
import json
from pathlib import Path

from src.benchmark.constant_maturity import constant_maturity_weights
from src.contracts.eligibility import is_eligible
from src.contracts.maturity import days_to_anchor
from src.data.asof import ObservationStore
from src.data.calendar import SessionCalendar, TradingSession
from src.metrics.portfolio_pnl import portfolio_pnl
from src.portfolio.baseline import build_baseline_path
from src.portfolio.holdings import Holding, HoldingsBook
from src.validation.production import (validate_catalogue, validate_complete_curve,
                                      serialize_observations, deserialize_observations)
from src.validation.schemas import ContractError, ROOTS, decimal_value, utc_ns


def plain(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    return value


def dump(path, data):
    Path(path).write_text(json.dumps(plain(data), indent=2, sort_keys=True) + "\n")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_table(path, rows):
    import polars as pl
    if not rows:
        raise ContractError(f"Refusing an empty required export: {Path(path).name}")
    frame = pl.DataFrame(plain(rows), infer_schema_length=None)
    frame.select(sorted(frame.columns)).write_parquet(path)


def read_table(path):
    import polars as pl
    return pl.read_parquet(path).to_dicts()


class CapturingStore(ObservationStore):
    """Record exact consumed versions while keeping the original stream intact."""
    def __init__(self, observations, calendar, max_source_age_sessions=1):
        super().__init__(observations, calendar, max_source_age_sessions)
        self.consumed = {}

    def select_version(self, *args, **kwargs):
        selected = super().select_version(*args, **kwargs)
        if selected.observation is not None:
            o = selected.observation
            self.consumed[o.observation_id] = o
        return selected


class FrozenStore(CapturingStore):
    """Consumed-version projection is valid only for frozen decision contexts."""
    def __init__(self, observations, calendar, dates, max_source_age_sessions=1):
        super().__init__(observations, calendar, max_source_age_sessions)
        self.allowed_dates = frozenset(dates)

    def validate_context(self, context):
        super().validate_context(context)
        if context.return_date not in self.allowed_dates:
            raise ContractError("Consumed observations cannot support a new decision context")


@dataclass
class Inputs:
    catalogue_rows: list
    calendar_rows: list
    maturity_dates: list
    dates: list
    statistics_contracts: list
    destination_scope: list
    provenance: dict
    observations: object

    @property
    def calendar(self):
        return self.observations.calendar

    @property
    def catalogue(self):
        return validate_catalogue(self.catalogue_rows)


def load_production(production_dir, inventory_dir, config):
    """Use the accepted acquisition, checking integrity rather than re-sourcing it."""
    p, inv = Path(production_dir), Path(inventory_dir)
    scope = json.loads((p / "acquisition_scope.json").read_text())
    if scope["initial_decision_date"] != "2026-02-27" or scope["project_spec_version"] != "8.0":
        raise ContractError("The frozen production scope does not match Version 8")
    for file, expected in scope["reference_hashes"].items():
        if sha(inv / file) != expected:
            raise ContractError(f"Frozen reference changed: {file}")
    source_files = ("definition.jsonl", "statistics.jsonl", "catalogue.json",
                    "evaluation_session_calendar.json", "observation_history.json")
    file_manifest = json.loads((p / "file_hashes.json").read_text())
    hashes = {r["file"]: r["sha256"] for r in file_manifest["files"]}
    for file in source_files:
        if sha(p / file) != hashes[file]:
            raise ContractError(f"Frozen production file changed: {file}")
    if json.loads((p / "readiness_report.json").read_text())["verdict"] != "READY FOR ISSUE #5 PHASE B":
        raise ContractError("Accepted production readiness report required")
    rows = json.loads((p / "catalogue.json").read_text())["records"]
    # Preserve the remaining canonical definition fields from the frozen history.
    raw_definitions = {}
    with (p / "definition.jsonl").open() as stream:
        for line in stream:
            r = json.loads(line)
            raw_definitions.setdefault(r["raw_symbol"], r)
    for r in rows:
        raw = raw_definitions[r["contract"]]
        for field in ("maturity_month", "maturity_year", "display_factor", "main_fraction", "sub_fraction"):
            r[field] = raw[field]
    catalogue = validate_catalogue(rows)
    calendar_rows = json.loads((p / "evaluation_session_calendar.json").read_text())["sessions"]
    schedule = json.loads((inv / "cme_scheduled_sessions.json").read_text())
    horizon = max(c.anchor for c in catalogue.contracts.values())
    root_dates = {root: sorted({d["date"] for month in schedule["months"]
                               for product in month["products"] if product["root"] == root
                               for d in product["sessions"]
                               if calendar_rows[0]["trading_date"] <= d["date"] <= horizon}) for root in ROOTS}
    if not all(root_dates[root] == root_dates[ROOTS[0]] for root in ROOTS):
        raise ContractError("Root-specific maturity schedules disagree")
    maturity_dates = root_dates[ROOTS[0]]
    calendar = SessionCalendar((TradingSession(**r) for r in calendar_rows), maturity_dates)
    observations = deserialize_observations((p / "observation_history.json").read_text(),
                                           {s: c.identity for s, c in catalogue.contracts.items()}, calendar)
    store = CapturingStore(observations, calendar, config["max_liquidity_source_age_sessions"])
    dates = [d for d in calendar.dates if "2026-03-02" <= d <= "2026-09-30"]
    if len(dates) != 148 or calendar.decision(dates[0]).decision_date != "2026-02-27":
        raise ContractError("Frozen evaluation boundary changed")
    planning = json.loads((inv / "contract_inventory.json").read_text())["scenarios"]
    if len(planning) != 1 or planning[0]["seed_decision"] != "2026-02-27":
        raise ContractError("Only the approved February seed is permitted")
    planned = planning[0]
    # These are the already accepted contract-reach bounds, not a second engine.
    destination = []
    for contract, footprint in sorted(planned["footprint"].items()):
        for role, span in sorted(footprint["roles"].items()):
            if role.startswith("destination_"):
                destination.append(dict(contract=contract, commodity=contract[:2],
                                        first=span["first"], last=span["last"], witness=role))
    provenance = dict(project_spec_version="8.0", dataset="GLBX.MDP3", start="2026-01-26",
                      snapshot_end_exclusive="2026-10-02T00:00:00Z", initialisation="2026-02-27",
                      first_return="2026-03-02", last_return="2026-09-30",
                      production_hashes={k: hashes[k] for k in source_files},
                      accepted_reference_hashes=scope["reference_hashes"],
                      projection="Only exact consumed event versions; full raw revision stream remains local",
                      source_date_basis="ts_ref", publication_basis="ts_recv",
                      fnd_policy="Direct CME or accepted official-source-derived FND; never expiration",
                      timing_search_policy="Issue #23: first-held through strictly before baseline -5; fallback -5",
                      normal_settlement_source="https://www.cmegroup.com/trading/agricultural/files/daily-grains-settlement-procedure.pdf",
                      calendar_source="https://www.cmegroup.com/trading-hours.html")
    return Inputs(rows, calendar_rows, maturity_dates, dates,
                  planned["required_statistics_symbols"], destination, provenance, store)


def target_and_cap(aum, weight, price, multiplier, adv20, participation):
    values = [decimal_value(v) for v in (aum, weight, price, multiplier, adv20, participation)]
    a, w, p, m, adv, rate = values
    if a <= 0 or p <= 0 or m <= 0 or w < 0 or adv < 0 or rate <= 0:
        raise ContractError("Invalid decision-time sizing input")
    notional = a * w
    q = notional / (p * m)
    cap = (rate * adv).to_integral_value(rounding=ROUND_FLOOR)
    constrained = min(q, cap)
    return dict(target_notional=notional, dollar_contract_notional=p * m,
                pre_capacity_contracts=q, capacity_limit_contracts=cap,
                post_capacity_fractional_contracts=constrained,
                implemented_contracts=constrained.to_integral_value(rounding=ROUND_HALF_UP),
                capacity_binding=q > cap)


def choose_reference(default, fallback, check, require_binding=False):
    first = check(default)
    if bool(first["binding_rows"]) == require_binding:
        return default, [first]
    second = check(fallback)
    if require_binding and not second["binding_rows"]:
        # Version 8 permits reporting a non-binding capacity limitation, never
        # searching additional AUMs or altering participation to manufacture it.
        return fallback, [first, second]
    if bool(second["binding_rows"]) != require_binding:
        raise ContractError("Declared AUM fallback fails feasibility; stop without tuning")
    return fallback, [first, second]


def holdings_from_contract_path(path_rows, decision_inputs, catalogue, calendar, config, aum,
                                strategy="baseline"):
    """Shared unchanged sizing/cap/nearest layer for destination or timing paths.

    The caller supplies contract identities only. Capacity/rounding strands use
    the frozen pre/post-capacity targets and their own engines instead.
    """
    lookup = {(r["date"], r["contract"]): r for r in decision_inputs}
    holdings, targets = [], []
    for pos in path_rows:
        day, root, symbol = pos["date"], pos["commodity"], pos["contract"]
        ctx = calendar.decision(day)
        r = lookup.get((day, symbol))
        if (pos.get("implemented", True) is not True or r is None or not r["decision_valid"]
                or root not in config["target_weights"] or catalogue[symbol].identity.root != root):
            raise ContractError("Path requires explicit implemented, publication-safe contract inputs")
        if not is_eligible(catalogue[symbol], day):
            raise ContractError("Implemented path would hold a contract at/after FND")
        size = target_and_cap(aum, config["target_weights"][root], r["settlement"],
                              catalogue[symbol].dollars_per_price_unit, r["adv20"],
                              config["capacity_participation_rate"])
        latest = max((r["settlement_published_at"], r["adv20_latest_publication"]), key=utc_ns)
        holdings.append(Holding(day, strategy, root, symbol, ctx.cutoff_utc, ctx.cutoff_utc, latest,
            implemented_contracts=size["implemented_contracts"],
            details=dict(target_weight=config["target_weights"][root], target_notional=size["target_notional"],
                pre_capacity_contracts=size["pre_capacity_contracts"], capacity_limit_contracts=size["capacity_limit_contracts"],
                price=r["settlement"], dollar_contract_notional=size["dollar_contract_notional"],
                implemented_notional=size["implemented_contracts"] * size["dollar_contract_notional"],
                maturity_rank=r["maturity_rank"], days_to_anchor=r["days_to_anchor"],
                flags=["capacity_truncated"] if size["capacity_binding"] else [])))
        targets.append(dict(date=day, fixed_aum=aum, commodity=root, contract=symbol,
                            maturity_rank=r["maturity_rank"], target_weight=config["target_weights"][root], **size))
    return holdings, targets


def coverage_gate(valid, candidate_count, config):
    count = sum(valid)
    if not candidate_count or len(valid) != candidate_count:
        raise ContractError("Explicit complete candidate mask required")
    survival = Decimal(count) / Decimal(candidate_count)
    return dict(candidate_dates=candidate_count, common_valid_dates=count,
                survival=survival, passed=count >= config["tracking_min_common_dates"]
                and survival >= config["tracking_min_candidate_survival"])


def build(inputs, config):
    """Construct only shared inputs, benchmark, frozen baseline and preflight."""
    calendar, store, catalogue = inputs.calendar, inputs.observations, inputs.catalogue
    contracts = catalogue.contracts
    contexts = {d: calendar.decision(d) for d in inputs.dates}
    decision_dates = [c.decision_date for c in contexts.values()]
    ranks = {s: i + 1 for root in ROOTS for i, s in enumerate(sorted(
        (s for s in contracts if s[:2] == root), key=lambda s: (contracts[s].anchor, s)))}
    curve = []
    for day in decision_dates:
        for s, c in sorted(contracts.items()):
            if c.activation <= day <= c.expiration:
                curve.append(dict(date=day, commodity=c.identity.root, contract=s, anchor=c.anchor,
                                  days_to_anchor=days_to_anchor(calendar, day, c.anchor), maturity_rank=ranks[s],
                                  eligible=is_eligible(c, day)))
    certificate = validate_complete_curve(curve, decision_dates, catalogue, calendar)
    path = build_baseline_path(curve, decision_dates, ROOTS, calendar, contracts)
    primary = {(r["date"], r["commodity"]): r["contract"] for r in path.positions}
    decision_inputs, lookup, invalid = [], {}, defaultdict(list)
    endpoints = []
    for day, context in contexts.items():
        for s in inputs.statistics_contracts:
            c = contracts[s]
            if not is_eligible(c, context.decision_date):
                continue
            selected = {kind: store.latest_available(s, kind, context).observation
                        for kind in ("settlement", "cleared_volume", "open_interest")}
            adv = store.adv20(s, context)
            reasons = [f"{s}: unavailable {k}" for k, o in selected.items() if o is None]
            if not adv["available"]:
                reasons.append(f"{s}: {adv['reason']}")
            row = dict(date=day, decision_date=context.decision_date, decision_cutoff=context.cutoff_utc,
                       commodity=s[:2], contract=s, maturity_rank=ranks[s],
                       days_to_anchor=days_to_anchor(calendar, context.decision_date, c.anchor),
                       decision_valid=not reasons, invalid_reasons=reasons)
            for kind, o in selected.items():
                row[kind] = o.statistic_value if o else None
                row[kind + "_source_date"] = o.trading_date if o else None
                row[kind + "_published_at"] = o.publication_timestamp if o else None
                row[kind + "_observation_id"] = o.observation_id if o else None
            row.update(adv20=Decimal(adv["adv20_contracts"]) if adv["available"] else None,
                       adv20_window_start=adv.get("window_start"), adv20_window_end=adv.get("window_end"),
                       adv20_latest_publication=max((r["publication_timestamp"] for r in adv["rows"]), key=utc_ns) if adv["available"] else None,
                       adv20_observation_ids=[r["observation_id"] for r in adv["rows"]])
            decision_inputs.append(row); lookup[(day, s)] = row; invalid[day].extend(reasons)
            marks = [store.select_version(s, d, "settlement", utc_ns(inputs.provenance["snapshot_end_exclusive"]) - 1).observation
                     for d in (context.decision_date, day)]
            if any(o is None for o in marks):
                invalid[day].append(f"{s}: missing exact-contract settlement endpoint")
            else:
                old, new = marks
                entry = dict(trading_date=day, previous_session=context.decision_date, commodity=s[:2], contract=s,
                             previous_settlement=old.statistic_value, settlement=new.statistic_value,
                             dollars_per_price_unit=c.dollars_per_price_unit,
                             settlement_change=new.statistic_value - old.statistic_value,
                             contract_return=(new.statistic_value - old.statistic_value) / old.statistic_value,
                             tracking_price_eligible=True, quality_flags=[old.quality, new.quality],
                             previous_observation_id=old.observation_id, observation_id=new.observation_id)
                endpoints.append(entry)

    timing_inputs = []
    for event in path.roll_events:
        pair = [event["outgoing_contract"], event["baseline_destination"]]
        for day, ctx in contexts.items():
            if ctx.decision_date > event["date"]:
                continue
            for kind in ("cleared_volume", "open_interest"):
                source_date, aligned = store.latest_common(pair, [kind], ctx)
                if source_date is None:
                    invalid[day].append(f"{pair}: no aligned {kind}")
                timing_inputs.append(dict(date=day, decision_date=ctx.decision_date,
                    decision_cutoff=ctx.cutoff_utc, commodity=event["commodity"],
                    outgoing_contract=pair[0], incoming_contract=pair[1],
                    baseline_event_date=event["date"], statistic=kind, source_date=source_date,
                    outgoing_value=aligned[(pair[0], kind)].observation.statistic_value if aligned else None,
                    incoming_value=aligned[(pair[1], kind)].observation.statistic_value if aligned else None,
                    observation_ids=[aligned[(s, kind)].observation.observation_id for s in pair] if aligned else [],
                    publication_timestamps=[aligned[(s, kind)].observation.publication_timestamp for s in pair] if aligned else []))
    baseline_scope = {(d, primary[(d, root)]) for d in inputs.dates for root in ROOTS}
    destination_scope = {(d, r["contract"]) for d, ctx in contexts.items() for r in inputs.destination_scope
                         if r["first"] <= ctx.decision_date <= r["last"]}
    timing_scope = set(baseline_scope)
    for event in path.roll_events:
        # Conservative superset of every permitted first-held search window;
        # do not evaluate crossovers or choose a timing method here.
        for day, ctx in contexts.items():
            if ctx.decision_date <= event["date"]:
                timing_scope.update((day, s) for s in (event["outgoing_contract"], event["baseline_destination"]))
    preflight_scope = destination_scope | timing_scope | baseline_scope
    if any(key not in lookup or not lookup[key]["decision_valid"] for key in preflight_scope):
        raise ContractError("Preflight requires complete publication-safe sizing inputs")
    weights = config["target_weights"]
    def check(scope_name, keys, aum):
        binding = 0
        for key in sorted(keys):
            r = lookup[key]
            size = target_and_cap(aum, weights[r["commodity"]], r["settlement"],
                                  contracts[r["contract"]].dollars_per_price_unit, r["adv20"],
                                  config["capacity_participation_rate"])
            binding += size["capacity_binding"]
        return dict(scope=scope_name, fixed_aum=aum, checked_rows=len(keys), binding_rows=binding)
    selection_aum, selection_checks = choose_reference(config["selection_roll_reference_aum_usd"],
        config["selection_roll_fallback_reference_aum_usd"], lambda a: check("destination_timing_envelope", preflight_scope, a))
    rounding_aum, rounding_checks = choose_reference(config["rounding_reference_aum_usd"],
        config["rounding_fallback_reference_aum_usd"], lambda a: check("rounding_baseline_targets", baseline_scope, a))
    capacity_aum, capacity_checks = choose_reference(config["capacity_reference_aum_usd"],
        config["capacity_fallback_reference_aum_usd"], lambda a: check("capacity_baseline_targets", baseline_scope, a), True)
    references = dict(destination=selection_aum, timing=selection_aum, capacity=capacity_aum, rounding=rounding_aum)
    aums = sorted(set(config["aum_grid_usd"]) | set(references.values()))
    benchmark_rows, baseline_rows, target_rows, daily_rows = [], [], [], []
    fallback_records = []
    accounting_valid = defaultdict(lambda: True)
    curves = defaultdict(dict)
    for r in curve:
        if r["eligible"]:
            curves[(r["date"], r["commodity"])][r["contract"]] = r["days_to_anchor"]
    for aum in aums:
        holdings = []
        for day, ctx in contexts.items():
            for root in ROOTS:
                interpolation = constant_maturity_weights(curves[(ctx.decision_date, root)], config["benchmark_target_days"])
                for s, fraction in interpolation.weights.items():
                    r = lookup.get((day, s))
                    if r is None or not r["decision_valid"]:
                        invalid[day].append(f"{s}: benchmark sizing input unavailable");continue
                    target = Decimal(aum) * weights[root] * fraction
                    q = target / (r["settlement"] * contracts[s].dollars_per_price_unit)
                    holdings.append(Holding(day, "benchmark", root, s, ctx.cutoff_utc, ctx.cutoff_utc,
                                            r["settlement_published_at"], benchmark_fractional_contracts=q))
                    benchmark_rows.append(dict(date=day, decision_date=ctx.decision_date, commodity=root,
                        contract=s, fixed_aum=aum, target_weight=weights[root], interpolation_weight=fraction,
                        target_notional=target, fractional_contracts=q, benchmark_fallback=interpolation.benchmark_fallback,
                        decision_cutoff=ctx.cutoff_utc, inputs_as_of=r["settlement_published_at"], price=r["settlement"]))
                if interpolation.benchmark_fallback and aum == aums[0]:
                    fallback_records.append(dict(date=day, decision_date=ctx.decision_date, commodity=root,
                                                 contracts=sorted(interpolation.weights)))
        sized, targets = holdings_from_contract_path(path.positions, decision_inputs, contracts, calendar, config, aum)
        holdings.extend(sized)
        baseline_rows.extend(dict(h.to_record(), fixed_aum=aum) for h in sized)
        target_rows.extend(targets)
        book = HoldingsBook(holdings, calendar, contracts)
        validity = {(d, strategy): not invalid[d] for d in inputs.dates for strategy in ("benchmark", "baseline")}
        daily = portfolio_pnl(book, store, inputs.dates, ("benchmark", "baseline"), aum,
                              inputs.provenance["snapshot_end_exclusive"], validity)
        for r in daily:
            accounting_valid[r.date] &= r.common_valid
            daily_rows.append(dict(date=r.date, fixed_aum=aum, common_valid_date=r.common_valid,
                benchmark_pnl=r.pnl["benchmark"] if r.pnl else None,
                baseline_pnl=r.pnl["baseline"] if r.pnl else None,
                benchmark_daily_return=r.returns["benchmark"] if r.returns else None,
                baseline_daily_return=r.returns["baseline"] if r.returns else None,
                invalid_reasons=list(r.invalid_reasons)))
    mask = [not invalid[d] and accounting_valid[d] for d in inputs.dates]
    coverage = coverage_gate(mask, len(inputs.dates), config)
    if not coverage["passed"]:
        raise ContractError(f"Coverage gate failed: {coverage['common_valid_dates']}/{len(inputs.dates)}; no outcome-based sample search")
    masks = [dict(date=d, strand=strand, fixed_aum=references[strand], common_valid_date=ok,
                  invalid_reasons=sorted(set(invalid[d]))) for strand in references for d, ok in zip(inputs.dates, mask)]
    tables = dict(contracts=inputs.catalogue_rows, eligible_curve=curve, roll_events=list(path.roll_events),
                  baseline_contract_path=list(path.positions), decision_inputs=decision_inputs, timing_inputs=timing_inputs,
                  benchmark_holdings=benchmark_rows, baseline_holdings=baseline_rows,
                  post_capacity_targets=target_rows, contract_returns=endpoints,
                  baseline_daily=daily_rows, common_masks=masks)
    preflight = dict(references=references, core_aum_grid=config["aum_grid_usd"],
                     checks=selection_checks + rounding_checks + capacity_checks,
                     selection_basis="Only binding/non-binding capacity; no return or RMSE inspection",
                     destination_scope_basis="Frozen accepted contract-reach inventory",
                     timing_scope_basis="Conservative pair superset; no crossover signals evaluated")
    if not capacity_checks[-1]["binding_rows"]:
        preflight["capacity_limitation"] = "Still non-binding at the declared fallback; participation unchanged"
    return tables, dict(project_spec_version="8.0", coverage=coverage, preflight=preflight,
                        frozen_dates=[d for d, ok in zip(inputs.dates, mask) if ok],
                        calendar_id=calendar.calendar_id, maturity_calendar_id=calendar.maturity_calendar_id,
                        catalogue_id=catalogue.catalogue_id, curve_complete=certificate["complete_relative_to_catalogue"],
                        curve_certificate=dict(complete_relative_to_catalogue=True, contracts=len(contracts),
                                               decision_dates=len(decision_dates), date_root_groups=len(certificate["checks"])),
                        benchmark_fallbacks=fallback_records, metric_comparisons_run=False)


def save_inputs(inputs, directory):
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    dump(directory / "catalogue.json", inputs.catalogue_rows)
    dump(directory / "calendar.json", dict(sessions=inputs.calendar_rows, maturity_dates=inputs.maturity_dates))
    dump(directory / "scope.json", dict(dates=inputs.dates, statistics_contracts=inputs.statistics_contracts,
                                        destination_scope=inputs.destination_scope, provenance=inputs.provenance))
    payload = json.loads(serialize_observations(
        sorted(inputs.observations.consumed.values(), key=lambda o: o.observation_id),
        {s: c.identity for s, c in inputs.catalogue.contracts.items()}, inputs.calendar))
    write_table(directory / "consumed_observations.parquet", payload["observations"])
    dump(directory / "hashes.json", {p.name: sha(p) for p in sorted(directory.iterdir()) if p.name != "hashes.json"})


def load_inputs(directory, config):
    directory = Path(directory)
    hashes = json.loads((directory / "hashes.json").read_text())
    for file, digest in hashes.items():
        if Path(file).name != file or sha(directory / file) != digest:
            raise ContractError("Frozen handoff input hash mismatch")
    scope = json.loads((directory / "scope.json").read_text())
    rows = json.loads((directory / "catalogue.json").read_text())
    catalogue = validate_catalogue(rows)
    c = json.loads((directory / "calendar.json").read_text())
    calendar = SessionCalendar((TradingSession(**r) for r in c["sessions"]), c["maturity_dates"])
    envelope = dict(format="section0_observation_history_v1", project_spec_version="8.0",
                    calendar_id=calendar.calendar_id, observations=read_table(directory / "consumed_observations.parquet"))
    observations = deserialize_observations(json.dumps(envelope),
        {s: c.identity for s, c in catalogue.contracts.items()}, calendar)
    store = FrozenStore(observations, calendar, scope["dates"], config["max_liquidity_source_age_sessions"])
    return Inputs(rows, c["sessions"], c["maturity_dates"], scope["dates"], scope["statistics_contracts"],
                  scope["destination_scope"], scope["provenance"], store)


def export(inputs, config, directory, freeze_inputs=False):
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise ContractError("Output directory must be empty; do not overwrite a frozen handoff")
    tables, metadata = build(inputs, config)
    directory.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        write_table(directory / (name + ".parquet"), rows)
    dump(directory / "freeze.json", metadata)
    dump(directory / "provenance.json", inputs.provenance)
    with (directory / "tracking_coverage_report.csv").open("w", newline="") as stream:
        fields = ("strand", "fixed_aum", "candidate_dates", "common_valid_dates", "survival", "passed", "first_return", "last_return")
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n"); writer.writeheader()
        for strand, aum in metadata["preflight"]["references"].items():
            writer.writerow(dict(strand=strand, fixed_aum=aum, **plain(metadata["coverage"]),
                                 first_return=inputs.dates[0], last_return=inputs.dates[-1]))
    if freeze_inputs:
        save_inputs(inputs, directory / "inputs")
    dump(directory / "output_hashes.json", {p.name: sha(p) for p in sorted(directory.iterdir()) if p.is_file()})
    return metadata
