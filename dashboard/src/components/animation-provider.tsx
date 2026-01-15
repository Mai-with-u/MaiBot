import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { AnimationContext } from '@/lib/animation-context'

type AnimationProviderProps = {
  children: ReactNode
  defaultEnabled?: boolean
  defaultWavesEnabled?: boolean
  storageKey?: string
  wavesStorageKey?: string
}

export function AnimationProvider({
  children,
  defaultEnabled = true,
  defaultWavesEnabled = true,
  storageKey = 'enable-animations',
  wavesStorageKey = 'enable-waves-background',
}: AnimationProviderProps) {
  const [enableAnimations, setEnableAnimations] = useState<boolean>(() => {
    const stored = localStorage.getItem(storageKey)
    return stored !== null ? stored === 'true' : defaultEnabled
  })

  const [enableWavesBackground, setEnableWavesBackground] = useState<boolean>(() => {
    const stored = localStorage.getItem(wavesStorageKey)
    return stored !== null ? stored === 'true' : defaultWavesEnabled
  })

  useEffect(() => {
    const root = document.documentElement

    if (enableAnimations) {
      root.classList.remove('no-animations')
    } else {
      root.classList.add('no-animations')
    }

    localStorage.setItem(storageKey, String(enableAnimations))
  }, [enableAnimations, storageKey])

  useEffect(() => {
    localStorage.setItem(wavesStorageKey, String(enableWavesBackground))
  }, [enableWavesBackground, wavesStorageKey])

  const value = {
    enableAnimations,
    setEnableAnimations,
    enableWavesBackground,
    setEnableWavesBackground,
  }

  return <AnimationContext.Provider value={value}>{children}</AnimationContext.Provider>
}
