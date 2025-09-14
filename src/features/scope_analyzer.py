"""
Analysiert den Umfang, die Struktur und den Aufwand eines Projekts.

Diese Klasse ist darauf spezialisiert, quantitative Metriken zur Größe und
Komplexität eines Projekts zu berechnen. Sie untersucht die gesamte Hierarchie
von Issues, die unter einem Business Epic zusammengefasst sind, und liefert
wichtige Kennzahlen für das Projektmanagement und die Portfolioplanung.

Die Analyse umfasst:
-   **Größenmetriken:** Zählt die Gesamtzahl der Issues, die Anzahl der technischen
    Epics und die Anzahl der User Stories.
-   **Aufwandsschätzung:** Aggregiert die Story Points aller User Stories zu einer
    Gesamtsumme.
-   **Strukturanalyse:** Identifiziert, auf wie viele verschiedene Jira-Projekte
    (Teams/Systeme) sich die Arbeit verteilt. Dies dient als Indikator für die
    organisatorische Komplexität und den Koordinationsaufwand.
-   **Qualitative Bewertung:** Generiert eine textuelle Zusammenfassung, die das
    Projekt basierend auf den ermittelten Metriken als "klein", "mittel" oder "groß"
    einstuft und eine Empfehlung bezüglich des Management-Fokus ausspricht,
    insbesondere bei hoher Komplexität.
"""

# src/features/scope_analyzer.py
import statistics
import json
from src.utils.project_data_provider import ProjectDataProvider

class ScopeAnalyzer:
    """
    Analysiert den Umfang, die Struktur und den Aufwand eines Projekts.

    Diese Klasse berechnet Metriken zur Größe des Projekts, wie z.B. die Anzahl
    der Issues, die Verteilung auf verschiedene Jira-Projekte und die
    Gesamtsumme der Story Points.
    """

    def _load_project_name_map(self, path='project_name_mapping.json') -> dict:
        """Lädt die Projekt-Namen-Map aus einer JSON-Datei."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f"Projekt-Mapping-Datei nicht gefunden unter: {path}")
            return {}
        except json.JSONDecodeError:
            logger.error(f"Fehler beim Parsen der JSON-Datei: {path}")
            return {}

    def _clean_status_name(self, raw_name: str) -> str:
        """
        Extrahiert und bereinigt einen Status-Namen aus einem rohen String.

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

    def analyze(self, data_provider: ProjectDataProvider) -> dict:
        """
        Führt eine detaillierte Analyse des Projektumfangs und der Struktur durch.

        Diese Methode ermittelt Kennzahlen wie die Gesamtzahl der Issues, die
        Summe der Story Points und die Verteilung der Arbeit auf verschiedene
        Jira-Projekte und technische Epics.

        Args:
            data_provider (ProjectDataProvider): Ein Objekt, das alle notwendigen,
                vorgeladenen Projektdaten enthält.

        Returns:
            dict: Ein Dictionary mit den Analyseergebnissen.
        """
        # Mapping von Projekt-Abkürzung zu vollem Namen basierend auf Jira_Projects.txt
        project_name_map = self._load_project_name_map('./src/utils/project_name_mapping.json')

        issue_details = data_provider.issue_details
        issue_tree = data_provider.issue_tree
        root_epic_id = data_provider.epic_id

        total_issues = len(issue_details)

        epic_keys = [k for k, v in issue_details.items() if v.get('type') == 'Epic']
        story_keys = [k for k, v in issue_details.items() if v.get('type') == 'Story']

        epic_breakdown = {}
        if issue_tree:
            for epic_key in epic_keys:
                epic_breakdown[epic_key] = []
                if issue_tree.has_node(epic_key):
                    for child_key in issue_tree.successors(epic_key):
                        child_details = issue_details.get(child_key)
                        if child_details and child_details['type'] in ['Story', 'Bug']:
                                epic_breakdown[epic_key].append({
                                    "key": child_key,
                                    "type": child_details['type'],
                                    "points": child_details.get('points', 0),
                                    "resolution": child_details.get('resolution', 'N/A')
                                })

        total_story_points = sum(v.get('points', 0) for k, v in issue_details.items() if k in story_keys)
        stories_per_epic_counts = [
            len([c for c in epic_breakdown.get(epic_key, []) if c['type'] == 'Story'])
            for epic_key in epic_keys
        ]

        project_distribution_abbr = {}
        for key, details in issue_details.items():
            issue_type = details.get('type')
            if key == root_epic_id or issue_type in ['Business Epic', 'Bug']:
                continue
            prefix = key.split('-')[0]
            project_distribution_abbr[prefix] = project_distribution_abbr.get(prefix, 0) + 1

        project_distribution_full = {}
        for abbr, count in project_distribution_abbr.items():
            full_name = project_name_map.get(abbr, abbr)
            project_distribution_full[full_name] = project_distribution_full.get(full_name, 0) + count

        sorted_project_distribution = dict(sorted(
            project_distribution_full.items(),
            key=lambda item: item[1],
            reverse=True
        ))

        project_count = len(sorted_project_distribution)

        # +++ NEUE LOGIK FÜR SCOPE EVALUATION (jetzt mit Empfehlung) +++

        num_epics = len(epic_keys)
        num_stories = len(story_keys)

        # Bewertung der Größe
        if num_epics == 0 or num_stories == 0:
            size_evaluation = "ein Business Epic ohne (bislang) erkennbare Umsetzungsanteile"
        elif num_epics < 2 or num_stories < 10:
            size_evaluation = "ein vergleichsweise sehr kleines Business Epic"
        elif num_epics < 5 or num_stories < 20:
            size_evaluation = "ein vergleichsweise eher kleines Business Epic"
        elif num_epics < 10 or num_stories < 35:
            size_evaluation = "ein Business Epic mit vergleichsweise normaler Größe"
        elif num_epics < 20 or num_stories < 70:
            size_evaluation = "ein vergleichsweise großes Business Epic"
        else:
            size_evaluation = "ein vergleichsweise sehr großes Business Epic"

        # Bewertung der Komplexität
        if project_count == 0:
            complexity_evaluation = ""
        elif project_count == 1:
            complexity_evaluation = "ohne übergreifende Abhängigkeiten"
        elif project_count < 3:
            complexity_evaluation = "mit geringen übergreifenden Abhängigkeiten"
        elif project_count < 5:
            complexity_evaluation = "mit normalen übergreifenden Abhängigkeiten"
        else:
            complexity_evaluation = "mit hohen übergreifenden Abhängigkeiten"

        base_evaluation_text = f"Es handelt sich um {size_evaluation} {complexity_evaluation}."

        # Logik für zusätzliche Empfehlungen
        is_large_or_very_large = size_evaluation in ["ein vergleichsweise großes Business Epic", "ein vergleichsweise sehr großes Business Epic"]
        has_high_complexity = complexity_evaluation == "hohen übergreifenden Abhängigkeiten"

        recommendation_text = ""
        # Strikte UND-Bedingung zuerst prüfen
        if is_large_or_very_large and has_high_complexity:
            recommendation_text = "Auf Grund der Größe/Komplexität des Business Epics erscheint eine enge Begleitung des BEO durch das LPM und der beteiligten VSO sinnvoll"
        # Dann die allgemeinere ODER-Bedingung prüfen
        elif is_large_or_very_large or has_high_complexity:
            recommendation_text = "Auf Grund der Größe/Komplexität des Business Epics sollte eine enge Unterstützung des BEO durch die VSO und das LPM in Betracht gezogen werden"

        # Finale Zeichenkette zusammensetzen
        scope_evaluation_text = f"{base_evaluation_text} {recommendation_text}".strip()

        # +++ ENDE NEUE LOGIK +++

        return {
            "total_issues_found": total_issues,
            "total_epics_found": num_epics,
            "total_stories_found": num_stories,
            "total_story_points": total_story_points,
            "stories_per_epic_counts": stories_per_epic_counts,
            "epic_breakdown": epic_breakdown,
            "project_count": project_count,
            "project_distribution": sorted_project_distribution,
            "scope_evaluation": scope_evaluation_text # AKTUALISIERTES FELD
        }
