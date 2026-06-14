import { Link, useRouterState } from '@tanstack/react-router'
import {
  BookOpen,
  Check,
  ChevronLeft,
  FileText,
  Globe,
  LogOut,
  Menu,
  MessageSquare,
  Moon,
  MoreHorizontal,
  Search,
  Server,
  Settings,
  SlidersHorizontal,
  Sun,
} from 'lucide-react'
import { LayoutGroup, motion } from 'motion/react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { BackgroundLayer } from '@/components/background-layer'
import { BackendManager } from '@/components/electron/BackendManager'
import { SearchDialog } from '@/components/search-dialog'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { toggleThemeWithTransition } from '@/components/use-theme'
import { useBackground } from '@/hooks/use-background'
import { logout } from '@/lib/auth'
import { isElectron } from '@/lib/runtime'
import { cn } from '@/lib/utils'
import type { WorkspaceMode } from './types'

const LANGUAGE_CODES = ['zh', 'en', 'ja', 'ko'] as const
const LANGUAGE_NAMES: Record<(typeof LANGUAGE_CODES)[number], string> = {
  zh: '中文',
  en: 'English',
  ja: '日本語',
  ko: '한국어',
}

interface HeaderProps {
  sidebarOpen: boolean
  mobileMenuOpen: boolean
  searchOpen: boolean
  actualTheme: 'light' | 'dark'
  onSidebarToggle: () => void
  onMobileMenuToggle: () => void
  onSearchOpenChange: (open: boolean) => void
  onThemeChange: (theme: 'light' | 'dark' | 'system') => void
  onTopbarToggle: () => void
  topbarCollapsed: boolean
  workspaceMode: WorkspaceMode
}

export function Header({
  sidebarOpen,
  mobileMenuOpen,
  searchOpen,
  actualTheme,
  onSidebarToggle,
  onMobileMenuToggle,
  onSearchOpenChange,
  onThemeChange,
  onTopbarToggle,
  topbarCollapsed,
  workspaceMode,
}: HeaderProps) {
  const { t, i18n: i18nInstance } = useTranslation()
  const currentLang = i18nInstance.language || 'zh'
  const { config: headerBg, inheritedFrom } = useBackground('header')
  const inheritsPageBackground = inheritedFrom === 'page'
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  const [backendManagerOpen, setBackendManagerOpen] = useState(false)
  const [activeBackendName, setActiveBackendName] = useState<string>('')

  useEffect(() => {
    if (!isElectron()) return
    window.electronAPI!.getActiveBackend().then((b) => {
      setActiveBackendName(b?.name ?? t('header.notConnected'))
    })
  }, [t])

  const handleLogout = async () => {
    await logout()
  }

  if (topbarCollapsed) {
    return (
      <motion.header
        data-dashboard-header="true"
        data-dashboard-header-collapsed="true"
        initial={{ height: 48 }}
        animate={{ height: 16 }}
        transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
        className={cn(
          'sticky top-0 isolate z-10 flex h-4 min-w-0 items-center justify-end border-b px-3 backdrop-blur-md sm:px-15',
          inheritsPageBackground ? 'bg-transparent' : 'bg-card/80'
        )}
      >
        {!inheritsPageBackground && <BackgroundLayer config={headerBg} layerId="header" />}
        <button
          type="button"
          data-dashboard-topbar-toggle="true"
          onClick={onTopbarToggle}
          aria-label={t('header.expandTopbar')}
          aria-expanded={!topbarCollapsed}
          title={t('header.expandTopbar')}
          className="bg-foreground/70 relative z-10 h-2 w-24 transition-shadow"
        />
      </motion.header>
    )
  }

  return (
    <motion.header
      data-dashboard-header="true"
      initial={{ height: 16 }}
      animate={{ height: 48 }}
      transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
      className={cn(
        'sticky top-0 isolate z-10 flex h-12 min-w-0 flex-col border-b px-3 backdrop-blur-md sm:px-4',
        inheritsPageBackground ? 'bg-transparent' : 'bg-card/80'
      )}
    >
      {!inheritsPageBackground && <BackgroundLayer config={headerBg} layerId="header" />}
      <div className="relative z-10 flex h-full min-h-0 items-center justify-between gap-2">
        <div className="flex min-w-0 shrink-0 items-center gap-2 sm:gap-4">
          {/* 移动端菜单按钮 */}
          <button
            onClick={onMobileMenuToggle}
            aria-label={t('a11y.closeMenu')}
            aria-expanded={mobileMenuOpen}
            className={cn(
              'hover:bg-accent rounded-lg p-2 lg:hidden',
              workspaceMode !== 'settings' && 'hidden'
            )}
          >
            <Menu className="h-5 w-5" />
          </button>

          {/* 桌面端侧边栏收起/展开按钮 */}
          <button
            onClick={onSidebarToggle}
            aria-label={sidebarOpen ? t('header.collapseSidebar') : t('header.expandSidebar')}
            aria-expanded={sidebarOpen}
            className={cn(
              'hover:bg-accent hidden rounded-lg p-2 lg:block',
              workspaceMode !== 'settings' && 'lg:hidden'
            )}
          >
            <ChevronLeft
              className={cn('h-5 w-5 transition-transform', !sidebarOpen && 'rotate-180')}
            />
          </button>
        </div>

        <div className="flex min-w-0 flex-1 items-center justify-end gap-1 sm:gap-2">
          {/* 工作区切换：复用 Tabs 组件 + Motion 动画指示器 */}
          <LayoutGroup id="workspace-switcher">
            <Tabs value={workspaceMode} aria-label={t('workspace.switcherLabel')}>
              <TabsList
                data-dashboard-workspace-tabs="true"
                className="relative h-9 gap-0.5 border bg-transparent p-1 shadow-sm"
              >
                <TabsTrigger
                  asChild
                  value="settings"
                  className="data-[state=active]:text-primary-foreground relative h-7 gap-1.5 bg-transparent px-2.5 text-sm font-medium data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                >
                  <Link to="/">
                    {workspaceMode === 'settings' && (
                      <motion.span
                        layoutId="workspace-tab-pill"
                        className="bg-primary absolute inset-0 -z-10 rounded-md shadow-sm"
                        transition={{ type: 'spring', stiffness: 480, damping: 38, mass: 0.6 }}
                      />
                    )}
                    <SlidersHorizontal className="h-3.5 w-3.5" />
                    <span className="hidden font-sans text-base font-semibold tracking-wider uppercase sm:inline">
                      {t('workspace.settings')}
                    </span>
                  </Link>
                </TabsTrigger>
                <TabsTrigger
                  asChild
                  value="chat"
                  className="data-[state=active]:text-primary-foreground relative h-7 gap-1.5 bg-transparent px-2.5 text-sm font-medium data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                >
                  <Link to="/chat">
                    {workspaceMode === 'chat' && (
                      <motion.span
                        layoutId="workspace-tab-pill"
                        className="bg-primary absolute inset-0 -z-10 rounded-md shadow-sm"
                        transition={{ type: 'spring', stiffness: 480, damping: 38, mass: 0.6 }}
                      />
                    )}
                    <MessageSquare className="h-3.5 w-3.5" />
                    <span className="hidden font-sans text-base font-semibold tracking-wider uppercase sm:inline">
                      {t('workspace.chat')}
                    </span>
                  </Link>
                </TabsTrigger>
                <TabsTrigger
                  asChild
                  value="logs"
                  className="data-[state=active]:text-primary-foreground relative h-7 gap-1.5 bg-transparent px-2.5 text-sm font-medium data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                >
                  <Link to="/logs">
                    {workspaceMode === 'logs' && (
                      <motion.span
                        layoutId="workspace-tab-pill"
                        className="bg-primary absolute inset-0 -z-10 rounded-md shadow-sm"
                        transition={{ type: 'spring', stiffness: 480, damping: 38, mass: 0.6 }}
                      />
                    )}
                    <FileText className="h-3.5 w-3.5" />
                    <span className="hidden font-sans text-base font-semibold tracking-wider uppercase sm:inline">
                      {t('workspace.logs')}
                    </span>
                  </Link>
                </TabsTrigger>
              </TabsList>
            </Tabs>
          </LayoutGroup>

          <div className="bg-border hidden h-6 w-px sm:block" />
          <Button
            asChild
            variant="ghost"
            size="icon"
            className={cn(pathname === '/settings' && 'bg-accent text-accent-foreground')}
            title={t('sidebar.menu.settings')}
            aria-label={t('sidebar.menu.settings')}
          >
            <Link to="/settings">
              <Settings className="h-4 w-4" />
            </Link>
          </Button>
          {/* 后端切换按钮（仅 Electron） */}
          {isElectron() && (
            <>
              <Button
                variant="ghost"
                size="sm"
                className="gap-2"
                onClick={() => setBackendManagerOpen(true)}
                title={t('header.toggleConnection')}
              >
                <Server className="h-4 w-4" />
                <span className="text-muted-foreground hidden max-w-25 truncate text-xs sm:inline">
                  {activeBackendName}
                </span>
              </Button>
              <BackendManager open={backendManagerOpen} onOpenChange={setBackendManagerOpen} />
              <div className="bg-border h-6 w-px" />
            </>
          )}
          {/* 搜索框 */}
          <Button
            variant="ghost"
            size="icon"
            onClick={() => onSearchOpenChange(true)}
            aria-label={t('header.searchPlaceholder')}
            title={t('header.searchPlaceholder')}
            className="hidden md:inline-flex"
          >
            <Search className="h-4 w-4" aria-hidden="true" />
          </Button>

          {/* 搜索对话框 */}
          <SearchDialog open={searchOpen} onOpenChange={onSearchOpenChange} />

          {/* 麦麦文档链接 */}
          <Button
            variant="ghost"
            size="icon"
            onClick={() => window.open('https://docs.mai-mai.org', '_blank')}
            className="hidden sm:inline-flex"
            title={t('header.viewDocs')}
            aria-label={t('header.viewDocs')}
          >
            <BookOpen className="h-4 w-4" />
          </Button>

          {/* 语言切换 */}
          <div className="hidden sm:block">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  title={t('header.switchLanguage')}
                  aria-label={t('header.switchLanguage')}
                >
                  <Globe className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {LANGUAGE_CODES.map((code) => (
                  <DropdownMenuItem
                    key={code}
                    onClick={() => i18nInstance.changeLanguage(code)}
                    className={cn(
                      'cursor-pointer',
                      currentLang.split('-')[0] === code && 'text-primary font-semibold'
                    )}
                  >
                    {currentLang.split('-')[0] === code && <Check className="mr-2 h-3 w-3" />}
                    {LANGUAGE_NAMES[code]}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>

          {/* 主题切换按钮 */}
          <button
            onClick={(e) => {
              const newTheme = actualTheme === 'dark' ? 'light' : 'dark'
              toggleThemeWithTransition(newTheme, onThemeChange, e)
            }}
            aria-label={
              actualTheme === 'dark' ? t('header.switchToLight') : t('header.switchToDark')
            }
            className="hover:bg-accent hidden rounded-lg p-2 sm:inline-flex"
          >
            {actualTheme === 'dark' ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
          </button>

          {/* 分隔线 */}
          <div className="bg-border hidden h-6 w-px sm:block" />

          {/* 登出按钮 */}
          <Button
            variant="ghost"
            size="icon"
            onClick={handleLogout}
            title={t('header.logout')}
            aria-label={t('header.logout')}
            className="hidden sm:inline-flex"
          >
            <LogOut className="h-4 w-4" />
          </Button>

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="sm:hidden"
                title={t('header.moreActions')}
                aria-label={t('header.moreActions')}
              >
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-48">
              <DropdownMenuItem
                onClick={(event) => {
                  const newTheme = actualTheme === 'dark' ? 'light' : 'dark'
                  toggleThemeWithTransition(newTheme, onThemeChange, event)
                }}
                className="cursor-pointer gap-2"
              >
                {actualTheme === 'dark' ? (
                  <Sun className="h-4 w-4" />
                ) : (
                  <Moon className="h-4 w-4" />
                )}
                {actualTheme === 'dark' ? t('header.switchToLight') : t('header.switchToDark')}
              </DropdownMenuItem>
              <DropdownMenuSub>
                <DropdownMenuSubTrigger className="cursor-pointer gap-2">
                  <Globe className="h-4 w-4" />
                  {t('header.switchLanguage')}
                </DropdownMenuSubTrigger>
                <DropdownMenuSubContent alignOffset={-4}>
                  {LANGUAGE_CODES.map((code) => (
                    <DropdownMenuItem
                      key={code}
                      onClick={() => i18nInstance.changeLanguage(code)}
                      className={cn(
                        'cursor-pointer',
                        currentLang.split('-')[0] === code && 'text-primary font-semibold'
                      )}
                    >
                      {currentLang.split('-')[0] === code && <Check className="mr-2 h-3 w-3" />}
                      {LANGUAGE_NAMES[code]}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuSubContent>
              </DropdownMenuSub>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={handleLogout} className="cursor-pointer gap-2">
                <LogOut className="h-4 w-4" />
                {t('header.logoutLabel')}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <div className="absolute right-5 bottom-[-7px] z-10 flex h-0 shrink-0 items-center justify-end sm:right-15.5">
        <button
          type="button"
          data-dashboard-topbar-toggle="true"
          onClick={onTopbarToggle}
          aria-label={t('header.collapseTopbar')}
          aria-expanded={!topbarCollapsed}
          title={t('header.collapseTopbar')}
          className="bg-foreground/70 flex h-3 w-24 shrink-0 items-center justify-center transition-shadow"
        >
          <span className="sr-only">{t('header.collapseTopbar')}</span>
        </button>
      </div>
    </motion.header>
  )
}
