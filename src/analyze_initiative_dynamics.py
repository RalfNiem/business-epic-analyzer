"""
Führt eine Analyse der Initiative-Dynamik für einen gegebenen Jira-Root-Knoten durch,
um Einblicke in den Fortschritt und die "Gesundheit" des Initiative-Portfolios zu erhalten.

Dieses Skript analysiert die gesamte Hierarchie eines Jira-Epics und
konzentriert sich dabei auf die 'Initiative'-Issues. Es berechnet und präsentiert
die folgenden Schlüsselanalysen für einen benutzerdefinierten Zeitraum (in Wochen):

Funktionsweise:
1.  **Datenbeschaffung:** Lädt die vollständige Issue-Hierarchie und alle
    zugehörigen Aktivitäten für das angegebene Epic.
2.  **Zeitanalyse:** Ermittelt für jede Initiative das exakte Erstellungsdatum
    (Zeitpunkt der ersten Aktivität) und das Abschlussdatum (Zeitpunkt des
    Wechsels zu 'Closed' oder 'Resolved').
3.  **Dynamik-Analyse:**
    - **Gesamtanzahl & Status:** Zählt alle Initiatives unter dem Root-Knoten und
      gruppiert sie nach ihrem aktuellen Status.
    - **Neu erstellte Initiatives:** Listet alle Initiatives auf, die innerhalb des
      Zeitraums erstellt wurden.
    - **Kürzlich abgeschlossene Initiatives:** Zeigt alle Initiatives, die im Zeitraum
      abgeschlossen wurden.
    - **Geänderte Initiatives:** Identifiziert Initiatives, deren Status sich innerhalb
      des Zeitraums geändert hat.

Usage:
    - Standard-Analyse für die letzten 4 Wochen:
      python src/analyze_initiative_dynamics.py BEMABU-1234

    - Analyse für einen benutzerdefinierten Zeitraum (z.B. 12 Wochen):
      python src/analyze_initiative_dynamics.py BEMABU-1234 --weeks 12
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

def analyze_initiative_dynamics(root_key: str, weeks: int):
    """
    Führt eine Analyse der Initiative-Dynamik für einen Jira-Root-Knoten durch.

    Args:
        root_key (str): Der Jira-Key des Business Epics.
        weeks (int): Der Analysezeitraum in Wochen.
    """
    logger.info(f"Starte Initiative-Dynamik-Analyse für Root-Knoten: {root_key} (Zeitraum: {weeks} Wochen)")
    days_period = weeks * 7
    time_cutoff = datetime.now().date() - timedelta(days=days_period)

    # 1. Daten-Provider initialisieren
    data_provider = ProjectDataProvider(epic_id=root_key, hierarchy_config=JIRA_TREE_FULL)
    if not data_provider.is_valid():
        logger.error(f"Konnte keine gültigen Daten für den Root-Knoten '{root_key}' laden.")
        return

    initiative_keys = {k for k, v in data_provider.issue_details.items() if v.get('type') == 'Initiative'}
    if not initiative_keys:
        logger.warning("Keine Initiatives in der Hierarchie gefunden.")
        return

    # 2. Zeitpunkte und Status für ALLE Initiatives ermitteln
    all_initiatives_data = []
    activities_by_key = defaultdict(list)
    for act in data_provider.all_activities:
        activities_by_key[act.get('issue_key')].append(act)

    for key in initiative_keys:
        initiative_info = {
            "key": key,
            "start_date": None,
            "end_date": None,
            "status_at_cutoff": "UNKNOWN",
            "current_status": clean_status_name(data_provider.issue_details.get(key, {}).get('status', 'Unknown'))
        }
        key_activities = sorted(activities_by_key.get(key, []), key=lambda x: x['zeitstempel_iso'])

        if not key_activities:
            continue

        initiative_info["start_date"] = datetime.fromisoformat(key_activities[0]['zeitstempel_iso']).date()

        status_at_cutoff_found = False
        for activity in reversed(key_activities):
            activity_date = datetime.fromisoformat(activity['zeitstempel_iso']).date()

            if not initiative_info["end_date"] and activity.get('feld_name') == 'Status':
                new_status = clean_status_name(activity.get('neuer_wert', ''))
                if new_status in {'CLOSED', 'RESOLVED'}:
                    initiative_info["end_date"] = activity_date

            if not status_at_cutoff_found and activity_date <= time_cutoff and activity.get('feld_name') == 'Status':
                 initiative_info["status_at_cutoff"] = clean_status_name(activity.get('neuer_wert', 'Unknown'))
                 status_at_cutoff_found = True

        if not status_at_cutoff_found:
            initiative_info['status_at_cutoff'] = 'UNKNOWN'

        all_initiatives_data.append(initiative_info)

    # --- Analysen durchführen ---
    status_counts = Counter(e['current_status'] for e in all_initiatives_data)
    newly_created_initiatives = [e for e in all_initiatives_data if e['start_date'] and e['start_date'] >= time_cutoff]
    recently_closed_initiatives = [e for e in all_initiatives_data if e['end_date'] and e['end_date'] >= time_cutoff]
    status_changed_initiatives = [
        e for e in all_initiatives_data
        if e['start_date'] and e['start_date'] < time_cutoff and e['status_at_cutoff'] != e['current_status']
    ]


    # --- Ausgabe der Ergebnisse ---
    print(f"\n===== Initiative-Analyse für Root-Knoten {root_key} (Letzte {weeks} Wochen) =====")

    print(f"\n--- 📊 Übersicht ---")
    print(f"Gesamtzahl der Initiatives unter {root_key}: {len(all_initiatives_data)}")
    print("Aktueller Status der Initiatives:")
    for status, count in sorted(status_counts.items()):
        print(f"  - {status:<20}: {count} Initiative(s)")

    print(f"\n--- 📈 Dynamik der letzten {weeks} Wochen ---")
    print(f"Neu erstellte Initiatives: {len(newly_created_initiatives)}")
    print(f"Abgeschlossene Initiatives ('Closed'/'Resolved'): {len(recently_closed_initiatives)}")
    print(f"Initiatives mit Statusänderung: {len(status_changed_initiatives)}")


    print(f"\n--- ✨ Details: {len(newly_created_initiatives)} Kürzlich erstellte Initiatives ---")
    if newly_created_initiatives:
        for initiative in sorted(newly_created_initiatives, key=lambda x: x['start_date'], reverse=True):
            days_ago = (datetime.now().date() - initiative['start_date']).days
            print(f"  - {initiative['key']:<15} | Erstellt am: {initiative['start_date'].strftime('%d.%m.%Y')} (vor {days_ago} Tagen)")
    else:
        print("  Keine Initiatives in diesem Zeitraum erstellt.")

    print(f"\n--- ✅ Details: {len(recently_closed_initiatives)} Kürzlich abgeschlossene Initiatives ---")
    if recently_closed_initiatives:
        for initiative in sorted(recently_closed_initiatives, key=lambda x: x['end_date'], reverse=True):
            print(f"  - {initiative['key']:<15} | Abgeschlossen am: {initiative['end_date'].strftime('%d.%m.%Y')}")
    else:
        print("  Keine Initiatives in diesem Zeitraum abgeschlossen.")

    print(f"\n--- 🔄 Details: {len(status_changed_initiatives)} Initiatives mit Statusänderung ---")
    if status_changed_initiatives:
        sorted_changed = sorted(status_changed_initiatives, key=lambda x: (x['status_at_cutoff'], x['current_status']))
        for initiative in sorted_changed:
            print(f"  - {initiative['key']:<15} | Statusänderung von '{initiative['status_at_cutoff']}' zu '{initiative['current_status']}'")
    else:
        print("  Keine Initiatives mit Statusänderung im Zeitraum gefunden.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Führt eine Analyse der Initiative-Dynamik für einen Jira-Root-Knoten durch."
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

    analyze_initiative_dynamics(args.root_key, args.weeks)
