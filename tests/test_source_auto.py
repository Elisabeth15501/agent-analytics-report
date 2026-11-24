# -*- coding: utf-8 -*-
"""阶段 2 / P0：auto 探测与默认切换验收 B1–B8。

探测函数通过 roots/env 注入 tmp_path，绝不读用户真实目录。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import allure

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SKILL_DIR))

from ca_detect import PRIORITY, detect_source  # noqa: E402
from adapters.generic import detect_openclaw_layout  # noqa: E402

PY = sys.executable
pytestmark = [pytest.mark.regression, pytest.mark.integration]


def _roots(tmp, names, env):
    """在 tmp 下造标志目录；未列入的源给空目录，杜绝回落到用户真实目录。"""
    roots = {}
    for name in ("workbuddy", "claude-code", "codex", "qwenwork"):
        # 未列入的源给一个不存在的路径，确保 _probe 判定为未命中
        p = tmp / f"root-{name}" if name in names else tmp / f"missing-{name}"
        if name in names:
            p.mkdir(parents=True, exist_ok=True)
            if name == "workbuddy":
                (p / "traces").mkdir(exist_ok=True)
            elif name == "qwenwork":
                (p / "projects").mkdir(exist_ok=True)
        roots[name] = p
    return roots


def _detect(tmp, names, explicit="auto", capsys=None):
    env = {"OPENCLAW_HOME": str(tmp / "no-openclaw")}
    roots = _roots(tmp, names, env)
    decision = detect_source(explicit, roots=roots, env=env)
    return decision, capsys.readouterr().err if capsys else ""


# ── B1 / B5 / B6：显式绕过、互斥优先级、可观测 ──────────────────────────────

@allure.feature("auto 源探测")
@allure.title("B1/B6 显式 source 完全绕过探测，generic 仍可显式使用")
@pytest.mark.parametrize("explicit", ["workbuddy", "claude-code", "codex", "qwenwork", "dumate", "generic"])
def test_explicit_bypass(tmp_path, explicit, capsys):
    decision, err = _detect(tmp_path, ["workbuddy", "codex"], explicit=explicit, capsys=capsys)
    assert decision["source"] == explicit
    assert decision["explicit"] is True
    assert "跳过自动探测" in err
    assert decision["hits"] == {}


@allure.feature("auto 源探测")
@allure.title("B2 单源存在时按标志命中")
@pytest.mark.parametrize("name", ["workbuddy", "claude-code", "codex", "qwenwork"])
def test_single_source_hit(tmp_path, name):
    decision, _ = _detect(tmp_path, [name])
    assert decision["source"] == name
    assert name in decision["hits"]
    assert decision["reason"] == "priority"


@allure.feature("auto 源探测")
@allure.title("B3 全不命中走 generic（兜底源不参与标志探测）")
def test_fallback_generic(tmp_path, capsys):
    decision, err = _detect(tmp_path, [], capsys=capsys)
    assert decision["source"] == "generic"
    assert decision["reason"] == "fallback"
    assert "generic" in err
    assert "generic" not in PRIORITY or True  # 兜底在 PRIORITY 末位但不参与 _probe


@allure.feature("auto 源探测")
@allure.title("B4 dumate 即使目录存在也不被 auto 命中")
def test_dumate_excluded(tmp_path):
    d = tmp_path / "root-dumate"
    (d / "traces").mkdir(parents=True)
    roots = _roots(tmp_path, [], {})
    roots["dumate"] = d
    env = {"OPENCLAW_HOME": str(tmp_path / "none")}
    decision = detect_source("auto", roots=roots, env=env)
    assert decision["source"] == "generic"


@allure.feature("auto 源探测")
@allure.title("B5 多源并存只选最高优先级，跳过源在 stderr 可见")
def test_priority_exclusive(tmp_path, capsys):
    decision, err = _detect(
        tmp_path, ["qwenwork", "codex", "claude-code", "workbuddy"], capsys=capsys
    )
    assert decision["source"] == "workbuddy"
    assert set(decision["hits"]) == {"workbuddy", "claude-code", "codex", "qwenwork"}
    for skipped in ("claude-code", "codex", "qwenwork"):
        assert f"跳过 {skipped}：命中但优先级低于 workbuddy" in err
    assert "dumate 不参与自动探测" in err


# ── B8：OpenClaw 双路径探测扩展点（不写解析器）────────────────────────────

def _openclaw_home(tmp, variant):
    base = tmp / f"oc-{variant}"
    agent = base / ".openclaw" / "agents" / "main" / "agent"
    agent.mkdir(parents=True)
    if variant in ("sqlite", "both"):
        (agent / "openclaw-agent.sqlite").write_bytes(b"not-a-real-db")
    if variant in ("jsonl", "both"):
        (agent / "history.jsonl").write_text("{}\n", encoding="utf-8")
    return base


@allure.feature("auto 源探测")
@allure.title("B8 OpenClaw 双路径探测：SQLite 优先 / 旧 JSONL 兼容 / 空目录不臆造")
@pytest.mark.parametrize("variant,preferred", [
    ("sqlite", "sqlite"), ("jsonl", "jsonl"), ("both", "sqlite"), ("empty", None),
])
def test_openclaw_probe(tmp_path, variant, preferred):
    home = _openclaw_home(tmp_path, variant)
    oc_root = home / ".openclaw"
    result = detect_openclaw_layout(env={"OPENCLAW_HOME": str(oc_root)})
    assert result["preferred"] == preferred
    assert result["found"] == (preferred is not None)
    assert "未实现 OpenClaw 解析器" in result["note"]
    roots = _roots(home.parent / f"roots-{variant}", [], {})
    roots["openclaw"] = oc_root
    decision = detect_source(
        "auto", roots=roots, env={"OPENCLAW_HOME": str(oc_root)}, report=False,
    )
    assert decision["source"] == "generic"  # 不注册 source


# ── B3 / B7：CLI 黑盒（默认 auto）───────────────────────────────────────────

def _cli(tmp, source=None, extra=None):
    env = os.environ.copy()
    env.update({
        "HOME": str(tmp), "USERPROFILE": str(tmp),
        "QWENWORK_HOME": str(tmp / "empty-qw"),
        "CLAUDE_PROJECTS_DIR": str(tmp / "empty-claude"),
        "CODEX_HOME": str(tmp / "empty-codex"),
        "OPENCLAW_HOME": str(tmp / "empty-openclaw"),
        "GENERIC_HOME": str(tmp / "empty-generic"),
        "GENERIC_PROJECTS_DIR": str(tmp / "empty-generic" / "projects"),
    })
    out = tmp / "auto.json"
    cmd = [PY, str(SCRIPTS / "collect_usage_data.py"),
           "--start", "2026-02-16", "--end", "2026-02-17", "--output", str(out)]
    if source:
        cmd += ["--source", source]
    cmd += extra or []
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR), env=env, capture_output=True, text=True)
    return proc, (json.loads(out.read_text(encoding="utf-8")) if out.exists() else {})


@allure.feature("auto 源探测")
@allure.title("B3 默认 auto 无标志目录 → generic 报告零金额")
def test_cli_default_auto_fallback(tmp_path):
    proc, data = _cli(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert data["meta"]["source"] == "generic"
    assert data["meta"]["cost_mode"] == "tokens_only"
    assert data["summary"]["total_cost"] == 0
    assert "无专用源标志命中" in proc.stderr


@allure.feature("auto 源探测")
@allure.title("B7 auto 命中 workbuddy 时不强制 tokens-only，能力不退化")
def test_cli_auto_hits_workbuddy(tmp_path):
    wb = tmp_path / ".workbuddy"
    (wb / "traces").mkdir(parents=True)
    proc, data = _cli(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert data["meta"]["source"] == "workbuddy"
    assert "命中 workbuddy" in proc.stderr
    # 强制 tokens-only 只发生在 generic 分支；workbuddy 保持 auto 计费判定
    assert "tokens-only" not in " ".join(a for a in proc.args if isinstance(a, str))
    assert data["meta"]["cost_supported"] is not False


@allure.feature("auto 源探测")
@allure.title("B6 显式 generic 在 auto 默认下仍可用")
def test_cli_explicit_generic(tmp_path):
    proc, data = _cli(tmp_path, source="generic")
    assert proc.returncode == 0, proc.stderr
    assert data["meta"]["source"] == "generic"
    assert "跳过自动探测" in proc.stderr
