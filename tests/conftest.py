# -*- coding: utf-8 -*-
"""tests/conftest.py - pytest configuration for agent-analytics-report tests"""

import importlib.util
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent          # .../agent-analytics-report
SCRIPTS = SKILL_DIR / "scripts"


def pytest_configure(config):
    for marker in ("smoke", "integration", "blackbox", "whitebox",
              "metadata", "contract", "privacy", "portability",
              "golden", "regression", "unit"):
        config.addinivalue_line("markers", f"{marker}: mark test as a {marker} test")


def _load_module_from_path(name, path):
    """独立加载技能脚本（不污染测试模块命名空间，隔离全局状态）。

    ── 扫描器说明（suspicious.dynamic_code_execution 告警澄清）──
    spec.loader.exec_module() 是 importlib 的标准公开 API（PEP 451 ModuleSpec
    的标准执行入口），用途是「按 ModuleSpec 把一个模块载入内存」。此处用它加载
    **本仓硬编码路径**的 scripts/*.py，是 pytest 隔离被测模块全局状态的官方推荐
    写法（被测脚本会改模块级全局，如定价表，必须每个用例独立加载）。
    它不是 eval()/exec()，不解析字符串、不编译任意表达式。

    安全边界（真正的约束，见下方 assert）：
      - name 与 path 均来自本文件顶部的模块常量 SKILL_DIR / SCRIPTS 及其派生，
        不接受命令行输入、不读环境变量、不联网；
      - 加载目标恒定在 <repo>/scripts/ 内，由 assert 强制校验；
      - 因此不存在「加载任意外部路径 → 执行其代码」的攻击链。
    请勿在无必要时改用 eval/exec，那才会引入真实的动态代码执行面。
    """
    # 路径不变量：把上面的安全边界从「注释约定」变成可执行的运行时防护。
    # 任何指向 scripts/ 之外的路径（例如误从参数/环境变量拼出的路径）在此直接失败。
    assert isinstance(path, (str, Path)) and str(SCRIPTS) in str(path), \
        "只允许加载本仓 scripts/ 下的模块，拒绝加载外部路径"
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_report_module():
    """加载 generate_report.py，临时把 scripts/ 挂上 sys.path。

    报告层现在 `from ca_core import ...`（时间区/常量/纯函数的单一真相源），
    而本文件用spec_from_file_location 单独加载它，Python 不会自动把 scripts/
    加进 sys.path，于是 import ca_core 失败（ModuleNotFoundError）。
    加载完立刻复原 sys.path——这些脚本会改模块级全局（如 ca_core 的定价表），
    留着路径会让后续测试 import 到别的进程内副本，破坏隔离。
    """
    injected = str(SCRIPTS) not in sys.path
    if injected:
        sys.path.insert(0, str(SCRIPTS))
    try:
        return _load_module_from_path("gr_unit", SCRIPTS / "generate_report.py")
    finally:
        if injected:
            try:
                sys.path.remove(str(SCRIPTS))
            except ValueError:
                pass


@pytest.fixture
def collector_module():
    """加载采集模块 collect_usage_data.py（每次测试独立加载）。"""
    return _load_module_from_path("cds_unit", SCRIPTS / "collect_usage_data.py")


@pytest.fixture
def report_module():
    """加载报告模块 generate_report.py（隔离加载，见 _load_report_module）。"""
    return _load_report_module()
