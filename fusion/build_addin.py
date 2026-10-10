"""Build a standalone Fusion add-in ZIP using the same core shipped with FreeCAD."""

import json
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def main():
    version = json.loads((HERE / "CadAI" / "CadAI.manifest").read_text(encoding="utf-8"))["version"]
    out = HERE / "dist" / f"CadAI-fusion-{version}.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for source, prefix in ((HERE / "CadAI", "CadAI"),
                               (ROOT / "freecad" / "CadAI" / "cadai_core", "CadAI/cadai_core")):
            for file in source.rglob("*"):
                if file.is_file() and file.suffix in (".py", ".manifest") and "__pycache__" not in file.parts:
                    archive.write(file, prefix + "/" + file.relative_to(source).as_posix())
        archive.write(HERE / "README.md", "README.md")
        archive.write(ROOT / "LICENSE", "LICENSE")
    print(out)


if __name__ == "__main__":
    main()
