import { useState, useEffect, useCallback, useRef } from 'react'
import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Database, CircleDot, GitBranch, Route, Repeat } from 'lucide-react'

function clean(v) { return String(v || '').trim() }

function KvList({ items, onChange }) {
  const add = () => onChange([...items, { key: '', value: '' }])
  const remove = (idx) => { const n = items.filter((_, i) => i !== idx); onChange(n.length ? n : [{ key: '', value: '' }]) }
  const update = (idx, f, v) => onChange(items.map((it, i) => i === idx ? { ...it, [f]: v } : it))
  return (
    <div className="grid gap-1">
      {items.map((item, idx) => (
        <div key={idx} className="grid grid-cols-[1fr_1fr_24px] gap-1 items-center">
          <Input placeholder="key" value={item.key} onChange={(e) => update(idx, 'key', e.target.value)} className="h-6 text-xs font-mono" />
          <Input placeholder="value" value={item.value} onChange={(e) => update(idx, 'value', e.target.value)} className="h-6 text-xs font-mono" />
          <Button type="button" variant="ghost" size="icon" onClick={() => remove(idx)} className="h-6 w-6 text-muted-foreground hover:text-destructive shrink-0"><span className="text-xs">×</span></Button>
        </div>
      ))}
      <Button type="button" variant="ghost" size="sm" onClick={add} className="h-5 text-[9px] font-mono text-muted-foreground hover:text-foreground justify-start">+ 添加属性</Button>
    </div>
  )
}

function kvToObj(items) { const o = {}; items.forEach(({ key, value }) => { const k = clean(key); if (k) o[k] = value }); return o }
function objToKv(obj) { const e = Object.entries(obj || {}); return e.length ? e.map(([k, v]) => ({ key: String(k), value: v == null ? '' : String(v) })) : [{ key: '', value: '' }] }
function Field({ label, children }) { return <div className="grid gap-0.5"><Label className="text-[9px] text-muted-foreground font-mono leading-none">{label}</Label>{children}</div> }

function PickableInput({ label, placeholder, value, onChange, inputId }) {
  const pickTarget = useAppStore((s) => s.pickTarget)
  const setPickTarget = useAppStore((s) => s.setPickTarget)
  const active = pickTarget === inputId
  return (
    <Field label={label}>
      <div className="flex gap-1">
        <Input placeholder={placeholder} value={value} onChange={(e) => onChange(e.target.value)} className="h-6 text-xs font-mono flex-1" />
        <Button type="button" variant="ghost" size="sm" onClick={() => setPickTarget(active ? null : inputId)} className={cn('text-[9px] font-mono px-1.5 h-6 shrink-0', active && 'text-primary bg-primary/10 border border-primary/30')}>{active ? '...' : '抓取'}</Button>
      </div>
    </Field>
  )
}

function CrudBtns({ onCreate, onUpdate, onDelete, busy }) {
  return (
    <div className="flex gap-1 mt-2">
      <Button onClick={onCreate} disabled={busy} size="sm" className="flex-1 h-6 text-xs font-mono">新增</Button>
      <Button onClick={onUpdate} disabled={busy} variant="outline" size="sm" className="flex-1 h-6 text-xs font-mono">更新</Button>
      <Button onClick={onDelete} disabled={busy} variant="destructive" size="sm" className="flex-1 h-6 text-xs font-mono">删除</Button>
    </div>
  )
}

const SECTIONS = [
  { key: 'load', icon: Database, label: '图谱加载' },
  { key: 'node', icon: CircleDot, label: '节点操作' },
  { key: 'rel', icon: GitBranch, label: '关系操作' },
  { key: 'path', icon: Route, label: '路径查询' },
  { key: 'replace', icon: Repeat, label: '关系替换' },
]

export default function OpsPanel() {
  const [expanded, setExpanded] = useState(false)
  const [activeSection, setActiveSection] = useState('load')
  const [arrowTop, setArrowTop] = useState(0)
  const panelRef = useRef(null)
  const iconRefs = useRef({})
  const groupId = useAppStore((s) => s.groupId)
  const setGroupId = useAppStore((s) => s.setGroupId)
  const pageSize = useAppStore((s) => s.pageSize)
  const setPageSize = useAppStore((s) => s.setPageSize)
  const loadGroup = useAppStore((s) => s.loadGroup)
  const mutate = useAppStore((s) => s.mutate)
  const findPath = useAppStore((s) => s.findPath)
  const setStatus = useAppStore((s) => s.setStatus)
  const setPathHighlight = useAppStore((s) => s.setPathHighlight)
  const pickTarget = useAppStore((s) => s.pickTarget)
  const setPickTarget = useAppStore((s) => s.setPickTarget)
  const pickedNid = useAppStore((s) => s.pickedNid)
  const setPickedNid = useAppStore((s) => s.setPickedNid)
  const selectedNodeId = useAppStore((s) => s.selectedNodeId)
  const nodes = useAppStore((s) => s.nodes)
  const opsExpand = useAppStore((s) => s.opsExpand)
  const setOpsExpand = useAppStore((s) => s.setOpsExpand)
  const status = useAppStore((s) => s.status)
  const statusError = useAppStore((s) => s.statusError)
  const confirm = useAppStore((s) => s.confirm)

  const [nodeNid, setNodeNid] = useState('')
  const [nodeName, setNodeName] = useState('')
  const [nodeLabels, setNodeLabels] = useState('')
  const [nodeDesc, setNodeDesc] = useState('')
  const [nodeProps, setNodeProps] = useState([{ key: '', value: '' }])
  const [nodeMeta, setNodeMeta] = useState([{ key: '', value: '' }])
  const [relSource, setRelSource] = useState('')
  const [relTarget, setRelTarget] = useState('')
  const [relType, setRelType] = useState('')
  const [relDesc, setRelDesc] = useState('')
  const [relProps, setRelProps] = useState([{ key: '', value: '' }])
  const [pathSource, setPathSource] = useState('')
  const [pathTarget, setPathTarget] = useState('')
  const [hasPath, setHasPath] = useState(false)
  const [replaceOld, setReplaceOld] = useState('')
  const [replaceNew, setReplaceNew] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!selectedNodeId) return
    const n = nodes?.find((x) => x.id === selectedNodeId)
    if (!n) return
    const r = n.raw || n
    setNodeNid(clean(r.nid))
    setNodeName(clean(r.name))
    setNodeLabels(Array.isArray(r.labels) ? r.labels.join(',') : '')
    setNodeDesc(clean(r.description))
    setNodeProps(objToKv(r.properties))
    setNodeMeta(objToKv(r.meta))
  }, [selectedNodeId])

  useEffect(() => {
    if (!opsExpand) return
    setOpsExpand(false)
    handleSectionClick('node')
  }, [opsExpand])

  useEffect(() => {
    if (!pickTarget || !pickedNid) return
    const map = { relSource: setRelSource, relTarget: setRelTarget, pathSource: setPathSource, pathTarget: setPathTarget, replaceOld: setReplaceOld, replaceNew: setReplaceNew }
    map[pickTarget]?.(pickedNid)
    setPickTarget(null)
    setPickedNid(null)
  }, [pickedNid, pickTarget])

  const notify = useCallback(async (m, t) => { const { toast } = await import('sonner'); t === 'error' ? toast.error(m) : toast.success(m) }, [])
  const buildNodePayload = () => { const g = clean(groupId), u = clean(nodeNid); if (!g) throw new Error('group_id 不能为空'); if (!u) throw new Error('nid 不能为空'); return { group_id: g, nid: u, name: clean(nodeName), labels: nodeLabels.split(',').map(s => clean(s)).filter(Boolean), description: clean(nodeDesc), properties: kvToObj(nodeProps), meta: kvToObj(nodeMeta) } }
  const buildRelPayload = () => { const g = clean(groupId), s = clean(relSource), t = clean(relTarget), rt = clean(relType); if (!g || !s || !t || !rt) throw new Error('字段不能为空'); return { group_id: g, source_nid: s, target_nid: t, rel_type: rt, description: clean(relDesc), properties: kvToObj(relProps) } }
  const wrap = (fn) => async () => { if (busy) return; try { setBusy(true); await fn() } catch (e) { notify(e.message, 'error') } finally { setBusy(false) } }

  const handleLoad = wrap(async () => { const g = clean(groupId); if (!g) { setStatus('group_id 不能为空', true); return }; await loadGroup(g) })
  const handleNodeCreate = wrap(async () => { const p = buildNodePayload(); if (!await confirm(`确认新增节点？\n节点ID: ${p.nid}\n名称: ${p.name || '-'}`)) return; await mutate('/group-graph/api/node', 'POST', p) })
  const handleNodeUpdate = wrap(async () => { const p = buildNodePayload(); if (!await confirm(`确认更新节点？\n节点ID: ${p.nid}\n名称: ${p.name || '-'}`)) return; await mutate('/group-graph/api/node', 'PUT', p) })
  const handleNodeDelete = wrap(async () => { const p = buildNodePayload(); if (!await confirm(`确认删除节点？\n节点ID: ${p.nid}\n名称: ${p.name || '-'}`)) return; await mutate('/group-graph/api/node', 'DELETE', { group_id: p.group_id, nid: p.nid }) })
  const handleRelCreate = wrap(async () => { const p = buildRelPayload(); if (!await confirm(`确认新增关系？\n源节点: ${p.source_nid}\n目标节点: ${p.target_nid}\n类型: ${p.rel_type}`)) return; await mutate('/group-graph/api/relation', 'POST', p) })
  const handleRelUpdate = wrap(async () => { const p = buildRelPayload(); if (!await confirm(`确认更新关系？\n源节点: ${p.source_nid}\n目标节点: ${p.target_nid}\n类型: ${p.rel_type}`)) return; await mutate('/group-graph/api/relation', 'PUT', p) })
  const handleRelDelete = wrap(async () => { const p = buildRelPayload(); if (!await confirm(`确认删除关系？\n源节点: ${p.source_nid}\n目标节点: ${p.target_nid}\n类型: ${p.rel_type}`)) return; await mutate('/group-graph/api/relation', 'DELETE', { group_id: p.group_id, source_nid: p.source_nid, target_nid: p.target_nid }) })
  const handleFindPath = () => { const s = clean(pathSource), t = clean(pathTarget); if (!s || !t) { setStatus('请输入起点和终点', true); return }; if (s === t) { setStatus('起点和终点相同', true); return }; const r = findPath(s, t); if (r.error) { setStatus(r.error, true); return }; setPathHighlight(r.highlight); setHasPath(true); setStatus(`路径: ${r.hops} 跳, ${r.pathNodes.length} 节点`) }
  const handleClearPath = () => { setPathHighlight(null); setHasPath(false); setStatus('已清除路径高亮') }
  const handleReplace = wrap(async () => { const g = clean(groupId), o = clean(replaceOld), n = clean(replaceNew); if (!g || !o || !n) { notify('字段不能为空', 'error'); return }; if (o === n) { notify('不能相同', 'error'); return }; if (!await confirm(`确认将全部关系从 ${o} 转移至 ${n}？`)) return; await mutate('/group-graph/api/replace-node-relations', 'POST', { group_id: g, old_nid: o, new_nid: n }) })

  const [panelOffset, setPanelOffset] = useState(0)
  const [panelReady, setPanelReady] = useState(false)

  const handleSectionClick = (key) => {
    if (expanded && activeSection === key) {
      setExpanded(false)
      return
    }
    setActiveSection(key)
    setPanelReady(false)
    setExpanded(true)
    // Wait for panel to render (invisible), then measure and position
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        try {
          const icon = iconRefs.current[key]
          const bar = document.querySelector('[data-iconbar]')
          const panel = panelRef.current
          if (icon && bar && panel) {
            const barTop = bar.getBoundingClientRect().top
            const barH = bar.getBoundingClientRect().height
            const panelH = panel.getBoundingClientRect().height
            const iconCenter = icon.getBoundingClientRect().top + icon.getBoundingClientRect().height / 2 - barTop
            // Panel offset: align panel top so arrow matches icon center
            let offset = iconCenter - 20
            offset = Math.max(0, Math.min(offset, barH - panelH))
            setPanelOffset(offset)
            // Arrow tip at icon center: rotate(-45) shifts visual tip ~4px down from CSS top
            const arrowY = Math.max(0, Math.min(iconCenter - offset - 4, panelH - 11))
            setArrowTop(arrowY)
            setPanelReady(true)
          }
        } catch (e) { setPanelReady(true) }
      })
    })
  }

  const IconBar = (
    <div data-iconbar className={cn(
      'flex flex-col items-center gap-1 py-2 w-10 shrink-0',
      'bg-card/90 backdrop-blur-md border border-border shadow-lg rounded-xl',
      pickTarget && 'ring-2 ring-primary/30'
    )}>
      {SECTIONS.map(({ key, icon: Icon, label }) => (
        <Button key={key} ref={(el) => { iconRefs.current[key] = el }} variant="ghost" size="icon"
          onClick={() => handleSectionClick(key)} title={label}
          className={cn('size-7 text-muted-foreground hover:text-foreground',
            activeSection === key && expanded && 'text-primary bg-primary/10')}>
          <Icon className="size-3.5" />
        </Button>
      ))}
    </div>
  )

  return (
    <div className="absolute left-3 top-[20%] z-20 pointer-events-none">
      <div className="pointer-events-auto relative inline-flex">
        {IconBar}

        {/* Content panel - positioned relative to icon bar */}
        {expanded && (
          <div className={cn('absolute left-full ml-2.5 transition-opacity duration-100', !panelReady && 'opacity-0 pointer-events-none')} style={{ top: `${panelOffset}px` }}>
            {/* Arrow: rotated square pointing left */}
            <div className="absolute pointer-events-none"
              style={{ left: '-5px', top: `${arrowTop}px`, zIndex: 30 }}>
              <div className="w-[11px] h-[11px] bg-card border border-border border-r-0 border-b-0 -rotate-45" />
            </div>
            <div ref={panelRef} className={cn(
              'relative',
              'w-[290px] max-h-[80vh]',
              'bg-card/95 backdrop-blur-md border border-border',
              'rounded-xl shadow-lg font-mono flex flex-col',
              'animate-in slide-in-from-left-1 duration-150'
            )}>
            <div className="flex items-center justify-between px-3 py-1.5 border-b border-border shrink-0">
              <span className="text-xs font-mono font-semibold text-foreground">
                {SECTIONS.find(s => s.key === activeSection)?.label}
              </span>
              <button
                onClick={() => setExpanded(false)}
                className="text-muted-foreground hover:text-foreground p-0.5 rounded hover:bg-muted/50 transition-colors text-xs leading-none"
              >
                ×
              </button>
            </div>

          {/* Content */}
          <div className="flex-1 overflow-y-auto graph-scrollbar p-2.5 min-h-0">
            {activeSection === 'load' && <div className="grid gap-2">
              <div className="grid grid-cols-2 gap-2"><Field label="分组ID"><Input placeholder="product_demo" value={groupId} onChange={(e) => setGroupId(e.target.value)} className="h-7 text-xs font-mono" /></Field><Field label="每页条数"><Input value={pageSize} onChange={(e) => setPageSize(e.target.value)} className="h-7 text-xs font-mono" /></Field></div>
              <Button onClick={handleLoad} disabled={busy} className="w-full h-7 text-xs font-mono">加载图谱</Button>
            </div>}
            {activeSection === 'node' && <div className="grid gap-2">
              <div className="grid grid-cols-2 gap-2"><Field label="节点ID (nid)"><Input value={nodeNid} onChange={(e) => setNodeNid(e.target.value)} className="h-7 text-xs font-mono" placeholder="唯一ID" /></Field><Field label="名称"><Input value={nodeName} onChange={(e) => setNodeName(e.target.value)} className="h-7 text-xs font-mono" /></Field></div>
              <Field label="标签 (逗号分隔)"><Input value={nodeLabels} onChange={(e) => setNodeLabels(e.target.value)} className="h-7 text-xs font-mono" placeholder="Core,Service" /></Field>
              <Field label="描述"><Input value={nodeDesc} onChange={(e) => setNodeDesc(e.target.value)} className="h-7 text-xs font-mono" /></Field>
              <Field label="属性"><KvList items={nodeProps} onChange={setNodeProps} /></Field>
              <Field label="元信息"><KvList items={nodeMeta} onChange={setNodeMeta} /></Field>
              <CrudBtns onCreate={handleNodeCreate} onUpdate={handleNodeUpdate} onDelete={handleNodeDelete} busy={busy} />
            </div>}
            {activeSection === 'rel' && <div className="grid gap-2">
              <div className="grid grid-cols-2 gap-2"><PickableInput label="源节点" placeholder="source nid" value={relSource} onChange={setRelSource} inputId="relSource" /><PickableInput label="目标节点" placeholder="target nid" value={relTarget} onChange={setRelTarget} inputId="relTarget" /></div>
              <Field label="关系类型"><Input value={relType} onChange={(e) => setRelType(e.target.value)} className="h-7 text-xs font-mono" placeholder="RELATED" /></Field>
              <Field label="描述"><Input value={relDesc} onChange={(e) => setRelDesc(e.target.value)} className="h-7 text-xs font-mono" /></Field>
              <Field label="属性"><KvList items={relProps} onChange={setRelProps} /></Field>
              <CrudBtns onCreate={handleRelCreate} onUpdate={handleRelUpdate} onDelete={handleRelDelete} busy={busy} />
            </div>}
            {activeSection === 'path' && <div className="grid gap-2">
              <div className="grid grid-cols-2 gap-2"><PickableInput label="起点" placeholder="起始节点" value={pathSource} onChange={setPathSource} inputId="pathSource" /><PickableInput label="终点" placeholder="目标节点" value={pathTarget} onChange={setPathTarget} inputId="pathTarget" /></div>
              <div className="flex gap-1"><Button onClick={handleFindPath} size="sm" className="flex-1 h-7 text-xs font-mono">查找最短路径</Button>{hasPath && <Button onClick={handleClearPath} variant="outline" size="sm" className="h-7 w-7 p-0 font-mono"><span className="text-xs">×</span></Button>}</div>
            </div>}
            {activeSection === 'replace' && <div className="grid gap-2">
              <div className="grid grid-cols-2 gap-2"><PickableInput label="旧节点ID" placeholder="原节点 nid" value={replaceOld} onChange={setReplaceOld} inputId="replaceOld" /><PickableInput label="新节点ID" placeholder="目标节点 nid" value={replaceNew} onChange={setReplaceNew} inputId="replaceNew" /></div>
              <Button onClick={handleReplace} disabled={busy} className="w-full h-7 text-xs font-mono">替换全部关系</Button>
            </div>}
          </div>

          {/* Status */}
          <div className={cn('shrink-0 border-t border-border px-2.5 py-1 font-mono text-[9px] text-right truncate', statusError ? 'text-destructive' : 'text-muted-foreground')}>
            {status || ''}
          </div>
          </div>
        </div>
      )}
      </div>
    </div>
  )
}
