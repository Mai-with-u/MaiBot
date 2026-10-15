import { memo, useEffect, useState } from 'react'

import type {
  GitStatus,
  MaimaiVersion,
  MarketplaceSortKey,
  PluginInfo,
  PluginProgressById,
  PluginStatsData,
} from './types'
import { getPluginType } from './types'
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
  searchQuery: string
  pluginTypeFilter: string
  showCompatibleOnly: boolean
  sortBy: MarketplaceSortKey
  gitStatus: GitStatus | null
  maimaiVersion: MaimaiVersion | null
  pluginStats: Record<string, PluginStatsData>
  /** 排序专用的统计快照：不随本次会话内的点赞变化，避免卡片在点击后当场换位；缺省时与 pluginStats 相同 */
  sortPluginStats?: Record<string, PluginStatsData>
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

function getMarketplaceColumnCount(): number {
  if (window.matchMedia('(min-width: 1280px)').matches) return 4
  if (window.matchMedia('(min-width: 768px)').matches) return 3
  if (window.matchMedia('(min-width: 640px)').matches) return 2
  return 1
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
  searchQuery,
  pluginTypeFilter,
  showCompatibleOnly,
  sortBy,
  gitStatus,
  maimaiVersion,
  pluginStats,
  sortPluginStats = pluginStats,
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
  const [columnCount, setColumnCount] = useState(getMarketplaceColumnCount)
  useEffect(() => {
    const queries = [640, 768, 1280].map((width) => window.matchMedia(`(min-width: ${width}px)`))
    const updateColumns = () => setColumnCount(getMarketplaceColumnCount())
    queries.forEach((query) => query.addEventListener('change', updateColumns))
    return () => queries.forEach((query) => query.removeEventListener('change', updateColumns))
  }, [])
  const [surpriseSeed] = useState(() => {
    // 同一浏览器会话沿用抽选种子，返回市场或重挂载时不重新抽选推荐位。
    const savedSeed = sessionStorage.getItem(SURPRISE_SEED_STORAGE_KEY)
    if (savedSeed) return savedSeed
    const seed = Math.random().toString(36).slice(2)
    sessionStorage.setItem(SURPRISE_SEED_STORAGE_KEY, seed)
    return seed
  })
  const [renderTime] = useState(() => Date.now())

  // 过滤插件
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

  const matchedPlugins = plugins.filter((plugin) => {
    // 跳过没有 manifest 的插件
    if (!plugin.manifest) {
      console.warn('[过滤] 跳过无 manifest 的插件:', plugin.id)
      return false
    }

    // 全部插件只展示 plugin-repo 中存在的市场插件，本地独有插件只在“已安装”显示。
    if (plugin.source === 'local') {
      return false
    }

    // 搜索过滤
    const matchesSearch =
      searchQuery === '' ||
      plugin.manifest.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      plugin.manifest.author?.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      plugin.manifest.description?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (plugin.manifest.keywords &&
        plugin.manifest.keywords.some((k) => k.toLowerCase().includes(searchQuery.toLowerCase())))

    // 类型过滤
    const matchesType = pluginTypeFilter === 'all' || getPluginType(plugin) === pluginTypeFilter

    // 兼容性过滤
    const matchesCompatibility =
      !showCompatibleOnly || !maimaiVersion || checkPluginCompatibility(plugin)

    return matchesSearch && matchesType && matchesCompatibility
  })
  const scoreBasis = matchedPlugins.reduce<MarketplaceScoreBasis>(
    (basis, plugin) => {
      const stats = getPluginStats(plugin)
      const downloads = stats?.downloads ?? plugin.downloads ?? 0
      const likes = stats?.likes ?? 0
      const rating = stats?.rating ?? plugin.rating ?? 0
      const ratingCount = stats?.rating_count ?? 0

      return {
        maxDownloadScore: Math.max(basis.maxDownloadScore, Math.log10(downloads + 1)),
        maxLikeScore: Math.max(basis.maxLikeScore, Math.log10(likes + 1)),
        maxRatingScore: Math.max(basis.maxRatingScore, rating * Math.log10(ratingCount + 2)),
        maxMarketplaceOrder: Math.max(basis.maxMarketplaceOrder, plugin.marketplace_order ?? 0),
      }
    },
    {
      maxDownloadScore: 0,
      maxLikeScore: 0,
      maxRatingScore: 0,
      maxMarketplaceOrder: 0,
    }
  )
  const now = renderTime
  const filteredPlugins = matchedPlugins.sort((left, right) => {
    const valueDiff = getSortValue(right, scoreBasis, now) - getSortValue(left, scoreBasis, now)
    if (valueDiff !== 0) {
      return valueDiff
    }

    const freshnessDiff = getPluginFreshness(right) - getPluginFreshness(left)
    if (freshnessDiff !== 0) {
      return freshnessDiff
    }

    return (left.manifest?.name || left.id).localeCompare(right.manifest?.name || right.id)
  })

  const surprisePlugins = selectSurprisePlugins(filteredPlugins, sortBy, surpriseSeed)
  const surprisePluginIds = new Set(surprisePlugins.map(getPluginIdentity))
  const mainPlugins = filteredPlugins.filter(
    (plugin) => !surprisePluginIds.has(getPluginIdentity(plugin))
  )
  const displayPlugins = [...surprisePlugins, ...mainPlugins]
  const pluginRows: PluginInfo[][] = []
  for (let index = 0; index < displayPlugins.length; index += columnCount) {
    pluginRows.push(displayPlugins.slice(index, index + columnCount))
  }
  const isAnyPluginInstalling = Object.values(pluginProgressById).some(
    (progress) => progress.operation === 'install' && progress.stage === 'loading'
  )

  const renderPluginCard = (plugin: PluginInfo) => (
    <PluginCard
      key={plugin.id}
      plugin={plugin}
      gitStatus={gitStatus}
      maimaiVersion={maimaiVersion}
      pluginStats={pluginStats}
      loadProgress={pluginProgressById[plugin.id] ?? null}
      isAnyPluginInstalling={isAnyPluginInstalling}
      likingPluginIds={likingPluginIds}
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
    // 各行内部共享网格轨道；屏幕外行交给浏览器跳过布局和绘制，保留 DOM 与滚动状态。
    <div className="flex flex-col gap-3">
      {pluginRows.map((row) => (
        <div
          key={getPluginIdentity(row[0])}
          className="-m-2 grid grid-cols-1 gap-3 p-2 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4"
          style={{ contentVisibility: 'auto', containIntrinsicSize: 'auto 200px' }}
        >
          {row.map(renderPluginCard)}
        </div>
      ))}
    </div>
  )
})
