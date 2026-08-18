import { useEffect, useMemo, useState } from 'react'

import { BOT_CONFIG_UPDATED_EVENT, getBotConfigCached } from '@/lib/config-api'

import { menuSections } from './constants'
import type { MenuSection } from './types'

interface MenuFeatureFlags {
  behaviorLearning: boolean
  replyEffects: boolean
}

function resolveMenuFeatureFlags(config: Record<string, unknown> | null): MenuFeatureFlags {
  const experimental = config?.experimental
  const behaviorLearning =
    experimental && typeof experimental === 'object' && 'enable_behavior_learning' in experimental
      ? Boolean((experimental as Record<string, unknown>).enable_behavior_learning)
      : true
  const debug = config?.debug
  const replyEffects =
    debug && typeof debug === 'object'
      ? (debug as Record<string, unknown>).enable_reply_effect_tracking === true
      : false

  return {
    behaviorLearning,
    replyEffects,
  }
}

function filterMenuSections(flags: MenuFeatureFlags | null): MenuSection[] {
  return menuSections
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => {
        if (item.featureFlag === 'behaviorLearning') return flags?.behaviorLearning === true
        if (item.featureFlag === 'replyEffects') return flags?.replyEffects === true
        return true
      }),
    }))
    .filter((section) => section.items.length > 0)
}

export function useMenuSections(): MenuSection[] {
  const [featureFlags, setFeatureFlags] = useState<MenuFeatureFlags | null>(null)

  useEffect(() => {
    let cancelled = false

    const refreshFeatureFlags = () => {
      getBotConfigCached()
        .then((result) => {
          if (!cancelled) {
            setFeatureFlags(resolveMenuFeatureFlags(result ?? null))
          }
        })
        .catch(() => {
          if (!cancelled) {
            setFeatureFlags({ behaviorLearning: true, replyEffects: false })
          }
        })
    }

    refreshFeatureFlags()
    window.addEventListener(BOT_CONFIG_UPDATED_EVENT, refreshFeatureFlags)

    return () => {
      cancelled = true
      window.removeEventListener(BOT_CONFIG_UPDATED_EVENT, refreshFeatureFlags)
    }
  }, [])

  return useMemo(() => filterMenuSections(featureFlags), [featureFlags])
}
