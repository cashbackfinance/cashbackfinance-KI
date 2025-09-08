from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from typing import Dict
from models import ChatRequest, ChatResponse, ChatMessage, LeadRequest, LeadResponse
from settings import Settings
from middleware import attach_cors
from services.openai_client import chat_completion
from services import hubspot_client
import asyncio

app = FastAPI(title="Cashback Finance API", version="1.0.0")
settings = Settings()
attach_cors(app, settings)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    # Build system prompt from settings + lead-orientierung leise einweben
    system_prompt = settings.SYSTEM_PROMPT + " Antworte in kurzen, klaren Schritten. Frage nach Einwilligung zur Kontaktaufnahme, wenn sinnvoll."
    try:
        assistant_text = chat_completion(
            messages=[m.model_dump() for m in req.messages],
            system_prompt=system_prompt,
            model=settings.MODEL_NAME
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OpenAI error: {e}")

    # Optional: create/update HubSpot contact and attach note
    if req.lead_opt_in and req.email:
        try:
            contact_id = await hubspot_client.upsert_contact(req.email)
            last_q = req.messages[-1].content if req.messages else ""
            note_text = f"Lead aus Website-Chat. Letzte Nutzerfrage:\n\n{last_q}"
            if contact_id:
                await hubspot_client.add_note_to_contact(contact_id, note_text)
        except Exception as e:
            # Don't fail chat if HubSpot fails
            contact_id = None

    return ChatResponse(message=ChatMessage(role="assistant", content=assistant_text))

@app.post("/lead", response_model=LeadResponse)
async def lead(req: LeadRequest):
    if not settings.HUBSPOT_PRIVATE_APP_TOKEN:
        return LeadResponse(status="skipped", detail="No HUBSPOT_PRIVATE_APP_TOKEN set")
    try:
        contact_id = await hubspot_client.upsert_contact(req.email, req.firstname, req.lastname, req.phone)
        if req.context and contact_id:
            await hubspot_client.add_note_to_contact(contact_id, req.context)
        return LeadResponse(status="ok", hubspot_contact_id=contact_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"HubSpot error: {e}")
