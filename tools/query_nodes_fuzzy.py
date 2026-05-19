from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin.config.logger_format import plugin_logger_handler

from core.constants import VECTOR_INDEX_NAME
from core.embedding_common import generate_embeddings, has_embedding_model
from core.graph_query_common import normalize_group_id, parse_limit, run_cypher_query, strip_embedding_fields
from core.types import clean_text
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)


class QueryNodesFuzzyTool(Tool):
    _QUERY = """
MATCH (n:KnowledgeNode)
WHERE (
    toLower(coalesce(n.name, '')) CONTAINS toLower($keyword)
    OR toLower(coalesce(n.description, '')) CONTAINS toLower($keyword)
    OR toLower(coalesce(n.uid, '')) CONTAINS toLower($keyword)
)
AND ($group_id = '' OR coalesce(n.group_id, '') = $group_id)
RETURN n
ORDER BY coalesce(n.name, n.uid) ASC
LIMIT $limit
"""
    _VECTOR_QUERY = """
CALL db.index.vector.queryNodes($index_name, $limit, $query_vector)
YIELD node, score
WHERE ($group_id = '' OR coalesce(node.group_id, '') = $group_id)
RETURN node AS n
ORDER BY score DESC
LIMIT $limit
"""
    _VECTOR_INDEX_NAME = VECTOR_INDEX_NAME

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        keyword = clean_text(tool_parameters.get("keyword"))
        embedding_model = tool_parameters.get("embedding_model")
        database = clean_text(tool_parameters.get("database"))
        group_id = normalize_group_id(tool_parameters.get("group_id"))
        if not keyword:
            yield self.create_text_message("❌ keyword 不能为空。")
            return

        try:
            limit = parse_limit(tool_parameters.get("limit"), default=20, max_value=100)
            rows: list[dict[str, Any]] = []
            query_mode = "text"
            if has_embedding_model(embedding_model):
                try:
                    rows = self._run_vector_query(
                        embedding_model=embedding_model,
                        keyword=keyword,
                        group_id=group_id,
                        database=database,
                        limit=limit,
                    )
                    if rows:
                        query_mode = "vector"
                except Exception as exc:
                    logger.warning("vector query failed, fallback to text query: %s", exc)
                    rows = []
            if not rows:
                rows = run_cypher_query(
                    self.runtime,
                    query=self._QUERY,
                    parameters={"keyword": keyword, "group_id": group_id, "limit": limit},
                    database=database,
                    limit=limit,
                    allow_write=False,
                )
        except Exception as exc:
            yield self.create_text_message(f"❌ 查询失败：{exc}")
            return

        sanitized_rows = strip_embedding_fields(rows)
        summary = f"模糊查询完成，关键字“{keyword}”，命中 {len(rows)} 条。"
        payload = {
            "count": len(rows),
            "results": sanitized_rows,
            "summary": summary,
            "query_mode": query_mode,
            "request": {
                "keyword": keyword,
                "group_id": group_id,
                "database": database,
                "limit": limit,
            },
        }
        yield self.create_variable_message("count", len(rows))
        yield self.create_variable_message("results", sanitized_rows)
        yield self.create_variable_message("summary", summary)
        yield self.create_variable_message("query_mode", query_mode)
        yield self.create_json_message(payload)
        yield self.create_text_message(f"✅ {summary}")

    def _run_vector_query(
        self,
        *,
        embedding_model: Any,
        keyword: str,
        group_id: str,
        database: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        vectors = generate_embeddings(
            self.session,
            model_config=embedding_model,
            texts=[keyword],
        )
        if not vectors:
            return []
        query_vector = vectors[0]

        return run_cypher_query(
            self.runtime,
            query=self._VECTOR_QUERY,
            parameters={
                "index_name": self._VECTOR_INDEX_NAME,
                "limit": limit,
                "query_vector": query_vector,
                "group_id": group_id,
            },
            database=database,
            limit=limit,
            allow_write=False,
        )
