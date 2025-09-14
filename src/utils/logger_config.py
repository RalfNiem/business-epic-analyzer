# src/utils/logger_config.py
import logging
import os
import sys
from collections import deque
from utils.config import LOGS_DIR, MAX_LOG_ITEMS

def _trim_log_file(log_path, max_lines):
    """
    Überprüft eine Datei und kürzt sie auf eine maximale Zeilenanzahl,
    indem die ältesten Zeilen (am Anfang der Datei) entfernt werden.
    """
    if not os.path.exists(log_path) or max_lines <= 0:
        return

    try:
        # Effizientes Einlesen der letzten 'max_lines' Zeilen
        with open(log_path, 'r', encoding='utf-8') as f:
            # deque ist optimiert für das Anhängen und Entfernen von beiden Enden
            lines = deque(f, maxlen=max_lines)

        # Überprüfen, ob die Datei tatsächlich gekürzt werden muss.
        # Dies ist der Fall, wenn die Datei ursprünglich mehr als max_lines hatte.
        # Wir können das nur indirekt prüfen, indem wir die Datei neu schreiben,
        # falls die gelesene Zeilenanzahl der maximalen Kapazität des deque entspricht
        # und die Datei nicht leer war.
        if lines and os.path.getsize(log_path) > 0:
            # Hole die aktuelle Zeilenanzahl, um unnötiges Schreiben zu vermeiden
            # Wir müssen die Datei erneut öffnen, um die Gesamtzahl zu ermitteln
            with open(log_path, 'r', encoding='utf-8') as f:
                total_lines = sum(1 for _ in f)

            if total_lines > max_lines:
                # Schreibe nur die notwendigen Zeilen zurück
                with open(log_path, 'w', encoding='utf-8') as f:
                    f.writelines(lines)

                # Wir können hier keinen Logger verwenden, da er gerade konfiguriert wird.
                # Daher eine einfache print-Ausgabe zur Information.
                # print(f"INFO: Logdatei auf die letzten {len(lines)} Zeilen gekürzt.")

    except Exception as e:
        # Ausgabe, falls beim Kürzen ein Fehler auftritt
        print(f"WARNUNG: Fehler beim Kürzen der Log-Datei {log_path}: {e}")


def setup_logger():
    """
    Konfiguriert den Logger mit separaten Loglevels für Datei und Konsole
    und kürzt die Log-Datei bei der Initialisierung.
    """
    # Pfad für die Log-Datei
    log_file_path = os.path.join(LOGS_DIR, "jira_scraper.log")

    # *** NEUER SCHRITT: Log-Datei vor der Initialisierung kürzen ***
    _trim_log_file(log_file_path, MAX_LOG_ITEMS)

    # Logger-Instanz holen
    logger = logging.getLogger("jira_scraper")
    logger.setLevel(logging.INFO)  # Das niedrigste Level, das verarbeitet wird

    # Verhindern, dass bei jedem Import neue Handler hinzugefügt werden
    if logger.hasHandlers():
        logger.handlers.clear()

    logger.propagate = False

    # Sicherstellen, dass das Log-Verzeichnis existiert
    os.makedirs(LOGS_DIR, exist_ok=True)

    # Formatter, der für beide Handler verwendet wird
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # 1. File Handler: Schreibt alles ab INFO-Level in die Datei
    # Der Modus 'a' (append) wird beibehalten. Das Kürzen geschieht davor.
    file_handler = logging.FileHandler(log_file_path, mode='a', encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    # 2. Console Handler: Gibt alles ab WARNING-Level im Terminal aus
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)

    # Handler zum Logger hinzufügen
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger

# Erstelle eine globale Logger-Instanz, die überall importiert werden kann
logger = setup_logger()
