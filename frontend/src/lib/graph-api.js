import { clean, relStoreKey, genDemoNodes, genDemoLinks } from "./graph-store";

/* ═══════════════════════════════════════════════════════════════════
 GraphAPI – HTTP 通信层

 负责与后端服务进行所有网络交互，包括：
 - req():           统一的 fetch 请求封装，自动拼接插件基础路径
 - loadGroup():     按分组图谱加载，支持游标分页，每页回调通知 UI 增量渲染
 - loadDemoPaged(): 生成模拟数据并模拟分页加载效果
 - mutate():        执行增删改操作，并同步更新前端 store

 基础路径自动检测：从 window.location.pathname 匹配 /e/{plugin_id} 格式
 ═══════════════════════════════════════════════════════════════════ */

export class GraphAPI {
  /**
   * @param {import('./graph-store').GraphStore} store - 底层图数据存储实例
   */
  constructor(store) {
    this.store = store;
    // 自动检测插件基础路径：/e/{plugin_id}
    const m = typeof window !== "undefined" ? window.location.pathname.match(/^\/e\/[^/]+/) : null;
    this.base = m ? m[0] : "";
  }

  /** 将相对路径拼接上插件基础路径，生成完整的请求 URL */
  url(p) {
    return this.base.replace(/\/+$/, "") + (String(p).startsWith("/") ? p : `/${p}`);
  }

  /**
   * 统一的 HTTP 请求封装
   * 自动处理 JSON 解析、错误抛出，以及 multipart 表单数据的特殊处理
   * @param {string} path - 请求路径（相对路径或绝对 URL）
   * @param {object} [opt] - 配置项：{ method, body, formData }
   *   - method: HTTP 方法，默认 GET
   *   - body: 已 JSON.stringify 的请求体
   *   - formData: FormData 对象（用于文件上传，会自动移除 Content-Type 让浏览器设置 boundary）
   * @returns {Promise<object>} 解析后的 JSON 响应
   * @throws {Error} 接口返回非 JSON 或 HTTP 状态码非 2xx 时抛出异常
   */
  async req(path, opt = {}) {
    const m = (opt.method || "GET").toUpperCase();
    const u = /^https?:\/\//.test(path) ? path : this.url(path);
    const init = { method: m, headers: { Accept: "application/json" } };

    if (opt.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = opt.body;
    }

    // multipart 表单数据：移除 Content-Type，让浏览器自动设置 boundary 分隔符
    if (opt.formData) {
      delete init.headers["Content-Type"];
      init.body = opt.formData;
    }

    const res = await fetch(u, init);
    const txt = await res.text();
    let d = {};
    if (txt) {
      try {
        d = JSON.parse(txt);
      } catch {
        throw new Error(`接口返回非 JSON: ${txt.slice(0, 120)}`);
      }
    }
    if (!res.ok) throw new Error(d.error || `HTTP ${res.status}`);
    return d;
  }

  /**
   * 按分组 ID 加载图谱数据，支持游标分页
   * 逐页获取节点和关系数据，每页加载完成后通过 onPage 回调通知 UI 更新
   *
   * @param {string} gid - 分组 ID（group_id）
   * @param {number} pageSize - 每页条目数
   * @param {function} [onPage] - 每页加载完成的回调函数，参数为 (graphData, pageNum)
   * @returns {Promise<{nodes: object[], links: object[]}>} 最终的图谱数据
   */
  async loadGroup(gid, pageSize, onPage) {
    this.store.reset();
    const ps = Number(clean(pageSize) || "300");
    let nc = "",
      rc = "",
      pg = 0,
      hasMore = true;
    const base = `/group-graph/api/graph?group_id=${encodeURIComponent(gid)}&page_size=${ps}`;

    while (hasMore) {
      pg++;
      let url = base;
      if (nc) url += `&node_cursor=${encodeURIComponent(nc)}`;
      if (rc) url += `&rel_cursor=${encodeURIComponent(rc)}`;

      const d = await this.req(url);

      this.store.addNodes(d.nodes || []);
      this.store.addLinks(d.relations || []);

      nc = d.next_node_cursor || "";
      rc = d.next_rel_cursor || "";

      if (!d.nodes_has_more && !d.relations_has_more) hasMore = false;

      const g = this.store.mapGraphData();
      if (onPage) onPage(g, pg);
    }

    return this.store.mapGraphData();
  }

  /**
   * 生成并加载示例图谱数据，模拟分页加载效果
   * 创建 1000 个节点和关联关系，每 200 条为一页，页间延迟 2 秒模拟网络加载
   *
   * @param {function} [onPage] - 每页加载完成的回调函数，参数为 (graphData, pageNum)
   * @returns {Promise<{nodes: object[], links: object[]}>} 最终的图谱数据
   */
  async loadDemoPaged(onPage) {
    this.store.reset();
    const dn = genDemoNodes(1000);
    const dl = genDemoLinks(dn);
    const total = Math.max(dn.length, dl.length);
    let pg = 1;

    for (let s = 0; s < total; s += 200) {
      const e = s + 200;
      this.store.addNodes(dn.slice(s, e));
      this.store.addLinks(dl.slice(s, e));
      const g = this.store.mapGraphData();
      if (onPage) onPage(g, pg);
      pg++;
      // 模拟网络延迟，每页间隔 2 秒
      await new Promise((r) => setTimeout(r, 2000));
    }

    return this.store.mapGraphData();
  }

  /**
   * 执行 CRUD 增删改操作（POST/PUT/DELETE）
   * 先向后端发送请求，成功后将变更同步到前端 store，实现乐观更新
   *
   * @param {string} path - API 端点路径
   * @param {string} method - HTTP 方法（POST/PUT/DELETE）
   * @param {object} payload - 请求体对象
   * @returns {Promise<object|null>} 变更后的图谱数据，失败时返回 null
   */
  async mutate(path, method, payload) {
    const result = await this.req(path, { method, body: JSON.stringify(payload) });
    if (result.ok !== true) {
      return null;
    }

    if (path.includes("/replace-node-relations")) {
      this.store.applyReplaceRelations(payload.old_nid);
      // 关系替换操作：清空旧节点的所有关系，然后刷新第一页获取后端最新关系数据
      await this._refreshAfterReplace();
    } else {
      this.store.applyMutation(path, method, payload);
    }

    const g = this.store.mapGraphData();
    return g;
  }

  /**
   * 关系替换后的轻量刷新
   * 因为 replace-node-relations 接口不返回新的关系数据，
   * 所以需要重新请求第一页来获取后端新增的关系
   * @private
   */
  async _refreshAfterReplace() {
    // 需要 group_id 才能刷新；如果 store 中没有节点则无需操作
    const anyNode = this.store.nodeMap.values().next().value;
    if (!anyNode?.group_id) return;

    const gid = anyNode.group_id;
    const ps = 300;
    const d = await this.req(`/group-graph/api/graph?group_id=${encodeURIComponent(gid)}&page=1&page_size=${ps}`);

    let added = 0;
    for (const r of d.relations || []) {
      const k = relStoreKey(r);
      if (k && !this.store.linkMap.has(k)) {
        this.store.linkMap.set(k, r);
        added++;
      }
    }

    if (added > 0) this.store.mapGraphData();
  }

  /**
   * 通过 multipart 表单上传文件（Excel/JSON）到后端进行导入
   * @param {string} gid - 分组 ID（group_id）
   * @param {File} file - 待上传的文件对象
   * @param {string} mode - 导入模式：'merge'（合并）或 'override'（覆盖）
   * @returns {Promise<object>} 服务端响应（含 nodes_imported、relations_imported、relations_skipped 等统计字段）
   */
  async importFile(gid, file, mode) {
    const fd = new FormData();
    fd.append("file", file);
    const url = `/group-graph/api/import?group_id=${encodeURIComponent(gid)}&mode=${mode}`;
    return this.req(url, { method: "POST", formData: fd });
  }
}
