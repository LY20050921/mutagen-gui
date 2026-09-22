"""UI 偏好与路径设置的持久化（``settings.json``）。

存放位置见 :data:`config.SETTINGS_FILE`（默认 ``%APPDATA%\\MutagenGUI``）。

读取时会把空字段用默认值兜底，所以调用方拿到的 :class:`Settings`
永远是可直接使用的值，不需要再判断空。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path

import config

from .cli import find_mutagen_exe


@dataclass
class Settings:
    """可持久化的用户设置。"""

    mutagen_exe: str = ""
    """``mutagen.exe`` 的完整路径。"""

    default_yml_dir: str = ""
    """新建实例时 yml 的默认保存目录。"""

    default_ssh_alias: str = ""
    """新建实例时预填的 SSH 别名。"""

    poll_interval_sec: int = 0
    """状态轮询间隔（秒）。"""

    theme: str = "dark"
    """主题名；v1 只有 ``dark``。"""

    # -- 读写 -------------------------------------------------------------- #

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        target = path or config.SETTINGS_FILE

        data: dict[str, object] = {}
        if target.exists():
            try:
                raw = json.loads(target.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data = raw
            except (json.JSONDecodeError, OSError):
                data = {}

        known = {f.name for f in fields(cls)}
        settings = cls(**{k: v for k, v in data.items() if k in known})  # type: ignore[arg-type]

        # 兜底：让调用方拿到的永远是可用的值
        if not settings.mutagen_exe:
            found = find_mutagen_exe()
            settings.mutagen_exe = str(found) if found else ""
        if not settings.default_yml_dir:
            settings.default_yml_dir = str(config.DEFAULT_YML_DIR)
        if not settings.default_ssh_alias:
            settings.default_ssh_alias = config.DEFAULT_SSH_ALIAS
        if not settings.poll_interval_sec or settings.poll_interval_sec <= 0:
            settings.poll_interval_sec = config.POLL_INTERVAL_SEC
        if not settings.theme:
            settings.theme = "dark"

        return settings

    def save(self, path: Path | None = None) -> None:
        target = path or config.SETTINGS_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {f.name: getattr(self, f.name) for f in fields(self)},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    # -- 便捷 -------------------------------------------------------------- #

    def mutagen_path(self) -> Path | None:
        """返回可用的 mutagen 路径；无效时返回 ``None``。"""
        if self.mutagen_exe:
            candidate = Path(self.mutagen_exe)
            if candidate.is_file():
                return candidate
        return find_mutagen_exe()

    def yml_dir(self) -> Path:
        return Path(self.default_yml_dir)


__all__ = ["Settings"]
