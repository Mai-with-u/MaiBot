import type { ThinkingProbeMode } from '@/lib/config-api'

import { describe, expect, it } from 'vitest'

import { applyThinkingProbeMode, isProbedThinkingEnabled, readThinkingProbeMode } from '../thinkingProbe'

const modes: ThinkingProbeMode[] = ['thinking_type', 'enable_thinking', 'reasoning_effort_nested']

describe('thinkingProbe 参数转换', () => {
  it.each(modes)('%s 开关状态在保存参数中可恢复', (mode) => {
    const enabled = applyThinkingProbeMode({ headers: { 'X-Test': 'keep' } }, mode, true)
    expect(readThinkingProbeMode(enabled)).toBe(mode)
    expect(isProbedThinkingEnabled(enabled, mode)).toBe(true)

    const disabled = applyThinkingProbeMode(enabled, mode, false)
    expect(readThinkingProbeMode(disabled)).toBe(mode)
    expect(isProbedThinkingEnabled(disabled, mode)).toBe(false)
    expect(disabled.headers).toEqual({ 'X-Test': 'keep' })
  })

  it.each(modes)('%s 同时清理顶层与 body 的冲突思考字段并保留无关参数', (mode) => {
    const original = {
      headers: { 'X-Route': 'relay' },
      query: { region: 'asia' },
      top_p: 0.8,
      thinking: { type: 'enabled' },
      enable_thinking: true,
      reasoning_effort: 'low',
      thinking_budget: 2048,
      thinking_config: { thinking_budget: 2048 },
      reasoning: { effort: 'low', summary: 'auto' },
      body: {
        frequency_penalty: 0.2,
        thinking: { type: 'disabled' },
        enable_thinking: false,
        reasoning_effort: 'high',
        thinking_budget: 4096,
        thinking_config: { thinking_budget: 4096 },
        reasoning: { effort: 'high', summary: 'detailed' },
      },
    }
    const snapshot = structuredClone(original)
    const applied = applyThinkingProbeMode(original, mode, false)

    expect(readThinkingProbeMode(applied)).toBe(mode)
    expect(isProbedThinkingEnabled(applied, mode)).toBe(false)
    expect(applied).toMatchObject({ headers: original.headers, query: original.query, top_p: 0.8 })
    expect(applied.body).toEqual({ frequency_penalty: 0.2, reasoning: { summary: 'detailed' } })
    expect(applied.reasoning).toEqual(mode === 'reasoning_effort_nested'
      ? { effort: 'none', summary: 'auto' }
      : { summary: 'auto' })
    for (const key of ['reasoning_effort', 'thinking_budget', 'thinking_config']) {
      expect(applied).not.toHaveProperty(key)
    }
    if (mode !== 'thinking_type') expect(applied).not.toHaveProperty('thinking')
    if (mode !== 'enable_thinking') expect(applied).not.toHaveProperty('enable_thinking')
    expect(original).toEqual(snapshot)
  })

  it('恢复 body 参数且遵循顶层覆盖 body 的实际请求语义', () => {
    const params = {
      body: { reasoning: { effort: 'none', summary: 'auto' } },
    }
    expect(readThinkingProbeMode(params)).toBe('reasoning_effort_nested')
    expect(isProbedThinkingEnabled(params, 'reasoning_effort_nested')).toBe(false)
    expect(applyThinkingProbeMode(params, 'reasoning_effort_nested', true).reasoning).toEqual({
      effort: 'high', summary: 'auto',
    })
    expect(readThinkingProbeMode({ body: { thinking: { type: 'enabled' } }, thinking: null })).toBeNull()
    expect(isProbedThinkingEnabled({ body: { enable_thinking: true }, enable_thinking: false }, 'enable_thinking')).toBe(false)
  })

  it.each(modes)('%s 应用结果不保留后端探测已清理的非法 reasoning 参数', (mode) => {
    const applied = applyThinkingProbeMode({
      reasoning: 'enabled',
      body: { reasoning: ['high'], top_p: 0.8 },
    }, mode, true)
    expect(applied.body).toEqual({ top_p: 0.8 })
    if (mode === 'reasoning_effort_nested') expect(applied.reasoning).toEqual({ effort: 'high' })
    else expect(applied).not.toHaveProperty('reasoning')
    expect(readThinkingProbeMode(applied)).toBe(mode)
  })

  it('缺少、非法或冲突的格式不被认作唯一已配置格式', () => {
    expect(readThinkingProbeMode({})).toBeNull()
    expect(readThinkingProbeMode({ thinking: null, enable_thinking: 'true', reasoning: [] })).toBeNull()
    expect(readThinkingProbeMode({ thinking: { type: 'enabled' }, body: { enable_thinking: true } })).toBeNull()
  })
})
