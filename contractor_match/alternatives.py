"""Counterfactuals checked against all hard filters, with no network or recursion."""
from datetime import timedelta
from itertools import combinations, product

from .catalogue import Profile
from .eligibility import failures
from .models import Alternative, ConditionChange, FIRST_DATE, LAST_DATE, RecommendationRequest


def suggest_alternatives(
    request: RecommendationRequest, profiles: list[Profile], current: list[Profile]
) -> tuple[list[Alternative], str | None]:
    if not profiles:
        return [], "Смена даты, бюджета или длительности не создаст отсутствующую в городе категорию."
    if len(current) >= 3:
        return [], None
    profiles = [p for p in profiles if p.city == request.city and request.category in p.categories
                and request.event_format in p.event_formats
                and (not request.language or request.language in p.languages)]
    if not profiles:
        return [], "При текущих городе, категории, формате и языке смена даты, бюджета или часов не поможет."
    days = [FIRST_DATE + timedelta(days=i) for i in range((LAST_DATE - FIRST_DATE).days + 1)]
    # At equal distance prefer the later date; never look outside the known calendar.
    days.sort(key=lambda day: (abs((day - request.date).days), day < request.date))
    values = {
        "date": [day for day in days if day != request.date],
        "budget_kzt": sorted({p.price_from_kzt for p in profiles if p.price_from_kzt > request.budget_kzt}),
        "duration_hours": sorted({p.max_hours for p in profiles if p.max_hours is not None
                                  and request.duration_hours is not None
                                  and p.max_hours < request.duration_hours}, reverse=True),
    }
    current_ids = {p.id for p in current}

    def proposal(updates: dict) -> Alternative | None:
        # Updates come only from validated catalogue values and in-window dates.
        changed = request.model_copy(update=updates)
        eligible = sorted((p for p in profiles if not failures(p, changed)), key=lambda p: p.id)
        if len(eligible) <= len(current):
            return None
        changes = [ConditionChange(
            field=field,
            from_value=request.date.isoformat() if field == "date" else getattr(request, field),
            to_value=value.isoformat() if field == "date" else value,
        ) for field, value in updates.items()]
        labels = {
            "date": lambda value: f"дата {value.strftime('%d.%m.%Y')}",
            "budget_kzt": lambda value: f"бюджет {value:,} ₸".replace(",", " "),
            "duration_hours": lambda value: f"длительность {value:g} ч",
        }
        message = "; ".join(labels[key](value) for key, value in updates.items())
        prefix = "Изменить одно условие" if len(updates) == 1 else f"Изменить условий: {len(updates)}"
        return Alternative(
            changes=changes, request=changed, eligible_count=len(eligible),
            added_count=len({p.id for p in eligible} - current_ids),
            candidate_ids=[p.id for p in eligible],
            message=f"{prefix} — {message}: подходят {len(eligible)} (сейчас {len(current)}). "
                    "Проверено по календарю и стартовым ценам каталога; остальные условия сохранены.",
        )

    singles = []
    for field, options in values.items():
        for value in options:
            candidate = proposal({field: value})
            if candidate:
                singles.append(candidate)
                break
    if singles:
        return singles, ("Минимальное изменение каждого отдельного условия: ближайшая дата "
                         "(при равенстве — позднее), минимальное повышение бюджета или сокращение часов. "
                         "Варианты независимы и не применяются автоматически.")

    # No single change works. Enumerate combinations of threshold values; at 66
    # profiles/date window 100 days this remains small. Stop at the fewest fields.
    def cost_updates(updates):
        return (abs((updates.get("date", request.date) - request.date).days),
                updates.get("budget_kzt", request.budget_kzt) - request.budget_kzt,
                (request.duration_hours - updates.get("duration_hours", request.duration_hours))
                if request.duration_hours else 0)

    def cost(candidate):
        return cost_updates({change.field: getattr(candidate.request, change.field) for change in candidate.changes})

    frontier = []
    for size in (2, 3):
        for fields in combinations(values, size):
            for items in product(*(values[field] for field in fields)):
                updates = dict(zip(fields, items))
                candidate_cost = cost_updates(updates)
                if any(all(a <= b for a, b in zip(cost(other), candidate_cost)) for other in frontier):
                    continue
                candidate = proposal(updates)
                if candidate:
                    frontier = [other for other in frontier
                                if not all(a <= b for a, b in zip(candidate_cost, cost(other)))]
                    frontier.append(candidate)
        if frontier:
            break
    frontier.sort(key=lambda c: (*cost(c), c.request.date < request.date, c.request.date))
    if frontier:
        return frontier[:3], ("Одного изменения недостаточно. Показаны проверенные сочетания с минимальным "
                              "числом изменённых полей; затем приоритет ближайшей даты, бюджета и часов. "
                              "Это разные компромиссы, а не единственный лучший вариант.")
    return [], ("Проверены даты 23.09–31.12.2026, повышение бюджета до стартовых цен каталога "
                "и сокращение длительности. Эти изменения не увеличивают выбор при текущих городе, "
                "категории, формате и языке.")
