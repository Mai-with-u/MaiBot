import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { DataTransferWorkspace } from '@/routes/data-transfer/DataTransferWorkspace'
import { useDataTransfer } from '@/routes/data-transfer/useDataTransfer'
import { LocalCacheTab } from '@/routes/settings/LocalCacheTab'

export function DataTransferPage() {
  const viewModel = useDataTransfer()
  return (
    // 手机端让导入导出与本地缓存内容自然展开，交给工作区统一滚动。
    <ScrollArea
      className="h-full max-lg:h-auto max-lg:overflow-visible max-lg:[&>[data-dashboard-scrollbar]]:hidden"
      viewportClassName="max-lg:h-auto max-lg:!overflow-visible max-lg:overscroll-auto"
      scrollbars="vertical"
    >
      <div className="mx-auto w-full max-w-6xl space-y-6 p-4 sm:p-6">
        <DataTransferWorkspace viewModel={viewModel} />
        <Separator variant="groove" />
        <details id="local-cache" className="group">
          <summary className="cursor-pointer py-2 text-sm font-medium">本地缓存管理</summary>
          <div className="pt-4">
            <LocalCacheTab />
          </div>
        </details>
      </div>
    </ScrollArea>
  )
}
