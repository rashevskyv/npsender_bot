"""AI Entity Extractor module for parsing unstructured recipient info, contextual updates, or answering conversational questions."""

import json
import logging
import datetime
from typing import Optional, List, Dict, Any
from openai import AsyncOpenAI

from src.config import Settings
from src.ai.schemas import ParsedRecipientInfo, AIRegisterFilterResult

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are an intelligent AI assistant for creating Nova Poshta Express Waybills (ТТН) and helping users with shipment details.

Determine the user's intent:

1. CONVERSATIONAL INTENT (is_recipient_info: false):
   If the user greets you, asks who you are ("хто ти", "що ти робиш"), asks what you need or how to use you ("що тобі треба", "які дані потрібні", "як з тобою працювати"), or asks general questions:
   - Set `is_recipient_info`: false
   - Set `conversational_response`: Provide a polite, friendly response in Ukrainian explaining:
     • Who you are: AI assistant for instant Nova Poshta Express Waybill (ТТН) generation.
     • What data you need: Recipient Full Name (ПІБ), Phone (телефон), City (місто), Branch or Postomat number (номер відділення або поштомату), Optional cargo description and declared value.
     • Available features: View active shipments, manage waybill drafts, toggle payer & cargo settings.

2. RECIPIENT INFO OR CONTEXTUAL UPDATE / EDIT INTENT (is_recipient_info: true):
   If the user provides recipient delivery information OR sends follow-up / modification messages in natural language (e.g. changing name, phone, city, branch, street address, cargo description, declared value, or COD):
   - Set `is_recipient_info`: true
   - ALWAYS MERGE previous active recipient data with new updates. Keep all non-null fields from previous data (last_name, first_name, middle_name, phone, city_name, warehouse_number, street_name, building_number, etc.) unless the new message explicitly updates or replaces them.
   - Specific update rules:
     • Recipient Name update (e.g. "зміни прізвище на Іваненко", "отримувач Шевченко Тарас", "поміняй ім'я на Арсеній"): update `last_name`, `first_name`, and/or `middle_name`.
     • Phone update (e.g. "зміни телефон на 0971234567", "новий номер 0502850704"): update `phone`.
     • City update (e.g. "місто Львів", "відправ у Київ", "поміняй місто на Одесу"): update `city_name`.
     • Branch/Postomat update (e.g. "відділення 12", "поштомат 2548", "поміняй на відділення №1"): update `warehouse_number`, `is_postomat`, and set `is_address_delivery: false`, `street_name: null`, `building_number: null`.
     • Address / Street / House update (e.g. "вулиця Франка 10", "вулиця Віри Гордієнко, а не провулок. Саме вулиця", "зміни адресу на вул. Соборна 25 кв 14"): update `street_name`, `building_number`, `flat_number`, set `is_address_delivery: true`, `has_address_suspicion: false`, and set `warehouse_number: null`.
     • Cargo Description update (e.g. "зміни опис на планшет", "опис сувенір", "товар: одяг"): update `cargo_description`.
     • Declared Value update (e.g. "оцінка 5000 грн", "зміни оцінку на 15000"): update `declared_value`.
     • COD update (e.g. "додай наложку 15000 на картку", "накладений платіж 3000 готівка", "прибери наложку"): update `cod_amount` (0 if removed) and `cod_payment_type` ("cash" or "card").

   - Extract/merge the following fields:
     • last_name: Recipient's last name (Прізвище)
     • first_name: Recipient's first name (Ім'я)
     • middle_name: Recipient's patronymic/middle name (По-батькові), if present
     • phone: Phone number normalized starting with 380 or 0 (e.g. 380971234567 or 0971234567)
     • city_name: Settlement name without prefix (e.g. "Київ", "Одеса", "Дніпро", "Рівне", "Сміла")
     • region_name: Oblast name if specified (e.g. "Дніпропетровська", "Черкаська", "Київська")
     • district_name: District/Raion name if specified
     • settlement_type: Settlement type if specified ("місто", "село", "смт")
     • warehouse_number: Primary integer branch or postomat number (e.g. 36, 12, 26584). 
       IMPORTANT: If the text includes a branch/postomat number AND a physical street address of the branch, extract ONLY the branch/postomat integer ID (`36` or `26584`).
     • is_postomat: Boolean (true if text mentions "поштомат", false for "відділення")
     • street_name, building_number, flat_number: extract personal home/office street name, building/house number, flat/apartment number.
     • has_address_suspicion: Boolean (true if text mentions personal home/office street address, apartment, or keywords "додому", "кур'єром", "на адресу", "вул.", "буд.", "кв.")
     • is_address_delivery: Boolean (true if user explicitly confirmed or requested courier door delivery or specified a street with house number without warehouse)
     • cargo_description: Item description if mentioned or updated
     • declared_value: Declared value number if mentioned or updated
     • cod_amount: Cash on Delivery amount in UAH if specified
     • cod_payment_type: "cash" or "card" if specified

3. REGISTER & WAYBILL FILTERING INTENT (is_register_intent: true):
   If the user asks to list/filter waybills by date, time, or cargo description, or asks to create a ScanSheet register (e.g. "надай мені всі накладні, які були створені за сьогодні", "створи реєстр з усіх накладних з описом сувенір", "створи реєстр з накладних створених вчора до обіду", "покажи мої реєстри", "створи реєстр з накладної 2045..."):
   - Set `is_register_intent`: true
   - Set `register_action`:
     • "create": if user requests to build/create a register (ScanSheet)
     • "list": if user asks to view existing registers
     • "filter_drafts": if user asks to list, view, or discuss waybill drafts matching a filter
   - Set filter criteria:
     • filter_cargo_description: item name if user filtered by cargo description (e.g. "сувенір", "планшет")
     • filter_time_period: "today" (за сьогодні), "yesterday" (за вчора), "yesterday_before_noon" (вчора до обіду), or "all" (усі)

Return ONLY valid JSON matching this schema."""


REGISTER_FILTER_SYSTEM_PROMPT = """You are an intelligent Nova Poshta logistics assistant specialized in filtering express waybill drafts (ТТН / чернетки) and selecting waybills to combine into a ScanSheet Register (Реєстр).

You will receive:
1. `current_timestamp`: The exact current date, time and day of week.
2. `drafts`: A JSON array containing all active un-shipped waybill drafts for the user.
3. The user's natural language request.

Your task:
1. Analyze the user's requested action:
   • "create": User explicitly wants to create / make / combine into a register (e.g. "створи реєстр", "зроби реєстр з усіх чернеток", "об'єднай у реєстр", "створи реєстр з накладної 20451506611097", "згенеруй сканшит")
   • "filter_drafts": User only wants to see, find, or list drafts matching criteria without creating a register (e.g. "покажи чернетки за вчора", "знайди накладні на Київ", "які чернетки мають опис сувенір")
   • "list_registers": User wants to view existing registers (e.g. "покажи мої реєстри")
   • "not_found": No drafts in the provided list match the requested criteria

2. Select the matching waybills (`selected_doc_numbers`):
   • Explicit TTN numbers: If user mentions one or more specific TTN numbers (e.g. "20451506611097"), match those exact drafts from `drafts`.
   • All drafts: If user requests all drafts ("з усіх моїх чернеток", "з усіх накладних", "з усіх", "всі чернетки", "створи реєстр") without restricting filters -> return ALL `int_doc_number` values present in `drafts`.
   • Date / Time filters: Use `current_timestamp` to evaluate relative time phrases:
     - "сьогодні" (today) -> drafts where `created_at` date matches current date.
     - "вчора" (yesterday) -> drafts where `created_at` date matches yesterday's date.
     - "вчора до 12:00" / "до обіду" -> drafts created yesterday before 12:00.
     - "за останні 2 дні" -> drafts within past 2 days.
   • Cargo description / keywords: e.g. "сувенір", "планшет", "одяг", "документи" -> match drafts where `cargo_description` contains the query.
   • Recipient name / City / Branch: e.g. "у Кривий Ріг", "для Залужної", "для Юлії" -> match corresponding fields.
   • Payment / COD filters: e.g. "з наложкою", "без наложки", "на картку".

3. Return ONLY valid JSON in this format:
{
  "action": "create" | "filter_drafts" | "list_registers" | "not_found",
  "selected_doc_numbers": ["20451506611097", ...],
  "summary": "Короткий опис вибірки (наприклад: '1 накладна (Залужна Юлія, Кривий Ріг)' або '3 накладні за вчора')",
  "explanation": "Коротке пояснення логіки вибору українською мовою"
}
"""


class AIExtractor:
    """Async AI extractor leveraging OpenAI client."""

    def __init__(self, settings: Settings):
        self.settings = settings
        base_url = settings.ai_base_url if settings.ai_provider == "openai_compatible" else None

        self.client = AsyncOpenAI(
            api_key=settings.ai_api_key,
            base_url=base_url,
        )
        self.model = settings.ai_model

    async def parse_text(
        self, text: str, previous_info: Optional[ParsedRecipientInfo] = None
    ) -> ParsedRecipientInfo:
        """Parse unstructured text with optional previous context for merging updates."""
        user_prompt = text
        if previous_info and previous_info.is_recipient_info:
            prev_json = previous_info.model_dump_json(exclude_none=True)
            user_prompt = (
                f"Existing Active Recipient Data:\n{prev_json}\n\n"
                f"User Follow-up Message:\n{text}\n\n"
                "Please update the active recipient data with any new details from the follow-up message."
            )

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            if not content:
                logger.warning("Empty response from AI model")
                return ParsedRecipientInfo(
                    is_recipient_info=False,
                    conversational_response="Я не зміг розпізнати повідомлення. Будь ласка, надішліть реквізити отримувача (ПІБ, телефон, місто, номер відділення) або скористайтеся кнопками нижче.",
                )

            data = json.loads(content)
            return ParsedRecipientInfo(**data)
        except Exception as e:
            logger.error(f"Error parsing recipient text with AI: {e}")
            try:
                # Fallback without json_object constraint if unsupported
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                raw_text = response.choices[0].message.content or ""
                clean_text = raw_text.replace("```json", "").replace("```", "").strip()
                data = json.loads(clean_text)
                return ParsedRecipientInfo(**data)
            except Exception as fallback_err:
                logger.error(f"Fallback AI parsing failed: {fallback_err}")
                return ParsedRecipientInfo(
                    is_recipient_info=False,
                    conversational_response="Привіт! Я AI-бот для автоматичного створення накладних Нової Пошти (ТТН). Надішліть мені ПІБ отримувача, телефон, місто та номер відділення/поштомату!",
                )

    async def filter_drafts_for_register(
        self, user_prompt: str, drafts: List[Dict[str, Any]]
    ) -> AIRegisterFilterResult:
        """Evaluate active drafts against user prompt with AI to select TTNs for register creation."""
        now = datetime.datetime.now()
        time_context = {
            "current_timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "day_of_week": now.strftime("%A"),
            "today_date": now.strftime("%Y-%m-%d"),
            "yesterday_date": (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d"),
        }

        user_content = (
            f"Context:\n{json.dumps(time_context, ensure_ascii=False, indent=2)}\n\n"
            f"Active Waybill Drafts ({len(drafts)}):\n{json.dumps(drafts, ensure_ascii=False, indent=2)}\n\n"
            f"User Request:\n{user_prompt}"
        )

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": REGISTER_FILTER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            if not content:
                logger.warning("Empty response from AI model for register filtering")
                return AIRegisterFilterResult(action="not_found", selected_doc_numbers=[])

            data = json.loads(content)
            return AIRegisterFilterResult(**data)
        except Exception as e:
            logger.error(f"Error filtering drafts for register with AI: {e}")
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": REGISTER_FILTER_SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                )
                raw_text = response.choices[0].message.content or ""
                clean_text = raw_text.replace("```json", "").replace("```", "").strip()
                data = json.loads(clean_text)
                return AIRegisterFilterResult(**data)
            except Exception as fallback_err:
                logger.error(f"Fallback AI register filtering failed: {fallback_err}")
                # Programmatic fallback: if user prompt mentions "всі" or "усі" or "реєстр", select all drafts
                all_nums = [str(d.get("int_doc_number", "")) for d in drafts if d.get("int_doc_number")]
                return AIRegisterFilterResult(
                    action="create" if all_nums else "not_found",
                    selected_doc_numbers=all_nums,
                    summary=f"Усі активні чернетки ({len(all_nums)})" if all_nums else None,
                    explanation="Автоматичний вибір усіх чернеток через недоступність AI",
                )
