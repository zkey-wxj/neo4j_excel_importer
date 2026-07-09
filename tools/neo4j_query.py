from __future__ import annotations

import logging
import re
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin.config.logger_format import plugin_logger_handler

from core.graph_query_common import parse_bool, parse_json_object, parse_limit, run_cypher_query, strip_embedding_fields
from core.types import clean_text

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)


# 只读模式（allow_write=False）下兜底拦截的写/删除类操作：标准 Cypher 删除关键字 + 可绕过 EXPLAIN query_type 的 apoc 写/重构过程。
# 作为 query_type 白名单之外的补充防线，仅在关闭写入时生效；显式开启 allow_write_queries 时不拦截。
_FORBIDDEN_QUERY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:DELETE|DETACH|DROP|REMOVE)\b", re.IGNORECASE), "DELETE/DETACH/DROP/REMOVE 等删除或破坏性关键字"),
    (re.compile(r"\bapoc\.(?:refactor|create|periodic)\.", re.IGNORECASE), "apoc.refactor/create/periodic 写入或重构过程"),
    (re.compile(r"\bapoc\.cypher\.run\b", re.IGNORECASE), "apoc.cypher.run 动态执行"),
]
_LITERAL_RE = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`[^`]*`")


def _detect_forbidden_query(query: str) -> str | None:
    """检测 query 是否含禁止的删除/写入操作，命中返回说明，无命中返回 None。
    先剥离字符串字面量与反引号标识符，避免属性值/别名含关键字的误判。"""
    stripped = _LITERAL_RE.sub(" ", query)
    for pattern, label in _FORBIDDEN_QUERY_PATTERNS:
        if pattern.search(stripped):
            return label
    return None

class Neo4jQueryTool(Tool):
    """执行 Cypher 查询，默认只读，可按参数显式开启写入。"""

    _MAX_QUERY_LENGTH = 4000

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        query = clean_text(tool_parameters.get("query"))
        database = clean_text(tool_parameters.get("database"))

        logger.info("Neo4jQueryTool invoked | query_len=%d database=%s", len(query), database)
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

            # 仅在关闭写入时兜底拦截删除/破坏性操作（query_type 检测对 apoc 等场景可能漏判）
            if not allow_write:
                forbidden = _detect_forbidden_query(query)
                if forbidden:
                    yield self.create_text_message(f"❌ 本工具仅用于查询，禁止 {forbidden}。")
                    return

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
        request_echo = {
            "database": database,
            "max_records": max_records,
            "allow_write_queries": allow_write,
            "parameters": params,
        }

        yield self.create_variable_message("count", len(rows))
        yield self.create_variable_message("results", sanitized_rows)
        yield self.create_variable_message("summary", summary)
        yield self.create_variable_message("request", request_echo)
        yield self.create_json_message({
            "count": len(rows),
            "results": sanitized_rows,
            "summary": summary,
            "request": request_echo,
        })
        yield self.create_text_message(f"✅ {summary}")
