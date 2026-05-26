import { useEffect, useRef } from 'react'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Toaster } from 'sonner'
import { useAppStore } from '@/store'
import useGraphCanvas from '@/hooks/useGraphCanvas'
import StatsBar from '@/components/StatsBar'
import ControlBar from '@/components/ControlBar'
import SearchPanel from '@/components/SearchPanel'
import OpsPanel from '@/components/OpsPanel'
import DetailPanel from '@/components/DetailPanel'
import LegendPanel from '@/components/LegendPanel'
import ImportModal from '@/components/ImportModal'
import FullscreenLoading from '@/components/FullscreenLoading'
import Minimap from '@/components/Minimap'

export default function App() {
  const canvasRef = useRef(null)
  const graphCanvas = useGraphCanvas(canvasRef)

  // Initial load from URL params - runs once
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const gid = params.get('group_id') || ''
    const demo = params.get('demo') === 'true'
    const store = useAppStore.getState()

    if (gid) {
      store.setGroupId(gid)
      store.loadGroup(gid).finally(() => store.setFullscreenLoading(false))
    } else if (demo) {
      store.loadDemo().finally(() => store.setFullscreenLoading(false))
    } else {
      store.setFullscreenLoading(false)
      store.setStatus('请输入 group_id 后加载图谱')
    }
  }, [])

  // Keyboard: ESC exits pick mode
  useEffect(() => {
    const handler = (e) => {
      if (e.key === 'Escape') {
        const store = useAppStore.getState()
        if (store.pickTarget) {
          store.setPickTarget(null)
          store.setStatus('已退出抓取模式')
        }
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [])

  return (
    <TooltipProvider delay={300}>
      <div className="relative w-full h-dvh overflow-hidden graph-bg">
        {/* Fullscreen loading overlay */}
        <FullscreenLoading />

        {/* Stats bar */}
        <StatsBar graphCanvas={graphCanvas} />

        {/* Canvas */}
        <canvas
          ref={canvasRef}
          className="absolute inset-0 w-full h-full block z-[1]"
        />

        {/* Control bar */}
        <ControlBar graphCanvas={graphCanvas} />

        {/* Search panel */}
        <SearchPanel />

        {/* Operations panel */}
        <OpsPanel />

        {/* Detail panel */}
        <DetailPanel />

        {/* Legend panel */}
        <LegendPanel />

        {/* Minimap */}
        <Minimap graphCanvas={graphCanvas} />

        {/* Import modal */}
        <ImportModal />

        {/* Toast notifications */}
        <Toaster
          position="top-center"
          toastOptions={{
            className: 'font-mono text-xs',
            duration: 3000,
          }}
        />
      </div>
    </TooltipProvider>
  )
}
