"""后台任务：避免把界面卡在 mutagen 调用上。

Mutagen 每次调用都要几十到几百毫秒（远程时更久），
如果直接在主线程跑，每 5 秒的轮询就会让界面卡顿一次。

所以统一用 :class:`QRunnable` 丢进 ``QThreadPool``，通过信号回主线程更新 UI。

.. warning::
   **必须用 :func:`submit` 启动任务，不要直接调 ``QThreadPool.start()``。**

   ``QThreadPool`` 只持有 C++ 指针，Python 侧若没有任何引用，
   运行中的任务会被 GC 回收，随后 emit 就会抛
   ``RuntimeError: Signal source has been deleted``（实测踩过）。
   :func:`submit` 会把任务挂在调用方对象上，直到它结束才释放。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from mutagen_core.cli import MutagenCLI, Result
from mutagen_core.parser import parse_sync_list_json

_PENDING_ATTR = "_pending_tasks"


def submit(
    task: QRunnable,
    owner: object,
    on_finished,
    on_failed=None,
) -> None:
    """启动后台任务，并把它的引用挂在 ``owner`` 上直到结束。

    :param task: 待运行的 :class:`QRunnable`（要求带 ``signals.finished``）
    :param owner: 调用方对象（窗口 / 对话框），任务引用挂在它上面
    :param on_finished: ``signals.finished`` 的槽
    :param on_failed: ``signals.failed`` 的槽（任务没有该信号时忽略）
    """
    pending: set = getattr(owner, _PENDING_ATTR, None)
    if pending is None:
        pending = set()
        setattr(owner, _PENDING_ATTR, pending)

    pending.add(task)
    task.signals.finished.connect(on_finished)
    task.signals.finished.connect(lambda *_: pending.discard(task))

    if on_failed is not None and hasattr(task.signals, "failed"):
        task.signals.failed.connect(on_failed)
        task.signals.failed.connect(lambda *_: pending.discard(task))

    task.setAutoDelete(False)  # 所有权交给 Python，避免 C++ 侧提前析构
    QThreadPool.globalInstance().start(task)


def _emit(signals: QObject, name: str, *args: object) -> None:
    """安全发射信号：接收方可能已经被销毁。"""
    try:
        getattr(signals, name).emit(*args)
    except RuntimeError:
        # 窗口/对话框已关闭，静默忽略
        pass


class _PollSignals(QObject):
    finished = Signal(object)   # list[SessionState]
    failed = Signal(str)


class PollTask(QRunnable):
    """在后台拉取一次全局会话状态。"""

    def __init__(self, cli: MutagenCLI) -> None:
        super().__init__()
        self.cli = cli
        self.signals = _PollSignals()

    def run(self) -> None:  # noqa: D102 - QRunnable 接口
        try:
            result = self.cli.sync_list_json()
        except Exception as exc:  # noqa: BLE001 - 后台线程不能抛出去
            _emit(self.signals, "failed", str(exc))
            return

        if result.ok:
            _emit(self.signals, "finished", parse_sync_list_json(result.output))
        else:
            _emit(self.signals, "failed", result.error_message or "无法读取会话状态")


class _CommandSignals(QObject):
    finished = Signal(object)   # Result


class CommandTask(QRunnable):
    """在后台执行一条 mutagen 命令。

    ``label`` 只用于日志展示，方便用户看清「这条输出是哪条命令产生的」。
    """

    def __init__(
        self,
        cli: MutagenCLI,
        args: list[str],
        label: str = "",
        timeout: Optional[int] = None,
    ) -> None:
        super().__init__()
        self.cli = cli
        self.args = args
        self.label = label
        self.timeout = timeout
        self.signals = _CommandSignals()

    def run(self) -> None:  # noqa: D102 - QRunnable 接口
        try:
            result = self.cli.run(self.args, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001 - 后台线程不能抛出去
            result = Result(
                ok=False,
                args=tuple(self.args),
                stderr=str(exc),
                exit_code=-1,
            )
        _emit(self.signals, "finished", result)


__all__ = ["submit", "PollTask", "CommandTask"]
