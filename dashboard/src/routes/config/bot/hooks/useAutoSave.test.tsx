import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { updateBotConfigSection } from '@/lib/config-api'

import { useAutoSave } from './useAutoSave'

vi.mock('@/lib/config-api', () => ({
  updateBotConfigSection: vi.fn(),
}))

const updateBotConfigSectionMock = vi.mocked(updateBotConfigSection)

function createDeferred<T = void>() {
  let resolve: (value: T | PromiseLike<T>) => void = () => {}
  let reject: (reason?: unknown) => void = () => {}
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, reject, resolve }
}

async function advanceDebounce(ms = 100): Promise<void> {
  await act(async () => {
    vi.advanceTimersByTime(ms)
    await Promise.resolve()
    await Promise.resolve()
  })
}

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('useAutoSave', () => {
  it('不同配置分区分别防抖，互不取消保存', async () => {
    vi.useFakeTimers()
    updateBotConfigSectionMock.mockResolvedValue({} as never)
    const setAutoSaving = vi.fn()
    const setHasUnsavedChanges = vi.fn()
    const { result } = renderHook(() =>
      useAutoSave(false, setAutoSaving, setHasUnsavedChanges, {
        debounceMs: 100,
      })
    )

    act(() => {
      result.current.triggerAutoSave('bot', { nickname: 'Mai' })
      result.current.triggerAutoSave('personality', { reply: 'hello' })
    })
    await advanceDebounce()

    expect(updateBotConfigSectionMock).toHaveBeenCalledTimes(2)
    expect(updateBotConfigSectionMock).toHaveBeenCalledWith('bot', {
      nickname: 'Mai',
    })
    expect(updateBotConfigSectionMock).toHaveBeenCalledWith('personality', {
      reply: 'hello',
    })
  })

  it('所有分区均保存完成后才清除未保存和保存中状态', async () => {
    vi.useFakeTimers()
    const botSave = createDeferred<Record<string, unknown>>()
    const personalitySave = createDeferred<Record<string, unknown>>()
    updateBotConfigSectionMock.mockImplementation((sectionName) => {
      return sectionName === 'bot' ? botSave.promise : personalitySave.promise
    })
    const setAutoSaving = vi.fn()
    const setHasUnsavedChanges = vi.fn()
    const { result } = renderHook(() =>
      useAutoSave(false, setAutoSaving, setHasUnsavedChanges, {
        debounceMs: 100,
      })
    )

    act(() => {
      result.current.triggerAutoSave('bot', { nickname: 'Mai' })
      result.current.triggerAutoSave('personality', { reply: 'hello' })
    })
    await advanceDebounce()

    await act(async () => {
      botSave.resolve({})
      await botSave.promise
      await Promise.resolve()
    })
    expect(setHasUnsavedChanges).toHaveBeenLastCalledWith(true)
    expect(setAutoSaving).toHaveBeenLastCalledWith(true)

    await act(async () => {
      personalitySave.resolve({})
      await personalitySave.promise
      await Promise.resolve()
    })
    expect(setHasUnsavedChanges).toHaveBeenLastCalledWith(false)
    expect(setAutoSaving).toHaveBeenLastCalledWith(false)
  })

  it('同一分区按修订顺序串行写入', async () => {
    vi.useFakeTimers()
    const firstSave = createDeferred<Record<string, unknown>>()
    const secondSave = createDeferred<Record<string, unknown>>()
    updateBotConfigSectionMock
      .mockImplementationOnce(() => firstSave.promise)
      .mockImplementationOnce(() => secondSave.promise)
    const { result } = renderHook(() =>
      useAutoSave(false, vi.fn(), vi.fn(), { debounceMs: 100 })
    )

    act(() => {
      result.current.triggerAutoSave('bot', { nickname: 'old' })
    })
    await advanceDebounce()

    act(() => {
      result.current.triggerAutoSave('bot', { nickname: 'new' })
    })
    await advanceDebounce()
    expect(updateBotConfigSectionMock).toHaveBeenCalledTimes(1)

    await act(async () => {
      firstSave.resolve({})
      await firstSave.promise
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(updateBotConfigSectionMock).toHaveBeenCalledTimes(2)
    expect(updateBotConfigSectionMock).toHaveBeenLastCalledWith('bot', {
      nickname: 'new',
    })

    await act(async () => {
      secondSave.resolve({})
      await secondSave.promise
      await Promise.resolve()
    })
  })

  it('整份配置写入会阻塞后来触发的分区保存，并保留新编辑的脏状态', async () => {
    vi.useFakeTimers()
    const firstSectionSave = createDeferred<Record<string, unknown>>()
    const newerSectionSave = createDeferred<Record<string, unknown>>()
    const fullSave = createDeferred<void>()
    updateBotConfigSectionMock
      .mockImplementationOnce(() => firstSectionSave.promise)
      .mockImplementationOnce(() => newerSectionSave.promise)
    const setHasUnsavedChanges = vi.fn()
    const { result } = renderHook(() =>
      useAutoSave(false, vi.fn(), setHasUnsavedChanges, { debounceMs: 100 })
    )

    act(() => {
      result.current.triggerAutoSave('bot', { nickname: 'first' })
    })
    await advanceDebounce()

    let fullSavePromise!: Promise<void>
    act(() => {
      fullSavePromise = result.current.runWithAutoSaveBarrier(() => fullSave.promise)
      result.current.triggerAutoSave('bot', { nickname: 'newer' })
    })
    await advanceDebounce()
    expect(updateBotConfigSectionMock).toHaveBeenCalledTimes(1)

    await act(async () => {
      firstSectionSave.resolve({})
      await firstSectionSave.promise
      await Promise.resolve()
    })
    expect(setHasUnsavedChanges).toHaveBeenLastCalledWith(true)
    expect(updateBotConfigSectionMock).toHaveBeenCalledTimes(1)

    await act(async () => {
      fullSave.resolve()
      await fullSavePromise
      await Promise.resolve()
    })
    expect(setHasUnsavedChanges).toHaveBeenLastCalledWith(true)
    expect(updateBotConfigSectionMock).toHaveBeenCalledTimes(2)

    await act(async () => {
      newerSectionSave.resolve({})
      await newerSectionSave.promise
      await Promise.resolve()
    })
    expect(setHasUnsavedChanges).toHaveBeenLastCalledWith(false)
  })

  it('重新加载后可清除被取消定时器留下的旧修订', async () => {
    vi.useFakeTimers()
    const setHasUnsavedChanges = vi.fn()
    const { result } = renderHook(() =>
      useAutoSave(false, vi.fn(), setHasUnsavedChanges, { debounceMs: 100 })
    )

    act(() => {
      result.current.triggerAutoSave('bot', { nickname: 'discarded' })
    })
    await act(async () => {
      await result.current.cancelPendingAutoSave()
      result.current.resetAutoSaveState()
    })

    expect(updateBotConfigSectionMock).not.toHaveBeenCalled()
    expect(setHasUnsavedChanges).toHaveBeenLastCalledWith(false)
  })
})
