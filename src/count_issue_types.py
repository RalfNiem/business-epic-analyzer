"""
Zählt und analysiert alle Jira-Issue-Typen unterhalb eines gegebenen Business Epics.

Funktionsweise:
1.  Nimmt einen Business-Epic-Key als Kommandozeilenargument.
2.  Lädt die vollständige Jira-Hierarchie für dieses Business Epic.
3.  Zählt die Vorkommen jedes einzelnen Issue-Typs (z.B. Epic, Story, Initiative).
4.  Gibt eine übersichtliche, formatierte Zusammenfassung der Zählung auf der
    Konsole aus.

Usage:
    python src/count_issue_types.py --issue <BUSINESS_EPIC_KEY>
    Beispiel:
    python src/count_issue_types.py --issue BEMABU-2087
"""

import os
import sys
import argparse
from collections import Counter

# Fügt das Projekt-Root-Verzeichnis zum Python-Pfad hinzu
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.project_data_provider import ProjectDataProvider
from utils.config import JIRA_TREE_FULL
from utils.logger_config import logger

def analyze_issue_types(business_epic_key: str):
    """
    Sammelt alle Jira-Issues für ein Business Epic und zählt sie nach Typ.

    Args:
        business_epic_key (str): Der Jira-Key des Business Epics.
    """
    logger.info(f"Sammle alle Jira Issues für Business Epic: {business_epic_key}")

    # 1. Daten über den ProjectDataProvider laden
    data_provider = ProjectDataProvider(epic_id=business_epic_key, hierarchy_config=JIRA_TREE_FULL)
    if not data_provider.is_valid():
        print(f"Fehler: Konnte keine gültigen Daten für das Epic '{business_epic_key}' laden.")
        return

    issue_details = data_provider.issue_details
    be_title = issue_details.get(business_epic_key, {}).get('title', 'Titel nicht gefunden')

    # 2. Alle Issue-Typen aus den Details extrahieren
    all_issue_types = [details.get('type') for details in issue_details.values() if details.get('type')]

    # 3. Die Vorkommen jedes Typs zählen
    type_counts = Counter(all_issue_types)

    # 4. Ergebnisse formatiert ausgeben
    print(f"\nJira Issue {business_epic_key} - {be_title}")
    print("\n--- Analyse nach Issue-Typ ---")

    # Sortiere die Ergebnisse nach der Anzahl (absteigend)
    sorted_types = sorted(type_counts.items(), key=lambda item: item[1], reverse=True)

    for issue_type, count in sorted_types:
        # Fügt ein 's' hinzu, wenn der Zähler nicht 1 ist
        plural_suffix = 's' if count != 1 else ''
        print(f"{count} {issue_type}{plural_suffix}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Analysiert und zählt Jira-Issues nach Typ für ein gegebenes Business Epic."
    )
    parser.add_argument(
        "--issue",
        type=str,
        required=True,
        help="Der Jira-Key des zu analysierenden Business Epics (z.B. 'BEMABU-1234')."
    )
    args = parser.parse_args()

    analyze_issue_types(args.issue)
