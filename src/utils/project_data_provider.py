# src/utils/project_data_provider.py
import json
import os
import sys
import sqlite3
from typing import List, Dict

from utils.logger_config import logger
from utils.jira_tree_classes import JiraTreeGenerator
from utils.config import JIRA_ISSUES_DIR, JSON_SUMMARY_DIR, DB_PATH

class ProjectDataProvider:
    """
    Zentrale Komponente zur Datenbereitstellung für die Analyse-Pipelines.

    Diese Klasse fungiert als effizienter 'Data Hub'. Sie abstrahiert die Datenquelle
    (SQLite-Datenbank oder JSON-Dateisystem) und stellt strukturierte, vorverarbeitete
    Daten für Downstream-Analysen (z.B. Scope, Zeit, Status) bereit.

    Funktionsweise:
    ---------------
    1.  **Quellenerkennung:** Prüft beim Start, ob eine SQLite-Datenbank (`DB_PATH`)
        verfügbar ist.
    2.  **Hybrid-Loading:**
        * **Modus A (Datenbank - Bevorzugt):** Lädt den Issue-Baum und alle
            Attributdaten via effizienter Batch-SQL-Abfragen. Dies ist deutlich
            schneller als das Einlesen einzelner Dateien.
        * **Modus B (Dateisystem - Fallback):** Falls die DB fehlt oder defekt ist,
            greift die Klasse auf das JSON-Verzeichnis (`JIRA_ISSUES_DIR`) zurück
            und lädt Dateien iterativ.
    3.  **Datenaufbereitung:** Erstellt einen gerichteten Graphen (`issue_tree`),
        eine chronologisch sortierte Liste aller Aktivitäten (`all_activities`)
        und einen Schnellzugriffs-Cache für Issue-Details (`issue_details`).

    Args:
        epic_id (str): Der Jira-Key des Root-Elements (z.B. "JRA-123"), für das
            der Datenraum geladen werden soll.
        hierarchy_config (dict, optional): Konfiguration für den `JiraTreeGenerator`,
            die bestimmt, welche Issue-Typen und Link-Typen im Baum enthalten sein
            sollen (z.B. `JIRA_TREE_FULL`).
"""
    def __init__(self, epic_id: str, hierarchy_config: dict = None):
        self.epic_id = epic_id
        self.db_conn = None
        self.use_db = False
        self.json_dir = JIRA_ISSUES_DIR

        # --- DB-Verbindungsversuch ---
        if os.path.exists(DB_PATH):
            try:
                # Teste die Verbindung und ob die Tabelle existiert
                conn = sqlite3.connect(DB_PATH, check_same_thread=False)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='issues';")
                if cursor.fetchone():
                    self.db_conn = conn
                    self.use_db = True
                    logger.info("SQLite-Datenbank gefunden. Nutze DB-Modus.")
                else:
                    logger.warning(f"SQLite-Datei {DB_PATH} gefunden, aber Tabelle 'issues' fehlt. Nutze Fallback (JSON-Dateien).")
                    conn.close()
            except sqlite3.Error as e:
                logger.error(f"Fehler beim Öffnen der SQLite-DB {DB_PATH}: {e}. Nutze Fallback (JSON-Dateien).")
        else:
            logger.warning(f"Keine SQLite-Datenbank unter {DB_PATH} gefunden. Nutze Fallback (JSON-Dateien).")
        # --- Ende DB-Verbindungsversuch ---

        # 2. TreeGenerator initialisieren (entweder mit DB-Verbindung oder mit Fallback-Pfad)
        if self.use_db:
            self.tree_generator = JiraTreeGenerator(
                allowed_types=hierarchy_config,
                db_conn=self.db_conn
            )
        else:
            self.tree_generator = JiraTreeGenerator(
                json_dir=self.json_dir, # Fallback auf das OneDrive-Verzeichnis
                allowed_types=hierarchy_config,
                db_conn=None
            )

        # 3. Baum erstellen (liest jetzt entweder aus DB oder Dateien)
        self.issue_tree = self.tree_generator.build_issue_tree(self.epic_id, include_rejected=False)

        # 4. Aktivitäten und Details aus dem Baum laden (effizient)
        self.all_activities = []
        self.issue_details = {}

        if self.is_valid():
            # --- DATENLADE-LOGIK MIT FALLBACK ---
            if self.use_db:
                self._load_data_from_db() # Neue, schnelle DB-Methode
                logger.info(f"ProjectDataProvider für Epic '{epic_id}' mit {len(self.issue_tree.nodes())} Issues initialisiert (via SQLite).")
            else:
                # Alte Datei-Methoden als Fallback nutzen
                self.all_activities = self._gather_all_activities()
                self.issue_details = self._build_issue_details_cache()
                logger.info(f"ProjectDataProvider für Epic '{epic_id}' mit {len(self.issue_tree.nodes())} Issues initialisiert (via JSON-Dateien).")
            # --- ENDE DATENLADE-LOGIK ---

            self.all_activities.sort(key=lambda x: x.get('zeitstempel_iso', ''))
        else:
            logger.warning(f"ProjectDataProvider für Epic '{epic_id}' konnte keinen gültigen Issue-Baum erstellen.")
            self._close_db_connection() # Auch im Fehlerfall DB schließen


    def _load_data_from_db(self):
        """
        Lädt alle Details und Aktivitäten für die Issues im Baum
        in einer einzigen, effizienten Batch-Operation aus der DB.
        """
        if not self.issue_tree or not self.db_conn:
            return

        node_keys = list(self.issue_tree.nodes())

        try:
            cursor = self.db_conn.cursor()
            placeholders = ",".join("?" * len(node_keys))
            query = f"SELECT key, data FROM issues WHERE key IN ({placeholders})"
            cursor.execute(query, node_keys)
            rows = cursor.fetchall()

            # Verbindung schließen, sobald Daten geholt wurden
            self._close_db_connection()

            for key, data_str in rows:
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    logger.warning(f"Konnte JSON-Daten für Key {key} aus DB nicht parsen.")
                    continue

                # --- Logik aus altem _gather_all_activities ---
                activities = data.get('activities', [])
                for activity in activities:
                    activity['issue_key'] = key
                self.all_activities.extend(activities)

                # --- Logik aus altem _build_issue_details_cache ---
                points = 0
                story_points_value = data.get('story_points')
                if story_points_value is not None:
                    try: points = int(story_points_value)
                    except (ValueError, TypeError): points = 0

                self.issue_details[key] = {
                    'type': data.get('issue_type'),
                    'title': data.get('title'),
                    'description': data.get('description'),
                    'acceptance_criteria': data.get('acceptance_criteria'),
                    'business_value': data.get('business_value'),
                    'status': data.get('status'),
                    'resolution': data.get('resolution'),
                    'points': points,
                    'target_start': data.get('target_start'),
                    'target_end': data.get('target_end'),
                    'fix_versions': data.get('fix_versions'),
                    'created': data.get('Created'),
                    'resolved': data.get('Resolved'),
                    'closed_date': data.get('Closed Date')
                }

        except sqlite3.Error as e:
            logger.error(f"Fehler beim Laden der Batch-Daten aus SQLite: {e}")
            self._close_db_connection()

    # +++ NEUE HILFSMETHODEN für DB-Verwaltung +++
    def _close_db_connection(self):
        """Schließt die Datenbankverbindung sicher."""
        if hasattr(self, 'db_conn') and self.db_conn:
            self.db_conn.close()
            self.db_conn = None

    def __del__(self):
        """Stellt sicher, dass die DB-Verbindung geschlossen wird, wenn das Objekt zerstört wird."""
        self._close_db_connection()

    def is_valid(self) -> bool:
        """Prüft, ob die grundlegenden Daten geladen werden konnten."""
        return self.issue_tree is not None and len(self.issue_tree.nodes()) > 0


    def _gather_all_activities(self) -> list:
        """Sammelt die Aktivitäten aller Issues im Baum."""
        all_activities = []
        if not self.issue_tree: return []
        for issue_key in self.issue_tree.nodes():
            file_path = os.path.join(self.json_dir, f"{issue_key}.json")
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    issue_data = json.load(f)
                    activities = issue_data.get('activities', [])
                    for activity in activities:
                        activity['issue_key'] = issue_key
                    all_activities.extend(activities)
            except (FileNotFoundError, json.JSONDecodeError) as e:
                logger.warning(f"Datei für Issue '{issue_key}' nicht gefunden oder fehlerhaft: {e}")
                continue
        return all_activities

    def _build_issue_details_cache(self) -> dict:
        """Erstellt einen zentralen Cache mit aufbereiteten Details zu jedem Issue."""
        cache = {}
        if not self.issue_tree: return {}
        for issue_key in self.issue_tree.nodes():
            file_path = os.path.join(self.json_dir, f"{issue_key}.json")
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    points = 0
                    story_points_value = data.get('story_points')
                    if story_points_value is not None:
                        try:
                            points = int(story_points_value)
                        except (ValueError, TypeError):
                            points = 0

                    cache[issue_key] = {
                        'type': data.get('issue_type'),
                        'title': data.get('title'),
                        'description': data.get('description'),
                        'acceptance_criteria': data.get('acceptance_criteria'),
                        'business_value': data.get('business_value'),
                        'status': data.get('status'),
                        'resolution': data.get('resolution'),
                        'points': points,
                        'target_start': data.get('target_start'),
                        'target_end': data.get('target_end'),
                        'fix_versions': data.get('fix_versions'),
                        'created': data.get('Created'),
                        'resolved': data.get('Resolved'),
                        'closed_date': data.get('Closed Date')
                    }

            except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Konnte Details für Issue '{issue_key}' nicht laden: {e}")
                continue
        return cache


    def get_epic_json_summary(self, epic_id: str) -> dict | None:
        """Loads the JSON summary for a given epic ID from the JSON_SUMMARY_DIR."""
        file_path = os.path.join(JSON_SUMMARY_DIR, f"{epic_id}_json_summary.json")
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.warning(f"JSON summary file not found for {epic_id}: {file_path}")
            return None
        except json.JSONDecodeError:
            logger.error(f"Error decoding JSON summary for {epic_id}: {file_path}")
            return None
