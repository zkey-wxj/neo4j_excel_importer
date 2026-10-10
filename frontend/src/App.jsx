import { useEffect, useRef, useState, useCallback } from 'react'
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
import NodeContextMenu from '@/components/NodeContextMenu'
import AddRelatedNodeDialog from '@/components/AddRelatedNodeDialog'
import FullscreenLoading from '@/components/FullscreenLoading'
import TopLoadingBar from '@/components/TopLoadingBar'
import Minimap from '@/components/Minimap'
import PasswordGate from '@/components/PasswordGate'

export default function App() {
  const canvasRef = useRef(null)

  /**
   * canvas 元素状态 + 回调 ref。
   * 鉴权未通过时 App 渲染的是密码门，canvas 要等 authState 变为 ready 之后才挂载；
   * 把元素本身作为状态传给 useGraphCanvas，事件绑定的 effect 才能在元素真正挂载后执行
   * （否则监听器会被绑到一个 null 上，表现为悬浮/点击/拖动/缩放全部失效）。
   */
  const [canvasEl, setCanvasEl] = useState(null)
  const bindCanvas = useCallback((el) => {
    canvasRef.current = el
    setCanvasEl(el)
  }, [])
  const graphCanvas = useGraphCanvas(canvasRef, canvasEl)
  const isLoadingData = useAppStore((s) => s.isLoadingData)

  const authState = useAppStore((s) => s.authState)

  // 启动访问鉴权：无需密码或已持有有效令牌则直接加载图谱，否则停在密码门
  // 具体的数据初始化在 store.initFromUrl 中，鉴权通过后才执行
  useEffect(() => {
    useAppStore.getState().bootstrapAuth()
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


  // 未通过访问校验时，只渲染密码门
  if (authState !== 'ready') {
    return <PasswordGate />
  }

  return (
    <TooltipProvider delay={300}>
      <div className="relative w-full h-dvh overflow-hidden graph-bg">
        {/* 全屏加载遮罩：操作性 loading（CRUD、导入等），背景透明 */}
        <FullscreenLoading />

        {/* 顶部加载条：图谱数据加载时显示 */}
        <TopLoadingBar />

        {/* 顶部统计栏：显示节点数、关系数、孤立节点数等图谱统计信息 */}
        <StatsBar graphCanvas={graphCanvas} />

        {/* 图谱画布：核心 Canvas 元素，由 D3 力导向仿真驱动渲染 */}
        <canvas
          ref={bindCanvas}
          className="absolute inset-0 w-full h-full block z-[1]"
        />

        {/* 数据加载遮罩：分页加载期间覆盖画布，完全禁用悬浮/点击/拖动/缩放等交互，
            避免加载过程中产生误操作；加载进度由顶部 TopLoadingBar 呈现 */}
        {isLoadingData && <div className="absolute inset-0 z-[15] cursor-wait" />}

        {/* 底部控制栏：缩放、重置视图、小地图开关、导入/导出功能 */}
        <ControlBar graphCanvas={graphCanvas} />

        {/* 搜索面板：支持按名称/nid/标签搜索，点击结果定位并选中图谱节点 */}
        <SearchPanel graphCanvas={graphCanvas} />

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

        {/* 节点右键菜单 */}
        <NodeContextMenu />

        {/* 新增关联节点对话框 */}
        <AddRelatedNodeDialog />

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
