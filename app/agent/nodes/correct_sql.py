"""
SQL 修正节点

负责在 SQL 校验失败后，结合原问题、原 SQL、结构化验证反馈和完整上下文做最小必要修正。
语法错误和语义槽位缺失都会进入本节点，但修正策略由 validation_phase 区分。
"""

import yaml
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt


async def correct_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """根据结构化校验反馈修正 SQL"""

    writer = runtime.stream_writer
    step = "校正SQL"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        # 校正 SQL 仍然需要完整上下文，避免模型只根据报错修语法却改丢业务语义
        table_infos = state["table_infos"]
        metric_infos = state["metric_infos"]
        date_info = state["date_info"]
        db_info = state["db_info"]
        query = state["query"]

        # sql 是待修正的候选 SQL，validation_feedback 是最近一次校验的结构化反馈
        sql = state["sql"]
        validation_phase = state.get("validation_phase", "none")
        validation_feedback = state.get("validation_issues", [])

        prompt = PromptTemplate(
            template=load_prompt("correct_sql"),
            input_variables=[
                "table_infos",
                "metric_infos",
                "date_info",
                "db_info",
                "query",
                "sql",
                "validation_phase",
                "validation_feedback",
            ],
        )
        # 修正后的输出仍然是一条纯 SQL 文本，用来覆盖 state["sql"]
        output_parser = StrOutputParser()
        chain = prompt | llm | output_parser

        result = await chain.ainvoke(
            {
                # 与生成节点保持一致，用 YAML 向模型提供稳定 可读的结构化上下文
                "table_infos": yaml.dump(
                    table_infos, allow_unicode=True, sort_keys=False
                ),
                "metric_infos": yaml.dump(
                    metric_infos, allow_unicode=True, sort_keys=False
                ),
                "date_info": yaml.dump(date_info, allow_unicode=True, sort_keys=False),
                "db_info": yaml.dump(db_info, allow_unicode=True, sort_keys=False),
                "query": query,
                "sql": sql,
                "validation_phase": validation_phase,
                "validation_feedback": yaml.dump(
                    validation_feedback, allow_unicode=True, sort_keys=False
                ),
            }
        )

        logger.info(f"校正后的SQL：{result}")
        writer({"type": "progress", "step": step, "status": "success"})
        # 分阶段自增修正计数，供 graph 条件边分别控制语法和语义重试上限
        updates = {
            "sql": result,
            "correction_count": state.get("correction_count", 0) + 1,
        }
        if validation_phase == "syntax":
            updates["syntax_correction_count"] = (
                state.get("syntax_correction_count", 0) + 1
            )
        elif validation_phase == "semantic":
            updates["semantic_correction_count"] = (
                state.get("semantic_correction_count", 0) + 1
            )
        return updates
    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise
