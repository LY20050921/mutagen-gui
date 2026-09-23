"""生成应用图标 ``assets/icon.ico``（打包 exe 用）。

为什么要有这一步
----------------
图标的**形状**是由代码画出来的（``ui/icons.py::app_icon_pixmap``），
这样运行时零图片依赖、跨机器一致。但 PyInstaller 需要磁盘上一个
**真实的 .ico 文件**才能把图标嵌进 exe 的资源段——所以这里把同一份绘图代码
渲染成多尺寸 PNG，再打包成 .ico。

为什么不用 Pillow
-----------------
Vista 之后的 ``.ico`` 允许**直接内嵌 PNG**，格式简单到不值得引一个依赖：
「6 字节文件头 + 每帧 16 字节目录项 + PNG 数据」。

用法::

    python tools/make_icon.py              # 生成 assets/icon.ico
    python tools/make_icon.py --check      # 只校验已有文件，不重新生成
    python tools/make_icon.py --preview    # 顺便输出一张 PNG 便于肉眼检查
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: Windows 图标里常用的尺寸集合（16 用于任务栏/菜单，256 用于大图标视图）
SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)

ICON_PATH = ROOT / "assets" / "icon.ico"
PREVIEW_PATH = ROOT / "assets" / "icon-preview.png"

_APP: object = None
"""持有 QGuiApplication 的引用。

离屏渲染 ``QPixmap`` 必须先有 ``QGuiApplication``；这里用模块级变量兜住它，
避免被 GC 回收（回收后 Qt 会直接崩）。
"""


def _ui_app() -> None:
    """确保存在一个 QGuiApplication（离屏即可，不需要真实窗口）。"""
    global _APP
    if _APP is not None:
        return

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    _APP = QGuiApplication.instance() or QGuiApplication([])


def _png_bytes(size: int) -> bytes:
    """渲染指定尺寸的图标，返回 PNG 字节。"""
    from PySide6.QtCore import QBuffer, QIODevice

    from ui import icons

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not icons.app_icon_pixmap(size).save(buffer, "PNG"):
        raise RuntimeError(f"{size}px 图标渲染失败")

    raw = buffer.data()
    # 先 .data() 再 bytes()：直接 bytes(QByteArray) 会被类型检查器判为「不兼容」，
    # 而 QByteArray.data() 的返回类型是 bytes | bytearray | memoryview，
    # 再包一层 bytes() 既类型干净、运行时也等价。
    return bytes(raw.data())


def write_ico(path: Path, frames: list[tuple[int, bytes]]) -> None:
    """把若干 ``(尺寸, PNG 字节)`` 打包成 .ico。

    ICO 结构（小端）::

        ICONDIR        保留(2)=0  类型(2)=1  帧数(2)
        ICONDIRENTRY×N 宽(1) 高(1) 调色板(1) 保留(1) 平面(2) 位深(2) 大小(4) 偏移(4)
        各帧数据        这里直接放整段 PNG
    """
    count = len(frames)
    directory = b""
    offset = 6 + 16 * count  # 文件头 6 字节 + 每帧目录项 16 字节
    payload = b""

    for size, png in frames:
        # 宽高各占 1 字节，所以 256 只能用 0 表示
        dimension = 0 if size >= 256 else size
        directory += struct.pack(
            "<BBBBHHII",
            dimension,            # 宽
            dimension,            # 高
            0,                    # 调色板颜色数（真彩填 0）
            0,                    # 保留
            1,                    # 色彩平面数
            32,                   # 位深
            len(png),             # 该帧数据大小
            offset,               # 该帧数据偏移
        )
        offset += len(png)
        payload += png

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<HHH", 0, 1, count) + directory + payload)


def verify(data: bytes) -> list[str]:
    """解析一遍 .ico，确认结构自洽；返回问题列表（空 = 正常）。"""
    if len(data) < 6:
        return ["文件太短，连文件头都不够"]

    problems: list[str] = []
    reserved, image_type, count = struct.unpack("<HHH", data[:6])
    if reserved != 0:
        problems.append(f"保留字段应为 0，实际 {reserved}")
    if image_type != 1:
        problems.append(f"类型应为 1（图标），实际 {image_type}")
    if count == 0:
        problems.append("一帧都没有")

    for index in range(count):
        entry = data[6 + 16 * index: 6 + 16 * (index + 1)]
        if len(entry) < 16:
            problems.append(f"第 {index} 帧目录项不完整")
            break

        width, height, _, _, _, _, length, offset = struct.unpack("<BBBBHHII", entry)
        label = 256 if width == 0 else width

        if width != height:
            problems.append(f"{label}px 宽高不一致")
        if offset + length > len(data):
            problems.append(f"{label}px 数据越界（offset={offset} size={length}）")
        elif data[offset: offset + 8] != b"\x89PNG\r\n\x1a\n":
            problems.append(f"{label}px 的载荷不是 PNG")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成应用图标 assets/icon.ico")
    parser.add_argument(
        "--check", action="store_true", help="只校验已有 .ico，不重新生成"
    )
    parser.add_argument(
        "--preview", action="store_true", help="顺便输出 assets/icon-preview.png"
    )
    args = parser.parse_args(argv)

    if not (args.check and ICON_PATH.is_file()):
        _ui_app()
        write_ico(ICON_PATH, [(size, _png_bytes(size)) for size in SIZES])

    data = ICON_PATH.read_bytes()
    problems = verify(data)

    if args.preview:
        _ui_app()
        PREVIEW_PATH.write_bytes(_png_bytes(256))

    print(f"文件：{ICON_PATH.relative_to(ROOT)}（{len(data)} 字节）")
    if problems:
        print("FAIL 图标结构有问题：")
        for item in problems:
            print(f"       {item}")
        return 1

    _, _, count = struct.unpack("<HHH", data[:6])
    sizes = ", ".join(f"{size}px" for size in SIZES[:count])
    print(f"OK {count} 帧（{sizes}），结构自洽")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
