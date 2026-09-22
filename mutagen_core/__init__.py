"""MutagenGUI 核心层（与 UI 无关）。

这一层不 import 任何 GUI 库，可以单独运行与测试：

    python -m mutagen_core.selftest

模块划分
--------
schema      字段规范（yml 白名单与枚举取值，唯一真相来源）
models      Project / SessionState 等数据类
template    由表单数据生成 yml 文本，以及反向解析
validator   保存前的静态校验
cli         mutagen 命令行的 subprocess 封装
parser      解析 `mutagen sync list --template '{{json .}}'` 的输出
registry    projects.json（实例注册表）读写
"""

# 这里刻意**不写 __all__**：它只能把模块名列一遍，包里的子模块并不会因此被导入，
# 于是 Pylance/pyright 会逐个报「已在 __all__ 中指定，但在模块中不存在」
# （实测 7 条警告）。模块清单已经在上面 docstring 里列了，
# 用 `from mutagen_core import template` 这种显式写法即可。

__version__ = "0.1.0"
