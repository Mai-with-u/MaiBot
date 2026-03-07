import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useRouter } from '@tanstack/react-router'

import { BackgroundLayer } from '@/components/background-layer'
import { BackToTop } from '@/components/back-to-top'
import { HttpWarningBanner } from '@/components/http-warning-banner'
import { SkipNav } from '@/components/ui/skip-nav'
import { useAnnounce } from '@/components/ui/announcer'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useTheme } from '@/components/use-theme'
import { useAuthGuard } from '@/hooks/use-auth'
import { useBackground } from '@/hooks/use-background'

import { TitleBar } from '@/components/electron/TitleBar'
import { isElectron } from '@/lib/runtime'
import { cn } from '@/lib/utils'
import { menuSections } from './constants'
import { Header } from './Header'
import { Sidebar } from './Sidebar'
import type { LayoutProps } from './types'

export function Layout({ children }: LayoutProps) {
  const { t } = useTranslation()
  const { checking } = useAuthGuard() // 检查认证状态
  const router = useRouter()
  const announce = useAnnounce()
  
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [tooltipsEnabled, setTooltipsEnabled] = useState(false) // 控制 tooltip 启用状态
  const { theme, setTheme } = useTheme()

  // 侧边栏状态变化时，延迟启用/禁用 tooltip
  useEffect(() => {
    if (sidebarOpen) {
      // 侧边栏展开时，立即禁用 tooltip
      setTooltipsEnabled(false)
    } else {
      // 侧边栏收起时，等待动画完成后再启用 tooltip
      const timer = setTimeout(() => {
        setTooltipsEnabled(true)
      }, 350) // 稍大于 CSS transition duration (300ms)
      return () => clearTimeout(timer)
    }
  }, [sidebarOpen])

  // 搜索快捷键监听（Cmd/Ctrl + K）
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setSearchOpen(true)
      }
    }
    
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])
  // 路由变更：焦点管理 + 屏幕阅读器播报 + document.title 更新
  useEffect(() => {
    // 构建 路径 -> 页面标题 的映射表（以当前语言 t() 翻译）
    const pathToLabel: Record<string, string> = {}
    for (const section of menuSections) {
      for (const item of section.items) {
        pathToLabel[item.path] = t(item.label)
      }
    }

    const unsubscribe = router.subscribe('onResolved', () => {
      const pathname = router.state.location.pathname
      const pageTitle = pathToLabel[pathname] ?? 'MaiBot Dashboard'
      const fullTitle = pageTitle === 'MaiBot Dashboard'
        ? 'MaiBot Dashboard'
        : `${pageTitle} — MaiBot Dashboard`

      // 更新 document.title
      document.title = fullTitle

      // 屏幕阅读器朗读导航结果
      announce(t('a11y.navigatedTo', { page: pageTitle }), 'polite')

      // 将焦点移到主内容区（仅当焦点不在其内部时）
      const mainEl = document.getElementById('main-content')
      if (mainEl && !mainEl.contains(document.activeElement)) {
        // requestAnimationFrame 确保 DOM 已渲染完成
        requestAnimationFrame(() => {
          mainEl.focus({ preventScroll: true })
        })
      }
    })

    return unsubscribe
  }, [router, announce, t])

  // 获取实际应用的主题（处理 system 情况）
  const getActualTheme = () => {
    if (theme === 'system') {
      return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
    }
    return theme
  }

  const actualTheme = getActualTheme()
  const pageBg = useBackground('page')

  // 认证检查中，显示加载状态
  if (checking) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <div className="text-muted-foreground">{t('layout.verifyingLogin')}</div>
      </div>
    )
  }

  return (
      <TooltipProvider delayDuration={300}>
        <SkipNav />
        {isElectron() && <TitleBar />}
      <div className={cn('flex h-screen overflow-hidden', isElectron() && 'pt-8')}>
      {/* Sidebar */}
      <Sidebar
        sidebarOpen={sidebarOpen}
        mobileMenuOpen={mobileMenuOpen}
        tooltipsEnabled={tooltipsEnabled}
        onMobileMenuClose={() => setMobileMenuOpen(false)}
      />

      {/* Mobile overlay */}
      {mobileMenuOpen && (
        <div
          aria-hidden="true"
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          onClick={() => setMobileMenuOpen(false)}
        />

      )}
      {/* Main content */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* HTTP 安全警告横幅 */}
        <HttpWarningBanner />
        
        {/* Topbar */}
        <Header
          sidebarOpen={sidebarOpen}
          mobileMenuOpen={mobileMenuOpen}
          searchOpen={searchOpen}
          actualTheme={actualTheme}
          onSidebarToggle={() => setSidebarOpen(!sidebarOpen)}
          onMobileMenuToggle={() => setMobileMenuOpen(!mobileMenuOpen)}
          onSearchOpenChange={setSearchOpen}
          onThemeChange={setTheme}
        />

        {/* Page content */}
        <main
          id="main-content"
          tabIndex={-1}
          className="relative flex-1 overflow-hidden bg-background outline-none"
        >
          <BackgroundLayer config={pageBg} layerId="page" />
          {children}
        </main>

        {/* Back to Top Button */}
        <BackToTop />
      </div>
      </div>
    </TooltipProvider>
  )
}
