"""Forgiving tool arguments: small local models (7-14B) get names, types and shapes slightly wrong all the time.

Instead of failing with a Python TypeError, the arguments are mapped onto the tool's JSON schema: misspelled or
aliased keys ("obj" -> "object"), numbers as text ("20 mm" -> 20), vectors as text ("0,0,1" -> [0, 0, 1]), single
values where a list is expected, enum values in the wrong case, "face1" -> "Face1", null for optional arguments.
What cannot be mapped becomes an error that shows a correct call, so the model can fix it on the next step.
Standard library only (the MCP server and plain-Python tests import it too).
"""

import difflib
import json
import re

from . import ToolError

WRAPPER_KEYS = ("arguments", "args", "parameters", "params", "input", "kwargs")

# Keys models use instead of the schema's. Only applied when the key itself is not a property of the tool.
ALIASES = {
    "object": ("obj", "object_name", "objectname", "object_id", "name", "target", "part", "body", "shape", "label",
               "base_object", "solid"),
    "objects": ("object_names", "names", "targets", "parts", "items"),
    "property": ("prop", "property_name", "attribute", "attr", "field", "parameter"),
    "value": ("new_value", "val", "to", "set_to"),
    "code": ("python", "python_code", "script", "source", "src", "command"),
    "query": ("q", "search", "text", "keyword", "keywords", "term"),
    "topic": ("recipe", "name", "pattern"),
    "position": ("pos", "location", "point", "center", "centre", "at", "origin", "xyz", "placement"),
    "diameter": ("dia", "d", "diam", "hole_diameter", "size_mm"),
    "radius": ("r", "radius_mm", "fillet_radius"),
    "size": ("chamfer", "chamfer_size", "distance", "length_mm"),
    "operation": ("op", "type", "mode", "kind"),
    "base": ("first", "a", "object", "from"),
    "tool": ("second", "b", "with", "cutter", "subtract"),
    "path": ("file", "filename", "file_path", "output", "out"),
    "analysis": ("analysis_name", "fem", "study"),
    "fixed_faces": ("fixed", "supports", "support_faces", "fix", "constraints"),
    "process": ("method", "manufacturing", "manufacturing_process"),
    "part_id": ("id", "part", "partid"),
    "job": ("job_id", "id"),
    "direction": ("dir", "vector", "axis", "drill_direction"),
    "force_n": ("force", "load", "magnitude", "newton", "newtons", "force_newton"),
}

ELEMENT_RE = re.compile(r"^(?:.*[:.])?\s*(face|edge|vertex)\s*_?(\d+)\s*$", re.IGNORECASE)
NUMBER_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?")
# inside a list a comma separates values ("1,2,3"); a lone number may use the Turkish decimal comma ("2,5")
LIST_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
TRUE_WORDS = {"true", "yes", "y", "1", "on", "evet", "doğru", "dogru", "açık", "acik"}
FALSE_WORDS = {"false", "no", "n", "0", "off", "hayır", "hayir", "yanlış", "yanlis", "kapalı", "kapali", "none"}


def _types(schema):
    t = schema.get("type")
    if isinstance(t, list):
        return t
    if t:
        return [t]
    if "enum" in schema:
        return ["string"]
    return []


def _norm_key(key):
    return re.sub(r"[\s\-]+", "_", str(key).strip()).lower()


def placeholder(name, schema):
    """A plausible value for an example call."""
    if "enum" in schema:
        return schema["enum"][0]
    if "default" in schema:
        return schema["default"]
    t = (_types(schema) or ["string"])[0]
    if t == "array":
        item = schema.get("items") or {}
        if _types(item)[:1] == ["number"]:
            return [0, 0, 0] if schema.get("minItems") == 3 or name in ("position", "direction", "normal") else [1]
        return ["Face1"] if "face" in name else ["Edge1"] if "edge" in name else ["..."]
    if t in ("number", "integer"):
        return 10
    if t == "boolean":
        return True
    if t == "object":
        return {}
    return "Box" if name in ("object", "base") else "..."


def example_call(tool):
    """A correct call: the tool's own example, else one built from the required arguments."""
    args = tool.example
    if args is None:
        props = tool.schema.get("properties", {})
        args = {k: placeholder(k, props.get(k, {})) for k in tool.schema.get("required", [])}
    return f"{tool.name} {json.dumps(args, ensure_ascii=False)}"


def usage(tool):
    props = tool.schema.get("properties", {})
    req = set(tool.schema.get("required", []))
    parts = []
    for k, s in props.items():
        t = "|".join(_types(s)) or "any"
        if "enum" in s:
            t = "|".join(str(e) for e in s["enum"])
        parts.append(f"{k}{'' if k in req else '?'}: {t}")
    return f"Argümanlar: {', '.join(parts) or '(yok)'}. Örnek: {example_call(tool)}"


def _element(value, prefix):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and float(value).is_integer():
        return f"{prefix}{int(value)}"
    if isinstance(value, str):
        m = ELEMENT_RE.match(value)
        if m:
            return m.group(1).capitalize() + m.group(2)
        if value.strip().isdigit():
            return f"{prefix}{int(value)}"
    return value


def _element_prefix(name):
    n = (name or "").lower()
    if "face" in n:
        return "Face"
    if "edge" in n:
        return "Edge"
    return None


def _number(value, integer):
    if isinstance(value, bool):
        raise ValueError
    if isinstance(value, (int, float)):
        num = value
    elif isinstance(value, str):
        m = NUMBER_RE.search(value)
        if not m:
            raise ValueError
        num = float(m.group(0).replace(",", "."))
    elif isinstance(value, list) and len(value) == 1:
        return _number(value[0], integer)
    else:
        raise ValueError
    if integer:
        return int(round(num))
    return int(num) if isinstance(num, int) else float(num)


def _boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        w = value.strip().lower()
        if w in TRUE_WORDS:
            return True
        if w in FALSE_WORDS:
            return False
    raise ValueError


def _array(value, schema, name):
    if isinstance(value, str):
        s = value.strip()
        if s.startswith(("[", "(")):
            try:
                value = json.loads(s.replace("(", "[").replace(")", "]"))
            except ValueError:
                value = [p for p in re.split(r"[\s,;]+", s.strip("[]()")) if p]
        elif "number" in _types(schema.get("items") or {}) or "integer" in _types(schema.get("items") or {}):
            value = LIST_NUMBER_RE.findall(s) or [s]
        else:
            value = [p.strip() for p in re.split(r"[,;]", s) if p.strip()] if ("," in s or ";" in s) else [s]
    elif isinstance(value, dict):
        keys = {k.lower(): v for k, v in value.items()}
        if {"x", "y", "z"} <= set(keys):
            value = [keys["x"], keys["y"], keys["z"]]
        else:
            value = [value]
    elif isinstance(value, tuple):
        value = list(value)
    elif not isinstance(value, list):
        value = [value]
    items = schema.get("items")
    if isinstance(items, dict):
        value = [coerce(v, items, name) for v in value]
    return value


def _enum(value, options):
    if value in options:
        return value
    text = str(value).strip()
    for o in options:
        if str(o).lower() == text.lower():
            return o
    simple = {re.sub(r"[\s\-]+", "_", str(o).lower()): o for o in options}
    key = re.sub(r"[\s\-]+", "_", text.lower())
    if key in simple:
        return simple[key]
    close = difflib.get_close_matches(key, list(simple), n=1, cutoff=0.75)
    if close:
        return simple[close[0]]
    raise ValueError


def coerce(value, schema, name=""):
    """Convert value towards schema. Raises ValueError when it cannot."""
    if not isinstance(schema, dict) or value is None:
        return value
    types = _types(schema)
    prefix = _element_prefix(name)
    if prefix and "string" in types and not isinstance(value, (list, dict)):
        value = _element(value, prefix)
    if "enum" in schema:
        return _enum(value, schema["enum"])
    if not types or len(types) > 1 and not isinstance(value, (list, dict)) and any(
            _matches(value, t) for t in types):
        return value
    t = types[0] if len(types) == 1 else next((t for t in types if t != "string"), types[0])
    if _matches(value, t):
        if t == "array":
            return _array(value, schema, name)
        if t == "object" and isinstance(schema.get("properties"), dict):
            return normalize_object(value, schema)
        return value
    if t in ("number", "integer"):
        return _number(value, t == "integer")
    if t == "boolean":
        return _boolean(value)
    if t == "array":
        return _array(value, schema, name)
    if t == "string":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(int(value)) if float(value).is_integer() else str(value)
        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
            return value[0]
        raise ValueError
    if t == "object":
        if isinstance(value, str):
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return normalize_object(parsed, schema) if isinstance(schema.get("properties"), dict) else parsed
        raise ValueError
    return value


def _matches(value, t):
    return {"string": isinstance(value, str),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "array": isinstance(value, list),
            "object": isinstance(value, dict),
            "null": value is None}.get(t, True)


def _map_key(key, props, taken):
    """Schema property for a key the model used, or None."""
    if key in props:
        return key
    nk = _norm_key(key)
    by_norm = {_norm_key(p): p for p in props}
    if nk in by_norm:
        return by_norm[nk]
    for cand in (nk + "s", nk[:-1] if nk.endswith("s") else None, nk + "_mm", nk[:-3] if nk.endswith("_mm") else None,
                 nk + "_deg", nk[:-4] if nk.endswith("_deg") else None):
        if cand and cand in by_norm and by_norm[cand] not in taken:
            return by_norm[cand]
    for prop, aliases in ALIASES.items():
        if prop in props and prop not in taken and nk in aliases:
            return prop
    close = difflib.get_close_matches(nk, list(by_norm), n=1, cutoff=0.8)
    if close and by_norm[close[0]] not in taken:
        return by_norm[close[0]]
    return None


def normalize_object(args, schema, notes=None, path=""):
    props = schema.get("properties") or {}
    if not props:
        return args
    out = {}
    for key, value in args.items():
        if value is None:
            continue
        prop = _map_key(key, props, out)
        if prop is None:
            if notes is not None:
                notes.append(path + str(key))
            continue
        try:
            out[prop] = coerce(value, props[prop], prop)
        except (ValueError, TypeError):
            raise ToolError(_bad_value(path + prop, value, props[prop]))
    return out


def _bad_value(name, value, schema):
    if "enum" in schema:
        want = "şunlardan biri: " + ", ".join(str(e) for e in schema["enum"])
    else:
        t = "|".join(_types(schema)) or "değer"
        want = {"number": "sayı (ör. 10)", "integer": "tam sayı (ör. 3)", "boolean": "true ya da false",
                "array": "liste (ör. [0, 0, 1])", "object": "nesne ({...})", "string": "metin"}.get(t, t)
        if schema.get("description"):
            want += f" — {schema['description']}"
    return f"'{name}' için geçersiz değer {json.dumps(value, ensure_ascii=False)[:80]}: {want} olmalı."


def unwrap(args, tool):
    """{"arguments": {...}} / {"name": tool, "arguments": {...}} / a JSON string -> the plain argument object."""
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except ValueError:
            raise ToolError(f"Argümanlar geçerli JSON değil. {usage(tool)}")
    if args is None:
        return {}
    if not isinstance(args, dict):
        raise ToolError(f"Argümanlar bir JSON nesnesi olmalı. {usage(tool)}")
    props = tool.schema.get("properties") or {}
    for _ in range(2):
        inner = [k for k in args if k in WRAPPER_KEYS and k not in props]
        rest = [k for k in args if k not in inner and k not in ("name", "tool", "function", "type")]
        if len(inner) == 1 and not rest:
            value = args[inner[0]]
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    break
            if isinstance(value, dict):
                args = value
                continue
        break
    return args


def normalize(tool, args):
    """Returns (arguments, ignored keys). Raises ToolError with a usage example when the call cannot work."""
    args = unwrap(args, tool)
    schema = tool.schema
    props = schema.get("properties") or {}
    ignored = []
    out = normalize_object(args, schema, ignored)
    # a lone value given for a tool with exactly one required argument under some other name
    required = schema.get("required", [])
    missing = [k for k in required if k not in out]
    if missing and len(missing) == 1 and len(ignored) == 1 and ignored[0] in args:
        try:
            out[missing[0]] = coerce(args[ignored[0]], props[missing[0]], missing[0])
            ignored, missing = [], []
        except (ValueError, TypeError):
            pass
    if missing:
        raise ToolError(f"{tool.name}: eksik zorunlu argüman: {', '.join(missing)}. {usage(tool)}")
    if ignored and not props:
        ignored = []  # tools without arguments: silently ignore whatever was sent
    return out, ignored


def suggest(name, names, limit=3):
    return difflib.get_close_matches(name, list(names), n=limit, cutoff=0.5)
