"""Add Connection / Edit 对话框（需求 6.3 / F1 / F9 / F10）。

设计要点
--------
* **BASIC / ADVANCED 两个标签页**：BASIC 管「连哪里 + 存哪里」，ADVANCED 管 yml 的全部会话参数
* **枚举一律用下拉框**，多值一律用 ``[+]`` / ``[−]`` 动态列表（需求 6.3 的两条硬性原则）
* **Save 前做静态校验**，因为 Mutagen 对 yml 是严格解析，写错键名会导致启动失败
* SSH 连接信息 **只读** ``~/.ssh/config``，不修改

对话框不自己重启会话——它把「用户选了立即重启」这件事通过
:attr:`AddConnectionDialog.restart_requested` 告诉调用方，
由主窗口在后台执行，避免把对话框卡在长时间的 terminate/start 上。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import config
from mutagen_core import schema, sshconfig, template, validator
from mutagen_core.cli import MutagenCLI
from mutagen_core.models import Project
from mutagen_core.registry import Registry
from mutagen_core.settings import Settings

from . import theme, widgets
from .tasks import submit
from .theme import Metrics

_EMPTY_CHOICE = "（使用 Mutagen 默认）"


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #


def _split_beta(beta: str) -> tuple[str, str]:
    """``autodl:/root/x`` -> ``("autodl", "/root/x")``。"""
    if ":" in beta:
        alias, _, remote = beta.partition(":")
        return alias, remote
    return beta, ""


class _ProbeSignals(QObject):
    #: (是否成功, 界面摘要, 完整说明)。
    #: 分开传是因为摘要要塞进**单行标签**，而完整说明（含 ssh 原始报错）
    #: 得挂到 tooltip 上 —— 用户排查时确实需要看到原文，但标签放不下。
    finished = Signal(bool, str, str)


class _ProbeTask(QRunnable):
    """后台执行「测试连接」，避免 ssh 超时把界面卡住。"""

    def __init__(self, alias: str, remote_path: str, timeout: int) -> None:
        super().__init__()
        self.alias = alias
        self.remote_path = remote_path
        self.timeout = timeout
        self.signals = _ProbeSignals()

    def run(self) -> None:  # noqa: D102
        try:
            result = sshconfig.test_endpoint(self.alias, self.remote_path, self.timeout)
        except Exception as exc:  # noqa: BLE001 - 后台线程不能抛出去
            result = sshconfig.ProbeResult(False, f"测试过程出错：{exc}", str(exc))
        self.signals.finished.emit(result.ok, result.summary, result.detail)


# --------------------------------------------------------------------------- #
# 端点级覆盖编辑器
# --------------------------------------------------------------------------- #


class _OverrideRow(QWidget):
    """一行端点级覆盖：字段下拉 + 值控件（枚举给下拉，其余给输入框）。"""

    removed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._field_combo = QComboBox()
        self._field_combo.setFixedWidth(230)
        for spec in schema.ENDPOINT_OVERRIDABLE_FIELDS:
            self._field_combo.addItem(spec.label, spec.key)
        self._field_combo.currentIndexChanged.connect(self._on_field_changed)
        layout.addWidget(self._field_combo, 0)

        self._value_stack = QStackedWidget()
        self._value_combo = QComboBox()
        self._value_edit = QLineEdit()
        self._value_stack.addWidget(self._value_combo)
        self._value_stack.addWidget(self._value_edit)
        layout.addWidget(self._value_stack, 1)

        remove = widgets.SmallIconButton("close", tooltip="删除这一项")
        remove.clicked.connect(lambda: self.removed.emit(self))
        layout.addWidget(remove, 0)

        self._on_field_changed()

    def _current_spec(self) -> schema.FieldSpec:
        key = self._field_combo.currentData()
        return schema.SESSION_FIELDS_BY_KEY.get(key, schema.ENDPOINT_OVERRIDABLE_FIELDS[0])

    def _on_field_changed(self) -> None:
        spec = self._current_spec()
        if spec.kind == schema.KIND_ENUM:
            self._value_combo.clear()
            for choice in spec.choices:
                self._value_combo.addItem(choice, choice)
            self._value_stack.setCurrentWidget(self._value_combo)
        else:
            self._value_edit.setPlaceholderText(spec.hint or spec.label)
            self._value_stack.setCurrentWidget(self._value_edit)

    def key(self) -> str:
        return str(self._field_combo.currentData() or "")

    def value(self) -> object:
        spec = self._current_spec()
        if spec.kind == schema.KIND_ENUM:
            return self._value_combo.currentData()
        text = self._value_edit.text().strip()
        if spec.kind == schema.KIND_INT and text:
            try:
                return int(text)
            except ValueError:
                return text
        return text

    def set_value(self, key: str, value: object) -> None:
        index = self._field_combo.findData(key)
        if index >= 0:
            self._field_combo.setCurrentIndex(index)
        spec = self._current_spec()
        if spec.kind == schema.KIND_ENUM:
            target = self._value_combo.findData(value)
            if target >= 0:
                self._value_combo.setCurrentIndex(target)
        else:
            self._value_edit.setText(str(value))


class EndpointOverrideEditor(QWidget):
    """``configurationAlpha`` / ``configurationBeta`` 的编辑区。

    只列出**允许端点级覆盖**的字段（需求附录 C.2 实测：共 10 个）。
    """

    def __init__(self, placeholder: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows = QVBoxLayout(self)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(6)

        add_button = widgets.FlatButton("+ 添加覆盖项")
        add_button.clicked.connect(lambda: self.add_row())
        self._rows.addWidget(add_button, alignment=Qt.AlignmentFlag.AlignLeft)

        self._placeholder = placeholder

    def add_row(self, key: str = "", value: object = "") -> None:
        row = _OverrideRow()
        row.removed.connect(self._remove_row)
        if key:
            row.set_value(key, value)
        # 插到「添加」按钮之前
        self._rows.insertWidget(self._rows.count() - 1, row)

    def _remove_row(self, row: QWidget) -> None:
        self._rows.removeWidget(row)
        row.setParent(None)
        row.deleteLater()

    def values(self) -> dict[str, object]:
        result: dict[str, object] = {}
        for index in range(self._rows.count()):
            widget = self._rows.itemAt(index).widget()
            if isinstance(widget, _OverrideRow):
                key = widget.key()
                value = widget.value()
                if key and value not in ("", None):
                    result[key] = value
        return result

    def set_values(self, mapping: dict[str, object]) -> None:
        for key, value in mapping.items():
            self.add_row(key, value)


# --------------------------------------------------------------------------- #
# 主对话框
# --------------------------------------------------------------------------- #


class AddConnectionDialog(QDialog):
    """新增或编辑一个 yml 实例。"""

    def __init__(
        self,
        cli: MutagenCLI,
        settings: Settings,
        project: Optional[Project] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.cli = cli
        self.settings = settings
        self.project = project
        self.is_edit = project is not None

        self.restart_requested = False
        self.saved_project: Optional[Project] = None

        # 控件登记表（校验时按 field 名回填错误）
        self._field_boxes: dict[str, widgets.FieldBox] = {}
        self._combo_widgets: dict[str, QComboBox] = {}
        self._text_widgets: dict[str, QLineEdit] = {}
        self._spin_widgets: dict[str, QSpinBox] = {}
        self._check_widgets: dict[str, QCheckBox] = {}
        self._list_editors: dict[str, widgets.DynamicListEditor] = {}
        self._hook_editors: dict[str, widgets.DynamicListEditor] = {}

        self._yml_path_touched = False
        self._initial_snapshot = ""
        self._initial_config: Optional[template.SessionConfig] = None
        """当前表单所基于的原始配置；写盘时用于算出「哪些字段被用户删掉了」。"""

        self._source_yml_path = ""
        """导入 / 编辑时的来源 yml 路径。

        如果用户把保存路径改成了别处，就是「另存为新文件」——新文件没有可合并的
        底本，原文件的注释带不过去，所以需要额外提示。
        """

        self.setWindowTitle("Edit Connection" if self.is_edit else "Add Connection")
        self.setMinimumWidth(Metrics.DIALOG_WIDTH)

        self._build_ui()
        self._load_initial()
        self._initial_snapshot = self._snapshot()

    # ------------------------------------------------------------------ UI --

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            Metrics.MARGIN, Metrics.MARGIN, Metrics.MARGIN, Metrics.MARGIN
        )
        layout.setSpacing(14)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._wrap_scroll(self._build_basic()), "BASIC")
        self._tabs.addTab(self._wrap_scroll(self._build_advanced()), "ADVANCED")
        layout.addWidget(self._tabs, 1)

        self._summary = QLabel()
        self._summary.setFont(theme.meta_font())
        self._summary.setStyleSheet(f"color: {theme.DANGER}; background: transparent;")
        self._summary.setWordWrap(True)
        self._summary.setVisible(False)
        layout.addWidget(self._summary)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(10)

        import_button = QPushButton("从已有 yml 导入…")
        import_button.setObjectName("SecondaryButton")
        import_button.setCursor(Qt.CursorShape.PointingHandCursor)
        import_button.setToolTip("读入一个已存在的 yml，把它的配置填进这张表单")
        import_button.clicked.connect(self._on_import_yml)
        footer.addWidget(import_button, 0)
        footer.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        save_button.setObjectName("PrimaryButton")
        save_button.setText("Save")
        save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        save_button.setDefault(True)

        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_button.setObjectName("SecondaryButton")
        cancel_button.setText("Cancel")
        cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)

        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons, 0)

        layout.addLayout(footer)

    @staticmethod
    def _wrap_scroll(page: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setWidget(page)
        return area

    # ------------------------------------------------------------- BASIC --

    def _build_basic(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 12, 12, 12)
        layout.setSpacing(16)

        # NAME
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("例如 OnePoseviaGen")
        self._name_edit.textChanged.connect(self._on_name_changed)
        self._register("name", "Name", self._name_edit, schema.SESSION_NAME_HELP)
        layout.addWidget(self._field_boxes["name"])

        # Connection
        layout.addWidget(widgets.section_title("Connection"))

        self._alias_combo = QComboBox()
        self._alias_combo.setEditable(True)
        for alias in sshconfig.list_aliases():
            self._alias_combo.addItem(alias)
        self._alias_combo.setCurrentText(self.settings.default_ssh_alias)
        self._alias_combo.currentTextChanged.connect(self._refresh_ssh_details)
        self._register(
            "alias", "SSH 别名", self._alias_combo,
            f"选项来自 {config.SSH_CONFIG_PATH}（只读取，不修改）",
        )
        layout.addWidget(self._field_boxes["alias"])

        layout.addWidget(self._build_ssh_details())

        # 测试连接
        test_row = QHBoxLayout()
        test_row.setContentsMargins(0, 0, 0, 0)
        test_row.setSpacing(10)

        self._test_button = QPushButton("测试连接")
        self._test_button.setObjectName("SecondaryButton")
        self._test_button.setFixedWidth(130)
        self._test_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._test_button.clicked.connect(self._on_test_connection)
        test_row.addWidget(self._test_button, 0)

        self._test_status = QLabel()
        self._test_status.setFont(theme.meta_font())
        self._test_status.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; background: transparent;")
        self._test_status.setWordWrap(True)
        test_row.addWidget(self._test_status, 1)
        layout.addLayout(test_row)

        # Remote
        layout.addWidget(widgets.section_title("Remote"))
        self._remote_path_edit = QLineEdit()
        self._remote_path_edit.setPlaceholderText("/root/OnePoseviaGen")
        self._register("beta", "Path", self._remote_path_edit, "")
        layout.addWidget(self._field_boxes["beta"])

        # Local
        layout.addWidget(widgets.section_title("Local"))
        local_row = QWidget()
        local_layout = QHBoxLayout(local_row)
        local_layout.setContentsMargins(0, 0, 0, 0)
        local_layout.setSpacing(8)

        self._alpha_edit = QLineEdit()
        self._alpha_edit.setPlaceholderText(r"D:\code\OnePoseviaGen")
        local_layout.addWidget(self._alpha_edit, 1)

        browse_local = QPushButton("浏览")
        browse_local.setObjectName("SecondaryButton")
        browse_local.setFixedWidth(80)
        browse_local.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_local.clicked.connect(self._browse_local)
        local_layout.addWidget(browse_local, 0)

        self._register("alpha", "Path", local_row, "")
        layout.addWidget(self._field_boxes["alpha"])

        # yml 保存路径
        layout.addWidget(widgets.section_title("yml 保存路径"))
        yml_row = QWidget()
        yml_layout = QHBoxLayout(yml_row)
        yml_layout.setContentsMargins(0, 0, 0, 0)
        yml_layout.setSpacing(8)

        self._yml_path_edit = QLineEdit()
        self._yml_path_edit.textEdited.connect(self._mark_yml_path_touched)
        yml_layout.addWidget(self._yml_path_edit, 1)

        browse_yml = QPushButton("浏览")
        browse_yml.setObjectName("SecondaryButton")
        browse_yml.setFixedWidth(80)
        browse_yml.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_yml.clicked.connect(self._browse_yml)
        yml_layout.addWidget(browse_yml, 0)

        self._register(
            "yml_path", "yml 文件路径", yml_row,
            "这个路径可以修改；Save 会把 yml 真正写到磁盘上",
        )
        layout.addWidget(self._field_boxes["yml_path"])

        layout.addStretch(1)
        return page

    def _build_ssh_details(self) -> QWidget:
        panel = QGroupBox("别名解析结果（只读）")
        grid = QGridLayout(panel)
        grid.setContentsMargins(12, 8, 12, 12)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)

        self._ssh_detail_labels: dict[str, QLabel] = {}
        for row, (key, _) in enumerate(
            [("HOST", ""), ("PORT", ""), ("USER", ""), ("KEY", "")]
        ):
            label = QLabel(key)
            label.setFont(theme.field_label_font())
            label.setStyleSheet(f"color: {theme.TEXT_LABEL}; background: transparent;")
            value = QLabel("—")
            value.setFont(theme.meta_font())
            value.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; background: transparent;")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(label, row, 0)
            grid.addWidget(value, row, 1)
            self._ssh_detail_labels[key] = value

        note = QLabel("GUI 只读取 ~/.ssh/config，不会修改它")
        note.setFont(theme.meta_font())
        note.setStyleSheet(f"color: {theme.TEXT_DISABLED}; background: transparent;")
        grid.addWidget(note, 4, 0, 1, 2)

        self._refresh_ssh_details()
        return panel

    # ---------------------------------------------------------- ADVANCED --

    def _build_advanced(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 12, 12, 12)
        layout.setSpacing(16)

        # 同步行为
        group = QGroupBox("同步行为")
        box_layout = QVBoxLayout(group)
        self._add_combo(box_layout, "mode")
        self._add_combo(box_layout, "hash")
        self._add_check(box_layout, "flushOnCreate")
        layout.addWidget(group)

        # 忽略规则
        group = QGroupBox("忽略规则")
        box_layout = QVBoxLayout(group)
        self._add_check(box_layout, "ignore.vcs")
        self._add_combo(box_layout, "ignore.syntax")
        editor = widgets.DynamicListEditor(
            placeholder="例如 checkpoints 或 /tmp", add_text="+ 添加忽略规则"
        )
        self._list_editors["ignore.paths"] = editor
        spec = schema.SESSION_FIELDS_BY_KEY["ignore.paths"]
        self._field_boxes["ignore.paths"] = widgets.FieldBox(spec.label, editor, spec.hint)
        box_layout.addWidget(self._field_boxes["ignore.paths"])
        layout.addWidget(group)

        # 符号链接
        group = QGroupBox("符号链接")
        box_layout = QVBoxLayout(group)
        self._add_combo(box_layout, "symlink.mode")
        layout.addWidget(group)

        # 文件监视
        group = QGroupBox("文件监视")
        box_layout = QVBoxLayout(group)
        self._add_combo(box_layout, "watch.mode")
        self._add_spin(box_layout, "watch.pollingInterval", 0, 86400, "0（默认）")
        layout.addWidget(group)

        # 探测与扫描
        group = QGroupBox("探测与扫描")
        box_layout = QVBoxLayout(group)
        self._add_combo(box_layout, "probeMode")
        self._add_combo(box_layout, "scanMode")
        self._add_combo(box_layout, "stageMode")
        layout.addWidget(group)

        # 压缩
        group = QGroupBox("压缩")
        box_layout = QVBoxLayout(group)
        self._add_combo(box_layout, "compression.algorithm")
        layout.addWidget(group)

        # 权限
        group = QGroupBox("权限")
        box_layout = QVBoxLayout(group)
        self._add_combo(box_layout, "permissions.mode")
        self._add_text(box_layout, "permissions.defaultFileMode")
        self._add_text(box_layout, "permissions.defaultDirectoryMode")
        self._add_text(box_layout, "permissions.defaultOwner")
        self._add_text(box_layout, "permissions.defaultGroup")
        layout.addWidget(group)

        # 大小限制
        group = QGroupBox("大小限制")
        box_layout = QVBoxLayout(group)
        self._add_spin(box_layout, "maxEntryCount", 0, 2_000_000_000, "0（不限）")
        self._add_text(box_layout, "maxStagingFileSize")
        layout.addWidget(group)

        # 自定义命令
        group = QGroupBox("自定义命令（mutagen project run <名称>）")
        box_layout = QVBoxLayout(group)
        self._commands_editor = widgets.KeyValueListEditor(
            "名称，如 ssh", "命令内容", "+ 添加命令"
        )
        box_layout.addWidget(self._commands_editor)
        layout.addWidget(group)

        # 生命周期钩子
        group = QGroupBox("生命周期钩子（在系统 shell 中执行）")
        box_layout = QVBoxLayout(group)
        for hook in schema.HOOK_NAMES:
            editor = widgets.DynamicListEditor(
                placeholder="shell 命令", add_text=f"+ 添加 {hook} 命令"
            )
            self._hook_editors[hook] = editor
            box_layout.addWidget(widgets.FieldBox(hook, editor, ""))
        layout.addWidget(group)

        # 端点级覆盖
        group = QGroupBox("端点级覆盖（仅部分字段可用）")
        box_layout = QVBoxLayout(group)
        self._alpha_override = EndpointOverrideEditor("alpha")
        self._beta_override = EndpointOverrideEditor("beta")
        box_layout.addWidget(widgets.FieldBox("configurationAlpha", self._alpha_override,
                                              "只作用于本地端点"))
        box_layout.addWidget(widgets.FieldBox("configurationBeta", self._beta_override,
                                              "只作用于远程端点"))
        box_layout.addWidget(widgets.hint_label(
            "支持端点级覆盖的字段："
            + "、".join(spec.label for spec in schema.ENDPOINT_OVERRIDABLE_FIELDS)
        ))
        layout.addWidget(group)

        layout.addStretch(1)
        return page

    # -------------------------------------------------- 字段登记小工具 --

    def _register(self, key: str, label: str, widget: QWidget, hint: str) -> None:
        self._field_boxes[key] = widgets.FieldBox(label, widget, hint)

    def _add_combo(self, layout: QVBoxLayout, key: str) -> None:
        spec = schema.SESSION_FIELDS_BY_KEY[key]
        combo = QComboBox()
        combo.addItem(_EMPTY_CHOICE, "")
        for choice in spec.choices:
            combo.addItem(choice, choice)
        self._combo_widgets[key] = combo
        self._field_boxes[key] = widgets.FieldBox(spec.label, combo, spec.hint)
        layout.addWidget(self._field_boxes[key])

    def _add_text(self, layout: QVBoxLayout, key: str) -> None:
        spec = schema.SESSION_FIELDS_BY_KEY[key]
        edit = QLineEdit()
        if spec.hint:
            edit.setPlaceholderText(spec.hint)
        self._text_widgets[key] = edit
        self._field_boxes[key] = widgets.FieldBox(spec.label, edit, "")
        layout.addWidget(self._field_boxes[key])

    def _add_spin(
        self, layout: QVBoxLayout, key: str, low: int, high: int, special: str
    ) -> None:
        spec = schema.SESSION_FIELDS_BY_KEY[key]
        spin = QSpinBox()
        spin.setRange(low, high)
        if special:
            spin.setSpecialValueText(special)
        self._spin_widgets[key] = spin
        self._field_boxes[key] = widgets.FieldBox(spec.label, spin, spec.hint)
        layout.addWidget(self._field_boxes[key])

    def _add_check(self, layout: QVBoxLayout, key: str) -> None:
        spec = schema.SESSION_FIELDS_BY_KEY[key]
        box = QCheckBox(spec.label)
        if spec.hint:
            box.setToolTip(spec.hint)
        self._check_widgets[key] = box
        layout.addWidget(box)

    # ------------------------------------------------------------ 取值 --

    def _collect(self) -> template.SessionConfig:
        values: dict[str, object] = {}

        for key, combo in self._combo_widgets.items():
            data = combo.currentData()
            if data:
                values[key] = data

        for key, edit in self._text_widgets.items():
            text = edit.text().strip()
            if text:
                values[key] = text

        for key, spin in self._spin_widgets.items():
            value = spin.value()
            # pollingInterval 的 0 表示「未设置」，不能写进 yml
            if key == "watch.pollingInterval" and value <= 0:
                continue
            values[key] = value

        for key, box in self._check_widgets.items():
            values[key] = box.isChecked()

        for key, editor in self._list_editors.items():
            items = editor.values()
            if items:
                values[key] = items

        alias = self._alias_combo.currentText().strip()
        remote = self._remote_path_edit.text().strip()

        return template.SessionConfig(
            name=self._name_edit.text().strip(),
            alpha=self._alpha_edit.text().strip(),
            beta=f"{alias}:{remote}" if remote else "",
            values=values,
            commands=self._commands_editor.values(),
            hooks={
                hook: editor.values()
                for hook, editor in self._hook_editors.items()
                if editor.values()
            },
            configuration_alpha=self._alpha_override.values(),
            configuration_beta=self._beta_override.values(),
        )

    def _apply(self, config: template.SessionConfig) -> None:
        self._name_edit.setText(config.name)
        self._alpha_edit.setText(config.alpha)

        alias, remote = _split_beta(config.beta)
        if alias:
            self._alias_combo.setCurrentText(alias)
        self._remote_path_edit.setText(remote)

        for key, combo in self._combo_widgets.items():
            value = config.get(key)
            index = combo.findData(value) if value else 0
            combo.setCurrentIndex(max(0, index))

        for key, edit in self._text_widgets.items():
            value = config.get(key)
            edit.setText("" if value is None else str(value))

        for key, spin in self._spin_widgets.items():
            value = config.get(key)
            spin.setValue(int(value) if isinstance(value, int) else 0)

        for key, box in self._check_widgets.items():
            value = config.get(key)
            box.setChecked(bool(value) if value is not None else False)

        for key, editor in self._list_editors.items():
            raw = config.get(key) or []
            editor.set_values(raw if isinstance(raw, (list, tuple)) else [])

        self._commands_editor.set_values(config.commands)
        for hook, editor in self._hook_editors.items():
            editor.set_values(config.hooks.get(hook, []))
        self._alpha_override.set_values(config.configuration_alpha)
        self._beta_override.set_values(config.configuration_beta)

    # ---------------------------------------------------------- 初始载入 --

    def _load_initial(self) -> None:
        if self.is_edit and self.project is not None:
            yml_path = Path(self.project.yml_path)
            self._yml_path_edit.setText(str(yml_path))

            config_obj: Optional[template.SessionConfig] = None
            if yml_path.exists():
                try:
                    config_obj = template.load_config_from_file(yml_path)
                except Exception:  # noqa: BLE001 - 解析失败就退回默认值
                    config_obj = None

            if config_obj is None:
                config_obj = template.SessionConfig.with_defaults(
                    name=self.project.name, alpha=self.project.alpha, beta=self.project.beta
                )
            else:
                config_obj.name = config_obj.name or self.project.name

            self._initial_config = config_obj
            self._source_yml_path = str(yml_path)
            self._apply(config_obj)
            self._yml_path_touched = True
            return

        # 新增：预填默认值
        self._yml_path_edit.setText(str(self.settings.yml_dir() / "新实例.yml"))
        defaults = template.SessionConfig.with_defaults()
        self._initial_config = defaults
        self._apply(defaults)
        self._alias_combo.setCurrentText(self.settings.default_ssh_alias)

    # ------------------------------------------------------------ 联动 --

    def _mark_yml_path_touched(self, *_args: object) -> None:
        self._yml_path_touched = True

    def _on_name_changed(self, text: str) -> None:
        if self._yml_path_touched:
            return
        name = text.strip() or "新实例"
        self._yml_path_edit.setText(str(self.settings.yml_dir() / f"{name}.yml"))

    def _refresh_ssh_details(self, *_args: object) -> None:
        alias = self._alias_combo.currentText().strip()
        host = sshconfig.find_host(alias)

        if host is None:
            for key in ("HOST", "PORT", "USER", "KEY"):
                self._ssh_detail_labels[key].setText("—")
            return

        for key, value in host.summary_lines():
            self._ssh_detail_labels[key].setText(value)

    def _browse_local(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择本地目录", self._alpha_edit.text() or str(Path.home())
        )
        if path:
            self._alpha_edit.setText(path)

    def _browse_yml(self) -> None:
        start = self._yml_path_edit.text() or str(self.settings.yml_dir())
        path, _ = QFileDialog.getSaveFileName(
            self, "选择 yml 保存位置", start, "YAML 文件 (*.yml *.yaml)"
        )
        if path:
            self._yml_path_edit.setText(path)
            self._yml_path_touched = True

    # ---------------------------------------------------------- 导入 yml --

    def _on_import_yml(self) -> None:
        """读入一个已存在的 yml，把配置填进表单（需求 F1.5）。

        .. warning::
           导入后点 Save 会用**表单内容重建**整个 yml。
           如果原文件里有本表单没覆盖的字段，它们会丢失——所以下面会明确告警。
        """
        start = str(self.settings.yml_dir())
        path, _ = QFileDialog.getOpenFileName(
            self, "选择要导入的 yml", start, "YAML 文件 (*.yml *.yaml)"
        )
        if not path:
            return

        try:
            text = Path(path).read_text(encoding="utf-8")
            document = template.parse_yaml_text(text)
        except Exception as exc:  # noqa: BLE001 - YAMLError / OSError
            QMessageBox.critical(self, "导入失败", f"无法解析这个 yml：\n\n{exc}")
            return

        count = template.count_sessions(document)
        if count == 0:
            QMessageBox.warning(
                self, "导入失败",
                "这个 yml 里没有任何同步会话（sync 段下只有 defaults）。",
            )
            return
        if count > 1:
            QMessageBox.warning(
                self, "多会话 yml",
                f"这个 yml 里有 {count} 个同步会话。\n\n"
                "v1 只支持单会话 yml（方案 A）。导入后只会加载第一个会话，"
                "其余会话在保存时会丢失——建议先手动拆分。",
            )

        try:
            imported = template.load_config(document)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "导入失败", str(exc))
            return

        # ---- Save 会用表单内容「重建」整个 yml，所以注释和未覆盖字段都会丢 ----
        unknown = validator.find_unknown_keys(document)
        comment_count = text.count("#")

        # 有 ruamel 时写入是「就地合并」，注释与未覆盖字段都会保留，不必吓唬用户
        if (unknown or comment_count) and not template.HAVE_RUAMEL:
            details: list[str] = []
            if comment_count:
                details.append(f"· 原文件里有 {comment_count} 处 # 注释，保存时会全部丢失")
            if unknown:
                preview = "、".join(unknown[:8])
                more = "" if len(unknown) <= 8 else f"（共 {len(unknown)} 个）"
                details.append(f"· 表单未覆盖的字段：{preview}{more}")

            answer = QMessageBox.warning(
                self,
                "保存会重建这个 yml",
                "未安装 ruamel.yaml，点 Save 会用表单内容**重新生成**整个 yml 文件。\n\n"
                + "\n".join(details)
                + "\n\n装一个 ruamel.yaml 可以避免这个问题。\n"
                "（只是导入看看、不点 Save 的话，磁盘上的文件不会被改动。）\n\n"
                "要现在导入吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self._initial_config = imported
        self._source_yml_path = path
        self._apply(imported)
        self._yml_path_edit.setText(path)
        self._yml_path_touched = True
        self._tabs.setCurrentIndex(0)
        self._summary.setVisible(False)

    # ---------------------------------------------------------- 测试连接 --

    def _on_test_connection(self) -> None:
        alias = self._alias_combo.currentText().strip()
        remote = self._remote_path_edit.text().strip()

        if not alias:
            self._set_test_status(False, "请先填写 SSH 别名")
            return

        self._test_button.setEnabled(False)
        self._set_test_status(None, "测试中…")

        submit(
            _ProbeTask(alias, remote, config.SSH_PROBE_TIMEOUT_SEC),
            self,
            self._on_probe_finished,
        )

    def _on_probe_finished(self, ok: bool, summary: str, detail: str = "") -> None:
        self._test_button.setEnabled(True)
        self._set_test_status(ok, summary, detail)

    def _set_test_status(
        self, ok: Optional[bool], message: str, detail: str = ""
    ) -> None:
        color = theme.TEXT_SECONDARY
        prefix = ""
        if ok is True:
            color, prefix = theme.SUCCESS, "✓ "
        elif ok is False:
            color, prefix = theme.DANGER, "✗ "
        self._test_status.setStyleSheet(f"color: {color}; background: transparent;")
        self._test_status.setText(prefix + message)
        # 完整说明（含 ssh 原始报错）挂 tooltip：单行标签塞不下，
        # 但排查时确实需要看到原文
        self._test_status.setToolTip(detail)

    # ------------------------------------------------------------ 校验 --

    def _snapshot(self) -> str:
        try:
            return template.dump_yaml(self._collect())
        except Exception:  # noqa: BLE001
            return ""

    def _is_dirty(self) -> bool:
        return self._snapshot() != self._initial_snapshot

    def _validate(self) -> bool:
        config = self._collect()
        yml_path = self._yml_path_edit.text().strip()
        issues = validator.validate(config, yml_path)

        for box in self._field_boxes.values():
            box.set_error(None)

        leftovers: list[str] = []
        for issue in issues:
            if not issue.is_error:
                continue
            box = self._field_boxes.get(issue.field)
            if box is not None:
                box.set_error(issue.message)
            else:
                leftovers.append(f"{issue.field}：{issue.message}")

        if leftovers:
            self._summary.setText("；".join(leftovers[:4]))
            self._summary.setVisible(True)
        else:
            self._summary.clear()
            self._summary.setVisible(False)

        if validator.has_errors(issues):
            # 把标签页切到第一个有错的位置（BASIC 优先）
            self._tabs.setCurrentIndex(0)
            return False
        return True

    # ------------------------------------------------------------ 保存 --

    def _session_running(self) -> bool:
        try:
            result = self.cli.sync_list_json()
        except Exception:  # noqa: BLE001
            return False
        if not result.ok:
            return False
        from mutagen_core.parser import parse_sync_list_json

        name = self.project.name if self.project is not None else self._name_edit.text().strip()
        for session in parse_sync_list_json(result.output):
            if session.name == name:
                return session.is_running
        return False

    def _ask_restart(self) -> Optional[str]:
        """返回 ``restart`` / ``save`` / ``None``（取消）。"""
        box = QMessageBox(self)
        box.setWindowTitle("配置已修改")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("这个实例的会话正在运行。")
        box.setInformativeText(
            "yml 的配置改动需要重启会话才会生效，要怎么处理？"
        )
        restart_button = box.addButton("立即重启会话", QMessageBox.ButtonRole.AcceptRole)
        save_button = box.addButton("仅保存（稍后手动重启）", QMessageBox.ButtonRole.ActionRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()

        clicked = box.clickedButton()
        if clicked is restart_button:
            return "restart"
        if clicked is save_button:
            return "save"
        return None

    def _on_save(self) -> None:
        if not self._validate():
            return

        restart = False
        if self.is_edit and self._session_running():
            choice = self._ask_restart()
            if choice is None:
                return
            restart = choice == "restart"

        config = self._collect()
        yml_path = Path(self._yml_path_edit.text().strip())

        # 同名文件已存在（且不是本实例原路径）→ 询问是否覆盖
        original = Path(self.project.yml_path) if self.project is not None else None
        if yml_path.exists() and (original is None or yml_path != original):
            answer = QMessageBox.question(
                self,
                "文件已存在",
                f"{yml_path}\n\n该文件已存在，要覆盖它吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        try:
            template.write_yaml(
                config,
                yml_path,
                backup=True,
                initial=self._initial_config,
                # 把来源文件传进去当合并底本：这样「导入 A 另存为 B」时，
                # A 的注释也会一起带到 B，而不是因为 B 不存在就整份重建。
                source=self._source_yml_path or None,
            )
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", f"无法写入 yml：\n{exc}")
            return

        registry = Registry()
        if self.project is None:
            project = Project(
                name=config.name,
                yml_path=str(yml_path),
                alpha=config.alpha,
                beta=config.beta,
                session_count=1,
            )
        else:
            project = self.project
            project.name = config.name
            project.yml_path = str(yml_path)
            project.alpha = config.alpha
            project.beta = config.beta
            project.session_count = 1

        try:
            registry.add(project)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "注册表写入失败", str(exc))

        self.saved_project = project
        self.restart_requested = restart
        self.accept()

    # -------------------------------------------------------- 关闭保护 --

    def reject(self) -> None:
        if self._is_dirty():
            answer = QMessageBox.question(
                self,
                "放弃修改？",
                "表单里有未保存的修改，确定要放弃吗？",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Discard:
                return
        super().reject()


__all__ = ["AddConnectionDialog", "EndpointOverrideEditor"]
