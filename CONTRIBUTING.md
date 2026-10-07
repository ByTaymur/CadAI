# Contributing to CadAI

Thanks for helping! Issues, ideas and pull requests are welcome.

## Good first contributions
- English UI strings (the UI is currently Turkish) via VS Code's l10n
- Testing on **macOS / Linux** (FreeCAD detection, add-on install, MCP setup) and reporting what breaks
- More DFM rules (with a cited source and a test on a real part), more FreeCAD recipes (each one runs in the tests)
- Assembly motion / animation in the 3D view
- Example models and demo GIFs for the README

## Before a pull request
1. Run the FreeCAD tests: `freecadcmd freecad/CadAI/tests/run_tests.py` — all must pass. MCP server changes:
   `python -m unittest freecad/CadAI/tests/test_mcp_server.py`. Lint: `ruff check freecad vscode/cadai-vscode/test`.
2. For extension changes (in `vscode/cadai-vscode`): `npm ci`, `npm test`, `npm run test:viewer`, then build the
   `.vsix` with `build_vsix.py`. After upgrading three.js or three-mesh-bvh run `npm run vendor` and commit the bundle.
3. Keep FreeCAD access on the GUI thread (the bridge does this); never assume a fixed install path.
4. Do not commit secrets or personal files (see `.gitignore` and [SECURITY.md](SECURITY.md)).
5. `AGENTS.md` describes how AI agents should work in this repo — keep it up to date when tools change.

## Reporting bugs
Include your OS, FreeCAD version, VS Code version, which AI agent you use, and the CadAI output channel log
(VS Code → Output → "CadAI"). Security problems: please use private vulnerability reporting, not public issues.
