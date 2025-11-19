"""
"Pre-Cache" Skript zum Erzwingen des Downloads von Cloud-Dateien.

Dieses Hilfsskript dient dazu, die Performance von Analyse-Skripts
zu verbessern, die auf Cloud-Speicher-Verzeichnisse (wie OneDrive)
zugreifen.

Problem:
Cloud-Dienste wie OneDrive verwenden eine Funktion namens "Dateien bei Bedarf"
(Files On-Demand). Dateien werden oft nur als Platzhalter angezeigt
(Symbol: Wolke oder grüner Haken-Umriss) und erst dann physisch
heruntergeladen, wenn ein Programm auf sie zugreift.

Wenn ein Analyse-Skript (z. B. `generate_backlog_analysis.py`) tausende
solcher .json-Dateien nacheinander öffnet, wird jede einzelne
Öffnung durch einen Download blockiert, was den gesamten Prozess extrem
verlangsamt.

Lösung:
Dieses Skript läuft *vor* der eigentlichen Analyse. Es iteriert durch
*alle* .json-Dateien im angegebenen Verzeichnis und führt einen
minimalen Lesezugriff (das Lesen von 1 Byte) durch. Dieser Zugriff
signalisiert dem OneDrive-Client, die vollständige Datei sofort
herunterzuladen.

Nachdem dieses Skript durchgelaufen ist, sind alle Zieldateien
physisch auf der lokalen Festplatte vorhanden (Symbol: ausgefüllter
grüner Haken), was den anschließenden Analyse-Lauf drastisch
beschleunigt.

Konfiguration:
Vor der ersten Ausführung muss die Variable `ONEDRIVE_JIRA_DIR`
in dieser Datei korrekt auf das Zielverzeichnis (z. B. .../data/jira_issues)
gesetzt werden.

Anwendung:
Führen Sie das Skript einfach über die Konsole aus, *bevor* Sie die
eigentliche Analyse starten:

$ python pre_cache_files.py
"""

import os
from pathlib import Path
import time

# --- HIER ANPASSEN ---
# Der Pfad zu Ihrem OneDrive-Verzeichnis, wie Sie ihn in config.py eingetragen haben.
ONEDRIVE_JIRA_DIR = Path("/Users/A763630/Library/CloudStorage/OneDrive-DeutscheTelekomAG/_Dokumente/GitHub/business-epic-analyzer/data/jira_issues")
# --- ENDE ANPASSEN ---

def pre_cache_directory(directory: Path):
    """
    Durchläuft ein Verzeichnis und liest 1 Byte von jeder .json-Datei,
    um den Download von OneDrive zu erzwingen.
    """
    print(f"Starte Pre-Caching für Verzeichnis:\n{directory}\n")
    if not directory.is_dir():
        print(f"Fehler: Verzeichnis nicht gefunden: {directory}")
        return

    start_time = time.time()
    file_count = 0
    errors = 0

    # Zuerst alle Dateien auflisten, um eine Gesamtzahl zu haben
    try:
        print("Suche nach .json-Dateien...")
        json_files = list(directory.glob("*.json"))
        total_files = len(json_files)
        if total_files == 0:
            print("Keine .json-Dateien im Verzeichnis gefunden.")
            return
        print(f"{total_files} .json-Dateien gefunden. Starte Download/Caching...")
    except Exception as e:
        print(f"Fehler beim Auflisten der Dateien: {e}")
        return

    # Jetzt jede Datei durchgehen und "anfassen"
    for i, file_path in enumerate(json_files):
        try:
            # 'rb' (read bytes) öffnen und 1 Byte lesen.
            # Das ist der minimal nötige Zugriff, um den Download auszulösen.
            with file_path.open('rb') as f:
                f.read(1)

            file_count += 1

            # Fortschrittsanzeige, damit Sie sehen, dass etwas passiert
            if (i + 1) % 100 == 0 or (i + 1) == total_files:
                progress = (i + 1) / total_files * 100
                print(f"  ... {i + 1} / {total_files} verarbeitet ({progress:.1f}%)")

        except Exception as e:
            print(f"Fehler beim Lesen von {file_path.name}: {e}")
            errors += 1

    end_time = time.time()
    duration = end_time - start_time

    print("\n--- Pre-Caching abgeschlossen ---")
    print(f"Erfolgreich gelesen: {file_count} Dateien")
    print(f"Fehler:              {errors} Dateien")
    print(f"Dauer:               {duration:.2f} Sekunden")
    print("---------------------------------")

    if errors == 0:
        print("Alle Dateien sind jetzt lokal zwischengespeichert.")
    else:
        print("Warnung: Einige Dateien konnten nicht gelesen werden.")

if __name__ == "__main__":
    pre_cache_directory(ONEDRIVE_JIRA_DIR)
