from __future__ import annotations

import argparse
import json
import sys

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
    doctor = commands.add_parser("doctor", help="Проверить окружение, CSV и настройки без сетевых вызовов")
    doctor.add_argument("--json", action="store_true", help="Машиночитаемый отчёт")
    doctor.add_argument("--frontend-origin", help="Точный origin страницы, например http://localhost:5173")
    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure") and hasattr(sys.stderr, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        from .doctor import print_report, run_checks
        return print_report(run_checks(args.frontend_origin), as_json=args.json)

    # Keep doctor usable in a fresh Python environment before pip install.
    from pydantic import ValidationError
    from .config import ConfigurationError
    from .models import RecommendationRequest
    from .observability import configure_logging
    from .service import RecommendationInputError, recommend
    from .supabase_catalogue import CatalogueUnavailable
    configure_logging()
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
    except (ValidationError, RecommendationInputError) as error:
        print(f"Ошибка ввода: {error}", file=sys.stderr)
        return 2
    except ConfigurationError as error:
        print(f"Ошибка конфигурации: {error}", file=sys.stderr)
        return 1
    except CatalogueUnavailable as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"Ошибка каталога: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print(response.message)
        understood = response.understanding
        if request.brief:
            print(f"Поняли: стиль — {', '.join(understood.styles) or 'не распознан'}; "
                  f"язык — {understood.effective_language or 'не задан'}; "
                  f"нежелательно — {', '.join(understood.unwanted) or 'не распознано'}.")
            for note in understood.notes:
                print(f"  {note}")
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
            print(f"   Почему подходит: {card.explanation}")
            for note in card.to_clarify:
                print(f"   Уточнить: {note}")
            for difference in card.differences:
                print(f"   Отличие: {difference}")
        for alternative in response.alternatives:
            print(f"\nЧто изменить: {alternative.message}")
        if response.alternatives_note:
            print(response.alternatives_note)
        print(f"Ранжирование: {response.ranking_mode}; причина: {response.ranking_reason}")
        print(f"\nРежим объяснений: {response.ai_mode}; причина: {response.ai_reason}")
    return 0
