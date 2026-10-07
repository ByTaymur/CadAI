"""Extra Python packages for FreeCAD's own Python (today: the Anthropic SDK for Claude in the side panel).

FreeCAD's Python lives under Program Files on Windows, so `python.exe -m pip install anthropic` needs administrator
rights there. CadAI installs into its own folder in the FreeCAD user directory instead (`pip install --target`) and
puts that folder on sys.path: no administrator rights, nothing written into FreeCAD's installation, and removing the
folder undoes it.
"""

import os
import subprocess
import sys

from . import config

PACKAGES = {"anthropic": "anthropic"}  # import name -> pip requirement


def lib_dir():
    return os.path.join(os.path.dirname(config.config_path()), "pylib")


def ensure_path():
    """Put CadAI's package folder on sys.path (after FreeCAD's own packages, so it never shadows them)."""
    path = lib_dir()
    if os.path.isdir(path) and path not in sys.path:
        sys.path.append(path)


def freecad_python():
    """FreeCAD's Python interpreter (in the GUI, sys.executable is FreeCAD itself)."""
    exe = sys.executable or ""
    if os.path.basename(exe).lower().startswith("python"):
        return exe
    try:
        import FreeCAD

        home = FreeCAD.getHomePath()
    except Exception:
        home = os.path.dirname(os.path.dirname(exe))
    for cand in (os.path.join(home, "bin", "python.exe"), os.path.join(home, "bin", "python3"),
                 os.path.join(home, "bin", "python")):
        if os.path.isfile(cand):
            return cand
    return exe


def install_command(name):
    return [freecad_python(), "-m", "pip", "install", "--upgrade", "--target", lib_dir(), PACKAGES[name]]


def install(name, timeout=600):
    """pip-install a package into CadAI's folder. Returns (ok, last lines of pip's output)."""
    os.makedirs(lib_dir(), exist_ok=True)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        p = subprocess.run(install_command(name), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=flags, stdin=subprocess.DEVNULL,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8", PIP_DISABLE_PIP_VERSION_CHECK="1"))
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    ensure_path()
    import importlib

    importlib.invalidate_caches()  # the folder's contents changed after Python may have looked at it
    out = (p.stdout + "\n" + p.stderr).strip()
    return p.returncode == 0, out[-1500:]
