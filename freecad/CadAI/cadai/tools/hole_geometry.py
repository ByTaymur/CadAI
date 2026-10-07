"""Recognize closed cylindrical bore sections in B-rep; open slots/fillets are not holes."""

import math

import FreeCAD

from . import ToolError


def cylindrical_holes(shape, axis="z"):
    direction = FreeCAD.Vector(*(1 if a == axis else 0 for a in "xyz"))
    if not shape.Solids:
        raise ToolError("Delik denetimi için katı geometri gerekli.")
    groups = []
    for i, face in enumerate(shape.Faces, 1):
        surface = face.Surface
        if surface.__class__.__name__ != "Cylinder" or abs(surface.Axis.dot(direction)) < 1 - 1e-7:
            continue
        u0, u1, v0, v1 = face.ParameterRange
        if abs((u1 - u0) - 2 * math.pi) > 1e-6:
            continue  # an open partial cylinder may be a slot or an inner fillet
        u, v = (u0 + u1) / 2, (v0 + v1) / 2
        point = face.valueAt(u, v)
        radial = point - surface.Center
        radial -= direction * radial.dot(direction)
        if face.normalAt(u, v).dot(radial) >= 0:
            continue  # convex boss/shaft, not a bore
        center = surface.Center - direction * surface.Center.dot(direction)
        ends = [face.valueAt(u, t).dot(direction) for t in (v0, v1)]
        lo, hi = min(ends), max(ends)
        groups.append({"center": center, "lo": lo, "hi": hi, "diameters": [2 * surface.Radius],
                       "faces": [f"Face{i}"]})
    # Adjacent coaxial sections (e.g. counterbores) belong to one bore. Distinct blind holes stay distinct.
    merged = []
    for group in groups:
        changed = True
        while changed:
            changed = False
            for other in list(merged):
                if ((group["center"] - other["center"]).Length < 1e-6
                        and group["lo"] <= other["hi"] + 1e-6 and other["lo"] <= group["hi"] + 1e-6):
                    group = {"center": group["center"], "lo": min(group["lo"], other["lo"]),
                             "hi": max(group["hi"], other["hi"]), "diameters": group["diameters"] + other["diameters"],
                             "faces": group["faces"] + other["faces"]}
                    merged.remove(other)
                    changed = True
        merged.append(group)
    return [{"center": [g["center"].x, g["center"].y, g["center"].z], "axis": axis,
             "range_mm": [g["lo"], g["hi"]], "diameters_mm": sorted(set(g["diameters"])), "faces": g["faces"]}
            for g in merged]
