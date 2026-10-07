"""Other open-source programs CadAI drives as separate processes: Blender (render), OpenSCAD, KiCad and a Python
with build123d / CadQuery (code CAD).

They never run inside FreeCAD: CadAI writes an input file, runs the program with a timeout and reads back a file
(STEP, CSG, PNG...). This keeps their licenses separate (GPL), a crash or hang cannot take FreeCAD down, and code
written by the AI for them runs outside FreeCAD's process.

Where a program is found, in order: the path set in VS Code (machine-scope setting, stored in config.json under
"external_tools"), an environment variable, PATH, the usual install folders. The AI can never choose the
executable: tools take no program paths.
"""

import glob
import os
import shutil
import subprocess
import sys

from . import config

PROGRAMS = {
    "blender": {
        "title": "Blender", "env": "CADAI_BLENDER", "names": ["blender"],
        "windows": [r"%ProgramFiles%\Blender Foundation\Blender*\blender.exe",
                    r"%ProgramFiles(x86)%\Steam\steamapps\common\Blender\blender.exe",
                    r"%LOCALAPPDATA%\Programs\Blender Foundation\Blender*\blender.exe"],
        "mac": ["/Applications/Blender.app/Contents/MacOS/Blender"],
        "linux": ["/snap/bin/blender", "/usr/bin/blender", "/opt/blender*/blender"],
        "version_args": ["--version"], "url": "https://www.blender.org/download/",
        "install": {"windows": "winget install BlenderFoundation.Blender", "mac": "brew install --cask blender",
                    "linux": "sudo snap install blender --classic"},
        "used_for": "render (Cycles, gerçekçi görsel ve dönen video)",
    },
    "openscad": {
        "title": "OpenSCAD", "env": "CADAI_OPENSCAD", "names": ["openscad"],
        "windows": [r"%ProgramFiles%\OpenSCAD*\openscad.com", r"%ProgramFiles%\OpenSCAD*\openscad.exe",
                    r"%LOCALAPPDATA%\Programs\OpenSCAD*\openscad.com"],
        "mac": ["/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD"],
        "linux": ["/usr/bin/openscad", "/snap/bin/openscad"],
        "version_args": ["--version"], "url": "https://openscad.org/downloads.html",
        "install": {"windows": "winget install OpenSCAD.OpenSCAD", "mac": "brew install --cask openscad",
                    "linux": "sudo apt install openscad"},
        "used_for": "OpenSCAD kodunu (.scad) FreeCAD katısına çevirme",
    },
    "kicad_cli": {
        "title": "KiCad (kicad-cli)", "env": "CADAI_KICAD_CLI", "names": ["kicad-cli"],
        "windows": [r"%ProgramFiles%\KiCad\*\bin\kicad-cli.exe"],
        "mac": ["/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"],
        "linux": ["/usr/bin/kicad-cli"],
        "version_args": ["version"], "url": "https://www.kicad.org/download/",
        "install": {"windows": "winget install KiCad.KiCad", "mac": "brew install --cask kicad",
                    "linux": "sudo apt install kicad"},
        "used_for": "KiCad devre kartını (.kicad_pcb) bileşenleriyle 3B olarak içe alma",
    },
    "codecad_python": {
        "title": "Python + build123d / CadQuery", "env": "CADAI_CODECAD_PYTHON", "names": ["python3", "python"],
        # CadAI's own environment first (VS Code's "Kurulum…" creates it), then the usual Pythons
        "windows": [r"%LOCALAPPDATA%\CadAI\codecad\Scripts\python.exe",
                    r"%LOCALAPPDATA%\Programs\Python\Python3*\python.exe", r"%SystemDrive%\Python3*\python.exe"],
        "mac": ["~/.cadai/codecad/bin/python", "/opt/homebrew/bin/python3", "/usr/local/bin/python3"],
        "linux": ["~/.cadai/codecad/bin/python", "/usr/bin/python3"],
        "version_args": ["-c", "import sys; print(sys.version.split()[0])"],
        "url": "https://build123d.readthedocs.io/en/latest/installation.html",
        "install": {
            "windows": r'python -m venv "%LOCALAPPDATA%\CadAI\codecad" && '
                       r'"%LOCALAPPDATA%\CadAI\codecad\Scripts\python.exe" -m pip install build123d',
            "mac": "python3 -m venv ~/.cadai/codecad && ~/.cadai/codecad/bin/python -m pip install build123d",
            "linux": "python3 -m venv ~/.cadai/codecad && ~/.cadai/codecad/bin/python -m pip install build123d"},
        "used_for": "build123d / CadQuery Python koduyla parça üretme",
    },
}

_PROBE = ("import importlib.util as u, json, sys; print(json.dumps({'python': sys.version.split()[0], "
          "'build123d': bool(u.find_spec('build123d')), 'cadquery': bool(u.find_spec('cadquery'))}))")
_found = {}


class ExternalError(Exception):
    """A program is missing, failed or timed out; the message is meant for the user / the AI."""


def _platform():
    return "windows" if os.name == "nt" else "mac" if sys.platform == "darwin" else "linux"


def configured_paths():
    return dict(config.load().get("external_tools") or {})


def configure(paths):
    """Save program paths set in VS Code ({"blender": "C:/.../blender.exe", ...}); empty value = auto-detect."""
    cfg = config.load()
    current = dict(cfg.get("external_tools") or {})
    for key, value in (paths or {}).items():
        if key not in PROGRAMS:
            continue
        value = str(value or "").strip().strip('"')
        if value:
            current[key] = value
        else:
            current.pop(key, None)
    cfg["external_tools"] = current
    config.save(cfg)
    _found.clear()
    return status()


def _candidates(key):
    spec = PROGRAMS[key]
    on_path = [hit for hit in (shutil.which(name) for name in spec["names"]) if hit]
    folders = []
    for pattern in spec.get(_platform(), []):
        pattern = os.path.expanduser(os.path.expandvars(pattern))
        folders += sorted(glob.glob(pattern), reverse=True)  # newest version first
    # a Python on PATH is usually the system one: CadAI's own code-CAD environment wins over it
    yield from (folders + on_path) if key == "codecad_python" else (on_path + folders)


def _python_info(exe):
    try:
        p = run([exe, "-c", _PROBE], timeout=60)
        import json

        return json.loads(p.stdout.strip().splitlines()[-1])
    except (ExternalError, ValueError, IndexError):
        return None


def find(key, required=False):
    """Absolute path of the program, or None. For codecad_python only a Python that has build123d or CadQuery."""
    if key in _found and (_found[key] is None or os.path.isfile(_found[key])):
        path = _found[key]
    else:
        spec = PROGRAMS[key]
        explicit = configured_paths().get(key) or os.environ.get(spec["env"], "")
        if explicit:
            path = explicit if os.path.isfile(explicit) else None
        else:
            path = None
            seen = set()
            for cand in _candidates(key):
                cand = os.path.abspath(cand)
                if cand in seen or not os.path.isfile(cand):
                    continue
                seen.add(cand)
                if key == "codecad_python":
                    info = _python_info(cand)
                    if not info or not (info["build123d"] or info["cadquery"]):
                        continue
                path = cand
                break
        _found[key] = path
    if path is None and required:
        raise ExternalError(missing_message(key))
    return path


def missing_message(key):
    spec = PROGRAMS[key]
    explicit = configured_paths().get(key) or os.environ.get(spec["env"], "")
    where = f" Ayarlanan yol bulunamadı: {explicit}." if explicit else ""
    return (f"{spec['title']} bulunamadı ({spec['used_for']}).{where} Kurulum: `{spec['install'][_platform()]}` "
            f"ya da {spec['url']}. Kurulu ama bulunamıyorsa yolunu VS Code ayarlarında (CadAI → Harici araçlar) ya da "
            f"{spec['env']} ortam değişkeninde verin.")


def run(args, timeout, cwd=None, env=None):
    """Run a program without a shell or a console window; raise ExternalError on timeout or a missing program."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run([str(a) for a in args], cwd=cwd, env=env, capture_output=True, timeout=timeout,
                              creationflags=flags, stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                              errors="replace")
    except subprocess.TimeoutExpired:
        raise ExternalError(f"{os.path.basename(str(args[0]))} {timeout} sn içinde bitmedi ve durduruldu.")
    except OSError as e:
        raise ExternalError(f"{os.path.basename(str(args[0]))} çalıştırılamadı: {e}")


def tail(text, n=3000):
    text = (text or "").strip()
    return text if len(text) <= n else "…" + text[-n:]


def version(key, path):
    if key == "codecad_python":
        info = _python_info(path) or {}
        libs = [n for n in ("build123d", "cadquery") if info.get(n)]
        return f"Python {info.get('python', '?')}" + (f" ({', '.join(libs)})" if libs else "")
    try:
        p = run([path] + PROGRAMS[key]["version_args"], timeout=60)
    except ExternalError:
        return None
    lines = [ln.strip() for ln in (p.stdout + "\n" + p.stderr).splitlines() if ln.strip()]
    return lines[0][:80] if lines else None


def status():
    out = {}
    for key, spec in PROGRAMS.items():
        path = find(key)
        item = {"title": spec["title"], "found": bool(path), "used_for": spec["used_for"]}
        if path:
            item.update(path=path, version=version(key, path))
        else:
            item.update(install=spec["install"][_platform()], url=spec["url"])
        if configured_paths().get(key):
            item["configured"] = True
        out[key] = item
    return out
