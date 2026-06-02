import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'

/**
 * 全屏加载遮罩组件
 * - 加载图谱：半透明背景
 * - 操作性 loading：全透明背景
 */
export default function FullscreenLoading() {
  const isFullscreenLoading = useAppStore((s) => s.isFullscreenLoading)
  const loadingText = useAppStore((s) => s.loadingText)
  const loadingBackdrop = useAppStore((s) => s.loadingBackdrop)

  return (
    <div
      className={cn(
        'fixed inset-0 z-[200] flex flex-col items-center justify-center gap-4',
        'transition-opacity duration-200',
        loadingBackdrop === 'semi' ? 'bg-background/60 backdrop-blur-xs' : 'bg-transparent',
        isFullscreenLoading
          ? 'opacity-100 pointer-events-auto'
          : 'opacity-0 pointer-events-none'
      )}
    >
      <div className="w-9 h-9 rounded-full border-[3px] border-primary/30 border-t-primary animate-spin" />
      {loadingText && (
        <span className="font-mono text-sm text-foreground/80">
          {loadingText}
        </span>
      )}
    </div>
  )
}
