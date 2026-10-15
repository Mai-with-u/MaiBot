import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { useEmbeddingWarning } from './useEmbeddingWarning'

const toastMock = vi.fn()

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: toastMock }),
}))

function setup() {
  const applyUpdate = vi.fn()
  const view = renderHook(() => useEmbeddingWarning({ applyUpdate }))
  return { ...view, applyUpdate }
}

describe('useEmbeddingWarning', () => {
  it('初始关闭，previous 为空时即使列表变化也不拦截', () => {
    const { result } = setup()
    expect(result.current.isOpen).toBe(false)

    let intercepted = true
    act(() => {
      intercepted = result.current.detectChange('model_list', ['alpha'])
    })
    expect(intercepted).toBe(false)
    expect(result.current.isOpen).toBe(false)
  })

  it('相同列表不拦截；长度或成员变化且已有 previous 时拦截', () => {
    const { result } = setup()
    act(() => result.current.setPrevious(['alpha']))

    let same = true
    act(() => {
      same = result.current.detectChange('model_list', ['alpha'])
    })
    expect(same).toBe(false)
    expect(result.current.isOpen).toBe(false)

    let lengthChanged = false
    act(() => {
      lengthChanged = result.current.detectChange('model_list', ['alpha', 'beta'])
    })
    expect(lengthChanged).toBe(true)
    expect(result.current.isOpen).toBe(true)
  })

  it('等长替换与仅新增成员都能检测为变化', () => {
    const { result } = setup()

    act(() => result.current.setPrevious(['alpha', 'beta']))
    let replaced = false
    act(() => {
      replaced = result.current.detectChange('model_list', ['alpha', 'gamma'])
    })
    expect(replaced).toBe(true)

    act(() => result.current.cancel())
    act(() => result.current.setPrevious(['alpha', 'alpha']))
    let appended = false
    act(() => {
      appended = result.current.detectChange('model_list', ['alpha', 'beta'])
    })
    expect(appended).toBe(true)
  })

  it('确认 model_list 更新会写回、同步 previous 并 toast', async () => {
    const { result, applyUpdate } = setup()
    act(() => result.current.setPrevious(['old']))
    act(() => {
      result.current.detectChange('model_list', ['new'])
    })

    await act(async () => {
      await result.current.confirm()
    })

    expect(applyUpdate).toHaveBeenCalledWith({ field: 'model_list', value: ['new'] })
    expect(toastMock).toHaveBeenCalledWith({
      title: '嵌入模型已选择',
      description: '配置将在 2 秒后自动保存',
    })
    expect(result.current.isOpen).toBe(false)

    let afterConfirm = true
    act(() => {
      afterConfirm = result.current.detectChange('model_list', ['new'])
    })
    expect(afterConfirm).toBe(false)
  })

  it('确认非 model_list 字段不改 previous；无待定时 confirm 不应用', async () => {
    const { result, applyUpdate } = setup()
    act(() => result.current.setPrevious(['alpha']))
    act(() => {
      result.current.detectChange('temperature', ['beta'])
    })

    await act(async () => {
      await result.current.confirm()
    })
    expect(applyUpdate).toHaveBeenCalledWith({ field: 'temperature', value: ['beta'] })

    let stillPrevious = false
    act(() => {
      stillPrevious = result.current.detectChange('model_list', ['beta'])
    })
    expect(stillPrevious).toBe(true)

    act(() => result.current.cancel())
    applyUpdate.mockClear()
    await act(async () => {
      await result.current.confirm()
    })
    expect(applyUpdate).not.toHaveBeenCalled()
  })

  it('setOpen(false) 放弃待定，setOpen(true) 保持等待态', () => {
    const { result } = setup()
    act(() => result.current.setPrevious(['alpha']))
    act(() => {
      result.current.detectChange('model_list', ['beta'])
    })
    expect(result.current.isOpen).toBe(true)

    act(() => result.current.setOpen(true))
    expect(result.current.isOpen).toBe(true)

    act(() => result.current.setOpen(false))
    expect(result.current.isOpen).toBe(false)
  })
})
