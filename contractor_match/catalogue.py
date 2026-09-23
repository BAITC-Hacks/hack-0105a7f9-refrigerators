from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import get_args

from .models import FIRST_DATE, LAST_DATE, EventFormat, Language


DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "contractors.csv"
EXPECTED_COLUMNS = {
    "id", "anon_name", "categories", "city", "city_imputed", "synthetic",
    "price_from_kzt", "price_imputed", "event_formats", "languages",
    "max_hours", "busy_dates", "description",
}


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    categories: tuple[str, ...]
    city: str
    city_imputed: bool
    synthetic: bool
    price_from_kzt: int
    price_imputed: bool
    event_formats: tuple[str, ...]
    languages: tuple[str, ...]
    max_hours: float | None
    busy_dates: frozenset[date]
    description: str


def _parts(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split("|") if part.strip())


def _flag(value: str) -> bool:
    if value not in {"True", "False"}:
        raise ValueError(f"Некорректный флаг в CSV: {value!r}")
    return value == "True"


def _validate_profile(profile: Profile) -> None:
    required = {
        "id": profile.id,
        "anon_name": profile.name,
        "city": profile.city,
        "description": profile.description,
    }
    for field, value in required.items():
        if not value:
            raise ValueError(f"поле {field} не может быть пустым")
    for field, values in (
        ("categories", profile.categories),
        ("event_formats", profile.event_formats),
        ("languages", profile.languages),
    ):
        if not values:
            raise ValueError(f"поле {field} не может быть пустым")
    if profile.price_from_kzt <= 0:
        raise ValueError("price_from_kzt должен быть больше нуля")
    if profile.max_hours is not None and (
        not math.isfinite(profile.max_hours) or profile.max_hours <= 0
    ):
        raise ValueError("max_hours должен быть положительным конечным числом")
    if unknown := set(profile.event_formats) - set(get_args(EventFormat)):
        raise ValueError(f"неизвестный event_format: {', '.join(sorted(unknown))}")
    if unknown := set(profile.languages) - set(get_args(Language)):
        raise ValueError(f"неизвестный language: {', '.join(sorted(unknown))}")
    if unknown := {day for day in profile.busy_dates if not FIRST_DATE <= day <= LAST_DATE}:
        raise ValueError(f"busy_dates вне календаря: {', '.join(str(day) for day in sorted(unknown))}")


def read_catalogue(path: Path) -> tuple[Profile, ...]:
    """Validate any nonempty catalogue, including small isolated test fixtures."""
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if (
            reader.fieldnames is None
            or len(reader.fieldnames) != len(EXPECTED_COLUMNS)
            or set(reader.fieldnames) != EXPECTED_COLUMNS
        ):
            raise ValueError("CSV не соответствует ожидаемой схеме каталога")
        parsed: list[Profile] = []
        for number, row in enumerate(reader, start=1):
            try:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("неверное число колонок")
                profile = Profile(
                    id=row["id"].strip(),
                    name=row["anon_name"].strip(),
                    categories=_parts(row["categories"]),
                    city=row["city"].strip(),
                    city_imputed=_flag(row["city_imputed"]),
                    synthetic=_flag(row["synthetic"]),
                    price_from_kzt=int(row["price_from_kzt"]),
                    price_imputed=_flag(row["price_imputed"]),
                    event_formats=_parts(row["event_formats"]),
                    languages=_parts(row["languages"]),
                    max_hours=float(row["max_hours"]) if row["max_hours"] else None,
                    busy_dates=frozenset(
                        date.fromisoformat(item) for item in _parts(row["busy_dates"])
                    ),
                    description=row["description"].strip(),
                )
                _validate_profile(profile)
            except (ValueError, TypeError) as error:
                raise ValueError(
                    f"Профиль №{number} ({row.get('id') or 'без id'}) в CSV: {error}"
                ) from error
            parsed.append(profile)
        profiles = tuple(parsed)
    if not profiles:
        raise ValueError("Каталог не может быть пустым")
    if len({p.id for p in profiles}) != len(profiles):
        raise ValueError("В каталоге повторяются id профилей")
    return profiles


@lru_cache(maxsize=1)
def load_catalogue() -> tuple[Profile, ...]:
    profiles = read_catalogue(DATA_FILE)
    if len(profiles) != 66 or sum(p.synthetic for p in profiles) != 13:
        raise ValueError("В исходном наборе ожидаются 66 профилей, включая 13 синтетических")
    return profiles
