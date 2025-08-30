"""
Auditiert exportierte Jira-Issue-Daten, um fehlende Analyse-Zusammenfassungen
und Dateninkonsistenzen zu identifizieren.

Dieses Skript dient als Werkzeug zur Qualitätssicherung in einer Daten-Pipeline.
Es führt zwei Hauptprüfungen durch:
1. Es verifiziert, ob für relevante Jira-Issues (Business Epics und Initiatives
   im Status 'Closed' oder 'In Progress') eine entsprechende Analyse-Datei
   ('_complete_summary.json') im Zielverzeichnis existiert.
2. Es identifiziert Jira-Issue-Dateien, in denen grundlegende Daten wie der
   'issue_type' fehlen.

Die Ergebnisse werden in zwei separaten Textdateien im Hauptverzeichnis des
Projekts gespeichert, um nachfolgende Verarbeitungsschritte (wie das Scrapen
oder die HTML-Generierung) zu steuern. Das Skript ist so konzipiert, dass es
direkt ausgeführt wird und seine Konfiguration aus den oberen Konstanten bezieht.
"""

import os
import json
import logging

# --- Konfiguration ---
# Verzeichnisse basierend auf der üblichen Projektstruktur.
BASE_DIR = os.path.join(os.path.dirname(__file__), '..')
JIRA_ISSUES_DIR = os.path.join(BASE_DIR, 'data', 'jira_issues')
JSON_SUMMARY_DIR = os.path.join(BASE_DIR, 'data', 'json_summary')

# Namen der Ausgabedateien
OUTPUT_FILE_MISSING_SUMMARY = "missing_summary_list.txt"
OUTPUT_FILE_MISSING_TYPE = "issues_with_missing_type.txt" # NEU

# Logging einrichten
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')


def find_issues():
    """
    Identifiziert und meldet Jira-Issues, die Aufmerksamkeit erfordern.

    Diese Funktion durchsucht das JIRA_ISSUES_DIR und führt zwei Prüfungen durch:
    - **Fehlende Zusammenfassungen:** Sammelt die Keys von "Business Epics" und
      "Business Initiatives" mit dem Status "Closed" oder "In Progress", für die
      keine `_complete_summary.json`-Datei im JSON_SUMMARY_DIR existiert.
    - **Fehlender Issue-Typ:** Sammelt die Keys von Issues, bei denen das Feld
      `issue_type` in der JSON-Datei fehlt oder leer ist.

    Die gesammelten Keys werden in zwei separate Dateien im Projekt-Root geschrieben
    und der Prozess wird auf der Konsole protokolliert.

    Side Effects:
        - Erstellt die Datei `missing_summary_list.txt` im Projekt-Root, falls
          relevante Issues ohne Zusammenfassung gefunden werden.
        - Erstellt die Datei `issues_with_missing_type.txt` im Projekt-Root, falls
          Issues mit fehlendem Typ gefunden werden.
        - Gibt Log-Meldungen auf der Konsole aus.
    """
    # Überprüfen, ob die Verzeichnisse existieren
    if not os.path.isdir(JIRA_ISSUES_DIR):
        logging.error(f"Das Jira-Issues-Verzeichnis wurde nicht gefunden: {JIRA_ISSUES_DIR}")
        return

    keys_needing_summary = []
    keys_with_missing_type = [] # NEUE LISTE

    logging.info(f"Durchsuche Verzeichnis: {os.path.abspath(JIRA_ISSUES_DIR)}")

    # 1. Iteriere über alle Jira Issues im Verzeichnis
    for filename in os.listdir(JIRA_ISSUES_DIR):
        if not filename.endswith('.json'):
            continue

        file_path = os.path.join(JIRA_ISSUES_DIR, filename)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Benötigte Felder sicher extrahieren
            issue_type = data.get("issue_type")
            status = data.get("status")
            key = data.get("key")

            if not key:
                logging.warning(f"Datei {filename} übersprungen, da der 'key' fehlt.")
                continue

            # NEUE PRÜFUNG: Ist der "issue_type" leer oder fehlt er?
            if not issue_type:
                logging.warning(f"-> Fehlender Typ: Issue {key} in Datei {filename} hat keinen 'issue_type'.")
                keys_with_missing_type.append(key)
                continue # Nächste Datei prüfen

            # Bestehende Prüfung für fehlende Summaries
            is_relevant_type = issue_type in ["Business Epic", "Business Initiative"]
            is_relevant_status = status in ["Closed", "In Progress"]

            if is_relevant_type and is_relevant_status:
                summary_filename = f"{key}_complete_summary.json"
                summary_filepath = os.path.join(JSON_SUMMARY_DIR, summary_filename)

                if not os.path.exists(summary_filepath):
                    logging.info(f"-> Fehlende Zusammenfassung für {key} (Status: {status})")
                    keys_needing_summary.append(key)

        except json.JSONDecodeError:
            logging.warning(f"Fehler beim Parsen der JSON-Datei: {filename}")
        except Exception as e:
            logging.warning(f"Ein unerwarteter Fehler ist bei Datei {filename} aufgetreten: {e}")

    # Speichere die Liste der Keys mit fehlenden Zusammenfassungen
    if keys_needing_summary:
        output_path = os.path.join(BASE_DIR, OUTPUT_FILE_MISSING_SUMMARY)
        with open(output_path, 'w', encoding='utf-8') as f:
            for key in sorted(keys_needing_summary):
                f.write(key + '\n')
        logging.info(f"\nErfolgreich! {len(keys_needing_summary)} Issues ohne Zusammenfassung gefunden.")
        logging.info(f"Die Liste wurde in der Datei '{os.path.abspath(output_path)}' gespeichert.")
    else:
        logging.info("\nAlle relevanten Issues haben bereits eine zugehörige Zusammenfassungsdatei.")

    # NEU: Speichere die Liste der Keys mit fehlendem issue_type
    if keys_with_missing_type:
        output_path_type = os.path.join(BASE_DIR, OUTPUT_FILE_MISSING_TYPE)
        with open(output_path_type, 'w', encoding='utf-8') as f:
            for key in sorted(keys_with_missing_type):
                f.write(key + '\n')
        logging.info(f"\nZusätzlich wurden {len(keys_with_missing_type)} Issues ohne 'issue_type' gefunden.")
        logging.info(f"Die Liste wurde in der Datei '{os.path.abspath(output_path_type)}' gespeichert.")
    else:
        logging.info("\nKeine Issues mit fehlendem 'issue_type' gefunden.")


if __name__ == "__main__":
    find_issues()
