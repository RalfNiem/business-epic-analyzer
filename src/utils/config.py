import os
from pathlib import Path
import platform
from dotenv import load_dotenv, find_dotenv

# Lade Umgebungsvariablen aus der .env-Datei im Projekt-Root
load_dotenv(find_dotenv())

# 1. Finde das Home-Verzeichnis des Benutzers (funktioniert auf Mac & Windows)
#    (ergibt /Users/A763630 auf macOS oder C:\Users\A763630 auf Windows)
home_dir = Path.home()

# 2. Definiere den systemspezifischen Teil des Pfades
system_name = platform.system()

if system_name == "Darwin":  # "Darwin" ist der Systemname für macOS
    # Relativer Pfad auf dem Mac
    relative_path = "Library/CloudStorage/OneDrive-DeutscheTelekomAG/_Dokumente/GitHub/business-epic-analyzer/data"
    JIRA_ISSUES_DIR = os.path.join(home_dir, "Library/CloudStorage/OneDrive-DeutscheTelekomAG/_Dokumente/GitHub/jira-loader/data/jira_issues")
    DB_PATH = os.path.join(home_dir, "Library/CloudStorage/OneDrive-DeutscheTelekomAG/_Dokumente/GitHub/jira-loader/data/jira_issues.sqlite")

elif system_name == "Windows":
    # Relativer Pfad auf Windows
    relative_path = "OneDrive - Deutsche Telekom AG/_Dokumente/GitHub/business-epic-analyzer/data"
    JIRA_ISSUES_DIR = os.path.join(home_dir, "OneDrive - Deutsche Telekom AG/_Dokumente/GitHub/jira-loader/data/jira_issues")
    DB_PATH = os.path.join(home_dir, "OneDrive - Deutsche Telekom AG/_Dokumente/GitHub/jira-loader/data/jira_issues.sqlite")

# 3. Setze den vollständigen Pfad zusammen
#    pathlib verwendet automatisch die korrekten Slashes für das OS
DATA_DIR = home_dir / relative_path

# Data subdirectories
HTML_REPORTS_DIR = os.path.join(DATA_DIR, 'html_reports')
ISSUE_TREES_DIR = os.path.join(DATA_DIR, 'issue_trees')
JSON_SUMMARY_DIR = os.path.join(DATA_DIR, 'json_summary')
PLOT_DIR = os.path.join(DATA_DIR, 'plots')

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_DIR = os.path.join(BASE_DIR, 'src')
LOGS_DIR = os.path.join(BASE_DIR, 'logs')
TEMPLATES_DIR = os.path.join(BASE_DIR, 'templates')
PROMPTS_DIR = os.path.join(BASE_DIR, 'prompts')

TOKEN_LOG_FILE = os.path.join(LOGS_DIR, "token_usage.jsonl")
ISSUE_LOG_FILE = os.path.join(LOGS_DIR, "failed_issues.log")

# Ensure directories exist
for directory in [LOGS_DIR, JIRA_ISSUES_DIR, HTML_REPORTS_DIR, ISSUE_TREES_DIR, JSON_SUMMARY_DIR]:
    os.makedirs(directory, exist_ok=True)

# Template file
EPIC_HTML_TEMPLATE = os.path.join(TEMPLATES_DIR, 'epic-html_template.html')

# LLM Models
# zulässige Modelle: DeepSeek-R1-0528, gpt-oss-120b, Llama-3.3-70B-Instruct, Mistral-Small-3.2-24B-Instruct-2506
#LLM_MODEL_SUMMARY = "Mistral-Small-3.2-24B-Instruct-2506"
LLM_MODEL_SUMMARY = "DeepSeek-R1-0528"
#LLM_MODEL_HTML_GENERATOR = "Mistral-Small-3.2-24B-Instruct-2506"
LLM_MODEL_HTML_GENERATOR = "gpt-oss-120b"
#LLM_MODEL_TIME_CREEP = "Mistral-Small-3.2-24B-Instruct-2506"
LLM_MODEL_TIME_CREEP = "gpt-oss-120b"
LLM_MODEL_BUSINESS_VALUE = "gpt-oss-120b"
LLM_MODEL_TRANSLATOR = "gpt-oss-120b"

# Default Flags
DEFAULT_SCRAPE_HTML = 'check'
SCRAPER_CHECK_DAYS = 1  # lädt nur dann ein Jira Issue wenn es älter als x Tage ist

# Credentials
JIRA_EMAIL ="ralf.niemeyer@telekom.de"

# NEU: Maximale Anzahl an Zeilen für die Haupt-Logdatei
MAX_LOG_ITEMS = 100000

# Konfiguration für die Jira Tree Ansichten
MAX_JIRA_TREE_CONTEXT_SIZE = 30

JIRA_TREE_MANAGEMENT_LIGHT = {
    "Business Initiative": ["realized_by", "child"],
    "Business Epic": ["realized_by", "child"],
    "Portfolio Epic": ["realized_by", "child"],
}

JIRA_TREE_MANAGEMENT = {
    "Business Initiative": ["realized_by", "child"],
    "Business Epic": ["realized_by", "child"],
    "Portfolio Epic": ["realized_by", "child"],
    "Initiative": ["realized_by", "child"]
}

JIRA_TREE_FULL = {
    "Business Initiative": ["realized_by", "child"],
    "Business Epic": ["realized_by", "child"],
    "Portfolio Epic": ["realized_by", "child"],
    "Initiative": ["realized_by", "child"],
    "Epic": ["issue_in_epic", "realized_by"],
}
