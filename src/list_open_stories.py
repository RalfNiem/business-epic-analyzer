"""
Listet alle offenen Stories für ein gegebenes Business Epic, gruppiert nach
den zugehörigen technischen Epics.

Funktionsweise:
1.  Nimmt einen Business-Epic-Key als Kommandozeilenargument entgegen.
2.  Lädt die vollständige Jira-Hierarchie für dieses Business Epic.
3.  Identifiziert alle technischen Epics und alle Stories innerhalb dieser Hierarchie.
4.  Filtert die Stories, um nur diejenigen beizubehalten, die nicht den Status
    'Closed' oder 'Resolved' haben.
5.  Gruppiert die offenen Stories unter ihrem jeweiligen technischen Epic.
6.  Gibt die Ergebnisse in einem formatierten, leicht lesbaren Format auf der
    Konsole aus.

Usage:
    python src/list_open_stories.py --issue <BUSINESS_EPIC_KEY>
    Beispiel:
    python src/list_open_stories.py --issue BEMABU-2087
"""

import os
import sys
import argparse
import networkx as nx
from collections import defaultdict

# Fügt das Projekt-Root-Verzeichnis zum Python-Pfad hinzu
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.project_data_provider import ProjectDataProvider
from utils.config import JIRA_TREE_FULL
from utils.logger_config import logger

def list_open_stories_for_be(business_epic_key: str):
    """
    Sammelt und druckt offene Stories für ein Business Epic.

    Args:
        business_epic_key (str): Der Jira-Key des Business Epics.
    """
    logger.info(f"Starte Analyse für offene Stories unter Business Epic: {business_epic_key}")

    # 1. Daten über den ProjectDataProvider laden
    data_provider = ProjectDataProvider(epic_id=business_epic_key, hierarchy_config=JIRA_TREE_FULL)
    if not data_provider.is_valid():
        print(f"Fehler: Konnte keine gültigen Daten für das Epic '{business_epic_key}' laden.")
        return

    issue_details = data_provider.issue_details
    issue_tree = data_provider.issue_tree

    # 2. Alle technischen Epics und Stories in der Hierarchie identifizieren
    all_epics = {key for key, details in issue_details.items() if details.get('type') == 'Epic'}
    all_stories = {key for key, details in issue_details.items() if details.get('type') == 'Story'}

    # 3. Stories filtern, die nicht 'Closed' oder 'Resolved' sind
    open_stories = []
    for story_key in all_stories:
        status = issue_details.get(story_key, {}).get('status', '').lower()
        if status not in ['closed', 'resolved']:
            open_stories.append(story_key)

    # Titel des Business Epics holen
    be_title = issue_details.get(business_epic_key, {}).get('title', 'Titel nicht gefunden')

    # Geänderte Ausgabe
    print(f"Jira Issue {business_epic_key} - {be_title}")
    print(f"Total Jira Stories with status not closed/resolved: {len(open_stories)}")

    if not open_stories:
        return

    # 4. Offene Stories den jeweiligen technischen Epics zuordnen
    stories_by_epic = defaultdict(list)
    for story_key in open_stories:
        parent_epic = None
        try:
            ancestors = nx.ancestors(issue_tree, story_key)
            parent_epics = ancestors.intersection(all_epics)
            if parent_epics:
                parent_epic = parent_epics.pop()
        except nx.NetworkXError:
            logger.warning(f"Konnte Story {story_key} keinem Epic zuordnen.")
            continue

        if parent_epic:
            stories_by_epic[parent_epic].append(story_key)

    # 5. Ergebnisse im gewünschten Format ausgeben
    sorted_epics = sorted(stories_by_epic.keys())

    for epic_key in sorted_epics:
        epic_title = issue_details.get(epic_key, {}).get('title', 'Titel nicht gefunden')
        print(f"\n{epic_key} {epic_title}")

        sorted_stories = sorted(stories_by_epic[epic_key])
        for story_key in sorted_stories:
            story_info = issue_details.get(story_key, {})
            status = story_info.get('status', 'N/A')
            title = story_info.get('title', 'Titel nicht gefunden')

            # --- VERBESSERTE FORMATIERUNG ---
            # Hier werden f-Strings genutzt, um die Spalten auszurichten:
            # - {story_key:<15}  -> linksbündig, 15 Zeichen breit
            # - {status:<20}     -> linksbündig, 20 Zeichen breit
            print(f"- {story_key:<15} | Status: {status:<20} | {title}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Listet offene Jira Stories für ein Business Epic, gruppiert nach technischen Epics."
    )
    parser.add_argument(
        "--issue",
        type=str,
        required=True,
        help="Der Jira-Key des zu analysierenden Business Epics (z.B. 'BEMABU-1234')."
    )
    args = parser.parse_args()

    list_open_stories_for_be(args.issue)
