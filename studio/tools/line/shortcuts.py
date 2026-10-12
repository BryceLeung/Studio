"""Editable, persistent keyboard mappings for Line review controls."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QKeySequenceEdit, QLabel,
    QMessageBox, QPushButton, QVBoxLayout,
)


DEFAULT_MAPPINGS = {
    "script_up": "A", "script_down": "Z", "link": "Space",
    "transcript_up": "K", "transcript_down": "M", "discard": "Backspace",
}
MAPPING_LABELS = {
    "script_up": "Up on script", "script_down": "Down on script",
    "link": "Link / unlink", "transcript_up": "Up on transcript",
    "transcript_down": "Down on transcript", "discard": "Discard / restore",
}


def validate_mappings(mappings: dict[str, str]) -> dict[str, str]:
    normalized = {}
    used = set()
    reserved = [sequence.toString(QKeySequence.SequenceFormat.PortableText)
                for key in (QKeySequence.StandardKey.Save, QKeySequence.StandardKey.Open)
                for sequence in QKeySequence.keyBindings(key)] + ["Ctrl+G"]
    for name in DEFAULT_MAPPINGS:
        text = mappings.get(name, "")
        sequence = QKeySequence(text, QKeySequence.SequenceFormat.PortableText)
        if text and (sequence.isEmpty() or sequence.count() != 1 or sequence[0].key() == Qt.Key.Key_unknown):
            raise ValueError(f"Choose one key combination for {MAPPING_LABELS[name]}.")
        value = sequence.toString(QKeySequence.SequenceFormat.PortableText)
        if value and (value in used or value in reserved):
            raise ValueError(f"{value} is already assigned. Choose a different key combination.")
        normalized[name] = value
        if value:
            used.add(value)
    return normalized


def preferences() -> QSettings:
    return QSettings("Studio", "Studio")


def load_mappings(settings: QSettings | None = None) -> dict[str, str]:
    settings = settings if settings is not None else preferences()
    values = {name: str(settings.value(f"line/shortcuts/{name}", default))
              for name, default in DEFAULT_MAPPINGS.items()}
    try:
        return validate_mappings(values)
    except ValueError:
        return dict(DEFAULT_MAPPINGS)


def save_mappings(mappings: dict[str, str], settings: QSettings | None = None) -> None:
    values = validate_mappings(mappings)
    settings = settings if settings is not None else preferences()
    for name, value in values.items():
        settings.setValue(f"line/shortcuts/{name}", value)
    settings.sync()
    if settings.status() != QSettings.Status.NoError:
        raise OSError("Could not save keyboard mappings.")


class KeyboardMappingsDialog(QDialog):
    def __init__(self, mappings: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Line! Keyboard Mappings")
        layout = QVBoxLayout(self)
        label = QLabel("Choose a field and press a key combination. Clear a field to disable it.")
        label.setWordWrap(True)
        layout.addWidget(label)
        form = QFormLayout()
        self.editors = {}
        for name, label in MAPPING_LABELS.items():
            editor = QKeySequenceEdit(QKeySequence(mappings[name]), self)
            editor.setMaximumSequenceLength(1)
            editor.setClearButtonEnabled(True)
            form.addRow(label, editor)
            self.editors[name] = editor
        layout.addLayout(form)
        defaults = QPushButton("Restore defaults")
        defaults.clicked.connect(self.restore_defaults)
        layout.addWidget(defaults)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.mappings = dict(mappings)

    def restore_defaults(self) -> None:
        for name, value in DEFAULT_MAPPINGS.items():
            self.editors[name].setKeySequence(QKeySequence(value))

    def accept(self) -> None:
        try:
            self.mappings = validate_mappings({name: editor.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
                                               for name, editor in self.editors.items()})
        except ValueError as error:
            QMessageBox.warning(self, "Keyboard mappings", str(error))
            return
        super().accept()
