import { create } from 'zustand'
import { GraphStore } from '@/lib/graph-store'
import { GraphAPI } from '@/lib/graph-api'

const graphStore = new GraphStore()
const graphAPI = new GraphAPI(graphStore)

export const useAppStore = create((set, get) => ({
  // ── 图谱数据 ──
  nodes: [],           // 渲染就绪的节点数组（含计算后的颜色、半径、权重等）
  links: [],           // 渲染就绪的关系数组（含曲线曲率、端点引用等）
  rawNodes: graphStore.nodeMap,   // 原始节点存储（nid → 节点对象，去重）
  rawLinks: graphStore.linkMap,   // 原始关系存储（relStoreKey → 关系对象，去重）
  graphStore,           // 底层 GraphStore 实例，负责数据去重和图算法计算
  graphAPI,             // GraphAPI 实例，负责与后端 HTTP 通信

  // ── 统计信息 ──
  stats: { nodeCount: 0, linkCount: 0, orphanCount: 0, nodeTypeCount: 0, relTypeCount: 0 },

  // ── UI 状态 ──
  groupId: '',          // 当前加载的图谱分组 ID
  pageSize: 300,        // 分页加载时每页的条目数
  status: '',           // 底部状态栏显示的文本信息
  statusError: false,   // 状态信息是否为错误样式
  isLoading: false,     // 是否正在执行异步操作（如 CRUD 操作）
  isLoadingData: false, // 是否正在分页加载图谱数据（含后续分页）
  isFullscreenLoading: true,  // 是否显示全屏加载遮罩（初始加载/切换分组时）

  // ── 交互状态 ──
  selectedNodeId: null,        // 当前选中的节点 ID
  hoveredNodeId: null,         // 当前鼠标悬停的节点 ID
  searchKeyword: '',           // 搜索过滤关键词（小写，已 trim）
  orphanFilter: false,         // 是否只显示孤立节点（无任何关系连接的节点）
  selectedLegendTypes: new Set(),  // 图例面板中选中的节点类型集合
  selectedRelTypes: new Set(),     // 图例面板中选中的关系类型集合
  pathHighlight: null,         // 路径高亮集合（包含节点 ID 和边 key）

  // ── 详情面板 ──
  detailNode: null,      // 详情面板展示的节点或关系对象

  // ── 小地图 ──
  showMinimap: false,    // 是否显示右下角小地图

  // ── 导入弹窗 ──
  showImportModal: false,  // 是否显示导入弹窗
  importFile: null,        // 待导入的文件对象（File 实例）
  importMode: 'merge',     // 导入模式：'merge' 合并 / 'override' 覆盖

  // ── 抓取模式（用于表单字段自动填入节点 ID）──
  pickTarget: null,  // 当前激活的抓取目标表单字段 ID（如 'relSource'）
  pickedNid: null,   // 最近一次抓取到的节点 nid

  // ── 缩放 ──
  zoom: 1.0,         // 当前画布缩放比例

  // ── 操作面板触发器 ──
  opsExpand: false,    // 从详情面板「编辑」按钮触发展开操作面板的信号

  // ── 拖拽建边 ──
  edgeCreating: false,        // 是否处于建边模式
  edgeSourceId: null,          // 源节点 ID
  edgeMouseWorld: null,        // 当前鼠标世界坐标 {x, y}
  edgeTargetId: null,          // 悬停的目标节点 ID
  edgePopoverPos: null,        // 浮窗屏幕坐标 {x, y}

  // ── 确认对话框 ──
  confirmOpen: false,       // 是否显示确认对话框
  confirmMessage: '',       // 确认对话框的提示文本
  _confirmResolve: null,    // Promise resolve 回调（内部使用）

  // ═══════════════════════════════════════════════════════════════════
  // 简单 setter 方法：直接更新 Zustand 状态
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
  setEdgeCreating: (v) => set({ edgeCreating: v }),
  setEdgeSourceId: (v) => set({ edgeSourceId: v }),
  setEdgeMouseWorld: (v) => set({ edgeMouseWorld: v }),
  setEdgeTargetId: (v) => set({ edgeTargetId: v }),
  setEdgePopoverPos: (v) => set({ edgePopoverPos: v }),
  /** 重置所有建边状态 */
  resetEdgeCreation: () => set({
    edgeCreating: false, edgeSourceId: null,
    edgeMouseWorld: null, edgeTargetId: null, edgePopoverPos: null,
  }),
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
  // 图例类型筛选切换
  // 用于图例面板中按节点类型或关系类型过滤图谱显示
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
  // 同步图谱数据：将底层 GraphStore 的原始数据转换为 React 渲染状态
  // 每次数据变更后调用，触发组件重新渲染
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
  // 异步操作：加载图谱、执行增删改、导入文件
  // ═══════════════════════════════════════════════════════════════════

  /** 按分组 ID 加载图谱，支持游标分页，每加载一页更新一次 UI */
  loadGroup: async (gid) => {
    const { graphAPI, updateGraphData } = get()
    set({ isFullscreenLoading: true, isLoadingData: true })
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
      set({ isFullscreenLoading: false, isLoadingData: false })
    }
  },

  /** 加载示例图谱数据，模拟分页加载效果 */
  loadDemo: async () => {
    const { graphAPI, updateGraphData } = get()
    set({ isFullscreenLoading: true, isLoadingData: true })
    await graphAPI.loadDemoPaged(() => {
      updateGraphData()
      set({ isFullscreenLoading: false })
    })
    updateGraphData()
    set({ status: '已加载示例图谱', isLoadingData: false })
  },

  /** 执行 CRUD 操作（POST/PUT/DELETE），成功后同步图谱数据到 React 状态 */
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

  /** 上传文件（Excel/JSON）到后端进行导入 */
  importFileToServer: async (file) => {
    const { graphAPI } = get()
    const { groupId, importMode } = get()
    set({ isLoading: true })
    try {
      const data = await graphAPI.importFile(groupId, file, importMode)
      set({ status: `导入成功: 节点 ${data.nodes_imported}，关系 ${data.relations_imported}，跳过 ${data.relations_skipped}` })
      // 导入完成后重新加载当前分组数据以刷新图谱
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
  // 路径查找（BFS 广度优先搜索最短路径）
  // 在 store 的 linkMap 上构建无向邻接表，使用 BFS 寻找两节点间最短路径
  // ═══════════════════════════════════════════════════════════════════

  /**
   * 使用 BFS 查找两个节点之间的最短路径
   * @param {string} src - 起点节点 ID
   * @param {string} tgt - 终点节点 ID
   * @returns {{ pathNodes: string[], highlight: Set, hops: number } | { error: string }}
   *   成功时返回路径节点数组、高亮集合（节点+边）、跳数；
   *   失败时返回错误信息
   */
  findPath: (src, tgt) => {
    const store = get().graphStore
    if (!store.nodeMap.has(src)) return { error: `起点 ${src} 不存在` }
    if (!store.nodeMap.has(tgt)) return { error: `终点 ${tgt} 不存在` }
    if (src === tgt) return { error: '起点和终点相同' }

    // 根据 linkMap 构建无向邻接表
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

    // BFS 广度优先搜索
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

    // 回溯构建完整路径
    const pathNodes = []
    let node = tgt
    while (node) {
      pathNodes.unshift(node)
      node = parent.get(node)
    }

    // 构建高亮集合：包含路径上所有节点 ID 和边 key（双向）
    const highlight = new Set()
    for (const nid of pathNodes) highlight.add(nid)
    for (let i = 0; i < pathNodes.length - 1; i++) {
      highlight.add(`${pathNodes[i]}=>${pathNodes[i + 1]}`)
      highlight.add(`${pathNodes[i + 1]}=>${pathNodes[i]}`)
    }

    return { pathNodes, highlight, hops: pathNodes.length - 1 }
  },
}))
