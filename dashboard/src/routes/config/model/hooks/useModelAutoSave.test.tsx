import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { updateModelConfigSection } from '@/lib/config-api'

import type { ModelInfo, ModelTaskConfig } from '../types'
import { useModelAutoSave } from './useModelAutoSave'

vi.mock('@/lib/config-api', () => ({
  updateModelConfigSection: vi.fn(),
}))

const updateModelConfigSectionMock = vi.mocked(updateModelConfigSection)

function createDeferred<T>() {
  let resolve: (value: T | PromiseLike<T>) => void = () => {}
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

function createModel(name: string): ModelInfo {
  return {
    model_identifier: name,
    name,
    api_provider: 'test',
    price_in: 0,
    price_out: 0,
  }
}

function createWriteQueue() {
  let chain = Promise.resolve()
  const enqueueWrite = (operation: () => Promise<void>): Promise<void> => {
    const operationPromise = chain.then(operation)
    chain = operationPromise.catch(() => undefined)
    return operationPromise
  }
  return enqueueWrite
}

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('useModelAutoSave', () => {
  it('模型和任务配置并发保存时汇总保存中与未保存状态', async () => {
    vi.useFakeTimers()
    const modelsSave = createDeferred<Record<string, unknown>>()
    const taskConfigSave = createDeferred<Record<string, unknown>>()
    updateModelConfigSectionMock.mockImplementation((sectionName) => {
      return sectionName === 'models'
        ? modelsSave.promise
        : taskConfigSave.promise
    })
    const onSavingChange = vi.fn()
    const onUnsavedChange = vi.fn()
    const enqueueWrite = createWriteQueue()
    const initialModels = [createModel('old')]
    const initialTaskConfig: ModelTaskConfig = {
      chat: { model_list: ['old'] },
    }
    const { result, rerender } = renderHook(
      ({
        models,
        taskConfig,
      }: {
        models: ModelInfo[]
        taskConfig: ModelTaskConfig
      }) =>
        useModelAutoSave({
          models,
          taskConfig,
          enqueueWrite,
          debounceMs: 100,
          onSavingChange,
          onUnsavedChange,
        }),
      {
        initialProps: {
          models: initialModels,
          taskConfig: initialTaskConfig,
        },
      }
    )

    act(() => {
      result.current.resetSnapshots(initialModels, initialTaskConfig)
      result.current.initialLoadRef.current = false
    })
    rerender({
      models: [createModel('new')],
      taskConfig: { chat: { model_list: ['new'] } },
    })

    await act(async () => {
      vi.advanceTimersByTime(100)
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(updateModelConfigSectionMock).toHaveBeenCalledTimes(1)

    await act(async () => {
      modelsSave.resolve({})
      await modelsSave.promise
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(updateModelConfigSectionMock).toHaveBeenCalledTimes(2)
    expect(onUnsavedChange).toHaveBeenLastCalledWith(true)
    expect(onSavingChange).toHaveBeenLastCalledWith(true)

    await act(async () => {
      taskConfigSave.resolve({})
      await taskConfigSave.promise
      await Promise.resolve()
    })
    expect(onUnsavedChange).toHaveBeenLastCalledWith(false)
    expect(onSavingChange).toHaveBeenLastCalledWith(false)
  })

  it('整份保存期间的新编辑排在屏障之后并保持未保存状态', async () => {
    vi.useFakeTimers()
    const barrierSave = createDeferred<void>()
    const postBarrierSave = createDeferred<Record<string, unknown>>()
    updateModelConfigSectionMock.mockImplementation(() => postBarrierSave.promise)
    const enqueueWrite = createWriteQueue()
    const onUnsavedChange = vi.fn()
    const initialModels = [createModel('initial')]
    const initialTaskConfig: ModelTaskConfig = {
      chat: { model_list: ['initial'] },
    }
    const { result, rerender } = renderHook(
      ({ models }: { models: ModelInfo[] }) =>
        useModelAutoSave({
          models,
          taskConfig: initialTaskConfig,
          enqueueWrite,
          debounceMs: 100,
          onUnsavedChange,
        }),
      { initialProps: { models: initialModels } }
    )

    act(() => {
      result.current.resetSnapshots(initialModels, initialTaskConfig)
      result.current.initialLoadRef.current = false
    })

    const clickedModels = [createModel('clicked')]
    rerender({ models: clickedModels })
    const checkpoint = result.current.prepareSaveBarrier(
      clickedModels,
      initialTaskConfig
    )
    const barrierPromise = enqueueWrite(() => barrierSave.promise)

    rerender({ models: [createModel('edited-after-click')] })
    await act(async () => {
      vi.advanceTimersByTime(100)
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(updateModelConfigSectionMock).not.toHaveBeenCalled()

    let applyModels: boolean | undefined
    await act(async () => {
      barrierSave.resolve()
      await barrierPromise
      applyModels = result.current.commitSaveBarrier(checkpoint).applyModels
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(applyModels).toBe(false)
    expect(onUnsavedChange).toHaveBeenLastCalledWith(true)
    expect(updateModelConfigSectionMock).toHaveBeenCalledWith(
      'models',
      expect.arrayContaining([
        expect.objectContaining({ name: 'edited-after-click' }),
      ])
    )

    await act(async () => {
      postBarrierSave.resolve({})
      await postBarrierSave.promise
      await Promise.resolve()
    })
    expect(onUnsavedChange).toHaveBeenLastCalledWith(false)
  })

  it('旧请求执行期间回退到基线时会排队补写基线', async () => {
    vi.useFakeTimers()
    const staleSave = createDeferred<Record<string, unknown>>()
    const correctiveSave = createDeferred<Record<string, unknown>>()
    updateModelConfigSectionMock
      .mockImplementationOnce(() => staleSave.promise)
      .mockImplementationOnce(() => correctiveSave.promise)
    const enqueueWrite = createWriteQueue()
    const onUnsavedChange = vi.fn()
    const initialModels = [createModel('initial')]
    const taskConfig: ModelTaskConfig = {
      chat: { model_list: ['initial'] },
    }
    const { result, rerender } = renderHook(
      ({ models }: { models: ModelInfo[] }) =>
        useModelAutoSave({
          models,
          taskConfig,
          enqueueWrite,
          debounceMs: 100,
          onUnsavedChange,
        }),
      { initialProps: { models: initialModels } }
    )

    act(() => {
      result.current.resetSnapshots(initialModels, taskConfig)
      result.current.initialLoadRef.current = false
    })
    rerender({ models: [createModel('stale')] })
    await act(async () => {
      vi.advanceTimersByTime(100)
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(updateModelConfigSectionMock).toHaveBeenCalledTimes(1)

    rerender({ models: initialModels })
    await act(async () => {
      vi.advanceTimersByTime(100)
      await Promise.resolve()
    })
    expect(onUnsavedChange).toHaveBeenLastCalledWith(true)
    expect(updateModelConfigSectionMock).toHaveBeenCalledTimes(1)

    await act(async () => {
      staleSave.resolve({})
      await staleSave.promise
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(updateModelConfigSectionMock).toHaveBeenCalledTimes(2)
    expect(updateModelConfigSectionMock).toHaveBeenLastCalledWith(
      'models',
      [expect.objectContaining({ name: 'initial' })]
    )
    expect(onUnsavedChange).toHaveBeenLastCalledWith(true)

    await act(async () => {
      correctiveSave.resolve({})
      await correctiveSave.promise
      await Promise.resolve()
    })
    expect(onUnsavedChange).toHaveBeenLastCalledWith(false)
  })
})
