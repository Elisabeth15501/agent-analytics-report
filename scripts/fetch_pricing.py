# -*- coding: utf-8 -*-
# fetch_pricing.py — P2-2 定价数据自动更新机制
#
# 设计原则（合规 + 安全优先）：
#   - **数据源由用户自备**：不抓取任何厂商网页（反爬/合规风险），只接受一个返回
#     JSON 的 URL 或本地文件（用户自己的定价镜像 / 官方机器可读端点）；
#   - **默认不落盘**：产出 pricing.candidate.json + pricing-diff.md 供人工审核；
#     显式 --apply 才写入 pricing.json，且自动备份原文件；
#   - **校验前置**：schema、非负数、相对现价倍率超限的条目一律拒绝并写入报告；
#   - **CI 友好**：--check 模式按 _pricing_rules.updated 与 --stale-days 检测定价
#     过期，退出码非 0 供流水线拦截。
#
# 输入格式（与 pricing.json 的 models 段同构）：
#   {
#     "models": {"glm-5.2": {"input": 2.0, "output": 8.0}, ...},
#     "timed_free": {...},          # 可选
#     "custom_local": {...},        # 可选
#     "source_url": "..."          # 可选，记录来源
#   }
#
# 用法：
#   python scripts/fetch_pricing.py --url  https://your-mirror/pricing.json
#   python scripts/fetch_pricing.py --file ./new-prices.json
#   python scripts/fetch_pricing.py --url ... --apply          # 审核后落盘
#   python scripts/fetch_pricing.py --check --stale-days 30    # CI 过期检查

import argparse
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# 测试缝：FETCH_PRICING_ROOT 可把 pricing.json 等路径重定向到临时目录，
# 使 CLI 黑盒测试完全隔离，绝不触碰真实定价文件。生产环境勿设置。
_ROOT = Path(os.environ["FETCH_PRICING_ROOT"]) if os.environ.get("FETCH_PRICING_ROOT") else _HERE
PRICING_PATH = _ROOT / "pricing.json"
CANDIDATE_PATH = _ROOT / "pricing.candidate.json"
DIFF_PATH = _ROOT / "pricing-diff.md"

# 与 pricing.json._pricing_rules.unit 对齐：元 / 百万 tokens
SANE_MAX_PRICE = 10000.0   # 单价绝对上限（防脏数据）
DEFAULT_MAX_RATIO = 5.0    # 新价相对现价的最大倍率（超出视为可疑，需 --force）

EXIT_OK, EXIT_CHECK_FAILED, EXIT_USAGE, EXIT_FETCH_ERROR = 0, 1, 2, 3


def fetch_source(url=None, file=None, timeout=30):
    """从 URL 或本地文件读取定价 JSON。返回 (dict, err_str)；成功则 err 为 None。"""
    if file:
        try:
            return json.loads(Path(file).read_text(encoding="utf-8")), None
        except (OSError, json.JSONDecodeError) as e:
            return None, f"读取本地文件失败：{e}"
    if url:
        req = urllib.request.Request(url, headers={"User-Agent": "agent-analytics-report/fetch-pricing"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8")), None
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as e:
            return None, f"拉取远端定价失败：{type(e).__name__}: {e}"
    return None, "必须提供 --url 或 --file"


def _valid_price(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= float(v) <= SANE_MAX_PRICE


def validate_entries(remote, current_models, max_ratio=DEFAULT_MAX_RATIO):
    """校验远端条目。返回 (accepted, rejected)。

    accepted: {模型: {"input": x, "output": y, "_status": "new"|"update"|"same"}}
    rejected: [{model, reason}]
    """
    accepted, rejected = {}, []
    remote_models = remote.get("models")
    if not isinstance(remote_models, dict) or not remote_models:
        return {}, [{"model": "*", "reason": "缺少非空 models 段，或格式不符"}]
    for name, entry in remote_models.items():
        if not isinstance(entry, dict):
            rejected.append({"model": name, "reason": f"条目非对象：{entry!r}"})
            continue
        inp, outp = entry.get("input"), entry.get("output")
        if not _valid_price(inp) or not _valid_price(outp):
            rejected.append({"model": name, "reason": f"单价必须是非负数字（≤{SANE_MAX_PRICE}），got input={inp!r} output={outp!r}"})
            continue
        inp, outp = float(inp), float(outp)
        cur = current_models.get(name)
        if isinstance(cur, dict):
            ci, co = cur.get("input"), cur.get("output")
            if _valid_price(ci) and _valid_price(co):
                ci, co = float(ci), float(co)
                if (ci, co) == (inp, outp):
                    continue  # 无变化，跳过
                ratio = max(inp / ci if ci > 0 else 0, outp / co if co > 0 else 0,
                            ci / inp if inp > 0 else 0, co / outp if outp > 0 else 0)
                if ratio > max_ratio:
                    rejected.append({"model": name, "reason":
                                     f"新价（{inp}/{outp}）相对现价（{ci}/{co}）偏差 {ratio:.1f}x，"
                                     f"超过 --max-ratio {max_ratio}，如确认无误请加 --force"})
                    continue  # 拒绝的条目不得再落入 accepted
        accepted[name] = {"input": inp, "output": outp,
                          "_status": "new" if cur is None else "update"}
    return accepted, rejected


def build_candidate(current, accepted, remote=None, source_label=""):
    """把 accepted 合并进 current 的副本，返回候选 dict（含审计字段）。

    remote 中若带 timed_free / custom_local 段也一并合入（可选增强）。
    """
    candidate = json.loads(json.dumps(current, ensure_ascii=False))  # 深拷贝
    candidate.setdefault("models", {}).update(
        {k: {"input": v["input"], "output": v["output"]} for k, v in accepted.items()})
    if isinstance(remote, dict):
        for section in ("timed_free", "custom_local"):
            if isinstance(remote.get(section), dict):
                candidate.setdefault(section, {}).update(remote[section])
    candidate["_pricing_rules"] = dict(current.get("_pricing_rules", {}))
    candidate["_pricing_rules"]["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    candidate["_pricing_rules"]["last_fetch_source"] = source_label
    return candidate


def write_diff_report(accepted, rejected, source_label, out_path=DIFF_PATH):
    lines = [
        "# 定价更新候选（fetch_pricing）",
        "",
        f"- 来源：{source_label}",
        f"- 生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"- 接受 {len(accepted)} 条 / 拒绝 {len(rejected)} 条",
        "",
        "## 接受的变更",
        "",
        "| 模型 | 状态 | 输入 | 输出 |",
        "|---|---|---|---|",
    ]
    for name in sorted(accepted):
        v = accepted[name]
        lines.append(f"| {name} | {v['_status']} | {v['input']} | {v['output']} |")
    lines += ["", "## 拒绝的条目（需人工核对）", ""]
    if rejected:
        lines += ["| 模型 | 原因 |", "|---|---|"]
        lines += [f"| {r['model']} | {r['reason']} |" for r in rejected]
    else:
        lines.append("（无）")
    lines += ["",
              "> 本文件为审核产物。确认无误后运行 `--apply` 落盘，"
              "或手动把接受的条目誊入 `scripts/pricing.json`。"]
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")


def apply_candidate(candidate_path=None, pricing_path=None):
    """备份当前 pricing.json 后写入候选。返回备份路径。

    路径参数缺省时在调用时读取模块常量（不能写进默认值——那会在定义时
    求值固化，测试 monkeypatch 与未来重构都会被绕过）。
    """
    candidate_path = Path(candidate_path) if candidate_path else CANDIDATE_PATH
    pricing_path = Path(pricing_path) if pricing_path else PRICING_PATH
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = pricing_path.with_name(f"pricing.json.bak-{stamp}")
    if pricing_path.exists():
        shutil.copy2(pricing_path, backup)
    shutil.copy2(candidate_path, pricing_path)
    return backup


def check_stale(stale_days):
    """CI 模式：检查 _pricing_rules.updated 是否过期。返回 (is_stale, message)。"""
    try:
        data = json.loads(PRICING_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return True, f"pricing.json 读取失败：{e}"
    updated = (data.get("_pricing_rules") or {}).get("updated", "")
    if not updated:
        return True, "pricing.json 缺少 _pricing_rules.updated，无法判断新旧，请补填（YYYY-MM-DD）"
    try:
        age = (datetime.now(timezone.utc) - datetime.strptime(updated, "%Y-%m-%d").replace(tzinfo=timezone.utc)).days
    except ValueError:
        return True, f"_pricing_rules.updated 格式非法：{updated!r}（应为 YYYY-MM-DD）"
    if age > stale_days:
        # 顺带统计未定价模型，给出行动指引
        missing = [m for m, v in (data.get("models") or {}).items()
                   if isinstance(v, dict) and (v.get("input") is None or v.get("output") is None)]
        msg = (f"定价已 {age} 天未更新（{updated}），超过阈值 {stale_days} 天。"
               + (f"当前缺价模型：{', '.join(sorted(missing))}。" if missing else ""))
        return True, msg
    return False, f"定价新鲜（{updated}，{age} 天前更新）"


def main():
    ap = argparse.ArgumentParser(description="定价数据自动更新（P2-2）：拉取→校验→出候选→人工审核→落盘")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--url", help="定价 JSON 端点（你自己的镜像或官方机器可读接口）")
    src.add_argument("--file", help="本地定价 JSON 文件路径")
    ap.add_argument("--apply", action="store_true",
                    help="把候选写入 pricing.json（自动备份为 pricing.json.bak-<时间戳>）。默认只出候选不落盘")
    ap.add_argument("--force", action="store_true", help="放行超过 --max-ratio 的可疑变更")
    ap.add_argument("--max-ratio", type=float, default=DEFAULT_MAX_RATIO,
                    help=f"新价相对现价允许的最大倍率（默认 {DEFAULT_MAX_RATIO}）")
    ap.add_argument("--check", action="store_true",
                    help="CI 模式：只检查 pricing.json 是否过期（按 _pricing_rules.updated），过期退出码 1")
    ap.add_argument("--stale-days", type=int, default=30, help="过期阈值天数（默认 30）")
    args = ap.parse_args()

    if args.check:
        stale, msg = check_stale(args.stale_days)
        print(("[STALE] " if stale else "[OK] ") + msg)
        sys.exit(EXIT_CHECK_FAILED if stale else EXIT_OK)

    if not args.url and not args.file:
        ap.print_usage()
        print("\n[ERROR] 需要 --url 或 --file（CI 过期检查用 --check）", file=sys.stderr)
        sys.exit(EXIT_USAGE)

    remote, err = fetch_source(url=args.url, file=args.file)
    if err:
        print(f"[ERROR] {err}", file=sys.stderr)
        sys.exit(EXIT_FETCH_ERROR)

    try:
        current = json.loads(PRICING_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[ERROR] 当前 pricing.json 读取失败：{e}", file=sys.stderr)
        sys.exit(EXIT_FETCH_ERROR)

    accepted, rejected = validate_entries(remote, current.get("models", {}),
                                          max_ratio=args.max_ratio)
    if args.force:
        # --force 只放行超倍率条目，schema 非法仍拒绝
        recheck = {r["model"]: r for r in rejected if "超过 --max-ratio" in r["reason"]}
        for name, entry in (remote.get("models") or {}).items():
            if name in recheck and isinstance(entry, dict) and _valid_price(entry.get("input")) \
                    and _valid_price(entry.get("output")):
                cur = current.get("models", {}).get(name)
                accepted[name] = {"input": float(entry["input"]), "output": float(entry["output"]),
                                  "_status": "new" if cur is None else "update"}
                rejected = [r for r in rejected if r["model"] != name]

    source_label = args.url or str(args.file)
    print(f"[INFO] 来源：{source_label}")
    print(f"[INFO] 接受 {len(accepted)} 条（new={sum(1 for v in accepted.values() if v['_status']=='new')}，"
          f"update={sum(1 for v in accepted.values() if v['_status']=='update')}），拒绝 {len(rejected)} 条")
    for r in rejected:
        print(f"  [REJECT] {r['model']}: {r['reason']}", file=sys.stderr)

    if not accepted:
        print("[OK] 无需更新（无变化或全部被拒绝）")
        write_diff_report(accepted, rejected, source_label)
        sys.exit(EXIT_OK)

    candidate = build_candidate(current, accepted, remote=remote, source_label=source_label)
    CANDIDATE_PATH.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    write_diff_report(accepted, rejected, source_label)
    print(f"[OK] 候选已写入 {CANDIDATE_PATH.name}，差异报告见 {DIFF_PATH.name}")
    if args.apply:
        backup = apply_candidate()
        print(f"[OK] 已落盘 pricing.json（备份：{backup.name}）")
    else:
        print("[NEXT] 人工审核 diff 后加 --apply 落盘")


if __name__ == "__main__":
    main()
