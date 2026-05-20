"""extract_graph_data.py — Dify Tool
从 Markdown 提取图谱结构数据，仅解析不写入 Neo4j。
"""
from __future__ import annotations

import logging
from collections.abc import Generator, Mapping
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin.config.logger_format import plugin_logger_handler

from core.graph_parser import GraphParser
from core.types import clean_text, ensure_mapping, utc_now_iso

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)

_STANDARD_MAPPING: dict[str, Any] = {
    "node": {
        "uid": "uid", "name": "name", "labels": ["labels"],
        "description": ["description"], "properties": ["grade_range", "*"],
    },
    "relation": {
        "source_uid": "source_uid", "rel_type": "rel_type", "target_uid": "target_uid",
        "description": ["description"], "properties": ["*"],
    },
}


def _node_to_dict(raw: dict[str, Any], group_id: str) -> dict[str, Any]:
    return {
        "uid": clean_text(raw.get("uid")),
        "name": clean_text(raw.get("name")),
        "labels": [clean_text(l) for l in (raw.get("labels") or []) if clean_text(l)] or ["Node"],
        "description": clean_text(raw.get("description")),
        "group_id": clean_text(raw.get("group_id")) or group_id,
        "properties": raw.get("properties") if isinstance(raw.get("properties"), dict) else {},
        "meta": raw.get("meta") if isinstance(raw.get("meta"), dict) else {},
    }


def _relation_to_dict(raw: dict[str, Any], group_id: str) -> dict[str, Any]:
    return {
        "source_uid": clean_text(raw.get("source_uid")),
        "target_uid": clean_text(raw.get("target_uid")),
        "rel_type": clean_text(raw.get("rel_type")),
        "direction": clean_text(raw.get("direction")) or "forward",
        "description": clean_text(raw.get("description")),
        "group_id": clean_text(raw.get("group_id")) or group_id,
        "properties": raw.get("properties") if isinstance(raw.get("properties"), dict) else {},
        "meta": raw.get("meta") if isinstance(raw.get("meta"), dict) else {},
    }


class ExtractGraphDataTool(GraphParser, Tool):
    """从 Markdown 提取图谱结构数据，仅解析不写入 Neo4j。"""

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        params: Mapping[str, Any] = ensure_mapping(tool_parameters, field_name="tool_parameters")

        text = str(params.get("text") or "").strip()
        mapping = self.resolve_mapping(params.get("mapping"), default=_STANDARD_MAPPING)
        input_group_id = str(params.get("group_id") or "").strip()
        group_id = input_group_id or utc_now_iso().replace("-", "").replace(":", "")[:14]

        logger.info("ExtractGraphDataTool invoked | text_len=%d group_id=%s", len(text), group_id)

        if not text:
            yield self.create_text_message("text cannot be empty.")
            return

        try:
            raw_nodes, raw_relations = self.parse_markdown_tables(text, mapping)
        except Exception as exc:
            logger.error("Parse failed: %s", exc)
            yield self.create_text_message(f"Parse failed: {exc}")
            return

        nodes = [_node_to_dict(n, group_id) for n in raw_nodes if clean_text(n.get("uid"))]
        relations = [
            _relation_to_dict(r, group_id) for r in raw_relations
            if clean_text(r.get("source_uid")) and clean_text(r.get("target_uid")) and clean_text(r.get("rel_type"))
        ]

        known_ids = {n["uid"] for n in nodes}
        skipped_rels = sum(1 for r in raw_relations if clean_text(r.get("source_uid")) not in known_ids or clean_text(r.get("target_uid")) not in known_ids)

        node_type_stats: dict[str, int] = {}
        for n in nodes:
            for label in n.get("labels", []):
                if label:
                    node_type_stats[label] = node_type_stats.get(label, 0) + 1
        rel_type_stats: dict[str, int] = {}
        for r in relations:
            rt = r.get("rel_type", "")
            if rt:
                rel_type_stats[rt] = rel_type_stats.get(rt, 0) + 1

        summary = f"Extracted {len(nodes)} nodes, {len(relations)} relations, skipped {skipped_rels} (missing nodes)."

        yield self.create_variable_message("group_id", group_id)
        yield self.create_variable_message("nodes_count", len(nodes))
        yield self.create_variable_message("relations_count", len(relations))
        yield self.create_variable_message("skipped_rels", skipped_rels)
        yield self.create_variable_message("node_type_stats", node_type_stats)
        yield self.create_variable_message("rel_type_stats", rel_type_stats)
        yield self.create_variable_message("nodes", nodes)
        yield self.create_variable_message("relations", relations)
        yield self.create_variable_message("summary", summary)
        yield self.create_json_message({
            "group_id": group_id, "nodes_count": len(nodes), "relations_count": len(relations),
            "skipped_rels": skipped_rels, "node_type_stats": node_type_stats, "rel_type_stats": rel_type_stats,
            "nodes": nodes, "relations": relations, "summary": summary,
        })
        yield self.create_text_message(f"OK {summary}")
