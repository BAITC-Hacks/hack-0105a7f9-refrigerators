from __future__ import annotations

import os
from dataclasses import dataclass, field


class ConfigurationError(RuntimeError):
    """Safe configuration error: never includes environment values or keys."""


@dataclass(frozen=True)
class AISettings:
    requested_provider: str
    provider: str
    model: str
    key: str = field(repr=False)


def load_settings() -> AISettings:
    requested = os.getenv("AI_PROVIDER", "auto").strip().casefold()
    if requested not in {"auto", "local", "openai", "nvidia"}:
        raise ConfigurationError("AI_PROVIDER должен быть auto, local, openai или nvidia.")
    keys = {name: os.getenv(f"{name.upper()}_API_KEY", "").strip() for name in ("openai", "nvidia")}
    provider = requested
    if provider == "auto":
        provider = next((name for name in ("openai", "nvidia") if keys[name]), "local")
    defaults = {"openai": "gpt-4o-mini", "nvidia": "mistralai/mistral-nemotron"}
    model = os.getenv(f"{provider.upper()}_MODEL", defaults.get(provider, "")).strip()
    if provider != "local" and not model:
        raise ConfigurationError("Название модели выбранного AI-провайдера не может быть пустым.")
    return AISettings(requested, provider, model, keys.get(provider, ""))
