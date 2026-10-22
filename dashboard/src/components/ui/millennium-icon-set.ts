/**
 * 千禧风格的手绘图标，全部登记在这一份表里。
 *
 * 默认画法：24 网格、方头直角、2.5 线宽的线条；个别像素风图标用 filled 改成实心。
 * 一个图标可以同时顶替两类来源：
 * - streamline：侧栏、菜单按名称取用的图标，由 MillenniumIcon 组件直接画出来；
 * - lucide：散落在各页面里的图标库图标，按类名用样式遮罩整体替换，不用改调用处。
 * 没登记的图标保持原样。
 */

export interface MillenniumIconDef {
  /** SVG 路径，24x24 网格 */
  path: string
  /** 顶替的侧栏/菜单图标名称 */
  streamline?: string[]
  /** 顶替的图标库图标名称（不带 lucide- 前缀） */
  lucide?: string[]
  /** 实心画法，不描边 */
  filled?: boolean
  /** 实心图形里需要镂空时使用奇偶填充 */
  evenOdd?: boolean
  /** 线宽，默认 2.5 */
  strokeWidth?: number
}

export const MILLENNIUM_ICON_STROKE_WIDTH = 2.5

export const MILLENNIUM_ICON_SET: MillenniumIconDef[] = [
  {
    streamline: ['desktop-chat-remix'],
    path: 'M3 4h18v12H3zM9 20h6M12 16v4M7 8h6M7 12h10',
  },
  {
    streamline: ['page-setting-remix', 'horizontal-slider-2-solid'],
    path: 'M4 7h10M18 7h2M4 17h4M12 17h8M14 4v6M8 14v6',
  },
  {
    streamline: ['module-remix'],
    path: 'M12 3l8 4v10l-8 4-8-4V7zM4 7l8 4 8-4M12 11v10',
  },
  {
    streamline: ['script-1-remix'],
    path: 'M6 3h9l4 4v14H6zM15 3v4h4M9 12h7M9 16h7',
  },
  {
    streamline: ['chat-bubble-square-write-remix'],
    path: 'M4 4h11M4 4v16h16V9M9 15l10-10 2 2-10 10H9z',
  },
  {
    streamline: ['sign-hashtag-solid'],
    path: 'M9 3L7 21M17 3l-2 18M4 9h17M3 15h17',
  },
  {
    streamline: ['user-sticker-square-remix'],
    path: 'M4 4h16v6H4zM4 10h16v10H4zM8 7h1M8 14h8M8 17h5',
  },
  {
    streamline: ['application-add-remix'],
    path: 'M4 4h7v7H4zM4 13h7v7H4zM13 13h7v7h-7M16.5 4v7M13 7.5h7',
  },
  {
    streamline: ['router-wifi-network-solid'],
    path: 'M3 14h18v6H3zM7 17h1M11 17h1M17 14V8M13 7l4-4 4 4',
  },
  {
    streamline: ['store-2-solid'],
    path: 'M3 9l2-5h14l2 5v3H3zM5 12v8h14v-8M10 20v-5h4v5',
  },
  {
    streamline: ['file-bookmark-solid'],
    path: 'M6 3h9l4 4v14H6zM15 3v4h4M9 11h4v6l-2-2-2 2z',
  },
  {
    streamline: ['delete-2-solid'],
    path: 'M4 7h16M9 7V4h6v3M6 7v13h12V7M10 11v6M14 11v6',
  },
  {
    streamline: ['line-arrow-right-1-remix'],
    path: 'M9 5l7 7-7 7',
  },
  {
    lucide: ['settings', 'settings-2', 'cog', 'wrench'],
    filled: true,
    evenOdd: true,
    path: 'M14 2h4v2h-2v2h-2v2h2v2h2V8h2V6h2v6h-2v2h-6v2h-2v2h-2v2H8v2H2v-6h2v-2h2v-2h2v-2h2V4h2V2zM4 18v2h2v-2z',
  },
  {
    lucide: ['book-open'],
    strokeWidth: 2,
    path: 'M12 6h-2V4H3v14h7v2h4v-2h7V4h-7v2zM12 6v14M6 8h3M6 12h3M15 8h3M15 12h3',
  },
  {
    lucide: ['log-out'],
    filled: true,
    path: 'M2 2h8v2H4v16h6v2H2zM8 10h10V8h-2V6h-2V4h2v2h2v2h2v2h2v4h-2v2h-2v2h-2v2h-2v-2h2v-2h2v-2H8z',
  },
  {
    streamline: ['search-bar-solid'],
    lucide: ['search'],
    path: 'M4 4h11v11H4zM15 15l6 6',
  },
  {
    lucide: ['globe', 'earth'],
    path: 'M12 3a9 9 0 1 0 0 18 9 9 0 1 0 0-18zM3 12h18M12 3c-5 5-5 13 0 18M12 3c5 5 5 13 0 18',
    strokeWidth: 2,
  },
  {
    lucide: ['languages'],
    path: 'M12 3v4M4 7h16M16 8L5 20M8 11l11 9',
  },
  {
    lucide: ['moon'],
    path: 'M20 14A8 8 0 1 1 10 4a6 6 0 0 0 10 10z',
  },
  {
    lucide: ['sun'],
    path: 'M9 9h6v6H9zM12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2',
  },
  {
    lucide: ['circle-alert', 'alert-circle'],
    path: 'M4 4h16v16H4zM12 8v5M12 16v1',
  },
  {
    streamline: ['information-circle-solid'],
    lucide: ['info'],
    path: 'M4 4h16v16H4zM12 11v6M12 7v1',
  },
  {
    lucide: ['circle-check', 'check-circle', 'circle-check-big', 'check-circle-2'],
    path: 'M4 4h16v16H4zM8 12l3 3 5-6',
  },
  {
    lucide: ['circle-x', 'x-circle'],
    path: 'M4 4h16v16H4zM9 9l6 6M15 9l-6 6',
  },
  {
    lucide: ['circle-help', 'help-circle', 'circle-question-mark'],
    path: 'M4 4h16v16H4zM9 9h6v3h-3v2M12 17v1',
  },
  {
    lucide: ['refresh-cw', 'refresh-ccw'],
    path: 'M4 11V6h13M14 3l3 3-3 3M20 13v5H7M10 21l-3-3 3-3',
  },
  {
    lucide: ['rotate-ccw'],
    path: 'M8 4L4 8l4 4M4 8h16v12H6',
  },
  {
    lucide: ['rotate-cw'],
    path: 'M16 4l4 4-4 4M20 8H4v12h14',
  },
  {
    lucide: ['clock', 'timer'],
    path: 'M4 4h16v16H4zM12 8v5h4',
  },
  {
    lucide: ['calendar', 'calendar-days', 'calendar-clock'],
    path: 'M4 5h16v16H4zM8 3v4M16 3v4M4 10h16M8 14h2M14 14h2M8 18h2M14 18h2',
  },
  {
    lucide: ['eye'],
    path: 'M2 12l5-5h10l5 5-5 5H7zM10 10h4v4h-4z',
  },
  {
    lucide: ['eye-off'],
    path: 'M2 12l5-5h10l5 5-5 5H7zM4 3l16 18',
  },
  {
    lucide: ['message-circle', 'message-square'],
    path: 'M4 4h16v12H10l-4 4v-4H4z',
  },
  {
    lucide: ['box'],
    path: 'M12 3 3 8v9l9 5 9-5V8zM3 8l9 5 9-5M12 13v9',
  },
  {
    lucide: ['user', 'circle-user', 'user-circle', 'circle-user-round', 'user-circle-2', 'user-round'],
    path: 'M9 4h6v6H9zM5 20v-6h14v6',
  },
  {
    lucide: ['users', 'users-round'],
    path: 'M6 5h5v5H6zM3 20v-6h11v6M16 6h3v4h-3M17 14h4v6',
  },
  {
    lucide: ['bot'],
    path: 'M5 9h14v10H5zM12 9V5M10 5h4M9 13v2M15 13v2M2 13v3M22 13v3',
  },
  {
    lucide: ['brain', 'cpu'],
    path: 'M7 7h10v10H7zM10 10h4v4h-4zM9 3v4M15 3v4M9 17v4M15 17v4M3 9h4M3 15h4M17 9h4M17 15h4',
  },
  {
    streamline: ['happy-face-remix'],
    lucide: ['smile'],
    path: 'M4 4h16v16H4zM9 9v2M15 9v2M8 15h8',
  },
  {
    streamline: ['edit-pdf-solid'],
    lucide: ['pencil', 'pen', 'pen-line', 'square-pen', 'edit', 'edit-2', 'edit-3', 'pencil-line'],
    path: 'M4 20h4L20 8l-4-4L4 16z',
  },
  {
    lucide: ['database'],
    path: 'M5 4h14v5H5zM5 9v5h14V9M5 14v6h14v-6M8 6.5h1M8 11.5h1M8 17h1',
  },
  {
    lucide: ['ellipsis', 'more-horizontal'],
    path: 'M5 12h1M12 12h1M19 12h1',
  },
  {
    lucide: ['circle-plus', 'plus-circle'],
    path: 'M4 4h16v16H4zM12 8v8M8 12h8',
  },
  {
    lucide: ['power'],
    path: 'M12 3v9M7 6H5v14h14V6h-2',
  },
  {
    lucide: ['gauge'],
    path: 'M4 18V8h16v10M12 18l4-6',
  },
  {
    lucide: ['ban'],
    path: 'M4 4h16v16H4zM6 6l12 12',
  },
  {
    lucide: ['history'],
    path: 'M8 3L4 7l4 4M4 7h16v13H5M12 11v4h3',
  },
  {
    lucide: ['palette'],
    path: 'M4 4h16v10h-6v6H4zM8 8h1M12 8h1M16 8h1M8 12h1',
  },
  {
    lucide: ['smile-plus'],
    path: 'M14 4H4v16h16V10M9 10v2M14 10v2M8 15h8M19 2v6M16 5h6',
  },
  {
    streamline: ['cyborg-solid'],
    lucide: ['brain-circuit'],
    path: 'M7 7h10v10H7zM10 10h4v4h-4zM9 3v4M15 3v4M9 17v4M15 17v4M3 9h4M3 15h4M17 9h4M17 15h4',
  },
  {
    lucide: ['camera'],
    path: 'M3 7h4l2-3h6l2 3h4v13H3zM9 11h6v5H9z',
  },
  {
    streamline: ['allergens-fish-remix'],
    lucide: ['house', 'home'],
    path: 'M3 11l9-8 9 8M6 10v10h12V10M10 20v-5h4v5',
  },
]

const STREAMLINE_INDEX = new Map<string, MillenniumIconDef>()
for (const icon of MILLENNIUM_ICON_SET) {
  for (const name of icon.streamline ?? []) STREAMLINE_INDEX.set(name, icon)
}

export function getMillenniumIcon(name: string): MillenniumIconDef | undefined {
  return STREAMLINE_INDEX.get(name)
}

function toMaskUrl(icon: MillenniumIconDef): string {
  const paint = icon.filled
    ? "fill='black'"
    : `fill='none' stroke='black' stroke-width='${icon.strokeWidth ?? MILLENNIUM_ICON_STROKE_WIDTH}' stroke-linecap='square' stroke-linejoin='miter'`
  const rule = icon.evenOdd ? " fill-rule='evenodd'" : ''
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' ${paint}><path${rule} d='${icon.path}'/></svg>`
  return `url("data:image/svg+xml,${svg.replace(/</g, '%3C').replace(/>/g, '%3E').replace(/#/g, '%23')}")`
}

/**
 * 生成替换图标库图标的样式：隐藏原来的线条，用当前文字色填充一个按新路径裁出的形状，
 * 颜色、尺寸都跟随原图标。
 */
export function buildMillenniumLucideCss(): string {
  const scope = ":root[data-dashboard-style='millennium'] svg.lucide"
  const replaced = MILLENNIUM_ICON_SET.filter((icon) => icon.lucide?.length)
  const toSelector = (names: string[]) => names.map((name) => `.lucide-${name}`).join(',')
  const all = toSelector(replaced.flatMap((icon) => icon.lucide ?? []))

  return [
    `${scope}:is(${all}){-webkit-mask:var(--mil-icon) center/100% 100% no-repeat;mask:var(--mil-icon) center/100% 100% no-repeat;background-color:currentColor}`,
    `${scope}:is(${all})>*{display:none}`,
    ...replaced.map((icon) => `${scope}:is(${toSelector(icon.lucide ?? [])}){--mil-icon:${toMaskUrl(icon)}}`),
  ].join(' ')
}

const STYLE_ELEMENT_ID = 'millennium-icon-style'

/** 把替换样式挂到页面上；规则只在千禧风格下生效，其他风格不受影响。 */
export function installMillenniumIconStyle(): void {
  if (typeof document === 'undefined' || document.getElementById(STYLE_ELEMENT_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_ELEMENT_ID
  style.textContent = buildMillenniumLucideCss()
  document.head.appendChild(style)
}
