import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { StepIndicator } from '@/components/ui/step-indicator'
import { PageFrame } from '@/components/ui/surface'

/** 正式引导和控件预览共享结构，业务保存逻辑由正式页面提供。 */
export function SetupWizardLayout({
  steps,
  currentStep,
  children,
  actions,
  headerActions,
}: {
  steps: { id: string; title: string; description: string; group?: { id: string; label: string } }[]
  currentStep: number
  children: ReactNode
  actions: ReactNode
  headerActions?: ReactNode
}) {
  const { t } = useTranslation()
  const step = steps[currentStep]
  const groups = new Map<string, { id: string; label: string; substeps?: { id: string; label: string }[] }>()
  for (const item of steps) {
    const group = item.group
    if (group) {
      const entry = groups.get(group.id) ?? { ...group, substeps: [] }
      entry.substeps?.push({ id: item.id, label: item.title })
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
      <PageFrame className="p-5 sm:p-7">
        <header className="mb-4">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <h1 className="text-2xl font-medium sm:text-[32px] sm:leading-tight">{step.title}</h1>
            {headerActions && <div className="ml-auto">{headerActions}</div>}
          </div>
          {step.description && (
            <p className="mt-3 text-sm leading-relaxed sm:text-base">{step.description}</p>
          )}
        </header>
        {children}
      </PageFrame>
      <footer className="mt-5 flex flex-wrap items-center justify-between gap-3">{actions}</footer>
    </main>
  )
}
