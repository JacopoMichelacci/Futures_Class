"""Maturity distances on a supplied observed-session calendar."""
from src.validation.schemas import ContractError


def days_to_anchor(calendar, trading_date: str, anchor: str) -> int:
    if trading_date not in calendar.indices or anchor not in calendar.indices:
        raise ContractError("Session calendar must explicitly cover the date and anchor")
    return calendar.indices[anchor] - calendar.indices[trading_date]
