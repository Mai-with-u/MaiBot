import { LayoutGrid, Package } from 'lucide-react'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export function ModelPresetsPage() {
  return (
    <div className="h-[calc(100vh-4rem)] flex flex-col p-4 sm:p-6">
      {/* 页面标题 */}
      <div className="mb-4 sm:mb-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold flex items-center gap-2">
              <LayoutGrid className="h-8 w-8" strokeWidth={2} />
              模型分配预设市场
            </h1>
            <p className="text-muted-foreground mt-1 text-sm sm:text-base">
              浏览和下载社区共享的模型分配预设配置
            </p>
          </div>
        </div>
      </div>

      <ScrollArea className="flex-1">
        <div className="flex items-center justify-center h-[calc(100vh-12rem)]">
          <Card className="max-w-2xl w-full border-dashed">
            <CardHeader className="text-center">
              <div className="flex justify-center mb-4">
                <Package className="h-16 w-16 text-muted-foreground" />
              </div>
              <CardTitle className="text-2xl">功能开发中</CardTitle>
              <CardDescription className="text-base">
                模型分配预设市场功能正在开发中，敬请期待！
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-3 text-sm text-muted-foreground">
                <p className="font-medium text-foreground">📦 即将推出的功能：</p>
                <ul className="space-y-2 ml-6">
                  <li className="flex items-start">
                    <span className="mr-2">•</span>
                    <span>浏览社区共享的模型分配预设配置</span>
                  </li>
                  <li className="flex items-start">
                    <span className="mr-2">•</span>
                    <span>一键下载和应用预设配置</span>
                  </li>
                  <li className="flex items-start">
                    <span className="mr-2">•</span>
                    <span>预设配置评分和评论系统</span>
                  </li>
                  <li className="flex items-start">
                    <span className="mr-2">•</span>
                    <span>根据使用场景智能推荐配置</span>
                  </li>
                </ul>
              </div>
            </CardContent>
          </Card>
        </div>
      </ScrollArea>
    </div>
  )
}

