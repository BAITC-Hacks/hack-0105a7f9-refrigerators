"""Conservative, visible rules for Russian briefs; no claims of general NLU."""
import re

from .catalogue import Profile
from .models import BriefUnderstanding, RecommendationRequest


STYLES = {"спокойный": r"\bспокойн\w*", "креативный": r"\bкреативн\w*",
          "традиционный": r"\bтрадиционн\w*"}
LANGUAGES = {"русский": r"\b(?:на|и|или)\s+русском\b", "казахский": r"\b(?:на|и|или)\s+казахском\b",
             "английский": r"\b(?:на|и|или)\s+английском\b"}
NEGATIVE = r"\b(?:без|не|никаких|никакого|никакой|исключить|исключая)\b"


def _negated(text: str, position: int) -> bool:
    # Deliberately conservative: mixed clauses may remain unrecognized.
    clause = re.split(r"[,;.!?]", text[:position])[-1]
    return bool(re.search(NEGATIVE, clause))


def understand(request: RecommendationRequest) -> tuple[RecommendationRequest, BriefUnderstanding]:
    text = (request.brief or "").casefold()
    result = BriefUnderstanding(effective_language=request.language,
                                language_source="field" if request.language else "unspecified")
    for name, pattern in STYLES.items():
        if any(not _negated(text, match.start()) for match in re.finditer(pattern, text)):
            result.styles.append(name)
    # Keep the user's negative fragment verbatim, not an invented positive promise.
    for match in re.finditer(r"\b(?:без|никаких|исключить|исключая)\s+[^,;.!?]+|\bне\s+[^,;.!?]+", text):
        result.unwanted.append(match.group(0).strip())
    for name, pattern in LANGUAGES.items():
        if any(not _negated(text, match.start()) for match in re.finditer(pattern, text)):
            result.languages.append(name)
    if request.language:
        if result.languages and result.languages != [request.language]:
            result.notes.append("Язык в отдельном поле отличается от текста; фильтр использует отдельное поле.")
    elif len(result.languages) == 1:
        result.effective_language = result.languages[0]
        result.language_source = "brief"
        request = request.model_copy(update={"language": result.effective_language})
        result.notes.append(f"Язык «{result.effective_language}» взят из пожелания и применён как фильтр.")
    elif len(result.languages) > 1:
        result.notes.append("В тексте несколько языков: уточните основной через поле language; языковой фильтр не применён.")
    if text:
        result.notes.append("Локальные правила распознают ограниченный набор фраз; остальные пожелания требуют уточнения.")
    return request, result


def card_details(profile: Profile, selected: list[Profile], request: RecommendationRequest,
                 understanding: BriefUnderstanding, quote: str) -> dict:
    why = [f"Город: {profile.city}; категория: {request.category}; формат: {request.event_format}.",
           f"По календарю каталога доступен {request.date.strftime('%d.%m.%Y')}.",
           f"Стартовая цена {profile.price_from_kzt:,} ₸ в пределах бюджета.".replace(",", " ")]
    clarify = ["Итоговую смету и актуальную доступность подтвердить у подрядчика."]
    if request.language:
        why.append(f"В профиле указан язык: {request.language}.")
    if request.duration_hours is not None and profile.max_hours is not None:
        why.append(f"Лимит {profile.max_hours:g} ч покрывает запрошенные {request.duration_hours:g} ч.")
    for style in understanding.styles:
        pattern = STYLES[style]
        matches = list(re.finditer(pattern, quote.casefold()))
        if matches and any(not _negated(quote.casefold(), match.start()) for match in matches):
            why.append(f"Стиль «{style}» заявлен в описании: «{quote}»")
        else:
            clarify.append(f"Стиль «{style}» не подтверждён выбранной цитатой — нужно уточнить.")
    for unwanted in understanding.unwanted:
        clarify.append(f"Пожелание «{unwanted}» нужно подтвердить отдельно; совпадение слов не служит доказательством.")
    if request.brief and not (understanding.styles or understanding.unwanted or understanding.languages):
        clarify.append("Соответствие свободному пожеланию целиком нужно уточнить у подрядчика.")
    if profile.price_imputed:
        clarify.append("Стартовая цена восстановлена в датасете и требует подтверждения.")
    if profile.city_imputed:
        clarify.append("Город восстановлен в датасете и требует подтверждения.")
    if profile.synthetic:
        clarify.append("Это синтетический профиль исходного учебного каталога.")
    others = [p for p in selected if p.id != profile.id]
    differences = []
    if others:
        minimum = min(p.price_from_kzt for p in selected)
        maximum = max(p.price_from_kzt for p in selected)
        if minimum != maximum:
            if profile.price_from_kzt == minimum:
                differences.append("Самая низкая стартовая цена среди показанных (возможна одинаковая цена).")
            else:
                delta = f"{profile.price_from_kzt - minimum:,}".replace(",", " ")
                differences.append(f"Стартовая цена на {delta} ₸ выше минимальной среди показанных.")
        only_languages = set(profile.languages) - {language for p in others for language in p.languages}
        if only_languages:
            differences.append(f"Среди показанных только у этого профиля указан язык: {', '.join(sorted(only_languages))}.")
        if all(quote not in p.description for p in others):
            differences.append(f"Собственная деталь описания: «{quote}»; это не доказывает отсутствие такого опыта у других.")
        if not differences:
            differences.append("По доступным полям и цитате подтверждённое отличие от остальных не найдено.")
    else:
        differences.append("Показан один кандидат; сравнивать с другими подходящими пока не с кем.")
    return dict(why_fits=why, to_clarify=clarify, differences=differences)
