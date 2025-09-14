"""
Analysiert die Entwicklung des Story-Backlogs über die Zeit.

Diese Klasse bietet eine quantitative Analyse, wie sich der Backlog von User Stories
innerhalb eines Projekts entwickelt hat. Sie ermittelt, wann Stories erstellt
wurden (als Indikator für den "Zufluss" in den Backlog) und wann sie abgeschlossen
wurden (als Indikator für den "Abfluss").

Die Analyseergebnisse werden in einem täglichen Zeitreihenformat aufbereitet,
das die kumulative Anzahl der erstellten und abgeschlossenen Stories sowie
den daraus resultierenden "aktiven Backlog" (die Differenz zwischen beiden)
zeigt. Diese Daten sind ideal für die Erstellung von "Cumulative Flow Diagrams"
oder ähnlichen Visualisierungen, die Einblicke in den Projektdurchsatz und
mögliche Engpässe geben.

Kernlogik:
-   Identifiziert alle Issues vom Typ 'Story' innerhalb des Projekts.
-   Ermittelt für jede Story den exakten Zeitpunkt der ersten Aktivität (Erstellung)
    und den Zeitpunkt des ersten Wechsels in einen Endstatus ('Resolved' oder 'Closed').
-   Berechnet die globalen Start- und Endzeitpunkte des gesamten Refinements.
-   Erstellt ein pandas DataFrame, das für jeden Tag im Projektzeitraum die Anzahl
    der neu erstellten und abgeschlossenen Stories erfasst.
-   Berechnet die kumulativen Summen, um den Verlauf des Gesamt-Backlogs darzustellen.
"""

import pandas as pd
from datetime import datetime, date
from utils.project_data_provider import ProjectDataProvider
from utils.logger_config import logger

class BacklogAnalyzer:
    """
    Analysiert die Entwicklung des Story-Backlogs über die Zeit.

    Ermittelt, wann Stories für das Refinement bereitgestellt, wann sie
    abgeschlossen wurden und wie sich der aktive Backlog daraus entwickelt.
    """

    def analyze(self, data_provider: ProjectDataProvider) -> dict:
        """
        Führt die Backlog-Analyse durch.

        Args:
            data_provider (ProjectDataProvider): Die zentrale Datenquelle für das Projekt.

        Returns:
            dict: Ein Dictionary mit den Analyseergebnissen, einschließlich
                  globaler Zeitpunkte und der aufbereiteten Daten für die Visualisierung.
        """
        logger.info("Starte Backlog-Analyse...")

        # 1. Stories identifizieren
        story_keys = {
            key for key, details in data_provider.issue_details.items()
            if details.get('type') == 'Story'
        }

        if not story_keys:
            logger.warning("Keine Stories im Projekt gefunden. Backlog-Analyse wird übersprungen.")
            return {"error": "Keine Stories gefunden"}

        # 2. Zeitpunkte pro Story ermitteln
        story_times = {key: {'start_time': None, 'finish_time': None} for key in story_keys}

        # data_provider.all_activities ist bereits nach Zeitstempel sortiert
        for activity in data_provider.all_activities:
            issue_key = activity.get('issue_key')
            if issue_key not in story_keys:
                continue

            timestamp = datetime.fromisoformat(activity['zeitstempel_iso'])

            # +++ NEUE LOGIK: start_time ist die erste Aktivität der Story +++
            if not story_times[issue_key]['start_time']:
                story_times[issue_key]['start_time'] = timestamp

            # Logik für finish_time bleibt bestehen: Erster RESOLVED- oder CLOSED-Zeitpunkt
            if activity.get('feld_name') == 'Status':
                new_value = activity.get('neuer_wert', '').upper()
                if new_value in ['RESOLVED', 'CLOSED'] and not story_times[issue_key]['finish_time']:
                    story_times[issue_key]['finish_time'] = timestamp

        # 3. Stories ohne jegliche Aktivität (und damit ohne start_time) ignorieren
        valid_stories = {
            key: times for key, times in story_times.items() if times['start_time']
        }

        if not valid_stories:
            logger.warning("Keine Stories mit Aktivitäten gefunden. Backlog-Analyse kann nicht durchgeführt werden.")
            return {"error": "Keine Stories mit Aktivitäten gefunden."}

        # 4. Globale Zeitpunkte berechnen
        all_start_times = [s['start_time'] for s in valid_stories.values()]
        all_finish_times = [s['finish_time'] for s in valid_stories.values() if s['finish_time']]

        coding_start_time = min(all_start_times)
        coding_finish_time = max(all_finish_times) if all_finish_times and len(all_finish_times) == len(valid_stories) else None

        # 5. Daten für Graphen vorbereiten
        end_date = coding_finish_time.date() if coding_finish_time else date.today()
        date_index = pd.to_datetime(pd.date_range(start=coding_start_time.date(), end=end_date, freq='D'))

        results_df = pd.DataFrame(0, index=date_index, columns=['refined', 'finished'])

        for story, times in valid_stories.items():
            start_date = times['start_time'].date()
            if pd.Timestamp(start_date) in results_df.index:
                results_df.loc[pd.Timestamp(start_date), 'refined'] += 1

            if times['finish_time']:
                finish_date = times['finish_time'].date()
                if pd.Timestamp(finish_date) in results_df.index:
                    results_df.loc[pd.Timestamp(finish_date), 'finished'] += 1

        # Kumulative Summen berechnen
        results_df['refined_backlog'] = results_df['refined'].cumsum()
        results_df['finished_backlog'] = results_df['finished'].cumsum()
        results_df['active_backlog'] = results_df['refined_backlog'] - results_df['finished_backlog']

        logger.info(f"Backlog-Analyse erfolgreich abgeschlossen. {len(valid_stories)} Stories berücksichtigt.")

        return {
            "coding_start_time": coding_start_time.isoformat(),
            "coding_finish_time": coding_finish_time.isoformat() if coding_finish_time else None,
            "results_df": results_df
        }
