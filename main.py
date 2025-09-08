from fastapi import FastAPI, HTTPException
from typing import List, Dict, Any, Optional
import re

from models import ChatRequest, ChatResponse, ChatMessage, LeadRequest, LeadResponse
from settings import Settings
from middleware import attach_cors
from services.openai_client import chat_completion
from services import hubspot_client

app = FastAPI(title="Cashback Finance API", version="2.1.0")
settings = Settings()
attach_cors(app, settings)

# ------------------------------------------------------------
# Stil-Guide (Du-Ansprache, Teaser -> Permission -> Kontaktdaten)
# ------------------------------------------------------------
STYLE_GUIDE = (
    "Vorgehen (immer): "
    "A) Fachlicher Teaser in 1–2 Sätzen: zeige konkreten Vorteil (z. B. Zins, Förderungen, Cashback, Microsaving). "
    "B) Dann Einwilligung abfragen: 'Darf ich deine Angaben an Cashback Finance übermitteln, ja?' "
    "C) Falls Zustimmung: ZUERST Name + E-Mail/Telefon erfragen; erst dann weitere Eckdaten (kurz, maximal 3 Rückfragen). "
    "D) Abschluss: Bestätigen, dass wir bündeln/übermitteln und ein Rückruf/Termin möglich ist. "
    "Regeln: Du-Ansprache, kein Druck, keine Vergleichsportale, keine Garantien/Zinszusagen; "
    "Hinweis 'unverbindlich, markt- und bonitätsabhängig' bei Zahlen; "
    "Mehrwert dezent: Einsparungen + Microsaving + bis zu 20 % Cashback auf Provisionen. "
)

# Kurzer Wissens-Block (kompakt halten, erweiterbar)
KNOW = """
WISSEN (Auszug):
- Baufinanzierung: Annuitätendarlehen, Zinsbindung, Effektivzins/Gesamtkosten, Widerruf 14 Tage; KfW/Programme möglich.
- Forward-Darlehen: Zinsabsicherung für Anschluss; BGB §§488/491; §34i GewO; PAngV.
- Privatkredit/Umschuldung: BGB §§488–505; Effektivzins bonitäts-/laufzeitabhängig.
- Versicherungen: VVG; §34d GewO; Beratungsdoku/IDD.
- Strom/Gas: EnWG; Wechsel spart oft 200–500 €/Jahr (orientierend).
- Kommunikation: TKG; Maxlaufzeit 24M; §312k Kündigungsbutton.
- Konto: KWG/PSD2; Gebühren/Dispo beachten.
"""

def build_system_prompt() -> str:
    base = settings.SYSTEM_PROMPT.strip() if settings.SYSTEM_PROMPT else (
        "Du bist die KI von Cashback Finance. Sprich konsequent in der Du-Form, hilfsbereit, klar, seriös. "
        "Ziel: informieren, entlasten, und verlässlich zur Zusammenarbeit mit Cashback Finance führen."
    )
    return f"{base}\n\n{STYLE_GUIDE}\n\n{KNOW}".strip()

# ------------------------------------------------------------
# Extraktion & Zusammenfassung (Consent/E-Mail aus Chat)
# ------------------------------------------------------------
EMAIL_RE   = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE_RE   = re.compile(r"(?:(?:\+|00)\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?)\d[\d\s-]{5,}")
MONEY_RE   = re.compile(r"(?<!\d)(\d{1,3}(?:[.\s]\d{3})*|\d+)(?:[.,]\d+)?\s*(?:€|eur|euro)", re.I)
RATE_RE    = re.compile(r"(\d+[.,]?\d*)\s*%")
DATE_RE    = re.compile(r"(\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{2,4})")

# Robuste Consent-Erkennung:
# - Explizite Formeln (ich willige ein, ihr dürft mich kontaktieren, etc.)
# - UND Bestätigungen mit Schlüsselwörtern (übermitteln/weiterleiten/weitergeben/speichern/aufnehmen/erfassen)
#   kombiniert mit ok/ja/einverstanden/passt
CONSENT_EXPLICIT = re.compile(
    r"(ich\s+(stimme|willige)\s+ein|du\s*darfst\s*mich\s*kontaktieren|ihr\s*dürft\s*mich\s*kontaktieren|"
    r"ja[, ]?\s*bitte\s*kontaktieren|kontaktaufnahme\s*(ist\s*)?erlaubt|einwilligung\s*(ist\s*)?erteilt)",
    re.I
)
CONSENT_KEYWORD_OK = re.compile(
    r"\b(übermitteln|weiterleiten|weitergeben|speichern|aufnehmen|erfassen)\b.*\b(ok|in ordnung|einverstanden|passt|ja)\b"
    r"|"
    r"\b(ok|in ordnung|einverstanden|passt|ja)\b.*\b(übermitteln|weiterleiten|weitergeben|speichern|aufnehmen|erfassen)\b",
    re.I
)

def extract_entities(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    txt = "\n".join([f"{m.get('role')}: {m.get('content','')}" for m in messages[-30:]])
    out: Dict[str, Any] = {}

    emails = EMAIL_RE.findall(txt)
    if emails:
        out["email_detected"] = emails[-1]

    phone = PHONE_RE.search(txt)
    if phone:
        out["phone_detected"] = phone.group(0)

    if CONSENT_EXPLICIT.search(txt) or CONSENT_KEYWORD_OK.search(txt):
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
        lines.append(f"Kontakt (erkannt/angegeben): <{email}>")
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

    # Chat-basierte Einwilligung + E-Mail (aus Chat oder req.email) → HubSpot schreiben
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
        # HubSpot darf den Chat nie stören
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
