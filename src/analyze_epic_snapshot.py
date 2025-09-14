"""
Führt eine "Snapshot"-Analyse der Epic- und Story-Dynamik für ein oder mehrere
Business Epics durch und generiert eine KI-gestützte Bewertung des
Projektfortschritts mithilfe von Pydantic.

Dieses Skript kann entweder ein einzelnes, per Kommandozeile übergebenes
Business Epic analysieren oder, falls kein spezifisches Epic angegeben wird,
eine Liste von Epics aus der Datei 'BE_Liste.txt' im Projekt-Root-Verzeichnis
verarbeiten.

Funktionsweise:
1.  **Datenbeschaffung:** Lädt die vollständige Issue-Hierarchie und alle
    zugehörigen Aktivitäten für jedes zu analysierende Business Epic.
2.  **Quantitative Analyse:**
    -   Listet alle Epics mit ihrem Status und der Story-Anzahl zu Beginn und
        am Ende des gewählten Zeitraums auf.
    -   Analysiert die Story-Dynamik (erstellt, abgeschlossen, offen) für die
        letzten 4 Wochen vor dem Enddatum.
3.  **KI-Bewertung (mit Pydantic):**
    -   Definiert eine strikte Ausgabestruktur mittels Pydantic-Modellen.
    -   Formatiert die Analyseergebnisse in einen strukturierten Prompt.
    -   Sendet die Daten an ein Azure AI GPT-4.1 Modell und fordert eine Antwort an,
        die direkt dem Pydantic-Modell entspricht.
4.  **Ausgabe:**
    -   Speichert die validierte KI-Bewertung in einer strukturierten JSON-Datei.
    -   Gibt sowohl die quantitative Analyse als auch die KI-Bewertung auf der
        Konsole aus.

Usage:
    # Analyse für ein einzelnes Epic
    python src/analyze_epic_snapshot.py --issue BEMABU-1234

    # Analyse für alle Epics aus der BE_Liste.txt
    python src/analyze_epic_snapshot.py
"""
import os
import sys
import argparse
import json
from datetime import datetime, timedelta
from collections import defaultdict
import networkx as nx
from typing import List

# Pydantic und Instructor für strukturierte LLM-Antworten
from pydantic import BaseModel, Field
import instructor

# Fügt das Projekt-Root-Verzeichnis zum Python-Pfad hinzu
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.project_data_provider import ProjectDataProvider
from utils.config import JIRA_TREE_FULL, DATA_DIR
from utils.logger_config import logger
from utils.azure_ai_client import AzureAIClient

# --- Pydantic Modelle für die strukturierte LLM-Antwort ---

class ScopeAnalysis(BaseModel):
    Summary: str = Field(..., description="Zusammenfassende Bewertung zu Scope-Reife und Stabilität.")
    Reasoning: List[str] = Field(..., description="Begründungspunkte als Liste von Strings.")

class ProgressAnalysis(BaseModel):
    Summary: str = Field(..., description="Zusammenfassende Bewertung zum Arbeitsfortschritt.")
    Reasoning: List[str] = Field(..., description="Begründungspunkte als Liste von Strings.")

class CompletionAnalysis(BaseModel):
    Summary: str = Field(..., description="Zusammenfassende Bewertung zur voraussichtlichen Fertigstellung.")
    Reasoning: List[str] = Field(..., description="Begründungspunkte als Liste von Strings.")

class LLMAnalysisResponse(BaseModel):
    Scope: ScopeAnalysis
    Progress: ProgressAnalysis
    Completion: CompletionAnalysis

def clean_status_name(raw_name: str) -> str:
    """Bereinigt einen rohen Status-Namen aus dem Aktivitätsprotokoll."""
    if not raw_name: return "N/A"
    if '[' in raw_name:
        try:
            return raw_name.split(':')[1].split('[')[0].strip().upper()
        except IndexError:
            return raw_name.strip().upper()
    return raw_name.strip().upper()

def get_status_at_date(activities: list, cutoff_date: datetime.date, creation_date: datetime.date) -> str:
    """Ermittelt den Status eines Issues zu einem bestimmten Datum."""
    if creation_date > cutoff_date:
        return "NOT_YET_CREATED"

    last_status = "FUNNEL"
    for act in activities:
        act_date = datetime.fromisoformat(act['zeitstempel_iso']).date()
        if act_date > cutoff_date:
            break
        if act.get('feld_name') == 'Status':
            last_status = clean_status_name(act.get('neuer_wert', 'FUNNEL'))
    return last_status

def generate_llm_analysis(issue_key: str, analysis_data: dict) -> dict:
    """
    Nutzt ein LLM, um die quantitativen Analyseergebnisse zu bewerten.

    Args:
        issue_key (str): Der Jira-Key des Business Epics.
        analysis_data (dict): Die gesammelten Daten aus der Snapshot-Analyse.

    Returns:
        dict: Die strukturierte JSON-Antwort vom LLM.
    """
    prompt_instructions = """
    Du bist Business Analyst und muss beurteilen, ob ein in Entwicklung befindliches Business Epic well-on-track ist; du bekommst für deine Analyse beigefügte Daten zur Auswertung:
    1) SCOPE:  Ist das Business Epics reif und der Scope stabil?
    => Der Scope ist stabil wenn sich die Anzahl der Epics nicht erhöht hat
    => Das Business Epic ist reif, wenn alle Epics sind zum Ende Datum anhand von Jira Stories ausdetailliert sind

    2) PROGRESS: Wurde an dem Business Epic gearbeitet?
    => entscheidend für aktive Arbeit ist, ob Stories abgeschlossen wurden (notwendige Bedingung)
    => ergänzende Kriterien sind, wenn sich der Status der Epics verändert hat und neue Stories erstellt wurden

    3) COMPLETION: Ist das Business Epic kurz davor fertiggestellt zu werden?
    => Epics sind überwiegend bereits im status closed/resolved
    => es sind nur noch sehr wenige Stories offen
    => Anzahl der offenen Stories im Verhältnis zu der Anzahl der abgeschlossenen Stories und der neu erstellten Stories ist so, dass erwartbar ist, dass bei gleicher Geschwindigkeit in den kommenden 3 Wochen alle Stories abgeschlossen werden

    Formuliere möglichst kurz und präzise, nutze Bullet Ponts wo sinnvoll.

    {
      "key": "BEMABU-2230",
      "date": "11.09.2025",
      "Scope": {
        "Summary": "Der Scope ist *stabil*",
        "Reasoning": [
          "Der Scope des Business Epics umfasst 5 Epics und hat sich nicht geändert",
          "Der Scope hat eine sehr hohe Reife, da alle Epics mit Stories hinterlegt sind" 
        ]
      },
      "Progress": {
        "Summary": "Es wurde sowohl *aktiv* an den Epics als auch an den Stories gearbeitet",
        "Reasoning": [
          "Es wurden 13 Stories abgeschlossen und 18 neue Stories erstellt",
          "Der Status aller 5 Epics hat sich verändert, ein Epic wurde abgeschlossen"
        ]
      },
      "Completion": {
        "Summary": "Die kurzfristige Fertigstellung ist *unwahrscheinlich*",
        "Reasoning": [
          "Mehr Stories erstellt (18) als abgeschlossen (13) wurden",
          "Bei 10 offenen Stories die Fertigstellung bei gleicher Geschwindigkeit voraussichtlich weitere 9,2 Wochen dauern würde"
        ]
      }
    }

    Deine Antwort muss exakt dem angeforderten Pydantic-JSON-Schema entsprechen.
    """

    full_user_prompt = f"""
    {prompt_instructions}

    Bitte analysiere das Business Epic {issue_key} basierend auf den folgenden Daten und gib deine Antwort als JSON-Objekt zurück, das dem Pydantic-Modell `LLMAnalysisResponse` entspricht.

    Analysezeitraum für Epics: {analysis_data['start_date_str']} bis {analysis_data['stop_date_str']}

    **Epic-Status zum Start:**
    - Gesamtzahl Epics: {analysis_data['epics_at_start_count']}
    - Epics-Details:
    {json.dumps(analysis_data['epics_at_start_details'], indent=2, ensure_ascii=False)}

    **Epic-Status zum Ende:**
    - Gesamtzahl Epics: {analysis_data['epics_at_stop_count']}
    - Epics-Details:
    {json.dumps(analysis_data['epics_at_stop_details'], indent=2, ensure_ascii=False)}

    **Story-Analyse für die letzten 4 Wochen (bis zum {analysis_data['stop_date_str']}):**
    - Neu hinzugekommene Stories: {analysis_data['newly_created_stories']}
    - Abgeschlossene Stories: {analysis_data['closed_stories_in_period']}
    - Anzahl offener Stories zum Enddatum: {analysis_data['open_stories_at_stop']}
    """

    try:
        logger.info("Starte LLM-Analyse mit Pydantic-Validierung...")
        ai_client = AzureAIClient()
        instructor.patch(ai_client.openai_client)

        llm_response_object = ai_client.openai_client.chat.completions.create(
            model="gpt-4.1",
            response_model=LLMAnalysisResponse,
            messages=[{"role": "user", "content": full_user_prompt}],
            temperature=0.1,
            max_tokens=2000,
        )
        logger.info("LLM-Analyse erfolgreich abgeschlossen und validiert.")

        final_json = {
            "key": issue_key,
            "date": datetime.now().strftime("%d.%m.%Y"),
            **llm_response_object.model_dump()
        }
        return final_json

    except Exception as e:
        logger.error(f"Fehler bei der LLM-Analyse: {e}", exc_info=True)
        return {"error": str(e)}

def analyze_epic_snapshot(issue_key: str, start_date_str: str, stop_date_str: str):
    try:
        start_date = datetime.strptime(start_date_str, "%d.%m.%Y").date()
        stop_date = datetime.strptime(stop_date_str, "%d.%m.%Y").date()
    except ValueError:
        logger.error(f"Ungültiges Datumsformat für Epic {issue_key}. Bitte DD.MM.YYYY verwenden.")
        return

    logger.info(f"Starte Snapshot-Analyse für {issue_key} von {start_date_str} bis {stop_date_str}")

    data_provider = ProjectDataProvider(epic_id=issue_key, hierarchy_config=JIRA_TREE_FULL)
    if not data_provider.is_valid():
        logger.error(f"Konnte keine gültigen Daten für das Epic '{issue_key}' laden.")
        return

    # Epic-Analyse
    epic_keys = {k for k, v in data_provider.issue_details.items() if v.get('type') == 'Epic'}
    activities_by_key = defaultdict(list)
    for act in data_provider.all_activities: activities_by_key[act.get('issue_key')].append(act)

    analyzed_epics = []
    for key in epic_keys:
        key_activities = sorted(activities_by_key.get(key, []), key=lambda x: x['zeitstempel_iso'])
        if not key_activities: continue
        creation_date = datetime.fromisoformat(key_activities[0]['zeitstempel_iso']).date()
        status_at_start = get_status_at_date(key_activities, start_date, creation_date)
        status_at_stop = get_status_at_date(key_activities, stop_date, creation_date)
        story_count = sum(1 for child_key in nx.descendants(data_provider.issue_tree, key) if data_provider.issue_details.get(child_key, {}).get('type') == 'Story') if key in data_provider.issue_tree else 0
        analyzed_epics.append({"key": key, "status_at_start": status_at_start, "status_at_stop": status_at_stop, "story_count": story_count})

    epics_at_start = [e for e in analyzed_epics if e['status_at_start'] != 'NOT_YET_CREATED']
    epics_at_stop = [e for e in analyzed_epics if e['status_at_stop'] != 'NOT_YET_CREATED']

    # Story-Analyse
    story_analysis_start_date = stop_date - timedelta(weeks=4)
    story_keys = {k for k, v in data_provider.issue_details.items() if v.get('type') == 'Story'}
    analyzed_stories = []
    closed_stati = {'CLOSED', 'DEPLOYMENT', 'RESOLVED'}
    for key in story_keys:
        key_activities = sorted(activities_by_key.get(key, []), key=lambda x: x['zeitstempel_iso'])
        if not key_activities: continue
        creation_date = datetime.fromisoformat(key_activities[0]['zeitstempel_iso']).date()
        closing_date = next((datetime.fromisoformat(act['zeitstempel_iso']).date() for act in reversed(key_activities) if act.get('feld_name') == 'Status' and clean_status_name(act.get('neuer_wert')) in closed_stati), None)
        analyzed_stories.append({"key": key, "creation_date": creation_date, "closing_date": closing_date})

    newly_created_stories_4_weeks = [s for s in analyzed_stories if story_analysis_start_date <= s['creation_date'] <= stop_date]
    closed_stories_in_period_4_weeks = [s for s in analyzed_stories if s['closing_date'] and story_analysis_start_date <= s['closing_date'] <= stop_date]
    open_stories_at_stop = [s for s in analyzed_stories if s['creation_date'] <= stop_date and (s['closing_date'] is None or s['closing_date'] > stop_date)]

    # Ausgabe
    print(f"\n===== Quantitative Analyse für {issue_key} =====")
    print(f"Zeitraum für Epics: {start_date_str} bis {stop_date_str}\n")
    print(f"--- 📊 Epic-Status zum Start ({start_date_str}) ---")
    print(f"Gesamtzahl Epics: {len(epics_at_start)}")
    if epics_at_start:
        for epic in sorted(epics_at_start, key=lambda x: x['key']):
            print(f"  - {epic['key']:<15} | Status: {epic['status_at_start']:<15} | #Stories: {epic['story_count']}")
    print(f"\n--- 📊 Epic-Status zum Ende ({stop_date_str}) ---")
    print(f"Gesamtzahl Epics: {len(epics_at_stop)}")
    if epics_at_stop:
        for epic in sorted(epics_at_stop, key=lambda x: x['key']):
            print(f"  - {epic['key']:<15} | Status: {epic['status_at_stop']:<15} | #Stories: {epic['story_count']}")
    print(f"\n--- 📝 Story-Analyse letzte 4 Wochen ---")
    print(f"Neu hinzugekommene Stories: {len(newly_created_stories_4_weeks)}")
    print(f"Abgeschlossene Stories: {len(closed_stories_in_period_4_weeks)}")
    print(f"Anzahl offener Stories zum {stop_date_str}: {len(open_stories_at_stop)}")

    analysis_input_data = {
        "start_date_str": start_date_str, "stop_date_str": stop_date_str,
        "epics_at_start_count": len(epics_at_start),
        "epics_at_start_details": [{"key": e['key'], "status": e['status_at_start'], "story_count": e['story_count']} for e in epics_at_start],
        "epics_at_stop_count": len(epics_at_stop),
        "epics_at_stop_details": [{"key": e['key'], "status": e['status_at_stop'], "story_count": e['story_count']} for e in epics_at_stop],
        "newly_created_stories": len(newly_created_stories_4_weeks),
        "closed_stories_in_period": len(closed_stories_in_period_4_weeks),
        "open_stories_at_stop": len(open_stories_at_stop)
    }

    llm_analysis_result = generate_llm_analysis(issue_key, analysis_input_data)

    print(f"\n\n===== KI-gestützte Bewertung für {issue_key} =====")
    print(json.dumps(llm_analysis_result, indent=2, ensure_ascii=False))

    output_dir = os.path.join(DATA_DIR, 'llm_analysis')
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{issue_key}_analysis.json")
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(llm_analysis_result, f, indent=2, ensure_ascii=False)
    logger.info(f"KI-Analyse erfolgreich gespeichert: {output_path}")
    print("\n" + "="*53)


if __name__ == "__main__":
    stop_date_default = datetime.now().strftime("%d.%m.%Y")
    start_date_default = (datetime.now() - timedelta(weeks=12)).strftime("%d.%m.%Y")

    parser = argparse.ArgumentParser(description="Führt eine Snapshot-Analyse und KI-Bewertung durch.")
    # Das --issue Argument ist nicht mehr 'required'
    parser.add_argument("--issue", type=str, help="Der Jira-Key eines einzelnen Business Epics (z.B. 'BEMABU-1234').")
    parser.add_argument("--start_date", type=str, default=start_date_default, help=f"Startdatum (Format: DD.MM.YYYY, Default: {start_date_default}).")
    parser.add_argument("--stop_date", type=str, default=stop_date_default, help=f"Enddatum (Format: DD.MM.YYYY, Default: {stop_date_default}).")
    args = parser.parse_args()

    epics_to_process = []
    # Logik zur Bestimmung der zu verarbeitenden Epics
    if args.issue:
        epics_to_process.append(args.issue)
        logger.info(f"Einzelnes Business Epic zur Analyse ausgewählt: {args.issue}")
    else:
        # Pfad zur BE_Liste.txt im Projekt-Root-Verzeichnis
        be_list_path = os.path.join(project_root, 'BE_Liste.txt')
        try:
            with open(be_list_path, 'r', encoding='utf-8') as f:
                # Entferne leere Zeilen und extrahiere nur den Key
                epics_to_process = [line.strip().split('​')[0] for line in f if line.strip()]
            logger.info(f"{len(epics_to_process)} Business Epics aus {be_list_path} geladen.")
        except FileNotFoundError:
            logger.error(f"Die Datei {be_list_path} wurde nicht gefunden. Bitte geben Sie ein --issue an oder erstellen Sie die Datei.")
            sys.exit(1)

    # Hauptschleife zur Verarbeitung der Epics
    for i, epic_key in enumerate(epics_to_process):
        print(f"\n{'='*70}")
        print(f"  Verarbeite Epic {i+1}/{len(epics_to_process)}: {epic_key}")
        print(f"{'='*70}")
        analyze_epic_snapshot(epic_key, args.start_date, args.stop_date)
