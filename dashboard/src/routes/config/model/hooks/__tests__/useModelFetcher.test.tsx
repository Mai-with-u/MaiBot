import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchProviderModels } from '@/lib/config-api'
import { modelListCache } from '../../constants'
import { useModelFetcher } from '../useModelFetcher'

vi.mock('@/lib/config-api', () => ({
  fetchProviderModels: vi.fn(),
}))

describe('useModelFetcher', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    modelListCache.clear()
  })

  it('自定义 OpenAI 兼容端点会尝试自动获取模型列表', async () => {
    vi.mocked(fetchProviderModels).mockResolvedValue([
      { id: 'custom-model', name: 'custom-model' },
    ])

    const { result } = renderHook(() =>
      useModelFetcher({
        getProviderConfig: () => ({
          name: 'custom',
          base_url: 'https://example.com/v1',
          api_key: 'sk-test',
          client_type: 'openai',
        }),
      })
    )

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

    const { result } = renderHook(() =>
      useModelFetcher({
        getProviderConfig: () => ({
          name: 'custom-gemini',
          base_url: 'https://generativelanguage.example.com/v1beta',
          api_key: 'gemini-key',
          client_type: 'gemini',
        }),
      })
    )

    await act(async () => {
      await result.current.fetchModelsForProvider('custom-gemini')
    })

    await waitFor(() => expect(result.current.availableModels).toHaveLength(1))
    expect(fetchProviderModels).toHaveBeenCalledWith('custom-gemini', 'gemini', '/models')
    expect(result.current.matchedTemplate?.display_name).toBe('自定义 Gemini 端点')
  })
})
