"""Does a local model manage CadAI's tools? Real FreeCAD, a real model, Turkish requests, results checked on the
geometry (volumes, sizes), not on the model's words.

    set CADAI_EVAL_MODEL=qwen3:8b
    "C:\\Program Files\\FreeCAD 1.1\\bin\\freecadcmd.exe" freecad\\CadAI\\tests\\eval_local_model.py

Environment: CADAI_EVAL_MODEL (required), CADAI_EVAL_URL (default http://localhost:11434/v1; Ollama is used through
its native API), CADAI_EVAL_SMALL (auto | on | off | both, default both), CADAI_EVAL_NUM_CTX (default 16384),
CADAI_EVAL_ONLY (comma separated case names), CADAI_EVAL_REPEAT (default 1), CADAI_EVAL_OUT (JSON report path).
Tools run without approval in a throwaway document. Prints one line per case and a summary per mode.
"""

import json
import math
import os
import sys
import tempfile
import time

ADDON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if "__file__" in globals() else os.getcwd()
sys.path.insert(0, ADDON_DIR)

import FreeCAD

import cadai.config

cadai.config.config_path = lambda: os.path.join(tempfile.mkdtemp(prefix="cadai_eval_"), "config.json")

from cadai.agent import Agent
from cadai.providers import ProviderError, make_provider
from cadai.tools import build_registry

REG = build_registry()
PI = math.pi


def say(*parts):
    text = " ".join(str(p) for p in parts)
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


def new_doc(name="Eval"):
    for d in list(FreeCAD.listDocuments()):
        FreeCAD.closeDocument(d)
    return FreeCAD.newDocument(name)


def plate_doc():
    doc = new_doc()
    box = doc.addObject("Part::Box", "Plate")
    box.Length, box.Width, box.Height = 80, 60, 10
    doc.recompute()
    return doc


def beam_doc():
    doc = new_doc()
    box = doc.addObject("Part::Box", "Beam")
    box.Length, box.Width, box.Height = 100, 20, 10
    doc.recompute()
    return doc


def visible_solids(doc):
    out = []
    for o in doc.Objects:
        shape = getattr(o, "Shape", None)
        if getattr(o, "Visibility", True) and shape is not None and not shape.isNull() and shape.Solids:
            out.append(o)
    return out


def has_volume(expected, tol=1.0):
    def check(doc, answer):
        vols = [round(o.Shape.Volume, 1) for o in visible_solids(doc)]
        return any(abs(v - expected) <= tol for v in vols), f"görünen hacimler {vols}, beklenen {expected:.1f}"
    return check


def mentions(*numbers):
    def check(doc, answer):
        text = (answer or "").replace(".", "").replace(" ", "")
        missing = [n for n in numbers if str(n) not in text]
        return not missing, f"yanıtta eksik: {missing}" if missing else "yanıt doğru"
    return check


def box_height(name, value):
    def check(doc, answer):
        obj = doc.getObject(name)
        h = float(obj.Height) if obj is not None else None
        return h is not None and abs(h - value) < 1e-6, f"{name}.Height = {h}"
    return check


def fem_ok(doc, answer):
    an = [o for o in doc.Objects if o.TypeId == "Fem::FemAnalysis"]
    results = [o for o in doc.Objects if o.TypeId.startswith("Fem::FemResult")]
    return bool(an and results), f"analiz {len(an)}, sonuç {len(results)}"


CASES = [
    ("inspect", plate_doc, "Belgede hangi nesneler var, plakanın ölçüleri ne?", mentions(80, 60, 10)),
    ("hole_center", plate_doc, "Plakanın üst yüzünün tam ortasına 8 mm çapında boydan boya delik aç.",
     has_volume(48000 - PI * 16 * 10)),
    ("thickness", plate_doc, "Plakanın kalınlığını 15 mm yap.", box_height("Plate", 15)),
    ("fillet", plate_doc, "Plakanın dört dikey köşe kenarını 5 mm yarıçapla yuvarlat.",
     has_volume(48000 - 4 * (25 - PI * 25 / 4) * 10)),
    ("boss", plate_doc, "Plakanın üstüne, tam ortasına 20 mm çapında, 30 mm yüksekliğinde bir silindir koy ve "
                        "plakayla birleştir.", has_volume(48000 + PI * 100 * 30)),
    ("corner_holes", plate_doc, "Plakanın dört köşesine, iki kenardan da 10 mm içeride, 6 mm çapında boydan boya "
                                "delik aç.", has_volume(48000 - 4 * PI * 9 * 10)),
    ("fem", beam_doc, "Kirişin sol ucu (x=0 yüzü) sabit, sağ ucuna (x=100 yüzü) 500 N aşağı (-Z) kuvvet uygula, malzeme "
                      "çelik. Analizi çalıştır ve en büyük gerilmeyi söyle.", fem_ok),
]


def run_case(profile, small_mode, case):
    name, setup, prompt, check = case
    doc = setup()
    provider = make_provider(profile)
    agent = Agent(REG)
    events = []
    t0 = time.time()
    error = None
    try:
        agent.run(provider, prompt, emit=events.append, max_steps=15, small_mode=small_mode)
    except ProviderError as e:
        error = str(e)
    seconds = time.time() - t0
    calls = [e[1] for e in events if e[0] == "tool_call"]
    results = [e[2] for e in events if e[0] == "tool_result"]
    answer = "\n".join(e[1] for e in events if e[0] == "assistant")
    ok, detail = check(FreeCAD.ActiveDocument or doc, answer) if not error else (False, error)
    return {"case": name, "small": small_mode, "ok": ok, "detail": detail, "seconds": round(seconds, 1),
            "tool_calls": [c.name for c in calls], "tool_errors": sum(1 for r in results if r.is_error),
            "infos": [e[1] for e in events if e[0] == "info"], "answer": answer[-400:]}


def main():
    model = os.environ.get("CADAI_EVAL_MODEL")
    if not model:
        say("CADAI_EVAL_MODEL ortam değişkenini verin (ör. qwen3:8b).")
        return
    profile = {"name": "eval", "kind": "openai", "base_url": os.environ.get("CADAI_EVAL_URL",
                                                                            "http://localhost:11434/v1"),
               "model": model, "api_key": "ollama", "vision": False,
               "num_ctx": int(os.environ.get("CADAI_EVAL_NUM_CTX", "16384"))}
    which = os.environ.get("CADAI_EVAL_SMALL", "both").lower()
    modes = {"both": [True, False], "on": [True], "off": [False], "auto": [cadai.config.is_small_model(profile)]}[which]
    only = {c.strip() for c in os.environ.get("CADAI_EVAL_ONLY", "").split(",") if c.strip()}
    repeat = int(os.environ.get("CADAI_EVAL_REPEAT", "1"))
    report = []
    for small_mode in modes:
        for case in CASES:
            if only and case[0] not in only:
                continue
            for _ in range(repeat):
                res = run_case(profile, small_mode, case)
                report.append(res)
                say(f"{'OK  ' if res['ok'] else 'FAIL'} {'small' if small_mode else 'full '} {res['case']:<13} "
                    f"{res['seconds']:>6}s  araç {len(res['tool_calls'])} hata {res['tool_errors']}  {res['detail']}"
                    f"  [{' '.join(res['tool_calls'])}]")
    say(f"\nModel: {model}")
    for small_mode in modes:
        rows = [r for r in report if r["small"] == small_mode]
        if rows:
            ok = sum(r["ok"] for r in rows)
            say(f"{'küçük model modu' if small_mode else 'tam araç seti   '}: {ok}/{len(rows)} başarılı, "
                f"{sum(r['tool_errors'] for r in rows)} araç hatası, {sum(r['seconds'] for r in rows):.0f} s")
    out = os.environ.get("CADAI_EVAL_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"model": model, "results": report}, f, ensure_ascii=False, indent=1)


main()
