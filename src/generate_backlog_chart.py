# src/generate_backlog_chart.py
import os
import sys
import argparse
import pandas as pd
from datetime import datetime

# Fügt das Projekt-Root-Verzeichnis zum Python-Pfad hinzu, um utils und features zu finden
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.project_data_provider import ProjectDataProvider
from features.backlog_analyzer import BacklogAnalyzer
from features.console_reporter import ConsoleReporter
from utils.config import JIRA_TREE_FULL
from utils.logger_config import logger

def main():
    """
    Hauptfunktion zur Orchestrierung der Backlog-Chart-Generierung.
    """
    parser = argparse.ArgumentParser(
        description="Generiert eine eigenständige Backlog-Entwicklungsübersicht für ein Jira Issue."
    )
    parser.add_argument(
        "--issue",
        type=str,
        required=True,
        help="Der Jira-Key des Business Epics (z.B. 'BEB2B-431')."
    )
    parser.add_argument(
        "--start_date",
        type=str,
        default="01-01-2025",
        help="Optionales Startdatum für den Graphen im Format DD-MM-YYYY oder DD.MM.YYYY. Standard: 01-01-2025."
    )
    parser.add_argument(
        "--end_date",
        type=str,
        default=None,
        help="Optionales Enddatum für den Graphen im Format DD-MM-YYYY oder DD.MM.YYYY. Standard: Aktuelles Datum."
    )
    args = parser.parse_args()

    if args.end_date is None:
        args.end_date = datetime.now().strftime('%d-%m-%Y')
        logger.info(f"Kein Enddatum angegeben. Verwende aktuelles Datum: {args.end_date}")


    logger.info(f"Starte eigenständige Backlog-Analyse für Epic: {args.issue}")

    # 1. Daten-Provider initialisieren
    data_provider = ProjectDataProvider(epic_id=args.issue, hierarchy_config=JIRA_TREE_FULL)
    if not data_provider.is_valid():
        logger.error(f"Konnte keine gültigen Daten für das Epic '{args.issue}' laden. Skript wird beendet.")
        return

    # 2. Backlog-Analyse ausführen
    backlog_analyzer = BacklogAnalyzer()
    backlog_results = backlog_analyzer.analyze(data_provider)

    if backlog_results.get("error"):
        logger.error(f"Fehler bei der Backlog-Analyse: {backlog_results['error']}")
        return

    # 3. DataFrame basierend auf den Zeit-Argumenten filtern
    results_df = backlog_results["results_df"]

    try:
        # KORREKTUR: Ersetze Punkte durch Bindestriche, bevor das Datum konvertiert wird
        if args.start_date:
            start_date_str = args.start_date.replace('.', '-')
            start_ts = pd.to_datetime(start_date_str, format='%d-%m-%Y')
            results_df = results_df[results_df.index >= start_ts]
            logger.info(f"Zeitraum auf Startdatum {args.start_date} eingeschränkt.")

        if args.end_date:
            end_date_str = args.end_date.replace('.', '-')
            end_ts = pd.to_datetime(end_date_str, format='%d-%m-%Y')
            results_df = results_df[results_df.index <= end_ts]
            logger.info(f"Zeitraum auf Enddatum {args.end_date} eingeschränkt.")

        if results_df.empty:
            logger.warning("Nach der Filterung nach Datum sind keine Daten mehr vorhanden. Es wird kein Chart erstellt.")
            return

        backlog_results["results_df"] = results_df

    except ValueError:
        logger.error(f"Fehler beim Parsen der Datumsangaben. Bitte das Format DD-MM-YYYY oder DD.MM.YYYY verwenden.")
        return
    except Exception as e:
        logger.error(f"Ein unerwarteter Fehler ist aufgetreten: {e}")
        return

    # 4. Plot-Generator aufrufen
    reporter = ConsoleReporter()
    reporter.create_backlog_plot(backlog_results, args.issue)

    logger.info(f"Backlog-Chart für {args.issue} wurde erfolgreich erstellt und im 'data/plots'-Verzeichnis gespeichert.")


if __name__ == "__main__":
    main()
