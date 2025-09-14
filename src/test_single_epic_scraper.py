import argparse
import os
import sys

# Fügt das Projekt-Root-Verzeichnis zum Python-Pfad hinzu
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.jira_scraper import JiraScraper
# BENÖTIGTE IMPORTE
from utils.azure_ai_client import AzureAIClient
from utils.prompt_loader import load_prompt_template
from utils.config import JIRA_EMAIL, LLM_MODEL_BUSINESS_VALUE
from utils.logger_config import logger

def test_single_epic(issue_key: str):
    """
    Führt einen gezielten Scraping-Lauf für ein einzelnes Business Epic durch.
    """
    logger.info(f"Starte Test-Scraping für einzelnes Epic: {issue_key}")
    issue_url = f"https://jira.telekom.de/browse/{issue_key}"

    # --- KORREKTUR: Initialisiere den AzureAIClient für den Fallback ---
    business_value_system_prompt = load_prompt_template("business_value_prompt.yaml", "system_prompt")
    ai_client = AzureAIClient(system_prompt=business_value_system_prompt)
    # --- ENDE DER KORREKTUR ---

    scraper = JiraScraper(
        url=issue_url,
        email=JIRA_EMAIL,
        model=LLM_MODEL_BUSINESS_VALUE,
        scrape_mode='true',
        azure_client=ai_client  # WICHTIG: Übergebe den initialisierten Client
    )

    try:
        if not scraper.login():
            logger.error("Login fehlgeschlagen. Der Test kann nicht fortgesetzt werden.")
            return

        logger.info(f"Extrahiere Daten für {issue_key} von der URL: {issue_url}")
        issue_data = scraper.extract_and_save_issue_data(issue_url, issue_key)

        if issue_data:
            json_path = os.path.join('data', 'jira_issues', f"{issue_key}.json")
            logger.info(f"Scraping für {issue_key} erfolgreich abgeschlossen.")
            logger.info(f"Die Datei wurde hier gespeichert: {os.path.abspath(json_path)}")
        else:
            logger.error(f"Fehler beim Scraping von {issue_key}. Es wurde keine JSON-Datei erstellt.")

    finally:
        if scraper.login_handler:
            scraper.login_handler.close()
            logger.info("Browser-Sitzung wurde ordnungsgemäß beendet.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Test-Skript zum Scrapen eines einzelnen Business Epics."
    )
    parser.add_argument(
        "--issue",
        type=str,
        required=True,
        help="Der Jira-Key des Business Epics, das gescraped werden soll."
    )
    args = parser.parse_args()

    test_single_epic(args.issue)
