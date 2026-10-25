"""Offline validation of standard ZC/ZS/ZW quotation units, not portfolio P&L."""
from collections.abc import Mapping
from decimal import Decimal

from src.validation.schemas import ContractError, ROOTS, decimal_value


def exact_decimal_value(value, name="value"):
    """Production amounts cannot recover precision already lost in binary floats."""
    if type(value) not in (str, int, Decimal):
        raise ContractError(f"{name} requires Decimal, integer or decimal string; floats are not accepted")
    return decimal_value(value, name)


def validate_contract_value(record):
    """Return a copy with verified Decimal terms; retain vendor evidence unchanged.

    ``min_price_increment_amount`` is source evidence, never the authority for
    the dollar tick. The caller must separately retain the CME source reference.
    """
    if not isinstance(record, Mapping):
        raise ContractError("Contract-value record must be a mapping")
    if record.get("root") not in ROOTS:
        raise ContractError("Contract-value validation requires ZC, ZS or ZW")
    if record.get("unit_of_measure") != "BU" or record.get("value_unit") != "cents_per_bushel":
        raise ContractError("Standard grain contracts require BU and cents_per_bushel")
    expected = {"unit_of_measure_qty": Decimal("5000"),
                "derived_price_scale": Decimal("0.01"),
                "min_price_increment": Decimal("0.25"),
                "tick_value": Decimal("12.50"),
                "dollars_per_price_unit": Decimal("50")}
    result = dict(record)
    for name, value in expected.items():
        result[name] = exact_decimal_value(record.get(name), name)
        if result[name] != value:
            raise ContractError(f"Unverified standard grain contract term: {name}")
    if (result["unit_of_measure_qty"] * result["derived_price_scale"]
            != result["dollars_per_price_unit"]
            or result["min_price_increment"] * result["dollars_per_price_unit"]
            != result["tick_value"]):
        raise ContractError("Contract-value terms do not reconcile")
    return result


def quote_units_to_usd(price_units, record):
    """Convert a quoted price or signed price move to dollars for ONE contract."""
    terms = validate_contract_value(record)
    return exact_decimal_value(price_units, "price_units") * terms["dollars_per_price_unit"]
