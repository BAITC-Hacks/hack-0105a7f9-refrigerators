from __future__ import annotations

import argparse
import json
import sys

from pydantic import ValidationError

from .models import RecommendationRequest
from .service import recommend


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Подбор event-подрядчиков")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("recommend", help="Подобрать до трёх подрядчиков")
    command.add_argument("--city", required=True)
    command.add_argument("--date", required=True, help="ГГГГ-ММ-ДД")
    command.add_argument("--event-format", required=True)
    command.add_argument("--category", required=True)
    command.add_argument("--budget-kzt", required=True, type=int)
    command.add_argument("--duration-hours", type=float)
    command.add_argument("--language")
    command.add_argument("--brief")
    command.add_argument("--json", action="store_true", help="Вывести тот же JSON, что и API")
    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        request = RecommendationRequest(
            city=args.city,
            date=args.date,
            event_format=args.event_format,
            category=args.category,
            budget_kzt=args.budget_kzt,
            duration_hours=args.duration_hours,
            language=args.language,
            brief=args.brief,
        )
        response = recommend(request)
    except (ValidationError, ValueError) as error:
        print(f"Ошибка ввода: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print(response.message)
        for index, card in enumerate(response.cards, 1):
            price = f"{card.price_from_kzt:,}".replace(",", " ")
            tags = [
                label
                for flag, label in (
                    (card.synthetic, "синтетический профиль"),
                    (card.city_imputed, "город восстановлен"),
                    (card.price_imputed, "цена оценочная"),
                )
                if flag
            ]
            print(f"\n{index}. {card.name} ({card.id}) — {card.category}, {card.city}")
            print(f"   Цена от {price} ₸" + (f" [{', '.join(tags)}]" if tags else ""))
            print(f"   {card.explanation}")
        print(f"\nРежим объяснений: {response.ai_mode}")
    return 0
