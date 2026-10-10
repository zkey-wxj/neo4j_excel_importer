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

/** sessionStorage 中保存访问令牌的键名 */
const TOKEN_KEY = "graph-access-token";

/** 读取本地保存的访问令牌 */
function readToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

/** 保存后端签发的访问令牌 */
function saveToken(token) {
  try {
    sessionStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* 隐私模式等写入失败场景忽略 */
  }
}

/** 清除本地访问令牌 */
function clearToken() {
  try {
    sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* 忽略 */
  }
}

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
   * 自动处理令牌注入、JSON 解析、错误抛出，以及 multipart 表单数据的特殊处理
   * @param {string} path - 请求路径（相对路径或绝对 URL）
   * @param {object} [opt] - 配置项：{ method, body, formData, responseType }
   *   - method: HTTP 方法，默认 GET
   *   - body: 已 JSON.stringify 的请求体
   *   - formData: FormData 对象（用于文件上传，会自动移除 Content-Type 让浏览器设置 boundary）
   *   - responseType: 传 "blob" 时返回二进制 Blob（导出下载用），默认返回解析后的 JSON
   * @returns {Promise<object|Blob>} 解析后的 JSON 响应，或 responseType 为 "blob" 时的 Blob
   * @throws {Error} HTTP 状态码非 2xx 时抛出错（错误对象带 status，401 或响应体带 auth_required 时带 authRequired）；
   *   JSON 响应体无法解析时抛出
   */
  async req(path, opt = {}) {
    const m = (opt.method || "GET").toUpperCase();
    const u = /^https?:\/\//.test(path) ? path : this.url(path);
    const init = { method: m, headers: { Accept: "application/json" } };
    const token = readToken();
    // 已通过访问校验时，统一携带令牌
    if (token) init.headers["Authorization"] = `Bearer ${token}`;

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

    // 二进制下载（导出接口）：错误响应仍是 JSON，交给统一错误处理
    if (opt.responseType === "blob") {
      if (!res.ok) throw this._errorFrom(res, await res.text());
      return res.blob();
    }

    const txt = await res.text();
    // 先判 HTTP 状态再解析：401 的响应体不一定是 JSON（网关可能注入错误页），
    // 只有交给 _errorFrom 才能清掉令牌并退回密码门
    if (!res.ok) throw this._errorFrom(res, txt);

    let d = {};
    if (txt) {
      try {
        d = JSON.parse(txt);
      } catch {
        throw new Error(`接口返回非 JSON: ${txt.slice(0, 120)}`);
      }
    }
    return d;
  }

  /**
   * 由响应构造错误对象
   * 401 或响应体带 auth_required 时清除本地令牌并打上标记，由上层退回密码门
   */
  _errorFrom(res, txt) {
    let d = {};
    if (txt) {
      try {
        d = JSON.parse(txt);
      } catch {
        /* 非 JSON 错误体，忽略 */
      }
    }
    const err = new Error(d.error || `HTTP ${res.status}`);
    err.status = res.status;
    if (res.status === 401 || d.auth_required) {
      clearToken();
      err.authRequired = true;
    }
    return err;
  }

  /** 探测是否需要访问密码，以及本地令牌是否仍然有效 */
  async checkAuth() {
    return this.req("/group-graph/api/auth");
  }

  /** 提交访问密码：成功后保存后端签发的令牌 */
  async login(password) {
    const d = await this.req("/group-graph/api/auth", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
    if (d.token) saveToken(d.token);
    return d;
  }

  /** 退出登录：令牌为无状态签名，只需清除本地副本 */
  logout() {
    clearToken();
  }

  /**
   * 下载当前分组的导出文件（Excel / JSON）
   * 走统一 req 通道以携带访问令牌，再以 <a download> 触发保存
   * @param {string} gid - 分组 ID
   * @param {'excel'|'json'} [format] - 导出格式，默认 excel
   */
  async exportFile(gid, format = "excel") {
    const fmt = format === "json" ? "json" : "excel";
    const path = `/group-graph/api/export?group_id=${encodeURIComponent(gid)}&format=${fmt}`;
    const blob = await this.req(path, { responseType: "blob" });
    this._downloadBlob(blob, `graph_${gid}.${fmt === "json" ? "json" : "xlsx"}`);
  }

  /** 触发浏览器保存 Blob（blob 下载不会采用 Content-Disposition 文件名，故本地拼接） */
  _downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    // 延迟释放：部分浏览器在 click 后立刻 revoke，会导致下载尚未开始就被回收
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  /**
   * 按分组 ID 加载图谱数据，支持游标分页
   * 逐页获取节点和关系数据，每页加载完成后通过 onPage 回调通知 UI 更新
   *
   * @param {string} gid - 分组 ID（group_id）
   * @param {number} pageSize - 每页条目数
   * @param {function} [onPage] - 每页加载完成的回调函数，签名为 (graphData, pageNum, meta)，
   *   meta 含 page / totalNodes / totalRels / loadedNodes / loadedRels（后端未提供总数时 total 为 -1）
   * @returns {Promise<{nodes: object[], links: object[]}>} 最终的图谱数据
   */
  async loadGroup(gid, pageSize, onPage) {
    this.store.reset();
    const ps = Number(clean(pageSize) || "500");
    let nc = "",
      rc = "",
      pg = 0,
      hasMore = true;
    const base = `/group-graph/api/graph?group_id=${encodeURIComponent(gid)}&page_size=${ps}`;

    // 分页推送节流：至少间隔 PUSH_INTERVAL 才触发一次 onPage 回调，
    // 避免后端出页过快时每页都跑完整渲染管线；最后一页强制推送保证数据完整
    const PUSH_INTERVAL = 300;
    let lastPushTime = 0;

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

      // 节流：首页与末页强制推送，其余满 PUSH_INTERVAL 才推送；不推送时跳过 mapGraphData 与 UI 渲染
      const now = performance.now();
      // 后端首页会返回 nodes_total / relations_total（-1 表示未统计），
      // 透传给 UI 以便展示确定性进度条（节点与关系分别推进，UI 按总数加权）
      const totalNodes = Number(d.nodes_total);
      const totalRels = Number(d.relations_total);
      if (onPage && (pg === 1 || !hasMore || now - lastPushTime >= PUSH_INTERVAL)) {
        onPage(this.store.mapGraphData(), pg, {
          page: pg,
          totalNodes: Number.isFinite(totalNodes) && totalNodes >= 0 ? totalNodes : -1,
          totalRels: Number.isFinite(totalRels) && totalRels >= 0 ? totalRels : -1,
          loadedNodes: this.store.nodeMap.size,
          loadedRels: this.store.linkMap.size,
        });
        lastPushTime = now;
      }
    }

    return this.store.mapGraphData();
  }

  /**
   * 生成并加载示例图谱数据，模拟分页加载效果
   * 创建 10000 个节点和关联关系，每 500 条为一页，页间延迟 2 秒模拟网络加载
   *
   * @param {function} [onPage] - 每页加载完成的回调函数，签名为 (graphData, pageNum, meta)，
   *   meta 含 page / totalNodes / totalRels / loadedNodes / loadedRels
   * @returns {Promise<{nodes: object[], links: object[]}>} 最终的图谱数据
   */
  async loadDemoPaged(onPage) {
    this.store.reset();
    const dn = genDemoNodes(10000);
    const dl = genDemoLinks(dn);
    const total = Math.max(dn.length, dl.length);
    let pg = 1;

    // 每页 500 条（与真实分页的默认值保持一致）
    const DEMO_PAGE = 500;
    for (let s = 0; s < total; s += DEMO_PAGE) {
      const e = s + DEMO_PAGE;
      this.store.addNodes(dn.slice(s, e));
      this.store.addLinks(dl.slice(s, e));
      const g = this.store.mapGraphData();
      if (onPage)
        onPage(g, pg, {
          page: pg,
          totalNodes: dn.length,
          totalRels: dl.length,
          loadedNodes: this.store.nodeMap.size,
          loadedRels: this.store.linkMap.size,
        });
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
    const ps = 500;
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
