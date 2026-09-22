"""保存前的静态校验。

为什么必须校验
--------------
实测确认 Mutagen 对 yml 是**严格解析**：出现未知字段会直接失败——

    field zzzTotallyBogusKeyXyz not found in type project.SynchronizationConfiguration

所以生成端必须保证键名与取值都合法，否则 ``mutagen project start`` 会报错。
本模块就是「第一道防线」；「第二道防线」是 Start 时 Mutagen 自己的解析，
它的原始报错会被原样回显到日志面板（需求 F12.4）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import schema
from .template import SessionConfig, flatten

LEVEL_ERROR = "error"
LEVEL_WARNING = "warning"

#: 远程端点形如 ``<别名>:<路径>``
_REMOTE_ENDPOINT = re.compile(r"^[A-Za-z0-9._-]+:(/.*|[A-Za-z]:[\\/].*)$")

#: 本地端点形如 ``D:\code\x`` 或 ``D:/code/x`` 或 UNC ``\\server\share``
_LOCAL_ENDPOINT = re.compile(r"^([A-Za-z]:[\\/]|\\\\|/)")

#: 八进制权限，如 0644
_OCTAL_MODE = re.compile(r"^0?[0-7]{3,4}$")


@dataclass(frozen=True)
class Issue:
    """一条校验问题。"""

    field: str
    level: str
    message: str

    @property
    def is_error(self) -> bool:
        return self.level == LEVEL_ERROR

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志
        return f"[{self.level}] {self.field}: {self.message}"


def _error(fld: str, msg: str) -> Issue:
    return Issue(fld, LEVEL_ERROR, msg)


def _warning(fld: str, msg: str) -> Issue:
    return Issue(fld, LEVEL_WARNING, msg)


# --------------------------------------------------------------------------- #
# 单项校验
# --------------------------------------------------------------------------- #


def validate_session_name(name: str) -> list[Issue]:
    """校验实例名 / 会话名。

    实测：Mutagen 会话名只允许 ``[A-Za-z0-9-]``；
    下划线、点号、空格都会报 ``invalid name character``。
    """
    if not name:
        return [_error("name", "实例名不能为空")]
    if not schema.SESSION_NAME_PATTERN.match(name):
        bad = next((c for c in name if not re.match(r"[A-Za-z0-9-]", c)), "")
        detail = f"（检测到非法字符 {bad!r}）" if bad else ""
        return [_error("name", f"实例名{schema.SESSION_NAME_HELP}{detail}")]
    return []


def validate_endpoint(field_name: str, value: str, *, remote: bool) -> list[Issue]:
    """校验 alpha / beta 端点。"""
    if not value:
        return [_error(field_name, "不能为空")]

    if remote:
        if not _REMOTE_ENDPOINT.match(value):
            return [_error(
                field_name,
                "远程端点格式应为 <SSH 别名>:<远程绝对路径>，例如 autodl:/root/project",
            )]
        return []

    if _REMOTE_ENDPOINT.match(value) and not _LOCAL_ENDPOINT.match(value):
        return [_error(
            field_name,
            "本地端点应为盘符路径（如 D:\\code\\project）或 UNC 路径，不应带 SSH 别名",
        )]
    return []


def validate_yml_path(path: str) -> list[Issue]:
    """校验 yml 保存路径。"""
    if not path:
        return [_error("yml_path", "yml 保存路径不能为空")]

    target = Path(path)
    issues: list[Issue] = []

    if target.suffix.lower() not in (".yml", ".yaml"):
        issues.append(_warning("yml_path", "建议使用 .yml 扩展名"))

    parent = target.parent
    if not parent.exists():
        issues.append(_warning("yml_path", f"目录不存在，保存时会自动创建：{parent}"))
    elif not parent.is_dir():
        issues.append(_error("yml_path", f"父路径不是目录：{parent}"))
    return issues


def validate_values(values: dict[str, Any]) -> list[Issue]:
    """按 ``schema.SESSION_FIELDS`` 白名单逐项校验。"""
    issues: list[Issue] = []

    for key, value in values.items():
        spec = schema.SESSION_FIELDS_BY_KEY.get(key)
        if spec is None:
            issues.append(_error(key, "未知字段（Mutagen 是严格解析，未知键会导致启动失败）"))
            continue

        if value is None or value == "":
            continue

        if spec.kind == schema.KIND_ENUM:
            if value not in spec.choices:
                issues.append(_error(
                    key, f"取值非法：{value!r}；合法取值为 {' / '.join(spec.choices)}"
                ))
        elif spec.kind == schema.KIND_INT:
            if isinstance(value, bool) or not isinstance(value, int):
                try:
                    int(str(value))
                except (TypeError, ValueError):
                    issues.append(_error(key, f"应为整数，收到 {value!r}"))
            elif value < 0:
                issues.append(_error(key, "不能为负数"))
        elif spec.kind == schema.KIND_BOOL:
            if not isinstance(value, bool):
                issues.append(_error(key, f"应为布尔值，收到 {value!r}"))
        elif spec.kind == schema.KIND_LIST:
            if not isinstance(value, (list, tuple)):
                issues.append(_error(key, "应为字符串列表"))
            elif any(not isinstance(item, str) for item in value):
                issues.append(_error(key, "列表项必须都是字符串"))
        elif spec.kind == schema.KIND_TEXT:
            if not isinstance(value, str):
                issues.append(_error(key, "应为字符串"))

    # 权限八进制格式
    for key in ("permissions.defaultFileMode", "permissions.defaultDirectoryMode"):
        raw = values.get(key)
        if isinstance(raw, str) and raw and not _OCTAL_MODE.match(raw):
            issues.append(_warning(key, f"建议写成四位八进制字符串，如 \"0644\"；当前为 {raw!r}"))

    return issues


def validate_commands(commands: dict[str, str]) -> list[Issue]:
    """校验顶层 ``commands:`` 段。"""
    issues: list[Issue] = []
    for name, command in commands.items():
        if not name:
            issues.append(_error("commands", "命令名不能为空"))
        elif not re.match(r"^[A-Za-z0-9-]+$", name):
            issues.append(_error(
                f"commands.{name}", "命令名只能包含字母、数字和连字符"
            ))
        if not str(command).strip():
            issues.append(_error(f"commands.{name}", "命令内容不能为空"))
    return issues


def validate_hooks(hooks: dict[str, list[str]]) -> list[Issue]:
    """校验生命周期钩子。"""
    issues: list[Issue] = []
    for hook, lines in hooks.items():
        if hook not in schema.HOOK_NAMES:
            issues.append(_error(hook, "不是合法的钩子名"))
            continue
        if not isinstance(lines, list):
            issues.append(_error(hook, "钩子必须是命令列表"))
        elif any(not str(line).strip() for line in lines):
            issues.append(_error(hook, "钩子里存在空命令"))
    return issues


def validate_endpoint_overrides(overrides: dict[str, Any], side: str) -> list[Issue]:
    """校验 ``configurationAlpha`` / ``configurationBeta`` 里的字段。

    实测限制：只有 CLI 里带 ``-alpha`` / ``-beta`` 变体的字段才允许端点级覆盖；
    ``symlink.mode`` 试过会报
    ``symbolic link mode cannot be specified on an endpoint-specific basis``。
    """
    allowed = {spec.key for spec in schema.ENDPOINT_OVERRIDABLE_FIELDS}
    issues: list[Issue] = []
    for key, value in overrides.items():
        if key not in allowed:
            issues.append(_error(
                f"configuration{side}.{key}",
                "该字段不支持端点级覆盖（只有 CLI 带 -alpha/-beta 变体的字段才可以）",
            ))
            continue
        issues.extend(
            Issue(
                f"configuration{side}.{issue.field}",
                issue.level,
                issue.message,
            )
            for issue in validate_values({key: value})
        )
    return issues


# --------------------------------------------------------------------------- #
# 整体校验
# --------------------------------------------------------------------------- #


def validate(config: SessionConfig, yml_path: str | None = None) -> list[Issue]:
    """校验完整的表单数据，返回按「错误优先」排序的问题列表。"""
    issues: list[Issue] = []
    issues.extend(validate_session_name(config.name))
    issues.extend(validate_endpoint("alpha", config.alpha, remote=False))
    issues.extend(validate_endpoint("beta", config.beta, remote=True))
    if yml_path is not None:
        issues.extend(validate_yml_path(yml_path))
    issues.extend(validate_values(config.values))
    issues.extend(validate_commands(config.commands))
    issues.extend(validate_hooks(config.hooks))
    issues.extend(validate_endpoint_overrides(config.configuration_alpha, "Alpha"))
    issues.extend(validate_endpoint_overrides(config.configuration_beta, "Beta"))
    return sorted(issues, key=lambda issue: 0 if issue.is_error else 1)


def errors_only(issues: Iterable[Issue]) -> list[Issue]:
    return [issue for issue in issues if issue.is_error]


def has_errors(issues: Iterable[Issue]) -> bool:
    return any(issue.is_error for issue in issues)


# --------------------------------------------------------------------------- #
# 校验「已存在的 yml」（用于导入场景）
# --------------------------------------------------------------------------- #


def find_unknown_keys(document: dict[str, Any]) -> list[str]:
    """在已解析的 yml 字典里找出不符合标准的字段路径。

    用于导入外部 yml 时给出提示：虽然这些字段 Mutagen 认，
    但 GUI 的表单还不认识它们（避免编辑后静默丢配置）。
    """
    unknown: list[str] = []

    sync = document.get("sync")
    if isinstance(sync, dict):
        for name, body in sync.items():
            if not isinstance(body, dict):
                continue
            for key, value in body.items():
                if key in ("alpha", "beta", "configurationAlpha", "configurationBeta"):
                    continue
                if isinstance(value, dict):
                    for dotted in flatten({key: value}):
                        if dotted not in schema.SESSION_FIELD_KEYS:
                            unknown.append(f"sync.{name}.{dotted}")
                elif key not in schema.SESSION_FIELD_KEYS:
                    unknown.append(f"sync.{name}.{key}")

        defaults = sync.get("defaults")
        if isinstance(defaults, dict):
            for dotted in flatten(defaults):
                if dotted not in schema.SESSION_FIELD_KEYS:
                    unknown.append(f"sync.defaults.{dotted}")

    known_top = {"sync", "forward", "commands", *schema.HOOK_NAMES}
    for key in document:
        if key not in known_top:
            unknown.append(str(key))

    return unknown


__all__ = [
    "LEVEL_ERROR",
    "LEVEL_WARNING",
    "Issue",
    "validate",
    "validate_session_name",
    "validate_endpoint",
    "validate_yml_path",
    "validate_values",
    "validate_commands",
    "validate_hooks",
    "validate_endpoint_overrides",
    "errors_only",
    "has_errors",
    "find_unknown_keys",
]
