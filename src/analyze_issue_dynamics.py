"""
Führt eine Analyse der Issue-Dynamik für einen gegebenen Jira-Root-Knoten durch,
um Einblicke in den Fortschritt und die "Gesundheit" des Portfolios zu erhalten.

Dieses Skript analysiert die gesamte Hierarchie eines Jira-Root-Knotens und
konzentriert sich dabei auf einen konfigurierbaren Issue-Typ (z.B. 'Epic', 'Initiative').
Es berechnet und präsentiert die folgenden Schlüsselanalysen für einen
benutzerdefinierten Zeitraum:

Funktionsweise:
1.  **Datenbeschaffung:** Lädt die vollständige Issue-Hierarchie und alle
    zugehörigen Aktivitäten für das angegebene Epic.
2.  **Zeitanalyse:** Ermittelt für jedes Issue des Ziel-Typs das exakte
    Erstellungsdatum (aus 'created') und das Abschlussdatum (aus 'closed_date'/'resolved').
3.  **Dynamik-Analyse:**
    - **Gesamtanzahl & Status:** Zählt alle Issues des Ziel-Typs unter dem
      Root-Knoten und gruppiert sie nach ihrem aktuellen Status (heute).
    - **Neu erstellte Issues:** Listet alle Issues des Ziel-Typs auf, die
      innerhalb des Zeitraums erstellt wurden.
    - **Kürzlich abgeschlossene Issues:** Zeigt alle Issues des Ziel-Typs,
      die im Zeitraum abgeschlossen wurden (inkl. Erstellungsdatum und Laufzeit).
    - **Geänderte Issues:** Identifiziert Issues des Ziel-Typs, die bereits
      vor dem Zeitraum existierten und deren Status sich *innerhalb* des
      Zeitraums (zwischen start_date und stop_date) geändert hat.

Usage:
    - Analyse mit Standard-Zeitraum ('01.01.2025' bis Heute) und Standard-Typ ('Epic'):
      python src/analyze_epic_dynamics.py --issue BEMABU-1234

    - Analyse für einen benutzerdefinierten Zeitraum:
      python src/analyze_epic_dynamics.py --issue BEMABU-1234 --start_date 01.01.2024 --stop_date 31.03.2024

    - Analyse für einen anderen Issue-Typ (z.B. Initiative):
      python src/analyze_epic_dynamics.py --issue BEMABU-1234 --issue_type Initiative
"""

import os
import sys
import argparse
from datetime import datetime, timedelta, date
from collections import defaultdict, Counter

# Fügt das Projekt-Root-Verzeichnis zum Python-Pfad hinzu
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.project_data_provider import ProjectDataProvider
from utils.config import JIRA_TREE_FULL, JIRA_ISSUES_DIR
from utils.logger_config import logger

# --- (Funktionen clean_status_name und get_status_at_date bleiben gleich) ---
def clean_status_name(raw_name: str) -> str:
    """Bereinigt einen rohen Status-Namen aus dem Aktivitätsprotokoll."""
    if not raw_name: return "N/A"
    if '[' in raw_name:
        try:
            return raw_name.split(':')[1].split('[')[0].strip().upper()
        except IndexError:
            return raw_name.strip().upper()
    return raw_name.strip().upper()

def get_status_at_date(key_activities: list, target_date: date, default_status: str = "UNKNOWN") -> str:
    """
    Ermittelt den Status eines Issues (basierend auf seinen Aktivitäten)
    zu einem bestimmten Zieldatum.
    """
    if not key_activities:
        return default_status

    for activity in reversed(key_activities):
        activity_date = datetime.fromisoformat(activity['zeitstempel_iso']).date()

        if activity_date > target_date:
            continue

        if activity.get('feld_name') == 'status':
            return clean_status_name(activity.get('neuer_wert', 'Unknown'))

    first_activity_date = datetime.fromisoformat(key_activities[0]['zeitstempel_iso']).date()
    if first_activity_date > target_date:
        return "NOT_CREATED_YET"
    else:
        for activity in key_activities:
            if activity.get('feld_name') == 'status':
                 return clean_status_name(activity.get('alter_wert', default_status))
        return default_status


# --- MODIFIZIERT: 'issue_type' als Parameter, gibt jetzt dict zurück ---
def analyze_epic_dynamics(root_key: str, start_date: date, stop_date: date, issue_type: str):
    """
    Führt eine Analyse der Dynamik für einen Jira-Root-Knoten und
    einen bestimmten Issue-Typ durch.

    Args:
        root_key (str): Der Jira-Key des Business Epics.
        start_date (date): Das Startdatum des Analysezeitraums.
        stop_date (date): Das Enddatum des Analysezeitraums.
        issue_type (str): Der zu analysierende Issue-Typ (z.B. 'Epic').

    Returns:
        dict: Ein Dictionary mit den Schlüsselmetriken für die CSV-Zusammenfassung.
    """
    date_range_str = f"{start_date.strftime('%d.%m.%Y')} - {stop_date.strftime('%d.%m.%Y')}"
    logger.info(f"Starte {issue_type}-Dynamik-Analyse für Root-Knoten: {root_key} (Zeitraum: {date_range_str})")

    # --- NEU: Standard-Rückgabewerte für Fehlerfälle ---
    default_metrics = {
        "Gesamtzahl Epics": 0,
        "% Backlog": 0.0,
        "% In Progress": 0.0,
        "% Closed": 0.0,
        "Erstellte Epics": 0,
        "Abgeschl. Epics": 0,
        "Epics Statusänderung": 0,
    }

    # 1. Daten-Provider initialisieren
    data_provider = ProjectDataProvider(epic_id=root_key, hierarchy_config=JIRA_TREE_FULL)
    if not data_provider.is_valid():
        logger.error(f"Konnte keine gültigen Daten für das Epic '{root_key}' laden oder Hierarchie ist leer.")
        print(f"Fehler: Konnte keine gültigen Daten für das Epic '{root_key}' laden oder die Hierarchie ist leer.", file=sys.stderr)
        return default_metrics

    # Filterung auf Basis des 'issue_type' Parameters
    issue_type_lower = issue_type.lower()
    target_issue_keys = {
        k for k, v in data_provider.issue_details.items()
        if v.get('type', '').lower() == issue_type_lower
    }

    if not target_issue_keys:
        logger.info(f"Keine Issues (Typ '{issue_type}') in der Hierarchie von '{root_key}' gefunden.")
        return default_metrics # --- NEU: Rückgabe im Fehlerfall

    # 2. Zeitpunkte und Status für ALLE Issues des Ziel-Typs ermitteln
    all_issues_data = []
    activities_by_key = defaultdict(list)
    for act in data_provider.all_activities:
        activities_by_key[act.get('issue_key')].append(act)

    for key in activities_by_key:
        activities_by_key[key].sort(key=lambda x: x['zeitstempel_iso'])

    for key in target_issue_keys:
        details = data_provider.issue_details.get(key, {})
        issue_info = {
            "key": key,
            "start_date": None,
            "end_date": None,
            "status_at_start_date": "UNKNOWN",
            "status_at_stop_date": "UNKNOWN",
            "current_status": clean_status_name(details.get('status', 'Unknown'))
        }

        created_str = details.get("created")
        if created_str:
            try:
                issue_info["start_date"] = datetime.fromisoformat(created_str).date()
            except (ValueError, TypeError):
                logger.info(f"Ungültiges 'created'-Datum für {key}. Überspringe.")
                continue
        else:
            logger.info(f"Kein 'created'-Datum für {key}. Überspringe.")
            continue

        end_str = details.get("closed_date") or details.get("resolved")
        if end_str:
            try:
                issue_info["end_date"] = datetime.fromisoformat(end_str).date()
            except (ValueError, TypeError):
                pass

        key_activities = activities_by_key.get(key, [])
        default_start_status = "FUNNEL"

        issue_info["status_at_start_date"] = get_status_at_date(key_activities, start_date, default_start_status)
        issue_info["status_at_stop_date"] = get_status_at_date(key_activities, stop_date, default_start_status)

        if issue_info["status_at_stop_date"] == "UNKNOWN" and stop_date == datetime.now().date():
             issue_info["status_at_stop_date"] = issue_info["current_status"]

        if issue_info["start_date"] > stop_date:
             issue_info["status_at_stop_date"] = "NOT_CREATED_YET"

        all_issues_data.append(issue_info)

    # --- Analysen durchführen ---
    status_counts = Counter(e['current_status'] for e in all_issues_data)
    total_epics = len(all_issues_data) # Gesamtanzahl speichern

    # --- NEU: Logik zur %-Berechnung (aus altem Parser) ---
    statuses_map = {
        # Kategorie "Backlog"
        "FUNNEL": "Backlog", "BACKLOG": "Backlog", "ANALYSIS": "Backlog", "REVIEW": "Backlog", "WAITING": "Backlog",
        # Kategorie "In Progress"
        "IN PROGRESS": "In Progress",
        # Kategorie "Closed"
        "RESOLVED": "Closed", "DEPLOYMENT": "Closed", "CLOSED": "Closed"
    }

    category_counts = {"Backlog": 0, "In Progress": 0, "Closed": 0}
    for status, count in status_counts.items():
        # .upper() für Robustheit
        category = statuses_map.get(status.upper(), "Other")
        if category in category_counts:
            category_counts[category] += count

    backlog_count = category_counts["Backlog"]
    progress_count = category_counts["In Progress"]
    closed_count = category_counts["Closed"]

    percent_backlog = (backlog_count / total_epics) * 100 if total_epics > 0 else 0.0
    percent_progress = (progress_count / total_epics) * 100 if total_epics > 0 else 0.0
    percent_closed = (closed_count / total_epics) * 100 if total_epics > 0 else 0.0
    # --- ENDE NEUE LOGIK ---

    newly_created_issues = [
        e for e in all_issues_data
        if e['start_date'] and start_date <= e['start_date'] <= stop_date
    ]
    recently_closed_issues = [
        e for e in all_issues_data
        if e['end_date'] and start_date <= e['end_date'] <= stop_date
    ]
    status_changed_issues = [
        e for e in all_issues_data
        if e['start_date'] and e['start_date'] < start_date and
           e['status_at_start_date'] != e['status_at_stop_date'] and
           e['status_at_stop_date'] != 'NOT_CREATED_YET'
    ]

    # --- NEU: Metriken-Dictionary füllen ---
    metrics = {
        "Gesamtzahl Epics": total_epics,
        "% Backlog": percent_backlog,
        "% In Progress": percent_progress,
        "% Closed": percent_closed,
        "Erstellte Epics": len(newly_created_issues),
        "Abgeschl. Epics": len(recently_closed_issues),
        "Epics Statusänderung": len(status_changed_issues),
    }

    # --- Ausgabe der Ergebnisse (BLEIBT UNVERÄNDERT FÜR DAS TEXT-LOG) ---
    print(f"\n===== {issue_type}-Analyse für Root-Knoten {root_key} ({date_range_str}) =====")

    print(f"\n--- 📊 Übersicht (Stand Heute: {datetime.now().date().strftime('%d.%m.%Y')}) ---")
    print(f"Gesamtzahl der {issue_type}s unter {root_key}: {len(all_issues_data)}")
    print(f"Aktueller Status der {issue_type}s:")

    status_order = [
        "FUNNEL", "BACKLOG", "REVIEW", "ANALYSIS", "IN PROGRESS",
        "WAITING", "VALIDATION", "DEPLOYMENT", "RESOLVED", "CLOSED"
    ]
    printed_statuses = set()

    for status in status_order:
        if status in status_counts:
            count = status_counts[status]
            print(f"  - {status:<20}: {count} Issue(s)")
            printed_statuses.add(status)

    remaining_statuses = sorted([s for s in status_counts if s not in printed_statuses])
    if remaining_statuses:
         print("  --- Andere Status ---")
         for status in remaining_statuses:
              count = status_counts[status]
              print(f"  - {status:<20}: {count} Issue(s)")

    print(f"\n--- 📈 Dynamik im Zeitraum ({date_range_str}) ---")
    print(f"Neu erstellte {issue_type}s: {len(newly_created_issues)}")
    print(f"Abgeschlossene {issue_type}s ('Closed'/'Resolved'): {len(recently_closed_issues)}")
    print(f"{issue_type}s mit Statusänderung (die vorher existierten): {len(status_changed_issues)}")

    print(f"\n--- ✨ Details: {len(newly_created_issues)} Kürzlich erstellte {issue_type}s ---")
    if newly_created_issues:
        for issue in sorted(newly_created_issues, key=lambda x: x['start_date'], reverse=True):
            days_ago = (datetime.now().date() - issue['start_date']).days
            print(f"  - {issue['key']:<15} | Erstellt am: {issue['start_date'].strftime('%d.%m.%Y')} (vor {days_ago} Tagen)")
    else:
        print(f"  Keine {issue_type}s in diesem Zeitraum erstellt.")

    print(f"\n--- ✅ Details: {len(recently_closed_issues)} Kürzlich abgeschlossene {issue_type}s ---")
    if recently_closed_issues:
        for issue in sorted(recently_closed_issues, key=lambda x: x['end_date'], reverse=True):
            laufzeit = (issue['end_date'] - issue['start_date']).days
            start_str = issue['start_date'].strftime('%d.%m.%Y')
            end_str = issue['end_date'].strftime('%d.%m.%Y')
            print(f"  - {issue['key']:<15} | Erstellt: {start_str} | Abgeschlossen: {end_str} (Laufzeit: {laufzeit} Tage)")
    else:
        print(f"  Keine {issue_type}s in diesem Zeitraum abgeschlossen.")

    print(f"\n--- 🔄 Details: {len(status_changed_issues)} {issue_type}s mit Statusänderung ---")
    if status_changed_issues:
        sorted_changed = sorted(status_changed_issues, key=lambda x: (x['status_at_start_date'], x['status_at_stop_date']))
        for issue in sorted_changed:
            print(f"  - {issue['key']:<15} | Statusänderung von '{issue['status_at_start_date']}' zu '{issue['status_at_stop_date']}'")
    else:
        print(f"  Keine {issue_type}s mit Statusänderung im Zeitraum gefunden.")

    # --- NEU: Rückgabe der Metriken ---
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Führt eine Analyse der Dynamik für einen bestimmten Jira-Issue-Typ durch."
    )
    # ... (Restlicher __main__ Block bleibt unverändert) ...
    parser.add_argument(
        "--issue",
        dest="root_key",
        type=str,
        required=True,
        help="Der Jira-Key des Root-Knotens (z.B. 'BEMABU-1234')."
    )
    parser.add_argument(
        "--issue_type",
        type=str,
        default='Epic',
        help="Der zu analysierende Jira-Issue-Typ (z.B. 'Epic', 'Initiative'). Gross-/Kleinschreibung wird ignoriert. Standard: 'Epic'"
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
        dest="stop_date",
        type=str,
        help="Synonym für --stop_date."
    )
    args = parser.parse_args()

    root_file_path = os.path.join(JIRA_ISSUES_DIR, f"{args.root_key}.json")
    if not os.path.exists(root_file_path):
        error_message = f"Fehler: Die JSON-Datei für das angegebene Root-Issue '{args.root_key}' wurde nicht gefunden"
        print(error_message, file=sys.stderr)
        sys.exit(1)


    date_format = '%d.%m.%Y'
    try:
        start_date_obj = datetime.strptime(args.start_date, date_format).date()
    except ValueError:
        error_message = f"Fehler: Ungültiges Startdatum: '{args.start_date}'. Bitte das Format DD.MM.YYYY verwenden."
        logger.error(error_message)
        print(error_message, file=sys.stderr)
        sys.exit(1)

    stop_date_obj = None
    if args.stop_date:
        try:
            stop_date_obj = datetime.strptime(args.stop_date, date_format).date()
        except ValueError:
            error_message = f"Fehler: Ungültiges Enddatum: '{args.stop_date}'. Bitte das Format DD.MM.YYYY verwenden."
            logger.error(error_message)
            print(error_message, file=sys.stderr)
            sys.exit(1)
    else:
        stop_date_obj = datetime.now().date()

    if start_date_obj > stop_date_obj:
        error_message = f"Fehler: Das Startdatum ({args.start_date}) liegt nach dem Enddatum ({stop_date_obj.strftime(date_format)})."
        logger.error(error_message)
        print(error_message, file=sys.stderr)
        sys.exit(1)

    # Der Rückgabewert wird hier verworfen, was für den direkten Aufruf OK ist.
    analyze_epic_dynamics(args.root_key, start_date_obj, stop_date_obj, args.issue_type)
