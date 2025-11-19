"""
Analysiert zeitbezogene Metriken von Jira-Projekten basierend auf Statuswechseln.

Diese Klasse ist verantwortlich für die Berechnung und Auswertung von zwei
wesentlichen zeitlichen Dimensionen eines Projekts:

1.  **Verweildauer (Cycle Time):** Sie ermittelt, wie lange das Haupt-Epic in den
    einzelnen Phasen seines Lebenszyklus (z.B. 'Funnel', 'Analysis', 'In Progress')
    verbracht hat. Dies gibt Aufschluss über mögliche Engpässe und die allgemeine
    Geschwindigkeit des Wertstroms auf strategischer Ebene.

2.  **Coding-Laufzeit (Lead Time):** Sie definiert und berechnet den Zeitraum der
    aktiven Software-Entwicklung.
    -   Start: Wenn die *erste* Story in den Status 'In Progress' wechselt
          (ermittelt aus den Aktivitäten).
    -   Ende: Wenn die *letzte* Story in einen Endstatus ('Resolved' oder 'Closed')
          übergeht (ermittelt aus den 'resolved'/'closed_date'-Feldern).
    Diese Metrik liefert eine präzise Messung der reinen Umsetzungsdauer.
"""

from datetime import datetime, timedelta
from src.utils.project_data_provider import ProjectDataProvider
from utils.logger_config import logger # NEU: logger importiert

class StatusAnalyzer:
    """
    Analysiert zeitbezogene Metriken von Jira-Projekten.

    Diese Klasse ist verantwortlich für die Berechnung der Verweildauer von
    Epics in bestimmten Status und für die Ermittlung des Zeitraums der
    aktiven Software-Entwicklung ("Coding Time").
    """

    def _clean_status_name(self, raw_name: str) -> str:
        """
        Extrahiert und bereinigt einen Status-Namen aus einem rohen String.

        Nimmt Strings wie '...:DONE [Status]' und gibt 'DONE' zurück.

        Args:
            raw_name (str): Der rohe Status-String aus den Aktivitätsdaten.

        Returns:
            str: Der bereinigte, großgeschriebene Status-Name oder 'N/A'.
        """
        if not raw_name: return "N/A"
        if '[' in raw_name:
            try: return raw_name.split(':')[1].split('[')[0].strip().upper()
            except IndexError: return raw_name.strip().upper()
        return raw_name.strip().upper()

    def _calculate_epic_status_durations(self, all_activities: list, epic_id: str) -> dict:
        """
        Berechnet die Verweildauer des Business Epics in allen durchlaufenen Status.

        Args:
            all_activities (list): Eine Liste aller Aktivitäten des Projekts.
            epic_id (str): Die Jira-ID des zu analysierenden Business Epics.

        Returns:
            dict: Ein Dictionary, das Status-Namen auf ihre Dauer (timedelta) abbildet.
        """
        status_durations = {}

        epic_activities = [act for act in all_activities if act.get('issue_key') == epic_id]
        if not epic_activities:
            return status_durations

        epic_status_changes = [act for act in epic_activities if act.get('feld_name') == 'Status']

        epic_start_time_iso = epic_activities[0]['zeitstempel_iso']
        epic_status_changes.insert(0, {'zeitstempel_iso': epic_start_time_iso, 'neuer_wert': 'FUNNEL'})

        for i in range(len(epic_status_changes) - 1):
            start_act, end_act = epic_status_changes[i], epic_status_changes[i+1]
            status_name = self._clean_status_name(start_act.get('neuer_wert'))
            duration = datetime.fromisoformat(end_act['zeitstempel_iso']) - datetime.fromisoformat(start_act['zeitstempel_iso'])
            current_duration = status_durations.get(status_name, timedelta(0))
            status_durations[status_name] = current_duration + duration

        if epic_status_changes:
            last_change = epic_status_changes[-1]
            last_status_name = self._clean_status_name(last_change.get('neuer_wert'))
            duration_since_last_change = datetime.now().astimezone() - datetime.fromisoformat(last_change['zeitstempel_iso'])
            current_duration = status_durations.get(last_status_name, timedelta(0))
            status_durations[last_status_name] = current_duration + duration_since_last_change

        return status_durations

    def analyze(self, data_provider: ProjectDataProvider) -> dict:
        """
        Führt die Analyse der Statuswechsel und Laufzeiten durch.
        ...
        """
        all_activities = data_provider.all_activities
        issue_details = data_provider.issue_details
        if not all_activities:
            return {}

        all_status_changes = [
            {
                "timestamp": act.get('zeitstempel_iso'),
                "issue": act.get('issue_key'),
                "from_status": self._clean_status_name(act.get('alter_wert', 'N/A')),
                "to_status": self._clean_status_name(act.get('neuer_wert', 'N/A'))
            }
            for act in all_activities if act.get('feld_name') == 'Status'
        ]

        durations = self._calculate_epic_status_durations(all_activities, data_provider.epic_id)

        story_keys = [k for k, v in issue_details.items() if v.get('type') == 'Story']
        start_time, end_time = None, None

        # --- LOGIK 1: Finde den ERSTEN 'In Progress'-Status (aus Aktivitäten) ---
        story_activities = [act for act in all_activities if act.get('issue_key') in story_keys]
        for activity in story_activities:
            if activity.get('feld_name') == 'status':
                if not start_time and self._clean_status_name(activity.get('neuer_wert')) == 'IN PROGRESS':
                    start_time = activity.get('zeitstempel_iso')
                    break # Optimierung: Stoppe, sobald der erste gefunden wurde

        # --- LOGIK 2: Finde den LETZTEN Abschluss (aus Cache-Feldern) ---
        all_story_completion_dates = []
        for key in story_keys:
            details = issue_details.get(key)
            if not details:
                continue

            resolved_str = details.get("resolved")
            closed_str = details.get("closed_date") # Nutzt den Schlüssel aus project_data_provider

            resolved_dt, closed_dt = None, None

            if resolved_str:
                try: resolved_dt = datetime.fromisoformat(resolved_str)
                except (ValueError, TypeError):
                    logger.warning(f"Ungültiges 'resolved' Datum für {key}: {resolved_str}")

            if closed_str:
                try: closed_dt = datetime.fromisoformat(closed_str)
                except (ValueError, TypeError):
                    logger.warning(f"Ungültiges 'closed_date' Datum für {key}: {closed_str}")

            # Finde das MINIMALE (früheste) Abschlussdatum FÜR DIESES EINE ISSUE
            issue_completion_dt = None
            if resolved_dt and closed_dt:
                issue_completion_dt = min(resolved_dt, closed_dt)
            elif resolved_dt:
                issue_completion_dt = resolved_dt
            elif closed_dt:
                issue_completion_dt = closed_dt

            if issue_completion_dt:
                all_story_completion_dates.append(issue_completion_dt)

        # Finde das MAXIMALE (späteste) Datum von allen minimalen Daten
        if all_story_completion_dates:
            final_end_dt = max(all_story_completion_dates)
            end_time = final_end_dt.isoformat()


        # --- BERECHNUNG DER 'coding_duration' (Logik bleibt gleich) ---
        coding_duration_str = "Nicht gestartet"
        if start_time:
            start_dt = datetime.fromisoformat(start_time)
            # Wenn end_time nicht gesetzt ist (weil nichts resolved/closed ist), das aktuelle Datum verwenden
            end_dt = datetime.fromisoformat(end_time) if end_time else datetime.now().astimezone()

            duration = end_dt - start_dt
            total_days = duration.days
            months = total_days // 30
            days = total_days % 30
            if months == 0:
                coding_duration_str = f"{days} Tage"
            else:
                coding_duration_str = f"{months} Monate, {days} Tage"

        return {
            "all_status_changes": all_status_changes,
            "epic_status_durations": durations,
            "coding_start_time": start_time,
            "coding_end_time": end_time,  # Das ist jetzt das MAXIMALE der MINIMALEN Daten
            "coding_duration": coding_duration_str
        }
