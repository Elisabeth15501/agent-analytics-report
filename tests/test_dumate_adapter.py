# -*- coding: utf-8 -*-
"""L1/L3 · 百度搭子（DuMate）数据源适配器测试

背景（实测，2026-10-02）：
  百度搭子桌面客户端（qianfan-desktop-app / DuMate）的数据落点与 WorkBuddy
  **共用同一布局**（~/.workbuddy 下的 traces / workbuddy.db / usage-log.json /
  projects / sessions，产出在 ~/WorkBuddy）。因此：
    - 不带任何适配器、以默认 `--source workbuddy` 运行即可采集全部数据；
    - 适配器的职责是「显式声明独立数据源 + 支持 DUMATE_HOME 路径覆盖」——
      报告标题显示「百度搭子」而非借用 WorkBuddy 名义，且支持测试隔离。

覆盖：
  - resolve_dumate_home：DUMATE_HOME 优先，默认 ~/.workbuddy
  - SUPPORTS_COST = True（与 WorkBuddy 同布局，token 为上游真值）
  - _patched_paths 上下文：临时替换 ca_sources 路径常量、用完恢复
  - 空 / 不存在数据目录 → 返回空 traces + 空 sessions，不崩溃
  - CLI `--source dumate` 注册 + 端到端采集（DUMATE_HOME 指向 tmp_path fixture）
  - 报告端：--source dumate 生成的报告标题为「百度搭子使用情况报告」
  - 隐私护栏：DUMATE_HOME 指向 tmp_path 时绝不触碰真实 ~/.workbuddy

⚠️ 本测试通过 DUMATE_HOME 把数据根目录指向 tmp_path，绝不读取真实
   ~/.workbuddy/ 或 ~/WorkBuddy/。
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

import ca_sources as cs  # noqa: E402
from adapters.dumate import (  # noqa: E402
    SUPPORTS_COST,
    _PATH_ATTRS,
    _patched_paths,
    collect_dumate,
    resolve_dumate_home,
)

pytestmark = [pytest.mark.unit, pytest.mark.integration]

PY = sys.executable

TZ = timezone(timedelta(hours=8))
START, END = "2026-08-10", "2026-08-16"
WIN_TS = int(datetime(2026, 8, 12, 10, 0, tzinfo=TZ).timestamp() * 1000)


def _write_session(
    db_path,
    sid="sess-dumate-1",
    model="ernie-4.5-flash",
    created=WIN_TS,
    # 标题刻意用可分类文案：R7 回归要靠它肉眼验证 task_type 落进真实桶。
    # 「百度搭子会话」这类中性词会落到「其他」，无法区分「真走了collect_task_types」
    # 与「走了 s.get('task_type','其他') 兜底恒等」——那正是 R7 的原始 bug 形态。
    title="修复 pytest 超时导致的重试逻辑 bug",
):
    con = sqlite3.connect(str(db_path))
    con.execute(
        "INSERT INTO sessions "
        "(id,cwd,title,custom_title,status,created_at,updated_at,mode,model,"
        "is_background_automation,deleted_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            sid,
            "/tmp/dumate",
            title,
            "",
            "active",
            created,
            created,
            "default",
            model,
            0,
            None,
        ),
    )
    con.commit()
    con.close()


def _write_trace(
    traces_dir, session_id, started_at, models=("ernie-4.5-flash",), inp=1200, out=800
):
    """写一条 trace（与 workbuddy 布局一致的扁平格式）。"""
    trace = {
        "traceId": f"t_{session_id}",
        "sessionId": session_id,
        "startedAt": started_at,
        "endedAt": started_at,
        "duration": 2000,
        "status": "done",
        "totalTokens": inp + out,
        "modelInfo": {
            "models": list(models),
            "totalInputTokens": inp,
            "totalOutputTokens": out,
            "totalCachedTokens": 0,
            "totalTokens": inp + out,
            "callCount": 1,
        },
    }
    pd = traces_dir / "1001"
    pd.mkdir(parents=True, exist_ok=True)
    (pd / "trace_1.json").write_text(
        json.dumps({"trace": trace}, ensure_ascii=False), encoding="utf-8"
    )


def _write_usage_log(home):
    """usage-log.json：一个在窗口内的技能 + activeDays。"""
    (home / "usage-log.json").write_text(
        json.dumps(
            {
                "skills": {
                    "skillhub-publish": {
                        "lastUsedDate": "2026-08-12",
                        "firstSeenDate": "2026-07-01",
                        "recentDates": ["2026-08-12"],
                    },
                    "out-of-window": {
                        "lastUsedDate": "2026-06-01",
                        "firstSeenDate": "2026-05-01",
                        "recentDates": [],
                    },
                },
                "activeDays": ["2026-08-12"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


@pytest.fixture
def dumate_home(tmp_path, monkeypatch):
    """构造一个最小 workbuddy 布局的 fixture，并把 DUMATE_HOME 指向它。"""
    home = tmp_path / "dumate-home"
    (home / "traces").mkdir(parents=True)
    (home / "projects").mkdir(parents=True)
    (home / "sessions").mkdir(parents=True)
    (home / "logs" / "runs").mkdir(parents=True)

    con = sqlite3.connect(str(home / "workbuddy.db"))
    con.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, cwd TEXT, title TEXT, "
        "custom_title TEXT, status TEXT, created_at INT, updated_at INT, "
        "mode TEXT, model TEXT, is_background_automation INT, deleted_at INT);"
        "CREATE TABLE automations (id TEXT PRIMARY KEY, name TEXT, status TEXT, "
        "deleted_at INT);"
        "CREATE TABLE automation_runs (thread_id TEXT, automation_id TEXT, "
        "status TEXT, result_success INT, created_at INT, updated_at INT, "
        "thread_title TEXT, source_cwd TEXT, metadata_json TEXT);"
        "CREATE TABLE session_usage (session_id TEXT, used INT, size INT, "
        "updated_at INT, credit_json TEXT);"
    )
    con.commit()
    con.close()

    _write_session(home / "workbuddy.db")
    _write_trace(home / "traces", "sess-dumate-1", "2026-08-12T10:00:00.000+08:00")
    _write_usage_log(home)

    monkeypatch.setenv("DUMATE_HOME", str(home))
    return home


# ── 一、路径解析 ────────────────────────────────────────────────────────────


@allure.feature("百度搭子适配器")
@allure.story("路径解析")
@allure.title("DUMATE_HOME 优先，默认 ~/.workbuddy")
def test_resolve_home_priority(monkeypatch, tmp_path):
    custom = tmp_path / "custom-home"
    monkeypatch.setenv("DUMATE_HOME", str(custom))
    assert resolve_dumate_home() == custom

    monkeypatch.delenv("DUMATE_HOME", raising=False)
    assert resolve_dumate_home() == Path.home() / ".workbuddy"


@allure.feature("百度搭子适配器")
@allure.story("能力声明")
@allure.title("SUPPORTS_COST = True（同 WorkBuddy，token 为上游真值）")
def test_supports_cost():
    assert SUPPORTS_COST is True


# ── 二、路径上下文管理 ──────────────────────────────────────────────────────


@allure.feature("百度搭子适配器")
@allure.story("路径隔离")
@allure.title("_patched_paths 临时替换路径常量、用完恢复")
def test_patched_paths_restores(tmp_path):
    saved = {name: getattr(cs, name) for name in _PATH_ATTRS}
    alt = tmp_path / "alt"
    with _patched_paths(alt):
        assert cs.TRACES_DIR == alt / "traces"
        assert cs.DB_PATH == alt / "workbuddy.db"
        assert cs.USAGE_LOG_PATH == alt / "usage-log.json"
    for name in _PATH_ATTRS:
        assert getattr(cs, name) == saved[name], f"路径常量 {name} 未恢复"


# ── 三、健壮性 ──────────────────────────────────────────────────────────────


@allure.feature("百度搭子适配器")
@allure.story("健壮性")
@allure.title("空 / 不存在数据目录 → 空结果，不崩溃")
def test_empty_home(monkeypatch, tmp_path):
    empty = tmp_path / "nonexistent-home"
    monkeypatch.setenv("DUMATE_HOME", str(empty))
    traces, db_data = collect_dumate(START, END)
    assert traces == []
    assert db_data["sessions"] == []
    assert db_data["automation_runs"] == []
    assert "skill_usage" in db_data


# ── 四、端到端（CLI）────────────────────────────────────────────────────────


@allure.feature("百度搭子适配器")
@allure.story("端到端")
@allure.title("--source dumate 采集：复用 workbuddy 布局，字段一致")
def test_cli_source_dumate_end_to_end(dumate_home, tmp_path):
    env = dict(os.environ)
    env["DUMATE_HOME"] = str(dumate_home)
    env["PYTHONIOENCODING"] = "utf-8"
    out_json = tmp_path / "dumate.json"
    r = subprocess.run(
        [
            PY,
            str(SCRIPTS_DIR / "collect_usage_data.py"),
            "--source",
            "dumate",
            "--start",
            START,
            "--end",
            END,
            "-o",
            str(out_json),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=str(SKILL_DIR),
    )
    assert r.returncode == 0, f"采集失败: {r.stderr[-800:]}"

    d = json.loads(out_json.read_text(encoding="utf-8"))
    assert d["meta"]["source"] == "dumate"
    assert d["meta"]["cost_supported"] is True
    assert d["meta"]["dumate_home"] == str(dumate_home)
    assert len(d["traces"]) == 1
    assert len(d["sessions"]) == 1
    assert d["traces"][0]["session_id"] == "sess-dumate-1"
    assert d["traces"][0]["input_tokens"] == 1200
    assert d["summary"]["total_tokens"] == 2000
    # 技能：只有窗口内的被计入
    assert set(d["skill_usage"]["skills"]) == {"skillhub-publish"}


@allure.feature("百度搭子适配器")
@allure.story("R7 回归")
@allure.title("dumate 任务类型真走启发式分类，不再 100%塌成「其他」")
def test_dumate_task_type_not_all_other(dumate_home, tmp_path):
    """R7 回归：dumate 分支曾用 `s.get("task_type","其他")` 收口，而适配器根本不做
    预分类、session dict 里也没有 task_type 键 → 16 个会话 100% 归「其他」。

    断言用**机制键** `_task_confidence` 而非 `!= "其他"`：
    只有 collect_task_types 的启发式分支会写这个键（ca_sessions.py:163），
    兜底恒等路径不会写。因此本用例能在 fixture 标题改动后依然守住同一个 bug，
    不会因为「标题恰好不可分类」而假绿。
    """
    env = dict(os.environ)
    env["DUMATE_HOME"] = str(dumate_home)
    env["PYTHONIOENCODING"] = "utf-8"
    out_json = tmp_path / "dumate_tasks.json"
    r = subprocess.run(
        [
            PY,
            str(SCRIPTS_DIR / "collect_usage_data.py"),
            "--source",
            "dumate",
            "--start",
            START,
            "--end",
            END,
            "-o",
            str(out_json),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=str(SKILL_DIR),
    )
    assert r.returncode == 0, f"采集失败: {r.stderr[-800:]}"
    d = json.loads(out_json.read_text(encoding="utf-8"))

    # 机制断言：_task_confidence 只由启发式分类写入，证明真走了 collect_task_types
    assert d["sessions"], "fixture 应至少有 1 个会话"
    assert "_task_confidence" in d["sessions"][0], (
        "session 缺少 _task_confidence —— dumate 分支又退回s.get('task_type','其他') "
        "兜底恒等了（R7 复发）"
    )
    # 分类落进真实桶：可分类标题应命中 Bug修复，而不是「其他」
    assert d["sessions"][0]["task_type"] == "Bug修复", (
        f"期望 Bug修复，实际 {d['sessions'][0].get('task_type')!r}"
    )
    dist = d["summary"]["task_type_distribution"]
    assert dist, "task_type_distribution 不应为空"
    assert dist != {"其他": len(d["sessions"])}, (
        f"任务类型仍全部塌成「其他」（R7 未修复）: {dist}"
    )


@allure.feature("百度搭子适配器")
@allure.story("端到端")
@allure.title("报告标题正确显示「百度搭子」，不再冒充 WorkBuddy")
def test_report_title_dumate(dumate_home, tmp_path):
    env = dict(os.environ)
    env["DUMATE_HOME"] = str(dumate_home)
    env["PYTHONIOENCODING"] = "utf-8"
    out_json = tmp_path / "dumate.json"
    r1 = subprocess.run(
        [
            PY,
            str(SCRIPTS_DIR / "collect_usage_data.py"),
            "--source",
            "dumate",
            "--start",
            START,
            "--end",
            END,
            "-o",
            str(out_json),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=str(SKILL_DIR),
    )
    assert r1.returncode == 0, f"采集失败: {r1.stderr[-800:]}"

    out_md = tmp_path / "dumate.md"
    r2 = subprocess.run(
        [
            PY,
            str(SCRIPTS_DIR / "generate_report.py"),
            str(out_json),
            "--output",
            str(out_md),
            "--format",
            "markdown",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=str(SKILL_DIR),
    )
    assert r2.returncode == 0, f"报告渲染失败: {r2.stderr[-800:]}"
    md = out_md.read_text(encoding="utf-8")
    assert "# 百度搭子使用情况报告" in md
    assert "Workbuddy使用情况报告" not in md


# ── 五、隐私护栏 ────────────────────────────────────────────────────────────


@allure.feature("百度搭子适配器")
@allure.story("隐私")
@allure.title("测试全程只读 tmp_path，绝不触碰真实 ~/.workbuddy")
def test_never_reads_real_home(dumate_home, monkeypatch):
    real_traces = Path.home() / ".workbuddy" / "traces"
    before = None
    if real_traces.exists():
        before = sum(1 for _ in real_traces.rglob("trace_*.json"))
    collect_dumate(START, END)
    if before is not None:
        # 环境变量已把探测锚到 tmp_path，真实目录不应被写入（计数不变即未触碰）
        assert sum(1 for _ in real_traces.rglob("trace_*.json")) == before
    assert str(dumate_home) in str(resolve_dumate_home())


# ── token 回退（ca_sources.collect_traces） ────────────────────────────────
# 原为 @pytest.mark.xfail(strict=True) 固化「回退逻辑不存在」的现状；
# 回退已实装（ca_sources 三级：顶层 totalTokens → span 还原 → modelInfo 分项之和），
# 该用例现转正向断言。strict xfail 在逻辑生效后会 XPASS 判失败，故必须摘掉标记。
@allure.feature("百度搭子适配器")
@allure.story("token 回退")
@allure.title("顶层无 totalTokens 时回退 modelInfo，并打 _token_source=fallback 标记")
def test_total_tokens_falls_back_to_model_info(tmp_path, monkeypatch):
    """构造顶层无 totalTokens、modelInfo 也无 totalTokens（只有分项）的 trace。

    旧代码：`_to_num(trace.get("totalTokens", 0)) or (_recovered["total"] if ...)`
    两个来源都拿不到 → total_tokens = 0，连带 effective_tokens / 成本全部被低估。
    期望：回退到 modelInfo 的分项之和 300+200=500，并标记 token_source == "fallback"。
    """
    traces_dir = tmp_path / "traces"
    (traces_dir / "1001").mkdir(parents=True)
    trace = {
        "traceId": "t_fallback",
        "sessionId": "sess-fallback",
        "startedAt": "2026-08-12T10:00:00.000+08:00",
        "endedAt": "2026-08-12T10:00:01.000+08:00",
        "duration": 1000,
        "status": "ok",
        # 顶层故意不给 totalTokens
        "modelInfo": {
            "models": ["ernie-4.5-flash"],
            "totalInputTokens": 300,
            "totalOutputTokens": 200,
            "totalCachedTokens": 0,
            "callCount": 1,
        },
    }
    (traces_dir / "1001" / "trace_1.json").write_text(
        json.dumps({"trace": trace}, ensure_ascii=False), encoding="utf-8"
    )
    # 路径常量是模块级的，隔离须写到真正读它的模块上
    monkeypatch.setattr(cs, "TRACES_DIR", traces_dir)

    traces = cs.collect_traces(START, END, {"sess-fallback": "ernie-4.5-flash"})
    assert len(traces) == 1, f"应采到1 条 trace，实际 {len(traces)}"
    t = traces[0]
    assert t["total_tokens"] == 500, (
        f"total_tokens 应回退到 modelInfo 分项之和 500，实际 {t['total_tokens']}"
    )
    assert t["token_source"] == "fallback", (
        f"token_source 应标记为 fallback，实际 {t.get('token_source')!r}"
    )
