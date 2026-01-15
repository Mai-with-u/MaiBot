import { useNavigate } from '@tanstack/react-router'
import { Home, Search, ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'

export function NotFoundPage() {
  const navigate = useNavigate()

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <div className="w-full max-w-2xl text-center">
        {/* 404 大标题 */}
        <div className="relative mb-8">
          <h1 className="text-[150px] font-black leading-none text-primary/10 select-none sm:text-[200px]">
            404
          </h1>
          <div className="absolute inset-0 flex items-center justify-center">
            <Search className="h-20 w-20 text-primary/30 sm:h-24 sm:w-24" />
          </div>
        </div>

        {/* 错误信息 */}
        <div className="space-y-4 mb-8">
          <h2 className="text-2xl font-bold text-foreground sm:text-3xl">
            页面未找到
          </h2>
          <p className="text-base text-muted-foreground sm:text-lg max-w-md mx-auto">
            抱歉，您访问的页面不存在或已被移除。请检查 URL 是否正确，或返回首页继续浏览。
          </p>
        </div>

        {/* 操作按钮 */}
        <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
          <Button
            size="lg"
            onClick={() => navigate({ to: '/' })}
            className="gap-2 w-full sm:w-auto"
          >
            <Home className="h-4 w-4" />
            返回首页
          </Button>
          <Button
            size="lg"
            variant="outline"
            onClick={() => window.history.back()}
            className="gap-2 w-full sm:w-auto"
          >
            <ArrowLeft className="h-4 w-4" />
            返回上一页
          </Button>
        </div>

        {/* 提示信息 */}
        <div className="mt-12 pt-8 border-t border-border">
          <p className="text-sm text-muted-foreground">
            如果您认为这是一个错误，请联系系统管理员
          </p>
        </div>
      </div>
    </div>
  )
}
