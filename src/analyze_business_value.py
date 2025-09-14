import os
import json
from collections import defaultdict

# --- Konfiguration ---
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
JIRA_ISSUES_DIR = os.path.join(BASE_DIR, 'data', 'jira_issues')
OUTPUT_FILE = os.path.join(BASE_DIR, 'business_value_analysis.txt')


def analyze_jira_issues():
    """
    Analysiert Jira-Issue-Dateien auf das Vorhandensein von Business-Value-Einträgen.
    """
    if not os.path.isdir(JIRA_ISSUES_DIR):
        print(f"Fehler: Das Verzeichnis '{JIRA_ISSUES_DIR}' wurde nicht gefunden.")
        return [], [], defaultdict(list)

    keys_with_any_bv = []
    keys_without_any_bv = []
    keys_by_detailed_field = defaultdict(list)

    # *** KORREKTUR: Eindeutige Felder für 'justification' ***
    fields_to_check = [
        "revenue", "cost_saving", "risk_loss",
        "risk_minimization", "strat_enablement", "time"
    ]
    # Die 'justification'-Felder werden jetzt separat und eindeutig behandelt.

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

            impact = business_value.get('business_impact', {})
            strategy = business_value.get('strategic_enablement', {})
            time_crit = business_value.get('time_criticality', {})

            has_content = any(impact.values()) or any(strategy.values()) or any(time_crit.values())
            if has_content:
                keys_with_any_bv.append(key)
            else:
                keys_without_any_bv.append(key)

            # --- Zweite Analyse mit eindeutigen Feldnamen ---
            # Standardfelder prüfen
            for field in fields_to_check:
                if impact.get(field): keys_by_detailed_field[field].append(key)
                if strategy.get(field): keys_by_detailed_field[field].append(key)
                if time_crit.get(field): keys_by_detailed_field[field].append(key)

            # *** KORREKTUR: Spezifische 'justification'-Felder prüfen und eindeutig benennen ***
            if impact.get('justification'):
                keys_by_detailed_field['business_impact_justification'].append(key)
            if strategy.get('justification'):
                keys_by_detailed_field['strategic_enablement_justification'].append(key)
            if time_crit.get('justification'):
                keys_by_detailed_field['time_criticality_justification'].append(key)

        except (json.JSONDecodeError, IOError) as e:
            print(f"Warnung: Konnte Datei {filename} nicht verarbeiten: {e}")

    return sorted(keys_with_any_bv), sorted(keys_without_any_bv), keys_by_detailed_field

def save_results_to_file(with_bv, without_bv, by_field, output_path):
    """
    Speichert die Analyseergebnisse in einer formatierten Textdatei.
    """
    print(f"\nSpeichere Ergebnisse in '{output_path}'...")
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("="*60 + "\n")
            f.write("      Analyse der Business Value Einträge\n")
            f.write("="*60 + "\n\n")

            f.write(f"--- {len(with_bv)} Issues MIT mindestens einem Business Value Eintrag ---\n")
            f.writelines(f"- {key}\n" for key in with_bv)
            f.write("\n")

            f.write(f"--- {len(without_bv)} Issues OHNE jeglichen Business Value Eintrag ---\n")
            f.writelines(f"- {key}\n" for key in without_bv)
            f.write("\n")

            f.write("="*60 + "\n")
            f.write("      Detaillierte Feld-Analyse\n")
            f.write("="*60 + "\n\n")

            for field, keys in sorted(by_field.items()):
                unique_keys = sorted(list(set(keys)))
                f.write(f"--- {len(unique_keys)} Issues mit Eintrag im Feld '{field}' ---\n")
                f.writelines(f"- {key}\n" for key in unique_keys)
                f.write("\n")
        print("Speichern erfolgreich.")
    except IOError as e:
        print(f"Fehler beim Schreiben der Datei: {e}")


if __name__ == "__main__":
    keys_with_bv, keys_without_bv, keys_by_field = analyze_jira_issues()
    if keys_with_bv or keys_without_bv:
        save_results_to_file(keys_with_bv, keys_without_bv, keys_by_field, OUTPUT_FILE)
    else:
        print("\nKeine relevanten 'Business Epics' oder 'Business Initiatives' zur Analyse gefunden.")
