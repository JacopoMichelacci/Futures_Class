"""Independent hand arithmetic for the three standard grain contracts."""
from decimal import Decimal
import unittest

from src.contracts.value import quote_units_to_usd, validate_contract_value
from src.validation.schemas import ContractError, ROOTS


def terms(root):
    return dict(root=root, unit_of_measure="BU", value_unit="cents_per_bushel",
                unit_of_measure_qty="5000", derived_price_scale="0.01",
                min_price_increment="0.25", tick_value="12.50", dollars_per_price_unit="50",
                min_price_increment_amount="1.0")


class ContractValueTests(unittest.TestCase):
    def test_tick_and_full_unit_moves_for_all_three_roots(self):
        for root in ROOTS:
            with self.subTest(root=root):
                self.assertEqual(quote_units_to_usd("0.25", terms(root)), Decimal("12.50"))
                self.assertEqual(quote_units_to_usd("1.00", terms(root)), Decimal("50"))
                self.assertEqual(quote_units_to_usd("-0.25", terms(root)), Decimal("-12.50"))

    def test_synthetic_contract_notionals(self):
        for root, price, dollars in (("ZC", "400", "20000"), ("ZS", "1000", "50000"),
                                     ("ZW", "600", "30000")):
            self.assertEqual(quote_units_to_usd(price, terms(root)), Decimal(dollars))

    def test_vendor_tick_amount_is_retained_but_not_trusted(self):
        original = terms("ZC")
        validated = validate_contract_value(original)
        self.assertEqual(validated["min_price_increment_amount"], "1.0")
        self.assertEqual(validated["tick_value"], Decimal("12.50"))
        self.assertEqual(original["tick_value"], "12.50")

    def test_wrong_units_or_specifications_fail(self):
        for key, value in (("root", "ES"), ("unit_of_measure", "LB"),
                           ("value_unit", "dollars_per_bushel"), ("unit_of_measure_qty", "1000"),
                           ("min_price_increment", "0.125"), ("derived_price_scale", "1"),
                           ("tick_value", "1"), ("dollars_per_price_unit", "5000")):
            with self.subTest(key=key), self.assertRaises(ContractError):
                validate_contract_value(dict(terms("ZC"), **{key: value}))

    def test_missing_nonfinite_and_boolean_terms_fail(self):
        for value in (None, "NaN", "Infinity", True):
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate_contract_value(dict(terms("ZS"), tick_value=value))

    def test_malformed_value_record_raises_contract_error(self):
        for record in (None, [], "not a record", 42):
            with self.subTest(record=record), self.assertRaises(ContractError):
                validate_contract_value(record)

    def test_binary_float_amounts_and_terms_are_rejected(self):
        with self.assertRaisesRegex(ContractError, "floats"):
            quote_units_to_usd(0.1 + 0.2, terms("ZC"))
        for field, value in (("unit_of_measure_qty", 5000.0), ("min_price_increment", 0.25),
                             ("dollars_per_price_unit", 50.0), ("tick_value", 12.5)):
            with self.subTest(field=field), self.assertRaisesRegex(ContractError, "floats"):
                validate_contract_value(dict(terms("ZC"), **{field: value}))

    def test_exact_input_types_are_accepted(self):
        for amount in (1, "1.00", Decimal("1.00")):
            self.assertEqual(quote_units_to_usd(amount, terms("ZW")), Decimal("50"))


if __name__ == "__main__":
    unittest.main()
