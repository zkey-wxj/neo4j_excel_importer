from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from neo4j import GraphDatabase
from dify_plugin.config.logger_format import plugin_logger_handler

from core.embedding_common import generate_embeddings, has_embedding_model
from core.graph_query_common import normalize_group_id, parse_limit, run_read_query
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
    _VECTOR_INDEX_NAME = "knowledge_node_embedding_idx"

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
            if has_embedding_model(embedding_model):
                try:
                    rows = self._run_vector_query(
                        embedding_model=embedding_model,
                        keyword=keyword,
                        group_id=group_id,
                        database=database,
                        limit=limit,
                    )
                except Exception as exc:
                    logger.warning("vector query failed, fallback to text query: %s", exc)
                    rows = []
            if not rows:
                rows = run_read_query(
                    self.runtime,
                    query=self._QUERY,
                    parameters={"keyword": keyword, "group_id": group_id, "limit": limit},
                    database=database,
                    limit=limit,
                )
        except Exception as exc:
            yield self.create_text_message(f"❌ 查询失败：{exc}")
            return

        summary = f"模糊查询完成，关键字“{keyword}”，命中 {len(rows)} 条。"
        yield self.create_variable_message("count", len(rows))
        yield self.create_variable_message("results", rows)
        yield self.create_variable_message("summary", summary)
        yield self.create_json_message({"count": len(rows), "results": rows, "summary": summary})
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
        uri = clean_text(self.runtime.credentials.get("neo4j_uri"))
        user = clean_text(self.runtime.credentials.get("neo4j_user"))
        pwd = clean_text(self.runtime.credentials.get("neo4j_password"))
        if not uri or not user or not pwd:
            raise ValueError("Neo4j 凭据不完整，请检查 neo4j_uri / neo4j_user / neo4j_password。")

        vectors = generate_embeddings(
            self.session,
            model_config=embedding_model,
            texts=[keyword],
        )
        if not vectors:
            return []
        query_vector = vectors[0]

        driver = GraphDatabase.driver(
            uri,
            auth=(user, pwd),
            connection_timeout=30.0,
            max_connection_lifetime=3600,
            user_agent="dify-neo4j-plugin/1.0",
        )
        try:
            session_kwargs: dict[str, Any] = {"fetch_size": min(max(limit, 1), 1000)}
            if database:
                session_kwargs["database"] = database
            with driver.session(**session_kwargs) as neo_session:
                explain_result = neo_session.run(
                    f"EXPLAIN {self._VECTOR_QUERY}",
                    {
                        "index_name": self._VECTOR_INDEX_NAME,
                        "limit": limit,
                        "query_vector": query_vector,
                        "group_id": group_id,
                    },
                )
                query_type = explain_result.consume().query_type
                if query_type not in {"r", "s"}:
                    raise ValueError(f"仅允许只读查询，当前 query_type={query_type}。")
                result = neo_session.run(
                    self._VECTOR_QUERY,
                    {
                        "index_name": self._VECTOR_INDEX_NAME,
                        "limit": limit,
                        "query_vector": query_vector,
                        "group_id": group_id,
                    },
                )
                return [record.data() for record in result]
        finally:
            driver.close()
