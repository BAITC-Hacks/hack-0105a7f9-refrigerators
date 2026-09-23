# Подключение NVIDIA NIM на Brev

Статус: адаптеры и тесты готовы; GPU и NIM-сервер не создавались. Живой доступ, качество русского ранжирования, повторяемость GPU-оценок и время ответа ещё не проверены. Подготовка подключения не активирует кредиты и не расходует compute.

## Что подключается

```text
API / CLI → разбор пожелания → общие жёсткие фильтры
                              ├─ проверенные альтернативы, локально
                              └─ TF-IDF или NIM /v1/ranking → до 3 карточек
                                   → локальные цитаты или NIM /v1/chat/completions
                                   → общий валидатор цитат → объяснения и сравнение
```

Brev предоставляет машину, а NVIDIA NIM обслуживает модель. Название `brev` в настройках приложения означает обращение к вашему NIM endpoint; backend не вызывает управляющий Brev API. Reranker получает только описания уже прошедших фильтры профилей и текст запроса. Chat-модель получает только запрос и разрешённые цитаты выбранных карточек. Альтернативы не вызывают модели.

Возможен только reranker с локальными объяснениями. Embeddings отдельно не нужны для полного перебора 66 описаний: текущий адаптер отправляет прошедшие фильтры passages напрямую в reranker. Это не обещание улучшения качества: нужно сравнить ответы на русских запросах с TF-IDF.

## Подготовить существующий сервер

1. Владелец выбирает модель, GPU и лимит расходов. Проверяет доступ к образу, лицензию, поддерживаемые языки и требования памяти. Разные NIM могут требовать разные контейнеры и порты; не предполагается, что две модели поместятся на одном GPU.
2. Запустить NIM по [официальной инструкции Brev](https://docs.nvidia.com/brev/guides/inference-deployment/deploying-nims) и [инструкции reranking NIM](https://docs.nvidia.com/nim/nemo-retriever/reranking/2.0/getting-started.html). Контейнер и модель закрепить конкретной версией. Готовность проверить через `/v1/health/ready`, точное имя модели взять из `/v1/models`.
3. Подключить порт через SSH. Например, после настройки SSH-доступа к выбранной машине, если reranker слушает на ней порт 8000:

~~~powershell
# Замените nim-host своим SSH-псевдонимом из настроек Brev.
ssh -N -L 8002:127.0.0.1:8000 nim-host
~~~

Это окно держит туннель; backend запускается в другом терминале. Аналогично локальный порт 8001 можно направить на порт chat-сервиса. На Windows Brev CLI использует WSL; доступный SSH-клиент также может держать туннель. Не путайте ссылку браузерного туннеля с API: Brev предупреждает, что браузерная авторизация может возвращать redirect вместо JSON. См. [Console Reference](https://docs.nvidia.com/brev/guides/console-reference).

HTTP разрешён приложением только для `localhost`, `127.0.0.1`, `::1`. Для удалённого endpoint нужен HTTPS. URL задаётся администратором через окружение, заканчивается на `/v1`, не содержит ключей, query или fragment. Redirect не выполняется.

## Настроить Firebird

PowerShell, только ранжирование на NIM:

~~~powershell
$env:AI_PROVIDER = 'local'
$env:RANKING_PROVIDER = 'brev'
$env:NIM_RERANK_BASE_URL = 'http://127.0.0.1:8002/v1'
$env:NIM_RERANK_MODEL = '<точный id из /v1/models reranker>'
# Если ваш workload/proxy требует токен, сохраните его локально в NIM_RERANK_API_KEY.
~~~

Дополнительно включить chat-объяснения:

~~~powershell
$env:AI_PROVIDER = 'brev'
$env:NIM_BASE_URL = 'http://127.0.0.1:8001/v1'
$env:NIM_MODEL = '<точный id из /v1/models chat-сервера>'
# Если ваш workload/proxy требует токен, используйте NIM_API_KEY.
~~~

На Linux те же переменные задаются через `export ИМЯ='значение'`. `.env` автоматически не читается. Имена моделей обязательны; приложение не угадывает установленную модель. Для локального защищённого SSH-туннеля токен workload может быть пустым. Для опубликованного endpoint настройте собственную авторизацию перед открытием доступа.

`BREV_API_KEY` управляет ресурсами Brev. `NGC_API_KEY` используется при получении образов/моделей. `NIM_API_KEY` и `NIM_RERANK_API_KEY` — токены вашего workload/proxy, если он их требует. Backend не читает Brev/NGC credentials. См. [разделение ключей](https://docs.nvidia.com/brev/guides/api-keys). Значения не отправляйте в чат и не коммитьте.

## Проверить подключение

После настройки и запуска моделей выполнить явно:

~~~powershell
# Только reranker, локальные объяснения
.\.venv\Scripts\python.exe -m contractor_match.demo --story --ranking brev
# Reranker и chat NIM
.\.venv\Scripts\python.exe -m contractor_match.demo --story --ranking brev --provider brev
~~~

У первого прогона в непустых ответах ожидаются `ranking_mode=brev`, `ranking_reason=success`, `ai_reason=local_requested`. У второго дополнительно `ai_mode=brev`, `ai_reason=success`. JSON доступен через `--json`. Код выхода 1 означает fallback или ответ дольше 10 секунд. Обычное демо без этих флагов всегда использует local + tfidf, независимо от окружения.

На весь внешний этап выделено 6 секунд: reranker получает максимум 2 секунды, объяснения — оставшееся время. Нет повторных попыток и дополнительных провайдеров. Невалидные, пропущенные, дублированные индексы и NaN/Infinity отклоняются; fallback ранжирования возвращает TF-IDF и безопасный `ranking_reason`. Непроверенные цитаты заменяются локальными. Исходные hard filters действуют при любом сбое.

Контракт [NIM ranking](https://docs.nvidia.com/nim/nemo-retriever/reranking/2.0/use-the-api-openai.html): `query.text`, `passages[].text`, ответ `rankings[].index/logit`. Стабильные входные индексы, оценки, затем цена и id определяют порядок. При одинаковых оценках порядок воспроизводим; смена модели, GPU-вычислений или fallback может изменить его. Для защиты пока используйте проверенный TF-IDF, а NIM показывайте как экспериментальную опцию до живого сравнения. API chat соответствует [NVIDIA NIM Chat Completions](https://docs.nvidia.com/nim/large-language-models/latest/api-reference.html).

## Воспроизводимость и расходы

Для будущего Launchable зафиксировать GPU, версии NIM-контейнера и модели, commit этого репозитория, install-команду `python -m pip install -r requirements.txt`, переменные без секретов и readiness-проверки. [Launchables](https://docs.nvidia.com/brev/concepts/launchables) связывают окружение и настройку запуска; опубликованный Launchable в этой итерации не создавался.

Точную цену и остаток кредитов проверяет владелец в своём Billing перед созданием GPU. Запущенная машина расходует compute; после остановки может продолжаться плата за storage. Это отдельные расходы Brev, не баланс NVIDIA Build inference. См. [Billing & Usage](https://docs.nvidia.com/brev/guides/console-reference#billing--usage).
