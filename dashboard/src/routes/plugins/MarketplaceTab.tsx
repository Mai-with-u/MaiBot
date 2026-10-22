import { memo, useMemo, useState } from 'react'

import type {
  GitStatus,
  MaimaiVersion,
  MarketplaceSortKey,
  PluginInfo,
  PluginProgressById,
  PluginStatsData,
} from './types'
import { PluginCard } from './PluginCard'

const SURPRISE_PLUGIN_COUNT = 4
const SURPRISE_CANDIDATE_LIMIT = 20
const SURPRISE_SEED_STORAGE_KEY = 'plugins-market-surprise-seed'
const FRESHNESS_BOOST_WEIGHT = 4
const FRESHNESS_BOOST_WINDOW_DAYS = 120
const LAUNCH_BOOST_WEIGHT = 12
const LAUNCH_BOOST_FULL_HOURS = 24
const LAUNCH_BOOST_DECAY_HOURS = 48
const UPDATE_BOOST_WEIGHT = 3
const UPDATE_BOOST_WINDOW_DAYS = 14
const GROWTH_BOOST_RANK_LIMIT = 8
const GROWTH_BOOST_WEIGHT_7D = 1
const GROWTH_BOOST_WEIGHT_30D = 0.6
const MS_PER_DAY = 24 * 60 * 60 * 1000
const MS_PER_HOUR = 60 * 60 * 1000

interface MarketplaceScoreBasis {
  maxDownloadScore: number
  maxLikeScore: number
  maxRatingScore: number
  maxMarketplaceOrder: number
}

interface MarketplaceTabProps {
  plugins: PluginInfo[]
  sortBy: MarketplaceSortKey
  gitStatus: GitStatus | null
  maimaiVersion: MaimaiVersion | null
  pluginStats: Record<string, PluginStatsData>
  /** 排序快照不随本次会话内的点赞变化，避免卡片在点击后当场换位。 */
  sortPluginStats: Record<string, PluginStatsData>
  pluginProgressById: PluginProgressById
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

function getPluginIdentity(plugin: PluginInfo): string {
  return plugin.manifest?.id || plugin.id || plugin.marketplace_id || plugin.manifest?.name
}

function parsePluginTime(value: string | undefined): number {
  if (!value) {
    return 0
  }

  const time = Date.parse(value)
  return Number.isNaN(time) ? 0 : time
}

// 插件的上架时间：市场条目自带发布时间时用它，否则取版本索引里最早那个版本的发布时间。
function getPublishedTime(plugin: PluginInfo): number {
  const publishedTime = parsePluginTime(plugin.published_at)
  if (publishedTime > 0) {
    return publishedTime
  }

  const releaseTimes = (plugin.releases?.versions ?? [])
    .map((release) => parsePluginTime(release.published_at))
    .filter((time) => time > 0)
  return releaseTimes.length > 0 ? Math.min(...releaseTimes) : 0
}

function getPluginFreshness(plugin: PluginInfo): number {
  const publishedTime = getPublishedTime(plugin)
  if (publishedTime > 0) {
    return publishedTime
  }

  const updatedTime = parsePluginTime(plugin.updated_at)
  if (updatedTime > 0) {
    return updatedTime
  }

  return plugin.marketplace_order ?? 0
}

function getFreshnessBoost(plugin: PluginInfo, maxMarketplaceOrder: number, now: number): number {
  const publishedTime = getPublishedTime(plugin)
  const updatedTime = parsePluginTime(plugin.updated_at)
  const pluginTime = publishedTime > 0 ? publishedTime : updatedTime

  if (pluginTime > 0) {
    const ageDays = Math.max(0, (now - pluginTime) / MS_PER_DAY)
    if (ageDays >= FRESHNESS_BOOST_WINDOW_DAYS) {
      return 0
    }

    return (1 - ageDays / FRESHNESS_BOOST_WINDOW_DAYS) * FRESHNESS_BOOST_WEIGHT
  }

  if (maxMarketplaceOrder <= 0) {
    return 0
  }

  return ((plugin.marketplace_order ?? 0) / maxMarketplaceOrder) * FRESHNESS_BOOST_WEIGHT
}

function getLaunchBoost(plugin: PluginInfo, now: number): number {
  const publishedTime = getPublishedTime(plugin)
  const updatedTime = parsePluginTime(plugin.updated_at)
  const pluginTime = publishedTime > 0 ? publishedTime : updatedTime

  if (pluginTime <= 0) {
    return 0
  }

  const ageHours = Math.max(0, (now - pluginTime) / MS_PER_HOUR)
  if (ageHours <= LAUNCH_BOOST_FULL_HOURS) {
    return LAUNCH_BOOST_WEIGHT
  }
  if (ageHours >= LAUNCH_BOOST_DECAY_HOURS) {
    return 0
  }

  const decayProgress =
    (ageHours - LAUNCH_BOOST_FULL_HOURS) / (LAUNCH_BOOST_DECAY_HOURS - LAUNCH_BOOST_FULL_HOURS)
  return (1 - decayProgress) * LAUNCH_BOOST_WEIGHT
}

// 更新加成：发布过新版本的插件，在新版本发布后的一段时间内小幅靠前。
// 「有没有更新」以版本索引为准：至少有两个正式版本（不含预发布和已撤回），
// 才把最新那个版本的发布时间算作一次更新；只有一个版本说明是首次收录，不算更新。
function getLatestUpdateTime(plugin: PluginInfo): number {
  const releaseTimes = (plugin.releases?.versions ?? [])
    .filter((release) => !release.prerelease && !release.yanked)
    .map((release) => parsePluginTime(release.published_at))
    .filter((time) => time > 0)

  if (releaseTimes.length >= 2) {
    return Math.max(...releaseTimes)
  }
  if (releaseTimes.length === 1) {
    return 0
  }

  // 没有版本索引时退回市场条目自带的时间：发布后 48 小时内的改动仍算上新期；
  // 没有发布时间的条目已经按更新时间计入上新和新鲜度加成，不再叠加。
  const publishedTime = parsePluginTime(plugin.published_at)
  const updatedTime = parsePluginTime(plugin.updated_at)
  if (publishedTime <= 0 || updatedTime - publishedTime < LAUNCH_BOOST_DECAY_HOURS * MS_PER_HOUR) {
    return 0
  }
  return updatedTime
}

function getUpdateBoost(plugin: PluginInfo, now: number): number {
  const updateTime = getLatestUpdateTime(plugin)
  if (updateTime <= 0) {
    return 0
  }

  const ageDays = Math.max(0, (now - updateTime) / MS_PER_DAY)
  if (ageDays >= UPDATE_BOOST_WINDOW_DAYS) {
    return 0
  }

  return (1 - ageDays / UPDATE_BOOST_WINDOW_DAYS) * UPDATE_BOOST_WEIGHT
}

function normalizeScore(value: number, maxValue: number, weight: number): number {
  if (maxValue <= 0) {
    return 0
  }

  return (value / maxValue) * weight
}

function getGrowthBoost(stats: PluginStatsData | undefined): number {
  if (!stats) return 0

  const rankings = [
    { rank: stats.downloads_growth_rank_7d, weight: GROWTH_BOOST_WEIGHT_7D },
    { rank: stats.likes_growth_rank_7d, weight: GROWTH_BOOST_WEIGHT_7D },
    { rank: stats.downloads_growth_rank_30d, weight: GROWTH_BOOST_WEIGHT_30D },
    { rank: stats.likes_growth_rank_30d, weight: GROWTH_BOOST_WEIGHT_30D },
  ]
  // 仅为前八名小幅加权，取最强的一项，避免多个榜单的加成叠加。
  return rankings.reduce((boost, { rank, weight }) => {
    if (rank == null || !Number.isInteger(rank) || rank < 1 || rank > GROWTH_BOOST_RANK_LIMIT) {
      return boost
    }
    return Math.max(boost, ((GROWTH_BOOST_RANK_LIMIT + 1 - rank) / GROWTH_BOOST_RANK_LIMIT) * weight)
  }, 0)
}

function getStableRandomRank(seed: string, plugin: PluginInfo): number {
  const value = `${seed}:${getPluginIdentity(plugin)}`
  let hash = 2166136261

  for (let i = 0; i < value.length; i++) {
    hash ^= value.charCodeAt(i)
    hash = Math.imul(hash, 16777619)
  }

  return hash >>> 0
}

function selectSurprisePlugins(
  plugins: PluginInfo[],
  sortBy: MarketplaceSortKey,
  seed: string
): PluginInfo[] {
  if (sortBy !== 'default' || plugins.length <= SURPRISE_PLUGIN_COUNT) {
    return []
  }

  const candidateCount = Math.min(
    SURPRISE_CANDIDATE_LIMIT,
    Math.max(SURPRISE_PLUGIN_COUNT, Math.ceil(plugins.length * 0.3))
  )

  return [...plugins]
    .sort((left, right) => {
      const freshnessDiff = getPluginFreshness(right) - getPluginFreshness(left)
      if (freshnessDiff !== 0) {
        return freshnessDiff
      }

      return (right.marketplace_order ?? 0) - (left.marketplace_order ?? 0)
    })
    .slice(0, candidateCount)
    .sort((left, right) => getStableRandomRank(seed, left) - getStableRandomRank(seed, right))
    .slice(0, SURPRISE_PLUGIN_COUNT)
}

export const MarketplaceTab = memo(function MarketplaceTab({
  plugins,
  sortBy,
  gitStatus,
  maimaiVersion,
  pluginStats,
  sortPluginStats,
  pluginProgressById,
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
}: MarketplaceTabProps) {
  const [surpriseSeed] = useState(() => {
    // 同一浏览器会话沿用抽选种子，返回市场或重挂载时不重新抽选推荐位。
    const savedSeed = sessionStorage.getItem(SURPRISE_SEED_STORAGE_KEY)
    if (savedSeed) return savedSeed
    const seed = Math.random().toString(36).slice(2)
    sessionStorage.setItem(SURPRISE_SEED_STORAGE_KEY, seed)
    return seed
  })
  const [renderTime] = useState(() => Date.now())

  // 排序只依赖插件、排序快照和筛选结果，不随点赞、进度或列数变化重算。
  const displayPlugins = useMemo(() => {
    const getPluginStats = (plugin: PluginInfo): PluginStatsData | undefined => {
      const statsIds = [plugin.manifest?.id, plugin.id].filter((id): id is string => Boolean(id))

      return statsIds.map((id) => sortPluginStats[id]).find(Boolean)
    }

    const getSortValue = (
      plugin: PluginInfo,
      scoreBasis: MarketplaceScoreBasis,
      now: number
    ): number => {
      const stats = getPluginStats(plugin)

      if (sortBy === 'default') {
        const downloads = stats?.downloads ?? plugin.downloads ?? 0
        const likes = stats?.likes ?? 0
        const rating = stats?.rating ?? plugin.rating ?? 0
        const ratingCount = stats?.rating_count ?? 0
        const downloadScore = Math.log10(downloads + 1)
        const likeScore = Math.log10(likes + 1)
        const ratingScore = rating * Math.log10(ratingCount + 2)

        return (
          normalizeScore(downloadScore, scoreBasis.maxDownloadScore, 4) +
          normalizeScore(likeScore, scoreBasis.maxLikeScore, 3) +
          normalizeScore(ratingScore, scoreBasis.maxRatingScore, 2) +
          getLaunchBoost(plugin, now) +
          getUpdateBoost(plugin, now) +
          getGrowthBoost(stats) +
          getFreshnessBoost(plugin, scoreBasis.maxMarketplaceOrder, now)
        )
      }
      if (sortBy === 'latest') {
        return getPluginFreshness(plugin)
      }
      if (sortBy === 'downloads') {
        return stats?.downloads ?? plugin.downloads ?? 0
      }
      if (sortBy === 'likes') {
        return stats?.likes ?? 0
      }
      if (sortBy === 'rating') {
        return stats?.rating ?? plugin.rating ?? 0
      }

      return 0
    }

    const scoreBasis: MarketplaceScoreBasis = {
      maxDownloadScore: 0,
      maxLikeScore: 0,
      maxRatingScore: 0,
      maxMarketplaceOrder: 0,
    }
    // 下载量、点赞量等直接排序不需要计算综合评分的归一化基准。
    if (sortBy === 'default') {
      for (const plugin of plugins) {
        const stats = getPluginStats(plugin)
        const downloadScore = Math.log10((stats?.downloads ?? plugin.downloads ?? 0) + 1)
        const likeScore = Math.log10((stats?.likes ?? 0) + 1)
        const ratingScore = (stats?.rating ?? plugin.rating ?? 0) * Math.log10((stats?.rating_count ?? 0) + 2)
        scoreBasis.maxDownloadScore = Math.max(scoreBasis.maxDownloadScore, downloadScore)
        scoreBasis.maxLikeScore = Math.max(scoreBasis.maxLikeScore, likeScore)
        scoreBasis.maxRatingScore = Math.max(scoreBasis.maxRatingScore, ratingScore)
        scoreBasis.maxMarketplaceOrder = Math.max(scoreBasis.maxMarketplaceOrder, plugin.marketplace_order ?? 0)
      }
    }
    // 每个插件只计算一次分数和时间，排序比较时不重复解析版本日期。
    const sortedPlugins = plugins.map((plugin) => ({
      plugin,
      score: getSortValue(plugin, scoreBasis, renderTime),
      freshness: getPluginFreshness(plugin),
    })).sort((left, right) =>
      right.score - left.score || right.freshness - left.freshness
      || (left.plugin.manifest.name || left.plugin.id).localeCompare(right.plugin.manifest.name || right.plugin.id)
    ).map(({ plugin }) => plugin)

    const surprisePlugins = selectSurprisePlugins(sortedPlugins, sortBy, surpriseSeed)
    const surprisePluginIds = new Set(surprisePlugins.map(getPluginIdentity))
    const mainPlugins = sortedPlugins.filter(
      (plugin) => !surprisePluginIds.has(getPluginIdentity(plugin))
    )
    return [...surprisePlugins, ...mainPlugins]
  }, [plugins, sortBy, sortPluginStats, renderTime, surpriseSeed])
  const isAnyPluginInstalling = Object.values(pluginProgressById).some(
    (progress) => progress.operation === 'install' && progress.stage === 'loading'
  )

  const renderPluginCard = (plugin: PluginInfo) => (
    <PluginCard
      key={plugin.id}
      plugin={plugin}
      gitStatus={gitStatus}
      maimaiVersion={maimaiVersion}
      stats={plugin.manifest?.id ? pluginStats[plugin.manifest.id] : undefined}
      loadProgress={pluginProgressById[plugin.id] ?? null}
      isAnyPluginInstalling={isAnyPluginInstalling}
      isLiking={likingPluginIds.has(plugin.manifest?.id || plugin.id)}
      onInstall={onInstall}
      onLike={onLike}
      onUpdate={onUpdate}
      onUninstall={onUninstall}
      onDetail={onDetail}
      checkPluginCompatibility={checkPluginCompatibility}
      needsUpdate={needsUpdate}
      getStatusBadge={getStatusBadge}
      getIncompatibleReason={getIncompatibleReason}
    />
  )

  return (
    // 卡片始终属于同一个网格，换列、筛选或排序只移动节点，不再按行拆卸重建。
    // CSS 按容器宽度排布：最小 300px、最多四列；两条 subgrid 轨道保持操作区对齐。
    <div
      data-plugin-market-grid="true"
      className="grid gap-x-6 gap-y-8"
      style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, max(300px, (100% - 72px) / 4)), 1fr))' }}
    >
      {displayPlugins.map((plugin) => (
        <div
          key={plugin.id}
          className="-mx-2 -my-4 row-span-2 grid grid-rows-subgrid gap-y-0 px-2 py-4"
          // 屏幕外卡片跳过布局和绘制；预留空间让跨边框的榜单标签完整显示。
          style={{ contentVisibility: 'auto', containIntrinsicSize: 'auto 200px' }}
        >
          {renderPluginCard(plugin)}
        </div>
      ))}
    </div>
  )
})
