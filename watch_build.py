#!/usr/bin/env python3
"""Watch campus_auto_login.py and auto-rebuild exe on change."""
import subprocess
import sys
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

SCRIPT_DIR = Path(__file__).resolve().parent
TARGET = SCRIPT_DIR / "campus_auto_login.py"
SPEC = SCRIPT_DIR / "campus_auto_login.spec"
DEBOUNCE_SEC = 2


class RebuildHandler(FileSystemEventHandler):
    def __init__(self):
        self._last_trigger = 0.0

    def on_modified(self, event):
        if Path(event.src_path).resolve() != TARGET:
            return
        now = time.time()
        if now - self._last_trigger < DEBOUNCE_SEC:
            return
        self._last_trigger = now
        print(f"\n[watch_build] Change detected, rebuilding exe ...", flush=True)
        ret = subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--clean", str(SPEC)],
            cwd=str(SCRIPT_DIR),
        )
        if ret.returncode == 0:
            print(f"[watch_build] Build succeeded: {SCRIPT_DIR / 'dist' / 'campus_auto_login.exe'}", flush=True)
        else:
            print(f"[watch_build] Build FAILED (exit {ret.returncode})", flush=True)


def main():
    handler = RebuildHandler()
    observer = Observer()
    observer.schedule(handler, str(SCRIPT_DIR), recursive=False)
    observer.start()
    print(f"[watch_build] Watching {TARGET} for changes ...", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
