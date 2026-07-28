import type { ReactNode } from 'react'

import { renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { AnimationSettings } from '@/lib/animation-context'
import { AnimationContext } from '@/lib/animation-context'

import { useAnimation } from '../use-animation'

describe('useAnimation', () => {
  it('在 AnimationProvider 内返回上下文值', () => {
    const setEnableAnimations = vi.fn()
    const value: AnimationSettings = { enableAnimations: false, setEnableAnimations }
    const wrapper = ({ children }: { children: ReactNode }) => (
      <AnimationContext.Provider value={value}>{children}</AnimationContext.Provider>
    )

    const { result } = renderHook(() => useAnimation(), { wrapper })

    // 返回的应是同一个上下文对象，而不是拷贝
    expect(result.current).toBe(value)
    expect(result.current.enableAnimations).toBe(false)

    result.current.setEnableAnimations(true)
    expect(setEnableAnimations).toHaveBeenCalledWith(true)
  })

  it('在 Provider 外使用时抛出错误', () => {
    // React 会把渲染期错误额外打到 console.error，这里静音避免测试输出噪声
    vi.spyOn(console, 'error').mockImplementation(() => {})

    expect(() => renderHook(() => useAnimation())).toThrow(
      'useAnimation must be used within an AnimationProvider'
    )
  })
})
