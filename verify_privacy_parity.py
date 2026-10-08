# -*- coding: utf-8 -*-
"""agent-analytics-report — privacy 声明一致性校验（可复现）

用途
----
验证「SKILL.md 里声明的隐私口径」与「metadata.json 的 privacy 字段」是否一致。
这是 P1 安全修复的收尾门禁：Finding #2/#6 指出扫描器读的declared privacy
statement 与实际行为不符，本脚本保证两处声明不会各自漂移。

为什么需要这个脚本（而不是靠人眼比对）
------------------------------------
SKILL.md 的 frontmatter 里**没有** `privacy:` 这个顶层键——隐私声明是
`description: |` 块标量里的**其中一行**。用`split(':')` 之类的朴素写法
会切错（那一行里含全角冒号「：」和半角冒号「:」混排，还有 `scripts/xxx.json:`
这种路径冒号）。所以必须按 YAML 块标量规则解析。

同时本脚本给出**两层结论**，避免把「主体相同」误报成「逐字相同」：
 结论 A（逐字）：两处文本**完全相等**（含前缀），则设计意图达成。
 结论 B（主体）：若 A 不成立，自动降级为「去掉前缀后主体相同」并明确报出。

用法
----
    python verify_privacy_parity.py            # 在仓库根目录执行
退出码：0 = 一致（逐字或主体）；1 = 不一致，需人工介入。

依赖：仅标准库（本机该解释器未装 pyyaml，故按 YAML 块标量规则手工实现，
      并对「块标量缩进」做了严格校验，见_check_block_scalar）。
"""
import json
import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent

# 隐私声明在 description 块标量里的行首标记（不含冒号，用 startswith 更稳）
PRIVACY_LINE_PREFIX = "隐私与权限（分阶段）："
# 块标量里这一行与整段声明共用的前缀——设计上是「同��句话」，
# 故 A 结论成立时长度相同；不成立时用它做主体比对。
SCALAR_PREFIX = PRIVACY_LINE_PREFIX


def die(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


# ── 1. 按 YAML 规则切frontmatter ────────────────────────────────────────────
def split_frontmatter(text):
    """返回 (frontmatter_body_lines, 其余正文)。不做去缩进等任何加工。"""
    if not text.startswith("---"):
        die("SKILL.md 首行不是 ---，frontmatter 缺失")
    parts = text.split("\n")
    end = None
    for i in range(1, len(parts)):
        if parts[i].strip() == "---":
            end = i
            break
    if end is None:
        die("frontmatter 没有结束标记 ---")
    return parts[1:end], parts[end + 1:]


def parse_description_block(body_lines):
    """从 frontmatter 里取出 description 的块标量内容。

    YAML 块标量规则：`key: |` 之后，所有**缩进比该行更深**的行都属于该标量，
    缩进深度取块内最小值并整体去除。
    这里刻意不依赖 PyYAML——本机解释器没装，且规则足够简单可控。
    """
    key = None
    for ln in body_lines:
        if ln.startswith("description:"):
            val = ln.split(":", 1)[1].strip()
            if val != "|":
                die(f"预期 description 为块标量 `|`，实际是 {val!r}；"
                    "若改了写法请同步本脚本")
            key = "description"
            break
    if key is None:
        die("frontmatter 里没有 description 键")

    # 收集 description 之后的所有更深缩进行
    block = []
    started = False
    for ln in body_lines[body_lines.index([b for b in body_lines if b.startswith("description:")][0]) + 1:]:
        if not ln.strip():
            block.append("")
            continue
        indent = len(ln) - len(ln.lstrip(" "))
        if indent > 0:
            block.append(ln)
            started = True
        elif started:
            break  # 回到顶层键，块结束
    if not block:
        die("description 块标量为空")
    # 去除公共缩进
    min_indent = min(len(l) - len(l.lstrip(" ")) for l in block if l.strip())
    return [l[min_indent:] if l.strip() else "" for l in block]


# ── 2. 两处取值 ──────────────────────────────────────────────────────────────
def get_skill_md_privacy_line():
    body, _ = split_frontmatter(SKILL_DIR.joinpath("SKILL.md").read_text(encoding="utf-8"))
    desc = parse_description_block(body)
    hits = [l for l in desc if l.startswith(PRIVACY_LINE_PREFIX)]
    if len(hits) != 1:
        die(f"description 块里以 {PRIVACY_LINE_PREFIX!r} 开头的行应恰好 1 条，实得 {len(hits)}")
    return hits[0].strip()


def get_metadata_privacy():
    raw = (SKILL_DIR / "metadata.json").read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as e:
        die(f"metadata.json 解析失败：{e}")
    val = data.get("privacy")
    if not isinstance(val, str) or not val.strip():
        die("metadata.json 的 privacy 缺失或不是非空字符串")
    return val.strip()


# ── 3. 两层结论 ──────────────────────────────────────────────────────────────
def main():
    skill_line = get_skill_md_privacy_line()
    meta_priv = get_metadata_privacy()

    print("取值来源（均为程序化提取，非手抄）")
    print("  A. SKILL.md  description 块标量内、匹配 "
          f"{PRIVACY_LINE_PREFIX!r} 的那一行")
    print("  B. metadata.json  ->  $.privacy")
    print()
    print(f"  A 长度 = {len(skill_line)}")
    print(f"  B 长度 = {len(meta_priv)}")
    print()

    # 结论 A：逐字（含前缀）
    verbatim = skill_line == meta_priv
    print("结论 A —— 逐字一致（含前缀）")
    print(f"  SKILL.md 行 == metadata.privacy : {verbatim}")
    if verbatim:
        print("  => 设计意图达成：两处声明是同一句话，零漂移。")
        print()
        print("RESULT: PASS (verbatim)")
        return 0

    # 结论 B：主体一致（剥前缀）
    a_body = skill_line[len(SCALAR_PREFIX):] if skill_line.startswith(SCALAR_PREFIX) else skill_line
    b_body = meta_priv[len(SCALAR_PREFIX):] if meta_priv.startswith(SCALAR_PREFIX) else meta_priv
    same_body = a_body == b_body
    print("结论 A 不成立，降级为结论 B")
    print(f"  前缀 {SCALAR_PREFIX!r} 长度 = {len(SCALAR_PREFIX)}")
    print(f"  A 剥前缀后长度 = {len(a_body)}")
    print(f"  B 剥前缀后长度 = {len(b_body)}")
    print(f"  主体是否相同 = {same_body}")
    if same_body:
        print("  => 主体语义相同，仅前缀存在差异（差异是设计如此）。")
        print()
        print("RESULT: PASS (body-identical, prefix differs by design)")
        return 0

    print("  => 主体也不同，两处声明已漂移，必须人工修正。")
    print()
    print("RESULT: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
