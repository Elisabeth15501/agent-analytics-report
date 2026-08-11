#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/render_allure_html.py — 将 allure-pytest 生成的 allure-results/ 渲染为
单个**自包含** HTML 可视化报告（无需 Java / 官方 Allure CLI）。

用法:
    python tools/render_allure_html.py \
        --results-dir allure-results \
        --output allure-report.html

特性:
  - 完全离线：CSS/JS 全部内联，可直接双击打开，也可被 present_files 预览。
  - 数据兼容：原始 allure-results/ 仍可被官方 `allure serve` 消费（本脚本只读不写）。
  - 支持：分组（feature → story）、状态/严重度/标签筛选、关键字搜索、
          步骤树展开、附件（JSON 美化 / 文本原样）内联查看。

注意：因本机无 Java，官方 allure CLI 无法渲染；此脚本是零依赖的等效可视化方案。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import html
import json
import os
import sys

TZ = _dt.timezone(_dt.timedelta(hours=8))

STATUS_ZH = {
    "passed": "通过", "failed": "失败", "skipped": "跳过",
    "broken": "中断", "unknown": "未知", "pending": "待定",
}
SEVERITY_ORDER = ["blocker", "critical", "normal", "minor", "trivial"]
SEVERITY_ZH = {
    "blocker": "阻塞", "critical": "严重", "normal": "普通",
    "minor": "次要", "trivial": "琐碎",
}


def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def _strip_quotes(v) -> str:
    s = str(v)
    if len(s) >= 2 and s[0] in "'\"" and s[-1] == s[0]:
        return s[1:-1]
    return s


def _label(labels, name):
    for lb in labels or []:
        if lb.get("name") == name:
            return lb.get("value")
    return None


def _all_labels(labels, name):
    return [lb.get("value") for lb in (labels or []) if lb.get("name") == name]


def _fmt_duration(ms):
    if ms is None:
        return "—"
    ms = int(ms)
    if ms < 1000:
        return f"{ms} ms"
    sec = ms / 1000.0
    if sec < 60:
        return f"{sec:.2f} s"
    return f"{int(sec // 60)}m {sec % 60:.1f}s"


def _read_steps(steps):
    out = []
    for st in steps or []:
        out.append({
            "name": st.get("name", ""),
            "status": st.get("status", "unknown"),
            "start": st.get("start"),
            "stop": st.get("stop"),
            "params": st.get("parameters") or [],
            "children": _read_steps(st.get("steps")),
        })
    return out


def load_results(results_dir: str):
    tests = []
    for path in sorted(glob.glob(os.path.join(results_dir, "*-result.json"))):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        attachments = []
        for att in data.get("attachments") or []:
            src = att.get("source")
            content = ""
            ctype = att.get("type", "text/plain")
            if src:
                fpath = os.path.join(results_dir, src)
                if os.path.exists(fpath):
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as af:
                            content = af.read()
                    except Exception:
                        content = ""
            attachments.append({
                "name": att.get("name", "attachment"),
                "type": ctype,
                "content": content,
            })
        params = {p.get("name"): _strip_quotes(p.get("value"))
                  for p in (data.get("parameters") or [])}
        tests.append({
            "name": data.get("name", os.path.basename(path)),
            "status": (data.get("status") or "unknown").lower(),
            "description": data.get("description", ""),
            "labels": data.get("labels", []),
            "feature": _label(data.get("labels"), "feature") or "未分类",
            "story": _label(data.get("labels"), "story") or "—",
            "severity": (_label(data.get("labels"), "severity") or "normal").lower(),
            "tags": _all_labels(data.get("labels"), "tag"),
            "params": params,
            "start": data.get("start"),
            "stop": data.get("stop"),
            "duration": (data.get("stop") or 0) - (data.get("start") or 0),
            "steps": _read_steps(data.get("steps")),
            "attachments": attachments,
        })
    return tests


def _render_step(st, depth=0):
    dur = (st.get("stop") or 0) - (st.get("start") or 0)
    params = st.get("params") or []
    param_html = ""
    if params:
        items = "".join(
            f'<span class="sp">{_esc(p.get("name",""))}={_esc(_strip_quotes(p.get("value","")))}</span>'
            for p in params
        )
        param_html = f'<div class="sp-wrap">{items}</div>'
    children = "".join(_render_step(c, depth + 1) for c in st.get("children", []))
    return (
        f'<div class="step" style="margin-left:{depth*18}px">'
        f'<span class="dot dot-{_esc(st["status"])}"></span>'
        f'<span class="step-name">{_esc(st["name"])}</span>'
        f'<span class="step-time">{_esc(_fmt_duration(dur))}</span>'
        f'{param_html}{children}</div>'
    )


def _render_attachment(att):
    ctype = att.get("type", "text/plain")
    content = att.get("content", "")
    # 尝试把 application/json 美化
    shown = content
    if "json" in ctype:
        try:
            obj = json.loads(content)
            shown = json.dumps(obj, ensure_ascii=False, indent=2)
        except Exception:
            shown = content
    return (
        f'<details class="att"><summary>{_esc(att["name"])} '
        f'<span class="att-type">({_esc(ctype)})</span></summary>'
        f'<pre class="att-body">{_esc(shown)}</pre></details>'
    )


def _render_test(t):
    status = t["status"]
    sev = t["severity"]
    sev_zh = SEVERITY_ZH.get(sev, sev)
    tags = "".join(f'<span class="tag">#{_esc(x)}</span>' for x in t["tags"])
    param_str = " · ".join(f"{_esc(k)}={_esc(v)}" for k, v in t["params"].items())
    steps_html = "".join(_render_step(s) for s in t["steps"]) or '<div class="muted">（无步骤）</div>'
    atts_html = "".join(_render_attachment(a) for a in t["attachments"]) or '<div class="muted">（无附件）</div>'
    desc = f'<div class="tc-desc">{_esc(t["description"])}</div>' if t["description"] else ""
    return (
        f'<div class="tc" data-status="{_esc(status)}" data-severity="{_esc(sev)}" '
        f'data-feature="{_esc(t["feature"])}" data-story="{_esc(t["story"])}" '
        f'data-name="{_esc(t["name"])}" data-tags="{_esc(" ".join(t["tags"]))}">'
        f'<div class="tc-head">'
        f'<span class="badge st-{_esc(status)}">{_esc(STATUS_ZH.get(status, status))}</span>'
        f'<span class="sev sev-{_esc(sev)}">{_esc(sev_zh)}</span>'
        f'<span class="tc-name">{_esc(t["name"])}</span>'
        f'<span class="tc-meta">⏱ {_esc(_fmt_duration(t["duration"]))}'
        f'{(" · " + param_str) if param_str else ""}</span>'
        f'</div>{desc}'
        f'<div class="tc-tags">{tags}</div>'
        f'<details class="tc-detail"><summary>步骤 & 附件 ▾</summary>'
        f'<div class="steps">{steps_html}</div>'
        f'<div class="atts-title">附件</div>{atts_html}'
        f'</details></div>'
    )


CSS = """
:root{
  --bg:#f6f8fa; --panel:#ffffff; --ink:#1f2328; --muted:#656d76;
  --border:#d0d7de; --green:#1a7f37; --red:#cf222e; --yellow:#9a6700;
  --orange:#bc4c00; --blue:#0969da; --purple:#8250df;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#0d1117; --panel:#161b22; --ink:#e6edf3; --muted:#8b949e;
    --border:#30363d; --green:#3fb950; --red:#f85149; --yellow:#d29922;
    --orange:#db6d28; --blue:#58a6ff; --purple:#bc8cff;
  }
}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;
  background:var(--bg);color:var(--ink);font-size:14px;line-height:1.5}
.wrap{max-width:1100px;margin:0 auto;padding:24px 18px 60px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--muted);margin-bottom:18px}
.cards{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:18px}
.card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:12px 16px;min-width:96px}
.card .n{font-size:22px;font-weight:700}
.card .l{color:var(--muted);font-size:12px}
.bar{height:8px;border-radius:6px;background:var(--border);overflow:hidden;margin:6px 0 18px}
.bar > i{display:block;height:100%;background:var(--green)}
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:14px}
.controls input,.controls select{background:var(--panel);color:var(--ink);border:1px solid var(--border);
  border-radius:8px;padding:6px 10px;font-size:13px}
.controls input{flex:1;min-width:180px}
button.fbtn{background:var(--panel);color:var(--ink);border:1px solid var(--border);border-radius:8px;
  padding:6px 12px;cursor:pointer;font-size:13px}
button.fbtn:hover{border-color:var(--blue)}
button.fbtn.on{background:var(--blue);color:#fff;border-color:var(--blue)}
.fgroup{display:flex;gap:4px}
.group{margin-bottom:22px}
.group-h{font-size:16px;font-weight:700;margin:14px 0 8px;padding-bottom:6px;border-bottom:2px solid var(--border)}
.story-h{font-size:13px;color:var(--muted);margin:10px 0 6px;font-weight:600}
.tc{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin-bottom:8px}
.tc-head{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.tc-name{font-weight:600}
.tc-meta{color:var(--muted);font-size:12px}
.tc-desc{color:var(--muted);font-size:12px;margin-top:4px}
.tc-tags{margin-top:4px}
.tag{display:inline-block;background:var(--bg);border:1px solid var(--border);border-radius:20px;
  padding:1px 8px;font-size:11px;color:var(--muted);margin-right:4px}
.badge{display:inline-block;padding:1px 9px;border-radius:20px;font-size:12px;font-weight:600;color:#fff}
.st-passed{background:var(--green)} .st-failed{background:var(--red)}
.st-skipped{background:var(--yellow);color:#1b1b1b} .st-broken{background:var(--orange)}
.st-unknown{background:var(--muted)}
.sev{display:inline-block;padding:1px 8px;border-radius:6px;font-size:11px;font-weight:600;color:#fff}
.sev-blocker{background:#7a1f1f} .sev-critical{background:var(--red)}
.sev-normal{background:var(--blue)} .sev-minor{background:var(--purple)} .sev-trivial{background:var(--muted)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle}
.dot-passed{background:var(--green)} .dot-failed{background:var(--red)}
.dot-skipped{background:var(--yellow)} .dot-broken{background:var(--orange)} .dot-unknown{background:var(--muted)}
.steps{margin:6px 0 4px}
.step{font-size:13px;padding:2px 0}
.step-name{font-weight:500}
.step-time{color:var(--muted);font-size:11px;margin-left:6px}
.sp-wrap{margin:2px 0 2px 14px}
.sp{display:inline-block;background:var(--bg);border:1px solid var(--border);border-radius:5px;
  padding:0 6px;font-size:11px;color:var(--muted);margin:1px 3px 1px 0;font-family:monospace}
.tc-detail{margin-top:6px}
.tc-detail summary{cursor:pointer;color:var(--blue);font-size:12px;user-select:none}
.atts-title{font-size:12px;color:var(--muted);margin:6px 0 2px;font-weight:600}
.att{margin:3px 0}
.att summary{cursor:pointer;font-size:13px}
.att-body{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:8px;
  overflow:auto;font-size:12px;font-family:monospace;max-height:300px}
.att-type{color:var(--muted);font-size:11px}
.muted{color:var(--muted);font-size:12px}
.hidden{display:none!important}
"""

JS = """
function applyFilters(){
  const st = document.querySelector('#f-status').value;
  const sv = document.querySelector('#f-sev').value;
  const ft = document.querySelector('#f-feature').value;
  const q = document.querySelector('#f-search').value.trim().toLowerCase();
  document.querySelectorAll('.tc').forEach(el=>{
    let ok = true;
    if(st!=='all' && el.dataset.status!==st) ok=false;
    if(sv!=='all' && el.dataset.severity!==sv) ok=false;
    if(ft!=='all' && el.dataset.feature!==ft) ok=false;
    if(q && !el.dataset.name.toLowerCase().includes(q) && !(el.dataset.tags||'').toLowerCase().includes(q)) ok=false;
    el.classList.toggle('hidden', !ok);
  });
  // 隐藏空分组
  document.querySelectorAll('.group').forEach(g=>{
    let vis = g.querySelectorAll('.tc:not(.hidden)').length;
    g.classList.toggle('hidden', vis===0);
  });
}
function setStatus(v){
  document.querySelector('#f-status').value=v; applyFilters();
}
function toggleAll(open){
  document.querySelectorAll('.tc-detail').forEach(d=>{ d.open=open; });
}
document.addEventListener('DOMContentLoaded',()=>{
  document.querySelector('#f-search').addEventListener('input',applyFilters);
  document.querySelector('#f-status').addEventListener('change',applyFilters);
  document.querySelector('#f-sev').addEventListener('change',applyFilters);
  document.querySelector('#f-feature').addEventListener('change',applyFilters);
});
"""


def build_html(tests, results_dir):
    total = len(tests)
    counts = {}
    dur_total = 0
    for t in tests:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
        dur_total += max(0, t["duration"])
    passed = counts.get("passed", 0)
    failed = counts.get("failed", 0)
    skipped = counts.get("skipped", 0)
    broken = counts.get("broken", 0)
    rate = (passed / total * 100) if total else 0

    features = sorted({t["feature"] for t in tests})
    sev_options = "".join(
        f'<option value="{s}">{SEVERITY_ZH.get(s,s)}</option>' for s in SEVERITY_ORDER)
    feat_options = "".join(f'<option value="{_esc(f)}">{_esc(f)}</option>' for f in features)

    # 分组
    groups = {}
    for t in tests:
        groups.setdefault(t["feature"], {}).setdefault(t["story"], []).append(t)

    groups_html = ""
    for feat in sorted(groups.keys()):
        stories = groups[feat]
        body = ""
        for story in sorted(stories.keys()):
            body += f'<div class="story-h">{_esc(story)}</div>'
            body += "".join(_render_test(t) for t in stories[story])
        groups_html += f'<div class="group" data-feature="{_esc(feat)}"><div class="group-h">{_esc(feat)}</div>{body}</div>'

    gen = _dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S (UTC+08:00)")
    src_name = os.path.basename(os.path.normpath(results_dir))

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Allure 报告 · agent-analytics-report 通道归因测试</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<h1>Allure 可视化报告 — 通道识别 / 接口归因测试</h1>
<div class="sub">数据源：<code>{_esc(src_name)}</code> · 生成于 {_esc(gen)} · 共 {total} 个用例（参数化覆盖全模型）</div>

<div class="cards">
  <div class="card"><div class="n">{total}</div><div class="l">总用例</div></div>
  <div class="card"><div class="n" style="color:var(--green)">{passed}</div><div class="l">通过</div></div>
  <div class="card"><div class="n" style="color:var(--red)">{failed}</div><div class="l">失败</div></div>
  <div class="card"><div class="n" style="color:var(--yellow)">{skipped}</div><div class="l">跳过</div></div>
  <div class="card"><div class="n" style="color:var(--orange)">{broken}</div><div class="l">中断</div></div>
  <div class="card"><div class="n">{rate:.1f}%</div><div class="l">通过率</div></div>
  <div class="card"><div class="n">{_esc(_fmt_duration(dur_total))}</div><div class="l">总耗时</div></div>
</div>
<div class="bar"><i style="width:{rate:.1f}%"></i></div>

<div class="controls">
  <input id="f-search" placeholder="搜索用例名 / 标签…">
  <select id="f-status">
    <option value="all">全部状态</option>
    <option value="passed">通过</option>
    <option value="failed">失败</option>
    <option value="skipped">跳过</option>
    <option value="broken">中断</option>
  </select>
  <select id="f-sev"><option value="all">全部严重度</option>{sev_options}</select>
  <select id="f-feature"><option value="all">全部模块</option>{feat_options}</select>
  <div class="fgroup">
    <button class="fbtn" onclick="setStatus('passed')">仅通过</button>
    <button class="fbtn" onclick="setStatus('failed')">仅失败</button>
    <button class="fbtn" onclick="setStatus('all')">重置</button>
  </div>
  <div class="fgroup">
    <button class="fbtn" onclick="toggleAll(true)">展开步骤</button>
    <button class="fbtn" onclick="toggleAll(false)">收起步骤</button>
  </div>
</div>

{groups_html}

</div>
<script>{JS}</script>
</body></html>"""


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render allure-results/ to self-contained HTML")
    ap.add_argument("--results-dir", default="allure-results", help="allure-pytest 结果目录")
    ap.add_argument("--output", default="allure-report.html", help="输出 HTML 路径")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.results_dir):
        print(f"[ERROR] 找不到结果目录: {args.results_dir}", file=sys.stderr)
        return 2

    tests = load_results(args.results_dir)
    if not tests:
        print(f"[WARN] {args.results_dir} 下没有 *-result.json，未生成报告", file=sys.stderr)
        return 1

    html_out = build_html(tests, args.results_dir)
    out_path = args.output
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)

    # 摘要
    total = len(tests)
    passed = sum(1 for t in tests if t["status"] == "passed")
    failed = sum(1 for t in tests if t["status"] == "failed")
    skipped = sum(1 for t in tests if t["status"] == "skipped")
    rate = passed / total * 100 if total else 0
    print(f"[OK] 渲染完成: {out_path}")
    print(f"     用例 {total} · 通过 {passed} · 失败 {failed} · 跳过 {skipped} · 通过率 {rate:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
