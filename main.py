from fastapi import FastAPI, HTTPException
from typing import List, Dict, Any, Optional
import re
import sys
import json

from models import ChatRequest, ChatResponse, ChatMessage, LeadRequest, LeadResponse
from settings import Settings
from middleware import attach_cors
from services.openai_client import chat_completion
from services import hubspot_client

app = FastAPI(title="Cashback Finance API", version="2.6.0")
settings = Settings()
attach_cors(app, settings)

# ---------- PROMPTS ----------
MASTER_FLOW = (
    "Arbeitsweise (Kundenakte): "
    "1) Startformular (Pflicht: Name/Alias, Geburtsdatum, Adresse PLZ/Ort, E-Mail, Telefon, Familienstand, "
    "Haushaltsgröße, Einkommen netto, Beruf/Status, Erstes Thema). "
    "2) Themenwahl: Privatkredit, Baufinanzierung, Versicherung, Strom & Gas, Kommunikation, Bausparen, "
    "Geldanlage/Altersvorsorge, Konto, Reise. "
    "3) Je Thema nur nötige Felder (max. 3 Fragen pro Turn). "
    "4) Beispielrechnung mit Hinweis: 'Dies ist eine unverbindliche Beispielrechnung.' "
    "5) Leadabschluss: Wenn Einwilligung vorliegt, klar bestätigen: 'Ich habe deine Daten sicher an Cashback Finance übermittelt.' "
    "6) Falls NOCH KEINE Einwilligung (weder UI noch Chat-Text), stelle am ENDE der Datenerfassung IMMER die JA/NEIN-Frage: "
    "'Möchtest du, dass ich deine Angaben gemäß Datenschutzerklärung an Cashback Finance übermittle?' "
)

STYLE_GUIDE = (
    "Stil/Regeln: Du-Ansprache, kurz, klar, seriös; verbraucherschutzkonform; keine Portale/DIY; "
    "keine Garantien/Zinszusagen; Zahlen immer 'unverbindlich, markt- und bonitätsabhängig'. "
    "Highlight Mehrwert: Einsparungen, Microsaving, bis zu 20 % Cashback auf Provisionen. "
    "Wichtig: Sage NIEMALS 'ich kann nicht übermitteln'. Wenn Einwilligung + Kontaktdaten da sind, bestätige die Übermittlung klar."
)

DOMAIN_KNOW = (
    "Wissen (Kurz): Baufinanzierung/Forward: BGB §§488/491, §34i GewO, PAngV, KfW; "
    "Privatkredit: BGB §§488–505; Versicherungen: VVG, §34d GewO; Strom/Gas: EnWG; Kommunikation: TKG/§312k; Konto: PSD2."
)

def build_system_prompt() -> str:
    base = settings.SYSTEM_PROMPT.strip() if settings.SYSTEM_PROMPT else (
        "Du bist die KI von Cashback Finance. Sprich konsequent in der Du-Form, hilfsbereit, klar, seriös. "
        "Ziel: informieren, entlasten und verlässlich zur Zusammenarbeit mit Cashback Finance führen."
    )
    return f"{base}\n\n{MASTER_FLOW}\n{STYLE_GUIDE}\n{DOMAIN_KNOW}".strip()

# ---------- REGEX ----------
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE_RE = re.compile(r"(?:(?:\+|00)\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?)\d[\d\s-]{5,}")
EURO_RE  = re.compile(r"(?<!\d)(\d{1,3}(?:[.\s]\d{3})*|\d+)(?:[.,]\d+)?\s*(?:€|eur|euro)", re.I)
PCT_RE   = re.compile(r"(\d+[.,]?\d*)\s*%")
DATE_RE  = re.compile(r"(\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{2,4})")
PLZ_RE   = re.compile(r"\b(\d{5})\b")

# Consent
CONSENT_EXPLICIT = re.compile(
    r"(ich\s+(stimme|willige)\s+ein|du\s*darfst\s*mich\s*kontaktieren|ihr\s*dürft\s*mich\s*kontaktieren|"
    r"ja[, ]?\s*bitte\s*kontaktieren|kontaktaufnahme\s*(ist\s*)?erlaubt|einwilligung\s*(ist\s*)?erteilt)",
    re.I
)
CONSENT_INTENT_VERB = r"(übermitteln|weiterleiten|weitergeben|bündeln|aufnehmen|erfassen|senden|schicken)"
CONSENT_OK_WORD     = r"(ok|okay|in ordnung|einverstanden|passt|ja|bitte|mach|los)"
CONSENT_KEYWORD_OK  = re.compile(
    rf"\b{CONSENT_INTENT_VERB}\b.*\b{CONSENT_OK_WORD}\b|\b{CONSENT_OK_WORD}\b.*\b{CONSENT_INTENT_VERB}\b",
    re.I
)
CONSENT_INTENT_SOFT = re.compile(
    rf"(an\s+cashback\s+finance\s+(schicken|senden|weiterleiten)|"
    rf"bitte\s+{CONSENT_INTENT_VERB}|"
    rf"eckdaten\s+{CONSENT_INTENT_VERB}|"
    rf"global[e]?\s+selbstauskunft\s+(erstellen|anfangen|starten|bündeln)|"
    rf"{CONSENT_INTENT_VERB})",
    re.I
)
NEGATION_NEAR  = re.compile(r"\b(nicht|kein|keine|nein|stop|stopp|abbrechen)\b", re.I)
ASSISTANT_ASKS = re.compile(r"(übermitteln|weiterleiten|weitergeben|bündeln|an\s+cashback\s+finance)", re.I)
USER_AFFIRM    = re.compile(r"\b(ja|ok|okay|passt|einverstanden|mach|bitte|los)\b", re.I)

def _msgs(messages: List[Dict[str, Any]], only_user: bool = False, last_n: int = 30) -> List[Dict[str, Any]]:
    msgs = messages[-last_n:]
    if only_user:
        msgs = [m for m in msgs if m.get("role") == "user"]
    return msgs

def detect_consent(messages: List[Dict[str, Any]]) -> bool:
    user_msgs = _msgs(messages, only_user=True, last_n=20)
    txt = "\n".join([(m.get("content") or "") for m in user_msgs])
    if CONSENT_EXPLICIT.search(txt):
        return True
    if any(CONSENT_KEYWORD_OK.search(m.get("content") or "") for m in user_msgs):
        if not any(NEGATION_NEAR.search(m.get("content") or "") for m in user_msgs):
            return True
    for m in user_msgs:
        t = m.get("content") or ""
        if CONSENT_INTENT_SOFT.search(t) and not NEGATION_NEAR.search(t):
            return True
    # Kontext: Assistent fragt → Nutzer bejaht
    last_msgs = messages[-6:]
    for i in range(len(last_msgs)-1):
        a, u = last_msgs[i], last_msgs[i+1]
        if a.get("role") == "assistant" and u.get("role") == "user":
            if ASSISTANT_ASKS.search(a.get("content") or "") and USER_AFFIRM.search(u.get("content") or ""):
                if not NEGATION_NEAR.search(u.get("content") or ""):
                    return True
    return False

# ---------- Kundenakte ----------
def _find_email_phone(text: str) -> Dict[str, str]:
    out = {}
    emails = EMAIL_RE.findall(text)
    if emails: out["email"] = emails[-1]
    ph = PHONE_RE.search(text)
    if ph: out["phone"] = ph.group(0)
    return out

def _extract_value_after(label: str, text: str) -> Optional[str]:
    pat = re.compile(rf"{label}\s*[:\-]?\s*(.+)", re.I)
    m = pat.search(text)
    if m:
        val = m.group(1).strip().split("\n")[0].strip()
        return val
    return None

def extract_startformular(full_text: str) -> Dict[str, Any]:
    sf: Dict[str, Any] = {}
    for lab, key in [
        ("Name", "name"), ("Alias", "alias"), ("Geburtsdatum", "geburtsdatum"),
        ("Adresse", "adresse"), ("PLZ", "plz"), ("Ort", "ort"),
        ("E-Mail", "email"), ("Telefon", "telefon"), ("Familienstand", "familienstand"),
        ("Haushaltsgröße", "haushalt"), ("Einkommen", "einkommen_netto"),
        ("Beruf", "beruf_status"), ("Erstes Beratungsthema", "erstes_thema")
    ]:
        val = _extract_value_after(lab, full_text)
        if val: sf[key] = val
    aux = _find_email_phone(full_text)
    if "email" not in sf and "email" in aux: sf["email"] = aux["email"]
    if "telefon" not in sf and "phone" in aux: sf["telefon"] = aux["phone"]
    if "plz" not in sf:
        m = PLZ_RE.search(full_text)
        if m: sf["plz"] = m.group(1)
    return sf

def extract_topics(full_text: str) -> Dict[str, Any]:
    topics: Dict[str, Any] = {}
    if re.search(r"\b(privatkredit|kredit|umschuldung)\b", full_text, re.I):
        topics["privatkredit"] = {
            "summe": _extract_value_after("Darlehenssumme", full_text) or _extract_value_after("Gewünschte Darlehenssumme", full_text),
            "wunschrate_laufzeit": _extract_value_after("Wunschrate", full_text) or _extract_value_after("Laufzeit", full_text),
            "verwendungszweck": _extract_value_after("Verwendungszweck", full_text)
        }
    if re.search(r"\b(baufinanz|baukredit|immobilie|forward)\b", full_text, re.I):
        topics["baufinanzierung"] = {
            "objektwert": _extract_value_after("Objektwert", full_text),
            "darlehensbedarf": _extract_value_after("Darlehensbedarf", full_text),
            "eigenkapital": _extract_value_after("Eigenkapital", full_text),
            "zinsbindung": _extract_value_after("Zinsbindung", full_text)
        }
    if re.search(r"\b(versicherung|haftpflicht|hausrat|wohngebäude|bu|pferde)\b", full_text, re.I):
        topics["versicherung"] = {
            "bestehend": _extract_value_after("Bestehende Versicherungen", full_text),
            "jahresbeitrag": _extract_value_after("Jahresbeitrag", full_text),
            "wechsel": _extract_value_after("Wechselbereitschaft", full_text)
        }
    if re.search(r"\b(strom|gas|kwh|pv|photovoltaik)\b", full_text, re.I):
        topics["strom_gas"] = {
            "anbieter": _extract_value_after("Anbieter", full_text),
            "strom_kwh": _extract_value_after("Jahresverbrauch Strom", full_text),
            "gas_kwh": _extract_value_after("Jahresverbrauch Gas", full_text),
            "kosten_monat": _extract_value_after("Monatliche Kosten", full_text),
            "pv": "ja" if re.search(r"\b(pv|photovoltaik)\b", full_text, re.I) else None
        }
    if re.search(r"\b(mobilfunk|internet|festnetz|telekom|o2|vodafone|dsl|kabel)\b", full_text, re.I):
        topics["kommunikation"] = {
            "anzahl_vertraege": _extract_value_after("Anzahl Verträge", full_text),
            "anbieter": _extract_value_after("Anbieter", full_text),
            "kosten_gesamt": _extract_value_after("Monatliche Kosten", full_text),
            "laufzeiten": _extract_value_after("Vertragslaufzeiten", full_text)
        }
    if re.search(r"\b(bauspar|wüstenrot|lbs)\b", full_text, re.I):
        topics["bausparen"] = {
            "vertragswert": _extract_value_after("Vertragswert", full_text) or _extract_value_after("Bausparsumme", full_text),
            "sparrate": _extract_value_after("Sparrate", full_text),
            "zweck": _extract_value_after("Ziel", full_text) or _extract_value_after("Zweck", full_text)
        }
    if re.search(r"\b(geldanlage|vorsorge|etf|rente|riester|rürup|fonds|sparrate|anlagebetrag)\b", full_text, re.I):
        topics["anlage_vorsorge"] = {
            "einmalanlage": _extract_value_after("Anlagebetrag (einmalig)", full_text) or _extract_value_after("Einmalanlage", full_text),
            "sparrate": _extract_value_after("Monatliche Sparrate", full_text),
            "horizont": _extract_value_after("Zielhorizont", full_text),
            "risiko": _extract_value_after("Risikoneigung", full_text),
            "vorhanden": _extract_value_after("Altersvorsorgeformen", full_text)
        }
    if re.search(r"\b(konto|konten|hausbank)\b", full_text, re.I):
        topics["konto"] = {
            "bestehende_konten": _extract_value_after("Bestehende Konten", full_text),
            "wechselinteresse": _extract_value_after("Wechselinteresse", full_text)
        }
    if re.search(r"\b(reise|urlaub)\b", full_text, re.I):
        topics["reise"] = {
            "jahresbudget": _extract_value_after("Jahresbudget", full_text),
            "gewohnheiten": _extract_value_after("Reisegewohnheiten", full_text)
        }
    return topics

def build_customer_dossier(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    full_text = "\n".join([f"{m.get('role')}: {m.get('content','')}" for m in messages])
    start = extract_startformular(full_text)
    topics = extract_topics(full_text)
    euros = [m.group(0) for m in EURO_RE.finditer(full_text)][:20]
    pcts  = [m.group(1) for m in PCT_RE.finditer(full_text)][:10]
    dates = [m.group(1) for m in DATE_RE.finditer(full_text)][:10]
    return {
        "startformular": start,
        "themen": topics,
        "streu_infos": {"betraege": euros, "prozente": pcts, "daten": dates},
    }

def render_note(dossier: Dict[str, Any], tail_chat: List[Dict[str, Any]]) -> str:
    sf = dossier.get("startformular", {})
    th = dossier.get("themen", {})
    def val(x): return x if x else "-"
    lines = []
    lines.append("Kundenakte – Kurzprotokoll (automatisch aus Chat) – Cashback Finance\n")
    lines.append("Startformular:")
    lines.append(f"- Name/Alias: {val(sf.get('name')) or val(sf.get('alias'))}")
    lines.append(f"- Geburtsdatum: {val(sf.get('geburtsdatum'))}")
    lines.append(f"- Adresse/PLZ/Ort: {val(sf.get('adresse'))} / {val(sf.get('plz'))}")
    lines.append(f"- E-Mail: {val(sf.get('email'))}")
    lines.append(f"- Telefon: {val(sf.get('telefon'))}")
    lines.append(f"- Familienstand: {val(sf.get('familienstand'))}")
    lines.append(f"- Haushalt: {val(sf.get('haushalt'))}")
    lines.append(f"- Einkommen netto: {val(sf.get('einkommen_netto'))}")
    lines.append(f"- Beruf/Status: {val(sf.get('beruf_status'))}")
    lines.append(f"- Erstes Beratungsthema: {val(sf.get('erstes_thema'))}\n")
    def bl(name: str, d: Dict[str, Any]):
        if not d: return
        lines.append(f"{name}:")
        for k, v in d.items():
            if v: lines.append(f"- {k.replace('_',' ').title()}: {v}")
        lines.append("")
    bl("Privatkredit", th.get("privatkredit", {}))
    bl("Baufinanzierung", th.get("baufinanzierung", {}))
    bl("Versicherung", th.get("versicherung", {}))
    bl("Strom & Gas", th.get("strom_gas", {}))
    bl("Kommunikation", th.get("kommunikation", {}))
    bl("Bausparen", th.get("bausparen", {}))
    bl("Geldanlage/Altersvorsorge", th.get("anlage_vorsorge", {}))
    bl("Konto", th.get("konto", {}))
    bl("Reise", th.get("reise", {}))
    lines.append("Chat-Auszug (letzte Nachrichten):")
    for m in tail_chat[-8:]:
        role = m.get("role"); content = (m.get("content") or "").strip()
        content = content if len(content) <= 500 else content[:497] + "…"
        lines.append(f"- {role}: {content}")
    return "\n".join(lines)

# ---------- Endpunkte ----------
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

    # Übermittlung nur bei Consent (UI oder Chat) + Kontakt
    try:
        msgs = [m.model_dump() for m in req.messages]
        text_all = "\n".join([m.get("content") or "" for m in msgs])
        consent_chat = detect_consent(msgs)
        consent_ui = bool(getattr(req, "lead_opt_in", False))  # UI-Checkbox „Daten übermitteln gemäß Datenschutzerklärung“
        consent = consent_ui or consent_chat

        ids = _find_email_phone(text_all)
        email_for_hs = req.email or ids.get("email")
        phone_for_hs = ids.get("phone")

        print(f"[CONSENT] ui={consent_ui} chat={consent_chat} -> {consent} | email={email_for_hs} | phone={phone_for_hs}", file=sys.stdout, flush=True)

        if consent and (email_for_hs or phone_for_hs):
            dossier = build_customer_dossier(msgs)
            note_text = render_note(dossier, msgs)
            contact_id = await hubspot_client.upsert_contact(email_for_hs, None, None, phone_for_hs)
            print(f"[HUBSPOT] upsert_contact -> {contact_id}", file=sys.stdout, flush=True)
            if contact_id:
                await hubspot_client.add_note_to_contact(contact_id, note_text)
                print("[HUBSPOT] add_note_to_contact -> OK", file=sys.stdout, flush=True)
    except Exception as e:
        print(f"[HUBSPOT][ERROR] {e}", file=sys.stdout, flush=True)

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
