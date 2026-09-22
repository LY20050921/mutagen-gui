"""控制台输出的健壮性处理。

Windows 中文环境的控制台默认编码是 **GBK**，遇到 ``⚠``（U+26A0）、``✓``
这类字符会直接抛 ``UnicodeEncodeError``。

.. note::
   这不是假想问题 —— 实测踩过：``app.py --check`` 打印实例行的
   「⚠ 状态残留」徽章时，整个检查流程被这个异常打断，
   表现是「检查跑到一半突然报 traceback」。

所以两个入口（``app.py``、``mutagen_core/selftest.py``）都要在开始干活前
先调用 :func:`make_console_safe`。
"""

from __future__ import annotations

import sys


def make_console_safe() -> None:
    """让 stdout / stderr 遇到无法表示的字符时**替换**而不是抛异常。

    刻意**只改 `errors`、不改 `encoding`**：

    * 改成 UTF-8 会在 GBK 控制台上把中文全变成乱码
    * 只改 errors 则中文照常显示，只有 ``⚠`` 这类字符退化成 ``?``

    两者相比，后者是唯一可接受的行为。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            # 流被重定向到不支持重配的对象（如某些 IDE 的伪终端）
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):
            # 流已关闭或不支持重配，忽略即可 —— 这只是锦上添花
            pass


__all__ = ["make_console_safe"]
