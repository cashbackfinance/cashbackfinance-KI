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

app = FastAPI(title="Cashback Finance API", version="2.5.0")
settings = Settings()
attach_cors(app, settings)

# =========================
# 1) System-/Style-Prompt
# =========================

MASTER_FLOW = (
    "Arbeitsweise (Kundenakte): "
    "1) Startformular (nur Pflichtangaben): Name/Alias, Geburtsdatum, Adresse (PLZ/Ort reicht), E-Mail, Telefon, "
    "   Familienstand, Haushaltsgröße (Erwachsene/Kinder), Einkommen netto (ca.), Beruf/Status, Erstes Beratungsthema. "
    "2) Themenwahl: Privatkredit, Baufinanzierung, Versicherung, Strom & Gas, Kommunikation, Bausparen, "
    "   Geldanlage/Altersvorsorge, Konto, Reise. "
    "3) Datentiefe je Thema: nur die Felder, die für Berechnung/Angebot nötig sind (gezielt, max. 3 Fragen pro Turn). "
    "4) Beispielrechnung mit Hinweis: 'Dies ist eine unverbindliche Beispielrechnung.' "
    "5) Abschlussfrage/Lead: 'Möchtest du, dass wir dich kontaktieren und es fix für dich umsetzen?' "
    "6) Fortlaufend: Neue Infos ergänzen die Kundenakte Schritt für Schritt. "
)

STYLE_GUIDE = (
    "Stil/Regeln: "
    "Du-Ansprache. Erst kurz fachlich anteasern (Mehrwert: Einsparungen, Microsaving, bis zu 20 % Cashback auf Provisionen), "
    "dann Zustimmung einholen, dann Kontaktdaten abfragen. "
    "Keine DIY-/Vergleichsportal-Empfehlungen. Keine Garantien oder Zinszusagen. Zahlen stets 'unverbindlich, markt- und bonitätsabhängig'. "
    "Wichtig: Wenn Nutzer zugestimmt hat und Kontaktdaten gegeben sind, bestätige klar: "
    "'Ich habe deine Daten sicher an Cashback Finance übermittelt.' Sage niemals, dass du das nicht kannst. "
)

DOMAIN_KNOW = (
    "Wissen (Kurz): Baufinanzierung: Annuitätendarlehen, KfW, Effektivzins; Forward: Zinsabsicherung; BGB §§488/491, §34i GewO, PAngV. "
    "Privatkredit/Umschuldung: BGB §§488–505. Versicherungen: VVG, §34d GewO (Beratungsdoku/IDD). "
    "Strom/Gas: EnWG; Kommunikation: TKG/§312k (Kündigungsbutton); Konto: PSD2. "
)

def build_system_prompt() -> str:
    base = settings.SYSTEM_PROMPT.strip() if settings.SYSTEM_PROMPT else (
        "Du bist die KI von Cashback Finance. Sprich konsequent in der Du-Form, hilfsbereit, klar, seriös. "
        "Ziel: informieren, entlasten und verlässlich zur Zusammenarbeit mit Cashback Finance führen."
    )
    return f"{base}\n\n{MASTER_FLOW}\n{STYLE_GUIDE}\n{DOMAIN_KNOW}".strip()

# =========================
# 2) Extraction / Consent
# =========================

EMAIL_RE   = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE_RE   = re.compile(r"(?:(?:\+|00)\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?)\d[\d\s-]{5,}")
EURO_RE    = re.compile(r"(?<!\d)(\d{1,3}(?:[.\s]\d{3})*|\d+)(?:[.,]\d+)?\s*(?:€|eur|euro)", re.I)
PCT_RE     = re.compile(r"(\d+[.,]?\d*)\s*%")
DATE_RE    = re.compile(r"(\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{2,4})")
PLZ_RE     = re.compile(r"\b(\d{5})\b")

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
NEGATION_NEAR   = re.compile(r"\b(nicht|kein|keine|nein|stop|stopp|abbrechen)\b", re.I)
ASSISTANT_ASKS  = re.compile(r"(übermitteln|weiterleiten|weitergeben|bündeln|an\s+cashback\s+finance)", re.I)
USER_AFFIRM     = re.compile(r"\b(ja|ok|okay|passt|einverstanden|mach|bitte|los)\b", re.I)

def _msgs(messages: List[Dict[str, Any]], only_user: bool = False, last_n: int = 30) -> List[Dict[str, Any]]:
    msgs = messages[-last_n:]
    if only_user:
        msgs = [m for m in msgs if m.get("role") == "user"]
    return msgs

def detect_consent(messages: List[Dict[str, Any]]) -> bool:
    user_msgs = _msgs(messages, only_user=True, last_n=20)
    text_all = "\n".join([m.get("content") or "" for m in user_msgs])
    if CONSENT_EXPLICIT.search(text_all):
        return True
    if any(CONSENT_KEYWORD_OK.search(m.get("content") or "") for m in user_msgs):
        if not any(NEGATION_NEAR.search(m.get("content") or "") for m in user_msgs):
            return True
    for m in user_msgs:
        t = m.get("content") or ""
        if CONSENT_INTENT_SOFT.search(t) and not NEGATION_NEAR.search(t):
            return True
    # Kontext: Assistent fragt → User bejaht
    last_msgs = messages[-6:]
    for i in range(len(last_msgs)-1):
        a, u = last_msgs[i], last_msgs[i+1]
        if a.get("role") == "assistant" and u.get("role") == "user":
            if ASSISTANT_ASKS.search(a.get("content") or "") and USER_AFFIRM.search(u.get("content") or ""):
                if not NEGATION_NEAR.search(u.get("content") or ""):
                    return True
    return False

# =========================
# 3) Kundenakte-Parser
# =========================

def _find_email_phone(text: str) -> Dict[str, str]:
    out = {}
    emails = EMAIL_RE.findall(text)
    if emails: out["email"] = emails[-1]
    ph = PHONE_RE.search(text)
    if ph: out["phone"] = ph.group(0)
    return out

def _extract_value_after(label: str, text: str) -> Optional[str]:
    # grob: "label: wert" / "label wert"
    pat = re.compile(rf"{label}\s*[:\-]?\s*(.+)", re.I)
    m = pat.search(text)
    if m:
        val = m.group(1).strip()
        # bis zum Zeilenende / trennzeichen
        val = val.split("\n")[0].strip()
        return val
    return None

def extract_startformular(full_text: str) -> Dict[str, Any]:
    sf: Dict[str, Any] = {}
    # Labels aus dem Masterprompt
    for lab, key in [
        ("Name", "name"), ("Alias", "alias"), ("Geburtsdatum", "geburtsdatum"),
        ("Adresse", "adresse"), ("PLZ", "plz"), ("Ort", "ort"),
        ("E-Mail", "email"), ("Telefon", "telefon"), ("Familienstand", "familienstand"),
        ("Haushaltsgröße", "haushalt"), ("Einkommen", "einkommen_netto"),
        ("Beruf", "beruf_status"), ("Erstes Beratungsthema", "erstes_thema")
    ]:
        val = _extract_value_after(lab, full_text)
        if val: sf[key] = val
    # Fallback Email/Telefon
    aux = _find_email_phone(full_text)
    if "email" not in sf and "email" in aux: sf["email"] = aux["email"]
    if "telefon" not in sf and "phone" in aux: sf["telefon"] = aux["phone"]
    # PLZ heuristik
    if "plz" not in sf:
        m = PLZ_RE.search(full_text)
        if m: sf["plz"] = m.group(1)
    return sf

def extract_topics(full_text: str) -> Dict[str, Any]:
    topics: Dict[str, Any] = {}

    # 1) Privatkredit
    if re.search(r"\b(privatkredit|kredit|umschuldung)\b", full_text, re.I):
        topics["privatkredit"] = {
            "summe": _extract_value_after("Darlehenssumme", full_text) or _extract_value_after("Gewünschte Darlehenssumme", full_text),
            "wunschrate_laufzeit": _extract_value_after("Wunschrate", full_text) or _extract_value_after("Laufzeit", full_text),
            "verwendungszweck": _extract_value_after("Verwendungszweck", full_text)
        }

    # 2) Baufinanzierung
    if re.search(r"\b(baufinanz|baukredit|immobilie|forward)\b", full_text, re.I):
        topics["baufinanzierung"] = {
            "objektwert": _extract_value_after("Objektwert", full_text),
            "darlehensbedarf": _extract_value_after("Darlehensbedarf", full_text),
            "eigenkapital": _extract_value_after("Eigenkapital", full_text),
            "zinsbindung": _extract_value_after("Zinsbindung", full_text)
        }

    # 3) Versicherung
    if re.search(r"\b(versicherung|haftpflicht|hausrat|wohngebäude|bu|pferde)\b", full_text, re.I):
        topics["versicherung"] = {
            "bestehend": _extract_value_after("Bestehende Versicherungen", full_text),
            "jahresbeitrag": _extract_value_after("Jahresbeitrag", full_text),
            "wechsel": _extract_value_after("Wechselbereitschaft", full_text)
        }

    # 4) Strom & Gas
    if re.search(r"\b(strom|gas|kwh|pv|photovoltaik)\b", full_text, re.I):
        topics["strom_gas"] = {
            "anbieter": _extract_value_after("Anbieter", full_text),
            "strom_kwh": _extract_value_after("Jahresverbrauch Strom", full_text),
            "gas_kwh": _extract_value_after("Jahresverbrauch Gas", full_text),
            "kosten_monat": _extract_value_after("Monatliche Kosten", full_text),
            "pv": "ja" if re.search(r"\b(pv|photovoltaik)\b", full_text, re.I) else None
        }

    # 5) Kommunikation
    if re.search(r"\b(mobilfunk|internet|festnetz|telekom|o2|vodafone|dsl|kabel)\b", full_text, re.I):
        topics["kommunikation"] = {
            "anzahl_vertraege": _extract_value_after("Anzahl Verträge", full_text),
            "anbieter": _extract_value_after("Anbieter", full_text),
            "kosten_gesamt": _extract_value_after("Monatliche Kosten", full_text),
            "laufzeiten": _extract_value_after("Vertragslaufzeiten", full_text)
        }

    # 6) Bausparen
    if re.search(r"\b(bauspar|wüstenrot|lbs)\b", full_text, re.I):
        topics["bausparen"] = {
            "vertragswert": _extract_value_after("Vertragswert", full_text) or _extract_value_after("Bausparsumme", full_text),
            "sparrate": _extract_value_after("Sparrate", full_text),
            "zweck": _extract_value_after("Ziel", full_text) or _extract_value_after("Zweck", full_text)
        }

    # 7) Geldanlage/Altersvorsorge
    if re.search(r"\b(geldanlage|vorsorge|etf|rente|riester|rürup|fonds|sparrate|anlagebetrag)\b", full_text, re.I):
        topics["anlage_vorsorge"] = {
            "einmalanlage": _extract_value_after("Anlagebetrag (einmalig)", full_text) or _extract_value_after("Einmalanlage", full_text),
            "sparrate": _extract_value_after("Monatliche Sparrate", full_text),
            "horizont": _extract_value_after("Zielhorizont", full_text),
            "risiko": _extract_value_after("Risikoneigung", full_text),
            "vorhanden": _extract_value_after("Altersvorsorgeformen", full_text)
        }

    # 8) Konto
    if re.search(r"\b(konto|konten|hausbank)\b", full_text, re.I):
        topics["konto"] = {
            "bestehende_konten": _extract_value_after("Bestehende Konten", full_text),
            "wechselinteresse": _extract_value_after("Wechselinteresse", full_text)
        }

    # 9) Reise
    if re.search(r"\b(reise|urlaub)\b", full_text, re.I):
        topics["reise"] = {
            "jahresbudget": _extract_value_after("Jahresbudget", full_text),
            "gewohnheiten": _extract_value_after("Reisegewohnheiten", full_text)
        }

    return topics

def build_customer_dossier(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Wir bauen die Akte aus dem Konversationstext
    full_text = "\n".join([f"{m.get('role')}: {m.get('content','')}" for m in messages])
    start = extract_startformular(full_text)
    topics = extract_topics(full_text)
    # zusätzliche Streu-Infos
    euros = [m.group(0) for m in EURO_RE.finditer(full_text)][:20]
    pcts  = [m.group(1) for m in PCT_RE.finditer(full_text)][:10]
    dates = [m.group(1) for m in DATE_RE.finditer(full_text)][:10]
    out: Dict[str, Any] = {
        "startformular": start,
        "themen": topics,
        "streu_infos": {
            "betraege": euros,
            "prozente": pcts,
            "daten": dates
        }
    }
    return out

def render_note(dossier: Dict[str, Any], tail_chat: List[Dict[str, Any]]) -> str:
    # Schöne, kompakte Notiz für HubSpot
    sf = dossier.get("startformular", {})
    th = dossier.get("themen", {})
    def val(x): return x if x else "-"
    lines = []
    lines.append("Kundenakte – Kurzprotokoll (automatisch aus Chat) – Cashback Finance")
    lines.append("")
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
    lines.append(f"- Erstes Beratungsthema: {val(sf.get('erstes_thema'))}")
    lines.append("")
    # Themen kompakt
    def bl(name: str, d: Dict[str, Any]):
        if not d: return
        lines.append(f"{name}:")
        for k, v in d.items():
            if v:
                lines.append(f"- {k.replace('_',' ').title()}: {v}")
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
    # Chat-Auszug
    lines.append("Chat-Auszug (letzte Nachrichten):")
    for m in tail_chat[-8:]:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        content = content if len(content) <= 500 else content[:497] + "…"
        lines.append(f"- {role}: {content}")
    return "\n".join(lines)

# =========================
# 4) Endpunkte
# =========================

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

    # --- Datentransfer zu HubSpot: sofort, wenn Consent + (E-Mail oder Telefon) vorhanden ---
    try:
        msgs = [m.model_dump() for m in req.messages]
        user_stream = [m for m in msgs if m.get("role") == "user"]
        text_all = "\n".join([m.get("content") or "" for m in msgs])

        consent = detect_consent(msgs)

        # E-Mail/Telefon aus Request oder Chat extrahieren
        email_for_hs = req.email
        phone_for_hs = None
        if not email_for_hs or not PHONE_RE.search(text_all):
            ids = _find_email_phone(text_all)
            email_for_hs = email_for_hs or ids.get("email")
            phone_for_hs = ids.get("phone")

        print(f"[CONSENT] detected={consent} | email={email_for_hs} | phone={phone_for_hs}", file=sys.stdout, flush=True)

        # Bedingung: Zustimmung + mindestens E-Mail (besser: E-Mail oder Telefon)
        if consent and (email_for_hs or phone_for_hs):
            # Kundenakte bauen und als Note transferieren
            dossier = build_customer_dossier(msgs)
            note_text = render_note(dossier, msgs)

            # Kontakt anlegen/aktualisieren
            contact_id = await hubspot_client.upsert_contact(email_for_hs, None, None, phone_for_hs)
            print(f"[HUBSPOT] upsert_contact -> {contact_id}", file=sys.stdout, flush=True)

            if contact_id:
                await hubspot_client.add_note_to_contact(contact_id, note_text)
                print("[HUBSPOT] add_note_to_contact -> OK", file=sys.stdout, flush=True)
    except Exception as e:
        print(f"[HUBSPOT][ERROR] {e}", file=sys.stdout, flush=True)
        # Chat darf nie scheitern

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
