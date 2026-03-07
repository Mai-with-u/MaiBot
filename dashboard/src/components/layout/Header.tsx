import { Link } from '@tanstack/react-router'
import { BookOpen, ChevronLeft, Globe, LogOut, Menu, Moon, PieChart, Search, Server, Sun } from 'lucide-react'
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
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Kbd } from '@/components/ui/kbd'
import { toggleThemeWithTransition } from '@/components/use-theme'
import { useBackground } from '@/hooks/use-background'
import { logout } from '@/lib/fetch-with-auth'
import { isElectron } from '@/lib/runtime'
import { cn } from '@/lib/utils'

const LANGUAGE_CODES = ['zh', 'en', 'ja', 'ko'] as const

interface HeaderProps {
  sidebarOpen: boolean
  mobileMenuOpen: boolean
  searchOpen: boolean
  actualTheme: 'light' | 'dark'
  onSidebarToggle: () => void
  onMobileMenuToggle: () => void
  onSearchOpenChange: (open: boolean) => void
  onThemeChange: (theme: 'light' | 'dark' | 'system') => void
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
}: HeaderProps) {
  const { t, i18n: i18nInstance } = useTranslation()
  const currentLang = i18nInstance.language || 'zh'
  const headerBg = useBackground('header')
  const [backendManagerOpen, setBackendManagerOpen] = useState(false)
  const [activeBackendName, setActiveBackendName] = useState<string>('')

  useEffect(() => {
    if (!isElectron()) return
    window.electronAPI!.getActiveBackend().then((b) => {
      setActiveBackendName(b?.name ?? t('header.notConnected'))
    })
  }, [])

  const handleLogout = async () => {
    await logout()
  }

  return (
    <header className="flex h-16 items-center justify-between border-b bg-card/80 backdrop-blur-md px-4 sticky top-0 z-10">
      <BackgroundLayer config={headerBg} layerId="header" />
      <div className="flex items-center gap-4">
        {/* 移动端菜单按钮 */}
        <button
          onClick={onMobileMenuToggle}
          aria-label={t('a11y.closeMenu')}
          aria-expanded={mobileMenuOpen}
          className="rounded-lg p-2 hover:bg-accent lg:hidden"
        >
          <Menu className="h-5 w-5" />
        </button>
        
        {/* 桌面端侧边栏收起/展开按钮 */}
        <button
          onClick={onSidebarToggle}
          aria-label={sidebarOpen ? t('header.collapseSidebar') : t('header.expandSidebar')}
          aria-expanded={sidebarOpen}
          className="hidden rounded-lg p-2 hover:bg-accent lg:block"
        >
          <ChevronLeft
            className={cn('h-5 w-5 transition-transform', !sidebarOpen && 'rotate-180')}
          />
        </button>
      </div>

      <div className="flex items-center gap-2">
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
              <span className="hidden sm:inline text-xs text-muted-foreground truncate max-w-[100px]">
                {activeBackendName}
              </span>
            </Button>
            <BackendManager open={backendManagerOpen} onOpenChange={setBackendManagerOpen} />
            <div className="h-6 w-px bg-border" />
          </>
        )}
        {/* 年度总结入口 */}
        <Link to="/annual-report">
          <Button
            variant="ghost"
            size="sm"
            className="gap-2 bg-gradient-to-r from-pink-500/10 to-purple-500/10 hover:from-pink-500/20 hover:to-purple-500/20 border border-pink-500/20"
            title={t('header.viewAnnualSummary')}
          >
            <PieChart className="h-4 w-4 text-pink-500" />
            <span className="hidden sm:inline bg-gradient-to-r from-pink-500 to-purple-500 bg-clip-text text-transparent font-medium">{t('header.annualSummary')}</span>
          </Button>
        </Link>

        {/* 搜索框 */}
        <button
          onClick={() => onSearchOpenChange(true)}
          aria-label={t('header.searchPlaceholder')}
          className="relative hidden md:flex items-center w-64 h-9 pl-9 pr-16 bg-background/50 border rounded-md hover:bg-accent/50 transition-colors text-left"
        >
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <span className="text-sm text-muted-foreground">{t('header.searchPlaceholder')}</span>
          <Kbd size="sm" className="absolute right-2 top-1/2 -translate-y-1/2">
            <span className="text-xs">⌘</span>K
          </Kbd>
        </button>

        {/* 搜索对话框 */}
        <SearchDialog open={searchOpen} onOpenChange={onSearchOpenChange} />

        {/* 麦麦文档链接 */}
        <Button
          variant="ghost"
          size="sm"
          onClick={() => window.open('https://docs.mai-mai.org', '_blank')}
          className="gap-2"
          title={t('header.viewDocs')}
        >
          <BookOpen className="h-4 w-4" />
          <span className="hidden sm:inline">{t('header.docs')}</span>
        </Button>

        {/* 语言切换 */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="gap-2">
              <Globe className="h-4 w-4" />
              <span className="hidden sm:inline text-xs">
                {t(`language.${currentLang.split('-')[0] as 'zh' | 'en' | 'ja' | 'ko'}`) ?? currentLang}
              </span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {LANGUAGE_CODES.map((code) => (
              <DropdownMenuItem
                key={code}
                onClick={() => i18nInstance.changeLanguage(code)}
                className={cn(
                  'cursor-pointer',
                  currentLang.split('-')[0] === code && 'font-semibold text-primary'
                )}
              >
                {currentLang.split('-')[0] === code && (
                  <span className="mr-2">✓</span>
                )}
                {t(`language.${code}`)}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        {/* 主题切换按钮 */}
        <button
          onClick={(e) => {
            const newTheme = actualTheme === 'dark' ? 'light' : 'dark'
            toggleThemeWithTransition(newTheme, onThemeChange, e)
          }}
          aria-label={actualTheme === 'dark' ? t('header.switchToLight') : t('header.switchToDark')}
          className="rounded-lg p-2 hover:bg-accent"
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
          title={t('header.logout')}
        >
          <LogOut className="h-4 w-4" />
          <span className="hidden sm:inline">{t('header.logoutLabel')}</span>
        </Button>
      </div>
    </header>
  )
}
