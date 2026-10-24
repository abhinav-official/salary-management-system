"""Full-population salary analytics, with current bands in one WAL snapshot."""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from app.database import read_transaction
from app.employees import DirectoryQuery
from app.exact_statistics import compa_bucket, percentage, salary_summary
from app.models import COUNTRY_CURRENCIES, LEVELS
from app.normalization import normalized_key


@dataclass(frozen=True)
class PopulationFilters:
    country: str | None = None
    department: str | None = None
    level: str | None = None

    def __post_init__(self):
        for field in ("country", "department", "level"):
            value = getattr(self, field)
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError("Analytics filters must be text")
                object.__setattr__(self, field, value.strip() or None)
        DirectoryQuery(country=self.country, department=self.department, level=self.level)

    def where(self):
        # Share directory filter semantics, without its search, sort or pagination.
        return DirectoryQuery(country=self.country, department=self.department, level=self.level).where()

    def as_dict(self):
        return {"country": self.country, "department": normalized_key(self.department or "") or None,
                "level": self.level}


def read_population(connection, filters: PopulationFilters):
    where, values = filters.where()
    return connection.execute("""SELECT e.country, e.department, e.department_key, e.level,
        e.salary_minor, b.midpoint_minor FROM employees e LEFT JOIN salary_bands b
        ON b.country=e.country AND b.level=e.level""" + where, values).fetchall()


def summaries(path: Path, filters: PopulationFilters):
    with read_transaction(path) as connection:
        rows = read_population(connection, filters)
        # Catalog labels and the population come from the same snapshot. Including
        # known departments across selected countries makes empty groups explicit.
        departments = {row["department_key"]: row["label"] for row in connection.execute(
            "SELECT department_key, min(department COLLATE BINARY) AS label FROM employees GROUP BY department_key")}
    if filters.department:
        key = normalized_key(filters.department)
        departments = {key: departments.get(key, filters.department.strip())}
    countries = [filters.country] if filters.country else sorted(COUNTRY_CURRENCIES)
    levels = [filters.level] if filters.level else LEVELS
    by_country, by_department, by_level = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in rows:
        salary = row["salary_minor"]
        by_country[row["country"]].append(salary)
        by_department[row["country"], row["department_key"]].append(salary)
        by_level[row["country"], row["level"]].append(salary)

    def group(country, values, **identity):
        return {"country": country, "currency": COUNTRY_CURRENCIES[country], **identity, **salary_summary(values)}

    return {"filters": filters.as_dict(), "total_count": len(rows),
            "countries": [group(country, by_country[country]) for country in countries],
            "departments": [group(country, by_department[country, key], department_key=key, department=departments[key])
                            for country in countries for key in sorted(departments)],
            "levels": [group(country, by_level[country, level], level=level) for country in countries for level in levels]}


BUCKETS = (("below_80", "Below 80%"), ("80_to_120", "80 to 120%"), ("above_120", "Above 120%"))


def compa_ratios(path: Path, filters: PopulationFilters):
    with read_transaction(path) as connection:
        rows = read_population(connection, filters)
    counts = dict.fromkeys((key for key, _ in BUCKETS), 0)
    excluded = 0
    for row in rows:
        if row["midpoint_minor"] is None:
            excluded += 1
        else:
            counts[compa_bucket(row["salary_minor"], row["midpoint_minor"])] += 1
    eligible = len(rows) - excluded
    return {"filters": filters.as_dict(), "total_count": len(rows), "eligible_count": eligible,
            "excluded_unbanded_count": excluded, "state": "available" if eligible else "no_eligible_data",
            "buckets": [{"key": key, "label": label, "count": counts[key],
                         "percentage": percentage(counts[key], eligible) if eligible else None}
                        for key, label in BUCKETS]}
