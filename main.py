from fastapi import FastAPI, HTTPException
from models import ChatRequest, ChatResponse, ChatMessage, LeadRequest, LeadResponse
from settings import Settings
from middleware import attach_cors
from services.openai_client import chat_completion
from services import hubspot_client
import asyncio

app = FastAPI(title="Cashback Finance API", version="1.4.0")
settings = Settings()
attach_cors(app, settings)

# --- Stil-Guide & Wissensbasis ------------------------------------------------
# Ziel: Du-Anrede, lösungsorientiert, Mehrwert von Cashback Finance dezent einbetten,
# klare Lead-Führung (Datenaufnahme -> Globale Selbstauskunft ODER Rückruf/Termin),
# keine DIY-/Portal-Verweise, verbraucherschutzkonform, mit Beispielrechnungen (Microsaving + Cashback).

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
    "führe eine kurze, klar gekennzeichnete Beispielrechnung durch: "
    "- Setze eine kleine plausible Annahme (als Beispiel markieren). "
    "- Nenne 1) monatliche Ersparnis (Microsaving), 2) jährlichen Effekt, 3) ggf. einmaligen Cashback-Betrag. "
    "- Formuliere so, dass der konkrete Nutzen erkennbar ist und in die nächsten Schritte überleitet."
)

# --- Wissensblöcke (rechtlich fundierte Kurz-Konzepte) ------------------------

KNOW_FORWARD = """
WISSEN: Forward-Darlehen (Zinssicherung für Anschlussfinanzierung)
- Rechtsgrundlagen: BGB §§ 488 ff., 491 ff.; Wohnimmobilienkreditrichtlinie; § 34i GewO; PAngV.
- Zweck: Heute Zinssatz für künftige Anschlussfinanzierung festschreiben (Vorlaufzeit typ. 12–60 Monate).
- Kosten: Forward-Aufschlag je Vorlaufmonat, grob 0,01–0,03 %-Punkte/Monat (marktabhängig).
- Voraussetzungen: Restschuld & Ablaufdatum der aktuellen Zinsbindung, Beleihungsauslauf, Bonität.
- Alternativen: Bauspar-Kombination, Prolongation Hausbank, variables Darlehen mit späterer Fixierung.
- Risiken: Fallen Zinsen, kann der Aufschlag nachteilig sein; steigen sie, ist der Schutz vorteilhaft.
- Praxis: Angebote ~6–12 Monate vor Fälligkeit vergleichen; ab ~36 Monaten Vorlauf steigen Aufschläge merklich.
"""

KNOW_BAUFI = """
WISSEN: Baufinanzierung
- Rechtsgrundlagen: BGB §§ 488 ff., 491 ff. (Verbraucherdarlehen), Wohnimmobilienkreditrichtlinie, § 34i GewO, PAngV.
- Eckpunkte: Annuitätendarlehen mit Zinsbindung 5–30 Jahre; Forward-Darlehen zur Zinssicherung bis ~60 Monate.
- Pflichtangaben: Effektivzins & Gesamtkosten; Widerrufsrecht 14 Tage.
- Förderung: KfW, Landesbanken, regionale Programme.
- Verbraucherschutz: Hinweis auf Vorfälligkeitsentschädigung; Beratungsdokumentationspflicht.
"""

KNOW_PRIVATKREDIT = """
WISSEN: Privatkredite
- Rechtsgrundlagen: BGB §§ 488–505; § 34c GewO; PAngV.
- Typisch: 1.000–80.000 €, Laufzeit 12–120 Monate; Effektivzins abhängig von Bonität, Zweck, Laufzeit.
- Widerruf: 14 Tage; Sondertilgung häufig möglich.
- Verbraucherschutz: Keine Versprechen 'ohne Schufa'; transparente Bonitätsprüfung & Vergleich.
"""

KNOW_BAUSPAR = """
WISSEN: Bausparvertrag
- Rechtsgrundlagen: BauSparkG; VAG/BaFin; Fördergesetze (Wohnungsbauprämie, Arbeitnehmersparzulage, Riester).
- Aufbau: Sparphase (Guthabenzins) + Darlehensphase (fester Sollzins).
- Förderung: WOP, ANSpZ, ggf. Riester.
- Verbraucherschutz: Langfristige Bindung, Abschlussgebühr/Kosten transparent machen.
"""

KNOW_VERSICHERUNG = """
WISSEN: Versicherungen
- Rechtsgrundlagen: VVG; § 34d GewO; VersVermV; IDD (EU).
- Sparten: Sach (Hausrat, Haftpflicht, Wohngebäude, Tier), KFZ, Personen (Leben/BU, Kranken).
- Pflichten: Beratungsdokumentation & Statusinformation; Vergütungstransparenz.
- Verbraucherschutz: Existenzielle Risiken zuerst; Pflichtversicherungen (KFZ, Kranken) klar benennen.
"""

KNOW_GELDANLAGE = """
WISSEN: Geldanlage
- Rechtsgrundlagen: WpHG; § 34f GewO; KAGB; BaFin-Regelwerk.
- Produkte: Fonds, ETFs, Aktien, Renten.
- Pflicht: Geeignetheitsprüfung (Kenntnisse, Ziele, Risikoprofil).
- Verbraucherschutz: Kapitalverlustrisiko, keine Garantien.
"""

KNOW_ALTERSVORSORGE = """
WISSEN: Altersvorsorge
- Rechtsgrundlagen: EStG (§10 Rürup, §10a Riester, §3 Nr.63 bAV); VVG; § 34d/f GewO; VersVermV.
- Produkte: Rürup, Riester, bAV, private Renten.
- Förderung: Steuerabzug (Rürup), Zulagen (Riester), Steuer-/SV-Ersparnis (bAV).
- Verbraucherschutz: Kosten offenlegen; steuerliche Einordnung -> Steuerberater.
"""

KNOW_KOMMUNIKATION = """
WISSEN: Kommunikation (Internet/Mobilfunk/Festnetz)
- Rechtsgrundlagen: TKG; BGB §309 Nr.9 (Laufzeiten); §312k BGB (Kündigungsbutton).
- Verträge: DSL, Kabel, Glasfaser, Mobilfunk; idR max. 24 Monate.
- Verbraucherschutz: Nach Mindestlaufzeit Kündigungsfrist max. 1 Monat; transparente Preisangaben.
"""

KNOW_STROM_GAS = """
WISSEN: Strom & Gas
- Rechtsgrundlagen: EnWG; BGB §309 Nr.9; PAngV.
- Ersparnis: Wechsel typ. 200–500 €/Jahr (orientierend).
- Verbraucherschutz: Grundversorgung 2 Wochen kündbar; Bonus-/Laufzeitbedingungen prüfen.
"""

KNOW_KONTO = """
WISSEN: Konto
- Rechtsgrundlagen: KWG; PSD2 (EU); BGB §§675 ff. (Zahlungsdienste).
- Girokonto: oft kostenlos mit Bedingungen (z. B. Geldeingang).
- Verbraucherschutz: Preis-Leistungsverzeichnis; jederzeit kündbar; Dispozinsen beachten.
"""

KNOW_REISE = """
WISSEN: Reise (Check24 Pro)
- Rechtsgrundlagen: BGB §§651a ff. (Pauschalreise); VVG bei Reiseversicherungen.
- Cashback: ca. 4 % Reisesumme (Richtwert) über Check24.
- Verbraucherschutz: Storno-/Umbuchung, Insolvenzabsicherung; Widerrufsrecht eingeschränkt.
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
        "Du bist die KI von Cashback Finance. Sprich Nutzer konsequent in der Du-Form an, "
        "freundlich, respektvoll und vertrauensvoll. "
        "Dein Ziel: klar informieren und verlässlich zur Zusammenarbeit mit Cashback Finance führen."
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

    # Optional: Lead-Erzeugung/Notiz in HubSpot (nur bei Einwilligung + E-Mail)
    if req.lead_opt_in and req.email:
        try:
            contact_id = await hubspot_client.upsert_contact(req.email)
            last_q = req.messages[-1].content if req.messages else ""
            note_text = f"Lead aus Website-Chat. Letzte Nutzerfrage:\n\n{last_q}"
            if contact_id:
                await hubspot_client.add_note_to_contact(contact_id, note_text)
        except Exception:
            # Chat darf nicht fehlschlagen, wenn HubSpot temporär nicht funktioniert
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
