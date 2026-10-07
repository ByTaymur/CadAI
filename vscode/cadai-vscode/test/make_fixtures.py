"""Regenerate the viewer test fixtures from a real FreeCAD + CalculiX run:
    freecadcmd vscode/cadai-vscode/test/make_fixtures.py
Writes test/fixtures/scene.json (the 3D view's scene for a cantilever and an L-block) and
test/fixtures/fem_von_mises.json (the solved cantilever's surface field), exactly as the bridge sends them.
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "freecad", "CadAI"))

import FreeCAD
import Part

import cadai.config

home = tempfile.mkdtemp()
cadai.config.config_path = lambda: os.path.join(home, "CadAI", "config.json")

from cadai import ui_actions
from cadai.tools import build_registry

REG = build_registry()
ui_actions.selection = lambda: []  # headless: no FreeCADGui.Selection
doc = FreeCAD.newDocument("Fixture")
beam = doc.addObject("Part::Box", "Beam")
beam.Length, beam.Width, beam.Height = 100, 20, 10
block = doc.addObject("Part::Feature", "LBlock")
shape = Part.makeBox(40, 40, 20).cut(Part.makeBox(20, 20, 20, FreeCAD.Vector(20, 20, 0)))
block.Shape = shape.cut(Part.makeCylinder(4, 20, FreeCAD.Vector(10, 10, 0)))
block.Placement.Base = FreeCAD.Vector(0, 40, 0)
doc.recompute()

out = os.path.join(HERE, "fixtures")
os.makedirs(out, exist_ok=True)
with open(os.path.join(out, "scene.json"), "w", encoding="utf-8") as f:
    json.dump(ui_actions.scene(1.0), f, separators=(",", ":"))

setup = json.loads(REG.run("fem_setup", {"object": "Beam", "fixed_faces": ["Face1"], "mesh_size_mm": 5,
                                         "forces": [{"faces": ["Face2"], "force_n": 500, "direction": [0, 0, -1]}]}).content)
res = REG.run("fem_run", {"analysis": setup["analysis"]})
assert not res.is_error, res.content
with open(os.path.join(out, "fem_von_mises.json"), "w", encoding="utf-8") as f:
    json.dump(ui_actions.fem_field(setup["analysis"], "von_mises"), f, separators=(",", ":"))
print("FIXTURES WRITTEN", out)

# Real geometry and definitions for the requirements sidebar (one intentional diameter mismatch).
res = REG.run("add_mounting_plate", {"length": 80, "width": 60, "thickness": 8, "hole_diameter": 6,
                                     "edge_offset_x": 10, "edge_offset_y": 10, "name": "Plate"})
assert not res.is_error, res.content
res = REG.run("set_design_requirements", {"requirements": [
    {"id": "Delik_sayisi", "measure": {"object": "Plate", "metric": "hole_count"}, "value": 4},
    {"id": "Delik_capi", "measure": {"object": "Plate", "metric": "hole_diameter"}, "value": 8},
    {"id": "Kenar_uzakligi", "measure": {"object": "Plate", "metric": "hole_edge_offset", "edge_axis": "x"},
     "value": 10}]})
assert not res.is_error, res.content
with open(os.path.join(out, "requirements.json"), "w", encoding="utf-8") as f:
    json.dump({"tree": ui_actions.tree(), "report": ui_actions.design_requirements()}, f, ensure_ascii=False, indent=2)
