# Nova Poshta AI Waybill Generator Telegram Bot 📦🤖

An intelligent Telegram Bot built with Python (`aiogram 3.x`) and AI (OpenAI API / Gemini API / local OpenAI-compatible endpoints) that automatically extracts structured recipient details from free-form text messages and generates Nova Poshta Express Waybills (ТТН).

---

## ✨ Features

- **🏷️ Sender Profile Pseudonyms & Mobile-Optimized 2-Row Limit Buttons (v0.24.12)**:
  - **Custom Profile Pseudonyms / Aliases (`/set_alias`, `/set_name`, `/rename_user`, `/reset_alias`)**: Users can assign intuitive, recognizable pseudonyms to their sender profiles (e.g. `Основний`, `ФОП`, `Склад`, `Водафон`) directly via the interactive `[ ✏️ Змінити псевдонім ]` button in the `👥 Користувачі` dashboard or by using `/set_alias [Name]`. To revert back to the verified official full name from Nova Poshta, simply send `/reset_alias`.
  - **Zero Mobile Truncation (Dedicated 2-Row Button Layout)**: Because Telegram inline buttons are strictly single-line, placing remaining balance text in parentheses next to long full names resulted in ugly truncation on mobile screens (`Рашевський Владіслав Сергійов...`). The profile management keyboard (`get_users_management_keyboard`) now uses an ergonomic **2-row layout per profile**:
    - **Row 1**: Profile identity & selection button: `✅ [Псевдонім або Скорочене ПІБ]` (or `🔄` for inactive profiles).
    - **Row 2**: Full-width dedicated remaining balance button: `💰 Залишок: 15199 грн` (or `🚨 Ліміт 30 000 грн вичерпано (0 грн)`). Tapping either row activates the profile.
  - **Automatic Disambiguation for Identical Full Names**: When multiple accounts share the identical legal name (e.g. two separate Nova Poshta API accounts registered under `Рашевський Владіслав Сергійович`), the bot automatically shortens long legal names to standard Ukrainian format `Прізвище І. П.` and appends the last 4 digits of the profile's phone number: `Рашевський В. С. (..9301)` vs `Рашевський В. С. (..6324)`, completely eliminating confusion even before a custom alias is set.
  - **Transparent Dashboard View**: The `👥 Користувачі` dashboard displays both the custom alias and the official counterparty name and phone number (`*Мій Основний* — Рашевський Владіслав Сергійович (тел: 0502559301)`).

- **🛡️ Bulletproof Standalone Bank Card Detection & Hijack Guard (v0.24.11)**:
  - **Eliminated Multi-Field False Positives**: Replaced loose whole-message digit filtering (`filter(str.isdigit, text)`) with strict contiguous block validation (`extract_standalone_bank_card`). Previously, if a recipient waybill message's scattered digits (e.g. 10-digit phone `0968071564` + 1-digit warehouse `3` + 5-digit COD `10300`) happened to sum to 16 digits (`0968071564310300`), the bot mistakenly intercepted the waybill as a bank card.
  - **Monolithic Block Validation**: Strictly verifies that a payment card is an unbroken 16–19 digit block (`XXXX XXXX XXXX XXXX`, `XXXX-XXXX-XXXX-XXXX`, or continuous), while inspecting surrounding context for recipient phone numbers (`0\d{9}`) and delivery destination keywords (`відділення`, `поштомат`, `місто`, `вулиця`).
  - **Graceful Card Waiting State Reset**: If a user previously clicked "Виплата: На картку" (setting `USER_CARD_WAITING`) but subsequently sends a recipient waybill draft, the waiting state is automatically cleared and waybill drafting continues uninterrupted.
  - **Embedded Card in Waybill Text**: If a user explicitly specifies a bank card inside the waybill text itself (e.g. `наложка 10300 грн на картку 4441 1114 0076 5537`), the card is automatically extracted and saved in settings, COD payout is set to `card`, and the waybill draft is fully generated with all recipient information preserved.

- **📦 Branch-Created Waybills (`5900...`) & Real-Time Phone Document Sync (v0.24.10)**:
  - **Full Parity with Nova Poshta Mobile App & Web Cabinet**: Standard Nova Poshta API method `InternetDocument/getDocumentList` only returns Internet Documents created online or via API (`2045...`), completely omitting shipments registered directly at physical branch counters (`5900...`). The bot now seamlessly integrates with Nova Poshta's native phone synchronization endpoints: `InternetDocument/getOutgoingDocumentsByPhone` and `InternetDocument/getIncomingDocumentsByPhone`.
  - **Comprehensive Outgoing & Incoming Merging**: Automatically merges online waybills with branch-created documents, deduping by waybill number (`IntDocNumber` / `Number`). Physical branch shipments are now immediately visible in `/outgoing` ("📤 Вихідні (що їдуть)") and `/incoming` ("📥 Вхідні (що їдуть)").
  - **Precise Cash On Delivery (COD) Accounting**: Physical branch shipments with Cash On Delivery (післяплата) are fully recognized and tracked. The bot extracts afterpayment amounts (`AfterpaymentOnGoodsCost`, `RedeliverySum`), detects bank card payout routes (`CardMaskedNumber`), tracks transit and received statuses, and includes them in the monthly volume against the 30,000 UAH financial monitoring threshold (`/cod`).
  - **Multi-Profile Phone Support**: Queries documents using the specific phone number linked to the active sender profile (`380XXXXXXXXX`), ensuring accurate tracking when switching between user profiles.
  - **Isolated Incoming Waybills Cache**: Introduced dedicated 300-second caching for incoming shipments (`_incoming_waybills_cache`), maintaining snappy response times without redundant API calls.

- **🏢 Automatic Website Cabinet Departure Address Sync & In-App Priority Hierarchy (v0.24.9)**:
  - **Auto-Sync from Nova Poshta Web Cabinet**: Seamlessly discovers and pulls your default departure city and branch from your account on the Nova Poshta website (`novaposhta.ua`) via API. The bot checks counterparty addresses (`Counterparty/getCounterpartyAddresses`) and inspects recent outgoing shipments (`InternetDocument/getDocumentList`), extracting `CitySender` and `SenderAddress` automatically without manual entry.
  - **In-App Configuration with Strict Priority**: You can customize or override your departure city and warehouse directly in the bot using `/set_city [City]` and `/set_warehouse [Number]`. The address configured directly in the app **always takes strict priority** over the one fetched from the website cabinet/API.
  - **One-Tap Website Sync (`/sync_address`)**: Quickly refresh or re-fetch your cabinet departure location at any time using the `/sync_address` (or `/sync_sender_address`) command or the dedicated `[ 🔄 Підтягнути адресу з сайту (API) ]` button in the `👥 Користувачі` dashboard.
  - **Pre-Flight Validation Guard**: Intercepts waybill creation before contacting Nova Poshta API if neither an in-app nor website departure point is present, permanently preventing `CitySender not selected, SenderAddress not selected` API crashes.
  - **Zero-Disruption Session Continuation**: Specifying your departure location never wipes your active draft. Upon running `/set_warehouse`, the bot immediately re-renders your refreshed waybill preview card with the `[ ✅ Створити ТТН ]` button for instant one-click completion.

- **💳 Digital Nova Poshta Client Card & High-Resolution Scannable Barcodes ("💳 Картка клієнта" / `/client_card`)**:
  - **Direct In-Bot Customer Card**: Open your customer loyalty card directly in Telegram without needing to open the official Nova Poshta mobile app at the branch counter or self-service terminal.
  - **Official API Loyalty Data**: Automatically queries Nova Poshta API 2.0 (`LoyaltyUser/getLoyaltyInfoByApiKey`) to fetch verified sender details, including Full Name, Phone number, Loyalty Card ID (`CID1428940191989`), and User Login.
  - **High-Resolution Scannable PNG Card**: Generates a high-contrast digital card image featuring Nova Poshta signature branding (`#DA291C`), sender credentials, and a high-resolution Code128 barcode (300 DPI) specifically optimized for optical and laser handheld barcode scanners at Nova Poshta branch desks.
  - **Dual Barcode Toggle**: Easily switch on the fly between Loyalty Card (CID) barcode and Phone Number barcode via one-tap inline buttons (`📱 Штрихкод телефону` vs `💳 Штрихкод картки (CID)`).
  - **One-Click Access**: Available from the persistent bottom reply keyboard (`💳 Картка клієнта`), the settings dashboard (`⚙️ Налаштування`), the slash command `/client_card` (or `/card`), and the Telegram bot commands menu.

- **🖥️ Desktop & Mobile Telegram Persistent Menu Button (`set_my_commands` + `set_chat_menu_button`)**:
  - **Always-Accessible Menu Button**: Registers system-wide bot commands with Telegram Bot API (`bot.set_my_commands`) and activates the persistent chat menu button (`bot.set_chat_menu_button(menu_button=MenuButtonCommands())`).
  - **Full Desktop Support**: Guarantees the presence of the permanent bottom-left "Меню" (Menu) button across Telegram Desktop (Windows, macOS, Linux) as well as iOS and Android clients, providing instant access to all core features (`/start`, `/users`, `/settings`, `/client_card`, `/outgoing`, `/incoming`, `/drafts`, `/scansheet`, `/cod`, `/track`, `/help`).

- **✨ Streamlined Users Dashboard & Multi-Line Balance Buttons ("👥 Користувачі" / `/users`)**:
  - **Zero Truncation on Mobile Screens**: Replaced cluttered inline button text by eliminating redundant `"Обрати: "` prefixes and `"(Активний)"` markers.
  - **Multi-Line Labels**: Remaining monthly COD balances are placed on an explicit second line (`\n(залишок: X грн)`) within each button, ensuring full visibility of both sender name and limit without ellipsis cutoffs.
  - **Safe Onboarding**: Fixed `TelegramBadRequest` when adding user profiles via `/add_user`, ensuring seamless onboarding and status cleanup.

- **🔍 Universal Express Waybill Tracking & Code128 Barcodes ("🔍 Відстежити ТТН" / `/track [НОМЕР]` / `/barcode [НОМЕР]`)**:
  - **Instant Action Prompt (Dual Buttons)**: When sending a 14-digit (or 11-digit) waybill number into the chat, the bot immediately provides dedicated action buttons:
    - `[ 🔍 Відстежити ]` — Fetches real-time tracking status, full route, financial details, and parcel parameters directly from Nova Poshta API.
    - `[ 📱 Згенерувати штрих-код ]` — Renders a crisp Code128 barcode photo for rapid scanning at Nova Poshta branches or postomats, accompanied by a quick `[ 🔍 Відстежити ТТН ]` button.
    - `[ 🌐 Відкрити на сайті Нової Пошти ]` — Direct URL to the official tracking portal.
  - **Comprehensive Data Extraction**: Fetches and displays all information available via Nova Poshta API (`TrackingDocument/getStatusDocuments`), including live status, route (sender/recipient cities, branch addresses, contact persons, masked phone numbers), timeline (creation date, scheduled delivery, actual delivery, last movement scan, free storage expiration), parcel parameters (cargo description, factual & volumetric weight, seats amount, announced value), and financial details (delivery cost, payer, payment status, COD/afterpayment amount, total amount to pay upon pickup).
  - **Instant Smart Detection & Direct Commands**: Automatically detects waybill numbers with or without spaces/hyphens (`2045 0123 4567 89`), handles natural language requests (*"де моя посилка 2045..."*, *"відстеж 2045..."*, *"штрихкод 2045..."*), and provides dedicated slash commands (`/track [ТТН]`, `/barcode [ТТН]`).
  - **Live Refresh & Quick Barcodes**: Includes `🔄 Оновити` to refresh parcel status in place and `📱 Згенерувати штрих-код` on both tracking cards and individual waybills.
  - **Public & Universal**: Works out of the box for any user without requiring prior AI configuration or custom API keys.

- **💰 Monthly Cash On Delivery (COD) Tracking & Limits ("💰 Накладений платіж" / `/cod`)**: Fully tracks monthly Cash On Delivery (післяплата) shipments directly via Nova Poshta API 2.0 (`InternetDocument/getDocumentList` & `InternetDocument/getOutgoingDocumentsByPhone`). Features include:
  - **Live Monthly Calculation**: Computes total COD volume (UAH sum and parcel count) from the 1st day of the current calendar month to today without caching, performing live API queries every time.
  - **Safe Financial Monitoring Ceiling (< 30,000 UAH / Max 29,999 UAH)**: Strictly warns when approaching or reaching 30,000 UAH (enforcing safe max limit of `29,999 грн` / `limit - 1`).
  - **Automatic Monthly Reset**: Statistics automatically reset on the 1st of every new month without requiring manual maintenance.
  - **Detailed Status Breakdown**: Categorizes shipments into 🟢 Received/Disbursed (Виплачено), 🚚 In Transit / Awaiting Pickup (У дорозі), 📝 Drafts (Чернетки), and 🔴 Refused/Returned (Відмови).
  - **Configurable Limits & Progress Bar**: Personal monthly limits (defaults: 30,000 UAH / 10 parcels) with visual emoji progress bars (`🟩🟩🟩🟨⬜ 85%`), customizable via quick-preset inline buttons or commands (`/set_cod_limit`, `/set_cod_count`).
  - **Proactive Threshold Warnings & Seamless Payout Toggling**: Displays timely warnings on parcel creation confirmation cards if creating a COD parcel will exceed or approach (>80%) monthly limits, with full interactive inline toggle support between Card and Cash payouts (`toggle_cod_type`) and live amount cycles.
  - **Automatic Live COD Limit Verification & Two-Step Protection Guard**: When adding or editing any waybill with Cash On Delivery, the bot automatically triggers live Nova Poshta API queries (`InternetDocument/getDocumentList`) to calculate current monthly COD volume without stale cache:
    - **Dynamic Volume Evaluation**: Automatically calculates whether the new COD amount fits within the user's monthly limits (or safe 29,999 UAH financial monitoring threshold). Shows a real-time status block (`✅`, `⚠️`, or `🚨`) on the verification card with current used amount, new total, and remaining safe margin.
    - **Smart Draft Deduplication**: When editing an existing draft (`editing_ref`), its prior COD amount is automatically deducted to prevent false double-counting.
    - **Accidental Exceedance Intercept**: If confirming a waybill would exceed the established monthly limit, creation is intercepted with an explicit warning dialog and interactive options (`[ ⚠️ Все одно створити ТТН ]`, `[ 💰 Змінити наложку ]`, `[ 🔙 До картки ТТН ]`, `[ ❌ Скасувати ]`) to prevent accidental financial monitoring blocks.
    - **Post-Creation Summary**: The final created waybill card displays the updated monthly COD volume and remaining limit.
  - **Direct Waybill Explorer Button ("📜 Накладні та суми наложки (ТТН)")**: One-tap button directly under the COD stats post to view a paginated list of all COD shipments sent during the current month with exact amounts, creation dates, recipients, and payout mechanisms (💳 Card vs 💵 Cash).

- **👥 Multi-User Sender Profiles & Automated COD Limit Balancing ("👥 Користувачі" / `/users` / `/add_user`)**: Support for multiple sender accounts/profiles within a single Telegram account to safely manage NovaPay's 30,000 UAH monthly financial monitoring threshold:
  - **Live Multi-Profile COD Dashboard**: Manage all your sender accounts with one tap. View used COD amounts, safe remaining limits, and parcel counts per sender in real time.
  - **Automated Sender Recommendation**: When creating or editing a waybill with Cash On Delivery (накладений платіж), if the active sender approaches (>80%) or exceeds the 30,000 UAH threshold, the bot automatically checks all configured senders and recommends switching to the user with the most remaining COD capacity.
  - **One-Click Profile Switching**: Switch senders directly on the waybill confirmation card (`[ 👥 Переключити на: Ім'я (+X грн ліміту) ]`) or on the limit-exceeded interception dialog without re-entering parcel details.
  - **Quick Account Onboarding**: Add extra Nova Poshta sender accounts via `/add_user [API_KEY] [Label]` or interactively through the dashboard. The bot automatically fetches sender full name, phone number, and counterparty references from Nova Poshta API.
  - **100% Backward Compatible & Persistent**: Existing configurations seamlessly migrate into a default profile while AI credentials remain shared. Non-active profiles can be deleted safely at any time.

- **💳 Digital Nova Poshta Client Loyalty Card (`/client_card` / `/card` / `💳 Картка клієнта`) (v0.24.7)**:
  - **Instant Branch Counter Scanning**: Generate a high-contrast, official-branded digital client card directly in Telegram without opening the official Nova Poshta mobile app.
  - **300 DPI Code128 Barcodes**: Built-in dual barcode engine using Pillow and `python-barcode` generates ultra-crisp barcodes with numeric subtitles, optimized for handheld laser and optical scanners used by branch operators.
  - **One-Tap Barcode Toggling**: Easily switch between `📱 Штрихкод телефону` (phone barcode) and `💳 Штрихкод картки (CID)` (loyalty CID barcode) directly underneath the card.
  - **Nova Poshta Loyalty API Integration**: Automatically queries `LoyaltyUser/getLoyaltyInfoByApiKey` to pull verified loyalty card CID, account login, full name, phone number, and personal discount rates.

- **💳 Payout Bank Card Management & Zero-Disruption Session Resume (v0.24.8)**:
  - **Seamless TTN Session Preservation**: Entering or updating your bank card (via `/set_card [CARD_NUMBER]` or typing 16 digits) while creating or editing a waybill never wipes your in-progress draft! The bot preserves all parsed recipient, cargo, and destination details, automatically flips the payout mode to `card`, updates previous messages, and delivers a refreshed verification card with action buttons (`[ 🔄 💳 Виплата: На картку (...) ]`, `[ ✅ Створити ТТН ]`) for immediate one-tap completion.
  - **Direct 16-Digit Chat Input & Waiting State**: Simply send your 16-digit card number directly into the chat (with or without spaces/hyphens), or click `Виплата: На картку` to trigger guided card entry.
  - **Automated Nova Poshta Card Discovery**: Toggling payout to card automatically queries Nova Poshta's `Counterparty/getPaymentCards` API to check for cards already registered in your business cabinet, automatically linking them without manual entry.
  - **Native API Integration**: Passes `RedeliveryPaymentCard` directly into Nova Poshta's `InternetDocument/save` API and `BackwardDeliveryData`, ensuring cash on delivery funds are routed directly to your card.

- **🎯 Smart City & Warehouse Retention & AI Address Disambiguation**:
  - **🧠 Intelligent Auto-Disambiguation by Address/Street & Multi-Turn History (v0.24.2 - v0.24.3)**: When multiple candidate settlements share the same name and branch number in Nova Poshta database (e.g. `Берегомет` in Chernivtsi Oblast with Branch #1 at `вул. Героїв Майдану, 237` vs `Берегомет` in Kitsman Raion with a branch at `вул. Головна, 13а`), the bot uses LLM intelligence (`DISAMBIGUATION_SYSTEM_PROMPT`) and enhanced heuristic matching.
    - **Multi-Turn Message History & Initial Message Memory (v0.24.3)**: Even when recipient creation spans multiple messages (e.g., Message 1 contains recipient name, city, branch, and street address `героїв Майдану 237`, while Message 2 provides valuation and cargo details `Оцінка 7500, в посилці планшет`), the bot preserves the initial message text throughout the session. When disambiguating candidates, all message turns are evaluated so that address clues from the initial message automatically resolve the candidate without displaying unnecessary disambiguation keyboards!
    - **Enhanced Heuristic Matcher**: Seamlessly parses street names after colons, strips type prefixes (`вул.`, `просп.`, `пров.`), and matches house numbers with letters (e.g., `13а`, `237`).
    - **Fallback to Interactive Keyboard**: If the messages contain no distinguishing address details, the interactive selection keyboard is presented as usual.
- **🔤 Comprehensive Ukrainian Apostrophe Normalization & Phonetic Fallback (v0.24.6)**:
  - **Universal Unicode Support**: Automatically normalizes all Unicode variations of Ukrainian apostrophes — including `ʼ` (`U+02BC` modifier letter apostrophe, default on mobile iOS/Android Ukrainian keyboards), `’` (`U+2019` typographic right quote), `‘` (`U+2018`), `` ` `` (`U+0060` EN layout backtick), `´` (`U+00B4`), `ʻ` (`U+02BB`), `ʹ` (`U+02B9`), `′` (`U+2032`), and `″` (`U+2033`) — into standard ASCII single quote `'` (`U+0027`).
  - **100% Nova Poshta API 2.0 Compatibility**: Nova Poshta's database strictly requires ASCII `'` for cities (e.g. `Кам'янське`, `Кам'янець-Подільський`, `П'ятихатки`), streets (e.g. `вул. В'ячеслава Чорновола`, `вул. Лук'янівська`), and recipient names (e.g. `Мар'яна`, `В'ячеслав`). Normalization across `search_city`, `search_street`, `get_settlement_ref`, and `create_recipient_counterparty` ensures zero dropped searches or false "місто не знайдено" errors.
  - **Intelligent Phonetic Fallback for Missing Apostrophes**: If a user enters a city name omitting the apostrophe altogether (e.g. `Камянське` or `Камянець-Подільський`), the bot automatically generates phonetic candidate variants according to Ukrainian grammar rules (after labial consonants `б`, `п`, `в`, `м`, `ф` and `р` before iotated vowels `я`, `ю`, `є`, `ї`) and queries the API until a valid match is resolved.
  - **Schema & Regex Sanitation**: Standardized across Pydantic schemas (`ParsedRecipientInfo`) and regex heuristics (`heal_parsed_recipient_info`), with line-bounded regex matching that prevents cross-line word bleeding.
- **⚡ Bulletproof Debounce Pipeline & Task Protection Guard (v0.24.5)**:
  - **Zero Mid-Flight Cancellation**: Strictly isolates the 1.0-second message buffer debounce timer from active AI and Nova Poshta API processing. When a user sends a follow-up message (e.g. adding declared value or cargo description *"Оцінка 7500, в посилці планшет"*) while the bot is actively parsing the initial recipient address, the running task is protected and never cancelled mid-flight.
  - **Sequential Per-User Locks**: Subsequent messages wait under a sequential `asyncio.Lock` per user. Once the initial draft is assembled, the follow-up message seamlessly updates the active session without orphaned status indicators or false missing-fields warnings (`ПІБ: N/A`).
- **🩹 Extended Entity Healing for Declared Values & Cargo Descriptions**: Regex and heuristic extraction automatically detects phrases like *"оцінка 50000 грн"*, *"в посилці steam deck OLED 512 та Odin 2 portal"*, *"наложка 15000 на картку"*, ensuring immediate and accurate updates even if the user sends free-form follow-up texts.
- **🏡 Intelligent Courier Address Delivery & Multi-Tiered Street Search ("🏡 Адресна доставка кур'єром")**: Seamlessly detects and creates courier door delivery waybills (`WarehouseDoors`) with intelligent street matching!
  - **Dual API Engine (`searchSettlementStreets` + `getStreet`)**: Uses the same intelligent full-text search engine as the official Nova Poshta mobile app! When users specify colloquial or shortened street names without official titles or first names (e.g. *"вул. Тарнавського"* instead of *"вул. Генерала Тарнавського"*, *"вул. Хмельницького"* instead of *"вул. Богдана Хмельницького"*, or renamed streets like *"Короленка"*), the bot searches `Address/searchSettlementStreets` by settlement GUID, maps candidate street names to official `getStreet` entries, and resolves the valid `StreetRef` needed for `Address/save`.
  - **Honorific & Title Expansion Fallback**: Includes built-in expansion for Ukrainian military, academic, and historical ranks/names (`Генерала`, `Академіка`, `Гетьмана`, `Степана`, `Богдана`, `Тараса`, `Лесі`, `Івана`, `Володимира`, `Михайла` etc.), ensuring instant discovery even when settlement references are unavailable.
  - **Permutations & Ranking**: Automatically strips street prefixes (`вул.`, `пров.`, `просп.`), tries multi-word permutations (e.g. *"вул. Віри Гордієнко"* -> *"вул. Гордієнко Віри"*), and ranks matches prioritizing exact match (+3.0) and substring match (+2.0).
  - **Interactive Disambiguation Keyboard**: If multiple matching streets exist (e.g. street vs lane), the bot displays an interactive selection keyboard (`[ 🏡 вул. ... ]`, `[ 🏡 пров. ... ]`) for one-tap disambiguation.

- **🛡️ Dual-Layer AI & Regex Entity Healing (`heal_parsed_recipient_info`)**: Bulletproof recipient entity extraction! If an AI model returns incomplete fields or encounters an issue, an automatic regex/heuristic healing pipeline scans the text to extract phones, postomats/branches, cities, and names with initials (`Мартинюк Є.В.`), ensuring 100% extraction reliability.
- **📩 Forwarded Message & Metadata Handling**: Automatically ignores Telegram forwarding metadata (`"Переслано від Vlad Martyniuk"`, `"Forwarded from..."`), extracting the real recipient details from the message body without confusion.
- **📦 Postomat & Russian/Surzhyk Variations Support**: Seamlessly handles terms like `"поштомат"`, `"паштомат"`, `"пм"`, `"відд"`, `"отделение"`, and postomats with location addresses in parentheses (e.g. `почтомат НП 24991 (просп Князя Володимира Великого 75А, 3 під'їзд)`) without false address delivery triggers.
- **🗣️ Full Natural Language & Voice Waybill Editing**: Modify any part of a waybill draft at any time using plain text or voice messages (e.g., *"зміни прізвище на Петренко"*, *"новий телефон 097..."*, *"відправ у Львів"*, *"поміняй на відділення №5"*, *"вулиця Франка 10, а не провулок"*, *"опис планшет"*, *"оцінка 15000"*, *"платник відправник"*).
- **🔒 Seamless Session Settings Persistence**: When toggling buttons (such as switching payer to *Sender*, cargo to *Documents*, or custom COD/declared values) and subsequently modifying the waybill via text/voice, all existing user configurations are strictly preserved without being reset to defaults.
- **📋 AI-Powered ScanSheet Registers, Natural Language Editing & Code128 Barcodes (`/registers`)**: Automatically builds, modifies, and manages Nova Poshta ScanSheet registers using LLM intelligence over user waybill drafts!
  - **Dynamic Natural Language Creation**: Users can create registers using arbitrary natural language requests (e.g. *"створи реєстр з усіх моїх чернеток"*, *"створи реєстр з накладної 20451506611097"*, *"об'єднай у реєстр вчорашні посилки"*, *"створи реєстр де опис сувенір"*). The bot fetches active un-shipped drafts directly from Nova Poshta API + local storage, feeds them as a structured JSON payload with timestamp context to the AI, registers the ScanSheet via Nova Poshta API, and delivers a scannable Code128 PNG barcode photo along with detailed waybill summaries.
  - **100% Document Synchronization with Official Mobile App & Partial Success Handling**: Parses nested `Data.Success`, `Data.Errors`, and `Data.Warnings` from Nova Poshta `ScanSheet/insertDocuments` responses. Reflects the exact number of waybills actually accepted by Nova Poshta, filters out drafts that already belong to prior registers (`scan_sheet_number`), and displays an informative warning block explaining why any waybill could not be included (e.g. *"Документ уже знаходиться у реєстрі 105-..."*).
  - **Conversational Waybill Removal (`ScanSheet/removeDocuments`)**: Effortlessly exclude individual waybills from an active register in natural language! Say *"Прибери, будь ласка, з реєстру накладну №2"*, *"видали другу накладну з реєстру"*, *"вилучи Кожина з реєстру"*, or *"прибери 2045... з реєстру"*. The bot matches the waybill by list index, TTN number, or recipient name, fetches exact Document Ref GUIDs via `ScanSheet/getScanSheetDocuments`, detaches the target waybill with `Ref` of the ScanSheet via Nova Poshta API, synchronizes local storage, and immediately generates an updated Code128 barcode and card with the remaining waybills.
  - **Automatic Corrupt Register Filtering & Live Count Synchronization**: Recovers the genuine active register from storage while strictly filtering out corrupt entries with missing numbers or refs. In `/registers`, automatically synchronizes waybill counts with live Nova Poshta API data.
  - **Natural Language Disbanding**: Disband registers simply by texting *"видали цей реєстр"* or *"розформуй реєстр"*, returning all waybills to active drafts.
  - **Bulletproof Code128 Barcode Generation**: Generates 100% scannable high-resolution Code128 barcodes directly in Telegram photos, preserving exact register numbers with hyphens (e.g. `105-80149920`) with strict input validation protecting against empty barcode crashes.
- **📩 Multi-Part & Reposted Message Accumulation**: When users forward/repost multiple messages in sequence (e.g., 1st message with Name/Phone, 2nd message with City/Branch), the bot saves partial context and seamlessly merges all incoming messages into a single complete waybill draft!
- **🔄 Active Session Context Memory & Live Draft Editing**: Remembers active recipient context. Users can edit any saved draft using live natural language (e.g. typing *"зміни опис на сувенір"* or *"оцінка 2000 грн"*), updating the waybill live in Nova Poshta database via `InternetDocument/update`!
- **⌨️ Persistent Reply Keyboard Menu**: Convenient Telegram bottom menu (`📦 Активні посилки`, `📝 Мої чернетки (ТТН)`, `👥 Користувачі`, `⚙️ Налаштування`, `❓ Допомога`) for quick 1-tap navigation without needing slash commands.
- **📄 Waybill Drafts Management & Synchronization (`/drafts` / `📝 Мої чернетки (ТТН)`)**: View active waybill drafts with live status tracking (`TrackingDocument/getStatusDocuments`). Automatically filters out and purges both physically shipped waybills and deleted/cancelled waybills (`StatusCode 2`, `3`, `"Видалено"`, `"Номер не знайдено"`), ensuring only genuine active un-shipped drafts are shown, with instant action buttons (`✏️ Редагувати ТТН` / `🗑 Видалити ТТН`).
- **🔐 Strict Multi-Tenant & Per-User Isolation (NP & AI)**: Each Telegram user has their own isolated profile in `user_settings.json`. New unconfigured users are protected by an Onboarding Guard (`ensure_user_configured`) and must bind both their personal Nova Poshta API key (`/set_np_key`) and personal AI API key (`/set_ai_key`, `/set_ai_url`, `/set_ai_model`), ensuring zero credential leakage between users!
- **🔍 Smart Missing Info Prompting**: Automatically validates extracted details and prompts the user if required recipient info (Full Name, Phone, City, or Branch) is missing.
- **⚡ 5-Minute In-Memory Waybill Caching**: Caches raw Nova Poshta waybill API responses for 5 minutes (`_fetch_raw_waybills_with_cache` and `_fetch_raw_incoming_waybills_with_cache`). Toggling between Outgoing and Incoming buttons returns instantly with 0 additional network calls! Automatically invalidates cache when creating new waybills.
- **📤 Outgoing Active Shipments (`/outgoing` / `📤 Вихідні (що їдуть)`)**: Lists all active outgoing packages sent by the user (both online `2045...` and branch-created `5900...` shipments via `getOutgoingDocumentsByPhone`, matched by sender phone number / counterparty GUID) that have not yet been picked up by the recipient.
- **📥 Incoming Active Shipments (`/incoming` / `📥 Вхідні (що їдуть)`)**: Lists all incoming packages traveling to the user (including branch-created `5900...` shipments via `getIncomingDocumentsByPhone`, matched by recipient phone number) with smart relative delivery date formatting (*"Сьогодні о 18:00"*, *"Завтра о 15:30"*, *"Післязавтра"*, or *"DD.MM.YYYY"*). Filtered to show only uncollected shipments.
- **🔄 Automatic "Легке повернення" (Light Return) Detection**: Identifies waybills created under Nova Poshta's Easy/Light Return service across drafts, outgoing, and incoming parcels, displaying a distinct `🔄 Легке повернення` tag on the waybill card only when applicable!
- **📱 Instant Waybill & Register Code128 Barcodes (`📱 Показати штрихкод`)**: Generate and display scannable high-resolution Code128 PNG barcodes directly in Telegram with a single tap under each waybill card (drafts, outgoing, and incoming shipments) as well as ScanSheet registers for rapid scanning at Nova Poshta branches and postomats!
- **📊 Precise ScanSheet Code128 Barcodes**: Generates 100% scannable high-resolution Code128 barcodes directly in Telegram photos, preserving exact register numbers with hyphens (e.g. `105-79184007`) matching Nova Poshta warehouse scanners.
- **💰 Configurable Declared Value & Defaults**: Default minimum declared value set to 500 UAH with quick interactive toggle buttons (500, 1000, 2000, 5000, 10000 UAH).
- **🔌 Multi-Provider AI Support**:
  1. **OpenAI-compatible endpoints** (e.g. `gemini-web2api v1.2.9` listening on `http://localhost:8081/v1` with `gemini-3.6-flash`).
  2. **Official OpenAI API** (`gpt-4o-mini`, etc.).
  3. **Official Google Gemini API**.
- **📍 Nova Poshta API 2.0 Integration**:
  - Validates city names against Nova Poshta settlement database.
  - Resolves branch or postomat numbers into exact GUID references.
  - Automatically creates Recipient Counterparties & Contact Persons.
  - Generates Express Waybills (ТТН) with instant tracking links.
- **🔄 Interactive Data Verification & Options**:
  - Displays a detailed confirmation card for user verification before generating the waybill.
  - Inline buttons allow toggling **Payer** (*Recipient* / *Sender*) and **Cargo Type** (*Parcel* / *Documents*).
- **🛠 CLI Sender Setup Utility**:
  - Included helper script `python -m src.cli.fetch_sender_info` to automatically retrieve your Nova Poshta Sender GUID references and populate `.env`.
- **🔐 Multi-Tenant & Security First**:
  - Sensitive credentials strictly isolated in `.env`.
  - Production-ready for deployment on remote servers (systemd, Docker, or background process).

---

## 🛠 Prerequisites

- Python 3.10+
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Nova Poshta API Key 2.0 (from [my.novaposhta.ua](https://my.novaposhta.ua/))
- AI API Key (OpenAI, Gemini, or local `gemini-web2api`)

---

## 🚀 Quick Start & Installation

### 1. Clone Repository & Setup Virtual Environment

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy `.env.example` to `.env`:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and insert your credentials:

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
NOVA_POSHTA_API_KEY=your_nova_poshta_api_key

# AI Provider (openai_compatible | openai | gemini)
AI_PROVIDER=openai_compatible
AI_BASE_URL=http://localhost:8081/v1
AI_MODEL=gemini-3.6-flash
```

### 3. Automatically Fetch Sender Credentials

Run the built-in CLI helper to list and verify your Nova Poshta Sender credentials:

```powershell
python -m src.cli.fetch_sender_info
```

Copy the printed `SENDER_*` values into your `.env` file.

### 4. Run the Bot

```powershell
python -m src.bot.main
```

---

## 🧪 Running Unit Tests

Run unit tests in parallel:

```powershell
pytest -n auto
```

---

## 📁 Project Structure

```
nova_poshhta_bot/
├── docs/
│   └── credentials_guide.md       # Step-by-step guide to obtaining API keys
├── src/
│   ├── ai/
│   │   ├── extractor.py           # LLM parser with custom base URL support
│   │   └── schemas.py             # Pydantic schemas for extracted data
│   ├── bot/
│   │   ├── handlers.py            # aiogram handlers & verification callbacks
│   │   ├── keyboards.py           # Interactive inline keyboards
│   │   └── main.py                # Bot polling entry point
│   ├── cli/
│   │   └── fetch_sender_info.py   # CLI helper for Nova Poshta sender refs
│   ├── nova_poshta/
│   │   ├── client.py              # Async Nova Poshta API 2.0 client
│   │   └── models.py              # Response/Request models
│   └── config.py                  # Pydantic BaseSettings configuration
├── tests/                         # Unit tests
├── .env.example
├── .gitignore
├── plan.md                        # Development plan (Ukrainian)
├── Task.md                        # Task checklist (Ukrainian)
├── Walkthrough.md                 # Change log (Ukrainian)
└── requirements.txt
```

---

## 📄 License

MIT License
