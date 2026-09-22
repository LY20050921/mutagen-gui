"""由表单数据生成 ``mutagen.yml``，以及反向解析回表单数据。

生成策略
--------
所有会话级配置写入 ``sync.defaults``，只有 ``alpha`` / ``beta`` /
``configurationAlpha`` / ``configurationBeta`` 写在命名会话下。

这样做有两个好处：

1. 产出的 yml 结构与用户手写的配置一致（见 ``D:\\code\\mutagen.yml``）
2. 将来若要放开「一个 yml 多个会话」，只需往 ``sync`` 下加会话即可，结构不用变

.. note::
   方案 A（需求附录 D.2）：**v1 的 GUI 只生成单会话 yml**。
   ``extract_sessions`` 仍会如实报告 yml 里的会话数量，用于把多会话 yml 标记为只读。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import schema

try:  # ruamel 用于「保留注释」的往返读写；缺失时退化为整份重建
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap

    HAVE_RUAMEL = True
except ImportError:  # pragma: no cover - 依赖缺失时的退化路径
    YAML = None  # type: ignore[assignment]
    CommentedMap = dict  # type: ignore[assignment,misc]
    HAVE_RUAMEL = False

# --------------------------------------------------------------------------- #
# 默认模板
# --------------------------------------------------------------------------- #

DEFAULT_IGNORE_PATHS: tuple[str, ...] = (
    "checkpoints",
    "/tmp",
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".ipynb_checkpoints",
    ".gradio",
)
"""GUI 新建实例时预填的忽略规则。

这组规则来自实际使用经验：``checkpoints`` 挡住模型权重、``__pycache__`` /
``*.pyc`` 挡 Python 缓存、``.gradio`` 挡 Gradio 运行时缓存。
"""

DEFAULT_COMMANDS_TEMPLATE: dict[str, str] = {
    "ssh": "ssh -t <别名> tmux new -A -s run -c <远程路径>",
    "log": "ssh <别名> tail -n 100 <远程路径>/run.log",
}
"""新建实例时可选的 commands 模板（需用户替换占位符）。"""


# --------------------------------------------------------------------------- #
# 表单数据载体
# --------------------------------------------------------------------------- #


@dataclass
class SessionConfig:
    """一个 yml 的完整表单数据。

    这是 UI 与 yml 之间唯一的中转结构：UI 读写它，``template`` 负责它 <-> yml。
    """

    name: str = ""
    """实例名 / 会话名，必须匹配 ``schema.SESSION_NAME_PATTERN``。"""

    alpha: str = ""
    """本地端点。"""

    beta: str = ""
    """远程端点，形如 ``<SSH 别名>:<远程路径>``。"""

    values: dict[str, Any] = field(default_factory=dict)
    """会话级配置，键为 schema 里的**点分路径**（如 ``ignore.paths``）。"""

    commands: dict[str, str] = field(default_factory=dict)
    """顶层 ``commands:`` 段。"""

    hooks: dict[str, list[str]] = field(default_factory=dict)
    """顶层生命周期钩子；键为 ``schema.HOOK_NAMES`` 之一。"""

    configuration_alpha: dict[str, Any] = field(default_factory=dict)
    """端点级覆盖（只允许 ``schema.ENDPOINT_OVERRIDABLE_FIELDS`` 里的字段）。"""

    configuration_beta: dict[str, Any] = field(default_factory=dict)

    # -- 便捷方法 ---------------------------------------------------------- #

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value

    @classmethod
    def with_defaults(cls, name: str = "", alpha: str = "", beta: str = "") -> "SessionConfig":
        """构造一个带 GUI 默认值的配置。"""
        values: dict[str, Any] = {}
        for spec in schema.SESSION_FIELDS:
            if spec.default is not None:
                values[spec.key] = (
                    list(spec.default) if spec.kind == schema.KIND_LIST
                    else spec.default
                )
        values["ignore.paths"] = list(DEFAULT_IGNORE_PATHS)
        return cls(name=name, alpha=alpha, beta=beta, values=values)


# --------------------------------------------------------------------------- #
# 点分键 <-> 嵌套字典
# --------------------------------------------------------------------------- #


def nest(dotted: dict[str, Any]) -> dict[str, Any]:
    """``{"a.b": 1}`` -> ``{"a": {"b": 1}}``。"""
    root: dict[str, Any] = {}
    for key, value in dotted.items():
        parts = key.split(".")
        node = root
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value
    return root


def flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """``{"a": {"b": 1}}`` -> ``{"a.b": 1}``（只展开纯字典节点）。"""
    out: dict[str, Any] = {}
    for key, value in data.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(flatten(value, f"{dotted}."))
        else:
            out[dotted] = value
    return out


def _prune(value: Any) -> bool:
    """判断某个值是否应当从 yml 里去掉。

    只去掉「未填写」的情况（``None`` / 空串 / 空容器）。
    ``0`` 和 ``False`` 是**有意义**的取值（如 ``maxEntryCount: 0`` 表示不限），必须保留。
    """
    if value is None:
        return True
    if isinstance(value, str) and value == "":
        return True
    if isinstance(value, (list, dict, tuple)) and len(value) == 0:
        return True
    return False


# --------------------------------------------------------------------------- #
# 生成 yml
# --------------------------------------------------------------------------- #


class _IndentDumper(yaml.SafeDumper):
    """让列表项相对父键缩进，输出更接近手写风格。"""

    def increase_indent(self, flow: bool = False, indentless: bool = False):  # noqa: D102
        return super().increase_indent(flow, False)


def build_document(config: SessionConfig) -> dict[str, Any]:
    """把表单数据组装成待 dump 的字典。"""
    session_name = config.name or "session"

    defaults_source = {
        key: value for key, value in config.values.items() if not _prune(value)
    }
    defaults = nest(defaults_source) if defaults_source else {}

    session: dict[str, Any] = {}
    if config.alpha:
        session["alpha"] = config.alpha
    if config.beta:
        session["beta"] = config.beta

    alpha_cfg = {k: v for k, v in config.configuration_alpha.items() if not _prune(v)}
    if alpha_cfg:
        session["configurationAlpha"] = nest(alpha_cfg)
    beta_cfg = {k: v for k, v in config.configuration_beta.items() if not _prune(v)}
    if beta_cfg:
        session["configurationBeta"] = nest(beta_cfg)

    sync: dict[str, Any] = {}
    if defaults:
        sync["defaults"] = defaults
    sync[session_name] = session

    document: dict[str, Any] = {"sync": sync}

    commands = {k: v for k, v in config.commands.items() if k and v}
    if commands:
        document["commands"] = commands

    for hook in schema.HOOK_NAMES:
        lines = [line for line in config.hooks.get(hook, []) if line]
        if lines:
            document[hook] = lines

    return document


def dump_yaml(config: SessionConfig) -> str:
    """把表单数据渲染成 yml 文本（**这是真正会落盘的内容**）。"""
    document = build_document(config)
    return yaml.dump(
        document,
        Dumper=_IndentDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        indent=2,
        width=4096,
    )


# --------------------------------------------------------------------------- #
# 解析 yml
# --------------------------------------------------------------------------- #


def parse_yaml_text(text: str) -> dict[str, Any]:
    """解析 yml 文本；语法错误会抛 ``yaml.YAMLError``。"""
    data = yaml.safe_load(text)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise yaml.YAMLError("yml 根节点必须是映射（mapping）")
    return data


def extract_sessions(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """取出 ``sync`` 下除 ``defaults`` 之外的所有命名会话。"""
    sync = document.get("sync")
    if not isinstance(sync, dict):
        return {}
    return {
        name: body if isinstance(body, dict) else {}
        for name, body in sync.items()
        if name != "defaults"
    }


def count_sessions(document: dict[str, Any]) -> int:
    """yml 里的同步会话数量（用于判断是否只读，见方案 A）。"""
    return len(extract_sessions(document))


def load_config(document: dict[str, Any]) -> SessionConfig:
    """把已解析的 yml 字典还原成 ``SessionConfig``。

    优先取第一个命名会话作为实例；多会话时由调用方用 :func:`count_sessions`
    判断并置为只读。
    """
    sessions = extract_sessions(document)
    if not sessions:
        raise ValueError("yml 中没有任何同步会话（sync 段下只有 defaults）")

    name, body = next(iter(sessions.items()))

    values: dict[str, Any] = {}
    defaults = document.get("sync", {}).get("defaults")
    if isinstance(defaults, dict):
        values.update(flatten(defaults))

    for key, value in body.items():
        if key in ("alpha", "beta", "configurationAlpha", "configurationBeta"):
            continue
        if isinstance(value, dict):
            values.update(flatten(value, f"{key}."))
        else:
            values[key] = value

    commands = document.get("commands")
    hooks = {
        hook: [str(line) for line in document.get(hook, [])]
        for hook in schema.HOOK_NAMES
        if isinstance(document.get(hook), list)
    }

    configuration_alpha = flatten(body.get("configurationAlpha") or {})
    configuration_beta = flatten(body.get("configurationBeta") or {})

    return SessionConfig(
        name=name,
        alpha=str(body.get("alpha", "") or ""),
        beta=str(body.get("beta", "") or ""),
        values=values,
        commands=dict(commands) if isinstance(commands, dict) else {},
        hooks=hooks,
        configuration_alpha=configuration_alpha,
        configuration_beta=configuration_beta,
    )


def load_config_from_text(text: str) -> SessionConfig:
    return load_config(parse_yaml_text(text))


def load_config_from_file(path: str | Path) -> SessionConfig:
    return load_config_from_text(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 落盘（注释保留式合并）
# --------------------------------------------------------------------------- #


def _ruamel():
    """构造「往返模式」的 ruamel 实例。

    缩进参数与 :class:`_IndentDumper` 保持一致，避免同一份配置在
    「合并写入」与「整份重建」两条路径下排版不同。
    """
    # YAML 在没装 ruamel 时是 None；调用方（合并写入 / 另存为）都先查了
    # HAVE_RUAMEL 才走到这里。这句断言把「不会为 None」告诉类型检查器。
    assert YAML is not None, "ruamel.yaml 未安装"
    instance = YAML()
    instance.preserve_quotes = True
    instance.width = 4096
    instance.indent(mapping=2, sequence=4, offset=2)
    return instance


def _managed_nodes(document: dict[str, Any], session_name: str) -> list[dict[str, Any]]:
    """托管字段可能出现的位置：``sync.defaults`` 与命名会话。"""
    sync = document.get("sync")
    if not isinstance(sync, dict):
        return []
    nodes: list[dict[str, Any]] = []
    for node in (sync.get("defaults"), sync.get(session_name)):
        if isinstance(node, dict):
            nodes.append(node)
    return nodes


def _locate(
    document: dict[str, Any], session_name: str, dotted_key: str
) -> tuple[dict[str, Any], str] | None:
    """找出某个点分键**实际所在**的节点。

    同一个字段既可能写在 ``sync.defaults``，也可能写在会话内（而且两边都合法）。
    修改时必须「就地更新」，否则会出现重复键，原位置的注释也留不住。
    """
    parts = dotted_key.split(".")
    for node in _managed_nodes(document, session_name):
        current: Any = node
        for part in parts[:-1]:
            current = current.get(part) if isinstance(current, dict) else None
            if not isinstance(current, dict):
                current = None
                break
        if isinstance(current, dict) and parts[-1] in current:
            return current, parts[-1]
    return None


def _same_value(current: Any, new: Any) -> bool:
    """判断两个值是否等价。

    这是**保住注释的关键**：只要不做赋值，ruamel 就会原样保留该节点
    （包括行尾注释和列表项注释）。一旦重新赋值，附着在该节点上的注释就没了。
    """
    if isinstance(current, bool) != isinstance(new, bool):
        return False
    if isinstance(current, (list, tuple)) or isinstance(new, (list, tuple)):
        if not isinstance(current, (list, tuple)) or not isinstance(new, (list, tuple)):
            return False
        return list(current) == list(new)
    return current == new


def _assign(node: dict[str, Any], key: str, value: Any) -> bool:
    """写入一个键；内容没变则跳过（保住注释）。返回是否真的写了。"""
    if key in node and _same_value(node.get(key), value):
        return False
    node[key] = value
    return True


def _set_path(root: dict[str, Any], dotted_key: str, value: Any) -> None:
    """按点分路径写入，缺失的中间映射自动创建。"""
    parts = dotted_key.split(".")
    node = root
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = CommentedMap()
            node[part] = child
        node = child
    _assign(node, parts[-1], value)


def _delete_path(root: dict[str, Any], dotted_key: str) -> bool:
    """按点分路径删除，并顺手清掉变成空映射的父节点。"""
    parts = dotted_key.split(".")
    chain: list[tuple[dict[str, Any], str]] = []
    node = root
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            return False
        chain.append((node, part))
        node = child

    if parts[-1] not in node:
        return False
    del node[parts[-1]]

    # 自下而上清理空映射，避免留下 `ignore: {}` 这类残留
    for parent, key in reversed(chain):
        child = parent.get(key)
        if isinstance(child, dict) and len(child) == 0:
            del parent[key]
        else:
            break
    return True


def _apply_key(document: dict[str, Any], session_name: str, key: str, value: Any) -> None:
    """就地更新托管字段；原文件里没有才登记到 ``sync.defaults``。"""
    found = _locate(document, session_name, key)
    if found is not None:
        node, leaf = found
        _assign(node, leaf, value)
        return

    sync = document.get("sync")
    if not isinstance(sync, dict):
        sync = CommentedMap()
        document["sync"] = sync
    defaults = sync.get("defaults")
    if not isinstance(defaults, dict):
        defaults = CommentedMap()
        sync["defaults"] = defaults
    _set_path(defaults, key, value)


def _remove_key(document: dict[str, Any], session_name: str, key: str) -> None:
    """把一个托管字段从它出现的任何位置删掉。"""
    for node in _managed_nodes(document, session_name):
        _delete_path(node, key)


def _merge_overrides(
    session: dict[str, Any],
    node_key: str,
    new_values: dict[str, Any],
    old_values: dict[str, Any],
) -> None:
    """合并 ``configurationAlpha`` / ``configurationBeta``。"""
    node = session.get(node_key)
    new_values = dict(new_values or {})

    if not isinstance(node, dict):
        if not new_values:
            session.pop(node_key, None)
            return
        node = CommentedMap()
        session[node_key] = node

    for key, value in new_values.items():
        if not _prune(value):
            _set_path(node, key, value)
    for key in (old_values or {}):
        if _prune(new_values.get(key)):
            _delete_path(node, key)

    if len(node) == 0:
        session.pop(node_key, None)


def _merge_mapping(
    document: dict[str, Any],
    node_key: str,
    new_values: dict[str, Any],
    old_values: dict[str, Any],
) -> None:
    """合并 ``commands`` 这类「名称 -> 值」的映射（已有条目保留其注释）。"""
    node = document.get(node_key)
    new_values = dict(new_values or {})

    if not isinstance(node, dict):
        if not new_values:
            document.pop(node_key, None)
            return
        node = CommentedMap()
        document[node_key] = node

    for key, value in new_values.items():
        _assign(node, key, value)
    for key in (old_values or {}):
        if key not in new_values:
            node.pop(key, None)

    if len(node) == 0:
        document.pop(node_key, None)


def _merge_list(
    document: dict[str, Any],
    node_key: str,
    new_lines: list[str],
    old_lines: list[str],
) -> None:
    """合并钩子这类字符串列表；内容没变就**原样留着**，以保住注释。"""
    new_lines = [line for line in (new_lines or []) if line]
    if list(new_lines) == list(old_lines or []):
        return
    if new_lines:
        document[node_key] = list(new_lines)
    else:
        document.pop(node_key, None)


def _merge_document(
    document: dict[str, Any],
    config: SessionConfig,
    initial: SessionConfig | None,
) -> None:
    """把表单内容合并进已有文档。

    原则：**只动托管字段**。注释、以及表单没覆盖的字段都原样保留。
    """
    sync = document.get("sync")
    if not isinstance(sync, dict):
        sync = CommentedMap()
        document["sync"] = sync

    old_name = initial.name if initial is not None else ""
    session_name = config.name or "session"

    # 会话改名：把旧会话整体搬过去（连同它的注释），而不是新建一个空会话
    session: Any = None
    if old_name and old_name != session_name and old_name in sync:
        session = sync.pop(old_name)
    if not isinstance(session, dict):
        existing = sync.get(session_name)
        session = existing if isinstance(existing, dict) else CommentedMap()
    sync[session_name] = session

    # ---- 端点 ----
    if config.alpha:
        _assign(session, "alpha", config.alpha)
    if config.beta:
        _assign(session, "beta", config.beta)

    # ---- 会话级配置 ----
    initial_values: dict[str, Any] = dict(initial.values) if initial is not None else {}

    for key, value in config.values.items():
        if _prune(value):
            continue
        _apply_key(document, session_name, key, value)

    # 用户清空 / 删掉的字段，要从文件里移除
    for key in initial_values:
        if _prune(config.values.get(key)):
            _remove_key(document, session_name, key)

    # ---- 端点级覆盖、自定义命令、生命周期钩子 ----
    _merge_overrides(
        session, "configurationAlpha", config.configuration_alpha,
        initial.configuration_alpha if initial else {},
    )
    _merge_overrides(
        session, "configurationBeta", config.configuration_beta,
        initial.configuration_beta if initial else {},
    )
    _merge_mapping(
        document, "commands", config.commands,
        initial.commands if initial else {},
    )
    for hook in schema.HOOK_NAMES:
        _merge_list(
            document, hook, config.hooks.get(hook, []),
            initial.hooks.get(hook, []) if initial else [],
        )


def _merge_base(target: Path, source: str | Path | None) -> Path | None:
    """决定用哪份文件当合并底本。

    * **给了 ``source`` 就用它** —— 它才是表单内容的来源，注释理应从它继承。
      这样「导入 A 另存为 B」也能把 A 的注释一起带过去，
      而不是因为 B 是新文件就整份重建（那正是注释丢失的原因）。
    * 否则用目标本身 —— 覆盖已有文件时，尽量保住它原有的注释。
    * 都没有则返回 ``None``，表示全新生成。
    """
    if source:
        candidate = Path(source)
        if candidate.is_file():
            return candidate
    if target.is_file():
        return target
    return None


def write_yaml(
    config: SessionConfig,
    path: str | Path,
    backup: bool = True,
    initial: SessionConfig | None = None,
    source: str | Path | None = None,
) -> Path:
    """把配置写入 yml 文件。

    优先走「**注释保留式合并**」：在原文档上就地更新托管字段——
    注释、以及表单未覆盖的字段都会被保住。

    只有下面三种情况才退化为「整份重建」：

    * 找不到任何可当底本的文件（既没有 ``source``，目标也不存在）
    * 底本无法解析
    * ``ruamel.yaml`` 不可用

    :param backup: 目标已存在时先备份为 ``<name>.yml.bak``（对应需求 F10.6）
    :param initial: 编辑 / 导入时的原始配置；用于算出「哪些字段被用户删掉了」
    :param source: 「另存为」时的来源 yml 路径（通常是导入时选的那个文件）
    :returns: 实际写入的路径
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    if backup and target.exists():
        target.with_suffix(target.suffix + ".bak").write_text(
            target.read_text(encoding="utf-8"), encoding="utf-8"
        )

    base = _merge_base(target, source)

    if HAVE_RUAMEL and base is not None:
        loader = _ruamel()
        document: Any = None
        try:
            document = loader.load(base.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 解析失败就退化为重建
            document = None

        if isinstance(document, dict):
            _merge_document(document, config, initial)
            with target.open("w", encoding="utf-8") as handle:
                loader.dump(document, handle)
            return target

    target.write_text(dump_yaml(config), encoding="utf-8")
    return target


__all__ = [
    "SessionConfig",
    "DEFAULT_IGNORE_PATHS",
    "DEFAULT_COMMANDS_TEMPLATE",
    "nest",
    "flatten",
    "build_document",
    "dump_yaml",
    "parse_yaml_text",
    "extract_sessions",
    "count_sessions",
    "load_config",
    "load_config_from_text",
    "load_config_from_file",
    "write_yaml",
    "HAVE_RUAMEL",
]
