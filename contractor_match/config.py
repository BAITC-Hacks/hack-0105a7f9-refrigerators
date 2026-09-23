from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class ConfigurationError(RuntimeError):
    """Safe configuration error: never includes environment values or keys."""


@dataclass(frozen=True)
class AISettings:
    requested_provider: str
    provider: str
    model: str
    key: str = field(repr=False)
    base_url: str = ""


@dataclass(frozen=True)
class RankingSettings:
    provider: str
    base_url: str = ""
    model: str = ""
    key: str = field(default="", repr=False)


def _nim_url(variable: str) -> str:
    value = os.getenv(variable, "").strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username
                 and not parsed.password and not parsed.query and not parsed.fragment
                 and parsed.path.endswith("/v1") and parsed.port != 0)
    except ValueError:
        valid = False
    if not valid:
        raise ConfigurationError(f"{variable}: нужен адрес API с /v1, без ключей, query и fragment.")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ConfigurationError(f"{variable}: HTTP разрешён только для локального SSH-туннеля; иначе нужен HTTPS.")
    return value


def _nim_model(variable: str) -> str:
    model = os.getenv(variable, "").strip()
    if not model:
        raise ConfigurationError(f"Укажите {variable}: точное имя модели из NIM /v1/models.")
    return model


def load_ranking_settings() -> RankingSettings:
    provider = os.getenv("RANKING_PROVIDER", "tfidf").strip().casefold()
    if provider not in {"tfidf", "brev"}:
        raise ConfigurationError("RANKING_PROVIDER должен быть tfidf или brev.")
    if provider == "tfidf":
        return RankingSettings(provider)
    return RankingSettings(provider, _nim_url("NIM_RERANK_BASE_URL"),
                           _nim_model("NIM_RERANK_MODEL"), os.getenv("NIM_RERANK_API_KEY", "").strip())


def load_settings() -> AISettings:
    load_ranking_settings()
    requested = os.getenv("AI_PROVIDER", "auto").strip().casefold()
    if requested not in {"auto", "local", "openai", "nvidia", "brev"}:
        raise ConfigurationError("AI_PROVIDER должен быть auto, local, openai, nvidia или brev.")
    if requested == "brev":
        return AISettings(requested, requested, _nim_model("NIM_MODEL"),
                          os.getenv("NIM_API_KEY", "").strip(), _nim_url("NIM_BASE_URL"))
    keys = {name: os.getenv(f"{name.upper()}_API_KEY", "").strip() for name in ("openai", "nvidia")}
    provider = requested
    if provider == "auto":
        provider = next((name for name in ("openai", "nvidia") if keys[name]), "local")
    defaults = {"openai": "gpt-4o-mini", "nvidia": "mistralai/mistral-nemotron"}
    model = os.getenv(f"{provider.upper()}_MODEL", defaults.get(provider, "")).strip()
    if provider != "local" and not model:
        raise ConfigurationError("Название модели выбранного AI-провайдера не может быть пустым.")
    return AISettings(requested, provider, model, keys.get(provider, ""))
