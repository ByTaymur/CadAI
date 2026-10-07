"""FreeCAD commands (toolbar/menu entries)."""

import os
import sys

import FreeCAD
import FreeCADGui as Gui

from .. import icon_path

DEBUG_PORT = 5678


class ShowPanelCommand:
    def GetResources(self):
        return {"Pixmap": icon_path(), "MenuText": "Asistan paneli",
                "ToolTip": "CadAI sohbet panelini aç/kapat", "Accel": "Ctrl+Shift+A"}

    def Activated(self):
        from . import panel

        panel.toggle()

    def IsActive(self):
        return True


class SettingsCommand:
    def GetResources(self):
        return {"Pixmap": "preferences-system", "MenuText": "Ayarlar",
                "ToolTip": "Model profilleri ve CadAI seçenekleri"}

    def Activated(self):
        from .. import config
        from . import panel
        from .settings import SettingsDialog

        dlg = SettingsDialog(config.load(), Gui.getMainWindow())
        if dlg.exec():
            config.save(dlg.cfg)
            if panel._panel is not None:
                panel._panel.cfg = dlg.cfg
                panel._panel._fill_profiles()

    def IsActive(self):
        return True


class BridgeCommand:
    """Start/stop the local bridge used by VS Code (cadai_mcp.py)."""

    def GetResources(self):
        return {"Pixmap": "edit-redo", "MenuText": "VS Code köprüsü (aç/kapat)",
                "ToolTip": "VS Code'daki yapay zekâ ajanlarının bu FreeCAD oturumunu kullanmasını sağlar"}

    def Activated(self):
        from .. import bridge
        from . import panel

        if bridge.is_running():
            bridge.stop_gui_bridge()
            FreeCAD.Console.PrintMessage("CadAI köprüsü kapatıldı.\n")
        else:
            bridge.start_gui_bridge()
        if panel._panel is not None:
            panel._panel.update_bridge_status()

    def IsActive(self):
        return True


class ReloadCommand:
    """Reload the cadai package without restarting FreeCAD (for development)."""

    def GetResources(self):
        return {"Pixmap": "view-refresh", "MenuText": "Eklentiyi yeniden yükle (geliştirici)",
                "ToolTip": "VS Code'da değiştirilen CadAI kodunu FreeCAD'i kapatmadan yükler"}

    def Activated(self):
        from .. import bridge
        from . import panel

        was_running = bridge.is_running()
        had_panel = panel._panel is not None and panel._panel.isVisible()
        bridge.stop_gui_bridge()
        if panel._panel is not None:
            Gui.getMainWindow().removeDockWidget(panel._panel)
            panel._panel.deleteLater()
            panel._panel = None
        for name in [m for m in sys.modules if m == "cadai" or m.startswith("cadai.")]:
            del sys.modules[name]
        import cadai.bridge
        import cadai.gui.panel as new_panel

        if was_running:
            cadai.bridge.start_gui_bridge()
        if had_panel:
            new_panel.toggle()
        FreeCAD.Console.PrintMessage("CadAI yeniden yüklendi.\n")

    def IsActive(self):
        return True


class DebugCommand:
    """Let VS Code attach its Python debugger (debugpy) to this FreeCAD process."""

    def GetResources(self):
        return {"Pixmap": "applications-python", "MenuText": "VS Code hata ayıklayıcısını bekle (geliştirici)",
                "ToolTip": f"debugpy'yi 127.0.0.1:{DEBUG_PORT} üzerinde başlatır; VS Code'da 'FreeCAD'e bağlan' çalıştırın"}

    def Activated(self):
        from PySide import QtWidgets

        try:
            import debugpy
        except ImportError:
            QtWidgets.QMessageBox.information(
                Gui.getMainWindow(), "CadAI", "debugpy kurulu değil. Komut satırında çalıştırın:\n\n"
                '"C:\\Program Files\\FreeCAD 1.1\\bin\\python.exe" -m pip install --user debugpy')
            return
        python = os.path.join(os.path.dirname(sys.executable), "python.exe" if os.name == "nt" else "python")
        debugpy.configure(python=python)
        debugpy.listen(("127.0.0.1", DEBUG_PORT))
        FreeCAD.Console.PrintMessage(f"debugpy 127.0.0.1:{DEBUG_PORT} üzerinde bekliyor.\n")

    def IsActive(self):
        return True


NAMES = ["CadAI_Panel", "CadAI_Settings", "CadAI_Bridge"]
DEV_NAMES = ["CadAI_Reload", "CadAI_Debug"]


def register():
    Gui.addCommand("CadAI_Panel", ShowPanelCommand())
    Gui.addCommand("CadAI_Settings", SettingsCommand())
    Gui.addCommand("CadAI_Bridge", BridgeCommand())
    Gui.addCommand("CadAI_Reload", ReloadCommand())
    Gui.addCommand("CadAI_Debug", DebugCommand())
