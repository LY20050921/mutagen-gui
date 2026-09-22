"""实例操作对话框（需求 6.4 / F2 / F12）。

普通模式下点击实例即打开这里，上面是 Mutagen 的各项操作按钮，
下面是「yml 配置」与「运行日志」两个标签页。

关于并发
--------
* 命令（start / stop / pause …）走后台线程，结果回主线程写日志
* ``sync monitor`` 是**长驻进程**，用 ``QProcess`` 流式读取，可随时停止
  （需求 F12.2：不要重复启动）
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QProcess,
    QProcessEnvironment,
    Qt,
    QTimer,
    QUrl,
)
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mutagen_core import template
from mutagen_core.cli import MutagenCLI
from mutagen_core.models import Project, SessionState
from mutagen_core.parser import parse_sync_list_json
from mutagen_core.states import ALL_OPERATIONS, GuiState, classify, enabled_operations

from . import theme
from .tasks import CommandTask, submit
from .theme import Metrics
from .yml_editor import YmlEditor

_CREATE_NO_WINDOW = 0x08000000
_MONO = ["Cascadia Mono", "Consolas", "Courier New"]

#: 判断「连不上远端」的错误特征串（大小写无关）。
#:
#: 实测报错样例::
#:
#:   unable to create synchronization session (X): unable to connect to beta:
#:   unable to connect to endpoint: unable to dial agent endpoint:
#:   unable to handshake with agent process: unable to receive server magic number:
#:   EOF (error output: ssh: connect to host xxx port 23984: Connection refused)
_CONNECTION_ERROR_HINTS = (
    "unable to connect to alpha",
    "unable to connect to beta",
    "connection refused",
    "connection timed out",
    "connection reset",
    "no route to host",
    "network is unreachable",
    "could not resolve hostname",
    "handshake with agent process",
)


def _looks_like_connection_error(message: str) -> bool:
    """错误信息是否属于「连不上远端」。"""
    lowered = (message or "").lower()
    return any(hint in lowered for hint in _CONNECTION_ERROR_HINTS)


class InstanceDialog(QDialog):
    """一个实例的操作面板。"""

    def __init__(
        self,
        cli: MutagenCLI,
        project: Project,
        state: Optional[SessionState] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.cli = cli
        self.project = project
        self._state = state
        self._state_loaded = state is not None
        """是否已经**拿到过**可信的会话状态。

        在拿到之前不能把 ``_state is None`` 当成「没有会话」——那会让按钮误判成
        「可以 Start」，用户一点就报 ``project already running``（真实踩过）。
        这段时间一律显示「读取中…」且只保留 List 可点。

        初值取自主窗口传入的状态：它来自主窗口的轮询，同样是可信的，
        这样主窗口已知状态时不会白闪一下「读取中…」。
        """
        self._state_error = ""
        """读取状态失败的原因；空串表示没出错。"""
        self._start_failed_already_running = False
        """Start 报了 ``already running``，等待用**刷新后的状态**判断真假。

        两种截然不同的原因都报这一句话：

        * 状态读取滞后 —— 会话其实在跑，刷新一下就好
        * **残留锁文件** —— 项目没有任何会话，但 yml 旁的 ``<yml>.lock`` 没被
          清理，``project start`` 会**永远**失败（重启 daemon 也没用）

        必须等 :meth:`_on_state_result` 拿到新鲜状态才能区分，所以这里只打标记。
        """
        self._queue: list[tuple[list[str], str, bool]] = []
        """待执行的命令队列：(参数, 标签, 是否预期失败)。"""
        self._monitor: Optional[QProcess] = None
        self._expect_resume_failure = False
        """「先建会话、等远端上线」流程里，resume 失败是**预期**的，日志要换个说法。"""

        self.setWindowTitle(project.name)
        self.resize(920, 720)

        self._build_ui()
        self._load_yml()
        self._update_header()
        self._refresh_state()

        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self._refresh_state)
        self._timer.start()

    # ------------------------------------------------------------------ UI --

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            Metrics.MARGIN, Metrics.MARGIN, Metrics.MARGIN, Metrics.MARGIN
        )
        layout.setSpacing(14)

        layout.addWidget(self._build_header())
        layout.addWidget(self._build_operations())
        layout.addWidget(self._build_file_actions())
        layout.addWidget(self._build_tabs(), 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addStretch(1)
        close_button = QPushButton("关闭")
        close_button.setObjectName("SecondaryButton")
        close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        close_button.clicked.connect(self.accept)
        footer.addWidget(close_button)
        layout.addLayout(footer)

    def _build_header(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(10)

        self._status_dot = QLabel()
        self._status_dot.setFixedWidth(14)
        self._status_dot.setStyleSheet("background: transparent;")
        title_row.addWidget(self._status_dot)

        self._status_label = QLabel("未启动")
        self._status_label.setFont(theme.font(15))
        self._status_label.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; background: transparent;"
        )
        title_row.addWidget(self._status_label)

        self._conflict_label = QLabel()
        self._conflict_label.setFont(theme.meta_font())
        self._conflict_label.setStyleSheet(
            f"color: {theme.DANGER}; background: transparent;"
        )
        title_row.addWidget(self._conflict_label)
        title_row.addStretch(1)
        layout.addLayout(title_row)

        for key, value in (
            ("Alpha", self.project.alpha),
            ("Beta", self.project.beta),
            ("yml", self.project.yml_path),
        ):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)

            label = QLabel(key)
            label.setFont(theme.field_label_font())
            label.setFixedWidth(52)
            label.setStyleSheet(f"color: {theme.TEXT_LABEL}; background: transparent;")
            row.addWidget(label)

            content = QLabel(value or "—")
            content.setFont(theme.meta_font())
            content.setStyleSheet(
                f"color: {theme.TEXT_SECONDARY}; background: transparent;"
            )
            content.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            content.setToolTip(value)
            row.addWidget(content, 1)
            layout.addLayout(row)

        return panel

    def _button(self, text: str, callback, *, primary: bool = False, danger: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(
            "PrimaryButton" if primary else ("DangerButton" if danger else "SecondaryButton")
        )
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(callback)
        return button

    def _build_operations(self) -> QWidget:
        group = QGroupBox("Mutagen 操作")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setSpacing(8)

        self._ops: dict[str, QPushButton] = {}

        def add(name: str, text: str, callback, **kwargs) -> None:
            button = self._button(text, callback, **kwargs)
            self._ops[name] = button
            layout.addWidget(button)

        add("start", "Start", self._on_start, primary=True)
        add("stop", "Stop", self._on_stop, danger=True)
        add("pause", "Pause", self._on_pause)
        add("resume", "Resume", self._on_resume)
        add("flush", "Flush", self._on_flush)
        add("restart", "Restart", self._on_restart)
        add("monitor", "Monitor", self._on_toggle_monitor)

        # 对应 `mutagen project list`：把会话详情打到日志面板。
        # 它是只读诊断命令，所以即使状态还没读回来也保持可点。
        list_button = self._button("List", self._on_list)
        list_button.setToolTip(
            "列出这个项目的会话详情\n"
            "等价于 mutagen project list -f <yml>"
        )
        self._ops["list"] = list_button
        layout.addWidget(list_button)

        layout.addStretch(1)

        return group

    def _build_file_actions(self) -> QWidget:
        group = QGroupBox("yml / 文件")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setSpacing(8)

        layout.addWidget(self._button("编辑 yml", self._on_edit_yml))
        layout.addWidget(self._button("打开 yml 目录", self._on_open_folder))
        layout.addWidget(self._button("复制本地路径", self._on_copy_alpha))
        layout.addWidget(self._button("复制远程端点", self._on_copy_beta))
        layout.addStretch(1)
        return group

    def _build_tabs(self) -> QWidget:
        tabs = QTabWidget()

        self._editor = YmlEditor()
        self._editor.save_requested.connect(lambda text: self._save_yml(text, restart=False))
        self._editor.save_and_restart_requested.connect(
            lambda text: self._save_yml(text, restart=True)
        )
        self._editor.set_read_only(not self.project.is_editable)
        tabs.addTab(self._editor, "yml 配置")

        self._log = QPlainTextEdit()
        font = QFont()
        font.setFamilies(_MONO)
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPixelSize(12)
        self._log.setFont(font)
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(5000)
        tabs.addTab(self._log, "运行日志")

        self._tabs = tabs
        return tabs

    # -------------------------------------------------------------- 数据 --

    def _load_yml(self) -> None:
        path = Path(self.project.yml_path)
        if not path.exists():
            self._editor.set_text(f"# yml 文件不存在：{path}\n# 可用「编辑 yml」重建配置后保存")
            return
        try:
            self._editor.set_text(path.read_text(encoding="utf-8"))
        except OSError as exc:
            self._editor.set_text(f"# 无法读取 yml：{exc}")

    def _update_header(self) -> None:
        """按状态机刷新标题区与按钮可用性（需求 3.6）。

        **唯一**入口：所有「界面该怎么显示、哪些按钮能点」的判断都收敛在这里，
        而判断依据全部来自 :func:`mutagen_core.states.classify` 与
        :data:`mutagen_core.states.OPERATIONS`——不再有散落各处的 if。
        """
        state = self._state

        # 判定状态机状态：把「未知 / 无会话 / 原始 status」统一成一个枚举
        gui_state = classify(
            loaded=self._state_loaded,
            exists=state is not None,
            status=state.status if state else "",
            paused=state.is_paused if state else False,
        )

        if gui_state is GuiState.UNKNOWN:
            text, level = "读取中…", "stopped"
        elif gui_state is GuiState.ABSENT:
            text, level = "未启动", "stopped"
        else:
            text, level = state.display_status, state.status_level

        from . import icons

        self._status_dot.setPixmap(icons.status_dot(theme.status_color(level), 11))
        self._status_label.setText(text)

        # —— 状态旁的说明文字 ——
        # 顺序即优先级：从「最需要用户知道」到「只是想告诉他没事」。
        if self._state_error:
            self._set_note("读取状态失败", theme.DANGER, tooltip=self._state_error)
        elif gui_state is GuiState.UNKNOWN:
            self._set_note("正在读取会话状态…", theme.TEXT_SECONDARY)
        elif gui_state is GuiState.HALTED:
            # 必须排在下面的 last_error 分支**之前**：
            # halted 的 last_error 也有值，但它**不会**自愈，不能显示成「正在重试」。
            self._set_note(
                "出错已停止，需要人工处理",
                theme.DANGER,
                tooltip=(state.last_error if state else "")
                + "\n\n点 Resume 可重新启动这个会话。",
            )
        elif state and state.conflicts:
            self._set_note(f"冲突 {state.conflicts}", theme.DANGER)
        elif gui_state is GuiState.CONNECTING:
            self._set_note(
                "Mutagen 会自动重连，无需操作",
                theme.WARNING,
                tooltip=(
                    "会话已创建，远端掉线时 daemon 会在后台持续重试。\n"
                    "远端恢复后会自动连上并继续同步，不需要人工干预。"
                ),
            )
        elif state and state.last_error:
            # 有错但在自愈（waiting-for-rescan）—— 重点是告诉用户「不用你管」
            self._set_note(
                "出错，Mutagen 正在自动重试",
                theme.WARNING,
                tooltip=state.last_error,
            )
        elif gui_state is GuiState.DISCONNECTED:
            self._set_note("已断开", theme.DANGER)
        else:
            self._set_note("")

        # —— 按钮可用性：完全由状态机表决定 ——
        #
        # ⭐ 刻意写成「按表统一赋值」而非逐个按钮写 if：
        #    以前是逐行 setEnabled，**漏写一行那个按钮就永久变灰**
        #    （Monitor 实际中过一次招）。现在策略只有一张表
        #    （mutagen_core.states.OPERATIONS），要改只改那一处。
        allowed = enabled_operations(gui_state)
        for name, button in self._ops.items():
            button.setEnabled(name in allowed)

        # 防漂移：界面按钮集合必须与状态机定义完全一致，
        # 否则新加的按钮会因为表里没有而永远是灰的。
        drift = set(self._ops) ^ set(ALL_OPERATIONS)
        assert not drift, f"界面按钮与状态机定义不一致：{sorted(drift)}"

    def enabled_operations(self) -> list[str]:
        """当前可点的操作按钮名（供自检 / 冒烟测试查询）。"""
        return sorted(name for name, button in self._ops.items() if button.isEnabled())

    def set_state(self, state: Optional[SessionState], *, loaded: bool = True) -> None:
        """从外部同步会话状态进来，并刷新按钮可用性。

        主窗口的轮询结果、以及 ``app.py --check`` 的按钮矩阵断言都走这里，
        保证「状态 -> 按钮」的映射只有一份实现。
        """
        self._state = state
        self._state_loaded = loaded
        self._state_error = ""
        self._update_header()

    def _set_note(self, text: str, color: str = "", tooltip: str = "") -> None:
        """设置状态行右侧的说明文字。"""
        self._conflict_label.setText(text)
        self._conflict_label.setToolTip(tooltip)
        self._conflict_label.setStyleSheet(
            f"color: {color or theme.TEXT_SECONDARY}; background: transparent;"
        )

    def _append_log(self, text: str) -> None:
        if not text.strip():
            return
        self._log.appendPlainText(text.rstrip())
        scrollbar = self._log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # ---------------------------------------------------------- 命令执行 --

    def _run_command(
        self, args: list[str], label: str, *, expect_failure: bool = False
    ) -> None:
        """执行一条命令。

        :param expect_failure: 预期会失败（例如远端还没上线时的 ``resume``）。
            这样日志里会给出友好解释，而不是一行刺眼的「[命令失败]」。
        """
        self._expect_resume_failure = expect_failure
        self._append_log(f"$ {label}")
        submit(CommandTask(self.cli, args, label), self, self._on_command_finished)

    def _on_command_finished(self, result) -> None:  # noqa: ANN001 - Result
        self._append_log(result.describe())
        expect_failure = self._expect_resume_failure
        self._expect_resume_failure = False

        if self._queue and result.ok:
            args, label, expect = self._queue.pop(0)
            self._run_command(args, label, expect_failure=expect)
            return

        self._queue.clear()

        if not result.ok:
            args = list(result.args)
            message = result.error_message

            if expect_failure:
                # 这一步失败是**预期**的（远端还没上线），换个说法解释
                self._append_log(
                    "远端目前还没上线——这一步失败是预期内的。\n"
                    "  · 会话已进入「正在连接」状态，Mutagen 会在后台持续重试\n"
                    "  · 远端一恢复就会自动连上并开始同步，**你不需要再做任何操作**\n"
                    "  · 想放弃等待，点 Stop 终止会话即可"
                )
            else:
                lowered = message.lower()
                # 需求 F12.4：原始报错必须原样展示
                self._append_log(f"[命令失败] {message}")

                if "start" in args and "already running" in lowered:
                    # ⚠️ 这一句报错有**两种完全不同的原因**，不能一上来就断言「在运行」：
                    #   ① 状态读取滞后：会话其实在跑
                    #   ② 残留锁文件：项目**一个会话都没有**，但 yml 旁的
                    #      ``<yml>.lock`` 没被清理 → ``project start`` 永远失败
                    #      （实测：重启 daemon 也无效，必须清锁文件）
                    # 到底哪种，得用**刷新后**的状态判断，所以这里只打标记。
                    self._start_failed_already_running = True
                    self._append_log(
                        "说明：Mutagen 说「已在运行」，但真假待确认：\n"
                        "      · 真有会话在跑 → 只是状态读取滞后，稍等会刷新\n"
                        "      · 一个会话都没有 → 是残留的锁文件没清理，需要处理\n"
                        "      正在重读状态以判断…"
                    )
                    self._state_loaded = False  # 回到「读取中」，不再展示旧状态
                elif "start" in args and _looks_like_connection_error(message):
                    self._append_log(
                        "说明：Mutagen 需要连上两端才能**建立**会话，"
                        "所以这次 Start 没有留下任何东西。\n"
                        "      远端一开机，直接再点一次 Start 就行。"
                    )
                    self._offer_remote_offline()

        self._refresh_state()

    def _run_sequence(self, steps: list[tuple]) -> None:
        """顺序执行多条命令。

        每一步是 ``(args, label)`` 或 ``(args, label, expect_failure)``。
        任一步失败就停止后续步骤。
        """
        self._queue = [
            (step[0], step[1], bool(step[2]) if len(step) > 2 else False)
            for step in steps
        ]
        if self._queue:
            args, label, expect = self._queue.pop(0)
            self._run_command(args, label, expect_failure=expect)

    def _offer_remote_offline(self) -> None:
        """Start 因远端不可达失败时，引导用户改用「先建会话，等远端上线自动开始」。

        依据实测：``sync create`` / ``project start`` **必须连上两端**才会建立会话，
        连不上就直接失败、什么都不留下；而**已创建**的会话在掉线时会被 daemon
        自动重连（状态 ``connecting-*``）。

        所以「远端还没开机」的场景，正确姿势是 ``start --paused`` + ``resume``：
        会话建好后即使一时连不上，也会一直重试到远端上线。
        """
        box = QMessageBox(self)
        box.setWindowTitle("远端服务器不可达")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("会话没能创建：Mutagen 必须连上两端才能建立会话。")
        box.setInformativeText(
            "所以这次 Start 不会留下任何东西——远端开机后直接再点 Start 即可。\n\n"
            "如果不想手动等，也可以先把会话建好：建好之后 Mutagen 会自动重连，"
            "远端一上线就自己开始同步。"
        )
        wait_button = box.addButton(
            "建好会话，等远端上线自动开始", QMessageBox.ButtonRole.ActionRole
        )
        box.addButton("知道了", QMessageBox.ButtonRole.RejectRole)
        box.exec()

        if box.clickedButton() is not wait_button:
            return

        # 注意：resume 这一步**必然失败**（远端还没通），所以标记为「预期失败」。
        # 但它失败之后会话会停留在 connecting-*，由 daemon 持续重连——
        # 这正是我们想要的效果。
        self._run_sequence([
            (
                ["project", "start", "--paused", "-f", self.project.yml_path],
                "以暂停状态创建会话（不尝试连接）",
            ),
            (
                ["sync", "resume", self.project.name],
                "开始连接（远端还没上线时这一步会失败，属正常）",
                True,
            ),
        ])

    # ------------------------------------------------------------ 按钮 --

    def _on_start(self) -> None:
        self._run_command(
            ["project", "start", "-f", self.project.yml_path], "启动会话"
        )

    def _on_stop(self) -> None:
        self._run_command(
            ["project", "terminate", "-f", self.project.yml_path], "停止会话"
        )

    def _on_pause(self) -> None:
        self._run_command(["sync", "pause", self.project.name], "暂停")

    def _on_resume(self) -> None:
        self._run_command(["sync", "resume", self.project.name], "恢复")

    def _on_flush(self) -> None:
        self._run_command(["sync", "flush", self.project.name], "立即同步一次")

    def _on_restart(self) -> None:
        self._run_sequence([
            (["project", "terminate", "-f", self.project.yml_path], "停止会话（重启第 1 步）"),
            (["project", "start", "-f", self.project.yml_path], "启动会话（重启第 2 步）"),
        ])

    def _on_edit_yml(self) -> None:
        if not self.project.is_editable:
            self._append_log("该 yml 包含多个会话，v1 不支持图形化编辑")
            return
        self._tabs.setCurrentWidget(self._editor)
        self._editor.set_edit_mode(True)

    def _on_open_folder(self) -> None:
        folder = Path(self.project.yml_path).parent
        if folder.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _on_copy_alpha(self) -> None:
        QGuiApplication.clipboard().setText(self.project.alpha)
        self._append_log(f"已复制本地路径：{self.project.alpha}")

    def _on_copy_beta(self) -> None:
        QGuiApplication.clipboard().setText(self.project.beta)
        self._append_log(f"已复制远程端点：{self.project.beta}")

    # -------------------------------------------------------- 监视进程 --

    def _on_toggle_monitor(self) -> None:
        if self._monitor is not None:
            self._stop_monitor()
            return

        self._tabs.setCurrentWidget(self._log)
        self._append_log(f"$ 开始监视会话 {self.project.name}（点 Monitor 可停止）")

        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.setProcessEnvironment(QProcessEnvironment.systemEnvironment())

        if os.name == "nt":  # pragma: no cover - 平台分支
            try:
                def _modifier(args) -> None:  # noqa: ANN001
                    args.flags |= _CREATE_NO_WINDOW

                process.setCreateProcessArgumentsModifier(_modifier)
            except (AttributeError, TypeError):
                pass

        process.readyReadStandardOutput.connect(self._read_monitor_output)
        process.finished.connect(self._on_monitor_finished)
        process.start(str(self.cli.exe), ["sync", "monitor", self.project.name])

        self._monitor = process
        self._ops["monitor"].setText("Stop Monitor")

    def _read_monitor_output(self) -> None:
        if self._monitor is None:
            return
        raw = bytes(self._monitor.readAllStandardOutput())
        text = raw.decode("utf-8", errors="replace")
        if text.strip():
            self._append_log(text)

    def _on_monitor_finished(self, *_args: object) -> None:
        self._append_log("（监视进程已结束）")
        self._monitor = None
        if "monitor" in self._ops:
            self._ops["monitor"].setText("Monitor")

    def _stop_monitor(self) -> None:
        if self._monitor is None:
            return
        self._monitor.kill()
        self._monitor.waitForFinished(2000)
        self._monitor = None
        self._ops["monitor"].setText("Monitor")
        self._append_log("（已停止监视）")

    # ------------------------------------------------------------ 状态 --

    def _refresh_state(self) -> None:
        submit(
            CommandTask(
                self.cli, ["sync", "list", "--template", "{{json .}}"], "刷新状态"
            ),
            self,
            self._on_state_result,
        )

    def _on_state_result(self, result) -> None:  # noqa: ANN001 - Result
        if not result.ok:
            # 读不到状态必须显性化：静默保留旧状态会让人以为一切正常，
            # 而实际上按钮的可用性判断已经不可靠了。
            self._state_error = result.error_message or "无法读取会话状态"
            self._update_header()
            return

        self._state_error = ""
        self._state_loaded = True
        sessions = parse_sync_list_json(result.output)
        self._state = next((s for s in sessions if s.name == self.project.name), None)
        self._update_header()

        # Start 刚报过 already running —— 现在拿到新鲜状态，可以判真假了
        if self._start_failed_already_running:
            self._start_failed_already_running = False
            if self._state is None:
                # 报了「已在运行」却一个会话都没有 → 残留锁文件
                self._offer_stale_lock_cleanup()
            else:
                self._append_log("已确认：会话确实在运行，之前的「未启动」是状态读取滞后。")

    def _offer_stale_lock_cleanup(self) -> None:
        """Start 报 ``already running``、但实际**没有任何会话** → 处理残留锁文件。

        实测（Mutagen 0.18.1）::

            $ mutagen project start -f <yml>
            Error: project already running
            $ mutagen sync list
            No synchronization sessions found        ← 一个都没有
            $ mutagen daemon stop && mutagen project start -f <yml>
            Error: project already running            ← 重启 daemon 也没用
            $ ls <yml 所在目录>
            <yml>.lock                                ← ★ 就是它

        ``project start`` 用 yml 旁边的 ``<yml>.lock``（内容是项目标识符
        ``proj_xxx``）判断「项目是否已在运行」。若会话被**绕过项目**的方式终止
        （``sync terminate`` 而非 ``project terminate``），或 daemon 异常退出，
        锁文件就会残留，于是 ``project start`` **永久失败**——而界面上看起来
        只是"启动没反应"。

        官方清理方式：``mutagen project terminate -f <yml>``
        （即使当前没有会话，它也会成功执行并删掉锁文件）。
        """
        lock_path = Path(self.project.yml_path + ".lock")
        self._append_log(
            "判断结果：**没有**任何会话在跑 —— 是残留的锁文件在作怪。\n"
            f"      锁文件：{lock_path}\n"
            "      它本该在会话终止时被删除；会话若被绕过项目的方式终止"
            "（或 daemon 异常退出），就会留下来，\n"
            "      导致 `project start` 永远报 already running（重启 daemon 也无效）。"
        )

        box = QMessageBox(self)
        box.setWindowTitle("项目状态残留（不是真的在运行）")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("Mutagen 认为这个项目「已在运行」，但实际上一个会话都没有。")
        box.setInformativeText(
            f"这是一份**残留的锁文件**造成的：\n{lock_path}\n\n"
            "起因：会话曾被绕过项目的方式终止（或 daemon 异常退出），"
            "锁文件没被清理。\n"
            "后果：`project start` 会一直报 already running，项目彻底起不来；"
            "重启 daemon 也不管用。\n\n"
            "清理方式：mutagen project terminate -f <yml>（即使没有会话也能成功）。"
        )
        clean_only = box.addButton("仅清理锁文件", QMessageBox.ButtonRole.ActionRole)
        clean_start = box.addButton("清理并启动", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(clean_start)
        box.exec()

        clicked = box.clickedButton()
        if clicked is clean_only:
            self._cleanup_stale_lock(then_start=False)
        elif clicked is clean_start:
            self._cleanup_stale_lock(then_start=True)

    def _cleanup_stale_lock(self, *, then_start: bool) -> None:
        """用官方命令清掉残留锁文件，可选紧接着启动项目。"""
        steps: list[tuple] = [
            (["project", "terminate", "-f", self.project.yml_path], "清理残留锁文件")
        ]
        if then_start:
            steps.append(
                (["project", "start", "-f", self.project.yml_path], "启动会话")
            )
        self._run_sequence(steps)

    def _on_list(self) -> None:
        """列出这个项目的会话详情（对应 ``mutagen project list``）。"""
        self._tabs.setCurrentWidget(self._log)
        self._run_command(
            ["project", "list", "-f", self.project.yml_path], "列出会话状态"
        )

    # -------------------------------------------------------- yml 保存 --

    def _save_yml(self, text: str, *, restart: bool) -> None:
        from mutagen_core import validator

        # 1) 语法校验
        try:
            document = template.parse_yaml_text(text)
        except Exception as exc:  # noqa: BLE001 - YAMLError
            self._append_log(f"[保存失败] YAML 语法错误：{exc}")
            self._editor.set_edit_mode(True)
            return

        # 2) 会话数校验（方案 A：只支持单会话）
        count = template.count_sessions(document)
        if count != 1:
            self._append_log(
                f"[保存失败] yml 里有 {count} 个同步会话；v1 只支持单会话（方案 A）"
            )
            return

        # 3) 未知字段提示（不阻断，但要告警）
        unknown = validator.find_unknown_keys(document)
        if unknown:
            self._append_log("[提示] 存在本 GUI 不认识的字段（会原样保留在文件里）："
                             + "、".join(unknown))

        # 4) 落盘
        try:
            Path(self.project.yml_path).write_text(text, encoding="utf-8")
        except OSError as exc:
            self._append_log(f"[保存失败] 无法写入：{exc}")
            return

        self._editor.mark_saved()
        self._append_log(f"已保存 yml：{self.project.yml_path}")

        if restart:
            self._run_sequence([
                (["project", "terminate", "-f", self.project.yml_path], "重启：停止会话"),
                (["project", "start", "-f", self.project.yml_path], "重启：启动会话"),
            ])
        else:
            self._append_log("提示：配置改动需要重启会话才会生效（可用 Restart 按钮）")

    # ------------------------------------------------------------ 关闭 --

    def closeEvent(self, event) -> None:  # noqa: D102, N802
        self._timer.stop()
        if self._monitor is not None:
            self._stop_monitor()
        super().closeEvent(event)


__all__ = ["InstanceDialog"]
