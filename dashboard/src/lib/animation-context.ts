/**
 * 动画上下文
 */

import { createContext } from 'react'

interface AnimationContextValue {
  reducedMotion: boolean
}

export const AnimationContext = createContext<AnimationContextValue>({
  reducedMotion: false,
})
