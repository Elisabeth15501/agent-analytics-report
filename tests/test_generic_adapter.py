# -*- coding: utf-8 -*-
"""阶段 1 / P0：通用适配器验收 A1–A14。

所有 fixture 由 tmp_path + 环境变量隔离，绝不读用户真实目录。
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import allure

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SKILL_DIR))

from adapters import generic as G  # noqa: E402

PY = sys.executable
pytestmark = [pytest.mark.regression, pytest.mark.integration]

DAY = "2026-02-16"
DAY2 = "2026-02-17"


@pytest.fixture
def gen_home(tmp_path, monkeypatch):
    root = tmp_path / "generic-home"
    (root / "projects").mkdir(parents=True)
    monkeypatch.setenv("GENERIC_HOME", str(root))
    monkeypatch.setenv("GENERIC_PROJECTS_DIR", str(root / "projects"))
    return root


def _write_jsonl(root, rows):
    path = root / "projects" / "session.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")
    return path


def _assistant(ts, sid, usage=None, text="", model="glm-5.2", rid=""):
    return {
        "timestamp": ts, "session_id": sid, "model": model,
        "message": {"role": "assistant", "content": text, "usage": usage or {}},
        "request_id": rid,
    }


def _cli(tmp_path, extra=None, env_extra=None, home=None, out_name="data.json"):
    """黑盒跑采集 CLI，返回 (proc, data)；所有源目录由 env 指向 tmp。"""
    env = os.environ.copy()
    env.update({
        "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
        "QWENWORK_HOME": str(tmp_path / "empty-qw"),
        "CLAUDE_PROJECTS_DIR": str(tmp_path / "empty-claude"),
        "CODEX_HOME": str(tmp_path / "empty-codex"),
        "OPENCLAW_HOME": str(tmp_path / "empty-openclaw"),
    })
    if home is not None:
        env["GENERIC_HOME"] = str(home)
        env["GENERIC_PROJECTS_DIR"] = str(home / "projects")
    env.update(env_extra or {})
    out = tmp_path / out_name
    cmd = [PY, str(SCRIPTS / "collect_usage_data.py"),
           "--start", DAY, "--end", DAY2, "--output", str(out), "--source", "generic"]
    cmd.extend(extra or [])
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR), env=env, capture_output=True, text=True)
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    return proc, data


def _report(tmp_path, data, fmt="markdown"):
    src = tmp_path / "report-data.json"
    src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / f"report.{fmt}"
    proc = subprocess.run(
        [PY, str(SCRIPTS / "generate_report.py"), str(src),
         "--output", str(out), "--format", fmt],
        cwd=str(SKILL_DIR), capture_output=True, text=True,
    )
    return proc, (out.read_text(encoding="utf-8") if out.exists() else "")


# ── 单元：路径 / 隐私 / 分层 ────────────────────────────────────────────────

@allure.feature("通用适配器")
@allure.title("A8 环境变量与根目录解析")
def test_generic_home_resolution(gen_home, monkeypatch):
    assert G.resolve_generic_home() == gen_home
    assert G.resolve_generic_projects_root() == gen_home / "projects"
    monkeypatch.delenv("GENERIC_HOME")
    assert G.resolve_generic_home().name == ".generic-agent"


@allure.feature("通用适配器")
@allure.title("A9 符号链接与 memory/ 目录不被跟随")
def test_no_symlink_no_memory(gen_home, tmp_path):
    secret = tmp_path / "secret"
    secret.mkdir()
    _write_jsonl(secret.parent / "generic-home", [])  # 保证 projects 存在
    (secret / "leak.jsonl").write_text(
        json.dumps(_assistant(f"{DAY}T01:00:00+08:00", "leak", {"input_tokens": 999})),
        encoding="utf-8",
    )
    link = gen_home / "projects" / "linked"
    try:
        link.symlink_to(secret, target_is_directory=True)
    except OSError as exc:
        pytest.fail(f"测试环境不支持创建符号链接，无法验证 A9：{exc}")
    (gen_home / "memory").mkdir(exist_ok=True)
    (gen_home / "memory" / "note.jsonl").write_text(
        json.dumps(_assistant(f"{DAY}T01:00:00+08:00", "mem", {"input_tokens": 888})),
        encoding="utf-8",
    )
    hints = G.detect_hints(gen_home)
    assert not any("linked" in p for p in hints["jsonl"])
    assert not any("memory" in Path(p).parts for p in hints["jsonl"])
    traces, _ = G.collect_generic(DAY, DAY2, home=gen_home)
    assert all(t["session_id"] not in ("leak", "mem") for t in traces)


@allure.feature("通用适配器")
@allure.title("A11/A12 token 四层标记与 missing 不写 0 真值")
def test_token_layers_and_missing():
    inp, out, cached, src, layer, est, missing = G._token_fields(
        {"input_tokens": 10, "output_tokens": 5, "cache_read.input_tokens": 2}, "x"
    )
    assert (inp, out, cached, src, layer, est, missing) == (10, 5, 2, "reported", "L2", False, False)
    inp, out, _, src, layer, est, missing = G._token_fields(
        {"output_tokens": 7}, ""
    )
    assert (inp, out, src, layer, est, missing) == (0, 7, "reported_partial", "L2", False, False)
    inp, _, _, src, layer, est, missing = G._token_fields({}, "你好世界")
    assert src == "estimated_heuristic" and layer == "L4" and est and not missing
    assert inp == G.estimate_tokens("你好世界") == 4
    inp, _, _, src, layer, est, missing = G._token_fields({}, "")
    assert src == "missing" and layer == "missing" and missing and inp == 0
    # L3 由调用方显式提供 tokenizer，不假装有内建 tokenizer
    _, _, _, src, layer, est, _ = G._token_fields({}, "abcd", tokenizer=lambda s: 1)
    assert (src, layer, est) == ("estimated_tokenizer", "L3", True)


@allure.feature("通用适配器")
@allure.title("A14 OTel 别名层与 pin 提交")
def test_otel_alias_and_pin():
    mapped = G.map_otel_attrs({"cache_read_input_tokens": 3, "input_tokens": 7})
    assert mapped["cache_read.input_tokens"] == 3 and mapped["input_tokens"] == 7
    assert G.OTEL_PIN_COMMIT == "afd3ff6b"
    source = (SKILL_DIR / "adapters" / "generic.py").read_text(encoding="utf-8")
    assert "gen_ai.usage.cache_creation_input_tokens" in source  # 只在别名表出现
    assert source.count("cache_read_input_tokens") <= 2


# ── SQLite 五 profile（A10）────────────────────────────────────────────────

def _make_sqlite_fixtures(tmp):
    paths = {}
    specs = {
        "opencode_current": (
            "CREATE TABLE session_v2 (id TEXT PRIMARY KEY, time_created INTEGER, "
            "tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER, "
            "tokens_cache_read INTEGER, tokens_cache_write INTEGER);"
            "CREATE TABLE session_message (id TEXT, session_id TEXT, type TEXT, seq INTEGER, "
            "time_created INTEGER, data TEXT);"
            "INSERT INTO session_v2 VALUES ('ses_1',1771171200000,120,30,5,40,7);"
            "INSERT INTO session_message VALUES ('m','ses_1','assistant',1,1771171200000,"
            "'{\"modelID\":\"claude-sonnet-4-5\"}');",
            "2026-02-16",
        ),
        "opencode_legacy": (
            "CREATE TABLE session (id TEXT, directory TEXT, time_created INTEGER, "
            "prompt_tokens INTEGER, completion_tokens INTEGER);"
            "CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, data TEXT);"
            "INSERT INTO session VALUES ('ses_old','/tmp',1771171200000,900,80);"
            "INSERT INTO message VALUES ('m','ses_old',1771171200000,'{\"modelID\":\"gpt-5\"}');",
            "2026-02-16",
        ),
        "devin": (
            "CREATE TABLE sessions (id TEXT, model TEXT, hidden INTEGER);"
            "CREATE TABLE message_nodes (node_id TEXT, session_id TEXT, chat_message TEXT);"
            "INSERT INTO sessions VALUES ('dev_1','swe-2-high',0);"
            "INSERT INTO sessions VALUES ('dev_hidden','swe-2-high',1);"
            "INSERT INTO message_nodes VALUES ('n1','dev_1',"
            "'{\"role\":\"assistant\",\"metadata\":{\"request_id\":\"req_A\","
            "\"generation_model\":\"swe-2-high\",\"created_at\":1771171200,"
            "\"metrics\":{\"input_tokens\":100,\"output_tokens\":20,\"cache_read_tokens\":30}}}');"
            "INSERT INTO message_nodes VALUES ('n2','dev_1',"
            "'{\"role\":\"assistant\",\"metadata\":{\"request_id\":\"req_A\","
            "\"generation_model\":\"swe-2-high\",\"created_at\":1771171200,"
            "\"metrics\":{\"input_tokens\":100,\"output_tokens\":20}}}');"
            "INSERT INTO message_nodes VALUES ('n9','dev_hidden',"
            "'{\"role\":\"assistant\",\"metadata\":{\"request_id\":\"req_H\","
            "\"created_at\":1771171200,\"metrics\":{\"input_tokens\":999}}}');",
            "2026-02-16",
        ),
        "cursor": (
            "CREATE TABLE cursorDiskKV (key TEXT, value TEXT);"
            "INSERT INTO cursorDiskKV VALUES ('bubbleId:abc::msg1',"
            "'{\"createdAt\":\"2026-02-16T00:00:00Z\",\"tokenCount\":{\"inputTokens\":64,"
            "\"outputTokens\":17}}');",
            "2026-02-16",
        ),
        "openclaw_gateway": (
            "CREATE TABLE transcript_events (session_id TEXT, seq INTEGER, event_json TEXT);"
            "INSERT INTO transcript_events VALUES ('sess-gw',1,"
            "'{\"role\":\"assistant\",\"model\":\"gw-model\",\"timestamp\":\"2026-02-16T01:00:00+08:00\","
            "\"usage\":{\"input_tokens\":12,\"output_tokens\":4,\"cache_read_input_tokens\":2}}');"
            "INSERT INTO transcript_events VALUES ('sess-gw',2,"
            "'{\"role\":\"assistant\",\"model\":\"gw-model\",\"timestamp\":\"2026-02-16T02:00:00+08:00\"}');",
            "2026-02-16",
        ),
    }
    for name, (script, _expected_day) in specs.items():
        p = tmp / f"{name}.db"
        con = sqlite3.connect(str(p))
        con.executescript(script)
        con.executescript(
            "CREATE TABLE credential (provider TEXT, access_token TEXT);"
            "INSERT INTO credential VALUES ('evil','DO_NOT_READ');"
            "CREATE TABLE account (id TEXT, secret TEXT);"
        )
        con.commit()
        con.close()
        paths[name] = p
    return paths


class _SpyConn:
    """记录所有 SQL 的只读连接代理，用于证明敏感表零访问。"""

    def __init__(self, conn):
        self._conn, self.sql = conn, []

    def execute(self, sql, *args):
        self.sql.append(sql)
        return self._conn.execute(sql, *args)


@allure.feature("通用适配器")
@allure.title("A10 SQLite 五 profile 只读白名单，credential/account 零访问")
def test_sqlite_profiles_and_whitelist(tmp_path):
    dbs = _make_sqlite_fixtures(tmp_path)
    for name, path in dbs.items():
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        spy = _SpyConn(con)
        profile = G.detect_sqlite_profile(spy)
        assert profile == name
        traces = G._PROFILE_READERS[profile](spy, "2026-02-16", "2026-02-17")
        assert traces, f"{name} 应抽出至少一条 trace"
        assert all(t["tokens_source"] in {
            "reported", "reported_partial", "estimated_heuristic", "missing"
        } for t in traces)
        assert all("credential" not in sql.lower() and "account" not in sql.lower()
                   and "DO_NOT_READ" not in sql for sql in spy.sql)
        assert not any("DO_NOT_READ" in json.dumps(t, ensure_ascii=False) for t in traces)
        con.close()
    # requestId 去重（devin 两条同 req_A 只留一条）
    con = sqlite3.connect(dbs["devin"].resolve().as_uri() + "?mode=ro", uri=True)
    devin = G._read_devin(con, "2026-02-16", "2026-02-17")
    con.close()
    assert len(devin) == 1 and devin[0]["_request_id"] == "req_A"
    # Gateway 的 event_json 分层：一条 L2、一条 missing
    con = sqlite3.connect(dbs["openclaw_gateway"].resolve().as_uri() + "?mode=ro", uri=True)
    gw = G._read_openclaw_gateway(con, "2026-02-16", "2026-02-17")
    con.close()
    assert {t["tokens_source"] for t in gw} == {"reported", "missing"}


@allure.feature("通用适配器")
@allure.title("A10 锁冲突显式降级，不静默当无数据")
def test_sqlite_lock_degrades_verbosely(tmp_path, capsys):
    path = tmp_path / "locked.db"
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE session_v2 (id TEXT, time_created INTEGER, tokens_input INTEGER, "
                "tokens_output INTEGER, tokens_reasoning INTEGER, tokens_cache_read INTEGER, "
                "tokens_cache_write INTEGER)")
    con.execute("CREATE TABLE session_message (id TEXT, session_id TEXT, data TEXT, time_created INTEGER)")
    con.commit()
    con.execute("BEGIN EXCLUSIVE")
    try:
        G._collect_sqlite([path], "2026-02-16", "2026-02-17")
        err = capsys.readouterr().err
        assert "WARN" in err and "锁冲突" in err
    finally:
        con.rollback()
        con.close()


# ── 端到端验收 A1–A7 ───────────────────────────────────────────────────────

@pytest.fixture
def generic_cli_data(tmp_path, gen_home):
    _write_jsonl(gen_home, [
        _assistant(f"{DAY}T01:00:00+08:00", "s2", {"input_tokens": 120, "output_tokens": 30,
                                                  "cache_read_input_tokens": 20},
                   "hello", "glm-5.2", "r1"),
        _assistant(f"{DAY}T02:00:00+08:00", "s2", {}, "你好世界这是一段中文对话", "glm-5.2", "r2"),
        _assistant("2026-01-01T01:00:00+08:00", "old", {"input_tokens": 999}, "", "x", "r3"),
    ])
    # 真·坏行必须原样写入，不能经 json.dumps（否则只是合法 JSON 字符串）
    with (gen_home / "projects" / "session.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{broken json\n")
    proc, data = _cli(tmp_path, home=gen_home)
    assert proc.returncode == 0, proc.stderr
    return proc, data, gen_home


@allure.feature("通用适配器")
@allure.title("A6/A7 强制 tokens-only，即便模型命中价表或显式 --cost-mode priced")
def test_meta_flags_and_forced_tokens_only(tmp_path, generic_cli_data):
    _proc, data, _home = generic_cli_data
    meta = data["meta"]
    assert meta["source"] == "generic"
    assert meta["cost_supported"] is False
    assert meta["cost_mode"] == "tokens_only"
    assert meta["tokens_source"] == "estimated"
    assert meta["skipped_bad_lines"] == 1  # 坏行跳过计数（S1.3）
    assert all(t["session_id"] != "old" for t in data["traces"])  # 日期窗口过滤
    assert all("_dialogue_text" in s for s in data["sessions"])  # 会话派生字段（S1.4）
    assert all(t["total_cost"] == 0 and t["input_cost"] == 0 for t in data["traces"])
    proc2, data2 = _cli(tmp_path, extra=["--cost-mode", "priced"],
                        home=generic_cli_data[2], out_name="forced.json")
    assert proc2.returncode == 0, proc2.stderr
    assert data2["meta"]["cost_mode"] == "tokens_only"


@allure.feature("通用适配器")
@allure.title("A1–A5 报告零金额 / 无补价块 / 用量速览 / 三列表头")
def test_report_zero_cost(tmp_path, generic_cli_data):
    _proc, data, _home = generic_cli_data
    proc, md = _report(tmp_path, data, "markdown")
    assert proc.returncode == 0, proc.stderr
    assert "¥" not in md
    assert "### 3.3" not in md
    assert "pricing.local.json" not in md
    assert "💰 花费速览" not in md
    assert "📈 用量速览" in md
    assert "| 模型 | 调用次数 | 实际消耗Token |" in md  # A5 三列，无单价列
    assert "实际消耗 Token（计费等效）" not in md


@allure.feature("通用适配器")
@allure.title("A11/A13 分层摘要可追溯，中文 L4 偏差提示落在报告")
def test_layer_summary_in_report(tmp_path, generic_cli_data):
    _proc, data, _home = generic_cli_data
    sources = {t["tokens_source"] for t in data["traces"]}
    assert "reported" in sources and "estimated_heuristic" in sources
    summary = data["meta"]["tokens_source_layer_summary"]
    assert set(summary) >= {"L2", "L4"}
    proc, md = _report(tmp_path, data, "markdown")
    assert proc.returncode == 0, proc.stderr
    assert "token 分层" in md and "68.3%" in md
