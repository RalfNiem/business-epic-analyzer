import json
import re
from utils.logger_config import logger

class LLMJsonParser:
    """
        Ein robuster Parser zum Extrahieren und Reparieren von JSON-Daten aus LLM-Antworten.

        Da Large Language Models (LLMs) oft Text, Markdown-Blöcke oder "Reasoning"-Tags
        (<think>...</think>) um das eigentliche JSON herum generieren oder invalides JSON
        erzeugen (z.B. verschachtelte Anführungszeichen), versucht dieser Parser,
        das JSON intelligent zu isolieren und syntaktische Fehler automatisch zu beheben.

        Hauptfunktionen:
        ----------------
        * **Extraktion:** Findet JSON in Markdown-Codeblöcken (```json ... ```) oder
        sucht nach geschweiften Klammern im Text.
        * **Bereinigung:** Entfernt <think>-Blöcke und Text vor/nach dem JSON.
        * **Reparatur:** Fixiert häufige JSON-Fehler wie:
            - Fehlende Anführungszeichen bei Keys.
            - Einfache statt doppelte Anführungszeichen.
            - Verschachtelte doppelte Anführungszeichen in Strings.
            - Trailing Commas.
        """
    def __init__(self):
        self.json_pattern = re.compile(r'```json\s*(.*?)\s*```', re.DOTALL)
        self.curly_pattern = re.compile(r'(\{.*\})', re.DOTALL)

    def extract_and_parse_json(self, text):
        """
        Extracts and parses JSON from LLM output text.

        Args:
            text (str): The text output from an LLM that might contain JSON

        Returns:
            dict: The parsed JSON data or empty dict if parsing fails
        """
        # Method 1: Try direct parsing (in case it's already valid JSON)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.info(f"Fehler bei Methode 1: Try direct parsing")
            pass

        # Method 2: Look for JSON code blocks
        json_match = self.json_pattern.search(text)
        if json_match:
            json_text = json_match.group(1)
            try:
                return json.loads(json_text)
            except json.JSONDecodeError:
                logger.info(f"Fehler bei Methode 2: Look for JSON code blocks")
                pass

        # If all methods fail, attempt to clean and fix the JSON
        return self._clean_and_fix_json(text)

    def _clean_and_fix_json(self, text):
        """
        Attempts to clean and fix malformed JSON.
        Diese Methode extrahiert jetzt auch intelligent JSON aus
        umgebendem Text (z.B. <think>... Blöcken).
        """
        try:
            # --- MODIFIZIERTE EXTRAKTIONS-LOGIK ---
            # Sucht nach dem JSON-Objekt *NACH* einem möglichen <think>...</think> Block.

            # Finde das Ende des letzten </think> Blocks
            last_think_tag_end = text.rfind('</think>')

            search_start_index = 0
            if last_think_tag_end != -1:
                # Setze den Startpunkt der Suche auf *nach* dem Tag
                search_start_index = last_think_tag_end + len('</think>')

            # Finde das erste '{' NACH dem <think> Block
            start_idx = text.find('{', search_start_index)

            # Finde die letzte '}' im gesamten Text
            end_idx = text.rfind('}')
            # --- ENDE MODIFIZIERTE LOGIK ---

            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                json_text = text[start_idx:end_idx+1]

                # Common fixes for malformed JSON
                # 1. Fix unquoted keys
                json_text = re.sub(r'([{,])\s*([a-zA-Z0-9_]+)\s*:', r'\1"\2":', json_text)

                # 2. Fix single quoted strings
                json_text = re.sub(r"'([^']*)'", r'"\1"', json_text)

                # 3. Fix nested double quotes in strings (NEW FIX)
                # This pattern looks for strings already in double quotes that contain internal double quotes
                # First, identify all string values in the JSON
                string_pattern = re.compile(r'"((?:[^"\\]|\\.)*)"')

                # This function will be called for each match and will fix inner double quotes
                def fix_inner_quotes(match):
                    inner_content = match.group(1)
                    # Replace unescaped double quotes with single quotes
                    # Skip quotes that are already escaped
                    fixed_content = re.sub(r'(?<!\\)"([^"]*)"', r"'\1'", inner_content)
                    return f'"{fixed_content}"'

                # Apply the fix to all string values in the JSON
                json_text = string_pattern.sub(fix_inner_quotes, json_text)

                # 4. Remove trailing commas
                json_text = re.sub(r',\s*([}\]])', r'\1', json_text)

                # Try to parse the fixed JSON
                try:
                    return json.loads(json_text)
                except json.JSONDecodeError as e:
                    # If still failing, attempt more aggressive fixes
                    logger.info(f"Fehler bei 'def _clean_and_fix_json()''")
                    return self._apply_aggressive_fixes(json_text)
        except Exception:
            pass

        # Return empty dict if all attempts fail
        logger.error("JSON Text konnte nach _clean_and_fix_json nicht gelesen werden.")
        return {}


    def _apply_aggressive_fixes(self, json_text):
        """
        Apply more aggressive fixes when standard ones fail.

        Args:
            json_text (str): Partially fixed JSON text

        Returns:
            dict: Parsed JSON or empty dict if all attempts fail
        """
        try:
            # 1. Alternative approach for nested quotes: Convert all nested double quotes to escaped double quotes
            # This pattern finds double quotes inside string literals
            string_blocks = re.findall(r'"([^"]*)"', json_text)
            for block in string_blocks:
                if '"' in block:
                    # There are unescaped double quotes inside this string
                    fixed_block = block.replace('"', r'\"')
                    json_text = json_text.replace(f'"{block}"', f'"{fixed_block}"')

            # 2. Handle array of strings with internal quotes
            # For arrays like ["text with "quotes" inside", "normal text"]
            array_pattern = re.compile(r'\[(.*?)\]', re.DOTALL)
            for array_match in array_pattern.finditer(json_text):
                array_content = array_match.group(1)
                if '"' in array_content:
                    # Split by commas, but be careful about commas inside quotes
                    items = []
                    in_quotes = False
                    current_item = ""
                    for char in array_content:
                        if char == '"' and (len(current_item) == 0 or current_item[-1] != '\\'):
                            in_quotes = not in_quotes
                        if char == ',' and not in_quotes:
                            items.append(current_item.strip())
                            current_item = ""
                        else:
                            current_item += char
                    if current_item:
                        items.append(current_item.strip())

                    # Fix each item
                    fixed_items = []
                    for item in items:
                        if item.startswith('"') and item.endswith('"'):
                            # This is a string item
                            inner_content = item[1:-1]
                            if '"' in inner_content:
                                # Fix inner quotes
                                inner_content = inner_content.replace('"', "'")
                                fixed_items.append(f'"{inner_content}"')
                            else:
                                fixed_items.append(item)
                        else:
                            fixed_items.append(item)

                    # Replace the array
                    new_array = "[" + ", ".join(fixed_items) + "]"
                    json_text = json_text.replace(array_match.group(0), new_array)

            # Try to parse again
            return json.loads(json_text)
        except Exception:
            logger.error(f"JSON Text konnte mit keiner json_parser Methode fehlerfrei gelesen werden")
            return {}

# Example usage
def parse_llm_json(result_text):
    parser = LLMJsonParser()
    result_json = parser.extract_and_parse_json(result_text)
    return result_json

# Test with the problematic JSON
if __name__ == "__main__":
    # Das fehlerhafte JSON mit inneren doppelten Anführungszeichen
    problematic_json_1 = """
    {
      "epicId": "BEB2B-413",
      "title": "SNP Mobile Underlay",
      "acceptance_criteria": [
        "Implementierung der Produkterweiterung des "Mobile Underlay" in der neuen IT-Chain.",
        "Prozessmodellierung in der Neutralität aller Overlay-Lösungen."
      ]
    }
    """

    # Fehlerhaftes JSON aus paste.txt
    problematic_json_2 = """
    {
      "epicId": "BEB2B-413",
      "title": "SNP Mobile Underlay",
      "ziele": {
        "gesamtziel": "Das Gesamtziel des Business Epics 'SNP Mobile Underlay' (BEB2B-413) ist die Realisierung und IT-unterstützende Bereitstellung einer primären mobilen Netzanschaltung als Underlay-Lösung für Kundenstandorte. Dies soll durch die Implementierung einer neuen mobilen Einwahloption erreicht werden, die als kostenoptimierte und hochverfügbare Alternative zu Festanschaltungen dient, sowohl integriert in Overlay-Lösungen als auch als Stand-Alone Produkt. Der Fokus liegt initial auf Deutschland. Die Umsetzung dieses Ziels ist bis zum 2025-09-30 geplant, mit einem angestrebten Produktlaunch in Q1.2026.",
        "einzelziele": []
      },
      "businessValue": {
        "businessImpact": {
          "skala": 20,
          "beschreibung": "Die mobile Primäranschaltung soll die Versorgungslücke des Underlay für alle Standorte mit fehlender oder Perfomance-schwachen festnetzbasierten Zuführungen, schließen. Vollumfängliche, leistungssteigernde Angebote in Kundennetzausschreibungen erhöhen den Produktumsatz des Underlay & Overlay Der Einsatz von Mobile Underlay in Infrastrukturschwachen Region schafft eine kurzfristige Lösung bis zum späteren Netzausbau von festnetzbasierten Internet-Anschlüssen."
        },
        "strategicEnablement": {
          "skala": 20,
          "beschreibung": "Keine Informationen verfügbar"
        },
        "timeCriticality": {
          "skala": 13,
          "beschreibung": "Einführung mittel- und langfristige "Underlay-Produkte" der Deutschen Telekom als Grundlage des Lösungsgeschäfts in der Internet-basierten Standort-vernetzung. Wettbewerbsfähige Internet-basiertes Access Produkt im Wettbewerb zu AT&T, British Telecom (BT), Orange oder Colt. Produkterweiterung mit mobilen Lösungen zur Erweiterung des Portfolios und Pflichterfüllung der Kundennetzanforderungen. Aktuelle Angebotsbeteiligung bei Cordes & Graefe für eine mobile Standortvernetzung von >800 Standorten. Im Fall eines Zuschlages wäre der Rollout ab März 2025. Weitere verbindliche Kundenaufträge bestehen bereits seitens Iduna Nova."
        }
      },
      "funktionen": [],
      "acceptance_criteria": [
        "Implementierung der Produkterweiterung des "Mobile Underlay" in der neuen IT-Chain (im FMO, d.h. Future Mode Operation) unter Berücksichtigung von PANDA , ServiceNow und weiteren.",
        "Prozessmodellierung in der Neutralität aller Overlay-Lösungen der DTAG, inkl. SIM-Kartenverwaltung und Rollout.",
        "Etablierung des technischen Betriebs und Netzmanagement analog der festnetzgebundenen Internet-Anschaltungen.",
        "Gate-Freigaben im GK-Board"
      ],
      "domainsAndITApplications": [
        "Reporting DOM0200 Magenta Business",
        "ISP Betrieb Netz (Mobile)",
        "Betrieb Kundenmanagement (Betrieb)",
        "Order 2 Fulfillment DOM0204",
        "Offer 2 Order DOM0204",
        "Lead 2 Offer DOM0204",
        "Fulfillment 2 Billing DOM0202",
        "Assurance DOM0200 T-GIP",
        "PANDA",
        "ServiceNow"
      ],
      "abhängigkeitenUndRisiken": [
        "Abhängigkeit von der "GK-Freigabe" für den Flatrate-Mobilfunk Tarif.",
        "Abhängigkeit von Gate-Freigaben im GK-Board (Gate 1-2 bereits erteilt am 04.12.2024).",
        "Risiko/Abhängigkeit: Das Business Epic hat ein geplantes Ende am 2025-09-30 (Q3_25), während der Produktlaunch erst für Q1.2026 geplant ist. Dies deutet auf nachfolgende Aktivitäten oder eine potenzielle Zeitplan-Diskrepanz hin.",
        "Abhängigkeit von der erfolgreichen Implementierung in der neuen IT-Chain (FMO) unter Berücksichtigung von PANDA, ServiceNow und weiteren Systemen.",
        "Abhängigkeit von der Prozessmodellierung für Overlay-Lösungen, SIM-Kartenverwaltung und Rollout.",
        "Abhängigkeit von der Etablierung des technischen Betriebs und Netzmanagements.",
        "Der Status 'Review' des Business Epics deutet darauf hin, dass es sich noch nicht in der aktiven Umsetzung befindet."
      ],
      "zeitplan": {
        "umsetzungsstart": "2025-07-01",
        "umsetzungsende": "2025-09-30",
        "fixVersions": [
          "Q3_25"
        ],
        "meilensteine": []
      }
    }
    """


    parser = LLMJsonParser()
    result = parser.extract_and_parse_json(problematic_json_2)

    print("Parsing erfolgreich:", bool(result))
    print("Geparste Daten:", json.dumps(result, indent=2, ensure_ascii=False))
