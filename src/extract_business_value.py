import os
import json
from collections import defaultdict

# --- Konfiguration ---

# Annahme, dass das Skript im 'src'-Verzeichnis liegt.
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
JIRA_ISSUES_DIR = os.path.join(BASE_DIR, 'data', 'jira_issues')

# Name für die Ausgabedatei.
OUTPUT_FILE = os.path.join(BASE_DIR, 'business_value_details.txt')

def analyze_and_extract_business_value():
    """
    Durchsucht und analysiert Jira-Issues in einem einzigen Durchgang.

    Diese Funktion führt einen umfassenden Scan des Jira-Issue-Verzeichnisses durch.
    Sie identifiziert relevante Business Epics und Initiatives, prüft das Vorhandensein
    von 'business_value'-Einträgen und extrahiert gleichzeitig die detaillierten
    Textinhalte der einzelnen Felder. Alle Ergebnisse werden gesammelt und für die
    finale Speicherung vorbereitet.

    Returns:
        dict: Ein Dictionary, das die extrahierten Inhalte für jedes Business-Value-Feld
              enthält. Der Schlüssel ist der Feldname (z.B. 'revenue') und der Wert ist
              ein weiteres Dictionary, das Jira-Keys auf deren Feldinhalte abbildet.
              Beispiel: {'revenue': {'BEMABU-123': 'Wert des Feldes'}}
    """
    if not os.path.isdir(JIRA_ISSUES_DIR):
        print(f"Fehler: Das Verzeichnis '{JIRA_ISSUES_DIR}' wurde nicht gefunden.")
        return defaultdict(dict)

    # Dictionary für die detaillierte Feldanalyse
    all_field_contents = defaultdict(dict)

    # Eindeutige Felder definieren, nach denen gesucht wird
    fields_to_check = {
        'business_impact': ["revenue", "cost_saving", "risk_loss", "justification"],
        'strategic_enablement': ["risk_minimization", "strat_enablement", "justification"],
        'time_criticality': ["time", "justification"]
    }

    print(f"Durchsuche Verzeichnis: {JIRA_ISSUES_DIR}")

    for filename in os.listdir(JIRA_ISSUES_DIR):
        if not filename.endswith('.json') or not (filename.startswith('BEMABU-') or filename.startswith('BEB2B-')):
            continue

        file_path = os.path.join(JIRA_ISSUES_DIR, filename)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if data.get('issue_type') not in ['Business Epic', 'Business Initiative']:
                continue

            key = data.get('key')
            business_value = data.get('business_value', {})
            if not key or not business_value:
                continue

            # Iteriere durch die Sektionen (business_impact, etc.)
            for section, fields in fields_to_check.items():
                section_data = business_value.get(section, {})
                if not section_data:
                    continue

                # Iteriere durch die Felder (revenue, etc.)
                for field in fields:
                    content = section_data.get(field)
                    if content:
                        # Erstelle einen eindeutigen Namen für die Speicherung,
                        # besonders wichtig für 'justification'
                        unique_field_name = f"{section}_{field}"
                        all_field_contents[unique_field_name][key] = content

        except (json.JSONDecodeError, IOError) as e:
            print(f"Warnung: Konnte Datei {filename} nicht verarbeiten: {e}")

    return all_field_contents


def save_detailed_content(all_contents: dict, output_path: str):
    """
    Speichert die extrahierten und gruppierten Feldinhalte in einer formatierten Textdatei.

    Args:
        all_contents (dict): Die gesammelten Inhalte aus der Analyse.
        output_path (str): Der Pfad zur Ausgabedatei.
    """
    print(f"\nSpeichere Detailinhalte in '{output_path}'...")
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("="*80 + "\n")
            f.write("      Detaillierte Inhalte der Business Value Felder\n")
            f.write("="*80 + "\n\n")

            # Sortiere nach dem Feldnamen für eine konsistente Ausgabe
            for field, contents in sorted(all_contents.items()):
                f.write(f"--- Inhalte für Feld: '{field}' ({len(contents)} Einträge) ---\n\n")
                if not contents:
                    f.write("Keine Inhalte gefunden.\n\n")
                else:
                    # Sortiere nach Jira-Key
                    for key, value in sorted(contents.items()):
                        f.write(f"  - Key: {key}\n")
                        # Formatiert mehrzeilige Inhalte für bessere Lesbarkeit
                        formatted_value = str(value).replace('\n', '\n    ')
                        f.write(f"    Inhalt: {formatted_value}\n\n")
                f.write("-" * 80 + "\n\n")
        print("Speichern erfolgreich.")
    except IOError as e:
        print(f"Fehler beim Schreiben der Datei: {e}")


if __name__ == "__main__":
    # Führe die kombinierte Analyse und Extraktion durch
    detailed_contents = analyze_and_extract_business_value()

    # Speichere die Ergebnisse
    if detailed_contents:
        save_detailed_content(detailed_contents, OUTPUT_FILE)
    else:
        print("\nKeine relevanten 'Business Epics' oder 'Business Initiatives' mit Inhalten gefunden.")
