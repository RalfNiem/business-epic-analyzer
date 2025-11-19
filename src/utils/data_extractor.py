"""
Modul zur Extraktion strukturierter Daten von JIRA-Issue-Webseiten.

Dieses Modul bietet die Funktionalität, Daten von JIRA-Webseiten mittels
Selenium zu parsen und zu extrahieren. Es ist darauf ausgelegt, eine Vielzahl
von Feldern und Beziehungen aus JIRA-Issues zu verarbeiten, darunter Titel,
Beschreibungen, Status, Verantwortliche, Story Points, Akzeptanzkriterien,
Anhänge sowie verschiedene Arten von Issue-Verknüpfungen.

Die Hauptklasse, DataExtractor, implementiert robuste Extraktionsmethoden mit
Fallback-Strategien, um verschiedene JIRA-UI-Layouts und Konfigurationen
abzudecken. Eine Schlüsselfunktion ist die Vereinheitlichung aller
gefundenen Beziehungen (z.B. 'is realized by', 'child issues', 'issues in epic')
in einer einzigen, konsistenten Liste namens `issue_links`.
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from bs4 import BeautifulSoup
import re
from utils.logger_config import logger


class DataExtractor:
    """
    Klasse zur Extraktion strukturierter Daten von JIRA-Issue-Webseiten.

    Diese Klasse ist darauf spezialisiert, eine Vielzahl von Feldern und
    Datenelementen von JIRA-Seiten mithilfe von Selenium zu extrahieren. Sie
    implementiert robuste Extraktionsmethoden mit mehrstufigen Fallback-
    Strategien, um eine zuverlässige Datenextraktion über verschiedene JIRA-
    Konfigurationen und UI-Layouts hinweg zu gewährleisten.

    Kernfunktionen:
    - Extrahiert Metadaten (Schlüssel, Titel, Status, Typ, Priorität, etc.).
    - Erfasst Beschreibungs- und "Business Scope"-Texte.
    - Verarbeitet optional "Business Value"-Daten über einen externen
      KI-Dienst.
    - Extrahiert Akzeptanzkriterien und "Fix Versions".
    - Erfasst Anhanginformationen (Dateien, Bilder).
    - Identifiziert und vereinheitlicht verwandte Issues ('realized by' Links,
      Child-Issues und 'Issues in epic') in einer einzigen `issue_links`-Liste.
    - Unterstützt Zeitdaten (z.B. Target Start/End).

    Die Klasse verwendet zuerst primäre Extraktionsmethoden und greift dann auf
    alternative Strategien zurück, falls die primären Methoden fehlschlagen.
    Dies stellt eine maximale Datenextraktion sicher, auch wenn die UI-Struktur
    von JIRA variiert.
    """

    def __init__(self, description_processor=None, model="claude-3-7-sonnet-latest", token_tracker=None, azure_client=None):
        """
        Initialisiert den DataExtractor.

        Args:
            description_processor (callable, optional): Eine Funktion, die
                Beschreibungstexte verarbeitet, um z.B. strukturierte
                Business-Value-Daten zu extrahieren. Falls None, wird dieser
                Schritt übersprungen.
            model (str, optional): Das KI-Modell, das für den
                `description_processor` verwendet wird.
            token_tracker (TokenUsage, optional): Ein Objekt zur Verfolgung der
                API-Token-Nutzung.
            azure_client (AzureAIClient, optional): Der Client für die
                Kommunikation mit dem KI-Dienst.
        """
        self.description_processor = description_processor
        self.model = model
        self.token_tracker = token_tracker
        self.azure_client = azure_client


    def _extract_story_points(self, driver):
        """
        Extrahiert die Story Points von der Seite mit mehreren Fallback-Strategien.

        Die Methode prüft zuerst den primären Selektor (via @title-Attribut) und
        greift bei einem Fehlschlag auf einen zweiten Selektor zurück, der nach
        dem sichtbaren Label-Text sucht. Innerhalb beider Strategien wird geprüft,
        ob der Wert in einem <input>-Feld oder als reiner Text vorliegt.
        """
        try:
            # PRIMÄRE STRATEGIE: Suche via @title='Story Points'
            value_container = driver.find_element(By.XPATH, "//strong[@title='Story Points']/following-sibling::div[1]")
        except NoSuchElementException:
            try:
                # FALLBACK-STRATEGIE: Suche via <label>-Text
                value_container = driver.find_element(By.XPATH, "//strong/label[contains(text(), 'Story Points')]/ancestor::strong/following-sibling::div[1]")
            except NoSuchElementException:
                # Wenn beide Strategien fehlschlagen, existiert das Feld nicht.
                return "n/a"

        # Wenn ein value_container gefunden wurde, extrahiere den Wert.
        try:
            # Prüfe, ob sich der Wert in einem <input>-Feld befindet
            input_element = value_container.find_element(By.TAG_NAME, "input")
            return input_element.get_attribute("value")
        except NoSuchElementException:
            # Wenn kein <input>, nimm den sichtbaren Text des Containers
            return value_container.text.strip()


    @staticmethod
    def _find_child_issues(driver):
        """
        Sucht nach Child Issues in der dedizierten Tabelle auf der Seite.
        """
        child_issues = []

        try:
            # Suche nach der Child-Issue-Tabelle
            child_table = driver.find_element(By.XPATH, "//table[contains(@class, 'jpo-child-issue-table')]")

            # Finde alle Links in der Tabelle
            child_links = child_table.find_elements(By.XPATH, ".//a[contains(@href, '/browse/')]")

            if child_links:
                logger.info(f"Gefunden: {len(child_links)} Child Issues")

                # Verarbeite jeden Child-Issue-Link
                for child_link in child_links:
                    # Extrahiere die URL, die den verlässlichen Key enthält
                    child_href = child_link.get_attribute("href")

                    # Extrahiere den Key direkt aus der URL anstatt aus dem sichtbaren Text
                    match = re.search(r'/browse/([A-Z][A-Z0-9]*-\d+)', child_href)
                    if not match:
                        continue

                    child_key = match.group(1)


                    logger.info(f"Child Issue gefunden: {child_key}")

                    # Versuche, den Summary-Text zu finden (falls vorhanden)
                    try:
                        # Finde das übergeordnete tr-Element
                        parent_row = child_link.find_element(By.XPATH, "./ancestor::tr")

                        # Suche nach der Zelle mit der Zusammenfassung (normalerweise die 2. oder 3. Zelle)
                        summary_cells = parent_row.find_elements(By.XPATH, "./td")
                        summary_text = ""

                        if len(summary_cells) >= 2:
                            # Die 2. Zelle enthält oft die Zusammenfassung
                            summary_text = summary_cells[1].text.strip()

                    except Exception as e:
                        summary_text = ""
                        logger.debug(f"Konnte Summary für Child Issue {child_key} nicht extrahieren")

                    # Füge die Informationen zur child_issues-Liste hinzu
                    child_issue_item = {
                        "key": child_key,
                        "title": child_key,  # Title ist oft nur der Key
                        "summary": summary_text,
                        "url": child_href
                    }

                    child_issues.append(child_issue_item)

        except Exception as e:
            logger.info(f"Keine Child Issues gefunden")

        return child_issues


    @staticmethod
    def _extract_business_scope(driver):
        """
        Extrahiert den "Business Scope"-Text aus der Jira-Seite.

        Implementiert mehrere Fallback-Mechanismen, um den Text auch aus
        komplexeren HTML-Strukturen (z.B. 'flooded' divs) zuverlässig zu
        extrahieren. Diese Version ist korrigiert, um das Feld basierend auf
        seinem sichtbaren Label-Text anstatt eines 'title'-Attributs zu finden.
        """
        business_scope = ""

        try:
            # --- KORREKTUR START ---
            # Der neue XPath sucht nach einem <strong>-Tag, das den Text "Business Scope"
            # enthält, und wählt dann das direkt folgende <div>-Geschwisterelement aus.
            # Dies ist robuster als die Suche nach einem 'title'-Attribut oder einer 'for'-ID.
            business_scope_div = driver.find_element(By.XPATH,
                "//strong[contains(., 'Business Scope')]/following-sibling::div[1]")
            # --- KORREKTUR ENDE ---


            # Robustere Extraktion des Textes - versuche verschiedene Wege
            # 1. Versuche zuerst, direkt den Text zu holen
            business_scope = business_scope_div.text.strip()

            # 2. Wenn der Text leer ist, versuche es mit flooded divs
            if not business_scope:
                # Suche nach allen div-Elementen mit Klasse 'flooded' innerhalb des Haupt-divs
                flooded_divs = business_scope_div.find_elements(By.XPATH, ".//div[contains(@class, 'flooded')]")

                # Sammle den Text aus allen gefundenen Elementen
                texts = []
                for div in flooded_divs:
                    div_text = div.text.strip()
                    if div_text:
                        texts.append(div_text)

                # Füge alle gefundenen Texte zusammen
                business_scope = "\n".join(texts)

            # Wenn immer noch leer, extrahiere den HTML-Inhalt und versuche es manuell zu parsen
            if not business_scope:
                html_content = business_scope_div.get_attribute('innerHTML')
                # Entferne HTML-Tags mit einem einfachen Ansatz (für komplexere Fälle könnte BeautifulSoup verwendet werden)
                import re
                business_scope = re.sub(r'<[^>]*>', ' ', html_content)
                business_scope = re.sub(r'\s+', ' ', business_scope).strip()

            if business_scope:
                logger.info(f"Business Scope gefunden: {business_scope[:50]}...")
            else:
                logger.info("Business Scope gefunden, aber Text ist leer")

        except Exception as e:
            logger.info(f"Business Scope konnte nicht extrahiert werden")

        return business_scope


    @staticmethod
    def _extract_business_value_from_table(soup: BeautifulSoup) -> dict | None:
        """
        Extrahiert den Business Value direkt aus den HTML-Tabellen in der Beschreibung.
        (Finale, robuste Version mit korrekter Sub-Header-Suche)
        """
        try:
            # Schritt 1: Finden der Überschrift (Anker)
            bv_header = soup.find(lambda tag: tag.name == 'h3' and 'Business Value / Cost of Delay' in tag.get_text())

            if not bv_header:
                return None

            # Schritt 2: Verarbeiten der Tabelleninhalte
            # Hilfsfunktion für sauberen Text
            def get_cell_text(table, row_idx, col_idx, table_name):
                try:
                    cell = table.find_all('tr')[row_idx].find_all(['td', 'th'])[col_idx]
                    text = cell.get_text(separator='\n', strip=True)
                    return text
                except IndexError:
                    return ""

            impact_header = soup.find(lambda tag: tag.name == 'p' and 'Business Impact' in tag.get_text())
            strategy_header = soup.find(lambda tag: tag.name == 'p' and 'Strategic Enablement/Risk Reduktion' in tag.get_text())
            time_header = soup.find(lambda tag: tag.name == 'p' and 'Time Criticality' in tag.get_text())

            if not (impact_header and strategy_header and time_header):
                return None

            impact_table = impact_header.find_next_sibling('div', class_='table-wrap').find('table')
            strategy_table = strategy_header.find_next_sibling('div', class_='table-wrap').find('table')
            time_table = time_header.find_next_sibling('div', class_='table-wrap').find('table')

            # Verarbeitung der einzelnen Tabellen
            business_impact = {
                "scale": int(get_cell_text(impact_table, 1, 0, "Business Impact") or 0),
                "revenue": get_cell_text(impact_table, 1, 1, "Business Impact"),
                "cost_saving": "",
                "risk_loss": get_cell_text(impact_table, 1, 2, "Business Impact"),
                "justification": get_cell_text(impact_table, 1, 3, "Business Impact")
            }
            strategic_enablement = {
                "scale": int(get_cell_text(strategy_table, 1, 0, "Strategic Enablement") or 0),
                "risk_minimization": get_cell_text(strategy_table, 1, 1, "Strategic Enablement"),
                "strat_enablement": get_cell_text(strategy_table, 1, 2, "Strategic Enablement"),
                "justification": get_cell_text(strategy_table, 1, 3, "Strategic Enablement")
            }
            time_criticality = {
                "scale": int(get_cell_text(time_table, 1, 0, "Time Criticality") or 0),
                "time": get_cell_text(time_table, 1, 1, "Time Criticality"),
                "justification": get_cell_text(time_table, 1, 2, "Time Criticality")
            }

            logger.info("-> Business Value Tabelleninhalte erfolgreich verarbeitet.")

            # Schritt 3: Bereinigung der Beschreibung
            bv_header.extract()
            impact_header.find_next_sibling('div', class_='table-wrap').extract()
            strategy_header.find_next_sibling('div', class_='table-wrap').extract()
            time_header.find_next_sibling('div', class_='table-wrap').extract()
            impact_header.extract()
            strategy_header.extract()
            time_header.extract()

            return {
                "business_impact": business_impact,
                "strategic_enablement": strategic_enablement,
                "time_criticality": time_criticality
            }

        except Exception as e:
            logger.error(f"Ein unerwarteter Fehler ist beim Parsen der Business-Value-Tabelle aufgetreten: {e}", exc_info=True)
            return None


    def extract_issue_data(self, driver, issue_key):
        """
        Extrahiert umfassende Daten eines Jira-Issues in ein strukturiertes Format.
        (Finale Version mit korrekter Extraktion von Business Value UND Nutzenstatement)
        """
        data = {
            "key": issue_key,
            "issue_type": "",
            "title": "",
            "status": "",
            "resolution": "",
            "story_points": "n/a",
            "description": "",
            "business_value": {},
            "assignee": "",
            "priority": "",
            "target_start": "",
            "target_end": "",
            "fix_versions": [],
            "acceptance_criteria": [],
            "components": [],
            "labels": [],
            "issue_links": [],
            "attachments": [],
        }

        # Title
        try:
            title_elem = driver.find_element(By.XPATH, "//div[@id='summary-val']/h2")
            data["title"] = title_elem.text.strip()
            logger.info(f"Titel gefunden: {data['title']}")
        except Exception as e:
            logger.info(f"Titel nicht gefunden: {e}")

        # Issue Type (muss vor der Description-Logik extrahiert werden)
        try:
            issue_type_container = driver.find_element(By.XPATH, "//span[@id='type-val']")
            data["issue_type"] = issue_type_container.text.strip()
            logger.info(f"Issue Type gefunden: {data['issue_type']}")
        except Exception as e:
            logger.error(f"Issue Type konnte nicht extrahiert werden: {e}")

        # --- START DER FINALEN LOGIK FÜR DESCRIPTION & BUSINESS VALUE ---
        try:
            desc_elem = driver.find_element(By.XPATH, "//div[contains(@id, 'description-val')]")
            description_html = desc_elem.get_attribute('innerHTML')
            soup = BeautifulSoup(description_html, 'lxml')

            # Versuche, den Business Value direkt aus der Tabelle zu extrahieren
            extracted_bv = self._extract_business_value_from_table(soup)

            if extracted_bv:
                # "Wenn-Ja"-Pfad: Tabelle wurde gefunden und geparst
                data["business_value"] = extracted_bv
                logger.info("Business Value direkt aus HTML-Tabelle extrahiert.")

                # NEUE LOGIK: Suche gezielt nach dem "Nutzenstatement"-Panel
                nutzen_panel = soup.find(lambda tag: tag.name == 'div' and "Business Epic Nutzenstatement" in tag.get_text())
                if nutzen_panel:
                    panel_content = nutzen_panel.find('div', class_='panelContent')
                    if panel_content:
                        data["description"] = panel_content.get_text(separator='\n', strip=True)
                        logger.info("Nutzenstatement wurde als Beschreibung extrahiert.")
                else:
                    logger.warning("Kein 'Nutzenstatement'-Panel gefunden. Beschreibung könnte unvollständig sein.")
                    data["description"] = soup.get_text(separator="\n", strip=True)
            else:
                # "Wenn-Nein"-Pfad (Fallback zur KI)
                logger.info("Keine strukturierte Business-Value-Tabelle gefunden. Nutze KI-Fallback.")
                full_description_text = desc_elem.text
                if data["issue_type"] == 'Business Epic' and self.description_processor:
                    try:
                        processed_text = self.description_processor(
                            full_description_text, self.model, self.token_tracker, self.azure_client
                        )
                        data["description"] = processed_text.get('description', full_description_text)
                        data["business_value"] = processed_text.get('business_value', {})
                        logger.info("Business Value per KI-Aufruf extrahiert und Beschreibung bereinigt.")
                    except Exception as bv_error:
                        logger.error(f"Fehler bei der Verarbeitung des Business Value via KI: {bv_error}")
                        data["description"] = full_description_text
                else:
                    data["description"] = full_description_text

            # --- NEUE LOGIK FÜR ACCEPTANCE CRITERIA ---
            # Zuerst das dedizierte Feld durchsuchen
            try:
                label_elem = driver.find_element(By.XPATH, "//label[text()='Acceptance Criteria:']")
                field_id = label_elem.get_attribute("for")
                acceptance_field = driver.find_element(By.XPATH, f"//div[@id='{field_id}-val']")
                # Hier extrahieren wir den rohen HTML-Inhalt, um Listen und Umbrüche zu erhalten
                ac_html = acceptance_field.get_attribute('innerHTML')
                ac_soup = BeautifulSoup(ac_html, 'lxml')

                # Extrahiere die Kriterien und filtere den Standard-Platzhaltertext heraus
                placeholder_text = "In Scope/Akzeptanzkriterien: siehe Ausfüllhilfe"
                criteria_list = [
                    item.get_text(strip=True) for item in ac_soup.find_all(['p', 'li'])
                    if item.get_text(strip=True) and placeholder_text not in item.get_text()
                ]

                if criteria_list:
                    data["acceptance_criteria"].extend(criteria_list)
                    logger.info(f"{len(criteria_list)} Acceptance Criteria aus dem dedizierten Feld extrahiert und bereinigt.")
            except Exception:
                 logger.info("Dediziertes 'Acceptance Criteria'-Feld nicht gefunden oder leer.")

            # Wenn das dedizierte Feld leer war, suche im Beschreibungs-Panel
            if not data["acceptance_criteria"]:
                logger.info("Suche nach Akzeptanzkriterien als Fallback im Beschreibungstext...")
                # Finde den spezifischen Header-Tag (<b> oder <strong>), um die Suche einzugrenzen
                ac_header = soup.find(lambda tag: tag.name in ['b', 'strong'] and 'In Scope / Akzeptanzkriterien' in tag.get_text())

                if ac_header:
                    # Navigiere vom Header zum übergeordneten Panel-Container
                    ac_panel = ac_header.find_parent('div', class_='panel')
                    if ac_panel:
                        panel_content = ac_panel.find('div', class_='panelContent')
                        if panel_content:
                            # Extrahiere die Kriterien und filtere den Standard-Platzhaltertext heraus
                            placeholder_text = "In Scope/Akzeptanzkriterien: siehe Ausfüllhilfe"
                            criteria_list = [
                                item.get_text(strip=True) for item in panel_content.find_all(['p', 'li'])
                                if item.get_text(strip=True) and placeholder_text not in item.get_text()
                            ]

                            if criteria_list:
                                data["acceptance_criteria"].extend(criteria_list)
                                logger.info(f"{len(criteria_list)} Acceptance Criteria aus dem 'In Scope'-Panel extrahiert und bereinigt.")
            # --- ENDE NEUE LOGIK FÜR ACCEPTANCE CRITERIA ---

        except Exception as e:
            logger.info(f"Beschreibung, BV und AC konnten nicht extrahiert werden: {e}")


        # Business Scope extrahieren und zur Description hinzufügen:
        try:
            business_scope = DataExtractor._extract_business_scope(driver)
            if business_scope:
                if data["description"]:
                    data["description"] += "\n\nBusiness Scope:\n" + business_scope
                else:
                    data["description"] = "Business Scope:\n" + business_scope
                logger.info(f"Business Scope zur Description hinzugefügt ({len(business_scope)} Zeichen)")
        except Exception as e:
            logger.info(f"Business Scope konnte nicht extrahiert werden")

        # Status
        try:
            status_button = driver.find_element(By.XPATH, "//a[contains(@class, 'aui-dropdown2-trigger') and contains(@class, 'opsbar-transitions__status-category_')]")
            status_span = status_button.find_element(By.XPATH, ".//span[@class='dropdown-text']")
            data["status"] = status_span.text
            logger.info(f"Status gefunden: {status_span.text}")
        except Exception as e:
            logger.info(f"Status nicht gefunden")

        # Story Points
        data["story_points"] = self._extract_story_points(driver)
        logger.info(f"Story Points direkt extrahiert: {data['story_points']}")

        # Assignee
        try:
            assignee_elem = driver.find_element(By.XPATH, "//span[contains(@id, 'assignee') or contains(@class, 'assignee')]")
            data["assignee"] = assignee_elem.text
            logger.info(f"Assignee gefunden: {assignee_elem.text}")
        except Exception as e:
            logger.info(f"Assignee nicht gefunden")

        # Priority
        try:
            priority_elem = driver.find_element(By.XPATH, "//span[@id='priority-val']")
            data["priority"] = priority_elem.text.strip()
            logger.info(f"Priority gefunden: {data['priority']}")
        except Exception as e:
            logger.info(f"Priority nicht gefunden")

        # Resolution
        try:
            resolution_elem = driver.find_element(By.XPATH, "//span[@id='resolution-val']")
            data["resolution"] = resolution_elem.text.strip()
            logger.info(f"Resolution gefunden: {data['resolution']}")
        except Exception as e:
            logger.info(f"Resolution nicht gefunden (normal bei 'Unresolved' Issues)")

        # fixVersion Daten
        try:
           fix_version_span = driver.find_element(By.XPATH, "//span[@id='fixVersions-field']")
           fix_version_links = fix_version_span.find_elements(By.XPATH, ".//a[contains(@href, '/issues/')]")
           for link in fix_version_links:
               link_html = link.get_attribute("outerHTML")
               match = re.search(r'>([^<]+)</a>', link_html)
               if match:
                   version = match.group(1).strip()
                   if version and version not in data["fix_versions"]:
                       data["fix_versions"].append(version)
           logger.info(f"{len(data['fix_versions'])} Fix Versions gefunden: {', '.join(data['fix_versions'])}")
        except Exception as e:
           logger.info(f"Fix Versions nicht gefunden")

        # Target Start und Target End Daten
        try:
            target_start_span = driver.find_element(By.XPATH, "//span[@data-name='Target start']")
            target_start_time = target_start_span.find_element(By.XPATH, ".//time[@datetime]")
            data["target_start"] = target_start_time.get_attribute("datetime")
            logger.info(f"Target Start-Datum gefunden: {data['target_start']}")
        except Exception as e:
            logger.info(f"Target Start-Datum nicht gefunden")
        try:
            target_end_span = driver.find_element(By.XPATH, "//span[@data-name='Target end']")
            target_end_time = target_end_span.find_element(By.XPATH, ".//time[@datetime]")
            data["target_end"] = target_end_time.get_attribute("datetime")
            logger.info(f"Target End-Datum gefunden: {data['target_end']}")
        except Exception as e:
            logger.info(f"Target End-Datum nicht gefunden")

        # Attachments
        try:
            attachments_list = driver.find_element(By.XPATH, "//ol[@id='attachment_thumbnails' and contains(@class, 'item-attachments')]")
            attachment_items = attachments_list.find_elements(By.XPATH, ".//li[contains(@class, 'attachment-content')]")
            for item in attachment_items:
                try:
                    download_url = item.get_attribute("data-downloadurl")
                    if download_url:
                        parts = download_url.split(":", 2)
                        if len(parts) >= 3:
                            attachment_item = {
                                "filename": parts[1], "url": parts[2], "mime_type": parts[0],
                                "size": item.find_element(By.XPATH, ".//dd[contains(@class, 'attachment-size')]").text.strip(),
                                "date": item.find_element(By.XPATH, ".//time[@datetime]").get_attribute("datetime")
                            }
                            data["attachments"].append(attachment_item)
                except Exception as item_error:
                    logger.info(f"Fehler beim Extrahieren eines Anhangs: {item_error}")
            logger.info(f"{len(data['attachments'])} Anhänge gefunden")
        except Exception as e:
            logger.info(f"Keine Anhänge gefunden")


        # Components
        try:
            components_container = driver.find_element(By.XPATH, "//span[@id='components-val']")
            component_links = components_container.find_elements(By.XPATH, ".//a[contains(@href, '/issues/')]")
            for comp_link in component_links:
                component_code = comp_link.text.strip()
                if component_code: data["components"].append({"code": component_code, "title": comp_link.get_attribute("title")})
            logger.info(f"{len(data['components'])} Components gefunden: {', '.join([comp['code'] for comp in data['components']])}")
        except Exception as e:
            logger.info(f"Keine Components gefunden")

        # Labels
        try:
            labels_container = driver.find_element(By.XPATH, "//div[contains(@class, 'labels-wrap')]")
            label_links = labels_container.find_elements(By.XPATH, ".//a[contains(@class, 'lozenge')]")
            for label_link in label_links:
                label_text = label_link.text.strip()
                if label_text:
                    data["labels"].append(label_text)
            if data["labels"]:
                logger.info(f"{len(data['labels'])} Labels gefunden: {', '.join(data['labels'])}")
            else:
                logger.info("Label-Container gefunden, aber keine Labels darin.")
        except Exception as e:
            logger.info(f"Keine Labels gefunden")

        # "is realized by" Links
        try:
            link_elements = driver.find_elements(By.XPATH,
                "//dl[contains(@class, 'links-list')]/dt[contains(text(), 'is realized by') or @title='is realized by']"
                "/..//a[contains(@class, 'issue-link')]")
            for link in link_elements:
                issue_key_attr = (link.get_attribute("data-issue-key") or link.text.strip()).replace('\u200b', '')
                summary_text = ""
                try:
                    parent_element = link.find_element(By.XPATH, "./ancestor::div[contains(@class, 'link-content')]")
                    summary_element = parent_element.find_element(By.XPATH, ".//span[contains(@class, 'link-summary')]")
                    summary_text = summary_element.text.strip()
                except:
                    pass
                link_item = {
                    "key": issue_key_attr, "title": link.text.strip(), "summary": summary_text,
                    "url": link.get_attribute("href"), "relation_type": "realized_by"
                }
                if not any(item["key"] == link_item["key"] for item in data["issue_links"]):
                    data["issue_links"].append(link_item)
            if link_elements:
                logger.info(f"{len(link_elements)} 'is realized by' Links zu 'issue_links' hinzugefügt.")
        except Exception as e:
            logger.info(f"'is realized by' Links konnten nicht gefunden werden")

        # Child Issues
        try:
            child_issues = DataExtractor._find_child_issues(driver)
            initial_link_count = len(data["issue_links"])
            for child in child_issues:
                if not any(item["key"] == child["key"] for item in data["issue_links"]):
                    child["relation_type"] = "child"
                    data["issue_links"].append(child)
            added_children = len(data["issue_links"]) - initial_link_count
            if added_children > 0:
                logger.info(f"{added_children} Child Issues zu 'issue_links' hinzugefügt.")
        except Exception as e:
             logger.info(f"Fehler bei der Verarbeitung von Child Issues")

        # "Issues in epic"
        try:
            wait = WebDriverWait(driver, 2)
            wait.until(EC.element_to_be_clickable((By.ID, "greenhopper-epics-issue-web-panel-label")))
            issue_table = driver.find_element(By.ID, "ghx-issues-in-epic-table")
            issue_rows = issue_table.find_elements(By.XPATH, ".//tr[contains(@class, 'issuerow')]")
            if issue_rows:
                logger.info(f"{len(issue_rows)} 'Issues in epic' in der Tabelle gefunden.")
                for row in issue_rows:
                    try:
                        key = row.get_attribute('data-issuekey')
                        if not any(item["key"] == key for item in data["issue_links"]):
                            url_element = row.find_element(By.XPATH, f".//a[@href='/browse/{key}']")
                            title_element = row.find_element(By.XPATH, ".//td[contains(@class, 'ghx-summary')]")
                            data["issue_links"].append({
                                "key": key, "title": title_element.text.strip(), "summary": title_element.text.strip(),
                                "url": url_element.get_attribute('href'), "relation_type": "issue_in_epic"
                            })
                    except Exception as row_error:
                        logger.warning(f"Konnte eine Zeile im 'Issues in epic'-Panel nicht parsen: {row_error}")
        except TimeoutException:
            logger.info("Abschnitt 'Issues in epic' nicht gefunden oder nicht rechtzeitig geladen.")
        except Exception as e:
            logger.info(f"Ein unerwarteter Fehler ist bei der Extraktion von 'Issues in epic' aufgetreten")

        return data


    def extract_activity_details(self, html_content):
        """
        Extrahiert und verarbeitet Aktivitätsdetails aus dem HTML-Inhalt.

        Diese Methode parst den Aktivitätsstrom (z.B. aus den "Verlauf" oder
        "Alle" Tabs), um eine chronologische Liste von Feldänderungen zu
        erstellen. Sie kann Aktionen, bei denen ein Benutzer mehrere Felder
        gleichzeitig ändert, korrekt in einzelne Events aufschlüsseln.

        Ein wesentlicher Teil der Funktionalität ist die Nachverarbeitung und
        Normalisierung der extrahierten Werte. So werden beispielsweise Werte
        für 'Status' oder 'Sprint' von Präfixen und IDs bereinigt, lange Texte
        wie 'Description' auf ein Kürzel '[...]' reduziert und Issue-Keys aus
        Feldern wie 'Epic Link' standardisiert. Dies gewährleistet saubere und
        konsistente Ausgabedaten für die weitere Analyse.
        """
        soup = BeautifulSoup(html_content, 'lxml')
        action_containers = soup.find_all('div', class_='actionContainer')

        extracted_data = []
        ignored_fields = ['Checklists', 'Remote Link', 'Link', 'Kommentar oder Erstellung']

        for container in action_containers:
            # Benutzer und Zeitstempel gelten für alle Änderungen in diesem Container
            user_name = "N/A"
            timestamp_iso = "N/A"

            details_block = container.find('div', class_='action-details')
            if not details_block:
                continue

            user_tag = details_block.find('a', class_='user-hover')
            if user_tag:
                user_name = user_tag.get_text(strip=True)

            time_tag = details_block.find('time', class_='livestamp')
            if time_tag:
                timestamp_iso = time_tag.get('datetime', 'N/A')

            body_block = container.find('div', class_='action-body')
            if body_block:
                # NEUE LOGIK: Finde alle Zeilen (tr) mit Änderungen
                change_rows = body_block.find_all('tr')
                for row in change_rows:
                    activity_name_tag = row.find('td', class_='activity-name')
                    if not activity_name_tag:
                        continue

                    activity_name = activity_name_tag.get_text(strip=True)
                    if activity_name in ignored_fields:
                        continue

                    # Roh-Werte extrahieren, um sie sauber verarbeiten zu können
                    old_value_raw = row.find('td', class_='activity-old-val').get_text(strip=True) if row.find('td', class_='activity-old-val') else ""
                    new_value_raw = row.find('td', class_='activity-new-val').get_text(strip=True) if row.find('td', class_='activity-new-val') else ""

                    old_value, new_value = old_value_raw, new_value_raw

                    # START DER ÄNDERUNG: Zentralisierte und erweiterte Verarbeitungslogik
                    if activity_name in ['Epic Child', 'Epic Link']:
                        old_match = re.search(r'([A-Z]+-\d+)', old_value_raw)
                        old_value = old_match.group(1) if old_match else old_value_raw
                        new_match = re.search(r'([A-Z]+-\d+)', new_value_raw)
                        new_value = new_match.group(1) if new_match else new_value_raw

                    elif activity_name in ['Status', 'Sprint', 'Fix Version/s']:
                        # Bereinigt Werte wie "Prefix:Value[...id...]" zu "Value" für alte und neue Werte
                        if old_value_raw:
                            old_value = old_value_raw.split(':')[-1].split('[')[0].strip()
                        if new_value_raw:
                            new_value = new_value_raw.split(':')[-1].split('[')[0].strip()

                        # Für Status, den Wert zusätzlich in Großbuchstaben umwandeln
                        if activity_name == 'Status':
                            old_value = old_value.upper()
                            new_value = new_value.upper()

                    elif activity_name == 'Fix Version/s':
                        match = re.search(r'(Q\d_\d{2})', new_value_raw)
                        new_value = match.group(1) if match else new_value_raw

                    elif activity_name in ['Acceptance Criteria', 'Description']:
                        new_value = '[...]' if new_value_raw else ''
                    # ENDE DER ÄNDERUNG

                    # Erstelle für jede einzelne Änderung einen eigenen Eintrag
                    extracted_data.append({
                        'benutzer': user_name,
                        'feld_name': activity_name,
                        'alter_wert': old_value,
                        'neuer_wert': new_value,
                        'zeitstempel_iso': timestamp_iso
                    })

        # Die finale Liste wird wie gewohnt umgedreht, um chronologisch zu sein
        return extracted_data[::-1]
