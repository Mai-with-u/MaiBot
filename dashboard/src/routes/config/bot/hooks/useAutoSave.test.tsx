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
  it('切换页面时立即保存仍处于防抖期的配置', async () => {
    vi.useFakeTimers()
    updateBotConfigSectionMock.mockResolvedValue({} as never)
    const { result, unmount } = renderHook(() => useAutoSave(false, vi.fn(), vi.fn()))

    act(() => {
      result.current.triggerAutoSave('personality', { reply_style: '离开前的最新内容' })
    })
    unmount()
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(updateBotConfigSectionMock).toHaveBeenCalledOnce()
    expect(updateBotConfigSectionMock).toHaveBeenCalledWith('personality', {
      reply_style: '离开前的最新内容',
    })
  })

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

  it('初始加载期间触发自动保存会被直接忽略', async () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useAutoSave(true, vi.fn(), vi.fn(), { debounceMs: 100 }))

    act(() => {
      result.current.triggerAutoSave('bot', { nickname: 'ignored' })
    })
    await advanceDebounce()

    expect(updateBotConfigSectionMock).not.toHaveBeenCalled()
  })

  it('同一分区再次编辑会重置防抖计时器，只保存最后一次', async () => {
    vi.useFakeTimers()
    updateBotConfigSectionMock.mockResolvedValue({} as never)
    const { result } = renderHook(() => useAutoSave(false, vi.fn(), vi.fn(), { debounceMs: 100 }))

    act(() => {
      result.current.triggerAutoSave('bot', { nickname: 'old' })
    })
    await act(async () => {
      vi.advanceTimersByTime(50)
      await Promise.resolve()
    })
    act(() => {
      result.current.triggerAutoSave('bot', { nickname: 'new' })
    })
    await advanceDebounce()

    expect(updateBotConfigSectionMock).toHaveBeenCalledOnce()
    expect(updateBotConfigSectionMock).toHaveBeenCalledWith('bot', { nickname: 'new' })
  })

  it('saveNow 会取消未到期的防抖并立即写入', async () => {
    vi.useFakeTimers()
    updateBotConfigSectionMock.mockResolvedValue({} as never)
    const { result } = renderHook(() => useAutoSave(false, vi.fn(), vi.fn(), { debounceMs: 100 }))

    act(() => {
      result.current.triggerAutoSave('bot', { nickname: 'debounced' })
    })
    await act(async () => {
      await result.current.saveNow('bot', { nickname: 'immediate' })
    })

    expect(updateBotConfigSectionMock).toHaveBeenCalledOnce()
    expect(updateBotConfigSectionMock).toHaveBeenCalledWith('bot', { nickname: 'immediate' })
  })

  it('保存失败时把 Error 和非 Error 都交给 onSaveError', async () => {
    vi.useFakeTimers()
    const onSaveError = vi.fn()
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    updateBotConfigSectionMock
      .mockRejectedValueOnce(new Error('网络失败'))
      .mockRejectedValueOnce('boom')
    const { result } = renderHook(() =>
      useAutoSave(false, vi.fn(), vi.fn(), { debounceMs: 100, onSaveError })
    )

    act(() => {
      result.current.triggerAutoSave('bot', { nickname: 'a' })
    })
    await advanceDebounce()
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(onSaveError).toHaveBeenCalledWith(expect.objectContaining({ message: '网络失败' }))

    act(() => {
      result.current.triggerAutoSave('bot', { nickname: 'b' })
    })
    await advanceDebounce()
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(onSaveError).toHaveBeenLastCalledWith(expect.objectContaining({ message: 'boom' }))
    consoleError.mockRestore()
  })

  it('前一次保存失败后，后续保存仍会继续执行', async () => {
    vi.useFakeTimers()
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    updateBotConfigSectionMock
      .mockRejectedValueOnce(new Error('first failed'))
      .mockResolvedValueOnce({} as never)
    const { result } = renderHook(() => useAutoSave(false, vi.fn(), vi.fn(), { debounceMs: 100 }))

    act(() => {
      result.current.triggerAutoSave('bot', { nickname: 'first' })
    })
    await advanceDebounce()
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    act(() => {
      result.current.triggerAutoSave('bot', { nickname: 'second' })
    })
    await advanceDebounce()
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(updateBotConfigSectionMock).toHaveBeenCalledTimes(2)
    expect(updateBotConfigSectionMock).toHaveBeenLastCalledWith('bot', { nickname: 'second' })
    consoleError.mockRestore()
  })

  it('卸载后完成的保存不会再回写保存状态', async () => {
    vi.useFakeTimers()
    const deferred = createDeferred<Record<string, unknown>>()
    updateBotConfigSectionMock.mockReturnValue(deferred.promise)
    const setAutoSaving = vi.fn()
    const onSaveSuccess = vi.fn()
    const { result, unmount } = renderHook(() =>
      useAutoSave(false, setAutoSaving, vi.fn(), { debounceMs: 100, onSaveSuccess })
    )

    act(() => {
      result.current.triggerAutoSave('bot', { nickname: 'gone' })
    })
    await advanceDebounce()
    unmount()

    await act(async () => {
      deferred.resolve({})
      await deferred.promise
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(onSaveSuccess).not.toHaveBeenCalled()
    expect(setAutoSaving).not.toHaveBeenCalledWith(false)
  })

  it('resetAutoSaveState 会清掉仍在防抖中的分区定时器', async () => {
    vi.useFakeTimers()
    const setHasUnsavedChanges = vi.fn()
    const { result } = renderHook(() =>
      useAutoSave(false, vi.fn(), setHasUnsavedChanges, { debounceMs: 100 })
    )

    act(() => {
      result.current.triggerAutoSave('bot', { nickname: 'stale' })
    })
    act(() => {
      result.current.resetAutoSaveState()
    })
    await advanceDebounce()

    expect(updateBotConfigSectionMock).not.toHaveBeenCalled()
    expect(setHasUnsavedChanges).toHaveBeenLastCalledWith(false)
  })

  it('saveNow 在没有待处理定时器时也会立即写入并提升修订', async () => {
    updateBotConfigSectionMock.mockResolvedValue({} as never)
    const setHasUnsavedChanges = vi.fn()
    const { result } = renderHook(() => useAutoSave(false, vi.fn(), setHasUnsavedChanges))

    await act(async () => {
      await result.current.saveNow('bot', { nickname: 'direct' })
    })

    expect(updateBotConfigSectionMock).toHaveBeenCalledOnce()
    expect(updateBotConfigSectionMock).toHaveBeenCalledWith('bot', { nickname: 'direct' })
    expect(setHasUnsavedChanges).toHaveBeenCalledWith(true)
    expect(setHasUnsavedChanges).toHaveBeenLastCalledWith(false)
  })

  it('卸载时冲刷防抖保存不会再把组件标为保存中', async () => {
    vi.useFakeTimers()
    updateBotConfigSectionMock.mockResolvedValue({} as never)
    const setAutoSaving = vi.fn()
    const { result, unmount } = renderHook(() =>
      useAutoSave(false, setAutoSaving, vi.fn(), { debounceMs: 100 })
    )

    act(() => {
      result.current.triggerAutoSave('bot', { nickname: 'flush' })
    })
    expect(setAutoSaving).not.toHaveBeenCalled()
    unmount()
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(updateBotConfigSectionMock).toHaveBeenCalledWith('bot', { nickname: 'flush' })
    expect(setAutoSaving).not.toHaveBeenCalled()
  })

  it('卸载后失败的保存不会再回调 onSaveError 或回写未保存状态', async () => {
    vi.useFakeTimers()
    const deferred = createDeferred<Record<string, unknown>>()
    updateBotConfigSectionMock.mockReturnValue(deferred.promise)
    const onSaveError = vi.fn()
    const setHasUnsavedChanges = vi.fn()
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    const { result, unmount } = renderHook(() =>
      useAutoSave(false, vi.fn(), setHasUnsavedChanges, { debounceMs: 100, onSaveError })
    )

    act(() => {
      result.current.triggerAutoSave('bot', { nickname: 'gone' })
    })
    await advanceDebounce()
    const callsAfterTrigger = setHasUnsavedChanges.mock.calls.length
    unmount()

    await act(async () => {
      deferred.reject('offline')
      await deferred.promise.catch(() => undefined)
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(onSaveError).not.toHaveBeenCalled()
    expect(setHasUnsavedChanges.mock.calls.length).toBe(callsAfterTrigger)
    consoleError.mockRestore()
  })

  it('runWithAutoSaveBarrier 在整份写入失败后仍解除保存中状态', async () => {
    const setAutoSaving = vi.fn()
    const { result } = renderHook(() => useAutoSave(false, setAutoSaving, vi.fn()))

    await act(async () => {
      await expect(
        result.current.runWithAutoSaveBarrier(async () => {
          throw new Error('full save failed')
        })
      ).rejects.toThrow('full save failed')
    })

    expect(setAutoSaving).toHaveBeenCalledWith(true)
    expect(setAutoSaving).toHaveBeenLastCalledWith(false)
  })

  it('卸载后 barrier 完成不会再回写保存中状态', async () => {
    const deferred = createDeferred<void>()
    const setAutoSaving = vi.fn()
    const { result, unmount } = renderHook(() => useAutoSave(false, setAutoSaving, vi.fn()))

    let barrierPromise!: Promise<void>
    act(() => {
      barrierPromise = result.current.runWithAutoSaveBarrier(() => deferred.promise)
    })
    expect(setAutoSaving).toHaveBeenCalledWith(true)
    unmount()

    await act(async () => {
      deferred.resolve()
      await barrierPromise
    })

    expect(setAutoSaving).not.toHaveBeenCalledWith(false)
  })

  it('cancelPendingAutoSave 会等待已经入队的写入结束', async () => {
    vi.useFakeTimers()
    const deferred = createDeferred<Record<string, unknown>>()
    updateBotConfigSectionMock.mockReturnValue(deferred.promise)
    const { result } = renderHook(() => useAutoSave(false, vi.fn(), vi.fn(), { debounceMs: 100 }))

    act(() => {
      result.current.triggerAutoSave('bot', { nickname: 'inflight' })
    })
    await advanceDebounce()

    let cancelled = false
    let cancelPromise!: Promise<void>
    act(() => {
      cancelPromise = result.current.cancelPendingAutoSave().then(() => {
        cancelled = true
      })
    })
    expect(cancelled).toBe(false)

    await act(async () => {
      deferred.resolve({})
      await deferred.promise
      await cancelPromise
    })
    expect(cancelled).toBe(true)
  })
})
