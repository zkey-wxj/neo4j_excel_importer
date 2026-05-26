import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'

function StatCell({ value, label, clickable, active, onClick }) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center px-4 py-2 font-mono',
        'bg-card border-r border-border last:border-r-0',
        'transition-colors select-none',
        clickable && 'cursor-pointer hover:bg-muted/50',
        active && 'bg-destructive/10'
      )}
      onClick={clickable ? onClick : undefined}
    >
      <span
        className={cn(
          'text-sm font-semibold tabular-nums leading-none',
          active ? 'text-destructive' : 'text-primary'
        )}
      >
        {value}
      </span>
      <span className="text-[9px] text-muted-foreground tracking-wide mt-1 leading-none">
        {label}
      </span>
    </div>
  )
}

function Spinner() {
  return (
    <div className="flex items-center justify-center bg-card px-3 py-2">
      <div
        className="w-3.5 h-3.5 rounded-full border-2 border-muted border-t-primary animate-spin"
      />
    </div>
  )
}

export default function StatsBar({ graphCanvas }) {
  const isLoading = useAppStore((s) => s.isLoading)
  const orphanFilter = useAppStore((s) => s.orphanFilter)
  const setOrphanFilter = useAppStore((s) => s.setOrphanFilter)
  const zoom = useAppStore((s) => s.zoom)
  const stats = useAppStore((s) => s.stats)

  const nodeCount = stats?.nodeCount ?? 0
  const linkCount = stats?.linkCount ?? 0
  const orphanCount = stats?.orphanCount ?? 0
  const nodeTypeCount = stats?.nodeTypeCount ?? 0
  const relTypeCount = stats?.relTypeCount ?? 0

  return (
    <div
      className={cn(
        'absolute top-4 left-1/2 -translate-x-1/2 z-10 flex',
        'rounded-xl overflow-hidden border border-border',
        'shadow-sm bg-card',
        'font-mono max-w-[calc(100vw-760px)]'
      )}
    >
      {isLoading && <Spinner />}
      <StatCell value={nodeCount} label="节点" />
      <StatCell value={linkCount} label="关系" />
      <StatCell
        value={orphanCount}
        label="孤立"
        clickable
        active={orphanFilter}
        onClick={() => {
          setOrphanFilter(!orphanFilter)
          graphCanvas?.gatherIsolates?.()
        }}
      />
      <StatCell value={nodeTypeCount} label="节点类型" />
      <StatCell value={relTypeCount} label="关系类型" />
      <StatCell
        value={`${(zoom ?? 1).toFixed(2)}x`}
        label="缩放"
      />
    </div>
  )
}
