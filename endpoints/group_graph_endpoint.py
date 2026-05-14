from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from werkzeug import Request, Response

from dify_plugin import Endpoint
from dify_plugin.config.logger_format import plugin_logger_handler

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

        try:
            if path == "/group-graph" and method == "GET":
                return self._render_html(r)

            store = GroupGraphStore(settings)
            if path == "/group-graph/api/graph" and method == "GET":
                return self._query_graph(r, store)

            if path == "/group-graph/api/node" and method == "POST":
                return self._create_node(r, store)
            if path == "/group-graph/api/node" and method == "PUT":
                return self._update_node(r, store)
            if path == "/group-graph/api/node" and method == "DELETE":
                return self._delete_node(r, store)

            if path == "/group-graph/api/relation" and method == "POST":
                return self._create_relation(r, store)
            if path == "/group-graph/api/relation" and method == "PUT":
                return self._update_relation(r, store)
            if path == "/group-graph/api/relation" and method == "DELETE":
                return self._delete_relation(r, store)
        except Exception as exc:
            logger.exception("endpoint invoke failed")
            return self._json_response({"error": str(exc)}, 500)

        return self._json_response({"error": f"Unsupported route: {method} {path}"}, 404)

    def _render_html(self, r: Request) -> Response:
        """渲染 D3 页面，并注入 root_path。"""
        html = Path(__file__).with_name("group_graph.html").read_text(encoding="utf-8")
        root_path_json = json.dumps(self._clean(r.root_path))
        html = html.replace("__ROOT_PATH_JSON__", root_path_json)
        return Response(html, status=200, content_type="text/html; charset=utf-8")

    def _query_graph(self, r: Request, store: GroupGraphStore) -> Response:
        """查询 group_id 图谱。"""
        group_id = self._clean(r.args.get("group_id"))
        if not group_id:
            return self._json_response({"error": "group_id 不能为空"}, 400)
        limit = self._limit(r.args.get("limit"), default=500, max_value=5000)
        return self._json_response(store.query_graph(group_id, limit), 200)

    def _create_node(self, r: Request, store: GroupGraphStore) -> Response:
        """新增节点。"""
        body = self._body_json(r)
        err = self._validate_required(body, ["group_id", "uid"])
        if err:
            return self._json_response({"error": err}, 400)
        node_id = store.create_node(body)
        if not node_id:
            return self._json_response({"error": "节点创建失败"}, 500)
        return self._json_response({"ok": True, "node_id": node_id}, 200)

    def _update_node(self, r: Request, store: GroupGraphStore) -> Response:
        """更新节点。"""
        body = self._body_json(r)
        err = self._validate_required(body, ["group_id", "uid"])
        if err:
            return self._json_response({"error": err}, 400)
        node_id = store.update_node(body)
        if not node_id:
            return self._json_response({"error": "未找到可更新节点"}, 404)
        return self._json_response({"ok": True, "node_id": node_id}, 200)

    def _delete_node(self, r: Request, store: GroupGraphStore) -> Response:
        """删除节点。"""
        body = self._body_json(r)
        err = self._validate_required(body, ["group_id", "uid"])
        if err:
            return self._json_response({"error": err}, 400)
        deleted = store.delete_node(body)
        if deleted <= 0:
            return self._json_response({"error": "未找到可删除节点"}, 404)
        return self._json_response({"ok": True, "deleted": deleted}, 200)

    def _create_relation(self, r: Request, store: GroupGraphStore) -> Response:
        """新增关系。"""
        body = self._body_json(r)
        err = self._validate_required(body, ["group_id", "source_uid", "target_uid", "rel_type"])
        if err:
            return self._json_response({"error": err}, 400)
        relation_id = store.create_relation(body)
        if not relation_id:
            return self._json_response({"error": "关系创建失败"}, 500)
        return self._json_response({"ok": True, "relation_id": relation_id}, 200)

    def _update_relation(self, r: Request, store: GroupGraphStore) -> Response:
        """更新关系。"""
        body = self._body_json(r)
        err = self._validate_required(body, ["relation_id"])
        if err:
            return self._json_response({"error": err}, 400)
        relation_id = store.update_relation(body)
        if not relation_id:
            return self._json_response({"error": "未找到可更新关系"}, 404)
        return self._json_response({"ok": True, "relation_id": relation_id}, 200)

    def _delete_relation(self, r: Request, store: GroupGraphStore) -> Response:
        """删除关系。"""
        body = self._body_json(r)
        err = self._validate_required(body, ["relation_id"])
        if err:
            return self._json_response({"error": err}, 400)
        deleted = store.delete_relation(body)
        if deleted <= 0:
            return self._json_response({"error": "未找到可删除关系"}, 404)
        return self._json_response({"ok": True, "deleted": deleted}, 200)

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
            if not self._clean(body.get(field)):
                missing.append(field)
        if missing:
            return "以下字段不能为空: " + ", ".join(missing)
        return ""

    def _json_response(self, data: dict[str, Any], status: int) -> Response:
        """统一 JSON 响应。"""
        return Response(json.dumps(data, ensure_ascii=False), status=status, content_type="application/json; charset=utf-8")

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()

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
