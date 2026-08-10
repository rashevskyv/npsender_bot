# Nova Poshta AI Waybill Generator Telegram Bot 📦🤖

An intelligent Telegram Bot built with Python (`aiogram 3.x`) and AI (OpenAI API / Gemini API / local OpenAI-compatible endpoints) that automatically extracts structured recipient details from free-form text messages and generates Nova Poshta Express Waybills (ТТН).

---

## ✨ Features

- **🏡 Courier Address Delivery Support ("🏡 Адресна доставка кур'єром")**: Seamlessly detects and creates courier door delivery waybills (`WarehouseDoors`)! When recipient information includes a street name and house number without a branch/postomat, the bot prompts the user with an interactive choice. It automatically queries `Address/getStreet` to validate the street in the destination city, registers the recipient's street address via `Address/save`, and creates the waybill with recipient address delivery.
- **📋 AI-Powered ScanSheet Registers & Code128 Barcodes (`/registers`)**: Automatically builds Nova Poshta ScanSheet registers using LLM intelligence over user waybill drafts! Users can create registers using arbitrary natural language requests (e.g. *"створи реєстр з усіх моїх чернеток"*, *"створи реєстр з накладної 20451506611097"*, *"об'єднай у реєстр вчорашні посилки"*, *"створи реєстр де опис сувенір"*). The bot fetches all active un-shipped drafts directly from Nova Poshta API + local storage, feeds them as a structured JSON payload with timestamp context to the AI, receives the exact matching TTN list, algorithmically registers the ScanSheet via Nova Poshta API, and delivers a scannable Code128 PNG barcode photo along with detailed waybill summaries (recipient, city/branch, cargo description, COD/declared value) and instant unbind/disband buttons!
- **🤖 AI Entity Extraction & Conversational Chat**: Parses unstructured text messages (Full Name, Phone, City, Branch/Postomat number, Cargo Description, Declared Value) while also intelligently handling general chat messages, greetings, and queries about bot capabilities.
- **📩 Multi-Part & Reposted Message Accumulation**: When users forward/repost multiple messages in sequence (e.g., 1st message with Name/Phone, 2nd message with City/Branch), the bot saves partial context and seamlessly merges all incoming messages into a single complete waybill draft!
- **🔄 Active Session Context Memory & Live Draft Editing**: Remembers active recipient context. Users can edit any saved draft using live natural language (e.g. typing *"зміни опис на сувенір"* or *"оцінка 2000 грн"*), updating the waybill live in Nova Poshta database via `InternetDocument/update`!
- **⌨️ Persistent Reply Keyboard Menu**: Convenient Telegram bottom menu (`📦 Активні посилки`, `📝 Мої чернетки (ТТН)`, `⚙️ Налаштування`, `❓ Допомога`) for quick 1-tap navigation without needing slash commands.
- **🇺🇦 Full Ukrainian UI & Localization**: Clean, friendly Ukrainian user interface across all cards, buttons, status updates, and AI prompts.
- **📄 Waybill Drafts Management (`/drafts`)**: View active waybill drafts with live status tracking (`TrackingDocument/getStatusDocuments`). Automatically filters out and purges shipments that have already been handed over/sent to Nova Poshta so only genuine un-shipped drafts are shown, with instant action buttons (`✏️ Редагувати ТТН` / `🗑 Видалити ТТН`).
- **🔐 Strict Multi-Tenant & Per-User Isolation (NP & AI)**: Each Telegram user has their own isolated profile in `user_settings.json`. New unconfigured users are protected by an Onboarding Guard (`ensure_user_configured`) and must bind both their personal Nova Poshta API key (`/set_np_key`) and personal AI API key (`/set_ai_key`, `/set_ai_url`, `/set_ai_model`), ensuring zero credential leakage between users!
- **🔍 Smart Missing Info Prompting**: Automatically validates extracted details and prompts the user if required recipient info (Full Name, Phone, City, or Branch) is missing.
- **⚡ 5-Minute In-Memory Waybill Caching**: Caches raw Nova Poshta waybill API responses for 5 minutes (`_fetch_raw_waybills_with_cache`). Toggling between Outgoing and Incoming buttons returns instantly with 0 additional network calls! Automatically invalidates cache when creating new waybills.
- **📤 Outgoing Active Shipments (`/outgoing` / `📤 Вихідні (що їдуть)`)**: Lists all active outgoing packages sent by the user (strictly matched by sender phone number / counterparty GUID) that have not yet been picked up by the recipient.
- **📥 Incoming Active Shipments (`/incoming` / `📥 Вхідні (що їдуть)`)**: Lists all incoming packages traveling to the user (strictly matched by recipient phone number) with smart relative delivery date formatting (*"Сьогодні о 18:00"*, *"Завтра о 15:30"*, *"Післязавтра"*, or *"DD.MM.YYYY"*). Filtered to show only uncollected shipments.
- **💵 Cash on Delivery (COD / Накладений платіж)**: AI automatically detects COD amounts from user text (e.g., *"наложка 1500 грн"*). Includes interactive inline button (`🔄 💰 Наложка: 1500 грн`) in confirmation cards, setting `BackwardDeliveryData` in Nova Poshta API with support for cash payouts or bank card transfers (`/set_card`).
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
