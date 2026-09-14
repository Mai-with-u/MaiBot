import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { getBotConfigCached, getModelConfigCached } from '@/lib/config-api'

import { useFeatureStatus } from './useFeatureStatus'

vi.mock('@/lib/config-api', () => ({
  getBotConfigCached: vi.fn(),
  getModelConfigCached: vi.fn(),
}))

const getBotConfigCachedMock = vi.mocked(getBotConfigCached)
const getModelConfigCachedMock = vi.mocked(getModelConfigCached)

function createDeferred<T>() {
  let resolve: (value: T | PromiseLike<T>) => void = () => {}
  let reject: (reason?: unknown) => void = () => {}
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('useFeatureStatus', () => {
  it('初始功能灯均为关闭', () => {
    const { result } = renderHook(() => useFeatureStatus())
    expect(result.current.featureStatus).toEqual({
      memoryEnabled: false,
      visualEnabled: false,
    })
  })

  it('从嵌套 config 解析记忆与视觉开启', async () => {
    getBotConfigCachedMock.mockResolvedValue({
      config: { a_memorix: { plugin: { enabled: true } } },
    })
    getModelConfigCachedMock.mockResolvedValue({
      config: { model_task_config: { vlm: { model_list: ['gpt-4v'] } } },
    })
    const { result } = renderHook(() => useFeatureStatus())

    await act(async () => {
      await result.current.fetchFeatureStatus()
    })

    expect(result.current.featureStatus).toEqual({
      memoryEnabled: true,
      visualEnabled: true,
    })
  })

  it('兼容未包一层 config 的主配置与模型配置', async () => {
    getBotConfigCachedMock.mockResolvedValue({
      a_memorix: { plugin: { enabled: true } },
    })
    getModelConfigCachedMock.mockResolvedValue({
      model_task_config: { vlm: { model_list: ['internvl'] } },
    })
    const { result } = renderHook(() => useFeatureStatus())

    await act(async () => {
      await result.current.fetchFeatureStatus()
    })

    expect(result.current.featureStatus).toEqual({
      memoryEnabled: true,
      visualEnabled: true,
    })
  })

  it('记忆开关必须严格为 true，空或空白视觉模型不算启用', async () => {
    getBotConfigCachedMock.mockResolvedValue({
      a_memorix: { plugin: { enabled: 'true' } },
    })
    getModelConfigCachedMock.mockResolvedValue({
      model_task_config: { vlm: { model_list: ['', '  ', null] } },
    } as never)
    const { result } = renderHook(() => useFeatureStatus())

    await act(async () => {
      await result.current.fetchFeatureStatus()
    })

    expect(result.current.featureStatus).toEqual({
      memoryEnabled: false,
      visualEnabled: false,
    })
  })

  it('vlm.model_list 非数组时视觉保持关闭', async () => {
    getBotConfigCachedMock.mockResolvedValue({
      a_memorix: { plugin: { enabled: true } },
    })
    getModelConfigCachedMock.mockResolvedValue({
      model_task_config: { vlm: { model_list: 'gpt-4v' } },
    } as never)
    const { result } = renderHook(() => useFeatureStatus())

    await act(async () => {
      await result.current.fetchFeatureStatus()
    })

    expect(result.current.featureStatus).toEqual({
      memoryEnabled: true,
      visualEnabled: false,
    })
  })

  it('模型配置失败时仍可根据主配置开启记忆，视觉关闭', async () => {
    getBotConfigCachedMock.mockResolvedValue({
      a_memorix: { plugin: { enabled: true } },
    })
    getModelConfigCachedMock.mockRejectedValue(new Error('模型配置不可用'))
    const { result } = renderHook(() => useFeatureStatus())

    await act(async () => {
      await result.current.fetchFeatureStatus()
    })

    expect(result.current.featureStatus).toEqual({
      memoryEnabled: true,
      visualEnabled: false,
    })
  })

  it('主配置失败时保持默认关闭，不读取模型结果', async () => {
    getBotConfigCachedMock.mockRejectedValue(new Error('主配置不可用'))
    getModelConfigCachedMock.mockResolvedValue({
      model_task_config: { vlm: { model_list: ['gpt-4v'] } },
    })
    const { result } = renderHook(() => useFeatureStatus())

    await act(async () => {
      await result.current.fetchFeatureStatus()
    })

    expect(result.current.featureStatus).toEqual({
      memoryEnabled: false,
      visualEnabled: false,
    })
  })

  it('配置字段缺失时保持关闭', async () => {
    getBotConfigCachedMock.mockResolvedValue({})
    getModelConfigCachedMock.mockResolvedValue({})
    const { result } = renderHook(() => useFeatureStatus())

    await act(async () => {
      await result.current.fetchFeatureStatus()
    })

    expect(result.current.featureStatus).toEqual({
      memoryEnabled: false,
      visualEnabled: false,
    })
  })

  it('解析过程抛错时回退到全部关闭', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    getBotConfigCachedMock.mockResolvedValue(null as never)
    getModelConfigCachedMock.mockResolvedValue({})
    const { result } = renderHook(() => useFeatureStatus())

    await act(async () => {
      await result.current.fetchFeatureStatus()
    })

    expect(result.current.featureStatus).toEqual({
      memoryEnabled: false,
      visualEnabled: false,
    })
    expect(errorSpy).toHaveBeenCalledWith('获取功能启用状态失败:', expect.any(Error))
  })

  it('卸载后完成的请求不会回写状态', async () => {
    const botDeferred = createDeferred<Record<string, unknown>>()
    getBotConfigCachedMock.mockReturnValue(botDeferred.promise)
    getModelConfigCachedMock.mockResolvedValue({
      model_task_config: { vlm: { model_list: ['gpt-4v'] } },
    })
    const { result, unmount } = renderHook(() => useFeatureStatus())

    let pending: Promise<void> = Promise.resolve()
    act(() => {
      pending = result.current.fetchFeatureStatus()
    })
    unmount()

    await act(async () => {
      botDeferred.resolve({ a_memorix: { plugin: { enabled: true } } })
      await pending
    })
  })
})
