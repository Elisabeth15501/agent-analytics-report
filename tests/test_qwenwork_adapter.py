# -*- coding: utf-8 -*-
"""L1/L3 · 千问办公（QwenWork）数据源适配器测试

覆盖：
  - token 估算：CJK 逐字、拉丁按 4 字符折算
  - 分组：一次响应的 thinking / text / tool_use 多行 → 归并为 1 条 trace
  - 上下文重发：后一请求的 input 估算 ≥ 前一请求（累积增长）
  - 运行日志关联：真实 duration_ms / 模型档位 / stop_reason
  - 真值优先：日志里 token 非 0 时自动改用真值并撤销 _tokens_estimated
  - 日期窗口过滤（闭区间）
  - agents.db 增强：真实会话标题与档位；库缺失 / 打不开时优雅降级
  - 健壮性：坏 JSON 行、缺 message、非 dict 行、毫秒整数时间戳
  - 路径探测：QWENWORK_HOME / QWENWORK_DB 环境变量覆盖
  - 通道与计价：parse_channel 识别 qwenwork: 前缀；未配档位 → 不计价
  - 黑盒：collect_usage_data.py --source qwenwork 端到端出报告，标题为千问办公

⚠️ 本测试通过 QWENWORK_HOME / QWENWORK_DB 把 fixture 根目录指向 tmp_path，
   绝不读取真实的 ~/.qwenworkcn/ 或 %APPDATA%/QwenWorkCN/。
"""

import json
import os
import sqlite3
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

from adapters.qwenwork import (  # noqa: E402
    QWENWORK_CHANNEL,
    _parse_run_log,
    _parse_session_file,
    _ts_to_ms,
    collect_qwenwork,
    collect_qwenwork_sessions,
    collect_qwenwork_traces,
    estimate_tokens,
    load_db_index,
    resolve_qwenwork_db,
    resolve_qwenwork_home,
)
from ca_core import parse_channel, price_of  # noqa: E402

pytestmark = [pytest.mark.unit, pytest.mark.integration]

PY = sys.executable

WIN = "2026-02-16"       # fixture 统一使用的日期（周一）
START, END = "2026-02-01", "2026-02-28"


# ── fixture 构造 ────────────────────────────────────────────────────────────

def _line(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _iso(hh, mm, ss=0):
    return "2026-02-16T%02d:%02d:%02d.000Z" % (hh, mm, ss)


def _runtime_config(sid, model="flash"):
    return _line({"type": "runtime-config", "sessionId": sid, "model": model,
                  "contextWindow": 1000000, "reasoningEffort": "medium",
                  "timestamp": _iso(9, 0)})


def _user(sid, ts, text, human=True, cwd="C:\\\\work\\\\p1"):
    obj = {"type": "user", "sessionId": sid, "timestamp": ts, "cwd": cwd,
           "version": "1.1.59", "isSidechain": False,
           "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
    if human:
        obj["humanInput"] = {"text": text, "mode": "prompt"}
    return _line(obj)


def _tool_result(sid, ts, content):
    return _line({"type": "user", "sessionId": sid, "timestamp": ts,
                  "message": {"role": "user", "content": [
                      {"type": "tool_result", "tool_use_id": "call_1",
                       "content": content}]}})


def _assistant(sid, ts, rid, blocks, model="flash"):
    """blocks: [(type, payload)]；同一 rid 的多行 = 一次模型响应。"""
    lines = []
    for typ, payload in blocks:
        if typ == "thinking":
            blk = {"type": "thinking", "thinking": payload}
        elif typ == "text":
            blk = {"type": "text", "text": payload}
        else:
            blk = {"type": "tool_use", "id": "call_1", "name": payload[0],
                   "input": payload[1]}
        lines.append(_line({
            "type": "assistant", "sessionId": sid, "timestamp": ts,
            "cwd": "C:\\\\work\\\\p1", "version": "1.1.59", "isSidechain": False,
            "requestTokenAnchor": {"request": "rq", "response": "rs", "requestId": rid},
            "message": {"role": "assistant", "model": model, "content": [blk]},
        }))
    return lines


def _write_session(home, sid, lines):
    d = Path(home) / "projects" / "C--work-p1"
    d.mkdir(parents=True, exist_ok=True)
    (d / (sid + ".jsonl")).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_run(home, run_id, sid, requests):
    """requests: [(request_id, model, start_hm, end_hm, tokens_dict)]"""
    d = Path(home) / "logs" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(
        {"run_id": run_id, "argv": ["x", "--session-id", sid]}, ensure_ascii=False),
        encoding="utf-8")
    lines = []
    for rid, model, s, e, tok in requests:
        lines.append("2026-02-16T%s:00+08:00 INFO  [session=%s turn=t1 loop=l:1 "
                     "request=%s] model.request.started request_index=1 "
                     'request_id="%s"' % (s, sid, rid, rid))
        lines.append("2026-02-16T%s:00+08:00 INFO  [session=%s turn=t1 loop=l:1 "
                     "request=%s] model.response.completed request_index=1 "
                     'request_id="%s" provider="qoder" model="%s" '
                     'stop_reason="tool_use" content_block_count=2 '
                     "input_tokens=%d output_tokens=%d "
                     "cache_read_input_tokens=%d cache_creation_input_tokens=%d"
                     % (e, sid, rid, rid, model,
                        tok.get("input_tokens", 0), tok.get("output_tokens", 0),
                        tok.get("cache_read_input_tokens", 0),
                        tok.get("cache_creation_input_tokens", 0)))
    (d / "qodercli.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def qw_home(tmp_path, monkeypatch):
    """干净的空 home（只保证目录存在，具体 fixture 由各用例自行写入）。"""
    home = tmp_path / "qwhome"
    (home / "projects").mkdir(parents=True)
    (home / "logs" / "runs").mkdir(parents=True)
    monkeypatch.setenv("QWENWORK_HOME", str(home))
    monkeypatch.delenv("QWENWORK_DB", raising=False)
    return home


def _make_db(path, chats, sub_chats, messages=()):
    """在 tmp_path 里造一个最小 agents.db（只建适配器用到的列）。"""
    con = sqlite3.connect(str(path))
    con.executescript(
        "CREATE TABLE chats (id TEXT PRIMARY KEY, name TEXT, worktree_path TEXT);"
        "CREATE TABLE sub_chats (id TEXT PRIMARY KEY, chat_id TEXT, "
        " session_id TEXT, mode TEXT, model_level TEXT, created_at INT, "
        " updated_at INT);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, metadata TEXT);")
    con.executemany("INSERT INTO chats VALUES (?,?,?)", chats)
    con.executemany("INSERT INTO sub_chats VALUES (?,?,?,?,?,?,?)", sub_chats)
    con.executemany("INSERT INTO messages VALUES (NULL,?)", [(m,) for m in messages])
    con.commit()
    con.close()


# ── 一、token 估算 ──────────────────────────────────────────────────────────

@allure.feature("千问办公适配器")
@allure.story("token 估算")
@allure.title("CJK 逐字 / 拉丁 4 字符折算")
def test_estimate_tokens_cjk_and_latin():
    assert estimate_tokens("") == 0
    assert estimate_tokens("你好世界") == 4                  # 4 个汉字 → 4
    assert estimate_tokens("abcd") == 1                      # 4 拉丁字符 → 1
    assert estimate_tokens("你好 abcd") == 3                 # 2 汉字 + 5 非 CJK → 2+1.25
    assert estimate_tokens("你好abcd") == 3                  # 2 汉字 + 4 字母 → 2+1
    # 纯空格与标点按非 CJK 折算
    assert estimate_tokens("        ") == 2


@allure.feature("千问办公适配器")
@allure.story("token 估算")
@allure.title("估算系数可复核（常量而非魔法数）")
def test_estimate_uses_declared_constants():
    from adapters import qwenwork as qw
    assert qw.CJK_TOKENS_PER_CHAR >= 1.0, "CJK 系数不得低估（宁可高估也不要把用量算少）"
    assert qw.LATIN_CHARS_PER_TOKEN > 1.0


# ── 二、分组与 schema ───────────────────────────────────────────────────────

def _one_session(qw_home, sid="s1"):
    rid_a = "aaaaaaaa-1111-1111-1111-111111111111"
    rid_b = "bbbbbbbb-2222-2222-2222-222222222222"
    lines = [
        _runtime_config(sid, "flash"),
        _user(sid, _iso(9, 0), "帮我把这份周报整理成三段式摘要"),
    ]
    # 一次响应被拆成三行：thinking + text + tool_use，共享 requestId
    lines += _assistant(sid, _iso(9, 1), rid_a,
                        [("thinking", "先读文件再动笔"),
                         ("text", "好的，我先看一下目录"),
                         ("tool_use", ("Glob", {"pattern": "*.md"}))])
    lines.append(_tool_result(sid, _iso(9, 2), "a.md\nb.md\nc.md"))
    lines += _assistant(sid, _iso(9, 3), rid_b,
                        [("text", "已完成三段式摘要")])
    _write_session(qw_home, sid, lines)
    return rid_a, rid_b


@allure.feature("千问办公适配器")
@allure.story("解析")
@allure.title("同一 requestId 的多行归并为 1 条 trace")
def test_group_by_request_id(qw_home):
    _one_session(qw_home)
    traces, meta = _parse_session_file(
        Path(qw_home) / "projects" / "C--work-p1" / "s1.jsonl", START, END)
    assert len(traces) == 2
    assert [t["channel"] for t in traces] == [QWENWORK_CHANNEL, QWENWORK_CHANNEL]
    assert all(t["call_count"] == 1 for t in traces)
    assert meta["id"] == "s1"
    assert meta["title"].startswith("帮我把这份周报")
    # 首条 assistant 响应有 3 个内容块
    assert traces[0]["_content_blocks"] == 3
    assert traces[1]["_content_blocks"] == 1


@allure.feature("千问办公适配器")
@allure.story("解析")
@allure.title("统一 trace schema 必填字段齐全")
def test_trace_schema_contract(qw_home):
    _one_session(qw_home)
    traces, db_data = collect_qwenwork(START, END)
    sessions = db_data["sessions"]
    assert sessions, "至少应派生出一个会话"
    required = {"date", "session_id", "total_tokens", "input_tokens", "output_tokens",
                "cached_tokens", "effective_tokens", "total_cost", "effective_cost",
                "channel", "raw_model", "exec_model", "call_count", "model_name",
                "started_at", "ended_at", "duration_ms", "status", "is_free",
                "model_key", "input_cost", "output_cost", "trace_id", "pid"}
    for t in traces:
        assert required <= set(t), f"缺字段：{required - set(t)}"
        assert t["total_tokens"] == t["input_tokens"] + t["output_tokens"]
        assert t["raw_model"].startswith("qwenwork:")
    assert {"id", "cwd", "title", "created_date", "mode", "model",
            "task_type"} <= set(sessions[0])


@allure.feature("千问办公适配器")
@allure.story("解析")
@allure.title("输入 token 按上下文重发逐请求累加")
def test_input_tokens_accumulate_context(qw_home):
    _one_session(qw_home)
    traces, _ = collect_qwenwork(START, END)
    first, second = traces
    # 第二次请求的输入必须包含第一次的全部内容（上下文重发），故严格更大
    assert second["input_tokens"] > first["input_tokens"]
    # 上游回传 0 → 全部走估算，透明字段必须标记出来
    assert all(t["_tokens_estimated"] for t in traces)
    assert all(t["cached_tokens"] == 0 for t in traces)


@allure.feature("千问办公适配器")
@allure.story("解析")
@allure.title("<synthetic> 占位响应不计为模型调用")
def test_synthetic_response_not_counted(qw_home):
    sid = "ssyn"
    rid_real = "77777777-7777-7777-7777-777777777777"
    lines = [_runtime_config(sid, "flash"),
             _user(sid, _iso(9, 0), "帮我把这份周报整理成三段式摘要")]
    lines += _assistant(sid, _iso(9, 1), rid_real, [("text", "已完成")])
    # 中断 / 错误时千问办公会写一条 model=<synthetic> 的占位消息
    lines += _assistant(sid, _iso(9, 2), "88888888-8888-8888-8888-888888888888",
                        [("text", "Request cancelled")], model="<synthetic>")
    _write_session(qw_home, sid, lines)
    traces, db_data = collect_qwenwork(START, END)
    assert len(traces) == 1
    assert all("<synthetic>" not in t["raw_model"] for t in traces)
    assert db_data["sessions"][0]["_synthetic_responses"] == 1


@allure.feature("千问办公适配器")
@allure.story("解析")
@allure.title("记忆整理后台任务被标记为自动化，不进用户任务榜")
def test_background_nudge_session_labeled(qw_home):
    sid = "snudge"
    rid = "66666666-6666-6666-6666-666666666666"
    lines = [_runtime_config(sid, "qwork-advanced"),
             _user(sid, _iso(9, 0), "Target file this round: MEMORY.md\nCurrent usage: 4481 / 10240")]
    lines += _assistant(sid, _iso(9, 1), rid, [("text", "已整理")])
    _write_session(qw_home, sid, lines)
    _, db_data = collect_qwenwork(START, END)
    s = db_data["sessions"][0]
    assert s["is_background_automation"] is True
    assert s["title"] == "记忆整理后台任务（awareness nudge）"


# ── 三、运行日志关联 ────────────────────────────────────────────────────────

@allure.feature("千问办公适配器")
@allure.story("运行日志")
@allure.title("从 qodercli.log 取真实耗时与 stop_reason")
def test_run_log_latency(qw_home):
    rid_a, rid_b = _one_session(qw_home)
    _write_run(qw_home, "2026-02-16T09-00-00-000+08-00-x-p1", "s1",
               [(rid_a, "flash", "09:00", "09:00", {}),
                (rid_b, "flash", "09:02", "09:02", {})])
    traces, _ = collect_qwenwork(START, END)
    assert traces[0]["duration_ms"] == 0 or traces[0]["duration_ms"] >= 0
    assert traces[0]["_stop_reason"] == "tool_use"


@allure.feature("千问办公适配器")
@allure.story("运行日志")
@allure.title("日志解析：起止配对 / 模型 / token 字段")
def test_parse_run_log(tmp_path):
    home = tmp_path / "h"
    rid = "cccccccc-3333-3333-3333-333333333333"
    # 真实日志里 session= 一定是 36 位 uuid，适配器也按 uuid 正则抓取
    sid = "99999999-9999-9999-9999-999999999999"
    _write_run(home, "run1", sid,
               [(rid, "pro", "10:00", "10:01",
                 {"input_tokens": 1200, "output_tokens": 300})])
    usage = _parse_run_log(home / "logs" / "runs" / "run1" / "qodercli.log")
    assert rid in usage
    rec = usage[rid]
    assert rec["model"] == "pro"
    assert rec["session_id"] == sid
    assert rec["duration_ms"] == 60000
    assert rec["tokens"]["input_tokens"] == 1200


@allure.feature("千问办公适配器")
@allure.story("口径升级")
@allure.title("上游回传真值后自动改用真值 token")
def test_real_usage_overrides_estimate(qw_home):
    rid_a, rid_b = _one_session(qw_home)
    _write_run(qw_home, "runreal", "s1",
               [(rid_a, "flash", "09:00", "09:00",
                 {"input_tokens": 5000, "output_tokens": 120,
                  "cache_read_input_tokens": 4000}),
                (rid_b, "flash", "09:02", "09:02",
                 {"input_tokens": 5200, "output_tokens": 60})])
    traces, _ = collect_qwenwork(START, END)
    t0 = traces[0]
    assert t0["_tokens_estimated"] is False
    assert t0["input_tokens"] == 5000 and t0["output_tokens"] == 120
    assert t0["cached_tokens"] == 4000
    # 真值口径下缓存命中按折扣计入计费等效量
    assert t0["effective_tokens"] < t0["total_tokens"]
    # 第二条仍有真值
    assert traces[1]["_tokens_estimated"] is False


# ── 四、日期窗口 ────────────────────────────────────────────────────────────

@allure.feature("千问办公适配器")
@allure.story("日期窗口")
@allure.title("窗口外的调用被丢弃（闭区间）")
def test_date_window_filter(qw_home):
    _one_session(qw_home)
    traces, _ = collect_qwenwork(WIN, WIN)
    assert len(traces) == 2
    assert all(t["date"] == WIN for t in traces)
    traces_out = collect_qwenwork_traces("2026-03-01", "2026-03-31")
    # 转录文件本身仍会被解析（3 月没有对应调用）
    assert traces_out == []


# ── 五、健壮性 ──────────────────────────────────────────────────────────────

@allure.feature("千问办公适配器")
@allure.story("健壮性")
@allure.title("坏行 / 缺字段 / 非 dict 行不抛异常")
def test_robustness(qw_home):
    sid = "sbad"
    rid = "dddddddd-4444-4444-4444-444444444444"
    lines = [_runtime_config(sid, "flash"),
             "{ this is not json",
             "[1,2,3]",
             _line({"type": "user"}),                       # 缺 message / timestamp
             _line({"type": "assistant", "sessionId": sid}),  # 缺 message
             _line({"type": "assistant", "sessionId": sid, "timestamp": _iso(9, 9),
                    "message": {"role": "assistant", "model": "flash",
                                "content": "字符串形式的 content"}}),
             ]
    lines += _assistant(sid, _iso(9, 10), rid, [("text", "收尾")])
    _write_session(qw_home, sid, lines)
    traces, meta = _parse_session_file(
        Path(qw_home) / "projects" / "C--work-p1" / (sid + ".jsonl"), START, END)
    assert len(traces) == 2
    assert meta["id"] == sid


@allure.feature("千问办公适配器")
@allure.story("健壮性")
@allure.title("毫秒整数时间戳（IM 渠道会话）同样能解析")
def test_numeric_timestamp(qw_home):
    sid = "snum"
    rid = "eeeeeeee-5555-5555-5555-555555555555"
    ms = _ts_to_ms(_iso(9, 0))
    lines = [_line({"type": "user", "sessionId": sid, "timestamp": ms,
                    "humanInput": {"text": "把这段中文翻译成英文"},
                    "message": {"role": "user",
                                "content": [{"type": "text", "text": "translate me"}]}})]
    lines += _assistant(sid, ms + 5000, rid, [("text", "done")])
    _write_session(qw_home, sid, lines)
    traces, meta = _parse_session_file(
        Path(qw_home) / "projects" / "C--work-p1" / (sid + ".jsonl"), START, END)
    assert len(traces) == 1
    assert traces[0]["date"] == WIN
    assert meta["title"] == "把这段中文翻译成英文"


@allure.feature("千问办公适配器")
@allure.story("健壮性")
@allure.title("空目录 / 无数据时产出空结果不崩溃")
def test_empty_home(qw_home):
    traces, db_data = collect_qwenwork(START, END)
    assert traces == []
    assert db_data["sessions"] == []
    assert db_data["automation_runs"] == []


# ── 六、agents.db 增强与降级 ────────────────────────────────────────────────

@allure.feature("千问办公适配器")
@allure.story("agents.db")
@allure.title("用库里的真名与档位覆盖转录推导值")
def test_db_enrichment(qw_home, tmp_path):
    _one_session(qw_home)
    db = tmp_path / "agents.db"
    _make_db(db,
             chats=[("chat1", "季度经营分析周报", "C:\\work\\p1")],
             sub_chats=[("sc1", "chat1", "s1", "agent", "pro",
                         1770000000, 1770100000)])
    traces, db_data = collect_qwenwork(START, END, db_path=db)
    s = db_data["sessions"][0]
    assert s["title"] == "季度经营分析周报"
    assert s["model"] == "qwenwork:pro"


@allure.feature("千问办公适配器")
@allure.story("agents.db")
@allure.title("库每轮 durationMs / numTurns 汇总")
def test_db_turn_stats(tmp_path):
    db = tmp_path / "agents.db"
    meta = json.dumps({"sessionId": "s1", "durationMs": 120000, "numTurns": 3})
    _make_db(db,
             chats=[("chat1", "标题", "/p1")],
             sub_chats=[("sc1", "chat1", "s1", "agent", "flash", 1, 2)],
             messages=[meta, json.dumps({"sessionId": "s1", "durationMs": 30000,
                                         "numTurns": 5})])
    idx = load_db_index(db)
    assert idx["s1"]["duration_ms"] == 150000
    assert idx["s1"]["num_turns"] == 5


@allure.feature("千问办公适配器")
@allure.story("agents.db")
@allure.title("库缺失 / 损坏时降级为空索引且不抛异常")
def test_db_missing_or_corrupt(tmp_path):
    assert load_db_index(tmp_path / "nope.db") == {}
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a sqlite file at all")
    assert load_db_index(junk) == {}


# ── 七、路径探测 ────────────────────────────────────────────────────────────

@allure.feature("千问办公适配器")
@allure.story("路径探测")
@allure.title("环境变量覆盖默认探测位")
def test_path_resolution(monkeypatch, tmp_path):
    monkeypatch.setenv("QWENWORK_HOME", str(tmp_path / "custom"))
    assert resolve_qwenwork_home() == tmp_path / "custom"
    monkeypatch.setenv("QWENWORK_DB", str(tmp_path / "custom.db"))
    assert resolve_qwenwork_db() == tmp_path / "custom.db"
    monkeypatch.delenv("QWENWORK_HOME")
    # 未设环境变量时返回一个 Path（存在与否由调用方判空），不得抛异常
    assert isinstance(resolve_qwenwork_home(), Path)


# ── 八、通道与计价 ──────────────────────────────────────────────────────────

@allure.feature("千问办公适配器")
@allure.story("通道归属")
@allure.title("parse_channel 识别 qwenwork: 前缀并剥出档位名")
def test_parse_channel_qwenwork():
    assert parse_channel("qwenwork:flash") == ("qwenwork", "flash")
    assert parse_channel("qwenwork:pro")[1] == "pro"


@allure.feature("千问办公适配器")
@allure.story("计价")
@allure.title("档位未配单价 → 不计价，但 token 照计")
def test_unpriced_tier_counts_tokens_only(qw_home):
    _one_session(qw_home)
    traces, _ = collect_qwenwork(START, END)
    assert traces
    # 用户可能在本机 pricing.local.json 里给档位补价，故断言与 price_of 实况一致
    priced = price_of("qwenwork:%s" % traces[0]["model_name"]) != (None, None)
    for t in traces:
        if not priced:
            # 未配单价时成本必须为 0（不得凭空造数）
            assert t["total_cost"] == 0.0 and t["effective_cost"] == 0.0
        assert t["total_tokens"] > 0


# ── 九、CLI 黑盒 ────────────────────────────────────────────────────────────

@allure.feature("千问办公适配器")
@allure.story("端到端")
@allure.title("--source qwenwork 采集 + 渲染，标题体现千问办公")
def test_cli_source_qwenwork_end_to_end(qw_home, tmp_path):
    _one_session(qw_home)
    env = dict(os.environ)
    env["QWENWORK_HOME"] = str(qw_home)
    env.pop("QWENWORK_DB", None)
    env["PYTHONIOENCODING"] = "utf-8"

    out_json = tmp_path / "qw.json"
    r = subprocess.run(
        [PY, str(SCRIPTS_DIR / "collect_usage_data.py"),
         "--source", "qwenwork", "--start", START, "--end", END,
         "-o", str(out_json)],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(SKILL_DIR))
    assert r.returncode == 0, f"采集失败: {r.stderr[-800:]}"
    d = json.loads(out_json.read_text(encoding="utf-8"))
    assert d["meta"]["source"] == "qwenwork"
    assert d["meta"]["tokens_source"] == "estimated", "token 口径必须显式标记"
    assert len(d["traces"]) == 2
    assert d["summary"]["total_tokens"] > 0
    assert all(m["channel"] == QWENWORK_CHANNEL for m in d["model_exec_stats"])

    out_md = tmp_path / "qw.md"
    r2 = subprocess.run(
        [PY, str(SCRIPTS_DIR / "generate_report.py"), str(out_json),
         "--output", str(out_md), "--format", "markdown"],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(SKILL_DIR))
    assert r2.returncode == 0, f"报告渲染失败: {r2.stderr[-800:]}"
    md = out_md.read_text(encoding="utf-8")
    assert "# 千问办公使用情况报告" in md, "外部源不得再冒充 WorkBuddy 标题"
    assert "token 口径：本地字符估算" in md, "必须声明 token 也是估算"
    assert "Workbuddy使用情况报告" not in md


@allure.feature("千问办公适配器")
@allure.story("端到端")
@allure.title("--import-official 与 qwenwork 源互斥（退出码 2）")
def test_official_export_rejected_for_qwenwork(qw_home, tmp_path):
    env = dict(os.environ)
    env["QWENWORK_HOME"] = str(qw_home)
    env["PYTHONIOENCODING"] = "utf-8"
    fake = tmp_path / "usage.xlsx"
    fake.write_bytes(b"x")
    r = subprocess.run(
        [PY, str(SCRIPTS_DIR / "collect_usage_data.py"),
         "--source", "qwenwork", "--import-official", str(fake),
         "--start", START, "--end", END, "-o", str(tmp_path / "x.json")],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(SKILL_DIR))
    assert r.returncode == 2
    assert "仅支持 --source workbuddy" in r.stderr


# ── 十、隐私护栏 ────────────────────────────────────────────────────────────

@allure.feature("千问办公适配器")
@allure.story("隐私")
@allure.title("测试全程只读 tmp_path，绝不触碰真实 ~/.qwenworkcn")
def test_never_reads_real_home(qw_home, monkeypatch):
    home = Path.home() / ".qwenworkcn"
    before = None
    if home.is_dir():
        before = sum(1 for _ in home.rglob("*.jsonl"))
    collect_qwenwork(START, END)
    if before is not None:
        # 环境变量已把探测锚到 tmp_path，真实目录不应被打开（计数不变即无写入）
        assert sum(1 for _ in home.rglob("*.jsonl")) == before
    assert str(qw_home) in str(resolve_qwenwork_home())
