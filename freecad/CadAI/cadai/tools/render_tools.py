"""Render tool: realistic pictures of the model (Blender Cycles) or a quick built-in render (cadai.render)."""

import base64
import json
import os
import re
import shutil
import threading
import time

from . import Tool, ToolError, ToolResult
from .geometry import active_doc, get_object

_JOBS = {}


def _objects(doc, names):
    from ..ui_actions import _display_objects

    if names:
        return [get_object(n, doc) for n in names]
    objs = [o for o in _display_objects(doc) if o.TypeId != "Sketcher::SketchObject"]
    objs = [o for o in objs if getattr(o, "Shape", None) is not None and o.Shape.Faces]
    if not objs:
        raise ToolError("Görünür, yüzeyi olan bir parça yok.")
    return objs


def _material_name(obj):
    mat = getattr(obj, "ShapeMaterial", None)
    name = getattr(mat, "Name", "") or ""
    return "" if name.lower() == "default" else name


def _out_path(doc, objs, ext):
    from .. import config

    if doc.FileName:
        folder = os.path.dirname(doc.FileName)
        stem = os.path.splitext(os.path.basename(doc.FileName))[0]
    else:
        folder = os.path.join(os.path.dirname(config.config_path()), "renders")
        stem = doc.Name
    part = f"_{objs[0].Name}" if len(objs) == 1 else ""
    return os.path.join(folder, re.sub(r'[\\/:*?"<>|]+', "_", f"{stem}{part}_render") + ext)


def _finish_blender(job):
    """Composite the Blender frames on the backdrop and write PNG / GIF (runs on any thread, no FreeCAD)."""
    from .. import render

    res = job["blender"]
    files = res["files"]
    still = render.composite(render.load_png(files[0]), job["background"])
    render.save_png(still, job["png"])
    out = {"ok": True, "engine": "blender", "device": res["device"], "samples": res["samples"],
           "blender": res.get("blender"), "png": job["png"], "seconds": res["seconds"]}
    if job["frames"]:
        render.gif_from_frames(files, job["background"], job["gif"])
        out["gif"] = job["gif"]
        out["frames"] = len(files)
    shutil.rmtree(job["work_dir"], ignore_errors=True)
    return out


def _result(out, materials, objs):
    from .. import render

    out = dict(out, objects=[o.Name for o in objs], materials=materials)
    out["note"] = ("Resme bak: malzeme ya da açı uygun değilse materials / view / azimuth_deg / elevation_deg ile "
                   "yeniden çağır. Taslak yerine son kalite için quality='final'.")
    return ToolResult(json.dumps(out, ensure_ascii=False, indent=1),
                      image_png_b64=base64.b64encode(render.preview_png(out["png"])).decode("ascii"))


def render_model(objects=None, view="iso", azimuth_deg=None, elevation_deg=None, engine="auto", quality="draft",
                 width=1600, height=1200, materials=None, default_material=None, background="studio",
                 turntable_frames=0, path=None, background_job=False):
    from .. import external, render

    if engine not in ("auto", "blender", "quick"):
        raise ToolError("engine: 'auto', 'blender' ya da 'quick'.")
    if quality not in render.QUALITY:
        raise ToolError("quality: 'draft' ya da 'final'.")
    width, height = int(width), int(height)
    if not (64 <= width <= 7680 and 64 <= height <= 7680):
        raise ToolError("width/height 64–7680 piksel olmalı.")
    frames = int(turntable_frames or 0)
    if frames and not 8 <= frames <= 240:
        raise ToolError("turntable_frames 8–240 arası olmalı (0 = tek resim).")
    try:
        if background not in ("studio", "white", "transparent"):
            render.parse_color(background)
        direction = render.view_direction(view, azimuth_deg, elevation_deg)
    except render.RenderError as e:
        raise ToolError(str(e))
    blender = external.find("blender") if engine in ("auto", "blender") else None
    if engine == "blender" and not blender:
        raise ToolError(external.missing_message("blender") + " Blender'sız hızlı render için engine='quick'.")
    if frames and not blender:
        raise ToolError("Dönen animasyon (turntable) Blender ister. " + external.missing_message("blender"))

    from ..ui_actions import _color

    doc = active_doc()
    objs = _objects(doc, objects)
    materials = materials or {}
    unknown = [k for k in materials if k not in {o.Name for o in objs} | {o.Label for o in objs}]
    if unknown:
        raise ToolError(f"materials içinde render edilmeyen nesne: {', '.join(unknown)}")
    tol_rel = render.QUALITY[quality]["tol"] if blender else 1 / 900
    diag = max(o.Shape.BoundBox.DiagonalLength for o in objs)
    meshes, used = [], {}
    for obj in objs:
        spec = materials.get(obj.Name, materials.get(obj.Label, default_material))
        try:
            mat = render.resolve_material(spec, tuple(_color(obj)) if obj.ViewObject else None,
                                          _material_name(obj), obj.Label)
        except render.RenderError as e:
            raise ToolError(f"{obj.Name}: {e}")
        v, t = render.tessellate(obj.Shape, max(diag * tol_rel, 0.005))
        if len(t):
            meshes.append((v, t, mat, obj.Name))
            used[obj.Name] = mat["preset"] if mat["preset"] not in ("plastic", "glossy_plastic", "anodized") else \
                f"{mat['preset']} #{''.join(f'{round(c * 255):02x}' for c in mat['color'])}"
    if not meshes:
        raise ToolError("Üçgenlenebilen yüzey yok.")

    png = os.path.abspath(os.path.expanduser(path)) if path else _out_path(doc, objs, ".png")
    if not png.lower().endswith(".png"):
        png += ".png"
    os.makedirs(os.path.dirname(png), exist_ok=True)

    if not blender:  # quick engine, right here
        t0 = time.time()
        try:
            img = render.quick_render([(v, t, m) for v, t, m, _ in meshes], direction, width, height)
            render.save_png(render.composite(img, background), png)
        except render.RenderError as e:
            raise ToolError(str(e))
        except PermissionError:
            raise ToolError(f"Dosya yazılamadı (başka programda açık olabilir): {png}")
        out = {"ok": True, "engine": "quick", "png": png, "seconds": round(time.time() - t0, 1)}
        if engine == "auto":
            out["hint"] = ("Blender bulunamadığı için yerleşik hızlı render kullanıldı. Gerçekçi render (Cycles, "
                           "yansıma, yumuşak gölge, dönen animasyon) için: " + external.missing_message("blender"))
        return _result(out, used, objs)

    wd = render.work_dir()
    job_path = render.blender_job(meshes, direction, width, height, quality, frames, wd)
    job = {"work_dir": wd, "background": background, "png": png, "frames": frames,
           "gif": png[:-4] + "_turntable.gif", "started": time.time(), "done": False}
    timeout = 3600 if frames else 900

    def work():
        try:
            job["blender"] = render.run_blender(blender, job_path, timeout)
            job["out"] = _finish_blender(job)
        except (render.RenderError, external.ExternalError) as e:
            job["error"] = str(e)
        except Exception as e:  # report, never kill the worker silently
            job["error"] = f"{type(e).__name__}: {e}"
        job["done"] = True

    if not background_job:
        work()
        if "error" in job:
            raise ToolError(job["error"])
        return _result(job["out"], used, objs)
    job_id = f"render{len(_JOBS) + 1}"
    job["objs"], job["used"] = objs, used
    _JOBS[job_id] = job
    threading.Thread(target=work, name=f"cadai-{job_id}", daemon=True).start()
    return {"ok": True, "job": job_id, "status": "running", "engine": "blender",
            "next": f"render_status(job='{job_id}') ile birkaç saniyede bir sonucu sor."}


def render_status(job):
    j = _JOBS.get(job)
    if j is None:
        raise ToolError(f"Böyle bir render işi yok: {job}. Bilinen işler: {list(_JOBS)}")
    if not j["done"]:
        return {"job": job, "status": "running", "elapsed_s": round(time.time() - j["started"], 1)}
    if "error" in j:
        raise ToolError(j["error"])
    return _result(dict(j["out"], job=job, status="finished"), j["used"], j["objs"])


_MATERIAL = {"oneOf": [
    {"type": "string", "description": "Preset name."},
    {"type": "object", "properties": {
        "preset": {"type": "string"},
        "color": {"type": "string", "description": "'#rrggbb' (plastic, anodized; tints metals too)."},
        "roughness": {"type": "number", "minimum": 0, "maximum": 1},
        "metallic": {"type": "number", "minimum": 0, "maximum": 1}}}]}

PRESET_NAMES = ("aluminium, anodized, steel, stainless, chrome, brass, copper, gold, cast_iron, black_oxide, plastic, "
                "glossy_plastic, rubber, glass, pcb, clay, wood")

TOOLS = [
    Tool("render",
         "Render a realistic picture of the model (PNG next to the .FCStd, or the CadAI folder) and return it. "
         "engine 'blender' = Blender Cycles in a separate process on the GPU: studio HDRI light, reflections, soft "
         "shadow on the floor, slight edge bevel; 'quick' = built-in renderer, a few seconds, no extra program; "
         "'auto' (default) uses Blender when it is installed. Materials are automatic (FreeCAD material name or "
         "label: 'Alüminyum', 'S235', 'PLA'... else the object's colour as plastic, default grey = aluminium); set "
         f"them per object with materials. Presets: {PRESET_NAMES}. turntable_frames > 0 also writes an orbiting "
         "animation (GIF, Blender only; use background_job=true and poll render_status). Look at the returned "
         "picture before reporting.",
         {"type": "object", "properties": {
             "objects": {"type": "array", "items": {"type": "string"},
                         "description": "Default: every visible part."},
             "view": {"type": "string", "enum": ["iso", "hero", "front", "back", "left", "right", "top", "bottom",
                                                 "iso_back"],
                      "description": "Camera direction; 'hero' = lower, product-shot angle. Default iso."},
             "azimuth_deg": {"type": "number", "description": "Instead of view: 0 = front, 90 = right."},
             "elevation_deg": {"type": "number", "description": "With azimuth_deg; default 30."},
             "engine": {"type": "string", "enum": ["auto", "blender", "quick"]},
             "quality": {"type": "string", "enum": ["draft", "final"],
                         "description": "draft: fast preview; final: more samples, finer mesh."},
             "width": {"type": "integer", "minimum": 64, "maximum": 7680},
             "height": {"type": "integer", "minimum": 64, "maximum": 7680},
             "materials": {"type": "object", "additionalProperties": _MATERIAL,
                           "description": "Object name/label -> preset or {preset, color, roughness, metallic}, "
                                          "e.g. {\"Plate\": \"anodized\", \"Cover\": {\"preset\": \"plastic\", "
                                          "\"color\": \"#ff6600\"}}."},
             "default_material": _MATERIAL,
             "background": {"type": "string",
                            "description": "'studio' (soft grey, default), 'white', 'transparent' or '#rrggbb'."},
             "turntable_frames": {"type": "integer", "minimum": 0, "maximum": 240,
                                  "description": "0 = still picture; e.g. 48 for an orbiting GIF."},
             "path": {"type": "string", "description": "PNG path (the GIF goes next to it)."},
             "background_job": {"type": "boolean",
                                "description": "Blender only: return a job id at once, poll render_status "
                                               "(long renders, turntables)."}}},
         render_model, mutates=True, destructive=False, idempotent=True, title="Render (Blender / quick)"),
    Tool("render_status",
         "Check a render started with render(background_job=true). Returns 'running' or the result with the picture.",
         {"type": "object", "properties": {"job": {"type": "string"}}, "required": ["job"]},
         render_status, title="Render job status"),
]
