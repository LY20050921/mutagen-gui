"""QPainter 绘制的矢量图标。

为什么不直接用 emoji 或图标字体
--------------------------------
图标是这个界面主要的识别元素（侧边按钮、列表行内的圆形按钮），
而 emoji 在 Windows 上会随系统字体版本变化渲染成完全不同的样子，
甚至出现豆腐块。所以统一用 QPainter 画，保证跨机器一致。

所有图标都在 ``0..size`` 的逻辑坐标系里绘制，内部按 2 倍分辨率渲染以保证高分屏清晰。
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

_DEVICE_SCALE = 2


# --------------------------------------------------------------------------- #
# 绘制原语
# --------------------------------------------------------------------------- #


def _begin(size: int, color: str, width_ratio: float = 0.09) -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(size * _DEVICE_SCALE, size * _DEVICE_SCALE)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(_DEVICE_SCALE)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(max(1.4, size * width_ratio))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    return pixmap, painter


def _fill(painter: QPainter, path: QPainterPath) -> None:
    painter.fillPath(path, painter.pen().color())


def _cloud_path(left: float, top: float, side: float) -> QPainterPath:
    """云朵轮廓：3 个圆 + 1 个圆角矩形求**并集**。

    ⚠️ **必须显式设成 ``WindingFill``**：``QPainterPath`` 默认是
    ``OddEvenFill``（奇偶填充），几个圆的重叠区域会被**交替减掉**，
    渲染出来像一朵**花瓣**而不是云。

    实测踩过：小尺寸（列表行里 20px）下不容易察觉，
    把它放大到 256px 做应用图标时一眼就看出来了。
    ``simplified()`` **不会**替你纠正这一点——它会照奇偶规则把空洞固化下来，
    所以填充规则要在**调用 simplified() 之前**就设好。
    """
    def rel(rx: float, ry: float, rw: float, rh: float) -> QRectF:
        return QRectF(left + side * rx, top + side * ry, side * rw, side * rh)

    combined = QPainterPath()
    combined.setFillRule(Qt.FillRule.WindingFill)
    combined.addEllipse(rel(0.08, 0.36, 0.40, 0.40))
    combined.addEllipse(rel(0.28, 0.16, 0.48, 0.48))
    combined.addEllipse(rel(0.52, 0.38, 0.38, 0.38))
    combined.addRoundedRect(rel(0.08, 0.56, 0.82, 0.24), side * 0.12, side * 0.12)

    # simplified() 去掉内部交线；将来若有人改成「描边」绘制它就有用了。
    merged = combined.simplified()
    merged.setFillRule(Qt.FillRule.WindingFill)
    return merged


# --------------------------------------------------------------------------- #
# 各个图标
# --------------------------------------------------------------------------- #


def _draw_plus(p: QPainter, s: float) -> None:
    p.drawEllipse(QRectF(s * 0.10, s * 0.10, s * 0.80, s * 0.80))
    p.drawLine(QPointF(s * 0.50, s * 0.30), QPointF(s * 0.50, s * 0.70))
    p.drawLine(QPointF(s * 0.30, s * 0.50), QPointF(s * 0.70, s * 0.50))


def _draw_edit(p: QPainter, s: float) -> None:
    body = QPainterPath()
    body.moveTo(s * 0.16, s * 0.84)
    body.lineTo(s * 0.24, s * 0.62)
    body.lineTo(s * 0.62, s * 0.24)
    body.lineTo(s * 0.76, s * 0.38)
    body.lineTo(s * 0.38, s * 0.76)
    body.closeSubpath()
    p.drawPath(body)
    p.drawLine(QPointF(s * 0.24, s * 0.62), QPointF(s * 0.38, s * 0.76))


def _draw_delete(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(s * 0.16, s * 0.28), QPointF(s * 0.84, s * 0.28))

    handle = QPainterPath()
    handle.moveTo(s * 0.38, s * 0.28)
    handle.lineTo(s * 0.41, s * 0.18)
    handle.lineTo(s * 0.59, s * 0.18)
    handle.lineTo(s * 0.62, s * 0.28)
    p.drawPath(handle)

    body = QPainterPath()
    body.moveTo(s * 0.24, s * 0.32)
    body.lineTo(s * 0.30, s * 0.84)
    body.lineTo(s * 0.70, s * 0.84)
    body.lineTo(s * 0.76, s * 0.32)
    p.drawPath(body)

    p.drawLine(QPointF(s * 0.42, s * 0.44), QPointF(s * 0.43, s * 0.72))
    p.drawLine(QPointF(s * 0.58, s * 0.44), QPointF(s * 0.57, s * 0.72))


def _draw_settings(p: QPainter, s: float) -> None:
    center = s / 2
    p.drawEllipse(QRectF(s * 0.14, s * 0.14, s * 0.72, s * 0.72))
    p.drawEllipse(QRectF(s * 0.36, s * 0.36, s * 0.28, s * 0.28))
    for index in range(8):
        angle = math.pi / 4 * index
        p.drawLine(
            QPointF(center + math.cos(angle) * s * 0.14,
                    center + math.sin(angle) * s * 0.14),
            QPointF(center + math.cos(angle) * s * 0.30,
                    center + math.sin(angle) * s * 0.30),
        )


def _draw_about(p: QPainter, s: float) -> None:
    p.drawEllipse(QRectF(s * 0.10, s * 0.10, s * 0.80, s * 0.80))
    font = p.font()
    font.setPixelSize(int(s * 0.52))
    font.setBold(True)
    p.setFont(font)
    p.drawText(QRectF(0, 0, s, s), Qt.AlignmentFlag.AlignCenter, "?")


def _draw_play(p: QPainter, s: float) -> None:
    path = QPainterPath()
    path.moveTo(s * 0.30, s * 0.20)
    path.lineTo(s * 0.82, s * 0.50)
    path.lineTo(s * 0.30, s * 0.80)
    path.closeSubpath()
    _fill(p, path)


def _draw_stop(p: QPainter, s: float) -> None:
    path = QPainterPath()
    path.addRoundedRect(QRectF(s * 0.26, s * 0.26, s * 0.48, s * 0.48), s * 0.06, s * 0.06)
    _fill(p, path)


def _draw_pause(p: QPainter, s: float) -> None:
    for left in (0.30, 0.56):
        path = QPainterPath()
        path.addRoundedRect(QRectF(s * left, s * 0.24, s * 0.14, s * 0.52), s * 0.04, s * 0.04)
        _fill(p, path)


def _draw_refresh(p: QPainter, s: float) -> None:
    p.drawArc(QRectF(s * 0.16, s * 0.16, s * 0.68, s * 0.68), 45 * 16, 250 * 16)
    head = QPainterPath()
    head.moveTo(s * 0.86, s * 0.18)
    head.lineTo(s * 0.96, s * 0.40)
    head.lineTo(s * 0.70, s * 0.36)
    head.closeSubpath()
    _fill(p, head)


def _draw_monitor(p: QPainter, s: float) -> None:
    p.drawEllipse(QRectF(s * 0.12, s * 0.12, s * 0.76, s * 0.76))
    p.drawEllipse(QRectF(s * 0.36, s * 0.36, s * 0.28, s * 0.28))
    p.drawLine(QPointF(s * 0.50, s * 0.50), QPointF(s * 0.80, s * 0.28))


def _draw_folder(p: QPainter, s: float) -> None:
    path = QPainterPath()
    path.moveTo(s * 0.12, s * 0.30)
    path.lineTo(s * 0.38, s * 0.30)
    path.lineTo(s * 0.45, s * 0.39)
    path.lineTo(s * 0.88, s * 0.39)
    path.lineTo(s * 0.88, s * 0.80)
    path.lineTo(s * 0.12, s * 0.80)
    path.closeSubpath()
    p.drawPath(path)


def _draw_copy(p: QPainter, s: float) -> None:
    p.drawRoundedRect(QRectF(s * 0.14, s * 0.14, s * 0.46, s * 0.54), s * 0.06, s * 0.06)
    p.drawRoundedRect(QRectF(s * 0.38, s * 0.32, s * 0.46, s * 0.54), s * 0.06, s * 0.06)


def _draw_document(p: QPainter, s: float) -> None:
    path = QPainterPath()
    path.moveTo(s * 0.24, s * 0.12)
    path.lineTo(s * 0.62, s * 0.12)
    path.lineTo(s * 0.78, s * 0.30)
    path.lineTo(s * 0.78, s * 0.88)
    path.lineTo(s * 0.24, s * 0.88)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(s * 0.62, s * 0.12), QPointF(s * 0.62, s * 0.30))
    p.drawLine(QPointF(s * 0.62, s * 0.30), QPointF(s * 0.78, s * 0.30))
    for y in (0.48, 0.60, 0.72):
        p.drawLine(QPointF(s * 0.36, s * y), QPointF(s * 0.66, s * y))


def _draw_link(p: QPainter, s: float) -> None:
    p.drawArc(QRectF(s * 0.10, s * 0.30, s * 0.44, s * 0.40), 60 * 16, 250 * 16)
    p.drawArc(QRectF(s * 0.46, s * 0.30, s * 0.44, s * 0.40), 240 * 16, 250 * 16)
    p.drawLine(QPointF(s * 0.36, s * 0.50), QPointF(s * 0.64, s * 0.50))


def _draw_cloud(p: QPainter, s: float) -> None:
    """实心云朵轮廓。"""
    _fill(p, _cloud_path(0.0, 0.0, s))


def _draw_check(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(s * 0.20, s * 0.52), QPointF(s * 0.42, s * 0.74))
    p.drawLine(QPointF(s * 0.42, s * 0.74), QPointF(s * 0.82, s * 0.28))


def _draw_close(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(s * 0.26, s * 0.26), QPointF(s * 0.74, s * 0.74))
    p.drawLine(QPointF(s * 0.74, s * 0.26), QPointF(s * 0.26, s * 0.74))


def _draw_warning(p: QPainter, s: float) -> None:
    path = QPainterPath()
    path.moveTo(s * 0.50, s * 0.12)
    path.lineTo(s * 0.92, s * 0.84)
    path.lineTo(s * 0.08, s * 0.84)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(s * 0.50, s * 0.38), QPointF(s * 0.50, s * 0.62))
    p.drawPoint(QPointF(s * 0.50, s * 0.72))


_DRAWERS: dict[str, Callable[[QPainter, float], None]] = {
    "plus": _draw_plus,
    "edit": _draw_edit,
    "delete": _draw_delete,
    "settings": _draw_settings,
    "about": _draw_about,
    "play": _draw_play,
    "stop": _draw_stop,
    "pause": _draw_pause,
    "refresh": _draw_refresh,
    "monitor": _draw_monitor,
    "folder": _draw_folder,
    "copy": _draw_copy,
    "document": _draw_document,
    "link": _draw_link,
    "cloud": _draw_cloud,
    "check": _draw_check,
    "close": _draw_close,
    "warning": _draw_warning,
}

ICON_NAMES: tuple[str, ...] = tuple(_DRAWERS)


# --------------------------------------------------------------------------- #
# 应用图标（窗口 / 任务栏 / exe）
# --------------------------------------------------------------------------- #


def _app_body_path(size: float) -> QPainterPath:
    """应用图标的圆角方形底（圆角比例对齐 Windows 11 图标的观感）。"""
    margin = size * 0.045
    path = QPainterPath()
    path.addRoundedRect(
        QRectF(margin, margin, size - margin * 2, size - margin * 2),
        size * 0.225,
        size * 0.225,
    )
    return path


def _app_cloud_path(size: float) -> QPainterPath:
    """应用图标里的云朵（与实例列表的云图标同一形状，只是摆位不同）。

    云朵的自然包围盒落在相对坐标 ``x∈[0.08,0.90] y∈[0.16,0.80]``，
    所以先把绘制方框放大到 ``0.73 × size``，再把它的**视觉中心**
    对准画布中心，最后略微上移做视觉配重（不然云会显得往下坠）。
    """
    side = size * 0.73              # 云朵绘制方框的边长
    center = size / 2
    left = center - 0.49 * side     # 0.49 = 包围盒中心 x
    top = center - 0.48 * side - size * 0.02
    return _cloud_path(left, top, side)


@lru_cache(maxsize=32)
def app_icon_pixmap(size: int = 256) -> QPixmap:
    """应用图标位图，正好 ``size × size`` 像素（``devicePixelRatio = 1``）。

    内部按 4 倍超采样再缩回来：16px 那种小尺寸下，圆角与云朵边缘才不会毛糙。
    打包 exe 时用它生成 ``.ico``（见 ``tools/make_icon.py``）。
    """
    from . import theme

    factor = 4
    canvas = float(size * factor)

    source = QPixmap(size * factor, size * factor)
    source.fill(Qt.GlobalColor.transparent)

    painter = QPainter(source)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    gradient = QLinearGradient(0.0, 0.0, canvas, canvas)
    gradient.setColorAt(0.0, QColor(theme.ACCENT_HOVER))
    gradient.setColorAt(1.0, QColor(theme.ACCENT_PRESSED))
    painter.fillPath(_app_body_path(canvas), QBrush(gradient))

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#FFFFFF"))
    painter.drawPath(_app_cloud_path(canvas))
    painter.end()

    return source.scaled(
        size,
        size,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def app_icon() -> QIcon:
    """应用图标（多尺寸），供窗口 / 任务栏使用。

    刻意在**运行时**画出来、而不是读图片文件：这样 exe 里不必携带任何图片资源，
    与本模块其余图标保持一致（见模块开头说明）。
    """
    result = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        result.addPixmap(app_icon_pixmap(size))
    return result


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=256)
def pixmap(name: str, color: str, size: int = 20) -> QPixmap:
    """绘制并缓存一个图标位图。"""
    drawer = _DRAWERS.get(name)

    def _draw_fallback(painter: QPainter, s: float) -> None:
        painter.drawEllipse(QRectF(s * 0.15, s * 0.15, s * 0.70, s * 0.70))

    result, painter = _begin(size, color)
    try:
        (drawer or _draw_fallback)(painter, float(size))
    finally:
        painter.end()
    return result


@lru_cache(maxsize=256)
def icon(name: str, color: str, size: int = 20) -> QIcon:
    """取得图标（内部按 name/color/size 缓存）。"""
    return QIcon(pixmap(name, color, size))


def status_dot(color: str, size: int = 10) -> QPixmap:
    """实心圆点，用于列表里的状态徽章。"""
    pixmap_ = QPixmap(size * _DEVICE_SCALE, size * _DEVICE_SCALE)
    pixmap_.fill(Qt.GlobalColor.transparent)
    pixmap_.setDevicePixelRatio(_DEVICE_SCALE)

    painter = QPainter(pixmap_)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(QRectF(0, 0, size, size))
    painter.end()
    return pixmap_


def ringed_dot(color: str, size: int = 12, ring: str = "#00000000") -> QPixmap:
    """带描边的圆点，在深色背景上更清晰。"""
    pixmap_ = QPixmap(size * _DEVICE_SCALE, size * _DEVICE_SCALE)
    pixmap_.fill(Qt.GlobalColor.transparent)
    pixmap_.setDevicePixelRatio(_DEVICE_SCALE)

    painter = QPainter(pixmap_)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(QRectF(0, 0, size, size))
    painter.end()
    return pixmap_


__all__ = [
    "icon",
    "pixmap",
    "status_dot",
    "ringed_dot",
    "app_icon",
    "app_icon_pixmap",
    "ICON_NAMES",
]
