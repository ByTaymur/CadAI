"""Persistent, declarative design checks. Stored data never executes code or edits geometry."""

import json
import math
import os
import tempfile
from datetime import datetime, timezone

import FreeCAD

from . import Tool, ToolError
from .geometry import RESULT_TYPES, active_doc, get_object, get_shape
from .model_tools import transaction

STORE = "CadAI_DesignRequirements"
MAX_REQUIREMENTS = 20
UNITS = {"bbox_x": "mm", "bbox_y": "mm", "bbox_z": "mm", "volume": "mm3",
         "solid_count": "count", "min_distance": "mm", "interference_volume": "mm3",
         "hole_count": "count", "hole_diameter": "mm", "hole_edge_offset": "mm"}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ToolError("Şart değerleri sonlu sayılar olmalı.")
    return value


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise ToolError("Şart kimliği ve nesne adları 1–64 karakter olmalı.")
    return value


def _descriptor(data, doc=None):
    if not isinstance(data, dict) or set(data) - {"object", "metric", "other", "axis", "edge_axis"}:
        raise ToolError("Ölçüm: object, metric, other, axis veya edge_axis kullan.")
    metric = data.get("metric")
    if metric not in UNITS:
        raise ToolError("Desteklenmeyen ölçüm: " + ", ".join(UNITS))
    out = {"object": _text(data.get("object")), "metric": metric}
    if metric in ("min_distance", "interference_volume"):
        out["other"] = _text(data.get("other"))
    elif "other" in data:
        raise ToolError("other yalnızca mesafe veya çakışma hacmi için kullanılabilir.")
    if metric.startswith("hole_"):
        out["axis"] = data.get("axis", "z")
        if out["axis"] not in ("x", "y", "z"):
            raise ToolError("Delik axis: x, y veya z olmalı.")
        if metric == "hole_edge_offset":
            out["edge_axis"] = data.get("edge_axis")
            if out["edge_axis"] not in ("x", "y", "z") or out["edge_axis"] == out["axis"]:
                raise ToolError("edge_axis delik ekseninden farklı x/y/z olmalı.")
        elif "edge_axis" in data:
            raise ToolError("edge_axis yalnızca hole_edge_offset için kullanılabilir.")
    elif "axis" in data or "edge_axis" in data:
        raise ToolError("axis/edge_axis yalnızca delik ölçümlerinde kullanılabilir.")
    if doc is not None:
        for key in ("object", "other"):
            if key in out:
                out[key] = get_object(out[key], doc).Name
    return out


def _requirement(data, doc=None):
    if not isinstance(data, dict) or set(data) - {
            "id", "measure", "operator", "value", "tolerance", "reference", "factor"}:
        raise ToolError("Geçersiz tasarım şartı alanları.")
    out = {"id": _text(data.get("id")), "measure": _descriptor(data.get("measure"), doc),
           "operator": data.get("operator", "eq"), "value": _number(data.get("value"))}
    if out["operator"] not in ("eq", "min", "max"):
        raise ToolError("operator: eq, min veya max olmalı.")
    out["tolerance"] = _number(data.get("tolerance", 0 if UNITS[out["measure"]["metric"]] == "count" else 0.01))
    if out["tolerance"] < 0:
        raise ToolError("Tolerans negatif olamaz.")
    if "reference" in data:
        out["reference"] = _descriptor(data["reference"], doc)
        if out["reference"]["metric"] in ("hole_diameter", "hole_edge_offset"):
            raise ToolError("Çok değerli delik ölçümü reference olamaz; tek değerli bir ölçüm seç.")
        if UNITS[out["measure"]["metric"]] != UNITS[out["reference"]["metric"]]:
            raise ToolError("İlişkinin iki ölçümü aynı birimde olmalı.")
        out["factor"] = _number(data.get("factor", 1))
    elif "factor" in data:
        raise ToolError("factor için reference gerekli.")
    return out


def _read(doc):
    obj = doc.getObject(STORE)
    if obj is None:
        return []
    raw = getattr(obj, "Definition", "")
    if not isinstance(raw, str) or len(raw) > 32768:
        raise ToolError("Kayıtlı şart verisi boyut sınırını aşıyor.")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("şema sürümü")
        items = data["requirements"]
        if not isinstance(items, list) or len(items) > MAX_REQUIREMENTS:
            raise ValueError("şart sayısı")
        items = [_requirement(item) for item in items]
        if len({r["id"] for r in items}) != len(items):
            raise ValueError("tekrarlanan kimlik")
        return items
    except (ValueError, KeyError, TypeError, ToolError) as e:
        raise ToolError(f"Kayıtlı tasarım şartları okunamadı: {e}")


def _shape(doc, name, cache):
    if name in cache:
        return cache[name]
    obj = doc.getObject(name)  # stored internal names: never silently rebind a deleted object to a label
    if obj is None:
        raise ToolError(f"Nesne artık yok: {name}")
    seen = set()
    while True:
        if obj.Name in seen:
            raise ToolError(f"Döngülü tasarım geçmişi: {name}")
        seen.add(obj.Name)
        if any(s in obj.State for s in ("Invalid", "Error", "Touched")):
            raise ToolError(f"{obj.Name}: geometri yeniden hesaplanmalı veya onarılmalı.")
        successors = []
        for parent in obj.InList:
            if parent.TypeId not in RESULT_TYPES:
                continue
            bases = [getattr(parent, "Base", None)] + list(getattr(parent, "Shapes", None) or [])
            if parent.TypeId in ("Part::Fuse", "Part::Common"):
                bases.append(getattr(parent, "Tool", None))
            if obj in bases:
                successors.append(parent)
        if not successors:
            break
        if len(successors) != 1:
            raise ToolError(f"{name}: birden çok sonuç var; şart için açık bir sonuç nesnesi seç.")
        obj = successors[0]
    shape = get_shape(obj)
    if not shape.isValid() or not shape.BoundBox.isValid():
        raise ToolError(f"Geçersiz geometri: {obj.Name}")
    cache[name] = (obj.Name, shape)
    return cache[name]


def _measure(doc, spec, cache):
    name, shape = _shape(doc, spec["object"], cache)
    metric = spec["metric"]
    evidence = {"object": name, "metric": metric}
    if metric.startswith("bbox_"):
        value = getattr(shape.BoundBox, metric[-1].upper() + "Length")
    elif metric == "solid_count":
        value = len(shape.Solids)
    elif metric == "volume":
        if not shape.Solids:
            raise ToolError(f"{name}: hacim ölçümü için katı gerekli.")
        value = shape.Volume
    elif metric.startswith("hole_"):
        from .hole_geometry import cylindrical_holes

        key = (name, "holes", spec["axis"])
        if key not in cache:
            cache[key] = cylindrical_holes(shape, spec["axis"])
        holes = cache[key]
        evidence["axis"] = spec["axis"]
        evidence["faces"] = [f for h in holes for f in h["faces"]][:32]
        evidence["recognized_holes"] = len(holes)
        if metric == "hole_count":
            value = len(holes)
        else:
            if not holes:
                raise ToolError("Bu eksende kapalı silindirik delik bulunamadı; ölçüm doğrulanamadı.")
            if metric == "hole_diameter":
                value = [d for h in holes for d in h["diameters_mm"]]
            else:
                axis = spec["edge_axis"]
                lo, hi = (getattr(shape.BoundBox, axis.upper() + end) for end in ("Min", "Max"))
                value = [min(h["center"]["xyz".index(axis)] - lo, hi - h["center"]["xyz".index(axis)]) for h in holes]
                evidence["edge_axis"] = axis
            if len(value) > 128:
                raise ToolError("Delik başına doğrulama sınırı (128 ölçüm) aşıldı.")
            return [_number(v) for v in value], evidence
    else:
        other, other_shape = _shape(doc, spec["other"], cache)
        if other == name:
            raise ToolError("Mesafe iki ayrı sonuç nesnesi arasında ölçülmeli.")
        evidence["other"] = other
        if metric == "interference_volume":
            if not shape.Solids or not other_shape.Solids:
                raise ToolError("Çakışma hacmi için iki katı gerekli.")
            common = shape.common(other_shape)
            if not common.isNull() and not common.isValid():
                raise ToolError("Çakışma geometrisi hesaplanamadı.")
            value = 0 if common.isNull() else common.Volume
        else:
            value = shape.distToShape(other_shape)[0]
    return _number(value), evidence


def check_design_requirements(ids=None, include_definitions=False):
    """Read only, including after mutations. Missing/broken/stale geometry is never a pass."""
    doc = FreeCAD.ActiveDocument
    if doc is None:
        return {"status": "not_configured", "count": 0, "checks": []}
    try:
        requirements = _read(doc)
    except ToolError as e:
        return {"status": "error", "error": str(e), "checks": []}
    if ids is not None:
        if not isinstance(ids, list) or not ids or set(ids) - {r["id"] for r in requirements}:
            raise ToolError("ids boş olmamalı ve yalnızca kayıtlı şart kimlikleri içermeli.")
        requirements = [r for r in requirements if r["id"] in ids]
    checks, cache = [], {}
    for req in requirements:
        row = {"id": req["id"], "status": "error", "unit": UNITS[req["measure"]["metric"]],
               "operator": req["operator"], "tolerance": req["tolerance"]}
        try:
            actual, evidence = _measure(doc, req["measure"], cache)
            target = req["value"]
            if "reference" in req:
                ref, ref_evidence = _measure(doc, req["reference"], cache)
                target = _number(req["factor"] * ref + target)
                row["reference"] = dict(ref_evidence, measured=ref, factor=req["factor"], offset=req["value"])
            deltas = [_number(a - target) for a in (actual if isinstance(actual, list) else [actual])]
            passed = all({"eq": abs(d) <= req["tolerance"], "min": d >= -req["tolerance"],
                          "max": d <= req["tolerance"]}[req["operator"]] for d in deltas)
            delta = max(deltas, key=abs)
            row.update(status="pass" if passed else "fail", measured=actual, expected=target,
                       deviation=delta, evidence=evidence)
        except Exception as e:
            row["error"] = str(e)[:240]
        checks.append(row)
    failed = sum(c["status"] != "pass" for c in checks)
    out = {"status": ("fail" if failed else "pass") if checks else "not_configured",
           "document": doc.Name, "count": len(checks), "failed": failed, "partial": ids is not None, "checks": checks}
    if include_definitions:
        out["requirements"] = requirements
    return out


def validation_summary(report):
    """Compact automatic evidence stays valid JSON even for many per-hole measurements."""
    out = {k: v for k, v in report.items() if k not in ("checks", "requirements")}
    failures = [c for c in report.get("checks", []) if c["status"] != "pass"]
    out["checks"] = failures[:3]
    out["details_tool"] = "check_design_requirements"
    if len(failures) > 3:
        out["omitted_failures"] = len(failures) - 3
    return out


def tool_check_design_requirements(ids=None, include_definitions=False):
    """Bound details, never truncate JSON or change the aggregate verdict. UI/export use the full report."""
    out = check_design_requirements(ids, include_definitions)
    out["checks"] = sorted(out.get("checks", []), key=lambda c: c["status"] == "pass")
    omitted = []
    while len(json.dumps(out, ensure_ascii=False, indent=1)) > 8500 and out["checks"]:
        row = out["checks"].pop()
        omitted.append(row["id"])
        if "requirements" in out:
            out["requirements"] = [r for r in out["requirements"] if r["id"] != row["id"]]
        out["omitted_ids"] = omitted
        out["details_truncated"] = True
        out["details_hint"] = "Tüm şartlar denetlendi. Ayrıntı için ids ile küçük bir alt küme iste."
    return out


def inspect_holes(object, axis="z"):
    from .hole_geometry import cylindrical_holes

    if axis not in ("x", "y", "z"):
        raise ToolError("axis: x/y/z olmalı.")
    doc = active_doc()
    name, shape = _shape(doc, get_object(object, doc).Name, {})
    holes = cylindrical_holes(shape, axis)
    return {"object": name, "axis": axis, "count": len(holes), "holes": holes[:64],
            "truncated": len(holes) > 64,
            "scope": "Dünya eksenine paralel tam silindirik iç yüzler; açık kanallar, konik ve bölünmüş yüzler hariç."}


def export_design_report(path):
    doc = active_doc()
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.splitext(path)[1].lower() != ".json":
        raise ToolError("Tasarım raporu için .json uzantısı gerekli.")
    report = check_design_requirements(include_definitions=True)
    report.update(schema_version=1, generated_at=datetime.now(timezone.utc).isoformat(), model_file=doc.FileName,
                  limitations="Kayıtlı şartlar denetlenir; kayıtlı olmayan hedefler ve üretilebilirlik kapsam dışıdır.")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".cadai-report-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return {"ok": True, "path": path, "design_validation": validation_summary(report)}


def set_design_requirements(requirements, remove_ids=None):
    doc = active_doc()
    if not isinstance(requirements, list) or len(requirements) > MAX_REQUIREMENTS:
        raise ToolError(f"En fazla {MAX_REQUIREMENTS} şart verilebilir.")
    incoming = [_requirement(item, doc) for item in requirements]
    ids = [r["id"] for r in incoming]
    remove_ids = [] if remove_ids is None else remove_ids
    if not isinstance(remove_ids, list):
        raise ToolError("remove_ids bir liste olmalı.")
    remove_ids = [_text(i) for i in remove_ids]
    if len(set(ids)) != len(ids) or set(ids) & set(remove_ids):
        raise ToolError("Şart kimlikleri benzersiz olmalı; aynı şart aynı çağrıda eklenip silinemez.")
    existing = {r["id"]: r for r in _read(doc)}
    if set(remove_ids) - set(existing):
        raise ToolError("Silinecek şart kimliği bulunamadı.")
    for key in remove_ids:
        del existing[key]
    existing.update((r["id"], r) for r in incoming)
    if len(existing) > MAX_REQUIREMENTS:
        raise ToolError(f"Belgede en fazla {MAX_REQUIREMENTS} şart saklanabilir.")
    with transaction(doc, "CadAI: tasarım şartları"):
        obj = doc.getObject(STORE)
        if obj is None:
            obj = doc.addObject("App::FeaturePython", STORE)
            obj.Label = "CadAI · Tasarım şartları"
            obj.addProperty("App::PropertyString", "Definition", "CadAI")
        obj.Definition = json.dumps({"version": 1, "requirements": list(existing.values())}, ensure_ascii=False)
        doc.recompute()
    return {"ok": True, "design_validation": check_design_requirements(), "requirements": list(existing.values())}


MEASURE_SCHEMA = {"type": "object", "properties": {
    "object": {"type": "string"}, "metric": {"type": "string", "enum": list(UNITS)},
    "other": {"type": "string", "description": "Second object for min_distance/interference_volume."},
    "axis": {"type": "string", "enum": ["x", "y", "z"], "description": "World hole axis, default z."},
    "edge_axis": {"type": "string", "enum": ["x", "y", "z"],
                  "description": "hole_edge_offset: center to nearest bounding-box side along this axis."}},
    "required": ["object", "metric"], "additionalProperties": False}
REQUIREMENT_SCHEMA = {"type": "object", "properties": {
    "id": {"type": "string", "description": "Stable short ID; reusing it updates just this requirement."},
    "measure": MEASURE_SCHEMA,
    "operator": {"type": "string", "enum": ["eq", "min", "max"]},
    "value": {"type": "number", "description": "Target, or offset when reference is set. mm, mm3 or count."},
    "tolerance": {"type": "number", "minimum": 0, "description": "Absolute tolerance; default 0.01, count 0."},
    "reference": MEASURE_SCHEMA,
    "factor": {"type": "number", "description": "Target = factor * reference measurement + value; default 1."}},
    "required": ["id", "measure", "value"], "additionalProperties": False}

TOOLS = [
    Tool("set_design_requirements",
         "Save user-requested design requirements in the FCStd document. Upsert by id; other requirements stay. "
         "Only update/remove a target when the user changes it, never to hide a failed check. Checks measure actual "
         "geometry: world-axis bounding sizes, solid volume/count, distance, interference_volume, hole count, "
         "all hole diameters and all hole center-to-bounding-box-edge offsets. Holes: closed cylinders along axis; "
         "not slots/cones/split surfaces. Always pair per-hole checks with a hole_count requirement. "
         "Relations compare same-unit measurements: target = factor * reference + value. "
         "Checks detect violations; they do not constrain or automatically repair geometry. No arbitrary code.",
         {"type": "object", "properties": {
             "requirements": {"type": "array", "items": REQUIREMENT_SCHEMA, "maxItems": MAX_REQUIREMENTS},
             "remove_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["requirements"]},
         set_design_requirements, mutates=True, idempotent=True, title="Tasarım şartlarını kaydet",
         example={"requirements": [{"id": "thickness", "measure": {"object": "Plate", "metric": "bbox_z"},
                                    "value": 8}]}),
    Tool("check_design_requirements",
         "Check saved design requirements against current geometry. Returns pass/fail/error, measured and expected "
         "values, tolerance, units and resolved objects. Read-only; never executes stored text. "
         "not_configured means no requirements were checked, NOT a pass. Call before declaring a design complete.",
         {"type": "object", "properties": {
             "ids": {"type": "array", "items": {"type": "string"}, "description": "Optional subset; partial report."},
             "include_definitions": {"type": "boolean", "description": "Include saved rules for editing/review."}}},
         tool_check_design_requirements,
         title="Tasarım şartlarını doğrula"),
    Tool("inspect_holes", "Find closed cylindrical bores along world axis x/y/z (default z). Return centers, "
         "diameters, axial ranges and face names. Partial cylinders, slots and cones are excluded; coaxial adjacent "
         "sections are grouped. Use with hole_count/diameter/edge-offset requirements.",
         {"type": "object", "properties": {"object": {"type": "string"},
                                             "axis": {"type": "string", "enum": ["x", "y", "z"]}},
          "required": ["object"]}, inspect_holes, title="Delikleri incele"),
    Tool("export_design_report", "Export a timestamped JSON report of all saved rules, measurements and failures. "
         "A report may contain failed/error/unconfigured checks; exporting is not certification.",
         {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
         export_design_report, mutates=True, idempotent=False, title="Tasarım denetim raporu"),
]
