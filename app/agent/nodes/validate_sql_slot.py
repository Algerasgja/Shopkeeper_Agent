"""
SQL 语义槽位校验节点

在语法校验（EXPLAIN）之后追加一层语义校验：用 sqlglot 从最终 SQL 解析槽位，
和大模型抽取的 query 槽位做 query ⊆ sql 比对，确保用户明确要求的指标 维度
和过滤列都真实出现在 SQL 中，弥补 EXPLAIN 只验语法和存在性的盲区。

和 validate_sql 一样：本节点不决定流程走向，只把硬性不一致写入结构化校验状态，
交给 graph.py 的条件边判断进入修正还是执行。值/算子差异只产告警，不拦截。
"""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.slots.slot_comparator import (
    compare_slots,
    format_feedback,
    has_hard_violation,
)
from app.agent.slots.sql_slot_extractor import extract_sql_slots
from app.agent.state import DataAgentState
from app.core.log import logger


async def validate_sql_slot(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """校验最终 SQL 是否覆盖用户问题的语义槽位"""

    writer = runtime.stream_writer
    step = "语义槽位校验"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        sql = state["sql"]
        table_infos = state["table_infos"]
        dialect = state["db_info"]["dialect"]
        query_slots = state["query_slots"]

        # sqlglot 解析失败属于工具自身异常，记录为 parse warning，不触发修正
        try:
            sql_slots, is_precise = extract_sql_slots(sql, table_infos, dialect)
        except Exception as e:
            message = f"解析 SQL 槽位失败，跳过语义校验: {e}"
            logger.error(f"{step} {message}")
            writer({"type": "warning", "step": step, "message": message})
            writer({"type": "progress", "step": step, "status": "success"})
            return {
                "error": None,
                "validation_phase": "parse",
                "validation_issues": [
                    {
                        "phase": "parse",
                        "severity": "warning",
                        "code": "SQL_SLOT_PARSE_FAILED",
                        "message": message,
                    }
                ],
                "severity": "warning",
            }

        diff = compare_slots(query_slots, sql_slots, is_precise)
        validation_issues = []

        # 值/算子差异是软信号，单独以 warning 推给前端，不进入 error 通道
        for warning in diff["value_warnings"]:
            writer({"type": "warning", "step": step, "message": warning})
            validation_issues.append(
                {
                    "phase": "semantic",
                    "severity": "warning",
                    "code": "FILTER_VALUE_MISMATCH",
                    "message": warning,
                }
            )

        if has_hard_violation(diff):
            feedback = format_feedback(diff)
            logger.info(f"{feedback}（is_precise={is_precise}）")
            writer({"type": "progress", "step": step, "status": "success"})
            # 把缺失槽位作为精确反馈写入结构化状态，供 correct_sql 定向修正
            return {
                "sql_slots": sql_slots,
                "error": feedback,
                "validation_phase": "semantic",
                "validation_issues": [
                    {
                        "phase": "semantic",
                        "severity": "error",
                        "code": "SLOT_MISMATCH",
                        "message": feedback,
                    },
                    *validation_issues,
                ],
                "severity": "error",
            }

        logger.info("SQL 语义槽位校验通过")
        writer({"type": "progress", "step": step, "status": "success"})
        return {
            "sql_slots": sql_slots,
            "error": None,
            "validation_phase": "semantic" if validation_issues else "none",
            "validation_issues": validation_issues,
            "severity": "warning" if validation_issues else "none",
        }

    except Exception as e:
        # 校验节点自身异常同样按 parse warning 处理，避免工具异常误杀已通过语法校验的 SQL
        message = f"{step} failed: {e}"
        logger.error(message)
        writer({"type": "warning", "step": step, "message": message})
        writer({"type": "progress", "step": step, "status": "success"})
        return {
            "error": None,
            "validation_phase": "parse",
            "validation_issues": [
                {
                    "phase": "parse",
                    "severity": "warning",
                    "code": "SQL_SLOT_VALIDATION_FAILED",
                    "message": message,
                }
            ],
            "severity": "warning",
        }
