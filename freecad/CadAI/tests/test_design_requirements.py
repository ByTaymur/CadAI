"""Real-geometry regression cases, called by run_tests.py in an isolated FreeCAD process."""

import json
import os
import tempfile

import FreeCAD


def run_tests(test, call, fresh_beam, registry):
    from cadai.tools.requirement_tools import STORE

    def req(key, metric, value, **extra):
        return dict(id=key, measure={"object": "Beam", "metric": metric}, value=value, **extra)

    @test
    def requirements_measure_mutations_and_follow_holes():
        doc = fresh_beam()
        saved = call("set_design_requirements", requirements=[
            req("length", "bbox_x", 100), req("volume", "volume", 20000), req("solid", "solid_count", 1)])
        assert saved["design_validation"]["status"] == "pass", saved
        out = call("make_hole", object="Beam", position=[50, 10, 10], diameter=4)
        check = out["design_validation"]
        assert out["ok"] and check["status"] == "fail" and check["failed"] == 1, out
        volume = next(c for c in check["checks"] if c["id"] == "volume")
        assert volume["evidence"]["object"] == "Beam_Hole", volume
        assert abs(volume["measured"] - doc.getObject("Beam_Hole").Shape.Volume) < 1e-8
        assert volume["expected"] == 20000 and volume["deviation"] < 0
        assert call("get_document_summary")["design_validation"]["status"] == "fail"
        # Changing the source dimension recomputes the result; failed requirement doesn't undo the edit.
        out = call("set_property", object="Beam", property="Length", value=120)
        assert out["design_validation"]["failed"] == 2 and float(doc.Beam.Length) == 120, out
        assert not registry.get("check_design_requirements").mutates
        assert registry.get("set_design_requirements").mutates

    @test
    def requirements_relations_and_clearance():
        fresh_beam()
        call("add_box", name="Lid", length=100, width=20, height=2, position=[0, 0, 12])
        relation = {"id": "matching_width", "measure": {"object": "Lid", "metric": "bbox_x"},
                    "reference": {"object": "Beam", "metric": "bbox_x"}, "value": 0}
        gap = {"id": "gap", "measure": {"object": "Beam", "metric": "min_distance", "other": "Lid"},
               "operator": "min", "value": 2, "tolerance": 0.001}
        out = call("set_design_requirements", requirements=[relation, gap, req("height", "bbox_z", 10, operator="max")])
        assert out["design_validation"]["status"] == "pass", out
        out = call("set_property", object="Beam", property="Length", value=120)
        assert out["design_validation"]["failed"] == 1, out
        out = call("set_property", object="Lid", property="Length", value=120)
        assert out["design_validation"]["status"] == "pass", out
        out = call("move_object", object="Lid", offset=[0, 0, -1])
        gap_result = next(c for c in out["design_validation"]["checks"] if c["id"] == "gap")
        assert gap_result["status"] == "fail" and abs(gap_result["measured"] - 1) < 1e-8, gap_result
        # Ratio plus offset in the same unit, evaluated from current geometry.
        out = call("set_design_requirements", requirements=[
            req("ratio", "bbox_x", 20, reference={"object": "Beam", "metric": "bbox_z"}, factor=10)])
        assert next(c for c in out["design_validation"]["checks"] if c["id"] == "ratio")["status"] == "pass"

    @test
    def requirements_persist_merge_remove_and_undo():
        doc = fresh_beam()
        call("set_design_requirements", requirements=[req("length", "bbox_x", 100), req("height", "bbox_z", 10)])
        out = call("set_design_requirements", requirements=[req("height", "bbox_z", 12)])
        assert len(out["requirements"]) == 2 and out["design_validation"]["failed"] == 1, out
        doc.undo()
        doc.recompute()
        assert call("check_design_requirements")["status"] == "pass"
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "requirements.FCStd")
            doc.saveAs(path)
            FreeCAD.closeDocument(doc.Name)
            doc = FreeCAD.openDocument(path)
            check = call("check_design_requirements")
            assert check["status"] == "pass" and check["count"] == 2, check
            doc.Beam.Label = "Different display label"
            assert call("check_design_requirements")["status"] == "pass"
            call("set_design_requirements", requirements=[], remove_ids=["height"])
            assert call("check_design_requirements")["count"] == 1
            call("set_design_requirements", requirements=[], remove_ids=["length"])
            assert call("check_design_requirements")["status"] == "not_configured"
            FreeCAD.closeDocument(doc.Name)

    @test
    def requirements_reject_bad_input_without_losing_targets():
        doc = fresh_beam()
        call("set_design_requirements", requirements=[req("length", "bbox_x", 100)])
        original = doc.getObject(STORE).Definition
        bad = [req("length", "bbox_x", float("nan")), req("length", "bbox_x", float("inf")),
               req("length", "bbox_x", 100, tolerance=-1),
               req("length", "bbox_x", 100, reference={"object": "Beam", "metric": "volume"}),
               req("length", "bbox_x", 100, factor=2)]
        for item in bad:
            out = registry.run("set_design_requirements", {"requirements": [item]})
            assert out.is_error, out.content
            assert doc.getObject(STORE).Definition == original
        out = registry.run("set_design_requirements", {"requirements": [], "remove_ids": ["missing"]})
        assert out.is_error and doc.getObject(STORE).Definition == original
        duplicate = registry.run("set_design_requirements", {"requirements": [req("x", "bbox_x", 1)] * 2})
        assert duplicate.is_error
        # A failed modeling operation preserves both geometry and checks.
        out = registry.run("run_python", {"code": "doc.removeObject('Beam')\nraise ValueError('rollback')"})
        assert out.is_error and call("check_design_requirements")["status"] == "pass"

    @test
    def requirements_missing_corrupt_and_stale_never_pass():
        doc = fresh_beam()
        call("set_design_requirements", requirements=[req("length", "bbox_x", 100)])
        doc.Beam.Length = 130
        assert call("check_design_requirements")["checks"][0]["status"] == "error", "stale shape must not pass"
        doc.recompute()
        assert call("check_design_requirements")["checks"][0]["status"] == "fail"
        out = call("run_python", code="doc.removeObject('Beam')")
        assert out["ok"] and out["design_validation"]["checks"][0]["status"] == "error", out
        doc.getObject(STORE).Definition = '{"version": 500, "requirements": []}'
        assert call("check_design_requirements")["status"] == "error"
        out = call("add_box", name="NewPart", length=10, width=10, height=10)
        assert out["ok"] and out["design_validation"]["status"] == "error", out
        doc.getObject(STORE).Definition = json.dumps({"version": 1, "requirements": [
            req("untrusted", "bbox_x", 100, code="raise RuntimeError('must never execute')")]})
        assert call("check_design_requirements")["status"] == "error"

    @test
    def requirements_ambiguous_results_and_tolerances():
        doc = fresh_beam()
        call("set_design_requirements", requirements=[req("length", "bbox_x", 100, tolerance=0.001)])
        out = call("set_property", object="Beam", property="Length", value=100.0005)
        assert out["design_validation"]["status"] == "pass"
        out = call("set_property", object="Beam", property="Length", value=100.002)
        assert out["design_validation"]["status"] == "fail"
        call("run_python", code="""
cutter = doc.addObject('Part::Cylinder', 'Cutter')
cutter.Radius = 2
cutter.Height = 20
cutter.Placement.Base = Vector(50, 10, 0)
for name in ('ResultA', 'ResultB'):
    cut = doc.addObject('Part::Cut', name)
    cut.Base = doc.Beam
    cut.Tool = cutter
""")
        check = call("check_design_requirements")
        assert check["checks"][0]["status"] == "error", check
        # Explicitly rebinding the target removes the ambiguity.
        item = req("length", "bbox_x", 100.002)
        item["measure"]["object"] = "ResultA"
        out = call("set_design_requirements", requirements=[item])
        assert out["design_validation"]["status"] == "pass", out
        assert doc.Beam.Length.Value == 100.002

    @test
    def requirements_empty_document_and_document_isolation():
        doc = fresh_beam()
        assert call("check_design_requirements")["status"] == "not_configured"
        out = call("set_property", object="Beam", property="Length", value=100)
        assert "design_validation" not in out
        call("set_design_requirements", requirements=[req("length", "bbox_x", 90)])
        second = FreeCAD.newDocument("NoRequirements")
        assert call("check_design_requirements")["status"] == "not_configured"
        FreeCAD.closeDocument(second.Name)
        FreeCAD.setActiveDocument(doc.Name)
        assert call("check_design_requirements")["status"] == "fail"
        FreeCAD.closeDocument(doc.Name)
        assert call("check_design_requirements")["status"] == "not_configured"

    @test
    def parametric_plate_preserves_holes_after_sequential_edits():
        doc = fresh_beam()
        plate = call("add_mounting_plate", length=80, width=60, thickness=8, hole_diameter=6,
                     edge_offset_x=10, edge_offset_y=12, name="Plate")
        assert plate["parameters_object"] == "Plate"
        requirements = [{"id": key, "measure": dict(object="Plate", metric=metric, **extra), "value": value}
                        for key, metric, value, extra in [
                            ("holes", "hole_count", 4, {}), ("diameters", "hole_diameter", 6, {}),
                            ("x_margin", "hole_edge_offset", 10, {"edge_axis": "x"}),
                            ("y_margin", "hole_edge_offset", 12, {"edge_axis": "y"})]]
        out = call("set_design_requirements", requirements=requirements)
        assert out["design_validation"]["status"] == "pass", out
        for prop, value in [("Length", 120), ("Width", 90), ("Height", 15)]:
            out = call("set_property", object="Plate", property=prop, value=value)
            assert out["design_validation"]["status"] == "pass", out
        holes = call("inspect_holes", object="Plate")
        centers = sorted(tuple(round(v, 5) for v in h["center"][:2]) for h in holes["holes"])
        assert centers == [(10, 12), (10, 78), (110, 12), (110, 78)], centers
        assert all(h["range_mm"] == [0.0, 15.0] for h in holes["holes"]), holes
        out = call("set_property", object="Plate", property="HoleDiameter", value=8)
        assert out["design_validation"]["failed"] == 1, out
        out = call("set_property", object="Plate", property="HoleDiameter", value=6)
        assert out["design_validation"]["status"] == "pass"
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "plate.FCStd")
            doc.saveAs(path)
            FreeCAD.closeDocument(doc.Name)
            doc = FreeCAD.openDocument(path)
            out = call("set_property", object="Plate", property="Length", value=140)
            assert out["design_validation"]["status"] == "pass", out
            assert max(h["center"][0] for h in call("inspect_holes", object="Plate")["holes"]) == 130
            FreeCAD.closeDocument(doc.Name)

    @test
    def parameter_relations_update_remove_and_rollback_cycles():
        doc = fresh_beam()
        call("add_box", name="Lid", length=80, width=20, height=2)
        out = call("set_parameter_relation", object="Lid", property="Length", reference_object="Beam",
                   reference_property="Length", factor=0.5, offset=2)
        assert out["value"] == 52 and out["unit"] == "mm", out
        call("set_property", object="Beam", property="Length", value=120)
        assert doc.Lid.Length.Value == 62
        cycle = registry.run("set_parameter_relation", {"object": "Beam", "property": "Length",
                              "reference_object": "Lid", "reference_property": "Length"})
        assert cycle.is_error, cycle.content
        assert doc.Beam.Length.Value == 120 and doc.Lid.Length.Value == 62
        bad = registry.run("set_parameter_relation", {"object": "Lid", "property": "Length",
                            "reference_object": "Beam", "reference_property": "__class__"})
        assert bad.is_error
        out = call("set_parameter_relation", object="Lid", property="Length", remove=True)
        assert out["expression"] is None and out["value"] == 62
        call("set_property", object="Beam", property="Length", value=140)
        assert doc.Lid.Length.Value == 62
        out = call("set_parameter_relation", object="Lid", property="Placement.Base.z", reference_object="Beam",
                   reference_property="Height", offset=2)
        assert out["value"] == 12, out
        call("set_property", object="Beam", property="Height", value=20)
        assert doc.Lid.Placement.Base.z == 22

    @test
    def holes_exclude_bosses_and_open_slots_and_detect_wrong_diameters():
        fresh_beam()
        call("add_cylinder", diameter=8, height=5, name="Boss")
        assert call("inspect_holes", object="Boss")["count"] == 0
        call("make_hole", object="Beam", position=[30, 10, 10], diameter=4)
        call("make_hole", object="Beam", position=[70, 10, 10], diameter=8)
        assert call("inspect_holes", object="Beam")["count"] == 2
        out = call("set_design_requirements", requirements=[req("diameter", "hole_diameter", 4)])
        row = out["design_validation"]["checks"][0]
        assert row["status"] == "fail" and sorted(row["measured"]) == [4, 8], row
        assert call("inspect_holes", object="Beam", axis="x")["count"] == 0
        # A cylindrical notch opening on the outside is not a closed bore.
        call("make_hole", object="Beam", position=[50, 0, 10], diameter=4, direction=[0, 0, -1])
        assert call("inspect_holes", object="Beam")["count"] == 2

    @test
    def interference_is_measured_separately_from_distance():
        fresh_beam()
        call("add_box", name="Other", length=10, width=20, height=10, position=[95, 0, 0])
        out = call("set_design_requirements", requirements=[
            {"id": "overlap", "measure": {"object": "Beam", "metric": "interference_volume", "other": "Other"},
             "operator": "max", "value": 0, "tolerance": 0.001}])
        row = out["design_validation"]["checks"][0]
        assert row["status"] == "fail" and abs(row["measured"] - 1000) < 1e-8, row
        out = call("move_object", object="Other", offset=[10, 0, 0])
        assert out["design_validation"]["status"] == "pass", out

    @test
    def requirements_ui_document_guard_and_json_report():
        from cadai import ui_actions

        doc = fresh_beam()
        try:
            ui_actions.update_design_requirements("WrongDoc", [req("length", "bbox_x", 1)])
            raise AssertionError("stale panel must not edit the active document")
        except Exception as e:
            assert "Belge" in str(e)
        assert call("check_design_requirements")["status"] == "not_configured"
        out = ui_actions.update_design_requirements(doc.Name, [req("length", "bbox_x", 100)])
        assert out["status"] == "pass" and out["requirements"][0]["value"] == 100
        # Direct UI/manual edits must be visible without going through Registry.run.
        doc.Beam.Length = 120
        doc.recompute()
        assert ui_actions.design_requirements()["status"] == "fail"
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "verification.json")
            call("export_design_report", path=path)
            with open(path, encoding="utf-8") as stream:
                report = json.load(stream)
            assert report["status"] == "fail" and report["requirements"][0]["value"] == 100
            assert report["checks"][0]["measured"] == 120 and report["generated_at"]
            assert registry.run("export_design_report", {"path": path + ".py"}).is_error
        assert call("check_design_requirements", ids=["length"])["partial"]


def main():
    import sys
    import traceback

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import cadai.config
    from cadai.tools import build_registry

    home = tempfile.mkdtemp(prefix="cadai_requirements_test_")
    cadai.config.config_path = lambda: os.path.join(home, "config.json")
    reg = build_registry()
    failures = []

    def test_one(fn):
        try:
            fn()
            print("PASS", fn.__name__, flush=True)
        except Exception:
            failures.append(fn.__name__)
            traceback.print_exc()
            print("FAIL", fn.__name__, flush=True)

    def invoke(tool_name, **args):
        out = reg.run(tool_name, args)
        assert not out.is_error, out.content
        return json.loads(out.content)

    def fresh():
        for name in list(FreeCAD.listDocuments()):
            FreeCAD.closeDocument(name)
        doc = FreeCAD.newDocument("T")
        box = doc.addObject("Part::Box", "Beam")
        box.Length, box.Width, box.Height = 100, 20, 10
        doc.recompute()
        return doc

    run_tests(test_one, invoke, fresh, reg)
    print("ALL PASSED" if not failures else "FAILED: " + ", ".join(failures), flush=True)
