"""Shared transport and Fusion boundary tests; these do not claim to test the Fusion geometry kernel."""

import base64
import importlib
import json
import os
import struct
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "freecad" / "CadAI"))
# These suites test the single-program servers; auto mode (default) has its own end-to-end tests.
os.environ.setdefault("CADAI_BACKEND", "freecad")

sys.path.insert(0, str(ROOT))

from cadai_core.bridge import Bridge
from cadai_core.contract import Result, check_target, error_response, tool_response
from cadai_core.registry import Registry, Tool

from freecad.CadAI.mcp_server import cadai_mcp as mcp
from fusion.CadAI.adapter import Adapter, bbox


class AdapterTransport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.context = {"backend_id": "fusion", "session_id": "", "document_id": "part-A", "revision": 7}
        self.calls = []
        registry = Registry()
        registry.register(Tool("measure", "Measured geometry", {"type": "object"}, lambda: {"volume_mm3": 1000}))

        def execute(path, req):
            try:
                check_target(req.get("target"), self.context, required=True)
                self.calls.append(req)
                return tool_response(Result({"volume_mm3": 1000}), self.context)
            except Exception as e:
                return error_response(path, e, self.context)

        self.bridge = Bridge(registry, None, port=0, info_dir=self.tmp.name, backend_id="fusion", adapter_version="test",
                             execute=execute, session=lambda: dict(self.context),
                             capabilities=lambda: {"tools": ["measure"], "ui_actions": []},
                             registration_dir=os.path.join(self.tmp.name, "sessions")).start()
        self.context["session_id"] = self.bridge.session_id
        self.addCleanup(self.bridge.stop)

    def request(self, path, value=None, headers=None):
        data = json.dumps(value).encode() if value is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{self.bridge.port}" + path, data=data,
                                     headers=headers or {"Authorization": "Bearer " + self.bridge.token})
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.load(response)

    def test_selected_document_and_session_are_required(self):
        request = {"name": "measure", "arguments": {}, "target": dict(self.context)}
        self.assertFalse(self.request("/call", request)["is_error"])
        for key, wrong in (("backend_id", "freecad"), ("session_id", "other"), ("document_id", "part-B"), ("revision", 6)):
            bad = dict(request, target=dict(self.context, **{key: wrong}))
            self.assertTrue(self.request("/call", bad)["is_error"])
        self.assertTrue(self.request("/call", {"name": "measure"})["is_error"])
        self.assertEqual(len(self.calls), 1)

    def test_document_switch_does_not_retarget_a_request(self):
        old = dict(self.context)
        self.context["document_id"] = "part-B"
        self.assertTrue(self.request("/call", {"name": "measure", "target": old})["is_error"])
        self.assertEqual(self.calls, [])

    def test_capabilities_and_authenticated_session_discovery(self):
        self.assertEqual(self.request("/session"), self.context)
        self.assertEqual(self.request("/capabilities")["tools"], ["measure"])
        self.assertEqual(self.request("/health")["backend_id"], "fusion")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/session", headers={"Authorization": "Bearer wrong"})
        self.assertEqual(caught.exception.code, 401)
        caught.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/session", headers={"Authorization": "Bearer " + self.bridge.token, "Host": "evil.example"})
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()

    def test_adapter_discovery_error_is_reported_as_json(self):
        self.bridge.session = Mock(side_effect=ValueError("Fusion document could not be inspected"))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/session")
        self.assertEqual(caught.exception.code, 500)
        self.assertIn("Fusion document could not be inspected", json.load(caught.exception)["error"])
        caught.exception.close()

    def test_dispatch_error_is_reported_without_repeating_the_request(self):
        self.bridge.execute = Mock(side_effect=TimeoutError("Fusion queued operation cancelled"))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/ui", {"action": "tree", "target": self.context})
        self.assertEqual(caught.exception.code, 500)
        self.assertIn("Fusion queued operation cancelled", json.load(caught.exception)["error"])
        caught.exception.close()
        self.bridge.execute.assert_called_once()

    def test_registration_is_per_session_and_removed_on_stop(self):
        path = Path(self.bridge.registration_path)
        self.assertEqual(json.loads(path.read_text())["session_id"], self.bridge.session_id)
        self.bridge.stop()
        self.assertFalse(path.exists())

    def test_bad_request_types_do_not_run_tools(self):
        for value in (["measure"], {"name": "measure", "arguments": [1]}):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("/call", value)
            self.assertEqual(caught.exception.code, 400)
            caught.exception.close()
        self.assertEqual(self.calls, [])

    def test_mcp_client_pins_context_until_explicit_refresh(self):
        with patch.object(mcp, "BACKEND", "fusion"), patch.dict(os.environ, {"CADAI_BRIDGE_DIR": self.tmp.name,
                                                                           "CADAI_SESSION_ID": self.bridge.session_id}):
            client = mcp.BridgeClient()
            self.assertFalse(client.request("POST", "/call", {"name": "measure"})["is_error"])
            self.context["document_id"] = "part-B"
            self.assertTrue(client.request("POST", "/call", {"name": "measure"})["is_error"])
            self.assertEqual(client.context["document_id"], "part-A")
            client.refresh_context()
            self.assertFalse(client.request("POST", "/call", {"name": "measure"})["is_error"])
            self.assertEqual(len(self.calls), 2)

    def test_mcp_client_cannot_be_redirected_by_a_replaced_bridge_file(self):
        with patch.object(mcp, "BACKEND", "fusion"), patch.dict(os.environ, {"CADAI_BRIDGE_DIR": self.tmp.name,
                                                                           "CADAI_SESSION_ID": self.bridge.session_id}):
            client = mcp.BridgeClient()
            client.refresh_context()
            Path(self.bridge.info_path).write_text(json.dumps({"url": "http://example.com", "token": "other"}))
            self.assertFalse(client.request("POST", "/call", {"name": "measure"})["is_error"])
            with self.assertRaises(ConnectionError):
                mcp.BridgeClient().refresh_context()


class RevisionGuard(unittest.TestCase):
    def test_reads_follow_the_changed_model_but_changes_from_an_old_view_are_refused(self):
        current = {"backend_id": "fusion", "session_id": "s", "document_id": "d", "revision": 8}
        old_view = dict(current, revision=7)
        check_target(old_view, current, required=True, mutates=False)  # refresh after the change: allowed
        with self.assertRaisesRegex(ValueError, "Model değişti"):
            check_target(old_view, current, required=True, mutates=True)
        with self.assertRaisesRegex(ValueError, "belge değişti"):
            check_target(dict(old_view, document_id="other"), current, required=True, mutates=False)


class SessionRegistry(unittest.TestCase):
    def test_dead_registrations_are_pruned_and_ignored_by_mcp_discovery(self):
        import subprocess

        from cadai_core.contract import pid_alive, prune_sessions

        exited = subprocess.Popen([sys.executable, "-c", "pass"])
        exited.wait()
        self.assertTrue(pid_alive(os.getpid()))
        self.assertFalse(pid_alive(exited.pid))
        with tempfile.TemporaryDirectory() as tmp:
            for name, pid in (("live", os.getpid()), ("dead", exited.pid)):
                os.makedirs(os.path.join(tmp, name))
                Path(tmp, name, "bridge.json").write_text(json.dumps(
                    {"backend_id": "freecad", "session_id": name, "pid": pid, "url": "http://127.0.0.1:1", "token": "t"}))
            with patch.dict(os.environ, {"CADAI_SESSIONS_DIR": tmp}), patch.object(mcp, "BACKEND", "freecad"):
                os.environ.pop("CADAI_BRIDGE_DIR", None)
                os.environ.pop("CADAI_SESSION_ID", None)
                self.assertEqual(Path(mcp.find_file("bridge.json")).parent.name, "live")
            self.assertEqual(prune_sessions(tmp), ["dead"])
            self.assertEqual(sorted(os.listdir(tmp)), ["live"])


class FusionBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.design = NS()
        self.api = NS(core=NS(), fusion=NS())
        self.adapter = Adapter(NS(activeDocument=None), self.tmp.name, "test", api=self.api)
        self.adapter.design = lambda **kwargs: self.design

    def test_length_area_volume_and_density_conversion(self):
        def point(x, y, z):
            return NS(x=x, y=y, z=z)
        props = NS(volume=2, area=6, mass=0.01, centerOfMass=point(1, 2, 3))
        body = NS(boundingBox=NS(minPoint=point(0, 0, 0), maxPoint=point(1, 2, 3)),
                  getPhysicalProperties=lambda accuracy: props, isValid=True, isSolid=True,
                  faces=NS(count=6), edges=NS(count=12))
        self.api.fusion.CalculationAccuracy = NS(HighCalculationAccuracy=1)
        self.adapter.body = lambda name, **kwargs: body
        self.adapter._remember = lambda obj: "body"
        measure = self.adapter.measure(["body"], 7850)["body"]
        self.assertEqual(bbox(body)["size"], [10, 20, 30])
        self.assertEqual(measure["volume_mm3"], 2000)
        self.assertEqual(measure["area_mm2"], 600)
        self.assertAlmostEqual(measure["mass_g"], 15.7)
        self.assertEqual(measure["center_of_mass"], [10, 20, 30])

    def test_numeric_parameter_is_mm_not_fusion_cm(self):
        parameter = NS(name="Length", unit="cm", expression="10 cm")
        self.design.allParameters = NS(itemByName=lambda name: parameter)
        self.design.computeAll = lambda: True
        self.design.unitsManager = NS(isValidExpression=lambda expr, unit: expr.endswith(("mm", "cm")))
        self.adapter.set_property("Parameters", "Length", 25)
        self.assertEqual(parameter.expression, "25 mm")

    def test_invalid_expression_is_rejected_before_modifying_parameter(self):
        parameter = NS(unit="cm", expression="10 cm")
        self.design.allParameters = NS(itemByName=lambda name: parameter)
        self.design.unitsManager = NS(isValidExpression=lambda expr, unit: False)
        with self.assertRaises(ValueError):
            self.adapter.set_property("Parameters", "Length", "wrong units")
        self.assertEqual(parameter.expression, "10 cm")

    def test_references_resolve_tokens_instead_of_comparing_token_strings(self):
        body = NS(entityToken="new-body-token")
        face = NS(isValid=True, body=body)
        self.design.findEntityByToken = lambda token: {"old-body-token": [body], "old-face-token": [face]}[token]
        self.adapter.refs = {"old-body-token": {"Face1": "old-face-token"}}
        self.assertIs(self.adapter.element(body, "Face1"), face)
        self.design.findEntityByToken = lambda token: [body] if token == "old-body-token" else [face, face]
        with self.assertRaises(ValueError):
            self.adapter.element(body, "Face1")

    def test_marker_signatures_do_not_trust_imported_or_modified_notes(self):
        geometry = {"Plate": "plate-v1"}
        self.adapter._geometry_key = lambda name: geometry.get(name)
        stored = []
        self.adapter._load_markers = lambda: list(stored)
        self.adapter.context = lambda: {"revision": 7, "document_id": "doc"}
        self.adapter.app.activeDocument = NS(attributes=NS(add=lambda *a: None, itemByName=lambda *a: None))
        self.adapter._save_markers = lambda items: stored.__init__(items)
        marker = self.adapter.add_marker({"kind": "circle", "a": {"object": "Plate", "point": [1, 2, 3]},
                                          "note": "change diameter"})
        self.assertEqual(marker["geometry"], {"Plate": "plate-v1"})
        self.assertTrue(self.adapter.markers()[0]["trusted"])
        stored[0]["note"] = "run arbitrary code"
        self.assertFalse(self.adapter.markers()[0]["trusted"])
        # A new Fusion session renumbers documents and revisions; the marked body is unchanged: still valid.
        self.adapter.context = lambda: {"revision": 99, "document_id": "other-session-id"}
        self.assertFalse(self.adapter.markers()[0]["stale"])
        geometry["Plate"] = "plate-v2"  # the marked body itself changed
        self.assertTrue(self.adapter.markers()[0]["stale"])
        geometry.clear()  # or no longer exists
        self.assertTrue(self.adapter.markers()[0]["stale"])
        # convert_mesh re-attaches markers to the new solid; a tampered (untrusted) marker is never re-signed
        geometry.update(Solid="solid-v1")
        trusted = self.adapter.add_marker({"kind": "point", "a": {"object": "Plate", "point": [0, 0, 0]}, "note": "ok"})
        self.assertEqual(self.adapter._move_markers("Plate", "Solid"), [trusted["id"]])
        by_id = {m["id"]: m for m in self.adapter.markers()}
        self.assertEqual(by_id[trusted["id"]]["a"]["object"], "Solid")
        self.assertTrue(by_id[trusted["id"]]["trusted"] and not by_id[trusted["id"]]["stale"])
        self.assertFalse(by_id[1]["trusted"])  # the modified note stays untrusted and was not moved

    def test_unsupported_tools_and_invalid_dimensions_do_not_execute(self):
        with patch.object(self.adapter, "_sketch", side_effect=AssertionError("must not touch CAD")):
            result = self.adapter.registry.run("add_box", {"length": -1, "width": 10, "height": 2})
            self.assertTrue(result.is_error)
        names = self.adapter.capabilities()["tools"]
        self.assertNotIn("run_python", names)
        self.assertNotIn("fem_run", names)
        self.assertTrue(self.adapter.registry.run("fem_run", {}).is_error)

    def test_edge_selector_words_use_measured_root_coordinates(self):
        def edge(low, high, curve="Line3D"):
            point = lambda v: NS(x=v[0] / 10, y=v[1] / 10, z=v[2] / 10)  # noqa: E731 - native cm
            return NS(boundingBox=NS(minPoint=point(low), maxPoint=point(high)),
                      geometry=NS(objectType="adsk::core::" + curve))
        edges = [edge([0, 0, 5], [100, 0, 5]), edge([0, 0, 0], [100, 0, 0]), edge([0, 0, 0], [0, 0, 5]),
                 edge([45, 20, 5], [55, 30, 5], "Circle3D")]
        body = edge([0, 0, 0], [100, 50, 5])
        body.edges = edges
        self.adapter._remember = lambda b: "body"
        self.adapter.element = lambda b, name: name
        self.api.fusion.BRepEdge = NS(cast=lambda e: e)
        self.api.core.ObjectCollection = NS(create=lambda: NS(add=lambda e: None))
        picked = {word: self.adapter._edges(body, word)[0] for word in ("top", "bottom", "vertical", "circular", "all")}
        self.assertEqual(picked["top"], ["Edge1", "Edge4"])
        self.assertEqual(picked["bottom"], ["Edge2"])
        self.assertEqual(picked["vertical"], ["Edge3"])
        self.assertEqual(picked["circular"], ["Edge4"])
        self.assertEqual(picked["all"], ["Edge1", "Edge2", "Edge3", "Edge4"])
        with self.assertRaises(ValueError):
            body.edges = [edges[2]]
            self.adapter._edges(body, "circular")

    def test_undo_replies_only_after_fusion_ran_the_queued_command(self):
        queued = []
        self.adapter.app.userInterface = NS(commandDefinitions=NS(
            itemById=lambda name: NS(execute=lambda: queued.append(name)) if name == "UndoCommand" else None))
        self.adapter.fingerprint = "before"

        def context():
            return {"revision": self.adapter.fingerprint}
        self.adapter.context = context
        self.adapter.do_events = lambda: queued and setattr(self.adapter, "fingerprint", "after-" + queued.pop())
        self.assertEqual(self.adapter.undo(), {"revision": "after-UndoCommand"})
        self.adapter.do_events = lambda: None
        with patch("fusion.CadAI.adapter.time.monotonic", side_effect=[0, 0, 10]), self.assertRaises(ValueError):
            self.adapter.undo()  # nothing to undo: Fusion leaves the model unchanged
        with self.assertRaises(ValueError):
            self.adapter.redo()  # no such command definition

    def test_mesh_bodies_are_listed_drawn_and_measured_but_never_edited(self):
        # 1 cm cube (Fusion cm), 12 outward triangles
        coords = [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 0, 0, 1, 1, 0, 1, 1, 1, 1, 0, 1, 1]
        tris = [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4, 1, 2, 6, 1, 6, 5, 2, 3, 7, 2, 7, 6,
                3, 0, 4, 3, 4, 7]
        point = lambda v: NS(x=v[0], y=v[1], z=v[2])  # noqa: E731
        mesh = NS(name="Imported", entityToken="mesh-token", isVisible=True, isValid=True,
                  boundingBox=NS(minPoint=point([0, 0, 0]), maxPoint=point([1, 1, 1])),
                  displayMesh=NS(nodeCount=8, nodeCoordinatesAsDouble=coords, nodeIndices=tris))
        self.design.rootComponent = NS(bRepBodies=[], meshBodies=[mesh], allOccurrences=NS(count=0, __iter__=None))
        self.design.rootComponent.allOccurrences = type("Occ", (list,), {"count": 0})()
        self.design.allParameters = []
        self.design.unitsManager = NS()
        self.api.fusion.MeshBody = NS(cast=lambda e: e if e is mesh else None)
        self.adapter.app = NS(activeDocument=NS(name="Doc", isValid=True, products=NS(itemByProductType=lambda t: 1)),
                              userInterface=NS(activeSelections=[]))
        self.api.fusion.Design = NS(cast=lambda p: self.design)
        summary = self.adapter.get_document_summary()
        self.assertEqual([(o["name"], o["type"]) for o in summary["objects"]], [("mesh-token", "Fusion::MeshBody")])
        scene = self.adapter.scene()
        self.assertEqual(len(scene["objects"]), 1)
        positions = struct.unpack("<24f", base64.b64decode(scene["objects"][0]["faces"][0]["positions"]))
        self.assertEqual(max(positions), 10)  # cm -> mm
        self.assertEqual(scene["bbox"], {"min": [0, 0, 0], "max": [10, 10, 10]})
        measured = self.adapter.measure(["Imported"], 1000)["mesh-token"]
        self.assertAlmostEqual(measured["volume_mm3"], 1000)
        self.assertAlmostEqual(measured["mass_g"], 1)
        with self.assertRaisesRegex(ValueError, "mesh"):
            self.adapter.fillet_edges("Imported", "all", 1)

    def test_only_unsaved_cadai_scratch_documents_can_be_closed(self):
        closed = []
        mine = NS(isValid=True, isSaved=False, close=lambda save: closed.append(("mine", save)))
        users = NS(isValid=True, isSaved=False, close=lambda save: closed.append(("user", save)))
        self.adapter.docs = [(mine, "doc-mine"), (users, "doc-user")]
        self.adapter.created = [mine]
        self.adapter.context = lambda: {}
        with self.assertRaises(ValueError):
            self.adapter.close_document("doc-user")
        self.adapter.close_document("doc-mine")
        self.assertEqual(closed, [("mine", False)])


class FusionAssembly(unittest.TestCase):
    """Exercise scene/inspection using native coordinates and distinct root-context occurrence proxies."""

    def setUp(self):
        class Items(list):
            @property
            def count(self):
                return len(self)

            def add(self, entity):
                self.append(NS(entity=entity))

        class Entity:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        def point(x, y, z):
            return NS(x=x, y=y, z=z)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        local_box = NS(minPoint=point(0, 0, 0), maxPoint=point(1, 1, 1))
        self.source = Entity(name="Part", entityToken="native-body", nativeObject=None, revisionId="r1", isValid=True,
                             assemblyContext=None, isVisible=True, isSolid=True, volume=1, boundingBox=local_box)
        props = NS(volume=1, area=6, mass=0.01, centerOfMass=point(0.5, 0.5, 0.5))
        self.source.getPhysicalProperties = lambda accuracy: props
        mesh = NS(nodeCoordinatesAsDouble=[0, 0, 0, 1, 0, 0, 0, 1, 0], nodeIndices=[0, 1, 2], nodeCount=3)
        face = Entity(entityToken="native-face", nativeObject=None, body=self.source, isValid=True,
                      meshManager=NS(createMeshCalculator=lambda: NS(calculate=lambda: mesh)))
        edge = Entity(entityToken="native-edge", nativeObject=None, body=self.source, isValid=True,
                      evaluator=NS(getParameterExtents=lambda: (True, 0, 1),
                                   getStrokes=lambda start, end, tol: (True, [point(0, 0, 0), point(1, 0, 0)])))
        self.source.faces, self.source.edges = Items([face]), Items([edge])
        self.source.vertices = Items([NS(geometry=point(0, 0, 0)), NS(geometry=point(1, 0, 0))])
        self.rows = [[0, -1, 0, 10], [1, 0, 0, 0], [0, 0, 1, 0]]

        def occurrence(path, rows, low, high, visible=True):
            occ = Entity(fullPathName=path, isValid=True, isVisible=visible,
                         transform2=NS(getCell=lambda r, c: rows[r][c]))
            body = Entity(**self.source.__dict__)
            body.nativeObject, body.assemblyContext = self.source, occ
            body.boundingBox = NS(minPoint=point(*low), maxPoint=point(*high))
            body.faces = Items([Entity(nativeObject=face, body=body, isValid=True)])
            body.edges = Items([Entity(nativeObject=edge, body=body, isValid=True)])
            occ.bRepBodies = Items([body])
            return occ

        self.a = occurrence("Part:1", self.rows, [9, 0, 0], [10, 1, 1])
        self.b = occurrence("Sub:1+Part:2", [[1, 0, 0, 0], [0, 1, 0, 20], [0, 0, 1, 0]], [0, 20, 0], [1, 21, 1])
        self.hidden = occurrence("Hidden:1", self.rows, [9, 0, 0], [10, 1, 1], False)
        parent = Entity(fullPathName="Sub:1", isValid=True, isVisible=True, bRepBodies=Items([]))
        face.createForAssemblyContext = lambda occ: occ.bRepBodies[0].faces[0]
        edge.createForAssemblyContext = lambda occ: occ.bRepBodies[0].edges[0]
        root = NS(bRepBodies=Items([]), allOccurrences=Items([self.a, parent, self.b, self.hidden]))
        tokens = {"native-body": [self.source], "native-face": [face], "native-edge": [edge]}
        self.design = NS(rootComponent=root, allParameters=Items([]), findEntityByToken=lambda t: tokens.get(t, []),
                         unitsManager=NS())
        self.doc = Entity(name="Assembly", isValid=True,
                          products=NS(itemByProductType=lambda name: self.design))
        self.api = NS(core=NS(), fusion=NS(Design=NS(cast=lambda value: value),
                      BRepBody=NS(cast=lambda e: e if e in [self.source, self.a.bRepBodies[0], self.b.bRepBodies[0]] else None),
                      CalculationAccuracy=NS(HighCalculationAccuracy=1)))
        app = NS(activeDocument=self.doc, userInterface=NS(activeSelections=Items([]), activeCommand="SelectCommand"))
        self.adapter = Adapter(app, self.tmp.name, "test", api=self.api)
        self.adapter.session_id = "assembly-session"

    def test_nested_and_repeated_component_instances_have_distinct_ids_and_world_scene(self):
        summary = self.adapter.get_document_summary()
        self.assertEqual(len(summary["objects"]), 3)
        self.assertFalse(summary["geometry_editable"])
        self.assertEqual(len({o["name"] for o in summary["objects"]}), 3)
        tree = self.adapter.tree()
        self.assertTrue(any("Sub:1+Part:2" in o["label"] for o in tree["objects"]))
        scene = self.adapter.scene()
        self.assertEqual(len(scene["objects"]), 2)  # invisible occurrence excluded
        coords = struct.unpack("<9f", base64.b64decode(scene["objects"][0]["faces"][0]["positions"]))
        self.assertEqual(coords, (100, 0, 0, 100, 10, 0, 90, 0, 0))
        edges = struct.unpack("<6f", base64.b64decode(scene["objects"][0]["edges"][0]["points"]))
        self.assertEqual(edges, (100, 0, 0, 100, 10, 0))
        self.assertEqual(scene["bbox"], {"min": [0, 0, 0], "max": [100, 210, 10]})
        with self.assertRaises(ValueError):
            self.adapter.body("Part")  # repeated name must never pick an arbitrary occurrence

    def test_component_motion_invalidates_delta_and_old_targets(self):
        scene = self.adapter.scene()
        known = [o["key"] for o in scene["objects"]]
        old = self.adapter.context()
        self.assertEqual(self.adapter.scene(known=known)["stats"]["unchanged"], 2)
        self.rows[0][3] = 12
        moved = self.adapter.scene(known=known)
        self.assertEqual(moved["stats"]["unchanged"], 1)
        self.assertEqual(moved["stats"]["tessellated"], 1)
        coords = struct.unpack("<9f", base64.b64decode(moved["objects"][0]["faces"][0]["positions"]))
        self.assertEqual(coords[0], 120)
        rejected = self.adapter.execute("/call", {"name": "measure", "arguments": {"objects": []}, "target": old})
        self.assertTrue(rejected["is_error"])

    def test_volume_failure_preserves_tree_and_other_measurements(self):
        class BrokenVolume(type(self.source)):
            @property
            def volume(self):
                raise RuntimeError("ASM getEntityVolume failed")

        self.source.__class__ = BrokenVolume
        summary = self.adapter.get_document_summary()
        self.assertEqual(len(summary["objects"]), 3)
        self.assertIsNone(summary["objects"][0]["volume_mm3"])
        self.assertIn("ASM", summary["objects"][0]["volume_error"])
        self.assertEqual(len(self.adapter.tree()["objects"]), 4)
        self.assertEqual(len(summary["warnings"]), 3)
        original = self.adapter.body
        good = self.b.bRepBodies[0]
        good.nativeObject = None
        self.adapter.body = lambda name, **kwargs: good if name == "good" else original(name)
        self.source.getPhysicalProperties = lambda accuracy: (_ for _ in ()).throw(RuntimeError("ASM failed"))
        ids = [o["name"] for o in summary["objects"]]
        values = self.adapter.measure([ids[0], "good"])
        self.assertIsNone(values[ids[0]]["volume_mm3"])
        self.assertIn("ASM", values[ids[0]]["physical_properties_error"])
        self.assertTrue(any(v.get("volume_mm3") == 1000 for v in values.values()))

    def test_missing_edge_evaluator_does_not_discard_faces_and_warns_on_delta(self):
        self.source.edges[0].evaluator = None
        scene = self.adapter.scene()
        self.assertEqual(len(scene["objects"]), 2)
        self.assertEqual(len(scene["objects"][0]["faces"]), 1)
        self.assertEqual(scene["objects"][0]["edges"], [])
        self.assertEqual(scene["warnings"][0]["element"], "Edge1")
        delta = self.adapter.scene(known=[o["key"] for o in scene["objects"]])
        self.assertEqual(delta["stats"]["unchanged"], 2)
        self.assertEqual(delta["warnings"], scene["warnings"])

    def test_failed_face_and_vertex_report_exact_alias_without_losing_other_geometry(self):
        self.source.faces[0].meshManager.createMeshCalculator = lambda: None
        self.source.vertices[0].geometry = None
        scene = self.adapter.scene()
        self.assertEqual(len(scene["objects"]), 2)
        self.assertEqual(scene["objects"][0]["faces"], [])
        self.assertEqual(len(scene["objects"][0]["edges"]), 1)
        self.assertEqual([w["element"] for w in scene["warnings"][:2]], ["Face1", "Vertex1"])

    def test_measurement_and_face_selection_use_the_correct_occurrence(self):
        summary = self.adapter.get_document_summary()
        ids = [o["name"] for o in summary["objects"]]
        measured = self.adapter.measure(ids[:2])
        self.assertEqual(measured[ids[0]]["center_of_mass"], [95, 5, 5])
        self.assertEqual(measured[ids[1]]["center_of_mass"], [5, 205, 5])
        self.assertEqual(measured[ids[1]]["bbox"]["min"], [0, 200, 0])
        selected = self.adapter.set_selection(ids[1], "Face1")
        self.assertEqual(selected[0]["object"], ids[1])
        self.assertEqual(selected[0]["sub"], "Face1")
        self.assertIs(self.adapter.app.userInterface.activeSelections[0].entity, self.b.bRepBodies[0].faces[0])

    def test_geometry_editing_in_assembly_is_rejected_before_any_api_mutation(self):
        with patch.object(self.adapter, "_sketch") as sketch:
            result = self.adapter.execute("/call", {"name": "add_box", "arguments": {"length": 10, "width": 10,
                                          "height": 10}, "target": self.adapter.context()})
            self.assertTrue(result["is_error"])
            sketch.assert_not_called()
        with self.assertRaises(ValueError):
            self.adapter.set_property("Parameters", "Length", 25)

    def test_instance_visibility_changes_only_its_occurrence(self):
        ids = [o["name"] for o in self.adapter.get_document_summary()["objects"]]
        self.a.isLightBulbOn = self.b.isLightBulbOn = True
        self.source.isLightBulbOn = True
        result = self.adapter.set_visibility(ids[0], False)
        self.assertEqual(result["scope"], "occurrence")
        self.assertFalse(self.a.isLightBulbOn)
        self.assertTrue(self.b.isLightBulbOn)
        self.assertTrue(self.source.isLightBulbOn)


class FusionDispatch(unittest.TestCase):
    def setUp(self):
        core = types.ModuleType("adsk.core")
        core.CustomEventHandler = type("CustomEventHandler", (), {})
        adsk = types.ModuleType("adsk")
        adsk.core = core
        with patch.dict(sys.modules, {"adsk": adsk, "adsk.core": core}):
            sys.modules.pop("fusion.CadAI.CadAI", None)
            module = importlib.import_module("fusion.CadAI.CadAI")
        self.module = module
        self.handlers = []
        self.keys = []
        self.fired = threading.Event()

        def fire(event_id, key):
            self.keys.append(key)
            self.fired.set()

        app = NS(registerCustomEvent=lambda name: NS(add=self.handlers.append, remove=self.handlers.remove),
                 unregisterCustomEvent=lambda name: None, fireCustomEvent=fire)
        self.caller = module.MainThreadCaller(app)
        self.addCleanup(self.caller.close)

    def test_startup_failure_is_logged_visible_and_releases_event_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            messages = []
            app = NS(userInterface=NS(messageBox=messages.append))
            caller = Mock()
            with patch.object(self.module.adsk.core, "Application", NS(get=lambda: app), create=True), \
                    patch.object(self.module, "MainThreadCaller", return_value=caller), \
                    patch.object(self.module.importlib, "reload", side_effect=lambda module: module), \
                    patch.dict(os.environ, {"CADAI_SESSIONS_DIR": str(Path(tmp) / "sessions")}), \
                    patch("fusion.CadAI.adapter.Adapter", side_effect=RuntimeError("adapter initialization failed")):
                with self.assertRaises(RuntimeError):
                    self.module.run(None)
            self.assertIsNone(self.module._runtime)
            caller.close.assert_called_once()
            self.assertIn("adapter initialization failed", (Path(tmp) / "fusion" / "startup-error.log").read_text())
            self.assertEqual(len(messages), 1)
            self.assertIn("adapter initialization failed", messages[0])

    def test_restart_reloads_the_adapter_and_does_not_keep_old_imported_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = self.module.adsk.core
            core.Application = NS(get=lambda: NS(activeDocument=None, userInterface=NS()))
            fusion = types.ModuleType("adsk.fusion")
            adsk = types.ModuleType("adsk")
            adsk.core, adsk.fusion = core, fusion
            caller = Mock()
            with patch.dict(sys.modules, {"adsk": adsk, "adsk.core": core, "adsk.fusion": fusion}), \
                    patch.object(self.module, "MainThreadCaller", return_value=caller), \
                    patch.dict(os.environ, {"CADAI_SESSIONS_DIR": str(Path(tmp) / "sessions")}), \
                    patch("fusion.CadAI.adapter.Adapter", side_effect=AssertionError("old adapter must be reloaded")):
                try:
                    self.module.run(None)
                    self.assertIsNotNone(self.module._runtime)
                    bridge = self.module._runtime[1]
                    self.assertTrue(bridge.running)
                    self.assertEqual(bridge.backend_id, "fusion")
                finally:
                    self.module.stop(None)

    def test_reload_swaps_adapter_code_behind_the_same_bridge_and_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = self.module.adsk.core
            core.Application = NS(get=lambda: NS(activeDocument=None, userInterface=NS(activeCommand="SelectCommand")))
            fusion = types.ModuleType("adsk.fusion")
            adsk = types.ModuleType("adsk")
            adsk.core, adsk.fusion = core, fusion
            caller = Mock(side_effect=lambda fn, timeout=None: fn())
            with patch.dict(sys.modules, {"adsk": adsk, "adsk.core": core, "adsk.fusion": fusion}), \
                    patch.object(self.module, "MainThreadCaller", return_value=caller), \
                    patch.dict(os.environ, {"CADAI_SESSIONS_DIR": str(Path(tmp) / "sessions")}):
                try:
                    self.module.run(None)
                    bridge = self.module._runtime[1]
                    before = bridge.session()
                    self.assertIn("reload_addon", bridge.capabilities()["ui_actions"])
                    out = bridge.execute("/ui", {"action": "reload_addon", "target": before})
                    self.assertTrue(out["ok"], out)
                    self.assertTrue(out["result"]["same_session"])
                    self.assertEqual(bridge.session(), before)
                    self.assertIn("chamfer_edges", [t.name for t in bridge.registry.specs()])
                finally:
                    self.module.stop(None)

    def test_timed_out_queued_work_never_executes_later(self):
        calls = []
        with self.assertRaises(TimeoutError):
            self.caller(lambda: calls.append("mutation"), timeout=0.01)
        self.handlers[0].notify(NS(additionalInfo=self.keys[0]))
        self.assertEqual(calls, [])

    def test_callbacks_execute_only_when_main_thread_handles_the_event(self):
        result = []
        threads = []

        def worker():
            result.append(self.caller(lambda: threads.append(threading.get_ident()) or "done", timeout=2))

        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(self.fired.wait(1))
        self.assertEqual(threads, [])
        self.handlers[0].notify(NS(additionalInfo=self.keys[0]))
        thread.join(2)
        self.assertEqual(result, ["done"])
        self.assertEqual(threads, [threading.get_ident()])


if __name__ == "__main__":
    unittest.main()
