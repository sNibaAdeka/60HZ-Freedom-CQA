# Freedom CQA — команда −60HZ

Прототип **Conversation Quality Analytics** для Freedom Broker (NU Data Challenge '26).
Оценивает работу оператора в чатах и звонках (RU / KZ / EN) по настраиваемому чек-листу. По каждому критерию
даёт объяснение и реплику-доказательство, поддерживает апелляцию и решение проверяющего.

> 🚧 Работа в процессе (хакатон, 7–8 ноября 2026). README будет дополняться.

## Возможности
- **Чек-лист**: критерии, формулировки, вес, тип ответа (да/нет, шкала), N/A; версии конфигурации.
- **Оценка**: LLM-судья и детерминированные правила. Реплики бота и системы используются как контекст, каждая цитата проверяется по транскрипту.
- **Результат**: ответ, баллы, вес, объяснение, переход к реплике или к моменту в записи звонка; итог с перераспределением веса при N/A.
- **Апелляция и пересмотр**: оспорить критерий, решение проверяющего с причиной, пересчёт итога, история «исходная оценка → решение → финал».
- **Интерфейс**: HTML + CSS + JavaScript без сборки, можно встроить как веб-ресурс Dynamics 365.

## Запуск
```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-cqa.txt

# ключи (файл .env не коммитится)
echo 'CQA_DATA_API="<ссылка GET из Student Guide без group_id>"' >> .env
export ANTHROPIC_API_KEY=...          # без ключа работает mock-режим на правилах

python scripts/fetch_dataset.py DEV   # данные через API организаторов -> data/raw/
uvicorn server:app --port 8000        # http://localhost:8000
```

Пакетная оценка (например, для контрольного набора):
```bash
python scripts/fetch_dataset.py <GROUP_ID>
python scripts/cqa_batch.py data/raw --out results/control --workers 8
```

## Структура
```
cqa/        ядро: loader (форматы диалогов), checklist, rules (RU/KZ/EN), llm (провайдеры, кэш), judge, store (SQLite)
server.py   FastAPI: диалоги, оценка, апелляции, экспорт
web/        одностраничный интерфейс (HTML/CSS/JS)
scripts/    fetch_dataset.py, cqa_batch.py, cqa_eval.py
data/sample синтетические примеры для демо без данных
```

## Модель и стоимость
По умолчанию используется Claude Sonnet через Anthropic API, также подходит любой OpenAI-совместимый endpoint (переменные `CQA_PROVIDER`, `CQA_MODEL`, `CQA_BASE_URL`).
Стоимость одной оценки будет указана после замеров.

Данные организаторов (реальные диалоги) в репозиторий не попадают.
