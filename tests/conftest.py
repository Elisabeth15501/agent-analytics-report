# -*- coding: utf-8 -*-
"""tests/conftest.py - pytest configuration for agent-analytics-report tests"""

import importlib.util
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


@pytest.fixture
def collector_module():
    """加载采集模块 collect_usage_data.py（每次测试独立加载）。"""
    return _load_module_from_path("cds_unit", SCRIPTS / "collect_usage_data.py")


@pytest.fixture
def report_module():
    """加载报告模块 generate_report.py（仅依赖标准库，隔离加载）。"""
    return _load_module_from_path("gr_unit", SCRIPTS / "generate_report.py")
