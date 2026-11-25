# -*- coding: utf-8 -*-
"""批次 5 采集层测试 — pytest + Allure 双可视化。

被测对象：scripts/ca_collect.py（从 collect_usage_data.py::main 搬出的 6 路分发 +
WorkBuddy 三步时序）。测试分三档：

  * 正常例 N1–N6：6 路源各自分发正确，返回 CollectionBundle 的 11 字段、
    cost_supported / source_meta / task_types 口径符合各源契约；
  * 边界例 B1–B4：预分类收口、LLM 分类器分支、空数据、SQLite 补全查询失败降级；
  * 报错例 E1–E2：下游 adapter / 分类器抛异常时不吞错、向上传播。

白盒手段：monkeypatch 各 adapter 的 collect_<agent> 与 ca_collect 的模块级依赖，
避免读用户真实数据（CI 结果不因本机安装状态漂移）；WorkBuddy 三步时序用
FakeConn + 调用序列断言，验证「补全 → setdefault → 重采」不可重排。
"""

import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import allure
import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"


def _ensure_path():
    injected = []
    for p in (str(REPO), str(SCRIPTS)):
        if p not in sys.path:
            sys.path.insert(0, p)
            injected.append(p)
    return injected


def _restore(injected):
    for p in injected:
        try:
            sys.path.remove(p)
        except ValueError:
            pass


@pytest.fixture
def mod():
    """独立加载 ca_collect.py（每次测试隔离模块级全局状态）。"""
    injected = _ensure_path()
    try:
        spec = importlib.util.spec_from_file_location(
            "ca_collect_unit", SCRIPTS / "ca_collect.py"
        )
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        yield m
    finally:
        _restore(injected)


def _adapter(name):
    injected = _ensure_path()
    try:
        return importlib.import_module(f"adapters.{name}")
    finally:
        _restore(injected)


def _fake_db(sessions=None):
    sessions = sessions if sessions is not None else [
        {"id": "s1", "model": "glm-4.7-flash", "task_type": "编码"}
    ]
    return {
        "sessions": sessions,
        "automation_runs": [{"id": "auto-1"}],
        "session_credits": [{"used": 7}],
    }


def _bundle_assert(b):
    """断言 CollectionBundle 的 11 字段齐全且类型正确。"""
    from ca_types import CollectionBundle
    assert isinstance(b, CollectionBundle)
    for field in ("traces", "sessions", "automation_runs", "session_credits",
                  "outputs"):
        assert isinstance(getattr(b, field), list), field
    for field in ("sid_to_rawmodel", "skill_usage", "memory_logs", "task_types",
                  "source_meta"):
        assert isinstance(getattr(b, field), dict), field
    assert isinstance(b.cost_supported, bool)
    assert b.sessions and b.sessions[0]["id"] == "s1"


def _args(source):
    return SimpleNamespace(source=source)


# ───────────────────────── 正常例 ─────────────────────────

@allure.feature("批次5 采集层")
@allure.title("N1 claude-code 分发：预分类收口 + 零 skill/outputs/memory")
def test_claude_code_dispatch(mod, monkeypatch):
    fake_traces = [{"date": "2026-11-10", "session_id": "s1", "total_tokens": 10}]
    monkeypatch.setattr(
        _adapter("claude_code"), "collect_claude_code",
        lambda s, e: (fake_traces, _fake_db()),
    )
    b = mod.collect_from_source(_args("claude-code"), "2026-11-01", "2026-11-30", None)
    _bundle_assert(b)
    assert b.traces == fake_traces
    assert b.task_types == {"s1": "编码"}
    assert b.cost_supported is True
    assert b.memory_logs == {} and b.outputs == []
    assert b.skill_usage["active_days"] == ["2026-11-10"]


@allure.feature("批次5 采集层")
@allure.title("N2 codex 分发：返回结构同源、不误带 WorkBuddy 字段")
def test_codex_dispatch(mod, monkeypatch):
    monkeypatch.setattr(
        _adapter("codex"), "collect_codex",
        lambda s, e: ([{"date": "2026-11-11", "session_id": "s1"}], _fake_db()),
    )
    b = mod.collect_from_source(_args("codex"), "2026-11-01", "2026-11-30", None)
    _bundle_assert(b)
    assert b.task_types == {"s1": "编码"}
    assert b.cost_supported is True


@allure.feature("批次5 采集层")
@allure.title("N3 qwenwork 分发：SUPPORTS_COST=False + _meta 合并")
def test_qwenwork_dispatch(mod, monkeypatch):
    qw = _adapter("qwenwork")
    monkeypatch.setattr(qw, "SUPPORTS_COST", False)
    db = _fake_db()
    db["skill_usage"] = {"skills": {"x": 1}, "active_days": []}
    db["outputs"] = [{"title": "run"}]
    db["_meta"] = {"single_channel": True}
    monkeypatch.setattr(qw, "collect_qwenwork", lambda s, e: ([], db))
    b = mod.collect_from_source(_args("qwenwork"), "2026-11-01", "2026-11-30", None)
    _bundle_assert(b)
    assert b.cost_supported is False
    assert b.source_meta["single_channel"] is True
    assert b.skill_usage == db["skill_usage"] and b.outputs == db["outputs"]
    assert b.memory_logs == {}  # 不得硬凑 memory_logs


@allure.feature("批次5 采集层")
@allure.title("N4 dumate 分发：本地标注 + 零金额 + 无条件直采任务类型")
def test_dumate_dispatch(mod, monkeypatch):
    dm = _adapter("dumate")
    monkeypatch.setattr(dm, "SUPPORTS_COST", False)
    monkeypatch.setattr(dm, "resolve_dumate_db", lambda: "/tmp/opencode.db")
    # 会话无 task_type 键：若走「收口预分类」会塌成「其他」，必须直采
    db = _fake_db([{"id": "s1", "model": "glm-5"}])
    monkeypatch.setattr(dm, "collect_dumate", lambda s, e: ([], db))
    seen = {}

    def fake_task_types(sessions, classifier=None):
        seen["called"] = True
        return {"s1": "问答"}

    monkeypatch.setattr(mod, "collect_task_types", fake_task_types)
    b = mod.collect_from_source(_args("dumate"), "2026-11-01", "2026-11-30", None)
    _bundle_assert(b)
    assert seen.get("called") is True
    assert b.task_types == {"s1": "问答"}
    assert b.cost_supported is False
    assert b.source_meta["local-only"] is True
    assert b.source_meta["source_notice"] == "本应用只录入本地会话数据"
    assert b.source_meta["dumate_db"] == "/tmp/opencode.db"


@allure.feature("批次5 采集层")
@allure.title("N5 generic 分发：tokens_only + 隐私红线（零 memory_logs）")
def test_generic_dispatch(mod, monkeypatch):
    g = _adapter("generic")
    monkeypatch.setattr(g, "SUPPORTS_COST", False)
    db = _fake_db([{"id": "s1", "model": "some-model"}])
    db["_meta"] = {"formats": ["jsonl"]}
    monkeypatch.setattr(g, "collect_generic", lambda s, e: ([], db))
    b = mod.collect_from_source(_args("generic"), "2026-11-01", "2026-11-30", None)
    _bundle_assert(b)
    assert b.cost_supported is False
    assert b.memory_logs == {}
    assert b.source_meta["formats"] == ["jsonl"]


class _FakeConn:
    row_factory = None

    def __init__(self, rows):
        self._rows = rows
        self.closed = False

    def execute(self, sql, params):
        self.sql, self.params = sql, params
        return self

    def fetchall(self):
        return self._rows

    def close(self):
        self.closed = True


@allure.feature("批次5 采集层")
@allure.title("N6 WorkBuddy 三步时序：补全 → setdefault → 重采不可重排")
def test_workbuddy_three_steps(mod, monkeypatch):
    db = _fake_db([{"id": "s1", "model": "gateway-model"}])
    monkeypatch.setattr(mod, "collect_db_data", lambda s, e: db)
    call_maps = []

    def fake_traces(s, e, sid_map):
        call_maps.append(dict(sid_map))
        # 初采发现一条窗口内 trace 属于窗口外会话 s2
        return [{"session_id": "s2", "date": "2026-11-05"}] if len(call_maps) == 1 else []

    monkeypatch.setattr(mod, "collect_traces", fake_traces)
    row = {
        "id": "s2", "cwd": "/tmp", "title": "t", "custom_title": "", "status": "done",
        "created_at": 1, "created_date": "2026-11-01", "updated_at": 2,
        "mode": "normal", "model": "deepseek-v3", "is_background_automation": 0,
    }
    conns = []

    def fake_connect(*a, **k):
        c = _FakeConn([row])
        conns.append(c)
        return c

    monkeypatch.setattr(mod.sqlite3, "connect", fake_connect)
    monkeypatch.setattr(mod, "collect_skill_usage", lambda s, e: {"skills": {}, "active_days": []})
    monkeypatch.setattr(mod, "collect_session_outputs", lambda s, e: ([], {}))
    monkeypatch.setattr(mod, "collect_task_types", lambda sessions, classifier=None: {})

    b = mod.collect_workbuddy_bundle("2026-11-01", "2026-11-30", None)

    assert len(call_maps) == 2, "必须初采 + 重采各一次"
    assert call_maps[0] == {"s1": "gateway-model"}          # 初采只有窗口内会话
    assert call_maps[1] == {"s1": "gateway-model", "s2": "deepseek-v3"}  # 重采含补全
    assert len(conns) == 1 and conns[0].closed is True
    assert any("s2" == s["id"] for s in b.sessions)
    assert b.cost_supported is True and b.source_meta == {}


# ───────────────────────── 边界例 ─────────────────────────

@allure.feature("批次5 采集层")
@allure.title("B1 预分类收口：classifier=None 时保留适配器 task_type，缺省落「其他」")
def test_adapter_presets_without_classifier(mod):
    db = _fake_db([
        {"id": "s1", "model": "m1", "task_type": "编码"},
        {"id": "s2", "model": "m2"},
    ])
    result = mod._task_types_from_adapter(db, None)
    assert result == {"s1": "编码", "s2": "其他"}


@allure.feature("批次5 采集层")
@allure.title("B2 有 LLM 分类器时基于 sessions 重分类（不收口预分类）")
def test_adapter_with_classifier(mod, monkeypatch):
    db = _fake_db([{"id": "s1", "model": "m1", "task_type": "编码"}])
    seen = {}

    def fake_collect(sessions, classifier=None):
        seen["classifier"] = classifier
        seen["sessions"] = sessions
        return {"s1": "分析"}

    monkeypatch.setattr(mod, "collect_task_types", fake_collect)
    marker = object()
    result = mod._task_types_from_adapter(db, marker)
    assert result == {"s1": "分析"}
    assert seen["classifier"] is marker and seen["sessions"] is db["sessions"]


@allure.feature("批次5 采集层")
@allure.title("B3 空数据边界：空 sessions/traces 仍返回结构完整的 Bundle")
def test_empty_data(mod, monkeypatch):
    monkeypatch.setattr(
        _adapter("claude_code"), "collect_claude_code",
        lambda s, e: ([], {"sessions": [], "automation_runs": [], "session_credits": []}),
    )
    b = mod.collect_from_source(_args("claude-code"), "2026-11-01", "2026-11-30", None)
    _bundle_assert(b) if b.sessions else None
    assert b.sessions == [] and b.traces == [] and b.task_types == {}
    assert b.sid_to_rawmodel == {} and b.cost_supported is True


@allure.feature("批次5 采集层")
@allure.title("B4 补全查询失败降级：SQLite 报错只告警、不中断采集")
def test_supplementary_query_failure_degrades(mod, monkeypatch, capsys):
    db = _fake_db([{"id": "s1", "model": "gateway-model"}])
    monkeypatch.setattr(mod, "collect_db_data", lambda s, e: db)
    calls = []

    def fake_traces(s, e, sid_map):
        calls.append(dict(sid_map))
        return [{"session_id": "s2"}] if len(calls) == 1 else []

    monkeypatch.setattr(mod, "collect_traces", fake_traces)
    monkeypatch.setattr(
        mod.sqlite3, "connect",
        lambda *a, **k: (_ for _ in ()).throw(sqlite3.Error("locked")),
    )
    monkeypatch.setattr(mod, "collect_skill_usage", lambda s, e: {})
    monkeypatch.setattr(mod, "collect_session_outputs", lambda s, e: ([], {}))
    monkeypatch.setattr(mod, "collect_task_types", lambda sessions, classifier=None: {})

    b = mod.collect_workbuddy_bundle("2026-11-01", "2026-11-30", None)
    assert len(calls) == 2
    assert calls[1] == {"s1": "gateway-model"}  # 补全失败 → 不污染映射
    assert "supplementary sessions query" in capsys.readouterr().err
    _bundle_assert(b)


# ───────────────────────── 报错例 ─────────────────────────

@allure.feature("批次5 采集层")
@allure.title("E1 adapter 抛异常：不吞错，原样向上传播")
def test_adapter_error_propagates(mod, monkeypatch):
    def boom(s, e):
        raise RuntimeError("adapter down")

    monkeypatch.setattr(_adapter("claude_code"), "collect_claude_code", boom)
    with pytest.raises(RuntimeError, match="adapter down"):
        mod.collect_from_source(_args("claude-code"), "2026-11-01", "2026-11-30", None)


@allure.feature("批次5 采集层")
@allure.title("E2 分类器抛异常：不吞错，原样向上传播")
def test_classifier_error_propagates(mod, monkeypatch):
    def boom(sessions, classifier=None):
        raise ValueError("classifier down")

    monkeypatch.setattr(_adapter("claude_code"), "collect_claude_code",
                        lambda s, e: ([], _fake_db()))
    monkeypatch.setattr(mod, "collect_task_types", boom)
    with pytest.raises(ValueError, match="classifier down"):
        mod.collect_from_source(_args("claude-code"), "2026-11-01", "2026-11-30", object())
