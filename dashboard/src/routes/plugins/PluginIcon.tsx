import { createElement, useMemo, useState, type CSSProperties } from 'react'
import {
  BarChart3,
  BookOpen,
  Bot,
  Cloud,
  Database,
  Gamepad2,
  Image as ImageIcon,
  Package,
  Plug,
  Puzzle,
  Search,
  ScrollText,
  Settings,
  Shield,
  Wrench,
  type LucideIcon,
} from 'lucide-react'

import { cn } from '@/lib/utils'
import type { PluginDisplayIcon, PluginType } from '@/types/plugin'

import { getPluginType } from './types'

interface PluginIconManifest {
  id?: string
  version?: string
  plugin_type?: PluginType | string
  display?: {
    icon?: PluginDisplayIcon
  }
}

interface PluginIconProps {
  pluginId: string
  manifest?: PluginIconManifest
  installed?: boolean
  installedVersion?: string
  marketplaceIconUrl?: string
  className?: string
  iconClassName?: string
}

const LUCIDE_ICONS: Record<string, LucideIcon> = {
  'bar-chart-3': BarChart3,
  bar_chart_3: BarChart3,
  bot: Bot,
  'book-open': BookOpen,
  book_open: BookOpen,
  cloud: Cloud,
  database: Database,
  gamepad2: Gamepad2,
  'gamepad-2': Gamepad2,
  image: ImageIcon,
  package: Package,
  plug: Plug,
  puzzle: Puzzle,
  search: Search,
  'scroll-text': ScrollText,
  scroll_text: ScrollText,
  settings: Settings,
  shield: Shield,
  wrench: Wrench,
}

const DEFAULT_TYPE_ICONS: Record<PluginType, LucideIcon> = {
  adapter: Plug, tool: Wrench, provider: Cloud, management: Shield,
  data: Database, media: ImageIcon, game: Gamepad2, integration: Plug,
  other: Package, chat: Bot, creative: ImageIcon, automation: Settings, deskpet: Bot,
}

function resolveLucideIcon(name: string | undefined): LucideIcon | undefined {
  if (!name) {
    return undefined
  }

  return LUCIDE_ICONS[name.trim().toLowerCase()]
}

function getFallbackIcon(manifest?: PluginIconManifest, icon?: PluginDisplayIcon): LucideIcon {
  const type = getPluginType({ manifest })
  return resolveLucideIcon(icon?.fallback) ?? (type ? DEFAULT_TYPE_ICONS[type] : Puzzle)
}

function getImageSource(
  pluginId: string,
  icon: PluginDisplayIcon,
  installed?: boolean,
  marketplaceIconUrl?: string,
  version?: string
): string | null {
  if (icon.type !== 'local') {
    return null
  }

  if (installed) {
    const cacheKey = new URLSearchParams({ version: version ?? '', icon: icon.value })
    return `/api/webui/plugins/icon/${encodeURIComponent(pluginId)}?${cacheKey}`
  }

  const url = marketplaceIconUrl?.trim()
  // CNB Raw 禁止跨站嵌图，由 WebUI 同源端点读取。
  if (url?.startsWith('https://cnb.cool/Mai-with-u/plugin-repo/-/git/raw/')) {
    return `/api/webui/plugins/market-icon?url=${encodeURIComponent(url)}`
  }
  return url || null
}

export function PluginIcon({
  pluginId,
  manifest,
  installed,
  installedVersion,
  marketplaceIconUrl,
  className,
  iconClassName
}: PluginIconProps) {
  const icon = manifest?.display?.icon
  const [failedImageSource, setFailedImageSource] = useState<string | null>(null)
  const imageSource = useMemo(
    () => icon ? getImageSource(pluginId, icon, installed, marketplaceIconUrl, installedVersion ?? manifest?.version) : null,
    [icon, installed, installedVersion, manifest?.version, marketplaceIconUrl, pluginId]
  )

  const style: CSSProperties | undefined = icon?.background ? { backgroundColor: icon.background } : undefined
  const baseClassName = cn(
    'flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary overflow-hidden',
    className
  )

  if (icon?.type === 'emoji') {
    return (
      <div className={baseClassName} style={style}>
        <span className={cn('flex items-center justify-center text-xl leading-none', iconClassName)} aria-hidden="true">
          {icon.value}
        </span>
      </div>
    )
  }

  if (imageSource && imageSource !== failedImageSource) {
    return (
      <div className={baseClassName} style={style}>
        <img
          src={imageSource}
          alt=""
          className="h-full w-full object-cover"
          loading="lazy"
          onError={() => setFailedImageSource(imageSource)}
        />
      </div>
    )
  }

  const resolvedIcon = icon?.type === 'lucide'
    ? resolveLucideIcon(icon.value) ?? getFallbackIcon(manifest, icon)
    : getFallbackIcon(manifest, icon)

  return (
    <div className={baseClassName} style={style}>
      {createElement(resolvedIcon, { className: cn('h-5 w-5', iconClassName) })}
    </div>
  )
}
