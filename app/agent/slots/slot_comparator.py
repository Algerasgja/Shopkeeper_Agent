"""
语义槽位比对

核心约束：query 槽位必须是 sql 槽位的子集（query ⊆ sql）。
也就是用户明确要的指标 维度 过滤列，最终 SQL 里都必须出现，否则视为漏算。
反向（SQL 多出用户没要的条件）只做软告警，不在这里拦截。

精确比对（is_precise=True）用 table.column 全限定名；
降级比对（is_precise=False）退化为裸列名集合，牺牲跨表精度换取不误杀。
"""

from app.agent.slots.slot_schema import FilterSlot, SlotState


def _bare(column: str) -> str:
    """取裸列名，丢弃表限定，用于降级比对"""

    return column.split(".")[-1]


def _norm_factory(is_precise: bool):
    """精确模式保留全限定名，降级模式只比裸列名"""

    return (lambda column: column) if is_precise else _bare


def _diff_value_warnings(
    query_filters: list[FilterSlot], sql_filters: list[FilterSlot], norm
) -> list[str]:
    """对同一列的过滤值/算子做软比对，差异只产告警，不进 error"""

    sql_by_column: dict[str, list[FilterSlot]] = {}
    for sql_filter in sql_filters:
        sql_by_column.setdefault(norm(sql_filter["column"]), []).append(sql_filter)

    warnings: list[str] = []
    for query_filter in query_filters:
        column = norm(query_filter["column"])
        candidates = sql_by_column.get(column)
        if not candidates:
            # 列缺失属于硬比对范畴，这里不重复告警
            continue
        query_value = (query_filter.get("value") or "").strip()
        if not query_value:
            continue
        matched = any(
            query_value == (candidate.get("value") or "").strip()
            for candidate in candidates
        )
        if not matched:
            sql_values = [candidate.get("value", "") for candidate in candidates]
            warnings.append(
                f"过滤列 {query_filter['column']} 期望值 '{query_value}'，"
                f"SQL 实际值 {sql_values}"
            )
    return warnings


def compare_slots(
    query_slots: SlotState, sql_slots: SlotState, is_precise: bool
) -> dict:
    """
    比对 query 槽位是否被 sql 槽位完整覆盖

    返回 diff 字典：
    - missing_metrics / missing_dimensions / missing_filters：query 要了但 SQL 没有，硬拦截
    - value_warnings：过滤值或算子不一致，软告警
    """

    norm = _norm_factory(is_precise)

    # 指标：agg + 列都要命中；列命中但 agg 不同（如 SUM 写成 COUNT）也算缺
    sql_metric_pairs = {
        (metric["agg"], norm(metric["column"])) for metric in sql_slots["metrics"]
    }
    missing_metrics = [
        metric
        for metric in query_slots["metrics"]
        if (metric["agg"], norm(metric["column"])) not in sql_metric_pairs
    ]

    # 维度：query 要分组的列必须出现在 sql 维度集合中
    sql_dimension_set = {norm(dimension) for dimension in sql_slots["dimensions"]}
    missing_dimensions = [
        dimension
        for dimension in query_slots["dimensions"]
        if norm(dimension) not in sql_dimension_set
    ]

    # 过滤：只要求列出现，具体值/算子交给软比对
    sql_filter_columns = {norm(item["column"]) for item in sql_slots["filters"]}
    missing_filters = [
        item
        for item in query_slots["filters"]
        if norm(item["column"]) not in sql_filter_columns
    ]

    value_warnings = _diff_value_warnings(
        query_slots["filters"], sql_slots["filters"], norm
    )

    return {
        "missing_metrics": missing_metrics,
        "missing_dimensions": missing_dimensions,
        "missing_filters": missing_filters,
        "value_warnings": value_warnings,
    }


def has_hard_violation(diff: dict) -> bool:
    """是否存在需要拦截并触发修正的硬性缺失"""

    return bool(
        diff["missing_metrics"]
        or diff["missing_dimensions"]
        or diff["missing_filters"]
    )


def format_feedback(diff: dict) -> str:
    """把缺失槽位整理成给 correct_sql 的精确反馈文案"""

    parts: list[str] = []
    if diff["missing_metrics"]:
        items = [
            f"{metric['agg']}({metric['column']})"
            for metric in diff["missing_metrics"]
        ]
        parts.append(f"缺少用户要求的指标：{', '.join(items)}")
    if diff["missing_dimensions"]:
        parts.append(
            f"缺少用户要求的分组维度：{', '.join(diff['missing_dimensions'])}"
        )
    if diff["missing_filters"]:
        items = [item["column"] for item in diff["missing_filters"]]
        parts.append(f"缺少用户要求的过滤条件列：{', '.join(items)}")
    return "语义槽位不一致：" + "；".join(parts)
