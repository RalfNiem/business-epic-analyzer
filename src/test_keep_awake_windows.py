"""
Testskript zur Überprüfung der "Keep-Awake"-Funktionalität unter Windows.

Dieses Skript dient ausschließlich Testzwecken. Es ruft die Windows-API-Funktion
`SetThreadExecutionState` mit den Flags `ES_CONTINUOUS`, `ES_SYSTEM_REQUIRED`
und `ES_DISPLAY_REQUIRED` auf. Dies soll verhindern, dass das System in den
Ruhezustand wechselt oder der Bildschirm gesperrt wird.

Das Skript aktiviert den Keep-Awake-Zustand, wartet für eine definierte Dauer
(z.B. 2 Minuten) und setzt den Zustand anschließend wieder zurück. Während der
Wartezeit kann der Benutzer beobachten, ob der PC wie erwartet aktiv bleibt.

**Wichtiger Hinweis:** Dieses Skript ist nur unter Windows funktionsfähig.
"""

import ctypes
import sys
import time

def test_windows_keep_awake_v2():
    """
    Testet die Windows-spezifische "Keep-Awake"-Funktion
    mit ES_SYSTEM_REQUIRED und ES_DISPLAY_REQUIRED.
    Verwendet ES_CONTINUOUS für eine dauerhafte Aktivierung.
    """

    if sys.platform != "win32":
        print(f"Dieses Testskript ist nur für Windows (win32).")
        print(f"Aktuelle Plattform: {sys.platform}")
        print("Skript wird beendet.")
        return

    print("Auf Windows-System erkannt.")

    # --- Definition der Windows-Konstanten ---

    # 0x80000000: Hält den Zustand aktiv, bis er explizit aufgehoben wird.
    ES_CONTINUOUS = 0x80000000

    # 0x00000001: Verhindert den Ruhezustand / Energiesparmodus (System-Idle).
    ES_SYSTEM_REQUIRED = 0x00000001

    # 0x00000002: Verhindert das Abschalten des Displays / Bildschirmschoner.
    # DIES IST DER FLAG, DER WAHRSCHEINLICH GEFEHLT HAT.
    ES_DISPLAY_REQUIRED = 0x00000002

    try:
        kernel32 = ctypes.windll.kernel32
        print("kernel32.dll geladen.")

    except Exception as e:
        print(f"Fehler beim Laden von kernel32.dll: {e}")
        return

    # Kombinierte Flags
    # Fordere an, dass System UND Display aktiv bleiben, und zwar DAUERHAFT.
    keep_awake_flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED

    # Flags zum Zurücksetzen (nur 'dauerhaft' aufheben)
    reset_flags = ES_CONTINUOUS

    try:
        print("\n--- START TEST (Dauer: 2 Minuten) ---")

        # 1. Keep-Awake-Zustand SETZEN
        print(f"[{time.strftime('%H:%M:%S')}] SETZE Keep-Awake: System + Display (0x{keep_awake_flags:X})")
        result = kernel32.SetThreadExecutionState(keep_awake_flags)

        if result == 0:
            print("FEHLER: SetThreadExecutionState konnte nicht gesetzt werden.")
            return

        print("\nSystem wird jetzt für 2 Minuten aktiv gehalten...")
        print("Bitte PC nicht berühren und beobachten.")

        # 2. Warten (in 1-Sekunden-Schritten, um auf Strg+C zu reagieren)
        for i in range(3600):
            time.sleep(1)
            print(f"\rVerbleibende Zeit: {3600 - i}s  ", end="")

        print("\n\n--- TEST ABGESCHLOSSEN ---")
        print("Wenn Ihr PC *nicht* gesperrt wurde und *nicht* in den Ruhezustand ging,")
        print("funktioniert dieser Ansatz.")

    except KeyboardInterrupt:
        print("\nTest vom Benutzer abgebrochen.")

    finally:
        # 3. Keep-Awake-Zustand ZURÜCKSETZEN
        # Wir rufen die Funktion nur mit ES_CONTINUOUS auf, um die
        # ES_SYSTEM_REQUIRED und ES_DISPLAY_REQUIRED Flags zu löschen.
        print(f"\n[{time.strftime('%H:%M:%S')}] SETZE ZURÜCK: Normaler Systemzustand (0x{reset_flags:X})")
        result = kernel32.SetThreadExecutionState(reset_flags)
        if result == 0:
            print("WARNUNG: Der Keep-Awake-Zustand konnte nicht sauber zurückgesetzt werden.")

        print("Skript beendet. Der normale System-Idle-Timer ist jetzt wieder aktiv.")


if __name__ == "__main__":
    test_windows_keep_awake_v2()
