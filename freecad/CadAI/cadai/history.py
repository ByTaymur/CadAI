"""Optional design history: every change of the model becomes a git commit, with notes that open as an Obsidian vault.

Off by default (config "history_enabled"). When on, each committed FreeCAD transaction — an AI tool call, a VS Code
action or a manual edit in FreeCAD — is snapshotted after a short pause:

    <history repo>/
      model/<Doc>.FCStd        copy of the document (Document.saveCopy; the user's own file is never touched)
      model/model.json         readable state: objects, dimensions, expressions, placements, volumes (diffs well)
      journal/YYYY-MM-DD/…md   one note per change: what changed, from where, links to the parts   (Obsidian)
      parts/<Object>.md        one note per object: current state and every change that touched it
      README.md                vault home: newest changes first
      .cadai/index.json        the history the notes are generated from

and committed with a message like "Beam.Length 100 mm → 120 mm". Where the repository lives:
  * folder setting empty: "<Doc>.cadai-history/" next to the .FCStd (its own repository), or the CadAI user folder
    while the document has never been saved (moved next to the file on the first save);
  * folder setting set: "<folder>/<Doc>/". If that is inside an existing git repository (a developer's project), only
    that sub-folder is committed — nothing else that is staged there is touched. Nothing is ever pushed.
Git runs on a worker thread so FreeCAD does not wait for it.
"""

import datetime
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading

import FreeCAD

from . import config

DEBOUNCE_MS = 1200
_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# "done" counts finished jobs (committed or failed); GET /version exposes it so the VS Code history tree refreshes
STATE = {"pending": [], "source": None, "open": {}, "last_error": None, "commits": 0, "done": 0, "timer": None}
_jobs = queue.Queue()
_memory = {}  # repo path -> {"snap": last recorded snapshot, "index": notes index}; the worker owns the files
_worker = None
_observer = None


# ---------------- settings & locations ----------------

def settings(reload=False):
    if reload or "settings" not in STATE:
        cfg = config.load()
        STATE["settings"] = {"enabled": bool(cfg.get("history_enabled", False)), "folder": cfg.get("history_folder", "") or "",
                             "notes": bool(cfg.get("history_notes", True)), "fcstd": bool(cfg.get("history_fcstd", True))}
    return dict(STATE["settings"])


def configure(enabled=None, folder=None, notes=None, fcstd=None):
    cfg = config.load()
    for key, value in (("history_enabled", enabled), ("history_folder", folder), ("history_notes", notes),
                       ("history_fcstd", fcstd)):
        if value is not None:
            cfg[key] = value
    config.save(cfg)
    if settings(reload=True)["enabled"]:
        install()
    return status()


def _safe(name):
    return re.sub(r'[\\/:*?"<>|#^\[\]]+', "_", str(name)).strip(" .") or "model"


def _stem(doc):
    return os.path.splitext(os.path.basename(doc.FileName))[0] if doc.FileName else _safe(doc.Label or doc.Name)


def repo_dir(doc, folder=None):
    folder = settings()["folder"] if folder is None else folder
    if folder:
        return os.path.join(os.path.abspath(os.path.expanduser(folder)), _safe(_stem(doc)))
    if doc.FileName:
        return os.path.join(os.path.dirname(doc.FileName), _stem(doc) + ".cadai-history")
    return os.path.join(os.path.dirname(config.config_path()), "history", _safe(doc.Name))


# ---------------- git ----------------

def _git(cwd, *args, check=True):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, creationflags=_FLAGS, timeout=120)
    out = p.stdout.decode("utf-8", "replace")
    if check and p.returncode:
        raise RuntimeError(f"git {' '.join(args[:2])}: {p.stderr.decode('utf-8', 'replace').strip()[:500]}")
    return out


def git_available():
    if "git" not in STATE:
        try:
            STATE["git"] = _git(None, "--version").strip()
        except (OSError, RuntimeError, subprocess.SubprocessError):
            STATE["git"] = None
    return STATE["git"]


def _toplevel(path):
    try:
        return os.path.normpath(_git(path, "rev-parse", "--show-toplevel").strip())
    except (OSError, RuntimeError):
        return None


def _repo_top(path):
    """Repository root that holds this history: the folder itself by default (its own repository, even when the
    model sits inside a project repository); with an explicitly chosen folder, the enclosing repository if any."""
    if not settings()["folder"]:
        return os.path.normpath(path) if os.path.isdir(os.path.join(path, ".git")) else None
    return _toplevel(path) if os.path.isdir(path) else None


def _prepare_repo(path):
    """Create the folder and its repository when needed; returns the repository root."""
    os.makedirs(path, exist_ok=True)
    top = _repo_top(path)
    if top is None:
        _git(path, "init", "-q")
        top = os.path.normpath(path)
    for name, text in ((".gitattributes", "*.FCStd binary\n*.fcstd binary\n"),
                       (".gitignore", ".obsidian/workspace*\n.obsidian/cache\n.trash/\n")):
        f = os.path.join(path, name)
        if not os.path.exists(f):
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(text)
    return top


def _identity_args(cwd):
    """Use the user's git identity; fall back to a neutral one so commits never fail on a fresh machine."""
    name = _git(cwd, "config", "user.name", check=False).strip()
    mail = _git(cwd, "config", "user.email", check=False).strip()
    args = []
    if not name:
        args += ["-c", "user.name=CadAI"]
    if not mail:
        args += ["-c", "user.email=cadai@localhost"]
    return args


def _busy(top):
    gitdir = os.path.join(top, ".git")
    return any(os.path.exists(os.path.join(gitdir, n))
               for n in ("MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD"))


def _commit(path, title, body, files, fcstd):
    """Write one snapshot's files and commit them. Runs on the worker thread, one job at a time, so a commit always
    holds exactly the change it describes."""
    top = _prepare_repo(path)
    if _busy(top):
        raise RuntimeError("git deposunda birleştirme/rebase sürüyor; commit atlandı.")
    for rel_file, text in files.items():
        _write(os.path.join(path, rel_file), text)
    if fcstd:
        tmp, rel_file = fcstd
        os.makedirs(os.path.dirname(os.path.join(path, rel_file)), exist_ok=True)
        os.replace(tmp, os.path.join(path, rel_file))
        shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)
    rel = os.path.relpath(path, top)
    _git(top, "add", "-A", "--", rel)
    if not _git(top, "status", "--porcelain", "--", rel).strip():
        return None
    # only this folder is committed (pathspec); a project repository's hooks run as usual
    _git(top, *_identity_args(top), "commit", "-q", "-m", title, "-m", body, "--", rel)
    return _git(top, "rev-parse", "--short", "HEAD").strip()


def _work():
    while True:
        job = _jobs.get()
        try:
            sha = _commit(*job)
            if sha:
                STATE["commits"] += 1
                STATE["last_commit"] = sha
            STATE["last_error"] = None
        except Exception as e:  # report, never crash FreeCAD
            STATE["last_error"] = str(e)
            FreeCAD.Console.PrintWarning(f"CadAI geçmiş: {e}\n")
        finally:
            STATE["done"] += 1
            _jobs.task_done()


def _enqueue(path, title, body, files, fcstd=None):
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_work, name="cadai-history", daemon=True)
        _worker.start()
    _jobs.put((path, title, body, files, fcstd))


def wait(timeout=60):
    """Block until queued commits are written (tests, shutdown)."""
    end = datetime.datetime.now() + datetime.timedelta(seconds=timeout)
    while _jobs.unfinished_tasks and datetime.datetime.now() < end:
        threading.Event().wait(0.05)
    return not _jobs.unfinished_tasks


# ---------------- snapshot & diff ----------------

def _num(x, nd=4):
    return round(float(x), nd)


def _n(x):
    """120.0 -> "120", 26400.0 -> "26400", 2.5 -> "2.5"."""
    return f"{float(x):.4f}".rstrip("0").rstrip(".")


def _pretty(q):
    """FreeCAD quantities as people write them: "100.0 mm" -> "100 mm", "2.50 mm" -> "2.5 mm"."""
    return re.sub(r"(\d+\.\d*?)0+(?=\D|$)", r"\1", str(q)).replace(". ", " ").rstrip(".")


def snapshot(doc):
    """Deterministic, diff-friendly state of the document."""
    from .tools.geometry import dimension_properties

    objects = {}
    for obj in doc.Objects:
        if obj.TypeId.startswith(("App::Origin", "App::Line", "App::Plane")) or obj.isDerivedFrom("Fem::FemResultObject"):
            continue  # derived data (FEM results) is not part of the design
        item = {"label": obj.Label, "type": obj.TypeId}
        dims = dimension_properties(obj)
        if dims:
            item["dimensions"] = {k: _pretty(v) for k, v in sorted(dims.items())}
        exprs = getattr(obj, "ExpressionEngine", None)
        if exprs:
            item["expressions"] = {k: v for k, v in sorted(exprs)}
        pl = getattr(obj, "Placement", None)
        if pl is not None and not pl.isIdentity():
            axis = pl.Rotation.Axis
            item["placement"] = {"base": [_num(c, 4) for c in pl.Base],
                                 "axis": [_num(c, 4) for c in axis], "angle_deg": _num(pl.Rotation.Angle * 57.29577951308232, 4)}
        shape = getattr(obj, "Shape", None)
        try:
            if shape is not None and not shape.isNull() and shape.BoundBox.isValid():
                b = shape.BoundBox
                item["bbox_mm"] = [_num(b.XLength, 3), _num(b.YLength, 3), _num(b.ZLength, 3)]
                if shape.Solids:
                    item["volume_mm3"] = _num(shape.Volume, 2)
        except Exception:
            pass
        if "Invalid" in obj.State or "Error" in obj.State:
            item["state"] = sorted(obj.State)
        objects[obj.Name] = item
    return {"document": doc.Label, "objects": objects}


def diff(old, new):
    """Human-readable changes between two snapshots: [(object, text)]."""
    changes = []
    a, b = (old or {}).get("objects", {}), new.get("objects", {})
    for name in sorted(set(b) - set(a)):
        changes.append((name, f"eklendi ({b[name]['type'].split('::')[-1]})"))
    for name in sorted(set(a) - set(b)):
        changes.append((name, "silindi"))
    for name in sorted(set(a) & set(b)):
        o, n = a[name], b[name]
        if o.get("label") != n.get("label"):
            changes.append((name, f"adı {o.get('label')} → {n.get('label')}"))
        for prop in sorted(set(o.get("dimensions", {})) | set(n.get("dimensions", {}))):
            ov, nv = o.get("dimensions", {}).get(prop), n.get("dimensions", {}).get(prop)
            if ov != nv:
                changes.append((name, f"{prop} {ov} → {nv}"))
        for prop in sorted(set(o.get("expressions", {})) | set(n.get("expressions", {}))):
            ov, nv = o.get("expressions", {}).get(prop), n.get("expressions", {}).get(prop)
            if ov != nv:
                changes.append((name, f"{prop} ifadesi {ov or '—'} → {nv or '—'}"))
        if o.get("placement") != n.get("placement"):
            changes.append((name, f"konum {(o.get('placement') or {}).get('base', [0, 0, 0])} → "
                                  f"{(n.get('placement') or {}).get('base', [0, 0, 0])}"))
        ov, nv = o.get("volume_mm3"), n.get("volume_mm3")
        if ov and nv and abs(nv - ov) > 1e-3 * max(abs(ov), 1):
            changes.append((name, f"hacim {_n(ov)} → {_n(nv)} mm³"))
        elif o.get("bbox_mm") != n.get("bbox_mm") and not any(c[0] == name for c in changes):
            size = lambda b: " × ".join(_n(x) for x in b or [])  # noqa: E731
            changes.append((name, f"boyut {size(o.get('bbox_mm'))} → {size(n.get('bbox_mm'))} mm"))
    return changes


DERIVED = ("hacim", "boyut")  # consequences of other changes: shown in the note, not counted in the title


def _title(changes, reasons):
    primary = [(o, t) for o, t in changes if not t.startswith(DERIVED)] or changes
    dims = [f"{obj}.{text.split(' ', 1)[0]} {text.split(' ', 1)[1]}" for obj, text in primary
            if "→" in text and not text.startswith("konum")]
    if dims:
        head = dims[0]
    elif primary:
        head = f"{primary[0][0]} {primary[0][1]}"
    else:
        head = reasons[0] if reasons else "Değişiklik"
    more = len(primary) - 1
    title = head + (f" (+{more} değişiklik)" if more > 0 else "")
    if any(r in ("Geri al", "Yinele") for r in reasons):
        title += " · geri alındı" if "Geri al" in reasons else " · yinelendi"
    return title if len(title) <= 72 else title[:69] + "…"


# ---------------- Obsidian notes ----------------

def _load_index(path):
    try:
        with open(os.path.join(path, ".cadai", "index.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"entries": []}


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _notes(doc, snap, index, entry):
    """Journal note for this change + regenerated part notes and vault home (plain Markdown with [[links]]),
    as {relative path: text}."""
    files = {}
    note = entry["note"]
    objs = sorted({o for o, _ in entry["changes"]})
    fm = ["---", f"date: {entry['time']}", f"document: {json.dumps(doc.Label, ensure_ascii=False)}",
          f"source: {json.dumps(entry['source'], ensure_ascii=False)}",
          "objects: [" + ", ".join(json.dumps(o, ensure_ascii=False) for o in objs) + "]",
          "tags: [cadai, tasarim-degisikligi]", "---", ""]
    body = [f"# {entry['title']}", ""]
    for obj, text in entry["changes"]:
        label = snap["objects"].get(obj, {}).get("label", obj)
        body.append(f"- [[parts/{_safe(obj)}|{label}]]: {text}")
    if not entry["changes"]:
        body.append("- (geometri değişmedi)")
    body += ["", f"**Kaynak:** {entry['source']}", f"**İşlemler:** {', '.join(entry['reasons']) or '—'}", "",
             "Model durumu: [[model/model.json]] · önceki/sonraki değişiklikler: [[README]]", ""]
    files[note + ".md"] = "\n".join(fm + body)

    by_obj = {}
    for e in index["entries"]:
        for o in {o for o, _ in e["changes"]}:
            by_obj.setdefault(o, []).append(e)
    for name in set(by_obj) - set(snap["objects"]):  # deleted objects keep their note and history
        lines = ["---", "tags: [cadai, parca, silindi]", "---", "", f"# {name} (silindi)", "",
                 "Bu nesne artık modelde yok.", "", "## Değişiklikler"]
        lines += [f"- [[{e['note']}|{e['time'][:16].replace('T', ' ')}]] {e['title']}" for e in reversed(by_obj[name])]
        files[f"parts/{_safe(name)}.md"] = "\n".join(lines) + "\n"
    for name, item in snap["objects"].items():
        lines = ["---", f"type: {item['type']}", "tags: [cadai, parca]", "---", "", f"# {item['label']}", "",
                 f"`{name}` · {item['type']}", ""]
        for key, title in (("dimensions", "Ölçüler"), ("expressions", "İfadeler")):
            if item.get(key):
                lines += [f"## {title}"] + [f"- {k}: {v}" for k, v in item[key].items()] + [""]
        if item.get("bbox_mm"):
            lines.append(f"Boyut: {' × '.join(_n(x) for x in item['bbox_mm'])} mm"
                         + (f" · hacim {_n(item['volume_mm3'])} mm³" if item.get("volume_mm3") else ""))
            lines.append("")
        lines.append("## Değişiklikler")
        for e in reversed(by_obj.get(name, [])):
            lines.append(f"- [[{e['note']}|{e['time'][:16].replace('T', ' ')}]] {e['title']}")
        files[f"parts/{_safe(name)}.md"] = "\n".join(lines) + "\n"
    home = [f"# {doc.Label} — tasarım geçmişi", "",
            "CadAI her değişikliği burada bir git commit'i ve bir not olarak saklar. Bu klasör Obsidian'da kasa (vault) "
            "olarak açılabilir; parçalar [[parts/...]] notlarında, değişiklikler günlükte.", "",
            "## Parçalar"]
    home += [f"- [[parts/{_safe(n)}|{o['label']}]]" for n, o in snap["objects"].items()] + ["", "## Son değişiklikler"]
    home += [f"- [[{e['note']}|{e['time'][:16].replace('T', ' ')}]] {e['title']} — {e['source']}"
             for e in reversed(index["entries"][-200:])]
    files["README.md"] = "\n".join(home) + "\n"
    return files


# ---------------- recording ----------------

def _repo_memory(path):
    m = _memory.get(path)
    if m is None:
        try:
            with open(os.path.join(path, "model", "model.json"), encoding="utf-8") as f:
                snap = json.load(f)
        except (OSError, ValueError):
            snap = None
        m = _memory[path] = {"snap": snap, "index": _load_index(path)}
    return m


def record(doc, reasons=None, source=None):
    """Snapshot the document now and queue a commit when something changed. Returns the entry or None."""
    s = settings()
    if not s["enabled"] or doc is None:
        return None
    path = repo_dir(doc)
    mem = _repo_memory(path)
    snap = snapshot(doc)
    old = mem["snap"]
    if old == snap:
        return None  # nothing that the history tracks has changed
    changes = diff(old, snap) if old is not None else [(n, "ilk kayıt") for n in sorted(snap["objects"])]
    reasons = [r for r in (reasons or []) if r]
    now = datetime.datetime.now().replace(microsecond=0)
    title = _title(changes, reasons) if old is not None else f"{doc.Label}: geçmiş başladı"
    note = f"journal/{now:%Y-%m-%d}/{now:%H%M%S} {_safe(title)[:60]}"
    taken = {e["note"] for e in mem["index"]["entries"]}
    k = 2
    while note in taken or (note + f" {k - 1}") in taken and k > 2:
        note, k = f"{note.rsplit(' ', 1)[0] if k > 2 else note} {k}", k + 1
    entry = {"time": now.isoformat(), "title": title, "source": source or "FreeCAD", "reasons": reasons,
             "changes": changes, "note": note}
    files = {"model/model.json": json.dumps(snap, indent=1, ensure_ascii=False, sort_keys=True) + "\n"}
    fcstd = None
    if s["fcstd"]:
        tmp = os.path.join(tempfile.mkdtemp(prefix="cadai_hist_"), "copy.FCStd")
        STATE["saving_copy"] = True
        try:
            doc.saveCopy(tmp)
            fcstd = (tmp, f"model/{_safe(_stem(doc))}.FCStd")
        except Exception as e:
            STATE["last_error"] = f"FCStd kopyası yazılamadı: {e}"
        finally:
            STATE["saving_copy"] = False
    if s["notes"]:
        mem["index"]["entries"].append(entry)
        files[".cadai/index.json"] = json.dumps(mem["index"], indent=1, ensure_ascii=False) + "\n"
        files.update(_notes(doc, snap, mem["index"], entry))
    mem["snap"] = snap
    body = "\n".join([f"- {o}: {t}" for o, t in changes[:40]] + ["", f"Kaynak: {entry['source']}",
                      f"İşlemler: {', '.join(reasons) or '—'}", "CadAI-History: 1"]
                     + ([f"CadAI-Note: {note}"] if s["notes"] else []))
    _enqueue(path, title, body, files, fcstd)
    return entry


def flush():
    """Record everything that happened since the last flush (called by the debounce timer)."""
    pending, STATE["pending"] = STATE["pending"], []
    by_doc = {}
    for item in pending:
        by_doc.setdefault(item["doc"], []).append(item)
    for name, items in by_doc.items():
        doc = FreeCAD.getDocument(name) if name in FreeCAD.listDocuments() else None
        sources = []
        for it in items:
            if it["source"] and it["source"] not in sources:
                sources.append(it["source"])
        record(doc, [it["reason"] for it in items], " · ".join(sources) or "FreeCAD")


def _schedule():
    try:
        from PySide import QtCore
    except ImportError:
        return  # headless: callers flush() themselves
    if QtCore.QCoreApplication.instance() is None:
        return
    if STATE["timer"] is None:
        t = QtCore.QTimer()
        t.setSingleShot(True)
        t.timeout.connect(flush)
        STATE["timer"] = t
    STATE["timer"].start(DEBOUNCE_MS)


def _queue(doc, reason):
    if not settings()["enabled"]:
        return
    STATE["pending"].append({"doc": doc.Name, "reason": reason, "source": STATE["source"]})
    _schedule()


class _Observer:
    def slotOpenTransaction(self, doc, name):
        STATE["open"][doc.Name] = name

    def slotCommitTransaction(self, doc):
        _queue(doc, STATE["open"].pop(doc.Name, "") or "Değişiklik")

    def slotAbortTransaction(self, doc):
        STATE["open"].pop(doc.Name, None)

    def slotUndoDocument(self, doc):
        _queue(doc, "Geri al")

    def slotRedoDocument(self, doc):
        _queue(doc, "Yinele")

    def slotFinishSaveDocument(self, doc, filename):
        if STATE.get("saving_copy") or os.path.normcase(os.path.abspath(filename)) != os.path.normcase(doc.FileName or ""):
            return  # our own snapshot copy, not a save by the user
        # a document saved for the first time: move its history from the user folder next to the file
        if not settings()["enabled"] or settings()["folder"]:
            return
        old = os.path.join(os.path.dirname(config.config_path()), "history", _safe(doc.Name))
        new = repo_dir(doc)
        if os.path.isdir(old) and not os.path.exists(new):
            try:
                wait(30)
                shutil.move(old, new)
                if old in _memory:
                    _memory[new] = _memory.pop(old)
            except OSError as e:
                STATE["last_error"] = f"geçmiş taşınamadı: {e}"
        _queue(doc, "Kaydet")


class source:
    """Context manager: attribute changes made inside to e.g. "AI · set_property" in the history."""

    def __init__(self, label):
        self.label, self.prev = label, None

    def __enter__(self):
        self.prev, STATE["source"] = STATE["source"], self.label

    def __exit__(self, *exc):
        STATE["source"] = self.prev


def install():
    global _observer
    if _observer is None:
        _observer = _Observer()
        FreeCAD.addDocumentObserver(_observer)


def uninstall():
    global _observer
    if _observer is not None:
        try:
            FreeCAD.removeDocumentObserver(_observer)
        except Exception:
            pass
        _observer = None


# ---------------- reading the history ----------------

def log(doc=None, limit=30):
    doc = doc or FreeCAD.ActiveDocument
    if doc is None:
        return {"enabled": settings()["enabled"], "entries": []}
    path = repo_dir(doc)
    out = {"enabled": settings()["enabled"], "document": doc.Label, "repo": path, "entries": []}
    top = _repo_top(path)
    if top is None:
        return out
    rel = os.path.relpath(path, top)
    raw = _git(top, "log", f"-{int(limit)}", "--date=iso-strict", "--pretty=format:%h%x1f%ad%x1f%s%x1f%b%x1e", "--", rel,
               check=False)
    for record in raw.split("\x1e"):
        parts = record.strip("\n").split("\x1f")
        if len(parts) != 4:
            continue
        entry = {"commit": parts[0], "date": parts[1], "title": parts[2], "changes": []}
        for line in parts[3].splitlines():  # the body record() wrote: "- Object: change", "Kaynak: …", trailers
            if line.startswith("- ") and ": " in line:
                obj, text = line[2:].split(": ", 1)
                entry["changes"].append({"object": obj, "text": text})
            elif line.startswith("Kaynak: "):
                entry["source"] = line[8:]
            elif line.startswith("CadAI-Note: "):
                entry["note"] = line[12:]
        out["entries"].append(entry)
    return out


def open_version(commit, doc=None):
    """Open the document as it was at `commit` as a NEW document (the current one is left alone)."""
    doc = doc or FreeCAD.ActiveDocument
    if doc is None:
        raise RuntimeError("Açık belge yok.")
    if not re.fullmatch(r"[0-9a-fA-F]{4,40}", str(commit)):
        raise RuntimeError(f"Geçersiz commit: {commit!r}")
    path = repo_dir(doc)
    top = _repo_top(path)
    if top is None:
        raise RuntimeError("Bu belgenin geçmişi yok.")
    rel = os.path.relpath(os.path.join(path, "model", _safe(_stem(doc)) + ".FCStd"), top).replace(os.sep, "/")
    p = subprocess.run(["git", "show", f"{commit}:{rel}"], cwd=top, capture_output=True, creationflags=_FLAGS, timeout=120)
    if p.returncode:
        raise RuntimeError(f"Bu commit'te FCStd yok: {p.stderr.decode('utf-8', 'replace').strip()[:300]}")
    import tempfile

    out = os.path.join(tempfile.mkdtemp(prefix="cadai_version_"), f"{_safe(_stem(doc))}@{commit}.FCStd")
    with open(out, "wb") as f:
        f.write(p.stdout)
    opened = FreeCAD.openDocument(out)
    return {"document": opened.Name, "file": out, "commit": commit}


def status(doc=None):
    doc = doc or FreeCAD.ActiveDocument
    s = settings()
    out = dict(s, git=git_available(), commits=STATE["commits"], last_error=STATE["last_error"],
               last_commit=STATE.get("last_commit"))
    if doc is not None:
        out["repo"] = repo_dir(doc)
        top = _repo_top(out["repo"])
        out["inside_project_repo"] = bool(top and os.path.normpath(top) != os.path.normpath(out["repo"]))
    return out
