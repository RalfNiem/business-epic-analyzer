"""
Führt eine Stapelverarbeitung von Jira-Analysen für eine Liste von Epic Keys durch.

Dieses Skript liest eine Liste von Jira-Keys aus der Datei 'Jira_Issues_for_analysis.txt'
(die sich eine Ebene über diesem Skript befinden muss).

Anschließend iteriert es über jeden Key und ruft nacheinander die folgenden
zwei Analyseskripte auf:
1. analyze_issue_dynamics.py (für die Epic/Initiative-Dynamik)
2. analyze_story_backlog.py (für die Story-Backlog-Analyse)

Alle Konsolenausgaben (stdout) der aufgerufenen Funktionen werden
in eine EINZIGE, zeitgestempelte Log-Datei im Verzeichnis
'data/backlog_analyse' umgeleitet.

ZUSÄTZLICH werden die von den Funktionen zurückgegebenen Metriken gesammelt
und eine ZUSAMMENFASSENDE TABELLE im CSV-Format (Semikolon-getrennt)
im selben Verzeichnis gespeichert.

[...] (Restlicher Docstring bleibt gleich)
"""

import os
import sys
import argparse
# import subprocess # NICHT MEHR NÖTIG
from datetime import datetime
# import re # NICHT MEHR NÖTIG
import csv
import traceback
import contextlib # NEU: Für stdout-Umleitung

# NEU: Direkter Import der Analysefunktionen
from analyze_issue_dynamics import analyze_epic_dynamics
from analyze_story_backlog import analyze_story_backlog

# ############################################################################
# CSV-SCHREIBER
# ############################################################################

def write_summary_csv(results, csv_filepath):
    """
    Schreibt die gesammelten Ergebnisse in eine CSV-Datei (Semikolon-getrennt).
    """
    if not results:
        print("Keine Daten zum Schreiben in die CSV-Datei gefunden.")
        return

    # Spaltenüberschriften
    headers = [
        "Issue Key",
        "Gesamtzahl Epics",
        "%-Anteil Epics (Backlog-Typ)",
        "%-Anteil Epics (IN PROGRESS)",
        "%-Anteil Epics (Closed-Typ)",
        "Anzahl erstellte Epics",
        "Anzahl abgeschl. Epics",
        "Anzahl Epics mit Statusänderung",
        "Gesamtzahl Stories",
        "Anzahl offener Stories",
        "Anzahl erstellte Stories",
        "Anzahl abgeschl. Stories",
        "Anzahl Backlogänderung (erstelle Stories minus abgeschlossene Stories)"
    ]

    # Mapping von unseren internen Daten-Keys zu den CSV-Headern
    key_map = {
        "Issue Key": "Issue Key",
        "Gesamtzahl Epics": "Gesamtzahl Epics",
        "%-Anteil Epics (Backlog-Typ)": "% Backlog",
        "%-Anteil Epics (IN PROGRESS)": "% In Progress",
        "%-Anteil Epics (Closed-Typ)": "% Closed",
        "Anzahl erstellte Epics": "Erstellte Epics",
        "Anzahl abgeschl. Epics": "Abgeschl. Epics",
        "Anzahl Epics mit Statusänderung": "Epics Statusänderung",
        "Gesamtzahl Stories": "Gesamtzahl Stories",
        "Anzahl offener Stories": "Offene Stories",
        "Anzahl erstellte Stories": "Erstellte Stories",
        "Anzahl abgeschl. Stories": "Abgeschl. Stories",
        "Anzahl Backlogänderung (erstelle Stories minus abgeschlossene Stories)": "Backlog-Änderung Stories"
    }

    internal_keys_ordered = [key_map[h] for h in headers]

    with open(csv_filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, delimiter=';')

        # 1. Header schreiben
        writer.writerow(headers)

        # 2. Datenzeilen schreiben
        for data_dict in results:
            row = []
            for internal_key in internal_keys_ordered:
                value = data_dict.get(internal_key, "")

                # Floats für deutsche Lokaliseirung (Komma statt Punkt)
                if isinstance(value, float):
                    row.append(f"{value:.0f}".replace('.', ','))
                else:
                    row.append(value)
            writer.writerow(row)

# ############################################################################
# HAUPTFUNKTION
# ############################################################################

def run_analysis():
    # --- 1. Pfade definieren (bleibt gleich) ---
    try:
        current_script_dir = os.path.abspath(os.path.dirname(__file__))
    except NameError:
        current_script_dir = os.path.abspath(os.getcwd())
    project_root = os.path.dirname(current_script_dir)
    input_file_path = os.path.join(project_root, 'Jira_Issues_for_analysis.txt')
    output_dir = os.path.join(project_root, 'data', 'backlog_analyse')
    # python_executable = sys.executable # NICHT MEHR NÖTIG

    # --- 2. Argumente parsen (bleibt gleich) ---
    parser = argparse.ArgumentParser(
        description="Führt eine Batch-Analyse für Jira-Epics aus."
    )
    # ... (Argumente bleiben gleich) ...
    parser.add_argument(
        "--start_date",
        type=str,
        default='01.01.2025',
        help="Startdatum für die Analyse (Format: DD.MM.YYYY). Standard: 01.01.2025"
    )
    parser.add_argument(
        "--stop_date",
        type=str,
        default=None,
        help="Enddatum (inklusiv) (Format: DD.MM.YYYY). Standard: Heute"
    )
    parser.add_argument(
        "--end_date",
        dest="stop_date",
        type=str,
        help="Synonym für --stop_date."
    )
    args = parser.parse_args()

    # --- NEU: Datums-Objekte ZENTRAL erstellen ---
    date_format = '%d.%m.%Y'
    try:
        start_date_obj = datetime.strptime(args.start_date, date_format).date()
    except ValueError:
        print(f"FEHLER: Ungültiges Startdatum: '{args.start_date}'. Bitte das Format DD.MM.YYYY verwenden.", file=sys.stderr)
        sys.exit(1)

    stop_date_obj = None
    if args.stop_date:
        try:
            stop_date_obj = datetime.strptime(args.stop_date, date_format).date()
        except ValueError:
            print(f"FEHLER: Ungültiges Enddatum: '{args.stop_date}'. Bitte das Format DD.MM.YYYY verwenden.", file=sys.stderr)
            sys.exit(1)
    else:
        # Standardwert: Heute
        stop_date_obj = datetime.now().date()

    if start_date_obj > stop_date_obj:
        print(f"Fehler: Das Startdatum ({args.start_date}) liegt nach dem Enddatum ({stop_date_obj.strftime(date_format)}).", file=sys.stderr)
        sys.exit(1)

# --- 3. Eingabedatei prüfen (bleibt gleich) ---
    if not os.path.exists(input_file_path):
        print(f"FEHLER: Eingabedatei nicht gefunden unter:\n{input_file_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_file_path, 'r', encoding='utf-8') as f:
        issue_keys = [line.strip() for line in f if line.strip() and not line.startswith('#')]

    if not issue_keys:
        print(f"WARNUNG: Keine Jira-Keys in '{input_file_path}' gefunden. Beende.", file=sys.stderr)
        sys.exit(0)

    print(f"{len(issue_keys)} Jira-Keys aus '{input_file_path}' geladen.")

    # --- 4. Output-Datei vorbereiten (bleibt gleich) ---
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M')
    output_filename = f"{timestamp}_backlog_analyse.txt"
    output_filepath = os.path.join(output_dir, output_filename)

    print(f"Alle Ausgaben werden in die folgende Datei umgeleitet:\n{output_filename}\n")

    # --- 5. Iteration und Ausführung (MODIFIZIERT) ---
    total_start_time = datetime.now()

    all_results_data = []

    # Standard-Metriken für Fehlerfälle
    default_dynamics_metrics = {
        "Gesamtzahl Epics": 0, "% Backlog": 0, "% In Progress": 0,
        "% Closed": 0, "Erstellte Epics": 0, "Abgeschl. Epics": 0,
        "Epics Statusänderung": 0,
    }

    # --- MODIFIZIERT: Gesamtzahl Stories auf 0 statt "N/A" ---
    default_backlog_metrics = {
        "Gesamtzahl Stories": 0,
        "Offene Stories": 0,
        "Erstellte Stories": 0,
        "Abgeschl. Stories": 0,
        "Backlog-Änderung Stories": 0,
    }


    with open(output_filepath, 'w', encoding='utf-8') as outfile:
        outfile.write(f"Batch-Analyse gestartet am: {total_start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        outfile.write(f"Verarbeite {len(issue_keys)} Issues\n")
        outfile.write(f"Analyse-Zeitraum: {args.start_date} bis {args.stop_date or 'Heute'}\n")
        outfile.write(f"{'='*120}\n\n")

        for i, key in enumerate(issue_keys, 1):
            print(f"Verarbeite Key {i}/{len(issue_keys)}: {key} ...")

            section_header = f"=== Analyse für Issue {i}/{len(issue_keys)}: {key} ==="
            outfile.write(f"\n{'='*len(section_header)}\n{section_header}\n{'='*len(section_header)}\n\n")

            # NEU: Aggregations-Dict für den Key
            key_results = {"Issue Key": key}

            # --- Aufruf 1: analyze_issue_dynamics.py (ersetzt subprocess) ---
            try:
                outfile.write("--- Start analyze_issue_dynamics ---\n")
                outfile.flush() # Puffer leeren

                # NEU: Direkter Aufruf mit stdout-Umleitung in die Log-Datei
                with contextlib.redirect_stdout(outfile):
                    dynamics_data = analyze_epic_dynamics(
                        root_key=key,
                        start_date=start_date_obj,
                        stop_date=stop_date_obj,
                        issue_type='Epic' # Wie im alten Skript implizit
                    )

                outfile.write("\n--- Ende analyze_issue_dynamics ---\n")

                # NEU: Ergebnisse mergen
                key_results.update(dynamics_data or default_dynamics_metrics)

            except Exception as e:
                outfile.write(f"\n--- KRITISCHER FEHLER (analyze_issue_dynamics.py) ---\n")
                outfile.write(f"Konnte Funktion nicht ausführen: {e}\n")
                traceback.print_exc(file=outfile)
                outfile.write("--- Fahre mit nächstem Skript fort ---\n\n")
                key_results.update(default_dynamics_metrics) # Standardwerte bei Fehler

            # --- Aufruf 2: analyze_story_backlog.py (ersetzt subprocess) ---
            try:
                outfile.write("\n--- Start analyze_story_backlog ---\n")
                outfile.flush()

                # NEU: Direkter Aufruf mit stdout-Umleitung
                with contextlib.redirect_stdout(outfile):
                    backlog_data = analyze_story_backlog(
                        epic_key=key,
                        start_date=start_date_obj,
                        stop_date=stop_date_obj
                    )

                outfile.write("\n--- Ende analyze_story_backlog ---\n")

                # NEU: Ergebnisse mergen
                key_results.update(backlog_data or default_backlog_metrics)

            except Exception as e:
                outfile.write(f"\n--- KRITISCHER FEHLER (analyze_story_backlog.py) ---\n")
                outfile.write(f"Konnte Funktion nicht ausführen: {e}\n")
                traceback.print_exc(file=outfile)
                key_results.update(default_backlog_metrics) # Standardwerte bei Fehler

            outfile.write(f"\n\n")
            all_results_data.append(key_results)

        total_end_time = datetime.now()
        duration = total_end_time - total_start_time
        outfile.write(f"Batch-Analyse abgeschlossen am: {total_end_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        outfile.write(f"Gesamtdauer: {duration}\n")

    print(f"\nRoh-Analyse (Datei 1/2) abgeschlossen.")
    print(f"Bericht gespeichert unter: {output_filepath}")

    # ############################################################################
    # NEUER SCHRITT: CSV-Zusammenfassung
    # ############################################################################
    print(f"\nErstelle Zusammenfassungs-Tabelle (Datei 2/2)...")

    csv_filename = f"{timestamp}_backlog_summary.csv"
    csv_filepath = os.path.join(output_dir, csv_filename)

    try:
        # NEU: Direkte Übergabe der gesammelten Daten
        # (Der Aufruf von parse_log_file() entfällt)
        write_summary_csv(all_results_data, csv_filepath)

        print(f"Zusammenfassung erfolgreich gespeichert unter: {csv_filepath}")

    except Exception as e:
        print(f"\n--- KRITISCHER FEHLER beim Erstellen der CSV-Zusammenfassung ---", file=sys.stderr)
        print(f"Fehlermeldung: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        print(f"Die Roh-Log-Datei ({output_filepath}) wurde jedoch erfolgreich erstellt.", file=sys.stderr)


    print(f"\nBatch-Verarbeitung vollständig abgeschlossen.")


if __name__ == "__main__":
    run_analysis()
