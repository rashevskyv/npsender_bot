# План розробки Telegram-бота "Nova Poshta AI Waybill Generator"

## Опис проекту
Створення Telegram-бота на Python (aiogram 3.x), який за допомогою штучного інтелекту (з гнучким вибором OpenAI-сумісного API, офіційного OpenAI або Gemini) парсить неструктурований текст із реквізитами отримувача (ПІБ, телефон, місто, номер відділення/поштомату, опис, оціночна вартість) та генерує express-накладну (ТТН) Нової Пошти з інтерактивною звіркою, можливістю видалення створених чернеток та переглядом вихідних посилок.

## Основні компоненти (Виконано у v0.4.1)
1. [x] **AI Entity Extractor & Conversational Chat (`src/ai/`)**:
   - Підтримка 3-х AI провайдерів через `openai` / `httpx` SDK. Сумісність з `gpt-5.6-luna`, `gpt-4o-mini`, `o1`, `gemini-3.6-flash`.
   - Pydantic-структури для вилучення даних та розмовного інтенту (`is_recipient_info`, `conversational_response`).

2. [x] **Nova Poshta API Client (`src/nova_poshta/`)**:
   - `searchSettlements` / `getCities` — пошук міста за назвою.
   - `getWarehouses` — пошук конкретного відділення або поштомату за номером (`FindByString`).
   - `Counterparty/save` & `ContactPerson/save` — створення отримувача.
   - `InternetDocument/save` — створення ТТН (з автозаповненням `OptionsSeat` та мін 500 грн).
   - `InternetDocument/delete` — видалення створених ТТН/чернеток.
   - `InternetDocument/getDocumentList` — відображення списку вихідних посилок.

3. [x] **Telegram Bot Interface (`src/bot/`)**:
   - `aiogram 3.x` з асинхронними обробниками команд (`/start`, `/help`, `/settings`, `/parcels`, `/drafts`, `/set_np_key`, `/set_ai_key`, `/reset_settings`).
   - Керування збереженими чернетками ТТН з можливістю видалення через кнопку `🗑 Delete Waybill`.
   - Встановлення персональних API ключів для кожного користувача окремо.

4. [x] **Персональне збереження даних (`src/storage.py`)**:
   - Локальний менеджер збереження налаштувань користувача та створених чернеток.

5. [x] **Повна інтеграційна перевірка**:
   - Перевірено та підтверджено працездатність Telegram Bot, Nova Poshta API та OpenAI API z моделлю `gpt-5.6-luna`.
