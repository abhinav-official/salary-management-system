"""Exact rational statistics; Decimal rounding occurs only at presentation."""

from decimal import Context, Decimal, FloatOperation, ROUND_HALF_UP, localcontext
from fractions import Fraction
from typing import Iterable

from app.money import validate_minor


def format_fraction(numerator: int, denominator: int) -> str:
    """Display an integer fraction to two decimals, independent of ambient context.

    The digit budget exceeds numerator + denominator digits: division error is
    smaller than the distance from a non-tie rational to a half-cent boundary.
    Exact ties terminate within this budget, so quantize cannot double-round.
    """
    if type(numerator) is not int or type(denominator) is not int or denominator <= 0:
        raise ValueError("An exact fraction requires integer numerator and positive integer denominator")
    context = Context(prec=len(str(abs(numerator))) + len(str(denominator)) + 10, rounding=ROUND_HALF_UP)
    context.traps[FloatOperation] = True
    with localcontext(context):
        amount = (Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.01"))
        return format(amount, ".2f")


def monetary_statistic(value: Fraction) -> dict:
    return {"minor_numerator": str(value.numerator), "minor_denominator": str(value.denominator),
            "amount": format_fraction(value.numerator, value.denominator * 100)}


def salary_summary(salaries: Iterable[int]) -> dict:
    values = sorted(validate_minor(value) for value in salaries)
    count = len(values)
    if not count:
        return {"count": 0, "state": "no_data", **dict.fromkeys(("average", "median", "minimum", "maximum", "spread"))}
    total = sum(values)  # Python integers do not overflow or promote to binary float.
    middle = count // 2
    median = Fraction(values[middle]) if count % 2 else Fraction(values[middle - 1] + values[middle], 2)
    statistics = {"average": Fraction(total, count), "median": median,
                  "minimum": Fraction(values[0]), "maximum": Fraction(values[-1]),
                  "spread": Fraction(values[-1] - values[0])}
    return {"count": count, "state": "available",
            **{name: monetary_statistic(value) for name, value in statistics.items()}}


def compa_bucket(salary_minor: int, midpoint_minor: int) -> str:
    validate_minor(salary_minor)
    validate_minor(midpoint_minor)
    if midpoint_minor == 0:
        raise ValueError("Band midpoint must be positive")
    scaled_salary = salary_minor * 100
    if scaled_salary < midpoint_minor * 80:
        return "below_80"
    if scaled_salary > midpoint_minor * 120:
        return "above_120"
    return "80_to_120"


def percentage(numerator: int, denominator: int) -> str:
    if type(numerator) is not int or type(denominator) is not int or denominator <= 0:
        raise ValueError("A percentage requires integer numerator and positive integer denominator")
    return format_fraction(numerator * 100, denominator)
