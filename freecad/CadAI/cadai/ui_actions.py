"""UI actions for the VS Code extension: scene geometry, model tree, selection, document commands.

All functions run on FreeCAD's GUI thread (the bridge dispatches them there). They are not exposed to the AI;
the AI uses the tools in cadai.tools.
"""

import base64
import math
import os
import sys
from array import array

import FreeCAD

from .tools import ToolError
from .tools.geometry import active_doc, dimension_properties, get_object, r
from .tools.inspect_tools import fem_target
from .tools.model_tools import transaction

# Change counters read by GET /version (plain ints: safe to read from the HTTP thread).
STATE = {"doc": 0, "sel": 0, "mk": 0}
_observers = []


def _bump(key):
    STATE[key] += 1


class _DocObserver:
    def slotCreatedDocument(self, doc): _bump("doc")
    def slotDeletedDocument(self, doc): _bump("doc")
    def slotActivateDocument(self, doc): _bump("doc")
    def slotCreatedObject(self, obj): _bump("doc")
    def slotDeletedObject(self, obj): _bump("doc")
    def slotChangedObject(self, obj, prop): _bump("doc")
    def slotRecomputedDocument(self, doc): _bump("doc")
    def slotUndoDocument(self, doc): _bump("doc")
    def slotRedoDocument(self, doc): _bump("doc")


class _SelObserver:
    def addSelection(self, *a): _bump("sel")
    def removeSelection(self, *a): _bump("sel")
    def setSelection(self, *a): _bump("sel")
    def clearSelection(self, *a): _bump("sel")


def install_observers():
    import FreeCADGui as Gui

    uninstall_observers()
    d, s = _DocObserver(), _SelObserver()
    FreeCAD.addDocumentObserver(d)
    Gui.Selection.addObserver(s)
    _observers.extend([d, s])


def uninstall_observers():
    try:
        import FreeCADGui as Gui
    except ImportError:
        Gui = None
    for o in _observers:
        try:
            if isinstance(o, _DocObserver):
                FreeCAD.removeDocumentObserver(o)
            elif Gui is not None:
                Gui.Selection.removeObserver(o)
        except Exception:
            pass
    _observers.clear()


# ---------------- helpers ----------------

SKIP_PREFIXES = ("Fem::", "App::Origin", "App::Line", "App::Plane", "PartDesign::Plane", "PartDesign::Line",
                 "PartDesign::Point", "PartDesign::CoordinateSystem")


def _in_body(obj):
    try:
        parent = obj.getParentGeoFeatureGroup()
    except Exception:
        return False
    return parent is not None and parent.TypeId == "PartDesign::Body"


def _color(obj):
    vo = getattr(obj, "ViewObject", None)
    try:
        c = vo.ShapeAppearance[0].DiffuseColor
        return [r(c[0], 3), r(c[1], 3), r(c[2], 3)]
    except Exception:
        pass
    try:
        c = vo.ShapeColor
        return [r(c[0], 3), r(c[1], 3), r(c[2], 3)]
    except Exception:
        return [0.8, 0.8, 0.8]


def _b64(typecode, values):
    """Little-endian binary + base64: ~3x faster to build than rounded JSON number lists and parsed natively by the
    viewer (float32 positions, uint16/uint32 indices)."""
    a = array(typecode, values)
    if sys.byteorder == "big":
        a.byteswap()
    return base64.b64encode(a.tobytes()).decode("ascii")


def _xyz(points):
    return [c for p in points for c in (p.x, p.y, p.z)]


# Tessellations of displayed objects, reused while an object's shape is unchanged. The cached TopoShape is kept
# alive on purpose: its TShape cannot be freed and its address reused by a different shape with the same hash.
_SCENE_CACHE = {"doc": None, "objects": {}}


def _scene_tolerance(diag, quality):
    """Chordal deflection, snapped to powers of two so small changes of the scene's size keep every cache entry."""
    raw = max(diag * 0.0015 / max(quality, 0.1), 0.005)
    return 2.0 ** round(math.log2(raw))


def _tessellate(obj, shape, tol, color):
    faces = []
    for i, f in enumerate(shape.Faces, 1):
        try:
            pts, tris = f.tessellate(tol)
        except Exception:
            continue
        if not tris:
            continue
        wide = len(pts) >= 65536
        faces.append({"name": f"Face{i}", "positions": _b64("f", _xyz(pts)), "wide": wide,
                      "indices": _b64("I" if wide else "H", [k for t in tris for k in t])})
    edges = []
    for i, e in enumerate(shape.Edges, 1):
        try:
            pts = e.discretize(Deflection=tol)
        except Exception:
            continue
        edges.append({"name": f"Edge{i}", "points": _b64("f", _xyz(pts))})
    return {"name": obj.Name, "label": obj.Label, "type": obj.TypeId, "color": color, "enc": "b64",
            "faces": faces, "edges": edges, "vertices": _b64("f", _xyz(v.Point for v in shape.Vertexes))}


def _display_objects(doc):
    objs = []
    for obj in doc.Objects:
        if obj.TypeId.startswith(SKIP_PREFIXES) or not getattr(obj, "Visibility", True):
            continue
        if _in_body(obj) and obj.TypeId != "Sketcher::SketchObject":
            continue  # draw the Body, not its features
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull() or not shape.BoundBox.isValid():
            continue
        objs.append(obj)
    return objs


def _display_name(obj, displayed):
    if obj.Name in displayed:
        return obj.Name
    target = fem_target(obj)
    return target.Name


# ---------------- actions ----------------

def selection():
    import FreeCADGui as Gui

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return []
    displayed = {o.Name for o in _display_objects(doc)}
    items = []
    for sel in Gui.Selection.getSelectionEx():
        name = _display_name(sel.Object, displayed)
        subs = [s.split(".")[-1] for s in sel.SubElementNames] or [""]
        for sub in subs:
            items.append({"object": name, "label": sel.Object.Label, "sub": sub})
    return items


def scene(quality=1.0, known=None):
    """Geometry for the VS Code 3D view. Every object carries a "key" that changes with its shape, color, label or
    the tessellation tolerance. Objects whose key is in `known` (what the viewer already shows) come back as
    {"name", "key", "same": true} without geometry; unchanged objects are never tessellated twice."""
    doc = FreeCAD.ActiveDocument
    if doc is None:
        return {"doc": None, "objects": [], "selection": []}
    import Part

    if _SCENE_CACHE["doc"] != doc.Name:
        _SCENE_CACHE.update(doc=doc.Name, objects={})
    cache = _SCENE_CACHE["objects"]
    known = set(known or ())
    objs = _display_objects(doc)
    bb = FreeCAD.BoundBox()
    for obj in objs:
        bb.add(obj.Shape.BoundBox)
    diag = bb.DiagonalLength if bb.isValid() else 100.0
    tol = _scene_tolerance(diag, quality)
    out, stats = [], {"tessellated": 0, "cached": 0, "unchanged": 0}
    for obj in objs:
        # obj.Shape keeps its identity until the object is recomputed (Part.getShape returns a fresh copy every call),
        # so it is the cache key; the global placement covers moves of a parent container.
        own = obj.Shape
        try:
            where = str(obj.getGlobalPlacement())
        except Exception:
            where = ""
        color = _color(obj)
        key = f"{obj.Name}|{own.hashCode()}|{where}|{tol!r}|{color}|{obj.Label}"
        entry = cache.get(obj.Name)
        if not (entry and entry["key"] == key and entry["shape"].isEqual(own)):
            data = _tessellate(obj, Part.getShape(obj), tol, color)
            entry = cache[obj.Name] = {"key": key, "shape": own, "data": dict(data, key=key)}
            stats["tessellated"] += 1
        elif key in known:
            out.append({"name": obj.Name, "label": obj.Label, "key": key, "same": True})
            stats["unchanged"] += 1
            continue
        else:
            stats["cached"] += 1
        out.append(entry["data"])
    for name in set(cache) - {o.Name for o in objs}:
        del cache[name]
    box = None
    if bb.isValid():
        box = {"min": [bb.XMin, bb.YMin, bb.ZMin], "max": [bb.XMax, bb.YMax, bb.ZMax]}
    return {"doc": doc.Name, "label": doc.Label, "objects": out, "bbox": box, "selection": selection(),
            "tolerance_mm": tol, "stats": stats}


def tree():
    import FreeCAD as App

    doc = App.ActiveDocument
    docs = [{"name": d.Name, "label": d.Label, "file": d.FileName} for d in App.listDocuments().values()]
    if doc is None:
        return {"active": None, "documents": docs, "objects": []}

    def node(obj):
        item = {"name": obj.Name, "label": obj.Label, "type": obj.TypeId,
                "visible": bool(getattr(obj, "Visibility", True)), "dims": dimension_properties(obj)}
        if "Invalid" in obj.State or "Error" in obj.State:
            item["error"] = True
        kids = [o for o in getattr(obj, "Group", []) or [] if not o.TypeId.startswith("App::Origin")]
        if kids:
            item["children"] = [node(k) for k in kids]
        return item

    top = []
    for obj in doc.Objects:
        if obj.TypeId.startswith(("App::Origin", "App::Line", "App::Plane")):
            continue
        try:
            if obj.getParentGroup() is not None or obj.getParentGeoFeatureGroup() is not None:
                continue
        except Exception:
            pass
        if obj.Name.startswith("CadAI_Analysis_") and "_Dir" in obj.Name:
            continue
        top.append(node(obj))
    return {"active": doc.Name, "label": doc.Label, "file": doc.FileName, "documents": docs, "objects": top}


def set_selection(object=None, sub="", additive=False):
    import FreeCADGui as Gui

    doc = active_doc()
    if not additive:
        Gui.Selection.clearSelection()
    if object:
        obj = get_object(object, doc)
        Gui.Selection.addSelection(doc.Name, obj.Name, sub or "")
    return selection()


def clear_selection():
    import FreeCADGui as Gui

    Gui.Selection.clearSelection()
    return []


def new_document(name="Model"):
    doc = FreeCAD.newDocument(name)
    FreeCAD.setActiveDocument(doc.Name)
    try:
        import FreeCADGui as Gui

        Gui.activeDocument().activeView().viewIsometric()
    except Exception:
        pass
    return {"name": doc.Name}


def open_document(path):
    if not os.path.isfile(path):
        raise ToolError(f"Dosya yok: {path}")
    ext = os.path.splitext(path)[1].lower()
    if ext == ".fcstd":
        doc = FreeCAD.openDocument(path)
    else:
        import Import

        doc = FreeCAD.ActiveDocument or FreeCAD.newDocument("Model")
        Import.insert(path, doc.Name)
        doc.recompute()
    FreeCAD.setActiveDocument(doc.Name)
    return {"name": doc.Name}


def save_document(path=None):
    doc = active_doc()
    if path:
        if not path.lower().endswith(".fcstd"):
            path += ".FCStd"
        doc.saveAs(path)
    elif doc.FileName:
        doc.save()
    else:
        raise ToolError("Belge daha önce kaydedilmemiş; bir dosya yolu gerekli.")
    return {"file": doc.FileName}


def activate_document(name):
    FreeCAD.setActiveDocument(name)
    try:
        import FreeCADGui as Gui

        Gui.getDocument(name).activeView()
        Gui.ActiveDocument = Gui.getDocument(name)
    except Exception:
        pass
    return {"active": name}


def undo():
    doc = active_doc()
    if doc.UndoCount == 0:
        raise ToolError("Geri alınacak işlem yok.")
    doc.undo()
    doc.recompute()
    return {"undo_left": doc.UndoCount, "redo_left": doc.RedoCount}


def redo():
    doc = active_doc()
    if doc.RedoCount == 0:
        raise ToolError("Yinelenecek işlem yok.")
    doc.redo()
    doc.recompute()
    return {"undo_left": doc.UndoCount, "redo_left": doc.RedoCount}


def recompute():
    doc = active_doc()
    doc.recompute()
    return {"invalid": [o.Name for o in doc.Objects if "Invalid" in o.State]}


def delete_object(name):
    doc = active_doc()
    obj = get_object(name, doc)
    with transaction(doc, f"CadAI: {obj.Label} sil"):
        for child in list(getattr(obj, "Group", []) or []):
            if doc.getObject(child.Name) is not None and not child.TypeId.startswith("App::Origin"):
                doc.removeObject(child.Name)
        doc.removeObject(obj.Name)
        doc.recompute()
    return {"deleted": name}


def set_visibility(name, visible):
    obj = get_object(name)
    obj.Visibility = bool(visible)
    return {"name": obj.Name, "visible": obj.Visibility}


PRIMITIVES = {
    "box": ("Part::Box", {"length": "Length", "width": "Width", "height": "Height"}),
    "cylinder": ("Part::Cylinder", {"radius": "Radius", "height": "Height"}),
    "sphere": ("Part::Sphere", {"radius": "Radius"}),
    "cone": ("Part::Cone", {"radius1": "Radius1", "radius2": "Radius2", "height": "Height"}),
    "torus": ("Part::Torus", {"radius1": "Radius1", "radius2": "Radius2"}),
}


def add_primitive(kind, params=None, position=None, name=None):
    if kind not in PRIMITIVES:
        raise ToolError(f"Bilinmeyen şekil: {kind}")
    type_id, mapping = PRIMITIVES[kind]
    doc = active_doc(create=True)
    with transaction(doc, f"CadAI: {kind} ekle"):
        obj = doc.addObject(type_id, name or kind.capitalize())
        for key, value in (params or {}).items():
            if key in mapping and value not in (None, ""):
                setattr(obj, mapping[key], float(value))
        if position:
            obj.Placement.Base = FreeCAD.Vector(*[float(v) for v in position])
        doc.recompute()
    return {"name": obj.Name}


def show_freecad():
    import FreeCADGui as Gui

    mw = Gui.getMainWindow()
    mw.showNormal()
    mw.raise_()
    mw.activateWindow()
    return {"ok": True}


def minimize_freecad():
    import FreeCADGui as Gui

    Gui.getMainWindow().showMinimized()
    return {"ok": True}


def start_debugger(port, host="127.0.0.1"):
    """Connect FreeCAD to the debug adapter VS Code is listening with (debugpy attach + "listen").

    FreeCAD connects to VS Code instead of listening itself: debugpy.listen() works once per process, and once its
    adapter has gone (VS Code "Stop", a crash) a second listen() raises while nothing listens any more, so the debugger
    could not be attached again without restarting FreeCAD. connect() can be repeated (also after an earlier listen()).
    It blocks until VS Code has sent its breakpoints, a fraction of a second."""
    import sys

    try:
        import debugpy
    except ImportError:
        raise ToolError("debugpy kurulu değil. FreeCAD'in Python'una kurun: "
                        "<FreeCAD>/bin/python -m pip install --user debugpy")
    import time

    from debugpy.server import api

    # while an earlier debugger object exists pydevd silently ignores connect() and VS Code would wait forever: give a
    # session that is just closing (attach right after "Stop") a moment, then clear what is left over (an old listen())
    deadline = time.time() + 3
    while api.get_global_debugger() is not None:
        if debugpy.is_client_connected():
            raise ToolError("FreeCAD zaten bir hata ayıklayıcıya bağlı.")
        if time.time() > deadline:
            api.pydevd.stoptrace()
            break
        time.sleep(0.1)
    python = os.path.join(os.path.dirname(sys.executable), "python.exe" if os.name == "nt" else "python")
    debugpy.configure(python=python)
    try:
        debugpy.connect((host, int(port)))
    except OSError as e:
        api.pydevd.stoptrace()  # a failed connect leaves a half-made debugger that would swallow the next connect()
        raise ToolError(f"VS Code'un hata ayıklayıcısına bağlanılamadı ({host}:{port}): {e}")
    return {"host": host, "port": int(port), "connected": True}


def _load_markers():
    from .tools import marker_tools

    return marker_tools.load()


def markers():
    """Markers for the UI, each with "trusted": False when it came inside a file instead of being made here."""
    from .tools import marker_tools

    return [dict(m, trusted=marker_tools.is_trusted(m)) for m in _load_markers()]


def _save_markers(items):
    from .tools import marker_tools

    marker_tools.save(items)
    _bump("mk")
    return items


def add_marker(marker):
    """marker: {"kind": "point"|"dimension"|"line"|"circle"|"pen", "a": target, ..., "note"}
    target: {"object", "label", "element", "snap": "vertex"|"edge"|"face", "point": [x,y,z], "normal"?}
    Markers made through this (local UI) path are signed; see marker_tools.sign."""
    from .tools import marker_tools

    items = _load_markers()
    marker = {k: v for k, v in dict(marker).items() if k not in ("sig", "trusted", "id")}
    marker["id"] = max([m["id"] for m in items] + [0]) + 1
    marker.setdefault("kind", "point")
    marker.setdefault("note", "")
    marker_tools.sign(marker)
    items.append(marker)
    _save_markers(items)
    return dict(marker, trusted=True)


def update_marker(id, note):
    """Editing a note in the local UI is the user vouching for it, so the marker is (re)signed."""
    from .tools import marker_tools

    items = _load_markers()
    for m in items:
        if m["id"] == id:
            m["note"] = note
            marker_tools.sign(m)
            _save_markers(items)
            return dict(m, trusted=True)
    raise ToolError(f"İşaret #{id} yok.")


def delete_marker(id):
    items = [m for m in _load_markers() if m["id"] != id]
    _save_markers(items)
    return items


def clear_markers():
    return _save_markers([])


def history_status():
    from . import history

    return history.status()


def history_configure(enabled=None, folder=None, notes=None, fcstd=None):
    from . import history

    out = history.configure(enabled, folder, notes, fcstd)
    if enabled and FreeCAD.ActiveDocument is not None:
        history.record(FreeCAD.ActiveDocument, ["Geçmiş açıldı"], "VS Code")  # first snapshot right away
    return out


def history_log(limit=50):
    from . import history

    return history.log(limit=limit)


def history_open_version(commit):
    from . import history

    try:
        return history.open_version(commit)
    except RuntimeError as e:
        raise ToolError(str(e))


def gpu_info():
    """Which GPU FreeCAD's OpenGL runs on and the 3D-view settings that matter for speed (see cadai.gpu)."""
    from . import gpu

    return gpu.info()


def gpu_apply_recommended(vbo=True):
    from . import gpu

    return gpu.apply_recommended(bool(vbo))


def fem_field(analysis=None, quantity="von_mises", mode=1):
    """FEM results on the part surface for the 3D view's color map (see fem_tools.result_field)."""
    from .tools import fem_tools

    return fem_tools.result_field(analysis, quantity, mode)


def external_status():
    """Other programs CadAI drives (Blender, OpenSCAD, KiCad, build123d/CadQuery): found or not, version."""
    from . import external

    return external.status()


def external_configure(paths=None):
    """Program paths from VS Code's machine-scope settings ({"blender": "...", ...}; empty = auto-detect)."""
    from . import external

    return external.configure(paths or {})


def reload_addon():
    from PySide import QtCore

    from .gui import commands

    QtCore.QTimer.singleShot(300, lambda: commands.ReloadCommand().Activated())
    return {"ok": True, "note": "Eklenti yeniden yükleniyor; köprü birkaç saniye içinde yeniden açılır."}


def design_requirements():
    from .tools.requirement_tools import check_design_requirements

    return check_design_requirements(include_definitions=True)


def update_design_requirements(document, requirements, remove_ids=None):
    from .tools.requirement_tools import set_design_requirements

    if active_doc().Name != document:
        raise ToolError("Belge değişti; şart listesini yenileyip tekrar deneyin.")
    set_design_requirements(requirements, remove_ids)
    return design_requirements()


def export_requirements(document, path):
    from .tools.requirement_tools import export_design_report

    if active_doc().Name != document:
        raise ToolError("Belge değişti; raporu yeni belge üzerinden yeniden isteyin.")
    return export_design_report(path)


ACTIONS = {f.__name__: f for f in (
    scene, tree, selection, set_selection, clear_selection, new_document, open_document, save_document,
    activate_document, undo, redo, recompute, delete_object, set_visibility, add_primitive, show_freecad,
    minimize_freecad, start_debugger, reload_addon, markers, add_marker, update_marker, delete_marker,
    clear_markers, fem_field, gpu_info, gpu_apply_recommended, history_status, history_configure, history_log,
    history_open_version, external_status, external_configure, design_requirements, update_design_requirements,
    export_requirements)}


def run(action, args):
    from . import history

    fn = ACTIONS.get(action)
    if fn is None:
        raise ToolError(f"Bilinmeyen işlem: {action}")
    with history.source(f"VS Code · {action}"):
        return fn(**(args or {}))
