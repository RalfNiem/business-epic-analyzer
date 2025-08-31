import os
from dotenv import load_dotenv, find_dotenv

# Lade Umgebungsvariablen aus der .env-Datei im Projekt-Root
load_dotenv(find_dotenv())

# Base paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_DIR = os.path.join(BASE_DIR, 'src')
LOGS_DIR = os.path.join(BASE_DIR, 'logs')
DATA_DIR = os.path.join(BASE_DIR, 'data')

TEMPLATES_DIR = os.path.join(BASE_DIR, 'templates')
PROMPTS_DIR = os.path.join(BASE_DIR, 'prompts')

# Data subdirectories
JIRA_ISSUES_DIR = os.path.join(DATA_DIR, 'jira_issues')
HTML_REPORTS_DIR = os.path.join(DATA_DIR, 'html_reports')
ISSUE_TREES_DIR = os.path.join(DATA_DIR, 'issue_trees')
JSON_SUMMARY_DIR = os.path.join(DATA_DIR, 'json_summary')
PLOT_DIR = os.path.join(DATA_DIR, 'plots')

TOKEN_LOG_FILE = os.path.join(LOGS_DIR, "token_usage.jsonl")
ISSUE_LOG_FILE = os.path.join(LOGS_DIR, "failed_issues.log")

# Ensure directories exist
for directory in [LOGS_DIR, JIRA_ISSUES_DIR, HTML_REPORTS_DIR, ISSUE_TREES_DIR, JSON_SUMMARY_DIR]:
    os.makedirs(directory, exist_ok=True)

# Template file
EPIC_HTML_TEMPLATE = os.path.join(TEMPLATES_DIR, 'epic-html_template.html')

# LLM Models
LLM_MODEL_HTML_GENERATOR = "gpt-4.1-mini"
LLM_MODEL_BUSINESS_VALUE = "gpt-4.1"
LLM_MODEL_TIME_CREEP = "o3-mini"
#LLM_MODEL_SUMMARY = "o3-mini"
LLM_MODEL_SUMMARY = "gpt-oss-120b"
LLM_MODEL_TRANSLATOR = "gpt-4.1"

# Default Flags
DEFAULT_SCRAPE_HTML = 'check'
SCRAPER_CHECK_DAYS = 14  # lädt nur dann ein Jira Issue wenn es älter als x Tage ist

# Credentials
JIRA_EMAIL ="ralf.niemeyer@telekom.de"

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
