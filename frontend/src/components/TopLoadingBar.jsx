import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'

/** 大数字加千分位，便于一眼读出加载规模 */
function fmtNum(n) {
  return Number(n || 0).toLocaleString('en-US')
}

/**
 * 由加载进度状态推导出「进度是否确定 / 百分比 / 展示文案」。
 *
 * 节点与关系是各自独立分页推进的（节点可能先加载完而关系仍在继续），
 * 因此进度按两者的总条目数加权，避免节点加载完时进度条虚满；
 * 总数缺失（后端未统计，约定为 -1）的那一项退化为「已加载计数」。
 */
function describeProgress(lp) {
  const loadedNodes = lp?.loadedNodes ?? 0
  const totalNodes = lp?.totalNodes ?? -1
  const loadedRels = lp?.loadedRels ?? 0
  const totalRels = lp?.totalRels ?? -1
  const page = lp?.page ?? 0

  const knownNodes = totalNodes > 0
  const knownRels = totalRels > 0
  const known = knownNodes || knownRels

  const all = (knownNodes ? totalNodes : loadedNodes) + (knownRels ? totalRels : loadedRels)
  const done = loadedNodes + loadedRels
  const pct = known && all > 0 ? Math.min(100, Math.round((done / all) * 100)) : 0

  const nodes = `${fmtNum(loadedNodes)}${knownNodes ? `/${fmtNum(totalNodes)}` : ''} 节点`
  const rels = `${fmtNum(loadedRels)}${knownRels ? `/${fmtNum(totalRels)}` : ''} 关系`
  const label = page > 0 ? `${nodes} · ${rels}${known ? ` · ${pct}%` : ''}` : '准备加载...'

  return { known, pct, label }
}

/**
 * 顶部加载进度指示器（图谱分页加载时显示在统计栏下方）
 * - 总数已知：确定性进度条 + 「已加载/总数」计数
 * - 总数未知：流动的脉冲进度条 + 已加载计数
 */
export default function TopLoadingBar() {
  const isLoadingData = useAppStore((s) => s.isLoadingData)
  const loadProgress = useAppStore((s) => s.loadProgress)
  const { known, pct, label } = describeProgress(loadProgress)

  return (
    <div
      className={cn(
        'absolute top-[72px] left-1/2 -translate-x-1/2 z-10 pointer-events-none',
        'transition-all duration-300 ease-out',
        isLoadingData
          ? 'opacity-100 translate-y-0'
          : 'opacity-0 -translate-y-2 pointer-events-none'
      )}
    >
      <div className="w-[380px] flex flex-col gap-1.5">
        <div className="h-[3px] w-full rounded-full bg-muted-foreground/20 overflow-hidden">
          {known ? (
            <div
              className="h-full bg-primary rounded-full transition-[width] duration-300 ease-out"
              style={{ width: `${pct}%` }}
            />
          ) : (
            <div className="h-full w-1/3 bg-primary/70 rounded-full animate-pulse" />
          )}
        </div>
        <div className="font-mono text-[11px] tabular-nums text-muted-foreground text-center">
          {label}
        </div>
      </div>
    </div>
  )
}
