"""Agent loop: send history to the model, run the tools it asks for, repeat until it answers."""

import json

from . import prompts
from .providers import ToolCall
from .tools import ToolError, ToolResult, small

SMALL_RESULT_CHARS = 5000  # one tool result; small models have 8-16K tokens for everything
SMALL_OLD_RESULT_CHARS = 600  # results older than the last two steps are shortened to this
MAX_SAME_FAILURE = 3  # the same failing call this many times ends the turn instead of burning steps
MAX_FAILED_STEPS = 6  # consecutive steps where every call failed


def _compact(content, limit):
    try:
        content = json.dumps(json.loads(content), ensure_ascii=False, separators=(",", ":"))
    except (ValueError, TypeError):
        pass
    if len(content) > limit:
        content = content[:limit] + " …(kısaltıldı; daha dar bir sorgu yap)"
    return content


class Agent:
    def __init__(self, registry):
        self.registry = registry
        self.history = []

    def reset(self):
        self.history = []

    def tools_for(self, mode, small_mode, texts=()):
        tools = self.registry.specs(include_mutating=(mode == "act"))
        return small.select(tools, texts) if small_mode else tools

    def _shorten_old_results(self):
        tool_msgs = [h for h in self.history if h["role"] == "tool"]
        for h in tool_msgs[:-2]:
            for res in h["results"]:
                if len(res["content"]) > SMALL_OLD_RESULT_CHARS:
                    res["content"] = res["content"][:SMALL_OLD_RESULT_CHARS] + " …(eski sonuç kısaltıldı)"
                res["image"] = None

    def run(self, provider, user_text, images=None, mode="act", executor=None, emit=None,
            should_stop=None, max_steps=25, small_mode=None):
        """executor(ToolCall) -> ToolResult runs a tool (the GUI routes it to the main thread and asks approval).
        emit(event_tuple) reports progress: ("assistant", text), ("tool_call", call), ("tool_result", call, result),
        ("info", text), ("error", text). small_mode: short prompt and fewer tools for 7-14B local models."""
        emit = emit or (lambda *_: None)
        should_stop = should_stop or (lambda: False)
        executor = executor or (lambda call: self.registry.run(call.name, call.args))
        if small_mode is None:
            small_mode = bool(getattr(provider, "small", False))
        self.history.append({"role": "user", "text": user_text, "images": list(images or [])})
        texts = [h["text"] for h in self.history if h["role"] == "user"]
        tools = self.tools_for(mode, small_mode, texts)
        # every registered tool of the mode may run (a small model may know one that was not listed)
        allowed = {t.name for t in self.registry.specs(include_mutating=(mode == "act"))}
        system = prompts.system_prompt(mode, small=small_mode)
        failures = {}
        failed_steps = 0
        changed = False
        verification_retries = 0

        for _ in range(max_steps):
            if should_stop():
                emit(("info", "Durduruldu."))
                return
            reply = provider.chat(system, self.history, tools)
            if getattr(reply, "warning", ""):
                emit(("info", reply.warning))
            if not reply.tool_calls and changed and mode == "act" and self.registry.get("check_design_requirements"):
                # Execute via the GUI executor, never touch FreeCAD from this worker thread.
                verification = ToolCall(f"cadai_verify_{len(self.history)}", "check_design_requirements", {})
                emit(("tool_call", verification))
                try:
                    checked = executor(verification)
                except Exception as e:
                    checked = ToolResult(str(e), is_error=True)
                emit(("tool_result", verification, checked))
                try:
                    report = json.loads(checked.content)
                    status = report.get("status") if not checked.is_error and not report.get("partial") else "error"
                except (ValueError, TypeError, AttributeError):
                    status = "error"
                self.history.append({"role": "assistant", "text": "", "tool_calls": [verification]})
                self.history.append({"role": "tool", "results": [{"id": verification.id, "name": verification.name,
                                     "content": checked.content, "is_error": checked.is_error, "image": None}]})
                if status not in ("pass", "not_configured"):
                    verification_retries += 1
                    if verification_retries <= 2 and not should_stop():
                        emit(("info", "Son doğrulamada karşılanmayan şartlar var; geometri için düzeltme turu başlatıldı."))
                        # A real tool observation supplies the failure; never expose the unverified success reply.
                        continue
                    emit(("error", "Tasarım tamamlanamadı: kayıtlı şartlar hâlâ sağlanmıyor veya doğrulanamıyor. "
                          "Son denetim raporundaki ölçüleri ve işlem geçmişini kontrol edin."))
                    return
                if status == "not_configured":
                    emit(("info", "Kayıtlı tasarım şartı yok; hedeflere uygunluk denetlenmedi."))
            self.history.append({"role": "assistant", "text": reply.text, "tool_calls": reply.tool_calls,
                                 "raw": reply.raw, "raw_kind": provider.kind})
            if reply.text:
                emit(("assistant", reply.text))
            if not reply.tool_calls:
                return
            results = []
            stop_reason = None
            for call in reply.tool_calls:
                resolved = self.registry.resolve_name(call.name)
                if should_stop():
                    result = ToolResult("Kullanıcı işlemi durdurdu.", is_error=True)
                elif resolved is None:
                    result = ToolResult(self.registry.unknown_tool_message(call.name, [t.name for t in tools]),
                                        is_error=True)
                elif resolved not in allowed:
                    result = ToolResult(f"{resolved} bu modda kullanılamaz (PLAN modunda yalnızca inceleme yapılır).",
                                        is_error=True)
                elif verification_retries and resolved == "set_design_requirements":
                    result = ToolResult("Düzeltme turunda hedefler değiştirilemez. Geometriyi düzelt veya eksik bilgiyi açıkla.",
                                        is_error=True)
                else:
                    call.name = resolved
                    try:  # canonical arguments first, so the approval dialog shows what will really run
                        call.args = self.registry.normalized_args(resolved, call.args)
                        emit(("tool_call", call))
                        result = executor(call)
                        if not result.is_error and self.registry.get(resolved).mutates:
                            changed = True
                        emit(("tool_result", call, result))
                    except ToolError as e:
                        result = ToolResult(str(e), is_error=True)
                        emit(("tool_result", call, result))
                content = result.content
                if result.is_error:
                    key = (call.name, json.dumps(call.args, sort_keys=True, ensure_ascii=False, default=str))
                    failures[key] = failures.get(key, 0) + 1
                    if failures[key] >= 2:
                        content += ("\nBu çağrıyı aynı argümanlarla zaten denedin ve aynı hatayı aldın. Argümanları "
                                    "değiştir, başka bir araç kullan ya da kullanıcıya neyin eksik olduğunu sor.")
                    if failures[key] >= MAX_SAME_FAILURE:
                        stop_reason = f"{call.name} aynı argümanlarla {failures[key]} kez hata verdi; durduruldu."
                if small_mode:
                    content = _compact(content, SMALL_RESULT_CHARS)
                results.append({"id": call.id, "name": call.name, "content": content,
                                "is_error": result.is_error,
                                "image": result.image_png_b64 if provider.vision else None})
            # every tool call gets a result, even when stopped, so the history stays valid for the next request
            self.history.append({"role": "tool", "results": results})
            failed_steps = failed_steps + 1 if all(r["is_error"] for r in results) else 0
            if failed_steps >= MAX_FAILED_STEPS:
                stop_reason = f"Art arda {failed_steps} adımda araçlar hata verdi; durduruldu."
            if stop_reason:
                emit(("info", stop_reason + " İsteği daha açık yazmayı ya da daha büyük bir model seçmeyi deneyin."))
                return
            if small_mode:
                self._shorten_old_results()
        emit(("info", f"Adım sınırına ({max_steps}) ulaşıldı. Devam etmek için yeni mesaj yazın."))
