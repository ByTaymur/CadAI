"""Shared helpers: document/object lookup and compact geometry descriptions."""

import difflib
import re

import FreeCAD

from . import ToolError

DIM_PROPERTY_TYPES = {
    "App::PropertyLength",
    "App::PropertyDistance",
    "App::PropertyAngle",
    "App::PropertyQuantity",
}


def r(x, nd=4):
    return round(float(x), nd)


def vec(v, nd=4):
    return [r(v.x, nd), r(v.y, nd), r(v.z, nd)]


def active_doc(create=False):
    doc = FreeCAD.ActiveDocument
    if doc is None:
        if not create:
            raise ToolError("Açık bir FreeCAD belgesi yok.")
        doc = FreeCAD.newDocument("CadAI")
    return doc


SUB_ELEMENT_RE = re.compile(r"^(.+?)\s*[:.]\s*(Face|Edge|Vertex)\d+$", re.IGNORECASE)


def _lookup(doc, key):
    obj = doc.getObject(key)
    if obj is None:
        matches = doc.getObjectsByLabel(key)
        obj = matches[0] if matches else None
    if obj is None:  # small models change the case: "box", "PLATE"
        low = key.lower()
        hits = [o for o in doc.Objects if o.Name.lower() == low or o.Label.lower() == low]
        obj = hits[0] if len(hits) == 1 else None
    return obj


def get_object(name, doc=None):
    doc = doc or active_doc()
    if isinstance(name, dict):  # {"name": "Box", ...} copied from a tool result
        name = name.get("name") or name.get("object") or name.get("label") or ""
    key = str(name).strip().strip("'\"`")
    obj = _lookup(doc, key) if key else None
    if obj is None:
        m = SUB_ELEMENT_RE.match(key)  # "Box:Face1" / "Box.Face1" where only the object is wanted
        if m:
            obj = _lookup(doc, m.group(1))
    if obj is None:
        raise ToolError(f"Nesne bulunamadı: {name!r}. " + object_choices(doc, key))
    return obj


def object_choices(doc, wanted=""):
    """Names the model can use, closest first, for error messages."""
    objs = [o for o in doc.Objects if getattr(o, "Shape", None) is not None and not o.TypeId.startswith(
        ("App::Origin", "App::Line", "App::Plane", "Fem::"))]
    if not objs:
        return "Belgede geometrisi olan nesne yok."
    names = {o.Name: o for o in objs}
    close = difflib.get_close_matches(wanted, list(names), n=3, cutoff=0.4) if wanted else []
    close += [o.Name for o in objs if o.Label.lower() == wanted.lower() and o.Name not in close]
    listed = []
    for o in objs[:25]:
        listed.append(o.Name if o.Label == o.Name else f"{o.Name} (etiket {o.Label!r})")
    hint = f"Benzer: {', '.join(close)}. " if close else ""
    return hint + "Belgedeki nesneler: " + ", ".join(listed) + (" …" if len(objs) > 25 else "")


RESULT_TYPES = ("Part::Cut", "Part::Fuse", "Part::MultiFuse", "Part::Common", "Part::Fillet", "Part::Chamfer")


def latest_version(obj):
    """Follow Cut/Fuse/Fillet/Chamfer results built on obj, so 'Plate' keeps meaning the current plate after a hole was
    cut into it (the hole tool makes Plate_Hole and hides Plate)."""
    seen = set()
    while obj.Name not in seen:
        seen.add(obj.Name)
        nxt = None
        for parent in obj.InList:
            if parent.TypeId not in RESULT_TYPES:
                continue
            uses = [getattr(parent, "Base", None)] + list(getattr(parent, "Shapes", None) or [])
            if parent.TypeId in ("Part::Fuse", "Part::Common"):
                uses.append(getattr(parent, "Tool", None))
            if obj in uses:
                nxt = parent
                break
        if nxt is None:
            return obj
        obj = nxt
    return obj


def get_shape(obj):
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        raise ToolError(f"{obj.Name} bir geometri (Shape) içermiyor.")
    return shape


def bbox(shape):
    b = shape.BoundBox
    return {"min": [r(b.XMin, 3), r(b.YMin, 3), r(b.ZMin, 3)], "max": [r(b.XMax, 3), r(b.YMax, 3), r(b.ZMax, 3)],
            "size": [r(b.XLength, 3), r(b.YLength, 3), r(b.ZLength, 3)]}


def face_normal(face):
    com = face.CenterOfMass
    try:
        u, v = face.Surface.parameter(com)
        return face.normalAt(u, v)
    except Exception:
        u0, u1, v0, v1 = face.ParameterRange
        return face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)


def describe_face(face, name):
    surf = face.Surface
    info = {"name": name, "type": surf.__class__.__name__, "area_mm2": r(face.Area, 3),
            "center": vec(face.CenterOfMass, 3)}
    if info["type"] == "Plane":
        info["normal"] = vec(face_normal(face), 4)
    if hasattr(surf, "Radius"):
        info["radius_mm"] = r(surf.Radius, 4)
    if info["type"] in ("Cylinder", "Cone"):
        info["axis"] = vec(surf.Axis, 4)
    return info


def describe_edge(edge, name):
    curve = edge.Curve
    info = {"name": name, "type": curve.__class__.__name__, "length_mm": r(edge.Length, 4)}
    if hasattr(curve, "Radius"):
        info["radius_mm"] = r(curve.Radius, 4)
        info["center"] = vec(curve.Center, 3)
    if edge.Vertexes:
        info["start"] = vec(edge.Vertexes[0].Point, 3)
        info["end"] = vec(edge.Vertexes[-1].Point, 3)
    return info


def describe_sub(shape, sub_name):
    try:
        sub = shape.getElement(sub_name)
    except Exception:
        return {"name": sub_name}
    if sub_name.startswith("Face"):
        return describe_face(sub, sub_name)
    if sub_name.startswith("Edge"):
        return describe_edge(sub, sub_name)
    if sub_name.startswith("Vertex"):
        return {"name": sub_name, "point": vec(sub.Point, 3)}
    return {"name": sub_name}


def dimension_properties(obj):
    props = {}
    for p in obj.PropertiesList:
        if p == "Placement":
            continue
        try:
            if obj.getTypeIdOfProperty(p) in DIM_PROPERTY_TYPES:
                props[p] = str(getattr(obj, p))
        except Exception:
            pass
    return props
