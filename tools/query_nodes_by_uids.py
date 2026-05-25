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


class QueryNodesByUidsTool(Tool):
    _QUERY = """
MATCH (n:KnowledgeNode)
WHERE n.uid IN $uid_list
  AND ($group_id = '' OR n.group_id = $group_id)
RETURN n
ORDER BY n.name ASC, n.uid ASC
LIMIT $limit
"""

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        raw_uids = clean_text(tool_parameters.get("uids"))
        group_id = normalize_group_id(tool_parameters.get("group_id"))
        database = clean_text(tool_parameters.get("database"))
        logger.info("QueryNodesByUidsTool invoked | uids=%s group_id=%s", raw_uids, group_id)
        if not raw_uids:
            yield self.create_text_message("❌ uids 不能为空。")
            return

        uid_list = [u.strip() for u in raw_uids.split(",") if u.strip()]
        if not uid_list:
            yield self.create_text_message("❌ uids 解析后为空。")
            return

        try:
            limit = parse_limit(tool_parameters.get("limit"), default=100, max_value=500)
            limit = max(limit, len(uid_list))
            rows = run_template_query(
                self.runtime,
                query=self._QUERY,
                parameters={"uid_list": uid_list, "group_id": group_id, "limit": limit},
                database=database,
                limit=limit,
            )
        except Exception as exc:
            yield self.create_text_message(f"❌ 查询失败：{exc}")
            return

        found_uids = set()
        for row in rows:
            uid = clean_text(as_mapping(row.get("n")).get("uid"))
            if uid:
                found_uids.add(uid)

        missing_uids = [u for u in uid_list if u not in found_uids]
        nodes = strip_embedding_fields(rows)
        summary = f"批量查询完成，请求 {len(uid_list)} 个 uid，命中 {len(rows)} 条，未命中 {len(missing_uids)} 个。"
        request_echo = {
            "uids": uid_list,
            "group_id": group_id,
            "database": database,
            "limit": limit,
        }

        yield self.create_variable_message("count", len(rows))
        yield self.create_variable_message("nodes", nodes)
        yield self.create_variable_message("missing_uids", missing_uids)
        yield self.create_variable_message("summary", summary)
        yield self.create_variable_message("request", request_echo)
        yield self.create_json_message({
            "count": len(rows),
            "nodes": nodes,
            "missing_uids": missing_uids,
            "summary": summary,
            "request": request_echo,
        })
        yield self.create_text_message(f"✅ {summary}")
