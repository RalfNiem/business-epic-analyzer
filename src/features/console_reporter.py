"""
Verantwortlich für die Darstellung von Analyseergebnissen auf der Konsole
und die Erzeugung von visuellen Plots.

Diese Klasse dient als primäre Ausgabeschicht für die Analyse-Pipeline. Sie nimmt
die strukturierten Ergebnis-Dictionaries der verschiedenen Analyzer entgegen und
bereitet sie in einer für den Endbenutzer verständlichen, textbasierten Form für
die Kommandozeile auf.

Zusätzlich zur reinen Textausgabe ist der `ConsoleReporter` auch dafür zuständig,
komplexere Daten in visuelle Formate umzuwandeln. Er nutzt die `matplotlib`-Bibliothek,
um aussagekräftige Graphen und Diagramme zu erstellen (z.B. Status-Timelines oder
Backlog-Entwicklungs-Charts) und diese als Bilddateien im `plots`-Verzeichnis zu
speichern. Dies ermöglicht eine tiefere, visuelle Analyse der Projektdaten, die
über eine reine Textdarstellung hinausgeht.

Hauptaufgaben:
-   Strukturierte und formatierte Ausgabe der Ergebnisse von `ScopeAnalyzer`,
    `StatusAnalyzer`, `TimeCreepAnalyzer` etc. auf der Konsole.
-   Verwendung von Hilfsfunktionen zur menschenlesbaren Formatierung von Daten
    (z.B. Zeitspannen, Datumsformate).
-   Generierung und Speicherung von Plots zur Visualisierung von:
    -   Status-Timelines.
    -   Backlog-Entwicklung über die Zeit.
    -   Aktivitäts-Dashboards, die verschiedene Metriken kombinieren.
"""

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
from datetime import datetime, timedelta, date #
import os
import json
import networkx as nx
import re

from utils.config import LLM_MODEL_TIME_CREEP, TOKEN_LOG_FILE, PLOT_DIR
from utils.logger_config import logger
from utils.project_data_provider import ProjectDataProvider # Import ProjectDataProvider
from utils.azure_ai_client import AzureAIClient # Import AzureAIClient
from utils.prompt_loader import load_prompt_template # Import load_prompt_template
from utils.token_usage_class import TokenUsage # Import TokenUsage
from utils.formatting_helpers import format_timedelta_to_months_days, calculate_duration_string, format_iso_to_dd_mm_yyyy

class ConsoleReporter:
    """
    Verantwortlich für die Darstellung von Analyseergebnissen auf der Konsole
    und die Erzeugung von visuellen Plots.
    """

    def __init__(self):
        # Initialize TokenUsage here so it can be passed to AzureAIClient
        self.token_tracker = TokenUsage(log_file_path=TOKEN_LOG_FILE)
        # AzureAIClient instance, system prompt can be general or specific
        self.azure_summary_client = AzureAIClient(system_prompt="Du bist ein hilfreicher Assistent für die Analyse von Jira-Tickets.")


    def report_scope(self, scope_results: dict):
        """Gibt die Ergebnisse der Umfang- & Aufwand-Analyse aus."""
        print("\n--- Analyse von Umfang und Aufwand ---")
        for epic_key, children in scope_results['epic_breakdown'].items():
            print(f"Epic {epic_key}")
            for child in children:
                if child['type'] == 'Story':
                    print(f"  Story {child['key']} ({child['points']} Pts., Resolution={child['resolution']})")
                else:
                    print(f"  Bug {child['key']}")
        print("\n--- Zusammenfassung Umfang & Aufwand ---")
        print(f"- Gesamtzahl aller Issues im Baum: {scope_results.get('total_issues', 0)}")
        print(f"- Gesamtzahl gefundener Epics: {scope_results['total_epics_found']}")
        print(f"- Gesamtzahl gefundener Stories: {scope_results['total_stories_found']}")
        project_count = scope_results.get('project_count', 0)
        print(f"- Anzahl beteiligter Jira-Projekte (ohne BE): {project_count}")
        if project_count > 0:
            print("  -> Verteilung auf die Projekte:")
            sorted_projects = sorted(scope_results.get('project_distribution', {}).items(), key=lambda item: item[1], reverse=True)
            for project, count in sorted_projects:
                plural = "s" if count > 1 else ""
                print(f"     - {project:<15} | {count} Issue{plural}")
        print(f"- Gesamtsumme der Story Points: {scope_results['total_story_points']}")

    def report_dynamics(self, dynamics_results: dict):
        """Gibt die Ergebnisse der Projektdynamik-Analyse aus."""
        print("\n--- Analyse der Projektdynamik ---")
        metadata = dynamics_results.get("analysis_metadata", {})
        print(json.dumps(metadata, indent=4, default=str))

    def report_status(self, status_results: dict, epic_id: str):
        """Gibt die Ergebnisse der Status-Analyse aus."""
        print("\n--- Analyse der Statuswechsel und Laufzeiten ---")
        print(f"\n--- Verweildauer des Epics '{epic_id}' in den Ziel-Status ---")
        durations = status_results.get('epic_status_durations', {})
        for status, duration in durations.items():
            if duration.total_seconds() > 0:
                print(f"- {status:<25}: {format_timedelta_to_months_days(duration)}")

        print("\n--- Coding-Laufzeit (basiert auf Story-Status) ---")
        start = status_results.get('coding_start_time')
        end = status_results.get('coding_end_time')
        start_str = format_iso_to_dd_mm_yyyy(start) if start else 'Nicht gefunden'
        end_str = format_iso_to_dd_mm_yyyy(end) if end else 'Nicht gefunden'
        print(f"- Coding-Start (erste Story 'In Progress'): {start_str}")
        print(f"- Coding-Ende (letzte Story 'Resolved/Closed'): {end_str}")

        if start and end:
            duration_str = calculate_duration_string(start, end)
            print(f"- Coding-Laufzeit: {duration_str}")
        else:
            print("- Coding-Laufzeit: Nicht berechenbar")

    def report_backlog(self, backlog_results: dict):
        """Gibt die Ergebnisse der Backlog-Analyse aus."""
        print("\n--- Analyse der Backlog-Entwicklung (Stories) ---")
        if backlog_results.get("error"):
            print(backlog_results["error"])
            return

        start_time = backlog_results.get('coding_start_time')
        finish_time = backlog_results.get('coding_finish_time')

        start_str = format_iso_to_dd_mm_yyyy(start_time) if start_time else "Nicht begonnen"
        finish_str = format_iso_to_dd_mm_yyyy(finish_time) if finish_time else "Offen"

        print(f"- Refinement-Start (erste Story in 'Refinement'): {start_str}")
        print(f"- Refinement-Ende (letzte Story in 'Resolved/Closed'): {finish_str}")

    def report_time_creep(self, time_creep_results: dict):
        """
        Gibt die Ergebnisse der Time-Creep-Analyse aus dem Issue-Baum aus
        und die bereits generierte LLM-Zusammenfassung.
        """
        print("\n--- Analyse der Terminverschiebungen (TIME_CREEP) ---")

        all_events = time_creep_results.get('time_creep_events', [])

        if all_events:
            all_events.sort(key=lambda x: x['timestamp'])
            for event in all_events:
                print(f"{event['timestamp']} | {event['issue']:<15} | {event['event_type']:<12} | {event['details']}")
        else:
            print("Keine relevanten Terminänderungen für strategische Issues gefunden.")

        # --- LLM-Zusammenfassung (wird jetzt nur noch abgerufen) ---
        llm_summary_text = time_creep_results.get('llm_time_creep_summary', "Keine LLM-Zusammenfassung verfügbar.")

        print("\n--- LLM-Zusammenfassung der Terminverschiebungen ---")
        print(llm_summary_text)


    def create_status_timeline_plot(self, status_changes: list, epic_id: str, all_activities: list):
        """Erstellt und speichert eine visuelle Timeline der Statuswechsel."""
        print(f"\nErstelle Status-Timeline-Plot für {epic_id}...")
        output_path = os.path.join(PLOT_DIR, f"{epic_id}_status_timeline.png")
        plt.figure(figsize=(10, 2))
        plt.text(0.5, 0.5, 'Status Timeline Plot', ha='center', va='center')
        plt.savefig(output_path, dpi=150)
        plt.close()
        logger.info(f"Status-Timeline-Grafik gespeichert unter: {output_path}")

    def create_backlog_plot(self, backlog_results: dict, epic_id: str):
        """Erstellt und speichert einen Graphen der Backlog-Entwicklung."""
        if backlog_results.get("error"):
            return # Don't create a plot if there was an error

        logger.info(f"Erstelle Backlog-Entwicklungs-Plot für {epic_id}...")
        results_df = backlog_results["results_df"]

        if results_df.empty:
            logger.warning(f"Keine Daten für Backlog-Plot von Epic {epic_id} vorhanden.")
            return

        fig, ax = plt.subplots(figsize=(12, 7))

        ax.plot(results_df.index, results_df['refined_backlog'], label='Refined Backlog (Kumulativ)', color='blue', linestyle='--')
        ax.plot(results_df.index, results_df['finished_backlog'], label='Finished Backlog (Kumulativ)', color='green')
        ax.fill_between(results_df.index, results_df['finished_backlog'], results_df['refined_backlog'],
                        color='orange', alpha=0.3, label='Active Backlog')

        # Formatting
        ax.set_title(f'Backlog-Entwicklung für Epic {epic_id}')
        ax.set_xlabel('Datum')
        ax.set_ylabel('Anzahl Stories')
        ax.legend()
        ax.grid(True, which='both', linestyle='--', linewidth=0.5)

        # Improve date formatting
        fig.autofmt_xdate()
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m.%Y'))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())

        plt.tight_layout()

        output_path = os.path.join(PLOT_DIR, f"{epic_id}_backlog_development.png")
        try:
            plt.savefig(output_path, dpi=150)
            logger.info(f"Backlog-Entwicklungs-Grafik gespeichert unter: {output_path}")
        except Exception as e:
            logger.error(f"Fehler beim Speichern des Backlog-Plots: {e}")
        finally:
            plt.close(fig)

    def create_activity_and_creep_plot(self, time_creep_results: dict, all_activities: list, epic_id: str):
        """Erstellt eine kombinierte Dashboard-Grafik."""
        print(f"Erstelle Aktivitäts-Dashboard für {epic_id}...")

        time_creep_events = time_creep_results.get('time_creep_events', [])

        output_path = os.path.join(PLOT_DIR, f"{epic_id}_activity_creep_dashboard.png")
        plt.figure(figsize=(10, 5))
        plt.text(0.5, 0.5, 'Activity & Creep Dashboard', ha='center', va='center')
        plt.savefig(output_path, dpi=150)
        plt.close()
        logger.info(f"Aktivitäts-Dashboard-Grafik gespeichert unter: {output_path}")
