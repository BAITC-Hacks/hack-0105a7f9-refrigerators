from __future__ import annotations

from contextlib import asynccontextmanager
from typing import get_args

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .catalogue import load_catalogue
from .models import (
    ApiError,
    CatalogueOptions,
    ErrorResponse,
    EventFormat,
    InputIssue,
    Language,
    RecommendationRequest,
    RecommendationResponse,
)
from .ranking import catalog_index
from .service import RecommendationInputError, catalogue_options, recommend


@asynccontextmanager
async def lifespan(_app: FastAPI):
    load_catalogue()
    catalog_index()
    yield


def _validation_message(field: str, error: dict) -> str:
    kind = error["type"]
    if kind == "missing":
        return "Обязательное поле не указано."
    if kind in {"string_too_short", "string_type"}:
        return "Укажите непустой текст."
    if kind == "string_too_long":
        return "Текст слишком длинный."
    if kind in {"greater_than", "greater_than_equal"}:
        return "Значение должно быть больше нуля."
    if kind in {"int_parsing", "int_type"}:
        return "Укажите целое число."
    if kind in {"float_parsing", "float_type"}:
        return "Укажите число."
    if kind.startswith("date_"):
        return "Укажите дату в формате ГГГГ-ММ-ДД."
    if kind == "literal_error":
        choices = {"event_format": get_args(EventFormat), "language": get_args(Language)}
        if field in choices:
            return f"Допустимые значения: {', '.join(choices[field])}."
    if kind == "value_error":
        return error["msg"].removeprefix("Value error, ")
    if kind == "json_invalid":
        return "Некорректный JSON."
    return "Некорректное значение."


def _error_response(issues: list[InputIssue]) -> JSONResponse:
    payload = ErrorResponse(error=ApiError(details=issues))
    return JSONResponse(status_code=422, content=payload.model_dump())


app = FastAPI(
    title="Умный подбор event-подрядчиков",
    description="До трёх проверяемых рекомендаций из каталога HackAlem AI.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def request_validation_error(_request: Request, error: RequestValidationError) -> JSONResponse:
    issues = [
        InputIssue(
            field=".".join(str(part) for part in item["loc"] if part != "body") or "body",
            message=_validation_message(str(item["loc"][-1]), item),
        )
        for item in error.errors()
    ]
    return _error_response(issues)


@app.exception_handler(RecommendationInputError)
async def recommendation_input_error(
    _request: Request, error: RecommendationInputError
) -> JSONResponse:
    return _error_response([InputIssue(field=error.field, message=str(error))])


@app.get("/health")
def health() -> dict[str, str]:
    load_catalogue()
    return {"status": "ok"}


@app.get("/catalogue/options", response_model=CatalogueOptions)
def options() -> CatalogueOptions:
    return catalogue_options()


@app.post(
    "/recommendations",
    response_model=RecommendationResponse,
    responses={422: {"model": ErrorResponse}},
)
def recommendations(request: RecommendationRequest) -> RecommendationResponse:
    return recommend(request)
