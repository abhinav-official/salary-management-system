from typing import Annotated, Literal
from urllib.parse import quote

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import employees
from app.band_routes import Country, Level
from app.database import connect
from app.models import Employee
from app.money import parse_money
from app.normalization import required_text

router = APIRouter(prefix="/employees", tags=["employees"])


class EmployeeChanges(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    email: str
    department: str
    country: Country
    level: Level
    salary_amount: str

    @field_validator("*", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("name", "email", "department", check_fields=False)
    @classmethod
    def text(cls, value: str, info):
        return required_text(value, info.field_name)

    @field_validator("email")
    @classmethod
    def email_address(cls, value: str):
        try:
            validate_email(value, check_deliverability=False)
        except EmailNotValidError as error:
            raise ValueError("Invalid email address") from error
        return value

    @field_validator("salary_amount")
    @classmethod
    def money(cls, value: str):
        parse_money(value)
        return value

    def changes(self):
        return {**self.model_dump(exclude={"salary_amount", "employee_id"}),
                "salary_minor": parse_money(self.salary_amount)}


class EmployeeCreate(EmployeeChanges):
    employee_id: str

    @field_validator("employee_id")
    @classmethod
    def identity(cls, value: str):
        return required_text(value, "employee_id")


class DirectoryParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search: str = ""
    country: Country | None = None
    level: Level | None = None
    department: str | None = None
    sort: Literal["employee_id", "name", "email", "department", "level", "country", "salary"] = "employee_id"
    direction: Literal["asc", "desc"] = "asc"
    page: int = Field(default=1, ge=1)
    page_size: Literal[25, 50, 100] = 25

    @field_validator("page_size", mode="before")
    @classmethod
    def page_size_from_query(cls, value):
        # HTTP query values are strings; retain the closed integer allowlist.
        return int(value) if isinstance(value, str) and value in {"25", "50", "100"} else value

    @field_validator("country", "level", "department", mode="before")
    @classmethod
    def optional_filter(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    def query(self):
        return employees.DirectoryQuery(**self.model_dump(exclude={"page", "page_size"}))


@router.get("")
def index(request: Request, params: Annotated[DirectoryParams, Query()]):
    return employees.list_employees(request.app.state.settings.database_path, params.query(), params.page, params.page_size)


@router.get("/filter-options")
def options(request: Request):
    return employees.filter_options(request.app.state.settings.database_path)


@router.get("/export.csv")
def export(request: Request, params: Annotated[DirectoryParams, Query()]):
    content = employees.export_csv(request.app.state.settings.database_path, params.query())
    return Response(content, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="employees.csv"'})


@router.post("", status_code=201)
def create(body: EmployeeCreate, request: Request, response: Response):
    result = employees.create_employee(request.app.state.settings.database_path,
                                       Employee(body.employee_id, **body.changes()))
    response.headers["ETag"] = result["etag"]
    response.headers["Location"] = "/employees/records/" + quote(result["employee_id"], safe="")
    return result


@router.get("/records/{employee_id:path}")
def detail(employee_id: str, request: Request, response: Response):
    with connect(request.app.state.settings.database_path, existing=True) as connection:
        result = employees.get_employee(connection, employee_id)
    response.headers["ETag"] = result["etag"]
    return result


@router.put("/records/{employee_id:path}")
def edit(employee_id: str, body: EmployeeChanges, request: Request, response: Response,
         if_match: Annotated[str | None, Header()] = None,
         country_change_confirmed: Annotated[bool, Query()] = False):
    result = employees.edit_employee(request.app.state.settings.database_path, employee_id, body.changes(),
                                     if_match, country_change_confirmed)
    response.headers["ETag"] = result["etag"]
    return result


@router.delete("/records/{employee_id:path}")
def delete(employee_id: str, request: Request, if_match: Annotated[str | None, Header()] = None,
           confirmed: Annotated[bool, Query()] = False):
    return employees.delete_employee(request.app.state.settings.database_path, employee_id, if_match, confirmed)
