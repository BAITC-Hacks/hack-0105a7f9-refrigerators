from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass

import httpx

from .catalogue import Profile
from .models import RecommendationRequest
from .ranking import local_quote, quote_candidates


TIMEOUT_SECONDS = 6.0
SYSTEM_PROMPT = (
    "Ты помогаешь подобрать event-подрядчиков. Для каждого переданного id выбери "
    "ровно одну строку из его списка allowed_quotes, которая лучше всего "
    "объясняет соответствие формату мероприятия и пожеланию заказчика. "
    "Скопируй строку без изменений, не выдумывай сведения и не добавляй других id. "
    "Ответь только JSON-объектом вида {\"items\":[{\"id\":\"...\",\"quote\":\"...\"}]}.")


@dataclass(frozen=True)
class ExplanationResult:
    quotes: dict[str, str]
    mode: str


def _payload(request: RecommendationRequest, profiles: list[Profile]) -> str:
    return json.dumps(
        {
            "request": {
                "event_format": request.event_format,
                "category": request.category,
                "brief": request.brief,
            },
            "profiles": [
                {"id": profile.id, "allowed_quotes": quote_candidates(profile)}
                for profile in profiles
            ],
        },
        ensure_ascii=False,
    )


def _extract_openai_text(data: dict) -> str:
    for output in data.get("output", []):
        if output.get("type") == "message":
            for content in output.get("content", []):
                if content.get("type") == "output_text":
                    return content["text"]
    raise ValueError("OpenAI не вернул текст ответа")


async def _call_openai(key: str, request: RecommendationRequest, profiles: list[Profile]) -> str:
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "quote": {"type": "string"}},
                    "required": ["id", "quote"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        response = await client.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "store": False,
                "input": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _payload(request, profiles)},
                ],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "contractor_evidence",
                        "strict": True,
                        "schema": schema,
                    }
                },
                "max_output_tokens": 700,
            },
        )
    response.raise_for_status()
    return _extract_openai_text(response.json())


async def _call_nvidia(key: str, request: RecommendationRequest, profiles: list[Profile]) -> str:
    model = os.getenv("NVIDIA_MODEL", "mistralai/mistral-nemotron")
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        response = await client.post(
            "https://integrate.api.nvidia.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _payload(request, profiles)},
                ],
                "temperature": 0,
                "max_tokens": 700,
                "stream": False,
            },
        )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _validated_quotes(raw: str, profiles: list[Profile]) -> dict[str, str]:
    decoded = json.loads(raw)
    items = decoded["items"]
    if not isinstance(items, list) or len(items) != len(profiles):
        raise ValueError("AI вернул неполный список подрядчиков")
    descriptions = {profile.id: profile.description for profile in profiles}
    allowed = {profile.id: set(quote_candidates(profile)) for profile in profiles}
    quotes: dict[str, str] = {}
    for item in items:
        identifier = item["id"]
        quote = item["quote"]
        if (
            identifier not in descriptions
            or identifier in quotes
            or not isinstance(quote, str)
            or not 12 <= len(quote) <= 280
            or re.search(r"[.!?]\s+\S", quote)
            or quote not in allowed[identifier]
            or quote not in descriptions[identifier]
        ):
            raise ValueError("AI вернул цитату, которой нет в исходном описании")
        quotes[identifier] = quote
    if len(set(quotes.values())) != len(quotes):
        raise ValueError("AI вернул одинаковые фрагменты для разных подрядчиков")
    return quotes


def generate_explanations(
    request: RecommendationRequest, profiles: list[Profile]
) -> ExplanationResult:
    provider = os.getenv("AI_PROVIDER", "auto").casefold()
    if provider == "auto":
        provider = next(
            (name for name in ("openai", "nvidia") if os.getenv(f"{name.upper()}_API_KEY")),
            "local",
        )
    key_name = {"openai": "OPENAI_API_KEY", "nvidia": "NVIDIA_API_KEY"}.get(provider)
    key = os.getenv(key_name, "") if key_name else ""
    if key:
        try:
            call = (
                _call_openai(key, request, profiles)
                if provider == "openai" else _call_nvidia(key, request, profiles)
            )
            raw = asyncio.run(asyncio.wait_for(call, timeout=TIMEOUT_SECONDS))
            return ExplanationResult(_validated_quotes(raw, profiles), provider)
        except (TimeoutError, httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
            pass
    return ExplanationResult(
        {profile.id: local_quote(profile, request) for profile in profiles},
        "fallback",
    )
