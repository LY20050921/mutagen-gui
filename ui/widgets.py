"""可复用界面组件。

包含需求里几个有明确规格的部件：

* :class:`SideButton`      —— 右侧「图标 + 文字」操作按钮（含激活 / 危险态）
* :class:`RoundIconButton` —— 列表行内的圆形图标按钮（编辑绿 / 删除红 / 复制灰）
* :class:`DynamicListEditor`  —— 多值参数的 ``[+]`` / ``[−]`` 动态增删行（需求 F1.9）
* :class:`KeyValueListEditor` —— ``commands`` 段的「名称 + 命令」动态列表
* :class:`FieldBox`        —— 字段容器，带标签 / 提示 / **即时错误提示**（需求 F10.1）
* :class:`InstanceRow`     —— 实例列表的一行
* :class:`EmptyState`      —— 空状态引导页（需求 6.7）
"""

from __future__ import annotations

from typing import Iterable, Optional

from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSpinBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config import MODE_DELETE, MODE_EDIT
from mutagen_core.models import Project, SessionState

from . import icons, theme
from .theme import Metrics


# --------------------------------------------------------------------------- #
# 滚轮保护
# --------------------------------------------------------------------------- #


def _enclosing_scroll_area(widget: QWidget) -> QAbstractScrollArea | None:
    """向上找出包裹该控件的滚动区域。"""
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea):
            return parent
        parent = parent.parentWidget()
    return None


class _WheelGuard(QObject):
    """应用级事件过滤器：让滚轮在下拉框 / 数字框上也能滚动页面。

    背景（实际使用反馈）
    --------------------
    ``QComboBox`` 与 ``QAbstractSpinBox`` 默认会**吃掉**滚轮事件来改自己的值。
    表单放在滚动区域里时，只要鼠标恰好停在某个选择框上，页面就滑不动了，
    而且极容易误改选项。

    处理方式
    --------
    滚轮落在这类控件上时，把滚动交给**外层滚动区域**，控件自身不再响应滚轮。
    想改值就点开列表选——这样既不会误触，滚动又始终顺畅。
    """

    _HUNGRY = (QComboBox, QAbstractSpinBox)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: D102 - Qt 接口
        if event.type() != QEvent.Type.Wheel:
            return False
        if not isinstance(obj, self._HUNGRY):
            return False

        area = _enclosing_scroll_area(obj)
        if area is None:  # 不在滚动区域里，保持 Qt 默认行为
            return False

        wheel = event  # type: ignore[assignment]
        delta = wheel.angleDelta().y()
        bar = area.verticalScrollBar()
        if delta == 0 or bar.maximum() <= bar.minimum():
            delta = wheel.angleDelta().x()
            bar = area.horizontalScrollBar()
        if delta == 0 or bar.maximum() <= bar.minimum():
            return False

        # 手感与 Qt 默认对齐：一个滚轮刻度（120）滚 3 行
        step = bar.singleStep() or 20
        bar.setValue(int(bar.value() - delta / 120.0 * step * 3))
        wheel.accept()
        return True


_WHEEL_GUARD: "_WheelGuard | None" = None


def install_wheel_guard(target: QObject) -> None:
    """安装滚轮保护。

    :param target: 传 ``QApplication`` 则全局生效；传某个控件则只对它生效
    """
    global _WHEEL_GUARD
    if _WHEEL_GUARD is None:
        _WHEEL_GUARD = _WheelGuard()
    target.installEventFilter(_WHEEL_GUARD)


# --------------------------------------------------------------------------- #
# 基础按钮
# --------------------------------------------------------------------------- #


class SideButton(QPushButton):
    """右侧竖排操作按钮：圆角矩形 + 图标 + 左对齐文字。"""

    def __init__(
        self,
        text: str,
        icon_name: str,
        *,
        size: int = Metrics.ICON_SIZE,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName("SideButton")
        self._icon_name = icon_name
        self._icon_size = size
        self.setFont(theme.button_font())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(size, size))
        self._refresh_icon()

    def _icon_color(self) -> str:
        if not self.isEnabled():
            return theme.TEXT_DISABLED
        if self.property("active") or self.property("danger"):
            return "#FFFFFF"
        return theme.TEXT_PRIMARY

    def _refresh_icon(self) -> None:
        self.setIcon(icons.icon(self._icon_name, self._icon_color(), self._icon_size))

    def set_active(self, active: bool, *, danger: bool = False) -> None:
        """切换激活态（主色底）或危险态（红色底）。"""
        theme.set_property(self, "active", bool(active))
        theme.set_property(self, "danger", bool(danger and active))
        self._refresh_icon()

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 - Qt 命名
        super().setEnabled(enabled)
        self._refresh_icon()


class RoundIconButton(QPushButton):
    """圆形实心图标按钮（列表行内使用）。

    颜色是动态的，所以样式直接写在控件上——包含全部需要的属性，
    避免依赖上层样式表的合并行为。
    """

    def __init__(
        self,
        icon_name: str,
        background: str,
        *,
        size: int = Metrics.ROUND_BUTTON_SIZE,
        icon_color: str = "#FFFFFF",
        tooltip: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        icon_size = int(size * 0.5)
        self.setIcon(icons.icon(icon_name, icon_color, icon_size))
        self.setIconSize(QSize(icon_size, icon_size))
        self.setToolTip(tooltip)
        self.setStyleSheet(
            f"QPushButton {{ background-color: {background}; border: none;"
            f" border-radius: {size // 2}px; }}"
            f"QPushButton:hover {{ border: 2px solid {theme.TEXT_PRIMARY}; }}"
            f"QPushButton:pressed {{ background-color: {theme.BG_BUTTON_PRESSED}; }}"
        )


class SmallIconButton(QPushButton):
    """小号无底色图标按钮，用于动态列表的「删除该行」。"""

    def __init__(
        self,
        icon_name: str,
        *,
        size: int = 28,
        icon_size: int = 15,
        tooltip: str = "",
        color: str = theme.TEXT_SECONDARY,
        hover_color: str = theme.DANGER,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setIcon(icons.icon(icon_name, color, icon_size))
        self.setIconSize(QSize(icon_size, icon_size))
        self.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; }}"
            f"QPushButton:hover {{ background-color: {theme.BG_BUTTON_HOVER};"
            f" border-radius: {size // 2}px; }}"
        )
        self._hover_color = hover_color
        self._normal_color = color
        self._icon_name = icon_name
        self._icon_size = icon_size

    def enterEvent(self, event) -> None:  # noqa: D102, N802 - Qt 命名
        self.setIcon(icons.icon(self._icon_name, self._hover_color, self._icon_size))
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: D102, N802 - Qt 命名
        self.setIcon(icons.icon(self._icon_name, self._normal_color, self._icon_size))
        super().leaveEvent(event)


class FlatButton(QPushButton):
    """无底色的文字按钮，用于「+ 添加一行」。"""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(theme.meta_font())
        self.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 1px dashed {theme.SEPARATOR};"
            f" border-radius: 4px; color: {theme.ACCENT}; padding: 6px 10px;"
            f" text-align: left; }}"
            f"QPushButton:hover {{ border: 1px dashed {theme.ACCENT};"
            f" background-color: {theme.BG_HOVER}; }}"
        )


# --------------------------------------------------------------------------- #
# 文本样式
# --------------------------------------------------------------------------- #


def field_label(text: str) -> QLabel:
    """字段标签：11px 全大写 + 字距。"""
    label = QLabel(text.upper())
    label.setFont(theme.field_label_font())
    label.setStyleSheet(f"color: {theme.TEXT_LABEL}; background: transparent;")
    return label


def section_title(text: str) -> QLabel:
    """分区标题：主色蓝。"""
    label = QLabel(text)
    label.setFont(theme.section_title_font())
    label.setStyleSheet(f"color: {theme.ACCENT}; background: transparent;")
    return label


def hint_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setFont(theme.meta_font())
    label.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; background: transparent;")
    label.setWordWrap(True)
    return label


def hairline() -> QFrame:
    """1px 分隔线。"""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet(f"background-color: {theme.SEPARATOR}; border: none;")
    return line


# --------------------------------------------------------------------------- #
# 字段容器（带即时校验提示）
# --------------------------------------------------------------------------- #


class FieldBox(QWidget):
    """一个表单字段：标签 + 控件 + 提示 + 错误信息。"""

    def __init__(
        self,
        label: str,
        widget: QWidget,
        hint: str = "",
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.widget = widget

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(field_label(label))
        layout.addWidget(widget)
        if hint:
            layout.addWidget(hint_label(hint))

        self._error = QLabel()
        self._error.setFont(theme.meta_font())
        self._error.setStyleSheet(f"color: {theme.DANGER}; background: transparent;")
        self._error.setWordWrap(True)
        self._error.setVisible(False)
        layout.addWidget(self._error)

    def set_error(self, message: Optional[str]) -> None:
        """设置错误信息；传 ``None`` 清除。同时给控件打上 ``invalid`` 属性供 QSS 标红。"""
        if message:
            self._error.setText(message)
            self._error.setVisible(True)
            theme.set_property(self.widget, "invalid", True)
        else:
            self._error.clear()
            self._error.setVisible(False)
            theme.set_property(self.widget, "invalid", False)


# --------------------------------------------------------------------------- #
# 动态列表（多值参数）
# --------------------------------------------------------------------------- #


class DynamicListEditor(QWidget):
    """字符串列表编辑器：每行一个输入框 + ``[−]``，底部 ``[+ 添加]``。

    对应需求 F1.9。``ignore.paths``、各生命周期钩子都用它。
    """

    changed = Signal()

    def __init__(
        self,
        placeholder: str = "",
        add_text: str = "+ 添加一行",
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._placeholder = placeholder

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(6)
        layout.addWidget(self._rows_container)

        self._add_button = FlatButton(add_text)
        self._add_button.clicked.connect(lambda: self.add_row())
        layout.addWidget(self._add_button, alignment=Qt.AlignmentFlag.AlignLeft)

    # -- 行操作 ------------------------------------------------------------ #

    def add_row(self, value: str = "") -> None:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)

        edit = QLineEdit(value)
        edit.setPlaceholderText(self._placeholder)
        edit.textChanged.connect(lambda _=None: self.changed.emit())
        row_layout.addWidget(edit, 1)

        remove = SmallIconButton("close", tooltip="删除这一行")
        remove.clicked.connect(lambda: self._remove_row(row))
        row_layout.addWidget(remove, 0)

        self._rows_layout.addWidget(row)
        self.changed.emit()

    def _remove_row(self, row: QWidget) -> None:
        self._rows_layout.removeWidget(row)
        row.setParent(None)
        row.deleteLater()
        self.changed.emit()

    # -- 取值 / 赋值 ------------------------------------------------------- #

    def values(self) -> list[str]:
        """返回非空行（空行会被丢弃，避免写出无意义的空条目）。"""
        result: list[str] = []
        for index in range(self._rows_layout.count()):
            row = self._rows_layout.itemAt(index).widget()
            if row is None:
                continue
            edit = row.findChild(QLineEdit)
            if edit is not None and edit.text().strip():
                result.append(edit.text().strip())
        return result

    def set_values(self, values: Iterable[str]) -> None:
        self.clear()
        for value in values:
            self.add_row(str(value))

    def clear(self) -> None:
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.changed.emit()

    def set_error(self, message: Optional[str]) -> None:
        self._add_button.setToolTip(message or "")


class KeyValueListEditor(QWidget):
    """「名称 + 值」的列表编辑器，用于 ``commands`` 段。"""

    changed = Signal()

    def __init__(
        self,
        key_placeholder: str = "名称",
        value_placeholder: str = "命令",
        add_text: str = "+ 添加命令",
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._key_placeholder = key_placeholder
        self._value_placeholder = value_placeholder

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(6)
        layout.addWidget(self._rows_container)

        self._add_button = FlatButton(add_text)
        self._add_button.clicked.connect(lambda: self.add_row())
        layout.addWidget(self._add_button, alignment=Qt.AlignmentFlag.AlignLeft)

    def add_row(self, key: str = "", value: str = "") -> None:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)

        key_edit = QLineEdit(key)
        key_edit.setPlaceholderText(self._key_placeholder)
        key_edit.setFixedWidth(150)
        key_edit.textChanged.connect(lambda _=None: self.changed.emit())
        row_layout.addWidget(key_edit, 0)

        value_edit = QLineEdit(value)
        value_edit.setPlaceholderText(self._value_placeholder)
        value_edit.textChanged.connect(lambda _=None: self.changed.emit())
        row_layout.addWidget(value_edit, 1)

        remove = SmallIconButton("close", tooltip="删除这一项")
        remove.clicked.connect(lambda: self._remove_row(row))
        row_layout.addWidget(remove, 0)

        self._rows_layout.addWidget(row)
        self.changed.emit()

    def _remove_row(self, row: QWidget) -> None:
        self._rows_layout.removeWidget(row)
        row.setParent(None)
        row.deleteLater()
        self.changed.emit()

    def values(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for index in range(self._rows_layout.count()):
            row = self._rows_layout.itemAt(index).widget()
            if row is None:
                continue
            edits = row.findChildren(QLineEdit)
            if len(edits) >= 2 and edits[0].text().strip():
                result[edits[0].text().strip()] = edits[1].text().strip()
        return result

    def set_values(self, mapping: dict[str, str]) -> None:
        self.clear()
        for key, value in mapping.items():
            self.add_row(str(key), str(value))

    def clear(self) -> None:
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.changed.emit()


# --------------------------------------------------------------------------- #
# 实例行
# --------------------------------------------------------------------------- #


class InstanceRow(QWidget):
    """实例列表的一行：云图标 + 名称 + 摘要 + （按模式出现的）行内按钮。

    行内按钮的实现依据需求 6.6 的「交互状态约定」（截图实证）：

    * 编辑模式 -> 绿色铅笔圆钮（+ 灰色复制圆钮）
    * 删除模式 -> 红色垃圾桶圆钮
    * 普通模式 -> 只有状态徽章

    ``missing=True`` 表示 yml 文件已不存在（被外部删除或移动）：
    整行置灰、显示红色「文件丢失」徽章，点击时由主窗口引导处理。

    ``stale_lock=True`` 表示**残留锁文件**：没有会话，但 yml 旁的
    ``<yml>.lock`` 还在（成因见 :func:`mutagen_core.states.has_stale_lock`）。
    显示琥珀色「状态残留」徽章——让用户在**点 Start 之前**就知道会失败，
    而不是点了之后收到一句莫名的 ``already running``。
    """

    activated = Signal(str)
    """点击行的主体（发出实例 id）。"""

    inline_action = Signal(str, str)
    """点击行内按钮：(实例 id, 动作名 ``edit`` / ``delete`` / ``copy-alpha``)。"""

    def __init__(
        self,
        project: Project,
        state: Optional[SessionState] = None,
        mode: str = "normal",
        missing: bool = False,
        stale_lock: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.project = project
        self.state = state
        self._missing = missing
        self._stale_lock = stale_lock
        self.setFixedHeight(Metrics.ROW_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(12)

        # 云图标（多会话 yml 用文档图标以示区别）
        icon_name = "cloud" if project.is_editable else "document"
        badge = QLabel()
        badge.setPixmap(
            icons.pixmap(
                icon_name,
                theme.ACCENT if project.is_editable else theme.TEXT_LABEL,
                Metrics.CLOUD_ICON_SIZE,
            )
        )
        badge.setFixedWidth(Metrics.CLOUD_ICON_SIZE + 4)
        badge.setStyleSheet("background: transparent;")
        layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)

        # 名称 + 摘要
        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(2)

        self._name_label = QLabel(project.name or "(未命名)")
        self._name_label.setFont(theme.instance_name_font())
        self._name_label.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        text_box.addWidget(self._name_label)

        self._meta_box = QWidget()
        self._meta_box.setStyleSheet("background: transparent;")
        self._meta_layout = QHBoxLayout(self._meta_box)
        self._meta_layout.setContentsMargins(0, 0, 0, 0)
        self._meta_layout.setSpacing(6)
        text_box.addWidget(self._meta_box)
        self._fill_meta(state)

        layout.addLayout(text_box, 1)

        # 行内按钮（按当前应用模式决定，见需求 6.6 交互状态约定）
        if mode == MODE_EDIT:
            layout.addWidget(
                self._make_round("copy", theme.BG_BUTTON, "复制本地路径", "copy-alpha")
            )
            layout.addWidget(self._make_round("edit", theme.SUCCESS, "编辑这个 yml", "edit"))
        elif mode == MODE_DELETE:
            layout.addWidget(self._make_round("delete", theme.DANGER, "删除这个 yml", "delete"))

        # 状态徽章（文件丢失 / 多会话只读）
        self._badge = QLabel()
        self._badge.setFont(theme.meta_font())
        self._badge.setVisible(False)
        layout.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignVCenter)

        self._apply_row_style()

    # -- 内部 -------------------------------------------------------------- #

    def _fill_meta(self, state: Optional[SessionState]) -> None:
        """重建摘要行。

        yml 已丢失时改为显示「文件不存在：<路径>」——此时同步状态毫无意义，
        显示出来只会误导。
        """
        while self._meta_layout.count():
            item = self._meta_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        if self._missing:
            dot = QLabel()
            dot.setPixmap(icons.status_dot(theme.DANGER, 9))
            dot.setStyleSheet("background: transparent;")
            self._meta_layout.addWidget(dot, 0)

            meta = QLabel(f"文件不存在：{self.project.yml_path}")
            meta.setFont(theme.meta_font())
            meta.setStyleSheet(f"color: {theme.DANGER}; background: transparent;")
            meta.setToolTip(
                "这个 yml 已被外部删除或移动。\n点击这一行可以移除条目或重新定位文件。"
            )
            self._meta_layout.addWidget(meta, 1)
            return

        status_text = state.display_status if state else "未启动"
        level = state.status_level if state else "stopped"
        if self._stale_lock:
            # 会有残留锁，说明**没有**会话 —— 说「未启动」会掩盖真正的问题：
            # 用户会去点 Start，然后收到一句莫名的 already running。
            status_text = "状态残留（点 Start 会失败）"
            level = "warning"
        dot = QLabel()
        dot.setPixmap(icons.status_dot(theme.status_color(level), 9))
        dot.setStyleSheet("background: transparent;")
        self._meta_layout.addWidget(dot, 0)

        parts: list[str] = [status_text]
        if self.project.beta:
            parts.append(self.project.beta)
        if state and state.conflicts:
            parts.append(f"冲突 {state.conflicts}")
        if state and state.last_error:
            parts.append("有错误")

        meta = QLabel("  ·  ".join(parts))
        meta.setFont(theme.meta_font())
        meta.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; background: transparent;")
        if state and state.last_error:
            meta.setToolTip(state.last_error)
        self._meta_layout.addWidget(meta, 1)

    def update_state(self, state: Optional[SessionState]) -> None:
        """轮询后只刷新摘要行，不重建整行（避免闪烁与选中态丢失）。"""
        self.state = state
        self._fill_meta(state)

    def set_missing(self, missing: bool) -> None:
        """更新「yml 文件是否还在」的显示状态。"""
        if self._missing == missing:
            return
        self._missing = missing
        self._apply_row_style()
        self._fill_meta(self.state)

    def is_missing(self) -> bool:
        return self._missing

    def set_stale_lock(self, stale: bool) -> None:
        """更新「是否残留锁文件」的显示状态。

        由主窗口在每次轮询后调用，依据 :func:`mutagen_core.states.has_stale_lock`。
        """
        if self._stale_lock == stale:
            return
        self._stale_lock = stale
        self._apply_row_style()
        self._fill_meta(self.state)

    def is_stale_lock(self) -> bool:
        """本行是否被标记为「状态残留」（供自检 / 冒烟测试查询）。"""
        return self._stale_lock

    def badge_text(self) -> str:
        """行尾徽章的文字；空串表示当前没有徽章（供自检 / 冒烟测试查询）。

        ⚠️ 这里用 ``isHidden()`` 而不是 ``isVisible()``：``isVisible()`` 在控件
        尚未挂到**已显示的**窗口上时**恒为 False**（自检场景正是如此），
        会让断言永远拿到空串。``isHidden()`` 反映的是「我们有没有显式隐藏它」，
        正是 :meth:`_apply_row_style` 控制的那个状态。
        """
        return "" if self._badge.isHidden() else self._badge.text()

    def _apply_row_style(self) -> None:
        """按「文件丢失 / 状态残留 / 多会话」决定名称颜色与行尾徽章。

        优先级：**文件丢失 > 状态残留 > 多会话只读** ——
        越靠前的问题越严重，也越需要用户先处理。
        """
        def badge(text: str, color: str, tooltip: str) -> None:
            self._badge.setText(text)
            self._badge.setStyleSheet(
                f"color: {color}; background: transparent;"
                f" border: 1px solid {color}; border-radius: 3px; padding: 1px 6px;"
            )
            self._badge.setToolTip(tooltip)
            self._badge.setVisible(True)

        if self._missing:
            self._name_label.setStyleSheet(
                f"color: {theme.TEXT_DISABLED}; background: transparent;"
            )
            badge(
                "⚠ 文件丢失",
                theme.DANGER,
                "这个 yml 已被外部删除或移动。\n点击这一行可以移除条目或重新定位文件。",
            )
            return

        self._name_label.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; background: transparent;"
        )

        if self._stale_lock:
            badge(
                "⚠ 状态残留",
                theme.WARNING,
                "Mutagen 认为这个项目「已在运行」，但实际上没有任何会话。\n\n"
                "原因：yml 旁的锁文件没被清理\n"
                f"  {self.project.yml_path}.lock\n\n"
                "后果：点 Start 会一直报 already running（重启 daemon 也没用）。\n"
                "怎么来的：同步运行时关机 / 重启电脑，或 daemon 异常退出。\n\n"
                "处理：点开这个实例后按 Start，界面会引导你一键清理。",
            )
            return

        if not self.project.is_editable:
            badge(
                "多会话 · 只读",
                theme.WARNING,
                "这个 yml 里含多个同步会话，v1 只能只读查看。",
            )
            return

        self._badge.setVisible(False)

    def _make_round(self, icon_name: str, color: str, tooltip: str, action: str) -> RoundIconButton:
        button = RoundIconButton(
            icon_name,
            color,
            size=36,
            tooltip=tooltip,
        )
        button.clicked.connect(lambda: self.inline_action.emit(self.project.id, action))
        return button

    # -- 交互 -------------------------------------------------------------- #

    def mouseReleaseEvent(self, event) -> None:  # noqa: D102, N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.project.id)
        super().mouseReleaseEvent(event)


# --------------------------------------------------------------------------- #
# 空状态
# --------------------------------------------------------------------------- #


class EmptyState(QWidget):
    """空状态引导页（需求 6.7）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addStretch(1)

        icon_label = QLabel()
        icon_label.setPixmap(icons.pixmap("cloud", theme.BG_BUTTON_HOVER, 96))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet("background: transparent;")
        layout.addWidget(icon_label)

        title = QLabel("还没有任何同步实例")
        title.setFont(theme.font(16, QFont.Weight.Medium))
        title.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("点击右侧「Add Connection」创建一个 yml 同步项目")
        subtitle.setFont(theme.meta_font())
        subtitle.setStyleSheet(f"color: {theme.TEXT_LABEL}; background: transparent;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        layout.addStretch(2)


__all__ = [
    "install_wheel_guard",
    "SideButton",
    "RoundIconButton",
    "SmallIconButton",
    "FlatButton",
    "field_label",
    "section_title",
    "hint_label",
    "hairline",
    "FieldBox",
    "DynamicListEditor",
    "KeyValueListEditor",
    "InstanceRow",
    "EmptyState",
]
