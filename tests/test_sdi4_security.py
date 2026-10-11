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
    text = "k=sk-abcdefghijklmnopqrstuvw u=http://evil.com/x m=a@b.com end"
    out = _minimize_dialogue([text])
    assert "sk-abcdefghijklmnopqrstuvw" not in out
    assert "http://evil.com/x" not in out
    assert "a@b.com" not in out
    assert "[REDACTED]" in out


@allure.feature("安全加固（回应 ClawHub need review）")
@allure.story("SQP-2 不影响分类")
@allure.title("_minimize_dialogue 短小无密文本保持不变（任务分类兜底不受影响）")
def test_minimize_dialogue_preserves_short_clean():
    text = "帮我用 python 写代码实现一个爬虫"
    assert _minimize_dialogue([text]) == text
