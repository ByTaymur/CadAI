"""Other open-source CAD programs, driven from CadAI (cadai.external): OpenSCAD and build123d / CadQuery code
becomes a FreeCAD solid, a KiCad board comes in with its components. All of them run as separate processes; the
result lands in the open FreeCAD document, so the 3D view, measuring, FEM, DFM, drawings and render work on it.

Code-made parts keep their source in the object (CadAI group: CadAIEngine, CadAISource, CadAIParams), so the part
can be regenerated later with other parameters: code_cad(replace=..., params=...).
"""

import contextlib
import csv
import io
import json
import os
import re
import shutil
import tempfile
from collections import Counter

from . import Tool, ToolError
from .geometry import active_doc, bbox, get_object, r

LANGUAGES = ("openscad", "build123d", "cadquery")
CODE_TIMEOUT = 300


def _ext():
    from .. import external

    return external


def external_tools():
    """Which of the other programs are installed, their versions and how to install the missing ones."""
    return {"programs": _ext().status(),
            "note": "Eksik program için 'install' komutunu kullanıcıya öner (VS Code: CadAI → Harici araçlar). "
                    "Programlar ayrı süreçte, penceresiz çalışır; sonuç açık FreeCAD belgesine gelir."}


# ---------------- shared ----------------

def _safe_name(text, default):
    return re.sub(r"\W+", "_", text or "")[:40].strip("_") or default


def _shape_report(obj):
    shape = obj.Shape
    types = Counter(f.Surface.__class__.__name__ for f in shape.Faces)
    out = {"object": obj.Name, "label": obj.Label, "bbox": bbox(shape), "solids": len(shape.Solids),
           "faces": len(shape.Faces), "face_types": dict(types.most_common()), "valid": shape.isValid()}
    if shape.Solids:
        out["volume_mm3"] = r(shape.Volume, 3)
    return out


def _store(doc, shape, name, replace, engine, source, params, source_file, label=None):
    """Put the shape in a Part::Feature that remembers how it was made (new, or the replaced object)."""
    if replace:
        obj = get_object(replace, doc)
        if obj.TypeId != "Part::Feature":
            raise ToolError(f"{obj.Name} kodla üretilmiş bir parça değil; replace yalnızca code_cad'in ürettiği "
                            "nesnelerde kullanılır.")
    else:
        obj = doc.addObject("Part::Feature", _safe_name(name, engine.capitalize()))
        if label or name:
            obj.Label = label or name
    for prop, kind, doc_text in (("CadAIEngine", "App::PropertyString", "Program that made this part"),
                                 ("CadAISource", "App::PropertyString", "Source code of the part"),
                                 ("CadAIParams", "App::PropertyString", "Parameters (JSON)"),
                                 ("CadAISourceFile", "App::PropertyString", "Source file the code came from")):
        if prop not in obj.PropertiesList:
            obj.addProperty(kind, prop, "CadAI", doc_text)
    placement = obj.Placement
    obj.Shape = shape
    if replace:
        obj.Placement = placement  # assigning a Shape also sets the placement; keep where the user moved it
    obj.CadAIEngine = engine
    obj.CadAISource = source
    obj.CadAIParams = json.dumps(params or {}, ensure_ascii=False)
    obj.CadAISourceFile = source_file or ""
    return obj


def _read_file(path, exts):
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise ToolError(f"Dosya yok: {path}")
    if not path.lower().endswith(exts):
        raise ToolError(f"Beklenen uzantı: {', '.join(exts)} ({path})")
    with open(path, encoding="utf-8", errors="replace") as f:
        return path, f.read()


def _solidify(shape):
    """Fuse loose solids (OpenSCAD's implicit top-level union) and merge coplanar faces."""
    import Part

    solids = shape.Solids
    if len(solids) > 1:
        try:
            fused = solids[0].multiFuse(solids[1:])
            if fused.isValid() and fused.Solids:
                shape = fused
        except Part.OCCError:
            pass
    try:
        refined = shape.removeSplitter()
        if refined.isValid():
            shape = refined
    except Part.OCCError:
        pass
    return shape


# ---------------- OpenSCAD ----------------

def _scad_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float, str, list)):
        return json.dumps(v)
    raise ToolError(f"OpenSCAD parametresi sayı, metin, mantıksal ya da liste olmalı: {v!r}")


def _openscad_run(exe, scad, out, params, cwd, extra=()):
    args = [exe, "-o", out, *extra]
    for k, v in (params or {}).items():
        if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_]*", k):
            raise ToolError(f"Geçersiz OpenSCAD parametre adı: {k!r}")
        args += ["-D", f"{k}={_scad_value(v)}"]
    p = _ext().run(args + [scad], timeout=CODE_TIMEOUT, cwd=cwd)
    log = (p.stdout + "\n" + p.stderr).strip()
    return p.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 0, log


def _openscad_shape(code, params, cwd, mode, doc):
    import FreeCAD
    import Mesh
    import Part

    ext = _ext()
    exe = ext.find("openscad", required=True)
    work = tempfile.mkdtemp(prefix="cadai_scad_")
    try:
        scad = os.path.join(work, "model.scad")
        with open(scad, "w", encoding="utf-8") as f:
            f.write(code)
        log = ""
        if mode == "brep":
            # FreeCAD's own importer turns OpenSCAD's CSG tree into exact solids (a cylinder stays a cylinder);
            # what it cannot (minkowski, hull, text...) it meshes by calling OpenSCAD itself.
            csg = os.path.join(work, "model.csg")
            ok, log = _openscad_run(exe, scad, csg, params, cwd)
            if not ok:
                raise ToolError("OpenSCAD hata verdi:\n" + ext.tail(log, 3000))
            pref = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/OpenSCAD")
            if not os.path.isfile(pref.GetString("openscadexecutable", "")):
                pref.SetString("openscadexecutable", exe)
            try:
                import importCSG
            except ImportError:
                import sys

                sys.path.append(os.path.join(FreeCAD.getHomePath(), "Mod", "OpenSCAD"))
                import importCSG

            before = {o.Name for o in doc.Objects}
            # importCSG's ply parser prints grammar warnings ("Token 'WORD' defined, but not used") on every import;
            # FreeCAD's report view shows them as red errors
            with contextlib.redirect_stderr(io.StringIO()):
                importCSG.insert(csg, doc.Name)
            doc.recompute()
            created = [o for o in doc.Objects if o.Name not in before]
            created_names = {o.Name for o in created}
            tops = [o for o in created if not any(p.Name in created_names for p in o.InList)]
            shapes = [o.Shape for o in tops if getattr(o, "Shape", None) is not None and not o.Shape.isNull()]
            for o in sorted(created, key=lambda o: len(o.OutList)):  # parents first
                if doc.getObject(o.Name) is not None:
                    doc.removeObject(o.Name)
            if not shapes:
                raise ToolError("OpenSCAD kodu bir 3B şekil üretmedi.\n" + ext.tail(log, 1500))
            shape = Part.makeCompound(shapes) if len(shapes) > 1 else shapes[0]
        else:
            stl = os.path.join(work, "model.stl")
            ok, log = _openscad_run(exe, scad, stl, params, cwd, ["--backend=manifold"])
            if not ok and "backend" in log:  # OpenSCAD 2021 has no manifold backend
                ok, log = _openscad_run(exe, scad, stl, params, cwd)
            if not ok:
                raise ToolError("OpenSCAD hata verdi:\n" + ext.tail(log, 3000))
            mesh = Mesh.Mesh(stl)
            shell = Part.Shape()
            shell.makeShapeFromMesh(mesh.Topology, 0.01)
            shape = Part.Solid(Part.Shell(shell.Faces))
        shape = _solidify(shape.copy())
        echo = [ln[5:].strip() for ln in log.splitlines() if ln.startswith("ECHO:")]
        warnings = [ln.strip() for ln in log.splitlines() if ln.startswith(("WARNING", "DEPRECATED"))]
        return shape, {"echo": echo[-30:], "warnings": warnings[-20:]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------- build123d / CadQuery ----------------

RUNNER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "codecad_runner.py")


def _python_shape(language, code, params, cwd):
    import Part

    ext = _ext()
    py = ext.find("codecad_python", required=True)
    work = tempfile.mkdtemp(prefix="cadai_code_")
    try:
        job = {"language": language, "code_file": os.path.join(work, "model.py"), "params": params or {},
               "step_file": os.path.join(work, "model.step"), "result_file": os.path.join(work, "result.json")}
        with open(job["code_file"], "w", encoding="utf-8") as f:
            f.write(code)
        job_file = os.path.join(work, "job.json")
        with open(job_file, "w", encoding="utf-8") as f:
            json.dump(job, f)
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=cwd or "")
        p = ext.run([py, RUNNER, job_file], timeout=CODE_TIMEOUT, cwd=cwd or work, env=env)
        if not os.path.isfile(job["result_file"]):
            raise ToolError(f"{language} betiği çalışmadı:\n" + ext.tail(p.stdout + "\n" + p.stderr, 3000))
        with open(job["result_file"], encoding="utf-8") as f:
            res = json.load(f)
        if not res.get("ok"):
            if "No module named" in res.get("error", ""):
                res["error"] += f"\n{language} bu Python'da kurulu değil: {py}"
            raise ToolError(f"{language} kodu hata verdi, {res.get('error')}\nÇıktı:\n{res.get('stdout', '')}")
        shape = Part.Shape()
        shape.read(job["step_file"])
        if shape.isNull():
            raise ToolError("Üretilen STEP okunamadı.")
        info = {k: res[k] for k in ("result_from", "params_applied", "params_injected") if res.get(k)}
        if res.get("stdout"):
            info["stdout"] = res["stdout"]
        return shape, info
    finally:
        shutil.rmtree(work, ignore_errors=True)


def code_cad(language=None, code=None, file=None, params=None, name=None, replace=None, mode="brep"):
    from .model_tools import transaction

    doc = active_doc(create=True)
    old = get_object(replace, doc) if replace else None
    if old is not None and not code and not file:  # regenerate from the stored source with new parameters
        if "CadAISource" not in old.PropertiesList:
            raise ToolError(f"{old.Name} kodla üretilmemiş; code ya da file ver.")
        language = language or old.CadAIEngine
        stored = json.loads(old.CadAIParams or "{}")
        params = dict(stored, **(params or {}))
        if old.CadAISourceFile and os.path.isfile(old.CadAISourceFile):
            file = old.CadAISourceFile
        else:
            code = old.CadAISource
    if file:
        if language is None:
            if not file.lower().endswith(".scad"):
                raise ToolError("Python dosyası için language: 'build123d' ya da 'cadquery'.")
            language = "openscad"
        file, code = _read_file(file, (".scad",) if language == "openscad" else (".py",))
    if language not in LANGUAGES:
        raise ToolError(f"language: {', '.join(LANGUAGES)}.")
    if not code or not code.strip():
        raise ToolError("code ya da file gerekli.")
    if mode not in ("brep", "mesh"):
        raise ToolError("mode: 'brep' ya da 'mesh'.")
    cwd = os.path.dirname(file) if file else (os.path.dirname(doc.FileName) if doc.FileName else None)
    label = name or (os.path.splitext(os.path.basename(file))[0] if file else None)
    try:
        with transaction(doc, f"CadAI: {language} → {label or replace or 'parça'}"):
            if language == "openscad":
                shape, info = _openscad_shape(code, params, cwd, mode, doc)
            else:
                shape, info = _python_shape(language, code, params, cwd)
            if not shape.Faces:
                raise ToolError("Üretilen şekilde yüzey yok (yalnızca 2B mi?). 3B bir katı üret.")
            obj = _store(doc, shape, name or label or language, replace, language, code, params, file, label)
            doc.recompute()
    except _ext().ExternalError as e:
        raise ToolError(str(e))
    out = {"ok": True, "language": language, **_shape_report(obj), **{k: v for k, v in info.items() if v}}
    if obj.Shape.Solids and not obj.Shape.isValid():
        out["warning"] = "Şekil geçersiz (çoğunlukla mesh'e düşen işlemlerden). FEM/teknik resimden önce düzelt."
    if not obj.Shape.Solids:
        out["warning"] = "Sonuç kapalı bir katı değil (yüzey/kabuk)."
    if language == "openscad" and mode == "brep" and obj.Shape.Solids:
        planar = sum(1 for f in obj.Shape.Faces if f.Surface.__class__.__name__ == "Plane")
        if planar == len(obj.Shape.Faces) and len(obj.Shape.Faces) > 200:
            out["note"] = ("Yüzlerin hepsi düzlem ve çok sayıda: bir kısmı mesh'e düşmüş olabilir (minkowski, hull, "
                           "text). Delik/yuvarlak ölçüleri için mümkünse bu işlemlerden kaçın.")
    return out


def code_cad_source(object):
    obj = get_object(object)
    if "CadAISource" not in obj.PropertiesList:
        raise ToolError(f"{obj.Name} kodla üretilmiş bir parça değil.")
    return {"object": obj.Name, "language": obj.CadAIEngine, "params": json.loads(obj.CadAIParams or "{}"),
            "file": obj.CadAISourceFile or None, "code": obj.CadAISource}


# ---------------- KiCad ----------------

def _board_solid(solids):
    """The board body: a thin plate at z 0, with the largest footprint."""
    flat = [s for s in solids if s.BoundBox.ZLength < 6 and abs(s.BoundBox.ZMin) < 0.2]
    pool = flat or solids
    return max(pool, key=lambda s: s.BoundBox.XLength * s.BoundBox.YLength)


def kicad_board(file, name=None, components=True, include_tracks=False, position=None):
    import FreeCAD
    import Part

    from .model_tools import transaction

    ext = _ext()
    file, _ = _read_file(file, (".kicad_pcb",))
    cli = ext.find("kicad_cli", required=True)
    work = tempfile.mkdtemp(prefix="cadai_kicad_")
    try:
        step = os.path.join(work, "board.step")
        args = [cli, "pcb", "export", "step", "--force", "--subst-models", "--user-origin", "0x0mm", "-o", step]
        if not components:
            args.append("--board-only")
        if include_tracks:
            args += ["--include-tracks", "--include-pads"]
        p = ext.run(args + [file], timeout=CODE_TIMEOUT)
        if p.returncode != 0 or not os.path.isfile(step):
            raise ToolError("kicad-cli STEP üretemedi:\n" + ext.tail(p.stdout + "\n" + p.stderr))
        missing_models = sorted({m for m in re.findall(r"[^\s'\"]+\.(?:wrl|step|stp)", p.stdout + p.stderr)
                                 if "Cannot" in p.stdout + p.stderr})[:20]
        pos_csv = os.path.join(work, "pos.csv")
        q = ext.run([cli, "pcb", "export", "pos", "--format", "csv", "--units", "mm", "-o", pos_csv, file],
                    timeout=120)
        rows = []
        if q.returncode == 0 and os.path.isfile(pos_csv):
            with open(pos_csv, encoding="utf-8", errors="replace") as f:
                rows = list(csv.DictReader(f))
        shape = Part.Shape()
        shape.read(step)
        if shape.isNull() or not shape.Solids:
            raise ToolError("KiCad STEP'i okunamadı ya da katı içermiyor (kartın Edge.Cuts dış hattı kapalı mı?).")
    except ext.ExternalError as e:
        raise ToolError(str(e))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    board = _board_solid(shape.Solids)
    bb = board.BoundBox
    # board centre at the origin (KiCad page coordinates are far from it), bottom face at z = 0, then `position`
    shift = FreeCAD.Vector(-bb.Center.x, -bb.Center.y, -bb.ZMin) + FreeCAD.Vector(*(position or [0, 0, 0]))
    others = [s for s in shape.Solids if not s.isSame(board)]
    doc = active_doc(create=True)
    stem = _safe_name(name or os.path.splitext(os.path.basename(file))[0], "PCB")
    with transaction(doc, f"CadAI: KiCad {stem}"):
        b = doc.addObject("Part::Feature", stem + "_Kart")
        b.Shape = board.translated(shift)
        b.Label = stem + " kart"
        objs = [b]
        if others:
            c = doc.addObject("Part::Feature", stem + "_Bilesenler")
            c.Shape = Part.makeCompound(others).translated(shift)
            c.Label = stem + " bileşenler"
            objs.append(c)
        for o in objs:
            o.addProperty("App::PropertyString", "KiCadFile", "CadAI", "Source KiCad board")
            o.KiCadFile = file
        if getattr(b, "ViewObject", None) is not None:
            b.ViewObject.ShapeColor = (0.05, 0.32, 0.14)
            if len(objs) > 1:
                objs[1].ViewObject.ShapeColor = (0.18, 0.18, 0.2)
        doc.recompute()

    def comp(row):
        x, y = float(row["PosX"]) + shift.x, float(row["PosY"]) + shift.y
        return {"ref": row["Ref"], "value": row["Val"], "footprint": row["Package"], "x": r(x, 2), "y": r(y, 2),
                "rot_deg": r(float(row["Rot"]), 1), "side": row["Side"]}

    comps = [comp(row) for row in rows]
    holes = [c for c in comps if re.search(r"mount|hole", c["footprint"], re.I) or re.match(r"H\d+$", c["ref"])]
    connectors = [c for c in comps if re.match(r"(J|P|CN|USB|X)\d+", c["ref"]) or
                  re.search(r"usb|conn|header|jack|terminal", c["footprint"], re.I)]
    out = {"ok": True, "file": file, "board": {"object": b.Name, "size_mm": [r(bb.XLength, 2), r(bb.YLength, 2)],
                                               "thickness_mm": r(bb.ZLength, 3), "bbox": bbox(b.Shape)},
           "components_object": objs[1].Name if len(objs) > 1 else None, "solids": len(shape.Solids),
           "overall_bbox": bbox(Part.makeCompound([o.Shape for o in objs])),
           "mounting_holes": holes, "connectors": connectors[:40], "component_count": len(comps),
           "components": comps[:60],
           "coordinates": "Kart merkezi XY=0, kartın alt yüzü z=0 (position verildiyse ona göre kaydırıldı). "
                          "Bileşen x/y değerleri modelle aynı koordinatlarda; kutu tasarımında delik ve konnektör "
                          "boşlukları için bunları kullan."}
    if len(comps) > 60:
        out["components_note"] = f"{len(comps)} bileşenin ilk 60'ı listelendi."
    if missing_models:
        out["missing_3d_models"] = missing_models
    return out


TOOLS = [
    Tool("external_tools",
         "Which other open-source programs CadAI can drive are installed: Blender (render), OpenSCAD, KiCad "
         "(kicad-cli), Python with build123d / CadQuery. Gives versions, or the install command for missing ones.",
         {"type": "object", "properties": {}},
         external_tools, title="External programs (Blender, OpenSCAD, KiCad, build123d)"),
    Tool("code_cad",
         "Make a part from code in another open-source CAD language and put it into the open FreeCAD document as "
         "a solid: OpenSCAD (.scad; mode 'brep' converts the CSG tree to exact FreeCAD solids — cylinders stay "
         "cylinders — 'mesh' goes through STL), build123d or CadQuery (Python, run in a separate Python, exact "
         "B-rep via STEP). Give code or a file. params override top-level variables (width = 40 → params "
         "{\"width\": 60}). For Python, assign the final shape to `result` (or call show_object). The source is "
         "stored in the object: replace=<object> with only params regenerates it with new values (keeps its "
         "placement); code_cad_source shows the code. Then verify with measure as usual.",
         {"type": "object", "properties": {
             "language": {"type": "string", "enum": list(LANGUAGES),
                          "description": "Not needed for a .scad file or when replacing."},
             "code": {"type": "string"},
             "file": {"type": "string", "description": "Path to a .scad or .py file (its folder is the working "
                                                       "directory, so include/use/import work)."},
             "params": {"type": "object", "description": "Top-level variable overrides (numbers, text, lists)."},
             "name": {"type": "string", "description": "Name/label of the new object."},
             "replace": {"type": "string", "description": "Object made earlier by code_cad to regenerate."},
             "mode": {"type": "string", "enum": ["brep", "mesh"], "description": "OpenSCAD only; default brep."}}},
         code_cad, mutates=True, destructive=False, title="Code CAD (OpenSCAD, build123d, CadQuery)"),
    Tool("code_cad_source",
         "Show the source code, language and parameters of a part made by code_cad.",
         {"type": "object", "properties": {"object": {"type": "string"}}, "required": ["object"]},
         code_cad_source, title="Source of a code-made part"),
    Tool("kicad_board",
         "Import a KiCad PCB (.kicad_pcb) as 3D: the board and its component models (kicad-cli STEP export), "
         "board centre at XY 0 and bottom at z 0. Returns board size and thickness, mounting holes, connectors "
         "and component positions in the same coordinates — use them to design an enclosure around the board "
         "(standoffs, connector cut-outs).",
         {"type": "object", "properties": {
             "file": {"type": "string", "description": "Path to the .kicad_pcb file."},
             "name": {"type": "string"},
             "components": {"type": "boolean", "description": "Include component 3D models (default true)."},
             "include_tracks": {"type": "boolean", "description": "Also copper tracks and pads (slower)."},
             "position": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3,
                          "description": "Where the board's bottom centre goes (mm)."}},
          "required": ["file"]},
         kicad_board, mutates=True, destructive=False, title="Import KiCad board"),
]
