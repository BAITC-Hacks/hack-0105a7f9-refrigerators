"""Read one immutable, verified catalogue snapshot. No writes or CSV fallback."""
from __future__ import annotations

from datetime import date

import httpx
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr

from .catalogue import EXPECTED_COLUMNS, Profile, _parts, _validate_profile
from .config import CatalogueSettings


class CatalogueUnavailable(RuntimeError):
    """Safe to expose; never contains provider payloads, URLs or credentials."""


class DatabaseRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: StrictStr
    anon_name: StrictStr
    categories: StrictStr
    city: StrictStr
    city_imputed: StrictBool
    synthetic: StrictBool
    price_from_kzt: StrictInt
    price_imputed: StrictBool
    event_formats: StrictStr
    languages: StrictStr
    max_hours: float | None
    busy_dates: StrictStr
    description: StrictStr

    def profile(self) -> Profile:
        values = self.model_dump()
        values["name"] = values.pop("anon_name")
        for name in ("categories", "event_formats", "languages"):
            values[name] = _parts(values[name])
        values["busy_dates"] = frozenset(date.fromisoformat(day) for day in _parts(self.busy_dates))
        profile = Profile(**values)
        _validate_profile(profile)
        return profile


def fetch_catalogue(settings: CatalogueSettings, original: tuple[Profile, ...]) -> tuple[Profile, ...]:
    try:
        # Hosted project's secret key uses apikey, not a fabricated Bearer JWT.
        # Redirects are disabled so credentials cannot follow a different host.
        with httpx.Client(timeout=3.0, follow_redirects=False) as client:
            response = client.get(
                f"{settings.url}/rest/v1/firebird_contractors",
                headers={"apikey": settings.key, "Accept": "application/json"},
                params={"select": ",".join(sorted(EXPECTED_COLUMNS)), "order": "id.asc", "limit": "67"},
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list) or len(payload) != len(original):
            raise ValueError("Unexpected catalogue size")
        profiles = tuple(DatabaseRow.model_validate(row).profile() for row in payload)
        by_id = {profile.id: profile for profile in profiles}
        # The hackathon dataset is fixed. Reject missing/extra/edited profiles,
        # including altered calendars, rather than silently changing decisions.
        if len(by_id) != len(original) or by_id != {profile.id: profile for profile in original}:
            raise ValueError("Catalogue differs from the supplied dataset")
        return tuple(by_id[profile.id] for profile in original)
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        raise CatalogueUnavailable("Каталог временно недоступен. Попробуйте позже.") from None
