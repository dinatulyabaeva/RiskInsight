# RiskInsight: запустить и подготовить к защите

## 1. Распакуйте архив

Внутри папка `riskinsight`. Откройте в ней терминал. Требуется Python 3.11 или новее.

## 2. Запустите DEMO

macOS/Linux:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Windows PowerShell:
```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Откройте `http://localhost:8000`. Выберите готовый сценарий. Метрики и MCP настоящие; текст шаблонный, LLM не вызывается.

## 3. Включите модель

В `.env`: APP_MODE=llm, LLM_API_KEY, LLM_MODEL и APP_ACCESS_TOKEN. Нужна модель с Chat Completions tool calling. Перезапустите сервер. В браузере введите APP_ACCESS_TOKEN — НЕ ключ модели. Выполните `python -m scripts.check_llm`. Приложение должно показывать LLM, а журнал — реальные вызовы.

## 4. Проверьте код

```bash
pip install -r requirements-dev.txt
python -m pytest -q
python -m scripts.smoke
```

Независимый MCP-клиент:
```bash
pip install -r requirements-sdk.txt
python -m scripts.sdk_smoke
```

## 5. Опубликуйте

Инструкции GitHub/Render и секретные переменные подробно описаны в README. Публичных адресов в поставке нет: это внешний шаг под вашим аккаунтом. После публикации добавьте реальные URL в README и проверьте сервис извне.

## 6. Откройте презентацию

`docs/presentation.html` или `http://localhost:8000/presentation`. Стрелки переключают слайды; N показывает заметки; F включает полноэкранный режим. Презентация автономная, срезы встроены, LLM из нее не вызывается.

Для подготовки выступления используйте `docs/DEFENSE.md`.

## Что нельзя пропустить

DEMO не закрывает обязательный AI-критерий. Нужны live LLM smoke-test, публичный репозиторий, работающий HTTPS-сервис и обязательный учебный воркшоп. Не выкладывайте .env, реальные клиентские данные и ключи API.
