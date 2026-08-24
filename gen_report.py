# -*- coding: utf-8 -*-
"""从 allure-results 生成独立 HTML 测试报告（无需 Java / allure CLI）。

路径全部相对本文件所在目录，可移植（本地、CI 通用）。
用法：python gen_report.py   （需先 `pytest --alluredir=allure-results`）
"""
import json
import glob
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(HERE, "allure-results")
OUT_HTML = os.path.join(HERE, "test-report.html")

SKILL = "agent-analytics-report"

cases = []
for f in glob.glob(os.path.join(RESULTS_DIR, "*.json")):
    base = os.path.basename(f)
    if base in ("categories.json", "executor.json", "environment.properties.json"):
        continue
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception:
        continue
    if not d.get("name") or not d.get("testCaseId"):
        continue
    labels = {l.get("name"): l.get("value") for l in d.get("labels", []) if l.get("name")}
    start = d.get("start", 0) or 0
    stop = d.get("stop", 0) or 0
    dur_ms = (stop - start) / 1_000_000 if stop and start else 0  # allure 用微秒
    cases.append({
        "name": d.get("name"),
        "status": d.get("status"),
        "feature": labels.get("feature", "未分类"),
        "story": labels.get("story", "—"),
        "severity": (labels.get("severity") or "normal").lower(),
        "tags": [],
        "duration_ms": dur_ms,
    })

# 重新精确取 tag
for f in glob.glob(os.path.join(RESULTS_DIR, "*.json")):
    base = os.path.basename(f)
    if base in ("categories.json", "executor.json", "environment.properties.json"):
        continue
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception:
        continue
    if not d.get("name") or not d.get("testCaseId"):
        continue
    tags = [l.get("value") for l in d.get("labels", []) if l.get("name") == "tag" and l.get("value")]
    for c in cases:
        if c["name"] == d.get("name") and c["status"] == d.get("status"):
            c["tags"] = tags
            break

total = len(cases)
passed = sum(1 for c in cases if c["status"] == "passed")
failed = sum(1 for c in cases if c["status"] != "passed")
broken = sum(1 for c in cases if c["status"] in ("broken", "failed"))
skipped = sum(1 for c in cases if c["status"] == "skipped")
total_ms = sum(c["duration_ms"] for c in cases)

feat_tree = defaultdict(lambda: defaultdict(list))
for c in cases:
    feat_tree[c["feature"]][c["story"]].append(c)

by_sev = defaultdict(int)
for c in cases:
    by_sev[c["severity"]] += 1

SEV_ORDER = ["blocker", "critical", "normal", "minor", "trivial"]
SEV_CN = {"blocker": "致命", "critical": "严重", "normal": "一般", "minor": "次要", "trivial": "轻微"}

def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

parts = []
parts.append("""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>agent-analytics-report · L0–L4 测试报告</title>
<style>
  :root{--bg:#ffffff;--fg:#1f2328;--muted:#656d76;--card:#f6f8fa;--border:#d0d7de;
        --green:#1a7f37;--green-bg:#daffd4;--red:#cf222e;--red-bg:#ffebe9;
        --blue:#0969da;--amber:#9a6700;--amber-bg:#fff8c5;}
  *{box-sizing:border-box}
  body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,"PingFang SC","Microsoft YaHei",sans-serif;
       background:var(--bg);color:var(--fg);line-height:1.5;padding:32px 24px;}
  .wrap{max-width:1080px;margin:0 auto;}
  h1{font-size:24px;margin:0 0 4px}
  .sub{color:var(--muted);font-size:13px;margin-bottom:24px}
  .cards{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:28px}
  .card{flex:1;min-width:150px;background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px 18px}
  .card .n{font-size:28px;font-weight:700;line-height:1.1}
  .card .l{color:var(--muted);font-size:12px;margin-top:4px}
  .card.pass .n{color:var(--green)} .card.fail .n{color:var(--red)}
  .card.skip .n{color:var(--amber)}
  .bar{height:8px;border-radius:6px;background:var(--green);margin-top:10px}
  .sev{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 26px}
  .sev span{font-size:12px;background:var(--card);border:1px solid var(--border);border-radius:20px;padding:3px 12px}
  h2{font-size:17px;border-bottom:1px solid var(--border);padding-bottom:8px;margin:28px 0 14px}
  .feat{margin-bottom:22px}
  .feat>.ft{font-weight:700;font-size:15px;margin-bottom:6px}
  .story{margin:10px 0 6px 14px;color:var(--muted);font-size:13px;font-weight:600}
  table{width:100%;border-collapse:collapse;font-size:13px;margin-left:14px}
  th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--border)}
  th{color:var(--muted);font-weight:600;font-size:12px}
  .badge{display:inline-block;font-size:11px;font-weight:700;border-radius:5px;padding:1px 8px}
  .b-pass{background:var(--green-bg);color:var(--green)}
  .b-fail{background:var(--red-bg);color:var(--red)}
  .b-skip{background:var(--amber-bg);color:var(--amber)}
  .tag{display:inline-block;font-size:10px;background:#eaeef2;color:#57606a;border-radius:4px;padding:0 6px;margin-left:4px}
  .sev-badge{display:inline-block;font-size:10px;border-radius:4px;padding:0 6px;margin-left:6px;background:#eaeef2;color:#57606a}
  .sev-blocker{background:#ffebe9;color:#cf222e}.sev-critical{background:#fff1e5;color:#bc4c00}
  .sev-normal{background:#ddf4ff;color:#0969da}.sev-minor{background:#f6f8fa;color:#57606a}
  .dur{color:var(--muted);font-size:11px}
  footer{margin-top:36px;color:var(--muted);font-size:12px;border-top:1px solid var(--border);padding-top:14px}
</style></head><body><div class="wrap">""")

parts.append(f'<h1>{esc(SKILL)} · L0 – L4 测试报告</h1>')
parts.append(f'<div class="sub">pytest + allure-pytest &nbsp;·&nbsp; 生成于 {datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")} (UTC+08:00) &nbsp;·&nbsp; 共 {total} 用例</div>')

parts.append('<div class="cards">')
parts.append(f'<div class="card"><div class="n">{total}</div><div class="l">用例总数</div></div>')
parts.append(f'<div class="card pass"><div class="n">{passed}</div><div class="l">通过</div><div class="bar"></div></div>')
fail_cls = "fail" if failed else "pass"
parts.append(f'<div class="card {fail_cls}"><div class="n">{failed}</div><div class="l">失败 / 异常</div></div>')
parts.append(f'<div class="card skip"><div class="n">{skipped}</div><div class="l">跳过</div></div>')
parts.append(f'<div class="card"><div class="n">{total_ms/1000:.2f}s</div><div class="l">总耗时</div></div>')
parts.append('</div>')

parts.append('<div class="sev">')
for s in SEV_ORDER:
    if by_sev.get(s):
        parts.append(f'<span>{SEV_CN.get(s, s)}（{s}）：{by_sev[s]}</span>')
parts.append('</div>')

parts.append('<h2>测试矩阵（按 Feature / Story）</h2>')
for feat in sorted(feat_tree.keys()):
    parts.append(f'<div class="feat"><div class="ft">{esc(feat)}</div>')
    for story in sorted(feat_tree[feat].keys()):
        rows = feat_tree[feat][story]
        if story != "—":
            parts.append(f'<div class="story">{esc(story)}</div>')
        parts.append('<table><thead><tr><th>用例</th><th>状态</th><th>严重度</th><th>标记</th><th>耗时</th></tr></thead><tbody>')
        for c in rows:
            st = c["status"]
            bcls = "b-pass" if st == "passed" else ("b-skip" if st == "skipped" else "b-fail")
            btxt = "通过" if st == "passed" else ("跳过" if st == "skipped" else "失败")
            sev = c["severity"]
            tags_html = "".join(f'<span class="tag">{esc(t)}</span>' for t in c["tags"])
            parts.append(
                f'<tr><td>{esc(c["name"])}</td>'
                f'<td><span class="badge {bcls}">{btxt}</span></td>'
                f'<td><span class="sev-badge sev-{esc(sev)}">{SEV_CN.get(sev, sev)}</span></td>'
                f'<td>{tags_html}</td>'
                f'<td class="dur">{c["duration_ms"]:.0f} ms</td></tr>'
            )
        parts.append('</tbody></table>')
    parts.append('</div>')

parts.append(f'<footer>本报告由 pytest + allure-pytest 生成 results 后，离线聚合为自包含 HTML（无需 Java / allure CLI）。'
             f'原始 allure-results 位于 <code>allure-results/</code> 下。</footer>')
parts.append('</div></body></html>')

open(OUT_HTML, "w", encoding="utf-8").write("\n".join(parts))
print("OK ->", OUT_HTML, "| cases:", total, "passed:", passed, "failed:", failed)
