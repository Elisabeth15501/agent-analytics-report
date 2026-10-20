"""回归测试：_render_anomaly_block 必须 HTML 转义来自 trace 的动态文本
（模型名 / 异常原因 / 日期 / 标题），防止构造的 payload（如 <script>）在报告打开时执行。

对应 ClawHub T09 / Tencent AI-Infra-Guard (aig-skill-scan) 在 v1.9.10 抓出的
"Unescaped model metadata enters executable HTML" HTML 注入面。
"""
import os
import sys

import pytest

# 让测试能 import scripts/generate_report.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import generate_report as g  # noqa: E402


def _crafted_block():
    return {
        "thresholds": {"p50": 0.0, "p95": 0.0, "session_p95": 0.0},
        "daily": [
            {
                "date": "2026-10-20",
                "value": 100.0,
                "reasons": ["模型 <script>alert(1)</script> 异常"],
            },
        ],
        "session": [
            {
                "title": "sess <b>x</b>",
                "value": 50.0,
                "models": ["<script>alert(2)</script>", "minimax-m3.1-flash"],
                "calls": 3,
            },
        ],
    }


def test_html_branch_escapes_payload():
    html = "\n".join(g._render_anomaly_block("html", "T", _crafted_block(), "cost"))
    assert "<script" not in html, "RAW <script> leaked into HTML output"
    assert "&lt;script&gt;" in html, "payload not HTML-escaped in HTML branch"
    # 正常模型名应原样出现、不受转义影响
    assert "minimax-m3.1-flash" in html


def test_md_branch_escapes_payload():
    md = "\n".join(g._render_anomaly_block("md", "T", _crafted_block(), "cost"))
    assert "<script" not in md, "RAW <script> leaked into MD output"
    assert "&lt;script&gt;" in md, "payload not HTML-escaped in MD branch"


def test_normal_models_no_regression():
    block = {
        "session": [
            {
                "title": "正常会话",
                "value": 1.0,
                "models": ["minimax-m3.1-flash", "kimi-k2.8-preview"],
                "calls": 1,
            }
        ]
    }
    out = "\n".join(g._render_anomaly_block("html", "T", block, "cost"))
    assert "minimax-m3.1-flash" in out and "kimi-k2.8-preview" in out
