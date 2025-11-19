# src/utils/jira_api_loader.py
"""
Modul für hocheffizientes, paralleles und Delta-basiertes Laden von Jira-Daten.

Dieses Modul stellt die `JiraApiLoader`-Klasse bereit, die einen
fortgeschrittenen, mehrstufigen Prozess implementiert, um Jira-Daten
effizient zu synchronisieren. Es ist für Szenarien optimiert, in denen
bereits ein lokaler Cache von Issue-Daten (als JSON-Dateien) existiert.

Im Gegensatz zu einem naiven "Alles neu laden"-Ansatz, führt dieser Lader
folgende Schritte aus:

1.  **Cache-basierte Baum-Analyse**: Für ein gegebenes Start-Issue
    (z.B. ein Epic) wird zuerst der *lokal* bekannte Abhängigkeitsbaum
    mithilfe des `JiraTreeGenerator` analysiert.
2.  **Delta-Check (Stale Check)**: Der Lader fragt die Jira-API per
    Bulk-JQL-Abfrage (`_get_updated_timestamps_in_bulk`), um die
    `updated`-Zeitstempel der im Cache gefundenen Issues zu erhalten.
3.  **Identifizierung**: Er vergleicht die Server-Zeitstempel mit den
    lokalen Datei-Zeitstempeln, um eine "veraltete" (stale) Teilmenge
    von Issues zu identifizieren (`_identify_stale_issues`).
4.  **Parallele Discovery**: Für alle veralteten Issues wird parallel
    eine "Light"-API-Abfrage durchgeführt, um *neue*, dem Cache
    bisher unbekannte Verknüpfungen (Kinder, Links) zu finden
    (`_find_new_links_for_key`).
5.  **Parallele Verarbeitung**: Nur die veralteten Issues und die neu
    gefundenen Issues werden in einem parallelen Worker-Pool
    vollständig heruntergeladen, transformiert und gespeichert
    (`_process_single_issue`).

## Architektonische Trennung (Separation of Concerns)

Dieser Lader ist ausschließlich für die **Orchestrierung von API-Aufrufen**
(LADEN) und die Steuerung der Parallelität zuständig.

Die eigentliche **Transformation** der rohen API-Antworten in das
saubere, finale JSON-Format wird vollständig an die importierte Klasse
`JiraDataTransformer` delegiert.
"""

import requests
import json
import os
import sys
import time
import concurrent.futures
from requests.exceptions import RequestException
from dotenv import load_dotenv, find_dotenv
from datetime import datetime, timezone

# --- Projekt-Root zum Python-Pfad hinzufügen ---
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# --- Notwendige Imports ---
from utils.logger_config import logger
from utils.config import JIRA_ISSUES_DIR, JIRA_TREE_FULL
from utils.jira_tree_classes import JiraTreeGenerator
from utils.jira_data_transformer import JiraDataTransformer


class JiraApiLoader:
    """
    Lädt und transformiert Jira-Issue-Daten mithilfe der Jira REST-API.
    Nutzt ein optimiertes, mehrstufig paralleles Verfahren:
    1. Gezieltes Discovery: Nutzt JiraTreeGenerator, um den spezifischen Baum
       für ein Start-Issue aus lokalen JSON-Dateien aufzubauen.
    2. Bulk Check: Prüft nur die im Baum gefundenen Issues auf serverseitige Änderungen.
    3. Parallele Delta-Analyse: Sucht parallel nach neuen Verknüpfungen der veralteten Issues.
    4. Parallele Verarbeitung: Verarbeitet nur die veralteten und neuen Issues.
    """
    # Diese Konstante wird für die Lade-Logik (_find_child_issues) benötigt
    PARENT_LINK_TYPES = ['Business Initiative', 'Business Epic', 'Portfolio Epic', 'Initiative']

    MAX_WORKERS = 20
    REQUEST_TIMEOUT = 60

    def __init__(self, jira_server: str, api_token: str, scrape_mode='check', check_days=7, azure_client=None, token_tracker=None):
        self.base_url = jira_server.rstrip('/')
        self.headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
        self.scrape_mode = scrape_mode
        self.check_days = check_days
        self.api_timings = []
        self.token_tracker = token_tracker
        self.tree_generator = JiraTreeGenerator(allowed_types=JIRA_TREE_FULL)
        self.transformer = JiraDataTransformer()

    def _log_api_performance_summary(self):
        """ Loggt eine Zusammenfassung der API-Performance. """
        logger.info("--- API Performance-Bericht ---")
        if not self.api_timings:
            logger.info("Keine API-Aufrufe während dieses Laufs durchgeführt.")
            return
        total_calls = len(self.api_timings)
        durations = [t['duration_ms'] for t in self.api_timings]
        avg_time = sum(durations) / total_calls if total_calls > 0 else 0
        max_time = max(durations) if durations else 0
        min_time = min(durations) if durations else 0
        logger.info(f"  - API-Aufrufe gesamt: {total_calls}")
        logger.info(f"  - Durchschnittliche Antwortzeit: {avg_time:.2f} ms")
        logger.info(f"  - Schnellste Antwortzeit: {min_time:.2f} ms")
        logger.info(f"  - Langsamste Antwortzeit: {max_time:.2f} ms")
        logger.info("---------------------------------")

    def process_epic_tree(self, root_key: str):
        """ Orchestriert den optimierten Lade- und Verarbeitungsprozess. """
        logger.info(f"--- Starte Verarbeitung für Baum von Epic {root_key} ---")
        start_time = time.monotonic()
        self.api_timings.clear()

        # Phase 1: Baum aus dem Cache erstellen
        logger.info(f"--- Phase 1: Baue spezifischen Baum für {root_key} aus lokalem Cache via JiraTreeGenerator ---")
        initial_tree_graph = self.tree_generator.build_issue_tree(root_key)

        keys_in_tree = set()
        if initial_tree_graph:
            keys_in_tree = set(initial_tree_graph.nodes())
            logger.info(f"{len(keys_in_tree)} relevante Issues im Baum von {root_key} gefunden.")
        else:
            logger.warning(f"Konnte initialen Baum für {root_key} aus Cache nicht erstellen. Verarbeite nur den Root-Key selbst.")
            keys_in_tree = {root_key}

        relevant_cached_keys = {
            key: datetime.fromtimestamp(os.path.getmtime(os.path.join(JIRA_ISSUES_DIR, f"{key}.json")), tz=timezone.utc)
            for key in keys_in_tree if os.path.exists(os.path.join(JIRA_ISSUES_DIR, f"{key}.json"))
        }

        # Phase 2: Geänderte Issues identifizieren
        logger.info("--- Phase 2: Suche nach serverseitigen Änderungen für diese Issues via Bulk JQL ---")
        keys_to_process = self._identify_stale_issues(relevant_cached_keys)
        if root_key not in keys_to_process:
            keys_to_process.add(root_key)
            logger.info(f"Füge Root-Key '{root_key}' zur Verarbeitung hinzu, um neue Verknüpfungen zu entdecken.")
        if not relevant_cached_keys and root_key:
             keys_to_process.add(root_key)

        if not keys_to_process:
            logger.info("Keine neuen oder geänderten Issues gefunden. Verarbeitung abgeschlossen.")
            duration = time.monotonic() - start_time
            logger.info(f"Gesamtdauer: {duration:.2f} Sekunden")
            return

        logger.info(f"--- Identifizierung abgeschlossen. {len(keys_to_process)} Issues müssen neu geladen/verarbeitet werden. ---")

        # Phase 3: Parallele Suche nach neuen Verknüpfungen
        logger.info(f"--- Phase 3: Starte PARALLELE Suche nach neuen Verknüpfungen ---")
        all_new_keys = set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            future_to_key = {
                executor.submit(self._find_new_links_for_key, key, keys_in_tree): key
                for key in keys_to_process
            }
            for future in concurrent.futures.as_completed(future_to_key):
                newly_found = future.result()
                if newly_found:
                    all_new_keys.update(newly_found)

        final_keys_to_process = keys_to_process.union(all_new_keys)
        logger.info(f"Verarbeitungssatz finalisiert. {len(final_keys_to_process)} totale Issues zu verarbeiten (veraltete + neue).")

        # Phase 4: Parallele Verarbeitung des finalen Sets
        logger.info(f"--- Phase 4: Starte parallele Verarbeitung von {len(final_keys_to_process)} Issues ---")
        success_count, fail_count, failed_keys_final = 0, 0, []
        total_count = len(final_keys_to_process)
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            future_to_key = {executor.submit(self._process_single_issue, key): key for key in final_keys_to_process}
            processed_count = 0
            for future in concurrent.futures.as_completed(future_to_key):
                processed_count += 1
                key = future_to_key[future]
                try:
                    if future.result():
                        success_count += 1
                        logger.info(f"({processed_count}/{total_count}) ✅ Erfolgreich verarbeitet: {key}")
                    else:
                        fail_count += 1
                        failed_keys_final.append(key)
                        logger.warning(f"({processed_count}/{total_count}) ⚠️ Fehlgeschlagen (im Worker): {key}")
                except Exception as exc:
                    fail_count += 1
                    failed_keys_final.append(key)
                    logger.error(f"({processed_count}/{total_count}) ❌ Unerwarteter Fehler im Thread für {key}: {exc}", exc_info=False)

        duration = time.monotonic() - start_time
        logger.info(f"--- Verarbeitung für Baum {root_key} abgeschlossen. ---")
        logger.info(f"Zusammenfassung: {success_count} erfolgreich, {fail_count} fehlgeschlagen.")
        if failed_keys_final:
            logger.warning(f"Final fehlgeschlagene Keys: {failed_keys_final}")
        self._log_api_performance_summary()
        logger.info(f"Gesamtdauer: {duration:.2f} Sekunden")

    def _find_new_links_for_key(self, key_to_check: str, keys_in_cached_tree: set) -> set:
        """
        Prüft ein einzelnes Issue auf neue, bisher unbekannte Verknüpfungen.

        Diese Methode führt eine "Light"-Abfrage (`_get_issue_details` ohne
        Changelog) und eine `_find_child_issues`-Abfrage durch, um die
        *aktuellen* Server-Verknüpfungen zu erhalten. Sie vergleicht diese
        mit dem bekanten Cache (`keys_in_cached_tree`) und gibt ein Set
        *aller neu entdeckten* Keys zurück.

        Args:
            key_to_check (str): Der Issue-Key, der auf neue Links geprüft wird.
            keys_in_cached_tree (set): Ein Set aller Keys, die im lokalen
                Cache-Baum (Phase 1) gefunden wurden.

        Returns:
            set: Ein Set von Issue-Keys, die neu entdeckt wurden.
        """
        newly_found_keys = set()
        try:
            live_details = self._get_issue_details(key_to_check, expand_changelog=False)
            if not live_details:
                return newly_found_keys

            fields = live_details.get('fields', {})
            live_linked_keys = set()

            issue_type = fields.get('Issue Type', {}).get('name', '')
            child_issues = self._find_child_issues(key_to_check, issue_type)
            for child in child_issues: live_linked_keys.add(child['key'])

            for link in fields.get('Linked Issues', []):
                linked_issue = link.get('outwardIssue') or link.get('inwardIssue')
                if linked_issue and (linked_key := linked_issue.get('key')):
                    live_linked_keys.add(linked_key)

            for task in fields.get('Sub-Tasks', []):
                if linked_key := task.get('key'):
                    live_linked_keys.add(linked_key)

            for linked_key in live_linked_keys:
                if linked_key not in keys_in_cached_tree:
                    logger.info(f"Neue Verknüpfung von {key_to_check} -> {linked_key} gefunden.")
                    newly_found_keys.add(linked_key)
            return newly_found_keys
        except Exception as e:
            logger.error(f"Fehler bei der Suche nach neuen Links für {key_to_check}: {e}")
            return newly_found_keys

    def _get_updated_timestamps_in_bulk(self, keys: list) -> dict:
        """
        Ruft die `updated`-Zeitstempel für eine Liste von Keys per Bulk-JQL ab.

        Args:
            keys (list): Eine Liste von Issue-Keys.

        Returns:
            dict: Ein Mapping `{issue_key: datetime_object}`.
        """
        if not keys: return {}
        server_timestamps = {}
        chunk_size = 200
        for i in range(0, len(keys), chunk_size):
            chunk = keys[i:i + chunk_size]
            jql_keys = ', '.join(f'"{key}"' for key in chunk)
            jql_query = f'issuekey in ({jql_keys})'
            url = f"{self.base_url}/rest/api/2/search"
            params = {'jql': jql_query, 'fields': 'updated', 'maxResults': len(chunk)}
            start_time, response = time.monotonic(), None
            try:
                response = requests.get(url, headers=self.headers, params=params, timeout=self.REQUEST_TIMEOUT)
                response.raise_for_status()
                data = response.json()
                for issue in data.get('issues', []):
                    if updated_str := issue.get('fields', {}).get('updated'):
                        server_timestamps[issue['key']] = datetime.fromisoformat(updated_str)
            except RequestException as e:
                 logger.error(f"Bulk-Abruf für Keys fehlgeschlagen: {e}")
            finally:
                duration_ms = (time.monotonic() - start_time) * 1000
                status_code = response.status_code if response is not None else "N/A"
                self.api_timings.append({"operation": "get_updated_timestamps_in_bulk", "duration_ms": duration_ms, "status_code": status_code})
        return server_timestamps

    def _identify_stale_issues(self, relevant_cached_keys: dict) -> set:
        """
        Identifiziert veraltete Issues durch Zeitstempelvergleich.

        Vergleicht die lokalen Dateizeitstempel (`relevant_cached_keys`)
        mit den Server-Zeitstempeln (`_get_updated_timestamps_in_bulk`)
        und gibt alle Keys zurück, die auf dem Server neuer sind.

        Args:
            relevant_cached_keys (dict): Mapping `{key: local_mtime}`.

        Returns:
            set: Ein Set von "stalen" (veralteten) Issue-Keys.
        """
        stale_keys = set()
        if not relevant_cached_keys: return stale_keys
        keys_to_check = list(relevant_cached_keys.keys())
        server_timestamps = self._get_updated_timestamps_in_bulk(keys_to_check)
        for key, local_mtime in relevant_cached_keys.items():
            server_time = server_timestamps.get(key)
            if not server_time:
                logger.warning(f"Issue {key} im Cache gefunden, aber nicht mehr auf dem Server. Wird ignoriert.")
                continue
            if server_time > local_mtime:
                stale_keys.add(key)
                logger.debug(f"Issue {key} ist veraltet. Server: {server_time}, Lokal: {local_mtime}.")
        return stale_keys

    def _process_single_issue(self, issue_key: str) -> bool:
        """
        Worker-Methode: Lädt, transformiert und speichert ein einzelnes Issue.

        Dies ist die Kern-Arbeitsmethode, die im parallelen Pool (Phase 4)
        ausgeführt wird. Sie führt alle notwendigen Lade-Schritte aus und
        delegiert die Transformation an `self.transformer`.

        Schritte:
        1. Lädt Haupt-Issue-Daten inkl. Changelog (`_get_issue_details`).
        2. Lädt die Liste der hierarchischen Kinder (`_find_child_issues`).
        3. Ruft `self.transformer.transform()` mit beiden Datensätzen auf.
        4. Speichert die resultierende JSON-Datei im `JIRA_ISSUES_DIR`.

        Args:
            issue_key (str): Der zu verarbeitende Issue-Key.

        Returns:
            bool: True bei Erfolg, False bei einem Fehler.
        """
        try:
            issue_details_raw = self._get_issue_details(issue_key, expand_changelog=True)
            if not issue_details_raw:
                logger.error(f"Konnte keine Details für {issue_key} laden, Verarbeitung abgebrochen.")
                return False

            # Lade-Operationen
            issue_type = issue_details_raw.get('fields', {}).get('Issue Type', {}).get('name', '')
            child_issues_list = self._find_child_issues(issue_key, issue_type)

            # --- TRANSFORMATIONSAUFRUF (GEÄNDERT) ---
            # Ruft den externen, reinen Transformer auf.
            # 'issue_details_raw' enthält den changelog, wie vom Transformer erwartet.
            final_data = self.transformer.transform(issue_details_raw, child_issues_list)
            # --- ENDE DER ÄNDERUNG ---

            issue_file_path = os.path.join(JIRA_ISSUES_DIR, f"{issue_key}.json")
            os.makedirs(os.path.dirname(issue_file_path), exist_ok=True)
            with open(issue_file_path, 'w', encoding='utf-8') as f:
                json.dump(final_data, f, indent=4, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"Fehler bei der Verarbeitung von {issue_key} im Worker: {e}", exc_info=False)
            return False

    def _get_issue_details(self, issue_key: str, expand_changelog: bool = True) -> dict:
        """
        Lädt die vollständigen Details für ein einzelnes Issue von der API.
        (GET /rest/api/2/issue/{key})

        Führt eine Vorab-Normalisierung der Custom Fields durch,
        indem die `names`-Map der API-Antwort angewendet wird.

        Args:
            issue_key (str): Der Key des Issues (z.B. "BEMABU-123").
            expand_changelog (bool): Ob das (große) Changelog-Feld
                mitgeladen werden soll.

        Returns:
            dict: Die rohe API-Antwort, oder ein leeres Diktat bei Fehlern.
        """
        url = f"{self.base_url}/rest/api/2/issue/{issue_key}?expand=names"
        if expand_changelog: url += ",changelog"
        response, start_time = None, time.monotonic()
        try:
            logger.info(f"Lade Details für Issue {issue_key} mit {url})")
            response = requests.get(url, headers=self.headers, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            # Feld-Normalisierung (Name Mapping) findet jetzt im Transformer statt,
            # aber es schadet nicht, sie hier als Vorverarbeitung zu belassen.
            # Der Transformer ist idempotent dagegen.
            if 'fields' in data and 'names' in data:
                name_map, cleaned_fields = data.get('names', {}), {k: v for k, v in data['fields'].items() if not (k.startswith('customfield_') and v is None)}
                data['fields'] = {name_map.get(k, k): v for k, v in cleaned_fields.items()}
            return data
        except RequestException as e:
            logger.error(f"API-Abruf für {issue_key} fehlgeschlagen: {e}")
            return {}
        finally:
            duration_ms = (time.monotonic() - start_time) * 1000
            status_code = response.status_code if response is not None else "N/A"
            self.api_timings.append({"operation": "get_issue_details" + ("_full" if expand_changelog else "_light"), "duration_ms": duration_ms, "status_code": status_code})

    def _find_child_issues(self, parent_key: str, parent_issue_type: str) -> list:
        """
        Führt eine JQL-Suche durch, um alle direkten hierarchischen Kinder zu finden.
        (GET /rest/api/2/search)

        Sucht basierend auf dem Typ des Parent-Issues nach:
        - `"Epic Link" = "KEY"` (für 'Epic')
        - `"Parent Link" = "KEY"` (für Typen in `PARENT_LINK_TYPES`)

        Args:
            parent_key (str): Der Key des Eltern-Issues.
            parent_issue_type (str): Der Typ des Eltern-Issues (z.B. "Epic").

        Returns:
            list: Eine vorformatierte Liste von Dictionaries, die für
                  die `transform`-Methode des Transformers bereit ist.
                  (Format: [{"key": ..., "title": ..., "relation_type": ...}])
        """
        jql_query = None
        if parent_issue_type == 'Epic': jql_query = f'"Epic Link" = "{parent_key}" ORDER BY created DESC'
        elif parent_issue_type in self.PARENT_LINK_TYPES: jql_query = f'"Parent Link" = "{parent_key}" ORDER BY created DESC'
        else: return []
        url = f"{self.base_url}/rest/api/2/search"
        params = {'jql': jql_query, 'fields': 'summary,status,issuetype'}
        response, start_time = None, time.monotonic()
        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            issues = response.json().get('issues', [])
        except RequestException as e:
            logger.error(f"Suche nach Child-Issues für {parent_key} fehlgeschlagen: {e}")
            return []
        finally:
            duration_ms = (time.monotonic() - start_time) * 1000
            status_code = response.status_code if response is not None else "N/A"
            self.api_timings.append({"operation": "find_child_issues", "duration_ms": duration_ms, "status_code": status_code})
        return [{"key": i.get('key'), "title": i.get('fields', {}).get('summary', ''), "summary": i.get('fields', {}).get('summary', ''), "url": f"{self.base_url}/browse/{i.get('key')}", "relation_type": "issue_in_epic"} for i in issues]
