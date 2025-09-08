from fastapi import FastAPI, HTTPException
from typing import List, Dict, Any, Optional
import re

from models import ChatRequest, ChatResponse, ChatMessage, LeadRequest, LeadResponse
from settings import Settings
from middleware import attach_cors
from services.openai_client import chat_completion
from services import hubspot_client

app = FastAPI(title="Cashback Finance API", version="1.6.0")
settings = Settings()
attach_cors(app, settings)

# ------------------------------------------------------------
# Stil-Guide: Du-Ansprache, Mehrwert, Beispielrechnungen etc.
# ------------------------------------------------------------
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

# ------------------------------------------------------------
# (Kurz)Wissensblöcke – du kannst sie bei Bedarf jederzeit
# erweitern; sie sind hier bewusst kompakt gehalten.
# ------------------------------------------------------------
KNOW_FORWARD = """
WISSEN: Forward-Darlehen (Zinssicherung für Anschlussfinanzierung)
- BGB §§488/491; WohnimmobilienkreditRL; §34i GewO; PAngV.
- Vorlaufzeit 12–60 Monate; Aufschlag je Vorlaufmonat (marktabhängig).
- Vorteil: Zinsrisiko absichern; Risiko: bei fallenden Zinsen ggf. teurer.
"""
KNOW_BAUFI = """
WISSEN: Baufinanzierung
- Annuitätendarlehen (Zinsbindung 5–30 J.), Pflichtangaben: Effektivzins/Gesamtkosten, Widerruf 14 Tage.
- Förderungen: KfW/Landesprogramme; Vorfälligkeitsentschädigung beachten.
"""
KNOW_PRIVATKREDIT = """
WISSEN: Privatkredite/Umschuldung
- BGB §§488–505; PAngV. Effektivzins bonitäts-/laufzeitabhängig, 14 Tage Widerruf.
"""
KNOW_VERSICHERUNG = """
WISSEN: Versicherungen
- VVG; §34d GewO; VersVermV/IDD. Beratungsdokumentation & Vergütungstransparenz.
"""
KNOW_STROM_GAS = """
WISSEN: Strom & Gas
- EnWG; PAngV. Wechsel spart oft 200–500 €/Jahr (orientierend). Bonus-/Laufzeiten prüfen.
"""
KNOW_KOMMUNIKATION = """
WISSEN: Kommunikation
- TKG; BGB §309 Nr.9; §312k BGB (Kündigungsbutton). Laufzeit idR max. 24 Monate.
"""
KNOW_KONTO = """
WISSEN: Konto
- KWG/PSD2; BGB Zahlungsdienste. Kontoführungsgebühren & Dispo im Blick; oft bedingungsfrei möglich.
"""
KNOW_GELDANLAGE = "WISSEN: Geldanlage – WpHG/§34f GewO/KAGB, Risiken ohne Garantie."
KNOW_ALTERSVORSORGE = "WISSEN: Altersvorsorge – EStG §10/§10a/§3 Nr.63; Produkt-/Steuerhinweise."
KNOW_REISE = "WISSEN: Reise – BGB §§651a ff.; über Partner i.d.R. ~4 % Cashback (orientierend)."

ALL_KNOWLEDGE = "\n".join([
    KNOW_FORWARD, KNOW_BAUFI, KNOW_PRIVATKREDIT, KNOW_VERSICHERUNG, KNOW_STROM_GAS,
    KNOW_KOMMUNIKATION, KNOW_KONTO, KNOW_GELDANLAGE, KNOW_ALTERSVORSORGE, KNOW_REISE
])

def build_system_prompt() -> str:
    base = settings.SYSTEM_PROMPT.strip() if settings.SYSTEM_PROMPT else (
        "Du bist die KI von Cashback Finance. Sprich Nutzer konsequent in der Du-Form an, "
        "freundlich, respektvoll und vertrauensvoll. "
        "Dein Ziel: klar informieren und verlässlich zur Zusammenarbeit mit Cashback Finance führen."
    )
    return f"{base}\n\n{STYLE_GUIDE}\n\n{ALL_KNOWLEDGE}".strip()

# ------------------------------------------------------------
# Extraktion & Zusammenfassung für HubSpot-Notiz
# ------------------------------------------------------------
_EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_MONEY = re.compile(r"(?<!\d)(\d{1,3}(?:[.\s]\d{3})*|\d+)(?:[.,]\d+)?\s*(?:€|eur|euro)", re.I)
_RATE  = re.compile(r"(\d+[.,]?\d*)\s*%")
_DATE  = re.compile(r"(\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{2,4})")
_PHONE = re.compile(r"(?:(?:\+|00)\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?)\d[\d\s-]{5,}")

def extract_entities(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    txt = "\n".join([f"{m.get('role')}: {m.get('content','')}" for m in messages[-30:]])
    out: Dict[str, Any] = {"topics": []}

    topics = {
        "baufi": ["baufinanz", "anschluss", "forward", "zinsbindung", "immobilie", "restschuld"],
        "privatkredit": ["umschuld", "ratenkredit", "privatkredit", "auto", "fahrzeug", "autokauf"],
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

    emails = _EMAIL.findall(txt)
    if emails: out["email_detected"] = emails[-1]
    money = [m.group(0) for m in _MONEY.finditer(txt)]
    rates = [r.group(1) for r in _RATE.finditer(txt)]
    dates = [d.group(1) for d in _DATE.finditer(txt)]
    phone = _PHONE.search(txt)
    if money: out["money_mentions"] = money[:10]
    if rates: out["percent_mentions"] = rates[:10]
    if dates: out["dates"] = dates[:10]
    if phone: out["phone_detected"] = phone.group(0)

    if "baufi" in out["topics"]:
        out["baufi"] = {}
    if "privatkredit" in out["topics"]:
        out["privatkredit"] = {}
    return out

def summarize_conversation(messages: List[Dict[str, Any]], email: Optional[str]) -> str:
    ents = extract_entities(messages)
    lines = []
    lines.append("Globale Selbstauskunft – Kurzprotokoll (automatisch aus Chat)")
    if email:
        lines.append(f"Kontakt (Formular/erkannt): <{email}>")
    if ents.get("email_detected") and ents.get("email_detected") != email:
        lines.append(f"Zusätzlich erkannt: <{ents['email_detected']}>")
    if "phone_detected" in ents:
        lines.append(f"Telefon (aus Chat): {ents['phone_detected']}")
    if ents.get("topics"):
        lines.append("Themen: " + ", ".join(ents["topics"]))
    if ents.get("money_mentions"):
        lines.append("Geldbeträge im Chat: " + ", ".join(ents["money_mentions"]))
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

    # Notiz an HubSpot: nur bei Einwilligung; E-Mail aus Formular ODER Chat
    if req.lead_opt_in:
        try:
            ents = extract_entities([m.model_dump() for m in req.messages])
            email_for_hubspot = req.email or ents.get("email_detected")
            if email_for_hubspot:
                contact_id = await hubspot_client.upsert_contact(email_for_hubspot)
                note_text = summarize_conversation([m.model_dump() for m in req.messages], email_for_hubspot)
                if contact_id:
                    await hubspot_client.add_note_to_contact(contact_id, note_text)
        except Exception:
            # HubSpot-Probleme dürfen den Chat nicht stören
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
