"""Render: pictures of the model with materials and studio light.

Two engines, same inputs (objects, view, materials, background):
- "blender": photorealistic, Blender Cycles in a separate process (GPU when there is one), soft shadows on a
  shadow catcher, studio HDRI reflections, optional turntable (GIF). The Blender side is blender_render.py.
- "quick": built-in numpy rasterizer, no extra program: smooth shading per B-rep face, key/fill light, metal
  reflections of a simple studio, soft ground shadow (shadow map), 2× supersampling. A few seconds, works anywhere.

Geometry goes over as triangles tessellated per B-rep face (vertices are not shared between faces), so smooth
shading stays inside a face and every CAD edge stays crisp.
"""

import json
import os
import re
import tempfile
import time

import numpy as np

# sRGB base colour, metallic, roughness (+ transmission). "color": None = the object's own colour.
PRESETS = {
    "aluminium": {"color": (0.91, 0.92, 0.93), "metallic": 1.0, "roughness": 0.32},
    "anodized": {"color": None, "metallic": 1.0, "roughness": 0.38, "default_color": (0.16, 0.36, 0.78)},
    "steel": {"color": (0.62, 0.63, 0.64), "metallic": 1.0, "roughness": 0.38},
    "stainless": {"color": (0.76, 0.75, 0.73), "metallic": 1.0, "roughness": 0.22},
    "chrome": {"color": (0.96, 0.96, 0.96), "metallic": 1.0, "roughness": 0.05},
    "brass": {"color": (0.93, 0.80, 0.46), "metallic": 1.0, "roughness": 0.25},
    "copper": {"color": (0.96, 0.66, 0.54), "metallic": 1.0, "roughness": 0.25},
    "gold": {"color": (1.0, 0.82, 0.45), "metallic": 1.0, "roughness": 0.18},
    "cast_iron": {"color": (0.38, 0.38, 0.39), "metallic": 1.0, "roughness": 0.65},
    "black_oxide": {"color": (0.13, 0.13, 0.14), "metallic": 1.0, "roughness": 0.45},
    "plastic": {"color": None, "metallic": 0.0, "roughness": 0.42, "default_color": (0.85, 0.85, 0.85)},
    "glossy_plastic": {"color": None, "metallic": 0.0, "roughness": 0.12, "default_color": (0.85, 0.85, 0.85)},
    "rubber": {"color": (0.06, 0.06, 0.06), "metallic": 0.0, "roughness": 0.85},
    "glass": {"color": (0.95, 0.97, 1.0), "metallic": 0.0, "roughness": 0.02, "transmission": 1.0},
    "pcb": {"color": (0.05, 0.32, 0.14), "metallic": 0.0, "roughness": 0.3},
    "clay": {"color": (0.82, 0.80, 0.77), "metallic": 0.0, "roughness": 0.75},
    "wood": {"color": (0.62, 0.43, 0.26), "metallic": 0.0, "roughness": 0.6},
}

# material / label text -> preset; the first match wins
MATERIAL_WORDS = [
    (r"paslanmaz|inox|stainless|\b30[14]\b|\b316", "stainless"), (r"krom|chrome", "chrome"),
    (r"pirin[cç]|brass|cuzn", "brass"), (r"bronz|bronze", "brass"), (r"bak[ıi]r|copper", "copper"),
    (r"alt[ıi]n|gold", "gold"), (r"elokson|anodi[zs]", "anodized"),
    (r"al[uü]m|\bal\b|\bal ?\d|60[0-9]{2}|70[0-9]{2}|aluminium|aluminum", "aluminium"),
    (r"d[oö]k[uü]m|cast iron|gg ?\d|gjl", "cast_iron"), (r"siyah oksit|black oxide|black_oxide", "black_oxide"),
    (r"[cç]elik|steel|s235|s355|c45|42crmo|1\.\d{4}", "steel"),
    (r"\bpla\b|\babs\b|petg|pom|delrin|naylon|nylon|\bpa ?6|plasti|polimer|\bpc\b|\bpp\b|\bpe\b", "plastic"),
    (r"kau[cç]uk|rubber|silikon|silicone|epdm|nbr", "rubber"), (r"\bcam\b|glass|akrilik|acrylic|pmma", "glass"),
    (r"\bpcb\b|fr-?4|devre kart|\bkart\b", "pcb"), (r"ah[sş]ap|wood", "wood"),  # "… kart": kicad_board's label
]

VIEW_DIRS = {  # direction from the part toward the camera (z up, front = -Y like FreeCAD)
    "iso": (1, -1, 1), "hero": (1.0, -1.7, 0.85), "front": (0, -1, 0), "back": (0, 1, 0), "left": (-1, 0, 0),
    "right": (1, 0, 0), "top": (0, 0, 1), "bottom": (0, 0, -1), "iso_back": (-1, 1, 1),
}
QUALITY = {"draft": {"samples": 48, "tol": 1 / 1500}, "final": {"samples": 384, "tol": 1 / 4000}}


class RenderError(Exception):
    pass


# ---------------- inputs ----------------

def view_direction(view="iso", azimuth_deg=None, elevation_deg=None):
    if azimuth_deg is not None or elevation_deg is not None:
        az, el = np.radians(azimuth_deg or 0.0), np.radians(30.0 if elevation_deg is None else elevation_deg)
        d = np.array([np.sin(az) * np.cos(el), -np.cos(az) * np.cos(el), np.sin(el)])  # az 0 = front
    else:
        if view not in VIEW_DIRS:
            raise RenderError(f"Bilinmeyen görünüş: {view} ({', '.join(VIEW_DIRS)}).")
        d = np.array(VIEW_DIRS[view], float)
    return d / np.linalg.norm(d)


def parse_color(value):
    if value is None:
        return None
    if isinstance(value, str):
        m = re.fullmatch(r"#?([0-9a-fA-F]{6})", value.strip())
        if not m:
            raise RenderError(f"Renk '#rrggbb' biçiminde olmalı: {value!r}")
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    c = [float(x) for x in value][:3]
    if len(c) != 3:
        raise RenderError(f"Renk üç sayı olmalı: {value!r}")
    return tuple(x / 255 if max(c) > 1 else x for x in c)


def preset_for_text(text):
    text = str(text or "").lower()
    for pattern, preset in MATERIAL_WORDS:
        if re.search(pattern, text):
            return preset
    return None


def resolve_material(spec, obj_color, material_name="", label=""):
    """Material dict for one object. spec: None, a preset name or {"preset", "color", "roughness", "metallic"}."""
    if isinstance(spec, str):
        spec = {"preset": spec}
    spec = dict(spec or {})
    preset = spec.get("preset")
    if preset is None:
        preset = preset_for_text(material_name) or preset_for_text(label)
    if preset is None:  # FreeCAD's default grey -> machined aluminium, a real colour -> plastic of that colour
        # FreeCAD 1.1's default is a bluish grey (0.678, 0.71, 0.741); older versions 0.8 grey
        grey = obj_color is None or (max(obj_color) - min(obj_color) < 0.08 and 0.6 < sum(obj_color) / 3 < 0.9)
        preset = "aluminium" if grey else "plastic"
    if preset not in PRESETS:
        raise RenderError(f"Bilinmeyen malzeme: {preset} ({', '.join(PRESETS)}).")
    p = PRESETS[preset]
    color = parse_color(spec.get("color")) or p["color"] or obj_color or p.get("default_color")
    if preset in ("plastic", "glossy_plastic") and obj_color is not None and spec.get("color") is None:
        color = obj_color
    return {"preset": preset, "color": [round(c, 4) for c in color],
            "metallic": float(spec.get("metallic", p["metallic"])),
            "roughness": float(spec.get("roughness", p["roughness"])),
            "transmission": float(p.get("transmission", 0.0))}


def tessellate(shape, tol):
    """(vertices N×3, triangles M×3) with every B-rep face tessellated on its own."""
    verts, tris, base = [], [], 0
    for face in shape.Faces:
        try:
            pts, tri = face.tessellate(tol)
        except Exception:
            continue
        if not tri:
            continue
        verts.append(np.array([(p.x, p.y, p.z) for p in pts], np.float32))
        tris.append(np.array(tri, np.int32) + base)
        base += len(pts)
    if not verts:
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int32)
    return np.concatenate(verts), np.concatenate(tris)


def to_linear(c):
    c = np.asarray(c, float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def to_srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def background_image(kind, w, h):
    """RGB float image of the backdrop: 'studio' soft grey gradient, 'white', or '#rrggbb'."""
    if kind == "studio":
        y = np.linspace(0, 1, h)[:, None]
        x = np.linspace(-1, 1, w)[None, :]
        g = 0.985 - 0.10 * y ** 1.4 - 0.035 * x ** 2
        return np.repeat(g[:, :, None], 3, axis=2) * np.array([1.0, 1.0, 1.01])
    if kind == "white":
        return np.ones((h, w, 3))
    return np.ones((h, w, 3)) * np.array(parse_color(kind))


def composite(rgba, background):
    """RGBA float (straight alpha) over the backdrop -> RGBA float; 'transparent' keeps the alpha."""
    if background == "transparent":
        return rgba
    h, w = rgba.shape[:2]
    bg = background_image(background, w, h)
    a = rgba[:, :, 3:4]
    out = np.ones((h, w, 4))
    out[:, :, :3] = rgba[:, :, :3] * a + bg * (1 - a)
    return out


def save_png(rgba, path):
    from PIL import Image

    img = Image.fromarray((np.clip(rgba, 0, 1) * 255 + 0.5).astype(np.uint8), "RGBA")
    if np.all(rgba[:, :, 3] >= 0.999):
        img = img.convert("RGB")
    img.save(path, optimize=True)


def load_png(path):
    from PIL import Image

    with Image.open(path) as img:
        return np.asarray(img.convert("RGBA"), np.float64) / 255.0


def preview_png(path, max_px=900):
    """Small PNG (bytes) of a rendered image for the AI to look at."""
    import io

    from PIL import Image

    with Image.open(path) as img:
        img = img.copy()
    img.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ---------------- quick engine (numpy) ----------------

def _raster(sx, sy, sz, T, nx, ny):
    """Z-buffer rasterization (larger z = closer). Returns zbuf, triangle id per pixel, barycentrics b0, b1."""
    zbuf = np.full(ny * nx, -np.inf)
    tid = np.full(ny * nx, -1, np.int64)
    b0 = np.zeros(ny * nx)
    b1 = np.zeros(ny * nx)
    xa, xb, xc = sx[T[:, 0]], sx[T[:, 1]], sx[T[:, 2]]
    ya, yb, yc = sy[T[:, 0]], sy[T[:, 1]], sy[T[:, 2]]
    za, zb, zc = sz[T[:, 0]], sz[T[:, 1]], sz[T[:, 2]]
    area = (xb - xa) * (yc - ya) - (xc - xa) * (yb - ya)
    i0 = np.floor(np.minimum(np.minimum(xa, xb), xc) - 0.5).astype(int).clip(0, nx - 1)
    i1 = np.ceil(np.maximum(np.maximum(xa, xb), xc) - 0.5).astype(int).clip(0, nx - 1)
    j0 = np.floor(np.minimum(np.minimum(ya, yb), yc) - 0.5).astype(int).clip(0, ny - 1)
    j1 = np.ceil(np.maximum(np.maximum(ya, yb), yc) - 0.5).astype(int).clip(0, ny - 1)
    ok = np.abs(area) > 1e-12

    def write(pix, z, t, w0, w1):
        order = np.lexsort((z, pix))  # by pixel, then depth: the last one of each pixel is the closest
        pix, z, t, w0, w1 = pix[order], z[order], t[order], w0[order], w1[order]
        last = np.r_[pix[1:] != pix[:-1], True]
        pix, z, t, w0, w1 = pix[last], z[last], t[last], w0[last], w1[last]
        closer = z > zbuf[pix]
        pix = pix[closer]
        zbuf[pix], tid[pix], b0[pix], b1[pix] = z[closer], t[closer], w0[closer], w1[closer]

    K = 8  # triangles whose box fits in K×K pixels go through in vectorized batches, the rest one by one
    small = ok & (i1 - i0 < K) & (j1 - j0 < K)
    oi, oj = np.meshgrid(np.arange(K), np.arange(K))
    oi, oj = oi.ravel(), oj.ravel()
    idx_small = np.nonzero(small)[0]
    for s in range(0, len(idx_small), 20000):
        t = idx_small[s:s + 20000]
        px = i0[t, None] + oi[None, :]
        py = j0[t, None] + oj[None, :]
        gx, gy = px + 0.5, py + 0.5
        w0 = ((xb[t, None] - gx) * (yc[t, None] - gy) - (xc[t, None] - gx) * (yb[t, None] - gy)) / area[t, None]
        w1 = ((xc[t, None] - gx) * (ya[t, None] - gy) - (xa[t, None] - gx) * (yc[t, None] - gy)) / area[t, None]
        w2 = 1 - w0 - w1
        m = (w0 >= -1e-7) & (w1 >= -1e-7) & (w2 >= -1e-7) & (px <= i1[t, None]) & (py <= j1[t, None])
        z = w0 * za[t, None] + w1 * zb[t, None] + w2 * zc[t, None]
        tt = np.broadcast_to(t[:, None], m.shape)
        write((py * nx + px)[m], z[m], tt[m], w0[m], w1[m])
    for t in np.nonzero(ok & ~small)[0]:
        gx, gy = np.meshgrid(np.arange(i0[t], i1[t] + 1) + 0.5, np.arange(j0[t], j1[t] + 1) + 0.5)
        w0 = ((xb[t] - gx) * (yc[t] - gy) - (xc[t] - gx) * (yb[t] - gy)) / area[t]
        w1 = ((xc[t] - gx) * (ya[t] - gy) - (xa[t] - gx) * (yc[t] - gy)) / area[t]
        w2 = 1 - w0 - w1
        m = (w0 >= -1e-7) & (w1 >= -1e-7) & (w2 >= -1e-7)
        if not m.any():
            continue
        z = w0 * za[t] + w1 * zb[t] + w2 * zc[t]
        pix = ((gy - 0.5).astype(int) * nx + (gx - 0.5).astype(int))[m]
        write(pix, z[m], np.full(pix.size, t), w0[m], w1[m])
    return zbuf, tid, b0, b1


def _box_blur(img, r):
    if r < 1:
        return img
    out = img
    for axis in (0, 1):
        c = np.cumsum(np.pad(out, [(r + 1, r) if a == axis else (0, 0) for a in range(2)], mode="edge"), axis=axis)
        n = out.shape[axis]
        hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
        lo = np.take(c, np.arange(0, n), axis=axis)
        out = (hi - lo) / (2 * r + 1)
    return out


def _frame(d):
    up_world = np.array([0.0, 0.0, 1.0]) if abs(d[2]) < 0.99 else np.array([0.0, 1.0, 0.0])
    right = np.cross(up_world, d)
    right /= np.linalg.norm(right)
    up = np.cross(d, right)
    return right, up


def _env(r_dir, key):
    """Radiance of a simple studio around the part (for metal reflections): bright ceiling, a softbox at the key
    light, darker floor."""
    z = r_dir[..., 2]
    sky = 0.18 + 0.72 * np.clip((z + 0.15) / 1.15, 0, 1) ** 1.5
    soft = 2.2 * np.exp(-(1 - np.clip(r_dir @ key, -1, 1)) / 0.06)
    return sky + soft


def quick_render(meshes, direction, width=1600, height=1200, ss=2):
    """meshes: [(vertices, triangles, material)]; returns straight-alpha RGBA float image (height×width×4)."""
    d = np.asarray(direction, float)
    right, up = _frame(d)
    V = np.concatenate([m[0] for m in meshes]).astype(np.float64)
    offs = np.cumsum([0] + [len(m[0]) for m in meshes])
    T = np.concatenate([m[1] + offs[i] for i, m in enumerate(meshes)])
    tri_mat = np.concatenate([np.full(len(m[1]), i) for i, m in enumerate(meshes)])
    if not len(T):
        raise RenderError("Çizilecek yüzey yok.")
    # per-vertex normals: area-weighted triangle normals (vertices are per B-rep face, so faces stay crisp)
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    fn = np.cross(b - a, c - a)
    vn = np.zeros_like(V)
    for k in range(3):
        np.add.at(vn, T[:, k], fn)
    vn /= np.linalg.norm(vn, axis=1, keepdims=True) + 1e-12

    W, H = width * ss, height * ss
    zmin = V[:, 2].min()
    show_ground = d[2] > 0.05
    pts = V
    if show_ground:  # leave room for the shadow on the floor
        pts = np.vstack([V, V * [1, 1, 0] + [0, 0, zmin]])
    u, v = pts @ right, pts @ up
    span = max((u.max() - u.min()) / W, (v.max() - v.min()) / H) * 1.12
    cu, cv = (u.max() + u.min()) / 2, (v.max() + v.min()) / 2
    sx = (V @ right - cu) / span + W / 2
    sy = H / 2 - (V @ up - cv) / span
    zbuf, tid, b0, b1 = _raster(sx, sy, V @ d, T, W, H)
    hit = tid >= 0

    key = 0.55 * d - 0.75 * right + 1.1 * np.array([0, 0, 1.0])
    key /= np.linalg.norm(key)
    fill = 0.9 * d + 0.8 * right + 0.25 * np.array([0, 0, 1.0])
    fill /= np.linalg.norm(fill)

    # shadow map along the key light
    lr, lu = _frame(key)
    lx, ly = V @ lr, V @ lu
    SM = 1400
    lspan = max(lx.max() - lx.min(), ly.max() - ly.min()) * 1.05 / SM
    l0x, l0y = lx.min() - lspan * 10, ly.min() - lspan * 10
    sm_w = int((lx.max() - l0x) / lspan) + 12
    sm_h = int((ly.max() - l0y) / lspan) + 12
    lz, _, _, _ = _raster((lx - l0x) / lspan, (ly - l0y) / lspan, V @ key, T, sm_w, sm_h)
    lz = lz.reshape(sm_h, sm_w)
    bias = max(np.ptp(V, axis=0).max() * 0.004, 1e-6)

    def lit(P):
        """1 where the point sees the key light (3×3 PCF), 0 in shadow."""
        px = ((P @ lr - l0x) / lspan).astype(int)
        py = ((P @ lu - l0y) / lspan).astype(int)
        pz = P @ key
        acc = np.zeros(len(P))
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                qx, qy = np.clip(px + di, 0, sm_w - 1), np.clip(py + dj, 0, sm_h - 1)
                acc += (lz[qy, qx] <= pz + bias)
        return acc / 9

    rgba = np.zeros((H * W, 4))
    idx = np.nonzero(hit)[0]
    t = tid[idx]
    w0, w1 = b0[idx], b1[idx]
    w2 = 1 - w0 - w1
    ia, ib, ic = T[t, 0], T[t, 1], T[t, 2]
    n = w0[:, None] * vn[ia] + w1[:, None] * vn[ib] + w2[:, None] * vn[ic]
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    n[(n @ d) < 0] *= -1  # face the camera whatever the triangle's orientation
    P = w0[:, None] * V[ia] + w1[:, None] * V[ib] + w2[:, None] * V[ic] + n * bias * 0.5
    mats = [m[2] for m in meshes]
    base = to_linear(np.array([m["color"] for m in mats]))[tri_mat[t]]
    metal = np.array([m["metallic"] for m in mats])[tri_mat[t]][:, None]
    rough = np.array([m["roughness"] for m in mats])[tri_mat[t]][:, None]
    shadow = lit(P)[:, None]
    ndk = np.clip(n @ key, 0, 1)[:, None]
    ndf = np.clip(n @ fill, 0, 1)[:, None]
    hemi = 0.5 + 0.5 * n[:, 2:3]
    diffuse = base * (0.95 * ndk * shadow + 0.32 * ndf + 0.22 * hemi + 0.05)
    refl = 2 * (n @ d)[:, None] * n - d
    env = _env(refl, key)[:, None]
    fres = 0.04 + 0.96 * (1 - np.clip(n @ d, 0, 1)[:, None]) ** 5
    gloss = 1 - rough
    spec_dielectric = fres * env * (0.25 + 0.75 * gloss) * (0.6 + 0.4 * shadow)
    h = key + d
    h /= np.linalg.norm(h)
    shin = 2 + 300 * gloss[:, 0] ** 3
    highlight = (np.clip(n @ h, 0, 1) ** shin * (shin + 8) / 40)[:, None] * shadow
    blur_env = env * gloss + (0.55 + 0.25 * hemi) * (1 - gloss)  # rough metal: blurred reflection
    metal_col = base * (0.30 * blur_env + 0.55 * ndk * shadow + 0.12 * ndf + 0.10) + base * highlight * 0.5
    dielectric_col = diffuse + spec_dielectric * 0.6 + highlight * 0.35
    col = metal * metal_col + (1 - metal) * dielectric_col
    rgba[idx, :3] = col
    rgba[idx, 3] = 1.0
    rgba = rgba.reshape(H, W, 4)

    if show_ground:  # shadow-catcher floor: only the darkening, as alpha over the backdrop
        gi = np.nonzero(~hit)[0]
        gx, gy = gi % W + 0.5, gi // W + 0.5
        u0 = (gx - W / 2) * span + cu
        v0 = (H / 2 - gy) * span + cv
        origin = u0[:, None] * right + v0[:, None] * up
        s = (zmin - origin[:, 2]) / d[2]
        G = origin + s[:, None] * d
        occl = np.zeros(H * W)
        occl[gi] = 1 - lit(G + [0, 0, bias])
        occl = _box_blur(occl.reshape(H, W), max(int(ss * 3), 1))
        # contact shadow: the footprint of the part's lowest few millimetres, dropped on the floor and blurred
        low = V[V[:, 2] < zmin + 0.04 * np.ptp(V, axis=0).max()] * [1, 1, 0] + [0, 0, zmin]
        foot = np.zeros((H, W))
        fx = ((low @ right - cu) / span + W / 2).astype(int)
        fy = (H / 2 - (low @ up - cv) / span).astype(int)
        ok = (fx >= 0) & (fx < W) & (fy >= 0) & (fy < H)
        foot[fy[ok], fx[ok]] = 1.0
        foot = np.clip(_box_blur(_box_blur(foot, ss * 4) * 6, ss * 8), 0, 1)
        dark = np.clip(0.42 * occl + 0.3 * foot, 0, 0.6)
        floor = ~hit.reshape(H, W)
        rgba[floor, 3] = dark[floor]
        rgba[floor, :3] = 0.0
    rgb = to_srgb(rgba[:, :, :3]) * rgba[:, :, 3:]  # premultiplied for the supersampling average
    out = np.concatenate([rgb, rgba[:, :, 3:]], axis=2)
    out = out.reshape(height, ss, width, ss, 4).mean(axis=(1, 3))
    a = out[:, :, 3:4]
    out[:, :, :3] = np.where(a > 1e-6, out[:, :, :3] / np.maximum(a, 1e-6), 0)  # back to straight alpha
    return out


# ---------------- Blender engine ----------------

BLENDER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender_render.py")


def blender_job(meshes, direction, width, height, quality, frames, work_dir):
    """Write the job (meshes.npz + job.json) for blender_render.py. Model mm -> Blender metres."""
    arrays = {}
    objects = []
    for i, (v, t, mat, name) in enumerate(meshes):
        arrays[f"v{i}"] = (np.asarray(v, np.float64) * 0.001).astype(np.float32)
        arrays[f"t{i}"] = np.asarray(t, np.int32)
        objects.append({"name": name, "material": mat})
    mesh_file = os.path.join(work_dir, "meshes.npz")
    np.savez(mesh_file, **arrays)
    job = {"mesh_file": mesh_file, "objects": objects, "direction": [float(x) for x in direction],
           "width": int(width), "height": int(height), "samples": QUALITY[quality]["samples"], "frames": int(frames),
           "out_dir": work_dir, "result_file": os.path.join(work_dir, "result.json")}
    path = os.path.join(work_dir, "job.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(job, f)
    return path


def run_blender(blender, job_path, timeout):
    from . import external

    t0 = time.time()
    p = external.run([blender, "-b", "--factory-startup", "--python-exit-code", "1", "--python", BLENDER_SCRIPT,
                      "--", job_path], timeout=timeout)
    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)
    if p.returncode != 0 or not os.path.isfile(job["result_file"]):
        raise RenderError("Blender render başarısız oldu:\n" + external.tail(p.stdout + "\n" + p.stderr, 2500))
    with open(job["result_file"], encoding="utf-8") as f:
        result = json.load(f)
    result["seconds"] = round(time.time() - t0, 1)
    return result


def gif_from_frames(paths, background, gif_path, fps=24, max_px=720):
    from PIL import Image

    frames = []
    for p in paths:
        rgba = composite(load_png(p), "white" if background == "transparent" else background)
        img = Image.fromarray((np.clip(rgba[:, :, :3], 0, 1) * 255 + 0.5).astype(np.uint8), "RGB")
        img.thumbnail((max_px, max_px))
        frames.append(img.quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
    frames[0].save(gif_path, save_all=True, append_images=frames[1:], duration=int(1000 / fps), loop=0,
                   optimize=True, disposal=2)


def work_dir():
    return tempfile.mkdtemp(prefix="cadai_render_")
