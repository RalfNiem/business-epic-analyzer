"""
Lädt gezielt eine Liste von Jira-Issues neu, basierend auf einer Input-Datei.

Dieses Skript ist ein Wartungswerkzeug, das entwickelt wurde, um bestimmte,
in einer Textdatei aufgelistete Jira-Issues erneut von der Weboberfläche zu laden.
Es ist nützlich, um veraltete oder fehlerhafte lokale JSON-Dateien zu aktualisieren.

Funktionsweise:
1.  **Einlesen der Keys:** Liest eine Liste von Jira-Keys aus der Datei
    'created_by_jira_software.txt' im Projekt-Root-Verzeichnis.
2.  **Einmaliger Login:** Initialisiert eine einzige, persistente `JiraScraper`-Sitzung
    und führt einen einmaligen Login durch, um die Effizienz zu maximieren.
3.  **Gezieltes Neuladen:** Iteriert über die eingelesenen Keys, navigiert zur
    jeweiligen Jira-Seite und extrahiert die Daten.
4.  **Überschreiben:** Speichert die neu geladenen Daten und überschreibt damit
    die existierende lokale JSON-Datei für das jeweilige Issue.
5.  **Fokussierter Scope:** Es werden bewusst KEINE untergeordneten `issue_links`
    verfolgt, um den Vorgang auf die in der Datei spezifizierten Issues zu
    beschränken.
6.  **Sicheres Beenden:** Schließt die Browser-Sitzung nach Abschluss aller Vorgänge
    sicher, auch im Fehlerfall.
7.  **Keep-Awake:** Startet einen Hintergrund-Thread, der durch Simulation von
    Tastatureingaben verhindert, dass das System während des langlaufenden
    Scraping-Prozesses in den Ruhezustand geht.
"""

import os
import sys
import threading
import subprocess
import time

# Fügt das Projekt-Root-Verzeichnis zum Python-Pfad hinzu
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.jira_scraper import JiraScraper
from utils.config import JIRA_EMAIL, LLM_MODEL_BUSINESS_VALUE
from utils.logger_config import logger # Importiert den zentralen Logger

# --- Konfiguration ---
INPUT_FILE = os.path.join(project_root, 'created_by_jira_software.txt')


def prevent_screensaver(stop_event):
    """
    Verhindert den System-Bildschirmschoner durch periodische Aktivitätssimulation.

    Diese Funktion wird in einem separaten Hintergrund-Thread ausgeführt. Sie wartet
    in einem festgelegten Intervall (8 Minuten) und simuliert dann systemweit einen
    Tastendruck der Leertaste. Dies signalisiert dem Betriebssystem (macOS), dass
    der Benutzer noch aktiv ist, und verhindert so, dass der Bildschirmschoner
    oder der Ruhezustand aktiviert wird.

    Args:
        stop_event (threading.Event): Ein Event-Objekt, das dem Thread signalisiert,
                                      sich zu beenden, wenn das Hauptprogramm
                                      abgeschlossen ist.
    """
    logger.info("Keep-Awake-Thread gestartet. Drückt alle 8 Minuten die Leertaste.")
    while not stop_event.is_set():
        if stop_event.wait(480):  # Wartet 480 Sekunden (8 Minuten)
            break
        if not stop_event.is_set():
            try:
                # AppleScript, um die Leertaste zu drücken
                script = 'tell application "System Events" to key code 49'
                subprocess.run(['osascript', '-e', script], check=True, capture_output=True)
                logger.info("Keep-Awake: Leertaste gedrückt, um den Bildschirmschoner zu verhindern.")
            except subprocess.CalledProcessError as e:
                logger.error(f"Keep-Awake-Fehler: AppleScript konnte nicht ausgeführt werden. {e}")
            except FileNotFoundError:
                logger.error("Keep-Awake-Fehler: 'osascript' Befehl nicht gefunden. Nur auf macOS verfügbar.")
                break
    logger.info("Keep-Awake-Thread wurde beendet.")


def read_keys_from_file(file_path: str) -> list[str]:
    """
    Liest Jira-Keys zeilenweise aus einer angegebenen Textdatei ein.

    Die Funktion öffnet die Datei, liest jede Zeile und extrahiert die Jira-Keys.
    Dabei werden führende/nachfolgende Leerzeichen entfernt und leere Zeilen
    ignoriert, um eine saubere Liste von Keys zu gewährleisten.

    Args:
        file_path (str): Der vollständige Pfad zur Textdatei, die die Jira-Keys enthält.

    Returns:
        list[str]: Eine Liste der eingelesenen Jira-Keys. Gibt eine leere Liste zurück,
                   wenn die Datei nicht gefunden wird.
    """
    if not os.path.exists(file_path):
        print(f"Fehler: Die Input-Datei '{file_path}' wurde nicht gefunden.")
        return []

    with open(file_path, 'r', encoding='utf-8') as f:
        # Entfernt Leerzeichen und leere Zeilen
        keys = [line.strip() for line in f if line.strip()]

    print(f"{len(keys)} Jira-Keys aus '{file_path}' eingelesen.")
    return keys

def rescrape_issues(keys_to_rescrap: list[str]):
    """
    Orchestriert den Prozess des Neuladens für eine Liste von Jira-Issues.

    Diese Funktion initialisiert den `JiraScraper` im Modus 'true', um ein
    erneutes Laden zu erzwingen. Sie führt einen einmaligen Login durch und
    iteriert dann über die Liste der Keys. Für jeden Key wird die entsprechende
    Jira-Seite aufgerufen, die Daten werden extrahiert und die lokale JSON-Datei
    wird überschrieben. Der Browser wird am Ende des Prozesses sicher geschlossen.

    Args:
        keys_to_rescrap (list[str]): Eine Liste von Jira-Keys, die erneut
                                     geladen werden sollen.
    """
    if not keys_to_rescrap:
        print("Keine Keys zum Neuladen vorhanden. Vorgang beendet.")
        return

    # Initialisiere den Scraper. Die URL wird später für jedes Issue gesetzt.
    # scrape_mode='true' stellt sicher, dass die Issues neu geladen werden.
    scraper = JiraScraper(
        url=f"https://jira.telekom.de/browse/{keys_to_rescrap[0]}",
        email=JIRA_EMAIL,
        model=LLM_MODEL_BUSINESS_VALUE,
        scrape_mode='true'
    )

    try:
        # Führe einen einmaligen Login durch
        if not scraper.login():
            print("Login fehlgeschlagen. Das Skript kann nicht fortfahren.")
            return

        # Iteriere über die Liste der Keys und lade jeden einzeln neu
        for i, key in enumerate(keys_to_rescrap):
            print(f"--- Verarbeite Key {i+1}/{len(keys_to_rescrap)}: {key} ---")
            issue_url = f"https://jira.telekom.de/browse/{key}"

            # Rufe nur die Methode zum Extrahieren der Daten auf.
            # Diese Methode navigiert zur URL, extrahiert die Daten und
            # speichert die JSON-Datei.
            scraper.extract_and_save_issue_data(issue_url, issue_key=key)

    finally:
        # Stelle sicher, dass der Browser am Ende immer geschlossen wird
        print("\nAlle Keys verarbeitet. Schließe die Browser-Sitzung.")
        if scraper.login_handler:
            scraper.login_handler.close()

if __name__ == "__main__":
    # Initialisiert das Event zum Stoppen des Threads
    stop_event = threading.Event()
    # Erstellt und konfiguriert den Hintergrund-Thread
    keep_awake_thread = threading.Thread(target=prevent_screensaver, args=(stop_event,))
    keep_awake_thread.daemon = True
    keep_awake_thread.start()

    try:
        # 1. Keys aus der Datei einlesen
        jira_keys = read_keys_from_file(INPUT_FILE)

        # 2. Scraping-Prozess für die eingelesenen Keys starten
        rescrape_issues(jira_keys)

        print("\nSkript erfolgreich beendet.")

    finally:
        # Stellt sicher, dass der Keep-Awake-Thread sauber beendet wird
        logger.info("Hauptprogramm wird beendet. Stoppe den Keep-Awake-Thread...")
        stop_event.set()
        keep_awake_thread.join(timeout=2)
