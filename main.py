from fastapi import FastAPI, HTTPException
from models import ChatRequest, ChatResponse, ChatMessage, LeadRequest, LeadResponse
from settings import Settings
from middleware import attach_cors
from services.openai_client import chat_completion
from services import hubspot_client
import asyncio
import re
from typing import List, Dict, Any, Optional

app = FastAPI(title="Cashback Finance API", version="1.5.0")
settings = Settings()
attach_cors(app, settings)

# --- Stil-Guide & Wissensbasis (wie zuvor) -----------------------------------
STYLE_GUIDE = (
    "Antwortstil (immer): "
    "1) Kurzantwort: positiv & lösungsorientiert (Du-Anrede, 1–2 Sätze). "
    "2) Einordnung: 2–4 Sätze, was das praktisch bedeutet (sachlich, verbraucherschutzkonform, widerspruchsfrei). "
    "3) Voraussetzungen & typische Konditionen: stichpunktartig; immer 'unverbindlich, markt- und bonitätsabhängig'. "
    "4) Cashback-Mehrwert (dezent & kontextbezogen): "
    "   Hebe hervor, dass wir über Einsparungen, Microsaving und 20 % Cashback auf Provisionen "
    "   einen echten finanziellen Vorteil für dich erzeugen – praxisnah, nicht aufdringlich. "
    "5) Nächste Schritte (Lead-Pfad): "
    "   A) Ich nehme jetzt deine Eckdaten auf und bündele sie zur 'Globalen Selbstauskunft' (du erhältst sie von uns). "
    "   B) Oder wir machen direkt einen Rückruf/Termin mit Cashback Finance. "
    "6) Abschlussfrage: 'Sollen wir deine Daten jetzt erfassen oder möchtest du lieber einen Rückruf?' "
    "Regeln: Du-Anrede. Keine Vergleichsportale/DIY-Aufforderungen. Keine Garantien/Zinszusagen. "
    "Maximal 3 gezielte Nachfragen, wenn Angaben fehlen. DSGVO: Kontakt-/Personendaten nur mit Einwilligung. "
    "Microsaving-/Beispielrechnungs-Hooks: Wenn der Nutzer 'sparen', 'günstiger', 'Kosten senken', 'Anschluss', 'Forward', "
    "'Umschuldung', 'Versicherung wechseln', 'Strom', 'Gas', 'Mobilfunk', 'Internet', 'Girokonto', 'Reise' o.ä. anspricht, "
    "führe eine kurze, klar gekennzeichnete Beispielrechnung durch (monatlich, jährlich, ggf. Cashback). "
)

# --- Wissensblöcke (gekürzt für Übersicht – behalte deine aus der letzten Version) ---
KNOW_FORWARD = "WISSEN: Forward-Darlehen …"
KNOW_BAUFI = "WISSEN: Baufinanzierung …"
KNOW_PRIVATKREDIT = "WISSEN: Privatkredite …"
KNOW_BAUSPAR = "WISSEN: Bausparvertrag …"
KNOW_VERSICHERUNG = "WISSEN: Versicherungen …"
KNOW_GELDANLAGE = "WISSEN: Geldanlage …"
KNOW_ALTERSVORSORGE = "WISSEN: Altersvorsorge …"
KNOW_KOMMUNIKATION = "WISSEN: Kommunikation …"
KNOW_STROM_GAS = "WISSEN: Strom & Gas …"
KNOW_KONTO = "WISSEN: Konto …"
KNOW_REISE = "WISSEN: Reise …"

ALL_KNOWLEDGE = "\n".join([
    KNOW_FORWARD, KNOW_BAUFI, KNOW_PRIVATKREDIT, KNOW_BAUSPAR, KNOW_VERSICHERUNG,
    KNOW_GELDANLAGE, KNOW_ALTERSVORSORGE, KNOW_KOMMUNIKATION, KNOW_STROM_GAS,
    KNOW_KONTO, KNOW_REISE
])

def build_system_prompt() -> str:
    base = settings.SYSTEM_PROMPT.strip() if settings.SYSTEM_PROMPT else (
        "Du bist die KI von Cashback Finance. Sprich Nutzer konsequent in der Du-Form an, "
        "freundlich, respektvoll und vertrauensvoll. "
        "Dein Ziel: klar informieren und verlässlich zur Zusammenarbeit mit Cashback Finance führen."
    )
    return f"{base}\n\n{STYLE_GUIDE}\n\n{ALL_KNOWLEDGE}".strip()

# --- Utility: Einfache Entitätsextraktion aus Chat-Verlauf --------------------

_MONEY = re.compile(r"(?<!\d)(\d{1,3}(?:[.\s]\d{3})*|\d+)(?:[.,]\d+)?\s*(?:€|eur|euro)", re.I)
_RATE = re.compile(r"(\d+[.,]?\d*)\s*%")
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{2,4})")
_PHONE = re.compile(r"(?:(?:\+|00)\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?)\d[\d\s-]{5,}")

def _norm_num(txt: str) -> str:
    # 300.000 -> 300000; 3,5% bleibt "3,5%" in RATE
    return txt.replace(".", "").replace(" ", "")

def extract_entities(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    txt = "\n".join([f"{m.get('role')}: {m.get('content','')}" for m in messages[-30:]])  # letzte 30 Beiträge
    out: Dict[str, Any] = {"topics": []}

    # grobe Topic-Erkennung
    topics = {
        "baufi": ["baufinanz", "anschluss", "forward", "zinsbindung", "immobilie", "restschuld"],
        "privatkredit": ["umschuld", "ratenkredit", "privatkredit"],
        "versicherungen": ["versicherung", "haftpflicht", "kfz", "hausrat", "bu", "kranken"],
        "strom_gas": ["strom", "gas", "grundversorgung", "abschlag", "kwh"],
        "kommunikation": ["mobilfunk", "internet", "dsl", "glasfaser", "tarif"],
        "konto": ["giro", "konto", "kontoführungsgebühr"],
        "reise": ["reise", "urlaub", "flug", "hotel"]
    }
    low = txt.lower()
    for key, kws in topics.items():
        if any(k in low for k in kws):
            out["topics"].append(key)

    # einfache Muster
    money = [m.group(0) for m in _MONEY.finditer(txt)]
    rates = [r.group(1) for r in _RATE.finditer(txt)]
    dates = [d.group(1) for d in _DATE.finditer(txt)]
    phone = _PHONE.search(txt)
    if money: out["money_mentions"] = money[:10]
    if rates: out["percent_mentions"] = rates[:10]
    if dates: out["dates"] = dates[:10]
    if phone: out["phone_detected"] = phone.group(0)

    # baufi-spezifische Heuristiken
    if "baufi" in out["topics"]:
        baufi: Dict[str, Any] = {}
        m_rest = re.search(r"(restschuld|darlehensrest)\D{0,12}(\d[\d.\s]{3,})", low)
        if m_rest:
            baufi["restschuld"] = _norm_num(m_rest.group(2))
        m_ende = re.search(r"(zinsbindung.*?(ende|bis))\D{0,8}(" + _DATE.pattern + ")", low, re.I)
        if m_ende:
            baufi["zinsbindung_ende_hint"] = m_ende.group(3)
        out["baufi"] = baufi

    return out

def summarize_conversation(messages: List[Dict[str, Any]], email: Optional[str]) -> str:
    ents = extract_entities(messages)
    lines = []
    lines.append("Globale Selbstauskunft – Kurzprotokoll (automatisch aus Chat)")
    if email:
        lines.append(f"Kontakt: <{email}>")
    if "phone_detected" in ents:
        lines.append(f"Telefon (aus Chat erkannt): {ents['phone_detected']}")
    if ents.get("topics"):
        lines.append("Themen: " + ", ".join(ents["topics"]))
    if ents.get("baufi"):
        lines.append("[Baufinanzierung]")
        for k, v in ents["baufi"].items():
            lines.append(f"- {k}: {v}")
    if ents.get("money_mentions"):
        lines.append("Geldbeträge im Chat: " + ", ".join(ents["money_mentions"]))
    if ents.get("percent_mentions"):
        lines.append("Prozentsätze im Chat: " + ", ".join(ents["percent_mentions"]))
    if ents.get("dates"):
        lines.append("Datumsangaben im Chat: " + ", ".join(ents["dates"]))
    lines.append("")
    lines.append("Chat-Verlauf (gekürzt):")
    for m in messages[-10:]:  # letzte 10 Beiträge
        role = m.get("role")
        content = m.get("content", "").strip()
        content = content if len(content) <= 500 else content[:497] + "…"
        lines.append(f"- {role}: {content}")
    return "\n".join(lines)

# --- Endpunkte ----------------------------------------------------------------

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

    # Lead-Notiz inkl. Gesprächszusammenfassung (statt nur letzte Frage)
    if req.lead_opt_in and req.email:
        try:
            contact_id = await hubspot_client.upsert_contact(req.email)
            note_text = summarize_conversation([m.model_dump() for m in req.messages], req.email)
            if contact_id:
                await hubspot_client.add_note_to_contact(contact_id, note_text)
        except Exception:
            pass  # HubSpot-Ausfall darf Chat nicht brechen

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
