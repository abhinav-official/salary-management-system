"""One text-normalization contract for domain records and SQLite constraints."""


def trim_text(value: str) -> str:
    return value.strip()


def normalized_key(value: str) -> str:
    return trim_text(value).casefold()


def required_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not trim_text(value) or "\x00" in value:
        raise ValueError(f"{field} must be nonempty text without null characters")
    return trim_text(value)
