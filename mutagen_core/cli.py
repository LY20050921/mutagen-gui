"""``mutagen`` 命令行的 subprocess 封装。

这里集中处理了四个**实测踩过的坑**，不要在别处重复实现：

1. **不能闪黑窗口**：Windows 上每次调用都要加 ``CREATE_NO_WINDOW``
2. **中文路径乱码**：必须显式 ``encoding="utf-8"`` + ``errors="replace"``
3. **Mutagen 不认系统内置 OpenSSH**：依赖用户环境变量 ``MUTAGEN_SSH_PATH``
   指向 Git for Windows 的 ``usr\\bin``
4. **项目命令要带 ``-f <yml>``**：否则 Mutagen 会去当前工作目录找 ``mutagen.yml``

另外：**命令参数一律用列表传递**，不经过 shell。
这正是之前 ``mutagen project run ssh`` 出问题的根源——
那条命令由 Windows shell 执行，引号和 ``&&`` 会被原样送到远程。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

_CREATE_NO_WINDOW = 0x08000000
"""Windows ``CREATE_NO_WINDOW``，防止子进程弹出控制台窗口。"""

_ERROR_LINE = re.compile(r"^\s*Error:.*$", re.MULTILINE)

DEFAULT_TIMEOUT = 60
"""普通命令的默认超时（秒）。"""


# --------------------------------------------------------------------------- #
# 结果对象
# --------------------------------------------------------------------------- #


@dataclass
class Result:
    """一次命令调用的结果。"""

    ok: bool
    args: tuple[str, ...] = ()
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False

    @property
    def output(self) -> str:
        """合并 stdout 与 stderr，供日志面板直接显示。"""
        chunks = [text.rstrip() for text in (self.stdout, self.stderr) if text and text.strip()]
        return "\n".join(chunks)

    @property
    def error_message(self) -> str:
        """提取 Mutagen 的原始报错行。

        需求 F12.4 要求「原始报错不得吞掉」，所以这里优先返回 ``Error:`` 开头的那几行，
        取不到时回退到完整 stderr。
        """
        for text in (self.stderr, self.stdout):
            if not text:
                continue
            matches = _ERROR_LINE.findall(text)
            if matches:
                return "\n".join(line.strip() for line in matches)
        return (self.stderr or self.stdout or "").strip()

    def describe(self) -> str:
        """用于日志面板的完整描述。"""
        header = " ".join(self.args) if self.args else ""
        body = self.output or "(无输出)"
        tail = f"\n[退出码 {self.exit_code}]"
        if self.timed_out:
            tail += " [已超时]"
        return f"$ {header}\n{body}{tail}"


# --------------------------------------------------------------------------- #
# 定位可执行文件
# --------------------------------------------------------------------------- #

_FALLBACK_LOCATIONS: tuple[str, ...] = (
    r"C:\tools\mutagen\mutagen.exe",
    r"D:\tools\mutagen\mutagen.exe",
    r"C:\Program Files\Mutagen\mutagen.exe",
)


def find_mutagen_exe(explicit: Optional[str] = None) -> Optional[Path]:
    """按优先级定位 ``mutagen.exe``。

    顺序：显式指定 → 环境变量 ``MUTAGEN_EXE`` → ``PATH`` → 常见安装位置。
    """
    for candidate in (explicit, os.environ.get("MUTAGEN_EXE")):
        if candidate:
            path = Path(candidate)
            if path.is_file():
                return path

    found = shutil.which("mutagen")
    if found:
        return Path(found)

    for candidate in _FALLBACK_LOCATIONS:
        path = Path(candidate)
        if path.is_file():
            return path

    return None


# --------------------------------------------------------------------------- #
# CLI 封装
# --------------------------------------------------------------------------- #


@dataclass
class MutagenCLI:
    """对 ``mutagen`` 的调用封装。"""

    exe: Path
    """``mutagen.exe`` 的路径。"""

    default_timeout: int = DEFAULT_TIMEOUT

    extra_env: dict[str, str] = field(default_factory=dict)
    """追加到子进程的环境变量，例如 ``MUTAGEN_SSH_PATH``。"""

    # -- 底层 -------------------------------------------------------------- #

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        # 保证 mutagen.exe 所在目录在 PATH 里（agents 包要一起被找到）
        exe_dir = str(self.exe.parent)
        path = env.get("PATH", "")
        if exe_dir.lower() not in path.lower():
            env["PATH"] = f"{exe_dir}{os.pathsep}{path}" if path else exe_dir
        env.update(self.extra_env)
        return env

    def _popen_kwargs(self) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "encoding": "utf-8",
            "errors": "replace",
            "env": self._env(),
        }
        if os.name == "nt":  # pragma: no cover - 平台分支
            kwargs["creationflags"] = _CREATE_NO_WINDOW
        return kwargs

    def run(self, args: Sequence[str], timeout: Optional[int] = None) -> Result:
        """同步执行一条命令。"""
        argv = [str(self.exe), *[str(a) for a in args]]
        effective_timeout = self.default_timeout if timeout is None else timeout

        try:
            completed = subprocess.run(  # noqa: S603 - 参数以列表传递，无 shell
                argv,
                timeout=effective_timeout,
                **self._popen_kwargs(),  # type: ignore[arg-type]
            )
        except subprocess.TimeoutExpired as exc:
            return Result(
                ok=False,
                args=tuple(argv),
                stdout=_decode(exc.stdout),
                stderr=_decode(exc.stderr),
                exit_code=-1,
                timed_out=True,
            )
        except FileNotFoundError:
            return Result(
                ok=False,
                args=tuple(argv),
                stderr=f"找不到可执行文件：{self.exe}",
                exit_code=-1,
            )

        return Result(
            ok=completed.returncode == 0,
            args=tuple(argv),
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            exit_code=completed.returncode,
        )

    def spawn(self, args: Sequence[str]) -> subprocess.Popen:
        """启动一条长驻命令（如 ``sync monitor``），由调用方逐行读取。"""
        argv = [str(self.exe), *[str(a) for a in args]]
        return subprocess.Popen(argv, **self._popen_kwargs())  # type: ignore[arg-type]

    # -- 版本与守护进程 ---------------------------------------------------- #

    def version(self) -> Result:
        return self.run(["version"], timeout=15)

    def daemon_start(self) -> Result:
        return self.run(["daemon", "start"], timeout=60)

    def daemon_stop(self) -> Result:
        return self.run(["daemon", "stop"], timeout=30)

    def daemon_status(self) -> Result:
        """通过一次轻量调用判断 daemon 是否可用。"""
        return self.run(["sync", "list"], timeout=15)

    # -- 会话查询 ---------------------------------------------------------- #

    def sync_list_json(self) -> Result:
        """输出 JSON 数组，供 :mod:`parser` 解析。"""
        return self.run(["sync", "list", "--template", "{{json .}}"], timeout=30)

    def sync_list_long(self) -> Result:
        """人类可读的详细输出，含 problems / conflicts 明细。"""
        return self.run(["sync", "list", "--long"], timeout=30)

    # -- 会话操作（按会话名或 ID） ---------------------------------------- #

    def sync_pause(self, session: str) -> Result:
        return self.run(["sync", "pause", session])

    def sync_resume(self, session: str) -> Result:
        return self.run(["sync", "resume", session])

    def sync_flush(self, session: str) -> Result:
        return self.run(["sync", "flush", session])

    def sync_reset(self, session: str) -> Result:
        return self.run(["sync", "reset", session])

    def sync_terminate(self, session: str, *, force: bool = False) -> Result:
        args = ["sync", "terminate"]
        if force:
            args.append("--force")
        args.append(session)
        return self.run(args)

    # -- 项目操作（按 yml 文件） ------------------------------------------ #

    def project_start(self, yml: str | Path, *, paused: bool = False) -> Result:
        args = ["project", "start", "-f", str(yml)]
        if paused:
            args.append("--paused")
        return self.run(args, timeout=180)

    def project_terminate(self, yml: str | Path) -> Result:
        return self.run(["project", "terminate", "-f", str(yml)], timeout=120)

    def project_pause(self, yml: str | Path) -> Result:
        return self.run(["project", "pause", "-f", str(yml)])

    def project_resume(self, yml: str | Path) -> Result:
        return self.run(["project", "resume", "-f", str(yml)])

    def project_flush(self, yml: str | Path) -> Result:
        return self.run(["project", "flush", "-f", str(yml)], timeout=120)

    def project_reset(self, yml: str | Path) -> Result:
        return self.run(["project", "reset", "-f", str(yml)], timeout=120)

    def project_list(self, yml: str | Path) -> Result:
        return self.run(["project", "list", "-f", str(yml)])

    def project_run(self, yml: str | Path, command: str, *extra: str) -> Result:
        """执行 yml 里 ``commands:`` 段定义的自定义命令。"""
        return self.run(["project", "run", "-f", str(yml), command, *extra])


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #


def _decode(raw: Optional[bytes | str]) -> str:
    """把 ``TimeoutExpired`` 携带的输出统一转成字符串。"""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw


def run_many(
    cli: MutagenCLI,
    commands: Iterable[tuple[Sequence[str], Optional[int]]],
) -> list[Result]:
    """顺序执行多条命令，用于「重启 = terminate + start」这类组合操作。"""
    results: list[Result] = []
    for args, timeout in commands:
        result = cli.run(args, timeout=timeout)
        results.append(result)
        if not result.ok:
            break
    return results


__all__ = [
    "Result",
    "MutagenCLI",
    "find_mutagen_exe",
    "run_many",
    "DEFAULT_TIMEOUT",
]
