"""读取 ``~/.ssh/config`` 并测试连接（需求 6.3 与 F9）。

**只读**：本模块不写入、不修改用户的 SSH 配置。
之所以这样设计，是因为 SSH 配置是用户的关键基础设施，
GUI 一旦写坏会导致所有远程连接失效——收益远小于风险。

.. note::
   不支持 ``Include`` 指令（会指向其它文件）。如果需要，可在 v2 里加递归合并。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import config

_CREATE_NO_WINDOW = 0x08000000
_PATTERN_CHARS = set("*?![]")


@dataclass
class SshHost:
    """``~/.ssh/config`` 里的一个 Host 条目。"""

    alias: str
    hostname: str = ""
    port: str = ""
    user: str = ""
    identity_file: str = ""

    @property
    def is_pattern(self) -> bool:
        """是否是通配条目（如 ``Host *``）——这类不该出现在别名下拉里。"""
        return any(ch in _PATTERN_CHARS for ch in self.alias)

    @property
    def endpoint_host(self) -> str:
        """用于展示的主机（没有 HostName 时退回别名）。"""
        return self.hostname or self.alias

    def summary_lines(self) -> list[tuple[str, str]]:
        """给「只读解析结果」面板用的 (标签, 值) 列表。"""
        return [
            ("HOST", self.hostname or "（未指定，用别名）"),
            ("PORT", self.port or "22（默认）"),
            ("USER", self.user or "（未指定）"),
            ("KEY", _shorten(self.identity_file)),
        ]


def _shorten(path: str) -> str:
    """把家目录替换成 ``~``，让只读面板更短。"""
    if not path:
        return "（未指定）"
    try:
        home = str(Path.home())
        if path.startswith(home):
            return "~" + path[len(home):]
    except (OSError, RuntimeError):
        pass
    return path


def read_ssh_config(path: Path | str | None = None) -> list[SshHost]:
    """解析 SSH 配置，返回全部 Host 条目（含通配条目）。

    支持 ``Host a b c`` 这种「一行多个别名共享同一配置块」的写法
    ——用户自己的配置就是这么写的（``Host autodl connect.bjb1.seetacloud.com``）。
    """
    target = Path(path) if path else config.SSH_CONFIG_PATH
    if not target.exists():
        return []

    try:
        raw_lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    hosts: list[SshHost] = []
    block: list[SshHost] = []

    for raw in raw_lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
        else:
            parts = line.split(None, 1)
            key = parts[0]
            value = parts[1].strip() if len(parts) > 1 else ""

        lowered = key.lower()

        if lowered == "host":
            block = [SshHost(alias=alias) for alias in value.split()]
            hosts.extend(block)
            continue

        if not block:
            continue

        if lowered == "hostname":
            for host in block:
                host.hostname = value
        elif lowered == "port":
            for host in block:
                host.port = value
        elif lowered == "user":
            for host in block:
                host.user = value
        elif lowered == "identityfile":
            for host in block:
                host.identity_file = value

    return hosts


def list_aliases(path: Path | str | None = None) -> list[str]:
    """返回可作为别名的条目（过滤掉通配条目，去重且保持原顺序）。"""
    seen: set[str] = set()
    result: list[str] = []
    for host in read_ssh_config(path):
        if host.is_pattern or host.alias in seen:
            continue
        seen.add(host.alias)
        result.append(host.alias)
    return result


def find_host(alias: str, path: Path | str | None = None) -> SshHost | None:
    """按别名查找。"""
    for host in read_ssh_config(path):
        if host.alias == alias:
            return host
    return None


# --------------------------------------------------------------------------- #
# 连接测试
# --------------------------------------------------------------------------- #


def _ssh_executable() -> str | None:
    """找 ssh。优先 Git for Windows 的（与 Mutagen 用的保持一致）。"""
    override = os.environ.get("MUTAGEN_SSH_PATH")
    if override:
        candidate = Path(override) / ("ssh.exe" if os.name == "nt" else "ssh")
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ssh")


def _run_ssh(alias: str, remote_args: list[str], timeout: int) -> tuple[bool, str]:
    exe = _ssh_executable()
    if exe is None:
        return False, "找不到 ssh 可执行文件（可设置 MUTAGEN_SSH_PATH）"

    argv = [
        exe,
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={timeout}",
        alias,
        *remote_args,
    ]

    kwargs: dict[str, object] = {
        "capture_output": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout + 6,
    }
    if os.name == "nt":  # pragma: no cover - 平台分支
        kwargs["creationflags"] = _CREATE_NO_WINDOW

    try:
        completed = subprocess.run(argv, **kwargs)  # noqa: S603 - 参数列表，无 shell
    except subprocess.TimeoutExpired:
        return False, f"连接超时（>{timeout} 秒）"
    except OSError as exc:
        return False, f"无法启动 ssh：{exc}"

    ok = completed.returncode == 0
    message = (completed.stderr or completed.stdout or "").strip()

    if ok:
        return True, message or "成功"
    return False, message or f"ssh 返回码 {completed.returncode}"


def test_connection(alias: str, timeout: int = config.SSH_PROBE_TIMEOUT_SEC) -> tuple[bool, str]:
    """检查 SSH 别名是否可达。"""
    if not alias:
        return False, "请先选择 SSH 别名"
    ok, message = _run_ssh(alias, ["echo", "mutagengui-ok"], timeout)
    if ok and "mutagengui-ok" in message:
        return True, "SSH 连接成功"
    if ok:
        return True, "SSH 连接成功"
    return False, message


def test_remote_path(
    alias: str,
    remote_path: str,
    timeout: int = config.SSH_PROBE_TIMEOUT_SEC,
) -> tuple[bool, str]:
    """检查远程路径是否存在且是目录。"""
    if not alias or not remote_path:
        return False, "请先填写别名与远程路径"
    ok, message = _run_ssh(alias, ["test", "-d", remote_path, "&&", "echo", "ok"], timeout)
    if ok:
        return True, "远程目录存在"
    return False, message or f"远程目录不存在：{remote_path}"


def test_endpoint(
    alias: str,
    remote_path: str,
    timeout: int = config.SSH_PROBE_TIMEOUT_SEC,
) -> tuple[bool, str]:
    """连接测试的完整流程：SSH 可达 → 远程目录存在（需求 F9.2）。"""
    reachable, message = test_connection(alias, timeout)
    if not reachable:
        return False, message

    exists, detail = test_remote_path(alias, remote_path, timeout)
    if not exists:
        return False, f"{message}；但{detail}"
    return True, f"{message}，且{detail}"


__all__ = [
    "SshHost",
    "read_ssh_config",
    "list_aliases",
    "find_host",
    "test_connection",
    "test_remote_path",
    "test_endpoint",
]
