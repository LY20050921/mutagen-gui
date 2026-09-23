"""把 MutagenGUI 打包成 exe，可选创建桌面快捷方式。

用法::

    python tools/build_exe.py                          # 只打包到 dist/MutagenGUI/
    python tools/build_exe.py --verify                 # 打包 + 启动实测
    python tools/build_exe.py --shortcut               # 打包 + 建桌面快捷方式
    python tools/build_exe.py --install-to D:/Apps/MutagenGUI --shortcut
    python tools/build_exe.py --copy-config            # 顺带带上当前实例列表

.. note::
   本脚本刻意用 **Python** 而不是 PowerShell 写：PowerShell 5.1 会把没有 BOM 的
   ``.ps1`` 按 ANSI/GBK 解读，脚本里的中文会直接变成乱码并破坏语法（实测踩过）。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mutagen_core.console import make_console_safe  # noqa: E402

SPEC = ROOT / "MutagenGUI.spec"
DIST = ROOT / "dist" / "MutagenGUI"
EXE_NAME = "MutagenGUI.exe"
APP_NAME = "MutagenGUI"


def _run(argv: list[str]) -> None:
    """跑一条命令，把命令本身打出来方便排查。"""
    print(f"$ {' '.join(argv)}")
    subprocess.run(argv, check=True, cwd=str(ROOT))


def _folder_mb(path: Path) -> float:
    total = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    return round(total / 1024 / 1024, 1)


def build() -> Path:
    """生成图标并调用 PyInstaller。"""
    print("\n[1/2] 生成图标…")
    _run([sys.executable, str(ROOT / "tools" / "make_icon.py")])

    print("[2/2] PyInstaller 打包…")
    _run([
        sys.executable, "-m", "PyInstaller", str(SPEC),
        "--noconfirm", "--log-level", "WARN",
    ])

    exe = DIST / EXE_NAME
    if not exe.is_file():
        raise SystemExit(f"FAIL 打包失败：找不到 {exe}")
    print(f"\n产物：{exe}（整个文件夹 {_folder_mb(DIST)} MB）")
    return exe


def verify(exe: Path, wait: int = 15) -> bool:
    """启动一次，确认它**真的**起来了。

    只看「进程还活着」是不够的：Windows GUI 程序崩溃时会弹一个 traceback 对话框，
    进程同样活着。所以这里进一步核对**主窗口标题**——真窗口是程序名，
    错误对话框不是。
    """
    print(f"\n[验证] 启动 {exe.name}，等 {wait} 秒…")
    process = subprocess.Popen([str(exe)], cwd=str(exe.parent))
    try:
        time.sleep(wait)
        if process.poll() is not None:
            print(f"FAIL 进程已退出（退出码 {process.returncode}）")
            return False

        lock = exe.parent / ".config" / "app.lock"
        if not lock.is_file():
            print(f"FAIL 没找到 {lock} —— 配置没有落在 exe 旁边？")
            return False

        title = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Process -Id {process.pid}).MainWindowTitle"],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()

        if title != APP_NAME:
            print(f"FAIL 主窗口标题是 {title!r}，期望 {APP_NAME!r}（可能弹了崩溃对话框）")
            return False

        print(f"OK 主窗口「{title}」已出现，配置写在 exe 所在目录")
        return True
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def install_to(exe: Path, target: Path) -> Path:
    """把整个 dist 文件夹拷到指定位置（exe 不能单独拷走，见 spec 里的说明）。"""
    print(f"\n[安装] 复制到 {target} …")
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(DIST, target)
    installed = target / EXE_NAME
    print(f"OK 已安装：{installed}")
    return installed


def copy_config(exe: Path) -> None:
    """把当前开发目录的实例列表 / 设置带过去。

    打包后的程序用的是**自己旁边**的 ``.config``（便携式布局），所以默认是空的
    （或者从很久以前的 ``%APPDATA%\\MutagenGUI`` 迁移来一份旧快照）。
    想让新 exe 直接看到现在这些实例，就把这两个 json 拷过去。
    """
    source_dir = ROOT / ".config"
    target_dir = exe.parent / ".config"
    target_dir.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for name in ("projects.json", "settings.json"):
        source = source_dir / name
        if source.is_file():
            shutil.copy2(source, target_dir / name)
            copied.append(name)

    if copied:
        print(f"\n[配置] 已带上：{'、'.join(copied)} → {target_dir}")
    else:
        print(f"\n[配置] {source_dir} 里没有可带的配置，跳过")


def create_shortcut(exe: Path) -> Path:
    """在桌面创建快捷方式（借 WScript.Shell，不引入第三方依赖）。"""
    desktop = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "[Environment]::GetFolderPath('Desktop')"],
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout.strip()

    link = Path(desktop) / f"{APP_NAME}.lnk"
    script = (
        "$sh = New-Object -ComObject WScript.Shell; "
        f"$lnk = $sh.CreateShortcut('{link}'); "
        f"$lnk.TargetPath = '{exe}'; "
        f"$lnk.WorkingDirectory = '{exe.parent}'; "
        f"$lnk.IconLocation = '{exe},0'; "
        "$lnk.Description = '用图形界面管理 Mutagen 同步项目'; "
        "$lnk.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=True, timeout=60,
    )
    print(f"\n[快捷方式] 已创建：{link}")
    return link


def main(argv: list[str] | None = None) -> int:
    make_console_safe()

    parser = argparse.ArgumentParser(description="打包 MutagenGUI 为 exe")
    parser.add_argument("--verify", action="store_true", help="打包后启动一次做实测")
    parser.add_argument("--shortcut", action="store_true", help="创建桌面快捷方式")
    parser.add_argument(
        "--install-to", type=Path, metavar="DIR",
        help="把打包结果整体拷到这个目录（建议：不要把 exe 单独拎出来）",
    )
    parser.add_argument(
        "--copy-config", action="store_true",
        help="把当前 .config 里的实例列表 / 设置一并带过去",
    )
    args = parser.parse_args(argv)

    exe = build()

    if args.install_to is not None:
        exe = install_to(exe, args.install_to.resolve())

    if args.copy_config:
        copy_config(exe)

    if args.verify and not verify(exe):
        return 1

    if args.shortcut:
        create_shortcut(exe)

    print("\n完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
