from __future__ import annotations

import math
import re
from collections import Counter
from functools import lru_cache

from .catalogue import Profile, load_catalogue
from .models import RecommendationRequest


RANKING_VERSION = "char-tfidf-3-5-v1"


def _grams(text: str) -> Counter[str]:
    result: Counter[str] = Counter()
    for word in re.findall(r"[\w]+", text.casefold(), flags=re.UNICODE):
        padded = f" {word} "
        for size in (3, 4, 5):
            result.update(padded[i : i + size] for i in range(len(padded) - size + 1))
    return result


class DescriptionIndex:
    """Small, fully local character n-gram TF-IDF index."""

    def __init__(self, texts: list[str]) -> None:
        document_grams = [_grams(text) for text in texts]
        frequency: Counter[str] = Counter()
        for grams in document_grams:
            frequency.update(grams.keys())
        count = len(texts)
        self.idf = {
            gram: math.log((count + 1) / (document_count + 1)) + 1
            for gram, document_count in frequency.items()
        }
        self.vectors = [self._vector(grams) for grams in document_grams]

    def _vector(self, grams: Counter[str]) -> dict[str, float]:
        weighted = {
            gram: (1 + math.log(freq)) * self.idf[gram]
            for gram, freq in grams.items()
            if gram in self.idf
        }
        norm = math.sqrt(sum(value * value for value in weighted.values()))
        return {gram: value / norm for gram, value in weighted.items()} if norm else {}

    def similarities(self, query: str) -> list[float]:
        vector = self._vector(_grams(query))
        return [
            sum(value * document.get(gram, 0.0) for gram, value in vector.items())
            for document in self.vectors
        ]


@lru_cache(maxsize=1)
def catalog_index() -> DescriptionIndex:
    return DescriptionIndex([profile.description for profile in load_catalogue()])


def _query(request: RecommendationRequest) -> str:
    if request.brief:
        # The category is already a hard filter; its repeated name can drown out
        # the more useful style and experience words in a short customer brief.
        brief = re.sub(re.escape(request.category), " ", request.brief, flags=re.IGNORECASE)
        return f"{brief} {request.event_format}".strip()
    return f"{request.category} {request.event_format}"


def rank_profiles(
    profiles: list[Profile], request: RecommendationRequest
) -> list[Profile]:
    catalogue = load_catalogue()
    scores = dict(zip((profile.id for profile in catalogue), catalog_index().similarities(_query(request))))
    return sorted(
        profiles,
        key=lambda profile: (-scores[profile.id], profile.price_from_kzt, profile.id),
    )


def quote_candidates(profile: Profile) -> list[str]:
    fragments = [
        fragment.strip()
        for fragment in re.split(r"(?<=[.!?])\s+|(?<=[.!?])(?=[A-ZА-ЯЁ])|\n+|•", profile.description)
        if fragment.strip()
    ]
    candidates = [fragment for fragment in fragments if len(fragment) >= 12] or fragments
    complete = [fragment for fragment in candidates if len(fragment) <= 280]
    candidates = complete or candidates
    excerpts = [
        fragment if len(fragment) <= 280 else fragment[:280].rsplit(" ", 1)[0].strip()
        for fragment in candidates
    ]
    return list(dict.fromkeys(excerpt for excerpt in excerpts if excerpt))


def has_negative_brief(request: RecommendationRequest) -> bool:
    return bool(request.brief and re.search(
        r"\b(?:без|не|никаких|никакого|никакой|исключить|исключая)\b", request.brief.casefold()
    ))


def evidence_options(profile: Profile, request: RecommendationRequest) -> list[str]:
    """Shortlist by text similarity; it is not proof that the whole brief is met."""
    excerpts = quote_candidates(profile)
    evidence_request = request.model_copy(update={"brief": None}) if has_negative_brief(request) else request
    query = _query(evidence_request) if evidence_request.brief else evidence_request.event_format
    if evidence_request.brief:
        hints = {
            "форум": "форум конференц бизнес",
            "делов": "делов конференц бизнес",
            "спокой": "спокой интеллигент ненавязчив мягк тонк юмор манер",
            "традиц": "традиц той казах",
            "креатив": "креатив оригинальн необыч",
            "эмоц": "эмоц атмосфер искрен естеств динамик момент",
            "репортаж": "репортаж незаметн момент событ",
        }
        for stem, related_words in hints.items():
            if stem in evidence_request.brief.casefold():
                query += f" {related_words}"
    scores = DescriptionIndex(excerpts).similarities(query)
    boilerplate = re.compile(
        r"топ[-\s]?\d|лучши[йемх]|идеальн|приветств|с уважением|всегда ваш|делает уровень|не сомнева", re.I
    )
    concrete = re.compile(
        r"\d|опыт|юмор|манер|подач|сценар|интерактив|импровиз|репортаж|эмоц|съ[её]м|"
        r"флорист|цветоч|оформ|оборуд|банкет|террас|панорам|язык|формат|конференц|тимбилдинг|"
        r"стиль|танц|развлеч|реч|незаметн|атмосфер", re.I
    )
    ordered = sorted(
        range(len(excerpts)),
        key=lambda index: (
            bool(boilerplate.search(excerpts[index])), not bool(concrete.search(excerpts[index])),
            -scores[index], index,
        ),
    )
    return [excerpts[index] for index in ordered[:3]]


def local_quotes(profiles: list[Profile], request: RecommendationRequest) -> dict[str, str]:
    """Pick distinct evidence across cards from the same shortlist used by AI."""
    quotes: dict[str, str] = {}
    for profile in profiles:
        options = evidence_options(profile, request)
        quotes[profile.id] = next((q for q in options if q not in quotes.values()), options[0])
    return quotes


def local_quote(profile: Profile, request: RecommendationRequest) -> str:
    return local_quotes([profile], request)[profile.id]
