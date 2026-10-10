"""Small local models (7-14B): forgiving tool calls, recovered text tool calls, small toolset. No FreeCAD needed:
    python -m unittest freecad/CadAI/tests/test_small_models.py
"""

import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "mcp_server"))
# These suites test the single-program servers; auto mode (default) has its own end-to-end tests.
os.environ.setdefault("CADAI_BACKEND", "freecad")


from cadai import config, prompts
from cadai.agent import Agent
from cadai.providers import (
    AnthropicProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    ProviderError,
    Reply,
    ToolCall,
    extract_text_tool_calls,
    is_ollama,
    make_provider,
    strip_thinking,
)
from cadai.tools import Registry, Tool, ToolError, ToolResult, small

VEC = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}


def fake_registry():
    calls = []

    def record(name):
        def fn(**kw):
            calls.append((name, kw))
            return {"ok": True, "args": kw}
        return fn

    reg = Registry()
    reg.register(Tool("get_document_summary", "List objects.", {"type": "object", "properties": {
        "include_fem": {"type": "boolean"}}}, record("get_document_summary"), example={}))
    reg.register(Tool("find_faces", "Find faces.", {"type": "object", "properties": {
        "object": {"type": "string"}, "normal": {"type": "array", "items": {"type": "number"}},
        "extreme": {"type": "string", "enum": ["min_x", "max_x", "min_z", "max_z"]},
        "radius_mm": {"type": "number"}}, "required": ["object"]}, record("find_faces"),
        example={"object": "Plate", "normal": [0, 0, 1]}))
    reg.register(Tool("make_hole", "Drill.", {"type": "object", "properties": {
        "object": {"type": "string"}, "position": VEC, "diameter": {"type": "number"},
        "depth": {"type": "number"}}, "required": ["object", "position"]}, record("make_hole"), mutates=True))
    reg.register(Tool("fem_setup", "FEM.", {"type": "object", "properties": {
        "object": {"type": "string"}, "fixed_faces": {"type": "array", "items": {"type": "string"}},
        "forces": {"type": "array", "items": {"type": "object", "properties": {
            "faces": {"type": "array", "items": {"type": "string"}}, "force_n": {"type": "number"},
            "direction": {"type": "array", "items": {"type": "number"}}}}},
        "material": {"type": "string", "enum": ["steel", "aluminum", "pla"]}},
        "required": ["object", "fixed_faces"]}, record("fem_setup"), mutates=True))
    reg.register(Tool("search_parts", "Parts.", {"type": "object", "properties": {
        "query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}, record("search_parts")))
    reg.register(Tool("run_python", "Python.", {"type": "object", "properties": {
        "code": {"type": "string"}, "description": {"type": "string"}}, "required": ["code"]},
        record("run_python"), mutates=True))
    reg.register(Tool("fillet_edges", "Fillet.", {"type": "object", "properties": {
        "object": {"type": "string"}, "edges": {"type": ["array", "string"], "items": {"type": "string"}},
        "radius": {"type": "number"}}, "required": ["object", "edges", "radius"]}, record("fillet_edges"),
        mutates=True))
    return reg, calls


class ForgivingArguments(unittest.TestCase):
    def setUp(self):
        self.reg, self.calls = fake_registry()

    def ok(self, name, a):
        res = self.reg.run(name, a)
        self.assertFalse(res.is_error, res.content)
        return self.calls[-1][1]

    def test_aliases_and_types(self):
        got = self.ok("make_hole", {"obj": "Plate", "center": "40, 30, 10", "Diameter": "8 mm", "depth": None})
        self.assertEqual(got, {"object": "Plate", "position": [40, 30, 10], "diameter": 8.0})
        got = self.ok("make_hole", {"object_name": "Plate", "position": {"x": 1, "y": 2, "z": 3}, "dia": "Ø6,5"})
        self.assertEqual(got, {"object": "Plate", "position": [1, 2, 3], "diameter": 6.5})
        got = self.ok("find_faces", {"object": "Beam", "extreme": "MAX X", "normal": "[0, 0, 1]"})
        self.assertEqual(got, {"object": "Beam", "extreme": "max_x", "normal": [0, 0, 1]})
        got = self.ok("search_parts", {"q": "M8", "limit": "5"})
        self.assertEqual(got, {"query": "M8", "limit": 5})

    def test_face_names_and_nested_objects(self):
        got = self.ok("fem_setup", {"object": "Beam", "fixed": ["face1", 3, "Beam:Face4"],
                                    "forces": {"face": "Face2", "force": "500 N", "dir": "0 0 -1"},
                                    "material": "Steel"})
        self.assertEqual(got["fixed_faces"], ["Face1", "Face3", "Face4"])
        self.assertEqual(got["forces"], [{"faces": ["Face2"], "force_n": 500.0, "direction": [0, 0, -1]}])
        self.assertEqual(got["material"], "steel")
        got = self.ok("fillet_edges", {"object": "P", "edges": "vertical", "radius": 2})
        self.assertEqual(got["edges"], "vertical")
        got = self.ok("fillet_edges", {"object": "P", "edges": [1, "edge3"], "radius": 2})
        self.assertEqual(got["edges"], ["Edge1", "Edge3"])

    def test_wrappers_and_strings(self):
        self.assertEqual(self.ok("find_faces", {"arguments": {"object": "Beam"}}), {"object": "Beam"})
        self.assertEqual(self.ok("find_faces", {"name": "find_faces", "arguments": '{"object": "Beam"}'}),
                         {"object": "Beam"})
        self.assertEqual(self.ok("find_faces", '{"object": "Beam"}'), {"object": "Beam"})
        self.assertEqual(self.ok("get_document_summary", {"anything": 1}), {})
        self.assertEqual(self.ok("search_parts", {"part": "608 bearing"}), {"query": "608 bearing"})

    def test_errors_show_a_correct_call(self):
        res = self.reg.run("find_faces", {"normal": [0, 0, 1]})
        self.assertTrue(res.is_error)
        self.assertIn("eksik zorunlu argüman: object", res.content)
        self.assertIn('find_faces {"object": "Plate", "normal": [0, 0, 1]}', res.content)
        res = self.reg.run("fem_setup", {"object": "B", "fixed_faces": ["Face1"], "material": "unobtainium"})
        self.assertTrue(res.is_error)
        self.assertIn("steel, aluminum, pla", res.content)
        res = self.reg.run("make_hole", {"object": "P", "position": [0, 0, 0], "diameter": "big"})
        self.assertIn("'diameter' için geçersiz değer", res.content)

    def test_ignored_arguments_are_reported(self):
        res = self.reg.run("find_faces", {"object": "Beam", "colour": "red"})
        self.assertEqual(json.loads(res.content)["ignored_arguments"], ["colour"])

    def test_tool_names(self):
        for name in ("functions.find_faces", "cadai-freecad__find_faces", "Find-Faces", " find_faces "):
            self.assertEqual(self.reg.resolve_name(name), "find_faces", name)
        self.assertEqual(self.reg.resolve_name("drill_hole"), "make_hole")
        self.assertIsNone(self.reg.resolve_name("create_sphere"))
        res = self.reg.run("drill_hole", {"object": "P", "position": [0, 0, 0]})
        self.assertFalse(res.is_error)
        res = self.reg.run("find_face", {"object": "P"})  # alias
        self.assertFalse(res.is_error)
        res = self.reg.run("fnd_faces", {"object": "P"})
        self.assertTrue(res.is_error)
        self.assertIn("find_faces", res.content)

    def test_normalized_args_for_approval(self):
        a = self.reg.normalized_args("run_python", {"script": "print(1)"})
        self.assertEqual(a, {"code": "print(1)"})
        with self.assertRaises(ToolError):
            self.reg.normalized_args("run_python", {})


class TextToolCalls(unittest.TestCase):
    KNOWN = ["find_faces", "measure", "get_document_summary"]

    def test_formats(self):
        cases = [
            '<tool_call>\n{"name": "find_faces", "arguments": {"object": "Beam"}}\n</tool_call>',
            'Önce yüzleri bulayım.\n```json\n{"name": "find_faces", "arguments": {"object": "Beam"}}\n```',
            '[TOOL_CALLS] [{"name": "find_faces", "arguments": {"object": "Beam"}}]',
            '<|python_tag|>{"name": "find_faces", "parameters": {"object": "Beam"}}',
            '<function=find_faces>\n<parameter=object>\nBeam\n</parameter>\n</function>',
            '<function=find_faces>{"object": "Beam"}</function>',
            '{"type": "function", "function": {"name": "find_faces", "arguments": "{\\"object\\": \\"Beam\\"}"}}',
        ]
        for text in cases:
            rest, calls = extract_text_tool_calls(text, self.KNOWN)
            self.assertEqual([(c.name, c.args) for c in calls], [("find_faces", {"object": "Beam"})], text)
            self.assertNotIn("{", rest)
            self.assertNotIn("tool_call", rest)
        rest, calls = extract_text_tool_calls(
            '<tool_call>{"name": "get_document_summary", "arguments": {}}</tool_call>\n'
            '<tool_call>{"name": "measure", "arguments": {"objects": ["Beam"]}}</tool_call>', self.KNOWN)
        self.assertEqual([c.name for c in calls], ["get_document_summary", "measure"])

    def test_plain_answers_stay_text(self):
        for text in ("Kiriş 100 mm uzunluğunda.", 'Ayar örneği: {"a": 1}', '{"name": "Beam", "volume": 3}'):
            rest, calls = extract_text_tool_calls(text, self.KNOWN)
            self.assertEqual((rest, calls), (text, []))

    def test_thinking_is_dropped(self):
        self.assertEqual(strip_thinking("<think>uzun düşünce</think>\nTamam."), "Tamam.")
        self.assertEqual(strip_thinking("uzun düşünce</think>Tamam."), "Tamam.")

    def test_openai_reply_recovers_calls(self):
        reply = OpenAICompatibleProvider.parse_response({"choices": [{"finish_reason": "stop", "message": {
            "content": '<think>x</think><tool_call>{"name": "measure", "arguments": {"objects": ["Beam"]}}</tool_call>'}}]},
            self.KNOWN)
        self.assertEqual(reply.text, "")
        self.assertEqual(reply.tool_calls[0].name, "measure")


class Ollama(unittest.TestCase):
    def test_native_api_with_context_size(self):
        profile = {"kind": "openai", "base_url": "http://localhost:11434/v1", "model": "qwen3:8b", "num_ctx": 12288}
        self.assertTrue(is_ollama(profile))
        p = make_provider(profile)
        self.assertIsInstance(p, OllamaProvider)
        self.assertEqual(p.root(), "http://localhost:11434")
        self.assertFalse(is_ollama(dict(profile, base_url="http://localhost:1234/v1")))
        self.assertFalse(is_ollama(dict(profile, native_ollama=False)))
        reg, _ = fake_registry()
        history = [{"role": "user", "text": "hi", "images": []},
                   {"role": "assistant", "text": "", "tool_calls": [ToolCall("t1", "find_faces", {"object": "B"})]},
                   {"role": "tool", "results": [{"id": "t1", "name": "find_faces", "content": "{}", "is_error": False}]}]
        payload = p.build_payload("sys", history, reg.specs())
        self.assertEqual(payload["options"], {"num_ctx": 12288})
        self.assertIs(payload["stream"], False)
        self.assertEqual(payload["messages"][2]["tool_calls"][0]["function"]["arguments"], {"object": "B"})
        self.assertEqual(payload["messages"][3], {"role": "tool", "tool_name": "find_faces", "content": "{}"})

    def test_parse_and_context_warning(self):
        data = {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "find_faces", "arguments": {"object": "B"}}}]},
            "done_reason": "stop", "prompt_eval_count": 500, "eval_count": 20}
        reply = OllamaProvider.parse_response(data, num_ctx=16384)
        self.assertEqual((reply.tool_calls[0].name, reply.tool_calls[0].args, reply.warning), ("find_faces", {"object": "B"}, ""))
        full = dict(data, prompt_eval_count=4090, eval_count=6)
        self.assertIn("Bağlam penceresi doldu", OllamaProvider.parse_response(full, num_ctx=4096).warning)


class ModelTimeout(unittest.TestCase):
    def test_existing_profiles_get_two_hours_and_overrides_win(self):
        for cls in (OpenAICompatibleProvider, OllamaProvider, AnthropicProvider):
            self.assertEqual(cls({}).timeout, 7200)
            self.assertEqual(cls({"timeout_seconds": 86400}).timeout, 86400)
        for cls in (OpenAICompatibleProvider, OllamaProvider):
            self.assertEqual(cls({"timeout_seconds": 86400}, timeout=30).timeout, 30)

    def test_invalid_timeouts_fail_before_request(self):
        for value in (0, -1, 86401, "bad", None, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ProviderError):
                make_provider({"kind": "openai", "timeout_seconds": value})

    def test_network_requests_use_profile_timeout(self):
        cases = [(OpenAICompatibleProvider, {"choices": [{"message": {"content": "ok"}}]}),
                 (OllamaProvider, {"message": {"content": "ok"}})]
        for cls, response in cases:
            with self.subTest(provider=cls.__name__), mock.patch("cadai.providers.urllib.request.urlopen") as request:
                request.return_value.__enter__.return_value.read.return_value = json.dumps(response).encode()
                provider = cls({"base_url": "http://localhost:1234/v1", "model": "local", "timeout_seconds": 18000})
                self.assertEqual(provider.chat("system", [], []).text, "ok")
                self.assertEqual(request.call_args.kwargs["timeout"], 18000)

    def test_anthropic_client_receives_timeout(self):
        sdk = mock.Mock()
        with mock.patch.dict(sys.modules, {"anthropic": sdk}), mock.patch("cadai.pydeps.ensure_path"):
            provider = AnthropicProvider({"api_key": "test", "timeout_seconds": 24000})
            self.assertIs(provider.client(), sdk.Anthropic.return_value)
            self.assertEqual(sdk.Anthropic.call_args.kwargs["timeout"], 24000)

    def test_length_warning_is_not_a_timeout_or_false_context_overflow(self):
        reply = OpenAICompatibleProvider.parse_response({"choices": [{"finish_reason": "length",
                                                                      "message": {"content": "partial"}}]})
        self.assertIn("bu bir zaman aşımı değil", reply.warning)
        reply = OllamaProvider.parse_response({"message": {"content": "partial"}, "done_reason": "length",
                                              "prompt_eval_count": 100, "eval_count": 20})
        self.assertIn("bu bir zaman aşımı değil", reply.warning)
        self.assertNotIn("Bağlam penceresi doldu", reply.warning)


class SmallMode(unittest.TestCase):
    def test_auto_detection(self):
        local = {"kind": "openai", "base_url": "http://localhost:11434/v1"}
        for model, small_ in (("qwen3:8b", True), ("Qwen3.5-9B-Instruct", True), ("gemma3:12b-it-q4", True),
                              ("qwen3:14b", True), ("qwen3.6", True), ("qwen3:32b", False), ("qwen3-30b-a3b", False),
                              ("llama3.1:70b", False), ("local-model", True)):
            self.assertEqual(config.is_small_model(dict(local, model=model)), small_, model)
        self.assertFalse(config.is_small_model({"kind": "openai", "base_url": "https://openrouter.ai/api/v1",
                                                "model": "qwen3:8b"}))
        self.assertFalse(config.is_small_model({"kind": "anthropic", "model": "claude-opus-5-5"}))
        self.assertTrue(config.is_small_model({"kind": "anthropic", "small_model": "on"}))
        self.assertFalse(config.is_small_model(dict(local, model="qwen3:8b", small_model="off")))

    def test_tool_selection_follows_the_conversation(self):
        base = small.tool_names(["Nesnenin yüksekliğini 12 milimetre yap"])
        self.assertEqual(base, small.CORE)  # "yükseklik" is not a load, "milimetre" is not a shaft
        self.assertLessEqual(len(base), 16)  # the read-only design checker must remain available after reopening a file
        self.assertIn("check_design_requirements", base)
        self.assertNotIn("set_design_requirements", base)
        self.assertIn("set_design_requirements", small.tool_names(["Bu boşluğu koru ve doğrula"]))
        self.assertIn("add_mounting_plate", small.tool_names(["Dört delikli plaka yap"]))
        fem = small.tool_names(["Uç yüze 500 N yük uygula, gerilmeye bak"])
        self.assertIn("fem_setup", fem)
        self.assertIn("search_parts", small.tool_names(["M8 cıvata ekle"]))
        self.assertIn("technical_drawing", small.tool_names(["Teknik resmini çıkar"]))
        self.assertIn("export_model", small.tool_names(["STEP olarak dışa aktar"]))
        for name in small.CORE:
            self.assertIn(name, small.SHORT)

    def test_trimmed_schema_keeps_required(self):
        reg, _ = fake_registry()
        views = {v.name: v for v in small.select(reg.specs(), ["yük analizi"])}
        self.assertEqual(set(views["fem_setup"].schema["properties"]),
                         {"object", "fixed_faces", "forces", "material"})
        self.assertNotIn("search_parts", views)

    def test_small_prompt_is_short(self):
        self.assertLess(len(prompts.system_prompt("act", small=True)), len(prompts.system_prompt("act")))
        self.assertIn("make_hole", prompts.SMALL)


class RegistryRequirements(unittest.TestCase):
    def test_checks_preserve_mutation_result_and_image(self):
        reg = Registry()
        schema = {"type": "object", "properties": {}}
        check = mock.Mock(return_value={"status": "fail", "failed": 1})
        reg.register(Tool("check_design_requirements", "", schema, check))
        for payload in ({"ok": True}, ToolResult('{"ok": true}', image_png_b64="image"), "changed"):
            mutate = mock.Mock(return_value=payload)
            reg.register(Tool("edit", "", schema, mutate, mutates=True))
            result = reg.run("edit", {})
            self.assertFalse(result.is_error, "a design failure must not imply the mutation rolled back")
            self.assertEqual(json.loads(result.content)["design_validation"]["status"], "fail")
            if isinstance(payload, ToolResult):
                self.assertEqual(result.image_png_b64, "image")
                self.assertTrue(json.loads(result.content)["ok"])
            mutate.assert_called_once()
        self.assertEqual(check.call_count, 3)
        reg.register(Tool("read", "", schema, lambda: {"ok": True}))
        self.assertNotIn("design_validation", json.loads(reg.run("read", {}).content))
        reg.register(Tool("rejected", "", schema, lambda: ToolResult("rejected", is_error=True), mutates=True))
        self.assertTrue(reg.run("rejected", {}).is_error)
        self.assertEqual(check.call_count, 3)

    def test_checker_failure_does_not_hide_completed_operation(self):
        reg = Registry()
        schema = {"type": "object", "properties": {}}
        check = mock.Mock(side_effect=RuntimeError("unavailable"))
        reg.register(Tool("check_design_requirements", "", schema, check))
        reg.register(Tool("edit", "", schema, lambda: {"ok": True}, mutates=True))
        result = reg.run("edit", {})
        self.assertFalse(result.is_error)
        payload = json.loads(result.content)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["design_validation"]["status"], "error")


class FakeProvider:
    kind = "fake"
    vision = False

    def __init__(self, replies, small_=True):
        self.replies = list(replies)
        self.seen = []
        self.small = small_

    def chat(self, system, history, tools):
        self.seen.append((system, [t.name for t in tools]))
        return self.replies.pop(0)


class AgentLoop(unittest.TestCase):
    def test_final_success_is_withheld_until_actual_checks_pass(self):
        reg, _ = fake_registry()
        state = {"status": "fail"}
        schema = {"type": "object", "properties": {}}
        reg.register(Tool("check_design_requirements", "", schema, lambda: dict(state)))
        reg.register(Tool("repair", "", schema, lambda: state.update(status="pass") or {"ok": True}, mutates=True))
        fake = FakeProvider([
            Reply(tool_calls=[ToolCall("m", "run_python", {"code": "edit"})]),
            Reply(text="Yanlış başarı bildirimi"),
            Reply(tool_calls=[ToolCall("r", "repair", {})]), Reply(text="Doğrulandı")])
        events = []
        agent = Agent(reg)
        agent.run(fake, "düzenle", emit=events.append)
        self.assertNotIn(("assistant", "Yanlış başarı bildirimi"), events)
        self.assertIn(("assistant", "Doğrulandı"), events)
        self.assertTrue(any(e[0] == "info" and "düzeltme" in e[1] for e in events))
        for i, entry in enumerate(agent.history):
            if entry["role"] == "assistant" and entry.get("tool_calls"):
                self.assertEqual(agent.history[i + 1]["role"], "tool")

    def test_final_verification_stops_after_bounded_repairs(self):
        reg, _ = fake_registry()
        reg.register(Tool("check_design_requirements", "", {"type": "object", "properties": {}},
                          lambda: {"status": "fail", "failed": 1}))
        fake = FakeProvider([Reply(tool_calls=[ToolCall("m", "run_python", {"code": "edit"})])]
                            + [Reply(text="Başarılı") for _ in range(3)])
        events = []
        Agent(reg).run(fake, "düzenle", emit=events.append)
        self.assertNotIn(("assistant", "Başarılı"), events)
        self.assertTrue(any(e[0] == "error" and "tamamlanamadı" in e[1] for e in events))
        self.assertEqual(len(fake.seen), 4)

    def test_small_mode_tools_prompt_and_aliases(self):
        reg, calls = fake_registry()
        fake = FakeProvider([Reply(tool_calls=[ToolCall("1", "drill_hole", {"obj": "P", "center": "1,2,3"})]),
                             Reply(text="Delik açıldı.")])
        events = []
        Agent(reg).run(fake, "Deliği aç", emit=events.append)
        self.assertEqual(calls, [("make_hole", {"object": "P", "position": [1, 2, 3]})])
        self.assertIn("Rules:", fake.seen[0][0])
        self.assertNotIn("search_parts", fake.seen[0][1])
        call = next(e[1] for e in events if e[0] == "tool_call")
        self.assertEqual((call.name, call.args), ("make_hole", {"object": "P", "position": [1, 2, 3]}))

    def test_repeated_failure_stops_the_turn(self):
        reg, _ = fake_registry()
        bad = ToolCall("x", "find_faces", {"normal": [0, 0, 1]})
        fake = FakeProvider([Reply(tool_calls=[bad]) for _ in range(5)])
        events = []
        agent = Agent(reg)
        agent.run(fake, "yüz bul", emit=events.append, max_steps=10)
        self.assertEqual(len(fake.seen), 3)
        self.assertIn("zaten denedin", agent.history[-1]["results"][0]["content"])
        self.assertTrue(any(e[0] == "info" and "durduruldu" in e[1] for e in events))

    def test_plan_mode_and_unknown_tools(self):
        reg, calls = fake_registry()
        fake = FakeProvider([Reply(tool_calls=[ToolCall("1", "make_hole", {"object": "P", "position": [0, 0, 0]}),
                                               ToolCall("2", "create_sphere", {})]),
                             Reply(text="Plan: ...")])
        agent = Agent(reg)
        agent.run(fake, "delik", mode="plan")
        res = agent.history[2]["results"]
        self.assertTrue(res[0]["is_error"] and "PLAN" in res[0]["content"])
        self.assertTrue(res[1]["is_error"] and "Bilinmeyen araç" in res[1]["content"])
        self.assertEqual(calls, [])

    def test_old_results_are_shortened(self):
        reg, _ = fake_registry()
        fake = FakeProvider([Reply(tool_calls=[ToolCall(str(i), "find_faces", {"object": "X" * 900 + str(i)})])
                             for i in range(4)] + [Reply(text="ok")])
        agent = Agent(reg)
        agent.run(fake, "bul")
        tool_msgs = [h for h in agent.history if h["role"] == "tool"]
        self.assertIn("eski sonuç kısaltıldı", tool_msgs[0]["results"][0]["content"])
        self.assertNotIn("kısaltıldı", tool_msgs[-1]["results"][0]["content"])


class McpToolset(unittest.TestCase):
    def test_small_toolset_from_environment(self):
        import cadai_mcp

        tool = Tool("find_faces", "Long description " * 20, {"type": "object", "properties": {
            "object": {"type": "string"}, "angle_tol_deg": {"type": "number"}, "normal": {"type": "array"}},
            "required": ["object"]}, None)
        fem = Tool("fem_setup", "FEM", {"type": "object", "properties": {"object": {"type": "string"},
                                                                       "fixed_faces": {"type": "array"}}}, None)
        manifest = [tool.manifest(), fem.manifest()]
        self.assertEqual(manifest[0]["small"]["group"], "core")

        class Client:
            def request(self, method, path, payload=None, timeout=None):
                return manifest

        with mock.patch.dict(os.environ, {"CADAI_TOOLSET": "small"}):
            tools = {t["name"]: t for t in cadai_mcp.list_tools(Client())}
            self.assertEqual(set(tools), {"freecad_bridge_status", "find_faces"})
            self.assertEqual(set(tools["find_faces"]["inputSchema"]["properties"]), {"object", "normal"})
            self.assertIn("Rules:", cadai_mcp.instructions())
        with mock.patch.dict(os.environ, {"CADAI_TOOLSET": "small+fem"}):
            self.assertIn("fem_setup", {t["name"] for t in cadai_mcp.list_tools(Client())})
        with mock.patch.dict(os.environ, {"CADAI_TOOLSET": ""}):
            tools = {t["name"]: t for t in cadai_mcp.list_tools(Client())}
            self.assertIn("angle_tol_deg", tools["find_faces"]["inputSchema"]["properties"])


if __name__ == "__main__":
    unittest.main()
