from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from collections.abc import Mapping
from functools import lru_cache
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


TOKEN_TTL_SECONDS = 7 * 24 * 3600
TOKEN_HEADER = "Authorization"
TOKEN_SCHEME = "Bearer"
MAX_TOKEN_LENGTH = 100
TOKEN_KEY_SALT = b"neo4j-graph-endpoint-token-v1"
TOKEN_KEY_ITERATIONS = 200_000

# 路由常量：分发逻辑与页面渲染共用同一份字面量，避免路径字符串散落在判断里
GRAPH_PAGE_PATH = "/group-graph"
AUTH_API_PATH = "/group-graph/api/auth"
GRAPH_API_PATH = "/group-graph/api/graph"
NODE_API_PATH = "/group-graph/api/node"
RELATION_API_PATH = "/group-graph/api/relation"
REPLACE_RELATIONS_API_PATH = "/group-graph/api/replace-node-relations"
NEIGHBORS_API_PATH = "/group-graph/api/neighbors"
SHORTEST_PATH_API_PATH = "/group-graph/api/path"
STATS_API_PATH = "/group-graph/api/stats"
GROUP_API_PATH = "/group-graph/api/group"
EXPORT_API_PATH = "/group-graph/api/export"
IMPORT_API_PATH = "/group-graph/api/import"

# 登录冷却：所有来源共享同一个计数器，因为 Dify 网关下无法区分真实客户端来源。
# 上限 3 秒只用于钝化暴力尝试，不构成定向爆破防护，并会短暂影响他人的登录尝试。
AUTH_FAILURE_DELAY_STEP = 0.5
AUTH_FAILURE_DELAY_MAX = 3.0


@lru_cache(maxsize=8)
def _derive_token_key(access_password: str) -> bytes:
    """把访问密码派生为 HMAC 密钥。

    用 PBKDF2-HMAC-SHA256 而非单轮哈希，避免令牌被拿到后离线高速爆破密码；
    结果按密码缓存，免去每个请求重复迭代。
    """
    return hashlib.pbkdf2_hmac(
        "sha256", access_password.encode("utf-8"), TOKEN_KEY_SALT, TOKEN_KEY_ITERATIONS,
    )


_AUTH_FAILURE_LOCK = threading.Lock()
_AUTH_FAILURE_STATE = {"count": 0, "block_until": 0.0}


def _auth_retry_after() -> int:
    """返回登录接口还需等待的秒数；0 表示当前可以尝试。"""
    with _AUTH_FAILURE_LOCK:
        remaining = _AUTH_FAILURE_STATE["block_until"] - time.time()
    return int(remaining) + 1 if remaining > 0 else 0


def _register_auth_failure() -> None:
    """记录一次密码校验失败，按连续失败次数叠加登录冷却。"""
    with _AUTH_FAILURE_LOCK:
        _AUTH_FAILURE_STATE["count"] += 1
        delay = min(
            _AUTH_FAILURE_STATE["count"] * AUTH_FAILURE_DELAY_STEP, AUTH_FAILURE_DELAY_MAX,
        )
        _AUTH_FAILURE_STATE["block_until"] = time.time() + delay


def _reset_auth_failures() -> None:
    """密码校验成功后清零失败计数。"""
    with _AUTH_FAILURE_LOCK:
        _AUTH_FAILURE_STATE["count"] = 0
        _AUTH_FAILURE_STATE["block_until"] = 0.0


class GroupGraphEndpoint(Endpoint):
    """提供 group_id 图谱可视化与 CRUD API。"""

    def _invoke(self, r: Request, values: Mapping, settings: Mapping) -> Response:
        """按 path + method 分发页面渲染与数据操作。"""
        path = r.path
        method = r.method.upper()
        logger.info("GroupGraphEndpoint invoked, method=%s path=%s", method, path)
        store: GroupGraphStore | None = None

        try:
            access_password = clean_text(settings.get("access_password"))

            if path == AUTH_API_PATH:
                return self._handle_auth(r, method, access_password)

            if access_password and not (path == GRAPH_PAGE_PATH and method == "GET"):
                if not self._is_token_valid(r, access_password):
                    logger.warning(
                        "GroupGraphEndpoint rejected unauthorized request, method=%s path=%s",
                        method, path,
                    )
                    return self._json_response(
                        {"error": "未授权：请先输入访问密码", "auth_required": True}, 401,
                    )

            if path == GRAPH_PAGE_PATH and method == "GET":
                return self._render_html(r)

            store = GroupGraphStore(settings)
            if path == GRAPH_API_PATH and method == "GET":
                return self._query_graph(r, store)

            if path == NODE_API_PATH and method == "POST":
                return self._create_node(r, store, settings)
            if path == NODE_API_PATH and method == "PUT":
                return self._update_node(r, store, settings)
            if path == NODE_API_PATH and method == "DELETE":
                return self._delete_node(r, store)

            if path == RELATION_API_PATH and method == "POST":
                return self._create_relation(r, store)
            if path == RELATION_API_PATH and method == "PUT":
                return self._update_relation(r, store)
            if path == RELATION_API_PATH and method == "DELETE":
                return self._delete_relation(r, store)

            if path == REPLACE_RELATIONS_API_PATH and method == "POST":
                return self._replace_node_relations(r, store)

            if path == NEIGHBORS_API_PATH and method == "GET":
                return self._expand_neighbors(r, store)
            if path == SHORTEST_PATH_API_PATH and method == "GET":
                return self._find_path(r, store)
            if path == STATS_API_PATH and method == "GET":
                return self._get_stats(r, store)
            if path == GROUP_API_PATH and method == "DELETE":
                return self._clear_group(r, store)

            if path == EXPORT_API_PATH and method == "GET":
                return self._export_data(r, store)
            if path == IMPORT_API_PATH and method == "POST":
                return self._import_data(r, store, settings)
        except Exception as exc:
            logger.exception("endpoint invoke failed")
            return self._json_response({"error": str(exc)}, 500)
        finally:
            if store is not None:
                store.close()

        return self._json_response({"error": f"Unsupported route: {method} {path}"}, 404)

    def _handle_auth(self, r: Request, method: str, access_password: str) -> Response:
        """访问密码探测与登录：GET 报告是否需要鉴权，POST 校验密码并签发令牌。"""
        if method == "GET":
            return self._json_response({
                "auth_required": bool(access_password),
                "authorized": bool(access_password) and self._is_token_valid(r, access_password),
            }, 200)

        if method == "POST":
            if not access_password:
                return self._json_response({"ok": True, "token": ""}, 200)

            retry_after = _auth_retry_after()
            if retry_after > 0:
                return self._json_response(
                    {"error": f"尝试过于频繁，请 {retry_after} 秒后重试"},
                    429,
                    headers={"Retry-After": str(retry_after)},
                )

            body = self._body_json(r)
            if not hmac.compare_digest(clean_text(body.get("password")), access_password):
                logger.warning("GroupGraphEndpoint auth failed, path=%s", r.path)
                _register_auth_failure()
                return self._json_response({"error": "访问密码错误", "auth_required": True}, 401)

            _reset_auth_failures()
            return self._json_response({"ok": True, "token": self._issue_token(access_password)}, 200)

        return self._json_response({"error": f"Unsupported route: {method} {r.path}"}, 404)

    def _is_token_valid(self, r: Request, access_password: str) -> bool:
        """校验 Authorization 头中的 Bearer 令牌：格式正确、签名一致且未过期。"""
        scheme, _, raw_token = clean_text(r.headers.get(TOKEN_HEADER)).partition(" ")
        token = clean_text(raw_token)
        # 非 Bearer 形式或超长令牌直接拒绝，避免把无上限的字符串送进签名比较
        if scheme.lower() != TOKEN_SCHEME.lower() or not token or len(token) > MAX_TOKEN_LENGTH:
            return False
        raw_expires_at, _, signature = token.partition(".")
        if not raw_expires_at or not signature:
            return False
        try:
            expires_at = int(raw_expires_at)
        except ValueError:
            return False
        if expires_at < int(time.time()):
            return False
        return hmac.compare_digest(signature, self._sign_token(access_password, expires_at))

    def _issue_token(self, access_password: str) -> str:
        """签发无状态令牌 {过期时间戳}.{HMAC 签名}，改密码即旧令牌全部失效。"""
        expires_at = int(time.time()) + TOKEN_TTL_SECONDS
        return f"{expires_at}.{self._sign_token(access_password, expires_at)}"

    @staticmethod
    def _sign_token(access_password: str, expires_at: int) -> str:
        """以 PBKDF2 派生的密钥，对过期时间戳做 HMAC-SHA256。"""
        return hmac.new(
            _derive_token_key(access_password), str(expires_at).encode("utf-8"), hashlib.sha256,
        ).hexdigest()

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
        page_size = self._limit(r.args.get("page_size"), default=500, max_value=5000)
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

    def _json_response(
        self, data: dict[str, Any], status: int, headers: Mapping[str, str] | None = None,
    ) -> Response:
        """统一 JSON 响应；headers 用于 Retry-After 之类的附加头。"""
        return Response(
            json.dumps(data, ensure_ascii=False),
            status=status,
            headers=dict(headers or {}),
            content_type="application/json; charset=utf-8",
        )

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
