from typing import Annotated, Generic, Literal, TypeVar

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app import imports
from app.xlsx import ImportProblem, MAX_UPLOAD

router = APIRouter(prefix="/imports", tags=["imports"])


class PreviewMetadata(BaseModel):
    preview_id: str
    owner: str
    worksheet: str
    source_digest: str
    proposed_digest: str
    created_at: int
    expires_at: int
    status: Literal["clean", "invalid"]
    total_rows: int
    valid_count: int
    invalid_count: int
    error_count: int
    unbanded_count: int
    valid_unbanded_count: int


class ImportIssue(BaseModel):
    model_config = ConfigDict(extra="allow")
    sheet: str
    row: int | None
    field: str
    code: str
    message: str


class WorksheetInfo(BaseModel):
    name: str
    header_row: int | None
    data_rows: int
    candidate: bool
    header_errors: list[ImportIssue]
    formula_count: int


class InspectionResponse(BaseModel):
    source_digest: str
    worksheets: list[WorksheetInfo]
    selection_required: bool
    selected_worksheet: str | None


class PreviewValues(BaseModel):
    employee_id: str | None
    name: str | None
    email: str | None
    department: str | None
    level: str | None
    country: str | None
    salary_minor: str | None


class RawCell(BaseModel):
    value: str
    kind: str
    formula: str | None


class BandSnapshot(BaseModel):
    revision: int
    etag: str
    minimum_minor: str
    midpoint_minor: str
    maximum_minor: str


class PreviewRow(BaseModel):
    sheet: str
    row_number: int
    valid: bool
    values: PreviewValues
    salary_amount: str | None
    salary_input: str | None
    rounding_applied: bool | None
    currency: str | None
    band_status: Literal["Band-matched", "Unbanded"] | None
    band: BandSnapshot | None
    raw_cells: dict[str, RawCell]
    errors: list[ImportIssue]


Item = TypeVar("Item")


class PreviewPage(BaseModel, Generic[Item]):
    preview_id: str
    status: Literal["clean", "invalid"]
    items: list[Item]
    total: int
    page: int
    page_size: int
    total_pages: int


class PageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(default=1, ge=1)
    page_size: Literal[25, 50, 100] = 25

    @field_validator("page_size", mode="before")
    @classmethod
    def query_size(cls, value):
        return int(value) if isinstance(value, str) and value in {"25", "50", "100"} else value


async def uploaded(request):
    if request.headers.get("content-type", "").split(";", 1)[0].lower() not in {
        "application/octet-stream", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }:
        raise ImportProblem(415, "unsupported_upload", "Send the .xlsx file as the raw request body")
    length = request.headers.get("content-length")
    if length and (not length.isdecimal() or int(length) > MAX_UPLOAD):
        raise ImportProblem(413, "upload_limit", "Workbook exceeds 10 MiB or has an invalid length")
    contents = bytearray()
    async for chunk in request.stream():
        if len(contents) + len(chunk) > MAX_UPLOAD:
            raise ImportProblem(413, "upload_limit", "Workbook exceeds 10 MiB")
        contents.extend(chunk)
    return bytes(contents)


@router.post("/inspect", response_model=InspectionResponse)
async def inspect(request: Request):
    return await run_in_threadpool(imports.inspect, await uploaded(request))


@router.post("/previews", status_code=201, response_model=PreviewMetadata)
async def create(request: Request, response: Response, worksheet: Annotated[str | None, Query()] = None):
    result = await run_in_threadpool(imports.create_preview, request.app.state.settings.database_path,
                                    request.app.state.settings.hr_email, await uploaded(request), worksheet)
    response.headers["Location"] = "/imports/previews/" + result["preview_id"]
    return result


@router.get("/previews/{preview_id}", response_model=PreviewMetadata)
def detail(preview_id: str, request: Request):
    return imports.preview(request.app.state.settings.database_path, request.app.state.settings.hr_email, preview_id)


@router.get("/previews/{preview_id}/rows", response_model=PreviewPage[PreviewRow])
def rows(preview_id: str, request: Request, params: Annotated[PageParams, Query()]):
    return imports.pages(request.app.state.settings.database_path, request.app.state.settings.hr_email,
                         preview_id, "rows", params.page, params.page_size)


@router.get("/previews/{preview_id}/errors", response_model=PreviewPage[ImportIssue])
def errors(preview_id: str, request: Request, params: Annotated[PageParams, Query()]):
    return imports.pages(request.app.state.settings.database_path, request.app.state.settings.hr_email,
                         preview_id, "errors", params.page, params.page_size)
