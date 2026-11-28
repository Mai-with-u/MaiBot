import { motion } from 'motion/react'

import { useMotion } from '@/lib/motion'
import { cn } from '@/lib/utils'

interface ThinkingIllustrationProps {
  className?: string
  size?: 'sm' | 'md' | 'lg'
}

const gapClasses = {
  sm: 'gap-1',
  md: 'gap-1.5',
  lg: 'gap-2',
}

const dotSizeClasses = {
  sm: 'h-1.5 w-1.5',
  md: 'h-2 w-2',
  lg: 'h-2.5 w-2.5',
}

export function ThinkingIllustration({ className, size = 'md' }: ThinkingIllustrationProps) {
  const { style, enabled, duration } = useMotion()
  const modernPulse = style === 'modern' && enabled

  return (
    <div
      role="status"
      aria-label="加载中"
      className={cn('inline-flex items-center justify-center text-primary', gapClasses[size], className)}
    >
      {modernPulse ? (
        [0, 1, 2].map((index) => (
          <motion.span
            key={index}
            className={cn('rounded-full bg-current', dotSizeClasses[size])}
            animate={{
              y: [0, -3.5, 0],
              scale: [0.85, 1.08, 0.85],
              opacity: [0.45, 1, 0.45],
            }}
            transition={{
              duration: duration(0.95),
              repeat: Infinity,
              delay: duration(index * 0.14),
              ease: 'easeInOut',
            }}
          />
        ))
      ) : (
        <>
          <span className={cn('animate-bounce rounded-full bg-current', dotSizeClasses[size])} />
          <span
            className={cn('animate-bounce rounded-full bg-current', dotSizeClasses[size])}
            style={{ animationDelay: '120ms' }}
          />
          <span
            className={cn('animate-bounce rounded-full bg-current', dotSizeClasses[size])}
            style={{ animationDelay: '240ms' }}
          />
        </>
      )}
      <span className="sr-only">加载中</span>
    </div>
  )
}
