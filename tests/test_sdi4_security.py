# -*- coding: utf-8 -*-
"""SDI-4 修复 + SQP-2 收敛 单测（回应 ClawHub 扫描 need review）。

SDI-4：fetch_remote_pricing 必须在目标不可达 / 非 200 / 坏 JSON 时优雅降级
（返回 {}），而非让 urllib 抛出的 URLError / HTTPError 逃逸导致采集器崩溃
（原实现 except 只接 requests.RequestException）。

SQP-2：_minimize_dialogue 必须把会话正文压成 ≤120 字符的短样本，并对明显凭据
（sk- key / URL / 邮箱）脱敏；短小无密文本保持原样，确保任务分类兜底不受影响。
"""

import sys
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
import allure

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
for _p in (str(SKILL_DIR), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.codex import _minimize_dialogue  # noqa: E402
from scripts.report_charts import build_model_cost_chart  # noqa: E402

pytestmark = [pytest.mark.regression, pytest.mark.privacy]


class _FakeResp:
    """模拟 urlopen 返回的上下文管理器响应（带 .read()）。"""

    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


# ── SDI-4：优雅降级 ─────────────────────────────────────────────────────────

@allure.feature("安全加固（回应 ClawHub need review）")
@allure.story("SDI-4 优雅降级")
@allure.title("fetch_remote_pricing 在目标不可达时返回空 dict 而非崩溃")
def test_fetch_remote_pricing_unreachable(collector_module):
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("host down")):
        assert collector_module.fetch_remote_pricing("http://127.0.0.1:9/x", ["gpt-4"]) == {}


@allure.feature("安全加固（回应 ClawHub need review）")
@allure.story("SDI-4 优雅降级")
@allure.title("fetch_remote_pricing 在非 200 (HTTPError) 时返回空 dict")
def test_fetch_remote_pricing_http_error(collector_module):
    with patch("urllib.request.urlopen",
               side_effect=urllib.error.HTTPError("http://x", 500, "e", {}, None)):
        assert collector_module.fetch_remote_pricing("http://x", ["gpt-4"]) == {}


@allure.feature("安全加固（回应 ClawHub need review）")
@allure.story("SDI-4 优雅降级")
@allure.title("fetch_remote_pricing 在坏 JSON 时返回空 dict（ValueError 兜底）")
def test_fetch_remote_pricing_bad_json(collector_module):
    with patch("urllib.request.urlopen", return_value=_FakeResp(b"not json")):
        assert collector_module.fetch_remote_pricing("http://x", ["gpt-4"]) == {}


@allure.feature("安全加固（回应 ClawHub need review）")
@allure.story("SDI-4 正常路径")
@allure.title("fetch_remote_pricing 成功时解析模型单价")
def test_fetch_remote_pricing_success(collector_module):
    payload = b'{"models": {"gpt-4": {"input": 0.5, "output": 1.5}}}'
    with patch("urllib.request.urlopen", return_value=_FakeResp(payload)):
        res = collector_module.fetch_remote_pricing("http://x", ["gpt-4"])
    assert res == {"gpt-4": {"input": 0.5, "output": 1.5}}


# ── SQP-2：隐私面收敛 ─────────────────────────────────────────────────────────

@allure.feature("安全加固（回应 ClawHub need review）")
@allure.story("SQP-2 隐私面收敛")
@allure.title("_minimize_dialogue 长文本截断到 ≤120 字符")
def test_minimize_dialogue_truncates_long():
    long_text = "x" * 500
    assert len(_minimize_dialogue([long_text])) <= 120


@allure.feature("安全加固（回应 ClawHub need review）")
@allure.story("SQP-2 隐私面收敛")
@allure.title("_minimize_dialogue 对 sk-/URL/邮箱脱敏为 [REDACTED]")
def test_minimize_dialogue_redacts_secrets():
    # 构造一个 sk- 前缀的伪密钥以验证脱敏：运行时拼接，源码中不含真实形态的密钥字面量，
    # 避免被发布扫描误判为凭据泄漏（真实 OpenAI 密钥形如 sk- + 20+ 位高熵字符）。
    fake_sk = "sk-" + "a" * 24
    text = f"k={fake_sk} u=http://evil.com/x m=a@b.com end"
    out = _minimize_dialogue([text])
    assert fake_sk not in out
    assert "http://evil.com/x" not in out
    assert "a@b.com" not in out
    assert "[REDACTED]" in out


@allure.feature("安全加固（回应 ClawHub need review）")
@allure.story("SQP-2 不影响分类")
@allure.title("_minimize_dialogue 短小无密文本保持不变（任务分类兜底不受影响）")
def test_minimize_dialogue_preserves_short_clean():
    text = "帮我用 python 写代码实现一个爬虫"
    assert _minimize_dialogue([text]) == text


# ── SDI-4：越窗会话元数据收集修复 ─────────────────────────────────────────────

@allure.feature("安全加固（回应 ClawHub need review）")
@allure.story("SDI-4 越窗收集修复")
@allure.title("collect_*_sessions 仅纳入窗口内会话（越窗且无窗内 trace 排除）")
def test_sessions_window_filter(monkeypatch):
    import adapters.claude_code as cc
    import adapters.codex as cx
    from pathlib import Path as _P

    in_meta = {"id": "in", "created_date": "2026-09-01", "_dialogue_text": "写代码"}
    out_meta = {"id": "out", "created_date": "2020-01-01", "_dialogue_text": "旧会话"}
    unk_meta = {"id": "unk", "created_date": None, "_dialogue_text": "无日期"}

    def _fake_claude(f, sd, ed):
        name = _P(f).name
        if name == "in.jsonl":
            return [{"t": 1}], dict(in_meta)
        if name == "out.jsonl":
            return [], dict(out_meta)
        return [], dict(unk_meta)

    def _fake_codex(f, sd, ed):
        name = _P(f).name
        if name == "in.jsonl":
            return [{"t": 1}], dict(in_meta)
        if name == "out.jsonl":
            return [], dict(out_meta)
        return [], dict(unk_meta)

    monkeypatch.setattr(cc, "_parse_claude_file", _fake_claude)
    monkeypatch.setattr(cc, "_iter_jsonl_files", lambda root: ["in.jsonl", "out.jsonl", "unk.jsonl"])
    monkeypatch.setattr(cx, "_parse_codex_file", _fake_codex)
    monkeypatch.setattr(cx, "_iter_jsonl_files", lambda root: ["in.jsonl", "out.jsonl", "unk.jsonl"])

    cc_sessions = cc.collect_claude_code_sessions("2026-09-01", "2026-09-07", projects_root="x")["sessions"]
    cx_sessions = cx.collect_codex_sessions("2026-09-01", "2026-09-07", sessions_root="x")["sessions"]
    for sessions in (cc_sessions, cx_sessions):
        ids = {s["id"] for s in sessions}
        assert "in" in ids        # 窗口内（或窗内有 trace）→ 纳入
        assert "out" not in ids   # 越窗且无 trace → 排除
        assert "unk" not in ids   # 日期未知且无 trace → 排除


# ── SDI-4：build_model_cost_chart 存储型 XSS 修复 ─────────────────────────────

@allure.feature("安全加固（回应 ClawHub need review）")
@allure.story("SDI-4 存储型 XSS")
@allure.title("build_model_cost_chart 对模型名做 HTML 转义防止存储型 XSS")
def test_build_model_cost_chart_escapes_model_name():
    """模型名来自本地日志 / 配置，可能含引号或标记；必须转义后落入 HTML。"""
    html_out = build_model_cost_chart(
        model_stats=[{"model": '"><script>alert(1)</script>', "effective_cost": 1.23}]
    )
    # 原始未转义载荷不得出现在输出中（防止属性 / 文本上下文逃逸）
    assert '"><script>alert(1)</script>' not in html_out
    # 转义后的形态必须存在：< / > / 引号均被 html.escape 处理
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html_out

