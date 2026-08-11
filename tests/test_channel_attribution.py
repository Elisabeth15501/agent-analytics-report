# -*- coding: utf-8 -*-
"""通道识别（接口归因）测试 — pytest + Allure 双可视化，覆盖「全部模型」。

核心目标
--------
验证采集层能正确识别「**同一模型经不同接口调用**」这一情况：
  - 会话配置 = <model>（裸名）            → 由 parse_channel 推导的通道
    （通常为 gateway / WorkBuddy 官方接口；SiliconFlow 等第三方裸名本身即 custom-local）
  - 会话配置 = custom-local:<model>       → custom-local（用户自建 / 第三方 API Key 接口）

§3.1 接口维度必须把两者拆成两条独立行（model_key 不同）。

模型覆盖策略（关键点）
----------------------
不再硬编码某个模型，而是**动态收集采集层 runtime 已知的全模型集**：
  - 发布定价表 MODEL_PRICING
  - 下架模型 DELISTED_MODELS
  - 用户自定义 / 自建模型 USER_CUSTOM_MODELS、ALL_CUSTOM_MODELS（含本地 Ollama、外部 API）
统一小写去重后参数化。这样新模型加入定价表或用户自定义接口后，测试**自动覆盖**，
无需手工维护列表 —— 即「适用于所有模型」。

与 conftest.py 的关系
---------------------
conftest.py 注册了本技能统一的 marker 体系
（smoke / integration / blackbox / whitebox / metadata / contract / privacy / portability / golden / regression）。
本文件的每个用例都按 conftest 的语义精确标注对应 marker，并叠加 Allure 的
feature / story / title / severity / step / attach，使 CI 可按 `-m` 过滤、
Allure 报告可按层级与严重度可视化。

测试类别
--------
集成测试（Integration，跨 trace 解析 + 会话表查询 + sid_map 映射 + 通道分类多模块协同）
+ 回归测试（Regression，锁定跨窗口长会话误判 gateway 的 bug）
+ 行为/功能测试（黑盒验证采集器对外表现：按接口维度 §3.1 拆分）
+ Golden（两接口正确拆分的黄金路径）。
"""

import importlib.util
import json
import shutil
import sqlite3
import tempfile

import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Allure 接入（无 allure 环境时优雅降级为 no-op，保证脚本仍可被纯 pytest 运行）──
try:
    import allure
except ImportError:  # pragma: no cover - 仅用于未安装 allure-pytest 的兜底
    import types
    allure = types.SimpleNamespace()

    def _noop(*_a, **_k):
        def deco(func):
            return func
        return deco
    allure.step = _noop
    allure.feature = _noop
    allure.story = _noop
    allure.title = _noop
    allure.severity = _noop
    allure.attach = lambda *_a, **_k: None
    allure.severity_level = types.SimpleNamespace(
        TRIVIAL="trivial", MINOR="minor", NORMAL="normal",
        CRITICAL="critical", BLOCKER="blocker")
    allure.attachment_type = types.SimpleNamespace(
        TEXT="text/plain", JSON="application/json", HTML="text/html", CSV="text/csv")

TZ = timezone(timedelta(hours=8))

SKILL_DIR = Path(__file__).resolve().parent.parent          # .../agent-analytics-report
SRC = SKILL_DIR / "scripts" / "collect_usage_data.py"
REAL_DB = Path.home() / ".workbuddy" / "workbuddy.db"

# 选一个真实数据绝对为空的历史窗口，保证测试隔离
WINDOW = ("2020-01-06", "2020-01-12")

skip_no_db = pytest.mark.skipif(
    not REAL_DB.exists(),
    reason="未找到真实 workbuddy.db，跳过（仅影响需要复用 schema 的集成测试）",
)


# ── 动态模型集（适用于所有模型的关键）───────────────────────────────────────

def _load_collector_module():
    """独立加载采集模块（不污染测试模块命名空间）。"""
    spec = importlib.util.spec_from_file_location("cds_attr_mod", str(SRC))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _collect_test_models(mod):
    """动态收集「通道归因测试」应覆盖的全部模型（小写去重）。

    取自采集层 runtime 已知模型全集，新模型加入后自动覆盖。排除路由别名与免费特例。
    """
    models = set()
    for src in ("MODEL_PRICING", "DELISTED_MODELS", "USER_CUSTOM_MODELS", "ALL_CUSTOM_MODELS"):
        val = getattr(mod, src, None)
        if isinstance(val, dict):
            models |= {m.lower() for m in val}
        elif isinstance(val, (set, list, tuple)):
            models |= {m.lower() for m in val}
    models |= {m.lower() for m in getattr(mod, "DISCOVERED_LOCAL", set())}
    models |= {m.lower() for m in getattr(mod, "DISCOVERED_EXTERNAL", set())}
    # 排除：路由别名 / 兜底占位符 / 免费特例
    models -= {"auto", "default"}
    models = {m for m in models if ":free" not in m}
    return sorted(models)


# pytest 收集阶段：把 `model` 参数动态扩展为全模型集
def pytest_generate_tests(metafunc):
    if "model" in metafunc.fixturenames:
        m = _load_collector_module()
        models = _collect_test_models(m)
        metafunc.parametrize("model", models, ids=lambda x: f"model={x}")


# ── fixtures / helpers ──────────────────────────────────────────────────────

@pytest.fixture
def mod():
    """加载采集模块（每次测试独立加载，隔离全局状态）。"""
    return _load_collector_module()


@pytest.fixture
def env():
    """构建隔离的临时环境：空表 db（复用真实 schema）+ 空 traces 目录，测试后清理。"""
    tmp = Path(tempfile.mkdtemp(prefix="cds_attr_"))
    db_path = tmp / "workbuddy.db"
    traces = tmp / "traces"
    traces.mkdir()
    try:
        con = sqlite3.connect(str(db_path))
        con.execute("ATTACH ? AS real", (str(REAL_DB),))
        for tbl in ("sessions", "automations", "automation_runs", "session_usage"):
            try:
                con.execute(f"CREATE TABLE {tbl} AS SELECT * FROM real.{tbl} WHERE 0=1")
            except sqlite3.Error:
                pass
        con.commit()
    except sqlite3.Error:
        # 回退：直接整库复制（真实窗口无数据，安全）
        con.close()
        shutil.copy(REAL_DB, db_path)
        con = sqlite3.connect(str(db_path))
    finally:
        con.close()
    yield db_path, traces
    shutil.rmtree(tmp, ignore_errors=True)


@allure.step("插入会话 sid={sid} model={model}")
def _insert_session(db_path, sid, model, created):
    con = sqlite3.connect(str(db_path))
    con.execute(
        "INSERT INTO sessions "
        "(id,cwd,title,custom_title,status,created_at,updated_at,mode,model,is_background_automation,deleted_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (sid, "/tmp/test", f"t-{sid}", "", "active", created, created,
         "default", model, 0, None),
    )
    con.commit()
    con.close()


@allure.step("写 trace idx={idx} session={session_id} key={key_id}")
def _write_trace(traces_dir, idx, session_id, started_at,
                 models=("deepseek-v4-flash",), inp=100, out=50, key_id=None):
    """写一条 trace。key_id 仅作为「本次调用使用的 API Key 标识」场景上下文，采集层不消费。"""
    trace = {
        "traceId": f"t_{session_id}_{idx}",
        "sessionId": session_id,
        "startedAt": started_at,
        "endedAt": started_at,
        "duration": 1000,
        "status": "done",
        "modelInfo": {
            "models": list(models),
            "totalInputTokens": inp,
            "totalOutputTokens": out,
            "totalCachedTokens": 0,
            "totalTokens": inp + out,
            "callCount": 1,
        },
    }
    if key_id is not None:
        trace["keyId"] = key_id
    pd = traces_dir / str(idx)
    pd.mkdir()
    (pd / "trace_1.json").write_text(
        json.dumps({"trace": trace}, ensure_ascii=False), encoding="utf-8")


@allure.step("运行采集核心（含跨窗口补全会话）")
def _run_collection(mod, db_path, traces_dir, start, end):
    """复刻 main() 的采集核心流程（含跨窗口补全会话修复），返回 trace 列表。"""
    mod.DB_PATH = db_path
    mod.TRACES_DIR = traces_dir

    db_data = mod.collect_db_data(start, end)
    sid_to_rawmodel = {s["id"]: (s.get("model") or "default") for s in db_data["sessions"]}
    traces = mod.collect_traces(start, end, sid_to_rawmodel)

    # —— 跨窗口长会话补全会话（已修复逻辑）——
    trace_sids = {t.get("session_id") for t in traces if t.get("session_id")}
    existing = {s["id"] for s in db_data["sessions"]}
    missing = trace_sids - existing
    if missing:
        cdb = sqlite3.connect(str(db_path))
        cdb.row_factory = sqlite3.Row
        ph = ",".join("?" * len(missing))
        for r in cdb.execute(
            f"SELECT * FROM sessions WHERE id IN ({ph}) AND deleted_at IS NULL",
            list(missing),
        ).fetchall():
            db_data["sessions"].append({
                k: r[k] for k in (
                    "id", "cwd", "title", "custom_title", "status",
                    "created_at", "updated_at", "mode", "model", "is_background_automation")
            })
        cdb.close()

        for s in db_data["sessions"]:
            sid_to_rawmodel.setdefault(s["id"], s.get("model") or "default")
        traces = mod.collect_traces(start, end, sid_to_rawmodel)
    return traces


@allure.step("推导期望通道: {raw}")
def _expect_channel(mod, raw):
    """给定会话配置标识符，采集层理应归因的通道（由真实 parse_channel 推导，避免硬编码）。"""
    return mod.parse_channel(raw)[0]


def _attach_attribution(detail: dict, name: str = "归因明细"):
    """把关键归因结果作为 Allure 附件，便于报告内直接检视。"""
    allure.attach(
        json.dumps(detail, ensure_ascii=False, indent=2),
        name=name,
        attachment_type=allure.attachment_type.JSON,
    )


# ── 模型集自检（非参数化）────────────────────────────────────────────────────

@skip_no_db
@pytest.mark.smoke
@pytest.mark.metadata
@allure.feature("通道识别 / 接口归因（Channel Attribution）")
@allure.story("模型集自检")
@allure.title("动态模型发现必须非空且覆盖自建模型")
@allure.severity(allure.severity_level.NORMAL)
def test_model_set_nonempty(mod):
    """防御性：动态模型发现必须产出非空集合，否则「适用于所有模型」形同虚设。"""
    models = _collect_test_models(mod)
    assert models, "未能从采集层发现任何测试模型（pricing / 自定义模型配置异常？）"
    # 必须覆盖到用户实际自建模型（如 DeepSeek 平台 Key 接入的模型）
    assert any("deepseek" in m for m in models), "模型集应至少含 deepseek 系列"
    _attach_attribution({"discovered_models": models, "count": len(models)},
                        name="已发现模型全集")


# ── 核心：同一模型的不同接口被正确拆分 ─────────────────────────────────────

@skip_no_db
@pytest.mark.integration
@pytest.mark.smoke
@pytest.mark.golden
@allure.feature("通道识别 / 接口归因（Channel Attribution）")
@allure.story("同一模型不同接口拆分")
@allure.title("模型 {model}: 官方接口 vs custom-local 正确拆分为两通道")
@allure.severity(allure.severity_level.CRITICAL)
def test_gateway_vs_custom_local(mod, env, model):
    """官方接口(裸名→推导通道) 与 custom-local 接口应正确拆分为两通道。"""
    db_path, traces = env
    gw_raw = model                     # 裸名：通道由 parse_channel 推导
    cl_raw = f"custom-local:{model}"   # 自建 / 第三方 Key 接口
    _insert_session(db_path, "gw", gw_raw, _epoch_ms(2020, 1, 6))
    _insert_session(db_path, "cl", cl_raw, _epoch_ms(2020, 1, 7))
    _write_trace(traces, 1, "gw", "2020-01-06T10:05:00+08:00", models=(model,))
    _write_trace(traces, 2, "cl", "2020-01-07T10:05:00+08:00", models=(model,))

    out = _run_collection(mod, db_path, traces, *WINDOW)
    assert len(out) == 2, f"[{model}] 期望 2 条 trace，实得 {len(out)}"

    by = {t["session_id"]: t for t in out}
    gw_exp = _expect_channel(mod, gw_raw)
    assert by["gw"]["channel"] == gw_exp, \
        f"[{model}] 裸名会话应判 {gw_exp}，实得 {by['gw']['channel']}"
    assert by["gw"]["model_key"] == gw_raw
    assert by["cl"]["channel"] == "custom-local", \
        f"[{model}] custom-local 会话应判 custom-local，实得 {by['cl']['channel']}"
    assert by["cl"]["model_key"] == cl_raw

    # 仅当裸名本身确为 gateway 时，才验证「两个不同接口」真正拆成 gateway vs custom-local 两通道
    if gw_exp == "gateway":
        ch = {t["channel"] for t in out}
        assert ch == {"gateway", "custom-local"}, \
            f"[{model}] 两接口未分离：{ch}"

    _attach_attribution({
        "model": model,
        "gateway_side": {"raw": gw_raw, "channel": by["gw"]["channel"], "model_key": by["gw"]["model_key"]},
        "custom_local_side": {"raw": cl_raw, "channel": by["cl"]["channel"], "model_key": by["cl"]["model_key"]},
    })


# ── 回归：跨窗口长会话必须并入 sid_to_rawmodel，否则误判 gateway ──────────────

@skip_no_db
@pytest.mark.integration
@pytest.mark.regression
@allure.feature("通道识别 / 接口归因（Channel Attribution）")
@allure.story("回归: 跨窗口长会话")
@allure.title("模型 {model}: 跨窗口会话仍识别为 custom-local（防 gateway 误判）")
@allure.severity(allure.severity_level.CRITICAL)
def test_cross_window_supplement(mod, env, model):
    """回归：跨窗口长会话（创建早于窗口）必须并入 sid_to_rawmodel，否则误判 gateway。"""
    db_path, traces = env
    cl_raw = f"custom-local:{model}"
    # 创建于 2020-01-01（窗口外），但窗口内有 trace → 必须靠补全会话修复识别为 custom-local
    _insert_session(db_path, "cl_xw", cl_raw, _epoch_ms(2020, 1, 1))
    _write_trace(traces, 1, "cl_xw", "2020-01-08T10:05:00+08:00", models=(model,))

    out = _run_collection(mod, db_path, traces, *WINDOW)
    xw = next(t for t in out if t["session_id"] == "cl_xw")
    assert xw["channel"] == "custom-local", \
        f"[{model}] 跨窗口 custom-local 会话应判 custom-local(修复点)，实得 {xw['channel']}"
    assert xw["model_key"] == cl_raw
    _attach_attribution({
        "model": model, "session": "cl_xw",
        "channel": xw["channel"], "model_key": xw["model_key"],
        "note": "跨窗口补全会话修复点",
    })


# ── 同会话两次调用使用不同 Key，仍归同一接口（不按 Key 拆分）──────────────────

@skip_no_db
@pytest.mark.integration
@pytest.mark.regression
@allure.feature("通道识别 / 接口归因（Channel Attribution）")
@allure.story("同会话不同 Key")
@allure.title("模型 {model}: 同会话两次调用不同 Key 仍归同一接口（不按 Key 拆分）")
@allure.severity(allure.severity_level.BLOCKER)
def test_same_session_two_calls_different_keys(mod, env, model):
    """同会话两次调用，Key 不同，应归到同一 custom-local 接口（不按 Key 拆分）。"""
    db_path, traces = env
    cl_raw = f"custom-local:{model}"
    sid = "cl_2k"
    _insert_session(db_path, sid, cl_raw, _epoch_ms(2020, 1, 8))
    # 同一会话、同一接口，但两次调用使用了不同的 API Key（keyId 仅为场景上下文）
    _write_trace(traces, 1, sid, "2020-01-08T10:05:00+08:00", models=(model,), key_id="sk-KEY-A-xxxx")
    _write_trace(traces, 2, sid, "2020-01-08T10:30:00+08:00", models=(model,), key_id="sk-KEY-B-yyyy")

    out = _run_collection(mod, db_path, traces, *WINDOW)
    sess = [t for t in out if t["session_id"] == sid]
    assert len(sess) == 2, f"[{model}] 期望同会话 2 条 trace，实得 {len(sess)}"

    # 两次调用都判为 custom-local，且 model_key 完全一致（Key 差异不影响归因）
    for t in sess:
        assert t["channel"] == "custom-local", \
            f"[{model}] Key 差异不应改变接口：期望 custom-local，实得 {t['channel']}"
        assert t["model_key"] == cl_raw

    # §3.1 接口维度应合并到同一行（2 次调用），证明没有按 Key 拆成两行
    agg = mod.aggregate_traces_by(out, "model_key")
    row = next((r for r in agg if r["model"] == cl_raw), None)
    assert row is not None, f"[{model}] §3.1 缺少 {cl_raw} 行"
    assert row["calls"] == 2, f"[{model}] §3.1 应按接口合并 2 次调用，实得 {row['calls']}"
    _attach_attribution({
        "model": model, "session": sid,
        "calls": len(sess), "channel": "custom-local", "model_key": cl_raw,
        "keys_used": ["sk-KEY-A-xxxx", "sk-KEY-B-yyyy"],
        "section31_calls_merged": row["calls"],
        "note": "Key 差异不影响接口归因（回归锁）",
    })


# ── 端到端：§3.1 接口维度把两种接口拆成两条独立行 ───────────────────────────

@skip_no_db
@pytest.mark.integration
@pytest.mark.golden
@allure.feature("通道识别 / 接口归因（Channel Attribution）")
@allure.story("§3.1 接口维度拆分")
@allure.title("模型 {model}: §3.1 把两种接口拆成两条独立行")
@allure.severity(allure.severity_level.CRITICAL)
def test_section31_splits_two_interfaces(mod, env, model):
    """端到端：§3.1 接口维度把 gateway 侧与 custom-local 侧拆成两条独立行。"""
    db_path, traces = env
    gw_raw, cl_raw = model, f"custom-local:{model}"
    _insert_session(db_path, "gw", gw_raw, _epoch_ms(2020, 1, 6))
    _insert_session(db_path, "cl", cl_raw, _epoch_ms(2020, 1, 7))
    _insert_session(db_path, "cl_xw", cl_raw, _epoch_ms(2020, 1, 1))
    _write_trace(traces, 1, "gw", "2020-01-06T10:05:00+08:00", models=(model,))
    _write_trace(traces, 2, "cl", "2020-01-07T10:05:00+08:00", models=(model,))
    _write_trace(traces, 3, "cl_xw", "2020-01-08T10:05:00+08:00", models=(model,))

    out = _run_collection(mod, db_path, traces, *WINDOW)
    agg = mod.aggregate_traces_by(out, "model_key")
    keys = {r["model"] for r in agg}
    assert gw_raw in keys, f"[{model}] §3.1 缺少 gateway 侧行 {gw_raw}"
    assert cl_raw in keys, f"[{model}] §3.1 缺少 custom-local 侧行 {cl_raw}（两接口未分离）"

    # 通道计数：gw_raw 的通道由 parse_channel 推导（多数=gateway，SiliconFlow 类裸名本身=custom-local）
    gw_ch = _expect_channel(mod, gw_raw)
    ch_count = {}
    for t in out:
        ch_count[t["channel"]] = ch_count.get(t["channel"], 0) + 1
    expected = {}
    expected[gw_ch] = expected.get(gw_ch, 0) + 1
    expected["custom-local"] = expected.get("custom-local", 0) + 2
    assert ch_count == expected, f"[{model}] 通道计数错误: 实得 {ch_count}，期望 {expected}"
    _attach_attribution({
        "model": model,
        "section31_rows": [{"model_key": r["model"], "channel": r.get("channel"), "calls": r["calls"]}
                           for r in agg if r["model"] in (gw_raw, cl_raw)],
        "channel_count": ch_count,
    })


def _epoch_ms(y, mo, d, h=10, mi=0, s=0):
    return int(datetime(y, mo, d, h, mi, s, tzinfo=TZ).timestamp() * 1000)


if __name__ == "__main__":
    # 允许 `python test_channel_attribution.py` 直接跑（等价于 pytest 调用）
    import sys
    sys.exit(pytest.main([__file__, "-v", "--alluredir=allure-results"]))
