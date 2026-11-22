"""ca_billing.py — 计价口径判定与定价补价（main() 拆分 · 批次 2 建立，批次 4 扩充）。

批次 2 收纳（自门面原样搬入，语义不变）：
  - _decide_cost_mode          : 计价模式判定（纯函数）
  - fetch_remote_pricing       : 用户自有定价镜像拉取（仅显式 --pricing-api 时调用）

批次 4 收纳（自 main 内联代码块抽出，语义不变；阶段 I 仅建新、main 不调用）：
  - import_official_l1         : ⑮ 官方导出 L1 对账（含第 3 处 exit 2）
  - collect_unconfigured_models: ⑯ 未配置单价模型收集 + 折裸名去重
  - tier_pricing_basis_meta    : ⑯ 档位计价口径 meta（原与 unconfigured 相邻）
  - apply_cost_mode            : ⑰ cost_mode 判定 + tokens_only 提示
  - decorate_pricing_meta      : ⑱ 定价透明度 meta 批量赋值
  - apply_pricing_lookup       : ⑲ 搜索链接 + 条件联网补价

依赖说明：
  - fetch_remote_pricing 用 `urllib.request.urlopen`（模块单例），保留 import 形式，
    使测试 `monkeypatch.setattr(cud.urllib.request, "urlopen", ...)` 仍命中同一对象。
  - import_official_l1 内 `DISPLAY_MERGE` 用 `dict(DISPLAY_MERGE)` 调用时复制，
    保持门面/`test_display_merge` 原地改的是同一 dict 对象（契约 2）。
  - 门面 collect_usage_data.py re-export 全部名字，测试按旧路径访问不受影响。
"""

import json
import sys
import urllib.parse
import urllib.request
import urllib.error

from ca_core import (
    normalize_model,
    is_entry_tier,
    ROUTER_ALIASES,
    DISPLAY_MERGE,
    TIMED_FREE,
    LOW_CONFIDENCE,
    LOW_CONFIDENCE_BIAS,
    MODE_RATES_META,
    _PRICING_LOCAL_LOADED,
)
from ca_aggregate import build_savings_insights_from_official


def _decide_cost_mode(
    requested,
    unconfigured,
    configured_rows,
    total_cost,
    total_effective_cost,
    has_official,
):
    """判定本次报告的计价模式，返回 "priced" 或 "tokens_only"。

    priced      ：照常输出金额（默认，历史行为）
    tokens_only ：整份报告隐藏计费维度，只讲 Token / 调用 / 耗时 / 任务
    auto（默认）：只要「没有任何可计价的模型」就自动切 tokens_only——覆盖两类数据形态：
      ① 读到了模型调用数据，但全部未配置单价；② 连模型数据都读不到（如只产出聚合 token 的源）。
      适配一切无计费能力的 Agent（千问办公、百度搭子等），不依赖每适配器写死标志位。
      三条边界必须分清，否则会误删合法内容：
      ① 限免 / 本地模型的 ¥0.00 是**已配置单价**算出来的真实结果（configured_rows 非空），
         不是「没价可算」，绝不能因此删掉成本章节；
      ② 已导入官方积分导出（L1 真值）时金额来自账单，与静态价表是否命中无关；
      ③ 只有「有未配置单价的模型 + 无任何已配置行 + 总额为 0」同时成立，
         报告才会满屏 ¥0.00 与「未配置」——这时藏起来比摊开更诚实。
    """
    if requested == "tokens-only":
        return "tokens_only"
    if requested == "priced":
        return "priced"
    # auto 决策：只认「有没有可计价的模型」，与具体是哪个适配器无关。
    #   has_priced_model = 有已配置单价的模型，或存在官方账单（L1 真值，永远有金额）
    #   任一为真 → 照常 priced；都为假且本期金额为 0 → tokens_only。
    # 这把两种无计费形态一并兜住（不再依赖 `unconfigured` 是否为空这个易漏的判据）：
    #   ① unconfigured 非空、configured_rows 空（读到模型但全未配价）
    #   ② unconfigured 与 configured_rows 都空（连模型数据都读不到）
    # 限免 / 本地模型那种「单价已配置且合法为 ¥0.00」仍算 configured_rows 非空，不误判
    # （见 test_legit_zero_cost_stays_priced）。
    has_priced_model = bool(configured_rows) or has_official
    zero_everywhere = not ((total_cost or 0) > 0 or (total_effective_cost or 0) > 0)
    if not has_priced_model and zero_everywhere:
        return "tokens_only"
    return "priced"


def fetch_remote_pricing(api_url, unconfigured_models, timeout=10):
    """拉取用户自有定价镜像（仅当 --pricing-api 显式传入时调用；默认路径不调用，零网络）。

    返回 {model: {"input": float, "output": float}}；任何网络/解析异常均吞掉并返回空 dict，
    保证目标不可达时优雅降级而非崩溃（修复 SDI-4：原实现 except 只接 requests.RequestException，
    urllib 抛出的 URLError/HTTPError 会逃逸导致采集器崩溃）。URL 只来自命令行参数，不从 trace/
    会话内容派生（防 SSRF）。
    """
    network_estimates = {}
    for model in sorted(unconfigured_models):
        try:
            with urllib.request.urlopen(api_url, timeout=timeout) as resp:
                remote = json.loads(resp.read().decode("utf-8"))
            if not isinstance(remote, dict):
                raise ValueError("定价镜像响应不是 dict")
            models_map = remote.get("models", {})
            if not isinstance(models_map, dict):
                models_map = {}
            rm = (
                models_map.get(normalize_model(model))
                or models_map.get(model)
            )
            if rm and "input" in rm and "output" in rm:
                network_estimates[model] = {"input": float(rm["input"]),
                                           "output": float(rm["output"])}
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError,
                AttributeError, TypeError) as e:
            print(f"[WARN] 联网检索 {model} 失败（已降级，不影响报告）：{e}", file=sys.stderr)
    return network_estimates


# ── 批次 4：计费口径 ─────────────────────────────────────────────

def import_official_l1(result, args, window):
    """⑮：官方导出 L1 对账（对应原 747–823 行）；含第 3 处 exit 2。

    window 提供 start_date / end_date（原为 main 局部变量）。
    DISPLAY_MERGE 在调用时 dict(...) 复制（契约 2），保持与门面原地改同一对象。
    """
    if not args.import_official:
        return
    try:
        from adapters.official_usage import (
            OfficialUsageError,
            collect_official_usage,
            reconcile_with_trace,
        )

        official = collect_official_usage(
            args.import_official,
            start_date=window.start,
            end_date=window.end,
            sheet=args.official_sheet,
        )
    except (OfficialUsageError, ImportError) as e:
        print(f"[ERROR] 官方用量导出导入失败：{e}", file=sys.stderr)
        sys.exit(2)

    # L1 真值模式：§4.4 省钱建议改用官方真实积分（official.by_model），
    # 而非 trace 估算的 effective_cost。归并变体（hy3-x→hy3 等）使 §4.4 与 §3.5 对账口径一致。
    result["savings_insights"] = build_savings_insights_from_official(
        official["by_model"], alias_map=dict(DISPLAY_MERGE)
    )

    # 被报告窗口过滤掉的行数（导出通常是「最近30日」，报告窗口可能只有 7 天）
    _all_rows = official["meta"].get("parsed_rows", 0) or 0
    official["meta"]["rows_in_window"] = official["totals"]["requests"]
    official["meta"]["rows_out_of_window"] = max(
        0, _all_rows - official["totals"]["requests"]
    )

    result["official_usage"] = official
    # 官方侧先按 display_merge 归拢（hy3-x → hy3 等），与 trace 侧显示口径对齐。
    result["reconciliation"] = reconcile_with_trace(
        official, result["model_stats"], alias_map=dict(DISPLAY_MERGE)
    )
    result["meta"]["cost_source"] = "official"
    result["meta"]["official_import"] = {
        "file": official["meta"]["file"],
        "sheet": official["meta"]["sheet"],
        "window": official["meta"]["window"],
        "rows_total": _all_rows,
        "rows_in_window": official["totals"]["requests"],
        "rows_out_of_window": official["meta"]["rows_out_of_window"],
        "columns": sorted(official["meta"]["columns"]),
    }
    result["summary"]["total_cost_official"] = official["totals"]["credits"]
    result["summary"]["official_requests"] = official["totals"]["requests"]
    result["summary"]["official_free_requests"] = official["totals"][
        "free_requests"
    ]
    print(
        f"[INFO] 官方导出已导入：{official['totals']['requests']} 请求 / "
        f"{official['totals']['credits']:.2f} 积分（窗口 {official['meta']['window']['first']}"
        f" ~ {official['meta']['window']['last']}）→ 成本口径 L1 真值",
        file=sys.stderr,
    )
    if official["meta"]["rows_out_of_window"]:
        print(
            f"[WARN] 官方导出共 {_all_rows} 行，其中 "
            f"{official['meta']['rows_out_of_window']} 行不在报告窗口 "
            f"{window.start}~{window.end} 内，已排除",
            file=sys.stderr,
        )
    if official["totals"]["requests"] == 0:
        print(
            f"[WARN] 官方导出在窗口 {window.start}~{window.end} 内没有记录，"
            f"对账章节可能为空",
            file=sys.stderr,
        )


def collect_unconfigured_models(result):
    """⑯：未配置单价模型收集 + 折裸名去重（对应原 825–831 行），返回 sorted list。"""
    unconfigured = set()
    for stats in (result["model_stats"], result["model_exec_stats"]):
        for m in stats:
            # 已下架官方模型不算「缺失单价」——它们本就无需用户补写，仅标注即可
            # 档位入口（qwenwork pro / 早期 flash）也不算——它们是入口非模型、底层未公开，
            # 让用户补一个凭空的单价会污染账单口径（见 ca_core.QWENWORK_ENTRY_TIERS）。
            if (
                not m.get("configured")
                and m.get("model") not in ROUTER_ALIASES
                and not m.get("is_delisted")
                and not m.get("is_entry_tier")
                and not is_entry_tier(m.get("model"))
            ):
                unconfigured.add(m["model"])
    # 入口维度与执行维度可能对同一底层模型各记一次，补价只需裸名 → 折裸名去重
    unconfigured = {m.split(":", 1)[-1] if ":" in m else m for m in unconfigured}
    result["meta"]["unconfigured_models"] = sorted(unconfigured)
    return sorted(unconfigured)


def tier_pricing_basis_meta(result, counts):
    """⑯：档位计价口径 meta（对应原 832–840 行）。"""
    result["meta"]["tier_pricing_basis"] = {
        "note": "档位名（fast-model 等）为路由档位，非真实模型名；单价为倍率锚定估算值",
        "tier_traces": len(counts.tier_traces),
        "tier_cost": counts.tier_name_cost,
        "alt_hy3_cost": counts.tier_alt_hy3_cost,
    }


def apply_cost_mode(result, args, unconfigured, totals, has_official):
    """⑰：cost_mode 判定 + tokens_only 提示（对应原 842–865 行）。

    has_official 由调用方从 result["meta"]["cost_source"] 算出传入（纯布尔）。
    """
    cost_mode = _decide_cost_mode(
        requested=args.cost_mode,
        unconfigured=unconfigured,
        configured_rows=[
            m
            for st in (result["model_stats"], result["model_exec_stats"])
            if st
            for m in st
            if m.get("configured")
        ],
        total_cost=totals.total_cost,
        total_effective_cost=totals.total_effective_cost,
        has_official=has_official,
    )
    result["meta"]["cost_mode"] = cost_mode
    if cost_mode == "tokens_only":
        print(
            "[INFO] 计价模式=tokens_only：本期无命中单价的模型"
            + ("（未配置具名模型：" + "、".join(sorted(unconfigured)[:6])
               + ("…" if len(unconfigured) > 6 else "") + "）"
               if unconfigured else "（本期仅档位入口/底层未公开模型，无具名模型可计价）")
            + "，报告将隐藏金额维度；"
            + ("具名模型可在本机 scripts/pricing.local.json 补单价；"
               if unconfigured else "档位入口底层模型未公开，补单价无依据、请勿臆造；")
            + "或加 --cost-mode priced 强制保留现状。",
            file=sys.stderr,
        )
    return cost_mode


def decorate_pricing_meta(result):
    """⑱：定价透明度 meta 批量赋值（对应原 866–889 行）。"""
    result["meta"]["timed_free"] = dict(TIMED_FREE)
    result["meta"]["low_confidence"] = dict(LOW_CONFIDENCE)
    result["meta"]["low_confidence_bias"] = dict(LOW_CONFIDENCE_BIAS)
    result["meta"]["pricing_local_loaded"] = bool(_PRICING_LOCAL_LOADED)
    result["meta"]["mode_rates"] = dict(MODE_RATES_META.get("rates", {}))
    result["meta"]["mode_cost_estimated"] = bool(
        MODE_RATES_META.get("auto_estimate", False)
    )
    result["meta"]["mode_config_cache_loaded"] = bool(
        MODE_RATES_META.get("config_cache_loaded", False)
    )
    result["meta"]["mode_config_cache_path"] = MODE_RATES_META.get("config_cache_path")
    result["meta"]["mode_config_cache_mtime"] = MODE_RATES_META.get(
        "config_cache_mtime"
    )


def apply_pricing_lookup(result, args, unconfigured):
    """⑲：搜索链接 + 条件联网补价（对应原 890–911 行）；三条件出站门禁原样保留。"""
    pricing_lookup = {
        "mode": args.lookup_pricing,
        "api": args.pricing_api,
        "network_estimates": {},
        "search_links": {},
    }
    if args.lookup_pricing == "online" and unconfigured:
        for model in sorted(unconfigured):
            pricing_lookup["search_links"][model] = (
                "https://duckduckgo.com/html/?q="
                + urllib.parse.quote(f"{model} API pricing")
            )
    # ── 安全守卫（防 SSRF + 默认离线）──
    # 仅当用户「显式传入 --pricing-api <URL>」时才拉取**用户自己的**定价镜像；
    # URL 只来自命令行参数，绝不从 trace / 会话内容派生。目标不可达时由
    # fetch_remote_pricing 统一接 urllib 自身异常并优雅降级（修复 SDI-4）。
    if args.lookup_pricing == "online" and args.pricing_api and unconfigured:
        pricing_lookup["network_estimates"] = fetch_remote_pricing(args.pricing_api, unconfigured)
    result["meta"]["pricing_lookup"] = pricing_lookup
