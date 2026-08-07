# План розробки Telegram-бота "Nova Poshta AI Waybill Generator"

## Опис проекту
Створення Telegram-бота на Python (aiogram 3.x), який за допомогою штучного інтелекту (з гнучким вибором OpenAI-сумісного API, офіційного OpenAI або Gemini) парсить неструктурований текст із реквізитами отримувача (ПІБ, телефон, місто, номер відділення/поштомату) та генерує express-накладну (ТТН) Нової Пошти з попередньою інтерактивною звіркою даних.

## Основні компоненти (Виконано у v0.1.2)
1. [x] **AI Entity Extractor (`src/ai/`)**:
   - Гнучка підтримка 3-х AI провайдерів через `openai` / `httpx` SDK:
     - OpenAI-compatible (наприклад, local `gemini-web2api`, Base URL: `http://localhost:8081/v1`, модель: `gemini-3.6-flash`).
     - Офіційний OpenAI API (`gpt-4o-mini` тощо).
     - Офіційний Gemini API.
   - Pydantic-структури для вилучення даних: `RecipientName`, `Phone`, `City`, `WarehouseNumber`, `WarehouseType` (відділення/поштомат/адреса).

2. [x] **Nova Poshta API Client (`src/nova_poshta/`)**:
   - `searchSettlements` / `getCities` — пошук міста за назвою.
   - `getWarehouses` — пошук конкретного відділення або поштомату за номером та Ref міста.
   - `Counterparty/save` & `ContactPerson/save` — створення отримувача.
   - `InternetDocument/save` — створення ТТН (з можливістю вибору платника: Отримувач / Відправник).

3. [x] **Telegram Bot Interface (`src/bot/`)**:
   - `aiogram 3.x` з асинхронним обробником текстових повідомлень.
   - Відображення розпарсених даних для перевірки користувачем перед формуванням ТТН.
   - Інтерактивна Inline-клавіатура: зміна платника (Отримувач/Відправник), вибір типу вантажу (посилка/документи), кнопка "Підтвердити та створити ТТН", "Скасувати".

4. [x] **CLI помічник для відправника (`src/cli/fetch_sender_info.py`)**:
   - Автоматичний пошук Ref відправника (`CounterpartyRef`, `ContactSenderRef`, `CitySenderRef`, `SenderAddressRef`) за ключем НП та автоматичне заповнення `.env`.

5. [x] **Конфігурація та Безпека**:
   - `.env` та `.env.example` без збереження чутливих даних у Git.
   - Підготовка до розгортання на віддаленому сервері (systemd / Docker).
