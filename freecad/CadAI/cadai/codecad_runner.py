"""Runs in a separate Python that has build123d or CadQuery (never inside FreeCAD):

    python codecad_runner.py job.json

job.json: {"language": "build123d"|"cadquery", "code_file", "params": {...}, "step_file", "result_file"}.
Top-level assignments of a parameter name are replaced by the given value (like OpenSCAD's -D), so
`width = 40` in the code becomes `width = <params["width"]>`. The result is taken from a variable called result /
part / model / assembly, from show_object(...) calls (CQ-editor style), or else from the last shape the code
assigned. It is written as STEP (exact B-rep) for FreeCAD; result.json reports what was found, the printed output
and the error with its line number.
"""

import ast
import contextlib
import io
import json
import sys
import traceback

RESULT_NAMES = ("result", "part", "model", "assembly", "assy", "shape", "obj")


def apply_params(tree, params):
    """Replace the value of top-level `name = ...` (also `a, b = 1, 2`) for every name in params."""
    applied = set()

    def const(name):
        applied.add(name)
        return ast.parse(repr(params[name]), mode="eval").body

    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if len(targets) != 1:
            continue
        target = targets[0]
        if isinstance(target, ast.Name) and target.id in params:
            node.value = const(target.id)
        elif (isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple)
              and len(target.elts) == len(node.value.elts)):
            node.value.elts = [const(t.id) if isinstance(t, ast.Name) and t.id in params else v
                               for t, v in zip(target.elts, node.value.elts)]
    ast.fix_missing_locations(tree)
    return applied


def to_occ(value):
    """build123d / CadQuery object (or a list of them) -> OCP TopoDS_Shape, or None."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        shapes = [s for s in (to_occ(v) for v in value) if s is not None]
        if not shapes:
            return None
        if len(shapes) == 1:
            return shapes[0]
        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS_Compound

        comp, builder = TopoDS_Compound(), BRep_Builder()
        builder.MakeCompound(comp)
        for s in shapes:
            builder.Add(comp, s)
        return comp
    cls = type(value).__name__
    if cls in ("BuildPart", "BuildSketch", "BuildLine"):  # build123d builders
        return to_occ(getattr(value, {"BuildPart": "part", "BuildSketch": "sketch", "BuildLine": "line"}[cls]))
    if cls == "Workplane":  # CadQuery
        return to_occ(value.vals() if len(value.vals()) > 1 else value.val())
    if cls == "Assembly" and hasattr(value, "toCompound"):
        return to_occ(value.toCompound())
    wrapped = getattr(value, "wrapped", None)
    if wrapped is not None and type(wrapped).__name__.startswith("TopoDS"):
        return wrapped
    if type(value).__name__.startswith("TopoDS"):
        return value
    return None


def write_step(shape, path):
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    writer = STEPControl_Writer()
    Interface_Static.SetCVal_s("write.step.unit", "MM")
    writer.Transfer(shape, STEPControl_AsIs)
    if writer.Write(path) != IFSelect_RetDone:
        raise RuntimeError("STEP yazılamadı")


def main():
    with open(sys.argv[1], encoding="utf-8") as f:
        job = json.load(f)
    out = {"ok": False}
    stdout = io.StringIO()
    try:
        with open(job["code_file"], encoding="utf-8") as f:
            code = f.read()
        tree = ast.parse(code, filename="<cadai>")
        params = job.get("params") or {}
        applied = apply_params(tree, params)
        shown = []
        ns = {"__name__": "__main__", "params": params,
              "show_object": lambda obj, *a, **k: shown.append(obj), "show": lambda *objs, **k: shown.extend(objs)}
        if job["language"] == "build123d":
            exec("from build123d import *", ns)
        else:
            exec("import cadquery as cq", ns)
        for k, v in params.items():
            if k not in applied:
                ns[k] = v
        before = set(ns)
        with contextlib.redirect_stdout(stdout):
            exec(compile(tree, "<cadai>", "exec"), ns)
        source, value = None, None
        for name in RESULT_NAMES:
            if to_occ(ns.get(name)) is not None:
                source, value = name, ns[name]
                break
        if value is None and shown:
            source, value = "show_object", shown
        if value is None:
            for name in reversed([k for k in ns if k not in before and not k.startswith("_")]):
                if to_occ(ns[name]) is not None:
                    source, value = name, ns[name]
                    break
        shape = to_occ(value)
        if shape is None:
            raise RuntimeError("Kod bir şekil üretmedi. Sonucu 'result' adlı değişkene ata (ya da show_object(...)).")
        write_step(shape, job["step_file"])
        out = {"ok": True, "result_from": source, "params_applied": sorted(applied),
               "params_injected": sorted(set(params) - applied)}
    except SyntaxError as e:
        out["error"] = f"Sözdizimi hatası, satır {e.lineno}: {e.msg}\n{(e.text or '').rstrip()}"
    except Exception as e:
        frames = [f for f in traceback.extract_tb(e.__traceback__) if f.filename == "<cadai>"]
        where = f"satır {frames[-1].lineno}: " if frames else ""
        out["error"] = f"{where}{type(e).__name__}: {e}"
        out["traceback"] = traceback.format_exc(limit=6)[-2500:]
    out["stdout"] = stdout.getvalue()[-4000:]
    with open(job["result_file"], "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
