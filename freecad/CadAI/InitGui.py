import FreeCADGui as Gui


class CadAIWorkbench(Gui.Workbench):
    # FreeCAD executes InitGui.py with separate globals/locals: module-level imports are not visible
    # inside the class body, so import here.
    import cadai as _cadai

    MenuText = "CadAI"
    ToolTip = "Yapay zekâ destekli modelleme ve FEM analizi"
    Icon = _cadai.icon_path()
    del _cadai

    def Initialize(self):
        from cadai.gui import commands

        commands.register()
        self.appendToolbar("CadAI", commands.NAMES)
        self.appendMenu("CadAI", commands.NAMES)
        self.appendMenu(["CadAI", "Geliştirici"], commands.DEV_NAMES)

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(CadAIWorkbench())

# Start the VS Code/MCP bridge with FreeCAD (can be turned off in config: bridge_autostart).
try:
    import cadai.bridge

    cadai.bridge.autostart()
except Exception as e:
    import FreeCAD

    FreeCAD.Console.PrintError(f"CadAI köprüsü başlatılamadı: {e}\n")
