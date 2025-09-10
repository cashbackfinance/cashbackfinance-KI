# main.py
from __future__ import annotations

import re
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from models import ChatRequest, ChatResponse, ChatMessage, LeadRequest, LeadResponse
from settings import Settings
from middleware import attach_cors
from services.openai_client import chat_completion
from services import hubspot_client


# =========================
# App & Settings
# =========================
app = FastAPI(title="Cashback Finance API", version="1.1.0")
settings = Settings()
attach_cors(app, settings)


# =========================
# Utility: Regex Finder
# =========================
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s\-\/]?)?(?:\(?\d+\)?[\s\-\/]?){5,}")  # großzügig
ZIP_RE = re.compile(r"\b(\d{5})\b")
CITY_RE = re.compile(r"\b\d{5}\s+([A-Za-zÄÖÜäöüß\-\.\s]{2,})")


def _find_email_phone(text: str) -> Dict[str, Optional[str]]:
    email = None
    phone = None

    m = EMAIL_RE.search(text or "")
    if m:
        email = m.group(0).strip()

    # Telefon grob normalisieren (nur Ziffern und +)
    pm = PHONE_RE.search(text or "")
    if pm:
        raw = pm.group(0)
        digits = re.sub(r"[^\d+]", "", raw)
        if len(re.sub(r"\D", "", digits)) >= 7:
            phone = digits

    return {"email": email, "phone": phone}


def _split_name(fullname: str) -> Dict[str, Optional[str]]:
    fullname = (fullname or "").strip()
    if not fullname:
        return {"firstname": None, "lastname": None}
    parts = [p for p in fullname.split() if p.strip()]
    if len(parts) == 1:
        return {"firstname": parts[0], "lastname": None}
    return {"firstname": parts[0], "lastname": " ".join(parts[1:])}


# =========================
# Consent Detection
# =========================
AFFIRM = [
    "ja", "okay", "ok", "mach das", "bitte übermitteln", "du darfst übermitteln",
    "einverstanden", "zustimmung", "gern", "go", "weitergeben", "weiterleiten",
    "übermitteln", "übertrage", "senden", "absenden"
]
DENY = [
    "nein", "nicht übermitteln", "kein", "keine übermittlung", "stopp", "stop", "abbrechen"
]


def detect_consent(messages: List[Dict]) -> bool:
    """
    True, wenn in der Unterhaltung ausdrücklich zugestimmt wurde,
    ohne direkt negiert zu werden.
    """
    text = " ".join([(m.get("content") or "") for m in messages]).lower()
    if any(x in text for x in DENY):
        return False
    return any(x in text for x in AFFIRM)


# =========================
# Lightweight Dossier Builder
# (zieht, was zuverlässig erkennbar ist)
# =========================
def build_customer_dossier(messages: List[Dict]) -> Dict:
    """
    Sehr einfache Extraktion gängiger Felder aus dem Chatverlauf.
    Alles optional/defensiv. Dient primär dazu, die HubSpot-Note
    sinnvoll vorzufüllen und Standardfelder zu befüllen.
    """
    joined = "\n".join([m.get("content") or "" for m in messages])

    # Name – nimm letzte Nennung nach "Name", "Ich heiße", etc. – fallback: kein Name
    name = None
    for pat in [
        r"(?:name|ich hei(?:s|ß)e|mein name ist)\s*[:\-]?\s*([A-Za-zÄÖÜäöüß\-\.\s]{2,})",
    ]:
        m = re.search(pat, joined, re.IGNORECASE)
        if m:
            cand = m.group(1).strip()
            if 2 <= len(cand) <= 80:
                name = cand
                break

    # Adresse – grob: Straße möglich, aber sicher erfassbar sind PLZ/Ort
    zipc = None
    city = None
    m_zip = ZIP_RE.search(joined)
    if m_zip:
        zipc = m_zip.group(1)
        m_city = CITY_RE.search(joined)
        if m_city:
            city = m_city.group(1).strip()

    # Beruf
    job = None
    m_job = re.search(r"(?:beruf|status|job|tätigkeit)\s*[:\-]?\s*([A-Za-zÄÖÜäöüß\-\.\s]{2,})", joined, re.IGNORECASE)
    if m_job:
        job = m_job.group(1).strip()

    # Einkommen (ca. Angabe)
    income = None
    m_inc = re.search(r"(?:einkommen|netto)\s*[:\-]?\s*~?\s*([0-9\.\s]+)\s*€?", joined, re.IGNORECASE)
    if m_inc:
        income = m_inc.group(1).strip()

    # schnelle Ableitungen
    emails_phones = _find_email_phone(joined)
    name_parts = _split_name(name or "")

    return {
        "startformular": {
            "name": name,
            "firstname": name_parts.get("firstname"),
            "lastname": name_parts.get("lastname"),
            "plz": zipc,
            "ort": city,
            "beruf_status": job,
            "einkommen": income,
            "email": emails_phones.get("email"),
            "phone": emails_phones.get("phone"),
        }
    }


def render_note(dossier: Dict, messages: List[Dict]) -> str:
    last_user = ""
    for m in reversed(messages):
        if (m.get("role") or "") == "user":
            last_user = (m.get("content") or "").strip()
            break

    sf = dossier.get("startformular", {}) or {}
    lines = [
        "Kundenakte – Kurzprotokoll (Cashback Finance KI)",
        "",
        f"Name: {sf.get('name') or (sf.get('firstname') or '') + ' ' + (sf.get('lastname') or '')}".strip(),
        f"E-Mail: {sf.get('email') or '-'}",
        f"Telefon: {sf.get('phone') or '-'}",
        f"PLZ/Ort: {(sf.get('plz') or '-')}/{(sf.get('ort') or '-')}",
        f"Beruf/Status: {sf.get('beruf_status') or '-'}",
        f"Einkommen (ca.): {sf.get('einkommen') or '-'}",
        "",
        f"Letzte Nutzerfrage/Intent: {last_user or '-'}",
        "",
        "Hinweis: Datenerfassung via Website-Chat, Übermittlung nach Zustimmung im Chat."
    ]
    return "\n".join(lines)


# =========================
# System Prompt (DU, Mehrwert & Compliance)
# =========================
SYSTEM_PROMPT = (
    "Du bist die KI von Cashback Finance. Ton: freundlich, klar, lösungsorientiert, DU-Ansprache. "
    "Ziele: (1) fachlich korrekt beraten, (2) pragmatische Next Steps, (3) dezent Mehrwert von Cashback Finance: "
    "durch Einsparungen, Microsaving und bis zu 20 % Cashback auf Provisionen entstehen reale Vorteile. "
    "NIE aufdringlich, sondern hilfreich und transparent. "
    "Datenschutz: Speichere im Chat keine sensiblen PII dauerhaft. Frage erst, bevor du Daten an Cashback Finance übermittelst. "
    "Wenn der Nutzer Datenübermittlung wünscht, bestätige kurz und frage nach Name + Kontakt (E-Mail/Telefon), falls noch nicht vorhanden. "
    "Bei Baufinanzierung: nicht erst Nein, dann Ja – weise proaktiv auf Optionen (z. B. Forward-Darlehen) hin und erkläre kurz die Logik. "
    "Antworte in kurzen, geordneten Schritten (1., 2., 3.). "
)


# =========================
# Routes
# =========================
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    Kern-Endpunkt: AI-Antwort + (bei Consent) HubSpot-Übergabe.
    """
    # 1) OpenAI-Aufruf
    messages_payload = [m.model_dump() for m in req.messages]
    system_prompt = SYSTEM_PROMPT + " Bitte antworte jetzt präzise auf die letzte Nutzerfrage."
    try:
        assistant_text = chat_completion(
            messages=messages_payload,
            system_prompt=system_prompt,
            model=settings.MODEL_NAME,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OpenAI error: {e}")

    # 2) Consent & HubSpot-Übergabe (defensiv)
    try:
        # gesamter Text der Unterhaltung
        flat_text = "\n".join([m.get("content") or "" for m in messages_payload])

        consent_chat = detect_consent(messages_payload)
        consent_ui = bool(getattr(req, "lead_opt_in", False))
        consent = consent_ui or consent_chat

        # E-Mail/Telefon aus Request + Text
        ids = _find_email_phone(flat_text)
        email_for_hs = (req.email or ids.get("email") or "").strip()
        phone_for_hs = (ids.get("phone") or "").strip()

        # Dossier bauen (Name, PLZ/Ort, Beruf, Income – optional)
        dossier = build_customer_dossier(messages_payload)
        sf = dossier.get("startformular", {}) or {}

        # Name splitten
        firstname = sf.get("firstname")
        lastname = sf.get("lastname")

        # Falls kein Name im Dossier: versuche minimalen Fallback aus Chat
        if not (firstname or lastname):
            # manchmal kommt "Tester Test," Zeile für Zeile
            first_line = ""
            for m in req.messages:
                if m.role == "user":
                    first_line = (m.content or "").strip().splitlines()[0]
                    break
            split = _split_name(first_line)
            firstname = firstname or split.get("firstname")
            lastname = lastname or split.get("lastname")

        street = sf.get("address") or ""  # wir extrahieren bewusst nur PLZ/Ort sicher
        city = sf.get("ort") or None
        zipc = sf.get("plz") or None
        jobtitle = sf.get("beruf_status") or None

        print(f"[CONSENT] ui={consent_ui} chat={consent_chat} -> {consent} | email={email_for_hs} | phone={phone_for_hs}", flush=True)

        if consent and (email_for_hs or phone_for_hs):
            extra = {"address": street, "city": city or "", "zip": zipc or "", "jobtitle": jobtitle or ""}
            contact_id = await hubspot_client.upsert_contact(
                email_for_hs or f"no-email+{phone_for_hs}@example.invalid",
                firstname=firstname,
                lastname=lastname,
                phone=phone_for_hs or None,
                extra_properties=extra,
            )
            print(f"[HUBSPOT] upsert_contact -> {contact_id}", flush=True)

            if contact_id:
                note_text = render_note(dossier, messages_payload)
                await hubspot_client.add_note_to_contact(contact_id, note_text)
                print("[HUBSPOT] add_note_to_contact -> OK", flush=True)

    except Exception as e:
        # niemals die Chat-Antwort blockieren
        print(f"[HUBSPOT][ERROR] {e}", flush=True)

    # 3) Antwort an den Client
    return ChatResponse(message=ChatMessage(role="assistant", content=assistant_text))


@app.post("/lead", response_model=LeadResponse)
async def lead(req: LeadRequest):
    """
    Optionaler, separater Lead-Endpunkt (falls du direkt Leads posten willst).
    """
    if not settings.HUBSPOT_PRIVATE_APP_TOKEN:
        return LeadResponse(status="skipped", detail="No HUBSPOT_PRIVATE_APP_TOKEN set")

    try:
        contact_id = await hubspot_client.upsert_contact(
            req.email,
            firstname=None,
            lastname=None,
            phone=req.phone,
            extra_properties={"address": "", "city": "", "zip": "", "jobtitle": ""},
        )
        if req.context and contact_id:
            await hubspot_client.add_note_to_contact(contact_id, req.context)

        return LeadResponse(status="ok", hubspot_contact_id=contact_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"HubSpot error: {e}")
