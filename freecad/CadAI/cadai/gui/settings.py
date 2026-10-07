"""Settings dialog: edit model profiles and agent options."""

import copy

from PySide import QtWidgets

from .. import config
from ..providers import ProviderError, model_timeout


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CadAI Ayarları")
        self.cfg = copy.deepcopy(cfg)
        self._current = None

        lay = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        self.list = QtWidgets.QListWidget()
        self.list.currentRowChanged.connect(self._select)
        row.addWidget(self.list, 1)

        form_box = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(form_box)
        self.name = QtWidgets.QLineEdit()
        self.kind = QtWidgets.QComboBox()
        self.kind.addItem("OpenAI uyumlu (Ollama, LM Studio, OpenRouter...)", "openai")
        self.kind.addItem("Anthropic (Claude)", "anthropic")
        self.base_url = QtWidgets.QLineEdit()
        self.model = QtWidgets.QLineEdit()
        self.api_key = QtWidgets.QLineEdit()
        self.api_key.setEchoMode(QtWidgets.QLineEdit.Password)
        self.api_key_env = QtWidgets.QLineEdit()
        self.api_key_env.setPlaceholderText("ör. ANTHROPIC_API_KEY (önerilen)")
        self.vision = QtWidgets.QCheckBox("Görüntü okuyabilir (vision)")
        self.small_model = QtWidgets.QComboBox()
        self.small_model.addItem("Otomatik (yerel ve ≤14B model → açık)", "auto")
        self.small_model.addItem("Açık: kısa istem, ~15 araç", "on")
        self.small_model.addItem("Kapalı: tüm araçlar", "off")
        self.small_model.setToolTip("Küçük yerel modeller (7-14B) çok sayıda araçta şaşırır. Bu mod kısa bir sistem "
                                    "istemi, kodsuz modelleme araçları ve yalnızca konuyla ilgili araçları gönderir.")
        self.num_ctx = QtWidgets.QSpinBox()
        self.num_ctx.setRange(2048, 262144)
        self.num_ctx.setSingleStep(4096)
        self.num_ctx.setSuffix(" token")
        self.num_ctx.setToolTip("Ollama bağlam penceresi. Ollama varsayılanı ekran kartı belleğine göre seçer, küçük "
                                "kartlarda 4096: bu, CadAI'nin istemi ve araçlarına yetmez ve istem sessizce kesilir.")
        self.timeout_minutes = QtWidgets.QSpinBox()
        self.timeout_minutes.setRange(1, 1440)
        self.timeout_minutes.setSuffix(" dakika")
        self.timeout_minutes.setToolTip("Her model isteğinde ağ bekleme süresi; varsayılan 120 dakika (2 saat). "
                                       "Token sınırında kesilen yanıtlar için bağlam/yanıt token sınırını artırın.")
        form.addRow("Profil adı", self.name)
        form.addRow("Tür", self.kind)
        form.addRow("Adres (base URL)", self.base_url)
        form.addRow("Model", self.model)
        form.addRow("API anahtarı", self.api_key)
        form.addRow("Anahtar ortam değişkeni", self.api_key_env)
        form.addRow("", self.vision)
        form.addRow("Küçük model modu", self.small_model)
        form.addRow("Bağlam (Ollama num_ctx)", self.num_ctx)
        form.addRow("Model zaman aşımı", self.timeout_minutes)
        row.addWidget(form_box, 2)
        lay.addLayout(row)

        pbtns = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("Profil ekle")
        add.clicked.connect(self._add)
        rem = QtWidgets.QPushButton("Profili sil")
        rem.clicked.connect(self._remove)
        pbtns.addWidget(add)
        pbtns.addWidget(rem)
        pbtns.addStretch(1)
        lay.addLayout(pbtns)

        opts = QtWidgets.QFormLayout()
        self.auto_approve = QtWidgets.QCheckBox("Değişiklikleri onay sormadan uygula (önerilmez)")
        self.auto_approve.setChecked(bool(self.cfg.get("auto_approve")))
        self.max_steps = QtWidgets.QSpinBox()
        self.max_steps.setRange(1, 200)
        self.max_steps.setValue(int(self.cfg.get("max_steps", 25)))
        opts.addRow("", self.auto_approve)
        opts.addRow("En fazla adım", self.max_steps)
        lay.addLayout(opts)

        note = QtWidgets.QLabel("Ollama'ya kendi API'siyle bağlanılır ve bağlam penceresi buradan ayarlanır. LM Studio'da "
                                "modeli en az 16K bağlamla yükleyin. Claude için API anahtarı (console.anthropic.com) "
                                "gerekir; 'anthropic' paketi eksikse panel ilk mesajda kurmayı önerir. Claude "
                                "aboneliğiyle (anahtarsız) çalışmak için VS Code'da Claude Code'u kullanın.")
        note.setWordWrap(True)
        lay.addWidget(note)

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        for p in self.cfg["profiles"]:
            self.list.addItem(p["name"])
        names = [p["name"] for p in self.cfg["profiles"]]
        active = self.cfg.get("active_profile")
        self.list.setCurrentRow(names.index(active) if active in names else 0)
        self.resize(720, 420)

    def _store(self):
        if self._current is None or self._current >= len(self.cfg["profiles"]):
            return
        p = self.cfg["profiles"][self._current]
        old_name = p["name"]
        p.update(name=self.name.text().strip() or old_name, kind=self.kind.currentData(),
                 base_url=self.base_url.text().strip(), model=self.model.text().strip(),
                 api_key=self.api_key.text(), api_key_env=self.api_key_env.text().strip(),
                 vision=self.vision.isChecked(), small_model=self.small_model.currentData(),
                 num_ctx=self.num_ctx.value(), timeout_seconds=self.timeout_minutes.value() * 60)
        if self.cfg.get("active_profile") == old_name:
            self.cfg["active_profile"] = p["name"]
        self.list.item(self._current).setText(p["name"])

    def _select(self, row):
        self._store()
        self._current = row
        if row < 0:
            return
        p = self.cfg["profiles"][row]
        self.name.setText(p["name"])
        self.kind.setCurrentIndex(max(0, self.kind.findData(p["kind"])))
        self.base_url.setText(p.get("base_url", ""))
        self.model.setText(p.get("model", ""))
        self.api_key.setText(p.get("api_key", ""))
        self.api_key_env.setText(p.get("api_key_env", ""))
        self.vision.setChecked(bool(p.get("vision")))
        self.small_model.setCurrentIndex(max(0, self.small_model.findData(str(p.get("small_model", "auto")))))
        self.num_ctx.setValue(int(p.get("num_ctx") or 16384))
        try:
            timeout = model_timeout(p)
        except ProviderError:
            timeout = config.DEFAULT_MODEL_TIMEOUT_SECONDS
        self.timeout_minutes.setValue(int((timeout + 59) // 60))

    def _add(self):
        self._store()
        self.cfg["profiles"].append({"name": f"Yeni profil {len(self.cfg['profiles']) + 1}", "kind": "openai",
                                     "base_url": "http://localhost:11434/v1", "model": "", "api_key": "",
                                     "api_key_env": "", "vision": False,
                                     "small_model": "auto", "num_ctx": 16384})
        self.list.addItem(self.cfg["profiles"][-1]["name"])
        self.list.setCurrentRow(len(self.cfg["profiles"]) - 1)

    def _remove(self):
        row = self.list.currentRow()
        if row < 0 or len(self.cfg["profiles"]) <= 1:
            return
        self._current = None
        removed = self.cfg["profiles"].pop(row)
        self.list.takeItem(row)
        if self.cfg.get("active_profile") == removed["name"]:
            self.cfg["active_profile"] = self.cfg["profiles"][0]["name"]

    def accept(self):
        self._store()
        self.cfg["auto_approve"] = self.auto_approve.isChecked()
        self.cfg["max_steps"] = self.max_steps.value()
        super().accept()
