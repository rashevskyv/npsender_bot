# Список завдань (Task.md) - Nova Poshta AI Bot v0.10.0

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
  - [x] Вилучення полів області (`region_name`) та району (`district_name`) для розрізнення однакових міст
  - [x] 100% AI-ориєнтований парсинг без регулярних виразів з робастною валідацією Pydantic

- [x] **Крок 4: Telegram Bot Interface (aiogram 3)**
  - [x] Перевірка номера відділення/поштомату у всіх кандидатах однакових міст
  - [x] Інтерактивні кнопки вибору області/району (`CitySelectCallback`), якщо відділення існує в декількох одноіменних населених пунктах
  - [x] Постійне меню Reply Keyboard та асинхронний дибаунсер повідомлень

- [x] **Крок 5: Тестування та Документація**
  - [x] Проведено повну перевірку Telegram API, Nova Poshta API та OpenAI API
  - [x] Модульні тести — 12 passed
  - [x] Оновити `README.md` (англійською мовою)
  - [x] Оновити `docs/credentials_guide.md`
