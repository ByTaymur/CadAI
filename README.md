# CadAI — point at your part, describe the change, let AI do it

**CadAI** brings FreeCAD into VS Code and lets any AI coding agent edit real CAD models. Instead of describing
geometry in words, you **show** it: put numbered markers, dimensions, lines, circles or freehand strokes directly on
the 3D model, write what you want there ("make this hole Ø10", "this distance should be 40 mm"), and send it to the
agent you already use — **Claude Code, Codex, Cline, Kilo Code or GitHub Copilot**.

> 🇹🇷 Türkçe özet aşağıda.

## Why
- **Show, don't describe.** AI agents are much better at *editing* existing CAD than generating it from scratch
  (see [CADGenBench](https://github.com/huggingface/cadgenbench)). CadAI gives them exact coordinates and the
  face/edge/vertex under every marker.
- **No FreeCAD UI needed.** FreeCAD runs minimized as the geometry engine; you work in VS Code.
- **Any agent.** Everything goes through one MCP server (`cadai-freecad`), so the same tools work in every agent.
- **Engineering, not just shapes.** Parametric edits, measurements, FEM analysis (Gmsh + CalculiX) with
  hand-calculation, force-balance and mesh-convergence checks, and design-for-manufacturing checks with measured
  evidence.

## Features
- **Persistent design requirements**: save requested sizes, volume, solid count, clearances and dimensional
  relationships inside the FCStd document. AI/MCP mutations automatically return measured pass/fail evidence;
  missing or invalid geometry cannot pass. See [examples and limits](docs/design-requirements.md).
- **Parametric mounting plates and dimension links**: native expressions preserve four-hole edge offsets after
  resizing; a requirements panel shows measured failures, edits targets and exports JSON evidence. Bore count,
  diameter, bounding-box edge offsets and interference volume are checked on real geometry. The internal agent
  verifies before its final answer and attempts up to two repair rounds.
- Live 3D view of the open FreeCAD document; click a face to select it (selection is shared with the AI)
- Annotation tools on the model: 📍 marker, 📏 dimension, ╱ line, ◯ circle (on the face plane), ✎ freehand pen —
  snapping to vertex › edge › face; markers are saved inside the `.FCStd` file
- Model tree with dimension editing, show/hide, delete; open/save FCStd, STEP, STL
- FEM wizard: pick fixed faces and load faces, run static or modal analysis in the background; results as a
  **von Mises / displacement color map on the deformed part** right in the 3D view (legend, 99th-percentile clamp,
  value under the cursor), plus an automatic **force-balance check** (support reactions vs. applied loads)
- **DFM checks** measured on the exact B-rep — FDM 3D printing (build volume, overhangs, bridging holes, thin walls),
  3-axis CNC (sharp internal corners, small radii, deep holes, second setups, undercuts), injection molding (draft,
  undercuts, wall thickness/uniformity), sheet metal (thickness, bend radius, hole size); findings are painted on the
  model
- **Technical drawings** (A3 PDF + PNG, Turkish title block): first-angle views with exact hidden lines, automatic
  section A-A, the largest standard scale that fits, overall sizes, grouped hole diameters (`4x Ø4,20`), center lines
  and the mass from the material; the AI adds dimensions and notes measured from the model, never made up, and a
  layout check reports overlapping texts so it can fix them
- **Render**: photorealistic pictures with **Blender Cycles** on your GPU (OptiX/CUDA/HIP/oneAPI/Metal) — studio light,
  reflections, soft floor shadow, materials from the FreeCAD material or per part (aluminium, anodized, steel, brass,
  plastic + colour…), and orbiting **turntable GIFs**; without Blender a built-in renderer still gives a shaded picture
- **Other open-source CAD programs, driven from VS Code** (they run in the background, no windows): **OpenSCAD**
  code becomes exact FreeCAD solids (cylinders stay cylinders), **build123d / CadQuery** scripts come in as B-rep via
  STEP, the source stays in the part so it can be regenerated with new parameters; **KiCad** boards come in with their
  components, mounting holes and connector positions for enclosure design. The sidebar shows what is installed and
  installs the rest (winget / brew / apt) after you confirm the command
- **16 000+ standard parts** from [step.parts](https://www.step.parts) (ISO/DIN screws, nuts, bearings, extrusions,
  motors…), downloaded with SHA-256 verification and dropped on the selected face
- Section view, BVH-accelerated picking and one draw call per part (three.js r186 + three-mesh-bvh); the view only
  redraws when something changes, and FreeCAD sends **only changed parts** in compact binary form (a one-part edit on
  a 400-hole plate: ~7 ms and 15 kB instead of ~750 ms and 8.8 MB)
- **GPU diagnostics for any brand** (AMD, NVIDIA, Intel, Apple, Qualcomm; Windows/Linux/macOS): finds a graphics card
  that sits idle because the monitor is plugged into the motherboard, apps running on the integrated GPU of a laptop,
  software rendering, old drivers and slow FreeCAD view settings — and fixes what can be fixed in software
- **Optional automatic design history**: every change (AI, VS Code or manual in FreeCAD) becomes a git commit with a
  readable message (`Beam.Length 100 mm → 120 mm`), a diff-friendly `model.json` and Markdown notes that open as an
  **Obsidian** vault; go back to any version as a new document. Off by default; can commit into a project repo (only
  its own folder) for developers
- AI tools (MCP): inspect, find faces/edges, measure, screenshot, parametric edit, Python modeling with **tested
  FreeCAD recipes**, FEM (+ convergence study), DFM, standard parts, technical drawings, export
  STEP/IGES/BREP/GLB/STL/3MF
- MCP server speaks the current spec (2026-07-28, stateless) and older clients; tools carry read-only/destructive
  hints so agents can auto-approve safe ones; prompts appear as slash commands (e.g. `/mcp__cadai-freecad__apply_markers`)
- VS Code registers the server natively (Copilot agent mode sees it without editing `mcp.json`)
- FreeCAD side panel with its own agent for local (Ollama, LM Studio) or cloud models

## Install
1. Install **FreeCAD 1.0+** ([download](https://www.freecad.org/downloads.php)).
2. Install the **CadAI** VS Code extension (Marketplace / Open VSX, or the `.vsix` from Releases).
3. In VS Code open the **CadAI** sidebar → **Start FreeCAD**. The extension installs its FreeCAD add-on
   automatically and offers **Connect AI agents**, which registers the `cadai-freecad` MCP server with the agents
   you have installed.

Tested on Windows 11/10 with FreeCAD 1.1. macOS and Linux support is implemented but **experimental** — reports welcome.

The 3D viewer supports any GPU vendor with a working **WebGL 2** driver (discrete, integrated, virtual or software rendering).
It retries rejected context settings, limits resolution to the GPU's drawing-buffer capabilities and restores the view
after context loss. GPU names may be hidden by the browser; this does not block the viewer. WebGL 1-only hardware
cannot use the current three.js renderer; the panel explains this and offers diagnostics
([three.js requirements](https://threejs.org/docs/pages/WebGLRenderer.html)).

## How it fits together
```
VS Code ─ CadAI extension (3D view, markers, tree, FEM wizard)
   │                                  │
   │  AI agent (Claude Code, Codex,   │ local HTTP bridge (127.0.0.1 + token)
   │  Cline, Kilo, Copilot)           │
   └── MCP: cadai-freecad ────────────┴──► FreeCAD + CadAI add-on (tools run on the live document)
```

## Repository layout
| Path | What |
|---|---|
| `freecad/CadAI/` | FreeCAD add-on (Python): tools, bridge, MCP server, tests |
| `vscode/cadai-vscode/` | VS Code extension (plain JavaScript, three.js viewer) |
| `AGENTS.md` | Shared rules for AI agents working on this repo or on models |
| `PLAN.md` | Design notes and history (Turkish) |
| `SECURITY.md` | Security model and how to report issues |

## Development
- FreeCAD add-on tests (real FreeCAD + Gmsh + CalculiX, scripted fake LLM):
  `freecadcmd freecad/CadAI/tests/run_tests.py`
- MCP server tests (any Python 3.9+, no FreeCAD): `python -m unittest freecad/CadAI/tests/test_mcp_server.py`
- Extension tests (Node 20+, in `vscode/cadai-vscode`): `npm ci && npm test && npm run test:viewer`
  (the viewer test drives the real webview in headless Edge/Chrome with fixtures produced by a real FreeCAD run)
- Lint: `ruff check freecad vscode/cadai-vscode/test`
- Build the extension (no Node needed): `python vscode/cadai-vscode/build_vsix.py` →
  `code --install-extension vscode/cadai-vscode/dist/cadai-latest.vsix`
- CI (`.github/workflows/ci.yml`) runs all of the above, including the FreeCAD suite on the official portable build.
- Developer setup: link `freecad/CadAI` into FreeCAD's `Mod` folder and set `cadai.addonPath` to it; the
  extension then leaves your link alone and uses the source directly.

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## Security
`run_python` lets the AI run Python inside FreeCAD. Approve changes consciously, especially on files you did not
create; markers that arrive inside someone else's file are flagged as untrusted. Details: [SECURITY.md](SECURITY.md).

## License
[LGPL-2.1-or-later](LICENSE), the same as FreeCAD. Bundles three.js and three-mesh-bvh (both MIT). Parts inserted
from step.parts keep their own licenses (see each part's page, stored on the object as `StepPartsUrl`).

---

## 🇹🇷 Türkçe özet
CadAI, FreeCAD'i VS Code'a taşır ve modeli **göstererek tarif etmenizi** sağlar: 3B görünümde parçanın üzerine
nokta, ölçü, çizgi, daire ya da kalemle işaret koyup ne istediğinizi yazarsınız; kullandığınız yapay zekâ ajanı
(Claude Code, Codex, Cline, Kilo, Copilot) işaretleri okuyup değişikliği yapar. FEM analizi (3B renk haritası,
kuvvet dengesi ve mesh yakınsaması denetimiyle), ölçüme dayalı üretilebilirlik (DFM) denetimi, 16 000+ standart
parça (step.parts), Türkçe antetli A3 teknik resim (PDF), ölçüm ve parametrik düzenleme de dahildir. Blender ile
ekran kartında gerçekçi render ve dönen animasyon; OpenSCAD, build123d/CadQuery kodu ve KiCad kartları da arka planda
çalışan programlarla doğrudan modele gelir (kenar çubuğu → "Render ve diğer programlar"). Kurulum: FreeCAD 1.0+ ve
VS Code'da CadAI eklentisi; gerisini eklenti kendisi kurar.
Arayüz şu an Türkçedir; İngilizce arayüz yol haritasındadır.


Model request timeouts default to 2 hours for all providers, including existing profiles. In the FreeCAD CadAI settings, **Model zaman a??m?** accepts 1?1440 minutes per profile (`timeout_seconds` in config.json). This controls network waiting, not a total conversation deadline. A token-length warning requires increasing the server output/context limit or starting a new conversation; a longer timeout alone does not remove token limits.
