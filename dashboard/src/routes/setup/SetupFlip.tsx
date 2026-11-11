import type { ReactNode } from 'react'
import { AnimatePresence, motion, useIsPresent, useReducedMotion } from 'motion/react'

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
  const reducedMotion = useReducedMotion()

  return (
    <motion.div
      className={className}
      inert={!isPresent}
      transformTemplate={(_, transform) => transform ? `perspective(1400px) ${transform}` : 'none'}
      initial={reducedMotion ? false : { rotateX: 75 * direction, opacity: 0 }}
      animate={{ rotateX: 0, opacity: 1 }}
      exit={reducedMotion ? undefined : 'leave'}
      variants={{
        leave: (nextDirection: 1 | -1) => ({
          rotateX: -75 * nextDirection,
          opacity: 0,
          transition: { duration: 0.16, ease: 'easeIn' },
        }),
      }}
      transition={{ duration: reducedMotion ? 0 : 0.22, ease: 'easeOut' }}
    >
      {children}
    </motion.div>
  )
}
