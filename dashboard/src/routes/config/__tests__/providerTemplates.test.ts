import { describe, expect, it } from 'vitest'

import { getThinkingEffort, isThinkingEnabled, setThinkingEffort, validateThinkingParams } from '../model/thinkingFormats'
import { findTemplateByBaseUrl, resolveModelFetcherTemplate, resolveThinkingFormatForModel } from '../providerTemplates'

describe('providerTemplates', () => {
  it('OpenAI 推理档位随模型与协议变化，可读写旧配置并保留其他推理参数', () => {
    const template = findTemplateByBaseUrl('https://api.openai.com/v1')
    const terra = resolveThinkingFormatForModel(template, 'gpt-6.1-terra-2026-10-01')!
    const chat = resolveThinkingFormatForModel(template, 'gpt-6.1-terra', 'openai')!
    const legacy = { body: { reasoning: { summary: 'auto' }, reasoning_effort: 'high', top_p: 0.9 } }
    expect(getThinkingEffort(legacy, terra)).toBe('high')
    const edited = setThinkingEffort(legacy, terra, 'none')
    expect(edited).toEqual({ body: { top_p: 0.9 }, reasoning: { effort: 'none', summary: 'auto' } })
    expect(isThinkingEnabled(edited, terra)).toBe(false)
    expect(getThinkingEffort(edited, chat)).toBe('none')
    const switched = setThinkingEffort(edited, chat, 'high')
    expect(switched).toEqual({ body: { top_p: 0.9 }, reasoning: { summary: 'auto' }, reasoning_effort: 'high' })
    expect(getThinkingEffort(switched, terra)).toBe('high')
    expect(legacy.body.reasoning_effort).toBe('high')
    const astra = resolveThinkingFormatForModel(template, 'gpt-6.2-astra')!
    expect(validateThinkingParams(edited, astra)).toContain('reasoning.effort')
    expect(validateThinkingParams(edited, terra)).toBeNull()
    expect(getThinkingEffort({}, resolveThinkingFormatForModel(template, 'gpt-5.4')!)).toBe('none')
    expect(getThinkingEffort({}, resolveThinkingFormatForModel(template, 'gpt-5.5')!)).toBe('medium')
    expect(resolveThinkingFormatForModel(template, 'gpt-4.1-mini')).toBeNull()
  })

  it('为未知自定义 OpenAI 兼容端点启用模型列表获取', () => {
    const template = resolveModelFetcherTemplate('https://example.com/v1', 'openai')

    expect(template?.id).toBe('custom-openai-compatible')
    expect(template?.display_name).toBe('自定义 OpenAI 兼容端点')
    expect(template?.modelFetcher).toEqual({ endpoint: '/models', parser: 'openai' })
  })

  it('为未知自定义 Gemini 端点使用 Gemini 解析器', () => {
    const template = resolveModelFetcherTemplate(
      'https://generativelanguage.example.com/v1beta',
      'gemini'
    )

    expect(template?.id).toBe('custom-gemini')
    expect(template?.client_type).toBe('gemini')
    expect(template?.modelFetcher).toEqual({ endpoint: '/models', parser: 'gemini' })
  })

  it('为自定义 OpenAI Responses 端点保留 Responses 客户端类型', () => {
    const template = resolveModelFetcherTemplate('https://responses.example.com/v1', 'openai_responses')

    expect(template?.id).toBe('custom-openai-responses')
    expect(template?.client_type).toBe('openai_responses')
    expect(template?.modelFetcher).toEqual({ endpoint: '/models', parser: 'openai' })
  })

  it('未配置 modelFetcher 的内置模板也默认乐观尝试 /models', () => {
    const template = resolveModelFetcherTemplate('https://api.anthropic.com/v1', 'openai')

    expect(template?.id).toBe('anthropic')
    expect(template?.modelFetcher).toEqual({ endpoint: '/models', parser: 'openai' })
  })

  it('Gemini 内置模板未配置 modelFetcher 时按 Gemini 解析器补齐', () => {
    const template = resolveModelFetcherTemplate(
      'https://generativelanguage.googleapis.com/v1beta',
      'gemini'
    )

    expect(template?.id).toBe('gemini')
    expect(template?.modelFetcher).toEqual({ endpoint: '/models', parser: 'gemini' })
  })

  it('直接按 URL 查找模板时不把未知 URL 识别为内置模板', () => {
    expect(findTemplateByBaseUrl('https://example.com/v1')).toBeNull()
  })

  it('普通智谱 GLM-5.3 限制关闭与力度，但 Coding 套餐仍允许关闭', () => {
    const apiTemplate = findTemplateByBaseUrl('https://open.bigmodel.cn/api/paas/v4')
    const codingTemplate = findTemplateByBaseUrl('https://open.bigmodel.cn/api/coding/paas/v4')
    const apiThinking = resolveThinkingFormatForModel(apiTemplate, 'glm-5.3-flash')!
    const codingThinking = resolveThinkingFormatForModel(codingTemplate, 'glm-5.3-flash')!

    expect(apiThinking.canDisable).toBe(false)
    expect(apiThinking.efforts).toEqual(['low', 'high', 'max'])
    expect(validateThinkingParams({ thinking: { type: 'disabled' } }, apiThinking)).toBe('当前模型不支持关闭思考')
    expect(validateThinkingParams({ reasoning_effort: 'minimal' }, apiThinking)).toContain('reasoning_effort 只能是')
    expect(codingThinking.canDisable).toBe(true)
    expect(validateThinkingParams({ thinking: { type: 'disabled' } }, codingThinking)).toBeNull()
    expect(resolveThinkingFormatForModel(apiTemplate, 'glm-4.7')?.canDisable).toBe(true)
  })
})
