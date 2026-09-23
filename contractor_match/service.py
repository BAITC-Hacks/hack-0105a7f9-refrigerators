from __future__ import annotations

from .catalogue import Profile, load_catalogue
from .explanations import generate_explanations
from .models import Card, RecommendationRequest, RecommendationResponse
from .ranking import rank_profiles


REASON_LABELS = {
    "busy": "заняты на дату",
    "over_budget": "стартовая цена выше бюджета",
    "wrong_format": "не работают с указанным форматом",
    "wrong_language": "не работают на указанном языке",
    "too_short": "не могут работать столько часов",
}


def _failures(profile: Profile, request: RecommendationRequest) -> tuple[str, ...]:
    result: list[str] = []
    if request.date in profile.busy_dates:
        result.append("busy")
    if profile.price_from_kzt > request.budget_kzt:
        result.append("over_budget")
    if request.event_format not in profile.event_formats:
        result.append("wrong_format")
    if request.language and request.language not in profile.languages:
        result.append("wrong_language")
    if (
        request.duration_hours is not None
        and profile.max_hours is not None
        and profile.max_hours < request.duration_hours
    ):
        result.append("too_short")
    return tuple(result)


def _reason_text(reasons: dict[str, int]) -> str:
    parts = [
        f"{REASON_LABELS[key]}: {count}"
        for key, count in reasons.items()
        if count
    ]
    return "; ".join(parts)


def _card_explanation(
    profile: Profile, request: RecommendationRequest, quote: str
) -> str:
    date_text = request.date.strftime("%d.%m.%Y")
    price = f"{profile.price_from_kzt:,}".replace(",", " ")
    first = (
        f"Свободен {date_text}, работает с форматом «{request.event_format}»; "
        f"цена от {price} ₸ не превышает бюджет"
    )
    if request.language:
        first += f", работает на языке «{request.language}»"
    if request.duration_hours is not None and profile.max_hours is not None:
        first += f", может работать до {profile.max_hours:g} ч"
    if profile.price_imputed:
        first += " (цена оценочная)"
    return f"{first}. В описании: «{quote.rstrip('.')}»."


def recommend(request: RecommendationRequest) -> RecommendationResponse:
    catalogue = load_catalogue()
    city_profiles = [profile for profile in catalogue if profile.city == request.city]
    if not city_profiles:
        raise ValueError(
            f"Города «{request.city}» нет в каталоге. "
            f"Доступны: {', '.join(sorted({p.city for p in catalogue}))}."
        )
    category_profiles = [
        profile for profile in city_profiles if request.category in profile.categories
    ]
    if not category_profiles:
        return RecommendationResponse(
            status="category_absent",
            message=f"В городе {request.city} нет подрядчиков категории «{request.category}».",
            cards=[],
            reasons={},
            ai_mode="not_used",
        )

    reasons = {key: 0 for key in REASON_LABELS}
    eligible: list[Profile] = []
    for profile in category_profiles:
        failures = _failures(profile, request)
        for reason in failures:
            reasons[reason] += 1
        if not failures:
            eligible.append(profile)
    if not eligible:
        return RecommendationResponse(
            status="no_eligible",
            message=(
                f"В городе {request.city} есть {len(category_profiles)} профилей категории "
                f"«{request.category}», но ни один не подходит. Причины: {_reason_text(reasons)}. "
                "Причины могут пересекаться."
            ),
            cards=[],
            reasons=reasons,
            ai_mode="not_used",
        )

    selected = rank_profiles(eligible, request)[:3]
    evidence = generate_explanations(request, selected)
    cards = [
        Card(
            id=profile.id,
            name=profile.name,
            category=request.category,
            city=profile.city,
            price_from_kzt=profile.price_from_kzt,
            synthetic=profile.synthetic,
            city_imputed=profile.city_imputed,
            price_imputed=profile.price_imputed,
            explanation=_card_explanation(profile, request, evidence.quotes[profile.id]),
            evidence_quote=evidence.quotes[profile.id],
        )
        for profile in selected
    ]
    message = f"Подобрано подрядчиков: {len(cards)}."
    if len(cards) < 3:
        message += f" Меньше трёх: в городе всего {len(category_profiles)} профилей категории"
        if _reason_text(reasons):
            message += f"; не подошли по условиям: {_reason_text(reasons)}"
        message += "."
    return RecommendationResponse(
        status="matched", message=message, cards=cards, reasons=reasons, ai_mode=evidence.mode
    )
