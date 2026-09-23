"""Offline diagnostics; imports only stdlib until dependency checks have run."""
from __future__ import annotations

import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import sys
import time


ROOT = Path(__file__).resolve().parent.parent
EXPECTED_DATA_SHA256 = "6a724b6b7dfb5973343e68ba18dadb60fc807d87e3d78f03ee86fb26cb089f7d"


def _dependencies() -> tuple[str, str, dict]:
    installed, missing, mismatched = {}, [], []
    for line in (ROOT / "requirements-lock.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-r ")):
            continue
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9.+!_-]*)", line)
        if not match:
            return "error", "Не удалось прочитать зафиксированные зависимости.", {}
        name, expected = match.groups()
        try:
            actual = metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
            continue
        installed[name] = actual
        if actual != expected:
            mismatched.append(name)
    details = {"installed": installed, "missing": missing, "different_versions": mismatched}
    if missing:
        return "error", "Не все зависимости установлены. Выполните python -m pip install -r requirements-lock.txt.", details
    if mismatched:
        return "warning", "Версии отличаются от проверенных. Установите requirements-lock.txt или повторите тесты и экспорт.", details
    return "ok", "Зависимости соответствуют requirements-lock.txt.", details


def run_checks(frontend_origin: str | None = None) -> dict:
    checks = []

    def add(name, state, message, **details):
        checks.append({"name": name, "status": state, "message": message, **details})

    version = ".".join(map(str, sys.version_info[:3]))
    add("python", "ok" if sys.version_info >= (3, 11) else "error",
        f"Python {version}; требуется 3.11 или новее.")
    try:
        state, message, details = _dependencies()
        add("dependencies", state, message, **details)
        have_dependencies = state != "error"
    except Exception:
        add("dependencies", "error", "Не удалось проверить зависимости. Проверьте requirements-lock.txt и окружение.")
        have_dependencies = False

    # Settings/CORS use stdlib and can still be checked before pip install.
    from .config import ConfigurationError, load_cors_origins, load_ranking_settings, load_settings
    try:
        settings = load_settings()
        if settings.requested_provider == "local":
            add("ai", "ok", "Выбраны локальные объяснения; внешний ключ не требуется.", provider="local")
        elif settings.provider == "local":
            add("ai", "warning", "Auto не нашёл API-ключей: объяснения будут локальными.", provider="local", key_present=False)
        elif settings.provider != "brev" and not settings.key:
            add("ai", "warning", "Для выбранного провайдера нет ключа: будет fallback с причиной missing_key.",
                provider=settings.provider, key_present=False)
        else:
            add("ai", "ok", "Настройки объяснений заполнены. Доступ к модели не проверялся; для NIM ключ может быть необязателен.",
                provider=settings.provider, key_present=bool(settings.key))
    except ConfigurationError as error:
        add("ai", "error", str(error))  # ConfigurationError contains only safe static instructions.
    try:
        ranking = load_ranking_settings()
        add("ranking", "ok", "Выбран локальный TF-IDF." if ranking.provider == "tfidf"
            else "Настройки NIM ранжирования заполнены. Доступ к модели не проверялся.", provider=ranking.provider)
    except ConfigurationError as error:
        add("ranking", "error", str(error))
    try:
        origins = load_cors_origins()
        if frontend_origin is not None and frontend_origin not in origins:
            add("cors", "error", "Указанный origin фронтенда не разрешён. Добавьте его в CORS_ALLOWED_ORIGINS и перезапустите API.",
                allowed_count=len(origins), frontend_allowed=False)
        else:
            add("cors", "ok", f"Разрешено origins: {len(origins)}. " +
                ("Origin фронтенда разрешён." if frontend_origin is not None else "Для проверки страницы укажите --frontend-origin."),
                allowed_count=len(origins), frontend_allowed=True if frontend_origin is not None else None)
    except ConfigurationError as error:
        add("cors", "error", str(error))

    profiles = None
    if have_dependencies:
        try:
            from .catalogue import read_catalogue
            path = ROOT / "data" / "contractors.csv"
            # Read from disk; do not reuse or change the running server's caches.
            profiles = read_catalogue(path)
            synthetic = sum(profile.synthetic for profile in profiles)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if len(profiles) != 66 or synthetic != 13:
                add("catalogue", "error", "Ожидаются 66 профилей, включая 13 синтетических.",
                    profiles=len(profiles), synthetic=synthetic)
                profiles = None
            else:
                unchanged = digest == EXPECTED_DATA_SHA256
                add("catalogue", "ok" if unchanged else "warning",
                    "Каталог загружен: 66 профилей, 13 синтетических. " +
                    ("Исходный файл подтверждён." if unchanged else "Содержимое отличается от исходного файла; проверьте изменения и перезапустите API."),
                    profiles=len(profiles), synthetic=synthetic, sha256=digest)
        except Exception:
            # CSV/parser errors can contain source text. Keep them out of the report.
            add("catalogue", "error", "Не удалось прочитать или проверить data/contractors.csv. Проверьте наличие файла и схему CSV.")
    else:
        add("catalogue", "skipped", "Проверка каталога отложена до установки зависимостей.")
    if profiles is not None:
        try:
            from .ranking import DescriptionIndex, RANKING_VERSION
            start = time.perf_counter()
            index = DescriptionIndex([profile.description for profile in profiles])
            assert len(index.vectors) == len(profiles)
            add("index", "ok", "Локальный индекс построен.", ranking_version=RANKING_VERSION,
                duration_ms=round((time.perf_counter() - start) * 1000, 3))
        except Exception:
            add("index", "error", "Не удалось построить локальный индекс. Проверьте каталог и зависимости.")
    else:
        add("index", "skipped", "Построение индекса отложено до успешной проверки каталога.")
    status = "error" if any(item["status"] == "error" for item in checks) else (
        "warning" if any(item["status"] == "warning" for item in checks) else "ok")
    return {"status": status, "live_provider_checked": False,
            "message": "Локальная диагностика. Сеть и внешние модели не вызывались; работа запущенного сервера не проверялась.",
            "checks": checks}


def print_report(report: dict, *, as_json: bool = False) -> int:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        labels = {"ok": "OK", "warning": "ВНИМАНИЕ", "error": "ОШИБКА", "skipped": "ПРОПУСК"}
        print(report["message"])
        for check in report["checks"]:
            print(f"[{labels[check['status']]}] {check['name']}: {check['message']}")
            if check["name"] == "dependencies":
                for key, title in (("missing", "Отсутствуют"), ("different_versions", "Другие версии")):
                    if check.get(key):
                        print(f"  {title}: {', '.join(check[key])}")
        print(f"Итог: {labels[report['status']]}")
    return 1 if report["status"] == "error" else 0
