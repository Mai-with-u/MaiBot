import { AlertCircle, CheckCircle2, Download, Loader2, MessageSquare, RefreshCw, Star, ThumbsUp, Trash2, TrendingUp } from 'lucide-react'
import { type CSSProperties, memo, useRef, useState } from 'react'

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
  stats: PluginStatsData | undefined
  loadProgress: PluginLoadProgress | null
  isAnyPluginInstalling?: boolean
  isLiking: boolean
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
    ? `${Math.floor(ageMinutes / (24 * 60))}天前`
    : ageMinutes >= 60
      ? `${Math.floor(ageMinutes / 60)}小时前`
      : `${Math.floor(ageMinutes)}分钟前`
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

export const PluginCard = memo(function PluginCard({
  plugin,
  gitStatus,
  maimaiVersion,
  stats,
  loadProgress,
  isAnyPluginInstalling = false,
  isLiking,
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

  const likeCount = stats?.likes ?? 0
  const downloadCount = stats?.downloads ?? plugin.downloads ?? 0
  const ratingValue = stats?.rating ?? plugin.rating ?? 0
  const reviewCount = stats?.comment_count ?? plugin.review_count ?? 0
  const isLiked = stats?.liked === true
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
      className="relative isolate row-span-2 grid h-full min-w-0 cursor-pointer grid-cols-1 grid-rows-subgrid gap-y-0"
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
      {/* 榜单标签跨在卡片右上边框，不挤占标题区域。 */}
      {showDownloadGrowth && downloadGrowth && (
        <div
          data-plugin-growth-badge="downloads"
          className="absolute right-4 top-0 z-10 -translate-y-1/2 flex items-center gap-1 rounded-sm bg-orange-700 px-1.5 py-1 text-[11px] font-semibold text-white shadow-sm dark:bg-orange-400 dark:text-orange-950"
          title={`${downloadGrowth.days} 天下载飙升榜第 ${downloadGrowth.rank} 名，按本期比上期的增加量排序`}
        >
          <TrendingUp className="h-3 w-3 shrink-0" />
          <span className="whitespace-nowrap">{downloadGrowth.days}日下载 #{downloadGrowth.rank}</span>
        </div>
      )}
      {!showDownloadGrowth && likeGrowth && (
        <div
          data-plugin-growth-badge="likes"
          className="absolute right-4 top-0 z-10 -translate-y-1/2 flex items-center gap-1 rounded-sm bg-orange-700 px-1.5 py-1 text-[11px] font-semibold text-white shadow-sm dark:bg-orange-400 dark:text-orange-950"
          title={`${likeGrowth.days} 天点赞飙升榜第 ${likeGrowth.rank} 名，按本期比上期的增加量排序，仅统计当前有效的赞`}
        >
          <TrendingUp className="h-3 w-3 shrink-0" />
          <span className="whitespace-nowrap">{likeGrowth.days}日点赞 #{likeGrowth.rank}</span>
        </div>
      )}
      {/* 安装状态与兼容性提示放在左上角，卡片边框保持统一。 */}
      <div className="absolute left-4 top-0 z-10 flex -translate-y-1/2 items-center gap-2">
        {plugin.installed && (
          <span
            data-plugin-installed-badge="true"
            className="inline-flex items-center whitespace-nowrap rounded-sm bg-orange-700 px-2 py-1 text-[11px] font-semibold text-white shadow-sm dark:bg-orange-400 dark:text-orange-950"
          >
            已安装
          </span>
        )}
        {getStatusBadge(plugin)}
      </div>
      <CardHeader className="min-w-0 space-y-4 p-5 sm:p-5">
        <div data-plugin-card-identity="true" className="flex min-w-0 items-center gap-4">
          <div data-plugin-card-icon="true" className="relative h-16 w-16 shrink-0">
            <PluginIcon
              pluginId={plugin.id}
              manifest={plugin.manifest}
              installed={plugin.installed}
              marketplaceIconUrl={plugin.assets?.icon_64}
              className="absolute inset-0 h-full w-full rounded-md shadow-md"
              iconClassName="h-6 w-6 text-3xl"
            />
            {needsUpdate(plugin) && (
              <span data-plugin-update-ribbon="true" className="absolute inset-x-0 bottom-0 bg-orange-600 px-0.5 py-1 text-center text-[10px] font-semibold leading-none text-white">
                可更新
              </span>
            )}
          </div>
          <div className="flex min-h-16 min-w-0 flex-1 flex-col justify-center gap-2">
            {/* 标题只占一行，超长时在整卡悬停或键盘聚焦期间滚动显示。 */}
            <CardTitle className="h-[1lh] min-w-0 text-[19px] font-normal leading-[1.2] text-foreground">
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
            <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1">
              <div data-plugin-card-author="true" className="min-w-0 truncate text-xs text-muted-foreground" title={plugin.manifest?.author?.name || 'Unknown'}>
                {plugin.manifest?.author?.name || 'Unknown'}
              </div>
              {recentActivity && (
                <div
                  data-plugin-activity={recentActivity.action === '发布' ? 'publish' : 'update'}
                  className="relative shrink-0 pl-3 text-[11px] leading-tight text-muted-foreground"
                  title={`${recentActivity.timeLabel}${recentActivity.action}`}
                >
                  <span className={`absolute left-0 top-1/2 h-1.5 w-1.5 -translate-y-1/2 ${recentActivity.action === '发布' ? 'bg-emerald-500' : 'bg-orange-500'}`} aria-hidden="true" />
                  <span className="whitespace-nowrap">{recentActivity.timeLabel}{recentActivity.action}</span>
                </div>
              )}
            </div>
          </div>
        </div>
        <CardDescription data-plugin-card-description="true" className="line-clamp-2 min-h-[2lh] min-w-0 break-words text-sm leading-[22px]">
          {plugin.manifest?.description || '无描述'}
        </CardDescription>
      </CardHeader>
      <CardContent data-plugin-card-body="true" className="flex min-w-0 flex-col justify-center px-5 py-4 sm:px-5 sm:py-4">
        <div className="min-w-0 space-y-2">
          <div data-plugin-card-toolbar="true" className="flex min-w-0 items-center justify-between gap-2">
            {/* 按实际内容宽度排列，三组读数等距，数字紧跟图标并在显示框内垂直居中。 */}
            <div data-plugin-card-stats="true" className="grid min-w-max flex-1 grid-cols-[max-content_max-content_max-content] content-center items-center gap-x-3 font-mono text-xs leading-none tabular-nums text-muted-foreground">
              <div className="flex items-center gap-1" title={`下载量：${downloadCount.toLocaleString()}`}>
                <Download className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                <span className="sr-only">下载</span>
                <span
                  data-plugin-stat-value="downloads"
                  className={`flex h-3.5 items-center whitespace-nowrap text-left ${downloadCount !== 0 ? 'text-primary' : ''}`}
                >
                  {downloadCount >= 10_000 ? `${(downloadCount / 10_000).toFixed(1)}w` : downloadCount.toString()}
                </span>
              </div>
              <div className="flex items-center gap-1" title="评分">
                <Star className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                <span className="sr-only">评分</span>
                <span
                  data-plugin-stat-value="rating"
                  className={`flex h-3.5 items-center whitespace-nowrap text-left ${ratingValue !== 0 ? 'text-primary' : ''}`}
                >
                  {ratingValue.toFixed(1)}
                </span>
              </div>
              <div className="flex items-center gap-1" title={`评论数：${reviewCount.toLocaleString()}`}>
                <MessageSquare className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                <span className="sr-only">评论</span>
                <span
                  data-plugin-stat-value="reviews"
                  className={`flex h-3.5 items-center whitespace-nowrap text-left ${reviewCount !== 0 ? 'text-primary' : ''}`}
                >
                  {reviewCount >= 1_000 ? `${(reviewCount / 1_000).toFixed(1)}k` : reviewCount.toString()}
                </span>
              </div>
            </div>
            {/* 操作区独立处理交互，点击按钮或按钮间隙均不会打开详情。 */}
            <div data-plugin-card-actions="true" className="ml-auto flex shrink-0 items-center justify-end gap-1">
              <Button
                data-plugin-like-button="true"
                data-liked={isLiked ? 'true' : undefined}
                variant={isLiked ? 'secondary' : 'outline'}
                size="sm"
                className="gap-1 px-1.5 [&_svg]:size-3.5"
                title={`${isLiked ? '取消点赞' : '点赞'} · ${likeCount.toLocaleString()}`}
                aria-label={isLiked ? '取消点赞' : '点赞'}
                aria-pressed={isLiked}
                disabled={isLiking}
                onClick={() => onLike(plugin)}
              >
                {isLiking ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <ThumbsUp className="h-4 w-4" />
                )}
                <span className="min-w-[3ch] text-right font-mono tabular-nums">
                  {likeCount >= 1_000 ? `${(likeCount / 1_000).toFixed(1)}k` : likeCount.toString()}
                </span>
              </Button>
              {plugin.installed ? (
                needsUpdate(plugin) ? (
                  <Button
                    size="icon-sm"
                    className="[&_svg]:size-3.5"
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
                    size="icon-sm"
                    className="[&_svg]:size-3.5"
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
                  size="icon-sm"
                  className="[&_svg]:size-3.5"
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
          {/* 标签 */}
          {plugin.manifest?.keywords && plugin.manifest.keywords.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {plugin.manifest.keywords.slice(0, 3).map((keyword) => (
                <Badge key={keyword} variant="outline" data-plugin-keyword="true" className="min-w-0 max-w-full px-1.5 py-0 text-[11px]">
                  <span className="truncate">{keyword}</span>
                </Badge>
              ))}
              {plugin.manifest.keywords.length > 3 && (
                <Badge variant="outline" data-plugin-keyword="true" className="px-1.5 py-0 text-[11px]">
                  +{plugin.manifest.keywords.length - 3}
                </Badge>
              )}
            </div>
          )}

          {/* 安装/卸载/更新进度与操作区共用内边距，窄屏下也保持对齐。 */}
          {loadProgress &&
            (loadProgress.stage === 'loading' || loadProgress.stage === 'success' || loadProgress.stage === 'error') &&
            loadProgress.operation !== 'fetch' &&
            loadProgress.plugin_id === plugin.id && (
            <div data-plugin-card-progress={loadProgress.stage} className={`space-y-2 rounded-lg border p-2.5 ${
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
          )}
        </div>
      </CardContent>
    </Card>
  )
}, (previous, next) => {
  // 用户点赞覆盖层可能重建统计对象；比较当前卡片的统计字段，避免无关卡片重画。
  // 其余 props（包括操作回调）全部参与比较，不能忽略有效更新。
  const keys = Object.keys(previous) as (keyof PluginCardProps)[]
  return keys.length === Object.keys(next).length && keys.every((key) => {
    if (key !== 'stats') return Object.is(previous[key], next[key])
    if (previous.stats === next.stats) return true
    if (!previous.stats || !next.stats) return false
    const statsKeys = Object.keys(previous.stats) as (keyof PluginStatsData)[]
    return statsKeys.length === Object.keys(next.stats).length
      && statsKeys.every((field) => Object.is(previous.stats![field], next.stats![field]))
  })
})
