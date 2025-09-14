"""
Orchestriert die Ausführung einer Reihe von modularen Analyzer-Klassen.

Diese Klasse dient als zentraler "Dirigent" für den Analyseprozess. Ihre Hauptaufgabe
ist es, die Hauptanwendung von der Kenntnis über die spezifischen, einzelnen
Analyseschritte zu entkoppeln. Sie nimmt eine Liste von beliebigen Analyzer-Klassen
entgegen, führt deren `analyze`-Methode nacheinander aus und sammelt die
Ergebnisse in einem einzigen, strukturierten Dictionary.

Dieses Design fördert die Modularität und Erweiterbarkeit des Systems. Neue
Analyseschritte können einfach durch die Erstellung einer neuen Analyzer-Klasse
und deren Hinzufügen zur Konfiguration integriert werden, ohne dass die
Ablaufsteuerung in der Hauptanwendung geändert werden muss.

Hauptfunktionalität:
-   Nimmt eine Liste von Analyzer-Klassen entgegen (nicht Instanzen).
-   Iteriert durch jede Klasse, instanziiert sie und ruft ihre `analyze`-Methode auf.
-   Verwendet einen `ProjectDataProvider`, um allen Analyzern eine konsistente
    und effiziente Datenbasis zur Verfügung zu stellen.
-   Sammelt die Ergebnisse und gibt sie in einem Dictionary zurück, wobei die
    Klassennamen als Schlüssel dienen, um eine klare Zuordnung zu gewährleisten.
-   Beinhaltet eine Fehlerbehandlung, um sicherzustellen, dass der Ausfall eines
    Analyzers nicht den gesamten Prozess zum Absturz bringt.
"""

from utils.project_data_provider import ProjectDataProvider
from utils.logger_config import logger

class AnalysisRunner:
    """
    Orchestriert die Ausführung einer Reihe von modularen Analyzer-Klassen.

    Diese Klasse entkoppelt die Hauptanwendung von der Kenntnis über die
    spezifischen Analyseschritte. Sie nimmt eine Liste von Analyzer-Klassen,
    führt sie nacheinander aus und sammelt die Ergebnisse in einem
    strukturierten Dictionary.
    """

    def __init__(self, analyzer_classes: list):
        """
        Initialisiert den Runner mit einer Liste von Analyzer-Klassen.

        Args:
            analyzer_classes (list): Eine Liste der Analyzer-Klassen (nicht Instanzen),
                                     die ausgeführt werden sollen. z.B. [ScopeAnalyzer, DynamicsAnalyzer].
        """
        self.analyzer_classes = analyzer_classes
        logger.info(f"AnalysisRunner mit {len(analyzer_classes)} Analyzern initialisiert.")

    def run_analyses(self, data_provider: ProjectDataProvider) -> dict:
        """
        Führt alle konfigurierten Analysen für den gegebenen DataProvider aus.

        Args:
            data_provider (ProjectDataProvider): Die zentrale Datenquelle für das Projekt.

        Returns:
            dict: Ein Dictionary, das die Ergebnisse jeder Analyse enthält.
                  Der Schlüssel ist der Name der Analyzer-Klasse.
                  z.B. {'ScopeAnalyzer': {...}, 'DynamicsAnalyzer': {...}}
        """
        all_results = {}
        logger.info(f"Starte Ausführung von {len(self.analyzer_classes)} Analysen für Epic '{data_provider.epic_id}'.")

        for analyzer_class in self.analyzer_classes:
            analyzer_name = analyzer_class.__name__
            try:
                logger.info(f"-> Führe {analyzer_name} aus...")
                # 1. Instanziiere den Analyzer
                analyzer_instance = analyzer_class()
                # 2. Führe die Analyse durch und speichere das Ergebnis
                analysis_result = analyzer_instance.analyze(data_provider)
                all_results[analyzer_name] = analysis_result
                logger.info(f"<- {analyzer_name} erfolgreich abgeschlossen.")
            except Exception as e:
                logger.error(f"Fehler bei der Ausführung von {analyzer_name}: {e}", exc_info=True)
                all_results[analyzer_name] = {"error": str(e)}

        return all_results
