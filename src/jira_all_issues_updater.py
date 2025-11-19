# jira_force_reload.py
"""
Force Reload Skript für Jira Issues (jira_force_reload.py)

Dieses Skript ist eine Modifikation des 'Freshness Updaters'.
Sein Zweck ist es, **alle** lokal im JIRA_ISSUES_DIR vorhandenen JSON-Dateien
erneut von der Jira-API abzurufen und zu überschreiben.

Es findet **keine** Überprüfung statt, ob die Issues veraltet sind.

Das Skript implementiert zudem eine Windows "Keep-Awake"-Logik, um zu
verhindern, dass der PC während des (potenziell langen) Ladevorgangs
in den Ruhezustand geht oder der Bildschirm gesperrt wird.

Die Abarbeitung erfolgt rein seriell, um API-Rate-Limits zu schonen.

**Verwendung:**

Das Skript wird direkt ohne Argumente ausgeführt. Es benötigt die Umgebungsvariablen
`JIRA_SERVER_URL` und `JIRA_ACCESS_TOKEN`.

```bash
python jira_force_reload.py
"""

import os
import sys
import json
import time
import requests
import logging
import contextlib # NEU: Für Keep-Awake Context Manager
import ctypes     # NEU: Für Windows API (Keep-Awake)
from datetime import datetime
from requests.exceptions import RequestException
from dotenv import load_dotenv, find_dotenv

# --- 1. Konfiguration & Initialisierung ---
load_dotenv(find_dotenv())
project_root = os.path.abspath(os.path.dirname(__file__))
src_path = os.path.join(project_root, 'src')
if src_path not in sys.path:
    sys.path.insert(0, src_path)

from utils.config import LOGS_DIR, JIRA_ISSUES_DIR
from utils.jira_data_transformer import JiraDataTransformer

JIRA_SERVER = os.getenv("JIRA_SERVER_URL", 'https://jira.telekom.de')
JIRA_API_TOKEN = os.getenv("JIRA_ACCESS_TOKEN")

os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(JIRA_ISSUES_DIR, exist_ok=True)

# --- 2. Logger-Konfiguration ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

if not logger.handlers:
    log_file = os.path.join(LOGS_DIR, "force_reload.log") # Log-Dateiname angepasst
    file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)


# --- 3. NEU: Keep-Awake Context Manager ---

# Windows-Konstanten (aus test_keep_awake_windows.py)
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

@contextlib.contextmanager
def windows_keep_awake(logger_instance):
    """
    Context-Manager, der Windows während der Ausführung wach hält.
    Verhindert System-Ruhezustand und Bildschirm-Abschaltung.
    """
    if sys.platform != "win32":
        logger_instance.info("Kein Windows-System. Keep-Awake-Logik wird übersprungen.")
        yield # Einfach den Code-Block ausführen
        return # Und beenden

    logger_instance.info("Windows-System erkannt. Initialisiere Keep-Awake...")
    kernel32 = None
    try:
        kernel32 = ctypes.windll.kernel32
        keep_awake_flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED

        if kernel32.SetThreadExecutionState(keep_awake_flags) == 0:
            logger_instance.warning("SetThreadExecutionState (Aktivieren) fehlgeschlagen.")
        else:
            logger_instance.info("Keep-Awake (System + Display) ist jetzt AKTIV.")

        yield # Hier läuft der Haupt-Update-Prozess

    except Exception as e:
        logger_instance.error(f"Fehler beim Aktivieren von Keep-Awake: {e}")
        if 'yield' not in locals(): # Fallback, falls 'yield' nie erreicht wurde
            yield
    finally:
        if kernel32:
            logger_instance.info("Deaktiviere Keep-Awake...")
            reset_flags = ES_CONTINUOUS
            if kernel32.SetThreadExecutionState(reset_flags) == 0:
                logger_instance.warning("SetThreadExecutionState (Zurücksetzen) fehlgeschlagen.")
            else:
                logger_instance.info("Keep-Awake ist DEAKTIVIERT. System-Timer sind wieder normal.")
        else:
            logger_instance.info("Keep-Awake wurde nie initialisiert, kein Zurücksetzen nötig.")


# --- 4. JiraBatchUpdater Klasse (angepasst) ---

class JiraBatchUpdater:
    """
    Kapselt die Logik für das serielle Neuladen von Jira-Issues.
    """
    REQUEST_TIMEOUT = 60
    PARENT_LINK_TYPES = ['Business Initiative', 'Business Epic', 'Portfolio Epic', 'Initiative']

    def __init__(self, jira_server: str, api_token: str):
        if not all([jira_server, api_token]):
            raise ValueError("JIRA_SERVER_URL und JIRA_ACCESS_TOKEN müssen gesetzt sein.")
        self.base_url = jira_server.rstrip('/')
        self.headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
        self.transformer = JiraDataTransformer()
        logger.info(f"JiraBatchUpdater initialisiert für Server {jira_server}.")

    def get_all_local_issue_keys(self) -> list[str]:
        """
        ** MODIFIZIERT: Liest alle Issue-Keys aus den Dateinamen im JIRA_ISSUES_DIR
        und filtert sie, um nur Keys > 'SNOWTC' zurückzugeben. **
        Führt keine Zeitstempel-Prüfung durch.
        """
        logger.info("--- Phase 1: Lese alle lokal vorhandenen Issue-Keys ein ---")
        print("\n--- Phase 1: Lese alle lokal vorhandenen Issue-Keys ein ---")

        keys = []
        all_local_keys_count = 0 # Zum Zählen aller gefundenen Keys vor dem Filtern

        try:
            for filename in os.listdir(JIRA_ISSUES_DIR):
                if filename.endswith(".json"):
                    all_local_keys_count += 1
                    key = os.path.splitext(filename)[0]

        except FileNotFoundError:
             logger.warning(f"Verzeichnis {JIRA_ISSUES_DIR} nicht gefunden.")
             return []
        except Exception as e:
             logger.error(f"Fehler beim Lesen des Verzeichnisses {JIRA_ISSUES_DIR}: {e}")
             return []

        logger.info(f"✅ {all_local_keys_count} lokale Issues insgesamt gefunden. Nach Filterung (>{filter_key_threshold}) werden {len(keys)} Issues neu geladen.")
        print(f"✅ {all_local_keys_count} lokale Issues insgesamt gefunden. Nach Filterung (>{filter_key_threshold}) werden {len(keys)} Issues neu geladen.")
        return keys

    def run_batch_update(self, keys_to_update: list[str]):
        """
        Phase 2: Führt das SERIELLE Update für alle übergebenen Keys aus.
        (Funktionalität für Statistik und Ausgabe bleibt gleich)
        """
        if not keys_to_update:
            logger.info("Keine Issues zum Neuladen gefunden, Batch-Lauf wird übersprungen.")
            return

        logger.info(f"--- Phase 2: Starte SERIELLES Force-Reload für {len(keys_to_update)} Issues ---")
        print(f"\n--- Phase 2: Starte SERIELLES Force-Reload für {len(keys_to_update)} Issues ---")

        start_time = time.monotonic()
        success_count, fail_count = 0, 0
        total_count = len(keys_to_update)

        logger.info(f"🚀 Starte serielle Verarbeitung...")
        print(f"🚀 Starte serielle Verarbeitung...")

        for i, key in enumerate(keys_to_update):
            processed_count = i + 1
            progress = f"({processed_count}/{total_count})"

            try:
                if self._process_single_issue(key):
                    success_count += 1
                    logger.info(f"{progress} ✅ Erfolgreich neu geladen: {key}")
                else:
                    fail_count += 1
                    logger.warning(f"{progress} ⚠️ Fehlgeschlagen (im Worker): {key}")
                    print(f"   ...⚠️ Fehlgeschlagen (im Worker): {key}")
            except Exception as exc:
                fail_count += 1
                logger.error(f"{progress} ❌ Unerwarteter Fehler bei {key}: {exc}", exc_info=False)
                print(f"   ...❌ Unerwarteter Fehler bei {key}: {exc}")

        duration = time.monotonic() - start_time
        avg_time_per_issue = (duration / success_count) if success_count > 0 else 0

        print("\n" + "="*40)
        logger.info("--- Batch-Verarbeitung abgeschlossen ---")
        logger.info(f"Zusammenfassung: {success_count} erfolgreich, {fail_count} fehlgeschlagen.")
        logger.info(f"Gesamtdauer: {duration:.2f} Sekunden")
        logger.info(f"Durchschnittszeit pro Issue: {avg_time_per_issue:.2f} Sekunden")

        print("\n--- ✅ Batch-Verarbeitung abgeschlossen ---")
        print(f"Erfolgreich neu geladene Issues: {success_count}")
        print(f"Fehlgeschlagen:                   {fail_count}")
        print(f"Gesamtdauer:                      {duration:.2f} Sekunden")
        print(f"Durchschnitt pro Issue:           {avg_time_per_issue:.2f} Sekunden")

    def _process_single_issue(self, issue_key: str) -> bool:
        """
        Verarbeitet ein einzelnes Issue: Abrufen, Suchen von Kindern, Transformieren, Speichern.
        (Konsolenausgabe beim Speichern bleibt erhalten)
        """
        try:
            # 1. Rohdaten abrufen
            issue_details_raw = self._get_issue_details(issue_key, expand_changelog=True)
            if not issue_details_raw:
                return False

            # 2. Issue-Typ extrahieren
            issue_type = issue_details_raw.get('fields', {}).get('issuetype', {}).get('name', '')

            # 3. Kind-Issues suchen (API-Aufruf)
            child_issues_list = self._find_child_issues(issue_key, issue_type)

            # 4. Transformation
            final_data = self.transformer.transform(
                issue_details_raw,
                child_issues_list
            )

            # 5. Datei speichern
            issue_file_path = os.path.join(JIRA_ISSUES_DIR, f"{issue_key}.json")
            with open(issue_file_path, 'w', encoding='utf-8') as f:
                json.dump(final_data, f, indent=4, ensure_ascii=False)

            return True

        except Exception as e:
            logger.error(f"Fehler bei der Verarbeitung von {issue_key} im Worker: {e}", exc_info=False)
            return False

    def _get_issue_details(self, issue_key: str, expand_changelog: bool = True) -> dict:
        """ Ruft die Rohdaten eines Issues ab. """
        url = f"{self.base_url}/rest/api/2/issue/{issue_key}?expand=names"
        if expand_changelog: url += ",changelog"
        try:
            response = requests.get(url, headers=self.headers, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            return data
        except RequestException as e:
            logger.error(f"API-Abruf für {issue_key} fehlgeschlagen: {e}")
            return {}

    def _find_child_issues(self, parent_key: str, parent_issue_type: str) -> list:
        """ Findet Kind-Issues (API-Aufruf). """
        jql_query = None
        if parent_issue_type == 'Epic': jql_query = f'"Epic Link" = "{parent_key}"'
        elif parent_issue_type in self.PARENT_LINK_TYPES: jql_query = f'"Parent Link" = "{parent_key}"'
        else: return []

        url = f"{self.base_url}/rest/api/2/search"
        params = {'jql': jql_query, 'fields': 'summary,status,issuetype'}
        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            issues = response.json().get('issues', [])

            if parent_issue_type in self.PARENT_LINK_TYPES:
                relation_name = "child"
            elif parent_issue_type == 'Epic':
                relation_name = "issue_in_epic"
            else:
                relation_name = "unknown"

            return [{"key": i.get('key'), "title": i.get('fields', {}).get('summary', ''), "summary": i.get('fields', {}).get('summary', ''), "url": f"{self.base_url}/browse/{i.get('key')}", "relation_type": relation_name} for i in issues]
        except RequestException as e:
            logger.error(f"Suche nach Child-Issues für {parent_key} fehlgeschlagen: {e}")
            return []


# --- 5. Ausführung ---
if __name__ == "__main__":
    if not JIRA_API_TOKEN:
        logger.error("❌ FEHLER: 'JIRA_ACCESS_TOKEN' nicht gefunden.")
        sys.exit(1)

    updater = JiraBatchUpdater(jira_server=JIRA_SERVER, api_token=JIRA_API_TOKEN)

    # ** NEU: Hauptlogik in den Keep-Awake-Kontext eingebettet **
    try:
        with windows_keep_awake(logger):

            # 1. Alle lokalen Keys holen (statt 'identify_stale_issues')
            issues_to_reload = updater.get_all_local_issue_keys()

            # 2. Batch-Update (seriell) ausführen
            if issues_to_reload:
                updater.run_batch_update(issues_to_reload)
            else:
                logger.info("Keine lokalen Issues gefunden, die neu geladen werden könnten.")
                print("\nKeine lokalen Issues gefunden. Es gibt nichts zu tun.")

    except KeyboardInterrupt:
        logger.warning("\nProgrammabbruch durch Benutzer (Strg+C).")
        print("\nProgrammabbruch durch Benutzer (Strg+C).")
        # Der 'finally'-Block im Keep-Awake-Manager wird trotzdem ausgeführt
        sys.exit(130)
    except Exception as e:
        logger.critical(f"Ein kritischer Fehler ist aufgetreten: {e}", exc_info=True)
        print(f"\nEin kritischer, unerwarteter Fehler ist aufgetreten: {e}")
        # Der 'finally'-Block im Keep-Awake-Manager wird trotzdem ausgeführt
        sys.exit(1)

    logger.info("✅ Programm erfolgreich beendet.")
    print("\n✅ Programm erfolgreich beendet.")
