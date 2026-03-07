import { useTheme } from '@/components/use-theme'

import type { BackgroundConfig } from '@/lib/theme/tokens'
import { defaultBackgroundConfig } from '@/lib/theme/tokens'

type BackgroundLayerId = 'page' | 'sidebar' | 'header' | 'card' | 'dialog'

/**
 * 获取指定层级的背景配置
 * 处理继承逻辑：如果 inherit 为 true，返回页面级别配置
 * @param layerId - 背景层级标识
 * @returns 对应层级的背景配置
 */
export function useBackground(layerId: BackgroundLayerId): BackgroundConfig {
  const { themeConfig } = useTheme()
  const bgMap = themeConfig.backgroundConfig ?? {}

  const config = bgMap[layerId] ?? defaultBackgroundConfig

  // 处理继承逻辑：非 page 层级且 inherit 为 true，返回 page 配置
  if (layerId !== 'page' && config.inherit) {
    return bgMap.page ?? defaultBackgroundConfig
  }

  return config
}
