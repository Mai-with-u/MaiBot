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

interface PluginIconManifest {
  id?: string
  plugin_type?: PluginType | string
  display?: {
    icon?: PluginDisplayIcon
  }
}

interface PluginIconProps {
  pluginId: string
  manifest?: PluginIconManifest
  installed?: boolean
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
  adapter: Plug,
  chat: Bot,
  creative: ImageIcon,
  provider: Cloud,
  management: Shield,
  search: Search,
  knowledge: BookOpen,
  media: ImageIcon,
  game: Gamepad2,
  security: Shield,
  automation: Settings,
  extension: Puzzle,
  other: Package,
}

function normalizePluginType(value: PluginType | string | undefined): PluginType {
  if (!value?.trim()) {
    return 'extension'
  }

  return value in DEFAULT_TYPE_ICONS ? (value as PluginType) : 'other'
}

function resolveLucideIcon(name: string | undefined): LucideIcon | undefined {
  if (!name) {
    return undefined
  }

  return LUCIDE_ICONS[name.trim().toLowerCase()]
}

function getFallbackIcon(manifest?: PluginIconManifest, icon?: PluginDisplayIcon): LucideIcon {
  return resolveLucideIcon(icon?.fallback) ?? DEFAULT_TYPE_ICONS[normalizePluginType(manifest?.plugin_type)]
}

function getImageSource(
  pluginId: string,
  icon: PluginDisplayIcon,
  installed?: boolean,
  marketplaceIconUrl?: string
): string | null {
  if (icon.type !== 'local') {
    return null
  }

  if (installed) {
    return `/api/webui/plugins/icon/${encodeURIComponent(pluginId)}`
  }

  return marketplaceIconUrl?.trim() || null
}

export function PluginIcon({
  pluginId,
  manifest,
  installed,
  marketplaceIconUrl,
  className,
  iconClassName
}: PluginIconProps) {
  const icon = manifest?.display?.icon
  const [failedImageSource, setFailedImageSource] = useState<string | null>(null)
  const imageSource = useMemo(
    () => icon ? getImageSource(pluginId, icon, installed, marketplaceIconUrl) : null,
    [icon, installed, marketplaceIconUrl, pluginId]
  )

  const style: CSSProperties | undefined = icon?.background ? { backgroundColor: icon.background } : undefined
  const baseClassName = cn(
    'flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary overflow-hidden',
    className
  )

  if (icon?.type === 'emoji') {
    return (
      <div className={baseClassName} style={style}>
        <span className={cn('text-xl leading-none', iconClassName)} aria-hidden="true">
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
