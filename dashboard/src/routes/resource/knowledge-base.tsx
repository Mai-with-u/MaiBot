import { Database } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export function KnowledgeBasePage() {
  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex-none border-b bg-card/50 px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">麦麦知识库管理</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              管理和组织麦麦的知识库内容
            </p>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto max-w-4xl">
          <Card>
            <CardHeader className="text-center">
              <div className="mx-auto mb-4 flex h-20 w-20 items-center justify-center rounded-full bg-primary/10">
                <Database className="h-10 w-10 text-primary" />
              </div>
              <CardTitle className="text-2xl">麦麦知识库管理</CardTitle>
              <CardDescription className="text-base">
                功能开发中，敬请期待
              </CardDescription>
            </CardHeader>
            <CardContent className="text-center text-sm text-muted-foreground">
              <p>此功能将提供知识库的创建、编辑、导入和管理能力</p>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
