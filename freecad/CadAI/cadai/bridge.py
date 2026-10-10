"""FreeCAD bridge adapter; shared HTTP transport lives in cadai_core.bridge."""

import json
import os

from cadai_core.bridge import DEFAULT_PORT
from cadai_core.bridge import MAX_BODY_BYTES as MAX_BODY_BYTES
from cadai_core.bridge import Bridge as CoreBridge
from cadai_core.bridge import tool_manifest as tool_manifest

from . import __version__, config

_bridge = None


def default_info_dir():
    return os.path.dirname(config.config_path())


class Bridge(CoreBridge):
    def __init__(self, registry, dispatch, port=DEFAULT_PORT, info_dir=None, ui=None, version=None, **kwargs):
        super().__init__(registry, dispatch, port, info_dir or default_info_dir(), ui, version,
                         adapter_version=__version__, backend_id="freecad", writer=config.write_private, **kwargs)


# ---------------- FreeCAD GUI integration ----------------

def _gui_caller():
    """Returns call(fn) that runs fn() on the GUI thread and returns its result (or re-raises its exception)."""
    from PySide import QtCore

    class Caller(QtCore.QObject):
        request = QtCore.Signal(object)

        def __init__(self):
            super().__init__()
            self.request.connect(self._handle, QtCore.Qt.BlockingQueuedConnection)

        def __call__(self, fn):
            box = {"fn": fn}
            self.request.emit(box)
            if "error" in box:
                raise box["error"]
            return box.get("result")

        @QtCore.Slot(object)
        def _handle(self, box):
            try:
                box["result"] = box["fn"]()
            except Exception as e:
                box["error"] = e

    return Caller()


def _run_tool_logged(registry, name, args):
    import FreeCADGui as Gui
    from PySide import QtWidgets

    from .gui import panel
    from .tools import ToolError, ToolResult

    # resolve aliases ("create_box") and argument spellings first: approval must see the tool and code that will run
    name = registry.resolve_name(name) or name
    tool = registry.get(name)
    if tool is not None:
        try:
            args = registry.normalized_args(name, args)
        except ToolError as e:
            return ToolResult(str(e), is_error=True)
    log = panel._panel._append if panel._panel is not None else None
    if log:
        log("VS Code → araç", f"{name} {json.dumps(args, ensure_ascii=False)[:300]}", "#86a")
    if tool is not None and tool.mutates and config.load().get("bridge_approval"):
        answer = QtWidgets.QMessageBox.question(
            Gui.getMainWindow(), "CadAI: VS Code değişiklik istiyor", f"{name}\n{json.dumps(args, ensure_ascii=False)[:1500]}")
        if answer != QtWidgets.QMessageBox.Yes:
            return ToolResult("Kullanıcı FreeCAD'de reddetti.", is_error=True)
    from . import history

    with history.source(f"AI · {name}"):
        result = registry.run(name, args)
    if log:
        log("← hata" if result.is_error else "← sonuç", result.content[:300], "#c33" if result.is_error else "#86a")
    if tool is not None and tool.mutates:
        Gui.updateGui()
    return result


def start_gui_bridge():
    global _bridge
    if _bridge is not None and _bridge.running:
        return _bridge
    from . import ui_actions
    from .tools import ToolResult, build_registry

    registry = build_registry()
    call = _gui_caller()

    from cadai_core.contract import READ_ONLY_UI, capabilities, check_target, error_response, sessions_dir, tool_response

    from .session import document_context

    def context():
        return dict(document_context(), backend_id="freecad", session_id=_bridge.session_id)

    def execute_on_gui(path, req):
        current = context()
        try:
            if path == "/call":
                tool = registry.get(req.get("name")) if isinstance(req.get("name"), str) else None
                mutates = tool is None or getattr(tool, "mutates", True)
            else:
                mutates = req.get("action") not in READ_ONLY_UI
            check_target(req.get("target"), current, mutates=mutates)
            if path == "/call":
                if not isinstance(req.get("name"), str):
                    raise ValueError("name missing")
                return tool_response(_run_tool_logged(registry, req["name"], req.get("arguments", {})), context())
            result = ui_actions.run(req.get("action"), req.get("args", {}))
            return {"ok": True, "result": result, "context": context()}
        except Exception as e:
            return error_response(path, e, current)

    def execute(path, req):
        try:
            return call(lambda: execute_on_gui(path, req))
        except Exception as e:
            return error_response(path, e)

    def dispatch(name, args):
        try:
            return call(lambda: _run_tool_logged(registry, name, args))
        except Exception as e:
            return ToolResult(f"{type(e).__name__}: {e}", is_error=True)

    def ui(action, args):
        return call(lambda: ui_actions.run(action, args))

    from . import history

    ui_actions.install_observers()
    port = int(config.load().get("bridge_port", DEFAULT_PORT))
    _bridge = Bridge(registry, dispatch, port, ui=ui,
                     version=lambda: dict(ui_actions.STATE, hist=history.STATE["done"]),
                     session=lambda: call(context), execute=execute,
                     capabilities=lambda: capabilities("freecad", __version__, registry, ui_actions.ACTIONS),
                     registration_dir=sessions_dir())
    _bridge.start()
    _bridge._caller = call  # keep the QObject alive
    import FreeCAD

    FreeCAD.Console.PrintMessage(f"CadAI köprüsü açık: http://127.0.0.1:{_bridge.port} (VS Code / MCP için)\n")
    return _bridge


def stop_gui_bridge():
    global _bridge
    if _bridge is not None:
        _bridge.stop()
        _bridge = None
    try:
        from . import ui_actions

        ui_actions.uninstall_observers()
    except Exception:
        pass


def is_running():
    return _bridge is not None and _bridge.running


def autostart():
    if config.load().get("bridge_autostart", True):
        start_gui_bridge()
    from . import history

    if history.settings()["enabled"]:
        history.install()  # optional design history: every change becomes a git commit
