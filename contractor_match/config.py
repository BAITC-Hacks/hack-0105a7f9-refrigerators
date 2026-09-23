from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class ConfigurationError(RuntimeError):
    """Safe configuration error: never includes environment values or keys."""


@dataclass(frozen=True)
class CatalogueSettings:
    provider: str
    url: str = ""
    key: str = field(default="", repr=False)


def load_catalogue_settings() -> CatalogueSettings:
    provider = os.getenv("CATALOGUE_PROVIDER", "csv").strip().casefold()
    if provider not in {"csv", "supabase"}:
        raise ConfigurationError("CATALOGUE_PROVIDER должен быть csv или supabase.")
    if provider == "csv":
        return CatalogueSettings(provider)
    value = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "https" and parsed.hostname
                 and parsed.hostname.endswith(".supabase.co")
                 and not parsed.username and not parsed.password and not parsed.query
                 and not parsed.fragment and not parsed.path and parsed.port in {None, 443})
    except ValueError:
        valid = False
    if not valid:
        raise ConfigurationError("SUPABASE_URL: нужен HTTPS-адрес проекта *.supabase.co без пути и ключей.")
    key = os.getenv("SUPABASE_SECRET_KEY", "").strip()
    if not key or "\n" in key or "\r" in key:
        raise ConfigurationError("Укажите серверный SUPABASE_SECRET_KEY в окружении backend.")
    return CatalogueSettings(provider, value, key)


def load_cors_origins() -> list[str]:
    defaults = ",".join(f"http://{host}:{port}" for host in ("localhost", "127.0.0.1")
                        for port in (3000, 4317, 5173))
    raw = os.getenv("CORS_ALLOWED_ORIGINS", defaults)
    origins = []
    for value in raw.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            parsed = urlsplit(value)
            valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                     and not parsed.username and not parsed.password and not parsed.query
                     and not parsed.fragment and parsed.path in {"", "/"} and parsed.port != 0)
        except ValueError:
            valid = False
        if not valid or "*" in value:
            raise ConfigurationError("CORS_ALLOWED_ORIGINS: перечислите точные HTTP(S) origins без путей и ключей.")
        # Match browser serialization: lowercase host and no default port.
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        port = f":{parsed.port}" if parsed.port and parsed.port != {"http": 80, "https": 443}[parsed.scheme] else ""
        origin = f"{parsed.scheme}://{host}{port}"
        if origin not in origins:
            origins.append(origin)
    return origins


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
