import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  buildSearchNavigationPath,
  getConfigSearchField,
  getModelConfigTabForField,
  scrollToConfigSearchField,
} from './config-search-navigation'

describe('config search navigation', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('为 Bot 配置字段生成受控查询参数', () => {
    expect(buildSearchNavigationPath('/config/bot', 'chat.reply_timing.talk_value')).toBe(
      '/config/bot?field=chat.reply_timing.talk_value'
    )
    expect(getConfigSearchField('?field=chat.reply_timing.talk_value')).toBe(
      'chat.reply_timing.talk_value'
    )
  })

  it('根据模型字段选择正确标签页', () => {
    expect(getModelConfigTabForField('api_providers.name')).toBe('providers')
    expect(getModelConfigTabForField('models.model_identifier')).toBe('models')
    expect(getModelConfigTabForField('model_task_config.utils.model_list')).toBe('tasks')
    expect(buildSearchNavigationPath('/config/model', 'model_task_config.utils.model_list')).toBe(
      '/config/model?field=model_task_config.utils.model_list&tab=tasks'
    )
  })

  it('找不到叶子字段时定位到最近的已渲染父字段', () => {
    const parent = document.createElement('div')
    parent.dataset.configFieldPath = 'model_task_config.utils'
    parent.scrollIntoView = vi.fn()
    document.body.appendChild(parent)

    const result = scrollToConfigSearchField('model_task_config.utils.model_list')

    expect(result).toBe(parent)
    expect(parent.scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' })
    expect(parent.dataset.configSearchHighlight).toBe('true')
  })

  it('缺少字段或非配置路径时原样返回路径', () => {
    expect(buildSearchNavigationPath('/config/bot')).toBe('/config/bot')
    expect(buildSearchNavigationPath('/config/bot', '')).toBe('/config/bot')
    expect(buildSearchNavigationPath('/plugins', 'models.name')).toBe('/plugins')
  })

  it('模型字段缺省落到 providers 标签，Bot 路径不带 tab', () => {
    expect(getModelConfigTabForField('api_providers.base_url')).toBe('providers')
    expect(getModelConfigTabForField('')).toBe('providers')
    expect(buildSearchNavigationPath('/config/model', 'api_providers.name')).toBe(
      '/config/model?field=api_providers.name&tab=providers'
    )
    expect(buildSearchNavigationPath('/config/bot', 'personality.personality')).toBe(
      '/config/bot?field=personality.personality'
    )
  })

  it('解析查询串时去掉首尾空白，缺省或只有其他参数时返回空串', () => {
    expect(getConfigSearchField('field=%20talk.value%20')).toBe('talk.value')
    expect(getConfigSearchField('field=')).toBe('')
    expect(getConfigSearchField('')).toBe('')
    expect(getConfigSearchField('tab=models')).toBe('')
    expect(getConfigSearchField('?tab=tasks&field=models.name')).toBe('models.name')
  })

  it('精确匹配叶子字段时直接滚动并在超时后清除高亮', () => {
    vi.useFakeTimers()
    const leaf = document.createElement('div')
    leaf.dataset.configFieldPath = 'chat.reply_timing.talk_value'
    leaf.scrollIntoView = vi.fn()
    document.body.appendChild(leaf)

    expect(scrollToConfigSearchField('chat.reply_timing.talk_value')).toBe(leaf)
    expect(leaf.scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' })
    expect(leaf.dataset.configSearchHighlight).toBe('true')

    vi.advanceTimersByTime(2200)
    expect(leaf.dataset.configSearchHighlight).toBeUndefined()
    vi.useRealTimers()
  })

  it('支持 data-dynamic-field 定位', () => {
    const field = document.createElement('div')
    field.dataset.dynamicField = 'models.0.name'
    field.scrollIntoView = vi.fn()
    document.body.appendChild(field)

    expect(scrollToConfigSearchField('models.0.name')).toBe(field)
  })

  it('找不到任何字段时返回 null', () => {
    expect(scrollToConfigSearchField('missing.field')).toBeNull()
  })

  it('父字段折叠时先展开，再延迟定位叶子字段', () => {
    vi.useFakeTimers()
    const parent = document.createElement('div')
    parent.dataset.configFieldPath = 'chat.reply_timing'
    parent.scrollIntoView = vi.fn()
    const expandButton = document.createElement('button')
    expandButton.setAttribute('aria-expanded', 'false')
    expandButton.addEventListener('click', () => {
      expandButton.setAttribute('aria-expanded', 'true')
      const leaf = document.createElement('div')
      leaf.dataset.configFieldPath = 'chat.reply_timing.talk_value'
      leaf.scrollIntoView = vi.fn()
      parent.appendChild(leaf)
    })
    parent.appendChild(expandButton)
    document.body.appendChild(parent)

    const firstMatch = scrollToConfigSearchField('chat.reply_timing.talk_value')
    expect(firstMatch).toBe(parent)
    expect(expandButton.getAttribute('aria-expanded')).toBe('true')

    vi.advanceTimersByTime(100)
    const leaf = parent.querySelector('[data-config-field-path="chat.reply_timing.talk_value"]')
    expect(leaf).not.toBeNull()
    expect((leaf as HTMLElement).dataset.configSearchHighlight).toBe('true')
    vi.useRealTimers()
  })
})
