from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin.config.logger_format import plugin_logger_handler

from core.graph_query_common import as_mapping, normalize_group_id, parse_limit, run_template_query, strip_embedding_fields
from core.types import clean_text

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)


class QueryNodesByNidsTool(Tool):
    _QUERY = """
MATCH (n:KnowledgeNode)
WHERE n.nid IN $nid_list
  AND ($group_id = '' OR n.group_id = $group_id)
RETURN n
ORDER BY n.name ASC, n.nid ASC
LIMIT $limit
"""

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        raw_nids = clean_text(tool_parameters.get("nids"))
        group_id = normalize_group_id(tool_parameters.get("group_id")) or clean_text(self.runtime.credentials.get("group_id"))
        database = clean_text(tool_parameters.get("database"))
        logger.info("QueryNodesByNidsTool invoked | nids=%s group_id=%s", raw_nids, group_id)
        if not raw_nids:
            yield self.create_text_message("❌ nids 不能为空。")
            return

        nid_list = [u.strip() for u in raw_nids.split(",") if u.strip()]
        if not nid_list:
            yield self.create_text_message("❌ nids 解析后为空。")
            return

        try:
            limit = parse_limit(tool_parameters.get("limit"), default=100, max_value=500)
            limit = max(limit, len(nid_list))
            rows = run_template_query(
                self.runtime,
                query=self._QUERY,
                parameters={"nid_list": nid_list, "group_id": group_id, "limit": limit},
                database=database,
                limit=limit,
            )
        except Exception as exc:
            yield self.create_text_message(f"❌ 查询失败：{exc}")
            return

        found_nids = set()
        for row in rows:
            nid = clean_text(as_mapping(row.get("n")).get("nid"))
            if nid:
                found_nids.add(nid)

        missing_nids = [u for u in nid_list if u not in found_nids]
        nodes = strip_embedding_fields(rows)
        summary = f"批量查询完成，请求 {len(nid_list)} 个 nid，命中 {len(rows)} 条，未命中 {len(missing_nids)} 个。"
        request_echo = {
            "nids": nid_list,
            "group_id": group_id,
            "database": database,
            "limit": limit,
        }

        yield self.create_variable_message("count", len(rows))
        yield self.create_variable_message("nodes", nodes)
        yield self.create_variable_message("missing_nids", missing_nids)
        yield self.create_variable_message("summary", summary)
        yield self.create_variable_message("request", request_echo)
        yield self.create_json_message({
            "count": len(rows),
            "nodes": nodes,
            "missing_nids": missing_nids,
            "summary": summary,
            "request": request_echo,
        })
        yield self.create_text_message(f"✅ {summary}")
