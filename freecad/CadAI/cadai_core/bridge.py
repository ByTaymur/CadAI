"""CAD-independent local bridge transport. All CAD calls are delegated to adapter callbacks.

HTTP on 127.0.0.1 only, bearer token required. Connection info goes to <FreeCAD user dir>/CadAI/bridge.json.
  GET  /health   -> {"ok": true, ...}
  GET  /version  -> {"doc", "sel", "mk", "hist"}   change counters, cheap to poll
  GET  /tools    -> [{"name", "description", "input_schema", "mutates"}]
  POST /call     {"name", "arguments"} -> {"content", "is_error", "image"}     AI tools
  POST /ui       {"action", "args"}    -> {"ok", "result"} | {"ok": false, "error"}   extension UI actions
"""

import hmac
import json
import os
import secrets
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .contract import private_write, prune_sessions

DEFAULT_PORT = 47800
MAX_BODY_BYTES = 8 * 1024 * 1024  # requests are small JSON; refuse anything absurd


class _ExclusiveServer(ThreadingHTTPServer):
    """On Windows SO_REUSEADDR lets a second process bind the same port; two FreeCADs would then share 47800
    and requests would reach the wrong one. Bind exclusively so the second instance moves to the next port."""

    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address):
        # The client hung up before we answered (VS Code request timed out while the GUI thread was busy, window
        # reloaded, agent cancelled a tool). Nothing to answer; the default handler would dump a traceback into
        # FreeCAD's report view for every such request.
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


def tool_manifest(registry):
    return [t.manifest() for t in registry.specs()]


class Bridge:
    def __init__(self, registry, dispatch, port=DEFAULT_PORT, info_dir=None, ui=None, version=None,
                 adapter_version="", backend_id="", session=None, execute=None, capabilities=None, writer=None,
                 registration_dir=None):
        """dispatch(name, args) -> ToolResult and ui(action, args) -> object must run where FreeCAD expects it
        (the GUI thread). version() -> dict is called on the HTTP thread and must be cheap and thread-safe."""
        self.registry = registry
        self.dispatch = dispatch
        self.ui = ui
        self.version = version or (lambda: {})
        self.port = port
        self.info_dir = info_dir
        self.adapter_version = adapter_version
        self.backend_id = backend_id
        self.session_id = secrets.token_hex(16)
        self.session = session
        self.execute = execute
        self.capabilities = capabilities or (lambda: {})
        self.writer = writer or private_write
        self.registration_dir = registration_dir
        self.info_path = os.path.join(self.info_dir, "bridge.json")
        self.token = secrets.token_urlsafe(24)
        self.server = None
        self.thread = None

    def start(self):
        handler = self._handler_class()
        last_error = None
        for port in range(self.port, self.port + 20):
            try:
                self.server = _ExclusiveServer(("127.0.0.1", port), handler)
                self.port = self.server.server_address[1]
                break
            except OSError as e:
                last_error = e
        if self.server is None:
            raise RuntimeError(f"Köprü için boş port bulunamadı: {last_error}")
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name="cadai-bridge", daemon=True)
        self.thread.start()
        os.makedirs(self.info_dir, exist_ok=True)
        self.writer(self.info_path, json.dumps({"url": f"http://127.0.0.1:{self.port}", "token": self.token,
                                                  "pid": os.getpid(), "version": self.adapter_version,
                                                  "backend_id": self.backend_id, "session_id": self.session_id,
                                                  "protocol_version": 1 if self.session and self.execute else 0}, indent=2))
        with open(os.path.join(self.info_dir, "tools_manifest.json"), "w", encoding="utf-8") as f:
            json.dump(tool_manifest(self.registry), f, indent=1, ensure_ascii=False)
        if self.registration_dir:
            prune_sessions(self.registration_dir)
            self.registration_path = os.path.join(self.registration_dir, self.session_id, "bridge.json")
            os.makedirs(os.path.dirname(self.registration_path), exist_ok=True)
            with open(self.info_path, encoding="utf-8") as f:
                self.writer(self.registration_path, f.read())
        return self

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        try:
            with open(self.info_path, encoding="utf-8") as f:
                is_ours = json.load(f).get("token") == self.token
            if is_ours:
                os.remove(self.info_path)
        except (OSError, ValueError):
            pass
        if self.registration_dir:
            try:
                os.remove(self.registration_path)
                os.rmdir(os.path.dirname(self.registration_path))
            except OSError:
                pass

    @property
    def running(self):
        return self.server is not None

    def _handler_class(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, payload):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self):
                # DNS-rebinding guard: a browser tricked into talking to us would send a foreign Host header
                host = (self.headers.get("Host") or "").split(":")[0]
                if host not in ("127.0.0.1", "localhost"):
                    self._send(403, {"error": "forbidden host"})
                    return False
                given = self.headers.get("Authorization") or ""
                if hmac.compare_digest(given.encode("utf-8"), f"Bearer {bridge.token}".encode()):
                    return True
                self._send(401, {"error": "unauthorized"})
                return False

            def _body(self):
                length = int(self.headers.get("Content-Length", 0))
                if length < 0 or length > MAX_BODY_BYTES:
                    raise ValueError(f"body too large ({length} bytes)")
                return json.loads(self.rfile.read(length).decode("utf-8"))

            def do_GET(self):
                if not self._authorized():
                    return
                try:
                    self._get()
                except (BrokenPipeError, ConnectionResetError):
                    raise
                except Exception as e:
                    self._send(500, {"error": f"{type(e).__name__}: {e}"})

            def _get(self):
                if self.path == "/health":
                    self._send(200, {"ok": True, "version": bridge.adapter_version, "pid": os.getpid(),
                                      "backend_id": bridge.backend_id, "session_id": bridge.session_id,
                                      "protocol_version": 1 if bridge.session and bridge.execute else 0})
                elif self.path == "/version":
                    self._send(200, bridge.version())
                elif self.path == "/session" and bridge.session is not None:
                    self._send(200, bridge.session())
                elif self.path == "/capabilities":
                    self._send(200, bridge.capabilities())
                elif self.path == "/tools":
                    self._send(200, tool_manifest(bridge.registry))
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                if not self._authorized():
                    return
                try:
                    req = self._body()
                    if not isinstance(req, dict):
                        raise ValueError("request must be an object")
                    if not isinstance(req.get("arguments", {}), dict) or not isinstance(req.get("args", {}), dict):
                        raise ValueError("arguments must be an object")
                except ValueError as e:
                    self._send(400, {"error": f"bad request: {e}"})
                    return
                if bridge.execute is not None and self.path in ("/call", "/ui"):
                    try:
                        response = bridge.execute(self.path, req)
                    except Exception as e:
                        self._send(500, {"error": f"{type(e).__name__}: {e}"})
                        return
                    self._send(200, response)
                    return
                if self.path == "/call":
                    if "name" not in req:
                        self._send(400, {"error": "name missing"})
                        return
                    res = bridge.dispatch(req["name"], req.get("arguments") or {})
                    self._send(200, {"content": res.content, "is_error": res.is_error, "image": res.image_png_b64})
                elif self.path == "/ui" and bridge.ui is not None:
                    try:
                        payload = {"ok": True, "result": bridge.ui(req.get("action"), req.get("args") or {})}
                    except Exception as e:
                        payload = {"ok": False, "error": str(e)}
                    self._send(200, payload)  # outside the try: a dropped client must not get a second reply
                else:
                    self._send(404, {"error": "not found"})

        return Handler


