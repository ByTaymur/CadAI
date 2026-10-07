"""What small local models (7-14B) see: fewer tools, one-line descriptions, only the arguments they need.

A 9B model loses track with 30 tools and ~7000 tokens of schemas (and Ollama's 4K default context on 8-16 GB GPUs
cuts the prompt). Small mode sends ~15 core tools; the specialist ones (FEM, drawing, render...) are added only when
the user's messages mention their topic. Tools still accept every argument of the full schema (see args.py).
Standard library only: the MCP server imports this without FreeCAD.
"""

import copy
import re
from types import SimpleNamespace

# tool -> (short description, arguments kept besides the required ones; None = all)
SHORT = {
    "set_design_requirements": ("Save user targets by id; preserve other targets. Never change targets to hide failure.", None),
    "check_design_requirements": ("Measure saved design targets; report pass/fail/error. Does not change geometry.", None),
    "inspect_holes": ("Measure closed cylindrical bores: count, centers, diameters, face names. axis defaults to z.", None),
    "set_parameter_relation": ("Keep target parameter = factor * reference parameter + offset using FreeCAD expressions.", None),
    "add_mounting_plate": ("Rectangular plate, four through holes; preserves hole edge offsets when dimensions change.", None),
    "export_design_report": ("Save all design requirements and measured results to a .json report.", None),
    "get_document_summary": ("List all objects with names, sizes and volume. Call first to learn object names.", []),
    "get_selection": ("What the user selected in the 3D view (object, face/edge names). Use when they say 'this', "
                      "'bu', 'burası'.", None),
    "get_markers": ("Read the numbered markers (#1, #2...) the user placed on the model, each with a note saying "
                    "what to change there.", None),
    "find_faces": ("Find faces of an object. Top face: normal=[0,0,1]. Face at the +X end: extreme='max_x'. Holes "
                   "of radius 4: surface_type='Cylinder', radius_mm=4. Returns names and center points.",
                   ["normal", "extreme", "surface_type", "radius_mm"]),
    "list_edges": ("List edges of an object (names, length, type), optionally only those of some faces.",
                   ["faces", "curve_type"]),
    "measure": ("Measure objects: size (bbox), volume, mass, validity. Use after every change.",
                ["objects", "density_kg_m3"]),
    "set_property": ("Change one property of an existing object, e.g. Length of a box or Pad to 25.", None),
    "add_box": ("Create a box/plate: length (X), width (Y), height (Z) in mm; position = min corner.", None),
    "add_cylinder": ("Create a cylinder: diameter, height in mm; position = bottom center; direction = axis.",
                     ["diameter", "position", "direction", "name"]),
    "make_hole": ("Drill a hole: position = point on the face (a 'center' from find_faces). Through the part "
                  "unless depth is given.", ["diameter", "depth", "direction"]),
    "fillet_edges": ("Round edges: edges = ['Edge1', ...] from list_edges, or 'all' / 'top' / 'bottom' / "
                     "'vertical' / 'circular'.", None),
    "chamfer_edges": ("Bevel edges by size mm: edges = ['Edge1', ...] or 'all' / 'top' / 'bottom' / 'vertical' / "
                      "'circular'.", None),
    "boolean": ("Combine two objects: cut (remove tool from base), fuse (join) or common (overlap).", None),
    "move_object": ("Move (position or offset [x,y,z]) or rotate (rotation_deg about rotation_axis) an object.",
                    None),
    "run_python": ("Last resort when no other tool fits: run FreeCAD Python (variables doc, App, Part, Vector). Get "
                   "working code from freecad_recipes first.", ["description"]),
    "freecad_recipes": ("Tested FreeCAD Python code to start run_python from. Call without topic to list them.", None),
    "capture_view": ("Screenshot of the 3D view (only useful if you can see images).", None),
    "search_parts": ("Search ready standard parts (screws, nuts, washers, bearings, profiles, motors), e.g. "
                     "query='M8x20 socket head'.", ["standard", "limit"]),
    "insert_part": ("Insert a part found by search_parts at position [x,y,z].", ["position", "rotation_axis",
                                                                                "rotation_deg", "name"]),
    "export_model": ("Export objects to a file: .step, .stl, .3mf, .glb...", None),
    "technical_drawing": ("Make an A3 technical drawing (PDF + PNG) of one part with views and main dimensions.",
                          ["scale", "material", "title"]),
    "dfm_check": ("Manufacturability check of a part for fdm (3D printing), cnc, injection_molding or sheet_metal.",
                  []),
    "fem_setup": ("Set up a stress analysis: fixed faces, forces [{faces, force_n, direction}], material. Face names "
                  "from find_faces.", ["forces", "material", "analysis_type"]),
    "fem_run": ("Run the analysis made by fem_setup; returns max stress, displacement, force balance.", []),
    "fem_status": ("Status of a background FEM job.", None),
    "beam_hand_calc": ("Hand calculation for a beam to compare with FEM.", ["force_n", "width_mm", "height_mm"]),
    "render": ("Realistic picture of the model (PNG).", ["objects", "view", "materials"]),
    "render_status": ("Status of a background render job.", None),
    "design_history": ("Earlier saved versions of the model (if history is on).", None),
    "open_design_version": ("Open an earlier version as a new document.", None),
    "code_cad": ("Make a solid from OpenSCAD / build123d / CadQuery code.", ["language", "code", "file", "name",
                                                                             "params", "replace"]),
    "code_cad_source": ("Show the code a code_cad object was made from.", None),
    "kicad_board": ("Import a KiCad PCB with components; gives board size, holes, connectors.", ["position"]),
    "external_tools": ("Which external programs (Blender, OpenSCAD, KiCad...) are installed.", None),
}

CORE = ["get_document_summary", "get_selection", "get_markers", "find_faces", "list_edges", "measure", "set_property",
        "add_box", "add_cylinder", "make_hole", "fillet_edges", "chamfer_edges", "boolean", "move_object",
        "run_python", "check_design_requirements"]

# group -> (regex fragments matched at the start of a word in the user's messages, tools); prefixes catch Turkish suffixes.
GROUPS = {
    "requirements": (("şart", "sart", "gereksinim", "koru", "aynı kal", "doğrula", "kontrol", "requirement",
                      "constraint", "preserve", "validate", "clearance", "boşluk", "tolerans"),
                     ["set_design_requirements", "check_design_requirements", "set_parameter_relation"]),
    "holes": (("delik", "hole", "bore"), ["inspect_holes"]),
    "plate": (("plaka", "plate"), ["add_mounting_plate"]),
    "report": (("rapor", "report"), ["export_design_report"]),
    "recipes": (("python", "kod", "code", "script", "tarif", "recipe", "pocket", "cep", "profil", "revolve",
                 "shaft", "eskiz", "sketch", "partdesign", "pad"), ["freecad_recipes"]),
    "view": (("ekran görüntüsü", "screenshot", "görüntü al", "resmini çek", "nasıl görünüyor"),
             ["capture_view"]),
    "parts": (("cıvata", "civata", "vida", "somun", "pul", "rulman", "yatak", "sigma", "motor", "nema", "bolt",
               "screw", "nut", "washer", "bearing", "standart parça", "katalog", "step.parts", "din", "iso"),
              ["search_parts", "insert_part"]),
    "export": (("dışa aktar", "disa aktar", "export", "step", "stp", "stl", "3mf", "glb", "gltf", "iges", "dosyaya",
                "kaydet"), ["export_model"]),
    "drawing": (("teknik res", "teknik çiz", "drawing", "pdf", "antet", "görünüş", "kesit", "ölçülendir"),
                ["technical_drawing"]),
    "dfm": (("üretilebilir", "uretilebilir", "imal", "dfm", "3b baskı", "3d baskı", "baskı", "yazıcı", "3d print",
             "print", "fdm", "cnc", "freze", "enjeksiyon", "kalıp", "injection", "sac", "sheet"), ["dfm_check"]),
    "fem": (("fem", "analiz", "gerilme", "gerilim", "stress", "mukavemet", "dayan", "yük(?!s)", "kuvvet", "newton",
             "deplasman", "sehim", "eğil", "esne", "frekans", "titreşim", "load", "force", "deflection", "strength",
             "kırıl", "emniyet"),
            ["fem_setup", "fem_run", "fem_status", "beam_hand_calc"]),
    "render": (("render", "gerçekçi", "fotoğraf", "fotograf", "görselleştir", "sunum", "turntable", "animasyon"),
               ["render", "render_status"]),
    "history": (("dün", "önceki", "eski sürüm", "eski hal", "geçmiş", "gecmis", "history", "ne değişti", "commit",
                 "geri getir"), ["design_history", "open_design_version"]),
    "codecad": (("openscad", "scad", "cadquery", "build123d", "kod cad", "code cad"), ["code_cad", "code_cad_source",
                                                                                         "external_tools"]),
    "kicad": (("kicad", "pcb", "devre kart", "elektronik kart", "kart kutusu"), ["kicad_board", "external_tools"]),
}

# invented tool names small models use -> the real tool
TOOL_ALIASES = {
    "create_box": "add_box", "make_box": "add_box", "box": "add_box", "add_plate": "add_box", "create_plate": "add_box",
    "create_cube": "add_box", "add_cube": "add_box",
    "create_cylinder": "add_cylinder", "make_cylinder": "add_cylinder", "cylinder": "add_cylinder",
    "add_hole": "make_hole", "drill_hole": "make_hole", "create_hole": "make_hole", "hole": "make_hole",
    "drill": "make_hole", "cut_hole": "make_hole",
    "fillet": "fillet_edges", "add_fillet": "fillet_edges", "round_edges": "fillet_edges",
    "chamfer": "chamfer_edges", "add_chamfer": "chamfer_edges", "bevel_edges": "chamfer_edges",
    "cut": "boolean", "fuse": "boolean", "union": "boolean", "subtract": "boolean", "boolean_operation": "boolean",
    "move": "move_object", "translate": "move_object", "rotate": "move_object", "set_placement": "move_object",
    "get_objects": "get_document_summary", "list_objects": "get_document_summary", "document_summary":
        "get_document_summary", "get_document": "get_document_summary", "summary": "get_document_summary",
    "get_summary": "get_document_summary",
    "selection": "get_selection", "get_selected": "get_selection",
    "markers": "get_markers", "get_marker": "get_markers", "list_markers": "get_markers",
    "get_faces": "find_faces", "list_faces": "find_faces", "find_face": "find_faces",
    "get_edges": "list_edges", "find_edges": "list_edges",
    "measure_object": "measure", "get_dimensions": "measure", "measurement": "measure",
    "python": "run_python", "execute_python": "run_python", "exec_python": "run_python", "run_code": "run_python",
    "execute_code": "run_python", "freecad_python": "run_python",
    "set_parameter": "set_property", "change_property": "set_property", "set_dimension": "set_property",
    "update_property": "set_property",
    "recipes": "freecad_recipes", "get_recipe": "freecad_recipes",
    "export": "export_model", "save_as": "export_model", "export_step": "export_model", "export_stl": "export_model",
    "screenshot": "capture_view", "take_screenshot": "capture_view",
    "drawing": "technical_drawing", "make_drawing": "technical_drawing",
}

WORD_START = r"(?<![0-9a-zçğıöşü])"


def _mentions(text, words):
    low = text.replace("İ", "i").lower()
    return any(re.search(WORD_START + w, low) for w in words)


def groups_for(texts):
    joined = "\n".join(t for t in texts if t)
    return [g for g, (words, _) in GROUPS.items() if _mentions(joined, words)]


def group_of(name):
    if name in CORE:
        return "core"
    return next((g for g, (_, tools) in GROUPS.items() if name in tools), None)


def tool_names(texts=()):
    names = list(CORE)
    for g in groups_for(texts):
        names += [n for n in GROUPS[g][1] if n not in names]
    return names


def trim_schema(schema, keep):
    schema = copy.deepcopy(schema)
    props = schema.get("properties") or {}
    if keep is not None:
        wanted = set(schema.get("required", [])) | set(keep)
        schema["properties"] = {k: v for k, v in props.items() if k in wanted}
    for v in schema.get("properties", {}).values():  # nested descriptions cost tokens and rarely help
        if isinstance(v, dict) and len(v.get("description", "")) > 90:
            v["description"] = v["description"][:87].rsplit(" ", 1)[0] + "…"
    return schema


def view(tool):
    """Small-model version of a Tool (same name and function), or None when it is not offered to small models."""
    entry = SHORT.get(tool.name)
    if entry is None:
        return None
    desc, keep = entry
    return SimpleNamespace(name=tool.name, description=desc, schema=trim_schema(tool.schema, keep),
                           mutates=tool.mutates, func=getattr(tool, "func", None), title=getattr(tool, "title", ""))


def select(tools, texts=()):
    """Small-model views of the tools relevant to the conversation, in a stable order."""
    by_name = {t.name: t for t in tools}
    out = []
    for name in tool_names(texts):
        if name in by_name:
            v = view(by_name[name])
            if v is not None:
                out.append(v)
    return out
