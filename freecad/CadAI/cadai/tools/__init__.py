"""Tool registry. Every tool is a plain function plus a JSON schema the LLM sees."""

import json
import traceback
from dataclasses import dataclass, field

MAX_RESULT_CHARS = 12000


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    image_png_b64: str = None


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    func: object
    mutates: bool = False  # changes the document or files -> needs approval, hidden in plan mode
    title: str = ""  # short human-readable name shown by agent UIs (MCP annotations.title)
    destructive: bool = True  # only meaningful when mutates: may overwrite/delete existing things (MCP destructiveHint)
    idempotent: bool = False  # repeating the same call has no further effect (MCP idempotentHint)
    open_world: bool = False  # talks to services outside this machine (MCP openWorldHint)
    example: dict = None  # a correct call's arguments, shown when the model gets the call wrong

    def manifest(self):
        from . import small

        out = {"name": self.name, "title": self.title, "description": self.description, "input_schema": self.schema,
               "mutates": self.mutates, "destructive": self.destructive, "idempotent": self.idempotent,
               "open_world": self.open_world}
        view = small.view(self)
        if view is not None:  # what small local models see (MCP server with CADAI_TOOLSET=small)
            out["small"] = {"description": view.description, "input_schema": view.schema,
                            "group": small.group_of(self.name)}
        return out


@dataclass
class Registry:
    tools: dict = field(default_factory=dict)

    def register(self, tool):
        self.tools[tool.name] = tool

    def get(self, name):
        return self.tools.get(name)

    def specs(self, include_mutating=True):
        return [t for t in self.tools.values() if include_mutating or not t.mutates]

    def resolve_name(self, name):
        """The registered tool a model meant: 'functions.measure', 'cadai-freecad__measure', 'Measure', 'add-box' and
        common invented names ('create_box', 'drill_hole') all resolve. None when nothing fits well enough."""
        from . import small

        if not isinstance(name, str):
            return None
        if name in self.tools:
            return name
        key = name.strip().strip("`'\" ").split("__")[-1].split(".")[-1].split("/")[-1]
        key = key.replace("-", "_").replace(" ", "_").lower()
        if key in self.tools:
            return key
        alias = small.TOOL_ALIASES.get(key)
        return alias if alias in self.tools else None

    def unknown_tool_message(self, name, allowed=None):
        from .args import suggest

        names = sorted(allowed if allowed is not None else self.tools)
        close = suggest(str(name).lower(), names)
        hint = f" Bunu mu demek istedin: {', '.join(close)}?" if close else ""
        return f"Bilinmeyen araç: {name!r}.{hint} Kullanılabilir araçlar: {', '.join(names)}"

    def normalized_args(self, name, args):
        """The arguments as the tool will receive them (raises ToolError). Approval dialogs must show these: a
        run_python call may arrive with its code under 'script' or 'python'."""
        from . import args as argtools

        tool = self.get(self.resolve_name(name) or "")
        if tool is None:
            raise ToolError(self.unknown_tool_message(name))
        if isinstance(args, dict) and "__invalid_json__" in args:
            raise ToolError(f"Araç argümanları geçerli JSON değil: {str(args['__invalid_json__'])[:300]!r}. "
                            f"{argtools.usage(tool)}")
        return argtools.normalize(tool, args)[0]

    def run(self, name, args):
        from . import args as argtools

        tool = self.get(self.resolve_name(name) or "")
        if tool is None:
            return ToolResult(self.unknown_tool_message(name), is_error=True)
        if isinstance(args, dict) and "__invalid_json__" in args:
            raw = args["__invalid_json__"]
            return ToolResult(f"Araç argümanları geçerli JSON değil: {str(raw)[:300]!r}. {argtools.usage(tool)}",
                              is_error=True)
        try:
            args, ignored = argtools.normalize(tool, args)
            out = tool.func(**args)
        except ToolError as e:
            msg = str(e)
            if "Örnek:" not in msg and ("argüman" in msg or "geçersiz değer" in msg):
                msg += " " + argtools.usage(tool)
            return ToolResult(msg, is_error=True)
        except TypeError as e:
            return ToolResult(f"Hatalı argüman: {e}. {argtools.usage(tool)}", is_error=True)
        except Exception as e:
            tb = traceback.format_exc(limit=4)
            return ToolResult(f"{type(e).__name__}: {e}\n{tb}", is_error=True)
        checker = self.get("check_design_requirements")
        if tool.mutates and tool.name != "set_design_requirements" and checker is not None:
            if not isinstance(out, ToolResult) or not out.is_error:
                # A mutation already happened: a failed design check must not claim it was rolled back,
                # nor trigger a blind retry of the mutation. Preserve its result and attach separate evidence.
                try:
                    validation = checker.func()
                except Exception as e:
                    validation = {"status": "error", "error": str(e)[:240]}
                if validation["status"] != "not_configured":
                    if isinstance(out, dict):
                        out = {"design_validation": validation, **out}
                    elif isinstance(out, ToolResult):
                        try:
                            payload = json.loads(out.content)
                        except (TypeError, ValueError):
                            payload = out.content
                        payload = payload if isinstance(payload, dict) else {"result": payload}
                        out = ToolResult(json.dumps({"design_validation": validation, **payload}, ensure_ascii=False),
                                         image_png_b64=out.image_png_b64)
                    else:
                        out = {"design_validation": validation, "result": out}
        if isinstance(out, ToolResult):
            result = out
        elif isinstance(out, str):
            result = ToolResult(out)
        else:
            if ignored and isinstance(out, dict):
                out = dict(out, ignored_arguments=ignored)
            result = ToolResult(json.dumps(out, ensure_ascii=False, indent=1, default=str))
            ignored = []
        if ignored:
            result.content += f"\n(yok sayılan argümanlar: {', '.join(ignored)})"
        if len(result.content) > MAX_RESULT_CHARS:
            result.content = result.content[:MAX_RESULT_CHARS] + "\n... (kısaltıldı)"
        return result


class ToolError(Exception):
    """Expected failure with a message meant for the model."""


def build_registry():
    from . import (
        dfm_tools,
        drawing_tools,
        fem_tools,
        history_tools,
        inspect_tools,
        integration_tools,
        marker_tools,
        model_tools,
        parametric_tools,
        parts_tools,
        recipes,
        render_tools,
        requirement_tools,
        shape_tools,
    )

    reg = Registry()
    for module in (inspect_tools, marker_tools, model_tools, shape_tools, recipes, fem_tools, dfm_tools, parts_tools,
                   history_tools, drawing_tools, render_tools, integration_tools, requirement_tools, parametric_tools):
        for tool in module.TOOLS:
            reg.register(tool)
    return reg
