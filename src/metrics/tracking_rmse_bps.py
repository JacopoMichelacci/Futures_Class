"""Daily realised-return tracking RMSE. No annualisation or method selection."""
from decimal import Decimal

from src.validation.schemas import ContractError


def tracking_rmse_bps(daily, benchmark="benchmark") -> dict[str, Decimal]:
    rows = tuple(daily)
    if not rows or len({r.date for r in rows}) != len(rows):
        raise ContractError("Distinct daily comparison rows required")
    if len({r.fixed_aum for r in rows}) != 1:
        raise ContractError("The fixed AUM denominator cannot change through time")
    if any(not r.fixed_aum.is_finite() or r.fixed_aum <= 0 for r in rows):
        raise ContractError("The fixed AUM denominator must be finite and positive")
    valid = [r for r in rows if r.common_valid]
    if not valid:
        raise ContractError("No common valid evaluation dates; RMSE is unavailable, not zero")
    names = set(valid[0].returns or {})
    if benchmark not in names or len(names) < 2:
        raise ContractError("Benchmark and implementation returns required")
    for r in valid:
        if r.returns is None or set(r.returns) != names or any(not x.is_finite() for x in r.returns.values()):
            raise ContractError("Methods must use identical valid dates and finite returns")
    return {name: (sum((r.returns[name] - r.returns[benchmark]) ** 2 for r in valid) / len(valid)).sqrt() * Decimal(10000)
            for name in sorted(names - {benchmark})}
