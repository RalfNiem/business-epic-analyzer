import sys
import os
import json
from pydantic import ValidationError

# Pfad-Setup: Füge 'src' zum Suchpfad hinzu, damit Module gefunden werden
sys.path.insert(0, os.path.abspath('src'))

from utils.dna_bot_client import DnaBotClient
from utils.business_impact_api import AIResponse
from utils.json_parser import LLMJsonParser  # <--- NEU: Import des Parsers

def test_pydantic_integration():
    print("--- Starte Test: DNA Bot mit Pydantic Modellen (mit Parser) ---\n")

    # 1. Client & Parser initialisieren
    try:
        client = DnaBotClient()
        parser = LLMJsonParser() # <--- NEU: Parser Instanz
    except ValueError as e:
        print(f"Fehler bei der Initialisierung (Fehlen Env-Vars?): {e}")
        return

    # 2. Das JSON-Schema aus dem Pydantic-Modell extrahieren
    json_schema = json.dumps(AIResponse.model_json_schema(), indent=2)

    # 3. Test-Szenario definieren
    test_description = (
        "Wir müssen das Legacy-System abschalten, da der Support ausläuft. "
        "Dadurch sparen wir 50k Lizenzkosten pro Jahr. Es ist kritisch für die Security-Compliance "
        "und muss bis Ende Q3 fertig sein."
    )

    system_prompt = f"""
    Du bist ein Business Analyst. Extrahiere den Business Value aus dem Text.
    
    WICHTIG: Deine Antwort MUSS ein valides JSON-Objekt sein, das exakt diesem Schema entspricht:
    {json_schema}
    """

    print(f"Sende Anfrage für Text: '{test_description[:50]}...'\n")

    # 4. Abfrage an den DNA Bot
    try:
        result = client.completion(
            model_name="Mistral-Small-3.2-24B-Instruct-2506", 
            user_prompt=test_description,
            system_prompt=system_prompt,
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        
        raw_text = result['text']
        print(f"Rohe Antwort vom LLM:\n{raw_text}\n")
        print("-" * 30)

        # 5. BEREINIGUNG: Nutze den LLMJsonParser
        # Dies entfernt Markdown-Blöcke (```json ... ```) und repariert kleine Syntaxfehler
        print("Bereinige Antwort mit LLMJsonParser...")
        parsed_dict = parser.extract_and_parse_json(raw_text)
        
        if not parsed_dict:
            print("❌ FEHLER: Parser konnte kein valides JSON extrahieren.")
            return

        print("JSON erfolgreich extrahiert.")

        # 6. Validierung gegen Pydantic
        # Wir validieren nun das Python-Dict, nicht den rohen String!
        print("Versuche Pydantic-Validierung...")
        parsed_obj = AIResponse.model_validate(parsed_dict)
        
        print("\n✅ ERFOLG! Das Extrahierte JSON entspricht dem Pydantic-Modell.")
        print("=" * 30)
        print(f"Cleaned Description: {parsed_obj.cleaned_description}")
        print(f"Impact Scale:        {parsed_obj.business_value.business_impact.scale}")
        print(f"Cost Saving:         {parsed_obj.business_value.business_impact.cost_saving}")
        print(f"Time Criticality:    {parsed_obj.business_value.time_criticality.time}")
        print("=" * 30)

    except ValidationError as e:
        print(f"\n❌ VALIDIERUNGSFEHLER: Das JSON passt nicht zum Schema.\n{e}")
    except Exception as e:
        print(f"\n❌ UNERWARTETER FEHLER: {e}")

if __name__ == "__main__":
    test_pydantic_integration()