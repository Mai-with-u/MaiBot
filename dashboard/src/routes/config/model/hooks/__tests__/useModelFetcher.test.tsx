import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchProviderModels } from '@/lib/config-api'
import { CACHE_TTL, modelListCache } from '../../constants'
import type { ProviderConfig } from '../../types'
import { useAutoFetchModels, useModelFetcher } from '../useModelFetcher'

vi.mock('@/lib/config-api', () => ({
  fetchProviderModels: vi.fn(),
}))

const openaiProvider: ProviderConfig = {
  name: 'openai',
  base_url: 'https://api.openai.com/v1',
  api_key: 'sk-test',
  client_type: 'openai',
}

function renderFetcher(
  getProviderConfig: (providerName: string) => ProviderConfig | undefined
) {
  return renderHook(() => useModelFetcher({ getProviderConfig }))
}

describe('useModelFetcher', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    modelListCache.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('自定义 OpenAI 兼容端点会尝试自动获取模型列表', async () => {
    vi.mocked(fetchProviderModels).mockResolvedValue([
      { id: 'custom-model', name: 'custom-model' },
    ])

    const { result } = renderFetcher(() => ({
      name: 'custom',
      base_url: 'https://example.com/v1',
      api_key: 'sk-test',
      client_type: 'openai',
    }))

    await act(async () => {
      await result.current.fetchModelsForProvider('custom')
    })

    expect(fetchProviderModels).toHaveBeenCalledWith('custom', 'openai', '/models')
    expect(result.current.matchedTemplate?.display_name).toBe('自定义 OpenAI 兼容端点')
    expect(result.current.availableModels).toEqual([{ id: 'custom-model', name: 'custom-model' }])
  })

  it('自定义 Gemini 端点会使用 Gemini 解析器获取模型列表', async () => {
    vi.mocked(fetchProviderModels).mockResolvedValue([
      { id: 'gemini-custom', name: 'Gemini Custom' },
    ])

    const { result } = renderFetcher(() => ({
      name: 'custom-gemini',
      base_url: 'https://generativelanguage.example.com/v1beta',
      api_key: 'gemini-key',
      client_type: 'gemini',
    }))

    await act(async () => {
      await result.current.fetchModelsForProvider('custom-gemini')
    })

    await waitFor(() => expect(result.current.availableModels).toHaveLength(1))
    expect(fetchProviderModels).toHaveBeenCalledWith('custom-gemini', 'gemini', '/models')
    expect(result.current.matchedTemplate?.display_name).toBe('自定义 Gemini 端点')
  })

  it('clearModels 清空模型列表、错误和匹配模板', async () => {
    vi.mocked(fetchProviderModels).mockRejectedValue(new Error('上游 502'))
    vi.spyOn(console, 'error').mockImplementation(() => {})

    const { result } = renderFetcher(() => openaiProvider)

    await act(async () => {
      await result.current.fetchModelsForProvider('openai')
    })

    expect(result.current.availableModels).toEqual([])
    expect(result.current.modelFetchError).toBe('上游 502')
    expect(result.current.matchedTemplate?.display_name).toBe('OpenAI')

    act(() => {
      result.current.clearModels()
    })

    expect(result.current.availableModels).toEqual([])
    expect(result.current.modelFetchError).toBeNull()
    expect(result.current.matchedTemplate).toBeNull()
  })

  it('缺少提供商或 base_url 时提示配置不完整并清空已有结果', async () => {
    vi.mocked(fetchProviderModels).mockResolvedValue([{ id: 'gpt-4', name: 'gpt-4' }])

    const { result } = renderFetcher((name) => (name === 'openai' ? openaiProvider : undefined))

    await act(async () => {
      await result.current.fetchModelsForProvider('openai')
    })
    expect(result.current.availableModels).toHaveLength(1)

    await act(async () => {
      await result.current.fetchModelsForProvider('missing')
    })

    expect(fetchProviderModels).toHaveBeenCalledTimes(1)
    expect(result.current.availableModels).toEqual([])
    expect(result.current.matchedTemplate).toBeNull()
    expect(result.current.modelFetchError).toBe('提供商配置不完整，请先在"模型厂商设置"中配置')

    const { result: emptyUrlResult } = renderFetcher(() => ({
      ...openaiProvider,
      base_url: '',
    }))
    await act(async () => {
      await emptyUrlResult.current.fetchModelsForProvider('openai')
    })
    expect(emptyUrlResult.current.modelFetchError).toBe(
      '提供商配置不完整，请先在"模型厂商设置"中配置'
    )
    expect(fetchProviderModels).toHaveBeenCalledTimes(1)
  })

  it('未配置 API Key 时提示补全密钥且不请求接口', async () => {
    vi.mocked(fetchProviderModels).mockResolvedValue([{ id: 'gpt-4', name: 'gpt-4' }])

    const { result } = renderFetcher(() => ({
      ...openaiProvider,
      api_key: '',
    }))

    await act(async () => {
      await result.current.fetchModelsForProvider('openai')
    })

    expect(fetchProviderModels).not.toHaveBeenCalled()
    expect(result.current.availableModels).toEqual([])
    expect(result.current.matchedTemplate).toBeNull()
    expect(result.current.modelFetchError).toBe(
      '该提供商未配置 API Key，请先在"模型厂商设置"中填写'
    )
  })

  it('模板不支持自动获取时清空列表且不报错', async () => {
    vi.mocked(fetchProviderModels).mockResolvedValue([{ id: 'claude', name: 'claude' }])

    const { result } = renderFetcher(() => ({
      name: 'anthropic',
      base_url: 'https://api.anthropic.com/v1',
      api_key: 'sk-ant',
      client_type: 'openai',
    }))

    await act(async () => {
      await result.current.fetchModelsForProvider('anthropic')
    })

    expect(fetchProviderModels).not.toHaveBeenCalled()
    expect(result.current.availableModels).toEqual([])
    expect(result.current.modelFetchError).toBeNull()
    expect(result.current.matchedTemplate?.display_name).toBe('Anthropic (Claude)')
    expect(result.current.matchedTemplate?.modelFetcher).toBeUndefined()
  })

  it('缓存未过期时直接复用，过期或强制刷新才重新请求', async () => {
    vi.mocked(fetchProviderModels).mockResolvedValue([{ id: 'gpt-4o', name: 'gpt-4o' }])

    const { result } = renderFetcher(() => openaiProvider)

    await act(async () => {
      await result.current.fetchModelsForProvider('openai')
    })
    expect(fetchProviderModels).toHaveBeenCalledTimes(1)

    vi.mocked(fetchProviderModels).mockResolvedValue([{ id: 'gpt-5', name: 'gpt-5' }])

    await act(async () => {
      await result.current.fetchModelsForProvider('openai')
    })
    expect(fetchProviderModels).toHaveBeenCalledTimes(1)
    expect(result.current.availableModels).toEqual([{ id: 'gpt-4o', name: 'gpt-4o' }])
    expect(result.current.modelFetchError).toBeNull()

    await act(async () => {
      await result.current.fetchModelsForProvider('openai', true)
    })
    expect(fetchProviderModels).toHaveBeenCalledTimes(2)
    expect(result.current.availableModels).toEqual([{ id: 'gpt-5', name: 'gpt-5' }])

    const cacheKey = 'openai:https://api.openai.com/v1'
    modelListCache.set(cacheKey, {
      models: [{ id: 'stale', name: 'stale' }],
      timestamp: Date.now() - CACHE_TTL - 1,
    })
    vi.mocked(fetchProviderModels).mockResolvedValue([{ id: 'fresh', name: 'fresh' }])

    await act(async () => {
      await result.current.fetchModelsForProvider('openai')
    })
    expect(fetchProviderModels).toHaveBeenCalledTimes(3)
    expect(result.current.availableModels).toEqual([{ id: 'fresh', name: 'fresh' }])
  })

  it('请求进行中将 fetchingModels 置为 true，结束后恢复', async () => {
    let resolveModels: (value: { id: string; name: string }[]) => void = () => {}
    vi.mocked(fetchProviderModels).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveModels = resolve
        })
    )

    const { result } = renderFetcher(() => openaiProvider)

    let pending: Promise<void> | undefined
    act(() => {
      pending = result.current.fetchModelsForProvider('openai')
    })

    await waitFor(() => expect(result.current.fetchingModels).toBe(true))
    expect(result.current.modelFetchError).toBeNull()

    await act(async () => {
      resolveModels([{ id: 'gpt-4', name: 'gpt-4' }])
      await pending
    })

    expect(result.current.fetchingModels).toBe(false)
    expect(result.current.availableModels).toEqual([{ id: 'gpt-4', name: 'gpt-4' }])
  })

  it.each([
    ['无效的密钥', 'API Key 无效或已过期，请检查"模型厂商设置"中的密钥'],
    ['密钥已过期', 'API Key 无效或已过期，请检查"模型厂商设置"中的密钥'],
    ['API Key rejected', 'API Key 无效或已过期，请检查"模型厂商设置"中的密钥'],
    ['权限不足', '没有权限获取模型列表，请检查 API Key 权限'],
    ['timeout of 30000ms', '请求超时，请检查网络连接后重试'],
    ['连接超时', '请求超时，请检查网络连接后重试'],
    ['不支持列出模型', '该提供商不支持自动获取模型列表，请手动输入'],
    ['上游 502', '上游 502'],
  ] as const)('将错误「%s」映射为友好提示', async (rawMessage, friendlyMessage) => {
    vi.mocked(fetchProviderModels).mockRejectedValue(new Error(rawMessage))
    vi.spyOn(console, 'error').mockImplementation(() => {})

    const { result } = renderFetcher(() => openaiProvider)

    await act(async () => {
      await result.current.fetchModelsForProvider('openai')
    })

    expect(result.current.modelFetchError).toBe(friendlyMessage)
    expect(result.current.availableModels).toEqual([])
    expect(result.current.fetchingModels).toBe(false)
  })

  it('空错误信息回退为默认失败文案', async () => {
    vi.mocked(fetchProviderModels).mockRejectedValue(new Error(''))
    vi.spyOn(console, 'error').mockImplementation(() => {})

    const { result } = renderFetcher(() => openaiProvider)

    await act(async () => {
      await result.current.fetchModelsForProvider('openai')
    })

    expect(result.current.modelFetchError).toBe('获取模型列表失败')
    expect(result.current.availableModels).toEqual([])
  })
})

describe('useAutoFetchModels', () => {
  it('仅在编辑对话框打开且已选提供商时触发拉取', () => {
    const fetchModelsForProvider = vi.fn()
    const { rerender } = renderHook(
      ({
        editDialogOpen,
        apiProvider,
      }: {
        editDialogOpen: boolean
        apiProvider: string | undefined
      }) => useAutoFetchModels(editDialogOpen, apiProvider, fetchModelsForProvider),
      { initialProps: { editDialogOpen: false, apiProvider: 'openai' as string | undefined } }
    )

    expect(fetchModelsForProvider).not.toHaveBeenCalled()

    rerender({ editDialogOpen: true, apiProvider: 'openai' })
    expect(fetchModelsForProvider).toHaveBeenCalledTimes(1)
    expect(fetchModelsForProvider).toHaveBeenCalledWith('openai')

    rerender({ editDialogOpen: true, apiProvider: 'gemini' })
    expect(fetchModelsForProvider).toHaveBeenCalledTimes(2)
    expect(fetchModelsForProvider).toHaveBeenLastCalledWith('gemini')

    fetchModelsForProvider.mockClear()
    rerender({ editDialogOpen: true, apiProvider: undefined })
    expect(fetchModelsForProvider).not.toHaveBeenCalled()

    rerender({ editDialogOpen: false, apiProvider: 'openai' })
    expect(fetchModelsForProvider).not.toHaveBeenCalled()
  })

  it('对话框打开时用真实 fetchModelsForProvider 拉取当前提供商', async () => {
    vi.mocked(fetchProviderModels).mockResolvedValue([{ id: 'gpt-4', name: 'gpt-4' }])
    // 稳定 getter，避免 fetchModelsForProvider 每轮渲染换引用导致自动拉取死循环
    const getProviderConfig = () => openaiProvider

    const { result } = renderHook(() => {
      const fetcher = useModelFetcher({ getProviderConfig })
      useAutoFetchModels(true, 'openai', fetcher.fetchModelsForProvider)
      return fetcher
    })

    await waitFor(() => {
      expect(result.current.availableModels).toEqual([{ id: 'gpt-4', name: 'gpt-4' }])
    })
    expect(fetchProviderModels).toHaveBeenCalledWith('openai', 'openai', '/models')
  })
})
