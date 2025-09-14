"""
Lädt, kategorisiert und analysiert LLM-Bewertungen von Business Epics.

Dieses Skript führt einen zweistufigen Analyseprozess durch:
1.  **Kategorisierung:** Es lädt alle vorhandenen Analyse-JSON-Dateien aus dem
    `data/llm_analysis`-Verzeichnis und teilt die Business Epics in drei
    vordefinierte Kategorien ein:
    -   Erfolgreich abgeschlossen
    -   Keine untergeordneten Epics zur Analyse vorhanden
    -   In Bearbeitung / Unverändert

2.  **Kritikalitätsanalyse:** Für die Epics der dritten Kategorie wird eine
    weitere, KI-gestützte Analyse durchgeführt. Ein detaillierter Prompt mit den
    Daten dieser Epics wird an ein GPT-4.1-Modell gesendet, um sie nach ihrer
    Kritikalität (A, B, C, D) zu bewerten.

Das finale Ergebnis ist ein Kritikalätsbericht, der sowohl auf der Konsole
ausgegeben als auch als JSON-Datei gespeichert wird.

Usage:
    python src/categorize_and_analyze_epics.py
"""
import os
import sys
import json
from datetime import datetime
from typing import List, Dict

# Pydantic und Instructor für strukturierte LLM-Antworten
from pydantic import BaseModel, Field
import instructor

# Fügt das Projekt-Root-Verzeichnis zum Python-Pfad hinzu
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.config import DATA_DIR
from utils.logger_config import logger
from utils.azure_ai_client import AzureAIClient

# --- Pydantic Modelle für die Kritikalätsanalyse ---

class CriticalityCategorization(BaseModel):
    hochkritisch: List[str] = Field(..., description="Business Epics der Kategorie A: hochkritisch.")
    kritisch: List[str] = Field(..., description="Business Epics der Kategorie B: kritisch.")
    mittel: List[str] = Field(..., description="Business Epics der Kategorie C: mittel.")
    unkritisch: List[str] = Field(..., description="Business Epics der Kategorie D: unkritisch.")

def load_analysis_files() -> List[Dict]:
    """Lädt alle Analyse-Dateien aus dem llm_analysis-Verzeichnis."""
    analysis_dir = os.path.join(DATA_DIR, 'llm_analysis')
    if not os.path.isdir(analysis_dir):
        logger.error(f"Analyse-Verzeichnis nicht gefunden: {analysis_dir}")
        return []

    all_analyses = []
    for filename in os.listdir(analysis_dir):
        if filename.endswith('_analysis.json'):
            file_path = os.path.join(analysis_dir, filename)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if "key" in data: # Nur valide Dateien hinzufügen
                        all_analyses.append(data)
            except json.JSONDecodeError:
                logger.warning(f"Fehler beim Parsen der JSON-Datei: {filename}")

    logger.info(f"{len(all_analyses)} Analyse-Dateien geladen.")
    return all_analyses

def categorize_epics(analyses: List[Dict]) -> Dict[str, List[Dict]]:
    """Kategorisiert die geladenen Analysen."""
    categories = {
        "completed": [],
        "no_sub_epics": [],
        "in_progress": []
    }

    for analysis in analyses:
        # Kategorie 1: Erfolgreich abgeschlossen
        completion_summary = analysis.get("Completion", {}).get("Summary", "").lower()
        if "wahrscheinlich" in completion_summary or "realistisch" in completion_summary:
            categories["completed"].append(analysis)
        # Kategorie 2: Keine untergeordneten Epics (hierfür benötigen wir die ursprüngliche Analyse,
        # die Annahme hier ist, dass ein "error" oder leere Felder darauf hindeuten)
        elif not analysis.get("Scope") or not analysis.get("Progress"):
             categories["no_sub_epics"].append(analysis)
        # Kategorie 3: In Bearbeitung
        else:
            categories["in_progress"].append(analysis)

    return categories

def generate_criticality_analysis(epics_in_progress: List[Dict]) -> Dict:
    """
    Führt die KI-gestützte Kritikalitätsanalyse durch, indem jedes Epic einzeln
    bewertet wird, um das Kontextlimit des Modells nicht zu überschreiten.
    """
    if not epics_in_progress:
        logger.info("Keine Epics in Bearbeitung für die Kritikalätsanalyse gefunden.")
        return {}

    # Der ursprüngliche System-Prompt bleibt für jede Anfrage unverändert.
    prompt_instructions = """
    Du bist ein erfahrener Portfolio-Manager. Deine Aufgabe ist es, eine Liste von Business Epics zu analysieren und sie nach ihrer Kritikalität zu kategorisieren.

    Hier sind die Kritikalitäts-Kategorien mit Beispielen:

    A) hochkritisch: Business Epics mit vielen offenen Jira Stories UND signifikanten Scope-Anpassungen (z.B. neue Epics hinzugekommen oder Reife unklar).
    *Beispiel 1*
    ###
    {
      "key": "BEMABU-1992",
      "date": "11.09.2025",
      "Scope": {
        "Summary": "Der Scope ist *nicht stabil* und die Reife ist *eingeschränkt*.",
        "Reasoning": [
          "Die Anzahl der Epics ist von 60 auf 71 gestiegen (+11 Epics).",
          "Mehrere neue Epics im Status FUNNEL/ANALYSIS, d.h. noch nicht ausdetailliert.",
          "Nicht alle Epics sind mit Stories ausdetailliert (z.B. mehrere neue FUNNEL-Epics mit 0 Stories)."
        ]
      },
      "Progress": {
        "Summary": "Es wurde *aktiv* an Stories gearbeitet, aber der Fortschritt ist gering.",
        "Reasoning": [
          "41 neue Stories wurden erstellt, 14 abgeschlossen.",
          "Statusveränderungen bei Epics (z.B. mehrere Epics von FUNNEL/IN PROGRESS zu CLOSED/ANALYSIS).",
          "Die Zahl der offenen Stories ist weiterhin hoch (60)."
        ]
      },
      "Completion": {
        "Summary": "Eine kurzfristige Fertigstellung ist *unrealistisch*.",
        "Reasoning": [
          "Nur 14 Stories in 4 Wochen abgeschlossen, aber 41 neue Stories hinzugekommen.",
          "Offene Stories: 60; bei gleicher Geschwindigkeit würde die Fertigstellung deutlich länger als 3 Wochen dauern (>17 Wochen).",
          "Viele Epics sind weiterhin offen oder in frühen Status (FUNNEL, ANALYSIS, IN PROGRESS)."
        ]
      }
    }
    ###
    *Beispiel 2*
    ###
    {
      "key": "BEOC-411",
      "date": "11.09.2025",
      "Scope": {
        "Summary": "Der Scope ist *nicht stabil* und die Reife ist *niedrig*.",
        "Reasoning": [
          "Die Anzahl der Epics hat sich von 70 auf 89 erhöht (+19 neue Epics).",
          "Viele neue Epics sind noch im Status FUNNEL, ANALYSIS oder BACKLOG und nicht mit Stories ausdetailliert.",
          "Es gibt zahlreiche Epics ohne Stories oder mit nur sehr wenigen Stories."
        ]
      },
      "Progress": {
        "Summary": "Es wurde *aktiv* an Stories gearbeitet, aber der Fortschritt ist gering.",
        "Reasoning": [
          "30 neue Stories wurden erstellt, aber nur 9 abgeschlossen.",
          "Die Anzahl der offenen Stories ist gestiegen (jetzt 55).",
          "Einige Epics haben den Status gewechselt (z.B. von FUNNEL zu IN PROGRESS oder CLOSED)."
        ]
      },
      "Completion": {
        "Summary": "Eine kurzfristige Fertigstellung ist *unrealistisch*.",
        "Reasoning": [
          "Nur 9 Stories abgeschlossen bei 30 neuen Stories und 55 offenen Stories.",
          "Das Verhältnis von abgeschlossenen zu offenen Stories ist sehr ungünstig.",
          "Bei gleichbleibender Geschwindigkeit würde die Fertigstellung deutlich länger als 3 Wochen dauern."
        ]
      }
    }
    ###

    B) kritisch: Business Epics mit entweder erheblichen Scope-Anpassungen ODER einer hohen Anzahl offener Stories, die den Fortschritt blockieren.
    *Beispiel 1*
    ###
    {
      "key": "BEMABU-2461",
      "date": "11.09.2025",
      "Scope": {
        "Summary": "Der Scope ist *nicht stabil* und die Reife ist *unzureichend*.",
        "Reasoning": [
          "Die Anzahl der Epics hat sich von 10 auf 13 erhöht (+3 neue Epics).",
          "Mindestens 3 neue Epics wurden im Analysezeitraum hinzugefügt.",
          "Mindestens 3 Epics (MOFUDHS-4616, MOFUDHS-4617, MOFUDHS-4618) haben keine Stories hinterlegt.",
          "Nicht alle Epics sind mit Stories ausdetailliert."
        ]
      },
      "Progress": {
        "Summary": "Es wurde *aktiv* an Stories gearbeitet, Statusveränderungen bei Epics sind erkennbar.",
        "Reasoning": [
          "12 Stories wurden abgeschlossen, 1 neue Story wurde erstellt.",
          "Mehrere Epics haben ihren Status verändert (z.B. von BACKLOG zu IN PROGRESS/WAITING/REVIEW).",
          "Die Anzahl der offenen Stories ist leicht gesunken, was auf Fortschritt hindeutet."
        ]
      },
      "Completion": {
        "Summary": "Eine kurzfristige Fertigstellung ist *unwahrscheinlich*.",
        "Reasoning": [
          "Es sind noch 11 Stories offen, bei nur 12 abgeschlossenen Stories in 4 Wochen.",
          "Das Verhältnis von offenen zu abgeschlossenen Stories lässt erwarten, dass bei gleichbleibender Geschwindigkeit die Fertigstellung mehr als 3 Wochen dauern wird.",
          "Mehrere Epics sind weiterhin nicht abgeschlossen (kein Epic im Status CLOSED/RESOLVED)."
        ]
      }
    }
    ###
    *Beispiel 2*
    ###
    {
      "key": "BEMABU-2460",
      "date": "11.09.2025",
      "Scope": {
        "Summary": "Der Scope ist *nicht stabil* und die Reife ist *eingeschränkt*.",
        "Reasoning": [
          "Die Anzahl der Epics hat sich von 6 auf 7 erhöht (neues Epic: MAGBUS-112266).",
          "Mindestens ein Epic (MAGBUS-112266) ist ohne Stories, daher ist die Ausdetaillierung nicht vollständig."
        ]
      },
      "Progress": {
        "Summary": "Es wurde *aktiv* an Stories und Epics gearbeitet.",
        "Reasoning": [
          "10 Stories wurden abgeschlossen, 7 neue Stories erstellt.",
          "Status von mindestens einem Epic hat sich verändert (MAGBUS-107898: BACKLOG → IN PROGRESS)."
        ]
      },
      "Completion": {
        "Summary": "Eine kurzfristige Fertigstellung ist *unwahrscheinlich*.",
        "Reasoning": [
          "Es sind 37 Stories offen, mehr als in den letzten 4 Wochen abgeschlossen wurden (10).",
          "Bei gleichbleibender Geschwindigkeit würde die Fertigstellung voraussichtlich deutlich länger als 3 Wochen dauern."
        ]
      }
    }
    ###

    C) mittel: Business Epics ohne Scope-Anpassungen und mit nur wenigen offenen Stories. Der Fortschritt ist sichtbar, aber nicht abgeschlossen.
    *Beispiel 1*
    ###
    {
      "key": "BEMABU-2336",
      "date": "11.09.2025",
      "Scope": {
        "Summary": "Der Scope ist *stabil* und das Business Epic ist *reif*.",
        "Reasoning": [
          "Die Anzahl der Epics blieb konstant bei 3 über den Analysezeitraum.",
          "Alle Epics sind mit Stories ausdetailliert (insgesamt 30 Stories verteilt auf 3 Epics)."
        ]
      },
      "Progress": {
        "Summary": "Es wurde *aktiv* an Stories gearbeitet, aber nur geringe Fortschritte erzielt.",
        "Reasoning": [
          "2 Stories wurden abgeschlossen, 1 neue Story wurde erstellt.",
          "Der Status eines Epics hat sich verändert (von ANALYSIS zu WAITING), die anderen blieben gleich."
        ]
      },
      "Completion": {
        "Summary": "Eine kurzfristige Fertigstellung ist *unwahrscheinlich*.",
        "Reasoning": [
          "Nur 2 Stories wurden in 4 Wochen abgeschlossen, 4 Stories sind noch offen.",
          "Bei gleichbleibender Geschwindigkeit würde die Fertigstellung voraussichtlich weitere 8 Wochen dauern."
        ]
      }
    }
    ###
    *Beispiel 2*
    ###
    {
      "key": "BEMABU-2189",
      "date": "11.09.2025",
      "Scope": {
        "Summary": "Der Scope ist *leicht erweitert*, Reife ist hoch.",
        "Reasoning": [
          "Anzahl der Epics hat sich von 12 auf 13 erhöht (neues Epic SECEIT-3293 hinzugekommen) → leichte Scope-Erweiterung.",
          "Alle Epics sind mit Stories hinterlegt, keine Epics ohne Stories am Enddatum."
        ]
      },
      "Progress": {
        "Summary": "Es wurde *aktiv* an den Epics und Stories gearbeitet.",
        "Reasoning": [
          "13 Stories wurden abgeschlossen, 1 neue Story wurde erstellt.",
          "Status von mehreren Epics hat sich verändert (u.a. viele Epics von BACKLOG/WAITING zu CLOSED/RESOLVED).",
          "Mehrere Epics wurden abgeschlossen (Status CLOSED/RESOLVED erhöht)."
        ]
      },
      "Completion": {
        "Summary": "Eine kurzfristige Fertigstellung ist *unwahrscheinlich*.",
        "Reasoning": [
          "Es sind noch 13 Stories offen, nur 13 Stories in 4 Wochen abgeschlossen.",
          "Bei gleichbleibender Geschwindigkeit dauert die Fertigstellung voraussichtlich weitere 4 Wochen oder länger.",
          "Nur ein geringer Anteil der Stories wurde neu erstellt, aber der offene Bestand bleibt hoch."
        ]
      }
    }
    ###

    D) unkritisch: Business Epics, bei denen die Analyse vorhersagt, dass sie wahrscheinlich in den nächsten Wochen abgeschlossen werden können.
    *Beispiel 1*
    ###
    {
      "key": "BEMABU-2260",
      "date": "11.09.2025",
      "Scope": {
        "Summary": "Der Scope ist *stabil* und das Business Epic ist *reif*.",
        "Reasoning": [
          "Die Anzahl der Epics blieb konstant bei 1.",
          "Das Epic ist vollständig ausdetailliert, da die einzige Story dem Epic zugeordnet ist."
        ]
      },
      "Progress": {
        "Summary": "Es wurde *aktiv* am Business Epic gearbeitet.",
        "Reasoning": [
          "Die einzige Story wurde im Analysezeitraum abgeschlossen.",
          "Der Status des Epics hat sich von BACKLOG zu RESOLVED verändert."
        ]
      },
      "Completion": {
        "Summary": "Die Fertigstellung ist *unmittelbar* erreicht.",
        "Reasoning": [
          "Alle Stories sind abgeschlossen, es sind keine offenen Stories mehr vorhanden.",
          "Das Epic ist im Status RESOLVED."
        ]
      }
    }
    ###
    *Beispiel 2*
    ###
    {
      "key": "BEMABU-2276",
      "date": "11.09.2025",
      "Scope": {
        "Summary": "Der Scope ist *stabil* und das Epic ist *reif*.",
        "Reasoning": [
          "Die Anzahl der Epics hat sich nicht verändert (1 Epic zu Beginn und am Ende).",
          "Das Epic ist mit Stories ausdetailliert (27 Stories hinterlegt)."
        ]
      },
      "Progress": {
        "Summary": "Es wurde *aktiv* an dem Epic gearbeitet.",
        "Reasoning": [
          "8 Stories wurden abgeschlossen.",
          "3 neue Stories wurden erstellt.",
          "Der Status des Epics hat sich von 'BACKLOG' zu 'IN PROGRESS' verändert."
        ]
      },
      "Completion": {
        "Summary": "Eine kurzfristige Fertigstellung ist *möglich*, aber nicht gesichert.",
        "Reasoning": [
          "Es sind nur noch 7 Stories offen.",
          "In den letzten 4 Wochen wurden 8 Stories abgeschlossen, was auf eine gute Bearbeitungsgeschwindigkeit hindeutet.",
          "Wenn die Geschwindigkeit beibehalten wird, könnten alle Stories in weniger als 4 Wochen abgeschlossen werden."
        ]
      }
    }
    ###

    Analysiere die bereitgestellten Daten und ordne jeden Business Epic Key einer der vier Kategorien zu.
    Gib deine Antwort als JSON-Objekt zurück, das dem Pydantic-Modell `CriticalityCategorization` entspricht.
    """

    # Initialisiere den KI-Client
    ai_client = AzureAIClient()
    instructor.patch(ai_client.openai_client)

    # Dictionary zum Sammeln der finalen Ergebnisse
    final_categorization = {
        "hochkritisch": [], "kritisch": [], "mittel": [], "unkritisch": []
    }

    # Jedes Epic einzeln verarbeiten, um das Kontextlimit zu umgehen
    for epic in epics_in_progress:
        epic_key = epic.get('key')
        logger.info(f"Analysiere Kritikalität für Epic: {epic_key}...")

        # Erstelle den Kontext-Teil des Prompts nur mit den Daten des aktuellen Epics
        context_data = "Hier sind die Analyseergebnisse des Business Epics, das sich aktuell in Bearbeitung befindet:\n\n"
        context_data += f"--- Business Epic: {epic.get('key')} ---\n"
        context_data += f"Scope Summary: {epic.get('Scope', {}).get('Summary', 'N/A')}\n"
        context_data += f"Scope Reasoning: {', '.join(epic.get('Scope', {}).get('Reasoning', []))}\n"
        context_data += f"Progress Summary: {epic.get('Progress', {}).get('Summary', 'N/A')}\n"
        context_data += f"Progress Reasoning: {', '.join(epic.get('Progress', {}).get('Reasoning', []))}\n"
        context_data += f"Completion Summary: {epic.get('Completion', {}).get('Summary', 'N/A')}\n"
        context_data += f"Completion Reasoning: {', '.join(epic.get('Completion', {}).get('Reasoning', []))}\n\n"

        # Kombiniere Anweisungen und Daten zu einem einzigen User-Prompt
        full_user_prompt = f"{prompt_instructions}\n\n{context_data}"

        try:
            response = ai_client.openai_client.chat.completions.create(
                model="gpt-4.1",
                response_model=CriticalityCategorization,
                messages=[{"role": "user", "content": full_user_prompt}],
                temperature=0.0,
            )

            # Ergebnis der Einzel-Analyse in das finale Dictionary einfügen
            response_data = response.model_dump()
            if response_data.get("hochkritisch"):
                final_categorization["hochkritisch"].append(epic_key)
            elif response_data.get("kritisch"):
                final_categorization["kritisch"].append(epic_key)
            elif response_data.get("mittel"):
                final_categorization["mittel"].append(epic_key)
            elif response_data.get("unkritisch"):
                final_categorization["unkritisch"].append(epic_key)

        except Exception as e:
            logger.error(f"Fehler bei der KI-Kritikalitätsanalyse für Epic {epic_key}: {e}", exc_info=True)

    logger.info("KI-Kritikalitätsanalyse für alle Epics erfolgreich abgeschlossen.")
    return final_categorization


if __name__ == "__main__":
    # 1. Analysen laden
    all_analyses = load_analysis_files()

    if all_analyses:
        # 2. Epics kategorisieren
        categorized_epics = categorize_epics(all_analyses)

        print("\n===== Kategorisierung der Business Epics =====")
        print(f"Erfolgreich abgeschlossen: {len(categorized_epics['completed'])} ({[e['key'] for e in categorized_epics['completed']]})")
        print(f"Keine untergeordneten Epics: {len(categorized_epics['no_sub_epics'])} ({[e['key'] for e in categorized_epics['no_sub_epics']]})")
        print(f"In Bearbeitung: {len(categorized_epics['in_progress'])} ({[e['key'] for e in categorized_epics['in_progress']]})")

        # 3. Kritikalitätsanalyse für Epics in Bearbeitung durchführen
        criticality_result = generate_criticality_analysis(categorized_epics['in_progress'])

        if criticality_result:
            print("\n===== KI-gestützte Kritikalitätsanalyse =====")
            print(json.dumps(criticality_result, indent=2, ensure_ascii=False))

            # 4. Ergebnis speichern
            output_dir = os.path.join(DATA_DIR, 'llm_analysis')
            output_path = os.path.join(output_dir, 'criticality_report.json')
            try:
                with open(output_path, 'w', encoding='utf-8') as f:
                    json.dump(criticality_result, f, indent=2, ensure_ascii=False)
                logger.info(f"Kritikalitätsbericht erfolgreich gespeichert: {output_path}")
            except Exception as e:
                logger.error(f"Fehler beim Speichern des Berichts: {e}")
