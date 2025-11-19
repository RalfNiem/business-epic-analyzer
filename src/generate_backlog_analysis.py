# src/generate_backlog_analysis.py
"""
Orchestriert den gesamten Prozess der Backlog-Analyse und Berichterstattung.

Diese Hauptfunktion dient als Einstiegspunkt des Skripts und steuert den
gesamten Workflow von der Datenerfassung bis zur Ausgabe der Ergebnisse.
Der Prozess umfasst die folgenden Schritte:

1.  **Argumenten-Parsing**: Verarbeitet Kommandozeilenargumente, um
    den Jira-Key des zu analysierenden Epics (`--issue`) sowie einen
    optionalen Analysezeitraum (`--start_date`, `--end_date`) zu erhalten.

2.  **Datenbeschaffung**: Initialisiert den `ProjectDataProvider`, um alle
    relevanten Issue-Daten, einschließlich der Hierarchie unterhalb des
    angegebenen Epics, aus den lokalen JSON-Dateien zu laden.

3.  **Backlog-Analyse**: Führt mit dem `BacklogAnalyzer` eine detaillierte
    Analyse der geladenen Daten durch. Dabei werden die Erstellungs- und
    Abschlusszeitpunkte für alle Stories und Bugs ermittelt und in einer
    Zeitreihe zusammengefasst.

4.  **Zeitliche Filterung**: Filtert die Analyseergebnisse auf den vom
    Benutzer definierten Zeitraum.

5.  **Konsolenausgabe**:
    -   Erstellt und zeigt ein ASCII-basiertes Diagramm des Backlog-Verlaufs
        (erstellt, abgeschlossen, aktiv) direkt in der Konsole an.
    -   Gibt tabellarische Zusammenfassungen der Metriken aus:
        -   Monatliche und quartalsweise Übersicht.
        -   Gesamtzahlen für neue und abgeschlossene Stories/Bugs.
        -   Eine nach Anzahl sortierte Aufschlüsselung der abgeschlossenen
            Stories nach ihren jeweiligen Jira-Projekten.

6.  **HTML-Berichterstellung**: Generiert einen eigenständigen HTML-Bericht,
    der detaillierte, klickbare Listen aller neuen und abgeschlossenen
    Stories und Bugs im Analysezeitraum enthält. Diese Datei wird im
    Projekt-Root-Verzeichnis gespeichert.

Die Funktion hat keine Rückgabewerte, ihre Ausführung resultiert in
Ausgaben auf der Konsole und der Erstellung einer HTML-Datei.
"""

import os
import sys
import argparse
import pandas as pd
from datetime import datetime
from collections import Counter

# Fügt das Projekt-Root-Verzeichnis zum Python-Pfad hinzu, um utils und features zu finden
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.project_data_provider import ProjectDataProvider
from features.backlog_analyzer import BacklogAnalyzer
from features.console_reporter import ConsoleReporter
from utils.config import JIRA_TREE_FULL
from utils.logger_config import logger

def generate_html_report(issue_key, detailed_issues, story_keys, bug_keys, start_ts, end_ts):
    """
    Erstellt einen HTML-Bericht mit Listen der Jira-Keys für die Analyse.
    """
    new_stories, closed_stories, new_bugs, closed_bugs = [], [], [], []

    for key, times in detailed_issues.items():
        # KORREKTUR: Mache die aware datetime-Objekte naive, um den Vergleich zu ermöglichen
        start_time_naive = times['start_time'].replace(tzinfo=None)

        # Neue Stories und Bugs im Zeitraum
        if start_ts <= start_time_naive <= end_ts:
            if key in story_keys:
                new_stories.append((key, times['start_time']))
            elif key in bug_keys:
                new_bugs.append((key, times['start_time']))

        # Abgeschlossene Issues im Zeitraum
        if times['finish_time'] and pd.notna(times['finish_time']):
            finish_time_naive = times['finish_time'].replace(tzinfo=None)
            if start_ts <= finish_time_naive <= end_ts:
                if key in story_keys:
                    closed_stories.append((key, times['finish_time']))
                elif key in bug_keys:
                    closed_bugs.append((key, times['finish_time']))

    # Sortiere die Listen absteigend nach Datum
    new_stories.sort(key=lambda x: x[1], reverse=True)
    closed_stories.sort(key=lambda x: x[1], reverse=True)
    new_bugs.sort(key=lambda x: x[1], reverse=True)
    closed_bugs.sort(key=lambda x: x[1], reverse=True)

    # Erstelle den HTML-Inhalt
    html_content = f"""
    <html>
    <head>
        <title>Backlog-Analyse für {issue_key}</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            h1, h2 {{ color: #333; }}
            ul {{ list-style-type: none; padding-left: 0; }}
            li {{ margin-bottom: 5px; }}
            a {{ color: #0052cc; text-decoration: none; }}
            a:hover {{ text-decoration: underline; }}
        </style>
    </head>
    <body>
        <h1>Backlog-Analyse für {issue_key}</h1>

        <h2>1) Neue Stories ({len(new_stories)})</h2>
        <ul>
            {''.join(f'<li><a href="https://jira.telekom.de/browse/{key}" target="_blank">{key}</a> - Erstellt am: {date.strftime("%d.%m.%Y")}</li>' for key, date in new_stories)}
        </ul>

        <h2>2) Abgeschlossene Stories ({len(closed_stories)})</h2>
        <ul>
            {''.join(f'<li><a href="https://jira.telekom.de/browse/{key}" target="_blank">{key}</a> - Abgeschlossen am: {date.strftime("%d.%m.%Y")}</li>' for key, date in closed_stories)}
        </ul>

        <h2>3) Neue Bugs ({len(new_bugs)})</h2>
        <ul>
            {''.join(f'<li><a href="https://jira.telekom.de/browse/{key}" target="_blank">{key}</a> - Erstellt am: {date.strftime("%d.%m.%Y")}</li>' for key, date in new_bugs)}
        </ul>

        <h2>4) Abgeschlossene Bugs ({len(closed_bugs)})</h2>
        <ul>
            {''.join(f'<li><a href="https://jira.telekom.de/browse/{key}" target="_blank">{key}</a> - Abgeschlossen am: {date.strftime("%d.%m.%Y")}</li>' for key, date in closed_bugs)}
        </ul>
    </body>
    </html>
    """

    # Speichere die HTML-Datei
    date_str = datetime.now().strftime('%y-%m-%d')
    filename = f"{date_str}_{issue_key}_Backloganalyse.html"
    output_path = os.path.join(project_root, filename) # Speichert im Hauptverzeichnis

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        logger.info(f"HTML-Bericht erfolgreich gespeichert: {output_path}")
    except Exception as e:
        logger.error(f"Fehler beim Speichern des HTML-Berichts: {e}")

def main():
    """
    Hauptfunktion zur Orchestrierung der Backlog-Chart-Generierung und Tabellenausgabe.
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

    # 2. Backlog-Analyse ausführen (liefert jetzt detaillierte Spalten)
    backlog_analyzer = BacklogAnalyzer()
    backlog_results = backlog_analyzer.analyze(data_provider)

    if backlog_results.get("error"):
        logger.error(f"Fehler bei der Backlog-Analyse: {backlog_results['error']}")
        return

    # 3. DataFrame basierend auf den Zeit-Argumenten filtern
    results_df = backlog_results["results_df"]
    start_ts, end_ts = None, None
    try:
        start_date_str = args.start_date.replace('.', '-')
        start_ts = pd.to_datetime(start_date_str, format='%d-%m-%Y')
        results_df = results_df[results_df.index >= start_ts]

        end_date_str = args.end_date.replace('.', '-')
        end_ts = pd.to_datetime(end_date_str, format='%d-%m-%Y')
        results_df = results_df[results_df.index <= end_ts]

        if results_df.empty:
            logger.warning("Nach der Filterung nach Datum sind keine Daten mehr vorhanden.")
            return

        plot_df = results_df[['refined_story_backlog', 'finished_story_backlog', 'active_story_backlog']].copy()
        plot_df.rename(columns={'refined_story_backlog': 'refined_backlog', 'finished_story_backlog': 'finished_backlog', 'active_story_backlog': 'active_backlog'}, inplace=True)
        plotter_payload = {"results_df": plot_df}

    except Exception as e:
        logger.error(f"Ein unerwarteter Fehler ist aufgetreten: {e}")
        return

    # 4. Plot-Generator aufrufen
    reporter = ConsoleReporter()
    reporter.create_backlog_plot(plotter_payload, args.issue)
    logger.info(f"Backlog-Chart für {args.issue} wurde erfolgreich erstellt.")

    # --- Tabellarische Zusammenfassung mit der neuen Logik ---
    print("\n" + "="*80)
    print(f"    Tabellarische Übersicht für Epic: {args.issue}")
    print("="*80)

    print(f"\n--- Analysierter Zeitraum ---\n- Start Date: {args.start_date}\n- End Date: {args.end_date}")

    agg_df = results_df[['refined_stories', 'finished_stories', 'finished_bugs']]

    monthly_summary = agg_df.resample('ME').sum()
    monthly_summary = monthly_summary[(monthly_summary.T != 0).any()]
    monthly_summary.index = monthly_summary.index.strftime('%Y-%m')
    monthly_summary.rename(columns={
        'refined_stories': 'Neue Stories', 'finished_stories': 'Abgeschl. Stories',
         'finished_bugs': 'Abgeschl. Bugs'
    }, inplace=True)

    print("\n--- Monatliche Übersicht ---")
    if not monthly_summary.empty: print(monthly_summary.to_string())
    else: print("Keine monatlichen Daten im Zeitraum vorhanden.")

    if not monthly_summary.empty:
        quarterly_summary = agg_df.resample('QE').sum()
        quarterly_summary = quarterly_summary[(quarterly_summary.T != 0).any()]
        quarterly_summary.index = quarterly_summary.index.to_period('Q').strftime('Q%q-%Y')
        quarterly_summary.rename(columns={
            'refined_stories': 'Neue Stories', 'finished_stories': 'Abgeschl. Stories',
             'finished_bugs': 'Abgeschl. Bugs'
        }, inplace=True)
        print("\n--- Quartalsübersicht ---")
        print(quarterly_summary.to_string())

    total_summary = agg_df.sum()
    print("\n--- Gesamtzahlen ---")
    print(f"Total Stories refined:           {total_summary['refined_stories']}")
    print(f"Total Stories closed/resolved:   {total_summary['finished_stories']}")
    print(f"Total Bugs closed/resolved:      {total_summary['finished_bugs']}")

    # --- NEU: Aufschlüsselung der abgeschlossenen Stories nach Jira-Projekt ---
    print("\n--- Jira Projects ---")
    print(f"Total Stories closed/resolved:   {total_summary['finished_stories']}")

    project_counter = Counter()

    # Iteriere durch alle Issues, um die abgeschlossenen Stories im Zeitraum zu finden
    for key, times in backlog_results["detailed_issues"].items():
        if key in backlog_results["story_keys"] and times['finish_time'] and pd.notna(times['finish_time']):
            finish_time_naive = times['finish_time'].replace(tzinfo=None)
            if start_ts <= finish_time_naive <= end_ts:
                # Extrahiere den Projekt-Key (z.B. "SDN" aus "SDN-1234")
                project_key = key.split('-')[0]
                project_counter[project_key] += 1

    # Sortiere die Projekte nach der Anzahl der abgeschlossenen Stories (absteigend)
    sorted_projects = project_counter.most_common()

    if not sorted_projects:
        print("- Keine abgeschlossenen Stories im angegebenen Zeitraum gefunden.")
    else:
        # Finde die maximale Länge des Projektnamens für die Formatierung
        max_len = max(len(proj) for proj, count in sorted_projects)
        for project, count in sorted_projects:
            # {:<{max_len}} sorgt für eine linksbündige Ausrichtung mit dynamischer Breite
            print(f"- {project:<{max_len}}: {count:>4}")

    print("="*80)


    # HTML-Bericht generieren und speichern
    generate_html_report(
        args.issue,
        backlog_results["detailed_issues"],
        backlog_results["story_keys"],
        backlog_results["bug_keys"],
        start_ts,
        end_ts
    )

if __name__ == "__main__":
    main()
