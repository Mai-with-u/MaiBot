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
})
