"""
SQL 侧语义槽位抽取

基于 sqlglot 把最终 SQL 解析成 AST，再抽取指标 维度 过滤三类槽位。
抽取前先用 filtered table_infos 构造 schema 做 qualify，把列补全成 table.column，
这样和 query 侧抽取的物理命名保持一致，比对才有意义。

qualify 失败（列歧义 不存在 或 SQL 过于复杂）不在这里判错，
而是返回 is_precise=False 让比对层降级到裸列名比对，避免误杀正确 SQL。
"""

from sqlglot import exp, parse_one
from sqlglot.optimizer.qualify import qualify

from app.agent.slots.slot_schema import SlotState

# sqlglot 聚合表达式到统一 agg 名称的映射
_AGG_MAP = {
    exp.Sum: "SUM",
    exp.Count: "COUNT",
    exp.Avg: "AVG",
    exp.Min: "MIN",
    exp.Max: "MAX",
}


def _build_schema(table_infos: list) -> dict:
    """把 filtered table_infos 转成 sqlglot qualify 需要的 {表: {列: 类型}} 结构"""

    schema: dict[str, dict[str, str]] = {}
    for table_info in table_infos:
        columns = {
            column["name"]: column.get("type") or "UNKNOWN"
            for column in table_info["columns"]
        }
        schema[table_info["name"]] = columns
    return schema


def _build_alias_map(ast: exp.Expression) -> dict:
    """构造 表别名->真实表名 映射，把 SQL 里的 o/r 这类别名还原成物理表名"""

    alias_map: dict[str, str] = {}
    for table in ast.find_all(exp.Table):
        real_name = table.name
        # 别名优先，没有别名时键就是表名自身，保证 column.table 都能查到
        alias = table.alias or table.name
        alias_map[alias] = real_name
    return alias_map


def _column_fqn(column: exp.Column, alias_map: dict) -> str:
    """把列表达式还原成 真实表名.column，无表限定时退化为裸列名"""

    if column.table:
        real_table = alias_map.get(column.table, column.table)
        return f"{real_table}.{column.name}"
    return column.name


def _has_complex_structure(ast: exp.Expression) -> bool:
    """子查询 CTE 集合操作等会让槽位跨层失真，命中即降级为非精确比对"""

    return bool(
        ast.find(exp.Subquery)
        or ast.find(exp.CTE)
        or ast.find(exp.Union)
        or ast.find(exp.Window)
    )


def _extract_metrics(ast: exp.Expression, alias_map: dict) -> list:
    """抽取聚合指标：聚合函数 + 其作用列"""

    metrics = []
    for node in ast.find_all(exp.AggFunc):
        agg = _AGG_MAP.get(type(node))
        if agg is None:
            continue
        # COUNT(DISTINCT x) 与 COUNT(x) 口径不同，单独标记
        if isinstance(node, exp.Count) and node.this and node.this.find(exp.Distinct):
            agg = "COUNT_DISTINCT"
        column = node.find(exp.Column)
        metrics.append(
            {"agg": agg, "column": _column_fqn(column, alias_map) if column else "*"}
        )
    return metrics


def _extract_dimensions(ast: exp.Expression, alias_map: dict) -> list:
    """抽取分组维度：优先 GROUP BY 列，没有 GROUP BY 时取 SELECT 中的非聚合列"""

    dimensions: list[str] = []
    group = ast.find(exp.Group)
    if group is not None:
        for expression in group.expressions:
            column = expression.find(exp.Column)
            if column is not None:
                dimensions.append(_column_fqn(column, alias_map))
        return dimensions

    # 无 GROUP BY：SELECT 里不在聚合函数内的列视为隐式维度
    select = ast.find(exp.Select)
    if select is not None:
        for projection in select.expressions:
            if projection.find(exp.AggFunc) is not None:
                continue
            for column in projection.find_all(exp.Column):
                dimensions.append(_column_fqn(column, alias_map))
    return dimensions


def _literal_value(predicate: exp.Expression) -> str:
    """从谓词右侧尽量取出字面值，仅供软比对，取不到返回空串"""

    literal = predicate.find(exp.Literal)
    if literal is not None:
        return literal.this
    return ""


def _extract_filters(ast: exp.Expression, alias_map: dict) -> list:
    """抽取过滤条件：WHERE 内每个比较谓词的列 算子和值"""

    filters = []
    where = ast.find(exp.Where)
    if where is None:
        return filters
    for predicate in where.find_all(exp.Binary):
        # 只关心带列的比较谓词，AND/OR 这类连接节点跳过
        if isinstance(predicate, (exp.And, exp.Or)):
            continue
        column = predicate.find(exp.Column)
        if column is None:
            continue
        filters.append(
            {
                "column": _column_fqn(column, alias_map),
                "op": predicate.key,
                "value": _literal_value(predicate),
            }
        )
    return filters


def extract_sql_slots(
    sql: str, table_infos: list, dialect: str
) -> tuple[SlotState, bool]:
    """
    从最终 SQL 抽取语义槽位

    返回 (槽位, is_precise)：is_precise 为 True 表示列已限定到表，可做精确比对；
    为 False 表示 qualify 失败或 SQL 过于复杂，比对层应降级到裸列名比对。
    """

    ast = parse_one(sql, read=dialect)

    is_precise = True
    if _has_complex_structure(ast):
        # 复杂结构下槽位会跨层混淆，直接降级，不强行 qualify
        is_precise = False
    else:
        try:
            ast = qualify(ast, schema=_build_schema(table_infos), dialect=dialect)
        except Exception:
            # 列歧义 不存在等问题在 EXPLAIN 阶段已兜底，这里只降级不判错
            is_precise = False

    alias_map = _build_alias_map(ast)
    slots: SlotState = {
        "metrics": _extract_metrics(ast, alias_map),
        "dimensions": _extract_dimensions(ast, alias_map),
        "filters": _extract_filters(ast, alias_map),
    }
    return slots, is_precise
