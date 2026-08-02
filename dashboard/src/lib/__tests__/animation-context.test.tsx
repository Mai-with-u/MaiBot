import { renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useContext, type ReactNode } from 'react'

import { AnimationContext, type AnimationSettings } from '../animation-context'

describe('AnimationContext', () => {
  it('无 Provider 时默认值为 undefined，供消费方检测缺失的上下文', () => {
    const { result } = renderHook(() => useContext(AnimationContext))
    expect(result.current).toBeUndefined()
  })

  it('Provider 提供的动画设置可被消费方原样读取', () => {
    const settings: AnimationSettings = {
      enableAnimations: false,
      setEnableAnimations: vi.fn(),
    }
    const wrapper = ({ children }: { children: ReactNode }) => (
      <AnimationContext.Provider value={settings}>{children}</AnimationContext.Provider>
    )

    const { result } = renderHook(() => useContext(AnimationContext), { wrapper })

    expect(result.current).toBe(settings)
    expect(result.current?.enableAnimations).toBe(false)
  })
})
