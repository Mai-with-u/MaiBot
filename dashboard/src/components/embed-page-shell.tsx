import type { ReactNode } from 'react'
import { useEffect } from 'react'

import { BackgroundLayer } from '@/components/background-layer'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useBackground } from '@/hooks/use-background'
import { useAuthGuard } from '@/hooks/use-auth'

interface EmbedPageShellProps {
  children: ReactNode
  shellId: string
  title: string
}

/**
 * 给外部程序嵌入使用的页面外壳：不挂载 dashboard 顶栏和侧边栏。
 */
export function EmbedPageShell({ children, shellId, title }: EmbedPageShellProps) {
  const { checking } = useAuthGuard()
  const { config: pageBg } = useBackground('page')

  useEffect(() => {
    document.title = title
  }, [title])

  if (checking) {
    return (
      <div className="bg-background flex h-screen items-center justify-center">
        <div className="text-muted-foreground">正在加载...</div>
      </div>
    )
  }

  return (
    <TooltipProvider delayDuration={300}>
      <div data-dashboard-shell={shellId} className="relative isolate h-screen overflow-hidden">
        <BackgroundLayer config={pageBg} layerId="page" />
        <main
          id="main-content"
          data-dashboard-main="true"
          tabIndex={-1}
          className="relative z-10 h-full min-h-0 outline-none"
        >
          {children}
        </main>
      </div>
    </TooltipProvider>
  )
}
