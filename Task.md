# Список завдань (Task.md) - Nova Poshta AI Bot v0.24.10

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

- [x] **Крок 4: Підтримка адресної доставки, вибір вулиць, збереження стану сесії, переслані повідомлення та автолікування сутностей (v0.19.4 - v0.19.8)**
  - [x] Додати моделі `StreetInfo` та `AddressSaveResult` у `src/nova_poshta/models.py`
  - [x] Реалізувати інтелектуальний пошук вулиць `search_street` з перестановками слів та ранжуванням
  - [x] Реалізувати метод `create_counterparty_address` у `src/nova_poshta/client.py`
  - [x] Спростити `WaybillActionCallback` (action, session_id), усунувши ліміт 64 байти в Telegram
  - [x] Додати `StreetSelectCallback` та клавіатуру вибору вулиці/провулку при декількох точних співпадіннях
  - [x] Збереження всіх змінених користувачем параметрів сесії (платник, вантаж, оцінка, наложка) при текстовому редагуванні
  - [x] Підтримка природномовного редагування будь-яких полів накладної (ПІБ, телефон, місто, адреса/відділення, опис, оцінка, наложка, платник)
  - [x] Ігнорування заголовків пересилання (`"Переслано від..."`), підтримка ініціалів (`Мартинюк Є.В.`) та суржикових поштоматів
  - [x] Впровадження автолікування сутностей `heal_parsed_recipient_info` для захисту від збоїв або неповних відповідей AI-моделей
  - [x] Повне очищення сесії при створенні або скасуванні ТТН у `clear_user_active_session`

- [x] **Крок 5: Синхронізація та надійне видалення чернеток ТТН (v0.19.9 - v0.19.10)**
  - [x] Розпізнавання статусів видалених/неіснуючих накладних (`StatusCode 2`, `3`, `"Видалено"`, `"Номер не знайдено"`) та відмов (`103`) у `get_documents_status`
  - [x] Автоматичне вилучення неактивних ТТН із локальної бази `user_drafts.json` у `fetch_user_active_drafts`
  - [x] Очищення фільтрацією у `get_internet_document_list` для ігнорування `DeletionMark`/`Видалено`
  - [x] Надійне видалення у `UserSettingsManager.delete_user_draft` за `ref` GUID та `int_doc_number`
  - [x] Ігнорування `data/*.json` у `.gitignore`

- [x] **Крок 6: Генерація штрих-кодів та розпізнавання легкого повернення (v0.20.0 - v0.20.2)**
  - [x] Додати кнопку `📱 Показати штрихкод` до карток чернеток ТТН (`get_draft_keyboard`)
  - [x] Додати клавіатуру `get_waybill_keyboard` до вихідних та вхідних накладних
  - [x] Реалізувати обробник `DraftActionCallback(action="barcode")` для генерації Code128 фото-штрихкоду
  - [x] Оптимізувати пропорції штрих-коду (`module_width=0.35`, `font_size=9`, `quiet_zone=6.5`)
  - [x] Реалізувати функцію `check_is_light_return` для автоматичного розпізнавання та відображення послуги "Легке повернення" на картках ТТН
  - [x] Написати юніт-тести для перевірки надсилання штрихкоду та розпізнавання легкого повернення (41 passed)

- [x] **Крок 7: 15-хвилинна сесія, фіксація міста та автолікування опису й оцінки (v0.21.0)**
  - [x] Встановити 15-хвилинний таймаут сесії (`SESSION_TIMEOUT_SECONDS = 900`) з автоочищенням неактивних сесій
  - [x] Фіксувати вибір міста/відділення/вулиці в сесії та не запитувати повторно при зміні оцінки чи опису вантажу
  - [x] Додати регулярні вирази для автолікування `declared_value`, `cargo_description`, `cod_amount`, `payer_type` у `heal_parsed_recipient_info`
  - [x] Написати юніт-тести для перевірки сесії та оновлень полів (`tests/test_active_session_and_updates.py`)
  - [x] Запустити всі 44 тести паралельно (`pytest -n auto`)
  - [x] Оновити `Walkthrough.md` та `README.md`

- [x] **Крок 8: Відстеження накладеного платежу, місячні ліміти та статистика (v0.22.0)**
  - [x] Додати Pydantic моделі `CODItemInfo` та `CODMonthlyStats` у `src/nova_poshta/models.py`
  - [x] Додати метод `get_monthly_cod_stats` у `src/nova_poshta/client.py` з фільтрацією за місяць, статусами та сумами
  - [x] Розширити `UserCustomSettings` полями `cod_monthly_limit_sum` та `cod_monthly_limit_count` у `src/storage.py`
  - [x] Додати кнопку `💰 Накладений платіж` у `get_main_reply_keyboard()` та inline-клавіатури в `src/bot/keyboards.py`
  - [x] Реалізувати дашборд статистики з прогрес-барами та розбивкою статусів у `src/bot/handlers.py`
  - [x] Реалізувати інтерактивне налаштування місячних лімітів (30k/50k/150k грн, 5/10/20 посилок, кастомні значення)
  - [x] Додати попередження при створенні ТТН з наложкою при наближенні чи перевищенні ліміту
  - [x] Написати юніт-тести `tests/test_cod_tracking.py`
  - [x] Запустити всі 50 тестів паралельно (`pytest -n auto`)
- [x] **Крок 9: Безпечна межа (< 30 000 / макс. 29 999 грн), live-опитування та список ТТН (v0.22.1)**
  - [x] Впровадити безпечний ліміт `sum_limit - 1` (`29 999 грн` при ліміті 30 000 грн) у дашборді та попередженнях
  - [x] Забезпечити пряме live-опитування API Нової Пошти кожного разу без кешування при формуванні ТТН
  - [x] Додати функцію `_extract_float_amount` для надійного парсингу сум післяплати з усіх полів API
  - [x] Додати кнопку `[ 📜 Накладні та суми наложки (ТТН) ]` прямо під постом дашборду накладеного платежу
  - [x] Написати юніт-тести та перевірити всі 51 тест паралельно (`pytest -n auto`)
  - [x] Оновити `Walkthrough.md`, `Task.md`, `plan.md` та `README.md`

- [x] **Крок 10: Суворе розпізнавання грошової післяплати без врахування тарифів доставки (v0.22.2)**
  - [x] Виключити хибні спрацьовування на `CostOnSite`, `BackwardDeliveryCost`, `Cost` та послуги повернення документів
  - [x] Обмежити парсинг `BackwardDeliveryData` тільки грошовими типами `CargoType in ("Money", "TrMax", "Afterpayment", ...)`
  - [x] Оновити юніт-тести та перевірити всі 51 тест паралельно (`pytest -n auto`)
  - [x] Оновити `Walkthrough.md`, `Task.md`, `plan.md` та `README.md`

- [x] **Крок 11: Універсальне відстеження будь-яких накладних (ТТН) через API (v0.23.0)**
  - [x] Створити Pydantic-модель `TrackingDocumentDetails` у `src/nova_poshta/models.py`
  - [x] Реалізувати метод `track_document` у `src/nova_poshta/client.py` з підтримкою очищення номерів
  - [x] Створити функцію форматування детальної картки `format_tracking_card` з усіма даними API
  - [x] Додати автоматичне розпізнавання 14-значних та 11-значних ТТН (`extract_ttn_from_text`) та інтенту (`is_tracking_intent`)
  - [x] Додати кнопку `🔍 Відстежити ТТН` у головне меню, команди `/track`, `/tracking` та стан очікування
  - [x] Додати кнопки `📱 Показати штрихкод` (Code128) та `🔄 Оновити` під карткою відстеження
  - [x] Написати юніт-тести у `tests/test_tracking.py` та перевірити всі 63 тести паралельно (`pytest -n auto`)
  - [x] Оновити `Walkthrough.md`, `Task.md`, `plan.md` та `README.md`

- [x] **Крок 12: Виправлення сумісності з Python 3.12 (NameError: name 'Dict' is not defined) (v0.23.1)**
  - [x] Додати імпорт `Dict` з модуля `typing` у `src/nova_poshta/models.py`
  - [x] Додати регресійний тест `test_tracking_document_details_type_hints` у `tests/test_tracking.py` з перевіркою `typing.get_type_hints`
  - [x] Перевірити виконання всіх 64 тестів паралельно (`pytest -n auto`)
  - [x] Оновити `Walkthrough.md`, `Task.md`, `plan.md` та `README.md`

- [x] **Крок 13: Виправлення збою генератора штрихкоду та вилучення накладних з реєстру (ScanSheet) через AI (v0.23.2)**
  - [x] Захистити `generate_code128_barcode` від порожніх рядків (`ValueError`)
  - [x] Додати валідацію `Errors` та непорожнього `Number`/`Ref` у `create_scan_sheet`
  - [x] Додати метод `remove_documents_from_scan_sheet` у `NovaPoshtaClient`
  - [x] Оновити `AIRegisterFilterResult`, `ParsedRecipientInfo` та `REGISTER_FILTER_SYSTEM_PROMPT` діями `remove_waybill` і `delete_register`
  - [x] Додати збереження контексту активного реєстру `USER_LAST_SCANSHEET_CONTEXT` та метод вилучення накладної з реєстру в `storage.py`
  - [x] Реалізувати природномовне вилучення накладної (за порядковим номером №2, ТТН або прізвищем) та оновлення картки реєстру зі штрихкодом
  - [x] Написати юніт-тести та перевірити всі тести паралельно (`pytest -n auto`)
  - [x] Оновити `Walkthrough.md`, `Task.md`, `plan.md` та `README.md`

- [x] **Крок 14: Виправлення вибору реєстру, очищення пошкоджених записів та передача Ref реєстру (v0.23.3)**
  - [x] Додати валідацію в `storage.py` та метод `cleanup_invalid_scansheets` для очищення записів з порожнім номером/ref
  - [x] Реалізувати метод `get_scan_sheet_documents` та передачу `scan_sheet_ref` у `remove_documents_from_scan_sheet`
  - [x] Забезпечити точний вибір дійсного реєстру (наприклад, `105-80149920`) при порожньому контексті в пам'яті
  - [x] Додати юніт-тести та перевірити всі 72 тести паралельно (`pytest -n auto`)
  - [x] Оновити `Walkthrough.md`, `Task.md`, `plan.md` та `README.md`

- [x] **Крок 15: Інтелектуальний комбінований пошук вулиць через searchSettlementStreets та getStreet (v0.23.4)**
  - [x] Додати опціональне поле `settlement_ref` у модель `CityInfo` (`src/nova_poshta/models.py`)
  - [x] Додати кешування `_city_settlement_cache` та метод `get_settlement_ref` у `NovaPoshtaClient` (`src/nova_poshta/client.py`)
  - [x] Збагатити `search_city` автоматичним мапінгом `DeliveryCity -> SettlementRef` через `searchSettlements`
  - [x] Реалізувати багаторівневий пошук у `search_street`: прямий запит `getStreet`, інтелектуальний повнотекстовий пошук через `searchSettlementStreets` та фолбек розширення популярними званнями/титулами/іменами
  - [x] Покращити ранжування знайдених вулиць з урахуванням точного збігу та входження підрядка
  - [x] Передавати `settlement_ref` та `city_name` у виклики `search_street` у `src/bot/handlers.py`
  - [x] Додати юніт-тести в `tests/test_nova_poshta.py` та `tests/test_address_delivery.py` (76 passed)
  - [x] Оновити `Walkthrough.md`, `Task.md`, `plan.md` та `README.md`

- [x] **Крок 16: Виправлення розбіжності кількості накладних у створеному реєстрі (ScanSheet) та фільтрація вже зареєстрованих чернеток (v0.23.5)**
  - [x] Розширити `ScanSheetInfo` полями `success_documents`, `error_documents` та увімкнути `populate_by_name` (`src/nova_poshta/models.py`)
  - [x] Додати поле `scan_sheet_number` до `WaybillItemInfo` та `SavedDraft` (`src/nova_poshta/models.py`, `src/storage.py`)
  - [x] Реалізувати метод `update_drafts_scansheet` у `UserSettingsManager` (`src/storage.py`)
  - [x] Реалізувати точний парсинг `Data.Success`, `Data.Errors`, `Data.Warnings` у `create_scan_sheet` (`src/nova_poshta/client.py`)
  - [x] Зчитувати `ScanSheetNumber` для кожної чернетки у `get_internet_document_list` (`src/nova_poshta/client.py`)
  - [x] Навчити системний промпт AI `REGISTER_FILTER_SYSTEM_PROMPT` відфільтровувати вже зареєстровані чернетки (`src/ai/extractor.py`)
  - [x] Зберігати у новому реєстрі та контексті Telegram-бота виключно фактично додані накладні, з відображенням попередження про відхилені ТТН (`src/bot/handlers.py`)
  - [x] Синхронізувати кількість накладних у `cmd_registers` з live API Нової Пошти
  - [x] Написати юніт-тести та перевірити всі 81 тест паралельно (`pytest -n auto`)
  - [x] Оновити `Walkthrough.md`, `Task.md`, `plan.md` та `README.md`

- [x] **Крок 17: Виправлення UnboundLocalError при зміні виплати наложки або оцінки (v0.23.6)**
  - [x] Перенести ініціалізацію `eff_settings` та `user_np_client` на початок `process_waybill_callback` (`src/bot/handlers.py`)
  - [x] Усунути збій `UnboundLocalError: cannot access local variable 'user_np_client'` при натисканні кнопок `toggle_cod_type`, `cycle_cod`, `cycle_value`
  - [x] Написати регресійний юніт-тест `test_waybill_action_toggle_cod_type_and_cycles_no_unbound_local_error` (`tests/test_active_session_and_updates.py`)
  - [x] Запустити всі 82 тести паралельно (`pytest -n auto`)
  - [x] Оновити `Walkthrough.md`, `Task.md`, `plan.md` та `README.md`

- [x] **Крок 18: Автоматичний контроль ліміту післяплати при додаванні накладної (v0.23.7)**
  - [x] Реалізувати модульну функцію `evaluate_cod_limits` (`src/bot/handlers.py`) для розрахунку використаних коштів і посилок через live API `get_monthly_cod_stats`
  - [x] Додати захист від подвійного підрахунку старої суми наложки при редагуванні чинних чернеток (`editing_ref`)
  - [x] Відображати в картці перевірки ТТН детальний блок контролю: використано наразі, сума цієї ТТН, новий підсумок, точний залишок або перевищення
  - [x] Додати клавіатуру `get_limit_exceeded_confirmation_keyboard` у `src/bot/keyboards.py` з діями `force_confirm`, `cycle_cod`, `back_to_card`, `cancel`
  - [x] Перехоплювати дію `action == "confirm"` у разі перетину ліміту та виводити екран підтвердження з попередженням про фінмоніторинг NovaPay
  - [x] Обробляти дію `action == "force_confirm"` для свідомого створення ТТН попри перевищення ліміту
  - [x] Додати оновлений баланс післяплати за місяць у фінальну картку створеної ТТН
  - [x] Виправити друкарську помилку формування заголовка успішного створення накладної
  - [x] Написати 8 нових юніт-тестів у `tests/test_cod_tracking.py` та `tests/test_active_session_and_updates.py`
  - [x] Запустити всі 90 тестів паралельно (`pytest -n auto`)
  - [x] Оновити `Walkthrough.md`, `Task.md`, `plan.md` та `README.md`

- [x] **Крок 19: Мультиакаунти (користувачі/відправники), кнопка «Користувачі» та інтелектуальний розподіл лімітів післяплати (v0.24.0)**
  - [x] Реалізувати модель `SenderProfile` та оновити `UserCustomSettings` у `src/storage.py`
  - [x] Забезпечити 100% зворотну сумісність та авто-міграцію поточного відправника у перший профіль
  - [x] Реалізувати методи `get_sender_profiles`, `set_active_profile`, `add_sender_profile`, `delete_sender_profile` у `UserSettingsManager`
  - [x] Додати кнопку `👥 Користувачі` до `get_main_reply_keyboard()` та callback-схему `UserProfileCallback` у `src/bot/keyboards.py`
  - [x] Реалізувати клавіатуру керування користувачами `get_users_management_keyboard`
  - [x] Реалізувати дашборд `cmd_users` з живими даними післяплати кожного профілю та перемиканням в 1 клік
  - [x] Реалізувати функціонал додавання користувача через API-ключ Нової Пошти (`/add_user` та інтерактивний prompt)
  - [x] Створити функцію `evaluate_all_profiles_cod_limits` для live-порівняння залишків лімітів усіх профілів
  - [x] Додати інтелектуальну рекомендацію переключитися на користувача з більшим доступним залишком ліміту (у картку ТТН та на екран блокування)
  - [x] Додати кнопки перемикання профілю безпосередньо у картку ТТН та екран блокування ліміту
  - [x] Реалізувати дію `switch_profile` у `process_waybill_callback` з миттєвим перерахунком лімітів та оновленням картки
  - [x] Написати комплексні юніт-тести в `tests/test_multi_user_profiles.py`
  - [x] Запустити всі тести паралельно (`pytest -n auto`)
  - [x] Оновити документацію в `README.md`, `Walkthrough.md`, `Task.md` та `plan.md`
- [x] **Крок 20: Швидкі дії для накладної: вибір між «Відстежити» та «Згенерувати штрих-код» (v0.24.1)**
  - [x] Оновити callback-схему `TrackActionCallback` для підтримки дій `track`, `refresh`, `barcode`
  - [x] Створити клавіатуру дій `get_waybill_action_keyboard(doc_number)` з кнопками `[ 🔍 Відстежити ]` та `[ 📱 Згенерувати штрих-код ]`
  - [x] Оновити кнопку отримання штрих-коду в картці відстеження на `📱 Згенерувати штрих-код`
  - [x] Створити клавіатуру `get_barcode_keyboard(doc_number)` з кнопкою `[ 🔍 Відстежити ТТН ]`
  - [x] Реалізувати генерацію та відправку штрих-коду Code128 у функції `send_waybill_barcode` з безпечною перевіркою callback/message
  - [x] Додати обробку команд `/barcode`, `/штрихкод`, `/code128` та стан очікування `USER_BARCODE_WAITING`
  - [x] Додати детекцію природномовного інтенту `is_barcode_intent`
  - [x] Налаштувати реакцію на надсилання «голого» номера ТТН: надсилання меню з вибором дій «Відстежити» та «Згенерувати штрих-код»
  - [x] Написати юніт-тести в `tests/test_tracking.py`
  - [x] Запустити всі 108 тестів паралельно (`pytest -n auto`)
  - [x] Оновити `Walkthrough.md`, `Task.md`, `plan.md` та `README.md`

- [x] **Крок 21: Інтелектуальне визначення населеного пункту та відділення за адресою через AI (v0.24.2)**
  - [x] Створити Pydantic-схему `AICandidateDisambiguationResult` у `src/ai/schemas.py`
  - [x] Реалізувати системний промпт `DISAMBIGUATION_SYSTEM_PROMPT` у `src/ai/extractor.py`
  - [x] Реалізувати метод `disambiguate_candidates` у `AIExtractor` з перевіркою високої впевненості (`confidence == "high"`)
  - [x] Реалізувати детерміністичний евристичний fallback `heuristic_disambiguate_candidates` для токенного співставлення вулиці та номера будинку
  - [x] Зберігати `raw_text` в об'єкті сесії `PENDING_SESSIONS[session_id]` та передавати у `_continue_processing_recipient_info`
  - [x] Інтегрувати авто-вибір кандидата у `src/bot/handlers.py` при наявності декількох збігів
  - [x] Зберегти показ клавіатури вибору населеного пункту `get_city_selection_keyboard` при відсутності адреси або низькій впевненості
  - [x] Додати юніт-тести для AI та евристики в `tests/test_ai_extractor.py` та інтеграційний тест у `tests/test_active_session_and_updates.py`
  - [x] Запустити всі 113 тестів паралельно (`pytest -n auto`)
  - [x] Оновити `Walkthrough.md`, `Task.md`, `plan.md` та `README.md`

- [x] **Крок 22: Збереження першочергового повідомлення та мультидіалогова дисамбігуація населених пунктів за адресою (v0.24.3)**
  - [x] Забезпечити збереження першочергового тексту (`initial_raw_text`) та списку всіх повідомлень сесії (`all_raw_texts`) у `PENDING_SESSIONS` та `USER_INITIAL_MESSAGE_TEXT`
  - [x] Автоматично підхоплювати `initial_raw_text` при доповненні реквізитів наступними повідомленнями (оцінка, опис, тощо)
  - [x] Оновлювати `_continue_processing_recipient_info` та передавати комбінований текст усіх повідомлень користувача до `disambiguate_candidates`
  - [x] Покращити евристику `heuristic_disambiguate_candidates`: витяг вулиці після `:`, відсікання типів вулиць, співставлення номерів будинків з літерами (наприклад, `13а`, `237`)
  - [x] Очищати `USER_INITIAL_MESSAGE_TEXT` у `clear_user_active_session` та `_cleanup_expired_sessions`
  - [x] Написати мультидіалоговий юніт-тест `test_auto_disambiguate_settlement_multi_turn_initial_message` у `tests/test_active_session_and_updates.py`
  - [x] Запустити всі 114 тестів паралельно (`pytest -n auto`)
  - [x] Оновити `Walkthrough.md`, `Task.md`, `plan.md` та `README.md`

- [x] **Крок 23: Розширене діагностичне логування дисамбігуації кандидатів та моніторинг виконання (v0.24.4)**
  - [x] Додати логування запиту дисамбігуації `Attempting candidate disambiguation across X candidates with query text`
  - [x] Додати логування результату `Candidate disambiguation result: chosen_idx`
  - [x] Запустити всі 114 тестів паралельно (`pytest -n auto`)
  - [x] Оновити `Walkthrough.md`, `Task.md`, `plan.md` та `README.md`

- [x] **Крок 24: Усунення стану перегонів у дебаунсері та захист активних задач від скасування (v0.24.5)**
  - [x] Додати `USER_PROCESSING_LOCKS` та `get_user_processing_lock(user_id)` у `src/bot/handlers.py`
  - [x] Відокремити таймер дебаунсингу (`_debounce_and_dispatch`) від послідовного виконання під блокуванням (`_process_user_accumulated_messages`)
  - [x] Видаляти завершений таймер із `USER_DEBOUNCE_TASKS` у блоці `finally` одразу після 1.0с очікування, унеможливлюючи скасування активної обробки наступними повідомленнями
  - [x] Забезпечити коректне успадкування активної сесії при надсиланні доповнень (оцінка, опис тощо) без помилкових попереджень `missing_fields`
  - [x] Написати регресійний юніт-тест `test_followup_message_does_not_cancel_active_processing_task` у `tests/test_active_session_and_updates.py`
  - [x] Запустити всі 115 тестів паралельно (`pytest -n auto`)
  - [x] Оновити `Walkthrough.md`, `Task.md`, `plan.md` та `README.md`

- [x] **Крок 25: Нормалізація українського апострофа в пошуку Нової Пошти, AI-екстракторі та валідаторах (v0.24.6)**
  - [x] Створити модуль утиліт `src/utils/text_cleaner.py` з функціями `normalize_apostrophes`, `get_city_search_variants`, `is_city_matched`
  - [x] Інтегрувати нормалізацію апострофа та варіативний пошук у `src/nova_poshta/client.py` (`search_city`, `search_street`, `get_settlement_ref`, `create_recipient_counterparty`)
  - [x] Додати автоматичну нормалізацію апострофів у валідатори `ParsedRecipientInfo` у `src/ai/schemas.py`
  - [x] Забезпечити нормалізацію апострофів у вхідних текстах та регулярних виразах `AIExtractor` у `src/ai/extractor.py`
  - [x] Оновити обробники бота `src/bot/handlers.py` для використання нормалізованого зіставлення міст
  - [x] Створити набір тестів `tests/test_apostrophes.py` та запустити всі тести паралельно (`python -m pytest -n auto` -> 124 пройдено)
  - [x] Оновити `Walkthrough.md`, `Task.md`, `plan.md` та `README.md`

- [x] **Крок 26: Виправлення TelegramBadRequest при додаванні користувача, постійна кнопка Меню в Desktop/Mobile, редизайн кнопок дашборду та цифрова Картка клієнта (v0.24.7)**
  - [x] Виправити `TelegramBadRequest` у `_handle_add_profile_with_key`: видалити `status_msg` перед відправкою повідомлення з `ReplyKeyboardMarkup`
  - [x] Зареєструвати команди бота `bot.set_my_commands` та налаштувати постійну кнопку меню `bot.set_chat_menu_button` у `src/bot/main.py`
  - [x] Оптимізувати кнопки дашборду користувачів у `get_users_management_keyboard`: прибрати `Обрати:`, прибрати `(Активний)`, перенести залишок на `\n` другого рядка кнопки
  - [x] Додати метод API `get_loyalty_info` у `src/nova_poshta/client.py` (`LoyaltyUser/getLoyaltyInfoByApiKey`) для отримання даних картки клієнта/лояльності
  - [x] Реалізувати генератор контрастного цифрового зображення картки клієнта `generate_client_card_image` у `src/utils/barcode_gen.py`
  - [x] Створити команду `/client_card`, кнопку у налаштуваннях та інлайн-клавіатуру перемикання штрихкоду (Телефон vs Картка CID)
  - [x] Додати юніт-тести в `tests/test_client_card_and_ui.py` та виконати всі 132 тести паралельно (`python -m pytest -n auto`)
  - [x] Оновити `Walkthrough.md`, `Task.md`, `plan.md` та `README.md`

- [x] **Крок 27: Збереження та плавне відновлення сесії створення ТТН при додаванні або зміні банківської картки (v0.24.8)**
  - [x] Прибрати деструктивний виклик `clear_user_active_session` з обробника команди `/set_card` та суміжних команд налаштувань
  - [x] Реалізувати централізовану генерацію перевірочної картки та клавіатури дій `_build_waybill_preview_message` у `src/bot/handlers.py`
  - [x] Створити функцію `_save_user_card_and_resume_session`: збереження картки, оновлення `cod_payment_type="card"`, редагування попереднього повідомлення та відправка оновленого драфту з робочими інлайн-кнопками для миттєвого продовження
  - [x] Додати підтримку збереження картки за прямим введенням 16 цифр у чат та через стан очікування `USER_CARD_WAITING`
  - [x] Реалізувати автовиявлення вже збережених карток у кабінеті Нової Пошти через `get_payment_cards` при натисканні виплати на картку
  - [x] Додати підтримку параметрів `cod_payment_type` та `payment_card` (`RedeliveryPaymentCard`) у `create_waybill` клієнта НП
  - [x] Створити юніт-тести `tests/test_card_session_resume.py` та успішно виконати всі 136 тестів паралельно (`python -m pytest -n auto`)
  - [x] Оновити `Walkthrough.md`, `Task.md`, `plan.md` та `README.md`

- [x] **Крок 28: Автоматичне підтягування адреси відправлення з сайту/API, налаштування в додатку з вищим пріоритетом та захист від збоїв генерації ТТН (v0.24.9)**
  - [x] Реалізувати метод `fetch_sender_address_from_api(counterparty_ref)` у `NovaPoshtaClient` з перевіркою `Counterparty/getCounterpartyAddresses` (Sender, Recipient) та резервним аналізом останніх відправлень `InternetDocument/getDocumentList`
  - [x] Інтегрувати витяг адреси відправки в `fetch_sender_profile` під час додавання або синхронізації користувача
  - [x] Додати поля `api_sender_city_ref`, `api_sender_city_name`, `api_sender_address_ref`, `api_sender_warehouse_name` у `SenderProfile` та `UserCustomSettings`
  - [x] Реалізувати строгу багаторівневу ієрархію пріоритетів у `get_effective_settings` (ручне налаштування в додатку через `/set_city` та `/set_warehouse` має абсолютний найвищий пріоритет над адресою з сайту/API)
  - [x] Забезпечити автоматичне успадкування адреси відправлення (`_ensure_profile_migration`) від налаштованих профілів-донорів або API-адреси
  - [x] Реалізувати pre-flight валідацію в обробнику підтвердження ТТН `confirm` / `force_confirm` перед зверненням до API Нової Пошти з блокуванням помилки `CitySender not selected, SenderAddress not selected`, збереженням сесії та виведенням інструкцій налаштування
  - [x] Усунути деструктивне очищення сесії при виклику команд `/set_city` та `/set_warehouse` з автоматичним відновленням чернетки після вказання відділення
  - [x] Додати команду `/sync_address` (`/sync_sender_address`) та інлайн-кнопку `[ 🔄 Підтягнути адресу з сайту (API) ]` у картці керування профілем `👥 Користувачі`
  - [x] Відображати походження адреси відправника в дашборді профілю (`(в додатку)` vs `(з сайту/API)`)
  - [x] Створити набір тестів `tests/test_sender_address_api_and_priority.py` та верифікувати проходження всіх 143 тестів паралельно (`python -m pytest -n auto`)
  - [x] Оновити `Walkthrough.md`, `Task.md`, `plan.md` та `README.md`

- [x] **Крок 29: Інтеграція створених у відділенні накладних (5900...) через `getOutgoingDocumentsByPhone` та `getIncomingDocumentsByPhone`, точний підрахунок післяплати та оптимізація кешування (v0.24.10)**
  - [x] Дослідити поведінку API Нової Пошти та виявити методи `InternetDocument/getOutgoingDocumentsByPhone` та `InternetDocument/getIncomingDocumentsByPhone`, що повертають як інтернет-накладні (`2045...`), так і накладні, створені у відділенні оператором (`5900...`)
  - [x] Реалізувати уніфікований нормалізатор `_normalize_phone_doc` у `src/nova_poshta/client.py` для адаптації відповідей телефонних методів до внутрішньої структури накладної
  - [x] Оновити `_fetch_raw_waybills_with_cache` для об'єднання накладних з `getDocumentList` та `getOutgoingDocumentsByPhone` з глобальним кешуванням за API-ключем (300с)
  - [x] Додати `_fetch_raw_incoming_waybills_with_cache` та інтегрувати `getIncomingDocumentsByPhone` у `get_incoming_waybills` для повноцінного відстеження посилок, що прямують до користувача
  - [x] Інтегрувати `getOutgoingDocumentsByPhone` у `get_monthly_cod_stats` для виявлення посилок з післяплатою, створених у відділенні (зокрема 14 800 грн на картку), з урахуванням у статуси «У дорозі» та щомісячний ліміт 29 999 грн
  - [x] Оновити `get_effective_settings` у `src/storage.py` для безпечного наслідування API-ключа від `user_custom.nova_poshta_api_key`, якщо окремий профіль не містить власного ключа
  - [x] Додати юніт-тести в `tests/test_nova_poshta.py` для перевірки нормалізації, виявлення посилок з відділення у вихідних та підрахунку післяплати
  - [x] Виконати всі 146 тестів проекту паралельно (`python -m pytest -n auto`)
  - [x] Оновити `Walkthrough.md`, `Task.md`, `plan.md` та `README.md`




