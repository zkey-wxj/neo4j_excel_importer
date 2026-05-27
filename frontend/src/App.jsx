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
import ConfirmDialog from '@/components/ConfirmDialog'
import EdgeCreatePopover from '@/components/EdgeCreatePopover'
import FullscreenLoading from '@/components/FullscreenLoading'
import TopLoadingBar from '@/components/TopLoadingBar'
import Minimap from '@/components/Minimap'

export default function App() {
  const canvasRef = useRef(null)
  const graphCanvas = useGraphCanvas(canvasRef)

  // 从 URL 参数初始化加载图谱数据（仅在组件挂载时执行一次）
  // 支持两种模式：demo=true 加载示例数据，或通过 group_id 加载指定分组
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const gid = params.get('group_id') || ''
    const demo = params.get('demo') === 'true'
    const store = useAppStore.getState()
    if (gid) store.setGroupId(gid)
    if (demo) {
      // 示例模式：生成模拟数据用于演示
      store.loadDemo().finally(() => store.setFullscreenLoading(false))
    } else if (gid) {
      // 正式模式：从后端 API 加载指定分组的图谱数据
      store.loadGroup(gid).finally(() => store.setFullscreenLoading(false))
    } else {
      // 未指定参数：提示用户输入 group_id
      store.setFullscreenLoading(false)
      store.setStatus('请输入 group_id 后加载图谱')
    }
  }, [])

  // 键盘事件监听：按 ESC 键退出「抓取节点」模式
  // 抓取模式用于通过点击图谱节点来自动填入节点 ID 到表单字段
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
        {/* 全屏加载遮罩层：图谱数据加载期间显示旋转动画 */}
        <FullscreenLoading />

        {/* 顶部加载条：CRUD/导入操作时从顶部弹出 */}
        <TopLoadingBar />

        {/* 顶部统计栏：显示节点数、关系数、孤立节点数等图谱统计信息 */}
        <StatsBar graphCanvas={graphCanvas} />

        {/* 图谱画布：核心 Canvas 元素，由 D3 力导向仿真驱动渲染 */}
        <canvas
          ref={canvasRef}
          className="absolute inset-0 w-full h-full block z-[1]"
        />

        {/* 底部控制栏：缩放、重置视图、小地图开关、导入/导出功能 */}
        <ControlBar graphCanvas={graphCanvas} />

        {/* 搜索面板：支持按名称、nid、标签搜索过滤节点 */}
        <SearchPanel />

        {/* 操作面板：图谱加载、节点/关系 CRUD、路径查询、关系替换 */}
        <OpsPanel />

        {/* 详情面板：展示选中节点或关系的属性、邻居等详细信息 */}
        <DetailPanel />

        {/* 图例面板：按节点类型/关系类型筛选，支持颜色图例展示 */}
        <LegendPanel />

        {/* 小地图：缩略显示全图布局，高亮当前视口位置 */}
        <Minimap graphCanvas={graphCanvas} />

        {/* 导入弹窗：支持 Excel/JSON 文件导入，可选合并或覆盖模式 */}
        <ImportModal />

        {/* 确认对话框：通用确认弹窗，用于危险操作前的二次确认 */}
        <ConfirmDialog />

        {/* 建边浮窗：拖拽建边完成后弹出，输入关系类型并创建 */}
        <EdgeCreatePopover />

        {/* 消息通知：顶部居中显示操作结果的 Toast 提示 */}
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
