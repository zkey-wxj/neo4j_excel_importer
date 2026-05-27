import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'

/**
 * 顶部转圈加载指示器
 * 弹出到统计栏下方，无背景色，仅一个 spinner + 状态文字
 */
export default function TopLoadingBar() {
  const isLoading = useAppStore((s) => s.isLoading)
  const isLoadingData = useAppStore((s) => s.isLoadingData)
  const active = isLoading || isLoadingData

  return (
    <div
      className={cn(
        'absolute top-[72px] left-1/2 -translate-x-1/2 z-10',
        'flex items-center gap-1.5 font-mono text-[11px] text-muted-foreground',
        'transition-all duration-300 ease-out',
        active
          ? 'opacity-100 translate-y-0'
          : 'opacity-0 -translate-y-2 pointer-events-none'
      )}
    >
      <div className="w-6 h-6 rounded-full border-2 border-muted-foreground/30 border-t-primary animate-spin" />
    </div>
  )
}
