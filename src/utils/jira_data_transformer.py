"""
Jira Data Transformer (jira_data_transformer.py)

Dieses Modul kapselt die gesamte statische Logik zur **Transformation und Bereinigung** von rohen Jira REST API Antworten in ein standardisiertes, konsistentes
internes Datenformat.

Die Klasse `JiraDataTransformer` ist darauf ausgelegt, von anderen Skripten
(wie z.B. Lade-Skripten) instanziiert und verwendet zu werden, um die
Datenverarbeitung zu zentralisieren und wiederholten Code zu vermeiden.

## Hauptfunktionalität der Klasse

Die zentrale Methode `transform(api_data)` nimmt die rohe JSON-Antwort der Jira API
entgegen und führt die folgenden Schritte aus, um das finale, strukturierte
Issue-Dictionary zu erstellen:

1.  **Feldnamen-Normalisierung**: Wendet die im API-Response enthaltene `name_map` an,
    um Custom Fields und andere Attribute auf ihre lesbaren Namen abzubilden.
2.  **Beschreibungskombination**: Kombiniert die oft getrennten Felder 'Business Scope'
    und 'Description' in einem einzigen, formatierten Feld `description`.
3.  **Akzeptanzkriterien-Parsing**: Konvertiert das rohe 'Acceptance Criteria'-Feld (oft ein mehrzeiliger String)
    in eine saubere Liste von Kriterien, wobei Aufzählungszeichen (`*`, `-`) entfernt werden.
4.  **Aktivitäten-Extraktion**: Filtert das umfangreiche Changelog (`changelog` aus der API-Antwort)
    und extrahiert nur die relevanten Änderungen für einen vordefinierten Satz an kritischen Feldern
    (`ALLOWED_ACTIVITY_FIELDS`), um die Historie zu bereinigen.
5.  **Link- und Hierarchie-Erkennung**:
    * **Link-Parsing**: Extrahiert alle relevanten Issue-Verknüpfungen (einschließlich 'realized\_by'
        und Sub-Tasks) und konsolidiert sie in der Liste `issue_links`.
    * **Parent-Link-Ermittlung**: Findet den hierarchischen Elternschlüssel (`parent_link`) basierend
        auf Feldern wie 'Parent Link', 'Epic Link' oder der 'realizes'-Beziehung.
6.  **Attribut-Normalisierung**: Standardisiert komplexe Felder wie 'Status', 'Assignee' und 'Team'
    auf einfache Namen oder Display-Namen.

## Konstanten

* `PARENT_LINK_TYPES`: Definiert Jira-Typen auf Portfolio-Ebene, die Kind-Issues über
    das Feld "Parent Link" halten können.
* `ALLOWED_ACTIVITY_FIELDS`: Legt fest, welche Feldänderungen aus dem Changelog
    als relevante Aktivitäten extrahiert werden sollen.
"""

# Entfernte Imports: requests, RequestException
import os
from .logger_config import logger

# Logik-Konstanten, die für die Transformation relevant sind
# PARENT_LINK_TYPES wurde entfernt, da es nur von _find_child_issues verwendet wurde.
ALLOWED_ACTIVITY_FIELDS = [
    "Program Increment",
    "status",
    "Fix Version",
    "Version",
    "Target end",
    "Target start",
    "Business Scope",
    "Component",
    "Acceptance Criteria",
    "description",
    "summary",
    "resolution",
    "Epic Child",
    "Sprint",
    "Story Points",
    "Attachment"
    ]

REALIZED_BY_RELATION_NAME = "realizes"

class JiraDataTransformer:
    """
    Kapselt die gesamte Logik zur Transformation von rohen Jira-API-Daten
    in eine saubere, strukturierte Form. Diese Klasse tätigt keine
    eigenen API-Aufrufe.
    """
    def __init__(self):
        """
        Initialisiert den Transformer. Benötigt keine Server- oder
        Token-Abhängigkeiten mehr.
        """
        pass

    def _parse_acceptance_criteria(self, ac_field) -> list:
        """Wandelt das Acceptance-Criteria-Feld in eine saubere Liste um."""
        if not ac_field: return []
        if isinstance(ac_field, list): return ac_field
        if isinstance(ac_field, str):
            lines = [line.strip() for line in ac_field.splitlines() if line.strip()]
            cleaned_lines = []
            for line in lines:
                if line.startswith('* '): cleaned_lines.append(line[2:])
                elif line.startswith('- '): cleaned_lines.append(line[2:])
                else: cleaned_lines.append(line)
            return cleaned_lines
        return []

    def _create_combined_description(self, fields: dict) -> str:
        """Kombiniert 'Business Scope' und 'Description'."""
        business_scope = fields.get('Business Scope', "")
        description = fields.get('Description', "")
        parts = []
        if business_scope and business_scope.strip():
            parts.append(f"*Business Scope*\r\n{business_scope.strip()}")
        if description and description.strip():
            if description.strip().startswith("Description\n"):
                description = description.strip()[12:]
            parts.append(f"*Beschreibung*\r\n{description.strip()}")
        return "\n\n".join(parts)

    def _extract_business_value(self, fields: dict) -> dict:
        """Erstellt das 'business_value' Objekt."""
        return {
            "business_impact": {"scale": fields.get("Business Value", None), "revenue": "", "cost_saving": "", "risk_loss": "", "justification": ""},
            "strategic_enablement": {"scale": fields.get("RROE", None), "risk_minimization": "", "strat_enablement": "", "justification": ""},
            "time_criticality": {"scale": fields.get("Time Criticality", None), "time": fields.get("Due Date", ""), "justification": ""}
        }

    def _extract_activities(self, changelog_data: dict) -> list:
        """Extrahiert relevante Aktivitäten aus dem Changelog."""
        activities = []
        if not changelog_data: return activities
        for entry in changelog_data.get('histories', []):
            author = entry.get('author', {}).get('displayName', 'Unbekannt')
            timestamp = entry.get('created', '')
            for item in entry.get('items', []):
                field_name = item.get('field')
                if field_name in ALLOWED_ACTIVITY_FIELDS:
                    display_name = 'Affects Version' if field_name == 'Version' else field_name

                    if field_name == "description":
                        alter_wert = "old description value not saved"
                        neuer_wert = "new description value not saved"
                    else:
                        alter_wert = item.get('fromString')
                        neuer_wert = item.get('toString')

                    activities.append({
                        "benutzer": author,
                        "feld_name": display_name,
                        "alter_wert": alter_wert,
                        "neuer_wert": neuer_wert,
                        "zeitstempel_iso": timestamp
                    })
        return activities

    def _extract_realized_by_links(self, fields: dict) -> list:
        """Extrahiert 'realized_by' Verknüpfungen."""
        issue_links_list = []
        for link in fields.get('Linked Issues', []):
            relation, issue = (None, None)
            if 'outwardIssue' in link:
                relation = link.get('type', {}).get('inward')
                issue = link.get('outwardIssue')
            elif 'inwardIssue' in link:
                relation = link.get('type', {}).get('outward')
                issue = link.get('inwardIssue')
            if relation == REALIZED_BY_RELATION_NAME and issue:
                issue_links_list.append({"key": issue.get('key'), "summary": issue.get('fields', {}).get('summary'), "relation_type": "realized_by"})
        return issue_links_list

    def _find_parent_key(self, fields: dict) -> str | None:
        """Findet den Parent-Key eines Issues."""
        parent_link = fields.get('Parent Link')
        if isinstance(parent_link, dict): return parent_link.get('key')
        if isinstance(parent_link, str): return parent_link
        epic_link = fields.get('Epic Link')
        if isinstance(epic_link, str): return epic_link
        for link in fields.get('Linked Issues', []):
            if 'outwardIssue' in link and link.get('type', {}).get('outward') == 'realizes':
                return link.get('outwardIssue', {}).get('key')
        return None

    def _get_team_name(self, fields: dict) -> str | None:
        """Extrahiert den Team-Namen."""
        team_value = fields.get('Team')
        if isinstance(team_value, dict): return team_value.get('name', team_value.get('value'))
        if isinstance(team_value, str): return team_value
        return None

    def transform(self, api_data: dict, child_issues_list: list) -> dict:
        """
        Die Hauptmethode, die die rohen API-Daten und eine Liste von
        Kind-Issues entgegennimmt und das vollständig strukturierte
        Issue-Dictionary zurückgibt.

        Args:
            api_data (dict): Die rohe JSON-Antwort von der /issue/{key} API.
            child_issues_list (list): Eine *zuvor geladene* Liste von
                                      Kind-Issues (z.B. Stories in einem Epic).
        """
        name_map = api_data.get('names', {})
        fields = api_data.get('fields', {})
        if name_map:
            fields = {name_map.get(k, k): v for k, v in fields.items()}

        issue_key = api_data.get('key')
        issue_type = fields.get('Issue Type', {}).get('name', '')

        sub_tasks_raw = fields.get('subtasks', [])
        sub_tasks_list = [{"key": task.get('key'), "title": task.get('fields', {}).get('summary', ''), "url": f"/browse/{task.get('key')}", "relation_type": "sub_task"} for task in sub_tasks_raw]

        issue_links_list = self._extract_realized_by_links(fields)
        
        issue_links_list.extend(child_issues_list)
        issue_links_list.extend(sub_tasks_list)

        def get_name(obj): return obj.get('name') if isinstance(obj, dict) else None
        def get_display_name(obj): return obj.get('displayName') if isinstance(obj, dict) else None

        return {
            "key": api_data.get('key'), "issue_type": get_name(fields.get('Issue Type')),
            "title": fields.get('Summary', ""), "status": get_name(fields.get('Status')),
            "resolution": get_name(fields.get('Resolution')), "story_points": fields.get("Story Points", None),
            "parent_link": self._find_parent_key(fields), "description": self._create_combined_description(fields),
            "business_value": self._extract_business_value(fields), "assignee": get_display_name(fields.get('Assignee')),
            "priority": get_name(fields.get('Priority')), "target_start": fields.get('Target start'),
            "target_end": fields.get('Target end'), "fix_versions": [v.get('name') for v in fields.get('Fix Version/s', []) if v],
            "affects_versions": [v.get('name') for v in fields.get('Affects Version/s', []) if v],
            "acceptance_criteria": self._parse_acceptance_criteria(fields.get('Acceptance Criteria')),
            "components": [c.get('name') for c in fields.get('Component/s', []) if c], "labels": fields.get('Labels', []),
            "issue_links": issue_links_list,
            "attachments": [{"filename": a.get('filename'), "url": a.get('content'), "size": f"{round(a.get('size', 0) / 1024)} kB", "date": a.get('created')} for a in fields.get('Attachment', [])],
            "activities": self._extract_activities(api_data.get('changelog', {})), "Issue Id": fields.get('Issue Id', None),
            "Created": fields.get('Created', None), "Resolved": fields.get('Resolved', None),
            "Closed Date": fields.get('Closed Date', None), "Creator": get_display_name(fields.get('Creator')),
            "Team": self._get_team_name(fields)
        }
