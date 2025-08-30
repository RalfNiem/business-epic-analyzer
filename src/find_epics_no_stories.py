"""
Identifiziert Business Epics, die keine untergeordneten Stories enthalten.

Dieses Skript dient der Datenanalyse und Qualitätssicherung. Es durchsucht ein
bestimmtes Verzeichnis nach `_complete_summary.json`-Dateien, die zuvor von
anderen Analyse-Skripten generiert wurden.

Für jede gefundene Datei wird der Wert des Schlüssels 'total_stories' innerhalb
des 'scope_summary'-Objekts geprüft. Wenn dieser Wert 0 ist, wird der
entsprechende Epic-Key (aus dem Dateinamen extrahiert) gesammelt.

Das Ergebnis ist eine Textdatei, die eine Liste aller Epic-Keys enthält, die
keine Stories aufweisen. Diese Liste kann als Grundlage für weitere Analysen
oder zur Bereinigung von Projekt-Backlogs dienen.
"""

import os
import json
import logging

# --- Konfiguration ---
# Geben Sie hier das Verzeichnis an, in dem Ihre JSON-Dateien liegen.
# Annahme: Das Skript wird im 'src'-Verzeichnis ausgeführt und die Daten sind in '../data/json_summary'
# Passen Sie den Pfad bei Bedarf an.
INPUT_DIRECTORY = os.path.join(os.path.dirname(__file__), '..', 'data', 'json_summary')

# Geben Sie hier den Namen der Ausgabedatei an.
OUTPUT_FILE = "epics_with_zero_stories.txt"

# Logging einrichten
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')


def find_epics_with_zero_stories(input_dir: str, output_file: str):
    """
    Durchsucht ein Verzeichnis nach *_complete_summary.json Dateien, filtert diejenigen
    mit total_stories == 0 und speichert die Epic-Keys in einer Datei.

    Args:
        input_dir (str): Der Pfad zum Verzeichnis mit den JSON-Dateien.
        output_file (str): Der Name der Ausgabedatei.
    """
    if not os.path.isdir(input_dir):
        logging.error(f"Das angegebene Verzeichnis existiert nicht: {input_dir}")
        return

    epics_with_zero_stories = []
    logging.info(f"Durchsuche Verzeichnis: {os.path.abspath(input_dir)}")

    # 1. Iteriere über alle Dateien im Verzeichnis
    for filename in os.listdir(input_dir):
        if filename.endswith("_complete_summary.json"):
            file_path = os.path.join(input_dir, filename)

            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # 2. Extrahiere den Wert von "total_stories"
                # .get() wird verwendet, um Fehler zu vermeiden, falls ein Schlüssel fehlt
                scope_summary = data.get("scope_summary", {})
                total_stories = scope_summary.get("total_stories")

                # 3. Wenn "total_stories" == 0, füge den Epic-Key zur Liste hinzu
                if total_stories == 0:
                    # Extrahiere den Epic-Key aus dem Dateinamen
                    epic_key = filename.replace("_complete_summary.json", "")
                    epics_with_zero_stories.append(epic_key)
                    logging.info(f"-> Gefunden: {epic_key} hat 0 Stories.")

            except json.JSONDecodeError:
                logging.warning(f"Fehler beim Parsen der JSON-Datei: {filename}")
            except Exception as e:
                logging.warning(f"Ein unerwarteter Fehler ist bei Datei {filename} aufgetreten: {e}")

    # 4. Speichere die Liste in einer Datei
    if epics_with_zero_stories:
        output_path = os.path.join(os.path.dirname(__file__), '..', output_file)
        with open(output_path, 'w', encoding='utf-8') as f:
            for key in sorted(epics_with_zero_stories): # Sortiert für Konsistenz
                f.write(key + '\n')
        logging.info(f"\nErfolgreich! {len(epics_with_zero_stories)} Epics mit 0 Stories gefunden.")
        logging.info(f"Die Liste wurde in der Datei '{output_path}' gespeichert.")
    else:
        logging.info("\nKeine Epics mit 0 Stories im angegebenen Verzeichnis gefunden.")


if __name__ == "__main__":
    find_epics_with_zero_stories(INPUT_DIRECTORY, OUTPUT_FILE)
