"""核心层自检。

用法::

    cd D:\\MutagenGUI
    & 'D:\\miniconda3\\envs\\mutagen-gui\\python.exe' -m mutagen_core.selftest

检查内容：

1. 默认配置能生成 yml 文本
2. 「生成 -> 解析」往返后字段一致
3. 校验器能拦住非法输入（非法会话名 / 未知字段 / 非法枚举 / 端点级覆盖越界 / 端点格式）
4. 多会话识别与未知字段探测（导入场景）
5. **注释保留**：改值 / 增字段 / 清字段 / 备份四种情况下，注释与未覆盖字段都不丢
6. **状态判定**：「正在连接 / 重连」不会被误报成「已断开」
7. **生成的 yml 能被真实 Mutagen 严格解析**（最关键的一步）

第 5 步会把 schema 里能安全开启的字段**全部写进 yml**，交给 Mutagen 做严格解析。
如果字段清单有错（比如某个键名拼错），这一步会直接失败——
这就是「文档里那份字段白名单」的自动化回归测试。

跳过第 5 步：``--no-mutagen``
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

from . import schema, template, validator
from .cli import MutagenCLI, find_mutagen_exe
from .console import make_console_safe

# --------------------------------------------------------------------------- #
# 极简测试框架
# --------------------------------------------------------------------------- #

_passed = 0
_failed = 0
_failures: list[str] = []


def section(title: str) -> None:
    print(f"\n== {title} ==")


def check(name: str, ok: bool, detail: str = "") -> bool:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed += 1
        _failures.append(f"{name} -> {detail}")
        print(f"  [FAIL] {name}")
        if detail:
            for line in str(detail).splitlines():
                print(f"         {line}")
    return ok


def guard(name: str, fn) -> None:
    """运行一个测试函数，异常转成失败而不是让整个自检中断。"""
    try:
        fn()
    except Exception:  # noqa: BLE001 - 自检脚本需要兜住所有异常
        check(name, False, traceback.format_exc())


# --------------------------------------------------------------------------- #
# 1 & 2：生成与往返
# --------------------------------------------------------------------------- #


def _demo_config() -> template.SessionConfig:
    config = template.SessionConfig.with_defaults(
        name="onepose-demo",
        alpha=r"D:\code\OnePoseviaGen",
        beta="autodl:/root/OnePoseviaGen",
    )
    config.commands = {
        "ssh": "ssh -t autodl tmux new -A -s run -c /root/OnePoseviaGen",
        "log": "ssh autodl tail -n 100 /root/OnePoseviaGen/run.log",
    }
    return config


def test_generation() -> str:
    section("1. 生成 yml")
    text = template.dump_yaml(_demo_config())
    print(text.rstrip())
    check("生成了非空 yml", bool(text.strip()))
    check("含 sync 段", "sync:" in text)
    check("含会话名", "onepose-demo:" in text)
    check("含 commands 段", "commands:" in text)
    return text


def test_roundtrip() -> None:
    section("2. 生成 -> 解析 往返")

    def body() -> None:
        text = template.dump_yaml(_demo_config())
        config = template.load_config_from_text(text)

        check("会话名一致", config.name == "onepose-demo", repr(config.name))
        check("alpha 一致", config.alpha == r"D:\code\OnePoseviaGen", repr(config.alpha))
        check("beta 一致", config.beta == "autodl:/root/OnePoseviaGen", repr(config.beta))
        check("mode 一致", config.get("mode") == "two-way-resolved", repr(config.get("mode")))
        check("symlink.mode 一致", config.get("symlink.mode") == "ignore",
              repr(config.get("symlink.mode")))
        check("ignore.vcs 一致", config.get("ignore.vcs") is True,
              repr(config.get("ignore.vcs")))
        check(
            "ignore.paths 一致（列表未被打散）",
            list(config.get("ignore.paths") or []) == list(template.DEFAULT_IGNORE_PATHS),
            repr(config.get("ignore.paths")),
        )
        check("flushOnCreate 一致（False 不被误删）",
              config.get("flushOnCreate") is True, repr(config.get("flushOnCreate")))
        check(
            "commands 一致",
            config.commands.get("ssh", "").startswith("ssh -t autodl"),
            repr(config.commands),
        )

    guard("往返一致性", body)


# --------------------------------------------------------------------------- #
# 3：校验器
# --------------------------------------------------------------------------- #


def test_validator() -> None:
    section("3. 校验器")

    def body() -> None:
        yml_path = str(Path(tempfile.gettempdir()) / "mg-selftest.yml")

        good = _demo_config()
        issues = validator.validate(good, yml_path)
        check(
            "合法配置无错误",
            not validator.has_errors(issues),
            "\n".join(str(i) for i in issues),
        )

        # 会话名：Mutagen 实测不允许下划线
        bad_name = _demo_config()
        bad_name.name = "bad_name"
        issues = validator.validate(bad_name)
        check(
            "拦住含下划线的会话名",
            any(i.field == "name" and i.is_error for i in issues),
            "\n".join(str(i) for i in issues),
        )

        # 未知字段：Mutagen 严格解析会直接报错
        bad_key = _demo_config()
        bad_key.set("zzzBogusKey", 1)
        issues = validator.validate(bad_key)
        check(
            "拦住未知字段",
            any(i.field == "zzzBogusKey" and i.is_error for i in issues),
            "\n".join(str(i) for i in issues),
        )

        # 非法枚举
        bad_enum = _demo_config()
        bad_enum.set("mode", "two-way-whatever")
        issues = validator.validate(bad_enum)
        check(
            "拦住非法枚举取值",
            any(i.field == "mode" and i.is_error for i in issues),
            "\n".join(str(i) for i in issues),
        )

        # 端点级覆盖：symlink.mode 实测不允许
        bad_override = _demo_config()
        bad_override.configuration_alpha = {"symlink.mode": "ignore"}
        issues = validator.validate(bad_override)
        check(
            "拦住 symlink.mode 的端点级覆盖",
            any(i.field.startswith("configurationAlpha") and i.is_error for i in issues),
            "\n".join(str(i) for i in issues),
        )

        # beta 端点格式
        bad_beta = _demo_config()
        bad_beta.beta = "这不是一个端点"
        issues = validator.validate(bad_beta)
        check(
            "拦住非法 beta 端点格式",
            any(i.field == "beta" and i.is_error for i in issues),
            "\n".join(str(i) for i in issues),
        )

        # 非八进制的权限串应给出警告（"999" 含 8/9，不是合法八进制）
        warned = _demo_config()
        warned.set("permissions.defaultFileMode", "999")
        issues = validator.validate(warned)
        check(
            "非八进制权限格式给出警告",
            any(i.field == "permissions.defaultFileMode" and not i.is_error for i in issues),
            "\n".join(str(i) for i in issues),
        )

        # 合法八进制（三位或四位）不应报警告
        octal_ok = _demo_config()
        octal_ok.set("permissions.defaultFileMode", "0644")
        issues = validator.validate(octal_ok)
        check(
            "合法八进制权限不报警告",
            not any(i.field == "permissions.defaultFileMode" for i in issues),
            "\n".join(str(i) for i in issues),
        )

    guard("校验器行为", body)


# --------------------------------------------------------------------------- #
# 4：导入与多会话识别
# --------------------------------------------------------------------------- #


def test_import_helpers() -> None:
    section("4. 导入与多会话识别")

    def body() -> None:
        multi = (
            "sync:\n"
            "  job-alpha:\n"
            '    alpha: "D:/a"\n'
            '    beta: "autodl:/root/a"\n'
            "  job-beta:\n"
            '    alpha: "D:/b"\n'
            '    beta: "autodl:/root/b"\n'
        )
        document = template.parse_yaml_text(multi)
        check("识别出 2 个会话（多会话 -> 只读）",
              template.count_sessions(document) == 2,
              str(template.count_sessions(document)))

        single = template.parse_yaml_text(
            'sync:\n  job:\n    alpha: "D:/a"\n    beta: "autodl:/root/a"\n'
        )
        check("识别出 1 个会话", template.count_sessions(single) == 1,
              str(template.count_sessions(single)))

        empty = template.parse_yaml_text("sync:\n  defaults:\n    mode: two-way-safe\n")
        check("defaults-only 识别为 0 个会话", template.count_sessions(empty) == 0,
              str(template.count_sessions(empty)))

        weird = template.parse_yaml_text(
            "sync:\n"
            "  job:\n"
            '    alpha: "D:/a"\n'
            '    beta: "autodl:/root/a"\n'
            "    someFutureField: 1\n"
            "unknownTopLevel: 2\n"
        )
        unknown = validator.find_unknown_keys(weird)
        check("找出未知字段（导入时提示用）",
              "sync.job.someFutureField" in unknown and "unknownTopLevel" in unknown,
              repr(unknown))

    guard("导入辅助函数", body)


# --------------------------------------------------------------------------- #
# 5：注释保留（ruamel 合并写入）
# --------------------------------------------------------------------------- #

_COMMENTED_YML = """\
# 顶层注释：这就是用户的配置
sync:
  # 默认配置段
  defaults:
    mode: two-way-resolved   # 冲突时本地胜出
    ignore:
      vcs: true
      paths:
        - checkpoints        # 权重目录
        - __pycache__
    customUnknownField: 42   # 表单不认识的字段

  onepose:
    alpha: D:/a
    beta: autodl:/root/a

commands:
  ssh: ssh -t autodl tmux new -A -s run   # 进远程
"""


def test_comment_preservation() -> None:
    section("5. 注释保留（ruamel 合并写入）")

    if not template.HAVE_RUAMEL:
        check("ruamel.yaml 可用", False, "未安装 ruamel.yaml，注释无法保留")
        return
    check("ruamel.yaml 可用", True)

    def body() -> None:
        workdir = Path(tempfile.mkdtemp(prefix="mutagengui-comment-"))
        try:
            yml = workdir / "commented.yml"
            yml.write_text(_COMMENTED_YML, encoding="utf-8")

            initial = template.load_config_from_file(yml)
            check("能解析带注释的 yml", initial.name == "onepose", repr(initial.name))

            # ---- 改值 + 新增字段 ----
            updated = template.load_config_from_file(yml)
            updated.set("mode", "two-way-safe")
            updated.set("hash", "sha256")
            updated.set("watch.mode", "force-poll")
            updated.commands["gpu"] = "ssh autodl nvidia-smi"

            template.write_yaml(updated, yml, backup=False, initial=initial)
            result = yml.read_text(encoding="utf-8")
            print("  --- 合并后的 yml ---")
            for line in result.splitlines():
                print(f"  | {line}")
            print("  -------------------")

            check("保留顶层注释", "顶层注释：这就是用户的配置" in result)
            check("保留段内注释", "默认配置段" in result)
            check("保留行尾注释", "冲突时本地胜出" in result)
            check("保留列表项注释", "权重目录" in result)
            check("保留 commands 注释", "进远程" in result)
            check("保留表单未覆盖的字段", "customUnknownField" in result)
            check("值已改写为 two-way-safe", "two-way-safe" in result)
            check("新增字段 hash 已写入", "sha256" in result)
            check("新增字段 watch.mode 已写入", "force-poll" in result)
            check("新增命令 gpu 已写入", "gpu" in result)

            # ---- 清空字段 -> 应从文件移除，且不影响其它注释 ----
            trimmed = template.load_config_from_file(yml)
            trimmed.values.pop("ignore.vcs", None)
            template.write_yaml(trimmed, yml, backup=False, initial=updated)
            result2 = yml.read_text(encoding="utf-8")

            check("被清空的字段已从文件移除", "vcs" not in result2, result2)
            check("移除字段后其余注释仍在",
                  "权重目录" in result2 and "顶层注释" in result2, result2)
            check("移除字段后未覆盖字段仍在", "customUnknownField" in result2, result2)

            # ---- 另存为新路径：注释必须从「来源文件」继承 ----
            # 这对应真实场景：导入 A -> 改名 + 改保存路径到新文件 B -> 注释不能丢
            renamed = workdir / "renamed.yml"
            fresh = template.load_config_from_text(result2)
            template.write_yaml(
                fresh, renamed, backup=False, initial=fresh, source=yml
            )
            copied = renamed.read_text(encoding="utf-8")
            print("  --- 另存为 renamed.yml 的结果 ---")
            for line in copied.splitlines():
                print(f"  | {line}")
            print("  -------------------------------")

            check("另存为新文件时顶层注释已带过去", "顶层注释" in copied, copied)
            check("另存为新文件时段内注释已带过去", "默认配置段" in copied, copied)
            check("另存为新文件时列表项注释已带过去", "权重目录" in copied, copied)
            check("另存为新文件时未覆盖字段已带过去",
                  "customUnknownField" in copied, copied)
            check("另存为不影响来源文件", yml.read_text(encoding="utf-8") == result2)

            # ---- 备份确实生成了 ----
            template.write_yaml(trimmed, yml, backup=True, initial=trimmed)
            check("生成了 .bak 备份", (workdir / "commented.yml.bak").exists())
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    guard("注释保留", body)


# --------------------------------------------------------------------------- #
# 6：状态判定（连接中 vs 已断开）
# --------------------------------------------------------------------------- #


def test_status_classification() -> None:
    section("6. 状态判定（连接中 vs 已断开）")

    def body() -> None:
        from .models import SessionState

        # 实测取样：远端不可达、但会话已创建
        connecting = SessionState.from_json({
            "name": "demo",
            "status": "connecting-beta",
            "paused": False,
            "alpha": {"connected": True},
            "beta": {"connected": False},
        })
        check("connecting-beta 不报「已断开」",
              "断开" not in connecting.display_status, connecting.display_status)
        check("connecting-beta 显示「正在连接」",
              "连接" in connecting.display_status, connecting.display_status)
        check("connecting-beta 等级为 connecting",
              connecting.status_level == "connecting", connecting.status_level)
        check("connecting-beta 的 is_syncing 为假", not connecting.is_syncing)

        # 同步过之后掉线 -> 措辞应变成「重连」
        reconnecting = SessionState.from_json({
            "name": "demo",
            "status": "connecting-beta",
            "paused": False,
            "successfulCycles": 12,
            "alpha": {"connected": True},
            "beta": {"connected": False},
        })
        check("掉线重连时显示「重连」",
              "重连" in reconnecting.display_status, reconnecting.display_status)

        # 正常同步
        watching = SessionState.from_json({
            "name": "demo",
            "status": "watching",
            "paused": False,
            "successfulCycles": 3,
            "alpha": {"connected": True},
            "beta": {"connected": True},
        })
        check("watching 显示「同步中」",
              watching.display_status == "同步中", watching.display_status)
        check("watching 等级为 running", watching.status_level == "running", watching.status_level)
        check("watching 的 is_syncing 为真", watching.is_syncing)

        # 暂停
        paused = SessionState.from_json({
            "name": "demo",
            "status": "watching",
            "paused": True,
            "alpha": {"connected": True},
            "beta": {"connected": True},
        })
        check("暂停时显示「已暂停」", paused.display_status == "已暂停", paused.display_status)
        check("暂停时等级为 paused", paused.status_level == "paused", paused.status_level)
        check("暂停时 is_running 为假", not paused.is_running)

        # 完全没有会话
        empty = SessionState.from_json({})
        check("无会话时显示「未启动」", empty.display_status == "未启动", empty.display_status)
        check("无会话时等级为 stopped", empty.status_level == "stopped", empty.status_level)

        # 出错停止
        halted = SessionState.from_json({
            "name": "demo",
            "status": "halted-on-error",
            "lastError": "something broke",
            "alpha": {"connected": True},
            "beta": {"connected": True},
        })
        check("出错时等级为 error", halted.status_level == "error", halted.status_level)

    guard("状态判定", body)


# --------------------------------------------------------------------------- #
# 6.5：GUI 状态机（需求 3.6）
# --------------------------------------------------------------------------- #


def test_gui_states() -> None:
    section("6.5 GUI 状态机（状态映射 + 按钮策略）")

    def body() -> None:
        from .models import SessionState
        from .states import ALL_OPERATIONS, OPERATIONS, GuiState, classify

        # ---- classify：原始状态 -> 状态机状态 ----
        cases = [
            # (说明, loaded, exists, status, paused, 期望)
            ("还没读到状态", False, False, "", False, GuiState.UNKNOWN),
            ("读到但没有会话", True, False, "", False, GuiState.ABSENT),
            ("正常同步", True, True, "watching", False, GuiState.SYNCING),
            ("扫描中", True, True, "scanning", False, GuiState.SYNCING),
            ("暂存中", True, True, "staging-beta", False, GuiState.SYNCING),
            ("首次连接", True, True, "connecting-alpha", False, GuiState.CONNECTING),
            ("掉线重连", True, True, "connecting-beta", False, GuiState.CONNECTING),
            ("已暂停", True, True, "paused", True, GuiState.PAUSED),
            ("已断开", True, True, "disconnected", False, GuiState.DISCONNECTED),
            ("出错停止", True, True, "halted-on-error", False, GuiState.HALTED),
            # ⭐ waiting-for-rescan 归入「同步中」：Mutagen 每 5 秒自己重试，
            #    它**不该**被当成「已停止」——实测踩过的坑
            ("出错但自动重试", True, True, "waiting-for-rescan", False, GuiState.SYNCING),
        ]
        for label, loaded, exists, status, paused, expected in cases:
            got = classify(loaded=loaded, exists=exists, status=status, paused=paused)
            check(f"classify[{label}] -> {expected.value}", got is expected, got.value)

        # ---- ⭐ 核心区分：「有错误」不等于「已停止」----
        # 实测取样：maxEntryCount 超限
        retrying = SessionState.from_json({
            "name": "demo",
            "status": "waiting-for-rescan",
            "paused": False,
            "lastError": "alpha scan error: exceeded allowed entry count",
            "alpha": {"connected": True},
            "beta": {"connected": True},
        })
        check("自动重试中不显示「已停止」",
              "停止" not in retrying.display_status, retrying.display_status)
        check("自动重试中显示「重试」",
              "重试" in retrying.display_status, retrying.display_status)
        check("自动重试中等级为 warning（不是 error）",
              retrying.status_level == "warning", retrying.status_level)
        check("自动重试中 is_retrying 为真", retrying.is_retrying)
        check("自动重试中 is_halted 为假", not retrying.is_halted)
        check("自动重试中 gui_state 为 syncing",
              retrying.gui_state is GuiState.SYNCING, retrying.gui_state.value)

        # 对照：真的 halted 才该说「已停止」
        halted = SessionState.from_json({
            "name": "demo",
            "status": "halted-on-error",
            "lastError": "some hub error",
            "alpha": {"connected": True},
            "beta": {"connected": True},
        })
        check("halted-on-error 显示「已停止」",
              "停止" in halted.display_status, halted.display_status)
        check("halted-on-error 等级为 error",
              halted.status_level == "error", halted.status_level)
        check("halted-on-error 的 gui_state 为 halted",
              halted.gui_state is GuiState.HALTED, halted.gui_state.value)
        check("halted-on-error 的 is_retrying 为假", not halted.is_retrying)

        # ---- 按钮策略表的完备性与安全底线 ----
        missing = sorted(s.value for s in GuiState if s not in OPERATIONS)
        check("每个状态都有按钮策略", not missing, str(missing))

        unknown_buttons = sorted(
            f"{s.value}:{sorted(v - ALL_OPERATIONS)}"
            for s, v in OPERATIONS.items() if v - ALL_OPERATIONS
        )
        check("策略里没有未定义的按钮名", not unknown_buttons, str(unknown_buttons))

        no_list = sorted(s.value for s, v in OPERATIONS.items() if "list" not in v)
        check("List 在所有状态下都可点（出问题时的救命入口）", not no_list, str(no_list))

        # ⭐ 安全底线：只有「无会话」允许 Start。
        #    尤其「未知」绝不能允许——那时一点就报 already running。
        can_start = sorted(s.value for s, v in OPERATIONS.items() if "start" in v)
        check("只有「无会话」状态允许 Start",
              can_start == [GuiState.ABSENT.value], str(can_start))

        # ⭐ 会话存在时 Start 必须全面禁用（含掉线重连、已暂停、出错）
        exists_states = [s for s in GuiState if s not in (GuiState.UNKNOWN, GuiState.ABSENT)]
        bad_start = sorted(s.value for s in exists_states if "start" in OPERATIONS[s])
        check("会话存在时一律不能 Start（会报 already running）",
              not bad_start, str(bad_start))

        # halted：Pause 无意义（已经停了），出口是 Resume
        check("出错停止时 Pause 不可点", "pause" not in OPERATIONS[GuiState.HALTED])
        check("出错停止时 Resume 可点", "resume" in OPERATIONS[GuiState.HALTED])

        # 连接中 Flush 没意义
        check("正在连接时 Flush 不可点",
              "flush" not in OPERATIONS[GuiState.CONNECTING])

    guard("GUI 状态机", body)


# --------------------------------------------------------------------------- #
# 6.6：残留锁文件识别
# --------------------------------------------------------------------------- #


def test_stale_lock_detection() -> None:
    section("6.6 残留锁文件识别（project already running 的根因）")

    def body() -> None:
        import tempfile
        from pathlib import Path

        from .states import has_stale_lock, lock_path

        with tempfile.TemporaryDirectory() as tmp:
            yml = Path(tmp) / "proj.yml"
            yml.write_text("sync: {}\n", encoding="utf-8")
            lock = lock_path(yml)

            check("lock_path 就是 <yml>.lock",
                  lock == Path(str(yml) + ".lock"), str(lock))

            # 没有锁文件 -> 当然不是残留
            check("没有锁文件时不算残留",
                  not has_stale_lock(yml, session_exists=False))

            # ★ 实测的卡死场景：锁在、会话没了
            lock.write_text("proj_abcdef\n", encoding="utf-8")
            check("有锁但无会话 -> 判定为残留（Start 会永久失败）",
                  has_stale_lock(yml, session_exists=False))

            # 有锁 + 有会话 -> 锁是**正常**的，项目确实在跑
            check("有锁且有会话 -> 不算残留",
                  not has_stale_lock(yml, session_exists=True))

            # 模拟「带同步关机」：daemon 停了、会话没了，但锁还在
            check("模拟关机后（会话消失、锁仍在）-> 判定为残留",
                  has_stale_lock(yml, session_exists=False))

            # 用 project terminate 清掉锁之后 -> 恢复正常
            lock.unlink()
            check("锁被清掉后不再是残留",
                  not has_stale_lock(yml, session_exists=False))

    guard("残留锁文件识别", body)


# --------------------------------------------------------------------------- #
# 7：真实 Mutagen 严格解析
# --------------------------------------------------------------------------- #


def _full_config(alpha: Path, beta: Path) -> template.SessionConfig:
    """把 schema 里能安全开启的字段全部填上，用于验证字段清单。"""
    config = template.SessionConfig.with_defaults(
        name="selftest-probe",
        alpha=str(alpha),
        beta=str(beta),
    )
    config.set("hash", "xxh128")
    config.set("maxEntryCount", 10000)
    config.set("maxStagingFileSize", "1 GB")
    config.set("probeMode", "probe")
    config.set("scanMode", "accelerated")
    config.set("stageMode", "mutagen")
    config.set("compression.algorithm", "deflate")
    config.set("permissions.mode", "portable")
    config.set("permissions.defaultFileMode", "0644")
    config.set("permissions.defaultDirectoryMode", "0755")
    config.set("ignore.syntax", "mutagen")
    config.commands = {"ping": "echo pong"}
    config.hooks = {"beforeCreate": ["echo selftest-hook-ran"]}
    config.configuration_alpha = {"scanMode": "full"}
    return config


def test_mutagen_strict_parse(workdir: Path) -> None:
    section("7. 真实 Mutagen 严格解析（验证字段白名单）")

    exe = find_mutagen_exe()
    if exe is None:
        check("找到 mutagen 可执行文件", False,
              "未找到；请设置环境变量 MUTAGEN_EXE 或确认安装路径")
        return

    print(f"  mutagen: {exe}")
    cli = MutagenCLI(exe=exe)

    version = cli.version()
    check("mutagen version 可用", version.ok, version.error_message or version.output)
    print(f"  版本: {version.output.strip()}")

    alpha = workdir / "alpha"
    beta = workdir / "beta"
    alpha.mkdir(parents=True, exist_ok=True)
    beta.mkdir(parents=True, exist_ok=True)
    (alpha / "hello.txt").write_text("hello mutagen\n", encoding="utf-8")

    yml = workdir / "selftest.yml"
    config = _full_config(alpha, beta)
    template.write_yaml(config, yml, backup=False)

    print("  --- 生成的 yml ---")
    for line in yml.read_text(encoding="utf-8").splitlines():
        print(f"  | {line}")
    print("  ------------------")

    start = cli.project_start(yml)
    check(
        "Mutagen 严格解析通过并成功创建会话",
        start.ok,
        start.error_message or start.output,
    )

    if start.ok:
        listing = cli.sync_list_json()
        check("能读到会话 JSON 状态", listing.ok, listing.error_message or listing.output)
        if listing.ok:
            from .parser import parse_sync_list_json

            sessions = parse_sync_list_json(listing.output)
            names = [s.name for s in sessions]
            check("会话名出现在 sync list 中", "selftest-probe" in names, repr(names))
            check("能解析出结构化状态",
                  any(s.identifier and s.status for s in sessions),
                  repr([(s.name, s.status) for s in sessions]))

    terminate = cli.project_terminate(yml)
    check("能正常终止会话", terminate.ok, terminate.error_message or terminate.output)


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    # 必须先做：traceback 里若含「⚠」这类字符，在 GBK 控制台上会把自检打断
    make_console_safe()

    parser = argparse.ArgumentParser(description="MutagenGUI 核心层自检")
    parser.add_argument("--no-mutagen", action="store_true",
                        help="跳过第 5 步（不调用 Mutagen）")
    parser.add_argument("--keep-temp", action="store_true",
                        help="保留临时目录以便排查")
    args = parser.parse_args(argv)

    print("MutagenGUI 核心层自检")
    print(f"Python {sys.version.split()[0]}")
    print(f"字段规范：{len(schema.SESSION_FIELDS)} 个会话级字段，"
          f"{len(schema.ENDPOINT_OVERRIDABLE_FIELDS)} 个支持端点级覆盖")

    test_generation()
    test_roundtrip()
    test_validator()
    test_import_helpers()
    test_comment_preservation()
    test_status_classification()
    test_gui_states()
    test_stale_lock_detection()

    workdir: Path | None = None
    if args.no_mutagen:
        section("7. 真实 Mutagen 严格解析")
        print("  已按 --no-mutagen 跳过")
    else:
        workdir = Path(tempfile.mkdtemp(prefix="mutagengui-selftest-"))
        print(f"\n临时工作目录：{workdir}")
        try:
            test_mutagen_strict_parse(workdir)
        finally:
            if args.keep_temp:
                print(f"已保留临时目录：{workdir}")
            else:
                for attempt in range(3):
                    time.sleep(1)
                    try:
                        shutil.rmtree(workdir)
                        break
                    except OSError:
                        if attempt == 2:
                            print(f"提示：临时目录未能删除（被占用），可稍后手动删：{workdir}")

    print("\n" + "=" * 60)
    print(f"结果：{_passed} 通过，{_failed} 失败")
    if _failures:
        print("失败项：")
        for item in _failures:
            print(f"  - {item}")
    print("=" * 60)
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
