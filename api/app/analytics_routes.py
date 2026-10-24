from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import analytics
from app.band_routes import Country, Level

router = APIRouter(prefix="/analytics", tags=["analytics"])
Currency = Literal["INR", "USD", "GBP", "EUR", "SGD", "AED"]


class AnalyticsFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    country: Country | None = None
    department: str | None = None
    level: Level | None = None

    @field_validator("*", mode="before")
    @classmethod
    def optional_filter(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    def population(self):
        return analytics.PopulationFilters(**self.model_dump())


class MonetaryStatistic(BaseModel):
    minor_numerator: str
    minor_denominator: str
    amount: str


class CountrySummary(BaseModel):
    country: Country
    currency: Currency
    count: int
    state: Literal["available", "no_data"]
    average: MonetaryStatistic | None
    median: MonetaryStatistic | None
    minimum: MonetaryStatistic | None
    maximum: MonetaryStatistic | None
    spread: MonetaryStatistic | None


class DepartmentSummary(CountrySummary):
    department_key: str
    department: str


class LevelSummary(CountrySummary):
    level: Level


class SummariesResponse(BaseModel):
    filters: AnalyticsFilters
    total_count: int
    countries: list[CountrySummary]
    departments: list[DepartmentSummary]
    levels: list[LevelSummary]


class CompaBucket(BaseModel):
    key: Literal["below_80", "80_to_120", "above_120"]
    label: str
    count: int
    percentage: str | None


class CompaResponse(BaseModel):
    filters: AnalyticsFilters
    total_count: int
    eligible_count: int
    excluded_unbanded_count: int
    state: Literal["available", "no_eligible_data"]
    buckets: list[CompaBucket] = Field(min_length=3, max_length=3)


@router.get("/summaries", response_model=SummariesResponse)
def summaries(request: Request, filters: Annotated[AnalyticsFilters, Query()]):
    return analytics.summaries(request.app.state.settings.database_path, filters.population())


@router.get("/compa-ratio", response_model=CompaResponse)
def compa_ratio(request: Request, filters: Annotated[AnalyticsFilters, Query()]):
    return analytics.compa_ratios(request.app.state.settings.database_path, filters.population())
