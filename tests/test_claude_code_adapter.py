# -*- coding: utf-8 -*-
"""L1/L3 · Claude Code 适配器测试（P2-1）

覆盖：
  - JSONL 解析：assistant usage → 统一 trace schema（token / 成本 / 通道）
  - 日期窗口过滤（闭区间，越界丢弃）
  - 缓存命中折扣：effective_tokens / effective_cost
  - 健壮性：坏 JSON 行、非 dict、无 usage 的 assistant 行、缺字段
  - 会话派生：title / cwd / task_type / model 前缀
  - 黑盒：collect_usage_data.py --source claude-code 产出合法 JSON 且能渲染报告

⚠️ 本测试通过 CLAUDE_PROJECTS_DIR 环境变量把 fixture 根目录指向 tmp_path，
   绝不读取真实的 ~/.claude/。
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

from adapters.claude_code import (  # noqa: E402
    CLAUDE_CHANNEL,
    _parse_claude_file,
    _ts_to_ms,
    collect_claude_code,
    collect_claude_code_sessions,
    collect_claude_code_traces,
    resolve_claude_projects_root,
)

pytestmark = [pytest.mark.unit, pytest.mark.integration]

PY = sys.executable


# ── fixture 构造 ────────────────────────────────────────────────────────────

def _line(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _user(ts, text, sid="s1", cwd="/Users/t/proj"):
    return _line({
        "type": "user", "timestamp": ts, "sessionId": sid, "cwd": cwd,
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    })


def _assistant(ts, model, inp, out, cache_read=0, cache_create=0,
               uuid="m1", sid="s1", text="ok", usage=True):
    msg = {"role": "assistant", "model": model,
           "content": [{"type": "text", "text": text}]}
    if usage:
        msg["usage"] = {
            "input_tokens": inp, "output_tokens": out,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_create,
        }
    return _line({"type": "assistant", "timestamp": ts, "uuid": uuid,
                  "sessionId": sid, "message": msg})


@pytest.fixture
def projects_root(tmp_path):
    """临时 ~/.claude/projects 根，写入后通过环境变量注入。"""
    root = tmp_path / "projects"
    (root / "-Users-t-proj").mkdir(parents=True)
    old = os.environ.get("CLAUDE_PROJECTS_DIR")
    os.environ["CLAUDE_PROJECTS_DIR"] = str(root)
    yield root
    if old is None:
        os.environ.pop("CLAUDE_PROJECTS_DIR", None)
    else:
        os.environ["CLAUDE_PROJECTS_DIR"] = old


def _write_session(root, name, lines):
    p = root / "-Users-t-proj" / f"{name}.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# ── 解析正确性 ──────────────────────────────────────────────────────────────

@allure.feature("Claude Code 适配器")
@allure.story("JSONL 解析")
@allure.title("assistant usage 行被解析为统一 trace schema")
@allure.severity(allure.severity_level.CRITICAL)
def test_parse_assistant_usage_to_trace(projects_root):
    _write_session(projects_root, "s1", [
        _user("2026-02-10T10:00:00.000Z", "帮我用 python 写一个快排函数并加上单元测试"),
        _assistant("2026-02-10T10:00:05.000Z", "claude-sonnet-4-20250514",
                   2000, 500, cache_read=5000, cache_create=1000, uuid="m1"),
    ])
    traces, meta = _parse_claude_file(
        projects_root / "-Users-t-proj" / "s1.jsonl", "2026-02-01", "2026-02-28")

    assert len(traces) == 1, "应解析出 1 条 trace"
    t = traces[0]
    assert t["date"] == "2026-02-10"
    assert t["session_id"] == "s1"
    assert t["exec_model"] == "claude-sonnet-4-20250514"
    assert t["channel"] == CLAUDE_CHANNEL
    assert t["raw_model"] == f"{CLAUDE_CHANNEL}:claude-sonnet-4-20250514"
    assert t["input_tokens"] == 2000
    assert t["output_tokens"] == 500
    assert t["total_tokens"] == 2500
    assert t["cached_tokens"] == 5000, "cached 应等于 cache_read_input_tokens"
    assert t["_cache_creation_input_tokens"] == 1000
    assert t["call_count"] == 1
    assert t["status"] == "success"
    # 成本必须被正确计价（该模型已在 pricing.json 中登记）
    assert t["total_cost"] > 0, "已登记模型应产出非零成本"
    assert t["effective_cost"] <= t["total_cost"] + 1e-9, "缓存折扣后成本不应高于原价"
    assert meta["title"].startswith("帮我用 python"), "title 应取首条用户消息"
    assert meta["cwd"] == "/Users/t/proj"


@allure.feature("Claude Code 适配器")
@allure.story("缓存折扣")
@allure.title("缓存命中使 effective_tokens / effective_cost 下降")
def test_cache_discount_applied(projects_root):
    _write_session(projects_root, "s2", [
        _user("2026-02-11T09:00:00.000Z", "重构这个模块"),
        _assistant("2026-02-11T09:00:10.000Z", "claude-sonnet-4-20250514",
                   10000, 1000, cache_read=8000, uuid="m2"),
    ])
    traces, _ = _parse_claude_file(
        projects_root / "-Users-t-proj" / "s2.jsonl", "2026-02-01", "2026-02-28")
    t = traces[0]
    assert t["effective_tokens"] < t["total_tokens"], "缓存命中应降低有效 token"
    assert t["effective_cost"] < t["total_cost"], "缓存命中应降低有效成本"


@allure.feature("Claude Code 适配器")
@allure.story("日期过滤")
@allure.title("窗口外的 assistant 行被丢弃")
def test_date_window_filter(projects_root):
    _write_session(projects_root, "s3", [
        _user("2026-01-05T10:00:00.000Z", "窗口前"),
        _assistant("2026-01-05T10:00:05.000Z", "claude-sonnet-4-20250514", 100, 50, uuid="a"),
        _user("2026-02-12T10:00:00.000Z", "窗口内"),
        _assistant("2026-02-12T10:00:05.000Z", "claude-sonnet-4-20250514", 200, 60, uuid="b"),
        _user("2026-03-20T10:00:00.000Z", "窗口后"),
        _assistant("2026-03-20T10:00:05.000Z", "claude-sonnet-4-20250514", 300, 70, uuid="c"),
    ])
    traces, _ = _parse_claude_file(
        projects_root / "-Users-t-proj" / "s3.jsonl", "2026-02-01", "2026-02-28")
    assert len(traces) == 1, "只应保留窗口内的 1 条"
    assert traces[0]["date"] == "2026-02-12"
    assert traces[0]["trace_id"].endswith("b")


# ── 健壮性 ──────────────────────────────────────────────────────────────────

@allure.feature("Claude Code 适配器")
@allure.story("健壮性")
@allure.title("坏行 / 无 usage / 非 dict 不导致崩溃")
def test_malformed_lines_skipped(projects_root):
    p = projects_root / "-Users-t-proj" / "s4.jsonl"
    p.write_text(
        _user("2026-02-13T10:00:00.000Z", "健壮性测试") + "\n"
        + "{ this is not json" + "\n"
        + _line(["not", "a", "dict"]) + "\n"
        + _line({"type": "assistant", "timestamp": "2026-02-13T10:00:01.000Z",
                 "message": {"role": "assistant", "model": "claude-sonnet-4-20250514",
                             "content": []}}) + "\n"          # 无 usage
        + _line({"type": "system", "timestamp": "2026-02-13T10:00:02.000Z"}) + "\n"
        + _assistant("2026-02-13T10:00:03.000Z", "claude-sonnet-4-20250514", 10, 20, uuid="d")
        + "\n",
        encoding="utf-8")
    traces, meta = _parse_claude_file(p, "2026-02-01", "2026-02-28")
    assert len(traces) == 1, "仅保留带 usage 的正常 assistant 行"
    assert meta["id"] == "s4"


@allure.feature("Claude Code 适配器")
@allure.story("健壮性")
@allure.title("未登记模型产出零成本且被标记")
def test_unknown_model_zero_cost(projects_root):
    _write_session(projects_root, "s5", [
        _user("2026-02-14T10:00:00.000Z", "未知模型"),
        _assistant("2026-02-14T10:00:05.000Z", "claude-future-9-20270101", 999, 999, uuid="e"),
    ])
    traces, _ = _parse_claude_file(
        projects_root / "-Users-t-proj" / "s5.jsonl", "2026-02-01", "2026-02-28")
    t = traces[0]
    assert t["total_cost"] == 0.0
    assert t["exec_model"] == "claude-future-9-20270101", "未知模型仍应被记录，不丢数据"


@allure.feature("Claude Code 适配器")
@allure.story("路径探测")
@allure.title("CLAUDE_PROJECTS_DIR 覆盖默认探测路径")
def test_projects_root_override(projects_root):
    assert str(resolve_claude_projects_root()) == str(projects_root)


@allure.feature("Claude Code 适配器")
@allure.story("工具函数")
@allure.title("ISO 时间戳解析为毫秒")
def test_ts_to_ms():
    assert _ts_to_ms("2026-02-10T10:00:00.000Z") > 0
    assert _ts_to_ms("") == 0
    assert _ts_to_ms("not-a-timestamp") == 0


# ── 聚合层集成 ──────────────────────────────────────────────────────────────

@allure.feature("Claude Code 适配器")
@allure.story("会话派生")
@allure.title("collect_claude_code 产出 traces 与可分类的 sessions")
def test_collect_claude_code_returns_traces_and_sessions(projects_root):
    _write_session(projects_root, "s6", [
        _user("2026-02-15T10:00:00.000Z", "帮我用 python 写一个快排函数并加上单元测试"),
        _assistant("2026-02-15T10:00:05.000Z", "claude-sonnet-4-20250514", 3000, 800,
                   cache_read=1000, uuid="f"),
    ])
    _write_session(projects_root, "s7", [
        _user("2026-02-16T10:00:00.000Z", "修复登录接口的 bug"),
        _assistant("2026-02-16T10:00:05.000Z", "claude-opus-4-5-20251101", 5000, 400, uuid="g"),
    ])
    traces, db = collect_claude_code("2026-02-01", "2026-02-28")

    assert len(traces) == 2
    assert len(db["sessions"]) == 2
    assert db["automation_runs"] == []
    assert db["session_credits"] == []
    for s in db["sessions"]:
        assert "task_type" in s, "会话必须被预分类任务类型"
        assert s["mode"] == CLAUDE_CHANNEL
        assert s["model"].startswith(f"{CLAUDE_CHANNEL}:")
        assert "_dialogue_text" not in s, "内部辅助字段不应外泄"
    # 每个 session 的 id 与 trace 可关联
    assert {t["session_id"] for t in traces} == {s["id"] for s in db["sessions"]}


@allure.feature("Claude Code 适配器")
@allure.story("会话派生")
@allure.title("sessions 接口与 traces 接口结果一致")
def test_sessions_api_consistent_with_traces(projects_root):
    _write_session(projects_root, "s8", [
        _user("2026-02-17T10:00:00.000Z", "写个文档"),
        _assistant("2026-02-17T10:00:05.000Z", "claude-haiku-4-20250514", 100, 50, uuid="h"),
    ])
    tr = collect_claude_code_traces("2026-02-01", "2026-02-28")
    db = collect_claude_code_sessions("2026-02-01", "2026-02-28")
    assert len(tr) == 1 and len(db["sessions"]) == 1
    assert tr[0]["session_id"] == db["sessions"][0]["id"]


# ── 黑盒：端到端 CLI ────────────────────────────────────────────────────────

@allure.feature("Claude Code 适配器")
@allure.story("端到端")
@allure.title("--source claude-code 产出合法 JSON 且可渲染报告")
@allure.severity(allure.severity_level.CRITICAL)
def test_cli_source_claude_code_end_to_end(projects_root, tmp_path):
    _write_session(projects_root, "e2e", [
        _user("2026-02-18T10:00:00.000Z", "帮我用 python 写一个快排函数并加上单元测试"),
        _assistant("2026-02-18T10:00:05.000Z", "claude-sonnet-4-20250514", 4000, 900,
                   cache_read=2000, uuid="i"),
        _user("2026-02-18T10:10:00.000Z", "再加个基准测试"),
        _assistant("2026-02-18T10:10:08.000Z", "claude-opus-4-5-20251101", 6000, 300, uuid="j"),
    ])
    env = dict(os.environ)
    env["CLAUDE_PROJECTS_DIR"] = str(projects_root)
    env["PYTHONIOENCODING"] = "utf-8"

    out_json = tmp_path / "cc.json"
    r = subprocess.run(
        [PY, str(SCRIPTS_DIR / "collect_usage_data.py"),
         "--source", "claude-code", "--start", "2026-02-01", "--end", "2026-02-28",
         "-o", str(out_json)],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(SKILL_DIR))
    assert r.returncode == 0, f"采集失败: {r.stderr[-800:]}"
    assert out_json.exists()

    d = json.loads(out_json.read_text(encoding="utf-8"))
    assert d["meta"]["source"] == "claude-code"
    assert len(d["traces"]) == 2
    assert len(d["sessions"]) == 1
    s = d["summary"]
    assert s["total_tokens"] == (4000 + 900) + (6000 + 300)
    assert s["total_cost"] > 0
    assert s["total_effective_cost"] <= s["total_cost"] + 1e-9
    # 每个模型都要有通道标记
    assert all(m["channel"] == CLAUDE_CHANNEL for m in d["model_exec_stats"])

    # 报告可渲染
    out_html = tmp_path / "cc.html"
    r2 = subprocess.run(
        [PY, str(SCRIPTS_DIR / "generate_report.py"), str(out_json),
         "--output", str(out_html), "--format", "html"],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(SKILL_DIR))
    assert r2.returncode == 0, f"报告渲染失败: {r2.stderr[-800:]}"
    assert out_html.exists() and out_html.stat().st_size > 0
    html = out_html.read_text(encoding="utf-8")
    assert "Claude" in html or "claude" in html, "HTML 应体现 Claude 通道"


@allure.feature("Claude Code 适配器")
@allure.story("端到端")
@allure.title("空 projects 目录不崩溃，产出空报告")
def test_cli_empty_projects_root(tmp_path):
    empty = tmp_path / "empty_projects"
    empty.mkdir()
    env = dict(os.environ)
    env["CLAUDE_PROJECTS_DIR"] = str(empty)
    env["PYTHONIOENCODING"] = "utf-8"
    out_json = tmp_path / "empty.json"
    r = subprocess.run(
        [PY, str(SCRIPTS_DIR / "collect_usage_data.py"),
         "--source", "claude-code", "--start", "2026-02-01", "--end", "2026-02-28",
         "-o", str(out_json)],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(SKILL_DIR))
    assert r.returncode == 0, f"空目录应正常退出: {r.stderr[-800:]}"
    d = json.loads(out_json.read_text(encoding="utf-8"))
    assert d["traces"] == []
    assert d["summary"]["total_tokens"] == 0
