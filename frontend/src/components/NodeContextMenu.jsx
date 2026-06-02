import { useEffect, useRef } from 'react'
import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'
import { ArrowRight, ArrowLeft } from 'lucide-react'

/**
 * 节点右键上下文菜单
 * 右键点击节点后弹出，提供「新增关联节点」操作
 */
export default function NodeContextMenu() {
  const contextMenu = useAppStore((s) => s.contextMenu)
  const setContextMenu = useAppStore((s) => s.setContextMenu)
  const setAddRelatedDialog = useAppStore((s) => s.setAddRelatedDialog)
  const ref = useRef(null)

  // 点击外部关闭
  useEffect(() => {
    if (!contextMenu) return
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setContextMenu(null)
    }
    document.addEventListener('pointerdown', handler)
    return () => document.removeEventListener('pointerdown', handler)
  }, [contextMenu, setContextMenu])

  // ESC 关闭
  useEffect(() => {
    if (!contextMenu) return
    const handler = (e) => { if (e.key === 'Escape') setContextMenu(null) }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [contextMenu, setContextMenu])

  if (!contextMenu) return null

  const openDialog = (direction) => {
    setAddRelatedDialog({
      direction,
      sourceNid: contextMenu.nid,
      sourceName: contextMenu.nodeName,
    })
    setContextMenu(null)
  }

  // 防止菜单超出视口
  const px = Math.min(contextMenu.x + 4, window.innerWidth - 200)
  const py = Math.min(contextMenu.y + 4, window.innerHeight - 120)

  return (
    <div
      ref={ref}
      className={cn(
        'fixed z-50 w-[190px] py-1',
        'bg-card/95 backdrop-blur-md border border-border',
        'rounded-lg shadow-xl font-mono text-xs',
        'animate-in fade-in-0 zoom-in-95 duration-100',
      )}
      style={{ left: px, top: py }}
      onContextMenu={(e) => e.preventDefault()}
    >
      <div className="px-3 py-1.5 text-[10px] text-muted-foreground truncate border-b border-border mb-1">
        {contextMenu.nodeName}
      </div>
      <button
        className="w-full px-3 py-1.5 flex items-center gap-2 hover:bg-muted/50 transition-colors text-left"
        onClick={() => openDialog('outgoing')}
      >
        <ArrowRight className="size-3 text-primary" />
        <span>新增下级节点（→ 出边）</span>
      </button>
      <button
        className="w-full px-3 py-1.5 flex items-center gap-2 hover:bg-muted/50 transition-colors text-left"
        onClick={() => openDialog('incoming')}
      >
        <ArrowLeft className="size-3 text-primary" />
        <span>新增上级节点（← 入边）</span>
      </button>
    </div>
  )
}
