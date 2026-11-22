#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""千问办公 · 模型 id 时间线自证工具（P1）。

回答「本机某 model id 首次/末次出现于何时、调用多少次」这类问题，用真实
（已按 requestId 全局去重的）数据替代文档假设。典型用途：
  - 本机 flash 是否早于 2026-08-26 出现（验证「标准档 8-26 才有」的时间线）
  - qwen3.8-max-preview 何时被 qwen4-max 等新模型取代

用法：
  python scripts/qwenwork_model_timeline.py --start 2026-08-01 --end 2026-11-30
  python scripts/qwenwork_model_timeline.py --days 120 --json   # 机器可读

只读本机 ~/.qwenworkcn/（可用 QWENWORK_HOME / QWENWORK_DB 覆盖），零写入、零联网。
"""
import argparse
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.qwenwork import collect_qwenwork_traces, model_id_timeline  # noqa: E402


def _resolve_dates(args):
    if args.start and args.end:
        return args.start, args.end
    from datetime import date, timedelta
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=args.days - 1)).isoformat()
    return start, end


def main():
    p = argparse.ArgumentParser(description="千问办公模型 id 时间线（本机实测自证）")
    p.add_argument("--start", help="起始日期 YYYY-MM-DD（含），与 --end 搭配")
    p.add_argument("--end", help="结束日期 YYYY-MM-DD（含），与 --start 搭配")
    p.add_argument("--days", type=int, default=120,
                   help="滚动天数（未给 --start/--end 时生效，默认 120）")
    p.add_argument("--json", action="store_true", help="输出 JSON 而非 Markdown 表格")
    args = p.parse_args()

    start, end = _resolve_dates(args)
    traces = collect_qwenwork_traces(start, end)
    rows = model_id_timeline(traces)

    if args.json:
        print(json.dumps({"start": start, "end": end, "total_calls": len(traces),
                          "models": rows}, ensure_ascii=False, indent=2))
        return

    print(f"# 千问办公模型 id 时间线（{start} ~ {end}）\n")
    print(f"共 {len(traces)} 次调用（已按 requestId 去重）、{len(rows)} 个 model id。\n")
    print("| model id | 首次出现 | 末次出现 | 调用次数 |")
    print("|---|---|---|---|")
    for r in rows:
        print(f"| {r['model']} | {r['first_date']} | {r['last_date']} | {r['calls']} |")
    print("\n> 口径：id 取 trace 的原始 `model_key` 裸名（flash/pro 等入口 id 保留），"
          "日期为调用发生日，调用次数已按 requestId 全局去重。"
          "本表为本地实测回答「何时出现」，不代表官方平台发布时间。")


if __name__ == "__main__":
    main()
