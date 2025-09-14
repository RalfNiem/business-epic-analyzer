import json
import os
import re

def clean_description(description: str) -> str | None:
    """
    Prüft, ob ein spezifischer String in der Beschreibung vorkommt und kürzt sie.

    Args:
        description: Der ursprüngliche Beschreibungstext.

    Returns:
        Der gekürzte Text oder None, falls der String nicht gefunden wurde.
    """
    # Definiere den String, nach dem gesucht wird
    # re.escape stellt sicher, dass Sonderzeichen wie '(' oder '?' korrekt behandelt werden
    cutoff_string = r"siehe Ausfüllhilfe:\nhttps://wiki.telekom.de/x/UEVbfQ"

    # re.split() ist robuster als str.split(), da es mit regulären Ausdrücken arbeitet
    parts = re.split(cutoff_string, description)

    # Wenn die Liste mehr als ein Element hat, wurde der String gefunden und die Teilung war erfolgreich
    if len(parts) > 1:
        # Der erste Teil der Liste ist der Text VOR dem Trennstring
        return parts[0].strip()
    else:
        # Der String wurde nicht gefunden
        return None

def process_jira_epics(keys_file_path: str, issues_dir: str, output_file: str):
    """
    Lädt Jira Epics, bereinigt die Beschreibungen und speichert das Ergebnis als JSON.

    Args:
        keys_file_path: Pfad zur Textdatei mit den Jira Keys.
        issues_dir: Pfad zum Verzeichnis mit den JSON-Dateien der Issues.
        output_file: Pfad zur JSON-Ausgabedatei.
    """
    # --- 1. Jira Keys aus der Datei laden ---
    try:
        with open(keys_file_path, 'r', encoding='utf-8') as f:
            # Filtere leere Zeilen und entferne Whitespace
            jira_keys = [line.strip()[2:] for line in f if line.strip()]
        print(f"✅ {len(jira_keys)} Jira Keys aus '{keys_file_path}' geladen.")
    except FileNotFoundError:
        print(f"❌ FEHLER: Die Datei '{keys_file_path}' wurde nicht gefunden.")
        return

    all_results = []
    processed_count = 0

    # --- 2. Jedes Epic verarbeiten ---
    for key in jira_keys:
        json_file_path = os.path.join(issues_dir, f"{key}.json")

        if not os.path.exists(json_file_path):
            print(f"⚠️ WARNUNG: JSON-Datei für Key '{key}' nicht gefunden. Wird übersprungen.")
            continue

        try:
            with open(json_file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            original_description = data.get("description", "")

            # --- 3. Beschreibung bereinigen ---
            new_description = clean_description(original_description)

            # --- 4. Ergebnis-Dictionary erstellen und nur bei Erfolg hinzufügen ---
            if new_description is not None:
                result = {
                    "jira_key": key,
                    "old_description": original_description,
                    "new_description": new_description
                }
                all_results.append(result)
                processed_count += 1
                print(f"✔️ Beschreibung für '{key}' erfolgreich gekürzt.")

        except json.JSONDecodeError:
            print(f"❌ FEHLER: Die Datei '{json_file_path}' enthält kein valides JSON. Wird übersprungen.")
        except Exception as e:
            print(f"❌ Ein unerwarteter Fehler ist bei der Verarbeitung von '{key}' aufgetreten: {e}")

    # --- 5. Ergebnisse als JSON speichern ---
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            # indent=2 sorgt für eine lesbare Formatierung
            # ensure_ascii=False stellt die korrekte Darstellung von Umlauten sicher
            json.dump(all_results, f, indent=2, ensure_ascii=False)
        print(f"\n🎉 Erfolg! {processed_count} von {len(jira_keys)} Beschreibungen wurden gekürzt und in '{output_file}' gespeichert.")
    except Exception as e:
        print(f"\n❌ FEHLER beim Speichern der JSON-Datei: {e}")


# --- Hauptteil des Skripts ---
if __name__ == "__main__":
    # Pfade basierend auf der erwarteten Verzeichnisstruktur definieren
    # Annahme: Das Skript wird im 'src'-Verzeichnis ausgeführt.
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    PROJECT_ROOT = os.path.dirname(BASE_DIR)

    KEYS_FILE = os.path.join(PROJECT_ROOT, 'business_epic_list.txt')
    JIRA_ISSUES_DIR = os.path.join(PROJECT_ROOT, 'data', 'jira_issues')
    OUTPUT_JSON_FILE = os.path.join(PROJECT_ROOT, 'cleaned_descriptions.json')

    # Die Hauptfunktion aufrufen
    process_jira_epics(KEYS_FILE, JIRA_ISSUES_DIR, OUTPUT_JSON_FILE)
