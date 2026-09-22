# MutagenGUI

用图形界面管理多个 Mutagen 同步项目，交互参考 **SSHFS-Win Manager**。

> **核心理念：一个 yml 文件 = 一个实例。所有操作都围绕 yml 文件进行。**

设计文档见 [`docs/design.md`](docs/design.md)。

---

## 当前状态

| 层 | 状态 | 说明 |
|---|---|---|
| `mutagen_core/` 核心层 | ✅ 完成，自检 **31/31 通过** | 与 UI 完全解耦，可独立运行与测试 |
| `ui/` 界面层 | ✅ v1 骨架完成，构建检查通过 | PySide6；暗色主题对标 SSHFS-Win Manager |
| 打包 | ⏳ 待开发 | PyInstaller |

三项关键验证：

1. **生成的 yml 能被真实 Mutagen 0.18.1 严格解析** —— `selftest.py` 会把全部 20 个字段写进 yml 交给 Mutagen 校验
2. **能正确导入现有 yml** —— 用 `D:\code\mutagen.yml` 实测：会话名、两端、7 条自定义命令全部解析正确，且无未知字段
3. **注释不丢** —— 对同一份真实文件做「改值 + 保存」，注释数 **104 → 104**、行数 **144 → 144** 完全守恒，只有目标字段被改写

---

## 目录结构

```
MutagenGUI/
├── docs/
│   └── design.md            # 设计文档（含实测字段白名单，附录 C）
├── README.md
├── requirements.txt         # Python 依赖
├── app.py                   # 程序入口
├── config.py                # 全局默认值与路径
├── .config/                 # 程序数据（首次启动自动创建）
│   ├── projects.json        #   实例注册表
│   ├── settings.json        #   用户偏好（改过 Settings 才生成）
│   └── app.lock             #   单实例锁
├── ymls/                    # 新建实例时的默认 yml 目录
├── mutagen_core/            # 核心层（不依赖任何 GUI 库）
│   ├── schema.py            # ⭐ 字段规范：yml 白名单与枚举取值（唯一真相来源）
│   ├── models.py            # Project / SessionState 数据类
│   ├── template.py          # 表单数据 <-> yml 文本，含带备份的落盘
│   ├── validator.py         # 保存前的静态校验
│   ├── cli.py               # mutagen 命令行的 subprocess 封装
│   ├── parser.py            # 解析 sync list 的 JSON 输出
│   ├── registry.py          # projects.json 读写 + 单实例锁
│   ├── settings.py          # 用户设置持久化
│   ├── sshconfig.py         # 只读解析 ~/.ssh/config + 连接测试
│   └── selftest.py          # 核心层自检脚本
└── ui/                      # 界面层
    ├── theme.py             # 配色 / 字体 / 尺寸 + 全局 QSS（需求 6.6）
    ├── icons.py             # QPainter 绘制的矢量图标
    ├── widgets.py           # 可复用组件（侧边按钮、动态列表、实例行、空状态）
    ├── main_window.py       # 主窗口：实例列表 + 右侧模式按钮
    ├── add_dialog.py        # Add Connection / Edit 对话框（BASIC / ADVANCED）
    ├── op_dialog.py         # 实例操作对话框（命令按钮 + yml 编辑 + 日志）
    ├── yml_editor.py        # 双模式 yml 编辑器（查看 / 编辑）
    ├── settings_dialog.py   # Settings 对话框
    └── tasks.py             # 后台任务（轮询 / 命令 / 连接测试）
```

---

## 运行程序

```powershell
cd D:\MutagenGUI
& 'D:\miniconda3\envs\mutagen-gui\python.exe' app.py
```

辅助开关（用于无界面环境下自检）：

| 开关 | 用途 |
|---|---|
| `--check` | 离屏自检：静态检查 → 构建主窗口与三个对话框 → 滚轮保护 → yml 丢失检测，然后退出（秒级） |
| `--smoke` | 离屏跑一小段事件循环，连初始化与首次轮询一起验证 |
| `--version` | 打印版本 |

### 配置存放位置

程序数据放在**项目目录下的 `.config`**（便携式布局，整个程序目录可以整体拷走）：

| 文件 | 内容 |
|---|---|
| `.config/projects.json` | 实例注册表（**只记「yml 在哪」**，配置本身在 yml 里）|
| `.config/settings.json` | Settings 对话框里改过的偏好（存在时才生成）|
| `.config/app.lock` | 单实例锁（内容是本进程 PID）|

需要放到别处时，设置环境变量 `MUTAGENGUI_CONFIG_DIR`。

> 早期版本把数据放在 `%APPDATA%\MutagenGUI`。首次启动会把它里面的配置**复制**到
> `.config`（复制而非移动，旧文件留作备份），并在状态栏提示迁移结果。

---

## 环境准备

conda 独立环境（**不要用 base**）：

```powershell
conda create -n mutagen-gui python=3.11 -y
conda activate mutagen-gui
pip install -r requirements.txt
```

本机环境信息：

| 项 | 值 |
|---|---|
| conda | `D:\miniconda3\Scripts\conda.exe` |
| 环境路径 | `D:\miniconda3\envs\mutagen-gui` |

前置要求：**Mutagen 0.18.1** 已安装，且 `MUTAGEN_SSH_PATH` 指向 Git for Windows 的 `usr\bin`
（Mutagen 在 Windows 上不使用系统内置 OpenSSH）。

---

## 运行自检

```powershell
cd D:\MutagenGUI
& 'D:\miniconda3\envs\mutagen-gui\python.exe' -m mutagen_core.selftest
```

它会依次检查：

1. 默认配置能生成 yml 文本
2. 「生成 → 解析」往返后字段一致
3. 校验器能拦住非法输入（非法会话名 / 未知字段 / 非法枚举 / 端点级覆盖越界 / 端点格式）
4. 多会话识别与未知字段探测（导入场景）
5. **真实 Mutagen 严格解析** —— 用两个临时目录当端点，跑完自动清理

跳过第 5 步（不调用 Mutagen）：

```powershell
... -m mutagen_core.selftest --no-mutagen
```

保留临时目录以便排查：

```powershell
... -m mutagen_core.selftest --keep-temp
```

---

## 核心层模块职责

| 模块 | 职责 | 关键点 |
|---|---|---|
| `schema` | yml 字段的**唯一真相来源** | 20 个会话级字段、10 个支持端点级覆盖、8 个钩子 |
| `states` | **会话状态机 + 按钮策略** | ⭐ 状态定义、`classify()`、`OPERATIONS` 表、`has_stale_lock()`。**界面按钮能不能点只查这里** |
| `models` | `Project`（实例）、`SessionState`（运行态） | 运行态可从 Mutagen JSON 直接反序列化 |
| `template` | 生成 / 解析 yml | 所有配置写 `sync.defaults`，只有 alpha/beta 写在会话下 |
| `validator` | 保存前静态校验 | 白名单 + 枚举 + 类型 + 端点格式 + 端点级覆盖限制 |
| `cli` | 调用 mutagen | 统一处理 `CREATE_NO_WINDOW`、UTF-8、`-f` 参数 |
| `parser` | 解析 `sync list` JSON | 容错：解析失败返回空列表，不影响其他实例 |
| `registry` | `projects.json` | 原子写入；OS 文件锁实现单实例保护 |
| `sshconfig` | 只读解析 `~/.ssh/config` + 连接测试 | **不修改**用户的 SSH 配置（写坏会导致所有远程连接失效）；ssh 报错翻译成「原因 + 怎么做」（`diagnose_ssh_failure`）|
| `settings` | 用户偏好（`settings.json`）| 轮询间隔、默认 yml 目录、Mutagen 路径、SSH 别名 |
| `console` | 控制台编码健壮性 | GBK 控制台下打印 `⚠` 会抛异常打断流程（实测踩过），统一改为 `errors="replace"` |

---

## ⭐ 关键实测结论（不要凭记忆改）

这些结论都来自对 **本机 Mutagen 0.18.1** 的实测，是代码里多处逻辑的依据：

| # | 结论 | 影响 |
|---|---|---|
| 1 | Mutagen 对 yml 是**严格解析**，未知字段直接报错 | 生成端必须做白名单校验；`validator.py` 的存在理由 |
| 2 | `compression` 是**嵌套结构** | 必须写 `compression.algorithm`，不能写扁平字符串 |
| 3 | `symlink.mode` **不允许**端点级覆盖 | 放进 `configurationAlpha` 会报 `symbolic link mode cannot be specified on an endpoint-specific basis` |
| 4 | 会话名**只允许** `[A-Za-z0-9-]` | 下划线、点号、空格都会报 `invalid name character` |
| 5 | `sync list --template '{{json .}}'` 的 `status` 是**小写枚举**（如 `watching`） | 不是人类可读的 "Watching for changes" |
| 6 | 无冲突时 JSON 里**没有** `conflicts` 字段 | `parser` 对多种形态做了兼容 |
| 7 | Mutagen 在 Windows 上**不使用系统内置 OpenSSH** | 依赖 `MUTAGEN_SSH_PATH` |
| 8 | `project start` 用 yml 旁的 **`<yml>.lock`** 判断「项目是否在运行」 | `sync terminate` 会留下残留锁 → 项目永久起不来。**终止必须用 `project terminate`**（见下节）|
| 9 | `waiting-for-rescan` 是「出错但**在自动重试**」，不是「已停止」 | 与 `halted-on-error` 必须分开显示，否则误导用户去人工处理 |
| 10 | `ignore.vcs` 内置忽略**只有 5 个**目录：`.git`/`.svn`/`.hg`/`.bzr`/`_darcs`，⚠️ **不含 `CVS`** | 用 CVS 的老项目得自己在 `ignore.paths` 里加 |
| 11 | `ignore.vcs` **不写时 Mutagen 默认为「不忽略」**（`.git` 会同步），而 GUI 默认写 `true`（忽略）| **有意相反**。同步 `.git` 要付三重代价：首次传整个对象库、每次 `git gc` 重传 pack、两端同时 commit 互相覆盖。`selftest` 有断言钉住这个决定 |
| 12 | ssh 失败**必须翻译**再给用户看 | 直接把 `Host key verification failed.` 丢出去用户看不懂（真被问过）。`diagnose_ssh_failure` 覆盖 6 类常见失败，各给不同处理办法；**原始报错保留在 tooltip** |

> 修改 `schema.py` 后**必须重跑自检**第 5 步 —— 它就是这套字段清单的自动化回归测试。

---

## ⭐ 会话状态机（改按钮逻辑前必看）

界面上「哪些按钮能点」**只由会话状态决定**。定义在 `mutagen_core/states.py`，
**那是唯一定义处**——要改按钮策略只改那里的 `OPERATIONS` 表。

| 状态 | 含义 | 会话存在 | 需要人工 |
|---|---|---|---|
| `UNKNOWN` | 还没读到状态——**不知道**会话存不存在 | 未知 | — |
| `ABSENT` | 读到了：没有会话 | 否 | — |
| `CONNECTING` | 正在连接（首次连接 / 掉线重连）| 是 | **不需要**，daemon 自动重连 |
| `SYNCING` | 工作中（含扫描 / 暂存 / `waiting-for-rescan`）| 是 | 不需要 |
| `PAUSED` | 已暂停 | 是 | 不需要 |
| `HALTED` | 出错**已停止**，不会自愈 | 是 | **需要**（点 Resume）|
| `DISCONNECTED` | 已断开，不在重试 | 是 | 视情况 |

**按钮矩阵**（完整版见 [`docs/design.md`](docs/design.md) 3.6）：

```
              Start  Stop  Restart  Monitor  Pause  Resume  Flush  List
UNKNOWN         ✗     ✗      ✗        ✗       ✗      ✗      ✗      ✓
ABSENT          ✓     ✗      ✗        ✗       ✗      ✗      ✗      ✓
CONNECTING      ✗     ✓      ✓        ✓       ✓      ✗      ✗      ✓
SYNCING         ✗     ✓      ✓        ✓       ✓      ✗      ✓      ✓
PAUSED          ✗     ✓      ✓        ✓       ✗      ✓      ✗      ✓
HALTED          ✗     ✓      ✓        ✓       ✗      ✓      ✗      ✓
DISCONNECTED    ✗     ✓      ✓        ✓       ✗      ✓      ✗      ✓
```

### 三条最容易搞错的规则

| 规则 | 为什么 |
|---|---|
| **`UNKNOWN` 时只留 `List`** | 把「不知道」当成「没有会话」，`Start` 就成唯一可点项，一点就报 `already running`（踩过）|
| **会话存在时 `Start` 一律灰着**（含掉线重连）| 会话并没有消失，再点只会报 `already running` |
| **`HALTED` 时 `Pause` 灰、`Resume` 亮** | 已经停了，暂停没意义；出口是 `Resume` |

> **实现要求**：`op_dialog._update_header()` 必须**按表统一赋值**，不许逐个按钮写 `if`
> ——以前那样写时，补「状态未知禁用全部按钮」漏掉了重新启用 `Monitor`，它**永久变灰**。
> 现在表驱动 + 断言防漂移（界面按钮集合必须等于 `ALL_OPERATIONS`）。

### 另一个容易搞错的地方：`waiting-for-rescan` 不是「已停止」

实测（`maxEntryCount` 超限触发）：

```
status    = "waiting-for-rescan"        ← 不是 halted-on-error
lastError = "alpha scan error: exceeded allowed entry count"
人类可读   = "Status: Waiting 5 seconds for rescan"
```

**Mutagen 每 5 秒自己重扫，根本没停**，用户什么都不用做。
所以标签拆成两个：`waiting-for-rescan` → 「出错，自动重试中…」（警告色），
`halted-on-error` → 「出错已停止」（危险色）。**只有后者需要人工介入。**

### ⚠️「project already running」但实际没有会话 —— 残留锁文件

**这是最容易让人怀疑人生的一个坑**（已实测复现并定位）：

```
$ mutagen project start -f <yml>
Error: project already running          ← 它说在运行

$ mutagen sync list
No synchronization sessions found       ← 却一个会话都没有

$ mutagen daemon stop; mutagen project start -f <yml>
Error: project already running          ← 重启 daemon 也没用！

$ dir <yml 所在目录>
<yml>.lock                              ← ★ 就是它
```

**根因**：Mutagen 用 yml 旁边的 `<yml>.lock`（内容是项目标识符 `proj_xxx`）
判断「项目是否已在运行」，而**这个锁只有 `project terminate` 会清理**。

### ⚠️ 触发条件比想象中普遍得多（实测模拟关机）

```
$ mutagen project start -f <yml>        # 建会话 + 生成锁
$ Test-Path <yml>.lock
True
$ mutagen daemon stop                   # 模拟关机（**没有**做 project terminate）
$ Test-Path <yml>.lock
True                                     ← ★ 锁幸存下来了
$ mutagen project start -f <yml>
Error: project already running           ← ★ 卡死
```

**结论：只要在同步运行时关机 / 重启电脑，下次开机 `project start` 就永久失败。**
（daemon 重启后会话没了，但锁还在。）

三个触发途径：

| 触发 | 常见度 |
|---|---|
| 同步运行时**关机 / 重启 / 强制断电** | ★★★ 最常见 |
| daemon 异常退出 / 崩溃 | ★★ |
| 用 `sync terminate` 绕过项目终止会话 | ★（本项目曾如此，已修）|

**怎么确认是这个坑**：

| 现象 | 是残留锁吗 |
|---|---|
| `already running` + `sync list` 为空 | ✅ 是 |
| `already running` + `sync list` 里**有**该会话 | ❌ 只是状态读取滞后 |
| 重启 daemon 后仍然报 | ✅ 是（状态不在 daemon 里）|
| 换个会话名就能启动 | ✅ 是 |

**解法**：

```powershell
mutagen project terminate -f <yml>      # 没有会话也能成功，并会删掉锁文件
```

### GUI 的两道防线

| 防线 | 时机 | 行为 |
|---|---|---|
| **① 主动标记** | 每 5 秒轮询 | 检出「有锁 + 无会话」→ 列表行显示琥珀色 **「⚠ 状态残留」** 徽章，摘要写「状态残留（点 Start 会失败）」，tooltip 说明成因。**让你在点 Start 之前就知道** |
| **② 失败后引导** | 点 Start 报 `already running` 时 | 重读状态 → 确认无会话 → 弹「项目状态残留」对话框，提供【仅清理锁文件】/【清理并启动】 |

检测逻辑：`mutagen_core.states.has_stale_lock(yml_path, session_exists=...)`
（纯函数，`selftest` 覆盖 6 项断言）。

> **为什么必须有主动标记**：只靠 ② 的话，用户会先收到一句莫名的
> `already running`，而界面状态仍显示「未启动」——完全对不上，只能靠猜。
> 这个问题**每次带同步关机都会复发**，不是一次性事故，所以必须前置暴露。

> ### ⚠️ 开发铁律
>
> **终止会话一律用 `project terminate -f <yml>`，不要用 `sync terminate <name>`。**
>
> 唯一例外：会话本身就是绕过项目建的（「先建会话、等远端上线」流程用的是
> `sync create`），此时两种都要试一遍。
>
> 本项目曾在「删除实例」流程里用了 `sync terminate` —— 已修。

## 已定决策

| 决策 | 结论 |
|---|---|
| 实例模型 | **一个 yml = 一个实例**；v1 只支持**单会话 yml**，多会话 yml 导入后为只读（方案 A）|
| 连接信息 | **SSH 别名优先** —— GUI 只读 `~/.ssh/config`，不修改它 |
| 状态刷新 | 每 5 秒轮询一次 `sync list`，用 JSON 模板拿结构化数据 |
| 关闭 GUI | **不停止同步**（会话由 Mutagen daemon 独立维护），重开 GUI 自动恢复 |

---

## 已知限制（v1）

| 限制 | 说明 |
|---|---|
| 被改动那条列表项的注释 | 例如修改了 `ignore.paths` 里的某一项，该项的行尾注释会丢。**列表内容不变时不受影响** |
| 一个 yml 只支持一个同步会话 | 方案 A；多会话 yml 导入后标记为只读 |
| 只从本地编辑 yml | 远程手改 yml 后，需要重启 GUI 才会读到新内容 |
| 主题只有暗色 | 需求 6.6 规定的 v1 唯一主题 |

### 注释是怎么保住的

关键在 `template.write_yaml()`：

* 目标文件已存在且 `ruamel.yaml` 可用时，**在原文档上就地更新托管字段**，而不是重建
* 写入前先做**值等价判断**（`_assign`）——内容没变就完全不碰那个节点，
  于是附着其上的行尾注释、列表项注释都原样保留
* 表单没覆盖的字段（`customUnknownField` 之类）也不会被删，因为代码只动自己认识的键
* **合并底本的优先级**：先用「来源文件」（导入 / 编辑时选的那个），再用「目标文件」。
  于是「导入 A → 改名 → 另存为 B」时 B 会继承 A 的注释，而不会因为 B 不存在就整份重建
* 只有「找不到任何底本 / 底本解析失败 / 没装 ruamel」三种情况才退化为整份重建

对应的回归测试在 `selftest.py` 的第 5 节（14 条断言覆盖改值、增字段、清字段、备份）。

## 健壮性处理

| 场景 | 行为 |
|---|---|
| **实例的 yml 被外部删除或移动** | 启动时与**每次状态轮询**都检测：整行置灰 + 红色「⚠ 文件丢失」徽章 + 显示期望路径。点击该行可 **移除条目** 或 **重新定位文件**（重新定位会先解析成功才写注册表，不会指向一个用不了的文件）|
| 注册表文件损坏 | 自动改名为 `.corrupt-<pid>` 留档并返回空列表，不让程序起不来 |
| 单实例锁文件不可写 | 降级为「不做单实例保护」并打印警告，不阻断启动 |
| 已有另一个实例在运行 | 明确提示后退出。基于 OS 文件锁，进程退出自动释放，**不会留下需要手动清理的死锁文件** |
| 后台任务所属窗口被关闭 | 信号发射做容错；任务引用由 `tasks.submit()` 持有，避免被 GC 提前回收 |
| 表单未覆盖的 yml 字段 | 原样保留，不会被删除（`write_yaml` 只动它认识的键）|
| 状态轮询失败 | 单次失败只影响状态栏提示，不影响列表与已有内容 |

### 路径脱敏（截图分享时不泄露用户名）

界面上**只读展示**的路径一律经 `config.display_path()` 折叠：

| 原始 | 展示 |
|---|---|
| `C:\Users\somebody\.ssh\config` | `~/.ssh/config` |
| `C:\Users\somebody\projects\foo` | `~/projects/foo` |
| `C:\Users\someone_else\x` | `C:\Users\<user>\x` |
| `D:\code\TRELLIS.2` | 原样（本来就不含用户名）|

**为什么**：用户把界面截图贴出去（提 issue、写博客、发群）时，
`C:\Users\<名字>\` 会把 Windows 用户名一起带出去。

> ⚠️ **刻意不脱敏的两处**：
>
> 1. **可编辑的输入框**（Settings 的目录、Add Connection 的路径/yml 保存路径）
>    必须是**真实路径** —— 否则用户一点保存就会把 `~` 写进 yml，路径直接失效
> 2. **「复制路径」给的也是真实值** —— 那是拿去用的，不是拿去看的
>
> 所以截图时注意避开这两处。

断言：`app.py --check` 的 `_check_path_masking`（8 条，含「真实 SSH 配置路径里不含用户名」）。

### 四道自检，缺一不可

| 检查 | 覆盖什么 | 抓不到什么 |
|---|---|---|
| `python -m compileall` | 语法错误 | 拼错的名字、漏掉的导入 |
| `python app.py --check` | **静态检查（pyflakes）** + 构建期错误 + 关键初始状态 + 路径脱敏 + 用户名泄露扫描 | 运行期逻辑分支 |
| `python -m mutagen_core.selftest` | 核心层逻辑 + **真实 Mutagen 严格解析** | UI 层 |
| **类型检查（pyright）** | 类型/属性错误、漏判 `None`、PySide6 API 用法 | 逻辑正确性 |

> **为什么必须做静态检查（pyflakes）**：`compileall` 只查语法，构建窗口也只走构造路径。
> 实测踩过一次：`op_dialog.py` 里 `_offer_remote_offline()` 用了 `QMessageBox` 但**忘了导入**，
> 平时完全看不出来——**只有远端连不上、走到那条错误分支时才会崩**（NameError）。
> 现在 `--check` 会先跑一遍 pyflakes，把这类问题挡在启动前。
>
> 没装 pyflakes 时会显示 `SKIP`（不影响其他检查）：`pip install pyflakes`

**类型检查命令**（`pyright` 与 VS Code 的 Pylance 同源，结果一致）：

```powershell
& 'D:\miniconda3\envs\mutagen-gui\python.exe' -m pyright `
    --pythonpath 'D:\miniconda3\envs\mutagen-gui\python.exe' `
    app.py config.py mutagen_core ui
```

> ⚠️ **`--pythonpath` 不能省**：不带它，pyright 找不到 PySide6，会先把三行
> `import PySide6.*` 报成 `reportMissingImports`，**真正的问题反而被淹没**。
>
> **基准：`0 errors, 0 warnings, 0 informations`。** 改动后请保持这个数字。

**首次全量清理时修掉的几类问题**（44 错误 + 14 警告 → 0）：

| 类别 | 典型写法 | 正确写法 |
|---|---|---|
| **一行引发 31 个错误** | `kwargs: dict[str, object]` 后 `subprocess.run(argv, **kwargs)` | 用 `dict[str, Any]` —— `object` 会与**每个**具名参数都不兼容 |
| PySide6 存根缺口 | `process.setCreateProcessArgumentsModifier(...)`（存根里搜不到 `CreateProcess`）| 用 `getattr(process, "…", None)`，顺带完成「API 不存在就跳过」的降级 |
| Qt 类型收窄 | `event.type() == QEvent.Type.Wheel` 后取 `angleDelta()` | 改 `isinstance(event, QWheelEvent)` —— 类型检查器才认 |
| `QLayoutItem \| None` | `layout.itemAt(i).widget()` / `takeAt(0).widget()` | 走 `widgets.layout_widget()` / `widgets.clear_layout()`（已封装） |
| `QByteArray` 转换 | `bytes(qba)` 或 `qba.data()`（后者可能是 `memoryview`，没有 `.decode`）| `bytes(qba.data())` —— 两步都做才既类型干净又运行正确 |
| 冗余 `__all__` | `__init__.py` 里只列模块名却不导入 | 直接删掉（模块清单写在 docstring 里更有用）|

### ⭐ 一个容易混淆的区别：创建时 vs 运行中

这两件事行为完全不同，是实测确认的：

| 阶段 | 远端不可达时 | 原因 |
|---|---|---|
| **创建会话**（`project start`）| ❌ 直接失败，**不留下任何会话** | Mutagen 必须连上两端才能**建立**会话 |
| **会话已存在后掉线** | ✅ 状态变 `connecting-alpha/beta`，daemon **持续自动重连** | Mutagen 自带的重连机制 |

所以「远程没开机就点 Start」是不会自动重连的——因为会话压根没建起来。

**GUI 的应对**：识别连接类错误后弹出说明，并提供一键方案「**建好会话，等远端上线自动开始**」：

```python
# 先暂停创建（不尝试连接），再 resume 一次。
# resume 必然失败，但失败后会话会停留在 connecting-*，由 daemon 一直重试到远端上线。
_run_sequence([
    (["project", "start", "--paused", "-f", yml], "以暂停状态创建会话"),
    (["sync", "resume", name], "开始连接", True),   # True = 预期失败，日志换友好说法
])
```

**状态显示上的差异**（`SessionState.status_level`）：

| 状态 | 显示 | 颜色 |
|---|---|---|
| `connecting-beta`（首次连接）| 正在连接远程… | 琥珀 ⚠ |
| `connecting-beta`（曾同步过）| 正在**重连**远程… | 琥珀 ⚠ |
| `disconnected` / 某端未连接 | 已断开 | 红 |
| `watching` | 同步中 | 绿 |

## 后续计划

1. 用真实窗口跑一遍端到端流程（新建 → 启动 → 暂停 → 恢复 → 停止 → 删除）
2. 补齐需求里的次要项：列表排序（F11.5）、环境引导细节（F8）
3. PyInstaller 打包成单文件 exe
4. （可选）改用 `ruamel.yaml` 以支持保留 yml 注释
