"""Hand-calculated rounding rules and shared-accounting integration tests."""
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
import itertools
import json
import subprocess
import sys
import unittest

from src.data.asof import ObservationStore
from src.data.fixtures import load_fixture
from src.metrics.portfolio_pnl import portfolio_pnl
from src.metrics.tracking_rmse_bps import tracking_rmse_bps
from src.portfolio.holdings import Holding, HoldingsBook
from src.rounding.rules import (ROUNDING_METHODS, RoundingTarget, build_rounded_portfolio,
                                round_away_from_zero, round_nearest,
                                round_residual_greedy, round_toward_zero)
from src.validation.schemas import ContractError

ROOT = Path(__file__).resolve().parents[2]


def target(root="ZC", contract="ZC_SYN_NEAR", rank=1, q="1.6", notional="100", cap=10):
    return RoundingTarget(root, contract, rank, q, notional, cap, "0.333333", "1000")


class RoundingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = load_fixture(ROOT / "data/fixtures/phase_a.json")

    def test_scalar_rules_match_hand_examples(self):
        for q, nearest, floor, ceiling in [("0", 0, 0, 0), ("0.1", 0, 0, 1),
                                          ("1", 1, 1, 1), ("1.49", 1, 1, 2),
                                          ("1.51", 2, 1, 2), ("2.9", 3, 2, 3)]:
            with self.subTest(q=q):
                self.assertEqual(round_nearest(q, 10), nearest)
                self.assertEqual(round_toward_zero(q, 10), floor)
                self.assertEqual(round_away_from_zero(q, 10), ceiling)

    def test_invalid_or_not_yet_capped_inputs_are_rejected(self):
        for rule in (round_nearest, round_toward_zero, round_away_from_zero):
            for q, cap in [("-1", 10), ("NaN", 10), ("Infinity", 10), ("bad", 10),
                           ("2.1", 2), (1, "1.5"), (0, -1), (0, "Infinity")]:
                with self.subTest(rule=rule.__name__, q=q, cap=cap), self.assertRaises(ContractError):
                    rule(q, cap)

    def test_nearest_exact_halves_round_up_regardless_of_integer_parity(self):
        for q, expected in [("0.5", 1), ("1.5", 2), ("2.5", 3), ("3.5", 4),
                            ("10000000000000000000000000000.5", 10000000000000000000000000001)]:
            with self.subTest(q=q):
                result = round_nearest(q, expected)
                self.assertIs(type(result), int)
                self.assertEqual(result, expected)
                self.assertEqual(round_toward_zero(q, expected), expected - 1)
                self.assertEqual(round_away_from_zero(q, expected), expected)

    def test_scalar_outputs_are_integers_within_capacity(self):
        for rule in (round_nearest, round_toward_zero, round_away_from_zero):
            for q, cap in [(0, 0), ("0.9", 1), ("1.99", 2), (2, 2)]:
                n = rule(q, cap)
                self.assertIs(type(n), int)
                self.assertTrue(0 <= n <= cap)

    def test_decimal_precision_is_not_reduced_to_binary_float(self):
        self.assertEqual(round_nearest("1.49999999999999999999999999999", 2), 1)
        self.assertEqual(round_nearest("1.50000000000000000000000000001", 2), 2)
        self.assertEqual(round_away_from_zero("0.00000000000000000000000000001", 1), 1)

    def test_greedy_hand_objective_and_dollar_weighted_priority(self):
        rows = [target(q="1.6", notional="100"),
                target("ZS", "ZS_SYN_NEAR", q="2.9", notional="10")]
        result = round_residual_greedy(rows)
        self.assertEqual(result.quantities, {"ZC_SYN_NEAR": 2, "ZS_SYN_NEAR": 3})
        self.assertEqual(result.initial_squared_error, 3681)
        self.assertEqual(result.final_squared_error, 1601)
        self.assertEqual([s.contract for s in result.steps], ["ZC_SYN_NEAR", "ZS_SYN_NEAR"])
        self.assertEqual([s.improvement for s in result.steps], [2000, 80])

    def test_greedy_exact_ties_use_root_then_earlier_maturity(self):
        rows = [target("ZW", "ZW_SYN_NEAR", q="1.75", notional="10"),
                target("ZC", "ZC_SYN_NEXT", rank=2, q="1.75", notional="10"),
                target("ZS", "ZS_SYN_NEAR", q="1.75", notional="10"),
                target("ZC", "ZC_SYN_NEAR", rank=1, q="1.75", notional="10")]
        expected = ["ZC_SYN_NEAR", "ZC_SYN_NEXT", "ZS_SYN_NEAR", "ZW_SYN_NEAR"]
        for order in itertools.permutations(rows):
            result = round_residual_greedy(order)
            self.assertEqual([s.contract for s in result.steps], expected)
            self.assertEqual([s.improvement for s in result.steps], [50] * 4)

    def test_greedy_does_not_add_for_zero_or_negative_improvement(self):
        for q in ("0", "1", "1.49", "1.5"):
            result = round_residual_greedy([target(q=q)])
            self.assertEqual(result.steps, ())
            self.assertEqual(result.initial_squared_error, result.final_squared_error)

    def test_greedy_never_exceeds_caps_including_zero(self):
        rows = [target(q=0, cap=0), target("ZS", "ZS_SYN_NEAR", q="1.9", cap=2)]
        self.assertEqual(round_residual_greedy(rows).quantities, {"ZC_SYN_NEAR": 0, "ZS_SYN_NEAR": 2})

    def test_every_greedy_step_strictly_improves_the_stated_objective(self):
        for a, b in itertools.product(("0", "0.2", "0.5", "0.8", "1.3", "1.7", "2"), repeat=2):
            rows = [target(q=a, cap=2), target("ZS", "ZS_SYN_NEAR", q=b, notional=37, cap=2)]
            result = round_residual_greedy(rows)
            n = {r.contract: round_toward_zero(r.post_capacity_fractional_contracts, 2) for r in rows}
            error = lambda: sum(((Fraction(r.post_capacity_fractional_contracts) - n[r.contract]) * Fraction(r.dollar_contract_notional)) ** 2 for r in rows)
            for step in result.steps:
                before = error()
                self.assertEqual(n[step.contract], step.quantity_before)
                n[step.contract] += 1
                self.assertLess(error(), before)
                self.assertEqual(before - error(), step.improvement)
            self.assertEqual(error(), result.final_squared_error)
            self.assertLessEqual(result.final_squared_error, result.initial_squared_error)

    def test_greedy_uses_post_capacity_quantity_not_uncapped_target_notional(self):
        row = replace(target(q="1.2"), target_notional=Decimal("999999"))
        self.assertEqual(round_residual_greedy([row]).quantities[row.contract], 1)

    def test_duplicate_contract_or_ambiguous_maturity_rank_is_rejected(self):
        row = target()
        for rows in ([], [row, row], [row, target(contract="ZC_SYN_NEXT", rank=1)]):
            with self.assertRaises(ContractError):
                round_residual_greedy(rows)

    def test_target_metadata_validation(self):
        for change in ({"commodity": "ES"}, {"contract": ""}, {"maturity_rank": True},
                       {"maturity_rank": 0}, {"dollar_contract_notional": 0},
                       {"target_weight": 2}, {"target_notional": -1}):
            with self.subTest(change=change), self.assertRaises(ContractError):
                replace(target(), **change)

    def test_greedy_output_cannot_be_mutated(self):
        result = round_residual_greedy([target()])
        with self.assertRaises(TypeError):
            result.quantities["ZC_SYN_NEAR"] = 99

    def build(self, method, rows=None, **overrides):
        kwargs = dict(date="2030-02-01", formed_at="2030-01-31T09:00:00Z",
                      inputs_as_of="2030-01-31T08:00:00Z", calendar=self.f.calendar,
                      catalogue=self.f.catalogue)
        kwargs.update(overrides)
        return build_rounded_portfolio(rows or [target()], method, **kwargs)

    def test_all_builders_keep_same_contracts_targets_caps_and_timing(self):
        rows = [target(q="1.6"), target("ZS", "ZS_SYN_NEAR", q="0.2"),
                target("ZW", "ZW_SYN_NEAR", q=0, cap=0)]
        for method in ROUNDING_METHODS:
            book = self.build(method, rows)
            self.assertEqual([r.contract for r in book.rows], [r.contract for r in rows])
            for holding, original in zip(book.rows, rows):
                self.assertEqual(holding.details["target_notional"], original.target_notional)
                self.assertEqual(holding.details["target_weight"], original.target_weight)
                self.assertEqual(holding.details["capacity_limit_contracts"], original.capacity_limit_contracts)
                self.assertEqual(holding.details["implemented_notional"], holding.quantity * original.dollar_contract_notional)
                self.assertEqual(holding.date, "2030-02-01")
                self.assertEqual(holding.decision_cutoff, "2030-01-31T18:15:00Z")
            self.assertEqual(book.rows[-1].quantity, 0)

    def test_builder_uses_shared_identity_and_provenance_guards(self):
        for kwargs in ({"formed_at": "2030-02-01T09:00:00Z"},
                       {"inputs_as_of": "2030-01-31T10:00:00Z"}):
            with self.assertRaises(ContractError):
                self.build("toward_zero", **kwargs)
        with self.assertRaises(ContractError):
            self.build("toward_zero", [target(root="ZS")])
        with self.assertRaises(ContractError):
            self.build("unknown")

    def test_half_up_portfolio_respects_caps_without_changing_greedy_stop_rule(self):
        rows = [target(q="2.5", cap=3), target("ZS", "ZS_SYN_NEAR", q="3.5", cap=4),
                target("ZW", "ZW_SYN_NEAR", q="0.5", cap=1)]
        expected = {"nearest": [3, 4, 1], "toward_zero": [2, 3, 0],
                    "away_from_zero": [3, 4, 1], "residual_greedy": [2, 3, 0]}
        for method in ROUNDING_METHODS:
            book = self.build(method, rows)
            self.assertEqual([r.quantity for r in book.rows], expected[method])
            self.assertTrue(all(r.quantity <= r.details["capacity_limit_contracts"] for r in book.rows))
        greedy = round_residual_greedy(rows)
        self.assertEqual(greedy.steps, ())
        self.assertEqual(greedy.final_squared_error, greedy.initial_squared_error)

    def test_generated_holdings_use_shared_pnl_and_rmse_without_redefinition(self):
        books = [self.build(method, [target(q="1.6")]) for method in ROUNDING_METHODS]
        reference = replace(books[0].rows[0], strategy="benchmark", implemented_contracts=None,
                            benchmark_fractional_contracts="1.6")
        book = HoldingsBook([reference] + [r for b in books for r in b.rows], self.f.calendar, self.f.catalogue)
        names = ["benchmark"] + [b.rows[0].strategy for b in books]
        daily = portfolio_pnl(book, self.f.observations, [reference.date], names, 1000000,
                              self.f.raw["snapshot_end_exclusive"])
        self.assertEqual(daily[0].pnl["benchmark"], 80)
        self.assertEqual(daily[0].pnl["rounding_toward_zero"], 50)
        self.assertEqual(daily[0].pnl["rounding_nearest"], 100)
        self.assertEqual(tracking_rmse_bps(daily), {"rounding_nearest": Decimal("0.2"),
                         "rounding_toward_zero": Decimal("0.3"), "rounding_away_from_zero": Decimal("0.2"),
                         "rounding_residual_greedy": Decimal("0.2")})
        missing = ObservationStore([r for r in self.f.observations.observations
                                   if not (r.identity.contract == "ZC_SYN_NEAR" and r.statistic_type == "settlement" and r.trading_date == reference.date)], self.f.calendar)
        invalid = portfolio_pnl(book, missing, [reference.date], names, 1000000,
                                self.f.raw["snapshot_end_exclusive"])
        self.assertFalse(invalid[0].common_valid)
        self.assertIsNone(invalid[0].returns)

    def test_fixture_demo_runs_all_four_rules_offline(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/run_person4.py"), "--fixtures"], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(set(output["quantities"]), set(ROUNDING_METHODS))
        self.assertEqual(output["nearest_tie_convention"], "half_up")
        self.assertFalse(output["empirical_analysis"])
        self.assertIsNone(output["preferred_method"])

    def test_demo_requires_explicit_fixture_mode(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/run_person4.py")], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--fixtures", result.stderr)


if __name__ == "__main__":
    unittest.main()
