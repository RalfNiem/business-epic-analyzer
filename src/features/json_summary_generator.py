"""
Erzeugt eine umfassende, formatierte JSON-Zusammenfassung, die für die
Verarbeitung durch ein LLM und die finale HTML-Generierung optimiert ist.

Diese Klasse agiert als zentraler "Aggregator" nach Abschluss aller metrischen
Analysen. Ihre Hauptaufgabe ist es, die strukturierten, aber rohen Daten aus
dem `AnalysisRunner` mit der qualitativen, textbasierten Zusammenfassung,
die von einem Sprachmodell (LLM) generiert wurde, zu einem einzigen, kohärenten
JSON-Objekt zu verschmelzen.

Während dieses Prozesses werden die Daten nicht nur zusammengeführt, sondern
auch für die finale Präsentation aufbereitet. Zeitspannen werden in ein
menschenlesbares Format umgewandelt (z.B. "3 Monate, 12 Tage") und Datumsangaben
werden normiert. Das resultierende `_complete_summary.json`-Dokument dient als
alleinige Datenquelle für den `EpicHtmlGenerator`, was die Entkopplung von
Analyse und Präsentation sicherstellt.

Kernfunktionen:
-   Fusioniert metrische Analyseergebnisse (Scope, Status, etc.) mit der
    LLM-generierten inhaltlichen Zusammenfassung.
-   Formatiert Daten wie Zeitspannen und ISO-Datumsstempel in ein
    präsentationsfreundliches Format.
-   Konstruiert Dateipfade für eingebettete Artefakte wie Plots.
-   Speichert das finale, angereicherte JSON-Dokument, das als Kontext für
    die HTML-Generierung dient.
"""

import json
import os
from datetime import datetime

from src.utils.config import JSON_SUMMARY_DIR, PLOT_DIR
from src.utils.logger_config import logger
from src.utils.formatting_helpers import (
    format_timedelta_to_months_days,
    calculate_duration_string,
    format_iso_to_dd_mm_yyyy
)

class JsonSummaryGenerator:
    """
    Erzeugt eine umfassende, formatierte JSON-Zusammenfassung aus allen
    Analyseergebnissen und inhaltlichen Daten, die für die Verarbeitung
    durch ein LLM und die finale HTML-Generierung optimiert ist.
    """

    def generate_and_save_complete_summary(self, analysis_results: dict, content_summary: dict, epic_id: str) -> dict:
        """
        Fusioniert die metrischen Analyseergebnisse mit der inhaltlichen
        Zusammenfassung und speichert sie als eine einzige JSON-Datei.

        Args:
            analysis_results (dict): Die Ergebnisse aus dem AnalysisRunner.
            content_summary (dict): Die inhaltliche Zusammenfassung aus dem LLM-Aufruf.
            epic_id (str): Die ID des Business Epics.

        Returns:
            dict: Das fusionierte, vollständige Dictionary.
        """
        logger.info(f"Erstelle fusionierte JSON-Zusammenfassung für Epic {epic_id}...")

        # 1. Metrische Analyseergebnisse aufbereiten
        metric_summary = self._build_metric_summary_dict(analysis_results, epic_id)

        # 2. Inhaltliche Zusammenfassung und metrische Analyse fusionieren
        # Beginne mit der inhaltlichen Zusammenfassung und füge die metrische Analyse hinzu,
        # um die Top-Level-Struktur von content_summary zu erhalten.
        complete_data = content_summary.copy()
        complete_data.update(metric_summary) # Fügt die Schlüssel aus metric_summary hinzu

        # 3. Pfad für die Ausgabedatei definieren
        output_path = os.path.join(JSON_SUMMARY_DIR, f"{epic_id}_complete_summary.json")

        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(complete_data, f, ensure_ascii=False, indent=2)
            logger.info(f"Vollständige JSON-Zusammenfassung erfolgreich gespeichert: {output_path}")
        except Exception as e:
            logger.error(f"Fehler beim Speichern der vollständigen JSON-Zusammenfassung für {epic_id}: {e}")

        return complete_data


    def _build_metric_summary_dict(self, results: dict, epic_id: str) -> dict:
        """Stellt das Dictionary mit den metrischen Analyseergebnissen zusammen."""

        # Scope-Daten extrahieren
        scope_res = results.get('ScopeAnalyzer', {})
        scope_summary = {
            "total_issues": scope_res.get("total_issues_found", 0),
            "total_epics": scope_res.get("total_epics_found", 0),
            "total_stories": scope_res.get("total_stories_found", 0),
            "involved_projects_count": scope_res.get("project_count", 0),
            "project_issue_distribution": scope_res.get("project_distribution", {}),
            "scope_evaluation": scope_res.get("scope_evaluation", "Keine Bewertung verfügbar.")
        }

        # Status- & Laufzeit-Daten extrahieren und formatieren
        status_res = results.get('StatusAnalyzer', {})
        status_durations = status_res.get('epic_status_durations', {})
        formatted_durations = {
            status: format_timedelta_to_months_days(td)
            for status, td in status_durations.items()
        }

        start_time = status_res.get('coding_start_time')
        end_time = status_res.get('coding_end_time')

        status_summary = {
            "epic_status_durations": formatted_durations,
            "coding_start_date": format_iso_to_dd_mm_yyyy(start_time),
            "coding_end_date": format_iso_to_dd_mm_yyyy(end_time),
            "coding_duration": status_res.get("coding_duration", "Keine Angabe")
        }

        # TimeCreep-Daten extrahieren
        time_creep_res = results.get('TimeCreepAnalyzer', {})
        time_creep_summary = time_creep_res.get("llm_time_creep_summary", "Keine Zusammenfassung verfügbar.")

        # Backlog-Plot-Pfad konstruieren
        backlog_plot_path = os.path.join(PLOT_DIR, f"{epic_id}_backlog_development.png")

        # Finale Struktur der metrischen Zusammenfassung
        metric_summary = {
            "analysis_timestamp_utc": datetime.utcnow().isoformat(),
            "scope_summary": scope_summary,
            "status_and_duration_summary": status_summary,
            "time_creep_summary_llm": time_creep_summary,
            "backlog_development_plot_path": backlog_plot_path.replace("\\", "/") # für OS-Kompatibilität
        }

        return metric_summary
