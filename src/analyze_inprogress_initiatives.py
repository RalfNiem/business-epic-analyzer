"""
Analysiert Jira Issues vom Typ 'Initiative', die sich im Status 'In Progress'
befinden. Für jede dieser Initiatives wird eine detaillierte Übersicht der
darunter liegenden Epics erstellt, um zu bewerten, ob dort noch aktive
Arbeit stattfindet.

Funktionsweise:
1.  **Datenbeschaffung:** Lädt die vollständige Issue-Hierarchie und alle
    zugehörigen Aktivitäten für einen gegebenen Root-Knoten.
2.  **Filterung:** Identifiziert alle 'Initiative'-Issues, die sich aktuell
    im Status 'In Progress' befinden.
3.  **Epic-Analyse pro Initiative:** Für jede gefundene Initiative wird eine
    detaillierte Analyse der untergeordneten Epics durchgeführt, die Folgendes
    umfasst:
    - Gesamtanzahl und Statusverteilung der Epics.
    - Anzahl der in den letzten Wochen neu erstellten Epics.
    - Anzahl der in den letzten Wochen abgeschlossenen Epics.
    - Anzahl der Epics, deren Status sich im Analysezeitraum geändert hat.

Usage:
    - Standard-Analyse für die letzten 4 Wochen:
      python src/analyze_inprogress_initiatives.py BEMABU-1234

    - Analyse für einen benutzerdefinierten Zeitraum (z.B. 12 Wochen):
      python src/analyze_inprogress_initiatives.py BEMABU-1234 --weeks 12
"""

import os
import sys
import argparse
from datetime import datetime, timedelta
from collections import defaultdict, Counter
import networkx as nx

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

def analyze_initiative_progress(root_key: str, weeks: int):
    """
    Analysiert den Fortschritt von Initiatives im Status 'In Progress'.

    Args:
        root_key (str): Der Jira-Key des Root-Knotens.
        weeks (int): Der Analysezeitraum in Wochen.
    """
    logger.info(f"Starte Analyse für 'In Progress' Initiatives unter {root_key} (Zeitraum: {weeks} Wochen)")
    days_period = weeks * 7
    time_cutoff = datetime.now().date() - timedelta(days=days_period)

    # 1. Daten-Provider für den gesamten Baum initialisieren
    data_provider = ProjectDataProvider(epic_id=root_key, hierarchy_config=JIRA_TREE_FULL)
    if not data_provider.is_valid():
        logger.error(f"Konnte keine gültigen Daten für den Root-Knoten '{root_key}' laden.")
        return

    # 2. Alle Initiatives im Status 'IN PROGRESS' finden
    inprogress_initiatives = {
        k for k, v in data_provider.issue_details.items()
        if v.get('type') == 'Initiative' and clean_status_name(v.get('status')) == 'IN PROGRESS'
    }

    if not inprogress_initiatives:
        logger.warning("Keine Initiatives im Status 'In Progress' in der Hierarchie gefunden.")
        return

    print(f"\n===== Analyse für {len(inprogress_initiatives)} 'In Progress' Initiatives unter {root_key} (Letzte {weeks} Wochen) =====")

    # 3. Für jede Initiative eine separate Analyse der untergeordneten Epics durchführen
    activities_by_key = defaultdict(list)
    for act in data_provider.all_activities:
        activities_by_key[act.get('issue_key')].append(act)

    for initiative_key in sorted(list(inprogress_initiatives)):
        print(f"\n\n--- Initiative: {initiative_key} ---")

        # Finde alle untergeordneten Epics dieser Initiative
        try:
            descendants = nx.descendants(data_provider.issue_tree, initiative_key)
        except nx.NetworkXError:
            descendants = set() # Keine Nachfolger

        epic_keys_under_initiative = {
            k for k in descendants
            if data_provider.issue_details.get(k, {}).get('type') == 'Epic'
        }

        if not epic_keys_under_initiative:
            print("  -> Keine untergeordneten Epics für diese Initiative gefunden.")
            continue

        # Analysiere die gefundenen Epics
        all_epics_data = []
        for key in epic_keys_under_initiative:
            epic_info = {
                "key": key, "start_date": None, "end_date": None,
                "status_at_cutoff": "UNKNOWN",
                "current_status": clean_status_name(data_provider.issue_details.get(key, {}).get('status', 'Unknown'))
            }
            key_activities = sorted(activities_by_key.get(key, []), key=lambda x: x['zeitstempel_iso'])

            if not key_activities: continue

            epic_info["start_date"] = datetime.fromisoformat(key_activities[0]['zeitstempel_iso']).date()
            status_at_cutoff_found = False
            for activity in reversed(key_activities):
                activity_date = datetime.fromisoformat(activity['zeitstempel_iso']).date()
                if not epic_info["end_date"] and activity.get('feld_name') == 'Status' and clean_status_name(activity.get('neuer_wert', '')) in {'CLOSED', 'RESOLVED'}:
                    epic_info["end_date"] = activity_date
                if not status_at_cutoff_found and activity_date <= time_cutoff and activity.get('feld_name') == 'Status':
                    epic_info["status_at_cutoff"] = clean_status_name(activity.get('neuer_wert', 'Unknown'))
                    status_at_cutoff_found = True

            if not status_at_cutoff_found: epic_info['status_at_cutoff'] = 'UNKNOWN'
            all_epics_data.append(epic_info)

        # Führe die Analyse für die Epics dieser Initiative durch
        status_counts = Counter(e['current_status'] for e in all_epics_data)
        newly_created = [e for e in all_epics_data if e['start_date'] and e['start_date'] >= time_cutoff]
        recently_closed = [e for e in all_epics_data if e['end_date'] and e['end_date'] >= time_cutoff]
        status_changed = [e for e in all_epics_data if e['start_date'] and e['start_date'] < time_cutoff and e['status_at_cutoff'] != e['current_status']]

        # Gib die Ergebnisse für diese Initiative aus
        print(f"  - Gesamtzahl Epics: {len(all_epics_data)}")
        print("  - Aktueller Status der Epics:")
        for status, count in sorted(status_counts.items()):
            print(f"    - {status:<20}: {count} Epic(s)")

        print(f"\n  - Dynamik der letzten {weeks} Wochen:")
        print(f"    - Neu erstellte Epics: {len(newly_created)}")
        print(f"    - Abgeschlossene Epics: {len(recently_closed)}")
        print(f"    - Epics mit Statusänderung: {len(status_changed)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Analysiert 'In Progress' Initiatives und die Dynamik ihrer untergeordneten Epics."
    )
    parser.add_argument(
        "root_key",
        type=str,
        help="Der Jira-Key des Root-Knotens, unter dem die Initiatives gesucht werden sollen (z.B. 'BEMABU-1234')."
    )
    parser.add_argument(
        "--weeks",
        type=int,
        default=4,
        help="Der Analysezeitraum in Wochen (Standard: 4)."
    )
    args = parser.parse_args()

    analyze_initiative_progress(args.root_key, args.weeks)
