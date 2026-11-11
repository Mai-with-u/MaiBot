/**
 * 麦麦观察首页的卡片排法：上排放不随时间变化的卡片，刻线以下是跟着统计周期走的卡片。
 * 用户在编辑态里调整的顺序、宽度和增删都存到本地，下次打开保持。
 */

export const HOME_CARD_IDS = [
  'status',
  'quote',
  'keys',
  'disk',
  'news',
  'usage',
  'cache',
  'models',
  'requests',
  'tokens',
  'pet',
] as const
export type HomeCardId = (typeof HOME_CARD_IDS)[number]
export type HomeZone = 'top' | 'stats'
export type HomeCardSpan = 3 | 4 | 6 | 12

export interface HomeCardSlot {
  id: HomeCardId
  span: HomeCardSpan
}

export interface HomeLayout {
  top: HomeCardSlot[]
  stats: HomeCardSlot[]
  /** 用户在编辑态里移除过桌宠卡片：之后开启桌宠也不再自动放回，可从卡片库手动添加 */
  petDismissed?: boolean
}

/** 每张卡片属于哪一段：统计类卡片只能放在刻线以下，跟着周期拨杆走 */
export const HOME_CARD_ZONE: Record<HomeCardId, HomeZone> = {
  status: 'top',
  quote: 'top',
  keys: 'top',
  disk: 'top',
  news: 'top',
  usage: 'stats',
  cache: 'stats',
  models: 'stats',
  requests: 'stats',
  tokens: 'stats',
  // 桌宠卡片和两张趋势图排在同一行，归到刻线以下
  pet: 'stats',
}

/** 图表卡片所在的那一行吃掉面板剩余高度 */
export const HOME_TALL_CARDS = new Set<HomeCardId>(['requests', 'tokens', 'pet'])

export const HOME_CARD_SPANS: HomeCardSpan[] = [3, 4, 6, 12]

// 默认沿用原来的排布：右上角是资讯；一言不默认展示，从卡片库添加
export const DEFAULT_HOME_LAYOUT: HomeLayout = {
  top: [
    { id: 'status', span: 3 },
    { id: 'keys', span: 3 },
    { id: 'disk', span: 3 },
    { id: 'news', span: 3 },
  ],
  stats: [
    { id: 'usage', span: 6 },
    { id: 'cache', span: 3 },
    { id: 'models', span: 3 },
    { id: 'requests', span: 6 },
    { id: 'tokens', span: 6 },
  ],
}

const STORAGE_KEY = 'maibot-observation-home-layout'

function cloneLayout(layout: HomeLayout): HomeLayout {
  return {
    top: layout.top.map((slot) => ({ ...slot })),
    stats: layout.stats.map((slot) => ({ ...slot })),
    ...(layout.petDismissed ? { petDismissed: true } : {}),
  }
}

function isCardId(value: unknown): value is HomeCardId {
  return typeof value === 'string' && (HOME_CARD_IDS as readonly string[]).includes(value)
}

function isSpan(value: unknown): value is HomeCardSpan {
  return value === 3 || value === 4 || value === 6 || value === 12
}

/** 读本地保存的排法；格式不对、卡片放错段或重复时丢掉对应项，整体读不出来就用默认排法 */
export function loadHomeLayout(): HomeLayout {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return cloneLayout(DEFAULT_HOME_LAYOUT)
    const parsed = JSON.parse(raw) as Partial<Record<HomeZone, unknown>> & { petDismissed?: unknown }
    const seen = new Set<HomeCardId>()
    const pick = (zone: HomeZone): HomeCardSlot[] => {
      const list = Array.isArray(parsed[zone]) ? (parsed[zone] as unknown[]) : []
      const slots: HomeCardSlot[] = []
      for (const item of list) {
        const { id, span } = (item ?? {}) as { id?: unknown; span?: unknown }
        if (!isCardId(id) || HOME_CARD_ZONE[id] !== zone || seen.has(id)) continue
        seen.add(id)
        slots.push({ id, span: isSpan(span) ? span : 3 })
      }
      return slots
    }
    return {
      top: pick('top'),
      stats: pick('stats'),
      ...(parsed.petDismissed === true ? { petDismissed: true } : {}),
    }
  } catch {
    return cloneLayout(DEFAULT_HOME_LAYOUT)
  }
}

export function saveHomeLayout(layout: HomeLayout): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(layout))
  } catch {
    // 存储不可用时只影响下次打开时的排法
  }
}

export function resetHomeLayout(): HomeLayout {
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    // 同上
  }
  return cloneLayout(DEFAULT_HOME_LAYOUT)
}

/** 不在面板上的卡片，给卡片库用；没开桌宠时卡片库里也不提供桌宠卡片 */
export function missingHomeCards(layout: HomeLayout, petEnabled = false): HomeCardId[] {
  const present = new Set([...layout.top, ...layout.stats].map((slot) => slot.id))
  return HOME_CARD_IDS.filter((id) => !present.has(id) && (petEnabled || id !== 'pet'))
}

/**
 * 开启桌宠后第一次进首页，把桌宠卡片放进图表行：两张趋势图还是默认的各占一半时，
 * 三张卡片各占三分之一；用户改过排法时追加在统计段末尾。已经放过或用户移除过就不动。
 */
export function insertDesktopPetCard(layout: HomeLayout): HomeLayout {
  if (layout.petDismissed || layout.stats.some((slot) => slot.id === 'pet')) return layout
  const stats = layout.stats.map((slot) => ({ ...slot }))
  const requests = stats.findIndex((slot) => slot.id === 'requests')
  const tokens = stats.findIndex((slot) => slot.id === 'tokens')
  if (
    requests >= 0 &&
    tokens >= 0 &&
    Math.abs(requests - tokens) === 1 &&
    stats[requests].span === 6 &&
    stats[tokens].span === 6
  ) {
    stats[requests].span = 4
    stats[tokens].span = 4
    stats.splice(Math.max(requests, tokens) + 1, 0, { id: 'pet', span: 4 })
  } else {
    stats.push({ id: 'pet', span: 3 })
  }
  return { ...layout, stats }
}

/**
 * 实际摆在面板上的统计段：桌宠关闭时去掉桌宠卡片，
 * 自动排成三分之一的两张趋势图恢复各占一半，不留空位。
 */
export function visibleStatsSlots(layout: HomeLayout, petEnabled: boolean): HomeCardSlot[] {
  const pet = layout.stats.find((slot) => slot.id === 'pet')
  if (petEnabled || !pet) return layout.stats
  const autoThirds =
    pet.span === 4 &&
    layout.stats.filter((slot) => (slot.id === 'requests' || slot.id === 'tokens') && slot.span === 4)
      .length === 2
  return layout.stats
    .filter((slot) => slot.id !== 'pet')
    .map((slot) =>
      autoThirds && (slot.id === 'requests' || slot.id === 'tokens') ? { ...slot, span: 6 } : slot
    )
}

/**
 * 按 12 列从左到右装行，算出 grid-template-rows：普通行用 rowHeight 作最小高度并按内容撑开，
 * 有图表的行吃掉剩余高度。行高本身用 CSS 变量，宽 / 中尺寸各自给值。
 */
export function homeGridRows(slots: HomeCardSlot[], rowHeight: string, tallHeight: string): string {
  const rows: boolean[] = []
  let used = 12
  for (const slot of slots) {
    if (used + slot.span > 12) {
      rows.push(false)
      used = 0
    }
    used += slot.span
    if (HOME_TALL_CARDS.has(slot.id)) rows[rows.length - 1] = true
  }
  return rows
    .map((tall) => (tall ? `minmax(${tallHeight}, 1fr)` : `minmax(${rowHeight}, auto)`))
    .join(' ')
}
