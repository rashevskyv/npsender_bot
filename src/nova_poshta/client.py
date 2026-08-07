"""Async client for interacting with Nova Poshta API 2.0."""

import asyncio
import datetime
import logging
from typing import Optional, List, Dict, Any
import httpx

from src.config import Settings
from src.nova_poshta.models import (
    CityInfo,
    WarehouseInfo,
    CounterpartyRecipientResult,
    WaybillCreateResult,
    WaybillItemInfo,
    ScanSheetInfo,
)

logger = logging.getLogger(__name__)


class NovaPoshtaClient:
    """Nova Poshta API 2.0 client."""

    def __init__(self, settings: Settings):
        self.api_key = settings.nova_poshta_api_key
        self.api_url = settings.nova_poshta_api_url
        self.settings = settings

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
            "ServiceType": self.settings.default_service_type,
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
            called_method="save",
            method_properties=method_props,
        )
        data = res.get("data", [])
        if not data:
            raise RuntimeError("InternetDocument/save returned empty data array")

        doc_info = data[0]
        return WaybillCreateResult(
            int_doc_number=doc_info.get("IntDocNumber", ""),
            ref=doc_info.get("Ref", ""),
            cost=float(doc_info.get("CostOnSite", doc_info.get("Cost", 0))),
            estimated_delivery_date=doc_info.get("EstimatedDeliveryDate"),
        )

    async def get_outgoing_waybills(
        self, days_back: int = 30, limit: int = 10
    ) -> List[WaybillItemInfo]:
        """Fetch list of outgoing shipments for the past N days."""
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
                "Limit": str(limit),
                "GetFullList": "1",
            },
        )
        items = []
        for doc in res.get("data", []):
            cost_val = doc.get("CostOnSite") or doc.get("Cost") or "0"
            items.append(
                WaybillItemInfo(
                    int_doc_number=str(doc.get("IntDocNumber", "")),
                    state_name=str(doc.get("StateName", "Unspecified")),
                    recipient_name=str(doc.get("RecipientContactPerson", "N/A")),
                    recipient_phone=doc.get("RecipientsPhone"),
                    city_recipient=str(doc.get("CityRecipientDescription", "N/A")),
                    address_recipient=str(doc.get("RecipientAddressDescription", "N/A")),
                    cost=float(cost_val),
                    description=str(doc.get("Description", "Посилка")),
                    estimated_delivery_date=doc.get("EstimatedDeliveryDate"),
                    date_created=doc.get("DateTime"),
                )
            )
        return items

    async def fetch_sender_profile(self, api_key: str) -> Dict[str, str]:
        """Fetch sender profile credentials for a custom API key."""
        payload = {
            "apiKey": api_key,
            "modelName": "Counterparty",
            "calledMethod": "getCounterparties",
            "methodProperties": {"CounterpartyProperty": "Sender"},
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(self.api_url, json=payload)
            response.raise_for_status()
            data = response.json()
            if not data.get("success", False):
                err = ", ".join(data.get("errors", []))
                raise RuntimeError(f"Invalid Nova Poshta API Key: {err}")

            cps = data.get("data", [])
            if not cps:
                raise RuntimeError("No Sender counterparties found for this API key.")

            cp = cps[0]
            cp_ref = cp.get("Ref", "")
            cp_desc = cp.get("Description", "Sender")

            # Fetch Contact Person
            c_res = await client.post(
                self.api_url,
                json={
                    "apiKey": api_key,
                    "modelName": "Counterparty",
                    "calledMethod": "getCounterpartyContactPersons",
                    "methodProperties": {"Ref": cp_ref},
                },
            )
            contacts = c_res.json().get("data", [])
            contact_ref = contacts[0].get("Ref", "") if contacts else ""
            phone = contacts[0].get("Phones", "") if contacts else ""

            # Fetch Address
            a_res = await client.post(
                self.api_url,
                json={
                    "apiKey": api_key,
                    "modelName": "Counterparty",
                    "calledMethod": "getCounterpartyAddressesAddresses",
                    "methodProperties": {"Ref": cp_ref, "CounterpartyProperty": "Sender"},
                },
            )
            addrs = a_res.json().get("data", [])
            address_ref = addrs[0].get("Ref", "") if addrs else ""
            city_ref = addrs[0].get("CityRef", "") if addrs else ""

            return {
                "sender_counterparty_ref": cp_ref,
                "sender_contact_ref": contact_ref,
                "sender_city_ref": city_ref,
                "sender_address_ref": address_ref,
                "sender_phone": phone,
                "sender_name": cp_desc,
            }

    async def delete_waybill(self, document_ref: str) -> bool:
        """Delete an Express Waybill / Draft by document Ref GUID."""
        res = await self._post(
            model_name="InternetDocument",
            called_method="delete",
            method_properties={"DocumentRefs": document_ref},
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
            "ServiceType": self.settings.default_service_type,
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
        """Create a Nova Poshta ScanSheet (Register) from a list of waybill Ref GUIDs."""
        res = await self._post(
            model_name="ScanSheet",
            called_method="save",
            method_properties={"DocumentRefs": document_refs},
        )
        data = res.get("data", [])
        if not data:
            raise RuntimeError("ScanSheet/save returned empty data array")

        info = data[0]
        return ScanSheetInfo(
            Ref=info.get("Ref", ""),
            Number=info.get("Number", ""),
            DateTime=info.get("DateTime", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            CountOfDocuments=int(info.get("CountOfDocuments", len(document_refs))),
        )

    async def get_scan_sheets(self, days_back: int = 30) -> List[ScanSheetInfo]:
        """Fetch list of user's active registers (ScanSheets)."""
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

            sheets.append(
                ScanSheetInfo(
                    Ref=str(item.get("Ref", "")),
                    Number=str(item.get("Number", "")),
                    DateTime=str(item.get("DateTime", "")),
                    CountOfDocuments=cnt,
                )
            )
        return sheets

    async def delete_scan_sheet(self, scan_sheet_ref: str) -> bool:
        """Delete / unbind a ScanSheet register by Ref GUID."""
        res = await self._post(
            model_name="ScanSheet",
            called_method="deleteScanSheet",
            method_properties={"ScanSheetRef": scan_sheet_ref},
        )
        return res.get("success", False)
