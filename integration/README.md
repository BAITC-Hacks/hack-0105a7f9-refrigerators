# Основа для команды фронтенда

Начните с [инструкции подключения](../docs/FRONTEND_HANDOFF.md) и [AGENTS.md](../AGENTS.md).

- `api-client.mjs` — fetch-клиент, преобразование формы, timeout/отмена и защита от устаревшего ответа. Без зависимостей и выбора UI-фреймворка.
- `api-client.d.mts` — типизированный интерфейс клиента.
- `api-types.d.ts`, `openapi.json`, `examples/*.json` — сгенерированный контракт и реальные локальные ответы.
- `tests/api-client.test.mjs` — локальные Node-тесты с этими ответами.

Не редактируйте сгенерированные файлы вручную:

```sh
python -m contractor_match.export_contract
python -m contractor_match.export_contract --check
node --test integration/tests/api-client.test.mjs
```

При переносе клиента в frontend сохраняйте рядом `.mjs`, `.d.mts` и `api-types.d.ts`. Файлы схемы и примеров нужны для разработки, их не обязательно включать в production bundle. Репозиторий не содержит ключей и не требует внешнего AI для этих проверок.
