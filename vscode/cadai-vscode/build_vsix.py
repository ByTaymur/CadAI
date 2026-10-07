"""Package the extension as a .vsix without Node/vsce (standard library only).

    python build_vsix.py            -> dist/cadai-<version>.vsix
Install:  code --install-extension dist/cadai-<version>.vsix --force
"""

import json
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
INCLUDE = ["package.json", "extension.js", "bridge.js", "agents.js", "freecad.js", "mcp_setup.js", "gpu.js", "history_tree.js",
           "README.md", "LICENSE.txt", "media"]
# the FreeCAD add-on ships inside the extension and is installed into FreeCAD on first run
ADDON_SRC = os.path.join(HERE, "..", "..", "freecad", "CadAI")
ADDON_DST = "extension/freecad-addon/CadAI"

CONTENT_TYPES = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension=".json" ContentType="application/json"/><Default Extension=".js" ContentType="application/javascript"/>
<Default Extension=".svg" ContentType="image/svg+xml"/><Default Extension=".css" ContentType="text/css"/>
<Default Extension=".html" ContentType="text/html"/><Default Extension=".md" ContentType="text/markdown"/>
<Default Extension=".txt" ContentType="text/plain"/><Default Extension=".vsixmanifest" ContentType="text/xml"/>
<Default Extension=".py" ContentType="text/x-python"/><Default Extension=".xml" ContentType="text/xml"/>
<Default Extension=".png" ContentType="image/png"/>
<Override PartName="/extension/freecad-addon/CadAI/LICENSE" ContentType="text/plain"/>
</Types>"""

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011" xmlns:d="http://schemas.microsoft.com/developer/vsx-schema-design/2011">
  <Metadata>
    <Identity Language="en-US" Id="{name}" Version="{version}" Publisher="{publisher}" />
    <DisplayName>{display}</DisplayName>
    <Description xml:space="preserve">{description}</Description>
    <Tags></Tags>
    <Categories>Other,Visualization</Categories>
    <GalleryFlags>Public</GalleryFlags>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="{engine}" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionDependencies" Value="" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionPack" Value="" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="ui,workspace" />
      <Property Id="Microsoft.VisualStudio.Code.LocalizedLanguages" Value="" />
    </Properties>
    <License>extension/LICENSE.txt</License>
  </Metadata>
  <Installation><InstallationTarget Id="Microsoft.VisualStudio.Code"/></Installation>
  <Dependencies/>
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true" />
    <Asset Type="Microsoft.VisualStudio.Services.Content.Details" Path="extension/README.md" Addressable="true" />
    <Asset Type="Microsoft.VisualStudio.Services.Content.License" Path="extension/LICENSE.txt" Addressable="true" />
  </Assets>
</PackageManifest>"""


def xml_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main():
    with open(os.path.join(HERE, "package.json"), encoding="utf-8") as f:
        pkg = json.load(f)
    os.makedirs(os.path.join(HERE, "dist"), exist_ok=True)
    out = os.path.join(HERE, "dist", f"{pkg['name']}-{pkg['version']}.vsix")
    manifest = MANIFEST.format(name=pkg["name"], version=pkg["version"], publisher=pkg["publisher"],
                               display=xml_escape(pkg["displayName"]), description=xml_escape(pkg["description"]),
                               engine=pkg["engines"]["vscode"])
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("extension.vsixmanifest", manifest)
        for item in INCLUDE:
            src = os.path.join(HERE, item)
            if os.path.isdir(src):
                for root, _, files in os.walk(src):
                    for name in files:
                        full = os.path.join(root, name)
                        z.write(full, "extension/" + os.path.relpath(full, HERE).replace(os.sep, "/"))
            else:
                z.write(src, "extension/" + item)
        for root, dirs, files in os.walk(ADDON_SRC):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in files:
                full = os.path.join(root, name)
                z.write(full, ADDON_DST + "/" + os.path.relpath(full, ADDON_SRC).replace(os.sep, "/"))
    latest = os.path.join(HERE, "dist", "cadai-latest.vsix")
    with open(out, "rb") as src_f, open(latest, "wb") as dst_f:
        dst_f.write(src_f.read())
    print(out)
    # VS Code keeps the contributions (views, settings, commands) of an installed version in a cache: reinstalling
    # the same version runs the new JavaScript against the old manifest ("command ... not found")
    installed = os.path.join(os.path.expanduser("~"), ".vscode", "extensions", f"{pkg['publisher']}.{pkg['name']}-{pkg['version']}")
    if os.path.isdir(installed):
        print(f"UYARI: {pkg['version']} sürümü zaten kurulu. package.json'daki yeni görünüm/ayar/komutlar ancak sürüm "
              "artırılınca etkinleşir (package.json \"version\").")


if __name__ == "__main__":
    main()
