"""解析 ``mutagen sync list`` 的输出。

主路径是 JSON：``mutagen sync list --template '{{json .}}'``。
下面是**实测拿到的真实结构**（Mutagen 0.18.1，单会话、本地两端）：:

    [{
      "identifier": "sync_H40TRiNB0kBgjH9NyjCWtpqsF2YTyLT5tHiK0c5AZBm",
      "version": 1,
      "creationTime": "2026-09-22T07:02:53.8126029Z",
      "creatingVersion": "0.18.1",
      "alpha": {"protocol": "local", "path": "D:\\\\temp\\\\alpha",
                "connected": true, "scanned": true, "directories": 1},
      "beta":  {...},
      "mode": "two-way-resolved",
      "ignore": {"paths": ["checkpoints", "__pycache__"], "vcs": true},
      "symlink": {"mode": "ignore"},
      "name": "mgj-probe",
      "paused": false,
      "status": "watching",
      "successfulCycles": 3
    }]

两个实测结论：

* ``status`` 是**小写枚举**（如 ``watching``），不是人类可读的 "Watching for changes"
* 无冲突时 **``conflicts`` 字段不出现**；本模块对多种形态做了兼容

没有任何会话时输出是 ``[]``。
"""

from __future__ import annotations

import json

from .models import SessionState

_EMPTY_MARKERS = (
    "no synchronization sessions found",
    "no sessions found",
)


def is_empty_output(text: str) -> bool:
    """判断输出是否表示「没有任何会话」。"""
    stripped = (text or "").strip()
    if stripped in ("", "[]", "null"):
        return True
    lowered = stripped.lower()
    return any(marker in lowered for marker in _EMPTY_MARKERS)


def parse_sync_list_json(text: str) -> list[SessionState]:
    """把 JSON 输出解析成 ``SessionState`` 列表。

    解析失败时返回空列表而不是抛异常——状态刷新是高频操作，
    不能因为一次异常输出把整个界面弄崩（单实例失败不应影响其他实例）。
    """
    stripped = (text or "").strip()
    if is_empty_output(stripped):
        return []

    # 少数情况下 mutagen 会在 JSON 前打印提示信息（如自动启动 daemon），
    # 这里截取从第一个 '[' 到最后一个 ']' 之间的内容，提高健壮性。
    start = stripped.find("[")
    end = stripped.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    payload = stripped[start:end + 1]

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []

    sessions: list[SessionState] = []
    for entry in data:
        if isinstance(entry, dict):
            sessions.append(SessionState.from_json(entry))
    return sessions


def index_by_name(sessions: list[SessionState]) -> dict[str, SessionState]:
    """按会话名建索引，便于把运行态关联回实例。"""
    return {session.name: session for session in sessions if session.name}


def index_by_identifier(sessions: list[SessionState]) -> dict[str, SessionState]:
    """按会话 ID 建索引。"""
    return {session.identifier: session for session in sessions if session.identifier}


def match_project(
    sessions_by_name: dict[str, SessionState],
    sessions_by_id: dict[str, SessionState],
    *,
    name: str = "",
    session_id: str = "",
) -> SessionState | None:
    """按「会话名优先、会话 ID 兜底」的顺序找到实例的运行态。

    名称优先是因为用户可能重建过会话（ID 会变），而名字稳定。
    """
    if name and name in sessions_by_name:
        return sessions_by_name[name]
    if session_id and session_id in sessions_by_id:
        return sessions_by_id[session_id]
    return None


def extract_status_lines(text: str) -> list[str]:
    """从 ``project list`` 的人类可读输出里提取 ``Name:`` / ``Status:`` 行。

    仅在 JSON 路径不可用时作为兜底展示。
    """
    lines: list[str] = []
    for raw in (text or "").splitlines():
        stripped = raw.strip()
        if stripped.startswith(("Name:", "Status:", "Identifier:", "Alpha:", "Beta:")):
            lines.append(stripped)
    return lines


__all__ = [
    "is_empty_output",
    "parse_sync_list_json",
    "index_by_name",
    "index_by_identifier",
    "match_project",
    "extract_status_lines",
]
