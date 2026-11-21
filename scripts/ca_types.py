"""ca_types.py — main() 拆分后的跨步骤数据类型（批次 1）。

本模块只定义 4 个 dataclass，零依赖（仅标准库 dataclasses），不 import
ca_core / ca_sources / 门面，故不会改变现有导入图，也不影响任何测试。

用途（供后续批次 2–6 使用）：
  - Window              : ③ resolve_period_window 的返回载体（含 ⑥ meta_days）
  - CollectionBundle    : ⑤ collect_from_source 的返回载体，替代原 9 个局部变量 + 2 个共享状态
  - Totals              : ⑨ compute_totals 的返回载体（token / 成本 / 活跃天数）
  - TransparencyCounts  : ⑪ compute_transparency_counts 的返回载体（未知/档位/失败 + 缓存率）

设计约束：字段顺序与方案 §3.1 一致；result dict 仍是唯一累积产物，
这些 dataclass 只在步骤函数之间传递，不进 JSON，故不改变输出键顺序。
"""

from dataclasses import dataclass


@dataclass
class Window:
    """日期窗口：③④⑥ 的产物，供 ⑤ 采集与 ⑦⑧ result 骨架使用。"""
    start: str          # start_date
    end: str            # end_date
    key: str            # period_key（day/week/month/year）
    label: str          # period_label
    days: int           # meta_days（原 625–637，只依赖 args+key，可提前算）


@dataclass
class CollectionBundle:
    """采集包：⑤ collect_from_source 的返回载体。

    替代原 main() 中跨分支共享的 9 个局部变量 + cost_supported / source_meta
    两个共享状态，消除「每路分支都写同名变量」的隐式耦合。
    """
    traces: list
    sessions: list
    automation_runs: list
    session_credits: list
    sid_to_rawmodel: dict
    skill_usage: dict
    outputs: list
    memory_logs: dict
    task_types: dict
    cost_supported: bool
    source_meta: dict


@dataclass
class Totals:
    """汇总数值：⑨ compute_totals 的返回载体，对应原 677–694 行。"""
    total_tokens: int
    total_input: int
    total_output: int
    total_cached: int
    total_effective: int
    total_credits: int
    total_cost: float
    total_input_cost: float
    total_output_cost: float
    total_effective_cost: float
    active_days: list         # sorted


@dataclass
class TransparencyCounts:
    """计价透明度计数：⑪ compute_transparency_counts 的返回载体。

    对应原 746–758 行；cache_rate 由 Totals 算出后一并携带，供 ⑫ build_summary 取用。
    """
    unknown_model_traces: int
    tier_traces: list
    tier_name_cost: float
    tier_alt_hy3_cost: float
    error_traces: int
    cache_rate: float
