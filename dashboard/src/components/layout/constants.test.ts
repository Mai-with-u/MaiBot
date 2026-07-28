import { describe, expect, it } from 'vitest'

import { menuSections } from './constants'

import type { MenuItem } from './types'

/** 展平所有分组下的菜单项，便于做全量断言 */
const allItems: MenuItem[] = menuSections.flatMap((section) => section.items)

describe('menuSections 菜单结构', () => {
  it('包含概览/机器人配置/资源/扩展监控四个分组且顺序固定', () => {
    expect(menuSections.map((section) => section.title)).toEqual([
      'sidebar.groups.overview',
      'sidebar.groups.botConfig',
      'sidebar.groups.botResources',
      'sidebar.groups.extensionsMonitor',
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

  it('数据迁移位于扩展与维护分组', () => {
    const extensionsSection = menuSections.find(
      (section) => section.title === 'sidebar.groups.extensionsMonitor'
    )
    const dataTransferItem = extensionsSection?.items.find((item) => item.path === '/data-transfer')

    expect(dataTransferItem?.label).toBe('sidebar.menu.dataTransfer')
    expect(dataTransferItem?.searchDescription).toBe('search.items.dataTransferDesc')
  })

  it('行为学习项受 behaviorLearning 特性开关控制，且是唯一带开关的项', () => {
    const flaggedItems = allItems.filter((item) => item.featureFlag !== undefined)

    expect(flaggedItems).toHaveLength(1)
    expect(flaggedItems[0].path).toBe('/resource/behavior')
    expect(flaggedItems[0].featureFlag).toBe('behaviorLearning')
  })

  it('searchDescription 均为 search.items 命名空间的 i18n key', () => {
    for (const item of allItems) {
      if (item.searchDescription !== undefined) {
        expect(item.searchDescription).toMatch(/^search\.items\./)
      }
    }
  })
})
