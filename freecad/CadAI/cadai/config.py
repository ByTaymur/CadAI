"""Settings: model profiles and agent options, stored as JSON in the FreeCAD user dir."""

import copy
import json
import os
import re
import urllib.parse

DEFAULT_MODEL_TIMEOUT_SECONDS = 7200

DEFAULT_CONFIG = {
    "active_profile": "Ollama (yerel)",
    "auto_approve": False,
    "max_steps": 25,
    "bridge_autostart": True,   # start the VS Code/MCP bridge when FreeCAD starts
    "bridge_port": 47800,
    "bridge_approval": False,   # VS Code agents (Cline etc.) already ask before mutating tools
    "profiles": [
        {
            "name": "Ollama (yerel)",
            "kind": "openai",
            "base_url": "http://localhost:11434/v1",
            "model": "qwen3:14b",
            "api_key": "ollama",
            "api_key_env": "",
            "vision": False,
            "small_model": "auto",
            "num_ctx": 16384,
        },
        {
            "name": "LM Studio (yerel)",
            "kind": "openai",
            "base_url": "http://localhost:1234/v1",
            "model": "local-model",
            "api_key": "lm-studio",
            "api_key_env": "",
            "vision": False,
        },
        {
            "name": "Claude",
            "kind": "anthropic",
            "base_url": "",
            "model": "claude-opus-5-5",
            "api_key": "",
            "api_key_env": "ANTHROPIC_API_KEY",
            "vision": True,
        },
        {
            "name": "OpenAI uyumlu (özel)",
            "kind": "openai",
            "base_url": "https://api.openai.com/v1",
            "model": "",
            "api_key": "",
            "api_key_env": "OPENAI_API_KEY",
            "vision": True,
        },
    ],
}


def config_path():
    try:
        import FreeCAD

        base = FreeCAD.getUserAppDataDir()
    except Exception:
        base = os.path.join(os.path.expanduser("~"), ".cadai")
    return os.path.join(base, "CadAI", "config.json")


def load():
    path = config_path()
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (OSError, ValueError):
            pass
    return cfg


def write_private(path, text):
    """Write a file only the current user can read: config.json may hold API keys and bridge.json holds the token
    that controls FreeCAD. On Windows the per-user profile folder is already private; on POSIX use mode 0600."""
    if os.name == "nt":
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, 0o600)


def save(cfg):
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_private(path, json.dumps(cfg, indent=2, ensure_ascii=False))


def active_profile(cfg):
    for p in cfg["profiles"]:
        if p["name"] == cfg.get("active_profile"):
            return p
    return cfg["profiles"][0]


LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal")
SMALL_MAX_BILLION = 14.0


def model_size_billion(model):
    """Parameter count from names like 'qwen3:8b', 'Qwen3.5-9B-Instruct', 'gemma3:12b-it-q4' (None if not given).
    For mixture-of-experts names ('30b-a3b') the total counts: they handle tools like large models."""
    m = re.search(r"(?<![\w.])(\d+(?:\.\d+)?)\s*b(?![a-z])", (model or "").lower().replace("_", "-").replace(":", " "))
    return float(m.group(1)) if m else None


def is_small_model(profile):
    """Small-model mode (short prompt, ~15 tools, forgiving calls): 'on', 'off' or 'auto' (default). Auto: a local
    server (Ollama, LM Studio, llama.cpp) running a model of at most 14B parameters, or of unknown size."""
    value = profile.get("small_model", "auto")
    if isinstance(value, bool):
        return value
    value = str(value).strip().lower()
    if value in ("on", "true", "yes", "1"):
        return True
    if value in ("off", "false", "no", "0"):
        return False
    if profile.get("kind") == "anthropic":
        return False
    host = (urllib.parse.urlparse(profile.get("base_url") or "").hostname or "").lower()
    local = host in LOCAL_HOSTS or host.endswith(".local") or host.startswith(("192.168.", "10."))
    if not local:
        return False
    size = model_size_billion(profile.get("model"))
    return size is None or size <= SMALL_MAX_BILLION


def resolve_api_key(profile):
    if profile.get("api_key_env"):
        value = os.environ.get(profile["api_key_env"], "")
        if value:
            return value
    return profile.get("api_key", "")
