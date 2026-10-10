"""MCP server tests that need no FreeCAD: a fake bridge stands in for the add-on. Run with any Python 3.9+:
    python -m unittest freecad/CadAI/tests/test_mcp_server.py -v
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(os.path.dirname(HERE), "mcp_server", "cadai_mcp.py")
sys.path.insert(0, os.path.dirname(SERVER))

# These suites test the single-program servers; auto mode (default) has its own end-to-end tests.
os.environ.setdefault("CADAI_BACKEND", "freecad")
import cadai_mcp as mcp

TOKEN = "test-token"
MANIFEST = [
    {"name": "measure", "title": "Measure", "description": "Measure objects.", "mutates": False,
     "input_schema": {"type": "object", "properties": {"objects": {"type": "array"}}}},
    {"name": "set_property", "title": "Change a property", "description": "Change one property.", "mutates": True,
     "destructive": True, "idempotent": True, "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_markers", "description": "Read markers.", "mutates": False,
     "input_schema": {"type": "object", "properties": {}}},
]
MODERN = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
          "io.modelcontextprotocol/clientCapabilities": {},
          "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"}}


class FakeBridge:
    """Answers like cadai/bridge.py: /health, /tools, /call."""

    def __init__(self):
        self.calls = []
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _ok(self):
                if self.headers.get("Authorization") != "Bearer " + TOKEN:
                    self._send(401, {"error": "unauthorized"})
                    return False
                return True

            def do_GET(self):
                if not self._ok():
                    return
                if self.path == "/health":
                    self._send(200, {"ok": True, "version": "test"})
                elif self.path == "/tools":
                    self._send(200, MANIFEST)
                else:
                    self._send(404, {})

            def do_POST(self):
                if not self._ok():
                    return
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                bridge.calls.append(req)
                name = req["name"]
                if name == "measure":
                    self._send(200, {"content": json.dumps({"Beam": {"volume_mm3": 20000.0}}), "is_error": False,
                                     "image": None})
                elif name == "get_markers":
                    self._send(200, {"content": json.dumps({"markers": [{"id": 1, "note": "x", "trusted": False}]}),
                                     "is_error": False, "image": None})
                elif name == "capture_view":
                    self._send(200, {"content": "ekte", "is_error": False, "image": "iVBORw0KGgo="})
                else:
                    self._send(200, {"content": f"Bilinmeyen araç: {name}", "is_error": True, "image": None})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.dir = tempfile.mkdtemp()
        with open(os.path.join(self.dir, "bridge.json"), "w", encoding="utf-8") as f:
            json.dump({"url": f"http://127.0.0.1:{self.server.server_address[1]}", "token": TOKEN}, f)

    def __enter__(self):
        self.thread.start()
        os.environ["CADAI_BRIDGE_DIR"] = self.dir
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def rpc(method, params=None, msg_id=1, modern=False):
    params = dict(params or {})
    if modern:
        params["_meta"] = dict(MODERN, **params.get("_meta", {}))
    return mcp.handle({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}, mcp.BridgeClient(timeout=10))


class LegacyClient(unittest.TestCase):
    """Clients that use the initialize handshake (MCP 2025-11-25 and earlier)."""

    def test_initialize_negotiates_a_supported_version(self):
        res = rpc("initialize", {"protocolVersion": "2025-06-18"})["result"]
        self.assertEqual(res["protocolVersion"], "2025-06-18")
        self.assertEqual(res["serverInfo"]["name"], "cadai")
        self.assertIn("get_selection", res["instructions"])
        self.assertEqual(set(res["capabilities"]), {"tools", "resources", "prompts"})
        # an unknown version is answered with our latest legacy one, never echoed back
        self.assertEqual(rpc("initialize", {"protocolVersion": "2099-01-01"})["result"]["protocolVersion"], "2025-11-25")

    def test_notifications_get_no_reply(self):
        self.assertIsNone(mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, None))
        self.assertIsNone(mcp.handle({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                      "params": {"requestId": 3}}, None))

    def test_ping_and_unknown_method(self):
        self.assertEqual(rpc("ping")["result"], {})
        self.assertEqual(rpc("nope/method")["error"]["code"], -32601)

    def test_discover_without_meta_is_rejected(self):
        self.assertEqual(rpc("server/discover")["error"]["code"], -32602)

    def test_invalid_request(self):
        self.assertEqual(mcp.handle({"id": 1, "method": "ping"}, None)["error"]["code"], -32600)


class ModernClient(unittest.TestCase):
    """Stateless MCP 2026-07-28: version and capabilities in every request's _meta."""

    def test_discover(self):
        res = rpc("server/discover", modern=True)["result"]
        self.assertEqual(res["resultType"], "complete")
        self.assertIn("2026-07-28", res["supportedVersions"])
        self.assertIn("2025-11-25", res["supportedVersions"])
        self.assertEqual(res["_meta"]["io.modelcontextprotocol/serverInfo"]["name"], "cadai")
        self.assertIn("tools", res["capabilities"])
        self.assertGreater(res["ttlMs"], 0)
        self.assertIn(res["cacheScope"], ("public", "private"))

    def test_unsupported_version(self):
        err = rpc("tools/list", {"_meta": {"io.modelcontextprotocol/protocolVersion": "1900-01-01"}}, modern=True)["error"]
        self.assertEqual(err["code"], -32022)
        self.assertEqual(err["data"]["requested"], "1900-01-01")
        self.assertIn("2026-07-28", err["data"]["supported"])

    def test_missing_client_capabilities(self):
        res = mcp.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/list",
                          "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}}}, None)
        self.assertEqual(res["error"]["code"], -32602)

    def test_initialize_is_not_a_modern_method(self):
        self.assertEqual(rpc("initialize", modern=True)["error"]["code"], -32601)


class WithBridge(unittest.TestCase):
    def test_tools_have_annotations_and_stable_order(self):
        with FakeBridge():
            for modern in (False, True):
                res = rpc("tools/list", modern=modern)["result"]
                names = [t["name"] for t in res["tools"]]
                self.assertEqual(names, ["freecad_bridge_status", "get_markers", "measure", "set_property"])
                tools = {t["name"]: t for t in res["tools"]}
                self.assertTrue(tools["measure"]["annotations"]["readOnlyHint"])
                self.assertEqual(tools["measure"]["title"], "Measure")
                sp = tools["set_property"]["annotations"]
                self.assertFalse(sp["readOnlyHint"])
                self.assertTrue(sp["destructiveHint"] and sp["idempotentHint"])
                self.assertFalse(sp["openWorldHint"])
                self.assertIn("[Modeli değiştirir]", tools["set_property"]["description"])
                self.assertEqual(tools["get_markers"]["title"], "Get markers")  # derived when the add-on gives none
                self.assertEqual("ttlMs" in res, modern)

    def test_call_returns_text_and_structured_content(self):
        with FakeBridge() as fb:
            res = rpc("tools/call", {"name": "measure", "arguments": {"objects": ["Beam"]}}, modern=True)["result"]
            self.assertFalse(res["isError"])
            self.assertEqual(res["structuredContent"], {"Beam": {"volume_mm3": 20000.0}})
            self.assertEqual(json.loads(res["content"][0]["text"]), res["structuredContent"])
            self.assertEqual(fb.calls[-1], {"name": "measure", "arguments": {"objects": ["Beam"]}})
            img = rpc("tools/call", {"name": "capture_view"})["result"]
            self.assertEqual(img["content"][1], {"type": "image", "data": "iVBORw0KGgo=", "mimeType": "image/png"})
            self.assertNotIn("structuredContent", img)
            bad = rpc("tools/call", {"name": "nope"})["result"]
            self.assertTrue(bad["isError"])
            self.assertNotIn("structuredContent", bad)
            self.assertEqual(rpc("tools/call", {"name": "measure", "arguments": "x"})["error"]["code"], -32602)

    def test_status_tool(self):
        with FakeBridge():
            res = rpc("tools/call", {"name": "freecad_bridge_status"})["result"]
            self.assertFalse(res["isError"])
            self.assertTrue(res["structuredContent"]["ok"])

    def test_resources(self):
        with FakeBridge() as fb:
            uris = [r["uri"] for r in rpc("resources/list", modern=True)["result"]["resources"]]
            self.assertIn("cadai://markers", uris)
            contents = rpc("resources/read", {"uri": "cadai://markers"}, modern=True)["result"]["contents"]
            self.assertEqual(contents[0]["mimeType"], "application/json")
            self.assertFalse(json.loads(contents[0]["text"])["markers"][0]["trusted"])
            self.assertEqual(fb.calls[-1]["name"], "get_markers")
            self.assertEqual(rpc("resources/read", {"uri": "cadai://nope"}, modern=True)["error"]["code"], -32602)

    def test_prompts_never_embed_marker_notes(self):
        names = [p["name"] for p in rpc("prompts/list")["result"]["prompts"]]
        self.assertEqual(names, ["apply_markers", "fem_check", "inspect_model"])
        with FakeBridge() as fb:
            msg = rpc("prompts/get", {"name": "apply_markers", "arguments": {"extra": "önce #2"}})["result"]
            text = msg["messages"][0]["content"]["text"]
            self.assertIn("get_markers", text)
            self.assertIn("önce #2", text)
            self.assertEqual(fb.calls, [], "prompts must not pull (possibly untrusted) marker notes into the prompt")
        self.assertEqual(rpc("prompts/get", {"name": "nope"})["error"]["code"], -32602)


class Offline(unittest.TestCase):
    def setUp(self):
        os.environ["CADAI_BRIDGE_DIR"] = tempfile.mkdtemp()  # no bridge.json: FreeCAD is closed

    def test_tools_fall_back_to_bundled_manifest(self):
        names = {t["name"] for t in rpc("tools/list")["result"]["tools"]}
        self.assertTrue({"freecad_bridge_status", "find_faces", "fem_run"} <= names, names)

    def test_calls_explain_that_freecad_is_closed(self):
        res = rpc("tools/call", {"name": "measure", "arguments": {}})["result"]
        self.assertTrue(res["isError"])
        self.assertIn("FreeCAD", res["content"][0]["text"])
        err = rpc("resources/read", {"uri": "cadai://document"}, modern=True)["error"]
        self.assertEqual(err["code"], -32603)


class StdioProcess(unittest.TestCase):
    def test_round_trip_over_stdio(self):
        env = dict(os.environ, CADAI_BRIDGE_DIR=tempfile.mkdtemp())
        msgs = [
            {"jsonrpc": "2.0", "id": "d", "method": "server/discover", "params": {"_meta": MODERN}},
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "freecad_bridge_status"}},
        ]
        stdin = ("".join(json.dumps(m) + "\n" for m in msgs) + "not json\n").encode("utf-8")
        p = subprocess.run([sys.executable, SERVER], input=stdin, capture_output=True, env=env, timeout=60)
        replies = {m["id"]: m for m in (json.loads(x) for x in p.stdout.decode("utf-8").splitlines() if x.strip())}
        self.assertEqual(set(replies), {"d", 1, 2, 3, None}, p.stderr.decode())
        self.assertEqual(replies["d"]["result"]["resultType"], "complete")
        self.assertEqual(replies[1]["result"]["protocolVersion"], "2025-11-25")
        self.assertGreater(len(replies[2]["result"]["tools"]), 10)
        self.assertTrue(replies[3]["result"]["isError"])
        self.assertEqual(replies[None]["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main(verbosity=2)
