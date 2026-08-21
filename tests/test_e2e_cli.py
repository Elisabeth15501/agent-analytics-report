# -*- coding: utf-8 -*-
"""L3 · CLI / 端到端（End-to-End CLI）— pytest + Allure 双可视化。

以「黑盒」方式驱动 generate_report.py 的真正命令行入口：
  - --help 可正常输出（CLI 入口健康）
  - 给定数据 JSON，分别生成 markdown / html / json 三种格式到文件，
    文件存在、非空、含关键章节，且 HTML 中恶意模型名被转义
  - 非法 --format 被 argparse 拦截（非零退出）

全程只喂合成数据 JSON，绝不触碰真实 workbuddy.db / traces。
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import allure

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_DIR / "scripts"
GEN = SCRIPTS / "generate_report.py"

pytestmark = [pytest.mark.integration, pytest.mark.blackbox]


def _report_data():
    """构造一份结构完整、可驱动三格式生成器的 fixture 数据。"""
    return {
        "meta": {
            "period": "week", "start_date": "2026-08-10", "end_date": "2026-08-16",
            "timed_free": {"hy3": "2026-08-31"},
        },
        "summary": {
            "active_day_count": 5,
            "active_days": ["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"],
            "total_sessions": 12, "total_traces": 40,
            "total_automation_runs": 3, "successful_automation_runs": 3,
            "total_outputs": 2, "skills_used": 4,
            "total_tokens": 1_000_000, "total_effective_tokens": 900_000,
            "total_input_tokens": 600_000, "total_output_tokens": 400_000,
            "total_cached_tokens": 100_000,
            "total_cost": 1.2, "total_effective_cost": 1.0,
            "total_input_cost": 0.7, "total_output_cost": 0.5,
            "task_type_distribution": {"代码生成": 500000, "问答": 300000, "其他": 100000},
        },
        "daily_tokens": {
            "2026-08-10": {"total": 200000, "effective": 180000, "effective_cost": 0.2},
            "2026-08-11": {"total": 220000, "effective": 200000, "effective_cost": 0.3},
        },
        "skill_usage": {"skills": {
            "ai-weekly": {"usage_count_in_range": 2, "last_used": "2026-08-11"},
        }},
        "outputs": [{"name": "report.md", "size": 1234}],
        "automation_runs": [{"name": "日报", "status": "success"}],
        "traces": [
            {"exec_model": "glm-5.2", "raw_model": "glm-5.2",
             "input_tokens": 600000, "output_tokens": 400000},
        ],
        "model_stats": [
            {"model": "glm-5.2", "calls": 10, "effective_tokens": 500000, "effective_cost": 0.8,
             "input_tokens": 300000, "output_tokens": 200000,
             "unit_price_input": 8.0, "unit_price_output": 28.0, "configured": True},
        ],
        "model_exec_stats": [
            {"model": "glm-5.2", "calls": 10, "effective_tokens": 500000,
             "effective_cost": 0.8, "input_tokens": 300000, "output_tokens": 200000,
             "unit_price_input": 8.0, "unit_price_output": 28.0, "configured": True},
        ],
        "session_stats": {"rows": [], "buckets": []},
        "session_credits": [],
        "sessions": [],
        "cost_anomalies": {},
        "savings_insights": {},
    }


def _run_cli(tmp, fmt, data):
    """把 data 写成临时 JSON，调用 CLI 生成指定格式到临时文件，返回 (proc, out_path)。"""
    data_path = tmp / "data.json"
    data_path.write_text(json.dumps(data), encoding="utf-8")
    out_path = tmp / f"report.{fmt}"
    proc = subprocess.run(
        [sys.executable, str(GEN), str(data_path), "--output", str(out_path), "--format", fmt],
        cwd=str(SKILL_DIR), capture_output=True, text=True,
    )
    return proc, out_path


# ── CLI 健康 ─────────────────────────────────────────────────────────────────

@allure.feature("CLI / 端到端（E2E CLI）")
@allure.story("CLI 入口健康")
@allure.title("generate_report.py --help 正常输出且退出码 0")
@allure.severity(allure.severity_level.NORMAL)
def test_cli_help():
    proc = subprocess.run(
        [sys.executable, str(GEN), "--help"],
        cwd=str(SKILL_DIR), capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"--help 应退出 0，实得 {proc.returncode}\n{proc.stderr}"
    assert "报告生成器" in (proc.stdout or ""), "帮助信息应含描述文案"


# ── 三格式端到端生成 ─────────────────────────────────────────────────────────

@allure.feature("CLI / 端到端（E2E CLI）")
@allure.story("端到端报告生成")
@allure.title("CLI 生成 markdown 报告到文件且含关键章节")
@allure.severity(allure.severity_level.CRITICAL)
def test_cli_generate_markdown():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        proc, out = _run_cli(td, "markdown", _report_data())
        assert proc.returncode == 0, f"生成失败：{proc.stderr}"
        assert out.exists() and out.stat().st_size > 200, "markdown 报告文件应非空"
        text = out.read_text(encoding="utf-8")
        assert "# Workbuddy使用情况报告" in text
        assert "## 一、概览统计" in text


@allure.feature("CLI / 端到端（E2E CLI）")
@allure.story("端到端报告生成")
@allure.title("CLI 生成 html 报告到文件且为合法 HTML、含标题")
@allure.severity(allure.severity_level.CRITICAL)
def test_cli_generate_html():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        proc, out = _run_cli(td, "html", _report_data())
        assert proc.returncode == 0, f"生成失败：{proc.stderr}"
        assert out.exists() and out.stat().st_size > 200, "html 报告文件应非空"
        text = out.read_text(encoding="utf-8")
        assert "<html" in text.lower()
        assert "Workbuddy使用情况报告" in text


@allure.feature("CLI / 端到端（E2E CLI）")
@allure.story("端到端报告生成")
@allure.title("CLI 生成 json 报告到文件且可解析、含 summary/meta")
@allure.severity(allure.severity_level.CRITICAL)
def test_cli_generate_json():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        proc, out = _run_cli(td, "json", _report_data())
        assert proc.returncode == 0, f"生成失败：{proc.stderr}"
        assert out.exists() and out.stat().st_size > 50, "json 报告文件应非空"
        parsed = json.loads(out.read_text(encoding="utf-8"))
        assert "summary" in parsed and "meta" in parsed


# ── XSS 端到端回归 ──────────────────────────────────────────────────────────

@allure.feature("CLI / 端到端（E2E CLI）")
@allure.story("XSS 端到端回归")
@allure.title("恶意模型名经 CLI 生成 HTML 后被转义，不出现原始标签")
@allure.severity(allure.severity_level.BLOCKER)
def test_cli_html_escapes_malicious_model():
    data = _report_data()
    evil = '<img src=x onerror=alert(1)>'
    data["model_stats"] = [{
        "model": evil, "calls": 1, "effective_tokens": 10, "effective_cost": 0.0,
        "input_tokens": 5, "output_tokens": 5,
        "unit_price_input": None, "unit_price_output": None, "configured": False,
    }]
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        proc, out = _run_cli(td, "html", data)
        assert proc.returncode == 0, f"生成失败：{proc.stderr}"
        html = out.read_text(encoding="utf-8")
        assert evil not in html, "恶意模型名必须以转义形式出现，不得保留原始标签"
        assert "&lt;img" in html.lower(), "应出现转义后的 &lt;img"


# ── 非法参数拦截 ─────────────────────────────────────────────────────────────

@allure.feature("CLI / 端到端（E2E CLI）")
@allure.story("非法参数拦截")
@allure.title("非法 --format 被 argparse 拦截（非零退出）")
@allure.severity(allure.severity_level.NORMAL)
def test_cli_invalid_format_rejected():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        data_path = td / "data.json"
        data_path.write_text(json.dumps(_report_data()), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(GEN), str(data_path), "--format", "pdf"],
            cwd=str(SKILL_DIR), capture_output=True, text=True,
        )
        assert proc.returncode != 0, "非法 --format 应被 argparse 拒绝（非零退出）"
