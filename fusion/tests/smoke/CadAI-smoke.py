"""Run through Fusion Scripts and Add-Ins from a source checkout. Uses only its own unsaved document."""

import json
import math
import os
import sys
import tempfile
import traceback
from pathlib import Path

import adsk.core
import adsk.fusion

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "freecad" / "CadAI"))

from fusion.CadAI.adapter import Adapter


def run(context):
    app = adsk.core.Application.get()
    previous = app.activeDocument
    test_doc = None
    report = []
    try:
        with tempfile.TemporaryDirectory(prefix="cadai-fusion-smoke-") as temp:
            adapter = Adapter(app, temp, "0.19.3")
            adapter.session_id = "smoke"
            adapter.new_document("CadAI Smoke")
            test_doc = app.activeDocument

            def call(name, args):
                result = adapter.execute("/call", {"name": name, "arguments": args, "target": adapter.context()})
                if result["is_error"]:
                    raise AssertionError(result["content"])
                report.append(name)
                return json.loads(result["content"])

            box = call("add_box", {"length": 100, "width": 50, "height": 5, "name": "Plate"})["object"]
            measured = call("measure", {"objects": [box]})[box]
            assert all(abs(a - b) < 0.001 for a, b in zip(measured["bbox"]["size"], [100, 50, 5]))
            assert abs(measured["volume_mm3"] - 25000) < 0.01
            faces = call("find_faces", {"object": box, "surface_type": "Plane"})["faces"]
            top = [f for f in faces if f.get("normal", [0, 0, 0])[2] > 0.99]
            assert len(top) == 1
            hole = call("make_hole", {"object": box, "face": top[0]["name"], "position": [50, 25, 5], "diameter": 6})
            call("set_property", {"object": "Parameters", "property": hole["diameter_parameter"], "value": 10})
            measured = call("measure", {"objects": [box]})[box]
            assert abs(measured["volume_mm3"] - (25000 - math.pi * 25 * 5)) < 0.05
            cylinders = call("find_faces", {"object": box, "surface_type": "Cylinder"})["faces"]
            assert len(cylinders) == 1 and abs(cylinders[0]["radius_mm"] - 5) < 0.001
            edges = call("list_edges", {"object": box})["edges"]
            outer = [e for e in edges if e["curve"] == "Line3D" and abs(e["length_mm"] - 100) < 0.001
                     and abs(e["bbox"]["min"][2] - 5) < 0.001 and abs(e["bbox"]["max"][2] - 5) < 0.001]
            assert outer
            call("fillet_edges", {"object": box, "edges": [outer[0]["name"]], "radius": 1})
            scene = adapter.scene()
            delta = adapter.scene(known=[o["key"] for o in scene["objects"]])
            assert scene["objects"] and scene["objects"][0]["faces"]
            assert delta["stats"]["tessellated"] == 0 and delta["stats"]["unchanged"] == 1
            adapter.add_marker({"kind": "point", "a": {"object": box, "point": [50, 25, 5]}, "note": "Smoke"})
            assert adapter.markers()[0]["trusted"]
            call("export_model", {"path": os.path.join(temp, "plate.step")})
            assert os.path.getsize(os.path.join(temp, "plate.step")) > 1000
            report.append("scene_delta_and_signed_markers")
            # Component instances, including nesting, use native local meshes plus root-context placement.
            design = adapter.design()
            root = design.rootComponent
            adapter.body(box).isLightBulbOn = False
            rotation = adsk.core.Matrix3D.create()
            rotation.setToRotation(math.pi / 2, adsk.core.Vector3D.create(0, 0, 1), adsk.core.Point3D.create(0, 0, 0))
            rotation.translation = adsk.core.Vector3D.create(20, 0, 0)
            first = root.occurrences.addNewComponent(rotation)
            sketch = first.component.sketches.add(first.component.xYConstructionPlane)
            sketch.sketchCurves.sketchLines.addTwoPointRectangle(adsk.core.Point3D.create(0, 0, 0),
                                                               adsk.core.Point3D.create(2, 1, 0))
            feature = first.component.features.extrudeFeatures.addSimple(
                sketch.profiles.item(0), adsk.core.ValueInput.createByReal(0.5),
                adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
            feature.bodies.item(0).name = "Assembly Smoke Body"
            sketch.isVisible = False
            translation = adsk.core.Matrix3D.create()
            translation.translation = adsk.core.Vector3D.create(0, 20, 0)
            second = root.occurrences.addExistingComponent(first.component, translation)
            parent_matrix = adsk.core.Matrix3D.create()
            parent_matrix.translation = adsk.core.Vector3D.create(10, 0, 0)
            parent = root.occurrences.addNewComponent(parent_matrix)
            child_matrix = adsk.core.Matrix3D.create()
            child_matrix.translation = adsk.core.Vector3D.create(0, 0, 30)
            parent.component.occurrences.addExistingComponent(first.component, child_matrix)
            assembly = adapter.get_document_summary()
            assert not assembly["geometry_editable"]
            ids = [o["name"] for o in assembly["objects"] if not o["hidden"]]
            assert len(ids) == 3 and len(set(ids)) == 3
            values = adapter.measure(ids)
            centers = sorted(tuple(round(v, 3) for v in m["center_of_mass"]) for m in values.values())
            assert centers == sorted([(195.0, 10.0, 2.5), (10.0, 205.0, 2.5), (110.0, 5.0, 302.5)]), centers
            assembly_scene = adapter.scene()
            assert len(assembly_scene["objects"]) == 3
            delta = adapter.scene(known=[o["key"] for o in assembly_scene["objects"]])
            assert delta["stats"]["unchanged"] == 3
            translation.translation = adsk.core.Vector3D.create(0, 25, 0)
            second.transform2 = translation
            moved = adapter.scene(known=[o["key"] for o in assembly_scene["objects"]])
            assert moved["stats"]["unchanged"] == 2 and moved["stats"]["tessellated"] == 1
            report.append("rotated_repeated_nested_instances_and_motion_delta")
        app.userInterface.messageBox("CadAI Fusion smoke PASSED\n" + "\n".join(report))
    except Exception:
        app.userInterface.messageBox("CadAI Fusion smoke FAILED\n" + traceback.format_exc())
    finally:
        if test_doc and test_doc.isValid:
            test_doc.close(False)
        if previous and previous.isValid:
            previous.activate()
