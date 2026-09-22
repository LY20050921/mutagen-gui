"""MutagenGUI 界面层（PySide6）。

模块划分
--------
theme        配色 / 字体 / 尺寸常量 + 全局样式表
icons        QPainter 绘制的矢量图标（不依赖图片资源与 emoji 字体）
widgets      可复用组件（侧边按钮、圆形图标按钮、状态点、实例行、空状态）
main_window  主窗口：实例列表 + 右侧模式按钮
add_dialog   Add Connection / Edit 对话框（BASIC / ADVANCED）
yml_editor   双模式 yml 编辑器（查看 / 编辑）
op_dialog    实例操作对话框（Mutagen 命令按钮 + 日志面板）
"""

__all__ = [
    "theme",
    "icons",
    "widgets",
    "main_window",
    "add_dialog",
    "yml_editor",
    "op_dialog",
]
