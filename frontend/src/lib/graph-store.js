import * as d3 from 'd3'
import { DEMO_NAMES, DEMO_TYPES, DEMO_RELS } from './constants'

/* ═══════════════════════════════════════════════════════════════════
 Helper utilities
 ═══════════════════════════════════════════════════════════════════ */

/** Safe string trim: null/undefined → empty trimmed string */
export function clean(v) {
  return String(v || '').trim()
}

/** Get the first label from a node's labels array, falling back to "Node" */
export function parseType(labels) {
  const a = Array.isArray(labels) ? labels.map(x => clean(x)).filter(Boolean) : []
  if (a.length > 0) return a[0]
  return 'Node'
}

/** Extract weight from a node's various locations, clamped to [1, 100] */
export function parseWeight(n) {
  for (const v of [n.weight, n.properties?.weight, n.properties?.score, n.meta?.score]) {
    const x = Number(v)
    if (Number.isFinite(x)) return Math.max(1, Math.min(100, Math.round(x)))
  }
  return 50
}

/** Display name for a node */
export function nodeLabel(n) {
  return clean(n.name) || clean(n.nid) || 'Node'
}

/** Relation type string */
export function relLabel(r) {
  return clean(r.rel_type) || 'RELATED'
}

/** Dedup key for a relation: source_nid=>target_nid:rel_type */
export function relStoreKey(r) {
  return `${clean(r.source_nid)}=>${clean(r.target_nid)}:${relLabel(r)}`
}

/* ═══════════════════════════════════════════════════════════════════
 Type metadata cache – color / background / radius per node type
 Colours are deterministically computed from the type string via FNV-1a hash → HSL.
 ═══════════════════════════════════════════════════════════════════ */
const _typeMetaCache = new Map()

/** Hash a string to a 32-bit integer (FNV-1a) */
function fnv1a(str) {
  let h = 0x811c9dc5
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i)
    h = (h * 0x01000193) | 0
  }
  return h >>> 0
}

/** Returns { color, bg, r } for a given node type string */
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
 Demo data generators
 ═══════════════════════════════════════════════════════════════════ */

/**
 * Simple seeded PRNG (Mulberry32) for deterministic demo data.
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
 * Generate demo nodes in a tree hierarchy for knowledge-graph root entities.
 *
 *   Level 0:  3  roots     (System)
 *   Level 1:  30 children  (Component)
 *   Level 2:  120 leaves   (Database / Model / Task)
 *   Level 3:  remainder    (Model / Task)
 *
 * Root nodes can reach every descendant → highest reachability weight.
 */
export function genDemoNodes(count) {
  const now = new Date().toISOString()
  const meta = { created_at: now, updated_at: now, source: 'demo', version: 1 }
  const rand = mulberry32(42)

  // Shuffle names with seeded PRNG
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
 * Generate demo links as a tree with cross-links.
 *
 * Tree edges: each non-root node connects to a random parent in the level
 * above, creating a clear reachability hierarchy (root → … → leaf).
 * Cross-links: a small number of random edges between same-level siblings.
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

  // Tree edges: level boundaries at 3, 33, 153
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

  // Cross-links between random same-level siblings (~10% of n)
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
 GraphStore – plain (non-React) data manager

 - nodeMap / linkMap: deduplicated raw storage (nid → node, relKey → relation)
 - mapGraphData(): convert raw → render-ready arrays with computed weights
 - computeNodeWeights(): PageRank + HITS + degree centrality composite score
 - applyMutation() / applyReplaceRelations(): frontend-side CRUD
 ═══════════════════════════════════════════════════════════════════ */

export class GraphStore {
  constructor() {
    /** @type {Map<string, object>} nid → raw node */
    this.nodeMap = new Map()
    /** @type {Map<string, object>} relStoreKey → raw relation */
    this.linkMap = new Map()
    /** Render-ready node array (populated by mapGraphData) */
    this.nodes = []
    /** Render-ready link array (populated by mapGraphData) */
    this.links = []
    /** Computed graph-level stats */
    this.stats = { orphanCount: 0, nodeTypeCount: 0, relTypeCount: 0 }
  }

  /** Clear all data */
  reset() {
    this.nodeMap.clear()
    this.linkMap.clear()
    this.nodes = []
    this.links = []
    this.stats = { orphanCount: 0, nodeTypeCount: 0, relTypeCount: 0 }
  }

  /**
   * Add nodes, dedup by nid.
   * @param {object[]} arr - raw node objects with { nid, name, labels, ... }
   */
  addNodes(arr) {
    for (const n of arr) {
      const nid = clean(n.nid)
      if (nid) this.nodeMap.set(nid, n)
    }
  }

  /**
   * Add links, dedup by source_nid=>target_nid:rel_type.
   * @param {object[]} arr - raw relation objects
   */
  addLinks(arr) {
    for (const r of arr) {
      const k = relStoreKey(r)
      if (k) this.linkMap.set(k, r)
    }
  }

  /**
   * Convert raw data to render-ready arrays.
   * Validates link connectivity, extracts type/colour, computes composite
   * node weights via PageRank + HITS + degree centrality.
   * @returns {{ nodes: object[], links: object[] }}
   */
  mapGraphData() {
    const raw = Array.from(this.nodeMap.values())
    const rels = Array.from(this.linkMap.values())
    const nidSet = new Set(this.nodeMap.keys())
    const degMap = new Map()
    const valid = []

    // Validate relations – both endpoints must exist in nodeMap
    for (const r of rels) {
      const s = clean(r.source_nid)
      const t = clean(r.target_nid)
      if (!nidSet.has(s) || !nidSet.has(t) || !s || !t) continue
      valid.push(r)
      degMap.set(s, (degMap.get(s) || 0) + 1)
      degMap.set(t, (degMap.get(t) || 0) + 1)
    }

    // Build render nodes
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

    // Map nid → render id for link resolution
    const idMap = new Map(this.nodes.map(n => [n.nid, n.id]))

    // Build render links
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

    // Compute composite node weights via graph algorithms
    this.computeNodeWeights()
    this.computeStats()

    return { nodes: this.nodes, links: this.links }
  }

  /** Compute orphan count, distinct node type count, distinct rel type count */
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
   * Apply a CRUD mutation directly to the frontend store.
   * @param {string} path  - API path (contains "/node" or "/relation")
   * @param {string} method - POST / PUT / DELETE
   * @param {object} payload - the request payload
   */
  applyMutation(path, method, payload) {
    if (path.includes('/node')) {
      const nid = clean(payload.nid)
      if (method === 'POST' || method === 'PUT') {
        this.nodeMap.set(nid, payload)
      } else if (method === 'DELETE') {
        this.nodeMap.delete(nid)
        // Also remove any relations referencing this node
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
   * Remove all relations referencing oldNid (used after replace-node-relations).
   * @param {string} oldNid
   */
  applyReplaceRelations(oldNid) {
    const toRemove = []
    for (const [k, r] of this.linkMap) {
      if (clean(r.source_nid) === oldNid || clean(r.target_nid) === oldNid) toRemove.push(k)
    }
    toRemove.forEach(k => this.linkMap.delete(k))
  }

  /**
   * Compute composite node weights for knowledge-graph root entities.
   *
   * Primary metric: reachability — how many downstream nodes a node can reach
   * via outgoing edges (BFS).  Root entities that expand into the most
   * descendants score highest.
   *
   * Final score = rawWeight * 0.25 + reachability * 0.75, clamped to [1, 100].
   */
  computeNodeWeights() {
    const N = this.nodes.length
    if (!N) return
    const L = this.links.length
    if (!L) {
      this.nodes.forEach(n => { n.weight = parseWeight(n.raw) })
      return
    }

    // Build adjacency (outgoing only) indexed by node position
    const idIdx = new Map(this.nodes.map((n, i) => [n.id, i]))
    const adj = Array.from({ length: N }, () => [])

    for (let i = 0; i < L; i++) {
      const l = this.links[i]
      const si = idIdx.get(l.source)
      const ti = idIdx.get(l.target)
      if (si == null || ti == null || si === ti) continue
      adj[si].push(ti)
    }

    // ── Reachability (BFS from each node) ──
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

    // ── Min-max normalise reachability to [0, 1] ──
    let mn = Infinity, mx = -Infinity
    for (let i = 0; i < N; i++) {
      if (reach[i] < mn) mn = reach[i]
      if (reach[i] > mx) mx = reach[i]
    }
    const range = mx - mn || 1
    for (let i = 0; i < N; i++) reach[i] = (reach[i] - mn) / range

    // ── Weighted combination ──
    for (let i = 0; i < N; i++) {
      const raw = parseWeight(this.nodes[i].raw) / 100
      this.nodes[i].weight = Math.round(Math.max(1, Math.min(100, (raw * 0.25 + reach[i] * 0.75) * 100)))
    }
  }
}
