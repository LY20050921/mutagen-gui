"""数据模型：实例（Project）与运行态（SessionState）。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .states import HALTED_STATUSES, RETRYING_STATUSES, GuiState, classify


def now_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id() -> str:
    """生成实例的本地唯一 ID。"""
    return uuid.uuid4().hex


# --------------------------------------------------------------------------- #
# 实例（注册表里的一条记录）
# --------------------------------------------------------------------------- #


@dataclass
class Project:
    """一个实例 = 一个 yml 文件。

    表示「最近打开过的某个 yml」。因为 yml 才是唯一事实来源，
    注册表本质上只是「最近打开的 yml 列表 + 展示用的缓存字段」。
    """

    id: str = field(default_factory=new_id)
    name: str = ""
    """显示名；同时用作 yml 里的**会话名**（受 SESSION_NAME_PATTERN 约束）。"""

    yml_path: str = ""
    """yml 文件的绝对路径。"""

    alpha: str = ""
    """本地端点，派生自 yml 内容（也是「选文件夹导入」的匹配依据）。"""

    beta: str = ""
    """远程端点，派生自 yml 内容。"""

    session_count: int = 1
    """yml 内 `sync` 下的会话数量。

    方案 A（见需求附录 D.2）：v1 只支持 = 1 的情况；
    >= 2 时实例标记为**只读**（不可在 GUI 里编辑）。
    """

    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    last_session_id: str = ""
    """上次已知的 Mutagen 会话 ID，用于把运行态关联回实例。"""

    # -- 便捷属性 ---------------------------------------------------------- #

    @property
    def is_editable(self) -> bool:
        """是否允许在 GUI 中编辑（多会话 yml 只读）。"""
        return self.session_count == 1

    def touch(self) -> None:
        self.updated_at = now_iso()

    # -- 序列化 ------------------------------------------------------------ #

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "yml_path": self.yml_path,
            "alpha": self.alpha,
            "beta": self.beta,
            "session_count": self.session_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_session_id": self.last_session_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


# --------------------------------------------------------------------------- #
# 运行态（来自 `mutagen sync list --template '{{json .}}'`）
# --------------------------------------------------------------------------- #


@dataclass
class EndpointState:
    """单侧端点（alpha 或 beta）的运行态。"""

    url: str = ""
    protocol: str = ""
    path: str = ""
    connected: bool = False
    scanned: bool = False
    directories: int = 0
    files: int = 0
    symbolic_links: int = 0
    total_size: int = 0

    @classmethod
    def from_json(cls, data: Optional[dict[str, Any]]) -> "EndpointState":
        if not data:
            return cls()
        contents = data.get("synchronizableContents") or {}
        return cls(
            url=data.get("url", "") or "",
            protocol=data.get("protocol", "") or "",
            path=data.get("path", "") or "",
            connected=bool(data.get("connected", False)),
            scanned=bool(data.get("scanned", False)),
            directories=int(data.get("directories", 0) or 0),
            files=int(data.get("files", 0) or 0),
            symbolic_links=int(data.get("symbolicLinks", 0) or 0),
            total_size=int(contents.get("totalSize", 0) or 0)
            if isinstance(contents, dict) else 0,
        )


#: Mutagen 的 status 取值 -> 中文显示。
#:
#: 实测确认的取值：``watching``（同步中）、``connecting-beta``（重连中）。
#: ``connecting-*`` 不在这里映射——因为它要区分「首次连接」和「掉线重连」，
#: 由 :attr:`SessionState.display_status` 单独处理。
STATUS_LABELS: dict[str, str] = {
    "watching": "同步中",
    "scanning": "扫描中",
    "reconciling": "同步中",
    "staging-alpha": "暂存中（本地）",
    "staging-beta": "暂存中（远程）",
    "transitioning": "状态切换中",
    "saving": "保存状态中",
    "paused": "已暂停",
    "disconnected": "已断开",
    # 出错**但在自动重试**：Mutagen 每 5 秒重扫，不需要人工干预。
    # 实测：maxEntryCount 超限时就是它。
    "waiting-for-rescan": "出错，自动重试中…",
    "halted-on-error": "出错已停止",
}

#: 状态等级 -> 语义说明（供 UI 决定颜色与提示文案）。
STATUS_LEVELS: dict[str, str] = {
    "running": "正常同步",
    "connecting": "正在连接（Mutagen 会自动重连）",
    "paused": "已暂停",
    # 有错但在自愈：**不是**错误级，用警告色即可，免得把人吓去瞎折腾
    "warning": "有错，Mutagen 正在自动重试",
    "disconnected": "已断开",
    "error": "出错已停止（需要人工处理）",
    "stopped": "未启动",
}


@dataclass
class SessionState:
    """一个 Mutagen 同步会话的当前状态。"""

    name: str = ""
    identifier: str = ""
    status: str = ""
    """Mutagen 原始 status 字符串，例如 ``watching``。"""

    paused: bool = False
    mode: str = ""
    alpha: EndpointState = field(default_factory=EndpointState)
    beta: EndpointState = field(default_factory=EndpointState)
    successful_cycles: int = 0
    conflicts: int = 0
    problems: list[str] = field(default_factory=list)
    last_error: str = ""
    creating_version: str = ""

    # -- 便捷属性 ---------------------------------------------------------- #

    @property
    def is_paused(self) -> bool:
        return self.paused or self.status == "paused"

    @property
    def is_connecting(self) -> bool:
        """是否处于「正在连接某一端」的状态。

        Mutagen 在**会话已创建之后**连不上远端时，会把状态置为 ``connecting-alpha`` /
        ``connecting-beta``，并在后台**持续重试**——这是它自带的重连机制，无需人工干预。

        但注意：**创建会话时**是另一回事。``mutagen sync create`` / ``project start``
        必须连上两端才能建立会话，连不上就直接失败、不会留下任何会话。
        """
        return self.status.startswith("connecting")

    @property
    def connecting_side(self) -> str:
        """``connecting-alpha`` / ``connecting-beta`` -> ``本地`` / ``远程``；否则空串。"""
        if not self.is_connecting:
            return ""
        return "远程" if self.status.endswith("beta") else "本地"

    @property
    def is_halted(self) -> bool:
        """是否**真的停了**、需要人工干预（``halted-on-error``）。

        与 :attr:`is_retrying` 的区别是本 GUI 最容易搞错的一处：
        **「有错误」不等于「已停止」**。
        """
        return self.status in HALTED_STATUSES

    @property
    def is_retrying(self) -> bool:
        """是否有错误、但 Mutagen **仍在自动重试**（``waiting-for-rescan``）。

        实测（Mutagen 0.18.1）：``maxEntryCount`` 超限时就是这个状态，
        Mutagen 每 5 秒重新扫描一次，**不需要人工做任何事**。
        """
        return self.status in RETRYING_STATUSES

    @property
    def gui_state(self) -> GuiState:
        """本会话在 GUI 状态机里的状态（需求 3.6）。

        按钮可用性只由它决定，见 :data:`mutagen_core.states.OPERATIONS`。
        """
        return classify(
            loaded=True, exists=True, status=self.status, paused=self.is_paused
        )

    @property
    def has_synced(self) -> bool:
        """是否成功同步过（用来区分「首次连接」与「掉线重连」）。"""
        return self.successful_cycles > 0

    @property
    def is_running(self) -> bool:
        """会话是否处于**活动**状态（已启动、未暂停、未停止）。

        连接中也算活动——会话确实在跑，只是暂时连不上远端。
        """
        if self.is_paused:
            return False
        return self.status not in ("", "disconnected", "halted-on-error")

    @property
    def is_syncing(self) -> bool:
        """是否正在**实际同步**（连接中的不算）。"""
        return (
            self.is_running
            and not self.is_connecting
            and self.alpha.connected
            and self.beta.connected
        )

    @property
    def is_healthy(self) -> bool:
        return self.is_syncing and not self.problems and not self.last_error

    @property
    def display_status(self) -> str:
        """用于列表徽章的中文状态。"""
        if self.is_paused:
            return STATUS_LABELS["paused"]
        if self.is_halted:
            return STATUS_LABELS["halted-on-error"]

        # ⚠️ 「有错误」≠「已停止」：waiting-for-rescan 这类状态 Mutagen
        # 每 5 秒会自己重试，说成「已停止」会误导用户去人工处理（实测踩过）。
        if self.is_retrying or (self.last_error and not self.is_connecting):
            return STATUS_LABELS["waiting-for-rescan"]

        # ⚠️ 「正在连接」必须排在「已断开」之前判断。
        # 远端掉线时状态是 connecting-*，那其实是 Mutagen 在**自动重连**；
        # 若按「某一端未连接」直接判成「已断开」，会让人误以为需要人工介入。
        if self.is_connecting:
            action = "重连" if self.has_synced else "连接"
            return f"正在{action}{self.connecting_side}…"

        if self.status == "disconnected":
            return STATUS_LABELS["disconnected"]
        if self.status and not (self.alpha.connected and self.beta.connected):
            return STATUS_LABELS["disconnected"]
        if not self.status:
            return "未启动"
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def status_level(self) -> str:
        """语义化状态等级，供 UI 决定颜色与提示文案。

        取值：``running`` / ``connecting`` / ``paused`` / ``disconnected`` /
        ``error`` / ``stopped``
        """
        if self.is_paused:
            return "paused"
        if self.is_halted:
            return "error"
        # 有错但在自愈：warning 而不是 error，避免把人吓去瞎折腾
        if self.is_retrying or (self.last_error and not self.is_connecting):
            return "warning"
        if self.is_connecting:
            return "connecting"
        if self.status == "disconnected":
            return "disconnected"
        if self.status and not (self.alpha.connected and self.beta.connected):
            return "disconnected"
        if not self.status:
            return "stopped"
        return "running"

    # -- 序列化 ------------------------------------------------------------ #

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "SessionState":
        return cls(
            name=data.get("name", "") or "",
            identifier=data.get("identifier", "") or "",
            status=data.get("status", "") or "",
            paused=bool(data.get("paused", False)),
            mode=data.get("mode", "") or "",
            alpha=EndpointState.from_json(data.get("alpha")),
            beta=EndpointState.from_json(data.get("beta")),
            successful_cycles=int(data.get("successfulCycles", 0) or 0),
            creating_version=data.get("creatingVersion", "") or "",
            conflicts=_count_conflicts(data),
            problems=_collect_problems(data),
            last_error=data.get("lastError", "") or "",
        )


def _count_conflicts(data: dict[str, Any]) -> int:
    """从会话 JSON 里尽力统计冲突数量。

    实测发现：无冲突时该字段**不出现在** ``--template '{{json .}}'`` 的输出里，
    所以这里对多种可能的形态都做兼容，取不到就返回 0。
    """
    conflicts = data.get("conflicts")
    if isinstance(conflicts, list):
        return len(conflicts)
    if isinstance(conflicts, int):
        return conflicts
    for key in ("alpha", "beta"):
        side = data.get(key)
        if isinstance(side, dict):
            value = side.get("conflicts")
            if isinstance(value, list) and value:
                return len(value)
    return 0


def _collect_problems(data: dict[str, Any]) -> list[str]:
    """收集 scan / transition 问题描述。"""
    problems: list[str] = []
    for key in ("problems", "scanProblems", "transitionProblems"):
        value = data.get(key)
        if isinstance(value, list):
            problems.extend(str(item) for item in value)
    return problems


__all__ = [
    "now_iso",
    "new_id",
    "Project",
    "EndpointState",
    "SessionState",
    "GuiState",
    "STATUS_LABELS",
    "STATUS_LEVELS",
]
