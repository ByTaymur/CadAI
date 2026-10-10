"""Auto mode (default): one `cadai` MCP server follows whichever CAD program runs (FreeCAD or Fusion).

Runs the real server as a subprocess against fake FreeCAD/Fusion bridges in an isolated sessions directory.
    python -m unittest freecad/CadAI/tests/test_mcp_auto.py
"""

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(os.path.dirname(HERE), "mcp_server", "cadai_mcp.py")


class FakeCAD:
    """Minimal protocol-1 bridge for one CAD program; records the calls it receives."""

    def __init__(self, sessions_dir, backend, tools):
        self.backend, self.tools, self.calls = backend, tools, []
        self.session_id = backend + "-session"
        self.context = {"backend_id": backend, "session_id": self.session_id, "document_id": backend + "-doc",
                        "document_name": backend.upper() + " part", "revision": 1}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, value):
                body = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.headers.get("Authorization") != "Bearer tok-" + backend:
                    return self.send_error(401)
                self.send({"/health": {"ok": True, "version": "9.9", "backend_id": backend,
                                       "session_id": fake.session_id, "protocol_version": 1},
                           "/session": fake.context,
                           "/tools": [{"name": t, "description": t, "input_schema": {"type": "object"},
                                       "mutates": False} for t in fake.tools]}[self.path])

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.calls.append(req)
                wrong = req.get("target", {}).get("session_id") != fake.session_id
                self.send({"content": json.dumps({"backend": backend}), "is_error": wrong, "context": fake.context})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.dir = os.path.join(sessions_dir, self.session_id)
        os.makedirs(self.dir)
        with open(os.path.join(self.dir, "bridge.json"), "w", encoding="utf-8") as f:
            json.dump({"url": f"http://127.0.0.1:{self.server.server_address[1]}", "token": "tok-" + backend,
                       "pid": os.getpid(), "backend_id": backend, "session_id": self.session_id,
                       "protocol_version": 1}, f)

    def close(self):
        os.remove(os.path.join(self.dir, "bridge.json"))
        os.rmdir(self.dir)
        self.server.shutdown()
        self.server.server_close()


class AutoServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sessions = os.path.join(self.tmp.name, "CadAI", "sessions")
        os.makedirs(self.sessions)
        env = {k: v for k, v in os.environ.items() if not k.startswith("CADAI_")}
        env.update(CADAI_SESSIONS_DIR=self.sessions, CADAI_WATCH_INTERVAL="0.2", PYTHONIOENCODING="utf-8")
        self.proc = subprocess.Popen([sys.executable, SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=env)
        self.addCleanup(self.proc.stdout.close)
        self.addCleanup(self.proc.wait, 10)
        self.addCleanup(self.proc.stdin.close)
        self.lines = queue.Queue()
        threading.Thread(target=lambda: [self.lines.put(json.loads(raw)) for raw in self.proc.stdout],
                         daemon=True).start()
        self.next_id = 0
        self.notes = []
        info = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "test", "version": "1"}})
        self.assertEqual(info["serverInfo"]["name"], "cadai")
        self.assertTrue(info["capabilities"]["tools"]["listChanged"])

    def cad(self, backend, tools):
        fake = FakeCAD(self.sessions, backend, tools)
        self.addCleanup(lambda: os.path.exists(fake.dir) and fake.close())
        return fake

    def rpc(self, method, params=None):
        self.next_id += 1
        self.proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": self.next_id, "method": method,
                                           "params": params or {}}) + "\n").encode())
        self.proc.stdin.flush()
        while True:
            msg = self.lines.get(timeout=20)
            if msg.get("id") == self.next_id:
                return msg["result"]
            self.notes.append(msg.get("method"))

    def tools(self):
        return {t["name"] for t in self.rpc("tools/list")["tools"]}

    def call(self, name, args=None):
        out = self.rpc("tools/call", {"name": name, "arguments": args or {}})
        return out["isError"], out["content"][0]["text"]

    def wait_for_list_changed(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                msg = self.lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if msg.get("method") == "notifications/tools/list_changed":
                return
        self.fail("no notifications/tools/list_changed")

    def test_follows_the_running_program_and_never_guesses_between_two(self):
        # nothing open: last known tools stay listed; calls explain what to open
        self.assertTrue({"cad_bridge_status", "cad_select_session", "fem_run"} <= self.tools())
        error, text = self.call("get_document_summary")
        self.assertTrue(error)
        self.assertIn("Açık CAD programı bulunamadı", text)

        fusion = self.cad("fusion", ["get_document_summary", "import_mesh"])
        self.wait_for_list_changed()
        self.assertEqual(self.tools(), {"cad_bridge_status", "cad_select_session", "cad_refresh_context",
                                        "get_document_summary", "import_mesh"})
        self.assertEqual(self.call("get_document_summary"), (False, json.dumps({"backend": "fusion"})))
        status = json.loads(self.call("cad_bridge_status")[1])
        self.assertEqual((status["connected"]["backend"], status["connected"]["document"]), ("fusion", "FUSION part"))

        freecad = self.cad("freecad", ["get_document_summary", "run_python"])
        self.wait_for_list_changed()
        error, text = self.call("get_document_summary")
        self.assertTrue(error)
        self.assertIn("cad_select_session", text)
        self.assertEqual(len(fusion.calls), 1)  # the ambiguous call went nowhere

        self.assertFalse(self.call("cad_select_session", {"backend": "freecad"})[0])
        self.assertIn("run_python", self.tools())
        self.assertEqual(self.call("get_document_summary"), (False, json.dumps({"backend": "freecad"})))
        self.assertEqual(freecad.calls[-1]["target"]["session_id"], "freecad-session")

        freecad.close()  # FreeCAD quits: back to the only program left
        self.wait_for_list_changed()
        self.assertEqual(self.call("get_document_summary"), (False, json.dumps({"backend": "fusion"})))
        self.assertEqual(fusion.calls[-1]["target"]["session_id"], "fusion-session")

    def test_session_chosen_in_vs_code_wins_when_both_are_open(self):
        self.cad("fusion", ["get_document_summary"])
        freecad = self.cad("freecad", ["get_document_summary"])
        with open(os.path.join(self.sessions, "active-session.json"), "w", encoding="utf-8") as f:
            json.dump({"session_id": freecad.session_id}, f)
        self.assertEqual(self.call("get_document_summary"), (False, json.dumps({"backend": "freecad"})))
        status = json.loads(self.call("cad_bridge_status")[1])
        self.assertEqual(sorted(s["backend"] for s in status["sessions"]), ["freecad", "fusion"])


if __name__ == "__main__":
    unittest.main()
