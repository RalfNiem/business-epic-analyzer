"""
Führt eine detaillierte, zustandsbasierte Analyse von Terminänderungen durch
und generiert eine LLM-basierte Zusammenfassung der Ergebnisse.

Diese Klasse ist darauf spezialisiert, den "Time Creep" – also die schleichende
Verschiebung von Fertigstellungsterminen – auf einer strategischen Ebene
sichtbar zu machen. Sie analysiert nicht nur den aktuellen, sondern den
gesamten chronologischen Verlauf von Zieldaten ('Target end') und geplanten
Auslieferungsquartalen ('Fix Version/s').

Ein besonderer Fokus liegt auf den wichtigsten Planungseinheiten: dem Business
Epic selbst und seinen direkten Nachfolgern (z.B. Portfolio Epics, Initiatives).
Diese Einschränkung dient dazu, das Signal-Rausch-Verhältnis zu verbessern und
die Analyse auf die für die strategische Steuerung relevantesten
Verschiebungen zu konzentrieren.

Kernfunktionalität:
-   Analysiert die Aktivitätshistorie für die Felder 'Target end' und 'Fix Version/s'.
-   Parst und normalisiert verschiedene Datums- und Quartalsformate.
-   Klassifiziert jede Änderung als "TIME_SET" (erstmalige Setzung), "TIME_CREEP"
    (Verschiebung in die Zukunft) oder "TIME_PULL_IN" (Verschiebung in die Vergangenheit).
-   Nutzt ein Large Language Model (LLM), um die identifizierten Terminverschiebungen
    im Kontext der Epic-Beschreibung zu interpretieren und eine prägnante,
    qualitative Zusammenfassung der Termintreue zu erstellen.
"""

import re
import json
import os # +++ NEUER IMPORT
from datetime import datetime, date
from collections import OrderedDict
import networkx as nx

from src.utils.project_data_provider import ProjectDataProvider
from src.utils.logger_config import logger
from src.utils.azure_ai_client import AzureAIClient
from src.utils.prompt_loader import load_prompt_template
from src.utils.token_usage_class import TokenUsage
# +++ NEUER IMPORT für den Dateipfad +++
from src.utils.config import LLM_MODEL_TIME_CREEP, TOKEN_LOG_FILE, JIRA_ISSUES_DIR
from src.utils.formatting_helpers import format_timedelta_to_months_days

class TimeCreepAnalyzer:
    """
    Führt eine detaillierte, zustandsbasierte Analyse von Terminänderungen durch
    und generiert eine LLM-basierte Zusammenfassung der Ergebnisse.
    ... (Rest der Docstring bleibt unverändert) ...
    """

    def __init__(self):
        """Initialisiert den Analyzer mit notwendigen Clients für die LLM-Analyse."""
        self.token_tracker = TokenUsage(log_file_path=TOKEN_LOG_FILE)
        self.azure_client = AzureAIClient(system_prompt="Du bist ein hilfreicher Assistent für die Analyse von Jira-Tickets.")

    # ... (_normalize_fix_version_string, _parse_any_date_string, etc. bleiben unverändert) ...
    def _normalize_fix_version_string(self, raw_str: str) -> str:
        """
        Extrahiert den kanonischen 'PIxx' oder 'Qx_yy' Teil aus einem String.
        """
        if not raw_str:
            return raw_str

        pi_match = re.search(r'(PI\d+)', raw_str)
        if pi_match:
            return pi_match.group(1)

        q_match = re.search(r'(Q\d_\d{2})', raw_str)
        if q_match:
            return q_match.group(1)

        return raw_str

    def _parse_any_date_string(self, date_str: str) -> tuple[date, date] | None:
        """
        Parst ein Datum aus verschiedenen Jira-Formaten.
        """
        if not date_str: return None
        parsed_date = None
        try:
            parsed_date = datetime.fromisoformat(date_str).date()
        except ValueError:
            try:
                cleaned_str = date_str.split(':')[-1].strip()
                parsed_date = datetime.strptime(cleaned_str, '%d/%b/%Y').date()
            except (ValueError, IndexError):
                logger.warning(f"Konnte Datum nicht aus String parsen: '{date_str}'")

        return (parsed_date, parsed_date) if parsed_date else None

    def _parse_fix_version_to_date(self, version_string: str) -> tuple[date, date] | None:
        """
        Wandelt eine 'Fix Version' (PI oder Quartal) in einen exakten Zeitraum um.
        """
        if not version_string: return None

        year, quarter = None, None

        pi_match = re.search(r'PI(\d+)', version_string)
        if pi_match:
            pi_number = int(pi_match.group(1))
            base_pi_for_q1, base_year_short = 27, 25
            pi_offset = pi_number - base_pi_for_q1
            year_offset = pi_offset // 4
            quarter = (pi_offset % 4) + 1
            year = 2000 + base_year_short + year_offset
        else:
            q_match = re.search(r'Q(\d)_(\d{2})', version_string)
            if q_match:
                quarter, year_short = map(int, q_match.groups())
                year = 2000 + year_short

        if year and quarter:
            start_month = (quarter - 1) * 3 + 1
            end_month = start_month + 2
            end_day = 31 if end_month in [1, 3, 5, 7, 8, 10, 12] else (30 if end_month in [4, 6, 9, 11] else (29 if year % 4 == 0 else 28))
            start_date = date(year, start_month, 1)
            end_date = date(year, end_month, end_day)
            return (start_date, end_date)

        return None

    def _compare_dates(self, issue_key, field, old_date, new_date, old_value_str, new_value_str):
        """
        Vergleicht zwei Daten, klassifiziert die Änderung und erstellt ein Event-Dictionary.
        """
        event_type, details = None, ""

        if field == 'Fix Version/s':
            old_display = old_value_str if old_value_str else "None"
            new_display = new_value_str if new_value_str else "None"
        else:
            old_display = old_date.strftime('%Y-%m-%d') if old_date else "None"
            new_display = new_date.strftime('%Y-%m-%d') if new_date else "None"

        if old_date is None and new_date is not None:
            event_type, details = "TIME_SET", f"Termin '{field}' gesetzt auf: {new_display}"
        elif old_date is not None and new_date is None:
            pass # Ignoriert
        elif old_date is not None and new_date is not None and new_date != old_date:
            if new_date > old_date:
                event_type, details = "TIME_CREEP", f"Termin '{field}' verschoben von {old_display} auf {new_display}"
            else:
                event_type, details = "TIME_PULL_IN", f"Termin '{field}' vorgezogen von {old_display} auf {new_display}"

        return {"issue": issue_key, "event_type": event_type, "details": details} if event_type else None


    def _generate_llm_summary(self, all_events: list, data_provider: ProjectDataProvider) -> str:
        """Generiert eine LLM-Zusammenfassung der Time-Creep-Ereignisse."""
        epic_id = data_provider.epic_id
        logger.info(f"Generiere LLM-Zusammenfassung für Time Creep von Epic {epic_id}...")

        try:
            # 1. Formatiere Events für den LLM-Input
            formatted_time_creep_events = []
            for event in all_events:
                if event.get('event_type') == 'TIME_CREEP':
                    formatted_time_creep_events.append(f"- {event['issue']}: {event['details']}")

            time_creep_str = "\n".join(formatted_time_creep_events)
            if not time_creep_str:
                return f"Das Business Epic {epic_id} weist keine signifikanten Terminverschiebungen auf."

            # 2. Lade die rohe JSON-Datei des Business Epics als Kontext
            raw_epic_data = None
            epic_file_path = os.path.join(JIRA_ISSUES_DIR, f"{epic_id}.json")
            try:
                with open(epic_file_path, 'r', encoding='utf-8') as f:
                    raw_epic_data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError) as e:
                logger.error(f"Konnte rohe Epic-Datei für Time-Creep-Analyse nicht laden: {e}")
                return "LLM-Zusammenfassung nicht verfügbar (Rohdaten für Epic fehlen)."

            # 3. Entferne den 'activities'-Schlüssel für einen schlankeren Prompt
            if 'activities' in raw_epic_data:
                del raw_epic_data['activities']

            # 4. Lade das Prompt-Template und formatiere den Prompt
            summary_prompt_template = load_prompt_template('time_creep_summary.yaml', 'user_prompt_template')
            full_user_prompt = summary_prompt_template.format(
                epic_id=epic_id,
                # epic_id_json_summary wird jetzt mit den Rohdaten gefüllt
                epic_id_json_summary=json.dumps(raw_epic_data, indent=2, ensure_ascii=False),
                time_creep=time_creep_str
            )

            # 5. Rufe das LLM auf
            llm_response = self.azure_client.completion(
                model_name=LLM_MODEL_TIME_CREEP,
                user_prompt=full_user_prompt,
                temperature=0.2,
                max_tokens=10000
            )

            # Token-Nutzung loggen
            if self.token_tracker and "usage" in llm_response:
                usage = llm_response["usage"]
                self.token_tracker.log_usage(
                    model=LLM_MODEL_TIME_CREEP,
                    input_tokens=usage.prompt_tokens,
                    output_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                    task_name=f"time_creep_summary"
                )

            return llm_response.get("text", "LLM-Antwort konnte nicht verarbeitet werden.")

        except Exception as e:
            logger.error(f"Fehler bei der Generierung der LLM Time Creep Summary für {epic_id}", exc_info=True)
            return "LLM-Zusammenfassung fehlgeschlagen aufgrund eines internen Fehlers."

    def analyze(self, data_provider: ProjectDataProvider) -> dict:
        """
        Führt die Hauptanalyse der Terminänderungen durch und generiert eine Zusammenfassung.
        FOKUS: Nur der Root-Knoten und seine direkten Nachfolger werden analysiert,
        um die Aufmerksamkeit auf die übergeordnete Ebene zu lenken.
        """
        issue_tree = data_provider.issue_tree
        all_activities = data_provider.all_activities
        issue_details = data_provider.issue_details
        root_node_key = data_provider.epic_id # Der Startpunkt der Analyse

        ALLOWED_TYPES = {'Business Epic', 'Portfolio Epic', 'Initiative', 'Epic'}
        ALLOWED_FIELDS = {'Target end', 'Fix Version/s'}

        # --- NEUE LOGIK START ---
        # 1. Bestimme die zu analysierenden Knoten: Root + direkte Nachfolger
        if not issue_tree.has_node(root_node_key):
            logger.error(f"Root-Knoten {root_node_key} nicht im Issue-Graphen gefunden.")
            return {
                "issue_tree_with_creep": issue_tree,
                "time_creep_events": [],
                "llm_time_creep_summary": "Analyse nicht möglich, da Root-Knoten fehlt."
            }

        # Erstelle eine Liste, die nur den Root-Knoten und seine direkten Kinder enthält.
        nodes_to_analyze = [root_node_key] + list(issue_tree.successors(root_node_key))
        logger.info(f"Analysiere Time Creep für Root-Knoten '{root_node_key}' und seine {len(list(issue_tree.successors(root_node_key)))} direkten Nachfolger.")
        # --- NEUE LOGIK ENDE ---

        activities_by_issue = {}
        for activity in all_activities:
            key = activity.get('issue_key')
            if key:
                activities_by_issue.setdefault(key, []).append(activity)

        # --- GEÄNDERTE SCHLEIFE ---
        # Iteriere nur über die zuvor definierte, eingeschränkte Liste von Knoten.
        for issue_key in nodes_to_analyze:
            issue_activities = activities_by_issue.get(issue_key, [])
            if not issue_activities:
                continue

            if issue_details.get(issue_key, {}).get('type') not in ALLOWED_TYPES:
                continue

            # Die folgende Detailanalyse für ein einzelnes Issue bleibt unverändert.
            relevant_activities = sorted([a for a in issue_activities if a.get('feld_name') in ALLOWED_FIELDS], key=lambda x: x['zeitstempel_iso'])
            if not relevant_activities: continue

            creation_date_str = min(act['zeitstempel_iso'] for act in issue_activities)[:10]
            activities_by_day = OrderedDict()
            for activity in relevant_activities:
                activities_by_day.setdefault(activity['zeitstempel_iso'][:10], []).append(activity)

            events = []
            current_known_states = {'Target end': None, 'Fix Version/s': None}

            for day_str, daily_activities in activities_by_day.items():
                last_activities = {f: next((a for a in reversed(daily_activities) if a.get('feld_name') == f), None) for f in ALLOWED_FIELDS}

                for field, activity in last_activities.items():
                    if not activity: continue

                    raw_new_str = activity.get('neuer_wert')
                    parse_func = self._parse_any_date_string if field == 'Target end' else self._parse_fix_version_to_date
                    new_range = parse_func(raw_new_str)

                    old_state = current_known_states[field]
                    start_of_day_state = None if day_str == creation_date_str else old_state

                    event_data = None
                    raw_old_str = start_of_day_state[0] if start_of_day_state else None
                    norm_new_str = self._normalize_fix_version_string(raw_new_str)
                    norm_old_str = self._normalize_fix_version_string(raw_old_str)

                    old_range = start_of_day_state[1] if start_of_day_state else None
                    old_end_date = old_range[1] if old_range else None
                    new_end_date = new_range[1] if new_range else None

                    if field == 'Fix Version/s':
                        target_end_state = current_known_states['Target end']
                        target_end_date = target_end_state[1][1] if target_end_state else None

                        if new_range and target_end_date and new_range[0] <= target_end_date <= new_range[1]:
                            pass
                        else:
                            if old_end_date != new_end_date:
                                event_data = self._compare_dates(issue_key, field, old_end_date, new_end_date, norm_old_str, norm_new_str)
                    else:
                        if old_end_date != new_end_date:
                            event_data = self._compare_dates(issue_key, field, old_end_date, new_end_date, norm_old_str, norm_new_str)

                    if event_data:
                        event_data['timestamp'] = day_str
                        events.append(event_data)

                    if new_range is not None:
                        current_known_states[field] = (norm_new_str, new_range)

            if events:
                events.sort(key=lambda x: x['timestamp'])
                issue_tree.nodes[issue_key]['time_creep_events'] = events

        # Sammle alle Events für die LLM-Zusammenfassung (jetzt nur noch von den relevanten Knoten)
        all_events = []
        for node_key in nodes_to_analyze:
            if 'time_creep_events' in issue_tree.nodes.get(node_key, {}):
                all_events.extend(issue_tree.nodes[node_key]['time_creep_events'])

        all_events.sort(key=lambda x: x.get('timestamp', ''))

        # Generiere die LLM-Zusammenfassung basierend auf den gefilterten Events
        llm_summary = self._generate_llm_summary(all_events, data_provider)

        # Füge die Zusammenfassung als Attribut zum Root-Knoten hinzu
        if issue_tree.has_node(root_node_key):
            issue_tree.nodes[root_node_key]['llm_time_creep_summary'] = llm_summary

        return {
            "issue_tree_with_creep": issue_tree,
            "time_creep_events": all_events,
            "llm_time_creep_summary": llm_summary
        }
