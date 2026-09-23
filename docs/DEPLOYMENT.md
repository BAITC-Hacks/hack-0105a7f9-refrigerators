# Общий сайт: Vercel + Supabase

## Архитектура и бесплатный режим

`Браузер → Vercel (app.py: сайт + FastAPI) → Supabase (каталог)`.
В публичном демо явно задаются `AI_PROVIDER=local`, `RANKING_PROVIDER=tfidf`.
Платные модели не вызываются. Тарифы: Vercel Hobby и Supabase Free; их квоты
и возможная пауза бесплатной базы продолжают действовать.

Vercel поддерживает FastAPI и Python 3.14: [FastAPI](https://vercel.com/docs/frameworks/backend/fastapi),
[Python runtime](https://vercel.com/docs/functions/runtimes/python).
Зависимости из requirements.txt ограничены проверенными версиями requirements-lock.txt.
`app.py` обслуживает `/web/`, единственный JS-клиент и прежние API-маршруты.
Корень перенаправляет на `/web/`. Исходники backend, `.env` и CSV не раздаются как файлы сайта.

## Локально: сайт и backend одной командой

После установки зависимостей из корня репозитория:

```powershell
$env:AI_PROVIDER = 'local'
$env:RANKING_PROVIDER = 'tfidf'
$env:CATALOGUE_PROVIDER = 'csv'
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --no-access-log
```

Открыть `http://127.0.0.1:8000/`. На публичном домене клиент использует тот же
origin. Прежний локальный `http.server 5173` по-прежнему обращается к API на 8000.

## Supabase: новая база, без пользовательских аккаунтов

1. Создать проект `firebird` на Free. Пароль базы сохранить в менеджере паролей.
   Data API включён, автоматически открывать новые таблицы выключено, RLS включён.
2. В SQL Editor один раз выполнить `supabase/migrations/202609230001_contractors.sql`.
3. Table Editor → `firebird_contractors` → Import CSV → `data/contractors.csv`.
   Использовать исходные имена колонок. Пустые max_hours должны стать SQL NULL;
   пустые busy_dates — пустой строкой (при необходимости задать default).
4. Проверить импорт:

```sql
select count(*) as profiles,
       count(*) filter (where synthetic) as synthetic
from public.firebird_contractors;
-- 66 и 13
select relrowsecurity from pg_class
where oid = 'public.firebird_contractors'::regclass;
-- true
```

Списки в таблице сохраняют исходный разделитель `|`. Числа и флаги имеют SQL-типы.
RLS и SQL grants запрещают анонимному пользователю и роли authenticated доступ
к таблице. Backend читает через серверный Secret key в заголовке `apikey`.
Не передавать этот ключ браузеру, в Git, чат или NEXT_PUBLIC/VITE-переменные.
Документация: [ключи Supabase](https://supabase.com/docs/guides/getting-started/api-keys),
[защита Data API](https://supabase.com/docs/guides/api/securing-your-api).

Режим `supabase` действительно читает базу. Он проверяет все 66 строк, типы,
доступность, описания и происхождение по исходному CSV. Изменённый/неполный каталог
не принимается. Это фиксированный набор хакатона: редактирование профилей не поддерживается.
Успешный снимок и TF-IDF кэшируются на срок жизни процесса; для обновления окружения
нужен redeploy. Тёплый процесс продолжает работать со своим проверенным снимком.
При неудачной первой загрузке API отвечает 503 `catalogue_unavailable`, без подмены CSV;
следующий запрос повторяет загрузку. `/health` проверяет наличие рабочего снимка,
а не делает новый запрос в базу при каждом обращении.

## Vercel: переменные Production

| Имя | Значение |
|---|---|
| AI_PROVIDER | local |
| RANKING_PROVIDER | tfidf |
| CATALOGUE_PROVIDER | supabase |
| SUPABASE_URL | HTTPS URL вашего проекта Supabase |
| SUPABASE_SECRET_KEY | Secret key из API Keys, тип Secret в Vercel |
| CORS_ALLOWED_ORIGINS | пустая строка при едином домене |

Ключи AI не нужны. Не включать платный AI на публичной форме без отдельного контроля
доступа и расходов. При отдельном frontend origin перечислить точные адреса в CORS.

Текущий GitHub-репозиторий приватный и находится в организации. Его прямая Git-интеграция
не доступна на Hobby: [ограничения Git](https://vercel.com/docs/git).
Для публикации из локальной рабочей копии используется официальный CLI:

```sh
npx vercel login
npx vercel link --project firebird
npx vercel --prod
```

Выбирать свой Hobby-аккаунт. `.vercel/` и `.env*` игнорируются Git.
Если CLI сообщает об ограничении плана, не менять приватность репозитория и не
включать Pro автоматически. Автоматического деплоя каждого push без Git-интеграции нет:
после обновления main повторить CLI deployment. GitHub Actions остаётся выключен.

## Проверка перед публикацией

```sh
python -m unittest discover -s tests -v
python -m contractor_match.export_contract --check
node --test integration/tests/api-client.test.mjs
python -m contractor_match.demo --story
python -m contractor_match doctor
```

Doctor всегда offline, даже с Supabase-переменными. Он проверяет настройки и эталонный
CSV; это не подтверждение подключения к базе. Экспорт контракта и демо используют CSV.
После деплоя открыть `/health`, `/docs`, главную страницу и выполнить запросы:

- Алматы, ведущий, корпоратив, 1 200 000 ₸: 15.10.2026 → 6 подходящих / 3 карточки;
  19.12.2026 → 1 подходящий / 1 карточка. Применить предложенную соседнюю дату.
- Астана, декоратор → `category_absent`.
- Астана, флорист, 15.10.2026, 100 000 ₸ → `no_eligible`.

Проверить `/data/contractors.csv` и `/.env`: 404. Проверить номер обращения в ошибках.
Фактические облачные адреса и результат живого прогона фиксируются в PROJECT_STATE.md.
