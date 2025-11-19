"""
Führt eine umfassende, zeitbasierte Analyse des Story-Backlogs für ein
gegebenes Jira Business Epic durch, um Einblicke in die aktuelle Dynamik,
den Fortschritt und die "Gesundheit" des Backlogs zu erhalten.

Dieses Skript analysiert die gesamte Hierarchie eines Jira-Epics und
konzentriert sich dabei auf die 'Story'-Issues. Es berechnet und präsentiert
vier Schlüsselanalysen für einen benutzerdefinierten Zeitraum:

Funktionsweise:
1.  **Datenbeschaffung:** Lädt die vollständige Issue-Hierarchie und alle
    zugehörigen Aktivitäten für das angegebene Epic.
2.  **Zeitanalyse:** Ermittelt für jede Story das exakte Erstellungsdatum
    (aus dem Feld 'Created') und das Abschlussdatum (aus den Feldern
    'Closed Date' or 'Resolved'). Das Datum der letzten Aktivität wird
    weiterhin aus der Historie ermittelt.
3.  **Vier-Quadranten-Analyse:**
    - **Backlog-Veränderung:** Berechnet die Netto-Veränderung des Backlogs
      (neu erstellte vs. abgeschlossene Stories) im definierten Zeitraum und
      gibt an, ob der Backlog gewachsen oder geschrumpft ist.
    - **Neu erstellte Stories:** Listet alle Stories auf, die innerhalb des
      Zeitraums erstellt wurden, um die jüngsten Aktivitäten im Refinement
      zu zeigen.
    - **Kürzlich abgeschlossene Stories:** Zeigt alle Stories, die im Zeitraum
      abgeschlossen wurden. Die Liste ist nach dem Abschlussdatum (neueste
      zuerst) sortiert.
    - **Offene Stories:** Listet alle noch offenen Stories auf, sortiert
      zuerst nach Status (gemäß einer definierten Reihenfolge) und dann
      nach dem Datum ihrer letzten Aktivität (neueste zuerst).

Usage:
    Das Skript wird über die Kommandozeile ausgeführt und erfordert einen
    Jira-Key. Der Analysezeitraum ist optional.

    - Analyse mit Standard-Zeitraum ('01.01.2025' bis Heute):
      python src/analyze_story_backlog.py --issue BEMABU-1234

    - Analyse für einen benutzerdefinierten Zeitraum:
      python src/analyze_story_backlog.py --issue BEMABU-1234 --start_date 01.06.2024 --stop_date 30.06.2024

    - Synonyme (end_date statt stop_date):
      python src/analyze_story_backlog.py --issue BEMABU-1234 --start_date 01.01.2024 --end_date 15.07.2024
"""

import os
import sys
import argparse
from datetime import datetime, timedelta, date
from collections import defaultdict

# Fügt das Projekt-Root-Verzeichnis zum Python-Pfad hinzu
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.project_data_provider import ProjectDataProvider
from utils.config import JIRA_TREE_FULL
from utils.logger_config import logger

def analyze_story_backlog(epic_key: str, start_date: date, stop_date: date):
    """
    Führt eine umfassende Analyse des Story-Backlogs für ein Business Epic durch.

    Args:
        epic_key (str): Der Jira-Key des Business Epics.
        start_date (date): Das Startdatum (inklusiv) des Analysezeitraums.
        stop_date (date): Das Enddatum (inklusiv) des Analysezeitraums.

    Returns:
        dict: Ein Dictionary mit den Schlüsselmetriken für die CSV-Zusammenfassung.
    """
    date_range_str = f"{start_date.strftime('%d.%m.%Y')} - {stop_date.strftime('%d.%m.%Y')}"
    logger.info(f"Starte umfassende Backlog-Analyse für Epic: {epic_key} (Zeitraum: {date_range_str})")

    # --- NEU: Standard-Rückgabewerte ---
    default_metrics = {
        "Gesamtzahl Stories": 0,
        "Offene Stories": 0,
        "Erstellte Stories": 0,
        "Abgeschl. Stories": 0,
        "Backlog-Änderung Stories": 0,
    }

    # 1. Daten-Provider initialisieren
    data_provider = ProjectDataProvider(epic_id=epic_key, hierarchy_config=JIRA_TREE_FULL)
    if not data_provider.is_valid():
        logger.error(f"Konnte keine gültigen Daten für das Epic '{epic_key}' laden.")
        return default_metrics

    story_keys = {k for k, v in data_provider.issue_details.items() if v.get('type') == 'Story'}
    if not story_keys:
        logger.info("Keine Stories in der Hierarchie gefunden.")
        return default_metrics # --- NEU: Rückgabe im Fehlerfall

    # --- NEU: Gesamtzahl erfassen ---
    total_stories = len(story_keys)

    # 2. Start-, End- und letztes Aktivitätsdatum für ALLE Stories ermitteln
    all_stories_data = []
    statuses_to_exclude_open = {'closed', 'resolved'}

    activities_by_key = defaultdict(list)
    for act in data_provider.all_activities:
        activities_by_key[act.get('issue_key')].append(act)

    for key in story_keys:
        story_info = {"key": key, "start_date": None, "end_date": None, "last_activity_date": None}
        details = data_provider.issue_details.get(key)

        if not details:
            logger.info(f"Keine Details für Story {key} gefunden, wird übersprungen.")
            continue

        # 1. Erstellungsdatum (start_date)
        created_str = details.get("created")
        if created_str:
            try:
                story_info["start_date"] = datetime.fromisoformat(created_str).date()
            except (ValueError, TypeError):
                logger.info(f"Ungültiges 'created'-Datum für {key}: {created_str}")
                continue
        else:
            logger.info(f"Kein 'created'-Datum für {key} gefunden.")
            continue

        # 2. Abschlussdatum (end_date)
        closed_str = details.get("closed_date")
        resolved_str = details.get("resolved")
        end_date_str = closed_str or resolved_str

        if end_date_str:
            try:
                story_info["end_date"] = datetime.fromisoformat(end_date_str).date()
            except (ValueError, TypeError):
                logger.info(f"Ungültiges 'End'-Datum für {key}: {end_date_str}")
                pass

        # 3. Letztes Aktivitätsdatum (last_activity_date)
        key_activities = activities_by_key.get(key)
        if key_activities:
            try:
                story_info["last_activity_date"] = datetime.fromisoformat(key_activities[-1]['zeitstempel_iso']).date()
            except (ValueError, TypeError):
                logger.info(f"Ungültiges 'last_activity_date' für {key}")
                pass

        if not story_info["last_activity_date"]:
             story_info["last_activity_date"] = story_info["start_date"]

        all_stories_data.append(story_info)


    # --- Start der Analysen ---
    newly_created_stories = [
        s for s in all_stories_data
        if s['start_date'] and start_date <= s['start_date'] <= stop_date
    ]
    recently_closed_stories = [
        s for s in all_stories_data
        if s['end_date'] and start_date <= s['end_date'] <= stop_date
    ]

    open_stories_data = []
    for story in all_stories_data:
        if not story['start_date']: continue
        details = data_provider.issue_details.get(story['key'], {})
        status = details.get('status')
        if status and status.lower() not in statuses_to_exclude_open:
            story['status'] = status
            open_stories_data.append(story)

    # --- Metriken für Rückgabe berechnen ---
    num_created = len(newly_created_stories)
    num_closed = len(recently_closed_stories)
    backlog_change = num_created - num_closed
    num_open = len(open_stories_data)


    # --- Ausgabe der Ergebnisse (BLEIBT UNVERÄNDERT FÜR DAS TEXT-LOG) ---
    print(f"\n===== Backlog-Analyse für Epic {epic_key} ({date_range_str}) =====")
    print("\n--- 📈 Backlog-Veränderung ---")
    print(f"Gesamtzahl der Stories unter {epic_key}: {total_stories}")
    if backlog_change > 0:
        print(f"Der Story-Backlog ist im Zeitraum {date_range_str} um {backlog_change} Stories gewachsen.")
    elif backlog_change < 0:
        print(f"Der Story-Backlog ist im Zeitraum {date_range_str} um {abs(backlog_change)} Stories geschrumpft.")
    else:
        print(f"Der Story-Backlog ist im Zeitraum {date_range_str} konstant geblieben.")
    print(f"(Neu erstellt: {num_created}, Abgeschlossen: {num_closed})\n")


    print(f"--- ✨ {len(newly_created_stories)} Im Zeitraum erstellte Stories ---")
    if newly_created_stories:
        sorted_new = sorted(newly_created_stories, key=lambda x: x['start_date'], reverse=True)
        for story in sorted_new:
            days_ago = (datetime.now().date() - story['start_date']).days
            print(f"  - {story['key']:<15} | Erstellt am: {story['start_date'].strftime('%d.%m.%Y')} (vor {days_ago} Tagen)")
    else:
        print("  Keine Stories in diesem Zeitraum erstellt.")
    print()

    print(f"--- ✅ {len(recently_closed_stories)} Im Zeitraum abgeschlossene Stories ---")
    if recently_closed_stories:
        valid_closed_stories = []
        for story in recently_closed_stories:
            if story['start_date']:
                story['laufzeit'] = (story['end_date'] - story['start_date']).days
                valid_closed_stories.append(story)

        sorted_closed = sorted(valid_closed_stories, key=lambda x: x['end_date'], reverse=True)

        for story in sorted_closed:
            print(f"  - {story['key']:<15} | Erstellt: {story['start_date'].strftime('%d.%m.%Y')} | Abgeschlossen: {story['end_date'].strftime('%d.%m.%Y')} (Laufzeit: {story['laufzeit']} Tage)")
    else:
        print("  Keine Stories in diesem Zeitraum abgeschlossen.")
    print()


    print(f"--- 📝 {len(open_stories_data)} Offene Stories (sortiert nach Status und letzter Aktivität) ---")
    if open_stories_data:
        status_order_list = [
            "FUNNEL", "ANALYSIS", "REFINEMENT", "BACKLOG", "IN PROGRESS", "WAITING",
            "REVIEW", "IN DEVELOPMENT TEST", "TESTABLE", "IN TEST", "RESOLVED", "CLOSED"
        ]
        status_priority_map = {status: index for index, status in enumerate(status_order_list)}
        max_prio = len(status_priority_map)

        sorted_by_date = sorted(open_stories_data, key=lambda x: x['last_activity_date'], reverse=True)
        sorted_open = sorted(
            sorted_by_date,
            key=lambda x: status_priority_map.get(x.get('status', '').upper(), max_prio)
        )

        for story in sorted_open:
            last_activity_days_ago = (datetime.now().date() - story['last_activity_date']).days
            status_display = story.get('status', 'N/A')
            print(f"  - {story['key']:<15} | Status: {status_display:<20} | Letzte Aktivität: {story['last_activity_date'].strftime('%d.%m.%Y')} (vor {last_activity_days_ago} Tagen)")
    else:
        print("  Alle Stories sind geschlossen oder erledigt.")

    # --- NEU: Metriken-Dictionary zurückgeben ---
    metrics = {
        "Gesamtzahl Stories": total_stories, # Explizite Anforderung
        "Offene Stories": num_open,
        "Erstellte Stories": num_created,
        "Abgeschl. Stories": num_closed,
        "Backlog-Änderung Stories": backlog_change,
    }
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Führt eine umfassende Analyse des Story-Backlogs für ein Jira Business Epic durch."
    )
    # ... (Restlicher __main__ Block bleibt unverändert) ...
    parser.add_argument(
        "--issue",
        dest="epic_key",
        type=str,
        required=True,
        help="Der Jira-Key des Business Epics (z.B. 'BEMABU-1234')."
    )
    parser.add_argument(
        "--start_date",
        type=str,
        default='01.01.2025',
        help="Startdatum für die Analyse (Format: DD.MM.YYYY). Standard: 01.01.2025"
    )
    parser.add_argument(
        "--stop_date",
        type=str,
        default=None,
        help="Enddatum (inklusiv) für die Analyse (Format: DD.MM.YYYY). Standard: Heute"
    )
    parser.add_argument(
        "--end_date",
        dest="stop_date", # Schreibt auf die gleiche Variable wie --stop_date
        type=str,
        help="Synonym für --stop_date."
    )
    args = parser.parse_args()

    date_format = '%d.%m.%Y'
    try:
        start_date_obj = datetime.strptime(args.start_date, date_format).date()
    except ValueError:
        logger.error(f"Ungültiges Startdatum: '{args.start_date}'. Bitte das Format DD.MM.YYYY verwenden.")
        sys.exit(1)

    stop_date_obj = None
    if args.stop_date:
        try:
            stop_date_obj = datetime.strptime(args.stop_date, date_format).date()
        except ValueError:
            logger.error(f"Ungültiges Enddatum: '{args.stop_date}'. Bitte das Format DD.MM.YYYY verwenden.")
            sys.exit(1)
    else:
        stop_date_obj = datetime.now().date()

    if start_date_obj > stop_date_obj:
        logger.error(f"Fehler: Das Startdatum ({args.start_date}) liegt nach dem Enddatum ({stop_date_obj.strftime(date_format)}).")
        sys.exit(1)

    # Der Rückgabewert wird hier verworfen, was für den direkten Aufruf OK ist.
    analyze_story_backlog(args.epic_key, start_date_obj, stop_date_obj)
