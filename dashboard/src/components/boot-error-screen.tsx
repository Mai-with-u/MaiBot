import { AlertCircle, HelpCircle, Moon, RefreshCw, Sun } from 'lucide-react'
import { useLayoutEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  applyMillenniumFonts,
  loadMillenniumCjkFont,
  loadMillenniumFont,
} from '@/lib/theme/millennium-font'
import { applyThemePipeline } from '@/lib/theme/pipeline'
import { loadThemeConfig } from '@/lib/theme/storage'
import { APP_FULL_NAME } from '@/lib/version'

const THEME_MODE_STORAGE_KEY = 'maibot-theme-mode'

/**
 * 启动时读不到 WebUI 运行模式的兜底页。
 *
 * 这时还没有挂载主题、路由和多语言，文案直接写中文；版式和登录页逐项对齐：
 * 居中卡片、右上主题键、标题下一行提示、红色提示框、整宽主键、小号帮助链接、底部版本号。
 */
export function BootErrorScreen({ message }: { message: string }) {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))
  const [helpOpen, setHelpOpen] = useState(false)

  // 主题提供者此时没有挂载：按本地保存的主题配置补上配色和像素字，和登录页看起来一致。
  useLayoutEffect(() => {
    applyMillenniumFonts(loadMillenniumFont(), loadMillenniumCjkFont())
    applyThemePipeline(loadThemeConfig(), dark)
  }, [dark])

  const toggleTheme = () => {
    const next = !dark
    document.documentElement.classList.toggle('dark', next)
    document.documentElement.classList.toggle('light', !next)
    try {
      localStorage.setItem(THEME_MODE_STORAGE_KEY, next ? 'dark' : 'light')
    } catch {
      // 存储不可用时只切换当前页面
    }
    setDark(next)
  }

  return (
    <div
      data-auth-page="true"
      data-boot-error="true"
      // 主题管线此时还没运行，不铺 bg-background，直接露出 body 上的机壳底纹
      className="relative isolate flex min-h-screen items-center justify-center overflow-hidden p-4"
    >
      <Card className="bg-card/80 border-border/50 relative z-10 w-full max-w-md shadow-2xl backdrop-blur-xl">
        <button
          type="button"
          data-auth-theme-toggle="true"
          onClick={toggleTheme}
          className="hover:bg-accent text-foreground absolute top-4 right-4 z-10 rounded-lg p-2 transition-colors"
          title={dark ? '切换到浅色模式' : '切换到深色模式'}
          aria-label={dark ? '切换到浅色模式' : '切换到深色模式'}
        >
          {dark ? (
            <Sun className="h-5 w-5" strokeWidth={2.5} fill="none" />
          ) : (
            <Moon className="h-5 w-5" strokeWidth={2.5} fill="none" />
          )}
        </button>

        <CardHeader className="pr-16 text-left">
          <div className="space-y-2">
            <CardTitle className="text-2xl font-bold">WebUI 暂时无法连接</CardTitle>
            <CardDescription className="flex items-center gap-2 text-base">
              <span aria-hidden="true" className="text-primary font-mono">
                &gt;
              </span>
              麦麦的后端没有正常响应
            </CardDescription>
          </div>
        </CardHeader>

        <CardContent className="space-y-4">
          <div
            role="alert"
            className="flex items-center gap-2 rounded-md bg-red-50 p-3 text-sm text-red-600 dark:bg-red-950/50 dark:text-red-400"
          >
            <AlertCircle className="h-4 w-4 flex-shrink-0" strokeWidth={2} fill="none" />
            <span className="break-words">{message}</span>
          </div>

          <Button type="button" className="w-full" onClick={() => window.location.reload()}>
            <RefreshCw className="h-4 w-4" />
            重新连接
          </Button>

          <button
            type="button"
            aria-expanded={helpOpen}
            onClick={() => setHelpOpen((open) => !open)}
            className="text-primary hover:text-primary/80 flex w-full items-center justify-start gap-1 text-left text-[11px] underline-offset-4 transition-colors hover:underline"
          >
            <HelpCircle className="h-3 w-3" strokeWidth={2} fill="none" />
            一直连不上？看看这几项
          </button>
          {helpOpen && (
            <ul className="text-muted-foreground list-disc space-y-1 pl-5 text-xs">
              <li>确认麦麦主程序已经启动，并且没有在启动过程中报错退出。</li>
              <li>确认浏览器打开的地址和端口与 WebUI 配置一致。</li>
              <li>刚更新过麦麦时，等待后端加载完成后再重新连接。</li>
            </ul>
          )}
        </CardContent>
      </Card>

      <div className="text-muted-foreground absolute right-0 bottom-4 left-0 z-10 text-center text-xs">
        <p>{APP_FULL_NAME}</p>
      </div>
    </div>
  )
}
