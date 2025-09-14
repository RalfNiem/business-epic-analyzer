"""
Führt eine schnelle, konsolenbasierte Analyse für ein einzelnes Business Epic aus.

Dieses Skript dient als dediziertes Werkzeug für schnelle Ad-hoc-Analysen. Es lädt
alle erforderlichen Daten für ein einzelnes, per Kommandozeile übergebenes
Business Epic und führt die vollständige Suite von verfügbaren Analysemodulen aus.

Im Gegensatz zum Haupt-Workflow in `main_scraper.py` werden die Ergebnisse
nicht in JSON- oder HTML-Dateien gespeichert. Stattdessen werden alle Auswertungen
und Plots direkt auf der Konsole bzw. im `plots`-Verzeichnis ausgegeben, was eine
unmittelbare und fokussierte Analyse ermöglicht.

Funktionsweise:
1.  Nimmt einen Business-Epic-Key als Kommandozeilenargument entgegen.
2.  Nutzt den `ProjectDataProvider`, um alle assoziierten Jira-Daten zu laden.
3.  Initialisiert den `AnalysisRunner` mit allen verfügbaren Analyzer-Klassen.
4.  Führt alle Analysen aus.
5.  Verwendet den `ConsoleReporter`, um die Ergebnisse jeder Analyse übersichtlich
    auf der Konsole darzustellen und die zugehörigen Diagramme zu erstellen.

Usage:
    python src/run_quick_analysis.py --issue BEMABU-1234
"""
import os
import sys
import argparse
import json

# Fügt das Projekt-Root-Verzeichnis zum Python-Pfad hinzu, um utils und features zu finden
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Importiert die notwendigen Bausteine
from utils.project_data_provider import ProjectDataProvider
from utils.config import JIRA_TREE_FULL
from utils.logger_config import logger

from features.analysis_runner import AnalysisRunner
from features.console_reporter import ConsoleReporter

# Importiert alle verfügbaren Analyzer-Klassen
from features.scope_analyzer import ScopeAnalyzer
from features.dynamics_analyzer import DynamicsAnalyzer
from features.status_analyzer import StatusAnalyzer
from features.time_creep_analyzer import TimeCreepAnalyzer
from features.backlog_analyzer import BacklogAnalyzer
#from features.maturity_analyzer import MaturityAnalyzer

# Konfiguriert die Liste aller Analyzer, die ausgeführt werden sollen
ALL_ANALYZERS = [
    ScopeAnalyzer,
    DynamicsAnalyzer,
    StatusAnalyzer,
    TimeCreepAnalyzer,
    BacklogAnalyzer,
    #MaturityAnalyzer,
]

def run_quick_analysis(epic_key: str):
    """
    Orchestriert den Lade-, Analyse- und Reporting-Prozess für ein einzelnes Epic.

    Args:
        epic_key (str): Der Jira-Key des zu analysierenden Business Epics.
    """
    print(f"\n{'='*60}")
    print(f"  Starte schnelle Analyse für Business Epic: {epic_key}")
    print(f"{'='*60}\n")

    # 1. Daten laden
    logger.info(f"Lade Daten für Epic '{epic_key}'...")
    data_provider = ProjectDataProvider(epic_id=epic_key, hierarchy_config=JIRA_TREE_FULL)

    if not data_provider.is_valid():
        logger.error(f"Konnte keine gültigen Daten für das Epic '{epic_key}' laden. Skript wird beendet.")
        return
    logger.info("Daten erfolgreich geladen.")

    # 2. Analysen ausführen
    logger.info(f"Führe {len(ALL_ANALYZERS)} Analysen aus...")
    analysis_runner = AnalysisRunner(analyzer_classes=ALL_ANALYZERS)
    all_results = analysis_runner.run_analyses(data_provider)
    logger.info("Alle Analysen abgeschlossen.")

    # 3. Ergebnisse auf der Konsole ausgeben und Plots erstellen
    reporter = ConsoleReporter()

    # Scope-Analyse
    if 'ScopeAnalyzer' in all_results:
        reporter.report_scope(all_results['ScopeAnalyzer'])

    # Dynamics-Analyse
    if 'DynamicsAnalyzer' in all_results:
        reporter.report_dynamics(all_results['DynamicsAnalyzer'])

    # Status-Analyse
    if 'StatusAnalyzer' in all_results:
        reporter.report_status(all_results['StatusAnalyzer'], epic_key)
        # Plot für die Status-Timeline erstellen
        reporter.create_status_timeline_plot(
            status_changes=all_results['StatusAnalyzer'].get('all_status_changes', []),
            epic_id=epic_key,
            all_activities=data_provider.all_activities
        )

    # Time-Creep-Analyse
    if 'TimeCreepAnalyzer' in all_results:
        reporter.report_time_creep(all_results['TimeCreepAnalyzer'])
        # Plot für das Aktivitäts-Dashboard erstellen
        reporter.create_activity_and_creep_plot(
            time_creep_results=all_results['TimeCreepAnalyzer'],
            all_activities=data_provider.all_activities,
            epic_id=epic_key
        )

    # Backlog-Analyse
    if 'BacklogAnalyzer' in all_results:
        reporter.report_backlog(all_results['BacklogAnalyzer'])
        # Plot für die Backlog-Entwicklung erstellen
        reporter.create_backlog_plot(all_results['BacklogAnalyzer'], epic_key)

    # Maturity-Analyse (Reifegrad)
    if 'MaturityAnalyzer' in all_results:
        print("\n--- Analyse des Reifegrads ---")
        # Da es keine dedizierte Reporter-Funktion gibt, geben wir das JSON formatiert aus
        print(json.dumps(all_results['MaturityAnalyzer'], indent=2, ensure_ascii=False))

    print(f"\n{'='*60}")
    print(f"  Analyse für {epic_key} abgeschlossen.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Führt eine schnelle, konsolenbasierte Analyse für ein Jira Business Epic durch."
    )
    parser.add_argument(
        "--issue",
        type=str,
        required=True,
        help="Der Jira-Key des zu analysierenden Business Epics (z.B. 'BEMABU-1234')."
    )
    args = parser.parse_args()

    run_quick_analysis(args.issue)
