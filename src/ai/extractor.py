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
   If the user greets you, asks who you are ("хто ти", "що ти робиш"), asks what you need or how to use you ("що тобі треба", "які дані потрібні", "як з тобою працювати"), or asks general questions WITHOUT any delivery recipient info:
   - Set `is_recipient_info`: false
   - Set `conversational_response`: Provide a polite, friendly response in Ukrainian explaining:
     • Who you are: AI assistant for instant Nova Poshta Express Waybill (ТТН) generation.
     • What data you need: Recipient Full Name (ПІБ), Phone (телефон), City (місто), Branch or Postomat number (номер відділення або поштомату), Optional cargo description and declared value.
     • Available features: View active shipments, manage waybill drafts, toggle payer & cargo settings.

2. RECIPIENT INFO OR CONTEXTUAL UPDATE / EDIT INTENT (is_recipient_info: true):
   If the user provides recipient delivery information OR sends follow-up / modification messages in natural language (e.g. changing name, phone, city, branch, street address, cargo description, declared value, or COD):
   - Set `is_recipient_info`: true
   - ALWAYS MERGE previous active recipient data with new updates. Keep all non-null fields from previous data unless the new message explicitly updates or replaces them.

   - TELEGRAM FORWARDED HEADERS:
     Ignore Telegram forwarding metadata headers like "Переслано від <Name>", "Forwarded from <Name>", or "Переслане повідомлення".
     Extract the recipient details strictly from the message payload body (e.g. in "Переслано від Vlad Martyniuk ... Мартинюк Є.В.", the recipient name is "Мартинюк Є.В.", NOT "Vlad Martyniuk").

   - RECIPIENT NAME & INITIALS:
     Support Ukrainian names with initials format like "Мартинюк Є.В.", "Ковальчук Р. О.", "Іванов І.":
     • last_name: "Мартинюк"
     • first_name: "Є."
     • middle_name: "В."
     NEVER discard or leave name null when initials are provided!

   - POSTOMAT & BRANCH TERMINOLOGY:
     Support Ukrainian, Russian, and Surzhyk terms:
     • "поштомат", "почтомат", "паштомат", "пм", "поштомат НП" -> set `is_postomat: true`, `warehouse_number: <int>`
     • "відділення", "відд", "склад", "отделение" -> set `is_postomat: false`, `warehouse_number: <int>`
     • If a postomat or branch number is specified (e.g. "почтомат НП 24991") followed by a physical location address in parentheses (e.g. "(просп Князя Володимира Великого 75А, 3 під'їзд)"), extract `warehouse_number: 24991`, `is_postomat: true`, and set `has_address_suspicion: false` and `is_address_delivery: false`.

   - PHONE LABELS:
     Extract phone number even if prefixed with "Тел.", "тел:", "номер", "т." (e.g. "Тел. 0674840376" -> "0674840376").

   - Specific update rules:
     • Recipient Name update (e.g. "зміни прізвище на Іваненко", "отримувач Шевченко Тарас", "поміняй ім'я на Арсеній"): update `last_name`, `first_name`, and/or `middle_name`.
     • Phone update (e.g. "зміни телефон на 0971234567", "новий номер 0502850704"): update `phone`.
     • City update (e.g. "місто Львів", "відправ у Київ", "поміняй місто на Одесу"): update `city_name`.
     • Branch/Postomat update (e.g. "відділення 12", "поштомат 2548", "поміняй на відділення №1"): update `warehouse_number`, `is_postomat`, and set `is_address_delivery: false`, `street_name: null`, `building_number: null`.
     • Address / Street / House update (e.g. "вулиця Франка 10", "вулиця Віри Гордієнко, а не провулок. Саме вулиця", "зміни адресу на вул. Соборна 25 кв 14"): update `street_name`, `building_number`, `flat_number`, set `is_address_delivery: true`, `has_address_suspicion: false`, and set `warehouse_number: null`.
     • Cargo Description update (e.g. "зміни опис на планшет", "опис сувенір", "товар: одяг"): update `cargo_description`.
     • Declared Value update (e.g. "оцінка 5000 грн", "зміни оцінку на 15000"): update `declared_value`.
     • COD update (e.g. "додай наложку 15000 на картку", "накладений платіж 3000 готівка", "прибери наложку"): update `cod_amount` (0 if removed) and `cod_payment_type` ("cash" or "card").
     • Payer update (e.g. "платник відправник", "я оплачу", "оплата відправник"): update `payer_type` to "Sender"; ("платник отримувач", "оплата отримувачем"): update `payer_type` to "Recipient".
     • Cargo Type update (e.g. "тип вантажу документи", "документи"): update `cargo_type` to "Documents"; ("посилка"): update `cargo_type` to "Parcel".

   - Extract/merge the following fields:
     • last_name: Recipient's last name (Прізвище)
     • first_name: Recipient's first name (Ім'я)
     • middle_name: Recipient's patronymic/middle name (По-батькові), if present
     • phone: Phone number normalized starting with 380 or 0 (e.g. 380971234567 or 0971234567)
     • city_name: Settlement name without prefix (e.g. "Київ", "Одеса", "Дніпро", "Рівне", "Сміла")
     • region_name: Oblast name if specified (e.g. "Дніпропетровська", "Черкаська", "Київська")
     • district_name: District/Raion name if specified
     • settlement_type: Settlement type if specified ("місто", "село", "смт")
     • warehouse_number: Primary integer branch or postomat number (e.g. 36, 12, 26584, 24991). 
       IMPORTANT: If the text includes a branch/postomat number AND a physical street address of the branch, extract ONLY the branch/postomat integer ID (`36` or `24991`).
     • is_postomat: Boolean (true if text mentions "поштомат"/"почтомат", false for "відділення")
     • street_name, building_number, flat_number: extract personal home/office street name, building/house number, flat/apartment number.
     • has_address_suspicion: Boolean (true if text mentions personal home/office street address, apartment, or keywords "додому", "кур'єром", "на адресу", "вул.", "буд.", "кв." WITHOUT a branch/postomat number)
     • is_address_delivery: Boolean (true if user explicitly confirmed or requested courier door delivery or specified a street with house number without warehouse)
     • cargo_description: Item description if mentioned or updated
     • declared_value: Declared value number if mentioned or updated
     • cod_amount: Cash on Delivery amount in UAH if specified
     • cod_payment_type: "cash" or "card" if specified
     • payer_type: "Sender" or "Recipient" if explicitly specified
     • cargo_type: "Parcel" or "Documents" if explicitly specified

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


UKRAINIAN_CITIES_REFERENCE = [
    "Київ", "Одеса", "Харків", "Дніпро", "Львів", "Запоріжжя", "Кривий Ріг",
    "Миколаїв", "Вінниця", "Полтава", "Чернігів", "Черкаси", "Житомир", "Суми",
    "Хмельницький", "Чернівці", "Рівне", "Кам'янське", "Кропивницький",
    "Івано-Франківськ", "Кременчук", "Тернопіль", "Луцьк", "Біла Церква",
    "Ужгород", "Нікополь", "Бровари", "Бердянськ", "Павлоград",
    "Кам'янець-Подільський", "Мукачево", "Конотоп", "Умань", "Олександрія",
    "Дрогобич", "Бердичів", "Шостка", "Бахмут", "Ізмаїл", "Новомосковськ",
    "Ковель", "Ніжин", "Сміла", "Калуш", "Червоноград", "Первомайськ",
    "Бориспіль", "Коростень", "Коломия", "Чорноморськ", "Стрий", "Прилуки",
    "Лозова", "Новоград-Волинський", "Енергодар", "Нововолинськ", "Горішні Плавні",
    "Ізюм", "Білгород-Дністровський", "Ірпінь", "Буча", "Вишневе", "Васильків",
    "Обухів", "Боярка", "Вишгород", "Фастів", "Трускавець", "Самбір", "Чортків",
]


class AIExtractor:
    """Async AI extractor leveraging OpenAI client with robust entity healing."""

    def __init__(self, settings: Settings):
        self.settings = settings
        base_url = settings.ai_base_url if settings.ai_provider == "openai_compatible" else None

        self.client = AsyncOpenAI(
            api_key=settings.ai_api_key,
            base_url=base_url,
        )
        self.model = settings.ai_model

    @classmethod
    def heal_parsed_recipient_info(
        cls, text: str, parsed: ParsedRecipientInfo
    ) -> ParsedRecipientInfo:
        """Heal and fill missing recipient fields from raw text via robust regex heuristics."""
        import re

        if not text or not text.strip():
            return parsed

        if parsed.is_register_intent:
            return parsed

        # 1. Phone extraction / normalization
        if not parsed.phone:
            phone_match = re.search(
                r'(?:(?:\+?38)?\s*\(?(0\d{2})\)?[\s\-]?(\d{3})[\s\-]?(\d{2})[\s\-]?(\d{2})|\b(0\d{9})\b|\b(\+?380\d{9})\b)',
                text,
            )
            if phone_match:
                raw_phone = "".join(c for c in phone_match.group(0) if c.isdigit())
                if len(raw_phone) == 10 and raw_phone.startswith("0"):
                    parsed.phone = raw_phone
                elif len(raw_phone) == 12 and raw_phone.startswith("380"):
                    parsed.phone = raw_phone
                elif len(raw_phone) == 9:
                    parsed.phone = "0" + raw_phone

        # 2. Postomat / Branch extraction
        if not parsed.warehouse_number:
            # Check postomat first (Ukrainian / Russian / Surzhyk variations)
            postomat_match = re.search(
                r'(?:поштомат|почтомат|паштомат|пм|поштоматі|почтоматі)\s*(?:нп|№|номер)?\s*(\d{1,6})',
                text,
                re.IGNORECASE,
            )
            if postomat_match:
                parsed.warehouse_number = int(postomat_match.group(1))
                parsed.is_postomat = True
            else:
                # Check branch
                branch_match = re.search(
                    r'(?:відділення|відділенні|відд|склад|складі|отделение)\s*(?:нп|№|номер)?\s*(\d{1,5})',
                    text,
                    re.IGNORECASE,
                )
                if branch_match:
                    parsed.warehouse_number = int(branch_match.group(1))
                    parsed.is_postomat = False
                else:
                    # Check generic "НП 24991" or "№ 24991"
                    generic_match = re.search(r'(?:нп|№|номер)\s*(\d{1,6})', text, re.IGNORECASE)
                    if generic_match:
                        num = int(generic_match.group(1))
                        parsed.warehouse_number = num
                        if num > 1000:
                            parsed.is_postomat = True

        # 3. City extraction
        if not parsed.city_name:
            for city in UKRAINIAN_CITIES_REFERENCE:
                if re.search(rf'\b{re.escape(city)}\b', text, re.IGNORECASE):
                    parsed.city_name = city
                    break
            if not parsed.city_name:
                city_prefix_match = re.search(
                    r'(?:м\.|місто|смт|с\.)\s*([А-ЯЄІЇҐ][а-яєіїґ\'-]+)', text, re.IGNORECASE
                )
                if city_prefix_match:
                    parsed.city_name = city_prefix_match.group(1)

        # 4. Name with initials or full name
        if not parsed.last_name:
            clean_lines = [
                line
                for line in text.splitlines()
                if not re.search(
                    r'(?:переслано від|forwarded from|переслане повідомлення)', line, re.IGNORECASE
                )
            ]
            clean_body = "\n".join(clean_lines)

            # Check initials format: "Мартинюк Є.В." or "Мартинюк Є. В."
            initials_match = re.search(
                r'\b([А-ЯЄІЇҐ][а-яєіїґ\']+)\s+([А-ЯЄІЇҐ]\.(?:\s*[А-ЯЄІЇҐ]\.)?)', clean_body
            )
            if initials_match:
                parsed.last_name = initials_match.group(1)
                raw_initials = initials_match.group(2).replace(" ", "").split(".")
                parsed.first_name = (
                    raw_initials[0] + "." if len(raw_initials) > 0 and raw_initials[0] else None
                )
                parsed.middle_name = (
                    raw_initials[1] + "." if len(raw_initials) > 1 and raw_initials[1] else None
                )
            else:
                # Check 2 or 3 word name: "Мартинюк Євген Васильович"
                name_match = re.search(
                    r'\b([А-ЯЄІЇҐ][а-яєіїґ\']+)\s+([А-ЯЄІЇҐ][а-яєіїґ\']+)(?:\s+([А-ЯЄІЇҐ][а-яєіїґ\']+))?\b',
                    clean_body,
                )
                if name_match:
                    candidate_last = name_match.group(1)
                    candidate_first = name_match.group(2)
                    if (
                        candidate_last not in UKRAINIAN_CITIES_REFERENCE
                        and candidate_first not in UKRAINIAN_CITIES_REFERENCE
                    ):
                        parsed.last_name = candidate_last
                        parsed.first_name = candidate_first
                        if name_match.group(3):
                            parsed.middle_name = name_match.group(3)

        # 5. Prevent false address delivery suspicion if postomat/branch number was detected
        if parsed.warehouse_number:
            parsed.has_address_suspicion = False
            parsed.is_address_delivery = False

        # 6. Declared value extraction
        if parsed.declared_value is None:
            decl_match = re.search(
                r'(?:оцінка|оціночна\s+вартість|оцінити|цінність|вартість)\s*(?:на|:)?\s*(\d+(?:[.,]\d+)?)\s*(?:грн|uah)?',
                text,
                re.IGNORECASE,
            )
            if not decl_match:
                decl_match = re.search(
                    r'(\d+(?:[.,]\d+)?)\s*(?:грн|uah)?\s*(?:оцінка|оціночна\s+вартість)',
                    text,
                    re.IGNORECASE,
                )
            if decl_match:
                try:
                    parsed.declared_value = float(decl_match.group(1).replace(",", "."))
                except (ValueError, TypeError):
                    pass

        # 7. Cargo description extraction
        if not parsed.cargo_description:
            desc_match = re.search(
                r'(?:в\s+посилці|в\s+посилкі|опис\s+вантажу|опис|вміст|товар|що\s+всередині)\s*(?:на|:)?\s*([^\n\r]+)',
                text,
                re.IGNORECASE,
            )
            if desc_match:
                raw_desc = desc_match.group(1).strip()
                raw_desc = re.sub(
                    r'(?:,?\s*(?:оцінка|оціночна\s+вартість|наложка|накладений\s+платіж)\s*(?:на|:)?\s*\d+.*$)',
                    '',
                    raw_desc,
                    flags=re.IGNORECASE,
                ).strip()
                raw_desc = raw_desc.strip(" ,.-:")
                if raw_desc:
                    parsed.cargo_description = raw_desc

        # 8. COD amount and payout type extraction
        if parsed.cod_amount is None:
            cod_match = re.search(
                r'(?:наложка|накладений\s+платіж|наложенный\s+платеж|післяплата)\s*(?:на|:)?\s*(\d+(?:[.,]\d+)?)\s*(?:грн)?',
                text,
                re.IGNORECASE,
            )
            if cod_match:
                try:
                    parsed.cod_amount = float(cod_match.group(1).replace(",", "."))
                except (ValueError, TypeError):
                    pass
            elif re.search(r'(?:без\s+наложки|зняти\s+наложку|прибрати\s+наложку|наложка\s*0)', text, re.IGNORECASE):
                parsed.cod_amount = 0.0

        if not parsed.cod_payment_type:
            if re.search(r'(?:на\s+картку|на\s+карту|картка|карта)', text, re.IGNORECASE):
                parsed.cod_payment_type = "card"
            elif re.search(r'(?:готівка|готівкою)', text, re.IGNORECASE):
                parsed.cod_payment_type = "cash"

        # 9. Payer type extraction
        if not parsed.payer_type:
            payer_match = re.search(
                r'(?:платник|оплата)\s*(?:за\s*доставку)?\s*(?:на|:)?\s*(відправник|отримувач|я|ми|клієнт)',
                text,
                re.IGNORECASE,
            )
            if payer_match:
                p_word = payer_match.group(1).lower()
                if p_word in ("відправник", "я", "ми"):
                    parsed.payer_type = "Sender"
                elif p_word in ("отримувач", "клієнт"):
                    parsed.payer_type = "Recipient"

        # 10. Cargo type extraction
        if not parsed.cargo_type:
            if re.search(r'\bдокументи\b', text, re.IGNORECASE):
                parsed.cargo_type = "Documents"
            elif re.search(r'\bпосилка\b', text, re.IGNORECASE):
                parsed.cargo_type = "Parcel"

        # If essential recipient details or modification details exist, ensure is_recipient_info is True
        if (
            parsed.phone
            or parsed.city_name
            or parsed.warehouse_number
            or parsed.last_name
            or parsed.street_name
            or parsed.declared_value is not None
            or parsed.cargo_description
            or parsed.cod_amount is not None
            or parsed.payer_type
            or parsed.cargo_type
        ):
            parsed.is_recipient_info = True

        return parsed

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
                empty_res = ParsedRecipientInfo(
                    is_recipient_info=False,
                    conversational_response="Я не зміг розпізнати повідомлення. Будь ласка, надішліть реквізити отримувача (ПІБ, телефон, місто, номер відділення) або скористайтеся кнопками нижче.",
                )
                return self.heal_parsed_recipient_info(text, empty_res)

            data = json.loads(content)
            parsed_res = ParsedRecipientInfo(**data)
            return self.heal_parsed_recipient_info(text, parsed_res)
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
                parsed_res = ParsedRecipientInfo(**data)
                return self.heal_parsed_recipient_info(text, parsed_res)
            except Exception as fallback_err:
                logger.error(f"Fallback AI parsing failed: {fallback_err}")
                empty_res = ParsedRecipientInfo(
                    is_recipient_info=False,
                    conversational_response="Привіт! Я AI-бот для автоматичного створення накладних Нової Пошти (ТТН). Надішліть мені ПІБ отримувача, телефон, місто та номер відділення/поштомату!",
                )
                healed_fallback = self.heal_parsed_recipient_info(text, empty_res)
                if (
                    healed_fallback.phone
                    or healed_fallback.city_name
                    or healed_fallback.warehouse_number
                    or healed_fallback.last_name
                ):
                    return healed_fallback
                return empty_res

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
