# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配方。

用法::

    pyinstaller MutagenGUI.spec --noconfirm

产物：``dist/MutagenGUI/MutagenGUI.exe``

.. warning::
   **exe 不能单独拷走** —— PyInstaller 把 Python 解释器、PySide6 等都放在
   同目录下。要用的是 ``dist/MutagenGUI/`` **整个文件夹**（也因此配置
   ``.config`` / ``ymls`` / ``logs`` 会落在文件夹里，符合本项目的便携式布局。
   桌面上放一个指向 exe 的**快捷方式**即可，见 ``tools/build_exe.ps1``。
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(SPECPATH)

# --------------------------------------------------------------------------- #
# 图标：形状由代码定义（ui/icons.py），这里只把它渲染成 exe 需要的 .ico
# --------------------------------------------------------------------------- #

ICON = ROOT / "assets" / "icon.ico"
if not ICON.is_file():
    print(f"[spec] {ICON.name} 不存在，先运行 tools/make_icon.py 生成…")
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "make_icon.py")],
        check=True,
        cwd=str(ROOT),
    )

# --------------------------------------------------------------------------- #
# 分析
# --------------------------------------------------------------------------- #

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    # ruamel.yaml 在 template.py 里是「有就用、没有就退化」的导入，
    # PyInstaller 的静态分析能看到它，这里列出来只是保险。
    hiddenimports=["ruamel.yaml"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 下面这些本项目用不到。剔掉能显著减小体积——尤其 PySide6 的
    # Qml / WebEngine / 3D / Multimedia 那几个，单独就是几十上百 MB。
    excludes=[
        "tkinter",
        "unittest",
        "doctest",
        "pydoc",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickWidgets",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebChannel",
        "PySide6.Qt3DCore",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtNetworkAuth",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtPositioning",
        "PySide6.QtRemoteObjects",
        "PySide6.QtSensors",
        "PySide6.QtSerialPort",
        "PySide6.QtSql",
        "PySide6.QtStateMachine",
        "PySide6.QtTest",
        "PySide6.QtTextToSpeech",
        "PySide6.QtWebSockets",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

# --------------------------------------------------------------------------- #
# 裁剪用不到的 Qt 动态库
#
# PyInstaller 的 PySide6 钩子会把整包 Qt 都收进来，其中 Quick / Qml / Pdf /
# OpenGL 等加起来二十多 MB，而本项目**只用 QtCore / QtGui / QtWidgets**。
#
# ⚠️ 注意 `excludes` 管不到这些：它只过滤 **Python 模块**，
#    而这些是 C 动态库（.dll）和扩展模块（.pyd），得从 binaries 里剔。
#
# ⚠️ 裁错会**启动即崩**，所以每次改完这个列表都必须真的运行一次 exe 验证。
# --------------------------------------------------------------------------- #

#: 用不到的 Qt 库 / 扩展模块（按文件名前缀匹配，小写）
_UNUSED_QT_PREFIXES = (
    "qt6quick", "qt6qml", "qt6pdf", "qt6opengl", "qt6designer", "qt6sql",
    "qt6test", "qt6charts", "qt6datavis", "qt6multimedia", "qt6positioning",
    "qt6remoteobjects", "qt6sensors", "qt6serialport", "qt6statemachine",
    "qt6texttospeech", "qt6websockets", "qt6webengine", "qt6webchannel",
    "qt6concurrent", "qt6print",
    "qtquick", "qtqml", "qtpdf", "qtopengl", "qtdesigner", "qtsql",
    "qttest", "qtcharts", "qtdatavis", "qtmultimedia", "qtpositioning",
    "qtremoteobjects", "qtsensors", "qtserialport", "qtstatemachine",
    "qttexttospeech", "qtwebsockets", "qtwebengine", "qtwebchannel",
    "qtconcurrent", "qtprint",
)


def _used(entry) -> bool:
    """这个 binary 是否需要打进包里。"""
    return not Path(entry[0]).name.lower().startswith(_UNUSED_QT_PREFIXES)


a.binaries = [item for item in a.binaries if _used(item)]

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MutagenGUI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                  # UPX 压缩常被杀软误报，不值当
    console=False,              # GUI 程序：别弹黑色控制台窗口
    disable_windowed_traceback=False,   # 崩溃时弹窗显示 traceback，而不是静默退出
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MutagenGUI",
)
