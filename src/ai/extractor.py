"""AI Entity Extractor module for parsing unstructured recipient info, contextual updates, or answering conversational questions."""

import json
import logging
from typing import Optional
from openai import AsyncOpenAI

from src.config import Settings
from src.ai.schemas import ParsedRecipientInfo

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

2. RECIPIENT INFO OR CONTEXTUAL UPDATE INTENT (is_recipient_info: true):
   If the user provides recipient delivery information OR sends follow-up/reposted/forwarded messages containing missing parts or updates of an active shipment:
   - Set `is_recipient_info`: true
   - ALWAYS MERGE previous active recipient data with new updates. Keep all non-null fields from previous data (last_name, first_name, middle_name, phone, city_name, warehouse_number, etc.) unless the new message explicitly overrides them.
   - Extract/merge the following fields:
     • last_name: Recipient's last name (Прізвище)
     • first_name: Recipient's first name (Ім'я)
     • middle_name: Recipient's patronymic/middle name (По-батькові), if present
     • phone: Phone number normalized starting with 380 or 0 (e.g. 380971234567 or 0971234567)
     • city_name: Settlement name without prefix (e.g. "Київ", "Одеса", "Дніпро", "Рівне", "Черкаське")
     • region_name: Oblast name if specified (e.g. "Дніпропетровська", "Донецька", "Київська")
     • district_name: District/Raion name if specified (e.g. "Новомосковський", "Краматорський")
     • settlement_type: Settlement type if specified ("місто", "село", "смт")
     • warehouse_number: Primary integer branch or postomat number (e.g. 36, 12, 26584). 
       IMPORTANT: If the text includes a branch/postomat number AND a physical street address of the branch (e.g. "Відділення №36 (до 30 кг на одне місце): вул. Княгині Ольги, 8" or "Поштомат №26584 (вул. Миколайчука 15v)"), extract ONLY the branch/postomat integer ID (`36` or `26584`). Ignore weight limits "(до 30 кг)" and street numbers "вул. Княгині Ольги, 8" when determining `warehouse_number`!
     • is_postomat: Boolean (true if text mentions "поштомат", false for "відділення")
     • street_name, building_number, flat_number: extract personal home/office street name, building/house number, flat/apartment number.
     • has_address_suspicion: Boolean (true if text mentions personal home/office street address, apartment, or keywords "додому", "кур'єром", "на адресу", "вул.", "буд.", "кв.")
     • is_address_delivery: Boolean (true if user explicitly confirmed or requested courier door delivery)
     • cargo_description: Item description if mentioned or updated
     • declared_value: Declared value number if mentioned or updated

3. REGISTER & WAYBILL FILTERING INTENT (is_register_intent: true):
   If the user asks to list/filter waybills by date, time, or cargo description, or asks to create a ScanSheet register (e.g. "надай мені всі накладні, які були створені за сьогодні", "створи реєстр з усіх накладних з описом сувенір", "створи реєстр з накладних створених вчора до обіду", "покажи мої реєстри"):
   - Set `is_register_intent`: true
   - Set `register_action`:
     • "create": if user requests to build/create a register (ScanSheet)
     • "list": if user asks to view existing registers
     • "filter_drafts": if user asks to list, view, or discuss waybill drafts matching a filter
   - Set filter criteria:
     • filter_cargo_description: item name if user filtered by cargo description (e.g. "сувенір", "планшет")
     • filter_time_period: "today" (за сьогодні), "yesterday" (за вчора), "yesterday_before_noon" (вчора до обіду), or "all" (усі)

Return ONLY valid JSON matching this schema."""


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
