"""主题：配色、字体、组件尺寸与全局样式表。

所有数值来自需求文档 **6.6 UI 设计规范**，取色自 SSHFS-Win Manager 实际截图。

.. note::
   Qt 的 QSS **不支持** ``letter-spacing``，所以「字段标签全大写 + 字距」这类排版
   要用 :func:`label_font` / :func:`apply_letter_spacing` 在代码里设置 ``QFont``。
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QWidget

# --------------------------------------------------------------------------- #
# 配色（需求 6.6）
# --------------------------------------------------------------------------- #

BG_WINDOW = "#2B2B2B"
"""窗口背景（深炭灰）。"""

BG_PANEL = "#333333"
"""面板 / 卡片背景。"""

BG_BUTTON = "#3C3C3C"
BG_BUTTON_HOVER = "#484848"
BG_BUTTON_PRESSED = "#2E2E2E"

BG_INPUT = "#2A2A2A"
BORDER_INPUT = "#3D3D3D"
BORDER_FOCUS = "#4A9EFF"

ACCENT = "#4A9EFF"
"""主色 / 强调色：分区标题、激活按钮、Save、Tab 下划线。"""

ACCENT_HOVER = "#6FB3FF"
ACCENT_PRESSED = "#3A8AE6"

TEXT_PRIMARY = "#FFFFFF"
TEXT_NORMAL = "#E8E8E8"
TEXT_SECONDARY = "#B0B0B0"
TEXT_LABEL = "#8A8A8A"
TEXT_DISABLED = "#5A5A5A"

DANGER = "#E53935"
"""危险色：Delete mode 激活、删除按钮。"""

DANGER_HOVER = "#EF5350"
DANGER_PRESSED = "#C62828"

SUCCESS = "#4CAF50"
"""成功色：Edit mode 行内图标。"""

WARNING = "#FFB300"
SEPARATOR = "#3D3D3D"

BG_SELECTED = "#3A3A3A"
BG_HOVER = "#333333"

# --------------------------------------------------------------------------- #
# 字体
# --------------------------------------------------------------------------- #

FONT_FAMILY = "Segoe UI"
FONT_FALLBACK = "Microsoft YaHei"

# --------------------------------------------------------------------------- #
# 尺寸（需求 6.6 组件规格）
# --------------------------------------------------------------------------- #


class Metrics:
    """组件尺寸常量。"""

    SIDE_BUTTON_WIDTH = 200
    SIDE_BUTTON_HEIGHT = 40
    SIDE_BUTTON_RADIUS = 14
    SIDE_BUTTON_SPACING = 10

    ROUND_BUTTON_SIZE = 44
    PILL_BUTTON_RADIUS = 20
    PILL_BUTTON_MIN_WIDTH = 120
    PILL_BUTTON_HEIGHT = 40

    INPUT_HEIGHT = 38
    INPUT_RADIUS = 4

    ROW_HEIGHT = 64
    ICON_SIZE = 20
    CLOUD_ICON_SIZE = 32

    WINDOW_DEFAULT_WIDTH = 1080
    WINDOW_DEFAULT_HEIGHT = 620
    WINDOW_MIN_WIDTH = 900
    WINDOW_MIN_HEIGHT = 560

    DIALOG_WIDTH = 720
    MARGIN = 24
    GAP = 12


# --------------------------------------------------------------------------- #
# 状态色（用于列表徽章）
# --------------------------------------------------------------------------- #

#: 状态**等级** -> 颜色。
#:
#: 等级由 ``mutagen_core.models.SessionState.status_level`` 给出，
#: 用它而不是中文标签，是为了避免「文案一改颜色就失效」。
STATUS_COLORS: dict[str, str] = {
    "running": SUCCESS,        # 正常同步
    "connecting": WARNING,     # 正在连接 / 掉线重连：需要留意，但不是错误
    "paused": WARNING,
    # 有错误但 Mutagen 在自动重试（waiting-for-rescan）——用警告色而非危险色，
    # 因为**不需要人工干预**，用红色会把人吓去瞎折腾
    "warning": WARNING,
    "disconnected": DANGER,
    "error": DANGER,
    "stopped": TEXT_DISABLED,  # 还没启动
}


def status_color(level: str) -> str:
    """按状态等级取颜色；取不到时用次要文字色兜底。"""
    return STATUS_COLORS.get(level, TEXT_SECONDARY)


# --------------------------------------------------------------------------- #
# 字体工厂
# --------------------------------------------------------------------------- #


def font(
    pixel_size: int,
    weight: QFont.Weight = QFont.Weight.Normal,
    *,
    letter_spacing: float | None = None,
    bold: bool = False,
) -> QFont:
    """构造一个界面字体。"""
    result = QFont(FONT_FAMILY)
    result.setStyleHint(QFont.StyleHint.SansSerif)
    result.setFamilies([FONT_FAMILY, FONT_FALLBACK])
    result.setPixelSize(pixel_size)
    result.setWeight(QFont.Weight.Bold if bold else weight)
    if letter_spacing is not None:
        result.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_spacing)
    return result


def window_title_font() -> QFont:
    """窗口标题：14px semi-bold。"""
    return font(14, QFont.Weight.DemiBold)


def instance_name_font() -> QFont:
    """实例名：15px regular。"""
    return font(15)


def field_label_font() -> QFont:
    """字段标签：11px，全大写 + 1px 字距（QSS 不支持 letter-spacing，只能代码设）。"""
    return font(11, letter_spacing=1.0)


def section_title_font() -> QFont:
    """分区标题：15px medium，主色蓝。"""
    return font(15, QFont.Weight.Medium)


def button_font() -> QFont:
    """按钮文字：13px medium。"""
    return font(13, QFont.Weight.Medium)


def meta_font() -> QFont:
    """状态 / 摘要文字：12px。"""
    return font(12)


def label_font() -> QFont:
    return font(13)


# --------------------------------------------------------------------------- #
# 调色板（让原生控件——菜单、工具提示、滚动条——也跟随暗色）
# --------------------------------------------------------------------------- #


def build_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BG_WINDOW))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT_NORMAL))
    palette.setColor(QPalette.ColorRole.Base, QColor(BG_INPUT))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(BG_PANEL))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT_NORMAL))
    palette.setColor(QPalette.ColorRole.Button, QColor(BG_BUTTON))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(BG_BUTTON))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_DISABLED))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(TEXT_DISABLED))
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(TEXT_DISABLED)
    )
    return palette


# --------------------------------------------------------------------------- #
# 全局样式表
# --------------------------------------------------------------------------- #

_STYLESHEET = f"""
/* ---------- 全局 ---------- */
QWidget {{
    background-color: {BG_WINDOW};
    color: {TEXT_NORMAL};
    font-family: "{FONT_FAMILY}", "{FONT_FALLBACK}";
    font-size: 13px;
}}

QMainWindow, QDialog {{
    background-color: {BG_WINDOW};
}}

/* ---------- 右侧操作按钮 ---------- */
QPushButton#SideButton {{
    background-color: {BG_BUTTON};
    color: {TEXT_PRIMARY};
    border: none;
    border-radius: {Metrics.SIDE_BUTTON_RADIUS}px;
    min-height: {Metrics.SIDE_BUTTON_HEIGHT}px;
    max-height: {Metrics.SIDE_BUTTON_HEIGHT}px;
    min-width: {Metrics.SIDE_BUTTON_WIDTH}px;
    max-width: {Metrics.SIDE_BUTTON_WIDTH}px;
    text-align: left;
    padding-left: 16px;
}}
QPushButton#SideButton:hover {{
    background-color: {BG_BUTTON_HOVER};
}}
QPushButton#SideButton:pressed {{
    background-color: {BG_BUTTON_PRESSED};
}}
QPushButton#SideButton:disabled {{
    background-color: {BG_PANEL};
    color: {TEXT_DISABLED};
}}
QPushButton#SideButton[active="true"] {{
    background-color: {ACCENT};
    color: #FFFFFF;
}}
QPushButton#SideButton[active="true"]:hover {{
    background-color: {ACCENT_HOVER};
}}
QPushButton#SideButton[danger="true"] {{
    background-color: {DANGER};
    color: #FFFFFF;
}}

/* ---------- 圆形图标按钮 ---------- */
QPushButton#RoundButton {{
    border: none;
    border-radius: {Metrics.ROUND_BUTTON_SIZE // 2}px;
    min-width: {Metrics.ROUND_BUTTON_SIZE}px;
    max-width: {Metrics.ROUND_BUTTON_SIZE}px;
    min-height: {Metrics.ROUND_BUTTON_SIZE}px;
    max-height: {Metrics.ROUND_BUTTON_SIZE}px;
}}
QPushButton#RoundButton:hover {{
    border: 2px solid {TEXT_PRIMARY};
}}

/* ---------- 胶囊按钮 ---------- */
QPushButton#PrimaryButton {{
    background-color: {ACCENT};
    color: #FFFFFF;
    border: none;
    border-radius: {Metrics.PILL_BUTTON_RADIUS}px;
    min-height: {Metrics.PILL_BUTTON_HEIGHT}px;
    max-height: {Metrics.PILL_BUTTON_HEIGHT}px;
    min-width: {Metrics.PILL_BUTTON_MIN_WIDTH}px;
    padding: 0 22px;
}}
QPushButton#PrimaryButton:hover {{ background-color: {ACCENT_HOVER}; }}
QPushButton#PrimaryButton:pressed {{ background-color: {ACCENT_PRESSED}; }}
QPushButton#PrimaryButton:disabled {{ background-color: {BG_BUTTON}; color: {TEXT_DISABLED}; }}

QPushButton#SecondaryButton {{
    background-color: {BG_BUTTON};
    color: {TEXT_PRIMARY};
    border: none;
    border-radius: {Metrics.PILL_BUTTON_RADIUS}px;
    min-height: {Metrics.PILL_BUTTON_HEIGHT}px;
    max-height: {Metrics.PILL_BUTTON_HEIGHT}px;
    min-width: {Metrics.PILL_BUTTON_MIN_WIDTH}px;
    padding: 0 22px;
}}
QPushButton#SecondaryButton:hover {{ background-color: {BG_BUTTON_HOVER}; }}
QPushButton#SecondaryButton:pressed {{ background-color: {BG_BUTTON_PRESSED}; }}
QPushButton#SecondaryButton:disabled {{
    background-color: {BG_PANEL};
    color: {TEXT_DISABLED};
}}

QPushButton#DangerButton {{
    background-color: {DANGER};
    color: #FFFFFF;
    border: none;
    border-radius: {Metrics.PILL_BUTTON_RADIUS}px;
    min-height: {Metrics.PILL_BUTTON_HEIGHT}px;
    max-height: {Metrics.PILL_BUTTON_HEIGHT}px;
    min-width: {Metrics.PILL_BUTTON_MIN_WIDTH}px;
    padding: 0 22px;
}}
QPushButton#DangerButton:hover {{ background-color: {DANGER_HOVER}; }}
QPushButton#DangerButton:pressed {{ background-color: {DANGER_PRESSED}; }}
/* ⚠️ 这一条不能少：缺了它，**禁用的 Stop 按钮会保持鲜红**，
   看上去跟可点的一模一样（实测被用户当成 bug 报过）。 */
QPushButton#DangerButton:disabled {{
    background-color: {BG_PANEL};
    color: {TEXT_DISABLED};
}}

/* ---------- 输入控件 ---------- */
QLineEdit, QSpinBox, QComboBox {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER_INPUT};
    border-radius: {Metrics.INPUT_RADIUS}px;
    color: {TEXT_PRIMARY};
    min-height: {Metrics.INPUT_HEIGHT}px;
    max-height: {Metrics.INPUT_HEIGHT}px;
    padding: 0 10px;
    selection-background-color: {ACCENT};
}}
QPlainTextEdit, QTextEdit {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER_INPUT};
    border-radius: {Metrics.INPUT_RADIUS}px;
    color: {TEXT_PRIMARY};
    padding: 8px 10px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {BORDER_FOCUS};
}}
QLineEdit:read-only {{
    color: {TEXT_SECONDARY};
    background-color: {BG_PANEL};
}}
QLineEdit[invalid="true"], QComboBox[invalid="true"] {{
    border: 1px solid {DANGER};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    color: {TEXT_DISABLED};
}}

QComboBox::drop-down {{
    border: none;
    width: 26px;
}}
QComboBox::down-arrow {{
    image: none;
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_SECONDARY};
    margin-right: 10px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER_INPUT};
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT};
    outline: none;
}}

/* ---------- 复选框 ---------- */
QCheckBox {{
    color: {TEXT_NORMAL};
    spacing: 8px;
    min-height: 24px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 3px;
    border: 1px solid {BORDER_INPUT};
    background-color: {BG_INPUT};
}}
QCheckBox::indicator:hover {{ border: 1px solid {ACCENT}; }}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border: 1px solid {ACCENT};
}}

/* ---------- 标签页 ---------- */
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar {{ qproperty-drawBase: 0; background: transparent; }}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_LABEL};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 8px 2px;
    margin-right: 22px;
}}
QTabBar::tab:hover {{ color: {TEXT_SECONDARY}; }}
QTabBar::tab:selected {{
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT};
}}

/* ---------- 列表 ---------- */
QListWidget {{
    background: transparent;
    border: none;
    outline: none;
}}
QListWidget::item {{
    background: transparent;
    border-radius: 6px;
    margin: 2px 6px;
}}
QListWidget::item:hover {{ background-color: {BG_HOVER}; }}
QListWidget::item:selected {{ background-color: {BG_SELECTED}; }}

/* ---------- 分组框 ---------- */
QGroupBox {{
    border: 1px solid {SEPARATOR};
    border-radius: 6px;
    margin-top: 16px;
    padding: 14px 12px 10px 12px;
    color: {ACCENT};
    font-size: 13px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    background-color: {BG_WINDOW};
}}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #4A4A4A;
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: #5A5A5A; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: #4A4A4A;
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

/* ---------- 其他 ---------- */
QStatusBar {{
    background-color: {BG_WINDOW};
    color: {TEXT_SECONDARY};
    border-top: 1px solid {SEPARATOR};
}}
QStatusBar::item {{ border: none; }}

QToolTip {{
    background-color: {BG_BUTTON};
    color: {TEXT_PRIMARY};
    border: 1px solid #4A4A4A;
    padding: 6px 8px;
}}
QSplitter::handle {{ background-color: {SEPARATOR}; }}
QLabel#Separator {{ background-color: {SEPARATOR}; }}
QMenu {{
    background-color: {BG_BUTTON};
    border: 1px solid {SEPARATOR};
    padding: 4px;
}}
QMenu::item {{ padding: 7px 24px 7px 14px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {ACCENT}; color: #FFFFFF; }}
QMenu::separator {{ height: 1px; background: {SEPARATOR}; margin: 4px 8px; }}
"""


def build_stylesheet() -> str:
    """返回全局样式表。"""
    return _STYLESHEET


def apply_theme(app: QApplication) -> None:
    """把暗色主题应用到整个应用。"""
    app.setStyle("Fusion")
    app.setPalette(build_palette())
    app.setStyleSheet(build_stylesheet())
    app.setFont(font(13))


# --------------------------------------------------------------------------- #
# 属性选择器辅助
# --------------------------------------------------------------------------- #


def set_property(widget: QWidget, name: str, value: object) -> None:
    """设置 QSS 动态属性并触发重绘。

    Qt 不会在属性变更后自动重新求值样式，必须手动 unpolish/polish。
    """
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def apply_label_font(label, kind: str = "normal") -> None:
    """按语义给标签套字体。"""
    mapping = {
        "field": field_label_font,
        "section": section_title_font,
        "instance": instance_name_font,
        "meta": meta_font,
        "title": window_title_font,
        "normal": label_font,
    }
    label.setFont(mapping.get(kind, label_font)())


__all__ = [
    "BG_WINDOW", "BG_PANEL", "BG_BUTTON", "BG_BUTTON_HOVER", "BG_BUTTON_PRESSED",
    "BG_INPUT", "BORDER_INPUT", "BORDER_FOCUS",
    "ACCENT", "ACCENT_HOVER", "ACCENT_PRESSED",
    "TEXT_PRIMARY", "TEXT_NORMAL", "TEXT_SECONDARY", "TEXT_LABEL", "TEXT_DISABLED",
    "DANGER", "SUCCESS", "WARNING", "SEPARATOR", "BG_SELECTED", "BG_HOVER",
    "FONT_FAMILY", "FONT_FALLBACK", "Metrics",
    "STATUS_COLORS", "status_color",
    "font", "window_title_font", "instance_name_font", "field_label_font",
    "section_title_font", "button_font", "meta_font", "label_font",
    "build_palette", "build_stylesheet", "apply_theme",
    "set_property", "apply_label_font",
]
