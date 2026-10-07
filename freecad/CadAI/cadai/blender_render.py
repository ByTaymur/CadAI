"""Runs inside Blender (not FreeCAD): blender -b --factory-startup --python blender_render.py -- job.json

Builds the scene from CadAI's job (meshes.npz in metres, Principled materials, camera direction), lights it with
Blender's bundled studio HDRI plus a key area light, puts a shadow catcher under the part and renders with Cycles
on the best GPU (OptiX/CUDA/HIP/oneAPI/Metal, CPU otherwise) to a transparent PNG; CadAI composites the backdrop.
With frames > 0 the camera orbits the part (turntable) and every frame is written.
Writes result.json: device, samples, files.
"""

import json
import math
import os
import re
import sys

import bpy
import numpy as np
from mathutils import Vector

INTEGRATED = re.compile(r"Radeon\(TM\) Graphics|Radeon Graphics$|UHD|Iris|Intel\(R\) Graphics|Intel\(R\) Arc\(TM\) Graphics")


def srgb_to_linear(c):
    return [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]


def setup_device(scene):
    scene.cycles.device = "CPU"
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except KeyError:
        return "CPU"
    for kind in ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"):
        try:
            prefs.compute_device_type = kind
        except TypeError:
            continue
        prefs.get_devices()
        gpus = [d for d in prefs.devices if d.type == kind]
        discrete = [d for d in gpus if not INTEGRATED.search(d.name)]
        use = discrete or gpus
        if not use:
            continue
        for d in prefs.devices:
            d.use = d in use
        scene.cycles.device = "GPU"
        return kind + ": " + ", ".join(d.name for d in use)
    return "CPU"


def set_input(node, names, value):
    for name in names:
        if name in node.inputs:
            node.inputs[name].default_value = value
            return


def make_material(name, spec, bevel_radius):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    set_input(bsdf, ["Base Color"], (*srgb_to_linear(spec["color"]), 1.0))
    set_input(bsdf, ["Metallic"], spec["metallic"])
    set_input(bsdf, ["Roughness"], spec["roughness"])
    if spec.get("transmission"):
        set_input(bsdf, ["Transmission Weight", "Transmission"], spec["transmission"])
        set_input(bsdf, ["IOR"], 1.45)
    if spec["preset"] in ("pcb", "glossy_plastic"):
        set_input(bsdf, ["Coat Weight", "Clearcoat"], 0.6)
    bevel = nodes.new("ShaderNodeBevel")  # rounds the razor-sharp CAD edges a little: they catch the light
    bevel.inputs["Radius"].default_value = bevel_radius
    bevel.samples = 6
    mat.node_tree.links.new(bevel.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def main():
    job_path = sys.argv[sys.argv.index("--") + 1]
    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    data = np.load(job["mesh_file"])

    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    corners = []
    for i in range(len(job["objects"])):
        v = data[f"v{i}"]
        a, b = v.min(axis=0), v.max(axis=0)
        corners += [(x, y, z) for x in (a[0], b[0]) for y in (a[1], b[1]) for z in (a[2], b[2])]
        lo, hi = np.minimum(lo, a), np.maximum(hi, b)
    center = Vector(((lo + hi) / 2).tolist())
    radius = float(np.linalg.norm(hi - lo) / 2) or 0.01
    bevel_radius = min(max(radius * 0.004, 0.0002), 0.002)

    for i, item in enumerate(job["objects"]):
        v, t = data[f"v{i}"], data[f"t{i}"]
        me = bpy.data.meshes.new(item["name"])
        me.from_pydata(v.tolist(), [], t.tolist())
        me.update()
        try:
            me.shade_smooth()
        except AttributeError:
            me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
        ob = bpy.data.objects.new(item["name"], me)
        scene.collection.objects.link(ob)
        me.materials.append(make_material(item["name"], item["material"], bevel_radius))

    # floor: shadow catcher at the lowest point (transparent in the PNG except for the shadows)
    bpy.ops.mesh.primitive_plane_add(size=radius * 60, location=(center.x, center.y, float(lo[2])))
    floor = bpy.context.active_object
    floor.is_shadow_catcher = True

    # world: Blender's bundled studio HDRI (light + reflections only; the film is transparent)
    world = bpy.data.worlds.new("Studio")
    scene.world = world
    world.use_nodes = True
    wn = world.node_tree.nodes
    bg = next(n for n in wn if n.type == "BACKGROUND")
    hdri = os.path.join(bpy.utils.system_resource("DATAFILES"), "studiolights", "world", "studio.exr")
    if os.path.isfile(hdri):
        env = wn.new("ShaderNodeTexEnvironment")
        env.image = bpy.data.images.load(hdri)
        world.node_tree.links.new(env.outputs["Color"], bg.inputs["Color"])
        bg.inputs["Strength"].default_value = 0.45
    else:
        bg.inputs["Color"].default_value = (0.6, 0.6, 0.6, 1)

    d = Vector(job["direction"]).normalized()
    right = Vector((0, 0, 1)).cross(d) if abs(d.z) < 0.99 else Vector((1, 0, 0))
    right.normalize()
    up = d.cross(right)

    def area_light(name, direction, dist, size, irradiance):
        """Area light aimed at the centre; power so the part gets about `irradiance` W/m² (E ≈ P / (π d²))."""
        data = bpy.data.lights.new(name, "AREA")
        data.size = size
        data.energy = irradiance * math.pi * dist * dist
        ob = bpy.data.objects.new(name, data)
        ob.location = center + direction * dist
        ob.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()
        ob.visible_camera = False
        scene.collection.objects.link(ob)

    # key light from the upper left (crisp shadow) + a big softbox where the camera's view of the top faces
    # reflects to (behind, above), so flat metal faces show a bright reflection instead of a dark sky
    area_light("Key", (0.55 * d - 0.75 * right + Vector((0, 0, 1.1))).normalized(), radius * 5, radius * 2.5, 3.0)
    area_light("Softbox", Vector((-d.x, -d.y, max(d.z, 0.35))).normalized(), radius * 4, radius * 6, 1.5)

    # camera: perspective, the closest distance at which every corner of every part is inside the frame
    cam_data = bpy.data.cameras.new("Camera")
    cam_data.lens = 70
    cam_data.sensor_width = 36
    w, h = job["width"], job["height"]
    fov = 2 * math.atan(18 / cam_data.lens)
    tan_h = math.tan(fov / 2) if w >= h else math.tan(fov / 2) * w / h
    tan_v = math.tan(fov / 2) if h >= w else math.tan(fov / 2) * h / w
    distance = 0.0
    for c in corners:
        p = Vector(c) - center
        distance = max(distance, p.dot(d) + abs(p.dot(right)) / tan_h, p.dot(d) + abs(p.dot(up)) / tan_v)
    distance *= 1.08
    cam_data.clip_start = distance * 0.01
    cam_data.clip_end = distance * 10 + radius * 100
    cam = bpy.data.objects.new("Camera", cam_data)
    pivot = bpy.data.objects.new("Pivot", None)
    pivot.location = center
    scene.collection.objects.link(pivot)
    scene.collection.objects.link(cam)
    cam.parent = pivot
    cam.location = d * distance
    cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    scene.render.engine = "CYCLES"
    device = setup_device(scene)
    scene.cycles.samples = job["samples"]
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.use_denoising = True
    scene.render.film_transparent = True
    scene.render.resolution_x, scene.render.resolution_y = w, h
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    try:
        scene.view_settings.view_transform = "AgX"
        scene.view_settings.look = "AgX - Medium High Contrast"
    except TypeError:
        pass

    files = []
    frames = job["frames"]
    for i in range(max(frames, 1)):
        pivot.rotation_euler = (0, 0, 2 * math.pi * i / frames if frames else 0)
        path = os.path.join(job["out_dir"], f"frame_{i:04d}.png")
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        files.append(path)
    with open(job["result_file"], "w", encoding="utf-8") as f:
        json.dump({"device": device, "samples": job["samples"], "files": files,
                   "blender": bpy.app.version_string}, f)


main()
