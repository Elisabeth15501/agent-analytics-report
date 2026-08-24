# -*- coding: utf-8 -*-
"""L4 · 发布一致性（Publish Parity）— pytest + Allure 双可视化。

守护「本地 / GitHub / SkillHub」三端发布时的一致性，避免版本漂移与误发布：
  - metadata.json 版本 == config.json 版本（捕捉已知漂移 1.1.1≠1.1.2）
  - 两版本均为合法 semver（x.y.z）
  - 发布交付物齐全：SKILL.md / metadata.json / config.json / scripts / references /
    examples / LICENSE.md，且 scripts 下含两个核心脚本
  - .gitignore 闸门：测试文件不被忽略（能进 GitHub/SkillHub），
    敏感/产物文件必须被忽略（data.json / pricing.local.json / allure-results / report.* / 测试报告.*）

注：GitHub / SkillHub 远端包体一致性需在 CI 中拉取比对，本层只校验「本地源码一致性」，
为远端比对提供基线。
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

import allure

SKILL_DIR = Path(__file__).resolve().parent.parent

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

pytestmark = [pytest.mark.metadata, pytest.mark.contract]


def _load_json(name):
    return json.loads((SKILL_DIR / name).read_text(encoding="utf-8"))


def _is_gitignored(rel_path):
    """用 git check-ignore 判定某相对路径是否被忽略；git 不可用时抛异常由调用方 skip。"""
    proc = subprocess.run(
        ["git", "-C", str(SKILL_DIR), "check-ignore", "-q", rel_path],
        capture_output=True, text=True,
    )
    if proc.returncode == 0:
        return True     # 被忽略
    if proc.returncode == 1:
        return False    # 未忽略
    raise RuntimeError(f"git check-ignore 异常：{proc.stderr}")


# ── 版本一致性 ──────────────────────────────────────────────────────────────

@allure.feature("发布一致性（Publish Parity）")
@allure.story("版本同步")
@allure.title("metadata.json 版本 == config.json 版本（捕捉 1.1.1≠1.1.2 漂移）")
@allure.severity(allure.severity_level.CRITICAL)
def test_version_parity():
    meta_v = _load_json("metadata.json")["version"]
    cfg_v = _load_json("config.json")["version"]
    assert meta_v == cfg_v, (
        f"发布版本漂移：metadata.json={meta_v} 但 config.json={cfg_v}，"
        f"SkillHub upgrade 会据此误判，须同步为同一版本"
    )


@allure.feature("发布一致性（Publish Parity）")
@allure.story("版本格式")
@allure.title("两版本文件均为合法 semver（x.y.z）")
@allure.severity(allure.severity_level.NORMAL)
def test_version_semver():
    for name in ("metadata.json", "config.json"):
        v = _load_json(name)["version"]
        assert SEMVER.match(v), f"{name} 版本 {v!r} 不是合法 semver（x.y.z）"


# ── 交付物齐全 ──────────────────────────────────────────────────────────────

@allure.feature("发布一致性（Publish Parity）")
@allure.story("交付物齐全")
@allure.title("发布必需文件与核心脚本均存在")
@allure.severity(allure.severity_level.CRITICAL)
def test_required_artifacts_present():
    required = [
        "SKILL.md", "metadata.json", "config.json", "LICENSE.md",
        "scripts", "references", "examples",
        "scripts/collect_usage_data.py", "scripts/generate_report.py",
    ]
    missing = [p for p in required if not (SKILL_DIR / p).exists()]
    assert not missing, f"缺失发布交付物：{missing}"


# ── .gitignore 闸门 ──────────────────────────────────────────────────────────

@allure.feature("发布一致性（Publish Parity）")
@allure.story(".gitignore 闸门")
@allure.title("测试文件不被忽略（能进 GitHub / SkillHub），敏感/产物必须被忽略")
@allure.severity(allure.severity_level.CRITICAL)
def test_gitignore_gates():
    try:
        # 必须「不被忽略」：发布交付物与测试文件（能进 GitHub / SkillHub）
        must_not_ignore = [
            "tests/test_cost_math.py",
            "tests/test_report_generation.py",
            "tests/test_pricing_boundary.py",
            "tests/test_e2e_cli.py",
            "tests/test_publish_parity.py",
        ]
        for p in must_not_ignore:
            assert not _is_gitignored(p), f"{p} 被 .gitignore 忽略，测试体系将无法进发布包"

        # 敏感 / 生成产物 / 开发者内部文档必须「被忽略」（不进公开仓库）
        must_ignore = [
            "data.json",
            "pricing.local.json",
            "allure-results",
            "report.md",
            "测试报告.md",
            "TESTING.md",
            "TECH_DEBT.md",
        ]
        for p in must_ignore:
            assert _is_gitignored(p), f"{p} 未被 .gitignore 忽略，存在误发布风险"
    except RuntimeError as e:
        pytest.skip(f"git 不可用，跳过 .gitignore 闸门校验：{e}")
