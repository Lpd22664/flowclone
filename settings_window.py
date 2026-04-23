"""Settings dialog. Dark, minimal, tuned to match the overlay language."""
from __future__ import annotations

import threading

import keyboard
from PyQt6.QtCore import Qt, pyqtSignal
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
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import autostart
from config import config, PROVIDER_GROQ, PROVIDER_OPENAI, PROVIDER_SPEC


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
            combo = keyboard.read_hotkey(suppress=False)
        except Exception:
            combo = ""
        self.captured.emit(combo or self._field.text())


class SettingsDialog(QDialog):
    saved = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FlowClone Settings")
        self.setModal(False)
        self.setMinimumWidth(520)
        self.setStyleSheet(SETTINGS_QSS)

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
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        form.setContentsMargins(0, 6, 0, 6)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)

        self._provider = QComboBox()
        for pid in (PROVIDER_OPENAI, PROVIDER_GROQ):
            self._provider.addItem(PROVIDER_SPEC[pid]["display"], pid)
        current_provider = config.provider
        idx = max(
            0,
            next(
                (i for i in range(self._provider.count())
                 if self._provider.itemData(i) == current_provider),
                0,
            ),
        )
        self._provider.setCurrentIndex(idx)
        form.addRow("Provider", self._provider)

        self._openai_key = QLineEdit(config.api_key_for(PROVIDER_OPENAI))
        self._openai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._openai_key.setPlaceholderText("sk-…")
        form.addRow("OpenAI API key", self._openai_key)

        self._groq_key = QLineEdit(config.api_key_for(PROVIDER_GROQ))
        self._groq_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._groq_key.setPlaceholderText("gsk_…  (free from console.groq.com)")
        form.addRow("Groq API key", self._groq_key)

        self._language = QComboBox()
        for code, label in LANGUAGES:
            self._language.addItem(label, code)
        current_lang = config.get("language", "en")
        idx = next((i for i, (c, _) in enumerate(LANGUAGES) if c == current_lang), 0)
        self._language.setCurrentIndex(idx)
        form.addRow("Transcription language", self._language)

        self._ptt = HotkeyCapture(config.get("push_to_talk_hotkey", "right alt"))
        self._ptt.captured.connect(self._ptt.set_value)
        self._ptt.captured.connect(lambda _: self._ptt._btn.setEnabled(True))
        form.addRow("Push-to-talk hotkey", self._ptt)

        self._cmd = HotkeyCapture(config.get("command_mode_hotkey", "ctrl+shift+space"))
        self._cmd.captured.connect(self._cmd.set_value)
        self._cmd.captured.connect(lambda _: self._cmd._btn.setEnabled(True))
        form.addRow("Command Mode hotkey", self._cmd)

        self._ai = QCheckBox("Enable AI cleanup (GPT-4o-mini)")
        self._ai.setChecked(bool(config.get("ai_cleanup_enabled", True)))
        form.addRow("", self._ai)

        self._fillers = QCheckBox("Remove filler words (um, uh, like…)")
        self._fillers.setChecked(bool(config.get("remove_filler_words", True)))
        form.addRow("", self._fillers)

        self._autostart = QCheckBox("Start FlowClone when Windows starts")
        if autostart.is_supported():
            self._autostart.setChecked(autostart.is_enabled())
        else:
            self._autostart.setEnabled(False)
            self._autostart.setToolTip(
                "Only available in the installed FlowClone.exe build. "
                "When running from source, add a shortcut to shell:startup instead."
            )
        form.addRow("", self._autostart)

        self._dictionary = QPlainTextEdit()
        self._dictionary.setPlaceholderText("One term per line (proper nouns, jargon…)")
        self._dictionary.setPlainText("\n".join(config.dictionary_words()))
        self._dictionary.setMinimumHeight(110)
        form.addRow("Custom dictionary", self._dictionary)

        root.addLayout(form)

        info = QLabel(
            "Admin rights may be required for global hotkeys to reach elevated apps.",
            self,
        )
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

    def _on_save(self):
        provider = self._provider.currentData() or PROVIDER_OPENAI
        openai_key = self._openai_key.text().strip()
        groq_key = self._groq_key.text().strip()
        language = self._language.currentData() or "en"
        ptt = self._ptt.value() or "right alt"
        cmd = self._cmd.value() or "ctrl+shift+space"
        ai_enabled = self._ai.isChecked()
        remove_fillers = self._fillers.isChecked()
        dict_text = self._dictionary.toPlainText()

        try:
            if openai_key:
                config.set_api_key_for(PROVIDER_OPENAI, openai_key)
            if groq_key:
                config.set_api_key_for(PROVIDER_GROQ, groq_key)
            config.set("provider", provider)
            config.set("language", language)
            config.set("push_to_talk_hotkey", ptt)
            config.set("command_mode_hotkey", cmd)
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
                QMessageBox.warning(
                    self,
                    "FlowClone",
                    f"Couldn't update Windows startup entry:\n{e}\n\n"
                    "Other settings were saved.",
                )

        self.saved.emit()
        self.accept()
