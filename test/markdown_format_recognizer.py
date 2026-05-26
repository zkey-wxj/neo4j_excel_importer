from __future__ import annotations

import argparse
import json
import re
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None


# 测试脚本内置映射（不读取 YAML）
DEFAULT_MAPPING: dict[str, Any] = {
    "node": {
        "nid": "NodeID",
        "name": "name",
        "labels": ["node_type", "keywords"],
        "description": ["definition", "description", "说明", "备注", "简介"],
        "properties": ["level", "grade_range", "keywords", "teaching_tip"],
    },
    "relation": {
        "source_nid": "SourceID",
        "rel_type": "RelationType",
        "target_nid": "TargetID",
        "description": ["description", "说明", "备注", "简介"],
        "properties": [],
    },
    "group_id": "group_id",
}

TITLE_LABEL = "Title"
CONTAINS_REL = "包含"
SPLIT_PATTERN = re.compile(r"[;,，；]+")


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _build_meta() -> dict[str, Any]:
    now = _iso_utc_now()
    return {
        "created_at": now,
        "updated_at": now,
    }


def _norm_header(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    text = re.sub(r"\s+", "", text)
    return text.lower()


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def _split_label_values(raw: str) -> list[str]:
    if not raw:
        return []
    parts = [p.strip() for p in SPLIT_PATTERN.split(raw) if p and p.strip()]
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if part not in seen:
            seen.add(part)
            out.append(part)
    return out


def _normalize_labels(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, (list, tuple, set)):
        out = []
        for item in raw:
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    text = str(raw).strip()
    return [text] if text else []


@dataclass
class TableStats:
    index: int
    kind: str
    row_count: int
    node_header_count: int
    relation_header_count: int
    title_row_count: int


class Recognizer:
    def __init__(self, mapping: dict[str, Any] | None = None) -> None:
        self.mapping = mapping or DEFAULT_MAPPING
        self.nodes: dict[str, dict[str, Any]] = {}
        self.relations: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.tables: list[TableStats] = []

        self.node_map = self.mapping.get("node", {})
        self.rel_map = self.mapping.get("relation", {})

        self.node_id_col = self.node_map.get("nid", "NodeID")
        self.node_name_col = self.node_map.get("name", "name")
        self.node_labels_cols = _normalize_labels(self.node_map.get("labels", self.node_map.get("primary_label", "node_type")))
        self.node_desc_cols = _normalize_labels(
            self.node_map.get("description", self.node_map.get("definition", "description"))
        )
        self.node_prop_cols = self.node_map.get("properties", ["*"])

        self.rel_src_col = self.rel_map.get("source_nid", "SourceID")
        self.rel_type_col = self.rel_map.get("rel_type", "RelationType")
        self.rel_tgt_col = self.rel_map.get("target_nid", "TargetID")
        self.rel_desc_cols = _normalize_labels(self.rel_map.get("description", ["description", "说明", "备注", "简介"]))
        self.rel_prop_cols = self.rel_map.get("properties", ["*"])

        self.group_id_col = self.mapping.get("group_id", "group_id")

    def parse_markdown(self, content: str) -> dict[str, Any]:
        rows = self._parse_markdown_rows(content)
        self._parse_row_block(rows, table_index=0)
        return self._build_result()

    def parse_excel(self, file_path: str) -> dict[str, Any]:
        if pd is None:
            raise RuntimeError("未安装 pandas，无法解析 Excel")
        xl = pd.ExcelFile(file_path)
        table_index = 0
        for sheet_name in xl.sheet_names:
            df = xl.parse(sheet_name=sheet_name, header=None)
            rows = [[_safe_text(c) for c in row] for row in df.values.tolist()]
            if not rows:
                continue
            self._parse_row_block(rows, table_index=table_index)
            table_index += 1
        if table_index == 0:
            self.tables.append(TableStats(0, "unknown", 0, 0, 0, 0))
        return self._build_result()

    def _parse_markdown_rows(self, content: str) -> list[list[str]]:
        rows: list[list[str]] = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped.startswith("|"):
                continue
            parts = [p.strip() for p in stripped.split("|")[1:-1]]
            if not parts:
                continue
            if self._is_separator_row(parts):
                continue
            rows.append(parts)
        return rows

    def _is_separator_row(self, parts: list[str]) -> bool:
        if not parts:
            return False
        for part in parts:
            raw = part.replace(":", "").replace("-", "").strip()
            if raw:
                return False
        return True

    def _parse_row_block(self, rows: list[list[str]], table_index: int) -> None:
        if not rows:
            self.tables.append(TableStats(table_index, "unknown", 0, 0, 0, 0))
            return

        node_header_count = 0
        relation_header_count = 0
        title_row_count = 0

        current_title_node_id: str | None = None
        active_node_header: list[str] | None = None
        active_rel_header: list[str] | None = None
        active_mode: str | None = None

        for row in rows:
            if not row:
                continue

            padded = self._pad_row(row, 8)

            if self._looks_like_header(padded, self.node_id_col, self.node_name_col):
                active_node_header = padded
                active_rel_header = None
                active_mode = "node"
                node_header_count += 1
                continue

            if self._looks_like_header(padded, self.rel_src_col, self.rel_tgt_col):
                active_rel_header = padded
                active_node_header = None
                active_mode = "relation"
                relation_header_count += 1
                continue

            if self._is_title_row(padded):
                title_row_count += 1
                title_name = _safe_text(padded[0])
                if title_name:
                    current_title_node_id = self._ensure_title_node(title_name)
                continue

            if active_mode == "relation" and active_rel_header is not None:
                row_dict = self._row_to_dict(active_rel_header, padded)
                rel = self._build_relation(row_dict)
                if rel:
                    self._upsert_relation(rel)
                    continue

            if active_mode == "node" and active_node_header is not None:
                row_dict = self._row_to_dict(active_node_header, padded)
                node = self._build_node(row_dict)
                if node:
                    self._upsert_node(node)
                    if current_title_node_id and current_title_node_id != node["nid"]:
                        self._upsert_relation(
                            {
                                "source_nid": current_title_node_id,
                                "rel_type": CONTAINS_REL,
                                "target_nid": node["nid"],
                                "direction": "forward",
                                "group_id": node.get("group_id", ""),
                                "properties": {},
                                "meta": _build_meta(),
                            }
                        )
                    continue

            if active_rel_header is not None and active_mode is None:
                row_dict = self._row_to_dict(active_rel_header, padded)
                rel = self._build_relation(row_dict)
                if rel:
                    self._upsert_relation(rel)
                    continue

        kind = "mixed"
        if node_header_count > 0 and relation_header_count == 0:
            kind = "node"
        elif node_header_count == 0 and relation_header_count > 0:
            kind = "relation"

        self.tables.append(
            TableStats(
                index=table_index,
                kind=kind,
                row_count=len(rows),
                node_header_count=node_header_count,
                relation_header_count=relation_header_count,
                title_row_count=title_row_count,
            )
        )

    def _pad_row(self, row: list[str], min_len: int) -> list[str]:
        out = [_safe_text(x) for x in row]
        if len(out) < min_len:
            out.extend([""] * (min_len - len(out)))
        return out

    def _looks_like_header(self, row: list[str], required_a: str, required_b: str) -> bool:
        headers = {_norm_header(x) for x in row if _safe_text(x)}
        return _norm_header(required_a) in headers and _norm_header(required_b) in headers

    def _is_title_row(self, row: list[str]) -> bool:
        first = _safe_text(row[0])
        if not first:
            return False
        for cell in row[1:]:
            if _safe_text(cell):
                return False
        return True

    def _row_to_dict(self, header: list[str], row: list[str]) -> dict[str, str]:
        data: dict[str, str] = {}
        max_len = max(len(header), len(row))
        for i in range(max_len):
            key = _safe_text(header[i]) if i < len(header) else ""
            val = _safe_text(row[i]) if i < len(row) else ""
            if key:
                data[key] = val
        return data

    def _extract_by_header(self, data: dict[str, str], header_name: str) -> str:
        target = _norm_header(header_name)
        if not target:
            return ""
        for k, v in data.items():
            if _norm_header(k) == target:
                return _safe_text(v)
        return ""

    def _extract_first_by_headers(self, data: dict[str, str], headers: list[str]) -> str:
        for header in headers:
            value = self._extract_by_header(data, header)
            if value:
                return value
        return ""

    def _collect_properties(self, data: dict[str, str], explicit: list[str], reserved: set[str]) -> dict[str, Any]:
        props: dict[str, Any] = {}
        include_all = any(str(x).strip() == "*" for x in explicit)

        for col in explicit:
            col_name = str(col).strip()
            if not col_name or col_name == "*":
                continue
            value = self._extract_by_header(data, col_name)
            if value:
                props[col_name] = value

        if include_all:
            for k, v in data.items():
                key = _safe_text(k)
                val = _safe_text(v)
                if not key or not val:
                    continue
                if _norm_header(key) in reserved:
                    continue
                if key not in props:
                    props[key] = val

        return props

    def _build_node(self, data: dict[str, str]) -> dict[str, Any] | None:
        node_id = self._extract_by_header(data, self.node_id_col)
        name = self._extract_by_header(data, self.node_name_col)
        if not node_id and not name:
            return None

        if not node_id:
            node_id = f"auto_{uuid.uuid4().hex[:12]}"

        label_values: list[str] = []
        for field in self.node_labels_cols:
            raw = self._extract_by_header(data, field)
            if raw:
                label_values.extend(_split_label_values(raw))

        dedup_labels: list[str] = []
        label_seen: set[str] = set()
        for item in label_values:
            if item and item not in label_seen:
                label_seen.add(item)
                dedup_labels.append(item)
        if not dedup_labels:
            dedup_labels = ["Node"]

        description = self._extract_first_by_headers(data, self.node_desc_cols)
        group_id = self._extract_by_header(data, self.group_id_col)

        reserved = {
            _norm_header(self.node_id_col),
            _norm_header(self.node_name_col),
            _norm_header(self.group_id_col),
        }
        for field in self.node_desc_cols:
            reserved.add(_norm_header(field))
        for field in self.node_labels_cols:
            reserved.add(_norm_header(field))

        props = self._collect_properties(data, self.node_prop_cols, reserved)

        node: dict[str, Any] = {
            "nid": node_id,
            "name": name,
            "labels": dedup_labels,
            "group_id": group_id,
            "properties": props,
            "meta": _build_meta(),
        }
        if description:
            node["description"] = description
        return node

    def _build_relation(self, data: dict[str, str]) -> dict[str, Any] | None:
        src = self._extract_by_header(data, self.rel_src_col)
        rel_type = self._extract_by_header(data, self.rel_type_col)
        tgt = self._extract_by_header(data, self.rel_tgt_col)
        if not src or not rel_type or not tgt:
            return None

        description = self._extract_first_by_headers(data, self.rel_desc_cols)
        group_id = self._extract_by_header(data, self.group_id_col)

        reserved = {
            _norm_header(self.rel_src_col),
            _norm_header(self.rel_type_col),
            _norm_header(self.rel_tgt_col),
            _norm_header(self.group_id_col),
        }
        for field in self.rel_desc_cols:
            reserved.add(_norm_header(field))
        props = self._collect_properties(data, self.rel_prop_cols, reserved)

        relation: dict[str, Any] = {
            "source_nid": src,
            "target_nid": tgt,
            "rel_type": rel_type,
            "direction": "forward",
            "group_id": group_id,
            "properties": props,
            "meta": _build_meta(),
        }
        if description:
            relation["description"] = description
        return relation

    def _ensure_title_node(self, title: str) -> str:
        existing = self._find_title_node_id(title)
        if existing:
            return existing

        node_id = f"title_{uuid.uuid4().hex[:12]}"
        self._upsert_node(
            {
                "nid": node_id,
                "name": title,
                "labels": [TITLE_LABEL],
                "group_id": "",
                "properties": {},
                "meta": _build_meta(),
            }
        )
        return node_id

    def _find_title_node_id(self, title: str) -> str | None:
        for node_id, node in self.nodes.items():
            labels = node.get("labels") or []
            if node.get("name") == title and TITLE_LABEL in labels:
                return node_id
        return None

    def _upsert_node(self, node: dict[str, Any]) -> None:
        node_id = node["nid"]
        existing = self.nodes.get(node_id)
        if not existing:
            self.nodes[node_id] = node
            return

        if node.get("name") and not existing.get("name"):
            existing["name"] = node["name"]
        if node.get("description") and not existing.get("description"):
            existing["description"] = node["description"]
        if node.get("group_id") and not existing.get("group_id"):
            existing["group_id"] = node["group_id"]
        merged_labels = (existing.get("labels") or []) + (node.get("labels") or [])
        dedup_labels: list[str] = []
        seen: set[str] = set()
        for item in merged_labels:
            if item and item not in seen:
                seen.add(item)
                dedup_labels.append(item)
        existing["labels"] = dedup_labels or ["Node"]

        props = existing.get("properties") or {}
        for k, v in (node.get("properties") or {}).items():
            if k not in props and v:
                props[k] = v
        existing["properties"] = props

    def _upsert_relation(self, relation: dict[str, Any]) -> None:
        key = (relation["source_nid"], relation["rel_type"], relation["target_nid"])
        existing = self.relations.get(key)
        if not existing:
            self.relations[key] = relation
            return

        if relation.get("group_id") and not existing.get("group_id"):
            existing["group_id"] = relation["group_id"]
        if relation.get("description") and not existing.get("description"):
            existing["description"] = relation["description"]
        if relation.get("direction") and not existing.get("direction"):
            existing["direction"] = relation["direction"]

        props = existing.get("properties") or {}
        for k, v in (relation.get("properties") or {}).items():
            if k not in props and v:
                props[k] = v
        existing["properties"] = props

    def _build_result(self) -> dict[str, Any]:
        table_dump = [
            {
                "index": t.index,
                "kind": t.kind,
                "row_count": t.row_count,
                "node_header_count": t.node_header_count,
                "relation_header_count": t.relation_header_count,
                "title_row_count": t.title_row_count,
            }
            for t in self.tables
        ]
        return {
            "table_count": len(self.tables),
            "node_count": len(self.nodes),
            "relation_count": len(self.relations),
            "mapping": self.mapping,
            "tables": table_dump,
            "nodes": list(self.nodes.values()),
            "relations": list(self.relations.values()),
        }


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _detect_input_type(path: Path, input_type: str) -> str:
    if input_type != "auto":
        return input_type
    ext = path.suffix.lower()
    if ext in {".md", ".markdown"}:
        return "markdown"
    if ext in {".xlsx", ".xls"}:
        return "excel"
    return "markdown"


def main() -> None:
    parser = argparse.ArgumentParser(description="Markdown/Excel 图谱格式识别测试脚本")
    parser.add_argument("--file", required=True, help="输入文件路径，支持 .md/.xlsx/.xls")
    parser.add_argument(
        "--input-type",
        default="auto",
        choices=["auto", "markdown", "excel"],
        help="输入类型，默认 auto",
    )
    parser.add_argument("--output", default="result.json", help="输出 JSON 文件路径")
    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    recognizer = Recognizer(mapping=DEFAULT_MAPPING)
    kind = _detect_input_type(file_path, args.input_type)

    if kind == "excel":
        result = recognizer.parse_excel(str(file_path))
    else:
        content = _read_text(file_path)
        result = recognizer.parse_markdown(content)

    output_path = Path(args.output)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
