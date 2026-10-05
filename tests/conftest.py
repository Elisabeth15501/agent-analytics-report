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
    """独立加载技能脚本（不污染测试模块命名空间，隔离全局状态）。"""
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
