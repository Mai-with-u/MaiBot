import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { AlertTriangle, ArrowUpDown, Filter, Info, Loader2, Search, Settings2 } from 'lucide-react'

import { RestartOverlay } from '@/components/restart-overlay'
import { RestartProvider } from '@/lib/restart-context'

import { InstallDialog } from './InstallDialog'
import { MarketplaceTab } from './MarketplaceTab'
import { usePluginMarketplaceActions } from './hooks/usePluginMarketplaceActions'
import { usePluginMarketplaceBootstrap } from './hooks/usePluginMarketplaceBootstrap'
import { usePluginMarketplaceViewState } from './hooks/usePluginMarketplaceViewState'
import { getPluginType, PLUGIN_TYPE_OPTIONS, type MarketplaceSortKey } from './types'
import { PluginDetailPage } from '../plugin-detail'

const PLUGIN_MARKET_COMPATIBLE_ONLY_KEY = 'plugins-market-compatible-only'

interface PluginMarketplacePageProps {
  embedded?: boolean
}

// 插件市场页：只展示市场索引、安装状态和版本信息
export function PluginMarketplacePage({ embedded = false }: PluginMarketplacePageProps) {
  return (
    <RestartProvider>
      <PluginMarketplacePageContent embedded={embedded} />
    </RestartProvider>
  )
}

// 内部组件：实际内容
function PluginMarketplacePageContent({ embedded }: Required<PluginMarketplacePageProps>) {
  const navigate = useNavigate()
  const settingsRoute: '/plugin-mirrors' | '/plugin-mirrors/embed' = embedded
    ? '/plugin-mirrors/embed'
    : '/plugin-mirrors'
  const [restartNoticeVisible, setRestartNoticeVisible] = useState(
    () => localStorage.getItem('plugins-restart-notice-dismissed') !== 'true'
  )
  const [showCompatibleOnly] = useState(
    () => localStorage.getItem(PLUGIN_MARKET_COMPATIBLE_ONLY_KEY) !== 'false'
  )
  const [detailPluginId, setDetailPluginId] = useState<string | null>(null)

  const {
    plugins,
    setPlugins,
    loading,
    error,
    gitStatus,
    marketplaceProgress,
    pluginProgressById,
    maimaiVersion,
    pluginStats,
    setPluginStats,
    setInstalledPlugins,
    setPluginProgress,
    setCompletedPluginProgress,
  } = usePluginMarketplaceBootstrap()

  const {
    scrollViewportRef,
    searchQuery,
    setSearchQuery,
    pluginTypeFilter,
    setPluginTypeFilter,
    marketplaceSortBy,
    setMarketplaceSortBy,
    showInstalledPlugins,
    setShowInstalledPlugins,
  } = usePluginMarketplaceViewState({
    loading,
    pluginCount: plugins.length,
  })

  const {
    installDialogOpen,
    installingPlugin,
    installProgress,
    likingPluginIds,
    openInstallDialog,
    handleInstallDialogOpenChange,
    handleInstall,
    handleUninstall,
    handleUpdate,
    handleLike,
    checkPluginCompatibility,
    getIncompatibleReason,
    getStatusBadge,
    needsUpdate,
  } = usePluginMarketplaceActions({
    gitStatus,
    maimaiVersion,
    pluginProgressById,
    setPlugins,
    setInstalledPlugins,
    setPluginStats,
    setPluginProgress,
    setCompletedPluginProgress,
  })

  const isFetchingMarketplace = marketplaceProgress?.stage === 'loading'
    && marketplaceProgress.operation === 'fetch'

  const dismissRestartNotice = () => {
    localStorage.setItem('plugins-restart-notice-dismissed', 'true')
    setRestartNoticeVisible(false)
  }

  // 过滤插件用于标签页统计
  const getFilteredPluginCount = () => {
    return plugins.filter(p => {
      if (!p.manifest) return false
      if (p.source === 'local') return false
      if (!showInstalledPlugins && p.installed) return false
      const matchesSearch = searchQuery === '' ||
        p.manifest.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        p.manifest.description?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (p.manifest.keywords && p.manifest.keywords.some(k => k.toLowerCase().includes(searchQuery.toLowerCase())))
      const matchesType = pluginTypeFilter === 'all' || getPluginType(p) === pluginTypeFilter
      const matchesCompatibility = !showCompatibleOnly ||
        !maimaiVersion ||
        checkPluginCompatibility(p)

      return matchesSearch && matchesType && matchesCompatibility
    }).length
  }

  return (
    <ScrollArea className="h-full" viewportRef={scrollViewportRef}>
      <div className="space-y-6 p-4 sm:p-6">
        {/* 安装提示 */}
        {restartNoticeVisible && (
          <Card className="border-blue-200 bg-blue-50 dark:bg-blue-950/20 dark:border-blue-900">
            <CardContent className="py-3!">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-center gap-2">
                  <Info className="h-4 w-4 text-blue-600 flex-shrink-0" />
                  <p className="text-sm text-blue-800 dark:text-blue-200">
                    安装、卸载或更新插件后，部分插件需要<span className="font-semibold">重启麦麦</span>才能生效
                  </p>
                </div>
                <Button type="button" variant="outline" size="sm" onClick={dismissRestartNotice}>
                  我知道了
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Git 状态警告 */}
        {gitStatus && !gitStatus.installed && (
          <Card className="border-orange-600 bg-orange-50 dark:bg-orange-950/20">
            <CardHeader>
              <div className="flex items-center gap-3">
                <AlertTriangle className="h-5 w-5 text-orange-600" />
                <div>
                  <CardTitle className="text-lg text-orange-900 dark:text-orange-100">
                    Git 未安装
                  </CardTitle>
                  <CardDescription className="text-orange-800 dark:text-orange-200">
                    {gitStatus.error || '请先安装 Git 才能使用插件安装功能'}
                  </CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-orange-800 dark:text-orange-200">
                您可以从 <a href="https://git-scm.com/downloads" target="_blank" rel="noopener noreferrer" className="underline font-medium">git-scm.com</a> 下载并安装 Git。
                安装完成后，请重启麦麦应用。
              </p>
            </CardContent>
          </Card>
        )}

        {/* 搜索和筛选栏 */}
        <Card className="p-4">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
            {/* 搜索框 */}
            <div className="relative w-full sm:max-w-md sm:flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="搜索插件..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-9"
              />
            </div>

            {/* 类型筛选 */}
            <Select value={pluginTypeFilter} onValueChange={setPluginTypeFilter}>
              <SelectTrigger
                aria-label="类型筛选"
                title="类型筛选"
                className="w-full justify-center gap-1 px-2 sm:w-12"
              >
                <Filter className="h-4 w-4" />
                <span className="sr-only">
                  <SelectValue placeholder="选择类型" />
                </span>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部类型</SelectItem>
                {PLUGIN_TYPE_OPTIONS.map(option => (
                  <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>

            {/* 排序 */}
            <Select
              value={marketplaceSortBy}
              onValueChange={(value) => setMarketplaceSortBy(value as MarketplaceSortKey)}
            >
              <SelectTrigger
                aria-label="排序"
                title="排序"
                className="w-full justify-center gap-1 px-2 sm:w-12"
              >
                <ArrowUpDown className="h-4 w-4" />
                <span className="sr-only">
                  <SelectValue placeholder="排序" />
                </span>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="default">推荐排序</SelectItem>
                <SelectItem value="latest">最新上架</SelectItem>
                <SelectItem value="downloads">下载最多</SelectItem>
                <SelectItem value="likes">点赞最多</SelectItem>
                <SelectItem value="rating">评分最高</SelectItem>
              </SelectContent>
            </Select>

            <Badge
              variant="outline"
              data-plugin-market-count-badge="true"
              className="h-9 border-input bg-transparent px-3 text-sm font-normal"
            >
              全部插件 {getFilteredPluginCount()}
            </Badge>

            <Button
              type="button"
              variant="ghost"
              data-plugin-market-settings-button="true"
              className="w-full bg-transparent shadow-none hover:bg-transparent sm:ml-auto sm:w-auto"
              onClick={() => navigate({ to: settingsRoute })}
            >
              <Settings2 className="h-4 w-4 mr-2" />
              设置
            </Button>

            {/* 兼容性筛选 */}
            <div className="flex w-full items-center justify-between gap-3 sm:w-auto sm:min-w-fit sm:flex-col sm:items-center sm:justify-center sm:gap-1">
              <label
                htmlFor="show-installed-plugins"
                className="cursor-pointer text-xs font-medium leading-none text-muted-foreground whitespace-nowrap"
              >
                显示已安装
              </label>
              <Switch
                id="show-installed-plugins"
                checked={showInstalledPlugins}
                onCheckedChange={setShowInstalledPlugins}
              />
            </div>
          </div>
          {isFetchingMarketplace && (
            <div
              className="mt-3 flex min-w-0 items-center gap-2 rounded-md border bg-background/85 px-3 py-2 text-xs shadow-sm backdrop-blur"
              aria-live="polite"
            >
              <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />
              <span className="shrink-0 font-medium">加载插件市场</span>
              <span className="min-w-0 truncate text-muted-foreground">
                {marketplaceProgress?.message || '正在获取插件清单'}
              </span>
            </div>
          )}
        </Card>

        {/* 加载错误显示 */}
        {marketplaceProgress
          && marketplaceProgress.operation === 'fetch'
          && marketplaceProgress.stage === 'error'
          && marketplaceProgress.error && (
          <Card className="border-destructive bg-destructive/10">
            <CardHeader>
              <div className="flex items-center gap-3">
                <AlertTriangle className="h-5 w-5 text-destructive" />
                <div>
                  <CardTitle className="text-lg text-destructive">
                    加载失败
                  </CardTitle>
                  <CardDescription className="text-destructive/80">
                    {marketplaceProgress.error}
                  </CardDescription>
                </div>
              </div>
            </CardHeader>
          </Card>
        )}

        {/* 插件卡片网格 */}
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <ThinkingIllustration size="lg" />
          </div>
        ) : error ? (
          <Card className="p-6">
            <div className="flex flex-col items-center justify-center py-8 text-center">
              <AlertTriangle className="h-12 w-12 text-destructive mb-4" />
              <h3 className="text-lg font-semibold mb-2">加载失败</h3>
              <p className="text-sm text-muted-foreground mb-4">{error}</p>
              <Button onClick={() => window.location.reload()}>
                重新加载
              </Button>
            </div>
          </Card>
        ) : (
          <MarketplaceTab
            plugins={plugins}
            searchQuery={searchQuery}
            pluginTypeFilter={pluginTypeFilter}
            showCompatibleOnly={showCompatibleOnly}
            hideInstalledPlugins={!showInstalledPlugins}
            sortBy={marketplaceSortBy}
            gitStatus={gitStatus}
            maimaiVersion={maimaiVersion}
            pluginStats={pluginStats}
            pluginProgressById={pluginProgressById}
            likingPluginIds={likingPluginIds}
            onInstall={openInstallDialog}
            onLike={handleLike}
            onUpdate={handleUpdate}
            onUninstall={handleUninstall}
            onDetail={(plugin) => setDetailPluginId(plugin.id)}
            checkPluginCompatibility={checkPluginCompatibility}
            needsUpdate={needsUpdate}
            getStatusBadge={getStatusBadge}
            getIncompatibleReason={getIncompatibleReason}
          />
        )}

        {/* 安装对话框 */}
        <InstallDialog
          open={installDialogOpen}
          plugin={installingPlugin}
          loadProgress={installProgress}
          onOpenChange={handleInstallDialogOpenChange}
          onInstall={handleInstall}
        />

        <Dialog open={detailPluginId !== null} onOpenChange={(open) => !open && setDetailPluginId(null)}>
          <DialogContent className="max-w-[calc(100vw-2rem)] p-0 [--dialog-width:88rem]" hideCloseButton>
            {detailPluginId ? (
              <PluginDetailPage
                embedded={embedded}
                mode="dialog"
                onClose={() => setDetailPluginId(null)}
                pluginId={detailPluginId}
              />
            ) : null}
          </DialogContent>
        </Dialog>

        {/* 重启遮罩层 */}
        <RestartOverlay />
      </div>
    </ScrollArea>
  )
}
