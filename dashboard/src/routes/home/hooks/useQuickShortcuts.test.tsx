import { cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useQuickShortcuts } from './useQuickShortcuts'

vi.mock('react-i18next', () => {
  const t = (key: string) => key
  return { useTranslation: () => ({ t }) }
})

vi.mock('@/lib/plugin-api', () => ({
  getInstalledPlugins: vi.fn().mockResolvedValue([]),
  getPluginConfigSchema: vi.fn(),
}))

const STORAGE_KEY = 'maibot-home-quick-shortcuts'
const SIDEBAR_REDUNDANT_IDS = [
  'route:plugin-market',
  'route:plugin-config',
  'route:model-providers',
  'route:bot-config',
  'route:emoji',
  'route:expression',
]

function renderQuickShortcuts() {
  return renderHook(() =>
    useQuickShortcuts({
      isRestarting: false,
      handleRestart: vi.fn(),
      uncheckedCount: 0,
      onOpenReviewer: vi.fn(),
    })
  )
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('useQuickShortcuts', () => {
  it('默认快捷操作不包含侧边栏可一步到达的入口', () => {
    const { result } = renderQuickShortcuts()

    expect(result.current.quickShortcutIds).toEqual([
      'action:restart',
      'action:expression-review',
      'route:logs',
    ])
    expect(result.current.filteredQuickShortcutOptions.map((item) => item.id)).not.toEqual(
      expect.arrayContaining(SIDEBAR_REDUNDANT_IDS)
    )
  })

  it('读取旧配置时移除重复入口并同步迁移本地存储', () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify([
        'route:plugin-market',
        'route:model-list',
        'route:bot-config',
        'action:restart',
      ])
    )

    const { result } = renderQuickShortcuts()

    expect(result.current.quickShortcutIds).toEqual(['route:model-list', 'action:restart'])
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')).toEqual([
      'route:model-list',
      'action:restart',
    ])
  })

  it('旧配置只含重复入口时恢复为新的默认快捷操作', () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(SIDEBAR_REDUNDANT_IDS))

    const { result } = renderQuickShortcuts()

    expect(result.current.quickShortcutIds).toEqual([
      'action:restart',
      'action:expression-review',
      'route:logs',
    ])
  })
})
