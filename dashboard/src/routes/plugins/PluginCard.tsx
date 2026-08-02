import { AlertCircle, CheckCircle2, Download, Info, Loader2, RefreshCw, ThumbsUp, Trash2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'

import { PluginIcon } from './PluginIcon'
import type { GitStatus, MaimaiVersion, PluginInfo, PluginLoadProgress, PluginStatsData } from './types'
import { getPluginProgressDetail, getPluginTypeLabel } from './types'

interface PluginCardProps {
  plugin: PluginInfo
  gitStatus: GitStatus | null
  maimaiVersion: MaimaiVersion | null
  pluginStats: Record<string, PluginStatsData>
  loadProgress: PluginLoadProgress | null
  isAnyPluginInstalling?: boolean
  likingPluginIds: Set<string>
  onInstall: (plugin: PluginInfo) => void
  onLike: (plugin: PluginInfo) => void
  onUpdate: (plugin: PluginInfo) => void
  onUninstall: (plugin: PluginInfo) => void
  onDetail: (plugin: PluginInfo) => void
  checkPluginCompatibility: (plugin: PluginInfo) => boolean
  needsUpdate: (plugin: PluginInfo) => boolean
  getStatusBadge: (plugin: PluginInfo) => React.JSX.Element | null
  getIncompatibleReason: (plugin: PluginInfo) => string | null
}

export function PluginCard({
  plugin,
  gitStatus,
  maimaiVersion,
  pluginStats,
  loadProgress,
  isAnyPluginInstalling = false,
  likingPluginIds,
  onInstall,
  onLike,
  onUpdate,
  onUninstall,
  onDetail,
  checkPluginCompatibility,
  needsUpdate,
  getStatusBadge,
  getIncompatibleReason,
}: PluginCardProps) {
  const stats = [plugin.manifest?.id]
    .map(id => id ? pluginStats[id] : undefined)
    .find(Boolean)
  const likeCount = stats?.likes ?? 0
  const downloadCount = stats?.downloads ?? plugin.downloads ?? 0
  const ratingValue = stats?.rating ?? plugin.rating ?? 0
  const reviewCount = stats?.rating_count ?? plugin.review_count ?? 0
  const isLiked = stats?.liked === true
  const isLiking = likingPluginIds.has(plugin.manifest?.id || plugin.id)
  const isInstalling = loadProgress?.operation === 'install'
    && loadProgress.stage === 'loading'
    && loadProgress?.plugin_id === plugin.id
  const isPluginOperating = loadProgress?.stage === 'loading'
    && loadProgress.operation !== 'fetch'
  const progressDetail = loadProgress ? getPluginProgressDetail(loadProgress) : null

  return (
    <Card
      key={plugin.id}
      data-plugin-market-card="true"
      className="flex h-full flex-col"
    >
      <CardHeader className="p-4 pb-2.5">
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 flex-1 items-start gap-2.5">
            <div className="flex h-[4.125rem] w-12 shrink-0 flex-col items-center gap-1.5">
              <PluginIcon
                pluginId={plugin.id}
                manifest={plugin.manifest}
                installed={plugin.installed}
                marketplaceIconUrl={plugin.assets?.icon_64}
                className="h-12 w-12 rounded-md"
                iconClassName="h-5 w-5"
              />
              <span
                data-plugin-type-label="true"
                className="text-primary whitespace-nowrap text-center text-xs font-semibold leading-none"
              >
                {getPluginTypeLabel(plugin)}
              </span>
            </div>
            <CardTitle className="h-[4.125rem] min-w-0 flex-1 text-lg leading-[1.2]">
              <span className="line-clamp-2 break-words">
                {plugin.manifest?.name || plugin.id}
              </span>
            </CardTitle>
          </div>
          {getStatusBadge(plugin)}
        </div>
        <CardDescription className="line-clamp-3 min-h-[3.09375rem] text-xs leading-snug">
          {plugin.manifest?.description || '无描述'}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex-1 px-4 pb-2.5">
        <div className="space-y-2">
          {/* 统计信息 */}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <div className="flex items-center gap-1">
              <span>下载</span>
              <span
                data-plugin-stat-value="downloads"
                className={downloadCount !== 0 ? 'text-primary' : undefined}
              >
                {downloadCount.toLocaleString()}
              </span>
            </div>
            <div className="flex items-center gap-1">
              <span>评分</span>
              <span
                data-plugin-stat-value="rating"
                className={ratingValue !== 0 ? 'text-primary' : undefined}
              >
                {ratingValue.toFixed(1)}
              </span>
            </div>
            <div className="flex items-center gap-1">
              <span>点赞</span>
              <span
                data-plugin-stat-value="likes"
                className={likeCount !== 0 ? 'text-primary' : undefined}
              >
                {likeCount.toLocaleString()}
              </span>
            </div>
            <div className="flex items-center gap-1">
              <span>评论</span>
              <span
                data-plugin-stat-value="reviews"
                className={reviewCount !== 0 ? 'text-primary' : undefined}
              >
                {reviewCount.toLocaleString()}
              </span>
            </div>
          </div>
          {/* 标签 */}
          <div className="flex flex-wrap gap-1.5">
            {plugin.manifest?.keywords && plugin.manifest.keywords.slice(0, 3).map((keyword) => (
              <Badge key={keyword} variant="outline" className="px-1.5 py-0 text-[11px]">
                {keyword}
              </Badge>
            ))}
            {plugin.manifest?.keywords && plugin.manifest.keywords.length > 3 && (
              <Badge variant="outline" className="px-1.5 py-0 text-[11px]">
                +{plugin.manifest.keywords.length - 3}
              </Badge>
            )}
          </div>
          {/* 版本和作者 */}
          <div className="space-y-1 border-t pt-2 text-xs text-muted-foreground">
            <div>v{plugin.manifest?.version || 'unknown'} · {plugin.manifest?.author?.name || 'Unknown'}</div>
            {/* 支持版本 */}
            {plugin.manifest?.host_application && (
              <div className="flex items-center gap-1">
                <span>支持:</span>
                <span className="font-medium">
                  {plugin.manifest.host_application.min_version}
                  {plugin.manifest.host_application.max_version 
                    ? ` - ${plugin.manifest.host_application.max_version}`
                    : ' - 最新版本'
                  }
                </span>
              </div>
            )}
          </div>
        </div>
      </CardContent>
      <CardFooter className="mt-auto px-4 pb-4 pt-1.5">
        <div className="grid w-full grid-cols-3 gap-2 sm:flex sm:items-center sm:justify-end">
          <Button
            variant={isLiked ? 'secondary' : 'outline'}
            size="sm"
            className="w-full px-2 sm:w-auto"
            title={isLiked ? '取消点赞' : '点赞'}
            aria-label={isLiked ? '取消点赞' : '点赞'}
            disabled={isLiking}
            onClick={() => onLike(plugin)}
          >
            {isLiking ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <ThumbsUp className={isLiked ? 'h-4 w-4 fill-current' : 'h-4 w-4'} />
            )}
            <span>{likeCount.toLocaleString()}</span>
          </Button>
          <Button 
            variant="outline"
            size="sm"
            className="w-full px-0 sm:w-8"
            title="查看详情"
            aria-label="查看详情"
            onClick={() => onDetail(plugin)}
          >
            <Info className="h-4 w-4" />
          </Button>
          {plugin.installed ? (
            needsUpdate(plugin) ? (
              <Button 
                size="sm"
                className="w-full sm:w-auto"
                disabled={
                  !gitStatus?.installed
                  || isPluginOperating
                  || (maimaiVersion !== null && !checkPluginCompatibility(plugin))
                }
                title={
                  !gitStatus?.installed
                    ? 'Git 未安装'
                    : isPluginOperating
                      ? '插件操作进行中'
                    : (maimaiVersion !== null && !checkPluginCompatibility(plugin))
                      ? (getIncompatibleReason(plugin) ?? '插件与当前麦麦版本不兼容')
                      : undefined
                }
                onClick={() => onUpdate(plugin)}
              >
                {isPluginOperating ? (
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="mr-1 h-4 w-4" />
                )}
                {isPluginOperating ? '更新中' : '更新'}
              </Button>
            ) : (
              <Button 
                variant="destructive" 
                size="sm"
                className="w-full sm:w-auto"
                disabled={!gitStatus?.installed || isPluginOperating}
                title={
                  !gitStatus?.installed
                    ? 'Git 未安装'
                    : isPluginOperating
                      ? '插件操作进行中'
                      : undefined
                }
                onClick={() => onUninstall(plugin)}
              >
                <Trash2 className="h-4 w-4 mr-1" />
                卸载
              </Button>
            )
          ) : (
            <Button 
              size="sm"
              className="w-full px-0 sm:w-8"
              disabled={
                !gitStatus?.installed || 
                isAnyPluginInstalling ||
                (maimaiVersion !== null && !checkPluginCompatibility(plugin))
              }
              title={
                !gitStatus?.installed 
                  ? 'Git 未安装' 
                  : (maimaiVersion !== null && !checkPluginCompatibility(plugin))
                    ? (getIncompatibleReason(plugin) ?? '插件与当前麦麦版本不兼容')
                    : undefined
              }
              aria-label={isInstalling ? '正在安装' : '安装'}
              onClick={() => onInstall(plugin)}
            >
              {isInstalling ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
            </Button>
          )}
        </div>
      </CardFooter>
      {/* 安装/卸载/更新进度显示 - 在卡片下方 */}
      {loadProgress && 
        (loadProgress.stage === 'loading' || loadProgress.stage === 'success' || loadProgress.stage === 'error') && 
        loadProgress.operation !== 'fetch' && 
        loadProgress.plugin_id === plugin.id && (
        <div className="-mt-1 px-4 pb-4">
          <div className={`space-y-2 rounded-lg border p-2.5 ${
            loadProgress.stage === 'success' 
              ? 'bg-green-50 dark:bg-green-950/20 border-green-200 dark:border-green-900' 
              : loadProgress.stage === 'error'
                ? 'bg-red-50 dark:bg-red-950/20 border-red-200 dark:border-red-900'
                : 'bg-muted/50'
          }`}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                {loadProgress.stage === 'loading' ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : loadProgress.stage === 'success' ? (
                  <CheckCircle2 className="h-3 w-3 text-green-600" />
                ) : (
                  <AlertCircle className="h-3 w-3 text-red-600" />
                )}
                <span className={`text-xs font-medium ${
                  loadProgress.stage === 'success' 
                    ? 'text-green-700 dark:text-green-300' 
                    : loadProgress.stage === 'error'
                      ? 'text-red-700 dark:text-red-300'
                      : ''
                }`}>
                  {loadProgress.stage === 'loading' ? (
                    <>
                      {loadProgress.operation === 'install' && '正在安装'}
                      {loadProgress.operation === 'uninstall' && '正在卸载'}
                      {loadProgress.operation === 'update' && '正在更新'}
                    </>
                  ) : loadProgress.stage === 'success' ? (
                    <>
                      {loadProgress.operation === 'install' && '安装完成'}
                      {loadProgress.operation === 'uninstall' && '卸载完成'}
                      {loadProgress.operation === 'update' && '更新完成'}
                    </>
                  ) : (
                    <>
                      {loadProgress.operation === 'install' && '安装失败'}
                      {loadProgress.operation === 'uninstall' && '卸载失败'}
                      {loadProgress.operation === 'update' && '更新失败'}
                    </>
                  )}
                </span>
              </div>
              {loadProgress.stage !== 'error' && (
                <span className={`text-xs font-medium ${
                  loadProgress.stage === 'success' ? 'text-green-700 dark:text-green-300' : ''
                }`}>{loadProgress.progress}%</span>
              )}
            </div>
            {loadProgress.stage !== 'error' && (
              <Progress 
                value={loadProgress.progress} 
                className={`h-1.5 ${loadProgress.stage === 'success' ? '[&>div]:bg-green-500' : ''}`} 
              />
            )}
            <div className={`text-xs ${
              loadProgress.stage === 'success' 
                ? 'text-green-600 dark:text-green-400 truncate' 
                : loadProgress.stage === 'error'
                  ? 'text-red-600 dark:text-red-400'
                  : 'text-muted-foreground truncate'
            }`}>
              {loadProgress.stage === 'error' ? (loadProgress.error || loadProgress.message || '操作失败') : loadProgress.message}
            </div>
            {progressDetail && (
              <div className="truncate text-xs text-muted-foreground">
                {progressDetail}
              </div>
            )}
          </div>
        </div>
      )}
    </Card>
  )
}
