"""SQL 校验闭环的条件路由规则"""

from app.agent.state import DataAgentState

# 语法和语义修正分别计数，避免语法重试消耗语义重试额度
MAX_SYNTAX_CORRECTION = 2
MAX_SEMANTIC_CORRECTION = 2


def route_after_validate_sql(state: DataAgentState) -> str:
    """语法校验后决定进入语义校验、修正或最终错误"""

    if state.get("severity") != "error":
        return "validate_sql_slot"

    if state.get("validation_phase") == "syntax":
        if state.get("syntax_correction_count", 0) < MAX_SYNTAX_CORRECTION:
            return "correct_sql"
        return "final_error"

    return "final_error"


def route_after_validate_sql_slot(state: DataAgentState) -> str:
    """语义槽位校验后决定执行、修正或最终错误"""

    if state.get("severity") != "error":
        return "run_sql"

    if state.get("validation_phase") == "semantic":
        if state.get("semantic_correction_count", 0) < MAX_SEMANTIC_CORRECTION:
            return "correct_sql"
        return "final_error"

    return "final_error"
