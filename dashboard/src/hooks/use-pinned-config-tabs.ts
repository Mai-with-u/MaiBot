import { useState } from 'react'

interface PinnedConfigTab {
  id: string
  label: string
}

const STORAGE_KEY = 'maibot-pinned-config-tabs'

export function usePinnedConfigTabs() {
  const [pinnedTabs, setPinnedTabs] = useState<PinnedConfigTab[]>(() => {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === null) return []
    const value: unknown = JSON.parse(stored)
    if (!Array.isArray(value) || value.some((tab) =>
      typeof tab !== 'object' || tab === null ||
      typeof tab.id !== 'string' || typeof tab.label !== 'string'
    )) {
      throw new Error('钉固设置页面数据格式错误')
    }
    return value
  })

  const togglePin = (tab: PinnedConfigTab) => {
    const next = pinnedTabs.some((item) => item.id === tab.id)
      ? pinnedTabs.filter((item) => item.id !== tab.id)
      : [...pinnedTabs, { id: tab.id, label: tab.label }]
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    setPinnedTabs(next)
  }

  return { pinnedTabs, togglePin }
}
