import os
import sys
import time
import concurrent.futures
import logging
from datetime import datetime

# Passen Sie den Pfad an, falls nötig
# project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
# if project_root not in sys.path:
#     sys.path.insert(0, project_root)

# Annahme: Der DnaBotClient liegt im Pfad
try:
    from utils.dna_bot_client import DnaBotClient
    # Wir benötigen einen Dummy-Logger, falls der Client ihn erwartet
    class DummyLogger:
        def info(self, msg): pass
        def error(self, msg): pass
        def critical(self, msg): pass
        def debug(self, msg): pass
    # Temporäre Anpassung der Logger-Konfiguration für dieses Skript
    # Dies ist notwendig, wenn dna_bot_client.py 'from utils.logger_config import logger' verwendet
    if 'utils.logger_config' in sys.modules:
        logging.getLogger().setLevel(logging.CRITICAL) # Reduziere Logs des Clients
except ImportError:
    print("FEHLER: Konnte DnaBotClient nicht importieren. Prüfen Sie den Pfad.")
    sys.exit(1)

# --- KONFIGURATION ---
MODEL_NAME = "gpt-oss-120b"
NUM_CALLS = 20
PROMPT = "Erkläre ausführlich, was ein Business Epic im Kontext von Jira ist. Antworte mit mindestens 150 Wörtern."
MAX_TOKENS = 1000
# Wir initialisieren den Client VOR den Tests, damit der Token nur einmal geholt wird
CLIENT = DnaBotClient(verify_ssl=False)


def run_single_completion(worker_id: int):
    """
    Führt einen einzelnen synchronen Aufruf an den DNABot durch.
    """
    start_time = time.time()

    # Sicherstellen, dass der Token vorhanden ist, bevor die Anfrage gesendet wird (optional,
    # da completion dies automatisch tun sollte, aber es hilft bei der Diagnose)
    try:
        # Hier nutzen wir den global initialisierten Client
        response = CLIENT.completion(
            model_name=MODEL_NAME,
            user_prompt=f"Worker {worker_id}: {PROMPT}",
            max_tokens=MAX_TOKENS,
            temperature=0.1
        )
        duration = time.time() - start_time
        return worker_id, duration, "Erfolg", len(response.get("text", ""))

    except Exception as e:
        duration = time.time() - start_time
        return worker_id, duration, f"Fehler: {e}", 0


def run_serial_test(num_calls):
    """Führt eine Serie synchroner Aufrufe aus."""
    print(f"\n{'='*50}\n Starte Seriellen Test ({num_calls} Aufrufe)...")
    results = []
    total_start = time.time()

    for i in range(1, num_calls + 1):
        print(f"  -> Serieller Aufruf {i}/{num_calls}...", end="", flush=True)
        worker_id, duration, status, length = run_single_completion(i)
        print(f" Dauer: {duration:.2f}s, Status: {status.split(':')[0]}")
        results.append(duration)

    total_duration = time.time() - total_start
    return total_duration, results


def run_parallel_test(num_calls):
    """Führt parallele Aufrufe mit einem ThreadPoolExecutor aus."""
    print(f"\n{'='*50}\n Starte Parallelen Test ({num_calls} Worker)...")
    results = []
    total_start = time.time()

    # Definiere die maximale Anzahl der Worker (gleich der Anzahl der Aufrufe)
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_calls) as executor:

        # Sende alle Aufgaben gleichzeitig
        future_to_worker = {executor.submit(run_single_completion, i): i for i in range(1, num_calls + 1)}

        # Warte auf die Ergebnisse
        for future in concurrent.futures.as_completed(future_to_worker):
            worker_id = future_to_worker[future]
            try:
                worker_id, duration, status, length = future.result()
                print(f"  <- Worker {worker_id} abgeschlossen. Dauer: {duration:.2f}s, Status: {status.split(':')[0]}")
                results.append(duration)
            except Exception as e:
                print(f"  <- Worker {worker_id} ist abgestürzt: {e}")
                results.append(0) # 0, da der Fehler die Gesamtlaufzeit nicht beeinflusst

    total_duration = time.time() - total_start
    return total_duration, results


def main():

    print(f"\n--- DNABot Parallelitätstest gestartet ---")
    print(f"Ziel: {MODEL_NAME}, Anzahl Aufrufe: {NUM_CALLS}")
    print(f"Erster Token-Abruf...")
    try:
        # Initialer Token-Abruf, um diesen Schritt aus den Testzeiten zu entfernen
        CLIENT._ensure_token_valid()
    except Exception as e:
        print(f"FEHLER: Konnte keinen initialen Access Token abrufen: {e}")
        sys.exit(1)

    # --- 1. Serieller Test ---
    serial_total_time, serial_times = run_serial_test(NUM_CALLS)

    # --- 2. Paralleler Test ---
    parallel_total_time, parallel_times = run_parallel_test(NUM_CALLS)


    # --- 3. Ergebnisse zusammenfassen ---
    print(f"\n{'='*50}\n ZUSAMMENFASSUNG")
    print(f"Anzahl erfolgreicher Aufrufe: {NUM_CALLS}")
    print(f"Modell: {MODEL_NAME}")
    print(f"Anzahl Worker: {NUM_CALLS}")

    print("\n--- Laufzeit Vergleich ---")
    print(f"Seriell (Gesamtdauer): {serial_total_time:.2f} Sekunden")
    print(f"Parallel (Gesamtdauer): {parallel_total_time:.2f} Sekunden")

    print("\n--- Durchschnittsvergleich ---")
    avg_serial = sum(serial_times) / NUM_CALLS
    avg_parallel = sum(parallel_times) / NUM_CALLS
    print(f"Seriell (Durchschnitt pro Aufruf): {avg_serial:.2f} Sekunden")
    print(f"Parallel (Durchschnitt pro Aufruf): {avg_parallel:.2f} Sekunden")

    # --- Fazit zur Parallelität ---
    parallel_factor = serial_total_time / parallel_total_time if parallel_total_time > 0 else float('inf')

    print(f"\n--- FAZIT ZUR PARALLELISIERUNG ---")
    if parallel_total_time < serial_total_time * 0.5:
        print("✅ Die API scheint stark parallelisiert und gut skalierbar zu sein.")
        print(f"Der Parallel-Test war ca. {parallel_factor:.1f}-mal schneller als der Serielle Test.")
    elif parallel_total_time < serial_total_time * 0.9:
        print("🟡 Die API erlaubt Parallelität, aber die Skalierung ist durch interne Wartezeiten oder Rate Limits begrenzt.")
        print(f"Der Parallel-Test war ca. {parallel_factor:.1f}-mal schneller.")
    else:
        print("❌ Die API scheint interne Serialisierung oder strikte Rate Limits zu verwenden.")
        print("Die parallele Ausführung brachte kaum Geschwindigkeitsvorteile.")

    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
