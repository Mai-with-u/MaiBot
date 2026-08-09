import { describe, expect, it } from 'vitest'

import { menuSections } from './constants'

import type { MenuItem } from './types'

/** 展平所有分组下的菜单项，便于做全量断言 */
const allItems: MenuItem[] = menuSections.flatMap((section) => section.items)

describe('menuSections 菜单结构', () => {
  it('包含概览/机器人配置/资源/扩展与集成/高级工具五个分组且顺序固定', () => {
    expect(menuSections.map((section) => section.title)).toEqual([
      'sidebar.groups.overview',
      'sidebar.groups.botConfig',
      'sidebar.groups.botResources',
      'sidebar.groups.extensionsMonitor',
      'sidebar.groups.advancedTools',
    ])
  })

  it('每个分组至少包含一个菜单项', () => {
    for (const section of menuSections) {
      expect(section.items.length).toBeGreaterThan(0)
    }
  })

  it('所有菜单项路径全局唯一且以 / 开头', () => {
    const paths = allItems.map((item) => item.path)

    expect(new Set(paths).size).toBe(paths.length)
    for (const path of paths) {
      expect(path.startsWith('/')).toBe(true)
    }
  })

  it('所有菜单项 label 均为 sidebar.menu 命名空间的 i18n key', () => {
    for (const item of allItems) {
      expect(item.label).toMatch(/^sidebar\.menu\./)
    }
  })

  it('所有菜单项 icon 均为可渲染的组件（函数）', () => {
    for (const item of allItems) {
      expect(typeof item.icon).toBe('function')
    }
  })

  it('首页位于概览分组且路径为 /', () => {
    const homeItem = menuSections[0].items[0]

    expect(homeItem.label).toBe('sidebar.menu.home')
    expect(homeItem.path).toBe('/')
    expect(homeItem.searchDescription).toBe('search.items.homeDesc')
  })

  it('模型管理项携带新手引导 tourId', () => {
    const modelItem = allItems.find((item) => item.path === '/config/model')

    expect(modelItem).toBeDefined()
    expect(modelItem?.tourId).toBe('sidebar-model-management')
  })

  it('数据管理位于高级工具分组末尾', () => {
    const advancedToolsSection = menuSections.find(
      (section) => section.title === 'sidebar.groups.advancedTools'
    )
    const dataTransferItem = advancedToolsSection?.items.at(-1)

    expect(dataTransferItem?.label).toBe('sidebar.menu.dataTransfer')
    expect(dataTransferItem?.path).toBe('/data-transfer')
    expect(dataTransferItem?.searchDescription).toBe('search.items.dataTransferDesc')
  })

  it('Prompt 管理位于高级工具分组首位', () => {
    const advancedToolsSection = menuSections.find(
      (section) => section.title === 'sidebar.groups.advancedTools'
    )

    expect(advancedToolsSection?.items[0]).toMatchObject({
      label: 'sidebar.menu.promptManagement',
      path: '/config/prompts',
    })
  })

  it('适配器管理拥有独立入口并位于麦麦配置编辑分组', () => {
    const botConfigSection = menuSections.find(
      (section) => section.title === 'sidebar.groups.botConfig'
    )
    const adapterIndex =
      botConfigSection?.items.findIndex((item) => item.path === '/adapter-management') ?? -1
    const modelIndex =
      botConfigSection?.items.findIndex((item) => item.path === '/config/model') ?? -1

    expect(adapterIndex).toBeGreaterThanOrEqual(0)
    expect(adapterIndex).toBeGreaterThan(modelIndex)
  })

  it('详细统计数据不再占用主侧边栏入口', () => {
    const advancedToolsSection = menuSections.find(
      (section) => section.title === 'sidebar.groups.advancedTools'
    )

    expect(advancedToolsSection?.items).not.toEqual(
      expect.arrayContaining([expect.objectContaining({ path: '/statistics' })])
    )
    expect(advancedToolsSection?.items).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          label: 'sidebar.menu.replyEffects',
          path: '/reply-effects',
          featureFlag: 'replyEffects',
        }),
      ])
    )
  })

  it('行为学习与回复效果入口分别受特性开关控制', () => {
    const flaggedItems = allItems.filter((item) => item.featureFlag !== undefined)

    expect(flaggedItems).toHaveLength(2)
    expect(flaggedItems).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ path: '/resource/behavior', featureFlag: 'behaviorLearning' }),
        expect.objectContaining({ path: '/reply-effects', featureFlag: 'replyEffects' }),
      ])
    )
  })

  it('searchDescription 均为 search.items 命名空间的 i18n key', () => {
    for (const item of allItems) {
      if (item.searchDescription !== undefined) {
        expect(item.searchDescription).toMatch(/^search\.items\./)
      }
    }
  })
})
