"""Copy fusion/CadAI and the shared core into the installed add-in and reload it in the running Fusion (dev helper)."""

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import live_smoke

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
base = os.environ.get("APPDATA") or os.path.expanduser("~/Library/Application Support")
TARGET = os.path.join(base, "Autodesk", "Autodesk Fusion", "API", "AddIns", "CadAI")


def main():
    if not os.path.isdir(TARGET):
        raise SystemExit(f"Kurulu Fusion eklentisi yok: {TARGET}")
    for name in ("adapter.py", "CadAI.py", "CadAI.manifest"):
        shutil.copy2(os.path.join(ROOT, "fusion", "CadAI", name), TARGET)
    core = os.path.join(ROOT, "freecad", "CadAI", "cadai_core")
    for name in os.listdir(core):
        if name.endswith(".py"):
            shutil.copy2(os.path.join(core, name), os.path.join(TARGET, "cadai_core"))
    live = live_smoke.Live(live_smoke.find_session())
    print(live.ui("reload_addon"))


if __name__ == "__main__":
    main()
