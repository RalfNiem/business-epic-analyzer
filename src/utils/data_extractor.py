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
        Extrahiert die Story Points von der Seite.

        Die Methode ist robust und prüft zuerst, ob der Wert in einem
        <input>-Feld vorliegt (wie es beim Bearbeiten eines Issues der Fall ist)
        und greift andernfalls auf den sichtbaren Text des Containers zurück.
        """
        try:
            # Finde das <strong>-Element mit dem Titel "Story Points" und wähle
            # das direkt folgende <div>-Geschwisterelement aus.
            value_container = driver.find_element(By.XPATH, "//strong[@title='Story Points']/following-sibling::div[1]")

            # Prüfe, ob sich der Wert in einem <input>-Feld befindet
            try:
                input_element = value_container.find_element(By.TAG_NAME, "input")
                return input_element.get_attribute("value")
            except NoSuchElementException:
                # Wenn kein <input>, nimm den sichtbaren Text des Containers
                return value_container.text.strip()
        except NoSuchElementException:
            # Wenn das Feld gar nicht existiert
            return "n/a"

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
                    # Extrahiere Issue-Schlüssel und URL
                    raw_text = child_link.text.strip()
                    child_href = child_link.get_attribute("href")

                    # KORREKTUR: Verwende re.search, um den Key robuster zu extrahieren
                    match = re.search(r'([A-Z]+-\d+)', raw_text)
                    if not match:
                        continue  # Überspringe, wenn kein gültiger Key gefunden wird

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
        extrahieren.
        """
        business_scope = ""

        try:
            # Suche nach dem Label mit title="Business Scope"
            business_scope_label = driver.find_element(By.XPATH,
                "//strong[@title='Business Scope']//label[contains(@for, 'customfield_')]")

            # Hole die customfield_id
            field_id = business_scope_label.get_attribute("for")

            # Suche nach dem zugehörigen Wert-Element
            business_scope_div = driver.find_element(By.XPATH, f"//div[@id='{field_id}-val']")

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


    def extract_issue_data(self, driver, issue_key):
        """
        Extrahiert umfassende Daten eines Jira-Issues in ein strukturiertes Format.

        Diese Methode ist der primäre Extraktionsmotor. Sie durchsucht die
        Jira-Seite systematisch nach einer Vielzahl von Metadaten und Inhalten.

        Ein Schlüsselmerkmal ist die Aggregation aller gefundenen Issue-
        Beziehungen ("is realized by", "child issues", "issues in epic") in
        eine einzige Liste `issue_links`. Jeder Eintrag in dieser Liste wird mit
        einem `relation_type` versehen, um die Art der Beziehung für die
        nachgelagerte Verarbeitung klar zu kennzeichnen.

        Für kritische Felder wie "Issue Type" und "Acceptance Criteria" sind
        Fallback-Methoden implementiert, um die Extraktion auch bei
        abweichenden UI-Strukturen zu gewährleisten.
        """
        data = {
            "key": issue_key,
            "issue_type": "",
            "title": "",
            "status": "",
            "resolution": "",  # <-- Hinzugefügt
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
            title_elem = driver.find_element(By.XPATH, "//h2[@id='summary-val']")
            data["title"] = title_elem.text.strip()
            logger.info(f"Titel gefunden: {data['title']}")
        except Exception as e:
            logger.info(f"Titel nicht gefunden: {e}")

        # Description
        try:
            desc_elem = driver.find_element(By.XPATH, "//div[contains(@id, 'description') or contains(@class, 'description')]")
            data["description"] = desc_elem.text
            logger.info(f"Beschreibung gefunden ({len(desc_elem.text)} Zeichen)")
        except Exception as e:
            logger.info(f"Beschreibung nicht gefunden: {e}")

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

        # Resolution <-- NEUER BLOCK START
        try:
            resolution_elem = driver.find_element(By.XPATH, "//span[@id='resolution-val']")
            data["resolution"] = resolution_elem.text.strip()
            logger.info(f"Resolution gefunden: {data['resolution']}")
        except Exception as e:
            # Dies ist ein erwarteter Fall für offene Issues.
            logger.info(f"Resolution nicht gefunden (normal bei 'Unresolved' Issues)")
        # NEUER BLOCK ENDE -->

        # Issue Type
        try:
            issue_type_container = driver.find_element(By.XPATH, "//span[@id='type-val']")
            issue_type_img = issue_type_container.find_element(By.XPATH, ".//img[@alt]")
            alt_text = issue_type_img.get_attribute("alt")
            match = re.match(r'Icon:\s+(.*)', alt_text)
            if match:
                issue_type = match.group(1).strip()
                data["issue_type"] = issue_type
                logger.info(f"Issue Type gefunden (aus alt-Attribut): {issue_type}")
            else:
                issue_type = issue_type_img.get_attribute("title")
                data["issue_type"] = issue_type
                logger.info(f"Issue Type gefunden (aus title-Attribut): {issue_type}")
        except Exception as e:
            # Fallback-Block für Issue Type
            logger.info(f"Issue Type mit primärer Methode nicht gefunden, starte Fallback...")
            try:
                issue_type_elements = driver.find_elements(By.XPATH, "//img[contains(@alt, 'Icon:')]")
                for img in issue_type_elements:
                    alt_text = img.get_attribute("alt")
                    if alt_text:
                        match = re.match(r'Icon:\s+(.*)', alt_text)
                        if match:
                            issue_type = match.group(1).strip()
                            data["issue_type"] = issue_type
                            logger.info(f"Issue Type mit Fallback-Methode gefunden (aus alt-Attribut): {issue_type}")
                            break
                if not data["issue_type"]:
                    logger.warning("Issue Type auch mit Fallback-Methode nicht gefunden.")
            except Exception as fallback_e:
                logger.error(f"Issue Type mit beiden Methoden nicht gefunden: {e}, {fallback_e}")

        # Business Value nur bei 'Business Epic' verarbeiten
        if data["issue_type"] == 'Business Epic' and self.description_processor is not None:
            try:
                processed_text = self.description_processor(
                    data["description"], self.model, self.token_tracker, self.azure_client
                )
                data["description"] = processed_text['description']
                data["business_value"] = processed_text['business_value']
                logger.info(f"Business Value ergänzt")
            except Exception as bv_error:
                logger.error(f"Fehler bei der Verarbeitung des Business Value: {bv_error}")

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

        # Acceptance Criteria
        try:
            acceptance_title = driver.find_element(By.XPATH, "//strong[@title='Acceptance Criteria']")
            label_elem = acceptance_title.find_element(By.XPATH, ".//label")
            field_id = label_elem.get_attribute("for")
            acceptance_field = driver.find_element(By.XPATH, f"//div[@id='{field_id}-val']")
            criteria_items = acceptance_field.find_elements(By.XPATH, ".//ul/li")
            if not criteria_items: criteria_items = acceptance_field.find_elements(By.XPATH, ".//p")
            for item in criteria_items:
                criterion_text = item.text.strip()
                if criterion_text: data["acceptance_criteria"].append(criterion_text)
            logger.info(f"{len(data['acceptance_criteria'])} Acceptance Criteria gefunden")
        except Exception as e:
            # Fallback-Block für Acceptance Criteria
            logger.info(f"Acceptance Criteria mit primärer Methode nicht gefunden, starte Fallback...")
            try:
                acceptance_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Acceptance Criteria') or contains(@title, 'Acceptance Criteria')]")
                if acceptance_elements:
                    ul_elements = driver.find_elements(By.XPATH, "//ul[preceding::*[contains(text(), 'Acceptance Criteria')]][1]//li")
                    if ul_elements:
                        for item in ul_elements:
                            criterion_text = item.text.strip()
                            if criterion_text and criterion_text not in data["acceptance_criteria"]:
                                data["acceptance_criteria"].append(criterion_text)
                logger.info(f"Mit Fallback-Methode {len(data['acceptance_criteria'])} Acceptance Criteria gefunden")
            except Exception as fallback_e:
                logger.error(f"Acceptance Criteria mit beiden Methoden nicht gefunden")

        # Labels
        try:
            labels_ul = driver.find_element(By.XPATH, "//ul[contains(@class, 'labels')]")
            label_links = labels_ul.find_elements(By.XPATH, ".//li/a[@title]")
            for label_link in label_links:
                label_title = label_link.get_attribute("title")
                if label_title: data["labels"].append(label_title)
            logger.info(f"{len(data['labels'])} Labels gefunden: {', '.join(data['labels'])}")
        except Exception as e:
            logger.info(f"Labels nicht gefunden")

        # Components
        try:
            components_container = driver.find_element(By.XPATH, "//span[@id='components-field']")
            component_links = components_container.find_elements(By.XPATH, ".//a[contains(@href, '/issues/')]")
            for comp_link in component_links:
                component_code = comp_link.text.strip()
                if component_code: data["components"].append({"code": component_code, "title": comp_link.get_attribute("title")})
            logger.info(f"{len(data['components'])} Components gefunden: {', '.join([comp['code'] for comp in data['components']])}")
        except Exception as e:
            logger.info(f"Keine Components gefunden")

        # 1. "is realized by" Links extrahieren und direkt zu 'issue_links' hinzufügen
        try:
            link_elements = driver.find_elements(By.XPATH,
                "//dl[contains(@class, 'links-list')]/dt[contains(text(), 'is realized by') or @title='is realized by']"
                "/..//a[contains(@class, 'issue-link')]")

            for link in link_elements:
                issue_key_attr = (link.get_attribute("data-issue-key") or link.text.strip()).replace('\u200b', '')

                # Optional: Versuche, den Summary-Text zu finden
                summary_text = ""
                try:
                    parent_element = link.find_element(By.XPATH, "./ancestor::div[contains(@class, 'link-content')]")
                    summary_element = parent_element.find_element(By.XPATH, ".//span[contains(@class, 'link-summary')]")
                    summary_text = summary_element.text.strip()
                except:
                    pass # Summary ist optional

                link_item = {
                    "key": issue_key_attr,
                    "title": link.text.strip(),
                    "summary": summary_text,
                    "url": link.get_attribute("href"),
                    "relation_type": "realized_by"  # Beziehungstyp direkt hier setzen
                }

                # Nur hinzufügen, wenn der Key noch nicht in der Zielliste ist
                if not any(item["key"] == link_item["key"] for item in data["issue_links"]):
                    data["issue_links"].append(link_item)

            if link_elements:
                logger.info(f"{len(link_elements)} 'is realized by' Links zu 'issue_links' hinzugefügt.")
        except Exception as e:
            logger.info(f"'is realized by' Links konnten nicht gefunden werden")

        # 2. Child Issues extrahieren und direkt zu 'issue_links' hinzufügen
        try:
            child_issues = DataExtractor._find_child_issues(driver) # Nur einmal aufrufen
            initial_link_count = len(data["issue_links"])

            for child in child_issues:
                # Prüfen, ob das Child Issue bereits in der Zielliste ist
                if not any(item["key"] == child["key"] for item in data["issue_links"]):
                    child["relation_type"] = "child"  # Beziehungstyp setzen
                    data["issue_links"].append(child)

            added_children = len(data["issue_links"]) - initial_link_count
            if added_children > 0:
                logger.info(f"{added_children} Child Issues zu 'issue_links' hinzugefügt.")
        except Exception as e:
             logger.info(f"Fehler bei der Verarbeitung von Child Issues")


        # 3. "Issues in epic" extrahieren und direkt zu 'issue_links' hinzufügen
        try:
            # Kurzes, explizites Warten auf den Container, der per JS nachgeladen wird
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
                                "key": key,
                                "title": title_element.text.strip(),
                                "summary": title_element.text.strip(),
                                "url": url_element.get_attribute('href'),
                                "relation_type": "issue_in_epic"
                            })
                    except Exception as row_error:
                        logger.warning(f"Konnte eine Zeile im 'Issues in epic'-Panel nicht parsen: {row_error}")
        except TimeoutException:
            # Normalfall für Issues, die keine Epics sind. Kein Fehler.
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
