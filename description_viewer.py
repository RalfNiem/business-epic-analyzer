"""
Streamlit Utility: Jira Description Viewer (description_viewer.py)

Dieses Skript implementiert eine einfache Webanwendung (Utility-App)
mit Streamlit, die als Werkzeug zur Fehlersuche und Validierung dient.

Der Hauptzweck besteht darin, den 'description'-Schlüssel aus einer
lokal gespeicherten Jira-Issue-JSON-Datei zu extrahieren, das
Jira-spezifische Markup (z.B. h1., *bold*, {code}) in Standard-Markdown
umzuwandeln und das formatierte Ergebnis anzuzeigen.

Dies ermöglicht es Entwicklern und Anwendern, schnell zu überprüfen,
wie eine Beschreibung nach der Transformation aussieht, die von den
Lader-Skripten (wie jira_api_loader) angewendet wird.

## Kernfunktionalität

1.  **Streamlit UI**: Stellt eine zweispaltige Weboberfläche bereit.
    * **Linke Spalte**: Enthält ein Texteingabefeld, in das der Benutzer
        einen Jira-Key eingeben kann (z.B. "TF-5491").
    * **Rechte Spalte**: Zeigt die formatierte Ausgabe oder Fehlermeldungen an.
2.  **Datenladung**: Basierend auf der Eingabe versucht das Skript, die
    entsprechende Datei zu laden (z.B. `data/jira_issues/TF-5491.json`).
3.  **Fehlerbehandlung**: Fängt gängige Fehler ab, wie z.B. wenn die Datei
    nicht gefunden wird (`FileNotFoundError`) oder wenn sie kein gültiges
    JSON enthält (`json.JSONDecodeError`).
4.  **Transformation**: Nutzt die `convert_jira_to_markdown`-Funktion, um
    die extrahierte Beschreibung zu konvertieren.
5.  **Anzeige**: Rendert den konvertierten Markdown-Text in der rechten
    Spalte der App.

## Hilfsfunktion: `convert_jira_to_markdown(text)`

* **Zweck**: Eine "statische" Funktion, die einen String mit Jira-Markup
    als Eingabe nimmt und einen String mit Standard-Markdown-Markup zurückgibt.
* **Technik**: Verwendet eine Reihe von regulären Ausdrücken (regex), um
    gängige Jira-Tags zu ersetzen:
    * `h1.` ... `h6.` -> `#` ... `######`
    * `*strong*` -> `**strong**`
    * `_emphasis_` -> `*emphasis*`
    * `{code}` / `{noformat}` -> ````
    * `* list` -> `- list`
    * `# list` -> `1. list`
    * `[link|text]` -> `[text](link)`
    * `{panel}` -> `> ` (Blockquote)
    * Entfernt `{color}`-Tags.

## Verwendung

Das Skript wird über Streamlit von der Kommandozeile aus gestartet:

```bash
streamlit run description_viewer.py
"""

import streamlit as st
import json
import os
import re

# Die Konvertierungslogik bleibt exakt dieselbe
def convert_jira_to_markdown(text: str) -> str:
    """
    Wandelt Jira-spezifisches Markup in Standard-Markdown um.
    """
    if not isinstance(text, str):
        return "Fehler: Die Beschreibung ist kein gültiger Text."

    # Ersetzungen für gängiges Jira-Markup
    text = re.sub(r'<linebreak>', '\n', text)
    text = re.sub(r'^\s*h([1-6])\.\s*(.*)', r'#\1 \2', text, flags=re.MULTILINE)
    text = re.sub(r'(?<!\w)\*([^\s*][^*]*?)\*(?!\w)', r'**\1**', text)
    text = re.sub(r'(?<!\w)_([^\s_][^_]*?)_(?!\w)', r'*\1*', text)
    text = re.sub(r'\{code[^}]*\}', '```', text, flags=re.IGNORECASE)
    text = re.sub(r'\{noformat\}', '```', text, flags=re.IGNORECASE)
    text = re.sub(r'^\s*\*\s+', '- ', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*#\s+', '1. ', text, flags=re.MULTILINE)
    text = re.sub(r'\[([^|\]]+?)\|([^\]]+?)\]', r'[\2](\1)', text)
    text = re.sub(r'\[(https?://[^\]]+)\]', r'<\1>', text)
    text = re.sub(r'\{panel[^\}]*\}', '\n> ', text, flags=re.IGNORECASE)
    text = re.sub(r'\{panel\}', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\{color:[^}]+\}(.*?)\{color\}', r'\1', text, flags=re.IGNORECASE)
    text = re.sub(r'\+([^+]+)\+', r'\1', text)
    text = re.sub(r'\|', ' | ', text)

    return text

# --- Streamlit App ---

# Seitentitel und Layout konfigurieren
st.set_page_config(page_title="Jira Description Viewer", layout="wide")

st.title("Jira Description Viewer 🔎")

# Zweispaltiges Layout erstellen
col1, col2 = st.columns([1, 2]) # Linke Spalte ist halb so breit wie die rechte

# Linke Spalte für die Eingabe
with col1:
    st.header("Eingabe")
    jira_key_input = st.text_input(
        "Geben Sie hier den Jira Key ein:",
        placeholder="z.B. TF-5491"
    ).strip().upper()

# Rechte Spalte für die Ausgabe
with col2:
    st.header("Formatierte Beschreibung")

    if jira_key_input:
        file_path = os.path.join("data", "jira_issues", f"{jira_key_input}.json")

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            description = data.get('description')

            if description:
                # Jira-Text in Markdown umwandeln
                markdown_output = convert_jira_to_markdown(description)
                # Formatiertes Markdown in der App anzeigen
                st.markdown(markdown_output, unsafe_allow_html=True)
            else:
                st.error(f"Die Datei für '{jira_key_input}' enthält keinen 'description'-Schlüssel.")

        except FileNotFoundError:
            st.error(f"Fehler: Die Datei für den Key '{jira_key_input}' wurde nicht gefunden.")
        except json.JSONDecodeError:
            st.error(f"Fehler: Die Datei für '{jira_key_input}' ist keine gültige JSON-Datei.")
    else:
        st.info("Bitte geben Sie links einen Jira Key ein, um die Beschreibung anzuzeigen.")
