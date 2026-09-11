# -*- coding: utf-8 -*-
"""L1/L3 · OpenAI Codex CLI 适配器测试

覆盖：
  - JSONL 解析：turn.completed usage → 统一 trace schema（token / 成本 / 通道）
  - 日期窗口过滤（闭区间，越界丢弃）
  - 缓存命中折扣：effective_tokens / effective_cost
  - 健壮性：坏 JSON 行、非 dict、无 usage 的 turn.completed、缺字段
  - 未知模型零成本且被记录
  - 会话派生：title / cwd / task_type / model 前缀
  - 路径探测：CODEX_HOME 覆盖默认探测路径
  - 黑盒：collect_usage_data.py --source codex 产出合法 JSON 且能渲染报告

⚠️ 本测试通过 CODEX_HOME 环境变量把 fixture 根目录指向 tmp_path，
   绝不读取真实的 ~/.codex/。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import allure

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
for _p in (str(SKILL_DIR), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.codex import (  # noqa: E402
    CODEX_CHANNEL,
    _parse_codex_file,
    _ts_to_ms,
    collect_codex,
    collect_codex_sessions,
    collect_codex_traces,
    resolve_codex_sessions_root,
)

pytestmark = [pytest.mark.unit, pytest.mark.integration]

PY = sys.executable


# ── fixture 构造 ────────────────────────────────────────────────────────────

def _line(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _session_meta(sid="s1", cwd="/Users/t/proj", model="gpt-5-codex"):
    return _line({
        "type": "session_meta",
        "timestamp": "2026-02-10T09:59:00.000Z",
        "payload": {"id": sid, "cwd": cwd, "cli_version": "0.134.0",
                    "model_provider": "openai", "model": model, "source": "cli"},
    })


def _user_msg(ts, text, sid="s1"):
    # Codex 用户消息：response_item / payload.type=message, role=user
    return _line({
        "type": "response_item", "timestamp": ts, "sessionId": sid,
        "payload": {"type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": text}]},
    })


def _assistant_msg(ts, text, model="gpt-5-codex", sid="s1"):
    return _line({
        "type": "response_item", "timestamp": ts, "sessionId": sid,
        "payload": {"type": "message", "role": "assistant", "model": model,
                    "content": [{"type": "output_text", "text": text}]},
    })


def _turn_completed(ts, inp, out, cache_read=0, reasoning=0, sid="s1"):
    # ⭐ 主用量来源：turn.completed 含 usage（顶层，与 danielvaughan 文档一致）
    usage = {"input_tokens": inp, "cached_input_tokens": cache_read,
             "output_tokens": out, "reasoning_output_tokens": reasoning}
    return _line({
        "type": "turn.completed", "timestamp": ts, "sessionId": sid,
        "usage": usage,
    })


@pytest.fixture
def codex_home(tmp_path):
    """临时 CODEX_HOME，其下 sessions/ 写入 rollout 文件，通过环境变量注入。"""
    home = tmp_path / "codex_home"
    (home / "sessions" / "2026" / "02" / "10").mkdir(parents=True)
    old = os.environ.get("CODEX_HOME")
    os.environ["CODEX_HOME"] = str(home)
    yield home
    if old is None:
        os.environ.pop("CODEX_HOME", None)
    else:
        os.environ["CODEX_HOME"] = old


def _write_session(home, name, lines, datepath="2026/02/10"):
    p = home / "sessions" / datepath / f"rollout-{name}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# ── 解析正确性 ──────────────────────────────────────────────────────────────

@allure.feature("Codex CLI 适配器")
@allure.story("JSONL 解析")
@allure.title("turn.completed usage 被解析为统一 trace schema")
@allure.severity(allure.severity_level.CRITICAL)
def test_parse_turn_completed_to_trace(codex_home):
    _write_session(codex_home, "s1", [
        _session_meta("s1", "/Users/t/proj", "gpt-5-codex"),
        _user_msg("2026-02-10T10:00:00.000Z", "帮我用 python 写一个快排函数并加上单元测试"),
        _turn_completed("2026-02-10T10:00:05.000Z", 2000, 500, cache_read=5000, reasoning=128),
    ])
    traces, meta = _parse_codex_file(
        codex_home / "sessions" / "2026" / "02" / "10" / "rollout-s1.jsonl",
        "2026-02-01", "2026-02-28")

    assert len(traces) == 1, "应解析出 1 条 trace"
    t = traces[0]
    assert t["date"] == "2026-02-10"
    assert t["session_id"] == "s1"
    assert t["exec_model"] == "gpt-5-codex"
    assert t["channel"] == CODEX_CHANNEL
    assert t["raw_model"] == f"{CODEX_CHANNEL}:gpt-5-codex"
    assert t["input_tokens"] == 2000
    assert t["output_tokens"] == 500
    assert t["total_tokens"] == 2500
    assert t["cached_tokens"] == 5000, "cached 应等于 cached_input_tokens"
    assert t["_reasoning_output_tokens"] == 128
    assert t["call_count"] == 1
    assert t["status"] == "success"
    assert t["total_cost"] > 0, "已登记模型应产出非零成本"
    assert t["effective_cost"] <= t["total_cost"] + 1e-9, "缓存折扣后成本不应高于原价"
    assert meta["title"].startswith("帮我用 python"), "title 应取首条用户消息"
    assert meta["cwd"] == "/Users/t/proj"


@allure.feature("Codex CLI 适配器")
@allure.story("缓存折扣")
@allure.title("缓存命中使 effective_tokens / effective_cost 下降")
def test_cache_discount_applied(codex_home):
    _write_session(codex_home, "s2", [
        _session_meta("s2", "/Users/t/proj", "gpt-5-codex"),
        _user_msg("2026-02-11T09:00:00.000Z", "重构这个模块"),
        _turn_completed("2026-02-11T09:00:10.000Z", 10000, 1000, cache_read=8000),
    ])
    traces, _ = _parse_codex_file(
        codex_home / "sessions" / "2026" / "02" / "10" / "rollout-s2.jsonl",
        "2026-02-01", "2026-02-28")
    t = traces[0]
    assert t["effective_tokens"] < t["total_tokens"], "缓存命中应降低有效 token"
    assert t["effective_cost"] < t["total_cost"], "缓存命中应降低有效成本"


@allure.feature("Codex CLI 适配器")
@allure.story("日期过滤")
@allure.title("窗口外的 turn.completed 被丢弃")
def test_date_window_filter(codex_home):
    # 混在三个日期目录里，仅 02-12 在窗口内
    _write_session(codex_home, "s_a", [
        _session_meta("sa", "/p", "gpt-5-codex"),
        _user_msg("2026-01-05T10:00:00.000Z", "窗口前"),
        _turn_completed("2026-01-05T10:00:05.000Z", 100, 50),
    ], datepath="2026/01/05")
    _write_session(codex_home, "s_b", [
        _session_meta("sb", "/p", "gpt-5-codex"),
        _user_msg("2026-02-12T10:00:00.000Z", "窗口内"),
        _turn_completed("2026-02-12T10:00:05.000Z", 200, 60),
    ], datepath="2026/02/12")
    _write_session(codex_home, "s_c", [
        _session_meta("sc", "/p", "gpt-5-codex"),
        _user_msg("2026-03-20T10:00:00.000Z", "窗口后"),
        _turn_completed("2026-03-20T10:00:05.000Z", 300, 70),
    ], datepath="2026/03/20")
    traces = collect_codex_traces("2026-02-01", "2026-02-28")
    assert len(traces) == 1, "只应保留窗口内的 1 条"
    assert traces[0]["date"] == "2026-02-12"


@allure.feature("Codex CLI 适配器")
@allure.story("健壮性")
@allure.title("坏行 / 无 usage / 非 dict 不导致崩溃")
def test_malformed_lines_skipped(codex_home):
    p = codex_home / "sessions" / "2026" / "02" / "10" / "rollout-s4.jsonl"
    p.write_text(
        _session_meta("s4", "/p", "gpt-5-codex") + "\n"
        + "{ this is not json" + "\n"
        + _line(["not", "a", "dict"]) + "\n"
        + _line({"type": "turn.completed", "timestamp": "2026-02-13T10:00:01.000Z"}) + "\n"  # 无 usage
        + _line({"type": "system", "timestamp": "2026-02-13T10:00:02.000Z"}) + "\n"
        + _turn_completed("2026-02-13T10:00:03.000Z", 10, 20, cache_read=4) + "\n",
        encoding="utf-8")
    traces, meta = _parse_codex_file(p, "2026-02-01", "2026-02-28")
    assert len(traces) == 1, "仅保留带 usage 的正常 turn.completed"
    assert meta["id"] == "s4"


@allure.feature("Codex CLI 适配器")
@allure.story("健壮性")
@allure.title("未登记模型产出零成本且被记录")
def test_unknown_model_zero_cost(codex_home):
    _write_session(codex_home, "s5", [
        _session_meta("s5", "/p", "codex-future-9"),
        _user_msg("2026-02-14T10:00:00.000Z", "未知模型"),
        _turn_completed("2026-02-14T10:00:05.000Z", 999, 999, cache_read=100),
    ])
    traces, _ = _parse_codex_file(
        codex_home / "sessions" / "2026" / "02" / "10" / "rollout-s5.jsonl",
        "2026-02-01", "2026-02-28")
    t = traces[0]
    assert t["total_cost"] == 0.0
    assert t["exec_model"] == "codex-future-9", "未知模型仍应被记录，不丢数据"


@allure.feature("Codex CLI 适配器")
@allure.story("路径探测")
@allure.title("CODEX_HOME 覆盖默认探测路径")
def test_sessions_root_override(codex_home):
    assert str(resolve_codex_sessions_root()) == str(codex_home / "sessions")


@allure.feature("Codex CLI 适配器")
@allure.story("工具函数")
@allure.title("ISO 时间戳解析为毫秒")
def test_ts_to_ms():
    assert _ts_to_ms("2026-02-10T10:00:00.000Z") > 0
    assert _ts_to_ms("") == 0
    assert _ts_to_ms("not-a-timestamp") == 0


@allure.feature("Codex CLI 适配器")
@allure.story("会话派生")
@allure.title("collect_codex 产出 traces 与可分类的 sessions")
def test_collect_codex_returns_traces_and_sessions(codex_home):
    _write_session(codex_home, "s6", [
        _session_meta("s6", "/p", "gpt-5-codex"),
        _user_msg("2026-02-15T10:00:00.000Z", "帮我用 python 写一个快排函数并加上单元测试"),
        _turn_completed("2026-02-15T10:00:05.000Z", 3000, 800, cache_read=1000),
    ])
    _write_session(codex_home, "s7", [
        _session_meta("s7", "/p", "gpt-4o"),
        _user_msg("2026-02-16T10:00:00.000Z", "修复登录接口的 bug"),
        _turn_completed("2026-02-16T10:00:05.000Z", 5000, 400),
    ])
    traces, db = collect_codex("2026-02-01", "2026-02-28")

    assert len(traces) == 2
    assert len(db["sessions"]) == 2
    assert db["automation_runs"] == []
    assert db["session_credits"] == []
    for s in db["sessions"]:
        assert "task_type" in s, "会话必须被预分类任务类型"
        assert s["mode"] == CODEX_CHANNEL
        assert s["model"].startswith(f"{CODEX_CHANNEL}:")
        assert "_dialogue_text" not in s, "内部辅助字段不应外泄"
    assert {t["session_id"] for t in traces} == {s["id"] for s in db["sessions"]}


@allure.feature("Codex CLI 适配器")
@allure.story("会话派生")
@allure.title("sessions 接口与 traces 接口结果一致")
def test_sessions_api_consistent_with_traces(codex_home):
    _write_session(codex_home, "s8", [
        _session_meta("s8", "/p", "gpt-5-codex"),
        _user_msg("2026-02-17T10:00:00.000Z", "写个文档"),
        _turn_completed("2026-02-17T10:00:05.000Z", 100, 50),
    ])
    tr = collect_codex_traces("2026-02-01", "2026-02-28")
    db = collect_codex_sessions("2026-02-01", "2026-02-28")
    assert len(tr) == 1 and len(db["sessions"]) == 1
    assert tr[0]["session_id"] == db["sessions"][0]["id"]


@allure.feature("Codex CLI 适配器")
@allure.story("端到端")
@allure.title("--source codex 产出合法 JSON 且可渲染报告")
@allure.severity(allure.severity_level.CRITICAL)
def test_cli_source_codex_end_to_end(codex_home, tmp_path):
    _write_session(codex_home, "e2e", [
        _session_meta("e2e", "/p", "gpt-5-codex"),
        _user_msg("2026-02-18T10:00:00.000Z", "帮我用 python 写一个快排函数并加上单元测试"),
        _turn_completed("2026-02-18T10:00:05.000Z", 4000, 900, cache_read=2000),
        _user_msg("2026-02-18T10:10:00.000Z", "再加个基准测试"),
        _turn_completed("2026-02-18T10:10:08.000Z", 6000, 300),
    ])
    env = dict(os.environ)
    env["CODEX_HOME"] = str(codex_home)
    env["PYTHONIOENCODING"] = "utf-8"

    out_json = tmp_path / "codex.json"
    r = subprocess.run(
        [PY, str(SCRIPTS_DIR / "collect_usage_data.py"),
         "--source", "codex", "--start", "2026-02-01", "--end", "2026-02-28",
         "-o", str(out_json)],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(SKILL_DIR))
    assert r.returncode == 0, f"采集失败: {r.stderr[-800:]}"
    assert out_json.exists()

    d = json.loads(out_json.read_text(encoding="utf-8"))
    assert d["meta"]["source"] == "codex"
    assert len(d["traces"]) == 2
    assert len(d["sessions"]) == 1
    s = d["summary"]
    assert s["total_tokens"] == (4000 + 900) + (6000 + 300)
    assert s["total_cost"] > 0
    assert s["total_effective_cost"] <= s["total_cost"] + 1e-9
    assert all(m["channel"] == CODEX_CHANNEL for m in d["model_exec_stats"])

    out_html = tmp_path / "codex.html"
    r2 = subprocess.run(
        [PY, str(SCRIPTS_DIR / "generate_report.py"), str(out_json),
         "--output", str(out_html), "--format", "html"],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(SKILL_DIR))
    assert r2.returncode == 0, f"报告渲染失败: {r2.stderr[-800:]}"
    assert out_html.exists() and out_html.stat().st_size > 0
    html = out_html.read_text(encoding="utf-8")
    assert "Codex" in html or "codex" in html, "HTML 应体现 Codex 通道"


@allure.feature("Codex CLI 适配器")
@allure.story("端到端")
@allure.title("空 sessions 目录不崩溃，产出空报告")
def test_cli_empty_root(tmp_path):
    empty_home = tmp_path / "empty_home"
    (empty_home / "sessions").mkdir(parents=True)
    env = dict(os.environ)
    env["CODEX_HOME"] = str(empty_home)
    env["PYTHONIOENCODING"] = "utf-8"
    out_json = tmp_path / "empty.json"
    r = subprocess.run(
        [PY, str(SCRIPTS_DIR / "collect_usage_data.py"),
         "--source", "codex", "--start", "2026-02-01", "--end", "2026-02-28",
         "-o", str(out_json)],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(SKILL_DIR))
    assert r.returncode == 0, f"空目录应正常退出: {r.stderr[-800:]}"
    d = json.loads(out_json.read_text(encoding="utf-8"))
    assert d["traces"] == []
    assert d["summary"]["total_tokens"] == 0
