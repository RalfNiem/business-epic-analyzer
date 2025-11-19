"""
Identifiziert Business Epics, für die noch kein HTML-Report generiert wurde.

Dieses Skript dient als Hilfsmittel in der Reporting-Pipeline. Es führt
folgende Schritte aus:
1.  Extrahiert alle eindeutigen Business-Epic-Keys aus den Dateinamen im
    Verzeichnis `data/json_summary`. Es wird angenommen, dass jede Datei dort
    die Ergebnisse einer Analyse für ein bestimmtes Epic enthält und der Key
    Teil des Dateinamens ist (z.B. `BEMABU-123_complete_summary.json`).
2.  Überprüft für jeden extrahierten Key, ob im Verzeichnis `data/html_reports`
    bereits eine entsprechende HTML-Datei (z.B. `BEMABU-123_summary.html`)
    existiert. Es werden dabei gängige Namensvarianten berücksichtigt.
3.  Sammelt alle Keys, für die *keine* passende HTML-Datei gefunden wurde.
4.  Speichert die Liste der fehlenden Keys in der Textdatei `keys_missing_html.txt`
    im Projekt-Root.

Diese Liste kann anschließend verwendet werden, um gezielt nur für die fehlenden
Epics die HTML-Generierung anzustoßen.
"""

import os

# --- Konfiguration ---
# Annahme: Dieses Skript wird im Projekt-Root-Verzeichnis ausgeführt.
JSON_SUMMARY_DIR = os.path.join('data', 'json_summary')
HTML_REPORTS_DIR = os.path.join('data', 'html_reports')
OUTPUT_FILE = 'keys_missing_html.txt'

# --- Skriptlogik ---

def extract_epic_keys_from_filenames():
    """
    Extrahiert Epic Keys aus den Dateinamen im JSON-Verzeichnis.
    Der Key ist der Teil des Namens vor dem ersten Unterstrich ('_').
    Gibt eine sortierte Liste mit eindeutigen Schlüsseln zurück.
    """
    if not os.path.isdir(JSON_SUMMARY_DIR):
        print(f"Fehler: Das Verzeichnis '{JSON_SUMMARY_DIR}' wurde nicht gefunden.")
        return []

    print(f"1. Extrahiere Epic Keys aus '{JSON_SUMMARY_DIR}'...")
    epic_keys = []
    for filename in os.listdir(JSON_SUMMARY_DIR):
        if os.path.isfile(os.path.join(JSON_SUMMARY_DIR, filename)) and '_' in filename:
            epic_key = filename.split('_')[0]
            epic_keys.append(epic_key)

    unique_keys = sorted(list(set(epic_keys)))
    print(f"-> {len(unique_keys)} eindeutige Keys gefunden.")
    return unique_keys

def find_keys_without_html(keys_to_check):
    """
    Überprüft für eine Liste von Keys, ob eine entsprechende HTML-Datei
    im HTML-Reports-Verzeichnis existiert.
    Gibt eine Liste der Keys zurück, für die keine HTML-Datei gefunden wurde.
    """
    if not os.path.isdir(HTML_REPORTS_DIR):
        print(f"Fehler: Das Verzeichnis '{HTML_REPORTS_DIR}' wurde nicht gefunden.")
        return keys_to_check

    print(f"\n2. Prüfe auf existierende HTML-Dateien in '{HTML_REPORTS_DIR}'...")
    keys_missing_html = []
    for key in keys_to_check:

        # KORRIGIERT: Prüft jetzt verschiedene mögliche Namensformate
        possible_filenames = [
            f"{key}_summary.html",          # Z.B. BEB2B-121_summary.html
            f"{key}.html",                  # Z.B. BEB2B-121.html
            f"{key}_complete_summary.html" # Z.B. BEB2B-121_complete_summary.html
        ]

        found = False
        for filename in possible_filenames:
            expected_path = os.path.join(HTML_REPORTS_DIR, filename)
            if os.path.exists(expected_path):
                found = True
                break # Datei gefunden, innere Schleife abbrechen

        if not found:
            keys_missing_html.append(key)

    return keys_missing_html

if __name__ == "__main__":
    # Schritt 1: Alle Epic Keys extrahieren
    all_keys = extract_epic_keys_from_filenames()

    if not all_keys:
        print("Vorgang beendet, da keine Keys gefunden wurden.")
    else:
        # Schritt 2: Keys finden, bei denen die HTML-Datei fehlt
        missing_list = find_keys_without_html(all_keys)

        print("\n--- Ergebnis ---")
        # Schritt 3: Ergebnis ausgeben und speichern
        if not missing_list:
            print("✅ Für alle Keys existiert bereits eine HTML-Datei.")
        else:
            print(f"❗️ Für {len(missing_list)} der {len(all_keys)} Keys fehlt eine HTML-Datei:")
            for key in missing_list:
                print(f"  - {key}")

            with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                for key in missing_list:
                    f.write(key + '\n')
            print(f"\n📝 Diese Liste wurde in der Datei '{OUTPUT_FILE}' gespeichert.")
