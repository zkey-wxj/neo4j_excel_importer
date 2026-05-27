import { create } from 'zustand'
import { GraphStore } from '@/lib/graph-store'
import { GraphAPI } from '@/lib/graph-api'

const graphStore = new GraphStore()
const graphAPI = new GraphAPI(graphStore)

export const useAppStore = create((set, get) => ({
  // ── Data ──
  nodes: [],
  links: [],
  rawNodes: graphStore.nodeMap,
  rawLinks: graphStore.linkMap,
  graphStore,
  graphAPI,

  // ── Stats ──
  stats: { nodeCount: 0, linkCount: 0, orphanCount: 0, nodeTypeCount: 0, relTypeCount: 0 },

  // ── UI state ──
  groupId: '',
  pageSize: 300,
  status: '',
  statusError: false,
  isLoading: false,
  isFullscreenLoading: true,

  // ── Interaction state ──
  selectedNodeId: null,
  hoveredNodeId: null,
  searchKeyword: '',
  orphanFilter: false,
  selectedLegendTypes: new Set(),
  selectedRelTypes: new Set(),
  pathHighlight: null,

  // ── Detail panel ──
  detailNode: null,

  // ── Minimap ──
  showMinimap: false,

  // ── Import modal ──
  showImportModal: false,
  importFile: null,
  importMode: 'merge',

  // ── Pick mode (for "grab node" buttons) ──
  pickTarget: null, // input field id
  pickedNid: null, // nid of picked node

  // ── Zoom ──
  zoom: 1.0,

  // ── Ops panel trigger ──
  opsExpand: false,

  // ── Confirm dialog ──
  confirmOpen: false,
  confirmMessage: '',
  _confirmResolve: null,

  // ═══════════════════════════════════════════════════════════════════
  // Simple setters
  // ═══════════════════════════════════════════════════════════════════
  setGroupId: (v) => set({ groupId: v }),
  setPageSize: (v) => set({ pageSize: v }),
  setStatus: (t, err = false) => set({ status: t, statusError: err }),
  setLoading: (v) => set({ isLoading: v }),
  setFullscreenLoading: (v) => set({ isFullscreenLoading: v }),
  setSelectedNode: (id) => set({ selectedNodeId: id }),
  setHoveredNode: (id) => set({ hoveredNodeId: id }),
  setSearchKeyword: (v) => set({ searchKeyword: v }),
  setOrphanFilter: (v) => set({ orphanFilter: v }),
  setZoom: (v) => set({ zoom: v }),
  setOpsExpand: (v) => set({ opsExpand: v }),
  setShowMinimap: (v) => set({ showMinimap: v }),
  setShowImportModal: (v) => set({ showImportModal: v }),
  setImportFile: (v) => set({ importFile: v }),
  setImportMode: (v) => set({ importMode: v }),
  setPickTarget: (v) => set({ pickTarget: v }),
  setPickedNid: (v) => set({ pickedNid: v }),
  setPathHighlight: (v) => set({ pathHighlight: v }),
  setDetailNode: (v) => set({ detailNode: v }),
  setConfirmOpen: (v) => set({ confirmOpen: v }),

  /** 显示确认对话框，返回 Promise<boolean> */
  confirm: (message) => new Promise((resolve) => {
    set({ confirmOpen: true, confirmMessage: message, _confirmResolve: resolve })
  }),
  _handleConfirm: (result) => {
    const { _confirmResolve } = get()
    _confirmResolve?.(result)
    set({ confirmOpen: false, confirmMessage: '', _confirmResolve: null })
  },

  // ═══════════════════════════════════════════════════════════════════
  // Legend type toggles
  // ═══════════════════════════════════════════════════════════════════
  toggleLegendType: (key) => {
    const s = new Set(get().selectedLegendTypes)
    s.has(key) ? s.delete(key) : s.add(key)
    set({ selectedLegendTypes: s })
  },
  clearLegendTypes: () => set({ selectedLegendTypes: new Set() }),
  toggleRelType: (key) => {
    const s = new Set(get().selectedRelTypes)
    s.has(key) ? s.delete(key) : s.add(key)
    set({ selectedRelTypes: s })
  },
  clearRelTypes: () => set({ selectedRelTypes: new Set() }),

  // ═══════════════════════════════════════════════════════════════════
  // Sync graph data from the plain store into Zustand state
  // ═══════════════════════════════════════════════════════════════════
  updateGraphData: () => {
    const g = graphStore.mapGraphData()
    set({
      nodes: g.nodes,
      links: g.links,
      stats: {
        nodeCount: g.nodes.length,
        linkCount: g.links.length,
        ...graphStore.stats,
      },
    })
    return g
  },

  // ═══════════════════════════════════════════════════════════════════
  // Async actions
  // ═══════════════════════════════════════════════════════════════════

  /** Load a graph group by gid with paginated cursor support */
  loadGroup: async (gid) => {
    const { graphAPI, updateGraphData } = get()
    set({ isFullscreenLoading: true })
    try {
      const ps = get().pageSize
      await graphAPI.loadGroup(gid, ps, (_, pg) => {
        updateGraphData()
        set({ isFullscreenLoading: false })
        set({ status: `游标加载中: 第 ${pg} 页` })
      })
      updateGraphData()
      set({ status: `加载完成: 节点 ${graphStore.nodeMap.size}，关系 ${graphStore.links.length}` })
    } catch (e) {
      set({ status: `加载失败: ${e.message}`, statusError: true })
    } finally {
      set({ isFullscreenLoading: false })
    }
  },

  /** Load demo graph data with simulated pagination */
  loadDemo: async () => {
    const { graphAPI, updateGraphData } = get()
    set({ isFullscreenLoading: true })
    await graphAPI.loadDemoPaged(() => {
      updateGraphData()
      set({ isFullscreenLoading: false })
    })
    updateGraphData()
    set({ status: '已加载示例图谱' })
  },

  /** Execute a CRUD mutation, then sync graph data to React state */
  mutate: async (path, method, payload) => {
    const { graphAPI, updateGraphData } = get()
    set({ isLoading: true })
    try {
      set({ status: '执行中...' })
      const result = await graphAPI.mutate(path, method, payload)
      if (result) {
        updateGraphData()
        set({ status: '操作成功' })
      }
      return result
    } catch (e) {
      set({ status: `操作失败: ${e.message}`, statusError: true })
      return null
    } finally {
      set({ isLoading: false })
    }
  },

  /** Import a file (Excel/JSON) to the backend */
  importFileToServer: async (file) => {
    const { graphAPI } = get()
    const { groupId, importMode } = get()
    set({ isLoading: true })
    try {
      const data = await graphAPI.importFile(groupId, file, importMode)
      set({ status: `导入成功: 节点 ${data.nodes_imported}，关系 ${data.relations_imported}，跳过 ${data.relations_skipped}` })
      // Reload the group after import
      await get().loadGroup(groupId)
      return data
    } catch (e) {
      set({ status: `导入失败: ${e.message}`, statusError: true })
      return null
    } finally {
      set({ isLoading: false })
      set({ showImportModal: false, importFile: null })
    }
  },

  // ═══════════════════════════════════════════════════════════════════
  // Path finding (BFS shortest path)
  // ═══════════════════════════════════════════════════════════════════

  /**
   * Find shortest path between two nodes using BFS on the store's linkMap.
   * Returns { pathNodes, highlight, hops } or { error }.
   */
  findPath: (src, tgt) => {
    const store = get().graphStore
    if (!store.nodeMap.has(src)) return { error: `起点 ${src} 不存在` }
    if (!store.nodeMap.has(tgt)) return { error: `终点 ${tgt} 不存在` }
    if (src === tgt) return { error: '起点和终点相同' }

    // Build undirected adjacency list from linkMap
    const adj = new Map()
    store.linkMap.forEach((r) => {
      const s = (r.source_nid || '').trim()
      const t = (r.target_nid || '').trim()
      if (!s || !t) return
      if (!adj.has(s)) adj.set(s, [])
      if (!adj.has(t)) adj.set(t, [])
      adj.get(s).push(t)
      adj.get(t).push(s)
    })

    // BFS
    const queue = [src]
    const visited = new Set([src])
    const parent = new Map()
    let found = false

    while (queue.length && !found) {
      const cur = queue.shift()
      for (const nei of adj.get(cur) || []) {
        if (visited.has(nei)) continue
        visited.add(nei)
        parent.set(nei, cur)
        if (nei === tgt) {
          found = true
          break
        }
        queue.push(nei)
      }
    }

    if (!found) return { error: '未找到路径' }

    // Backtrack path
    const pathNodes = []
    let node = tgt
    while (node) {
      pathNodes.unshift(node)
      node = parent.get(node)
    }

    // Build highlight set: nodes + edge keys
    const highlight = new Set()
    for (const nid of pathNodes) highlight.add(nid)
    for (let i = 0; i < pathNodes.length - 1; i++) {
      highlight.add(`${pathNodes[i]}=>${pathNodes[i + 1]}`)
      highlight.add(`${pathNodes[i + 1]}=>${pathNodes[i]}`)
    }

    return { pathNodes, highlight, hops: pathNodes.length - 1 }
  },
}))
