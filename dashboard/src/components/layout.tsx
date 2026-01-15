import { Menu, Moon, Sun, ChevronLeft, Home, Settings, LogOut, FileText, Server, Boxes, Smile, MessageSquare, UserCircle, FileSearch, Package, BookOpen, Search, Sliders, Network, Hash, LayoutGrid, Database, Activity, PieChart } from 'lucide-react'
import { useState, useEffect } from 'react'
import { Link, useMatchRoute } from '@tanstack/react-router'
import { useTheme, toggleThemeWithTransition } from './use-theme'
import { useAuthGuard } from '@/hooks/use-auth'
import { logout } from '@/lib/fetch-with-auth'
import { Button } from '@/components/ui/button'
import { Kbd } from '@/components/ui/kbd'
import { SearchDialog } from '@/components/search-dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import { HttpWarningBanner } from '@/components/http-warning-banner'
import { BackToTop } from '@/components/back-to-top'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import { formatVersion } from '@/lib/version'
import type { ReactNode, ComponentType } from 'react'
import type { LucideProps } from 'lucide-react'

interface LayoutProps {
  children: ReactNode
}

interface MenuItem {
  icon: ComponentType<LucideProps>
  label: string
  path: string
  tourId?: string
}

interface MenuSection {
  title: string
  items: MenuItem[]
}

export function Layout({ children }: LayoutProps) {
  const { checking } = useAuthGuard() // 检查认证状态
  
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [tooltipsEnabled, setTooltipsEnabled] = useState(false) // 控制 tooltip 启用状态
  const { theme, setTheme } = useTheme()
  const matchRoute = useMatchRoute()

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

  // 认证检查中，显示加载状态
  if (checking) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <div className="text-muted-foreground">正在验证登录状态...</div>
      </div>
    )
  }

  // 菜单项配置 - 分块结构
  const menuSections: MenuSection[] = [
    {
      title: '概览',
      items: [
        { icon: Home, label: '首页', path: '/' },
      ],
    },
    {
      title: '麦麦配置编辑',
      items: [
        { icon: FileText, label: '麦麦主程序配置', path: '/config/bot' },
        { icon: Server, label: 'AI模型厂商配置', path: '/config/modelProvider', tourId: 'sidebar-model-provider' },
        { icon: Boxes, label: '模型管理与分配', path: '/config/model', tourId: 'sidebar-model-management' },
        { icon: Sliders, label: '麦麦适配器配置', path: '/config/adapter' },
      ],
    },
    {
      title: '麦麦资源管理',
      items: [
        { icon: Smile, label: '表情包管理', path: '/resource/emoji' },
        { icon: MessageSquare, label: '表达方式管理', path: '/resource/expression' },
        { icon: Hash, label: '黑话管理', path: '/resource/jargon' },
        { icon: UserCircle, label: '人物信息管理', path: '/resource/person' },
        { icon: Network, label: '知识库图谱可视化', path: '/resource/knowledge-graph' },
        { icon: Database, label: '麦麦知识库管理', path: '/resource/knowledge-base' },
      ],
    },
    {
      title: '扩展与监控',
      items: [
        { icon: Package, label: '插件市场', path: '/plugins' },
        { icon: LayoutGrid, label: '配置模板市场', path: '/config/pack-market' },
        { icon: Sliders, label: '插件配置', path: '/plugin-config' },
        { icon: FileSearch, label: '日志查看器', path: '/logs' },
        { icon: Activity, label: '计划器&回复器监控', path: '/planner-monitor' },
        { icon: MessageSquare, label: '本地聊天室', path: '/chat' },
      ],
    },
    {
      title: '系统',
      items: [
        { icon: Settings, label: '系统设置', path: '/settings' },
      ],
    },
  ]

  // 获取实际应用的主题（处理 system 情况）
  const getActualTheme = () => {
    if (theme === 'system') {
      return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
    }
    return theme
  }

  const actualTheme = getActualTheme()

  // 登出处理
  const handleLogout = async () => {
    await logout()
  }

  return (
    <TooltipProvider delayDuration={300}>
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-50 flex flex-col border-r bg-card transition-all duration-300 lg:relative lg:z-0',
          // 移动端始终显示完整宽度，桌面端根据 sidebarOpen 切换
          'w-64 lg:w-auto',
          sidebarOpen ? 'lg:w-64' : 'lg:w-16',
          mobileMenuOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'
        )}
      >
        {/* Logo 区域 */}
        <div className="flex h-16 items-center border-b px-4">
          <div
            className={cn(
              'relative flex items-center justify-center flex-1 transition-all overflow-hidden',
              // 移动端始终完整显示,桌面端根据 sidebarOpen 切换
              'lg:flex-1',
              !sidebarOpen && 'lg:flex-none lg:w-8'
            )}
          >
            {/* 移动端始终显示完整 Logo，桌面端根据 sidebarOpen 切换 */}
            <div className={cn(
              "flex items-baseline gap-2",
              !sidebarOpen && "lg:hidden"
            )}>
              <span className="font-bold text-xl text-primary-gradient whitespace-nowrap">MaiBot WebUI</span>
              <span className="text-xs text-primary/60 whitespace-nowrap">
                {formatVersion()}
              </span>
            </div>
            {/* 折叠时的 Logo - 仅桌面端显示 */}
            {!sidebarOpen && (
              <span className="hidden lg:block font-bold text-primary-gradient text-2xl">M</span>
            )}
          </div>
        </div>

        <ScrollArea className={cn(
          "flex-1 overflow-x-hidden",
          !sidebarOpen && "lg:w-16"
        )}>
          <nav className={cn(
            "p-4",
            !sidebarOpen && "lg:p-2 lg:w-16"
          )}>
            <ul className={cn(
              // 移动端始终使用正常间距,桌面端根据 sidebarOpen 切换
              "space-y-6",
              !sidebarOpen && "lg:space-y-3 lg:w-full"
            )}>
            {menuSections.map((section, sectionIndex) => (
              <li key={section.title}>
                {/* 块标题 - 移动端始终可见，桌面端根据 sidebarOpen 切换 */}
                <div className={cn(
                  "px-3 h-[1.25rem]",
                  // 移动端始终显示，桌面端根据状态切换
                  "mb-2",
                  !sidebarOpen && "lg:mb-1 lg:invisible"
                )}>
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60 whitespace-nowrap">
                    {section.title}
                  </h3>
                </div>

                {/* 分割线 - 仅在桌面端折叠时显示 */}
                {!sidebarOpen && sectionIndex > 0 && (
                  <div className="hidden lg:block mb-2 border-t border-border" />
                )}

                {/* 菜单项列表 */}
                <ul className="space-y-1">
                  {section.items.map((item) => {
                    const isActive = matchRoute({ to: item.path })
                    const Icon = item.icon

                    const menuItemContent = (
                      <>
                        {/* 左侧高亮条 */}
                        {isActive && (
                          <div className="absolute left-0 top-1/2 h-8 w-1 -translate-y-1/2 rounded-r-full bg-primary transition-opacity duration-300" />
                        )}
                        <div className={cn(
                          'flex items-center transition-all duration-300',
                          sidebarOpen ? 'gap-3' : 'gap-3 lg:gap-0'
                        )}>
                          <Icon
                            className={cn(
                              'h-5 w-5 flex-shrink-0',
                              isActive && 'text-primary'
                            )}
                            strokeWidth={2}
                            fill="none"
                          />
                          <span className={cn(
                            'text-sm font-medium whitespace-nowrap transition-all duration-300',
                            isActive && 'font-semibold',
                            sidebarOpen 
                              ? 'opacity-100 max-w-[200px]' 
                              : 'opacity-100 max-w-[200px] lg:opacity-0 lg:max-w-0 lg:overflow-hidden'
                          )}>
                            {item.label}
                          </span>
                        </div>
                      </>
                    )

                    return (
                      <li key={item.path} className="relative">
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Link
                              to={item.path}
                              data-tour={item.tourId}
                              className={cn(
                                'relative flex items-center rounded-lg py-2 transition-all duration-300',
                                'hover:bg-accent hover:text-accent-foreground',
                                isActive
                                  ? 'bg-accent text-foreground'
                                  : 'text-muted-foreground hover:text-foreground',
                                sidebarOpen ? 'px-3' : 'px-3 lg:px-0 lg:justify-center lg:w-12 lg:mx-auto'
                              )}
                              onClick={() => setMobileMenuOpen(false)}
                            >
                              {menuItemContent}
                            </Link>
                          </TooltipTrigger>
                          {tooltipsEnabled && (
                            <TooltipContent side="right" className="hidden lg:block">
                              <p>{item.label}</p>
                            </TooltipContent>
                          )}
                        </Tooltip>
                      </li>
                    )
                  })}
                </ul>
              </li>
            ))}
          </ul>
          </nav>
        </ScrollArea>
      </aside>

      {/* Mobile overlay */}
      {mobileMenuOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          onClick={() => setMobileMenuOpen(false)}
        />
      )}

      {/* Main content */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* HTTP 安全警告横幅 */}
        <HttpWarningBanner />
        
        {/* Topbar */}
        <header className="flex h-16 items-center justify-between border-b bg-card/80 backdrop-blur-md px-4 sticky top-0 z-10">
          <div className="flex items-center gap-4">
            {/* 移动端菜单按钮 */}
            <button
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              className="rounded-lg p-2 hover:bg-accent lg:hidden"
            >
              <Menu className="h-5 w-5" />
            </button>
            
            {/* 桌面端侧边栏收起/展开按钮 */}
            <button
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="hidden rounded-lg p-2 hover:bg-accent lg:block"
              title={sidebarOpen ? '收起侧边栏' : '展开侧边栏'}
            >
              <ChevronLeft
                className={cn('h-5 w-5 transition-transform', !sidebarOpen && 'rotate-180')}
              />
            </button>
          </div>

          <div className="flex items-center gap-2">
            {/* 年度总结入口 */}
            <Link to="/annual-report">
              <Button
                variant="ghost"
                size="sm"
                className="gap-2 bg-gradient-to-r from-pink-500/10 to-purple-500/10 hover:from-pink-500/20 hover:to-purple-500/20 border border-pink-500/20"
                title="查看年度总结"
              >
                <PieChart className="h-4 w-4 text-pink-500" />
                <span className="hidden sm:inline bg-gradient-to-r from-pink-500 to-purple-500 bg-clip-text text-transparent font-medium">2025 年度总结</span>
              </Button>
            </Link>

            {/* 搜索框 */}
            <button
              onClick={() => setSearchOpen(true)}
              className="relative hidden md:flex items-center w-64 h-9 pl-9 pr-16 bg-background/50 border rounded-md hover:bg-accent/50 transition-colors text-left"
            >
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <span className="text-sm text-muted-foreground">搜索...</span>
              <Kbd size="sm" className="absolute right-2 top-1/2 -translate-y-1/2">
                <span className="text-xs">⌘</span>K
              </Kbd>
            </button>

            {/* 搜索对话框 */}
            <SearchDialog open={searchOpen} onOpenChange={setSearchOpen} />

            {/* 麦麦文档链接 */}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => window.open('https://docs.mai-mai.org', '_blank')}
              className="gap-2"
              title="查看麦麦文档"
            >
              <BookOpen className="h-4 w-4" />
              <span className="hidden sm:inline">麦麦文档</span>
            </Button>

            {/* 主题切换按钮 */}
            <button
              onClick={(e) => {
                const newTheme = actualTheme === 'dark' ? 'light' : 'dark'
                toggleThemeWithTransition(newTheme, setTheme, e)
              }}
              className="rounded-lg p-2 hover:bg-accent"
              title={actualTheme === 'dark' ? '切换到浅色模式' : '切换到深色模式'}
            >
              {actualTheme === 'dark' ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
            </button>

            {/* 分隔线 */}
            <div className="h-6 w-px bg-border" />

            {/* 登出按钮 */}
            <Button
              variant="ghost"
              size="sm"
              onClick={handleLogout}
              className="gap-2"
              title="登出系统"
            >
              <LogOut className="h-4 w-4" />
              <span className="hidden sm:inline">登出</span>
            </Button>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-hidden bg-background">{children}</main>

        {/* Back to Top Button */}
        <BackToTop />
      </div>
    </div>
    </TooltipProvider>
  )
}
