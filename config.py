"""全局默认值与路径。

约定
----
* **程序数据**（实例注册表、UI 偏好、单实例锁）放在项目目录下的 ``.config``
  —— 便携式布局，整个程序目录可以整体拷走，配置跟着走
* **用户产出**（生成的 yml）默认放项目下的 ``ymls`` 目录，可在 Settings 里改
* 本文件不放任何密钥；SSH 连接信息一律从 ``~/.ssh/config`` 读取

早期版本把程序数据放在 ``%APPDATA%\\MutagenGUI``。首次启动时会自动迁移过来，
见 :func:`migrate_legacy_config`。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

APP_NAME = "MutagenGUI"
APP_VERSION = "0.1.0"

APP_ROOT = Path(__file__).resolve().parent
"""项目根目录（本仓库所在目录）。"""

# --------------------------------------------------------------------------- #
# 目录
# --------------------------------------------------------------------------- #

DEFAULT_YML_DIR = APP_ROOT / "ymls"
"""新建实例时 yml 的默认保存目录（需求 F7 可在 Settings 修改）。"""

LOG_DIR = APP_ROOT / "logs"
"""日志导出目录。"""


def _config_dir() -> Path:
    """程序数据目录。

    默认是**项目目录下的 ``.config``**（便携式布局）：整个程序目录可以整体拷贝走，
    配置跟着走，也方便随时打开查看。

    需要放到别处时，设置环境变量 ``MUTENGUI_CONFIG_DIR`` 覆盖。
    """
    override = os.environ.get("MUTAGENGUI_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return APP_ROOT / ".config"


CONFIG_DIR = _config_dir()
PROJECTS_FILE = CONFIG_DIR / "projects.json"
"""实例注册表（只记录「最近打开的 yml」）。"""

SETTINGS_FILE = CONFIG_DIR / "settings.json"
"""UI 偏好与路径设置。"""

LOCK_FILE = CONFIG_DIR / "app.lock"
"""单实例锁文件（需求：不支持同时运行多个 GUI 进程）。"""


def legacy_config_dirs() -> list[Path]:
    """早期版本用过的配置目录（用于一次性迁移）。"""
    candidates: list[Path] = []

    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / APP_NAME)

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        candidates.append(Path(xdg) / APP_NAME)

    try:
        candidates.append(Path.home() / ".config" / APP_NAME)
    except (RuntimeError, OSError):  # pragma: no cover - 取不到家目录
        pass

    return [path for path in candidates if path != CONFIG_DIR]


def migrate_legacy_config() -> str:
    """把旧位置的配置搬到当前配置目录。

    只在当前目录还没有配置时执行一次。采用**复制**而不是移动——
    万一新版有问题，旧文件还在原地可以找回。

    :returns: 给用户看的说明；没有需要迁移的内容时返回空串。
    """
    if PROJECTS_FILE.exists() or SETTINGS_FILE.exists():
        return ""

    for legacy in legacy_config_dirs():
        if not legacy.is_dir():
            continue

        migrated: list[str] = []
        for name in ("projects.json", "settings.json"):
            source = legacy / name
            if not source.is_file():
                continue
            try:
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, CONFIG_DIR / name)
                migrated.append(name)
            except OSError:
                continue

        if migrated:
            return (
                f"已把配置从旧位置迁移过来：{legacy} → {CONFIG_DIR}"
                f"（{'、'.join(migrated)}）"
            )

    return ""

# --------------------------------------------------------------------------- #
# 行为参数
# --------------------------------------------------------------------------- #

POLL_INTERVAL_SEC = 5
"""状态轮询间隔（需求 F5.4）。"""

LOG_TAIL_LINES = 100
"""``run log`` 类命令默认展示的行数。"""

DEFAULT_SSH_ALIAS = "autodl"
"""新建实例时预填的 SSH 别名（仅作为默认值）。"""

SSH_CONFIG_PATH = Path.home() / ".ssh" / "config"
"""SSH 配置路径；GUI 只读取、不修改。"""

SSH_PROBE_TIMEOUT_SEC = 5
"""连接测试的超时（需求 F9.2）。"""

# --------------------------------------------------------------------------- #
# 编辑 yml 后对运行中会话的处理方式（需求 3.5）
# --------------------------------------------------------------------------- #

PROMPT_RESTART = "restart"
PROMPT_SAVE_ONLY = "save-only"
PROMPT_CANCEL = "cancel"

# --------------------------------------------------------------------------- #
# 三种应用模式（需求 3.4）
# --------------------------------------------------------------------------- #

MODE_NORMAL = "normal"
MODE_EDIT = "edit"
MODE_DELETE = "delete"

MODE_LABELS = {
    MODE_NORMAL: "普通模式",
    MODE_EDIT: "编辑模式",
    MODE_DELETE: "删除模式",
}


def ensure_directories() -> None:
    """确保运行所需的目录都存在。"""
    for directory in (CONFIG_DIR, DEFAULT_YML_DIR, LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)


__all__ = [
    "APP_NAME",
    "APP_VERSION",
    "APP_ROOT",
    "DEFAULT_YML_DIR",
    "LOG_DIR",
    "CONFIG_DIR",
    "PROJECTS_FILE",
    "SETTINGS_FILE",
    "LOCK_FILE",
    "legacy_config_dirs",
    "migrate_legacy_config",
    "POLL_INTERVAL_SEC",
    "LOG_TAIL_LINES",
    "DEFAULT_SSH_ALIAS",
    "SSH_CONFIG_PATH",
    "SSH_PROBE_TIMEOUT_SEC",
    "PROMPT_RESTART",
    "PROMPT_SAVE_ONLY",
    "PROMPT_CANCEL",
    "MODE_NORMAL",
    "MODE_EDIT",
    "MODE_DELETE",
    "MODE_LABELS",
    "ensure_directories",
]
