import { describe, expect, it } from 'vitest'

import {
  getDeepSeekReasoningEffort,
  isDeepSeekThinkingEnabled,
  isDeepSeekWebSearchEnabled,
  setDeepSeekReasoningEffort,
  setDeepSeekThinkingEnabled,
  setDeepSeekWebSearchEnabled,
  validateDeepSeekExtraParams,
} from './deepSeekExtraParams'

describe('DeepSeek Chat Completions 额外参数', () => {
  it('未配置时按官方默认值显示为开启思考和 high', () => {
    expect(isDeepSeekThinkingEnabled({}, 'openai')).toBe(true)
    expect(getDeepSeekReasoningEffort({}, 'openai')).toBe('high')
  })

  it('图形开关写入 thinking，并清除 Responses 格式的重复配置', () => {
    const disabled = setDeepSeekThinkingEnabled(
      { reasoning: { effort: 'max' }, top_p: 0.8 },
      'openai',
      false
    )
    expect(disabled).toEqual({ thinking: { type: 'disabled' }, top_p: 0.8 })

    const enabled = setDeepSeekReasoningEffort(disabled, 'openai', 'max')
    expect(enabled).toEqual({
      thinking: { type: 'enabled' },
      reasoning_effort: 'max',
      top_p: 0.8,
    })
  })

  it('拒绝 Chat Completions 中的原生联网工具', () => {
    expect(validateDeepSeekExtraParams(
      { tools: [{ type: 'web_search' }] },
      'openai'
    )).toContain('不支持原生联网搜索')
  })
})

describe('DeepSeek Responses API 额外参数', () => {
  it('开关和力度写入 reasoning.effort，并清除 Chat 格式的重复配置', () => {
    const disabled = setDeepSeekThinkingEnabled(
      { thinking: { type: 'enabled' }, reasoning_effort: 'max', top_p: 0.8 },
      'openai_responses',
      false
    )
    expect(disabled).toEqual({ reasoning: { effort: 'none' }, top_p: 0.8 })

    const enabled = setDeepSeekReasoningEffort(disabled, 'openai_responses', 'low')
    expect(enabled).toEqual({ reasoning: { effort: 'low' }, top_p: 0.8 })
  })

  it('联网开关保留其他工具，且重复开启不会重复添加', () => {
    const original = {
      tools: [{ type: 'function', name: 'local_tool' }],
      temperature: 0.5,
    }
    const enabled = setDeepSeekWebSearchEnabled(original, true)
    const enabledAgain = setDeepSeekWebSearchEnabled(enabled, true)

    expect(enabledAgain).toEqual({
      tools: [
        { type: 'function', name: 'local_tool' },
        { type: 'web_search' },
      ],
      temperature: 0.5,
    })
    expect(isDeepSeekWebSearchEnabled(enabledAgain)).toBe(true)

    expect(setDeepSeekWebSearchEnabled(enabledAgain, false)).toEqual(original)
  })

  it('校验冲突格式、非法力度和重复联网工具', () => {
    expect(validateDeepSeekExtraParams(
      { thinking: { type: 'enabled' } },
      'openai_responses'
    )).toContain('不能重复配置')
    expect(validateDeepSeekExtraParams(
      { reasoning: { effort: 'medium' } },
      'openai_responses'
    )).toContain('只能是 none、low、high 或 max')
    expect(validateDeepSeekExtraParams(
      { tools: [{ type: 'web_search' }, { type: 'web_search_2025_08_26' }] },
      'openai_responses'
    )).toBe('联网搜索工具只能配置一次')
  })
})
