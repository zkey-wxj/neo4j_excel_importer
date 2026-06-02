import { useState, useRef, useEffect, useCallback } from 'react'
import { toast } from 'sonner'
import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

function clean(v) { return String(v ?? '').trim() }

/**
 * 拖拽建边浮窗组件
 * 在画布上从一个节点 Shift+拖拽到另一个节点后弹出，
 * 用于输入关系类型和描述，确认后创建关系
 */
export default function EdgeCreatePopover() {
  const edgeCreating = useAppStore((s) => s.edgeCreating)
  const edgeSourceId = useAppStore((s) => s.edgeSourceId)
  const edgeTargetId = useAppStore((s) => s.edgeTargetId)
  const edgePopoverPos = useAppStore((s) => s.edgePopoverPos)
  const groupId = useAppStore((s) => s.groupId)
  const rawNodes = useAppStore((s) => s.rawNodes)
  const mutate = useAppStore((s) => s.mutate)
  const resetEdgeCreation = useAppStore((s) => s.resetEdgeCreation)
  const setStatus = useAppStore((s) => s.setStatus)

  const [relType, setRelType] = useState('')
  const [desc, setDesc] = useState('')
  const [busy, setBusy] = useState(false)
  const inputRef = useRef(null)
  const wrapperRef = useRef(null)

  // 弹窗打开时聚焦输入框，重置表单
  useEffect(() => {
    if (edgeCreating && edgePopoverPos) {
      setRelType('')
      setDesc('')
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [edgeCreating, edgePopoverPos])

  // 点击外部关闭
  useEffect(() => {
    if (!edgeCreating || !edgePopoverPos) return
    const handler = (e) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target)) {
        resetEdgeCreation()
        const canvas = document.querySelector('canvas')
        if (canvas) canvas.style.cursor = 'default'
      }
    }
    document.addEventListener('pointerdown', handler)
    return () => document.removeEventListener('pointerdown', handler)
  }, [edgeCreating, edgePopoverPos, resetEdgeCreation])

  // ESC 关闭（浮窗自身的键盘监听）
  useEffect(() => {
    if (!edgeCreating || !edgePopoverPos) return
    const handler = (e) => {
      if (e.key === 'Escape') {
        resetEdgeCreation()
        const canvas = document.querySelector('canvas')
        if (canvas) canvas.style.cursor = 'default'
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [edgeCreating, edgePopoverPos, resetEdgeCreation])

  const handleConfirm = useCallback(async () => {
    const rt = clean(relType) || 'RELATED'
    const srcNode = rawNodes.get(edgeSourceId)
    const tgtNode = rawNodes.get(edgeTargetId)
    const srcNid = clean(srcNode?.nid ?? edgeSourceId)
    const tgtNid = clean(tgtNode?.nid ?? edgeTargetId)
    const gid = clean(groupId)

    if (!gid) { setStatus('group_id 不能为空', true); return }
    if (!srcNid || !tgtNid) { setStatus('节点 ID 无效', true); return }

    setBusy(true)
    try {
      const r = await mutate('/group-graph/api/relation', 'POST', {
        group_id: gid,
        source_nid: srcNid,
        target_nid: tgtNid,
        rel_type: rt,
        description: clean(desc),
        properties: {},
      })
      r ? toast.success('关系创建成功') : toast.error('关系创建失败')
    } finally {
      setBusy(false)
      resetEdgeCreation()
      const canvas = document.querySelector('canvas')
      if (canvas) canvas.style.cursor = 'default'
    }
  }, [relType, desc, edgeSourceId, edgeTargetId, rawNodes, groupId, mutate, resetEdgeCreation, setStatus])

  // Enter 确认
  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.isComposing) {
      e.preventDefault()
      handleConfirm()
    }
  }

  if (!edgeCreating || !edgePopoverPos) return null

  const srcNode = rawNodes.get(edgeSourceId)
  const tgtNode = rawNodes.get(edgeTargetId)
  const srcName = srcNode?.name || srcNode?.nid || edgeSourceId
  const tgtName = tgtNode?.name || tgtNode?.nid || edgeTargetId

  // 定位：浮窗居中于目标节点右上方，防止超出视口
  const px = Math.min(edgePopoverPos.x + 16, window.innerWidth - 240)
  const py = Math.max(edgePopoverPos.y - 80, 10)

  return (
    <div
      ref={wrapperRef}
      className={cn(
        'fixed z-50 w-[220px]',
        'bg-card/95 backdrop-blur-md border border-border',
        'rounded-xl shadow-xl font-mono',
        'animate-in fade-in-0 zoom-in-95 duration-150',
      )}
      style={{ left: `${px}px`, top: `${py}px` }}
      onKeyDown={handleKeyDown}
    >
      {/* 头部：源→目标 */}
      <div className="px-3 py-1.5 border-b border-border">
        <div className="text-xs text-muted-foreground truncate">
          <span className="text-foreground font-semibold">{srcName}</span>
          {' → '}
          <span className="text-foreground font-semibold">{tgtName}</span>
        </div>
      </div>

      {/* 表单 */}
      <div className="p-2.5 grid gap-2">
        <div className="grid gap-0.5">
          <Label className="text-xs text-muted-foreground font-mono leading-none">
            关系类型 <span className="text-destructive">*</span>
          </Label>
          <Input
            ref={inputRef}
            placeholder="RELATED"
            value={relType}
            onChange={(e) => setRelType(e.target.value)}
            className="h-6 text-xs font-mono"
          />
        </div>
        <div className="grid gap-0.5">
          <Label className="text-xs text-muted-foreground font-mono leading-none">
            描述
          </Label>
          <Input
            placeholder="可选"
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            className="h-6 text-xs font-mono"
          />
        </div>
        <div className="flex gap-1 mt-0.5">
          <Button
            onClick={handleConfirm}
            disabled={busy}
            size="sm"
            className="flex-1 h-6 text-xs font-mono"
          >
            {busy ? '创建中...' : '创建关系'}
          </Button>
          <Button
            onClick={() => {
              resetEdgeCreation()
              const canvas = document.querySelector('canvas')
              if (canvas) canvas.style.cursor = 'default'
            }}
            disabled={busy}
            variant="outline"
            size="sm"
            className="h-6 text-xs font-mono px-3"
          >
            取消
          </Button>
        </div>
      </div>
    </div>
  )
}
