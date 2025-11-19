"""
    Erstellt einen Graphen von JIRA-Issues basierend auf einer flexiblen Hierarchie.

    Diese Klasse baut einen gerichteten Graphen (NetworkX DiGraph), der die Beziehungen
    zwischen Issues darstellt.

    Datenquellen-Logik:
    -------------------
    Die Klasse unterstützt einen hybriden Datenzugriff (`_fetch_issue_data`):
    1. **Primär (SQLite):** Wenn eine `db_conn` übergeben wurde, wird versucht,
       die Issue-Daten direkt aus der Datenbank-Tabelle `issues` zu lesen.
    2. **Fallback (JSON-Dateien):** Falls keine DB-Verbindung besteht oder der Key
       nicht in der DB ist, wird versucht, die entsprechende JSON-Datei aus
       dem `json_dir` zu lesen.

    Dies ermöglicht den Einsatz sowohl in reinen Datei-basierten Workflows als auch
    in performanten DB-gestützten Umgebungen.
    """

import json
import os
import networkx as nx
import matplotlib.pyplot as plt
from pathlib import Path
import sqlite3
from collections import defaultdict
import matplotlib.patches as mpatches
from utils.logger_config import logger
from utils.config import (
    JIRA_ISSUES_DIR,
    ISSUE_TREES_DIR,
    JSON_SUMMARY_DIR,
    LOGS_DIR,
    JIRA_TREE_MANAGEMENT,
    ISSUE_LOG_FILE
)


class JiraTreeGenerator:
    """
    Erstellt einen Graphen von JIRA-Issues basierend auf einer flexiblen Hierarchie.

    Diese Klasse durchsucht JSON-Dateien von JIRA-Issues und baut einen gerichteten
    Graphen (einen Baum), der die Beziehungen zwischen den Issues darstellt. Die Art
    der zu verfolgenden Beziehungen ist flexibel konfigurierbar.
    """

    # +++ KORRIGIERTER CONSTRUCTOR +++
    def __init__(self, json_dir=JIRA_ISSUES_DIR, allowed_types=None, db_conn: sqlite3.Connection = None):
        """
        Initialisiert den JiraTreeGenerator.

        Diese Klasse kann so konfiguriert werden, dass sie verschiedene Hierarchie-Typen
        verwendet. Wenn keine Konfiguration (`allowed_types`) übergeben wird, greift sie
        auf die Standardkonfiguration `JIRA_TREE_MANAGEMENT` zurück.

        Args:
            json_dir (str): Das Verzeichnis, das die JIRA-Issue-JSON-Dateien enthält.
                            (Wird für den Fallback genutzt).
            allowed_types (dict, optional): Ein Dictionary, das einem Issue-Typ (str) eine
                                            Liste von erlaubten Beziehungs-Typen (str)
                                            zuordnet. Z.B. {'Epic': ['realized_by'], ...}.
                                            Wenn None, wird der Standard aus der config verwendet.
            db_conn (sqlite3.Connection, optional): Eine bestehende SQLite-Datenbankverbindung.
                                                    Wenn diese angegeben wird, wird das Lesen
                                                    aus dem 'json_dir' ignoriert.
        """
        self.json_dir = json_dir
        # Verwende die übergebene Konfiguration, oder greife auf den Standard zurück
        self.allowed_hierarchy_types = allowed_types if allowed_types is not None else JIRA_TREE_MANAGEMENT
        # +++ KORRIGIERTE ZEILE +++
        self.db_conn = db_conn # Speichert die DB-Verbindung (kann None sein)

    # +++ ENDE DER KORREKTUR +++

    def _log_missing_issue(self, issue_key: str):
        """
        Protokolliert einen fehlenden Issue-Key in der zentralen Log-Datei.
        Verhindert doppelte Einträge, um die Datei sauber zu halten.
        """
        try:
            existing_keys = set()
            # Prüfen, ob die Log-Datei bereits existiert und Einträge hat
            if os.path.exists(ISSUE_LOG_FILE):
                with open(ISSUE_LOG_FILE, 'r', encoding='utf-8') as f:
                    existing_keys = {line.strip() for line in f}

            # Nur schreiben, wenn der Key noch nicht in der Datei ist
            if issue_key not in existing_keys:
                with open(ISSUE_LOG_FILE, 'a', encoding='utf-8') as f:
                    f.write(f"{issue_key}\n")
                logger.info(f"Fehlender Key '{issue_key}' wurde zur Nachverfolgung in {ISSUE_LOG_FILE} hinzugefügt.")
        except Exception as e:
            logger.error(f"Fehler beim Schreiben des fehlenden Keys '{issue_key}' in die Log-Datei: {e}")


    def _fetch_issue_data(self, key: str) -> dict | None:
        """
        Holt die Rohdaten für einen einzelnen Issue-Key, entweder aus der DB oder aus einer Datei.
        """
        # Priorität 1: Datenbank-Verbindung nutzen
        if self.db_conn:
            try:
                cursor = self.db_conn.cursor()
                cursor.execute("SELECT data FROM issues WHERE key = ?", (key,))
                row = cursor.fetchone()
                if row:
                    return json.loads(row[0])
                else:
                    logger.warning(f"Skipping child {key}: Key nicht in der SQLite-DB gefunden.")
                    self._log_missing_issue(key)
                    return None
            except Exception as e:
                logger.error(f"Fehler beim Lesen von Key {key} aus SQLite: {e}")
                return None

        # Priorität 2: Fallback auf altes Datei-System
        file_path = self.find_json_for_key(key)
        if not file_path:
            logger.warning(f"Skipping child {key}: JSON file not found (File-Fallback).")
            self._log_missing_issue(key)
            return None

        return self.read_jira_issue(file_path)


    def read_jira_issue(self, file_path):
        """
        Liest einen JIRA-Issue aus einer JSON-Datei.
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                return json.load(file)
        except FileNotFoundError:
            logger.error(f"Warning: File {file_path} not found")
            return None
        except json.JSONDecodeError:
            logger.error(f"Error: File {file_path} contains invalid JSON")
            return None

    def find_json_for_key(self, key):
        """
        Findet die passende JSON-Datei für einen bestimmten JIRA-Key.
        """
        exact_path = os.path.join(self.json_dir, f"{key}.json")
        if os.path.exists(exact_path):
            return exact_path
        json_files = glob.glob(os.path.join(self.json_dir, "*.json"))
        for file_path in json_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as file:
                    data = json.load(file)
                    if data.get("key") == key:
                        return file_path
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
        return None

    def build_issue_tree(self, root_key, include_rejected=False):
        """
        Baut einen gerichteten Graphen basierend auf einer flexiblen Hierarchie-Konfiguration.
        """
        logger.info(f"Building issue tree for root issue: {root_key}")
        G = nx.DiGraph()

        # --- MODIFIZIERTER DATENZUGRIFF ---
        # Nutzt die neue Methode _fetch_issue_data, die den Fallback enthält
        root_data = self._fetch_issue_data(root_key)
        # --- ENDE MODIFIKATION ---

        if not root_data:
            logger.error(f"Error: No data found for root key {root_key}")
            return None

        root_issue_type = root_data.get('issue_type', '')
        if root_issue_type not in self.allowed_hierarchy_types:
            logger.error(f"Error: Root issue {root_key} is of type '{root_issue_type}', "
                         f"which is not a valid starting point in the hierarchy configuration.")
            return None

        # Prüft auf eine Liste von auszuschließenden Resolution-Typen
        resolutions_to_skip = ['Rejected', 'Withdrawn']
        root_resolution = root_data.get('resolution')
        if not include_rejected and root_resolution in resolutions_to_skip:
            logger.error(f"Error: Root issue {root_key} has resolution '{root_resolution}' and will not be processed.")
            return None

        G.add_node(root_key, **root_data)
        visited = set()

        def _add_children(parent_key):
            """Rekursive Hilfsfunktion, die generisch nach Kindern sucht."""
            if parent_key in visited:
                return
            visited.add(parent_key)

            parent_data = G.nodes[parent_key]
            parent_issue_type = parent_data.get('issue_type', '')

            allowed_relations = self.allowed_hierarchy_types.get(parent_issue_type, [])

            if not allowed_relations or 'issue_links' not in parent_data:
                return

            for link in parent_data['issue_links']:
                relation_type = link.get('relation_type')

                if relation_type in allowed_relations:
                    child_key = link.get('key')
                    if not child_key:
                        continue

                    # --- MODIFIZIERTER DATENZUGRIFF ---
                    # Nutzt die neue Methode _fetch_issue_data, die den Fallback enthält
                    child_data = self._fetch_issue_data(child_key)
                    # --- ENDE MODIFIKATION ---

                    if not child_data:
                        # Logging passiert bereits in _fetch_issue_data
                        continue
                    # Prüft auf eine Liste von auszuschließenden Resolution-Typen
                    child_resolution = child_data.get('resolution')
                    if not include_rejected and child_resolution in resolutions_to_skip:
                        logger.info(f"Skipping child {child_key} because its resolution is '{child_resolution}'.")
                        continue

                    G.add_node(child_key, **child_data)
                    G.add_edge(parent_key, child_key)
                    _add_children(child_key)

        _add_children(root_key)

        if G.number_of_nodes() <= 1 and not root_data.get('issue_links'):
            logger.info(f"Warning: The root issue {root_key} has no 'issue_links' entries")

        logger.info(f"Tree built. Number of nodes: {G.number_of_nodes()}")
        return G


class JiraTreeVisualizer:
    """
    Klasse zur Visualisierung eines JIRA-Issue-Baum-Graphen.

    Nimmt einen `networkx.DiGraph` entgegen und erstellt eine grafische Darstellung,
    die als Bilddatei gespeichert wird. Die Knoten werden nach ihrem Status eingefärbt.
    """
    def __init__(self, output_dir=ISSUE_TREES_DIR, format='png'):
        """
        Initialisiert den Visualizer.

        Args:
            output_dir (str): Das Verzeichnis zum Speichern der erstellten Bilder.
            format (str): Das Dateiformat für die Ausgabe (z.B. 'png', 'svg').
        """
        self.output_dir = output_dir
        self.format = format
        self.status_colors = {'Funnel': 'lightgray', 'Backlog for Analysis': 'lightgray', 'Analysis': 'lemonchiffon', 'Backlog': 'lemonchiffon', 'Review': 'lemonchiffon', 'Waiting': 'lightblue', 'In Progress': 'lightgreen', 'Deployment': 'lightgreen', 'Validation': 'lightgreen', 'Resolved': 'green', 'Closed': 'green'}

    def _determine_node_size_and_font(self, G):
        """Bestimmt dynamisch die Größe der Knoten und Schrift basierend auf der Knotenanzahl."""
        if G.number_of_nodes() > 20: return 2000, 8, (20, 12)
        elif G.number_of_nodes() > 10: return 3000, 8, (16, 12)
        else: return 4000, 9, (12, 12)

    def visualize(self, G, root_key, output_file=None):
        """
        Erstellt und speichert eine Visualisierung des Graphen.

        Der Graph wird mit einem hierarchischen Layout (dot) dargestellt. Die Knoten-
        beschriftungen enthalten den Key und die Fix-Version(en). Eine Legende erklärt
        die Farbkodierung der Status.

        Args:
            G (nx.DiGraph): Der zu visualisierende Graph.
            root_key (str): Der Schlüssel des Wurzel-Issues, wird für den Dateinamen
                            und Titel verwendet.
            output_file (str, optional): Der vollständige Pfad zur Ausgabedatei.
                                         Wenn nicht angegeben, wird ein Standardname
                                         im `output_dir` generiert.

        Returns:
        bool: True, wenn die Visualisierung erfolgreich gespeichert wurde, sonst False.
        """
        if G is None or not isinstance(G, nx.DiGraph) or G.number_of_nodes() <= 1:
            if G is None or not isinstance(G, nx.DiGraph): logger.error("Error: Invalid graph provided.")
            else: logger.info(f"Warning: The graph contains only the root node {root_key}.")
            return False

        if output_file is None:
            os.makedirs(self.output_dir, exist_ok=True)
            output_file = os.path.join(self.output_dir, f"{root_key}_issue_tree.{self.format}")

        # pos = nx.nx_agraph.graphviz_layout(G, prog='dot')  # funktioniert nur bei MacOS/Linux mit installiertem Graphviz
        pos = nx.spring_layout(G, seed=42) # plattformunabhängig, aber weniger hierarchisch
        NODE_SIZE, FONT_SIZE, figure_size = self._determine_node_size_and_font(G)
        plt.figure(figsize=figure_size)

        nodes_by_status = defaultdict(list)
        for node, attrs in G.nodes(data=True):
            nodes_by_status[attrs.get('status', '')].append(node)

        for status, nodes in nodes_by_status.items():
            nx.draw_networkx_nodes(G, pos, nodelist=nodes, node_size=NODE_SIZE, node_color=self.status_colors.get(status, 'peachpuff'), alpha=0.8)

        labels = {}
        for node, attrs in G.nodes(data=True):
            fix_versions = attrs.get('fix_versions', [])
            fix_versions_string = "\n".join(fix_versions) if isinstance(fix_versions, list) else str(fix_versions)
            labels[node] = f"{node.split('-')[0]}-\n{node.split('-')[1]}\n{fix_versions_string}"

        nx.draw_networkx_edges(G, pos, width=1.0, alpha=0.5, arrows=True, arrowstyle='->', arrowsize=15)
        nx.draw_networkx_labels(G, pos, labels=labels, font_size=FONT_SIZE, font_family='sans-serif', verticalalignment='center')

        legend_patches = [mpatches.Patch(color=color, label=status) for status, color in self.status_colors.items() if status and any(node for node in nodes_by_status.get(status, []))]
        plt.legend(handles=legend_patches, loc='upper right', title='Status')

        title = G.nodes[list(G.nodes())[0]].get("title", '')
        plt.title(f"{root_key} Jira Hierarchy\n{title}", fontsize=16)
        plt.axis('off')

        try:
            plt.tight_layout()
            plt.savefig(output_file, dpi=100, bbox_inches='tight')
            plt.close()
            logger.info(f"Issue Tree saved: {output_file}")
            return True
        except Exception as e:
            logger.error(f"Error saving visualization: {e}")
            return False


class JiraContextGenerator:
    """
    Erstellt einen intelligenten, Token-limitierten JSON-Kontext
    aus einem JIRA-Issue-Baum (nx.DiGraph).

    Implementiert "Phase 2: Pruning"
    - Priorisiert Inhalte (Essenziell vs. Kürzbar)
    - Priorisiert Hierarchie (Level 0, 1, dann 2)
    - Hält ein Token-Budget ein (4 Zeichen / Token Näherung)
    """

    # --- Konfiguration der Pruning-Logik ---
    ESSENTIAL_FIELDS = ['key', 'title', 'status', 'issue_type', 'description']
    CUTTABLE_FIELDS = ['acceptance_criteria']
    MAX_HIERARCHY_LEVEL = 2
    CHAR_PER_TOKEN = 4
    # Puffer für System-Prompt, User-Prompt-Template und JSON-Wrapper
    TOKEN_PUFFER = 500

    def __init__(self, output_dir=JSON_SUMMARY_DIR):
        """
        Initialisiert den Kontext-Generator.
        (Original-Konstruktor beibehalten)
        """
        self.output_dir = output_dir

    def _build_pruned_node_payload_char(self,
                                        details: dict,
                                        remaining_char_budget: int) -> (dict, int):
        """
        Erstellt den Payload (das Dict) für einen *einzelnen* Knoten
        und gibt das Payload sowie die *verbrauchten Zeichen* zurück.
        """
        node_payload = {}

        # 1. Tier 1 (Essentielle Felder)
        for field in self.ESSENTIAL_FIELDS:
            node_payload[field] = details.get(field)

        # 2. Tier 2 (Kürzbare Felder) - Vollständiger Versuch
        for field in self.CUTTABLE_FIELDS:
            node_payload[field] = details.get(field)

        # 3. Messe vollen Knoten (Tier 1 + Tier 2)
        try:
            node_chars = len(json.dumps(node_payload))
        except TypeError: # Fängt nicht-serialisierbare Daten ab
             node_chars = len(str(node_payload))

        if node_chars <= remaining_char_budget:
            # Passt komplett rein
            return node_payload, node_chars

        # 4. Passt nicht. Kürze Tier 2 (Acceptance Criteria)
        for field in self.CUTTABLE_FIELDS:
            node_payload.pop(field, None)

        # 5. Berechne Budget, das für Tier 2 übrig ist
        try:
            tier_1_chars = len(json.dumps(node_payload))
        except TypeError:
            tier_1_chars = len(str(node_payload))

        budget_left_for_tier_2 = remaining_char_budget - tier_1_chars

        # Puffer für '...', Schlüssel, Anführungszeichen
        MIN_TIER_2_BUDGET = 50
        if budget_left_for_tier_2 <= MIN_TIER_2_BUDGET:
             # Passt nicht mal Tier 1 oder kein Budget mehr übrig
             if tier_1_chars <= remaining_char_budget:
                 logger.warning(f"Lasse Tier 2 für {details.get('key')} weg (Budget erschöpft).")
                 return node_payload, tier_1_chars
             else:
                 logger.error(f"Knoten {details.get('key')} (nur Essentielles) ist zu groß ({tier_1_chars} Chars).")
                 return None, 0 # Dieser Knoten kann nicht hinzugefügt werden

        # 6. Füge Tier 2 gekürzt hinzu
        for field in self.CUTTABLE_FIELDS:
            value = details.get(field)
            if not value:
                continue

            # Verfügbare Zeichen für den *Wert* (abzgl. Puffer)
            available_chars = budget_left_for_tier_2 - len(field) - 20

            if available_chars > 0:
                node_payload[field] = str(value)[:available_chars] + "..."

        # 7. Finale Messung des gekürzten Knotens
        try:
            final_node_chars = len(json.dumps(node_payload))
        except TypeError:
            final_node_chars = len(str(node_payload))

        if final_node_chars <= remaining_char_budget:
            return node_payload, final_node_chars
        else:
            # Fallback: Wenn Tier 1 + gekürztes Tier 2 immer noch zu groß ist
            for field in self.CUTTABLE_FIELDS:
                node_payload.pop(field, None)
            final_node_chars = len(json.dumps(node_payload))

            if final_node_chars <= remaining_char_budget:
                 logger.warning(f"Lasse Tier 2 für {details.get('key')} weg (Budget erschöpft nach Kürzung).")
                 return node_payload, final_node_chars
            else:
                 logger.error(f"Knoten {details.get('key')} (selbst gekürzt) ist zu groß. Wird verworfen.")
                 return None, 0

    def generate_context(self, G: nx.DiGraph, root_key: str, max_token_budget: int = 40000) -> str:
        """
        Generiert eine JSON-formatierte Zeichenkette durch intelligentes Pruning.
        Ersetzt die Originalmethode.
        """
        if G is None or not isinstance(G, nx.DiGraph):
            logger.error("Error: Invalid graph provided.")
            return "{}"
        if root_key not in G:
            logger.error(f"Error: Root node {root_key} not found in the graph.")
            return "{}"

        logger.info(f"Starte intelligente Kontext-Generierung für {root_key} (Budget: {max_token_budget} Tokens)")

        # 1. Budget in Zeichen umrechnen (abzgl. Puffer)
        INTERNAL_BUDGET_CHAR = (max_token_budget - self.TOKEN_PUFFER) * self.CHAR_PER_TOKEN

        # 2. Finale Struktur vorbereiten
        final_context_dict = {"root": root_key, "issues": []}

        # Puffer für JSON-Wrapper abziehen
        INTERNAL_BUDGET_CHAR -= len(json.dumps(final_context_dict))

        nodes_added_to_context = set()

        # 3. BFS-Traversal (Level-für-Level) [basiert auf Original-Logik, cite: 1, line 401]
        for node_key in nx.bfs_tree(G, source=root_key):

            # --- Hierarchie-Check ---
            try:
                depth = nx.shortest_path_length(G, source=root_key, target=node_key)
            except nx.NetworkXNoPath:
                logger.warning(f"Knoten {node_key} ist im BFS-Baum, aber Pfad nicht gefunden? Überspringe.")
                continue

            if depth > self.MAX_HIERARCHY_LEVEL:
                logger.info(f"Erreiche max. Hierarchie ({self.MAX_HIERARCHY_LEVEL}). Ignoriere Knoten {node_key} und tiefer.")
                continue # Nächster Knoten im BFS (könnte ein anderer, flacherer Zweig sein)

            # 4. Payload für diesen Knoten erstellen
            node_attrs = G.nodes[node_key]

            pruned_payload, node_chars = self._build_pruned_node_payload_char(
                node_attrs,
                INTERNAL_BUDGET_CHAR
            )

            if pruned_payload is None:
                # Selbst der gekürzte Knoten passt nicht mehr
                logger.warning(f"Budget erschöpft. Stoppe Kontext bei Knoten {node_key}.")
                break # Breche die *gesamte* BFS-Schleife ab.

            # 5. Verknüpfungen hinzufügen (wie im Original)
            # Diese werden *innerhalb* des Budgets des Knotens hinzugefügt.

            # Füge Eltern hinzu (realizes)
            pruned_payload["realizes"] = [
                {"key": parent, "title": G.nodes[parent].get('title', 'No title')}
                for parent in G.predecessors(node_key)
            ]

            # Füge Kinder hinzu (realized_by)
            pruned_payload["realized_by"] = [
                {"key": child_key, "title": G.nodes[child_key].get('title', 'No title')}
                for child_key in G.successors(node_key)
            ]

            # 6. Finale Kosten (Payload + Links) neu berechnen
            try:
                final_node_chars = len(json.dumps(pruned_payload))
            except TypeError:
                final_node_chars = len(str(pruned_payload))

            # 7. Prüfen, ob die Links das Budget gesprengt haben
            if final_node_chars > INTERNAL_BUDGET_CHAR and node_key != root_key:
                 pruned_payload.pop('realized_by', None)
                 pruned_payload.pop('realizes', None)
                 try:
                    final_node_chars = len(json.dumps(pruned_payload))
                 except TypeError:
                    final_node_chars = len(str(pruned_payload))

                 if final_node_chars > INTERNAL_BUDGET_CHAR:
                     logger.warning(f"Budget erschöpft (sogar ohne Links). Stoppe Kontext bei {node_key}.")
                     break # Break loop

            if final_node_chars > INTERNAL_BUDGET_CHAR and node_key == root_key:
                 logger.error(f"Root-Knoten {root_key} ist allein schon zu groß ({final_node_chars} Chars). Breche ab.")
                 return "{}"

            # 8. Knoten hinzufügen und Budget abziehen
            final_context_dict["issues"].append(pruned_payload)
            INTERNAL_BUDGET_CHAR -= (final_node_chars + 1) # +1 für Komma
            nodes_added_to_context.add(node_key)

        # 9. Finale JSON-String-Erstellung
        try:
            json_str = json.dumps(final_context_dict, indent=2, ensure_ascii=False)
        except TypeError as e:
            logger.error(f"Fehler beim Serialisieren des finalen Kontexts für {root_key}: {e}")
            return "{}"

        final_tokens = len(json_str) // self.CHAR_PER_TOKEN
        logger.info(f"Kontext-Generierung abgeschlossen. {len(nodes_added_to_context)} Knoten im Kontext. Finale geschätzte Tokens: {final_tokens}")

        return json_str
