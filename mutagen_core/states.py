"""GUI 会话状态机（需求 3.6）。

把 Mutagen 十几个原始 ``status`` 归并成**行为上可区分**的少数几个状态——
因为「按钮能不能点」只跟**行为**有关，跟 status 的字面值无关。

为什么单独成模块
----------------
这是本 GUI 最容易出错的地方，历史上一共错过三次：

1. 把「**状态未知**」当成「**没有会话**」→ ``Start`` 成了唯一可点项，
   用户一点就报 ``project already running``；
2. 把「**出错但在自动重试**」的 ``waiting-for-rescan`` 说成「已停止」，
   会让人以为需要人工处理（其实 Mutagen 每 5 秒自己重扫）；
3. 补「状态未知时禁用全部按钮」时漏了重新启用 ``Monitor`` → 它永久变灰。

所以把**状态定义**与**按钮策略**集中到这一个文件，做成一张显式的表，
并让 ``app.py --check`` 与 ``selftest`` 直接对这张表断言。
任何「某个按钮该不该亮」的问题，都应该在这张表里找到答案。
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

__all__ = [
    "GuiState",
    "ALL_OPERATIONS",
    "RETRYING_STATUSES",
    "HALTED_STATUSES",
    "OPERATIONS",
    "LOCK_SUFFIX",
    "classify",
    "enabled_operations",
    "lock_path",
    "has_stale_lock",
]


class GuiState(str, Enum):
    """GUI 状态机的状态。与 Mutagen 原始 ``status`` 的关系见 :func:`classify`。"""

    UNKNOWN = "unknown"
    """还没读到会话状态——**不知道**会话存不存在。

    与 :attr:`ABSENT` 的区别至关重要：这里**不能**假定「没有会话」。
    """

    ABSENT = "absent"
    """读到了：会话不存在（未启动）。"""

    CONNECTING = "connecting"
    """会话存在，但正在连接某一端。

    含两种情况，从行为上看完全一样（都**不需要人工干预**）：
    刚创建后的首次连接、以及掉线后的自动重连。
    Mutagen 会在后台持续重试，连上后自动继续同步。
    """

    SYNCING = "syncing"
    """会话在正常工作。

    涵盖 ``watching`` / ``scanning`` / ``reconciling`` / ``staging-*`` /
    ``transitioning`` / ``waiting-for-rescan``——它们在**按钮意义上**没有区别。
    """

    PAUSED = "paused"
    """用户主动暂停。出口是 ``Resume``。"""

    HALTED = "halted"
    """出错**已停止**，需要人工干预。出口是 ``Resume``（或 Stop 后重建）。"""

    DISCONNECTED = "disconnected"
    """已断开（``status == "disconnected"``），不在自动重试。"""


#: Mutagen **会自动重试**的错误状态。
#:
#: 实测（Mutagen 0.18.1，``maxEntryCount`` 超限触发）::
#:
#:     status    = "waiting-for-rescan"
#:     lastError = "alpha scan error: exceeded allowed entry count"
#:     paused    = false
#:     人类可读   = "Status: Waiting 5 seconds for rescan"
#:
#: ——**它并没有停**，Mutagen 每 5 秒重新扫描一次，不需要人工做任何事。
RETRYING_STATUSES = frozenset({"waiting-for-rescan"})

#: 真正停止、需要人工介入的状态。
#:
#: .. note::
#:    ``halted-on-error`` **尚未实测触发过**（``maxEntryCount`` 超限只会进
#:    ``waiting-for-rescan``）。此处按 Mutagen 语义定义：会话已停止，
#:    用 ``resume`` 重新启动它；这也是本状态**唯一**的恢复出口。
HALTED_STATUSES = frozenset({"halted-on-error"})


#: 所有操作按钮名（与 ``ui/op_dialog.py`` 里的按钮集合一致）。
ALL_OPERATIONS = frozenset(
    {"start", "stop", "pause", "resume", "flush", "restart", "monitor", "list"}
)


#: ⭐ **GUI 状态 -> 可点的操作按钮。这是按钮可用性的唯一定义处。**
#:
#: 判据只有三类，别混淆：
#:
#: 1. **会话是否存在** —— ``Start`` 只在不存在时可用；
#:    ``Stop`` / ``Restart`` / ``Monitor`` 只在存在时可用
#: 2. **当下做有没有意义** —— ``Flush``（连接中「刷新」没有意义）；
#:    ``Pause`` / ``Resume``（取决于会话是正常、已暂停还是已停止）
#: 3. **永远可点** —— ``List``（只读诊断，出任何问题都能用它看现场）
#:
#: ⚠️ 三条最容易搞错的规则：
#:
#: * :attr:`GuiState.CONNECTING` 时 ``Start`` **必须灰着**——会话并没有消失，
#:   再点 Start 只会报 ``already running``；掉线也不需要任何人工操作
#: * :attr:`GuiState.HALTED` 时 ``Pause`` 无意义（已经停了），出口是 ``Resume``
#: * :attr:`GuiState.UNKNOWN` 时只留 ``List``——**绝不把「不知道」当成「可以 Start」**
OPERATIONS: dict[GuiState, frozenset[str]] = {
    GuiState.UNKNOWN: frozenset({"list"}),
    GuiState.ABSENT: frozenset({"start", "list"}),
    GuiState.CONNECTING: frozenset({"stop", "restart", "monitor", "pause", "list"}),
    GuiState.SYNCING: frozenset(
        {"stop", "restart", "monitor", "pause", "flush", "list"}
    ),
    GuiState.PAUSED: frozenset({"stop", "restart", "monitor", "resume", "list"}),
    GuiState.HALTED: frozenset({"stop", "restart", "monitor", "resume", "list"}),
    GuiState.DISCONNECTED: frozenset({"stop", "restart", "monitor", "resume", "list"}),
}


def classify(
    *,
    loaded: bool,
    exists: bool,
    status: str = "",
    paused: bool = False,
) -> GuiState:
    """把「原始状态」映射到状态机状态——**唯一的映射实现**。

    :param loaded: 是否已经**成功读到过**一次会话状态。
        注意这与「会话不存在」是两件不同的事。
    :param exists: 读到的结果里，这个会话是否存在。
    :param status: Mutagen 原始 status 字符串，例如 ``watching``。
    :param paused: 原始 ``paused`` 字段。
    """
    if not loaded:
        return GuiState.UNKNOWN
    if not exists:
        return GuiState.ABSENT
    # 出错优先于其他判断：halted 比 paused 更需要用户注意
    if status in HALTED_STATUSES:
        return GuiState.HALTED
    if paused or status == "paused":
        return GuiState.PAUSED
    if status == "disconnected":
        return GuiState.DISCONNECTED
    if status.startswith("connecting"):
        return GuiState.CONNECTING
    return GuiState.SYNCING


def enabled_operations(state: GuiState) -> frozenset[str]:
    """该状态下可点的操作按钮集合。"""
    return OPERATIONS[state]


# --------------------------------------------------------------------------- #
# 残留锁文件：识别「project already running」卡死
# --------------------------------------------------------------------------- #

#: Mutagen 放在项目文件旁边的锁文件后缀。
#:
#: 实测（0.18.1）：``project start`` 靠它判断「项目是否已在运行」，
#: 而它**只有 ``project terminate`` 才会清理**。
LOCK_SUFFIX = ".lock"


def lock_path(yml_path: str | Path) -> Path:
    """Mutagen 为这个项目文件创建的锁文件路径。"""
    return Path(str(yml_path) + LOCK_SUFFIX)


def has_stale_lock(yml_path: str | Path, *, session_exists: bool) -> bool:
    """是否为**残留锁文件**。

    这是识别下面这个坑的**唯一依据**——实测（Mutagen 0.18.1）::

        $ mutagen project start -f <yml>
        Error: project already running        # 说在运行
        $ mutagen sync list
        No synchronization sessions found      # 却一个会话都没有
        $ dir <yml 所在目录>
        <yml>.lock                             # ★ 残留的锁
        $ mutagen daemon stop; mutagen project start -f <yml>
        Error: project already running         # 重启 daemon 也没用

    **触发条件比想象中普遍得多**。锁只有 ``project terminate`` 会清理，所以：

    * 同步运行时**关机 / 重启电脑** ← 最常见（实测模拟关机后锁依然在）
    * daemon 异常退出
    * 用 ``sync terminate`` 绕过项目终止会话

    任意一种都会留下残留锁，让 ``project start`` **永久失败**，
    而界面上看起来只是「点了 Start 没反应」。

    :param session_exists: 这个项目的会话当前是否存在。
        有会话说明锁是**正常**的（项目确实在跑），不算残留。
    """
    if session_exists:
        return False
    return lock_path(yml_path).exists()
