# Диагностика Firebird

## 1. Проверка окружения одной командой

Из корня проекта, PowerShell:

```powershell
.\.venv\Scripts\python.exe -m contractor_match doctor
.\.venv\Scripts\python.exe -m contractor_match doctor --frontend-origin http://localhost:5173
.\.venv\Scripts\python.exe -m contractor_match doctor --json
```

На Linux/macOS замените путь на `.venv/bin/python`. До установки зависимостей можно выполнить `python -m contractor_match doctor`: команда использует стандартную библиотеку и сообщит, какие пакеты отсутствуют.

Проверяются версия Python, установленные версии из requirements-lock.txt, конфигурация объяснений и ранжирования, CORS, схема CSV, 66 профилей/13 synthetic, SHA-256 исходного файла и построение локального индекса. Файл читается заново с диска. Кэш уже работающего сервера при этом не обновляется.

`--frontend-origin` — точный origin из адресной строки, без пути и завершающего `/`. Если он не разрешён, doctor подскажет изменить CORS_ALLOWED_ORIGINS и перезапустить API. Без этого флага проверяется только корректность настройки CORS, а не доступ конкретной страницы.

| Итог | Что означает | Код выхода |
|---|---|---:|
| OK / ok | Локальные проверки пройдены | 0 |
| ВНИМАНИЕ / warning | Сервис может работать, но есть отличие версий/данных или ожидаемый fallback | 0 |
| ОШИБКА / error | Исправить конфигурацию, origin, каталог или зависимости | 1 |
| ПРОПУСК / skipped у пункта | Проверке не хватило зависимостей или корректного каталога | Зависит от общего итога |

Пример: `AI_PROVIDER=openai` без ключа даёт предупреждение о `missing_key`; `AI_PROVIDER=local` — нормальный режим. Для auto отсутствие ключей означает локальные объяснения. Отчёт показывает только наличие ключа, значение не выводится. У NIM ключ может быть необязательным.

**Doctor не вызывает сеть и модели, не запускает API и не подтверждает, что ключ действителен.** В JSON всегда `live_provider_checked: false`. Живой AI проверяется отдельным явно выбранным запуском из README/BREV.md. Doctor также не заменяет автотесты и браузерную проверку сайта.

## 2. Номер запроса для фронтенда

Каждый HTTP-запрос к приложению получает новый серверный UUID в `X-Request-ID` — 32 шестнадцатеричных символа. Входящий заголовок с этим именем не используется. Номер приходит и для обработанных ошибок 404/405/422/500/503, а также для OPTIONS. Для браузера заголовок открыт через CORS на фактическом ответе API.

`X-Process-Time-Ms` — время работы сервера до начала ответа, в миллисекундах. Это не полное время ожидания браузера с передачей данных по сети. Оба заголовка описаны в OpenAPI. Тело бизнес-ответа сохраняет прежнюю схему.

Клиент `integration/api-client.mjs` сохраняет номер в `ApiClientError.requestId`, включая HTTP-ошибки и неверный формат ответа. Если заголовки уже пришли, номер сохраняется и при timeout чтения тела. Если сеть оборвалась до ответа, номера может не быть — не создавайте выдуманный код ошибки.

```javascript
try {
  const result = await latest.recommend(request);
  if (result !== null) renderResult(result);
} catch (error) {
  const suffix = error.requestId ? ` Код обращения: ${error.requestId}` : '';
  showError(error.message + suffix); // показать как текст
}
```

В примере renderResult/showError — функции вашего интерфейса. Для успешного запроса номер доступен в DevTools → Network → Response Headers. При обращении об ошибке передайте номер и условия воспроизведения разработчику.

## 3. Найти запрос в логах

Приложение пишет JSON-записи в stderr. В CLI `recommend --json` stdout по-прежнему содержит только JSON результата. При прямом использовании Python-библиотеки настройка обработчиков не меняется автоматически; её выполняют startup API и CLI recommend.

Для сохранения журнала локального API:

```powershell
New-Item -ItemType Directory -Force logs | Out-Null
$env:AI_PROVIDER = 'local'
$env:RANKING_PROVIDER = 'tfidf'
.\.venv\Scripts\python.exe -m uvicorn contractor_match.api:app --host 127.0.0.1 --port 8000 --no-access-log --log-level warning 2> logs/backend.log
```

В другом терминале:

```powershell
Select-String -Path logs/backend.log -SimpleMatch '<номер из X-Request-ID>'
```

Linux/macOS: `mkdir -p logs`, тот же запуск с `.venv/bin/python`, поиск `rg '<номер>' logs/backend.log`. Папка logs игнорируется Git.

`--no-access-log` отключает дублирующий стандартный журнал Uvicorn, который может содержать сырой URL. `--log-level warning` сокращает служебный вывод Uvicorn; уровень JSON-журнала приложения остаётся INFO. В stderr также могут появляться предупреждения сторонних библиотек.

| event | Полезные поля |
|---|---|
| http_request | request_id, route, method, http_status, duration_ms |
| recommendation_complete | status, counts, reasons, stage_ms, ai_mode/ai_reason, ranking_mode/ranking_reason |
| recommendation_failed | failed_stage, error_type, stage_ms, duration_ms |
| ai_fallback / ranking_fallback | provider, reason, тот же request_id |
| api_internal_error | exception.type и frames: файл, функция, строка |

Все записи содержат UTC timestamp. Для HTTP используется шаблон маршрута, неизвестный путь обозначается `unmatched`. Сырые URL/query, заголовки, IP, brief, описания, ключи и ответы провайдера в эти записи не входят. При 500 сохраняются последние 12 кадров стека без текста исключения, исходных строк, локальных переменных и абсолютных путей. В HTTP-ответ стек не передаётся. Настройки журналов сторонних библиотек приложение не меняет.

`stage_ms` содержит только реально выполненные этапы:

- configuration — проверка настройки провайдеров;
- understanding — локальный разбор пожелания;
- catalogue — получение каталога, обычно из кэша;
- scope — проверка и выбор города/категории;
- filters — проверка условий кандидатов;
- alternatives — поиск проверенных изменений;
- ranking — TF-IDF или NIM с возможным fallback;
- explanations — выбор, проверка цитат и локальный fallback при необходимости.

При пустой выдаче ranking/explanations отсутствуют: эти этапы не выполнялись. `duration_ms` включает также работу между этапами; сумма stage_ms может быть меньше. HTTP-замер дополнительно включает обработку и сериализацию ответа. Параллельные запросы имеют независимый контекст; CLI получает собственный номер подбора.

## Быстрый разбор проблем

| Наблюдение | Что проверить |
|---|---|
| 422 | error.details на фронтенде: строковый бюджет, дата, обязательные поля |
| Нет HTTP-ответа / CORS | Адрес API, запущен ли сервер, doctor --frontend-origin, вкладка Network |
| ai_mode=fallback | ai_reason: local_requested — выбран local; missing_key — нет ключа; timeout/auth_error — сбой внешнего вызова |
| Ответ медленный | stage_ms: alternatives, ranking, explanations; затем общий HTTP duration_ms |
| 500 | Найти request_id, затем api_internal_error.frames и recommendation_failed.failed_stage |
| Изменили CSV или окружение | Перезапустить сервер; doctor проверяет файлы и окружение своего процесса |

Проверки реализации: `python -m unittest discover -s tests -v`, `node --test integration/tests/api-client.test.mjs`, `python -m contractor_match.export_contract --check`. Они включают параллельность, безопасные кадры стека, отсутствие личного текста в логах и запуск doctor без site-packages.
