"""
import_graph.py — Dify Tool
将 Excel / Markdown 图谱表格导入 Neo4j 知识图谱。

解析规则
--------
1) 映射驱动：
   默认使用 DEFAULT_FIELD_MAPPING（也可通过工具参数 mapping 覆盖）。
   - 节点核心字段：nodeId / name / labels / description
   - 关系核心字段：src / relType / tgt
   - 其余字段按映射进入 properties

2) 表头扫描：
   在同一数据块内逐行扫描节点/关系表头，不依赖“首行即表头”。
   支持表头重复出现（例如同一大块中多段节点表或关系表）。

3) 标题行处理：
   类似“二、文笔模块内部顶层”且其余列为空/nan 的行，会生成 Title 节点，
   并自动补“包含”关系连接到后续子节点（或关系源节点）。
"""

from __future__ import annotations

import io
import json
import logging
import re
import uuid
from collections.abc import Generator
from typing import Any

import pandas as pd
from neo4j import GraphDatabase
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin.config.logger_format import plugin_logger_handler
from tools.types import (
    NodePayload,
    RelationPayload,
    node_from_excel_row,
    relation_from_excel_row,
    ensure_mapping,
    normalize_labels,
    normalize_properties,
    clean_text,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)

# 测试脚本 markdown_format_recognizer.py 同步的默认映射
DEFAULT_FIELD_MAPPING: dict[str, Any] = {
    "node": {
        "nodeId": "NodeID",
        "name": "name",
        "labels": ["node_type"],
        "description": ["description", "definition", "说明", "备注", "简介"],
        "properties": ["level", "grade_range", "keywords", "teaching_tip", "*"],
    },
    "relation": {
        "src": "SourceID",
        "relType": "RelationType",
        "tgt": "TargetID",
        "description": ["description", "说明", "备注", "简介"],
        "properties": ["*"],
    },
    "groupId": "group_id",
}
_LABEL_SPLIT_PATTERN = re.compile(r"[;,，；]+")

# ── Cypher 模板 ─────────────────────────────────────────────────────────────

_CONSTRAINT_CYPHER = (
    "CREATE CONSTRAINT IF NOT EXISTS "
    "FOR (n:KnowledgeNode) REQUIRE n.nodeId IS UNIQUE"
)

_UPSERT_NODES = """
UNWIND $rows AS row
MERGE (n:KnowledgeNode {nodeId: row.nodeId})
SET n.name        = row.name,
    n.description = row.description,
    n.groupId     = row.groupId
SET n += row.properties
WITH n, row
CALL apoc.create.addLabels(n, row.labels) YIELD node
RETURN count(node)
"""

_UPSERT_NODES_GENERIC = """
UNWIND $rows AS row
MERGE (n:KnowledgeNode {nodeId: row.nodeId})
SET n.name        = row.name,
    n.description = row.description,
    n.groupId     = row.groupId
SET n += row.properties
"""

# 关系：APOC 版（支持动态关系类型）
_UPSERT_RELS_APOC = """
UNWIND $rows AS row
MATCH (src:KnowledgeNode {nodeId: row.src})
MATCH (tgt:KnowledgeNode {nodeId: row.tgt})
CALL apoc.merge.relationship(src, row.relType, {groupId: row.groupId}, row.properties, tgt)
YIELD rel RETURN count(rel)
"""

# 关系：通用版（无 APOC；关系类型存为属性）
_UPSERT_RELS_GENERIC = """
UNWIND $rows AS row
MATCH (src:KnowledgeNode {nodeId: row.src})
MATCH (tgt:KnowledgeNode {nodeId: row.tgt})
MERGE (src)-[r:RELATED {relType: row.relType, groupId: row.groupId}]->(tgt)
SET r += row.properties
"""

# ── 工具类 ──────────────────────────────────────────────────────────────────

class ImportGraphTool(Tool):
    """将 Excel 图谱文件写入 Neo4j。"""

    # 固定表头常量
    _NODE_HEADER = [
        "NodeID", "name", "node_type", "description",
        "level", "grade_range", "keywords", "teaching_tip",
    ]
    _REL_HEADER_3 = ["SourceID", "RelationType", "TargetID"]
    _PROGRESS_VARIABLE = "summary"
    _GROUP_ID_STORAGE_KEY = "import_graph:last_group_id"

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        tool_parameters = ensure_mapping(tool_parameters, field_name="tool_parameters")
        credentials = ensure_mapping(self.runtime.credentials, field_name="runtime.credentials")

        # ── 1. 参数读取 ──────────────────────────────────────────────────
        excel_url   = str(tool_parameters.get("excel_url") or "").strip()
        excel_text  = str(tool_parameters.get("excel_text") or "").strip()
        batch_size  = int(tool_parameters.get("batch_size") or 500)
        clear_first = bool(tool_parameters.get("clear_before_import", False))
        group_id    = str(tool_parameters.get("group_id") or "").strip()
        mapping     = self._resolve_mapping(tool_parameters.get("mapping"))

        if not group_id:
            group_id = self._load_last_group_id_from_session()
        if not group_id:
            group_id = self._generate_group_id()

        neo4j_uri  = str(credentials.get("neo4j_uri",  "")).strip()
        neo4j_user = str(credentials.get("neo4j_user", "")).strip()
        neo4j_pwd  = str(credentials.get("neo4j_password", "")).strip()

        logger.info(
            "ImportGraphTool invoked | uri=%s user=%s batch=%d clear=%s group_id=%s",
            neo4j_uri, neo4j_user, batch_size, clear_first, group_id,
        )
        yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, "🚀 开始导入图谱任务...\n")
        yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, f"🧩 group_id: {group_id}\n")

        # ── 2. 参数校验 ──────────────────────────────────────────────────
        if not excel_text and not excel_url:
            yield self.create_text_message("❌ 请提供 excel_text 或 excel_url，二者不能同时为空。")
            return

        # ── 3. 读取并解析图谱 ────────────────────────────────────────────
        if excel_text:
            yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, "🧩 检测到 excel_text，开始解析 Markdown 图谱...\n")
            yield self.create_text_message("⏳ 正在解析 Markdown 图谱文本…")
            try:
                nodes_df, rels_df = self._parse_markdown_tables(excel_text, mapping)
            except Exception as exc:
                logger.error("解析 Markdown 文本失败: %s", exc)
                yield self.create_text_message(f"❌ 解析 Markdown 文本失败：{exc}")
                return
        else:
            yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, "📥 开始读取 Excel 文件...\n")
            yield self.create_text_message("⏳ 正在读取 Excel 文件…")
            try:
                excel_bytes = self._load_excel_bytes(excel_url)
            except Exception as exc:
                logger.error("读取 Excel 失败: %s", exc)
                yield self.create_text_message(f"❌ 读取 Excel 失败：{exc}")
                return

            yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, "🧩 Excel 已读取，开始解析图谱结构...\n")
            yield self.create_text_message("⏳ 正在解析图谱结构…")
            try:
                nodes_df, rels_df = self._parse_excel(excel_bytes, mapping)
            except Exception as exc:
                logger.error("解析 Excel 失败: %s", exc)
                yield self.create_text_message(f"❌ 解析 Excel 失败：{exc}")
                return

        logger.info("解析完成 | nodes=%d rels=%d", len(nodes_df), len(rels_df))
        yield self.create_stream_variable_message(
            self._PROGRESS_VARIABLE,
            f"✅ 解析完成：节点 {len(nodes_df)}，关系 {len(rels_df)}。\n"
        )
        yield self.create_text_message(
            f"✅ 解析完成：{len(nodes_df)} 个节点，{len(rels_df)} 条关系。\n"
            "⏳ 正在写入 Neo4j…"
        )

        # ── 5. 写入 Neo4j ────────────────────────────────────────────────
        try:
            stats = yield from self._write_to_neo4j(
                nodes_df, rels_df, neo4j_uri, neo4j_user, neo4j_pwd,
                batch_size=batch_size, clear_first=clear_first, group_id=group_id,
            )
        except Exception as exc:
            logger.error("写入 Neo4j 失败: %s", exc, exc_info=True)
            yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, f"❌ 写入失败：{exc}\n")
            yield self.create_text_message(f"❌ 写入 Neo4j 失败：{exc}")
            return

        # ── 6. 构建结果 ──────────────────────────────────────────────────
        summary = self._build_summary(stats)
        logger.info("导入完成 | %s", summary)
        yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, "🎉 Neo4j 写入完成，正在汇总统计结果...\n")
        self._save_group_id_to_session(group_id)

        # 工作流变量输出
        yield self.create_variable_message("group_id",       group_id)
        yield self.create_variable_message("nodes_count",     stats["nodes_count"])
        yield self.create_variable_message("rels_count",      stats["rels_count"])
        yield self.create_variable_message("skipped_rels",    stats["skipped_rels"])
        yield self.create_variable_message("node_type_stats", stats["node_type_stats"])
        yield self.create_variable_message("rel_type_stats",  stats["rel_type_stats"])
        yield self.create_variable_message("summary",         summary)

        # 人类可读输出
        yield self.create_text_message(f"✅ 导入完成！\n\n{summary}")
        yield self.create_json_message(stats)

    # ── 内部：加载 Excel 字节 ────────────────────────────────────────────

    def _load_excel_bytes(self, excel_url: str) -> bytes:
        if excel_url:
            import urllib.request
            with urllib.request.urlopen(excel_url, timeout=60) as resp:
                return resp.read()

        raise ValueError("无法获取 Excel 数据。")

    # ── 内部：解析 Markdown 表格 ────────────────────────────────────────

    def _parse_markdown_tables(
        self,
        excel_text: str,
        mapping: dict[str, Any],
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        rows = self._split_markdown_rows(excel_text)
        blocks = self._split_rows_into_blocks(rows)
        if not blocks:
            raise ValueError("未检测到 Markdown 表格。")
        return self._parse_blocks_with_mapping(blocks, mapping)

    # ── 内部：解析 Excel ─────────────────────────────────────────────────

    def _parse_excel(
        self,
        excel_bytes: bytes,
        mapping: dict[str, Any],
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        逐行扫描，识别节点表头和关系表头，收集所有行。
        """
        df_raw = pd.read_excel(io.BytesIO(excel_bytes), header=None, dtype=str).fillna("")
        rows = [[str(v or "").strip() for v in r] for r in df_raw.values.tolist()]
        blocks = self._split_rows_into_blocks(rows)
        if not blocks:
            raise ValueError("未检测到 Excel 表格行。")
        return self._parse_blocks_with_mapping(blocks, mapping)

    # ── 内部：统一映射解析 ───────────────────────────────────────────────

    @staticmethod
    def _resolve_mapping(mapping: Any) -> dict[str, Any]:
        if mapping is None:
            return DEFAULT_FIELD_MAPPING
        if isinstance(mapping, str):
            mapping = mapping.strip()
            if not mapping:
                return DEFAULT_FIELD_MAPPING
            mapping_obj = ensure_mapping(mapping, field_name="mapping")
            return dict(mapping_obj)
        mapping_obj = ensure_mapping(mapping, field_name="mapping")
        return dict(mapping_obj)

    @staticmethod
    def _normalize_row(row: list[Any]) -> list[str]:
        return [str(cell or "").strip() for cell in row]

    @staticmethod
    def _is_empty_like(value: str) -> bool:
        return str(value or "").strip().lower() in {"", "nan", "none", "null"}

    def _is_title_row(self, row: list[str]) -> bool:
        if not row:
            return False
        if self._is_empty_like(row[0]):
            return False
        return all(self._is_empty_like(cell) for cell in row[1:])

    @staticmethod
    def _sanitize_label(text: Any) -> str:
        value = str(text or "").strip()
        value = re.sub(r"[/\\\-\s]+", "_", value)
        return value or "Node"

    def _title_to_label(self, title: str) -> str:
        text = str(title or "").strip()
        if not text:
            return ""
        return self._sanitize_label(text)

    @staticmethod
    def _generate_title_node_id(table_index: int, title_index: int) -> str:
        return f"TITLE_{table_index + 1}_{title_index:04d}"

    @staticmethod
    def _normalize_label_fields(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if isinstance(value, (list, tuple, set)):
            fields: list[str] = []
            for item in value:
                text = str(item or "").strip()
                if text:
                    fields.append(text)
            return fields
        text = str(value or "").strip()
        return [text] if text else []

    def _split_labels_text(self, value: Any) -> list[str]:
        text = str(value or "").strip()
        if not text or self._is_empty_like(text):
            return []
        parts = [part.strip() for part in _LABEL_SPLIT_PATTERN.split(text) if part and part.strip()]
        labels: list[str] = []
        for part in parts:
            label = self._sanitize_label(part)
            if label and label not in labels:
                labels.append(label)
        return labels

    def _normalize_description_fields(self, value: Any) -> list[str]:
        fields = self._normalize_label_fields(value)
        if fields:
            return fields
        return ["description", "definition", "说明", "备注", "简介"]

    def _normalize_relation_description_fields(self, value: Any) -> list[str]:
        fields = self._normalize_label_fields(value)
        if fields:
            return fields
        return ["description", "说明", "备注", "简介"]

    def _extract_first_by_fields(self, row: list[str], index: dict[str, int], fields: list[str]) -> str:
        for field in fields:
            value = self._get_cell(row, index, field)
            if not self._is_empty_like(value):
                return value
        return ""

    def _split_markdown_rows(self, text: str) -> list[list[str]]:
        lines = [line.rstrip() for line in text.splitlines()]
        rows: list[list[str]] = []
        for line in lines:
            if "|" not in line:
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if not cells:
                continue
            if all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells):
                continue
            rows.append(cells)
        return rows

    def _split_rows_into_blocks(self, rows: list[list[str]]) -> list[list[list[str]]]:
        blocks: list[list[list[str]]] = []
        current: list[list[str]] = []
        for row in rows:
            normalized = self._normalize_row(row)
            if all(self._is_empty_like(cell) for cell in normalized):
                if current:
                    blocks.append(current)
                    current = []
                continue
            current.append(normalized)
        if current:
            blocks.append(current)
        return blocks

    @staticmethod
    def _starts_with(row: list[str], expected: list[str]) -> bool:
        if len(row) < len(expected):
            return False
        for idx, value in enumerate(expected):
            if row[idx].strip() != value:
                return False
        return True

    @staticmethod
    def _matches_any(value: str, candidates: list[str]) -> bool:
        text = str(value or "").strip()
        return any(text == str(candidate or "").strip() for candidate in candidates)

    def _is_node_header(self, row: list[str], mapping: dict[str, Any]) -> bool:
        node = mapping["node"]
        label_fields = self._normalize_label_fields(node.get("labels"))
        desc_fields = self._normalize_description_fields(node.get("description", node.get("definition")))
        if len(row) < 4:
            return False
        if row[0].strip() != str(node["nodeId"]).strip():
            return False
        if row[1].strip() != str(node["name"]).strip():
            return False

        effective_labels = label_fields or ["node_type"]
        if not self._matches_any(row[2], effective_labels):
            return False

        effective_desc_fields = desc_fields or ["description", "definition", "说明", "备注", "简介"]
        return self._matches_any(row[3], effective_desc_fields)

    def _is_rel_header(self, row: list[str], mapping: dict[str, Any]) -> bool:
        rel = mapping["relation"]
        expected = [rel["src"], rel["relType"], rel["tgt"]]
        return self._starts_with(row, expected)

    @staticmethod
    def _build_index(header: list[str]) -> dict[str, int]:
        result: dict[str, int] = {}
        for i, col in enumerate(header):
            key = col.strip()
            if key and key not in result:
                result[key] = i
        return result

    @staticmethod
    def _get_cell(row: list[str], index: dict[str, int], column: str) -> str:
        col_idx = index.get(column)
        if col_idx is None or col_idx >= len(row):
            return ""
        return row[col_idx].strip()

    @staticmethod
    def _normalize_property_value(value: str) -> str:
        return str(value or "").strip()

    def _collect_properties(
        self,
        row: list[str],
        index: dict[str, int],
        configured: list[str],
        reserved_fields: set[str],
    ) -> dict[str, Any]:
        props: dict[str, Any] = {}
        for field in configured:
            if field == "*":
                for col, col_idx in index.items():
                    if col in reserved_fields or col.startswith("Unnamed:"):
                        continue
                    value = row[col_idx].strip() if col_idx < len(row) else ""
                    if self._is_empty_like(value):
                        continue
                    if col not in props:
                        props[col] = self._normalize_property_value(value)
                continue

            value = self._get_cell(row, index, field)
            if self._is_empty_like(value):
                continue
            props[field] = self._normalize_property_value(value)
        return props

    def _parse_blocks_with_mapping(
        self,
        blocks: list[list[list[str]]],
        mapping: dict[str, Any],
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        node_rows: list[NodePayload] = []
        rel_rows: list[dict[str, Any]] = []

        node_map = mapping["node"]
        rel_map = mapping["relation"]
        label_fields = self._normalize_label_fields(node_map.get("labels"))
        desc_fields = self._normalize_description_fields(node_map.get("description", node_map.get("definition")))
        rel_desc_fields = self._normalize_relation_description_fields(rel_map.get("description"))

        for table_index, table in enumerate(blocks):
            current_kind: str | None = None
            current_index: dict[str, int] = {}
            current_title_node_id = ""
            title_seq = 1

            for raw_row in table:
                row = self._normalize_row(raw_row)
                if not any(not self._is_empty_like(cell) for cell in row):
                    continue
                if self._is_title_row(row):
                    title_name = str(row[0] or "").strip()
                    if not title_name:
                        continue
                    title_node_id = self._generate_title_node_id(table_index, title_seq)
                    title_seq += 1
                    node_rows.append(
                        NodePayload(
                            nodeId=title_node_id,
                            name=title_name,
                            labels=["Title"],
                            description="",
                            groupId="",
                            properties={},
                        )
                    )
                    current_title_node_id = title_node_id
                    continue

                if self._is_node_header(row, mapping):
                    current_kind = "node"
                    current_index = self._build_index(row)
                    continue

                if self._is_rel_header(row, mapping):
                    current_kind = "relation"
                    current_index = self._build_index(row)
                    continue

                if current_kind == "node":
                    node_id = self._get_cell(row, current_index, node_map["nodeId"])
                    if self._is_empty_like(node_id):
                        continue

                    name = self._get_cell(row, current_index, node_map["name"])
                    description = self._extract_first_by_fields(row, current_index, desc_fields)
                    labels: list[str] = []
                    for field in label_fields:
                        value = self._get_cell(row, current_index, field)
                        if self._is_empty_like(value):
                            continue
                        for label in self._split_labels_text(value):
                            if label not in labels:
                                labels.append(label)
                    if not labels:
                        labels = ["Node"]
                    reserved = {
                        node_map["nodeId"],
                        node_map["name"],
                    }
                    for field in desc_fields:
                        reserved.add(field)
                    for field in label_fields:
                        reserved.add(field)
                    props = self._collect_properties(
                        row=row,
                        index=current_index,
                        configured=list(node_map.get("properties", [])),
                        reserved_fields=reserved,
                    )
                    node_rows.append(
                        NodePayload(
                            nodeId=node_id,
                            name=name,
                            labels=labels,
                            description=description,
                            groupId="",
                            properties=props,
                        )
                    )
                    if current_title_node_id and current_title_node_id != node_id:
                        rel_rows.append(
                            {
                                "SourceID": current_title_node_id,
                                "RelationType": "包含",
                                "TargetID": node_id,
                                "description": "",
                            }
                        )
                    continue

                if current_kind == "relation":
                    src = self._get_cell(row, current_index, rel_map["src"])
                    rel_type = self._get_cell(row, current_index, rel_map["relType"])
                    tgt = self._get_cell(row, current_index, rel_map["tgt"])
                    if self._is_empty_like(src) or self._is_empty_like(rel_type) or self._is_empty_like(tgt):
                        continue
                    reserved = {rel_map["src"], rel_map["relType"], rel_map["tgt"]}
                    for field in rel_desc_fields:
                        reserved.add(field)
                    props = self._collect_properties(
                        row=row,
                        index=current_index,
                        configured=list(rel_map.get("properties", [])),
                        reserved_fields=reserved,
                    )
                    description = self._extract_first_by_fields(row, current_index, rel_desc_fields)
                    rel_rows.append(
                        {
                            "SourceID": src,
                            "RelationType": rel_type,
                            "TargetID": tgt,
                            "description": description,
                        }
                    )
                    if current_title_node_id and current_title_node_id != src:
                        rel_rows.append(
                            {
                                "SourceID": current_title_node_id,
                                "RelationType": "包含",
                                "TargetID": src,
                                "description": "",
                            }
                        )

        if node_rows:
            nodes_df = (
                pd.DataFrame(node_rows)
                .drop_duplicates(subset="nodeId")
                .reset_index(drop=True)
            )
        else:
            nodes_df = pd.DataFrame(columns=["nodeId", "name", "labels", "description", "groupId", "properties"])

        if rel_rows:
            rels_df = (
                pd.DataFrame(rel_rows)
                .drop_duplicates(subset=["SourceID", "RelationType", "TargetID"])
                .reset_index(drop=True)
            )
        else:
            rels_df = pd.DataFrame(columns=["SourceID", "RelationType", "TargetID", "description"])

        return nodes_df, rels_df

    # ── 内部：写入 Neo4j ─────────────────────────────────────────────────

    @staticmethod
    def _has_apoc(session) -> bool:
        try:
            session.run("RETURN apoc.version()").single()
            return True
        except Exception:
            return False

    def _write_to_neo4j(
        self,
        nodes_df: pd.DataFrame,
        rels_df:  pd.DataFrame,
        uri: str,
        user: str,
        pwd: str,
        *,
        batch_size: int = 500,
        clear_first: bool = False,
        group_id: str,
    ) -> Generator[ToolInvokeMessage, None, dict[str, Any]]:
        yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, "🔌 正在连接 Neo4j...\n")
        driver = GraphDatabase.driver(uri, auth=(user, pwd))
        skipped_rels = 0

        try:
            with driver.session() as session:
                # 可选：清空
                if clear_first:
                    logger.warning("执行清库操作！")
                    yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, "⚠️ 已启用 clear_before_import，先执行清库...\n")
                    session.run("MATCH (n) DETACH DELETE n")

                # 约束
                yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, "🧱 正在校验/创建唯一约束...\n")
                try:
                    session.run(_CONSTRAINT_CYPHER)
                except Exception as e:
                    logger.warning("约束创建跳过: %s", e)

                apoc = self._has_apoc(session)
                logger.info("APOC 可用: %s", apoc)
                yield self.create_stream_variable_message(
                    self._PROGRESS_VARIABLE,
                    f"🧪 APOC 可用性：{'可用' if apoc else '不可用，使用通用关系写入'}。\n"
                )

                # ── 写节点 ───────────────────────────────────────────
                node_rows: list[NodePayload] = []
                for _, row in nodes_df.iterrows():
                    row_data = row.to_dict()
                    if "nodeId" in row_data:
                        labels = normalize_labels(row_data.get("labels"))
                        if not labels:
                            labels = normalize_labels(row_data.get("node_type"))
                        properties = row_data.get("properties")
                        if isinstance(properties, dict):
                            properties = normalize_properties(properties, field_name="node.properties")
                        else:
                            properties = {}
                        node_rows.append(
                            NodePayload(
                                nodeId=clean_text(row_data.get("nodeId")),
                                name=clean_text(row_data.get("name")),
                                labels=labels or ["Node"],
                                description=clean_text(
                                    row_data.get("description")
                                    or row_data.get("definition")
                                    or row_data.get("说明")
                                    or row_data.get("备注")
                                    or row_data.get("简介")
                                ),
                                groupId=clean_text(group_id or row_data.get("groupId") or row_data.get("group_id")),
                                properties=properties,
                            )
                        )
                    else:
                        node_rows.append(node_from_excel_row(row_data, group_id=group_id))

                yield self.create_stream_variable_message(
                    self._PROGRESS_VARIABLE,
                    f"📦 开始写入节点，共 {len(node_rows)} 条，批大小 {batch_size}。\n"
                )
                for start in range(0, len(node_rows), batch_size):
                    batch = node_rows[start: start + batch_size]
                    try:
                        if apoc:
                            session.run(_UPSERT_NODES, rows=batch)
                        else:
                            session.run(_UPSERT_NODES_GENERIC, rows=batch)
                    except Exception as e:
                        logger.warning("节点批次写入失败，退回通用模式: %s", e)
                        yield self.create_stream_variable_message(
                            self._PROGRESS_VARIABLE,
                            f"⚠️ 节点批次触发回退，改用通用模式：{e}\n"
                        )
                        session.run(_UPSERT_NODES_GENERIC, rows=batch)
                    current = min(start + batch_size, len(node_rows))
                    logger.info("节点写入 %d/%d", current, len(node_rows))
                    yield self.create_stream_variable_message(
                        self._PROGRESS_VARIABLE,
                        f"📦 节点写入进度：{current}/{len(node_rows)}。\n"
                    )

                # ── 写关系 ───────────────────────────────────────────
                rel_cypher = _UPSERT_RELS_APOC if apoc else _UPSERT_RELS_GENERIC
                rel_rows: list[RelationPayload] = []
                known_ids  = {r["nodeId"] for r in node_rows}

                for _, row in rels_df.iterrows():
                    row_data = row.to_dict()
                    src = clean_text(row_data.get("src") or row_data.get("SourceID"))
                    tgt = clean_text(row_data.get("tgt") or row_data.get("TargetID"))
                    if src not in known_ids or tgt not in known_ids:
                        skipped_rels += 1
                        logger.warning("跳过关系（节点缺失）: %s → %s", src, tgt)
                        continue
                    if "src" in row_data:
                        rel_type = clean_text(row_data.get("relType") or row_data.get("RelationType"))
                        if not rel_type:
                            continue
                        properties = row_data.get("properties")
                        if isinstance(properties, dict):
                            properties = normalize_properties(properties, field_name="relation.properties")
                        else:
                            properties = {}
                        rel_rows.append(
                            RelationPayload(
                                src=src,
                                tgt=tgt,
                                relType=rel_type,
                                description=clean_text(
                                    row_data.get("description")
                                    or row_data.get("说明")
                                    or row_data.get("备注")
                                    or row_data.get("简介")
                                ),
                                groupId=clean_text(group_id or row_data.get("groupId") or row_data.get("group_id")),
                                properties=properties,
                            )
                        )
                    else:
                        rel_rows.append(relation_from_excel_row(row_data, group_id=group_id))

                yield self.create_stream_variable_message(
                    self._PROGRESS_VARIABLE,
                    f"🔗 开始写入关系，共 {len(rel_rows)} 条，跳过 {skipped_rels} 条。\n"
                )
                for start in range(0, len(rel_rows), batch_size):
                    batch = rel_rows[start: start + batch_size]
                    try:
                        session.run(rel_cypher, rows=batch)
                    except Exception as e:
                        logger.warning("关系批次失败，退回通用模式: %s", e)
                        yield self.create_stream_variable_message(
                            self._PROGRESS_VARIABLE,
                            f"⚠️ 关系批次触发回退，改用通用模式：{e}\n"
                        )
                        session.run(_UPSERT_RELS_GENERIC, rows=batch)
                    current = min(start + batch_size, len(rel_rows))
                    logger.info("关系写入 %d/%d", current, len(rel_rows))
                    yield self.create_stream_variable_message(
                        self._PROGRESS_VARIABLE,
                        f"🔗 关系写入进度：{current}/{len(rel_rows)}。\n"
                    )

        finally:
            driver.close()

        # 统计
        node_type_stats: dict[str, int] = {}
        for node in node_rows:
            labels = node.get("labels") or []
            for label in labels:
                key = str(label).strip()
                if not key:
                    continue
                node_type_stats[key] = node_type_stats.get(key, 0) + 1

        rel_type_stats: dict[str, int] = {}
        for rel in rel_rows:
            rel_type = str(rel.get("relType") or "").strip()
            if not rel_type:
                continue
            rel_type_stats[rel_type] = rel_type_stats.get(rel_type, 0) + 1

        yield self.create_stream_variable_message(
            self._PROGRESS_VARIABLE,
            f"✅ 写入阶段完成：节点 {len(node_rows)}，关系 {len(rel_rows)}，跳过 {skipped_rels}。\n"
        )

        return {
            "nodes_count":     len(node_rows),
            "rels_count":      len(rel_rows),
            "skipped_rels":    skipped_rels,
            "node_type_stats": node_type_stats,
            "rel_type_stats":  rel_type_stats,
        }

    @staticmethod
    def _generate_group_id() -> str:
        return uuid.uuid4().hex

    def _save_group_id_to_session(self, group_id: str) -> None:
        if not group_id:
            return
        payload = {"group_id": group_id}
        self.session.storage.set(
            self._GROUP_ID_STORAGE_KEY,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )

    def _load_last_group_id_from_session(self) -> str:
        try:
            raw = self.session.storage.get(self._GROUP_ID_STORAGE_KEY)
        except Exception:
            return ""
        if not raw:
            return ""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return ""
        return str(payload.get("group_id") or "").strip()

    # ── 内部：生成摘要文本 ────────────────────────────────────────────────

    @staticmethod
    def _build_summary(stats: dict) -> str:
        lines = [
            f"📊 导入统计",
            f"  节点写入：{stats['nodes_count']}",
            f"  关系写入：{stats['rels_count']}",
            f"  跳过关系：{stats['skipped_rels']}（源节点或目标节点不存在）",
            "",
            "节点类型分布：",
        ]
        for t, c in sorted(stats["node_type_stats"].items(), key=lambda x: -x[1]):
            lines.append(f"  {t:<24} {c}")
        lines.append("")
        lines.append("关系类型分布：")
        for t, c in sorted(stats["rel_type_stats"].items(), key=lambda x: -x[1]):
            lines.append(f"  {t:<24} {c}")
        return "\n".join(lines)
