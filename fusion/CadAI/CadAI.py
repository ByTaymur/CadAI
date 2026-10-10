"""Fusion entry point. CAD API access is confined to run/stop and custom event handlers."""

import importlib
import json
import os
import sys
import threading
import traceback
import uuid

import adsk.core

HERE = os.path.dirname(os.path.abspath(__file__))
# Source checkout and standalone bundles use the same CAD-independent core.
if not os.path.isdir(os.path.join(HERE, "cadai_core")):
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "freecad", "CadAI")))
else:
    sys.path.insert(0, HERE)

VERSION = "0.19.3"


def installed_version():
    """Version of the files on disk (may be newer than the code Fusion loaded at startup)."""
    try:
        with open(os.path.join(HERE, "CadAI.manifest"), encoding="utf-8") as stream:
            return json.load(stream)["version"]
    except (OSError, ValueError, KeyError):
        return VERSION
_runtime = None


class MainThreadCaller:
    """Cancel queued timed-out work; running work reports that its outcome may need inspection."""

    def __init__(self, app):
        self.app = app
        self.event_id = "cadai." + uuid.uuid4().hex
        self.lock = threading.Lock()
        self.pending = {}
        self.closed = False
        owner = self

        class Handler(adsk.core.CustomEventHandler):
            def notify(self, args):
                key = args.additionalInfo
                with owner.lock:
                    box = owner.pending.get(key)
                    if box is None or box["state"] != "queued":
                        return
                    box["state"] = "running"
                try:
                    box["result"] = box["fn"]()
                except Exception as e:
                    box["error"] = e
                finally:
                    box["done"].set()

        self.handler = Handler()
        self.event = app.registerCustomEvent(self.event_id)
        self.event.add(self.handler)

    def __call__(self, fn, timeout=300):
        key = uuid.uuid4().hex
        box = {"fn": fn, "state": "queued", "done": threading.Event()}
        with self.lock:
            if self.closed:
                raise RuntimeError("Fusion köprüsü kapandı.")
            self.pending[key] = box
        try:
            self.app.fireCustomEvent(self.event_id, key)
            if not box["done"].wait(timeout):
                with self.lock:
                    queued = box["state"] == "queued"
                    if queued:
                        box["state"] = "cancelled"
                detail = ("Kuyruktaki işlem iptal edildi." if queued else
                          "İşlem başlamış olabilir; yeniden denemeden modeli inceleyin.")
                raise TimeoutError("Fusion ana iş parçacığı zaman aşımı. " + detail)
            if "error" in box:
                raise box["error"]
            return box.get("result")
        finally:
            with self.lock:
                self.pending.pop(key, None)

    def close(self):
        with self.lock:
            self.closed = True
            for box in self.pending.values():
                if box["state"] == "queued":
                    box["state"] = "cancelled"
                    box["error"] = RuntimeError("Fusion köprüsü durduruldu; işlem uygulanmadı.")
                    box["done"].set()
        self.event.remove(self.handler)
        self.app.unregisterCustomEvent(self.event_id)


def run(context):
    global _runtime
    if _runtime is not None:
        return
    app = adsk.core.Application.get()
    caller = bridge = None
    info_dir = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~/Library/Application Support"), "CadAI", "fusion")
    try:
        # Fusion keeps imported modules after Stop/Run; reload the shared core so an updated install is really used.
        import cadai_core.bridge
        import cadai_core.contract
        import cadai_core.registry

        for module in (cadai_core.contract, cadai_core.registry, cadai_core.bridge):
            importlib.reload(module)
        from cadai_core.bridge import Bridge
        from cadai_core.contract import sessions_dir

        from . import adapter as adapter_module

        # Fusion can retain imported sibling modules after Stop/Run. Load the updated adapter source on every start.
        adapter_module = importlib.reload(adapter_module)
        Adapter = adapter_module.Adapter

        info_dir = os.path.join(os.path.dirname(sessions_dir()), "fusion")
        caller = MainThreadCaller(app)
        current = {}

        def reload_addon():
            """Development reload on the main thread: new adapter code, same bridge, session and document IDs."""
            import cadai_core.contract
            import cadai_core.registry

            importlib.reload(cadai_core.contract)
            importlib.reload(cadai_core.registry)
            module = importlib.reload(sys.modules[Adapter.__module__])
            old = current["adapter"]
            version = installed_version()
            new = module.Adapter(app, info_dir, version, reload=reload_addon)
            for key in module.Adapter.KEPT_STATE:
                if hasattr(old, key):
                    setattr(new, key, getattr(old, key))
            current["adapter"] = new
            bridge.registry = new.registry
            bridge.adapter_version = version
            # Session discovery (VS Code, MCP) reads the version from the registration files: keep them current.
            for path in (bridge.info_path, getattr(bridge, "registration_path", None)):
                try:
                    with open(path, encoding="utf-8") as stream:
                        published = json.load(stream)
                    if published.get("token") == bridge.token:
                        cadai_core.contract.private_write(path, json.dumps(dict(published, version=version), indent=2))
                except (OSError, ValueError, TypeError):
                    pass
            return {"reloaded": True, "same_session": True, "version": version,
                    "tools": [t.name for t in new.registry.specs()]}

        adapter = current["adapter"] = Adapter(app, info_dir, VERSION, reload=reload_addon)
        bridge = Bridge(adapter.registry, None, port=47840, info_dir=info_dir, backend_id="fusion", adapter_version=VERSION,
                        # A busy Fusion (large assembly, open dialog) can need more than a few seconds per event.
                        session=lambda: caller(current["adapter"].context, timeout=15),
                        version=lambda: caller(current["adapter"].version, timeout=3),
                        execute=lambda path, req: caller(lambda: current["adapter"].execute(path, req)),
                        capabilities=lambda: current["adapter"].capabilities(), registration_dir=sessions_dir())
        adapter.session_id = bridge.session_id
        bridge.start()
        _runtime = (caller, bridge)
        error_file = os.path.join(info_dir, "startup-error.log")
        if os.path.exists(error_file):
            os.remove(error_file)
    except Exception:
        detail = traceback.format_exc()
        if caller is not None:
            caller.close()
        if bridge is not None:
            bridge.stop()
        _runtime = None
        os.makedirs(info_dir, exist_ok=True)
        error_file = os.path.join(info_dir, "startup-error.log")
        with open(error_file, "w", encoding="utf-8") as stream:
            stream.write(detail)
        app.userInterface.messageBox("CadAI Fusion bağlantısı başlatılamadı.\n\n" + detail + "\nHata kaydı: " + error_file)
        raise


def stop(context):
    global _runtime
    if _runtime is not None:
        caller, bridge = _runtime
        caller.close()
        bridge.stop()
        _runtime = None
