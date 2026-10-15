import { AlertCircle, CheckCircle2, Download, Loader2, MessageSquare, RefreshCw, Star, ThumbsUp, Trash2, TrendingUp } from 'lucide-react'
import { type CSSProperties, useRef, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'

import { PluginIcon } from './PluginIcon'
import type { GitStatus, MaimaiVersion, PluginInfo, PluginLoadProgress, PluginStatsData } from './types'
import { getPluginProgressDetail } from './types'

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

function getRecentPluginActivity(plugin: PluginInfo): { timeLabel: string; action: string } | null {
  const parseTime = (value?: string) => value ? Date.parse(value) : NaN
  const releaseTimes = (plugin.releases?.versions ?? [])
    .filter((release) => !release.prerelease && !release.yanked)
    .map((release) => parseTime(release.published_at))
    .filter(Number.isFinite)
  const publishedTime = parseTime(plugin.published_at)
  const firstPublishedTime = Number.isFinite(publishedTime)
    ? publishedTime
    : releaseTimes.length > 0 ? Math.min(...releaseTimes) : NaN
  const activityTimes = [firstPublishedTime, parseTime(plugin.updated_at), ...releaseTimes]
    .filter(Number.isFinite)
  if (activityTimes.length === 0) return null

  // 首次发布时间之后的市场改动或正式版本发布都算更新，只展示最近七天的活动。
  const latestTime = Math.max(...activityTimes)
  const ageMinutes = (Date.now() - latestTime) / 60_000
  if (ageMinutes < 0 || ageMinutes >= 7 * 24 * 60) return null

  const timeLabel = ageMinutes >= 24 * 60
    ? `${Math.floor(ageMinutes / (24 * 60))} 天前`
    : ageMinutes >= 60
      ? `${Math.floor(ageMinutes / 60)} H前`
      : `${Math.floor(ageMinutes)} 分钟前`
  return {
    timeLabel,
    action: latestTime === firstPublishedTime ? '发布' : '更新',
  }
}

function getPluginGrowthRank(rank7d?: number | null, rank30d?: number | null): { rank: number; days: number } | null {
  // 仅展示前八名；同一指标优先展示短期飙升，避免四套位次挤占卡片标题。
  if (rank7d != null && Number.isInteger(rank7d) && rank7d >= 1 && rank7d <= 8) {
    return { rank: rank7d, days: 7 }
  }
  if (rank30d != null && Number.isInteger(rank30d) && rank30d >= 1 && rank30d <= 8) {
    return { rank: rank30d, days: 30 }
  }
  return null
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
  const pluginName = plugin.manifest?.name || plugin.id
  const titleViewportRef = useRef<HTMLSpanElement>(null)
  const titleTextRef = useRef<HTMLSpanElement>(null)
  const [titleOverflow, setTitleOverflow] = useState(0)

  // 仅在用户与当前卡片交互时测量，避免为整页插件强制布局和常驻尺寸监听。
  const measureTitleOverflow = () => {
    const viewport = titleViewportRef.current
    const text = titleTextRef.current
    if (!viewport || !text) return
    setTitleOverflow(Math.max(0, text.scrollWidth - viewport.clientWidth))
  }

  const stats = [plugin.manifest?.id]
    .map(id => id ? pluginStats[id] : undefined)
    .find(Boolean)
  const likeCount = stats?.likes ?? 0
  const downloadCount = stats?.downloads ?? plugin.downloads ?? 0
  const ratingValue = stats?.rating ?? plugin.rating ?? 0
  const reviewCount = stats?.comment_count ?? plugin.review_count ?? 0
  const isLiked = stats?.liked === true
  const isLiking = likingPluginIds.has(plugin.manifest?.id || plugin.id)
  const isInstalling = loadProgress?.operation === 'install'
    && loadProgress.stage === 'loading'
    && loadProgress?.plugin_id === plugin.id
  const isPluginOperating = loadProgress?.stage === 'loading'
    && loadProgress.operation !== 'fetch'
  const progressDetail = loadProgress ? getPluginProgressDetail(loadProgress) : null
  const recentActivity = getRecentPluginActivity(plugin)
  const downloadGrowth = getPluginGrowthRank(stats?.downloads_growth_rank_7d, stats?.downloads_growth_rank_30d)
  const likeGrowth = getPluginGrowthRank(stats?.likes_growth_rank_7d, stats?.likes_growth_rank_30d)
  // 下载与点赞只保留名次更靠前的一项；同名次优先短期榜，再优先下载榜。
  const showDownloadGrowth = downloadGrowth !== null && (
    likeGrowth === null
    || downloadGrowth.rank < likeGrowth.rank
    || (downloadGrowth.rank === likeGrowth.rank && downloadGrowth.days <= likeGrowth.days)
  )

  return (
    <Card
      key={plugin.id}
      data-plugin-market-card="true"
      data-plugin-installed={plugin.installed ? 'true' : undefined}
      data-plugin-recent-activity={recentActivity || downloadGrowth || likeGrowth ? 'true' : undefined}
      className="relative isolate row-span-3 grid h-full min-w-0 cursor-pointer grid-cols-1 grid-rows-subgrid gap-y-0"
      onPointerEnter={measureTitleOverflow}
      onFocusCapture={measureTitleOverflow}
      onClick={(event) => {
        // 操作区整体排除，禁用按钮不接收指针事件时也不会误打开详情。
        if (event.target instanceof Element && event.target.closest('button, a, [data-plugin-card-actions="true"]')) {
          return
        }
        onDetail(plugin)
      }}
    >
      <CardHeader className="min-w-0 p-3 pb-2 sm:p-3 sm:pb-2">
        <div className="flex min-w-0 items-start justify-between gap-2">
          <div className="flex min-w-0 flex-1 items-stretch gap-2">
            <div className="relative w-14 shrink-0 self-stretch">
              <PluginIcon
                pluginId={plugin.id}
                manifest={plugin.manifest}
                installed={plugin.installed}
                marketplaceIconUrl={plugin.assets?.icon_64}
                className="absolute inset-0 h-full w-full rounded-md"
                iconClassName="h-6 w-6 text-3xl"
              />
              {plugin.installed && <span className="sr-only">已安装</span>}
              {needsUpdate(plugin) && (
                <span className="absolute inset-x-0 bottom-0 bg-orange-600 px-0.5 py-1 text-center text-[10px] font-semibold leading-none text-white">
                  可更新
                </span>
              )}
            </div>
            <div className="flex min-h-14 min-w-0 flex-1 flex-col justify-center gap-1">
              {/* 标题只占一行，超长时在整卡悬停或键盘聚焦期间滚动显示。 */}
              <CardTitle className="h-[1lh] min-w-0 text-[19px] leading-[1.2] text-foreground">
                <button
                  type="button"
                  className="block w-full cursor-pointer text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                  aria-label={`查看 ${plugin.manifest?.name || plugin.id} 详情`}
                  onClick={() => onDetail(plugin)}
                >
                  <span
                    ref={titleViewportRef}
                    data-plugin-title-overflow={titleOverflow > 1 ? 'true' : undefined}
                    className="block w-full min-w-0 overflow-hidden"
                    style={{
                      '--plugin-title-scroll-distance': `-${titleOverflow}px`,
                      '--plugin-title-scroll-duration': `${Math.max(2.5, titleOverflow / 35)}s`,
                    } as CSSProperties}
                  >
                    <span ref={titleTextRef} data-plugin-title-text="true" className="block max-w-full truncate">
                      {pluginName}
                    </span>
                  </span>
                </button>
              </CardTitle>
              <div className="truncate text-xs text-muted-foreground" title={plugin.manifest?.author?.name || 'Unknown'}>
                {plugin.manifest?.author?.name || 'Unknown'}
              </div>
            </div>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-2">
            {getStatusBadge(plugin)}
            {showDownloadGrowth && downloadGrowth && (
              <div
                className="flex items-center gap-1 rounded-sm bg-orange-700 px-1.5 py-1 text-[11px] font-semibold text-white shadow-sm dark:bg-orange-400 dark:text-orange-950"
                title={`${downloadGrowth.days} 天下载飙升榜第 ${downloadGrowth.rank} 名，按本期比上期的增加量排序`}
              >
                <TrendingUp className="h-3 w-3 shrink-0" />
                <span className="whitespace-nowrap">{downloadGrowth.days}日下载 #{downloadGrowth.rank}</span>
              </div>
            )}
            {!showDownloadGrowth && likeGrowth && (
              <div
                className="flex items-center gap-1 rounded-sm bg-rose-700 px-1.5 py-1 text-[11px] font-semibold text-white shadow-sm dark:bg-rose-400 dark:text-rose-950"
                title={`${likeGrowth.days} 天点赞飙升榜第 ${likeGrowth.rank} 名，按本期比上期的增加量排序，仅统计当前有效的赞`}
              >
                <TrendingUp className="h-3 w-3 shrink-0" />
                <span className="whitespace-nowrap">{likeGrowth.days}日点赞 #{likeGrowth.rank}</span>
              </div>
            )}
            {recentActivity && (
              <div
                className={`flex flex-col border-r-2 pr-2 text-right text-[11px] leading-tight ${recentActivity.action === '发布' ? 'border-emerald-500/70' : 'border-orange-500/70'}`}
                title={`${recentActivity.timeLabel}${recentActivity.action}`}
              >
                <span className="text-muted-foreground whitespace-nowrap">{recentActivity.timeLabel}</span>
                <span className="mt-0.5 font-medium">{recentActivity.action}</span>
              </div>
            )}
          </div>
        </div>
        <div className="flex min-w-0 items-center justify-between gap-2">
          {/* 统计信息 */}
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            <div className="flex items-center gap-1" title="下载量">
              <Download className="h-3.5 w-3.5" aria-hidden="true" />
              <span className="sr-only">下载</span>
              <span
                data-plugin-stat-value="downloads"
                className={downloadCount !== 0 ? 'text-primary' : undefined}
              >
                {downloadCount.toLocaleString()}
              </span>
            </div>
            <div className="flex items-center gap-1" title="评分">
              <Star className="h-3.5 w-3.5" aria-hidden="true" />
              <span className="sr-only">评分</span>
              <span
                data-plugin-stat-value="rating"
                className={ratingValue !== 0 ? 'text-primary' : undefined}
              >
                {ratingValue.toFixed(1)}
              </span>
            </div>
            <div className="flex items-center gap-1" title="点赞数">
              <ThumbsUp className="h-3.5 w-3.5" aria-hidden="true" />
              <span className="sr-only">点赞</span>
              <span
                data-plugin-stat-value="likes"
                className={likeCount !== 0 ? 'text-primary' : undefined}
              >
                {likeCount.toLocaleString()}
              </span>
            </div>
            <div className="flex items-center gap-1" title="评论数">
              <MessageSquare className="h-3.5 w-3.5" aria-hidden="true" />
              <span className="sr-only">评论</span>
              <span
                data-plugin-stat-value="reviews"
                className={reviewCount !== 0 ? 'text-primary' : undefined}
              >
                {reviewCount.toLocaleString()}
              </span>
            </div>
          </div>
          {/* 操作区独立处理交互，点击按钮或按钮间隙均不会打开详情。 */}
          <div data-plugin-card-actions="true" className="flex shrink-0 items-center justify-end gap-1">
            <Button
              variant={isLiked ? 'secondary' : 'outline'}
              size="sm"
              className="h-7 gap-1 px-1.5 [&_svg]:size-3.5"
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
            {plugin.installed ? (
              needsUpdate(plugin) ? (
                <Button
                  size="sm"
                  className="h-7 w-7 px-0 [&_svg]:size-3.5"
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
                        : '更新'
                  }
                  aria-label={isPluginOperating ? '更新中' : '更新'}
                  onClick={() => onUpdate(plugin)}
                >
                  {isPluginOperating ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <RefreshCw className="h-4 w-4" />
                  )}
                </Button>
              ) : (
                <Button
                  variant="destructive"
                  size="sm"
                  className="h-7 w-7 px-0 [&_svg]:size-3.5"
                  disabled={!gitStatus?.installed || isPluginOperating}
                  title={
                    !gitStatus?.installed
                      ? 'Git 未安装'
                      : isPluginOperating
                        ? '插件操作进行中'
                        : '卸载'
                  }
                  aria-label="卸载"
                  onClick={() => onUninstall(plugin)}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              )
            ) : (
              <Button
                size="sm"
                className="h-7 w-7 px-0 [&_svg]:size-3.5"
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
        </div>
      </CardHeader>
      <CardContent className="min-w-0 p-3 pt-0 sm:p-3 sm:pt-0">
        <div className="min-w-0 space-y-2">
          <CardDescription className="line-clamp-2 min-h-[2lh] min-w-0 break-words text-xs leading-snug">
            {plugin.manifest?.description || '无描述'}
          </CardDescription>
          {/* 标签 */}
          {plugin.manifest?.keywords && plugin.manifest.keywords.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {plugin.manifest.keywords.slice(0, 3).map((keyword) => (
                <Badge key={keyword} variant="outline" className="min-w-0 max-w-full px-1.5 py-0 text-[11px]">
                  <span className="truncate">{keyword}</span>
                </Badge>
              ))}
              {plugin.manifest.keywords.length > 3 && (
                <Badge variant="outline" className="px-1.5 py-0 text-[11px]">
                  +{plugin.manifest.keywords.length - 3}
                </Badge>
              )}
            </div>
          )}
        </div>
      </CardContent>
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
