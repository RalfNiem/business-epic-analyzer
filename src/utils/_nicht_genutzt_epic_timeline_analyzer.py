# src/utils/epic_timeline_analyzer.py
"""Analysiert die vollständige Timeline eines Jira Epics.

Dieses Modul führt eine umfassende Analyse des Lebenszyklus von 'Story'- und
'Bug'-Issues durch, die einem bestimmten Epic zugeordnet sind. Es nutzt eine
dynamische Entdeckungsmethode, um alle verknüpften Issues anhand der
Jira-Aktivitäten zu finden und lädt bei Bedarf fehlende Daten automatisch
von Jira nach.

Funktionsweise:
1.  **Dynamische Entdeckung:** Statt einer statischen Baumstruktur analysiert das
    Skript die Aktivitäten des Haupt-Epics. Es identifiziert alle Issues, die
    jemals über das Feld 'Epic Child' mit dem Epic verknüpft wurden.
2.  **Datenvalidierung und Nachladen:** Das Skript prüft, ob für alle entdeckten
    Child-Issues lokale JSON-Dateien existieren. Fehlende oder veraltete
    Issues (gemäß scrape_mode='check') werden automatisch über eine **einmalig
    aufgebaute, persistente Jira-Session** nachgeladen.
3.  **Timeline-Analyse:** Für alle relevanten Issues vom Typ 'Story' oder 'Bug'
    werden folgende Zeitpunkte extrahiert:
    - Der Zeitpunkt der Zuordnung zum Epic (Erstellung im Kontext des Epics).
    - Der Zeitpunkt des Abschlusses ('Resolved' oder 'Closed').
    - Die Durchlaufzeit (Lead Time) von der Zuordnung bis zum Abschluss.
4.  **Visualisierung:** Die Ergebnisse werden sowohl tabellarisch als auch in Form
    von zwei Grafiken ausgegeben:
    - Eine Swimlane-Timeline, die den monatlichen Zu- und Abfluss von Issues zeigt.
    - Ein Histogramm, das die Verteilung der bereinigten Durchlaufzeiten visualisiert.

Besonderheiten:
- **Effizient:** Führt bei der Verarbeitung mehrerer Epics nur einen einzigen
  Login-Vorgang durch und verwendet die Session wieder.
- **Robust:** Lädt fehlende Daten selbstständig nach, anstatt mit einem Fehler
  abzubrechen.
- **Präzise:** Die Analyse basiert auf dem tatsächlichen Aktivitätsverlauf statt
  auf der finalen Baumstruktur, was eine genauere zeitliche Zuordnung ermöglicht.

Usage:
    - Für ein einzelnes Epic:
      python -m src.utils.epic_timeline_analyzer --epic_id BEMABU-2054

    - Für eine Liste von Epics aus einer Datei:
      python -m src.utils.epic_timeline_analyzer --file pfad/zur/deiner_liste.txt
"""
import os
import sys
import json
import argparse
import re
import pandas as pd
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import warnings
warnings.filterwarnings(
    "ignore",
    message="Converting to PeriodArray/Index representation will drop timezone information.",
    category=UserWarning
)

# Pfad-Konfiguration
src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from utils.logger_config import logger
from utils.jira_tree_classes import JiraTreeGenerator
from utils.jira_scraper import JiraScraper
from utils.config import JIRA_ISSUES_DIR, PLOT_DIR, JIRA_EMAIL, LLM_MODEL_BUSINESS_VALUE, SCRAPER_CHECK_DAYS


class EpicTimelineAnalyzer:
    """Orchestriert die Analyse der Erstellungs-, Abschluss- und Laufzeit-Timeline eines Epics.

    Diese Klasse kapselt die gesamte Logik zur dynamischen Entdeckung von Issues,
    zum Nachladen von Daten mittels einer wiederverwendbaren Scraper-Instanz und
    zur Erstellung der analytischen Auswertungen (Tabellen und Grafiken).

    Attributes:
        epic_id (str): Die Jira-ID des zu analysierenden Epics.
        json_dir (str): Der Pfad zum Verzeichnis mit den lokalen JSON-Dateien.
        tree_generator (JiraTreeGenerator): Eine Instanz zur Erstellung der initialen Issue-Hierarchie.
        scraper (JiraScraper | None): Eine optionale, bereits authentifizierte Scraper-Instanz.
    """

    def __init__(self, epic_id: str, json_dir: str = JIRA_ISSUES_DIR, scraper: JiraScraper | None = None):
        """Initialisiert den Analyzer.

        Args:
            epic_id (str): Die Jira-ID des zu analysierenden Epics.
            json_dir (str, optional): Der Pfad zum Verzeichnis der JSON-Dateien.
            scraper (JiraScraper | None, optional): Eine existierende, bereits authentifizierte
                JiraScraper-Instanz zur Wiederverwendung der Session.
        """
        self.epic_id = epic_id
        self.json_dir = json_dir
        self.tree_generator = JiraTreeGenerator(json_dir=self.json_dir)
        self.scraper = scraper
        logger.info(f"EpicTimelineAnalyzer für '{self.epic_id}' initialisiert.")

    def _parse_key(self, value_str: str) -> str | None:
        """Extrahiert einen JIRA-Key aus einem String mithilfe von regulären Ausdrücken."""
        if not value_str: return None
        match = re.search(r'([A-Z]+-\d+)', value_str)
        return match.group(1) if match else None

    def _clean_status_name(self, raw_name: str) -> str:
        """Bereinigt rohe Status-Namen, um die Vergleichbarkeit zu gewährleisten."""
        if not raw_name: return "N/A"
        if '[' in raw_name:
            try:
                return raw_name.split(':')[1].split('[')[0].strip().upper()
            except IndexError:
                return raw_name.strip().upper()
        return raw_name.strip().upper()

    def analyze_timeline(self) -> pd.DataFrame | None:
        """Führt die vollständige Analyse durch, inkl. dynamischer Entdeckung und Nachladen.

        Diese Methode ist der zentrale Orchestrator. Sie führt folgende Schritte aus:
        1.  Dynamische Entdeckung aller Child-Issues anhand der 'Epic Child'-Aktivitäten.
        2.  Automatisches Nachladen fehlender oder veralteter Issue-Daten von Jira
            über die bereitgestellte, persistente Scraper-Instanz.
        3.  Extraktion der Erstellungs- (Zuordnung zum Epic) und Abschlussdaten für
            alle relevanten 'Story'- und 'Bug'-Issues.

        Returns:
            pd.DataFrame | None: Ein pandas DataFrame mit den aufbereiteten Timeline-Daten
                                 oder None, wenn keine analysierbaren Issues gefunden wurden.
        """
        logger.info(f"Starte dynamische Entdeckung aller Child-Issues für {self.epic_id}...")
        initial_tree = self.tree_generator.build_issue_tree(self.epic_id)
        if not initial_tree:
            logger.error(f"Konnte initialen Issue-Baum für '{self.epic_id}' nicht erstellen.")
            return None

        activities_with_source = []
        for key in set(initial_tree.nodes()):
            file_path = os.path.join(self.json_dir, f"{key}.json")
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    issue_data = json.load(f)
                    for activity in issue_data.get('activities', []):
                        # Speichere die Quell-ID zusammen mit der Aktivität
                        activities_with_source.append((key, activity))
            except (FileNotFoundError, json.JSONDecodeError):
                continue

        from collections import defaultdict
        child_activities = []
        for source_key, act in activities_with_source:
            if act.get('feld_name') == 'Epic Child':
                # Hier ist die gewünschte Log-Ausgabe
                logger.info(f"'Epic Child' Aktivität in Quell-Issue '{source_key}' identifiziert.")
                child_activities.append(act)

        daily_activity_groups = defaultdict(list)
        for act in child_activities:
            child_key = self._parse_key(act.get('neuer_wert')) or self._parse_key(act.get('alter_wert'))
            if child_key:
                daily_activity_groups[(child_key, act['zeitstempel_iso'][:10])].append(act)

        net_add_activities = []
        for activities in daily_activity_groups.values():
            if any(act.get('neuer_wert') for act in activities) and not any(not act.get('neuer_wert') and act.get('alter_wert') for act in activities):
                net_add_activities.extend(act for act in activities if act.get('neuer_wert'))

        if not net_add_activities:
            logger.warning(f"Keine Netto-'Hinzufügen'-Aktivitäten für Epic {self.epic_id} gefunden.")
            return None

        required_child_keys = {self._parse_key(act['neuer_wert']) for act in net_add_activities}
        logger.info(f"{len(required_child_keys)} einzigartige Child-Issues durch Aktivitäten entdeckt.")

        missing_keys = [key for key in required_child_keys if key and not os.path.exists(os.path.join(self.json_dir, f"{key}.json"))]

        if missing_keys:
            if self.scraper:
                print(f"\n--- {len(missing_keys)} fehlende Child-Issues für Epic {self.epic_id} werden über bestehende Session nachgeladen... ---")
                for i, key in enumerate(missing_keys):
                    print(f"Lade Issue {i+1}/{len(missing_keys)}: {key}")
                    issue_url = f"https://jira.telekom.de/browse/{key}"
                    self.scraper.extract_and_save_issue_data(issue_url, key)
                print("--- Nachladen für dieses Epic abgeschlossen. ---")
            else:
                logger.warning("Fehlende Issues gefunden, aber es wurde kein Scraper für das automatische Nachladen bereitgestellt.")

        logger.info("Analysiere Erstellungs- und Abschlussdaten für alle entdeckten Issues...")
        timeline_data = []
        closed_stati = ['CLOSED', 'RESOLVED', 'DONE']
        creation_dates = {self._parse_key(act['neuer_wert']): act['zeitstempel_iso'] for act in net_add_activities}

        for key in required_child_keys:
            if not key: continue
            file_path = os.path.join(self.json_dir, f"{key}.json")
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    issue_data = json.load(f)
                    issue_type = issue_data.get("issue_type")
                    if issue_type not in ['Story', 'Bug']:
                        continue
                    activities = issue_data.get('activities', [])
                    closing_date = None
                    close_events = [act['zeitstempel_iso'] for act in activities if act.get('feld_name') == 'Status' and self._clean_status_name(act.get('neuer_wert')) in closed_stati]
                    if close_events:
                        closing_date = min(close_events)
                    timeline_data.append({
                        "key": key,
                        "type": issue_type,
                        "creation_date": datetime.fromisoformat(creation_dates[key]),
                        "closing_date": datetime.fromisoformat(closing_date) if closing_date else pd.NaT
                    })
            except (FileNotFoundError, json.JSONDecodeError):
                continue

        if not timeline_data:
            logger.warning(f"Keine gültigen 'Story'- oder 'Bug'-Issues zur Analyse für {self.epic_id} gefunden.")
            return None
        return pd.DataFrame(timeline_data)

    def create_timeline_plot(self, df: pd.DataFrame):
        """Erstellt eine "Swimlane"-Grafik für neue und abgeschlossene Issues."""
        if df is None or df.empty: return

        df['creation_month'] = df['creation_date'].dt.to_period('M')
        df['closing_month'] = df['closing_date'].dt.to_period('M')
        new_pivot = pd.pivot_table(df, index='creation_month', columns='type', values='key', aggfunc='count', fill_value=0)
        closed_pivot = pd.pivot_table(df.dropna(subset=['closing_date']), index='closing_month', columns='type', values='key', aggfunc='count', fill_value=0)

        start_date = min(new_pivot.index.min(), closed_pivot.index.min()) if not closed_pivot.empty else new_pivot.index.min()
        full_date_range = pd.period_range(start=start_date, end=pd.to_datetime('today').to_period('M'), freq='M')

        new_pivot = new_pivot.reindex(full_date_range, fill_value=0)
        closed_pivot = closed_pivot.reindex(full_date_range, fill_value=0)

        colors = {'Story_New': 'skyblue', 'Bug_New': 'lightcoral', 'Story_Closed': 'royalblue', 'Bug_Closed': 'firebrick'}
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(20, 14), sharex=True)
        fig.suptitle(f"Swimlane-Timeline für Stories & Bugs im Epic: {self.epic_id}", fontsize=20)

        new_pivot.plot(kind='bar', stacked=True, ax=ax1, color=[colors.get('Bug_New', 'red'), colors.get('Story_New', 'blue')])
        ax1.set_title("Swimlane 1: Monatlich neu erstellte Issues (Zufluss)", fontsize=14)
        ax1.set_ylabel("Anzahl Issues")
        ax1.grid(axis='y', linestyle='--', alpha=0.7)
        for container in ax1.containers:
            ax1.bar_label(container, label_type='center', fontsize=10, color='white', fontweight='bold')

        if not closed_pivot.empty:
            closed_pivot.plot(kind='bar', stacked=True, ax=ax2, color=[colors.get('Bug_Closed', 'darkred'), colors.get('Story_Closed', 'darkblue')])
            ax2.set_title("Swimlane 2: Monatlich abgeschlossene Issues (Abfluss)", fontsize=14)
            for container in ax2.containers:
                ax2.bar_label(container, label_type='center', fontsize=10, color='white', fontweight='bold')
        else:
            # Wenn keine Daten vorhanden sind, zeichnen wir eine leere Grafik mit einer Nachricht
            ax2.set_title("Swimlane 2: Monatlich abgeschlossene Issues (Abfluss)", fontsize=14)
            ax2.text(0.5, 0.5, 'Keine abgeschlossenen Issues gefunden', horizontalalignment='center', verticalalignment='center', transform=ax2.transAxes, fontsize=12, color='gray')
            ax2.set_yticks([]) # Entfernt die y-Achsen-Striche für eine saubere Optik

        ax2.set_ylabel("Anzahl Issues")
        ax2.set_xlabel("Monat")
        ax2.grid(axis='y', linestyle='--', alpha=0.7)

        for container in ax2.containers:
            ax2.bar_label(container, label_type='center', fontsize=10, color='white', fontweight='bold')

        ax2.set_xticklabels([p.strftime('%b %Y') for p in new_pivot.index], rotation=45, ha='right')

        legend_patches = [
            mpatches.Patch(color=colors['Story_New'], label='Story (Neu erstellt)'),
            mpatches.Patch(color=colors['Bug_New'], label='Bug (Neu erstellt)'),
            mpatches.Patch(color=colors['Story_Closed'], label='Story (Abgeschlossen)'),
            mpatches.Patch(color=colors['Bug_Closed'], label='Bug (Abgeschlossen)')
        ]
        fig.legend(handles=legend_patches, loc='upper right', fontsize=12, bbox_to_anchor=(0.9, 0.95))
        plt.tight_layout(rect=[0, 0, 1, 0.96])

        output_path = os.path.join(PLOT_DIR, f"{self.epic_id}_swimlane_timeline.png")
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        logger.info(f"Swimlane-Grafik gespeichert unter: {output_path}")
        plt.close(fig)

    def create_lead_time_histogram(self, df: pd.DataFrame):
        """Erstellt ein Histogramm der bereinigten Durchlaufzeiten."""
        if df is None or df.empty: return

        closed_df = df.dropna(subset=['closing_date']).copy()
        if closed_df.empty:
            logger.warning(f"Keine abgeschlossenen Issues für die Laufzeit-Analyse von {self.epic_id} gefunden.")
            return

        closed_df['lead_time_days'] = (closed_df['closing_date'] - closed_df['creation_date']).dt.days

        positive_lead_time_df = closed_df[closed_df['lead_time_days'] >= 0].copy()

        if positive_lead_time_df.empty:
            logger.warning(f"Nach dem Filtern auf positive Laufzeiten bleiben für {self.epic_id} keine Issues für die Analyse übrig.")
            return None

        stories_lead_time = positive_lead_time_df[positive_lead_time_df['type'] == 'Story']['lead_time_days']
        bugs_lead_time = positive_lead_time_df[positive_lead_time_df['type'] == 'Bug']['lead_time_days']

        stats = {"Story": stories_lead_time.describe(), "Bug": bugs_lead_time.describe()}

        fig, ax = plt.subplots(figsize=(16, 8))

        max_lead_time = positive_lead_time_df['lead_time_days'].max()
        bins = range(0, int(max_lead_time) + 15, 15)

        ax.hist(stories_lead_time, bins=bins, color='skyblue', alpha=0.7, label='Stories')
        ax.hist(bugs_lead_time, bins=bins, color='lightcoral', alpha=0.7, label='Bugs')

        if not stories_lead_time.empty:
            ax.axvline(stats['Story']['mean'], color='royalblue', linestyle='--', linewidth=2, label=f"Ø Story: {stats['Story']['mean']:.1f} Tage")
        if not bugs_lead_time.empty:
            ax.axvline(stats['Bug']['mean'], color='firebrick', linestyle='--', linewidth=2, label=f"Ø Bug: {stats['Bug']['mean']:.1f} Tage")

        ax.set_title(f"Verteilung der Durchlaufzeit für Epic: {self.epic_id} (bereinigt)", fontsize=16)
        ax.set_xlabel("Durchlaufzeit von Zuordnung bis Abschluss in Tagen")
        ax.set_ylabel("Anzahl der Issues")
        ax.legend()
        ax.grid(axis='y', linestyle='--', alpha=0.7)

        output_path = os.path.join(PLOT_DIR, f"{self.epic_id}_lead_time_histogram.png")
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        logger.info(f"Laufzeit-Histogramm gespeichert unter: {output_path}")
        plt.close(fig)

        return stats

def get_epics_from_input(epic_id_arg: str | None, file_arg: str | None) -> list[str]:
    """Lädt Business Epics entweder aus einem Argument oder einer Datei."""
    if epic_id_arg:
        return [epic_id_arg]

    file_path = file_arg
    if not file_path:
        file_path = input("Bitte geben Sie den Pfad zur TXT-Datei mit Business Epics ein (oder drücken Sie Enter für 'BE_Liste.txt'): ")
        if not file_path:
            file_path = "BE_Liste.txt"

    file_to_try = file_path if os.path.exists(file_path) else f"{file_path}.txt"
    if not os.path.exists(file_to_try):
        print(f"FEHLER: Die Datei {file_to_try} existiert nicht.")
        return []

    with open(file_to_try, 'r') as file:
        business_epics = [line.strip() for line in file if line.strip()]
    print(f"{len(business_epics)} Business Epics in '{file_to_try}' gefunden.")
    return business_epics

# Hauptausführung
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Analysiert die Erstellungs-, Abschluss- und Laufzeit-Timeline eines oder mehrerer Jira Epics.")
    parser.add_argument("--epic_id", type=str, help="Die ID eines einzelnen Business Epics, das analysiert werden soll.")
    parser.add_argument("--file", type=str, help="Der Pfad zu einer TXT-Datei, die eine Liste von Business Epic IDs enthält (eine pro Zeile).")
    args = parser.parse_args()

    epics_to_process = get_epics_from_input(args.epic_id, args.file)

    if not epics_to_process:
        print("Keine Business Epics zur Verarbeitung gefunden. Das Programm wird beendet.")
        sys.exit(0)

    scraper_instance = None
    try:
        print("\n--- Initialisiere Jira-Session für alle anstehenden Operationen... ---")
        scraper_instance = JiraScraper(
            "https://jira.telekom.de",
            JIRA_EMAIL,
            model=LLM_MODEL_BUSINESS_VALUE,
            scrape_mode='check',
            check_days=SCRAPER_CHECK_DAYS
        )

        login_success = scraper_instance.login_handler.login(scraper_instance.url, scraper_instance.email)
        if not login_success:
            print("Login fehlgeschlagen. Das Programm kann fehlende Issues nicht nachladen.")
            scraper_instance = None
        else:
            scraper_instance.driver = scraper_instance.login_handler.driver
            print("--- Session erfolgreich initialisiert. ---\n")

        # Die Hauptschleife über alle Epics
        for i, epic_id in enumerate(epics_to_process):
            print(f"\n\n==================================================================")
            print(f" Verarbeite Epic {i+1}/{len(epics_to_process)}: {epic_id}")
            print(f"==================================================================")

            analyzer = EpicTimelineAnalyzer(epic_id=epic_id, scraper=scraper_instance)
            df_timeline = analyzer.analyze_timeline()

            # KORREKTUR: Der folgende Block wurde NACH INNEN in die Schleife verschoben.
            # ------------------- START DES VERSCHOBENEN BLOCKS -------------------
            if df_timeline is not None and not df_timeline.empty:
                print(f"\n--- Analyse der Timeline von {len(df_timeline)} Stories & Bugs für {epic_id} ---")
                df_timeline['creation_date'] = pd.to_datetime(df_timeline['creation_date'], utc=True)
                df_timeline['closing_date'] = pd.to_datetime(df_timeline['closing_date'], utc=True)

                new_pivot = pd.pivot_table(df_timeline, index=df_timeline['creation_date'].dt.to_period('M'), columns='type', values='key', aggfunc='count', fill_value=0)
                closed_pivot = pd.pivot_table(df_timeline.dropna(subset=['closing_date']), index=df_timeline.dropna(subset=['closing_date'])['closing_date'].dt.to_period('M'), columns='type', values='key', aggfunc='count', fill_value=0)

                print("\n=== Monatlich neu erstellte Issues ===")
                print(new_pivot)
                print("\n=== Monatlich abgeschlossene Issues ===")
                print(closed_pivot)

                analyzer.create_timeline_plot(df_timeline)

                lead_time_stats = analyzer.create_lead_time_histogram(df_timeline)
                if lead_time_stats:
                    print("\n=== Analyse der bereinigten Durchlaufzeiten (in Tagen) ===")
                    print("Hinweis: Issues mit negativer Laufzeit (Abschluss vor Epic-Zuordnung) wurden für diese Statistik entfernt.")
                    if 'Story' in lead_time_stats and not lead_time_stats['Story'].empty:
                        print("\n--- Stories ---")
                        print(f"Durchschnitt: {lead_time_stats['Story']['mean']:.1f} Tage")
                        print(f"Median:       {lead_time_stats['Story']['50%']:.1f} Tage")
                        print(f"Schnellste:   {lead_time_stats['Story']['min']:.0f} Tage | Langsamste: {lead_time_stats['Story']['max']:.0f} Tage")
                    if 'Bug' in lead_time_stats and not lead_time_stats['Bug'].empty:
                        print("\n--- Bugs ---")
                        print(f"Durchschnitt: {lead_time_stats['Bug']['mean']:.1f} Tage")
                        print(f"Median:       {lead_time_stats['Bug']['50%']:.1f} Tage")
                        print(f"Schnellste:   {lead_time_stats['Bug']['min']:.0f} Tage | Langsamste: {lead_time_stats['Bug']['max']:.0f} Tage")
            else:
                print(f"\n---> Keine 'Story'- oder 'Bug'-Issues für das Epic {epic_id} gefunden, die analysiert werden konnten.")
            # ------------------- ENDE DES VERSCHOBENEN BLOCKS -------------------

    finally:
        if scraper_instance and scraper_instance.login_handler:
            print("\n--- Schließe Jira-Session. ---")
            scraper_instance.login_handler.close()
