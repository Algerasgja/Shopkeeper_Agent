"""
用户问题语义槽位抽取节点

负责让大模型把用户问题拆解成结构化语义槽位（指标 维度 过滤），
并借助 table_infos 的 alias 把业务说法映射成物理 表名.字段名。
抽取结果写入 state["query_slots"]，供后续 validate_sql_slot 与 SQL 槽位比对。

本节点和 filter_table 一样：模型只负责抽取，落库结构由程序兜底，
即使模型漏键或返回异常，也保证 query_slots 始终是完整的 SlotState 结构。
"""

import yaml
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.slots.slot_schema import SlotState
from app.agent.state import DataAgentState
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt


def _normalize_slots(raw: dict) -> SlotState:
    """把模型输出兜底成结构完整的 SlotState，缺键或类型异常时退化为空槽位"""

    if not isinstance(raw, dict):
        raw = {}
    metrics = raw.get("metrics")
    dimensions = raw.get("dimensions")
    filters = raw.get("filters")
    return SlotState(
        metrics=metrics if isinstance(metrics, list) else [],
        dimensions=dimensions if isinstance(dimensions, list) else [],
        filters=filters if isinstance(filters, list) else [],
    )


async def extract_query_slots(
    state: DataAgentState, runtime: Runtime[DataAgentContext]
):
    """从用户问题抽取语义槽位，供后续 SQL 语义一致性校验使用"""

    writer = runtime.stream_writer
    step = "抽取问题语义槽位"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]
        table_infos = state["table_infos"]
        metric_infos = state["metric_infos"]

        prompt = PromptTemplate(
            template=load_prompt("extract_query_slots"),
            input_variables=["query", "table_infos", "metric_infos"],
        )
        # 抽取结果是结构化 JSON 对象，沿用项目里其它节点的 JSON 解析方式
        output_parser = JsonOutputParser()
        chain = prompt | llm | output_parser

        result = await chain.ainvoke(
            {
                "query": query,
                # 与生成/过滤节点保持一致，用 YAML 向模型提供稳定可读的结构化上下文
                "table_infos": yaml.dump(
                    table_infos, allow_unicode=True, sort_keys=False
                ),
                "metric_infos": yaml.dump(
                    metric_infos, allow_unicode=True, sort_keys=False
                ),
            }
        )

        query_slots = _normalize_slots(result)
        logger.info(f"抽取的问题语义槽位：{query_slots}")
        writer({"type": "progress", "step": step, "status": "success"})
        return {"query_slots": query_slots}

    except Exception as e:
        # 抽取失败不阻断主流程：返回空槽位，后续校验自然放行（漏报优于误杀）
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "success"})
        return {
            "query_slots": SlotState(metrics=[], dimensions=[], filters=[])
        }
