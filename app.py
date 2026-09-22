"""MutagenGUI 程序入口。

::

    python app.py              # 正常启动
    python app.py --check      # 快速检查：离屏构建窗口后立刻退出
    python app.py --smoke      # 冒烟测试：离屏跑一小段事件循环后退出
    python app.py --version    # 打印版本
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import config
from mutagen_core.console import make_console_safe


def _parse_args(argv: list[str]) -> tuple[bool, bool, bool]:
    """返回 ``(smoke, check, show_version)``。

    * ``--check``：只构建窗口后立刻退出，用于秒级验证 UI 层没有构建期错误
    * ``--smoke``：真正跑一小段事件循环，连初始化与首次轮询一起验证
    """
    return "--smoke" in argv, "--check" in argv, "--version" in argv


_KEEP_ALIVE: list = []
"""模块级引用列表：构建出来的对话框在检查期间不能被 GC 回收。

它们内部可能已有后台任务在跑，提前回收会触发
``RuntimeError: Signal source has been deleted``。
"""


def _check_static() -> None:
    """静态检查：未定义名 / 未使用导入（未装 pyflakes 则跳过）。

    为什么要单独查这一项：``compileall`` 只查语法，构建窗口也只走到构造路径，
    而像 ``op_dialog.py`` 里漏导入的 ``QMessageBox`` 只有**走到那条错误分支**才会崩
    ——实测就是这么被用户撞上的。pyflakes 能在启动前就把它抓出来。
    """
    try:
        from pyflakes.api import checkRecursive
        from pyflakes.reporter import Reporter
    except ImportError:
        print("SKIP 静态检查（未安装 pyflakes，可 pip install pyflakes）")
        return

    import io

    out, err = io.StringIO(), io.StringIO()
    targets = [
        str(config.APP_ROOT / name)
        for name in ("app.py", "config.py", "mutagen_core", "ui")
    ]

    try:
        checkRecursive(targets, Reporter(out, err))
    except Exception as exc:  # noqa: BLE001 - 检查自身不能把流程搞崩
        print(f"FAIL 静态检查执行异常：{exc}")
        return

    report = (out.getvalue() + err.getvalue()).strip()
    if report:
        print("FAIL 静态检查发现问题：")
        print(report)
    else:
        print("OK 静态检查（无未定义名 / 未使用导入）")


def _check_button_styles() -> None:
    """检查按钮样式是否都有 ``:disabled`` 规则（仅 ``--check`` 使用）。

    为什么专门查这一项：**``:disabled`` 漏写不会报任何错**，但按钮被禁用后
    *看上去仍然可点*。实测被用户当成 bug 报过——`Start` 亮着的同一时刻
    `Stop` 也是鲜红的，因为 `DangerButton` 根本没有 `:disabled` 规则，
    禁用后背景色一点没变。功能其实是对的，纯粹是视觉误导。
    """
    from ui import theme

    qss = theme.build_stylesheet()
    names = ["PrimaryButton", "SecondaryButton", "DangerButton", "SideButton"]
    missing = [name for name in names if f"QPushButton#{name}:disabled" not in qss]
    if missing:
        print(f"FAIL 按钮样式缺 :disabled 规则（禁用后看着仍可点）：{missing}")
    else:
        print(f"OK 按钮样式的 :disabled 规则齐全（{len(names)} 个）")


def _check_row_badges() -> None:
    """检查实例行的徽章优先级（仅 ``--check`` 使用）。

    优先级必须是 **文件丢失 > 状态残留 > 多会话只读**：
    越靠前的问题越严重，也越需要用户先处理。顺序错了会让用户先去修
    次要问题，白费功夫。
    """
    from mutagen_core.models import Project
    from ui import widgets

    project = Project(
        name="check-row",
        yml_path=str(config.APP_ROOT / "ymls" / "check-row.yml"),
        alpha=str(config.APP_ROOT),
        beta="autodl:/root/check-row",
    )
    row = widgets.InstanceRow(project)
    _KEEP_ALIVE.append(row)

    stages = [
        # (场景, 文件丢失, 状态残留, 期望徽章)
        ("正常", False, False, ""),
        ("状态残留", False, True, "⚠ 状态残留"),
        ("残留 + 丢失（丢失应优先）", True, True, "⚠ 文件丢失"),
        ("丢失恢复后应退回状态残留", False, True, "⚠ 状态残留"),
    ]

    for label, missing, stale, expected in stages:
        row.set_missing(missing)
        row.set_stale_lock(stale)
        actual = row.badge_text()
        ok = actual == expected
        print(
            f"{'OK' if ok else 'FAIL'} 实例行徽章[{label}] "
            f"实际={actual!r}，期望={expected!r}"
        )


def _check_missing_yml(cli, settings) -> None:
    """验证「yml 丢失」检测：注册表指向不存在的文件时，应被标记出来。

    用**临时注册表**做检查，绝不碰用户真实的 projects.json。
    """
    try:
        from mutagen_core.models import Project
        from mutagen_core.registry import Registry
        from ui.main_window import MainWindow

        workdir = Path(tempfile.mkdtemp(prefix="mutagengui-missing-"))
        try:
            existing = workdir / "there.yml"
            existing.write_text(
                "sync:\n  demo:\n    alpha: D:/a\n    beta: autodl:/root/a\n",
                encoding="utf-8",
            )

            registry = Registry(path=workdir / "projects.json")
            registry.save([
                Project(
                    name="gone-demo",
                    yml_path=str(workdir / "not-there.yml"),
                    alpha="D:/a",
                    beta="autodl:/root/a",
                ),
                Project(
                    name="here-demo",
                    yml_path=str(existing),
                    alpha="D:/a",
                    beta="autodl:/root/a",
                ),
            ])

            window = MainWindow(cli, registry, settings)
            _KEEP_ALIVE.append(window)  # 保持引用，避免被 GC 回收

            flagged = window.missing_yml_names()
            ok = flagged == ["gone-demo"]
            detail = f"被判定为丢失的实例={flagged}（期望 ['gone-demo']）"
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001 - 检查本身不能把启动流程搞崩
        ok, detail = False, f"检查过程异常：{exc}"

    print(f"{'OK' if ok else 'FAIL'} yml 丢失检测（{detail}）")


def _check_wheel_guard() -> None:
    """验证滚轮保护：滚动区域里的下拉框收到滚轮时，应滚动页面而不是改自己的值。"""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QScrollArea,
        QVBoxLayout,
        QWidget,
    )

    try:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.resize(320, 200)

        content = QWidget()
        layout = QVBoxLayout(content)
        combos: list[QComboBox] = []
        for index in range(30):
            combo = QComboBox()
            for option in range(5):
                combo.addItem(f"选项 {index}-{option}")
            layout.addWidget(combo)
            combos.append(combo)
        area.setWidget(content)
        area.show()
        QApplication.processEvents()

        bar = area.verticalScrollBar()
        start = bar.maximum() // 2
        bar.setValue(start)

        target = combos[0]
        index_before = target.currentIndex()

        position = QPointF(target.width() / 2, target.height() / 2)
        event = QWheelEvent(
            position,
            position,
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(target, event)

        scrolled = bar.value() != start
        index_kept = target.currentIndex() == index_before
        ok = scrolled and index_kept
        detail = f"页面滚动={scrolled}，下拉框未被误改={index_kept}"
    except Exception as exc:  # noqa: BLE001 - 检查本身不能把启动流程搞崩
        ok, detail = False, f"检查过程异常：{exc}"

    print(f"{'OK' if ok else 'FAIL'} 滚轮保护（{detail}）")


def _check_dialogs(cli, settings) -> None:
    """构建各个对话框，确认它们没有构建期错误（仅 ``--check`` 使用）。"""
    from ui.add_dialog import AddConnectionDialog
    from ui.settings_dialog import SettingsDialog

    _KEEP_ALIVE.append(AddConnectionDialog(cli, settings))
    print("OK Add Connection 对话框构建成功")

    _KEEP_ALIVE.append(SettingsDialog(settings))
    print("OK Settings 对话框构建成功")

    _check_op_buttons(cli)


def _check_op_buttons(cli) -> None:
    """核对操作对话框的按钮可用性矩阵（仅 ``--check`` 使用）。

    矩阵的**定义**在 :data:`mutagen_core.states.OPERATIONS`。这里逐个状态确认
    「界面真的照那张表执行了」，并把整张表打印出来供肉眼复核。

    这条矩阵是踩坑换来的：

    * 把「**状态未知**」当成「没有会话」，Start 会成为唯一可点项，
      用户一点就报 ``project already running``；
    * 反过来，漏掉某个按钮的重新启用，它会**永久变灰**（Monitor 中过一次招）。
    """
    from mutagen_core.models import EndpointState, Project, SessionState
    from mutagen_core.states import ALL_OPERATIONS, GuiState, OPERATIONS, classify
    from ui.op_dialog import InstanceDialog

    dialog = InstanceDialog(
        cli,
        Project(
            name="check-demo",
            yml_path=str(config.APP_ROOT / "ymls" / "check-demo.yml"),
            alpha=str(config.APP_ROOT),
            beta="autodl:/root/check-demo",
        ),
    )
    _KEEP_ALIVE.append(dialog)

    up = EndpointState(connected=True)
    down = EndpointState(connected=False)

    # GUI 状态 -> (构造用的 SessionState 或 None, 是否已读到状态)
    scenarios: dict[GuiState, tuple[SessionState | None, bool]] = {
        GuiState.UNKNOWN: (None, False),
        GuiState.ABSENT: (None, True),
        GuiState.CONNECTING: (
            SessionState(
                name="check-demo", status="connecting-beta", alpha=up, beta=down
            ),
            True,
        ),
        GuiState.SYNCING: (
            SessionState(name="check-demo", status="watching", alpha=up, beta=up),
            True,
        ),
        GuiState.PAUSED: (
            SessionState(
                name="check-demo", status="paused", paused=True, alpha=up, beta=up
            ),
            True,
        ),
        GuiState.HALTED: (
            SessionState(
                name="check-demo",
                status="halted-on-error",
                last_error="hub error: boom",
                alpha=up,
                beta=up,
            ),
            True,
        ),
        GuiState.DISCONNECTED: (
            SessionState(name="check-demo", status="disconnected", alpha=down, beta=down),
            True,
        ),
    }

    if set(scenarios) != set(GuiState):
        missing = sorted(s.value for s in set(GuiState) - set(scenarios))
        print(f"FAIL 按钮矩阵测试数据不全，缺少状态：{missing}")
        return

    for gui_state in GuiState:
        state, loaded = scenarios[gui_state]

        # 先自校验：构造的数据必须真的会被判成这个状态，
        # 否则下面的断言就是在自欺欺人
        derived = classify(
            loaded=loaded,
            exists=state is not None,
            status=state.status if state else "",
            paused=state.is_paused if state else False,
        )
        if derived is not gui_state:
            print(
                f"FAIL 按钮矩阵[{gui_state.value}] 测试数据有误："
                f"构造出的状态是 {derived.value}"
            )
            continue

        dialog.set_state(state, loaded=loaded)
        actual = set(dialog.enabled_operations())
        want = set(OPERATIONS[gui_state])
        ok = actual == want
        print(
            f"{'OK' if ok else 'FAIL'} 按钮矩阵[{gui_state.value}] "
            f"可点={sorted(actual)}，期望={sorted(want)}"
        )

    print(
        f"     （状态机 {len(GuiState)} 个状态 × 操作按钮 {len(ALL_OPERATIONS)} 个）"
    )


def main(argv: list[str] | None = None) -> int:
    # 必须先做：否则打印「⚠ 状态残留」这类字符会把检查流程打断（实测踩过）
    make_console_safe()
    raw_args = list(sys.argv if argv is None else argv)
    smoke, check, show_version = _parse_args(raw_args)
    headless = smoke or check

    if show_version:
        print(f"{config.APP_NAME} {config.APP_VERSION}")
        return 0

    # 无头模式走离屏渲染，不需要真实显示器
    if headless:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from mutagen_core.cli import MutagenCLI
    from mutagen_core.registry import Registry, SingleInstanceLock
    from mutagen_core.settings import Settings
    from ui import theme, widgets
    from ui.main_window import MainWindow

    def notify(title: str, text: str, *, critical: bool = False) -> None:
        """弹提示。

        **无头模式下必须只打印、不弹窗**：离屏渲染没有人能点按钮，
        模态框会把进程永久挂住（实测踩过这个坑——进程卡死并一直持有单实例锁）。
        """
        if headless:
            print(f"[{title}] {text}", file=sys.stderr)
            return
        if critical:
            QMessageBox.critical(None, title, text)
        else:
            QMessageBox.warning(None, title, text)

    config.ensure_directories()
    migration_notice = config.migrate_legacy_config()

    app = QApplication(raw_args)
    app.setApplicationName(config.APP_NAME)
    app.setApplicationVersion(config.APP_VERSION)
    app.setOrganizationName(config.APP_NAME)
    theme.apply_theme(app)

    # Qt 的下拉框 / 数字框默认会「吃掉」滚轮事件来改自己的值，
    # 于是表单里滑不动页面。装个全局保护，把滚轮让给外层滚动区域。
    widgets.install_wheel_guard(app)

    settings = Settings.load()

    # ---- 单实例保护 ----
    lock = SingleInstanceLock(config.LOCK_FILE)
    if not lock.acquire():
        notify(
            config.APP_NAME,
            "已有另一个 MutagenGUI 实例在运行。\n\n"
            "本程序不支持同时运行多个窗口（避免注册表互相覆盖）。",
        )
        return 1

    try:
        if lock.warning:
            print(f"[警告] {lock.warning}", file=sys.stderr)

        # ---- 定位 mutagen ----
        exe = settings.mutagen_path()
        if exe is None:
            notify(
                "找不到 Mutagen",
                "没有找到 mutagen 可执行文件。\n\n"
                "程序仍会启动，请在 Settings 里指定它的路径。",
            )
            exe = Path(settings.mutagen_exe or "mutagen.exe")

        cli = MutagenCLI(exe=exe)
        window = MainWindow(cli, Registry(), settings)

        if migration_notice:
            print(f"[提示] {migration_notice}", file=sys.stderr)
            window.statusBar().showMessage(migration_notice, 20000)

        window.show()

        if check:
            print(
                f"OK 窗口构建成功：{window.windowTitle()} "
                f"{window.width()}x{window.height()}，"
                f"实例数={window.project_count()}"
            )
            _check_static()
            _check_button_styles()
            _check_row_badges()
            _check_dialogs(cli, settings)
            _check_wheel_guard()
            _check_missing_yml(cli, settings)
            return 0

        if smoke:
            # 给事件循环一点时间跑完初始化与首次轮询，然后自动退出
            QTimer.singleShot(2500, app.quit)

        return app.exec()
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
