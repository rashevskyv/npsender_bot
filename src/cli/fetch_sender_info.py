"""CLI script to fetch Sender credentials from Nova Poshta API and generate/update .env configuration."""

import asyncio
import os
import sys
import httpx
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("NOVA_POSHTA_API_KEY")
API_URL = "https://api.novaposhta.ua/v2.0/json/"


async def fetch_counterparties():
    if not API_KEY or API_KEY == "your_nova_poshta_api_key_here":
        print("[ERROR] Please set NOVA_POSHTA_API_KEY in your .env file first!")
        sys.exit(1)

    print(f"Connecting to Nova Poshta API with key: {API_KEY[:6]}...")
    async with httpx.AsyncClient() as client:
        # Fetch Sender Counterparties
        payload = {
            "apiKey": API_KEY,
            "modelName": "Counterparty",
            "calledMethod": "getCounterparties",
            "methodProperties": {"CounterpartyProperty": "Sender"},
        }
        res = await client.post(API_URL, json=payload)
        data = res.json()
        if not data.get("success"):
            print(f"[ERROR] Failed to fetch counterparties: {data.get('errors')}")
            return

        cps = data.get("data", [])
        if not cps:
            print("[WARN] No Sender counterparties found for this API key.")
            return

        sender_cp = cps[0]
        sender_cp_ref = sender_cp.get("Ref")
        print(f"[SUCCESS] Sender Counterparty: {sender_cp.get('Description')} (Ref: {sender_cp_ref})")

        # Fetch Contact Persons for this Counterparty
        cp_contacts_payload = {
            "apiKey": API_KEY,
            "modelName": "Counterparty",
            "calledMethod": "getCounterpartyContactPersons",
            "methodProperties": {"Ref": sender_cp_ref},
        }
        contacts_res = await client.post(API_URL, json=cp_contacts_payload)
        contacts_data = contacts_res.json().get("data", [])

        contact_ref = ""
        contact_phone = ""
        if contacts_data:
            c = contacts_data[0]
            contact_ref = c.get("Ref", "")
            contact_phone = c.get("Phones", "")
            print(f"[SUCCESS] Sender Contact Person: {c.get('LastName')} {c.get('FirstName')} (Ref: {contact_ref}, Phone: {contact_phone})")

        # Fetch Sender Addresses
        addresses_payload = {
            "apiKey": API_KEY,
            "modelName": "Counterparty",
            "calledMethod": "getCounterpartyAddressesAddresses",
            "methodProperties": {"Ref": sender_cp_ref, "CounterpartyProperty": "Sender"},
        }
        addr_res = await client.post(API_URL, json=addresses_payload)
        addr_data = addr_res.json().get("data", [])

        city_ref = ""
        address_ref = ""
        if addr_data:
            a = addr_data[0]
            address_ref = a.get("Ref", "")
            city_ref = a.get("CityRef", "")
            print(f"[SUCCESS] Sender Address: {a.get('Description')} (AddressRef: {address_ref}, CityRef: {city_ref})")

        print("\n" + "=" * 50)
        print("RECOMMENDED .env CONFIGURATION:")
        print("=" * 50)
        print(f"SENDER_COUNTERPARTY_REF={sender_cp_ref}")
        print(f"SENDER_CONTACT_REF={contact_ref}")
        print(f"SENDER_CITY_REF={city_ref}")
        print(f"SENDER_ADDRESS_REF={address_ref}")
        print(f"SENDER_PHONE={contact_phone}")
        print("=" * 50 + "\n")


if __name__ == "__main__":
    asyncio.run(fetch_counterparties())
