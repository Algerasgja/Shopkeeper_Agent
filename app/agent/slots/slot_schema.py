"""
语义槽位类型定义

query 侧（大模型抽取）和 sql 侧（sqlglot 解析）共用同一套结构，
都以物理 表名.字段名 表达，便于做确定性的 ⊆ 比对。
column 统一使用 "table.column" 形式；当无法限定到表时退化为裸列名。
"""

from typing import TypedDict


class MetricSlot(TypedDict):
    """聚合指标槽位，描述一次"用什么聚合算哪一列\""""

    agg: str  # SUM / COUNT / AVG / MIN / MAX / COUNT_DISTINCT
    column: str  # 物理 table.column，计数全部行时为 "*"


class FilterSlot(TypedDict):
    """过滤条件槽位，列为硬比对依据，算子和值仅用于软比对告警"""

    column: str  # 物理 table.column
    op: str  # =, >, >=, <, <=, in, like, between 等
    value: str  # 过滤值，软比对用，允许为空字符串


class SlotState(TypedDict):
    """一次问数的语义槽位集合"""

    metrics: list[MetricSlot]  # 聚合指标
    dimensions: list[str]  # 分组维度，物理 table.column
    filters: list[FilterSlot]  # 过滤条件
