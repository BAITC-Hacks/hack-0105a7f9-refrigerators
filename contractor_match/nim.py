"""Adapters for an explicitly configured, already running NVIDIA NIM workload."""
import asyncio
import logging
import math
import time
from dataclasses import dataclass

import httpx

from .catalogue import Profile
from .config import RankingSettings, load_ranking_settings
from .models import RecommendationRequest
from .ranking import _query, rank_profiles


RERANK_TIMEOUT_SECONDS = 2.0
EXTERNAL_BUDGET_SECONDS = 6.0
logger = logging.getLogger(__name__)


def assert_sync_context() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError("В async-коде вызывайте recommend через asyncio.to_thread.")


async def _call_rerank(settings: RankingSettings, request: RecommendationRequest,
                       profiles: list[Profile], timeout: float) -> object:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.post(
            settings.base_url + "/ranking",
            headers={"Authorization": f"Bearer {settings.key}"} if settings.key else {},
            json={"model": settings.model, "query": {"text": _query(request)},
                  "passages": [{"text": p.description} for p in profiles], "truncate": "END"},
        )
    response.raise_for_status()
    return response.json()


def validate_scores(data: object, size: int) -> dict[int, float]:
    if not isinstance(data, dict) or not isinstance(data.get("rankings"), list):
        raise ValueError("Missing rankings")
    scores = {}
    for item in data["rankings"]:
        if not isinstance(item, dict):
            raise ValueError("Invalid ranking item")
        index, score = item.get("index"), item.get("logit")
        if (type(index) is not int or not 0 <= index < size or index in scores
                or type(score) not in (int, float) or not math.isfinite(score)):
            raise ValueError("Invalid index or score")
        scores[index] = score
    if set(scores) != set(range(size)):
        raise ValueError("Incomplete rankings")
    return scores


@dataclass(frozen=True)
class RankingResult:
    profiles: list[Profile]
    mode: str
    reason: str


def select_order(profiles: list[Profile], request: RecommendationRequest, deadline: float) -> RankingResult:
    settings = load_ranking_settings()
    if settings.provider == "tfidf":
        return RankingResult(rank_profiles(profiles, request), "tfidf", "local_requested")
    # Stable passage indices and tie-breaks are independent of transport response order.
    stable = sorted(profiles, key=lambda p: p.id)
    timeout = min(RERANK_TIMEOUT_SECONDS, deadline - time.monotonic())
    if timeout <= 0:
        return RankingResult(rank_profiles(profiles, request), "tfidf", "timeout")
    assert_sync_context()

    async def bounded():
        return await asyncio.wait_for(_call_rerank(settings, request, stable, timeout), timeout)

    try:
        scores = validate_scores(asyncio.run(bounded()), len(stable))
        ordered = sorted(enumerate(stable), key=lambda pair: (-scores[pair[0]], pair[1].price_from_kzt, pair[1].id))
        return RankingResult([profile for _, profile in ordered], "brev", "success")
    except (TimeoutError, httpx.TimeoutException):
        reason = "timeout"
    except httpx.HTTPStatusError as error:
        reason = "auth_error" if error.response.status_code in {401, 403} else "rate_limited" if error.response.status_code == 429 else "provider_error"
    except httpx.RequestError:
        reason = "network_error"
    except (ValueError, TypeError, KeyError, OverflowError):
        reason = "invalid_response"
    logger.info("ranking_fallback provider=brev reason=%s", reason)
    return RankingResult(rank_profiles(profiles, request), "tfidf", reason)
