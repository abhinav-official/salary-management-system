from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from app import equity
from app.analytics_routes import AnalyticsFilters, Currency, MonetaryStatistic
from app.band_routes import Country, Level

router = APIRouter(prefix="/analytics", tags=["equity"])


class EquityItem(BaseModel):
    employee_id: str
    name: str
    department: str
    country: Country
    level: Level
    currency: Currency
    salary_minor: str
    salary_amount: str
    band_status: Literal["Band-matched", "Unbanded"]
    state: Literal["available", "unbanded", "insufficient_peers", "zero_peer_median"]
    state_label: str
    peer_count: int
    peer_median: MonetaryStatistic | None
    percentage_difference: str | None


class EquityResponse(BaseModel):
    filters: AnalyticsFilters
    peer_scope: Literal["full_saved_country_level_excluding_self"]
    total_count: int
    available_count: int
    excluded_unbanded_count: int
    insufficient_peers_count: int
    zero_peer_median_count: int
    items: list[EquityItem]


@router.get("/equity", response_model=EquityResponse)
def comparisons(request: Request, filters: Annotated[AnalyticsFilters, Query()]):
    return equity.comparisons(request.app.state.settings.database_path, filters.population())
