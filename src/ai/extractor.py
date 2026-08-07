"""AI Entity Extractor module for parsing unstructured recipient info using OpenAI API or compatible endpoints."""

import json
import logging
from typing import Optional
from openai import AsyncOpenAI

from src.config import Settings
from src.ai.schemas import ParsedRecipientInfo

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are an expert AI assistant specializing in parsing Ukrainian postal recipient information from unstructured text.
Your task is to extract recipient details for Nova Poshta express waybill creation.

Extract the following fields in JSON format:
- last_name: Recipient's last name (Прізвище)
- first_name: Recipient's first name (Ім'я)
- middle_name: Recipient's patronymic/middle name (По-батькові), if present
- phone: Phone number normalized to 10-12 digits starting with 380 or 0 (e.g., 380971234567 or 0971234567)
- city_name: City/settlement name without prefix (e.g., "Київ", "Одеса", "Дніпро")
- settlement_type: Settlement type if specified ("місто", "село", "смт")
- warehouse_number: Integer branch or postomat number (e.g., 5, 12, 114)
- is_postomat: Boolean (true if text mentions "поштомат" or "поштомат №...", false for standard branch "відділення")
- street_name: Street name if address delivery is requested
- building_number: Building number if address delivery is requested
- flat_number: Apartment number if address delivery is requested
- cargo_description: Specific item description if mentioned
- declared_value: Declared value number if mentioned

Return ONLY valid JSON matching this schema. Do not include markdown code blocks or additional text."""


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

    async def parse_text(self, text: str) -> ParsedRecipientInfo:
        """Parse unstructured text and return structured ParsedRecipientInfo."""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Parse the following recipient info:\n\n{text}"},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            if not content:
                logger.warning("Empty response from AI model")
                return ParsedRecipientInfo()

            data = json.loads(content)
            return ParsedRecipientInfo(**data)
        except Exception as e:
            logger.error(f"Error parsing recipient text with AI: {e}")
            # Fallback attempt if response_format wasn't respected or JSON parsing failed
            try:
                # Retry without json_object constraint if unsupported
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": text},
                    ],
                    temperature=0.1,
                )
                raw_text = response.choices[0].message.content or ""
                # Strip markdown blocks if any
                clean_text = raw_text.replace("```json", "").replace("```", "").strip()
                data = json.loads(clean_text)
                return ParsedRecipientInfo(**data)
            except Exception as fallback_err:
                logger.error(f"Fallback AI parsing failed: {fallback_err}")
                return ParsedRecipientInfo()
