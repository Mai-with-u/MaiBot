import type { ReactNode } from 'react'
import { AnimatePresence, motion, useIsPresent } from 'motion/react'

import { useMotion } from '@/lib/motion'

export function SetupFlip({ viewKey, direction, className, children }: {
  viewKey: string
  direction: 1 | -1
  className?: string
  children: ReactNode
}) {
  return (
    <AnimatePresence initial={false} mode="wait" custom={direction}>
      <FlipPanel key={viewKey} direction={direction} className={className}>
        {children}
      </FlipPanel>
    </AnimatePresence>
  )
}

function FlipPanel({ children, direction, className }: {
  children: ReactNode
  direction: 1 | -1
  className?: string
}) {
  const isPresent = useIsPresent()
  const { enabled, transitions } = useMotion()

  return (
    <motion.div
      className={className}
      inert={!isPresent}
      transformTemplate={(_, transform) => transform ? `perspective(1400px) ${transform}` : 'none'}
      initial={enabled ? { rotateX: 75 * direction, opacity: 0 } : false}
      animate={{ rotateX: 0, opacity: 1 }}
      exit={enabled ? 'leave' : undefined}
      variants={{
        leave: (nextDirection: 1 | -1) => ({
          rotateX: -75 * nextDirection,
          opacity: 0,
          transition: transitions.exit,
        }),
      }}
      transition={transitions.normal}
    >
      {children}
    </motion.div>
  )
}
