"""
Haupt-Orchestrierungsskript für das Laden, Analysieren und Berichten von
Jira Business Epic Daten.

Dieses Skript dient als zentraler Einstiegspunkt und steuert den gesamten
Workflow von der Datenerfassung bis zur Erstellung finaler Reports. Es kann
entweder für ein einzelnes, per Kommandozeile übergebenes Business Epic
oder für eine Liste von Epics aus einer Datei (`BE_Liste.txt`) ausgeführt werden.

Der Prozess umfasst typischerweise folgende Schritte (konfigurierbar über Flags):
1.  **Datenbeschaffung (Tree Loader):** Nutzt den `JiraTreeLoader`, um die
    vollständige Issue-Hierarchie für jedes Business Epic rekursiv von der
    Jira REST API zu laden. Unterstützt verschiedene Lademodi (`full`, `delta`, `none`),
    um entweder alle Daten frisch zu ziehen oder effizient nur Änderungen zu laden.
2.  **Metrische Analyse:** Führt eine Reihe von quantitativen Analysen
    (Scope, Status, Dynamik, Backlog) über den `AnalysisRunner`
    und spezialisierte Analyzer-Klassen (`ScopeAnalyzer`, `StatusAnalyzer` etc.)
    durch. Erstellt dabei auch Plot-Grafiken (z.B. Backlog-Entwicklung).
3.  **Inhaltliche Zusammenfassung (LLM):** Generiert mittels DNA-Bot Client (LLM)
    eine qualitative, textbasierte Zusammenfassung des Business Epics, seiner
    Ziele, Abhängigkeiten und Risiken. Unterstützt Streaming für lange Antworten
    und nutzt intelligente Kontext-Kürzung (Pruning).
4.  **Datenaggregation:** Fusioniert die Ergebnisse der metrischen Analysen
    und der LLM-Zusammenfassung zu einem einzigen, umfassenden JSON-Dokument
    (`_complete_summary.json`) über den `JsonSummaryGenerator`.
5.  **HTML-Berichterstellung:** Erzeugt einen detaillierten HTML-Report für jedes
    Business Epic mithilfe des `EpicHtmlGenerator`. Dieser nutzt LLM-Unterstützung
    zur Textgenerierung und integriert die erzeugten Plots.
6.  **Übersetzung (Optional):** Übersetzt den generierten HTML-Report ins Englische
    mittels des `HtmlTranslator` und des DNA-Bot Clients.
7.  **Fehlerbehandlung:** Beinhaltet Mechanismen zur Fehlerprotokollierung und
    einen expliziten Retry-Modus (`--retry-failed`) für fehlgeschlagene Ladevorgänge.
8.  **Keep-Awake:** Startet einen Hintergrund-Thread, um den System-Ruhezustand
    während langer Läufe zu verhindern.

Die Konfiguration der einzelnen Schritte erfolgt über Kommandozeilenargumente.
"""

import os
import re
import sys
import json
import argparse
import yaml
import time
import threading
import subprocess

# Fügen Sie das übergeordnete Verzeichnis (Projekt-Root) zum Suchpfad hinzu...
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# --- GEÄNDERTER IMPORT ---
# Ersetzt den alten JiraApiLoader durch den neuen rekursiven JiraTreeLoader
from utils.jira_tree_loader import JiraTreeLoader
# --- ENDE ÄNDERUNG ---

from utils.jira_tree_classes import JiraTreeGenerator, JiraTreeVisualizer, JiraContextGenerator
#from utils.azure_ai_client import AzureAIClient
from utils.dna_bot_client import DnaBotClient
from utils.epic_html_generator import EpicHtmlGenerator
from utils.token_usage_class import TokenUsage
from utils.logger_config import logger
from utils.json_parser import LLMJsonParser
from utils.html_translator import HtmlTranslator
from utils.project_data_provider import ProjectDataProvider
from utils.keep_awake import prevent_screensaver

# Importiere die spezifischen Analyzer-Klassen
from features.scope_analyzer import ScopeAnalyzer
from features.dynamics_analyzer import DynamicsAnalyzer
from features.status_analyzer import StatusAnalyzer
#from features.time_creep_analyzer import TimeCreepAnalyzer
from features.backlog_analyzer import BacklogAnalyzer
from features.analysis_runner import AnalysisRunner
from features.console_reporter import ConsoleReporter
from features.json_summary_generator import JsonSummaryGenerator

from utils.config import (
    # Pfade
    JSON_SUMMARY_DIR,
    HTML_REPORTS_DIR,
    PROMPTS_DIR,
    TOKEN_LOG_FILE,
    ISSUE_LOG_FILE,

    # Modelle
    LLM_MODEL_HTML_GENERATOR,
    LLM_MODEL_SUMMARY,
    LLM_MODEL_TRANSLATOR,
    # LLM_MODEL_BUSINESS_VALUE,

    # Tree Configs
    JIRA_TREE_MANAGEMENT,
    JIRA_TREE_FULL,

MAX_TOKEN_BUDGET_FOR_SUMMARY = 40000

# Zentrale Liste der zu verwendenden Analyzer
ANALYZERS_TO_RUN = [
    ScopeAnalyzer,
    #DynamicsAnalyzer,
    StatusAnalyzer,
    #TimeCreepAnalyzer,
    BacklogAnalyzer
]


def perform_final_retry(token_tracker):
    """
    Liest hartnäckig fehlgeschlagene Issues aus einer Log-Datei
    und versucht einen letzten, gezielten API-Lade-Durchlauf für diese.
    (ANGEPASST FÜR API-NUTZUNG)
    """
    if not os.path.exists(ISSUE_LOG_FILE) or os.path.getsize(ISSUE_LOG_FILE) == 0:
        logger.info("Keine fehlgeschlagenen Issues in der Log-Datei gefunden. Überspringe finalen Retry.")
        return
    logger.info(f"--- Starte finalen Retry-Versuch (API-MODUS) für Issues aus '{ISSUE_LOG_FILE}' ---")

    with open(ISSUE_LOG_FILE, 'r') as f:
        failed_keys = [line.strip() for line in f if line.strip()]

    # Extrahiere nur gültige Jira-Keys, um fehlerhafte Zeilen zu ignorieren
    key_pattern = re.compile(r'([A-Z][A-Z0-9]*-\d+)')
    valid_failed_keys = []
    for line in failed_keys:
        match = key_pattern.search(line)
        if match:
            valid_failed_keys.append(match.group(1))

    if not valid_failed_keys:
        logger.info("Log-Datei enthält keine gültigen Jira-Keys. Kein Retry notwendig.")
        return

    # --- NEU: JiraTreeLoader initialisieren ---
    # Nutzt denselben rekursiven Loader, den wir jetzt auch für den Hauptlauf verwenden.
    logger.info("Initialisiere JiraTreeLoader für den Retry-Lauf...")
    tree_loader = JiraTreeLoader(token_tracker=token_tracker)
    # --- ENDE NEU ---

    successful_retries, persistent_failures = [], []
    for key in valid_failed_keys:
        logger.info(f"Dritter Versuch (API-Modus, rekursiv) für Issue: {key}")

        # --- NEU: API-Aufruf statt Scraper ---
        # Wir rufen die Hauptmethode 'run' auf. Diese lädt den Key und
        # alle seine Kinder rekursiv.
        try:
            # Führe den rekursiven Ladevorgang für diesen einen Key aus
            tree_loader.run(start_key=key)

            # Prüfen, ob der *spezifische Key* selbst fehlgeschlagen ist
            if key in tree_loader.issues_to_retry:
                logger.warning(f"Issue {key} konnte auch im dritten Anlauf (API) nicht verarbeitet werden.")
                persistent_failures.append(key)
            else:
                logger.info(f"Issue {key} im dritten Anlauf (API) erfolgreich verarbeitet.")
                successful_retries.append(key)
        except Exception as e:
            logger.error(f"Schwerer Fehler bei API-Retry für {key}: {e}")
            persistent_failures.append(key)

    # Logdatei mit den endgültig fehlgeschlagenen Issues neu schreiben
    with open(ISSUE_LOG_FILE, 'w') as f:
        for key in persistent_failures: f.write(f"{key}\n")

    logger.info("--- Finaler Retry-Versuch (API-Modus) abgeschlossen. ---")
    logger.info(f"Erfolgreich nachgeholt: {len(successful_retries)}")
    logger.info(f"Endgültig fehlgeschlagen: {len(persistent_failures)}")

def load_prompt(filename, key):
    """Lädt einen Prompt aus einer YAML-Datei im PROMPTS_DIR."""
    file_path = os.path.join(PROMPTS_DIR, filename)
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            prompts = yaml.safe_load(file)
            return prompts[key]
    except (FileNotFoundError, KeyError) as e:
        logger.error(f"Fehler beim Laden des Prompts: {e}")
        sys.exit(1)

def get_business_epics_from_file(file_path=None):
    """
    Lädt und extrahiert Business Epic IDs aus einer Textdatei.
    """
    print("\n=== Telekom Jira Issue Extractor und Analyst ===")
    if not file_path:
        file_path = input("Bitte geben Sie den Pfad zur TXT-Datei mit Business Epics ein (oder drücken Sie Enter für 'BE_Liste.txt'): ")
        if not file_path:
            file_path = "BE_Liste.txt"

    file_to_try = file_path if os.path.exists(file_path) else f"{file_path}.txt"
    if not os.path.exists(file_to_try):
        print(f"FEHLER: Die Datei {file_to_try} existiert nicht.")
        return []

    business_epics = []
    epic_id_pattern = re.compile(r'[A-Z][A-Z0-9]*-\d+')

    with open(file_to_try, 'r', encoding='utf-8') as file:
        for line in file:
            match = epic_id_pattern.search(line)
            if match:
                business_epics.append(match.group(0))

    print(f"{len(business_epics)} Business Epics gefunden.")
    return business_epics

def main():
    """
    Hauptfunktion zur Orchestrierung des Skripts.
    """
    parser = argparse.ArgumentParser(description='Jira Issue Link Scraper')
    parser.add_argument('--html_summary', type=str.lower, choices=['true', 'false', 'check'], default='false', help="Erstellt JSON-Zusammenfassung und HTML-Report. 'true': immer neu; 'check': aus Cache, falls vorhanden; 'false': keine Erstellung.")
    parser.add_argument('--issue', type=str, default=None, help='Spezifische Jira-Issue-ID')
    parser.add_argument('--file', type=str, default=None, help='Pfad zur TXT-Datei mit Business Epics')
    parser.add_argument('--translate', type=str.lower, choices=['true', 'false', 'check'], default='false', help="Übersetzt den HTML-Report ins Englische.")
    parser.add_argument('--retry-failed', action='store_true', help='Führt nur den Retry für fehlgeschlagene Issues aus der Log-Datei aus.')
    parser.add_argument(
        '--loader-mode',
        type=str.lower,
        choices=['full', 'delta', 'none'],
        default='delta',
        help="Lademodus: 'full' (lädt alle Issues neu), "
             "'delta' (prüft DB-Cache auf Aktualität, Standard), "
             "'none' (überspringt den gesamten Ladevorgang)."
    )
    args = parser.parse_args()

    stop_event = threading.Event()
    keep_awake_thread = threading.Thread(target=prevent_screensaver, args=(stop_event,))
    keep_awake_thread.daemon = True
    keep_awake_thread.start()
    tree_generator_full = None

    try:
        token_tracker = TokenUsage(log_file_path=TOKEN_LOG_FILE)

        # +++ NEU: Logik zur Behandlung von --retry-failed +++
        if args.retry_failed:
            print("\n--- Modus: Nur fehlgeschlagene Issues erneut verarbeiten ---")
            perform_final_retry(token_tracker)
            # Nach dem Retry-Lauf wird das Skript beendet.
            return

        business_epics = [args.issue] if args.issue else get_business_epics_from_file(args.file)
        if not business_epics:
            print("Keine Business Epics gefunden. Programm wird beendet.")
            return

        # Azure Client für Business Value Extraktion initialisieren (immer verfügbar)
        #business_value_system_prompt = load_prompt("business_value_prompt.yaml", "system_prompt")
        #azure_extraction_client = AzureAIClient(system_prompt=business_value_system_prompt)

        # --- ANGEPASSTE LADE-LOGIK ---
        # Prüft auf den neuen 'none'-Modus statt auf 'false'
        if args.loader_mode != 'none':
            # Der 'scraper' Wert wird nicht mehr an den Loader übergeben,
            # da 'JiraTreeLoader' immer den 'full' Modus verwendet.
            # Der 'check'-Modus wird hier effektiv als 'true' behandelt
            # (d.h. "Laden ausführen").

            print(f"\n--- API-Lademodus gestartet (Modus: {args.loader_mode}) ---") # <--- Angepasste Ausgabe

            # Initialisiere den neuen Loader EINMAL
            try:
                # --- GEÄNDERT: loader_mode wird an den Konstruktor übergeben ---
                tree_loader = JiraTreeLoader(
                    token_tracker=token_tracker,
                    loader_mode=args.loader_mode
                )
                # --- ENDE ÄNDERUNG ---
            except ValueError as e:
                # Fängt den Fehler ab, wenn JIRA_API_TOKEN fehlt
                logger.error(f"FEHLER: {e}. Breche Ladevorgang ab.")
                return

            # Iteriere über die Epics und starte den rekursiven Ladevorgang
            for i, epic in enumerate(business_epics):
                print(f"\n\n=============================================================\nVerarbeite Business Epic {i+1}/{len(business_epics)}: {epic}")
                # Führe den Ladevorgang für dieses Epic aus
                # Die 'run'-Methode nutzt jetzt den Modus, der bei der Initialisierung gesetzt wurde.
                tree_loader.run(start_key=epic)

        else:
            print("\n--- Ladevorgang übersprungen (Modus: 'none') ---") # <--- Angepasste Ausgabe
        # --- ENDE ANGEPASSTE LADE-LOGIK ---

        # +++ VEREINHEITLICHTE LOGIK FÜR ANALYSE UND REPORTING +++
        if args.html_summary != 'false':
            print("\n--- Analyse / Reporting gestartet ---")

            # Initialisierung der benötigten Clients und Generatoren
            dna_bot_summary_client = DnaBotClient()
            visualizer = JiraTreeVisualizer(format='png')
            context_generator = JiraContextGenerator()

            # --- GEÄNDERT: DnaBotClient wird erstellt und injiziert ---
            logger.info("Initialisiere DnaBotClient für HTML-Generator...")
            dna_bot_html_client = DnaBotClient()  # Den Client für die HTML-Generierung erstellen
            html_generator = EpicHtmlGenerator(
                ai_client=dna_bot_html_client,   # Den Client hier "injizieren"
                model=LLM_MODEL_HTML_GENERATOR,
                token_tracker=token_tracker
            )
            # --- ENDE ÄNDERUNG ---

            json_parser = LLMJsonParser()
            analysis_runner = AnalysisRunner(ANALYZERS_TO_RUN)
            json_summary_generator = JsonSummaryGenerator()
            reporter = ConsoleReporter()


            for epic in business_epics:
                print(f"\n--- Starte Verarbeitung für {epic} ---")
                complete_epic_data = None
                complete_summary_path = os.path.join(JSON_SUMMARY_DIR, f"{epic}_complete_summary.json")

                # Schritt 1: Prüfen, ob eine gecachte Datei verwendet werden soll ('check'-Modus)
                if args.html_summary == 'check' and os.path.exists(complete_summary_path):
                    logger.info(f"Lade vollständige Zusammenfassung aus Cache: {complete_summary_path}")
                    try:
                        with open(complete_summary_path, 'r', encoding='utf-8') as f:
                            complete_epic_data = json.load(f)
                    except (json.JSONDecodeError, IOError) as e:
                        logger.info(f"Konnte Cache-Datei nicht lesen ({e}). Erstelle Zusammenfassung neu.")

                # Schritt 2: Wenn keine Cache-Datei vorhanden oder '--html_summary true', alles neu generieren
                if complete_epic_data is None:
                    logger.info("Keine gültige Cache-Datei gefunden oder Neuerstellung erzwungen. Generiere alle Daten...")

                    # 2a: Metrische Analysen durchführen
                    data_provider = ProjectDataProvider(epic_id=epic, hierarchy_config=JIRA_TREE_FULL)
                    if not data_provider.is_valid():
                        logger.error(f"Fehler: Konnte keine gültigen Daten für Analyse von Epic '{epic}' laden. Verarbeitung wird übersprungen.")
                        continue
                    print(f"     - Erstelle Analyse für {epic}")
                    analysis_results = analysis_runner.run_analyses(data_provider)
                    reporter.create_backlog_plot(analysis_results.get("BacklogAnalyzer", {}), epic)

                    # --- NEUE LOGIK (Phase 2: Pruning) ---

                    # 1. Erstelle IMMER den vollen Management-Baum als Datenbasis
                    logger.info(f"Erstelle vollständigen Baum (Datenbasis) für {epic} mit JIRA_TREE_MANAGEMENT.")
                    data_provider = ProjectDataProvider(epic_id=epic, hierarchy_config=JIRA_TREE_MANAGEMENT)
                    issue_graph_data = data_provider.issue_tree
                    if issue_graph_data is None:
                         logger.warning(f"Konnte keine Graph-Daten für {epic} erstellen (Root-Key fehlt?). Überspringe Visualisierung und Summary.")
                         continue

                    # 2. Erstelle die Visualisierung (nutzt denselben Graphen)
                    visualizer.visualize(issue_graph_data, epic)

                    # 3. Erstelle den intelligent gekürzten JSON-Kontext
                    # Der Generator kümmert sich selbst um das Kürzen und Logging.

                    json_context = context_generator.generate_context(
                        G=issue_graph_data,
                        root_key=epic,
                        max_token_budget=MAX_TOKEN_BUDGET_FOR_SUMMARY
                    )

                    if json_context == "{}":
                        logger.error(f"Konnte LLM-Kontext für {epic} nicht erstellen (selbst Root zu groß). Überspringe Summary-Generierung.")
                        continue
                    summary_prompt_template = load_prompt("summary_prompt.yaml", "user_prompt_template")
                    summary_prompt = summary_prompt_template.format(json_context=json_context)
                    print(f"     - Erstelle Summary für {epic} - Promptlänge ca {len(summary_prompt)/4:,.0f} Token")

                    # 1. Generator abrufen
                    response_generator = dna_bot_summary_client.completion(
                        model_name=LLM_MODEL_SUMMARY,
                        user_prompt=summary_prompt,
                        max_tokens=60000,
                        stream=True, # vermeidet time out fehler bei großen antworten
                        response_format={"type": "json_object"}
                    )

                    # 2. Stream konsumieren und Text zusammensetzen
                    # (Hierbei werden im Client automatisch <think>-Tags gefiltert und Usage-Daten gesammelt)
                    full_response_text = ""
                    for chunk in response_generator:
                        full_response_text += chunk

                    # 3. Usage-Daten aus dem Client-Status abrufen (erst NACH dem Stream verfügbar)
                    usage = dna_bot_summary_client.last_stream_usage

                    # 4. Token-Usage protokollieren
                    if token_tracker and usage:
                        token_tracker.log_usage(
                            model=LLM_MODEL_SUMMARY,
                            input_tokens=usage.get('prompt_tokens', 0),
                            output_tokens=usage.get('completion_tokens', 0),
                            total_tokens=usage.get('total_tokens', 0),
                            task_name=f"summary_generation"
                        )

                    # 5. JSON parsen (mit dem vollständig zusammengesetzten Text)
                    content_summary = json_parser.extract_and_parse_json(full_response_text)

                    epic_status = data_provider.issue_details.get(epic, {}).get('status', 'Unbekannt')
                    target_start_status = data_provider.issue_details.get(epic, {}).get('target_start', 'Unbekannt')
                    target_end_status = data_provider.issue_details.get(epic, {}).get('target_end', 'Unbekannt')
                    fix_version_status = data_provider.issue_details.get(epic, {}).get('fix_versions', 'Unbekannt')
                    ordered_content_summary = {"epicId": content_summary.get("epicId"), "title": content_summary.get("title"), "status": epic_status, "target_start": target_start_status, "target_end": target_end_status, "fix_versions": fix_version_status}
                    ordered_content_summary.update({k: v for k, v in content_summary.items() if k not in ordered_content_summary})
                    content_summary = ordered_content_summary

                    # 2c: Alle Daten fusionieren und als eine JSON-Datei speichern
                    complete_epic_data = json_summary_generator.generate_and_save_complete_summary(analysis_results=analysis_results, content_summary=content_summary, epic_id=epic)

                # Schritt 3: HTML-Datei aus den vollständigen Daten (neu oder aus Cache) erstellen
                if complete_epic_data:
                    print(f"     - Erstelle HTML-File für {epic}")
                    logger.info(f"Erstelle HTML-Report für {epic}...")
                    html_file = os.path.join(HTML_REPORTS_DIR, f"{epic}_summary.html")
                    html_generator.generate_epic_html(complete_epic_data, epic, html_file)
                else:
                    logger.error(f"Konnte keine vollständigen Daten für die HTML-Erstellung von {epic} erzeugen.")

                if args.translate != 'false':
                    # Eigener AI Client für den Übersetzer, da der System-Prompt spezialisiert ist.
                    # Der HtmlTranslator setzt den korrekten System-Prompt selbst.
                    dna_bot_translator_client = DnaBotClient()
                    html_translator = HtmlTranslator(
                        ai_client=dna_bot_translator_client,
                        token_tracker=token_tracker,
                        model_name=LLM_MODEL_TRANSLATOR
                    )

                    german_html_path = os.path.join(HTML_REPORTS_DIR, f"{epic}_summary.html")
                    english_html_path = os.path.join(HTML_REPORTS_DIR, f"{epic}_summary_englisch.html")

                    # WICHTIG: Prüfen, ob die deutsche Quelldatei existiert.
                    if not os.path.exists(german_html_path):
                        logger.warning(f"Übersetzung für {epic} übersprungen, da die deutsche HTML-Datei nicht existiert.")
                    else:
                        run_translation = False
                        if args.translate == 'true':
                            run_translation = True
                            logger.info(f"Übersetzung für {epic} wird erzwungen ('--translate true').")

                        elif args.translate == 'check':
                            if not os.path.exists(english_html_path):
                                run_translation = True
                                logger.info(f"Englische Version für {epic} existiert nicht. Starte Übersetzung ('--translate check').")
                            else:
                                logger.info(f"Englische Version für {epic} existiert bereits. Übersetzung wird übersprungen.")

                        if run_translation:
                            try:
                                print(f"     - Übersetze HTML-File für {epic}")
                                html_translator.translate_file(epic)
                            except Exception as e:
                                logger.error(f"Fehler bei der Übersetzung von {epic}: {e}")

        else:
            print("\n--- Analyse und HTML-Summary übersprungen ---")

    finally:
        logger.info("Hauptprogramm wird beendet. Stoppe den Keep-Awake-Thread...")
        stop_event.set()
        keep_awake_thread.join(timeout=2)


if __name__ == "__main__":
    main()
