# -*- coding: utf-8 -*-
"""P2-3 · 任务分类增强测试

覆盖：
  - 词边界：fix 不再误命中 prefix / fixture；bugfix 连写可命中
  - 信号密度：多信号类型取胜于单信号类型（不再首匹配即返回）
  - 平局打破：同分按 priority（保持旧版顺序语义）
  - 置信度：单信号 → 1.0；平票 → 0；随信号独占性单调
  - 规则外置：task_rules.json 加载、损坏 JSON 回退内置、坏正则跳过
  - collect_task_types：_dialogue_text 兜底（claude-code 会话）+ 置信度写入
  - LLM 分类器：未配 endpoint 报 ValueError；失败回退 ""；mock 成功路径白名单归一

⚠️ 不发起任何真实网络请求（LLM 路径用 mock）。
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import allure

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
for _p in (str(SKILL_DIR), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ca_core import load_task_rules, score_task_types  # noqa: E402
from ca_sessions import classify_task, collect_task_types  # noqa: E402
from task_classifier_llm import build_llm_classifier  # noqa: E402

pytestmark = pytest.mark.unit


# ── 词边界（修复旧版 re.search 无边界的误匹配）────────────────────────────

def test_word_boundary_fix_not_in_prefix():
    assert classify_task("解释一下 prefix 参数的含义") != "Bug修复"


def test_word_boundary_fix_not_in_fixture():
    assert classify_task("这个 fixture 为什么没有被加载？") != "Bug修复"


def test_word_boundary_bugfix_compound():
    # 连写 bugfix 应命中 Bug修复（\bbug\w*\b）
    assert classify_task("帮我修一个 bugfix") == "Bug修复"


def test_word_boundary_python_still_matches():
    assert classify_task("用 python 实现一个爬虫") == "代码开发"


# ── 信号密度（加权评分核心改进）───────────────────────────────────────────

def test_signal_density_code_beats_report():
    # 4 个代码信号 vs 1 个报告弱信号 → 代码开发（旧版首匹配可能判成报告生成）
    text = "帮我编写代码实现功能，再写代码做函数实现，用 python 编码，顺便生成一个报告"
    assert classify_task(text) == "代码开发"


def test_dominant_bug_signals_win():
    text = "调试时报错了，修复这个 bug，处理异常"
    assert classify_task(text) == "Bug修复"


def test_first_match_semantics_preserved_for_unambiguous():
    # 单一明确信号不因重构而漂移
    assert classify_task("写一部短剧剧本，主角是朱厚照") == "内容生成"
    assert classify_task("生成这周的周报，重点看 token 消耗") == "报告生成"


# ── 评分与置信度 ──────────────────────────────────────────────────────────

def test_score_empty_text():
    r = score_task_types("")
    assert r["top"] is None and r["confidence"] == 0.0
    assert score_task_types(None)["top"] is None


def test_score_single_match_confidence_one():
    r = score_task_types("生成这周的周报")
    assert r["top"] == "报告生成"
    assert r["confidence"] == 1.0


def test_confidence_lower_when_competing():
    solo = score_task_types("写一部短剧剧本")["confidence"]
    mixed = score_task_types("写一部短剧剧本，顺便生成一个周报")["confidence"]
    assert solo > mixed  # 有竞争信号时置信度下降


def test_no_match_returns_none_top():
    r = score_task_types("今天天气怎么样")
    assert r["top"] is None or r["top"] == "其他" or r["confidence"] < 0.3


# ── 规则外置：加载 / 回退 / 容错 ──────────────────────────────────────────

def test_load_task_rules_from_shipped_json():
    types, meta, source = load_task_rules()
    assert source == "task_rules.json"
    names = [t["name"] for t in types]
    assert "技能开发" in names and "Bug修复" in names
    assert all(t["patterns"] for t in types)
    assert 0 < meta["repeat_decay"] < 1


def test_load_task_rules_fallback_on_broken_json(tmp_path):
    bad = tmp_path / "task_rules.json"
    bad.write_text("{ not valid json !!!", encoding="utf-8")
    types, _meta, source = load_task_rules(bad)
    assert source == "builtin"
    assert types  # 内置规则非空


def test_load_task_rules_fallback_on_missing_file(tmp_path):
    types, _meta, source = load_task_rules(tmp_path / "nope.json")
    assert source == "builtin"


def test_bad_regex_skipped_not_fatal(tmp_path):
    rules = {
        "_meta": {"scoring": {}},
        "types": {
            "坏正则": {"priority": 1, "patterns": ["([unclosed"]},
            "有效类型": {"priority": 2, "patterns": [{"p": "写剧本", "w": 2.0}]},
        },
    }
    p = tmp_path / "task_rules.json"
    p.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    types, _meta, source = load_task_rules(p)
    assert source == "task_rules.json"
    r = score_task_types("帮我写剧本", types=types, meta=_meta)
    assert r["top"] == "有效类型"


def test_custom_rules_override(tmp_path):
    rules = {
        "_meta": {"scoring": {"default_weight": 1.0}},
        "types": {"游戏开发": {"priority": 0, "patterns": [{"p": "godot|像素游戏", "w": 3.0}]}},
    }
    p = tmp_path / "task_rules.json"
    p.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    types, meta, _ = load_task_rules(p)
    assert score_task_types("做一个像素游戏", types=types, meta=meta)["top"] == "游戏开发"


# ── P0 回归：collect_task_types 必须能独立调用（v1.3.0 拆分曾漏导入致 NameError）──

def test_collect_task_types_module_has_session_helpers():
    # 守护 ca_sessions 显式导入 get_session_content / get_session_artifact_fingerprint；
    # 缺失时 collect_usage_data.py（WorkBuddy 源）运行即 NameError。
    import ca_sessions
    assert hasattr(ca_sessions, "get_session_content")
    assert hasattr(ca_sessions, "get_session_artifact_fingerprint")


# ── collect_task_types：_dialogue_text 兜底 + 置信度透明字段 ──────────────

def test_collect_task_types_uses_dialogue_text_fallback(monkeypatch):
    # 模拟 claude-code 会话：无 WorkBuddy 内容文件，只有 _dialogue_text
    import ca_sessions
    monkeypatch.setattr(ca_sessions, "get_session_content", lambda *a, **k: "")
    monkeypatch.setattr(ca_sessions, "get_session_artifact_fingerprint", lambda *a, **k: "")
    sessions = [{"id": "cc1", "title": "", "custom_title": "", "cwd": "",
                 "_dialogue_text": "帮我用 python 写代码实现一个爬虫"}]
    task_map = collect_task_types(sessions)
    assert task_map["cc1"] == "代码开发"
    assert sessions[0]["_task_confidence"] >= 0.5


def test_collect_task_types_background_automation():
    sessions = [{"id": "auto1", "title": "定时任务", "custom_title": "",
                 "is_background_automation": True}]
    task_map = collect_task_types(sessions)
    assert task_map["auto1"] == "自动化配置"
    assert sessions[0]["_task_confidence"] == 1.0


def test_collect_task_types_pluggable_classifier(monkeypatch):
    monkeypatch.setattr("ca_sessions.get_session_content", lambda *a, **k: "")
    monkeypatch.setattr("ca_sessions.get_session_artifact_fingerprint", lambda *a, **k: "")
    sessions = [{"id": "s1", "title": "随便", "custom_title": "", "cwd": "",
                 "_dialogue_text": "帮我写代码"}]
    task_map = collect_task_types(sessions, classifier=lambda text: "自定义类型")
    assert task_map["s1"] == "自定义类型"


def test_collect_task_types_classifier_falsy_falls_back(monkeypatch):
    monkeypatch.setattr("ca_sessions.get_session_content", lambda *a, **k: "")
    monkeypatch.setattr("ca_sessions.get_session_artifact_fingerprint", lambda *a, **k: "")
    sessions = [{"id": "s1", "title": "", "custom_title": "", "cwd": "",
                 "_dialogue_text": "写剧本"}]
    task_map = collect_task_types(sessions, classifier=lambda text: "")
    assert task_map["s1"] == "内容生成"  # 回退启发式


# ── LLM 分类器（全程 mock，不发真实请求）──────────────────────────────────

def test_llm_classifier_requires_endpoint():
    with pytest.raises(ValueError):
        build_llm_classifier(None, "qwen2.5:7b")


def test_llm_classifier_requires_model():
    with pytest.raises(ValueError):
        build_llm_classifier("http://localhost:11434/v1", None)


def _mock_urlopen_response(content):
    resp = MagicMock()
    resp.read.return_value = json.dumps(
        {"choices": [{"message": {"content": content}}]}).encode("utf-8")
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_llm_classifier_success_exact_type():
    clf = build_llm_classifier("http://localhost:11434/v1", "qwen2.5:7b")
    with patch("task_classifier_llm.urllib.request.urlopen",
               return_value=_mock_urlopen_response("代码开发")):
        assert clf("用 python 写爬虫") == "代码开发"


def test_llm_classifier_wraps_answer():
    clf = build_llm_classifier("http://localhost:11434/v1", "qwen2.5:7b")
    with patch("task_classifier_llm.urllib.request.urlopen",
               return_value=_mock_urlopen_response("「报告生成」")):
        assert clf("生成周报") == "报告生成"


def test_llm_classifier_unknown_answer_maps_to_other():
    clf = build_llm_classifier("http://localhost:11434/v1", "qwen2.5:7b")
    with patch("task_classifier_llm.urllib.request.urlopen",
               return_value=_mock_urlopen_response("完全无关的回答")):
        assert clf("随便聊聊") == "其他"


def test_llm_classifier_network_failure_returns_falsy():
    import urllib.error
    clf = build_llm_classifier("http://localhost:11434/v1", "qwen2.5:7b")
    with patch("task_classifier_llm.urllib.request.urlopen",
               side_effect=urllib.error.URLError("connection refused")):
        assert clf("写代码") == ""  # falsy → collect_task_types 回退启发式


def test_llm_classifier_empty_text_no_call():
    clf = build_llm_classifier("http://localhost:11434/v1", "qwen2.5:7b")
    with patch("task_classifier_llm.urllib.request.urlopen") as mock_open:
        assert clf("") == "其他"
        assert clf("   ") == "其他"
        mock_open.assert_not_called()  # 空文本不发请求
