"""AI access to the optional design history (cadai.history): list past versions, open one as a new document."""

from . import Tool, ToolError


def design_history(limit=20):
    from .. import history

    out = history.log(limit=max(1, min(int(limit), 200)))
    if not out["enabled"]:
        out["note"] = ("Otomatik tasarım geçmişi kapalı. Kullanıcı VS Code'da CadAI → Tasarım geçmişi'nden açabilir; "
                       "açıkken her değişiklik bir git commit'i ve bir Obsidian notu olur.")
    return out


def open_design_version(commit):
    from .. import history

    try:
        return history.open_version(commit)
    except RuntimeError as e:
        raise ToolError(str(e))


TOOLS = [
    Tool("design_history",
         "List recent versions of the open document from the optional automatic design history (one git commit per "
         "change, newest first, with a summary like 'Beam.Length 100 mm → 120 mm'). Use it to answer 'what changed', "
         "to find a version to go back to, or before undoing a series of changes.",
         {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}}},
         design_history, title="Design history"),
    Tool("open_design_version",
         "Open the document as it was at a commit from design_history, as a NEW document next to the current one "
         "(nothing is overwritten). Compare with measure, or copy values back with set_property.",
         {"type": "object", "properties": {"commit": {"type": "string"}}, "required": ["commit"]},
         open_design_version, mutates=True, destructive=False, title="Open a past version"),
]
