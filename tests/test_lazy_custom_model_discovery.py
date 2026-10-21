# -*- coding: utf-8 -*-
"""A3 回归：ca_core 导入期不读取 ~/.workbuddy/models.json（懒加载）。

clawscan / skillspector 的 SDI-1 关注点：在生成 WorkBuddy / Claude / Codex 等
其它产品报告时，不应越权读取用户模型配置。修复后 discover_custom_models() 仅在
涉及自建模型（custom_local）时由聚合层按需触发。
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
for _p in (str(SKILL_DIR), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.mark.unit
def test_import_does_not_eagerly_discover_models():
    """在全新解释器里 import ca_core，断言导入期未触发发现（缓存为 None、
    DISCOVERED_LOCAL 为空）——即未读取 ~/.workbuddy/models.json。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = f"{SCRIPTS_DIR};{SKILL_DIR}"
    code = (
        "import ca_core\n"
        "print('CACHE', ca_core._DISCOVERED_CACHE)\n"
        "print('LOCAL', len(ca_core.DISCOVERED_LOCAL))\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(SKILL_DIR), env=env)
    assert r.returncode == 0, f"import 失败: {r.stderr[-600:]}"
    assert "CACHE None" in r.stdout, "导入期不得触发发现（缓存应为 None）"
    assert "LOCAL 0" in r.stdout, "导入期不得读取 models.json（DISCOVERED_LOCAL 应为空）"
