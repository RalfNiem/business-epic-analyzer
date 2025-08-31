"""
Führt eine umfassende, zeitbasierte Analyse des Story-Backlogs für ein
gegebenes Jira Business Epic durch, um Einblicke in die aktuelle Dynamik,
den Fortschritt und die "Gesundheit" des Backlogs zu erhalten.

Dieses Skript analysiert die gesamte Hierarchie eines Jira-Epics und
konzentriert sich dabei auf die 'Story'-Issues. Es berechnet und präsentiert
vier Schlüsselanalysen für einen benutzerdefinierten Zeitraum (in Wochen):

Funktionsweise:
1.  **Datenbeschaffung:** Lädt die vollständige Issue-Hierarchie und alle
    zugehörigen Aktivitäten für das angegebene Epic.
2.  **Zeitanalyse:** Ermittelt für jede Story das exakte Erstellungsdatum
    (Zeitpunkt der ersten Aktivität) und das Abschlussdatum (Zeitpunkt des
    Wechsels zu 'Closed' oder 'Resolved').
3.  **Vier-Quadranten-Analyse:**
    - **Backlog-Veränderung:** Berechnet die Netto-Veränderung des Backlogs
      (neu erstellte vs. abgeschlossene Stories) im definierten Zeitraum und
      gibt an, ob der Backlog gewachsen oder geschrumpft ist.
    - **Neu erstellte Stories:** Listet alle Stories auf, die innerhalb des
      Zeitraums erstellt wurden, um die jüngsten Aktivitäten im Refinement
      zu zeigen.
    - **Kürzlich abgeschlossene Stories:** Zeigt alle Stories, die im Zeitraum
      abgeschlossen wurden. Die Liste ist nach der Gesamtlaufzeit sortiert,
      um schnell zu erkennen, welche "Altlasten" kürzlich abgearbeitet wurden.
    - **Offene Stories:** Listet alle noch offenen Stories auf, sortiert nach
      dem Datum ihrer letzten Aktivität. Dies hilft, "vergessene" oder
      blockierte Tickets zu identifizieren, die lange nicht mehr bearbeitet wurden.

Usage:
    Das Skript wird über die Kommandozeile ausgeführt und erfordert einen
    Jira-Key. Der Analysezeitraum ist optional.

    - Standard-Analyse für die letzten 4 Wochen:
      python src/analyze_story_backlog.py BEMABU-1234

    - Analyse für einen benutzerdefinierten Zeitraum (z.B. 12 Wochen):
      python src/analyze_story_backlog.py BEMABU-1234 --weeks 12
"""

import os
import sys
import argparse
from datetime import datetime, timedelta
from collections import defaultdict

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

def analyze_story_backlog(epic_key: str, weeks: int):
    """
    Führt eine umfassende Analyse des Story-Backlogs für ein Business Epic durch.

    Args:
        epic_key (str): Der Jira-Key des Business Epics.
        weeks (int): Der Analysezeitraum in Wochen.
    """
    logger.info(f"Starte umfassende Backlog-Analyse für Epic: {epic_key} (Zeitraum: {weeks} Wochen)")
    days_period = weeks * 7

    # 1. Daten-Provider initialisieren und alle relevanten Daten laden
    data_provider = ProjectDataProvider(epic_id=epic_key, hierarchy_config=JIRA_TREE_FULL)
    if not data_provider.is_valid():
        logger.error(f"Konnte keine gültigen Daten für das Epic '{epic_key}' laden.")
        return

    story_keys = {k for k, v in data_provider.issue_details.items() if v.get('type') == 'Story'}
    if not story_keys:
        logger.warning("Keine Stories in der Hierarchie gefunden.")
        return

    # 2. Start-, End- und letztes Aktivitätsdatum für ALLE Stories ermitteln
    all_stories_data = []
    statuses_to_exclude_open = {'closed', 'resolved'}

    activities_by_key = defaultdict(list)
    for act in data_provider.all_activities:
        activities_by_key[act.get('issue_key')].append(act)

    for key in story_keys:
        story_info = {"key": key, "start_date": None, "end_date": None, "last_activity_date": None}
        key_activities = activities_by_key.get(key)

        if not key_activities:
            continue

        try:
            story_info["start_date"] = datetime.fromisoformat(key_activities[0]['zeitstempel_iso']).date()
            story_info["last_activity_date"] = datetime.fromisoformat(key_activities[-1]['zeitstempel_iso']).date()
        except (ValueError, TypeError):
            continue

        for activity in reversed(key_activities):
            if activity.get('feld_name') == 'Status':
                new_status = clean_status_name(activity.get('neuer_wert', ''))
                if new_status in {'CLOSED', 'RESOLVED'}:
                    try:
                        story_info["end_date"] = datetime.fromisoformat(activity['zeitstempel_iso']).date()
                        break
                    except (ValueError, TypeError):
                        pass
        all_stories_data.append(story_info)


    # --- Start der Analysen ---
    time_cutoff = datetime.now().date() - timedelta(days=days_period)

    newly_created_stories = [s for s in all_stories_data if s['start_date'] and s['start_date'] >= time_cutoff]
    recently_closed_stories = [s for s in all_stories_data if s['end_date'] and s['end_date'] >= time_cutoff]

    open_stories_data = []
    for story in all_stories_data:
        if not story['start_date']: continue
        details = data_provider.issue_details.get(story['key'], {})
        status = details.get('status')
        if status and status.lower() not in statuses_to_exclude_open:
            story['status'] = status
            open_stories_data.append(story)

    # --- Ausgabe der Ergebnisse ---
    print(f"\n===== Backlog-Analyse für Epic {epic_key} (Letzte {weeks} Wochen) =====")

    num_created = len(newly_created_stories)
    num_closed = len(recently_closed_stories)
    backlog_change = num_created - num_closed

    print("\n--- 📈 Backlog-Veränderung ---")
    if backlog_change > 0:
        print(f"Der Story-Backlog ist in den letzten {weeks} Wochen um {backlog_change} Stories gewachsen.")
    elif backlog_change < 0:
        print(f"Der Story-Backlog ist in den letzten {weeks} Wochen um {abs(backlog_change)} Stories geschrumpft.")
    else:
        print(f"Der Story-Backlog ist in den letzten {weeks} Wochen konstant geblieben.")
    print(f"(Neu erstellt: {num_created}, Abgeschlossen: {num_closed})\n")


    print(f"--- ✨ {len(newly_created_stories)} Kürzlich erstellte Stories ---")
    if newly_created_stories:
        sorted_new = sorted(newly_created_stories, key=lambda x: x['start_date'], reverse=True)
        for story in sorted_new:
            days_ago = (datetime.now().date() - story['start_date']).days
            print(f"  - {story['key']:<15} | Erstellt am: {story['start_date'].strftime('%d.%m.%Y')} (vor {days_ago} Tagen)")
    else:
        print("  Keine Stories in diesem Zeitraum erstellt.")
    print()

    print(f"--- ✅ {len(recently_closed_stories)} Kürzlich abgeschlossene Stories (sortiert nach Laufzeit) ---")
    if recently_closed_stories:
        for story in recently_closed_stories:
            story['laufzeit'] = (story['end_date'] - story['start_date']).days

        sorted_closed = sorted(recently_closed_stories, key=lambda x: x['laufzeit'])

        for story in sorted_closed:
            print(f"  - {story['key']:<15} | Erstellt: {story['start_date'].strftime('%d.%m.%Y')} | Abgeschlossen: {story['end_date'].strftime('%d.%m.%Y')} (Laufzeit: {story['laufzeit']} Tage)")
    else:
        print("  Keine Stories in diesem Zeitraum abgeschlossen.")
    print()

    print(f"--- 📝 {len(open_stories_data)} Offene Stories (sortiert nach letzter Aktivität) ---")
    if open_stories_data:
        # Sortiere nach dem Datum der letzten Aktivität, von alt nach neu
        sorted_open = sorted(open_stories_data, key=lambda x: x['last_activity_date'])

        for story in sorted_open:
            last_activity_days_ago = (datetime.now().date() - story['last_activity_date']).days
            print(f"  - {story['key']:<15} | Status: {story['status']:<20} | Letzte Aktivität: {story['last_activity_date'].strftime('%d.%m.%Y')} (vor {last_activity_days_ago} Tagen)")
    else:
        print("  Alle Stories sind geschlossen oder erledigt.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Führt eine umfassende Analyse des Story-Backlogs für ein Jira Business Epic durch."
    )
    parser.add_argument(
        "epic_key",
        type=str,
        help="Der Jira-Key des Business Epics (z.B. 'BEMABU-1234')."
    )
    parser.add_argument(
        "--weeks",
        type=int,
        default=4,
        help="Der Analysezeitraum in Wochen (Standard: 4)."
    )
    args = parser.parse_args()

    analyze_story_backlog(args.epic_key, args.weeks)
