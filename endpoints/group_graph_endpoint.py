from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from werkzeug import Request, Response

from dify_plugin import Endpoint
from dify_plugin.config.logger_format import plugin_logger_handler

from core.embedding_common import (
    build_node_embedding_text,
    generate_embeddings,
    has_embedding_model,
)
from core.graph_export import export_excel, export_json
from core.graph_parser import GraphParser
from core.types import NodePayload, clean_text, normalize_node, utc_now_iso
from endpoints.group_graph_store import GroupGraphStore

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)


class GroupGraphEndpoint(Endpoint):
    """提供 group_id 图谱可视化与 CRUD API。"""

    def _invoke(self, r: Request, values: Mapping, settings: Mapping) -> Response:
        """按 path + method 分发页面渲染与数据操作。"""
        path = r.path
        method = r.method.upper()
        logger.info("GroupGraphEndpoint invoked, method=%s path=%s", method, path)
        store: GroupGraphStore | None = None

        try:
            if path == "/group-graph" and method == "GET":
                return self._render_html(r)

            store = GroupGraphStore(settings)
            if path == "/group-graph/api/graph" and method == "GET":
                return self._query_graph(r, store)

            if path == "/group-graph/api/node" and method == "POST":
                return self._create_node(r, store, settings)
            if path == "/group-graph/api/node" and method == "PUT":
                return self._update_node(r, store, settings)
            if path == "/group-graph/api/node" and method == "DELETE":
                return self._delete_node(r, store)

            if path == "/group-graph/api/relation" and method == "POST":
                return self._create_relation(r, store)
            if path == "/group-graph/api/relation" and method == "PUT":
                return self._update_relation(r, store)
            if path == "/group-graph/api/relation" and method == "DELETE":
                return self._delete_relation(r, store)

            if path == "/group-graph/api/replace-node-relations" and method == "POST":
                return self._replace_node_relations(r, store)

            if path == "/group-graph/api/neighbors" and method == "GET":
                return self._expand_neighbors(r, store)
            if path == "/group-graph/api/path" and method == "GET":
                return self._find_path(r, store)
            if path == "/group-graph/api/stats" and method == "GET":
                return self._get_stats(r, store)
            if path == "/group-graph/api/group" and method == "DELETE":
                return self._clear_group(r, store)

            if path == "/group-graph/api/export" and method == "GET":
                return self._export_data(r, store)
            if path == "/group-graph/api/import" and method == "POST":
                return self._import_data(r, store, settings)
        except Exception as exc:
            logger.exception("endpoint invoke failed")
            return self._json_response({"error": str(exc)}, 500)
        finally:
            if store is not None:
                store.close()

        return self._json_response({"error": f"Unsupported route: {method} {path}"}, 404)

    def _render_html(self, r: Request) -> Response:
        """渲染 D3 页面。"""
        html = Path(__file__).with_name("group_graph.html").read_text(encoding="utf-8")
        return Response(html, status=200, content_type="text/html; charset=utf-8")

    def _query_graph(self, r: Request, store: GroupGraphStore) -> Response:
        """分页查询 group_id 图谱。"""
        group_id = clean_text(r.args.get("group_id"))
        if not group_id:
            return self._json_response({"error": "group_id 不能为空"}, 400)
        page = self._positive_int(r.args.get("page"), default=1)
        page_size = self._limit(r.args.get("page_size"), default=300, max_value=5000)
        node_cursor = clean_text(r.args.get("node_cursor"))
        rel_cursor = clean_text(r.args.get("rel_cursor"))
        return self._json_response(store.query_graph(
            group_id, page, page_size, node_cursor, rel_cursor,
        ), 200)

    def _create_node(self, r: Request, store: GroupGraphStore, settings: Mapping[str, Any]) -> Response:
        """新增节点。"""
        body = self._body_json(r)
        err = self._validate_required(body, ["group_id", "nid"])
        if err:
            return self._json_response({"error": err}, 400)
        try:
            node_payload = self._build_node_payload(body)
            node_payload = self._attach_node_embedding(node_payload, settings)
        except ValueError as exc:
            return self._json_response({"error": str(exc)}, 400)
        node_id = store.create_node(node_payload)
        if not node_id:
            return self._json_response({"error": "节点创建失败"}, 500)
        return self._json_response({"ok": True, "node_id": node_id}, 200)

    def _update_node(self, r: Request, store: GroupGraphStore, settings: Mapping[str, Any]) -> Response:
        """更新节点。"""
        body = self._body_json(r)
        err = self._validate_required(body, ["group_id", "nid"])
        if err:
            return self._json_response({"error": err}, 400)
        try:
            node_payload = self._build_node_payload(body)
            node_payload = self._attach_node_embedding(node_payload, settings)
        except ValueError as exc:
            return self._json_response({"error": str(exc)}, 400)
        node_id = store.update_node(node_payload)
        if not node_id:
            return self._json_response({"error": "未找到可更新节点"}, 404)
        return self._json_response({"ok": True, "node_id": node_id}, 200)

    def _delete_node(self, r: Request, store: GroupGraphStore) -> Response:
        """删除节点。"""
        body = self._body_json(r)
        err = self._validate_required(body, ["group_id", "nid"])
        if err:
            return self._json_response({"error": err}, 400)
        deleted = store.delete_node(body)
        if deleted <= 0:
            return self._json_response({"error": "未找到可删除节点"}, 404)
        return self._json_response({"ok": True, "deleted": deleted}, 200)

    def _create_relation(self, r: Request, store: GroupGraphStore) -> Response:
        """新增关系。"""
        body = self._body_json(r)
        err = self._validate_required(body, ["group_id", "source_nid", "target_nid", "rel_type"])
        if err:
            return self._json_response({"error": err}, 400)
        relation_ref = store.create_relation(body)
        if not relation_ref:
            return self._json_response({"error": "关系创建失败"}, 500)
        return self._json_response({"ok": True, "relation_ref": relation_ref}, 200)

    def _update_relation(self, r: Request, store: GroupGraphStore) -> Response:
        """更新关系。"""
        body = self._body_json(r)
        err = self._validate_required(body, ["group_id", "source_nid", "target_nid", "rel_type"])
        if err:
            return self._json_response({"error": err}, 400)
        relation_ref = store.update_relation(body)
        if not relation_ref:
            return self._json_response({"error": "未找到可更新关系"}, 404)
        return self._json_response({"ok": True, "relation_ref": relation_ref}, 200)

    def _delete_relation(self, r: Request, store: GroupGraphStore) -> Response:
        """删除关系。"""
        body = self._body_json(r)
        err = self._validate_required(body, ["group_id", "source_nid", "target_nid"])
        if err:
            return self._json_response({"error": err}, 400)
        deleted = store.delete_relation(body)
        if deleted <= 0:
            return self._json_response({"error": "未找到可删除关系"}, 404)
        return self._json_response({"ok": True, "deleted": deleted}, 200)

    def _replace_node_relations(self, r: Request, store: GroupGraphStore) -> Response:
        """将 old_nid 节点的全部关系转移至 new_nid 节点。"""
        body = self._body_json(r)
        err = self._validate_required(body, ["group_id", "old_nid", "new_nid"])
        if err:
            return self._json_response({"error": err}, 400)
        group_id = clean_text(body["group_id"])
        old_nid = clean_text(body["old_nid"])
        new_nid = clean_text(body["new_nid"])
        if old_nid == new_nid:
            return self._json_response({"error": "old_nid 和 new_nid 不能相同"}, 400)
        redirected = store.replace_node_relations(group_id, old_nid, new_nid)
        return self._json_response({"ok": True, "redirected": redirected}, 200)

    def _expand_neighbors(self, r: Request, store: GroupGraphStore) -> Response:
        """查询节点的 N 跳邻居。"""
        group_id = clean_text(r.args.get("group_id"))
        nid = clean_text(r.args.get("nid"))
        if not group_id or not nid:
            return self._json_response({"error": "group_id 和 nid 不能为空"}, 400)
        depth = self._limit(r.args.get("depth"), default=1, max_value=5)
        result = store.expand_neighbors(group_id, nid, depth)
        return self._json_response(result, 200)

    def _find_path(self, r: Request, store: GroupGraphStore) -> Response:
        """查找两节点间最短路径。"""
        group_id = clean_text(r.args.get("group_id"))
        source_nid = clean_text(r.args.get("source_nid"))
        target_nid = clean_text(r.args.get("target_nid"))
        if not group_id or not source_nid or not target_nid:
            return self._json_response({"error": "group_id、source_nid、target_nid 不能为空"}, 400)
        result = store.find_path(group_id, source_nid, target_nid)
        return self._json_response(result, 200)

    def _get_stats(self, r: Request, store: GroupGraphStore) -> Response:
        """获取图谱统计摘要。"""
        group_id = clean_text(r.args.get("group_id"))
        if not group_id:
            return self._json_response({"error": "group_id 不能为空"}, 400)
        result = store.get_stats(group_id)
        return self._json_response(result, 200)

    def _clear_group(self, r: Request, store: GroupGraphStore) -> Response:
        """清空指定 group_id 的全部数据。"""
        body = self._body_json(r)
        err = self._validate_required(body, ["group_id"])
        if err:
            return self._json_response({"error": err}, 400)
        group_id = clean_text(body["group_id"])
        deleted = store.clear_group(group_id)
        return self._json_response({"ok": True, "deleted": deleted}, 200)

    def _export_data(self, r: Request, store: GroupGraphStore) -> Response:
        """导出指定 group_id 的全部节点和关系，支持 excel/json 格式。"""
        group_id = clean_text(r.args.get("group_id"))
        if not group_id:
            return self._json_response({"error": "group_id 不能为空"}, 400)
        fmt = clean_text(r.args.get("format") or "excel").lower()
        nodes, relations = store.export_all(group_id)
        for n in nodes:
            n.pop("group_id", None)
        for rel in relations:
            rel.pop("group_id", None)
            rel.pop("id", None)

        if fmt == "json":
            blob = export_json(nodes, relations)
            return Response(
                blob, status=200,
                headers={"Content-Disposition": f'attachment; filename="graph_{group_id}.json"'},
                content_type="application/json; charset=utf-8",
            )
        blob = export_excel(nodes, relations)
        return Response(
            blob, status=200,
            headers={"Content-Disposition": f'attachment; filename="graph_{group_id}.xlsx"'},
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def _import_data(self, r: Request, store: GroupGraphStore, settings: Mapping[str, Any]) -> Response:
        """从上传的 Excel/JSON 文件导入节点和关系到指定 group_id。
        mode=override：清空分组后导入；mode=merge：按 nid UPSERT 合并。
        """
        group_id = clean_text(r.args.get("group_id"))
        mode = clean_text(r.args.get("mode") or "merge").lower()
        if mode not in ("merge", "override"):
            return self._json_response({"error": "mode 必须是 merge 或 override"}, 400)
        if not group_id:
            return self._json_response({"error": "group_id 不能为空"}, 400)

        file_storage = r.files.get("file")
        if not file_storage:
            return self._json_response({"error": "请上传文件（file 字段）"}, 400)
        file_bytes = file_storage.read()
        if not file_bytes:
            return self._json_response({"error": "文件内容为空"}, 400)

        filename = clean_text(file_storage.filename or "")
        mapping = GraphParser.resolve_mapping(None)

        try:
            if filename.endswith(".json"):
                data = json.loads(file_bytes)
                nodes = data.get("nodes", [])
                relations = data.get("relations", [])
            else:
                parser = GraphParser()
                nodes, relations = parser.parse_excel(file_bytes, mapping, source_name=filename)
        except Exception as exc:
            logger.error("Import parse failed: %s", exc)
            return self._json_response({"error": f"解析失败: {exc}"}, 400)

        if mode == "override":
            store.clear_group(group_id)
            result = store.import_all(group_id, nodes, relations, settings)
        else:
            result = store.merge_import(group_id, nodes, relations, settings)
        result["group_id"] = group_id
        result["mode"] = mode
        return self._json_response({"ok": True, **result}, 200)

    def _body_json(self, r: Request) -> dict[str, Any]:
        """读取 JSON body，空 body 返回空对象。"""
        raw = r.get_data(as_text=True)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except Exception:
            raise ValueError("请求体必须是 JSON 对象")
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def _validate_required(self, body: Mapping[str, Any], fields: list[str]) -> str:
        """校验必填字段。"""
        missing: list[str] = []
        for field in fields:
            if not clean_text(body.get(field)):
                missing.append(field)
        if missing:
            return "以下字段不能为空: " + ", ".join(missing)
        return ""

    def _json_response(self, data: dict[str, Any], status: int) -> Response:
        """统一 JSON 响应。"""
        return Response(json.dumps(data, ensure_ascii=False), status=status, content_type="application/json; charset=utf-8")

    def _build_node_payload(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """将请求体归一化为 NodePayload，抽取 meta_* 并刷新 updated_at。"""
        payload = dict(body)
        payload.pop("embedding", None)

        properties = payload.get("properties")
        normalized_properties = dict(properties) if isinstance(properties, Mapping) else {}

        meta_from_properties: dict[str, Any] = {}
        for raw_key in list(normalized_properties.keys()):
            key = clean_text(raw_key)
            if not key.startswith("meta_"):
                continue
            meta_key = clean_text(key[5:])
            value = self._normalize_meta_value(normalized_properties.pop(raw_key))
            if meta_key and value is not None:
                meta_from_properties[meta_key] = value

        merged_meta: dict[str, Any] = dict(meta_from_properties)
        body_meta = payload.get("meta")
        if isinstance(body_meta, Mapping):
            for raw_key, raw_value in body_meta.items():
                meta_key = clean_text(raw_key)
                value = self._normalize_meta_value(raw_value)
                if meta_key and value is not None:
                    merged_meta[meta_key] = value

        payload["properties"] = normalized_properties
        if merged_meta:
            payload["meta"] = merged_meta

        node_payload = dict(normalize_node(payload, index=0))
        if not clean_text(node_payload.get("name")):
            node_payload["name"] = clean_text(node_payload.get("nid"))

        raw_meta = node_payload.get("meta")
        normalized_meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}
        for meta_key, meta_value in merged_meta.items():
            if meta_key not in {"created_at", "updated_at", "source", "version"}:
                normalized_meta[meta_key] = meta_value

        now = utc_now_iso()
        normalized_meta["updated_at"] = now
        if not clean_text(normalized_meta.get("created_at")):
            normalized_meta["created_at"] = now
        node_payload["meta"] = normalized_meta
        return node_payload

    def _attach_node_embedding(self, node_payload: dict[str, Any], settings: Mapping[str, Any]) -> dict[str, Any]:
        """按 endpoint setting 的 embedding_model 自动生成节点向量。"""
        normalized = dict(node_payload)
        normalized.pop("embedding", None)

        embedding_model = settings.get("embedding_model")
        if not has_embedding_model(embedding_model):
            return normalized

        embedding_text = build_node_embedding_text(cast(NodePayload, normalized))
        if not embedding_text:
            return normalized

        vectors = generate_embeddings(
            self.session,
            model_config=embedding_model,
            texts=[embedding_text],
        )
        if vectors:
            normalized["embedding"] = vectors[0]
        return normalized

    def _normalize_meta_value(self, value: Any) -> Any | None:
        """归一化 meta 值：空值丢弃，字符串去空白。"""
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            return text if text else None
        return value

    def _limit(self, value: Any, *, default: int, max_value: int) -> int:
        """安全解析 limit。"""
        if value in (None, ""):
            return default
        try:
            parsed = int(value)
        except Exception:
            return default
        if parsed <= 0:
            return default
        return min(parsed, max_value)

    def _positive_int(self, value: Any, *, default: int) -> int:
        """解析正整数参数。"""
        if value in (None, ""):
            return default
        try:
            parsed = int(value)
        except Exception:
            return default
        return parsed if parsed > 0 else default
