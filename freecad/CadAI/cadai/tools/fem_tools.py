"""FEM tools on top of FreeCAD's FEM workbench: Gmsh meshing + CalculiX solving.

Verified on FreeCAD 1.1.1: a 100x20x10 mm steel cantilever with 500 N at the tip gives
0.4745 mm deflection and 149.4 MPa (hand calculation: 0.476 mm, 150 MPa).
"""

import multiprocessing
import os
import re
import subprocess
import tempfile
import threading
import time

import FreeCAD

from . import Tool, ToolError
from .geometry import active_doc, get_object, get_shape, r, vec
from .inspect_tools import fem_target
from .model_tools import transaction

MATERIALS = {
    "steel": {"Name": "Steel (S235)", "YoungsModulus": "210000 MPa", "PoissonRatio": "0.30", "Density": "7850 kg/m^3"},
    "stainless_steel": {"Name": "Stainless steel", "YoungsModulus": "193000 MPa", "PoissonRatio": "0.29",
                        "Density": "8000 kg/m^3"},
    "aluminum": {"Name": "Aluminium 6061", "YoungsModulus": "69000 MPa", "PoissonRatio": "0.33",
                 "Density": "2700 kg/m^3"},
    "titanium": {"Name": "Titanium Ti-6Al-4V", "YoungsModulus": "114000 MPa", "PoissonRatio": "0.34",
                 "Density": "4430 kg/m^3"},
    "pla": {"Name": "PLA", "YoungsModulus": "3500 MPa", "PoissonRatio": "0.36", "Density": "1240 kg/m^3"},
    "abs": {"Name": "ABS", "YoungsModulus": "2200 MPa", "PoissonRatio": "0.35", "Density": "1050 kg/m^3"},
}


def _analysis_name(obj):
    return f"CadAI_Analysis_{obj.Name}"


def _remove_analysis(doc, name):
    an = doc.getObject(name)
    if an is not None:
        for o in list(an.Group):
            if doc.getObject(o.Name) is not None:
                doc.removeObject(o.Name)
        doc.removeObject(name)
    for o in list(doc.Objects):
        if o.Name.startswith(name + "_Dir"):
            doc.removeObject(o.Name)


def _check_faces(shape, faces):
    names = [f"Face{i}" for i in range(1, len(shape.Faces) + 1)]
    bad = [f for f in faces if f not in names]
    if bad:
        raise ToolError(f"Geçersiz yüz adları: {bad}. Nesnede Face1..Face{len(names)} var; find_faces kullan.")


def _direction_helper(doc, base_name, idx, direction):
    """FreeCAD's force direction needs a reference edge; create a hidden line along the requested vector."""
    import Part

    v = FreeCAD.Vector(*direction)
    if v.Length == 0:
        raise ToolError("Kuvvet yönü sıfır vektör olamaz.")
    helper = doc.addObject("Part::Feature", f"{base_name}_Dir{idx}")
    helper.Shape = Part.makeLine(FreeCAD.Vector(0, 0, 0), v)
    if hasattr(helper, "Visibility"):
        helper.Visibility = False
    return helper


def fem_setup(object, fixed_faces, forces=None, pressures=None, material="steel", custom_material=None,
              mesh_size_mm=None, analysis_type="static", modes=6):
    import ObjectsFem

    doc = active_doc()
    obj = fem_target(get_object(object, doc))
    shape = get_shape(obj)
    if not shape.Solids:
        raise ToolError(f"{obj.Name} katı (solid) değil; FEM için katı gerekir.")
    forces = forces or []
    pressures = pressures or []
    _check_faces(shape, fixed_faces)
    for item in forces + pressures:
        _check_faces(shape, item.get("faces", []))
    if analysis_type == "static" and not forces and not pressures:
        raise ToolError("Statik analiz için en az bir kuvvet ya da basınç gerekli.")
    if custom_material:
        mat_props = {"Name": custom_material.get("name", "Custom"),
                     "YoungsModulus": f"{custom_material['youngs_modulus_mpa']} MPa",
                     "PoissonRatio": str(custom_material.get("poisson_ratio", 0.3)),
                     "Density": f"{custom_material.get('density_kg_m3', 7850)} kg/m^3"}
    elif material in MATERIALS:
        mat_props = MATERIALS[material]
    else:
        raise ToolError(f"Bilinmeyen malzeme {material!r}. Seçenekler: {list(MATERIALS)} ya da custom_material.")
    if mesh_size_mm is None:
        b = shape.BoundBox
        mesh_size_mm = max(min(b.XLength, b.YLength, b.ZLength) / 3.0, 0.5)

    name = _analysis_name(obj)
    with transaction(doc, "CadAI: FEM kurulumu"):
        _remove_analysis(doc, name)
        an = ObjectsFem.makeAnalysis(doc, name)
        solver = ObjectsFem.makeSolverCalculiXCcxTools(doc, name + "_Solver")
        solver.AnalysisType = "frequency" if analysis_type == "frequency" else "static"
        if analysis_type == "frequency":
            solver.EigenmodesCount = int(modes)
        an.addObject(solver)

        mat = ObjectsFem.makeMaterialSolid(doc, name + "_Material")
        m = mat.Material
        m.update(mat_props)
        mat.Material = m
        an.addObject(mat)

        if fixed_faces:
            fix = ObjectsFem.makeConstraintFixed(doc, name + "_Fixed")
            fix.References = [(obj, f) for f in fixed_faces]
            an.addObject(fix)

        for i, item in enumerate(forces, 1):
            fc = ObjectsFem.makeConstraintForce(doc, f"{name}_Force{i}")
            fc.References = [(obj, f) for f in item["faces"]]
            fc.Force = f"{float(item['force_n'])} N"
            helper = _direction_helper(doc, name, i, item["direction"])
            fc.Direction = (helper, ["Edge1"])
            fc.Reversed = False
            an.addObject(fc)

        for i, item in enumerate(pressures, 1):
            pc = ObjectsFem.makeConstraintPressure(doc, f"{name}_Pressure{i}")
            pc.References = [(obj, f) for f in item["faces"]]
            pc.Pressure = f"{float(item['pressure_mpa'])} MPa"
            pc.Reversed = bool(item.get("outward", False))
            an.addObject(pc)

        mesh = ObjectsFem.makeMeshGmsh(doc, name + "_Mesh")
        mesh.Shape = obj
        mesh.CharacteristicLengthMax = f"{float(mesh_size_mm)} mm"
        mesh.ElementOrder = "2nd"
        an.addObject(mesh)
        doc.recompute()

    return {"ok": True, "analysis": an.Name, "target": obj.Name, "analysis_type": solver.AnalysisType,
            "material": mat_props, "fixed_faces": fixed_faces,
            "forces": [{"faces": f["faces"], "force_n": f["force_n"],
                        "direction": vec(doc.getObject(f"{name}_Force{i}").DirectionVector, 4)}
                       for i, f in enumerate(forces, 1)],
            "pressures": pressures, "mesh_size_mm": r(mesh_size_mm, 3),
            "next": "fem_run ile çöz."}


def _percentile(values, q):
    try:
        import numpy  # bundled with FreeCAD; linear interpolation like every engineering tool

        return float(numpy.percentile(numpy.asarray(values, dtype=float), q * 100)) if len(values) else 0.0
    except ImportError:
        s = sorted(values)
        if not s:
            return 0.0
        k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
        return s[k]


_TOTAL_FORCE = re.compile(r"total force \(fx,fy,fz\) for set (\S+) and time\s+\S+\s*\n\s*(\S+)\s+(\S+)\s+(\S+)")


def parse_reaction_totals(dat_text):
    """Reaction force totals per node set from a CalculiX .dat file (*NODE PRINT, TOTALS=ONLY / RF).
    Returns {set_name: [fx, fy, fz]} in N; the last time step wins."""
    out = {}
    for m in _TOTAL_FORCE.finditer(dat_text or ""):
        try:
            out[m.group(1)] = [float(v.replace("D", "E")) for v in m.group(2, 3, 4)]
        except ValueError:
            continue
    return out


def _applied_loads(an):
    """Resultant of the analysis' force and pressure constraints (N)."""
    total = FreeCAD.Vector(0, 0, 0)
    for o in an.Group:
        if o.TypeId == "Fem::ConstraintForce":
            total += FreeCAD.Vector(o.DirectionVector).normalize() * o.Force.getValueAs("N").Value
        elif o.TypeId == "Fem::ConstraintPressure":
            p = o.Pressure.getValueAs("MPa").Value
            sign = 1.0 if o.Reversed else -1.0  # default: pressure pushes into the face (against its normal)
            for obj, subs in o.References:
                for sub in subs:
                    face = obj.Shape.getElement(sub)
                    pts, tris = face.tessellate(0.05)
                    for i, j, k in tris:
                        n = (pts[j] - pts[i]).cross(pts[k] - pts[i]) * 0.5  # area-weighted normal
                        if face.Orientation == "Reversed":
                            n = -n
                        total += n * (sign * p)
    return total


def force_balance(an, dat_path):
    """Equilibrium check: support reactions must cancel the applied loads. Catches wrong directions, missing
    supports and unit mistakes that still give a plausible-looking stress plot."""
    try:
        with open(dat_path, encoding="utf-8", errors="replace") as f:
            totals = parse_reaction_totals(f.read())
    except OSError:
        return None
    if not totals:
        return None
    reaction = FreeCAD.Vector(0, 0, 0)
    for fx, fy, fz in totals.values():
        reaction += FreeCAD.Vector(fx, fy, fz)
    applied = _applied_loads(an)
    scale = max(applied.Length, reaction.Length, 1e-9)
    imbalance = (applied + reaction).Length / scale * 100.0
    return {"applied_n": vec(applied, 3), "reaction_n": vec(reaction, 3), "imbalance_pct": r(imbalance, 3),
            "ok": imbalance < 1.0,
            "meaning": "Mesnet tepkileri uygulanan yükü dengelemeli (toplam ≈ 0). %1'i aşan dengesizlik yanlış yön, "
                       "eksik mesnet ya da birim hatası işaretidir."}


_JOBS = {}


def _run_ccx(fea):
    """Run the CalculiX binary for an already written .inp file. Safe to call from a worker thread."""
    work_dir = os.path.dirname(fea.inp_file_name)
    base = os.path.splitext(os.path.basename(fea.inp_file_name))[0]
    env = dict(os.environ, OMP_NUM_THREADS=str(multiprocessing.cpu_count()))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.run([fea.ccx_binary, "-i", base], cwd=work_dir, env=env, capture_output=True,
                       creationflags=flags)
    return p.returncode, p.stdout.decode(errors="replace"), p.stderr.decode(errors="replace")


def _prepare(analysis):
    from femmesh.gmshtools import GmshTools
    from femtools import ccxtools

    doc = active_doc()
    an = get_object(analysis, doc)
    if an.TypeId != "Fem::FemAnalysis":
        raise ToolError(f"{an.Name} bir FEM analizi değil.")
    solver = next((o for o in an.Group if hasattr(o, "AnalysisType") and hasattr(o, "WorkingDir")), None)
    mesh = next((o for o in an.Group if hasattr(o, "CharacteristicLengthMax")), None)
    if solver is None or mesh is None:
        raise ToolError("Analizde CalculiX çözücüsü ya da Gmsh mesh'i yok; önce fem_setup çalıştır.")

    err = GmshTools(mesh).create_mesh()
    if err:
        raise ToolError(f"Gmsh mesh oluşturamadı: {err}")
    node_count = mesh.FemMesh.NodeCount

    solver.WorkingDir = tempfile.mkdtemp(prefix="cadai_ccx_")
    fea = ccxtools.FemToolsCcx(an, solver)
    fea.update_objects()
    fea.setup_working_dir()
    fea.setup_ccx()
    msg = fea.check_prerequisites()
    if msg:
        raise ToolError(f"Analiz eksik: {msg}")
    if not fea.ccx_binary_present:
        raise ToolError(f"CalculiX (ccx) bulunamadı: {fea.ccx_binary}. FEM tercihlerinden yolunu ayarla.")
    fea.purge_results()
    fea.write_inp_file()
    if not fea.inp_file_name:
        raise ToolError("CalculiX girdi dosyası yazılamadı.")
    return doc, an, solver, fea, node_count


def _finish(doc, an, solver, fea, node_count, code, stdout, stderr):
    if code:
        raise ToolError(f"CalculiX hata kodu {code} ile bitti.\nstderr:\n{stderr[-1500:]}\nstdout (son):\n"
                        f"{stdout[-1500:]}")
    fea.load_results()
    doc.recompute()

    results = [o for o in an.Group if o.isDerivedFrom("Fem::FemResultObject")]
    if not results:
        raise ToolError(f"CalculiX sonuç üretmedi. Çalışma klasörü: {solver.WorkingDir}")
    out = {"ok": True, "analysis": an.Name, "nodes": node_count, "working_dir": solver.WorkingDir,
           "units": "mm, MPa, Hz"}
    if solver.AnalysisType == "frequency":
        freqs = sorted(r(o.EigenmodeFrequency, 3) for o in results if getattr(o, "EigenmodeFrequency", 0))
        out["frequencies_hz"] = freqs
        return out

    res = results[0]
    nodes = res.Mesh.FemMesh.Nodes
    balance = force_balance(an, os.path.splitext(fea.inp_file_name)[0] + ".dat")
    if balance:
        out["force_balance"] = balance
    vm = list(res.vonMises)
    disp = list(res.DisplacementLengths)
    i_vm = max(range(len(vm)), key=vm.__getitem__)
    i_d = max(range(len(disp)), key=disp.__getitem__)
    out.update({
        "max_displacement_mm": r(disp[i_d], 5),
        "max_displacement_at": vec(nodes[res.NodeNumbers[i_d]], 2),
        "max_displacement_vector": vec(res.DisplacementVectors[i_d], 5),
        "max_von_mises_mpa": r(vm[i_vm], 3),
        "max_von_mises_at": vec(nodes[res.NodeNumbers[i_vm]], 2),
        "von_mises_p99_mpa": r(_percentile(vm, 0.99), 3),
        "max_principal_mpa": r(max(res.PrincipalMax), 3),
        "min_principal_mpa": r(min(res.PrincipalMin), 3),
        "note": "Mesnet kenar/köşelerindeki tepe gerilme sayısal tekillik olabilir; p99 değerine ve el hesabına bak. "
                "Mesh yakınsaması için mesh_size_mm küçültülüp tekrar çözülmeli.",
    })
    return out


def fem_run(analysis, background=False):
    doc, an, solver, fea, node_count = _prepare(analysis)
    if not background:
        return _finish(doc, an, solver, fea, node_count, *_run_ccx(fea))

    job_id = f"job{len(_JOBS) + 1}"
    job = {"analysis": an.Name, "started": time.time(), "done": False, "result": None,
           "ctx": (doc, an, solver, fea, node_count)}

    def work():
        try:
            job["result"] = _run_ccx(fea)
        except Exception as e:
            job["result"] = (-1, "", f"{type(e).__name__}: {e}")
        job["done"] = True

    _JOBS[job_id] = job
    threading.Thread(target=work, name=f"cadai-{job_id}", daemon=True).start()
    return {"ok": True, "job": job_id, "status": "running", "nodes": node_count,
            "next": f"fem_status(job='{job_id}') ile birkaç saniyede bir sonucu sor."}


def fem_status(job):
    j = _JOBS.get(job)
    if j is None:
        raise ToolError(f"Böyle bir iş yok: {job}. Bilinen işler: {list(_JOBS)}")
    if not j["done"]:
        return {"job": job, "status": "running", "elapsed_s": round(time.time() - j["started"], 1)}
    if "summary" not in j:
        try:
            j["summary"] = _finish(*j["ctx"], *j["result"])  # load results on the GUI thread
        except ToolError as e:
            j["summary"] = {"ok": False, "error": str(e)}
        j["summary"]["elapsed_s"] = round(time.time() - j["started"], 1)
    return dict(j["summary"], job=job, status="finished")


def _analysis_parts(analysis):
    doc = active_doc()
    an = get_object(analysis, doc)
    if an.TypeId != "Fem::FemAnalysis":
        raise ToolError(f"{an.Name} bir FEM analizi değil.")
    mesh = next((o for o in an.Group if hasattr(o, "CharacteristicLengthMax")), None)
    solver = next((o for o in an.Group if hasattr(o, "AnalysisType") and hasattr(o, "WorkingDir")), None)
    if mesh is None or solver is None:
        raise ToolError("Analizde Gmsh mesh'i ya da çözücü yok; önce fem_setup çalıştır.")
    return doc, an, mesh, solver


def _change_pct(a, b):
    return r(abs(b - a) / max(abs(b), 1e-12) * 100.0, 2)


def fem_convergence(analysis, levels=3, ratio=0.7):
    """Solve the same analysis on successively finer meshes and report how much the results still change."""
    doc, an, mesh, solver = _analysis_parts(analysis)
    levels = max(2, min(int(levels), 4))
    ratio = max(0.4, min(float(ratio), 0.9))
    h0 = mesh.CharacteristicLengthMax.getValueAs("mm").Value
    frequency = solver.AnalysisType == "frequency"
    rows = []
    for i in range(levels):
        h = h0 * ratio ** i
        mesh.CharacteristicLengthMax = f"{h} mm"
        res = fem_run(an.Name)
        row = {"mesh_size_mm": r(h, 3), "nodes": res["nodes"]}
        if frequency:
            row["f1_hz"] = res["frequencies_hz"][0] if res.get("frequencies_hz") else None
        else:
            row.update({k: res[k] for k in ("max_displacement_mm", "von_mises_p99_mpa", "max_von_mises_mpa")})
        rows.append(row)
    a, b = rows[-2], rows[-1]
    out = {"analysis": an.Name, "levels": rows, "final_mesh_size_mm": rows[-1]["mesh_size_mm"]}
    if frequency:
        out["change_pct"] = {"f1": _change_pct(a["f1_hz"], b["f1_hz"])}
        out["converged"] = out["change_pct"]["f1"] < 1.0
    else:
        ch = {"max_displacement": _change_pct(a["max_displacement_mm"], b["max_displacement_mm"]),
              "von_mises_p99": _change_pct(a["von_mises_p99_mpa"], b["von_mises_p99_mpa"]),
              "max_von_mises": _change_pct(a["max_von_mises_mpa"], b["max_von_mises_mpa"])}
        out["change_pct"] = ch
        out["converged"] = ch["max_displacement"] < 2.0 and ch["von_mises_p99"] < 5.0
        peaks = [x["max_von_mises_mpa"] for x in rows]
        if all(q > p * 1.03 for p, q in zip(peaks, peaks[1:])) and ch["von_mises_p99"] < 5.0:
            out["singularity_warning"] = ("Tepe von Mises her inceltmede artmaya devam ediyor ama %99 değeri oturdu: "
                                          "tepe değer büyük olasılıkla mesnet köşesindeki sayısal tekillik. "
                                          "Tasarım için %99 değerini ya da köşeden uzaktaki gerilmeyi kullan.")
    out["note"] = ("Yakınsadı: sonuçlar mesh'ten bağımsız." if out["converged"]
                   else "Yakınsamadı: mesh'i daha da incelt (mesh_size_mm küçült) ya da kritik bölgede yerel incelt.")
    return out


def _results(an):
    results = [o for o in an.Group if o.isDerivedFrom("Fem::FemResultObject")]
    if not results:
        raise ToolError("Analizin sonucu yok; önce fem_run ile çöz.")
    return results


# 6-node (2nd order) triangle -> 4 linear triangles: corners 0,1,2 and mid-side nodes 3 (0-1), 4 (1-2), 5 (2-0)
_TRI6 = ((0, 3, 5), (3, 1, 4), (5, 4, 2), (3, 4, 5))


def result_field(analysis=None, quantity="von_mises", mode=1):
    """Nodal FEM results on the part's surface, grouped by geometric face, for the VS Code 3D view color map.
    Returns {faces: [{name, positions, indices, values, displacements}], min, max, p99, unit, ...}."""
    doc = active_doc()
    if analysis:
        an = get_object(analysis, doc)
    else:
        solved = [o for o in doc.Objects if o.TypeId == "Fem::FemAnalysis"
                  and any(c.isDerivedFrom("Fem::FemResultObject") for c in o.Group)]
        if not solved:
            raise ToolError("Belgede çözülmüş FEM analizi yok.")
        an = solved[-1]
    results = _results(an)
    res = results[min(max(int(mode), 1), len(results)) - 1]
    if quantity == "displacement":
        values, unit = list(res.DisplacementLengths), "mm"
    elif quantity == "von_mises":
        values, unit = list(res.vonMises), "MPa"
    else:
        raise ToolError("quantity: von_mises ya da displacement")
    mesh_obj = next((o for o in an.Group if hasattr(o, "CharacteristicLengthMax")), None)
    part = getattr(mesh_obj, "Shape", None) if mesh_obj is not None else None
    if part is None:
        raise ToolError("Analizin parçası bulunamadı.")
    # The result mesh read back from the .frd has volume elements only; the Gmsh mesh object has the same node ids
    # plus the surface triangles per geometric face, so take the surface from it and the values from the result.
    fm = mesh_obj.FemMesh if mesh_obj.FemMesh.FaceCount else res.Mesh.FemMesh
    nodes = res.Mesh.FemMesh.Nodes
    index = {nid: i for i, nid in enumerate(res.NodeNumbers)}
    disp = res.DisplacementVectors
    faces = []
    for fi, face in enumerate(part.Shape.Faces, 1):
        local, positions, vals, dvec, indices = {}, [], [], [], []
        for eid in fm.getFacesByFace(face):
            en = fm.getElementNodes(eid)
            tris = _TRI6 if len(en) == 6 else ((0, 1, 2),) if len(en) == 3 else ()
            for t in tris:
                for k in t:
                    nid = en[k]
                    li = local.get(nid)
                    if li is None:
                        li = local[nid] = len(local)
                        p, j = nodes[nid], index.get(nid)
                        positions += [r(p.x, 4), r(p.y, 4), r(p.z, 4)]
                        vals.append(r(values[j], 5) if j is not None else 0.0)
                        d = disp[j] if j is not None else FreeCAD.Vector()
                        dvec += [r(d.x, 6), r(d.y, 6), r(d.z, 6)]
                    indices.append(li)
        if not indices:
            continue
        # orient the face's triangles outward (one OCCT normal evaluation per face)
        a, b, c = (FreeCAD.Vector(*positions[3 * indices[k]:3 * indices[k] + 3]) for k in range(3))
        n_tri = (b - a).cross(c - a)
        try:
            u, v = face.Surface.parameter((a + b + c) / 3)
            n_face = face.normalAt(u, v)
        except Exception:
            n_face = n_tri
        if n_tri.dot(n_face) < 0:
            for k in range(0, len(indices), 3):
                indices[k + 1], indices[k + 2] = indices[k + 2], indices[k + 1]
        faces.append({"name": f"Face{fi}", "positions": positions, "indices": indices, "values": vals,
                      "displacements": dvec})
    if not faces:
        raise ToolError("Mesh yüzleri parçanın yüzleriyle eşleştirilemedi.")
    out = {"analysis": an.Name, "object": part.Name, "quantity": quantity, "unit": unit,
           "min": r(min(values), 5), "max": r(max(values), 5), "p99": r(_percentile(values, 0.99), 5),
           "max_displacement_mm": r(max(res.DisplacementLengths), 6), "faces": faces}
    if getattr(res, "EigenmodeFrequency", 0):
        out["mode"], out["frequency_hz"] = int(mode), r(res.EigenmodeFrequency, 3)
    return out


FORCE_ITEM = {"type": "object", "properties": {
    "faces": {"type": "array", "items": {"type": "string"}},
    "force_n": {"type": "number", "description": "Total force in newtons, spread over the faces."},
    "direction": {"type": "array", "items": {"type": "number"}, "description": "Force direction [x,y,z]."}},
    "required": ["faces", "force_n", "direction"]}

PRESSURE_ITEM = {"type": "object", "properties": {
    "faces": {"type": "array", "items": {"type": "string"}},
    "pressure_mpa": {"type": "number"},
    "outward": {"type": "boolean", "description": "Default false = pressure pushes into the face."}},
    "required": ["faces", "pressure_mpa"]}

TOOLS = [
    Tool("fem_setup",
         "Create (or replace) a CalculiX FEM analysis for one solid: material, fixed faces, forces, pressures and a "
         "2nd-order Gmsh mesh. Face names come from get_selection or find_faces. analysis_type 'frequency' gives "
         "natural frequencies (no loads needed). Then call fem_run.",
         {"type": "object", "properties": {
             "object": {"type": "string"},
             "fixed_faces": {"type": "array", "items": {"type": "string"}},
             "forces": {"type": "array", "items": FORCE_ITEM},
             "pressures": {"type": "array", "items": PRESSURE_ITEM},
             "material": {"type": "string", "enum": list(MATERIALS)},
             "custom_material": {"type": "object", "properties": {
                 "name": {"type": "string"}, "youngs_modulus_mpa": {"type": "number"},
                 "poisson_ratio": {"type": "number"}, "density_kg_m3": {"type": "number"}},
                 "required": ["youngs_modulus_mpa"]},
             "mesh_size_mm": {"type": "number", "description": "Max element size. Default: smallest bbox side / 3."},
             "analysis_type": {"type": "string", "enum": ["static", "frequency"]},
             "modes": {"type": "integer"}},
          "required": ["object", "fixed_faces"]},
         fem_setup, mutates=True, title="Set up FEM analysis", idempotent=True,
         example={"object": "Beam", "fixed_faces": ["Face1"],
                  "forces": [{"faces": ["Face2"], "force_n": 500, "direction": [0, 0, -1]}], "material": "steel"}),
    Tool("fem_run",
         "Mesh and solve an analysis created by fem_setup, then summarize results: max displacement, max and 99th "
         "percentile von Mises stress with locations, force balance (support reactions vs applied loads), or natural "
         "frequencies. Small parts finish in seconds. For "
         "large models or fine meshes set background=true: it returns a job id at once and you poll fem_status.",
         {"type": "object", "properties": {
             "analysis": {"type": "string"},
             "background": {"type": "boolean", "description": "Run CalculiX in the background (FreeCAD stays responsive)."}},
          "required": ["analysis"]},
         fem_run, mutates=True, title="Solve FEM analysis", destructive=False, idempotent=True),
    Tool("fem_status",
         "Check a background FEM job started with fem_run(background=true). Returns 'running' or the result summary.",
         {"type": "object", "properties": {"job": {"type": "string"}}, "required": ["job"]},
         fem_status, title="FEM job status"),
    Tool("fem_convergence",
         "Mesh convergence study: solve the analysis on 2-4 successively finer meshes (mesh size × ratio each step) "
         "and report how much max displacement, 99th-percentile and peak von Mises (or the first frequency) still "
         "change. Flags a stress singularity when only the peak keeps growing. Use for any result that matters; "
         "it takes a few solves.",
         {"type": "object", "properties": {
             "analysis": {"type": "string"},
             "levels": {"type": "integer", "minimum": 2, "maximum": 4, "description": "Number of meshes (default 3)."},
             "ratio": {"type": "number", "description": "Mesh size factor per level, 0.4-0.9 (default 0.7)."}},
          "required": ["analysis"]},
         fem_convergence, mutates=True, title="Mesh convergence study", destructive=False, idempotent=True),
]
