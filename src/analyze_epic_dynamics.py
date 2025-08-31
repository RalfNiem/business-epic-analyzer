"""
Führt eine Analyse der Epic-Dynamik für einen gegebenen Jira-Root-Knoten durch,
um Einblicke in den Fortschritt und die "Gesundheit" des Epic-Portfolios zu erhalten.

Dieses Skript analysiert die gesamte Hierarchie eines Jira-Epics und
konzentriert sich dabei auf die 'Epic'-Issues. Es berechnet und präsentiert
die folgenden Schlüsselanalysen für einen benutzerdefinierten Zeitraum (in Wochen):

Funktionsweise:
1.  **Datenbeschaffung:** Lädt die vollständige Issue-Hierarchie und alle
    zugehörigen Aktivitäten für das angegebene Epic.
2.  **Zeitanalyse:** Ermittelt für jedes Epic das exakte Erstellungsdatum
    (Zeitpunkt der ersten Aktivität) und das Abschlussdatum (Zeitpunkt des
    Wechsels zu 'Closed' oder 'Resolved').
3.  **Dynamik-Analyse:**
    - **Gesamtanzahl & Status:** Zählt alle Epics unter dem Root-Knoten und
      gruppiert sie nach ihrem aktuellen Status.
    - **Neu erstellte Epics:** Listet alle Epics auf, die innerhalb des
      Zeitraums erstellt wurden.
    - **Kürzlich abgeschlossene Epics:** Zeigt alle Epics, die im Zeitraum
      abgeschlossen wurden.
    - **Geänderte Epics:** Identifiziert Epics, deren Status sich innerhalb
      des Zeitraums geändert hat.

Usage:
    - Standard-Analyse für die letzten 4 Wochen:
      python src/analyze_epic_dynamics.py BEMABU-1234

    - Analyse für einen benutzerdefinierten Zeitraum (z.B. 12 Wochen):
      python src/analyze_epic_dynamics.py BEMABU-1234 --weeks 12
"""

import os
import sys
import argparse
from datetime import datetime, timedelta
from collections import defaultdict, Counter

# Fügt das Projekt-Root-Verzeichnis zum Python-Pfad hinzu
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.project_data_provider import ProjectDataProvider
from utils.config import JIRA_TREE_FULL
from utils.logger_config import logger

def clean_status_name(raw_name: str) -> str:
    """Bereinigt einen rohen Status-Namen aus dem Aktivitätsprotokoll."""
    if not raw_name: return "N/A"
    if '[' in raw_name:
        try:
            return raw_name.split(':')[1].split('[')[0].strip().upper()
        except IndexError:
            return raw_name.strip().upper()
    return raw_name.strip().upper()

def analyze_epic_dynamics(root_key: str, weeks: int):
    """
    Führt eine Analyse der Epic-Dynamik für einen Jira-Root-Knoten durch.

    Args:
        root_key (str): Der Jira-Key des Business Epics.
        weeks (int): Der Analysezeitraum in Wochen.
    """
    logger.info(f"Starte Epic-Dynamik-Analyse für Root-Knoten: {root_key} (Zeitraum: {weeks} Wochen)")
    days_period = weeks * 7
    time_cutoff = datetime.now().date() - timedelta(days=days_period)

    # 1. Daten-Provider initialisieren
    data_provider = ProjectDataProvider(epic_id=root_key, hierarchy_config=JIRA_TREE_FULL)
    if not data_provider.is_valid():
        logger.error(f"Konnte keine gültigen Daten für das Epic '{root_key}' laden.")
        return

    epic_keys = {k for k, v in data_provider.issue_details.items() if v.get('type') == 'Epic'}
    if not epic_keys:
        logger.warning("Keine Epics in der Hierarchie gefunden.")
        return

    # 2. Zeitpunkte und Status für ALLE Epics ermitteln
    all_epics_data = []
    activities_by_key = defaultdict(list)
    for act in data_provider.all_activities:
        activities_by_key[act.get('issue_key')].append(act)

    for key in epic_keys:
        epic_info = {
            "key": key,
            "start_date": None,
            "end_date": None,
            "status_at_cutoff": "UNKNOWN",
            "current_status": clean_status_name(data_provider.issue_details.get(key, {}).get('status', 'Unknown'))
        }
        key_activities = sorted(activities_by_key.get(key, []), key=lambda x: x['zeitstempel_iso'])

        if not key_activities:
            continue

        epic_info["start_date"] = datetime.fromisoformat(key_activities[0]['zeitstempel_iso']).date()

        status_at_cutoff_found = False
        for activity in reversed(key_activities):
            activity_date = datetime.fromisoformat(activity['zeitstempel_iso']).date()

            if not epic_info["end_date"] and activity.get('feld_name') == 'Status':
                new_status = clean_status_name(activity.get('neuer_wert', ''))
                if new_status in {'CLOSED', 'RESOLVED'}:
                    epic_info["end_date"] = activity_date

            if not status_at_cutoff_found and activity_date <= time_cutoff and activity.get('feld_name') == 'Status':
                 epic_info["status_at_cutoff"] = clean_status_name(activity.get('neuer_wert', 'Unknown'))
                 status_at_cutoff_found = True

        if not status_at_cutoff_found:
            epic_info['status_at_cutoff'] = 'UNKNOWN'

        all_epics_data.append(epic_info)

    # --- Analysen durchführen ---
    status_counts = Counter(e['current_status'] for e in all_epics_data)
    newly_created_epics = [e for e in all_epics_data if e['start_date'] and e['start_date'] >= time_cutoff]
    recently_closed_epics = [e for e in all_epics_data if e['end_date'] and e['end_date'] >= time_cutoff]
    status_changed_epics = [
        e for e in all_epics_data
        if e['start_date'] and e['start_date'] < time_cutoff and e['status_at_cutoff'] != e['current_status']
    ]


    # --- Ausgabe der Ergebnisse ---
    print(f"\n===== Epic-Analyse für Root-Knoten {root_key} (Letzte {weeks} Wochen) =====")

    print(f"\n--- 📊 Übersicht ---")
    print(f"Gesamtzahl der Epics unter {root_key}: {len(all_epics_data)}")
    print("Aktueller Status der Epics:")
    for status, count in sorted(status_counts.items()):
        print(f"  - {status:<20}: {count} Epic(s)")

    print(f"\n--- 📈 Dynamik der letzten {weeks} Wochen ---")
    print(f"Neu erstellte Epics: {len(newly_created_epics)}")
    print(f"Abgeschlossene Epics ('Closed'/'Resolved'): {len(recently_closed_epics)}")
    print(f"Epics mit Statusänderung: {len(status_changed_epics)}")


    print(f"\n--- ✨ Details: {len(newly_created_epics)} Kürzlich erstellte Epics ---")
    if newly_created_epics:
        for epic in sorted(newly_created_epics, key=lambda x: x['start_date'], reverse=True):
            days_ago = (datetime.now().date() - epic['start_date']).days
            print(f"  - {epic['key']:<15} | Erstellt am: {epic['start_date'].strftime('%d.%m.%Y')} (vor {days_ago} Tagen)")
    else:
        print("  Keine Epics in diesem Zeitraum erstellt.")

    print(f"\n--- ✅ Details: {len(recently_closed_epics)} Kürzlich abgeschlossene Epics ---")
    if recently_closed_epics:
        for epic in sorted(recently_closed_epics, key=lambda x: x['end_date'], reverse=True):
            print(f"  - {epic['key']:<15} | Abgeschlossen am: {epic['end_date'].strftime('%d.%m.%Y')}")
    else:
        print("  Keine Epics in diesem Zeitraum abgeschlossen.")

    print(f"\n--- 🔄 Details: {len(status_changed_epics)} Epics mit Statusänderung ---")
    if status_changed_epics:
        # KORREKTUR: Sortiere zuerst nach dem Anfangsstatus, dann nach dem Endstatus.
        sorted_changed = sorted(status_changed_epics, key=lambda x: (x['status_at_cutoff'], x['current_status']))
        for epic in sorted_changed:
            print(f"  - {epic['key']:<15} | Statusänderung von '{epic['status_at_cutoff']}' zu '{epic['current_status']}'")
    else:
        print("  Keine Epics mit Statusänderung im Zeitraum gefunden.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Führt eine Analyse der Epic-Dynamik für einen Jira-Root-Knoten durch."
    )
    parser.add_argument(
        "root_key",
        type=str,
        help="Der Jira-Key des Root-Knotens (z.B. 'BEMABU-1234')."
    )
    parser.add_argument(
        "--weeks",
        type=int,
        default=4,
        help="Der Analysezeitraum in Wochen (Standard: 4)."
    )
    args = parser.parse_args()

    analyze_epic_dynamics(args.root_key, args.weeks)
