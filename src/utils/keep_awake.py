# src/utils/keep_awake.py
"""
Dieses Modul stellt eine plattformübergreifende Funktion bereit,
um den System-Ruhezustand (Screensaver, Sperre) zu verhindern,
während ein langlebiger Prozess läuft.

Es unterstützt Windows und macOS.

Die Hauptfunktion 'prevent_screensaver' ist dafür ausgelegt,
in einem separaten Thread als Daemon zu laufen und auf ein
'threading.Event' zu warten, um sich sauber zu beenden.
"""

import sys
import logging
import subprocess
import threading
import time

# --- Plattformspezifische Imports für Windows ---
if sys.platform == "win32":
    import ctypes
    # --- Windows-Konstanten ---
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x0000001
    ES_DISPLAY_REQUIRED = 0x0000002
    try:
        kernel32 = ctypes.windll.kernel32
    except Exception:
        logging.critical("Konnte kernel32.dll nicht laden. Keep-Awake wird fehlschlagen.")
        kernel32 = None # Sicherstellen, dass die Variable existiert
elif sys.platform != "darwin":
    logging.warning(f"Keep-Awake-Funktion ist auf der Plattform '{sys.platform}' nicht implementiert.")


def prevent_screensaver(stop_event: threading.Event):
    """
    Läuft in einem separaten Thread und verhindert den System-Ruhezustand/Sperre.

    Args:
        stop_event (threading.Event): Ein Event, das signalisiert,
                                      wann der Thread beendet werden soll.
    """
    logging.info(f"Keep-Awake-Thread gestartet (Plattform: {sys.platform}).")

    try:
        if sys.platform == "win32":
            if not kernel32:
                logging.error("Keep-Awake (Windows): kernel32.dll nicht verfügbar.")
                return

            logging.info("Keep-Awake (Windows): Setze dauerhaften Aktiv-Zustand (System + Display).")
            flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED

            if kernel32.SetThreadExecutionState(flags) == 0:
                logging.error("Keep-Awake (Windows): SetThreadExecutionState konnte nicht gesetzt werden.")
                return

            stop_event.wait()

            logging.info("Keep-Awake (Windows): Setze System auf normalen Zustand zurück.")
            if kernel32.SetThreadExecutionState(ES_CONTINUOUS) == 0:
                 logging.warning("Keep-Awake (Windows): Reset-Aufruf fehlgeschlagen.")

        elif sys.platform == "darwin":
            while not stop_event.is_set():
                # Warte 8 Minuten (480 Sekunden).
                # Wenn das Event gesetzt wird, bricht wait() frühzeitig ab.
                if stop_event.wait(480):
                    break
                # Doppelte Prüfung, falls wait() ohne Event abläuft
                if stop_event.is_set():
                    break

                # Simuliere einen Tastendruck (keyCode 49 = Leertaste)
                # um den Mac wach zu halten
                script = 'tell application "System Events" to key code 49'
                subprocess.run(
                    ['osascript', '-e', script],
                    check=True, capture_output=True, text=True
                )
                logging.info("Keep-Awake (macOS): Leertaste simuliert.")
        else:
            logging.info(f"Keep-Awake: Funktion nicht unterstützt auf Plattform '{sys.platform}'.")
            # Warte einfach auf das Stop-Event
            stop_event.wait()

    except Exception as e:
        logging.error(f"Keep-Awake-Fehler: {e}. Thread wird beendet.")

    logging.info("Keep-Awake-Thread wurde beendet.")


# --- Beispielhafte Verwendung (zum Testen) ---
if __name__ == "__main__":
    """
    Startet einen Testlauf für die Keep-Awake-Funktion.
    """
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        stream=sys.stdout
    )

    print("Starte Keep-Awake-Test für 30 Sekunden...")
    print("Der Computer sollte während dieser Zeit nicht in den Ruhezustand gehen.")
    print("Drücken Sie Strg+C, um den Test vorzeitig zu beenden.")

    stop_event = threading.Event()
    keep_awake_thread = threading.Thread(target=prevent_screensaver, args=(stop_event,))
    keep_awake_thread.daemon = True
    keep_awake_thread.start()

    try:
        time.sleep(30)
        print("30 Sekunden sind abgelaufen. Beende Keep-Awake...")
    except KeyboardInterrupt:
        print("\nTest vom Benutzer abgebrochen. Beende Keep-Awake...")
    finally:
        stop_event.set()
        keep_awake_thread.join(timeout=2)
        print("Test beendet.")
