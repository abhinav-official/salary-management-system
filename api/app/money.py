"""Exact monetary boundaries: decimal major-unit input, integer minor-unit storage."""

import re
from decimal import Context, Decimal, DecimalException, FloatOperation, ROUND_HALF_UP, localcontext

MAX_MINOR = 2**63 - 1
_CENT = Decimal("0.01")
_EXCLUSIVE_MAJOR_LIMIT = Decimal("92233720368547758.08")
_DECIMAL_TEXT = re.compile(r"-?[0-9]+(?:\.[0-9]+)?\Z")


class MoneyError(ValueError):
    """An amount is invalid or outside SQLite's signed integer range."""


def validate_minor(value: int) -> int:
    # bool is an int subclass, but is not a monetary input.
    if type(value) is not int or not 0 <= value <= MAX_MINOR:
        raise MoneyError(f"Minor units must be an integer between 0 and {MAX_MINOR}")
    return value


def parse_money(value: str | Decimal) -> int:
    """Round exact major units half-up to cents, then return integer minor units.

    Text accepts plain decimal notation without separators, currency symbols,
    or exponents. Exact Decimal inputs also support numeric Excel XML values.
    Binary floats and implicit numeric coercion are deliberately rejected.
    """
    if isinstance(value, str):
        text = value.strip()
        if not _DECIMAL_TEXT.fullmatch(text):
            raise MoneyError("Enter a plain decimal amount without symbols or separators")
        amount = Decimal(text)
    elif isinstance(value, Decimal):
        amount = value
    else:
        raise MoneyError("Money must be decimal text or an exact Decimal, never a float")
    if not amount.is_finite():
        raise MoneyError("Money must be finite")
    # Check the sign before quantization, including negative sub-cent amounts.
    if amount.is_signed():
        raise MoneyError("Money cannot be negative")
    if amount >= _EXCLUSIVE_MAJOR_LIMIT:
        raise MoneyError("Money exceeds the supported range")
    # The maximum result has 19 digits. A private context avoids callers changing
    # rounding/precision/traps and never requires converting through float.
    context = Context(prec=32, rounding=ROUND_HALF_UP)
    context.traps[FloatOperation] = True
    try:
        with localcontext(context):
            minor = int(amount.quantize(_CENT) * 100)
    except DecimalException as error:
        raise MoneyError("Money cannot be represented in the supported range") from error
    return validate_minor(minor)


def format_money(minor: int) -> str:
    """Return exact two-decimal major units; currency is supplied separately."""
    validate_minor(minor)
    return f"{minor // 100}.{minor % 100:02d}"
