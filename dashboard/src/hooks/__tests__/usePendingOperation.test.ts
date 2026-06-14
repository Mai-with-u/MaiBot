import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { usePendingOperation } from '../usePendingOperation'

describe('usePendingOperation', () => {
  it('初始无待定操作、非等待态', () => {
    const { result } = renderHook(() => usePendingOperation<number>({ onConfirm: vi.fn() }))
    expect(result.current.pending).toBeNull()
    expect(result.current.isWaiting).toBe(false)
    expect(result.current.isConfirming).toBe(false)
  })

  it('submit 暂存待定操作并进入等待态', () => {
    const { result } = renderHook(() => usePendingOperation<{ id: number }>({ onConfirm: vi.fn() }))
    act(() => result.current.submit({ id: 7 }))
    expect(result.current.pending).toEqual({ id: 7 })
    expect(result.current.isWaiting).toBe(true)
  })

  it('confirm 执行 onConfirm（带待定操作）并在成功后清空', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    const { result } = renderHook(() => usePendingOperation<number>({ onConfirm }))

    act(() => result.current.submit(42))
    await act(async () => {
      await result.current.confirm()
    })

    expect(onConfirm).toHaveBeenCalledWith(42)
    expect(result.current.pending).toBeNull()
    expect(result.current.isWaiting).toBe(false)
  })

  it('无待定操作时 confirm 不调用 onConfirm', async () => {
    const onConfirm = vi.fn()
    const { result } = renderHook(() => usePendingOperation<number>({ onConfirm }))
    await act(async () => {
      await result.current.confirm()
    })
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('cancel 放弃待定操作', () => {
    const { result } = renderHook(() => usePendingOperation<number>({ onConfirm: vi.fn() }))
    act(() => result.current.submit(1))
    act(() => result.current.cancel())
    expect(result.current.pending).toBeNull()
    expect(result.current.isWaiting).toBe(false)
  })

  it('onConfirm 抛错时保留待定态以便重试', async () => {
    const onConfirm = vi.fn().mockRejectedValue(new Error('执行失败'))
    const { result } = renderHook(() => usePendingOperation<number>({ onConfirm }))

    act(() => result.current.submit(5))
    await act(async () => {
      await expect(result.current.confirm()).rejects.toThrow('执行失败')
    })

    expect(result.current.pending).toBe(5)
    expect(result.current.isWaiting).toBe(true)
    expect(result.current.isConfirming).toBe(false)
  })

  it('confirm 执行期间 isConfirming 为 true', async () => {
    let resolveConfirm: () => void = () => {}
    const onConfirm = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveConfirm = resolve
        }),
    )
    const { result } = renderHook(() => usePendingOperation<number>({ onConfirm }))

    act(() => result.current.submit(1))
    let confirmPromise: Promise<void> = Promise.resolve()
    act(() => {
      confirmPromise = result.current.confirm()
    })
    await waitFor(() => expect(result.current.isConfirming).toBe(true))

    await act(async () => {
      resolveConfirm()
      await confirmPromise
    })
    expect(result.current.isConfirming).toBe(false)
  })
})
