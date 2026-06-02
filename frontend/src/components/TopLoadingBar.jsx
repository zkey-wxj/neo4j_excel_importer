import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'

/**
 * 顶部加载指示器
 * 图谱数据加载时显示在统计栏下方
 */
export default function TopLoadingBar() {
  const isLoadingData = useAppStore((s) => s.isLoadingData)

  return (
    <div
      className={cn(
        'absolute top-[72px] left-1/2 -translate-x-1/2 z-10',
        'flex items-center gap-1.5 font-mono text-xs text-muted-foreground',
        'transition-all duration-300 ease-out',
        isLoadingData
          ? 'opacity-100 translate-y-0'
          : 'opacity-0 -translate-y-2 pointer-events-none'
      )}
    >
      <div className="w-6 h-6 rounded-full border-2 border-muted-foreground/30 border-t-primary animate-spin" />
    </div>
  )
}
