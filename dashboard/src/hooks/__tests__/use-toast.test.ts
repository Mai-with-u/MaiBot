import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { reducer, toast, useToast } from '../use-toast'

type ToastState = Parameters<typeof reducer>[0]
type ToasterToast = ToastState['toasts'][number]

/** 构造 reducer 用的最小 toast，避免每个用例重复字段 */
function makeToast(overrides: Pick<ToasterToast, 'id'> & Partial<ToasterToast>): ToasterToast {
  return { open: true, title: overrides.title ?? overrides.id, ...overrides }
}

/** 通过公开 API 清空模块级 memoryState 与延迟删除队列 */
function flushToastStore() {
  const { result, unmount } = renderHook(() => useToast())
  act(() => {
    result.current.dismiss()
    vi.advanceTimersByTime(5000)
  })
  unmount()
}

beforeEach(() => {
  // 只伪造 timeout，避免把 React 的微任务调度一起假掉
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  flushToastStore()
})

afterEach(() => {
  flushToastStore()
  vi.clearAllTimers()
  vi.useRealTimers()
})

describe('reducer', () => {
  it('ADD_TOAST 新项前置，并裁剪到最多 5 条', () => {
    let state: ToastState = { toasts: [] }

    for (let index = 1; index <= 6; index += 1) {
      state = reducer(state, {
        type: 'ADD_TOAST',
        toast: makeToast({ id: `item-${index}`, title: `t${index}` }),
      })
    }

    expect(state.toasts).toHaveLength(5)
    expect(state.toasts.map((item) => item.id)).toEqual([
      'item-6',
      'item-5',
      'item-4',
      'item-3',
      'item-2',
    ])
  })

  it('UPDATE_TOAST 只合并同 id 的条目，其余保持引用', () => {
    const keep = makeToast({ id: 'keep', title: '保留', description: '旧描述' })
    const target = makeToast({ id: 'target', title: '旧标题', description: '仍在' })
    const state = { toasts: [target, keep] }

    const next = reducer(state, {
      type: 'UPDATE_TOAST',
      toast: { id: 'target', title: '新标题' },
    })

    expect(next.toasts[0]).toEqual({ ...target, title: '新标题' })
    expect(next.toasts[1]).toBe(keep)
  })

  it('DISMISS_TOAST 传入 id 时只把对应项 open 设为 false', () => {
    const state = {
      toasts: [makeToast({ id: 'close-me' }), makeToast({ id: 'keep-me' })],
    }

    const next = reducer(state, { type: 'DISMISS_TOAST', toastId: 'close-me' })
    expect(next.toasts[0]?.open).toBe(false)
    expect(next.toasts[1]?.open).toBe(true)
  })

  it('DISMISS_TOAST 不传 id 时关闭全部条目', () => {
    const state = {
      toasts: [makeToast({ id: 'one' }), makeToast({ id: 'two' })],
    }

    const next = reducer(state, { type: 'DISMISS_TOAST' })
    expect(next.toasts.every((item) => item.open === false)).toBe(true)
  })

  it('DISMISS_TOAST 传入 id 时只关闭对应项，并在延迟后从模块状态移除', () => {
    const { result } = renderHook(() => useToast())

    act(() => {
      // 先把两条写进模块状态，再用 reducer 的同名分支走带 id 的关闭
      toast({ title: '关闭我' })
      toast({ title: '留下我' })
    })

    const dismissedId = result.current.toasts.find((item) => item.title === '关闭我')?.id
    expect(dismissedId).toBeDefined()

    act(() => {
      result.current.dismiss(dismissedId)
    })

    const dismissed = result.current.toasts.find((item) => item.id === dismissedId)
    const kept = result.current.toasts.find((item) => item.title === '留下我')
    expect(dismissed?.open).toBe(false)
    expect(kept?.open).toBe(true)

    act(() => {
      vi.advanceTimersByTime(4999)
    })
    expect(result.current.toasts.some((item) => item.id === dismissedId)).toBe(true)

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(result.current.toasts.some((item) => item.id === dismissedId)).toBe(false)
    expect(result.current.toasts).toHaveLength(1)
    expect(result.current.toasts[0]?.title).toBe('留下我')
  })

  it('DISMISS_TOAST 不传 id 时关闭全部，并在延迟后清空', () => {
    const { result } = renderHook(() => useToast())

    act(() => {
      toast({ title: '一' })
      toast({ title: '二' })
    })
    expect(result.current.toasts).toHaveLength(2)

    act(() => {
      result.current.dismiss()
    })
    expect(result.current.toasts.every((item) => item.open === false)).toBe(true)

    act(() => {
      vi.advanceTimersByTime(5000)
    })
    expect(result.current.toasts).toEqual([])
  })

  it('REMOVE_TOAST 传入 id 时只删除对应项', () => {
    const state = {
      toasts: [makeToast({ id: 'drop' }), makeToast({ id: 'keep' })],
    }

    const next = reducer(state, { type: 'REMOVE_TOAST', toastId: 'drop' })
    expect(next.toasts.map((item) => item.id)).toEqual(['keep'])
  })

  it('REMOVE_TOAST 不传 id 时清空列表', () => {
    const state = {
      toasts: [makeToast({ id: 'a' }), makeToast({ id: 'b' })],
    }

    expect(reducer(state, { type: 'REMOVE_TOAST' }).toasts).toEqual([])
  })
})

describe('toast() / useToast', () => {
  it('toast() 生成递增 id，并以 open:true 前置写入，超出 5 条时裁剪', () => {
    const { result } = renderHook(() => useToast())
    const created: Array<{ id: string }> = []

    act(() => {
      for (let index = 0; index < 6; index += 1) {
        created.push(toast({ title: `msg-${index}` }))
      }
    })

    expect(created).toHaveLength(6)
    expect(Number(created[1]?.id)).toBe(Number(created[0]?.id) + 1)
    expect(new Set(created.map((item) => item.id)).size).toBe(6)

    expect(result.current.toasts).toHaveLength(5)
    expect(result.current.toasts.map((item) => item.title)).toEqual([
      'msg-5',
      'msg-4',
      'msg-3',
      'msg-2',
      'msg-1',
    ])
    expect(result.current.toasts.every((item) => item.open === true)).toBe(true)
    expect(result.current.toast).toBe(toast)
  })

  it('update 只改目标 toast，其它字段与其它条目保持', () => {
    const { result } = renderHook(() => useToast())
    let handle: ReturnType<typeof toast> | undefined

    act(() => {
      toast({ title: '其它' })
      handle = toast({ title: '旧标题', description: '描述仍在' })
    })

    act(() => {
      // id 会被 toast.update 内部覆盖为创建时的 id
      handle?.update({ id: '会被覆盖', title: '新标题' })
    })

    const updated = result.current.toasts.find((item) => item.id === handle?.id)
    const other = result.current.toasts.find((item) => item.title === '其它')
    expect(updated?.title).toBe('新标题')
    expect(updated?.description).toBe('描述仍在')
    expect(other?.title).toBe('其它')
  })

  it('返回的 dismiss 关闭自身；重复调用不会排队两次删除', () => {
    const { result } = renderHook(() => useToast())
    let handle = { id: '', dismiss: () => {} }

    act(() => {
      handle = toast({ title: '待关' })
      toast({ title: '旁观' })
    })

    act(() => {
      handle.dismiss()
      handle.dismiss()
    })

    expect(result.current.toasts.find((item) => item.id === handle.id)?.open).toBe(false)
    expect(result.current.toasts.find((item) => item.title === '旁观')?.open).toBe(true)

    act(() => {
      vi.advanceTimersByTime(5000)
    })
    expect(result.current.toasts).toHaveLength(1)
    expect(result.current.toasts[0]?.title).toBe('旁观')

    act(() => {
      vi.advanceTimersByTime(5000)
    })
    expect(result.current.toasts).toHaveLength(1)
  })

  it('onOpenChange(false) 走 dismiss，true 则保持打开', () => {
    const { result } = renderHook(() => useToast())

    act(() => {
      toast({ title: '开关' })
    })

    const current = result.current.toasts[0]
    expect(current?.onOpenChange).toBeTypeOf('function')

    act(() => {
      current?.onOpenChange?.(true)
    })
    expect(result.current.toasts[0]?.open).toBe(true)

    act(() => {
      current?.onOpenChange?.(false)
    })
    expect(result.current.toasts.find((item) => item.id === current?.id)?.open).toBe(false)
  })

  it('多个 useToast 订阅同一模块状态，卸载后不再向旧实例推送', () => {
    const first = renderHook(() => useToast())
    const second = renderHook(() => useToast())

    act(() => {
      first.result.current.toast({ title: '同步' })
    })
    expect(second.result.current.toasts[0]?.title).toBe('同步')

    first.unmount()

    act(() => {
      toast({ title: '卸载后' })
    })

    expect(second.result.current.toasts.map((item) => item.title)).toContain('卸载后')
    expect(second.result.current.toasts).toHaveLength(2)
  })
})
