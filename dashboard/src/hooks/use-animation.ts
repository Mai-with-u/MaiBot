import { useContext } from 'react'
import { AnimationContext } from '@/lib/animation-context'

export const useAnimation = () => {
  const context = useContext(AnimationContext)

  if (context === undefined) {
    throw new Error('useAnimation must be used within an AnimationProvider')
  }

  return context
}
