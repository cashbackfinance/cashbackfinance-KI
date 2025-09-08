# Cashback Finance — FastAPI Backend (Render-ready)

This is a minimal, production-ready FastAPI service to power your website chat and lead capture.

## Features
- `POST /chat` — proxy to OpenAI (model configurable via env), returns an assistant message.
- `POST /lead` — upserts a HubSpot contact and logs a note with context.
- `GET /health` — simple healthcheck for Render.
- CORS enabled for your Jimdo domain.
- Environment-based config; no secrets in code.

## Quick Deploy on Render
1. Create a **New Web Service**.
2. Set **Environment** to **Python 3.11** (or higher).
3. **Build Command**: `pip install -r requirements.txt`
4. **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Set Environment Variables:
   - `OPENAI_API_KEY`
   - `MODEL_NAME` (e.g. `gpt-4o-mini` or `gpt-4.1-mini`)
   - `HUBSPOT_PRIVATE_APP_TOKEN` (optional for /lead)
   - `ALLOWED_ORIGINS` (comma-separated; e.g. `https://www.cashbackfinance.de,https://cashbackfinance.jimdosite.com`)
   - `SYSTEM_PROMPT` (optional — your lead-first advisory tone)
6. Deploy. Hit `/health` to confirm: `https://<your-service>.onrender.com/health`

## Test locally
```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

## Sample Requests
### Chat
```bash
curl -X POST http://localhost:8000/chat   -H "Content-Type: application/json"   -d '{"messages":[{"role":"user","content":"Hallo, ich brauche Hilfe zur Baufinanzierung."}], "lead_opt_in": true, "email": "kai@example.com"}'
```

### Lead
```bash
curl -X POST http://localhost:8000/lead   -H "Content-Type: application/json"   -d '{"email":"kai@example.com","firstname":"Kai","lastname":"Broth-Esser","context":"Lead aus Website-Chat: Baufinanzierung."}'
```

## Notes
- This service **does not** store PII by default; it only forwards to HubSpot if called.
- Adapt `/services/hubspot_client.py` to add CRM objects (deals/tickets) as needed.
- Add retrieval/knowledge base later under `services/knowledge.py`.
