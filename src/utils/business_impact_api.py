"""
Business Impact API Module (DnaBot Version)
===========================================

This module provides an AI-powered capability to analyze a given text description,
separate the core narrative from business value information, and structure that
business value data into a predefined schema.

It uses the DnaBotClient exclusively. Since DnaBot does not support native
Pydantic parsing via the API, this module injects the JSON schema into the
system prompt and uses the LLMJsonParser to clean and validate the output.

CLI Usage:
    python src/utils/business_impact_api.py --issue BEB2B-1234
"""

import json
import os
import sys
import argparse
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, ValidationError
from dotenv import load_dotenv, find_dotenv

# --- PATH SETUP FOR DIRECT EXECUTION ---
# Fügt 'src' zum Suchpfad hinzu, damit 'from utils...' funktioniert,
# wenn das Skript direkt ausgeführt wird.
if __name__ == "__main__":
    current_dir = os.path.dirname(os.path.abspath(__file__)) # src/utils
    src_dir = os.path.dirname(current_dir) # src
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

from utils.dna_bot_client import DnaBotClient
from utils.json_parser import LLMJsonParser
from utils.prompt_loader import load_prompt_template
from utils.logger_config import logger
from utils.config import JIRA_ISSUES_DIR, LLM_MODEL_BUSINESS_VALUE, TOKEN_LOG_FILE
from utils.token_usage_class import TokenUsage

_ = load_dotenv(find_dotenv())

# --- Pydantic Models (Unverändert) ---
class BusinessImpact(BaseModel):
    """Data model for the financial and operational impact of a task."""
    scale: int = Field(..., description="The overall impact scale (0-5).")
    revenue: Optional[str] = Field("", description="Details on revenue generation.")
    cost_saving: Optional[str] = Field("", description="Details on cost savings.")
    risk_loss: Optional[str] = Field("", description="Details on mitigating financial risks or losses.")
    justification: Optional[str] = Field("", description="A narrative explaining the business impact.")

class StrategicEnablement(BaseModel):
    """Data model for the strategic value and alignment of a task."""
    scale: int = Field(..., description="The overall strategic importance scale (0-5).")
    risk_minimization: Optional[str] = Field("", description="Details on minimizing non-financial risks.")
    strat_enablement: Optional[str] = Field("", description="Details on how this enables strategic initiatives.")
    justification: Optional[str] = Field("", description="A narrative explaining the strategic value.")

class TimeCriticality(BaseModel):
    """Data model for the urgency and time-based factors of a task."""
    scale: int = Field(..., description="The overall time criticality scale (0-5).")
    time: Optional[str] = Field("", description="The frequency or time horizon (e.g., 'Daily', 'Q3 2025').")
    justification: Optional[str] = Field("", description="A narrative explaining why this is time-critical.")

class BusinessValue(BaseModel):
    """A container for all business value dimensions."""
    business_impact: BusinessImpact
    strategic_enablement: StrategicEnablement
    time_criticality: TimeCriticality

class AIResponse(BaseModel):
    """The top-level Pydantic model that the AI is instructed to populate."""
    cleaned_description: str = Field(..., description="The description text, cleaned of any business value information.")
    business_value: BusinessValue


def get_empty_business_value_dict() -> dict:
    """Returns a default empty business value structure as a dictionary."""
    empty_bv = BusinessValue(
        business_impact=BusinessImpact(scale=0),
        strategic_enablement=StrategicEnablement(scale=0),
        time_criticality=TimeCriticality(scale=0)
    )
    return {
        "description": "",
        "business_value": empty_bv.model_dump()
    }

def process_description(description_text: str, model: str, token_tracker, ai_client: DnaBotClient) -> dict:
    """
    Analyzes a description using the DnaBotClient to extract structured business value.
    """
    if not description_text:
        return get_empty_business_value_dict()

    # 1. Prompts laden und vorbereiten
    prompt_template = load_prompt_template("business_impact_prompt.yaml", "user_prompt_template")
    user_prompt = prompt_template.format(description_text=description_text)

    # Schema für den System-Prompt generieren
    json_schema = json.dumps(AIResponse.model_json_schema(), indent=2)
    system_prompt = f"""
    You are a Business Analyst. Extract event information and business value from the description.
    Separate the core text from the business value data.

    IMPORTANT: You must output a valid JSON object that strictly conforms to this schema:
    {json_schema}
    """

    try:
        # 2. API-Aufruf an DnaBot
        response = ai_client.completion(
            model_name=model,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=0.1,
            response_format={"type": "json_object"}
        )

        raw_text = response['text']

        # 3. Token Logging
        if token_tracker and 'usage' in response:
            usage = response['usage']
            input_t = usage.get('prompt_tokens', 0)
            output_t = usage.get('completion_tokens', 0)
            total_t = usage.get('total_tokens', 0)
            token_tracker.log_usage(
                model=model,
                input_tokens=input_t,
                output_tokens=output_t,
                total_tokens=total_t,
                task_name="business_impact_dnabot"
            )

        # 4. Parsing & Bereinigung
        parser = LLMJsonParser()
        parsed_dict = parser.extract_and_parse_json(raw_text)

        if not parsed_dict:
            logger.warning("LLMJsonParser returned empty dict. Returning empty BV fallback.")
            return {"description": description_text, "business_value": get_empty_business_value_dict()["business_value"]}

        # 5. Validierung gegen Pydantic
        ai_response_object = AIResponse.model_validate(parsed_dict)

        return {
            "description": ai_response_object.cleaned_description.strip(),
            "business_value": ai_response_object.business_value.model_dump(),
        }

    except (ValidationError, json.JSONDecodeError) as e:
        logger.error(f"Validation/JSON Error in process_description: {e}")
        return {"description": description_text, "business_value": get_empty_business_value_dict()["business_value"]}

    except Exception as e:
        logger.error(f"Unexpected error in process_description: {e}")
        return {"description": description_text, "business_value": get_empty_business_value_dict()["business_value"]}


# --- MAIN EXECUTION BLOCK (CLI) ---
if __name__ == "__main__":
    # 1. Argumente parsen
    parser = argparse.ArgumentParser(description="Extract Business Impact for a specific Jira Issue.")
    parser.add_argument("--issue", required=True, help="The Jira Issue Key (e.g., BEB2B-1234)")
    args = parser.parse_args()

    issue_key = args.issue

    # 2. Issue-Daten laden
    json_path = os.path.join(JIRA_ISSUES_DIR, f"{issue_key}.json")
    if not os.path.exists(json_path):
        print(f"❌ Fehler: Datei nicht gefunden: {json_path}")
        print(f"   Bitte stellen Sie sicher, dass das Issue '{issue_key}' bereits gescraped wurde.")
        sys.exit(1)

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"❌ Fehler beim Lesen der JSON-Datei: {e}")
        sys.exit(1)

    # 3. Issue-Typ prüfen
    issue_type = data.get("issue_type")
    valid_types = ["Business Epic", "Business Initiative"]

    if issue_type not in valid_types:
        print(f"⚠️  Ignoriert: Issue {issue_key} ist vom Typ '{issue_type}'.")
        print(f"    Erwartet: {', '.join(valid_types)}")
        sys.exit(0)

    print(f"✅ Analysiere {issue_type}: {issue_key}")

    # 4. Beschreibung holen
    description = data.get("description", "")
    if not description:
        print("⚠️  Warnung: Keine Beschreibung im Issue gefunden. Analyse wird leer sein.")

    # 5. Analyse ausführen
    try:
        client = DnaBotClient()
        tracker = TokenUsage(log_file_path=TOKEN_LOG_FILE)

        # Modell aus Config oder Fallback
        model = LLM_MODEL_BUSINESS_VALUE if LLM_MODEL_BUSINESS_VALUE else "gpt-oss-120b"

        print(f"   Sende Anfrage an DnaBot ({model})...")
        result = process_description(description, model, tracker, client)

        # 6. Ergebnis ausgeben
        bv = result.get("business_value", {})
        cleaned_desc = result.get("description", "")

        print("\n" + "="*60)
        print(f"   BUSINESS IMPACT REPORT: {issue_key}")
        print("="*60)

        print(json.dumps(bv, indent=2, ensure_ascii=False))

        print("\n" + "-"*60)
        print("   BEREINIGTE BESCHREIBUNG (Vorschau):")
        print("-"*60)
        print(cleaned_desc[:300] + "..." if len(cleaned_desc) > 300 else cleaned_desc)
        print("="*60 + "\n")

    except Exception as e:
        print(f"❌ Fehler bei der Analyse: {e}")
        sys.exit(1)
