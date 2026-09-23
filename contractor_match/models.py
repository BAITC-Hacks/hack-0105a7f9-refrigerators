from __future__ import annotations

from datetime import date
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


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    city: str = Field(min_length=1)
    date: date
    event_format: EventFormat
    category: str = Field(min_length=1)
    budget_kzt: int = Field(gt=0)
    duration_hours: float | None = Field(default=None, gt=0)
    language: Language | None = None
    brief: str | None = Field(default=None, max_length=500)

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


class RecommendationResponse(BaseModel):
    status: Status
    message: str
    cards: list[Card]
    reasons: dict[str, int]
    ai_mode: AiMode


class CatalogueOptions(BaseModel):
    cities: list[str]
    categories_by_city: dict[str, list[str]]
    event_formats: list[str]
    languages: list[str]
    calendar_start: date
    calendar_end: date
