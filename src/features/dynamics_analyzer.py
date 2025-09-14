"""
Führt eine Analyse der allgemeinen Projektdynamik durch.

Diese Klasse untersucht den "Puls" eines Projekts, indem sie die Gesamtheit
aller Aktivitäten (Änderungen an Issues) auswertet. Sie zielt darauf ab,
Muster und Schlüsselereignisse zu identifizieren, die auf die "Gesundheit"
und den Fortschritt des Projekts hinweisen.

Die Analyse umfasst mehrere Dimensionen:
-   **Schlüsselereignisse:** Identifiziert kritische Vorkommnisse wie das Blockieren
    eines Tickets, Änderungen an der Zeitplanung oder signifikante
    Scope-Anpassungen.
-   **Aktivitätslevel:** Misst die Gesamtanzahl der Aktivitäten und deren Verteilung
    über verschiedene Felder, um zu sehen, wo die meiste Arbeit stattfindet.
-   **Zeitliche Dynamik:** Analysiert die Aktivität innerhalb eines bestimmten
    Zeitraums (z.B. die letzten 4 Wochen), um die aktuelle Dynamik zu bewerten.
-   **Hauptakteure:** Ermittelt die Top-3-Benutzer mit den meisten signifikanten
    Änderungen, um die Schlüsselpersonen im Projekt zu identifizieren.

Die Ergebnisse liefern einen schnellen Überblick darüber, ob ein Projekt aktiv
bearbeitet wird, wo potenzielle Probleme liegen und wer die treibenden Kräfte sind.
"""

from collections import Counter
from datetime import datetime, timedelta
from src.utils.project_data_provider import ProjectDataProvider

class DynamicsAnalyzer:
    """Führt die Analyse der allgemeinen Projektdynamik durch."""

    def analyze(self, data_provider: ProjectDataProvider) -> dict:
        """
        Analysiert die Projektdynamik und gibt die Ergebnisse als strukturiertes Dictionary zurück.
        """
        all_activities = data_provider.all_activities
        if not all_activities:
            return {}

        key_events = []
        significant_changes = []
        scope_change_tracker = set()
        SIGNIFICANT_FIELDS = ['Status', 'Description', 'Acceptance Criteria', 'Assignee', 'Fix Version/s']

        for activity in all_activities:
            field = activity.get('feld_name')
            new_value = activity.get('neuer_wert', '')
            issue_key = activity.get('issue_key')
            timestamp_iso = activity.get('zeitstempel_iso', '')
            event_type = None

            if field == 'Status' and new_value and 'BLOCKED' in new_value.upper():
                event_type = "STATUS_BLOCK"
                details = f"Status von '{issue_key}' wurde auf Blocked gesetzt."
            elif field in ['Target end', 'Fix Version/s']:
                event_type = "TIME_CHANGE"
                details = f"Zeitplanung von '{issue_key}' ({field}) wurde geändert."
            elif field in ['Description', 'Acceptance Criteria']:
                activity_date = timestamp_iso[:10]
                if (issue_key, activity_date) not in scope_change_tracker:
                    event_type = "SCOPE_CHANGE"
                    details = f"Der Scope von '{issue_key}' wurde an diesem Tag angepasst."
                    scope_change_tracker.add((issue_key, activity_date))

            if event_type:
                key_events.append({
                    "timestamp": timestamp_iso,
                    "issue": issue_key,
                    "event_type": event_type,
                    "details": details
                })

            if field in SIGNIFICANT_FIELDS:
                significant_changes.append(activity)

        contributors = [act['benutzer'] for act in significant_changes if act.get('benutzer')]
        top_contributors = Counter(contributors).most_common(3)
        key_contributors = [{"name": name, "contributions": count} for name, count in top_contributors]

        now = datetime.now().astimezone()
        four_weeks_ago = now - timedelta(weeks=4)
        activities_last_4_weeks = [
            act for act in all_activities
            if datetime.fromisoformat(act['zeitstempel_iso']) >= four_weeks_ago
        ]

        field_names = [act.get('feld_name') for act in all_activities if act.get('feld_name')]
        activity_counts_by_field = dict(Counter(field_names).most_common())

        key_events.sort(key=lambda x: x.get('timestamp', ''))

        return {
            "analysis_metadata": {
                "total_activities_found": len(all_activities),
                "activity_counts_by_field": activity_counts_by_field,
                "total_activities_last_4_weeks": len(activities_last_4_weeks),
                "total_significant_changes": len(significant_changes),
                "key_contributors": key_contributors
            },
            "key_events_chronological": key_events
        }
