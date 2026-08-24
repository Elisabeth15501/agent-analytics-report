# -*- coding: utf-8 -*-
"""把 README.md / SKILL.md 渲染为自包含 HTML（深色作品集风格）。

路径相对本文件所在目录，本地 / CI 通用。
用法：python docs_render.py
依赖：markdown（pip install markdown）
"""
import os
import markdown

HERE = os.path.dirname(os.path.abspath(__file__))

CSS = """
:root{
  --bg:#0d1117;--bg2:#161b22;--fg:#e6edf3;--muted:#8b949e;--border:#30363d;
  --green:#3fb950;--blue:#58a6ff;--amber:#d29922;--red:#f85149;
  --code-bg:#161b22;--pre-bg:#0b0f14;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);line-height:1.65;
     font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,"PingFang SC","Microsoft YaHei",sans-serif;}
.wrap{max-width:980px;margin:0 auto;padding:40px 24px 72px}
.back{display:inline-block;margin-bottom:18px;color:var(--blue);text-decoration:none;font-size:13px}
.back:hover{text-decoration:underline}
.markdown-body{font-size:15px}
.markdown-body h1,.markdown-body h2,.markdown-body h3,.markdown-body h4{
  line-height:1.3;margin:32px 0 14px;font-weight:700;
  border-bottom:1px solid var(--border);padding-bottom:8px}
.markdown-body h1{font-size:27px;margin-top:8px}
.markdown-body h2{font-size:21px}
.markdown-body h3{font-size:17px;border-bottom:none;padding-bottom:0}
.markdown-body p{margin:12px 0}
.markdown-body a{color:var(--blue);text-decoration:none}
.markdown-body a:hover{text-decoration:underline}
.markdown-body code{background:var(--code-bg);padding:2px 6px;border-radius:5px;
  font-size:85%;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.markdown-body pre{background:var(--pre-bg);border:1px solid var(--border);border-radius:8px;
  padding:14px 16px;overflow:auto}
.markdown-body pre code{background:transparent;padding:0;font-size:13px;line-height:1.5}
.markdown-body blockquote{margin:14px 0;padding:4px 16px;color:var(--muted);
  border-left:3px solid var(--border);background:var(--bg2);border-radius:0 6px 6px 0}
.markdown-body table{border-collapse:collapse;width:100%;margin:16px 0;font-size:13.5px;
  display:block;overflow:auto}
.markdown-body th,.markdown-body td{border:1px solid var(--border);padding:8px 12px;text-align:left}
.markdown-body th{background:var(--bg2);font-weight:600}
.markdown-body tr:nth-child(2n){background:rgba(255,255,255,.03)}
.markdown-body ul,.markdown-body ol{padding-left:24px;margin:12px 0}
.markdown-body li{margin:4px 0}
.markdown-body hr{border:none;border-top:1px solid var(--border);margin:28px 0}
.markdown-body img{max-width:100%}
.note{margin-top:40px;color:var(--muted);font-size:12px;border-top:1px solid var(--border);padding-top:14px}
"""

TPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style></head>
<body><div class="wrap">
<a class="back" href="index.html">← 返回作品集首页</a>
<article class="markdown-body">{body}</article>
<div class="note">本文档由 docs_render.py 从 {src} 自动渲染，随每次 push 同步更新。</div>
</div></body></html>"""

TARGETS = [("README.md", "README.html", "README · agent-analytics-report"),
           ("SKILL.md", "SKILL.html", "SKILL.md · agent-analytics-report")]


def render(src_name, out_name, title):
    src = os.path.join(HERE, src_name)
    with open(src, encoding="utf-8") as f:
        md = f.read()
    body = markdown.markdown(md, extensions=["tables", "fenced_code", "sane_lists", "toc"])
    html = TPL.format(title=title, css=CSS, body=body, src=src_name)
    out = os.path.join(HERE, out_name)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print("OK ->", out, "| bytes:", len(html.encode("utf-8")))


if __name__ == "__main__":
    for s, o, t in TARGETS:
        render(s, o, t)
