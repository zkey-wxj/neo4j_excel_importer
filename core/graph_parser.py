"""graph_parser.py — Excel / Markdown 图谱解析逻辑。
供 ImportGraphTool / ExtractGraphDataTool 复用，不继承 Tool。
"""
from __future__ import annotations

import io
import re
from collections.abc import Mapping
from typing import Any

import pandas as pd

from core.constants import DEFAULT_DIRECTION, DEFAULT_NODE_LABEL
from core.types import (
    clean_text,
    ensure_mapping,
    normalize_properties,
    utc_now_iso,
)

# ── 常量 / 正则 ────────────────────────────────────────────────────────────

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
    r"""^[\s*＊`'"''\[\]【】\(\)（）{}<>《》,，.;；:：!！?？、|\\/]+|[\s*＊`'"''\[\]【】\(\)（）{}<>《》,，.;；:：!！?？、|\\/]+$"""
)
_REL_TYPE_NORMALIZATION_MAP: dict[str, str] = {
    "包含：": "包含", "包含知识点": "包含", "包含子知识点": "包含",
    "包含层次": "包含", "包含题型": "包含", "包含维度": "包含",
    "包含阶段": "包含", "包含模块": "包含", "包含场景": "包含",
    "包含类型": "包含", "包含线路": "包含", "包含常识": "包含",
    "包含文体节点": "包含", "包含触发点": "包含", "包含方向": "包含",
    "包含学段场所": "包含", "包含子节点": "包含", "包含方法维度": "包含",
    "包含格式规范": "包含", "包含路径": "包含", "包含专项轴": "包含",
    "含有": "包含", "包含方法": "包含", "包括": "包含", "使用": "包含",
    "组成": "包含", "对应阅读能力": "对应", "对应阅读错误": "对应",
    "对应知识点": "对应", "对应立意能力": "对应", "对应维度": "对应",
    "同层对应": "对应", "同源异表": "对应", "同为输入": "对应",
    "调用能力": "调用", "调用阅读能力": "调用", "调用倾听能力": "调用",
    "调用口语能力": "调用", "调用思维": "调用", "调用思维能力": "调用",
    "调用策略": "调用", "调用技法": "调用", "支撑能力": "支撑",
    "支撑方法": "支撑", "支持": "支撑", "支撑写作能力": "支撑",
    "支撑阅读能力": "支撑", "支撑倾听能力": "支撑", "支撑口语能力": "支撑",
    "支撑演绎能力": "支撑", "支撑研学能力": "支撑", "支撑表达策略": "支撑",
    "基础支撑": "支撑", "输入支撑": "支撑", "关联写作": "关联",
    "关联写作能力": "关联", "关联阅读能力": "关联", "关联阅读方法": "关联",
    "关联倾听能力": "关联", "关联口语能力": "关联", "前置能力": "前置",
    "前置学习": "前置", "前置决定": "前置", "前置价值": "前置",
    "驱动": "驱动", "驱动构篇": "驱动", "承接驱动": "承接",
    "承接执行": "承接", "核心指向": "指向", "指向知识点": "指向",
    "指向维度": "指向", "指向写作文笔": "指向", "依赖阅读能力": "依赖",
    "核心依托": "依赖", "重要依托": "依赖", "训练能力": "训练",
    "贯穿": "贯通", "使用模型": "使用", "通过技法实现": "实现",
    "主要使用策略": "使用", "辅助使用策略": "使用", "可拓展至": "拓展",
    "是…的地方变体": "变体",
}


# ── 模块级辅助函数 ─────────────────────────────────────────────────────────

def _build_meta(source: str = "", source_row_num: int | None = None) -> dict[str, Any]:
    now = utc_now_iso()
    return {"created_at": now, "updated_at": now, "source": clean_text(source), "source_row": source_row_num or 0}


def _normalize_edge_symbols(value: Any) -> str:
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
    for prefix in ("包含", "对应", "调用", "关联", "前置", "驱动", "承接", "指向", "依赖", "训练", "适用", "拓展"):
        if text_without_colon.startswith(prefix):
            return prefix if prefix not in ("支撑", "支持") else "支撑"
    if text_without_colon.startswith("支撑") or text_without_colon.startswith("支持"):
        return "支撑"
    return _normalize_edge_symbols(text_without_colon)


# ── 解析类 ─────────────────────────────────────────────────────────────────

class GraphParser:
    """Excel / Markdown 图谱解析逻辑，不继承 Tool。"""

    class _TitleUidAllocator:
        def __init__(self) -> None:
            self._counters: list[int] = []

        def allocate(self, level: int) -> str:
            level = max(1, level)
            if not self._counters:
                self._counters = [0]
            if level > len(self._counters) + 1:
                level = len(self._counters) + 1
            self._counters = self._counters[:level]
            if len(self._counters) < level:
                self._counters.extend([0] * (level - len(self._counters)))
            self._counters[level - 1] += 1
            return f"TITLE_{'_'.join(f'{c:02d}' for c in self._counters)}"

    # ── 公开解析入口 ──────────────────────────────────────────────────────

    def load_excel_bytes(self, excel_url: str) -> bytes:
        import urllib.request
        with urllib.request.urlopen(excel_url, timeout=60) as resp:
            return resp.read()

    def parse_markdown_tables(
        self, text: str, mapping: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        rows = self._split_markdown_rows(text)
        blocks = self._split_rows_into_blocks(rows)
        if not blocks:
            raise ValueError("未检测到 Markdown 表格。")
        return self._parse_blocks_with_mapping(blocks, mapping, source_name="markdown_text")

    def parse_excel(
        self, excel_bytes: bytes, mapping: dict[str, Any], source_name: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        raw_sheets = pd.read_excel(io.BytesIO(excel_bytes), header=None, dtype=str, sheet_name=None)
        sheets = {name: df.fillna("") for name, df in raw_sheets.items()}
        return self._parse_multi_sheets(sheets, mapping, source_name)

    def _parse_multi_sheets(
        self, sheets: dict[str, pd.DataFrame], mapping: dict[str, Any], source_name: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """将所有 sheet 的行上下拼接后，复用原有块解析逻辑。"""
        all_blocks: list[list[list[str]]] = []
        for df in sheets.values():
            rows = [[str(v or "").strip() for v in r] for r in df.values.tolist()]
            all_blocks.extend(self._split_rows_into_blocks(rows))
        if not all_blocks:
            raise ValueError("未检测到 Excel 表格行。")
        return self._parse_blocks_with_mapping(all_blocks, mapping, source_name=source_name)

    @staticmethod
    def resolve_mapping(mapping: Any, default: dict[str, Any] | None = None) -> dict[str, Any]:
        fallback = default if default is not None else DEFAULT_FIELD_MAPPING
        if mapping is None:
            return fallback
        if isinstance(mapping, str):
            mapping = mapping.strip()
            if not mapping:
                return fallback
            return dict(ensure_mapping(mapping, field_name="mapping"))
        return dict(ensure_mapping(mapping, field_name="mapping"))

    # ── 内部：解析辅助 ────────────────────────────────────────────────────

    @staticmethod
    def _normalize_row(row: list[Any]) -> list[str]:
        return [str(cell or "").strip() for cell in row]

    @staticmethod
    def _is_empty_like(value: str) -> bool:
        return clean_text(value).lower() in _EMPTY_VALUES

    def _is_title_row(self, row: list[str]) -> bool:
        if not row or self._is_empty_like(row[0]):
            return False
        return all(self._is_empty_like(c) for c in row[1:])

    @staticmethod
    def _sanitize_label(text: Any) -> str:
        return clean_text(text) or DEFAULT_NODE_LABEL

    @staticmethod
    def _normalize_label_fields(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            t = clean_text(value)
            return [t] if t else []
        if isinstance(value, (list, tuple, set)):
            return [clean_text(item) for item in value if clean_text(item)]
        t = clean_text(value)
        return [t] if t else []

    def _split_labels_text(self, value: Any) -> list[str]:
        text = clean_text(value)
        if not text or self._is_empty_like(text):
            return []
        labels: list[str] = []
        for part in _LABEL_SPLIT_PATTERN.split(text):
            label = self._sanitize_label(_normalize_edge_symbols(part.strip()))
            if label and label not in labels:
                labels.append(label)
        return labels

    def _normalize_description_fields(self, value: Any) -> list[str]:
        fields = self._normalize_label_fields(value)
        return fields if fields else ["description", "definition", "说明", "备注", "简介"]

    def _normalize_relation_description_fields(self, value: Any) -> list[str]:
        fields = self._normalize_label_fields(value)
        return fields if fields else ["description", "说明", "备注", "简介"]

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
        m = _DECIMAL_SECTION_PATTERN.match(text)
        if m:
            return max(2, len([s for s in m.group(1).split(".") if s]) + 1)
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
        rows: list[list[str]] = []
        for line in text.splitlines():
            line = line.rstrip()
            if "|" not in line:
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not cells or all(re.fullmatch(r":?-{3,}:?", c or "") for c in cells):
                continue
            rows.append(cells)
        return rows

    def _split_rows_into_blocks(self, rows: list[list[str]]) -> list[list[list[str]]]:
        blocks: list[list[list[str]]] = []
        current: list[list[str]] = []
        for row in rows:
            normalized = self._normalize_row(row)
            if all(self._is_empty_like(c) for c in normalized):
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
        row_set = {clean_text(c) for c in row}
        return all(clean_text(v) in row_set for v in expected)

    @staticmethod
    def _matches_any(value: str, candidates: list[str]) -> bool:
        t = clean_text(value)
        return any(t == clean_text(c) for c in candidates)

    def _is_node_header(self, row: list[str], mapping: dict[str, Any]) -> bool:
        node = mapping["node"]
        if len(row) < 4:
            return False
        uid_ok = clean_text(row[0]) in {clean_text(node["uid"]), clean_text("uid")}
        name_ok = clean_text(row[1]) in {clean_text(node["name"]), clean_text("name")}
        
        
        if not uid_ok or not name_ok:
            return False
        label_fields = self._normalize_label_fields(node.get("labels"))
        if not self._matches_any(row[2], label_fields or ["node_type", "labels"]):
            return False
        
        return True

    def _is_rel_header(self, row: list[str], mapping: dict[str, Any]) -> bool:
        rel = mapping["relation"]
        return self._starts_with(row, [rel["source_uid"], rel["rel_type"], rel["target_uid"]]) \
            or self._starts_with(row, ["source_uid", "rel_type", "target_uid"])

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
        return field if field in index else fallback

    @staticmethod
    def _get_cell(row: list[str], index: dict[str, int], column: str) -> str:
        col_idx = index.get(column)
        if col_idx is None or col_idx >= len(row):
            return ""
        return _normalize_edge_symbols(clean_text(row[col_idx]))

    def _collect_properties(
        self, row: list[str], index: dict[str, int], configured: list[str], reserved_fields: set[str],
    ) -> dict[str, Any]:
        props: dict[str, Any] = {}
        for field in configured:
            if field == "*":
                for col, col_idx in index.items():
                    if col in reserved_fields or col.startswith("Unnamed:"):
                        continue
                    value = row[col_idx].strip() if col_idx < len(row) else ""
                    if self._is_empty_like(value) or col in props:
                        continue
                    props[col] = clean_text(value)
                continue
            value = self._get_cell(row, index, field)
            if not self._is_empty_like(value):
                props[field] = clean_text(value)
        return props

    # ── 内部：主解析流程 ──────────────────────────────────────────────────

    def _parse_blocks_with_mapping(
        self, blocks: list[list[list[str]]], mapping: dict[str, Any], source_name: str,
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
                if not any(not self._is_empty_like(c) for c in row):
                    continue
                if self._is_title_row(row):
                    next_non_empty: list[str] | None = None
                    for nr in table[row_index + 1:]:
                        if any(not self._is_empty_like(c) for c in nr):
                            next_non_empty = nr
                            break
                    if next_non_empty and self._is_rel_header(next_non_empty, mapping):
                        current_title_stack = []
                        continue
                    raw_name = clean_text(row[0])
                    title_name = self._normalize_name(raw_name)
                    inferred = self._infer_title_level(raw_name)
                    if inferred is None:
                        level = current_title_stack[-1][0] + 1 if current_title_stack and current_kind is None else 1
                    else:
                        level = inferred
                    while current_title_stack and current_title_stack[-1][0] >= level:
                        current_title_stack.pop()
                    uid = title_uid_allocator.allocate(level)
                    node_rows.append({"uid": uid, "name": title_name, "labels": ["Title"],
                                      "group_id": "", "properties": {}, "meta": _build_meta(source_name, source_row_num), "_scope_key": scope_key})
                    if current_title_stack and current_title_stack[-1][1] != uid:
                        rel_rows.append({"source_uid": current_title_stack[-1][1], "rel_type": "包含",
                                         "target_uid": uid, "direction": DEFAULT_DIRECTION, "group_id": "",
                                         "properties": {}, "meta": _build_meta(source_name, source_row_num), "_scope_key": scope_key})
                    current_title_stack.append((level, uid))
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
                    for f in label_fields:
                        for lb in self._split_labels_text(self._get_cell(row, current_index, f)):
                            if lb not in labels:
                                labels.append(lb)
                    if not labels:
                        labels = [DEFAULT_NODE_LABEL]
                    reserved = {uid_field, name_field, *label_fields, *desc_fields}
                    props = self._collect_properties(row, current_index, node_property_fields, reserved)
                    desc = self._extract_first_by_fields(row, current_index, desc_fields)
                    payload: dict[str, Any] = {
                        "uid": node_id, "name": self._normalize_name(self._get_cell(row, current_index, name_field)),
                        "labels": labels, "group_id": "", "properties": normalize_properties(props, field_name="properties"),
                        "meta": _build_meta(source_name, source_row_num), "_scope_key": scope_key,
                    }
                    if desc:
                        payload["description"] = desc
                    node_rows.append(payload)
                    for _, tuid in current_title_stack:
                        if tuid not in title_first_node_map:
                            title_first_node_map[tuid] = (payload["uid"], clean_text(payload.get("name")))
                    for _, tuid in current_title_stack:
                        if tuid != payload["uid"]:
                            rel_rows.append({"source_uid": tuid, "rel_type": "包含", "target_uid": payload["uid"],
                                             "direction": DEFAULT_DIRECTION, "group_id": "", "properties": {},
                                             "meta": _build_meta(source_name, source_row_num), "_scope_key": scope_key})
                    continue

                if current_kind == "relation":
                    src_f = self._resolve_field(current_index, rel_map["source_uid"], "source_uid")
                    rt_f = self._resolve_field(current_index, rel_map["rel_type"], "rel_type")
                    tgt_f = self._resolve_field(current_index, rel_map["target_uid"], "target_uid")
                    src = _normalize_edge_symbols(self._get_cell(row, current_index, src_f))
                    rt = _normalize_relation_type(self._get_cell(row, current_index, rt_f))
                    tgt = _normalize_edge_symbols(self._get_cell(row, current_index, tgt_f))
                    if self._is_empty_like(src) or self._is_empty_like(rt) or self._is_empty_like(tgt):
                        continue
                    reserved = {src_f, rt_f, tgt_f, *rel_desc_fields}
                    props = self._collect_properties(row, current_index, rel_property_fields, reserved)
                    desc = self._extract_first_by_fields(row, current_index, rel_desc_fields)
                    rp: dict[str, Any] = {
                        "source_uid": src, "rel_type": rt, "target_uid": tgt,
                        "direction": DEFAULT_DIRECTION, "group_id": "",
                        "properties": normalize_properties(props, field_name="properties"),
                        "meta": _build_meta(source_name, source_row_num), "_scope_key": scope_key,
                    }
                    if desc:
                        rp["description"] = desc
                    rel_rows.append(rp)
                    for _, tuid in current_title_stack:
                        if tuid != src:
                            rel_rows.append({"source_uid": tuid, "rel_type": "包含", "target_uid": src,
                                             "direction": DEFAULT_DIRECTION, "group_id": "", "properties": {},
                                             "meta": _build_meta(source_name, source_row_num), "_scope_key": scope_key})

        n1, r1 = self._collapse_redundant_titles(node_rows, rel_rows, title_first_node_map)
        n2, r2 = self._auto_rename_conflicted_uids(n1, r1)
        n3 = self._merge_nodes(n2)
        r3 = self._merge_relations(r2)
        n4, r4 = self._prune_single_link_title_to_title_nodes(n3, r3)
        for n in n4:
            n.pop("_scope_key", None)
        for r in r4:
            r.pop("_scope_key", None)
        return n4, r4

    # ── 内部：后处理 ──────────────────────────────────────────────────────

    @staticmethod
    def _collapse_redundant_titles(
        nodes: list[dict[str, Any]], relations: list[dict[str, Any]], title_first_node_map: dict[str, tuple[str, str]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        title_name_map = {clean_text(n.get("uid")): clean_text(n.get("name")) for n in nodes if any(clean_text(l) == "Title" for l in (n.get("labels") or []))}
        repl: dict[str, str] = {}
        for tuid, tname in title_name_map.items():
            first = title_first_node_map.get(tuid)
            if first and first[0] and first[1] and tname and tname == first[1]:
                repl[tuid] = first[0]
        if not repl:
            return nodes, relations
        rewritten = []
        for r in relations:
            s = repl.get(clean_text(r.get("source_uid")), clean_text(r.get("source_uid")))
            t = repl.get(clean_text(r.get("target_uid")), clean_text(r.get("target_uid")))
            if not s or not t or s == t:
                continue
            r["source_uid"], r["target_uid"] = s, t
            rewritten.append(r)
        return [n for n in nodes if clean_text(n.get("uid")) not in repl], rewritten

    @staticmethod
    def _build_scoped_uid(base_uid: str, scope_key: str) -> str:
        clean_base = clean_text(base_uid) or "NODE"
        idx = scope_key.rfind("#T")
        ti = 0
        if idx >= 0:
            txt = scope_key[idx + 2:].strip()
            if txt.isdigit():
                ti = int(txt)
        return f"{clean_base}__T{ti:02d}"

    def _auto_rename_conflicted_uids(
        self, nodes: list[dict[str, Any]], relations: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        uid_groups: dict[str, list[dict[str, Any]]] = {}
        for n in nodes:
            uid_groups.setdefault(clean_text(n.get("uid")), []).append(n)
        used = {clean_text(n.get("uid")) for n in nodes}
        scope_map: dict[tuple[str, str], str] = {}

        def alloc(old: str, sk: str) -> str:
            base = self._build_scoped_uid(old, sk)
            c, seq = base, 2
            while c in used:
                c, seq = f"{base}_{seq}", seq + 1
            used.add(c)
            return c

        for uid, recs in uid_groups.items():
            names = list(dict.fromkeys(clean_text(r.get("name")) for r in recs if clean_text(r.get("name"))))
            if len(names) <= 1:
                continue
            for r in recs[1:]:
                cn = clean_text(r.get("name"))
                if cn and cn != clean_text(recs[0].get("name")):
                    new = alloc(clean_text(r.get("uid")), clean_text(r.get("_scope_key")))
                    r["uid"] = new
                    scope_map[(clean_text(r.get("_scope_key")), clean_text(recs[0].get("uid")) if False else uid)] = new
        for rel in relations:
            sk = clean_text(rel.get("_scope_key"))
            for field in ("source_uid", "target_uid"):
                mapped = scope_map.get((sk, clean_text(rel.get(field))))
                if mapped:
                    rel[field] = mapped
        return nodes, relations

    @staticmethod
    def _merge_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for n in nodes:
            uid = clean_text(n.get("uid"))
            if uid not in merged:
                merged[uid] = n
                continue
            e = merged[uid]
            for l in n.get("labels", []):
                if l not in e.get("labels", []):
                    e.setdefault("labels", []).append(l)
            if not clean_text(e.get("name")) and clean_text(n.get("name")):
                e["name"] = n.get("name")
            if not clean_text(e.get("description")) and clean_text(n.get("description")):
                e["description"] = n.get("description")
            ep = dict(e.get("properties") or {})
            for k, v in (n.get("properties") or {}).items():
                if k not in ep and clean_text(v):
                    ep[k] = v
            e["properties"] = normalize_properties(ep, field_name="properties")
        return list(merged.values())

    @staticmethod
    def _merge_relations(relations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[tuple[str, str, str], dict[str, Any]] = {}
        for r in relations:
            key = (clean_text(r.get("source_uid")), clean_text(r.get("rel_type")), clean_text(r.get("target_uid")))
            if key not in merged:
                merged[key] = r
        return list(merged.values())

    @staticmethod
    def _prune_single_link_title_to_title_nodes(
        nodes: list[dict[str, Any]], relations: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        title_uids = {clean_text(n.get("uid")) for n in nodes if clean_text(n.get("uid")) and any(clean_text(l) == "Title" for l in (n.get("labels") or []))}
        if not title_uids:
            return nodes, relations
        incident: dict[str, set[int]] = {u: set() for u in title_uids}
        for i, r in enumerate(relations):
            s, t = clean_text(r.get("source_uid")), clean_text(r.get("target_uid"))
            if s in incident:
                incident[s].add(i)
            if t in incident:
                incident[t].add(i)
        remove = set()
        for uid, idxs in incident.items():
            if len(idxs) != 1:
                continue
            r = relations[next(iter(idxs))]
            other = clean_text(r.get("target_uid")) if clean_text(r.get("source_uid")) == uid else clean_text(r.get("source_uid"))
            if other in title_uids:
                remove.add(uid)
        if not remove:
            return nodes, relations
        return (
            [n for n in nodes if clean_text(n.get("uid")) not in remove],
            [r for r in relations if clean_text(r.get("source_uid")) not in remove and clean_text(r.get("target_uid")) not in remove],
        )
