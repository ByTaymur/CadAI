"""Chat dock widget. The model call runs in a worker thread; tools always run on the GUI thread."""

import html
import json

import FreeCAD
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from .. import config
from ..agent import Agent
from ..providers import MissingPackageError, ProviderError, make_provider
from ..tools import ToolResult, build_registry

_panel = None


def toggle():
    global _panel
    if _panel is None:
        _panel = ChatPanel(Gui.getMainWindow())
        Gui.getMainWindow().addDockWidget(QtCore.Qt.RightDockWidgetArea, _panel)
        _panel.show()
    else:
        _panel.setVisible(not _panel.isVisible())


class ToolBridge(QtCore.QObject):
    """Called from the worker thread; blocks until the GUI thread has run the tool."""

    request = QtCore.Signal(object)

    def __init__(self, handler):
        super().__init__()
        self.handler = handler
        self.request.connect(self._handle, QtCore.Qt.BlockingQueuedConnection)

    def __call__(self, call):
        box = {"call": call}
        self.request.emit(box)
        return box.get("result") or ToolResult("Araç çalıştırılamadı.", is_error=True)

    @QtCore.Slot(object)
    def _handle(self, box):
        try:
            box["result"] = self.handler(box["call"])
        except Exception as e:  # never leave the worker waiting without a result
            box["result"] = ToolResult(f"{type(e).__name__}: {e}", is_error=True)


class AgentWorker(QtCore.QThread):
    event = QtCore.Signal(object)

    def __init__(self, agent, provider, text, images, mode, executor, max_steps):
        super().__init__()
        self.agent, self.provider, self.text, self.images = agent, provider, text, images
        self.mode, self.executor, self.max_steps = mode, executor, max_steps
        self.stop_requested = False

    def run(self):
        try:
            self.agent.run(self.provider, self.text, self.images, self.mode, self.executor, self.event.emit,
                           lambda: self.stop_requested, self.max_steps)
        except MissingPackageError as e:
            self.event.emit(("missing_package", e.package, str(e)))
        except ProviderError as e:
            self.event.emit(("error", str(e)))
        except Exception as e:
            self.event.emit(("error", f"{type(e).__name__}: {e}"))


class InstallWorker(QtCore.QThread):
    """pip install into CadAI's own package folder (cadai.pydeps) without freezing FreeCAD."""

    done = QtCore.Signal(bool, str)

    def __init__(self, package):
        super().__init__()
        self.package = package

    def run(self):
        from .. import pydeps

        ok, out = pydeps.install(self.package)
        self.done.emit(ok, out)


class ChatPanel(QtWidgets.QDockWidget):
    def __init__(self, parent=None):
        super().__init__("CadAI Asistan", parent)
        self.setObjectName("CadAIChatPanel")
        self.registry = build_registry()
        self.agent = Agent(self.registry)
        self.bridge = ToolBridge(self.execute_tool)
        self.worker = None
        self.cfg = config.load()
        self._build_ui()
        self._fill_profiles()

    # ---------- UI ----------
    def _build_ui(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)

        top = QtWidgets.QHBoxLayout()
        self.profile_box = QtWidgets.QComboBox()
        self.profile_box.currentTextChanged.connect(self._profile_changed)
        self.mode_box = QtWidgets.QComboBox()
        self.mode_box.addItem("Uygula (Act)", "act")
        self.mode_box.addItem("Planla (Plan)", "plan")
        settings_btn = QtWidgets.QToolButton()
        settings_btn.setText("⚙")
        settings_btn.setToolTip("Ayarlar")
        settings_btn.clicked.connect(self.open_settings)
        top.addWidget(self.profile_box, 1)
        top.addWidget(self.mode_box)
        top.addWidget(settings_btn)
        lay.addLayout(top)

        self.log = QtWidgets.QTextBrowser()
        self.log.setOpenExternalLinks(True)
        lay.addWidget(self.log, 1)

        opts = QtWidgets.QHBoxLayout()
        self.attach_sel = QtWidgets.QCheckBox("Seçimi ekle")
        self.attach_sel.setChecked(True)
        self.attach_sel.setToolTip("Seçili yüz/kenar bilgisi mesaja otomatik eklenir.")
        self.attach_view = QtWidgets.QCheckBox("Görüntü ekle")
        self.attach_view.setToolTip("3D görünümün ekran görüntüsü eklenir (görüntü okuyabilen modeller için).")
        opts.addWidget(self.attach_sel)
        opts.addWidget(self.attach_view)
        opts.addStretch(1)
        self.bridge_label = QtWidgets.QLabel()
        opts.addWidget(self.bridge_label)
        lay.addLayout(opts)
        self.update_bridge_status()

        self.input = QtWidgets.QPlainTextEdit()
        self.input.setPlaceholderText("Örn: Seçili yüze 500 N aşağı yönlü kuvvet uygula, karşı yüzü sabitle ve analiz et."
                                      "  (Gönder: Ctrl+Enter)")
        self.input.setMaximumHeight(90)
        lay.addWidget(self.input)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self.input, activated=self.send)

        btns = QtWidgets.QHBoxLayout()
        self.send_btn = QtWidgets.QPushButton("Gönder")
        self.send_btn.clicked.connect(self.send)
        self.stop_btn = QtWidgets.QPushButton("Durdur")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop)
        new_btn = QtWidgets.QPushButton("Yeni sohbet")
        new_btn.clicked.connect(self.new_chat)
        btns.addWidget(self.send_btn)
        btns.addWidget(self.stop_btn)
        btns.addWidget(new_btn)
        lay.addLayout(btns)
        self.setWidget(w)

    def update_bridge_status(self):
        from .. import bridge

        if bridge.is_running():
            self.bridge_label.setText(f'<span style="color:#0a5">● VS Code köprüsü :{bridge._bridge.port}</span>')
        else:
            self.bridge_label.setText('<span style="color:#999">○ VS Code köprüsü kapalı</span>')

    def _fill_profiles(self):
        self.profile_box.blockSignals(True)
        self.profile_box.clear()
        for p in self.cfg["profiles"]:
            self.profile_box.addItem(p["name"])
        self.profile_box.setCurrentText(config.active_profile(self.cfg)["name"])
        self.profile_box.blockSignals(False)

    def _profile_changed(self, name):
        self.cfg["active_profile"] = name
        config.save(self.cfg)

    def open_settings(self):
        from .settings import SettingsDialog

        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec():
            self.cfg = dlg.cfg
            config.save(self.cfg)
            self._fill_profiles()

    # ---------- chat log ----------
    def _append(self, who, text, color="#222"):
        body = html.escape(text).replace("\n", "<br>")
        self.log.append(f'<p style="margin:4px 0"><b style="color:{color}">{who}:</b> {body}</p>')

    def _on_event(self, ev):
        kind = ev[0]
        if kind == "assistant":
            self._append("CadAI", ev[1], "#0a5")
        elif kind == "tool_call":
            call = ev[1]
            args = json.dumps(call.args, ensure_ascii=False)
            self._append("→ araç", f"{call.name} {args[:300]}", "#777")
        elif kind == "tool_result":
            res = ev[2]
            preview = res.content[:400] + ("…" if len(res.content) > 400 else "")
            self._append("← hata" if res.is_error else "← sonuç", preview, "#c33" if res.is_error else "#777")
        elif kind == "error":
            self._append("Hata", ev[1], "#c00")
        elif kind == "missing_package":
            self._append("Hata", ev[2], "#c00")
            self._offer_install(ev[1])
        else:
            self._append("Bilgi", ev[1], "#55a")

    def _offer_install(self, package):
        from .. import pydeps

        if getattr(self, "_installer", None) and self._installer.isRunning():
            return
        cmd = " ".join(f'"{a}"' if " " in a else a for a in pydeps.install_command(package))
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("CadAI: paket kurulumu")
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setText(f"'{package}' paketi kurulsun mu?")
        box.setInformativeText("CadAI'nin kendi klasörüne kurulur; yönetici izni gerekmez ve FreeCAD'in kurulumuna "
                               "dokunulmaz. Kurulum internetten indirir (~1 dk).")
        box.setDetailedText(cmd)
        box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        box.button(QtWidgets.QMessageBox.Yes).setText("Kur")
        box.button(QtWidgets.QMessageBox.No).setText("Vazgeç")
        if box.exec() != QtWidgets.QMessageBox.Yes:
            return
        self._append("Bilgi", f"'{package}' kuruluyor… ({pydeps.lib_dir()})", "#55a")
        self.send_btn.setEnabled(False)
        self._installer = InstallWorker(package)
        self._installer.done.connect(lambda ok, out: self._installed(package, ok, out))
        self._installer.start()

    def _installed(self, package, ok, out):
        self.send_btn.setEnabled(True)
        if ok:
            self._append("Bilgi", f"'{package}' kuruldu. Mesajınızı yeniden gönderebilirsiniz.", "#0a5")
        else:
            self._append("Hata", f"'{package}' kurulamadı:\n{out}", "#c00")

    # ---------- actions ----------
    def send(self):
        text = self.input.toPlainText().strip()
        if not text or (self.worker and self.worker.isRunning()):
            return
        profile = config.active_profile(self.cfg)
        provider = make_provider(profile)
        provider.small = config.is_small_model(profile)  # short prompt + fewer tools for 7-14B local models
        images = []
        if self.attach_sel.isChecked():
            sel = self.registry.run("get_selection", {})
            if not sel.is_error and '"object"' in sel.content:
                text += "\n\n[Seçim]\n" + sel.content
        if self.attach_view.isChecked():
            if provider.vision:
                shot = self.registry.run("capture_view", {"view": "iso"})
                if shot.image_png_b64:
                    images.append(shot.image_png_b64)
            else:
                self._append("Bilgi", "Bu profil görüntü desteklemiyor; görüntü eklenmedi.", "#55a")
        self._append("Sen", self.input.toPlainText().strip(), "#025")
        self.input.clear()

        mode = self.mode_box.currentData()
        self.worker = AgentWorker(self.agent, provider, text, images, mode, self.bridge,
                                  int(self.cfg.get("max_steps", 25)))
        self.worker.event.connect(self._on_event)
        self.worker.finished.connect(self._on_finished)
        self.send_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.worker.start()

    def stop(self):
        if self.worker:
            self.worker.stop_requested = True
            self._append("Bilgi", "Durduruluyor (mevcut model isteği bitince)…", "#55a")

    def _on_finished(self):
        self.send_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def new_chat(self):
        if self.worker and self.worker.isRunning():
            return
        self.agent.reset()
        self.log.clear()

    # ---------- tool execution (GUI thread) ----------
    def execute_tool(self, call):
        call.name = self.registry.resolve_name(call.name) or call.name  # approval must see the real tool
        tool = self.registry.get(call.name)
        if tool is not None and tool.mutates and not self.cfg.get("auto_approve"):
            if not self._approve(call):
                return ToolResult("Kullanıcı bu değişikliği onaylamadı. Başka bir yol öner ya da kullanıcıya sor.",
                                  is_error=True)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            result = self.registry.run(call.name, call.args)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if tool is not None and tool.mutates and FreeCAD.ActiveDocument:
            Gui.updateGui()
        return result

    def _approve(self, call):
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("CadAI: değişiklik onayı")
        box.setIcon(QtWidgets.QMessageBox.Question)
        summary = {k: v for k, v in call.args.items() if k != "code"}
        box.setText(f"Asistan <b>{html.escape(call.name)}</b> çalıştırmak istiyor.<br>"
                    f"{html.escape(json.dumps(summary, ensure_ascii=False)[:500])}")
        if "code" in call.args:
            box.setDetailedText(call.args["code"])
        box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        box.button(QtWidgets.QMessageBox.Yes).setText("Onayla")
        box.button(QtWidgets.QMessageBox.No).setText("Reddet")
        return box.exec() == QtWidgets.QMessageBox.Yes
