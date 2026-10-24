"""Identity-based leave-one-out equity over full saved country/level peers."""

from collections import defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Iterable

from app.analytics import PopulationFilters
from app.database import read_transaction
from app.exact_statistics import monetary_statistic, percentage
from app.models import COUNTRY_CURRENCIES
from app.money import format_money, validate_minor

STATES = {
    "available": ("available_count", "Available"),
    "unbanded": ("excluded_unbanded_count", "Unbanded"),
    "insufficient_peers": ("insufficient_peers_count", "Insufficient peers"),
    "zero_peer_median": ("zero_peer_median_count", "Peer median is zero"),
}


class PeerGroup:
    """Sort once; map each unique identity to its position, including salary ties."""

    def __init__(self, members: Iterable[tuple[str, int]]):
        ordered = sorted((validate_minor(salary), identity) for identity, salary in members)
        if not ordered:
            raise ValueError("A peer group must contain at least one employee")
        self.salaries = tuple(salary for salary, _ in ordered)
        self.positions = {identity: position for position, (_, identity) in enumerate(ordered)}
        if len(self.positions) != len(ordered):
            raise ValueError("Peer identities must be unique")

    @property
    def peer_count(self):
        return len(self.salaries) - 1

    def median_without(self, identity: str) -> Fraction | None:
        removed = self.positions[identity]
        if not self.peer_count:
            return None
        middle = self.peer_count // 2

        def remaining(position):
            # Map a position in the remaining sequence back past the removed row.
            return self.salaries[position + (position >= removed)]

        if self.peer_count % 2:
            return Fraction(remaining(middle))
        return Fraction(remaining(middle - 1) + remaining(middle), 2)


def read_targets(connection, filters: PopulationFilters):
    where, parameters = filters.where()
    return connection.execute("""SELECT e.employee_id, e.employee_id_key, e.name, e.department,
        e.country, e.level, e.salary_minor,
        CASE WHEN b.country IS NULL THEN 'Unbanded' ELSE 'Band-matched' END AS band_status
        FROM employees e LEFT JOIN salary_bands b ON b.country=e.country AND b.level=e.level"""
        + where + " ORDER BY e.employee_id_key", parameters).fetchall()


def read_peer_groups(connection):
    # No department, search or page restriction: peers are the full saved population.
    members = defaultdict(list)
    for row in connection.execute("""SELECT e.employee_id_key, e.country, e.level, e.salary_minor
        FROM employees e JOIN salary_bands b ON b.country=e.country AND b.level=e.level"""):
        members[row["country"], row["level"]].append((row["employee_id_key"], row["salary_minor"]))
    return {key: PeerGroup(group) for key, group in members.items()}


def equity_item(target, groups):
    result = {field: target[field] for field in
              ("employee_id", "name", "department", "country", "level", "band_status")}
    result.update(currency=COUNTRY_CURRENCIES[target["country"]], salary_minor=str(target["salary_minor"]),
                  salary_amount=format_money(target["salary_minor"]), peer_count=0,
                  peer_median=None, percentage_difference=None)
    if target["band_status"] == "Unbanded":
        state = "unbanded"
    else:
        group = groups[target["country"], target["level"]]
        result["peer_count"] = group.peer_count
        median = group.median_without(target["employee_id_key"])
        if median is None:
            state = "insufficient_peers"
        else:
            result["peer_median"] = monetary_statistic(median)
            if median == 0:
                state = "zero_peer_median"
            else:
                state = "available"
                difference = (Fraction(target["salary_minor"]) - median) / median
                result["percentage_difference"] = percentage(difference.numerator, difference.denominator)
    result.update(state=state, state_label=STATES[state][1])
    return result


def comparisons(path: Path, filters: PopulationFilters):
    with read_transaction(path) as connection:
        targets = read_targets(connection, filters)
        groups = read_peer_groups(connection)
    counts = dict.fromkeys((count_key for count_key, _ in STATES.values()), 0)
    items = []
    for target in targets:
        item = equity_item(target, groups)
        counts[STATES[item["state"]][0]] += 1
        items.append(item)
    return {"filters": filters.as_dict(), "peer_scope": "full_saved_country_level_excluding_self",
            "total_count": len(targets), **counts, "items": items}
