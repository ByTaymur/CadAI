"""Design-for-manufacturing checks measured on the exact B-rep: FDM 3D printing, 3-axis CNC milling, injection
molding and sheet metal. Every finding carries the measured value, the limit, the faces/edges it concerns and the
rule it is based on, so the agent (and the user) can see why something is flagged.

Geometry comes from FreeCAD/OCCT; wall thickness and undercuts are measured by ray casting (vectorized
Möller–Trumbore in numpy, which FreeCAD bundles) against a triangulation where every triangle knows its face.
"""

import math

import FreeCAD

from . import Tool, ToolError
from .geometry import bbox, get_object, get_shape, r

PROCESSES = {
    "fdm": {"label": "FDM 3B baskı", "min_wall_mm": 0.8, "overhang_deg": 45.0, "build_volume_mm": [256, 256, 256],
            "max_bridge_hole_mm": 6.0},
    "cnc": {"label": "3 eksen CNC freze", "min_wall_mm": 0.8, "min_tool_radius_mm": 1.0, "max_hole_depth_ratio": 4.0},
    "injection_molding": {"label": "Plastik enjeksiyon", "min_wall_mm": 0.8, "max_wall_mm": 4.0, "min_draft_deg": 1.0,
                          "max_wall_ratio": 2.0},
    "sheet_metal": {"label": "Sac metal", "max_thickness_cv_pct": 5.0},
}

RULES = {
    "build_volume": "Parça yazıcının baskı hacmine sığmalı (varsayılan 256×256×256 mm, Bambu Lab X1/P1 sınıfı).",
    "overhang": "FDM'de dikeyden 45°'den fazla eğik, aşağı bakan yüzeyler destek ister (yaygın 45° kuralı).",
    "bridge_hole": "Yatay eksenli büyük deliklerin tavanı sarkar; ~Ø6 mm üstünde damla (teardrop) profil ya da destek "
                   "gerekir.",
    "min_wall": "Duvar, sürecin en küçük güvenilir kalınlığından ince olmamalı (FDM: 2 çevre × 0,4 mm nozul; "
                "CNC metal ve enjeksiyon için tipik alt sınır ~0,8 mm).",
    "internal_corner": "Döner kesici (parmak freze) iç köşeyi keskin kesemez; iç köşeye takım yarıçapından büyük "
                       "radyus ver.",
    "small_internal_radius": "Takım yarıçapından küçük iç radyus standart parmak frezeyle işlenemez.",
    "hole_depth": "Derinlik/çap oranı 4'ü aşan delikler kademeli (gagalama) delme ya da özel matkap ister; 10'un üstü "
                  "çok zordur.",
    "second_setup": "Takım eksenine ters bakan yüzeyler aynı bağlamada işlenemez; parçanın çevrilmesi (ikinci bağlama) "
                    "gerekir.",
    "undercut": "Açılma/takım yönünde önü kapalı yüzeyler alttan kesmedir (undercut): enjeksiyonda yan maça, "
                "CNC'de T-kesici ya da 5 eksen gerekir.",
    "draft": "Kalıp açılma yönüne paralel duvarlarda en az ~1° koniklik (draft) olmalı; yoksa parça kalıptan zor "
             "çıkar ve iz kalır.",
    "max_wall": "Plastik enjeksiyonda kalın duvarlar çökme izi (sink) ve uzun soğuma süresi yapar (tipik üst sınır "
                "~4 mm).",
    "wall_uniformity": "Duvar kalınlığı düzgün olmalı; en kalın/en ince oranı 2'yi aşarsa çarpılma ve çökme riski artar.",
    "sheet_thickness": "Sac parçanın her yerinde kalınlık aynı olmalı (tek sac).",
    "bend_radius": "Bükümde iç yarıçap en az sac kalınlığı kadar olmalı (R ≥ t), yoksa çatlama riski.",
    "sheet_hole": "Sac parçada delik çapı en az sac kalınlığı kadar olmalı (Ø ≥ t), yoksa zımbalanamaz.",
}


# ---------------- triangulation with face ids + ray casting ----------------

class Soup:
    """All triangles of a shape with outward normals, areas, centroids and the index of their B-rep face."""

    def __init__(self, shape, tol):
        import numpy as np

        self.np = np
        v0, v1, v2, fid = [], [], [], []
        for fi, face in enumerate(shape.Faces, 1):
            try:
                pts, tris = face.tessellate(tol)
            except Exception:
                continue
            if not tris:
                continue
            P = np.array([[p.x, p.y, p.z] for p in pts], dtype=float)
            T = np.array(tris, dtype=int)
            a, b, c = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
            cr = np.cross(b - a, c - a)
            k = int(np.argmax(np.einsum("ij,ij->i", cr, cr)))
            nf = _normal_at(face, FreeCAD.Vector(*((a[k] + b[k] + c[k]) / 3)))
            if nf is not None and float(np.dot(cr[k], [nf.x, nf.y, nf.z])) < 0:
                b, c = c, b  # orient this face's triangles outward
            v0.append(a), v1.append(b), v2.append(c), fid.append(np.full(len(T), fi))
        if not v0:
            raise ToolError("Şekil üçgenlenemedi.")
        self.v0, v1, v2 = np.concatenate(v0), np.concatenate(v1), np.concatenate(v2)
        self.face = np.concatenate(fid)
        self.e1, self.e2 = v1 - self.v0, v2 - self.v0
        cr = np.cross(self.e1, self.e2)
        norm = np.linalg.norm(cr, axis=1)
        self.area = norm / 2
        self.normal = cr / np.maximum(norm, 1e-300)[:, None]
        self.center = (self.v0 + v1 + v2) / 3

    def cast(self, origins, dirs, t_min):
        """Distance to the first triangle hit by each ray (inf when nothing is hit)."""
        np = self.np
        out = np.full(len(origins), np.inf)
        n = len(self.v0)
        chunk = max(1, 1_500_000 // max(n, 1))
        for s in range(0, len(origins), chunk):
            o, d = origins[s:s + chunk, None, :], dirs[s:s + chunk, None, :]
            pvec = np.cross(d, self.e2[None])
            det = np.einsum("rnk,nk->rn", pvec, self.e1)
            ok = np.abs(det) > 1e-14
            inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
            tvec = o - self.v0[None]
            u = np.einsum("rnk,rnk->rn", tvec, pvec) * inv
            qvec = np.cross(tvec, self.e1[None])
            v = np.einsum("rnk,rnk->rn", np.broadcast_to(d, qvec.shape), qvec) * inv
            t = np.einsum("nk,rnk->rn", self.e2, qvec) * inv
            hit = ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9) & (t > t_min)
            out[s:s + chunk] = np.where(hit, t, np.inf).min(axis=1)
        return out

    def samples(self, per_face=30):
        """Indices of up to per_face triangles per face, spread over the face (largest first when few)."""
        np = self.np
        idx = []
        for fi in np.unique(self.face):
            tri = np.nonzero(self.face == fi)[0]
            if len(tri) > per_face:
                tri = tri[np.linspace(0, len(tri) - 1, per_face).round().astype(int)]
            idx.append(tri)
        return np.concatenate(idx)


def _normal_at(face, point):
    try:
        u, v = face.Surface.parameter(point)
        return face.normalAt(u, v)
    except Exception:
        return None


def _vec(d):
    v = FreeCAD.Vector(*(d or [0, 0, 1]))
    if v.Length == 0:
        raise ToolError("Yön vektörü sıfır olamaz.")
    return v.normalize()


def _faces(soup, mask, limit=12):
    np = soup.np
    ids, counts = np.unique(soup.face[mask], return_counts=True)
    order = np.argsort(-counts)
    return [f"Face{int(ids[i])}" for i in order[:limit]]


def _area_by_face(soup, mask, limit=12):
    np = soup.np
    out = {}
    for fi in np.unique(soup.face[mask]):
        out[f"Face{int(fi)}"] = r(float(soup.area[mask & (soup.face == fi)].sum()), 2)
    return dict(sorted(out.items(), key=lambda kv: -kv[1])[:limit])


def wall_thickness(soup, eps, per_face=30):
    """Thickness at sample points: distance along the inward normal to the opposite wall."""
    np = soup.np
    idx = soup.samples(per_face)
    origins = soup.center[idx]
    dist = soup.cast(origins, -soup.normal[idx], eps)
    good = np.isfinite(dist)
    return idx[good], dist[good]


def _thickness_stats(soup, eps):
    np = soup.np
    idx, t = wall_thickness(soup, eps)
    if not len(t):
        return None, idx, t
    return {"min_mm": r(float(t.min()), 3), "median_mm": r(float(np.median(t)), 3), "max_mm": r(float(t.max()), 3),
            "samples": int(len(t))}, idx, t


def _thin(soup, idx, t, limit_mm, finding):
    bad = t < limit_mm
    if bad.any():
        faces = sorted({f"Face{int(f)}" for f in soup.face[idx[bad]]}, key=lambda s: int(s[4:]))
        finding("error", "min_wall", f"En ince duvar {r(float(t[bad].min()), 3)} mm (sınır {limit_mm} mm).",
                measured_mm=r(float(t[bad].min()), 3), limit_mm=limit_mm, elements=faces[:12])


def _shadowed(soup, mask, direction, eps):
    """Triangles in mask whose way out along `direction` is blocked by the part itself."""
    np = soup.np
    idx = np.nonzero(mask)[0]
    if not len(idx):
        return np.zeros(len(soup.face), dtype=bool)
    if len(idx) > 4000:
        idx = idx[np.linspace(0, len(idx) - 1, 4000).round().astype(int)]
    d = np.asarray([direction.x, direction.y, direction.z], dtype=float)
    hit = np.isfinite(soup.cast(soup.center[idx] + soup.normal[idx] * eps, np.broadcast_to(d, (len(idx), 3)), eps))
    out = np.zeros(len(soup.face), dtype=bool)
    out[idx[hit]] = True
    return out


# ---------------- B-rep features ----------------

def _concave_cylinders(shape):
    """Cylindrical faces that curve away from the material (holes, inner fillets): (name, face, radius, axis)."""
    out = []
    for i, f in enumerate(shape.Faces, 1):
        s = f.Surface
        if s.__class__.__name__ != "Cylinder":
            continue
        u0, u1, v0, v1 = f.ParameterRange
        u, v = (u0 + u1) / 2, (v0 + v1) / 2
        p, n = f.valueAt(u, v), f.normalAt(u, v)
        axis = FreeCAD.Vector(s.Axis).normalize()
        radial = (p - s.Center) - axis * (p - s.Center).dot(axis)
        if n.dot(radial) < 0:
            out.append((f"Face{i}", f, s.Radius, axis))
    return out


def _extent_along(face, axis):
    vs = [v.Point.dot(axis) for v in face.Vertexes] or [0.0]
    b = face.BoundBox
    corners = [FreeCAD.Vector(x, y, z) for x in (b.XMin, b.XMax) for y in (b.YMin, b.YMax) for z in (b.ZMin, b.ZMax)]
    if not face.Vertexes:
        vs = [c.dot(axis) for c in corners]
    return max(vs) - min(vs)


def _concave_edge(shape, edge):
    import Part

    faces = shape.ancestorsOfType(edge, Part.Face)
    if len(faces) != 2 or any(f.Surface.__class__.__name__ != "Plane" for f in faces):
        return False
    mid = (edge.FirstParameter + edge.LastParameter) / 2
    p, tan = edge.valueAt(mid), edge.tangentAt(mid)
    n1, n2 = _normal_at(faces[0], p), _normal_at(faces[1], p)
    if n1 is None or n2 is None or abs(n1.dot(n2)) > 0.999:
        return False
    t1 = n1.cross(tan).normalize()
    step = max(edge.Length, 1.0) * 1e-3
    if faces[0].distToShape(Part.Vertex(p + t1 * step))[0] > step * 0.1:
        t1 = -t1  # make t1 point from the edge into face 1
    return n2.dot(t1) > 1e-6


# ---------------- process checks ----------------

def _fdm(shape, soup, d, opts, finding, meas, eps):
    np = soup.np
    b = shape.BoundBox
    size = sorted([b.XLength, b.YLength, b.ZLength], reverse=True)
    vol = sorted(opts["build_volume_mm"], reverse=True)
    meas["size_mm"] = [r(x, 2) for x in (b.XLength, b.YLength, b.ZLength)]
    if any(s > v for s, v in zip(size, vol)):
        finding("error", "build_volume", f"Parça {meas['size_mm']} mm, baskı hacmi {opts['build_volume_mm']} mm.",
                measured_mm=meas["size_mm"], limit_mm=opts["build_volume_mm"])
    dz = np.asarray([d.x, d.y, d.z])
    nd = soup.normal @ dz
    heights = soup.center @ dz
    on_bed = heights <= heights.min() + eps * 10
    limit = math.sin(math.radians(opts["overhang_deg"]))
    over = (-nd > limit) & ~on_bed
    meas["bed_contact_area_mm2"] = r(float(soup.area[on_bed & (nd < -0.99)].sum()), 2)
    if over.any():
        area = float(soup.area[over].sum())
        meas["overhang_area_mm2"] = r(area, 2)
        finding("warning", "overhang", f"{r(area, 1)} mm² yüzey dikeyden {opts['overhang_deg']}°'den fazla sarkıyor; "
                f"destek gerekir ya da parçayı çevir.", measured_mm2=r(area, 2), limit_deg=opts["overhang_deg"],
                elements=list(_area_by_face(soup, over)))
    for name, _face, radius, axis in _concave_cylinders(shape):
        if abs(axis.dot(d)) < 0.1 and 2 * radius > opts["max_bridge_hole_mm"]:
            finding("warning", "bridge_hole", f"{name}: yatay Ø{r(2 * radius, 2)} mm delik; tavanı sarkar.",
                    measured_mm=r(2 * radius, 3), limit_mm=opts["max_bridge_hole_mm"], elements=[name])
    stats, idx, t = _thickness_stats(soup, eps)
    if stats:
        meas["wall_thickness"] = stats
        _thin(soup, idx, t, opts["min_wall_mm"], finding)


def _cnc(shape, soup, d, opts, finding, meas, eps):
    np = soup.np
    dz = np.asarray([d.x, d.y, d.z])
    nd = soup.normal @ dz
    heights = soup.center @ dz
    bottom = heights <= heights.min() + eps * 10
    down = (nd < -0.02) & ~bottom
    if down.any():
        finding("warning", "second_setup", "Takım eksenine ters bakan yüzeyler var; ikinci bağlama gerekir.",
                measured_mm2=r(float(soup.area[down].sum()), 2), elements=list(_area_by_face(soup, down)))
    hidden = _shadowed(soup, nd > -0.02, d, eps)
    if hidden.any():
        finding("error", "undercut", "Üstten görünmeyen (önü kapalı) yüzeyler var: alttan kesme.",
                measured_mm2=r(float(soup.area[hidden].sum()), 2), elements=list(_area_by_face(soup, hidden)))
    sharp = []
    for i, e in enumerate(shape.Edges, 1):
        if e.Curve.__class__.__name__ == "Line" and abs(FreeCAD.Vector(e.Curve.Direction).normalize().dot(d)) > 0.99:
            if _concave_edge(shape, e):
                sharp.append(f"Edge{i}")
    if sharp:
        finding("error", "internal_corner", f"{len(sharp)} keskin iç köşe (takım eksenine paralel).",
                limit_mm=opts["min_tool_radius_mm"], elements=sharp[:20])
    holes = []
    for name, f, radius, axis in _concave_cylinders(shape):
        if abs(axis.dot(d)) < 0.99:
            continue
        depth = _extent_along(f, axis)
        if radius < opts["min_tool_radius_mm"] - 1e-9:
            finding("error", "small_internal_radius",
                    f"{name}: iç yarıçap {r(radius, 3)} mm < takım {opts['min_tool_radius_mm']} mm.",
                    measured_mm=r(radius, 3), limit_mm=opts["min_tool_radius_mm"], elements=[name])
        ratio = depth / (2 * radius)
        holes.append({"face": name, "diameter_mm": r(2 * radius, 3), "depth_mm": r(depth, 3), "depth_ratio": r(ratio, 2)})
        if ratio > opts["max_hole_depth_ratio"]:
            finding("error" if ratio > 10 else "warning", "hole_depth",
                    f"{name}: Ø{r(2 * radius, 2)} mm, derinlik {r(depth, 2)} mm (oran {r(ratio, 1)}).",
                    measured=r(ratio, 2), limit=opts["max_hole_depth_ratio"], elements=[name])
    if holes:
        meas["holes"] = holes[:20]
    stats, idx, t = _thickness_stats(soup, eps)
    if stats:
        meas["wall_thickness"] = stats
        _thin(soup, idx, t, opts["min_wall_mm"], finding)


def _molding(shape, soup, d, opts, finding, meas, eps):
    np = soup.np
    dz = np.asarray([d.x, d.y, d.z])
    nd = soup.normal @ dz
    wall = np.abs(nd) < math.sin(math.radians(opts["min_draft_deg"])) - 1e-9
    if wall.any():
        draft = np.degrees(np.arcsin(np.clip(np.abs(nd[wall]), 0, 1)))
        meas["min_draft_deg"] = r(float(draft.min()), 3)
        finding("warning", "draft", f"{r(float(soup.area[wall].sum()), 1)} mm² duvarda koniklik {opts['min_draft_deg']}°'den az.",
                measured_deg=r(float(draft.min()), 3), limit_deg=opts["min_draft_deg"],
                elements=list(_area_by_face(soup, wall)))
    up = _shadowed(soup, nd > 0.02, d, eps)
    dn = _shadowed(soup, nd < -0.02, -d, eps)
    under = up | dn
    if under.any():
        finding("warning", "undercut", "Açılma yönünde önü kapalı yüzeyler var: yan maça/itici gerekir.",
                measured_mm2=r(float(soup.area[under].sum()), 2), elements=list(_area_by_face(soup, under)))
    stats, idx, t = _thickness_stats(soup, eps)
    if stats:
        meas["wall_thickness"] = stats
        _thin(soup, idx, t, opts["min_wall_mm"], finding)
        if stats["max_mm"] > opts["max_wall_mm"]:
            thick = t > opts["max_wall_mm"]
            faces = sorted({f"Face{int(f)}" for f in soup.face[idx[thick]]}, key=lambda s: int(s[4:]))
            finding("warning", "max_wall", f"En kalın duvar {stats['max_mm']} mm (üst sınır {opts['max_wall_mm']} mm).",
                    measured_mm=stats["max_mm"], limit_mm=opts["max_wall_mm"], elements=faces[:12])
        ratio = stats["max_mm"] / max(stats["min_mm"], 1e-9)
        if ratio > opts["max_wall_ratio"]:
            finding("warning", "wall_uniformity", f"Duvar kalınlığı {stats['min_mm']}–{stats['max_mm']} mm (oran {r(ratio, 2)}).",
                    measured=r(ratio, 2), limit=opts["max_wall_ratio"])


def _sheet(shape, soup, d, opts, finding, meas, eps):
    np = soup.np
    stats, idx, t = _thickness_stats(soup, eps)
    if not stats:
        raise ToolError("Kalınlık ölçülemedi; sac parça kapalı bir katı olmalı.")
    # the sheet thickness is the most common short distance (edges of the blank give long in-plane distances)
    th = float(np.median(t[t <= np.percentile(t, 50)]))
    across = t[t <= th * 1.5]
    cv = float(np.std(across) / max(np.mean(across), 1e-12) * 100)
    meas["sheet_thickness_mm"] = r(th, 3)
    meas["thickness_cv_pct"] = r(cv, 2)
    if cv > opts["max_thickness_cv_pct"]:
        finding("error", "sheet_thickness", f"Kalınlık değişkenliği %{r(cv, 1)} (sınır %{opts['max_thickness_cv_pct']}).",
                measured_pct=r(cv, 2), limit_pct=opts["max_thickness_cv_pct"])
    for name, f, radius, axis in _concave_cylinders(shape):
        is_hole = _extent_along(f, axis) <= th * 1.2
        if is_hole and 2 * radius < th - 1e-9:
            finding("error", "sheet_hole", f"{name}: Ø{r(2 * radius, 3)} mm < sac kalınlığı {r(th, 3)} mm.",
                    measured_mm=r(2 * radius, 3), limit_mm=r(th, 3), elements=[name])
        elif not is_hole and radius < th - 1e-9:
            finding("warning", "bend_radius", f"{name}: büküm iç yarıçapı {r(radius, 3)} mm < t = {r(th, 3)} mm.",
                    measured_mm=r(radius, 3), limit_mm=r(th, 3), elements=[name])


CHECKS = {"fdm": _fdm, "cnc": _cnc, "injection_molding": _molding, "sheet_metal": _sheet}


def dfm_check(object, process, direction=None, min_wall_mm=None, max_wall_mm=None, overhang_deg=None,
              min_draft_deg=None, min_tool_radius_mm=None, build_volume_mm=None, max_hole_depth_ratio=None):
    if process not in CHECKS:
        raise ToolError(f"process: {', '.join(CHECKS)}")
    try:
        import numpy  # noqa: F401  (bundled with FreeCAD)
    except ImportError:
        raise ToolError("DFM için numpy gerekli (FreeCAD ile birlikte gelir).")
    obj = get_object(object)
    shape = get_shape(obj)
    if not shape.Solids:
        raise ToolError(f"{obj.Name} katı (solid) değil; DFM için kapalı bir katı gerekir.")
    opts = dict(PROCESSES[process])
    for key, value in (("min_wall_mm", min_wall_mm), ("max_wall_mm", max_wall_mm), ("overhang_deg", overhang_deg),
                       ("min_draft_deg", min_draft_deg), ("min_tool_radius_mm", min_tool_radius_mm),
                       ("build_volume_mm", build_volume_mm), ("max_hole_depth_ratio", max_hole_depth_ratio)):
        if value is not None:
            opts[key] = value
    d = _vec(direction)
    diag = shape.BoundBox.DiagonalLength
    tol = max(min(diag * 0.002, opts.get("min_wall_mm", 1.0) / 4), 0.005)
    soup = Soup(shape, tol)
    while len(soup.v0) > 60000 and tol < diag * 0.05:
        tol *= 2
        soup = Soup(shape, tol)
    eps = max(diag * 1e-6, 1e-6)
    findings, meas = [], {}

    def finding(severity, rule, message, **evidence):
        findings.append(dict({"severity": severity, "rule": rule, "message": message, "basis": RULES[rule]}, **evidence))

    CHECKS[process](shape, soup, d, opts, finding, meas, eps)
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    findings.sort(key=lambda f: {"error": 0, "warning": 1}.get(f["severity"], 2))
    return {"object": obj.Name, "process": process, "process_label": opts.pop("label"), "direction": [r(x, 4) for x in d],
            "verdict": "üretilebilir" if not errors and not warnings else
                       ("üretilebilir, dikkat" if not errors else "bu hâliyle üretilemez"),
            "errors": errors, "warnings": warnings, "findings": findings, "measurements": meas,
            "settings": opts, "bbox": bbox(shape),
            "method": f"B-rep yüzleri ~{r(tol, 4)} mm toleransla {len(soup.v0)} üçgene bölündü; kalınlık ve alttan "
                      "kesme ışın izlemeyle ölçüldü. Sınırlar tipik değerlerdir; üreticinin kurallarıyla teyit et."}


TOOLS = [
    Tool("dfm_check",
         "Design-for-manufacturing check measured on the exact geometry, with evidence for every finding (measured "
         "value, limit, faces/edges, rule). process: 'fdm' (3D printing: build volume, overhangs, horizontal holes, "
         "thin walls), 'cnc' (3-axis milling from +direction: sharp internal corners, small internal radii, deep "
         "holes, second setups, undercuts, thin walls), 'injection_molding' (draft, undercuts, wall thickness and "
         "uniformity), 'sheet_metal' (uniform thickness, bend radius ≥ t, hole Ø ≥ t). direction = build / tool / "
         "mold pull direction, default [0,0,1]. Limits can be overridden.",
         {"type": "object", "properties": {
             "object": {"type": "string"},
             "process": {"type": "string", "enum": list(CHECKS)},
             "direction": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
             "min_wall_mm": {"type": "number"}, "max_wall_mm": {"type": "number"},
             "overhang_deg": {"type": "number"}, "min_draft_deg": {"type": "number"},
             "min_tool_radius_mm": {"type": "number"}, "max_hole_depth_ratio": {"type": "number"},
             "build_volume_mm": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}},
          "required": ["object", "process"]},
         dfm_check, title="Manufacturability check (DFM)", example={"object": "Plate", "process": "fdm"}),
]
