# Список завдань (Task.md) - Nova Poshta AI Bot v0.12.1

- [x] **Крок 1: Базова структура та конфігурація**
  - [x] Створити структуру каталогу проєкту (`src/`, `tests/`, `docs/`)
  - [x] Додати `.gitignore`, `.env.example`, `requirements.txt` (з `python-barcode` та `Pillow`)
  - [x] Створити модуль конфігурації `src/config.py` з підтримкою трьох AI-провайдерів (OpenAI-compatible / OpenAI / Gemini)
  - [x] Створити модуль `src/storage.py` для збереження налаштувань, чернеток та реєстрів (`SavedScanSheet`)

- [x] **Крок 2: Інтеграція з Nova Poshta API**
  - [x] Створити асинхронний клієнт `src/nova_poshta/client.py`
  - [x] Валідація/пошук населеного пункту та відділень/поштоматів (`FindByString` та `WarehouseId`)
  - [x] Створення, список та видалення реєстрів (`ScanSheet/save`, `ScanSheet/getScanSheetList`, `ScanSheet/deleteScanSheet`)
  - [x] Автоматичний retry при `To many requests`

- [x] **Крок 3: Інтеграція з AI (OpenAI / Gemini / Local Web2API)**
  - [x] Розпізнавання запитів фільтрації накладних за датою, часом та описом вантажу
  - [x] Генерація штрих-кодів Code128 у модуль `src/utils/barcode_gen.py`
  - [x] 100% AI-ориєнтований парсинг без регулярних виразів з робастною валідацією Pydantic

- [x] **Крок 4: Telegram Bot Interface (aiogram 3)**
  - [x] Команда `/registers` та кнопка `📋 Реєстри (ScanSheet)` у постійному меню
  - [x] Фільтрація порожніх та вже відправлених реєстрів (`count_of_documents > 0`)
  - [x] Виправлення пріоритету обробки інтенту створення реєстру у `_handle_combined_text_message`
  - [x] Надсилання PNG штрих-коду реєстру у Telegram для сканування на відділенні
  - [x] Інтерактивне розформування / видалення реєстру (`RegisterActionCallback`)

- [x] **Крок 5: Тестування та Документація**
  - [x] Модульні тести — 16 passed
  - [x] Оновити `README.md` (англійською мовою)
  - [x] Оновити `docs/credentials_guide.md`
