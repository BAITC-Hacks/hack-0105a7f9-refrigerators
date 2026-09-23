from __future__ import annotations

from datetime import date
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


FIRST_DATE = date(2026, 9, 23)
LAST_DATE = date(2026, 12, 31)
EventFormat = Literal[
    "свадьба", "той", "корпоратив", "конференция", "юбилей", "день рождения"
]
Language = Literal["русский", "казахский", "английский"]
Status = Literal["matched", "category_absent", "no_eligible"]
AiMode = Literal["openai", "nvidia", "fallback", "not_used"]
AiReason = Literal[
    "success", "not_needed", "local_requested", "missing_key", "timeout",
    "auth_error", "rate_limited", "network_error", "provider_error",
    "invalid_response", "invalid_evidence",
]


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    city: str = Field(min_length=1, max_length=100)
    date: date
    event_format: EventFormat
    category: str = Field(min_length=1, max_length=100)
    budget_kzt: int = Field(gt=0, strict=True)
    duration_hours: float | None = Field(default=None, gt=0, strict=True, allow_inf_nan=False)
    language: Language | None = None
    brief: str | None = Field(default=None, max_length=500)

    @field_validator("city", "category", "brief", mode="before")
    @classmethod
    def normalize_whitespace(cls, value: object) -> object:
        return " ".join(value.split()) if isinstance(value, str) else value

    @field_validator("date", mode="before")
    @classmethod
    def calendar_date_only(cls, value: object) -> object:
        if type(value) is date or (isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
            return value
        raise ValueError("Укажите дату в формате ГГГГ-ММ-ДД, без времени и timestamp.")

    @field_validator("event_format", "language", mode="before")
    @classmethod
    def normalize_choice(cls, value: object) -> object:
        if isinstance(value, str):
            return " ".join(value.casefold().split())
        return value

    @field_validator("date")
    @classmethod
    def date_in_calendar(cls, value: date) -> date:
        if not FIRST_DATE <= value <= LAST_DATE:
            raise ValueError(
                "Дата должна быть в календаре с 2026-09-23 по 2026-12-31; "
                "за его пределами занятость неизвестна."
            )
        return value

    @field_validator("brief")
    @classmethod
    def normalize_brief(cls, value: str | None) -> str | None:
        return value or None


class Card(BaseModel):
    id: str
    name: str
    category: str
    city: str
    price_from_kzt: int
    synthetic: bool
    city_imputed: bool
    price_imputed: bool
    explanation: str
    evidence_quote: str
    evidence_note: str | None = None


class SelectionCounts(BaseModel):
    category_total: int = 0
    eligible_total: int = 0
    returned_total: int = 0
    excluded_total: int = 0


class RecommendationResponse(BaseModel):
    status: Status
    message: str
    cards: list[Card]
    reasons: dict[str, int]
    ai_mode: AiMode
    ai_reason: AiReason = "not_needed"
    counts: SelectionCounts = Field(default_factory=SelectionCounts)


class CatalogueOptions(BaseModel):
    cities: list[str]
    categories_by_city: dict[str, list[str]]
    event_formats: list[str]
    languages: list[str]
    calendar_start: date
    calendar_end: date


class InputIssue(BaseModel):
    field: str
    message: str


class ApiError(BaseModel):
    code: Literal["invalid_request", "service_misconfigured"] = "invalid_request"
    message: str = "Проверьте параметры запроса."
    details: list[InputIssue]


class ErrorResponse(BaseModel):
    error: ApiError
