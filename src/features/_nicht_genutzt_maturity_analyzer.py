"""
Analysiert die Reife von Business Epics basierend auf ihrer Phase
und spezifischen Kriterien, unter Nutzung von LLM für tiefergehende Textanalysen.

Diese Klasse führt eine qualitative Bewertung eines Business Epics durch, um dessen
"Reifegrad" zu bestimmen. Der Reifegrad gibt an, wie gut ein Epic definiert,
geplant und für die nächste Phase im Produktlebenszyklus vorbereitet ist.

Die Analyse ist phasenspezifisch, d.h., die Kriterien ändern sich je nachdem, ob sich
das Epic in der "Funnel"-, "Exploration"- oder einer späteren Phase befindet. Anstatt
nur quantitative Metriken zu verwenden, nutzt dieser Analyzer ein Large Language Model (LLM),
um die Qualität von textuellen Inhalten wie Beschreibungen, Geschäftswert-Begründungen
und Akzeptanzkriterien zu bewerten.

Kernprozesse:
-   Ermittelt die aktuelle Phase des Business Epics anhand seines Statusverlaufs.
-   Führt je nach Phase spezifische Reifegrad-Analysen durch:
    -   **Phase 1 (Funnel):** Bewertet die Qualität der initialen Beschreibung,
        die Präzision des angegebenen Business Value und das Vorhandensein einer
        ersten Aufschlüsselung in Portfolio Epics oder Initiatives.
    -   **Phase 2 (Exploration):** Führt eine Neubewertung der Beschreibung und des
        Business Value durch und prüft zusätzlich die Klarheit der untergeordneten
        Hierarchie sowie die Vollständigkeit von Zieldaten und Fix-Versionen.
-   Nutzt gezielte Prompts, um vom LLM strukturierte JSON-Antworten mit Bewertungen
    (z.B. "Hoch", "Mittel", "Niedrig") und Begründungen zu erhalten.
-   Protokolliert den Token-Verbrauch für alle LLM-Aufrufe.
"""

from datetime import datetime
import json
import re # Needed for potential regex in parsing
from typing import Dict, Any, Optional

from src.utils.project_data_provider import ProjectDataProvider
from src.utils.logger_config import logger
from src.utils.azure_ai_client import AzureAIClient # For LLM calls
from src.utils.token_usage_class import TokenUsage # For token tracking
from src.utils.prompt_loader import load_prompt_template # For loading LLM prompts
from src.utils.config import LLM_MODEL_MATURITY_ASSESSMENT, TOKEN_LOG_FILE # New config for LLM model

class MaturityAnalyzer:
    """
    Analysiert die Reife von Business Epics basierend auf ihrer Phase
    und spezifischen Kriterien, unter Nutzung von LLM für tiefergehende Textanalysen.
    """

    def __init__(self):
        self.token_tracker = TokenUsage(log_file_path=TOKEN_LOG_FILE)
        # Specific system prompt for maturity assessment
        system_prompt = load_prompt_template('maturity_assessment_prompt.yaml', 'system_prompt')
        self.azure_client = AzureAIClient(system_prompt=system_prompt)

    def _get_current_epic_phase(self, all_activities: list, epic_id: str) -> str:
        """
        Bestimmt die aktuelle Phase eines Business Epics basierend auf dem letzten Statuswechsel.
        """
        epic_status_changes = [
            act for act in all_activities
            if act.get('issue_key') == epic_id and act.get('feld_name') == 'Status'
        ]
        if not epic_status_changes:
            # If no status changes, infer from current status in issue_details (requires data provider access)
            # For now, return 'Unknown' or assume 'FUNNEL' if no activities logged.
            logger.warning(f"No status changes found for epic {epic_id}. Cannot determine phase accurately from activities.")
            return "UNKNOWN" # Or get it from data_provider.issue_details[epic_id]['status']

        # Sort by timestamp to get the latest status
        epic_status_changes.sort(key=lambda x: datetime.fromisoformat(x['zeitstempel_iso']), reverse=True)
        latest_status_raw = epic_status_changes[0].get('neuer_wert')
        cleaned_status = self._clean_status_name(latest_status_raw)

        # Phase Classification Logic
        if cleaned_status in ["FUNNEL", "BACKLOG FOR ANALYSIS"]:
            return "Funnel"
        elif cleaned_status in ["ANALYSIS", "REVIEW"]:
            return "Exploration"
        elif cleaned_status == "IN PROGRESS":
            return "Implementation & Deployment"
        elif cleaned_status == "CLOSED":
            # Special handling for 'Closed' epics with 'Rejected' resolution
            # This requires access to the full issue data, not just activities.
            # We'll assume the ProjectDataProvider will have this ready.
            # If a 'Closed' epic has 'Rejected' resolution, it's not truly "Completed" in a valuable sense.
            # For this context, we need to decide if 'Rejected' in 'Closed' means it's not "Completed".
            # From MAGBUS-105311, a "Closed" epic can have "Rejected" resolution.
            # The previous plan suggested excluding rejected issues from the tree building for visualization,
            # but for maturity, we need to process them to evaluate why they were closed/rejected.
            # For now, let's keep it simple based on status.
            return "Completed"
        else:
            return "Unknown"

    def _clean_status_name(self, raw_name: str) -> str:
        """
        Extrahiert und bereinigt einen Status-Namen aus einem rohen String.
        (Copied from StatusAnalyzer for self-containment, or could be a shared utility)
        """
        if not raw_name: return "N/A"
        if '[' in raw_name:
            try: return raw_name.split(':')[1].split('[')[0].strip().upper()
            except IndexError: return raw_name.strip().upper()
        return raw_name.strip().upper()

    def _assess_phase_1_maturity(self, data_provider: ProjectDataProvider, epic_id: str) -> Dict[str, Any]:
        """
        Führt die Reifegradanalyse für Business Epics in Phase 1 (Funnel) durch.
        Nutzt LLM für qualitative Bewertungen.
        """
        epic_data = data_provider.issue_details.get(epic_id, {})
        epic_description = epic_data.get('description', 'No description provided.')
        epic_title = epic_data.get('title', 'No title provided.')
        epic_business_value = epic_data.get('business_value', {})
        epic_acceptance_criteria = epic_data.get('acceptance_criteria', [])

        maturity_results = {
            "description_quality": {"rating": "N/A", "justification": ""},
            "business_value_precision": {"rating": "N/A", "justification": ""},
            "initial_breakdown_present": False,
            "initial_breakdown_quality": {"rating": "N/A", "justification": ""}
        }

        # 1. Business Epic Description & Goals Quality (LLM)
        desc_prompt_template = load_prompt_template('maturity_assessment_prompt.yaml', 'phase1_description_prompt')
        desc_prompt = desc_prompt_template.format(
            epic_title=epic_title,
            epic_description=epic_description,
            acceptance_criteria=json.dumps(epic_acceptance_criteria)
        )
        try:
            llm_response_desc = self.azure_client.completion(
                model_name=LLM_MODEL_MATURITY_ASSESSMENT,
                user_prompt=desc_prompt,
                response_format={"type": "json_object"}
            )
            parsed_response_desc = json.loads(llm_response_desc["text"])
            maturity_results["description_quality"] = {
                "rating": parsed_response_desc.get("rating", "N/A"),
                "justification": parsed_response_desc.get("justification", "")
            }
            # Log token usage
            if self.token_tracker and "usage" in llm_response_desc:
                self.token_tracker.log_usage(
                    model=LLM_MODEL_MATURITY_ASSESSMENT,
                    input_tokens=llm_response_desc["usage"].prompt_tokens,
                    output_tokens=llm_response_desc["usage"].completion_tokens,
                    total_tokens=llm_response_desc["usage"].total_tokens,
                    task_name=f"phase1_description_assessment_epic_{epic_id}"
                )
        except Exception as e:
            logger.error(f"LLM call failed for Phase 1 description assessment of {epic_id}: {e}")
            maturity_results["description_quality"]["justification"] = f"LLM assessment failed: {e}"


        # 2. Business Value Precision (LLM)
        bv_prompt_template = load_prompt_template('maturity_assessment_prompt.yaml', 'phase1_business_value_prompt')
        bv_prompt = bv_prompt_template.format(
            business_value_json=json.dumps(epic_business_value)
        )
        try:
            llm_response_bv = self.azure_client.completion(
                model_name=LLM_MODEL_MATURITY_ASSESSMENT,
                user_prompt=bv_prompt,
                response_format={"type": "json_object"}
            )
            parsed_response_bv = json.loads(llm_response_bv["text"])
            maturity_results["business_value_precision"] = {
                "rating": parsed_response_bv.get("rating", "N/A"),
                "justification": parsed_response_bv.get("justification", "")
            }
            # Log token usage
            if self.token_tracker and "usage" in llm_response_bv:
                self.token_tracker.log_usage(
                    model=LLM_MODEL_MATURITY_ASSESSMENT,
                    input_tokens=llm_response_bv["usage"].prompt_tokens,
                    output_tokens=llm_response_bv["usage"].completion_tokens,
                    total_tokens=llm_response_bv["usage"].total_tokens,
                    task_name=f"phase1_business_value_assessment_epic_{epic_id}"
                )
        except Exception as e:
            logger.error(f"LLM call failed for Phase 1 business value assessment of {epic_id}: {e}")
            maturity_results["business_value_precision"]["justification"] = f"LLM assessment failed: {e}"


        # 3. Initial Breakdown Structure (Direct Data Check)
        children_keys = list(data_provider.issue_tree.successors(epic_id))
        portfolio_epics_found = []
        initiatives_found = []

        if children_keys:
            maturity_results["initial_breakdown_present"] = True
            for child_key in children_keys:
                child_detail = data_provider.issue_details.get(child_key)
                if child_detail:
                    child_type = child_detail.get('type')
                    if child_type == 'Portfolio Epic':
                        portfolio_epics_found.append(child_key)
                    elif child_type == 'Initiative':
                        initiatives_found.append(child_key)

            if portfolio_epics_found or initiatives_found:
                breakdown_details = {
                    "portfolio_epics": portfolio_epics_found,
                    "initiatives": initiatives_found
                }
                # LLM for breakdown quality - provide context of children
                breakdown_prompt_template = load_prompt_template('maturity_assessment_prompt.yaml', 'phase1_breakdown_prompt')
                # Prepare a summary of children's titles and types for the LLM
                children_summary = []
                for child_key in children_keys:
                    child_detail = data_provider.issue_details.get(child_key)
                    if child_detail:
                        children_summary.append({
                            "key": child_key,
                            "type": child_detail.get('type'),
                            "title": child_detail.get('title')
                        })
                breakdown_prompt = breakdown_prompt_template.format(
                    epic_title=epic_title,
                    children_summary=json.dumps(children_summary)
                )

                try:
                    llm_response_breakdown = self.azure_client.completion(
                        model_name=LLM_MODEL_MATURITY_ASSESSMENT,
                        user_prompt=breakdown_prompt,
                        response_format={"type": "json_object"}
                    )
                    parsed_response_breakdown = json.loads(llm_response_breakdown["text"])
                    maturity_results["initial_breakdown_quality"] = {
                        "rating": parsed_response_breakdown.get("rating", "N/A"),
                        "justification": parsed_response_breakdown.get("justification", "")
                    }
                    # Log token usage
                    if self.token_tracker and "usage" in llm_response_breakdown:
                        self.token_tracker.log_usage(
                            model=LLM_MODEL_MATURITY_ASSESSMENT,
                            input_tokens=llm_response_breakdown["usage"].prompt_tokens,
                            output_tokens=llm_response_breakdown["usage"].completion_tokens,
                            total_tokens=llm_response_breakdown["usage"].total_tokens,
                            task_name=f"phase1_breakdown_assessment_epic_{epic_id}"
                        )
                except Exception as e:
                    logger.error(f"LLM call failed for Phase 1 breakdown assessment of {epic_id}: {e}")
                    maturity_results["initial_breakdown_quality"]["justification"] = f"LLM assessment failed: {e}"
            else:
                maturity_results["initial_breakdown_quality"]["justification"] = "No relevant Portfolio Epics or Initiatives found as direct children."


        return maturity_results


    def _assess_phase_2_maturity(self, data_provider: ProjectDataProvider, epic_id: str) -> Dict[str, Any]:
        """
        Führt die Reifegradanalyse für Business Epics in Phase 2 (Exploration) durch.
        Nutzt LLM für qualitative Bewertungen.
        """
        epic_data = data_provider.issue_details.get(epic_id, {})
        epic_description = epic_data.get('description', 'No description provided.')
        epic_title = epic_data.get('title', 'No title provided.')
        epic_business_value = epic_data.get('business_value', {})
        epic_target_start = epic_data.get('target_start')
        epic_target_end = epic_data.get('target_end')
        epic_acceptance_criteria = epic_data.get('acceptance_criteria', [])


        maturity_results = {
            "description_quality": {"rating": "N/A", "justification": ""},
            "business_value_precision": {"rating": "N/A", "justification": ""},
            "hierarchy_structure": {"status": "N/A", "details": []},
            "target_dates_and_versions": {"status": "N/A", "details": []}
        }

        # 1. Business Epic Description & Goals Quality (LLM Re-assessment)
        desc_prompt_template = load_prompt_template('maturity_assessment_prompt.yaml', 'phase2_epic_description_prompt')
        desc_prompt = desc_prompt_template.format(
            epic_title=epic_title,
            epic_description=epic_description,
            acceptance_criteria=json.dumps(epic_acceptance_criteria)
        )
        try:
            llm_response_desc = self.azure_client.completion(
                model_name=LLM_MODEL_MATURITY_ASSESSMENT,
                user_prompt=desc_prompt,
                response_format={"type": "json_object"}
            )
            parsed_response_desc = json.loads(llm_response_desc["text"])
            maturity_results["description_quality"] = {
                "rating": parsed_response_desc.get("rating", "N/A"),
                "justification": parsed_response_desc.get("justification", "")
            }
            if self.token_tracker and "usage" in llm_response_desc:
                self.token_tracker.log_usage(
                    model=LLM_MODEL_MATURITY_ASSESSMENT,
                    input_tokens=llm_response_desc["usage"].prompt_tokens,
                    output_tokens=llm_response_desc["usage"].completion_tokens,
                    total_tokens=llm_response_desc["usage"].total_tokens,
                    task_name=f"phase2_epic_description_assessment_epic_{epic_id}"
                )
        except Exception as e:
            logger.error(f"LLM call failed for Phase 2 epic description assessment of {epic_id}: {e}")
            maturity_results["description_quality"]["justification"] = f"LLM assessment failed: {e}"


        # 2. Business Value Precision (LLM Re-assessment)
        bv_prompt_template = load_prompt_template('maturity_assessment_prompt.yaml', 'phase2_business_value_prompt')
        bv_prompt = bv_prompt_template.format(
            business_value_json=json.dumps(epic_business_value)
        )
        try:
            llm_response_bv = self.azure_client.completion(
                model_name=LLM_MODEL_MATURITY_ASSESSMENT,
                user_prompt=bv_prompt,
                response_format={"type": "json_object"}
            )
            parsed_response_bv = json.loads(llm_response_bv["text"])
            maturity_results["business_value_precision"] = {
                "rating": parsed_response_bv.get("rating", "N/A"),
                "justification": parsed_response_bv.get("justification", "")
            }
            if self.token_tracker and "usage" in llm_response_bv:
                self.token_tracker.log_usage(
                    model=LLM_MODEL_MATURITY_ASSESSMENT,
                    input_tokens=llm_response_bv["usage"].prompt_tokens,
                    output_tokens=llm_response_bv["usage"].completion_tokens,
                    total_tokens=llm_response_bv["usage"].total_tokens,
                    task_name=f"phase2_business_value_assessment_epic_{epic_id}"
                )
        except Exception as e:
            logger.error(f"LLM call failed for Phase 2 business value assessment of {epic_id}: {e}")
            maturity_results["business_value_precision"]["justification"] = f"LLM assessment failed: {e}"


        # 3. Hierarchy Structure and Clarity of Children (Direct + LLM)
        hierarchy_details = []
        has_portfolio_epic_or_initiative = False
        has_epics_underneath = False

        for node in data_provider.issue_tree.nodes():
            if node == epic_id: continue # Skip the epic itself

            path = nx.shortest_path(data_provider.issue_tree, source=epic_id, target=node)
            if len(path) <= 1: continue # Only interested in descendants

            node_data = data_provider.issue_details.get(node)
            if not node_data: continue

            issue_type = node_data.get('type')
            if issue_type in ["Portfolio Epic", "Initiative", "Epic"]:
                has_portfolio_epic_or_initiative = True # At least one relevant child type found
                if issue_type == "Epic":
                    has_epics_underneath = True

                child_description = node_data.get('description', 'No description provided.')
                child_title = node_data.get('title', 'No title provided.')
                child_acceptance_criteria = node_data.get('acceptance_criteria', [])

                child_prompt_template = load_prompt_template('maturity_assessment_prompt.yaml', 'phase2_child_issue_prompt')
                child_prompt = child_prompt_template.format(
                    issue_key=node,
                    issue_type=issue_type,
                    issue_title=child_title,
                    issue_description=child_description,
                    acceptance_criteria=json.dumps(child_acceptance_criteria)
                )
                try:
                    llm_response_child = self.azure_client.completion(
                        model_name=LLM_MODEL_MATURITY_ASSESSMENT,
                        user_prompt=child_prompt,
                        response_format={"type": "json_object"}
                    )
                    parsed_response_child = json.loads(llm_response_child["text"])
                    hierarchy_details.append({
                        "key": node,
                        "type": issue_type,
                        "title": child_title,
                        "clarity_rating": parsed_response_child.get("rating", "N/A"),
                        "clarity_justification": parsed_response_child.get("justification", "")
                    })
                    if self.token_tracker and "usage" in llm_response_child:
                        self.token_tracker.log_usage(
                            model=LLM_MODEL_MATURITY_ASSESSMENT,
                            input_tokens=llm_response_child["usage"].prompt_tokens,
                            output_tokens=llm_response_child["usage"].completion_tokens,
                            total_tokens=llm_response_child["usage"].total_tokens,
                            task_name=f"phase2_child_clarity_assessment_{node}"
                        )
                except Exception as e:
                    logger.error(f"LLM call failed for Phase 2 child issue {node} clarity assessment: {e}")
                    hierarchy_details.append({
                        "key": node,
                        "type": issue_type,
                        "title": child_title,
                        "clarity_rating": "Error",
                        "clarity_justification": f"LLM assessment failed: {e}"
                    })

        if has_portfolio_epic_or_initiative:
            maturity_results["hierarchy_structure"]["status"] = "Found relevant hierarchy"
        else:
            maturity_results["hierarchy_structure"]["status"] = "No relevant hierarchy (Portfolio Epic, Initiative, Epic) found."
        maturity_results["hierarchy_structure"]["details"] = hierarchy_details

        # 4. Target Dates and Fix Versions (Direct Data Check)
        target_dates_and_versions_details = []

        # Epic's own dates
        epic_dates_status = "Missing"
        if epic_target_start and epic_target_end:
            epic_dates_status = "Present"
        target_dates_and_versions_details.append({
            "issue": epic_id,
            "type": "Business Epic",
            "target_start": epic_target_start,
            "target_end": epic_target_end,
            "fix_versions": epic_data.get('fix_versions', []),
            "dates_status": epic_dates_status
        })

        # Children's dates and versions
        for node in data_provider.issue_tree.nodes():
            if node == epic_id: continue

            node_data = data_provider.issue_details.get(node)
            if not node_data: continue

            issue_type = node_data.get('type')
            if issue_type in ["Portfolio Epic", "Initiative", "Epic"]:
                node_target_start = node_data.get('target_start')
                node_target_end = node_data.get('target_end')
                node_fix_versions = node_data.get('fix_versions', [])

                dates_status = "Missing"
                if issue_type in ["Portfolio Epic", "Initiative"]:
                    if node_target_start and node_target_end:
                        dates_status = "Present"
                elif issue_type == "Epic": # For Epics, focus on fix_versions
                    if node_fix_versions:
                        dates_status = "Present"
                    else:
                        dates_status = "Missing Fix Version"

                target_dates_and_versions_details.append({
                    "issue": node,
                    "type": issue_type,
                    "target_start": node_target_start,
                    "target_end": node_target_end,
                    "fix_versions": node_fix_versions,
                    "dates_status": dates_status
                })

        maturity_results["target_dates_and_versions"]["status"] = "Analyzed"
        maturity_results["target_dates_and_versions"]["details"] = target_dates_and_versions_details

        return maturity_results


    def analyze(self, data_provider: ProjectDataProvider) -> Dict[str, Any]:
        """
        Führt die Reifegradanalyse für ein gegebenes Business Epic durch.
        """
        epic_id = data_provider.epic_id
        logger.info(f"Starting Maturity Analysis for Epic: {epic_id}")

        if not data_provider.is_valid():
            logger.error(f"ProjectDataProvider for {epic_id} is not valid. Cannot perform maturity analysis.")
            return {"error": "Invalid ProjectDataProvider"}

        # Get the current phase of the Business Epic
        current_epic_phase = self._get_current_epic_phase(data_provider.all_activities, epic_id)
        logger.info(f"Epic {epic_id} is currently in Phase: {current_epic_phase}")

        analysis_results = {
            "epic_id": epic_id,
            "current_phase": current_epic_phase,
            "maturity_assessment": {}
        }

        # Based on the phase, perform the specific maturity assessment
        if current_epic_phase == "Funnel":
            analysis_results["maturity_assessment"] = self._assess_phase_1_maturity(data_provider, epic_id)
        elif current_epic_phase == "Exploration":
            analysis_results["maturity_assessment"] = self._assess_phase_2_maturity(data_provider, epic_id)
        elif current_epic_phase in ["Implementation & Deployment", "Completed"]:
            analysis_results["maturity_assessment"]["message"] = f"Maturity assessment for '{current_epic_phase}' phase is not yet defined."
        else:
            analysis_results["maturity_assessment"]["message"] = "Could not determine phase or phase not supported for maturity assessment."

        logger.info(f"Maturity Analysis for Epic {epic_id} completed.")
        return analysis_results
