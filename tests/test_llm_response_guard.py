# -*- coding: utf-8 -*-
"""安全报告 #10 回归测试：LLM / 定价镜像坏响应结构不得绕过优雅降级。

背景：`classify_text_with_llm` 原用链式 .get() 解析响应，message=null 时抛
AttributeError，而调用方 _classify 的异常白名单不含该类型 → 异常逃逸、报告生成中断。
本测试参数化各种畸形响应，断言 _classify 一律不抛异常并返回合法回退值。

⚠️ 不发起任何真实网络请求（urlopen 用 monkeypatch 替换）。
"""

import json
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
for _p in (str(SKILL_DIR), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import collect_usage_data as cud  # noqa: E402
import task_classifier_llm as tcl  # noqa: E402


class _FakeResponse:
    """最小 urlopen 上下文管理器，body 为给定字节。"""

    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


def _patch_urlopen(monkeypatch, payload_obj):
    body = (payload_obj if isinstance(payload_obj, bytes)
            else json.dumps(payload_obj).encode("utf-8"))
    monkeypatch.setattr(
        tcl.urllib.request, "urlopen",
        lambda *_a, **_k: _FakeResponse(body),
    )


# 畸形/边界响应 → 期望 _classify 不抛异常（返回 "" 触发启发式回退，或合法类型名）
_BAD_RESPONSES = [
    ("message 为 null", {"choices": [{"message": None}]}),
    ("choices 为空", {"choices": []}),
    ("choices 为 null", {"choices": None}),
    ("choices 非 list", {"choices": {"message": {"content": "编码"}}}),
    ("choices[0] 非 dict", {"choices": ["oops"]}),
    ("message 非 dict", {"choices": [{"message": "oops"}]}),
    ("content 非 str", {"choices": [{"message": {"content": 123}}]}),
    ("整体非 dict", ["not", "a", "dict"]),
    ("缺少 choices", {"model": "x"}),
]


@pytest.mark.parametrize("label,payload", _BAD_RESPONSES, ids=[x[0] for x in _BAD_RESPONSES])
def test_malformed_llm_response_degrades_gracefully(monkeypatch, label, payload):
    _patch_urlopen(monkeypatch, payload)
    cls = tcl.build_llm_classifier("http://localhost:11434/v1", "qwen2.5:7b")
    result = cls("这是一段用于分类的会话文本")
    # 不得抛异常；返回值为空串（回退启发式）或合法类型字符串
    assert isinstance(result, str), label


def test_valid_llm_response_still_classifies(monkeypatch):
    _patch_urlopen(monkeypatch, {"choices": [{"message": {"content": "代码开发"}}]})
    cls = tcl.build_llm_classifier("http://localhost:11434/v1", "qwen2.5:7b")
    assert cls("帮我写一段排序代码") == "代码开发"


# ---- #10b：定价镜像坏响应降级 ----

def test_malformed_pricing_mirror_degrades_gracefully(monkeypatch, capsys):
    body = json.dumps(["not", "a", "dict"]).encode("utf-8")
    monkeypatch.setattr(
        cud.urllib.request, "urlopen",
        lambda *_a, **_k: _FakeResponse(body),
    )
    result = cud.fetch_remote_pricing("http://localhost:9999/pricing", ["some-model"])
    assert result == {}
    assert "降级" in capsys.readouterr().err


def test_valid_pricing_mirror_still_works(monkeypatch):
    body = json.dumps({"models": {"glm-5.2": {"input": 1.0, "output": 4.0}}}).encode("utf-8")
    monkeypatch.setattr(
        cud.urllib.request, "urlopen",
        lambda *_a, **_k: _FakeResponse(body),
    )
    result = cud.fetch_remote_pricing("http://localhost:9999/pricing", ["glm-5.2"])
    assert result == {"glm-5.2": {"input": 1.0, "output": 4.0}}
