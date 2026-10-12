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
  pageSize: 500,        // 分页加载时每页的条目数
  status: '',           // 底部状态栏显示的文本信息
  statusError: false,   // 状态信息是否为错误样式
  isLoading: false,     // 是否正在执行异步操作（如 CRUD 操作）
  isLoadingData: false, // 是否正在分页加载图谱数据（含后续分页）
  isFullscreenLoading: false,  // 是否显示全屏加载遮罩（初始加载/切换分组时）
  loadProgress: { loadedNodes: 0, totalNodes: -1, loadedRels: 0, totalRels: -1, page: 0 }, // 分页加载进度（total 为 -1 表示后端未提供总数）
  loadingText: '',      // 全屏 loading 显示的业务文字
  loadingBackdrop: 'semi',  // 全屏 loading 背景：'transparent' 全透明 / 'semi' 半透明

  // ── 交互状态 ──
  selectedNodeId: null,        // 当前选中的节点 ID
  hoveredNodeId: null,         // 当前鼠标悬停的节点 ID
  hoverFocus: true,            // 悬停时是否聚焦高亮（默认开启；关闭可避免节点密集时悬停「一片灰色」）
  layoutPinned: false,         // 是否固定布局（冻结力导向仿真，节点位置锁定）
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

  // ── 右键菜单 ──
  contextMenu: null,       // { x, y, nodeId, nid, nodeName } 或 null

  // ── 关联节点对话框 ──
  addRelatedDialog: null,  // { direction: 'outgoing'|'incoming', sourceNid, sourceName } 或 null

  // ── 确认对话框 ──
  confirmOpen: false,       // 是否显示确认对话框
  confirmMessage: '',       // 确认对话框的提示文本
  _confirmResolve: null,    // Promise resolve 回调（内部使用）

  // ── 访问鉴权 ──
  authState: 'checking',   // 'checking' 校验中 / 'locked' 需输入密码 / 'ready' 已通过
  authError: '',           // 密码门上的错误提示

  // ═══════════════════════════════════════════════════════════════════
  // 简单 setter 方法：直接更新 Zustand 状态
  // ═══════════════════════════════════════════════════════════════════
  setGroupId: (v) => set({ groupId: v }),
  setPageSize: (v) => set({ pageSize: v }),
  setStatus: (t, err = false) => set({ status: t, statusError: err }),
  setLoading: (v) => set({ isLoading: v }),
  setLoadProgress: (v) => set({ loadProgress: v }),
  setFullscreenLoading: (v, text = '', backdrop = 'semi') => set({
    isFullscreenLoading: v,
    loadingText: text || (v ? '加载中...' : ''),
    loadingBackdrop: v ? backdrop : 'semi',
  }),
  setSelectedNode: (id) => set({ selectedNodeId: id }),
  setHoveredNode: (id) => set({ hoveredNodeId: id }),
  setHoverFocus: (v) => set({ hoverFocus: v }),
  setLayoutPinned: (v) => set({ layoutPinned: v }),
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
  setContextMenu: (v) => set({ contextMenu: v }),
  setAddRelatedDialog: (v) => set({ addRelatedDialog: v }),
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

  /** 启动访问鉴权：无需密码或已持有有效令牌则直接进入，否则停在密码门 */
  bootstrapAuth: async () => {
    const { graphAPI } = get()
    try {
      const d = await graphAPI.checkAuth()
      if (d.auth_required && !d.authorized) {
        set({ authState: 'locked', authError: '' })
        return
      }
    } catch (e) {
      if (e.authRequired) {
        set({ authState: 'locked', authError: '' })
        return
      }
      // 旧版服务端没有鉴权接口：保持原有开放行为
      if (e.status === 404) {
        set({ authState: 'ready', authError: '' })
        get().initFromUrl()
        return
      }
      // 网关或网络异常：停在密码门并给出可重试提示，避免静默放行后被 401 弹回
      set({ authState: 'locked', authError: e.message || '无法连接鉴权服务，请稍后重试' })
      return
    }
    set({ authState: 'ready', authError: '' })
    get().initFromUrl()
  },

  /** 提交访问密码，成功后进入图谱 */
  submitPassword: async (password) => {
    const { graphAPI } = get()
    try {
      await graphAPI.login(password)
      set({ authState: 'ready', authError: '' })
      get().initFromUrl()
      return true
    } catch (e) {
      set({ authError: e.message || '访问密码错误' })
      return false
    }
  },

  /** 令牌失效时清掉本地令牌并退回密码门 */
  lockSession: () => {
    get().graphAPI.logout()
    set({ authState: 'locked', authError: '访问密码已失效，请重新输入', status: '', statusError: false })
  },

  /** 从 URL 参数初始化图谱数据：demo=true 为示例模式，group_id 为正式模式 */
  initFromUrl: () => {
    const params = new URLSearchParams(window.location.search)
    const gid = params.get('group_id') || ''
    const demo = params.get('demo') === 'true'
    if (gid) set({ groupId: gid })
    if (demo) {
      // 示例模式：生成模拟数据用于演示
      get().loadDemo().finally(() => set({ isFullscreenLoading: false }))
    } else if (gid) {
      // 正式模式：从后端 API 加载指定分组的图谱数据
      get().loadGroup(gid).finally(() => set({ isFullscreenLoading: false }))
    } else {
      // 未指定参数：提示用户输入 group_id
      set({ isFullscreenLoading: false, status: '请输入 group_id 后加载图谱' })
    }
  },

  /** 按分组 ID 加载图谱，支持游标分页，每加载一页更新一次 UI */
  loadGroup: async (gid) => {
    const { graphAPI, updateGraphData, setFullscreenLoading, setLoadProgress } = get()
    setFullscreenLoading(true, '加载图谱数据...', 'semi')
    set({ isLoadingData: true })
    setLoadProgress({ loadedNodes: 0, totalNodes: -1, loadedRels: 0, totalRels: -1, page: 0 })
    // 加载期间跳过可达性 BFS，避免每页 mapGraphData 触发 O(N×(V+E)) 重算
    graphStore.skipWeights = true
    try {
      const ps = get().pageSize
      await graphAPI.loadGroup(gid, ps, (_, pg, meta) => {
        updateGraphData()
        if (pg === 1) setFullscreenLoading(false)
        if (meta) {
          set({
            loadProgress: {
              loadedNodes: meta.loadedNodes,
              totalNodes: meta.totalNodes,
              loadedRels: meta.loadedRels,
              totalRels: meta.totalRels,
              page: meta.page,
            },
          })
        }
        set({ status: `加载中: 第 ${pg} 页` })
      })
      // 恢复后末次更新会计算一次完整可达性权重
      graphStore.skipWeights = false
      updateGraphData()
      set({ status: `加载完成: 节点 ${graphStore.nodeMap.size}，关系 ${graphStore.links.length}` })
    } catch (e) {
      graphStore.skipWeights = false
      if (e.authRequired) {
        get().lockSession()
        return
      }
      set({ status: `加载失败: ${e.message}`, statusError: true })
    } finally {
      setFullscreenLoading(false)
      set({ isLoadingData: false })
    }
  },

  /** 加载示例图谱数据，模拟分页加载效果 */
  loadDemo: async () => {
    const { graphAPI, updateGraphData, setFullscreenLoading, setLoadProgress } = get()
    setFullscreenLoading(true, '加载示例图谱...', 'semi')
    set({ isLoadingData: true })
    setLoadProgress({ loadedNodes: 0, totalNodes: -1, loadedRels: 0, totalRels: -1, page: 0 })
    // 加载期间跳过可达性 BFS，完成后算一次
    graphStore.skipWeights = true
    try {
      await graphAPI.loadDemoPaged((_, pg, meta) => {
        updateGraphData()
        if (pg === 1) setFullscreenLoading(false)
        if (meta) {
          set({
            loadProgress: {
              loadedNodes: meta.loadedNodes,
              totalNodes: meta.totalNodes,
              loadedRels: meta.loadedRels,
              totalRels: meta.totalRels,
              page: meta.page,
            },
          })
        }
      })
      set({ status: '已加载示例图谱' })
    } catch (e) {
      set({ status: `加载示例数据失败: ${e.message}`, statusError: true })
    } finally {
      // 无论成功失败都要复位：否则权重计算被跳过、加载态一直挂着，
      // 自动相机与进度条会永远停在「加载中」
      graphStore.skipWeights = false
      updateGraphData()
      set({ isLoadingData: false })
    }
  },

  /** 执行 CRUD 操作（POST/PUT/DELETE），成功后同步图谱数据到 React 状态 */
  mutate: async (path, method, payload) => {
    const { graphAPI, updateGraphData, setFullscreenLoading } = get()
    setFullscreenLoading(true, '执行操作...')
    try {
      set({ status: '执行中...' })
      const result = await graphAPI.mutate(path, method, payload)
      if (result) {
        updateGraphData()
        set({ status: '操作成功' })
      }
      return result
    } catch (e) {
      if (e.authRequired) {
        get().lockSession()
        return null
      }
      set({ status: `操作失败: ${e.message}`, statusError: true })
      return null
    } finally {
      setFullscreenLoading(false)
    }
  },

  /** 上传文件（Excel/JSON）到后端进行导入 */
  importFileToServer: async (file) => {
    const { graphAPI, setFullscreenLoading } = get()
    const { groupId, importMode } = get()
    const { toast } = await import('sonner')
    setFullscreenLoading(true, '导入数据中...')
    try {
      const data = await graphAPI.importFile(groupId, file, importMode)
      const summary = `导入成功: 节点 ${data.nodes_imported}，关系 ${data.relations_imported}，跳过 ${data.relations_skipped}`
      // 先 toast：随后的图谱重载可能很久，结果提示不能等重载完成才出现
      toast.success(summary)
      // 导入完成后重新加载当前分组数据以刷新图谱
      await get().loadGroup(groupId)
      // loadGroup 会把 status 覆盖成「加载中: 第 N 页」/「加载完成: ...」，
      // 因此在重载结束后回写导入结果；若重载本身失败(statusError 已置位)则保留失败提示
      if (get().statusError) {
        toast.error('导入成功，但图谱刷新失败，请手动重新加载')
      } else {
        set({ status: summary, statusError: false })
      }
      return data
    } catch (e) {
      if (e.authRequired) {
        get().lockSession()
        return null
      }
      toast.error(`导入失败: ${e.message}`)
      set({ status: `导入失败: ${e.message}`, statusError: true })
      return null
    } finally {
      setFullscreenLoading(false)
      set({ showImportModal: false, importFile: null })
    }
  },

  /** 导出当前分组数据（excel/json），走统一请求层以携带访问令牌 */
  exportGroup: async (format = 'excel') => {
    const { graphAPI, groupId, setFullscreenLoading } = get()
    if (!groupId) {
      set({ status: '请先输入 group_id', statusError: true })
      return
    }
    setFullscreenLoading(true, '导出数据中...')
    try {
      await graphAPI.exportFile(groupId, format)
      set({ status: `已导出分组 ${groupId} 的数据`, statusError: false })
    } catch (e) {
      if (e.authRequired) {
        get().lockSession()
        return
      }
      set({ status: `导出失败: ${e.message}`, statusError: true })
    } finally {
      setFullscreenLoading(false)
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
