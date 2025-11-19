"""
Modul zur Übersetzung von HTML-Berichten mittels einer Batch-Strategie.

Diese Datei enthält die Klasse `HtmlTranslator`, die darauf spezialisiert ist,
HTML-Dateien mit Fachjargon aus der Telekommunikations- und IT-Branche präzise
von Deutsch nach Englisch zu übersetzen. Sie nutzt eine Batch-Verarbeitung, um
Effizienz und Übersetzungsqualität zu maximieren.
"""
import os
import sys
import logging
import json
from bs4 import BeautifulSoup, NavigableString
from typing import Any # <-- NEU: Importiere 'Any' für Flexibilität

# Stellt sicher, dass die übergeordneten utils-Module gefunden werden
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.config import HTML_REPORTS_DIR
# WIR IMPORTIEREN BEIDE, um flexibel zu bleiben, oder nur den DnaBotClient
#from utils.azure_ai_client import AzureAIClient 
from utils.dna_bot_client import DnaBotClient # <-- NEU
from utils.token_usage_class import TokenUsage

# System-Prompt für die Batch-Verarbeitung mit JSON
SYSTEM_PROMPT_TRANSLATOR = """
You are an expert translator specializing in the Telecommunications and IT sectors.
Your task is to translate a batch of German text snippets into professional, domain-specific English.
You will receive a JSON object with a key "texts_to_translate", which contains a list of objects, each with an "id" and "text".
You MUST return a JSON object with a single key "translations", containing a list of objects with the corresponding "id" and the translated "text".
It is crucial that you accurately translate technical terms, jargon, and business concepts.
Do NOT translate technical identifiers like Jira keys (e.g., 'BEMABU-2365').
Your response must be a valid JSON object and nothing else.
"""

# Liste von Tags, deren Inhalt übersetzt werden soll
TRANSLATABLE_TAGS = ['p', 'h1', 'h2', 'h3', 'li', 'td', 'th', 'title', 'div', 'strong', 'b', 'em']

class HtmlTranslator:
    """
    Eine Klasse zur Übersetzung von HTML-Dateien unter Beibehaltung der Struktur.

    Verwendet eine Batch-Strategie, um alle relevanten Textinhalte und Attribute
    gebündelt an eine KI-API zu senden und die Ergebnisse anschließend wieder
    in das HTML-Dokument einzufügen.
    """
    def __init__(self, 
                 ai_client: Any,  # <-- GEÄNDERT (von AzureAIClient zu Any)
                 token_tracker: TokenUsage, 
                 model_name: str):
        """
        Initialisiert den HtmlTranslator.

        Args:
            ai_client (Any): Ein instanziierter Client (DnaBotClient oder AzureAIClient).
            token_tracker (TokenUsage): Eine Instanz zur Protokollierung des Token-Verbrauchs.
            model_name (str): Der Name des zu verwendenden Übersetzungsmodells.
        """
        self.ai_client = ai_client
        
        # --- ANPASSUNG FÜR DNA-BOT-CLIENT ---
        # Der DnaBotClient erwartet den Prompt zur Laufzeit,
        # der AzureAIClient beim Start. Wir speichern ihn lokal.
        self.system_prompt = SYSTEM_PROMPT_TRANSLATOR 
        
        # Falls es der alte AzureAIClient ist, setzen wir das Attribut weiterhin
        if isinstance(self.ai_client, AzureAIClient):
             self.ai_client.system_prompt = SYSTEM_PROMPT_TRANSLATOR
        # --- ENDE ANPASSUNG ---
             
        self.token_tracker = token_tracker
        self.model_name = model_name

    def translate_file(self, issue_key: str):
        """
        Übersetzt eine einzelne HTML-Berichtsdatei vom Deutschen ins Englische.

        Implementiert eine Batch-Strategie, um alle Textknoten und 'alt'-Attribute
        zu extrahieren, in einer einzigen Anfrage zu übersetzen und die Ergebnisse
        wieder an den ursprünglichen Positionen im HTML einzufügen.

        Args:
            issue_key (str): Der Jira-Key des Epics (z.B. "BEMABU-1410"), der als
                             Basis für die Dateinamen dient.
        """
        input_filename = f"{issue_key}_summary.html"
        output_filename = f"{issue_key}_summary_englisch.html"
        input_filepath = os.path.join(HTML_REPORTS_DIR, input_filename)
        output_filepath = os.path.join(HTML_REPORTS_DIR, output_filename)

        if not os.path.exists(input_filepath):
            logging.error(f"Eingabedatei für Übersetzung nicht gefunden: {input_filepath}")
            return

        logging.info(f"Lese und parse Datei für Übersetzung: {input_filename}")
        with open(input_filepath, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f, 'lxml')

        # --- PHASE 1: Alle zu übersetzenden Inhalte extrahieren ---
        nodes_to_translate = []
        texts_for_api = []

        # Extrahiere Textknoten
        for text_node in soup.find_all(string=True):
            if text_node.parent.name in ['script', 'style'] or not text_node.strip():
                continue
            if text_node.parent.name in TRANSLATABLE_TAGS:
                original_text = text_node.strip()
                if original_text:
                    node_id = len(nodes_to_translate)
                    nodes_to_translate.append({"id": node_id, "type": "text", "node": text_node})
                    texts_for_api.append({"id": node_id, "text": original_text})

        # Extrahiere 'alt'-Attribute von Bildern
        for img_tag in soup.find_all('img', alt=True):
            original_alt = img_tag['alt'].strip()
            if original_alt:
                node_id = len(nodes_to_translate)
                nodes_to_translate.append({"id": node_id, "type": "attribute", "node": img_tag, "attr_name": "alt"})
                texts_for_api.append({"id": node_id, "text": original_alt})

        if not texts_for_api:
            logging.warning(f"Keine Texte zur Übersetzung in {input_filename} gefunden.")
            return

        logging.info(f"{len(texts_for_api)} Elemente zur Batch-Übersetzung extrahiert.")

        # --- PHASE 2: Einzelner API-Aufruf mit allen Texten ---
        try:
            api_payload = {"texts_to_translate": texts_for_api}
            user_prompt_json = json.dumps(api_payload, ensure_ascii=False, indent=2)

            # --- ANPASSUNG FÜR DNA-BOT-CLIENT ---
            response = self.ai_client.completion(
                model_name=self.model_name,
                user_prompt=user_prompt_json,
                system_prompt=self.system_prompt, # <-- HINZUGEFÜGT
                temperature=0.1,
                max_tokens=4096,
                response_format={"type": "json_object"}
            )
            # --- ENDE ANPASSUNG ---

            # Token-Tracking
            # Wir prüfen beide Antwortstrukturen (Objekt-Attribut 'usage' oder Dict-Key 'usage')
            usage_data = None
            if hasattr(response, 'usage'): # Für OpenAI-Objekte
                usage_data = response.usage
            elif isinstance(response, dict) and 'usage' in response: # Für DnaBotClient-Dicts
                usage_data = response['usage']

            if usage_data:
                # Prüfen, ob usage_data ein Objekt oder ein Dict ist
                if isinstance(usage_data, dict):
                    prompt_tokens = usage_data.get('prompt_tokens', 0)
                    completion_tokens = usage_data.get('completion_tokens', 0)
                    total_tokens = usage_data.get('total_tokens', 0)
                else: # Annahme: Objekt (wie bei OpenAI)
                    prompt_tokens = getattr(usage_data, 'prompt_tokens', 0)
                    completion_tokens = getattr(usage_data, 'completion_tokens', 0)
                    total_tokens = getattr(usage_data, 'total_tokens', 0)

                self.token_tracker.log_usage(
                    model=self.model_name,
                    input_tokens=prompt_tokens,
                    output_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    task_name="html_translation",
                    entity_id=issue_key
                )

            # --- PHASE 3: Antwort verarbeiten und Inhalte wieder einfügen ---
            # Der 'text'-Schlüssel ist bei beiden Clients identisch
            translated_data = json.loads(response['text'] if isinstance(response, dict) else response.choices[0].message.content)
            translations = translated_data.get("translations", [])

            if len(translations) != len(nodes_to_translate):
                logging.warning(f"Anzahl der Übersetzungen ({len(translations)}) stimmt nicht mit Originaltexten ({len(nodes_to_translate)}) überein!")

            for item in translations:
                item_id = item.get("id")
                translated_text = item.get("text", "").strip()
                if 0 <= item_id < len(nodes_to_translate):
                    target = nodes_to_translate[item_id]
                    if target['type'] == 'text':
                        target['node'].replace_with(NavigableString(translated_text))
                    elif target['type'] == 'attribute':
                        target['node'][target['attr_name']] = translated_text

        except json.JSONDecodeError:
            logging.error("Fehler beim Parsen der JSON-Antwort von der API.", exc_info=True)
            # Anpassung, um die Antwort aus beiden möglichen Strukturen zu holen
            raw_response_text = response.get('text', 'Keine Antwort') if isinstance(response, dict) else getattr(response.choices[0].message, 'content', 'Keine Antwort')
            logging.debug(f"Erhaltene Antwort: {raw_response_text}")
            return
        except Exception as e:
            logging.error(f"Ein Fehler ist während des API-Aufrufs aufgetreten: {e}", exc_info=True)
            return

        # Speichere die übersetzte HTML-Datei
        with open(output_filepath, "w", encoding='utf-8') as f:
            f.write(str(soup))

        logging.info(f"Übersetzte Datei erfolgreich gespeichert: {output_filepath}\n")