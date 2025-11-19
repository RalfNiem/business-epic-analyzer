"""
Module for tracking, analyzing, and reporting token usage in LLM API calls.

This module provides functionality to log, persist, and analyze token consumption
across different language models and time periods. It supports cost calculation based
on model-specific pricing structures and generates comprehensive usage reports.

The main class, TokenUsage, implements methods for logging API calls, calculating costs,
filtering usage data by various criteria, and generating summaries and reports in
multiple formats. It maintains up-to-date pricing information for various LLM models.

Key features:
- Token usage logging for API calls to different language models
- Cost calculation based on current pricing structures
- Temporal and model-based filtering of usage data
- Statistical analysis and aggregation of usage data
- Comprehensive report generation in multiple formats (text, JSON, HTML)
- Support for data export in CSV, JSON, and Excel formats
"""

import json
import os
import datetime
import argparse
from typing import Dict, List, Optional, Tuple, Union
import pandas as pd
from pathlib import Path

# ***** KORRIGIERTE LOGIK *****
# Dieser try-except-Block behandelt Importe robust, unabhängig davon, wie das Skript ausgeführt wird.
# - 'try': Funktioniert, wenn diese Datei als Modul importiert wird (z. B. von main_scraper.py).
# - 'except': Dient als Fallback, wenn das Skript direkt ausgeführt wird und die übergeordneten Pakete nicht kennt.
#   In diesem Fall wird der 'src'-Ordner zum Python-Pfad hinzugefügt, damit die 'utils'-Importe aufgelöst werden können.
try:
    from utils.logger_config import logger
    from utils.config import LOGS_DIR, TOKEN_LOG_FILE

except ModuleNotFoundError:
    import sys
    # Füge das übergeordnete Verzeichnis ('src') zum Suchpfad hinzu.
    src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    from utils.logger_config import logger
    from utils.config import LOGS_DIR, TOKEN_LOG_FILE

class TokenUsage:
    """
    Class for managing and analyzing token usage in LLM API calls.

    This class provides comprehensive functionality for logging, persisting, and analyzing
    token consumption across different language models and time periods. It maintains
    current pricing structures for various models and supports detailed reporting.

    Key features:
    - Token usage logging with metadata (model, task, entity identifiers)
    - Cost calculation based on up-to-date model pricing
    - Usage data filtering by time periods, tasks, entities, or models
    - Statistical analysis with customizable grouping and aggregation
    - Report generation in multiple formats (text, JSON, HTML)
    - Data export capabilities for further analysis

    The class uses a JSONL file format for storage, enabling continuous logging and
    easy retrieval of historical data. Each log entry captures timestamp, model, token
    counts, calculated costs, and optional metadata.
    """

    # Preisstruktur für verschiedene Modelle (in USD pro 1000 Tokens)
    # Stand: Mai 2025 - diese sollten regelmäßig aktualisiert werden
    MODEL_PRICING = {
        "gpt-5-mini": {"input": 0.00025, "output": 0.002},
        "gpt-5-chat-latest": {"input": 0.00125, "output": 0.010},
        "gpt-4.1": {"input": 0.002, "output": 0.008},
        "gpt-4.1-mini": {"input": 0.0004, "output": 0.0016},
        "gpt-4o": {"input": 0.0025, "output": 0.01},
        "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
        "o3": {"input": 0.002, "output": 0.008},
        "o3-pro": {"input": 0.02, "output": 0.08},
        "o4-mini": {"input": 0.0011, "output": 0.0044},
        "o3-mini": {"input": 0.0011, "output": 0.0044},

        "claude-3-5-haiku": {"input": 0.0008, "output": 0.004},
        "claude-3-7-sonnet-latest": {"input": 0.003, "output": 0.015},
        "claude-sonnet-4-20250514": {"input": 0.003, "output": 0.015},
        "claude-opus-4-20250514": {"input": 0.015, "output": 0.075},

        "eu.anthropic.claude-sonnet-4-20250514-v1:0": {"input": 0.003, "output": 0.015},
        "eu.anthropic.claude-3-7-sonnet-20250219-v1:0": {"input": 0.003, "output": 0.015},

        "gemini/gemini-2.5-pro": {"input": 0.00125, "output": 0.01},
        "gemini/gemini-2.5-flash": {"input": 0.00030, "output": 0.0025},
        "gemini/gemini-2.0-flash": {"input": 0.0001, "output": 0.0004},

        "Mistral-Small-3.2-24B-Instruct-2506": {"input": 0.0, "output": 0.0},
        "DeepSeek-R1-0528": {"input": 0.0, "output": 0.0},
        "gpt-oss-120b": {"input": 0.0, "output": 0.0},
        # Weitere Modelle hier hinzufügen
    }

    def __init__(self, log_file_path: str = None):
        """
        Initialisiert die TokenUsage-Klasse.

        Args:
            log_file_path: Pfad zur Log-Datei. Falls None, wird ein Standardpfad verwendet.
        """
        if log_file_path is None:
            base_dir = Path(LOGS_DIR)
            base_dir.mkdir(exist_ok=True)

            # Verwende das aktuelle Datum im Dateinamen
            date_str = datetime.datetime.now().strftime("%Y-%m-%d")
            self.log_file_path = base_dir / f"token_usage_{date_str}.jsonl"
        else:
            self.log_file_path = Path(log_file_path)
            # Stelle sicher, dass das Verzeichnis existiert
            self.log_file_path.parent.mkdir(exist_ok=True, parents=True)

    def log_usage(self,
                 model: str,
                 input_tokens: int,
                 output_tokens: int,
                 total_tokens: int,
                 task_name: str = None,
                 entity_id: str = None,
                 metadata: Dict = None) -> Dict:
        """
        Protokolliert einen Token-Verbrauch in der Log-Datei.

        Args:
            model: Name des verwendeten LLM-Modells
            input_tokens: Anzahl der Input-Tokens
            output_tokens: Anzahl der Output-Tokens
            total_tokens: Gesamtanzahl der Tokens
            task_name: Optionaler Name der Aufgabe (z.B. "html_generation")
            entity_id: Optionale ID der Entität (z.B. "BEMABU-1844")
            metadata: Optionale zusätzliche Metadaten

        Returns:
            Das geloggte Nutzungsobjekt mit Zeitstempel
        """
        # Erstelle einen Nutzungseintrag mit Zeitstempel
        timestamp = datetime.datetime.now().isoformat()

        # Überprüfe, ob reasoning tokens in den total_tokens enthalten sind
        # falls ja, addiere die reasoning tokens zu den output_tokens
        if (input_tokens+output_tokens) != total_tokens:
            output_tokens = total_tokens - input_tokens

        # Berechne die Kosten, falls das Modell in der Preisstruktur vorhanden ist
        cost = self._calculate_cost(model, input_tokens, output_tokens)

        usage_entry = {
            "timestamp": timestamp,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cost_usd": cost
        }

        # Füge optionale Felder hinzu, wenn sie vorhanden sind
        if task_name:
            usage_entry["task_name"] = task_name
        if entity_id:
            usage_entry["entity_id"] = entity_id
        if metadata:
            usage_entry["metadata"] = metadata

        # Schreibe den Eintrag in die Log-Datei (im JSONL-Format: eine JSON-Zeile pro Eintrag)
        with open(self.log_file_path, "a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(usage_entry) + "\n")

        return usage_entry

    def _calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """
        Berechnet die Kosten für einen API-Aufruf basierend auf dem Modell und der Tokenanzahl.

        Args:
            model: Name des verwendeten LLM-Modells
            input_tokens: Anzahl der Input-Tokens
            output_tokens: Anzahl der Output-Tokens

        Returns:
            Berechnete Kosten in USD
        """
        # Überprüfe, ob das Modell in der Preisstruktur vorhanden ist
        if model in self.MODEL_PRICING:
            pricing = self.MODEL_PRICING[model]
            # Berechne die Kosten (pro 1000 Tokens)
            input_cost = (input_tokens / 1000) * pricing["input"]
            output_cost = (output_tokens / 1000) * pricing["output"]
            return round(input_cost + output_cost, 6)  # Runde auf 6 Nachkommastellen
        else:
            # Wenn das Modell nicht bekannt ist, gib None zurück oder ein Standard-Preismodell
            print(f"Warnung: Keine Preisinformation für Modell '{model}' gefunden")
            return 0.0

    def get_usage_data(self) -> pd.DataFrame:
        """
        Lädt alle Token-Nutzungsdaten aus der Log-Datei in ein Pandas DataFrame.

        Returns:
            DataFrame mit allen Token-Nutzungsdaten
        """
        if not os.path.exists(self.log_file_path):
            return pd.DataFrame()

        # Lese JSONL-Datei Zeile für Zeile
        records = []
        with open(self.log_file_path, "r", encoding="utf-8") as log_file:
            for line in log_file:
                if line.strip():  # Überspringe leere Zeilen
                    records.append(json.loads(line))

        # Konvertiere in DataFrame
        df = pd.DataFrame(records)

        # Konvertiere Zeitstempel in datetime-Objekte
        if not df.empty and "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])

        return df

    def get_usage_in_timeframe(self,
                              start_time: Union[str, datetime.datetime] = None,
                              end_time: Union[str, datetime.datetime] = None,
                              task_name: str = None,
                              entity_id: str = None,
                              model: str = None) -> pd.DataFrame:
        """
        Filtert Token-Nutzungsdaten nach einem bestimmten Zeitraum und optionalen Kriterien.

        Args:
            start_time: Startzeit für die Filterung (inklusiv)
            end_time: Endzeit für die Filterung (exklusiv)
            task_name: Optionale Filterung nach Task-Name
            entity_id: Optionale Filterung nach Entity-ID
            model: Optionale Filterung nach Modell

        Returns:
            DataFrame mit den gefilterten Token-Nutzungsdaten
        """
        # Lade alle Daten
        df = self.get_usage_data()

        if df.empty:
            return df

        # Filtere nach Zeitraum, wenn angegeben
        if start_time is not None:
            if isinstance(start_time, str):
                start_time = pd.to_datetime(start_time)
            df = df[df["timestamp"] >= start_time]

        if end_time is not None:
            if isinstance(end_time, str):
                end_time = pd.to_datetime(end_time)
            df = df[df["timestamp"] < end_time]

        # Filtere nach Task-Name, wenn angegeben
        if task_name is not None and "task_name" in df.columns:
            df = df[df["task_name"] == task_name]

        # Filtere nach Entity-ID, wenn angegeben
        if entity_id is not None and "entity_id" in df.columns:
            df = df[df["entity_id"] == entity_id]

        # Filtere nach Modell, wenn angegeben
        if model is not None:
            df = df[df["model"] == model]

        return df

    def get_usage_summary(self,
                         start_time: Union[str, datetime.datetime] = None,
                         end_time: Union[str, datetime.datetime] = None,
                         group_by: List[str] = None) -> pd.DataFrame:
        """
        Erstellt eine Zusammenfassung der Token-Nutzung, optional gruppiert nach bestimmten Feldern.

        Args:
            start_time: Startzeit für die Filterung
            end_time: Endzeit für die Filterung
            group_by: Liste von Feldern, nach denen gruppiert werden soll (z.B. ["model", "task_name"])

        Returns:
            DataFrame mit der Zusammenfassung
        """
        # Hole die gefilterten Daten
        df = self.get_usage_in_timeframe(start_time, end_time)

        if df.empty:
            return pd.DataFrame()

        # Standardmäßig keine Gruppierung
        if group_by is None:
            summary = pd.DataFrame({
                "total_calls": [len(df)],
                "total_input_tokens": [df["input_tokens"].sum()],
                "total_output_tokens": [df["output_tokens"].sum()],
                "total_tokens": [df["total_tokens"].sum()],
                "total_cost_usd": [df["cost_usd"].sum() if "cost_usd" in df.columns else 0]
            })
            return summary

        # Mit Gruppierung
        grouped = df.groupby(group_by).agg({
            "input_tokens": "sum",
            "output_tokens": "sum",
            "total_tokens": "sum",
            "cost_usd": "sum" if "cost_usd" in df.columns else None,
            "model": "count"  # Anzahl der API-Aufrufe
        }).rename(columns={"model": "calls"})

        return grouped

    def get_cost_summary(self,
                        start_time: Union[str, datetime.datetime] = None,
                        end_time: Union[str, datetime.datetime] = None,
                        group_by: List[str] = None) -> Dict:
        """
        Erstellt eine Kostenzusammenfassung für den angegebenen Zeitraum.

        Args:
            start_time: Startzeit für die Filterung
            end_time: Endzeit für die Filterung
            group_by: Liste von Feldern, nach denen gruppiert werden soll

        Returns:
            Dictionary mit Kostenzusammenfassung
        """
        # Hole die gefilterten und gruppierten Daten
        df = self.get_usage_in_timeframe(start_time, end_time)

        if df.empty:
            return {"total_cost_usd": 0.0, "details": {}}

        # Berechne die Gesamtkosten
        total_cost = df["cost_usd"].sum() if "cost_usd" in df.columns else 0

        # Gruppiere nach Modell für detaillierte Kostenaufschlüsselung
        if group_by is None:
            group_by = ["model"]

        grouped = self.get_usage_summary(start_time, end_time, group_by)

        if "cost_usd" in grouped.columns:
            # Erstelle ein geschachteltes Dictionary aus dem gruppierten DataFrame
            if isinstance(grouped.index, pd.MultiIndex):
                details = {tuple(idx): {"cost_usd": row["cost_usd"]}
                        for idx, row in grouped.iterrows()}
            else:
                details = {idx: {"cost_usd": row["cost_usd"]}
                        for idx, row in grouped.iterrows()}
        else:
            details = {}

        return {
            "total_cost_usd": total_cost,
            "details": details
        }

    def generate_report(self,
                      start_time: Union[str, datetime.datetime] = None,
                      end_time: Union[str, datetime.datetime] = None,
                      output_format: str = "text",
                      output_file: str = None) -> str:
        """
        Generiert einen Bericht über die Token-Nutzung und Kosten.

        Args:
            start_time: Startzeit für die Filterung
            end_time: Endzeit für die Filterung
            output_format: Format des Berichts ("text", "json", "html")
            output_file: Pfad für die Ausgabedatei (wenn None, wird nur der Bericht zurückgegeben)

        Returns:
            Der generierte Bericht als String
        """
        # Hole die Nutzungsdaten
        df = self.get_usage_in_timeframe(start_time, end_time)

        if df.empty:
            report = "Keine Nutzungsdaten im angegebenen Zeitraum gefunden."
            if output_file:
                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(report)
            return report

        # Zusammenfassungen erstellen
        overall_summary = self.get_usage_summary(start_time, end_time)
        model_summary = self.get_usage_summary(start_time, end_time, ["model"])
        task_summary = None
        if "task_name" in df.columns:
            task_summary = self.get_usage_summary(start_time, end_time, ["task_name"])

        # Berichtszeitraum
        if start_time is None:
            start_time = df["timestamp"].min()
        if end_time is None:
            end_time = df["timestamp"].max()

        if isinstance(start_time, str):
            start_time = pd.to_datetime(start_time)
        if isinstance(end_time, str):
            end_time = pd.to_datetime(end_time)

        # Formatiere den Bericht basierend auf dem gewünschten Format
        if output_format == "json":
            report_data = {
                "report_period": {
                    "start": start_time.isoformat(),
                    "end": end_time.isoformat()
                },
                "overall_summary": overall_summary.to_dict("records")[0],
                "by_model": model_summary.reset_index().to_dict("records")
            }

            if task_summary is not None:
                report_data["by_task"] = task_summary.reset_index().to_dict("records")

            report = json.dumps(report_data, indent=2)

        elif output_format == "html":
            # HTML-Bericht erstellen
            html_parts = []
            html_parts.append("<!DOCTYPE html>")
            html_parts.append("<html><head><title>Token Usage Report</title>")
            html_parts.append("<style>")
            html_parts.append("body { font-family: Arial, sans-serif; margin: 20px; }")
            html_parts.append("table { border-collapse: collapse; width: 100%; margin-bottom: 20px; }")
            html_parts.append("th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }")
            html_parts.append("th { background-color: #f2f2f2; }")
            html_parts.append("h1, h2 { color: #333; }")
            html_parts.append("</style></head><body>")

            html_parts.append("<h1>Token Usage Report</h1>")
            html_parts.append(f"<p>Period: {start_time.strftime('%Y-%m-%d %H:%M:%S')} to {end_time.strftime('%Y-%m-%d %H:%M:%S')}</p>")

            html_parts.append("<h2>Overall Summary</h2>")
            html_parts.append(overall_summary.to_html())

            html_parts.append("<h2>By Model</h2>")
            html_parts.append(model_summary.to_html())

            if task_summary is not None:
                html_parts.append("<h2>By Task</h2>")
                html_parts.append(task_summary.to_html())

            html_parts.append("</body></html>")
            report = "\n".join(html_parts)

        else:  # text format
            report_parts = []
            report_parts.append("=== Token Usage Report ===")
            report_parts.append(f"Period: {start_time.strftime('%Y-%m-%d %H:%M:%S')} to {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
            report_parts.append("")

            report_parts.append("--- Overall Summary ---")
            report_parts.append(f"Total API Calls: {overall_summary['total_calls'].values[0]}")
            report_parts.append(f"Total Input Tokens: {overall_summary['total_input_tokens'].values[0]:,}")
            report_parts.append(f"Total Output Tokens: {overall_summary['total_output_tokens'].values[0]:,}")
            report_parts.append(f"Total Tokens: {overall_summary['total_tokens'].values[0]:,}")
            report_parts.append(f"Total Cost (USD): ${overall_summary['total_cost_usd'].values[0]:.2f}")
            report_parts.append("")

            report_parts.append("--- By Model ---")
            for idx, row in model_summary.iterrows():
                model_name = idx
                report_parts.append(f"Model: {model_name}")
                report_parts.append(f"  Calls: {row['calls']:,}")
                report_parts.append(f"  Input Tokens: {row['input_tokens']:,}")
                report_parts.append(f"  Output Tokens: {row['output_tokens']:,}")
                report_parts.append(f"  Total Tokens: {row['total_tokens']:,}")
                if "cost_usd" in row:
                    report_parts.append(f"  Cost (USD): ${row['cost_usd']:.2f}")
                report_parts.append("")

            if task_summary is not None and not task_summary.empty:
                report_parts.append("--- By Task ---")
                for idx, row in task_summary.iterrows():
                    task = idx
                    report_parts.append(f"Task: {task}")
                    report_parts.append(f"  Calls: {row['calls']:,}")
                    report_parts.append(f"  Input Tokens: {row['input_tokens']:,}")
                    report_parts.append(f"  Output Tokens: {row['output_tokens']:,}")
                    report_parts.append(f"  Total Tokens: {row['total_tokens']:,}")
                    if "cost_usd" in row:
                        report_parts.append(f"  Cost (USD): ${row['cost_usd']:.2f}")
                    report_parts.append("")

            report = "\n".join(report_parts)

        # Speichere den Bericht, wenn ein Ausgabepfad angegeben wurde
        if output_file:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(report)

        return report


    def export_usage_data(self, output_file: str, format: str = "csv") -> bool:
        """
        Exportiert alle Nutzungsdaten in eine Datei.

        Args:
            output_file: Pfad zur Ausgabedatei
            format: Format der Ausgabe ("csv", "json", "excel")

        Returns:
            True bei Erfolg, False bei Fehler
        """
        df = self.get_usage_data()

        if df.empty:
            print("Keine Daten zum Exportieren vorhanden.")
            return False

        try:
            if format.lower() == "csv":
                df.to_csv(output_file, index=False)
            elif format.lower() == "json":
                df.to_json(output_file, orient="records", indent=2)
            elif format.lower() == "excel":
                df.to_excel(output_file, index=False)
            else:
                raise ValueError(f"Nicht unterstütztes Format: {format}")

            print(f"Daten erfolgreich nach {output_file} exportiert.")
            return True

        except Exception as e:
            logger.error(f"Fehler beim Exportieren der Daten: {e}")
            return False


# Beispielverwendung
if __name__ == "__main__":

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Generate token usage reports for different time periods.')
    parser.add_argument('--time', choices=['day', 'week', 'month', 'year'], default = 'day',
                        help='Time period for the report: day, week, month, or year')
    args = parser.parse_args()

    # Initialisiere TokenUsage
    token_tracker = TokenUsage(log_file_path=TOKEN_LOG_FILE)

    # Aktuelles Datum
    now = datetime.datetime.now()

    # Zeitraum basierend auf dem Argument anpassen
    if args.time == 'day':
        # Aktueller Tag
        start_time = now.strftime("%Y-%m-%d") + "T00:00:00"
        end_time = now.strftime("%Y-%m-%d") + "T23:59:59"
        time_desc = "des aktuellen Tages"
    elif args.time == 'week':
        # Berechne Wochenbeginn (Montag)
        start_of_week = now - datetime.timedelta(days=now.weekday())
        start_time = start_of_week.strftime("%Y-%m-%d") + "T00:00:00"
        end_time = now.strftime("%Y-%m-%d") + "T23:59:59"
        time_desc = "der aktuellen Woche"
    elif args.time == 'month':
        # Beginn des aktuellen Monats
        start_time = now.strftime("%Y-%m") + "-01T00:00:00"
        end_time = now.strftime("%Y-%m-%d") + "T23:59:59"
        time_desc = "des aktuellen Monats"
    elif args.time == 'year':
        # Beginn des aktuellen Jahres
        start_time = now.strftime("%Y") + "-01-01T00:00:00"
        end_time = now.strftime("%Y-%m-%d") + "T23:59:59"
        time_desc = "des aktuellen Jahres"

    # Bericht für den aktuellen Tag generieren
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    output_format="txt"
    file_ext = "txt"  # Dateiendung
    output_file = os.path.join(LOGS_DIR, f"token_report_{args.time}.{file_ext}")
    report = token_tracker.generate_report(
        start_time=start_time,
        end_time=end_time,
        output_format=output_format,
        output_file=output_file
    )

    print(f"\nToken Usage Bericht {time_desc} wurde für den Zeitraum {start_time[:10]} bis {end_time[:10]} generiert\n")
