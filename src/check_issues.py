import os
import json
from datetime import datetime, timedelta

# Definition der Dateipfade
KEYS_FILE = 'created_by_jira_software.txt'
ISSUES_DIR = 'data/jira_issues/'

def update_jira_keys_list():
    """
    Liest eine Liste von Jira-Keys, prüft JSON-Issue-Dateien auf bestimmte
    Kriterien und fügt neue Keys zur Liste hinzu.
    """
    # 1. 'created_by_jira_software.txt' einlesen
    try:
        with open(KEYS_FILE, 'r') as f:
            existing_keys = set(line.strip() for line in f)
    except FileNotFoundError:
        print(f"Hinweis: Datei '{KEYS_FILE}' nicht gefunden. Es wird eine neue erstellt.")
        existing_keys = set()

    # Datum für den Vergleich vorbereiten (alles vor heute 00:00 Uhr)
    today = datetime.now().date()
    keys_to_add = []

    if not os.path.isdir(ISSUES_DIR):
        print(f"Fehler: Das Verzeichnis '{ISSUES_DIR}' wurde nicht gefunden.")
        return

    # 2. Alle Jira-Issues im Verzeichnis durchgehen
    for filename in os.listdir(ISSUES_DIR):
        # Nur JSON-Dateien berücksichtigen
        if not filename.endswith('.json'):
            continue

        file_path = os.path.join(ISSUES_DIR, filename)

        try:
            # *** KORREKTUR: Datei als JSON laden ***
            with open(file_path, 'r', encoding='utf-8') as f:
                issue_data = json.load(f)

            # *** KORREKTUR: Den Wert des "labels"-Schlüssels prüfen ***
            # .get("labels") ist sicher, falls der Schlüssel mal fehlt
            if issue_data.get("labels") == []:

                # 3. Änderungsdatum prüfen (gestern oder älter)
                mod_time = datetime.fromtimestamp(os.path.getmtime(file_path)).date()
                if mod_time < today:

                    # Jira-Key aus dem Dateinamen extrahieren
                    jira_key = os.path.splitext(filename)[0]

                    # 4. Prüfen, ob der Key bereits existiert
                    if jira_key not in existing_keys:
                        # 5. Key zur Liste der hinzuzufügenden Keys hinzufügen
                        keys_to_add.append(jira_key)
                        print(f"-> Key '{jira_key}' wird zur Liste hinzugefügt.")

        except json.JSONDecodeError:
            print(f"Fehler: Die Datei {filename} ist keine gültige JSON-Datei.")
        except Exception as e:
            print(f"Fehler beim Verarbeiten der Datei {filename}: {e}")

    # Die neuen Keys zur Datei hinzufügen, falls vorhanden
    if keys_to_add:
        with open(KEYS_FILE, 'a') as f:
            for key in sorted(keys_to_add):
                f.write(f"{key}\n")
        print(f"\nErfolgreich {len(keys_to_add)} neue Keys in '{KEYS_FILE}' gespeichert.")
    else:
        print("\nKeine neuen Keys gefunden, die den Kriterien entsprechen.")

if __name__ == "__main__":
    update_jira_keys_list()
