# services/hubspot_client.py
from __future__ import annotations

import os
from typing import Optional, Dict, Any

import httpx

HUBSPOT_BASE = "https://api.hubapi.com"
TOKEN = os.getenv("HUBSPOT_PRIVATE_APP_TOKEN", "")

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}

def _contact_props(email: str,
                   firstname: Optional[str],
                   lastname: Optional[str],
                   phone: Optional[str],
                   extra_properties: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    props = {"email": email}
    if firstname: props["firstname"] = firstname
    if lastname:  props["lastname"] = lastname
    if phone:     props["phone"] = phone
    if extra_properties:
        for k, v in extra_properties.items():
            if v is not None:
                props[k] = v
    return {"properties": props}

async def upsert_contact(email: str,
                         firstname: Optional[str] = None,
                         lastname: Optional[str] = None,
                         phone: Optional[str] = None,
                         extra_properties: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """
    Upsert ohne search:
    1) PATCH /crm/v3/objects/contacts/{email}?idProperty=email  (nur write nötig)
    2) Wenn 404 -> POST /crm/v3/objects/contacts (create)
    Rückgabe: contactId oder None
    """
    if not TOKEN:
        raise RuntimeError("HUBSPOT_PRIVATE_APP_TOKEN not set")

    payload = _contact_props(email, firstname, lastname, phone, extra_properties)

    async with httpx.AsyncClient(timeout=20) as client:
        # 1) Try PATCH by email (upsert if exists)
        patch_url = f"{HUBSPOT_BASE}/crm/v3/objects/contacts/{email}"
        try:
            r = await client.patch(patch_url, params={"idProperty": "email"}, headers=HEADERS, json=payload)
            if r.status_code == 404:
                # 2) Create if not exists
                create_url = f"{HUBSPOT_BASE}/crm/v3/objects/contacts"
                r2 = await client.post(create_url, headers=HEADERS, json=payload)
                r2.raise_for_status()
                return r2.json().get("id")
            r.raise_for_status()
            return r.json().get("id")
        except httpx.HTTPStatusError as e:
            # Wenn 409/400 o.ä., versuche Create
            if e.response is not None and e.response.status_code in (400, 409, 422):
                create_url = f"{HUBSPOT_BASE}/crm/v3/objects/contacts"
                r3 = await client.post(create_url, headers=HEADERS, json=payload)
                r3.raise_for_status()
                return r3.json().get("id")
            raise

async def add_note_to_contact(contact_id: str, note_text: str) -> None:
    """
    Legt eine Note an und verknüpft sie mit dem Contact.
    Erfordert: crm.objects.notes.write  (und typischerweise contacts.write für Association)
    """
    if not TOKEN:
        raise RuntimeError("HUBSPOT_PRIVATE_APP_TOKEN not set")

    note_payload = {
        "properties": {
            "hs_note_body": note_text
        },
        "associations": [
            {
                "to": {"id": contact_id},
                "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}]  # Note->Contact
            }
        ]
    }

    async with httpx.AsyncClient(timeout=20) as client:
        url = f"{HUBSPOT_BASE}/crm/v3/objects/notes"
        r = await client.post(url, headers=HEADERS, json=note_payload)
        r.raise_for_status()
