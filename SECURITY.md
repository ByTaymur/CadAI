# Security

CadAI lets an AI agent drive a live FreeCAD session. That is powerful, so this file explains what is protected,
what is not, and how to report a problem.

## Reporting a vulnerability
Please **do not open a public issue** for security problems. Use GitHub's *private vulnerability reporting*
(Security tab → "Report a vulnerability") on this repository. We aim to answer within 7 days.

## How CadAI is protected

| Area | Protection |
|---|---|
| **Local bridge** (FreeCAD ↔ VS Code / MCP) | Listens on `127.0.0.1` only. Every request needs a random per-session token (constant-time compare). Requests with a foreign `Host` header are refused (DNS-rebinding guard). Request bodies are size-limited. The port is bound exclusively (no second process can share it on Windows). |
| **Token & settings files** | `bridge.json` (token), `config.json` (may hold API keys) and `marker.key` live in the FreeCAD *user* folder, never in the project. On Linux/macOS they are created with mode `0600`; on Windows the per-user profile folder is private. |
| **API keys** | Not required for local models. For cloud models prefer an environment variable (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`); the key is read at call time and never written to the repository. |
| **Markers from other people's files** | Markers are stored inside `.FCStd` files, so a downloaded file could carry notes written to steer an AI ("prompt injection"). Markers created on your machine are signed (HMAC, local key). Unsigned or modified markers are reported to the AI as `"trusted": false` with a warning to ask the user before acting, and shown with ⚠ in the sidebar. |
| **VS Code webviews** | Strict Content-Security-Policy (no inline scripts). Text from model files is HTML-escaped. Webviews can only trigger an allow-list of CadAI commands. |
| **Executable paths** | `cadai.freecadPath` and `cadai.addonPath` are *machine-scoped* settings: a cloned workspace's `.vscode/settings.json` cannot point them at another program. |
| **Python packages for the FreeCAD panel** | Only the Anthropic SDK (`anthropic`, for Claude in the panel), installed from PyPI with FreeCAD's own `pip` into CadAI's folder in the FreeCAD user directory (`CadAI/pylib`), only after you confirm the dialog that shows the command. Nothing is written into FreeCAD's installation; that folder is appended to `sys.path` *after* FreeCAD's own packages, so it cannot replace them. Delete the folder to undo it. |
| **Standard parts (step.parts)** | The only network access besides your AI provider. HTTPS to an allow-list of hosts (`api.step.parts`, GitHub media) — also checked on every redirect —, a size limit, and the SHA-256 published by the catalog is verified before a STEP file is opened. Verified files are cached in the FreeCAD user folder. Part ids are validated before they reach a URL. |
| **MCP tool hints** | Every tool tells the agent whether it is read-only, destructive or reaches the internet (`readOnlyHint`, `destructiveHint`, `openWorldHint`). Clients may auto-approve read-only tools; anything that changes the model still asks. Prompt templates never embed marker notes (those come from files and may be untrusted); the agent reads them through `get_markers`, which flags untrusted ones. |
| **Design history (optional, off by default)** | Writes only into its own history folder (next to the model, a machine-level folder setting, or `<workspace>/cadai-history` when a workspace opts in); a workspace cannot point it elsewhere. Uses `Document.saveCopy`, so your model file is never modified. Commits locally with your git identity (or a neutral one), runs your repository's hooks, commits only its own folder, and **never pushes**. Snapshots contain the model, so treat the history like the model itself. |
| **GPU diagnostics** | Read-only system queries (WMI/`lspci`/`system_profiler`). The only change it can make is on Windows, after you click it: the per-app "High performance" GPU preference for FreeCAD and VS Code (the same registry value Windows' Graphics settings page writes), and FreeCAD's VBO/software-OpenGL view preferences. |
| **Other programs (Blender, OpenSCAD, KiCad, build123d/CadQuery Python)** | Run as separate processes without a shell, without a window and with a timeout; FreeCAD only exchanges files with them in a temporary folder. Their paths come from *machine-scoped* settings (`cadai.external.*`), environment variables or the usual install folders — never from a tool argument, so the AI cannot choose which program runs. Install commands are fixed in the extension and run only after you confirm them, visibly in a VS Code task. |
| **Model changes** | Every change runs in one undo transaction and is rolled back on error. The FreeCAD chat panel asks before every change; VS Code agents use their own approval (read-only tools may be auto-approved, changing tools are not). |

## What is *not* protected — know this before use
- **`run_python` runs arbitrary Python inside FreeCAD** with your user's rights. That is how the AI builds geometry.
  Only approve it when you understand what it does, and be careful when working on files you did not create.
- **`code_cad` runs build123d/CadQuery code (and OpenSCAD scripts)** in a separate process with your user's rights;
  a `.py` file is a program. Treat it like `run_python`: approve it only for code you trust.
- Any program running as **your user** can read the bridge token and control FreeCAD (same as it could run FreeCAD
  itself). CadAI does not defend against malware already on your machine.
- AI agents can make mistakes. Analysis (FEM) results are not a substitute for engineering review.

## Never commit
API keys, `.env` files, `bridge.json`, `config.json`, `marker.key`, personal notes. `.gitignore` covers these.
