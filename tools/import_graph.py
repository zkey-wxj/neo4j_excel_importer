"""
import_graph.py — Dify Tool
将 Excel / Markdown 图谱表格导入 Neo4j 知识图谱。

解析规则
--------
1) 映射驱动：
   默认使用 DEFAULT_FIELD_MAPPING（也可通过工具参数 mapping 覆盖）。
   - 节点核心字段：uid / name / labels / description / group_id
   - 关系核心字段：source_uid / rel_type / target_uid / group_id
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
from collections.abc import Generator, Mapping
from typing import Any, cast

import pandas as pd
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin.config.logger_format import plugin_logger_handler
from core.embedding_common import (
    build_node_embedding_text,
    generate_embeddings,
)
from core.graph_write_common import (
    clear_graph,
    get_apoc_capabilities,
    write_nodes,
    write_relations,
)
from core.constants import DEFAULT_DIRECTION, DEFAULT_NODE_LABEL
from core.types import (
    GraphMeta,
    NodePayload,
    RelationPayload,
    ensure_mapping,
    normalize_labels,
    normalize_properties,
    clean_text,
    utc_now_iso,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)

# 测试脚本 markdown_format_recognizer.py 同步的默认映射
DEFAULT_FIELD_MAPPING: dict[str, Any] = {
    "node": {
        "uid": "NodeID",
        "name": "name",
        "labels": ["node_type", "keywords"],
        "description": ["description", "definition", "说明", "备注", "简介"],
        "properties": ["level", "grade_range", "keywords", "teaching_tip"],
    },
    "relation": {
        "source_uid": "SourceID",
        "rel_type": "RelationType",
        "target_uid": "TargetID",
        "description": ["description", "说明", "备注", "简介"],
        "properties": [],
    },
}
_LABEL_SPLIT_PATTERN = re.compile(r"[;,，；]+")
_CHINESE_SECTION_PATTERN = re.compile(r"^[一二三四五六七八九十百千]+[、.．]")
_CHINESE_SUBSECTION_PATTERN = re.compile(r"^[（(][一二三四五六七八九十百千]+[)）]")
_DECIMAL_SECTION_PATTERN = re.compile(r"^(\d+(?:\.\d+)*)[、.．)）]?\s*")
_NAME_PREFIX_PATTERN = re.compile(
    r"^\s*(?:"
    r"[（(]\s*(?:\d+(?:\.\d+)*|[一二三四五六七八九十百千万零〇两]+)\s*[)）][、.．]?"
    r"|第[一二三四五六七八九十百千万零〇两\d]+(?:部分|章|节|单元|课|篇)?[：:]"
    r"|[一二三四五六七八九十百千万零〇两]+[、.．]"
    r"|\d+(?:\.\d+)+"
    r"|\d+[)）、]"
    r"|\d+[．]"
    r"|\d+\.(?!\d)"
    r")\s*"
)
_EMPTY_VALUES = {"", "nan", "none", "null"}
_EDGE_SYMBOL_PATTERN = re.compile(
    r"""^[\s*＊`"'“”‘’\[\]【】\(\)（）{}<>《》,，.;；:：!！?？、|\\/]+|[\s*＊`"'“”‘’\[\]【】\(\)（）{}<>《》,，.;；:：!！?？、|\\/]+$"""
)
_REL_TYPE_NORMALIZATION_MAP: dict[str, str] = {
    "包含：": "包含",
    "包含知识点": "包含",
    "包含子知识点": "包含",
    "包含层次": "包含",
    "包含题型": "包含",
    "包含维度": "包含",
    "包含阶段": "包含",
    "包含模块": "包含",
    "包含场景": "包含",
    "包含类型": "包含",
    "包含线路": "包含",
    "包含常识": "包含",
    "包含文体节点": "包含",
    "包含触发点": "包含",
    "包含方向": "包含",
    "包含学段场所": "包含",
    "包含子节点": "包含",
    "包含方法维度": "包含",
    "包含格式规范": "包含",
    "包含路径": "包含",
    "包含专项轴": "包含",
    "含有": "包含",
    "包含方法": "包含",
    "包括": "包含",
    "使用": "包含",
    "组成": "包含",
    "对应阅读能力": "对应",
    "对应阅读错误": "对应",
    "对应知识点": "对应",
    "对应立意能力": "对应",
    "对应维度": "对应",
    "同层对应": "对应",
    "同源异表": "对应",
    "同为输入": "对应",
    "调用能力": "调用",
    "调用阅读能力": "调用",
    "调用倾听能力": "调用",
    "调用口语能力": "调用",
    "调用思维": "调用",
    "调用思维能力": "调用",
    "调用策略": "调用",
    "调用技法": "调用",
    "支撑能力": "支撑",
    "支撑方法": "支撑",
    "支持": "支撑",
    "支撑写作能力": "支撑",
    "支撑阅读能力": "支撑",
    "支撑倾听能力": "支撑",
    "支撑口语能力": "支撑",
    "支撑演绎能力": "支撑",
    "支撑研学能力": "支撑",
    "支撑表达策略": "支撑",
    "基础支撑": "支撑",
    "输入支撑": "支撑",
    "关联写作": "关联",
    "关联写作能力": "关联",
    "关联阅读能力": "关联",
    "关联阅读方法": "关联",
    "关联倾听能力": "关联",
    "关联口语能力": "关联",
    "前置能力": "前置",
    "前置学习": "前置",
    "前置决定": "前置",
    "前置价值": "前置",
    "驱动": "驱动",
    "驱动构篇": "驱动",
    "承接驱动": "承接",
    "承接执行": "承接",
    "核心指向": "指向",
    "指向知识点": "指向",
    "指向维度": "指向",
    "指向写作文笔": "指向",
    "依赖阅读能力": "依赖",
    "核心依托": "依赖",
    "重要依托": "依赖",
    "训练能力": "训练",
    "贯穿": "贯通",
    "使用模型": "使用",
    "通过技法实现": "实现",
    "主要使用策略": "使用",
    "辅助使用策略": "使用",
    "可拓展至": "拓展",
    "是…的地方变体": "变体",
}


def _build_meta(source: str = "", source_row_num: int | None = None) -> dict[str, Any]:
    now = utc_now_iso()
    return {
        "created_at": now,
        "updated_at": now,
        "source": clean_text(source),
        "source_row": source_row_num or 0,
    }


def _normalize_edge_symbols(value: Any) -> str:
    """去除字段前后无意义符号，仅保留核心文本。"""
    text = clean_text(value)
    if not text:
        return ""
    candidate = text
    for _ in range(3):
        updated = _EDGE_SYMBOL_PATTERN.sub("", candidate).strip()
        if updated == candidate:
            break
        candidate = updated
    return candidate


def _normalize_relation_type(rel_type: Any) -> str:
    text = _normalize_edge_symbols(rel_type)
    if not text:
        return ""
    text = re.sub(r"[*＊]+$", "", text).strip()
    text = re.sub(r"[，,；;。]+$", "", text).strip()
    text_without_colon = text.rstrip("：:").strip()

    normalized = _REL_TYPE_NORMALIZATION_MAP.get(text) or _REL_TYPE_NORMALIZATION_MAP.get(text_without_colon)
    if normalized:
        return normalized

    if text_without_colon.startswith("包含"):
        return "包含"
    if text_without_colon.startswith("对应"):
        return "对应"
    if text_without_colon.startswith("调用"):
        return "调用"
    if text_without_colon.startswith("支撑") or text_without_colon.startswith("支持"):
        return "支撑"
    if text_without_colon.startswith("关联"):
        return "关联"
    if text_without_colon.startswith("前置"):
        return "前置"
    if text_without_colon.startswith("驱动"):
        return "驱动"
    if text_without_colon.startswith("承接"):
        return "承接"
    if text_without_colon.startswith("指向"):
        return "指向"
    if text_without_colon.startswith("依赖"):
        return "依赖"
    if text_without_colon.startswith("训练"):
        return "训练"
    if text_without_colon.startswith("适用"):
        return "适用"
    if text_without_colon.startswith("拓展"):
        return "拓展"
    return _normalize_edge_symbols(text_without_colon)

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

    class _TitleUidAllocator:
        """按标题层级分配递增 UID：TITLE_01 / TITLE_01_01。"""

        def __init__(self) -> None:
            self._counters: list[int] = []

        def allocate(self, level: int) -> str:
            normalized_level = max(1, level)
            if not self._counters:
                self._counters = [0]
            if normalized_level > len(self._counters) + 1:
                normalized_level = len(self._counters) + 1
            self._counters = self._counters[:normalized_level]
            if len(self._counters) < normalized_level:
                self._counters.extend([0] * (normalized_level - len(self._counters)))
            self._counters[normalized_level - 1] += 1
            segments = [f"{counter:02d}" for counter in self._counters]
            return f"TITLE_{'_'.join(segments)}"

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        params: Mapping[str, Any] = ensure_mapping(tool_parameters, field_name="tool_parameters")
        credentials: Mapping[str, Any] = ensure_mapping(self.runtime.credentials, field_name="runtime.credentials")

        # ── 1. 参数读取 ──────────────────────────────────────────────────
        excel_url   = str(params.get("excel_url") or "").strip()
        excel_text  = str(params.get("excel_text") or "").strip()
        batch_size  = max(1, int(params.get("batch_size") or 500))
        embedding_batch_size = int(params.get("embedding_batch_size") or 50)
        if embedding_batch_size <= 0:
            embedding_batch_size = 50
        clear_first = bool(params.get("clear_before_import", False))
        input_group_id = str(params.get("group_id") or "").strip()
        embedding_model = params.get("embedding_model")
        group_id    = input_group_id
        mapping     = self._resolve_mapping(params.get("mapping"))

        if not group_id:
            group_id = self._load_last_group_id_from_session()
        if not group_id:
            group_id = self._generate_group_id()

        neo4j_uri  = str(credentials.get("neo4j_uri",  "")).strip()
        neo4j_user = str(credentials.get("neo4j_user", "")).strip()
        neo4j_pwd  = str(credentials.get("neo4j_password", "")).strip()

        logger.info(
            "ImportGraphTool invoked | uri=%s user=%s batch=%d embedding_batch=%d clear=%s group_id=%s",
            neo4j_uri, neo4j_user, batch_size, embedding_batch_size, clear_first, group_id,
        )
        yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, "🚀 开始导入图谱任务...\n")
        yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, f"🧩 group_id: {group_id}\n")
        if embedding_model:
            yield self.create_stream_variable_message(
                self._PROGRESS_VARIABLE,
                "🧠 向量开关：已启用\n",
            )
        else:
            yield self.create_stream_variable_message(
                self._PROGRESS_VARIABLE,
                "🧠 向量开关：未启用\n",
            )

        # ── 2. 参数校验 ──────────────────────────────────────────────────
        if not excel_text and not excel_url:
            yield self.create_text_message("❌ 请提供 excel_text 或 excel_url，二者不能同时为空。")
            return

        # ── 3. 读取并解析图谱 ────────────────────────────────────────────
        if excel_text:
            yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, "🧩 检测到 excel_text，开始解析 Markdown 图谱...\n")
            try:
                parsed_nodes, parsed_relations = self._parse_markdown_tables(excel_text, mapping)
            except Exception as exc:
                logger.error("解析 Markdown 文本失败: %s", exc)
                yield self.create_text_message(f"❌ 解析 Markdown 文本失败：{exc}")
                return
        else:
            yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, "📥 开始读取 Excel 文件...\n")
            try:
                excel_bytes = self._load_excel_bytes(excel_url)
            except Exception as exc:
                logger.error("读取 Excel 失败: %s", exc)
                yield self.create_text_message(f"❌ 读取 Excel 失败：{exc}")
                return

            yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, "🧩 Excel 已读取，开始解析图谱结构...\n")
            try:
                source_name = excel_url.split("/")[-1].split("?")[0] if excel_url else "excel_url"
                parsed_nodes, parsed_relations = self._parse_excel(excel_bytes, mapping, source_name=source_name)
            except Exception as exc:
                logger.error("解析 Excel 失败: %s", exc)
                yield self.create_text_message(f"❌ 解析 Excel 失败：{exc}")
                return

        logger.info("解析完成 | nodes=%d rels=%d", len(parsed_nodes), len(parsed_relations))
        yield self.create_stream_variable_message(
            self._PROGRESS_VARIABLE,
            f"✅ 解析完成：节点 {len(parsed_nodes)}，关系 {len(parsed_relations)}。\n"
        )

        # ── 5. 写入 Neo4j ────────────────────────────────────────────────
        try:
            stats = yield from self._write_to_neo4j(
                parsed_nodes, parsed_relations, neo4j_uri, neo4j_user, neo4j_pwd,
                batch_size=batch_size, clear_first=clear_first, group_id=group_id,
                embedding_model=embedding_model,
                embedding_batch_size=embedding_batch_size,
            )
        except Exception as exc:
            logger.error("写入 Neo4j 失败: %s", exc, exc_info=True)
            yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, f"❌ 写入失败\n")
            yield self.create_text_message(f"❌ 写入 Neo4j 失败：{exc}")
            return

        # ── 6. 构建结果 ──────────────────────────────────────────────────
        summary = self._build_summary(stats)
        logger.info("导入完成 | %s", summary)
        yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, f"🎉 Neo4j 写入完成")
        self._save_group_id_to_session(group_id)

        # 工作流变量输出
        yield self.create_variable_message("group_id",       group_id)
        yield self.create_variable_message("nodes_count",     stats["nodes_count"])
        yield self.create_variable_message("rels_count",      stats["rels_count"])
        yield self.create_variable_message("skipped_rels",    stats["skipped_rels"])
        yield self.create_variable_message("node_type_stats", stats["node_type_stats"])
        yield self.create_variable_message("rel_type_stats",  stats["rel_type_stats"])

        # 人类可读输出
        yield self.create_text_message(f"✅ 导入完成！\n\ngroup_id: {group_id}\n{summary}")

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
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        rows = self._split_markdown_rows(excel_text)
        blocks = self._split_rows_into_blocks(rows)
        if not blocks:
            raise ValueError("未检测到 Markdown 表格。")
        return self._parse_blocks_with_mapping(blocks, mapping, source_name="markdown_text")

    # ── 内部：解析 Excel ─────────────────────────────────────────────────

    def _parse_excel(
        self,
        excel_bytes: bytes,
        mapping: dict[str, Any],
        source_name: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        逐行扫描，识别节点表头和关系表头，收集所有行。
        """
        df_raw = pd.read_excel(io.BytesIO(excel_bytes), header=None, dtype=str).fillna("")
        rows = [[str(v or "").strip() for v in r] for r in df_raw.values.tolist()]
        blocks = self._split_rows_into_blocks(rows)
        if not blocks:
            raise ValueError("未检测到 Excel 表格行。")
        return self._parse_blocks_with_mapping(blocks, mapping, source_name=source_name)

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
        return clean_text(value).lower() in _EMPTY_VALUES

    def _is_title_row(self, row: list[str]) -> bool:
        if not row:
            return False
        if self._is_empty_like(row[0]):
            return False
        return all(self._is_empty_like(cell) for cell in row[1:])

    @staticmethod
    def _sanitize_label(text: Any) -> str:
        value = clean_text(text)
        return value or DEFAULT_NODE_LABEL

    def _title_to_label(self, title: str) -> str:
        text = clean_text(title)
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
            text = clean_text(value)
            return [text] if text else []
        if isinstance(value, (list, tuple, set)):
            fields: list[str] = []
            for item in value:
                text = clean_text(item)
                if text:
                    fields.append(text)
            return fields
        text = clean_text(value)
        return [text] if text else []

    def _split_labels_text(self, value: Any) -> list[str]:
        text = clean_text(value)
        if not text or self._is_empty_like(text):
            return []
        parts = [part.strip() for part in _LABEL_SPLIT_PATTERN.split(text) if part and part.strip()]
        labels: list[str] = []
        for part in parts:
            label = self._sanitize_label(_normalize_edge_symbols(part))
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

    @staticmethod
    def _infer_title_level(title: str) -> int | None:
        text = clean_text(title)
        if not text:
            return None
        if _CHINESE_SUBSECTION_PATTERN.match(text):
            return 3
        if _CHINESE_SECTION_PATTERN.match(text):
            return 2
        decimal_match = _DECIMAL_SECTION_PATTERN.match(text)
        if decimal_match:
            segments = [segment for segment in decimal_match.group(1).split(".") if segment]
            return max(2, len(segments) + 1)
        return None

    @staticmethod
    def _normalize_name(name: Any) -> str:
        text = _normalize_edge_symbols(name)
        if not text:
            return ""
        candidate = text
        for _ in range(3):
            updated = _NAME_PREFIX_PATTERN.sub("", candidate, count=1).strip()
            if not updated or updated == candidate:
                break
            candidate = updated
        return _normalize_edge_symbols(candidate or text)

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
            if clean_text(row[idx]) != clean_text(value):
                return False
        return True

    @staticmethod
    def _matches_any(value: str, candidates: list[str]) -> bool:
        text = clean_text(value)
        return any(text == clean_text(candidate) for candidate in candidates)

    def _is_node_header(self, row: list[str], mapping: dict[str, Any]) -> bool:
        node = mapping["node"]
        label_fields = self._normalize_label_fields(node.get("labels"))
        desc_fields = self._normalize_description_fields(node.get("description", node.get("definition")))
        if len(row) < 4:
            return False
        uid_ok = clean_text(row[0]) in {clean_text(node["uid"]), clean_text("uid")}
        name_ok = clean_text(row[1]) in {clean_text(node["name"]), clean_text("name")}
        if not uid_ok or not name_ok:
            return False

        effective_labels = label_fields or ["node_type"]
        if not self._matches_any(row[2], effective_labels):
            return False

        effective_desc_fields = desc_fields or ["description", "definition", "说明", "备注", "简介"]
        return self._matches_any(row[3], effective_desc_fields)

    def _is_rel_header(self, row: list[str], mapping: dict[str, Any]) -> bool:
        rel = mapping["relation"]
        expected = [rel["source_uid"], rel["rel_type"], rel["target_uid"]]
        fallback = ["source_uid", "rel_type", "target_uid"]
        return self._starts_with(row, expected) or self._starts_with(row, fallback)

    @staticmethod
    def _build_index(header: list[str]) -> dict[str, int]:
        result: dict[str, int] = {}
        for i, col in enumerate(header):
            key = clean_text(col)
            if key and key not in result:
                result[key] = i
        return result

    @staticmethod
    def _resolve_field(index: dict[str, int], field: str, fallback: str) -> str:
        """优先使用映射值，index 中不存在时回退到键名本身。"""
        if field in index:
            return field
        return fallback

    def _resolve_fields(self, index: dict[str, int], fields: list[str], fallbacks: list[str]) -> list[str]:
        """合并映射值与回退键名，保持顺序且去重。"""
        resolved: list[str] = []
        seen: set[str] = set()
        for field in fields:
            if field in index and field not in seen:
                resolved.append(field)
                seen.add(field)
        for fb in fallbacks:
            if fb in index and fb not in seen:
                resolved.append(fb)
                seen.add(fb)
        return resolved

    @staticmethod
    def _get_cell(row: list[str], index: dict[str, int], column: str) -> str:
        col_idx = index.get(column)
        if col_idx is None or col_idx >= len(row):
            return ""
        return _normalize_edge_symbols(clean_text(row[col_idx]))

    @staticmethod
    def _normalize_property_value(value: str) -> str:
        return clean_text(value)

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
        source_name: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        node_rows: list[dict[str, Any]] = []
        rel_rows: list[dict[str, Any]] = []
        title_uid_allocator = self._TitleUidAllocator()
        title_first_node_map: dict[str, tuple[str, str]] = {}

        node_map = mapping["node"]
        rel_map = mapping["relation"]
        label_fields = self._normalize_label_fields(node_map.get("labels"))
        desc_fields = self._normalize_description_fields(node_map.get("description", node_map.get("definition")))
        rel_desc_fields = self._normalize_relation_description_fields(rel_map.get("description"))
        node_property_fields = self._normalize_label_fields(node_map.get("properties"))
        rel_property_fields = self._normalize_label_fields(rel_map.get("properties"))

        for table_index, table in enumerate(blocks):
            scope_key = f"TABLE#T{table_index + 1}"
            current_kind: str | None = None
            current_index: dict[str, int] = {}
            current_title_stack: list[tuple[int, str]] = []

            for row_index, raw_row in enumerate(table):
                row = self._normalize_row(raw_row)
                source_row_num = row_index + 1
                if not any(not self._is_empty_like(cell) for cell in row):
                    continue
                if self._is_title_row(row):
                    next_non_empty_row: list[str] | None = None
                    for next_row in table[row_index + 1:]:
                        if any(not self._is_empty_like(cell) for cell in next_row):
                            next_non_empty_row = next_row
                            break
                    if next_non_empty_row and self._is_rel_header(next_non_empty_row, mapping):
                        current_title_stack = []
                        continue
                    raw_title_name = clean_text(row[0])
                    title_name = self._normalize_name(raw_title_name)
                    inferred_level = self._infer_title_level(raw_title_name)
                    if inferred_level is None:
                        if current_title_stack and current_kind is None:
                            title_level = current_title_stack[-1][0] + 1
                        else:
                            title_level = 1
                    else:
                        title_level = inferred_level
                    while current_title_stack and current_title_stack[-1][0] >= title_level:
                        current_title_stack.pop()

                    current_title_uid = title_uid_allocator.allocate(title_level)
                    node_rows.append(
                        {
                            "uid": current_title_uid,
                            "name": title_name,
                            "labels": ["Title"],
                            "group_id": "",
                            "properties": {},
                            "meta": _build_meta(source_name, source_row_num),
                            "_scope_key": scope_key,
                        }
                    )
                    if current_title_stack and current_title_stack[-1][1] != current_title_uid:
                        rel_rows.append(
                            {
                                "source_uid": current_title_stack[-1][1],
                                "rel_type": "包含",
                                "target_uid": current_title_uid,
                                "direction": DEFAULT_DIRECTION,
                                "group_id": "",
                                "properties": {},
                                "meta": _build_meta(source_name, source_row_num),
                                "_scope_key": scope_key,
                            }
                        )
                    current_title_stack.append((title_level, current_title_uid))
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
                    uid_field = self._resolve_field(current_index, node_map["uid"], "uid")
                    name_field = self._resolve_field(current_index, node_map["name"], "name")
                    node_id = _normalize_edge_symbols(self._get_cell(row, current_index, uid_field))
                    if self._is_empty_like(node_id):
                        continue
                    labels: list[str] = []
                    for field in label_fields:
                        for label in self._split_labels_text(self._get_cell(row, current_index, field)):
                            if label not in labels:
                                labels.append(label)
                    if not labels:
                        labels = [DEFAULT_NODE_LABEL]
                    reserved = {uid_field, name_field, *label_fields, *desc_fields}
                    props = self._collect_properties(
                        row=row,
                        index=current_index,
                        configured=node_property_fields,
                        reserved_fields=reserved,
                    )
                    description = self._extract_first_by_fields(row, current_index, desc_fields)
                    node_payload: dict[str, Any] = {
                        "uid": node_id,
                        "name": self._normalize_name(self._get_cell(row, current_index, name_field)),
                        "labels": labels,
                        "group_id": "",
                        "properties": normalize_properties(props, field_name="properties"),
                        "meta": _build_meta(source_name, source_row_num),
                        "_scope_key": scope_key,
                    }
                    if description:
                        node_payload["description"] = description
                    node_rows.append(node_payload)

                    for _, current_title_uid in current_title_stack:
                        if current_title_uid not in title_first_node_map:
                            title_first_node_map[current_title_uid] = (
                                node_payload["uid"],
                                clean_text(node_payload.get("name")),
                            )
                    for _, current_title_uid in current_title_stack:
                        if current_title_uid == node_payload["uid"]:
                            continue
                        rel_rows.append(
                            {
                                "source_uid": current_title_uid,
                                "rel_type": "包含",
                                "target_uid": node_payload["uid"],
                                "direction": DEFAULT_DIRECTION,
                                "group_id": "",
                                "properties": {},
                                "meta": _build_meta(source_name, source_row_num),
                                "_scope_key": scope_key,
                            }
                        )
                    continue

                if current_kind == "relation":
                    src_uid_field = self._resolve_field(current_index, rel_map["source_uid"], "source_uid")
                    rel_type_field = self._resolve_field(current_index, rel_map["rel_type"], "rel_type")
                    tgt_uid_field = self._resolve_field(current_index, rel_map["target_uid"], "target_uid")
                    src = _normalize_edge_symbols(self._get_cell(row, current_index, src_uid_field))
                    rel_type = _normalize_relation_type(self._get_cell(row, current_index, rel_type_field))
                    tgt = _normalize_edge_symbols(self._get_cell(row, current_index, tgt_uid_field))
                    if self._is_empty_like(src) or self._is_empty_like(rel_type) or self._is_empty_like(tgt):
                        continue
                    reserved = {src_uid_field, rel_type_field, tgt_uid_field, *rel_desc_fields}
                    props = self._collect_properties(
                        row=row,
                        index=current_index,
                        configured=rel_property_fields,
                        reserved_fields=reserved,
                    )
                    description = self._extract_first_by_fields(row, current_index, rel_desc_fields)
                    rel_payload: dict[str, Any] = {
                        "source_uid": src,
                        "rel_type": rel_type,
                        "target_uid": tgt,
                        "direction": DEFAULT_DIRECTION,
                        "group_id": "",
                        "properties": normalize_properties(props, field_name="properties"),
                        "meta": _build_meta(source_name, source_row_num),
                        "_scope_key": scope_key,
                    }
                    if description:
                        rel_payload["description"] = description
                    rel_rows.append(rel_payload)
                    for _, current_title_uid in current_title_stack:
                        if current_title_uid == src:
                            continue
                        rel_rows.append(
                            {
                                "source_uid": current_title_uid,
                                "rel_type": "包含",
                                "target_uid": src,
                                "direction": DEFAULT_DIRECTION,
                                "group_id": "",
                                "properties": {},
                                "meta": _build_meta(source_name, source_row_num),
                                "_scope_key": scope_key,
                            }
                        )

        nodes_after_collapse, rels_after_collapse = self._collapse_redundant_titles(
            node_rows,
            rel_rows,
            title_first_node_map,
        )
        renamed_nodes, renamed_rels = self._auto_rename_conflicted_uids(nodes_after_collapse, rels_after_collapse)
        merged_nodes = self._merge_nodes(renamed_nodes)
        merged_rels = self._merge_relations(renamed_rels)
        trimmed_nodes, trimmed_rels = self._prune_single_link_title_to_title_nodes(merged_nodes, merged_rels)
        final_nodes = trimmed_nodes

        for node in final_nodes:
            node.pop("_scope_key", None)
        for rel in trimmed_rels:
            rel.pop("_scope_key", None)

        return final_nodes, trimmed_rels

    @staticmethod
    def _collapse_redundant_titles(
        nodes: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        title_first_node_map: dict[str, tuple[str, str]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        title_name_map: dict[str, str] = {}
        for record in nodes:
            labels = record.get("labels") or []
            if any(clean_text(label) == "Title" for label in labels):
                title_name_map[clean_text(record.get("uid"))] = clean_text(record.get("name"))

        replacement_map: dict[str, str] = {}
        for title_uid, title_name in title_name_map.items():
            first_node = title_first_node_map.get(title_uid)
            if not first_node:
                continue
            first_uid, first_name = first_node
            if first_uid and first_name and title_name and title_name == first_name:
                replacement_map[title_uid] = first_uid

        if not replacement_map:
            return nodes, relations

        rewritten_relations: list[dict[str, Any]] = []
        for record in relations:
            source_uid = replacement_map.get(clean_text(record.get("source_uid")), clean_text(record.get("source_uid")))
            target_uid = replacement_map.get(clean_text(record.get("target_uid")), clean_text(record.get("target_uid")))
            if not source_uid or not target_uid or source_uid == target_uid:
                continue
            record["source_uid"] = source_uid
            record["target_uid"] = target_uid
            rewritten_relations.append(record)

        kept_nodes = [record for record in nodes if clean_text(record.get("uid")) not in replacement_map]
        return kept_nodes, rewritten_relations

    @staticmethod
    def _build_scoped_uid(base_uid: str, scope_key: str) -> str:
        clean_base = clean_text(base_uid) or "NODE"
        marker = "#T"
        idx = scope_key.rfind(marker)
        table_index = 0
        if idx >= 0:
            text = scope_key[idx + len(marker):].strip()
            if text.isdigit():
                table_index = int(text)
        table_suffix = f"T{table_index:02d}" if table_index > 0 else "T00"
        return f"{clean_base}__{table_suffix}"

    def _auto_rename_conflicted_uids(
        self,
        nodes: list[dict[str, Any]],
        relations: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        uid_groups: dict[str, list[dict[str, Any]]] = {}
        for record in nodes:
            uid = clean_text(record.get("uid"))
            uid_groups.setdefault(uid, []).append(record)

        used_uids = {clean_text(record.get("uid")) for record in nodes}
        scope_uid_map: dict[tuple[str, str], str] = {}

        def allocate_uid(old_uid: str, scope_key: str) -> str:
            base_uid = self._build_scoped_uid(old_uid, scope_key)
            candidate = base_uid
            seq = 2
            while candidate in used_uids:
                candidate = f"{base_uid}_{seq}"
                seq += 1
            used_uids.add(candidate)
            return candidate

        for uid, records in uid_groups.items():
            distinct_names: list[str] = []
            for record in records:
                name = clean_text(record.get("name"))
                if name and name not in distinct_names:
                    distinct_names.append(name)
            if len(distinct_names) <= 1:
                continue
            kept_name = clean_text(records[0].get("name"))
            for record in records[1:]:
                current_name = clean_text(record.get("name"))
                if not current_name or current_name == kept_name:
                    continue
                old_uid = clean_text(record.get("uid"))
                scope_key = clean_text(record.get("_scope_key"))
                new_uid = allocate_uid(old_uid, scope_key)
                record["uid"] = new_uid
                scope_uid_map[(scope_key, old_uid)] = new_uid

        for relation in relations:
            scope_key = clean_text(relation.get("_scope_key"))
            for field in ("source_uid", "target_uid"):
                old_uid = clean_text(relation.get(field))
                scoped_new_uid = scope_uid_map.get((scope_key, old_uid))
                if scoped_new_uid:
                    relation[field] = scoped_new_uid
        return nodes, relations

    @staticmethod
    def _merge_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for node in nodes:
            uid = clean_text(node.get("uid"))
            existing = merged.get(uid)
            if existing is None:
                merged[uid] = node
                continue
            existing_labels = existing.get("labels", [])
            for label in node.get("labels", []):
                if label not in existing_labels:
                    existing_labels.append(label)
            existing["labels"] = existing_labels
            if not clean_text(existing.get("name")) and clean_text(node.get("name")):
                existing["name"] = node.get("name")
            if not clean_text(existing.get("description")) and clean_text(node.get("description")):
                existing["description"] = node.get("description")
            existing_props = dict(existing.get("properties") or {})
            for key, value in (node.get("properties") or {}).items():
                if key not in existing_props and clean_text(value):
                    existing_props[key] = value
            existing["properties"] = normalize_properties(existing_props, field_name="properties")
        return list(merged.values())

    @staticmethod
    def _merge_relations(relations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[tuple[str, str, str], dict[str, Any]] = {}
        for relation in relations:
            key = (
                clean_text(relation.get("source_uid")),
                clean_text(relation.get("rel_type")),
                clean_text(relation.get("target_uid")),
            )
            if key not in merged:
                merged[key] = relation
        return list(merged.values())

    @staticmethod
    def _prune_single_link_title_to_title_nodes(
        nodes: list[dict[str, Any]],
        relations: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        title_uids: set[str] = set()
        for node in nodes:
            uid = clean_text(node.get("uid"))
            labels = node.get("labels") or []
            if uid and any(clean_text(label) == "Title" for label in labels):
                title_uids.add(uid)
        if not title_uids:
            return nodes, relations

        incident_map: dict[str, set[int]] = {uid: set() for uid in title_uids}
        for idx, relation in enumerate(relations):
            source_uid = clean_text(relation.get("source_uid"))
            target_uid = clean_text(relation.get("target_uid"))
            if source_uid in incident_map:
                incident_map[source_uid].add(idx)
            if target_uid in incident_map:
                incident_map[target_uid].add(idx)

        remove_title_uids: set[str] = set()
        for title_uid, rel_indexes in incident_map.items():
            if len(rel_indexes) != 1:
                continue
            relation = relations[next(iter(rel_indexes))]
            source_uid = clean_text(relation.get("source_uid"))
            target_uid = clean_text(relation.get("target_uid"))
            other_uid = target_uid if source_uid == title_uid else source_uid
            if other_uid in title_uids:
                remove_title_uids.add(title_uid)

        if not remove_title_uids:
            return nodes, relations

        kept_nodes = [node for node in nodes if clean_text(node.get("uid")) not in remove_title_uids]
        kept_relations = [
            relation for relation in relations
            if clean_text(relation.get("source_uid")) not in remove_title_uids
            and clean_text(relation.get("target_uid")) not in remove_title_uids
        ]
        return kept_nodes, kept_relations

    @staticmethod
    def _prune_orphan_title_nodes(
        nodes: list[dict[str, Any]],
        relations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        degrees: dict[str, int] = {}
        for node in nodes:
            uid = clean_text(node.get("uid"))
            if uid:
                degrees[uid] = 0
        for relation in relations:
            source_uid = clean_text(relation.get("source_uid"))
            target_uid = clean_text(relation.get("target_uid"))
            if source_uid in degrees:
                degrees[source_uid] += 1
            if target_uid in degrees:
                degrees[target_uid] += 1
        kept: list[dict[str, Any]] = []
        for node in nodes:
            uid = clean_text(node.get("uid"))
            labels = node.get("labels") or []
            is_title = any(clean_text(label) == "Title" for label in labels)
            if is_title and degrees.get(uid, 0) == 0:
                continue
            kept.append(node)
        return kept

    # ── 内部：写入 Neo4j ─────────────────────────────────────────────────
    def _write_to_neo4j(
        self,
        parsed_nodes_input: list[dict[str, Any]],
        parsed_relations_input: list[dict[str, Any]],
        uri: str,
        user: str,
        pwd: str,
        *,
        batch_size: int = 500,
        embedding_batch_size: int = 50,
        clear_first: bool = False,
        group_id: str,
        embedding_model: Any = None,
    ) -> Generator[ToolInvokeMessage, None, dict[str, Any]]:
        yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, "🔌 正在连接 Neo4j...\n")
        skipped_rels = 0

        try:
            # 可选：清空
            if clear_first:
                logger.warning("执行清库操作！")
                yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, "⚠️ 已启用 clear_before_import，先执行清库...\n")
                clear_graph(uri, user, pwd, group_id=group_id)

            apoc_nodes, apoc_rels = get_apoc_capabilities(uri, user, pwd)
            logger.info("APOC 可用性 | nodes=%s rels=%s", apoc_nodes, apoc_rels)
            yield self.create_stream_variable_message(
                self._PROGRESS_VARIABLE,
                "🧪 APOC 可用性："
                f"节点标签={'可用' if apoc_nodes else '不可用'}，"
                f"关系合并={'可用' if apoc_rels else '不可用'}。\n"
            )

            # ── 规范化节点 ────────────────────────────────
            parsed_nodes: list[NodePayload] = []
            for row_data in parsed_nodes_input:
                uid = clean_text(row_data.get("uid"))
                if not uid:
                    continue
                labels = normalize_labels(row_data.get("labels"))
                if not labels:
                    labels = [DEFAULT_NODE_LABEL]
                properties = row_data.get("properties")
                if isinstance(properties, dict):
                    properties = normalize_properties(properties, field_name="node.properties")
                else:
                    properties = {}
                parsed_nodes.append(
                    NodePayload(
                        uid=uid,
                        name=clean_text(row_data.get("name")),
                        labels=labels or [DEFAULT_NODE_LABEL],
                        description=clean_text(
                            row_data.get("description")
                            or row_data.get("definition")
                            or row_data.get("说明")
                            or row_data.get("备注")
                            or row_data.get("简介")
                        ),
                        group_id=group_id,
                        properties=properties,
                        meta=cast("GraphMeta", ensure_mapping(row_data.get("meta"), field_name="node.meta") or _build_meta()),
                    )
                )

            if embedding_model:
                yield self.create_stream_variable_message(
                    self._PROGRESS_VARIABLE,
                    f"🧠 开始生成节点向量...\n",
                )
                node_indexes: list[int] = []
                node_texts: list[str] = []
                for index, node in enumerate(parsed_nodes):
                    embedding_text = build_node_embedding_text(node)
                    if not embedding_text:
                        continue
                    node_indexes.append(index)
                    node_texts.append(embedding_text)

                processed_count = 0
                for start in range(0, len(node_texts), embedding_batch_size):
                    text_batch = node_texts[start: start + embedding_batch_size]
                    index_batch = node_indexes[start: start + embedding_batch_size]
                    vectors = generate_embeddings(
                        self.session,
                        model_config=embedding_model,
                        texts=text_batch,
                    )
                    if len(vectors) != len(index_batch):
                        raise ValueError("embedding 返回数量与节点数量不一致。")
                    for local_index, node_index in enumerate(index_batch):
                        parsed_nodes[node_index]["embedding"] = vectors[local_index]
                    processed_count += len(index_batch)
                    yield self.create_stream_variable_message(
                        self._PROGRESS_VARIABLE,
                        f"🧠 向量已生成 {processed_count}/{len(node_texts)} 条。\n",
                    )
                yield self.create_stream_variable_message(
                    self._PROGRESS_VARIABLE,
                    "🧠 节点向量生成完成。\n",
                )

            # ── 规范化关系（新结构） ────────────────────────────────
            parsed_relations: list[RelationPayload] = []
            known_ids = {r["uid"] for r in parsed_nodes}
            for row_data in parsed_relations_input:
                source_uid = clean_text(row_data.get("source_uid"))
                target_uid = clean_text(row_data.get("target_uid"))
                if source_uid not in known_ids or target_uid not in known_ids:
                    skipped_rels += 1
                    logger.warning("跳过关系（节点缺失）: %s → %s", source_uid, target_uid)
                    continue
                rel_type = _normalize_relation_type(row_data.get("rel_type"))
                if not rel_type:
                    continue
                properties = row_data.get("properties")
                if isinstance(properties, dict):
                    properties = normalize_properties(properties, field_name="relation.properties")
                else:
                    properties = {}
                parsed_relations.append(
                    RelationPayload(
                        source_uid=source_uid,
                        target_uid=target_uid,
                        rel_type=rel_type,
                        direction=DEFAULT_DIRECTION,
                        description=clean_text(
                            row_data.get("description")
                            or row_data.get("说明")
                            or row_data.get("备注")
                            or row_data.get("简介")
                        ),
                        group_id=group_id,
                        properties=properties,
                        meta=cast("GraphMeta", ensure_mapping(row_data.get("meta"), field_name="relation.meta") or _build_meta()),
                    )
                )

            node_rows: list[NodePayload] = parsed_nodes
            rel_rows: list[RelationPayload] = parsed_relations

            yield self.create_stream_variable_message(
                self._PROGRESS_VARIABLE,
                f"📦 开始写入节点，共 {len(node_rows)} 条。\n"
            )
            nodes_count = write_nodes(uri, user, pwd, node_rows, batch_size=batch_size)
            yield self.create_stream_variable_message(
                self._PROGRESS_VARIABLE,
                f"📦 节点写入完成：{nodes_count}/{len(node_rows)}。\n"
            )

            yield self.create_stream_variable_message(
                self._PROGRESS_VARIABLE,
                f"🔗 开始写入关系，共 {len(rel_rows)} 条，跳过 {skipped_rels} 条。\n"
            )
            rels_count = write_relations(uri, user, pwd, rel_rows, batch_size=batch_size)
            yield self.create_stream_variable_message(
                self._PROGRESS_VARIABLE,
                f"🔗 关系写入完成：{rels_count}/{len(rel_rows)}。\n"
            )
        except Exception as exc:
            logger.error("写入 Neo4j 失败: %s", exc, exc_info=True)
            raise

        # 统计
        node_type_stats: dict[str, int] = {}
        for node in parsed_nodes:
            labels = node.get("labels") or []
            for label in labels:
                key = str(label).strip()
                if not key:
                    continue
                node_type_stats[key] = node_type_stats.get(key, 0) + 1

        rel_type_stats: dict[str, int] = {}
        for rel in parsed_relations:
            rel_type = str(rel.get("rel_type") or "").strip()
            if not rel_type:
                continue
            rel_type_stats[rel_type] = rel_type_stats.get(rel_type, 0) + 1

        yield self.create_stream_variable_message(
            self._PROGRESS_VARIABLE,
            f"✅ 写入阶段完成：节点 {len(node_rows)}，关系 {len(rel_rows)}，跳过 {skipped_rels}。\n"
        )

        return {
            "nodes_count":     nodes_count,
            "rels_count":      rels_count,
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
