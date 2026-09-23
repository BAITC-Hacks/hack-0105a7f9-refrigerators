"""One set of hard filters for selection and every proposed alternative."""
from .catalogue import Profile
from .models import RecommendationRequest


def failures(profile: Profile, request: RecommendationRequest) -> tuple[str, ...]:
    result = []
    if request.city != profile.city or request.category not in profile.categories:
        result.append("outside_category")
    if request.date in profile.busy_dates:
        result.append("busy")
    if profile.price_from_kzt > request.budget_kzt:
        result.append("over_budget")
    if request.event_format not in profile.event_formats:
        result.append("wrong_format")
    if request.language and request.language not in profile.languages:
        result.append("wrong_language")
    if (request.duration_hours is not None and profile.max_hours is not None
            and profile.max_hours < request.duration_hours):
        result.append("too_short")
    return tuple(result)
