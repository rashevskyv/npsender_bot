# Список завдань (Task.md) - Nova Poshta AI Bot v0.19.3

- [x] **Крок 1: Базова структура та конфігурація**
  - [x] Створити структуру каталогу проєкту (`src/`, `tests/`, `docs/`)
  - [x] Додати `.gitignore`, `.env.example`, `requirements.txt` (з `python-barcode` та `Pillow`)
  - [x] Створити модуль конфігурації `src/config.py` з підтримкою трьох AI-провайдерів (OpenAI-compatible / OpenAI / Gemini)
  - [x] Створити модуль `src/storage.py` для збереження налаштувань, чернеток та реєстрів (`SavedScanSheet`)

- [x] **Крок 2: Інтеграція з Nova Poshta API**
  - [x] Створити асинхронний клієнт `src/nova_poshta/client.py`
  - [x] Валідація/пошук населеного пункту та відділень/поштоматів (`FindByString` та `WarehouseId`)
  - [x] Роздільне формування вихідних (`get_outgoing_waybills`) та вхідних (`get_incoming_waybills`) посилок
  - [x] Створення, список та видалення реєстрів (`ScanSheet/save`, `ScanSheet/getScanSheetList`, `ScanSheet/deleteScanSheet`)
  - [x] Фільтрація реєстрів за датою (до 2 днів) та статусом відправлення (`Printed` та статус ТТН)
  - [x] Отримання live-статусів ТТН (`TrackingDocument/getStatusDocuments`) та очищення відправлених чернеток
  - [x] Автоматичний retry при `To many requests`

- [x] **Крок 3: Інтеграція AI-об'єднання чернеток у реєстри (v0.19.0)**
  - [x] Додати Pydantic схему `AIRegisterFilterResult` у `src/ai/schemas.py`
  - [x] Реалізувати метод `filter_drafts_for_register` у `src/ai/extractor.py` з передачею JSON активних чернеток та контексту часу
  - [x] Реалізувати єдину функцію синхронізації невідправлених чернеток `fetch_user_active_drafts` у `src/bot/handlers.py`
  - [x] Оновити `_handle_combined_text_message` для створення реєстрів на основі вибору AI та генерації штрих-коду Code128 з розширеним описом накладних
  - [x] Підтримка вибірки за будь-якими критеріями (номери ТТН, "усі чернетки", дати "вчора/сьогодні", місто, опис вантажу, наложка)

- [x] **Крок 4: Тестування та Документація**
  - [x] Написати модульні тести для `filter_drafts_for_register` та повного циклу реєстрів (28 passed)
  - [x] Запустити всі тести
  - [x] Оновити `README.md` та `Walkthrough.md` українською мовою
