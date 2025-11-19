"""
    Ein Client für die Interaktion mit der DNA-Bot LLM API (TARDIS/Stargate).

    Dieser Client verwaltet die Kommunikation mit dem LLM-Backend und abstrahiert die
    Komplexität der Authentifizierung sowie des Anfrage-Managements. Er unterstützt
    sowohl synchrone Abfragen als auch Streaming-Antworten und beinhaltet eine
    spezifische Logik zur Bereinigung von Modell-Ausgaben.

    Hauptfunktionen:
    ----------------
    * **Authentifizierung:** Automatisches Abrufen und Erneuern von OAuth2-Access-Tokens
        (Client Credentials Flow) basierend auf Umgebungsvariablen.
    * **Streaming & Synchron:** Unterstützung für `stream=True` (Generator) und
        `stream=False` (Dictionary) bei Chat-Completions.
    * **Output-Bereinigung:** Automatische Entfernung von internen "Reasoning"-Blöcken
        (eingeschlossen in `<think>...</think>` Tags), sowohl im Stream als auch im
        Gesamttext, um dem Endnutzer nur die finale Antwort zu präsentieren.
    * **Fehlerbehandlung:** Logging von Fehlern und Weiterleitung von API-Exceptions.

    Voraussetzungen (Umgebungsvariablen):
    -------------------------------------
    Der Client erwartet zwingend folgende Einträge in der `.env` Datei oder den System-Umgebungsvariablen:
    * `DNABOT_TOKEN_URL`: URL zum Token-Endpunkt.
    * `DNABOT_CHAT_ENDPOINT`: URL zum Chat-Completion-Endpunkt.
    * `DNABOT_CLIENT_ID`: Die Client-ID für die Authentifizierung.
    * `DNABOT_CLIENT_SECRET`: Das Client-Secret.

Beispiel:
    ---------
    >>> from utils.dna_bot_client import DnaBotClient
    >>> client = DnaBotClient()

    >>> # 1. Synchroner Aufruf (Standard)
    >>> result = client.completion(
    ...     model_name="gpt-oss-120b",
    ...     user_prompt="Fasse diesen Text zusammen.",
    ...     temperature=0.1
    ... )
    >>> print(result["text"])
    >>> print(result["usage"])

    >>> # 2. Streaming Aufruf
    >>> stream = client.completion(
    ...     model_name="gpt-oss-120b",
    ...     user_prompt="Schreibe ein Gedicht.",
    ...     stream=True
    ... )
    >>> for chunk in stream:
    ...     print(chunk, end="", flush=True)

"""
import requests
import os
import re
import time
import json
import sys
import logging
import urllib3
from requests.exceptions import RequestException
from utils.logger_config import logger

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())

class DnaBotClient:
    """
    Ein Client für die DNA-Bot LLM API (TARDIS/Stargate).
    Unterstützt nun auch Streaming.
    """
    TOKEN_EXPIRATION_BUFFER = 30

    def __init__(self, verify_ssl: bool = True):
        self.TOKEN_URL = os.getenv("DNABOT_TOKEN_URL")
        self.CHAT_ENDPOINT = os.getenv("DNABOT_CHAT_ENDPOINT")
        self.CLIENT_ID = os.getenv("DNABOT_CLIENT_ID")
        self.CLIENT_SECRET = os.getenv("DNABOT_CLIENT_SECRET")

        if not self.CLIENT_ID or not self.CLIENT_SECRET:
            logger.critical("DNABOT_CLIENT_ID und DNABOT_CLIENT_SECRET müssen gesetzt sein!")
            raise ValueError("Fehlende DNA-Bot-Credentials.")

        self.verify_ssl = verify_ssl
        self.access_token = None
        self.token_expires_at = 0
        self.last_stream_usage = {}
        logger.info("DnaBotClient initialisiert.")

    def _get_access_token(self):
        logger.info(f"Hole neuen Access Token von {self.TOKEN_URL}...")
        token_payload = {
            "grant_type": "client_credentials",
            "client_id": self.CLIENT_ID,
            "client_secret": self.CLIENT_SECRET,
        }
        try:
            response = requests.post(
                self.TOKEN_URL, data=token_payload, verify=self.verify_ssl
            )
            response.raise_for_status()
            token_data = response.json()
            self.access_token = token_data.get("access_token")
            expires_in = token_data.get("expires_in", 300)
            self.token_expires_at = time.time() + expires_in - self.TOKEN_EXPIRATION_BUFFER

            if not self.access_token:
                raise ValueError("Konnte Access Token nicht extrahieren.")
            logger.info("Access Token erfolgreich erhalten.")
        except RequestException as e:
            logger.error(f"Fehler beim Holen des Access Tokens: {e}")
            self.access_token = None
            self.token_expires_at = 0
            raise

    def _ensure_token_valid(self):
        if time.time() >= self.token_expires_at:
            self._get_access_token()


    def _clean_reasoning_text(self, text: str) -> str:
        """Entfernt den <think>...</think> Block aus dem Text."""
        if not text:
            return text
        # Regex, um den gesamten Block von <think> bis </think> (inklusive Tags und Inhalt) zu entfernen.
        # re.DOTALL stellt sicher, dass der Match auch über Zeilenumbrüche geht.
        cleaned_text = re.sub(r'<think>.*?<\/think>', '', text, flags=re.DOTALL | re.IGNORECASE).strip()
        return cleaned_text


    def completion(self,
                   model_name: str,
                   user_prompt: str,
                   system_prompt: str = None,
                   max_tokens: int = 4096,
                   temperature: float = 0.1,
                   response_format: dict = None,
                   stream: bool = False):
        """
        Führt einen Chat-Completions-Aufruf durch.

        Returns:
            dict: Bei stream=False (Standard): {"text": "...", "usage": {...}}
            generator: Bei stream=True: Ein Iterator, der Text-Fragmente (Strings) liefert.
        """
        self._ensure_token_valid()

        final_system_prompt = system_prompt
        if response_format and response_format.get("type") == "json_object":
            json_suffix = " IMPORTANT: The output must be exclusively in JSON format!"
            final_system_prompt = (final_system_prompt or "") + json_suffix

        messages = []
        if final_system_prompt:
            messages.append({"role": "system", "content": final_system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        chat_headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # Payload Aufbau (angepasst an Ihr Schema)
        chat_payload = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream
        }

        # Wenn Streaming aktiv ist, Usage-Stats anfordern (lt. Schema)
        if stream:
            chat_payload["stream_options"] = {"include_usage": True}

        logger.info(f"Sende Anfrage (Modell: {model_name}, Stream: {stream})...")

        try:
            # Wichtig: stream=True auch im requests.post Aufruf
            response = requests.post(
                self.CHAT_ENDPOINT,
                headers=chat_headers,
                json=chat_payload,
                verify=self.verify_ssl,
                timeout=180,
                stream=stream
            )

            response.raise_for_status()

            if stream:
                self.last_stream_usage = {}
                return self._process_stream(response)
            else:
                # Klassische synchrone Verarbeitung
                data = response.json()
                raw_text = data["choices"][0]["message"]["content"]

                # NEU: Filterung für den synchronen Modus
                text_response = self._clean_reasoning_text(raw_text)

                return {
                    "text": text_response,
                    "usage": data.get("usage", {})
                }

        except RequestException as e:
            logger.error(f"Fehler bei Anfrage: {e}")
            if e.response is not None:
                logger.error(f"API-Antwort: {e.response.text}")
            raise e

    def _process_stream(self, response):
        """Generator für Streaming-Chunks. Ignoriert <think>...</think> Blöcke."""

        # NEU: Zustandsvariable zur Erkennung des Reasoning-Blocks
        in_reasoning_block = False

        for line in response.iter_lines():
            if line:
                decoded = line.decode('utf-8').strip()
                if decoded.startswith("data: "):
                    data_str = decoded[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)

                        # Usage Stats im letzten Chunk speichern
                        if "usage" in chunk and chunk["usage"]:
                            self.last_stream_usage = chunk["usage"]

                        # Content Delta extrahieren
                        if "choices" in chunk and chunk["choices"]:
                            delta = chunk["choices"][0].get("delta", {})
                            content = delta.get("content", "")

                            if content:
                                # Start-Tag prüfen (robuster gegen Whitespace)
                                if content.strip().startswith("<think>"):
                                    in_reasoning_block = True
                                    continue

                                # Ende-Tag prüfen
                                if "</think>" in content:
                                    in_reasoning_block = False
                                    # Yield den Teil NACH </think>
                                    content_after_think = content.split("</think>", 1)[-1]
                                    if content_after_think:
                                        yield content_after_think
                                    continue

                                # Regulären Inhalt yielden, wenn nicht im Block
                                if not in_reasoning_block:
                                    yield content

                    except json.JSONDecodeError:
                        continue
