"""Technical drawings (A3, Turkish title block) of a FreeCAD part, straight from its geometry.

Style follows the user's drawing kit (sheet.py / render.py, "teknik resim şablonu"): A3 landscape 420×297 mm with a
10 mm frame, SolidWorks-like Turkish title block (ÇİZEN/DENET./ONAY./ÜRET./KALİTE, BAŞLIK, MALZEME, RESİM NO,
AĞIRLIK, ÖLÇEK, REVİZYON), thick lines 1.3 pt / thin 0.55 pt, filled arrow heads, decimal comma ("51,80"), 45°
section hatching, dash-dot center lines, dashed hidden edges, NOTLAR and a shaded isometric picture.

What the kit drew by hand is computed here:
  * views: exact hidden-line removal of the B-rep (TechDraw.projectEx) in first-angle (ISO-E) arrangement:
    front top-left, view from the left to its right, top view below it, isometric top-right;
  * section A-A (optional, automatic when the front view has hidden edges): the solid is cut through its middle,
    the cut face is hatched and the cutting plane is marked in the front view;
  * scale: the largest standard scale that fits; mass from the volume and the material's density;
  * dimensions: overall sizes, diameters of the holes and bosses a view shows as circles (grouped, "4x Ø5,00"),
    center lines of round features — and any dimension or note the caller adds (measured from the model, never made
    up);
  * a layout check: overlapping texts and anything outside the frame are reported, so the agent can fix them.

matplotlib runs on its own Agg canvas (never pyplot): FreeCAD's GUI and its Plot workbench keep their backend.
"""

import datetime
import itertools
import math
import os
import re

import numpy as np

W, H = 420.0, 297.0
TK, TN = 1.3, 0.55  # line widths (pt): visible edges / thin lines
FS = 8.5
CL = (0, (14, 3, 2, 3))  # center line
HD = (0, (4, 2))  # hidden edge
SCALES = [(10, 1), (5, 1), (2, 1), (1, 1), (1, 2), (1, 5), (1, 10), (1, 20), (1, 50), (1, 100)]

# view frames: (right, up); the viewer sits at right × up. First angle: view from the left goes right of the front.
VIEWS = {
    "front": ((1, 0, 0), (0, 0, 1)),  # viewer at -Y
    "top": ((1, 0, 0), (0, 1, 0)),  # viewer at +Z
    "left": ((0, -1, 0), (0, 0, 1)),  # viewer at -X ("sol görünüş")
    "section": ((0, 1, 0), (0, 0, 1)),  # viewer at +X looking at the cut, arrows in the front view point to -X
    "iso": ((1, 1, 0), (-1, 1, 2)),  # viewer at front-right-top
}
VIEW_TITLES = {"front": "ÖN GÖRÜNÜŞ", "top": "ÜST GÖRÜNÜŞ", "left": "SOL GÖRÜNÜŞ", "section": "KESİT A-A"}

# density by material name (kg/m³); the first match wins, the caller can always pass density_kg_m3
DENSITIES = [
    (r"paslanmaz|inox|stainless|\b30[14]\b|\b316", 8000), (r"titan", 4430), (r"pirin[cç]|brass|cuzn", 8500),
    (r"bronz|bronze", 8800), (r"bak[ıi]r|copper|\bcu\b", 8960), (r"magnez", 1800), (r"7075", 2810),
    (r"al[uü]m|\bal\b|\bal ?\d|60[0-9]{2}|50[0-9]{2}|aluminium|aluminum", 2700), (r"d[oö]k[uü]m|cast iron|gg ?\d|gjl", 7200),
    (r"[cç]elik|steel|st ?\d|s235|s355|c45|42crmo|1\.\d{4}", 7850), (r"\bpla\b", 1240), (r"\babs\b", 1050),
    (r"petg", 1270), (r"pom|delrin|asetal", 1410), (r"naylon|nylon|\bpa ?6", 1140), (r"pe-?hd|polietilen", 950),
    (r"\bpc\b|polikarbonat", 1200), (r"teflon|ptfe", 2200),
]


def density_for(material):
    text = str(material or "").lower()
    for pattern, rho in DENSITIES:
        if re.search(pattern, text):
            return rho
    return None


def upper_tr(text):
    """Upper case with Turkish dotted/dotless i (str.upper turns 'i' into 'I', not 'İ')."""
    return str(text).replace("i", "İ").replace("ı", "I").upper()


def fmt(v, digits=2):
    return f"{v:.{digits}f}".replace(".", ",")


def scale_text(scale):
    a, b = scale
    return f"{a}:{b}"


def parse_scale(text):
    m = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", str(text))
    if not m or not int(m.group(1)) or not int(m.group(2)):
        raise ValueError(f"Ölçek '2:1' ya da '1:5' biçiminde olmalı: {text!r}")
    return int(m.group(1)), int(m.group(2))


# ---------------- geometry ----------------

def view_matrix(name):
    import FreeCAD

    right, up = (FreeCAD.Vector(*v).normalize() for v in VIEWS[name])
    t = right.cross(up)
    return FreeCAD.Matrix(right.x, right.y, right.z, 0, up.x, up.y, up.z, 0, t.x, t.y, t.z, 0, 0, 0, 0, 1)


def to_view(name, p):
    """Model point -> (x, y) in the view's plane (model units)."""
    right, up = (np.array(v, float) / np.linalg.norm(v) for v in VIEWS[name])
    p = np.asarray(p, float)
    return float(right @ p), float(up @ p)


def _polylines(compound, tol):
    out = []
    if compound is None or compound.isNull():
        return out
    for e in compound.Edges:
        try:
            pts = e.discretize(Deflection=tol)
        except Exception:
            pts = [v.Point for v in e.Vertexes]
        if len(pts) >= 2:
            out.append(np.array([(p.x, p.y) for p in pts]))
    return out


def project(shape, name, tol):
    """Hidden-line projection of `shape` in a view: visible/hidden polylines, circles seen face-on, bounds."""
    import FreeCAD
    import TechDraw

    t = shape.copy()
    t.transformShape(view_matrix(name))
    r = TechDraw.projectEx(t, FreeCAD.Vector(0, 0, 1))
    # 0 hard, 1 smooth (tangent), 3 outline (silhouettes) visible; 5 hard and 8 outline hidden
    visible = _polylines(r[0], tol) + _polylines(r[3], tol)
    hidden = _polylines(r[5], tol) + _polylines(r[8], tol)
    circles = []
    for comp, vis in ((r[0], True), (r[5], False)):
        if comp is None or comp.isNull():
            continue
        for e in comp.Edges:
            c = e.Curve
            if type(c).__name__ == "Circle" and abs(e.LastParameter - e.FirstParameter) > math.pi * 1.5:
                circles.append({"x": c.Center.x, "y": c.Center.y, "r": c.Radius, "visible": vis})
    pts = np.vstack(visible + hidden) if visible or hidden else np.zeros((1, 2))
    return {"visible": visible, "hidden": hidden, "circles": _unique_circles(circles),
            "bounds": (pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max())}


def _unique_circles(circles):
    out = []
    for c in sorted(circles, key=lambda c: not c["visible"]):
        if not any(abs(c["x"] - o["x"]) < 1e-3 and abs(c["y"] - o["y"]) < 1e-3 and abs(c["r"] - o["r"]) < 1e-3 for o in out):
            out.append(c)
    return out


def axes_in_view(shape, name):
    """Axes of cylindrical/conical faces lying in the view plane, as center-line segments (view coordinates)."""
    right, up = (np.array(v, float) / np.linalg.norm(v) for v in VIEWS[name])
    toward = np.cross(right, up)
    segs = []
    for f in shape.Faces:
        s = f.Surface
        if type(s).__name__ not in ("Cylinder", "Cone"):
            continue
        a = np.array([s.Axis.x, s.Axis.y, s.Axis.z])
        if abs(a @ toward) > 1e-6:
            continue
        c = np.array([(s.Center if hasattr(s, "Center") else s.Apex).x, (s.Center if hasattr(s, "Center") else s.Apex).y,
                      (s.Center if hasattr(s, "Center") else s.Apex).z])
        ts = [float((np.array([v.X, v.Y, v.Z]) - c) @ a) for v in f.Vertexes] or [0.0]
        p0, p1 = c + a * min(ts), c + a * max(ts)
        seg = (to_view(name, p0), to_view(name, p1))
        if np.hypot(seg[1][0] - seg[0][0], seg[1][1] - seg[0][1]) < 1e-6:
            continue
        if not any(_same_seg(seg, o) for o in segs):
            segs.append(seg)
    return segs


def _same_seg(a, b):
    def on_line(p, s):
        (x0, y0), (x1, y1) = s
        dx, dy = x1 - x0, y1 - y0
        return abs((p[0] - x0) * dy - (p[1] - y0) * dx) / max(math.hypot(dx, dy), 1e-9) < 1e-3
    return on_line(a[0], b) and on_line(a[1], b)


def section(shape, cut_x):
    """Cut the solid at x = cut_x (plane normal X), keep x <= cut_x. -> (kept solid, cut face) in model space."""
    import FreeCAD
    import Part

    bb = shape.BoundBox
    pad = max(bb.DiagonalLength, 1.0)
    half = Part.makeBox(cut_x - bb.XMin + pad, bb.YLength + 2 * pad, bb.ZLength + 2 * pad,
                        FreeCAD.Vector(bb.XMin - pad, bb.YMin - pad, bb.ZMin - pad))
    kept = shape.common(half)
    try:
        face = Part.makeFace(shape.slice(FreeCAD.Vector(1, 0, 0), cut_x), "Part::FaceMakerBullseye")
    except Exception:
        face = None
    return kept, face


def face_rings(shape, name, tol):
    """Boundary rings of the planar faces in `shape` (a face or a compound of separate cut regions), in view
    coordinates, per face: outer counter-clockwise, holes clockwise (so a non-zero fill leaves the holes empty)."""
    out = []
    for face in shape.Faces:
        rings = []
        for w in face.Wires:
            pts = np.array([to_view(name, (p.x, p.y, p.z)) for p in w.discretize(Deflection=tol)])
            area = 0.5 * np.sum(pts[:, 0] * np.roll(pts[:, 1], -1) - np.roll(pts[:, 0], -1) * pts[:, 1])
            if (area < 0) == w.isSame(face.OuterWire):
                pts = pts[::-1]
            rings.append(pts)
        out.append(rings)
    return out


def shaded_image(shape, name="iso", px=700):
    """Shaded picture of the part (z-buffer, Lambert light) as RGBA array and its extent in view coordinates."""
    bb = shape.BoundBox
    pts, tris = shape.tessellate(max(bb.DiagonalLength / 400.0, 0.01))
    right, up = (np.array(v, float) / np.linalg.norm(v) for v in VIEWS[name])
    toward = np.cross(right, up)
    P = np.array([(p.x, p.y, p.z) for p in pts]) @ np.vstack([right, up, toward]).T
    T = np.array(tris, int)
    x0, y0 = P[:, 0].min(), P[:, 1].min()
    span = max(P[:, 0].max() - x0, P[:, 1].max() - y0, 1e-9)
    k = (px - 1) / span
    nx, ny = int((P[:, 0].max() - x0) * k) + 2, int((P[:, 1].max() - y0) * k) + 2
    sx, sy = (P[:, 0] - x0) * k, (P[:, 1] - y0) * k
    zbuf = np.full((ny, nx), -np.inf)
    shade = np.zeros((ny, nx))
    light = np.array([-0.35, 0.55, 1.0])
    light /= np.linalg.norm(light)
    a, b, c = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    n = np.cross(b - a, c - a)
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    lum = 0.45 + 0.55 * np.clip(np.abs(n @ light), 0, 1)
    for i, (ia, ib, ic) in enumerate(T):
        xs, ys, zs = sx[[ia, ib, ic]], sy[[ia, ib, ic]], P[[ia, ib, ic], 2]
        area = (xs[1] - xs[0]) * (ys[2] - ys[0]) - (xs[2] - xs[0]) * (ys[1] - ys[0])
        if abs(area) < 1e-12:
            continue
        i0, i1 = int(max(xs.min(), 0)), int(min(xs.max() + 1, nx - 1))
        j0, j1 = int(max(ys.min(), 0)), int(min(ys.max() + 1, ny - 1))
        gx, gy = np.meshgrid(np.arange(i0, i1 + 1) + 0.5, np.arange(j0, j1 + 1) + 0.5)
        w0 = ((xs[1] - gx) * (ys[2] - gy) - (xs[2] - gx) * (ys[1] - gy)) / area
        w1 = ((xs[2] - gx) * (ys[0] - gy) - (xs[0] - gx) * (ys[2] - gy)) / area
        w2 = 1 - w0 - w1
        inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not inside.any():
            continue
        z = w0 * zs[0] + w1 * zs[1] + w2 * zs[2]
        sub = zbuf[j0:j1 + 1, i0:i1 + 1]
        closer = inside & (z > sub)
        sub[closer] = z[closer]
        shade[j0:j1 + 1, i0:i1 + 1][closer] = lum[i]
    img = np.zeros((ny, nx, 4))
    hit = np.isfinite(zbuf)
    base = np.array([0.80, 0.82, 0.86])
    img[hit, :3] = base * shade[hit, None]
    img[hit, 3] = 1.0
    extent = (x0, x0 + nx / k, y0, y0 + ny / k)
    return img[::-1], extent  # row 0 at the top for imshow


# ---------------- sheet ----------------

class Sheet:
    """One A3 sheet drawn in millimetres (1 data unit = 1 mm)."""

    def __init__(self):
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from matplotlib.patches import Rectangle

        self.fig = Figure(figsize=(W / 25.4, H / 25.4))
        FigureCanvasAgg(self.fig)
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, W)
        self.ax.set_ylim(0, H)
        self.ax.set_aspect("equal")
        self.ax.axis("off")
        self.ax.add_patch(Rectangle((10, 10), W - 20, H - 20, fill=False, lw=1.6))
        self.texts = []  # texts placed on the drawing (checked for overlaps); title block texts are not
        self.keepout = []  # ((x0, y0, x1, y1), name): areas texts must not enter (the isometric picture)

    def line(self, x1, y1, x2, y2, lw=TK, ls="-", zorder=2):
        self.ax.plot([x1, x2], [y1, y2], lw=lw, ls=ls, color="k", solid_capstyle="butt", zorder=zorder)

    def polyline(self, pts, lw=TK, ls="-", zorder=2):
        self.ax.plot(pts[:, 0], pts[:, 1], lw=lw, ls=ls, color="k", solid_capstyle="butt", zorder=zorder)

    def arrow(self, x, y, dx, dy, length=3.0, width=0.55):
        from matplotlib.patches import Polygon

        n = math.hypot(dx, dy) or 1.0
        dx, dy = dx / n, dy / n
        px, py = -dy, dx
        self.ax.add_patch(Polygon([(x, y), (x - dx * length + px * width, y - dy * length + py * width),
                                   (x - dx * length - px * width, y - dy * length - py * width)],
                                  closed=True, fc="k", ec="k", lw=0.3, zorder=3))

    def text(self, x, y, s, fs=FS, rot=0, ha="center", va="center", check=True, **kw):
        t = self.ax.text(x, y, s, fontsize=fs, rotation=rot, ha=ha, va=va, zorder=4, **kw)
        if check:
            self.texts.append(t)
        return t

    def dim_h(self, x1, y1, x2, y2, yd, txt, tol=None):
        """Horizontal dimension between two points, dimension line at height yd."""
        sgn = 1 if yd > max(y1, y2) else -1
        for x, y in ((x1, y1), (x2, y2)):
            self.line(x, y + sgn * 1.0, x, yd + sgn * 1.5, TN)
        a, b = min(x1, x2), max(x1, x2)
        if b - a > 9:
            self.line(a, yd, b, yd, TN)
            self.arrow(a, yd, -1, 0)
            self.arrow(b, yd, 1, 0)
        else:
            self.line(a - 7, yd, b + 7, yd, TN)
            self.arrow(a, yd, 1, 0)
            self.arrow(b, yd, -1, 0)
        cx = (a + b) / 2
        self.text(cx, yd + 1.0, txt, va="bottom")
        if tol:
            off = len(txt) * 2.6 + 0.5
            self.text(cx + off, yd + 3.6, tol[0], fs=6, ha="left", va="bottom")
            self.text(cx + off, yd + 1.0, tol[1], fs=6, ha="left", va="bottom")

    def dim_v(self, x1, y1, x2, y2, xd, txt, tol=None):
        """Vertical dimension between two points, dimension line at x = xd."""
        sgn = 1 if xd > max(x1, x2) else -1
        for x, y in ((x1, y1), (x2, y2)):
            self.line(x + sgn * 1.0, y, xd + sgn * 1.5, y, TN)
        a, b = min(y1, y2), max(y1, y2)
        if b - a > max(9.0, len(txt) * 1.9 + 7):  # the text (beside the line) fits between the arrows
            self.line(xd, a, xd, b, TN)
            self.arrow(xd, a, 0, -1)
            self.arrow(xd, b, 0, 1)
            ty = (a + b) / 2
        else:
            # short span: arrows from outside, the text level beside the line on its outer side, so it stays
            # within the height of the dimension instead of reaching into a neighbouring view
            self.line(xd, a - 7, xd, b + 7, TN)
            self.arrow(xd, a, 0, 1)
            self.arrow(xd, b, 0, -1)
            tx, ha = xd + sgn * 1.5, "left" if sgn > 0 else "right"
            self.text(tx, (a + b) / 2, txt, ha=ha)
            if tol:
                tx += sgn * (len(txt) * 1.75 + 1)
                self.text(tx, (a + b) / 2 + 1.3, tol[0], fs=6, ha=ha, va="bottom")
                self.text(tx, (a + b) / 2 - 1.3, tol[1], fs=6, ha=ha, va="top")
            return
        self.text(xd - 1.0, ty, txt, rot=90, va="bottom")
        if tol:
            off = len(txt) * 2.6 + 0.5
            self.text(xd - 3.6, ty + off, tol[0], fs=6, rot=90, ha="left", va="bottom")
            self.text(xd - 1.0, ty + off, tol[1], fs=6, rot=90, ha="left", va="bottom")

    def dim_aligned(self, x1, y1, x2, y2, offset, txt):
        d = np.array([x2 - x1, y2 - y1], float)
        n = np.linalg.norm(d) or 1.0
        u = d / n
        nrm = np.array([-u[1], u[0]]) * offset
        a, b = np.array([x1, y1]) + nrm, np.array([x2, y2]) + nrm
        for p, q in ((np.array([x1, y1]), a), (np.array([x2, y2]), b)):
            e = q + np.sign(offset) * 1.5 * np.array([-u[1], u[0]])
            self.line(*p, *e, TN)
        self.line(*a, *b, TN)
        self.arrow(*a, *(-u))
        self.arrow(*b, *u)
        ang = math.degrees(math.atan2(u[1], u[0]))
        if ang > 90 or ang < -90:
            ang += 180
        m = (a + b) / 2 + np.array([-u[1], u[0]]) * 1.0 * np.sign(offset or 1)
        self.text(m[0], m[1], txt, rot=ang, va="bottom" if offset >= 0 else "top")

    def leader(self, px, py, ex, ey, txt, fs=7.5, right=True):
        """Leader from point (px, py) to (ex, ey) with a shelf carrying the text."""
        self.line(px, py, ex, ey, TN)
        self.arrow(px, py, px - ex, py - ey, length=2.5, width=0.5)
        width = len(txt) * fs * 0.21 + 2
        shelf = width if right else -width
        self.line(ex, ey, ex + shelf, ey, TN)
        self.text(ex + 1 if right else ex + shelf + 1, ey + 1, txt, fs=fs, ha="left", va="bottom")

    def hatch(self, rings, zorder=1):
        from matplotlib.patches import PathPatch
        from matplotlib.path import Path

        verts, codes = [], []
        for r in rings:
            verts += [tuple(p) for p in r] + [tuple(r[0])]
            codes += [Path.MOVETO] + [Path.LINETO] * (len(r) - 1) + [Path.CLOSEPOLY]
        self.ax.add_patch(PathPatch(Path(verts, codes), fill=False, hatch="////", ec="k", lw=0, zorder=zorder))

    def notes(self, lines, x=200, y=138):
        self.text(x, y, "NOTLAR:", fs=8.5, ha="left", weight="bold")
        for i, n in enumerate(lines):
            self.text(x, y - 7 - i * 6.5, f"{i + 1}. {n}", fs=7.5, ha="left")

    def title_block(self, baslik, resim_no, malzeme_kisa, malzeme_uzun, rev, olcek, agirlik_g, miktar, tarih, cizen=""):
        """The kit's title block (sheet.title_block), unchanged in layout."""
        from matplotlib.patches import Rectangle

        T = lambda *a, **k: self.text(*a, check=False, **k)  # noqa: E731
        L = self.line
        T(262, 79, "MİKTAR: " + miktar, fs=9, ha="left")
        T(310, 79, "MALZEME: " + malzeme_uzun, fs=11, ha="left", weight="bold")
        tbx, tby, tbw, tbh = 200, 10, 210, 62
        self.ax.add_patch(Rectangle((tbx, tby), tbw, tbh, fill=False, lw=TK))
        cols = [tbx, tbx + 16, tbx + 34, tbx + 46, tbx + 60]
        rows = [tby + tbh - 16] + [tby + tbh - 16 - 7.2 * i for i in range(1, 7)]
        L(tbx, rows[0], tbx + 60, rows[0], TN)
        T(tbx + 1.5, tby + tbh - 2,
          "AKSİ BELİRTİLMEDİKÇE:\nBOYUTLAR MİLİMETREDİR\nYÜZEY CİLASI:\nTOLERANSLAR:  DOĞRUSAL:   AÇISAL:",
          fs=4.3, ha="left", va="top")
        T(tbx + 42, tby + tbh - 2, "BİTİRME:", fs=4.3, ha="left", va="top")
        T(tbx + 62, tby + tbh - 2, "KESKİN KENARLARI\nPAHLAYIN VE KIRIN", fs=4.3, ha="left", va="top")
        L(tbx + 40, tby + tbh, tbx + 40, rows[0], TN)
        L(tbx + 60, tby + tbh, tbx + 60, tby, TN)
        labels = ["", "ÇİZEN", "DENET.", "ONAY.", "ÜRET.", "KALİTE"]
        for i in range(1, 6):
            L(tbx, rows[i], tbx + 60, rows[i], TN)
        for c in cols[1:4]:
            L(c, rows[0], c, tby, TN)
        for i, h in enumerate(["İSİM", "İMZA", "TARİH"]):
            T((cols[i + 1] + cols[i + 2]) / 2, (rows[0] + rows[1]) / 2, h, fs=4.8)
        for i, lb in enumerate(labels[1:], 1):
            T(tbx + 1.5, (rows[i] + rows[i + 1]) / 2 if i < 5 else (rows[5] + tby) / 2, lb, fs=4.8, ha="left")
        if cizen:
            T((cols[1] + cols[2]) / 2, (rows[1] + rows[2]) / 2, cizen[:14], fs=4.3)
        T((cols[3] + cols[4]) / 2, (rows[1] + rows[2]) / 2, tarih, fs=4.3)
        L(tbx + 60, tby + tbh - 16, tbx + tbw, tby + tbh - 16, TN)
        T(tbx + 100, tby + tbh - 8, "TEKNİK RESMİ ÖLÇEKLENDİRMEYİN", fs=5)
        L(tbx + 140, tby + tbh, tbx + 140, tby + tbh - 16, TN)
        T(tbx + 142, tby + tbh - 4, "REVİZYON", fs=4.8, ha="left", va="top")
        T(tbx + 175, tby + tbh - 10, "REV. " + str(rev), fs=9, weight="bold")
        L(tbx + 60, tby + 28, tbx + tbw, tby + 28, TN)
        T(tbx + 62, tby + tbh - 19, "BAŞLIK:", fs=4.8, ha="left", va="top")
        T(tbx + 135, tby + 36, baslik, fs=15 if len(baslik) <= 18 else 11, weight="bold")
        T(tbx + 62, tby + 26, "MALZEME:", fs=4.8, ha="left", va="top")
        T(tbx + 85, tby + 18, malzeme_kisa, fs=9 if len(malzeme_kisa) <= 12 else 7)
        L(tbx + 110, tby + 28, tbx + 110, tby, TN)
        T(tbx + 112, tby + 26, "RESİM NO.", fs=4.8, ha="left", va="top")
        T(tbx + 150, tby + 15, resim_no, fs=14 if len(resim_no) <= 10 else 9)
        L(tbx + 190, tby + 28, tbx + 190, tby + 8, TN)
        T(tbx + 200, tby + 18, "A3", fs=11)
        L(tbx + 60, tby + 8, tbx + tbw, tby + 8, TN)
        T(tbx + 62, tby + 4, f"AĞIRLIK: ~{agirlik_g:.0f} g" if agirlik_g is not None else "AĞIRLIK: —", fs=5.5, ha="left")
        T(tbx + 112, tby + 4, "ÖLÇEK: " + olcek, fs=5.5, ha="left")
        T(tbx + 170, tby + 4, "SAYFA 1 / 1", fs=5.5, ha="left")

    def check_layout(self):
        """Overlapping drawing texts, texts crossing the frame or the title block: [message]."""
        self.fig.canvas.draw()
        rend = self.fig.canvas.get_renderer()
        to_mm = self.ax.transData.inverted()
        boxes = []
        for t in self.texts:
            if not t.get_text().strip():
                continue
            bb = t.get_window_extent(rend)
            (x0, y0), (x1, y1) = to_mm.transform([(bb.x0, bb.y0), (bb.x1, bb.y1)])
            boxes.append((t.get_text().replace("\n", " "), (x0, y0, x1, y1)))
        out = []
        for i, (a, ba) in enumerate(boxes):
            if ba[0] < 10 or ba[1] < 10 or ba[2] > W - 10 or ba[3] > H - 10:
                out.append(f"'{a}' çerçevenin dışına taşıyor")
            if ba[2] > 200.5 and ba[1] < 85.5 and not a.startswith("NOTLAR"):
                out.append(f"'{a}' antet bölgesine giriyor")
            for (k0, k1, k2, k3), what in self.keepout:
                if min(ba[2], k2) - max(ba[0], k0) > 0.3 and min(ba[3], k3) - max(ba[1], k1) > 0.3:
                    out.append(f"'{a}' {what} üstüne biniyor")
            for b, bb2 in boxes[i + 1:]:
                if min(ba[2], bb2[2]) - max(ba[0], bb2[0]) > 0.3 and min(ba[3], bb2[3]) - max(ba[1], bb2[1]) > 0.3:
                    out.append(f"'{a}' ile '{b}' üst üste")
        return out

    def save(self, pdf=None, png=None, dpi=200):
        if pdf:
            self.fig.savefig(pdf, format="pdf")
        if png:
            self.fig.savefig(png, format="png", dpi=dpi, facecolor="white")


# ---------------- the drawing ----------------

class ViewPlacement:
    """A view on the sheet: model view coordinates -> sheet mm."""

    def __init__(self, name, proj, scale, cx, cy):
        self.name, self.proj, self.s = name, proj, scale
        x0, y0, x1, y1 = proj["bounds"]
        self.mx, self.my = (x0 + x1) / 2, (y0 + y1) / 2
        self.cx, self.cy = cx, cy
        # distance (mm) from the view's box to its next free dimension line on each side; dimensions stack outward
        self.slot = {"below": 9.0, "above": 8.0, "left": 9.0, "right": 10.0}

    def take(self, side, extra=0.0):
        d = self.slot[side] + extra
        self.slot[side] = d + 9.0
        return d

    def P(self, x, y):
        return self.cx + self.s * (x - self.mx), self.cy + self.s * (y - self.my)

    def box(self):
        x0, y0, x1, y1 = self.proj["bounds"]
        (a, b), (c, d) = self.P(x0, y0), self.P(x1, y1)
        return a, b, c, d


def choose_scale(fits):
    for s in SCALES:
        if fits(s[0] / s[1]):
            return s
    return SCALES[-1]


# sheet zones (mm): drawing area inside the 10 mm frame; title block + MİKTAR line up to y 85 right of x 198;
# NOTLAR from y 140 down, right of x 198; isometric picture in the top-right corner (x .. 405, y 186..282)
LEFT, TOP, BOTTOM = 34.0, 268.0, 15.0  # BOTTOM: center lines below the lowest view
TB_X, NOTES_Y = 198.0, 146.0
ISO_X0, ISO_X1, ISO_Y0, ISO_Y1 = 338.0, 405.0, 186.0, 282.0
ISO_MIN_W = 48.0  # the isometric picture may shrink to this width to make room for the side view
GAP_ROW = 28.0  # between the front view and the top view: dimensions, cutting-plane marks, view title
GAP_COL = 36.0  # between the front view and the side view: dimensions and diameter leaders
LEADER_W = 34.0  # room right of a view that carries diameter leaders
DIM_W = 20.0  # room right of a view without leaders (dimensions added on that side)


def title_offset(name, section, n_above=0):
    """Height of a view's title above the view's box: clear of the A-A marks and of the dimensions above it."""
    return max(12.0 if name == "front" and section else 6.0, 7.0 + 9.0 * n_above if n_above else 0.0)


def plan_layout(sizes, side, top, s, leaders=None, counts=None, force=False):
    """View centers on the sheet at scale s (first angle) and the box left for the isometric picture, or None when
    the views do not fit. `leaders`: views that carry diameter leaders on their right; `counts`: number of added
    dimensions per (view, side of the view), for which room is kept."""
    leaders = leaders or set()
    n = lambda v, where: (counts or {}).get((v, where), 0)  # noqa: E731

    def margin(v):  # room right of a view: its leaders and the dimensions added on that side
        if v in leaders:
            return LEADER_W + 9.0 * n(v, "right")
        return DIM_W + 9.0 * max(0, n(v, "right") - 1)

    fw, fh = sizes["front"][0] * s, sizes["front"][1] * s
    sw, sh = (sizes[side][0] * s, sizes[side][1] * s) if side else (0.0, 0.0)
    th = sizes["top"][1] * s if top else 0.0
    row_h = max(fh, sh)
    section = side == "section"
    # the titles (and the dimensions above the first row) stay inside the frame
    row_top = min(TOP, H - 17 - max(title_offset(v, section, n(v, "above")) for v in ("front", side) if v))
    row_bottom = row_top - row_h
    fx1 = LEFT + fw
    # the front view's right side: its leaders and dimensions; the side view's left dimensions
    gap_col = max(GAP_COL, margin("front") + 2) + 9.0 * n(side, "left")
    right_limit = ISO_X1 - ISO_MIN_W - 4  # the first row shares its height with the isometric picture
    row_right = fx1 + gap_col + sw + margin(side) if side else fx1 + margin("front")
    # below the notes line the first row stays left of the notes and the title block
    ok = (row_bottom >= NOTES_Y or row_right <= TB_X) and row_right <= right_limit
    below = lambda v: 9.0 * n(v, "below")  # noqa: E731
    gap_row = GAP_ROW + 9.0 * n("front", "below") + title_offset("top", section, n("top", "above")) - 6.0
    if top:
        ty1 = row_bottom - gap_row
        ty0 = ty1 - th
        ok &= ty0 >= BOTTOM + below("top")
        # below the notes/title block line the top view and its leaders stay left of the title block
        ok &= fx1 + margin("top") <= TB_X or ty0 >= 88
    else:
        ok &= row_bottom >= BOTTOM + (16.0 if section else 9.0) + below("front")  # + the overall width
    if not ok and not force:
        return None
    # spare width: shift the columns right, centred in what is left of the sheet
    spare = max(0.0, TB_X - margin("top") - fx1) / 2 if top else max(0.0, right_limit - row_right) / 2
    spare = min(spare, max(0.0, right_limit - row_right) / 2)
    cx = LEFT + spare + fw / 2
    out = {"front": (cx, row_top - row_h / 2)}
    if side:
        out[side] = (fx1 + spare + gap_col + sw / 2, row_top - row_h / 2)
    if top:
        out["top"] = (cx, row_bottom - gap_row - th / 2)
    out["iso_box"] = (min(max(ISO_X0, row_right + spare + 4), ISO_X1 - ISO_MIN_W), ISO_Y0, ISO_X1, ISO_Y1)
    return out


def make_drawing(shape, *, title, drawing_no, material="", material_long="", quantity="1 ADET", revision=1, scale=None,
                 views=("front", "top", "side", "iso"), section_mode="auto", notes=None, dimensions=None, leaders=None,
                 density_kg_m3=None, drawn_by="", date=None, pdf=None, png=None, dpi=200, preview=None, preview_dpi=72):
    """Draw `shape` on an A3 sheet and save it. Returns a report dict (scale, mass, views, auto dimensions, warnings).
    `preview`: a binary file object that also receives a small PNG of the sheet (for the agent to look at)."""
    bb = shape.BoundBox
    tol_model = max(bb.DiagonalLength / 8000.0, 0.002)  # smooth arcs even when the sheet enlarges the part
    report = {"warnings": [], "dimensions": []}
    want = set(views)
    proj = {"front": project(shape, "front", tol_model)}
    has_hidden = bool(proj["front"]["hidden"])
    use_section = section_mode == "always" or (section_mode == "auto" and has_hidden and "side" in want)
    side_name = None
    cut_face = None
    if "side" in want:
        if use_section:
            cut_x = (bb.XMin + bb.XMax) / 2
            kept, cut_face = section(shape, cut_x)
            proj["section"] = project(kept, "section", tol_model)
            proj["section"]["hidden"] = []  # section views show what is cut and what lies behind it, nothing hidden
            side_name = "section"
            report["section_x_mm"] = round(cut_x, 3)
        else:
            proj["left"] = project(shape, "left", tol_model)
            side_name = "left"
    if "top" in want:
        proj["top"] = project(shape, "top", tol_model)

    def size(name):
        x0, y0, x1, y1 = proj[name]["bounds"]
        return x1 - x0, y1 - y0

    sizes = {n: size(n) for n in proj}
    with_leaders = {n for n in proj if proj[n]["circles"]}
    counts = {}
    for d in dimensions or []:
        v = side_name if d.get("view", "front") == "side" else d.get("view", "front")
        where = _dim_side(v, d, bool(proj[v]["circles"])) if v in proj else None
        if where:
            counts[(v, where)] = counts.get((v, where), 0) + 1
    layout = lambda s, force=False: plan_layout(  # noqa: E731
        sizes, side_name, "top" in proj, s, with_leaders, counts, force)
    sc = parse_scale(scale) if scale else choose_scale(lambda s: layout(s) is not None)
    s = sc[0] / sc[1]
    centers = layout(s)
    if centers is None:
        report["warnings"].append(f"{scale_text(sc)} ölçeğinde görünüşler sayfaya sığmıyor; daha küçük ölçek seçin.")
        centers = layout(s, True)
    iso_box = centers.pop("iso_box")
    sheet = Sheet()
    place = {n: ViewPlacement(n, proj[n], s, *centers[n]) for n in centers}

    for name, vp in place.items():
        _draw_view(sheet, vp, shape, cut_face if name == "section" else None, tol_model)

    # section line A-A in the front view
    if side_name == "section":
        vp = place["front"]
        cx, _ = vp.P(*to_view("front", (report["section_x_mm"], 0, 0)))
        x0, y0, x1, y1 = vp.box()
        sheet.line(cx, y0 - 3, cx, y1 + 3, TN, CL)
        for ya, sgn in ((y0 - 3, -1), (y1 + 3, 1)):
            sheet.line(cx, ya, cx, ya + sgn * 4, TK * 1.3)
            sheet.line(cx, ya + sgn * 4, cx - 8, ya + sgn * 4, TN)
            sheet.arrow(cx - 8, ya + sgn * 4, -1, 0, length=3.5, width=0.9)
            sheet.text(cx - 14, ya + sgn * 4, "A", fs=10)

    # automatic dimensions: overall sizes and the round features each view shows as circles
    _auto_dims(sheet, place, report)
    for d in dimensions or []:
        _user_dim(sheet, place, d, report)
    for item in leaders or []:
        _user_leader(sheet, place, item, report, iso_box if "iso" in want else None)

    # view titles above the views, clear of the dimensions placed above them and of the A-A marks
    for name, vp in place.items():
        x0, y0, x1, y1 = vp.box()
        n_above = round((vp.slot["above"] - 8.0) / 9.0)
        sheet.text(x0, y1 + title_offset(name, side_name == "section", n_above), VIEW_TITLES[name], fs=9, ha="left",
                   weight="bold")

    # isometric picture, top-right
    if "iso" in want:
        img, ext = shaded_image(shape)
        iso_proj = project(shape, "iso", tol_model)
        bx0, by0, bx1, by1 = iso_box
        sheet.keepout.append(((bx0, by0, bx1, by1), "izometrik resmin"))
        k = min((bx1 - bx0) / (ext[1] - ext[0]), (by1 - by0) / (ext[3] - ext[2]))
        ox = (bx0 + bx1) / 2 - k * (ext[0] + ext[1]) / 2
        oy = (by0 + by1) / 2 - k * (ext[2] + ext[3]) / 2
        # no lanczos: its ringing draws a dark fringe along the transparent edge of the picture
        sheet.ax.imshow(img, extent=(ox + k * ext[0], ox + k * ext[1], oy + k * ext[2], oy + k * ext[3]),
                        zorder=1, interpolation="antialiased")
        for pl in iso_proj["visible"]:
            sheet.ax.plot(ox + k * pl[:, 0], oy + k * pl[:, 1], lw=0.5, color="#1a1a1a", zorder=2)

    rho = density_kg_m3 or density_for(material) or density_for(material_long)
    mass = shape.Volume * 1e-9 * rho * 1000 if rho else None
    if mass is None:
        report["warnings"].append("Malzemenin yoğunluğu bilinmiyor: ağırlık boş bırakıldı (density_kg_m3 verin).")
    std = ["Aksi belirtilmedikçe ölçüler mm'dir.", "Keskin kenarları pahlayın ve kırın."]
    lines = std + [n for n in (notes or []) if n not in std]
    sheet.notes(lines[:9])
    if len(lines) > 9:
        report["warnings"].append(f"Notlardan yalnızca ilk 9'u sığdı ({len(lines)} not verildi).")
    sheet.title_block(title, drawing_no, material or "—", material_long or material or "—", revision, scale_text(sc), mass,
                      quantity, date or datetime.date.today().strftime("%d.%m.%Y"), drawn_by)
    report["warnings"] += sheet.check_layout()
    sheet.save(pdf, png, dpi)
    if preview is not None:
        sheet.fig.savefig(preview, format="png", dpi=preview_dpi, facecolor="white")
    report.update(scale=scale_text(sc), mass_g=round(mass, 1) if mass is not None else None, density_kg_m3=rho,
                  views=[n for n in place] + (["iso"] if "iso" in want else []), volume_mm3=round(shape.Volume, 2))
    return report


def _draw_view(sheet, vp, shape, cut_face, tol):
    for pl in vp.proj["hidden"]:
        sheet.polyline(np.array([vp.P(*p) for p in pl]), TN * 0.9, HD, zorder=1)
    if cut_face is not None:
        for rings in face_rings(cut_face, vp.name, tol):
            sheet.hatch([np.array([vp.P(*p) for p in r]) for r in rings])
    for pl in vp.proj["visible"]:
        sheet.polyline(np.array([vp.P(*p) for p in pl]), TK)
    # center lines: crosses on circles seen face-on, axes of round features seen from the side
    for c in vp.proj["circles"]:
        x, y = vp.P(c["x"], c["y"])
        ext = vp.s * c["r"] + 3
        sheet.line(x - ext, y, x + ext, y, TN, CL, zorder=1)
        sheet.line(x, y - ext, x, y + ext, TN, CL, zorder=1)
    for (a, b) in axes_in_view(shape, vp.name):
        p, q = np.array(vp.P(*a)), np.array(vp.P(*b))
        u = (q - p) / (np.linalg.norm(q - p) or 1)
        sheet.line(*(p - 3 * u), *(q + 3 * u), TN, CL, zorder=1)


def _outer_circle(vp, horizontal):
    """The circle of a view that spans the whole view across (a round part seen end-on), or None."""
    x0, y0, x1, y1 = vp.proj["bounds"]
    span, mid = (x1 - x0, (x0 + x1) / 2) if horizontal else (y1 - y0, (y0 + y1) / 2)
    for c in vp.proj["circles"]:
        if c["visible"] and abs(2 * c["r"] - span) < 1e-3 and abs((c["x"] if horizontal else c["y"]) - mid) < 1e-3:
            return c
    return None


def _auto_dims(sheet, place, report):
    # a round part: its outer diameter is dimensioned in the front view as "Ø.." instead of with a leader
    outer = _outer_circle(place["top"], True) if "top" in place and "front" in place else None
    for name, vp in place.items():
        x0, y0, x1, y1 = vp.proj["bounds"]
        (a, b, c, d) = vp.box()
        if name == "front":
            # below the cutting-plane marks of section A-A when there is one
            txt = ("Ø" if outer else "") + fmt(x1 - x0)
            sheet.dim_h(a, b, c, b, b - vp.take("below", 7 if "section" in place else 0), txt)
            sheet.dim_v(a, b, a, d, a - vp.take("left"), fmt(y1 - y0))
            report["dimensions"].append({"view": name, "text": txt, "auto": "çap" if outer else "genişlik"})
            report["dimensions"].append({"view": name, "text": fmt(y1 - y0), "auto": "yükseklik"})
        elif name == "top":
            sheet.dim_v(a, b, a, d, a - vp.take("left"), fmt(y1 - y0))
            report["dimensions"].append({"view": name, "text": fmt(y1 - y0), "auto": "derinlik"})
            if "front" not in place:
                sheet.dim_h(a, b, c, b, b - vp.take("below"), fmt(x1 - x0))
        elif name in ("left", "section") and "top" not in place:
            sheet.dim_h(a, b, c, b, b - vp.take("below"), fmt(x1 - x0))
            report["dimensions"].append({"view": name, "text": fmt(x1 - x0), "auto": "derinlik"})
        _circle_leaders(sheet, vp, report, skip=outer if name == "top" else None)


def _circle_leaders(sheet, vp, report, skip=None):
    groups = {}
    for c in vp.proj["circles"]:
        if skip is not None and abs(c["r"] - skip["r"]) < 1e-3 and abs(c["x"] - skip["x"]) < 1e-3:
            continue
        groups.setdefault(round(c["r"], 3), []).append(c)
    if not groups:
        return
    x0, y0, x1, y1 = vp.box()
    # at most 6 leaders: visible features first, then hole patterns, then the larger ones; largest circle lowest so
    # the leaders fan out without crossing
    picked = sorted(groups.items(), key=lambda kv: (not any(c["visible"] for c in kv[1]), len(kv[1]) < 2, -kv[0]))[:6]
    items = sorted(picked, key=lambda kv: -kv[0])
    arrows = []
    for k, (r, cs) in enumerate(items):
        c = max(cs, key=lambda c: (c["x"], c["y"]))  # the right-most circle carries the leader
        cx, cy = vp.P(c["x"], c["y"])
        txt = (f"{len(cs)}x " if len(cs) > 1 else "") + "Ø" + fmt(2 * r)
        ang = math.radians(15 + (60 * k / (len(items) - 1) if len(items) > 1 else 30))
        arrows.append((cy + vp.s * r * math.sin(ang), cx + vp.s * r * math.cos(ang), txt))
    # shelves stack upward; of all orders (at most 6! = 720) take the one with the fewest crossing leaders
    best = None
    for order in itertools.permutations(arrows):
        last, lines = -1e9, []
        for py, px, txt in order:
            last = max(py + 6, last + 6.5)
            lines.append(((px, py), (x1 + 8, last), txt))
        cost = (sum(_crosses(a[:2], b[:2]) for a, b in itertools.combinations(lines, 2)),
                sum(math.dist(*ln[:2]) for ln in lines))
        if best is None or cost < best[0]:
            best = (cost, lines)
    for (px, py), (ex, ey), txt in best[1]:
        if ey > 284:
            continue
        sheet.leader(px, py, ex, ey, txt)
        report["dimensions"].append({"view": vp.name, "text": txt, "auto": "çap"})


def _crosses(s1, s2):
    """Do two segments ((x, y), (x, y)) cross?"""
    def side(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    (a, b), (c, d) = s1, s2
    return side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0


def _point_in(place, view, p):
    if view not in place:
        raise ValueError(f"'{view}' görünüşü bu resimde yok (var olanlar: {', '.join(place)})")
    vp = place[view]
    return np.array(vp.P(*to_view(view, p))), vp


def _dim_side(view, d, has_circles):
    """Where an added dimension goes: 'below'/'above' (horizontal), 'left'/'right' (vertical), None (aligned)."""
    try:
        a, b = to_view(view, d["from"]), to_view(view, d["to"])
    except (KeyError, TypeError, ValueError):
        return None
    kind = d.get("direction") or ("horizontal" if abs(b[0] - a[0]) >= abs(b[1] - a[1]) else "vertical")
    if d.get("side") == "inside" and kind in ("horizontal", "vertical"):
        return "inside"
    if kind == "horizontal":
        return "above" if d.get("side") == "above" else "below"
    if kind == "vertical":
        # the right side belongs to the diameter leaders when the view has round features seen face-on
        return d.get("side") if d.get("side") in ("left", "right") else ("left" if has_circles else "right")
    return None


def _user_dim(sheet, place, d, report):
    view = d.get("view", "front")
    if view == "side":
        view = "section" if "section" in place else "left"
    try:
        a, vp = _point_in(place, view, d["from"])
        b, _ = _point_in(place, view, d["to"])
    except (KeyError, ValueError) as e:
        report["warnings"].append(f"Ölçü atlandı: {e}")
        return
    where = _dim_side(view, d, bool(vp.proj["circles"]))
    kind = d.get("direction") or ("horizontal" if abs(b[0] - a[0]) >= abs(b[1] - a[1]) else "vertical")
    x0, y0, x1, y1 = vp.box()
    off = float(d.get("offset_mm", 0) or 0)
    tol = d.get("tolerance")
    if where == "inside":  # through the points themselves (inside the view, like a bore's diameter)
        value = (abs(b[0] - a[0]) if kind == "horizontal" else abs(b[1] - a[1])) / vp.s
        if kind == "horizontal":
            sheet.dim_h(a[0], a[1], b[0], b[1], (a[1] + b[1]) / 2 + off, d.get("text") or fmt(value), tol)
        else:
            sheet.dim_v(a[0], a[1], b[0], b[1], (a[0] + b[0]) / 2 + off, d.get("text") or fmt(value), tol)
    elif where in ("above", "below"):
        value = abs(b[0] - a[0]) / vp.s
        yd = (y1 + vp.take("above", off)) if where == "above" else (y0 - vp.take("below", off))
        sheet.dim_h(a[0], a[1], b[0], b[1], yd, d.get("text") or fmt(value), tol)
    elif where in ("left", "right"):
        value = abs(b[1] - a[1]) / vp.s
        xd = (x1 + vp.take("right", off)) if where == "right" else (x0 - vp.take("left", off))
        sheet.dim_v(a[0], a[1], b[0], b[1], xd, d.get("text") or fmt(value), tol)
    else:
        value = float(np.linalg.norm(b - a)) / vp.s
        sheet.dim_aligned(a[0], a[1], b[0], b[1], 8 + off, d.get("text") or fmt(value))
    report["dimensions"].append({"view": view, "text": d.get("text") or fmt(value), "measured_mm": round(value, 4)})


def _user_leader(sheet, place, item, report, iso_box=None):
    view = item.get("view", "front")
    if view == "side":
        view = "section" if "section" in place else "left"
    try:
        p, vp = _point_in(place, view, item["at"])
    except (KeyError, ValueError) as e:
        report["warnings"].append(f"Not oku atlandı: {e}")
        return
    x0, y0, x1, y1 = vp.box()
    txt = str(item.get("text", ""))
    width = len(txt) * 7.5 * 0.21 + 2  # as Sheet.leader
    others = [v.box() for v in place.values() if v is not vp] + ([iso_box] if iso_box else [])

    def free(ex, ey, right):  # the shelf and its text stay inside the frame and off the other views
        a, b = (ex, ex + width) if right else (ex - width, ex)
        return a >= 12 and b <= W - 12 and 12 <= ey <= H - 16 and not any(
            min(b, o[2] + 2) - max(a, o[0] - 2) > 0 and min(ey + 4, o[3] + 2) - max(ey, o[1] - 2) > 0 for o in others)

    rise = float(item.get("rise_mm", 10))
    toward_center = p[0] < (x0 + x1) / 2  # inside the view the shelf points to its middle
    candidates = {  # side -> (shelf start x, y, shelf to the right)
        "right": (x1 + 10, p[1] + rise, True),
        "left": (x0 - 10, p[1] + rise, False),
        "inside": (p[0] + (8 if toward_center else -8), p[1] - abs(rise), toward_center),
    }
    order = ["right", "left"] if p[0] >= (x0 + x1) / 2 else ["left", "right"]
    side = item.get("side") if item.get("side") in candidates else next(
        (c for c in order if free(*candidates[c])), "inside")
    ex, ey, right = candidates[side]
    sheet.leader(p[0], p[1], ex, ey, txt, right=right)


def default_paths(doc, obj):
    """<folder of the .FCStd>/<file>_<object>_teknik_resim.pdf|png, or the CadAI folder for unsaved documents."""
    from . import config

    if doc.FileName:
        folder = os.path.dirname(doc.FileName)
        stem = os.path.splitext(os.path.basename(doc.FileName))[0]
    else:
        folder = os.path.join(os.path.dirname(config.config_path()), "drawings")
        stem = doc.Name
    base = os.path.join(folder, re.sub(r'[\\/:*?"<>|]+', "_", f"{stem}_{obj.Name}_teknik_resim"))
    return base + ".pdf", base + ".png"
