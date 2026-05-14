from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from core.graph_query_common import parse_bool, parse_json_object, parse_limit, run_cypher_query, strip_embedding_fields
from core.types import clean_text


class Neo4jQueryTool(Tool):
    """执行 Cypher 查询，默认只读，可按参数显式开启写入。"""

    _MAX_QUERY_LENGTH = 4000

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        query = clean_text(tool_parameters.get("query"))
        database = clean_text(tool_parameters.get("database"))

        if not query:
            yield self.create_text_message("❌ query 不能为空。")
            return
        if len(query) > self._MAX_QUERY_LENGTH:
            yield self.create_text_message(f"❌ query 过长，最大允许 {self._MAX_QUERY_LENGTH} 个字符。")
            return

        try:
            params = parse_json_object(tool_parameters.get("parameters"), field_name="parameters")
            max_records = parse_limit(tool_parameters.get("max_records"), default=1000, max_value=2000)
            allow_write = parse_bool(
                tool_parameters.get("allow_write_queries"),
                default=False,
                field_name="allow_write_queries",
            )

            rows = run_cypher_query(
                self.runtime,
                query=query,
                parameters=params,
                database=database,
                limit=max_records,
                allow_write=allow_write,
            )
        except Exception as exc:
            yield self.create_text_message(f"❌ Neo4j 查询失败：{exc}")
            return

        sanitized_rows = strip_embedding_fields(rows)
        summary = f"Neo4j 查询完成，返回 {len(rows)} 条记录。"
        payload = {
            "count": len(rows),
            "results": sanitized_rows,
            "summary": summary,
            "request": {
                "database": database,
                "max_records": max_records,
                "allow_write_queries": allow_write,
                "parameters": params,
            },
        }

        yield self.create_variable_message("count", len(rows))
        yield self.create_variable_message("results", sanitized_rows)
        yield self.create_variable_message("summary", summary)
        yield self.create_json_message(payload)
        yield self.create_text_message(f"✅ {summary}")
