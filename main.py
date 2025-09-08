from fastapi import FastAPI, HTTPException
from typing import List, Dict, Any, Optional
import re

from models import ChatRequest, ChatResponse, ChatMessage, LeadRequest, LeadResponse
from settings import Settings
from middleware import attach_cors
from services.openai_client import chat_completion
from services import hubspot_client

app = FastAPI(title="Cashback Finance API", version="2.2.0")
settings = Settings()
attach_cors(app, settings)

# ------------------------------------------------------------
# Stil-Guide: Teaser → Permission → Kontakt → wenige Eckdaten
# ------------------------------------------------------------
STYLE_GUIDE = (
    "Vorgehen (immer): "
    "A) Fachlicher Teaser (1–2 Sätze) mit praktischem Mehrwert (Einsparungen, Förderungen, bis zu 20 % Cashback auf Provisionen). "
    "B) Einwilligung abfragen: 'Darf ich deine Angaben an Cashback Finance übermitteln, ja?' "
    "C) Bei Zustimmung: zuerst Name + E-Mail/Telefon, danach max. 3 Eckdatenfragen. "
    "D) Abschluss: bestätigen, dass wir bündeln/übermitteln; optional Rückruf/Termin anbieten. "
    "Regeln: Du-Ansprache, verbraucherschutzkonform, keine Portale/DIY-Aufforderungen, keine Garantien/Zinszusagen; "
    "Zahlen stets 'unverbindlich, markt- und bonitätsabhängig'. "
)

KNOW = (
    "WISSEN (Auszug): "
    "Baufinanzierung: Annuitätendarlehen, Zinsbindung, Effektivzins/Gesamtkosten, Widerruf 14 Tage; KfW. "
    "Forward-Darlehen: Zinsabsicherung; BGB §§488/491; §34i GewO; PAngV. "
    "Privatkredit/Umschuldung: BGB §§488–505. Versicherungen: VVG; §34d GewO. "
    "Strom/Gas: EnWG; Wechsel spart oft 200–500 €/J (orientierend). Kommunikation: TKG; §312k. Konto: PSD2."
)

def build_system_prompt() -> str:
    base = settings.SYSTEM_PROMPT.strip() if settings.SYSTEM_PROMPT else (
        "Du bist die KI von Cashback Finance. Sprich konsequent in der Du-Form, hilfsbereit, klar, seriös. "
        "Ziel: informieren, entlasten und verlässlich zur Zusammenarbeit mit Cashback Finance führen."
    )
    return f"{base}\n\n{STYLE_GUIDE}\n\n{KNOW}".strip()

# ------------------------------------------------------------
# Extraktion (E-Mail, Telefon, Consent) + Zusammenfassung
# ------------------------------------------------------------
EMAIL_RE   = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE_RE   = re.compile(r"(?:(?:\+|00)\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?)\d[\d\s-]{5,}")
MONEY_RE   = re.compile(r"(?<!\d)(\d{1,3}(?:[.\s]\d{3})*|\d+)(?:[.,]\d+)?\s*(?:€|eur|euro)", re.I)
RATE_RE    = re.compile(r"(\d+[.,]?\d*)\s*%")
DATE_RE    = re.compile(r"(\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{2,4})")

# 1) Explizite Einwilligung (klar-juristisch)
CONSENT_EXPLICIT = re.compile(
    r"(ich\s+(stimme|willige)\s+ein|du\s*darfst\s*mich\s*kontaktieren|ihr\s*dürft\s*mich\s*kontaktieren|"
    r"ja[, ]?\s*bitte\s*kontaktieren|kontaktaufnahme\s*(ist\s*)?erlaubt|einwilligung\s*(ist\s*)?erteilt)",
    re.I
)
# 2) Intent-basierte Zustimmung (komfortabel): Übermitteln/weiterleiten/bündeln/aufnehmen/erfassen/senden/schicken
CONSENT_INTENT_VERB = r"(übermitteln|weiterleiten|weitergeben|bündeln|aufnehmen|erfassen|senden|schicken)"
CONSENT_OK_WORD     = r"(ok|in ordnung|einverstanden|passt|ja|bitte)"
CONSENT_KEYWORD_OK  = re.compile(
    rf"\b{CONSENT_INTENT_VERB}\b.*\b{CONSENT_OK_WORD}\b|\b{CONSENT_OK_WORD}\b.*\b{CONSENT_INTENT_VERB}\b",
    re.I
)
# 3) Reine Intent-Formulierungen (ohne OK), z. B. "bitte bündeln", "eckdaten aufnehmen", "an cashback finance schicken"
CONSENT_INTENT_SOFT = re.compile(
    rf"(an\s+cashback\s+finance\s+(schicken|senden|weiterleiten)|"
    rf"bitte\s+{CONSENT_INTENT_VERB}|"
    rf"eckdaten\s+{CONSENT_INTENT_VERB}|"
    rf"global[e]?\s+selbstauskunft\s+(erstellen|anfangen|starten|bündeln)|"
    rf"{CONSENT_INTENT_VERB})",
    re.I
)
NEGATION_NEAR = re.compile(r"\b(nicht|kein|keine|nein|stop|stopp|abbrechen)\b", re.I)

def _msgs_text(messages: List[Dict[str, Any]], only_user: bool = False, last_n: int = 20) -> List[str]:
    msgs = messages[-last_n:]
    if only_user:
        msgs = [m for m in msgs if (m.get("role") == "user")]
    return [m.get("content") or "" for m in msgs]

def detect_consent(messages: List[Dict[str, Any]]) -> bool:
    user_texts = _msgs_text(messages, only_user=True, last_n=20)
    joined = "\n".join(user_texts)

    # 1) Explizit?
    if CONSENT_EXPLICIT.search(joined):
        return True

    # 2) Intent + OK in einem Satz?
    if any(CONSENT_KEYWORD_OK.search(t) for t in user_texts):
        # Negation in unmittelbarer Nähe verhindert Zustimmung
        if not any(NEGATION_NEAR.search(t) for t in user_texts):
            return True

    # 3) Reiner Intent (z. B. "bitte bündeln", "eckdaten aufnehmen") ohne Negation
    for t in user_texts:
        if CONSENT_INTENT_SOFT.search(t) and not NEGATION_NEAR.search(t):
            return True

    return False

def extract_entities(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    txt = "\n".join([f"{m.get('role')}: {m.get('content','')}" for m in messages[-30:]])
    out: Dict[str, Any] = {}
    emails = EMAIL_RE.findall(txt)
    if emails:
        out["email_detected"] = emails[-1]
    phone = PHONE_RE.search(txt)
    if phone:
        out["phone_detected"] = phone.group(0)
    if detect_consent(messages):
        out["consent_detected"] = True
    money = [m.group(0) for m in MONEY_RE.finditer(txt)]
    rates = [r.group(1) for r in RATE_RE.finditer(txt)]
    dates = [d.group(1) for d in DATE_RE.finditer(txt)]
    if money: out["money_mentions"] = money[:10]
    if rates: out["percent_mentions"] = rates[:10]
    if dates: out["dates"] = dates[:10]
    return out

def summarize_conversation(messages: List[Dict[str, Any]], email: Optional[str]) -> str:
    ents = extract_entities(messages)
    lines = []
    lines.append("Globale Selbstauskunft – Kurzprotokoll (automatisch aus Chat)")
    if email:
        lines.append(f"Kontakt (angegeben/erkannt): <{email}>")
    if ents.get("phone_detected"):
        lines.append(f"Telefon (aus Chat): {ents['phone_detected']}")
    if ents.get("money_mentions"):
        lines.append("Beträge im Chat: " + ", ".join(ents["money_mentions"]))
    if ents.get("percent_mentions"):
        lines.append("Prozentsätze im Chat: " + ", ".join(ents["percent_mentions"]))
    if ents.get("dates"):
        lines.append("Datumsangaben im Chat: " + ", ".join(ents["dates"]))
    lines.append("")
    lines.append("Chat-Verlauf (gekürzt):")
    for m in messages[-10:]:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        content = content if len(content) <= 500 else content[:497] + "…"
        lines.append(f"- {role}: {content}")
    return "\n".join(lines)

# ------------------------------------------------------------
# Endpunkte
# ------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    system_prompt = build_system_prompt()
    try:
        assistant_text = chat_completion(
            messages=[m.model_dump() for m in req.messages],
            system_prompt=system_prompt,
            model=settings.MODEL_NAME
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OpenAI error: {e}")

    # Reiner Chat-Consent (+ E-Mail aus Chat ODER req.email) → HubSpot
    try:
        msgs = [m.model_dump() for m in req.messages]
        ents = extract_entities(msgs)
        consent = bool(ents.get("consent_detected"))
        email_for_hubspot = req.email or ents.get("email_detected")
        if consent and email_for_hubspot:
            contact_id = await hubspot_client.upsert_contact(email_for_hubspot)
            note_text = summarize_conversation(msgs, email_for_hubspot)
            if contact_id:
                await hubspot_client.add_note_to_contact(contact_id, note_text)
    except Exception:
        # HubSpot darf nie den Chat blockieren
        pass

    return ChatResponse(message=ChatMessage(role="assistant", content=assistant_text))

@app.post("/lead", response_model=LeadResponse)
async def lead(req: LeadRequest):
    if not settings.HUBSPOT_PRIVATE_APP_TOKEN:
        return LeadResponse(status="skipped", detail="No HUBSPOT_PRIVATE_APP_TOKEN set")
    try:
        contact_id = await hubspot_client.upsert_contact(
            req.email, req.firstname, req.lastname, req.phone
        )
        if req.context and contact_id:
            await hubspot_client.add_note_to_contact(contact_id, req.context)
        return LeadResponse(status="ok", hubspot_contact_id=contact_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"HubSpot error: {e}")
