"""Mutagen 项目文件（``mutagen.yml``）字段规范。

本模块是 yml 字段的**唯一真相来源**（single source of truth）。

依据来自对 **Mutagen 0.18.1 的实测验证**，而不是文档记忆：

* Mutagen 对 yml 使用**严格解析**——出现未知字段会直接报错：
  ``field xxx not found in type project.SynchronizationConfiguration``
* 因此「能被 ``mutagen project start`` 解析通过」等价于「键名与取值都合法」

完整实测记录见 ``requirements.md`` 附录 C。

.. warning::
   修改本文件后**必须重新实测校验**（用 ``selftest.py`` 跑一遍即可），
   不要凭记忆增删字段。

实测踩到的两个坑，已体现在下面的 spec 中：

1. ``compression`` 是**嵌套结构** ``compression.algorithm``，不是扁平字符串
2. ``symlink.mode`` **不允许**端点级覆盖（其余 10 个字段允许）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# --------------------------------------------------------------------------- #
# 枚举取值（全部来自 `mutagen sync create --help` 的实测输出）
# --------------------------------------------------------------------------- #

SYNC_MODES: tuple[str, ...] = (
    "two-way-safe",
    "two-way-resolved",
    "one-way-safe",
    "one-way-replica",
)

HASH_ALGORITHMS: tuple[str, ...] = ("sha1", "sha256", "xxh128")

PROBE_MODES: tuple[str, ...] = ("probe", "assume")

SCAN_MODES: tuple[str, ...] = ("full", "accelerated")

STAGE_MODES: tuple[str, ...] = ("mutagen", "neighboring")

COMPRESSION_ALGORITHMS: tuple[str, ...] = ("none", "deflate", "zstandard")

SYMLINK_MODES: tuple[str, ...] = ("ignore", "portable", "posix-raw")

WATCH_MODES: tuple[str, ...] = ("portable", "force-poll", "no-watch")

IGNORE_SYNTAXES: tuple[str, ...] = ("mutagen", "docker")

PERMISSIONS_MODES: tuple[str, ...] = ("portable", "manual")

# --------------------------------------------------------------------------- #
# 字段类型
# --------------------------------------------------------------------------- #

KIND_ENUM = "enum"       # 下拉框：取值必须在 choices 内
KIND_TEXT = "text"       # 自由文本
KIND_INT = "int"         # 整数
KIND_BOOL = "bool"       # 复选框
KIND_LIST = "list"       # 动态列表（多值），每行一个字符串

KINDS = (KIND_ENUM, KIND_TEXT, KIND_INT, KIND_BOOL, KIND_LIST)


@dataclass(frozen=True)
class FieldSpec:
    """单个 yml 字段的规范描述。"""

    key: str
    """yml 中的键路径；嵌套字段用 ``.`` 分隔，例如 ``compression.algorithm``。"""

    label: str
    """表单里显示的中文名。"""

    kind: str
    """取值类型，见 ``KIND_*``。"""

    choices: tuple[str, ...] = ()
    """当 ``kind == KIND_ENUM`` 时的合法取值。"""

    endpoint_overridable: bool = False
    """是否允许用 ``configurationAlpha`` / ``configurationBeta`` 做端点级覆盖。

    规则（实测得出）：CLI 里存在 ``--xxx-alpha`` / ``--xxx-beta`` 双变体的字段才为 True。
    """

    default: Any = None
    """GUI 默认值；``None`` 表示默认不写入 yml（用 Mutagen 内置默认）。"""

    hint: str = ""
    """表单里的补充说明。"""


# --------------------------------------------------------------------------- #
# 同步会话字段（实测：19 个字段全部合法）
# --------------------------------------------------------------------------- #

SESSION_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "mode", "同步模式", KIND_ENUM, SYNC_MODES, False, "two-way-resolved",
        "双向同步；冲突时 alpha（本地）总是胜出，不产生冲突",
    ),
    FieldSpec(
        "hash", "哈希算法", KIND_ENUM, HASH_ALGORITHMS, False, None,
        "内容哈希算法；留空使用 Mutagen 默认",
    ),
    FieldSpec(
        "maxEntryCount", "最大条目数", KIND_INT, (), False, None,
        "两端管理的条目上限，0 表示不限",
    ),
    FieldSpec(
        "maxStagingFileSize", "暂存文件大小上限", KIND_TEXT, (), False, None,
        '形如 "1 GB"；留空表示不限',
    ),
    FieldSpec("probeMode", "探测模式", KIND_ENUM, PROBE_MODES, True, None,
              "probe：实际探测；assume：直接假定目标存在"),
    FieldSpec("scanMode", "扫描模式", KIND_ENUM, SCAN_MODES, True, None,
              "full：完整扫描；accelerated：加速扫描"),
    FieldSpec("stageMode", "暂存模式", KIND_ENUM, STAGE_MODES, True, None,
              "mutagen：在专用目录暂存；neighboring：与目标同目录暂存"),
    FieldSpec(
        "compression.algorithm", "压缩算法", KIND_ENUM, COMPRESSION_ALGORITHMS, True, None,
        "注意：yml 里是嵌套写法 compression.algorithm，不能写成扁平字符串",
    ),
    FieldSpec("symlink.mode", "符号链接模式", KIND_ENUM, SYMLINK_MODES, False, "ignore",
              "该字段不支持端点级覆盖；ignore = 完全忽略符号链接"),
    FieldSpec("watch.mode", "文件监视模式", KIND_ENUM, WATCH_MODES, True, "portable",
              "portable：可移植的监视；force-poll：强制轮询；no-watch：不监视"),
    FieldSpec("watch.pollingInterval", "轮询间隔（秒）", KIND_INT, (), True, None,
              "仅在轮询模式下有意义"),
    # ⚠️ 实测（Mutagen 0.18.1）两个要点，改这个默认值前务必先读：
    #
    # ① 内置忽略名单只有 **5 个**：.git / .svn / .hg / .bzr / _darcs
    #    —— **CVS 不在名单里**，无论这个字段怎么设，CVS 目录都会照常同步。
    # ② **不写这个字段时，Mutagen 的默认是「不忽略」**（.git 会照常同步）。
    #    而这里刻意把 GUI 默认设成 True（忽略），**有意与 Mutagen 默认相反**：
    #    同步 .git 要付三重代价 —— 首次传整个对象库、每次 git gc 重传 pack、
    #    两端同时 commit 会互相覆盖。对同步工具来说不划算。
    #    selftest 有断言把这个决定钉住，避免以后被无意改掉。
    FieldSpec("ignore.vcs", "忽略版本控制目录", KIND_BOOL, (), False, True,
              "勾选＝不同步 .git/.svn/.hg/.bzr/_darcs（CVS 不在内置名单里）；"
              "取消＝连 .git 一起同步，远端也能用 git 命令，"
              "但首次要传完整对象库，且两边同时 commit 有覆盖风险"),
    FieldSpec("ignore.syntax", "忽略规则语法", KIND_ENUM, IGNORE_SYNTAXES, False, "mutagen",
              "mutagen：Mutagen 语法；docker：Docker 风格的 .dockerignore 语法"),
    FieldSpec("ignore.paths", "忽略规则", KIND_LIST, (), False,
              ("checkpoints", "/tmp", "__pycache__", "*.pyc",
               ".ipynb_checkpoints", ".gradio"),
              "每行一条；以 / 开头只锚定同步根目录；支持 ! 取反"),
    FieldSpec("permissions.mode", "权限模式", KIND_ENUM, PERMISSIONS_MODES, False, None,
              "portable：可移植；manual：手动指定权限"),
    FieldSpec("permissions.defaultFileMode", "默认文件权限", KIND_TEXT, (), True, None,
              '八进制字符串，如 "0644"'),
    FieldSpec("permissions.defaultDirectoryMode", "默认目录权限", KIND_TEXT, (), True, None,
              '八进制字符串，如 "0755"'),
    FieldSpec("permissions.defaultOwner", "默认所有者", KIND_TEXT, (), True, None, ""),
    FieldSpec("permissions.defaultGroup", "默认用户组", KIND_TEXT, (), True, None, ""),
    FieldSpec("flushOnCreate", "创建后立即完整同步", KIND_BOOL, (), False, True,
              "会话创建后立刻强制跑一次完整同步，不必等文件变动触发"),
)

SESSION_FIELDS_BY_KEY: dict[str, FieldSpec] = {spec.key: spec for spec in SESSION_FIELDS}
"""按 key 索引字段规范。"""

SESSION_FIELD_KEYS: frozenset[str] = frozenset(SESSION_FIELDS_BY_KEY)
"""全部合法的字段 key，用于白名单校验。"""

ENDPOINT_OVERRIDABLE_FIELDS: tuple[FieldSpec, ...] = tuple(
    spec for spec in SESSION_FIELDS if spec.endpoint_overridable
)
"""允许端点级覆盖的字段（实测共 10 个）。"""

# --------------------------------------------------------------------------- #
# 生命周期钩子（实测：作为顶层键，值是命令字符串列表）
# --------------------------------------------------------------------------- #

HOOK_NAMES: tuple[str, ...] = (
    "beforeCreate",
    "afterCreate",
    "beforePause",
    "afterPause",
    "beforeResume",
    "afterResume",
    "beforeTerminate",
    "afterTerminate",
)

# --------------------------------------------------------------------------- #
# 会话名规则（实测：下划线、点号、空格全部非法；字母数字与连字符合法）
# --------------------------------------------------------------------------- #

SESSION_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")
"""会话名/实例名的合法字符：字母、数字、连字符。"""

SESSION_NAME_HELP = "只能包含字母、数字和连字符（-），不能有下划线、点号、空格"

# --------------------------------------------------------------------------- #
# 端点
# --------------------------------------------------------------------------- #

#: 远程端点写法：``<SSH 别名>:<绝对路径>``；本地端点写盘符路径。
ENDPOINT_HELP = "远程格式为 <SSH 别名>:<远程绝对路径>，例如 autodl:/root/project"

__all__ = [
    "SYNC_MODES", "HASH_ALGORITHMS", "PROBE_MODES", "SCAN_MODES", "STAGE_MODES",
    "COMPRESSION_ALGORITHMS", "SYMLINK_MODES", "WATCH_MODES", "IGNORE_SYNTAXES",
    "PERMISSIONS_MODES",
    "KIND_ENUM", "KIND_TEXT", "KIND_INT", "KIND_BOOL", "KIND_LIST", "KINDS",
    "FieldSpec", "SESSION_FIELDS", "SESSION_FIELDS_BY_KEY", "SESSION_FIELD_KEYS",
    "ENDPOINT_OVERRIDABLE_FIELDS",
    "HOOK_NAMES",
    "SESSION_NAME_PATTERN", "SESSION_NAME_HELP",
    "ENDPOINT_HELP",
]
