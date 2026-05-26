import { clean, relStoreKey, genDemoNodes, genDemoLinks } from "./graph-store";

/* ═══════════════════════════════════════════════════════════════════
 GraphAPI – HTTP communication layer

 - req():     unified fetch wrapper, auto-prefixes plugin base path
 - loadGroup(): paginated graph loading with cursor support
 - loadDemoPaged(): generate & load demo data with simulated pagination
 - mutate():  CRUD operations that apply changes to the store

 Auto-detects base path from window.location.pathname matching /e/{id}.
 ═══════════════════════════════════════════════════════════════════ */

export class GraphAPI {
  /**
   * @param {import('./graph-store').GraphStore} store
   */
  constructor(store) {
    this.store = store;
    // Auto-detect plugin base path: /e/{plugin_id}
    const m = typeof window !== "undefined" ? window.location.pathname.match(/^\/e\/[^/]+/) : null;
    this.base = m ? m[0] : "";
  }

  /** Prepend base path to a relative URL */
  url(p) {
    return this.base.replace(/\/+$/, "") + (String(p).startsWith("/") ? p : `/${p}`);
  }

  /**
   * Unified HTTP request wrapper.
   * @param {string} path - request path (relative or absolute)
   * @param {object} [opt] - { method, body } – body is already stringified for JSON
   * @returns {Promise<object>} parsed JSON response
   */
  async req(path, opt = {}) {
    const m = (opt.method || "GET").toUpperCase();
    const u = /^https?:\/\//.test(path) ? path : this.url(path);
    const init = { method: m, headers: { Accept: "application/json" } };

    if (opt.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = opt.body;
    }

    // For multipart form data, remove Content-Type so the browser sets the boundary
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
   * Paginated graph loading with cursor support.
   * Fetches all pages, calling `onPage(data, pageNum)` after each page
   * so the UI can render incrementally.
   *
   * @param {string} gid - group_id
   * @param {number} pageSize - items per page
   * @param {function} [onPage] - callback(graphData, pageNum)
   * @returns {Promise<{nodes: object[], links: object[]}>}
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
   * Generate and load demo data with simulated pagination.
   * Creates 1000 nodes and associated links, loaded in pages of 200
   * with a 2-second delay between pages to simulate real loading.
   *
   * @param {function} [onPage] - callback(graphData, pageNum)
   * @returns {Promise<{nodes: object[], links: object[]}>}
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
      // Simulate network delay between pages
      await new Promise((r) => setTimeout(r, 2000));
    }

    return this.store.mapGraphData();
  }

  /**
   * Execute a CRUD mutation (POST/PUT/DELETE) against the backend,
   * then apply the change to the frontend store.
   *
   * @param {string} path - API endpoint path
   * @param {string} method - HTTP method
   * @param {object} payload - request body
   * @returns {Promise<object|null>} graph data after mutation, or null on failure
   */
  async mutate(path, method, payload) {
    const result = await this.req(path, { method, body: JSON.stringify(payload) });
    if (result.ok !== true) {
      return null;
    }

    if (path.includes("/replace-node-relations")) {
      this.store.applyReplaceRelations(payload.old_nid);
      // After replace, refresh first page to pick up new relations from backend
      await this._refreshAfterReplace();
    } else {
      this.store.applyMutation(path, method, payload);
    }

    const g = this.store.mapGraphData();
    return g;
  }

  /**
   * After replace-node-relations the backend doesn't return new relation data,
   * so we do a lightweight refresh of the first page to pick them up.
   * @private
   */
  async _refreshAfterReplace() {
    // We need a gid to refresh; if the store has no nodes, nothing to refresh.
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
   * Import a file (Excel/JSON) via multipart form upload.
   * @param {string} gid - group_id
   * @param {File} file - the file to upload
   * @param {string} mode - 'merge' or 'override'
   * @returns {Promise<object>} server response
   */
  async importFile(gid, file, mode) {
    const fd = new FormData();
    fd.append("file", file);
    const url = `/group-graph/api/import?group_id=${encodeURIComponent(gid)}&mode=${mode}`;
    return this.req(url, { method: "POST", formData: fd });
  }
}
