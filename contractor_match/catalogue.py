from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path


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


@lru_cache(maxsize=1)
def load_catalogue() -> tuple[Profile, ...]:
    with DATA_FILE.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None or set(reader.fieldnames) != EXPECTED_COLUMNS:
            raise ValueError("CSV не соответствует ожидаемой схеме каталога")
        profiles = tuple(
            Profile(
                id=row["id"],
                name=row["anon_name"],
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
            for row in reader
        )
    if len(profiles) != 66 or len({p.id for p in profiles}) != len(profiles):
        raise ValueError("Ожидались 66 профилей с уникальными id")
    return profiles
