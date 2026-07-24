import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  getModelConfig,
  getModelConfigCached,
  getModelConfigSchema,
  getModelConfigVersions,
  updateModelConfig,
  updateModelConfigSection,
} from '@/lib/config-api'

import { useModelConfig } from './useModelConfig'

const toastMock = vi.fn()

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: toastMock }),
}))

vi.mock('@/lib/config-api', () => ({
  createModelConfigVersion: vi.fn(),
  deleteModelConfigVersion: vi.fn(),
  getModelConfig: vi.fn(),
  getModelConfigCached: vi.fn(),
  getModelConfigSchema: vi.fn(),
  getModelConfigVersions: vi.fn(),
  switchModelConfigVersion: vi.fn(),
  testModelCapability: vi.fn(),
  testProviderConnection: vi.fn(),
  updateModelConfig: vi.fn(),
  updateModelConfigSection: vi.fn(),
}))

const getModelConfigMock = vi.mocked(getModelConfig)
const getModelConfigCachedMock = vi.mocked(getModelConfigCached)
const getModelConfigSchemaMock = vi.mocked(getModelConfigSchema)
const getModelConfigVersionsMock = vi.mocked(getModelConfigVersions)
const updateModelConfigMock = vi.mocked(updateModelConfig)
const updateModelConfigSectionMock = vi.mocked(updateModelConfigSection)

function createDeferred<T>() {
  let resolve: (value: T | PromiseLike<T>) => void = () => {}
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

const provider = (name: string) => ({
  name,
  base_url: `https://${name}.example.com/v1`,
  api_key: `key-${name}`,
  client_type: 'openai',
})

function modelConfig() {
  return {
    models: [
      {
        name: 'model',
        model_identifier: 'model',
        api_provider: 'main',
        price_in: 0,
        price_out: 0,
      },
    ],
    api_providers: [provider('main'), provider('spare-a'), provider('spare-b')],
    model_task_config: {
      replyer: { model_list: ['model'] },
    },
  }
}

beforeEach(() => {
  const config = modelConfig()
  getModelConfigMock.mockResolvedValue(config as never)
  getModelConfigCachedMock.mockResolvedValue(config as never)
  getModelConfigSchemaMock.mockResolvedValue({
    schema: { nested: {} },
  } as never)
  getModelConfigVersionsMock.mockResolvedValue({
    active_version: null,
    versions: [],
  } as never)
  updateModelConfigMock.mockResolvedValue(config as never)
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('useModelConfig provider 自动保存', () => {
  it('旧请求完成时不会把请求期间的新 provider 编辑回退', async () => {
    const firstSave = createDeferred<Record<string, unknown>>()
    const secondSave = createDeferred<Record<string, unknown>>()
    updateModelConfigSectionMock
      .mockImplementationOnce(() => firstSave.promise)
      .mockImplementationOnce(() => secondSave.promise)
    const { result } = renderHook(() => useModelConfig())
    await waitFor(() => expect(result.current.loading).toBe(false))
    vi.useFakeTimers()

    act(() => result.current.openProviderDeleteDialog(1))
    await act(async () => {
      await result.current.handleConfirmProviderDelete()
    })
    expect(result.current.apiProviders.map(({ name }) => name)).toEqual([
      'main',
      'spare-b',
    ])

    await act(async () => {
      vi.advanceTimersByTime(2000)
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(updateModelConfigSectionMock).toHaveBeenCalledTimes(1)

    act(() => result.current.openProviderDeleteDialog(1))
    await act(async () => {
      await result.current.handleConfirmProviderDelete()
    })
    expect(result.current.apiProviders.map(({ name }) => name)).toEqual(['main'])

    await act(async () => {
      vi.advanceTimersByTime(2000)
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(updateModelConfigSectionMock).toHaveBeenCalledTimes(1)

    await act(async () => {
      firstSave.resolve({})
      await firstSave.promise
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(result.current.apiProviders.map(({ name }) => name)).toEqual(['main'])
    expect(updateModelConfigSectionMock).toHaveBeenCalledTimes(2)
    expect(updateModelConfigSectionMock).toHaveBeenLastCalledWith(
      'api_providers',
      [expect.objectContaining({ name: 'main' })]
    )

    await act(async () => {
      secondSave.resolve({})
      await secondSave.promise
      await Promise.resolve()
    })
    expect(result.current.hasUnsavedChanges).toBe(false)
    expect(result.current.autoSaving).toBe(false)
  })
})
