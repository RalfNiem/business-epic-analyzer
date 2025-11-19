"""
Orchestriert einen KI-gestützten Workflow zur Qualitätsverbesserung von
Business Epic Beschreibungen und Business Value Extraktionen.

Dieses Skript liest die Daten von Business Epics aus lokalen JSON-Dateien im
Verzeichnis `data/jira_issues`. Es **durchsucht das Verzeichnis und verarbeitet
standardmäßig nur das *erste* gefundene Issue vom Typ "Business Epic"**,
basierend auf einem internen Limit (`max_epics_to_process`, standardmäßig auf 1 gesetzt).
Es akzeptiert keine spezifischen Epic-Keys über die Kommandozeile und liest
keine Listen aus Dateien ein.

Für das ausgewählte Epic führt es die folgenden Schritte aus:
1.  Es sendet die ursprüngliche, oft unstrukturierte 'description' an ein
    Azure OpenAI-Sprachmodell (LLM).
2.  Das LLM generiert daraus eine bereinigte, prägnante neue Beschreibung und
    extrahiert einen strukturierten 'business_value' im JSON-Format. Die
    Antwort wird mithilfe der 'instructor'-Bibliothek direkt in ein
    Pydantic-Modell validiert.
3.  Anschließend wird ein zweiter Prompt an das LLM gesendet, der die alte
    und die neue Version des Business Value enthält. Das LLM vergleicht beide
    Versionen und bewertet die Qualität, den Informationsgewinn und den
    Informationsverlust.
4.  Alle Ergebnisse (neue Beschreibung, neuer Business Value, KI-Bewertung)
    werden für das verarbeitete Epic in einer zentralen JSONL-Datei
    ('data/comparison_results.jsonl') gespeichert.

Usage:
    Stellen Sie sicher, dass die Azure AI Umgebungsvariablen gesetzt sind
    und die Jira-Issue-Daten im Verzeichnis `data/jira_issues` vorhanden sind.
    Führen Sie das Skript dann direkt aus dem `src`-Verzeichnis aus:

    ```bash
    python run_comparison.py
    ```
    Das Skript verarbeitet standardmäßig nur das erste gefundene Business Epic
    und speichert die Ergebnisse in `data/comparison_results.jsonl`. Um mehr
    Epics zu verarbeiten, muss die Variable `max_epics_to_process` im Skript
    angepasst werden.
"""
import os
import json
import sys
import instructor  # ### HINZUGEFÜGT ###
from typing import Dict, Any

# Fügen Sie das Projekt-Root zum Suchpfad hinzu, falls notwendig
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Importieren der notwendigen Module und Klassen aus Ihrem Projekt
from utils.business_impact_api import process_description
from utils.azure_ai_client import AzureAIClient
from utils.token_usage_class import TokenUsage
from utils.prompt_loader import load_prompt_template
from utils.config import (
    JIRA_ISSUES_DIR,
    LLM_MODEL_BUSINESS_VALUE,
    LLM_MODEL_SUMMARY,
    TOKEN_LOG_FILE,
    DATA_DIR
)

def run_comparison_workflow():
    """
    Orchestriert den gesamten Prozess: Einlesen, Verarbeiten, Vergleichen und Speichern.
    """
    print("Starte den Workflow zum Vergleich der Business-Value-Extraktion...")

    # --- 1. Initialisierung ---
    azure_client = AzureAIClient(system_prompt="Du bist ein präziser Daten-Analyse-Assistent.")

    # ### HINZUGEFÜGT: Patche den Client, um Pydantic-Modelle zu unterstützen ###
    instructor.patch(azure_client)

    token_tracker = TokenUsage(log_file_path=TOKEN_LOG_FILE)
    comparison_prompt_template = load_prompt_template("comparison_prompt.yaml", "comparison_prompt_template")

    try:
        all_files = [f for f in os.listdir(JIRA_ISSUES_DIR) if f.endswith('.json')]
        print(f"Gefunden: {len(all_files)} JSON-Dateien im Verzeichnis.")
    except FileNotFoundError:
        print(f"FEHLER: Das Verzeichnis '{JIRA_ISSUES_DIR}' wurde nicht gefunden.")
        return

    all_results = []
    processed_epic_count = 0
    max_epics_to_process = 1

    # --- 2. Haupt-Verarbeitungsschleife ---
    for filename in all_files:
        if processed_epic_count >= max_epics_to_process:
            print(f"\nLimit von {max_epics_to_process} Business Epics erreicht. Beende die Verarbeitung.")
            break
        filename = "BEMABU-1872.json"
        epic_key = filename.replace('.json', '')

        try:
            filepath = os.path.join(JIRA_ISSUES_DIR, filename)
            with open(filepath, 'r', encoding='utf-8') as f:
                issue_data = json.load(f)

            if issue_data.get("issue_type") != "Business Epic":
                #print(f"Info: {epic_key} ist kein Business Epic (Typ: {issue_data.get('issue_type')}). Wird übersprungen.")
                continue

            print(f"\n--- Verarbeite Business Epic: {epic_key} ({processed_epic_count + 1}/{max_epics_to_process}) ---")

            original_description = issue_data.get("description", "")
            old_business_value = issue_data.get("business_value", {})

            if not original_description:
                print(f"WARNUNG: Keine 'description' in {filename} gefunden. Überspringe.")
                continue

            # --- 3. Generiere neue Daten mit der business_impact_api (jetzt mit Pydantic) ---
            print("Generiere neue Description und neuen Business Value...")
            processed_data = process_description(
                description_text=original_description,
                model=LLM_MODEL_BUSINESS_VALUE,
                token_tracker=token_tracker,
                azure_client=azure_client
            )
            new_description = processed_data["description"]
            new_business_value = processed_data["business_value"]

            # --- 4. Vergleiche den alten und neuen Business Value ---
            print("Vergleiche alten und neuen Business Value mit der KI...")
            old_bv_str = json.dumps(old_business_value, indent=2, ensure_ascii=False)
            new_bv_str = json.dumps(new_business_value, indent=2, ensure_ascii=False)

            comparison_prompt = comparison_prompt_template.format(
                description=original_description,
                old_business_value=old_bv_str,
                new_business_value=new_bv_str
            )

            # Hier könnten wir den Vergleichs-Prompt ebenfalls auf Pydantic umstellen,
            # aber zur Vereinfachung belassen wir es vorerst bei der bisherigen Methode.
            comparison_response = azure_client.chat.completions.create(
                model=LLM_MODEL_SUMMARY,
                messages=[{"role": "user", "content": comparison_prompt}],
                response_format={"type": "json_object"}
            )

            ai_assessment = json.loads(comparison_response.choices[0].message.content)

            # --- 5. Speichere die Ergebnisse für dieses Epic ---
            result_for_epic = {
                "epic_key": epic_key,
                "new_description": new_description,
                "new_business_value": new_business_value,
                "ai_assessment": ai_assessment
            }
            all_results.append(result_for_epic)
            processed_epic_count += 1
            print(f"Verarbeitung für {epic_key} abgeschlossen.")

        except Exception as e:
            print(f"FEHLER bei der Verarbeitung von {filename}: {e}")

    # --- 6. Speichere alle Ergebnisse in einer Datei ---
    output_filename = os.path.join(DATA_DIR, "comparison_results.jsonl")
    print(f"\nSpeichere alle {len(all_results)} Ergebnisse in '{output_filename}'...")
    try:
        with open(output_filename, 'w', encoding='utf-8') as f:
            for entry in all_results:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        print("Speichern erfolgreich.")
    except Exception as e:
        print(f"FEHLER beim Speichern der Ergebnisse: {e}")

if __name__ == "__main__":
    run_comparison_workflow()
