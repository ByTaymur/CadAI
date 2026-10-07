"""Tested FreeCAD Python recipes for run_python (retrieval instead of guessing APIs).

Models write much better FreeCAD code when they start from a working pattern. Every recipe here is executed by the
test suite (tests/run_tests.py) in a real FreeCAD, so the snippets stay correct across FreeCAD versions. Recipes
that edit a part use an object called "Plate"; the agent replaces names and numbers at the top of the snippet.
"""

from . import Tool, ToolError

RECIPES = {
    "through_hole": {
        "title": "Delik aç (parametrik, Part::Cut)",
        "when": "Var olan bir katıya belirli noktadan, belirli yönde delik açmak.",
        "code": '''target = doc.getObject("Plate")          # delinecek nesne
center = Vector(40, 40, 10)               # delik merkezi (yüzeyin üzerinde)
direction = Vector(0, 0, -1)              # malzemenin içine doğru
diameter, depth = 9.0, 30.0               # mm (derinlik delik boyu; boydan boya için parçadan uzun ver)
tool = doc.addObject("Part::Cylinder", "Hole")
tool.Radius, tool.Height = diameter / 2, depth + 1
tool.Placement = Placement(center - direction * 1, Rotation(Vector(0, 0, 1), direction))
cut = doc.addObject("Part::Cut", target.Name + "_Hole")
cut.Base, cut.Tool = target, tool
target.Visibility = tool.Visibility = False
doc.recompute()
print(cut.Name, cut.Shape.isValid(), round(cut.Shape.Volume, 3))''',
    },
    "bolt_circle": {
        "title": "Daire üzerinde delik dizisi (flanş deliği)",
        "when": "PCD (bölüm dairesi) üzerinde eşit aralıklı N delik.",
        "code": '''target = doc.getObject("Plate")
center = Vector(40, 40, 10)               # bölüm dairesinin merkezi (üst yüzey)
direction = Vector(0, 0, -1)
pcd, count, diameter, depth = 50.0, 6, 6.6, 30.0
holes = []
for i in range(count):
    a = 2 * math.pi * i / count
    c = center + Vector(math.cos(a), math.sin(a), 0) * (pcd / 2)
    h = doc.addObject("Part::Cylinder", f"BoltHole{i + 1}")
    h.Radius, h.Height = diameter / 2, depth + 1
    h.Placement = Placement(c - direction * 1, Rotation(Vector(0, 0, 1), direction))
    h.Visibility = False
    holes.append(h)
fused = doc.addObject("Part::MultiFuse", "BoltHoles")
fused.Shapes = holes
fused.Visibility = False
cut = doc.addObject("Part::Cut", target.Name + "_BoltCircle")
cut.Base, cut.Tool = target, fused
target.Visibility = False
doc.recompute()
print(cut.Name, len([f for f in cut.Shape.Faces if f.Surface.__class__.__name__ == "Cylinder"]), "silindirik yüz")''',
    },
    "fillet_edges": {
        "title": "Kenar yuvarlatma (Part::Fillet)",
        "when": "Seçilen kenarlara radyus. Kenar numaralarını list_edges ya da get_selection'dan al.",
        "code": '''target = doc.getObject("Plate")
edges = [1, 3, 5, 7]                      # Edge1, Edge3... (list_edges ile bul)
radius = 2.0
fillet = doc.addObject("Part::Fillet", target.Name + "_Fillet")
fillet.Base = target
fillet.Edges = [(i, radius, radius) for i in edges]
target.Visibility = False
doc.recompute()
print(fillet.Name, fillet.Shape.isValid(), len(fillet.Shape.Faces), "yüz")''',
    },
    "chamfer_edges": {
        "title": "Pah kırma (Part::Chamfer)",
        "when": "Kenarlara eşit pah, ör. 1 mm × 45°.",
        "code": '''target = doc.getObject("Plate")
edges = [2, 4, 6, 8]
size = 1.0
chamfer = doc.addObject("Part::Chamfer", target.Name + "_Chamfer")
chamfer.Base = target
chamfer.Edges = [(i, size, size) for i in edges]
target.Visibility = False
doc.recompute()
print(chamfer.Name, chamfer.Shape.isValid())''',
    },
    "rect_pocket": {
        "title": "Dikdörtgen cep",
        "when": "Üst yüzeyde belirli derinlikte dikdörtgen cep (köşeleri yuvarlatılmış istersen sonra fillet).",
        "code": '''target = doc.getObject("Plate")
top_z = target.Shape.BoundBox.ZMax
length, width, depth = 30.0, 20.0, 4.0
center_xy = (40.0, 40.0)
box = doc.addObject("Part::Box", "PocketTool")
box.Length, box.Width, box.Height = length, width, depth + 1
box.Placement.Base = Vector(center_xy[0] - length / 2, center_xy[1] - width / 2, top_z - depth)
box.Visibility = False
cut = doc.addObject("Part::Cut", target.Name + "_Pocket")
cut.Base, cut.Tool = target, box
target.Visibility = False
doc.recompute()
print(cut.Name, round(target.Shape.Volume - cut.Shape.Volume, 3), "mm³ çıkarıldı")''',
    },
    "plate_with_holes": {
        "title": "Sıfırdan bağlantı plakası (köşe delikleri + pah)",
        "when": "Ör. M8 cıvatalar için 80×80×10 mm plaka, 4 köşe deliği Ø9 (orta geçiş), kenarlarda 1 mm pah.",
        "code": '''L, W, T = 80.0, 80.0, 10.0
hole_d, margin, chamfer = 9.0, 10.0, 1.0
plate = Part.makeBox(L, W, T)
for x in (margin, L - margin):
    for y in (margin, W - margin):
        plate = plate.cut(Part.makeCylinder(hole_d / 2, T + 2, Vector(x, y, -1)))
outer = [e for e in plate.Edges if e.Curve.__class__.__name__ == "Line"]
plate = plate.makeChamfer(chamfer, outer)
obj = doc.addObject("Part::Feature", "MountingPlate")
obj.Shape = plate
doc.recompute()
print(obj.Name, plate.isValid(), round(plate.Volume, 3))''',
    },
    "extrude_profile": {
        "title": "2B profili uzat (ekstrüzyon)",
        "when": "Nokta listesiyle tanımlı kapalı profilden katı (L profil, braket, özel kesit).",
        "code": '''points = [(0, 0), (40, 0), (40, 5), (5, 5), (5, 30), (0, 30)]   # XY düzleminde, mm
length = 60.0                                                       # Z yönünde uzatma
wire = Part.makePolygon([Vector(x, y, 0) for x, y in points] + [Vector(*points[0], 0)])
solid = Part.Face(wire).extrude(Vector(0, 0, length))
obj = doc.addObject("Part::Feature", "Profile")
obj.Shape = solid
doc.recompute()
print(obj.Name, solid.isValid(), round(solid.Volume, 3))''',
    },
    "revolve_shaft": {
        "title": "Döndürerek mil/pim (revolve)",
        "when": "Kademeli mil, pim, burç: yarım kesit profili Z ekseni etrafında döndürülür.",
        "code": '''# (yarıçap, z) noktaları, eksen üzerinde başlayıp biten yarım kesit
profile = [(0, 0), (10, 0), (10, 20), (6, 20), (6, 50), (0, 50)]
wire = Part.makePolygon([Vector(r_, 0, z) for r_, z in profile] + [Vector(profile[0][0], 0, profile[0][1])])
solid = Part.Face(wire).revolve(Vector(0, 0, 0), Vector(0, 0, 1), 360)
obj = doc.addObject("Part::Feature", "Shaft")
obj.Shape = solid
doc.recompute()
print(obj.Name, solid.isValid(), round(solid.Volume, 3))''',
    },
    "partdesign_pad": {
        "title": "PartDesign: kısıtlı eskiz + Pad (tam parametrik)",
        "when": "Ölçüleri sonra set_property ile değişecek parametrik parça (eskiz kısıtları + Pad uzunluğu).",
        "code": '''import Sketcher
body = doc.addObject("PartDesign::Body", "Body")
sk = body.newObject("Sketcher::SketchObject", "BaseSketch")
xy = next(f for f in body.Origin.OriginFeatures if f.Role == "XY_Plane")
sk.AttachmentSupport = [(xy, "")]
sk.MapMode = "FlatFace"
w, h, height = 60.0, 40.0, 8.0
p = [Vector(0, 0, 0), Vector(w, 0, 0), Vector(w, h, 0), Vector(0, h, 0)]
for i in range(4):
    sk.addGeometry(Part.LineSegment(p[i], p[(i + 1) % 4]))
for i in range(4):
    sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
sk.addConstraint(Sketcher.Constraint("Horizontal", 0))
sk.addConstraint(Sketcher.Constraint("Horizontal", 2))
sk.addConstraint(Sketcher.Constraint("Vertical", 1))
sk.addConstraint(Sketcher.Constraint("Vertical", 3))
sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, w))
sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, h))
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = height
doc.recompute()
print(body.Name, body.Shape.isValid(), round(body.Shape.Volume, 3))''',
    },
}


def freecad_recipes(topic=None):
    if not topic:
        return {"recipes": [{"topic": k, "title": v["title"], "when": v["when"]} for k, v in RECIPES.items()],
                "how_to_use": "freecad_recipes(topic=...) ile kodu al, baştaki adları/ölçüleri değiştir, run_python ile "
                              "çalıştır, measure ile doğrula."}
    if topic not in RECIPES:
        raise ToolError(f"Bilinmeyen tarif {topic!r}. Seçenekler: {', '.join(RECIPES)}")
    rec = RECIPES[topic]
    return {"topic": topic, "title": rec["title"], "when": rec["when"], "code": rec["code"],
            "variables": "run_python içinde hazır: doc, App, Part, Vector, Placement, Rotation, math",
            "tested": "Bu kod test paketinde gerçek FreeCAD'de çalıştırılıyor."}


TOOLS = [
    Tool("freecad_recipes",
         "Tested FreeCAD Python patterns to start run_python code from (holes, bolt circles, fillets, chamfers, "
         "pockets, plates, extrusions, revolved shafts, parametric PartDesign sketch + pad). Call without topic to "
         "list them; with topic to get the code. Prefer this over writing FreeCAD API calls from memory.",
         {"type": "object", "properties": {"topic": {"type": "string", "enum": list(RECIPES)}}},
         freecad_recipes, title="FreeCAD code recipes"),
]
