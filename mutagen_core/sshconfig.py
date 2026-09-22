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


#: 常见 ssh 失败 -> 「一行中文说明」。键是 ssh 原始报错里的小写特征串。
#:
#: 实测动机：直接把 ssh 的英文原文丢给用户，用户看不懂
#: （真被问过「Host key verification failed 是什么意思」），
#: 而且下面这几种失败的**处理办法完全不同**，混在一起只会让人瞎试。
#:
#: ``{alias}`` 会替换成当前测试的别名，方便用户直接复制命令去执行。
_SSH_ERROR_HINTS: tuple[tuple[str, str], ...] = (
    (
        "host key verification failed",
        "未接受过该服务器指纹 · 先在终端执行 ssh {alias} 并输入 yes",
    ),
    (
        "connection refused",
        "端口没有服务在监听 · 实例可能已关机/释放，或端口已变",
    ),
    (
        "permission denied",
        "认证被拒绝 · 检查该别名的 IdentityFile 私钥与服务器上的公钥",
    ),
    (
        "no such identity",
        "找不到私钥文件 · 检查该别名的 IdentityFile 路径",
    ),
    (
        "could not resolve hostname",
        "域名解析失败 · 检查网络或该别名的 HostName",
    ),
    (
        "connection timed out",
        "连接超时 · 确认服务器可达、端口开放",
    ),
)


@dataclass(frozen=True)
class ProbeResult:
    """一次连通性测试的结果。"""

    ok: bool

    summary: str
    """一行以内的说明，用于界面上的状态标签。"""

    detail: str = ""
    """完整说明（含 ssh 原始报错），用于 tooltip。"""

    def __str__(self) -> str:  # pragma: no cover - 仅为打印方便
        return self.summary


def diagnose_ssh_failure(raw: str, alias: str = "") -> tuple[str, str]:
    """把 ssh 的英文报错拆成 ``(一行中文说明, 完整说明)``。

    **原始报错始终保留**在完整说明里：需求 F12.4 要求不得吞掉真实输出，
    用户也可能需要拿它去搜索。

    :param raw: ssh 的原始 stderr。
    :param alias: 当前测试的别名，用于拼出可直接执行的命令。
    """
    raw = raw.strip()
    headline = raw.splitlines()[0] if raw else "ssh 执行失败"

    lowered = raw.lower()
    for keyword, summary in _SSH_ERROR_HINTS:
        if keyword in lowered:
            return summary.replace("{alias}", alias or "<别名>"), raw
    return headline, raw


def test_connection(alias: str, timeout: int = config.SSH_PROBE_TIMEOUT_SEC) -> ProbeResult:
    """检查 SSH 别名是否可达。"""
    if not alias:
        return ProbeResult(False, "请先选择 SSH 别名")

    ok, raw = _run_ssh(alias, ["echo", "mutagengui-ok"], timeout)
    if ok:
        return ProbeResult(True, "SSH 连接成功")

    summary, detail = diagnose_ssh_failure(raw, alias)
    return ProbeResult(False, summary, detail)


def test_remote_path(
    alias: str,
    remote_path: str,
    timeout: int = config.SSH_PROBE_TIMEOUT_SEC,
) -> ProbeResult:
    """检查远程路径是否存在且是目录。"""
    if not alias or not remote_path:
        return ProbeResult(False, "请先填写别名与远程路径")

    ok, raw = _run_ssh(alias, ["test", "-d", remote_path, "&&", "echo", "ok"], timeout)
    if ok:
        return ProbeResult(True, "远程目录存在")

    # 目录不存在时 ssh 只是返回非 0，**没有**任何输出 —— 这时别拿空串当说明
    if not raw.strip():
        return ProbeResult(False, f"远程目录不存在或不可访问：{remote_path}")

    summary, detail = diagnose_ssh_failure(raw, alias)
    return ProbeResult(False, summary, detail)


def test_endpoint(
    alias: str,
    remote_path: str,
    timeout: int = config.SSH_PROBE_TIMEOUT_SEC,
) -> ProbeResult:
    """连接测试的完整流程：SSH 可达 → 远程目录存在（需求 F9.2）。"""
    reachable = test_connection(alias, timeout)
    if not reachable.ok:
        return reachable

    path_result = test_remote_path(alias, remote_path, timeout)
    if not path_result.ok:
        return ProbeResult(
            False, f"SSH 已连通，但{path_result.summary}", path_result.detail
        )

    return ProbeResult(True, f"{reachable.summary}，且{path_result.summary}")


__all__ = [
    "SshHost",
    "ProbeResult",
    "read_ssh_config",
    "list_aliases",
    "find_host",
    "diagnose_ssh_failure",
    "test_connection",
    "test_remote_path",
    "test_endpoint",
]
