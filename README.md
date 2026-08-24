# Nova Poshta AI Waybill Generator Telegram Bot 📦🤖

An intelligent Telegram Bot built with Python (`aiogram 3.x`) and AI (OpenAI API / Gemini API / local OpenAI-compatible endpoints) that automatically extracts structured recipient details from free-form text messages and generates Nova Poshta Express Waybills (ТТН).

---

## ✨ Features

- **💰 Monthly Cash On Delivery (COD) Tracking & Limits ("💰 Накладений платіж" / `/cod`)**: Fully tracks monthly Cash On Delivery (післяплата) shipments directly via Nova Poshta API 2.0 (`InternetDocument/getDocumentList`). Features include:
  - **Live Monthly Calculation**: Computes total COD volume (UAH sum and parcel count) from the 1st day of the current calendar month to today without caching, performing live API queries every time.
  - **Safe Financial Monitoring Ceiling (< 30,000 UAH / Max 29,999 UAH)**: Strictly warns when approaching or reaching 30,000 UAH (enforcing safe max limit of `29,999 грн` / `limit - 1`).
  - **Automatic Monthly Reset**: Statistics automatically reset on the 1st of every new month without requiring manual maintenance.
  - **Detailed Status Breakdown**: Categorizes shipments into 🟢 Received/Disbursed (Виплачено), 🚚 In Transit / Awaiting Pickup (У дорозі), 📝 Drafts (Чернетки), and 🔴 Refused/Returned (Відмови).
  - **Configurable Limits & Progress Bar**: Personal monthly limits (defaults: 30,000 UAH / 10 parcels) with visual emoji progress bars (`🟩🟩🟩🟨⬜ 85%`), customizable via quick-preset inline buttons or commands (`/set_cod_limit`, `/set_cod_count`).
  - **Proactive Threshold Warnings**: Displays timely warnings on parcel creation confirmation cards if creating a COD parcel will exceed or approach (>80%) monthly limits.
  - **Direct Waybill Explorer Button ("📜 Накладні та суми наложки (ТТН)")**: One-tap button directly under the COD stats post to view a paginated list of all COD shipments sent during the current month with exact amounts, creation dates, recipients, and payout mechanisms (💳 Card vs 💵 Cash).

- **⏱️ 15-Minute Session TTL & Continuous Parcel Editing**: Active parcel creation sessions stay live for 15 minutes (`SESSION_TIMEOUT_SECONDS = 900`). Every user interaction (sending text, clicking buttons, or selecting locations) refreshes the activity timestamp, allowing users to freely adjust declared value, parcel description, COD, or payer details over multiple messages without session loss, while inactive sessions are automatically purged.

- **🎯 Smart City & Warehouse Retention (No Disambiguation Loop)**: Eliminates repeating city disambiguation prompts! When a user chooses a city from candidates (or sends details for ambiguous cities), the chosen city and warehouse are locked into the active session. Follow-up updates (e.g. changing declared value to 50,000 UAH or specifying cargo description) seamlessly update the parcel draft without re-triggering city search or prompting for the city again.
- **🩹 Extended Entity Healing for Declared Values & Cargo Descriptions**: Regex and heuristic extraction automatically detects phrases like *"оцінка 50000 грн"*, *"в посилці steam deck OLED 512 та Odin 2 portal"*, *"наложка 15000 на картку"*, ensuring immediate and accurate updates even if the user sends free-form follow-up texts.
- **🏡 Intelligent Courier Address Delivery & Street Selection ("🏡 Адресна доставка кур'єром")**: Seamlessly detects and creates courier door delivery waybills (`WarehouseDoors`)! When recipient info specifies an address, the bot uses multi-permutation street search (e.g. mapping *"вул. Віри Гордієнко"* -> *"вул. Гордієнко Віри"*) with prefix stripping and similarity ranking. If multiple matching candidates exist (e.g. street vs lane), the bot displays an interactive keyboard (`[ 🏡 вул. ... ]`, `[ 🏡 пров. ... ]`) so users can accurately choose.
- **🛡️ Dual-Layer AI & Regex Entity Healing (`heal_parsed_recipient_info`)**: Bulletproof recipient entity extraction! If an AI model returns incomplete fields or encounters an issue, an automatic regex/heuristic healing pipeline scans the text to extract phones, postomats/branches, cities, and names with initials (`Мартинюк Є.В.`), ensuring 100% extraction reliability.
- **📩 Forwarded Message & Metadata Handling**: Automatically ignores Telegram forwarding metadata (`"Переслано від Vlad Martyniuk"`, `"Forwarded from..."`), extracting the real recipient details from the message body without confusion.
- **📦 Postomat & Russian/Surzhyk Variations Support**: Seamlessly handles terms like `"поштомат"`, `"паштомат"`, `"пм"`, `"відд"`, `"отделение"`, and postomats with location addresses in parentheses (e.g. `почтомат НП 24991 (просп Князя Володимира Великого 75А, 3 під'їзд)`) without false address delivery triggers.
- **🗣️ Full Natural Language & Voice Waybill Editing**: Modify any part of a waybill draft at any time using plain text or voice messages (e.g., *"зміни прізвище на Петренко"*, *"новий телефон 097..."*, *"відправ у Львів"*, *"поміняй на відділення №5"*, *"вулиця Франка 10, а не провулок"*, *"опис планшет"*, *"оцінка 15000"*, *"платник відправник"*).
- **🔒 Seamless Session Settings Persistence**: When toggling buttons (such as switching payer to *Sender*, cargo to *Documents*, or custom COD/declared values) and subsequently modifying the waybill via text/voice, all existing user configurations are strictly preserved without being reset to defaults.
- **⚡ Compact Session-Driven Inline Keyboards**: Uses ultra-lightweight callback data schemas (`WaybillActionCallback`) preventing Telegram's 64-byte payload limits even with high declared values or custom COD amounts (e.g., 15,000+ UAH).
- **📋 AI-Powered ScanSheet Registers & Code128 Barcodes (`/registers`)**: Automatically builds Nova Poshta ScanSheet registers using LLM intelligence over user waybill drafts! Users can create registers using arbitrary natural language requests (e.g. *"створи реєстр з усіх моїх чернеток"*, *"створи реєстр з накладної 20451506611097"*, *"об'єднай у реєстр вчорашні посилки"*, *"створи реєстр де опис сувенір"*). The bot fetches all active un-shipped drafts directly from Nova Poshta API + local storage, feeds them as a structured JSON payload with timestamp context to the AI, receives the exact matching TTN list, algorithmically registers the ScanSheet via Nova Poshta API, and delivers a scannable Code128 PNG barcode photo along with detailed waybill summaries (recipient, city/branch, cargo description, COD/declared value) and instant unbind/disband buttons!
- **🤖 AI Entity Extraction & Conversational Chat**: Parses unstructured text messages (Full Name, Phone, City, Branch/Postomat number, Cargo Description, Declared Value) while also intelligently handling general chat messages, greetings, and queries about bot capabilities.
- **📩 Multi-Part & Reposted Message Accumulation**: When users forward/repost multiple messages in sequence (e.g., 1st message with Name/Phone, 2nd message with City/Branch), the bot saves partial context and seamlessly merges all incoming messages into a single complete waybill draft!
- **🔄 Active Session Context Memory & Live Draft Editing**: Remembers active recipient context. Users can edit any saved draft using live natural language (e.g. typing *"зміни опис на сувенір"* or *"оцінка 2000 грн"*), updating the waybill live in Nova Poshta database via `InternetDocument/update`!
- **⌨️ Persistent Reply Keyboard Menu**: Convenient Telegram bottom menu (`📦 Активні посилки`, `📝 Мої чернетки (ТТН)`, `⚙️ Налаштування`, `❓ Допомога`) for quick 1-tap navigation without needing slash commands.
- **📄 Waybill Drafts Management & Synchronization (`/drafts` / `📝 Мої чернетки (ТТН)`)**: View active waybill drafts with live status tracking (`TrackingDocument/getStatusDocuments`). Automatically filters out and purges both physically shipped waybills and deleted/cancelled waybills (`StatusCode 2`, `3`, `"Видалено"`, `"Номер не знайдено"`), ensuring only genuine active un-shipped drafts are shown, with instant action buttons (`✏️ Редагувати ТТН` / `🗑 Видалити ТТН`).
- **🔐 Strict Multi-Tenant & Per-User Isolation (NP & AI)**: Each Telegram user has their own isolated profile in `user_settings.json`. New unconfigured users are protected by an Onboarding Guard (`ensure_user_configured`) and must bind both their personal Nova Poshta API key (`/set_np_key`) and personal AI API key (`/set_ai_key`, `/set_ai_url`, `/set_ai_model`), ensuring zero credential leakage between users!
- **🔍 Smart Missing Info Prompting**: Automatically validates extracted details and prompts the user if required recipient info (Full Name, Phone, City, or Branch) is missing.
- **⚡ 5-Minute In-Memory Waybill Caching**: Caches raw Nova Poshta waybill API responses for 5 minutes (`_fetch_raw_waybills_with_cache`). Toggling between Outgoing and Incoming buttons returns instantly with 0 additional network calls! Automatically invalidates cache when creating new waybills.
- **📤 Outgoing Active Shipments (`/outgoing` / `📤 Вихідні (що їдуть)`)**: Lists all active outgoing packages sent by the user (strictly matched by sender phone number / counterparty GUID) that have not yet been picked up by the recipient.
- **📥 Incoming Active Shipments (`/incoming` / `📥 Вхідні (що їдуть)`)**: Lists all incoming packages traveling to the user (strictly matched by recipient phone number) with smart relative delivery date formatting (*"Сьогодні о 18:00"*, *"Завтра о 15:30"*, *"Післязавтра"*, or *"DD.MM.YYYY"*). Filtered to show only uncollected shipments.
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
