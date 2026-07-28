import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { HomeCardManager, type HomeCardDefinition } from './HomeCardManager'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  window.localStorage.clear()
})

describe('HomeCardManager 布局持久化', () => {
  it('卡片定义引用变化但布局内容不变时不重复写入 localStorage', () => {
    window.localStorage.setItem(
      'maibot-home-card-layout-v1',
      JSON.stringify({
        hidden: [],
        order: ['builtin:test'],
        rowModes: {},
      })
    )
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem')
    const createCards = (): HomeCardDefinition[] => [
      {
        id: 'builtin:test',
        render: () => <div>测试卡片</div>,
        source: 'builtin',
        title: '测试',
      },
    ]

    const view = render(<HomeCardManager cards={createCards()} pluginCards={[]} />)
    expect(screen.getByText('测试卡片')).toBeInTheDocument()
    expect(setItemSpy).not.toHaveBeenCalled()

    view.rerender(<HomeCardManager cards={createCards()} pluginCards={[]} />)
    expect(setItemSpy).not.toHaveBeenCalled()
  })
})
