"""
import_graph.py — Dify Tool
将 作文总谱 格式的 Excel 文件导入 Neo4j 知识图谱。

Excel 结构约定
--------------
文件中节点表和关系表交替出现：
  节点表表头：NodeID | name | node_type | definition | level | grade_range | keywords | teaching_tip
  关系表表头：SourceID | RelationType | TargetID [| 说明]

章节标题行（如"一、顶层入口"）和全空行会被自动跳过。
"""

from __future__ import annotations

import io
import json
import logging
import re
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
    ensure_mapping
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)

# ── Cypher 模板 ─────────────────────────────────────────────────────────────

_CONSTRAINT_CYPHER = (
    "CREATE CONSTRAINT IF NOT EXISTS "
    "FOR (n:KnowledgeNode) REQUIRE n.nodeId IS UNIQUE"
)

_UPSERT_NODES = """
UNWIND $rows AS row
MERGE (n:KnowledgeNode {nodeId: row.nodeId})
SET n.name        = row.name,
    n.nodeType    = row.nodeType,
    n.label       = row.label,
    n.definition  = row.definition,
    n.level       = row.level,
    n.gradeRange  = row.gradeRange,
    n.keywords    = row.keywords,
    n.teachingTip = row.teachingTip
"""

# 关系：APOC 版（支持动态关系类型）
_UPSERT_RELS_APOC = """
UNWIND $rows AS row
MATCH (src:KnowledgeNode {nodeId: row.src})
MATCH (tgt:KnowledgeNode {nodeId: row.tgt})
CALL apoc.merge.relationship(src, row.relType, {}, {description: row.desc}, tgt)
YIELD rel RETURN count(rel)
"""

# 关系：通用版（无 APOC；关系类型存为属性）
_UPSERT_RELS_GENERIC = """
UNWIND $rows AS row
MATCH (src:KnowledgeNode {nodeId: row.src})
MATCH (tgt:KnowledgeNode {nodeId: row.tgt})
MERGE (src)-[r:RELATED {relType: row.relType}]->(tgt)
SET r.description = row.desc
"""

# ── 工具类 ──────────────────────────────────────────────────────────────────

class ImportGraphTool(Tool):
    """将 Excel 图谱文件写入 Neo4j。"""

    # 固定表头常量
    _NODE_HEADER = [
        "NodeID", "name", "node_type", "definition",
        "level", "grade_range", "keywords", "teaching_tip",
    ]
    _REL_HEADER_3 = ["SourceID", "RelationType", "TargetID"]
    _PROGRESS_VARIABLE = "summary"

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        tool_parameters = ensure_mapping(tool_parameters, "tool_parameters")
        credentials = ensure_mapping(self.runtime.credentials, "runtime.credentials")

        # ── 1. 参数读取 ──────────────────────────────────────────────────
        excel_url   = str(tool_parameters.get("excel_url") or "").strip()
        excel_files = tool_parameters.get("excel_file")          # dify Files 对象或 File 对象
        excel_text  = str(tool_parameters.get("excel_text") or "").strip()
        batch_size  = int(tool_parameters.get("batch_size") or 500)
        clear_first = bool(tool_parameters.get("clear_before_import", False))

        neo4j_uri  = str(credentials.get("neo4j_uri",  "")).strip()
        neo4j_user = str(credentials.get("neo4j_user", "")).strip()
        neo4j_pwd  = str(credentials.get("neo4j_password", "")).strip()

        logger.info(
            "ImportGraphTool invoked | uri=%s user=%s batch=%d clear=%s",
            neo4j_uri, neo4j_user, batch_size, clear_first,
        )
        yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, "🚀 开始导入图谱任务...\n")

        # ── 2. 参数校验 ──────────────────────────────────────────────────
        if not excel_text and not excel_url and not self._has_uploaded_files(excel_files):
            yield self.create_text_message("❌ 请提供 excel_text、excel_url 或上传 excel_file，三者不能同时为空。")
            return

        # ── 3. 读取并解析图谱 ────────────────────────────────────────────
        if excel_text:
            yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, "🧩 检测到 excel_text，开始解析 Markdown 图谱...\n")
            yield self.create_text_message("⏳ 正在解析 Markdown 图谱文本…")
            try:
                nodes_df, rels_df = self._parse_markdown_tables(excel_text)
            except Exception as exc:
                logger.error("解析 Markdown 文本失败: %s", exc)
                yield self.create_text_message(f"❌ 解析 Markdown 文本失败：{exc}")
                return
        else:
            yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, "📥 开始读取 Excel 文件...\n")
            yield self.create_text_message("⏳ 正在读取 Excel 文件…")
            try:
                excel_bytes = self._load_excel_bytes(excel_url, excel_files)
            except Exception as exc:
                logger.error("读取 Excel 失败: %s", exc)
                yield self.create_text_message(f"❌ 读取 Excel 失败：{exc}")
                return

            yield self.create_stream_variable_message(self._PROGRESS_VARIABLE, "🧩 Excel 已读取，开始解析图谱结构...\n")
            yield self.create_text_message("⏳ 正在解析图谱结构…")
            try:
                nodes_df, rels_df = self._parse_excel(excel_bytes)
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
                batch_size=batch_size, clear_first=clear_first,
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

        # 工作流变量输出
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

    def _load_excel_bytes(self, excel_url: str, excel_files: Any) -> bytes:
        if self._has_uploaded_files(excel_files):
            selected_file = self._pick_first_xlsx_file(excel_files)
            if selected_file is None:
                if not excel_url:
                    raise ValueError("上传文件中未找到 .xlsx 文件，且未提供 excel_url。")
            else:
                # Dify File 对象：优先用 .blob，回退 .url
                if hasattr(selected_file, "blob") and selected_file.blob:
                    return selected_file.blob
                if hasattr(selected_file, "url") and selected_file.url:
                    excel_url = selected_file.url
                else:
                    if not excel_url:
                        raise ValueError("选中的 .xlsx 文件既没有 blob 也没有 url，且未提供 excel_url。")

        if excel_url:
            import urllib.request
            with urllib.request.urlopen(excel_url, timeout=60) as resp:
                return resp.read()

        raise ValueError("无法获取 Excel 数据。")

    @staticmethod
    def _pick_first_xlsx_file(excel_files: Any) -> Any | None:
        if isinstance(excel_files, list):
            candidates = excel_files
        else:
            candidates = [excel_files]

        for file_obj in candidates:
            filename = str(
                getattr(file_obj, "filename", None)
                or getattr(file_obj, "name", None)
                or ""
            ).strip().lower()
            if filename.endswith(".xlsx"):
                return file_obj

        return None

    @staticmethod
    def _has_uploaded_files(excel_files: Any) -> bool:
        if excel_files is None:
            return False
        if isinstance(excel_files, list):
            return len(excel_files) > 0
        return True

    # ── 内部：解析 Markdown 表格 ────────────────────────────────────────

    def _parse_markdown_tables(self, excel_text: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        tables = self._split_markdown_tables(excel_text)
        if not tables:
            raise ValueError("未检测到 Markdown 表格。")

        node_blocks: list[pd.DataFrame] = []
        rel_blocks: list[pd.DataFrame] = []

        for table in tables:
            header = [col.strip() for col in table[0]]
            rows = table[1:]
            if self._is_node_header(header):
                node_rows = []
                for row in rows:
                    if len(row) < 8:
                        continue
                    node_rows.append(
                        {
                            "NodeID": row[0],
                            "name": row[1],
                            "node_type": row[2],
                            "definition": row[3],
                            "level": row[4],
                            "grade_range": row[5],
                            "keywords": row[6],
                            "teaching_tip": row[7],
                        }
                    )
                if node_rows:
                    node_blocks.append(pd.DataFrame(node_rows))
                continue

            if self._is_rel_header(header):
                rel_rows = []
                has_desc = len(header) >= 4 and header[3] == "说明"
                for row in rows:
                    if len(row) < 3:
                        continue
                    rel_rows.append(
                        {
                            "SourceID": row[0],
                            "RelationType": row[1],
                            "TargetID": row[2],
                            "description": row[3] if has_desc and len(row) > 3 else "",
                        }
                    )
                if rel_rows:
                    rel_blocks.append(pd.DataFrame(rel_rows))

        if node_blocks:
            nodes_df = (
                pd.concat(node_blocks, ignore_index=True)
                .drop_duplicates(subset="NodeID")
                .reset_index(drop=True)
            )
        else:
            nodes_df = pd.DataFrame(columns=self._NODE_HEADER)

        if rel_blocks:
            rels_df = (
                pd.concat(rel_blocks, ignore_index=True)
                .drop_duplicates(subset=["SourceID", "RelationType", "TargetID"])
                .reset_index(drop=True)
            )
        else:
            rels_df = pd.DataFrame(columns=["SourceID", "RelationType", "TargetID", "description"])

        return nodes_df, rels_df

    @staticmethod
    def _is_node_header(header: list[str]) -> bool:
        expected = ["NodeID", "name", "node_type", "definition", "level", "grade_range", "keywords", "teaching_tip"]
        return header[: len(expected)] == expected

    @staticmethod
    def _is_rel_header(header: list[str]) -> bool:
        return len(header) >= 3 and header[:3] == ["SourceID", "RelationType", "TargetID"]

    @staticmethod
    def _split_markdown_tables(text: str) -> list[list[list[str]]]:
        lines = [line.rstrip() for line in text.splitlines()]
        blocks: list[list[str]] = []
        current: list[str] = []

        for line in lines:
            if "|" in line:
                current.append(line.strip())
            else:
                if current:
                    blocks.append(current)
                    current = []
        if current:
            blocks.append(current)

        tables: list[list[list[str]]] = []
        for block in blocks:
            parsed_rows: list[list[str]] = []
            for row in block:
                cells = [cell.strip() for cell in row.strip("|").split("|")]
                if not cells:
                    continue
                # 跳过 markdown 分隔行，如 |---|---|
                if all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells):
                    continue
                parsed_rows.append(cells)
            if len(parsed_rows) >= 2:
                tables.append(parsed_rows)

        return tables

    # ── 内部：解析 Excel ─────────────────────────────────────────────────

    def _parse_excel(self, excel_bytes: bytes) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        逐行扫描，识别节点表头和关系表头，收集所有行。
        """
        df_raw = pd.read_excel(io.BytesIO(excel_bytes), header=None, dtype=str)
        df_raw = df_raw.fillna("")

        node_blocks: list[pd.DataFrame] = []
        rel_blocks:  list[pd.DataFrame] = []

        i = 0
        nrows = len(df_raw)

        while i < nrows:
            row_vals = [v for v in df_raw.iloc[i].tolist() if v != ""]

            # ── 节点表 ──────────────────────────────────────────────────
            if row_vals[:len(self._NODE_HEADER)] == self._NODE_HEADER:
                data_rows, j = [], i + 1
                while j < nrows:
                    r = df_raw.iloc[j].tolist()
                    non_empty = [v for v in r if v != ""]
                    if not non_empty:
                        break
                    if non_empty[0] in ("SourceID", "NodeID", "层级"):
                        break
                    data_rows.append(r[:8])
                    j += 1
                if data_rows:
                    node_blocks.append(
                        pd.DataFrame(data_rows, columns=self._NODE_HEADER)
                    )
                i = j
                continue

            # ── 关系表 ──────────────────────────────────────────────────
            if row_vals[:3] == self._REL_HEADER_3:
                has_desc = len(row_vals) >= 4 and row_vals[3] == "说明"
                data_rows, j = [], i + 1
                while j < nrows:
                    r = df_raw.iloc[j].tolist()
                    non_empty = [v for v in r if v != ""]
                    if not non_empty:
                        break
                    if non_empty[0] in (
                        "SourceID", "NodeID", "层级",
                        "一、", "二、", "三、", "四、",
                        "五、", "六、", "七、", "八、",
                    ):
                        break
                    # 跳过章节标题行（第二列为空）
                    if r[1] == "":
                        j += 1
                        continue
                    data_rows.append({
                        "SourceID":     r[0],
                        "RelationType": r[1],
                        "TargetID":     r[2],
                        "description":  r[3] if has_desc and len(r) > 3 else "",
                    })
                    j += 1
                if data_rows:
                    rel_blocks.append(pd.DataFrame(data_rows))
                i = j
                continue

            i += 1

        # 合并 & 去重
        if node_blocks:
            nodes_df = (
                pd.concat(node_blocks, ignore_index=True)
                .drop_duplicates(subset="NodeID")
                .reset_index(drop=True)
            )
        else:
            nodes_df = pd.DataFrame(columns=self._NODE_HEADER)

        if rel_blocks:
            rels_df = (
                pd.concat(rel_blocks, ignore_index=True)
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
                    node_rows.append(node_from_excel_row(row.to_dict()))

                yield self.create_stream_variable_message(
                    self._PROGRESS_VARIABLE,
                    f"📦 开始写入节点，共 {len(node_rows)} 条，批大小 {batch_size}。\n"
                )
                for start in range(0, len(node_rows), batch_size):
                    batch = node_rows[start: start + batch_size]
                    session.run(_UPSERT_NODES, rows=batch)
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
                    src = str(row["SourceID"]).strip()
                    tgt = str(row["TargetID"]).strip()
                    if src not in known_ids or tgt not in known_ids:
                        skipped_rels += 1
                        logger.warning("跳过关系（节点缺失）: %s → %s", src, tgt)
                        continue
                    rel_rows.append(relation_from_excel_row(row.to_dict()))

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
        node_type_stats = (
            nodes_df["node_type"].value_counts().to_dict()
            if "node_type" in nodes_df.columns
            else {}
        )
        rel_type_stats = (
            rels_df["RelationType"].value_counts().to_dict()
            if "RelationType" in rels_df.columns
            else {}
        )

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
