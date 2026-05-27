import * as d3 from 'd3'
import { DEMO_NAMES, DEMO_TYPES, DEMO_RELS } from './constants'

/* ═══════════════════════════════════════════════════════════════════
 辅助工具函数
 ═══════════════════════════════════════════════════════════════════ */

/** 安全的字符串 trim：null/undefined 转为空字符串后 trim */
export function clean(v) {
  return String(v || '').trim()
}

/** 从节点的 labels 数组中提取第一个标签作为节点类型，无标签时回退为 "Node" */
export function parseType(labels) {
  const a = Array.isArray(labels) ? labels.map(x => clean(x)).filter(Boolean) : []
  if (a.length > 0) return a[0]
  return 'Node'
}

/** 从节点的多个可能位置提取权重值，限制在 [1, 100] 范围内 */
export function parseWeight(n) {
  for (const v of [n.weight, n.properties?.weight, n.properties?.score, n.meta?.score]) {
    const x = Number(v)
    if (Number.isFinite(x)) return Math.max(1, Math.min(100, Math.round(x)))
  }
  return 50
}

/** 获取节点的显示名称：优先使用 name，其次 nid，最后回退为 "Node" */
export function nodeLabel(n) {
  return clean(n.name) || clean(n.nid) || 'Node'
}

/** 获取关系的类型字符串，无类型时回退为 "RELATED" */
export function relLabel(r) {
  return clean(r.rel_type) || 'RELATED'
}

/** 生成关系的去重 key：source_nid=>target_nid:rel_type */
export function relStoreKey(r) {
  return `${clean(r.source_nid)}=>${clean(r.target_nid)}:${relLabel(r)}`
}

/* ═══════════════════════════════════════════════════════════════════
 类型元信息缓存 – 为每种节点类型缓存颜色、背景色、半径
 颜色通过 FNV-1a 哈希算法从类型字符串确定性地映射到 HSL 色值，
 保证相同类型始终获得相同颜色，不同类型大概率获得不同颜色
 ═══════════════════════════════════════════════════════════════════ */
const _typeMetaCache = new Map()

/** 将字符串哈希为 32 位整数（FNV-1a 算法，分布均匀且计算高效） */
function fnv1a(str) {
  let h = 0x811c9dc5
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i)
    h = (h * 0x01000193) | 0
  }
  return h >>> 0
}

/** 根据节点类型字符串返回 { color, bg, r } 元信息，带缓存 */
export function getTypeMeta(type) {
  if (!_typeMetaCache.has(type)) {
    const hash = fnv1a(type)
    const h = hash % 360
    const s = 55 + (hash % 25)
    const l = 48 + ((hash >> 8) % 12)
    const c = `hsl(${h}, ${s}%, ${l}%)`
    const rgba = d3.color(c).rgb()
    const bg = `rgba(${rgba.r},${rgba.g},${rgba.b},0.10)`
    const r = 10 + ((hash >> 16) % 4)
    _typeMetaCache.set(type, { color: c, bg, r })
  }
  return _typeMetaCache.get(type)
}

/* ═══════════════════════════════════════════════════════════════════
 示例数据生成器
 用于在无后端连接时生成模拟的图谱数据，方便前端开发和演示
 ═══════════════════════════════════════════════════════════════════ */

/**
 * 简单的种子随机数生成器（Mulberry32 算法）
 * 使用固定种子确保每次生成相同的随机序列，便于重现
 */
function mulberry32(seed) {
  return function () {
    seed |= 0; seed = seed + 0x6D2B79F5 | 0
    let t = Math.imul(seed ^ seed >>> 15, 1 | seed)
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t
    return ((t ^ t >>> 14) >>> 0) / 4294967296
  }
}

/**
 * 生成示例节点，采用树形层级结构模拟知识图谱的根实体
 *
 *   第 0 层：3 个根节点     (System 类型)
 *   第 1 层：30 个子节点    (Component 类型)
 *   第 2 层：120 个叶子节点  (Database / Model / Task 类型)
 *   第 3 层：剩余节点        (Model / Task 类型)
 *
 * 根节点可到达所有后代节点，因此拥有最高的可达性权重
 */
export function genDemoNodes(count) {
  const now = new Date().toISOString()
  const meta = { created_at: now, updated_at: now, source: 'demo', version: 1 }
  const rand = mulberry32(42)

  // 使用种子随机数打乱名称顺序
  const names = [...DEMO_NAMES]
  for (let i = names.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [names[i], names[j]] = [names[j], names[i]]
  }

  const l0 = 3, l1 = 30, l2 = 120, l3 = Math.max(0, count - l0 - l1 - l2)
  const levels = [
    ...Array.from({ length: l0 }, () => 'System'),
    ...Array.from({ length: l1 }, () => 'Component'),
    ...Array.from({ length: l2 }, () => ['Database', 'Model', 'Task'][Math.floor(rand() * 3)]),
    ...Array.from({ length: l3 }, () => ['Model', 'Task'][Math.floor(rand() * 2)]),
  ]

  return levels.slice(0, count).map((type, i) => ({
    nid: String(i + 1),
    name: names[i % names.length],
    labels: [type],
    description: '',
    group_id: 'demo',
    properties: { weight: Math.round(40 + rand() * 60) },
    meta,
  }))
}

/**
 * 生成示例关系数据，采用树形结构 + 少量交叉链接
 *
 * 树边：每个非根节点随机连接到上一层的某个父节点，
 *   形成清晰的可达性层级结构（根 → … → 叶子）
 * 交叉链接：在同层兄弟节点之间随机添加少量边（约占总数 10%），
 *   增加图谱的复杂度和真实感
 */
export function genDemoLinks(nodes) {
  const now = new Date().toISOString()
  const meta = { created_at: now, updated_at: now, source: 'demo', version: 1 }
  const rand = mulberry32(42)
  const n = nodes.length
  if (n < 2) return []

  const links = []
  const seen = new Set()

  const add = (s, t) => {
    const k = `${s}=>${t}`
    if (seen.has(k) || s === t) return
    seen.add(k)
    links.push({
      source_nid: s,
      target_nid: t,
      rel_type: DEMO_RELS[links.length % DEMO_RELS.length],
      direction: 'forward',
      description: '',
      weight: 1,
      group_id: 'demo',
      properties: {},
      meta,
    })
  }

  const nids = nodes.map(nd => nd.nid)

  // 树边：根据层级边界（3, 33, 153）构建父子关系
  const cuts = [3, 33, 153, n]
  for (let lv = 1; lv < cuts.length; lv++) {
    const pStart = cuts[lv - 1]
    const pEnd = cuts[lv]
    const parentStart = cuts[Math.max(0, lv - 2)]
    const parentEnd = cuts[lv - 1]
    for (let ci = pStart; ci < pEnd && ci < n; ci++) {
      const pi = parentStart + Math.floor(rand() * (parentEnd - parentStart))
      add(nids[pi], nids[ci])
    }
  }

  // 交叉链接：在随机的同层兄弟节点之间添加（约占节点数 10%）
  const crossTarget = Math.floor(n * 0.1)
  let attempts = 0
  while (links.length - (n - 1) < crossTarget && attempts < crossTarget * 10) {
    const a = Math.floor(rand() * n)
    const b = Math.floor(rand() * n)
    if (a !== b) add(nids[a], nids[b])
    attempts++
  }

  return links
}

/* ═══════════════════════════════════════════════════════════════════
 GraphStore – 纯数据管理器（非 React 组件）

 负责图谱原始数据的存储、去重和计算，核心职责包括：
 - nodeMap / linkMap: 去重的原始数据存储（nid → 节点，relKey → 关系）
 - mapGraphData():    将原始数据转换为渲染就绪的数组，计算节点权重
 - computeNodeWeights(): 通过可达性 BFS + 度中心性综合计算节点权重
 - applyMutation() / applyReplaceRelations(): 前端侧的乐观 CRUD 更新
 ═══════════════════════════════════════════════════════════════════ */

export class GraphStore {
  constructor() {
    /** @type {Map<string, object>} 原始节点存储：nid → 节点对象 */
    this.nodeMap = new Map()
    /** @type {Map<string, object>} 原始关系存储：relStoreKey → 关系对象 */
    this.linkMap = new Map()
    /** 渲染就绪的节点数组（由 mapGraphData 填充） */
    this.nodes = []
    /** 渲染就绪的关系数组（由 mapGraphData 填充） */
    this.links = []
    /** 图谱级别的统计信息：孤立节点数、节点类型数、关系类型数 */
    this.stats = { orphanCount: 0, nodeTypeCount: 0, relTypeCount: 0 }
  }

  /** 清空所有数据（重置 nodeMap、linkMap、nodes、links、stats） */
  reset() {
    this.nodeMap.clear()
    this.linkMap.clear()
    this.nodes = []
    this.links = []
    this.stats = { orphanCount: 0, nodeTypeCount: 0, relTypeCount: 0 }
  }

  /**
   * 批量添加节点，按 nid 去重（已存在则覆盖更新）
   * @param {object[]} arr - 原始节点对象数组，需包含 { nid, name, labels, ... }
   */
  addNodes(arr) {
    for (const n of arr) {
      const nid = clean(n.nid)
      if (nid) this.nodeMap.set(nid, n)
    }
  }

  /**
   * 批量添加关系，按 source_nid=>target_nid:rel_type 去重
   * @param {object[]} arr - 原始关系对象数组
   */
  addLinks(arr) {
    for (const r of arr) {
      const k = relStoreKey(r)
      if (k) this.linkMap.set(k, r)
    }
  }

  /**
   * 将原始数据转换为渲染就绪的数组
   * 处理流程：验证关系连通性 → 提取类型/颜色 → 计算综合节点权重
   * @returns {{ nodes: object[], links: object[] }}
   */
  mapGraphData() {
    const raw = Array.from(this.nodeMap.values())
    const rels = Array.from(this.linkMap.values())
    const nidSet = new Set(this.nodeMap.keys())
    const degMap = new Map()
    const valid = []

    // 验证关系有效性：两个端点都必须存在于 nodeMap 中
    for (const r of rels) {
      const s = clean(r.source_nid)
      const t = clean(r.target_nid)
      if (!nidSet.has(s) || !nidSet.has(t) || !s || !t) continue
      valid.push(r)
      degMap.set(s, (degMap.get(s) || 0) + 1)
      degMap.set(t, (degMap.get(t) || 0) + 1)
    }

    // 构建渲染节点数组：附加类型、颜色、半径等计算属性
    this.nodes = raw.map((n, i) => {
      const type = parseType(n.labels)
      const m = getTypeMeta(type)
      const nid = clean(n.nid)
      const deg = degMap.get(nid) || 0
      return {
        id: nid || String(i + 1),
        nid,
        label: nodeLabel(n),
        type,
        weight: parseWeight(n),
        color: m.color,
        bg: m.bg,
        r: Math.max(8, Math.min(26, m.r + deg * 0.9)),
        raw: n,
      }
    })

    // 构建 nid → 渲染 id 的映射表，用于关系端点解析
    const idMap = new Map(this.nodes.map(n => [n.nid, n.id]))

    // 构建渲染关系数组：将原始关系转换为 source/target 引用格式
    this.links = valid
      .map((r, i) => {
        const s = idMap.get(clean(r.source_nid))
        const t = idMap.get(clean(r.target_nid))
        if (!s || !t) return null
        return {
          source: s,
          target: t,
          relation: relLabel(r),
          _key: relStoreKey(r) || `${clean(r.source_nid)}=>${clean(r.target_nid)}:${relLabel(r)}:${i}`,
          raw: r,
        }
      })
      .filter(Boolean)

    // 通过图算法计算综合节点权重
    this.computeNodeWeights()
    this.computeStats()

    return { nodes: this.nodes, links: this.links }
  }

  /** 计算图谱统计信息：孤立节点数、节点类型数、关系类型数 */
  computeStats() {
    const connectedNids = new Set()
    this.links.forEach(l => {
      connectedNids.add(clean(typeof l.source === 'object' ? l.source?.id : l.source))
      connectedNids.add(clean(typeof l.target === 'object' ? l.target?.id : l.target))
    })

    const nodeTypes = new Set()
    this.nodes.forEach(n =>
      (n.raw?.labels || [n.type]).forEach(l => {
        const t = clean(l)
        if (t) nodeTypes.add(t)
      }),
    )

    const relTypes = new Set()
    this.links.forEach(l => {
      const t = clean(l.relation)
      if (t) relTypes.add(t)
    })

    this.stats = {
      orphanCount: Math.max(0, this.nodes.length - connectedNids.size),
      nodeTypeCount: nodeTypes.size,
      relTypeCount: relTypes.size,
    }
  }

  /**
   * 将 CRUD 操作直接应用到前端 store（乐观更新）
   * 根据 API 路径判断操作对象是节点还是关系，执行对应的增删改
   * @param {string} path  - API 路径（包含 "/node" 或 "/relation"）
   * @param {string} method - HTTP 方法：POST / PUT / DELETE
   * @param {object} payload - 请求体数据
   */
  applyMutation(path, method, payload) {
    if (path.includes('/node')) {
      const nid = clean(payload.nid)
      if (method === 'POST' || method === 'PUT') {
        this.nodeMap.set(nid, payload)
      } else if (method === 'DELETE') {
        this.nodeMap.delete(nid)
        // 删除节点时，同时移除所有引用该节点的关系
        const toRemove = []
        for (const [k, r] of this.linkMap) {
          if (clean(r.source_nid) === nid || clean(r.target_nid) === nid) toRemove.push(k)
        }
        toRemove.forEach(k => this.linkMap.delete(k))
      }
    } else if (path.includes('/relation')) {
      const key = relStoreKey(payload)
      if (method === 'POST' || method === 'PUT') {
        this.linkMap.set(key, payload)
      } else if (method === 'DELETE') {
        this.linkMap.delete(key)
      }
    }
  }

  /**
   * 移除指定节点的所有关联关系（用于 replace-node-relations 操作后）
   * @param {string} oldNid - 需要清除关系的旧节点 ID
   */
  applyReplaceRelations(oldNid) {
    const toRemove = []
    for (const [k, r] of this.linkMap) {
      if (clean(r.source_nid) === oldNid || clean(r.target_nid) === oldNid) toRemove.push(k)
    }
    toRemove.forEach(k => this.linkMap.delete(k))
  }

  /**
   * 计算知识图谱中每个节点的综合权重
   *
   * 主要指标：可达性（Reachability）—— 通过出边 BFS 计算每个节点
   *   能到达的下游节点数量。可扩展到最多后代的根实体得分最高。
   *
   * 最终得分 = 原始权重 × 0.25 + 可达性 × 0.75，限制在 [1, 100] 范围内
   * 这种加权方式确保图谱中具有广泛影响的根节点显示得更大更突出
   */
  computeNodeWeights() {
    const N = this.nodes.length
    if (!N) return
    const L = this.links.length
    if (!L) {
      this.nodes.forEach(n => { n.weight = parseWeight(n.raw) })
      return
    }

    // 构建出边邻接表（按节点索引）
    const idIdx = new Map(this.nodes.map((n, i) => [n.id, i]))
    const adj = Array.from({ length: N }, () => [])

    for (let i = 0; i < L; i++) {
      const l = this.links[i]
      const si = idIdx.get(l.source)
      const ti = idIdx.get(l.target)
      if (si == null || ti == null || si === ti) continue
      adj[si].push(ti)
    }

    // ── 可达性计算：对每个节点执行 BFS，统计可到达的下游节点数 ──
    const reach = new Float64Array(N)
    const visited = new Uint8Array(N)
    for (let u = 0; u < N; u++) {
      visited.fill(0)
      let count = 0
      const queue = [u]
      visited[u] = 1
      while (queue.length) {
        const cur = queue.shift()
        for (const v of adj[cur]) {
          if (!visited[v]) {
            visited[v] = 1
            count++
            queue.push(v)
          }
        }
      }
      reach[u] = count
    }

    // ── 归一化可达性到 [0, 1] 区间 ──
    let mn = Infinity, mx = -Infinity
    for (let i = 0; i < N; i++) {
      if (reach[i] < mn) mn = reach[i]
      if (reach[i] > mx) mx = reach[i]
    }
    const range = mx - mn || 1
    for (let i = 0; i < N; i++) reach[i] = (reach[i] - mn) / range

    // ── 加权组合：原始权重占 25%，可达性占 75% ──
    for (let i = 0; i < N; i++) {
      const raw = parseWeight(this.nodes[i].raw) / 100
      this.nodes[i].weight = Math.round(Math.max(1, Math.min(100, (raw * 0.25 + reach[i] * 0.75) * 100)))
    }
  }
}
