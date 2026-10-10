"""Fusion adapter: assembly inspection and scene, root-part native parameters/features.

All methods run on the Fusion main thread. Assembly geometry editing remains guarded.
"""

import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import sys
import time
from array import array

from cadai_core.contract import READ_ONLY_UI, Result, capabilities, check_target, error_response, private_write, tool_response
from cadai_core.registry import Registry, Tool


def vec(point, scale=10):
    return [round(v * scale, 7) for v in (point.x, point.y, point.z)]


def bbox(entity):
    box = entity.boundingBox
    low, high = vec(box.minPoint), vec(box.maxPoint)
    return {"min": low, "max": high, "size": [round(b - a, 7) for a, b in zip(low, high)]}


def binary(kind, values):
    data = array(kind, values)
    if sys.byteorder == "big":
        data.byteswap()
    return base64.b64encode(data.tobytes()).decode("ascii")


def native(entity):
    return getattr(entity, "nativeObject", None) or entity


def placement(body):
    occurrence = getattr(body, "assemblyContext", None)
    if occurrence is None:
        return None
    matrix = occurrence.transform2
    return tuple(tuple(matrix.getCell(row, col) for col in range(4)) for row in range(3))


def world_coordinates(values, transform=None):
    """Native component coordinates (cm) -> root assembly coordinates (mm), exactly once."""
    if transform is None:
        return [v * 10 for v in values]
    result = []
    for i in range(0, len(values), 3):
        x, y, z = values[i:i + 3]
        result.extend((row[0] * x + row[1] * y + row[2] * z + row[3]) * 10 for row in transform)
    return result


def mesh_data(mesh):
    """Native mesh body -> (node coordinates in cm, triangle node indices). Display mesh first, polygon mesh fallback."""
    source = native(mesh)
    display = getattr(source, "displayMesh", None)
    if display is not None and display.nodeCount:
        return list(display.nodeCoordinatesAsDouble), list(display.nodeIndices)
    polygons = source.mesh
    return list(polygons.nodeCoordinatesAsDouble), list(polygons.triangleIndices)


def mesh_volume(coords, indices):
    """Signed tetrahedron sum (mm3 for mm coordinates); meaningful only for a closed, consistently oriented mesh."""
    total = 0.0
    for i in range(0, len(indices) - 2, 3):
        a, b, c = (coords[3 * n:3 * n + 3] for n in indices[i:i + 3])
        total += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
                  + a[2] * (b[0] * c[1] - b[1] * c[0]))
    return abs(total) / 6


def schema(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False}


STRING = {"type": "string"}
NUMBER = {"type": "number"}
POSITIVE = {"type": "number", "exclusiveMinimum": 0}
POINT = {"type": "array", "items": NUMBER, "minItems": 3, "maxItems": 3}
STRINGS = {"type": "array", "items": STRING, "minItems": 1}
EDGE_SELECTORS = ("all", "top", "bottom", "vertical", "horizontal", "circular")
EDGES = {"type": ["array", "string"], "items": STRING, "minItems": 1}
MESH_UNITS = {"mm": "MillimeterMeshUnit", "cm": "CentimeterMeshUnit", "m": "MeterMeshUnit", "in": "InchMeshUnit",
              "ft": "FootMeshUnit"}
MESH_CONVERT = {"prismatic": "PrismaticMeshConvertMethodType", "faceted": "FacetedMeshConvertMethodType",
                "organic": "OrganicMeshConvertMethodType"}
COMBINE = {"cut": "CutFeatureOperation", "fuse": "JoinFeatureOperation", "common": "IntersectFeatureOperation"}


class Adapter:
    # Survives a development reload so document IDs, revisions and body/instance IDs stay stable.
    KEPT_STATE = ("session_id", "docs", "state", "fingerprint", "body_ids", "live_body_ids", "occurrence_ids",
                  "instance_ids", "document_id", "created")

    def __init__(self, app, info_dir, version, api=None, reload=None):
        if api is None:
            import adsk.core
            import adsk.fusion

            api = adsk
        self.app, self.core, self.fusion = app, api.core, api.fusion
        self.do_events = getattr(api, "doEvents", lambda: time.sleep(0.05))
        self.adapter_version, self.info_dir = version, info_dir
        self.session_id = None
        self.docs = []
        self.state = {"doc": 0, "sel": 0, "mk": 0}
        self.fingerprint = None
        self.last_selection = None
        self.refs, self.cache = {}, {}
        self.body_ids = {}
        self.live_body_ids = {}
        self.occurrence_ids = {}
        self.instance_ids = {}
        self.document_id = None
        self.registry = Registry()
        specs = [
            ("get_document_summary", "Inspect root and nested component body instances, placements and native parameters.",
             schema(), False),
            ("get_selection", "Read native Fusion or VS Code face/edge selection.", schema(), False),
            ("get_markers", "Read signed markers; untrusted notes require user approval.", schema(), False),
            ("list_parameters", "Read native parameter names, expressions, values and units.", schema(), False),
            ("find_faces", "Inspect faces; use returned IDs and measured geometry, never guess IDs.",
             schema({"object": STRING, "surface_type": STRING}, ("object",)), False),
            ("list_edges", "Inspect measured edges of a body.", schema({"object": STRING}, ("object",)), False),
            ("measure", "Measure exact body geometry in mm, mm2, mm3; optional density in kg/m3.",
             schema({"objects": STRINGS, "density_kg_m3": POSITIVE}, ("objects",)), False),
            ("set_property", "Change a native parameter. object=Parameters, property=parameter name. "
             "Numeric values use mm for lengths, degrees for angles; expressions include units.",
             schema({"object": STRING, "property": STRING, "value": {"type": ["number", "string"]}},
                    ("object", "property", "value")), True),
            ("add_box", "Create a native sketch/extrusion box at a root-coordinate position (mm).",
             schema({"length": POSITIVE, "width": POSITIVE, "height": POSITIVE, "position": POINT, "name": STRING},
                    ("length", "width", "height")), True),
            ("add_cylinder", "Create a native circular sketch/extrusion along root Z (mm). position is the bottom "
             "circle center; give radius or diameter.",
             schema({"radius": POSITIVE, "diameter": POSITIVE, "height": POSITIVE, "position": POINT, "name": STRING},
                    ("height",)), True),
            ("make_hole", "Create a native hole at a measured point on a planar face; only the named body is cut.",
             schema({"object": STRING, "face": STRING, "position": POINT, "diameter": POSITIVE, "depth": POSITIVE},
                    ("object", "face", "position", "diameter")), True),
            ("fillet_edges", "Create a native constant radius fillet (mm). edges: IDs from list_edges or one word: "
             + ", ".join(EDGE_SELECTORS) + ".",
             schema({"object": STRING, "edges": EDGES, "radius": POSITIVE}, ("object", "edges", "radius")), True),
            ("chamfer_edges", "Create a native equal distance chamfer (mm). edges: IDs from list_edges or one word: "
             + ", ".join(EDGE_SELECTORS) + ".",
             schema({"object": STRING, "edges": EDGES, "size": POSITIVE}, ("object", "edges", "size")), True),
            ("boolean", "Native combine feature: cut removes tool from base, fuse joins, common keeps the overlap. "
             "The tool body is consumed; the result keeps the base body ID.",
             schema({"operation": {"type": "string", "enum": list(COMBINE)}, "base": STRING, "tool": STRING},
                    ("operation", "base", "tool")), True),
            ("move_object", "Native move feature on one body. position: new bounding box minimum corner [x, y, z]; "
             "offset: move by [dx, dy, dz]; rotation_deg about rotation_axis (default Z) through the body center.",
             schema({"object": STRING, "position": POINT, "offset": POINT, "rotation_axis": POINT,
                     "rotation_deg": NUMBER}, ("object",)), True),
            ("convert_mesh", "Convert a mesh body (imported STL/OBJ/3MF, type Fusion::MeshBody) into an editable solid "
             "with Fusion's Convert Mesh, so holes, fillets, chamfers and booleans work on it. method: prismatic "
             "(default; machined parts: flat and cylindrical areas become real planes/cylinders), faceted (every "
             "triangle a face; always works, heavy), organic (freeform T-spline). Returns the new solid's ID; the "
             "mesh is consumed. Then inspect it with find_faces/list_edges before editing.",
             schema({"object": STRING, "method": {"type": "string", "enum": list(MESH_CONVERT)}}, ("object",)), True),
            ("scale_object", "Uniformly scale a body about the origin (native Scale feature), e.g. factor 0.1 when a mesh "
             "was imported in cm instead of mm. Ask the user before changing the size of their part.",
             schema({"object": STRING, "factor": POSITIVE}, ("object", "factor")), True),
            ("import_mesh", "Import an STL/OBJ/3MF file as a mesh body in the root component (view, select, measure; "
             "not editable as a solid). units: mm (default), cm, m, in, ft.",
             schema({"path": STRING, "units": {"type": "string", "enum": list(MESH_UNITS)}}, ("path",)), True),
            ("export_model", "Export the whole root component as STEP or F3D to a local file.",
             schema({"path": STRING}, ("path",)), True),
        ]
        for name, description, inputs, mutates in specs:
            self.registry.register(Tool(name, description, inputs, getattr(self, name), mutates,
                                        "export" if name in ("export_model", "import_mesh") else "core"))
        self.actions = {name: getattr(self, name) for name in (
            "tree", "scene", "selection", "set_selection", "markers", "add_marker", "update_marker", "delete_marker",
            "clear_markers", "new_document", "activate_document", "close_document", "set_visibility", "recompute",
            "save_document", "undo", "redo")}
        if reload is not None:
            self.actions["reload_addon"] = reload
        self.created = []

    def design(self, edit=False):
        doc = self.app.activeDocument
        if doc is None:
            raise ValueError("Açık Fusion belgesi yok.")
        design = self.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
        if design is None:
            raise ValueError("Aktif Fusion belgesi bir Design belgesi değil.")
        if edit and design.rootComponent.allOccurrences.count:
            raise ValueError("Alt bileşenler görüntülenebilir ve ölçülebilir; montaj geometrisi düzenleme henüz desteklenmiyor.")
        return design

    def bodies(self, design=None):
        root = (design or self.design()).rootComponent
        bodies = list(root.bRepBodies)
        for occurrence in root.allOccurrences:
            bodies.extend(occurrence.bRepBodies)
        return bodies

    def meshes(self, design=None):
        """Mesh bodies (imported STL/OBJ/3MF): viewable, selectable and measurable, never edited."""
        root = (design or self.design()).rootComponent
        meshes = list(getattr(root, "meshBodies", None) or [])
        for occurrence in root.allOccurrences:
            for mesh in getattr(getattr(occurrence, "component", None), "meshBodies", None) or []:
                meshes.append(mesh.createForAssemblyContext(occurrence))
        return meshes

    def is_mesh(self, entity):
        caster = getattr(self.fusion, "MeshBody", None)
        return caster is not None and caster.cast(entity) is not None

    def _occurrence_id(self, occurrence):
        source = native(occurrence)
        known = self.occurrence_ids.setdefault(self.document_id, [])
        for previous, saved in known:
            if previous.isValid and previous == source:
                return saved
        identity = secrets.token_hex(16)
        known.append((source, identity))
        return identity

    @staticmethod
    def visible(body):
        occurrence = getattr(body, "assemblyContext", None)
        return bool(body.isVisible and (occurrence is None or occurrence.isVisible))

    @staticmethod
    def label(body):
        occurrence = getattr(body, "assemblyContext", None)
        return f"{occurrence.fullPathName} / {body.name}" if occurrence else body.name

    def context(self):
        doc = self.app.activeDocument
        self.docs = [entry for entry in self.docs if entry[0].isValid]
        entry = next((e for e in self.docs if e[0] == doc), None) if doc else None
        if doc and entry is None:
            entry = (doc, secrets.token_hex(16))
            self.docs.append(entry)
        doc_id = entry[1] if entry else None
        self.document_id = doc_id
        live_ids = {e[1] for e in self.docs}
        self.body_ids = {k: v for k, v in self.body_ids.items() if k in live_ids}
        self.live_body_ids = {k: v for k, v in self.live_body_ids.items() if k in live_ids}
        self.occurrence_ids = {k: v for k, v in self.occurrence_ids.items() if k in live_ids}
        self.instance_ids = {k: v for k, v in self.instance_ids.items() if k[0] in live_ids}
        fingerprint = [doc_id, doc.name if doc else None]
        if doc:
            design = self.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
            if design:
                fingerprint += [(native(b).revisionId, self.label(b), self.visible(b), placement(b))
                                for b in self.bodies(design)]
                fingerprint += [(native(m).entityToken, self.label(m), self.visible(m), placement(m))
                                for m in self.meshes(design)]
                fingerprint += [(p.name, p.expression) for p in design.allParameters]
                fingerprint += [(self._occurrence_id(o), o.fullPathName, o.isVisible)
                                for o in design.rootComponent.allOccurrences]
        fingerprint = repr(fingerprint)
        if fingerprint != self.fingerprint:
            self.fingerprint = fingerprint
            self.state["doc"] += 1
            self.refs.clear()
        return {"backend_id": "fusion", "session_id": self.session_id, "document_id": doc_id,
                "document_name": doc.name if doc else None, "revision": self.state["doc"]}

    def version(self):
        self.context()
        try:
            selection = repr(self.selection()) if self.app.activeDocument else "[]"
        except ValueError:
            selection = "unsupported-document"
        if selection != self.last_selection:
            self.last_selection = selection
            self.state["sel"] += 1
        return dict(self.state)

    def capabilities(self):
        return capabilities("fusion", self.adapter_version, self.registry, self.actions, (
            "Experimental; real Fusion verification is required before release.",
            "Nested component bodies support viewing, selection, markers and measurements; assembly geometry editing "
            "is not supported yet. FEM, DFM, drawings, CAM and render are not supported.",
            "No atomic rollback after a failed mutation; inspect the model. undo/redo run Fusion's own commands.",
            "STEP/F3D export covers the whole root component, not a body selection.",
            "Changing an expression can change multiple bodies; resulting geometry is measured."))

    def execute(self, path, request):
        current = self.context()
        try:
            if path == "/call":
                name, args = request.get("name"), request.get("arguments", {})
                args = self.registry.normalized_args(name, args)
                mutates = self.registry.get(name).mutates
            else:
                action, args = request.get("action"), request.get("args", {})
                if action not in self.actions:
                    raise ValueError(f"Fusion adaptörü bu işlemi desteklemiyor: {action}")
                mutates = action not in READ_ONLY_UI
            check_target(request.get("target"), current, required=True, mutates=mutates)
            if path == "/call":
                if mutates and name != "export_model":
                    self.design(edit=True)

                def fn():
                    return self.registry.get(name).func(**args)
            else:
                def fn():
                    return self.actions[action](**args)
            if mutates and self.app.userInterface.activeCommand != "SelectCommand":
                self.app.userInterface.commandDefinitions.itemById("SelectCommand").execute()
                check_target(request.get("target"), self.context(), required=True)
            result = fn()
            if path == "/call" and mutates and name != "export_model":
                self.state["doc"] += 1
                self.context()
                result = dict(result, verification=self.measure([b.entityToken for b in self.design().rootComponent.bRepBodies]))
            if path == "/ui":
                return {"ok": True, "result": result, "context": self.context()}
            return tool_response(Result(result), self.context())
        except Exception as e:
            return error_response(path, e, self.context())

    def _body_id(self, body):
        # A body token is used as its opaque ID; it is resolved, never compared for semantic equality.
        source = native(body)
        token = source.entityToken
        known = self.body_ids.setdefault(self.document_id, [])
        live = self.live_body_ids.setdefault(self.document_id, [])
        for previous, saved in live:
            if previous.isValid and previous == source:
                token = saved
                break
        else:
            for saved in known:
                try:
                    resolved = self.design().findEntityByToken(saved)
                except RuntimeError:
                    resolved = []
                if len(resolved) == 1 and native(resolved[0]) == source:
                    token = saved
                    break
            else:
                known.append(token)
            live.append((source, token))
        occurrence = getattr(body, "assemblyContext", None)
        if occurrence:
            key = (self.document_id, self._occurrence_id(occurrence), occurrence.fullPathName, token)
            token = self.instance_ids.setdefault(key, "instance:" + secrets.token_hex(16))
        return token

    def _remember(self, body):
        token = self._body_id(body)
        if token not in self.refs:
            self.refs[token] = {f"Face{i}": native(f).entityToken for i, f in enumerate(body.faces, 1)}
            self.refs[token].update({f"Edge{i}": native(e).entityToken for i, e in enumerate(body.edges, 1)})
        return token

    def body(self, name, mesh_ok=False):
        design = self.design()
        meshes = [m for m in self.meshes(design) if self._body_id(m) == name or m.name == name]
        if meshes:
            if not mesh_ok:
                raise ValueError("Bu bir mesh (üçgen ağ) gövdesi: görüntülenir ve ölçülür ama B-rep yüzü/kenarı yoktur, "
                                 "düzenlenemez. Fusion'da Mesh → Dönüştür (Convert Mesh) ile katıya çevirin.")
            if len(meshes) != 1:
                raise ValueError("Mesh gövdesi adı belirsiz; get_document_summary kimliğini kullanın.")
            return meshes[0]
        if design.rootComponent.allOccurrences.count:
            found = [b for b in self.bodies(design) if self._body_id(b) == name or b.name == name]
            if len(found) != 1 or not found[0].isValid:
                raise ValueError("Gövde örneği yok veya ad belirsiz; get_document_summary kimliğini kullanın.")
            return found[0]
        try:
            found = design.findEntityByToken(name)
        except RuntimeError:
            found = []
        if not found:
            found = [b for b in design.rootComponent.bRepBodies if b.name == name]
        bodies = [self.fusion.BRepBody.cast(e) for e in found]
        bodies = [b for b in bodies if b is not None and b.isValid and b.parentComponent == design.rootComponent]
        if len(bodies) != 1:
            raise ValueError("Nesne bulunamadı veya adı belirsiz; get_document_summary çıktısındaki kimliği kullanın.")
        return bodies[0]

    def element(self, body, name):
        aliases = None
        if getattr(body, "assemblyContext", None) is not None:
            aliases = self.refs.get(self._body_id(body))
        for token, saved in self.refs.items():
            if aliases is not None:
                break
            if token.startswith("instance:"):
                continue
            matches = self.design().findEntityByToken(token)
            if len(matches) == 1 and matches[0] == body:
                aliases = saved
                break
        if aliases is None or name not in aliases:
            raise ValueError("Yüz/kenar referansı eski veya bilinmiyor. find_faces/list_edges ile yeniden inceleyin.")
        found = self.design().findEntityByToken(aliases[name])
        if len(found) != 1 or not found[0].isValid or native(getattr(found[0], "body", None)) != native(body):
            raise ValueError("Yüz/kenar artık tek bir öğeye çözülemiyor; işlem uygulanmadı.")
        occurrence = getattr(body, "assemblyContext", None)
        return native(found[0]).createForAssemblyContext(occurrence) if occurrence else found[0]

    def list_parameters(self):
        units = self.design().unitsManager
        return {"parameters": [{"name": p.name, "expression": p.expression, "unit": p.unit,
                                "display": units.formatInternalValue(p.value, p.unit, True)}
                               for p in self.design().allParameters]}

    def get_document_summary(self):
        current = self.context()
        design = self.design()
        objects, warnings = [], []
        for body in self.bodies(design):
            item = {"name": self._remember(body), "label": self.label(body), "type": "Fusion::BRepBody",
                    "bbox": bbox(body), "faces": body.faces.count, "hidden": not self.visible(body)}
            try:
                item["volume_mm3"] = native(body).volume * 1000
            except Exception as error:
                # Imported bodies can render successfully even when ASM cannot compute their volume.
                item.update(volume_mm3=None, volume_error=str(error))
                warnings.append({"object": item["name"], "label": item["label"], "operation": "volume",
                                 "error": str(error)})
            objects.append(item)
        for mesh in self.meshes(design):
            objects.append({"name": self._body_id(mesh), "label": self.label(mesh), "type": "Fusion::MeshBody",
                            "bbox": bbox(mesh), "faces": 0, "hidden": not self.visible(mesh), "editable": False,
                            "note": "Mesh gövdesi: görüntüleme, seçim ve ölçüm; düzenleme için Fusion'da katıya çevirin."})
        return {"document": self.app.activeDocument.name, "objects": objects,
                **self.list_parameters(), "context": current, "scope": "assembly",
                "geometry_editable": not bool(design.rootComponent.allOccurrences.count), "warnings": warnings}

    def measure(self, objects, density_kg_m3=None):
        result = {}
        for name in objects:
            body = self.body(name, mesh_ok=True)
            if self.is_mesh(body):
                coords, indices = mesh_data(body)
                coords = world_coordinates(coords, placement(body))
                volume = mesh_volume(coords, indices)
                item = {"bbox": bbox(body), "type": "mesh", "triangles": len(indices) // 3, "volume_mm3": volume,
                        "volume_source": "mesh triangles; valid only if the mesh is closed", "area_mm2": None,
                        "center_of_mass": None}
                if density_kg_m3 is not None:
                    item["mass_g"] = volume * 1e-6 * density_kg_m3
                result[self._body_id(body)] = item
                continue
            item = {"bbox": bbox(body), "valid": body.isValid, "solids": int(body.isSolid),
                    "faces": body.faces.count, "edges": body.edges.count}
            try:
                props = native(body).getPhysicalProperties(self.fusion.CalculationAccuracy.HighCalculationAccuracy)
                if props is None:
                    raise ValueError("Fusion fiziksel özellikleri hesaplayamadı.")
            except Exception as error:
                item.update(volume_mm3=None, area_mm2=None, mass_kg=None, center_of_mass=None,
                            physical_properties_error=str(error))
                result[self._remember(body)] = item
                continue
            center = world_coordinates([props.centerOfMass.x, props.centerOfMass.y, props.centerOfMass.z], placement(body))
            item.update(volume_mm3=props.volume * 1000, area_mm2=props.area * 100,
                        mass_kg=props.mass, center_of_mass=center)
            if density_kg_m3 is not None:
                item["mass_g"] = props.volume * 1e-3 * density_kg_m3
            result[self._remember(body)] = item
        return result

    def find_faces(self, object, surface_type=None):
        body = self.body(object)
        token = self._remember(body)
        faces = []
        for i, face in enumerate(body.faces, 1):
            kind = face.geometry.objectType.rsplit("::", 1)[-1]
            if surface_type and kind.lower() != surface_type.lower():
                continue
            ok, normal = face.evaluator.getNormalAtPoint(face.pointOnFace)
            item = {"name": f"Face{i}", "surface": kind, "area_mm2": face.area * 100,
                    "center": vec(face.centroid), "point_on_face": vec(face.pointOnFace), "bbox": bbox(face)}
            if ok:
                item["normal"] = vec(normal, 1)
            if hasattr(face.geometry, "radius"):
                item["radius_mm"] = face.geometry.radius * 10
            faces.append(item)
        return {"object": token, "count": len(faces), "faces": faces}

    def list_edges(self, object):
        body = self.body(object)
        token = self._remember(body)
        return {"object": token, "edges": [{"name": f"Edge{i}", "length_mm": e.length * 10,
                                            "curve": e.geometry.objectType.rsplit("::", 1)[-1], "bbox": bbox(e)}
                                           for i, e in enumerate(body.edges, 1)]}

    def set_property(self, object, property, value):
        name = property if object == "Parameters" else object
        if object != "Parameters" and property != "expression":
            raise ValueError("Fusion'da object=Parameters, property=parametre adı veya "
                             "object=parametre, property=expression kullanın.")
        design = self.design(edit=True)
        param = design.allParameters.itemByName(name)
        if param is None:
            raise ValueError("Parametre yok; list_parameters ile inceleyin.")
        if isinstance(value, (int, float)):
            if not math.isfinite(value):
                raise ValueError("Parametre sonlu olmalı.")
            units = design.unitsManager
            if units.isValidExpression("1 mm", param.unit):
                expression = f"{value} mm"
            elif units.isValidExpression("1 deg", param.unit):
                expression = f"{value} deg"
            else:
                raise ValueError("Bu parametrede birimli bir ifade verin; sayısal değer yalnızca uzunluk/açı için desteklenir.")
        else:
            expression = value
        if not design.unitsManager.isValidExpression(expression, param.unit):
            raise ValueError("Parametre ifadesi veya birimi geçersiz.")
        param.expression = expression
        if not design.computeAll():
            raise ValueError("Fusion yeniden hesaplayamadı; parametre uygulanmış olabilir, modeli inceleyin.")
        return {"parameter": name, "expression": param.expression}

    def _extrude(self, sketch, height, name):
        root = self.design(edit=True).rootComponent
        if sketch.profiles.count != 1:
            raise ValueError("Eskiz tek kapalı profil üretmedi; modeli inceleyin.")
        feature = root.features.extrudeFeatures.addSimple(sketch.profiles.item(0),
                                                        self.core.ValueInput.createByReal(height / 10),
                                                        self.fusion.FeatureOperations.NewBodyFeatureOperation)
        body = feature.bodies.item(0)
        body.name = name
        sketch.isVisible = False
        return {"object": self._remember(body), "feature": feature.name}

    def _sketch(self, position):
        root = self.design(edit=True).rootComponent
        # Use an offset construction plane so extrusion position is a native parameter.
        plane_input = root.constructionPlanes.createInput()
        plane_input.setByOffset(root.xYConstructionPlane, self.core.ValueInput.createByReal(position[2] / 10))
        plane = root.constructionPlanes.add(plane_input)
        sketch = root.sketches.add(plane)
        plane.isLightBulbOn = False  # isVisible is read-only
        return sketch

    def add_box(self, length, width, height, position=None, name="Box"):
        position = position or [0, 0, 0]
        sketch = self._sketch(position)
        lines = sketch.sketchCurves.sketchLines.addTwoPointRectangle(
            self.core.Point3D.create(position[0] / 10, position[1] / 10, 0),
            self.core.Point3D.create((position[0] + length) / 10, (position[1] + width) / 10, 0))
        dims = []
        for i, orientation, size in ((0, self.fusion.DimensionOrientations.HorizontalDimensionOrientation, length),
                                     (1, self.fusion.DimensionOrientations.VerticalDimensionOrientation, width)):
            line = lines.item(i)
            dimension = sketch.sketchDimensions.addDistanceDimension(
                line.startSketchPoint, line.endSketchPoint, orientation, line.startSketchPoint.geometry)
            if dimension is None:
                raise ValueError("Fusion eskiz ölçüsünü oluşturamadı; modeli inceleyin.")
            dimension.parameter.expression = f"{size} mm"
            dims.append(dimension.parameter.name)
        return dict(self._extrude(sketch, height, name), sketch_parameters=dims)

    def add_cylinder(self, height, radius=None, diameter=None, position=None, name="Cylinder"):
        if (radius is None) == (diameter is None):
            raise ValueError("radius ya da diameter değerlerinden yalnızca birini verin.")
        radius = radius if radius is not None else diameter / 2
        position = position or [0, 0, 0]
        sketch = self._sketch(position)
        circle = sketch.sketchCurves.sketchCircles.addByCenterRadius(
            self.core.Point3D.create(position[0] / 10, position[1] / 10, 0), radius / 10)
        # The text point must lie off the center; Fusion rejects a radial dimension placed at the center.
        text = self.core.Point3D.create((position[0] + radius * 1.5) / 10, (position[1] + radius * 1.5) / 10, 0)
        dimension = sketch.sketchDimensions.addRadialDimension(circle, text)
        if dimension is None:
            raise ValueError("Fusion yarıçap ölçüsünü oluşturamadı; modeli inceleyin.")
        dimension.parameter.expression = f"{radius} mm"
        return dict(self._extrude(sketch, height, name), radius_parameter=dimension.parameter.name)

    def make_hole(self, object, face, position, diameter, depth=None):
        self.design(edit=True)
        body = self.body(object)
        surface = self.element(body, face)
        if self.core.Plane.cast(surface.geometry) is None:
            raise ValueError("İlk Fusion delik aracı düz bir yüz gerektirir.")
        point = self.core.Point3D.create(*[v / 10 for v in position])
        evaluator = surface.evaluator
        found, param = evaluator.getParameterAtPoint(point)
        if found:
            ok, nearest = evaluator.getPointAtParameter(param)
            found = ok and nearest.distanceTo(point) < 1e-4 and evaluator.isParameterOnFace(param)
        if not found:
            raise ValueError("Delik noktası seçilen yüzün içinde değil; point_on_face değerini kullanın.")
        holes = self.design().rootComponent.features.holeFeatures
        inputs = holes.createSimpleInput(self.core.ValueInput.createByReal(diameter / 10))
        if not inputs.setPositionByPoint(surface, point):
            raise ValueError("Fusion delik konumunu kabul etmedi.")
        inputs.participantBodies = [body]
        if depth is None:
            inputs.setAllExtent(self.fusion.ExtentDirections.PositiveExtentDirection)
        else:
            inputs.setDistanceExtent(self.core.ValueInput.createByReal(depth / 10))
        before = body.volume
        feature = holes.add(inputs)
        removed = (before - body.volume) * 1000
        if removed <= 1e-6:
            raise ValueError("Delik malzeme çıkarmadı; özellik oluşmuş olabilir. Modeli ve yönü inceleyin.")
        return {"object": self._remember(body), "feature": feature.name, "diameter_parameter": feature.holeDiameter.name,
                "removed_volume_mm3": removed}

    def _edges(self, body, edges):
        """Edge IDs or one selector word -> (names, ObjectCollection). Selectors use measured root coordinates."""
        self._remember(body)
        if isinstance(edges, str):
            word = edges.strip().lower()
            if word not in EDGE_SELECTORS:
                edges = [edges]
            else:
                box, tol = bbox(body), 1e-4
                names = []
                for i, edge in enumerate(body.edges, 1):
                    low, high = bbox(edge)["min"], bbox(edge)["max"]
                    flat = abs(high[2] - low[2]) < tol
                    curve = edge.geometry.objectType.rsplit("::", 1)[-1]
                    if (word == "all" or (word == "top" and flat and abs(high[2] - box["max"][2]) < tol)
                            or (word == "bottom" and flat and abs(low[2] - box["min"][2]) < tol)
                            or (word == "horizontal" and flat)
                            or (word == "vertical" and curve == "Line3D" and abs(high[0] - low[0]) < tol
                                and abs(high[1] - low[1]) < tol)
                            or (word == "circular" and curve in ("Circle3D", "Arc3D"))):
                        names.append(f"Edge{i}")
                if not names:
                    raise ValueError(f"'{word}' seçicisine uyan kenar yok; list_edges ile kenar kimliklerini verin.")
                edges = names
        collection = self.core.ObjectCollection.create()
        for name in edges:
            edge = self.element(body, name)
            if self.fusion.BRepEdge.cast(edge) is None:
                raise ValueError(f"{name} bir kenar değil; list_edges kimliklerini kullanın.")
            collection.add(edge)
        return list(edges), collection

    def fillet_edges(self, object, edges, radius):
        self.design(edit=True)
        body = self.body(object)
        names, collection = self._edges(body, edges)
        fillets = self.design().rootComponent.features.filletFeatures
        inputs = fillets.createInput()
        inputs.edgeSetInputs.addConstantRadiusEdgeSet(collection, self.core.ValueInput.createByReal(radius / 10), False)
        feature = fillets.add(inputs)
        return {"object": self._remember(body), "feature": feature.name, "edges": names}

    def chamfer_edges(self, object, edges, size):
        self.design(edit=True)
        body = self.body(object)
        names, collection = self._edges(body, edges)
        chamfers = self.design().rootComponent.features.chamferFeatures
        inputs = chamfers.createInput2()
        inputs.chamferEdgeSets.addEqualDistanceChamferEdgeSet(collection, self.core.ValueInput.createByReal(size / 10),
                                                              False)
        feature = chamfers.add(inputs)
        return {"object": self._remember(body), "feature": feature.name, "edges": names}

    def boolean(self, operation, base, tool):
        self.design(edit=True)
        target, other = self.body(base), self.body(tool)
        if target == other:
            raise ValueError("base ve tool aynı gövde olamaz.")
        before = target.volume
        tools = self.core.ObjectCollection.create()
        tools.add(other)
        combines = self.design().rootComponent.features.combineFeatures
        inputs = combines.createInput(target, tools)
        inputs.operation = getattr(self.fusion.FeatureOperations, COMBINE[operation])
        inputs.isKeepToolBodies = False
        feature = combines.add(inputs)
        if not target.isValid or target.volume <= 1e-9:
            raise ValueError(f"{operation} sonucu boş: gövdeler birbirine değmiyor olabilir. Modeli inceleyin.")
        return {"object": self._remember(target), "feature": feature.name,
                "volume_change_mm3": (target.volume - before) * 1000}

    def move_object(self, object, position=None, offset=None, rotation_axis=None, rotation_deg=None):
        self.design(edit=True)
        body = self.body(object)
        if position is None and offset is None and not rotation_deg:
            raise ValueError("position (yeni konum), offset (kaydırma) ya da rotation_deg (döndürme) verin.")
        box = bbox(body)
        matrix = self.core.Matrix3D.create()
        if rotation_deg:
            axis = rotation_axis or [0, 0, 1]
            if math.sqrt(sum(v * v for v in axis)) < 1e-9:
                raise ValueError("rotation_axis sıfır olamaz.")
            center = [(a + b) / 20 for a, b in zip(box["min"], box["max"])]
            matrix.setToRotation(math.radians(rotation_deg), self.core.Vector3D.create(*axis),
                                 self.core.Point3D.create(*center))
        shift = [0.0, 0.0, 0.0]
        if position is not None:
            shift = [p - m for p, m in zip(position, box["min"])]
        if offset is not None:
            shift = [s + o for s, o in zip(shift, offset)]
        if any(shift):
            translation = self.core.Matrix3D.create()
            translation.translation = self.core.Vector3D.create(*[s / 10 for s in shift])
            matrix.transformBy(translation)
        bodies = self.core.ObjectCollection.create()
        bodies.add(body)
        moves = self.design().rootComponent.features.moveFeatures
        inputs = moves.createInput2(bodies)
        inputs.defineAsFreeMove(matrix)
        feature = moves.add(inputs)
        return {"object": self._remember(body), "feature": feature.name, "old_min": box["min"],
                "new_min": bbox(body)["min"]}

    def convert_mesh(self, object, method="prismatic"):
        design = self.design(edit=True)
        mesh = self.body(object, mesh_ok=True)
        if not self.is_mesh(mesh):
            raise ValueError("Bu zaten katı (B-rep) bir gövde; dönüştürme gerekmiyor.")
        mesh_id = self._body_id(mesh)  # the mesh is consumed by the conversion
        features = design.rootComponent.features
        added = []  # features this call created: removed again if the conversion fails (no half-done model)
        try:
            if method == "prismatic":
                # Prismatic conversion needs face groups first (Fusion: Generate Face Groups, then Convert Mesh).
                groups = features.meshGenerateFaceGroupsFeatures
                group_input = groups.createInput(mesh)
                group_input.meshGenerateFaceGroupsMethodType = \
                    self.fusion.MeshGenerateFaceGroupsMethodTypes.AccurateGenerateFaceGroupsType
                added.append(groups.add(group_input))
            converts = features.meshConvertFeatures
            inputs = converts.createInput([mesh])
            inputs.meshConvertMethodType = getattr(self.fusion.MeshConvertMethodTypes, MESH_CONVERT[method])
            count = converts.count
            try:
                feature = converts.add(inputs)
            finally:
                if converts.count > count and not locals().get("feature"):
                    added.append(converts.item(converts.count - 1))  # Fusion keeps a failed feature in the timeline
        except Exception as error:
            for item in reversed(added):
                try:
                    item.deleteMe()
                except Exception:
                    pass
            hint = " method=faceted ile yeniden deneyin (her üçgen bir yüz olur ama her zaman çalışır)." \
                if method != "faceted" else ""
            raise ValueError(f"Fusion mesh'i {method} yöntemiyle katıya çeviremedi; model değiştirilmedi. "
                             f"Fusion: {error}.{hint}")
        bodies = [b for b in feature.bodies if b.isValid]
        if not bodies:
            raise ValueError("Dönüştürme katı gövde üretmedi; mesh açık (su geçirmez değil) olabilir.")
        out = []
        for body in bodies:
            item = {"object": self._remember(body), "label": body.name, "faces": body.faces.count, "bbox": bbox(body),
                    "solid": bool(body.isSolid)}
            try:
                item["volume_mm3"] = body.volume * 1000
            except Exception as error:
                item["volume_error"] = str(error)
            out.append(item)
        moved = self._move_markers(mesh_id, out[0]["object"]) if len(out) == 1 else []
        return {"feature": feature.name, "method": method, "bodies": out, "markers_moved": moved,
                "next": "Yeni gövde kimliğini kullanın; yüzleri find_faces ile inceleyin."}

    def _move_markers(self, old, new):
        """Markers on a converted mesh point at the same coordinates on the new solid: re-attach them instead of
        leaving them stale. Only markers whose signature verifies are re-signed (never launder untrusted ones)."""
        items, moved = self._load_markers(), []
        for marker in items:
            if old not in self._marker_objects(marker):
                continue
            if not (isinstance(marker.get("sig"), str) and hmac.compare_digest(marker["sig"], self._signature(marker))):
                continue
            for value in marker.values():
                if isinstance(value, dict) and value.get("object") == old:
                    value.update(object=new, element="")
            marker["geometry"] = {name: self._geometry_key(name) for name in self._marker_objects(marker)}
            marker["sig"] = self._signature(marker)
            moved.append(marker["id"])
        if moved:
            self._save_markers(items)
        return moved

    def scale_object(self, object, factor):
        design = self.design(edit=True)
        body = self.body(object, mesh_ok=True)
        before = bbox(body)
        entities = self.core.ObjectCollection.create()
        entities.add(body)
        scales = design.rootComponent.features.scaleFeatures
        inputs = scales.createInput(entities, design.rootComponent.originConstructionPoint,
                                    self.core.ValueInput.createByReal(factor))
        feature = scales.add(inputs)
        return {"object": self._body_id(body), "feature": feature.name, "old_size": before["size"],
                "new_size": bbox(body)["size"]}

    def import_mesh(self, path, units="mm"):
        if os.path.splitext(path)[1].lower() not in (".stl", ".obj", ".3mf"):
            raise ValueError("Mesh içe aktarma STL, OBJ veya 3MF dosyası bekler.")
        if not os.path.isfile(path):
            raise ValueError(f"Dosya yok: {path}")
        root = self.design(edit=True).rootComponent
        added = root.meshBodies.add(path, getattr(self.fusion.MeshUnits, MESH_UNITS[units]))
        meshes = list(added) if added is not None else []
        if not meshes:
            raise ValueError("Fusion dosyadan mesh gövdesi oluşturmadı.")
        return {"objects": [self._body_id(m) for m in meshes], "type": "mesh"}

    def export_model(self, path):
        manager = self.design().exportManager
        suffix = os.path.splitext(path)[1].lower()
        if suffix in (".step", ".stp"):
            options = manager.createSTEPExportOptions(path, self.design().rootComponent)
        elif suffix == ".f3d":
            options = manager.createFusionArchiveExportOptions(path)
        else:
            raise ValueError("İlk Fusion adaptörü STEP veya F3D dışa aktarır.")
        if not manager.execute(options):
            raise ValueError("Fusion dışa aktaramadı.")
        return {"file": path, "scope": "root_component"}

    def tree(self):
        docs = [{"name": token, "label": doc.name, "file": ""} for doc, token in self.docs]
        if self.app.activeDocument is None:
            return {"active": None, "documents": docs, "objects": []}
        summary = self.get_document_summary()
        objects = [{"name": b["name"], "label": b["label"], "type": b["type"], "visible": not b["hidden"], "dims": {}}
                   for b in summary["objects"]]
        dims = {p["name"]: p["expression"] for p in summary["parameters"]}
        objects.append({"name": "Parameters", "label": "Parametreler", "type": "Fusion::Parameters",
                        "visible": True, "dims": dims})
        return {"active": summary["context"]["document_id"], "label": self.app.activeDocument.name, "file": "",
                "documents": docs, "objects": objects, "geometry_editable": summary["geometry_editable"],
                "warnings": summary["warnings"]}

    def scene(self, quality=1, known=None):
        context = self.context()
        if context["document_id"] is None:
            return {"doc": None, "objects": [], "selection": [], "context": context}
        design = self.design()
        objects, bounds, warnings = [], [], []
        stats = {"tessellated": 0, "cached": 0, "unchanged": 0}
        quality = max(0.1, min(float(quality), 10))
        for body in self.bodies(design):
            if not self.visible(body):
                continue
            source, transform = native(body), placement(body)
            name = self._remember(body)
            box = bbox(body)
            bounds.append(box)
            key = f"{context['document_id']}|{name}|{source.revisionId}|{self.label(body)}|{transform}|{quality}"
            if key in (known or []):
                objects.append({"name": name, "key": key, "same": True})
                warnings.extend(self.cache.get(key, {}).get("warnings", []))
                stats["unchanged"] += 1
                continue
            if key not in self.cache:
                tol = max(math.sqrt(sum(v * v for v in box["size"])) * 0.0015 / quality, 0.005) / 10
                faces, edges, body_warnings = [], [], []

                def warn(element, error, items=body_warnings, identity=name, label=self.label(body)):
                    items.append({"object": identity, "label": label, "element": element,
                                          "operation": "scene", "error": str(error)})

                for i, face in enumerate(source.faces, 1):
                    try:
                        calc = face.meshManager.createMeshCalculator()
                        calc.surfaceTolerance = tol
                        mesh = calc.calculate()
                        if mesh is None:
                            raise ValueError("Fusion yüzü üçgenleyemedi.")
                        coords = world_coordinates(mesh.nodeCoordinatesAsDouble, transform)
                        wide = mesh.nodeCount >= 65536
                        faces.append({"name": f"Face{i}", "positions": binary("f", coords), "wide": wide,
                                      "indices": binary("I" if wide else "H", mesh.nodeIndices)})
                    except Exception as error:
                        warn(f"Face{i}", error)
                for i, edge in enumerate(source.edges, 1):
                    try:
                        evaluator = edge.evaluator
                        if evaluator is None:
                            raise ValueError("Fusion kenar değerlendiricisi yok.")
                        ok, start, end = evaluator.getParameterExtents()
                        if not ok:
                            raise ValueError("Fusion kenar aralığını ölçemedi.")
                        ok, points = evaluator.getStrokes(start, end, tol)
                        if not ok:
                            raise ValueError("Fusion kenarı örnekleyemedi.")
                        coords = world_coordinates([c for p in points for c in (p.x, p.y, p.z)], transform)
                        edges.append({"name": f"Edge{i}", "points": binary("f", coords)})
                    except Exception as error:
                        warn(f"Edge{i}", error)
                vertices = []
                for i, vertex in enumerate(source.vertices, 1):
                    try:
                        point = vertex.geometry
                        vertices.extend((point.x, point.y, point.z))
                    except Exception as error:
                        warn(f"Vertex{i}", error)
                self.cache[key] = {"name": name, "label": self.label(body), "type": "Fusion::BRepBody", "key": key,
                                   "color": [0.75, 0.78, 0.82], "enc": "b64", "faces": faces, "edges": edges,
                                   "vertices": binary("f", world_coordinates(vertices, transform)),
                                   "warnings": body_warnings}
                stats["tessellated"] += 1
            else:
                stats["cached"] += 1
            objects.append(self.cache[key])
            warnings.extend(self.cache[key].get("warnings", []))
        for mesh in self.meshes(design):
            if not self.visible(mesh):
                continue
            name, transform, box = self._body_id(mesh), placement(mesh), bbox(mesh)
            bounds.append(box)
            key = f"{context['document_id']}|{name}|mesh|{self.label(mesh)}|{transform}"
            if key in (known or []):
                objects.append({"name": name, "key": key, "same": True})
                stats["unchanged"] += 1
                continue
            if key not in self.cache:
                item = {"name": name, "label": self.label(mesh), "type": "Fusion::MeshBody", "key": key,
                        "color": [0.75, 0.78, 0.82], "enc": "b64", "faces": [], "edges": [],
                        "vertices": binary("f", []), "warnings": []}
                try:
                    coords, indices = mesh_data(mesh)
                    wide = len(coords) // 3 >= 65536
                    item["faces"].append({"name": "Mesh", "positions": binary("f", world_coordinates(coords, transform)),
                                          "wide": wide, "indices": binary("I" if wide else "H", indices)})
                except Exception as error:
                    item["warnings"].append({"object": name, "label": self.label(mesh), "element": "Mesh",
                                             "operation": "scene", "error": str(error)})
                self.cache[key] = item
                stats["tessellated"] += 1
            else:
                stats["cached"] += 1
            objects.append(self.cache[key])
            warnings.extend(self.cache[key]["warnings"])
        live = {o["key"] for o in objects}
        self.cache = {k: v for k, v in self.cache.items() if k in live}
        box = {"min": [min(b["min"][i] for b in bounds) for i in range(3)],
               "max": [max(b["max"][i] for b in bounds) for i in range(3)]} if bounds else None
        return {"doc": context["document_id"], "objects": objects, "bbox": box,
                "selection": self.selection(), "stats": stats, "context": context, "warnings": warnings}

    def selection(self):
        result = []
        for selected in self.app.userInterface.activeSelections:
            entity = selected.entity
            if self.is_mesh(entity):
                result.append({"object": self._body_id(entity), "label": self.label(entity), "sub": ""})
                continue
            body = self.fusion.BRepBody.cast(entity) or getattr(entity, "body", None)
            if body is None:
                continue
            name = self._remember(body)
            sub = ""
            for alias, token in self.refs[name].items():
                matches = self.design().findEntityByToken(token)
                if len(matches) == 1 and native(matches[0]) == native(entity):
                    sub = alias
                    break
            result.append({"object": name, "label": self.label(body), "sub": sub})
        return result

    def get_selection(self):
        return {"selection": self.selection()}

    def set_selection(self, object=None, sub="", additive=False):
        selections = self.app.userInterface.activeSelections
        entity = None
        if object:
            body = self.body(object, mesh_ok=True)
            entity = self.element(body, sub) if sub and not self.is_mesh(body) else body
        if not additive:
            selections.clear()
        if entity is not None:
            selections.add(entity)
        self.state["sel"] += 1
        return self.selection()

    def _marker_key(self):
        os.makedirs(self.info_dir, exist_ok=True)
        path = os.path.join(self.info_dir, "marker-key")
        if not os.path.exists(path):
            private_write(path, secrets.token_hex(32))
        with open(path, encoding="utf-8") as f:
            return f.read().encode()

    def _signature(self, marker):
        payload = {k: v for k, v in marker.items() if k not in ("sig", "trusted", "stale")}
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        return hmac.new(self._marker_key(), raw, hashlib.sha256).hexdigest()

    def _load_markers(self):
        attribute = self.app.activeDocument.attributes.itemByName("CadAI", "markers")
        return json.loads(attribute.value) if attribute else []

    def _save_markers(self, items):
        self.app.activeDocument.attributes.add("CadAI", "markers", json.dumps(items, ensure_ascii=False))
        self.state["mk"] += 1

    def markers(self):
        if self.app.activeDocument is None:
            return []
        self.context()
        items = []
        for marker in self._load_markers():
            trusted = isinstance(marker.get("sig"), str) and hmac.compare_digest(marker["sig"], self._signature(marker))
            items.append(dict(marker, trusted=trusted, stale=self._marker_stale(marker)))
        return items

    @staticmethod
    def _marker_objects(marker):
        return sorted({v["object"] for v in marker.values() if isinstance(v, dict) and isinstance(v.get("object"), str)})

    def _geometry_key(self, name):
        """Changes only when the marked body's own geometry or placement changes; None if it no longer exists.
        Document IDs and revision counters restart with every Fusion session, so they cannot decide staleness."""
        try:
            body = self.body(name, mesh_ok=True)
        except Exception:
            return None
        if self.is_mesh(body):
            return f"mesh|{bbox(body)}|{placement(body)}"
        return f"brep|{native(body).revisionId}|{placement(body)}"

    def _marker_stale(self, marker):
        objects = self._marker_objects(marker)
        saved = marker.get("geometry")
        if isinstance(saved, dict):
            return any(saved.get(name) != self._geometry_key(name) for name in objects) or set(saved) != set(objects)
        # Markers from <= 0.19.0 carry no geometry key: stale only when the marked body is gone.
        return any(self._geometry_key(name) is None for name in objects)

    def get_markers(self):
        return {"markers": self.markers()}

    def add_marker(self, marker):
        items = self._load_markers()
        marker = {k: v for k, v in marker.items() if k not in ("id", "sig", "trusted", "stale")}
        marker.update(id=max([m["id"] for m in items] + [0]) + 1, revision=self.context()["revision"],
                      document_id=self.context()["document_id"])
        marker.setdefault("kind", "point")
        marker.setdefault("note", "")
        marker["geometry"] = {name: self._geometry_key(name) for name in self._marker_objects(marker)}
        marker["sig"] = self._signature(marker)
        items.append(marker)
        self._save_markers(items)
        return dict(marker, trusted=True, stale=False)

    def update_marker(self, id, note):
        items = self._load_markers()
        for marker in items:
            if marker["id"] == id:
                marker["note"] = note
                marker["sig"] = self._signature(marker)
                self._save_markers(items)
                return next(m for m in self.markers() if m["id"] == id)
        raise ValueError("İşaret bulunamadı.")

    def delete_marker(self, id):
        self._save_markers([m for m in self._load_markers() if m["id"] != id])
        return self.markers()

    def clear_markers(self):
        self._save_markers([])
        return []

    def new_document(self, name="Model"):
        doc = self.app.documents.add(self.core.DocumentTypes.FusionDesignDocumentType)
        doc.name = name
        self.created.append(doc)
        return self.context()

    def close_document(self, name):
        """Close without saving, only a never-saved document CadAI created in this session (tests, scratch work)."""
        for doc, identity in self.docs:
            if identity == name:
                if not any(doc == created for created in self.created if created.isValid) or doc.isSaved:
                    raise ValueError("Yalnızca CadAI'nin bu oturumda açtığı, kaydedilmemiş belge kapatılabilir.")
                doc.close(False)
                return self.context()
        raise ValueError("Fusion belgesi bulunamadı.")

    def _command(self, command_id):
        definition = self.app.userInterface.commandDefinitions.itemById(command_id)
        if definition is None:
            raise ValueError(f"Fusion komutu bulunamadı: {command_id}")
        before = self.fingerprint
        definition.execute()
        # Fusion runs commands after the current event handler returns; pump events until the model changes so
        # the reply describes the model after undo/redo, not before.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.do_events()
            if self.context() and self.fingerprint != before:
                break
        else:
            raise ValueError(f"{command_id} modeli değiştirmedi; geri alınacak/yinelenecek işlem olmayabilir.")
        return self.context()

    def undo(self):
        return self._command("UndoCommand")

    def redo(self):
        return self._command("RedoCommand")

    def activate_document(self, name):
        for doc, identity in self.docs:
            if identity == name:
                doc.activate()
                return self.context()
        raise ValueError("Fusion belgesi bulunamadı.")

    def set_visibility(self, name, visible):
        if name == "Parameters":
            raise ValueError("Parametreler bir gövde değil; gizlenemez.")
        body = self.body(name, mesh_ok=True)
        occurrence = getattr(body, "assemblyContext", None)
        (occurrence or body).isLightBulbOn = bool(visible)
        return {"visible": bool(visible), "scope": "occurrence" if occurrence else "body"}

    def recompute(self):
        return {"ok": self.design().computeAll()}

    def save_document(self, path=None):
        if path:
            return self.export_model(path)
        doc = self.app.activeDocument
        if not doc.isSaved:
            raise ValueError("Yeni Fusion belgesini önce Fusion arayüzünden bir proje klasörüne kaydedin.")
        if not doc.save("CadAI"):
            raise ValueError("Fusion belgeyi kaydedemedi.")
        return {"file": doc.name}
