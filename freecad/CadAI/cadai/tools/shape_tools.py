"""Modeling without code: boxes, cylinders, holes, fillets, chamfers, booleans, moves.

run_python needs the FreeCAD API from memory, which small local models (7-14B) rarely get right. These tools take
plain numbers, build parametric Part features (they stay editable with set_property) and return the measured result,
so a model can verify without another call. An object name always means its latest version: after make_hole on
"Plate", "Plate" refers to the cut result, so a second hole does not silently drop the first one.
"""

import FreeCAD

from . import Tool, ToolError
from .geometry import active_doc, bbox, get_object, get_shape, latest_version, r, vec
from .model_tools import transaction

Vector = FreeCAD.Vector


def _vector(value, name, default=None):
    if value is None:
        if default is None:
            raise ToolError(f"{name} gerekli: [x, y, z] (mm).")
        return Vector(*default)
    try:
        nums = [float(v) for v in value]
    except (TypeError, ValueError):
        raise ToolError(f"{name} [x, y, z] biçiminde üç sayı olmalı, gelen: {value!r}")
    if len(nums) == 2:
        nums.append(0.0)
    if len(nums) != 3:
        raise ToolError(f"{name} [x, y, z] biçiminde üç sayı olmalı, gelen: {value!r}")
    return Vector(*nums)


def _direction(value, name="direction"):
    v = _vector(value, name)
    if v.Length < 1e-9:
        raise ToolError(f"{name} sıfır vektör olamaz.")
    return v.normalize()


def _positive(value, name):
    if value is None or float(value) <= 0:
        raise ToolError(f"{name} sıfırdan büyük bir sayı olmalı (mm).")
    return float(value)


def _size(diameter, radius, what):
    if diameter is not None:
        return _positive(diameter, "diameter") / 2
    if radius is not None:
        return _positive(radius, "radius")
    raise ToolError(f"{what} için diameter (çap, mm) gerekli.")


def _target(name):
    """(latest version, note, short name for new objects): Plate -> Plate_Hole -> Plate_Hole001, not Plate_Hole_Hole."""
    obj = get_object(name)
    latest = latest_version(obj)
    get_shape(latest)
    note = None if latest is obj else f"{obj.Name} artık {latest.Name} içinde; işlem {latest.Name} üzerine yapıldı."
    return latest, note, obj.Name


def _report(obj, **extra):
    shape = obj.Shape
    out = {"ok": True, "object": obj.Name, "label": obj.Label, "bbox": bbox(shape), "valid": shape.isValid()}
    if shape.Solids:
        out["volume_mm3"] = r(shape.Volume, 3)
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def _check(doc, *objs):
    doc.recompute()
    bad = [o for o in doc.Objects if "Invalid" in o.State or "Error" in o.State]
    if bad:
        raise ToolError("FreeCAD işlemi hesaplayamadı, değişiklik geri alındı: "
                        + ", ".join(f"{o.Name} {o.State}" for o in bad))
    for o in objs:
        shape = getattr(o, "Shape", None)
        if shape is None or shape.isNull():
            raise ToolError(f"{o.Name} boş geometri üretti, değişiklik geri alındı.")


def _hide(*objs):
    for o in objs:
        o.Visibility = False


def add_box(length, width, height, position=None, name="Box"):
    doc = active_doc(create=True)
    with transaction(doc, f"CadAI: {name}"):
        box = doc.addObject("Part::Box", name or "Box")
        box.Length, box.Width, box.Height = (_positive(length, "length"), _positive(width, "width"),
                                             _positive(height, "height"))
        box.Placement.Base = _vector(position, "position", (0, 0, 0))
        _check(doc, box)
    return _report(box, editable="set_property ile Length / Width / Height değiştirilebilir.")


def add_cylinder(diameter=None, height=None, position=None, direction=None, name="Cylinder", radius=None):
    doc = active_doc(create=True)
    rad = _size(diameter, radius, "Silindir")
    axis = _direction(direction or [0, 0, 1])
    with transaction(doc, f"CadAI: {name}"):
        cyl = doc.addObject("Part::Cylinder", name or "Cylinder")
        cyl.Radius, cyl.Height = rad, _positive(height, "height")
        cyl.Placement = FreeCAD.Placement(_vector(position, "position", (0, 0, 0)), FreeCAD.Rotation(Vector(0, 0, 1), axis))
        _check(doc, cyl)
    return _report(cyl, editable="set_property ile Radius / Height değiştirilebilir.")


def _surface_info(shape, point):
    """Nearest point on the solid's boundary and the direction pointing into the material there."""
    import Part

    dist, pairs, infos = shape.distToShape(Part.Vertex(point))
    entry = pairs[0][0]
    inward = None
    kind, index, params = infos[0][0], infos[0][1], infos[0][2]
    faces = []
    if kind == "Face":
        faces = [(shape.Faces[index], params)]
    else:  # on an edge or vertex: try every face touching it
        faces = [(f, None) for f in shape.Faces if f.distToShape(Part.Vertex(entry))[0] < 1e-6]
    for face, uv in faces:
        try:
            u, v = uv if uv is not None else face.Surface.parameter(entry)
            n = face.normalAt(u, v)
        except Exception:
            continue
        if n.Length < 1e-9:
            continue
        n.normalize()
        eps = max(shape.BoundBox.DiagonalLength * 1e-4, 1e-3)
        if shape.isInside(entry - n * eps, 1e-7, True):
            inward = -n
        elif shape.isInside(entry + n * eps, 1e-7, True):
            inward = n
        if inward is not None:
            break
    return dist, entry, inward


def make_hole(object, position, diameter=None, depth=None, direction=None, radius=None):
    doc = active_doc()
    target, note, stem = _target(object)
    shape = target.Shape
    rad = _size(diameter, radius, "Delik")
    point = _vector(position, "position")
    dist, entry, inward = _surface_info(shape, point)
    if direction is not None:
        axis = _direction(direction)
    elif inward is not None:
        axis = inward
    else:
        raise ToolError("Delik yönü bulunamadı (nokta bir yüzün üzerinde değil). direction ver, ör. [0, 0, -1].")
    snap = max(1.0, shape.BoundBox.DiagonalLength * 0.01)
    if dist > snap:
        raise ToolError(f"position {vec(point, 3)} parçanın yüzeyinde değil (en yakın yüzey noktası {vec(entry, 3)}, "
                        f"{r(dist, 3)} mm uzakta). find_faces sonucundaki bir 'center' değerini ya da yüzey üzerinde bir "
                        "nokta ver.")
    start = point if dist <= 1e-3 else entry
    through = depth is None or float(depth) <= 0
    length = shape.BoundBox.DiagonalLength * 2 + 2 if through else float(depth) + 1
    with transaction(doc, f"CadAI: {target.Name} delik Ø{2 * rad:g}"):
        tool = doc.addObject("Part::Cylinder", stem + "_HoleTool")
        tool.Radius, tool.Height = rad, length
        tool.Placement = FreeCAD.Placement(start - axis * 1, FreeCAD.Rotation(Vector(0, 0, 1), axis))
        cut = doc.addObject("Part::Cut", stem + "_Hole")
        cut.Base, cut.Tool = target, tool
        _hide(target, tool)
        _check(doc, cut)
        removed = shape.Volume - cut.Shape.Volume if shape.Solids else 0
        if removed <= 1e-6:
            raise ToolError("Delik parçaya değmiyor (hiç malzeme çıkmadı); değişiklik geri alındı. position yüzeyde "
                            "olmalı (find_faces 'center' değerlerini kullan) ve direction parçanın içine bakmalı.")
    return _report(cut, hole={"diameter_mm": r(2 * rad, 4), "entry": vec(start, 3), "direction": vec(axis, 4),
                              "depth_mm": "boydan boya" if through else r(float(depth), 4)},
                   removed_volume_mm3=r(removed, 3),
                   moved_to_surface=(f"Nokta yüzeyden {r(dist, 3)} mm uzaktaydı; en yakın yüzey noktası kullanıldı."
                                     if dist > 1e-3 else None),
                   note=note, next=f"Sonraki işlemlerde {cut.Name} (ya da {stem}) adını kullan.")


EDGE_SELECTORS = ("all", "top", "bottom", "vertical", "horizontal", "circular", "straight")


def _pick_edges(shape, edges):
    """Edge numbers (1-based) from ["Edge1", 3, ...] or a word: all, top, bottom, vertical, horizontal, circular."""
    if isinstance(edges, str) and edges.strip().lower() in EDGE_SELECTORS:
        word = edges.strip().lower()
        b = shape.BoundBox
        tol = max(b.DiagonalLength * 1e-6, 1e-6)
        import Part

        picked = []
        for i, e in enumerate(shape.Edges, 1):
            if len({f.hashCode() for f in shape.ancestorsOfType(e, Part.Face)}) < 2:
                continue  # seam of a cylinder/hole: not a real corner, a fillet there fails
            zs = [v.Point.z for v in e.Vertexes] or [e.BoundBox.ZMin, e.BoundBox.ZMax]
            kind = e.Curve.__class__.__name__
            line_dir = None
            if kind == "Line" and len(e.Vertexes) == 2:
                d = e.Vertexes[1].Point - e.Vertexes[0].Point
                line_dir = d.normalize() if d.Length > 0 else None
            ok = {"all": True,
                  "top": all(abs(z - b.ZMax) < tol for z in zs) and e.BoundBox.ZLength < tol,
                  "bottom": all(abs(z - b.ZMin) < tol for z in zs) and e.BoundBox.ZLength < tol,
                  "vertical": line_dir is not None and abs(abs(line_dir.z) - 1) < 1e-6,
                  "horizontal": e.BoundBox.ZLength < tol,
                  "circular": kind == "Circle",
                  "straight": kind == "Line"}[word]
            if ok:
                picked.append(i)
        if not picked:
            raise ToolError(f"'{word}' seçimine uyan kenar yok.")
        return picked
    if isinstance(edges, (str, int)):
        edges = [edges]
    picked = []
    for e in edges or []:
        text = str(e).strip()
        num = text[4:] if text.lower().startswith("edge") else text
        if not num.isdigit():
            raise ToolError(f"Kenar anlaşılamadı: {e!r}. 'Edge3' gibi ad, numara ya da şu kelimelerden biri: "
                            + ", ".join(EDGE_SELECTORS))
        i = int(num)
        if not 1 <= i <= len(shape.Edges):
            raise ToolError(f"Edge{i} yok: bu nesnede {len(shape.Edges)} kenar var (list_edges ile bak).")
        picked.append(i)
    if not picked:
        raise ToolError("edges boş. list_edges ile kenar adlarını bul ya da 'all', 'top', 'vertical' gibi bir kelime ver.")
    return sorted(set(picked))


def _edge_feature(kind, object, edges, size):
    doc = active_doc()
    target, note, stem = _target(object)
    size = _positive(size, "radius" if kind == "Fillet" else "size")
    picked = _pick_edges(target.Shape, edges)
    with transaction(doc, f"CadAI: {target.Name} {kind}"):
        feat = doc.addObject(f"Part::{kind}", f"{stem}_{kind}")
        feat.Base = target
        feat.Edges = [(i, size, size) for i in picked]
        _hide(target)
        try:
            _check(doc, feat)
        except ToolError as e:
            raise ToolError(f"{e} Ölçü ({size:g} mm) bu kenarlar için büyük olabilir; daha küçük dene ya da daha az kenar "
                            "seç.")
        if not feat.Shape.isValid():
            raise ToolError(f"{kind} geçersiz geometri üretti, değişiklik geri alındı. Daha küçük ölçü dene.")
    return _report(feat, edges=[f"Edge{i}" for i in picked], note=note,
                   next=f"Sonraki işlemlerde {feat.Name} (ya da {stem}) adını kullan.")


def fillet_edges(object, edges, radius):
    return _edge_feature("Fillet", object, edges, radius)


def chamfer_edges(object, edges, size):
    return _edge_feature("Chamfer", object, edges, size)


BOOLEAN_TYPES = {"cut": "Part::Cut", "fuse": "Part::Fuse", "common": "Part::Common"}


def boolean(operation, base, tool):
    doc = active_doc()
    a, note_a, stem = _target(base)
    b, note_b, _ = _target(tool)
    if a == b:
        raise ToolError("base ve tool aynı nesne olamaz.")
    vol_a = a.Shape.Volume if a.Shape.Solids else 0
    with transaction(doc, f"CadAI: {operation} {a.Name} {b.Name}"):
        res = doc.addObject(BOOLEAN_TYPES[operation], f"{stem}_{operation.capitalize()}")
        res.Base, res.Tool = a, b
        _hide(a, b)
        _check(doc, res)
        if not res.Shape.Solids:
            raise ToolError(f"{operation} sonucu boş: nesneler birbirine değmiyor olabilir; değişiklik geri alındı.")
    return _report(res, volume_change_mm3=r(res.Shape.Volume - vol_a, 3),
                   note=" ".join(n for n in (note_a, note_b) if n) or None)


def move_object(object, position=None, offset=None, rotation_axis=None, rotation_deg=None):
    doc = active_doc()
    obj, note, _ = _target(object)
    if position is None and offset is None and not rotation_deg:
        raise ToolError("position (yeni konum), offset (kaydırma) ya da rotation_deg (döndürme) ver.")
    old = FreeCAD.Placement(obj.Placement)
    with transaction(doc, f"CadAI: {obj.Name} taşı"):
        pl = FreeCAD.Placement(obj.Placement)
        if rotation_deg:
            center = obj.Shape.BoundBox.Center
            rot = FreeCAD.Placement(Vector(), FreeCAD.Rotation(_direction(rotation_axis or [0, 0, 1], "rotation_axis"),
                                                               float(rotation_deg)), center)
            pl = rot.multiply(pl)
        if position is not None:
            pl.Base = _vector(position, "position")
        if offset is not None:
            pl.Base = pl.Base + _vector(offset, "offset")
        obj.Placement = pl
        _check(doc, obj)
    return _report(obj, old_position=vec(old.Base, 3), new_position=vec(obj.Placement.Base, 3), note=note)


VEC = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}


def _vec(desc):
    return dict(VEC, description=desc)


TOOLS = [
    Tool("add_box",
         "Create a box (block, plate) from length (X), width (Y), height (Z) in mm. position is its corner with the "
         "smallest x, y, z. Parametric: change sizes later with set_property.",
         {"type": "object", "properties": {
             "length": {"type": "number", "description": "X size, mm"},
             "width": {"type": "number", "description": "Y size, mm"},
             "height": {"type": "number", "description": "Z size, mm"},
             "position": _vec("Corner [x, y, z], mm. Default [0, 0, 0]."),
             "name": {"type": "string", "description": "Object name, e.g. 'Plate'."}},
          "required": ["length", "width", "height"]},
         add_box, mutates=True, destructive=False, title="Add a box",
         example={"length": 80, "width": 60, "height": 10, "name": "Plate"}),
    Tool("add_cylinder",
         "Create a cylinder (shaft, pin, boss) from diameter and height in mm. position is the center of its bottom "
         "circle; direction is its axis (default up [0, 0, 1]).",
         {"type": "object", "properties": {
             "diameter": {"type": "number", "description": "mm"},
             "height": {"type": "number", "description": "mm, along direction"},
             "position": _vec("Center of the bottom circle [x, y, z], mm. Default [0, 0, 0]."),
             "direction": _vec("Axis, default [0, 0, 1]."),
             "name": {"type": "string"},
             "radius": {"type": "number", "description": "Instead of diameter."}},
          "required": ["height"]},
         add_cylinder, mutates=True, destructive=False, title="Add a cylinder",
         example={"diameter": 20, "height": 40, "position": [0, 0, 0], "name": "Shaft"}),
    Tool("make_hole",
         "Drill a round hole into an object. position: a point on the face where the hole starts (take 'center' from "
         "find_faces or a marker point). The hole goes into the material, through the whole part unless depth is "
         "given. Returns the new object (e.g. Plate_Hole); the old name keeps working.",
         {"type": "object", "properties": {
             "object": {"type": "string", "description": "Object name or label."},
             "position": _vec("Hole center on the surface [x, y, z], mm."),
             "diameter": {"type": "number", "description": "mm"},
             "depth": {"type": "number", "description": "mm. Leave out for a through hole."},
             "direction": _vec("Drilling direction. Default: into the face at position."),
             "radius": {"type": "number", "description": "Instead of diameter."}},
          "required": ["object", "position"]},
         make_hole, mutates=True, title="Drill a hole",
         example={"object": "Plate", "position": [40, 30, 10], "diameter": 8}),
    Tool("fillet_edges",
         "Round edges of an object with a radius. edges: names from list_edges (['Edge1', 'Edge5']) or one word: "
         "'all', 'top', 'bottom', 'vertical', 'horizontal', 'circular'.",
         {"type": "object", "properties": {
             "object": {"type": "string"},
             "edges": {"type": ["array", "string"], "items": {"type": "string"},
                       "description": "['Edge1', 'Edge3'] or all / top / bottom / vertical / horizontal / circular"},
             "radius": {"type": "number", "description": "mm"}},
          "required": ["object", "edges", "radius"]},
         fillet_edges, mutates=True, title="Fillet edges",
         example={"object": "Plate", "edges": "vertical", "radius": 3}),
    Tool("chamfer_edges",
         "Bevel (chamfer) edges of an object by a size in mm (45°). edges: names from list_edges or one word: "
         "'all', 'top', 'bottom', 'vertical', 'horizontal', 'circular'.",
         {"type": "object", "properties": {
             "object": {"type": "string"},
             "edges": {"type": ["array", "string"], "items": {"type": "string"},
                       "description": "['Edge2', 'Edge4'] or all / top / bottom / vertical / horizontal / circular"},
             "size": {"type": "number", "description": "mm"}},
          "required": ["object", "edges", "size"]},
         chamfer_edges, mutates=True, title="Chamfer edges",
         example={"object": "Plate", "edges": "top", "size": 1}),
    Tool("boolean",
         "Combine two objects: 'cut' removes tool from base, 'fuse' joins them, 'common' keeps the overlap.",
         {"type": "object", "properties": {
             "operation": {"type": "string", "enum": list(BOOLEAN_TYPES)},
             "base": {"type": "string", "description": "Object that stays (cut: the part)."},
             "tool": {"type": "string", "description": "Object used on it (cut: what is removed)."}},
          "required": ["operation", "base", "tool"]},
         boolean, mutates=True, title="Cut / fuse / common",
         example={"operation": "cut", "base": "Plate", "tool": "Box"}),
    Tool("move_object",
         "Move or rotate an object. position: new placement position [x, y, z]; offset: move by [dx, dy, dz]; "
         "rotation_deg about rotation_axis (default Z) through the object's center.",
         {"type": "object", "properties": {
             "object": {"type": "string"},
             "position": _vec("New position [x, y, z], mm."),
             "offset": _vec("Move by [dx, dy, dz], mm."),
             "rotation_axis": _vec("Default [0, 0, 1]."),
             "rotation_deg": {"type": "number"}},
          "required": ["object"]},
         move_object, mutates=True, idempotent=False, title="Move / rotate",
         example={"object": "Shaft", "offset": [0, 0, 10]}),
]
