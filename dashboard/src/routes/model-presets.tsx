import { LayoutGrid, Package } from 'lucide-react'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export function ModelPresetsPage() {
  return (
    <div className="h-[calc(100vh-4rem)] flex flex-col p-4 sm:p-6">
      {/* 页面标题 */}
      <div className="mb-4 sm:mb-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <h1 className="text-2xl sm:text-3xl font-bold flex items-center gap-2">
            <LayoutGrid className="h-8 w-8" strokeWidth={2} />
            模型分配预设市场
          </h1>
        </div>
      </div>

      <ScrollArea className="flex-1">
        <div className="flex items-center justify-center h-[calc(100vh-12rem)]">
          <Card className="max-w-lg w-full border-dashed">
            <CardHeader className="text-center">
              <div className="flex justify-center mb-4">
                <Package className="h-12 w-12 text-muted-foreground" />
              </div>
              <CardTitle className="text-xl">功能开发中</CardTitle>
              <CardDescription className="text-sm">
                模型分配预设市场尚未开放，当前可在模型配置页面管理各任务的模型分配。
              </CardDescription>
            </CardHeader>
          </Card>
        </div>
      </ScrollArea>
    </div>
  )
}

