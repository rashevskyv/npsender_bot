# Nova Poshta AI Waybill Generator Telegram Bot 📦🤖

An intelligent Telegram Bot built with Python (`aiogram 3.x`) and AI (OpenAI API / Gemini API / local OpenAI-compatible endpoints) that automatically extracts structured recipient details from free-form text messages and generates Nova Poshta Express Waybills (ТТН).

---

## ✨ Features

- **🤖 AI Entity Extraction & Conversational Chat**: Parses unstructured text messages (Full Name, Phone, City, Branch/Postomat number, Cargo Description, Declared Value) while also intelligently handling general chat messages, greetings, and queries about bot capabilities.
- **📩 Multi-Part & Reposted Message Accumulation**: When users forward/repost multiple messages in sequence (e.g., 1st message with Name/Phone, 2nd message with City/Branch), the bot saves partial context and seamlessly merges all incoming messages into a single complete waybill draft!
- **🔄 Active Session Context Memory & Live Draft Editing**: Remembers active recipient context. Users can edit any saved draft using live natural language (e.g. typing *"зміни опис на сувенір"* or *"оцінка 2000 грн"*), updating the waybill live in Nova Poshta database via `InternetDocument/update`!
- **⌨️ Persistent Reply Keyboard Menu**: Convenient Telegram bottom menu (`📦 Активні посилки`, `📝 Мої чернетки (ТТН)`, `⚙️ Налаштування`, `❓ Допомога`) for quick 1-tap navigation without needing slash commands.
- **🇺🇦 Full Ukrainian UI & Localization**: Clean, friendly Ukrainian user interface across all cards, buttons, status updates, and AI prompts.
- **📄 Waybill Drafts Management (`/drafts`)**: View all created waybill drafts (ТТН) with instant action buttons (`✏️ Редагувати ТТН` / `🗑 Видалити ТТН`).
- **🔐 Multi-Tenant & Per-User API Keys**: Users can easily bind their own personal Nova Poshta API key (`/set_np_key`) and AI key (`/set_ai_key`) directly via Telegram chat. The bot automatically validates the key and fetches sender credentials.
- **🔍 Smart Missing Info Prompting**: Automatically validates extracted details and prompts the user if required recipient info (Full Name, Phone, City, or Branch) is missing.
- **📦 Active Shipments List (`/parcels`)**: Fetches active outgoing shipments from Nova Poshta API for the last 30 days and displays them in clean, human-readable markdown cards with live tracking links.
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
