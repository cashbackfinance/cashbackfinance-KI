from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from typing import Dict
from models import ChatRequest, ChatResponse, ChatMessage, LeadRequest, LeadResponse
from settings import Settings
from middleware import attach_cors
from services.openai_client import chat_completion
from services import hubspot_client
import asyncio

app = FastAPI(title="Cashback Finance API", version="1.2.0")
settings = Settings()
attach_cors(app, settings)

# --- Stil-Guide & Wissensbasis ------------------------------------------------

STYLE_GUIDE = (
    "Antwortstil (immer): "
    "1) Kurzantwort: eine Zeile mit der Lösung – positiv formuliert (z. B. 'Ja – …'). "
    "2) Was bedeutet das? 2–4 Sätze Erklärung. "
    "3) Voraussetzungen & typische Konditionen als Liste. "
    "4) Nächste Schritte: 2–3 konkrete Aktionen. "
    "5) Optional: Beispielrechnung/Orientierungswerte. "
    "Wenn Daten fehlen: gezielt max. 3 Kernparameter abfragen. "
    "Am Ende dezent Kontaktmöglichkeit anbieten. "
    "Keine Widersprüche (kein 'erst nein, dann ja'). "
    "Bei Zahlen immer 'unverbindlich, markt- und bonitätsabhängig' betonen."
)

KNOW_FORWARD = """
WISSEN: Forward-Darlehen (Zinssicherung für Anschlussfinanzierung)
- Rechtsgrundlagen: BGB §§ 488 ff., 491 ff.; Wohnimmobilienkreditrichtlinie; § 34i GewO; PAngV.
- Zweck: Heute Zinssatz für künftige Anschlussfinanzierung festschreiben (Vorlaufzeit typ. 12–60 Monate).
- Kosten: Forward-Aufschlag je Vorlaufmonat, grob 0,01–0,03 %-Punkte/Monat (marktabhängig).
- Voraussetzungen: Restschuld & Ablaufdatum der aktuellen Zinsbindung, Beleihungsauslauf, Bonität.
- Alternativen: Bauspar-Kombination, Prolongation bei der Hausbank, variables Darlehen mit späterer Fixierung.
- Risiken: Fallen die Zinsen, kann der gezahlte Aufschlag nachteilig sein; steigen sie, ist der Schutz vorteilhaft.
- Praxis: Angebote ca. 6–12 Monate vor Fälligkeit vergleichen; ab ~36 Monaten Vorlauf steigen Aufschläge merklich.
"""

KNOW_BAUFI = """
WISSEN: Baufinanzierung
- Rechtsgrundlagen: BGB §§ 488 ff., 491 ff. (Verbraucherdarlehen), Wohnimmobilienkreditrichtlinie, § 34i GewO, PAngV.
- Eckpunkte: Annuitätendarlehen mit Zinsbindung 5–30 Jahre; Forward-Darlehen zur Zinssicherung bis ~60 Monate im Voraus.
- Pflichtangaben: Effektivzins & Gesamtkosten, Widerrufsrecht (14 Tage).
- Förderung: KfW, Landesbanken, regionale Programme.
- Verbraucherschutz: Hinweis auf Vorfälligkeitsentschädigung; Beratungsdokumentationspflicht.
- Strategie: Lösungsorientiert; Konditionen beispielhaft und bonitätsabhängig kennzeichnen.
"""

KNOW_PRIVATKREDIT = """
WISSEN: Privatkredite
- Rechtsgrundlagen: BGB §§ 488–505; § 34c GewO; PAngV.
- Typisch: 1.000–80.000 €, Laufzeit 12–120 Monate; Effektivzins abhängig von Bonität, Zweck, Laufzeit.
- Widerruf: 14 Tage; Sondertilgung häufig möglich.
- Verbraucherschutz: Keine Versprechen „ohne Schufa“; Vergleich & Bonitätsprüfung transparent machen.
- Strategie: Bandbreiten nennen, Umschuldung als Option prüfen.
"""

KNOW_BAUSPAR = """
WISSEN: Bausparvertrag
- Rechtsgrundlagen: BauSparkG; VAG/BaFin; Fördergesetze (Wohnungsbauprämie, Arbeitnehmersparzulage, Riester).
- Aufbau: Sparphase (Guthabenzins) + Darlehensphase (fester Sollzins).
- Förderung: WOP, ANSpZ, ggf. Riester.
- Verbraucherschutz: Langfristige Bindung, Abschlussgebühr/Kosten transparent machen.
- Strategie: Geeignet für Zinssicherheit + Förderungen; eingeschränkte Flexibilität offen benennen.
"""

KNOW_VERSICHERUNG = """
WISSEN: Versicherungen
- Rechtsgrundlagen: VVG; § 34d GewO; VersVermV; IDD (EU).
- Sparten: Sach (Hausrat, Haftpflicht, Wohngebäude, Tier), KFZ, Personen (Leben/BU, Kranken).
- Pflichten: Beratungsdokumentation & Statusinformation; Transparenz zu Vergütung.
- Verbraucherschutz: Existenzielle Risiken zuerst; Pflichtversicherungen klar nennen (KFZ, Kranken).
- Strategie: Bedarfsgerecht; Beispielprämien nur orientierend; keine Produktgarantien.
"""

KNOW_GELDANLAGE = """
WISSEN: Geldanlage
- Rechtsgrundlagen: WpHG; § 34f GewO; KAGB; BaFin-Regelwerk.
- Produkte: Fonds, ETFs, Aktien, Renten.
- Pflicht: Geeignetheitsprüfung (Kenntnisse, Ziele, Risiko).
- Verbraucherschutz: Kapitalverlustrisiko, keine Garantien.
- Strategie: Diversifikation erklären; Szenarien/Beispiele als Orientierung, nicht als Zusage.
"""

KNOW_ALTERSVORSORGE = """
WISSEN: Altersvorsorge
- Rechtsgrundlagen: EStG (§10 Rürup, §10a Riester, §3 Nr.63 bAV); VVG; § 34d/f GewO; VersVermV.
- Produkte: Rürup, Riester, bAV, private Renten.
- Förderung: Steuerabzug (Rürup), Zulagen (Riester), Steuer-/SV-Ersparnis (bAV).
- Verbraucherschutz: Kosten offenlegen; Hinweis auf steuerliche Einordnung (Steuerberater).
- Strategie: Positiv („Ja – mit Förderung…“), einfache Beispielrechnungen, Daten abfragen (Einkommen/Horizont).
"""

KNOW_KOMMUNIKATION = """
WISSEN: Kommunikation (Internet/Mobilfunk/Festnetz)
- Rechtsgrundlagen: TKG; BGB §309 Nr.9 (Laufzeiten); §312k BGB (Kündigungsbutton).
- Verträge: DSL, Kabel, Glasfaser, Mobilfunk; i. d. R. max. 24 Monate Laufzeit.
- Provision: ~60 € pro Vertrag (Richtwert).
- Verbraucherschutz: Nach Mindestlaufzeit Kündigungsfrist max. 1 Monat; transparente Preisangaben.
- Strategie: Wechsel spart oft 20–30 €/Monat; Kündigungsfristen & Vergleich betonen.
"""

KNOW_STROM_GAS = """
WISSEN: Strom & Gas
- Rechtsgrundlagen: EnWG; BGB §309 Nr.9; PAngV.
- Ersparnis: Wechsel typ. 200–500 €/Jahr (orientierend).
- Provision: ~26 € je Vertrag (Richtwert).
- Verbraucherschutz: Grundversorgung jederzeit kündbar (2 Wochen); Bonusmodelle prüfen.
- Strategie: Ersparnis + Vertragsdetails erklären; vor Bonusfallen warnen.
"""

KNOW_KONTO = """
WISSEN: Konto
- Rechtsgrundlagen: KWG; PSD2 (EU); BGB §§675 ff. (Zahlungsdienste).
- Girokonto: oft kostenlos mit Bedingungen (z. B. Geldeingang); Provision ~40 € (Richtwert).
- Verbraucherschutz: Preis-Leistungsverzeichnis; jederzeit kündbar.
- Strategie: Vorteile (Kosten, Bonus) + Risiken (Dispozinsen) benennen; Vergleich anbieten.
"""

KNOW_REISE = """
WISSEN: Reise (Check24 Pro)
- Rechtsgrundlagen: BGB §§651a ff. (Pauschalreise); VVG bei Reiseversicherungen.
- Cashback: ca. 4 % der Reisesumme (Richtwert) über Check24.
- Verbraucherschutz: Storno-/Umbuchungsbedingungen; Insolvenzabsicherung; Widerrufsrecht eingeschränkt.
- Strategie: Sparpotenzial + Cashback hervorheben; Reiserücktritt/Versicherung prüfen.
"""

ALL_KNOWLEDGE = (
    KNOW_FORWARD
    + "\n" + KNOW_BAUFI
    + "\n" + KNOW_PRIVATKREDIT
    + "\n" + KNOW_BAUSPAR
    + "\n" + KNOW_VERSICHERUNG
    + "\n" + KNOW_GELDANLAGE
    + "\n" + KNOW_ALTERSVORSORGE
    + "\n" + KNOW_KOMMUNIKATION
    + "\n" + KNOW_STROM_GAS
    + "\n" + KNOW_KONTO
    + "\n" + KNOW_REISE
)

def build_system_prompt() -> str:
    base = settings.SYSTEM_PROMPT.strip() if settings.SYSTEM_PROMPT else (
        "Du bist die KI von Cashback Finance (deutsch), freundlich, klar, lösungsorientiert und auf Leadgenerierung ausgerichtet."
    )
    return f"{base}\n\n{STYLE_GUIDE}\n\n{ALL_KNOWLEDGE}".strip()

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
