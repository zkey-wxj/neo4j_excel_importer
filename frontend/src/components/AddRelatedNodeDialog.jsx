import { useState, useEffect, useRef, useMemo } from 'react'
import { toast } from 'sonner'
import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ArrowRight, ArrowLeft } from 'lucide-react'

function clean(v) { return String(v ?? '').trim() }

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
function Field({ label, children }) { return <div className="grid gap-0.5"><Label className="text-[9px] text-muted-foreground font-mono leading-none">{label}</Label>{children}</div> }

/**
 * 新增关联节点对话框
 * 从右键菜单触发，一次性创建新节点并建立与源节点的关系
 */
export default function AddRelatedNodeDialog() {
  const dialog = useAppStore((s) => s.addRelatedDialog)
  const setDialog = useAppStore((s) => s.setAddRelatedDialog)
  const groupId = useAppStore((s) => s.groupId)
  const links = useAppStore((s) => s.links)
  const mutate = useAppStore((s) => s.mutate)
  const loadGroup = useAppStore((s) => s.loadGroup)
  const confirm = useAppStore((s) => s.confirm)

  const [relType, setRelType] = useState('')
  const [relDesc, setRelDesc] = useState('')
  const [nid, setNid] = useState('')
  const [name, setName] = useState('')
  const [labels, setLabels] = useState('')
  const [desc, setDesc] = useState('')
  const [props, setProps] = useState([{ key: '', value: '' }])
  const [busy, setBusy] = useState(false)
  const [showSuggestions, setShowSuggestions] = useState(false)
  const nidRef = useRef(null)
  const wrapperRef = useRef(null)

  // 已有关系类型列表（去重）
  const existingRelTypes = useMemo(() => {
    const set = new Set()
    for (const link of links) {
      const rt = clean(link.raw?.rel_type ?? link.rel_type)
      if (rt) set.add(rt)
    }
    return [...set].sort()
  }, [links])

  // 过滤建议
  const filteredSuggestions = useMemo(() => {
    if (!relType) return existingRelTypes
    return existingRelTypes.filter((t) => t.toLowerCase().includes(relType.toLowerCase()))
  }, [relType, existingRelTypes])

  // 打开时重置表单并聚焦
  useEffect(() => {
    if (dialog) {
      setRelType('')
      setRelDesc('')
      setNid('')
      setName('')
      setLabels('')
      setDesc('')
      setProps([{ key: '', value: '' }])
      setShowSuggestions(false)
      setTimeout(() => nidRef.current?.focus(), 100)
    }
  }, [dialog])

  // ESC 关闭
  useEffect(() => {
    if (!dialog) return
    const handler = (e) => { if (e.key === 'Escape') setDialog(null) }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [dialog, setDialog])

  if (!dialog) return null

  const isOutgoing = dialog.direction === 'outgoing'
  const srcNid = isOutgoing ? dialog.sourceNid : nid
  const tgtNid = isOutgoing ? nid : dialog.sourceNid

  const handleConfirm = async () => {
    const g = clean(groupId)
    const newNid = clean(nid)
    const rt = clean(relType) || '包含'

    if (!g) { toast.error('group_id 不能为空'); return }
    if (!newNid) { toast.error('节点 ID 不能为空'); return }

    if (!await confirm(
      `新增节点「${clean(name) || newNid}」并建立关系\n` +
      `${isOutgoing ? dialog.sourceName + ' → ' + (clean(name) || newNid) : (clean(name) || newNid) + ' → ' + dialog.sourceName}\n` +
      `关系类型: ${rt}`
    )) return

    setBusy(true)
    try {
      // 1. 创建节点
      const nodeResult = await mutate('/group-graph/api/node', 'POST', {
        group_id: g, nid: newNid, name: clean(name),
        labels: labels.split(',').map((s) => clean(s)).filter(Boolean),
        description: clean(desc), properties: kvToObj(props),
      })
      if (!nodeResult) { toast.error('节点创建失败'); return }

      // 2. 创建关系
      const relResult = await mutate('/group-graph/api/relation', 'POST', {
        group_id: g, source_nid: srcNid, target_nid: tgtNid,
        rel_type: rt, description: clean(relDesc), properties: {},
      })
      if (!relResult) { toast.error('关系创建失败'); return }

      toast.success('节点和关系创建成功')
      setDialog(null)
    } catch (e) {
      toast.error(e.message)
    } finally {
      setBusy(false)
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.isComposing && e.target.tagName !== 'TEXTAREA') {
      e.preventDefault()
      handleConfirm()
    }
  }

  const px = Math.min(Math.max(dialog.sourceName ? 200 : 100, 100), window.innerWidth - 360)
  const py = Math.max(60, Math.min(120, window.innerHeight - 600))

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-[2px]"
      onClick={() => setDialog(null)}>
      <div
        ref={wrapperRef}
        className={cn(
          'w-[340px] max-h-[85vh] overflow-y-auto',
          'bg-card/95 backdrop-blur-md border border-border',
          'rounded-xl shadow-2xl font-mono',
          'animate-in fade-in-0 zoom-in-95 duration-150',
        )}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        {/* 头部 */}
        <div className="px-4 py-2.5 border-b border-border flex items-center gap-2">
          {isOutgoing
            ? <ArrowRight className="size-4 text-primary" />
            : <ArrowLeft className="size-4 text-primary" />}
          <div className="flex-1 min-w-0">
            <div className="text-xs font-semibold truncate">
              {isOutgoing
                ? <>{dialog.sourceName} <span className="text-muted-foreground">→</span> 新节点</>
                : <>新节点 <span className="text-muted-foreground">→</span> {dialog.sourceName}</>}
            </div>
            <div className="text-[10px] text-muted-foreground">
              {isOutgoing ? '新增下级节点（出边）' : '新增上级节点（入边）'}
            </div>
          </div>
          <button onClick={() => setDialog(null)}
            className="text-muted-foreground hover:text-foreground p-1 rounded hover:bg-muted/50 text-xs">×</button>
        </div>

        {/* 关系信息 */}
        <div className="px-4 pt-3 pb-1">
          <div className="text-[10px] text-muted-foreground font-semibold mb-2">关系信息</div>
          <div className="grid gap-2">
            <div className="relative">
              <Field label="关系类型">
                <Input
                  placeholder="包含"
                  value={relType}
                  onChange={(e) => { setRelType(e.target.value); setShowSuggestions(true) }}
                  onFocus={() => setShowSuggestions(true)}
                  onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
                  className="h-7 text-xs font-mono"
                />
              </Field>
              {showSuggestions && filteredSuggestions.length > 0 && (
                <div className="absolute z-10 left-0 right-0 mt-0.5 max-h-24 overflow-y-auto bg-card border border-border rounded-md shadow-lg">
                  {filteredSuggestions.slice(0, 8).map((t) => (
                    <button key={t}
                      className="w-full px-2 py-1 text-left text-xs font-mono hover:bg-muted/50 transition-colors"
                      onMouseDown={(e) => { e.preventDefault(); setRelType(t); setShowSuggestions(false) }}>
                      {t}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <Field label="关系描述">
              <Input placeholder="可选" value={relDesc} onChange={(e) => setRelDesc(e.target.value)} className="h-7 text-xs font-mono" />
            </Field>
          </div>
        </div>

        {/* 节点信息 */}
        <div className="px-4 pt-2 pb-3">
          <div className="text-[10px] text-muted-foreground font-semibold mb-2">新节点信息</div>
          <div className="grid gap-2">
            <div className="grid grid-cols-2 gap-2">
              <Field label="节点 ID (nid)">
                <Input ref={nidRef} placeholder="唯一ID" value={nid} onChange={(e) => setNid(e.target.value)} className="h-7 text-xs font-mono" />
              </Field>
              <Field label="名称">
                <Input placeholder="显示名称" value={name} onChange={(e) => setName(e.target.value)} className="h-7 text-xs font-mono" />
              </Field>
            </div>
            <Field label="标签 (逗号分隔)">
              <Input placeholder="Core,Service" value={labels} onChange={(e) => setLabels(e.target.value)} className="h-7 text-xs font-mono" />
            </Field>
            <Field label="描述">
              <Input placeholder="可选" value={desc} onChange={(e) => setDesc(e.target.value)} className="h-7 text-xs font-mono" />
            </Field>
            <Field label="属性">
              <KvList items={props} onChange={setProps} />
            </Field>
          </div>
        </div>

        {/* 按钮 */}
        <div className="px-4 pb-3 flex gap-2">
          <Button onClick={handleConfirm} disabled={busy} size="sm" className="flex-1 h-7 text-xs font-mono">
            {busy ? '创建中...' : '创建节点并关联'}
          </Button>
          <Button onClick={() => setDialog(null)} disabled={busy} variant="outline" size="sm" className="h-7 text-xs font-mono px-4">
            取消
          </Button>
        </div>
      </div>
    </div>
  )
}
