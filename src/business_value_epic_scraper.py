import os
import sys

# Fügt das Projekt-Root-Verzeichnis zum Python-Pfad hinzu
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.jira_scraper import JiraScraper
from utils.azure_ai_client import AzureAIClient
from utils.prompt_loader import load_prompt_template
from utils.config import JIRA_EMAIL, LLM_MODEL_BUSINESS_VALUE
from utils.logger_config import logger

def scrape_epics_from_file(file_path: str):
    """
    Liest eine Liste von Jira-Keys aus einer Datei ein und führt für jeden
    einzelnen Key einen Scraping-Lauf durch, um Business Value & Akzeptanzkriterien
    zu aktualisieren.
    """
    logger.info(f"Starte Batch-Scraping für Business Epics aus der Datei: {file_path}")

    # 1. Lese die Liste der Jira-Keys aus der Datei
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            # Filtere leere Zeilen und entferne Whitespace
            issue_keys = [line.strip().lstrip('- ').strip() for line in f if line.strip()]
        if not issue_keys:
            logger.error(f"Die Datei {file_path} ist leer oder enthält keine gültigen Keys.")
            return
        logger.info(f"{len(issue_keys)} Jira-Keys wurden aus der Datei geladen.")
    except FileNotFoundError:
        logger.error(f"Fehler: Die Eingabedatei '{file_path}' wurde nicht gefunden.")
        return

    # 2. Initialisiere den AI-Client und den Scraper einmal für die gesamte Sitzung
    business_value_system_prompt = load_prompt_template("business_value_prompt.yaml", "system_prompt")
    ai_client = AzureAIClient(system_prompt=business_value_system_prompt)
    base_jira_url = "https://jira.telekom.de/"

    # Der Scraper wird ohne URL initialisiert, da diese in der Schleife dynamisch gesetzt wird
    scraper = JiraScraper(
        url=base_jira_url,
        email=JIRA_EMAIL,
        model=LLM_MODEL_BUSINESS_VALUE,
        scrape_mode='true',
        azure_client=ai_client
    )

    try:
        # 3. Logge dich einmal für den gesamten Batch-Lauf ein
        if not scraper.login():
            logger.error("Login fehlgeschlagen. Der Batch-Lauf wird abgebrochen.")
            return

        # 4. Iteriere über alle Keys und scrape die Daten
        for issue_key in issue_keys:
            logger.info(f"--- Starte Verarbeitung für: {issue_key} ---")
            issue_url = f"https://jira.telekom.de/browse/{issue_key}"

            issue_data = scraper.extract_and_save_issue_data(issue_url, issue_key)

            if issue_data:
                json_path = os.path.join('data', 'jira_issues', f"{issue_key}.json")
                logger.info(f"Scraping für {issue_key} erfolgreich. Gespeichert unter: {os.path.abspath(json_path)}")
            else:
                logger.warning(f"Fehler beim Scraping von {issue_key}. Es wurde keine Datei erstellt.")
            logger.info(f"--- Verarbeitung für {issue_key} abgeschlossen ---")

    except Exception as e:
        logger.error(f"Ein unerwarteter Fehler ist während des Batch-Laufs aufgetreten: {e}")

    finally:
        # 5. Stelle sicher, dass die Browser-Sitzung am Ende geschlossen wird
        if hasattr(scraper, 'login_handler') and scraper.login_handler and scraper.login_handler.driver:
            scraper.login_handler.close()
            logger.info("Browser-Sitzung wurde ordnungsgemäß beendet.")

if __name__ == "__main__":
    # Der Dateiname ist nun fest im Skript verankert
    input_file = 'business_value_epic_list.txt'

    # Starte den Prozess
    scrape_epics_from_file(input_file)
