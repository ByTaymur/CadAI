"""Live Fusion check through the running CadAI add-in's local bridge (no Fusion Script run needed).

    python fusion/tests/live_smoke.py            # the only running Fusion session
    CADAI_SESSION_ID=<id> python fusion/tests/live_smoke.py

Works only in a new, unsaved document it creates and closes without saving; the user's documents are not touched.
"""

import glob
import json
import math
import os
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "freecad", "CadAI"))
from cadai_core.contract import sessions_dir


def request(info, method, path, payload=None, timeout=120):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(info["url"] + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + info["token"],
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def find_session():
    wanted = os.environ.get("CADAI_SESSION_ID")
    live = []
    for path in glob.glob(os.path.join(sessions_dir(), "*", "bridge.json")):
        with open(path, encoding="utf-8") as f:
            info = json.load(f)
        if info.get("backend_id") != "fusion" or (wanted and info.get("session_id") != wanted):
            continue
        try:
            if request(info, "GET", "/health", timeout=5).get("session_id") == info["session_id"]:
                live.append(info)
        except OSError:
            pass
    if len(live) != 1:
        raise SystemExit(f"Tam bir çalışan Fusion oturumu gerekli, bulunan: {len(live)}. CADAI_SESSION_ID verin.")
    return live[0]


class Live:
    def __init__(self, info):
        self.info = info
        self.target = request(info, "GET", "/session")
        self.passed = []

    def ui(self, action, /, **args):
        out = request(self.info, "POST", "/ui", {"action": action, "args": args, "target": self.target})
        self.target = out.get("context") or self.target
        if not out.get("ok"):
            raise AssertionError(f"{action}: {out.get('error')}")
        return out["result"]

    def call(self, tool, /, **args):
        out = request(self.info, "POST", "/call", {"name": tool, "arguments": args, "target": self.target})
        self.target = out.get("context") or self.target
        if out["is_error"]:
            raise AssertionError(f"{tool}: {out['content']}")
        self.passed.append(tool)
        return json.loads(out["content"])

    def volume(self, body):
        return self.call("measure", objects=[body])[body]["volume_mm3"]


def cube_stl(size, corner):
    """ASCII STL of an axis-aligned cube, outward normals."""
    x0, y0, z0 = corner
    p = [(x0 + size * (i & 1), y0 + size * (i >> 1 & 1), z0 + size * (i >> 2 & 1)) for i in range(8)]
    quads = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    lines = ["solid cube"]
    for a, b, c, d in quads:
        for tri in ((a, b, c), (a, c, d)):
            lines += ["facet normal 0 0 0", "outer loop"]
            lines += ["vertex {:g} {:g} {:g}".format(*p[i]) for i in tri] + ["endloop", "endfacet"]
    return "\n".join(lines + ["endsolid cube"]) + "\n"


def close(a, b, tol=1e-3):
    return abs(a - b) <= tol


def main():
    live = Live(find_session())
    print("Fusion oturumu:", live.info["session_id"], "sürüm", request(live.info, "GET", "/health")["version"])
    caps = request(live.info, "GET", "/capabilities")
    previous = live.target["document_id"]
    live.target = live.ui("new_document", name="CadAI Live Smoke")
    test_doc = live.target["document_id"]
    try:
        plate = live.call("add_box", length=100, width=50, height=5, name="Plate")["object"]
        m = live.call("measure", objects=[plate])[plate]
        assert all(close(a, b) for a, b in zip(m["bbox"]["size"], [100, 50, 5])), m
        assert close(m["volume_mm3"], 25000, 0.01), m
        top = [f for f in live.call("find_faces", object=plate, surface_type="Plane")["faces"]
               if f.get("normal", [0, 0, 0])[2] > 0.99]
        assert len(top) == 1, top
        hole = live.call("make_hole", object=plate, face=top[0]["name"], position=[50, 25, 5], diameter=6)
        live.call("set_property", object="Parameters", property=hole["diameter_parameter"], value=10)
        expected = 25000 - math.pi * 25 * 5
        assert close(live.volume(plate), expected, 0.05)
        radius = [f["radius_mm"] for f in live.call("find_faces", object=plate, surface_type="Cylinder")["faces"]]
        assert len(radius) == 1 and close(radius[0], 5), radius

        chamfer = live.call("chamfer_edges", object=plate, edges="vertical", size=1)
        assert len(chamfer["edges"]) == 4, chamfer
        expected -= 4 * 0.5 * 5  # four 1 x 1 / 2 mm triangles, 5 mm tall
        assert close(live.volume(plate), expected, 0.05)
        edges = live.call("list_edges", object=plate)["edges"]
        outer = [e for e in edges if e["curve"] == "Line3D" and close(e["length_mm"], 98)
                 and close(e["bbox"]["min"][2], 5) and close(e["bbox"]["max"][2], 5)]
        assert outer, edges
        live.call("fillet_edges", object=plate, edges=[outer[0]["name"]], radius=1)
        removed = expected - live.volume(plate)
        assert close(removed, (1 - math.pi / 4) * 98, 0.5), removed  # ends meet the chamfer faces
        expected -= removed

        pin = live.call("add_cylinder", diameter=8, height=20, position=[20, 25, -50], name="Pin")["object"]
        moved = live.call("move_object", object=pin, offset=[0, 0, 45])
        assert close(moved["new_min"][2], -5) and close(moved["new_min"][0], 16), moved
        cut = live.call("boolean", operation="cut", base=plate, tool=pin)
        assert close(cut["volume_change_mm3"], -math.pi * 16 * 5, 0.05), cut
        expected -= math.pi * 16 * 5
        assert close(live.volume(plate), expected, 0.05)
        names = [o["name"] for o in live.call("get_document_summary")["objects"]]
        assert names == [plate], names

        bar = live.call("add_box", length=10, width=2, height=2, position=[200, 0, 0], name="Bar")["object"]
        live.call("move_object", object=bar, rotation_deg=90)
        size = live.call("measure", objects=[bar])[bar]["bbox"]["size"]
        assert all(close(a, b) for a, b in zip(size, [2, 10, 2])), size
        if "undo" in caps["ui_actions"]:
            live.ui("undo")
            size = live.call("measure", objects=[bar])[bar]["bbox"]["size"]
            assert all(close(a, b) for a, b in zip(size, [10, 2, 2])), size
            live.ui("redo")
            size = live.call("measure", objects=[bar])[bar]["bbox"]["size"]
            assert all(close(a, b) for a, b in zip(size, [2, 10, 2])), size
            live.passed.append("undo_redo")

        # Imported mesh body (the user's real case: an STL in Fusion showed as "0 objects" before 0.18.1).
        with tempfile.TemporaryDirectory(prefix="cadai-fusion-mesh-") as temp:
            stl = os.path.join(temp, "cube.stl")
            with open(stl, "w", encoding="ascii") as f:
                f.write(cube_stl(10, [300, 0, 0]))
            mesh = live.call("import_mesh", path=stl)["objects"]
        assert len(mesh) == 1, mesh
        mesh = mesh[0]
        kinds = {o["name"]: o["type"] for o in live.ui("tree")["objects"]}
        assert kinds.get(mesh) == "Fusion::MeshBody", kinds
        m = live.call("measure", objects=[mesh])[mesh]
        assert close(m["volume_mm3"], 1000, 0.01) and all(close(v, 10) for v in m["bbox"]["size"]), m
        try:
            live.call("fillet_edges", object=mesh, edges="all", radius=1)
            raise AssertionError("mesh body must not be editable")
        except AssertionError as error:
            assert "mesh" in str(error).lower(), error

        # The user's real workflow: mesh -> editable solid -> hole at the face center (an M4 tap drill, Ø3.3).
        converted = live.call("convert_mesh", object=mesh)
        solid = converted["bodies"][0]
        assert len(converted["bodies"]) == 1 and solid["solid"] and solid["faces"] == 6, converted
        assert close(solid["volume_mm3"], 1000, 0.01), converted
        kinds = {o["name"]: o["type"] for o in live.ui("tree")["objects"]}
        assert kinds.get(solid["object"]) == "Fusion::BRepBody" and mesh not in kinds, kinds
        cube = solid["object"]
        top = [f for f in live.call("find_faces", object=cube, surface_type="Plane")["faces"]
               if f.get("normal", [0, 0, 0])[2] > 0.99]
        assert len(top) == 1 and all(close(a, b) for a, b in zip(top[0]["center"], [305, 5, 10])), top
        hole = live.call("make_hole", object=cube, face=top[0]["name"], position=top[0]["center"], diameter=3.3)
        assert close(hole["removed_volume_mm3"], math.pi * 1.65 ** 2 * 10, 0.05), hole
        scaled = live.call("scale_object", object=cube, factor=2)
        assert all(close(v, 20) for v in scaled["new_size"]), scaled
        assert close(live.volume(cube), 8 * (1000 - math.pi * 1.65 ** 2 * 10), 0.5)

        scene = live.ui("scene")
        assert len(scene["objects"]) == 3 and all(o["faces"] for o in scene["objects"]), scene.get("warnings")
        assert not scene["warnings"], scene["warnings"]
        delta = live.ui("scene", known=[o["key"] for o in scene["objects"]])
        assert delta["stats"]["tessellated"] == 0 and delta["stats"]["unchanged"] == 3, delta["stats"]
        live.ui("add_marker", marker={"kind": "point", "a": {"object": plate, "point": [50, 25, 5]}, "note": "Smoke"})
        assert live.ui("markers")[0]["trusted"]
        live.passed.append("scene_delta_and_signed_markers")
        with tempfile.TemporaryDirectory(prefix="cadai-fusion-live-") as temp:
            path = os.path.join(temp, "plate.step")
            live.call("export_model", path=path)
            assert os.path.getsize(path) > 1000
        print("PASS:", ", ".join(live.passed))
    finally:
        live.target = request(live.info, "GET", "/session")
        if live.target["document_id"] == test_doc:
            live.ui("close_document", name=test_doc)
        live.target = request(live.info, "GET", "/session")
        if previous and live.target["document_id"] != previous:
            try:
                live.ui("activate_document", name=previous)
            except AssertionError:
                pass


if __name__ == "__main__":
    main()
