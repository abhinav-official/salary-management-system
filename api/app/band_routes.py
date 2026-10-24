from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app import bands
from app.database import connect
from app.models import SalaryBand, validate_band_amounts
from app.money import parse_money

Country = Literal["India", "US", "UK", "Germany", "Singapore", "UAE"]
Level = Literal["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]
router = APIRouter(prefix="/bands", tags=["bands"])


class BandAmounts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    minimum_amount: str
    midpoint_amount: str
    maximum_amount: str

    @field_validator("minimum_amount", "midpoint_amount", "maximum_amount")
    @classmethod
    def exact_money(cls, value: str) -> str:
        parse_money(value)
        return value

    @model_validator(mode="after")
    def ordered_amounts(self):
        validate_band_amounts(parse_money(self.minimum_amount), parse_money(self.midpoint_amount),
                              parse_money(self.maximum_amount))
        return self

    def as_band(self, country: str, level: str) -> SalaryBand:
        return SalaryBand(country, level, parse_money(self.minimum_amount),
                          parse_money(self.midpoint_amount), parse_money(self.maximum_amount))


class BandCreate(BandAmounts):
    country: Country
    level: Level

    @field_validator("country", "level", mode="before")
    @classmethod
    def trim_classification(cls, value):
        return value.strip() if isinstance(value, str) else value


@router.get("")
def index(request: Request):
    items = bands.list_bands(request.app.state.settings.database_path)
    return {"items": items, "total": len(items)}


@router.get("/{country}/{level}")
def detail(country: Country, level: Level, request: Request, response: Response):
    with connect(request.app.state.settings.database_path, existing=True) as connection:
        result = bands.get_band(connection, country, level)
    response.headers["ETag"] = result["etag"]
    return result


@router.post("", status_code=201)
def create(body: BandCreate, request: Request, response: Response):
    result = bands.create_band(request.app.state.settings.database_path, body.as_band(body.country, body.level))
    response.headers["ETag"] = result["etag"]
    response.headers["Location"] = f'/bands/{result["country"]}/{result["level"]}'
    return result


@router.put("/{country}/{level}")
def edit(country: Country, level: Level, body: BandAmounts, request: Request,
         response: Response, if_match: Annotated[str | None, Header()] = None):
    result = bands.edit_band(request.app.state.settings.database_path, body.as_band(country, level), if_match)
    response.headers["ETag"] = result["etag"]
    return result


@router.delete("/{country}/{level}")
def delete(country: Country, level: Level, request: Request,
           if_match: Annotated[str | None, Header()] = None,
           confirmed: Annotated[bool, Query()] = False):
    return bands.delete_band(request.app.state.settings.database_path, country, level, if_match, confirmed)
