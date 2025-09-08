from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from typing import Dict
from models import ChatRequest, ChatResponse, ChatMessage, LeadRequest, LeadResponse
from settings import Settings
from middleware import attach_cors
from services.openai_client import chat_completion
from services import hubspot_client
import asyncio

app = FastAPI(title="Cashback Finance API", version="1.1.0")
settings = Settings()
attach_cors(app, settings)

# --- Beratungsstil & Wissen ---------------------------------------------------

STYLE_GUIDE = (
    "Antwortstil (immer): "
    "1) Kurzantwort: eine Zeile mit der Lösung – positiv formuliert (z. B. 'Ja – …'). "
    "2) Was bedeutet das? 2–4 Sätze Erklärung. "
    "3) Voraussetzungen & typische Konditionen als Liste. "
    "4) Nächste Schritte: 2–3 konkrete Aktionen. "
    "5) Optional: Beispielrechnung/Orientierungswerte. "
    "Wenn Daten fehlen: gezielt max. 3 Kernparameter abfragen. "
    "Am Ende dezent Kontaktmöglichkeit anbieten. "
    "Keine Widersprüche (kein 'erst nein, dann ja')."
)

KNOWLEDGE = """
WISSEN: Forward-Darlehen (Zinssicherung für Anschlussfinanzierung)
- Zweck: Heute Zinssatz für künftige Anschlussfinanzierung festschreiben (Vorlaufzeit typ. 12–60 Monate).
- Kosten: Forward-Aufschlag je Vorlaufmonat, grob 0,01–0,03 %-Punkte/Monat (marktabhängig).
- Voraussetzungen: Restschuld & Ablaufdatum der aktuellen Zinsbindung, Beleihungsauslauf, Bonität.
- Alternativen: Bauspar-Kombination, Prolongation bei der Hausbank, variables Darlehen mit späterer Fixierung.
- Risiken: Fallen die Zinsen, kann der gezahlte Aufschlag nachteilig sein; steigen sie, ist der Schutz vorteilhaft.
- Praxis: Angebote ca. 6–12 Monate vor Fälligkeit vergleichen; ab ~36 Monaten Vorlauf steigen Aufschläge merklich.
"""

def build_system_prompt() -> str:
    # SYSTEM_PROMPT aus ENV + Stil + Wissen
    base = settings.SYSTEM_PROMPT.strip() if settings.SYSTEM_PROMPT else ""
    return f"{base}\n\n{STYLE_GUIDE}\n\n{KNOWLEDGE}".strip()

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

    # Optional: Lead zu HubSpot
    if req.lead_opt_in and req.email:
        try:
            contact_id = await hubspot_client.upsert_contact(req.email)
            last_q = req.messages[-1].content if req.messages else ""
            note_text = f"Lead aus Website-Chat. Letzte Nutzerfrage:\n\n{last_q}"
            if contact_id:
                await hubspot_client.add_note_to_contact(contact_id, note_text)
        except Exception:
            # Chat nicht fehlschlagen lassen, wenn HubSpot ausfällt
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
