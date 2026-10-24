"""Validated records for API-owned persistence. HTTP operations follow later."""

from dataclasses import dataclass
from types import MappingProxyType

from email_validator import EmailNotValidError, validate_email

from app.money import MAX_MINOR, validate_minor
from app.normalization import normalized_key, required_text

COUNTRY_CURRENCIES = MappingProxyType({
    "India": "INR", "US": "USD", "UK": "GBP", "Germany": "EUR",
    "Singapore": "SGD", "UAE": "AED",
})
LEVELS = tuple(f"L{number}" for number in range(1, 9))


def validate_classification(country: str, level: str):
    if country not in COUNTRY_CURRENCIES:
        raise ValueError("Unsupported country")
    if level not in LEVELS:
        raise ValueError("Level must be L1 through L8")


def validate_revision(revision: int):
    if type(revision) is not int or not 1 <= revision <= MAX_MINOR:
        raise ValueError("Revision must be a positive supported integer")


@dataclass(frozen=True)
class Employee:
    employee_id: str
    name: str
    email: str
    department: str
    level: str
    country: str
    salary_minor: int
    revision: int = 1

    def __post_init__(self):
        for field in ("employee_id", "name", "email", "department", "level", "country"):
            object.__setattr__(self, field, required_text(getattr(self, field), field))
        try:
            validate_email(self.email, check_deliverability=False)
        except EmailNotValidError as error:
            raise ValueError("Invalid email address") from error
        validate_classification(self.country, self.level)
        validate_minor(self.salary_minor)
        validate_revision(self.revision)

    @property
    def employee_id_key(self) -> str:
        return normalized_key(self.employee_id)

    @property
    def email_key(self) -> str:
        return normalized_key(self.email)

    @property
    def department_key(self) -> str:
        return normalized_key(self.department)

    @property
    def currency(self) -> str:
        return COUNTRY_CURRENCIES[self.country]


@dataclass(frozen=True)
class SalaryBand:
    country: str
    level: str
    minimum_minor: int
    midpoint_minor: int
    maximum_minor: int
    revision: int = 1

    def __post_init__(self):
        for field in ("country", "level"):
            object.__setattr__(self, field, required_text(getattr(self, field), field))
        validate_classification(self.country, self.level)
        for value in (self.minimum_minor, self.midpoint_minor, self.maximum_minor):
            validate_minor(value)
        if not 0 <= self.minimum_minor <= self.midpoint_minor <= self.maximum_minor:
            raise ValueError("Band amounts must satisfy minimum <= midpoint <= maximum")
        if self.midpoint_minor == 0:
            raise ValueError("Band midpoint must be positive")
        validate_revision(self.revision)

    @property
    def currency(self) -> str:
        return COUNTRY_CURRENCIES[self.country]
