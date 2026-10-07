"""CadAI: AI assistant workbench for FreeCAD (modeling + FEM)."""

import os

__version__ = "0.16.2"

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.dirname(PACKAGE_DIR)


def icon_path(name="CadAI.svg"):
    return os.path.join(ADDON_DIR, "Resources", "icons", name)
