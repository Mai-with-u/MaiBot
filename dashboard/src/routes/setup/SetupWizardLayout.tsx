import type { ReactNode } from 'react'
import { animate, motion, useMotionValue, useReducedMotion } from 'motion/react'
import { useLayoutEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'

import { StepIndicator } from '@/components/ui/step-indicator'
import { PageFrame } from '@/components/ui/surface'
import { SetupFlip } from './SetupFlip'
import type { SetupStep } from './types'

/** 正式引导和控件预览共享结构，业务保存逻辑由正式页面提供。 */
export function SetupWizardLayout({
  steps,
  currentStep,
  children,
  actions,
  headerActions,
  hideTitle = false,
  animateContentHeight = false,
  transitionDirection = 1,
}: {
  steps: SetupStep[]
  currentStep: number
  children: ReactNode
  actions: ReactNode
  headerActions?: ReactNode
  hideTitle?: boolean
  animateContentHeight?: boolean
  transitionDirection?: 1 | -1
}) {
  const { t } = useTranslation()
  const reducedMotion = useReducedMotion()
  const step = steps[currentStep]
  const groups = new Map<string, { id: string; label: string; substeps?: { id: string; label: string }[] }>()
  for (const item of steps) {
    const group = item.group
    if (group) {
      const entry = groups.get(group.id) ?? { ...group, substeps: [] }
      entry.substeps?.push({ id: item.id, label: item.label ?? item.title })
      groups.set(group.id, entry)
    } else {
      groups.set(item.id, {
        id: item.id,
        label: item.id === 'custom-token' ? t('setupPage.steps.customToken.label') : item.title,
      })
    }
  }
  const stages = [...groups.values()]
  return (
    <main className="relative mx-auto w-full max-w-[800px] py-8">
      <div className="mb-6 sm:px-16">
        <StepIndicator
          currentStep={stages.findIndex((stage) => stage.id === (step.group?.id ?? step.id))}
          currentSubstep={step.id}
          label={t('setupPage.header.title')}
          steps={stages}
        />
      </div>
      <SetupFlip viewKey={step.id} direction={transitionDirection}>
        <PageFrame className="p-5 sm:p-7">
          <header className={!hideTitle || headerActions || step.description ? 'mb-4' : undefined}>
            <div className="flex flex-wrap items-center justify-between gap-4">
              <h1 className={hideTitle ? 'sr-only' : 'text-2xl font-medium sm:text-[32px] sm:leading-tight'}>
                {step.title}
              </h1>
              {headerActions && <div className="ml-auto">{headerActions}</div>}
            </div>
            {step.description && (
              <p className="mt-3 text-sm leading-relaxed sm:text-base">{step.description}</p>
            )}
          </header>
          {animateContentHeight && !reducedMotion ? <AnimatedContentHeight>{children}</AnimatedContentHeight> : children}
        </PageFrame>
        <footer className="mt-5 flex flex-wrap items-center justify-between gap-3">{actions}</footer>
      </SetupFlip>
    </main>
  )
}

function AnimatedContentHeight({ children }: { children: ReactNode }) {
  const contentRef = useRef<HTMLDivElement>(null)
  const height = useMotionValue<number | string>('auto')

  useLayoutEffect(() => {
    const content = contentRef.current!
    let contentHeight = content.offsetHeight
    height.jump(contentHeight)

    const observer = new ResizeObserver(() => {
      const nextHeight = content.offsetHeight
      if (nextHeight === contentHeight) return
      contentHeight = nextHeight
      animate(height, contentHeight, { duration: 0.24, ease: 'easeOut' })
    })
    observer.observe(content)
    return () => {
      observer.disconnect()
      height.stop()
    }
  }, [height])

  return (
    <motion.div className="overflow-clip [overflow-clip-margin:4px]" style={{ height }}>
      <div ref={contentRef}>{children}</div>
    </motion.div>
  )
}
