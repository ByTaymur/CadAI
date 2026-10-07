"""Structured native expressions and a reusable, fully parametric mounting plate."""

import math
import re

from . import Tool, ToolError
from .geometry import active_doc, get_object
from .model_tools import _invalid_objects, transaction

SCALAR_TYPES = {"App::PropertyLength": "mm", "App::PropertyDistance": "mm", "App::PropertyAngle": "deg",
                "App::PropertyFloat": "", "App::PropertyInteger": ""}
PLACEMENT_PATHS = {f"Placement.Base.{a}" for a in "xyz"}


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ToolError("Sonlu bir sayısal değer gerekli.")
    return float(value)


def parameter(obj, path):
    if path in PLACEMENT_PATHS and hasattr(obj, "Placement"):
        return float(getattr(obj.Placement.Base, path[-1])), "mm"
    if not isinstance(path, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", path) or path not in obj.PropertiesList:
        raise ToolError("Geçersiz parametre. Sayısal özellik adı veya Placement.Base.x/y/z kullan.")
    kind = obj.getTypeIdOfProperty(path)
    if kind not in SCALAR_TYPES:
        raise ToolError(f"{obj.Name}.{path}: desteklenen sayısal uzunluk/açı/float/integer özelliği değil.")
    value = getattr(obj, path)
    return finite(float(value.Value if hasattr(value, "Value") else value)), SCALAR_TYPES[kind]


def set_parameter_relation(object, property, reference_object=None, reference_property=None, factor=1, offset=0, remove=False):
    doc = active_doc()
    obj = get_object(object, doc)
    old_value, unit = parameter(obj, property)
    old_expression = dict(obj.ExpressionEngine).get(property)
    expression = None
    if not remove:
        ref = get_object(reference_object, doc)
        _, ref_unit = parameter(ref, reference_property)
        if unit != ref_unit:
            raise ToolError("Bağlanan parametrelerin birimleri aynı olmalı.")
        if obj == ref and property == reference_property:
            raise ToolError("Parametre kendisine bağlanamaz.")
        factor, offset = finite(factor), finite(offset)
        expression = f"{ref.Name}.{reference_property} * {factor:.17g} + ({offset:.17g} {unit})"
    with transaction(doc, f"CadAI: {obj.Name}.{property} bağıntısı"):
        obj.setExpression(property, expression)
        doc.recompute()
        invalid = _invalid_objects(doc)
        if invalid:
            raise ToolError("Bağıntı hesaplanamadı; değişiklik geri alındı: " + ", ".join(i["name"] for i in invalid))
        value, _ = parameter(obj, property)
    return {"ok": True, "object": obj.Name, "property": property, "expression": expression,
            "previous_expression": old_expression, "previous_value": old_value, "value": value, "unit": unit}


def add_mounting_plate(length, width, thickness, hole_diameter, edge_offset_x, edge_offset_y, name="MountingPlate"):
    """Native Box + Cylinder/Cut features; no custom Python proxy required to reopen/recompute."""
    values = [finite(v) for v in (length, width, thickness, hole_diameter, edge_offset_x, edge_offset_y)]
    length, width, thickness, hole_diameter, edge_offset_x, edge_offset_y = values
    if min(values) <= 0:
        raise ToolError("Bütün ölçüler pozitif olmalı.")
    radius = hole_diameter / 2
    if not (radius < edge_offset_x < (length - hole_diameter) / 2
            and radius < edge_offset_y < (width - hole_diameter) / 2):
        raise ToolError("Delikler kenara taşmamalı ve birbirine değmemeli; kenar uzaklıklarını/çapı kontrol et.")
    doc = active_doc(create=True)
    with transaction(doc, "CadAI: parametrik bağlantı plakası"):
        base = doc.addObject("Part::Box", name)
        base.Length, base.Width, base.Height = length, width, thickness
        for prop, value in (("HoleDiameter", hole_diameter), ("EdgeOffsetX", edge_offset_x), ("EdgeOffsetY", edge_offset_y)):
            base.addProperty("App::PropertyLength", prop, "Mounting plate")
            setattr(base, prop, value)
        current = base
        for i, (x, y) in enumerate(((False, False), (True, False), (False, True), (True, True)), 1):
            cutter = doc.addObject("Part::Cylinder", base.Name + f"_Drill{i}")
            cutter.setExpression("Radius", f"{base.Name}.HoleDiameter / 2")
            cutter.setExpression("Height", f"{base.Name}.Height + 2 mm")
            cutter.setExpression("Placement.Base.x", f"{base.Name}.Placement.Base.x + "
                                 + (f"{base.Name}.Length - " if x else "") + f"{base.Name}.EdgeOffsetX")
            cutter.setExpression("Placement.Base.y", f"{base.Name}.Placement.Base.y + "
                                 + (f"{base.Name}.Width - " if y else "") + f"{base.Name}.EdgeOffsetY")
            cutter.setExpression("Placement.Base.z", f"{base.Name}.Placement.Base.z - 1 mm")
            cut = doc.addObject("Part::Cut", base.Name + f"_Holes{i}")
            cut.Base, cut.Tool = current, cutter
            current.Visibility = cutter.Visibility = False
            current = cut
        doc.recompute()
        if _invalid_objects(doc) or current.Shape.isNull() or not current.Shape.isValid() or len(current.Shape.Solids) != 1:
            raise ToolError("Plaka oluşturulamadı; işlem geri alındı.")
        current.Label = base.Label + " · 4 delik"
    return {"ok": True, "object": current.Name, "parameters_object": base.Name,
            "parameters": dict(zip(("Length", "Width", "Height", "HoleDiameter", "EdgeOffsetX", "EdgeOffsetY"), values)),
            "volume_mm3": current.Shape.Volume,
            "note": "Ölçüleri parameters_object üzerinde set_property ile değiştir. Delikler kenar uzaklığını korur. "
                    "Şablon dünya XY düzlemindedir; döndürmek için sonuç nesnesini kullan. Tasarım şartlarını ayrıca kaydet."}


TOOLS = [
    Tool("set_parameter_relation",
         "Preserve a numeric parameter relation using native FreeCAD expressions: target = factor * reference + offset. "
         "Same units only (mm, deg or dimensionless). Supports dimension properties and Placement.Base.x/y/z. "
         "No arbitrary expressions/code. remove=true detaches the relation, retaining the current value. "
         "Use actual source parameters from get_document_summary. Cycles/errors roll back.",
         {"type": "object", "properties": {
             "object": {"type": "string"}, "property": {"type": "string"},
             "reference_object": {"type": "string"}, "reference_property": {"type": "string"},
             "factor": {"type": "number"}, "offset": {"type": "number"}, "remove": {"type": "boolean"}},
          "required": ["object", "property"]}, set_parameter_relation, mutates=True, idempotent=True,
         title="Parametre ilişkisini koru", example={"object": "Lid", "property": "Length",
                                                    "reference_object": "Plate", "reference_property": "Length"}),
    Tool("add_mounting_plate",
         "Create a parametric rectangular XY mounting plate with four through holes. Edge offsets are hole-center "
         "distances in mm. Native FreeCAD expressions keep holes at their offsets when Length/Width/Height or "
         "HoleDiameter/EdgeOffsetX/EdgeOffsetY change on parameters_object. No standard catalog parts.",
         {"type": "object", "properties": {
             **{k: {"type": "number", "exclusiveMinimum": 0} for k in
                ("length", "width", "thickness", "hole_diameter", "edge_offset_x", "edge_offset_y")},
             "name": {"type": "string"}},
          "required": ["length", "width", "thickness", "hole_diameter", "edge_offset_x", "edge_offset_y"]},
         add_mounting_plate, mutates=True, destructive=False, title="Parametrik bağlantı plakası",
         example={"length": 80, "width": 60, "thickness": 8, "hole_diameter": 6,
                  "edge_offset_x": 10, "edge_offset_y": 10}),
]
