"""Off-the-shelf parts from step.parts (https://www.step.parts, MIT catalog, 16 000+ STEP models): screws, nuts,
washers, bearings, profiles, motors, connectors... The agent searches the catalog and inserts a real STEP part
instead of modeling a standard component by hand.

Every download is checked: HTTPS to known hosts only (also after redirects), a size limit, and the SHA-256 the
catalog publishes. Verified files are cached in <FreeCAD user dir>/CadAI/parts/.
"""

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from . import Tool, ToolError
from .geometry import active_doc, bbox, r

API = "https://api.step.parts/v1"
ALLOWED_HOSTS = ("api.step.parts", "www.step.parts", "media.githubusercontent.com", "raw.githubusercontent.com",
                 "objects.githubusercontent.com", "github.com")
MAX_STEP_BYTES = 60 * 1024 * 1024
TIMEOUT_S = 30
PART_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,200}$")
USER_AGENT = "CadAI-FreeCAD (+https://github.com/taymur/CadAI)"


def _allowed(url):
    u = urllib.parse.urlsplit(url)
    return u.scheme == "https" and (u.hostname or "").lower() in ALLOWED_HOSTS


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed(newurl):
            raise ToolError(f"step.parts izin verilmeyen bir adrese yönlendirdi: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_CheckedRedirect)


def _get(url, limit, accept="application/json"):
    if not _allowed(url):
        raise ToolError(f"İzin verilmeyen adres: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with _opener.open(req, timeout=TIMEOUT_S) as resp:
            data = resp.read(limit + 1)
    except urllib.error.HTTPError as e:
        raise ToolError(f"step.parts HTTP {e.code}: {url}")
    except urllib.error.URLError as e:
        raise ToolError(f"step.parts'a ulaşılamadı ({getattr(e, 'reason', e)}). İnternet bağlantısı var mı?")
    if len(data) > limit:
        raise ToolError(f"Yanıt çok büyük (>{limit // (1024 * 1024)} MB): {url}")
    return data


def _api(path, params=None):
    query = urllib.parse.urlencode([(k, v) for k, v in (params or {}).items() if v not in (None, "")], doseq=True)
    try:
        return json.loads(_get(API + path + ("?" + query if query else ""), 4 * 1024 * 1024).decode("utf-8"))
    except ValueError:
        raise ToolError("step.parts geçersiz yanıt döndürdü.")


def _summary(item):
    return {"id": item.get("id"), "name": item.get("name"), "category": item.get("category"),
            "family": item.get("family"), "standard": (item.get("standard") or {}).get("designation"),
            "attributes": item.get("attributes") or {}, "size_kb": round((item.get("byteSize") or 0) / 1024, 1),
            "page": item.get("pageUrl")}


def search_parts(query, category=None, family=None, standard=None, tags=None, limit=10):
    limit = max(1, min(int(limit or 10), 30))
    data = _api("/parts", {"q": query, "category": category, "family": family, "standard": standard,
                           "tag": tags or None, "pageSize": limit})
    items = data.get("items") or []
    out = {"query": query, "count": len(items), "parts": [_summary(i) for i in items],
           "catalog_parts": (data.get("catalog") or {}).get("partCount"),
           "next": "insert_part(part_id=...) ile modele ekle."}
    if not items:
        out["hint"] = "Sonuç yok. Daha genel arayın (ör. 'M8 screw', 'bearing 608', '2020 extrusion') ya da category kullanın."
    return out


def _cache_dir():
    from .. import config

    path = os.path.join(os.path.dirname(config.config_path()), "parts")
    os.makedirs(path, exist_ok=True)
    return path


def fetch_step(part_id):
    """Download (or reuse from cache) the verified STEP file of a catalog part. Returns (path, catalog item)."""
    if not isinstance(part_id, str) or not PART_ID.match(part_id):
        raise ToolError(f"Geçersiz parça kimliği: {part_id!r}. search_parts sonucundaki 'id' değerini kullan.")
    item = _api("/parts/" + urllib.parse.quote(part_id))
    url, digest, size = item.get("stepUrl"), (item.get("sha256") or "").lower(), item.get("byteSize") or 0
    if not url or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ToolError(f"{part_id}: katalogda STEP adresi ya da SHA-256 yok.")
    if size > MAX_STEP_BYTES:
        raise ToolError(f"{part_id}: dosya çok büyük ({size // (1024 * 1024)} MB).")
    path = os.path.join(_cache_dir(), digest + ".step")
    if os.path.isfile(path):
        with open(path, "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() == digest:
                return path, item
    data = _get(url, MAX_STEP_BYTES, accept="application/step, */*")
    if hashlib.sha256(data).hexdigest() != digest:
        raise ToolError(f"{part_id}: indirilen dosyanın SHA-256 özeti katalogla uyuşmuyor; dosya kullanılmadı.")
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return path, item


def insert_part(part_id, position=None, rotation_axis=None, rotation_deg=0.0, name=None):
    import FreeCAD
    import Part

    from .model_tools import transaction

    path, item = fetch_step(part_id)
    shape = Part.Shape()
    shape.read(path)
    if shape.isNull():
        raise ToolError(f"{part_id}: STEP dosyası okunamadı.")
    doc = active_doc(create=True)
    label = name or (item.get("name") or part_id)
    with transaction(doc, f"CadAI: {label} ekle"):
        obj = doc.addObject("Part::Feature", re.sub(r"\W+", "_", name or part_id)[:40] or "Part")
        obj.Shape = shape
        obj.Label = label
        rot = FreeCAD.Rotation()
        if rotation_axis and rotation_deg:
            rot = FreeCAD.Rotation(FreeCAD.Vector(*rotation_axis), float(rotation_deg))
        obj.Placement = FreeCAD.Placement(FreeCAD.Vector(*(position or [0, 0, 0])), rot)
        obj.addProperty("App::PropertyString", "StepPartsId", "CadAI", "step.parts catalog id")
        obj.addProperty("App::PropertyString", "StepPartsUrl", "CadAI", "step.parts page (source and license)")
        obj.StepPartsId = part_id
        obj.StepPartsUrl = item.get("pageUrl") or ""
        doc.recompute()
    return {"ok": True, "object": obj.Name, "label": obj.Label, "part": _summary(item), "bbox": bbox(obj.Shape),
            "volume_mm3": r(obj.Shape.Volume, 3) if obj.Shape.Solids else None,
            "note": "Konum/yön için set_property ile Placement değiştirilebilir. Lisans ve kaynak: 'page' adresi."}


TOOLS = [
    Tool("search_parts",
         "Search step.parts, an open catalog of 16 000+ ready STEP models of standard parts (ISO/DIN screws, nuts, "
         "washers, bearings, aluminium extrusions, motors, connectors, boards...). Use it instead of modeling a "
         "standard component. Examples: query='M8 socket head', standard='ISO 4762'; query='608 bearing'; "
         "query='2020 extrusion'. Returns ids for insert_part.",
         {"type": "object", "properties": {
             "query": {"type": "string", "description": "Free text, e.g. 'M3x10 screw', 'bearing 608', 'NEMA 17'."},
             "category": {"type": "string", "description": "e.g. fastener, bearing, structural, electronics, motor"},
             "family": {"type": "string", "description": "e.g. socket-head-cap-screw, hex-nut, deep-groove-ball-bearing"},
             "standard": {"type": "string", "description": "e.g. ISO 4762, DIN 912, ISO 4032"},
             "tags": {"type": "array", "items": {"type": "string"}},
             "limit": {"type": "integer", "minimum": 1, "maximum": 30}},
          "required": ["query"]},
         search_parts, title="Search standard parts (step.parts)", open_world=True, example={"query": "M8x20 socket head"}),
    Tool("insert_part",
         "Download a part from step.parts (verified by SHA-256) and insert it into the open document as a solid at a "
         "position (mm) with an optional rotation. Use an id returned by search_parts.",
         {"type": "object", "properties": {
             "part_id": {"type": "string"},
             "position": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
             "rotation_axis": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
             "rotation_deg": {"type": "number"},
             "name": {"type": "string", "description": "Label for the new object (default: catalog name)."}},
          "required": ["part_id"]},
         insert_part, mutates=True, destructive=False, title="Insert a standard part (step.parts)", open_world=True),
]
