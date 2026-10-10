"""Small shared adapter contract. No CAD imports; targets are checked on the CAD thread."""

import json
import os

UNITS = {"length": "mm", "area": "mm2", "volume": "mm3", "angle": "deg",
         "force": "N", "stress": "MPa", "mass": "kg", "density": "kg/m3"}


def sessions_dir():
    override = os.environ.get("CADAI_SESSIONS_DIR")
    if override:
        return override
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.local/share")
    if os.sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    return os.path.join(base, "CadAI", "sessions")


def pid_alive(pid):
    """True if the process exists. On Windows os.kill(pid, 0) would terminate it, so query the handle instead."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except PermissionError:
            return True
        except OSError:
            return False
        return True
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5  # access denied: the process exists
    try:
        code = ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        kernel.CloseHandle(ctypes.c_void_p(handle))


def prune_sessions(directory=None):
    """Remove registrations left by processes that exited without stopping their bridge."""
    directory = directory or sessions_dir()
    try:
        entries = os.listdir(directory)
    except OSError:
        return []
    removed = []
    for entry in entries:
        path = os.path.join(directory, entry, "bridge.json")
        try:
            with open(path, encoding="utf-8") as f:
                pid = json.load(f).get("pid")
        except (OSError, ValueError, AttributeError):
            continue
        if pid_alive(pid):
            continue
        try:
            os.remove(path)
            os.rmdir(os.path.dirname(path))
            removed.append(entry)
        except OSError:
            pass
    return removed


def private_write(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(text)


# UI reads that refresh the view. They run because the model changed, so they must never be refused for it.
READ_ONLY_UI = frozenset(("tree", "scene", "selection", "markers"))


def check_target(target, current, required=False, mutates=True):
    """A request must reach the session and document it was made for. The revision guard (no change based on an
    outdated view or marker) applies to changes only; reads always return the current model."""
    if target is None and not required:
        return  # legacy FreeCAD callers
    if not mutates and isinstance(target, dict):
        target = {k: v for k, v in target.items() if k != "revision"}
    if not isinstance(target, dict):
        raise ValueError("CAD hedefi eksik. Önce oturumu ve belgeyi seçin.")
    for key in ("backend_id", "session_id", "document_id"):
        if key not in target or target[key] != current[key]:
            raise ValueError("CAD oturumu veya belge değişti. Görünümü yenileyip hedefi yeniden seçin.")
    if "revision" in target and target["revision"] != current["revision"]:
        raise ValueError("Model değişti; eski seçim/işaretle işlem yapılmadı. Görünümü yenileyin.")


def capabilities(backend_id, version, registry, actions, limitations=()):
    return {"protocol_version": 1, "backend_id": backend_id, "adapter_version": version, "units": dict(UNITS),
            "tools": [t.name for t in registry.specs()], "ui_actions": sorted(actions),
            "limitations": list(limitations)}


def tool_response(result, context=None):
    out = {"content": result.content, "is_error": result.is_error, "image": result.image_png_b64}
    if context is not None:
        out["context"] = context
    return out


def error_response(path, error, context=None):
    if path == "/ui":
        out = {"ok": False, "error": str(error)}
    else:
        out = {"content": str(error), "is_error": True, "image": None}
    if context is not None:
        out["context"] = context
    return out


class Result:
    def __init__(self, value, is_error=False):
        self.content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        self.is_error = is_error
        self.image_png_b64 = None
