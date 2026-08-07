# Список завдань (Task.md) - Nova Poshta AI Bot v0.8.0

- [x] **Крок 1: Базова структура та конфігурація**
  - [x] Створити структуру каталогу проєкту (`src/`, `tests/`, `docs/`)
  - [x] Додати `.gitignore`, `.env.example`, `requirements.txt`
  - [x] Створити модуль конфігурації `src/config.py` з підтримкою трьох AI-провайдерів (OpenAI-compatible / OpenAI / Gemini)
  - [x] Створити модуль `src/storage.py` для збереження персональних API ключів та чернеток

- [x] **Крок 2: Інтеграція з Nova Poshta API**
  - [x] Створити асинхронний клієнт `src/nova_poshta/client.py`
  - [x] Валідація/пошук населеного пункту та відділень/поштоматів (`FindByString` та `WarehouseId`)
  - [x] Створення контрагента та формування ТТН (`InternetDocument/save`) з масивом `OptionsSeat`
  - [x] Оновлення ТТН (`InternetDocument/update`) та видалення ТТН (`InternetDocument/delete`)

- [x] **Крок 3: Інтеграція з AI (OpenAI / Gemini / Local Web2API)**
  - [x] Повна відмова від регулярних виразів: AI Extractor самостійно розбирає адреси, відділення, вагові обмеження та описи вантажів
  - [x] Налаштовано `SYSTEM_PROMPT` для чіткого розмежування фізичних адрес відділень від особистої кур'єрської доставки

- [x] **Крок 4: Telegram Bot Interface (aiogram 3)**
  - [x] Створити постійне меню Reply Keyboard (`📦 Активні посилки`, `📝 Мої чернетки (ТТН)`, `⚙️ Налаштування`, `❓ Допомога`)
  - [x] Використання AI Extractor для парсингу збережених чернеток при натисканні `✏️ Редагувати ТТН`
  - [x] Функція `clear_user_active_session(user_id)` для автоматичного скидання контексту

- [x] **Крок 5: Тестування та Документація**
  - [x] Проведено повну перевірку Telegram API, Nova Poshta API та OpenAI API
  - [x] Модульні тести — 11 passed
  - [x] Оновити `README.md` (англійською мовою)
  - [x] Оновити `docs/credentials_guide.md`
