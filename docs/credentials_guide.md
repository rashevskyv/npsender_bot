# Step-by-Step Guide: Obtaining Required Credentials

To run the Nova Poshta AI Waybill Generator Bot, you need credentials for Telegram, Nova Poshta API, and an AI provider (OpenAI or Gemini).

---

## 1. Telegram Bot Token
1. Open Telegram and search for `@BotFather`.
2. Send the command `/newbot`.
3. Enter a display name for your bot (e.g., `My NP Waybill Generator`).
4. Enter a unique username ending in `bot` (e.g., `my_np_waybill_bot`).
5. `@BotFather` will reply with an **API Token** (formatted like `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`).
6. Copy this token to your `.env` file under `TELEGRAM_BOT_TOKEN`.

---

## 2. Nova Poshta API Key & Sender Details

### A. API Key
1. Log in to your Nova Poshta personal account at [my.novaposhta.ua](https://my.novaposhta.ua/).
2. Navigate to **Налаштування** (Settings) -> **Безпека** (Security) / **API 2.0**.
3. Click **Створити ключ** (Create Key).
4. Copy the generated key (a 32-character string).
5. Set this key in your `.env` file under `NOVA_POSHTA_API_KEY`.

### B. Sender Counterparty Ref & Contact Person Ref
To create waybills on behalf of your Nova Poshta account, Nova Poshta requires internal unique IDs (GUID references) for:
- Sender Counterparty (`SenderRef`)
- Sender Contact Person (`ContactSenderRef`)
- Sender City (`CitySenderRef`)
- Sender Warehouse / Address (`SenderAddressRef`)

*Note: The bot includes a built-in helper utility command (`python -m src.cli.fetch_sender_info`) that automatically fetches these references directly from Nova Poshta API using your `NOVA_POSHTA_API_KEY` and populates them into your `.env` file!*

---

## 3. OpenAI API Key (or Gemini API Key)

### OpenAI (Recommended)
1. Go to [platform.openai.com](https://platform.openai.com/).
2. Sign up or log in.
3. Go to **API Keys** -> **Create new secret key**.
4. Copy the key (`sk-...`).
5. Set this key in `.env` under `OPENAI_API_KEY`.

### Google Gemini (Alternative)
1. Go to [Google AI Studio](https://aistudio.google.com/).
2. Click **Get API key** -> **Create API key**.
3. Copy the generated key.
4. Set this key in `.env` under `GEMINI_API_KEY`.
