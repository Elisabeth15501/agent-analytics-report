# -*- coding: utf-8 -*-
"""批次 6 测试 — main 20 行流水线 + ca_cli 三函数 + 两处漂移回归。

三档：
  * 正常例 N1–N8：参数默认值、auto 探测 bypass、兼容闸门、日期窗口、
    流水线调用顺序、缓存率分母隔离、generic tokens_source 标记、门面 re-export；
  * 边界例 B1–B4：realtime、--days 覆盖、空数据端到端、dumate tokens-only；
  * 报错例 E1–E3：3 处 exit 2 中的两处（跨源 import-official、非法日期），
    以及官方导出导入失败沿流水线向上传播。

白盒隔离：所有 detect_source / adapter 调用均 monkeypatch，绝不读用户真实数据。
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import allure
import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
PY = sys.executable


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
def cli_mod():
    injected = _ensure_path()
    try:
        import ca_cli
        yield ca_cli
    finally:
        _restore(injected)


@pytest.fixture
def facade(monkeypatch):
    """独立加载门面，并隔离 auto 探测（防读用户真实目录）。"""
    injected = _ensure_path()
    try:
        import collect_usage_data as cud
        monkeypatch.setattr("ca_detect.detect_source",
                            lambda source, **kw: {"source": source, "reason": "explicit"})
        yield cud
    finally:
        _restore(injected)


def _args(**kw):
    base = dict(
        period="week", days=None, start=None, end=None, output=None,
        realtime=False, lookup_pricing="offline", pricing_api=None,
        source="auto", cost_mode="auto", task_classifier="heuristic",
        task_llm_endpoint=None, task_llm_model=None, task_llm_api_key=None,
        task_classifier_llm_ack=False, import_official=None, official_sheet=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ─────────────────── 正常例 ───────────────────

@allure.feature("批次6 main 流水线")
@allure.title("N1 build_parser 默认值与短选项")
def test_parser_defaults(cli_mod, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["collect_usage_data.py"])
    a = cli_mod.build_parser()
    assert (a.period, a.source, a.cost_mode) == ("week", "auto", "auto")
    assert (a.lookup_pricing, a.task_classifier) == ("offline", "heuristic")
    assert a.output is None and a.import_official is None

    monkeypatch.setattr(sys, "argv", [
        "collect_usage_data.py", "-o", "/tmp/x.json", "--source", "dumate",
        "--days", "14", "--cost-mode", "tokens-only",
    ])
    b = cli_mod.build_parser()
    assert (b.output, b.source, b.days, b.cost_mode) == (
        "/tmp/x.json", "dumate", 14, "tokens-only")


@allure.feature("批次6 main 流水线")
@allure.title("N2 validate_args 显式源绕过 auto 探测")
def test_validate_explicit_bypass(cli_mod, monkeypatch):
    seen = {}

    def fake_detect(source, **kw):
        seen["called"] = True
        return {"source": source, "reason": "explicit"}

    monkeypatch.setattr("ca_detect.detect_source", fake_detect)
    a = _args(source="generic", cost_mode="auto")
    cli_mod.validate_args(a)
    assert a.source == "generic" and a.cost_mode == "tokens-only"
    assert seen.get("called") is True  # detect_source 内部自行 bypass，仍被调用但直接返回


@allure.feature("批次6 main 流水线")
@allure.title("N3 validate_args 的 --import-official 兼容闸门")
def test_validate_import_official_gate(cli_mod, monkeypatch):
    seen = {}

    def fake_detect(source, **kw):
        seen["src"] = source
        return {"source": source, "reason": "x"}

    monkeypatch.setattr("ca_detect.detect_source", fake_detect)
    a = _args(source="auto", import_official="usage.xlsx")
    cli_mod.validate_args(a)
    assert seen["src"] == "workbuddy" and a.source == "workbuddy"


@allure.feature("批次6 main 流水线")
@allure.title("N4 resolve_period_window 四种预设")
def test_window_presets(cli_mod, monkeypatch):
    from ca_core import PERIOD_DAYS
    for key in ("day", "week", "month", "year"):
        w = cli_mod.resolve_period_window(_args(period=key))
        assert w.key == key and w.days == PERIOD_DAYS.get(key, 7)
        assert w.start <= w.end and w.label


@allure.feature("批次6 main 流水线")
@allure.title("N5 绝对日期窗口 meta_days 与显式参数提示")
def test_window_absolute(cli_mod, monkeypatch):
    import io
    err = io.StringIO()
    monkeypatch.setattr(cli_mod.sys, "stderr", err)
    w = cli_mod.resolve_period_window(
        _args(start="2026-11-01", end="2026-11-07"))
    assert w.start == "2026-11-01" and w.end == "2026-11-07" and w.days == 7
    assert "生效参数：--start/--end" in err.getvalue()


@allure.feature("批次6 main 流水线")
@allure.title("N6 main 严格按契约顺序调用 20 步")
def test_main_step_order(facade, monkeypatch):
    calls = []
    names = [
        "build_parser", "validate_args", "resolve_period_window",
        "build_task_classifier_with_consent", "collect_from_source",
        "build_result_skeleton", "compute_totals", "compute_transparency_counts",
        "estimate_request_count", "build_summary", "build_task_distribution",
        "attach_aggregates", "import_official_l1", "collect_unconfigured_models",
        "tier_pricing_basis_meta", "apply_cost_mode", "decorate_pricing_meta",
        "apply_pricing_lookup", "enrich_analysis", "emit",
    ]

    def stub(name, value=None):
        def f(*a, **kw):
            calls.append(name)
            return value
        return f

    a = _args(source="generic")
    w = SimpleNamespace(start="2026-11-01", end="2026-11-07", key="week",
                        label="周报", days=7)
    bundle = SimpleNamespace(
        traces=[], sessions=[], automation_runs=[], session_credits=[],
        sid_to_rawmodel={}, skill_usage={"skills": {}}, outputs=[],
        memory_logs={}, task_types={}, cost_supported=False, source_meta={},
    )
    values = {
        "build_parser": a, "resolve_period_window": w, "collect_from_source": bundle,
        "build_result_skeleton": {"meta": {}},
        "compute_totals": SimpleNamespace(total_cost=0, total_effective_cost=0),
        "compute_transparency_counts": SimpleNamespace(),
        "estimate_request_count": 0, "collect_unconfigured_models": [],
    }
    for n in names:
        monkeypatch.setattr(facade, n, stub(n, values.get(n)))
    facade.main()
    assert calls == names, f"流水线顺序漂移: {calls}"


@allure.feature("批次6 main 流水线")
@allure.title("N7 缓存率分母按 source 隔离（dumate 用 total）")
def test_cache_rate_source_isolation(facade):
    from ca_types import CollectionBundle, Totals
    # 构造 cache.read > input 的数据：旧口径会 >100%，正确口径不会。
    totals = Totals(
        total_tokens=100, total_input=40, total_output=20, total_cached=80,
        total_effective=100, total_credits=0, total_cost=0,
        total_input_cost=0, total_output_cost=0, total_effective_cost=0,
        active_days=[],
    )
    bundle = CollectionBundle(
        [], [], [], [], {}, {"skills": {}}, [], {}, {}, False, {})
    dm = facade.compute_transparency_counts(bundle, totals, "dumate")
    wb = facade.compute_transparency_counts(bundle, totals, "workbuddy")
    assert dm.cache_rate == pytest.approx(80.0)   # 80 / total(100)
    assert wb.cache_rate == pytest.approx(200.0)  # 80 / input(40)，历史口径保留


@allure.feature("批次6 main 流水线")
@allure.title("N7b 旧两参调用兼容（source 默认 ''，口径同旧）")
def test_transparency_counts_legacy_two_arg(facade):
    """门面 re-export 的旧调用 cud.compute_transparency_counts(bundle, totals) 不得 TypeError。"""
    from ca_types import CollectionBundle, Totals
    totals = Totals(
        total_tokens=100, total_input=40, total_output=20, total_cached=80,
        total_effective=100, total_credits=0, total_cost=0,
        total_input_cost=0, total_output_cost=0, total_effective_cost=0,
        active_days=[],
    )
    bundle = CollectionBundle(
        [], [], [], [], {}, {"skills": {}}, [], {}, {}, False, {})
    legacy = facade.compute_transparency_counts(bundle, totals)
    wb = facade.compute_transparency_counts(bundle, totals, "workbuddy")
    assert legacy.cache_rate == pytest.approx(wb.cache_rate) == pytest.approx(200.0)


@allure.feature("批次6 main 流水线")
@allure.title("N8 build_result_skeleton 对 generic 也标 tokens_source")
def test_skeleton_generic_token_source(facade):
    from ca_types import CollectionBundle
    bundle = CollectionBundle(
        [], [], [], [], {}, {"skills": {}}, [], {}, {}, False, {})
    a = _args(source="generic")
    w = SimpleNamespace(start="s", end="e", key="week", label="l", days=7)
    result = facade.build_result_skeleton(a, w, bundle)
    assert result["meta"]["tokens_source"] == "estimated"


@allure.feature("批次6 main 流水线")
@allure.title("N9 门面 re-export 最终形态：新旧符号均可见")
def test_facade_reexports(facade):
    for name in ("build_parser", "validate_args", "resolve_period_window",
                 "emit", "collect_from_source", "compute_transparency_counts",
                 "build_task_classifier_with_consent", "_decide_cost_mode",
                 "fetch_remote_pricing", "import_official_l1"):
        assert hasattr(facade, name), f"门面缺符号 {name}"


# ─────────────────── 边界例 ───────────────────

@allure.feature("批次6 main 流水线")
@allure.title("B1 realtime 忽略其他日期参数并提示")
def test_window_realtime(cli_mod, monkeypatch):
    import io
    err = io.StringIO()
    monkeypatch.setattr(cli_mod.sys, "stderr", err)
    w = cli_mod.resolve_period_window(
        _args(realtime=True, period="year", start="2020-01-01", end="2020-01-02"))
    assert w.key == "day" and "实时模式" in err.getvalue()


@allure.feature("批次6 main 流水线")
@allure.title("B2 --days 覆盖 period 且 meta_days 取 days")
def test_window_days_override(cli_mod, monkeypatch):
    import io
    err = io.StringIO()
    monkeypatch.setattr(cli_mod.sys, "stderr", err)
    w = cli_mod.resolve_period_window(_args(period="month", days=3))
    assert w.days == 3 and w.key == "custom"  # resolve_date_range 的 --days 口径
    assert "--days 3" in err.getvalue()


@allure.feature("批次6 main 流水线")
@allure.title("B3 空数据端到端产出合法 JSON")
def test_cli_empty_data(tmp_path):
    env = os.environ.copy()
    for k, v in {
        "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
        "APPDATA": str(tmp_path), "DUMATE_DB_PATH": str(tmp_path / "no.db"),
        "GENERIC_HOME": str(tmp_path / "empty"),
        "GENERIC_PROJECTS_DIR": str(tmp_path / "empty" / "projects"),
        "CLAUDE_PROJECTS_DIR": str(tmp_path / "empty"),
        "CODEX_HOME": str(tmp_path / "empty"), "QWENWORK_HOME": str(tmp_path / "empty"),
        "OPENCLAW_HOME": str(tmp_path / "empty"),
    }.items():
        env[k] = v
    out = tmp_path / "out.json"
    p = subprocess.run(
        [PY, str(SCRIPTS / "collect_usage_data.py"), "--source", "generic",
         "--period", "day", "--output", str(out)],
        cwd=str(REPO), env=env, capture_output=True, text=True,
    )
    assert p.returncode == 0, p.stderr
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["meta"]["source"] == "generic"
    assert d["summary"]["total_tokens"] == 0


@allure.feature("批次6 main 流水线")
@allure.title("B4 dumate 在 auto 探测链中强制 tokens-only")
def test_validate_dumate_tokens_only(cli_mod, monkeypatch):
    monkeypatch.setattr("ca_detect.detect_source",
                        lambda source, **kw: {"source": "dumate", "reason": "auto"})
    a = _args(source="auto", cost_mode="auto")
    cli_mod.validate_args(a)
    assert a.source == "dumate" and a.cost_mode == "tokens-only"


# ─────────────────── 报错例 ───────────────────

@allure.feature("批次6 main 流水线")
@allure.title("E1 --import-official 跨源 exit 2")
def test_error_import_official_cross_source(cli_mod, monkeypatch):
    import io
    err = io.StringIO()
    monkeypatch.setattr(cli_mod.sys, "stderr", err)
    monkeypatch.setattr("ca_detect.detect_source",
                        lambda source, **kw: {"source": "codex", "reason": "explicit"})
    with pytest.raises(SystemExit) as e:
        cli_mod.validate_args(_args(source="codex", import_official="u.xlsx"))
    assert e.value.code == 2
    assert "[ERROR] --import-official 仅支持 --source workbuddy" in err.getvalue()


@allure.feature("批次6 main 流水线")
@allure.title("E2 非法日期 exit 2")
def test_error_bad_date(cli_mod, monkeypatch):
    import io
    err = io.StringIO()
    monkeypatch.setattr(cli_mod.sys, "stderr", err)
    with pytest.raises(SystemExit) as e:
        cli_mod.resolve_period_window(_args(start="2026-13-99", end="2026-14-99"))
    assert e.value.code == 2 and "[ERROR]" in err.getvalue()


@allure.feature("批次6 main 流水线")
@allure.title("E3 官方导出失败沿流水线原样抛 SystemExit(2)")
def test_error_official_propagates(facade, monkeypatch):
    def boom(*a, **kw):
        raise SystemExit(2)
    monkeypatch.setattr(facade, "build_parser", lambda: _args())
    monkeypatch.setattr(facade, "validate_args", lambda a: None)
    monkeypatch.setattr(facade, "resolve_period_window",
                        lambda a: SimpleNamespace(start="s", end="e", key="week",
                                                  label="l", days=7))
    monkeypatch.setattr(facade, "build_task_classifier_with_consent", lambda a: None)
    monkeypatch.setattr(facade, "collect_from_source", lambda *a: SimpleNamespace(
        traces=[], sessions=[], automation_runs=[], session_credits=[],
        sid_to_rawmodel={}, skill_usage={"skills": {}}, outputs=[],
        memory_logs={}, task_types={}, cost_supported=True, source_meta={}))
    for n in ("build_result_skeleton", "compute_totals", "compute_transparency_counts",
              "estimate_request_count", "build_summary", "build_task_distribution",
              "attach_aggregates"):
        monkeypatch.setattr(facade, n, lambda *a, **kw: (
            {"meta": {}} if n == "build_result_skeleton" else SimpleNamespace()))
    monkeypatch.setattr(facade, "import_official_l1", boom)
    with pytest.raises(SystemExit) as e:
        facade.main()
    assert e.value.code == 2
