"""Settings dialog. Dark, minimal, tuned to match the overlay language."""

from __future__ import annotations

import sys
import threading

from PyQt6.QtCore import QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import autostart
import theme
import whispercpp
from audio import list_input_devices
from config import (
    config,
    AI_PROVIDERS,
    PROVIDER_GROQ,
    PROVIDER_LOCAL_LLM,
    PROVIDER_OPENAI,
    PROVIDER_SPEC,
    TRANSCRIPTION_PROVIDERS,
)
from hotkeys import HotkeyManager


LANGUAGES = [
    ("en", "English"),
    ("fr", "French"),
    ("de", "German"),
    ("es", "Spanish"),
    ("it", "Italian"),
    ("pt", "Portuguese"),
    ("ja", "Japanese"),
    ("zh", "Chinese"),
    ("auto", "Auto-detect"),
]


SETTINGS_QSS = """
QDialog {
    background-color: #0f1012;
    color: #e6e6ea;
}
QLabel {
    color: #e6e6ea;
    font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Inter', 'SF Pro Text', sans-serif;
    font-size: 13px;
}
QLabel#heading {
    font-size: 20px;
    font-weight: 600;
    letter-spacing: 0.2px;
    padding-bottom: 2px;
}
QLabel#subheading {
    color: #8a8a93;
    font-size: 12px;
    padding-bottom: 8px;
}
QLabel#footnote {
    color: #6b6b73;
    font-size: 11px;
}
QLabel#section {
    font-size: 14px;
    font-weight: 600;
    padding-top: 6px;
}
QProgressBar {
    background-color: #17181b;
    color: #e6e6ea;
    border: 1px solid #2a2b30;
    border-radius: 6px;
    min-height: 18px;
    text-align: center;
    font-size: 11px;
}
QProgressBar::chunk {
    background-color: #0a84ff;
    border-radius: 5px;
}
QLineEdit, QPlainTextEdit, QComboBox {
    background-color: #17181b;
    color: #f1f1f4;
    border: 1px solid #2a2b30;
    border-radius: 8px;
    padding: 7px 10px;
    selection-background-color: #0a84ff;
    font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Inter', sans-serif;
    font-size: 13px;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
    border: 1px solid #0a84ff;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background-color: #17181b;
    color: #f1f1f4;
    selection-background-color: #0a84ff;
    border: 1px solid #2a2b30;
    outline: none;
}
QCheckBox {
    color: #e6e6ea;
    spacing: 8px;
    font-size: 13px;
}
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid #3a3b40;
    border-radius: 4px;
    background: #17181b;
}
QCheckBox::indicator:checked {
    background: #0a84ff;
    border: 1px solid #0a84ff;
    image: none;
}
QPushButton {
    background-color: #1d1e22;
    color: #e6e6ea;
    border: 1px solid #2a2b30;
    border-radius: 8px;
    padding: 7px 14px;
    font-size: 13px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #26272c;
}
QPushButton#primary {
    background-color: #0a84ff;
    border: 1px solid #0a84ff;
    color: white;
}
QPushButton#primary:hover {
    background-color: #0b90ff;
}
QPushButton:disabled {
    color: #6b6b73;
    background-color: #151619;
    border: 1px solid #1f2024;
}
QFrame#divider {
    background-color: #202126;
    max-height: 1px;
    min-height: 1px;
    border: none;
}
"""


SETTINGS_QSS_LIGHT = """
QDialog {
    background-color: #f5f5f7;
    color: #1d1d1f;
}
QLabel {
    color: #1d1d1f;
    font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Inter', 'SF Pro Text', sans-serif;
    font-size: 13px;
}
QLabel#heading {
    font-size: 20px;
    font-weight: 600;
    letter-spacing: 0.2px;
    padding-bottom: 2px;
}
QLabel#subheading {
    color: #6e6e73;
    font-size: 12px;
    padding-bottom: 8px;
}
QLabel#footnote {
    color: #86868b;
    font-size: 11px;
}
QLabel#section {
    font-size: 14px;
    font-weight: 600;
    padding-top: 6px;
}
QProgressBar {
    background-color: #ffffff;
    color: #1d1d1f;
    border: 1px solid #d2d2d7;
    border-radius: 6px;
    min-height: 18px;
    text-align: center;
    font-size: 11px;
}
QProgressBar::chunk {
    background-color: #007aff;
    border-radius: 5px;
}
QLineEdit, QPlainTextEdit, QComboBox {
    background-color: #ffffff;
    color: #1d1d1f;
    border: 1px solid #d2d2d7;
    border-radius: 8px;
    padding: 7px 10px;
    selection-background-color: #007aff;
    selection-color: #ffffff;
    font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Inter', sans-serif;
    font-size: 13px;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
    border: 1px solid #007aff;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background-color: #ffffff;
    color: #1d1d1f;
    selection-background-color: #007aff;
    selection-color: #ffffff;
    border: 1px solid #d2d2d7;
    outline: none;
}
QCheckBox {
    color: #1d1d1f;
    spacing: 8px;
    font-size: 13px;
}
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid #c7c7cc;
    border-radius: 4px;
    background: #ffffff;
}
QCheckBox::indicator:checked {
    background: #007aff;
    border: 1px solid #007aff;
    image: none;
}
QPushButton {
    background-color: #ffffff;
    color: #1d1d1f;
    border: 1px solid #d2d2d7;
    border-radius: 8px;
    padding: 7px 14px;
    font-size: 13px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #ececf0;
}
QPushButton#primary {
    background-color: #007aff;
    border: 1px solid #007aff;
    color: white;
}
QPushButton#primary:hover {
    background-color: #0a84ff;
}
QPushButton:disabled {
    color: #a1a1a6;
    background-color: #f0f0f2;
    border: 1px solid #e5e5ea;
}
QFrame#divider {
    background-color: #e5e5ea;
    max-height: 1px;
    min-height: 1px;
    border: none;
}
"""

SETTINGS_QSS_BY_SCHEME = {
    theme.SCHEME_DARK: SETTINGS_QSS,
    theme.SCHEME_LIGHT: SETTINGS_QSS_LIGHT,
}


def _check_icon() -> QIcon:
    """Green check mark marking downloaded whisper.cpp models."""
    pm = QPixmap(16, 16)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor("#30d158"), 2.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawPolyline([QPointF(3, 8.5), QPointF(6.5, 12), QPointF(13, 4.5)])
    p.end()
    return QIcon(pm)


def _blank_icon() -> QIcon:
    """Same size as the check icon so model names stay aligned."""
    pm = QPixmap(16, 16)
    pm.fill(Qt.GlobalColor.transparent)
    return QIcon(pm)


class HotkeyCapture(QWidget):
    """Click-to-capture hotkey input."""

    captured = pyqtSignal(str)

    def __init__(self, initial: str, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._field = QLineEdit(initial, self)
        self._field.setReadOnly(True)
        self._btn = QPushButton("Capture", self)
        self._btn.clicked.connect(self._start_capture)
        layout.addWidget(self._field, stretch=1)
        layout.addWidget(self._btn)
        self._thread: threading.Thread | None = None

    def value(self) -> str:
        return self._field.text().strip()

    def set_value(self, v: str):
        self._field.setText(v)

    def _start_capture(self):
        if self._thread and self._thread.is_alive():
            return
        self._btn.setEnabled(False)
        self._field.setText("Press keys…")
        self._thread = threading.Thread(target=self._capture_worker, daemon=True)
        self._thread.start()

    def _capture_worker(self):
        try:
            combo = HotkeyManager.capture_hotkey()
        except Exception:
            combo = ""
        self.captured.emit(combo or self._field.text())


class SettingsDialog(QDialog):
    saved = pyqtSignal()
    # (bytes_done, bytes_total) and (model name, error message or "") from
    # the download thread. `object` because sizes can exceed a C++ int.
    _download_progress = pyqtSignal(object, object)
    _download_finished = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FlowClone Settings")
        self.setModal(False)
        self.setMinimumWidth(520)
        self._apply_color_scheme(config.get("color_scheme", theme.SCHEME_SYSTEM))

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        heading = QLabel("Settings", self)
        heading.setObjectName("heading")
        root.addWidget(heading)

        subheading = QLabel(
            "API keys are stored locally in .env. Pick a provider — Groq is free "
            "(console.groq.com) and uses whisper-large-v3-turbo + llama-3.3-70b.",
            self,
        )
        subheading.setObjectName("subheading")
        subheading.setWordWrap(True)
        root.addWidget(subheading)

        divider = QFrame(self)
        divider.setObjectName("divider")
        root.addWidget(divider)

        form = QFormLayout()
        form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        form.setContentsMargins(0, 6, 0, 6)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)

        self._provider = QComboBox()
        for pid in TRANSCRIPTION_PROVIDERS:
            self._provider.addItem(PROVIDER_SPEC[pid]["display"], pid)
        self._provider.setCurrentIndex(max(0, self._provider.findData(config.provider)))
        self._provider.currentIndexChanged.connect(self._update_provider_rows)
        form.addRow("Transcription provider", self._provider)

        self._ai_provider = QComboBox()
        for pid in AI_PROVIDERS:
            self._ai_provider.addItem(PROVIDER_SPEC[pid]["display"], pid)
        self._ai_provider.setCurrentIndex(
            max(0, self._ai_provider.findData(config.ai_provider))
        )
        self._ai_provider.currentIndexChanged.connect(self._update_provider_rows)
        form.addRow("AI processing provider", self._ai_provider)

        self._local_llm_url = QLineEdit(config.get("local_llm_url", ""))
        self._local_llm_url.setPlaceholderText("http://localhost:11434/v1")
        self._local_llm_url.setToolTip(
            "OpenAI-compatible endpoint, e.g. Ollama, llama.cpp server or LM Studio"
        )
        form.addRow("Local LLM URL", self._local_llm_url)

        self._local_llm_model = QLineEdit(config.get("local_llm_model", ""))
        self._local_llm_model.setPlaceholderText("llama3.2")
        form.addRow("Local LLM model", self._local_llm_model)

        self._openai_key = QLineEdit(config.api_key_for(PROVIDER_OPENAI))
        self._openai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._openai_key.setPlaceholderText("sk-…")
        form.addRow("OpenAI API key", self._openai_key)

        self._groq_key = QLineEdit(config.api_key_for(PROVIDER_GROQ))
        self._groq_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._groq_key.setPlaceholderText("gsk_…  (free from console.groq.com)")
        form.addRow("Groq API key", self._groq_key)
        self._form = form
        self._update_provider_rows()

        self._language = QComboBox()
        for code, label in LANGUAGES:
            self._language.addItem(label, code)
        current_lang = config.get("language", "en")
        idx = next((i for i, (c, _) in enumerate(LANGUAGES) if c == current_lang), 0)
        self._language.setCurrentIndex(idx)
        form.addRow("Transcription language", self._language)

        self._mic = QComboBox()
        # Long device names would otherwise stretch the whole dialog.
        self._mic.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._mic.setMinimumContentsLength(24)
        self._mic.addItem("System default", "")
        for label in list_input_devices():
            self._mic.addItem(label, label)
        current_mic = config.get("input_device", "") or ""
        idx = self._mic.findData(current_mic)
        if idx < 0:
            # Saved device isn't connected right now — keep it selectable so
            # saving other settings doesn't silently drop the choice.
            self._mic.addItem(f"{current_mic} — not connected", current_mic)
            idx = self._mic.count() - 1
        self._mic.setCurrentIndex(idx)
        self._mic.setToolTip(self._mic.currentText())
        self._mic.currentTextChanged.connect(self._mic.setToolTip)
        form.addRow("Microphone", self._mic)

        self._ptt = HotkeyCapture(config.get("push_to_talk_hotkey", "right alt"))
        self._ptt.captured.connect(self._ptt.set_value)
        self._ptt.captured.connect(lambda _: self._ptt._btn.setEnabled(True))
        form.addRow("Push-to-talk hotkey", self._ptt)

        self._cmd = HotkeyCapture(config.get("command_mode_hotkey", "ctrl+shift+space"))
        self._cmd.captured.connect(self._cmd.set_value)
        self._cmd.captured.connect(lambda _: self._cmd._btn.setEnabled(True))
        form.addRow("Command Mode hotkey", self._cmd)

        self._settings_hk = HotkeyCapture(config.get("settings_hotkey", "ctrl+shift+f"))
        self._settings_hk.captured.connect(self._settings_hk.set_value)
        self._settings_hk.captured.connect(
            lambda _: self._settings_hk._btn.setEnabled(True)
        )
        form.addRow("Settings hotkey", self._settings_hk)

        self._ai = QCheckBox("Enable AI cleanup")
        self._ai.setChecked(bool(config.get("ai_cleanup_enabled", True)))
        form.addRow("", self._ai)

        self._fillers = QCheckBox("Remove filler words (um, uh, like…)")
        self._fillers.setChecked(bool(config.get("remove_filler_words", True)))
        form.addRow("", self._fillers)

        if sys.platform == "win32":
            self._autostart = QCheckBox("Start FlowClone when Windows starts")
            no_autostart_tip = (
                "Only available in the installed FlowClone.exe build. "
                "When running from source, add a shortcut to shell:startup instead."
            )
        else:
            self._autostart = QCheckBox("Start FlowClone when you log in")
            no_autostart_tip = (
                "Only available in packaged builds. When running from source, "
                "add a .desktop file to ~/.config/autostart instead."
            )
        if autostart.is_supported():
            self._autostart.setChecked(autostart.is_enabled())
        else:
            self._autostart.setEnabled(False)
            self._autostart.setToolTip(no_autostart_tip)
        form.addRow("", self._autostart)

        self._color_scheme = QComboBox()
        for value, label in theme.SCHEMES:
            self._color_scheme.addItem(label, value)
        self._color_scheme.setCurrentIndex(
            max(0, self._color_scheme.findData(config.get("color_scheme")))
        )
        self._color_scheme.currentIndexChanged.connect(
            lambda: self._apply_color_scheme(self._color_scheme.currentData())
        )
        form.addRow("Color scheme", self._color_scheme)

        self._dictionary = QPlainTextEdit()
        self._dictionary.setPlaceholderText("One term per line (proper nouns, jargon…)")
        self._dictionary.setPlainText("\n".join(config.dictionary_words()))
        self._dictionary.setMinimumHeight(110)
        form.addRow("Custom dictionary", self._dictionary)

        local_divider = QFrame(self)
        local_divider.setObjectName("divider")
        form.addRow(local_divider)
        local_heading = QLabel("Local transcription (whisper.cpp)", self)
        local_heading.setObjectName("section")
        form.addRow(local_heading)

        self._whisper_model = QComboBox()
        self._icon_downloaded = _check_icon()
        self._icon_missing = _blank_icon()
        for name in whispercpp.MODELS:
            self._whisper_model.addItem(name, name)
            self._refresh_model_item(self._whisper_model.count() - 1)
        idx = self._whisper_model.findData(config.get("whisper_model", "base"))
        self._whisper_model.setCurrentIndex(max(0, idx))
        self._whisper_model.currentIndexChanged.connect(self._on_model_changed)
        form.addRow("Model", self._whisper_model)

        download_box = QWidget(self)
        download_layout = QVBoxLayout(download_box)
        download_layout.setContentsMargins(0, 0, 0, 0)
        download_layout.setSpacing(6)
        self._download_btn = QPushButton("Download", download_box)
        self._download_btn.clicked.connect(self._on_download_clicked)
        download_layout.addWidget(
            self._download_btn, alignment=Qt.AlignmentFlag.AlignLeft
        )
        self._download_bar = QProgressBar(download_box)
        self._download_bar.setRange(0, 1000)
        download_layout.addWidget(self._download_bar)
        self._download_status = QLabel("", download_box)
        self._download_status.setObjectName("footnote")
        self._download_status.setWordWrap(True)
        download_layout.addWidget(self._download_status)
        form.addRow("", download_box)

        self._download_thread: threading.Thread | None = None
        self._download_cancel = threading.Event()
        self._downloading: str | None = None
        self._download_progress.connect(self._on_download_progress)
        self._download_finished.connect(self._on_download_finished)
        self._update_download_ui()

        root.addLayout(form)

        if sys.platform == "win32":
            info_text = "Admin rights may be required for global hotkeys to reach elevated apps."
        else:
            info_text = (
                "Global hotkeys and typing need access to /dev/input and "
                "/dev/uinput (e.g. add your user to the 'input' group)."
            )
        info = QLabel(info_text, self)
        info.setObjectName("footnote")
        info.setWordWrap(True)
        root.addWidget(info)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save", self)
        save_btn.setObjectName("primary")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)
        buttons.addStretch(1)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)
        root.addLayout(buttons)

    # --- Color scheme ------------------------------------------------------

    def _apply_color_scheme(self, choice: str | None):
        """Restyle the dialog; also used to preview the combo selection."""
        self.setStyleSheet(SETTINGS_QSS_BY_SCHEME[theme.effective_scheme(choice)])

    # --- Provider-specific rows ------------------------------------------

    def _update_provider_rows(self):
        selected = {self._provider.currentData(), self._ai_provider.currentData()}
        local_llm = PROVIDER_LOCAL_LLM in selected
        self._form.setRowVisible(self._local_llm_url, local_llm)
        self._form.setRowVisible(self._local_llm_model, local_llm)
        self._form.setRowVisible(self._openai_key, PROVIDER_OPENAI in selected)
        self._form.setRowVisible(self._groq_key, PROVIDER_GROQ in selected)
        if self.isVisible():
            self.adjustSize()  # shrink when rows get hidden

    # --- whisper.cpp models ----------------------------------------------

    def _refresh_model_item(self, idx: int):
        name = self._whisper_model.itemData(idx)
        info = whispercpp.MODELS[name]
        parts = [
            name,
            whispercpp.format_size(info.size),
            "English" if info.english_only else "Multilingual",
        ]
        if info.diarization:
            parts.append("speaker turns")
        downloaded = whispercpp.is_downloaded(name)
        self._whisper_model.setItemText(idx, "  ·  ".join(parts))
        self._whisper_model.setItemIcon(
            idx, self._icon_downloaded if downloaded else self._icon_missing
        )
        self._whisper_model.setItemData(
            idx,
            "Downloaded" if downloaded else "Not downloaded",
            Qt.ItemDataRole.ToolTipRole,
        )

    def _update_download_ui(self):
        name = self._whisper_model.currentData()
        downloading = self._downloading is not None
        self._download_btn.setVisible(downloading or not whispercpp.is_downloaded(name))
        if downloading:
            self._download_btn.setText(f"Cancel download of {self._downloading}")
        else:
            size = whispercpp.format_size(whispercpp.MODELS[name].size)
            self._download_btn.setText(f"Download {name} ({size})")
        self._download_bar.setVisible(downloading)
        self._download_status.setVisible(bool(self._download_status.text()))

    def _on_model_changed(self):
        if self._downloading is None:
            self._download_status.setText("")
        self._update_download_ui()

    def _on_download_clicked(self):
        if self._downloading is not None:
            self._download_cancel.set()
            self._download_btn.setEnabled(False)
            return
        name = self._whisper_model.currentData()
        self._downloading = name
        self._download_cancel = threading.Event()
        self._download_bar.setValue(0)
        self._download_bar.setFormat("Connecting…")
        self._download_status.setText("")
        self._download_status.setVisible(False)
        self._update_download_ui()
        self._download_thread = threading.Thread(
            target=self._download_worker,
            args=(name, self._download_cancel),
            daemon=True,
        )
        self._download_thread.start()

    def _download_worker(self, name: str, cancel: threading.Event):
        try:
            whispercpp.download_model(
                name, progress=self._download_progress.emit, cancel=cancel
            )
            error = ""
        except whispercpp.DownloadCancelled:
            error = "Download cancelled."
        except Exception as e:
            error = str(e) or type(e).__name__
        self._download_finished.emit(name, error)

    def _on_download_progress(self, done: int, total: int):
        self._download_bar.setValue(int(done * 1000 / total) if total else 0)
        self._download_bar.setFormat(
            f"{whispercpp.format_size(done)} / {whispercpp.format_size(total)}"
            f"  ({done * 100 // max(total, 1)}%)"
        )

    def _on_download_finished(self, name: str, error: str):
        self._downloading = None
        self._download_btn.setEnabled(True)
        idx = self._whisper_model.findData(name)
        if idx >= 0:
            self._refresh_model_item(idx)
        self._download_status.setText(error or f"Downloaded {name}.")
        self._download_status.setVisible(True)
        self._update_download_ui()

    def done(self, result: int):
        # Closing the dialog aborts a running download; the partial file is
        # kept and resumed on the next attempt.
        self._download_cancel.set()
        super().done(result)

    def _on_save(self):
        provider = self._provider.currentData() or PROVIDER_OPENAI
        ai_provider = self._ai_provider.currentData() or PROVIDER_OPENAI
        local_llm_url = self._local_llm_url.text().strip()
        local_llm_model = self._local_llm_model.text().strip()
        openai_key = self._openai_key.text().strip()
        groq_key = self._groq_key.text().strip()
        language = self._language.currentData() or "en"
        input_device = self._mic.currentData() or ""
        whisper_model = self._whisper_model.currentData() or "base"
        ptt = self._ptt.value() or "right alt"
        cmd = self._cmd.value() or "ctrl+shift+space"
        settings_hk = self._settings_hk.value() or "ctrl+shift+f"
        color_scheme = self._color_scheme.currentData() or theme.SCHEME_SYSTEM
        ai_enabled = self._ai.isChecked()
        remove_fillers = self._fillers.isChecked()
        dict_text = self._dictionary.toPlainText()

        try:
            if openai_key:
                config.set_api_key_for(PROVIDER_OPENAI, openai_key)
            if groq_key:
                config.set_api_key_for(PROVIDER_GROQ, groq_key)
            config.set("provider", provider)
            config.set("ai_provider", ai_provider)
            config.set("local_llm_url", local_llm_url)
            config.set("local_llm_model", local_llm_model)
            config.set("language", language)
            config.set("input_device", input_device)
            config.set("whisper_model", whisper_model)
            config.set("push_to_talk_hotkey", ptt)
            config.set("command_mode_hotkey", cmd)
            config.set("settings_hotkey", settings_hk)
            config.set("color_scheme", color_scheme)
            config.set("ai_cleanup_enabled", ai_enabled)
            config.set("remove_filler_words", remove_fillers)
            config.save()
            config.save_dictionary(dict_text)
        except Exception as e:
            QMessageBox.critical(self, "FlowClone", f"Failed to save settings: {e}")
            return

        # Apply auto-start preference. Registry writes are separate from the
        # JSON save above — failure here shouldn't wipe the rest of the save.
        if autostart.is_supported():
            try:
                if self._autostart.isChecked():
                    autostart.enable()
                else:
                    autostart.disable()
            except OSError as e:
                where = (
                    "Windows startup entry"
                    if sys.platform == "win32"
                    else "autostart entry"
                )
                QMessageBox.warning(
                    self,
                    "FlowClone",
                    f"Couldn't update {where}:\n{e}\n\nOther settings were saved.",
                )

        self.saved.emit()
        self.accept()
