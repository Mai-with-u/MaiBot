import { useMemo } from 'react'

import { useFeatureFlags, type FeatureFlags } from '@/hooks/use-feature-flags'

import { menuSections } from './constants'
import type { MenuSection } from './types'

function filterMenuSections(flags: FeatureFlags | null): MenuSection[] {
  return menuSections
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => {
        if (item.featureFlag) return flags?.[item.featureFlag] === true
        return true
      }),
    }))
    .filter((section) => section.items.length > 0)
}

export function useMenuSections(): MenuSection[] {
  const { flags } = useFeatureFlags()
  return useMemo(() => filterMenuSections(flags), [flags])
}
