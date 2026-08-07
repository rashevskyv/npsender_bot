# Nova Poshta AI Waybill Generator Telegram Bot 📦🤖

An intelligent Telegram Bot built with Python (`aiogram 3.x`) and AI (OpenAI API / Gemini API / local OpenAI-compatible endpoints) that automatically extracts structured recipient details from free-form text messages and generates Nova Poshta Express Waybills (ТТН).

---

## ✨ Features

- **🤖 AI Entity Extraction**: Parses unstructured text messages (Full Name, Phone, City, Branch/Postomat number) in any order using LLM models.
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
