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
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

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
    """实心云朵轮廓（用合并路径去掉内部交线）。"""
    combined = QPainterPath()
    combined.addEllipse(QRectF(s * 0.08, s * 0.36, s * 0.40, s * 0.40))
    combined.addEllipse(QRectF(s * 0.28, s * 0.16, s * 0.48, s * 0.48))
    combined.addEllipse(QRectF(s * 0.52, s * 0.38, s * 0.38, s * 0.38))
    base = QRectF(s * 0.08, s * 0.56, s * 0.82, s * 0.24)
    combined.addRoundedRect(base, s * 0.12, s * 0.12)
    _fill(p, combined.simplified())


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


__all__ = ["icon", "pixmap", "status_dot", "ringed_dot", "ICON_NAMES"]
