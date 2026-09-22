"""双模式 yml 编辑器（需求 3.5 / F3）。

两种模式
--------
* **查看模式**（默认）：只读，语法高亮
* **编辑模式**：可改，顶部有醒目的「修改未保存」提示

组件本身不决定「保存后要不要重启会话」——那是业务决策，
所以只向外发信号，由 :mod:`ui.op_dialog` / :mod:`ui.add_dialog` 处理。
"""

from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import theme

_MONO_FAMILIES = ["Cascadia Mono", "Consolas", "JetBrains Mono", "Courier New"]


def _mono_font(pixel_size: int = 13) -> QFont:
    font = QFont()
    font.setFamilies(_MONO_FAMILIES)
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(pixel_size)
    return font


class YamlHighlighter(QSyntaxHighlighter):
    """极简 YAML 高亮：键、字符串、注释、列表符号、布尔 / 数字。

    规则按顺序应用，**后面的会覆盖前面的**，所以注释放在最后。
    """

    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        self._rules: list[tuple[re.Pattern[str], QTextCharFormat, int]] = self._build()

    @staticmethod
    def _fmt(color: str, *, bold: bool = False, italic: bool = False) -> QTextCharFormat:
        char_format = QTextCharFormat()
        char_format.setForeground(QColor(color))
        if bold:
            char_format.setFontWeight(QFont.Weight.DemiBold)
        if italic:
            char_format.setFontItalic(True)
        return char_format

    def _build(self):
        return [
            # (正则, 格式, 要高亮的捕获组序号；0 表示整体)
            (re.compile(r"^\s*-\s"), self._fmt(theme.TEXT_SECONDARY), 0),
            (
                re.compile(r"^(\s*)([A-Za-z_][\w.\-]*)(\s*:)"),
                self._fmt(theme.ACCENT, bold=True),
                2,
            ),
            (re.compile(r'"[^"\n]*"'), self._fmt(theme.SUCCESS), 0),
            (re.compile(r"'[^'\n]*'"), self._fmt(theme.SUCCESS), 0),
            (re.compile(r"\b(?:true|false|null)\b"), self._fmt(theme.WARNING), 0),
            (re.compile(r"\b\d+\b"), self._fmt(theme.TEXT_PRIMARY), 0),
            # 注释放最后，让它覆盖同行其它着色
            (re.compile(r"#.*$"), self._fmt(theme.TEXT_DISABLED, italic=True), 0),
        ]

    def highlightBlock(self, text: str) -> None:  # noqa: D102, N802 - Qt 命名
        for pattern, char_format, group in self._rules:
            for match in pattern.finditer(text):
                start, end = match.span(group)
                if end > start:
                    self.setFormat(start, end - start, char_format)


class YmlEditor(QWidget):
    """yml 文本编辑器（查看 / 编辑双模式）。"""

    save_requested = Signal(str)
    """请求保存（参数是当前文本）。"""

    save_and_restart_requested = Signal(str)
    """请求保存并重启会话。"""

    edit_mode_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._editing = False
        self._original = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # ---- 顶部：模式提示 + 切换按钮 ----
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)

        self._mode_label = QLabel()
        self._mode_label.setFont(theme.meta_font())
        header.addWidget(self._mode_label, 1)

        self._toggle_button = QPushButton("编辑")
        self._toggle_button.setObjectName("SecondaryButton")
        self._toggle_button.setFixedHeight(32)
        self._toggle_button.setMinimumWidth(90)
        self._toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_button.clicked.connect(self.toggle_edit_mode)
        header.addWidget(self._toggle_button, 0)
        layout.addLayout(header)

        # ---- 文本区 ----
        self._text = QPlainTextEdit()
        self._text.setFont(_mono_font())
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._text.setTabStopDistance(28)
        self._text.setReadOnly(True)
        self._text.textChanged.connect(self._on_text_changed)
        self._highlighter = YamlHighlighter(self._text.document())
        layout.addWidget(self._text, 1)

        # ---- 底部：保存按钮（仅编辑模式可见）----
        self._footer = QWidget()
        footer_layout = QHBoxLayout(self._footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(10)
        footer_layout.addStretch(1)

        self._cancel_button = QPushButton("取消修改")
        self._cancel_button.setObjectName("SecondaryButton")
        self._cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_button.clicked.connect(self.revert)
        footer_layout.addWidget(self._cancel_button)

        self._save_button = QPushButton("保存")
        self._save_button.setObjectName("SecondaryButton")
        self._save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_button.clicked.connect(lambda: self.save_requested.emit(self.text()))
        footer_layout.addWidget(self._save_button)

        self._save_restart_button = QPushButton("保存并重启会话")
        self._save_restart_button.setObjectName("PrimaryButton")
        self._save_restart_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_restart_button.clicked.connect(
            lambda: self.save_and_restart_requested.emit(self.text())
        )
        footer_layout.addWidget(self._save_restart_button)

        layout.addWidget(self._footer)

        self.set_edit_mode(False)

    # ------------------------------------------------------------------ #

    def set_text(self, text: str, *, read_only: Optional[bool] = None) -> None:
        """设置内容并记录为「原始状态」。"""
        self._text.setPlainText(text)
        self._original = text
        if read_only is not None:
            self.set_edit_mode(not read_only)

    def text(self) -> str:
        return self._text.toPlainText()

    def is_dirty(self) -> bool:
        return self._text.toPlainText() != self._original

    def is_editing(self) -> bool:
        return self._editing

    def set_edit_mode(self, editing: bool) -> None:
        self._editing = editing
        self._text.setReadOnly(not editing)
        self._footer.setVisible(editing)
        self._toggle_button.setText("退出编辑" if editing else "编辑")
        self._refresh_mode_label()
        self.edit_mode_changed.emit(editing)

    def toggle_edit_mode(self) -> None:
        self.set_edit_mode(not self._editing)

    def revert(self) -> None:
        """放弃修改，回到上次保存的文本。"""
        self._text.setPlainText(self._original)
        self.set_edit_mode(False)

    def mark_saved(self) -> None:
        """保存成功后清掉脏标记。"""
        self._original = self._text.toPlainText()
        self._refresh_mode_label()

    def set_read_only(self, read_only: bool) -> None:
        if read_only and self._editing:
            self.set_edit_mode(False)

    # ------------------------------------------------------------------ #

    def _on_text_changed(self) -> None:
        self._refresh_mode_label()

    def _refresh_mode_label(self) -> None:
        if not self._editing:
            self._mode_label.setText("查看模式（只读）")
            self._mode_label.setStyleSheet(
                f"color: {theme.TEXT_LABEL}; background: transparent;"
            )
            return

        if self.is_dirty():
            self._mode_label.setText("⚠ 编辑模式 — 修改未保存")
            self._mode_label.setStyleSheet(
                f"color: {theme.WARNING}; background: transparent;"
            )
        else:
            self._mode_label.setText("编辑模式")
            self._mode_label.setStyleSheet(
                f"color: {theme.ACCENT}; background: transparent;"
            )


__all__ = ["YmlEditor", "YamlHighlighter"]
