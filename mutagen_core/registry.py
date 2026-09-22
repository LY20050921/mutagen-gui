"""实例注册表（``projects.json``）的读写。

设计要点
--------
注册表**不是**配置来源，只是「最近打开的 yml 列表」。

真正的配置永远在 yml 文件里，所以：

* 外部手改 yml → 重启 GUI 后按 yml 内容刷新注册表缓存字段
* 删除注册表条目 → 不会删除 yml，也不会停止会话（需求 F1.3 的边界）

写入采用 **临时文件 + os.replace** 的原子替换，避免断电/崩溃写坏文件。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from config import PROJECTS_FILE

from .models import Project, now_iso

_SCHEMA_VERSION = 1


class RegistryError(RuntimeError):
    """注册表读写失败。"""


# --------------------------------------------------------------------------- #
# 单实例锁
# --------------------------------------------------------------------------- #


class SingleInstanceLock:
    """基于 OS 文件锁的单实例保护。

    使用 ``msvcrt.locking``（Windows）/ ``fcntl.flock``（POSIX）而不是
    「有锁文件就算占用」——因为 OS 锁会随进程退出**自动释放**，
    不会留下需要人工清理的死锁文件。

    注意：**不要**用 ``os.kill(pid, 0)`` 判断进程存活，
    在 Windows 上那会真的调用 TerminateProcess 把进程杀掉。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None
        self.warning = ""
        """获取锁过程中遇到的**非致命**问题（例如锁文件建不出来）。"""

    def acquire(self) -> bool:
        """尝试获取锁。

        :returns: ``True`` 表示可以继续运行，``False`` 表示已有实例在运行。
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            # 锁文件本身建不出来（权限、只读盘…）不应该拖垮启动。
            # 退化为「不做单实例保护」，并留下提示。
            self.warning = f"无法创建锁文件，已跳过单实例保护：{exc}"
            return True

        handle = os.fdopen(fd, "r+", encoding="utf-8", newline="")

        try:
            if os.name == "nt":  # pragma: no cover - 平台分支
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - 平台分支
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False

        # PID 只是排查用的参考信息。注意：写入「被自己锁住的区域」在某些
        # 文件系统 / 打开模式下会被拒绝（实测 "a+" 追加模式下 truncate 会
        # 抛 PermissionError），所以这里整体容错——写不进去也不影响锁的效力。
        try:
            handle.seek(0)
            handle.truncate(0)
            handle.write(f"{os.getpid()}\n")
            handle.flush()
        except OSError as exc:
            self.warning = f"锁文件写入 PID 失败（不影响使用）：{exc}"

        self._handle = handle
        return True

    def release(self) -> None:
        """释放锁（进程退出时 OS 也会自动释放）。"""
        if self._handle is None:
            return
        try:
            if os.name == "nt":  # pragma: no cover - 平台分支
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover - 平台分支
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "SingleInstanceLock":
        if not self.acquire():
            raise RegistryError("已有另一个 MutagenGUI 实例在运行")
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #


@dataclass
class Registry:
    """实例列表的持久化容器。"""

    path: Path = PROJECTS_FILE

    # -- 读写 -------------------------------------------------------------- #

    def load(self) -> list[Project]:
        """读取实例列表。

        文件不存在返回空列表；内容损坏时**备份原文件**并返回空列表，
        而不是让整个程序起不来。
        """
        if not self.path.exists():
            return []

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._quarantine()
            return []

        if isinstance(raw, list):
            entries = raw
        elif isinstance(raw, dict):
            entries = raw.get("projects", [])
        else:
            return []

        projects: list[Project] = []
        for entry in entries:
            if isinstance(entry, dict):
                try:
                    projects.append(Project.from_dict(entry))
                except TypeError:
                    continue
        return projects

    def save(self, projects: list[Project]) -> None:
        """原子写回。"""
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "updated_at": now_iso(),
            "projects": [project.to_dict() for project in projects],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".json.tmp")
        try:
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp, self.path)
        except OSError as exc:  # pragma: no cover - 磁盘异常
            temp.unlink(missing_ok=True)
            raise RegistryError(f"写入注册表失败：{exc}") from exc

    def _quarantine(self) -> None:
        """把损坏的注册表改名留档，便于事后排查。"""
        try:
            self.path.with_suffix(f".json.corrupt-{os.getpid()}").write_bytes(
                self.path.read_bytes()
            )
            self.path.unlink()
        except OSError:
            pass

    # -- 查询 -------------------------------------------------------------- #

    def __iter__(self) -> Iterator[Project]:
        return iter(self.load())

    def find_by_id(self, project_id: str) -> Optional[Project]:
        return next((p for p in self.load() if p.id == project_id), None)

    def find_by_yml(self, yml_path: str | Path) -> Optional[Project]:
        """按 yml 路径查找，用于「同一个 yml 不重复添加」。"""
        target = _normalize_path(yml_path)
        return next(
            (p for p in self.load() if _normalize_path(p.yml_path) == target),
            None,
        )

    def find_by_alpha(self, alpha: str) -> Optional[Project]:
        """按本地端点查找，用于「选文件夹导入」的匹配（需求 F1.6）。"""
        target = _normalize_path(alpha)
        return next(
            (p for p in self.load() if p.alpha and _normalize_path(p.alpha) == target),
            None,
        )

    def find_by_name(self, name: str) -> Optional[Project]:
        return next((p for p in self.load() if p.name == name), None)

    # -- 增删改 ------------------------------------------------------------ #

    def add(self, project: Project) -> Project:
        """新增；若同 yml 已存在则就地更新。"""
        projects = self.load()
        existing = next(
            (p for p in projects
             if _normalize_path(p.yml_path) == _normalize_path(project.yml_path)),
            None,
        )
        if existing is not None:
            project.id = existing.id
            project.created_at = existing.created_at
            projects[projects.index(existing)] = project
        else:
            projects.append(project)
        self.save(projects)
        return project

    def update(self, project: Project) -> None:
        projects = self.load()
        for index, existing in enumerate(projects):
            if existing.id == project.id:
                project.touch()
                projects[index] = project
                break
        else:
            projects.append(project)
        self.save(projects)

    def remove(self, project_id: str) -> bool:
        """只从注册表移除条目——**不删除 yml，也不停止会话**。"""
        projects = self.load()
        remaining = [p for p in projects if p.id != project_id]
        if len(remaining) == len(projects):
            return False
        self.save(remaining)
        return True


def _normalize_path(value: str | Path) -> str:
    """路径比较用的归一化形式（大小写无关、分隔符统一）。"""
    text = str(value).replace("/", "\\").rstrip("\\")
    return text.lower()


__all__ = [
    "Registry",
    "RegistryError",
    "SingleInstanceLock",
]
