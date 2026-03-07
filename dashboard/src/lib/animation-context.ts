import { createContext } from 'react'

export type AnimationSettings = {
  enableAnimations: boolean
  enableWavesBackground: boolean
  setEnableAnimations: (enable: boolean) => void
  setEnableWavesBackground: (enable: boolean) => void
}

export const AnimationContext = createContext<AnimationSettings | undefined>(undefined)
