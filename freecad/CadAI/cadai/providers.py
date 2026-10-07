"""LLM providers behind one interface: chat(system, history, tools) -> Reply.

History is provider-neutral; each provider converts it:
  {"role": "user", "text": str, "images": [png_b64, ...]}
  {"role": "assistant", "text": str, "tool_calls": [ToolCall], "raw": provider payload, "raw_kind": str}
  {"role": "tool", "results": [{"id", "name", "content", "is_error", "image"}]}
"""

import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field

from . import config

DEFAULT_NUM_CTX = 16384  # Ollama sizes its default by VRAM (4096 on small GPUs): the prompt + tools would be cut off


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Reply:
    text: str = ""
    tool_calls: list = field(default_factory=list)
    raw: object = None
    stop_reason: str = ""
    warning: str = ""  # shown to the user, e.g. the context window was too small


class ProviderError(Exception):
    pass


def model_timeout(profile, override=None):
    value = override if override is not None else profile.get("timeout_seconds", config.DEFAULT_MODEL_TIMEOUT_SECONDS)
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ProviderError("Model zaman aşımı 1–86400 saniye arasında bir sayı olmalı.")
    if not math.isfinite(value) or not 1 <= value <= 86400:
        raise ProviderError("Model zaman aşımı 1–86400 saniye arasında bir sayı olmalı.")
    return value


LENGTH_WARNING = ("Model yanıtı token/uzunluk sınırında kesildi; bu bir zaman aşımı değil. "
                  "Sunucunun yanıt token sınırını veya bağlam penceresini artırın; gerekirse yeni sohbet başlatın.")


class MissingPackageError(ProviderError):
    """A Python package the provider needs is not installed; the panel offers to install it (cadai.pydeps)."""

    def __init__(self, package, message):
        super().__init__(message)
        self.package = package


def is_ollama(profile):
    if profile.get("kind") == "ollama":
        return True
    if profile.get("kind") != "openai" or profile.get("native_ollama") is False:
        return False
    url = urllib.parse.urlparse(profile.get("base_url") or "")
    return url.port == 11434


def make_provider(profile):
    if profile["kind"] == "anthropic":
        return AnthropicProvider(profile)
    if is_ollama(profile):
        return OllamaProvider(profile)
    return OpenAICompatibleProvider(profile)


# ---------- small-model output repair ----------

THINK_RE = re.compile(r"<think>.*?</think>\s*", re.S | re.I)
CALL_ARG_KEYS = ("arguments", "parameters", "args", "input")
NAME_RE = re.compile(r"[A-Za-z_][\w.\-]*$")
FUNCTION_TAG_RE = re.compile(r"<function=([\w.\-]+)>(.*?)</function>", re.S)
PARAMETER_TAG_RE = re.compile(r"<parameter=([\w\-]+)>\s*(.*?)\s*</parameter>", re.S)
LEFTOVER_RE = re.compile(r"</?(?:tool_call|tool_calls|function_call|toolcall|tools)>|\[TOOL_CALLS\]|<\|python_tag\|>"
                         r"|<\|tool_call\|>|<\|/tool_call\|>|```(?:json|tool_code|tool_call|python)?", re.I)


def strip_thinking(text):
    """Reasoning some local servers leave in the content: it only fills the context of the next request."""
    if not text:
        return ""
    text = THINK_RE.sub("", text)
    if "</think>" in text:  # the opening tag was part of the chat template
        text = text.split("</think>", 1)[1]
    return text.strip()


def _value(text):
    try:
        return json.loads(text)
    except ValueError:
        return text


def _as_call(d, known):
    if not isinstance(d, dict):
        return None
    if isinstance(d.get("function"), dict):  # OpenAI shape written out as text
        d = d["function"]
    name = d.get("name") or d.get("tool") or d.get("tool_name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        return None
    key = next((k for k in CALL_ARG_KEYS if k in d), None)
    if key is None and name not in known:
        return None
    args = d.get(key) if key else {k: v for k, v in d.items() if k not in ("name", "tool", "tool_name")}
    if isinstance(args, str):
        args = _parse_args(args)
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None
    return ToolCall(id="call_" + uuid.uuid4().hex[:12], name=name, args=args)


def extract_text_tool_calls(text, known=()):
    """Tool calls a model wrote into its answer instead of the tool_calls field: <tool_call>{...}</tool_call> (Qwen,
    Hermes), [TOOL_CALLS] [...] (Mistral), <|python_tag|>{...} (Llama), ```json {...}```, <function=name>...</function>
    (Qwen-coder XML). Returns (remaining text, calls)."""
    if not text or ("{" not in text and "<function=" not in text):
        return text or "", []
    known = set(known or ())
    calls, spans = [], []
    for m in FUNCTION_TAG_RE.finditer(text):
        body = m.group(2).strip()
        params = PARAMETER_TAG_RE.findall(body)
        if params:
            args = {k: _value(v) for k, v in params}
        else:
            args = _parse_args(body) if body else {}
        if isinstance(args, dict) and "__invalid_json__" not in args:
            calls.append(ToolCall(id="call_" + uuid.uuid4().hex[:12], name=m.group(1), args=args))
            spans.append(m.span())
    if not calls:
        dec = json.JSONDecoder()
        i = text.find("{")
        while i >= 0:
            try:
                value, end = dec.raw_decode(text, i)
            except ValueError:
                i = text.find("{", i + 1)
                continue
            call = _as_call(value, known)
            if call is not None:
                calls.append(call)
                spans.append((i, end))
                i = text.find("{", end)
            else:
                i = text.find("{", i + 1)
    if not calls:
        return text, []
    rest, last = [], 0
    for a, b in spans:
        rest.append(text[last:a])
        last = b
    rest.append(text[last:])
    remaining = LEFTOVER_RE.sub("", "".join(rest))
    remaining = re.sub(r"^\s*\[[\s,]*\]\s*$", "", remaining, flags=re.M).strip()
    return remaining, calls


def repair_reply(text, calls, known):
    """Clean a local model's answer: drop leaked reasoning, recover tool calls written as text."""
    text = strip_thinking(text)
    if not calls:
        text, calls = extract_text_tool_calls(text, known)
    return text, calls


def _parse_args(raw):
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {"__invalid_json__": raw}
    except ValueError:
        return {"__invalid_json__": raw}


class OpenAICompatibleProvider:
    """OpenAI chat-completions protocol: Ollama, LM Studio, llama.cpp, vLLM, OpenRouter, OpenAI."""

    kind = "openai"

    def __init__(self, profile, timeout=None):
        self.profile = profile
        self.vision = bool(profile.get("vision"))
        self.timeout = model_timeout(profile, timeout)

    def build_payload(self, system, history, tools):
        msgs = [{"role": "system", "content": system}]
        for h in history:
            if h["role"] == "user":
                if self.vision and h.get("images"):
                    content = [{"type": "text", "text": h["text"]}] + [
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + img}}
                        for img in h["images"]]
                else:
                    content = h["text"]
                msgs.append({"role": "user", "content": content})
            elif h["role"] == "assistant":
                m = {"role": "assistant", "content": h.get("text") or ""}
                if h.get("tool_calls"):
                    m["tool_calls"] = [{"id": c.id, "type": "function",
                                        "function": {"name": c.name, "arguments": json.dumps(c.args, ensure_ascii=False)}}
                                       for c in h["tool_calls"]]
                msgs.append(m)
            elif h["role"] == "tool":
                images = []
                for res in h["results"]:
                    msgs.append({"role": "tool", "tool_call_id": res["id"],
                                 "content": ("HATA: " if res["is_error"] else "") + res["content"]})
                    if res.get("image"):
                        images.append(res["image"])
                if images and self.vision:
                    msgs.append({"role": "user", "content": [{"type": "text", "text": "Araçların ürettiği görüntüler:"}]
                                 + [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + i}}
                                    for i in images]})
        payload = {"model": self.profile["model"], "messages": msgs}
        if tools:
            payload["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                                  "parameters": t.schema}} for t in tools]
            payload["tool_choice"] = "auto"
        if self.profile.get("temperature") is not None:
            payload["temperature"] = float(self.profile["temperature"])
        return payload

    def chat(self, system, history, tools):
        if not self.profile.get("model"):
            raise ProviderError("Profilde model adı boş. Ayarlar'dan model girin.")
        url = self.profile["base_url"].rstrip("/") + "/chat/completions"
        body = json.dumps(self.build_payload(system, history, tools)).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        key = config.resolve_api_key(self.profile)
        if key:
            headers["Authorization"] = "Bearer " + key
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:2000]
            raise ProviderError(f"{url} HTTP {e.code}: {detail}")
        except urllib.error.URLError as e:
            raise ProviderError(f"{url} adresine bağlanılamadı ({e.reason}). Sunucu (Ollama/LM Studio) açık mı?")
        return self.parse_response(data, [t.name for t in tools or []])

    @staticmethod
    def parse_response(data, known=()):
        try:
            choice = data["choices"][0]
            msg = choice["message"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError(f"Beklenmeyen yanıt: {str(data)[:1000]}")
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            calls.append(ToolCall(id=tc.get("id") or "call_" + uuid.uuid4().hex[:12], name=fn.get("name", ""),
                                  args=_parse_args(fn.get("arguments"))))
        text, calls = repair_reply(msg.get("content") or "", calls, known)
        stop = choice.get("finish_reason") or ""
        return Reply(text=text, tool_calls=calls, raw=msg, stop_reason=stop,
                     warning=(LENGTH_WARNING
                              if stop == "length" else ""))


class OllamaProvider:
    """Ollama's own /api/chat. Unlike its OpenAI-compatible endpoint it takes options.num_ctx: Ollama gives 4096
    tokens by default on small GPUs, and CadAI's prompt plus tools would be silently cut from the front."""

    kind = "ollama"

    def __init__(self, profile, timeout=None):
        self.profile = profile
        self.vision = bool(profile.get("vision"))
        self.timeout = model_timeout(profile, timeout)
        self.num_ctx = int(profile.get("num_ctx") or DEFAULT_NUM_CTX)

    def root(self):
        url = (self.profile.get("base_url") or "http://localhost:11434").rstrip("/")
        for suffix in ("/v1", "/api"):
            if url.endswith(suffix):
                url = url[: -len(suffix)]
        return url

    def build_payload(self, system, history, tools):
        msgs = [{"role": "system", "content": system}]
        for h in history:
            if h["role"] == "user":
                m = {"role": "user", "content": h["text"]}
                if self.vision and h.get("images"):
                    m["images"] = list(h["images"])
                msgs.append(m)
            elif h["role"] == "assistant":
                m = {"role": "assistant", "content": h.get("text") or ""}
                if h.get("tool_calls"):
                    m["tool_calls"] = [{"function": {"name": c.name, "arguments": c.args}} for c in h["tool_calls"]]
                msgs.append(m)
            elif h["role"] == "tool":
                images = []
                for res in h["results"]:
                    msgs.append({"role": "tool", "tool_name": res["name"],
                                 "content": ("HATA: " if res["is_error"] else "") + res["content"]})
                    if res.get("image"):
                        images.append(res["image"])
                if images and self.vision:
                    msgs.append({"role": "user", "content": "Araçların ürettiği görüntüler:", "images": images})
        payload = {"model": self.profile["model"], "messages": msgs, "stream": False,
                   "options": {"num_ctx": self.num_ctx}}
        if self.profile.get("temperature") is not None:
            payload["options"]["temperature"] = float(self.profile["temperature"])
        if self.profile.get("think") is not None:
            payload["think"] = bool(self.profile["think"])
        if tools:
            payload["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                                  "parameters": t.schema}} for t in tools]
        return payload

    def chat(self, system, history, tools):
        model = self.profile.get("model")
        if not model:
            raise ProviderError("Profilde model adı boş. Ayarlar'dan model girin (ör. qwen3:8b).")
        url = self.root() + "/api/chat"
        body = json.dumps(self.build_payload(system, history, tools)).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:2000]
            if e.code == 404 and "not found" in detail:
                raise ProviderError(f"Ollama'da '{model}' modeli yok. İndirmek için: ollama pull {model}")
            if "does not support tools" in detail:
                raise ProviderError(f"'{model}' araç çağırmayı desteklemiyor. Araç destekli bir model seçin "
                                    "(ör. qwen3, qwen3.5, llama3.1, mistral-nemo).")
            raise ProviderError(f"{url} HTTP {e.code}: {detail}")
        except urllib.error.URLError as e:
            raise ProviderError(f"{url} adresine bağlanılamadı ({e.reason}). Ollama açık mı?")
        return self.parse_response(data, [t.name for t in tools or []], self.num_ctx)

    @staticmethod
    def parse_response(data, known=(), num_ctx=DEFAULT_NUM_CTX):
        msg = data.get("message") if isinstance(data, dict) else None
        if not isinstance(msg, dict):
            raise ProviderError(f"Beklenmeyen yanıt: {str(data)[:1000]}")
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            calls.append(ToolCall(id=tc.get("id") or "call_" + uuid.uuid4().hex[:12], name=fn.get("name", ""),
                                  args=_parse_args(fn.get("arguments"))))
        text, calls = repair_reply(msg.get("content") or "", calls, known)
        used = int(data.get("prompt_eval_count") or 0) + int(data.get("eval_count") or 0)
        warning = ""
        if used >= num_ctx - 32:
            warning = (f"Bağlam penceresi doldu ({used}/{num_ctx} token): model konuşmanın başını göremiyor olabilir. "
                       "Ayarlar'da bağlamı (num_ctx) büyütün ya da yeni sohbet başlatın.")
        elif data.get("done_reason") == "length":
            warning = LENGTH_WARNING
        return Reply(text=text, tool_calls=calls, raw=msg, stop_reason=data.get("done_reason") or "", warning=warning)


FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
EFFORT_PREFIXES = ("claude-opus-", "claude-fable-", "claude-sonnet-5")


class AnthropicProvider:
    """Claude through the official Anthropic Python SDK."""

    kind = "anthropic"

    def __init__(self, profile, effort="high"):
        self.profile = profile
        self.vision = True
        self.effort = effort
        self.timeout = model_timeout(profile)
        self._client = None

    def client(self):
        if self._client is None:
            from . import pydeps

            pydeps.ensure_path()  # the SDK may be installed in CadAI's own package folder
            try:
                import anthropic
            except ImportError as e:
                import os

                if os.path.isdir(os.path.join(pydeps.lib_dir(), "anthropic")):  # installed but broken
                    raise ProviderError(f"'anthropic' paketi yüklenemedi ({e}). Klasör: {pydeps.lib_dir()} — "
                                        "Windows'ta yol 260 karakteri aşıyorsa uzun yol desteğini açın.")
                raise MissingPackageError("anthropic", "Claude için Anthropic Python paketi (anthropic) kurulu değil. "
                                                       "Panel kurmayı önerir; yönetici izni gerekmez.")
            kwargs = {"api_key": config.resolve_api_key(self.profile) or None, "timeout": self.timeout}
            if self.profile.get("base_url"):
                kwargs["base_url"] = self.profile["base_url"]
            self._client = anthropic.Anthropic(**kwargs)
        return self._client

    @staticmethod
    def _image_block(b64):
        return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}

    def build_messages(self, history):
        msgs = []

        def add(role, blocks):
            if msgs and msgs[-1]["role"] == role:
                msgs[-1]["content"].extend(blocks)
            else:
                msgs.append({"role": role, "content": list(blocks)})

        for h in history:
            if h["role"] == "user":
                add("user", [self._image_block(i) for i in h.get("images") or []]
                    + [{"type": "text", "text": h["text"]}])
            elif h["role"] == "assistant":
                if h.get("raw_kind") == "anthropic" and h.get("raw") is not None:
                    msgs.append({"role": "assistant", "content": h["raw"]})  # keeps thinking blocks unchanged
                else:
                    blocks = [{"type": "text", "text": h["text"]}] if h.get("text") else []
                    blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.args}
                               for c in h.get("tool_calls") or []]
                    msgs.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "."}]})
            elif h["role"] == "tool":
                blocks = []
                for res in h["results"]:
                    content = [{"type": "text", "text": res["content"] or "(boş)"}]
                    if res.get("image"):
                        content.append(self._image_block(res["image"]))
                    blocks.append({"type": "tool_result", "tool_use_id": res["id"], "content": content,
                                   "is_error": bool(res["is_error"])})
                add("user", blocks)
        return msgs

    def chat(self, system, history, tools):
        model = self.profile.get("model") or "claude-opus-5-5"
        kwargs = {"model": model, "max_tokens": 16000, "system": system, "messages": self.build_messages(history)}
        if tools:
            kwargs["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.schema}
                               for t in tools]
        if model.startswith(EFFORT_PREFIXES):
            kwargs["output_config"] = {"effort": self.effort}
        client = self.client()
        import anthropic  # importable now: client() put CadAI's package folder on sys.path

        try:
            if model in FALLBACK_MODELS and not self.profile.get("base_url"):
                resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                                   **kwargs)
            else:
                resp = client.messages.create(**kwargs)
        except anthropic.AuthenticationError:
            raise ProviderError("Anthropic API anahtarı geçersiz ya da eksik (ANTHROPIC_API_KEY).")
        except anthropic.RateLimitError:
            raise ProviderError("Anthropic hız sınırı aşıldı; biraz sonra tekrar deneyin.")
        except anthropic.APIStatusError as e:
            if "credit balance" in str(e.message).lower():
                raise ProviderError("Anthropic hesabınızda API kredisi yok. console.anthropic.com → Plans & Billing'den "
                                    "kredi yükleyin (Claude Pro/Max aboneliği API kredisi sayılmaz), sonra yeniden "
                                    "gönderin.")
            raise ProviderError(f"Anthropic API hatası {e.status_code}: {e.message}")
        except TypeError as e:  # the SDK found no credential at all (no key, token or `ant auth` profile)
            if "authentication" not in str(e):
                raise
            raise ProviderError("Claude için API anahtarı yok. console.anthropic.com → API Keys'ten bir anahtar alıp "
                                "Ayarlar'da 'API anahtarı' kutusuna yapıştırın ya da ANTHROPIC_API_KEY ortam "
                                "değişkenini tanımlayıp FreeCAD'i yeniden başlatın.")
        except anthropic.APIConnectionError:
            raise ProviderError("Anthropic API'ye bağlanılamadı (internet bağlantısı?).")

        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            why = getattr(details, "explanation", None) or "model isteği reddetti"
            return Reply(text=f"(Model yanıt vermeyi reddetti: {why})", raw=None, stop_reason="refusal")
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(id=b.id, name=b.name, args=b.input if isinstance(b.input, dict) else {})
                 for b in resp.content if b.type == "tool_use"]
        return Reply(text=text, tool_calls=calls, raw=resp.content, stop_reason=resp.stop_reason or "")
