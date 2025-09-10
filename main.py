# main.py
from __future__ import annotations

import re
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from models import ChatRequest, ChatResponse, ChatMessage, LeadRequest, LeadResponse
from settings import Settings
from middleware import attach_cors
from services.openai_client import chat_completion
from services import hubspot_client

app = FastAPI(title="Cashback Finance API", version="1.3.0")
settings = Settings()
attach_cors(app, settings)

# ---------- Regex Utilities ----------
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s\-\/]?)?(?:\(?\d+\)?[\s\-\/]?){5,}")  # großzügig
ZIP_RE = re.compile(r"\b(\d{5})\b")
CITY_AFTER_ZIP_RE = re.compile(r"\b\d{5}\s+([A-Za-zÄÖÜäöüß\-\.\s]{2,})")
POSSIBLE_NAME_LINE_RE = re.compile(r"^[A-Za-zÄÖÜäöüß'’\-]+\s+[A-Za-zÄÖÜäöüß'’\-]+$", re.UNICODE)

# Zustimmung / Ablehnung (breit gefasst, inkl. „Daten aufnehmen/erfassen“ + explizit „übermitteln“)
AFFIRM_PATTERNS = [
    r"\bja\b", r"\bok(?:ay)?\b", r"\beinverstanden\b", r"\bzustimm\w*\b", r"\b(stimme|stimm)\s+zu\b",
    r"\bübermitt\w+\b", r"\bweiterleit\w+\b", r"\bweitergeb\w+\b", r"\bsend\w+\b", r"\babsend\w+\b", r"\bschick\w+\b",
    r"\bdaten\s+(?:aufnehmen|erfassen)\b", r"\beckdaten\s+aufnehmen\b", r"\bselbstauskunft\b"
]
DENY_PATTERNS = [
    r"\bnein\b", r"\bkein(e|en)?\s+übermittlung\b", r"\bnicht\s+übermitteln\b", r"\babbrechen\b", r"\bstopp?\b", r"\bstop\b",
]
AFFIRM_RE = re.compile("|".join(AFFIRM_PATTERNS), re.IGNORECASE)
DENY_RE = re.compile("|".join(DENY_PATTERNS), re.IGNORECASE)

def _find_email_phone(text: str) -> Dict[str, Optional[str]]:
    email = None; phone = None
    m = EMAIL_RE.search(text or "");  email = m.group(0).strip() if m else None
    pm = PHONE_RE.search(text or "")
    if pm:
        raw = pm.group(0)
        digits = re.sub(r"[^\d+]", "", raw)
        if len(re.sub(r"\D", "", digits)) >= 7:
            phone = digits
    return {"email": email, "phone": phone}

def _split_name(fullname: str) -> Dict[str, Optional[str]]:
    fullname = (fullname or "").strip()
    if not fullname: return {"firstname": None, "lastname": None}
    parts = [p for p in fullname.split() if p.strip()]
    if len(parts) == 1: return {"firstname": parts[0], "lastname": None}
    return {"firstname": parts[0], "lastname": " ".join(parts[1:])}

def detect_consent(messages: List[Dict]) -> bool:
    text = " ".join([(m.get("content") or "") for m in messages])
    if DENY_RE.search(text): return False
    return bool(AFFIRM_RE.search(text))

# ---------- Dossier-Extraktion ----------
def build_customer_dossier(messages: List[Dict]) -> Dict:
    joined = "\n".join([m.get("content") or "" for m in messages])
    email_phone = _find_email_phone(joined)

    # Name: 1) „Name ist…“ 2) Fallback: isolierte Zeile „Vorname Nachname“
    name = None
    for pat in [r"(?:name|ich hei(?:s|ß)e|mein name ist)\s*[:\-]?\s*([A-Za-zÄÖÜäöüß'’\-\.\s]{2,})"]:
        m = re.search(pat, joined, re.IGNORECASE)
        if m:
            cand = m.group(1).strip()
            if 2 <= len(cand) <= 80:
                name = cand; break
    if not name:
        for m in reversed(messages):
            if (m.get("role") or "") == "user":
                for line in (m.get("content") or "").splitlines():
                    line = line.strip()
                    if line and len(line) <= 80 and not EMAIL_RE.search(line) and not PHONE_RE.search(line):
                        if POSSIBLE_NAME_LINE_RE.match(line): name = line; break
            if name: break

    plz = None; ort = None
    m_zip = ZIP_RE.search(joined)
    if m_zip:
        plz = m_zip.group(1)
        m_city = CITY_AFTER_ZIP_RE.search(joined)
        if m_city: ort = m_city.group(1).strip()

    job = None
    m_job = re.search(r"(?:beruf|status|job|tätigkeit)\s*[:\-]?\s*([A-Za-zÄÖÜäöüß'’\-\.\s]{2,})", joined, re.IGNORECASE)
    if m_job: job = m_job.group(1).strip()

    parts = _split_name(name or "")
    return {
        "startformular": {
            "name": name,
            "firstname": parts.get("firstname"),
            "lastname": parts.get("lastname"),
            "email": email_phone.get("email"),
            "phone": email_phone.get("phone"),
            "plz": plz, "ort": ort, "beruf_status": job,
        }
    }

def render_note(dossier: Dict, messages: List[Dict]) -> str:
    last_user = ""
    for m in reversed(messages):
        if (m.get("role") or "") == "user":
            last_user = (m.get("content") or "").strip(); break
    sf = dossier.get("startformular", {}) or {}
    name_line = sf.get("name") or " ".join([s for s in [sf.get('firstname'), sf.get('lastname')] if s]) or "-"
    lines = [
        "Kundenakte – Kurzprotokoll (Cashback Finance KI) ",
        f"Name: {name_line}",
        f"E-Mail: {sf.get('email') or '-'}",
        f"Telefon: {sf.get('phone') or '-'}",
        f"PLZ/Ort: {(sf.get('plz') or '-')}/{(sf.get('ort') or '-')}",
        f"Beruf/Status: {sf.get('beruf_status') or '-'}",
        "",
        f"Letzte Nutzerangabe/Intent: {last_user or '-'}",
        "Hinweis: Erfasst via Website-Chat. Übermittlung nach Zustimmung im Chat."
    ]
    return "\n".join(lines)

# ---------- STRIKTER Intake-Flow im System-Prompt ----------
SYSTEM_PROMPT = (
    "Du bist die KI von Cashback Finance. Ton: freundlich, klar, DU-Ansprache. "
    "Ziel: schnelle, korrekte Hilfe + Next Steps. Zeige dezent den Mehrwert: Einsparungen, Microsaving, bis zu 20 % Cashback. "
    "WICHTIG – folge IMMER diesem Vorgehen:\n"
    "1) Begrüße kurz. 2) Wiederhole knapp das Ziel des Nutzers. 3) Bitte UMGEHEND um Eckdaten (Kompaktformular):\n"
    "   - Name (oder Alias)\n"
    "   - E-Mail ODER Telefon\n"
    "   - 2–3 themenspezifische Fragen (z. B. bei Baufinanzierung: Kaufpreis, Eigenkapital, Nettoeinkommen ca.)\n"
    "4) Wenn die Eckdaten da sind, FRAGE EXPLIZIT nach Einwilligung zur Übermittlung an Cashback Finance:\n"
    "   „Darf ich deine Angaben gemäß Datenschutzerklärung an Cashback Finance übermitteln?“\n"
    "5) Bei „Ja“: bestätige die Übermittlung in EINEM Satz und beende mit: „Du wirst kontaktiert.“\n"
    "6) Bei Baufinanzierung: nenne proaktiv sinnvolle Optionen (z. B. Forward-Darlehen), statt erst zu verneinen.\n"
    "Antworte stets in kurzen, geordneten Schritten (1., 2., 3.)."
)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    # 1) KI-Antwort
    messages_payload = [m.model_dump() for m in req.messages]
    try:
        assistant_text = chat_completion(
            messages=messages_payload,
            system_prompt=SYSTEM_PROMPT,
            model=settings.MODEL_NAME,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OpenAI error: {e}")

    # 2) Consent & HubSpot (nicht blockierend, aber sichtbar geloggt)
    try:
        flat_text = "\n".join([m.get("content") or "" for m in messages_payload])
        consent_ui = bool(getattr(req, "lead_opt_in", False))
        consent_chat = detect_consent(messages_payload)
        consent = consent_ui or consent_chat

        ids = _find_email_phone(flat_text)
        email_for_hs = (req.email or ids.get("email") or "").strip()
        phone_for_hs = (ids.get("phone") or "").strip()

        dossier = build_customer_dossier(messages_payload)
        sf = dossier.get("startformular", {}) or {}
        firstname = sf.get("firstname"); lastname = sf.get("lastname")

        if not (firstname or lastname):
            for m in reversed(req.messages):
                if m.role == "user":
                    first_line = (m.content or "").strip().splitlines()[0]
                    parts = _split_name(first_line)
                    firstname = firstname or parts.get("firstname")
                    lastname  = lastname  or parts.get("lastname")
                    break

        city = sf.get("ort") or ""; zipc = sf.get("plz") or ""; jobtitle = sf.get("beruf_status") or ""

        # Debug-Logging, um Ursachen sofort zu sehen
        print(f"[INTAKE] email={email_for_hs} phone={phone_for_hs} name=({firstname},{lastname}) plz={zipc} ort={city}", flush=True)
        print(f"[CONSENT] ui={consent_ui} chat={consent_chat} -> {consent}", flush=True)

        if consent and (email_for_hs or phone_for_hs):
            extra = {"address": "", "city": city, "zip": zipc, "jobtitle": jobtitle}
            contact_id = await hubspot_client.upsert_contact(
                email_for_hs or f"no-email+{phone_for_hs}@example.invalid",
                firstname=firstname, lastname=lastname,
                phone=phone_for_hs or None,
                extra_properties=extra,
            )
            print(f"[HUBSPOT] upsert_contact -> {contact_id}", flush=True)
            if contact_id:
                note_text = render_note(dossier, messages_payload)
                await hubspot_client.add_note_to_contact(contact_id, note_text)
                print("[HUBSPOT] add_note_to_contact -> OK", flush=True)
        else:
            if not consent:
                print("[HUBSPOT] Skipped: no consent", flush=True)
            elif not (email_for_hs or phone_for_hs):
                print("[HUBSPOT] Skipped: no email/phone found", flush=True)

    except Exception as e:
        print(f"[HUBSPOT][ERROR] {e}", flush=True)

    return ChatResponse(message=ChatMessage(role="assistant", content=assistant_text))

@app.post("/lead", response_model=LeadResponse)
async def lead(req: LeadRequest):
    if not settings.HUBSPOT_PRIVATE_APP_TOKEN:
        return LeadResponse(status="skipped", detail="No HUBSPOT_PRIVATE_APP_TOKEN set")
    try:
        contact_id = await hubspot_client.upsert_contact(
            req.email, firstname=None, lastname=None, phone=req.phone,
            extra_properties={"address": "", "city": "", "zip": "", "jobtitle": ""},
        )
        if req.context and contact_id:
            await hubspot_client.add_note_to_contact(contact_id, req.context)
        return LeadResponse(status="ok", hubspot_contact_id=contact_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"HubSpot error: {e}")
