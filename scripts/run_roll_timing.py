"""Fixture-only roll-timing demonstration (Issue #23). Never downloads market data.

Applies the four timing rules to the shared Phase A baseline pair. The crossover
search window is the proposed causal boundary (pending team sign-off): from the
first supplied decision session up to, but excluding, the baseline -5 session.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.fixtures import load_fixture
from src.portfolio.baseline import build_baseline_path
from src.rolling.rules import (RULES, apply_roll_timing_rule, build_baseline_roll_pairs,
                               build_roll_timing_portfolio)
from src.validation.schemas import load_project_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", action="store_true", help="Run only the committed synthetic example")
    args = parser.parse_args()
    if not args.fixtures:
        parser.error("Real-data timing is Phase B; supply --fixtures for the offline demonstration")
    config = load_project_config(ROOT / "config/project.yaml")
    f = load_fixture(ROOT / "data/fixtures/phase_a.json", config["max_liquidity_source_age_sessions"])
    case = f.raw["baseline_path_case"]
    path = build_baseline_path(case["eligible_curve"], case["decision_dates"], case["commodities"],
                               f.calendar, f.catalogue)
    pairs = build_baseline_roll_pairs(path)
    schedules = {}
    for rule in RULES:
        decisions = [apply_roll_timing_rule(p, rule, f.calendar, f.catalogue, f.observations,
                                            case["decision_dates"][0], p.baseline_event_date)
                     for p in pairs]
        rows = build_roll_timing_portfolio(path, decisions, f.calendar, f.catalogue)
        schedules[rule] = {"decisions": [{k: d[k] for k in ("roll_decision_date", "first_incoming_interval_end",
                                                            "sessions_before_anchor", "fallback_flag", "trigger")}
                                         for d in decisions],
                           "contract_path": [(r["decision_date"], r["date"], r["contract"]) for r in rows]}
    print(json.dumps({"mode": "synthetic_fixture_only", "preferred_method_selected": False,
                      "pairs": [p.__dict__ for p in pairs], "schedules": schedules},
                     default=str, indent=2))


if __name__ == "__main__":
    main()
