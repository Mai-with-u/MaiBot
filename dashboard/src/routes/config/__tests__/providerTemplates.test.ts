import { describe, expect, it } from 'vitest'

import { findTemplateByBaseUrl, resolveModelFetcherTemplate } from '../providerTemplates'

describe('providerTemplates', () => {
  it('为未知自定义 OpenAI 兼容端点启用模型列表获取', () => {
    const template = resolveModelFetcherTemplate('https://example.com/v1', 'openai')

    expect(template?.id).toBe('custom-openai-compatible')
    expect(template?.display_name).toBe('自定义 OpenAI 兼容端点')
    expect(template?.modelFetcher).toEqual({ endpoint: '/models', parser: 'openai' })
  })

  it('为未知自定义 Gemini 端点使用 Gemini 解析器', () => {
    const template = resolveModelFetcherTemplate('https://generativelanguage.example.com/v1beta', 'gemini')

    expect(template?.id).toBe('custom-gemini')
    expect(template?.client_type).toBe('gemini')
    expect(template?.modelFetcher).toEqual({ endpoint: '/models', parser: 'gemini' })
  })

  it('保留已知但不支持自动获取的内置模板状态', () => {
    const template = resolveModelFetcherTemplate('https://api.anthropic.com/v1', 'openai')

    expect(template?.id).toBe('anthropic')
    expect(template?.modelFetcher).toBeUndefined()
  })

  it('直接按 URL 查找模板时不把未知 URL 识别为内置模板', () => {
    expect(findTemplateByBaseUrl('https://example.com/v1')).toBeNull()
  })
})
