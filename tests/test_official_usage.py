# -*- coding: utf-8 -*-
"""F17 · 官方用量导出适配器（adapters/official_usage.py）测试。

覆盖三层：
  1. xlsx 解析层（纯标准库实现）：明文串 / 共享字符串 / inlineStr / 日期序列值
  2. 表头映射层：**按表头名**映射，兼容 5 列旧版与 6 列新版（含 User Prompt）
     —— 这是 09-13 官方加列导致「Prompt 被当成模型名」事故的回归门禁
  3. 采集 / 渲染层：--import-official 端到端、L1 横幅切换、§3.5 双源对账、零回归

所有 fixture 都在 tmp_path 内用 zipfile 现场生成最小 xlsx，**绝不读真实导出文件**；
CLI 端到端用 USERPROFILE 指到临时目录，杜绝触碰真实 ~/.workbuddy。
"""

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import allure

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_DIR / "scripts"
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from adapters.official_usage import (  # noqa: E402
    OfficialUsageError,
    collapse_display_aliases,
    collect_official_usage,
    load_official_usage,
    map_headers,
    parse_credits,
    parse_timestamp,
    read_sheet,
    reconcile_with_trace,
)

pytestmark = [pytest.mark.unit, pytest.mark.regression]


# ──────────────────────────────────────────────────────────────
# fixture：现场生成最小 xlsx（zip + XML，无需 openpyxl）
# ──────────────────────────────────────────────────────────────

def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _col_ref(idx):
    """0-based 列号 → Excel 列标（0→A, 25→Z, 26→AA）。"""
    ref = ""
    n = idx + 1
    while n:
        n, rem = divmod(n - 1, 26)
        ref = chr(65 + rem) + ref
    return ref


def _cell_xml(ref, cell):
    """cell: str（明文 t="str"）/ ("s", idx) 共享串 / ("n", val[, style]) 数值 / ("i", text) inlineStr"""
    if isinstance(cell, tuple):
        kind = cell[0]
        if kind == "s":
            return f'<c r="{ref}" t="s"><v>{cell[1]}</v></c>'
        if kind == "n":
            style = cell[2] if len(cell) > 2 else None
            s_attr = f' s="{style}"' if style is not None else ""
            return f'<c r="{ref}"{s_attr}><v>{cell[1]}</v></c>'
        if kind == "i":
            return f'<c r="{ref}" t="inlineStr"><is><t>{_esc(cell[1])}</t></is></c>'
    text = "" if cell is None else str(cell)
    return f'<c r="{ref}" t="str"><v>{_esc(text)}</v></c>'


def make_xlsx(path, rows, shared=None, num_fmts=None, sheet_name="Usage Details"):
    """生成一个最小可用 xlsx。rows 为二维列表，元素见 _cell_xml。"""
    shared = shared or []
    sheet_rows = []
    for r_i, row in enumerate(rows, start=1):
        cells = "".join(_cell_xml(f"{_col_ref(c_i)}{r_i}", c) for c_i, c in enumerate(row))
        sheet_rows.append(f'<row r="{r_i}">{cells}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData>' + "".join(sheet_rows) + '</sheetData></worksheet>'
    )

    ss_items = "".join(f'<si><t>{_esc(s)}</t></si>' for s in shared)
    ss_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
              f' count="{len(shared)}" uniqueCount="{len(shared)}">{ss_items}</sst>')

    # 默认 cellXfs：索引 0 = 常规；若传 num_fmts 则在其后追加「日期格式」样式
    xfs = ['<xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>']
    for _ in (num_fmts or []):
        xfs.append('<xf numFmtId="14" fontId="0" fillId="0" borderId="0" applyNumberFormat="1"/>')
    styles_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                  '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                  f'<cellXfs count="{len(xfs)}">' + "".join(xfs) + '</cellXfs></styleSheet>')

    wb_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
              'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
              f'<sheets><sheet name="{_esc(sheet_name)}" sheetId="1" r:id="rId1"/></sheets></workbook>')
    rels_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                 'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
                     'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                     '</Types>')

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", wb_xml)
        z.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        z.writestr("xl/sharedStrings.xml", ss_xml)
        z.writestr("xl/styles.xml", styles_xml)
    return path


# 旧版 5 列（~2026-09-12）
OLD_HEADER = ["RequestID", "积分消耗", "模型", "客户端", "时间"]
# 新版 6 列（2026-09-13 起，中间插入 User Prompt）
NEW_HEADER = ["RequestID", "积分消耗", "User Prompt", "模型", "客户端", "时间"]


def _old_rows():
    return [OLD_HEADER,
            ["id1", "0", "hy3", "WorkBuddy", "2026-09-12 10:00:00"],
            ["id2", "43.40", "hy4-preview", "WorkBuddy", "2026-09-12 11:32:00"],
            ["id3", "5.71", "hunyuan-image-alpha", "", "2026-09-12 14:24:00"],
            ["id4", "11.38", "glm-5.3-flash", "VSCode", "2026-09-13 09:00:00"]]


def _new_rows():
    return [NEW_HEADER,
            ["id1", "0", "帮我看看优化方案", "hy3", "WorkBuddy", "2026-09-13 23:53:00"],
            ["id2", "43.40", "写个脚本", "hy4-preview", "WorkBuddy", "2026-09-13 11:32:00"]]


# ──────────────────────────────────────────────────────────────
# 1. xlsx 解析层
# ──────────────────────────────────────────────────────────────

@allure.feature("F17 官方导出")
@allure.story("xlsx 解析")
@allure.title("明文单元格解析（官方导出实际形态）")
def test_read_sheet_plain_strings(tmp_path):
    p = make_xlsx(tmp_path / "a.xlsx", _old_rows())
    rows = read_sheet(p)
    assert rows[0] == OLD_HEADER
    assert rows[1][2] == "hy3"
    assert rows[2][1] == "43.40"


@allure.feature("F17 官方导出")
@allure.story("xlsx 解析")
@allure.title("缺失单元格补空串（图像模型客户端为空）")
def test_read_sheet_missing_cell_padded(tmp_path):
    rows = _old_rows()
    # 手工删掉第 3 行的「客户端」单元格，模拟空值不写单元格
    rows[3] = ["id3", "5.71", "hunyuan-image-alpha", None, "2026-09-12 14:24:00"]
    p = make_xlsx(tmp_path / "b.xlsx", rows)
    got = read_sheet(p)
    assert got[3][3] == ""


@allure.feature("F17 官方导出")
@allure.story("xlsx 解析")
@allure.title("共享字符串（t=s）与 inlineStr 均可解析")
def test_read_sheet_shared_and_inline(tmp_path):
    p = make_xlsx(tmp_path / "c.xlsx",
                  [["模型", "时间"],
                   [("s", 0), ("i", "inline-value")]],
                  shared=["hy4-preview"])
    rows = read_sheet(p)
    assert rows[1][0] == "hy4-preview"
    assert rows[1][1] == "inline-value"


@allure.feature("F17 官方导出")
@allure.story("xlsx 解析")
@allure.title("日期序列值按样式还原为 YYYY-MM-DD HH:MM:SS")
def test_read_sheet_date_serial(tmp_path):
    # 46263.5 = 2026-08-29 12:00:00（Excel 序列日，含 1900 闰年 bug 补偿）
    p = make_xlsx(tmp_path / "d.xlsx",
                  [["时间"], [("n", 46263.5, 1)]],
                  num_fmts=[1])
    rows = read_sheet(p)
    assert rows[1][0].startswith("2026-08-29 12:00")


@allure.feature("F17 官方导出")
@allure.story("xlsx 解析")
@allure.title("损坏 / 非 xlsx 文件抛 OfficialUsageError")
def test_read_sheet_bad_file(tmp_path):
    bad = tmp_path / "bad.xlsx"
    bad.write_text("not a zip", encoding="utf-8")
    with pytest.raises(OfficialUsageError):
        read_sheet(bad)


@allure.feature("F17 官方导出")
@allure.story("xlsx 解析")
@allure.title("指定不存在的工作表名抛 OfficialUsageError")
def test_read_sheet_unknown_sheet(tmp_path):
    p = make_xlsx(tmp_path / "e.xlsx", _old_rows())
    with pytest.raises(OfficialUsageError, match="找不到工作表"):
        read_sheet(p, sheet="不存在的表")


# ──────────────────────────────────────────────────────────────
# 2. 表头映射层（按名字，不认列位）
# ──────────────────────────────────────────────────────────────

@allure.feature("F17 官方导出")
@allure.story("表头映射")
@allure.title("新版 6 列表头：模型取自「模型」列而非新增的 User Prompt 列")
def test_new_format_model_not_prompt(tmp_path):
    """回归门禁：官方 09-13 加列后，按列位解析会把 Prompt 当模型名。"""
    p = make_xlsx(tmp_path / "new.xlsx", _new_rows())
    rows, meta = load_official_usage(p)
    assert {r["model"] for r in rows} == {"hy3", "hy4-preview"}
    assert rows[0]["prompt"] == "帮我看看优化方案"
    assert meta["columns"]["model"] == 3      # 6 列版「模型」在第 4 列
    assert meta["columns"]["prompt"] == 2


@allure.feature("F17 官方导出")
@allure.story("表头映射")
@allure.title("旧版 5 列同样正确（向后兼容）")
def test_old_format_columns(tmp_path):
    p = make_xlsx(tmp_path / "old.xlsx", _old_rows())
    rows, meta = load_official_usage(p)
    assert meta["columns"]["model"] == 2
    assert "prompt" not in meta["columns"]
    assert [r["model"] for r in rows] == ["hy3", "hy4-preview",
                                          "hunyuan-image-alpha", "glm-5.3-flash"]


@allure.feature("F17 官方导出")
@allure.story("表头映射")
@allure.title("表头大小写 / 空格不敏感，英文别名可用")
def test_map_headers_aliases():
    idx = map_headers(["Request ID", " 积分 ", " Model ", "Client", "Time"])
    assert idx["request_id"] == 0
    assert idx["credits"] == 1
    assert idx["model"] == 2
    assert idx["client"] == 3
    assert idx["time"] == 4


@allure.feature("F17 官方导出")
@allure.story("表头映射")
@allure.title("缺必需列（模型 / 积分 / 时间）抛错并列出缺失项")
def test_missing_required_column(tmp_path):
    p = make_xlsx(tmp_path / "miss.xlsx", [["RequestID", "客户端"], ["a", "b"]])
    with pytest.raises(OfficialUsageError, match="缺少必需列"):
        load_official_usage(p)


@allure.feature("F17 官方导出")
@allure.story("字段解析")
@allure.title("积分空值/非数字 → 0.0；时间多种格式归一")
def test_parse_credits_and_timestamp():
    assert parse_credits("") == 0.0
    assert parse_credits("1,234.50") == 1234.5
    assert parse_credits("abc") == 0.0
    assert parse_credits(None) == 0.0
    assert parse_timestamp("2026-09-13 23:53:00") == "2026-09-13 23:53:00"
    assert parse_timestamp("2026/09/13 23:53") == "2026-09-13 23:53:00"
    assert parse_timestamp("2026-09-13") == "2026-09-13 00:00:00"


# ──────────────────────────────────────────────────────────────
# 3. 聚合与免费判定
# ──────────────────────────────────────────────────────────────

@allure.feature("F17 官方导出")
@allure.story("聚合")
@allure.title("积分=0 判为免费请求，聚合与总额正确")
def test_collect_aggregation(tmp_path):
    p = make_xlsx(tmp_path / "agg.xlsx", _old_rows())
    d = collect_official_usage(p)
    assert d["totals"]["requests"] == 4
    assert d["totals"]["credits"] == round(0 + 43.40 + 5.71 + 11.38, 2)
    assert d["totals"]["free_requests"] == 1
    assert d["totals"]["paid_requests"] == 3
    top = d["by_model"][0]
    assert top["name"] == "hy4-preview" and top["credits"] == 43.4


@allure.feature("F17 官方导出")
@allure.story("聚合")
@allure.title("空客户端归入 (空)（图像模型官方不标客户端）")
def test_empty_client_group(tmp_path):
    p = make_xlsx(tmp_path / "cl.xlsx", _old_rows())
    d = collect_official_usage(p)
    names = {c["name"] for c in d["by_client"]}
    assert "(空)" in names
    assert "WorkBuddy" in names


@allure.feature("F17 官方导出")
@allure.story("聚合")
@allure.title("按北京日期过滤窗口（含端点）")
def test_date_filter(tmp_path):
    p = make_xlsx(tmp_path / "f.xlsx", _old_rows())
    d = collect_official_usage(p, start_date="2026-09-13", end_date="2026-09-13")
    assert d["totals"]["requests"] == 1
    assert d["by_model"][0]["name"] == "glm-5.3-flash"


@allure.feature("F17 官方导出")
@allure.story("聚合")
@allure.title("模型名为空的行被跳过并计数")
def test_skip_empty_model(tmp_path):
    rows = [OLD_HEADER, ["idx", "1.0", "", "WorkBuddy", "2026-09-12 10:00:00"]]
    p = make_xlsx(tmp_path / "g.xlsx", rows)
    _, meta = load_official_usage(p)
    assert meta["parsed_rows"] == 0
    assert meta["skipped_rows"] == 1


# ──────────────────────────────────────────────────────────────
# 4. 双源对账
# ──────────────────────────────────────────────────────────────

def _official_stub(rows):
    return {"by_model": [{"name": n, "requests": r, "credits": c,
                          "free_requests": 0, "paid_requests": r, "avg_credits": 0.0}
                         for n, r, c in rows],
            "totals": {"requests": sum(r for _, r, _ in rows),
                       "credits": round(sum(c for _, _, c in rows), 2)}}


@allure.feature("F17 官方导出")
@allure.story("双源对账")
@allure.title("官方有 / trace 无 → 判为 trace 盲区并汇总漏记积分")
def test_reconcile_missing_in_trace():
    off = _official_stub([("hunyuan-image-alpha", 21, 119.91), ("hy4-preview", 5, 43.4)])
    trace = [{"model": "hy4-preview", "calls": 12, "total_cost": 99.0}]
    rec = reconcile_with_trace(off, trace)
    assert [m["model"] for m in rec["missing_in_trace"]] == ["hunyuan-image-alpha"]
    assert rec["missing_credits"] == 119.91
    assert [m["model"] for m in rec["both"]] == ["hy4-preview"]
    assert rec["ratio"] == round(12 / 26, 1)  # 12 generation / 26 请求


@allure.feature("F17 官方导出")
@allure.story("双源对账")
@allure.title("trace 有 / 官方无 → 判为本地/免费/路由，不算漏记")
def test_reconcile_trace_only():
    off = _official_stub([("hy3", 2, 0.0)])
    trace = [{"model": "custom-local:qwen", "calls": 30, "total_cost": 0.0},
             {"model": "hy3", "calls": 4, "total_cost": 0.0}]
    rec = reconcile_with_trace(off, trace)
    assert [m["model"] for m in rec["trace_only"]] == ["custom-local:qwen"]
    assert rec["missing_in_trace"] == []


@allure.feature("F17 官方导出")
@allure.story("双源对账")
@allure.title("display_merge 归拢：官方侧变体名合并到基础名并保留 variants")
def test_collapse_display_aliases_merges_variants():
    by_model = [{"name": "hy3", "requests": 444, "credits": 0.0},
                {"name": "hy3-x", "requests": 97, "credits": 397.43}]
    merged, alias_of = collapse_display_aliases(
        by_model, {"hy3-x": "hy3", "hy4-preview-x": "hy4-preview"})
    assert alias_of == {"hy3": "hy3", "hy3-x": "hy3"}
    assert merged["hy3"]["requests"] == 541
    assert merged["hy3"]["credits"] == 397.43
    assert merged["hy3"]["variants"] == ["hy3-x"]
    # 无 alias_map 时行为不变（保持旧契约）
    plain, _ = collapse_display_aliases(by_model, None)
    assert set(plain) == {"hy3", "hy3-x"}
    assert all(v["variants"] == [] for v in plain.values())


@allure.feature("F17 官方导出")
@allure.story("双源对账")
@allure.title("回归：官方 hy3-x 不被误判为 trace 盲区（2026-08-13~09-12 实测 410.95 积分虚报）")
def test_reconcile_display_alias_no_false_blindspot():
    off = _official_stub([("hy3", 444, 0.0), ("hy3-x", 97, 397.43),
                          ("hy4-preview", 62, 43.4), ("hy4-preview-x", 1, 13.52),
                          ("hunyuan-image-alpha", 21, 119.91)])
    trace = [{"model": "hy3", "calls": 559, "total_cost": 92.26},
             {"model": "hy4-preview", "calls": 54, "total_cost": 17.85}]
    rec = reconcile_with_trace(off, trace, alias_map={"hy3-x": "hy3",
                                                      "hy4-preview-x": "hy4-preview"})
    assert [m["model"] for m in rec["missing_in_trace"]] == ["hunyuan-image-alpha"]
    assert rec["missing_credits"] == 119.91
    both = {m["model"]: m for m in rec["both"]}
    assert both["hy3"]["official_requests"] == 541
    assert both["hy3"]["official_credits"] == 397.43
    assert both["hy3"]["variants"] == ["hy3-x"]
    assert both["hy4-preview"]["variants"] == ["hy4-preview-x"]
    # 对照：不传 alias_map 时会误报（保留该断言以防修复被回退）
    bad = reconcile_with_trace(off, trace)
    assert set(m["model"] for m in bad["missing_in_trace"]) >= {"hy3-x", "hy4-preview-x"}


@allure.feature("F17 官方导出")
@allure.story("双源对账")
@allure.title("变体归拢不得剥离 custom-local: 前缀（保留自建/外部来源可辨识性）")
def test_reconcile_alias_keeps_custom_local_prefix():
    off = _official_stub([("hy3", 2, 0.0)])
    trace = [{"model": "custom-local:hy3", "calls": 7, "total_cost": 0.0},
             {"model": "hy3", "calls": 4, "total_cost": 0.0}]
    rec = reconcile_with_trace(off, trace, alias_map={"hy3-x": "hy3"})
    names = [m["model"] for m in rec["trace_only"]]
    assert "custom-local:hy3" in names  # 前缀未被归一化掉


# ──────────────────────────────────────────────────────────────
# 5. CLI 端到端（USERPROFILE 隔离，不碰真实数据）
# ──────────────────────────────────────────────────────────────

def _run_collect(args, home, extra_env=None):
    env = dict(os.environ)
    env["USERPROFILE"] = str(home)
    env["HOME"] = str(home)
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, str(SCRIPTS / "collect_usage_data.py")] + args,
                          capture_output=True, text=True, env=env, cwd=str(SKILL_DIR),
                          encoding="utf-8", errors="replace")


@allure.feature("F17 官方导出")
@allure.story("CLI 端到端")
@allure.title("--import-official 端到端：写入 official_usage / reconciliation，cost_source=official")
def test_cli_import_official(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    out = tmp_path / "out.json"
    p = make_xlsx(tmp_path / "cli.xlsx", _old_rows())
    r = _run_collect(["--start", "2026-09-12", "--end", "2026-09-14",
                      "--import-official", str(p), "-o", str(out)], home)
    assert r.returncode == 0, r.stderr
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["meta"]["cost_source"] == "official"
    assert d["official_usage"]["totals"]["requests"] == 4
    assert d["summary"]["total_cost_official"] > 0
    assert d["summary"]["official_requests"] == 4
    assert d["reconciliation"]["official_requests"] == 4
    assert d["meta"]["official_import"]["rows_in_window"] == 4
    # 导出文件全程只读：不应被修改
    assert p.exists()


@allure.feature("F17 官方导出")
@allure.story("CLI 端到端")
@allure.title("非 workbuddy 源 + --import-official → 退出码 2")
def test_cli_import_official_with_codex(tmp_path):
    home = tmp_path / "home2"
    home.mkdir()
    p = make_xlsx(tmp_path / "cli2.xlsx", _old_rows())
    r = _run_collect(["--source", "codex", "--import-official", str(p)], home)
    assert r.returncode == 2
    assert "仅支持 --source workbuddy" in (r.stderr or "")


@allure.feature("F17 官方导出")
@allure.story("CLI 端到端")
@allure.title("导入失败（文件不存在）→ 退出码 2 且报错可读")
def test_cli_import_missing_file(tmp_path):
    home = tmp_path / "home3"
    home.mkdir()
    r = _run_collect(["--import-official", str(tmp_path / "nope.xlsx")], home)
    assert r.returncode == 2
    assert "导入失败" in (r.stderr or "")


@allure.feature("F17 官方导出")
@allure.story("零回归")
@allure.title("不传 --import-official：无 official_usage 键，cost_source 保持 estimate")
def test_cli_zero_regression(tmp_path):
    home = tmp_path / "home4"
    home.mkdir()
    out = tmp_path / "plain.json"
    r = _run_collect(["--start", "2026-09-12", "--end", "2026-09-14", "-o", str(out)], home)
    assert r.returncode == 0, r.stderr
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["meta"]["cost_source"] == "estimate"
    assert "official_usage" not in d
    assert "reconciliation" not in d
    assert "total_cost_official" not in d["summary"]


# ──────────────────────────────────────────────────────────────
# 6. 报告渲染（L1 横幅 + §3.5 双源对账）
# ──────────────────────────────────────────────────────────────

def _report_data_with_official():
    return {
        "meta": {"period": "week", "start_date": "2026-09-12", "end_date": "2026-09-14",
                 "cost_source": "official", "timed_free": {}, "low_confidence": {}},
        "summary": {"active_day_count": 3, "active_days": ["2026-09-12"], "total_sessions": 1,
                    "total_automation_runs": 0, "successful_automation_runs": 0,
                    "total_outputs": 0, "skills_used": 0,
                    "total_tokens": 1000, "total_effective_tokens": 900,
                    "total_input_tokens": 600, "total_output_tokens": 400,
                    "total_cached_tokens": 100,
                    "total_cost": 1.0, "total_effective_cost": 0.9,
                    "total_input_cost": 0.6, "total_output_cost": 0.4,
                    "total_cost_official": 212.26, "official_requests": 82,
                    "official_free_requests": 47,
                    "task_type_distribution": {"其他": 1}},
        "daily_tokens": {}, "skill_usage": {"skills": {}}, "outputs": [],
        "automation_runs": [], "traces": [], "sessions": [],
        "model_stats": [{"model": "hy4-preview", "calls": 19, "total_cost": 50.0,
                         "configured": True, "unit_price_input": 0.29,
                         "unit_price_output": 0.29, "effective_cost": 50.0,
                         "input_tokens": 1000, "output_tokens": 500,
                         "effective_tokens": 1200, "is_delisted": False,
                         "is_router": False, "timed_free": False}],
        "model_exec_stats": [], "tier_stats": [], "session_stats": [],
        "task_token_stats": {}, "top_tasks": [],
        "official_usage": {
            "source": "workbuddy-official",
            "by_model": [{"name": "hy4-preview", "requests": 12, "credits": 43.4,
                          "free_requests": 11, "paid_requests": 1, "avg_credits": 3.62},
                         {"name": "hunyuan-image-alpha", "requests": 1, "credits": 5.71,
                          "free_requests": 0, "paid_requests": 1, "avg_credits": 5.71}],
            "totals": {"requests": 82, "credits": 212.26, "free_requests": 47,
                       "paid_requests": 35, "models": 2, "clients": 2},
            "meta": {"window": {"first": "2026-09-12", "last": "2026-09-14"}},
        },
        "reconciliation": {
            "official_requests": 82, "trace_generations": 101, "ratio": 1.2,
            "missing_in_trace": [{"model": "hunyuan-image-alpha",
                                  "official_requests": 1, "official_credits": 5.71}],
            "missing_credits": 5.71, "trace_only": [], "both": [],
        },
    }


@allure.feature("F17 官方导出")
@allure.story("报告渲染")
@allure.title("L1：横幅显示官方真值，§3.5 双源对账渲染（MD + HTML）")
def test_report_l1_rendering(report_module):
    data = _report_data_with_official()
    md = report_module.generate_markdown_report(data)
    assert "成本口径：L1 真值" in md
    assert "212.26 积分" in md
    assert "### 3.5 双源对账" in md
    assert "hunyuan-image-alpha" in md
    assert "generation 粒度" in md

    html = report_module.generate_html_report(data)
    assert "3.5 双源对账" in html
    assert "官方积分合计（L1 真值）" in html
    assert "调用次数·generation 粒度" in html


@allure.feature("F17 官方导出")
@allure.story("报告渲染")
@allure.title("§3.5 标出被归并的收费版变体（hy3 含 hy3-x），不显示为独立盲区")
def test_report_reconciliation_shows_merged_variants(report_module):
    data = _report_data_with_official()
    data["reconciliation"] = {
        "official_requests": 82, "trace_generations": 101, "ratio": 1.2,
        "missing_in_trace": [], "missing_credits": 0.0, "trace_only": [],
        "both": [{"model": "hy3", "official_requests": 541, "trace_generations": 559,
                  "official_credits": 397.43, "trace_est_cost": 92.26,
                  "variants": ["hy3-x"]}],
    }
    md = report_module.generate_markdown_report(data)
    assert "hy3（含 hy3-x）" in md
    html = report_module.generate_html_report(data)
    assert "hy3（含 hy3-x）" in html


@allure.feature("F17 官方导出")
@allure.story("报告渲染")
@allure.title("L2：无官方导出时不渲染 §3.5，横幅仍为估算警告（零回归）")
def test_report_l2_no_reconciliation(report_module):
    data = _report_data_with_official()
    data["meta"]["cost_source"] = "estimate"
    data.pop("official_usage")
    data.pop("reconciliation")
    md = report_module.generate_markdown_report(data)
    html = report_module.generate_html_report(data)
    assert "### 3.5 双源对账" not in md
    assert "3.5 双源对账" not in html
    assert "成本口径：L2" in md or "L2" in md
    assert "请勿据此做预算或账单对账" in md


@allure.feature("F17 官方导出")
@allure.story("报告渲染")
@allure.title("cost_source=official 但缺 official_usage 数据时回落 L2（防御脏数据）")
def test_report_l1_flag_without_data_falls_back(report_module):
    data = _report_data_with_official()
    data.pop("official_usage")
    md = report_module.generate_markdown_report(data)
    assert "成本口径：L1 真值" not in md
