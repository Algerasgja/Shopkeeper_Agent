import asyncio
import unittest

from app.agent.nodes.final_error import final_error
from app.agent.nodes.validate_sql import validate_sql
from app.agent.nodes.validate_sql_slot import validate_sql_slot
from app.agent.validation_routes import (
    MAX_SEMANTIC_CORRECTION,
    MAX_SYNTAX_CORRECTION,
    route_after_validate_sql,
    route_after_validate_sql_slot,
)


class RuntimeStub:
    def __init__(self, context=None):
        self.context = context or {}
        self.events = []
        self.stream_writer = self.events.append


class DWRepositoryStub:
    def __init__(self, error=None):
        self.error = error

    async def validate(self, sql):
        if self.error:
            raise RuntimeError(self.error)


TABLE_INFOS = [
    {
        "name": "fact_order",
        "role": "fact",
        "description": "订单事实表",
        "columns": [
            {"name": "order_id", "type": "varchar"},
            {"name": "region_id", "type": "varchar"},
            {"name": "order_amount", "type": "float"},
        ],
    },
    {
        "name": "dim_region",
        "role": "dim",
        "description": "区域维表",
        "columns": [
            {"name": "region_id", "type": "varchar"},
            {"name": "region_name", "type": "varchar"},
        ],
    },
]

QUERY_SLOTS = {
    "metrics": [{"agg": "SUM", "column": "fact_order.order_amount"}],
    "dimensions": [],
    "filters": [{"column": "dim_region.region_name", "op": "=", "value": "华北"}],
}


class ValidationLoopTest(unittest.TestCase):
    def test_validate_sql_success_clears_validation_status(self):
        runtime = RuntimeStub({"dw_mysql_repository": DWRepositoryStub()})

        result = asyncio.run(validate_sql({"sql": "select 1"}, runtime))

        self.assertIsNone(result["error"])
        self.assertEqual(result["validation_phase"], "none")
        self.assertEqual(result["severity"], "none")
        self.assertEqual(result["validation_issues"], [])

    def test_validate_sql_error_returns_syntax_issue(self):
        runtime = RuntimeStub(
            {"dw_mysql_repository": DWRepositoryStub("Unknown column 'bad_col'")}
        )

        result = asyncio.run(validate_sql({"sql": "select bad_col"}, runtime))

        self.assertEqual(result["validation_phase"], "syntax")
        self.assertEqual(result["severity"], "error")
        self.assertEqual(result["validation_issues"][0]["phase"], "syntax")
        self.assertEqual(result["validation_issues"][0]["code"], "SQL_EXPLAIN_FAILED")

    def test_route_after_validate_sql_uses_syntax_retry_limit(self):
        retry_state = {
            "validation_phase": "syntax",
            "severity": "error",
            "syntax_correction_count": MAX_SYNTAX_CORRECTION - 1,
        }
        exhausted_state = {
            "validation_phase": "syntax",
            "severity": "error",
            "syntax_correction_count": MAX_SYNTAX_CORRECTION,
        }

        self.assertEqual(route_after_validate_sql({"severity": "none"}), "validate_sql_slot")
        self.assertEqual(route_after_validate_sql(retry_state), "correct_sql")
        self.assertEqual(route_after_validate_sql(exhausted_state), "final_error")

    def test_validate_sql_slot_error_returns_semantic_issue(self):
        runtime = RuntimeStub()
        state = {
            "sql": (
                "select sum(o.order_amount) as sales_amount "
                "from fact_order o "
                "join dim_region r on o.region_id = r.region_id"
            ),
            "table_infos": TABLE_INFOS,
            "db_info": {"dialect": "mysql", "version": "8.0"},
            "query_slots": QUERY_SLOTS,
        }

        result = asyncio.run(validate_sql_slot(state, runtime))

        self.assertEqual(result["validation_phase"], "semantic")
        self.assertEqual(result["severity"], "error")
        self.assertEqual(result["validation_issues"][0]["code"], "SLOT_MISMATCH")
        self.assertIn("dim_region.region_name", result["error"])

    def test_validate_sql_slot_parse_failure_returns_warning(self):
        runtime = RuntimeStub()
        state = {
            "sql": "select * from",
            "table_infos": TABLE_INFOS,
            "db_info": {"dialect": "mysql", "version": "8.0"},
            "query_slots": QUERY_SLOTS,
        }

        result = asyncio.run(validate_sql_slot(state, runtime))

        self.assertIsNone(result["error"])
        self.assertEqual(result["validation_phase"], "parse")
        self.assertEqual(result["severity"], "warning")
        self.assertEqual(result["validation_issues"][0]["code"], "SQL_SLOT_PARSE_FAILED")

    def test_route_after_validate_sql_slot_uses_semantic_retry_limit(self):
        retry_state = {
            "validation_phase": "semantic",
            "severity": "error",
            "semantic_correction_count": MAX_SEMANTIC_CORRECTION - 1,
        }
        exhausted_state = {
            "validation_phase": "semantic",
            "severity": "error",
            "semantic_correction_count": MAX_SEMANTIC_CORRECTION,
        }

        self.assertEqual(route_after_validate_sql_slot({"severity": "none"}), "run_sql")
        self.assertEqual(route_after_validate_sql_slot(retry_state), "correct_sql")
        self.assertEqual(route_after_validate_sql_slot(exhausted_state), "final_error")

    def test_final_error_returns_last_sql_and_validation_feedback(self):
        runtime = RuntimeStub()
        state = {
            "sql": "select bad_col from fact_order",
            "validation_phase": "syntax",
            "validation_issues": [
                {
                    "phase": "syntax",
                    "severity": "error",
                    "code": "SQL_EXPLAIN_FAILED",
                    "message": "Unknown column 'bad_col'",
                }
            ],
            "syntax_correction_count": MAX_SYNTAX_CORRECTION,
        }

        asyncio.run(final_error(state, runtime))

        final_events = [event for event in runtime.events if event["type"] == "final_error"]
        self.assertEqual(len(final_events), 1)
        self.assertEqual(final_events[0]["sql"], "select bad_col from fact_order")
        self.assertEqual(final_events[0]["validation_phase"], "syntax")
        self.assertEqual(
            final_events[0]["validation_issues"][0]["code"], "SQL_EXPLAIN_FAILED"
        )


if __name__ == "__main__":
    unittest.main()
