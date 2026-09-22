"""Settings 对话框（需求 F7）。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import config
from mutagen_core.settings import Settings

from . import widgets
from .theme import Metrics


class SettingsDialog(QDialog):
    """编辑 mutagen 路径、默认 yml 目录、SSH 别名与轮询间隔。"""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(Metrics.DIALOG_WIDTH)

        self._settings = settings

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            Metrics.MARGIN, Metrics.MARGIN, Metrics.MARGIN, Metrics.MARGIN
        )
        layout.setSpacing(18)

        # Mutagen 可执行文件
        self._exe_edit = QLineEdit(settings.mutagen_exe)
        self._exe_edit.setPlaceholderText("例如 D:\\tools\\mutagen\\mutagen.exe")
        layout.addWidget(self._path_field(
            "MUTAGEN 可执行文件", self._exe_edit, self._browse_exe,
            "留空则自动在 PATH 与常见安装位置里查找",
        ))

        # 默认 yml 目录
        self._yml_dir_edit = QLineEdit(settings.default_yml_dir)
        layout.addWidget(self._path_field(
            "默认 YML 保存目录", self._yml_dir_edit, self._browse_yml_dir,
            "新建实例时的默认保存位置，仍可在每个实例里单独修改",
        ))

        # SSH 别名
        self._alias_edit = QLineEdit(settings.default_ssh_alias)
        self._alias_edit.setPlaceholderText("例如 autodl")
        layout.addWidget(widgets.FieldBox(
            "默认 SSH 别名", self._alias_edit,
            f"只读取，不修改：{config.SSH_CONFIG_PATH}",
        ))

        # 轮询间隔
        self._poll_spin = QSpinBox()
        self._poll_spin.setRange(2, 600)
        self._poll_spin.setSuffix(" 秒")
        self._poll_spin.setValue(max(2, settings.poll_interval_sec))
        layout.addWidget(widgets.FieldBox(
            "状态轮询间隔", self._poll_spin,
            "间隔越短状态越实时，但会频繁调用 mutagen",
        ))

        layout.addWidget(widgets.hairline())
        layout.addWidget(widgets.hint_label(
            "设置保存在 %APPDATA%\\MutagenGUI\\settings.json"
        ))
        layout.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        save_button.setObjectName("PrimaryButton")
        save_button.setText("Save")
        save_button.setCursor(Qt.CursorShape.PointingHandCursor)

        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_button.setObjectName("SecondaryButton")
        cancel_button.setText("Cancel")
        cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)

        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, alignment=Qt.AlignmentFlag.AlignRight)

    # ------------------------------------------------------------------ #

    def _path_field(
        self,
        label: str,
        edit: QLineEdit,
        on_browse,
        hint: str = "",
    ) -> QWidget:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(edit, 1)

        button = QPushButton("浏览")
        button.setObjectName("SecondaryButton")
        button.setFixedWidth(80)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(on_browse)
        row.addWidget(button, 0)

        return widgets.FieldBox(label, container, hint)

    def _browse_exe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 mutagen 可执行文件", self._exe_edit.text() or "C:\\",
            "可执行文件 (*.exe);;所有文件 (*)",
        )
        if path:
            self._exe_edit.setText(path)

    def _browse_yml_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择默认 yml 保存目录", self._yml_dir_edit.text() or str(Path.home())
        )
        if path:
            self._yml_dir_edit.setText(path)

    # ------------------------------------------------------------------ #

    def apply_to(self, settings: Settings) -> None:
        """把界面上的值写回 Settings 对象（由调用方决定何时 save）。"""
        settings.mutagen_exe = self._exe_edit.text().strip()
        settings.default_yml_dir = self._yml_dir_edit.text().strip()
        settings.default_ssh_alias = self._alias_edit.text().strip()
        settings.poll_interval_sec = self._poll_spin.value()


__all__ = ["SettingsDialog"]
