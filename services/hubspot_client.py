import httpx
from settings import Settings

BASE = "https://api.hubapi.com"

async def upsert_contact(email: str, firstname: str | None = None, lastname: str | None = None, phone: str | None = None):
    settings = Settings()
    if not settings.HUBSPOT_PRIVATE_APP_TOKEN:
        return None

    headers = {
        "Authorization": f"Bearer {settings.HUBSPOT_PRIVATE_APP_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {
        "properties": {
            "email": email,
        }
    }
    if firstname: data["properties"]["firstname"] = firstname
    if lastname: data["properties"]["lastname"] = lastname
    if phone: data["properties"]["phone"] = phone

    async with httpx.AsyncClient(timeout=30.0) as client:
        # try to create or update
        r = await client.post(f"{BASE}/crm/v3/objects/contacts", headers=headers, json=data)
        if r.status_code == 201:
            return r.json().get("id")
        # if exists, search then update
        q = {"filterGroups":[{"filters":[{"propertyName":"email","operator":"EQ","value":email}]}]}
        r = await client.post(f"{BASE}/crm/v3/objects/contacts/search", headers=headers, json=q)
        results = r.json().get("results", [])
        if results:
            return results[0].get("id")
        return None

async def add_note_to_contact(contact_id: str, note_text: str):
    settings = Settings()
    if not settings.HUBSPOT_PRIVATE_APP_TOKEN or not contact_id:
        return False

    headers = {
        "Authorization": f"Bearer {settings.HUBSPOT_PRIVATE_APP_TOKEN}",
        "Content-Type": "application/json",
    }
    note_payload = {
        "properties": {
            "hs_note_body": note_text
        },
        "associations": [
            {"to": {"id": contact_id}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}]} # 202: note-to-contact
        ]
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{BASE}/crm/v3/objects/notes", headers=headers, json=note_payload)
        return r.status_code in (200, 201)
