"""Read-only tools: document summary, selection, face search, measurement, screenshots, hand calcs."""

import base64
import math
import os
import tempfile

from . import Tool, ToolError, ToolResult
from .geometry import (
    active_doc,
    bbox,
    describe_edge,
    describe_face,
    describe_sub,
    dimension_properties,
    face_normal,
    get_object,
    get_shape,
    latest_version,
    r,
    vec,
)

SKIP_TYPES = ("Fem::", "App::Origin", "App::Line", "App::Plane")


def get_document_summary(include_fem=False):
    doc = active_doc()
    objects = []
    for obj in doc.Objects:
        if not include_fem and obj.TypeId.startswith(SKIP_TYPES):
            continue
        item = {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
        if "Invalid" in obj.State or "Error" in obj.State:
            item["state"] = obj.State
        dims = dimension_properties(obj)
        if dims:
            item["dimensions"] = dims
        exprs = getattr(obj, "ExpressionEngine", None)
        if exprs:
            item["expressions"] = {k: v for k, v in exprs}
        shape = getattr(obj, "Shape", None)
        if shape is not None and not shape.isNull() and shape.BoundBox.isValid():
            item["bbox"] = bbox(shape)
            if shape.Solids:
                item["volume_mm3"] = r(shape.Volume, 3)
                item["faces"] = len(shape.Faces)
        if hasattr(obj, "Group") and obj.Group:
            item["children"] = [o.Name for o in obj.Group]
        if not getattr(obj, "Visibility", True):
            item["hidden"] = True
        latest = latest_version(obj)
        if latest is not obj:  # consumed by a cut/fillet...: edits should target the result
            item["superseded_by"] = latest.Name
        if hasattr(obj, "Placement") and not obj.Placement.isIdentity():
            item["placement"] = {"base": vec(obj.Placement.Base, 3), "rotation_deg": [
                r(a, 3) for a in obj.Placement.Rotation.toEuler()]}
        objects.append(item)
    from .requirement_tools import check_design_requirements

    out = {"document": doc.Name, "objects": objects}
    validation = check_design_requirements()
    if validation["status"] != "not_configured":
        out["design_validation"] = validation
    return out


def get_selection():
    try:
        import FreeCADGui as Gui
    except ImportError:
        raise ToolError("Seçim yalnızca FreeCAD arayüzünde kullanılabilir.")
    if not getattr(Gui, "Selection", None):
        raise ToolError("Seçim yalnızca FreeCAD arayüzünde kullanılabilir.")
    items = []
    for sel in Gui.Selection.getSelectionEx():
        obj = sel.Object
        entry = {"object": obj.Name, "label": obj.Label, "type": obj.TypeId, "fem_target": fem_target(obj).Name}
        shape = getattr(obj, "Shape", None)
        subs = []
        for sub_name in sel.SubElementNames:
            short = sub_name.split(".")[-1]
            subs.append(describe_sub(shape, short) if shape is not None else {"name": short})
        if subs:
            entry["elements"] = subs
        items.append(entry)
    if not items:
        return {"selection": [], "note": "Hiçbir şey seçili değil."}
    return {"selection": items}


def fem_target(obj):
    """PartDesign features share faces with their Body when they are its tip; FEM should reference the Body."""
    if obj.TypeId.startswith("PartDesign::") and obj.TypeId != "PartDesign::Body":
        try:
            body = obj.getParentGeoFeatureGroup()
        except Exception:
            body = None
        if body is not None and getattr(body, "Tip", None) == obj:
            return body
    return obj


AXES = {"x": 0, "y": 1, "z": 2}


def find_faces(object, surface_type=None, normal=None, extreme=None, radius_mm=None, angle_tol_deg=5.0):
    obj = get_object(object)
    shape = get_shape(obj)
    faces = [(f"Face{i}", f) for i, f in enumerate(shape.Faces, 1)]
    if surface_type:
        faces = [(n, f) for n, f in faces if f.Surface.__class__.__name__.lower() == surface_type.lower()]
    if normal:
        import FreeCAD

        target = FreeCAD.Vector(*normal)
        if target.Length == 0:
            raise ToolError("normal sıfır vektör olamaz.")
        target.normalize()
        cos_tol = math.cos(math.radians(angle_tol_deg))
        faces = [(n, f) for n, f in faces
                 if f.Surface.__class__.__name__ == "Plane" and face_normal(f).dot(target) >= cos_tol]
    if radius_mm is not None:
        faces = [(n, f) for n, f in faces
                 if hasattr(f.Surface, "Radius") and abs(f.Surface.Radius - radius_mm) < 1e-3 + 1e-3 * radius_mm]
    if extreme:
        kind, axis = extreme.split("_")
        idx = AXES[axis]
        coords = [list(f.CenterOfMass)[idx] for _, f in faces]
        if coords:
            best = max(coords) if kind == "max" else min(coords)
            faces = [(n, f) for (n, f), c in zip(faces, coords) if abs(c - best) < 1e-6]
    return {"object": obj.Name, "fem_target": fem_target(obj).Name, "count": len(faces),
            "faces": [describe_face(f, n) for n, f in faces[:60]]}


def list_edges(object, curve_type=None, faces=None):
    obj = get_object(object)
    shape = get_shape(obj)
    if faces:
        wanted = set()
        for fname in faces:
            for e in shape.getElement(fname).Edges:
                for i, e2 in enumerate(shape.Edges, 1):
                    if e2.isSame(e):
                        wanted.add(i)
        edges = [(f"Edge{i}", shape.Edges[i - 1]) for i in sorted(wanted)]
    else:
        edges = [(f"Edge{i}", e) for i, e in enumerate(shape.Edges, 1)]
    if curve_type:
        edges = [(n, e) for n, e in edges if e.Curve.__class__.__name__.lower() == curve_type.lower()]
    return {"object": obj.Name, "count": len(edges), "edges": [describe_edge(e, n) for n, e in edges[:80]]}


def measure(objects=None, density_kg_m3=None, distance_between=None):
    out = {}
    for name in objects or []:
        obj = get_object(name)
        shape = get_shape(obj)
        m = {"bbox": bbox(shape), "area_mm2": r(shape.Area, 3), "faces": len(shape.Faces),
             "edges": len(shape.Edges), "solids": len(shape.Solids), "valid": shape.isValid()}
        if shape.Solids:
            m["volume_mm3"] = r(shape.Volume, 3)
            try:
                m["center_of_mass"] = vec(shape.CenterOfMass, 3)
            except Exception:
                pass
            if density_kg_m3:
                m["mass_g"] = r(shape.Volume * 1e-9 * density_kg_m3 * 1000, 3)
        out[obj.Name] = m
    if distance_between:
        if len(distance_between) != 2:
            raise ToolError("distance_between iki referans almalı, ör. ['Box:Face1', 'Box:Face2'].")
        shapes = []
        for ref in distance_between:
            name, _, sub = ref.partition(":")
            s = get_shape(get_object(name))
            shapes.append(s.getElement(sub) if sub else s)
        dist, pairs, _ = shapes[0].distToShape(shapes[1])
        out["distance_mm"] = r(dist, 4)
        if pairs:
            out["closest_points"] = [vec(pairs[0][0], 3), vec(pairs[0][1], 3)]
    if not out:
        raise ToolError("objects veya distance_between verilmeli.")
    return out


VIEWS = {"iso": "viewIsometric", "front": "viewFront", "top": "viewTop", "right": "viewRight",
         "back": "viewRear", "bottom": "viewBottom", "left": "viewLeft"}


def capture_view(view="iso"):
    try:
        import FreeCADGui as Gui
    except ImportError:
        raise ToolError("Görüntü alma yalnızca FreeCAD arayüzünde çalışır.")
    gdoc = getattr(Gui, "ActiveDocument", None)
    if gdoc is None:
        raise ToolError("Açık bir 3D görünüm yok.")
    v = gdoc.ActiveView
    getattr(v, VIEWS.get(view, "viewIsometric"))()
    v.fitAll()
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        v.saveImage(path, 800, 600, "White")
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
    finally:
        os.remove(path)
    return ToolResult(f"{view} görünümünün ekran görüntüsü ektedir.", image_png_b64=data)


def beam_hand_calc(case, length_mm, force_n=0.0, width_mm=None, height_mm=None, inertia_mm4=None,
                   fiber_dist_mm=None, area_mm2=None, youngs_modulus_mpa=210000.0, density_kg_m3=7850.0):
    """Euler-Bernoulli reference values, used to sanity-check FEM results."""
    L, P, E = float(length_mm), float(force_n), float(youngs_modulus_mpa)
    if width_mm and height_mm:
        b, h = float(width_mm), float(height_mm)
        inertia_mm4 = inertia_mm4 or b * h ** 3 / 12.0
        fiber_dist_mm = fiber_dist_mm or h / 2.0
        area_mm2 = area_mm2 or b * h
    res = {"case": case, "assumptions": "Euler-Bernoulli, lineer elastik, küçük şekil değiştirme"}
    if case == "axial":
        if not area_mm2:
            raise ToolError("axial için area_mm2 ya da width_mm+height_mm gerekli.")
        res.update(stress_mpa=r(P / area_mm2), elongation_mm=r(P * L / (E * area_mm2), 6), formula="σ=P/A, δ=PL/(EA)")
        return res
    if not inertia_mm4:
        raise ToolError("inertia_mm4 ya da width_mm+height_mm gerekli.")
    I = float(inertia_mm4)  # noqa: E741 (second moment of area, standard notation)
    if case == "cantilever_end_load":
        M = P * L
        res.update(max_moment_nmm=r(M), tip_deflection_mm=r(P * L ** 3 / (3 * E * I), 6),
                   formula="δ=PL³/(3EI), σ=PL·c/I")
    elif case == "simply_supported_center_load":
        M = P * L / 4
        res.update(max_moment_nmm=r(M), center_deflection_mm=r(P * L ** 3 / (48 * E * I), 6),
                   formula="δ=PL³/(48EI), σ=(PL/4)·c/I")
    elif case == "cantilever_first_frequency":
        if not area_mm2:
            raise ToolError("Frekans için area_mm2 ya da width_mm+height_mm gerekli.")
        EI = E * 1e6 * I * 1e-12  # N·m²
        rho_a = density_kg_m3 * area_mm2 * 1e-6  # kg/m
        f1 = (1.8751 ** 2) / (2 * math.pi * (L / 1000) ** 2) * math.sqrt(EI / rho_a)
        res.update(first_frequency_hz=r(f1, 2), formula="f₁=(1.8751²/(2πL²))·√(EI/(ρA))")
        return res
    else:
        raise ToolError("case: cantilever_end_load, simply_supported_center_load, axial, cantilever_first_frequency")
    if fiber_dist_mm:
        res["max_bending_stress_mpa"] = r(M * float(fiber_dist_mm) / I, 4)
    return res


TOOLS = [
    Tool("get_document_summary",
         "List objects in the active FreeCAD document with type, dimension properties, expressions, bounding box "
         "and volume. Call this first to learn object names.",
         {"type": "object", "properties": {
             "include_fem": {"type": "boolean", "description": "Also list FEM objects (analysis, constraints)."}}},
         get_document_summary, title="Document summary", example={}),
    Tool("get_selection",
         "Return what the user has selected in the 3D view: objects and faces/edges with geometry "
         "(type, area, center, normal, radius). Use these face names for edits and FEM loads.",
         {"type": "object", "properties": {}}, get_selection, title="Current selection"),
    Tool("find_faces",
         "Find faces of an object by criteria instead of guessing names. Example: the face at the far +X end "
         "-> extreme='max_x'; top flat face -> normal=[0,0,1]; holes of radius 4 -> surface_type='Cylinder', radius_mm=4.",
         {"type": "object", "properties": {
             "object": {"type": "string", "description": "Object name or label."},
             "surface_type": {"type": "string", "description": "Plane, Cylinder, Cone, Sphere, Toroid, BSplineSurface"},
             "normal": {"type": "array", "items": {"type": "number"}, "description": "Outward normal [x,y,z] of planar faces."},
             "extreme": {"type": "string", "enum": ["min_x", "max_x", "min_y", "max_y", "min_z", "max_z"],
                         "description": "Keep faces whose center is extreme along an axis."},
             "radius_mm": {"type": "number"},
             "angle_tol_deg": {"type": "number"}},
          "required": ["object"]},
         find_faces, title="Find faces", example={"object": "Plate", "normal": [0, 0, 1]}),
    Tool("list_edges",
         "List edges of an object (optionally only those bounding given faces, or of a curve type: Line, Circle).",
         {"type": "object", "properties": {
             "object": {"type": "string"},
             "curve_type": {"type": "string"},
             "faces": {"type": "array", "items": {"type": "string"}}},
          "required": ["object"]},
         list_edges, title="List edges", example={"object": "Plate", "faces": ["Face6"]}),
    Tool("measure",
         "Measure objects (bounding box, volume, area, mass, validity) and/or the minimum distance between two "
         "references like 'Box:Face1'. Use after every modeling change to verify dimensions.",
         {"type": "object", "properties": {
             "objects": {"type": "array", "items": {"type": "string"}},
             "density_kg_m3": {"type": "number"},
             "distance_between": {"type": "array", "items": {"type": "string"}}}},
         measure, title="Measure", example={"objects": ["Plate"]}),
    Tool("capture_view",
         "Take a screenshot of the 3D view from a standard direction so you can visually check the model.",
         {"type": "object", "properties": {
             "view": {"type": "string", "enum": list(VIEWS)}}},
         capture_view, title="Screenshot of the 3D view"),
    Tool("beam_hand_calc",
         "Hand-calculation reference values (Euler-Bernoulli) to sanity-check FEM results: cantilever_end_load, "
         "simply_supported_center_load, axial, cantilever_first_frequency. Units: mm, N, MPa, kg/m³.",
         {"type": "object", "properties": {
             "case": {"type": "string", "enum": ["cantilever_end_load", "simply_supported_center_load", "axial",
                                                 "cantilever_first_frequency"]},
             "length_mm": {"type": "number"},
             "force_n": {"type": "number"},
             "width_mm": {"type": "number", "description": "Rectangular section width (perpendicular to load)."},
             "height_mm": {"type": "number", "description": "Rectangular section height (in load direction)."},
             "inertia_mm4": {"type": "number"},
             "fiber_dist_mm": {"type": "number"},
             "area_mm2": {"type": "number"},
             "youngs_modulus_mpa": {"type": "number"},
             "density_kg_m3": {"type": "number"}},
          "required": ["case", "length_mm"]},
         beam_hand_calc, title="Beam hand calculation"),
]
