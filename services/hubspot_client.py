# services/hubspot_client.py
import os
import httpx

HUBSPOT_TOKEN = os.getenv("HUBSPOT_PRIVATE_APP_TOKEN", "")

API_BASE = "https://api.hubapi.com"

HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_TOKEN}",
    "Content-Type": "application/json",
}


async def _search_contact_by_email(email: str) -> str | None:
    """Returns contactId or None."""
    if not email:
        return None
    url = f"{API_BASE}/crm/v3/objects/contacts/search"
    payload = {
        "filterGroups": [
            {
                "filters": [
                    {"propertyName": "email", "operator": "EQ", "value": email}
                ]
            }
        ],
        "properties": ["email"],
        "limit": 1,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, headers=HEADERS, json=payload)
        r.raise_for_status()
        data = r.json()
        results = data.get("results") or []
        if results:
            return results[0].get("id")
    return None


async def upsert_contact(
    email: str,
    firstname: str | None = None,
    lastname: str | None = None,
    phone: str | None = None,
    extra_properties: dict | None = None,
) -> str | None:
    """
    Create or update a contact. Returns contactId.
    Only uses standard properties to avoid schema errors.
    """
    if not HUBSPOT_TOKEN:
        raise RuntimeError("HUBSPOT_PRIVATE_APP_TOKEN missing")

    contact_id = await _search_contact_by_email(email)

    # Standard properties (safe in every HubSpot portal)
    props = {"email": email, "lifecyclestage": "lead"}
    if firstname:
        props["firstname"] = firstname
    if lastname:
        props["lastname"] = lastname
    if phone:
        props["phone"] = phone

    # Optional address block (only if provided)
    if extra_properties:
        street = (extra_properties.get("address") or "").strip()
        city = (extra_properties.get("city") or "").strip()
        zipc = (extra_properties.get("zip") or "").strip()
        if street:
            props["address"] = street
        if city:
            props["city"] = city
        if zipc:
            props["zip"] = zipc
        # optional jobtitle if available
        job = (extra_properties.get("jobtitle") or "").strip()
        if job:
            props["jobtitle"] = job

    async with httpx.AsyncClient(timeout=20) as client:
        if contact_id:
            url = f"{API_BASE}/crm/v3/objects/contacts/{contact_id}"
            r = await client.patch(url, headers=HEADERS, json={"properties": props})
            r.raise_for_status()
            return contact_id
        else:
            url = f"{API_BASE}/crm/v3/objects/contacts"
            r = await client.post(url, headers=HEADERS, json={"properties": props})
            r.raise_for_status()
            return r.json().get("id")


async def add_note_to_contact(contact_id: str, note_body: str) -> str | None:
    """
    Creates a note and associates it with a contact (CRM v3).
    """
    if not HUBSPOT_TOKEN:
        raise RuntimeError("HUBSPOT_PRIVATE_APP_TOKEN missing")
    if not contact_id:
        return None

    url = f"{API_BASE}/crm/v3/objects/notes"
    payload = {
        "properties": {"hs_note_body": note_body[:99000]},  # safety cut
        "associations": [
            {
                "to": {"id": contact_id},
                # HubSpot-defined association type for note→contact
                "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}],
            }
        ],
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, headers=HEADERS, json=payload)
        r.raise_for_status()
        data = r.json()
        return data.get("id")
