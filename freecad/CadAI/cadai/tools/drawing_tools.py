"""Technical drawing (A3 PDF + PNG, Turkish title block) of a part, straight from its geometry (cadai.drawing)."""

import base64
import io
import json
import os

from . import Tool, ToolError, ToolResult
from .geometry import active_doc, get_object, get_shape


def _object_material(obj):
    """(material name, density kg/m³) from FreeCAD's material property of the object, when one is set."""
    mat = getattr(obj, "ShapeMaterial", None)
    name = getattr(mat, "Name", "") or ""
    if not name or name.lower() == "default":
        return "", None
    rho = None
    try:
        import FreeCAD

        rho = FreeCAD.Units.Quantity(mat.PhysicalProperties["Density"]).getValueAs("kg/m^3").Value
    except Exception:
        pass
    return name, rho


def technical_drawing(object, title=None, drawing_no=None, material=None, material_long=None, quantity="1 ADET",
                      revision=1, scale=None, views=None, section="auto", notes=None, dimensions=None, leaders=None,
                      density_kg_m3=None, drawn_by="", path=None):
    from .. import drawing

    if section not in ("auto", "always", "none"):
        raise ToolError("section: 'auto', 'always' ya da 'none'.")
    views = list(views or ["front", "top", "side", "iso"])
    unknown = set(views) - {"front", "top", "side", "iso"}
    if unknown:
        raise ToolError(f"Bilinmeyen görünüş: {', '.join(sorted(unknown))} (front, top, side, iso).")
    if "front" not in views:
        views.insert(0, "front")  # the other views are laid out around the front view
    if scale:
        try:
            drawing.parse_scale(scale)
        except ValueError as e:
            raise ToolError(str(e))
    doc = active_doc()
    obj = get_object(object, doc)
    shape = get_shape(obj)
    if not shape.Solids:
        raise ToolError(f"{obj.Name} katı (solid) değil; teknik resim için kapalı bir katı gerekir.")
    obj_material, obj_rho = _object_material(obj)
    if not material or material == obj_material:  # the object's own material: its density from FreeCAD
        material, density_kg_m3 = obj_material, density_kg_m3 or obj_rho
    if path:
        pdf = os.path.abspath(os.path.expanduser(path))
        if not pdf.lower().endswith(".pdf"):
            pdf += ".pdf"
        png = pdf[:-4] + ".png"
    else:
        pdf, png = drawing.default_paths(doc, obj)
    os.makedirs(os.path.dirname(pdf), exist_ok=True)
    preview = io.BytesIO()
    try:
        report = drawing.make_drawing(
            shape, title=drawing.upper_tr(title or obj.Label), drawing_no=drawing_no or obj.Label, material=material or "",
            material_long=drawing.upper_tr(material_long or material or ""), quantity=quantity, revision=revision, scale=scale,
            views=views, section_mode=section, notes=notes, dimensions=dimensions, leaders=leaders,
            density_kg_m3=density_kg_m3, drawn_by=drawn_by, pdf=pdf, png=png, preview=preview)
    except PermissionError:
        raise ToolError(f"Dosya yazılamadı (başka bir programda açık olabilir): {pdf}")
    report = {"object": obj.Name, "pdf": pdf, "png": png, **report}
    if report["warnings"]:
        report["hint"] = ("Uyarıları ekteki resme bakarak düzelt: ölçünün yeri (side, offset_mm), not okunun yeri "
                          "(side, rise_mm) ya da daha küçük ölçek; sonra aracı yeniden çağır.")
    return ToolResult(json.dumps(report, ensure_ascii=False, indent=1),
                      image_png_b64=base64.b64encode(preview.getvalue()).decode("ascii"))


_POINT = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}

TOOLS = [
    Tool("technical_drawing",
         "Technical drawing of a part on an A3 sheet with the Turkish title block (BAŞLIK, MALZEME, RESİM NO, AĞIRLIK, "
         "ÖLÇEK, REVİZYON), saved as PDF + PNG (200 dpi) next to the .FCStd. Views in first-angle (ISO-E) "
         "arrangement with exact hidden lines: front (ÖN), side (SOL GÖRÜNÜŞ, or KESİT A-A through the middle when "
         "section='auto' and the front view has hidden edges), top (ÜST), shaded isometric. Automatic: largest standard "
         "scale that fits, overall sizes, diameters of round features seen as circles ('4x Ø4,20'), center lines, "
         "mass = volume × density of the material. Returns a report and a small picture of the sheet.\n"
         "Rules: never invent a dimension. Points for added dimensions/leaders are model coordinates (mm) taken from "
         "list_edges / find_faces / measure; the value is measured between them on the drawing. Look at the returned "
         "picture; if 'warnings' is not empty (overlapping texts, text outside the frame or on the isometric picture, "
         "views not fitting) fix it — move a dimension with side/offset_mm, a leader with side/rise_mm, or pick a "
         "smaller scale — and call again.",
         {"type": "object", "properties": {
             "object": {"type": "string", "description": "Object name or label (a solid)."},
             "title": {"type": "string", "description": "BAŞLIK; default: the object's label."},
             "drawing_no": {"type": "string", "description": "RESİM NO; default: the object's label."},
             "material": {"type": "string",
                          "description": "Short material name for the title block, e.g. 'AL 6061', 'S235', 'PLA'. "
                                         "Default: the object's FreeCAD material. Sets the density for the mass."},
             "material_long": {"type": "string", "description": "Full name above the title block, e.g. "
                                                                "'ALÜMİNYUM 6061'."},
             "quantity": {"type": "string", "description": "MİKTAR, default '1 ADET'."},
             "revision": {"type": ["integer", "string"]},
             "scale": {"type": "string", "description": "e.g. '2:1', '1:5'; default: the largest standard scale "
                                                        "that fits."},
             "views": {"type": "array", "items": {"type": "string", "enum": ["front", "top", "side", "iso"]},
                       "description": "Default all four."},
             "section": {"type": "string", "enum": ["auto", "always", "none"],
                         "description": "Side view as section A-A (cut at the middle of X)."},
             "notes": {"type": "array", "items": {"type": "string"},
                       "description": "NOTLAR lines (two standard notes come first; at most 9 in all)."},
             "dimensions": {"type": "array", "description": "Added dimensions between two model points.", "items": {
                 "type": "object", "properties": {
                     "view": {"type": "string", "enum": ["front", "top", "side"]},
                     "from": _POINT, "to": _POINT,
                     "direction": {"type": "string", "enum": ["horizontal", "vertical", "aligned"],
                                   "description": "In the view; default from the points."},
                     "side": {"type": "string", "enum": ["below", "above", "left", "right", "inside"],
                              "description": "Where the dimension line goes; 'inside' = through the points (e.g. a "
                                             "bore's diameter inside a section)."},
                     "offset_mm": {"type": "number", "description": "Extra distance on the sheet (mm)."},
                     "text": {"type": "string", "description": "Only to add a prefix like 'Ø' or a fit ('Ø40 g6'); "
                                                               "the number must be the measured one."},
                     "tolerance": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2,
                                   "description": "Upper and lower deviation, e.g. ['0,00', '-0,01']."}},
                 "required": ["from", "to"]}},
             "leaders": {"type": "array", "description": "Note arrows, e.g. thread or groove callouts.", "items": {
                 "type": "object", "properties": {
                     "view": {"type": "string", "enum": ["front", "top", "side"]},
                     "at": _POINT, "text": {"type": "string"},
                     "side": {"type": "string", "enum": ["right", "left", "inside"]},
                     "rise_mm": {"type": "number", "description": "Height of the shelf above the point (sheet mm)."}},
                 "required": ["at", "text"]}},
             "density_kg_m3": {"type": "number", "description": "When the material name does not give it."},
             "drawn_by": {"type": "string", "description": "ÇİZEN"},
             "path": {"type": "string", "description": "PDF path; the PNG goes next to it."}},
          "required": ["object"]},
         technical_drawing, mutates=True, destructive=False, idempotent=True, title="Technical drawing (A3 PDF)",
         example={"object": "Plate"}),
]
