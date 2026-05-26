"""import_graph.py — Dify Tool
将 Excel / Markdown 图谱表格导入 Neo4j 知识图谱。
"""
from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Generator, Mapping
from typing import Any, cast

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin.config.logger_format import plugin_logger_handler

from core.embedding_common import build_node_embedding_text, generate_embeddings
from core.graph_parser import GraphParser, _build_meta, _normalize_relation_type
from core.graph_write_common import clear_graph, get_apoc_capabilities, write_nodes, write_relations
from core.constants import DEFAULT_DIRECTION, DEFAULT_NODE_LABEL
from core.types import GraphMeta, NodePayload, RelationPayload, ensure_mapping, normalize_labels, normalize_properties, clean_text

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)


class ImportGraphTool(GraphParser, Tool):
    """将 Excel 图谱文件写入 Neo4j。"""

    _PROGRESS_VARIABLE = "summary"
    _GROUP_ID_STORAGE_KEY = "import_graph:last_group_id"

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        params: Mapping[str, Any] = ensure_mapping(tool_parameters, field_name="tool_parameters")
        credentials: Mapping[str, Any] = ensure_mapping(self.runtime.credentials, field_name="runtime.credentials")

        excel_url = str(params.get("excel_url") or "").strip()
        excel_text = str(params.get("excel_text") or "").strip()
        batch_size = max(1, int(params.get("batch_size") or 500))
        embedding_batch_size = max(1, int(params.get("embedding_batch_size") or 50))
        clear_first = bool(params.get("clear_before_import", False))
        input_group_id = str(params.get("group_id") or "").strip()
        embedding_model = params.get("embedding_model")
        group_id = input_group_id or self._load_last_group_id_from_session() or uuid.uuid4().hex
        mapping = self.resolve_mapping(params.get("mapping"))

        neo4j_uri = str(credentials.get("neo4j_uri", "")).strip()
        neo4j_user = str(credentials.get("neo4j_user", "")).strip()
        neo4j_pwd = str(credentials.get("neo4j_password", "")).strip()

        logger.info("ImportGraphTool invoked | uri=%s user=%s batch=%d group_id=%s", neo4j_uri, neo4j_user, batch_size, group_id)
        yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, f"🧩 group_id: {group_id}\n")

        if not excel_text and not excel_url:
            yield self.create_text_message("请提供 excel_text 或 excel_url。")
            return

        try:
            if excel_text:
                parsed_nodes, parsed_relations = self.parse_markdown_tables(excel_text, mapping)
            else:
                source_name = excel_url.split("/")[-1].split("?")[0] if excel_url else "excel_url"
                parsed_nodes, parsed_relations = self.parse_excel(self.load_excel_bytes(excel_url), mapping, source_name=source_name)
        except Exception as exc:
            logger.error("解析失败: %s", exc)
            yield self.create_text_message(f"解析失败: {exc}")
            return

        logger.info("解析完成 | nodes=%d rels=%d", len(parsed_nodes), len(parsed_relations))
        yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, f"✅ 解析完成：节点 {len(parsed_nodes)}，关系 {len(parsed_relations)}\n")

        try:
            stats = yield from self._write_to_neo4j(
                parsed_nodes, parsed_relations, neo4j_uri, neo4j_user, neo4j_pwd,
                batch_size=batch_size, clear_first=clear_first, group_id=group_id,
                embedding_model=embedding_model, embedding_batch_size=embedding_batch_size,
            )
        except Exception as exc:
            logger.error("写入 Neo4j 失败: %s", exc, exc_info=True)
            yield self.create_text_message(f"写入 Neo4j 失败: {exc}")
            return

        summary = self._build_summary(stats)
        logger.info("导入完成 | %s", summary)
        self._save_group_id_to_session(group_id)

        yield self.create_variable_message("group_id", group_id)
        yield self.create_variable_message("nodes_count", stats["nodes_count"])
        yield self.create_variable_message("rels_count", stats["rels_count"])
        yield self.create_variable_message("skipped_rels", stats["skipped_rels"])
        yield self.create_variable_message("node_type_stats", stats["node_type_stats"])
        yield self.create_variable_message("rel_type_stats", stats["rel_type_stats"])
        yield self.create_text_message(f"✅ 导入完成！\n\ngroup_id: {group_id}\n{summary}")

    def _write_to_neo4j(
        self, parsed_nodes_input: list[dict[str, Any]], parsed_relations_input: list[dict[str, Any]],
        uri: str, user: str, pwd: str, *,
        batch_size: int = 500, embedding_batch_size: int = 50, clear_first: bool = False,
        group_id: str, embedding_model: Any = None,
    ) -> Generator[ToolInvokeMessage, None, dict[str, Any]]:
        yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, "🔌 正在连接 Neo4j...\n")
        skipped_rels = 0

        try:
            if clear_first:
                logger.warning("执行清库操作！")
                yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, "⚠️ 清库中...\n")
                clear_graph(uri, user, pwd, group_id=group_id)

            apoc_nodes, apoc_rels = get_apoc_capabilities(uri, user, pwd)
            logger.info("APOC 可用性 | nodes=%s rels=%s", apoc_nodes, apoc_rels)

            parsed_nodes: list[NodePayload] = []
            for row_data in parsed_nodes_input:
                nid = clean_text(row_data.get("nid"))
                if not nid:
                    continue
                labels = normalize_labels(row_data.get("labels")) or [DEFAULT_NODE_LABEL]
                properties = row_data.get("properties") if isinstance(row_data.get("properties"), dict) else {}
                parsed_nodes.append(NodePayload(
                    nid=nid, name=clean_text(row_data.get("name")), labels=labels,
                    description=clean_text(row_data.get("description") or row_data.get("definition") or row_data.get("说明") or row_data.get("备注") or row_data.get("简介")),
                    group_id=group_id, properties=normalize_properties(properties, field_name="node.properties"),
                    meta=cast("GraphMeta", ensure_mapping(row_data.get("meta"), field_name="node.meta") or _build_meta()),
                ))

            if embedding_model:
                yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, "🧠 生成节点向量...\n")
                idxs, texts = [], []
                for i, n in enumerate(parsed_nodes):
                    t = build_node_embedding_text(n)
                    if t:
                        idxs.append(i)
                        texts.append(t)
                for start in range(0, len(texts), embedding_batch_size):
                    vecs = generate_embeddings(self.session, model_config=embedding_model, texts=texts[start:start + embedding_batch_size])
                    for vi, ni in enumerate(idxs[start:start + embedding_batch_size]):
                        parsed_nodes[ni]["embedding"] = vecs[vi]

            parsed_relations: list[RelationPayload] = []
            known_ids = {r["nid"] for r in parsed_nodes}
            for row_data in parsed_relations_input:
                su, tu = clean_text(row_data.get("source_nid")), clean_text(row_data.get("target_nid"))
                if su not in known_ids or tu not in known_ids:
                    skipped_rels += 1
                    continue
                rt = _normalize_relation_type(row_data.get("rel_type"))
                if not rt:
                    continue
                properties = row_data.get("properties") if isinstance(row_data.get("properties"), dict) else {}
                parsed_relations.append(RelationPayload(
                    source_nid=su, target_nid=tu, rel_type=rt, direction=DEFAULT_DIRECTION,
                    description=clean_text(row_data.get("description") or row_data.get("说明") or row_data.get("备注") or row_data.get("简介")),
                    group_id=group_id, properties=normalize_properties(properties, field_name="relation.properties"),
                    meta=cast("GraphMeta", ensure_mapping(row_data.get("meta"), field_name="relation.meta") or _build_meta()),
                ))

            yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, f"📦 写入节点 {len(parsed_nodes)} 条\n")
            nodes_count = write_nodes(uri, user, pwd, parsed_nodes, batch_size=batch_size)
            yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, f"🔗 写入关系 {len(parsed_relations)} 条，跳过 {skipped_rels}\n")
            rels_count = write_relations(uri, user, pwd, parsed_relations, batch_size=batch_size)
        except Exception:
            logger.error("写入 Neo4j 失败", exc_info=True)
            raise

        node_type_stats: dict[str, int] = {}
        for n in parsed_nodes:
            for l in n.get("labels") or []:
                k = str(l).strip()
                if k:
                    node_type_stats[k] = node_type_stats.get(k, 0) + 1
        rel_type_stats: dict[str, int] = {}
        for r in parsed_relations:
            rt = str(r.get("rel_type") or "").strip()
            if rt:
                rel_type_stats[rt] = rel_type_stats.get(rt, 0) + 1

        return {"nodes_count": nodes_count, "rels_count": rels_count, "skipped_rels": skipped_rels,
                "node_type_stats": node_type_stats, "rel_type_stats": rel_type_stats}

    def _save_group_id_to_session(self, group_id: str) -> None:
        if group_id:
            self.session.storage.set(self._GROUP_ID_STORAGE_KEY, json.dumps({"group_id": group_id}, ensure_ascii=False).encode("utf-8"))

    def _load_last_group_id_from_session(self) -> str:
        try:
            raw = self.session.storage.get(self._GROUP_ID_STORAGE_KEY)
        except Exception:
            return ""
        if not raw:
            return ""
        try:
            return str(json.loads(raw.decode("utf-8")).get("group_id") or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _build_summary(stats: dict) -> str:
        lines = [f"📊 导入统计", f"  节点写入：{stats['nodes_count']}", f"  关系写入：{stats['rels_count']}",
                 f"  跳过关系：{stats['skipped_rels']}", "", "节点类型分布："]
        for t, c in sorted(stats["node_type_stats"].items(), key=lambda x: -x[1]):
            lines.append(f"  {t:<24} {c}")
        lines += ["", "关系类型分布："]
        for t, c in sorted(stats["rel_type_stats"].items(), key=lambda x: -x[1]):
            lines.append(f"  {t:<24} {c}")
        return "\n".join(lines)
