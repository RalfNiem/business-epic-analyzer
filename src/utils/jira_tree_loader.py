"""
Rekursiver Jira Tree Loader (Klasse)

Diese Klasse implementiert einen robusten, parallelen und rekursiven
Mechanismus zum Herunterladen eines gesamten Abhängigkeitsbaums von Jira-Issues.

Features:
---------
* **Rekursives Laden:** Startet bei einem Root-Key und lädt rekursiv alle verknüpften
  Issues (Kinder, Links) herunter.
* **Hybrid-Modus (Full vs. Delta):**
    * `loader_mode='full'`: Lädt den Baum *immer* vollständig neu von der API.
      Dies garantiert maximale Datenfrische, ist aber langsamer.
    * `loader_mode='delta'`: Nutzt eine lokale SQLite-Datenbank als Cache.
      Prüft vor dem Laden einer Ebene effizient via Batch-JQL, ob Issues auf dem
      Server aktueller sind als in der DB. Nur veraltete oder neue Issues werden
      geladen; der Rest kommt blitzschnell aus der DB.
* **Parallelität:** Nutzt einen ThreadPoolExecutor für parallele API-Requests.
* **DB-Sync:** Speichert geladene Issues atomar (UPSERT) in der SQLite-Datenbank,
  um den Cache für zukünftige 'delta'-Läufe aktuell zu halten.
"""

import requests
import json
import os
import time
import sqlite3  # <--- HINZUGEFÜGT
from datetime import datetime, timezone  # <--- HINZUGEFÜGT
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from requests.exceptions import RequestException

# Interne Importe
import os
from utils.config import (
    JIRA_ISSUES_DIR, ISSUE_LOG_FILE,
    DB_PATH  # <--- HINZUGEFÜGT
)
from utils.logger_config import logger
from utils.jira_data_transformer import JiraDataTransformer

JIRA_SERVER = os.getenv("JIRA_SERVER_URL", "https://jira.telekom.de")
JIRA_API_TOKEN = os.getenv("JIRA_ACCESS_TOKEN")

# Konstanten
REQUEST_TIMEOUT = 60
PARENT_LINK_TYPES = ['Business Initiative', 'Business Epic', 'Portfolio Epic', 'Initiative']
MAX_CONCURRENT_REQUESTS = 1
JQL_CHUNK_SIZE = 200  # <--- HINZUGEFÜGT (Effiziente Batch-Größe)


class JiraTreeLoader:
    """
    Lädt einen vollständigen, rekursiven Jira-Baum von der API.
    Führt (im 'full' Modus) *keine* Delta-Checks durch.
    Führt (im 'delta' Modus) effiziente Batch-Delta-Checks durch.
    """

    def __init__(self, token_tracker=None, loader_mode='full'):  # <--- GEÄNDERT
        """
        Initialisiert den rekursiven Loader.

        Args:
            token_tracker (TokenUsage, optional): Ein Tracker-Objekt.
            loader_mode (str, optional): 'full' (Standard, lädt immer alles neu)
                                         oder 'delta' (nutzt DB-Cache und Freshness-Checks).
        """
        if not JIRA_API_TOKEN:
            logger.critical("FEHLER: JIRA_ACCESS_TOKEN nicht in .env gefunden.")
            raise ValueError("JIRA_ACCESS_TOKEN fehlt.")

        self.loader_mode = loader_mode  # <--- HINZUGEFÜGT
        self.base_url = JIRA_SERVER.rstrip('/')
        self.headers = {"Authorization": f"Bearer {JIRA_API_TOKEN}", "Content-Type": "application/json"}
        self.transformer = JiraDataTransformer()
        self.token_tracker = token_tracker

        # Zustandsvariablen...
        self.processed_keys = set()
        self.processed_keys_lock = Lock()
        self.issues_to_retry = {}
        self.api_timings = []
        self.executor = None
        self.max_workers = MAX_CONCURRENT_REQUESTS
        self.REQUEST_TIMEOUT = REQUEST_TIMEOUT

        os.makedirs(JIRA_ISSUES_DIR, exist_ok=True)

        # --- HINZUGEFÜGT: Stelle DB-Struktur sicher ---
        self._create_db_table_if_not_exists()

    # --- NEUE METHODE (Logik aus update_sqlite_db.py) ---
    def _create_db_table_if_not_exists(self):
        """
        Stellt sicher, dass die DB-Datei und die 'issues'-Tabelle
        existieren, bevor Lese-/Schreiboperationen starten.
        """
        logger.info(f"Sichere, dass DB & Tabelle unter {DB_PATH} existieren...")
        try:
            # Längeres Timeout für potenzielle Thread-Konkurrenz
            with sqlite3.connect(DB_PATH, timeout=10) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS issues (
                    key TEXT PRIMARY KEY,
                    data TEXT,
                    file_last_modified_timestamp INTEGER
                )
                """)
                cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_issue_key ON issues (key);
                """)
                conn.commit()
            logger.info("Datenbank und Tabelle 'issues' sind bereit.")
        except sqlite3.Error as e:
            logger.error(f"Kritischer SQLite-Fehler beim Erstellen der Tabelle: {e}")

    def run(self, start_key: str, loader_mode=None):  # <--- GEÄNDERT
        """
        Orchestriert den gesamten, zweistufigen Ladevorgang für einen Start-Key.
        Diese Methode ist der Haupteinstiegspunkt.
        Kann den Modus der Instanz (self.loader_mode) überschreiben.
        """
        # 1. Zustand für diesen Lauf zurücksetzen
        self.processed_keys.clear()
        self.issues_to_retry.clear()
        self.api_timings.clear()

        start_time = time.time()

        # --- HINZUGEFÜGT: Lademodus bestimmen ---
        current_loader_mode = loader_mode if loader_mode is not None else self.loader_mode
        if current_loader_mode == 'delta':
            logger.info(f"--- Starte REKURSIVEN 'DELTA' Ladevorgang für: {start_key} ---")
        else:
            logger.info(f"--- Starte REKURSIVEN 'FULL' Ladevorgang für: {start_key} ---")
        # --- ENDE ---

        # --- Phase 1: Erster Lade-Durchlauf ---
        logger.info("--- 🚀 Phase 1: Starte ersten Lade-Durchlauf ---")
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            self.executor = executor
            # Start-Issue: Wir erzwingen 'full_load' für das Start-Issue,
            # um sicherzustellen, dass der Baum-Startpunkt aktuell ist.
            # Die Delta-Logik greift dann für alle Kinder in _process_related_issues.
            initial_data = self._fetch_and_process_issue(start_key, 'full_load', is_retry=False)
            if initial_data:
                # Rekursion für alle verknüpften Issues starten (jetzt parallel)
                self._process_related_issues(initial_data, current_loader_mode, is_retry=False)

        self.executor = None  # Executor für Phase 1 beendet

        # --- Phase 2: Retry-Durchlauf ---
        if self.issues_to_retry:
            logger.info(f"--- 🔁 Phase 2: Starte Retry-Durchlauf für {len(self.issues_to_retry)} Issue(s) ---")
            retries = self.issues_to_retry.copy()
            self.issues_to_retry.clear()

            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                self.executor = executor
                future_to_key = {
                    # Retries sind immer 'full_load'
                    executor.submit(self._fetch_and_process_issue, key, 'full_load', True): key
                    for key in retries
                }

                for future in as_completed(future_to_key):
                    try:
                        retried_data = future.result()
                        if retried_data:
                            # Startet die Rekursion auch für fehlgeschlagene
                            # (Modus 'full', da wir im Retry-Pfad sind)
                            self._process_related_issues(retried_data, 'full', True)
                    except Exception as exc:
                        key = future_to_key[future]
                        logger.error(f"Fehler bei Verarbeitung von Retry-Task für {key}: {exc}")
            ...

    # --- NEUE METHODE (für Delta-Modus) ---
    def _get_issue_from_db(self, issue_key: str) -> (dict, int):
        """
        Liest einen Issue-Daten-String und Zeitstempel aus der SQLite-DB
        und parst die Daten.
        """
        try:
            # Nutze kurzlebige Verbindungen für Thread-Sicherheit
            with sqlite3.connect(DB_PATH, timeout=10) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT data, file_last_modified_timestamp FROM issues WHERE key = ?", (issue_key,))
                row = cursor.fetchone()
                if row:
                    try:
                        data = json.loads(row[0])
                        timestamp = row[1]
                        return data, timestamp
                    except json.JSONDecodeError:
                        logger.warning(f"Delta: DB-Daten für {issue_key} korrupt. Ignoriere Cache.")
        except sqlite3.Error as e:
            logger.error(f"Delta: DB-Lesefehler für {issue_key}: {e}")
        return None, None  # Nicht gefunden oder Fehler

    # --- NEUE METHODE (für Delta-Modus) ---
    def _identify_work_batch(self, keys_to_fetch: list[str]) -> (list, list, list):
        """
        Prüft eine Liste von Keys (Batch) gegen die DB und die API.
        Gibt zurück: (new_keys, stale_keys, fresh_keys)
        """
        db_keys_to_check = {}  # {key: db_timestamp}
        keys_in_db = set()
        stale_keys, fresh_keys = [], []

        # 1. DB-Prüfung (Lokal, EINMAL)
        try:
            placeholders = ','.join('?' for _ in keys_to_fetch)
            query = f"SELECT key, file_last_modified_timestamp FROM issues WHERE key IN ({placeholders})"
            with sqlite3.connect(DB_PATH, timeout=10) as conn:
                cursor = conn.cursor()
                cursor.execute(query, keys_to_fetch)
                for row in cursor.fetchall():
                    key = row[0]
                    timestamp = row[1]
                    db_keys_to_check[key] = timestamp
                    keys_in_db.add(key)
        except sqlite3.Error as e:
            logger.error(f"Delta: DB-Batch-Lesefehler: {e}. Lade alle {len(keys_to_fetch)} Keys neu.")
            return keys_to_fetch, [], []  # Fallback: Alle als 'neu' behandeln

        # Keys, die nicht in der DB gefunden wurden
        new_keys = [key for key in keys_to_fetch if key not in keys_in_db]

        if not db_keys_to_check:
            # Nur neue Keys gefunden, keine API-Prüfung nötig
            return new_keys, stale_keys, fresh_keys

        # 2. API-Batch-Prüfung (Remote)
        logger.info(f"Delta: Prüfe Aktualität von {len(db_keys_to_check)} Issues (Batch-JQL)...")
        server_timestamps = self._get_server_timestamps_in_bulk(list(db_keys_to_check.keys()))

        # 3. Vergleich
        for key, db_mtime_int in db_keys_to_check.items():
            server_time_dt = server_timestamps.get(key)

            if server_time_dt:
                db_time_dt = datetime.fromtimestamp(db_mtime_int, tz=timezone.utc)
                if server_time_dt > db_time_dt:
                    stale_keys.append(key)  # Server ist neuer
                else:
                    fresh_keys.append(key)  # DB ist aktuell
            else:
                # Konnte Server-Zeit nicht finden (z.B. gelöscht, Rechteproblem)
                # Wir behandeln es als 'fresh', um keinen API-Call zu triggern.
                logger.warning(f"Delta: Konnte Server-Zeit für {key} nicht finden. Nutze DB-Cache.")
                fresh_keys.append(key)

        return new_keys, stale_keys, fresh_keys

    # --- NEUE METHODE (Helper für _identify_work_batch, adaptiert von jira_freshness_updater.py) ---
    def _get_server_timestamps_in_bulk(self, keys: list[str]) -> dict[str, datetime]:
        """Fragt die 'updated'-Zeitstempel für eine Liste von Keys effizient per JQL ab."""
        server_timestamps = {}
        for i in range(0, len(keys), JQL_CHUNK_SIZE):
            chunk = keys[i:i + JQL_CHUNK_SIZE]
            jql_keys = ', '.join(f'"{key}"' for key in chunk)
            jql_query = f'issuekey in ({jql_keys})'
            url = f"{self.base_url}/rest/api/2/search"
            params = {'jql': jql_query, 'fields': 'updated', 'maxResults': len(chunk)}
            try:
                response = requests.get(url, headers=self.headers, params=params, timeout=self.REQUEST_TIMEOUT)
                response.raise_for_status()
                data = response.json()
                for issue in data.get('issues', []):
                    if updated_str := issue.get('fields', {}).get('updated'):
                        server_timestamps[issue['key']] = datetime.fromisoformat(updated_str)
            except RequestException as e:
                logger.error(f"Delta: JQL-Batch-Abruf fehlgeschlagen: {e}")
        return server_timestamps

    def _fetch_and_process_issue(self, issue_key, task_type: str, is_retry=False):  # <--- STARK GEÄNDERT
        """
        Der Kern-Worker: Lädt, transformiert, speichert (Datei)
        und synchronisiert (DB) ein einzelnes Issue.
        task_type: 'full_load' (API) oder 'db_load' (Cache).
        """
        # 1. Thread-sichere Prüfung (atomar)
        with self.processed_keys_lock:
            if issue_key in self.processed_keys:
                return None  # Bereits in Arbeit oder fertig
            # Sofort als "in Bearbeitung" markieren
            self.processed_keys.add(issue_key)

        # --- NEU: Task-Typ Weiche ---
        if task_type == 'db_load':
            logger.info(f"🔄 Cache 'fresh' (DB): {issue_key}")
            data, _ = self._get_issue_from_db(issue_key)
            if data:
                return data  # Erfolgreich aus DB geladen
            else:
                logger.warning(f"Delta: DB-Load für {issue_key} angefordert, aber nicht gefunden/korrupt. Führe Full-Load aus.")
                # Fällt automatisch durch zu 'full_load'

        # --- TASK 'full_load' (oder Fallback von 'db_load') ---

        # 3. API-Aufruf: Haupt-Issue laden (inkl. Changelog)
        url = f"{self.base_url}/rest/api/2/issue/{issue_key}?expand=names,changelog"
        logger.info(f"⬇️ Lade Issue: {issue_key}" + (" (Retry)" if is_retry else ""))
        try:
            start_request_time = time.monotonic()
            response = requests.get(url, headers=self.headers, timeout=REQUEST_TIMEOUT)
            duration = time.monotonic() - start_request_time
            logger.info(f"{duration:.2f} sek_{url}")

            response.raise_for_status()
            api_data = response.json()

            # 4. API-Aufruf: Hierarchische Child-Issues separat laden
            issue_type_name = api_data.get('fields', {}).get('issuetype', {}).get('name', '')
            child_issues_list = self._find_child_issues(issue_key, issue_type_name)

            # 5. Transformation (delegiert an zentralen Transformer)
            issue_data = self.transformer.transform(api_data, child_issues_list)

            # 6. Speicherung (Datei)
            file_path = os.path.join(JIRA_ISSUES_DIR, f"{issue_key}.json")
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(issue_data, f, indent=2, ensure_ascii=False)

            # 7. HINZUGEFÜGT: Direkte DB-Synchronisierung (UPSERT)
            try:
                # Hole den Zeitstempel der gerade geschriebenen Datei
                file_mtime = int(os.path.getmtime(file_path))

                # Konvertiere Diktionär in JSON-String für die DB
                data_as_string = json.dumps(issue_data)

                # Stelle eine Thread-sichere, kurzlebige Verbindung her
                with sqlite3.connect(DB_PATH, timeout=10) as conn:
                    cursor = conn.cursor()
                    # Nutze "UPSERT" (INSERT ON CONFLICT) für atomares Schreiben
                    cursor.execute("""
                        INSERT INTO issues (key, data, file_last_modified_timestamp)
                        VALUES (?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                            data = excluded.data,
                            file_last_modified_timestamp = excluded.file_last_modified_timestamp
                    """, (issue_key, data_as_string, file_mtime))
                    conn.commit()

            except sqlite3.Error as e:
                logger.error(f"Fehler beim Schreiben von {issue_key} in die SQLite-DB: {e}")
            except Exception as e:
                logger.error(f"Allg. Fehler beim DB-Sync von {issue_key}: {e}")
            # --- ENDE DB-Sync ---

            print(f"💾 Gespeichert: {issue_key}.json (-> DB-Sync)")

            return issue_data

        except RequestException as e:
            logger.error(f"Fehler beim Laden von {issue_key}: {e}")
            self.issues_to_retry[issue_key] = True  # Für Phase 2 vormerken

            # WICHTIG: Bei Fehler wieder für Retry freigeben
            with self.processed_keys_lock:
                if issue_key in self.processed_keys:
                    self.processed_keys.remove(issue_key)
            return None

    def _process_related_issues(self, issue_data, loader_mode='full', is_retry=False):  # <--- STARK GEÄNDERT
        """
        Verarbeitet rekursiv alle verknüpften Folge-Issues parallel.
        Nutzt im 'delta'-Modus den Batch-Check.
        """
        if not issue_data or not issue_data.get("issue_links"):
            return
        if not self.executor:
            logger.error("Executor nicht initialisiert. Abbruch der Rekursion.")
            return

        # 1. Alle zu prüfenden Keys aus den Links sammeln (und Duplikate entfernen)
        keys_to_fetch = list(set([
            link.get("key")
            for link in issue_data["issue_links"]
            if link.get("key")
        ]))

        if not keys_to_fetch:
            return

        # --- NEU: Task-Zuweisung basierend auf Modus ---
        future_to_key = {}

        if loader_mode == 'delta' and not is_retry:
            # 2a. Delta-Modus: Batch-Check ausführen
            new_keys, stale_keys, fresh_keys = self._identify_work_batch(keys_to_fetch)

            logger.info(f"Delta-Batch für Kinder von {issue_data.get('key', 'Unbekannt')}: {len(new_keys)} neu, {len(stale_keys)} veraltet, {len(fresh_keys)} aktuell.")

            # Jobs für 'full_load' (Neue und Veraltete)
            for key in new_keys + stale_keys:
                future_to_key[self.executor.submit(self._fetch_and_process_issue, key, 'full_load', is_retry)] = key

            # Jobs für 'db_load' (Aktuelle)
            for key in fresh_keys:
                future_to_key[self.executor.submit(self._fetch_and_process_issue, key, 'db_load', is_retry)] = key

        else:
            # 2b. Full-Modus (oder Retry): Alle als 'full_load' einreichen
            for key in keys_to_fetch:
                future_to_key[self.executor.submit(self._fetch_and_process_issue, key, 'full_load', is_retry)] = key
        # --- ENDE Task-Zuweisung ---

        if not future_to_key:
            return

        # 3. Auf Ergebnisse warten und Rekursion für die Kinder starten
        for future in as_completed(future_to_key):
            try:
                related_data = future.result()
                if related_data:
                    # REKURSION: Starte den Prozess für das Kind-Issue
                    # Wichtig: Modus und Retry-Status weitergeben
                    self._process_related_issues(related_data, loader_mode, is_retry)
            except Exception as exc:
                key = future_to_key[future]
                logger.error(f"Fehler bei Verarbeitung von Task für {key}: {exc}")

    def _find_child_issues(self, parent_key: str, parent_issue_type: str) -> list:
        """
        Sucht via JQL nach direkten hierarchischen Kind-Issues (z.B. Stories in einem Epic).
        (Diese Logik ist notwendig, um die 'child_issues_list' für den Transformer zu füllen)
        """
        jql_query = None
        if parent_issue_type == 'Epic':
            jql_query = f'"Epic Link" = "{parent_key}" ORDER BY created DESC'
        elif parent_issue_type in PARENT_LINK_TYPES:
            jql_query = f'"Parent Link" = "{parent_key}" ORDER BY created DESC'
        else:
            return [] # Kein Typ, der hierarchische Kinder haben kann

        url = f"{self.base_url}/rest/api/2/search"
        params = {'jql': jql_query, 'fields': 'summary,status,issuetype'}
        response, start_time = None, time.monotonic()

        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            issues = response.json().get('issues', [])
        except RequestException as e:
            logger.error(f"Suche nach Child-Issues für {parent_key} fehlgeschlagen: {e}")
            return []
        finally:
            # Performance-Logging
            duration_ms = (time.monotonic() - start_time) * 1000
            status_code = response.status_code if response is not None else "N/A"
            self.api_timings.append(
                {"operation": "find_child_issues", "duration_ms": duration_ms, "status_code": status_code})

        # Formatieren der Antwort für den Transformer
        relation_name = "issue_in_epic" if parent_issue_type == 'Epic' else "child"
        return [{
            "key": i.get('key'), "title": i.get('fields', {}).get('summary', ''),
            "summary": i.get('fields', {}).get('summary', ''),
            "url": f"{self.base_url}/browse/{i.get('key')}",
            "relation_type": relation_name
        } for i in issues]

    def _log_final_failures(self):
        """Protokolliert alle Issues, die auch nach dem zweiten Versuch fehlschlagen."""
        if not self.issues_to_retry:
            return

        logger.info(
            f"Schreibe {len(self.issues_to_retry)} endgültig fehlgeschlagene Issues in '{ISSUE_LOG_FILE}'")

        existing_keys = set()
        if os.path.exists(ISSUE_LOG_FILE):
            try:
                with open(ISSUE_LOG_FILE, 'r', encoding='utf-8') as f:
                    existing_keys = {line.strip() for line in f}
            except Exception as e:
                logger.error(f"Konnte bestehende Log-Datei nicht lesen: {e}")

        try:
            with open(ISSUE_LOG_FILE, 'a', encoding='utf-8') as f:
                for key in self.issues_to_retry:
                    if key not in existing_keys:
                        f.write(f"{key}\n")
        except Exception as e:
            logger.error(f"Konnte nicht in Log-Datei schreiben: {e}")
