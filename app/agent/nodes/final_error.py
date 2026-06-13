"""
最终错误节点

当 SQL 语法或语义修正达到上限后，不再执行已知有问题的 SQL，
只把最后生成的 SQL 和最后一次结构化校验反馈返回给前端参考。
"""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


async def final_error(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """输出最终错误信息，并终止 SQL 执行链路"""

    writer = runtime.stream_writer
    step = "最终错误"
    writer({"type": "progress", "step": step, "status": "running"})

    payload = {
        "type": "final_error",
        "sql": state.get("sql"),
        "validation_phase": state.get("validation_phase"),
        "validation_issues": state.get("validation_issues", []),
        "syntax_correction_count": state.get("syntax_correction_count", 0),
        "semantic_correction_count": state.get("semantic_correction_count", 0),
    }
    logger.info(f"SQL 修正达到上限，返回最终错误：{payload}")
    writer({"type": "progress", "step": step, "status": "success"})
    writer(payload)
    return {}
