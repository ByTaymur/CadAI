"""Tools that change the model. Each runs inside an undo transaction and is rolled back on error."""

import contextlib
import io
import math
import os

import FreeCAD

from . import Tool, ToolError
from .geometry import active_doc, bbox, get_object


def _invalid_objects(doc):
    return [{"name": o.Name, "state": o.State} for o in doc.Objects if "Invalid" in o.State or "Error" in o.State]


@contextlib.contextmanager
def transaction(doc, title):
    if doc.UndoMode == 0:
        doc.UndoMode = 1
    doc.openTransaction(title)
    try:
        yield
    except BaseException:
        doc.abortTransaction()
        doc.recompute()
        raise
    doc.commitTransaction()


def run_python(code, description=""):
    doc = active_doc(create=True)
    before = {o.Name for o in doc.Objects}
    import Part

    namespace = {"App": FreeCAD, "FreeCAD": FreeCAD, "doc": doc, "Part": Part, "Vector": FreeCAD.Vector,
                 "Placement": FreeCAD.Placement, "Rotation": FreeCAD.Rotation, "math": math}
    stdout = io.StringIO()
    try:
        with transaction(doc, "CadAI: " + (description or "Python")):
            with contextlib.redirect_stdout(stdout):
                exec(compile(code, "<cadai>", "exec"), namespace)
            doc.recompute()
            invalid = _invalid_objects(doc)
            if invalid:
                raise ToolError("Yeniden hesaplamada hatalı nesneler var, değişiklik geri alındı: "
                                + ", ".join(f"{i['name']} {i['state']}" for i in invalid))
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"Kod hata verdi ve değişiklik geri alındı.\n{type(e).__name__}: {e}\nÇıktı:\n{stdout.getvalue()}")
    created = [o for o in doc.Objects if o.Name not in before]
    result = {"ok": True, "created": [{"name": o.Name, "type": o.TypeId} for o in created]}
    for o in created:
        shape = getattr(o, "Shape", None)
        if shape is not None and not shape.isNull() and shape.BoundBox.isValid():
            result.setdefault("created_bbox", {})[o.Name] = bbox(shape)
    if stdout.getvalue():
        result["stdout"] = stdout.getvalue()[-4000:]
    return result


def set_property(object, property, value):
    doc = active_doc()
    obj = get_object(object, doc)
    if property not in obj.PropertiesList:
        raise ToolError(f"{obj.Name} nesnesinde {property!r} özelliği yok. Mevcut: {', '.join(obj.PropertiesList)}")
    old = str(getattr(obj, property))
    try:
        with transaction(doc, f"CadAI: {obj.Name}.{property}"):
            setattr(obj, property, value)
            doc.recompute()
            invalid = _invalid_objects(doc)
            if invalid:
                raise ToolError("Yeniden hesaplama başarısız, değişiklik geri alındı: "
                                + ", ".join(f"{i['name']} {i['state']}" for i in invalid))
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"Değer atanamadı ({type(e).__name__}: {e}); değişiklik geri alındı.")
    return {"ok": True, "object": obj.Name, "property": property, "old": old, "new": str(getattr(obj, property))}


def export_model(objects, path):
    doc = active_doc()
    objs = [get_object(n, doc) for n in objects]
    path = os.path.abspath(os.path.expanduser(path))
    ext = os.path.splitext(path)[1].lower()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if ext in (".step", ".stp", ".iges", ".igs"):
        import Import

        Import.export(objs, path)
    elif ext in (".glb", ".gltf"):
        import Import

        # OCCT's glTF writer only writes an existing triangulation: without this the file has no geometry
        for o in objs:
            shape = getattr(o, "Shape", None)
            if shape is not None and not shape.isNull():
                shape.tessellate(max(shape.BoundBox.DiagonalLength * 0.0005, 0.01))
        Import.export(objs, path)
    elif ext == ".brep":
        import Part

        Part.makeCompound([o.Shape for o in objs]).exportBrep(path)
    elif ext in (".stl", ".obj", ".3mf"):
        import Mesh

        Mesh.export(objs, path)
    else:
        raise ToolError("Desteklenen uzantılar: .step .stp .iges .igs .brep .glb .gltf .stl .obj .3mf")
    return {"ok": True, "path": path, "bytes": os.path.getsize(path)}


TOOLS = [
    Tool("run_python",
         "Run FreeCAD Python code to create or change geometry. Variables: doc (active document), App, Part, "
         "Vector, Placement, Rotation, math. Runs in one undo transaction; on error or invalid recompute everything "
         "is rolled back. Prefer set_property for changing an existing dimension.",
         {"type": "object", "properties": {
             "code": {"type": "string", "description": "Python code. End with doc.recompute() is not needed."},
             "description": {"type": "string", "description": "Short title shown in the undo history."}},
          "required": ["code"]},
         run_python, mutates=True, title="Run FreeCAD Python",
         example={"code": "b = doc.addObject('Part::Box', 'Block')\nb.Length = 20", "description": "Block"}),
    Tool("set_property",
         "Change one property of an existing object, e.g. Length of a Pad to '20 mm' or Radius of a cylinder to 5. "
         "Use for parametric edits. Rolled back if recompute fails.",
         {"type": "object", "properties": {
             "object": {"type": "string"},
             "property": {"type": "string"},
             "value": {"description": "New value: number, string with unit like '20 mm', or boolean.",
                       "type": ["number", "string", "boolean"]}},
          "required": ["object", "property", "value"]},
         set_property, mutates=True, title="Change a property", idempotent=True,
         example={"object": "Plate", "property": "Length", "value": 120}),
    Tool("export_model",
         "Export objects to a file. Extension selects format: .step/.stp/.iges/.brep (exact CAD), .glb/.gltf "
         "(web/AR, with triangulation) or .stl/.obj/.3mf (mesh, 3D printing).",
         {"type": "object", "properties": {
             "objects": {"type": "array", "items": {"type": "string"}},
             "path": {"type": "string"}},
          "required": ["objects", "path"]},
         export_model, mutates=True, title="Export STEP/STL/3MF", idempotent=True,
         example={"objects": ["Plate"], "path": "C:/Users/me/plate.step"}),
]
