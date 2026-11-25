# -*- coding: utf-8 -*-
"""L1/L3 · 百度搭子（DuMate）专用 reader 测试（2026-11-25 重写）

实证基线：
  百度搭子真实数据在 %APPDATA%/qianfan-desktop-app/qianfan_desk_xdg/<hash>/
  data/opencode/opencode.db，OpenCode 旧代 schema：
    session(id,title,directory,time_created,time_updated,model_settings,
            source,channel,type,time_deleted)
    message(id,session_id,time_created,data,time_deleted)
    data JSON: {role:'assistant', tokens:{total,input,output,reasoning,
                cache:{read,write}}, time:{created,completed}, modelID, requestID}

覆盖：
  - 路径发现：DUMATE_DB_PATH > DUMATE_HOME > 固定 APPDATA 目录自动发现
  - 专用 reader：L2 token、cache.read、requestID 复用不去重、窗口外会话补全
  - 能力声明：SUPPORTS_COST=False + CLI 强制 tokens-only + 全篇零金额
  - 报告标注：标题与数据来源含「本应用只录入本地会话数据」
  - 隐私：只读 mode=ro、敏感表零访问、不读 ~/.workbuddy、符号链接跳过
  - R7 回归：dumate 任务类型真走 collect_task_types，不塌成「其他」

所有 fixture 通过环境变量指向 tmp_path，绝不读取用户真实目录。
"""

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
import allure

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
for _p in (str(SKILL_DIR), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import adapters.dumate as dm  # noqa: E402

pytestmark = [pytest.mark.unit, pytest.mark.integration]

PY = sys.executable
TZ = timezone(timedelta(hours=8))
START, END = "2026-11-01", "2026-11-30"


def _ms(y, m, d, h=10):
    return int(datetime(y, m, d, h, tzinfo=TZ).timestamp() * 1000)


def _make_db(path, *, in_window=2, out_window=1, shared_request_id=False):
    """构造最小真实 schema fixture；返回 (db_path, 会话 id 列表)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.executescript(
        "CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT, directory TEXT, "
        "time_created INT, time_updated INT, model_settings TEXT, source TEXT, "
        "channel TEXT, type TEXT, time_deleted INT);"
        "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INT, "
        "time_updated INT, data TEXT, time_deleted INT);"
        "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, "
        "time_created INT, time_updated INT, data TEXT, time_deleted INT);"
        "CREATE TABLE control_account (email TEXT, access_token TEXT, refresh_token TEXT);"
    )
    # 敏感表放可识别数据：测试将断言 reader 从不 SELECT 它。
    con.execute(
        "INSERT INTO control_account VALUES ('private@example.com','SECRET_ACCESS','SECRET_REFRESH')"
    )
    sids = []
    for i in range(in_window):
        sid = f"ses-win-{i}"
        sids.append(sid)
        con.execute(
            "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, "修复 pytest 超时导致的重试逻辑 bug", "/workspace/proj",
             _ms(2026, 11, 5 + i), _ms(2026, 11, 5 + i),
             '{"autoModelLevel":"L0"}', "manual", "", "work", None),
        )
    for i in range(out_window):
        sid = f"ses-out-{i}"
        sids.append(sid)
        con.execute(
            "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, "窗口外创建的会话", "/workspace/old",
             _ms(2026, 10, 20), _ms(2026, 11, 10),
             '{"autoModelLevel":"L0"}', "manual", "", "work", None),
        )
    call = 0
    for sid in sids:
        created = _ms(2026, 11, 10) if sid.startswith("ses-out") else _ms(2026, 11, 5)
        for k in range(2):
            call += 1
            created_ms = created + k * 1000
            msg = {
                "role": "assistant",
                "modelID": "glm-5",
                "time": {"created": created_ms, "completed": created_ms + 1500},
                "tokens": {
                    "total": 1000, "input": 700, "output": 200, "reasoning": 50,
                    "cache": {"read": 100, "write": 0},
                },
                "finish": "stop",
                "requestID": "same-request-id" if shared_request_id else f"req-{call}",
            }
            con.execute(
                "INSERT INTO message VALUES (?,?,?,?,?,?)",
                (f"msg-{call}", sid, created_ms, created_ms + 1500,
                 json.dumps(msg, ensure_ascii=False), None),
            )
        # 一条 user 消息带假 token，必须被 role 过滤掉
        con.execute(
            "INSERT INTO message VALUES (?,?,?,?,?,?)",
            (f"msg-user-{sid}", sid, created, created,
             json.dumps({"role": "user", "tokens": {"total": 99999},
                         "time": {"created": created}}, ensure_ascii=False), None),
        )
    con.commit()
    con.close()
    return path, sids


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path, _ = _make_db(tmp_path / "fixture" / "data" / "opencode" / "opencode.db")
    monkeypatch.setenv("DUMATE_DB_PATH", str(path))
    monkeypatch.delenv("DUMATE_HOME", raising=False)
    return path


# ── 一、路径解析与能力声明 ──────────────────────────────────────────────────

@allure.feature("百度搭子适配器")
@allure.story("路径解析")
@allure.title("DUMATE_DB_PATH 优先于 DUMATE_HOME，显式指定不存在时不回退真实目录")
def test_resolve_db_priority(monkeypatch, tmp_path):
    explicit = tmp_path / "explicit.db"
    explicit.touch()
    legacy = tmp_path / "legacy.db"
    legacy.touch()
    monkeypatch.setenv("DUMATE_DB_PATH", str(explicit))
    monkeypatch.setenv("DUMATE_HOME", str(legacy))
    assert dm.resolve_dumate_db() == explicit

    missing = tmp_path / "missing" / "opencode.db"
    monkeypatch.setenv("DUMATE_DB_PATH", str(missing))
    assert dm.resolve_dumate_db() == missing  # 不悄悄回退 %APPDATA%


@allure.feature("百度搭子适配器")
@allure.story("路径解析")
@allure.title("无环境变量时按 APPDATA/qianfan-desktop-app 自动发现，多账号取最新")
def test_resolve_db_autodiscover(monkeypatch, tmp_path):
    root = tmp_path / "AppData" / "Roaming" / "qianfan-desktop-app"
    newest = None
    for name in ("aaa", "bbb"):
        p = root / "qianfan_desk_xdg" / name / "data" / "opencode" / "opencode.db"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"x")
        newest = p
    old = root / "qianfan_desk_xdg" / "aaa" / "data" / "opencode" / "opencode.db"
    os.utime(old, (1_700_000_000, 1_700_000_000))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    monkeypatch.delenv("DUMATE_DB_PATH", raising=False)
    monkeypatch.delenv("DUMATE_HOME", raising=False)
    assert dm.resolve_dumate_db() == newest


@allure.feature("百度搭子适配器")
@allure.story("能力声明")
@allure.title("SUPPORTS_COST = False（只显示模型数据，不出金额）")
def test_supports_cost_false():
    assert dm.SUPPORTS_COST is False


# ── 二、专用 reader ─────────────────────────────────────────────────────────

@allure.feature("百度搭子适配器")
@allure.story("专用 reader")
@allure.title("L2 token / cache.read 抽取，user 消息过滤，窗口外会话补全")
def test_reader_l2_tokens_and_out_of_window(db_path):
    traces, db_data = dm.collect_dumate(START, END)
    assert len(traces) == 6, f"期望 6 条 assistant 调用，实际 {len(traces)}"
    assert all(t["tokens_source_layer"] == "L2" for t in traces)
    assert all(t["tokens_source"] == "reported" for t in traces)
    assert all(t["total_cost"] == 0.0 for t in traces)
    assert all(t["channel"] == "dumate" for t in traces)
    assert all(t["_tokens_estimated"] is False for t in traces)
    t = traces[0]
    assert t["total_tokens"] == 1000
    assert t["input_tokens"] == 700
    assert t["output_tokens"] == 200
    assert t["cached_tokens"] == 100
    assert t["reasoning_tokens"] == 50
    assert t["duration_ms"] == 1500
    assert t["model_name"] == "glm-5"
    # 窗口外创建但有窗口内 trace 的会话必须纳入
    assert any(s["id"] == "ses-out-0" for s in db_data["sessions"])
    assert all(s["model"] == "glm-5" for s in db_data["sessions"])


@allure.feature("百度搭子适配器")
@allure.story("专用 reader")
@allure.title("同一 requestID 复用于多条消息时不得误删（按 message.id 去重）")
def test_shared_request_id_not_deduplicated(tmp_path, monkeypatch):
    path, _ = _make_db(tmp_path / "shared.db", shared_request_id=True)
    monkeypatch.setenv("DUMATE_DB_PATH", str(path))
    traces, _ = dm.collect_dumate(START, END)
    assert len(traces) == 6, "同 requestID 的多条 usage 都是真实调用，不能按 requestID 去重"


@allure.feature("百度搭子适配器")
@allure.story("专用 reader")
@allure.title("不存在 / schema 不符的数据库 → 空结果，不崩溃")
def test_missing_and_bad_schema(monkeypatch, tmp_path):
    monkeypatch.setenv("DUMATE_DB_PATH", str(tmp_path / "no-such.db"))
    traces, data = dm.collect_dumate(START, END)
    assert traces == [] and data["sessions"] == []

    bad = tmp_path / "bad.db"
    con = sqlite3.connect(str(bad))
    con.execute("CREATE TABLE unrelated (x INT)")
    con.commit()
    con.close()
    monkeypatch.setenv("DUMATE_DB_PATH", str(bad))
    traces, data = dm.collect_dumate(START, END)
    assert traces == [] and data["sessions"] == []


@allure.feature("百度搭子适配器")
@allure.story("隐私")
@allure.title("只读 mode=ro + 敏感表零访问")
def test_readonly_and_no_sensitive_access(db_path, monkeypatch):
    original_connect = sqlite3.connect
    seen_sql, seen_uri = [], []

    class SpyConnection:
        def __init__(self, inner):
            self._inner = inner

        @property
        def row_factory(self):
            return self._inner.row_factory

        @row_factory.setter
        def row_factory(self, value):
            self._inner.row_factory = value

        def execute(self, sql, *args):
            seen_sql.append(str(sql).lower())
            return self._inner.execute(sql, *args)

        def close(self):
            self._inner.close()

    def spy_connect(*args, **kwargs):
        seen_uri.append(str(args[0] if args else kwargs.get("database", "")))
        return SpyConnection(original_connect(*args, **kwargs))

    monkeypatch.setattr(dm.sqlite3, "connect", spy_connect)
    traces, _ = dm.collect_dumate(START, END)
    assert traces, "fixture 应有数据"
    assert all("mode=ro" in u for u in seen_uri), seen_uri
    assert not any("control_account" in s for s in seen_sql), seen_sql
    assert not any("from part" in s or "join part" in s for s in seen_sql), seen_sql
    assert not any("memory_chunk" in s or "session_share" in s for s in seen_sql), seen_sql


@allure.feature("百度搭子适配器")
@allure.story("隐私")
@allure.title("符号链接路径被跳过")
def test_symlink_skipped(monkeypatch, tmp_path, db_path):
    link_root = tmp_path / "linked"
    link_root.mkdir()
    link = link_root / "opencode.db"
    try:
        link.symlink_to(db_path)
    except (OSError, NotImplementedError):
        pytest.skip("当前平台不支持创建符号链接")
    monkeypatch.setenv("DUMATE_DB_PATH", str(link))
    traces, _ = dm.collect_dumate(START, END)
    assert traces == [], "符号链接指向的数据源必须跳过"


# ── 三、CLI 与报告 ──────────────────────────────────────────────────────────

def _run_cli(out_json, env_extra=None):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(env_extra or {})
    return subprocess.run(
        [PY, str(SCRIPTS_DIR / "collect_usage_data.py"), "--source", "dumate",
         "--start", START, "--end", END, "-o", str(out_json)],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(SKILL_DIR),
    )


@allure.feature("百度搭子适配器")
@allure.story("端到端")
@allure.title("--source dumate 输出 cost_supported=False / tokens_only / 本地会话标注")
def test_cli_tokens_only(db_path, tmp_path):
    out = tmp_path / "dumate.json"
    r = _run_cli(out)
    assert r.returncode == 0, r.stderr[-800:]
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["meta"]["source"] == "dumate"
    assert d["meta"]["cost_supported"] is False
    assert d["meta"]["cost_mode"] == "tokens_only"
    assert d["meta"]["source_notice"] == "本应用只录入本地会话数据"
    assert str(db_path) == d["meta"]["dumate_db"]
    assert len(d["traces"]) == 6
    assert sum(t["total_cost"] for t in d["traces"]) == 0.0
    assert d["summary"]["total_tokens"] == 6000


@allure.feature("百度搭子适配器")
@allure.story("R7 回归")
@allure.title("dumate 任务类型走启发式分类，不塌成「其他」")
def test_dumate_task_type_not_all_other(db_path, tmp_path):
    out = tmp_path / "dumate_tasks.json"
    r = _run_cli(out)
    assert r.returncode == 0, r.stderr[-800:]
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["sessions"], "fixture 应至少有 1 个会话"
    # 窗口外会话按 created_at 排在首位、标题不可分类，故逐条找可分类会话。
    classified = [s for s in d["sessions"] if "_task_confidence" in s]
    assert classified, "又退回预分类兜底（R7 复发）"
    assert any(s["task_type"] == "Bug修复" for s in classified), (
        [(s.get("title"), s.get("task_type")) for s in d["sessions"]]
    )
    dist = d["summary"]["task_type_distribution"]
    assert dist != {"其他": len(d["sessions"])}


@allure.feature("百度搭子适配器")
@allure.story("端到端")
@allure.title("报告标题/来源含「本应用只录入本地会话数据」，全篇零金额")
def test_report_local_only_zero_cost(db_path, tmp_path):
    out = tmp_path / "dumate.json"
    r = _run_cli(out)
    assert r.returncode == 0, r.stderr[-800:]
    for fmt, suffix in (("markdown", "md"), ("html", "html")):
        report = tmp_path / f"dumate.{suffix}"
        rp = subprocess.run(
            [PY, str(SCRIPTS_DIR / "generate_report.py"), str(out),
             "--output", str(report), "--format", fmt],
            capture_output=True, text=True, encoding="utf-8",
            env=dict(os.environ), cwd=str(SKILL_DIR),
        )
        assert rp.returncode == 0, rp.stderr[-800:]
        text = report.read_text(encoding="utf-8")
        assert "百度搭子使用情况报告" in text
        assert "本应用只录入本地会话数据" in text
        assert "归属未证实" not in text
        assert "¥" not in text, f"{fmt} 报告仍有金额符号"


# ── 六、缓存占比口径（OpenCode: total = input + output + cache.read）──────────

@allure.feature("百度搭子适配器")
@allure.story("缓存口径")
@allure.title("缓存占比用 cache.read/total 作分母，不会出现 >100%")
def test_cache_rate_denominator_is_total(monkeypatch, tmp_path):
    """真实 opencode 库里 cache.read 远大于 input；用 input 作分母会得到 >100%。"""
    path = tmp_path / "cachefixture" / "data" / "opencode" / "opencode.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.executescript(
        "CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT, directory TEXT, "
        "time_created INT, time_updated INT, model_settings TEXT, source TEXT, "
        "channel TEXT, type TEXT, time_deleted INT);"
        "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INT, "
        "time_updated INT, data TEXT, time_deleted INT);"
    )
    con.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("sid-cache", "缓存占比测试", "/workspace/dm", _ms(2026, 11, 10),
                 _ms(2026, 11, 10), '{"autoModelLevel":"L0"}', "manual", "", "work", None))
    # 与真实分布同量级：total=1717, input=232, output=77, cache.read=1408 → 82.0%
    msg = {"role": "assistant", "modelID": "glm-5",
           "time": {"created": _ms(2026, 11, 10), "completed": _ms(2026, 11, 10) + 1500},
           "tokens": {"total": 1717, "input": 232, "output": 77, "reasoning": 63,
                      "cache": {"read": 1408, "write": 0}},
           "finish": "stop", "requestID": "req-cache"}
    con.execute("INSERT INTO message VALUES (?,?,?,?,?,?)",
                ("msg-cache", "sid-cache", _ms(2026, 11, 10),
                 _ms(2026, 11, 10) + 1500, json.dumps(msg, ensure_ascii=False), None))
    con.commit()
    con.close()

    out = tmp_path / "dumate-cache.json"
    r = _run_cli(out, env_extra={"DUMATE_DB_PATH": str(path)})
    assert r.returncode == 0, r.stderr[-800:]
    d = json.loads(out.read_text(encoding="utf-8"))

    s = d["summary"]
    expected = round(s["total_cached_tokens"] / s["total_tokens"] * 100, 1)
    assert 0 < expected < 100, "fixture 应构造出 cache≫input 的真实场景"
    assert s["cache_rate"] == expected, (
        f"缓存占比应以 total 为分母：期望 {expected}%，实际 {s['cache_rate']}%"
    )
    # 反证：旧口径（cache/input）会 >100%，正是本次要消灭的 bug
    old_rate = s["total_cached_tokens"] / s["total_input_tokens"] * 100
    assert old_rate > 100, "fixture 必须能暴露旧口径的 >100% 缺陷"

    # 报告层（MD + HTML）同步修正：缓存占比行 <=100% 且与 summary 一致
    for fmt, suffix in (("markdown", "md"), ("html", "html")):
        report = tmp_path / f"dumate-cache.{suffix}"
        rp = subprocess.run(
            [PY, str(SCRIPTS_DIR / "generate_report.py"), str(out),
             "--output", str(report), "--format", fmt],
            capture_output=True, text=True, encoding="utf-8",
            env=dict(os.environ), cwd=str(SKILL_DIR),
        )
        assert rp.returncode == 0, rp.stderr[-800:]
        text = report.read_text(encoding="utf-8")
        import re as _re
        rates = [float(x) for x in _re.findall(r"(\d+(?:\.\d+)?)%", text)
                 if "缓存" in text]
        # 至少总览与任务表两处缓存占比均 <=100%
        assert all(v <= 100 for v in rates), f"{fmt} 报告存在 >100% 的比例：{rates}"


@allure.feature("百度搭子适配器")
@allure.story("缓存口径")
@allure.title("非 dumate 源仍用 cache/input 口径（helper 零漂移）")
def test_cache_rate_helper_other_sources_unchanged():
    sys.path.insert(0, str(SCRIPTS_DIR))
    import generate_report as gr
    # workbuddy 等：分母=input
    assert gr._cache_rate_of(100, 1000, 1200, "workbuddy") == 10.0
    assert gr._cache_rate_of(100, 1000, 1200, "claude-code") == 10.0
    assert gr._cache_rate_of(100, 1000, 1200, "qwenwork") == 10.0
    # dumate：分母=total
    assert gr._cache_rate_of(1408, 232, 1717, "dumate") == pytest.approx(1408 / 1717 * 100)
    # 零分母不炸
    assert gr._cache_rate_of(0, 0, 0, "dumate") == 0
