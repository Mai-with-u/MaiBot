import type { ComponentType } from 'react'
import { Icon } from '@iconify/react'

import { useTheme } from '@/components/use-theme'
import { cn } from '@/lib/utils'
import { getStreamlineIcon } from './streamline-icons'
import type { StreamlineCollection } from './streamline-icons'

type FallbackIcon = ComponentType<{
  className?: string
  color?: string
  size?: number | string
}>

interface StreamlineIconProps {
  name: string
  collection?: StreamlineCollection
  fallback?: FallbackIcon
  className?: string
  color?: string
  size?: number | string
}

export function StreamlineIcon({
  name,
  collection = 'streamline-sharp',
  fallback: Fallback,
  className,
  color,
  size = 16,
}: StreamlineIconProps) {
  const { themeConfig } = useTheme()
  const icon = getStreamlineIcon(collection, name)

  if ((themeConfig.dashboardStyle !== 'future-retro' || !icon) && Fallback) {
    return <Fallback className={className} color={color} size={size} />
  }

  return (
    <Icon
      icon={icon ?? `${collection}:${name}`}
      className={cn('inline-block shrink-0', className)}
      color={color}
      width={size}
      height={size}
    />
  )
}
