# src/features/backlog_analyzer.py
import pandas as pd
from datetime import datetime, date
from utils.project_data_provider import ProjectDataProvider
from utils.logger_config import logger

class BacklogAnalyzer:
    """
    Analysiert die Entwicklung des Story- und Bug-Backlogs über die Zeit.

    Diese Klasse führt eine quantitative, zeitbasierte Analyse durch, um die
    Dynamik des Backlogs innerhalb eines Projekts zu visualisieren. Sie ermittelt,
    wann Stories und Bugs erstellt wurden (als Indikator für den "Zufluss" in den Backlog)
    und wann sie abgeschlossen wurden (als Indikator für den "Abfluss").

    Die Analyseergebnisse werden in einem täglichen Zeitreihenformat in einem
    pandas DataFrame aufbereitet. Dieser enthält die folgenden Metriken:
    -   `refined_stories`: Anzahl der an einem Tag neu erstellten Stories.
    -   `finished_stories`: Anzahl der an einem Tag abgeschlossenen Stories.
    -   `refined_bugs`: Anzahl der an einem Tag neu erstellten Bugs.
    -   `finished_bugs`: Anzahl der an einem Tag abgeschlossenen Bugs.
    -   `refined_story_backlog`: Die kumulative Summe der erstellten Stories.
    -   `finished_story_backlog`: Die kumulative Summe der abgeschlossenen Stories.
    -   `refined_bug_backlog`: Die kumulative Summe der erstellten Bugs.
    -   `finished_bug_backlog`: Die kumulative Summe der abgeschlossenen Bugs.
    -   `active_story_backlog`: Die Differenz zwischen `refined_story_backlog` und
        `finished_story_backlog`, die den zu jedem Zeitpunkt aktiven Story-Backlog darstellt.
    -   `active_bug_backlog`: Die Differenz zwischen `refined_bug_backlog` und
        `finished_bug_backlog`, die den zu jedem Zeitpunkt aktiven Bug-Backlog darstellt.

    Diese aufbereiteten Daten sind ideal für die Erstellung von "Cumulative Flow Diagrams"
    oder ähnlichen Visualisierungen, die Einblicke in den Projektdurchsatz,
    Engpässe und die allgemeine "Gesundheit" des Backlogs geben.

    Kernlogik der Analyse:
    1.  Identifiziert alle Issues vom Typ 'Story' und 'Bug' innerhalb des Projekts.
    2.  Ermittelt für jedes dieser Issues den exakten Zeitpunkt der Erstellung
        (aus dem 'created'-Feld) und den Zeitpunkt des Abschlusses
        (aus 'closed_date' oder 'resolved').
    3.  Erstellt ein DataFrame, das für jeden Tag im Projektzeitraum die Anzahl
        der neu erstellten und abgeschlossenen Stories/Bugs erfasst.
    4.  Berechnet die kumulativen Summen, um den Verlauf der jeweiligen Backlogs darzustellen.
    """

    def analyze(self, data_provider: ProjectDataProvider) -> dict:
        """
        Führt die Backlog-Analyse für Stories und Bugs durch und liefert sowohl
        aggregierte Daten für Tabellen als auch Rohdaten für Detail-Listen.
        """
        logger.info("Starte erweiterte Backlog-Analyse für Stories und Bugs...")

        # 1. Stories und Bugs identifizieren
        story_keys = {
            key for key, details in data_provider.issue_details.items()
            if details.get('type') == 'Story'
        }
        bug_keys = {
            key for key, details in data_provider.issue_details.items()
            if details.get('type') == 'Bug'
        }
        all_relevant_keys = story_keys.union(bug_keys)

        if not all_relevant_keys:
            logger.warning("Keine Stories oder Bugs im Projekt gefunden. Backlog-Analyse wird übersprungen.")
            return {"error": "Keine Stories oder Bugs gefunden"}

        # --- KORREKTUR START ---
        # 2. Zeitpunkte pro Issue effizient ermitteln (aus dem Cache)
        issue_times = {}

        for key in all_relevant_keys:
            details = data_provider.issue_details.get(key)
            if not details:
                logger.warning(f"Keine Details für {key} gefunden, wird übersprungen.")
                continue

            start_time, finish_time = None, None

            # Startdatum ermitteln
            created_str = details.get("created")
            if created_str:
                try:
                    start_time = datetime.fromisoformat(created_str)
                except (ValueError, TypeError):
                    logger.warning(f"Ungültiges 'created'-Datum für {key}: {created_str}")
                    continue # Ohne Startdatum ist das Issue nutzlos
            else:
                logger.warning(f"Kein 'created'-Datum für {key} gefunden.")
                continue # Ohne Startdatum ist das Issue nutzlos

            # Enddatum ermitteln
            closed_str = details.get("closed_date")
            resolved_str = details.get("resolved")

            end_date_str = closed_str if closed_str else resolved_str

            if end_date_str:
                try:
                    finish_time = datetime.fromisoformat(end_date_str)
                except (ValueError, TypeError):
                    logger.warning(f"Ungültiges 'End'-Datum für {key}: {end_date_str}")
                    pass # finish_time bleibt None

            issue_times[key] = {'start_time': start_time, 'finish_time': finish_time}

        # 3. Issues ohne Aktivität ignorieren (redundant, da wir oben 'continue' nutzen, aber sicher ist sicher)
        valid_issues = {
            key: times for key, times in issue_times.items() if times['start_time']
        }
        # --- KORREKTUR ENDE ---


        if not valid_issues:
            logger.warning("Keine Issues mit gültigen Zeitstempeln gefunden. Backlog-Analyse kann nicht durchgeführt werden.")
            return {"error": "Keine Issues mit gültigen Zeitstempeln gefunden."}

        # 4. Globale Zeitpunkte berechnen (basierend auf allen Issues)
        all_start_times = [s['start_time'] for s in valid_issues.values()]
        all_finish_times = [s['finish_time'] for s in valid_issues.values() if s['finish_time'] and pd.notna(s['finish_time'])]

        coding_start_time = min(all_start_times) if all_start_times else datetime.now().astimezone()
        coding_finish_time = max(all_finish_times) if all_finish_times else None

        # 5. Daten für Graphen und Tabellen vorbereiten
        end_date_obj = coding_finish_time.date() if coding_finish_time else date.today()
        # Sicherstellen, dass das Enddatum mindestens das Startdatum ist
        start_date_obj = coding_start_time.date()
        if end_date_obj < start_date_obj:
            end_date_obj = start_date_obj

        date_index = pd.to_datetime(pd.date_range(start=start_date_obj, end=end_date_obj, freq='D'))

        # Spalten für die erweiterte Analyse anlegen
        results_df = pd.DataFrame(0, index=date_index, columns=['refined_stories', 'finished_stories', 'refined_bugs', 'finished_bugs'])

        for key, times in valid_issues.items():
            start_date_ts = pd.Timestamp(times['start_time'].date())
            if start_date_ts in results_df.index:
                if key in story_keys:
                    results_df.loc[start_date_ts, 'refined_stories'] += 1
                elif key in bug_keys:
                    results_df.loc[start_date_ts, 'refined_bugs'] += 1

            if times['finish_time'] and pd.notna(times['finish_time']):
                finish_date_ts = pd.Timestamp(times['finish_time'].date())
                if finish_date_ts in results_df.index:
                    if key in story_keys:
                        results_df.loc[finish_date_ts, 'finished_stories'] += 1
                    elif key in bug_keys:
                        results_df.loc[finish_date_ts, 'finished_bugs'] += 1

        # 6. Kumulative Summen und aktive Backlogs berechnen
        results_df['refined_story_backlog'] = results_df['refined_stories'].cumsum()
        results_df['finished_story_backlog'] = results_df['finished_stories'].cumsum()
        results_df['active_story_backlog'] = results_df['refined_story_backlog'] - results_df['finished_story_backlog']

        results_df['refined_bug_backlog'] = results_df['refined_bugs'].cumsum()
        results_df['finished_bug_backlog'] = results_df['finished_bugs'].cumsum()
        results_df['active_bug_backlog'] = results_df['refined_bug_backlog'] - results_df['finished_bug_backlog']

        logger.info(f"Erweiterte Backlog-Analyse erfolgreich abgeschlossen. {len(valid_issues)} Issues berücksichtigt.")

        # Hinzufügen der detaillierten Rohdaten zum Rückgabewert
        return {
            "coding_start_time": coding_start_time.isoformat(),
            "coding_finish_time": coding_finish_time.isoformat() if coding_finish_time else None,
            "results_df": results_df,
            "detailed_issues": valid_issues,
            "story_keys": story_keys,
            "bug_keys": bug_keys
        }
