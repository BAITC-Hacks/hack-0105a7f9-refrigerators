"""Reproducible scenarios through the real pipeline, offline by default."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time

from .catalogue import DATA_FILE
from .config import load_settings
from .models import RecommendationRequest
from .ranking import RANKING_VERSION
from .service import recommend


BASE = dict(city="Алматы", date="2026-10-15", event_format="корпоратив", category="Ведущий", budget_kzt=1200000)
HOST = BASE | {"brief": "спокойный ведущий делового форума"}
SCENARIOS = [
    ("Ведущий: осень", HOST),
    ("Ведущий: декабрь", HOST | {"date": "2026-12-19"}),
    ("Редкая категория", BASE | {"category": "Флорист", "budget_kzt": 300000}),
    ("Категории нет в городе", BASE | {"city": "Астана", "category": "Декоратор"}),
    ("Условия не выполнены", BASE | {"city": "Астана", "category": "Флорист", "budget_kzt": 100000}),
    ("Площадка", BASE | {"category": "Банкетный зал", "budget_kzt": 5000000}),
    ("Пожелание с отрицанием", BASE | {"brief": "без шумных конкурсов"}),
    ("Фотограф: эмоции", BASE | {"category": "Фотограф", "brief": "живые эмоции и репортаж"}),
]


def run_demo() -> dict:
    results = []
    for name, fields in SCENARIOS:
        request = RecommendationRequest(**fields)
        started = time.perf_counter()
        response = recommend(request)
        results.append({
            "scenario": name, "request": request.model_dump(mode="json"),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "response": response.model_dump(mode="json"),
        })
    return {
        "python": platform.python_version(), "platform": platform.system(),
        "ranking_version": RANKING_VERSION,
        "data_sha256": hashlib.sha256(DATA_FILE.read_bytes()).hexdigest(),
        "scenarios": results,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Реальные демонстрационные запросы Firebird")
    parser.add_argument("--provider", choices=("local", "openai", "nvidia"), default="local")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    os.environ["AI_PROVIDER"] = args.provider
    if args.provider != "local" and not load_settings().key:
        parser.error(f"Для живого прогона задайте {args.provider.upper()}_API_KEY локально.")
    report = run_demo()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for result in report["scenarios"]:
            response = result["response"]
            print(f"\n{result['scenario']}: {response['status']} ({result['elapsed_ms']} мс)")
            print(response["message"])
            print(f"Режим: {response['ai_mode']}; причина: {response['ai_reason']}")
            for card in response["cards"]:
                print(f"  {card['id']} — {card['name']}: {card['explanation']}")
    slow = any(result["elapsed_ms"] > 10000 for result in report["scenarios"])
    fallback = args.provider != "local" and any(
        result["response"]["cards"] and result["response"]["ai_mode"] != args.provider
        for result in report["scenarios"]
    )
    return 1 if slow or fallback else 0


if __name__ == "__main__":
    raise SystemExit(main())
