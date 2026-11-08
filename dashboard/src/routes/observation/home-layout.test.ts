import { beforeEach, describe, expect, it } from 'vitest'

import { desktopPetCardState, type DesktopPetStatus } from '@/lib/desktop-pet-api'

import {
  DEFAULT_HOME_LAYOUT,
  insertDesktopPetCard,
  loadHomeLayout,
  missingHomeCards,
  saveHomeLayout,
  visibleStatsSlots,
  type HomeLayout,
} from './home-layout'

function defaultLayout(): HomeLayout {
  return JSON.parse(JSON.stringify(DEFAULT_HOME_LAYOUT)) as HomeLayout
}

describe('首页桌宠卡片排法', () => {
  beforeEach(() => localStorage.clear())

  it('默认排法下三张卡片各占三分之一，桌宠排在 Token 消耗后面', () => {
    const next = insertDesktopPetCard(defaultLayout())
    expect(next.stats.slice(-3)).toEqual([
      { id: 'requests', span: 4 },
      { id: 'tokens', span: 4 },
      { id: 'pet', span: 4 },
    ])
  })

  it('用户改过趋势图宽度时只追加到统计段末尾', () => {
    const layout = defaultLayout()
    layout.stats = layout.stats.map((slot) => (slot.id === 'tokens' ? { ...slot, span: 12 } : slot))
    const next = insertDesktopPetCard(layout)
    expect(next.stats.find((slot) => slot.id === 'tokens')?.span).toBe(12)
    expect(next.stats.at(-1)).toEqual({ id: 'pet', span: 3 })
  })

  it('已经放过或用户移除过时不再自动放回', () => {
    const inserted = insertDesktopPetCard(defaultLayout())
    expect(insertDesktopPetCard(inserted)).toBe(inserted)
    const dismissed = { ...defaultLayout(), petDismissed: true }
    expect(insertDesktopPetCard(dismissed)).toBe(dismissed)
  })

  it('桌宠关闭时隐藏卡片，自动排成三分之一的趋势图恢复各占一半', () => {
    const inserted = insertDesktopPetCard(defaultLayout())
    expect(visibleStatsSlots(inserted, true)).toBe(inserted.stats)
    const hidden = visibleStatsSlots(inserted, false)
    expect(hidden.some((slot) => slot.id === 'pet')).toBe(false)
    expect(hidden.slice(-2)).toEqual([
      { id: 'requests', span: 6 },
      { id: 'tokens', span: 6 },
    ])
  })

  it('没开桌宠时卡片库里不提供桌宠卡片', () => {
    expect(missingHomeCards(defaultLayout(), false)).not.toContain('pet')
    expect(missingHomeCards(defaultLayout(), true)).toContain('pet')
  })

  it('保存和读取时保留三分之一宽度与「已移除」标记', () => {
    saveHomeLayout({ ...insertDesktopPetCard(defaultLayout()), petDismissed: true })
    const loaded = loadHomeLayout()
    expect(loaded.petDismissed).toBe(true)
    expect(loaded.stats.find((slot) => slot.id === 'pet')?.span).toBe(4)
  })
})

describe('桌宠卡片状态', () => {
  const base: DesktopPetStatus = {
    enabled: true,
    supported: true,
    running: true,
    mode: 'local',
    ready: true,
    visible: true,
    error: '',
    skin_id: 'builtin',
    thinking: false,
    bubble: '',
    todos: { pending: 0, items: [] },
  }

  it.each([
    [{}, 'idle'],
    [{ thinking: true }, 'thinking'],
    [{ visible: false }, 'hidden'],
    [{ ready: false }, 'starting'],
    [{ mode: 'remote', running: false, ready: false }, 'waiting'],
    [{ error: '启动失败' }, 'error'],
    [{ supported: false, ready: false }, 'error'],
  ] as const)('%o → %s', (patch, expected) => {
    expect(desktopPetCardState({ ...base, ...patch } as DesktopPetStatus)).toBe(expected)
  })
})
