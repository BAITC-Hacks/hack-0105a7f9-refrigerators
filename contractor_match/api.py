from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from typing import get_args

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .catalogue import load_catalogue
from .supabase_catalogue import CatalogueUnavailable
from .config import ConfigurationError, load_cors_origins, load_settings
from .models import (
    ApiError,
    CatalogueOptions,
    ErrorResponse,
    EventFormat,
    HealthResponse,
    InputIssue,
    Language,
    RecommendationRequest,
    RecommendationResponse,
)
from .ranking import catalog_index
from .observability import RequestLoggingMiddleware, configure_logging, log_exception
from .service import RecommendationInputError, catalogue_options, recommend


logger = logging.getLogger(__name__)

DIAGNOSTIC_HEADERS = {
    "X-Request-ID": {"description": "Созданный сервером номер запроса для поиска в логах.",
                     "schema": {"type": "string", "pattern": "^[a-f0-9]{32}$"}},
    "X-Process-Time-Ms": {"description": "Время до начала HTTP-ответа в миллисекундах.",
                          "schema": {"type": "number", "minimum": 0}},
}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    load_settings()
    try:
        load_catalogue()
        catalog_index()
    except CatalogueUnavailable:
        # Allow HTTP 503 + request_id and recovery on the next request. Failed
        # loads are never cached; invalid local files still fail at startup.
        logger.warning("catalogue_unavailable_at_startup")
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
    if kind == "finite_number":
        return "Укажите конечное число."
    if kind == "extra_forbidden":
        return "Неизвестное поле запроса."
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


async def request_validation_error(_request: Request, error: RequestValidationError) -> JSONResponse:
    issues = [
        InputIssue(
            field=".".join(str(part) for part in item["loc"] if part != "body") or "body",
            message=_validation_message(str(item["loc"][-1]), item),
        )
        for item in error.errors()
    ]
    return _error_response(issues)


async def recommendation_input_error(
    _request: Request, error: RecommendationInputError
) -> JSONResponse:
    return _error_response([InputIssue(field=error.field, message=str(error))])


async def configuration_error(_request: Request, error: ConfigurationError) -> JSONResponse:
    payload = ErrorResponse(error=ApiError(
        code="service_misconfigured", message="Сервис настроен некорректно.",
        details=[InputIssue(field="configuration", message=str(error))],
    ))
    return JSONResponse(status_code=503, content=payload.model_dump())


async def catalogue_error(_request: Request, _error: CatalogueUnavailable) -> JSONResponse:
    payload = ErrorResponse(error=ApiError(
        code="catalogue_unavailable", message="Каталог временно недоступен. Попробуйте позже.", details=[],
    ))
    return JSONResponse(status_code=503, content=payload.model_dump())


async def http_error(_request: Request, error: HTTPException) -> JSONResponse:
    code, message = {
        404: ("not_found", "Маршрут API не найден."),
        405: ("method_not_allowed", "Этот HTTP-метод не поддерживается маршрутом."),
    }.get(error.status_code, ("http_error", "Не удалось обработать HTTP-запрос."))
    payload = ErrorResponse(error=ApiError(code=code, message=message, details=[]))
    return JSONResponse(status_code=error.status_code, content=payload.model_dump(), headers=error.headers)


async def safe_unexpected_error(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as error:
        log_exception(logger, error)
        payload = ErrorResponse(error=ApiError(
            code="internal_error", message="Не удалось выполнить запрос. Попробуйте ещё раз.", details=[],
        ))
        return JSONResponse(status_code=500, content=payload.model_dump())


def health() -> HealthResponse:
    load_settings()
    load_catalogue()
    return HealthResponse()


def options() -> CatalogueOptions:
    return catalogue_options()


def recommendations(request: RecommendationRequest) -> RecommendationResponse:
    return recommend(request)


def create_app() -> FastAPI:
    api = FastAPI(
        title="Умный подбор event-подрядчиков",
        description="До трёх проверяемых рекомендаций из каталога HackAlem AI.",
        version="0.2.0", lifespan=lifespan,
        responses={200: {"headers": DIAGNOSTIC_HEADERS},
                   500: {"model": ErrorResponse, "headers": DIAGNOSTIC_HEADERS},
                   503: {"model": ErrorResponse, "headers": DIAGNOSTIC_HEADERS}},
    )
    api.add_exception_handler(RequestValidationError, request_validation_error)
    api.add_exception_handler(RecommendationInputError, recommendation_input_error)
    api.add_exception_handler(ConfigurationError, configuration_error)
    api.add_exception_handler(CatalogueUnavailable, catalogue_error)
    api.add_exception_handler(HTTPException, http_error)
    # CORS wraps the error handler; request tracing also wraps preflight responses.
    api.middleware("http")(safe_unexpected_error)
    api.add_middleware(CORSMiddleware, allow_origins=load_cors_origins(),
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Accept"],
                       allow_credentials=False, expose_headers=list(DIAGNOSTIC_HEADERS))
    api.add_middleware(RequestLoggingMiddleware)
    api.add_api_route("/health", health, methods=["GET"], response_model=HealthResponse,
                      operation_id="getHealth")
    api.add_api_route("/catalogue/options", options, methods=["GET"], response_model=CatalogueOptions,
                      operation_id="getCatalogueOptions")
    api.add_api_route("/recommendations", recommendations, methods=["POST"],
                      response_model=RecommendationResponse, operation_id="recommend",
                      responses={422: {"model": ErrorResponse, "headers": DIAGNOSTIC_HEADERS}})
    return api


app = create_app()
