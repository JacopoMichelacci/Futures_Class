"""Pure benchmark interpolation for fixtures; no historical portfolio pipeline."""
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from src.validation.schemas import ContractError


@dataclass(frozen=True)
class Interpolation:
    weights: dict[str, Decimal]
    benchmark_fallback: bool


def constant_maturity_weights(eligible_days: Mapping[str, int], target_days: int = 60) -> Interpolation:
    """Split notional between bracketing anchors; flag the nearest-contract fallback.

    Inputs must already be filtered for eligibility. This is not a destination rule.
    Equal-distance fallback ties choose the nearer maturity, then contract ID.
    """
    if not eligible_days or type(target_days) is not int or target_days <= 0:
        raise ContractError("Eligible contracts and a positive target horizon are required")
    if any(type(d) is not int or d <= 0 for d in eligible_days.values()):
        raise ContractError("Eligible maturity distances must be positive session counts")
    ordered = sorted(eligible_days, key=lambda c: (eligible_days[c], c))
    exact = [c for c in ordered if eligible_days[c] == target_days]
    if exact:
        return Interpolation({exact[0]: Decimal(1)}, False)
    near = [c for c in ordered if eligible_days[c] < target_days]
    far = [c for c in ordered if eligible_days[c] > target_days]
    if not near or not far:
        chosen = min(ordered, key=lambda c: (abs(eligible_days[c] - target_days), eligible_days[c], c))
        return Interpolation({chosen: Decimal(1)}, True)
    left, right = near[-1], far[0]
    far_weight = Decimal(target_days - eligible_days[left]) / Decimal(eligible_days[right] - eligible_days[left])
    return Interpolation({left: Decimal(1) - far_weight, right: far_weight}, False)
