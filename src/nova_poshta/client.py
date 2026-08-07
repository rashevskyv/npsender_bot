"""Async client for interacting with Nova Poshta API 2.0."""

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
)

logger = logging.getLogger(__name__)


class NovaPoshtaClient:
    """Nova Poshta API 2.0 client."""

    def __init__(self, settings: Settings):
        self.api_key = settings.nova_poshta_api_key
        self.api_url = settings.nova_poshta_api_url
        self.settings = settings

    async def _post(
        self, model_name: str, called_method: str, method_properties: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Make raw POST request to Nova Poshta API."""
        payload = {
            "apiKey": self.api_key,
            "modelName": model_name,
            "calledMethod": called_method,
            "methodProperties": method_properties,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(self.api_url, json=payload)
            response.raise_for_status()
            data = response.json()
            if not data.get("success", False):
                errors = ", ".join(data.get("errors", []))
                warnings = ", ".join(data.get("warnings", []))
                err_msg = f"Nova Poshta API Error [{model_name}/{called_method}]: {errors or warnings}"
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
