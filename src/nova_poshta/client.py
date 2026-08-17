"""Async client for interacting with Nova Poshta API 2.0."""

import asyncio
import datetime
import time
import logging
import re
from typing import Optional, List, Dict, Any, Tuple
import httpx

from src.config import Settings
from src.nova_poshta.models import (
    CityInfo,
    WarehouseInfo,
    CounterpartyRecipientResult,
    WaybillCreateResult,
    WaybillItemInfo,
    ScanSheetInfo,
    StreetInfo,
    AddressSaveResult,
)

logger = logging.getLogger(__name__)


def _clean_phone(phone_str: Optional[str]) -> str:
    """Extract digits from phone string and return last 9 digits."""
    if not phone_str:
        return ""
    digits = "".join(filter(str.isdigit, str(phone_str)))
    return digits[-9:] if len(digits) >= 9 else digits


def _name_matches(user_name: Optional[str], target_name: Optional[str]) -> bool:
    """Check if any word in user_name matches target_name."""
    if not user_name or not target_name:
        return False
    u_words = [w.lower().strip() for w in user_name.split() if len(w.strip()) > 2]
    t_lower = target_name.lower()
    return any(w in t_lower for w in u_words)


class NovaPoshtaClient:
    """Nova Poshta API 2.0 client."""

    _waybills_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}

    def __init__(self, settings: Settings):
        self.api_key = settings.nova_poshta_api_key
        self.api_url = settings.nova_poshta_api_url
        self.settings = settings

    def invalidate_waybills_cache(self):
        """Clear in-memory waybills cache for this client's API key."""
        cache_key = self.api_key or "default"
        NovaPoshtaClient._waybills_cache.pop(cache_key, None)

    async def _fetch_raw_waybills_with_cache(
        self, days_back: int = 30, ttl_seconds: int = 300
    ) -> List[Dict[str, Any]]:
        """Fetch raw document list with 5-minute (300s) in-memory cache to prevent redundant API calls."""
        cache_key = self.api_key or "default"
        now = time.time()

        if cache_key in NovaPoshtaClient._waybills_cache:
            ts, cached_docs = NovaPoshtaClient._waybills_cache[cache_key]
            if now - ts < ttl_seconds:
                logger.debug(f"Returning {len(cached_docs)} cached waybill docs for API key (age: {now - ts:.1f}s)")
                return cached_docs

        today = datetime.date.today()
        from_date = (today - datetime.timedelta(days=days_back)).strftime("%d.%m.%Y")
        to_date = today.strftime("%d.%m.%Y")

        res = await self._post(
            model_name="InternetDocument",
            called_method="getDocumentList",
            method_properties={
                "DateTimeFrom": from_date,
                "DateTimeTo": to_date,
                "Page": "1",
                "Limit": "50",
                "GetFullList": "1",
            },
        )
        docs = res.get("data", [])
        NovaPoshtaClient._waybills_cache[cache_key] = (now, docs)
        return docs

    async def _post(
        self,
        model_name: str,
        called_method: str,
        method_properties: Dict[str, Any],
        max_retries: int = 2,
    ) -> Dict[str, Any]:
        """Make raw POST request to Nova Poshta API with automatic retry on rate limits."""
        payload = {
            "apiKey": self.api_key,
            "modelName": model_name,
            "calledMethod": called_method,
            "methodProperties": method_properties,
        }
        for attempt in range(max_retries + 1):
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(self.api_url, json=payload)
                response.raise_for_status()
                data = response.json()
                if not data.get("success", False):
                    errors = ", ".join(data.get("errors", []))
                    warnings = ", ".join(data.get("warnings", []))
                    err_msg = f"Nova Poshta API Error [{model_name}/{called_method}]: {errors or warnings}"

                    if ("many requests" in err_msg.lower() or "too many" in err_msg.lower()) and attempt < max_retries:
                        logger.warning(
                            f"Rate limited by Nova Poshta API ({err_msg}). Retrying in 0.6s (attempt {attempt + 1}/{max_retries})..."
                        )
                        await asyncio.sleep(0.6 * (attempt + 1))
                        continue

                    logger.error(err_msg)
                    raise RuntimeError(err_msg)
                return data

    async def fetch_sender_profile(self, api_key_override: Optional[str] = None) -> Dict[str, Any]:
        """Fetch Sender Counterparty info, Contact Person info, phone, and name from Nova Poshta API."""
        orig_key = self.api_key
        if api_key_override:
            self.api_key = api_key_override

        try:
            res = await self._post(
                model_name="Counterparty",
                called_method="getCounterparties",
                method_properties={"CounterpartyProperty": "Sender", "Page": "1"},
            )
            cps = res.get("data", [])
            if not cps:
                raise RuntimeError("Не знайдено контрагента відправника для цього API-ключа Нової Пошти.")

            cp_item = cps[0]
            cp_ref = cp_item.get("Ref", "")
            sender_name = cp_item.get("Description", "")

            # Get contact persons for this counterparty
            res_cp = await self._post(
                model_name="Counterparty",
                called_method="getCounterpartyContactPersons",
                method_properties={"Ref": cp_ref, "Page": "1"},
            )
            contacts = res_cp.get("data", [])
            contact_ref = ""
            phone = ""
            if contacts:
                contact_item = contacts[0]
                contact_ref = contact_item.get("Ref", "")
                phone = contact_item.get("Phones", "")
                first = contact_item.get("FirstName", "")
                last = contact_item.get("LastName", "")
                middle = contact_item.get("MiddleName", "")
                contact_full_name = f"{last} {first} {middle}".strip()
                if not contact_full_name:
                    contact_full_name = str(contact_item.get("Description", "")).strip()

                if not sender_name or "приватна особа" in sender_name.lower() or contact_full_name:
                    if contact_full_name:
                        sender_name = contact_full_name

            # Get default sender addresses if available
            sender_city_ref = ""
            sender_address_ref = ""
            try:
                res_addr = await self._post(
                    model_name="Counterparty",
                    called_method="getCounterpartyAddresses",
                    method_properties={"Ref": cp_ref, "CounterpartyProperty": "Sender"},
                )
                addrs = res_addr.get("data", [])
                if addrs:
                    sender_city_ref = addrs[0].get("CityRef", "")
                    sender_address_ref = addrs[0].get("Ref", "")
            except Exception:
                pass

            return {
                "sender_counterparty_ref": cp_ref,
                "sender_contact_ref": contact_ref,
                "sender_name": sender_name,
                "sender_phone": phone,
                "sender_city_ref": sender_city_ref,
                "sender_address_ref": sender_address_ref,
            }
        finally:
            self.api_key = orig_key

    async def search_city(self, city_name: str) -> List[CityInfo]:
        """Search for a city by name."""
        res = await self._post(
            model_name="Address",
            called_method="getCities",
            method_properties={"FindByString": city_name, "Limit": "10"},
        )
        cities = []
        for item in res.get("data", []):
            cities.append(
                CityInfo(
                    Ref=item.get("Ref", ""),
                    Description=item.get("Description", ""),
                    AreaDescription=item.get("AreaDescription"),
                    RegionsDescription=item.get("RegionsDescription"),
                )
            )
        return cities

    async def get_warehouse(
        self, city_ref: str, warehouse_number: int, is_postomat: bool = False
    ) -> Optional[WarehouseInfo]:
        """Find specific warehouse or postomat by number in given city."""
        res = await self._post(
            model_name="Address",
            called_method="getWarehouses",
            method_properties={
                "CityRef": city_ref,
                "FindByString": str(warehouse_number),
                "WarehouseId": str(warehouse_number),
                "Language": "UA",
                "Limit": "100",
            },
        )
        warehouses = res.get("data", [])
        target_num_str = str(warehouse_number)

        # Filter by number
        matched = []
        for item in warehouses:
            num = str(item.get("Number", "")).strip()
            if num == target_num_str:
                matched.append(item)

        if not matched:
            return None

        # If user explicitly wants a postomat or branch, try to filter by TypeOfWarehouse
        # Postomat type refs usually contain 'postomat' or TypeOfWarehouse 'f9316480-5f2d-425d-bc2c-ac7cd36078a6' / '841339c7-591a-42e2-8233-7a0a00f0ed6f'
        selected = matched[0]
        if is_postomat:
            for item in matched:
                desc = item.get("Description", "").lower()
                if "поштомат" in desc or "postomat" in desc:
                    selected = item
                    break

        return WarehouseInfo(
            Ref=selected.get("Ref", ""),
            Description=selected.get("Description", ""),
            Number=str(selected.get("Number", "")),
            TypeOfWarehouse=selected.get("TypeOfWarehouse", ""),
            CityRef=selected.get("CityRef", city_ref),
            CityDescription=selected.get("CityDescription"),
        )

    async def search_street(
        self, city_ref: str, street_name: str
    ) -> List[StreetInfo]:
        """Search for streets by name within a city with intelligent variations and ranking."""
        raw_name = street_name.strip()
        if not raw_name or not city_ref:
            return []

        # Remove street type prefixes like вул., пров., etc.
        cleaned = raw_name
        street_prefixes = [
            "вул.", "вулиця", "пров.", "провулок", "просп.", "проспект", "пр-т",
            "бульв.", "бульвар", "б-р", "наб.", "набережна", "тупик", "узвіз",
            "площа", "майдан", "шосе", "алея", "проїзд", "дорога"
        ]
        for p in street_prefixes:
            pattern = rf"^{re.escape(p)}\s*|\s*{re.escape(p)}$"
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()

        words = [w for w in re.findall(r"[\w']+", cleaned) if len(w) >= 2]

        queries = [cleaned]
        if len(words) > 1:
            queries.append(" ".join(reversed(words)))
            for w in reversed(words):
                if len(w) >= 3 and w not in queries:
                    queries.append(w)

        seen_refs = set()
        all_streets: List[StreetInfo] = []

        for q in queries:
            try:
                res = await self._post(
                    model_name="Address",
                    called_method="getStreet",
                    method_properties={
                        "CityRef": city_ref,
                        "FindByString": q,
                        "Page": "1",
                    },
                )
                for item in res.get("data", []):
                    ref_str = str(item.get("Ref", ""))
                    if ref_str and ref_str not in seen_refs:
                        seen_refs.add(ref_str)
                        all_streets.append(
                            StreetInfo(
                                Ref=ref_str,
                                Description=str(item.get("Description", "")),
                                StreetsType=str(item.get("StreetsType", "вул.")),
                                CityRef=str(item.get("CityRef", city_ref)),
                            )
                        )
                if all_streets:
                    break
            except Exception as e:
                logger.warning(f"Error querying getStreet with '{q}': {e}")

        # Rank all_streets by similarity to raw input
        def score_street(s: StreetInfo) -> float:
            s_words = set(re.findall(r"[\w']+", s.description.lower()))
            q_words = set(w.lower() for w in words)
            overlap = len(s_words & q_words)
            # Exact match bonus
            if s.description.lower() == cleaned.lower():
                overlap += 2.0
            # Street type bonus if user specified "вул" / "пров" etc.
            if "вул" in raw_name.lower() and "вул" in s.streets_type.lower():
                overlap += 0.5
            elif "пров" in raw_name.lower() and "пров" in s.streets_type.lower():
                overlap += 0.5
            elif "просп" in raw_name.lower() and "просп" in s.streets_type.lower():
                overlap += 0.5
            return float(overlap)

        all_streets.sort(key=score_street, reverse=True)
        return all_streets

    async def create_counterparty_address(
        self,
        counterparty_ref: str,
        street_ref: str,
        building_number: str,
        flat: str = "",
        note: str = "",
    ) -> AddressSaveResult:
        """Create and save a recipient address for a counterparty in Nova Poshta."""
        res = await self._post(
            model_name="Address",
            called_method="save",
            method_properties={
                "CounterpartyRef": counterparty_ref,
                "StreetRef": street_ref,
                "BuildingNumber": str(building_number).strip(),
                "Flat": str(flat).strip() if flat else "",
                "Note": note,
            },
        )
        data = res.get("data", [])
        if not data:
            raise RuntimeError("Address/save returned empty data array")

        addr_info = data[0]
        return AddressSaveResult(
            Ref=str(addr_info.get("Ref", "")),
            Description=str(addr_info.get("Description", "")),
        )

    async def create_recipient_counterparty(
        self, first_name: str, last_name: str, phone: str, middle_name: str = ""
    ) -> CounterpartyRecipientResult:
        """Create or get Recipient Counterparty and Contact Person."""
        # Sanitize phone (must start with 380)
        phone_clean = "".join(filter(str.isdigit, phone))
        if len(phone_clean) == 10 and phone_clean.startswith("0"):
            phone_clean = f"38{phone_clean}"

        res = await self._post(
            model_name="Counterparty",
            called_method="save",
            method_properties={
                "FirstName": first_name.strip(),
                "MiddleName": middle_name.strip(),
                "LastName": last_name.strip(),
                "Phone": phone_clean,
                "Email": "",
                "CounterpartyType": "PrivatePerson",
                "CounterpartyProperty": "Recipient",
            },
        )
        data = res.get("data", [])
        if not data:
            raise RuntimeError("Counterparty/save returned empty data array")

        cp_item = data[0]
        cp_ref = cp_item.get("Ref", "")
        contact_person_data = cp_item.get("ContactPerson", {}).get("data", [])
        contact_ref = contact_person_data[0].get("Ref", "") if contact_person_data else ""

        return CounterpartyRecipientResult(
            counterparty_ref=cp_ref,
            contact_person_ref=contact_ref,
        )

    async def create_waybill(
        self,
        recipient_cp_ref: str,
        recipient_contact_ref: str,
        recipient_phone: str,
        recipient_city_ref: str,
        recipient_warehouse_ref: str,
        payer_type: str = "Recipient",
        description: str = "Посилка",
        seats_amount: int = 1,
        weight: float = 1.0,
        declared_value: float = 300.0,
        cod_amount: Optional[float] = None,
        cod_payer_type: str = "Recipient",
        service_type: Optional[str] = None,
    ) -> WaybillCreateResult:
        """Create Nova Poshta Express Waybill (ТТН)."""
        today_str = datetime.date.today().strftime("%d.%m.%Y")

        phone_clean = "".join(filter(str.isdigit, recipient_phone))
        if len(phone_clean) == 10 and phone_clean.startswith("0"):
            phone_clean = f"38{phone_clean}"

        options_seat = [
            {
                "volumetricVolume": "0.004",
                "volumetricWidth": "20",
                "volumetricLength": "20",
                "volumetricHeight": "10",
                "weight": str(weight),
            }
            for _ in range(seats_amount)
        ]

        eff_service_type = service_type or self.settings.default_service_type or "WarehouseWarehouse"

        method_props = {
            "Sender": self.settings.sender_counterparty_ref,
            "ContactSender": self.settings.sender_contact_ref,
            "SendersPhone": self.settings.sender_phone,
            "CitySender": self.settings.sender_city_ref,
            "SenderAddress": self.settings.sender_address_ref,
            "Recipient": recipient_cp_ref,
            "ContactRecipient": recipient_contact_ref,
            "RecipientsPhone": phone_clean,
            "CityRecipient": recipient_city_ref,
            "RecipientAddress": recipient_warehouse_ref,
            "PayerType": payer_type,
            "PaymentMethod": self.settings.default_payment_method,
            "ServiceType": eff_service_type,
            "SeatsAmount": str(seats_amount),
            "Weight": str(weight),
            "Cost": str(declared_value),
            "CargoType": self.settings.default_cargo_type,
            "Description": description,
            "DateTime": today_str,
            "OptionsSeat": options_seat,
        }

        if cod_amount and cod_amount > 0:
            declared_value = max(declared_value, cod_amount)
            method_props["Cost"] = str(declared_value)
            method_props["BackwardDeliveryData"] = [
                {
                    "PayerType": cod_payer_type,
                    "CargoType": "Money",
                    "RedeliveryString": str(int(cod_amount)),
                }
            ]

        res = await self._post(
            model_name="InternetDocument",
            called_method="save",
            method_properties=method_props,
        )
        data = res.get("data", [])
        if not data:
            raise RuntimeError("InternetDocument/save returned empty data array")

        doc_info = data[0]
        self.invalidate_waybills_cache()
        return WaybillCreateResult(
            int_doc_number=doc_info.get("IntDocNumber", ""),
            ref=doc_info.get("Ref", ""),
            cost=float(doc_info.get("CostOnSite", doc_info.get("Cost", 0))),
            estimated_delivery_date=doc_info.get("EstimatedDeliveryDate"),
        )

    async def get_outgoing_waybills(
        self,
        user_phone: Optional[str] = None,
        user_name: Optional[str] = None,
        user_cp_ref: Optional[str] = None,
        days_back: int = 30,
        limit: int = 20,
    ) -> List[WaybillItemInfo]:
        """Fetch list of active outgoing shipments sent by user (not yet received) for the past N days."""
        eff_phone = user_phone or getattr(self.settings, "sender_phone", None)
        eff_name = user_name or getattr(self.settings, "sender_name", None)
        eff_cp_ref = user_cp_ref or getattr(self.settings, "sender_counterparty_ref", None)

        raw_docs = await self._fetch_raw_waybills_with_cache(days_back=days_back)
        items = []
        for doc in raw_docs:
            state_id = str(doc.get("StateId", doc.get("StatusCode", "")))
            state_name = str(doc.get("StateName", doc.get("Status", "Unspecified")))

            # Skip items already received/picked up by recipient
            if state_id in ("9", "10", "11", "106") or any(
                kw in state_name.lower() for kw in ["отримано", "забрано", "видано"]
            ):
                continue

            doc_sender_cp = str(doc.get("Sender", ""))
            doc_sender_phone = doc.get("SendersPhone")
            doc_sender_name = doc.get("SenderContactPerson") or doc.get("SenderDescription")

            is_outgoing = False
            if eff_cp_ref and doc_sender_cp and eff_cp_ref == doc_sender_cp:
                is_outgoing = True
            elif eff_phone and doc_sender_phone and _clean_phone(eff_phone) == _clean_phone(doc_sender_phone):
                is_outgoing = True
            elif not eff_phone and not eff_cp_ref:
                is_outgoing = True

            if not is_outgoing:
                continue

            cost_val = doc.get("CostOnSite") or doc.get("Cost") or "0"
            items.append(
                WaybillItemInfo(
                    int_doc_number=str(doc.get("IntDocNumber", "")),
                    state_name=state_name,
                    state_id=state_id,
                    recipient_name=str(doc.get("RecipientContactPerson", doc.get("RecipientDescription", "N/A"))),
                    recipient_phone=doc.get("RecipientsPhone"),
                    sender_name=str(doc.get("SenderContactPerson", doc.get("SenderDescription", "N/A"))),
                    sender_phone=doc.get("SendersPhone"),
                    city_recipient=str(doc.get("CityRecipientDescription", "N/A")),
                    address_recipient=str(doc.get("RecipientAddressDescription", "N/A")),
                    cost=float(cost_val),
                    description=str(doc.get("Description", "Посилка")),
                    estimated_delivery_date=doc.get("EstimatedDeliveryDate"),
                    date_created=doc.get("DateTime"),
                )
            )
        return items

    async def get_incoming_waybills(
        self,
        user_phone: Optional[str] = None,
        user_name: Optional[str] = None,
        user_cp_ref: Optional[str] = None,
        days_back: int = 30,
        limit: int = 20,
    ) -> List[WaybillItemInfo]:
        """Fetch list of active incoming shipments traveling to user (not yet received) for the past N days."""
        eff_phone = user_phone or getattr(self.settings, "sender_phone", None)
        eff_cp_ref = user_cp_ref or getattr(self.settings, "sender_counterparty_ref", None)

        raw_docs = await self._fetch_raw_waybills_with_cache(days_back=days_back)
        items = []
        for doc in raw_docs:
            state_id = str(doc.get("StateId", doc.get("StatusCode", "")))
            state_name = str(doc.get("StateName", doc.get("Status", "Unspecified")))

            # Skip items already received/picked up
            if state_id in ("9", "10", "11", "106") or any(
                kw in state_name.lower() for kw in ["отримано", "забрано", "видано"]
            ):
                continue

            doc_sender_cp = str(doc.get("Sender", ""))
            doc_sender_phone = doc.get("SendersPhone")

            doc_recip_cp = str(doc.get("Recipient", ""))
            doc_recip_phone = doc.get("RecipientsPhone")

            is_sender = False
            if eff_cp_ref and doc_sender_cp and eff_cp_ref == doc_sender_cp:
                is_sender = True
            elif eff_phone and doc_sender_phone and _clean_phone(eff_phone) == _clean_phone(doc_sender_phone):
                is_sender = True

            is_recipient = False
            if eff_cp_ref and doc_recip_cp and eff_cp_ref == doc_recip_cp:
                is_recipient = True
            elif eff_phone and doc_recip_phone and _clean_phone(eff_phone) == _clean_phone(doc_recip_phone):
                is_recipient = True
            elif not is_sender and not eff_phone and not eff_cp_ref:
                is_recipient = True

            if is_sender or not is_recipient:
                continue

            cost_val = doc.get("CostOnSite") or doc.get("Cost") or "0"
            items.append(
                WaybillItemInfo(
                    int_doc_number=str(doc.get("IntDocNumber", "")),
                    state_name=state_name,
                    state_id=state_id,
                    recipient_name=str(doc.get("RecipientContactPerson", doc.get("RecipientDescription", "N/A"))),
                    recipient_phone=doc.get("RecipientsPhone"),
                    sender_name=str(doc.get("SenderContactPerson", doc.get("SenderDescription", "N/A"))),
                    sender_phone=doc.get("SendersPhone"),
                    city_recipient=str(doc.get("CityRecipientDescription", "N/A")),
                    address_recipient=str(doc.get("RecipientAddressDescription", "N/A")),
                    cost=float(cost_val),
                    description=str(doc.get("Description", "Посилка")),
                    estimated_delivery_date=doc.get("EstimatedDeliveryDate"),
                    date_created=doc.get("DateTime"),
                )
            )
        return items

    async def get_documents_status(
        self, document_numbers: List[str], phone: str = ""
    ) -> Dict[str, Dict[str, Any]]:
        """Fetch tracking status for a list of TTN document numbers.
        
        Returns a dict mapping document_number -> {
            'status_code': str,
            'status_name': str,
            'is_shipped': bool,
            'is_deleted': bool,
            'is_draft': bool,
        }
        """
        if not document_numbers:
            return {}

        docs_payload = [
            {"DocumentNumber": doc_num, "Phone": phone}
            for doc_num in document_numbers
        ]

        try:
            res = await self._post(
                model_name="TrackingDocument",
                called_method="getStatusDocuments",
                method_properties={"Documents": docs_payload},
            )
        except Exception as e:
            logger.error(f"Error fetching document statuses from Nova Poshta: {e}")
            return {}

        results = {}
        shipped_codes = {"4", "41", "5", "6", "7", "8", "9", "10", "11", "12", "14", "104", "105", "106"}
        deleted_codes = {"2", "3"}
        for item in res.get("data", []):
            doc_num = str(item.get("Number", item.get("DocumentNumber", "")))
            status_code = str(item.get("StatusCode", ""))
            status_name = str(item.get("Status", item.get("StateName", "")))

            is_shipped = False
            is_deleted = False

            if status_code in deleted_codes:
                is_deleted = True
            elif status_code in shipped_codes:
                is_shipped = True
            else:
                try:
                    code_int = int(status_code)
                    if code_int in (2, 3):
                        is_deleted = True
                    elif code_int >= 4 and code_int not in (101, 102, 103, 108):
                        is_shipped = True
                except ValueError:
                    pass

            lower_name = status_name.lower()
            if any(w in lower_name for w in ["видалено", "скасовано", "не знайдено", "не знайдена", "не існує", "помилка"]):
                is_deleted = True
            elif any(w in lower_name for w in ["прямує", "прибув", "отримано", "відправлено", "у відділенні", "доставлено", "видано"]):
                is_shipped = True
            elif any(w in lower_name for w in ["очікує посилку", "очікує надходження", "створено", "чернетка"]):
                is_shipped = False

            is_draft = not is_shipped and not is_deleted and (status_code == "1" or any(w in lower_name for w in ["очікує", "створено", "чернетка"]))

            results[doc_num] = {
                "status_code": status_code,
                "status_name": status_name,
                "is_shipped": is_shipped,
                "is_deleted": is_deleted,
                "is_draft": is_draft,
            }

        return results

    async def get_internet_document_list(
        self, days_back: int = 30
    ) -> List[WaybillItemInfo]:
        """Fetch active un-shipped waybill drafts directly from Nova Poshta API (InternetDocument/getDocumentList)."""
        dt_from = (datetime.datetime.now() - datetime.timedelta(days=days_back)).strftime("%d.%m.%Y")
        dt_to = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%d.%m.%Y")

        try:
            res = await self._post(
                model_name="InternetDocument",
                called_method="getDocumentList",
                method_properties={
                    "DateTimeFrom": dt_from,
                    "DateTimeTo": dt_to,
                    "GetFullList": "1",
                },
            )
        except Exception as e:
            logger.error(f"Error fetching document list from Nova Poshta: {e}")
            return []

        data = res.get("data", [])
        if not data:
            return []

        items = []
        for doc in data:
            doc_num = str(doc.get("IntDocNumber", doc.get("Number", "")))
            if not doc_num:
                continue

            # Skip deleted or marked for deletion documents
            deletion_mark = doc.get("DeletionMark")
            if deletion_mark in (True, 1, "1", "true", "True"):
                continue

            state_id = str(doc.get("StateId", doc.get("StatusCode", "")))
            if state_id in ("2", "3"):
                continue

            state_name_lower = str(doc.get("StateName", doc.get("StateDescription", ""))).lower()
            if any(w in state_name_lower for w in ["видалено", "скасовано", "не знайдено"]):
                continue

            delivery_cost = float(doc.get("CostOnSite", 0) or 0)
            declared_val = float(doc.get("Cost", doc.get("DeclaredCost", 0)) or 0)
            payer_type = str(doc.get("PayerType", "Recipient"))

            # Extract COD (Накладений платіж) details from BackwardDeliveryData or direct fields
            cod_val = 0.0
            cod_type = "cash"

            bw_data = doc.get("BackwardDeliveryData") or []
            if isinstance(bw_data, list):
                for bw in bw_data:
                    if isinstance(bw, dict):
                        c_type = bw.get("CargoType", "")
                        if c_type in ("Money", "Цінні папери", "Грошовий переказ", "TrMax", "Afterpayment"):
                            try:
                                cod_val = float(bw.get("RedeliveryString", 0) or 0)
                            except (ValueError, TypeError):
                                pass
                            if bw.get("RedeliveryPaymentCard") or bw.get("PaymentMethod") == "Card":
                                cod_type = "card"

            if not cod_val:
                for k in ["AfterpaymentOnGoodsCost", "RedeliveryString", "BackwardDeliveryMoney", "BackwardDeliveryCost", "RedeliverySum"]:
                    val = doc.get(k)
                    if val:
                        try:
                            cod_val = float(val)
                            if cod_val > 0:
                                break
                        except (ValueError, TypeError):
                            pass

            if doc.get("RedeliveryPaymentCard") or doc.get("PaymentCard") or doc.get("BackwardDeliveryPaymentType") == "Card":
                cod_type = "card"

            # Auto-enforce rule: declared_value must be at least cod_amount and at least 500
            declared_val = max(declared_val, cod_val, 500.0)

            rec_name = (
                doc.get("RecipientContactPerson")
                or doc.get("RecipientDescription")
                or "Отримувач"
            )

            items.append(
                WaybillItemInfo(
                    int_doc_number=doc_num,
                    ref=str(doc.get("Ref", doc_num)),
                    state_name=str(doc.get("StateName", doc.get("StateDescription", "Чернетка (Невідправлена)"))),
                    recipient_name=str(rec_name),
                    recipient_phone=str(doc.get("RecipientsPhone", "")),
                    sender_name=str(doc.get("SenderDescription", "")),
                    sender_phone=str(doc.get("SendersPhone", "")),
                    city_recipient=str(doc.get("CityRecipientDescription", "")),
                    address_recipient=str(doc.get("RecipientAddressDescription", "")),
                    cost=delivery_cost,
                    declared_value=declared_val,
                    cod_amount=cod_val,
                    cod_payment_type=cod_type,
                    payer_type=payer_type,
                    description=str(doc.get("Description", "Посилка")),
                    estimated_delivery_date=doc.get("EstimatedDeliveryDate"),
                    date_created=doc.get("DateTime"),
                )
            )
        return items

    async def delete_waybill(self, document_ref: str) -> bool:
        """Delete an Express Waybill / Draft by document Ref GUID."""
        doc_refs = [document_ref] if isinstance(document_ref, str) else list(document_ref)
        res = await self._post(
            model_name="InternetDocument",
            called_method="delete",
            method_properties={"DocumentRefs": doc_refs},
        )
        return bool(res.get("success", False))

    async def update_waybill(
        self,
        document_ref: str,
        recipient_cp_ref: str,
        recipient_contact_ref: str,
        recipient_phone: str,
        recipient_city_ref: str,
        recipient_warehouse_ref: str,
        payer_type: str = "Recipient",
        description: str = "Посилка",
        seats_amount: int = 1,
        weight: float = 1.0,
        declared_value: float = 500.0,
        service_type: Optional[str] = None,
    ) -> WaybillCreateResult:
        """Update an existing Nova Poshta Express Waybill (ТТН)."""
        today_str = datetime.date.today().strftime("%d.%m.%Y")

        phone_clean = "".join(filter(str.isdigit, recipient_phone))
        if len(phone_clean) == 10 and phone_clean.startswith("0"):
            phone_clean = f"38{phone_clean}"

        options_seat = [
            {
                "volumetricVolume": "0.004",
                "volumetricWidth": "20",
                "volumetricLength": "20",
                "volumetricHeight": "10",
                "weight": str(weight),
            }
            for _ in range(seats_amount)
        ]

        eff_service_type = service_type or self.settings.default_service_type or "WarehouseWarehouse"

        method_props = {
            "Ref": document_ref,
            "Sender": self.settings.sender_counterparty_ref,
            "ContactSender": self.settings.sender_contact_ref,
            "SendersPhone": self.settings.sender_phone,
            "CitySender": self.settings.sender_city_ref,
            "SenderAddress": self.settings.sender_address_ref,
            "Recipient": recipient_cp_ref,
            "ContactRecipient": recipient_contact_ref,
            "RecipientsPhone": phone_clean,
            "CityRecipient": recipient_city_ref,
            "RecipientAddress": recipient_warehouse_ref,
            "PayerType": payer_type,
            "PaymentMethod": self.settings.default_payment_method,
            "ServiceType": eff_service_type,
            "SeatsAmount": str(seats_amount),
            "Weight": str(weight),
            "Cost": str(declared_value),
            "CargoType": self.settings.default_cargo_type,
            "Description": description,
            "DateTime": today_str,
            "OptionsSeat": options_seat,
        }

        res = await self._post(
            model_name="InternetDocument",
            called_method="update",
            method_properties=method_props,
        )
        data = res.get("data", [])
        if not data:
            raise RuntimeError("InternetDocument/update returned empty data array")

        doc_info = data[0]
        return WaybillCreateResult(
            int_doc_number=doc_info.get("IntDocNumber", ""),
            ref=doc_info.get("Ref", document_ref),
            cost=float(doc_info.get("CostOnSite", doc_info.get("Cost", 0))),
            estimated_delivery_date=doc_info.get("EstimatedDeliveryDate"),
        )

    async def create_scan_sheet(self, document_refs: List[str]) -> ScanSheetInfo:
        """Create a Nova Poshta ScanSheet (Register) from a list of waybill Ref GUIDs / numbers."""
        res = await self._post(
            model_name="ScanSheet",
            called_method="insertDocuments",
            method_properties={"DocumentRefs": document_refs},
        )
        data = res.get("data", [])
        if not data:
            raise RuntimeError("ScanSheet/insertDocuments returned empty data array")

        info = data[0]
        raw_cnt = (
            info.get("CountOfDocuments")
            or len(info.get("Success", []))
            or len(document_refs)
        )
        try:
            cnt = int(raw_cnt)
        except (ValueError, TypeError):
            cnt = len(document_refs)

        return ScanSheetInfo(
            Ref=str(info.get("Ref", "")),
            Number=str(info.get("Number", "")),
            DateTime=str(info.get("DateTime", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))),
            CountOfDocuments=cnt,
        )

    async def get_scan_sheets(self, days_back: int = 2) -> List[ScanSheetInfo]:
        """Fetch list of user's active registers (ScanSheets). Defaults to past 2 days."""
        today = datetime.date.today()
        from_date = (today - datetime.timedelta(days=days_back)).strftime("%d.%m.%Y")
        to_date = today.strftime("%d.%m.%Y")

        res = await self._post(
            model_name="ScanSheet",
            called_method="getScanSheetList",
            method_properties={
                "DateTimeFrom": from_date,
                "DateTimeTo": to_date,
            },
        )
        data = res.get("data", [])
        sheets = []
        for item in data:
            raw_cnt = (
                item.get("CountOfDocuments")
                or item.get("CountPoint")
                or item.get("Count")
                or item.get("CountOfTTN")
                or item.get("CountDocuments")
                or 0
            )
            try:
                cnt = int(raw_cnt)
            except (ValueError, TypeError):
                cnt = 0

            printed_val = str(item.get("Printed", item.get("IsPrinted", "0"))).strip()
            is_printed = printed_val in ("1", "true", "True")

            sheets.append(
                ScanSheetInfo(
                    Ref=str(item.get("Ref", "")),
                    Number=str(item.get("Number", "")),
                    DateTime=str(item.get("DateTime", "")),
                    CountOfDocuments=cnt,
                    is_printed=is_printed,
                )
            )
        return sheets

    async def delete_scan_sheet(self, scan_sheet_ref: str) -> bool:
        """Delete / unbind a ScanSheet register by Ref GUID or Number."""
        res = await self._post(
            model_name="ScanSheet",
            called_method="deleteScanSheet",
            method_properties={"ScanSheetRefs": [scan_sheet_ref]},
        )
        return res.get("success", False)

    async def get_payment_cards(self, counterparty_ref: str) -> List[Dict[str, Any]]:
        """Fetch registered payment cards for a counterparty from Nova Poshta API."""
        try:
            res = await self._post(
                model_name="Counterparty",
                called_method="getPaymentCards",
                method_properties={"CounterpartyRef": counterparty_ref},
            )
            return res.get("data", [])
        except Exception as e:
            logger.warning(f"Failed to fetch payment cards for counterparty {counterparty_ref}: {e}")
            return []
