import unittest
from decimal import Decimal, FloatOperation, ROUND_DOWN, localcontext

from app.money import MAX_MINOR, MoneyError, format_money, parse_money, validate_minor


class MoneyTests(unittest.TestCase):
    def test_half_up_rounding_to_integer_minor_units(self):
        cases = {
            "123.455": 12346, "123.454": 12345,
            "0.005": 1, "0.0049": 0, "0.015": 2, "0.045": 5,
            "1.999": 200, "0": 0, " 001.20 ": 120,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                actual = parse_money(text)
                self.assertIs(type(actual), int)
                self.assertEqual(actual, expected)

    def test_negative_inputs_are_rejected_before_rounding(self):
        for value in ("-0.001", "-0.00", "-1", Decimal("-0.000000001")):
            with self.subTest(value=value), self.assertRaises(MoneyError):
                parse_money(value)

    def test_float_boolean_and_implicit_numeric_inputs_are_rejected(self):
        for value in (123.455, 100.0, float("nan"), float("inf"), True, 123, None, b"1.25"):
            with self.subTest(value=value), self.assertRaises(MoneyError):
                parse_money(value)

    def test_nonfinite_and_ambiguous_text_is_rejected(self):
        for value in ("NaN", "Infinity", "1,000.00", "INR 10", "1e2", ".5", "1.",
                      "", " ", "1_000", "１２.５０", "1 2", Decimal("NaN"),
                      Decimal("sNaN"), Decimal("Infinity")):
            with self.subTest(value=value), self.assertRaises(MoneyError):
                parse_money(value)

    def test_exact_decimal_numeric_cell_values(self):
        self.assertEqual(parse_money(Decimal("1.23455E+2")), 12346)
        self.assertEqual(parse_money(Decimal("0.01")), 1)
        self.assertEqual(parse_money(Decimal("1E-1000")), 0)

    def test_supported_range_and_rounding_overflow(self):
        self.assertEqual(parse_money("92233720368547758.07"), MAX_MINOR)
        self.assertEqual(parse_money("92233720368547758.074"), MAX_MINOR)
        for value in ("92233720368547758.075", "92233720368547758.08",
                      Decimal("1E+999999")):
            with self.subTest(value=value), self.assertRaises(MoneyError):
                parse_money(value)

    def test_money_is_independent_of_callers_decimal_context(self):
        with localcontext() as context:
            context.prec = 4
            context.rounding = ROUND_DOWN
            context.traps[FloatOperation] = True
            self.assertEqual(parse_money("123.455"), 12346)
            self.assertEqual(parse_money("92233720368547758.07"), MAX_MINOR)
            self.assertEqual(format_money(MAX_MINOR), "92233720368547758.07")

    def test_formatting_and_cent_preserving_round_trips(self):
        for minor, text in ((0, "0.00"), (1, "0.01"), (10001, "100.01"),
                            (12346, "123.46"), (MAX_MINOR, "92233720368547758.07")):
            with self.subTest(minor=minor):
                self.assertEqual(format_money(minor), text)
                self.assertEqual(parse_money(format_money(minor)), minor)

    def test_minor_unit_interfaces_reject_coercion_and_out_of_range_values(self):
        for value in (-1, MAX_MINOR + 1, True, 100.0, "100", Decimal("100"), None):
            for operation in (validate_minor, format_money):
                with self.subTest(operation=operation.__name__, value=value), self.assertRaises(MoneyError):
                    operation(value)
